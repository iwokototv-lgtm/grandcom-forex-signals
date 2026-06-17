"""
Position Manager
Tracks all open positions in MongoDB with hard cap enforcement.

Hard limits:
- MAX 5 concurrent positions per pair
- Total account exposure cap at 10%
- Full audit trail in open_positions / closed_positions collections
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_POSITIONS_PER_PAIR: int = 5
MAX_ACCOUNT_EXPOSURE_PCT: float = 0.10  # 10 % of account balance


class PositionManager:
    """
    Manages open positions with MongoDB persistence.

    Collections used:
        open_positions   – live trades
        closed_positions – archived trades
    """

    def __init__(self, db=None, account_balance: float = 10_000.0):
        self._db = db
        self.account_balance = account_balance

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def set_db(self, db) -> None:
        self._db = db

    def set_account_balance(self, balance: float) -> None:
        self.account_balance = balance

    # ------------------------------------------------------------------
    # Reset (clear phantom positions on startup)
    # ------------------------------------------------------------------

    async def reset(self) -> None:
        """
        Reset all open positions (call on startup to clear phantom positions).

        Clears the open_positions MongoDB collection so the account starts
        with 0% exposure on every restart.

        Guarantees new signals can be sent immediately after a restart.
        """
        if self._db is not None:
            try:
                result = await self._db.open_positions.delete_many({})
                logger.info(
                    f"[POSITION_MANAGER] MongoDB open_positions cleared "
                    f"({result.deleted_count} phantom positions removed)"
                )
            except Exception as exc:
                logger.warning(
                    f"[POSITION_MANAGER] Failed to clear MongoDB open_positions: {exc} "
                    f"(non-fatal, will retry on next signal)"
                )
        else:
            logger.info("[POSITION_MANAGER] No DB configured, skipping reset")

    # ------------------------------------------------------------------
    # Add position
    # ------------------------------------------------------------------

    async def add_position(
        self,
        pair: str,
        entry: float,
        tp_levels: List[float],
        sl: float,
        size: float,
        confidence: float,
        signal_type: str,
        analysis: str = "",
    ) -> Dict[str, Any]:
        """
        Attempt to open a new position.

        Returns a dict with ``allowed`` bool and ``reason`` string.
        On success also returns ``position_id``.
        """
        # --- guard: position count ---
        count = await self.get_position_count(pair)
        if count >= MAX_POSITIONS_PER_PAIR:
            msg = (
                f"[{pair}] Position limit reached ({count}/{MAX_POSITIONS_PER_PAIR}) — "
                "new trade BLOCKED"
            )
            logger.warning(msg)
            return {"allowed": False, "reason": msg}

        # --- guard: exposure ---
        exposure_pct = await self.get_total_exposure_pct()
        new_exposure = (size * entry) / self.account_balance if self.account_balance > 0 else 0
        if exposure_pct + new_exposure > MAX_ACCOUNT_EXPOSURE_PCT:
            msg = (
                f"[{pair}] Exposure cap breached "
                f"({(exposure_pct + new_exposure) * 100:.1f}% > "
                f"{MAX_ACCOUNT_EXPOSURE_PCT * 100:.0f}%) — new trade BLOCKED"
            )
            logger.warning(msg)
            return {"allowed": False, "reason": msg}

        doc: Dict[str, Any] = {
            "pair": pair,
            "signal_type": signal_type,
            "entry_price": entry,
            "tp_levels": tp_levels,
            "sl_price": sl,
            "size": size,
            "confidence": confidence,
            "analysis": analysis,
            "status": "OPEN",
            "pnl": 0.0,
            "opened_at": datetime.now(timezone.utc),
            "closed_at": None,
            "close_reason": None,
            "exit_price": None,
        }

        if self._db is not None:
            try:
                result = await self._db.open_positions.insert_one(doc)
                position_id = str(result.inserted_id)
                logger.info(
                    f"[{pair}] Position opened — id={position_id} "
                    f"entry={entry} size={size} conf={confidence}%"
                )
                return {"allowed": True, "position_id": position_id, "reason": "OK"}
            except Exception as exc:
                logger.error(f"[{pair}] Failed to store position: {exc}")
                return {"allowed": False, "reason": f"DB error: {exc}"}

        # No DB — still allow but warn
        logger.warning(f"[{pair}] No DB — position not persisted")
        return {"allowed": True, "position_id": None, "reason": "NO_DB"}

    # ------------------------------------------------------------------
    # Close single position
    # ------------------------------------------------------------------

    async def close_position(
        self,
        position_id: str,
        exit_price: float,
        reason: str = "MANUAL",
    ) -> Dict[str, Any]:
        """Close a single position by its MongoDB _id string."""
        if self._db is None:
            return {"success": False, "reason": "NO_DB"}

        try:
            from bson import ObjectId

            now = datetime.now(timezone.utc)
            pos = await self._db.open_positions.find_one({"_id": ObjectId(position_id)})
            if pos is None:
                return {"success": False, "reason": "NOT_FOUND"}

            entry = pos.get("entry_price", exit_price)
            size = pos.get("size", 0.0)
            signal_type = pos.get("signal_type", "BUY")
            pnl = (
                (exit_price - entry) * size
                if signal_type == "BUY"
                else (entry - exit_price) * size
            )

            closed_doc = {**pos, "status": "CLOSED", "exit_price": exit_price,
                          "pnl": round(pnl, 2), "closed_at": now, "close_reason": reason}
            closed_doc.pop("_id", None)

            await self._db.closed_positions.insert_one(closed_doc)
            await self._db.open_positions.delete_one({"_id": ObjectId(position_id)})

            logger.info(
                f"Position {position_id} closed — exit={exit_price} "
                f"pnl={pnl:.2f} reason={reason}"
            )
            return {"success": True, "pnl": round(pnl, 2), "reason": reason}

        except Exception as exc:
            logger.error(f"close_position error: {exc}")
            return {"success": False, "reason": str(exc)}

    # ------------------------------------------------------------------
    # Close ALL positions
    # ------------------------------------------------------------------

    async def close_all_positions(
        self,
        exit_price_map: Optional[Dict[str, float]] = None,
        reason: str = "SYSTEM",
    ) -> Dict[str, Any]:
        """
        Close every open position.

        Args:
            exit_price_map: {pair: current_price} — used for P&L calc.
            reason: Why positions are being closed (e.g. REVERSAL, DAILY_LOSS_LIMIT).
        """
        if self._db is None:
            return {"success": False, "closed": 0, "reason": "NO_DB"}

        exit_price_map = exit_price_map or {}
        positions = await self.get_open_positions()
        closed_count = 0
        total_pnl = 0.0

        for pos in positions:
            pair = pos.get("pair", "UNKNOWN")
            exit_price = exit_price_map.get(pair, pos.get("entry_price", 0.0))
            pos_id = str(pos.get("_id", ""))
            result = await self.close_position(pos_id, exit_price, reason=reason)
            if result.get("success"):
                closed_count += 1
                total_pnl += result.get("pnl", 0.0)

        logger.warning(
            f"close_all_positions: closed={closed_count} "
            f"total_pnl={total_pnl:.2f} reason={reason}"
        )
        return {
            "success": True,
            "closed": closed_count,
            "total_pnl": round(total_pnl, 2),
            "reason": reason,
        }

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_open_positions(self, pair: Optional[str] = None) -> List[Dict]:
        """Return all open positions, optionally filtered by pair."""
        if self._db is None:
            return []
        try:
            query: Dict[str, Any] = {"status": "OPEN"}
            if pair:
                query["pair"] = pair.upper()
            cursor = self._db.open_positions.find(query)
            return await cursor.to_list(length=500)
        except Exception as exc:
            logger.error(f"get_open_positions error: {exc}")
            return []

    async def get_position_count(self, pair: Optional[str] = None) -> int:
        """Count open positions, optionally per pair."""
        if self._db is None:
            return 0
        try:
            query: Dict[str, Any] = {"status": "OPEN"}
            if pair:
                query["pair"] = pair.upper()
            return await self._db.open_positions.count_documents(query)
        except Exception as exc:
            logger.error(f"get_position_count error: {exc}")
            return 0

    async def get_total_exposure(self) -> float:
        """Return total notional exposure across all open positions."""
        positions = await self.get_open_positions()
        return sum(
            pos.get("size", 0.0) * pos.get("entry_price", 0.0)
            for pos in positions
        )

    async def get_total_exposure_pct(self) -> float:
        """Return total exposure as a fraction of account balance."""
        if self.account_balance <= 0:
            return 0.0
        exposure = await self.get_total_exposure()
        return exposure / self.account_balance

    async def get_positions_summary(self) -> Dict[str, Any]:
        """Return a summary dict suitable for Telegram alerts."""
        positions = await self.get_open_positions()
        total_count = len(positions)
        exposure_pct = await self.get_total_exposure_pct()

        by_pair: Dict[str, int] = {}
        for pos in positions:
            p = pos.get("pair", "UNKNOWN")
            by_pair[p] = by_pair.get(p, 0) + 1

        return {
            "total_open": total_count,
            "max_per_pair": MAX_POSITIONS_PER_PAIR,
            "exposure_pct": round(exposure_pct * 100, 2),
            "exposure_cap_pct": MAX_ACCOUNT_EXPOSURE_PCT * 100,
            "by_pair": by_pair,
        }


# Global singleton — db injected at startup
position_manager = PositionManager()

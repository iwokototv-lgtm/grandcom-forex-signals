"""
Trade Manager — V4.0 Gold Signals
===================================
Manages open trades stored in MongoDB with automatic Breakeven (BE),
Trailing Stop (TS), and partial-profit management.

Trade lifecycle
---------------
  ACTIVE  → (TP1 hit)  → PARTIAL  → (TP2/TP3 hit or TS triggered) → WIN
  ACTIVE  → (SL hit)   → LOSS
  ACTIVE  → (manual)   → CLOSED

Partial-profit schedule
-----------------------
  TP1 : close 50% of position, activate BE + TS
  TP2 : close 30% of remaining position
  TP3 : close 20% of remaining position (or let TS run)

BE activation
-------------
  When price moves +0.5R in trade direction, SL is moved to entry (zero-risk).

Trailing Stop
-------------
  After TP1 is hit, SL trails price by 1 ATR.  Updated every management cycle.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("trade_manager")

# Partial-profit percentages at each TP level
PARTIAL_SIZES: dict[str, float] = {
    "TP1": 0.50,   # Close 50% at TP1
    "TP2": 0.30,   # Close 30% at TP2
    "TP3": 0.20,   # Close 20% at TP3
}


class TradeManager:
    """
    Manage open trades with BE / TS / partial-profit logic.

    All state is persisted to MongoDB (gold_signals_v4 collection).
    An in-memory cache is maintained for fast access during the management loop.

    Usage
    -----
    At startup call ``await trade_manager.sync_from_mongodb(db)`` to load
    existing open trades.  Then call ``await trade_manager.run_management_cycle(db, prices)``
    every 2 minutes to process BE / TS / partial updates.
    """

    def __init__(self) -> None:
        # trade_id (str) → trade document dict
        self._open_trades: dict[str, dict] = {}

        # Metrics counters (reset on each startup)
        self.metrics: dict[str, int] = {
            "be_activations":  0,
            "ts_updates":      0,
            "partial_closes":  0,
            "trade_closes":    0,
        }

    # ------------------------------------------------------------------
    # Startup sync
    # ------------------------------------------------------------------

    async def sync_from_mongodb(self, db: Any) -> dict:
        """
        Load all ACTIVE / PARTIAL trades from MongoDB into memory at startup.

        Returns a summary dict with the count of trades loaded.
        """
        if db is None:
            logger.warning("TradeManager.sync_from_mongodb: MongoDB not connected")
            return {"synced": False, "error": "MongoDB not connected", "open_trades": 0}

        try:
            cursor = db.gold_signals_v4.find(
                {"status": {"$in": ["ACTIVE", "PARTIAL"]}},
                {"_id": 1, "pair": 1, "type": 1, "entry_price": 1, "sl_price": 1,
                 "tp_levels": 1, "be_trigger": 1, "be_sl": 1, "be_enabled": 1,
                 "ts_start": 1, "ts_distance": 1, "ts_enabled": 1,
                 "status": 1, "lots": 1, "tp1_hit": 1, "tp2_hit": 1, "tp3_hit": 1,
                 "tp1_price": 1, "tp2_price": 1, "tp3_price": 1,
                 "be_activated": 1, "current_sl": 1, "ts_last_price": 1,
                 "created_at": 1},
            )
            trades = await cursor.to_list(500)

            self._open_trades = {}
            for trade in trades:
                trade_id = str(trade["_id"])
                trade["_id_str"] = trade_id
                self._open_trades[trade_id] = trade

            count = len(self._open_trades)
            logger.info(f"✅ TradeManager synced — {count} open trade(s) loaded from MongoDB")
            return {"synced": True, "open_trades": count}

        except Exception as exc:
            logger.error(f"TradeManager.sync_from_mongodb failed: {exc}", exc_info=True)
            return {"synced": False, "error": str(exc), "open_trades": 0}

    # ------------------------------------------------------------------
    # Management cycle (called every 2 minutes)
    # ------------------------------------------------------------------

    async def run_management_cycle(
        self,
        db: Any,
        current_prices: dict[str, float],
    ) -> dict:
        """
        Process all open trades against current prices.

        For each open trade:
          1. Check BE activation (price reached be_trigger)
          2. Check TP1 hit → take partial profit + activate TS
          3. Update trailing stop (if TP1 already hit)
          4. Check SL hit → close as LOSS

        Parameters
        ----------
        db             : Motor MongoDB database instance.
        current_prices : {pair: current_price} mapping, e.g. {"XAUUSD": 2345.50}.

        Returns
        -------
        Summary dict with counts of actions taken.
        """
        summary: dict[str, int] = {
            "trades_checked": 0,
            "be_activations": 0,
            "ts_updates":     0,
            "partial_closes": 0,
            "sl_hits":        0,
        }

        if not self._open_trades:
            return summary

        for trade_id, trade in list(self._open_trades.items()):
            pair          = trade.get("pair", "")
            current_price = current_prices.get(pair)

            if current_price is None or current_price <= 0:
                continue

            summary["trades_checked"] += 1

            signal_type  = trade.get("type", "BUY")
            entry        = float(trade.get("entry_price", 0))
            sl           = float(trade.get("current_sl") or trade.get("sl_price", 0))
            tp_levels    = trade.get("tp_levels", [])
            tp1          = float(tp_levels[0]) if tp_levels else None
            be_trigger   = float(trade.get("be_trigger", 0))
            be_sl        = float(trade.get("be_sl", entry))
            be_enabled   = trade.get("be_enabled", True)
            be_activated = trade.get("be_activated", False)
            ts_enabled   = trade.get("ts_enabled", True)
            ts_start     = float(trade.get("ts_start", 0))
            ts_distance  = float(trade.get("ts_distance", 0))
            tp1_hit      = trade.get("tp1_hit", False)
            atr          = float(trade.get("indicators", {}).get("atr", ts_distance) or ts_distance)

            # ── 1. Check SL hit ──────────────────────────────────────────────
            sl_hit = (
                (signal_type == "BUY"  and current_price <= sl) or
                (signal_type == "SELL" and current_price >= sl)
            ) if sl > 0 else False

            if sl_hit:
                await self.close_trade(db, trade_id, current_price, "LOSS")
                summary["sl_hits"] += 1
                continue

            # ── 2. Breakeven activation ──────────────────────────────────────
            if be_enabled and not be_activated and be_trigger > 0:
                be_hit = (
                    (signal_type == "BUY"  and current_price >= be_trigger) or
                    (signal_type == "SELL" and current_price <= be_trigger)
                )
                if be_hit:
                    await self.activate_breakeven(db, trade_id, current_price)
                    trade["be_activated"] = True
                    trade["current_sl"]   = be_sl
                    sl = be_sl
                    summary["be_activations"] += 1

            # ── 3. TP1 partial profit ────────────────────────────────────────
            if tp1 is not None and not tp1_hit:
                tp1_reached = (
                    (signal_type == "BUY"  and current_price >= tp1) or
                    (signal_type == "SELL" and current_price <= tp1)
                )
                if tp1_reached:
                    await self.take_partial_profit(db, trade_id, "TP1", current_price)
                    trade["tp1_hit"] = True
                    tp1_hit = True
                    summary["partial_closes"] += 1

            # ── 4. Trailing stop update (after TP1 hit) ──────────────────────
            if ts_enabled and tp1_hit and ts_distance > 0:
                updated = await self.update_trailing_stop(
                    db, trade_id, current_price, atr, signal_type, sl
                )
                if updated:
                    summary["ts_updates"] += 1

        # Update module-level metrics
        self.metrics["be_activations"]  += summary["be_activations"]
        self.metrics["ts_updates"]      += summary["ts_updates"]
        self.metrics["partial_closes"]  += summary["partial_closes"]
        self.metrics["trade_closes"]    += summary["sl_hits"]

        return summary

    # ------------------------------------------------------------------
    # Individual trade operations
    # ------------------------------------------------------------------

    async def activate_breakeven(
        self,
        db: Any,
        trade_id: str,
        current_price: float,
    ) -> bool:
        """
        Move SL to entry price when +0.5R profit is reached.

        Idempotent: if BE was already activated (e.g. persisted before a
        restart) this method logs and returns False without touching MongoDB.

        Returns True if the update was applied.
        """
        if db is None:
            return False

        try:
            from bson import ObjectId
            trade = self._open_trades.get(trade_id, {})

            # ── Idempotency guard ────────────────────────────────────────────
            if trade.get("be_activated"):
                logger.info(
                    f"[{trade.get('pair', '?')}] BE already activated — "
                    f"skipping duplicate activation at price {current_price}"
                )
                return False

            be_sl = float(trade.get("be_sl", trade.get("entry_price", 0)))

            await db.gold_signals_v4.update_one(
                {"_id": ObjectId(trade_id)},
                {
                    "$set": {
                        "be_activated":  True,
                        "current_sl":    be_sl,
                        "be_activated_at": datetime.now(timezone.utc),
                        "be_price":      current_price,
                    }
                },
            )

            # Update in-memory cache
            if trade_id in self._open_trades:
                self._open_trades[trade_id]["be_activated"] = True
                self._open_trades[trade_id]["current_sl"]   = be_sl

            logger.info(
                f"[{trade.get('pair', '?')}] 🛡 BE activated — "
                f"SL moved to entry {be_sl} at price {current_price}"
            )
            return True

        except Exception as exc:
            logger.error(f"activate_breakeven({trade_id}) failed: {exc}")
            return False

    async def update_trailing_stop(
        self,
        db: Any,
        trade_id: str,
        current_price: float,
        atr: float,
        signal_type: str,
        current_sl: float,
    ) -> bool:
        """
        Trail SL by 1 ATR after TP1 is hit.

        For BUY : new_sl = current_price - atr  (only moves up)
        For SELL: new_sl = current_price + atr  (only moves down)

        Idempotent: if the SL was already trailed to this exact price level
        (ts_last_price persisted before a restart) the update is skipped to
        prevent re-firing the same TS move on restart.

        Returns True if the SL was actually updated (moved in favour).
        """
        if db is None or atr <= 0:
            return False

        try:
            from bson import ObjectId
            trade = self._open_trades.get(trade_id, {})

            # ── Idempotency guard ────────────────────────────────────────────
            last_ts_price = trade.get("ts_last_price")
            if last_ts_price is not None and float(last_ts_price) == current_price:
                logger.info(
                    f"[{trade.get('pair', '?')}] TS already updated at price "
                    f"{current_price} — skipping duplicate trailing-stop update"
                )
                return False

            if signal_type == "BUY":
                new_sl = round(current_price - atr, 2)
                improved = new_sl > current_sl
            else:
                new_sl = round(current_price + atr, 2)
                improved = new_sl < current_sl

            if not improved:
                return False

            await db.gold_signals_v4.update_one(
                {"_id": ObjectId(trade_id)},
                {
                    "$set": {
                        "current_sl":       new_sl,
                        "ts_last_updated":  datetime.now(timezone.utc),
                        "ts_last_price":    current_price,
                    }
                },
            )

            # Update in-memory cache (including ts_last_price for idempotency)
            if trade_id in self._open_trades:
                self._open_trades[trade_id]["current_sl"]    = new_sl
                self._open_trades[trade_id]["ts_last_price"] = current_price

            logger.info(
                f"[{trade.get('pair', '?')}] 🔄 TS updated — "
                f"SL {current_sl} → {new_sl} (price={current_price}, ATR={atr})"
            )
            return True

        except Exception as exc:
            logger.error(f"update_trailing_stop({trade_id}) failed: {exc}")
            return False

    async def take_partial_profit(
        self,
        db: Any,
        trade_id: str,
        tp_level: str,
        current_price: float,
    ) -> bool:
        """
        Record a partial close at TP1 / TP2 / TP3.

        Partial sizes: TP1=50%, TP2=30%, TP3=20%.
        Updates trade status to PARTIAL and logs the close.

        Idempotent: if the TP level was already recorded (e.g. persisted
        before a restart) this method logs and returns False without touching
        MongoDB, preventing double-counted partial closes.

        Returns True if the update was applied.
        """
        if db is None:
            return False

        try:
            from bson import ObjectId
            trade        = self._open_trades.get(trade_id, {})

            # ── Idempotency guard ────────────────────────────────────────────
            if trade.get(f"{tp_level.lower()}_hit"):
                tp_price = trade.get(f"{tp_level.lower()}_price", "unknown")
                logger.info(
                    f"[{trade.get('pair', '?')}] {tp_level} already hit at "
                    f"{tp_price} — skipping duplicate partial close"
                )
                return False

            partial_pct  = PARTIAL_SIZES.get(tp_level, 0.50)
            lots         = float(trade.get("lots", 0.01))
            partial_lots = round(lots * partial_pct, 2)

            entry        = float(trade.get("entry_price", 0))
            signal_type  = trade.get("type", "BUY")
            pnl_pts      = (
                (current_price - entry) if signal_type == "BUY"
                else (entry - current_price)
            )
            pnl_usd      = round(pnl_pts * partial_lots * 100, 2)   # 100 oz/lot

            update_fields: dict = {
                "status":                "PARTIAL",
                f"{tp_level.lower()}_hit":       True,
                f"{tp_level.lower()}_price":     current_price,
                f"{tp_level.lower()}_lots":      partial_lots,
                f"{tp_level.lower()}_pnl_usd":   pnl_usd,
                f"{tp_level.lower()}_hit_at":    datetime.now(timezone.utc),
            }

            # TP1 also activates BE
            if tp_level == "TP1":
                update_fields["tp1_hit"]     = True
                update_fields["be_activated"] = True
                update_fields["current_sl"]   = float(trade.get("be_sl", entry))

            await db.gold_signals_v4.update_one(
                {"_id": ObjectId(trade_id)},
                {"$set": update_fields},
            )

            # Update in-memory cache
            if trade_id in self._open_trades:
                self._open_trades[trade_id].update(update_fields)

            logger.info(
                f"[{trade.get('pair', '?')}] 💰 Partial close at {tp_level} — "
                f"{partial_lots} lots @ {current_price} | P&L: ${pnl_usd:+.2f}"
            )
            return True

        except Exception as exc:
            logger.error(f"take_partial_profit({trade_id}, {tp_level}) failed: {exc}")
            return False

    async def close_trade(
        self,
        db: Any,
        trade_id: str,
        close_price: float,
        result: str,
    ) -> bool:
        """
        Mark a trade as CLOSED / WIN / LOSS and calculate final P&L.

        Parameters
        ----------
        result : "WIN", "LOSS", or "CLOSED" (manual close).

        Idempotent: if the trade is already in a terminal state (WIN / LOSS /
        CLOSED) this method logs and returns False without touching MongoDB,
        preventing duplicate close operations on restart.

        Returns True if the update was applied.
        """
        if db is None:
            return False

        try:
            from bson import ObjectId
            trade       = self._open_trades.get(trade_id, {})

            # ── Idempotency guard ────────────────────────────────────────────
            current_status = trade.get("status", "")
            if current_status in ("WIN", "LOSS", "CLOSED"):
                logger.info(
                    f"[{trade.get('pair', '?')}] Trade already closed with "
                    f"status '{current_status}' — skipping duplicate close at "
                    f"price {close_price}"
                )
                return False

            entry       = float(trade.get("entry_price", 0))
            lots        = float(trade.get("lots", 0.01))
            signal_type = trade.get("type", "BUY")

            pnl_pts = (
                (close_price - entry) if signal_type == "BUY"
                else (entry - close_price)
            )
            pnl_usd = round(pnl_pts * lots * 100, 2)

            await db.gold_signals_v4.update_one(
                {"_id": ObjectId(trade_id)},
                {
                    "$set": {
                        "status":     result,
                        "result":     result,
                        "close_price": close_price,
                        "close_pnl_usd": pnl_usd,
                        "closed_at":  datetime.now(timezone.utc),
                    }
                },
            )

            # Remove from in-memory cache
            self._open_trades.pop(trade_id, None)

            emoji = "✅" if result == "WIN" else ("❌" if result == "LOSS" else "🔒")
            logger.info(
                f"[{trade.get('pair', '?')}] {emoji} Trade closed — "
                f"{result} @ {close_price} | P&L: ${pnl_usd:+.2f}"
            )
            return True

        except Exception as exc:
            logger.error(f"close_trade({trade_id}, {result}) failed: {exc}")
            return False

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_open_trades(self, pair: Optional[str] = None) -> list[dict]:
        """
        Return all open (ACTIVE / PARTIAL) trades, optionally filtered by pair.
        Uses the in-memory cache — no MongoDB round-trip.
        """
        trades = list(self._open_trades.values())
        if pair:
            trades = [t for t in trades if t.get("pair", "").upper() == pair.upper()]
        return trades

    def get_metrics(self) -> dict:
        """Return cumulative management metrics since last startup."""
        return {
            **self.metrics,
            "open_trade_count": len(self._open_trades),
        }

    def register_new_trade(self, trade_id: str, trade_doc: dict) -> None:
        """
        Register a newly created trade in the in-memory cache.
        Called by gold_server_v4 after inserting a signal into MongoDB.
        """
        self._open_trades[trade_id] = {**trade_doc, "_id_str": trade_id}
        logger.debug(f"TradeManager: registered new trade {trade_id} ({trade_doc.get('pair')})")


# Module-level singleton
_trade_manager = TradeManager()


def get_trade_manager() -> TradeManager:
    """Return the module-level TradeManager singleton."""
    return _trade_manager

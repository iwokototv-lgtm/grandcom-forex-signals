"""
Position Monitor
Runs every 2 minutes to check all open positions for:
  - Stop loss hits
  - Take profit hits (any TP level)
  - Reversal detection (regime flip)
  - Risk limit violations (daily loss > 5%)
  - Drawdown violations (drawdown > 15%)

Does NOT generate new signals — only manages existing positions.
Sends Telegram alerts only when a position is closed.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_LOG_PREFIX = "[POSITION_MON]"


class PositionMonitor:
    """
    Monitors all open positions every 2 minutes and closes them when
    stop loss, take profit, reversal, or risk limits are triggered.

    Dependencies are injected at startup via ``configure()``.
    """

    def __init__(self):
        self._position_manager = None
        self._reversal_detector = None
        self._risk_manager = None
        self._drawdown_recovery = None
        self._fetch_ohlcv = None       # async callable: (pair, interval, outputsize) -> df
        self._close_position_fn = None  # async callable: (position_id, price, reason) -> dict
        self._send_alert_fn = None      # async callable: (msg: str) -> None
        self._pairs: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Dependency injection
    # ------------------------------------------------------------------

    def configure(
        self,
        position_manager,
        reversal_detector,
        risk_manager,
        drawdown_recovery,
        fetch_ohlcv,
        close_position_fn,
        send_alert_fn,
        pairs: Dict[str, Any],
    ) -> None:
        """Inject all required dependencies. Call once at server startup."""
        self._position_manager = position_manager
        self._reversal_detector = reversal_detector
        self._risk_manager = risk_manager
        self._drawdown_recovery = drawdown_recovery
        self._fetch_ohlcv = fetch_ohlcv
        self._close_position_fn = close_position_fn
        self._send_alert_fn = send_alert_fn
        self._pairs = pairs
        logger.info(f"{_LOG_PREFIX} PositionMonitor configured — pairs={list(pairs.keys())}")

    # ------------------------------------------------------------------
    # Main monitoring cycle
    # ------------------------------------------------------------------

    async def run_cycle(self) -> Dict[str, Any]:
        """
        Execute one full monitoring cycle across all open positions.

        Returns a summary dict with counts of checks and closes.
        """
        logger.info(f"{_LOG_PREFIX} Starting 2-min position monitoring cycle")

        if self._position_manager is None:
            logger.warning(f"{_LOG_PREFIX} Not configured — skipping cycle")
            return {"checked": 0, "closed": 0, "errors": 0}

        positions = await self._position_manager.get_open_positions()
        if not positions:
            logger.info(f"{_LOG_PREFIX} No open positions to monitor")
            return {"checked": 0, "closed": 0, "errors": 0}

        logger.info(f"{_LOG_PREFIX} Monitoring {len(positions)} open position(s)")

        # ── Global risk check (applies to all positions) ─────────────────────
        global_halt = await self._check_global_risk()

        checked = 0
        closed = 0
        errors = 0

        # Cache price data per pair to avoid redundant API calls
        price_cache: Dict[str, Optional[float]] = {}
        df_cache: Dict[str, Any] = {}

        for position in positions:
            try:
                pair = position.get("pair", "")
                position_id = str(position.get("_id", ""))
                signal_type = position.get("signal_type", "BUY")
                entry_price = float(position.get("entry_price", 0))
                sl_price = float(position.get("sl_price", 0))
                tp_levels: List[float] = [
                    float(t) for t in position.get("tp_levels", [])
                ]

                if not pair or not position_id:
                    logger.warning(f"{_LOG_PREFIX} Skipping malformed position: {position}")
                    continue

                checked += 1

                # ── Fetch current price (cached per pair) ─────────────────────
                if pair not in price_cache:
                    df = await self._fetch_ohlcv(pair, "4h", 5)
                    if df is not None and len(df) > 0:
                        price_cache[pair] = float(df.iloc[-1]["close"])
                        df_cache[pair] = df
                    else:
                        price_cache[pair] = None
                        df_cache[pair] = None
                        logger.warning(
                            f"{_LOG_PREFIX} [{pair}] Could not fetch price — skipping position"
                        )

                current_price = price_cache.get(pair)
                if current_price is None:
                    errors += 1
                    continue

                # ── Check 1: Global risk halt ─────────────────────────────────
                if global_halt["halted"]:
                    reason = global_halt["reason"]
                    logger.warning(
                        f"{_LOG_PREFIX} [{pair}] Global risk halt — closing position: {reason}"
                    )
                    result = await self._close_position_fn(position_id, current_price, reason)
                    if result.get("success"):
                        closed += 1
                        await self._send_close_alert(
                            pair, signal_type, entry_price, current_price,
                            result.get("pnl", 0.0), reason
                        )
                    continue

                # ── Check 2: Stop loss hit ────────────────────────────────────
                sl_hit = (
                    (signal_type == "BUY" and current_price <= sl_price) or
                    (signal_type == "SELL" and current_price >= sl_price)
                )
                if sl_hit:
                    logger.warning(
                        f"{_LOG_PREFIX} [{pair}] STOP_LOSS hit — "
                        f"price={current_price} sl={sl_price}"
                    )
                    result = await self._close_position_fn(
                        position_id, current_price, "STOP_LOSS"
                    )
                    if result.get("success"):
                        closed += 1
                        await self._send_close_alert(
                            pair, signal_type, entry_price, current_price,
                            result.get("pnl", 0.0), "STOP_LOSS"
                        )
                    continue

                # ── Check 3: Take profit hit (any level) ──────────────────────
                tp_hit = False
                for i, tp in enumerate(tp_levels):
                    hit = (
                        (signal_type == "BUY" and current_price >= tp) or
                        (signal_type == "SELL" and current_price <= tp)
                    )
                    if hit:
                        tp_label = f"TAKE_PROFIT_{i + 1}"
                        logger.info(
                            f"{_LOG_PREFIX} [{pair}] {tp_label} hit — "
                            f"price={current_price} tp={tp}"
                        )
                        result = await self._close_position_fn(
                            position_id, current_price, tp_label
                        )
                        if result.get("success"):
                            closed += 1
                            await self._send_close_alert(
                                pair, signal_type, entry_price, current_price,
                                result.get("pnl", 0.0), tp_label
                            )
                        tp_hit = True
                        break

                if tp_hit:
                    continue

                # ── Check 4: Reversal detection ───────────────────────────────
                df = df_cache.get(pair)
                if df is not None and len(df) >= 5:
                    try:
                        reversal = await self._reversal_detector.detect_reversal(
                            pair, df, signal_type
                        )
                        if reversal.get("reversal_detected"):
                            rev_reason = reversal.get("reason", "REVERSAL")
                            logger.warning(
                                f"{_LOG_PREFIX} [{pair}] REVERSAL detected — "
                                f"closing position: {rev_reason}"
                            )
                            result = await self._close_position_fn(
                                position_id, current_price, "REVERSAL"
                            )
                            if result.get("success"):
                                closed += 1
                                await self._send_close_alert(
                                    pair, signal_type, entry_price, current_price,
                                    result.get("pnl", 0.0), f"REVERSAL: {rev_reason}"
                                )
                            continue
                    except Exception as exc:
                        logger.warning(
                            f"{_LOG_PREFIX} [{pair}] Reversal check error (fail-open): {exc}"
                        )

                # ── Check 5: Drawdown recovery halt ───────────────────────────
                try:
                    dd_assessment = self._drawdown_recovery.assess(
                        current_balance=self._risk_manager.current_equity
                    )
                    if dd_assessment.get("trading_halted"):
                        halt_reason = dd_assessment.get("halt_reason", "DRAWDOWN_HALT")
                        logger.warning(
                            f"{_LOG_PREFIX} [{pair}] DrawdownRecovery halt — "
                            f"closing position: {halt_reason}"
                        )
                        result = await self._close_position_fn(
                            position_id, current_price, halt_reason
                        )
                        if result.get("success"):
                            closed += 1
                            await self._send_close_alert(
                                pair, signal_type, entry_price, current_price,
                                result.get("pnl", 0.0), halt_reason
                            )
                        continue
                except Exception as exc:
                    logger.warning(
                        f"{_LOG_PREFIX} [{pair}] Drawdown check error (fail-open): {exc}"
                    )

                logger.debug(
                    f"{_LOG_PREFIX} [{pair}] Position OK — "
                    f"price={current_price} entry={entry_price} sl={sl_price}"
                )

            except Exception as exc:
                errors += 1
                logger.error(
                    f"{_LOG_PREFIX} Unhandled error monitoring position "
                    f"{position.get('_id', '?')}: {exc}",
                    exc_info=True,
                )

        summary = {
            "checked": checked,
            "closed": closed,
            "errors": errors,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(
            f"{_LOG_PREFIX} Cycle complete — "
            f"checked={checked} closed={closed} errors={errors}"
        )
        return summary

    # ------------------------------------------------------------------
    # Global risk check
    # ------------------------------------------------------------------

    async def _check_global_risk(self) -> Dict[str, Any]:
        """
        Check global risk limits that would require closing ALL positions.
        Returns ``{"halted": bool, "reason": str}``.
        """
        if self._risk_manager is None:
            return {"halted": False, "reason": "OK"}
        try:
            risk_check = await self._risk_manager.enforce_risk_limits()
            if not risk_check.get("trading_allowed", True):
                return {
                    "halted": True,
                    "reason": risk_check.get("reason", "RISK_LIMIT"),
                }
        except Exception as exc:
            logger.warning(f"{_LOG_PREFIX} Global risk check error (fail-open): {exc}")
        return {"halted": False, "reason": "OK"}

    # ------------------------------------------------------------------
    # Telegram alert for position close
    # ------------------------------------------------------------------

    async def _send_close_alert(
        self,
        pair: str,
        signal_type: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
        reason: str,
    ) -> None:
        """Send a Telegram alert when a position is closed by the monitor."""
        if self._send_alert_fn is None:
            return

        emoji_map = {
            "STOP_LOSS": "🛑",
            "TAKE_PROFIT_1": "✅",
            "TAKE_PROFIT_2": "✅✅",
            "TAKE_PROFIT_3": "✅✅✅",
            "REVERSAL": "🔄",
        }
        # Default emoji for risk-based closes
        emoji = emoji_map.get(reason.split(":")[0].strip(), "⚠️")
        pnl_sign = "+" if pnl >= 0 else ""
        direction_emoji = "🟢" if signal_type == "BUY" else "🔴"

        msg = (
            f"{emoji} <b>POSITION CLOSED — {pair}</b>\n"
            f"\n"
            f"<b>Direction:</b> {direction_emoji} {signal_type}\n"
            f"<b>Reason:</b> {reason}\n"
            f"<b>Entry:</b> {entry_price}  →  <b>Exit:</b> {exit_price}\n"
            f"<b>P&L:</b> {pnl_sign}{pnl:.2f}\n"
            f"<i>⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
            f"| Position Monitor</i>"
        )

        try:
            await self._send_alert_fn(msg)
            logger.info(
                f"{_LOG_PREFIX} [{pair}] Close alert sent — reason={reason} pnl={pnl_sign}{pnl:.2f}"
            )
        except Exception as exc:
            logger.error(f"{_LOG_PREFIX} [{pair}] Close alert failed: {exc}")


# Global singleton — configured at server startup
position_monitor = PositionMonitor()

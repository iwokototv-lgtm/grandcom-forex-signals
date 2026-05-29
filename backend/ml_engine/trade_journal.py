"""
Trade Journal — v3.0
Persistent trade logging, analysis, and pattern recognition.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class TradeJournal:
    """
    Comprehensive trade journal with:
    - Full trade lifecycle tracking (signal → entry → exit)
    - Pattern recognition (time-of-day, day-of-week, regime patterns)
    - Mistake categorisation (premature exit, oversized, wrong regime, etc.)
    - Exportable trade log for external analysis
    """

    MISTAKE_CATEGORIES: list[str] = [
        "WRONG_REGIME",
        "LOW_CONFIDENCE",
        "OVERSIZED",
        "PREMATURE_EXIT",
        "LATE_ENTRY",
        "AGAINST_TREND",
        "NEWS_EVENT",
        "CORRELATION_VIOLATION",
        "NONE",
    ]

    def __init__(self) -> None:
        self._journal: list[dict] = []
        self._open_trades: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Trade Lifecycle
    # ------------------------------------------------------------------

    def open_trade(
        self,
        trade_id: str,
        symbol: str,
        direction: str,
        entry_price: float,
        tp_levels: list[float],
        sl: float,
        lots: float,
        strategy: str,
        regime: str,
        confidence: float,
        timeframe: str,
        signal_analysis: str = "",
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        """Record a new trade opening."""
        now = datetime.now(timezone.utc)
        trade = {
            "trade_id": trade_id,
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry_price,
            "tp_levels": tp_levels,
            "sl": sl,
            "lots": lots,
            "strategy": strategy,
            "regime": regime,
            "confidence": confidence,
            "timeframe": timeframe,
            "signal_analysis": signal_analysis,
            "metadata": metadata or {},
            "status": "OPEN",
            "opened_at": now.isoformat(),
            "closed_at": None,
            "exit_price": None,
            "pnl_pips": None,
            "pnl_usd": None,
            "exit_reason": None,
            "mistake": "NONE",
            "notes": "",
            "max_adverse_excursion": 0.0,
            "max_favourable_excursion": 0.0,
        }
        self._open_trades[trade_id] = trade
        logger.info(
            f"[Journal] Trade opened: {trade_id} {symbol} {direction} "
            f"@ {entry_price} strategy={strategy} conf={confidence:.0f}%"
        )
        return trade

    def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str = "MANUAL",
        mistake: str = "NONE",
        notes: str = "",
    ) -> dict[str, Any] | None:
        """Record a trade closing."""
        if trade_id not in self._open_trades:
            logger.warning(f"[Journal] Trade {trade_id} not found in open trades")
            return None

        trade = self._open_trades.pop(trade_id)
        now = datetime.now(timezone.utc)

        trade["status"] = "CLOSED"
        trade["closed_at"] = now.isoformat()
        trade["exit_price"] = exit_price
        trade["exit_reason"] = exit_reason
        trade["mistake"] = mistake if mistake in self.MISTAKE_CATEGORIES else "NONE"
        trade["notes"] = notes

        # Calculate P&L
        direction = trade["direction"]
        entry = trade["entry_price"]
        lots = trade["lots"]

        if direction == "BUY":
            pnl_pips = exit_price - entry
        else:
            pnl_pips = entry - exit_price

        # Approximate USD P&L (for gold: 1 pip ≈ $1 per 0.01 lot)
        pnl_usd = pnl_pips * lots * 100

        trade["pnl_pips"] = round(pnl_pips, 4)
        trade["pnl_usd"] = round(pnl_usd, 2)

        self._journal.append(trade)

        # Keep last 500 closed trades
        if len(self._journal) > 500:
            self._journal = self._journal[-500:]

        logger.info(
            f"[Journal] Trade closed: {trade_id} exit={exit_price} "
            f"pnl={pnl_usd:+.2f} reason={exit_reason}"
        )
        return trade

    def update_excursion(
        self,
        trade_id: str,
        current_price: float,
    ) -> None:
        """Update MAE/MFE for an open trade."""
        if trade_id not in self._open_trades:
            return

        trade = self._open_trades[trade_id]
        entry = trade["entry_price"]
        direction = trade["direction"]

        if direction == "BUY":
            excursion = current_price - entry
        else:
            excursion = entry - current_price

        if excursion < 0:
            trade["max_adverse_excursion"] = min(
                trade["max_adverse_excursion"], excursion
            )
        else:
            trade["max_favourable_excursion"] = max(
                trade["max_favourable_excursion"], excursion
            )

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def pattern_analysis(self) -> dict[str, Any]:
        """Identify patterns in closed trade history."""
        if not self._journal:
            return {"total_trades": 0, "patterns": {}}

        # Day-of-week performance
        dow_pnl: dict[str, list[float]] = {}
        hour_pnl: dict[int, list[float]] = {}
        mistake_counts: dict[str, int] = {}

        for trade in self._journal:
            pnl = trade.get("pnl_usd", 0.0) or 0.0
            opened = trade.get("opened_at", "")
            mistake = trade.get("mistake", "NONE")

            try:
                dt = datetime.fromisoformat(opened)
                dow = dt.strftime("%A")
                hour = dt.hour
                dow_pnl.setdefault(dow, []).append(pnl)
                hour_pnl.setdefault(hour, []).append(pnl)
            except Exception:
                pass

            mistake_counts[mistake] = mistake_counts.get(mistake, 0) + 1

        best_day = max(dow_pnl, key=lambda d: sum(dow_pnl[d]), default=None)
        worst_day = min(dow_pnl, key=lambda d: sum(dow_pnl[d]), default=None)
        best_hour = max(hour_pnl, key=lambda h: sum(hour_pnl[h]), default=None)

        return {
            "total_trades": len(self._journal),
            "open_trades": len(self._open_trades),
            "patterns": {
                "best_day_of_week": best_day,
                "worst_day_of_week": worst_day,
                "best_hour_utc": best_hour,
                "day_of_week_pnl": {
                    d: round(sum(v), 2) for d, v in dow_pnl.items()
                },
                "mistake_distribution": mistake_counts,
            },
        }

    def get_open_trades(self) -> list[dict]:
        """Return all currently open trades."""
        return list(self._open_trades.values())

    def get_closed_trades(self, limit: int = 50) -> list[dict]:
        """Return most recent closed trades."""
        return self._journal[-limit:]

    def get_trade(self, trade_id: str) -> dict | None:
        """Retrieve a specific trade by ID (open or closed)."""
        if trade_id in self._open_trades:
            return self._open_trades[trade_id]
        for t in reversed(self._journal):
            if t["trade_id"] == trade_id:
                return t
        return None

    def summary(self) -> dict[str, Any]:
        """High-level journal summary."""
        closed = self._journal
        if not closed:
            return {
                "total_closed": 0,
                "total_open": len(self._open_trades),
                "win_rate": 0.0,
                "total_pnl": 0.0,
            }

        pnls = [t.get("pnl_usd", 0.0) or 0.0 for t in closed]
        wins = [p for p in pnls if p > 0]

        return {
            "total_closed": len(closed),
            "total_open": len(self._open_trades),
            "win_rate": round(len(wins) / len(pnls), 3) if pnls else 0.0,
            "total_pnl": round(sum(pnls), 2),
            "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
            "best_trade": round(max(pnls), 2) if pnls else 0.0,
            "worst_trade": round(min(pnls), 2) if pnls else 0.0,
            "profit_factor": (
                round(sum(wins) / abs(sum(p for p in pnls if p < 0)), 2)
                if any(p < 0 for p in pnls)
                else 0.0
            ),
        }


# Module-level singleton
trade_journal = TradeJournal()

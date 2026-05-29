"""
Performance Attributor — v3.0
Tracks and attributes P&L to strategies, regimes, timeframes, and pairs.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


class PerformanceAttributor:
    """
    Decomposes portfolio performance into attributable components:

    - By strategy (SMC/ICT, Mean Reversion, Trend)
    - By market regime (Trend Up/Down, Range, High/Low Vol)
    - By timeframe (1H, 4H, Daily, Weekly)
    - By pair (XAUUSD, XAUEUR)
    - By session (London, New York, Asia)
    - By signal confidence bucket (60-70%, 70-80%, 80%+)
    """

    def __init__(self) -> None:
        self._trades: list[dict] = []

    # ------------------------------------------------------------------
    # Trade Recording
    # ------------------------------------------------------------------

    def record_trade(
        self,
        trade_id: str,
        symbol: str,
        direction: str,
        strategy: str,
        regime: str,
        timeframe: str,
        confidence: float,
        entry_price: float,
        exit_price: float,
        lots: float,
        pnl_usd: float,
        session: str = "",
        opened_at: datetime | None = None,
        closed_at: datetime | None = None,
    ) -> None:
        """Record a completed trade for attribution analysis."""
        now = datetime.now(timezone.utc)
        trade = {
            "trade_id": trade_id,
            "symbol": symbol,
            "direction": direction,
            "strategy": strategy,
            "regime": regime,
            "timeframe": timeframe,
            "confidence": confidence,
            "confidence_bucket": self._confidence_bucket(confidence),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "lots": lots,
            "pnl_usd": pnl_usd,
            "session": session or self._current_session(opened_at or now),
            "opened_at": (opened_at or now).isoformat(),
            "closed_at": (closed_at or now).isoformat(),
            "recorded_at": now.isoformat(),
            "win": pnl_usd > 0,
        }
        self._trades.append(trade)

        # Keep last 1000 trades
        if len(self._trades) > 1000:
            self._trades = self._trades[-1000:]

        logger.info(
            f"[Attributor] Trade recorded: {symbol} {direction} "
            f"strategy={strategy} pnl={pnl_usd:+.2f} "
            f"regime={regime} conf={confidence:.0f}%"
        )

    # ------------------------------------------------------------------
    # Attribution Reports
    # ------------------------------------------------------------------

    def by_strategy(self, lookback_days: int = 30) -> dict[str, Any]:
        """P&L and win-rate attribution by strategy."""
        trades = self._recent_trades(lookback_days)
        return self._group_attribution(trades, "strategy")

    def by_regime(self, lookback_days: int = 30) -> dict[str, Any]:
        """P&L and win-rate attribution by market regime."""
        trades = self._recent_trades(lookback_days)
        return self._group_attribution(trades, "regime")

    def by_pair(self, lookback_days: int = 30) -> dict[str, Any]:
        """P&L and win-rate attribution by trading pair."""
        trades = self._recent_trades(lookback_days)
        return self._group_attribution(trades, "symbol")

    def by_timeframe(self, lookback_days: int = 30) -> dict[str, Any]:
        """P&L and win-rate attribution by timeframe."""
        trades = self._recent_trades(lookback_days)
        return self._group_attribution(trades, "timeframe")

    def by_session(self, lookback_days: int = 30) -> dict[str, Any]:
        """P&L and win-rate attribution by trading session."""
        trades = self._recent_trades(lookback_days)
        return self._group_attribution(trades, "session")

    def by_confidence(self, lookback_days: int = 30) -> dict[str, Any]:
        """P&L and win-rate attribution by confidence bucket."""
        trades = self._recent_trades(lookback_days)
        return self._group_attribution(trades, "confidence_bucket")

    def full_report(self, lookback_days: int = 30) -> dict[str, Any]:
        """Complete attribution report across all dimensions."""
        trades = self._recent_trades(lookback_days)
        total_pnl = sum(t["pnl_usd"] for t in trades)
        wins = [t for t in trades if t["win"]]

        return {
            "period_days": lookback_days,
            "total_trades": len(trades),
            "total_pnl_usd": round(total_pnl, 2),
            "win_rate": round(len(wins) / len(trades), 3) if trades else 0.0,
            "avg_pnl_per_trade": round(total_pnl / len(trades), 2) if trades else 0.0,
            "by_strategy": self._group_attribution(trades, "strategy"),
            "by_regime": self._group_attribution(trades, "regime"),
            "by_pair": self._group_attribution(trades, "symbol"),
            "by_timeframe": self._group_attribution(trades, "timeframe"),
            "by_session": self._group_attribution(trades, "session"),
            "by_confidence": self._group_attribution(trades, "confidence_bucket"),
            "best_strategy": self._best_group(trades, "strategy"),
            "worst_strategy": self._worst_group(trades, "strategy"),
            "best_regime": self._best_group(trades, "regime"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    def _recent_trades(self, lookback_days: int) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        result = []
        for t in self._trades:
            try:
                closed = datetime.fromisoformat(t["closed_at"])
                if closed.tzinfo is None:
                    closed = closed.replace(tzinfo=timezone.utc)
                if closed >= cutoff:
                    result.append(t)
            except Exception:
                result.append(t)
        return result

    def _group_attribution(
        self, trades: list[dict], key: str
    ) -> dict[str, dict[str, Any]]:
        groups: dict[str, list[dict]] = defaultdict(list)
        for t in trades:
            groups[str(t.get(key, "UNKNOWN"))].append(t)

        result: dict[str, dict[str, Any]] = {}
        for group_name, group_trades in groups.items():
            pnls = [t["pnl_usd"] for t in group_trades]
            wins = [t for t in group_trades if t["win"]]
            total_pnl = sum(pnls)
            result[group_name] = {
                "trades": len(group_trades),
                "wins": len(wins),
                "losses": len(group_trades) - len(wins),
                "win_rate": round(len(wins) / len(group_trades), 3),
                "total_pnl": round(total_pnl, 2),
                "avg_pnl": round(total_pnl / len(group_trades), 2),
                "best_trade": round(max(pnls), 2) if pnls else 0.0,
                "worst_trade": round(min(pnls), 2) if pnls else 0.0,
            }
        return result

    def _best_group(self, trades: list[dict], key: str) -> str | None:
        groups = self._group_attribution(trades, key)
        if not groups:
            return None
        return max(groups, key=lambda k: groups[k]["total_pnl"])

    def _worst_group(self, trades: list[dict], key: str) -> str | None:
        groups = self._group_attribution(trades, key)
        if not groups:
            return None
        return min(groups, key=lambda k: groups[k]["total_pnl"])

    @staticmethod
    def _confidence_bucket(confidence: float) -> str:
        if confidence >= 85:
            return "85%+"
        if confidence >= 75:
            return "75-85%"
        if confidence >= 65:
            return "65-75%"
        return "<65%"

    @staticmethod
    def _current_session(dt: datetime) -> str:
        hour = dt.hour
        if 0 <= hour < 8:
            return "ASIA"
        if 7 <= hour < 16:
            return "LONDON"
        if 13 <= hour < 22:
            return "NEW_YORK"
        return "OFF_HOURS"


# Module-level singleton
performance_attributor = PerformanceAttributor()

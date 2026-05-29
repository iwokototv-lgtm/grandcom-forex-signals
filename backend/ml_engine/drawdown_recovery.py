"""
Drawdown Recovery Manager — v3.0
Manages position sizing and trading behaviour during drawdown periods.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


class DrawdownRecoveryManager:
    """
    Monitors portfolio drawdown and applies recovery protocols:

    - Soft drawdown (5-10%): Reduce position sizes by 50%
    - Hard drawdown (10-15%): Reduce to 25%, increase selectivity
    - Critical drawdown (>15%): Pause trading, alert operator
    - Recovery mode: Gradually restore position sizes as equity recovers

    Also tracks:
    - Consecutive loss streaks
    - Daily / weekly loss limits
    - Maximum adverse excursion (MAE) per trade
    """

    SOFT_THRESHOLD: float = 0.05      # 5% drawdown
    HARD_THRESHOLD: float = 0.10      # 10% drawdown
    CRITICAL_THRESHOLD: float = 0.15  # 15% drawdown

    def __init__(
        self,
        recovery_threshold: float = 0.05,
        recovery_scale: float = 0.50,
        max_consecutive_losses: int = 3,
        daily_loss_limit: float = 0.03,
        weekly_loss_limit: float = 0.06,
    ) -> None:
        self.recovery_threshold = recovery_threshold
        self.recovery_scale = recovery_scale
        self.max_consecutive_losses = max_consecutive_losses
        self.daily_loss_limit = daily_loss_limit
        self.weekly_loss_limit = weekly_loss_limit

        # State
        self.equity_peak: float = 0.0
        self.current_equity: float = 0.0
        self.consecutive_losses: int = 0
        self.daily_pnl: float = 0.0
        self.weekly_pnl: float = 0.0
        self.monthly_pnl: float = 0.0
        self.trade_history: list[dict] = []
        self._day_start: datetime = datetime.utcnow()
        self._week_start: datetime = datetime.utcnow()
        self._paused_until: datetime | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def initialise(self, starting_equity: float) -> None:
        """Set initial equity baseline."""
        self.equity_peak = starting_equity
        self.current_equity = starting_equity
        logger.info(f"[DrawdownMgr] Initialised with equity={starting_equity:,.2f}")

    def record_trade(self, pnl: float, symbol: str = "", strategy: str = "") -> None:
        """Record a completed trade result."""
        self.current_equity += pnl
        self.daily_pnl += pnl
        self.weekly_pnl += pnl
        self.monthly_pnl += pnl

        if self.current_equity > self.equity_peak:
            self.equity_peak = self.current_equity

        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        self.trade_history.append(
            {
                "pnl": pnl,
                "symbol": symbol,
                "strategy": strategy,
                "equity": self.current_equity,
                "drawdown": self.current_drawdown(),
                "timestamp": datetime.utcnow().isoformat(),
            }
        )

        # Keep last 200 trades
        if len(self.trade_history) > 200:
            self.trade_history = self.trade_history[-200:]

        logger.info(
            f"[DrawdownMgr] Trade recorded: pnl={pnl:+.2f} "
            f"equity={self.current_equity:,.2f} "
            f"drawdown={self.current_drawdown():.1%} "
            f"consecutive_losses={self.consecutive_losses}"
        )

    def reset_daily(self) -> None:
        """Reset daily PnL counter (call at start of each trading day)."""
        self.daily_pnl = 0.0
        self._day_start = datetime.utcnow()

    def reset_weekly(self) -> None:
        """Reset weekly PnL counter."""
        self.weekly_pnl = 0.0
        self._week_start = datetime.utcnow()

    def current_drawdown(self) -> float:
        """Current drawdown from equity peak (0.0 = no drawdown, 0.10 = 10%)."""
        if self.equity_peak <= 0:
            return 0.0
        return max(0.0, (self.equity_peak - self.current_equity) / self.equity_peak)

    def get_status(self) -> dict[str, Any]:
        """
        Get current drawdown status and trading permissions.

        Returns:
            dict with drawdown_level, can_trade, position_scale,
            pause_reason, and recovery_progress.
        """
        dd = self.current_drawdown()
        can_trade = True
        pause_reason: str | None = None
        position_scale = 1.0

        # Check pause timer
        if self._paused_until and datetime.utcnow() < self._paused_until:
            can_trade = False
            pause_reason = f"Paused until {self._paused_until.isoformat()}"
            position_scale = 0.0
        # Critical drawdown
        elif dd >= self.CRITICAL_THRESHOLD:
            can_trade = False
            pause_reason = f"Critical drawdown {dd:.1%} — trading suspended"
            position_scale = 0.0
            self._set_pause(hours=24)
        # Hard drawdown
        elif dd >= self.HARD_THRESHOLD:
            position_scale = 0.25
            pause_reason = f"Hard drawdown {dd:.1%} — position size 25%"
        # Soft drawdown
        elif dd >= self.SOFT_THRESHOLD:
            position_scale = self.recovery_scale
            pause_reason = f"Soft drawdown {dd:.1%} — position size {self.recovery_scale:.0%}"
        # Consecutive losses
        elif self.consecutive_losses >= self.max_consecutive_losses:
            position_scale = 0.50
            pause_reason = f"{self.consecutive_losses} consecutive losses — reduced sizing"
        # Daily loss limit
        elif self.equity_peak > 0 and abs(self.daily_pnl) / self.equity_peak >= self.daily_loss_limit:
            can_trade = False
            pause_reason = f"Daily loss limit reached ({self.daily_loss_limit:.1%})"
            position_scale = 0.0
        # Weekly loss limit
        elif self.equity_peak > 0 and abs(self.weekly_pnl) / self.equity_peak >= self.weekly_loss_limit:
            can_trade = False
            pause_reason = f"Weekly loss limit reached ({self.weekly_loss_limit:.1%})"
            position_scale = 0.0

        # Recovery progress
        recovery_progress = 0.0
        if dd > 0 and self.equity_peak > 0:
            recovery_progress = 1.0 - (dd / self.SOFT_THRESHOLD) if dd < self.SOFT_THRESHOLD else 0.0

        return {
            "can_trade": can_trade,
            "position_scale": round(position_scale, 2),
            "drawdown_level": round(dd, 4),
            "drawdown_pct": f"{dd:.1%}",
            "drawdown_regime": self._drawdown_regime(dd),
            "consecutive_losses": self.consecutive_losses,
            "daily_pnl": round(self.daily_pnl, 2),
            "weekly_pnl": round(self.weekly_pnl, 2),
            "monthly_pnl": round(self.monthly_pnl, 2),
            "equity_peak": round(self.equity_peak, 2),
            "current_equity": round(self.current_equity, 2),
            "pause_reason": pause_reason,
            "recovery_progress": round(recovery_progress, 3),
            "timestamp": datetime.utcnow().isoformat(),
        }

    def apply_to_position_size(self, base_lots: float) -> float:
        """Apply drawdown scaling to a base position size."""
        status = self.get_status()
        if not status["can_trade"]:
            return 0.0
        adjusted = base_lots * status["position_scale"]
        return max(0.01, round(adjusted, 2)) if adjusted > 0 else 0.0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _drawdown_regime(self, dd: float) -> str:
        if dd >= self.CRITICAL_THRESHOLD:
            return "CRITICAL"
        if dd >= self.HARD_THRESHOLD:
            return "HARD"
        if dd >= self.SOFT_THRESHOLD:
            return "SOFT"
        return "NORMAL"

    def _set_pause(self, hours: int = 24) -> None:
        self._paused_until = datetime.utcnow() + timedelta(hours=hours)
        logger.warning(
            f"[DrawdownMgr] Trading paused for {hours}h until {self._paused_until.isoformat()}"
        )

    def get_trade_stats(self) -> dict[str, Any]:
        """Return summary statistics from trade history."""
        if not self.trade_history:
            return {"total_trades": 0}

        pnls = [t["pnl"] for t in self.trade_history]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        return {
            "total_trades": len(pnls),
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": round(len(wins) / len(pnls), 3) if pnls else 0.0,
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
            "profit_factor": (
                round(sum(wins) / abs(sum(losses)), 2)
                if losses and sum(losses) != 0
                else 0.0
            ),
            "total_pnl": round(sum(pnls), 2),
            "max_consecutive_losses": self.consecutive_losses,
        }


# Module-level singleton
drawdown_recovery_manager = DrawdownRecoveryManager()

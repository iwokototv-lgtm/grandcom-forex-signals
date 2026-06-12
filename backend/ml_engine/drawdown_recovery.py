"""
Drawdown Recovery Manager
Gradual position size recovery after drawdown periods
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class DrawdownRecoveryManager:
    """
    Drawdown Recovery Management System.

    Implements a gradual recovery protocol that reduces position sizes
    during drawdown periods and slowly restores them as performance recovers.

    Features:
    - Real-time drawdown tracking (peak-to-trough)
    - Tiered recovery levels (25%, 50%, 75%, 100%)
    - Recovery speed control (conservative/moderate/aggressive)
    - Maximum drawdown circuit breaker
    - Consecutive loss tracking
    - Win rate monitoring for recovery confirmation
    """

    def __init__(
        self,
        max_drawdown_pct: float = 15.0,
        daily_drawdown_limit: float = 5.0,
        recovery_factor: float = 0.5,
        recovery_speed: str = "moderate",
        consecutive_loss_limit: int = 5,
        min_win_rate_for_recovery: float = 0.45,
    ):
        self.max_drawdown_pct = max_drawdown_pct
        self.daily_drawdown_limit = daily_drawdown_limit
        self.recovery_factor = recovery_factor
        self.recovery_speed = recovery_speed
        self.consecutive_loss_limit = consecutive_loss_limit
        self.min_win_rate_for_recovery = min_win_rate_for_recovery
        self.version = "3.0.0"

        # State
        self.peak_balance: float = 0.0  # Initialised on first assess() call
        self._peak_initialised: bool = False
        self.current_balance: float = 0.0
        self.daily_start_balance: float = 0.0
        self.trade_history: List[Dict] = []
        self.consecutive_losses: int = 0
        self.trading_halted: bool = False
        self.halt_reason: str = ""

    # ------------------------------------------------------------------
    # Main Assessment
    # ------------------------------------------------------------------

    def assess(
        self,
        current_balance: float,
        trade_results: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """
        Assess current drawdown state and return position size multiplier.

        Args:
            current_balance: Current account balance
            trade_results: Recent trade results [{"pnl": float, "timestamp": str}]

        Returns:
            Assessment with size multiplier and recovery status
        """
        try:
            # Initialise peak on first call so it reflects the real starting balance
            if not self._peak_initialised:
                self.peak_balance = current_balance
                self._peak_initialised = True
                logger.info(
                    f"[DRAWDOWN] Initialised — peak={self.peak_balance:.2f} "
                    f"starting={current_balance:.2f}"
                )

            # Update state
            if current_balance > self.peak_balance:
                self.peak_balance = current_balance
                logger.info(f"[DRAWDOWN] New peak — {self.peak_balance:.2f}")
            self.current_balance = current_balance

            if self.daily_start_balance == 0:
                self.daily_start_balance = current_balance

            # Update trade history
            if trade_results:
                self._update_trade_history(trade_results)

            # Calculate drawdown metrics
            drawdown = self._calculate_drawdown()
            daily_dd = self._calculate_daily_drawdown()
            consecutive = self._count_consecutive_losses()
            win_rate = self._calculate_win_rate()

            # Determine trading status
            halt, halt_reason = self._check_halt_conditions(drawdown, daily_dd, consecutive)
            self.trading_halted = halt
            self.halt_reason = halt_reason

            # Calculate size multiplier
            multiplier = self._calculate_multiplier(drawdown, consecutive, win_rate)

            # Recovery level
            recovery_level = self._determine_recovery_level(drawdown)

            result = {
                "valid": True,
                "trading_halted": halt,
                "halt_reason": halt_reason if halt else None,
                "size_multiplier": round(multiplier, 4),
                "recovery_level": recovery_level,
                "drawdown": drawdown,
                "daily_drawdown": daily_dd,
                "consecutive_losses": consecutive,
                "win_rate": round(win_rate, 4),
                "peak_balance": round(self.peak_balance, 2),
                "current_balance": round(current_balance, 2),
                "recovery_speed": self.recovery_speed,
                "timestamp": datetime.utcnow().isoformat(),
                "version": self.version,
            }

            logger.info(
                f"DrawdownRecovery: dd={drawdown['current_pct']:.2f}% "
                f"mult={multiplier:.3f} halt={halt} level={recovery_level}"
            )
            return result

        except Exception as exc:
            logger.error(f"Drawdown assessment error: {exc}", exc_info=True)
            return {
                "valid": False,
                "trading_halted": False,
                "size_multiplier": 1.0,
                "error": str(exc),
            }

    # ------------------------------------------------------------------
    # Drawdown Calculation
    # ------------------------------------------------------------------

    def _calculate_drawdown(self) -> Dict[str, Any]:
        """Calculate current drawdown from peak."""
        if self.peak_balance <= 0:
            return {"current_pct": 0.0, "current_abs": 0.0, "severity": "NONE"}

        current_dd_abs = self.peak_balance - self.current_balance
        current_dd_pct = (current_dd_abs / self.peak_balance) * 100

        if current_dd_pct >= self.max_drawdown_pct:
            severity = "CRITICAL"
        elif current_dd_pct >= self.max_drawdown_pct * 0.75:
            severity = "SEVERE"
        elif current_dd_pct >= self.max_drawdown_pct * 0.5:
            severity = "MODERATE"
        elif current_dd_pct >= self.max_drawdown_pct * 0.25:
            severity = "MILD"
        else:
            severity = "NONE"

        return {
            "current_pct": round(current_dd_pct, 4),
            "current_abs": round(current_dd_abs, 2),
            "max_allowed_pct": self.max_drawdown_pct,
            "severity": severity,
            "recovery_needed_pct": round(current_dd_pct / (1 - current_dd_pct / 100), 4) if current_dd_pct < 100 else 999.0,
        }

    def _calculate_daily_drawdown(self) -> Dict[str, Any]:
        """Calculate intraday drawdown from daily open."""
        if self.daily_start_balance <= 0:
            return {"current_pct": 0.0, "limit_pct": self.daily_drawdown_limit}

        daily_dd_abs = self.daily_start_balance - self.current_balance
        daily_dd_pct = (daily_dd_abs / self.daily_start_balance) * 100

        return {
            "current_pct": round(max(daily_dd_pct, 0.0), 4),
            "current_abs": round(max(daily_dd_abs, 0.0), 2),
            "limit_pct": self.daily_drawdown_limit,
            "limit_breached": daily_dd_pct >= self.daily_drawdown_limit,
        }

    # ------------------------------------------------------------------
    # Trade History
    # ------------------------------------------------------------------

    def _update_trade_history(self, trade_results: List[Dict]) -> None:
        """Update internal trade history."""
        for trade in trade_results:
            self.trade_history.append({
                "pnl": float(trade.get("pnl", 0)),
                "timestamp": trade.get("timestamp", datetime.utcnow().isoformat()),
                "win": float(trade.get("pnl", 0)) > 0,
            })

        # Keep last 100 trades
        if len(self.trade_history) > 100:
            self.trade_history = self.trade_history[-100:]

    def _count_consecutive_losses(self) -> int:
        """Count consecutive losing trades from most recent."""
        count = 0
        for trade in reversed(self.trade_history):
            if not trade.get("win", True):
                count += 1
            else:
                break
        self.consecutive_losses = count
        return count

    def _calculate_win_rate(self, lookback: int = 20) -> float:
        """Calculate win rate over recent trades."""
        recent = self.trade_history[-lookback:]
        if not recent:
            return 0.5
        wins = sum(1 for t in recent if t.get("win", False))
        return wins / len(recent)

    # ------------------------------------------------------------------
    # Halt Conditions
    # ------------------------------------------------------------------

    def _check_halt_conditions(
        self,
        drawdown: Dict,
        daily_dd: Dict,
        consecutive: int,
    ) -> tuple:
        """Check if trading should be halted."""
        if drawdown["current_pct"] >= self.max_drawdown_pct:
            return True, f"MAX_DRAWDOWN_BREACHED ({drawdown['current_pct']:.2f}%)"

        if daily_dd.get("limit_breached"):
            return True, f"DAILY_DRAWDOWN_LIMIT ({daily_dd['current_pct']:.2f}%)"

        if consecutive >= self.consecutive_loss_limit:
            return True, f"CONSECUTIVE_LOSSES ({consecutive})"

        return False, ""

    # ------------------------------------------------------------------
    # Size Multiplier
    # ------------------------------------------------------------------

    def _calculate_multiplier(
        self,
        drawdown: Dict,
        consecutive: int,
        win_rate: float,
    ) -> float:
        """
        Calculate position size multiplier based on drawdown state.

        Recovery schedule:
        - No drawdown: 1.0x
        - Mild (0-25% of max): 0.75x
        - Moderate (25-50% of max): 0.5x
        - Severe (50-75% of max): 0.25x
        - Critical (75%+ of max): 0.0x (halt)
        """
        if self.trading_halted:
            return 0.0

        dd_pct = drawdown["current_pct"]
        max_dd = self.max_drawdown_pct

        # Base multiplier from drawdown level
        if dd_pct >= max_dd:
            base = 0.0
        elif dd_pct >= max_dd * 0.75:
            base = 0.25
        elif dd_pct >= max_dd * 0.5:
            base = 0.5
        elif dd_pct >= max_dd * 0.25:
            base = 0.75
        else:
            base = 1.0

        # Consecutive loss penalty
        if consecutive >= 3:
            base *= max(0.5, 1.0 - (consecutive - 2) * 0.1)

        # Win rate bonus (recovery confirmation)
        if win_rate >= self.min_win_rate_for_recovery and dd_pct < max_dd * 0.5:
            recovery_bonus = (win_rate - self.min_win_rate_for_recovery) * self.recovery_factor
            base = min(base + recovery_bonus, 1.0)

        # Speed adjustment
        speed_factors = {"conservative": 0.8, "moderate": 1.0, "aggressive": 1.2}
        speed = speed_factors.get(self.recovery_speed, 1.0)

        return max(0.0, min(base * speed, 1.0))

    def _determine_recovery_level(self, drawdown: Dict) -> str:
        """Determine current recovery level label."""
        dd_pct = drawdown["current_pct"]
        max_dd = self.max_drawdown_pct

        if dd_pct == 0:
            return "FULL_CAPACITY"
        elif dd_pct < max_dd * 0.25:
            return "LEVEL_4_75PCT"
        elif dd_pct < max_dd * 0.5:
            return "LEVEL_3_50PCT"
        elif dd_pct < max_dd * 0.75:
            return "LEVEL_2_25PCT"
        elif dd_pct < max_dd:
            return "LEVEL_1_MINIMAL"
        return "HALTED"

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset_daily(self, current_balance: float) -> None:
        """Reset daily tracking at start of new trading day."""
        self.daily_start_balance = current_balance
        logger.info(f"DrawdownRecovery: Daily reset at balance={current_balance:.2f}")

    def reset_all(self, starting_balance: float) -> None:
        """Full reset (new account or manual override)."""
        self.peak_balance = starting_balance
        self._peak_initialised = True
        self.current_balance = starting_balance
        self.daily_start_balance = starting_balance
        self.trade_history = []
        self.consecutive_losses = 0
        self.trading_halted = False
        self.halt_reason = ""
        logger.info(f"DrawdownRecovery: Full reset at balance={starting_balance:.2f}")


# Global instance
drawdown_recovery = DrawdownRecoveryManager()

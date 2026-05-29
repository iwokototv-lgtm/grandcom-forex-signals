"""
Strategy Router
Routes signals to appropriate strategies based on market regime and conditions
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Regime → Strategy mapping
REGIME_STRATEGY_MAP = {
    "TREND_UP": ["smc_ict", "breakout"],
    "TREND_DOWN": ["smc_ict", "breakout"],
    "RANGE": ["mean_reversion", "smc_ict"],
    "HIGH_VOL": ["smc_ict"],
    "LOW_VOL": ["mean_reversion"],
    "CHAOS": [],  # No trading in chaos
    "UNKNOWN": ["smc_ict"],
}

# Strategy priority (higher = preferred)
STRATEGY_PRIORITY = {
    "smc_ict": 3,
    "breakout": 2,
    "mean_reversion": 1,
}


class StrategyRouter:
    """
    Intelligent Strategy Router.

    Routes incoming market data to the appropriate trading strategy
    based on:
    - Current market regime (from RegimeDetector)
    - MTF alignment score
    - SMC/ICT signal quality
    - Economic calendar status
    - Portfolio risk state

    Produces a unified signal recommendation with full context.
    """

    def __init__(self):
        self.version = "3.0.0"
        self.regime_map = REGIME_STRATEGY_MAP
        self.priority = STRATEGY_PRIORITY

    # ------------------------------------------------------------------
    # Main Routing
    # ------------------------------------------------------------------

    def route(
        self,
        regime_analysis: Dict[str, Any],
        smc_analysis: Dict[str, Any],
        mtf_analysis: Dict[str, Any],
        mean_reversion_analysis: Dict[str, Any],
        pivot_analysis: Dict[str, Any],
        calendar_check: Dict[str, Any],
        portfolio_state: Dict[str, Any],
        symbol: str = "XAUUSD",
    ) -> Dict[str, Any]:
        """
        Route to best strategy and produce unified signal.

        Args:
            regime_analysis: Output from RegimeDetector
            smc_analysis: Output from SMCICTStrategy
            mtf_analysis: Output from MultiTimeframeConfirmation
            mean_reversion_analysis: Output from MeanReversionStrategy
            pivot_analysis: Output from PivotPointsAnalyzer
            calendar_check: Output from EconomicCalendar
            portfolio_state: Output from PortfolioManager
            symbol: Trading symbol

        Returns:
            Unified signal with strategy, direction, confidence, and levels
        """
        try:
            result: Dict[str, Any] = {
                "symbol": symbol,
                "timestamp": datetime.utcnow().isoformat(),
                "version": self.version,
                "valid": True,
            }

            # 1. Pre-flight checks
            preflight = self._preflight_checks(calendar_check, portfolio_state)
            result["preflight"] = preflight

            if not preflight["pass"]:
                result["signal"] = "NEUTRAL"
                result["reason"] = preflight["reason"]
                result["confidence"] = 0.0
                return result

            # 2. Determine active strategies for current regime
            regime_name = regime_analysis.get("regime_name", "UNKNOWN")
            active_strategies = self.regime_map.get(regime_name, ["smc_ict"])
            result["regime"] = regime_name
            result["active_strategies"] = active_strategies

            if not active_strategies:
                result["signal"] = "NEUTRAL"
                result["reason"] = f"NO_STRATEGIES_FOR_REGIME_{regime_name}"
                result["confidence"] = 0.0
                return result

            # 3. Collect signals from active strategies
            strategy_signals = self._collect_strategy_signals(
                active_strategies,
                smc_analysis,
                mean_reversion_analysis,
                mtf_analysis,
            )
            result["strategy_signals"] = strategy_signals

            # 4. Select best strategy
            best_strategy, best_signal = self._select_best_strategy(
                strategy_signals, active_strategies
            )
            result["selected_strategy"] = best_strategy

            # 5. MTF confirmation
            mtf_direction = mtf_analysis.get("dominant_direction", "NEUTRAL")
            mtf_score = mtf_analysis.get("alignment_score", 0)
            result["mtf_confirmation"] = {
                "direction": mtf_direction,
                "score": mtf_score,
                "confirmed": mtf_score >= 60,
            }

            # 6. Pivot zone context
            pivot_zone = pivot_analysis.get("zone", {}).get("name", "UNKNOWN")
            pivot_bias = pivot_analysis.get("bias", "NEUTRAL")
            result["pivot_context"] = {
                "zone": pivot_zone,
                "bias": pivot_bias,
            }

            # 7. Composite signal
            composite = self._composite_signal(
                best_signal,
                mtf_direction,
                mtf_score,
                pivot_bias,
                regime_analysis,
                smc_analysis,
            )
            result.update(composite)

            logger.info(
                f"StrategyRouter [{symbol}]: strategy={best_strategy} "
                f"signal={composite['signal']} confidence={composite['confidence']:.2f} "
                f"regime={regime_name}"
            )
            return result

        except Exception as exc:
            logger.error(f"Strategy routing error [{symbol}]: {exc}", exc_info=True)
            return {
                "symbol": symbol,
                "signal": "NEUTRAL",
                "confidence": 0.0,
                "error": str(exc),
                "valid": False,
            }

    # ------------------------------------------------------------------
    # Pre-flight Checks
    # ------------------------------------------------------------------

    def _preflight_checks(
        self,
        calendar_check: Dict[str, Any],
        portfolio_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run pre-flight safety checks before routing."""
        checks = []

        # Economic calendar
        if not calendar_check.get("safe_to_trade", True):
            checks.append(f"CALENDAR_BLOCKED: {calendar_check.get('reason', 'Unknown')}")

        # Portfolio capacity
        if portfolio_state.get("open_positions", 0) >= 5:
            checks.append("MAX_POSITIONS_REACHED")

        # Daily drawdown
        if portfolio_state.get("daily_pnl", 0) < -500:  # $500 daily loss limit
            checks.append("DAILY_LOSS_LIMIT")

        passed = len(checks) == 0
        return {
            "pass": passed,
            "reason": checks[0] if checks else "ALL_CLEAR",
            "checks_failed": checks,
        }

    # ------------------------------------------------------------------
    # Signal Collection
    # ------------------------------------------------------------------

    def _collect_strategy_signals(
        self,
        active_strategies: List[str],
        smc_analysis: Dict[str, Any],
        mean_reversion_analysis: Dict[str, Any],
        mtf_analysis: Dict[str, Any],
    ) -> Dict[str, Dict]:
        """Collect signals from all active strategies."""
        signals = {}

        if "smc_ict" in active_strategies:
            smc_bias = smc_analysis.get("smc_bias", "NEUTRAL")
            smc_score = smc_analysis.get("smc_score", 0)
            signals["smc_ict"] = {
                "signal": "BUY" if smc_bias == "BULLISH" else ("SELL" if smc_bias == "BEARISH" else "NEUTRAL"),
                "confidence": min(smc_score / 10, 1.0),
                "score": smc_score,
                "bias": smc_bias,
            }

        if "mean_reversion" in active_strategies:
            mr_composite = mean_reversion_analysis.get("composite", {})
            signals["mean_reversion"] = {
                "signal": mr_composite.get("signal", "NEUTRAL"),
                "confidence": mr_composite.get("confidence", 0.0),
                "squeeze": mr_composite.get("squeeze_active", False),
            }

        if "breakout" in active_strategies:
            # Breakout: use MTF direction as proxy
            mtf_dir = mtf_analysis.get("dominant_direction", "NEUTRAL")
            mtf_score = mtf_analysis.get("alignment_score", 0)
            signals["breakout"] = {
                "signal": "BUY" if mtf_dir == "BULLISH" else ("SELL" if mtf_dir == "BEARISH" else "NEUTRAL"),
                "confidence": mtf_score / 100,
                "mtf_score": mtf_score,
            }

        return signals

    # ------------------------------------------------------------------
    # Strategy Selection
    # ------------------------------------------------------------------

    def _select_best_strategy(
        self,
        strategy_signals: Dict[str, Dict],
        active_strategies: List[str],
    ) -> tuple:
        """Select the highest-priority strategy with a non-neutral signal."""
        # Sort by priority
        sorted_strategies = sorted(
            active_strategies,
            key=lambda s: self.priority.get(s, 0),
            reverse=True,
        )

        for strategy in sorted_strategies:
            signal_data = strategy_signals.get(strategy, {})
            if signal_data.get("signal") not in ("NEUTRAL", None):
                return strategy, signal_data

        # Fallback: return highest priority regardless
        best = sorted_strategies[0] if sorted_strategies else "smc_ict"
        return best, strategy_signals.get(best, {"signal": "NEUTRAL", "confidence": 0.0})

    # ------------------------------------------------------------------
    # Composite Signal
    # ------------------------------------------------------------------

    def _composite_signal(
        self,
        strategy_signal: Dict[str, Any],
        mtf_direction: str,
        mtf_score: float,
        pivot_bias: str,
        regime_analysis: Dict[str, Any],
        smc_analysis: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build composite signal from all inputs."""
        base_signal = strategy_signal.get("signal", "NEUTRAL")
        base_confidence = float(strategy_signal.get("confidence", 0.0))

        # Alignment bonuses
        alignment_bonus = 0.0

        # MTF alignment
        if mtf_direction != "NEUTRAL" and mtf_score >= 60:
            mtf_signal = "BUY" if mtf_direction == "BULLISH" else "SELL"
            if mtf_signal == base_signal:
                alignment_bonus += 0.15

        # Pivot bias alignment
        pivot_signal_map = {
            "STRONG_BULLISH": "BUY",
            "BULLISH": "BUY",
            "STRONG_BEARISH": "SELL",
            "BEARISH": "SELL",
        }
        pivot_signal = pivot_signal_map.get(pivot_bias, "NEUTRAL")
        if pivot_signal == base_signal:
            alignment_bonus += 0.05

        # Regime risk multiplier
        risk_mult = regime_analysis.get("risk_multiplier", 1.0)

        # Final confidence
        final_confidence = min((base_confidence + alignment_bonus) * risk_mult, 1.0)
        final_confidence_pct = round(final_confidence * 100, 1)

        # SMC quality gate
        smc_score = smc_analysis.get("smc_score", 0)
        quality = "EXCELLENT" if smc_score >= 8 else ("GOOD" if smc_score >= 6 else ("FAIR" if smc_score >= 4 else "POOR"))

        return {
            "signal": base_signal,
            "confidence": final_confidence_pct,
            "confidence_raw": round(final_confidence, 4),
            "alignment_bonus": round(alignment_bonus, 4),
            "risk_multiplier": round(risk_mult, 4),
            "signal_quality": quality,
            "smc_score": smc_score,
            "meets_threshold": final_confidence_pct >= 60 and base_signal != "NEUTRAL",
        }


# Global instance
strategy_router = StrategyRouter()

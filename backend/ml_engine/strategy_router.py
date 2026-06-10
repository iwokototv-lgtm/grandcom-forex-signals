"""
Strategy Router
Unanimous-agreement router for the 3-component core signal system.

Logic:
  - All 3 core components (Trend, S/R, MTF) must vote the same direction.
  - Minimum confidence threshold: 70% (up from legacy 62%).
  - Signal quality score penalises signals that barely meet the threshold.
  - Pre-flight checks: economic calendar + portfolio capacity.

The old averaging/priority approach is replaced by strict AND-gate logic.
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Minimum confidence to emit a tradeable signal
MIN_CONFIDENCE_THRESHOLD = 70.0


class StrategyRouter:
    """
    Unanimous-Agreement Strategy Router v3.1

    Replaces the old weighted-average approach with strict AND-gate logic:
      1. All 3 core components must vote the same direction (BUY or SELL).
      2. Composite confidence (geometric mean) must be >= 70%.
      3. Pre-flight checks must pass (calendar, portfolio capacity).

    Kept backward-compatible: the route() method still accepts the old
    keyword arguments so existing callers don't break, but it now delegates
    to the new unanimous-vote logic.
    """

    def __init__(self):
        self.version = "3.1.0"
        self.min_confidence = MIN_CONFIDENCE_THRESHOLD

    # ------------------------------------------------------------------
    # Main Routing (backward-compatible signature)
    # ------------------------------------------------------------------

    def route(
        self,
        # New 3-component inputs (preferred)
        component_votes: Optional[Dict[str, str]] = None,
        component_confidences: Optional[Dict[str, float]] = None,
        # Legacy inputs (accepted but not used for signal logic)
        regime_analysis: Optional[Dict[str, Any]] = None,
        smc_analysis: Optional[Dict[str, Any]] = None,
        mtf_analysis: Optional[Dict[str, Any]] = None,
        mean_reversion_analysis: Optional[Dict[str, Any]] = None,
        pivot_analysis: Optional[Dict[str, Any]] = None,
        calendar_check: Optional[Dict[str, Any]] = None,
        portfolio_state: Optional[Dict[str, Any]] = None,
        symbol: str = "XAUUSD",
    ) -> Dict[str, Any]:
        """
        Route to a signal using unanimous-agreement logic.

        When called from HybridPortfolioSystemV3 (new path), component_votes
        and component_confidences are provided directly.

        When called from legacy code, the old keyword arguments are accepted
        and a best-effort signal is derived from mtf_analysis + pivot_analysis.

        Returns:
            Dict with signal, confidence, meets_threshold, and diagnostics.
        """
        try:
            result: Dict[str, Any] = {
                "symbol": symbol,
                "timestamp": datetime.utcnow().isoformat(),
                "version": self.version,
                "valid": True,
                "router_mode": "unanimous_agreement",
            }

            # ── Pre-flight checks ─────────────────────────────────────
            cal = calendar_check or {}
            port = portfolio_state or {}
            preflight = self._preflight_checks(cal, port)
            result["preflight"] = preflight

            if not preflight["pass"]:
                result.update({
                    "signal": "NEUTRAL",
                    "confidence": 0.0,
                    "meets_threshold": False,
                    "reason": preflight["reason"],
                    "selected_strategy": "BLOCKED",
                })
                return result

            # ── New path: component votes provided directly ───────────
            if component_votes is not None:
                return self._route_from_votes(
                    component_votes,
                    component_confidences or {},
                    result,
                    symbol,
                )

            # ── Legacy path: derive votes from old analysis dicts ─────
            return self._route_legacy(
                mtf_analysis or {},
                pivot_analysis or {},
                smc_analysis or {},
                regime_analysis or {},
                result,
                symbol,
            )

        except Exception as exc:
            logger.error(f"Strategy routing error [{symbol}]: {exc}", exc_info=True)
            return {
                "symbol": symbol,
                "signal": "NEUTRAL",
                "confidence": 0.0,
                "meets_threshold": False,
                "error": str(exc),
                "valid": False,
            }

    # ------------------------------------------------------------------
    # New unanimous-vote path
    # ------------------------------------------------------------------

    def _route_from_votes(
        self,
        votes: Dict[str, str],
        confidences: Dict[str, float],
        result: Dict[str, Any],
        symbol: str,
    ) -> Dict[str, Any]:
        """Route using explicit component votes (new 3-component system)."""
        result["component_votes"] = votes

        vote_values = list(votes.values())
        all_buy = all(v == "BUY" for v in vote_values)
        all_sell = all(v == "SELL" for v in vote_values)
        unanimous = all_buy or all_sell

        if not unanimous:
            disagreeing = [k for k, v in votes.items() if v != vote_values[0]]
            result.update({
                "signal": "NEUTRAL",
                "confidence": 0.0,
                "meets_threshold": False,
                "selected_strategy": "NO_CONSENSUS",
                "rejection_reason": f"SPLIT_VOTE: {votes}",
                "disagreeing_components": disagreeing,
            })
            logger.info(
                f"StrategyRouter [{symbol}]: NEUTRAL — split vote {votes}"
            )
            return result

        signal = "BUY" if all_buy else "SELL"

        # Composite confidence: geometric mean of all component confidences
        conf_values = [confidences.get(k, 0.0) for k in votes]
        if all(c > 0 for c in conf_values):
            composite = (
                float(np.prod(conf_values)) ** (1.0 / len(conf_values))
            ) * 100
        else:
            composite = 0.0
        composite = round(composite, 1)

        meets = composite >= self.min_confidence
        quality = self._quality_label(composite)

        result.update({
            "signal": signal if meets else "NEUTRAL",
            "confidence": composite,
            "meets_threshold": meets,
            "selected_strategy": "unanimous_3_component",
            "signal_quality": quality,
            "min_confidence_threshold": self.min_confidence,
        })

        if not meets:
            result["rejection_reason"] = (
                f"BELOW_THRESHOLD: {composite:.1f}% < {self.min_confidence}%"
            )

        logger.info(
            f"StrategyRouter [{symbol}]: {signal} conf={composite:.1f}% "
            f"quality={quality} unanimous={unanimous} meets={meets}"
        )
        return result

    # ------------------------------------------------------------------
    # Legacy compatibility path
    # ------------------------------------------------------------------

    def _route_legacy(
        self,
        mtf_analysis: Dict[str, Any],
        pivot_analysis: Dict[str, Any],
        smc_analysis: Dict[str, Any],
        regime_analysis: Dict[str, Any],
        result: Dict[str, Any],
        symbol: str,
    ) -> Dict[str, Any]:
        """
        Derive a signal from legacy analysis dicts.

        Applies the same unanimous-agreement principle: MTF direction,
        pivot bias, and SMC bias must all agree.  Confidence threshold
        is still 70%.
        """
        mtf_dir = mtf_analysis.get("dominant_direction", "NEUTRAL")
        mtf_score = float(mtf_analysis.get("alignment_score", 0.0))
        pivot_bias = pivot_analysis.get("bias", "NEUTRAL")
        smc_bias = smc_analysis.get("smc_bias", "NEUTRAL")
        smc_score = float(smc_analysis.get("smc_score", 0))

        # Map to BUY/SELL/NEUTRAL
        def _to_signal(val: str) -> str:
            if val in ("BULLISH", "STRONG_BULLISH"):
                return "BUY"
            if val in ("BEARISH", "STRONG_BEARISH"):
                return "SELL"
            return "NEUTRAL"

        mtf_signal = "BUY" if mtf_dir == "BULLISH" else ("SELL" if mtf_dir == "BEARISH" else "NEUTRAL")
        pivot_signal = _to_signal(pivot_bias)
        smc_signal = "BUY" if smc_bias == "BULLISH" else ("SELL" if smc_bias == "BEARISH" else "NEUTRAL")

        votes = {
            "mtf": mtf_signal,
            "pivot": pivot_signal,
            "smc": smc_signal,
        }
        result["component_votes"] = votes

        all_buy = all(v == "BUY" for v in votes.values())
        all_sell = all(v == "SELL" for v in votes.values())
        unanimous = all_buy or all_sell

        if not unanimous:
            result.update({
                "signal": "NEUTRAL",
                "confidence": 0.0,
                "meets_threshold": False,
                "selected_strategy": "NO_CONSENSUS",
                "rejection_reason": f"SPLIT_VOTE: {votes}",
            })
            return result

        signal = "BUY" if all_buy else "SELL"

        # Confidence: geometric mean of normalised scores
        mtf_conf = min(mtf_score / 100.0, 1.0)
        smc_conf = min(smc_score / 10.0, 1.0)
        pivot_conf = 0.75 if pivot_bias in ("STRONG_BULLISH", "STRONG_BEARISH") else 0.65
        composite = ((mtf_conf * smc_conf * pivot_conf) ** (1.0 / 3.0)) * 100
        composite = round(composite, 1)

        meets = composite >= self.min_confidence
        quality = self._quality_label(composite)

        result.update({
            "signal": signal if meets else "NEUTRAL",
            "confidence": composite,
            "meets_threshold": meets,
            "selected_strategy": "legacy_unanimous",
            "signal_quality": quality,
            "min_confidence_threshold": self.min_confidence,
            "regime": regime_analysis.get("regime_name", "UNKNOWN"),
        })

        if not meets:
            result["rejection_reason"] = (
                f"BELOW_THRESHOLD: {composite:.1f}% < {self.min_confidence}%"
            )

        logger.info(
            f"StrategyRouter [{symbol}] (legacy): {signal} conf={composite:.1f}% "
            f"quality={quality} meets={meets}"
        )
        return result

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

        if not calendar_check.get("safe_to_trade", True):
            checks.append(f"CALENDAR_BLOCKED: {calendar_check.get('reason', 'Unknown')}")

        if portfolio_state.get("open_positions", 0) >= 5:
            checks.append("MAX_POSITIONS_REACHED")

        if portfolio_state.get("daily_pnl", 0) < -500:
            checks.append("DAILY_LOSS_LIMIT")

        passed = len(checks) == 0
        return {
            "pass": passed,
            "reason": checks[0] if checks else "ALL_CLEAR",
            "checks_failed": checks,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _quality_label(self, confidence_pct: float) -> str:
        """Return a human-readable quality label for a confidence score."""
        margin = confidence_pct - self.min_confidence
        if margin >= 10:
            return "EXCELLENT"
        elif margin >= 5:
            return "GOOD"
        elif margin >= 0:
            return "FAIR"
        else:
            return "BELOW_THRESHOLD"


# Global instance
strategy_router = StrategyRouter()

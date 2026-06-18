"""
Signal Filters (v3.4)
======================
Comprehensive pass/fail checks applied after signal generation.

ALL six filters must pass for a signal to be sent:
  1. Confidence ≥ 85%
  2. Risk:Reward ≥ 1:2
  3. Spread < maximum allowed
  4. No high-impact news within 30 minutes
  5. No duplicate signal on same 4H candle
  6. Higher timeframe trend agrees

Usage::

    from ml_engine.signal_filters import SignalFilters

    filters = SignalFilters()
    result = await filters.apply(
        signal="BUY",
        confidence=87.5,
        risk_reward=2.3,
        spread=0.5,
        max_spread=2.0,
        has_high_impact_news=False,
        is_duplicate_on_candle=False,
        higher_tf_trend_agrees=True,
    )
    if result["all_pass"]:
        # send signal
        ...
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Minimum confidence required to pass Filter 1
MIN_CONFIDENCE_PCT: float = 85.0

# Minimum risk:reward ratio required to pass Filter 2
MIN_RISK_REWARD: float = 2.0


class SignalFilters:
    """
    Comprehensive signal quality gate.

    All six filters must pass for ``all_pass`` to be True.
    If any filter fails the returned ``signal`` is downgraded to ``"NEUTRAL"``.
    """

    def __init__(
        self,
        min_confidence: float = MIN_CONFIDENCE_PCT,
        min_risk_reward: float = MIN_RISK_REWARD,
    ) -> None:
        self.min_confidence = min_confidence
        self.min_risk_reward = min_risk_reward

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def apply(
        self,
        signal: str,
        confidence: float,
        risk_reward: float,
        spread: float,
        max_spread: float,
        has_high_impact_news: bool,
        is_duplicate_on_candle: bool,
        higher_tf_trend_agrees: bool,
    ) -> Dict[str, Any]:
        """
        Apply all six signal filters.

        Args:
            signal:                 Proposed signal direction ("BUY" / "SELL").
            confidence:             Signal confidence as a percentage (0–100).
            risk_reward:            Calculated risk:reward ratio (e.g. 2.3 means 1:2.3).
            spread:                 Current spread in pips / price units.
            max_spread:             Maximum allowed spread for this pair.
            has_high_impact_news:   True if a high-impact news event is within 30 min.
            is_duplicate_on_candle: True if the same signal was already sent on this 4H candle.
            higher_tf_trend_agrees: True if the daily / weekly trend aligns with the signal.

        Returns:
            {
                "all_pass":       bool,
                "filters_passed": int   (0–6),
                "filters_failed": list[str],
                "signal":         str   (original signal or "NEUTRAL" if any filter fails),
            }
        """
        filters_passed = 0
        filters_failed: List[str] = []

        # ── Filter 1: Confidence ─────────────────────────────────────────────
        if confidence >= self.min_confidence:
            filters_passed += 1
            logger.debug(f"[SignalFilters] ✅ F1 PASSED: confidence={confidence:.1f}%")
        else:
            msg = f"Confidence {confidence:.1f}% < {self.min_confidence:.0f}%"
            filters_failed.append(msg)
            logger.debug(f"[SignalFilters] ❌ F1 FAILED: {msg}")

        # ── Filter 2: Risk:Reward ────────────────────────────────────────────
        if risk_reward >= self.min_risk_reward:
            filters_passed += 1
            logger.debug(f"[SignalFilters] ✅ F2 PASSED: R:R={risk_reward:.2f}")
        else:
            msg = f"R:R {risk_reward:.2f} < 1:{self.min_risk_reward:.0f}"
            filters_failed.append(msg)
            logger.debug(f"[SignalFilters] ❌ F2 FAILED: {msg}")

        # ── Filter 3: Spread ─────────────────────────────────────────────────
        if spread < max_spread:
            filters_passed += 1
            logger.debug(f"[SignalFilters] ✅ F3 PASSED: spread={spread:.1f} < max={max_spread:.1f}")
        else:
            msg = f"Spread {spread:.1f} > max {max_spread:.1f}"
            filters_failed.append(msg)
            logger.debug(f"[SignalFilters] ❌ F3 FAILED: {msg}")

        # ── Filter 4: High-Impact News ───────────────────────────────────────
        if not has_high_impact_news:
            filters_passed += 1
            logger.debug("[SignalFilters] ✅ F4 PASSED: no high-impact news within 30 min")
        else:
            msg = "High-impact news within 30 min"
            filters_failed.append(msg)
            logger.debug(f"[SignalFilters] ❌ F4 FAILED: {msg}")

        # ── Filter 5: Duplicate Signal ───────────────────────────────────────
        if not is_duplicate_on_candle:
            filters_passed += 1
            logger.debug("[SignalFilters] ✅ F5 PASSED: no duplicate on same 4H candle")
        else:
            msg = "Duplicate signal on same 4H candle"
            filters_failed.append(msg)
            logger.debug(f"[SignalFilters] ❌ F5 FAILED: {msg}")

        # ── Filter 6: Higher TF Trend ────────────────────────────────────────
        if higher_tf_trend_agrees:
            filters_passed += 1
            logger.debug("[SignalFilters] ✅ F6 PASSED: higher TF trend agrees")
        else:
            msg = "Higher TF trend disagrees"
            filters_failed.append(msg)
            logger.debug(f"[SignalFilters] ❌ F6 FAILED: {msg}")

        # ── Result ───────────────────────────────────────────────────────────
        all_pass = filters_passed == 6

        logger.info(
            f"[SignalFilters] {filters_passed}/6 passed "
            f"({'ALL PASS' if all_pass else 'FAILED: ' + ', '.join(filters_failed)})"
        )

        return {
            "all_pass": all_pass,
            "filters_passed": filters_passed,
            "filters_failed": filters_failed,
            "signal": signal if all_pass else "NEUTRAL",
        }

    # ------------------------------------------------------------------
    # Convenience: synchronous wrapper
    # ------------------------------------------------------------------

    def apply_sync(
        self,
        signal: str,
        confidence: float,
        risk_reward: float,
        spread: float,
        max_spread: float,
        has_high_impact_news: bool,
        is_duplicate_on_candle: bool,
        higher_tf_trend_agrees: bool,
    ) -> Dict[str, Any]:
        """
        Synchronous version of :meth:`apply` for use in non-async contexts.

        Internally runs the same logic without the ``await`` overhead.
        """
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Already inside an event loop — create a new one in a thread
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        asyncio.run,
                        self.apply(
                            signal=signal,
                            confidence=confidence,
                            risk_reward=risk_reward,
                            spread=spread,
                            max_spread=max_spread,
                            has_high_impact_news=has_high_impact_news,
                            is_duplicate_on_candle=is_duplicate_on_candle,
                            higher_tf_trend_agrees=higher_tf_trend_agrees,
                        ),
                    )
                    return future.result()
            else:
                return loop.run_until_complete(
                    self.apply(
                        signal=signal,
                        confidence=confidence,
                        risk_reward=risk_reward,
                        spread=spread,
                        max_spread=max_spread,
                        has_high_impact_news=has_high_impact_news,
                        is_duplicate_on_candle=is_duplicate_on_candle,
                        higher_tf_trend_agrees=higher_tf_trend_agrees,
                    )
                )
        except Exception as exc:
            logger.error(f"SignalFilters.apply_sync error: {exc}")
            return {
                "all_pass": False,
                "filters_passed": 0,
                "filters_failed": [f"EXCEPTION: {exc}"],
                "signal": "NEUTRAL",
            }


# Module-level singleton
signal_filters = SignalFilters()

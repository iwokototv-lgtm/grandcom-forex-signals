"""
Signal generation engine - hybrid portfolio system.
"""
import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MIN_CONFIDENCE = 60  # Default; callers may override via config


class SignalEngine:
    """Generates and validates trading signals using the hybrid portfolio system."""

    def __init__(self, hybrid_system, validator):
        self.hybrid_system = hybrid_system
        self.validator = validator

    async def generate(
        self,
        pair: str,
        indicators: Dict[str, Any],
        hybrid_ctx: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Generate a signal from the hybrid system context.

        Returns:
            Signal dict with type, confidence, entry, analysis, hybrid_ctx,
            indicators, and generated_at — or None if generation fails.
        """
        try:
            signal_type = str(hybrid_ctx.get("hybrid_signal", "NEUTRAL")).upper()
            confidence = float(hybrid_ctx.get("hybrid_confidence", 0.0))
            entry = float(hybrid_ctx.get("entry", 0) or indicators["price"])
            if entry <= 0:
                entry = indicators["price"]

            analysis = hybrid_ctx.get("analysis", "")
            if not analysis:
                analysis = f"Hybrid signal: {signal_type} (confidence={confidence}%)"

            logger.info(
                f"[{pair}] ✅ STAGE 1 - SIGNAL GENERATED: {signal_type} "
                f"(confidence={confidence}%, entry={entry})"
            )

            return {
                "pair": pair,
                "signal_type": signal_type,
                "confidence": confidence,
                "entry": entry,
                "analysis": analysis,
                "hybrid_ctx": hybrid_ctx,
                "indicators": indicators,
                "generated_at": datetime.now(timezone.utc),
            }
        except Exception as exc:
            logger.error(f"[{pair}] ❌ STAGE 1 - SIGNAL GENERATION FAILED: {exc}")
            return None

    async def validate(
        self,
        pair: str,
        signal: Dict[str, Any],
        tps: list,
        sl: float,
        min_confidence: int = MIN_CONFIDENCE,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Validate a signal through pre-checks and the full validator pipeline.

        Returns:
            (is_valid, validation_result)
        """
        try:
            signal_type = signal["signal_type"]
            confidence = signal["confidence"]
            entry = signal["entry"]

            # Pre-validation: NEUTRAL or unknown signal type
            if signal_type == "NEUTRAL" or signal_type not in ("BUY", "SELL"):
                logger.info(
                    f"[{pair}] ❌ STAGE 2 - VALIDATION FAILED: "
                    f"Hybrid returned {signal_type}"
                )
                return False, None

            # Pre-validation: confidence below minimum
            if confidence < min_confidence:
                logger.warning(
                    f"[{pair}] ❌ STAGE 2 - VALIDATION FAILED: "
                    f"Confidence {confidence}% < {min_confidence}%"
                )
                return False, None

            # Geometry validation
            if signal_type == "BUY" and (tps[0] <= entry or sl >= entry):
                logger.warning(
                    f"[{pair}] ❌ STAGE 2 - VALIDATION FAILED: "
                    f"BUY geometry invalid (TP1={tps[0]} <= entry={entry} or SL={sl} >= entry)"
                )
                return False, None

            if signal_type == "SELL" and (tps[0] >= entry or sl <= entry):
                logger.warning(
                    f"[{pair}] ❌ STAGE 2 - VALIDATION FAILED: "
                    f"SELL geometry invalid (TP1={tps[0]} >= entry={entry} or SL={sl} <= entry)"
                )
                return False, None

            # Full validation pipeline
            validation_result = await self.validator.validate({
                "pair": pair,
                "signal": signal_type,
                "confidence": confidence,
                "entry": entry,
                "tp_levels": tps,
                "sl": sl,
                "analysis": signal["analysis"],
            })

            if not validation_result["valid"]:
                logger.warning(
                    f"[{pair}] ❌ STAGE 2 - VALIDATION FAILED: "
                    f"{validation_result['reason']} "
                    f"(checks_failed={validation_result['checks_failed']})"
                )
                return False, validation_result

            logger.info(
                f"[{pair}] ✅ STAGE 2 - VALIDATION PASSED: {signal_type} signal "
                f"(confidence={confidence}%, entry={entry}, "
                f"checks={len(validation_result['checks_passed'])})"
            )
            return True, validation_result

        except Exception as exc:
            logger.error(f"[{pair}] ❌ STAGE 2 - VALIDATION ERROR: {exc}")
            return False, None

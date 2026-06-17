"""
Signal Validation Pipeline
Validates signals before sending to Telegram.
Logs all transformations and rejections with clear reasons.
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class SignalValidator:
    """Validate signals and log all transformations."""

    def __init__(self):
        self.validation_checks = []

    async def validate(self, signal_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate signal and return result with reason.

        Returns:
            {
                "valid": bool,
                "signal": "BUY" | "SELL" | "NEUTRAL",
                "reason": "explanation of validation result",
                "checks_passed": [list of passed checks],
                "checks_failed": [list of failed checks],
                "timestamp": ISO-8601 string,
            }
        """
        result = {
            "valid": True,
            "signal": signal_data.get("signal", "NEUTRAL"),
            "reason": "Signal passed all validation checks",
            "checks_passed": [],
            "checks_failed": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        pair = signal_data.get("pair", "UNKNOWN")
        original_signal = signal_data.get("signal", "NEUTRAL")

        # Check 1: Signal type is valid
        if signal_data.get("signal") not in ("BUY", "SELL", "NEUTRAL"):
            result["checks_failed"].append("Invalid signal type")
            result["valid"] = False
            result["signal"] = "NEUTRAL"
            result["reason"] = f"Invalid signal type: {signal_data.get('signal')}"
            logger.error(f"[{pair}] VALIDATION FAILED: {result['reason']}")
            return result
        result["checks_passed"].append("Signal type valid")

        # Check 2: Confidence meets minimum threshold
        confidence = signal_data.get("confidence", 0.0)
        if confidence < 60.0:
            result["checks_failed"].append(f"Confidence too low ({confidence}% < 60%)")
            result["valid"] = False
            result["signal"] = "NEUTRAL"
            result["reason"] = f"Confidence {confidence}% below 60% minimum"
            logger.warning(f"[{pair}] VALIDATION FAILED: {result['reason']}")
            return result
        result["checks_passed"].append(f"Confidence sufficient ({confidence}%)")

        # Check 3: Entry price is reasonable
        entry = signal_data.get("entry", 0.0)
        if entry <= 0:
            result["checks_failed"].append("Invalid entry price")
            result["valid"] = False
            result["signal"] = "NEUTRAL"
            result["reason"] = f"Invalid entry price: {entry}"
            logger.error(f"[{pair}] VALIDATION FAILED: {result['reason']}")
            return result
        result["checks_passed"].append(f"Entry price valid ({entry})")

        # Check 4: TP levels are reasonable
        tps = signal_data.get("tp_levels", [])
        if not tps or len(tps) < 3:
            result["checks_failed"].append("Missing TP levels")
            result["valid"] = False
            result["signal"] = "NEUTRAL"
            result["reason"] = "Missing or incomplete TP levels"
            logger.error(f"[{pair}] VALIDATION FAILED: {result['reason']}")
            return result
        result["checks_passed"].append(f"TP levels valid ({len(tps)} levels)")

        # Check 5: SL is reasonable
        sl = signal_data.get("sl", 0.0)
        if sl <= 0:
            result["checks_failed"].append("Invalid SL price")
            result["valid"] = False
            result["signal"] = "NEUTRAL"
            result["reason"] = f"Invalid SL price: {sl}"
            logger.error(f"[{pair}] VALIDATION FAILED: {result['reason']}")
            return result
        result["checks_passed"].append(f"SL valid ({sl})")

        # All checks passed
        logger.info(
            f"[{pair}] ✅ VALIDATION PASSED: {original_signal} signal "
            f"(confidence={confidence}%, entry={entry}, checks={len(result['checks_passed'])})"
        )
        return result


# Module-level singleton
signal_validator = SignalValidator()

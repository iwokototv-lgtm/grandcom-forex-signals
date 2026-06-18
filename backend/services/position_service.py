"""
Position management service.
"""
import logging
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


class PositionService:
    """Manages position registration and tracking."""

    def __init__(self, position_manager, risk_manager, drawdown_recovery):
        self.position_manager = position_manager
        self.risk_manager = risk_manager
        self.drawdown_recovery = drawdown_recovery

    async def register(
        self,
        pair: str,
        signal_type: str,
        entry: float,
        tps: list,
        sl: float,
        confidence: float,
        analysis: str,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Register a new position after drawdown recovery assessment.

        Returns:
            (success, position_result) where position_result contains
            position_id on success or a reason string on failure.
        """
        try:
            # Check drawdown recovery — may reduce size or halt trading
            dd_assessment = self.drawdown_recovery.assess(
                current_balance=self.risk_manager.current_equity
            )

            if dd_assessment.get("trading_halted"):
                halt_reason = dd_assessment.get("halt_reason", "DRAWDOWN_HALT")
                logger.warning(f"[{pair}] DrawdownRecovery halt: {halt_reason}")
                return False, {"reason": halt_reason}

            size_multiplier = dd_assessment.get("size_multiplier", 1.0)
            position_size = round(1.0 * size_multiplier, 4)

            logger.info(
                f"[{pair}] ✅ STAGE 5 - POSITION REGISTERED: "
                f"attempting {signal_type} {entry} (size={position_size})"
            )

            pos_result = await self.position_manager.add_position(
                pair=pair,
                entry=entry,
                tp_levels=tps,
                sl=sl,
                size=position_size,
                confidence=confidence,
                signal_type=signal_type,
                analysis=analysis,
            )

            if pos_result.get("allowed", True):
                logger.info(
                    f"[{pair}] ✅ STAGE 5 - POSITION REGISTERED: "
                    f"id={pos_result.get('position_id')}"
                )
                return True, pos_result
            else:
                block_reason = pos_result.get("reason", "Unknown reason")
                logger.warning(
                    f"[{pair}] ⚠️ STAGE 5 - POSITION BLOCKED: {block_reason} "
                    f"(signal already sent to Telegram)"
                )
                return False, pos_result

        except Exception as exc:
            logger.error(f"[{pair}] ❌ STAGE 5 - POSITION REGISTRATION ERROR: {exc}")
            return False, None

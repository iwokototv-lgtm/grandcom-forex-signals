"""
Notification service - logs all signal events to MongoDB.
"""
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class NotificationService:
    """Logs signal events to MongoDB for audit trail."""

    def __init__(self, db, retry_func):
        self.db = db
        self.retry_func = retry_func

    async def log_event(
        self,
        pair: str,
        event_type: str,
        signal: str,
        confidence: float,
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Log a signal event to MongoDB.

        Returns:
            True if logged successfully, False otherwise.
        """
        if self.db is None:
            return False

        try:
            async def _insert():
                event = {
                    "timestamp": datetime.now(timezone.utc),
                    "pair": pair,
                    "event_type": event_type,
                    "signal": signal,
                    "confidence": confidence,
                    "reason": reason,
                    "metadata": metadata or {},
                }
                return await self.db.signal_events.insert_one(event)

            result = await self.retry_func(
                f"log_event[{event_type}]",
                _insert,
            )

            if result:
                logger.debug(f"[{pair}] Signal event logged: {event_type}")
                return True
            else:
                logger.warning(f"[{pair}] Failed to log signal event: {event_type}")
                return False

        except Exception as exc:
            logger.error(f"[{pair}] ❌ NOTIFICATION SERVICE ERROR: {exc}")
            return False

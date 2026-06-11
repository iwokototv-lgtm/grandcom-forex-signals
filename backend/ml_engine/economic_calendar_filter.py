"""
Economic Calendar Filter
Thin wrapper around EconomicCalendar that adds:
- Explicit blackout logging
- Convenience async helpers matching the spec interface
- News blackout period tracking in MongoDB (economic_events collection)
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .economic_calendar import EconomicCalendar

logger = logging.getLogger(__name__)

BLACKOUT_MINUTES_BEFORE: int = 30
BLACKOUT_MINUTES_AFTER: int = 30


class EconomicCalendarFilter:
    """
    High-level economic calendar filter for the signal pipeline.

    Usage::

        filter = EconomicCalendarFilter(db=db)
        if not await filter.is_blackout_period("XAUUSD"):
            # safe to trade
    """

    def __init__(self, db=None):
        self._db = db
        self._calendar = EconomicCalendar(
            blackout_minutes_before=BLACKOUT_MINUTES_BEFORE,
            blackout_minutes_after=BLACKOUT_MINUTES_AFTER,
            high_impact_only=True,
        )

    def set_db(self, db) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Fetch calendar
    # ------------------------------------------------------------------

    async def fetch_calendar(self) -> List[Dict]:
        """
        Fetch and cache the economic calendar.
        Also persists events to MongoDB ``economic_events`` collection.
        """
        events = await self._calendar._get_events()

        if self._db is not None and events:
            try:
                # Upsert each event by (event title + datetime)
                for ev in events:
                    await self._db.economic_events.update_one(
                        {
                            "event": ev.get("event"),
                            "datetime": ev.get("datetime"),
                        },
                        {"$set": {**ev, "fetched_at": datetime.now(timezone.utc)}},
                        upsert=True,
                    )
            except Exception as exc:
                logger.warning(f"economic_events upsert failed: {exc}")

        return events

    # ------------------------------------------------------------------
    # Blackout check
    # ------------------------------------------------------------------

    async def is_blackout_period(
        self,
        symbol: str = "XAUUSD",
        check_time: Optional[datetime] = None,
    ) -> bool:
        """
        Return True if trading should be paused due to an upcoming or
        recent high-impact economic event.
        """
        result = await self._calendar.is_safe_to_trade(symbol, check_time)
        safe = result.get("safe_to_trade", True)

        if not safe:
            blocking = result.get("blocking_events", [])
            for ev in blocking:
                logger.warning(
                    f"[{symbol}] NEWS BLACKOUT — {ev.get('event')} "
                    f"in {ev.get('minutes_to_event', '?')} min"
                )
            # Log to MongoDB
            if self._db is not None:
                try:
                    await self._db.economic_events.insert_one({
                        "type": "BLACKOUT_TRIGGERED",
                        "symbol": symbol,
                        "blocking_events": blocking,
                        "timestamp": datetime.now(timezone.utc),
                    })
                except Exception:
                    pass

        return not safe

    async def get_blackout_status(
        self,
        symbol: str = "XAUUSD",
    ) -> Dict[str, Any]:
        """Full status dict from the underlying calendar check."""
        return await self._calendar.is_safe_to_trade(symbol)

    # ------------------------------------------------------------------
    # Next high-impact event
    # ------------------------------------------------------------------

    async def get_next_high_impact_event(
        self, symbol: str = "XAUUSD"
    ) -> Optional[Dict[str, Any]]:
        """Return the next high-impact event for the given symbol."""
        result = await self._calendar.is_safe_to_trade(symbol)
        return result.get("next_event")


# Global singleton — db injected at startup
economic_calendar_filter = EconomicCalendarFilter()

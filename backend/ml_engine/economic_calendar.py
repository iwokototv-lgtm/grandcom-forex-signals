"""
Economic Calendar Integration
High-impact event filtering and pre/post-event risk management
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    import aiohttp as _aiohttp
    _HAS_AIOHTTP = True
except ImportError:  # pragma: no cover
    _aiohttp = None  # type: ignore[assignment]
    _HAS_AIOHTTP = False

try:
    import pandas as _pd  # noqa: F401 — imported for type hints only
except ImportError:  # pragma: no cover
    pass

logger = logging.getLogger(__name__)

CALENDAR_URL = os.environ.get(
    "ECONOMIC_CALENDAR_URL",
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
)

HIGH_IMPACT_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "XAU", "CHF"}
HIGH_IMPACT_KEYWORDS = {
    "NFP", "Non-Farm", "CPI", "Inflation", "FOMC", "Fed", "Interest Rate",
    "GDP", "Unemployment", "Retail Sales", "PMI", "ISM", "PPI",
    "Jackson Hole", "ECB", "BOE", "BOJ", "SNB", "RBA",
}


class EconomicCalendar:
    """
    Economic Calendar Integration for Trade Filtering.

    Fetches upcoming high-impact economic events and provides:
    - Pre-event blackout windows (default: 30 min before)
    - Post-event volatility windows (default: 15 min after)
    - Impact scoring for each event
    - Currency-specific filtering
    - Gold-specific event sensitivity
    """

    def __init__(
        self,
        blackout_minutes_before: int = 30,
        blackout_minutes_after: int = 15,
        high_impact_only: bool = True,
        gold_sensitive_currencies: Optional[List[str]] = None,
    ):
        self.blackout_before = blackout_minutes_before
        self.blackout_after = blackout_minutes_after
        self.high_impact_only = high_impact_only
        self.gold_sensitive = gold_sensitive_currencies or ["USD", "EUR", "XAU"]
        self.version = "3.0.0"
        self._cache: Optional[List[Dict]] = None
        self._cache_time: Optional[datetime] = None
        self._cache_ttl_minutes = 60

    # ------------------------------------------------------------------
    # Main Check
    # ------------------------------------------------------------------

    async def is_safe_to_trade(
        self,
        symbol: str = "XAUUSD",
        check_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Check if it is safe to trade at the given time.

        Returns:
            Dict with safe_to_trade bool, reason, and upcoming events
        """
        try:
            now = check_time or datetime.now(timezone.utc)
            events = await self._get_events()

            upcoming = self._filter_relevant_events(events, symbol)
            blocking_events = self._find_blocking_events(upcoming, now)

            safe = len(blocking_events) == 0
            reason = "CLEAR" if safe else f"BLOCKED_BY_{blocking_events[0]['event'][:30]}"

            next_event = self._next_event(upcoming, now)

            return {
                "safe_to_trade": safe,
                "reason": reason,
                "blocking_events": blocking_events,
                "upcoming_events": upcoming[:5],
                "next_event": next_event,
                "check_time": now.isoformat(),
                "symbol": symbol,
                "version": self.version,
            }

        except Exception as exc:
            logger.error(f"Economic calendar check error: {exc}", exc_info=True)
            # Fail open — don't block trading on calendar errors
            return {
                "safe_to_trade": True,
                "reason": "CALENDAR_ERROR_FAIL_OPEN",
                "error": str(exc),
                "blocking_events": [],
            }

    def is_safe_to_trade_sync(
        self,
        events: List[Dict],
        symbol: str = "XAUUSD",
        check_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Synchronous version using pre-fetched events."""
        now = check_time or datetime.now(timezone.utc)
        upcoming = self._filter_relevant_events(events, symbol)
        blocking_events = self._find_blocking_events(upcoming, now)
        safe = len(blocking_events) == 0

        return {
            "safe_to_trade": safe,
            "reason": "CLEAR" if safe else f"BLOCKED_BY_{blocking_events[0]['event'][:30]}",
            "blocking_events": blocking_events,
            "upcoming_events": upcoming[:5],
        }

    # ------------------------------------------------------------------
    # Event Fetching
    # ------------------------------------------------------------------

    async def _get_events(self) -> List[Dict]:
        """Fetch events with caching."""
        now = datetime.now(timezone.utc)

        # Return cached if fresh
        if (
            self._cache is not None
            and self._cache_time is not None
            and (now - self._cache_time).total_seconds() < self._cache_ttl_minutes * 60
        ):
            return self._cache

        events = await self._fetch_events()
        self._cache = events
        self._cache_time = now
        return events

    async def _fetch_events(self) -> List[Dict]:
        """Fetch economic calendar from ForexFactory or configured URL."""
        if not _HAS_AIOHTTP:
            logger.warning("aiohttp not available — economic calendar fetch skipped.")
            return []
        try:
            async with _aiohttp.ClientSession() as session:
                async with session.get(
                    CALENDAR_URL,
                    timeout=_aiohttp.ClientTimeout(total=10),
                    headers={"User-Agent": "GoldSignalsBot/3.0"},
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"Calendar fetch returned {resp.status}")
                        return []
                    data = await resp.json(content_type=None)

            if not isinstance(data, list):
                return []

            events = []
            for item in data:
                try:
                    event = self._parse_event(item)
                    if event:
                        events.append(event)
                except Exception:
                    continue

            logger.info(f"Economic calendar: fetched {len(events)} events")
            return events

        except Exception as exc:
            logger.error(f"Calendar fetch error: {exc}")
            return []

    def _parse_event(self, item: Dict) -> Optional[Dict]:
        """Parse a raw calendar event."""
        try:
            # ForexFactory format
            date_str = item.get("date", "")
            time_str = item.get("time", "")
            currency = item.get("currency", "").upper()
            impact = item.get("impact", "").lower()
            title = item.get("title", item.get("event", ""))

            if not date_str or not title:
                return None

            # Parse datetime
            try:
                if time_str and time_str not in ("All Day", "Tentative", ""):
                    dt_str = f"{date_str} {time_str}"
                    dt = datetime.strptime(dt_str, "%m-%d-%Y %I:%M%p").replace(tzinfo=timezone.utc)
                else:
                    dt = datetime.strptime(date_str, "%m-%d-%Y").replace(tzinfo=timezone.utc)
            except ValueError:
                return None

            return {
                "event": title,
                "currency": currency,
                "impact": impact,
                "datetime": dt.isoformat(),
                "datetime_obj": dt,
                "is_high_impact": impact in ("high", "red"),
                "is_gold_sensitive": currency in self.gold_sensitive,
            }

        except Exception:
            return None

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _filter_relevant_events(
        self, events: List[Dict], symbol: str
    ) -> List[Dict]:
        """Filter events relevant to the given symbol."""
        relevant = []
        now = datetime.now(timezone.utc)

        for event in events:
            # Only future events (within 24h)
            dt = event.get("datetime_obj")
            if not dt:
                continue
            if dt < now - timedelta(hours=1):
                continue
            if dt > now + timedelta(hours=24):
                continue

            # Impact filter
            if self.high_impact_only and not event.get("is_high_impact"):
                continue

            # Currency relevance
            currency = event.get("currency", "")
            if symbol in ("XAUUSD", "XAUEUR"):
                if currency not in HIGH_IMPACT_CURRENCIES:
                    continue
            else:
                if currency not in HIGH_IMPACT_CURRENCIES:
                    continue

            relevant.append(event)

        # Sort by datetime
        relevant.sort(key=lambda x: x.get("datetime_obj", datetime.max.replace(tzinfo=timezone.utc)))
        return relevant

    def _find_blocking_events(
        self, events: List[Dict], now: datetime
    ) -> List[Dict]:
        """Find events that block trading at the given time."""
        blocking = []
        for event in events:
            dt = event.get("datetime_obj")
            if not dt:
                continue

            window_start = dt - timedelta(minutes=self.blackout_before)
            window_end = dt + timedelta(minutes=self.blackout_after)

            if window_start <= now <= window_end:
                blocking.append({
                    "event": event["event"],
                    "currency": event["currency"],
                    "datetime": event["datetime"],
                    "minutes_to_event": round((dt - now).total_seconds() / 60, 1),
                    "in_blackout": True,
                })

        return blocking

    def _next_event(
        self, events: List[Dict], now: datetime
    ) -> Optional[Dict]:
        """Find the next upcoming event."""
        future = [e for e in events if e.get("datetime_obj", now) > now]
        if not future:
            return None

        next_e = future[0]
        dt = next_e["datetime_obj"]
        return {
            "event": next_e["event"],
            "currency": next_e["currency"],
            "datetime": next_e["datetime"],
            "minutes_away": round((dt - now).total_seconds() / 60, 1),
        }

    # ------------------------------------------------------------------
    # Impact Scoring
    # ------------------------------------------------------------------

    def score_event_impact(self, event: Dict) -> int:
        """Score event impact for gold (0-10)."""
        score = 0
        title = event.get("event", "").upper()
        currency = event.get("currency", "")

        # Base impact
        if event.get("is_high_impact"):
            score += 5

        # Gold-sensitive currency
        if currency in ("USD", "XAU"):
            score += 3
        elif currency in ("EUR", "GBP"):
            score += 1

        # Keyword matching
        for kw in HIGH_IMPACT_KEYWORDS:
            if kw.upper() in title:
                score += 2
                break

        return min(score, 10)


# Global instance
economic_calendar = EconomicCalendar()

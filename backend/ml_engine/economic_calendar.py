"""
Economic Calendar Manager — v3.0
Event-based signal filtering to avoid trading around high-impact news.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# Gold-relevant currencies and event keywords
GOLD_RELEVANT_CURRENCIES: set[str] = {"USD", "EUR", "GBP", "XAU"}
HIGH_IMPACT_KEYWORDS: list[str] = [
    "NFP", "Non-Farm", "FOMC", "Fed", "Interest Rate", "CPI", "Inflation",
    "GDP", "Unemployment", "Payroll", "Powell", "ECB", "BOE", "Rate Decision",
    "Monetary Policy", "Jackson Hole", "Treasury",
]
MEDIUM_IMPACT_KEYWORDS: list[str] = [
    "PMI", "ISM", "Retail Sales", "PPI", "Trade Balance", "Consumer Confidence",
    "Housing", "Durable Goods", "ADP", "Jobless Claims",
]


class EconomicCalendarManager:
    """
    Fetches and caches economic calendar events.
    Provides blackout windows around high/medium impact events.

    Data source: ForexFactory-compatible JSON API (or fallback to static schedule).
    """

    def __init__(
        self,
        high_impact_blackout_minutes: int = 60,
        medium_impact_blackout_minutes: int = 30,
        cache_ttl_minutes: int = 60,
    ) -> None:
        self.high_impact_blackout = timedelta(minutes=high_impact_blackout_minutes)
        self.medium_impact_blackout = timedelta(minutes=medium_impact_blackout_minutes)
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)

        self._events_cache: list[dict] = []
        self._cache_fetched_at: datetime | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def is_safe_to_trade(
        self, symbol: str = "XAUUSD"
    ) -> dict[str, Any]:
        """
        Check whether it is safe to open a new trade right now.

        Returns:
            dict with safe (bool), reason, next_event, blackout_until.
        """
        try:
            events = await self._get_events()
            now = datetime.now(timezone.utc)

            upcoming = self._filter_relevant_events(events, symbol)

            for event in upcoming:
                event_time = event.get("datetime_utc")
                if not event_time:
                    continue

                impact = event.get("impact", "LOW").upper()
                blackout = (
                    self.high_impact_blackout
                    if impact == "HIGH"
                    else self.medium_impact_blackout
                    if impact == "MEDIUM"
                    else timedelta(minutes=0)
                )

                if blackout.total_seconds() == 0:
                    continue

                window_start = event_time - blackout
                window_end = event_time + blackout

                if window_start <= now <= window_end:
                    return {
                        "safe": False,
                        "reason": f"{impact} impact event: {event.get('title', 'Unknown')}",
                        "event": event.get("title"),
                        "event_time": event_time.isoformat(),
                        "blackout_until": window_end.isoformat(),
                        "currency": event.get("currency"),
                    }

            # Find next upcoming event
            future_events = [
                e for e in upcoming
                if e.get("datetime_utc") and e["datetime_utc"] > now
            ]
            next_event = None
            if future_events:
                next_event = min(future_events, key=lambda e: e["datetime_utc"])

            return {
                "safe": True,
                "reason": "No high/medium impact events in blackout window",
                "next_event": next_event.get("title") if next_event else None,
                "next_event_time": (
                    next_event["datetime_utc"].isoformat() if next_event else None
                ),
            }

        except Exception as exc:
            logger.error(f"[EconCalendar] is_safe_to_trade error: {exc}")
            # Fail open — don't block trading on calendar errors
            return {
                "safe": True,
                "reason": f"Calendar check failed (fail-open): {exc}",
                "error": str(exc),
            }

    async def get_upcoming_events(
        self, hours_ahead: int = 24, symbol: str = "XAUUSD"
    ) -> list[dict]:
        """Return upcoming events relevant to the symbol within hours_ahead."""
        try:
            events = await self._get_events()
            now = datetime.now(timezone.utc)
            cutoff = now + timedelta(hours=hours_ahead)
            relevant = self._filter_relevant_events(events, symbol)
            return [
                e for e in relevant
                if e.get("datetime_utc") and now <= e["datetime_utc"] <= cutoff
            ]
        except Exception as exc:
            logger.error(f"[EconCalendar] get_upcoming_events error: {exc}")
            return []

    # ------------------------------------------------------------------
    # Event Fetching & Caching
    # ------------------------------------------------------------------

    async def _get_events(self) -> list[dict]:
        """Return cached events or fetch fresh ones."""
        now = datetime.now(timezone.utc)
        if (
            self._cache_fetched_at
            and (now - self._cache_fetched_at) < self.cache_ttl
            and self._events_cache
        ):
            return self._events_cache

        events = await self._fetch_events()
        if events:
            self._events_cache = events
            self._cache_fetched_at = now
        elif not self._events_cache:
            self._events_cache = self._static_fallback_events()

        return self._events_cache

    async def _fetch_events(self) -> list[dict]:
        """
        Fetch economic calendar from ForexFactory JSON API.
        Returns empty list on failure (caller uses cache/fallback).
        """
        try:
            url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json(content_type=None)

            parsed: list[dict] = []
            for item in data:
                try:
                    dt_str = item.get("date", "")
                    if not dt_str:
                        continue
                    # ForexFactory format: "01-15-2025 8:30am"
                    dt = datetime.strptime(dt_str, "%m-%d-%Y %I:%M%p").replace(
                        tzinfo=timezone.utc
                    )
                    parsed.append(
                        {
                            "title": item.get("title", ""),
                            "currency": item.get("country", "").upper(),
                            "impact": item.get("impact", "Low").upper(),
                            "datetime_utc": dt,
                            "forecast": item.get("forecast", ""),
                            "previous": item.get("previous", ""),
                        }
                    )
                except Exception:
                    continue

            logger.info(f"[EconCalendar] Fetched {len(parsed)} events")
            return parsed

        except Exception as exc:
            logger.warning(f"[EconCalendar] Fetch failed: {exc}")
            return []

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _filter_relevant_events(
        self, events: list[dict], symbol: str
    ) -> list[dict]:
        """Filter events relevant to the given symbol."""
        relevant: list[dict] = []
        for event in events:
            currency = event.get("currency", "").upper()
            title = event.get("title", "")
            impact = event.get("impact", "LOW").upper()

            # Always include USD events for gold
            if currency in GOLD_RELEVANT_CURRENCIES:
                relevant.append(event)
                continue

            # Include high-impact events for any major currency
            if impact == "HIGH":
                relevant.append(event)

        return relevant

    # ------------------------------------------------------------------
    # Static Fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _static_fallback_events() -> list[dict]:
        """
        Minimal static schedule for known recurring high-impact events.
        Used when the live calendar API is unavailable.
        """
        now = datetime.now(timezone.utc)
        # First Friday of month = NFP (approximate)
        events: list[dict] = []

        # Add placeholder for next Friday 13:30 UTC (typical NFP time)
        days_until_friday = (4 - now.weekday()) % 7
        next_friday = now + timedelta(days=days_until_friday)
        nfp_time = next_friday.replace(hour=13, minute=30, second=0, microsecond=0)

        events.append(
            {
                "title": "Non-Farm Payrolls (estimated)",
                "currency": "USD",
                "impact": "HIGH",
                "datetime_utc": nfp_time,
                "forecast": "",
                "previous": "",
                "is_static": True,
            }
        )

        return events


# Module-level singleton
economic_calendar = EconomicCalendarManager()

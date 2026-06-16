"""
Candle Tracker
Tracks the last processed 4H candle timestamp per trading pair.

Prevents redundant signal generation by ensuring signals are only
emitted when a NEW 4H candle has closed since the last processed one.

Storage: MongoDB collection ``candle_tracking`` (with in-process cache).
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_LOG_PREFIX = "[CANDLE_TRACKER]"


class CandleTracker:
    """
    Track last processed 4H candle per pair.

    Usage::

        tracker = CandleTracker(db=motor_db)

        is_new = await tracker.is_new_candle(pair, current_candle_time)
        if is_new:
            await generate_signal(pair)
            await tracker.update_candle_time(pair, current_candle_time)
    """

    def __init__(self, db=None):
        self._db = db
        self._cache: Dict[str, Optional[datetime]] = {}  # {pair: last_candle_time}

    # ------------------------------------------------------------------
    # Dependency injection
    # ------------------------------------------------------------------

    def set_db(self, db) -> None:
        """Inject (or replace) the Motor database handle."""
        self._db = db

    # ------------------------------------------------------------------
    # Reset helpers
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """
        Reset all tracked candles (call on startup to clear stale state).

        Clears the in-process cache so every pair is treated as first-seen
        on the next ``is_new_candle`` call, guaranteeing a signal is
        generated immediately after a restart.
        """
        self._cache.clear()
        logger.info(f"{_LOG_PREFIX} State reset on startup")

    def reset_pair(self, pair: str) -> None:
        """
        Reset the tracked candle for a specific pair.

        Removes the pair from the in-process cache so the next
        ``is_new_candle`` call treats it as first-seen.
        """
        if pair in self._cache:
            del self._cache[pair]
            logger.info(f"{_LOG_PREFIX} [{pair}] State reset")

    # ------------------------------------------------------------------
    # State inspection
    # ------------------------------------------------------------------

    def get_state(self) -> dict:
        """Return a copy of the current in-process cache (for debugging)."""
        return dict(self._cache)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _ensure_aware(self, dt: datetime) -> datetime:
        """Convert naive datetime to UTC-aware."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    async def is_new_candle(self, pair: str, current_time: datetime) -> bool:
        """
        Return ``True`` if *current_time* is strictly newer than the last
        processed candle for *pair*.

        First-time calls (no record in cache or MongoDB) always return
        ``True`` so the very first scan always generates a signal.
        """
        last_time = await self.get_last_candle_time(pair)

        if last_time is None:
            logger.info(
                f"{_LOG_PREFIX} [{pair}] No previous candle record — "
                f"treating as NEW (first run)"
            )
            return True

        # Normalize both to UTC-aware for safe comparison
        current_time = self._ensure_aware(current_time)
        last_time = self._ensure_aware(last_time)

        is_new = current_time > last_time
        if not is_new:
            logger.info(
                f"{_LOG_PREFIX} [{pair}] Same 4H candle as last signal — skipping "
                f"(last={last_time}, current={current_time})"
            )
        return is_new

    async def get_last_candle_time(self, pair: str) -> Optional[datetime]:
        """
        Return the last processed candle time for *pair*, or ``None`` if
        no record exists yet.

        Checks the in-process cache first; falls back to MongoDB.
        """
        # 1. In-process cache
        if pair in self._cache:
            return self._cache[pair]

        # 2. MongoDB
        if self._db is not None:
            try:
                doc = await self._db.candle_tracking.find_one({"pair": pair})
                if doc:
                    last_time = doc.get("last_candle_time")
                    self._cache[pair] = last_time
                    logger.debug(
                        f"{_LOG_PREFIX} [{pair}] Loaded last candle time "
                        f"from MongoDB: {last_time}"
                    )
                    return last_time
            except Exception as exc:
                logger.warning(
                    f"{_LOG_PREFIX} [{pair}] candle_tracking query failed "
                    f"(returning None): {exc}"
                )

        return None

    async def update_candle_time(self, pair: str, candle_time: datetime) -> None:
        """
        Persist *candle_time* as the last processed candle for *pair*.

        Updates the in-process cache immediately and upserts the
        ``candle_tracking`` MongoDB document asynchronously.
        """
        # Always update cache so subsequent in-process checks are fast
        self._cache[pair] = candle_time

        if self._db is not None:
            try:
                await self._db.candle_tracking.update_one(
                    {"pair": pair},
                    {
                        "$set": {
                            "pair": pair,
                            "last_candle_time": candle_time,
                            "updated_at": datetime.now(timezone.utc),
                        }
                    },
                    upsert=True,
                )
                logger.debug(
                    f"{_LOG_PREFIX} [{pair}] candle_tracking updated → {candle_time}"
                )
            except Exception as exc:
                logger.error(
                    f"{_LOG_PREFIX} [{pair}] candle_tracking update failed: {exc}"
                )

    def clear_cache(self, pair: Optional[str] = None) -> None:
        """
        Evict one or all entries from the in-process cache.

        Useful in tests or after a manual reset.
        """
        if pair is not None:
            self._cache.pop(pair, None)
        else:
            self._cache.clear()


# ---------------------------------------------------------------------------
# Module-level singleton — imported by gold_server_v3
# ---------------------------------------------------------------------------
candle_tracker = CandleTracker()

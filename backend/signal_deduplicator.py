"""
Signal Deduplicator — V4.2
Prevents the same (candle_timestamp + symbol + direction) setup from firing
more than once per 4H candle window.

The rescanner writes fresh data only 6×/day (at :05 after each 4H close).
Between refreshes the server re-evaluates identical data ~120 times, which
would fire the same signal 120 times without this guard.

Strategy
--------
Primary  : MongoDB collection ``signal_dedupe_locks`` with a TTL index on
           ``expires_at``.  A unique index on ``_dedupe_key`` makes the
           insert atomic — a duplicate key error means the lock already
           exists and the signal should be skipped.

Fallback : In-memory dict protected by asyncio.Lock.  Not persistent across
           restarts, but prevents duplicates within a single process lifetime.

TTL
---
Default 4 hours (14 400 s) — matches the 4H candle window so locks expire
automatically when the next candle opens.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pymongo.errors import DuplicateKeyError

logger = logging.getLogger("signal_deduplicator")

# Collection name used for MongoDB-backed locks
DEDUPE_COLLECTION = "signal_dedupe_locks"

# Default TTL — 4 hours in seconds
DEFAULT_TTL_SECONDS = 14_400


class SignalDeduplicator:
    """
    Deduplication lock keyed on ``(candle_timestamp, symbol, direction)``.

    Usage::

        deduplicator = SignalDeduplicator(db)

        if await deduplicator.has_signalled(candle_ts, pair, direction):
            logger.info("Duplicate — skipping")
            return

        # … generate signal …

        await deduplicator.mark_signalled(candle_ts, pair, direction)

    The ``db`` argument is a Motor ``AsyncIOMotorDatabase`` instance (or
    ``None`` to fall back to the in-memory implementation).
    """

    def __init__(self, db: Any = None) -> None:
        self._db = db
        # In-memory fallback: key → expiry timestamp (UTC epoch seconds)
        self._memory_store: dict[str, float] = {}
        self._memory_lock = asyncio.Lock()
        self._mongo_ready = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def setup(self) -> None:
        """
        Ensure the MongoDB collection has the required indexes.

        Call once at startup (after the DB connection is established).
        Idempotent — safe to call multiple times.
        """
        if self._db is None:
            logger.warning(
                "SignalDeduplicator: MongoDB not available — "
                "falling back to in-memory deduplication"
            )
            return

        try:
            col = self._db[DEDUPE_COLLECTION]

            # Unique index on the composite key — makes insert atomic
            await col.create_index("_dedupe_key", unique=True, background=True)

            # TTL index — MongoDB auto-deletes documents after expires_at
            await col.create_index(
                "expires_at",
                expireAfterSeconds=0,   # expire AT the stored datetime
                background=True,
            )

            self._mongo_ready = True
            logger.info(
                f"✅ SignalDeduplicator: MongoDB indexes ensured on "
                f"'{DEDUPE_COLLECTION}'"
            )
        except Exception as exc:
            logger.error(
                f"❌ SignalDeduplicator: index setup failed — "
                f"falling back to in-memory: {exc}"
            )
            self._mongo_ready = False

    async def has_signalled(
        self,
        candle_ts: str,
        symbol: str,
        direction: str,
    ) -> bool:
        """
        Return ``True`` if a signal for this (candle_ts, symbol, direction)
        tuple has already been fired and the lock has not yet expired.
        """
        key = self._make_key(candle_ts, symbol, direction)

        if self._mongo_ready and self._db is not None:
            return await self._mongo_has(key)
        return await self._memory_has(key)

    async def mark_signalled(
        self,
        candle_ts: str,
        symbol: str,
        direction: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> bool:
        """
        Record that a signal has been fired for this tuple.

        Returns ``True`` if the lock was successfully created (i.e. this is
        the first signal for this setup), ``False`` if a lock already existed
        (race condition — the signal should be discarded by the caller).
        """
        key = self._make_key(candle_ts, symbol, direction)

        if self._mongo_ready and self._db is not None:
            return await self._mongo_mark(key, ttl_seconds)
        return await self._memory_mark(key, ttl_seconds)

    # ------------------------------------------------------------------
    # Key construction
    # ------------------------------------------------------------------

    @staticmethod
    def _make_key(candle_ts: str, symbol: str, direction: str) -> str:
        """
        Build a deterministic string key from the three deduplication fields.

        Example: ``"2024-01-15T04:00:00+00:00:XAUUSD:BUY"``
        """
        return f"{candle_ts}:{symbol.upper()}:{direction.upper()}"

    # ------------------------------------------------------------------
    # MongoDB backend
    # ------------------------------------------------------------------

    async def _mongo_has(self, key: str) -> bool:
        """Check whether a lock document exists in MongoDB."""
        try:
            doc = await self._db[DEDUPE_COLLECTION].find_one(
                {"_dedupe_key": key},
                {"_id": 1},
            )
            return doc is not None
        except Exception as exc:
            logger.warning(
                f"SignalDeduplicator._mongo_has failed — "
                f"treating as not-signalled: {exc}"
            )
            # Fail-open: if we can't check, allow the signal through
            return False

    async def _mongo_mark(self, key: str, ttl_seconds: int) -> bool:
        """
        Insert a lock document.  Returns True on success, False if the key
        already exists (duplicate key error → race condition caught).
        """
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl_seconds)

        doc = {
            "_dedupe_key": key,
            "created_at":  now,
            "expires_at":  expires_at,
            "ttl_seconds": ttl_seconds,
        }

        try:
            await self._db[DEDUPE_COLLECTION].insert_one(doc)
            logger.debug(f"SignalDeduplicator: lock created — key={key!r}")
            return True
        except DuplicateKeyError:
            logger.debug(
                f"SignalDeduplicator: lock already exists — key={key!r}"
            )
            return False
        except Exception as exc:
            # Any other error — log and fail-open (allow signal through)
            logger.warning(
                f"SignalDeduplicator._mongo_mark unexpected error — "
                f"allowing signal through: {exc}"
            )
            return True

    # ------------------------------------------------------------------
    # In-memory fallback backend
    # ------------------------------------------------------------------

    async def _memory_has(self, key: str) -> bool:
        """Check the in-memory store, pruning expired entries."""
        async with self._memory_lock:
            expiry = self._memory_store.get(key)
            if expiry is None:
                return False
            now_ts = datetime.now(timezone.utc).timestamp()
            if now_ts >= expiry:
                # Expired — remove and treat as not-signalled
                del self._memory_store[key]
                return False
            return True

    async def _memory_mark(self, key: str, ttl_seconds: int) -> bool:
        """Insert a lock into the in-memory store."""
        async with self._memory_lock:
            now_ts = datetime.now(timezone.utc).timestamp()
            existing_expiry = self._memory_store.get(key)
            if existing_expiry is not None and now_ts < existing_expiry:
                # Lock already exists and has not expired
                logger.debug(
                    f"SignalDeduplicator (memory): lock already exists — key={key!r}"
                )
                return False
            self._memory_store[key] = now_ts + ttl_seconds
            logger.debug(
                f"SignalDeduplicator (memory): lock created — key={key!r}"
            )
            return True

"""
Unit tests for CandleTracker.reset() — MongoDB clearing behaviour.

Covers:
  - reset() clears both in-memory cache and MongoDB candle_tracking collection
  - reset() handles MongoDB errors gracefully (non-fatal)
  - First is_new_candle() call after reset() always returns True
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_reset_clears_mongodb():
    """reset() should clear the MongoDB candle_tracking collection."""
    from ml_engine.candle_tracker import CandleTracker

    # Mock MongoDB
    mock_db = MagicMock()
    mock_collection = AsyncMock()
    mock_db.candle_tracking = mock_collection
    mock_collection.delete_many = AsyncMock(
        return_value=MagicMock(deleted_count=2)
    )

    tracker = CandleTracker(db=mock_db)
    tracker._cache["XAUUSD"] = datetime.now(timezone.utc)
    tracker._cache["XAUEUR"] = datetime.now(timezone.utc)

    await tracker.reset()

    # Verify both cache and MongoDB were cleared
    assert len(tracker._cache) == 0  # ✅ Cache cleared
    mock_collection.delete_many.assert_called_once_with({})  # ✅ MongoDB cleared


@pytest.mark.asyncio
async def test_reset_handles_mongodb_error():
    """reset() should handle MongoDB errors gracefully (non-fatal)."""
    from ml_engine.candle_tracker import CandleTracker

    mock_db = MagicMock()
    mock_collection = AsyncMock()
    mock_db.candle_tracking = mock_collection
    mock_collection.delete_many = AsyncMock(
        side_effect=Exception("MongoDB connection error")
    )

    tracker = CandleTracker(db=mock_db)
    tracker._cache["XAUUSD"] = datetime.now(timezone.utc)

    # Should not raise — error is logged but non-fatal
    await tracker.reset()

    # Cache should still be cleared even if MongoDB fails
    assert len(tracker._cache) == 0  # ✅ Cache cleared despite MongoDB error
    mock_collection.delete_many.assert_called_once()  # ✅ Attempt was made


@pytest.mark.asyncio
async def test_reset_without_db_clears_cache_only():
    """reset() with no DB configured should still clear the in-memory cache."""
    from ml_engine.candle_tracker import CandleTracker

    tracker = CandleTracker(db=None)
    tracker._cache["XAUUSD"] = datetime.now(timezone.utc)
    tracker._cache["XAUEUR"] = datetime.now(timezone.utc)

    # Should not raise even without a DB handle
    await tracker.reset()

    assert len(tracker._cache) == 0  # ✅ Cache cleared


@pytest.mark.asyncio
async def test_first_signal_after_reset():
    """After reset(), the first is_new_candle() call should return True."""
    from ml_engine.candle_tracker import CandleTracker

    mock_db = MagicMock()
    mock_collection = AsyncMock()
    mock_db.candle_tracking = mock_collection
    mock_collection.delete_many = AsyncMock(
        return_value=MagicMock(deleted_count=1)
    )
    # MongoDB returns no record after reset (collection was cleared)
    mock_collection.find_one = AsyncMock(return_value=None)

    tracker = CandleTracker(db=mock_db)

    # Simulate a stale timestamp that was in cache before reset
    tracker._cache["XAUUSD"] = datetime(2026, 6, 18, 7, 0, 0, tzinfo=timezone.utc)

    # Reset on startup — clears both cache and MongoDB
    await tracker.reset()

    # First is_new_candle() call after reset should always be True
    current_time = datetime.now(timezone.utc)
    is_new = await tracker.is_new_candle("XAUUSD", current_time)

    assert is_new is True  # ✅ Signal generated immediately after restart


@pytest.mark.asyncio
async def test_reset_called_before_set_db():
    """reset() called before set_db() (no DB) should not raise."""
    from ml_engine.candle_tracker import CandleTracker

    tracker = CandleTracker()  # No DB injected yet
    tracker._cache["XAUUSD"] = datetime.now(timezone.utc)

    await tracker.reset()

    assert len(tracker._cache) == 0  # ✅ Cache cleared, no crash


@pytest.mark.asyncio
async def test_reset_then_set_db_then_update():
    """After reset() + set_db(), update_candle_time() should work normally."""
    from ml_engine.candle_tracker import CandleTracker

    mock_db = MagicMock()
    mock_collection = AsyncMock()
    mock_db.candle_tracking = mock_collection
    mock_collection.delete_many = AsyncMock(
        return_value=MagicMock(deleted_count=0)
    )
    mock_collection.update_one = AsyncMock()

    tracker = CandleTracker(db=mock_db)
    await tracker.reset()

    candle_time = datetime(2026, 6, 18, 11, 0, 0, tzinfo=timezone.utc)
    await tracker.update_candle_time("XAUUSD", candle_time)

    # Cache should now hold the new timestamp
    assert tracker._cache["XAUUSD"] == candle_time  # ✅ Updated correctly
    mock_collection.update_one.assert_called_once()  # ✅ MongoDB upsert called

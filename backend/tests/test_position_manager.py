"""
Unit tests for PositionManager.reset() — phantom position clearing behaviour.

Covers:
  - reset() clears the MongoDB open_positions collection
  - reset() handles MongoDB errors gracefully (non-fatal)
  - First add_position() call after reset() is accepted (0% exposure)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_reset_clears_open_positions():
    """reset() should clear the MongoDB open_positions collection."""
    from ml_engine.position_manager import PositionManager

    # Mock MongoDB
    mock_db = MagicMock()
    mock_collection = AsyncMock()
    mock_db.open_positions = mock_collection
    mock_collection.delete_many = AsyncMock(
        return_value=MagicMock(deleted_count=3)
    )

    pm = PositionManager(db=mock_db, account_balance=10_000.0)
    await pm.reset()

    # Verify MongoDB was cleared
    mock_collection.delete_many.assert_called_once_with({})


@pytest.mark.asyncio
async def test_reset_handles_mongodb_error():
    """reset() should handle MongoDB errors gracefully (non-fatal)."""
    from ml_engine.position_manager import PositionManager

    mock_db = MagicMock()
    mock_collection = AsyncMock()
    mock_db.open_positions = mock_collection
    mock_collection.delete_many = AsyncMock(
        side_effect=Exception("MongoDB connection error")
    )

    pm = PositionManager(db=mock_db, account_balance=10_000.0)

    # Should not raise — error is logged but non-fatal
    await pm.reset()

    mock_collection.delete_many.assert_called_once()


@pytest.mark.asyncio
async def test_reset_without_db_does_not_raise():
    """reset() with no DB configured should not raise."""
    from ml_engine.position_manager import PositionManager

    pm = PositionManager(db=None, account_balance=10_000.0)

    # Should complete without error
    await pm.reset()


@pytest.mark.asyncio
async def test_first_signal_after_reset():
    """After reset(), first signal should be accepted (0% exposure)."""
    from ml_engine.position_manager import PositionManager

    mock_db = MagicMock()
    mock_collection = AsyncMock()
    mock_db.open_positions = mock_collection
    mock_collection.delete_many = AsyncMock(
        return_value=MagicMock(deleted_count=3)
    )
    # No positions remain after reset
    mock_collection.count_documents = AsyncMock(return_value=0)
    mock_collection.find = MagicMock(
        return_value=AsyncMock(to_list=AsyncMock(return_value=[]))
    )
    mock_collection.insert_one = AsyncMock(
        return_value=MagicMock(inserted_id="mock-id-123")
    )

    pm = PositionManager(db=mock_db, account_balance=10_000.0)

    # Reset on startup — clears phantom positions
    await pm.reset()

    # Try to add a position (should succeed because exposure is 0%)
    result = await pm.add_position(
        pair="XAUUSD",
        entry=2350.00,
        tp_levels=[2355.00, 2360.00, 2365.00],
        sl=2340.00,
        size=0.02,
        confidence=0.75,
        signal_type="SELL",
        analysis="Test signal",
    )

    assert result["allowed"] is True  # ✅ Position accepted after reset

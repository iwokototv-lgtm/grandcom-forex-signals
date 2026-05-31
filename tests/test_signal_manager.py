"""
Unit tests for SignalManager — manager review & approval workflow.

All tests run without a live MongoDB instance.  Async database operations
are mocked using unittest.mock.AsyncMock so no real MongoDB or motor
connection is required.

Test coverage:
  - Permission enforcement (ADMIN / MANAGER allowed; VIEWER / unknown blocked)
  - Price-level validation helper (_validate_price_levels)
  - Signal approval workflow (approve_signal)
  - Signal rejection workflow (reject_signal, mandatory reason)
  - Signal adjustment workflow (adjust_signal, price validation)
  - Audit trail logging (_audit writes to signal_review_log)
  - Serialisation helper (_serialize)
  - Module-level singleton existence and interface
"""

from __future__ import annotations

import os
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup — mirrors conftest.py so the file is usable standalone
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(REPO_ROOT, "backend")

for _p in (REPO_ROOT, BACKEND_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Safe env defaults
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "gold_signals_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-ci")


# ---------------------------------------------------------------------------
# Import the module under test (skip gracefully if deps are missing)
# ---------------------------------------------------------------------------

try:
    from signal_manager import (
        SignalManager,
        _check_review_permission,
        _validate_price_levels,
        _serialize,
        signal_manager,
    )
    _IMPORT_OK = True
    _IMPORT_ERROR = ""
except Exception as _exc:
    _IMPORT_OK = False
    _IMPORT_ERROR = str(_exc)


def _require_import():
    """Skip the test if signal_manager could not be imported."""
    if not _IMPORT_OK:
        pytest.skip(f"signal_manager import failed: {_IMPORT_ERROR}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manager(role: str = "ADMIN", manager_id: str = "mgr-001") -> dict:
    """Return a minimal manager dict for use in tests."""
    return {"manager_id": manager_id, "role": role}


def _make_mock_db(signal_doc=None):
    """
    Build a MagicMock that mimics a motor database with async collection
    methods.  If *signal_doc* is provided it is returned by find_one().
    """
    db = MagicMock()

    # signals collection
    signals_col = MagicMock()
    signals_col.find_one = AsyncMock(return_value=signal_doc)
    signals_col.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    signals_col.count_documents = AsyncMock(return_value=0)

    # Cursor returned by find()
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=[])
    signals_col.find = MagicMock(return_value=cursor)

    # signal_review_log collection
    log_col = MagicMock()
    log_col.insert_one = AsyncMock(return_value=MagicMock())

    # signal_adjustments collection
    adj_col = MagicMock()
    adj_col.insert_one = AsyncMock(return_value=MagicMock())
    adj_cursor = MagicMock()
    adj_cursor.sort = MagicMock(return_value=adj_cursor)
    adj_col.find = MagicMock(return_value=adj_cursor)

    db.signals = signals_col
    db.signal_review_log = log_col
    db.signal_adjustments = adj_col

    return db


def _make_pending_signal(signal_id_str: str = None) -> dict:
    """Return a minimal PENDING_REVIEW signal document."""
    try:
        from bson import ObjectId
        oid = ObjectId(signal_id_str) if signal_id_str else ObjectId()
    except Exception:
        oid = "fake-oid"

    from datetime import datetime
    return {
        "_id": oid,
        "pair": "XAUUSD",
        "type": "BUY",
        "entry_price": 1900.0,
        "tp_levels": [1920.0, 1940.0],
        "sl_price": 1880.0,
        "confidence": 75.0,
        "status": "PENDING_REVIEW",
        "created_at": datetime.utcnow(),
    }


# ===========================================================================
# Permission enforcement
# ===========================================================================

class TestPermissionEnforcement:
    """_check_review_permission must allow ADMIN/MANAGER and block others."""

    def test_admin_allowed(self):
        _require_import()
        _check_review_permission(_make_manager(role="ADMIN"))  # must not raise

    def test_manager_allowed(self):
        _require_import()
        _check_review_permission(_make_manager(role="MANAGER"))  # must not raise

    def test_viewer_blocked(self):
        _require_import()
        with pytest.raises(PermissionError):
            _check_review_permission(_make_manager(role="VIEWER"))

    def test_unknown_role_blocked(self):
        _require_import()
        with pytest.raises(PermissionError):
            _check_review_permission(_make_manager(role="UNKNOWN"))

    def test_empty_role_blocked(self):
        _require_import()
        with pytest.raises(PermissionError):
            _check_review_permission(_make_manager(role=""))


# ===========================================================================
# Price-level validation
# ===========================================================================

class TestPriceLevelValidation:
    """_validate_price_levels must enforce BUY/SELL directional constraints."""

    def test_valid_buy_signal(self):
        _require_import()
        ok, err = _validate_price_levels("BUY", 1900.0, [1920.0, 1940.0, 1960.0], 1880.0)
        assert ok, err

    def test_valid_sell_signal(self):
        _require_import()
        ok, err = _validate_price_levels("SELL", 1900.0, [1880.0, 1860.0, 1840.0], 1920.0)
        assert ok, err

    def test_buy_sl_above_entry_rejected(self):
        _require_import()
        ok, err = _validate_price_levels("BUY", 1900.0, [1920.0], 1950.0)
        assert not ok
        assert "sl_price" in err.lower() or "sl" in err.lower()

    def test_buy_tp_below_entry_rejected(self):
        _require_import()
        ok, err = _validate_price_levels("BUY", 1900.0, [1880.0], 1880.0)
        assert not ok

    def test_sell_sl_below_entry_rejected(self):
        _require_import()
        ok, err = _validate_price_levels("SELL", 1900.0, [1880.0], 1850.0)
        assert not ok
        assert "sl_price" in err.lower() or "sl" in err.lower()

    def test_sell_tp_above_entry_rejected(self):
        _require_import()
        ok, err = _validate_price_levels("SELL", 1900.0, [1920.0], 1920.0)
        assert not ok

    def test_empty_tp_levels_rejected(self):
        _require_import()
        ok, err = _validate_price_levels("BUY", 1900.0, [], 1880.0)
        assert not ok
        assert "tp_levels" in err.lower()

    def test_too_many_tp_levels_rejected(self):
        _require_import()
        ok, err = _validate_price_levels(
            "BUY", 1900.0,
            [1910.0, 1920.0, 1930.0, 1940.0, 1950.0, 1960.0],  # 6 > MAX_TP_LEVELS (5)
            1880.0,
        )
        assert not ok

    def test_invalid_signal_type_rejected(self):
        _require_import()
        ok, err = _validate_price_levels("HOLD", 1900.0, [1920.0], 1880.0)
        assert not ok
        assert "buy or sell" in err.lower() or "signal_type" in err.lower()

    def test_zero_entry_price_rejected(self):
        _require_import()
        ok, err = _validate_price_levels("BUY", 0.0, [1920.0], 1880.0)
        assert not ok

    def test_buy_decreasing_tp_levels_rejected(self):
        _require_import()
        ok, err = _validate_price_levels("BUY", 1900.0, [1940.0, 1920.0], 1880.0)
        assert not ok

    def test_sell_increasing_tp_levels_rejected(self):
        _require_import()
        ok, err = _validate_price_levels("SELL", 1900.0, [1860.0, 1880.0], 1920.0)
        assert not ok

    def test_case_insensitive_signal_type(self):
        _require_import()
        ok, err = _validate_price_levels("buy", 1900.0, [1920.0], 1880.0)
        assert ok, err


# ===========================================================================
# Serialisation helper
# ===========================================================================

class TestSerialize:
    """_serialize must convert ObjectId and datetime to JSON-safe types."""

    def test_plain_dict_unchanged(self):
        _require_import()
        doc = {"key": "value", "number": 42}
        assert _serialize(doc) == {"key": "value", "number": 42}

    def test_id_field_converted_to_string(self):
        _require_import()
        try:
            from bson import ObjectId
        except ImportError:
            pytest.skip("bson not available")
        oid = ObjectId()
        result = _serialize({"_id": oid, "name": "test"})
        assert "id" in result
        assert result["id"] == str(oid)
        assert "_id" not in result

    def test_datetime_converted_to_iso_string(self):
        _require_import()
        from datetime import datetime
        now = datetime(2024, 1, 15, 12, 0, 0)
        result = _serialize({"created_at": now})
        assert isinstance(result["created_at"], str)
        assert "2024-01-15" in result["created_at"]

    def test_nested_dict_serialised(self):
        _require_import()
        from datetime import datetime
        result = _serialize({"meta": {"ts": datetime(2024, 1, 1, 0, 0, 0)}})
        assert isinstance(result["meta"]["ts"], str)

    def test_list_of_dicts_serialised(self):
        _require_import()
        from datetime import datetime
        result = _serialize({
            "items": [
                {"ts": datetime(2024, 1, 1, 0, 0, 0)},
                {"ts": datetime(2024, 1, 2, 0, 0, 0)},
            ]
        })
        assert isinstance(result["items"][0]["ts"], str)
        assert isinstance(result["items"][1]["ts"], str)


# ===========================================================================
# SignalManager singleton
# ===========================================================================

class TestSignalManagerSingleton:
    """The module-level signal_manager singleton must be a SignalManager."""

    def test_singleton_is_signal_manager_instance(self):
        _require_import()
        assert isinstance(signal_manager, SignalManager)

    def test_singleton_has_required_methods(self):
        _require_import()
        required = [
            "get_pending_signals",
            "get_signal_details",
            "approve_signal",
            "reject_signal",
            "adjust_signal",
            "get_signal_history",
            "get_approval_stats",
        ]
        for method in required:
            assert hasattr(signal_manager, method), (
                f"SignalManager is missing method: {method}"
            )


# ===========================================================================
# Approval workflow (async, uses AsyncMock — no real DB required)
# ===========================================================================

class TestApprovalWorkflow:
    """
    Test approve_signal / reject_signal / adjust_signal using AsyncMock
    database stubs.  No real MongoDB or motor connection is needed.
    """

    @pytest.fixture(autouse=True)
    def _skip_if_no_import(self):
        _require_import()

    @pytest.mark.asyncio
    async def test_approve_pending_signal(self):
        """Approving a PENDING_REVIEW signal returns success=True and new_status=ACTIVE."""
        signal_doc = _make_pending_signal()
        signal_id = str(signal_doc["_id"])
        db = _make_mock_db(signal_doc=signal_doc)

        sm = SignalManager()
        sm._get_db = lambda: db

        result = await sm.approve_signal(
            requesting_manager=_make_manager(role="ADMIN"),
            signal_id=signal_id,
            notes="Looks good",
        )

        assert result["success"] is True
        assert result["new_status"] == "ACTIVE"
        assert result["signal_id"] == signal_id
        db.signals.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_approve_requires_permission(self):
        """A VIEWER cannot approve signals — PermissionError is raised."""
        sm = SignalManager()
        sm._get_db = lambda: _make_mock_db()

        with pytest.raises(PermissionError):
            await sm.approve_signal(
                requesting_manager=_make_manager(role="VIEWER"),
                signal_id="507f1f77bcf86cd799439011",
            )

    @pytest.mark.asyncio
    async def test_approve_nonexistent_signal(self):
        """Approving a non-existent signal returns success=False."""
        db = _make_mock_db(signal_doc=None)  # find_one returns None
        sm = SignalManager()
        sm._get_db = lambda: db

        try:
            from bson import ObjectId
            fake_id = str(ObjectId())
        except ImportError:
            fake_id = "507f1f77bcf86cd799439011"

        result = await sm.approve_signal(
            requesting_manager=_make_manager(role="ADMIN"),
            signal_id=fake_id,
        )
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_approve_invalid_id(self):
        """Approving with a malformed ObjectId returns success=False."""
        sm = SignalManager()
        sm._get_db = lambda: _make_mock_db()

        result = await sm.approve_signal(
            requesting_manager=_make_manager(role="ADMIN"),
            signal_id="not-a-valid-objectid",
        )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_approve_already_active_signal(self):
        """Approving an ACTIVE signal returns success=False."""
        signal_doc = _make_pending_signal()
        signal_doc["status"] = "ACTIVE"
        signal_id = str(signal_doc["_id"])
        db = _make_mock_db(signal_doc=signal_doc)

        sm = SignalManager()
        sm._get_db = lambda: db

        result = await sm.approve_signal(
            requesting_manager=_make_manager(role="ADMIN"),
            signal_id=signal_id,
        )
        assert result["success"] is False
        assert "active" in result["error"].lower() or "cannot be approved" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_reject_pending_signal(self):
        """Rejecting a PENDING_REVIEW signal returns success=True and new_status=REJECTED."""
        signal_doc = _make_pending_signal()
        signal_id = str(signal_doc["_id"])
        db = _make_mock_db(signal_doc=signal_doc)

        sm = SignalManager()
        sm._get_db = lambda: db

        result = await sm.reject_signal(
            requesting_manager=_make_manager(role="MANAGER"),
            signal_id=signal_id,
            reason="Entry price is too far from structure",
        )

        assert result["success"] is True
        assert result["new_status"] == "REJECTED"
        assert "entry price" in result["rejection_reason"].lower()
        db.signals.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_reject_requires_reason(self):
        """Rejecting without a reason returns success=False."""
        signal_doc = _make_pending_signal()
        signal_id = str(signal_doc["_id"])
        db = _make_mock_db(signal_doc=signal_doc)

        sm = SignalManager()
        sm._get_db = lambda: db

        result = await sm.reject_signal(
            requesting_manager=_make_manager(role="ADMIN"),
            signal_id=signal_id,
            reason="",
        )
        assert result["success"] is False
        assert "reason" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_reject_blank_reason(self):
        """Rejecting with a whitespace-only reason returns success=False."""
        signal_doc = _make_pending_signal()
        signal_id = str(signal_doc["_id"])
        db = _make_mock_db(signal_doc=signal_doc)

        sm = SignalManager()
        sm._get_db = lambda: db

        result = await sm.reject_signal(
            requesting_manager=_make_manager(role="ADMIN"),
            signal_id=signal_id,
            reason="   ",
        )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_adjust_signal_entry_price(self):
        """Adjusting entry price updates the signal and sets status to ADJUSTED."""
        signal_doc = _make_pending_signal()
        signal_id = str(signal_doc["_id"])
        db = _make_mock_db(signal_doc=signal_doc)

        sm = SignalManager()
        sm._get_db = lambda: db

        result = await sm.adjust_signal(
            requesting_manager=_make_manager(role="ADMIN"),
            signal_id=signal_id,
            entry_price=1895.0,
            notes="Adjusted to better structure level",
        )

        assert result["success"] is True
        assert result["new_status"] == "ADJUSTED"
        assert result["entry_price"] == 1895.0

    @pytest.mark.asyncio
    async def test_adjust_signal_no_fields_provided(self):
        """Adjusting with no fields provided returns success=False."""
        signal_doc = _make_pending_signal()
        signal_id = str(signal_doc["_id"])
        db = _make_mock_db(signal_doc=signal_doc)

        sm = SignalManager()
        sm._get_db = lambda: db

        result = await sm.adjust_signal(
            requesting_manager=_make_manager(role="ADMIN"),
            signal_id=signal_id,
        )
        assert result["success"] is False
        assert "at least one" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_adjust_signal_invalid_price_structure(self):
        """Adjusting to an invalid price structure returns success=False."""
        signal_doc = _make_pending_signal()
        signal_id = str(signal_doc["_id"])
        db = _make_mock_db(signal_doc=signal_doc)

        sm = SignalManager()
        sm._get_db = lambda: db

        # SL above entry for a BUY signal — invalid
        result = await sm.adjust_signal(
            requesting_manager=_make_manager(role="ADMIN"),
            signal_id=signal_id,
            sl_price=1950.0,
        )
        assert result["success"] is False
        assert "validation" in result["error"].lower() or "sl" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_audit_log_written_on_approve(self):
        """Approving a signal writes an entry to signal_review_log."""
        signal_doc = _make_pending_signal()
        signal_id = str(signal_doc["_id"])
        db = _make_mock_db(signal_doc=signal_doc)

        sm = SignalManager()
        sm._get_db = lambda: db

        await sm.approve_signal(
            requesting_manager=_make_manager(role="ADMIN"),
            signal_id=signal_id,
            notes="Audit test",
        )

        db.signal_review_log.insert_one.assert_called_once()
        call_args = db.signal_review_log.insert_one.call_args[0][0]
        assert call_args["action"] == "signal:approve"
        assert call_args["signal_id"] == signal_id
        assert call_args["success"] is True

    @pytest.mark.asyncio
    async def test_audit_log_written_on_reject(self):
        """Rejecting a signal writes an entry to signal_review_log."""
        signal_doc = _make_pending_signal()
        signal_id = str(signal_doc["_id"])
        db = _make_mock_db(signal_doc=signal_doc)

        sm = SignalManager()
        sm._get_db = lambda: db

        await sm.reject_signal(
            requesting_manager=_make_manager(role="MANAGER"),
            signal_id=signal_id,
            reason="Risk/reward ratio is too low",
        )

        db.signal_review_log.insert_one.assert_called_once()
        call_args = db.signal_review_log.insert_one.call_args[0][0]
        assert call_args["action"] == "signal:reject"
        assert call_args["signal_id"] == signal_id


# ===========================================================================
# Get pending signals (async, uses AsyncMock)
# ===========================================================================

class TestGetPendingSignals:
    """get_pending_signals must return only PENDING_REVIEW signals."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_import(self):
        _require_import()

    @pytest.mark.asyncio
    async def test_returns_pending_signals(self):
        """get_pending_signals returns success=True with the signals list."""
        signal_doc = _make_pending_signal()
        db = _make_mock_db(signal_doc=signal_doc)

        # Override the cursor to return one signal
        cursor = MagicMock()
        cursor.sort = MagicMock(return_value=cursor)
        cursor.limit = MagicMock(return_value=cursor)
        cursor.to_list = AsyncMock(return_value=[signal_doc])
        db.signals.find = MagicMock(return_value=cursor)
        db.signals.count_documents = AsyncMock(return_value=1)

        sm = SignalManager()
        sm._get_db = lambda: db

        result = await sm.get_pending_signals(
            requesting_manager=_make_manager(role="ADMIN")
        )

        assert result["success"] is True
        assert result["total"] == 1
        assert len(result["signals"]) == 1

    @pytest.mark.asyncio
    async def test_viewer_cannot_list_pending(self):
        """A VIEWER cannot list pending signals — PermissionError is raised."""
        sm = SignalManager()
        sm._get_db = lambda: _make_mock_db()

        with pytest.raises(PermissionError):
            await sm.get_pending_signals(
                requesting_manager=_make_manager(role="VIEWER")
            )

    @pytest.mark.asyncio
    async def test_empty_queue_returns_empty_list(self):
        """An empty queue returns success=True with an empty signals list."""
        db = _make_mock_db()
        db.signals.count_documents = AsyncMock(return_value=0)

        sm = SignalManager()
        sm._get_db = lambda: db

        result = await sm.get_pending_signals(
            requesting_manager=_make_manager(role="MANAGER")
        )

        assert result["success"] is True
        assert result["signals"] == []
        assert result["total"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

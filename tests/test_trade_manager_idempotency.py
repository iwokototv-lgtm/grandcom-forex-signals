"""
Idempotency tests for TradeManager lifecycle actions.

These tests verify that after a mid-trade server restart — where MongoDB
already has the persisted state from a previous cycle — the TradeManager
does NOT re-fire lifecycle events (TP1 partial close, BE activation, TS
update) or double-count partial closes.

Each test simulates a restart by pre-loading the in-memory cache with a
trade document that already has the relevant flag set (as sync_from_mongodb
would populate it from MongoDB), then calling the lifecycle method again
and asserting it returns False and does not touch the database.

No live MongoDB instance is required — all DB calls are intercepted by a
simple async mock that records whether update_one was called.
"""

from __future__ import annotations

import asyncio
import sys
import os
import pytest

# ---------------------------------------------------------------------------
# Path setup — mirrors tests/conftest.py so the file works standalone too
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(REPO_ROOT, "backend")

for _p in (REPO_ROOT, BACKEND_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Guard: skip the whole module if bson is not installed (CI without pymongo)
# ---------------------------------------------------------------------------
try:
    from bson import ObjectId as _ObjectId  # noqa: F401
    from trade_manager import TradeManager
    _IMPORT_ERROR = None
except Exception as _exc:
    _IMPORT_ERROR = _exc

if _IMPORT_ERROR is not None:
    pytest.skip(
        f"Skipping test_trade_manager_idempotency: import failed — {_IMPORT_ERROR}",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run a coroutine synchronously (works in pytest without asyncio plugin)."""
    return asyncio.get_event_loop().run_until_complete(coro)


class _MockCollection:
    """Minimal async MongoDB collection stub that records update_one calls."""

    def __init__(self):
        self.update_one_calls: list[tuple] = []

    async def update_one(self, filter_doc, update_doc, *args, **kwargs):
        self.update_one_calls.append((filter_doc, update_doc))


class _MockDB:
    """Minimal async MongoDB database stub."""

    def __init__(self):
        self.gold_signals_v4 = _MockCollection()


def _make_trade_id() -> str:
    """Return a valid 24-char hex ObjectId string."""
    from bson import ObjectId
    return str(ObjectId())


def _make_base_trade(trade_id: str, **overrides) -> dict:
    """Return a minimal trade document suitable for the in-memory cache."""
    doc = {
        "_id_str":      trade_id,
        "pair":         "XAUUSD",
        "type":         "BUY",
        "status":       "ACTIVE",
        "entry_price":  2300.00,
        "sl_price":     2280.00,
        "current_sl":   2280.00,
        "be_sl":        2300.00,
        "be_trigger":   2310.00,
        "be_enabled":   True,
        "be_activated": False,
        "tp_levels":    [2320.00, 2340.00, 2360.00],
        "tp1_hit":      False,
        "ts_enabled":   True,
        "ts_start":     2320.00,
        "ts_distance":  15.00,
        "lots":         0.10,
    }
    doc.update(overrides)
    return doc


# ---------------------------------------------------------------------------
# Test: TP1 partial close idempotency
# ---------------------------------------------------------------------------

class TestTakePartialProfitIdempotency:
    """take_partial_profit must not re-fire when tp1_hit is already True."""

    def test_tp1_not_refired_after_restart(self):
        """
        Scenario: server crashed after TP1 was recorded in MongoDB.
        On restart sync_from_mongodb loads the trade with tp1_hit=True.
        Calling take_partial_profit("TP1") again must return False and
        must NOT call db.update_one.
        """
        trade_id = _make_trade_id()
        db = _MockDB()

        tm = TradeManager()
        # Simulate post-restart state: tp1 already persisted
        tm._open_trades[trade_id] = _make_base_trade(
            trade_id,
            tp1_hit=True,
            tp1_price=2320.00,
            status="PARTIAL",
        )

        result = _run(tm.take_partial_profit(db, trade_id, "TP1", 2320.00))

        assert result is False, "take_partial_profit must return False when TP1 already hit"
        assert len(db.gold_signals_v4.update_one_calls) == 0, (
            "update_one must NOT be called when TP1 is already recorded"
        )

    def test_tp1_fires_when_not_yet_hit(self):
        """
        Sanity check: take_partial_profit must still execute when tp1_hit is False.
        """
        trade_id = _make_trade_id()
        db = _MockDB()

        tm = TradeManager()
        tm._open_trades[trade_id] = _make_base_trade(trade_id, tp1_hit=False)

        result = _run(tm.take_partial_profit(db, trade_id, "TP1", 2320.00))

        assert result is True, "take_partial_profit must return True on first TP1 hit"
        assert len(db.gold_signals_v4.update_one_calls) == 1, (
            "update_one must be called exactly once on first TP1 hit"
        )

    def test_tp2_not_refired_after_restart(self):
        """TP2 idempotency mirrors TP1 — tp2_hit flag must block re-execution."""
        trade_id = _make_trade_id()
        db = _MockDB()

        tm = TradeManager()
        tm._open_trades[trade_id] = _make_base_trade(
            trade_id,
            tp1_hit=True,
            tp2_hit=True,
            tp2_price=2340.00,
            status="PARTIAL",
        )

        result = _run(tm.take_partial_profit(db, trade_id, "TP2", 2340.00))

        assert result is False, "take_partial_profit must return False when TP2 already hit"
        assert len(db.gold_signals_v4.update_one_calls) == 0

    def test_partial_close_not_double_counted(self):
        """
        Calling take_partial_profit twice for the same TP level must only
        produce one update_one call — the second call is blocked by the guard.
        """
        trade_id = _make_trade_id()
        db = _MockDB()

        tm = TradeManager()
        tm._open_trades[trade_id] = _make_base_trade(trade_id, tp1_hit=False)

        # First call — should execute
        _run(tm.take_partial_profit(db, trade_id, "TP1", 2320.00))
        # Second call — should be blocked (in-memory cache now has tp1_hit=True)
        result2 = _run(tm.take_partial_profit(db, trade_id, "TP1", 2320.00))

        assert result2 is False, "Second TP1 call must be blocked by idempotency guard"
        assert len(db.gold_signals_v4.update_one_calls) == 1, (
            "update_one must be called exactly once even when TP1 is triggered twice"
        )


# ---------------------------------------------------------------------------
# Test: Breakeven activation idempotency
# ---------------------------------------------------------------------------

class TestActivateBreakevenIdempotency:
    """activate_breakeven must not re-fire when be_activated is already True."""

    def test_be_not_reactivated_after_restart(self):
        """
        Scenario: server crashed after BE was activated and persisted.
        On restart sync_from_mongodb loads the trade with be_activated=True.
        Calling activate_breakeven again must return False and must NOT call
        db.update_one.
        """
        trade_id = _make_trade_id()
        db = _MockDB()

        tm = TradeManager()
        tm._open_trades[trade_id] = _make_base_trade(
            trade_id,
            be_activated=True,
            current_sl=2300.00,   # already at entry (BE level)
        )

        result = _run(tm.activate_breakeven(db, trade_id, 2315.00))

        assert result is False, "activate_breakeven must return False when BE already active"
        assert len(db.gold_signals_v4.update_one_calls) == 0, (
            "update_one must NOT be called when BE is already activated"
        )

    def test_be_activates_when_not_yet_set(self):
        """Sanity check: activate_breakeven must execute when be_activated is False."""
        trade_id = _make_trade_id()
        db = _MockDB()

        tm = TradeManager()
        tm._open_trades[trade_id] = _make_base_trade(trade_id, be_activated=False)

        result = _run(tm.activate_breakeven(db, trade_id, 2315.00))

        assert result is True, "activate_breakeven must return True on first activation"
        assert len(db.gold_signals_v4.update_one_calls) == 1

    def test_be_not_double_activated_in_same_session(self):
        """
        Calling activate_breakeven twice in the same session must only produce
        one update_one call — the second call is blocked by the in-memory guard.
        """
        trade_id = _make_trade_id()
        db = _MockDB()

        tm = TradeManager()
        tm._open_trades[trade_id] = _make_base_trade(trade_id, be_activated=False)

        _run(tm.activate_breakeven(db, trade_id, 2315.00))
        result2 = _run(tm.activate_breakeven(db, trade_id, 2315.00))

        assert result2 is False, "Second BE activation must be blocked"
        assert len(db.gold_signals_v4.update_one_calls) == 1


# ---------------------------------------------------------------------------
# Test: Trailing stop update idempotency
# ---------------------------------------------------------------------------

class TestUpdateTrailingStopIdempotency:
    """update_trailing_stop must not re-update when ts_last_price matches current price."""

    def test_ts_not_reupdated_at_same_price_after_restart(self):
        """
        Scenario: server crashed after a TS update was persisted with
        ts_last_price=2325.00.  On restart sync_from_mongodb loads the trade
        with ts_last_price=2325.00.  Calling update_trailing_stop at the same
        price must return False and must NOT call db.update_one.
        """
        trade_id = _make_trade_id()
        db = _MockDB()

        tm = TradeManager()
        tm._open_trades[trade_id] = _make_base_trade(
            trade_id,
            tp1_hit=True,
            ts_last_price=2325.00,
            current_sl=2310.00,
        )

        result = _run(
            tm.update_trailing_stop(db, trade_id, 2325.00, 15.00, "BUY", 2310.00)
        )

        assert result is False, (
            "update_trailing_stop must return False when ts_last_price matches current price"
        )
        assert len(db.gold_signals_v4.update_one_calls) == 0, (
            "update_one must NOT be called when TS was already updated at this price"
        )

    def test_ts_updates_at_new_price(self):
        """Sanity check: TS must update when price has moved beyond the last recorded level."""
        trade_id = _make_trade_id()
        db = _MockDB()

        tm = TradeManager()
        tm._open_trades[trade_id] = _make_base_trade(
            trade_id,
            tp1_hit=True,
            ts_last_price=2325.00,   # last update was at 2325
            current_sl=2310.00,
        )

        # Price has moved up to 2340 — new_sl = 2340 - 15 = 2325 > 2310 → improved
        result = _run(
            tm.update_trailing_stop(db, trade_id, 2340.00, 15.00, "BUY", 2310.00)
        )

        assert result is True, "update_trailing_stop must return True when price has moved"
        assert len(db.gold_signals_v4.update_one_calls) == 1

    def test_ts_not_reupdated_twice_at_same_price_in_session(self):
        """
        Calling update_trailing_stop twice at the same price in one session
        must only produce one update_one call.
        """
        trade_id = _make_trade_id()
        db = _MockDB()

        tm = TradeManager()
        tm._open_trades[trade_id] = _make_base_trade(
            trade_id,
            tp1_hit=True,
            current_sl=2295.00,
        )

        # First call at 2325 — should execute (new_sl=2310 > 2295)
        _run(tm.update_trailing_stop(db, trade_id, 2325.00, 15.00, "BUY", 2295.00))
        # Second call at the same price — should be blocked
        result2 = _run(
            tm.update_trailing_stop(db, trade_id, 2325.00, 15.00, "BUY", 2310.00)
        )

        assert result2 is False, "Second TS update at same price must be blocked"
        assert len(db.gold_signals_v4.update_one_calls) == 1

    def test_ts_sell_not_reupdated_at_same_price(self):
        """TS idempotency works for SELL trades too."""
        trade_id = _make_trade_id()
        db = _MockDB()

        tm = TradeManager()
        tm._open_trades[trade_id] = _make_base_trade(
            trade_id,
            type="SELL",
            tp1_hit=True,
            ts_last_price=2280.00,
            current_sl=2295.00,
        )

        result = _run(
            tm.update_trailing_stop(db, trade_id, 2280.00, 15.00, "SELL", 2295.00)
        )

        assert result is False, "SELL TS must not re-update at the same price"
        assert len(db.gold_signals_v4.update_one_calls) == 0


# ---------------------------------------------------------------------------
# Test: close_trade idempotency
# ---------------------------------------------------------------------------

class TestCloseTradeIdempotency:
    """close_trade must not re-close a trade already in a terminal state."""

    @pytest.mark.parametrize("terminal_status", ["WIN", "LOSS", "CLOSED"])
    def test_close_not_refired_for_terminal_status(self, terminal_status):
        """
        Scenario: server crashed after a trade was closed and persisted.
        On restart sync_from_mongodb would not load terminal trades (they are
        filtered out), but if one slips through or close_trade is called
        manually, it must be blocked.
        """
        trade_id = _make_trade_id()
        db = _MockDB()

        tm = TradeManager()
        tm._open_trades[trade_id] = _make_base_trade(
            trade_id,
            status=terminal_status,
        )

        result = _run(tm.close_trade(db, trade_id, 2320.00, terminal_status))

        assert result is False, (
            f"close_trade must return False when status is already '{terminal_status}'"
        )
        assert len(db.gold_signals_v4.update_one_calls) == 0, (
            f"update_one must NOT be called when trade is already '{terminal_status}'"
        )

    def test_close_executes_for_active_trade(self):
        """Sanity check: close_trade must execute for an ACTIVE trade."""
        trade_id = _make_trade_id()
        db = _MockDB()

        tm = TradeManager()
        tm._open_trades[trade_id] = _make_base_trade(trade_id, status="ACTIVE")

        result = _run(tm.close_trade(db, trade_id, 2280.00, "LOSS"))

        assert result is True, "close_trade must return True for an ACTIVE trade"
        assert len(db.gold_signals_v4.update_one_calls) == 1

    def test_close_executes_for_partial_trade(self):
        """close_trade must execute for a PARTIAL trade (TP1 hit, not yet fully closed)."""
        trade_id = _make_trade_id()
        db = _MockDB()

        tm = TradeManager()
        tm._open_trades[trade_id] = _make_base_trade(
            trade_id,
            status="PARTIAL",
            tp1_hit=True,
        )

        result = _run(tm.close_trade(db, trade_id, 2340.00, "WIN"))

        assert result is True, "close_trade must return True for a PARTIAL trade"
        assert len(db.gold_signals_v4.update_one_calls) == 1

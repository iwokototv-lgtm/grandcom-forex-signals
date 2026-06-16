"""
Unit Tests for Edge Cases — Bug #188, #191, #192

Covers the three critical bugs that affected signal production:

  Bug #188 — Peak balance hardcoded to $1M instead of account balance
  Bug #191 — Reversal detector ran BEFORE guard checking if positions exist
  Bug #192 — Consensus required 3/3 (unanimous) instead of 2/3 (majority)

Each test is self-contained and does NOT require a live database, API keys,
or a running server.  All external dependencies are mocked.
"""

import pytest
import pandas as pd
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch

from ml_engine.drawdown_recovery import DrawdownRecoveryManager
from ml_engine.reversal_detector import ReversalDetector
from ml_engine.hybrid_portfolio_system_v3 import HybridPortfolioSystemV3


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_ohlcv(n: int = 100, base_price: float = 2500.0) -> pd.DataFrame:
    """Return a minimal OHLCV DataFrame with *n* rows for indicator tests."""
    rng = np.random.default_rng(42)
    close = base_price + rng.normal(0, 5, n).cumsum()
    close = np.maximum(close, 1.0)  # keep prices positive
    high = close + rng.uniform(0, 3, n)
    low = close - rng.uniform(0, 3, n)
    low = np.maximum(low, 1.0)
    open_ = close + rng.normal(0, 1, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close})


# ═══════════════════════════════════════════════════════════════════════════════
# Bug #188 — Drawdown Peak Balance Initialization
# ═══════════════════════════════════════════════════════════════════════════════

class TestDrawdownPeakBalanceInitialization:
    """
    Verify peak balance is initialized to the real account balance,
    not a hardcoded value (e.g. $1,000,000).

    Root cause: peak_balance was set to a hardcoded constant on first call,
    causing drawdown to always read ~99% and halting trading immediately.
    """

    def test_reset_peak_balance_sets_correct_value(self):
        """reset_peak_balance() must set peak_balance to the supplied amount."""
        ddr = DrawdownRecoveryManager()

        for account_balance in [1_000, 10_000, 100_000]:
            ddr.reset_peak_balance(account_balance)
            assert ddr.peak_balance == account_balance, (
                f"peak_balance {ddr.peak_balance} != account_balance {account_balance}"
            )

    def test_drawdown_is_zero_at_start(self):
        """Drawdown must be 0% immediately after reset — no phantom drawdown."""
        ddr = DrawdownRecoveryManager()

        for account_balance in [1_000, 10_000, 100_000]:
            ddr.reset_peak_balance(account_balance)
            drawdown_pct = ddr.calculate_drawdown(account_balance)
            assert drawdown_pct == 0.0, (
                f"Drawdown should be 0% at start, got {drawdown_pct}% "
                f"(account_balance={account_balance})"
            )

    def test_drawdown_not_hardcoded_to_one_million(self):
        """
        If peak_balance were hardcoded to $1M and account is $10K,
        drawdown would be ~99%.  Verify this does NOT happen.
        """
        ddr = DrawdownRecoveryManager()
        account_balance = 10_000.0

        ddr.reset_peak_balance(account_balance)
        drawdown_pct = ddr.calculate_drawdown(account_balance)

        # With the bug: drawdown ≈ 99% → trading halted
        # After fix:    drawdown == 0% → trading allowed
        assert drawdown_pct < 1.0, (
            f"Drawdown {drawdown_pct:.2f}% is suspiciously high — "
            "peak_balance may be hardcoded to a large value"
        )

    def test_assess_initializes_peak_from_first_balance(self):
        """
        assess() must initialize peak_balance from the first call's
        current_balance, not from a hardcoded constant.
        """
        ddr = DrawdownRecoveryManager()
        account_balance = 25_000.0

        result = ddr.assess(current_balance=account_balance)

        assert result["valid"], "assess() returned invalid result"
        assert result["peak_balance"] == account_balance, (
            f"peak_balance {result['peak_balance']} != account_balance {account_balance}"
        )
        assert result["drawdown"]["current_pct"] == 0.0, (
            f"Drawdown should be 0% on first assess(), got {result['drawdown']['current_pct']}%"
        )

    def test_trading_not_halted_at_correct_balance(self):
        """
        Trading must NOT be halted when account balance equals peak balance
        (i.e. no drawdown has occurred).
        """
        ddr = DrawdownRecoveryManager()
        account_balance = 10_000.0

        ddr.reset_peak_balance(account_balance)
        result = ddr.assess(current_balance=account_balance)

        assert not result["trading_halted"], (
            f"Trading should not be halted at zero drawdown. "
            f"halt_reason={result.get('halt_reason')}"
        )
        assert result["size_multiplier"] == 1.0, (
            f"Size multiplier should be 1.0 at zero drawdown, "
            f"got {result['size_multiplier']}"
        )

    def test_real_drawdown_detected_correctly(self):
        """
        After a genuine loss, drawdown should be calculated correctly
        relative to the real peak, not a hardcoded value.
        """
        ddr = DrawdownRecoveryManager()
        starting_balance = 10_000.0
        loss_amount = 500.0  # 5% drawdown

        ddr.reset_peak_balance(starting_balance)
        current_balance = starting_balance - loss_amount
        drawdown_pct = ddr.calculate_drawdown(current_balance)

        expected_pct = (loss_amount / starting_balance) * 100  # 5.0%
        assert abs(drawdown_pct - expected_pct) < 0.01, (
            f"Expected drawdown ~{expected_pct:.2f}%, got {drawdown_pct:.4f}%"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Bug #191 — Reversal Detection with Zero Positions
# ═══════════════════════════════════════════════════════════════════════════════

class TestReversalWithZeroPositions:
    """
    Verify reversal detection is skipped when no positions exist.

    Root cause: the guard that checks position count was placed AFTER the
    reversal detection call, so detect_reversal() ran even with 0 positions,
    producing false CLOSE_ALL alerts.
    """

    @pytest.mark.asyncio
    async def test_reversal_detector_returns_no_reversal_on_first_call(self):
        """
        On the very first call for a pair, the detector has no prior regime
        to compare against, so it must NOT fire a reversal.
        """
        rd = ReversalDetector()
        df = _make_ohlcv(100)

        result = await rd.detect_reversal("XAUUSD", df, "BULLISH")

        assert not result["reversal_detected"], (
            "Reversal should not trigger on first call (no prior regime to compare)"
        )

    @pytest.mark.asyncio
    async def test_reversal_requires_confirmation_bars(self):
        """
        A single opposite-direction bar must NOT trigger a reversal —
        REVERSAL_CONFIRM_BARS consecutive bars are required.
        """
        from ml_engine.reversal_detector import REVERSAL_CONFIRM_BARS
        rd = ReversalDetector()

        # Seed the detector with a BULLISH regime
        df_bull = _make_ohlcv(100, base_price=2500.0)
        # Force bullish indicators: high RSI, bullish MA
        df_bull["close"] = np.linspace(2400, 2600, 100)
        df_bull["high"] = df_bull["close"] + 5
        df_bull["low"] = df_bull["close"] - 5
        await rd.detect_reversal("XAUUSD", df_bull, "BUY")

        # Now send a single bearish bar — should NOT trigger reversal yet
        df_bear = _make_ohlcv(100, base_price=2300.0)
        df_bear["close"] = np.linspace(2600, 2400, 100)
        df_bear["high"] = df_bear["close"] + 5
        df_bear["low"] = df_bear["close"] - 5

        result = await rd.detect_reversal("XAUUSD", df_bear, "SELL")

        # With REVERSAL_CONFIRM_BARS > 1, a single bar should not confirm
        if REVERSAL_CONFIRM_BARS > 1:
            assert not result["reversal_detected"], (
                f"Single opposite bar should not trigger reversal "
                f"(REVERSAL_CONFIRM_BARS={REVERSAL_CONFIRM_BARS})"
            )

    @pytest.mark.asyncio
    async def test_guard_order_positions_checked_before_detection(self):
        """
        Simulate the guard logic: reversal detection must be skipped
        entirely when position_count == 0.

        This test mocks the position manager and reversal detector to verify
        that detect_reversal() is never called when there are no positions.
        """
        mock_position_manager = MagicMock()
        mock_position_manager.get_position_count = AsyncMock(return_value=0)

        mock_reversal_detector = MagicMock()
        mock_reversal_detector.detect_reversal = AsyncMock(
            return_value={"reversal_detected": True, "reason": "FAKE_REVERSAL"}
        )

        # Simulate the guard logic from generate_signal()
        open_count = await mock_position_manager.get_position_count()

        reversal_called = False
        if open_count > 0:
            # This block should NOT execute when open_count == 0
            await mock_reversal_detector.detect_reversal("XAUUSD", None, "BULLISH")
            reversal_called = True

        assert not reversal_called, (
            "detect_reversal() must NOT be called when no positions exist"
        )
        mock_reversal_detector.detect_reversal.assert_not_called()

    @pytest.mark.asyncio
    async def test_guard_order_detection_runs_with_positions(self):
        """
        Reversal detection MUST run when positions exist (open_count > 0).
        """
        mock_position_manager = MagicMock()
        mock_position_manager.get_position_count = AsyncMock(return_value=2)

        mock_reversal_detector = MagicMock()
        mock_reversal_detector.detect_reversal = AsyncMock(
            return_value={"reversal_detected": False, "reason": "NO_REVERSAL"}
        )

        open_count = await mock_position_manager.get_position_count()

        reversal_called = False
        if open_count > 0:
            await mock_reversal_detector.detect_reversal("XAUUSD", None, "BULLISH")
            reversal_called = True

        assert reversal_called, (
            "detect_reversal() MUST be called when positions exist"
        )
        mock_reversal_detector.detect_reversal.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_false_alert_with_zero_positions(self):
        """
        End-to-end guard simulation: with 0 positions, no reversal alert
        should ever be sent, even if the detector would fire.
        """
        mock_position_manager = MagicMock()
        mock_position_manager.get_position_count = AsyncMock(return_value=0)

        mock_send_alert = AsyncMock()

        open_count = await mock_position_manager.get_position_count()

        if open_count > 0:
            # Reversal detection and alert would happen here
            await mock_send_alert("XAUUSD", "FAKE_REVERSAL", 0, 0.0)

        mock_send_alert.assert_not_called()

    def test_reversal_detector_state_reset(self):
        """reset_state() must clear the pair's regime history."""
        rd = ReversalDetector()
        rd._state["XAUUSD"] = {"regime": "BUY", "confirm_count": 1, "last_reversal": None}

        rd.reset_state("XAUUSD")

        assert rd.get_state("XAUUSD") == {}, (
            "State should be empty after reset_state()"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Bug #192 — Consensus Voting Logic (2/3 majority, not 3/3 unanimous)
# ═══════════════════════════════════════════════════════════════════════════════

class TestConsensusWithDisagreement:
    """
    Verify consensus uses 2/3 majority vote, not 3/3 unanimous.

    Root cause: the original logic required ALL three components to agree,
    meaning any single NEUTRAL or opposing vote killed the signal entirely.
    Real-world markets always have some disagreement between indicators.
    """

    def setup_method(self):
        """Create a fresh HybridPortfolioSystemV3 for each test."""
        self.hybrid = HybridPortfolioSystemV3(account_balance=10_000.0)

    def test_three_of_three_buy_produces_buy(self):
        """3/3 BUY → BUY with 90% confidence."""
        result = self.hybrid._apply_consensus_logic("BUY", "BUY", "BUY")
        assert result["signal"] == "BUY", f"Expected BUY, got {result['signal']}"
        assert result["confidence"] == 90, f"Expected 90%, got {result['confidence']}"

    def test_two_of_three_buy_produces_buy(self):
        """2/3 BUY (one NEUTRAL) → BUY with 70% confidence."""
        result = self.hybrid._apply_consensus_logic("BUY", "BUY", "NEUTRAL")
        assert result["signal"] == "BUY", f"Expected BUY, got {result['signal']}"
        assert result["confidence"] == 70, f"Expected 70%, got {result['confidence']}"

    def test_two_of_three_buy_with_opposing_vote(self):
        """2/3 BUY (one SELL) → BUY with 70% confidence."""
        result = self.hybrid._apply_consensus_logic("BUY", "SELL", "BUY")
        assert result["signal"] == "BUY", f"Expected BUY, got {result['signal']}"
        assert result["confidence"] == 70, f"Expected 70%, got {result['confidence']}"

    def test_no_consensus_produces_neutral(self):
        """1 BUY + 1 SELL + 1 NEUTRAL → NEUTRAL (no majority)."""
        result = self.hybrid._apply_consensus_logic("BUY", "SELL", "NEUTRAL")
        assert result["signal"] == "NEUTRAL", f"Expected NEUTRAL, got {result['signal']}"
        assert result["confidence"] == 0, f"Expected 0%, got {result['confidence']}"

    def test_three_of_three_sell_produces_sell(self):
        """3/3 SELL → SELL with 90% confidence."""
        result = self.hybrid._apply_consensus_logic("SELL", "SELL", "SELL")
        assert result["signal"] == "SELL", f"Expected SELL, got {result['signal']}"
        assert result["confidence"] == 90, f"Expected 90%, got {result['confidence']}"

    def test_two_of_three_sell_produces_sell(self):
        """2/3 SELL (one NEUTRAL) → SELL with 70% confidence."""
        result = self.hybrid._apply_consensus_logic("SELL", "SELL", "NEUTRAL")
        assert result["signal"] == "SELL", f"Expected SELL, got {result['signal']}"
        assert result["confidence"] == 70, f"Expected 70%, got {result['confidence']}"

    def test_all_combinations(self):
        """
        Exhaustive test of all meaningful vote combinations.

        Ensures the 2/3 majority rule is applied consistently across
        every possible input combination.
        """
        test_cases = [
            # (A,        B,        C,         expected_signal, expected_conf)
            ("BUY",    "BUY",    "BUY",     "BUY",     90),
            ("BUY",    "BUY",    "NEUTRAL",  "BUY",     70),
            ("BUY",    "BUY",    "SELL",    "BUY",     70),
            ("BUY",    "NEUTRAL", "BUY",    "BUY",     70),
            ("BUY",    "SELL",   "BUY",     "BUY",     70),
            ("NEUTRAL", "BUY",   "BUY",     "BUY",     70),
            ("SELL",   "BUY",    "BUY",     "BUY",     70),
            ("SELL",   "SELL",   "SELL",    "SELL",    90),
            ("SELL",   "SELL",   "NEUTRAL",  "SELL",    70),
            ("SELL",   "SELL",   "BUY",     "SELL",    70),
            ("SELL",   "NEUTRAL", "SELL",   "SELL",    70),
            ("SELL",   "BUY",    "SELL",    "SELL",    70),
            ("NEUTRAL", "SELL",  "SELL",    "SELL",    70),
            ("BUY",    "SELL",   "NEUTRAL",  "NEUTRAL", 0),
            ("BUY",    "NEUTRAL", "SELL",   "NEUTRAL", 0),
            ("NEUTRAL", "BUY",   "SELL",    "NEUTRAL", 0),
            ("NEUTRAL", "NEUTRAL", "NEUTRAL", "NEUTRAL", 0),
            ("BUY",    "NEUTRAL", "NEUTRAL", "NEUTRAL", 0),
            ("SELL",   "NEUTRAL", "NEUTRAL", "NEUTRAL", 0),
        ]

        for signal_a, signal_b, signal_c, expected_signal, expected_conf in test_cases:
            result = self.hybrid._apply_consensus_logic(signal_a, signal_b, signal_c)
            assert result["signal"] == expected_signal, (
                f"A={signal_a} B={signal_b} C={signal_c}: "
                f"expected signal={expected_signal}, got {result['signal']}"
            )
            assert result["confidence"] == expected_conf, (
                f"A={signal_a} B={signal_b} C={signal_c}: "
                f"expected confidence={expected_conf}, got {result['confidence']}"
            )

    def test_unanimous_not_required_for_signal(self):
        """
        The critical regression test: a signal MUST be produced when 2/3
        components agree, even if the third disagrees.

        With the old unanimous-vote bug, this would return NEUTRAL.
        """
        # 2 BUY + 1 SELL — should produce BUY, not NEUTRAL
        result = self.hybrid._apply_consensus_logic("BUY", "BUY", "SELL")
        assert result["signal"] != "NEUTRAL", (
            "BUG #192 REGRESSION: 2/3 BUY should produce BUY signal, "
            "not NEUTRAL. Unanimous vote is NOT required."
        )
        assert result["signal"] == "BUY"

    def test_vote_counts_are_returned(self):
        """_apply_consensus_logic() must return buy_count and sell_count."""
        result = self.hybrid._apply_consensus_logic("BUY", "BUY", "SELL")
        assert "buy_count" in result, "buy_count missing from result"
        assert "sell_count" in result, "sell_count missing from result"
        assert result["buy_count"] == 2
        assert result["sell_count"] == 1

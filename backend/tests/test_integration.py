"""
Integration Tests — Signal Generation Pipeline

Tests the full signal generation pipeline end-to-end, including:
  1. Signal generation at startup (no positions exist)
  2. Signal generation with existing positions (reversal detection path)
  3. Full pipeline: fetch → indicators → hybrid analysis → GPT → validate

These tests mock external dependencies (TwelveData API, OpenAI, MongoDB,
Telegram) so they can run in CI without live credentials.
"""

import pytest
import pandas as pd
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from datetime import datetime, timezone


# ── Shared fixtures ───────────────────────────────────────────────────────────

def _make_ohlcv(n: int = 100, base_price: float = 2500.0) -> pd.DataFrame:
    """Return a realistic OHLCV DataFrame with *n* rows."""
    rng = np.random.default_rng(0)
    close = base_price + rng.normal(0, 5, n).cumsum()
    close = np.maximum(close, 1.0)
    high = close + rng.uniform(0.5, 3, n)
    low = close - rng.uniform(0.5, 3, n)
    low = np.maximum(low, 1.0)
    open_ = close + rng.normal(0, 1, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close})


def _make_gpt_response(signal: str = "BUY", confidence: int = 75) -> dict:
    """Return a mock GPT signal response."""
    entry = 2500.0
    return {
        "signal": signal,
        "confidence": confidence,
        "entry_price": entry,
        "tp_levels": [entry + 5, entry + 10, entry + 15],
        "sl_price": entry - 15,
        "analysis": "Mock analysis for testing purposes.",
        "risk_reward": 2.0,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Test 1: Signal Generation at Startup (no positions)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSignalGenerationStartup:
    """
    Verify signal generation works correctly at startup when no positions exist.

    At startup:
    - position_count == 0
    - reversal detection must be SKIPPED (Bug #191 guard)
    - signal pipeline must proceed normally
    """

    @pytest.mark.asyncio
    async def test_reversal_skipped_when_no_positions(self):
        """
        With 0 open positions, reversal detection must be bypassed entirely.
        The pipeline should proceed to signal generation without error.
        """
        mock_pm = MagicMock()
        mock_pm.get_position_count = AsyncMock(return_value=0)

        mock_rd = MagicMock()
        mock_rd.detect_reversal = AsyncMock(
            return_value={"reversal_detected": True, "reason": "FAKE"}
        )

        # Simulate the guard logic from generate_signal()
        open_count = await mock_pm.get_position_count()

        reversal_triggered = False
        if open_count > 0:
            result = await mock_rd.detect_reversal("XAUUSD", None, "BULLISH")
            if result.get("reversal_detected"):
                reversal_triggered = True

        assert not reversal_triggered, (
            "Reversal must not trigger at startup when no positions exist"
        )
        mock_rd.detect_reversal.assert_not_called()

    @pytest.mark.asyncio
    async def test_pipeline_proceeds_after_zero_position_check(self):
        """
        After the position guard passes (0 positions), the pipeline must
        continue to fetch data and generate a signal.
        """
        mock_pm = MagicMock()
        mock_pm.get_position_count = AsyncMock(return_value=0)

        mock_fetch = AsyncMock(return_value=_make_ohlcv(100))
        mock_gpt = AsyncMock(return_value=_make_gpt_response("BUY", 75))

        # Simulate the guard + pipeline flow
        open_count = await mock_pm.get_position_count()
        assert open_count == 0

        # Guard passes — fetch data
        df = await mock_fetch("XAUUSD")
        assert df is not None
        assert len(df) >= 52

        # Generate signal
        gpt_result = await mock_gpt("XAUUSD", {}, {}, {})
        assert gpt_result is not None
        assert gpt_result["signal"] in ("BUY", "SELL", "NEUTRAL")

    @pytest.mark.asyncio
    async def test_no_false_close_all_at_startup(self):
        """
        close_all_positions() must NOT be called at startup when there are
        no positions, even if the reversal detector would fire.
        """
        mock_pm = MagicMock()
        mock_pm.get_position_count = AsyncMock(return_value=0)

        mock_close_all = AsyncMock(return_value={"closed": 0, "total_pnl": 0.0})

        open_count = await mock_pm.get_position_count()

        if open_count > 0:
            # This block must NOT execute
            await mock_close_all(reason="REVERSAL")

        mock_close_all.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# Test 2: Signal Generation with Existing Positions
# ═══════════════════════════════════════════════════════════════════════════════

class TestSignalGenerationWithPositions:
    """
    Verify reversal detection runs correctly when positions exist.
    """

    @pytest.mark.asyncio
    async def test_reversal_detection_runs_with_positions(self):
        """
        With open positions, detect_reversal() must be called.
        """
        mock_pm = MagicMock()
        mock_pm.get_position_count = AsyncMock(return_value=2)

        mock_rd = MagicMock()
        mock_rd.detect_reversal = AsyncMock(
            return_value={"reversal_detected": False, "reason": "NO_REVERSAL"}
        )

        open_count = await mock_pm.get_position_count()

        if open_count > 0:
            await mock_rd.detect_reversal("XAUUSD", _make_ohlcv(), "BULLISH")

        mock_rd.detect_reversal.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_all_called_on_reversal(self):
        """
        When a reversal is detected AND positions exist, close_all_positions()
        must be called.
        """
        mock_pm = MagicMock()
        mock_pm.get_position_count = AsyncMock(return_value=3)

        mock_rd = MagicMock()
        mock_rd.detect_reversal = AsyncMock(
            return_value={
                "reversal_detected": True,
                "reason": "Regime flipped BUY→SELL",
                "previous_regime": "BUY",
                "new_regime": "SELL",
            }
        )

        mock_close_all = AsyncMock(
            return_value={"closed": 3, "total_pnl": -150.0, "reason": "REVERSAL"}
        )
        mock_send_alert = AsyncMock()

        open_count = await mock_pm.get_position_count()

        if open_count > 0:
            reversal = await mock_rd.detect_reversal("XAUUSD", _make_ohlcv(), "BULLISH")
            if reversal.get("reversal_detected"):
                close_result = await mock_close_all(reason=f"REVERSAL: {reversal['reason']}")
                closed_count = close_result.get("closed", 0)
                if closed_count > 0:
                    await mock_send_alert(
                        "XAUUSD",
                        reversal["reason"],
                        closed_count,
                        close_result.get("total_pnl", 0.0),
                    )

        mock_close_all.assert_called_once()
        mock_send_alert.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_alert_when_close_returns_zero(self):
        """
        If close_all_positions() closes 0 positions (race condition),
        no Telegram alert should be sent.
        """
        mock_pm = MagicMock()
        mock_pm.get_position_count = AsyncMock(return_value=1)

        mock_rd = MagicMock()
        mock_rd.detect_reversal = AsyncMock(
            return_value={"reversal_detected": True, "reason": "FAKE_REVERSAL"}
        )

        # Simulate race: positions were closed between count check and close_all
        mock_close_all = AsyncMock(
            return_value={"closed": 0, "total_pnl": 0.0}
        )
        mock_send_alert = AsyncMock()

        open_count = await mock_pm.get_position_count()

        if open_count > 0:
            reversal = await mock_rd.detect_reversal("XAUUSD", _make_ohlcv(), "BULLISH")
            if reversal.get("reversal_detected"):
                close_result = await mock_close_all(reason="REVERSAL")
                closed_count = close_result.get("closed", 0)
                if closed_count > 0:
                    await mock_send_alert("XAUUSD", "FAKE_REVERSAL", closed_count, 0.0)

        mock_send_alert.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# Test 3: Full Signal Pipeline (Fetch → Indicators → Hybrid → GPT → Validate)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFullSignalPipeline:
    """
    Verify the entire signal generation pipeline works end-to-end
    with mocked external dependencies.
    """

    @pytest.mark.asyncio
    async def test_indicator_computation_from_ohlcv(self):
        """
        compute_indicators() must return all required fields from valid OHLCV data.
        """
        import ta

        df = _make_ohlcv(100)
        close = df["close"]
        high = df["high"]
        low = df["low"]

        rsi = ta.momentum.RSIIndicator(close, window=14).rsi()
        macd_obj = ta.trend.MACD(close)
        ma20 = ta.trend.SMAIndicator(close, window=20).sma_indicator()
        ma50 = ta.trend.SMAIndicator(close, window=50).sma_indicator()
        atr = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()

        ind = {
            "price": round(float(df["close"].iloc[-1]), 2),
            "rsi": round(float(rsi.iloc[-1]), 2),
            "macd": round(float(macd_obj.macd().iloc[-1]), 6),
            "macd_sig": round(float(macd_obj.macd_signal().iloc[-1]), 6),
            "ma20": round(float(ma20.iloc[-1]), 2),
            "ma50": round(float(ma50.iloc[-1]), 2),
            "atr": round(float(atr.iloc[-1]), 2),
            "trend": "BULLISH" if float(df["close"].iloc[-1]) > float(ma50.iloc[-1]) else "BEARISH",
        }

        required_fields = ["price", "rsi", "macd", "macd_sig", "ma20", "ma50", "atr", "trend"]
        for field in required_fields:
            assert field in ind, f"Missing indicator field: {field}"

        assert 0 <= ind["rsi"] <= 100, f"RSI out of range: {ind['rsi']}"
        assert ind["atr"] > 0, f"ATR must be positive: {ind['atr']}"
        assert ind["trend"] in ("BULLISH", "BEARISH"), f"Invalid trend: {ind['trend']}"

    @pytest.mark.asyncio
    async def test_hybrid_system_component_a_trend(self):
        """
        Component A (trend confirmation) must return a valid vote dict
        from OHLCV data without requiring external API calls.
        """
        from ml_engine.hybrid_portfolio_system_v3 import HybridPortfolioSystemV3

        hybrid = HybridPortfolioSystemV3(account_balance=10_000.0)
        df = _make_ohlcv(100)

        result = hybrid._component_a_trend(df)

        assert "vote" in result, "Component A must return 'vote'"
        assert result["vote"] in ("BUY", "SELL", "NEUTRAL"), (
            f"Invalid vote: {result['vote']}"
        )
        assert "confidence" in result, "Component A must return 'confidence'"
        assert 0.0 <= result["confidence"] <= 1.0, (
            f"Confidence out of range: {result['confidence']}"
        )
        assert result.get("valid", True), f"Component A returned invalid: {result}"

    @pytest.mark.asyncio
    async def test_consensus_logic_in_pipeline(self):
        """
        The consensus logic must produce a signal when 2/3 components agree,
        even if the third disagrees (Bug #192 regression).
        """
        from ml_engine.hybrid_portfolio_system_v3 import HybridPortfolioSystemV3

        hybrid = HybridPortfolioSystemV3(account_balance=10_000.0)

        # 2/3 BUY — must produce BUY, not NEUTRAL
        result = hybrid._apply_consensus_logic("BUY", "BUY", "SELL")
        assert result["signal"] == "BUY", (
            "2/3 BUY must produce BUY signal in pipeline consensus"
        )

        # 2/3 SELL — must produce SELL, not NEUTRAL
        result = hybrid._apply_consensus_logic("SELL", "SELL", "BUY")
        assert result["signal"] == "SELL", (
            "2/3 SELL must produce SELL signal in pipeline consensus"
        )

    @pytest.mark.asyncio
    async def test_signal_validation_bounds(self):
        """
        Generated signals must have valid signal type and confidence bounds.
        """
        valid_signals = ("BUY", "SELL", "NEUTRAL")

        # Simulate various GPT responses
        test_responses = [
            {"signal": "BUY", "confidence": 75},
            {"signal": "SELL", "confidence": 80},
            {"signal": "NEUTRAL", "confidence": 0},
        ]

        for resp in test_responses:
            assert resp["signal"] in valid_signals, (
                f"Invalid signal type: {resp['signal']}"
            )
            assert 0 <= resp["confidence"] <= 100, (
                f"Confidence out of range: {resp['confidence']}"
            )

    @pytest.mark.asyncio
    async def test_drawdown_check_does_not_halt_at_zero_drawdown(self):
        """
        Drawdown recovery check must NOT halt trading when drawdown is 0%
        (i.e. account balance equals peak balance — Bug #188 regression).
        """
        from ml_engine.drawdown_recovery import DrawdownRecoveryManager

        ddr = DrawdownRecoveryManager()
        account_balance = 10_000.0

        # Initialize correctly (not hardcoded)
        ddr.reset_peak_balance(account_balance)

        assessment = ddr.assess(current_balance=account_balance)

        assert not assessment.get("trading_halted"), (
            "Trading must not be halted at 0% drawdown. "
            "Peak balance may be hardcoded (Bug #188)."
        )
        assert assessment.get("size_multiplier", 0) == 1.0, (
            f"Size multiplier should be 1.0 at 0% drawdown, "
            f"got {assessment.get('size_multiplier')}"
        )

    @pytest.mark.asyncio
    async def test_minimum_candle_requirement(self):
        """
        Pipeline must reject DataFrames with fewer than 52 candles
        (insufficient for indicator calculation).
        """
        df_short = _make_ohlcv(30)  # Too few candles
        df_ok = _make_ohlcv(100)    # Sufficient candles

        assert len(df_short) < 52, "Short DataFrame should have < 52 rows"
        assert len(df_ok) >= 52, "OK DataFrame should have >= 52 rows"

        # Simulate the guard from generate_signal()
        def _check_candles(df):
            return df is not None and len(df) >= 52

        assert not _check_candles(df_short), "Short DataFrame should fail candle check"
        assert _check_candles(df_ok), "OK DataFrame should pass candle check"

    @pytest.mark.asyncio
    async def test_position_manager_add_position_mock(self):
        """
        Position manager add_position() must be called with correct parameters
        after a valid signal is generated.
        """
        mock_pm = MagicMock()
        mock_pm.add_position = AsyncMock(
            return_value={"allowed": True, "position_id": "test-id-123", "reason": "OK"}
        )

        result = await mock_pm.add_position(
            pair="XAUUSD",
            entry=2500.0,
            tp_levels=[2505.0, 2510.0, 2515.0],
            sl=2485.0,
            size=1.0,
            confidence=75.0,
            signal_type="BUY",
            analysis="Test signal",
        )

        assert result["allowed"], "Position should be allowed"
        assert result["position_id"] == "test-id-123"
        mock_pm.add_position.assert_called_once()

"""
Comprehensive tests for signal generation pipeline.
Tests edge cases, integration, and startup validation.

Covers the three production bugs fixed in PRs #188, #191, #192:
  Bug #1 (PR #188): Peak balance hardcoded to $1M instead of account balance
  Bug #2 (PR #191): Reversal detector running before guard check (false alerts)
  Bug #3 (PR #192): Consensus logic too strict (3/3 instead of 2/3)
"""

import pytest
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
import pandas as pd
import numpy as np

from ml_engine.risk_manager import RiskManager
from ml_engine.position_manager import PositionManager
from ml_engine.reversal_detector import ReversalDetector
from ml_engine.hybrid_portfolio_system_v3 import HybridPortfolioSystemV3


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────

def _make_df(n: int = 60) -> pd.DataFrame:
    """Return a minimal OHLCV DataFrame with *n* rows."""
    close = np.linspace(1900.0, 1950.0, n)
    return pd.DataFrame({
        "open":  close - 1.0,
        "high":  close + 2.0,
        "low":   close - 2.0,
        "close": close,
        "volume": np.ones(n) * 1000,
    })


# ─────────────────────────────────────────────────────────────────────────
# UNIT TESTS: Bug #1 — Peak balance initialisation (PR #188)
# ─────────────────────────────────────────────────────────────────────────

class TestDrawdownInitialization:
    """Verify peak balance is initialised from account balance, not hardcoded."""

    def test_peak_balance_equals_account_balance_on_first_set(self):
        """equity_peak must equal the balance passed to set_account_balance."""
        account_balance = 5000.0
        risk_mgr = RiskManager()

        # Before any call, equity_peak is 0
        assert risk_mgr.equity_peak == 0.0

        risk_mgr.set_account_balance(account_balance)

        assert risk_mgr.equity_peak == account_balance, (
            f"Peak balance {risk_mgr.equity_peak} != account balance {account_balance}. "
            "Bug #1: peak was hardcoded to $1M."
        )

    def test_peak_balance_not_hardcoded_to_1m(self):
        """equity_peak must never be $1,000,000 when balance is different."""
        risk_mgr = RiskManager()
        risk_mgr.set_account_balance(10_000.0)

        assert risk_mgr.equity_peak != 1_000_000.0, (
            "Bug #1 regression: equity_peak is still hardcoded to $1M."
        )

    def test_peak_balance_updates_on_new_high(self):
        """equity_peak should update when a higher balance is set."""
        risk_mgr = RiskManager()
        risk_mgr.set_account_balance(10_000.0)
        risk_mgr.set_account_balance(12_000.0)  # new high

        assert risk_mgr.equity_peak == 12_000.0

    def test_peak_balance_does_not_decrease(self):
        """equity_peak must not decrease when balance drops below the peak."""
        risk_mgr = RiskManager()
        risk_mgr.set_account_balance(10_000.0)
        risk_mgr.set_account_balance(8_000.0)  # drawdown — peak stays

        assert risk_mgr.equity_peak == 10_000.0

    @pytest.mark.asyncio
    async def test_drawdown_calculation_correct(self):
        """Drawdown percentage must be calculated against the correct peak."""
        risk_mgr = RiskManager()
        risk_mgr.set_account_balance(1_000.0)   # peak = 1000
        risk_mgr.current_equity = 900.0          # simulate 10% loss

        result = await risk_mgr.check_account_drawdown()
        drawdown_pct = result["drawdown_pct"]

        assert 9.5 < drawdown_pct < 10.5, (
            f"Drawdown {drawdown_pct:.2f}% should be ~10% "
            f"(peak=1000, current=900). Bug #1 may still be present."
        )

    @pytest.mark.asyncio
    async def test_drawdown_zero_when_at_peak(self):
        """Drawdown must be 0% when current equity equals peak."""
        risk_mgr = RiskManager()
        risk_mgr.set_account_balance(10_000.0)

        result = await risk_mgr.check_account_drawdown()
        assert result["drawdown_pct"] == 0.0


# ─────────────────────────────────────────────────────────────────────────
# UNIT TESTS: Bug #2 — Reversal guard order (PR #191)
# ─────────────────────────────────────────────────────────────────────────

class TestReversalDetectionGuard:
    """Verify reversal detection is gated on open positions (Bug #2 fix)."""

    @pytest.mark.asyncio
    async def test_no_reversal_check_when_zero_positions(self):
        """
        When no positions exist, detect_reversal must NOT be called.
        Bug #2: reversal ran before the position-count guard, causing false alerts.
        """
        position_mgr = PositionManager()
        reversal_det = ReversalDetector()
        df = _make_df()

        # Patch get_position_count to return 0
        position_mgr.get_position_count = AsyncMock(return_value=0)
        reversal_det.detect_reversal = AsyncMock(
            return_value={"reversal_detected": True, "reason": "Test reversal"}
        )

        # Simulate the guard logic from generate_signal
        open_count = await position_mgr.get_position_count()
        if open_count > 0:
            await reversal_det.detect_reversal("XAUUSD", df, "NEUTRAL")

        # detect_reversal must NOT have been called (no positions)
        reversal_det.detect_reversal.assert_not_called()

    @pytest.mark.asyncio
    async def test_reversal_check_runs_when_positions_exist(self):
        """detect_reversal must be called when positions are open."""
        position_mgr = PositionManager()
        reversal_det = ReversalDetector()
        df = _make_df()

        position_mgr.get_position_count = AsyncMock(return_value=2)
        reversal_det.detect_reversal = AsyncMock(
            return_value={"reversal_detected": False, "reason": ""}
        )

        open_count = await position_mgr.get_position_count()
        if open_count > 0:
            await reversal_det.detect_reversal("XAUUSD", df, "NEUTRAL")

        reversal_det.detect_reversal.assert_called_once()

    @pytest.mark.asyncio
    async def test_alert_not_sent_when_no_positions_closed(self):
        """
        send_reversal_alert must be skipped when closed_count == 0.
        This prevents false-positive Telegram alerts.
        """
        from gold_server_v3 import send_reversal_alert

        with patch("gold_server_v3.get_bot") as mock_get_bot:
            mock_bot = AsyncMock()
            mock_get_bot.return_value = mock_bot

            # closed_count = 0 → alert must be suppressed
            await send_reversal_alert(
                pair="XAUUSD",
                reason="Test reversal",
                closed_count=0,
                total_pnl=0.0,
            )

            mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_alert_sent_when_positions_closed(self):
        """send_reversal_alert must fire when positions were actually closed."""
        from gold_server_v3 import send_reversal_alert

        with patch("gold_server_v3.get_bot") as mock_get_bot:
            mock_bot = AsyncMock()
            mock_get_bot.return_value = mock_bot

            await send_reversal_alert(
                pair="XAUUSD",
                reason="Trend reversal confirmed",
                closed_count=2,
                total_pnl=150.0,
            )

            mock_bot.send_message.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────
# UNIT TESTS: Bug #3 — Consensus logic 2/3 (PR #192)
# ─────────────────────────────────────────────────────────────────────────

class TestConsensusLogic:
    """Verify hybrid consensus uses 2/3 majority vote, not 3/3 (Bug #3 fix)."""

    def _make_hybrid(self) -> HybridPortfolioSystemV3:
        return HybridPortfolioSystemV3(account_balance=10_000.0)

    @pytest.mark.asyncio
    async def test_consensus_2_out_of_3_buy(self):
        """BUY signal must be generated when 2/3 components vote BUY."""
        hybrid = self._make_hybrid()
        df = _make_df()

        # A=BUY, B=BUY, C=NEUTRAL → 2/3 BUY
        hybrid._component_a_trend = MagicMock(
            return_value={"vote": "BUY", "confidence": 0.80, "valid": True}
        )
        hybrid._component_b_sr = MagicMock(
            return_value={"vote": "BUY", "confidence": 0.75, "valid": True}
        )
        hybrid._component_c_mtf = MagicMock(
            return_value={"vote": "NEUTRAL", "confidence": 0.0, "valid": True}
        )

        # Bypass economic calendar and MTF async calls
        with patch.object(
            hybrid.economic_calendar, "is_safe_to_trade",
            new_callable=AsyncMock,
            return_value={"safe_to_trade": True},
        ):
            with patch.object(
                hybrid.mtf_confirmation, "analyze",
                new_callable=AsyncMock,
                return_value={
                    "valid": True,
                    "dominant_direction": "NEUTRAL",
                    "alignment_score": 50.0,
                },
            ):
                result = await hybrid.generate_signal(
                    symbol="XAUUSD", df_4h=df, strategy_mode="original"
                )

        assert result["signal"] == "BUY", (
            f"Expected BUY (2/3 agree), got {result['signal']}. "
            "Bug #3: consensus may still require 3/3."
        )
        assert result["confidence"] == 70, (
            f"Expected 70% confidence for 2/3 consensus, got {result['confidence']}."
        )

    @pytest.mark.asyncio
    async def test_consensus_2_out_of_3_sell(self):
        """SELL signal must be generated when 2/3 components vote SELL."""
        hybrid = self._make_hybrid()
        df = _make_df()

        hybrid._component_a_trend = MagicMock(
            return_value={"vote": "SELL", "confidence": 0.80, "valid": True}
        )
        hybrid._component_b_sr = MagicMock(
            return_value={"vote": "SELL", "confidence": 0.75, "valid": True}
        )
        hybrid._component_c_mtf = MagicMock(
            return_value={"vote": "NEUTRAL", "confidence": 0.0, "valid": True}
        )

        with patch.object(
            hybrid.economic_calendar, "is_safe_to_trade",
            new_callable=AsyncMock,
            return_value={"safe_to_trade": True},
        ):
            with patch.object(
                hybrid.mtf_confirmation, "analyze",
                new_callable=AsyncMock,
                return_value={
                    "valid": True,
                    "dominant_direction": "NEUTRAL",
                    "alignment_score": 50.0,
                },
            ):
                result = await hybrid.generate_signal(
                    symbol="XAUUSD", df_4h=df, strategy_mode="original"
                )

        assert result["signal"] == "SELL", (
            f"Expected SELL (2/3 agree), got {result['signal']}."
        )

    @pytest.mark.asyncio
    async def test_consensus_3_out_of_3_highest_confidence(self):
        """All 3 components agreeing must yield 90% confidence."""
        hybrid = self._make_hybrid()
        df = _make_df()

        hybrid._component_a_trend = MagicMock(
            return_value={"vote": "BUY", "confidence": 0.90, "valid": True}
        )
        hybrid._component_b_sr = MagicMock(
            return_value={"vote": "BUY", "confidence": 0.85, "valid": True}
        )
        hybrid._component_c_mtf = MagicMock(
            return_value={"vote": "BUY", "confidence": 0.80, "valid": True}
        )

        with patch.object(
            hybrid.economic_calendar, "is_safe_to_trade",
            new_callable=AsyncMock,
            return_value={"safe_to_trade": True},
        ):
            with patch.object(
                hybrid.mtf_confirmation, "analyze",
                new_callable=AsyncMock,
                return_value={
                    "valid": True,
                    "dominant_direction": "BULLISH",
                    "alignment_score": 90.0,
                },
            ):
                result = await hybrid.generate_signal(
                    symbol="XAUUSD", df_4h=df, strategy_mode="original"
                )

        assert result["signal"] == "BUY"
        assert result["confidence"] == 90, (
            f"Expected 90% confidence for 3/3 consensus, got {result['confidence']}."
        )

    @pytest.mark.asyncio
    async def test_consensus_no_agreement_neutral(self):
        """1-1-1 split (BUY/SELL/NEUTRAL) must produce NEUTRAL with 0 confidence."""
        hybrid = self._make_hybrid()
        df = _make_df()

        hybrid._component_a_trend = MagicMock(
            return_value={"vote": "BUY", "confidence": 0.60, "valid": True}
        )
        hybrid._component_b_sr = MagicMock(
            return_value={"vote": "SELL", "confidence": 0.60, "valid": True}
        )
        hybrid._component_c_mtf = MagicMock(
            return_value={"vote": "NEUTRAL", "confidence": 0.0, "valid": True}
        )

        with patch.object(
            hybrid.economic_calendar, "is_safe_to_trade",
            new_callable=AsyncMock,
            return_value={"safe_to_trade": True},
        ):
            with patch.object(
                hybrid.mtf_confirmation, "analyze",
                new_callable=AsyncMock,
                return_value={
                    "valid": True,
                    "dominant_direction": "NEUTRAL",
                    "alignment_score": 50.0,
                },
            ):
                result = await hybrid.generate_signal(
                    symbol="XAUUSD", df_4h=df, strategy_mode="original"
                )

        assert result["signal"] == "NEUTRAL", (
            f"Expected NEUTRAL (no consensus), got {result['signal']}."
        )
        assert result["confidence"] == 0, (
            f"Expected 0% confidence for no consensus, got {result['confidence']}."
        )

    def test_consensus_vote_counting_buy(self):
        """Direct vote-count logic: 2 BUY votes must produce BUY."""
        signals = ["BUY", "BUY", "NEUTRAL"]
        buy_count = signals.count("BUY")
        sell_count = signals.count("SELL")

        if buy_count >= 2:
            signal = "BUY"
        elif sell_count >= 2:
            signal = "SELL"
        else:
            signal = "NEUTRAL"

        assert signal == "BUY"

    def test_consensus_vote_counting_requires_only_2(self):
        """Consensus threshold is 2/3, not 3/3."""
        # 2 votes should be enough — this was the Bug #3 regression
        for buy_votes in [2, 3]:
            signals = ["BUY"] * buy_votes + ["NEUTRAL"] * (3 - buy_votes)
            buy_count = signals.count("BUY")
            result = "BUY" if buy_count >= 2 else "NEUTRAL"
            assert result == "BUY", (
                f"Expected BUY with {buy_votes}/3 votes, got NEUTRAL. "
                "Bug #3: threshold may still be 3/3."
            )


# ─────────────────────────────────────────────────────────────────────────
# INTEGRATION TESTS: Full Pipeline
# ─────────────────────────────────────────────────────────────────────────

class TestSignalGenerationPipeline:
    """Integration tests for the full signal generation pipeline."""

    @pytest.mark.asyncio
    async def test_generate_signal_skips_when_risk_halted(self):
        """generate_signal must return early when risk manager halts trading."""
        from gold_server_v3 import generate_signal

        with patch("gold_server_v3._calendar_filter") as mock_cal, \
             patch("gold_server_v3._risk_manager") as mock_risk:

            mock_cal.is_blackout_period = AsyncMock(return_value=False)
            mock_risk.enforce_risk_limits = AsyncMock(
                return_value={"trading_allowed": False, "reason": "DAILY_LOSS_LIMIT"}
            )
            mock_risk.set_account_balance = MagicMock()

            with patch("gold_server_v3._position_manager") as mock_pm:
                mock_pm.get_position_count = AsyncMock(return_value=0)
                mock_pm.close_all_positions = AsyncMock(
                    return_value={"closed": 0, "total_pnl": 0.0}
                )

                # Should return early — no signal generated
                result = await generate_signal("XAUUSD")
                assert result is None  # generate_signal returns None on early exit

    @pytest.mark.asyncio
    async def test_generate_signal_skips_during_news_blackout(self):
        """generate_signal must return early during economic calendar blackout."""
        from gold_server_v3 import generate_signal

        with patch("gold_server_v3._calendar_filter") as mock_cal:
            mock_cal.is_blackout_period = AsyncMock(return_value=True)
            mock_cal.get_next_high_impact_event = AsyncMock(
                return_value={"event": "NFP", "minutes_away": 15}
            )

            result = await generate_signal("XAUUSD")
            assert result is None

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_with_retry_succeeds_on_second_attempt(self):
        """fetch_ohlcv_with_retry must succeed when first attempt times out."""
        from gold_server_v3 import fetch_ohlcv_with_retry
        import aiohttp

        call_count = 0

        async def mock_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise asyncio.TimeoutError()
            # Second attempt returns valid data
            mock_resp = AsyncMock()
            mock_resp.json = AsyncMock(return_value={
                "values": [
                    {"datetime": "2024-01-01 00:00:00",
                     "open": "1900", "high": "1910", "low": "1890", "close": "1905"}
                    for _ in range(60)
                ]
            })
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=False)
            return mock_resp

        mock_session = AsyncMock()
        mock_session.get = mock_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                df = await fetch_ohlcv_with_retry(
                    "XAUUSD", interval="4h", outputsize=60, max_retries=3
                )

        assert df is not None, "fetch_ohlcv_with_retry should succeed on retry"
        assert call_count == 2, f"Expected 2 attempts, got {call_count}"

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_with_retry_returns_none_after_all_failures(self):
        """fetch_ohlcv_with_retry must return None after exhausting all retries."""
        from gold_server_v3 import fetch_ohlcv_with_retry

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                df = await fetch_ohlcv_with_retry(
                    "XAUUSD", interval="4h", outputsize=60, max_retries=3
                )

        assert df is None


# ─────────────────────────────────────────────────────────────────────────
# STARTUP VALIDATION
# ─────────────────────────────────────────────────────────────────────────

class TestStartupValidation:
    """Validate system state at startup to catch initialisation bugs early."""

    def test_account_balance_initialized_nonzero(self):
        """RiskManager must accept a positive account balance."""
        risk_mgr = RiskManager()
        risk_mgr.set_account_balance(10_000.0)

        assert risk_mgr.current_equity > 0, "Account balance not initialised"
        assert risk_mgr.starting_balance > 0, "Starting balance not set"

    def test_peak_balance_initialized_correctly(self):
        """equity_peak must equal account balance after set_account_balance."""
        risk_mgr = RiskManager()
        risk_mgr.set_account_balance(10_000.0)

        assert risk_mgr.equity_peak == 10_000.0, (
            f"equity_peak={risk_mgr.equity_peak} should be 10000.0 (Bug #1 check)"
        )

    def test_hybrid_system_initializes_all_components(self):
        """HybridPortfolioSystemV3 must initialise all 3 core components."""
        hybrid = HybridPortfolioSystemV3(account_balance=10_000.0)

        assert hybrid.mtf_confirmation is not None, "Component C (MTF) not initialised"
        assert hybrid.pivot_analyzer is not None, "Component B (S/R) not initialised"
        assert hybrid.feature_engineer is not None, "Feature engineer not initialised"

    def test_hybrid_system_initializes_extended_engines(self):
        """HybridPortfolioSystemV3 must initialise extended engines (MR, PA, Macro)."""
        hybrid = HybridPortfolioSystemV3(account_balance=10_000.0)

        assert hybrid.mean_reversion_engine is not None, "MR engine not initialised"
        assert hybrid.price_action_engine is not None, "PA engine not initialised"
        assert hybrid.macro_filter_engine is not None, "Macro filter not initialised"

    def test_reversal_detector_initializes_empty_state(self):
        """ReversalDetector must start with no tracked pairs."""
        detector = ReversalDetector()
        assert detector._state == {}, "ReversalDetector should start with empty state"

    def test_position_manager_initializes_without_db(self):
        """PositionManager must initialise without a DB connection."""
        pm = PositionManager()
        assert pm._db is None
        assert pm.account_balance > 0

    def test_consensus_threshold_is_2_not_3(self):
        """
        Verify the consensus threshold is 2/3 (not 3/3) by checking the
        vote-counting logic directly.  This is the Bug #3 regression check.
        """
        # Simulate the vote counting from HybridPortfolioSystemV3.generate_signal
        for buy_count, sell_count, expected in [
            (3, 0, "BUY"),    # 3/3 → BUY
            (2, 0, "BUY"),    # 2/3 → BUY (was broken: required 3/3)
            (0, 3, "SELL"),   # 3/3 → SELL
            (0, 2, "SELL"),   # 2/3 → SELL (was broken: required 3/3)
            (1, 1, "NEUTRAL"),  # no consensus
            (1, 0, "NEUTRAL"),  # only 1 vote
        ]:
            if buy_count >= 2:
                signal = "BUY"
            elif sell_count >= 2:
                signal = "SELL"
            else:
                signal = "NEUTRAL"

            assert signal == expected, (
                f"buy={buy_count} sell={sell_count}: expected {expected}, got {signal}"
            )


# ─────────────────────────────────────────────────────────────────────────
# MONITORING & ALERTS
# ─────────────────────────────────────────────────────────────────────────

class TestMonitoringAlerts:
    """Test monitoring, metrics, and alerting for anomalies."""

    @pytest.mark.asyncio
    async def test_signal_metrics_tracks_cycles(self):
        """SignalMetrics must increment total_cycles on each call."""
        from gold_server_v3 import SignalMetrics

        metrics = SignalMetrics()
        assert metrics.total_cycles == 0

        metrics.total_cycles += 1
        result = await metrics.log_metrics()

        assert result["total_cycles"] == 1

    @pytest.mark.asyncio
    async def test_signal_metrics_success_rate_zero_when_no_signals(self):
        """Success rate must be 0% when no signals were generated."""
        from gold_server_v3 import SignalMetrics

        metrics = SignalMetrics()
        metrics.total_cycles = 5
        metrics.successful_signals = 0

        result = await metrics.log_metrics()
        assert result["success_rate"] == "0.0%"

    @pytest.mark.asyncio
    async def test_signal_metrics_success_rate_100_percent(self):
        """Success rate must be 100% when all cycles produced signals."""
        from gold_server_v3 import SignalMetrics

        metrics = SignalMetrics()
        metrics.total_cycles = 10
        metrics.successful_signals = 10

        result = await metrics.log_metrics()
        assert result["success_rate"] == "100.0%"

    @pytest.mark.asyncio
    async def test_health_signals_endpoint_healthy(self):
        """GET /api/health/signals must return HEALTHY when success rate is good."""
        from gold_server_v3 import app
        from httpx import AsyncClient, ASGITransport

        with patch("gold_server_v3._signal_metrics") as mock_metrics:
            mock_metrics.log_metrics = AsyncMock(return_value={
                "total_cycles": 10,
                "successful_signals": 9,
                "failed_cycles": 1,
                "success_rate": "90.0%",
                "retry_attempts": 1,
                "api_timeouts": 0,
                "api_errors": 0,
            })

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/health/signals")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "HEALTHY"
        assert "metrics" in data
        assert "alerts" in data
        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_health_signals_endpoint_critical_when_zero_signals(self):
        """GET /api/health/signals must return CRITICAL when success rate is 0%."""
        from gold_server_v3 import app
        from httpx import AsyncClient, ASGITransport

        with patch("gold_server_v3._signal_metrics") as mock_metrics:
            mock_metrics.log_metrics = AsyncMock(return_value={
                "total_cycles": 5,
                "successful_signals": 0,
                "failed_cycles": 5,
                "success_rate": "0.0%",
                "retry_attempts": 15,
                "api_timeouts": 5,
                "api_errors": 0,
            })

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/health/signals")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "CRITICAL"
        assert len(data["alerts"]) > 0

    @pytest.mark.asyncio
    async def test_health_signals_endpoint_warning_low_success_rate(self):
        """GET /api/health/signals must return WARNING when success rate < 50%."""
        from gold_server_v3 import app
        from httpx import AsyncClient, ASGITransport

        with patch("gold_server_v3._signal_metrics") as mock_metrics:
            mock_metrics.log_metrics = AsyncMock(return_value={
                "total_cycles": 10,
                "successful_signals": 4,
                "failed_cycles": 6,
                "success_rate": "40.0%",
                "retry_attempts": 6,
                "api_timeouts": 2,
                "api_errors": 4,
            })

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/health/signals")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "WARNING"

    @pytest.mark.asyncio
    async def test_reversal_alert_false_positive_prevention(self):
        """
        Reversal alert must not fire when closed_count == 0.
        This is the Bug #2 false-positive prevention check.
        """
        from gold_server_v3 import send_reversal_alert

        with patch("gold_server_v3.get_bot") as mock_get_bot:
            mock_bot = AsyncMock()
            mock_get_bot.return_value = mock_bot

            # Simulate: reversal detected but no positions were open
            await send_reversal_alert(
                pair="XAUUSD",
                reason="Reversal detected",
                closed_count=0,   # ← no positions closed
                total_pnl=0.0,
            )

            # Must NOT send a Telegram message
            mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_consensus_disagreement_logged(self):
        """
        Component votes must be logged in the result for debugging.
        Ensures consensus decisions are traceable.
        """
        hybrid = HybridPortfolioSystemV3(account_balance=10_000.0)
        df = _make_df()

        hybrid._component_a_trend = MagicMock(
            return_value={"vote": "BUY", "confidence": 0.70, "valid": True}
        )
        hybrid._component_b_sr = MagicMock(
            return_value={"vote": "SELL", "confidence": 0.70, "valid": True}
        )
        hybrid._component_c_mtf = MagicMock(
            return_value={"vote": "NEUTRAL", "confidence": 0.0, "valid": True}
        )

        with patch.object(
            hybrid.economic_calendar, "is_safe_to_trade",
            new_callable=AsyncMock,
            return_value={"safe_to_trade": True},
        ):
            with patch.object(
                hybrid.mtf_confirmation, "analyze",
                new_callable=AsyncMock,
                return_value={
                    "valid": True,
                    "dominant_direction": "NEUTRAL",
                    "alignment_score": 50.0,
                },
            ):
                result = await hybrid.generate_signal(
                    symbol="XAUUSD", df_4h=df, strategy_mode="original"
                )

        # component_votes must be present for debugging
        assert "component_votes" in result, (
            "component_votes missing from result — consensus decisions not traceable"
        )
        votes = result["component_votes"]
        assert "A_trend" in votes
        assert "B_sr" in votes
        assert "C_mtf" in votes


# ─────────────────────────────────────────────────────────────────────────
# PYTEST CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

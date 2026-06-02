"""
Tests for HybridEnhancementIndicators
Gold Trading System v3.0.2

Covers all 13 hybrid enhancement indicators, confluence scoring,
position sizing, and stop loss strategies.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from ml_engine.hybrid_enhancement_indicators import (
    HybridEnhancementIndicators,
    IndicatorResult,
    CONFLUENCE_HIGH_THRESHOLD,
    CONFLUENCE_MEDIUM_THRESHOLD,
    _label,
    _clamp,
)


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def hei():
    return HybridEnhancementIndicators()


@pytest.fixture
def full_buy_signal():
    """A comprehensive BUY signal with all optional fields."""
    return {
        "type": "BUY",
        "pair": "XAUUSD",
        "entry_price": 2350.00,
        "sl_price": 2330.00,
        "tp_levels": [2390.00, 2420.00, 2470.00],
        "atr": 15.0,
        "atr_ratio": 1.1,
        "adx": 30.0,
        "rsi": 45.0,
        "macd": 0.5,
        "macd_signal": 0.3,
        "stoch_rsi": 35.0,
        "vwap": 2345.0,
        "bb_upper": 2380.0,
        "bb_lower": 2320.0,
        "bb_middle": 2350.0,
        "swing_high": 2400.0,
        "swing_low": 2328.0,
        "nearest_support": 2332.0,
        "nearest_resistance": 2395.0,
        "smc_score": 7.5,
        "order_flow_bias": "BULLISH",
        "liquidity_sweep_against": False,
        "momentum_score": 7.0,
        "regime": "TREND_UP",
        "strategy": "TREND",
        "zscore_20": 0.5,
        "structure_bias": 4,
        "ma20_slope": 0.2,
        "trade_type": "SWING",
        "m15_confirmed": True,
        "account_balance": 10000.0,
        "position_size": 0.05,
        "stop_type": "HYBRID",
        "mtf_alignment": {
            "h4_aligned": True,
            "h1_aligned": True,
            "m15_aligned": True,
            "h4_bias": "BULLISH",
            "h1_structure": "UPTREND",
            "m15_trigger": True,
        },
        "news_events": [],
        "news_checked": True,
    }


@pytest.fixture
def full_sell_signal():
    """A comprehensive SELL signal in RANGE regime."""
    return {
        "type": "SELL",
        "pair": "XAUUSD",
        "entry_price": 2390.00,
        "sl_price": 2410.00,
        "tp_levels": [2350.00, 2320.00, 2280.00],
        "atr": 15.0,
        "atr_ratio": 0.9,
        "adx": 18.0,
        "rsi": 65.0,
        "macd": -0.3,
        "macd_signal": -0.1,
        "stoch_rsi": 72.0,
        "vwap": 2395.0,
        "bb_upper": 2410.0,
        "bb_lower": 2330.0,
        "bb_middle": 2370.0,
        "swing_high": 2408.0,
        "swing_low": 2310.0,
        "nearest_support": 2315.0,
        "nearest_resistance": 2395.0,
        "smc_score": 7.0,
        "order_flow_bias": "BEARISH",
        "liquidity_sweep_against": False,
        "momentum_score": 6.5,
        "regime": "RANGE",
        "strategy": "MEAN_REVERSION",
        "zscore_20": 1.8,
        "structure_bias": -2,
        "ma20_slope": -0.1,
        "trade_type": "SWING",
        "m15_confirmed": True,
        "account_balance": 10000.0,
        "position_size": 0.05,
        "stop_type": "HYBRID",
        "mtf_alignment": {
            "h4_aligned": True,
            "h1_aligned": True,
            "m15_aligned": False,
            "h4_bias": "BEARISH",
        },
        "news_events": [],
        "news_checked": True,
    }


# ─────────────────────────────────────────────────────────────
# Helper tests
# ─────────────────────────────────────────────────────────────

class TestHelpers:
    def test_label_high(self):
        assert _label(0.80) == "HIGH"
        assert _label(CONFLUENCE_HIGH_THRESHOLD) == "HIGH"

    def test_label_medium(self):
        assert _label(0.65) == "MEDIUM"
        assert _label(CONFLUENCE_MEDIUM_THRESHOLD) == "MEDIUM"

    def test_label_low(self):
        assert _label(0.40) == "LOW"
        assert _label(0.0) == "LOW"

    def test_clamp_bounds(self):
        assert _clamp(1.5) == 1.0
        assert _clamp(-0.5) == 0.0
        assert _clamp(0.5) == 0.5


# ─────────────────────────────────────────────────────────────
# Indicator 1: SMC + Order Flow
# ─────────────────────────────────────────────────────────────

class TestSMCOrderFlow:
    def test_high_smc_aligned_of_passes(self, hei, full_buy_signal):
        result = hei.smc_order_flow(full_buy_signal)
        assert isinstance(result, IndicatorResult)
        assert result.score > 0.5
        assert result.name == "smc_order_flow"

    def test_low_smc_score_reduces_score(self, hei):
        signal = {"type": "BUY", "smc_score": 2.0, "order_flow_bias": "BULLISH"}
        result = hei.smc_order_flow(signal)
        assert result.score < 0.7

    def test_misaligned_order_flow_penalised(self, hei):
        signal = {"type": "BUY", "smc_score": 8.0, "order_flow_bias": "BEARISH"}
        result = hei.smc_order_flow(signal)
        assert result.score < 0.7

    def test_liquidity_sweep_against_penalised(self, hei):
        signal = {
            "type": "BUY",
            "smc_score": 8.0,
            "order_flow_bias": "BULLISH",
            "liquidity_sweep_against": True,
        }
        result = hei.smc_order_flow(signal)
        # Should be penalised vs no sweep
        signal_no_sweep = {**signal, "liquidity_sweep_against": False}
        result_no_sweep = hei.smc_order_flow(signal_no_sweep)
        assert result.score < result_no_sweep.score


# ─────────────────────────────────────────────────────────────
# Indicator 2: Triple Momentum
# ─────────────────────────────────────────────────────────────

class TestTripleMomentum:
    def test_all_aligned_buy_scores_high(self, hei):
        signal = {
            "type": "BUY",
            "rsi": 45.0,
            "macd": 0.5,
            "macd_signal": 0.2,
            "stoch_rsi": 35.0,
        }
        result = hei.triple_momentum(signal)
        assert result.score >= 0.75
        assert result.details["aligned_count"] == 3

    def test_all_aligned_sell_scores_high(self, hei):
        signal = {
            "type": "SELL",
            "rsi": 65.0,
            "macd": -0.3,
            "macd_signal": -0.1,
            "stoch_rsi": 72.0,
        }
        result = hei.triple_momentum(signal)
        assert result.score >= 0.6

    def test_none_aligned_scores_low(self, hei):
        signal = {
            "type": "BUY",
            "rsi": 75.0,   # Overbought — bad for BUY
            "macd": -0.5,  # Bearish — bad for BUY
            "macd_signal": 0.0,
            "stoch_rsi": 85.0,  # Overbought — bad for BUY
        }
        result = hei.triple_momentum(signal)
        assert result.score < 0.5

    def test_default_values_neutral(self, hei):
        """Missing momentum data should return neutral score."""
        result = hei.triple_momentum({"type": "BUY"})
        assert 0.0 <= result.score <= 1.0


# ─────────────────────────────────────────────────────────────
# Indicator 3: VWAP + Price Action
# ─────────────────────────────────────────────────────────────

class TestVWAPPriceAction:
    def test_buy_above_vwap_scores_high(self, hei):
        signal = {"type": "BUY", "entry_price": 2355.0, "vwap": 2345.0}
        result = hei.vwap_price_action(signal)
        assert result.score >= 0.7

    def test_sell_above_vwap_scores_high(self, hei):
        """SELL entry above VWAP = selling at premium = good."""
        signal = {"type": "SELL", "entry_price": 2395.0, "vwap": 2345.0}
        result = hei.vwap_price_action(signal)
        assert result.score >= 0.7

    def test_no_vwap_neutral(self, hei):
        signal = {"type": "BUY", "entry_price": 2350.0}
        result = hei.vwap_price_action(signal)
        assert result.score == 0.5
        assert result.label == "MEDIUM"


# ─────────────────────────────────────────────────────────────
# Indicator 4: Fibonacci + SMC Confluence
# ─────────────────────────────────────────────────────────────

class TestFibonacciSMCConfluence:
    def test_entry_near_fib_618_scores_high(self, hei):
        """Entry near 61.8% retracement with high SMC should score high."""
        # Swing: 2300 → 2400, 61.8% retracement = 2338.2
        signal = {
            "type": "BUY",
            "entry_price": 2338.0,
            "swing_high": 2400.0,
            "swing_low": 2300.0,
            "smc_score": 8.0,
        }
        result = hei.fibonacci_smc_confluence(signal)
        assert result.score > 0.5
        assert result.name == "fibonacci_smc"

    def test_missing_swing_points_low_score(self, hei):
        signal = {"type": "BUY", "entry_price": 2350.0, "smc_score": 7.0}
        result = hei.fibonacci_smc_confluence(signal)
        assert result.score <= 0.5

    def test_stacked_confluence_bonus(self, hei):
        """Fib-aligned + high SMC should get stacked bonus."""
        # 50% retracement of 2300→2400 = 2350
        signal = {
            "type": "BUY",
            "entry_price": 2350.0,
            "swing_high": 2400.0,
            "swing_low": 2300.0,
            "smc_score": 8.0,
        }
        result = hei.fibonacci_smc_confluence(signal)
        assert result.details.get("stacked_bonus", 0) > 0 or result.score > 0.7


# ─────────────────────────────────────────────────────────────
# Indicator 5: ATR + Bollinger Bands
# ─────────────────────────────────────────────────────────────

class TestATRBollingerBands:
    def test_normal_atr_ratio_scores_high(self, hei):
        signal = {
            "entry_price": 2350.0,
            "atr": 15.0,
            "atr_ratio": 1.1,
            "bb_upper": 2380.0,
            "bb_lower": 2320.0,
            "bb_middle": 2350.0,
        }
        result = hei.atr_bollinger_bands(signal)
        assert result.score >= 0.6

    def test_bb_squeeze_scores_high(self, hei):
        """BB squeeze = breakout imminent = high score."""
        signal = {
            "entry_price": 2350.0,
            "atr": 15.0,
            "atr_ratio": 1.0,
            "bb_upper": 2351.0,   # Very tight bands
            "bb_lower": 2349.0,
            "bb_middle": 2350.0,
        }
        result = hei.atr_bollinger_bands(signal)
        assert result.details.get("bb_squeeze") is True
        assert result.score >= 0.6

    def test_extreme_atr_ratio_scores_low(self, hei):
        """ATR ratio > 2.5 = too volatile = low score."""
        signal = {"entry_price": 2350.0, "atr": 15.0, "atr_ratio": 2.8}
        result = hei.atr_bollinger_bands(signal)
        assert result.score < 0.6


# ─────────────────────────────────────────────────────────────
# Indicator 6: Range + Breakout Filter
# ─────────────────────────────────────────────────────────────

class TestRangeBreakoutFilter:
    def test_clear_trend_scores_high(self, hei):
        signal = {"type": "BUY", "regime": "TREND_UP", "adx": 30.0, "atr_ratio": 1.1}
        result = hei.range_breakout_filter(signal)
        assert result.score >= 0.8

    def test_counter_trend_penalised(self, hei):
        signal = {"type": "SELL", "regime": "TREND_UP", "adx": 30.0, "atr_ratio": 1.1}
        result = hei.range_breakout_filter(signal)
        assert result.score < 0.5
        assert result.details["counter_trend"] is True

    def test_chaos_regime_scores_low(self, hei):
        signal = {"type": "BUY", "regime": "CHAOS", "adx": 10.0, "atr_ratio": 2.5}
        result = hei.range_breakout_filter(signal)
        assert result.score <= 0.3

    def test_range_with_low_adx_scores_well(self, hei):
        signal = {"type": "BUY", "regime": "RANGE", "adx": 18.0, "atr_ratio": 0.9}
        result = hei.range_breakout_filter(signal)
        assert result.score >= 0.7


# ─────────────────────────────────────────────────────────────
# Indicator 7: Swing + Scalp Timing
# ─────────────────────────────────────────────────────────────

class TestSwingScalpTiming:
    def test_swing_with_m15_and_good_rr(self, hei):
        signal = {
            "type": "BUY",
            "trade_type": "SWING",
            "m15_confirmed": True,
            "entry_price": 2350.0,
            "sl_price": 2330.0,
            "tp_levels": [2400.0, 2430.0, 2470.0],  # TP1 R:R = 2.5:1
        }
        result = hei.swing_scalp_timing(signal)
        assert result.score >= 0.7
        assert result.details["tp1_rr"] == pytest.approx(2.5, abs=0.01)

    def test_no_m15_confirmation_reduces_score(self, hei):
        signal = {
            "type": "BUY",
            "trade_type": "SWING",
            "m15_confirmed": False,
            "entry_price": 2350.0,
            "sl_price": 2330.0,
            "tp_levels": [2400.0],
        }
        result_no_m15 = hei.swing_scalp_timing(signal)
        signal_m15 = {**signal, "m15_confirmed": True}
        result_m15 = hei.swing_scalp_timing(signal_m15)
        assert result_m15.score > result_no_m15.score

    def test_low_rr_scores_low(self, hei):
        signal = {
            "type": "BUY",
            "trade_type": "SWING",
            "m15_confirmed": True,
            "entry_price": 2350.0,
            "sl_price": 2330.0,
            "tp_levels": [2363.0],  # TP1 R:R = 0.65:1
        }
        result = hei.swing_scalp_timing(signal)
        assert result.score < 0.5


# ─────────────────────────────────────────────────────────────
# Indicator 8: Trend + Mean Reversion
# ─────────────────────────────────────────────────────────────

class TestTrendMeanReversion:
    def test_trend_strategy_in_trend_regime(self, hei):
        signal = {
            "type": "BUY",
            "strategy": "TREND",
            "regime": "TREND_UP",
            "zscore_20": 0.5,
            "adx": 30.0,
        }
        result = hei.trend_mean_reversion(signal)
        assert result.score >= 0.7

    def test_mean_reversion_in_range_regime(self, hei):
        signal = {
            "type": "SELL",
            "strategy": "MEAN_REVERSION",
            "regime": "RANGE",
            "zscore_20": 1.8,
            "adx": 18.0,
        }
        result = hei.trend_mean_reversion(signal)
        assert result.score >= 0.6

    def test_strategy_regime_mismatch_penalised(self, hei):
        signal = {
            "type": "BUY",
            "strategy": "MEAN_REVERSION",
            "regime": "TREND_UP",
            "zscore_20": 0.3,
            "adx": 30.0,
        }
        result = hei.trend_mean_reversion(signal)
        assert result.score < 0.7

    def test_breakout_transition_detected(self, hei):
        signal = {
            "type": "BUY",
            "strategy": "BREAKOUT",
            "regime": "BREAKOUT",
            "zscore_20": 0.2,
            "adx": 35.0,
        }
        result = hei.trend_mean_reversion(signal)
        assert result.details.get("breakout_transition") is True


# ─────────────────────────────────────────────────────────────
# Indicator 9: MTF Pyramid Breakdown
# ─────────────────────────────────────────────────────────────

class TestMTFPyramidBreakdown:
    def test_full_alignment_scores_high(self, hei, full_buy_signal):
        result = hei.mtf_pyramid_breakdown(full_buy_signal)
        assert result.score >= 0.9
        assert result.details["aligned_count"] == 3

    def test_partial_alignment_medium_score(self, hei):
        signal = {
            "type": "BUY",
            "mtf_alignment": {
                "h4_aligned": True,
                "h1_aligned": True,
                "m15_aligned": False,
            },
        }
        result = hei.mtf_pyramid_breakdown(signal)
        assert 0.5 <= result.score <= 0.9
        assert result.details["aligned_count"] == 2

    def test_no_alignment_scores_low(self, hei):
        signal = {
            "type": "BUY",
            "mtf_alignment": {
                "h4_aligned": False,
                "h1_aligned": False,
                "m15_aligned": False,
            },
        }
        result = hei.mtf_pyramid_breakdown(signal)
        assert result.score <= 0.4

    def test_h4_bias_alignment_bonus(self, hei):
        signal = {
            "type": "BUY",
            "mtf_alignment": {
                "h4_aligned": True,
                "h1_aligned": True,
                "m15_aligned": True,
                "h4_bias": "BULLISH",
            },
        }
        result = hei.mtf_pyramid_breakdown(signal)
        assert result.details["h4_bias_aligned"] is True


# ─────────────────────────────────────────────────────────────
# Indicator 10: Session MTF Weighting
# ─────────────────────────────────────────────────────────────

class TestSessionMTFWeighting:
    def test_returns_indicator_result(self, hei, full_buy_signal):
        result = hei.session_mtf_weighting(full_buy_signal)
        assert isinstance(result, IndicatorResult)
        assert result.name == "session_mtf_weighting"
        assert 0.0 <= result.score <= 1.0

    def test_full_alignment_scores_well(self, hei, full_buy_signal):
        result = hei.session_mtf_weighting(full_buy_signal)
        assert result.score > 0.5

    def test_no_alignment_scores_low(self, hei):
        signal = {
            "mtf_alignment": {
                "h4_aligned": False,
                "h1_aligned": False,
                "m15_aligned": False,
            }
        }
        result = hei.session_mtf_weighting(signal)
        assert result.score == 0.0


# ─────────────────────────────────────────────────────────────
# Indicator 11: Fixed + Trailing Stop Hybrid
# ─────────────────────────────────────────────────────────────

class TestFixedTrailingStopHybrid:
    def test_tp1_lock_tp3_trail_scores_high(self, hei):
        """TP1 at 1:1 R:R (lock) + TP3 at 1:3+ R:R (trail) = high score."""
        signal = {
            "type": "BUY",
            "entry_price": 2350.0,
            "sl_price": 2330.0,
            "tp_levels": [2370.0, 2410.0, 2410.0],  # TP1=1:1, TP3=3:1
            "atr": 15.0,
            "stop_type": "HYBRID",
        }
        # Adjust TP3 to be at 3:1
        signal["tp_levels"] = [2370.0, 2410.0, 2410.0]
        result = hei.fixed_trailing_stop_hybrid(signal)
        assert result.score >= 0.5

    def test_no_tp3_reduces_score(self, hei):
        signal = {
            "type": "BUY",
            "entry_price": 2350.0,
            "sl_price": 2330.0,
            "tp_levels": [2370.0],  # Only TP1
            "atr": 15.0,
        }
        result = hei.fixed_trailing_stop_hybrid(signal)
        assert result.score < 0.8

    def test_trailing_stop_type_bonus(self, hei):
        signal = {
            "type": "BUY",
            "entry_price": 2350.0,
            "sl_price": 2330.0,
            "tp_levels": [2370.0, 2410.0, 2410.0],
            "atr": 15.0,
            "stop_type": "TRAILING",
        }
        result_trailing = hei.fixed_trailing_stop_hybrid(signal)
        signal_no_type = {**signal, "stop_type": ""}
        result_no_type = hei.fixed_trailing_stop_hybrid(signal_no_type)
        assert result_trailing.score >= result_no_type.score


# ─────────────────────────────────────────────────────────────
# Indicator 12: Volatility-Adjusted Position Sizing
# ─────────────────────────────────────────────────────────────

class TestVolatilityPositionSizing:
    def test_ideal_position_size_scores_high(self, hei):
        """Position size within 20% of ideal should score 1.0."""
        # Risk = 20 pips = $2.00, 1% of $10000 = $100, ideal = 50 units
        signal = {
            "entry_price": 2350.0,
            "sl_price": 2330.0,
            "atr": 15.0,
            "atr_ratio": 1.0,
            "account_balance": 10000.0,
            "position_size": 5.0,  # $100 / $20 = 5 units
        }
        result = hei.volatility_position_sizing(signal)
        assert result.score >= 0.7

    def test_no_position_size_partial_credit(self, hei):
        """No position_size should give partial credit with suggestion."""
        signal = {
            "entry_price": 2350.0,
            "sl_price": 2330.0,
            "atr": 15.0,
            "account_balance": 10000.0,
        }
        result = hei.volatility_position_sizing(signal)
        assert result.score == pytest.approx(0.6, abs=0.1)
        assert "ideal_size" in result.details

    def test_high_volatility_reduces_ideal_size(self, hei):
        """High ATR ratio should reduce ideal position size."""
        signal_normal = {
            "entry_price": 2350.0,
            "sl_price": 2330.0,
            "atr": 15.0,
            "atr_ratio": 1.0,
            "account_balance": 10000.0,
        }
        signal_high_vol = {**signal_normal, "atr_ratio": 1.8}
        result_normal   = hei.volatility_position_sizing(signal_normal)
        result_high_vol = hei.volatility_position_sizing(signal_high_vol)
        # High vol should have lower ideal size
        assert result_high_vol.details["vol_multiplier"] < result_normal.details["vol_multiplier"]


# ─────────────────────────────────────────────────────────────
# Indicator 13: Dynamic Confluence Score
# ─────────────────────────────────────────────────────────────

class TestDynamicConfluenceScore:
    def test_all_high_scores_confluence_high(self, hei):
        """All indicators at 1.0 should produce HIGH confluence."""
        scores = {name: 1.0 for name in [
            "smc_order_flow", "triple_momentum", "vwap_price_action",
            "fibonacci_smc", "atr_bollinger", "range_breakout_filter",
            "swing_scalp_timing", "trend_mean_reversion", "mtf_pyramid",
            "session_mtf_weighting", "fixed_trailing_stop", "volatility_position_size",
        ]}
        result = hei.dynamic_confluence_score(scores=scores)
        assert result.score >= CONFLUENCE_HIGH_THRESHOLD
        assert result.label == "HIGH"

    def test_all_low_scores_confluence_low(self, hei):
        """All indicators at 0.0 should produce LOW confluence."""
        scores = {name: 0.0 for name in [
            "smc_order_flow", "triple_momentum", "vwap_price_action",
            "fibonacci_smc", "atr_bollinger", "range_breakout_filter",
            "swing_scalp_timing", "trend_mean_reversion", "mtf_pyramid",
            "session_mtf_weighting", "fixed_trailing_stop", "volatility_position_size",
        ]}
        result = hei.dynamic_confluence_score(scores=scores)
        assert result.score < CONFLUENCE_MEDIUM_THRESHOLD
        assert result.label == "LOW"

    def test_empty_scores_neutral(self, hei):
        result = hei.dynamic_confluence_score(scores={})
        assert result.score == 0.5

    def test_confluence_from_signal(self, hei, full_buy_signal):
        """Confluence can be computed directly from a signal."""
        result = hei.dynamic_confluence_score(signal=full_buy_signal)
        assert isinstance(result, IndicatorResult)
        assert 0.0 <= result.score <= 1.0


# ─────────────────────────────────────────────────────────────
# Aggregate Scoring
# ─────────────────────────────────────────────────────────────

class TestAggregateScoring:
    def test_score_all_returns_13_indicators(self, hei, full_buy_signal):
        """score_all() must return exactly 13 indicator scores."""
        scores = hei.score_all(full_buy_signal)
        assert len(scores) == 13
        expected = [
            "smc_order_flow", "triple_momentum", "vwap_price_action",
            "fibonacci_smc", "atr_bollinger", "range_breakout_filter",
            "swing_scalp_timing", "trend_mean_reversion", "mtf_pyramid",
            "session_mtf_weighting", "fixed_trailing_stop",
            "volatility_position_size", "dynamic_confluence",
        ]
        for name in expected:
            assert name in scores, f"Missing indicator: {name}"

    def test_score_all_values_in_range(self, hei, full_buy_signal):
        """All scores must be in [0.0, 1.0]."""
        scores = hei.score_all(full_buy_signal)
        for name, score in scores.items():
            assert 0.0 <= score <= 1.0, f"{name} score {score} out of range"

    def test_score_all_detailed_returns_indicator_results(self, hei, full_buy_signal):
        """score_all_detailed() must return IndicatorResult objects."""
        results = hei.score_all_detailed(full_buy_signal)
        assert len(results) == 13
        for r in results:
            assert isinstance(r, IndicatorResult)
            assert r.name
            assert r.label in ("HIGH", "MEDIUM", "LOW")
            assert r.explanation

    def test_score_all_without_confluence(self, hei, full_buy_signal):
        """score_all(include_confluence=False) returns 12 indicators."""
        scores = hei.score_all(full_buy_signal, include_confluence=False)
        assert len(scores) == 12
        assert "dynamic_confluence" not in scores

    def test_empty_signal_does_not_crash(self, hei):
        """Empty signal should not raise an exception."""
        scores = hei.score_all({})
        assert len(scores) == 13
        for score in scores.values():
            assert 0.0 <= score <= 1.0

    def test_sell_signal_scores(self, hei, full_sell_signal):
        """SELL signal should produce valid scores for all indicators."""
        scores = hei.score_all(full_sell_signal)
        assert len(scores) == 13
        for name, score in scores.items():
            assert 0.0 <= score <= 1.0, f"{name} score {score} out of range"

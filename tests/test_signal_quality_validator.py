"""
Tests for SignalQualityValidator
Gold Trading System v3.0.2

Covers all 10 quality checks and the composite scoring system.
"""

import sys
import os
import pytest
from datetime import datetime, timezone, timedelta

# Ensure backend is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from ml_engine.signal_quality_validator import (
    SignalQualityValidator,
    signal_quality_validator,
    ValidationResult,
    QualityCheck,
    _grade,
    _session_expiry,
    _utc_now,
    REGIME_TREND_UP,
    REGIME_TREND_DOWN,
    REGIME_RANGE,
    REGIME_BREAKOUT,
    RR_MINIMUM_SWING,
    ENTRY_BAND_PIPS,
    PIP_SIZE_GOLD,
    CONFIDENCE_MEDIUM,
    SESSION_LONDON_OPEN,
    SESSION_NY_OPEN,
    SESSION_NY_CLOSE,
)


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def validator():
    return SignalQualityValidator(min_quality_score=55.0)


@pytest.fixture
def good_buy_signal():
    """A well-formed BUY signal that should pass most checks."""
    return {
        "type": "BUY",
        "pair": "XAUUSD",
        "entry_price": 2350.00,
        "entry_band_low": 2349.50,
        "entry_band_high": 2350.50,
        "sl_price": 2330.00,
        "tp_levels": [2390.00, 2420.00, 2460.00],
        "confidence": 78.0,
        "regime": "TREND_UP",
        "adx": 30.0,
        "atr": 15.0,
        "atr_ratio": 1.1,
        "swing_high": 2400.00,
        "swing_low": 2328.00,
        "nearest_support": 2332.00,
        "nearest_resistance": 2395.00,
        "smc_score": 7.5,
        "momentum_score": 7.0,
        "structure_bias": 4,
        "ma20_slope": 0.2,
        "trade_type": "SWING",
        "mtf_alignment": {
            "h4_aligned": True,
            "h1_aligned": True,
            "m15_aligned": True,
            "h4_bias": "BULLISH",
        },
        "news_events": [],
        "news_checked": True,
        "expiry": "Valid until 22:00 UTC (NY Close)",
    }


@pytest.fixture
def good_sell_signal():
    """A well-formed SELL signal in RANGE regime at resistance."""
    return {
        "type": "SELL",
        "pair": "XAUUSD",
        "entry_price": 2390.00,
        "entry_band_low": 2389.50,
        "entry_band_high": 2390.50,
        "sl_price": 2410.00,
        "tp_levels": [2350.00, 2320.00, 2290.00],
        "confidence": 72.0,
        "regime": "RANGE",
        "adx": 18.0,
        "atr": 15.0,
        "atr_ratio": 0.9,
        "swing_high": 2408.00,
        "swing_low": 2310.00,
        "nearest_support": 2315.00,
        "nearest_resistance": 2395.00,
        "smc_score": 7.0,
        "momentum_score": 6.5,
        "structure_bias": -2,
        "ma20_slope": -0.1,
        "trade_type": "SWING",
        "mtf_alignment": {
            "h4_aligned": True,
            "h1_aligned": True,
            "m15_aligned": False,
            "h4_bias": "BEARISH",
        },
        "news_events": [],
        "news_checked": True,
        "expiry": "Valid until 22:00 UTC (NY Close)",
    }


@pytest.fixture
def poor_signal():
    """A poorly-formed signal that should fail multiple checks."""
    return {
        "type": "SELL",
        "pair": "XAUUSD",
        "entry_price": 2320.00,   # Near support — wrong for RANGE SELL
        "sl_price": 2330.00,
        "tp_levels": [2310.00],   # Only 1:0.5 R:R
        "confidence": 75.0,       # Static confidence
        "regime": "RANGE",
        "adx": 15.0,
        "atr": 15.0,
        "atr_ratio": 1.0,
        "nearest_support": 2315.00,
        "nearest_resistance": 2395.00,
        "smc_score": 3.0,
        "momentum_score": 3.0,
        "trade_type": "SWING",
        "mtf_alignment": {},
        "news_events": ["NFP Release", "JOLTS Data"],
    }


# ─────────────────────────────────────────────────────────────
# Helper tests
# ─────────────────────────────────────────────────────────────

class TestHelpers:
    def test_grade_a(self):
        assert _grade(90) == "A"
        assert _grade(85) == "A"

    def test_grade_b(self):
        assert _grade(80) == "B"
        assert _grade(70) == "B"

    def test_grade_c(self):
        assert _grade(65) == "C"
        assert _grade(55) == "C"

    def test_grade_d(self):
        assert _grade(50) == "D"
        assert _grade(40) == "D"

    def test_grade_f(self):
        assert _grade(39) == "F"
        assert _grade(0) == "F"

    def test_session_expiry_london(self):
        # 09:00 UTC = London session → expires at NY open (13:00)
        dt = datetime(2024, 1, 15, 9, 0, tzinfo=timezone.utc)
        expiry = _session_expiry(dt)
        assert "13:00" in expiry
        assert "NY Open" in expiry

    def test_session_expiry_ny(self):
        # 15:00 UTC = NY session → expires at NY close (22:00)
        dt = datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc)
        expiry = _session_expiry(dt)
        assert "22:00" in expiry
        assert "NY Close" in expiry

    def test_session_expiry_dead_zone(self):
        # 02:00 UTC = dead zone → expires at London open (07:00)
        dt = datetime(2024, 1, 15, 2, 0, tzinfo=timezone.utc)
        expiry = _session_expiry(dt)
        assert "07:00" in expiry
        assert "London Open" in expiry


# ─────────────────────────────────────────────────────────────
# Check 1: R:R Validation
# ─────────────────────────────────────────────────────────────

class TestRRValidation:
    def test_rr_minimum_swing_passes(self, validator):
        """R:R ≥ 2:1 for swing trades should pass."""
        signal = {
            "type": "BUY",
            "entry_price": 2350.0,
            "sl_price": 2330.0,
            "tp_levels": [2390.0, 2420.0],  # TP1 R:R = 2:1
            "trade_type": "SWING",
        }
        check = validator._check_rr_validation(signal)
        assert check.passed is True
        assert check.score >= 0.7
        assert check.details["tp1_rr"] == pytest.approx(2.0, abs=0.01)

    def test_rr_below_minimum_fails(self, validator):
        """R:R 1:1.3 should fail for swing trades."""
        signal = {
            "type": "BUY",
            "entry_price": 2350.0,
            "sl_price": 2330.0,
            "tp_levels": [2376.0],  # TP1 R:R = 1.3:1
            "trade_type": "SWING",
        }
        check = validator._check_rr_validation(signal)
        assert check.passed is False
        assert check.details["tp1_rr"] == pytest.approx(1.3, abs=0.01)
        assert len(check.suggestions) > 0

    def test_rr_excellent_scores_high(self, validator):
        """R:R ≥ 3:1 should score 1.0."""
        signal = {
            "type": "BUY",
            "entry_price": 2350.0,
            "sl_price": 2330.0,
            "tp_levels": [2410.0],  # TP1 R:R = 3:1
            "trade_type": "SWING",
        }
        check = validator._check_rr_validation(signal)
        assert check.passed is True
        assert check.score == 1.0

    def test_rr_scalp_lower_minimum(self, validator):
        """Scalp trades accept R:R ≥ 1.3:1."""
        signal = {
            "type": "BUY",
            "entry_price": 2350.0,
            "sl_price": 2340.0,
            "tp_levels": [2363.0],  # TP1 R:R = 1.3:1
            "trade_type": "SCALP",
        }
        check = validator._check_rr_validation(signal)
        assert check.passed is True

    def test_rr_sell_signal(self, validator):
        """R:R validation works for SELL signals."""
        signal = {
            "type": "SELL",
            "entry_price": 2390.0,
            "sl_price": 2410.0,
            "tp_levels": [2350.0, 2320.0],  # TP1 R:R = 2:1
            "trade_type": "SWING",
        }
        check = validator._check_rr_validation(signal)
        assert check.passed is True
        assert check.details["tp1_rr"] == pytest.approx(2.0, abs=0.01)

    def test_rr_missing_data_fails(self, validator):
        """Missing entry/SL/TP should fail gracefully."""
        check = validator._check_rr_validation({})
        assert check.passed is False
        assert check.score == 0.0


# ─────────────────────────────────────────────────────────────
# Check 2: Regime Classification
# ─────────────────────────────────────────────────────────────

class TestRegimeClassification:
    def test_trend_up_with_high_adx(self, validator):
        """ADX > 25 + positive slope = TREND_UP."""
        signal = {
            "adx": 30.0,
            "atr_ratio": 1.1,
            "structure_bias": 5,
            "ma20_slope": 0.3,
            "zscore_20": 0.5,
        }
        regime = validator._classify_regime(signal)
        assert regime == REGIME_TREND_UP

    def test_trend_down_with_high_adx(self, validator):
        """ADX > 25 + negative slope = TREND_DOWN."""
        signal = {
            "adx": 28.0,
            "atr_ratio": 1.0,
            "structure_bias": -5,
            "ma20_slope": -0.3,
            "zscore_20": -0.5,
        }
        regime = validator._classify_regime(signal)
        assert regime == REGIME_TREND_DOWN

    def test_range_with_low_adx(self, validator):
        """ADX < 25 = RANGE."""
        signal = {
            "adx": 18.0,
            "atr_ratio": 0.9,
            "structure_bias": 0,
            "ma20_slope": 0.0,
            "zscore_20": 0.2,
        }
        regime = validator._classify_regime(signal)
        assert regime == REGIME_RANGE

    def test_range_sell_at_support_fails(self, validator):
        """RANGE SELL at support (bottom of range) should fail."""
        signal = {
            "type": "SELL",
            "regime": "RANGE",
            "entry_price": 2320.0,
            "nearest_support": 2315.0,
            "nearest_resistance": 2395.0,
            "adx": 18.0,
            "atr_ratio": 0.9,
        }
        check = validator._check_regime_classification(signal)
        assert check.passed is False
        assert check.score < 0.5
        assert any("resistance" in s.lower() for s in check.suggestions)

    def test_range_sell_at_resistance_passes(self, validator):
        """RANGE SELL at resistance (top of range) should pass."""
        signal = {
            "type": "SELL",
            "regime": "RANGE",
            "entry_price": 2390.0,   # 94% into range
            "nearest_support": 2315.0,
            "nearest_resistance": 2395.0,
            "adx": 18.0,
            "atr_ratio": 0.9,
        }
        check = validator._check_regime_classification(signal)
        assert check.passed is True

    def test_no_regime_returns_inferred(self, validator):
        """Missing regime label returns inferred regime."""
        signal = {
            "type": "BUY",
            "regime": "",
            "adx": 30.0,
            "atr_ratio": 1.1,
            "structure_bias": 4,
            "ma20_slope": 0.2,
        }
        check = validator._check_regime_classification(signal)
        assert check.passed is False
        assert "inferred_regime" in check.details


# ─────────────────────────────────────────────────────────────
# Check 3: Entry Band Validation
# ─────────────────────────────────────────────────────────────

class TestEntryBandValidation:
    def test_10pip_band_passes(self, validator):
        """10-pip entry band should pass."""
        signal = {
            "entry_price": 2350.0,
            "entry_band_low": 2349.50,
            "entry_band_high": 2350.50,  # 10 pips wide
        }
        check = validator._check_entry_band(signal)
        assert check.passed is True
        assert check.details["band_pips"] == pytest.approx(10.0, abs=0.1)

    def test_1pip_band_fails(self, validator):
        """1-pip entry band should fail."""
        signal = {
            "entry_price": 2350.0,
            "entry_band_low": 2349.95,
            "entry_band_high": 2350.05,  # 1 pip wide
        }
        check = validator._check_entry_band(signal)
        assert check.passed is False
        assert len(check.suggestions) > 0

    def test_no_band_suggests_zone(self, validator):
        """Missing band should suggest a 10-pip zone."""
        signal = {"entry_price": 2350.0}
        check = validator._check_entry_band(signal)
        assert check.passed is False
        assert "suggested_band_low" in check.details
        assert "suggested_band_high" in check.details
        # Suggested band should be 10 pips wide
        width = check.details["suggested_band_high"] - check.details["suggested_band_low"]
        assert width == pytest.approx(ENTRY_BAND_PIPS * PIP_SIZE_GOLD, abs=0.01)

    def test_wide_band_scores_high(self, validator):
        """20-pip band should score higher than minimum."""
        signal = {
            "entry_price": 2350.0,
            "entry_band_low": 2349.00,
            "entry_band_high": 2351.00,  # 20 pips
        }
        check = validator._check_entry_band(signal)
        assert check.passed is True
        assert check.score > 0.7


# ─────────────────────────────────────────────────────────────
# Check 4: Dynamic Confidence Scoring
# ─────────────────────────────────────────────────────────────

class TestDynamicConfidenceScoring:
    def test_full_mtf_alignment_high_confidence(self, validator):
        """3/3 MTF alignment + high SMC + momentum = high confidence."""
        signal = {
            "mtf_alignment": {
                "h4_aligned": True,
                "h1_aligned": True,
                "m15_aligned": True,
            },
            "smc_score": 8.0,
            "momentum_score": 8.0,
            "confidence": 75.0,
            "news_events": [],
        }
        conf = validator._compute_dynamic_confidence(signal)
        assert conf >= 70.0

    def test_no_mtf_low_confidence(self, validator):
        """No MTF alignment = lower confidence."""
        signal = {
            "mtf_alignment": {
                "h4_aligned": False,
                "h1_aligned": False,
                "m15_aligned": False,
            },
            "smc_score": 5.0,
            "momentum_score": 5.0,
            "confidence": 75.0,
            "news_events": [],
        }
        conf = validator._compute_dynamic_confidence(signal)
        assert conf < 70.0

    def test_news_events_reduce_confidence(self, validator):
        """High-impact news events reduce confidence."""
        signal_no_news = {
            "mtf_alignment": {"h4_aligned": True, "h1_aligned": True, "m15_aligned": True},
            "smc_score": 7.0,
            "momentum_score": 7.0,
            "news_events": [],
        }
        signal_with_news = {
            **signal_no_news,
            "news_events": ["NFP Release"],
        }
        conf_no_news   = validator._compute_dynamic_confidence(signal_no_news)
        conf_with_news = validator._compute_dynamic_confidence(signal_with_news)
        assert conf_no_news > conf_with_news

    def test_static_confidence_divergence_flagged(self, validator):
        """Static confidence diverging >15% from dynamic should be flagged."""
        signal = {
            "confidence": 95.0,  # Static — too high
            "mtf_alignment": {"h4_aligned": False, "h1_aligned": False, "m15_aligned": False},
            "smc_score": 2.0,
            "momentum_score": 2.0,
            "news_events": [],
        }
        check = validator._check_dynamic_confidence(signal)
        assert len(check.suggestions) > 0


# ─────────────────────────────────────────────────────────────
# Check 5: SL Anchoring
# ─────────────────────────────────────────────────────────────

class TestSLAnchoring:
    def test_sl_anchored_to_swing_low_buy(self, validator):
        """BUY SL just below swing low should pass."""
        signal = {
            "type": "BUY",
            "entry_price": 2350.0,
            "sl_price": 2326.0,   # 2 pips below swing low 2328
            "atr": 15.0,
            "swing_low": 2328.0,
        }
        check = validator._check_sl_anchoring(signal)
        assert check.passed is True
        assert check.details["anchored"] is True

    def test_sl_anchored_to_swing_high_sell(self, validator):
        """SELL SL just above swing high should pass."""
        signal = {
            "type": "SELL",
            "entry_price": 2390.0,
            "sl_price": 2412.0,   # 2 pips above swing high 2410
            "atr": 15.0,
            "swing_high": 2410.0,
        }
        check = validator._check_sl_anchoring(signal)
        assert check.passed is True

    def test_sl_too_tight_fails(self, validator):
        """SL too close to swing low (< 0.1 ATR buffer) should fail."""
        signal = {
            "type": "BUY",
            "entry_price": 2350.0,
            "sl_price": 2327.9,   # Only 0.1 pips below swing low
            "atr": 15.0,
            "swing_low": 2328.0,
        }
        check = validator._check_sl_anchoring(signal)
        assert check.passed is False
        assert len(check.suggestions) > 0

    def test_sl_wrong_side_buy_fails(self, validator):
        """BUY SL above entry should fail immediately."""
        signal = {
            "type": "BUY",
            "entry_price": 2350.0,
            "sl_price": 2360.0,  # Above entry — invalid
            "atr": 15.0,
        }
        check = validator._check_sl_anchoring(signal)
        assert check.passed is False
        assert check.score == 0.0

    def test_sl_wrong_side_sell_fails(self, validator):
        """SELL SL below entry should fail immediately."""
        signal = {
            "type": "SELL",
            "entry_price": 2390.0,
            "sl_price": 2380.0,  # Below entry — invalid
            "atr": 15.0,
        }
        check = validator._check_sl_anchoring(signal)
        assert check.passed is False
        assert check.score == 0.0


# ─────────────────────────────────────────────────────────────
# Check 6: Regime-Specific Entry Rules
# ─────────────────────────────────────────────────────────────

class TestRegimeEntryRules:
    def test_range_sell_at_resistance_passes(self, validator):
        """RANGE SELL at resistance (top 30%) should pass."""
        signal = {
            "type": "SELL",
            "regime": "RANGE",
            "entry_price": 2390.0,   # 94% into range
            "nearest_support": 2315.0,
            "nearest_resistance": 2395.0,
            "adx": 18.0,
            "atr": 15.0,
            "atr_ratio": 0.9,
        }
        check = validator._check_regime_entry_rules(signal)
        assert check.passed is True

    def test_range_sell_at_support_fails(self, validator):
        """RANGE SELL at support (bottom 30%) should fail."""
        signal = {
            "type": "SELL",
            "regime": "RANGE",
            "entry_price": 2320.0,   # 6% into range
            "nearest_support": 2315.0,
            "nearest_resistance": 2395.0,
            "adx": 18.0,
            "atr": 15.0,
            "atr_ratio": 0.9,
        }
        check = validator._check_regime_entry_rules(signal)
        assert check.passed is False
        assert any("resistance" in s.lower() for s in check.suggestions)

    def test_range_buy_at_support_passes(self, validator):
        """RANGE BUY at support (bottom 30%) should pass."""
        signal = {
            "type": "BUY",
            "regime": "RANGE",
            "entry_price": 2320.0,   # 6% into range
            "nearest_support": 2315.0,
            "nearest_resistance": 2395.0,
            "adx": 18.0,
            "atr": 15.0,
            "atr_ratio": 0.9,
        }
        check = validator._check_regime_entry_rules(signal)
        assert check.passed is True

    def test_trend_up_sell_penalised(self, validator):
        """TREND_UP SELL is counter-trend and should be penalised."""
        signal = {
            "type": "SELL",
            "regime": "TREND_UP",
            "entry_price": 2390.0,
            "nearest_support": 2315.0,
            "nearest_resistance": 2395.0,
            "adx": 30.0,
            "atr": 15.0,
            "atr_ratio": 1.1,
        }
        check = validator._check_regime_entry_rules(signal)
        assert check.passed is False
        assert check.score <= 0.3

    def test_trend_down_buy_penalised(self, validator):
        """TREND_DOWN BUY is counter-trend and should be penalised."""
        signal = {
            "type": "BUY",
            "regime": "TREND_DOWN",
            "entry_price": 2320.0,
            "nearest_support": 2315.0,
            "nearest_resistance": 2395.0,
            "adx": 28.0,
            "atr": 15.0,
            "atr_ratio": 1.0,
        }
        check = validator._check_regime_entry_rules(signal)
        assert check.passed is False
        assert check.score <= 0.3


# ─────────────────────────────────────────────────────────────
# Check 7: Session Quality Detection
# ─────────────────────────────────────────────────────────────

class TestSessionQualityDetection:
    def test_london_ny_overlap_high_quality(self, validator):
        """13:00–16:00 UTC = HIGH session quality."""
        now = datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc)
        quality = validator._get_session_quality(now)
        assert quality == "HIGH"

    def test_post_ny_close_dead_zone(self, validator):
        """22:00–07:00 UTC = DEAD_ZONE."""
        for hour in [22, 23, 0, 1, 2, 3, 4, 5, 6]:
            now = datetime(2024, 1, 15, hour, 0, tzinfo=timezone.utc)
            quality = validator._get_session_quality(now)
            assert quality == "DEAD_ZONE", f"Hour {hour} should be DEAD_ZONE"

    def test_london_session_medium_quality(self, validator):
        """07:00–13:00 UTC = MEDIUM session quality."""
        now = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        quality = validator._get_session_quality(now)
        assert quality == "MEDIUM"

    def test_dead_zone_check_fails(self, validator):
        """Session check should fail during dead zone."""
        now = datetime(2024, 1, 15, 2, 0, tzinfo=timezone.utc)
        check = validator._check_session_quality({}, now)
        assert check.passed is False
        assert check.score <= 0.2
        assert len(check.suggestions) > 0

    def test_high_quality_session_passes(self, validator):
        """Session check should pass during London/NY overlap."""
        now = datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc)
        check = validator._check_session_quality({}, now)
        assert check.passed is True
        assert check.score == 1.0


# ─────────────────────────────────────────────────────────────
# Check 8: Signal Expiry
# ─────────────────────────────────────────────────────────────

class TestSignalExpiry:
    def test_expiry_present_passes(self, validator):
        """Signal with expiry field should pass."""
        now = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        signal = {"expiry": "Valid until 22:00 UTC (NY Close)"}
        check = validator._check_signal_expiry(signal, now)
        assert check.passed is True
        assert check.score == 1.0

    def test_no_expiry_fails_with_suggestion(self, validator):
        """Signal without expiry should fail and suggest one."""
        now = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        check = validator._check_signal_expiry({}, now)
        assert check.passed is False
        assert check.score == 0.5
        assert "computed_expiry" in check.details

    def test_expiry_field_name_valid_until(self, validator):
        """valid_until field is also accepted."""
        now = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        signal = {"valid_until": "Valid until 13:00 UTC (NY Open)"}
        check = validator._check_signal_expiry(signal, now)
        assert check.passed is True


# ─────────────────────────────────────────────────────────────
# Check 9: News Filter
# ─────────────────────────────────────────────────────────────

class TestNewsFilter:
    def test_nfp_flagged(self, validator):
        """NFP event should be flagged."""
        now = datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc)
        signal = {"news_events": ["NFP Release"]}
        check = validator._check_news_filter(signal, now)
        assert check.passed is False
        assert "NFP" in " ".join(check.details["news_flags"])

    def test_jolts_flagged(self, validator):
        """JOLTS event should be flagged."""
        now = datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc)
        signal = {"news_events": ["JOLTS Job Openings"]}
        check = validator._check_news_filter(signal, now)
        assert check.passed is False

    def test_beige_book_flagged(self, validator):
        """Beige Book event should be flagged."""
        now = datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc)
        signal = {"news_events": ["Beige Book Release"]}
        check = validator._check_news_filter(signal, now)
        assert check.passed is False

    def test_fomc_flagged(self, validator):
        """FOMC event should be flagged."""
        now = datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc)
        signal = {"news_events": ["FOMC Meeting Minutes"]}
        check = validator._check_news_filter(signal, now)
        assert check.passed is False

    def test_no_news_passes(self, validator):
        """No news events should pass."""
        now = datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc)
        signal = {"news_events": [], "news_checked": True}
        check = validator._check_news_filter(signal, now)
        assert check.passed is True
        assert check.score == 1.0

    def test_multiple_news_all_flagged(self, validator):
        """Multiple high-impact events should all be flagged."""
        now = datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc)
        signal = {"news_events": ["NFP Release", "FOMC Statement", "CPI Data"]}
        check = validator._check_news_filter(signal, now)
        assert check.passed is False
        assert len(check.details["news_flags"]) >= 2


# ─────────────────────────────────────────────────────────────
# Check 10: MTF Recalculation
# ─────────────────────────────────────────────────────────────

class TestMTFRecalculation:
    def test_full_mtf_alignment_passes(self, validator):
        """3/3 MTF alignment should pass."""
        signal = {
            "confidence": 80.0,
            "mtf_alignment": {
                "h4_aligned": True,
                "h1_aligned": True,
                "m15_aligned": True,
            },
        }
        check = validator._check_mtf_recalculation(signal)
        assert check.passed is True
        assert check.details["aligned_count"] == 3

    def test_partial_mtf_alignment_fails(self, validator):
        """1/3 MTF alignment should fail."""
        signal = {
            "confidence": 75.0,
            "mtf_alignment": {
                "h4_aligned": True,
                "h1_aligned": False,
                "m15_aligned": False,
            },
        }
        check = validator._check_mtf_recalculation(signal)
        assert check.passed is False

    def test_no_mtf_data_fails(self, validator):
        """No MTF data should fail with suggestion."""
        signal = {"confidence": 75.0}
        check = validator._check_mtf_recalculation(signal)
        assert check.passed is False
        assert len(check.suggestions) > 0

    def test_static_confidence_too_high_flagged(self, validator):
        """Confidence much higher than MTF-derived expectation should be flagged."""
        signal = {
            "confidence": 95.0,  # Way too high for 0/3 MTF alignment
            "mtf_alignment": {
                "h4_aligned": False,
                "h1_aligned": False,
                "m15_aligned": False,
            },
        }
        check = validator._check_mtf_recalculation(signal)
        assert check.passed is False
        assert len(check.suggestions) > 0


# ─────────────────────────────────────────────────────────────
# Full Validation Integration Tests
# ─────────────────────────────────────────────────────────────

class TestFullValidation:
    def test_good_buy_signal_passes(self, validator, good_buy_signal):
        """A well-formed BUY signal should pass validation."""
        result = validator.validate(good_buy_signal)
        assert isinstance(result, ValidationResult)
        assert result.quality_score > 0
        assert result.grade in ("A", "B", "C", "D", "F")
        assert result.regime in (
            REGIME_TREND_UP, REGIME_TREND_DOWN, REGIME_RANGE, REGIME_BREAKOUT,
            "HIGH_VOL", "LOW_VOL", "CHAOS"
        )
        assert result.expiry is not None
        assert len(result.enhancement_scores) == 13

    def test_poor_signal_low_score(self, validator, poor_signal):
        """A poorly-formed signal should score low."""
        result = validator.validate(poor_signal)
        assert result.quality_score < 70.0
        assert len(result.recommendations) > 0

    def test_result_has_all_required_fields(self, validator, good_buy_signal):
        """ValidationResult must have all required fields."""
        result = validator.validate(good_buy_signal)
        d = result.to_dict()
        required_keys = [
            "passed", "quality_score", "confidence_score", "grade",
            "regime", "session_quality", "expiry", "news_flags",
            "recommendations", "warnings", "enhancement_scores",
            "checks", "timestamp",
        ]
        for key in required_keys:
            assert key in d, f"Missing key: {key}"

    def test_enhancement_scores_all_13_present(self, validator, good_buy_signal):
        """All 13 hybrid enhancement indicator scores must be present."""
        result = validator.validate(good_buy_signal)
        expected_indicators = [
            "smc_order_flow", "triple_momentum", "vwap_price_action",
            "fibonacci_smc", "atr_bollinger", "range_breakout_filter",
            "swing_scalp_timing", "trend_mean_reversion", "mtf_pyramid",
            "session_mtf_weighting", "fixed_trailing_stop",
            "volatility_position_size", "dynamic_confluence",
        ]
        for indicator in expected_indicators:
            assert indicator in result.enhancement_scores, f"Missing indicator: {indicator}"

    def test_news_flagged_signal_has_news_flags(self, validator):
        """Signal with NFP should have news_flags populated."""
        signal = {
            "type": "BUY",
            "entry_price": 2350.0,
            "sl_price": 2330.0,
            "tp_levels": [2390.0],
            "news_events": ["NFP Release"],
        }
        result = validator.validate(signal)
        assert len(result.news_flags) > 0

    def test_singleton_works(self):
        """Module-level singleton should be usable."""
        result = signal_quality_validator.validate({
            "type": "BUY",
            "entry_price": 2350.0,
            "sl_price": 2330.0,
            "tp_levels": [2390.0],
        })
        assert isinstance(result, ValidationResult)

    def test_quality_score_range(self, validator, good_buy_signal):
        """Quality score must be in [0, 100]."""
        result = validator.validate(good_buy_signal)
        assert 0.0 <= result.quality_score <= 100.0

    def test_confidence_score_range(self, validator, good_buy_signal):
        """Confidence score must be in [0, 100]."""
        result = validator.validate(good_buy_signal)
        assert 0.0 <= result.confidence_score <= 100.0

    def test_empty_signal_does_not_crash(self, validator):
        """Empty signal dict should not raise an exception."""
        result = validator.validate({})
        assert isinstance(result, ValidationResult)
        assert result.quality_score >= 0.0

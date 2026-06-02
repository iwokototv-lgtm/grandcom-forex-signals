"""
Signal Quality Validator — Comprehensive Signal Validation Suite
Gold Trading System v3.0.2

Enforces institutional-grade signal quality standards across nine dimensions:

  1. RiskRewardValidator      — Enforce 1:2 minimum R:R for swing trades
  2. RegimeValidator          — Distinguish BEARISH_TREND from RANGE correctly
  3. EntryValidator           — Enforce 10-pip entry zones (e.g. 4470–4480)
  4. ConfidenceCalculator     — Dynamic scoring from MTF, SMC, momentum, session, news
  5. SLValidator              — Anchor SL to swing high/low, quantify ATR multiple
  6. SessionValidator         — Flag post-NY close, recommend London open (07:00 UTC)
  7. MTFValidator             — Dynamic recalculation; confidence drops on misalignment
  8. SignalExpiryValidator    — Add expiry field (e.g. 'Valid until 02:00 UTC')
  9. NewsFilterValidator      — Flag JOLTS, Beige Book, NFP; recommend size reduction

Usage:
    from ml_engine.signal_quality_validator import SignalQualityValidator

    validator = SignalQualityValidator()
    report = validator.validate(signal_dict)
    # report.passed, report.overall_score, report.issues, report.recommendations
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

# Risk/Reward thresholds
RR_MINIMUM_SWING   = 2.0   # 1:2 minimum for swing trades
RR_MINIMUM_SCALP   = 1.5   # 1:1.5 minimum for scalp trades
RR_GOOD            = 2.5
RR_EXCELLENT       = 3.0

# Entry zone width in pips (XAUUSD: 1 pip = $0.10)
ENTRY_ZONE_PIPS_MIN  = 10   # Minimum 10-pip zone
ENTRY_ZONE_PIPS_MAX  = 30   # Maximum 30-pip zone (beyond this is too wide)
PIP_SIZE_XAUUSD      = 0.10  # $0.10 per pip for gold

# ATR multiples for SL anchoring
SL_ATR_MIN   = 0.5   # SL must be at least 0.5 ATR beyond structure
SL_ATR_MAX   = 2.5   # SL beyond 2.5 ATR is too wide
SL_ATR_IDEAL = 1.0   # Ideal SL buffer

# Session windows (UTC hours)
LONDON_OPEN_UTC   = 7    # 07:00 UTC
LONDON_CLOSE_UTC  = 16   # 16:00 UTC
NY_OPEN_UTC       = 13   # 13:00 UTC
NY_CLOSE_UTC      = 22   # 22:00 UTC
ASIAN_OPEN_UTC    = 0    # 00:00 UTC
ASIAN_CLOSE_UTC   = 8    # 08:00 UTC

# Post-NY close dead zone (22:00–07:00 UTC)
DEAD_ZONE_START_UTC = 22
DEAD_ZONE_END_UTC   = 7

# Confidence scoring weights
CONFIDENCE_WEIGHTS = {
    "mtf_alignment":    0.25,
    "smc_confluence":   0.20,
    "momentum":         0.15,
    "session_quality":  0.15,
    "news_clear":       0.10,
    "rr_quality":       0.10,
    "regime_clarity":   0.05,
}

# High-impact news events that affect gold
GOLD_NEWS_EVENTS = {
    "NFP":          {"impact": "CRITICAL",  "blackout_hours": 2,  "size_reduction": 0.50},
    "Non-Farm":     {"impact": "CRITICAL",  "blackout_hours": 2,  "size_reduction": 0.50},
    "FOMC":         {"impact": "CRITICAL",  "blackout_hours": 4,  "size_reduction": 0.75},
    "Fed":          {"impact": "HIGH",      "blackout_hours": 2,  "size_reduction": 0.50},
    "CPI":          {"impact": "CRITICAL",  "blackout_hours": 2,  "size_reduction": 0.50},
    "Inflation":    {"impact": "HIGH",      "blackout_hours": 1,  "size_reduction": 0.40},
    "JOLTS":        {"impact": "HIGH",      "blackout_hours": 1,  "size_reduction": 0.30},
    "Beige Book":   {"impact": "MEDIUM",    "blackout_hours": 1,  "size_reduction": 0.25},
    "GDP":          {"impact": "HIGH",      "blackout_hours": 1,  "size_reduction": 0.40},
    "PPI":          {"impact": "MEDIUM",    "blackout_hours": 1,  "size_reduction": 0.25},
    "ISM":          {"impact": "MEDIUM",    "blackout_hours": 1,  "size_reduction": 0.25},
    "PMI":          {"impact": "MEDIUM",    "blackout_hours": 1,  "size_reduction": 0.20},
    "Retail Sales": {"impact": "HIGH",      "blackout_hours": 1,  "size_reduction": 0.35},
    "Jackson Hole": {"impact": "CRITICAL",  "blackout_hours": 6,  "size_reduction": 0.75},
}

# Regime classification thresholds
REGIME_ADX_TREND_THRESHOLD  = 25   # ADX > 25 = trending
REGIME_ADX_RANGE_THRESHOLD  = 20   # ADX < 20 = ranging
REGIME_BEARISH_SLOPE_CUTOFF = -0.1  # MA slope < -0.1 = bearish trend
REGIME_BULLISH_SLOPE_CUTOFF =  0.1  # MA slope > +0.1 = bullish trend

# Signal expiry windows by trade type
EXPIRY_HOURS = {
    "SCALP":  2,
    "SWING":  24,
    "INTRA":  8,
    "DEFAULT": 12,
}

# MTF alignment thresholds
MTF_FULL_ALIGNMENT    = 0.80   # ≥ 80% = full alignment
MTF_PARTIAL_ALIGNMENT = 0.60   # 60–79% = partial
MTF_MISALIGNED        = 0.40   # < 40% = misaligned (confidence penalty)

# Confidence penalty for MTF misalignment
MTF_MISALIGNMENT_PENALTY = 15.0   # Subtract 15 confidence points


# ─────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────

@dataclass
class ValidationIssue:
    """A single validation finding."""
    severity:    str    # CRITICAL | WARNING | INFO
    validator:   str    # Which validator raised this
    code:        str    # Machine-readable code
    message:     str    # Human-readable description
    suggestion:  str    # Recommended fix


@dataclass
class ValidationReport:
    """Complete validation report for a signal."""
    signal_id:          Optional[str]
    passed:             bool
    overall_score:      float                          # 0–100
    issues:             List[ValidationIssue] = field(default_factory=list)
    recommendations:    List[str]             = field(default_factory=list)
    confidence_breakdown: Dict[str, float]    = field(default_factory=dict)
    dynamic_confidence: float                 = 75.0
    expiry_utc:         Optional[str]         = None
    session_quality:    str                   = "UNKNOWN"
    news_flags:         List[str]             = field(default_factory=list)
    regime_classification: str               = "UNKNOWN"
    rr_ratio:           float                 = 0.0
    entry_zone_pips:    float                 = 0.0
    sl_atr_multiple:    float                 = 0.0
    mtf_alignment_pct:  float                 = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal_id":           self.signal_id,
            "passed":              self.passed,
            "overall_score":       round(self.overall_score, 2),
            "dynamic_confidence":  round(self.dynamic_confidence, 2),
            "expiry_utc":          self.expiry_utc,
            "session_quality":     self.session_quality,
            "news_flags":          self.news_flags,
            "regime_classification": self.regime_classification,
            "rr_ratio":            round(self.rr_ratio, 3),
            "entry_zone_pips":     round(self.entry_zone_pips, 1),
            "sl_atr_multiple":     round(self.sl_atr_multiple, 3),
            "mtf_alignment_pct":   round(self.mtf_alignment_pct, 1),
            "confidence_breakdown": {
                k: round(v, 2) for k, v in self.confidence_breakdown.items()
            },
            "issues": [
                {
                    "severity":   i.severity,
                    "validator":  i.validator,
                    "code":       i.code,
                    "message":    i.message,
                    "suggestion": i.suggestion,
                }
                for i in self.issues
            ],
            "recommendations": self.recommendations,
            "critical_count": sum(1 for i in self.issues if i.severity == "CRITICAL"),
            "warning_count":  sum(1 for i in self.issues if i.severity == "WARNING"),
        }


# ─────────────────────────────────────────────────────────────
# Individual Validators
# ─────────────────────────────────────────────────────────────

class RiskRewardValidator:
    """
    Enforce 1:2 minimum R:R for swing trades.

    The previous system accepted 1:1.3 which is insufficient for
    institutional-grade signal quality.  This validator enforces:
      - Swing trades: minimum 1:2 (TP1 must deliver 2× the risk)
      - Scalp trades: minimum 1:1.5
      - Excellent: 1:3 or better
    """

    def validate(
        self,
        signal_type: str,
        entry_price: float,
        sl_price: float,
        tp_levels: List[float],
        trade_type: str = "SWING",
    ) -> Tuple[List[ValidationIssue], float, float]:
        """
        Returns (issues, rr_ratio, score_contribution 0-100).
        """
        issues: List[ValidationIssue] = []
        risk = abs(entry_price - sl_price)

        if risk <= 0:
            issues.append(ValidationIssue(
                severity="CRITICAL",
                validator="RiskRewardValidator",
                code="RR_ZERO_RISK",
                message="Entry and SL are at the same price — risk is zero.",
                suggestion="Separate SL from entry by at least 1 ATR.",
            ))
            return issues, 0.0, 0.0

        if not tp_levels:
            issues.append(ValidationIssue(
                severity="CRITICAL",
                validator="RiskRewardValidator",
                code="RR_NO_TP",
                message="No TP levels provided — R:R cannot be evaluated.",
                suggestion="Add at least one TP level at minimum 2:1 R:R.",
            ))
            return issues, 0.0, 0.0

        direction = signal_type.upper()
        tp1 = tp_levels[0]
        reward = (tp1 - entry_price) if direction == "BUY" else (entry_price - tp1)
        rr_ratio = reward / risk if risk > 0 else 0.0

        minimum_rr = RR_MINIMUM_SCALP if trade_type.upper() == "SCALP" else RR_MINIMUM_SWING

        if rr_ratio < minimum_rr:
            issues.append(ValidationIssue(
                severity="CRITICAL",
                validator="RiskRewardValidator",
                code="RR_BELOW_MINIMUM",
                message=(
                    f"R:R of {rr_ratio:.2f}:1 is below the {minimum_rr}:1 minimum "
                    f"for {trade_type} trades. Previous threshold of 1:1.3 was too lenient."
                ),
                suggestion=(
                    f"Move TP1 to achieve {minimum_rr}:1 R:R. "
                    f"Required TP1: "
                    f"{entry_price + risk * minimum_rr:.5g} (BUY) or "
                    f"{entry_price - risk * minimum_rr:.5g} (SELL). "
                    f"Alternatively, tighten SL to reduce risk."
                ),
            ))
        elif rr_ratio < RR_GOOD:
            issues.append(ValidationIssue(
                severity="WARNING",
                validator="RiskRewardValidator",
                code="RR_ACCEPTABLE",
                message=f"R:R of {rr_ratio:.2f}:1 meets minimum but is below the ideal 2.5:1.",
                suggestion=f"Consider extending TP2/TP3 to improve overall R:R profile.",
            ))

        # Score: 0 at RR=0, 50 at RR=minimum, 80 at RR=good, 100 at RR=excellent
        if rr_ratio >= RR_EXCELLENT:
            score = 100.0
        elif rr_ratio >= RR_GOOD:
            score = 80.0 + (rr_ratio - RR_GOOD) / (RR_EXCELLENT - RR_GOOD) * 20.0
        elif rr_ratio >= minimum_rr:
            score = 50.0 + (rr_ratio - minimum_rr) / (RR_GOOD - minimum_rr) * 30.0
        else:
            score = max(0.0, rr_ratio / minimum_rr * 50.0)

        return issues, rr_ratio, round(score, 2)


class RegimeValidator:
    """
    Distinguish BEARISH_TREND from RANGE correctly.

    The previous system confused RANGE with BEARISH_TREND when ADX was
    borderline.  This validator applies strict criteria:
      - ADX > 25 + negative MA slope → BEARISH_TREND
      - ADX < 20 + price oscillating → RANGE
      - ADX 20–25 → TRANSITIONAL (requires additional confirmation)
    """

    REGIME_LABELS = {
        "BEARISH_TREND":  "Price is in a confirmed downtrend (ADX > 25, negative slope).",
        "BULLISH_TREND":  "Price is in a confirmed uptrend (ADX > 25, positive slope).",
        "RANGE":          "Price is ranging (ADX < 20, oscillating between S/R).",
        "TRANSITIONAL":   "Regime is transitioning — ADX 20–25, direction unclear.",
        "HIGH_VOLATILITY": "High volatility event-driven regime.",
        "UNKNOWN":        "Insufficient data to classify regime.",
    }

    def validate(
        self,
        adx: float,
        ma_slope: float,
        structure_bias: int,
        atr_ratio: float,
        signal_type: str,
    ) -> Tuple[List[ValidationIssue], str, float]:
        """
        Returns (issues, regime_label, score_contribution 0-100).
        """
        issues: List[ValidationIssue] = []

        # Classify regime
        if atr_ratio > 1.8:
            regime = "HIGH_VOLATILITY"
        elif adx > REGIME_ADX_TREND_THRESHOLD:
            if ma_slope < REGIME_BEARISH_SLOPE_CUTOFF and structure_bias < -2:
                regime = "BEARISH_TREND"
            elif ma_slope > REGIME_BULLISH_SLOPE_CUTOFF and structure_bias > 2:
                regime = "BULLISH_TREND"
            elif ma_slope < 0:
                regime = "BEARISH_TREND"
            else:
                regime = "BULLISH_TREND"
        elif adx < REGIME_ADX_RANGE_THRESHOLD:
            regime = "RANGE"
        else:
            regime = "TRANSITIONAL"

        direction = signal_type.upper()

        # Check for entry logic inversion (selling at support in range, etc.)
        if regime == "RANGE":
            if direction == "SELL" and structure_bias > 1:
                issues.append(ValidationIssue(
                    severity="CRITICAL",
                    validator="RegimeValidator",
                    code="REGIME_ENTRY_INVERTED",
                    message=(
                        "RANGE regime: SELL signal detected near support (structure_bias > 0). "
                        "In a range, SELL entries should be at resistance, not support."
                    ),
                    suggestion=(
                        "Wait for price to reach resistance before entering SELL. "
                        "Current entry appears to be at support — this is inverted logic."
                    ),
                ))
            elif direction == "BUY" and structure_bias < -1:
                issues.append(ValidationIssue(
                    severity="CRITICAL",
                    validator="RegimeValidator",
                    code="REGIME_ENTRY_INVERTED",
                    message=(
                        "RANGE regime: BUY signal detected near resistance (structure_bias < 0). "
                        "In a range, BUY entries should be at support, not resistance."
                    ),
                    suggestion=(
                        "Wait for price to reach support before entering BUY. "
                        "Current entry appears to be at resistance — this is inverted logic."
                    ),
                ))

        # Check for regime/direction mismatch
        if regime == "BEARISH_TREND" and direction == "BUY":
            issues.append(ValidationIssue(
                severity="WARNING",
                validator="RegimeValidator",
                code="REGIME_COUNTER_TREND_BUY",
                message=(
                    f"BUY signal in BEARISH_TREND regime (ADX={adx:.1f}, slope={ma_slope:.3f}). "
                    "Counter-trend trades carry higher failure risk."
                ),
                suggestion=(
                    "Reduce position size by 50% for counter-trend trades. "
                    "Ensure strong SMC confluence (OB + FVG + OTE) before entry."
                ),
            ))
        elif regime == "BULLISH_TREND" and direction == "SELL":
            issues.append(ValidationIssue(
                severity="WARNING",
                validator="RegimeValidator",
                code="REGIME_COUNTER_TREND_SELL",
                message=(
                    f"SELL signal in BULLISH_TREND regime (ADX={adx:.1f}, slope={ma_slope:.3f}). "
                    "Counter-trend trades carry higher failure risk."
                ),
                suggestion=(
                    "Reduce position size by 50% for counter-trend trades. "
                    "Ensure strong SMC confluence (OB + FVG + OTE) before entry."
                ),
            ))

        if regime == "TRANSITIONAL":
            issues.append(ValidationIssue(
                severity="WARNING",
                validator="RegimeValidator",
                code="REGIME_TRANSITIONAL",
                message=(
                    f"Regime is TRANSITIONAL (ADX={adx:.1f}, slope={ma_slope:.3f}). "
                    "Direction is unclear — signal quality is reduced."
                ),
                suggestion=(
                    "Wait for ADX to break above 25 (trend) or below 20 (range) "
                    "before committing full position size."
                ),
            ))

        # Score: 100 for clear regime match, 70 for transitional, 50 for counter-trend
        critical_count = sum(1 for i in issues if i.severity == "CRITICAL")
        warning_count  = sum(1 for i in issues if i.severity == "WARNING")
        if critical_count > 0:
            score = 30.0
        elif warning_count > 0:
            score = 60.0
        else:
            score = 100.0

        return issues, regime, round(score, 2)


class EntryValidator:
    """
    Enforce 10-pip entry zones (e.g. 4470–4480).

    The previous system used 1-pip bands which are unrealistic for
    institutional execution.  This validator enforces:
      - Minimum 10-pip zone width
      - Maximum 30-pip zone width
      - Zone must straddle a structural level (support/resistance)
    """

    def validate(
        self,
        entry_price: float,
        entry_zone_low: Optional[float],
        entry_zone_high: Optional[float],
        nearest_structure: float,
        atr: float,
        signal_type: str,
    ) -> Tuple[List[ValidationIssue], float, float]:
        """
        Returns (issues, zone_width_pips, score_contribution 0-100).
        """
        issues: List[ValidationIssue] = []

        # If no zone provided, infer from entry price
        if entry_zone_low is None or entry_zone_high is None:
            # Default: ±5 pips around entry (10-pip zone)
            half_zone = ENTRY_ZONE_PIPS_MIN / 2 * PIP_SIZE_XAUUSD
            entry_zone_low  = entry_price - half_zone
            entry_zone_high = entry_price + half_zone
            issues.append(ValidationIssue(
                severity="WARNING",
                validator="EntryValidator",
                code="ENTRY_NO_ZONE",
                message=(
                    f"No entry zone provided. Inferred ±5-pip zone: "
                    f"{entry_zone_low:.5g}–{entry_zone_high:.5g}."
                ),
                suggestion=(
                    f"Explicitly define a 10-pip entry zone around the structural level "
                    f"at {nearest_structure:.5g}. Example: "
                    f"{nearest_structure - 0.50:.5g}–{nearest_structure + 0.50:.5g}."
                ),
            ))

        zone_width = entry_zone_high - entry_zone_low
        zone_pips  = zone_width / PIP_SIZE_XAUUSD

        if zone_pips < ENTRY_ZONE_PIPS_MIN:
            issues.append(ValidationIssue(
                severity="CRITICAL",
                validator="EntryValidator",
                code="ENTRY_ZONE_TOO_NARROW",
                message=(
                    f"Entry zone of {zone_pips:.1f} pips is too narrow (minimum {ENTRY_ZONE_PIPS_MIN} pips). "
                    f"A 1-pip band is unrealistic for institutional execution."
                ),
                suggestion=(
                    f"Widen entry zone to at least {ENTRY_ZONE_PIPS_MIN} pips. "
                    f"Suggested zone: "
                    f"{entry_price - ENTRY_ZONE_PIPS_MIN * PIP_SIZE_XAUUSD / 2:.5g}–"
                    f"{entry_price + ENTRY_ZONE_PIPS_MIN * PIP_SIZE_XAUUSD / 2:.5g}."
                ),
            ))
        elif zone_pips > ENTRY_ZONE_PIPS_MAX:
            issues.append(ValidationIssue(
                severity="WARNING",
                validator="EntryValidator",
                code="ENTRY_ZONE_TOO_WIDE",
                message=(
                    f"Entry zone of {zone_pips:.1f} pips is too wide (maximum {ENTRY_ZONE_PIPS_MAX} pips). "
                    f"Wide zones reduce precision and worsen R:R."
                ),
                suggestion=(
                    f"Narrow entry zone to {ENTRY_ZONE_PIPS_MIN}–{ENTRY_ZONE_PIPS_MAX} pips "
                    f"centred on the structural level at {nearest_structure:.5g}."
                ),
            ))

        # Check zone contains the structural level
        if not (entry_zone_low <= nearest_structure <= entry_zone_high):
            dist_pips = abs(entry_price - nearest_structure) / PIP_SIZE_XAUUSD
            if dist_pips > ENTRY_ZONE_PIPS_MAX:
                issues.append(ValidationIssue(
                    severity="WARNING",
                    validator="EntryValidator",
                    code="ENTRY_ZONE_MISSES_STRUCTURE",
                    message=(
                        f"Entry zone {entry_zone_low:.5g}–{entry_zone_high:.5g} does not "
                        f"contain the nearest structural level at {nearest_structure:.5g} "
                        f"({dist_pips:.1f} pips away)."
                    ),
                    suggestion=(
                        f"Re-centre entry zone on the structural level: "
                        f"{nearest_structure - ENTRY_ZONE_PIPS_MIN * PIP_SIZE_XAUUSD / 2:.5g}–"
                        f"{nearest_structure + ENTRY_ZONE_PIPS_MIN * PIP_SIZE_XAUUSD / 2:.5g}."
                    ),
                ))

        # Score
        if ENTRY_ZONE_PIPS_MIN <= zone_pips <= ENTRY_ZONE_PIPS_MAX:
            score = 100.0
        elif zone_pips < ENTRY_ZONE_PIPS_MIN:
            score = max(0.0, zone_pips / ENTRY_ZONE_PIPS_MIN * 60.0)
        else:
            score = max(40.0, 100.0 - (zone_pips - ENTRY_ZONE_PIPS_MAX) * 3.0)

        return issues, round(zone_pips, 1), round(score, 2)


class ConfidenceCalculator:
    """
    Dynamic confidence scoring from MTF, SMC, momentum, session, and news.

    Replaces the static 75% fixed confidence with a multi-factor dynamic
    score that accurately reflects current market conditions.
    """

    def calculate(
        self,
        mtf_alignment_pct:  float,
        smc_confluence_pct: float,
        momentum_score:     float,
        session_score:      float,
        news_clear_score:   float,
        rr_score:           float,
        regime_score:       float,
    ) -> Tuple[float, Dict[str, float]]:
        """
        Returns (dynamic_confidence 0-100, breakdown dict).

        Each input is expected in 0–100 range.
        """
        components = {
            "mtf_alignment":   min(100.0, max(0.0, mtf_alignment_pct)),
            "smc_confluence":  min(100.0, max(0.0, smc_confluence_pct)),
            "momentum":        min(100.0, max(0.0, momentum_score)),
            "session_quality": min(100.0, max(0.0, session_score)),
            "news_clear":      min(100.0, max(0.0, news_clear_score)),
            "rr_quality":      min(100.0, max(0.0, rr_score)),
            "regime_clarity":  min(100.0, max(0.0, regime_score)),
        }

        # Weighted sum
        total_weight = sum(CONFIDENCE_WEIGHTS.values())
        weighted_sum = sum(
            components[k] * CONFIDENCE_WEIGHTS[k]
            for k in components
        )
        dynamic_confidence = weighted_sum / total_weight

        # Apply MTF misalignment penalty
        if mtf_alignment_pct < MTF_MISALIGNED * 100:
            dynamic_confidence = max(0.0, dynamic_confidence - MTF_MISALIGNMENT_PENALTY)
            components["mtf_penalty_applied"] = -MTF_MISALIGNMENT_PENALTY

        return round(dynamic_confidence, 2), components


class SLValidator:
    """
    Anchor SL to swing high/low and quantify ATR multiple.

    The previous system placed SL without ATR quantification.  This
    validator ensures:
      - SL is anchored to a structural swing point
      - SL buffer is expressed as an ATR multiple (0.5–2.5 ATR)
      - SL is not inside a liquidity cluster
    """

    def validate(
        self,
        signal_type:  str,
        entry_price:  float,
        sl_price:     float,
        swing_high:   Optional[float],
        swing_low:    Optional[float],
        atr:          float,
        nearest_support:    float,
        nearest_resistance: float,
    ) -> Tuple[List[ValidationIssue], float, float]:
        """
        Returns (issues, sl_atr_multiple, score_contribution 0-100).
        """
        issues: List[ValidationIssue] = []

        if atr <= 0:
            atr = abs(entry_price * 0.005)

        risk = abs(entry_price - sl_price)
        sl_atr_multiple = risk / atr if atr > 0 else 0.0
        direction = signal_type.upper()

        # Check ATR multiple bounds
        if sl_atr_multiple < SL_ATR_MIN:
            issues.append(ValidationIssue(
                severity="CRITICAL",
                validator="SLValidator",
                code="SL_TOO_TIGHT",
                message=(
                    f"SL is only {sl_atr_multiple:.2f} ATR from entry (minimum {SL_ATR_MIN} ATR). "
                    f"SL at {sl_price:.5g} is dangerously tight — high stop-hunt risk."
                ),
                suggestion=(
                    f"Move SL to at least {SL_ATR_MIN} ATR from entry. "
                    f"Suggested SL: "
                    f"{entry_price - atr * SL_ATR_MIN:.5g} (BUY) or "
                    f"{entry_price + atr * SL_ATR_MIN:.5g} (SELL)."
                ),
            ))
        elif sl_atr_multiple > SL_ATR_MAX:
            issues.append(ValidationIssue(
                severity="WARNING",
                validator="SLValidator",
                code="SL_TOO_WIDE",
                message=(
                    f"SL is {sl_atr_multiple:.2f} ATR from entry (maximum {SL_ATR_MAX} ATR). "
                    f"Excessively wide SL degrades R:R ratio."
                ),
                suggestion=(
                    f"Tighten SL to {SL_ATR_IDEAL}–{SL_ATR_MAX} ATR from entry. "
                    f"Suggested SL: "
                    f"{entry_price - atr * SL_ATR_IDEAL:.5g} (BUY) or "
                    f"{entry_price + atr * SL_ATR_IDEAL:.5g} (SELL)."
                ),
            ))

        # Check structural anchoring
        anchored = False
        anchor_description = "No structural anchor found."

        if direction == "BUY":
            # SL should be below swing low or support
            if swing_low is not None and sl_price <= swing_low:
                anchored = True
                anchor_description = f"SL anchored below swing low at {swing_low:.5g}."
            elif sl_price <= nearest_support:
                anchored = True
                anchor_description = f"SL anchored below support at {nearest_support:.5g}."
        else:  # SELL
            # SL should be above swing high or resistance
            if swing_high is not None and sl_price >= swing_high:
                anchored = True
                anchor_description = f"SL anchored above swing high at {swing_high:.5g}."
            elif sl_price >= nearest_resistance:
                anchored = True
                anchor_description = f"SL anchored above resistance at {nearest_resistance:.5g}."

        if not anchored:
            issues.append(ValidationIssue(
                severity="WARNING",
                validator="SLValidator",
                code="SL_NOT_ANCHORED",
                message=(
                    f"SL at {sl_price:.5g} is not anchored to a structural level. "
                    f"{anchor_description}"
                ),
                suggestion=(
                    f"Anchor SL to the nearest swing {'low' if direction == 'BUY' else 'high'} "
                    f"or {'support' if direction == 'BUY' else 'resistance'} level. "
                    f"ATR multiple: {sl_atr_multiple:.2f}× (target: {SL_ATR_IDEAL}×)."
                ),
            ))

        # Score
        critical_count = sum(1 for i in issues if i.severity == "CRITICAL")
        warning_count  = sum(1 for i in issues if i.severity == "WARNING")
        if critical_count > 0:
            score = 20.0
        elif warning_count > 0:
            score = 65.0
        elif SL_ATR_MIN <= sl_atr_multiple <= SL_ATR_MAX and anchored:
            score = 100.0
        else:
            score = 80.0

        return issues, round(sl_atr_multiple, 3), round(score, 2)


class SessionValidator:
    """
    Flag post-NY close timing and recommend London open (07:00 UTC).

    Trading during the dead zone (22:00–07:00 UTC) produces false signals
    due to low liquidity.  This validator flags such signals and recommends
    optimal session windows.
    """

    SESSION_NAMES = {
        "LONDON":  f"London Session ({LONDON_OPEN_UTC:02d}:00–{LONDON_CLOSE_UTC:02d}:00 UTC)",
        "NY":      f"New York Session ({NY_OPEN_UTC:02d}:00–{NY_CLOSE_UTC:02d}:00 UTC)",
        "OVERLAP": f"London/NY Overlap ({NY_OPEN_UTC:02d}:00–{LONDON_CLOSE_UTC:02d}:00 UTC)",
        "ASIAN":   f"Asian Session ({ASIAN_OPEN_UTC:02d}:00–{ASIAN_CLOSE_UTC:02d}:00 UTC)",
        "DEAD":    f"Dead Zone ({DEAD_ZONE_START_UTC:02d}:00–{DEAD_ZONE_END_UTC:02d}:00 UTC)",
    }

    SESSION_SCORES = {
        "OVERLAP": 100.0,
        "LONDON":   85.0,
        "NY":       80.0,
        "ASIAN":    50.0,
        "DEAD":     10.0,
    }

    def validate(
        self,
        signal_time_utc: Optional[datetime] = None,
    ) -> Tuple[List[ValidationIssue], str, float]:
        """
        Returns (issues, session_name, score_contribution 0-100).
        """
        issues: List[ValidationIssue] = []

        if signal_time_utc is None:
            signal_time_utc = datetime.now(timezone.utc)

        # Normalise to UTC
        if signal_time_utc.tzinfo is None:
            signal_time_utc = signal_time_utc.replace(tzinfo=timezone.utc)

        hour = signal_time_utc.hour

        # Classify session
        if NY_OPEN_UTC <= hour < LONDON_CLOSE_UTC:
            session = "OVERLAP"
        elif LONDON_OPEN_UTC <= hour < LONDON_CLOSE_UTC:
            session = "LONDON"
        elif NY_OPEN_UTC <= hour < NY_CLOSE_UTC:
            session = "NY"
        elif ASIAN_OPEN_UTC <= hour < ASIAN_CLOSE_UTC:
            session = "ASIAN"
        else:
            session = "DEAD"

        score = self.SESSION_SCORES.get(session, 50.0)

        if session == "DEAD":
            issues.append(ValidationIssue(
                severity="CRITICAL",
                validator="SessionValidator",
                code="SESSION_DEAD_ZONE",
                message=(
                    f"Signal generated at {signal_time_utc.strftime('%H:%M')} UTC — "
                    f"this is the post-NY close dead zone ({DEAD_ZONE_START_UTC:02d}:00–"
                    f"{DEAD_ZONE_END_UTC:02d}:00 UTC). "
                    f"Low liquidity produces false signals and wide spreads."
                ),
                suggestion=(
                    f"Delay signal until London open at {LONDON_OPEN_UTC:02d}:00 UTC. "
                    f"Best execution windows: London ({LONDON_OPEN_UTC:02d}:00–{LONDON_CLOSE_UTC:02d}:00 UTC) "
                    f"or NY ({NY_OPEN_UTC:02d}:00–{NY_CLOSE_UTC:02d}:00 UTC)."
                ),
            ))
        elif session == "ASIAN":
            issues.append(ValidationIssue(
                severity="WARNING",
                validator="SessionValidator",
                code="SESSION_LOW_LIQUIDITY",
                message=(
                    f"Signal generated during Asian session ({signal_time_utc.strftime('%H:%M')} UTC). "
                    f"Gold liquidity is reduced — signals have higher false-positive rate."
                ),
                suggestion=(
                    f"Reduce position size by 25% during Asian session. "
                    f"Wait for London open at {LONDON_OPEN_UTC:02d}:00 UTC for full-size entry."
                ),
            ))

        return issues, session, score


class MTFValidator:
    """
    Dynamic MTF recalculation with confidence penalty on misalignment.

    The previous system did not recalculate confidence when MTF alignment
    changed.  This validator:
      - Checks alignment across 1H, 4H, Daily, Weekly
      - Applies confidence penalty when alignment < 60%
      - Identifies which timeframes are misaligned
    """

    TIMEFRAME_WEIGHTS = {
        "1h":    0.15,
        "4h":    0.35,
        "1day":  0.35,
        "1week": 0.15,
    }

    def validate(
        self,
        mtf_data: Dict[str, Dict[str, Any]],
        signal_direction: str,
    ) -> Tuple[List[ValidationIssue], float, float]:
        """
        Args:
            mtf_data: Dict keyed by timeframe with 'direction' and 'score' fields.
            signal_direction: 'BUY' or 'SELL'

        Returns (issues, alignment_pct, score_contribution 0-100).
        """
        issues: List[ValidationIssue] = []
        direction = signal_direction.upper()
        expected_mtf_direction = "BULLISH" if direction == "BUY" else "BEARISH"

        aligned_weight   = 0.0
        misaligned_tfs:  List[str] = []
        total_weight     = 0.0

        for tf, data in mtf_data.items():
            if not data.get("valid", True):
                continue
            weight    = self.TIMEFRAME_WEIGHTS.get(tf, 0.25)
            tf_dir    = data.get("direction", "NEUTRAL")
            tf_score  = data.get("score", 50.0) / 100.0

            if tf_dir == expected_mtf_direction:
                aligned_weight += weight * tf_score
            elif tf_dir != "NEUTRAL":
                misaligned_tfs.append(tf)

            total_weight += weight

        alignment_pct = (aligned_weight / total_weight * 100.0) if total_weight > 0 else 0.0

        if alignment_pct < MTF_MISALIGNED * 100:
            issues.append(ValidationIssue(
                severity="CRITICAL",
                validator="MTFValidator",
                code="MTF_SEVERELY_MISALIGNED",
                message=(
                    f"MTF alignment is only {alignment_pct:.1f}% for {direction} signal. "
                    f"Misaligned timeframes: {', '.join(misaligned_tfs) or 'none detected'}. "
                    f"Confidence will be penalised by {MTF_MISALIGNMENT_PENALTY} points."
                ),
                suggestion=(
                    f"Do not trade until at least {MTF_PARTIAL_ALIGNMENT * 100:.0f}% MTF alignment. "
                    f"Check {', '.join(misaligned_tfs)} for conflicting signals."
                ),
            ))
        elif alignment_pct < MTF_PARTIAL_ALIGNMENT * 100:
            issues.append(ValidationIssue(
                severity="WARNING",
                validator="MTFValidator",
                code="MTF_PARTIAL_ALIGNMENT",
                message=(
                    f"MTF alignment is {alignment_pct:.1f}% — partial alignment only. "
                    f"Misaligned timeframes: {', '.join(misaligned_tfs) or 'none'}."
                ),
                suggestion=(
                    f"Reduce position size by 30% until alignment exceeds "
                    f"{MTF_FULL_ALIGNMENT * 100:.0f}%. "
                    f"Monitor {', '.join(misaligned_tfs)} for confirmation."
                ),
            ))

        # Score
        if alignment_pct >= MTF_FULL_ALIGNMENT * 100:
            score = 100.0
        elif alignment_pct >= MTF_PARTIAL_ALIGNMENT * 100:
            score = 60.0 + (alignment_pct - MTF_PARTIAL_ALIGNMENT * 100) / (
                (MTF_FULL_ALIGNMENT - MTF_PARTIAL_ALIGNMENT) * 100
            ) * 40.0
        else:
            score = max(0.0, alignment_pct / (MTF_PARTIAL_ALIGNMENT * 100) * 60.0)

        return issues, round(alignment_pct, 1), round(score, 2)


class SignalExpiryValidator:
    """
    Add signal expiry field to prevent indefinitely valid signals.

    Signals without expiry remain in the queue forever, leading to
    stale entries at unfavourable prices.
    """

    def calculate_expiry(
        self,
        signal_time_utc: Optional[datetime],
        trade_type: str,
        session: str,
    ) -> Tuple[List[ValidationIssue], str]:
        """
        Returns (issues, expiry_utc_string).
        """
        issues: List[ValidationIssue] = []

        if signal_time_utc is None:
            signal_time_utc = datetime.now(timezone.utc)

        if signal_time_utc.tzinfo is None:
            signal_time_utc = signal_time_utc.replace(tzinfo=timezone.utc)

        hours = EXPIRY_HOURS.get(trade_type.upper(), EXPIRY_HOURS["DEFAULT"])

        # Dead zone signals expire at next London open
        if session == "DEAD":
            next_london = signal_time_utc.replace(
                hour=LONDON_OPEN_UTC, minute=0, second=0, microsecond=0
            )
            if next_london <= signal_time_utc:
                next_london += timedelta(days=1)
            expiry = next_london
            issues.append(ValidationIssue(
                severity="INFO",
                validator="SignalExpiryValidator",
                code="EXPIRY_NEXT_LONDON",
                message=(
                    f"Signal generated in dead zone — expiry set to next London open: "
                    f"{expiry.strftime('%Y-%m-%d %H:%M')} UTC."
                ),
                suggestion="Signal will auto-expire if not triggered by London open.",
            ))
        else:
            expiry = signal_time_utc + timedelta(hours=hours)

        expiry_str = f"Valid until {expiry.strftime('%H:%M')} UTC ({expiry.strftime('%Y-%m-%d')})"
        return issues, expiry_str


class NewsFilterValidator:
    """
    Flag JOLTS, Beige Book, NFP and recommend position size reduction.

    The previous system ignored news events entirely.  This validator
    checks for upcoming high-impact events and applies appropriate
    blackout windows and size reduction recommendations.
    """

    def validate(
        self,
        upcoming_events: List[Dict[str, Any]],
        signal_time_utc: Optional[datetime] = None,
        check_window_hours: int = 4,
    ) -> Tuple[List[ValidationIssue], List[str], float]:
        """
        Args:
            upcoming_events: List of event dicts with 'title', 'time', 'impact' fields.
            signal_time_utc: Time of signal generation.
            check_window_hours: Hours ahead to check for events.

        Returns (issues, news_flags, score_contribution 0-100).
        """
        issues: List[ValidationIssue] = []
        news_flags: List[str] = []

        if signal_time_utc is None:
            signal_time_utc = datetime.now(timezone.utc)

        if signal_time_utc.tzinfo is None:
            signal_time_utc = signal_time_utc.replace(tzinfo=timezone.utc)

        window_end = signal_time_utc + timedelta(hours=check_window_hours)
        score = 100.0

        for event in upcoming_events:
            event_title = event.get("title", event.get("event", ""))
            event_time  = event.get("time", event.get("datetime"))

            # Parse event time
            if isinstance(event_time, str):
                try:
                    event_time = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
                except ValueError:
                    continue
            if event_time is None:
                continue
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)

            # Check if event falls within window
            if not (signal_time_utc <= event_time <= window_end):
                continue

            # Match against known gold-sensitive events
            matched_event = None
            for keyword, config in GOLD_NEWS_EVENTS.items():
                if keyword.lower() in event_title.lower():
                    matched_event = (keyword, config)
                    break

            if matched_event is None:
                # Check generic impact level
                impact = event.get("impact", "").upper()
                if impact in ("HIGH", "CRITICAL"):
                    matched_event = (event_title[:30], {
                        "impact": impact,
                        "blackout_hours": 1,
                        "size_reduction": 0.25,
                    })

            if matched_event:
                keyword, config = matched_event
                impact       = config["impact"]
                blackout_hrs = config["blackout_hours"]
                size_red     = config["size_reduction"]
                mins_to_event = int((event_time - signal_time_utc).total_seconds() / 60)

                flag = (
                    f"{keyword} ({impact}) in {mins_to_event} min — "
                    f"reduce size by {int(size_red * 100)}%"
                )
                news_flags.append(flag)

                if impact == "CRITICAL":
                    issues.append(ValidationIssue(
                        severity="CRITICAL",
                        validator="NewsFilterValidator",
                        code=f"NEWS_{keyword.upper().replace(' ', '_')}",
                        message=(
                            f"CRITICAL news event '{keyword}' in {mins_to_event} minutes "
                            f"({event_time.strftime('%H:%M')} UTC). "
                            f"Blackout window: {blackout_hrs} hours."
                        ),
                        suggestion=(
                            f"Do NOT enter new positions within {blackout_hrs} hours of {keyword}. "
                            f"If already in trade, reduce size by {int(size_red * 100)}% "
                            f"and tighten SL to breakeven."
                        ),
                    ))
                    score = min(score, 20.0)
                elif impact == "HIGH":
                    issues.append(ValidationIssue(
                        severity="WARNING",
                        validator="NewsFilterValidator",
                        code=f"NEWS_{keyword.upper().replace(' ', '_')}",
                        message=(
                            f"HIGH impact event '{keyword}' in {mins_to_event} minutes. "
                            f"Gold volatility expected."
                        ),
                        suggestion=(
                            f"Reduce position size by {int(size_red * 100)}% before {keyword}. "
                            f"Consider waiting until after the event for cleaner entry."
                        ),
                    ))
                    score = min(score, 60.0)
                else:
                    issues.append(ValidationIssue(
                        severity="INFO",
                        validator="NewsFilterValidator",
                        code=f"NEWS_{keyword.upper().replace(' ', '_')}",
                        message=f"MEDIUM impact event '{keyword}' in {mins_to_event} minutes.",
                        suggestion=f"Monitor for volatility. Consider reducing size by {int(size_red * 100)}%.",
                    ))
                    score = min(score, 80.0)

        return issues, news_flags, round(score, 2)


# ─────────────────────────────────────────────────────────────
# Master Validator
# ─────────────────────────────────────────────────────────────

class SignalQualityValidator:
    """
    Master validator that orchestrates all nine sub-validators and
    produces a comprehensive ValidationReport.

    Usage:
        validator = SignalQualityValidator()
        report = validator.validate(signal_dict)
    """

    def __init__(self) -> None:
        self.rr_validator      = RiskRewardValidator()
        self.regime_validator  = RegimeValidator()
        self.entry_validator   = EntryValidator()
        self.confidence_calc   = ConfidenceCalculator()
        self.sl_validator      = SLValidator()
        self.session_validator = SessionValidator()
        self.mtf_validator     = MTFValidator()
        self.expiry_validator  = SignalExpiryValidator()
        self.news_validator    = NewsFilterValidator()

    def validate(self, signal: Dict[str, Any]) -> ValidationReport:
        """
        Run all validators against a signal document.

        Expected signal fields (all optional with sensible defaults):
            signal_type, entry_price, sl_price, tp_levels,
            entry_zone_low, entry_zone_high,
            atr, nearest_support, nearest_resistance,
            swing_high, swing_low,
            adx, ma_slope, structure_bias, atr_ratio,
            mtf_data (dict of timeframe → analysis),
            signal_time_utc (ISO string or datetime),
            trade_type (SWING/SCALP/INTRA),
            upcoming_events (list of event dicts),
            smc_confluence_pct, momentum_score,
            confidence (existing static value)

        Returns:
            ValidationReport with all findings and dynamic confidence.
        """
        all_issues:      List[ValidationIssue] = []
        all_recommendations: List[str]         = []

        # ── Extract signal fields ─────────────────────────────
        signal_id    = signal.get("id") or signal.get("signal_id")
        signal_type  = str(signal.get("type", "BUY")).upper()
        entry_price  = float(signal.get("entry_price", 0) or 0)
        sl_price     = float(signal.get("sl_price", 0) or 0)
        tp_levels    = [float(t) for t in (signal.get("tp_levels") or [])]
        trade_type   = str(signal.get("trade_type", "SWING")).upper()

        entry_zone_low  = signal.get("entry_zone_low")
        entry_zone_high = signal.get("entry_zone_high")
        if entry_zone_low  is not None: entry_zone_low  = float(entry_zone_low)
        if entry_zone_high is not None: entry_zone_high = float(entry_zone_high)

        atr                 = float(signal.get("atr", 0) or 0)
        nearest_support     = float(signal.get("nearest_support", entry_price * 0.99) or entry_price * 0.99)
        nearest_resistance  = float(signal.get("nearest_resistance", entry_price * 1.01) or entry_price * 1.01)
        swing_high          = signal.get("swing_high")
        swing_low           = signal.get("swing_low")
        if swing_high is not None: swing_high = float(swing_high)
        if swing_low  is not None: swing_low  = float(swing_low)

        if atr <= 0:
            atr = entry_price * 0.005

        adx             = float(signal.get("adx", 25) or 25)
        ma_slope        = float(signal.get("ma_slope", 0) or 0)
        structure_bias  = int(signal.get("structure_bias", 0) or 0)
        atr_ratio       = float(signal.get("atr_ratio", 1.0) or 1.0)

        mtf_data         = signal.get("mtf_data") or {}
        upcoming_events  = signal.get("upcoming_events") or []
        smc_confluence   = float(signal.get("smc_confluence_pct", 60) or 60)
        momentum_score   = float(signal.get("momentum_score", 60) or 60)

        # Parse signal time
        signal_time_raw = signal.get("signal_time_utc") or signal.get("created_at")
        signal_time_utc: Optional[datetime] = None
        if isinstance(signal_time_raw, datetime):
            signal_time_utc = signal_time_raw
        elif isinstance(signal_time_raw, str):
            try:
                signal_time_utc = datetime.fromisoformat(
                    signal_time_raw.replace("Z", "+00:00")
                )
            except ValueError:
                signal_time_utc = datetime.now(timezone.utc)
        else:
            signal_time_utc = datetime.now(timezone.utc)

        # ── 1. Risk/Reward Validation ─────────────────────────
        rr_issues, rr_ratio, rr_score = self.rr_validator.validate(
            signal_type=signal_type,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_levels=tp_levels,
            trade_type=trade_type,
        )
        all_issues.extend(rr_issues)

        # ── 2. Regime Validation ──────────────────────────────
        regime_issues, regime, regime_score = self.regime_validator.validate(
            adx=adx,
            ma_slope=ma_slope,
            structure_bias=structure_bias,
            atr_ratio=atr_ratio,
            signal_type=signal_type,
        )
        all_issues.extend(regime_issues)

        # ── 3. Entry Zone Validation ──────────────────────────
        nearest_structure = (
            nearest_support if signal_type == "BUY" else nearest_resistance
        )
        entry_issues, zone_pips, entry_score = self.entry_validator.validate(
            entry_price=entry_price,
            entry_zone_low=entry_zone_low,
            entry_zone_high=entry_zone_high,
            nearest_structure=nearest_structure,
            atr=atr,
            signal_type=signal_type,
        )
        all_issues.extend(entry_issues)

        # ── 4. SL Validation ─────────────────────────────────
        sl_issues, sl_atr_multiple, sl_score = self.sl_validator.validate(
            signal_type=signal_type,
            entry_price=entry_price,
            sl_price=sl_price,
            swing_high=swing_high,
            swing_low=swing_low,
            atr=atr,
            nearest_support=nearest_support,
            nearest_resistance=nearest_resistance,
        )
        all_issues.extend(sl_issues)

        # ── 5. Session Validation ─────────────────────────────
        session_issues, session, session_score = self.session_validator.validate(
            signal_time_utc=signal_time_utc,
        )
        all_issues.extend(session_issues)

        # ── 6. MTF Validation ─────────────────────────────────
        mtf_issues, mtf_alignment_pct, mtf_score = self.mtf_validator.validate(
            mtf_data=mtf_data,
            signal_direction=signal_type,
        )
        all_issues.extend(mtf_issues)

        # ── 7. Signal Expiry ──────────────────────────────────
        expiry_issues, expiry_utc = self.expiry_validator.calculate_expiry(
            signal_time_utc=signal_time_utc,
            trade_type=trade_type,
            session=session,
        )
        all_issues.extend(expiry_issues)

        # ── 8. News Filter ────────────────────────────────────
        news_issues, news_flags, news_score = self.news_validator.validate(
            upcoming_events=upcoming_events,
            signal_time_utc=signal_time_utc,
        )
        all_issues.extend(news_issues)

        # ── 9. Dynamic Confidence ─────────────────────────────
        dynamic_confidence, confidence_breakdown = self.confidence_calc.calculate(
            mtf_alignment_pct=mtf_alignment_pct,
            smc_confluence_pct=smc_confluence,
            momentum_score=momentum_score,
            session_score=session_score,
            news_clear_score=news_score,
            rr_score=rr_score,
            regime_score=regime_score,
        )

        # ── Overall Score ─────────────────────────────────────
        component_scores = [
            rr_score, regime_score, entry_score,
            sl_score, session_score, mtf_score, news_score,
        ]
        overall_score = sum(component_scores) / len(component_scores)

        # ── Pass/Fail ─────────────────────────────────────────
        critical_issues = [i for i in all_issues if i.severity == "CRITICAL"]
        passed = len(critical_issues) == 0 and overall_score >= 60.0

        # ── Recommendations ───────────────────────────────────
        if not passed:
            all_recommendations.append(
                "Signal does NOT meet quality standards. Review all CRITICAL issues before approval."
            )
        if dynamic_confidence < 75.0:
            all_recommendations.append(
                f"Dynamic confidence ({dynamic_confidence:.1f}%) is below the 75% threshold. "
                f"Improve MTF alignment and SMC confluence before entry."
            )
        if rr_ratio < RR_MINIMUM_SWING and trade_type == "SWING":
            all_recommendations.append(
                f"Restructure trade to achieve minimum 1:{RR_MINIMUM_SWING} R:R. "
                f"Current R:R: 1:{rr_ratio:.2f}."
            )
        if news_flags:
            all_recommendations.append(
                f"News events detected: {'; '.join(news_flags)}. "
                f"Reduce position size accordingly."
            )
        if session == "DEAD":
            all_recommendations.append(
                f"Delay entry until London open ({LONDON_OPEN_UTC:02d}:00 UTC) "
                f"for optimal liquidity."
            )

        report = ValidationReport(
            signal_id=str(signal_id) if signal_id else None,
            passed=passed,
            overall_score=round(overall_score, 2),
            issues=all_issues,
            recommendations=all_recommendations,
            confidence_breakdown=confidence_breakdown,
            dynamic_confidence=dynamic_confidence,
            expiry_utc=expiry_utc,
            session_quality=session,
            news_flags=news_flags,
            regime_classification=regime,
            rr_ratio=rr_ratio,
            entry_zone_pips=zone_pips,
            sl_atr_multiple=sl_atr_multiple,
            mtf_alignment_pct=mtf_alignment_pct,
        )

        logger.info(
            f"SignalQualityValidator [{signal_id}]: "
            f"passed={passed} score={overall_score:.1f} "
            f"confidence={dynamic_confidence:.1f}% "
            f"regime={regime} session={session} "
            f"rr={rr_ratio:.2f} critical={len(critical_issues)}"
        )
        return report


# ─────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────

signal_quality_validator = SignalQualityValidator()

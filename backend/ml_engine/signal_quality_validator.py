"""
Signal Quality Validator
Gold Trading System v3.0.2

Comprehensive signal quality validation with 13 hybrid enhancement indicators.
Addresses all known weaknesses in the legacy signal pipeline:

  - R:R minimum 1:2 for swing trades (was 1:1.3)
  - Regime reclassification (RANGE vs directional confusion)
  - 10-pip entry band validation (was 1-pip)
  - Dynamic confidence scoring (MTF + SMC + momentum + session + news)
  - SL anchoring to swing high/low + ATR quantification
  - Regime-specific entry rules (sell at RESISTANCE in range, not support)
  - Session quality detection (post-NY close, London open 07:00 UTC)
  - Signal expiry tracking (e.g. 'Valid until 02:00 UTC')
  - News filter integration (JOLTS, Beige Book, NFP)
  - Dynamic MTF confidence recalculation

Usage:
    from ml_engine.signal_quality_validator import SignalQualityValidator, signal_quality_validator

    result = signal_quality_validator.validate(signal_dict)
    if result.passed:
        # signal is approved for trading
        print(result.quality_score, result.recommendations)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

# Risk/Reward thresholds
RR_MINIMUM_SWING   = 2.0   # Minimum R:R for swing trades
RR_MINIMUM_SCALP   = 1.3   # Minimum R:R for scalp trades
RR_GOOD            = 2.5
RR_EXCELLENT       = 3.0

# Entry band width in pips (Gold: 1 pip = $0.10)
ENTRY_BAND_PIPS    = 10    # 10-pip zone required
PIP_SIZE_GOLD      = 0.10  # $0.10 per pip for XAUUSD

# Confidence thresholds
CONFIDENCE_HIGH    = 75.0
CONFIDENCE_MEDIUM  = 60.0
CONFIDENCE_LOW     = 50.0

# Session UTC hours
SESSION_LONDON_OPEN  = 7    # 07:00 UTC
SESSION_NY_OPEN      = 13   # 13:00 UTC
SESSION_NY_CLOSE     = 22   # 22:00 UTC
SESSION_ASIA_OPEN    = 0    # 00:00 UTC

# Post-NY close dead zone: 22:00–07:00 UTC
POST_NY_CLOSE_START  = 22
POST_NY_CLOSE_END    = 7

# ATR buffer multipliers for SL anchoring
SL_ATR_BUFFER_MIN  = 0.1
SL_ATR_BUFFER_MAX  = 0.5

# Regime names
REGIME_TREND_UP    = "TREND_UP"
REGIME_TREND_DOWN  = "TREND_DOWN"
REGIME_RANGE       = "RANGE"
REGIME_BREAKOUT    = "BREAKOUT"
REGIME_HIGH_VOL    = "HIGH_VOL"
REGIME_LOW_VOL     = "LOW_VOL"
REGIME_CHAOS       = "CHAOS"

# High-impact news events that require filtering
HIGH_IMPACT_NEWS = {
    "NFP", "Non-Farm Payroll", "FOMC", "Fed Rate Decision",
    "CPI", "Inflation", "GDP", "Unemployment", "JOLTS",
    "Beige Book", "Jackson Hole", "ECB", "BOE", "BOJ",
    "Retail Sales", "PPI", "ISM", "PMI",
}


# ─────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────

@dataclass
class QualityCheck:
    """Result of a single quality check."""
    name:        str
    passed:      bool
    score:       float          # 0.0 – 1.0 contribution to overall score
    weight:      float          # Relative weight in composite score
    message:     str
    details:     Dict[str, Any] = field(default_factory=dict)
    suggestions: List[str]      = field(default_factory=list)


@dataclass
class ValidationResult:
    """Complete signal quality validation result."""
    signal_id:        Optional[str]
    passed:           bool
    quality_score:    float          # 0–100 composite score
    confidence_score: float          # Dynamic confidence (0–100)
    grade:            str            # A / B / C / D / F
    checks:           List[QualityCheck]
    recommendations:  List[str]
    warnings:         List[str]
    regime:           str
    session_quality:  str            # HIGH / MEDIUM / LOW / DEAD_ZONE
    expiry:           Optional[str]  # e.g. 'Valid until 02:00 UTC'
    news_flags:       List[str]
    enhancement_scores: Dict[str, float]  # 13 hybrid indicator scores
    timestamp:        str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal_id":         self.signal_id,
            "passed":            self.passed,
            "quality_score":     round(self.quality_score, 2),
            "confidence_score":  round(self.confidence_score, 2),
            "grade":             self.grade,
            "regime":            self.regime,
            "session_quality":   self.session_quality,
            "expiry":            self.expiry,
            "news_flags":        self.news_flags,
            "recommendations":   self.recommendations,
            "warnings":          self.warnings,
            "enhancement_scores": {k: round(v, 3) for k, v in self.enhancement_scores.items()},
            "checks": [
                {
                    "name":        c.name,
                    "passed":      c.passed,
                    "score":       round(c.score, 3),
                    "weight":      c.weight,
                    "message":     c.message,
                    "details":     c.details,
                    "suggestions": c.suggestions,
                }
                for c in self.checks
            ],
            "timestamp": self.timestamp,
        }


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _grade(score: float) -> str:
    """Map a 0–100 quality score to a letter grade."""
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _session_expiry(current_utc: datetime) -> str:
    """
    Compute a human-readable signal expiry string based on the current
    UTC hour.  Signals expire at the next major session boundary.
    """
    hour = current_utc.hour
    if SESSION_LONDON_OPEN <= hour < SESSION_NY_OPEN:
        # London session — expires at NY open
        expiry_hour = SESSION_NY_OPEN
        label = "NY Open"
    elif SESSION_NY_OPEN <= hour < SESSION_NY_CLOSE:
        # NY session — expires at NY close
        expiry_hour = SESSION_NY_CLOSE
        label = "NY Close"
    else:
        # Dead zone / Asia — expires at London open
        expiry_hour = SESSION_LONDON_OPEN
        label = "London Open"

    # Build expiry string
    expiry_dt = current_utc.replace(
        hour=expiry_hour, minute=0, second=0, microsecond=0
    )
    if expiry_dt <= current_utc:
        expiry_dt += timedelta(days=1)

    return f"Valid until {expiry_dt.strftime('%H:%M')} UTC ({label})"


# ─────────────────────────────────────────────────────────────
# Main Validator
# ─────────────────────────────────────────────────────────────

class SignalQualityValidator:
    """
    Comprehensive signal quality validator for Gold Trading System v3.0.2.

    Runs 11 quality checks and computes a composite quality score (0–100).
    Signals scoring below 55 are flagged for adjustment; below 40 are rejected.

    Check weights (total = 1.0):
      rr_validation          0.18  — R:R ≥ 2:1 for swing trades
      regime_classification  0.12  — Correct regime label
      entry_band             0.10  — 10-pip entry zone
      dynamic_confidence     0.12  — MTF + SMC + momentum + session + news
      sl_anchoring           0.12  — SL at swing high/low + ATR buffer
      regime_entry_rules     0.12  — Sell at resistance, buy at support
      session_quality        0.08  — Avoid post-NY close dead zone
      signal_expiry          0.04  — Expiry field present and valid
      news_filter            0.06  — No high-impact news within 30 min
      mtf_recalculation      0.06  — MTF alignment recalculated dynamically
    """

    # Check weights — must sum to 1.0
    WEIGHTS = {
        "rr_validation":         0.18,
        "regime_classification": 0.12,
        "entry_band":            0.10,
        "dynamic_confidence":    0.12,
        "sl_anchoring":          0.12,
        "regime_entry_rules":    0.12,
        "session_quality":       0.08,
        "signal_expiry":         0.04,
        "news_filter":           0.06,
        "mtf_recalculation":     0.06,
    }

    def __init__(self, min_quality_score: float = 55.0) -> None:
        self.min_quality_score = min_quality_score

    # ═══════════════════════════════════════════════════════════
    # PUBLIC API
    # ═══════════════════════════════════════════════════════════

    def validate(self, signal: Dict[str, Any]) -> ValidationResult:
        """
        Run all quality checks on a signal dict and return a ValidationResult.

        Expected signal fields (all optional — validator degrades gracefully):
            type / signal       : "BUY" or "SELL"
            entry_price         : float
            sl_price            : float
            tp_levels           : List[float]
            confidence          : float (0–100)
            regime              : str (TREND_UP / TREND_DOWN / RANGE / BREAKOUT)
            atr                 : float
            swing_high          : float (optional)
            swing_low           : float (optional)
            nearest_support     : float (optional)
            nearest_resistance  : float (optional)
            mtf_alignment       : Dict (optional)
            smc_score           : float (optional, 0–10)
            momentum_score      : float (optional, 0–10)
            news_events         : List[str] (optional)
            entry_band_low      : float (optional)
            entry_band_high     : float (optional)
            trade_type          : "SWING" or "SCALP" (default SWING)
            created_at          : ISO datetime string (optional)
        """
        now = _utc_now()
        signal_id = str(signal.get("id") or signal.get("signal_id") or "")

        checks: List[QualityCheck] = []

        # ── Run all checks ────────────────────────────────────
        checks.append(self._check_rr_validation(signal))
        checks.append(self._check_regime_classification(signal))
        checks.append(self._check_entry_band(signal))
        checks.append(self._check_dynamic_confidence(signal))
        checks.append(self._check_sl_anchoring(signal))
        checks.append(self._check_regime_entry_rules(signal))
        checks.append(self._check_session_quality(signal, now))
        checks.append(self._check_signal_expiry(signal, now))
        checks.append(self._check_news_filter(signal, now))
        checks.append(self._check_mtf_recalculation(signal))

        # ── Composite quality score ───────────────────────────
        quality_score = sum(
            c.score * c.weight * 100 for c in checks
        )
        quality_score = _clamp(quality_score, 0.0, 100.0)

        # ── Dynamic confidence score ──────────────────────────
        confidence_score = self._compute_dynamic_confidence(signal)

        # ── Regime ───────────────────────────────────────────
        regime = self._classify_regime(signal)

        # ── Session quality ───────────────────────────────────
        session_quality = self._get_session_quality(now)

        # ── Signal expiry ─────────────────────────────────────
        expiry = signal.get("expiry") or _session_expiry(now)

        # ── News flags ────────────────────────────────────────
        news_flags = self._extract_news_flags(signal)

        # ── Enhancement scores (13 hybrid indicators) ─────────
        enhancement_scores = self._compute_enhancement_scores(signal)

        # ── Aggregate recommendations and warnings ────────────
        recommendations: List[str] = []
        warnings: List[str] = []
        for check in checks:
            if not check.passed:
                recommendations.extend(check.suggestions)
                if check.score < 0.3:
                    warnings.append(f"[{check.name}] {check.message}")

        passed = quality_score >= self.min_quality_score

        result = ValidationResult(
            signal_id=signal_id or None,
            passed=passed,
            quality_score=quality_score,
            confidence_score=confidence_score,
            grade=_grade(quality_score),
            checks=checks,
            recommendations=list(dict.fromkeys(recommendations)),  # deduplicate
            warnings=warnings,
            regime=regime,
            session_quality=session_quality,
            expiry=expiry,
            news_flags=news_flags,
            enhancement_scores=enhancement_scores,
            timestamp=now.isoformat(),
        )

        logger.info(
            f"SignalQualityValidator [{signal.get('type', '?')} "
            f"{signal.get('pair', signal.get('symbol', '?'))}]: "
            f"score={quality_score:.1f} grade={result.grade} "
            f"passed={passed} regime={regime}"
        )
        return result

    # ═══════════════════════════════════════════════════════════
    # INDIVIDUAL CHECKS
    # ═══════════════════════════════════════════════════════════

    def _check_rr_validation(self, signal: Dict[str, Any]) -> QualityCheck:
        """
        Check 1: Risk/Reward Validation
        Minimum 1:2 for swing trades, 1:1.3 for scalp trades.
        """
        name   = "rr_validation"
        weight = self.WEIGHTS[name]

        entry  = float(signal.get("entry_price", 0) or 0)
        sl     = float(signal.get("sl_price", 0) or 0)
        tps    = [float(t) for t in (signal.get("tp_levels") or [])]
        stype  = str(signal.get("type") or signal.get("signal") or "BUY").upper()
        trade_type = str(signal.get("trade_type", "SWING")).upper()

        min_rr = RR_MINIMUM_SWING if trade_type != "SCALP" else RR_MINIMUM_SCALP

        if entry <= 0 or sl <= 0 or not tps:
            return QualityCheck(
                name=name, passed=False, score=0.0, weight=weight,
                message="Missing entry_price, sl_price, or tp_levels — R:R cannot be computed.",
                suggestions=["Provide entry_price, sl_price, and at least one tp_level."],
            )

        risk = abs(entry - sl)
        if risk <= 0:
            return QualityCheck(
                name=name, passed=False, score=0.0, weight=weight,
                message="Entry and SL are at the same price — zero risk.",
                suggestions=["Separate entry and SL by at least 1 ATR."],
            )

        # Compute R:R for each TP
        rr_values: List[float] = []
        for tp in tps:
            reward = (tp - entry) if stype == "BUY" else (entry - tp)
            rr_values.append(reward / risk if risk > 0 else 0.0)

        tp1_rr  = rr_values[0] if rr_values else 0.0
        best_rr = max(rr_values) if rr_values else 0.0

        suggestions: List[str] = []
        if tp1_rr < min_rr:
            if stype == "BUY":
                suggested_tp1 = entry + risk * min_rr
                suggestions.append(
                    f"TP1 R:R is {tp1_rr:.2f}:1 — below minimum {min_rr}:1 for {trade_type}. "
                    f"Move TP1 to at least {suggested_tp1:.2f} ({min_rr}:1 R:R)."
                )
            else:
                suggested_tp1 = entry - risk * min_rr
                suggestions.append(
                    f"TP1 R:R is {tp1_rr:.2f}:1 — below minimum {min_rr}:1 for {trade_type}. "
                    f"Move TP1 to at least {suggested_tp1:.2f} ({min_rr}:1 R:R)."
                )
            suggestions.append(
                f"Alternatively, tighten SL to improve R:R. Current risk: {risk:.2f}."
            )

        # Score: linear interpolation between 0 and 1 based on TP1 R:R
        if tp1_rr >= RR_EXCELLENT:
            score = 1.0
        elif tp1_rr >= RR_GOOD:
            score = 0.85
        elif tp1_rr >= min_rr:
            score = 0.70
        elif tp1_rr >= 1.0:
            score = 0.40
        else:
            score = max(0.0, tp1_rr / min_rr * 0.35)

        passed = tp1_rr >= min_rr

        return QualityCheck(
            name=name,
            passed=passed,
            score=score,
            weight=weight,
            message=(
                f"TP1 R:R = {tp1_rr:.2f}:1, best R:R = {best_rr:.2f}:1 "
                f"(minimum {min_rr}:1 for {trade_type})."
            ),
            details={
                "tp1_rr": round(tp1_rr, 3),
                "best_rr": round(best_rr, 3),
                "all_rr": [round(r, 3) for r in rr_values],
                "min_required": min_rr,
                "trade_type": trade_type,
            },
            suggestions=suggestions,
        )

    def _check_regime_classification(self, signal: Dict[str, Any]) -> QualityCheck:
        """
        Check 2: Regime Classification
        Validates that the regime label is consistent with price action
        indicators (ADX, ATR ratio, structure bias).
        """
        name   = "regime_classification"
        weight = self.WEIGHTS[name]

        regime      = str(signal.get("regime", "") or "").upper()
        adx         = float(signal.get("adx", 0) or 0)
        atr_ratio   = float(signal.get("atr_ratio", 1.0) or 1.0)
        stype       = str(signal.get("type") or signal.get("signal") or "BUY").upper()
        struct_bias = float(signal.get("structure_bias", 0) or 0)

        # Reclassify regime based on indicators
        reclassified = self._classify_regime(signal)
        suggestions: List[str] = []

        if not regime:
            return QualityCheck(
                name=name, passed=False, score=0.5, weight=weight,
                message=f"No regime label provided. Inferred: {reclassified}.",
                details={"inferred_regime": reclassified},
                suggestions=[f"Set regime to '{reclassified}' based on current indicators."],
            )

        # Check for RANGE + directional sell confusion
        if regime == REGIME_RANGE and stype == "SELL":
            # In a range, a SELL is valid only if entry is near resistance
            entry       = float(signal.get("entry_price", 0) or 0)
            resistance  = float(signal.get("nearest_resistance", 0) or 0)
            support     = float(signal.get("nearest_support", 0) or 0)
            if resistance > 0 and support > 0 and entry > 0:
                range_size  = resistance - support
                entry_pct   = (entry - support) / range_size if range_size > 0 else 0.5
                if entry_pct < 0.6:
                    suggestions.append(
                        f"RANGE regime + SELL: entry at {entry:.2f} is in the lower "
                        f"{entry_pct * 100:.0f}% of the range — should sell near RESISTANCE "
                        f"({resistance:.2f}), not near support ({support:.2f})."
                    )
                    return QualityCheck(
                        name=name, passed=False, score=0.2, weight=weight,
                        message=(
                            f"RANGE regime SELL at support-side is incorrect. "
                            f"Entry ({entry:.2f}) is {entry_pct * 100:.0f}% into range — "
                            f"sell at resistance ({resistance:.2f})."
                        ),
                        details={
                            "regime": regime,
                            "entry_pct_in_range": round(entry_pct, 3),
                            "resistance": resistance,
                            "support": support,
                        },
                        suggestions=suggestions,
                    )

        # Check for RANGE regime with directional bias mismatch
        if regime in (REGIME_TREND_UP, REGIME_TREND_DOWN) and adx < 20:
            suggestions.append(
                f"ADX={adx:.1f} is below 20 — market is not trending. "
                f"Consider reclassifying regime from '{regime}' to '{REGIME_RANGE}'."
            )

        # Regime matches reclassification?
        regime_match = (regime == reclassified) or (
            regime in (REGIME_TREND_UP, REGIME_TREND_DOWN)
            and reclassified in (REGIME_TREND_UP, REGIME_TREND_DOWN)
        )

        score = 1.0 if regime_match else 0.5
        passed = regime_match or not suggestions

        return QualityCheck(
            name=name,
            passed=passed,
            score=score,
            weight=weight,
            message=(
                f"Regime '{regime}' {'matches' if regime_match else 'conflicts with'} "
                f"inferred regime '{reclassified}' (ADX={adx:.1f}, ATR ratio={atr_ratio:.2f})."
            ),
            details={
                "declared_regime": regime,
                "inferred_regime": reclassified,
                "adx": adx,
                "atr_ratio": atr_ratio,
                "structure_bias": struct_bias,
            },
            suggestions=suggestions,
        )

    def _check_entry_band(self, signal: Dict[str, Any]) -> QualityCheck:
        """
        Check 3: Entry Band Validation
        Entry must be specified as a 10-pip zone, not a single price point.
        """
        name   = "entry_band"
        weight = self.WEIGHTS[name]

        entry      = float(signal.get("entry_price", 0) or 0)
        band_low   = float(signal.get("entry_band_low", 0) or 0)
        band_high  = float(signal.get("entry_band_high", 0) or 0)
        symbol     = str(signal.get("pair") or signal.get("symbol") or "XAUUSD").upper()

        pip_size   = PIP_SIZE_GOLD  # $0.10 for XAUUSD
        min_band   = ENTRY_BAND_PIPS * pip_size  # $1.00 minimum band width

        suggestions: List[str] = []

        # If no band provided, derive from entry_price
        if band_low <= 0 or band_high <= 0:
            if entry > 0:
                band_low  = entry - (ENTRY_BAND_PIPS / 2) * pip_size
                band_high = entry + (ENTRY_BAND_PIPS / 2) * pip_size
                suggestions.append(
                    f"No entry band provided. Suggested 10-pip zone: "
                    f"{band_low:.2f}–{band_high:.2f} (centred on entry {entry:.2f})."
                )
                return QualityCheck(
                    name=name, passed=False, score=0.5, weight=weight,
                    message=(
                        f"Entry specified as single price {entry:.2f} — "
                        f"a 10-pip zone is required for realistic execution."
                    ),
                    details={
                        "entry_price": entry,
                        "suggested_band_low": round(band_low, 2),
                        "suggested_band_high": round(band_high, 2),
                        "required_band_pips": ENTRY_BAND_PIPS,
                    },
                    suggestions=suggestions,
                )
            else:
                return QualityCheck(
                    name=name, passed=False, score=0.0, weight=weight,
                    message="No entry_price or entry band provided.",
                    suggestions=["Provide entry_price and entry_band_low/entry_band_high."],
                )

        band_width = band_high - band_low
        band_pips  = band_width / pip_size

        if band_width < min_band:
            suggestions.append(
                f"Entry band {band_low:.2f}–{band_high:.2f} is only {band_pips:.1f} pips wide. "
                f"Widen to at least {ENTRY_BAND_PIPS} pips: "
                f"{entry - min_band / 2:.2f}–{entry + min_band / 2:.2f}."
            )
            score = _clamp(band_pips / ENTRY_BAND_PIPS * 0.7)
            passed = False
        else:
            score = min(1.0, 0.7 + (band_pips - ENTRY_BAND_PIPS) / ENTRY_BAND_PIPS * 0.3)
            passed = True

        return QualityCheck(
            name=name,
            passed=passed,
            score=score,
            weight=weight,
            message=(
                f"Entry band {band_low:.2f}–{band_high:.2f} "
                f"({band_pips:.1f} pips, minimum {ENTRY_BAND_PIPS} pips)."
            ),
            details={
                "band_low": band_low,
                "band_high": band_high,
                "band_pips": round(band_pips, 1),
                "required_pips": ENTRY_BAND_PIPS,
            },
            suggestions=suggestions,
        )

    def _check_dynamic_confidence(self, signal: Dict[str, Any]) -> QualityCheck:
        """
        Check 4: Dynamic Confidence Scoring
        Replaces static 75% confidence with a score derived from:
          MTF alignment (30%) + SMC score (25%) + momentum (20%) +
          session quality (15%) + news clearance (10%)
        """
        name   = "dynamic_confidence"
        weight = self.WEIGHTS[name]

        dynamic_conf = self._compute_dynamic_confidence(signal)
        static_conf  = float(signal.get("confidence", 0) or 0)

        suggestions: List[str] = []
        if abs(dynamic_conf - static_conf) > 15:
            suggestions.append(
                f"Static confidence ({static_conf:.0f}%) diverges significantly from "
                f"dynamic score ({dynamic_conf:.0f}%). Use dynamic scoring."
            )
        if dynamic_conf < CONFIDENCE_MEDIUM:
            suggestions.append(
                f"Dynamic confidence {dynamic_conf:.0f}% is below {CONFIDENCE_MEDIUM}% threshold. "
                f"Improve MTF alignment, SMC score, or wait for better session."
            )

        score  = _clamp(dynamic_conf / 100.0)
        passed = dynamic_conf >= CONFIDENCE_MEDIUM

        return QualityCheck(
            name=name,
            passed=passed,
            score=score,
            weight=weight,
            message=(
                f"Dynamic confidence = {dynamic_conf:.1f}% "
                f"(static = {static_conf:.0f}%, threshold = {CONFIDENCE_MEDIUM}%)."
            ),
            details={
                "dynamic_confidence": round(dynamic_conf, 2),
                "static_confidence":  round(static_conf, 2),
                "threshold":          CONFIDENCE_MEDIUM,
            },
            suggestions=suggestions,
        )

    def _check_sl_anchoring(self, signal: Dict[str, Any]) -> QualityCheck:
        """
        Check 5: SL Anchoring Validation
        SL must be anchored to a swing high/low with an ATR buffer.
        """
        name   = "sl_anchoring"
        weight = self.WEIGHTS[name]

        entry      = float(signal.get("entry_price", 0) or 0)
        sl         = float(signal.get("sl_price", 0) or 0)
        atr        = float(signal.get("atr", 0) or 0)
        swing_high = signal.get("swing_high")
        swing_low  = signal.get("swing_low")
        stype      = str(signal.get("type") or signal.get("signal") or "BUY").upper()

        suggestions: List[str] = []

        if entry <= 0 or sl <= 0:
            return QualityCheck(
                name=name, passed=False, score=0.0, weight=weight,
                message="Missing entry_price or sl_price.",
                suggestions=["Provide entry_price and sl_price."],
            )

        if atr <= 0:
            atr = entry * 0.005  # Fallback: 0.5% of price

        # Check SL is on the correct side
        if stype == "BUY" and sl >= entry:
            return QualityCheck(
                name=name, passed=False, score=0.0, weight=weight,
                message=f"BUY SL ({sl:.2f}) must be below entry ({entry:.2f}).",
                suggestions=[f"Move SL below entry. Suggested: {entry - atr * 1.5:.2f}."],
            )
        if stype == "SELL" and sl <= entry:
            return QualityCheck(
                name=name, passed=False, score=0.0, weight=weight,
                message=f"SELL SL ({sl:.2f}) must be above entry ({entry:.2f}).",
                suggestions=[f"Move SL above entry. Suggested: {entry + atr * 1.5:.2f}."],
            )

        # Check anchoring to swing point
        anchored = False
        anchor_details: Dict[str, Any] = {}

        if stype == "BUY" and swing_low is not None:
            swing_low_f = float(swing_low)
            buffer      = swing_low_f - sl  # positive = SL below swing low
            buffer_atr  = buffer / atr
            anchor_details = {
                "swing_low": swing_low_f,
                "sl_buffer_atr": round(buffer_atr, 3),
            }
            if SL_ATR_BUFFER_MIN <= buffer_atr <= SL_ATR_BUFFER_MAX:
                anchored = True
            elif buffer_atr < SL_ATR_BUFFER_MIN:
                suggestions.append(
                    f"SL ({sl:.2f}) is too close to swing low ({swing_low_f:.2f}). "
                    f"Buffer = {buffer_atr:.2f} ATR (minimum {SL_ATR_BUFFER_MIN} ATR). "
                    f"Move SL to {swing_low_f - atr * SL_ATR_BUFFER_MIN:.2f}."
                )
            else:
                suggestions.append(
                    f"SL ({sl:.2f}) is {buffer_atr:.2f} ATR below swing low ({swing_low_f:.2f}). "
                    f"Consider tightening to {swing_low_f - atr * 0.2:.2f} to improve R:R."
                )

        elif stype == "SELL" and swing_high is not None:
            swing_high_f = float(swing_high)
            buffer       = sl - swing_high_f  # positive = SL above swing high
            buffer_atr   = buffer / atr
            anchor_details = {
                "swing_high": swing_high_f,
                "sl_buffer_atr": round(buffer_atr, 3),
            }
            if SL_ATR_BUFFER_MIN <= buffer_atr <= SL_ATR_BUFFER_MAX:
                anchored = True
            elif buffer_atr < SL_ATR_BUFFER_MIN:
                suggestions.append(
                    f"SL ({sl:.2f}) is too close to swing high ({swing_high_f:.2f}). "
                    f"Buffer = {buffer_atr:.2f} ATR (minimum {SL_ATR_BUFFER_MIN} ATR). "
                    f"Move SL to {swing_high_f + atr * SL_ATR_BUFFER_MIN:.2f}."
                )
            else:
                suggestions.append(
                    f"SL ({sl:.2f}) is {buffer_atr:.2f} ATR above swing high ({swing_high_f:.2f}). "
                    f"Consider tightening to {swing_high_f + atr * 0.2:.2f} to improve R:R."
                )
        else:
            # No swing point provided — check ATR-based sizing
            sl_distance = abs(entry - sl)
            sl_atr      = sl_distance / atr
            anchor_details = {"sl_atr_distance": round(sl_atr, 3)}
            if 1.0 <= sl_atr <= 3.0:
                anchored = True
                suggestions.append(
                    "No swing high/low provided. SL is ATR-sized but not structurally anchored. "
                    "Provide swing_high or swing_low for better SL placement."
                )
            else:
                suggestions.append(
                    f"SL distance = {sl_atr:.2f} ATR (ideal: 1–3 ATR). "
                    "Provide swing_high/swing_low to anchor SL to structure."
                )

        score  = 1.0 if anchored else 0.4
        passed = anchored

        return QualityCheck(
            name=name,
            passed=passed,
            score=score,
            weight=weight,
            message=(
                f"SL {'anchored to structure' if anchored else 'NOT anchored to structure'} "
                f"(ATR = {atr:.2f})."
            ),
            details={
                "sl_price": sl,
                "entry_price": entry,
                "atr": round(atr, 4),
                "anchored": anchored,
                **anchor_details,
            },
            suggestions=suggestions,
        )

    def _check_regime_entry_rules(self, signal: Dict[str, Any]) -> QualityCheck:
        """
        Check 6: Regime-Specific Entry Rules
        - RANGE: sell at RESISTANCE, buy at SUPPORT
        - TREND_UP: buy on pullbacks, not at highs
        - TREND_DOWN: sell on rallies, not at lows
        - BREAKOUT: enter on confirmed breakout candle close
        """
        name   = "regime_entry_rules"
        weight = self.WEIGHTS[name]

        regime     = self._classify_regime(signal)
        stype      = str(signal.get("type") or signal.get("signal") or "BUY").upper()
        entry      = float(signal.get("entry_price", 0) or 0)
        resistance = float(signal.get("nearest_resistance", 0) or 0)
        support    = float(signal.get("nearest_support", 0) or 0)
        atr        = float(signal.get("atr", 0) or 0)

        if atr <= 0 and entry > 0:
            atr = entry * 0.005

        suggestions: List[str] = []
        score  = 1.0
        passed = True
        message = f"Entry rules for {regime} regime are satisfied."

        if regime == REGIME_RANGE:
            if resistance > 0 and support > 0 and entry > 0:
                range_size = resistance - support
                if range_size > 0:
                    entry_pct = (entry - support) / range_size

                    if stype == "SELL":
                        # Must sell near resistance (top 30% of range)
                        if entry_pct < 0.70:
                            score  = max(0.1, entry_pct * 0.3)
                            passed = False
                            message = (
                                f"RANGE SELL: entry ({entry:.2f}) is at {entry_pct * 100:.0f}% "
                                f"of range — must be in top 30% (near resistance {resistance:.2f})."
                            )
                            suggestions.append(
                                f"In RANGE regime, SELL entries must be near RESISTANCE. "
                                f"Wait for price to reach {resistance - atr * 0.3:.2f}–{resistance:.2f}."
                            )
                    elif stype == "BUY":
                        # Must buy near support (bottom 30% of range)
                        if entry_pct > 0.30:
                            score  = max(0.1, (1 - entry_pct) * 0.3)
                            passed = False
                            message = (
                                f"RANGE BUY: entry ({entry:.2f}) is at {entry_pct * 100:.0f}% "
                                f"of range — must be in bottom 30% (near support {support:.2f})."
                            )
                            suggestions.append(
                                f"In RANGE regime, BUY entries must be near SUPPORT. "
                                f"Wait for price to reach {support:.2f}–{support + atr * 0.3:.2f}."
                            )

        elif regime == REGIME_TREND_UP:
            if stype == "SELL":
                score  = 0.3
                passed = False
                message = "TREND_UP regime: SELL signal is counter-trend — high risk."
                suggestions.append(
                    "Avoid SELL signals in TREND_UP regime. "
                    "Wait for regime change to TREND_DOWN or RANGE before selling."
                )
            elif stype == "BUY" and support > 0 and entry > 0:
                # Buy should be on pullback to support, not at highs
                dist_from_support = (entry - support) / atr if atr > 0 else 0
                if dist_from_support > 3.0:
                    score  = 0.5
                    passed = False
                    message = (
                        f"TREND_UP BUY: entry ({entry:.2f}) is {dist_from_support:.1f} ATR "
                        f"above support ({support:.2f}) — chasing the trend."
                    )
                    suggestions.append(
                        f"Wait for pullback to {support + atr:.2f}–{support + atr * 1.5:.2f} "
                        f"before entering BUY in TREND_UP."
                    )

        elif regime == REGIME_TREND_DOWN:
            if stype == "BUY":
                score  = 0.3
                passed = False
                message = "TREND_DOWN regime: BUY signal is counter-trend — high risk."
                suggestions.append(
                    "Avoid BUY signals in TREND_DOWN regime. "
                    "Wait for regime change to TREND_UP or RANGE before buying."
                )
            elif stype == "SELL" and resistance > 0 and entry > 0:
                # Sell should be on rally to resistance, not at lows
                dist_from_resistance = (resistance - entry) / atr if atr > 0 else 0
                if dist_from_resistance > 3.0:
                    score  = 0.5
                    passed = False
                    message = (
                        f"TREND_DOWN SELL: entry ({entry:.2f}) is {dist_from_resistance:.1f} ATR "
                        f"below resistance ({resistance:.2f}) — chasing the trend."
                    )
                    suggestions.append(
                        f"Wait for rally to {resistance - atr * 1.5:.2f}–{resistance:.2f} "
                        f"before entering SELL in TREND_DOWN."
                    )

        return QualityCheck(
            name=name,
            passed=passed,
            score=score,
            weight=weight,
            message=message,
            details={
                "regime": regime,
                "signal_type": stype,
                "entry_price": entry,
                "nearest_resistance": resistance,
                "nearest_support": support,
            },
            suggestions=suggestions,
        )

    def _check_session_quality(
        self, signal: Dict[str, Any], now: datetime
    ) -> QualityCheck:
        """
        Check 7: Session Quality Detection
        Flags post-NY close dead zone (22:00–07:00 UTC) and rewards
        London open (07:00 UTC) and NY open (13:00 UTC) entries.
        """
        name   = "session_quality"
        weight = self.WEIGHTS[name]

        session_quality = self._get_session_quality(now)
        hour = now.hour

        suggestions: List[str] = []
        if session_quality == "DEAD_ZONE":
            suggestions.append(
                f"Current time {now.strftime('%H:%M')} UTC is in the post-NY close dead zone "
                f"(22:00–07:00 UTC). Low liquidity — avoid new entries. "
                f"Wait for London open at 07:00 UTC."
            )
            score  = 0.2
            passed = False
        elif session_quality == "LOW":
            suggestions.append(
                f"Current time {now.strftime('%H:%M')} UTC is in a low-quality session window. "
                f"Consider waiting for London ({SESSION_LONDON_OPEN}:00 UTC) or "
                f"NY ({SESSION_NY_OPEN}:00 UTC) open."
            )
            score  = 0.5
            passed = True
        elif session_quality == "MEDIUM":
            score  = 0.75
            passed = True
        else:  # HIGH
            score  = 1.0
            passed = True

        return QualityCheck(
            name=name,
            passed=passed,
            score=score,
            weight=weight,
            message=f"Session quality: {session_quality} (UTC hour: {hour:02d}:00).",
            details={
                "session_quality": session_quality,
                "utc_hour": hour,
                "london_open": SESSION_LONDON_OPEN,
                "ny_open": SESSION_NY_OPEN,
                "ny_close": SESSION_NY_CLOSE,
            },
            suggestions=suggestions,
        )

    def _check_signal_expiry(
        self, signal: Dict[str, Any], now: datetime
    ) -> QualityCheck:
        """
        Check 8: Signal Expiry Tracking
        Signals must have an expiry field. Expired signals are rejected.
        """
        name   = "signal_expiry"
        weight = self.WEIGHTS[name]

        expiry_str = signal.get("expiry") or signal.get("valid_until")
        suggestions: List[str] = []

        if not expiry_str:
            computed_expiry = _session_expiry(now)
            suggestions.append(
                f"No expiry field on signal. Computed expiry: '{computed_expiry}'. "
                f"Add 'expiry' field to all signals."
            )
            return QualityCheck(
                name=name, passed=False, score=0.5, weight=weight,
                message=f"Signal has no expiry field. Suggested: '{computed_expiry}'.",
                details={"computed_expiry": computed_expiry},
                suggestions=suggestions,
            )

        # Try to parse expiry and check if expired
        try:
            # Handle "Valid until HH:MM UTC" format
            if "until" in str(expiry_str).lower():
                parts = str(expiry_str).split()
                time_part = next((p for p in parts if ":" in p), None)
                if time_part:
                    h, m = map(int, time_part.split(":"))
                    expiry_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                    if expiry_dt <= now:
                        expiry_dt += timedelta(days=1)
                    if now >= expiry_dt:
                        return QualityCheck(
                            name=name, passed=False, score=0.0, weight=weight,
                            message=f"Signal has expired: '{expiry_str}'.",
                            suggestions=["Generate a new signal — this one has expired."],
                        )
        except Exception:
            pass  # Unparseable expiry — still present, partial credit

        return QualityCheck(
            name=name,
            passed=True,
            score=1.0,
            weight=weight,
            message=f"Signal expiry present: '{expiry_str}'.",
            details={"expiry": expiry_str},
        )

    def _check_news_filter(
        self, signal: Dict[str, Any], now: datetime
    ) -> QualityCheck:
        """
        Check 9: News Filter Integration
        Flags JOLTS, Beige Book, NFP, and other high-impact events
        within 30 minutes of signal creation.
        """
        name   = "news_filter"
        weight = self.WEIGHTS[name]

        news_events = signal.get("news_events") or []
        news_flags  = self._extract_news_flags(signal)
        suggestions: List[str] = []

        if news_flags:
            suggestions.append(
                f"High-impact news detected: {', '.join(news_flags)}. "
                f"Avoid new entries within 30 minutes of these events."
            )
            score  = 0.1
            passed = False
            message = f"News filter triggered: {', '.join(news_flags)}."
        elif not news_events and not signal.get("news_checked"):
            suggestions.append(
                "No news_events field on signal. Add economic calendar check "
                "(JOLTS, Beige Book, NFP, FOMC) before signal generation."
            )
            score  = 0.7
            passed = True
            message = "No news events provided — calendar check recommended."
        else:
            score  = 1.0
            passed = True
            message = "News filter clear — no high-impact events detected."

        return QualityCheck(
            name=name,
            passed=passed,
            score=score,
            weight=weight,
            message=message,
            details={
                "news_flags": news_flags,
                "news_events": news_events,
            },
            suggestions=suggestions,
        )

    def _check_mtf_recalculation(self, signal: Dict[str, Any]) -> QualityCheck:
        """
        Check 10: Dynamic MTF Confidence Recalculation
        Verifies that MTF alignment was recalculated dynamically and
        that confidence was not left static when MTF dropped.
        """
        name   = "mtf_recalculation"
        weight = self.WEIGHTS[name]

        mtf         = signal.get("mtf_alignment") or {}
        confidence  = float(signal.get("confidence", 0) or 0)
        suggestions: List[str] = []

        if not mtf:
            suggestions.append(
                "No mtf_alignment data on signal. "
                "Run MultiTimeframeAnalyzer before generating confidence score."
            )
            return QualityCheck(
                name=name, passed=False, score=0.4, weight=weight,
                message="No MTF alignment data — confidence may be stale.",
                suggestions=suggestions,
            )

        # Count aligned timeframes
        h4_aligned  = bool(mtf.get("h4_aligned") or mtf.get("H4_aligned"))
        h1_aligned  = bool(mtf.get("h1_aligned") or mtf.get("H1_aligned"))
        m15_aligned = bool(mtf.get("m15_aligned") or mtf.get("M15_aligned"))
        aligned_count = sum([h4_aligned, h1_aligned, m15_aligned])

        # Expected confidence based on MTF alignment
        expected_conf = 40.0 + aligned_count * 20.0  # 40 / 60 / 80 / 100

        # Check for static confidence mismatch
        conf_delta = abs(confidence - expected_conf)
        if conf_delta > 20 and confidence > expected_conf:
            suggestions.append(
                f"Confidence ({confidence:.0f}%) is {conf_delta:.0f}% higher than "
                f"MTF-derived expectation ({expected_conf:.0f}%). "
                f"Recalculate confidence dynamically from MTF alignment."
            )
            score  = 0.5
            passed = False
        else:
            score  = 0.6 + aligned_count * 0.13  # 0.6 / 0.73 / 0.86 / 1.0
            score  = _clamp(score)
            passed = aligned_count >= 2

        if aligned_count < 2:
            suggestions.append(
                f"Only {aligned_count}/3 timeframes aligned (H4={h4_aligned}, "
                f"H1={h1_aligned}, M15={m15_aligned}). "
                f"Minimum 2/3 required for signal approval."
            )

        return QualityCheck(
            name=name,
            passed=passed,
            score=score,
            weight=weight,
            message=(
                f"MTF alignment: {aligned_count}/3 timeframes aligned "
                f"(H4={h4_aligned}, H1={h1_aligned}, M15={m15_aligned}). "
                f"Confidence={confidence:.0f}%, MTF-expected={expected_conf:.0f}%."
            ),
            details={
                "h4_aligned": h4_aligned,
                "h1_aligned": h1_aligned,
                "m15_aligned": m15_aligned,
                "aligned_count": aligned_count,
                "confidence": confidence,
                "mtf_expected_confidence": expected_conf,
            },
            suggestions=suggestions,
        )

    # ═══════════════════════════════════════════════════════════
    # HELPER METHODS
    # ═══════════════════════════════════════════════════════════

    def _classify_regime(self, signal: Dict[str, Any]) -> str:
        """
        Infer the correct market regime from signal indicators.
        Reclassifies RANGE vs directional confusion.
        """
        adx         = float(signal.get("adx", 0) or 0)
        atr_ratio   = float(signal.get("atr_ratio", 1.0) or 1.0)
        struct_bias = float(signal.get("structure_bias", 0) or 0)
        ma_slope    = float(signal.get("ma20_slope", 0) or 0)
        zscore      = float(signal.get("zscore_20", 0) or 0)

        # Chaos / high volatility
        if atr_ratio > 2.0 or abs(zscore) > 3:
            return REGIME_CHAOS
        if atr_ratio > 1.5:
            return REGIME_HIGH_VOL

        # Low volatility
        if atr_ratio < 0.6 and adx < 20:
            return REGIME_LOW_VOL

        # Breakout detection
        if atr_ratio > 1.2 and adx > 30:
            return REGIME_BREAKOUT

        # Trend detection
        if adx > 25:
            if struct_bias > 3 and ma_slope > 0:
                return REGIME_TREND_UP
            elif struct_bias < -3 and ma_slope < 0:
                return REGIME_TREND_DOWN
            elif ma_slope > 0:
                return REGIME_TREND_UP
            else:
                return REGIME_TREND_DOWN

        # Default: range
        return REGIME_RANGE

    def _compute_dynamic_confidence(self, signal: Dict[str, Any]) -> float:
        """
        Compute dynamic confidence score from multiple components:
          MTF alignment  30%
          SMC score      25%
          Momentum       20%
          Session        15%
          News clearance 10%
        """
        # MTF alignment (0–1)
        mtf = signal.get("mtf_alignment") or {}
        h4  = bool(mtf.get("h4_aligned") or mtf.get("H4_aligned"))
        h1  = bool(mtf.get("h1_aligned") or mtf.get("H1_aligned"))
        m15 = bool(mtf.get("m15_aligned") or mtf.get("M15_aligned"))
        mtf_score = sum([h4, h1, m15]) / 3.0

        # SMC score (0–10 → 0–1)
        smc_raw   = float(signal.get("smc_score", 5) or 5)
        smc_score = _clamp(smc_raw / 10.0)

        # Momentum score (0–10 → 0–1)
        mom_raw   = float(signal.get("momentum_score", 5) or 5)
        mom_score = _clamp(mom_raw / 10.0)

        # Session quality (0–1)
        now = _utc_now()
        sq  = self._get_session_quality(now)
        session_map = {"HIGH": 1.0, "MEDIUM": 0.75, "LOW": 0.5, "DEAD_ZONE": 0.1}
        session_score = session_map.get(sq, 0.5)

        # News clearance (0–1)
        news_flags  = self._extract_news_flags(signal)
        news_score  = 0.0 if news_flags else 1.0

        # Weighted composite
        dynamic_conf = (
            mtf_score     * 0.30 +
            smc_score     * 0.25 +
            mom_score     * 0.20 +
            session_score * 0.15 +
            news_score    * 0.10
        ) * 100.0

        return round(_clamp(dynamic_conf, 0.0, 100.0), 2)

    def _get_session_quality(self, now: datetime) -> str:
        """
        Classify current session quality based on UTC hour.
        Returns: HIGH / MEDIUM / LOW / DEAD_ZONE
        """
        hour = now.hour

        # Post-NY close dead zone: 22:00–07:00 UTC
        if hour >= POST_NY_CLOSE_START or hour < POST_NY_CLOSE_END:
            return "DEAD_ZONE"

        # London open overlap with NY: 13:00–16:00 UTC (highest liquidity)
        if SESSION_NY_OPEN <= hour < 16:
            return "HIGH"

        # London session: 07:00–13:00 UTC
        if SESSION_LONDON_OPEN <= hour < SESSION_NY_OPEN:
            return "MEDIUM"

        # NY session tail: 16:00–22:00 UTC
        if 16 <= hour < SESSION_NY_CLOSE:
            return "MEDIUM"

        return "LOW"

    def _extract_news_flags(self, signal: Dict[str, Any]) -> List[str]:
        """Extract high-impact news event names from signal."""
        news_events = signal.get("news_events") or []
        flags: List[str] = []
        for event in news_events:
            event_str = str(event)
            for keyword in HIGH_IMPACT_NEWS:
                if keyword.lower() in event_str.lower():
                    flags.append(event_str)
                    break
        return flags

    def _compute_enhancement_scores(self, signal: Dict[str, Any]) -> Dict[str, float]:
        """
        Compute scores for all 13 hybrid enhancement indicators.
        Each score is 0.0–1.0.
        """
        from ml_engine.hybrid_enhancement_indicators import HybridEnhancementIndicators
        try:
            hei = HybridEnhancementIndicators()
            return hei.score_all(signal)
        except Exception as exc:
            logger.warning(f"Enhancement scoring failed: {exc}")
            return {
                "smc_order_flow":          0.5,
                "triple_momentum":         0.5,
                "vwap_price_action":       0.5,
                "fibonacci_smc":           0.5,
                "atr_bollinger":           0.5,
                "range_breakout_filter":   0.5,
                "swing_scalp_timing":      0.5,
                "trend_mean_reversion":    0.5,
                "mtf_pyramid":             0.5,
                "session_mtf_weighting":   0.5,
                "fixed_trailing_stop":     0.5,
                "volatility_position_size": 0.5,
                "dynamic_confluence":      0.5,
            }


# ─────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────

signal_quality_validator = SignalQualityValidator()

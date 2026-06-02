"""
Signal Quality Validator — Gold Trading System v3.0.2
Comprehensive signal validation and quality enhancement engine.

Addresses all 12 known signal quality issues:
  1.  R:R minimum enforcement  — 1:2 swing, 1:1.5 scalp
  2.  Regime reclassification  — TREND_UP / TREND_DOWN / RANGE / BREAKOUT
  3.  10-pip entry band         — realistic zone instead of 1-pip
  4.  Dynamic confidence        — MTF + SMC + momentum + session + news
  5.  SL anchored to structure  — swing high / swing low
  6.  ATR quantification        — actual value + derived position size
  7.  Regime-specific entry     — sell at resistance, buy at support
  8.  Session quality flagging  — post-NY close, low-liquidity periods
  9.  Entry positioning check   — correct side of range
  10. MTF-driven confidence     — recalculate when MTF drops
  11. Signal expiry field        — auto-expire stale signals
  12. News filter                — JOLTS, Beige Book, NFP, FOMC, CPI
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# Minimum R:R ratios by trade type
MIN_RR_SWING  = 2.0   # Issue #1 — was 1.3, now 1:2
MIN_RR_SCALP  = 1.5   # Issue #1 — minimum for scalp trades
MIN_RR_INTRA  = 1.8   # Intraday trades

# Entry band width in pips (Issue #3 — was 1 pip, now 10 pips)
ENTRY_BAND_PIPS = 10.0

# Confidence thresholds
CONFIDENCE_HIGH    = 75.0   # Issue #4 — dynamic, not static
CONFIDENCE_MEDIUM  = 65.0
CONFIDENCE_MINIMUM = 55.0

# ATR multipliers for SL anchoring (Issue #5)
SL_ATR_MULTIPLIER_SWING = 1.5
SL_ATR_MULTIPLIER_SCALP = 1.0

# Signal expiry windows (Issue #11)
EXPIRY_SWING_HOURS  = 4
EXPIRY_SCALP_HOURS  = 1
EXPIRY_INTRA_HOURS  = 2

# Session windows UTC (Issue #8)
SESSION_LONDON_START = 7
SESSION_LONDON_END   = 16
SESSION_NY_START     = 12
SESSION_NY_END       = 21
SESSION_OVERLAP_START = 13
SESSION_OVERLAP_END   = 16

# High-impact news keywords (Issue #12)
HIGH_IMPACT_NEWS_KEYWORDS = {
    "NFP", "Non-Farm Payroll", "FOMC", "Federal Reserve", "Interest Rate",
    "CPI", "Inflation", "GDP", "Unemployment", "Retail Sales",
    "JOLTS", "Job Openings", "Beige Book", "PPI", "PMI", "ISM",
    "Jackson Hole", "ECB", "BOE", "BOJ", "SNB", "RBA",
    "ADP", "Durable Goods", "Trade Balance", "Consumer Confidence",
}

# Pip multipliers per symbol type
PIP_MULTIPLIERS = {
    "GOLD":  100.0,   # XAUUSD: 0.01 = 1 pip
    "JPY":   100.0,   # USDJPY: 0.01 = 1 pip
    "FOREX": 10_000.0,  # EURUSD: 0.0001 = 1 pip
}

# Regime classification (Issue #2)
VALID_REGIMES = {"TREND_UP", "TREND_DOWN", "RANGE", "BREAKOUT", "HIGH_VOL", "LOW_VOL", "CHAOS"}

# Regime-specific entry rules (Issue #7 & #9)
REGIME_ENTRY_RULES = {
    "TREND_UP":   {"allowed_sides": ["BUY"],         "entry_zone": "PULLBACK",   "avoid": "OVERBOUGHT"},
    "TREND_DOWN": {"allowed_sides": ["SELL"],        "entry_zone": "RALLY",      "avoid": "OVERSOLD"},
    "RANGE":      {"allowed_sides": ["BUY", "SELL"], "entry_zone": "EXTREMES",   "avoid": "MIDRANGE"},
    "BREAKOUT":   {"allowed_sides": ["BUY", "SELL"], "entry_zone": "BREAKOUT",   "avoid": "FAKEOUT"},
    "HIGH_VOL":   {"allowed_sides": ["BUY", "SELL"], "entry_zone": "CONFIRMED",  "avoid": "NOISE"},
    "LOW_VOL":    {"allowed_sides": ["BUY", "SELL"], "entry_zone": "MEAN_REVERT","avoid": "BREAKOUT"},
}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _symbol_type(symbol: str) -> str:
    s = symbol.upper()
    if "XAU" in s:
        return "GOLD"
    if "JPY" in s:
        return "JPY"
    return "FOREX"


def _pips(price_diff: float, symbol: str) -> float:
    """Convert raw price difference to pips (always positive)."""
    mult = PIP_MULTIPLIERS.get(_symbol_type(symbol), 10_000.0)
    return abs(price_diff) * mult


def _rr(entry: float, sl: float, tp: float) -> float:
    """Calculate R:R ratio. Returns 0.0 if risk is zero."""
    risk   = abs(entry - sl)
    reward = abs(tp - entry)
    return round(reward / risk, 4) if risk > 0 else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# MAIN VALIDATOR CLASS
# ─────────────────────────────────────────────────────────────────────────────

class SignalQualityValidator:
    """
    Comprehensive signal quality validator for the Gold Trading System.

    Runs 13 independent validation checks and produces:
    - A per-check pass/fail result with detailed reasoning
    - A dynamic confidence score (0–100) derived from all checks
    - An overall confluence score (0–100) for final approval
    - A signal expiry timestamp
    - Actionable rejection reasons

    Usage::

        validator = SignalQualityValidator()
        result = validator.validate(signal_dict)
        if result["approved"]:
            # proceed with trade
        else:
            print(result["rejection_reasons"])
    """

    def __init__(
        self,
        min_rr_swing:  float = MIN_RR_SWING,
        min_rr_scalp:  float = MIN_RR_SCALP,
        entry_band_pips: float = ENTRY_BAND_PIPS,
        min_confluence: float = CONFIDENCE_HIGH,
    ) -> None:
        self.min_rr_swing    = min_rr_swing
        self.min_rr_scalp    = min_rr_scalp
        self.entry_band_pips = entry_band_pips
        self.min_confluence  = min_confluence
        self.version         = "1.0.0"

    # ═════════════════════════════════════════════════════════════════════════
    # PUBLIC ENTRY POINT
    # ═════════════════════════════════════════════════════════════════════════

    def validate(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run all 13 quality checks against a signal dictionary.

        Required signal keys:
            symbol, side (BUY/SELL), entry_price, sl_price, tp_levels (list),
            trade_type (SWING/SCALP/INTRA), regime, confidence,
            atr, account_balance

        Optional keys (enrich quality scoring):
            mtf_result, smc_result, momentum_data, session_data,
            news_events, swing_high, swing_low, current_price,
            position_in_range (0.0–1.0)

        Returns:
            {
              "approved": bool,
              "confluence_score": float,          # 0–100
              "dynamic_confidence": float,        # 0–100
              "expiry_at": str,                   # ISO-8601
              "checks": { check_name: {...} },
              "rejection_reasons": [str],
              "warnings": [str],
              "quality_tier": str,                # HIGH / MEDIUM / LOW / REJECTED
              "position_size_lots": float,
              "atr_value": float,
              "regime_classification": str,
              "session_quality": str,
              "news_flag": bool,
              "version": str,
            }
        """
        checks: Dict[str, Dict[str, Any]] = {}
        rejection_reasons: List[str] = []
        warnings: List[str] = []

        symbol     = str(signal.get("symbol", "XAUUSD")).upper()
        side       = str(signal.get("side", "BUY")).upper()
        entry      = float(signal.get("entry_price", 0))
        sl         = float(signal.get("sl_price", 0))
        tp_levels  = signal.get("tp_levels", [])
        tp1        = float(tp_levels[0]) if tp_levels else 0.0
        trade_type = str(signal.get("trade_type", "SWING")).upper()
        regime     = str(signal.get("regime", "RANGE")).upper()
        confidence = float(signal.get("confidence", 75.0))
        atr        = float(signal.get("atr", 0.0))
        balance    = float(signal.get("account_balance", 10_000.0))

        # ── Run all 13 checks ────────────────────────────────────────────────

        checks["rr_ratio"]          = self.validate_rr_ratio(entry, sl, tp1, trade_type, symbol)
        checks["regime"]            = self.validate_regime(regime, signal.get("regime_data", {}))
        checks["entry_band"]        = self.validate_entry_band(entry, signal.get("current_price", entry), symbol)
        checks["confidence"]        = self.validate_confidence(signal)
        checks["sl_anchoring"]      = self.validate_sl_anchoring(entry, sl, side, signal, symbol)
        checks["atr_quantification"]= self.validate_atr_quantification(atr, entry, sl, balance, symbol)
        checks["regime_logic"]      = self.validate_regime_logic(regime, side, signal)
        checks["session_quality"]   = self.validate_session_quality(signal.get("session_data", {}))
        checks["entry_positioning"] = self.validate_entry_positioning(entry, side, regime, signal)
        checks["mtf_alignment"]     = self.validate_mtf_alignment(side, signal.get("mtf_result", {}))
        checks["signal_expiry"]     = self.validate_signal_expiry(signal, trade_type)
        checks["news_filter"]       = self.validate_news_filter(signal.get("news_events", []))
        checks["confluence_score"]  = self.calculate_confluence_score(checks, signal)

        # ── Collect rejections and warnings ──────────────────────────────────
        for name, result in checks.items():
            if not result.get("pass", True):
                if result.get("blocking", True):
                    rejection_reasons.append(result.get("reason", f"{name} check failed"))
                else:
                    warnings.append(result.get("reason", f"{name} warning"))

        # ── Dynamic confidence (Issue #4 & #10) ──────────────────────────────
        dynamic_confidence = checks["confidence"].get("dynamic_confidence", confidence)

        # ── Confluence score (Issue #13) ──────────────────────────────────────
        confluence = checks["confluence_score"].get("score", 0.0)

        # ── Final approval ────────────────────────────────────────────────────
        approved = (
            len(rejection_reasons) == 0
            and confluence >= self.min_confluence
            and dynamic_confidence >= CONFIDENCE_MINIMUM
        )

        # ── Quality tier ──────────────────────────────────────────────────────
        if not approved:
            quality_tier = "REJECTED"
        elif confluence >= 85 and dynamic_confidence >= 80:
            quality_tier = "HIGH"
        elif confluence >= 75 and dynamic_confidence >= 65:
            quality_tier = "MEDIUM"
        else:
            quality_tier = "LOW"

        return {
            "approved":              approved,
            "confluence_score":      round(confluence, 1),
            "dynamic_confidence":    round(dynamic_confidence, 1),
            "expiry_at":             checks["signal_expiry"].get("expiry_at", ""),
            "checks":                checks,
            "rejection_reasons":     rejection_reasons,
            "warnings":              warnings,
            "quality_tier":          quality_tier,
            "position_size_lots":    checks["atr_quantification"].get("position_size_lots", 0.01),
            "atr_value":             checks["atr_quantification"].get("atr_value", atr),
            "regime_classification": checks["regime"].get("classification", regime),
            "session_quality":       checks["session_quality"].get("quality", "UNKNOWN"),
            "news_flag":             not checks["news_filter"].get("pass", True),
            "version":               self.version,
        }

    # ═════════════════════════════════════════════════════════════════════════
    # CHECK 1 — R:R RATIO  (Issue #1)
    # ═════════════════════════════════════════════════════════════════════════

    def validate_rr_ratio(
        self,
        entry: float,
        sl: float,
        tp: float,
        trade_type: str = "SWING",
        symbol: str = "XAUUSD",
    ) -> Dict[str, Any]:
        """
        Enforce minimum R:R ratios:
          - SWING trades: minimum 1:2
          - SCALP trades: minimum 1:1.5
          - INTRA  trades: minimum 1:1.8

        Issue #1: R:R 1:1.3 was previously accepted — now rejected.
        """
        rr = _rr(entry, sl, tp)
        min_rr = {
            "SWING": self.min_rr_swing,
            "SCALP": self.min_rr_scalp,
            "INTRA": MIN_RR_INTRA,
        }.get(trade_type, self.min_rr_swing)

        risk_pips   = _pips(entry - sl, symbol)
        reward_pips = _pips(tp - entry, symbol)

        passed = rr >= min_rr
        return {
            "pass":         passed,
            "blocking":     True,
            "rr":           rr,
            "min_rr":       min_rr,
            "trade_type":   trade_type,
            "risk_pips":    round(risk_pips, 1),
            "reward_pips":  round(reward_pips, 1),
            "reason": (
                f"R:R {rr:.2f} meets minimum {min_rr} for {trade_type}"
                if passed else
                f"R:R {rr:.2f} below minimum {min_rr} for {trade_type} — "
                f"risk {risk_pips:.1f} pips, reward {reward_pips:.1f} pips"
            ),
        }

    # ═════════════════════════════════════════════════════════════════════════
    # CHECK 2 — REGIME CLASSIFICATION  (Issue #2)
    # ═════════════════════════════════════════════════════════════════════════

    def validate_regime(
        self,
        regime: str,
        regime_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Classify and validate market regime.

        Issue #2: RANGE regime was being confused with directional SELL.
        Now enforces strict regime classification with BREAKOUT as a
        distinct regime separate from TREND.

        Regimes:
          TREND_UP   — ADX > 25, price above EMA50, HH/HL structure
          TREND_DOWN — ADX > 25, price below EMA50, LH/LL structure
          RANGE      — ADX < 20, price oscillating between S/R
          BREAKOUT   — ADX rising through 25, price breaking key level
          HIGH_VOL   — ATR ratio > 1.5
          LOW_VOL    — ATR ratio < 0.6
          CHAOS      — Extreme conditions, no trading
        """
        adx         = float(regime_data.get("adx", 25))
        atr_ratio   = float(regime_data.get("atr_ratio", 1.0))
        ma_slope    = float(regime_data.get("ma20_slope", 0))
        struct_bias = float(regime_data.get("structure_bias", 0))
        adx_rising  = bool(regime_data.get("adx_rising", False))

        # Reclassify regime from raw data when available
        if regime_data:
            if atr_ratio > 1.5:
                classification = "HIGH_VOL"
            elif atr_ratio < 0.6 and adx < 20:
                classification = "LOW_VOL"
            elif adx_rising and 20 <= adx <= 30:
                classification = "BREAKOUT"
            elif adx > 25 and struct_bias > 2 and ma_slope > 0:
                classification = "TREND_UP"
            elif adx > 25 and struct_bias < -2 and ma_slope < 0:
                classification = "TREND_DOWN"
            elif adx < 20:
                classification = "RANGE"
            else:
                classification = regime  # Trust provided regime
        else:
            classification = regime

        # Validate regime is known
        valid_regime = classification in VALID_REGIMES
        no_trade     = classification == "CHAOS"

        return {
            "pass":           valid_regime and not no_trade,
            "blocking":       no_trade,
            "regime_input":   regime,
            "classification": classification,
            "adx":            round(adx, 1),
            "atr_ratio":      round(atr_ratio, 2),
            "is_trending":    classification in ("TREND_UP", "TREND_DOWN"),
            "is_ranging":     classification == "RANGE",
            "is_breakout":    classification == "BREAKOUT",
            "reason": (
                f"CHAOS regime — no trading" if no_trade else
                f"Regime classified as {classification} (ADX={adx:.1f}, ATR_ratio={atr_ratio:.2f})"
            ),
        }

    # ═════════════════════════════════════════════════════════════════════════
    # CHECK 3 — ENTRY BAND  (Issue #3)
    # ═════════════════════════════════════════════════════════════════════════

    def validate_entry_band(
        self,
        entry: float,
        current_price: float,
        symbol: str = "XAUUSD",
    ) -> Dict[str, Any]:
        """
        Validate entry is within a realistic 10-pip zone of current price.

        Issue #3: 1-pip entry band was unrealistic for live execution.
        Now uses a 10-pip zone to account for spread and slippage.
        """
        distance_pips = _pips(entry - current_price, symbol)
        passed = distance_pips <= self.entry_band_pips

        return {
            "pass":           passed,
            "blocking":       True,
            "distance_pips":  round(distance_pips, 1),
            "band_pips":      self.entry_band_pips,
            "entry":          entry,
            "current_price":  current_price,
            "reason": (
                f"Entry {entry} within {distance_pips:.1f} pips of current price {current_price}"
                if passed else
                f"Entry {entry} is {distance_pips:.1f} pips from current price {current_price} "
                f"(max {self.entry_band_pips} pips)"
            ),
        }

    # ═════════════════════════════════════════════════════════════════════════
    # CHECK 4 — DYNAMIC CONFIDENCE  (Issue #4 & #10)
    # ═════════════════════════════════════════════════════════════════════════

    def validate_confidence(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate dynamic confidence from multiple sources.

        Issue #4: Static 75% confidence replaced with dynamic calculation.
        Issue #10: MTF dropped but confidence was static — now recalculates.

        Sources (weighted):
          MTF alignment score    — 30%
          SMC score              — 25%
          Momentum confluence    — 20%
          Session quality        — 15%
          News clearance         — 10%
        """
        base_confidence = float(signal.get("confidence", 75.0))

        # MTF contribution (30%)
        mtf = signal.get("mtf_result", {})
        mtf_score = float(mtf.get("alignment_score", mtf.get("confluence_score", 0))) if mtf else 0
        mtf_direction = mtf.get("trade_direction", "NEUTRAL") if mtf else "NEUTRAL"
        side = str(signal.get("side", "BUY")).upper()
        mtf_direction_match = (
            (side == "BUY"  and mtf_direction in ("BUY", "BULLISH")) or
            (side == "SELL" and mtf_direction in ("SELL", "BEARISH"))
        )
        # Normalise MTF score to 0-100 if it's 0-3 (confluence count)
        if mtf_score <= 3:
            mtf_score = (mtf_score / 3) * 100
        mtf_contribution = (mtf_score * 0.30) if mtf_direction_match else (mtf_score * 0.10)

        # SMC contribution (25%)
        smc = signal.get("smc_result", {})
        smc_score = float(smc.get("smc_score", 0)) if smc else 0
        smc_max   = 10.0  # SMC scores are 0-10
        smc_contribution = (smc_score / smc_max) * 100 * 0.25

        # Momentum contribution (20%)
        momentum = signal.get("momentum_data", {})
        mom_score = float(momentum.get("score", 50)) if momentum else 50.0
        mom_contribution = mom_score * 0.20

        # Session contribution (15%)
        session = signal.get("session_data", {})
        session_quality = session.get("quality", "MEDIUM") if session else "MEDIUM"
        session_scores  = {"HIGH": 100, "MEDIUM": 65, "LOW": 30, "CLOSED": 0}
        session_score   = session_scores.get(session_quality, 65)
        session_contribution = session_score * 0.15

        # News clearance contribution (10%)
        news_events = signal.get("news_events", [])
        news_clear  = not any(
            e.get("impact", "").lower() in ("high", "red")
            for e in news_events
        )
        news_contribution = 100 * 0.10 if news_clear else 0.0

        # Dynamic confidence
        dynamic_confidence = (
            mtf_contribution +
            smc_contribution +
            mom_contribution +
            session_contribution +
            news_contribution
        )
        dynamic_confidence = max(0.0, min(100.0, dynamic_confidence))

        # If no enrichment data provided, fall back to base confidence
        has_enrichment = bool(mtf or smc or momentum or session)
        if not has_enrichment:
            dynamic_confidence = base_confidence

        passed = dynamic_confidence >= CONFIDENCE_MINIMUM

        return {
            "pass":               passed,
            "blocking":           True,
            "base_confidence":    round(base_confidence, 1),
            "dynamic_confidence": round(dynamic_confidence, 1),
            "components": {
                "mtf":     round(mtf_contribution, 1),
                "smc":     round(smc_contribution, 1),
                "momentum":round(mom_contribution, 1),
                "session": round(session_contribution, 1),
                "news":    round(news_contribution, 1),
            },
            "mtf_direction_match": mtf_direction_match,
            "reason": (
                f"Dynamic confidence {dynamic_confidence:.1f}% (min {CONFIDENCE_MINIMUM}%)"
                if passed else
                f"Dynamic confidence {dynamic_confidence:.1f}% below minimum {CONFIDENCE_MINIMUM}%"
            ),
        }

    # ═════════════════════════════════════════════════════════════════════════
    # CHECK 5 — SL ANCHORING  (Issue #5)
    # ═════════════════════════════════════════════════════════════════════════

    def validate_sl_anchoring(
        self,
        entry: float,
        sl: float,
        side: str,
        signal: Dict[str, Any],
        symbol: str = "XAUUSD",
    ) -> Dict[str, Any]:
        """
        Verify SL is anchored to market structure (swing high/low).

        Issue #5: SL was not anchored to structure — now checks that SL
        is within ATR distance of the nearest swing high (SELL) or
        swing low (BUY).

        For BUY:  SL should be at or below the recent swing low
        For SELL: SL should be at or above the recent swing high
        """
        swing_high = float(signal.get("swing_high", 0))
        swing_low  = float(signal.get("swing_low", 0))
        atr        = float(signal.get("atr", 0))

        # If no structure data provided, check SL distance is reasonable
        if not swing_high and not swing_low:
            sl_distance_pips = _pips(entry - sl, symbol)
            # SL should be at least 5 pips and at most 100 pips from entry
            reasonable = 5 <= sl_distance_pips <= 100
            return {
                "pass":     reasonable,
                "blocking": False,  # Warning only — no structure data
                "anchored": False,
                "sl_distance_pips": round(sl_distance_pips, 1),
                "reason": (
                    f"SL {sl_distance_pips:.1f} pips from entry (no structure data for anchoring)"
                    if reasonable else
                    f"SL distance {sl_distance_pips:.1f} pips is outside reasonable range (5–100 pips)"
                ),
            }

        # Check SL anchoring to structure
        if side == "BUY":
            # SL should be near or below swing low
            if swing_low > 0:
                sl_vs_swing = sl - swing_low  # Negative = SL below swing low (good)
                sl_pips_from_swing = _pips(sl_vs_swing, symbol)
                # Allow SL up to 1 ATR above swing low (buffer)
                atr_buffer = atr if atr > 0 else _pips(1, symbol) / PIP_MULTIPLIERS.get(_symbol_type(symbol), 10000)
                anchored = sl <= swing_low + atr_buffer
                return {
                    "pass":              anchored,
                    "blocking":          False,
                    "anchored":          anchored,
                    "sl":                sl,
                    "swing_low":         swing_low,
                    "sl_pips_from_swing": round(sl_pips_from_swing, 1),
                    "reason": (
                        f"BUY SL {sl} anchored to swing low {swing_low}"
                        if anchored else
                        f"BUY SL {sl} not anchored to swing low {swing_low} — "
                        f"SL is {sl_pips_from_swing:.1f} pips above swing low"
                    ),
                }

        elif side == "SELL":
            # SL should be near or above swing high
            if swing_high > 0:
                sl_vs_swing = swing_high - sl  # Negative = SL above swing high (good)
                sl_pips_from_swing = _pips(sl_vs_swing, symbol)
                atr_buffer = atr if atr > 0 else 0
                anchored = sl >= swing_high - atr_buffer
                return {
                    "pass":              anchored,
                    "blocking":          False,
                    "anchored":          anchored,
                    "sl":                sl,
                    "swing_high":        swing_high,
                    "sl_pips_from_swing": round(sl_pips_from_swing, 1),
                    "reason": (
                        f"SELL SL {sl} anchored to swing high {swing_high}"
                        if anchored else
                        f"SELL SL {sl} not anchored to swing high {swing_high} — "
                        f"SL is {sl_pips_from_swing:.1f} pips below swing high"
                    ),
                }

        return {
            "pass":     True,
            "blocking": False,
            "anchored": False,
            "reason":   "SL anchoring check skipped — insufficient structure data",
        }

    # ═════════════════════════════════════════════════════════════════════════
    # CHECK 6 — ATR QUANTIFICATION  (Issue #6)
    # ═════════════════════════════════════════════════════════════════════════

    def validate_atr_quantification(
        self,
        atr: float,
        entry: float,
        sl: float,
        account_balance: float = 10_000.0,
        symbol: str = "XAUUSD",
        risk_pct: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Quantify ATR and derive position size.

        Issue #6: ATR was unquantified — now states actual ATR value,
        ATR in pips, and derives position size from 1% account risk.

        Position size formula (1% risk):
          risk_amount = account_balance * risk_pct / 100
          sl_distance = |entry - sl|
          For GOLD: lots = risk_amount / (sl_distance * 100)  [100 oz/lot]
        """
        atr_pips = _pips(atr, symbol) if atr > 0 else 0.0
        sl_distance = abs(entry - sl)
        sl_pips = _pips(sl_distance, symbol)

        # Position size calculation (1% account risk)
        risk_amount = account_balance * risk_pct / 100.0
        sym_type = _symbol_type(symbol)

        if sym_type == "GOLD":
            # XAUUSD: 1 lot = 100 oz, pip value ≈ $1/pip/lot
            position_size_lots = risk_amount / (sl_pips * 1.0) if sl_pips > 0 else 0.01
        elif sym_type == "JPY":
            # JPY pairs: pip value ≈ $9/pip/lot
            position_size_lots = risk_amount / (sl_pips * 9.0) if sl_pips > 0 else 0.01
        else:
            # Standard forex: pip value ≈ $10/pip/lot
            position_size_lots = risk_amount / (sl_pips * 10.0) if sl_pips > 0 else 0.01

        # Clamp to reasonable range
        position_size_lots = max(0.01, min(position_size_lots, 10.0))
        position_size_lots = round(position_size_lots, 2)

        # ATR validity check
        atr_valid = atr > 0 and atr_pips >= 1.0

        return {
            "pass":               atr_valid,
            "blocking":           False,  # Warning only
            "atr_value":          round(atr, 5),
            "atr_pips":           round(atr_pips, 1),
            "sl_distance":        round(sl_distance, 5),
            "sl_pips":            round(sl_pips, 1),
            "risk_pct":           risk_pct,
            "risk_amount_usd":    round(risk_amount, 2),
            "position_size_lots": position_size_lots,
            "account_balance":    account_balance,
            "reason": (
                f"ATR={atr:.5f} ({atr_pips:.1f} pips), "
                f"SL={sl_pips:.1f} pips, "
                f"Position size={position_size_lots} lots ({risk_pct}% risk)"
                if atr_valid else
                f"ATR not quantified (value={atr}) — position sizing may be inaccurate"
            ),
        }

    # ═════════════════════════════════════════════════════════════════════════
    # CHECK 7 — REGIME-SPECIFIC ENTRY LOGIC  (Issue #7 & #9)
    # ═════════════════════════════════════════════════════════════════════════

    def validate_regime_logic(
        self,
        regime: str,
        side: str,
        signal: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Enforce regime-specific entry rules.

        Issue #7: Selling at support in RANGE regime (wrong!) — now
        enforces that RANGE SELL entries must be near resistance, and
        RANGE BUY entries must be near support.

        Issue #9: Entry near day's low in RANGE for SELL (wrong!) — now
        checks position_in_range to ensure correct entry positioning.

        Rules:
          RANGE + SELL → entry must be in upper 30% of range (near resistance)
          RANGE + BUY  → entry must be in lower 30% of range (near support)
          TREND_UP     → only BUY allowed (pullback entries)
          TREND_DOWN   → only SELL allowed (rally entries)
          BREAKOUT     → either side, but must confirm breakout direction
        """
        rules = REGIME_ENTRY_RULES.get(regime, {})
        allowed_sides = rules.get("allowed_sides", ["BUY", "SELL"])

        # Check side is allowed for this regime
        side_allowed = side in allowed_sides

        # Range-specific positioning check (Issues #7 & #9)
        position_in_range = float(signal.get("position_in_range", 0.5))
        range_check_pass  = True
        range_check_note  = ""

        if regime == "RANGE":
            if side == "SELL":
                # Must be in upper 30% of range (near resistance)
                range_check_pass = position_in_range >= 0.70
                range_check_note = (
                    f"RANGE SELL: entry at {position_in_range:.0%} of range "
                    f"({'✓ near resistance' if range_check_pass else '✗ must be near resistance (≥70%)'})"
                )
            elif side == "BUY":
                # Must be in lower 30% of range (near support)
                range_check_pass = position_in_range <= 0.30
                range_check_note = (
                    f"RANGE BUY: entry at {position_in_range:.0%} of range "
                    f"({'✓ near support' if range_check_pass else '✗ must be near support (≤30%)'})"
                )

        # Trend-specific check
        trend_check_pass = True
        trend_check_note = ""
        if regime == "TREND_UP" and side == "SELL":
            trend_check_pass = False
            trend_check_note = "TREND_UP regime: SELL signals not allowed (counter-trend)"
        elif regime == "TREND_DOWN" and side == "BUY":
            trend_check_pass = False
            trend_check_note = "TREND_DOWN regime: BUY signals not allowed (counter-trend)"

        overall_pass = side_allowed and range_check_pass and trend_check_pass
        reasons = []
        if not side_allowed:
            reasons.append(f"{side} not allowed in {regime} regime (allowed: {allowed_sides})")
        if not range_check_pass:
            reasons.append(range_check_note)
        if not trend_check_pass:
            reasons.append(trend_check_note)

        return {
            "pass":              overall_pass,
            "blocking":          True,
            "regime":            regime,
            "side":              side,
            "side_allowed":      side_allowed,
            "range_check_pass":  range_check_pass,
            "trend_check_pass":  trend_check_pass,
            "position_in_range": round(position_in_range, 2),
            "allowed_sides":     allowed_sides,
            "entry_zone":        rules.get("entry_zone", "ANY"),
            "reason": (
                f"Regime logic OK: {side} in {regime} at {position_in_range:.0%} of range"
                if overall_pass else
                " | ".join(reasons)
            ),
        }

    # ═════════════════════════════════════════════════════════════════════════
    # CHECK 8 — SESSION QUALITY  (Issue #8)
    # ═════════════════════════════════════════════════════════════════════════

    def validate_session_quality(
        self,
        session_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Flag session quality and post-NY close timing.

        Issue #8: Post-NY close timing was ignored — now flags:
          - Post-NY close (after 21:00 UTC): LOW quality
          - Asian session only (00:00–07:00 UTC): LOW quality
          - London/NY overlap (13:00–16:00 UTC): HIGH quality
          - London session (07:00–16:00 UTC): MEDIUM-HIGH quality
          - NY session (12:00–21:00 UTC): MEDIUM-HIGH quality
          - Weekend: CLOSED
        """
        now = session_data.get("current_time")
        if isinstance(now, str):
            try:
                now = datetime.fromisoformat(now)
            except ValueError:
                now = None
        if now is None:
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        hour    = now.hour
        weekday = now.weekday()  # 0=Mon, 6=Sun

        # Weekend check
        if weekday >= 5:
            return {
                "pass":    False,
                "blocking": True,
                "quality": "CLOSED",
                "session": "WEEKEND",
                "hour_utc": hour,
                "reason":  "Weekend — markets closed",
            }

        # Determine session
        in_london  = SESSION_LONDON_START  <= hour < SESSION_LONDON_END
        in_ny      = SESSION_NY_START      <= hour < SESSION_NY_END
        in_overlap = SESSION_OVERLAP_START <= hour < SESSION_OVERLAP_END
        in_asia    = 0 <= hour < SESSION_LONDON_START
        post_ny    = hour >= SESSION_NY_END  # After 21:00 UTC

        # Session quality
        if in_overlap:
            session_name = "LONDON_NY_OVERLAP"
            quality      = "HIGH"
            blocking     = False
        elif in_london and not in_ny:
            session_name = "LONDON"
            quality      = "MEDIUM_HIGH"
            blocking     = False
        elif in_ny and not in_london:
            session_name = "NEW_YORK"
            quality      = "MEDIUM_HIGH"
            blocking     = False
        elif in_asia:
            session_name = "ASIA"
            quality      = "LOW"
            blocking     = False  # Warning only
        elif post_ny:
            session_name = "POST_NY_CLOSE"
            quality      = "LOW"
            blocking     = False  # Warning only — Issue #8
        else:
            session_name = "OFF_HOURS"
            quality      = "LOW"
            blocking     = False

        passed = quality not in ("CLOSED",)

        return {
            "pass":        passed,
            "blocking":    blocking,
            "quality":     quality,
            "session":     session_name,
            "hour_utc":    hour,
            "in_london":   in_london,
            "in_ny":       in_ny,
            "in_overlap":  in_overlap,
            "post_ny":     post_ny,
            "reason": (
                f"Session: {session_name} (UTC {hour:02d}:xx) — quality: {quality}"
            ),
        }

    # ═════════════════════════════════════════════════════════════════════════
    # CHECK 9 — ENTRY POSITIONING  (Issue #9)
    # ═════════════════════════════════════════════════════════════════════════

    def validate_entry_positioning(
        self,
        entry: float,
        side: str,
        regime: str,
        signal: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Validate entry is on the correct side of the range/structure.

        Issue #9: Entry near day's low in RANGE for SELL (wrong!) — now
        checks that entry is positioned correctly relative to the day's
        range and key structure levels.

        For RANGE regime:
          SELL → entry should be near day's high (top of range)
          BUY  → entry should be near day's low (bottom of range)

        For TREND regime:
          BUY  → entry should be on pullback (not at new high)
          SELL → entry should be on rally (not at new low)
        """
        day_high = float(signal.get("day_high", 0))
        day_low  = float(signal.get("day_low", 0))
        swing_high = float(signal.get("swing_high", 0))
        swing_low  = float(signal.get("swing_low", 0))

        # Use day range if available, else swing range
        range_high = day_high if day_high > 0 else swing_high
        range_low  = day_low  if day_low  > 0 else swing_low

        if range_high <= 0 or range_low <= 0 or range_high <= range_low:
            return {
                "pass":     True,
                "blocking": False,
                "reason":   "Entry positioning check skipped — no range data",
            }

        range_size = range_high - range_low
        position   = (entry - range_low) / range_size  # 0 = bottom, 1 = top

        if regime == "RANGE":
            if side == "SELL":
                # Should be in top 30% of range
                correct = position >= 0.70
                return {
                    "pass":            correct,
                    "blocking":        True,
                    "position_in_day": round(position, 2),
                    "range_high":      range_high,
                    "range_low":       range_low,
                    "reason": (
                        f"RANGE SELL entry at {position:.0%} of day range — "
                        f"{'✓ near top (resistance)' if correct else '✗ must be near top ≥70% — currently near bottom (wrong!)'}"
                    ),
                }
            elif side == "BUY":
                # Should be in bottom 30% of range
                correct = position <= 0.30
                return {
                    "pass":            correct,
                    "blocking":        True,
                    "position_in_day": round(position, 2),
                    "range_high":      range_high,
                    "range_low":       range_low,
                    "reason": (
                        f"RANGE BUY entry at {position:.0%} of day range — "
                        f"{'✓ near bottom (support)' if correct else '✗ must be near bottom ≤30% — currently near top (wrong!)'}"
                    ),
                }

        elif regime in ("TREND_UP", "TREND_DOWN"):
            if side == "BUY" and regime == "TREND_UP":
                # Pullback entry — should not be at the very top
                correct = position <= 0.80
                return {
                    "pass":    correct,
                    "blocking": False,
                    "position_in_day": round(position, 2),
                    "reason": (
                        f"TREND_UP BUY at {position:.0%} of range — "
                        f"{'✓ pullback entry' if correct else '⚠ entry near top — chasing price'}"
                    ),
                }
            elif side == "SELL" and regime == "TREND_DOWN":
                # Rally entry — should not be at the very bottom
                correct = position >= 0.20
                return {
                    "pass":    correct,
                    "blocking": False,
                    "position_in_day": round(position, 2),
                    "reason": (
                        f"TREND_DOWN SELL at {position:.0%} of range — "
                        f"{'✓ rally entry' if correct else '⚠ entry near bottom — chasing price'}"
                    ),
                }

        return {
            "pass":            True,
            "blocking":        False,
            "position_in_day": round(position, 2),
            "reason":          f"Entry at {position:.0%} of range — positioning acceptable for {regime}",
        }

    # ═════════════════════════════════════════════════════════════════════════
    # CHECK 10 — MTF ALIGNMENT  (Issue #10)
    # ═════════════════════════════════════════════════════════════════════════

    def validate_mtf_alignment(
        self,
        side: str,
        mtf_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Check multi-timeframe alignment and recalculate confidence if MTF drops.

        Issue #10: MTF dropped but confidence remained static — now
        recalculates confidence dynamically when MTF alignment is weak.

        Checks:
          - H4 bias aligns with trade direction
          - H1 structure aligns with trade direction
          - M15 trigger confirms entry
          - Overall confluence score ≥ 2/3
        """
        if not mtf_result:
            return {
                "pass":     False,
                "blocking": False,  # Warning only
                "score":    0,
                "aligned":  False,
                "reason":   "No MTF data provided — confidence may be overstated",
            }

        h4_bias    = mtf_result.get("h4_bias", {})
        h1_struct  = mtf_result.get("h1_structure", {})
        m15_trigger= mtf_result.get("m15_trigger", {})
        confluence = int(mtf_result.get("confluence_score", 0))
        direction  = str(mtf_result.get("trade_direction", "NEUTRAL")).upper()

        # Check alignment
        h4_aligned  = (
            (side == "BUY"  and h4_bias.get("direction") == "BULLISH") or
            (side == "SELL" and h4_bias.get("direction") == "BEARISH")
        ) if h4_bias else False

        h1_aligned  = (
            (side == "BUY"  and h1_struct.get("bias") == "BULLISH") or
            (side == "SELL" and h1_struct.get("bias") == "BEARISH")
        ) if h1_struct else False

        m15_aligned = (
            (side == "BUY"  and m15_trigger.get("trigger") == "BUY") or
            (side == "SELL" and m15_trigger.get("trigger") == "SELL")
        ) if m15_trigger else False

        aligned_count = sum([h4_aligned, h1_aligned, m15_aligned])
        direction_match = (
            (side == "BUY"  and direction in ("BUY", "BULLISH")) or
            (side == "SELL" and direction in ("SELL", "BEARISH"))
        )

        # Alignment score 0-100
        alignment_score = (aligned_count / 3) * 100

        # MTF is considered passing if at least 2/3 timeframes align
        passed = aligned_count >= 2 and direction_match

        return {
            "pass":            passed,
            "blocking":        False,
            "score":           confluence,
            "alignment_score": round(alignment_score, 1),
            "aligned":         passed,
            "h4_aligned":      h4_aligned,
            "h1_aligned":      h1_aligned,
            "m15_aligned":     m15_aligned,
            "aligned_count":   aligned_count,
            "direction_match": direction_match,
            "reason": (
                f"MTF alignment: {aligned_count}/3 timeframes aligned "
                f"({'✓' if passed else '✗'} direction={direction}, side={side})"
            ),
        }

    # ═════════════════════════════════════════════════════════════════════════
    # CHECK 11 — SIGNAL EXPIRY  (Issue #11)
    # ═════════════════════════════════════════════════════════════════════════

    def validate_signal_expiry(
        self,
        signal: Dict[str, Any],
        trade_type: str = "SWING",
    ) -> Dict[str, Any]:
        """
        Add signal expiry field and check if signal has already expired.

        Issue #11: No signal expiry — now adds expiry_at timestamp and
        rejects signals that have exceeded their validity window.

        Expiry windows:
          SWING  → 4 hours
          INTRA  → 2 hours
          SCALP  → 1 hour
        """
        expiry_hours = {
            "SWING": EXPIRY_SWING_HOURS,
            "INTRA": EXPIRY_INTRA_HOURS,
            "SCALP": EXPIRY_SCALP_HOURS,
        }.get(trade_type, EXPIRY_SWING_HOURS)

        # Parse signal creation time
        created_at = signal.get("created_at") or signal.get("timestamp")
        now = datetime.now(timezone.utc)

        if created_at:
            if isinstance(created_at, str):
                try:
                    created_at = datetime.fromisoformat(created_at)
                except ValueError:
                    created_at = now
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
        else:
            created_at = now

        expiry_at = created_at + timedelta(hours=expiry_hours)
        is_expired = now > expiry_at
        age_minutes = (now - created_at).total_seconds() / 60

        return {
            "pass":          not is_expired,
            "blocking":      True,
            "expiry_at":     expiry_at.isoformat(),
            "created_at":    created_at.isoformat(),
            "expiry_hours":  expiry_hours,
            "age_minutes":   round(age_minutes, 1),
            "is_expired":    is_expired,
            "reason": (
                f"Signal valid until {expiry_at.strftime('%H:%M UTC')} "
                f"(age: {age_minutes:.0f} min, window: {expiry_hours}h)"
                if not is_expired else
                f"Signal expired at {expiry_at.strftime('%H:%M UTC')} "
                f"(age: {age_minutes:.0f} min, window: {expiry_hours}h)"
            ),
        }

    # ═════════════════════════════════════════════════════════════════════════
    # CHECK 12 — NEWS FILTER  (Issue #12)
    # ═════════════════════════════════════════════════════════════════════════

    def validate_news_filter(
        self,
        news_events: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Check for high-impact news events that should block trading.

        Issue #12: No news filter — now flags JOLTS, Beige Book, NFP,
        FOMC, CPI, and other high-impact events within ±30 minutes.

        Specifically monitors:
          - NFP (Non-Farm Payrolls) — highest impact
          - FOMC decisions and minutes
          - CPI / PPI inflation data
          - JOLTS job openings
          - Beige Book
          - GDP releases
          - Fed Chair speeches
        """
        if not news_events:
            return {
                "pass":            True,
                "blocking":        False,
                "high_impact":     [],
                "news_clear":      True,
                "reason":          "No news events in window — clear to trade",
            }

        now = datetime.now(timezone.utc)
        high_impact_blocking = []
        high_impact_warning  = []

        for event in news_events:
            impact   = str(event.get("impact", "")).lower()
            title    = str(event.get("event", event.get("title", "")))
            currency = str(event.get("currency", "")).upper()
            dt_obj   = event.get("datetime_obj")

            if isinstance(dt_obj, str):
                try:
                    dt_obj = datetime.fromisoformat(dt_obj)
                except ValueError:
                    dt_obj = None
            if dt_obj and dt_obj.tzinfo is None:
                dt_obj = dt_obj.replace(tzinfo=timezone.utc)

            is_high_impact = impact in ("high", "red")
            is_gold_relevant = currency in ("USD", "EUR", "XAU", "GBP")
            is_keyword_match = any(kw.upper() in title.upper() for kw in HIGH_IMPACT_NEWS_KEYWORDS)

            if not (is_high_impact or is_keyword_match):
                continue

            if dt_obj:
                minutes_to_event = (dt_obj - now).total_seconds() / 60
                in_blackout = -15 <= minutes_to_event <= 30  # 30 min before, 15 min after
                in_warning  = -30 <= minutes_to_event <= 60  # Extended warning window
            else:
                in_blackout = False
                in_warning  = False

            event_info = {
                "event":           title,
                "currency":        currency,
                "impact":          impact,
                "minutes_to_event": round(minutes_to_event, 1) if dt_obj else None,
                "in_blackout":     in_blackout,
                "gold_relevant":   is_gold_relevant,
            }

            if in_blackout and is_gold_relevant:
                high_impact_blocking.append(event_info)
            elif in_warning or is_keyword_match:
                high_impact_warning.append(event_info)

        passed = len(high_impact_blocking) == 0

        return {
            "pass":            passed,
            "blocking":        True,
            "high_impact":     high_impact_blocking + high_impact_warning,
            "blocking_events": high_impact_blocking,
            "warning_events":  high_impact_warning,
            "news_clear":      passed,
            "reason": (
                "News filter clear — no high-impact events in blackout window"
                if passed else
                f"HIGH-IMPACT NEWS BLOCKING: {high_impact_blocking[0]['event']} "
                f"({high_impact_blocking[0].get('minutes_to_event', '?'):.0f} min away)"
            ),
        }

    # ═════════════════════════════════════════════════════════════════════════
    # CHECK 13 — CONFLUENCE SCORE  (Issue #4 / overall)
    # ═════════════════════════════════════════════════════════════════════════

    def calculate_confluence_score(
        self,
        checks: Dict[str, Dict[str, Any]],
        signal: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Calculate overall confluence score from all checks.

        Score > 75% = HIGH CONFIDENCE (approved)
        Score 65–75% = MEDIUM CONFIDENCE (review recommended)
        Score < 65% = LOW CONFIDENCE (rejected)

        Weights:
          MTF alignment      — 20%
          SMC score          — 15%
          R:R quality        — 15%
          Regime logic       — 15%
          Confidence         — 10%
          Session quality    — 10%
          News clearance     — 10%
          SL anchoring       — 5%
        """
        weights = {
            "mtf_alignment":     0.20,
            "confidence":        0.10,
            "rr_ratio":          0.15,
            "regime_logic":      0.15,
            "session_quality":   0.10,
            "news_filter":       0.10,
            "sl_anchoring":      0.05,
            "entry_positioning": 0.10,
            "entry_band":        0.05,
        }

        score = 0.0
        component_scores: Dict[str, float] = {}

        for check_name, weight in weights.items():
            check = checks.get(check_name, {})
            if check.get("pass", False):
                # Full score for passing checks
                component_score = 100.0
            else:
                # Partial score for non-blocking failures
                component_score = 0.0 if check.get("blocking", True) else 40.0

            component_scores[check_name] = component_score
            score += component_score * weight

        # Bonus for high dynamic confidence
        dyn_conf = checks.get("confidence", {}).get("dynamic_confidence", 0)
        if dyn_conf >= 80:
            score = min(100.0, score + 5.0)

        # Bonus for MTF full alignment
        mtf_aligned = checks.get("mtf_alignment", {}).get("aligned_count", 0)
        if mtf_aligned == 3:
            score = min(100.0, score + 5.0)

        # Penalty for expired signal
        if checks.get("signal_expiry", {}).get("is_expired", False):
            score = max(0.0, score - 30.0)

        # Penalty for news blocking
        if not checks.get("news_filter", {}).get("pass", True):
            score = max(0.0, score - 25.0)

        approved = score >= self.min_confluence

        return {
            "pass":             approved,
            "blocking":         True,
            "score":            round(score, 1),
            "min_score":        self.min_confluence,
            "component_scores": component_scores,
            "tier": (
                "HIGH"     if score >= 85 else
                "MEDIUM"   if score >= 75 else
                "LOW"      if score >= 65 else
                "REJECTED"
            ),
            "reason": (
                f"Confluence score {score:.1f}% — "
                f"{'APPROVED' if approved else 'REJECTED'} "
                f"(min {self.min_confluence}%)"
            ),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────────────────────

signal_quality_validator = SignalQualityValidator()

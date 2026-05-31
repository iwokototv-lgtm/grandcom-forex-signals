"""
Trade Geometry Rater — Objective 4-Component Signal Quality Scoring
Gold Trading System v3.0.2

Rates every signal on four independent dimensions (1–10 scale each):
  1. Entry Price Rating   — how well-positioned the entry is relative to structure
  2. Stop Loss Rating     — how tight and logical the SL placement is
  3. Risk/Reward Rating   — quality of the R:R ratio
  4. Take Profit Rating   — how realistic and well-spaced the TP levels are

An Overall Geometry Score (1–10) is computed as a weighted average of the
four components.  The score drives an automatic recommendation:
  APPROVE  — score ≥ 7.0
  ADJUST   — score ≥ 5.0
  REJECT   — score < 5.0

All methods are pure functions (no I/O) so they can be called synchronously
from any context without awaiting.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

# Recommendation thresholds
APPROVE_THRESHOLD = 7.0
ADJUST_THRESHOLD  = 5.0

# Component weights (must sum to 1.0)
WEIGHT_ENTRY  = 0.25
WEIGHT_SL     = 0.25
WEIGHT_RR     = 0.30
WEIGHT_TP     = 0.20

# Ideal R:R targets
RR_IDEAL_MIN  = 2.0   # 1:2 — minimum acceptable
RR_IDEAL_MID  = 3.0   # 1:3 — good
RR_IDEAL_MAX  = 5.0   # 1:5 — excellent (beyond this, TP may be unrealistic)

# SL distance thresholds (as % of entry price)
SL_TIGHT_PCT  = 0.005   # 0.5% — very tight (risky)
SL_GOOD_PCT   = 0.015   # 1.5% — good balance
SL_WIDE_PCT   = 0.040   # 4.0% — wide (reduces R:R)

# Entry quality thresholds (distance from nearest structure as % of entry)
ENTRY_IDEAL_PCT = 0.003  # within 0.3% of structure — excellent
ENTRY_GOOD_PCT  = 0.010  # within 1.0% — good
ENTRY_POOR_PCT  = 0.025  # beyond 2.5% — poor


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _clamp(value: float, lo: float = 1.0, hi: float = 10.0) -> float:
    """Clamp a float to [lo, hi] and round to 2 decimal places."""
    return round(max(lo, min(hi, value)), 2)


def _linear_score(
    value: float,
    best: float,
    worst: float,
    score_best: float = 10.0,
    score_worst: float = 1.0,
) -> float:
    """
    Map *value* linearly from [worst, best] → [score_worst, score_best].
    Works for both ascending (best > worst) and descending (best < worst) scales.
    """
    if best == worst:
        return (score_best + score_worst) / 2
    ratio = (value - worst) / (best - worst)
    return _clamp(score_worst + ratio * (score_best - score_worst))


# ─────────────────────────────────────────────────────────────
# COMPONENT RATERS
# ─────────────────────────────────────────────────────────────

def rate_entry_price(
    signal_type: str,
    entry_price: float,
    sl_price: float,
    tp_levels: List[float],
    recent_high: Optional[float] = None,
    recent_low: Optional[float] = None,
    atr: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Rate the entry price quality (1–10).

    Scoring logic:
    - If recent_high / recent_low are provided, reward entries close to
      key structure levels (support for BUY, resistance for SELL).
    - If ATR is provided, penalise entries that are more than 1 ATR away
      from the nearest structure level.
    - Fallback: score based on the entry's position within the SL–TP range.

    Returns:
        {
            "score": float,          # 1–10
            "label": str,            # EXCELLENT | GOOD | FAIR | POOR
            "rationale": str,
        }
    """
    direction = signal_type.upper()

    # ── Fallback: position within SL–TP range ────────────────
    if tp_levels:
        tp1 = tp_levels[0]
        total_range = abs(tp1 - sl_price)
        if total_range > 0:
            if direction == "BUY":
                # Entry should be close to SL side (near support)
                entry_pct = (entry_price - sl_price) / total_range
            else:
                # Entry should be close to SL side (near resistance)
                entry_pct = (sl_price - entry_price) / total_range

            # Ideal: entry_pct ≈ 0.0–0.25 (entry near structure)
            # Poor:  entry_pct > 0.50 (entry already deep into the move)
            if entry_pct <= 0.15:
                base_score = 9.5
            elif entry_pct <= 0.25:
                base_score = 8.0
            elif entry_pct <= 0.40:
                base_score = 6.5
            elif entry_pct <= 0.55:
                base_score = 5.0
            else:
                base_score = 3.0
        else:
            base_score = 5.0
    else:
        base_score = 5.0

    # ── Bonus: proximity to structure ────────────────────────
    structure_bonus = 0.0
    structure_note  = ""
    if recent_high is not None and recent_low is not None:
        if direction == "BUY":
            dist_pct = abs(entry_price - recent_low) / entry_price
        else:
            dist_pct = abs(entry_price - recent_high) / entry_price

        if dist_pct <= ENTRY_IDEAL_PCT:
            structure_bonus = 1.0
            structure_note  = f"entry within {dist_pct*100:.2f}% of key structure"
        elif dist_pct <= ENTRY_GOOD_PCT:
            structure_bonus = 0.5
            structure_note  = f"entry {dist_pct*100:.2f}% from structure"
        elif dist_pct > ENTRY_POOR_PCT:
            structure_bonus = -1.0
            structure_note  = f"entry {dist_pct*100:.2f}% from structure (far)"

    # ── ATR penalty ───────────────────────────────────────────
    atr_penalty = 0.0
    atr_note    = ""
    if atr is not None and atr > 0:
        sl_dist = abs(entry_price - sl_price)
        if sl_dist > 2.0 * atr:
            atr_penalty = -1.5
            atr_note    = f"SL distance ({sl_dist:.5f}) > 2×ATR ({atr:.5f})"
        elif sl_dist < 0.3 * atr:
            atr_penalty = -0.5
            atr_note    = f"SL distance ({sl_dist:.5f}) < 0.3×ATR — may be too tight"

    score = _clamp(base_score + structure_bonus + atr_penalty)

    if score >= 8.5:
        label = "EXCELLENT"
    elif score >= 7.0:
        label = "GOOD"
    elif score >= 5.0:
        label = "FAIR"
    else:
        label = "POOR"

    parts = [f"position-in-range score {base_score:.1f}"]
    if structure_note:
        parts.append(structure_note)
    if atr_note:
        parts.append(atr_note)

    return {
        "score":     score,
        "label":     label,
        "rationale": "; ".join(parts),
    }


def rate_stop_loss(
    signal_type: str,
    entry_price: float,
    sl_price: float,
    atr: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Rate the stop-loss placement quality (1–10).

    Scoring logic:
    - SL distance as % of entry price is the primary driver.
    - If ATR is provided, reward SL distances in the 0.5–1.5 ATR range.

    Returns:
        {
            "score": float,
            "label": str,
            "sl_distance_pct": float,
            "sl_distance_atr": float | None,
            "rationale": str,
        }
    """
    sl_dist     = abs(entry_price - sl_price)
    sl_dist_pct = sl_dist / entry_price if entry_price > 0 else 0.0

    # ── Base score from SL distance % ────────────────────────
    if sl_dist_pct <= SL_TIGHT_PCT:
        # Too tight — likely to be stopped out by noise
        base_score = 3.0
        pct_note   = f"SL very tight ({sl_dist_pct*100:.3f}%) — noise risk"
    elif sl_dist_pct <= SL_GOOD_PCT:
        # Ideal range
        base_score = _linear_score(sl_dist_pct, SL_GOOD_PCT, SL_TIGHT_PCT, 9.5, 3.0)
        pct_note   = f"SL distance {sl_dist_pct*100:.3f}% — good"
    elif sl_dist_pct <= SL_WIDE_PCT:
        # Acceptable but wide
        base_score = _linear_score(sl_dist_pct, SL_WIDE_PCT, SL_GOOD_PCT, 5.0, 9.5)
        pct_note   = f"SL distance {sl_dist_pct*100:.3f}% — wide"
    else:
        # Very wide — poor R:R
        base_score = _linear_score(sl_dist_pct, 0.10, SL_WIDE_PCT, 1.0, 5.0)
        pct_note   = f"SL distance {sl_dist_pct*100:.3f}% — very wide"

    # ── ATR adjustment ────────────────────────────────────────
    atr_bonus = 0.0
    atr_note  = ""
    sl_dist_atr: Optional[float] = None
    if atr is not None and atr > 0:
        sl_dist_atr = round(sl_dist / atr, 2)
        if 0.5 <= sl_dist_atr <= 1.5:
            atr_bonus = 1.0
            atr_note  = f"SL at {sl_dist_atr}×ATR — ideal"
        elif sl_dist_atr < 0.3:
            atr_bonus = -1.5
            atr_note  = f"SL at {sl_dist_atr}×ATR — dangerously tight"
        elif sl_dist_atr > 3.0:
            atr_bonus = -1.0
            atr_note  = f"SL at {sl_dist_atr}×ATR — too wide"

    score = _clamp(base_score + atr_bonus)

    if score >= 8.5:
        label = "EXCELLENT"
    elif score >= 7.0:
        label = "GOOD"
    elif score >= 5.0:
        label = "FAIR"
    else:
        label = "POOR"

    parts = [pct_note]
    if atr_note:
        parts.append(atr_note)

    return {
        "score":           score,
        "label":           label,
        "sl_distance_pct": round(sl_dist_pct * 100, 4),
        "sl_distance_atr": sl_dist_atr,
        "rationale":       "; ".join(parts),
    }


def rate_risk_reward(
    signal_type: str,
    entry_price: float,
    sl_price: float,
    tp_levels: List[float],
) -> Dict[str, Any]:
    """
    Rate the risk/reward ratio quality (1–10).

    Uses the first TP level (TP1) for the primary R:R calculation.
    If multiple TP levels are present, also computes the blended R:R
    (average of all TPs) and uses the better of the two.

    Returns:
        {
            "score": float,
            "label": str,
            "rr_tp1": float,
            "rr_blended": float | None,
            "rationale": str,
        }
    """
    if not tp_levels:
        return {
            "score":       1.0,
            "label":       "POOR",
            "rr_tp1":      0.0,
            "rr_blended":  None,
            "rationale":   "No TP levels provided",
        }

    risk = abs(entry_price - sl_price)
    if risk <= 0:
        return {
            "score":       1.0,
            "label":       "POOR",
            "rr_tp1":      0.0,
            "rr_blended":  None,
            "rationale":   "Zero risk (entry == SL)",
        }

    tp1    = tp_levels[0]
    rr_tp1 = abs(tp1 - entry_price) / risk

    # Blended R:R across all TP levels
    rr_blended: Optional[float] = None
    if len(tp_levels) > 1:
        avg_reward = sum(abs(tp - entry_price) for tp in tp_levels) / len(tp_levels)
        rr_blended = round(avg_reward / risk, 2)

    # Use the better of TP1 and blended
    effective_rr = max(rr_tp1, rr_blended or 0.0)

    # Score mapping
    if effective_rr >= RR_IDEAL_MAX:
        score = 10.0
        note  = f"R:R {effective_rr:.2f} — exceptional"
    elif effective_rr >= RR_IDEAL_MID:
        score = _linear_score(effective_rr, RR_IDEAL_MAX, RR_IDEAL_MID, 10.0, 8.0)
        note  = f"R:R {effective_rr:.2f} — excellent"
    elif effective_rr >= RR_IDEAL_MIN:
        score = _linear_score(effective_rr, RR_IDEAL_MID, RR_IDEAL_MIN, 8.0, 6.0)
        note  = f"R:R {effective_rr:.2f} — good"
    elif effective_rr >= 1.5:
        score = _linear_score(effective_rr, RR_IDEAL_MIN, 1.5, 6.0, 4.0)
        note  = f"R:R {effective_rr:.2f} — acceptable"
    elif effective_rr >= 1.0:
        score = _linear_score(effective_rr, 1.5, 1.0, 4.0, 2.0)
        note  = f"R:R {effective_rr:.2f} — marginal"
    else:
        score = 1.0
        note  = f"R:R {effective_rr:.2f} — below 1:1 (unacceptable)"

    score = _clamp(score)

    if score >= 8.5:
        label = "EXCELLENT"
    elif score >= 7.0:
        label = "GOOD"
    elif score >= 5.0:
        label = "FAIR"
    else:
        label = "POOR"

    return {
        "score":      score,
        "label":      label,
        "rr_tp1":     round(rr_tp1, 2),
        "rr_blended": rr_blended,
        "rationale":  note,
    }


def rate_take_profit(
    signal_type: str,
    entry_price: float,
    sl_price: float,
    tp_levels: List[float],
    recent_high: Optional[float] = None,
    recent_low: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Rate the take-profit placement quality (1–10).

    Scoring logic:
    - Reward having multiple TP levels (partial profit-taking).
    - Reward TP levels that are evenly spaced (not clustered).
    - Penalise TP levels that exceed recent swing high/low by a large margin
      (unrealistic targets).
    - Reward TP levels that align with key structure levels.

    Returns:
        {
            "score": float,
            "label": str,
            "tp_count": int,
            "rationale": str,
        }
    """
    direction = signal_type.upper()

    if not tp_levels:
        return {
            "score":    1.0,
            "label":    "POOR",
            "tp_count": 0,
            "rationale": "No TP levels provided",
        }

    tp_count = len(tp_levels)
    risk     = abs(entry_price - sl_price)

    # ── Base score from TP count ──────────────────────────────
    if tp_count == 1:
        base_score = 6.0
        count_note = "single TP level"
    elif tp_count == 2:
        base_score = 7.5
        count_note = "2 TP levels — good partial-profit structure"
    elif tp_count == 3:
        base_score = 9.0
        count_note = "3 TP levels — excellent partial-profit structure"
    else:
        base_score = 8.5
        count_note = f"{tp_count} TP levels — comprehensive"

    # ── Spacing quality ───────────────────────────────────────
    spacing_bonus = 0.0
    spacing_note  = ""
    if tp_count >= 2:
        gaps = [abs(tp_levels[i + 1] - tp_levels[i]) for i in range(tp_count - 1)]
        avg_gap = sum(gaps) / len(gaps)
        if avg_gap > 0:
            cv = math.sqrt(sum((g - avg_gap) ** 2 for g in gaps) / len(gaps)) / avg_gap
            if cv < 0.20:
                spacing_bonus = 0.5
                spacing_note  = "evenly spaced TPs"
            elif cv > 0.60:
                spacing_bonus = -0.5
                spacing_note  = "unevenly spaced TPs"

    # ── Structure alignment bonus ─────────────────────────────
    structure_bonus = 0.0
    structure_note  = ""
    if recent_high is not None and recent_low is not None:
        if direction == "BUY":
            # TP1 should be below or at recent high
            tp1_vs_high = (tp_levels[0] - recent_high) / entry_price
            if tp1_vs_high > 0.05:
                structure_bonus = -1.0
                structure_note  = f"TP1 {tp1_vs_high*100:.1f}% above recent high — may be unrealistic"
            elif abs(tp1_vs_high) <= 0.005:
                structure_bonus = 0.5
                structure_note  = "TP1 aligns with recent high"
        else:
            # TP1 should be above or at recent low
            tp1_vs_low = (recent_low - tp_levels[0]) / entry_price
            if tp1_vs_low > 0.05:
                structure_bonus = -1.0
                structure_note  = f"TP1 {tp1_vs_low*100:.1f}% below recent low — may be unrealistic"
            elif abs(tp1_vs_low) <= 0.005:
                structure_bonus = 0.5
                structure_note  = "TP1 aligns with recent low"

    score = _clamp(base_score + spacing_bonus + structure_bonus)

    if score >= 8.5:
        label = "EXCELLENT"
    elif score >= 7.0:
        label = "GOOD"
    elif score >= 5.0:
        label = "FAIR"
    else:
        label = "POOR"

    parts = [count_note]
    if spacing_note:
        parts.append(spacing_note)
    if structure_note:
        parts.append(structure_note)

    return {
        "score":    score,
        "label":    label,
        "tp_count": tp_count,
        "rationale": "; ".join(parts),
    }


# ─────────────────────────────────────────────────────────────
# OVERALL GEOMETRY RATER
# ─────────────────────────────────────────────────────────────

class TradeGeometryRater:
    """
    Compute a comprehensive geometry rating for a trading signal.

    Usage::

        rater  = TradeGeometryRater()
        result = rater.rate(signal_dict)
        # result["overall_score"]      → float 1–10
        # result["recommendation"]     → "APPROVE" | "ADJUST" | "REJECT"
        # result["components"]["entry"] → {score, label, rationale}
        # result["components"]["sl"]    → {score, label, ...}
        # result["components"]["rr"]    → {score, label, rr_tp1, ...}
        # result["components"]["tp"]    → {score, label, tp_count, ...}
    """

    # ── Public API ────────────────────────────────────────────

    def rate(
        self,
        signal: Dict[str, Any],
        market_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Rate a signal's trade geometry.

        Args:
            signal: Signal document.  Required keys:
                - ``type``        (str)  — "BUY" or "SELL"
                - ``entry_price`` (float)
                - ``sl_price``    (float)
                - ``tp_levels``   (list[float])
            market_context: Optional dict with keys:
                - ``recent_high`` (float)
                - ``recent_low``  (float)
                - ``atr``         (float)

        Returns:
            Full geometry rating dict (see class docstring).
        """
        try:
            return self._rate_internal(signal, market_context or {})
        except Exception as exc:
            logger.error(f"TradeGeometryRater.rate() failed: {exc}", exc_info=True)
            return self._error_result(str(exc))

    def rate_batch(
        self,
        signals: List[Dict[str, Any]],
        market_context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Rate a list of signals and return a list of geometry results."""
        return [self.rate(s, market_context) for s in signals]

    # ── Internal ──────────────────────────────────────────────

    def _rate_internal(
        self,
        signal: Dict[str, Any],
        ctx: Dict[str, Any],
    ) -> Dict[str, Any]:
        signal_type = signal.get("type", signal.get("signal", "BUY")).upper()
        entry_price = float(signal["entry_price"])
        sl_price    = float(signal["sl_price"])
        tp_levels   = [float(t) for t in signal.get("tp_levels", [])]

        recent_high = ctx.get("recent_high")
        recent_low  = ctx.get("recent_low")
        atr         = ctx.get("atr")

        # ── Rate each component ───────────────────────────────
        entry_rating = rate_entry_price(
            signal_type, entry_price, sl_price, tp_levels,
            recent_high, recent_low, atr,
        )
        sl_rating = rate_stop_loss(
            signal_type, entry_price, sl_price, atr,
        )
        rr_rating = rate_risk_reward(
            signal_type, entry_price, sl_price, tp_levels,
        )
        tp_rating = rate_take_profit(
            signal_type, entry_price, sl_price, tp_levels,
            recent_high, recent_low,
        )

        # ── Weighted overall score ────────────────────────────
        overall = (
            entry_rating["score"] * WEIGHT_ENTRY
            + sl_rating["score"]  * WEIGHT_SL
            + rr_rating["score"]  * WEIGHT_RR
            + tp_rating["score"]  * WEIGHT_TP
        )
        overall = _clamp(overall)

        # ── Recommendation ────────────────────────────────────
        if overall >= APPROVE_THRESHOLD:
            recommendation = "APPROVE"
        elif overall >= ADJUST_THRESHOLD:
            recommendation = "ADJUST"
        else:
            recommendation = "REJECT"

        # ── Improvement hints ─────────────────────────────────
        hints = self._build_hints(entry_rating, sl_rating, rr_rating, tp_rating)

        result = {
            "overall_score":    overall,
            "recommendation":   recommendation,
            "components": {
                "entry": entry_rating,
                "sl":    sl_rating,
                "rr":    rr_rating,
                "tp":    tp_rating,
            },
            "weights": {
                "entry": WEIGHT_ENTRY,
                "sl":    WEIGHT_SL,
                "rr":    WEIGHT_RR,
                "tp":    WEIGHT_TP,
            },
            "thresholds": {
                "approve": APPROVE_THRESHOLD,
                "adjust":  ADJUST_THRESHOLD,
            },
            "improvement_hints": hints,
            "signal_type":  signal_type,
            "entry_price":  entry_price,
            "sl_price":     sl_price,
            "tp_levels":    tp_levels,
        }

        logger.info(
            "📐 Geometry rating: %s %s → %.2f/10 (%s)",
            signal.get("pair", "?"),
            signal_type,
            overall,
            recommendation,
        )
        return result

    @staticmethod
    def _build_hints(
        entry: Dict,
        sl: Dict,
        rr: Dict,
        tp: Dict,
    ) -> List[str]:
        """Generate actionable improvement hints for sub-optimal components."""
        hints: List[str] = []

        if entry["score"] < 7.0:
            hints.append(
                f"Entry ({entry['score']}/10): {entry['rationale']}. "
                "Consider waiting for price to pull back closer to key structure."
            )
        if sl["score"] < 7.0:
            hints.append(
                f"Stop Loss ({sl['score']}/10): {sl['rationale']}. "
                "Adjust SL to 0.5–1.5× ATR from entry for optimal placement."
            )
        if rr["score"] < 7.0:
            hints.append(
                f"Risk/Reward ({rr['score']}/10): {rr['rationale']}. "
                "Target a minimum 1:2 R:R; 1:3 or better is preferred."
            )
        if tp["score"] < 7.0:
            hints.append(
                f"Take Profit ({tp['score']}/10): {tp['rationale']}. "
                "Add 2–3 TP levels at key structure points for partial profit-taking."
            )

        return hints

    @staticmethod
    def _error_result(error: str) -> Dict[str, Any]:
        return {
            "overall_score":    1.0,
            "recommendation":   "REJECT",
            "error":            error,
            "components":       {},
            "improvement_hints": [f"Rating failed: {error}"],
        }


# ─────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────

trade_geometry_rater = TradeGeometryRater()

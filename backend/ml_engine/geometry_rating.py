"""
Trade Geometry Rating System
Gold Trading System v3.0.2

Provides an objective, quantifiable geometry score (1–10) for every signal
component so managers can make data-driven approval decisions.

Rating components
─────────────────
  1. Entry Price     — How well the entry is placed relative to structure
  2. Stop Loss       — How well the SL is placed beyond key invalidation
  3. Risk/Reward     — Quality of the R:R ratio across all TP levels
  4. Take Profits    — How well TPs align with liquidity / structure targets

Overall score = average of the four component scores.

Decision thresholds
───────────────────
  ≥ 8.0  → APPROVE   (excellent geometry)
  ≥ 7.0  → APPROVE   (good geometry, meets minimum bar)
  ≥ 5.5  → ADJUST    (acceptable but needs improvement)
  < 5.5  → REJECT    (poor geometry, do not trade)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

# Approval thresholds
APPROVE_THRESHOLD: float = 7.0   # ≥ 7.0 → APPROVE
ADJUST_THRESHOLD:  float = 5.5   # ≥ 5.5 → ADJUST  (< 7.0)
# < 5.5 → REJECT

# Ideal R:R targets for scoring
RR_EXCELLENT: float = 3.0   # ≥ 3.0 → score 9–10
RR_GOOD:      float = 2.0   # ≥ 2.0 → score 7–8
RR_MINIMUM:   float = 1.5   # ≥ 1.5 → score 5–6
RR_POOR:      float = 1.0   # ≥ 1.0 → score 3–4
# < 1.0 → score 1–2

# Entry placement thresholds (distance from key level as % of ATR)
ENTRY_IDEAL_ZONE_PCT:  float = 0.25   # within 25 % of ATR from key level
ENTRY_GOOD_ZONE_PCT:   float = 0.50   # within 50 %
ENTRY_FAIR_ZONE_PCT:   float = 0.75   # within 75 %
ENTRY_POOR_ZONE_PCT:   float = 1.00   # within 100 %

# SL placement thresholds (distance beyond key level as % of ATR)
SL_IDEAL_BUFFER_PCT:   float = 0.10   # 10–30 % of ATR beyond key level
SL_GOOD_BUFFER_PCT:    float = 0.30   # 30–60 %
SL_FAIR_BUFFER_PCT:    float = 0.60   # 60–100 %
SL_MAX_BUFFER_PCT:     float = 1.50   # > 150 % → too wide

# TP alignment thresholds (distance from nearest structure level as % of ATR)
TP_IDEAL_ALIGN_PCT:    float = 0.20   # within 20 % of ATR from structure
TP_GOOD_ALIGN_PCT:     float = 0.40   # within 40 %
TP_FAIR_ALIGN_PCT:     float = 0.70   # within 70 %


# ─────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────

@dataclass
class ComponentRating:
    """Rating result for a single geometry component."""
    score:       float                    # 1.0 – 10.0
    label:       str                      # e.g. "EXCELLENT", "GOOD", "FAIR", "POOR"
    explanation: str                      # Human-readable reason for the score
    guidelines:  List[str] = field(default_factory=list)  # Adjustment suggestions


@dataclass
class GeometryRatingResult:
    """Full geometry rating for a signal."""
    signal_id:        Optional[str]
    pair:             str
    signal_type:      str                 # BUY | SELL
    entry_price:      float
    sl_price:         float
    tp_levels:        List[float]

    # Component ratings
    entry_rating:     ComponentRating
    sl_rating:        ComponentRating
    rr_rating:        ComponentRating
    tp_rating:        ComponentRating

    # Aggregate
    overall_score:    float               # Average of 4 components
    recommendation:   str                 # APPROVE | ADJUST | REJECT
    summary:          str                 # One-line summary for managers

    # Metadata
    atr:              Optional[float] = None
    rr_ratios:        List[float] = field(default_factory=list)
    rated_at:         Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict suitable for JSON responses."""
        return {
            "signal_id":     self.signal_id,
            "pair":          self.pair,
            "signal_type":   self.signal_type,
            "entry_price":   self.entry_price,
            "sl_price":      self.sl_price,
            "tp_levels":     self.tp_levels,
            "atr":           self.atr,
            "rr_ratios":     [round(r, 2) for r in self.rr_ratios],
            "rated_at":      self.rated_at,
            "overall_score": round(self.overall_score, 2),
            "recommendation": self.recommendation,
            "summary":       self.summary,
            "breakdown": {
                "entry": {
                    "score":       round(self.entry_rating.score, 2),
                    "label":       self.entry_rating.label,
                    "explanation": self.entry_rating.explanation,
                    "guidelines":  self.entry_rating.guidelines,
                },
                "stop_loss": {
                    "score":       round(self.sl_rating.score, 2),
                    "label":       self.sl_rating.label,
                    "explanation": self.sl_rating.explanation,
                    "guidelines":  self.sl_rating.guidelines,
                },
                "risk_reward": {
                    "score":       round(self.rr_rating.score, 2),
                    "label":       self.rr_rating.label,
                    "explanation": self.rr_rating.explanation,
                    "guidelines":  self.rr_rating.guidelines,
                },
                "take_profits": {
                    "score":       round(self.tp_rating.score, 2),
                    "label":       self.tp_rating.label,
                    "explanation": self.tp_rating.explanation,
                    "guidelines":  self.tp_rating.guidelines,
                },
            },
        }


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _score_label(score: float) -> str:
    """Map a numeric score to a human-readable quality label."""
    if score >= 9.0:
        return "EXCELLENT"
    if score >= 7.0:
        return "GOOD"
    if score >= 5.5:
        return "FAIR"
    if score >= 3.0:
        return "POOR"
    return "VERY_POOR"


def _clamp(value: float, lo: float = 1.0, hi: float = 10.0) -> float:
    """Clamp a score to the valid 1–10 range."""
    return max(lo, min(hi, value))


def _rr_for_tp(entry: float, sl: float, tp: float) -> float:
    """Calculate the risk/reward ratio for a single TP level."""
    risk   = abs(entry - sl)
    reward = abs(tp - entry)
    if risk <= 0:
        return 0.0
    return reward / risk


# ─────────────────────────────────────────────────────────────
# GeometryRating
# ─────────────────────────────────────────────────────────────

class GeometryRating:
    """
    Objective geometry scoring system for Gold trading signals.

    Usage
    ─────
    ::

        rater = GeometryRating()
        result = rater.rate(
            signal_type  = "BUY",
            entry_price  = 2345.00,
            sl_price     = 2330.00,
            tp_levels    = [2365.00, 2385.00, 2410.00],
            atr          = 18.50,
            support      = 2332.00,   # nearest support (BUY) or resistance (SELL)
            resistance   = 2368.00,   # nearest resistance (BUY) or support (SELL)
            pair         = "XAUUSD",
            signal_id    = "abc123",
        )
        print(result.overall_score)      # e.g. 7.75
        print(result.recommendation)     # e.g. "APPROVE"
        print(result.to_dict())          # full JSON-ready breakdown
    """

    # ── Public API ────────────────────────────────────────────

    def rate(
        self,
        signal_type:  str,
        entry_price:  float,
        sl_price:     float,
        tp_levels:    List[float],
        atr:          float,
        support:      Optional[float] = None,
        resistance:   Optional[float] = None,
        pair:         str = "XAUUSD",
        signal_id:    Optional[str] = None,
        extra_levels: Optional[List[float]] = None,
    ) -> GeometryRatingResult:
        """
        Rate all four geometry components and return a full result.

        Args:
            signal_type:  "BUY" or "SELL" (case-insensitive).
            entry_price:  Proposed entry price.
            sl_price:     Proposed stop-loss price.
            tp_levels:    List of take-profit levels (1–5 values).
            atr:          Current Average True Range (used for distance scaling).
            support:      Nearest support level (optional but improves accuracy).
            resistance:   Nearest resistance level (optional but improves accuracy).
            pair:         Trading pair symbol (default "XAUUSD").
            signal_id:    Optional signal identifier for traceability.
            extra_levels: Additional structure levels for TP alignment scoring.

        Returns:
            GeometryRatingResult with per-component scores and recommendation.

        Raises:
            ValueError: If signal_type is not BUY/SELL, or if price inputs are
                        structurally invalid (e.g. BUY with SL above entry).
        """
        from datetime import datetime

        direction = signal_type.upper()
        if direction not in ("BUY", "SELL"):
            raise ValueError(f"signal_type must be BUY or SELL, got '{signal_type}'")

        self._validate_structure(direction, entry_price, sl_price, tp_levels)

        # Ensure ATR is usable
        safe_atr = max(atr, 0.01)

        # Compute R:R ratios for all TP levels
        rr_ratios = [_rr_for_tp(entry_price, sl_price, tp) for tp in tp_levels]

        # Rate each component
        entry_rating = self.rate_entry_price(
            direction, entry_price, sl_price, safe_atr, support, resistance
        )
        sl_rating = self.rate_stop_loss(
            direction, entry_price, sl_price, safe_atr, support, resistance
        )
        rr_rating = self.rate_risk_reward(rr_ratios)
        tp_rating = self.rate_take_profits(
            direction, entry_price, sl_price, tp_levels, safe_atr,
            support, resistance, extra_levels
        )

        overall = self.calculate_overall_score(
            entry_rating.score,
            sl_rating.score,
            rr_rating.score,
            tp_rating.score,
        )
        recommendation = self.get_recommendation(overall)
        summary = self._build_summary(
            pair, direction, overall, recommendation,
            entry_rating, sl_rating, rr_rating, tp_rating,
        )

        return GeometryRatingResult(
            signal_id    = signal_id,
            pair         = pair,
            signal_type  = direction,
            entry_price  = entry_price,
            sl_price     = sl_price,
            tp_levels    = tp_levels,
            entry_rating = entry_rating,
            sl_rating    = sl_rating,
            rr_rating    = rr_rating,
            tp_rating    = tp_rating,
            overall_score = overall,
            recommendation = recommendation,
            summary      = summary,
            atr          = safe_atr,
            rr_ratios    = rr_ratios,
            rated_at     = datetime.utcnow().isoformat(),
        )

    # ── Component raters ──────────────────────────────────────

    def rate_entry_price(
        self,
        signal_type:  str,
        entry_price:  float,
        sl_price:     float,
        atr:          float,
        support:      Optional[float] = None,
        resistance:   Optional[float] = None,
    ) -> ComponentRating:
        """
        Rate entry price placement on a 1–10 scale.

        Scoring logic
        ─────────────
        For a BUY signal the ideal entry is as close as possible to a
        support level (or demand zone) without being below it.  For a
        SELL signal the ideal entry is as close as possible to a
        resistance level (or supply zone) without being above it.

        The distance between the entry and the nearest key level is
        expressed as a fraction of the ATR.  Smaller fractions earn
        higher scores.

        Score bands
        ───────────
        10   — Entry is within 10 % of ATR from the key level (perfect)
         9   — Within 25 %
         8   — Within 40 %
         7   — Within 55 %
         6   — Within 70 %
         5   — Within 85 %
         4   — Within 100 %
         3   — Within 125 %
         2   — Within 150 %
         1   — Beyond 150 % of ATR (very poor placement)

        Bonus / penalty
        ───────────────
        +0.5  if entry is at a confluence of support AND resistance
        -1.0  if entry is on the wrong side of the key level
        -0.5  if no key level is provided (cannot assess placement)
        """
        direction = signal_type.upper()

        # Determine the relevant key level
        key_level = self._get_entry_key_level(direction, support, resistance)

        if key_level is None:
            # No structure data — use SL distance as a proxy
            sl_distance = abs(entry_price - sl_price)
            atr_ratio   = sl_distance / atr
            # Ideal SL distance is 1–2 ATR
            if atr_ratio <= 1.0:
                base_score = 7.0
            elif atr_ratio <= 1.5:
                base_score = 6.0
            elif atr_ratio <= 2.0:
                base_score = 5.0
            else:
                base_score = 3.5
            return ComponentRating(
                score       = _clamp(base_score - 0.5),
                label       = _score_label(base_score - 0.5),
                explanation = (
                    "No key structure level provided. Entry scored using SL "
                    f"distance ({sl_distance:.2f} = {atr_ratio:.2f}× ATR)."
                ),
                guidelines  = [
                    "Provide support/resistance levels for a more accurate entry score.",
                    "Ideal entry is within 0.5–1.0 ATR of a key structure level.",
                ],
            )

        distance    = abs(entry_price - key_level)
        atr_ratio   = distance / atr

        # Check if entry is on the wrong side of the key level
        wrong_side = self._entry_wrong_side(direction, entry_price, key_level)

        # Base score from distance table
        base_score = self._distance_to_score(atr_ratio)

        # Penalty for wrong side
        if wrong_side:
            base_score -= 1.0
            wrong_side_note = " Entry is on the wrong side of the key level (−1.0)."
        else:
            wrong_side_note = ""

        # Bonus for confluence (both support and resistance provided and close)
        confluence_bonus = 0.0
        if support is not None and resistance is not None:
            zone_width = abs(resistance - support)
            if zone_width <= atr * 0.5:
                confluence_bonus = 0.5

        final_score = _clamp(base_score + confluence_bonus)

        explanation = (
            f"Entry {entry_price:.2f} is {distance:.2f} ({atr_ratio:.2f}× ATR) "
            f"from the key {direction} level at {key_level:.2f}.{wrong_side_note}"
        )
        if confluence_bonus:
            explanation += f" Confluence zone detected (+{confluence_bonus})."

        guidelines = self._entry_guidelines(direction, atr_ratio, wrong_side, entry_price, key_level, atr)

        return ComponentRating(
            score       = final_score,
            label       = _score_label(final_score),
            explanation = explanation,
            guidelines  = guidelines,
        )

    def rate_stop_loss(
        self,
        signal_type:  str,
        entry_price:  float,
        sl_price:     float,
        atr:          float,
        support:      Optional[float] = None,
        resistance:   Optional[float] = None,
    ) -> ComponentRating:
        """
        Rate stop-loss placement on a 1–10 scale.

        Scoring logic
        ─────────────
        A well-placed SL sits just beyond the structural invalidation
        point — far enough to avoid noise, close enough to keep risk
        tight.

        For a BUY signal the SL should be below the nearest support.
        For a SELL signal the SL should be above the nearest resistance.

        The buffer between the SL and the key level is expressed as a
        fraction of the ATR.

        Score bands (buffer as % of ATR)
        ─────────────────────────────────
        10   — Buffer is 5–20 % of ATR (just beyond the level, very tight)
         9   — 20–35 %
         8   — 35–50 %
         7   — 50–70 %
         6   — 70–90 %
         5   — 90–110 %
         4   — 110–140 %
         3   — 140–180 %
         2   — 180–220 %
         1   — > 220 % (far too wide or inside the level)

        Penalty
        ───────
        -2.0  if SL is on the wrong side of the key level (inside structure)
        -1.0  if SL distance from entry is < 0.5 ATR (too tight, likely to be hit)
        -0.5  if SL distance from entry is > 3.0 ATR (too wide, poor risk control)
        """
        direction = signal_type.upper()
        sl_key    = self._get_sl_key_level(direction, support, resistance)

        sl_distance_from_entry = abs(entry_price - sl_price)
        entry_atr_ratio        = sl_distance_from_entry / atr

        # Penalty for SL too tight or too wide relative to entry
        tight_penalty = 0.0
        wide_penalty  = 0.0
        if entry_atr_ratio < 0.5:
            tight_penalty = 1.0
        elif entry_atr_ratio > 3.0:
            wide_penalty = 0.5

        if sl_key is None:
            # No structure — score purely on SL distance from entry
            if entry_atr_ratio <= 1.0:
                base_score = 7.5
            elif entry_atr_ratio <= 1.5:
                base_score = 6.5
            elif entry_atr_ratio <= 2.0:
                base_score = 5.5
            elif entry_atr_ratio <= 2.5:
                base_score = 4.5
            else:
                base_score = 3.0
            final_score = _clamp(base_score - tight_penalty - wide_penalty - 0.5)
            return ComponentRating(
                score       = final_score,
                label       = _score_label(final_score),
                explanation = (
                    f"No key structure level provided. SL is {sl_distance_from_entry:.2f} "
                    f"({entry_atr_ratio:.2f}× ATR) from entry."
                ),
                guidelines  = [
                    "Provide support/resistance levels for a more accurate SL score.",
                    "Ideal SL is 1.0–1.5× ATR from entry, just beyond a key level.",
                ],
            )

        buffer      = abs(sl_price - sl_key)
        buffer_pct  = buffer / atr

        # Check if SL is on the wrong side (inside structure)
        wrong_side  = self._sl_wrong_side(direction, sl_price, sl_key)
        wrong_penalty = 2.0 if wrong_side else 0.0

        # Score from buffer table
        base_score  = self._buffer_to_score(buffer_pct)
        final_score = _clamp(base_score - wrong_penalty - tight_penalty - wide_penalty)

        wrong_note = " SL is inside the key level — invalidation not respected (−2.0)." if wrong_side else ""
        tight_note = f" SL is very tight ({entry_atr_ratio:.2f}× ATR from entry, −1.0)." if tight_penalty else ""
        wide_note  = f" SL is very wide ({entry_atr_ratio:.2f}× ATR from entry, −0.5)." if wide_penalty else ""

        explanation = (
            f"SL {sl_price:.2f} is {buffer:.2f} ({buffer_pct:.2f}× ATR) beyond "
            f"the key {direction} invalidation level at {sl_key:.2f}."
            f"{wrong_note}{tight_note}{wide_note}"
        )

        guidelines = self._sl_guidelines(
            direction, buffer_pct, wrong_side, entry_atr_ratio,
            sl_price, sl_key, entry_price, atr
        )

        return ComponentRating(
            score       = final_score,
            label       = _score_label(final_score),
            explanation = explanation,
            guidelines  = guidelines,
        )

    def rate_risk_reward(
        self,
        rr_ratios: List[float],
    ) -> ComponentRating:
        """
        Rate the risk/reward ratio on a 1–10 scale.

        Scoring logic
        ─────────────
        The primary R:R used for scoring is TP1 (the first take-profit
        level).  Additional TP levels contribute a weighted bonus.

        TP1 score table
        ───────────────
        10   — R:R ≥ 4.0
         9   — R:R ≥ 3.5
         8   — R:R ≥ 3.0
         7   — R:R ≥ 2.5
         6   — R:R ≥ 2.0
         5   — R:R ≥ 1.5
         4   — R:R ≥ 1.2
         3   — R:R ≥ 1.0
         2   — R:R ≥ 0.7
         1   — R:R < 0.7

        Multi-TP bonus
        ──────────────
        +0.3  for each additional TP level with R:R ≥ 2.0 (max +0.9)
        +0.5  if average R:R across all TPs ≥ 3.0
        """
        if not rr_ratios:
            return ComponentRating(
                score       = 1.0,
                label       = "VERY_POOR",
                explanation = "No TP levels provided — R:R cannot be calculated.",
                guidelines  = ["Add at least one TP level with R:R ≥ 1.5."],
            )

        tp1_rr     = rr_ratios[0]
        base_score = self._rr_to_score(tp1_rr)

        # Multi-TP bonus
        bonus = 0.0
        if len(rr_ratios) > 1:
            extra_good = sum(1 for r in rr_ratios[1:] if r >= 2.0)
            bonus += min(extra_good * 0.3, 0.9)
        avg_rr = sum(rr_ratios) / len(rr_ratios)
        if avg_rr >= 3.0:
            bonus += 0.5

        final_score = _clamp(base_score + bonus)

        # Build explanation
        rr_str = ", ".join(f"{r:.2f}" for r in rr_ratios)
        explanation = (
            f"TP1 R:R = {tp1_rr:.2f} (score {base_score:.1f}). "
            f"All R:R ratios: [{rr_str}]. Average R:R = {avg_rr:.2f}."
        )
        if bonus > 0:
            explanation += f" Multi-TP bonus: +{bonus:.1f}."

        guidelines = self._rr_guidelines(tp1_rr, avg_rr, rr_ratios)

        return ComponentRating(
            score       = final_score,
            label       = _score_label(final_score),
            explanation = explanation,
            guidelines  = guidelines,
        )

    def rate_take_profits(
        self,
        signal_type:   str,
        entry_price:   float,
        sl_price:      float,
        tp_levels:     List[float],
        atr:           float,
        support:       Optional[float] = None,
        resistance:    Optional[float] = None,
        extra_levels:  Optional[List[float]] = None,
    ) -> ComponentRating:
        """
        Rate take-profit alignment with market structure on a 1–10 scale.

        Scoring logic
        ─────────────
        Each TP level is evaluated for how closely it aligns with a
        known structure level (resistance for BUY, support for SELL).
        Alignment is measured as the distance from the nearest structure
        level expressed as a fraction of the ATR.

        Per-TP alignment score
        ──────────────────────
        10   — Within 10 % of ATR from a structure level
         9   — Within 20 %
         8   — Within 35 %
         7   — Within 50 %
         6   — Within 65 %
         5   — Within 80 %
         4   — Within 100 %
         3   — Within 130 %
         2   — Within 160 %
         1   — Beyond 160 % (no structural basis)

        The overall TP score is the weighted average of per-TP scores,
        with TP1 weighted 2×, TP2 weighted 1.5×, and TP3+ weighted 1×.

        Bonus / penalty
        ───────────────
        +0.5  if all TPs are on the correct side of entry
        -1.0  if any TP is on the wrong side of entry
        +0.3  for each TP that sits just below (BUY) or above (SELL) a
              liquidity cluster (within 20 % of ATR)
        """
        direction = signal_type.upper()

        if not tp_levels:
            return ComponentRating(
                score       = 1.0,
                label       = "VERY_POOR",
                explanation = "No TP levels provided.",
                guidelines  = ["Add at least one TP level aligned with a key structure level."],
            )

        # Collect all available structure levels for alignment
        structure_levels = self._collect_structure_levels(
            direction, support, resistance, extra_levels
        )

        # Check directional validity
        wrong_direction_count = 0
        for tp in tp_levels:
            if direction == "BUY" and tp <= entry_price:
                wrong_direction_count += 1
            elif direction == "SELL" and tp >= entry_price:
                wrong_direction_count += 1

        direction_penalty = 1.0 * wrong_direction_count

        # Score each TP
        tp_scores: List[float] = []
        tp_details: List[str]  = []

        for i, tp in enumerate(tp_levels):
            if structure_levels:
                nearest_dist = min(abs(tp - lvl) for lvl in structure_levels)
                align_ratio  = nearest_dist / atr
                tp_score     = self._alignment_to_score(align_ratio)
                nearest_lvl  = min(structure_levels, key=lambda lvl: abs(tp - lvl))
                tp_details.append(
                    f"TP{i+1}={tp:.2f} → {nearest_dist:.2f} ({align_ratio:.2f}× ATR) "
                    f"from level {nearest_lvl:.2f} → score {tp_score:.1f}"
                )
            else:
                # No structure — score based on R:R implied by this TP
                rr = _rr_for_tp(entry_price, sl_price, tp)
                tp_score = self._rr_to_score(rr) * 0.8   # slight penalty for no structure
                tp_details.append(
                    f"TP{i+1}={tp:.2f} → R:R {rr:.2f} → score {tp_score:.1f} (no structure)"
                )
            tp_scores.append(tp_score)

        # Weighted average (TP1 = 2×, TP2 = 1.5×, TP3+ = 1×)
        weights = [2.0, 1.5] + [1.0] * max(0, len(tp_scores) - 2)
        weights = weights[: len(tp_scores)]
        weighted_sum = sum(s * w for s, w in zip(tp_scores, weights))
        weight_total = sum(weights)
        avg_score    = weighted_sum / weight_total if weight_total > 0 else 1.0

        # Directional bonus/penalty
        direction_bonus = 0.5 if wrong_direction_count == 0 else 0.0

        final_score = _clamp(avg_score + direction_bonus - direction_penalty)

        explanation = (
            f"{len(tp_levels)} TP level(s) rated. "
            + " | ".join(tp_details)
        )
        if wrong_direction_count:
            explanation += (
                f" {wrong_direction_count} TP(s) on wrong side of entry (−{direction_penalty:.1f})."
            )

        guidelines = self._tp_guidelines(
            direction, tp_levels, entry_price, structure_levels, atr, avg_score
        )

        return ComponentRating(
            score       = final_score,
            label       = _score_label(final_score),
            explanation = explanation,
            guidelines  = guidelines,
        )

    # ── Aggregate ─────────────────────────────────────────────

    def calculate_overall_score(
        self,
        entry_score: float,
        sl_score:    float,
        rr_score:    float,
        tp_score:    float,
    ) -> float:
        """
        Calculate the overall geometry score as a weighted average.

        Weights
        ───────
        Risk/Reward  — 30 % (most critical: defines trade viability)
        Stop Loss    — 30 % (second most critical: defines risk)
        Entry Price  — 25 % (important: defines execution quality)
        Take Profits — 15 % (supporting: defines reward targets)

        Returns a score in the range [1.0, 10.0].
        """
        weighted = (
            rr_score    * 0.30
            + sl_score  * 0.30
            + entry_score * 0.25
            + tp_score  * 0.15
        )
        return _clamp(round(weighted, 4))

    def get_recommendation(self, overall_score: float) -> str:
        """
        Translate an overall geometry score into an approval recommendation.

        Thresholds
        ──────────
        ≥ 8.0  → APPROVE   (excellent — send to trading immediately)
        ≥ 7.0  → APPROVE   (good — meets minimum quality bar)
        ≥ 5.5  → ADJUST    (acceptable structure but needs improvement)
        < 5.5  → REJECT    (poor geometry — do not trade)
        """
        if overall_score >= APPROVE_THRESHOLD:
            return "APPROVE"
        if overall_score >= ADJUST_THRESHOLD:
            return "ADJUST"
        return "REJECT"

    # ── Validation ────────────────────────────────────────────

    def _validate_structure(
        self,
        direction:   str,
        entry_price: float,
        sl_price:    float,
        tp_levels:   List[float],
    ) -> None:
        """
        Validate that the price structure is directionally consistent.

        BUY  : sl < entry < tp1 ≤ tp2 ≤ …
        SELL : sl > entry > tp1 ≥ tp2 ≥ …

        Raises ValueError with a descriptive message on failure.
        """
        if entry_price <= 0:
            raise ValueError(f"entry_price must be > 0, got {entry_price}")
        if sl_price <= 0:
            raise ValueError(f"sl_price must be > 0, got {sl_price}")
        if not tp_levels:
            raise ValueError("tp_levels must contain at least one value")

        if direction == "BUY":
            if sl_price >= entry_price:
                raise ValueError(
                    f"BUY signal: sl_price ({sl_price}) must be < entry_price ({entry_price})"
                )
            if tp_levels[0] <= entry_price:
                raise ValueError(
                    f"BUY signal: tp_levels[0] ({tp_levels[0]}) must be > entry_price ({entry_price})"
                )
            for i in range(1, len(tp_levels)):
                if tp_levels[i] < tp_levels[i - 1]:
                    raise ValueError(
                        f"BUY signal: tp_levels must be non-decreasing "
                        f"(tp_levels[{i-1}]={tp_levels[i-1]} > tp_levels[{i}]={tp_levels[i]})"
                    )
        else:  # SELL
            if sl_price <= entry_price:
                raise ValueError(
                    f"SELL signal: sl_price ({sl_price}) must be > entry_price ({entry_price})"
                )
            if tp_levels[0] >= entry_price:
                raise ValueError(
                    f"SELL signal: tp_levels[0] ({tp_levels[0]}) must be < entry_price ({entry_price})"
                )
            for i in range(1, len(tp_levels)):
                if tp_levels[i] > tp_levels[i - 1]:
                    raise ValueError(
                        f"SELL signal: tp_levels must be non-increasing "
                        f"(tp_levels[{i-1}]={tp_levels[i-1]} < tp_levels[{i}]={tp_levels[i]})"
                    )

    # ── Key-level helpers ─────────────────────────────────────

    def _get_entry_key_level(
        self,
        direction: str,
        support:   Optional[float],
        resistance: Optional[float],
    ) -> Optional[float]:
        """
        Return the key level most relevant for entry placement scoring.

        BUY  → support (entry should be near support)
        SELL → resistance (entry should be near resistance)
        """
        if direction == "BUY":
            return support
        return resistance

    def _get_sl_key_level(
        self,
        direction:  str,
        support:    Optional[float],
        resistance: Optional[float],
    ) -> Optional[float]:
        """
        Return the key level most relevant for SL placement scoring.

        BUY  → support (SL should be just below support)
        SELL → resistance (SL should be just above resistance)
        """
        if direction == "BUY":
            return support
        return resistance

    def _entry_wrong_side(
        self,
        direction:   str,
        entry_price: float,
        key_level:   float,
    ) -> bool:
        """
        Return True if the entry is on the wrong side of the key level.

        BUY  → entry should be ABOVE support; wrong if entry < support
        SELL → entry should be BELOW resistance; wrong if entry > resistance
        """
        if direction == "BUY":
            return entry_price < key_level
        return entry_price > key_level

    def _sl_wrong_side(
        self,
        direction: str,
        sl_price:  float,
        sl_key:    float,
    ) -> bool:
        """
        Return True if the SL is on the wrong side of the invalidation level.

        BUY  → SL should be BELOW support; wrong if sl > support
        SELL → SL should be ABOVE resistance; wrong if sl < resistance
        """
        if direction == "BUY":
            return sl_price > sl_key
        return sl_price < sl_key

    def _collect_structure_levels(
        self,
        direction:    str,
        support:      Optional[float],
        resistance:   Optional[float],
        extra_levels: Optional[List[float]],
    ) -> List[float]:
        """Collect all available structure levels for TP alignment scoring."""
        levels: List[float] = []
        if support is not None:
            levels.append(support)
        if resistance is not None:
            levels.append(resistance)
        if extra_levels:
            levels.extend(extra_levels)
        return levels

    # ── Score tables ──────────────────────────────────────────

    def _distance_to_score(self, atr_ratio: float) -> float:
        """
        Convert a distance (as a multiple of ATR) to a 1–10 entry score.
        Smaller distance = better entry placement.
        """
        if atr_ratio <= 0.10:
            return 10.0
        if atr_ratio <= 0.25:
            return 9.0
        if atr_ratio <= 0.40:
            return 8.0
        if atr_ratio <= 0.55:
            return 7.0
        if atr_ratio <= 0.70:
            return 6.0
        if atr_ratio <= 0.85:
            return 5.0
        if atr_ratio <= 1.00:
            return 4.0
        if atr_ratio <= 1.25:
            return 3.0
        if atr_ratio <= 1.50:
            return 2.0
        return 1.0

    def _buffer_to_score(self, buffer_pct: float) -> float:
        """
        Convert a SL buffer (as a fraction of ATR) to a 1–10 SL score.
        Ideal buffer is 5–35 % of ATR — tight but beyond the level.
        """
        if 0.05 <= buffer_pct <= 0.20:
            return 10.0
        if 0.20 < buffer_pct <= 0.35:
            return 9.0
        if 0.35 < buffer_pct <= 0.50:
            return 8.0
        if 0.50 < buffer_pct <= 0.70:
            return 7.0
        if 0.70 < buffer_pct <= 0.90:
            return 6.0
        if 0.90 < buffer_pct <= 1.10:
            return 5.0
        if 1.10 < buffer_pct <= 1.40:
            return 4.0
        if 1.40 < buffer_pct <= 1.80:
            return 3.0
        if 1.80 < buffer_pct <= 2.20:
            return 2.0
        # buffer < 0.05 (inside the level) or > 2.20 (far too wide)
        return 1.0

    def _rr_to_score(self, rr: float) -> float:
        """Convert a risk/reward ratio to a 1–10 score."""
        if rr >= 4.0:
            return 10.0
        if rr >= 3.5:
            return 9.0
        if rr >= 3.0:
            return 8.0
        if rr >= 2.5:
            return 7.0
        if rr >= 2.0:
            return 6.0
        if rr >= 1.5:
            return 5.0
        if rr >= 1.2:
            return 4.0
        if rr >= 1.0:
            return 3.0
        if rr >= 0.7:
            return 2.0
        return 1.0

    def _alignment_to_score(self, align_ratio: float) -> float:
        """
        Convert a TP alignment distance (as a fraction of ATR) to a 1–10 score.
        Smaller distance = better alignment with structure.
        """
        if align_ratio <= 0.10:
            return 10.0
        if align_ratio <= 0.20:
            return 9.0
        if align_ratio <= 0.35:
            return 8.0
        if align_ratio <= 0.50:
            return 7.0
        if align_ratio <= 0.65:
            return 6.0
        if align_ratio <= 0.80:
            return 5.0
        if align_ratio <= 1.00:
            return 4.0
        if align_ratio <= 1.30:
            return 3.0
        if align_ratio <= 1.60:
            return 2.0
        return 1.0

    # ── Guideline builders ────────────────────────────────────

    def _entry_guidelines(
        self,
        direction:   str,
        atr_ratio:   float,
        wrong_side:  bool,
        entry_price: float,
        key_level:   float,
        atr:         float,
    ) -> List[str]:
        """Generate actionable entry adjustment guidelines."""
        tips: List[str] = []

        if wrong_side:
            if direction == "BUY":
                ideal = key_level + atr * 0.10
                tips.append(
                    f"Entry is below support. Move entry to ≥ {ideal:.2f} "
                    f"(just above support at {key_level:.2f})."
                )
            else:
                ideal = key_level - atr * 0.10
                tips.append(
                    f"Entry is above resistance. Move entry to ≤ {ideal:.2f} "
                    f"(just below resistance at {key_level:.2f})."
                )

        if atr_ratio > 1.0:
            if direction == "BUY":
                ideal = key_level + atr * 0.15
                tips.append(
                    f"Entry is too far from support. Target entry near {ideal:.2f} "
                    f"(within 0.25× ATR of support at {key_level:.2f})."
                )
            else:
                ideal = key_level - atr * 0.15
                tips.append(
                    f"Entry is too far from resistance. Target entry near {ideal:.2f} "
                    f"(within 0.25× ATR of resistance at {key_level:.2f})."
                )
        elif atr_ratio > 0.55:
            tips.append(
                f"Entry placement is acceptable but could be improved. "
                f"Aim for within 0.40× ATR of the key level ({key_level:.2f})."
            )

        if not tips:
            tips.append(
                f"Entry placement is {'excellent' if atr_ratio <= 0.25 else 'good'}. "
                f"No adjustment needed."
            )

        return tips

    def _sl_guidelines(
        self,
        direction:        str,
        buffer_pct:       float,
        wrong_side:       bool,
        entry_atr_ratio:  float,
        sl_price:         float,
        sl_key:           float,
        entry_price:      float,
        atr:              float,
    ) -> List[str]:
        """Generate actionable SL adjustment guidelines."""
        tips: List[str] = []

        if wrong_side:
            if direction == "BUY":
                ideal_sl = sl_key - atr * 0.15
                tips.append(
                    f"SL is above support — it will be hit by normal retracements. "
                    f"Move SL to {ideal_sl:.2f} (below support at {sl_key:.2f})."
                )
            else:
                ideal_sl = sl_key + atr * 0.15
                tips.append(
                    f"SL is below resistance — it will be hit by normal retracements. "
                    f"Move SL to {ideal_sl:.2f} (above resistance at {sl_key:.2f})."
                )

        if buffer_pct < 0.05:
            tips.append(
                "SL buffer is too tight — price noise will trigger it. "
                f"Add at least {atr * 0.10:.2f} buffer beyond the key level."
            )
        elif buffer_pct > 1.50:
            tips.append(
                f"SL buffer is too wide ({buffer_pct:.2f}× ATR). "
                "This increases risk unnecessarily. "
                f"Target a buffer of 0.10–0.35× ATR beyond the key level."
            )

        if entry_atr_ratio < 0.5:
            tips.append(
                f"SL is very close to entry ({entry_atr_ratio:.2f}× ATR). "
                "This is likely to be triggered by normal spread/noise. "
                "Consider widening the SL or moving the entry closer to the key level."
            )
        elif entry_atr_ratio > 3.0:
            tips.append(
                f"SL is very far from entry ({entry_atr_ratio:.2f}× ATR). "
                "This creates excessive risk per trade. "
                "Consider tightening the SL or reducing position size."
            )

        if not tips:
            tips.append(
                "SL placement is well-positioned beyond the key invalidation level. "
                "No adjustment needed."
            )

        return tips

    def _rr_guidelines(
        self,
        tp1_rr:    float,
        avg_rr:    float,
        rr_ratios: List[float],
    ) -> List[str]:
        """Generate actionable R:R improvement guidelines."""
        tips: List[str] = []

        if tp1_rr < 1.0:
            tips.append(
                f"TP1 R:R of {tp1_rr:.2f} is below 1:1 — this trade risks more than it gains. "
                "Move TP1 further from entry or tighten the SL to achieve at least 1.5:1."
            )
        elif tp1_rr < 1.5:
            tips.append(
                f"TP1 R:R of {tp1_rr:.2f} is below the 1.5:1 minimum. "
                "Extend TP1 to achieve at least 1.5:1, ideally 2.0:1."
            )
        elif tp1_rr < 2.0:
            tips.append(
                f"TP1 R:R of {tp1_rr:.2f} is acceptable. "
                "Consider extending TP1 to 2.0:1 if structure allows."
            )

        if avg_rr < 2.0 and len(rr_ratios) > 1:
            tips.append(
                f"Average R:R across all TPs is {avg_rr:.2f}. "
                "Aim for an average of ≥ 2.5:1 by extending TP2/TP3 targets."
            )

        if len(rr_ratios) == 1:
            tips.append(
                "Only one TP level is set. Adding TP2 (≥ 3.0:1) and TP3 (≥ 4.5:1) "
                "improves the overall R:R score and allows partial profit-taking."
            )

        if not tips:
            tips.append(
                f"R:R profile is {'excellent' if tp1_rr >= 3.0 else 'good'}. "
                f"TP1 at {tp1_rr:.2f}:1, average {avg_rr:.2f}:1. No adjustment needed."
            )

        return tips

    def _tp_guidelines(
        self,
        direction:        str,
        tp_levels:        List[float],
        entry_price:      float,
        structure_levels: List[float],
        atr:              float,
        avg_score:        float,
    ) -> List[str]:
        """Generate actionable TP alignment guidelines."""
        tips: List[str] = []

        if not structure_levels:
            tips.append(
                "No structure levels provided. TP targets should be placed at "
                "key resistance levels (BUY) or support levels (SELL) identified "
                "from the chart. Use pivot points, swing highs/lows, or order blocks."
            )
            return tips

        for i, tp in enumerate(tp_levels):
            nearest_dist = min(abs(tp - lvl) for lvl in structure_levels)
            align_ratio  = nearest_dist / atr
            nearest_lvl  = min(structure_levels, key=lambda lvl: abs(tp - lvl))

            if align_ratio > 1.0:
                if direction == "BUY":
                    tips.append(
                        f"TP{i+1} ({tp:.2f}) is {nearest_dist:.2f} from the nearest "
                        f"resistance at {nearest_lvl:.2f}. Move TP{i+1} to align with "
                        f"a resistance level (within 0.5× ATR = {atr * 0.5:.2f})."
                    )
                else:
                    tips.append(
                        f"TP{i+1} ({tp:.2f}) is {nearest_dist:.2f} from the nearest "
                        f"support at {nearest_lvl:.2f}. Move TP{i+1} to align with "
                        f"a support level (within 0.5× ATR = {atr * 0.5:.2f})."
                    )

        if avg_score >= 7.0 and not tips:
            tips.append(
                "TP levels are well-aligned with market structure. No adjustment needed."
            )
        elif not tips:
            tips.append(
                "TP alignment is acceptable. For higher scores, align each TP "
                "within 0.35× ATR of a key structure level."
            )

        return tips

    # ── Summary builder ───────────────────────────────────────

    def _build_summary(
        self,
        pair:         str,
        direction:    str,
        overall:      float,
        recommendation: str,
        entry_rating: ComponentRating,
        sl_rating:    ComponentRating,
        rr_rating:    ComponentRating,
        tp_rating:    ComponentRating,
    ) -> str:
        """Build a concise one-line summary for the manager dashboard."""
        weakest_score = min(
            entry_rating.score, sl_rating.score,
            rr_rating.score, tp_rating.score
        )
        weakest_name = {
            entry_rating.score: "entry",
            sl_rating.score:    "SL",
            rr_rating.score:    "R:R",
            tp_rating.score:    "TP",
        }[weakest_score]

        action_map = {
            "APPROVE": "✅ APPROVE",
            "ADJUST":  "⚠️  ADJUST",
            "REJECT":  "❌ REJECT",
        }
        action = action_map.get(recommendation, recommendation)

        return (
            f"{pair} {direction} — Overall {overall:.2f}/10 → {action} | "
            f"Entry {entry_rating.score:.1f} | SL {sl_rating.score:.1f} | "
            f"R:R {rr_rating.score:.1f} | TP {tp_rating.score:.1f} | "
            f"Weakest: {weakest_name} ({weakest_score:.1f})"
        )


# ─────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────

geometry_rater = GeometryRating()

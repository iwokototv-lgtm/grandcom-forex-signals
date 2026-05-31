"""
Trade Geometry Rating System v1.0
Gold Trading System v3.0.2

Provides an objective, quantifiable geometry quality score (1–10) for every
signal component before manager approval.  Four sub-ratings are computed
independently and averaged into an overall score:

  1. Entry Price Rating   — How well the entry is placed relative to structure
  2. Stop Loss Rating     — How well the SL is placed relative to structure
  3. Risk/Reward Rating   — Quality of the R:R ratio across TP levels
  4. Take Profit Rating   — How well TPs align with structural targets

Overall score ≥ 7.0  → APPROVE
Overall score 5.0–6.9 → ADJUST
Overall score < 5.0  → REJECT

Usage:
    from ml_engine.geometry_rating import GeometryRating, geometry_rater

    rating = geometry_rater.rate_signal(
        signal_type="BUY",
        entry_price=2345.50,
        sl_price=2330.00,
        tp_levels=[2365.00, 2385.00, 2410.00],
        current_price=2346.00,
        atr=12.5,
        swing_high=2390.00,
        swing_low=2328.00,
        nearest_support=2332.00,
        nearest_resistance=2368.00,
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

APPROVE_THRESHOLD = 7.0   # Overall score ≥ 7.0 → APPROVE
ADJUST_THRESHOLD  = 5.0   # Overall score 5.0–6.9 → ADJUST
                           # Overall score < 5.0  → REJECT

# Ideal R:R thresholds
RR_EXCELLENT = 3.0   # ≥ 3.0 → score 9–10
RR_GOOD      = 2.0   # ≥ 2.0 → score 7–8
RR_MINIMUM   = 1.5   # ≥ 1.5 → score 5–6
RR_POOR      = 1.0   # ≥ 1.0 → score 3–4
               # < 1.0 → score 1–2

# Entry proximity thresholds (as fraction of ATR)
ENTRY_IDEAL_ZONE   = 0.25   # Within 0.25 ATR of structure → excellent
ENTRY_GOOD_ZONE    = 0.50   # Within 0.50 ATR → good
ENTRY_FAIR_ZONE    = 1.00   # Within 1.00 ATR → fair
ENTRY_POOR_ZONE    = 1.50   # Within 1.50 ATR → poor
                             # > 1.50 ATR → very poor

# SL buffer thresholds (as fraction of ATR)
SL_IDEAL_BUFFER    = 0.10   # 0.10–0.30 ATR beyond structure → ideal
SL_GOOD_BUFFER     = 0.30   # 0.30–0.60 ATR → good
SL_FAIR_BUFFER     = 0.60   # 0.60–1.00 ATR → fair
SL_WIDE_BUFFER     = 1.00   # > 1.00 ATR → too wide
SL_TIGHT_BUFFER    = 0.05   # < 0.05 ATR → too tight (stop-hunt risk)

# TP alignment thresholds (fraction of distance to structural target)
TP_ALIGNED         = 0.05   # Within 5% of structural level → aligned
TP_NEAR            = 0.10   # Within 10% → near
TP_FAR             = 0.20   # Within 20% → far
                             # > 20% → misaligned


# ─────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────

@dataclass
class ComponentRating:
    """Rating for a single geometry component."""
    score:       float                    # 1.0 – 10.0
    label:       str                      # e.g. "EXCELLENT", "GOOD", "FAIR", "POOR", "VERY_POOR"
    explanation: str                      # Human-readable explanation
    guidelines:  List[str] = field(default_factory=list)  # Adjustment suggestions


@dataclass
class GeometryRatingResult:
    """Complete geometry rating for a signal."""
    signal_type:    str                   # BUY or SELL
    entry_rating:   ComponentRating
    sl_rating:      ComponentRating
    rr_rating:      ComponentRating
    tp_rating:      ComponentRating
    overall_score:  float                 # Average of the four component scores
    recommendation: str                   # APPROVE | ADJUST | REJECT
    summary:        str                   # One-line summary for managers
    adjustment_guidelines: List[str]      # Consolidated list of all adjustments needed

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict suitable for JSON responses."""
        return {
            "signal_type":   self.signal_type,
            "overall_score": round(self.overall_score, 2),
            "recommendation": self.recommendation,
            "summary":       self.summary,
            "components": {
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
            "adjustment_guidelines": self.adjustment_guidelines,
            "thresholds": {
                "approve": APPROVE_THRESHOLD,
                "adjust":  ADJUST_THRESHOLD,
            },
        }


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _score_label(score: float) -> str:
    """Map a numeric score to a human-readable label."""
    if score >= 9.0:
        return "EXCELLENT"
    if score >= 7.0:
        return "GOOD"
    if score >= 5.0:
        return "FAIR"
    if score >= 3.0:
        return "POOR"
    return "VERY_POOR"


def _clamp(value: float, lo: float = 1.0, hi: float = 10.0) -> float:
    """Clamp a value to [lo, hi]."""
    return max(lo, min(hi, value))


# ─────────────────────────────────────────────────────────────
# Main Rating Class
# ─────────────────────────────────────────────────────────────

class GeometryRating:
    """
    Rates the structural geometry of a trading signal on a 1–10 scale.

    All four component ratings are independent; the overall score is their
    unweighted average.  Each component returns a score, a label, a plain-
    English explanation, and specific adjustment guidelines.
    """

    # ── Entry Price Rating ────────────────────────────────────

    def rate_entry_price(
        self,
        signal_type:         str,
        entry_price:         float,
        current_price:       float,
        nearest_support:     float,
        nearest_resistance:  float,
        atr:                 float,
        swing_high:          Optional[float] = None,
        swing_low:           Optional[float] = None,
    ) -> ComponentRating:
        """
        Rate entry placement relative to market structure (1–10).

        Scoring logic:
        - BUY: ideal entry is at or just above a support level / demand zone.
          The closer the entry is to the nearest support (within ATR multiples),
          the higher the score.
        - SELL: ideal entry is at or just below a resistance level / supply zone.
          The closer the entry is to the nearest resistance, the higher the score.

        Additional bonus:
        - Entry aligns with a swing high/low (OTE zone) → +1 point
        - Entry is chasing price (far from structure) → penalty
        """
        if atr <= 0:
            atr = abs(entry_price * 0.005)  # Fallback: 0.5% of price

        direction = signal_type.upper()
        guidelines: List[str] = []

        if direction == "BUY":
            # Distance from entry to nearest support (positive = entry above support)
            dist_to_support = entry_price - nearest_support
            dist_atr = dist_to_support / atr if atr > 0 else 999.0

            if dist_atr <= ENTRY_IDEAL_ZONE:
                score = 9.5
                explanation = (
                    f"Entry at {entry_price:.5g} is within {dist_atr:.2f} ATR of support "
                    f"({nearest_support:.5g}) — optimal demand zone placement."
                )
            elif dist_atr <= ENTRY_GOOD_ZONE:
                score = 8.0
                explanation = (
                    f"Entry at {entry_price:.5g} is {dist_atr:.2f} ATR above support "
                    f"({nearest_support:.5g}) — good structural placement."
                )
            elif dist_atr <= ENTRY_FAIR_ZONE:
                score = 6.0
                explanation = (
                    f"Entry at {entry_price:.5g} is {dist_atr:.2f} ATR above support "
                    f"({nearest_support:.5g}) — acceptable but not ideal."
                )
                guidelines.append(
                    f"Consider waiting for a pullback closer to support at {nearest_support:.5g}."
                )
            elif dist_atr <= ENTRY_POOR_ZONE:
                score = 4.0
                explanation = (
                    f"Entry at {entry_price:.5g} is {dist_atr:.2f} ATR above support "
                    f"({nearest_support:.5g}) — entry is chasing price."
                )
                guidelines.append(
                    f"Adjust entry down toward {nearest_support + atr * 0.3:.5g} "
                    f"(0.3 ATR above support)."
                )
            else:
                score = 2.0
                explanation = (
                    f"Entry at {entry_price:.5g} is {dist_atr:.2f} ATR above support "
                    f"({nearest_support:.5g}) — significantly chasing price, high risk."
                )
                guidelines.append(
                    f"Do not enter here. Wait for price to retrace to "
                    f"{nearest_support + atr * 0.2:.5g}–{nearest_support + atr * 0.5:.5g}."
                )

            # Bonus: entry near swing low (OTE zone)
            if swing_low is not None:
                dist_swing = abs(entry_price - swing_low) / atr
                if dist_swing <= 0.3:
                    score = _clamp(score + 1.0)
                    explanation += " Entry aligns with recent swing low (OTE zone) — bonus."

        else:  # SELL
            # Distance from entry to nearest resistance (positive = entry below resistance)
            dist_to_resistance = nearest_resistance - entry_price
            dist_atr = dist_to_resistance / atr if atr > 0 else 999.0

            if dist_atr <= ENTRY_IDEAL_ZONE:
                score = 9.5
                explanation = (
                    f"Entry at {entry_price:.5g} is within {dist_atr:.2f} ATR of resistance "
                    f"({nearest_resistance:.5g}) — optimal supply zone placement."
                )
            elif dist_atr <= ENTRY_GOOD_ZONE:
                score = 8.0
                explanation = (
                    f"Entry at {entry_price:.5g} is {dist_atr:.2f} ATR below resistance "
                    f"({nearest_resistance:.5g}) — good structural placement."
                )
            elif dist_atr <= ENTRY_FAIR_ZONE:
                score = 6.0
                explanation = (
                    f"Entry at {entry_price:.5g} is {dist_atr:.2f} ATR below resistance "
                    f"({nearest_resistance:.5g}) — acceptable but not ideal."
                )
                guidelines.append(
                    f"Consider waiting for a rally closer to resistance at {nearest_resistance:.5g}."
                )
            elif dist_atr <= ENTRY_POOR_ZONE:
                score = 4.0
                explanation = (
                    f"Entry at {entry_price:.5g} is {dist_atr:.2f} ATR below resistance "
                    f"({nearest_resistance:.5g}) — entry is chasing price downward."
                )
                guidelines.append(
                    f"Adjust entry up toward {nearest_resistance - atr * 0.3:.5g} "
                    f"(0.3 ATR below resistance)."
                )
            else:
                score = 2.0
                explanation = (
                    f"Entry at {entry_price:.5g} is {dist_atr:.2f} ATR below resistance "
                    f"({nearest_resistance:.5g}) — significantly chasing price, high risk."
                )
                guidelines.append(
                    f"Do not enter here. Wait for price to rally to "
                    f"{nearest_resistance - atr * 0.5:.5g}–{nearest_resistance - atr * 0.2:.5g}."
                )

            # Bonus: entry near swing high (OTE zone)
            if swing_high is not None:
                dist_swing = abs(entry_price - swing_high) / atr
                if dist_swing <= 0.3:
                    score = _clamp(score + 1.0)
                    explanation += " Entry aligns with recent swing high (OTE zone) — bonus."

        score = _clamp(score)
        return ComponentRating(
            score=score,
            label=_score_label(score),
            explanation=explanation,
            guidelines=guidelines,
        )

    # ── Stop Loss Rating ──────────────────────────────────────

    def rate_stop_loss(
        self,
        signal_type:        str,
        entry_price:        float,
        sl_price:           float,
        nearest_support:    float,
        nearest_resistance: float,
        atr:                float,
        swing_high:         Optional[float] = None,
        swing_low:          Optional[float] = None,
    ) -> ComponentRating:
        """
        Rate stop loss placement relative to market structure (1–10).

        Scoring logic:
        - BUY: SL should be placed just below the nearest support level.
          A small buffer (0.10–0.30 ATR) below support is ideal.
          Too tight (< 0.05 ATR) risks stop-hunt; too wide (> 1.0 ATR) wastes R.
        - SELL: SL should be placed just above the nearest resistance level.
          Same buffer logic applies.

        Additional checks:
        - SL beyond a swing high/low → structural protection bonus
        - SL inside a liquidity cluster → penalty
        """
        if atr <= 0:
            atr = abs(entry_price * 0.005)

        direction = signal_type.upper()
        guidelines: List[str] = []

        if direction == "BUY":
            # SL must be below entry for BUY
            if sl_price >= entry_price:
                return ComponentRating(
                    score=1.0,
                    label="VERY_POOR",
                    explanation=(
                        f"INVALID: SL ({sl_price:.5g}) is at or above entry ({entry_price:.5g}) "
                        f"for a BUY trade. This is a structural error."
                    ),
                    guidelines=[
                        f"Move SL below entry. Suggested: {nearest_support - atr * 0.2:.5g} "
                        f"(0.2 ATR below support at {nearest_support:.5g})."
                    ],
                )

            # Buffer below support
            buffer = nearest_support - sl_price
            buffer_atr = buffer / atr

            if SL_TIGHT_BUFFER <= buffer_atr <= SL_IDEAL_BUFFER:
                score = 9.5
                explanation = (
                    f"SL at {sl_price:.5g} is {buffer_atr:.2f} ATR below support "
                    f"({nearest_support:.5g}) — ideal structural protection."
                )
            elif buffer_atr <= SL_GOOD_BUFFER:
                score = 8.0
                explanation = (
                    f"SL at {sl_price:.5g} is {buffer_atr:.2f} ATR below support "
                    f"({nearest_support:.5g}) — good placement with adequate buffer."
                )
            elif buffer_atr <= SL_FAIR_BUFFER:
                score = 6.0
                explanation = (
                    f"SL at {sl_price:.5g} is {buffer_atr:.2f} ATR below support "
                    f"({nearest_support:.5g}) — acceptable but wider than ideal."
                )
                guidelines.append(
                    f"Consider tightening SL to {nearest_support - atr * 0.2:.5g} "
                    f"to improve R:R ratio."
                )
            elif buffer_atr < SL_TIGHT_BUFFER:
                score = 4.0
                explanation = (
                    f"SL at {sl_price:.5g} is only {buffer_atr:.2f} ATR below support "
                    f"({nearest_support:.5g}) — dangerously tight, high stop-hunt risk."
                )
                guidelines.append(
                    f"Move SL to at least {nearest_support - atr * 0.15:.5g} "
                    f"(0.15 ATR below support) to avoid stop hunts."
                )
            else:  # buffer_atr > SL_WIDE_BUFFER
                score = 3.0
                explanation = (
                    f"SL at {sl_price:.5g} is {buffer_atr:.2f} ATR below support "
                    f"({nearest_support:.5g}) — excessively wide, poor R:R impact."
                )
                guidelines.append(
                    f"Tighten SL to {nearest_support - atr * 0.25:.5g} "
                    f"(0.25 ATR below support) to improve risk efficiency."
                )

            # Bonus: SL is below a swing low (structural protection)
            if swing_low is not None and sl_price < swing_low:
                score = _clamp(score + 0.5)
                explanation += f" SL is below swing low ({swing_low:.5g}) — structural protection."

        else:  # SELL
            # SL must be above entry for SELL
            if sl_price <= entry_price:
                return ComponentRating(
                    score=1.0,
                    label="VERY_POOR",
                    explanation=(
                        f"INVALID: SL ({sl_price:.5g}) is at or below entry ({entry_price:.5g}) "
                        f"for a SELL trade. This is a structural error."
                    ),
                    guidelines=[
                        f"Move SL above entry. Suggested: {nearest_resistance + atr * 0.2:.5g} "
                        f"(0.2 ATR above resistance at {nearest_resistance:.5g})."
                    ],
                )

            # Buffer above resistance
            buffer = sl_price - nearest_resistance
            buffer_atr = buffer / atr

            if SL_TIGHT_BUFFER <= buffer_atr <= SL_IDEAL_BUFFER:
                score = 9.5
                explanation = (
                    f"SL at {sl_price:.5g} is {buffer_atr:.2f} ATR above resistance "
                    f"({nearest_resistance:.5g}) — ideal structural protection."
                )
            elif buffer_atr <= SL_GOOD_BUFFER:
                score = 8.0
                explanation = (
                    f"SL at {sl_price:.5g} is {buffer_atr:.2f} ATR above resistance "
                    f"({nearest_resistance:.5g}) — good placement with adequate buffer."
                )
            elif buffer_atr <= SL_FAIR_BUFFER:
                score = 6.0
                explanation = (
                    f"SL at {sl_price:.5g} is {buffer_atr:.2f} ATR above resistance "
                    f"({nearest_resistance:.5g}) — acceptable but wider than ideal."
                )
                guidelines.append(
                    f"Consider tightening SL to {nearest_resistance + atr * 0.2:.5g} "
                    f"to improve R:R ratio."
                )
            elif buffer_atr < SL_TIGHT_BUFFER:
                score = 4.0
                explanation = (
                    f"SL at {sl_price:.5g} is only {buffer_atr:.2f} ATR above resistance "
                    f"({nearest_resistance:.5g}) — dangerously tight, high stop-hunt risk."
                )
                guidelines.append(
                    f"Move SL to at least {nearest_resistance + atr * 0.15:.5g} "
                    f"(0.15 ATR above resistance) to avoid stop hunts."
                )
            else:  # buffer_atr > SL_WIDE_BUFFER
                score = 3.0
                explanation = (
                    f"SL at {sl_price:.5g} is {buffer_atr:.2f} ATR above resistance "
                    f"({nearest_resistance:.5g}) — excessively wide, poor R:R impact."
                )
                guidelines.append(
                    f"Tighten SL to {nearest_resistance + atr * 0.25:.5g} "
                    f"(0.25 ATR above resistance) to improve risk efficiency."
                )

            # Bonus: SL is above a swing high (structural protection)
            if swing_high is not None and sl_price > swing_high:
                score = _clamp(score + 0.5)
                explanation += f" SL is above swing high ({swing_high:.5g}) — structural protection."

        score = _clamp(score)
        return ComponentRating(
            score=score,
            label=_score_label(score),
            explanation=explanation,
            guidelines=guidelines,
        )

    # ── Risk/Reward Rating ────────────────────────────────────

    def rate_risk_reward(
        self,
        entry_price: float,
        sl_price:    float,
        tp_levels:   List[float],
        signal_type: str,
    ) -> ComponentRating:
        """
        Rate the risk/reward ratio across all TP levels (1–10).

        Scoring is based on the *best achievable* R:R (TP1 minimum, TP3 ideal):
        - TP1 R:R ≥ 1.5 is the minimum acceptable threshold
        - TP2 R:R ≥ 2.0 is good
        - TP3 R:R ≥ 3.0 is excellent

        The score reflects the weighted quality of the full TP ladder.
        """
        guidelines: List[str] = []
        risk = abs(entry_price - sl_price)

        if risk <= 0:
            return ComponentRating(
                score=1.0,
                label="VERY_POOR",
                explanation="Cannot compute R:R — entry and SL are at the same price.",
                guidelines=["Ensure SL is separated from entry by at least 1 ATR."],
            )

        if not tp_levels:
            return ComponentRating(
                score=1.0,
                label="VERY_POOR",
                explanation="No TP levels provided — R:R cannot be evaluated.",
                guidelines=["Add at least one TP level at a minimum 1.5:1 R:R."],
            )

        direction = signal_type.upper()
        rr_values: List[float] = []

        for tp in tp_levels:
            reward = (
                tp - entry_price if direction == "BUY" else entry_price - tp
            )
            if reward > 0:
                rr_values.append(reward / risk)
            else:
                rr_values.append(0.0)

        tp1_rr = rr_values[0] if rr_values else 0.0
        best_rr = max(rr_values) if rr_values else 0.0
        avg_rr  = sum(rr_values) / len(rr_values) if rr_values else 0.0

        # Score based on TP1 (minimum acceptable) and best TP
        if tp1_rr >= RR_EXCELLENT:
            score = 10.0
            explanation = (
                f"Exceptional R:R — TP1 at {tp1_rr:.2f}:1, best TP at {best_rr:.2f}:1. "
                f"All TP levels exceed 3:1 threshold."
            )
        elif tp1_rr >= RR_GOOD:
            score = 8.5
            explanation = (
                f"Strong R:R — TP1 at {tp1_rr:.2f}:1, best TP at {best_rr:.2f}:1. "
                f"TP1 meets the 2:1 minimum for quality setups."
            )
        elif tp1_rr >= RR_MINIMUM:
            score = 6.5
            explanation = (
                f"Acceptable R:R — TP1 at {tp1_rr:.2f}:1, best TP at {best_rr:.2f}:1. "
                f"TP1 meets the 1.5:1 minimum threshold."
            )
            if best_rr >= RR_GOOD:
                score = 7.0
                explanation += f" Extended TPs improve overall profile."
            else:
                guidelines.append(
                    f"Extend TP2/TP3 to achieve ≥ 2:1 R:R. "
                    f"Suggested TP2: {entry_price + risk * 2.5:.5g} (2.5:1)."
                    if direction == "BUY" else
                    f"Extend TP2/TP3 to achieve ≥ 2:1 R:R. "
                    f"Suggested TP2: {entry_price - risk * 2.5:.5g} (2.5:1)."
                )
        elif tp1_rr >= RR_POOR:
            score = 4.0
            explanation = (
                f"Poor R:R — TP1 at {tp1_rr:.2f}:1, best TP at {best_rr:.2f}:1. "
                f"TP1 is below the 1.5:1 minimum threshold."
            )
            guidelines.append(
                f"Move TP1 to at least {entry_price + risk * 1.5:.5g} (1.5:1 R:R)."
                if direction == "BUY" else
                f"Move TP1 to at least {entry_price - risk * 1.5:.5g} (1.5:1 R:R)."
            )
            if sl_price != 0:
                guidelines.append(
                    f"Alternatively, tighten SL to improve R:R without moving TPs. "
                    f"Current risk: {risk:.5g}."
                )
        else:
            score = 2.0
            explanation = (
                f"Unacceptable R:R — TP1 at {tp1_rr:.2f}:1, best TP at {best_rr:.2f}:1. "
                f"Risk exceeds potential reward — this setup should not be traded."
            )
            guidelines.append(
                f"Completely restructure the trade. TP1 must be at minimum "
                f"{entry_price + risk * 1.5:.5g} (1.5:1 R:R)."
                if direction == "BUY" else
                f"Completely restructure the trade. TP1 must be at minimum "
                f"{entry_price - risk * 1.5:.5g} (1.5:1 R:R)."
            )

        # Bonus for having 3+ TP levels with progressive R:R
        if len(rr_values) >= 3 and all(
            rr_values[i] < rr_values[i + 1] for i in range(len(rr_values) - 1)
        ):
            score = _clamp(score + 0.5)
            explanation += " Progressive TP ladder detected — bonus."

        score = _clamp(score)
        return ComponentRating(
            score=score,
            label=_score_label(score),
            explanation=explanation,
            guidelines=guidelines,
        )

    # ── Take Profit Rating ────────────────────────────────────

    def rate_take_profits(
        self,
        signal_type:        str,
        entry_price:        float,
        sl_price:           float,
        tp_levels:          List[float],
        nearest_resistance: float,
        nearest_support:    float,
        swing_high:         Optional[float] = None,
        swing_low:          Optional[float] = None,
        atr:                float = 0.0,
    ) -> ComponentRating:
        """
        Rate TP alignment with structural targets (1–10).

        Scoring logic:
        - BUY: TPs should align with resistance levels, swing highs, or
          liquidity clusters above entry.
        - SELL: TPs should align with support levels, swing lows, or
          liquidity clusters below entry.

        Each TP is checked for proximity to a structural level.
        The score reflects the fraction of TPs that are structurally aligned.
        """
        if atr <= 0:
            atr = abs(entry_price * 0.005)

        guidelines: List[str] = []
        direction = signal_type.upper()

        if not tp_levels:
            return ComponentRating(
                score=1.0,
                label="VERY_POOR",
                explanation="No TP levels provided.",
                guidelines=["Add at least one TP level aligned with a structural target."],
            )

        # Build list of structural targets relevant to direction
        if direction == "BUY":
            structural_targets = [nearest_resistance]
            if swing_high is not None:
                structural_targets.append(swing_high)
        else:
            structural_targets = [nearest_support]
            if swing_low is not None:
                structural_targets.append(swing_low)

        # Evaluate each TP
        aligned_count = 0
        near_count    = 0
        tp_details: List[str] = []

        for i, tp in enumerate(tp_levels, start=1):
            # Check direction validity
            if direction == "BUY" and tp <= entry_price:
                tp_details.append(f"TP{i} ({tp:.5g}) is below entry — invalid for BUY.")
                guidelines.append(f"Move TP{i} above entry price {entry_price:.5g}.")
                continue
            if direction == "SELL" and tp >= entry_price:
                tp_details.append(f"TP{i} ({tp:.5g}) is above entry — invalid for SELL.")
                guidelines.append(f"Move TP{i} below entry price {entry_price:.5g}.")
                continue

            # Find closest structural target
            best_proximity = min(
                abs(tp - target) / max(abs(tp), 1e-9)
                for target in structural_targets
            )

            if best_proximity <= TP_ALIGNED:
                aligned_count += 1
                tp_details.append(
                    f"TP{i} ({tp:.5g}) is aligned with structural target "
                    f"(within {best_proximity * 100:.1f}%)."
                )
            elif best_proximity <= TP_NEAR:
                near_count += 1
                tp_details.append(
                    f"TP{i} ({tp:.5g}) is near a structural target "
                    f"({best_proximity * 100:.1f}% away)."
                )
            elif best_proximity <= TP_FAR:
                tp_details.append(
                    f"TP{i} ({tp:.5g}) is {best_proximity * 100:.1f}% from nearest "
                    f"structural target — consider adjusting."
                )
                # Suggest nearest structural level
                closest_target = min(structural_targets, key=lambda t: abs(tp - t))
                guidelines.append(
                    f"Adjust TP{i} to {closest_target:.5g} to align with structural target."
                )
            else:
                tp_details.append(
                    f"TP{i} ({tp:.5g}) is {best_proximity * 100:.1f}% from nearest "
                    f"structural target — misaligned."
                )
                closest_target = min(structural_targets, key=lambda t: abs(tp - t))
                guidelines.append(
                    f"TP{i} is misaligned. Move to {closest_target:.5g} "
                    f"(nearest structural level)."
                )

        total_tps = len(tp_levels)
        alignment_ratio = (aligned_count + near_count * 0.5) / total_tps if total_tps > 0 else 0.0

        # Score based on alignment ratio
        if alignment_ratio >= 0.9:
            score = 9.5
            explanation = (
                f"Excellent TP alignment — {aligned_count}/{total_tps} TPs are structurally "
                f"aligned. " + " ".join(tp_details)
            )
        elif alignment_ratio >= 0.7:
            score = 8.0
            explanation = (
                f"Good TP alignment — {aligned_count}/{total_tps} TPs aligned, "
                f"{near_count} near structural levels. " + " ".join(tp_details)
            )
        elif alignment_ratio >= 0.5:
            score = 6.0
            explanation = (
                f"Partial TP alignment — {aligned_count}/{total_tps} TPs aligned. "
                + " ".join(tp_details)
            )
        elif alignment_ratio >= 0.3:
            score = 4.0
            explanation = (
                f"Poor TP alignment — most TPs are not near structural levels. "
                + " ".join(tp_details)
            )
        else:
            score = 2.0
            explanation = (
                f"Very poor TP alignment — TPs are not aligned with any structural target. "
                + " ".join(tp_details)
            )

        # Bonus: TP1 is just before (not through) a major structural level
        if tp_levels and structural_targets:
            tp1 = tp_levels[0]
            closest = min(structural_targets, key=lambda t: abs(tp1 - t))
            dist_pct = abs(tp1 - closest) / max(abs(closest), 1e-9)
            if direction == "BUY" and tp1 < closest and dist_pct <= 0.02:
                score = _clamp(score + 0.5)
                explanation += f" TP1 is placed just before resistance ({closest:.5g}) — conservative bonus."
            elif direction == "SELL" and tp1 > closest and dist_pct <= 0.02:
                score = _clamp(score + 0.5)
                explanation += f" TP1 is placed just before support ({closest:.5g}) — conservative bonus."

        score = _clamp(score)
        return ComponentRating(
            score=score,
            label=_score_label(score),
            explanation=explanation,
            guidelines=guidelines,
        )

    # ── Overall Score & Recommendation ───────────────────────

    def calculate_overall_score(
        self,
        entry_rating: ComponentRating,
        sl_rating:    ComponentRating,
        rr_rating:    ComponentRating,
        tp_rating:    ComponentRating,
    ) -> float:
        """
        Calculate the overall geometry score as the unweighted average
        of the four component ratings.

        Returns a float in [1.0, 10.0].
        """
        scores = [
            entry_rating.score,
            sl_rating.score,
            rr_rating.score,
            tp_rating.score,
        ]
        return _clamp(sum(scores) / len(scores))

    def get_recommendation(self, overall_score: float) -> Tuple[str, str]:
        """
        Translate an overall score into an approval recommendation.

        Returns:
            (recommendation, summary) where recommendation is one of:
            APPROVE | ADJUST | REJECT
        """
        if overall_score >= APPROVE_THRESHOLD:
            return (
                "APPROVE",
                f"Signal geometry score {overall_score:.2f}/10 meets the approval threshold "
                f"(≥ {APPROVE_THRESHOLD}). Geometry is structurally sound.",
            )
        elif overall_score >= ADJUST_THRESHOLD:
            return (
                "ADJUST",
                f"Signal geometry score {overall_score:.2f}/10 is below the approval threshold "
                f"(< {APPROVE_THRESHOLD}) but above the rejection floor (≥ {ADJUST_THRESHOLD}). "
                f"Adjust price levels before approving.",
            )
        else:
            return (
                "REJECT",
                f"Signal geometry score {overall_score:.2f}/10 is below the rejection floor "
                f"(< {ADJUST_THRESHOLD}). Structural geometry is too poor to trade safely.",
            )

    # ── Consolidated Rating Entry Point ──────────────────────

    def rate_signal(
        self,
        signal_type:         str,
        entry_price:         float,
        sl_price:            float,
        tp_levels:           List[float],
        current_price:       float,
        atr:                 float,
        nearest_support:     float,
        nearest_resistance:  float,
        swing_high:          Optional[float] = None,
        swing_low:           Optional[float] = None,
    ) -> GeometryRatingResult:
        """
        Compute the full geometry rating for a signal.

        Args:
            signal_type:        "BUY" or "SELL"
            entry_price:        Proposed entry price
            sl_price:           Proposed stop loss price
            tp_levels:          List of take profit levels (1–5)
            current_price:      Current market price
            atr:                Average True Range (14-period)
            nearest_support:    Nearest support level below current price
            nearest_resistance: Nearest resistance level above current price
            swing_high:         Most recent swing high (optional)
            swing_low:          Most recent swing low (optional)

        Returns:
            GeometryRatingResult with all component ratings and recommendation
        """
        try:
            entry_rating = self.rate_entry_price(
                signal_type=signal_type,
                entry_price=entry_price,
                current_price=current_price,
                nearest_support=nearest_support,
                nearest_resistance=nearest_resistance,
                atr=atr,
                swing_high=swing_high,
                swing_low=swing_low,
            )

            sl_rating = self.rate_stop_loss(
                signal_type=signal_type,
                entry_price=entry_price,
                sl_price=sl_price,
                nearest_support=nearest_support,
                nearest_resistance=nearest_resistance,
                atr=atr,
                swing_high=swing_high,
                swing_low=swing_low,
            )

            rr_rating = self.rate_risk_reward(
                entry_price=entry_price,
                sl_price=sl_price,
                tp_levels=tp_levels,
                signal_type=signal_type,
            )

            tp_rating = self.rate_take_profits(
                signal_type=signal_type,
                entry_price=entry_price,
                sl_price=sl_price,
                tp_levels=tp_levels,
                nearest_resistance=nearest_resistance,
                nearest_support=nearest_support,
                swing_high=swing_high,
                swing_low=swing_low,
                atr=atr,
            )

            overall_score = self.calculate_overall_score(
                entry_rating, sl_rating, rr_rating, tp_rating
            )
            recommendation, summary = self.get_recommendation(overall_score)

            # Consolidate all adjustment guidelines
            all_guidelines: List[str] = []
            for component_name, rating in [
                ("Entry", entry_rating),
                ("Stop Loss", sl_rating),
                ("Risk/Reward", rr_rating),
                ("Take Profits", tp_rating),
            ]:
                for guideline in rating.guidelines:
                    all_guidelines.append(f"[{component_name}] {guideline}")

            result = GeometryRatingResult(
                signal_type=signal_type.upper(),
                entry_rating=entry_rating,
                sl_rating=sl_rating,
                rr_rating=rr_rating,
                tp_rating=tp_rating,
                overall_score=overall_score,
                recommendation=recommendation,
                summary=summary,
                adjustment_guidelines=all_guidelines,
            )

            logger.info(
                f"GeometryRating [{signal_type}]: "
                f"entry={entry_rating.score:.1f} sl={sl_rating.score:.1f} "
                f"rr={rr_rating.score:.1f} tp={tp_rating.score:.1f} "
                f"overall={overall_score:.2f} → {recommendation}"
            )
            return result

        except Exception as exc:
            logger.error(f"GeometryRating error: {exc}", exc_info=True)
            # Return a safe fallback result
            fallback = ComponentRating(
                score=1.0,
                label="VERY_POOR",
                explanation=f"Rating computation failed: {exc}",
                guidelines=["Review signal data and retry."],
            )
            return GeometryRatingResult(
                signal_type=signal_type.upper(),
                entry_rating=fallback,
                sl_rating=fallback,
                rr_rating=fallback,
                tp_rating=fallback,
                overall_score=1.0,
                recommendation="REJECT",
                summary=f"Rating failed due to error: {exc}",
                adjustment_guidelines=["Review signal data and retry."],
            )


# ─────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────

geometry_rater = GeometryRating()

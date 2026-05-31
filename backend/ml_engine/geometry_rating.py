"""
Trade Geometry Rating System
Gold Trading System v3.0.2

Provides objective, quantifiable geometry scoring (1–10 scale) for every
signal component so managers can make data-driven approval decisions.

Components rated:
  1. Entry Price   — How well the entry is placed relative to structure
  2. Stop Loss     — How well the SL is placed relative to structure
  3. Risk/Reward   — Quality of the R:R ratio across TP levels
  4. Take Profits  — How well TPs align with liquidity / structure targets

Each component is scored 1–10.  The overall score is the unweighted average
of all four component scores.  A recommendation (APPROVE / ADJUST / REJECT)
is derived from the overall score and any critical failures.

Usage:
    from ml_engine.geometry_rating import GeometryRating

    rater = GeometryRating()
    result = rater.rate_signal(
        signal_type="BUY",
        entry_price=2345.50,
        sl_price=2330.00,
        tp_levels=[2365.00, 2385.00, 2410.00],
        current_price=2346.00,          # optional — live market price
        atr=12.50,                       # optional — 14-period ATR
        support_level=2332.00,           # optional — nearest structural support
        resistance_level=2368.00,        # optional — nearest structural resistance
    )
    print(result["overall_score"])       # e.g. 7.5
    print(result["recommendation"])      # e.g. "APPROVE"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Score thresholds
# ─────────────────────────────────────────────────────────────

APPROVE_THRESHOLD = 7.0   # Overall score ≥ 7.0  → APPROVE
ADJUST_THRESHOLD  = 5.0   # Overall score ≥ 5.0  → ADJUST
# Overall score < 5.0 → REJECT

# Minimum acceptable R:R for any TP level
MIN_RR_ACCEPTABLE = 1.5
# Preferred minimum R:R for TP1
PREFERRED_RR_TP1  = 2.0
# Excellent R:R for TP1
EXCELLENT_RR_TP1  = 3.0

# Maximum acceptable SL distance as a multiple of ATR
MAX_SL_ATR_MULTIPLE = 3.0
# Ideal SL distance as a multiple of ATR
IDEAL_SL_ATR_MULTIPLE = 1.5

# Entry proximity tolerance: entry within this fraction of ATR from
# the ideal zone is considered "at structure"
ENTRY_STRUCTURE_TOLERANCE = 0.25  # 25 % of ATR


# ─────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────

@dataclass
class ComponentRating:
    """Rating result for a single geometry component."""
    score: float                        # 1.0 – 10.0
    label: str                          # e.g. "EXCELLENT", "GOOD", "POOR"
    explanation: str                    # Human-readable reason for the score
    adjustments: List[str] = field(default_factory=list)  # Suggested fixes
    details: Dict[str, Any] = field(default_factory=dict) # Raw metrics


@dataclass
class GeometryRatingResult:
    """Full geometry rating result for a signal."""
    signal_type: str
    entry_price: float
    sl_price: float
    tp_levels: List[float]

    entry_rating:   ComponentRating = field(default=None)   # type: ignore[assignment]
    sl_rating:      ComponentRating = field(default=None)   # type: ignore[assignment]
    rr_rating:      ComponentRating = field(default=None)   # type: ignore[assignment]
    tp_rating:      ComponentRating = field(default=None)   # type: ignore[assignment]

    overall_score:    float = 0.0
    overall_label:    str   = ""
    recommendation:   str   = ""          # APPROVE | ADJUST | REJECT
    critical_issues:  List[str] = field(default_factory=list)
    summary:          str   = ""
    rated_at:         str   = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict suitable for JSON responses."""
        def _cr(cr: ComponentRating) -> Dict[str, Any]:
            return {
                "score":       round(cr.score, 1),
                "label":       cr.label,
                "explanation": cr.explanation,
                "adjustments": cr.adjustments,
                "details":     cr.details,
            }

        return {
            "signal_type":  self.signal_type,
            "entry_price":  self.entry_price,
            "sl_price":     self.sl_price,
            "tp_levels":    self.tp_levels,
            "ratings": {
                "entry":       _cr(self.entry_rating),
                "stop_loss":   _cr(self.sl_rating),
                "risk_reward": _cr(self.rr_rating),
                "take_profits": _cr(self.tp_rating),
            },
            "overall_score":   round(self.overall_score, 1),
            "overall_label":   self.overall_label,
            "recommendation":  self.recommendation,
            "critical_issues": self.critical_issues,
            "summary":         self.summary,
            "rated_at":        self.rated_at,
        }


# ─────────────────────────────────────────────────────────────
# Score → label mapping
# ─────────────────────────────────────────────────────────────

def _score_label(score: float) -> str:
    if score >= 9.0:
        return "EXCELLENT"
    if score >= 7.0:
        return "GOOD"
    if score >= 5.0:
        return "ACCEPTABLE"
    if score >= 3.0:
        return "POOR"
    return "CRITICAL"


def _clamp(value: float, lo: float = 1.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, value))


# ─────────────────────────────────────────────────────────────
# GeometryRating
# ─────────────────────────────────────────────────────────────

class GeometryRating:
    """
    Objective geometry scoring engine for Gold trading signals.

    All public ``rate_*`` methods accept the raw signal price levels plus
    optional market-context parameters (ATR, support/resistance).  When
    context is omitted the engine falls back to price-ratio heuristics
    that still produce meaningful scores.

    The main entry point is ``rate_signal()`` which calls all four
    component raters and assembles the final ``GeometryRatingResult``.
    """

    # ── Public API ────────────────────────────────────────────

    def rate_signal(
        self,
        signal_type: str,
        entry_price: float,
        sl_price: float,
        tp_levels: List[float],
        *,
        current_price: Optional[float] = None,
        atr: Optional[float] = None,
        support_level: Optional[float] = None,
        resistance_level: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Rate all four geometry components and return the full result dict.

        Args:
            signal_type:       "BUY" or "SELL" (case-insensitive).
            entry_price:       Proposed entry price.
            sl_price:          Proposed stop-loss price.
            tp_levels:         List of take-profit prices (1–5 levels).
            current_price:     Current market price (optional).
            atr:               14-period ATR (optional, improves SL rating).
            support_level:     Nearest structural support (optional).
            resistance_level:  Nearest structural resistance (optional).

        Returns:
            Serialised ``GeometryRatingResult`` dict.
        """
        direction = signal_type.upper()
        if direction not in ("BUY", "SELL"):
            raise ValueError(f"signal_type must be BUY or SELL, got '{signal_type}'")
        if not tp_levels:
            raise ValueError("tp_levels must contain at least one value")

        # Derive risk distance (always positive)
        risk = abs(entry_price - sl_price)

        # ── Rate each component ───────────────────────────────
        entry_rating = self.rate_entry_price(
            direction, entry_price, sl_price, tp_levels,
            current_price=current_price,
            atr=atr,
            support_level=support_level,
            resistance_level=resistance_level,
        )
        sl_rating = self.rate_stop_loss(
            direction, entry_price, sl_price,
            atr=atr,
            support_level=support_level,
            resistance_level=resistance_level,
        )
        rr_rating = self.rate_risk_reward(
            direction, entry_price, sl_price, tp_levels,
        )
        tp_rating = self.rate_take_profits(
            direction, entry_price, sl_price, tp_levels,
            resistance_level=resistance_level,
            support_level=support_level,
            atr=atr,
        )

        # ── Overall score ─────────────────────────────────────
        overall_score = self.calculate_overall_score(
            entry_rating.score,
            sl_rating.score,
            rr_rating.score,
            tp_rating.score,
        )

        # ── Critical issues ───────────────────────────────────
        critical_issues: List[str] = []
        if entry_rating.score < 3.0:
            critical_issues.append("Entry placement is critically poor")
        if sl_rating.score < 3.0:
            critical_issues.append("Stop-loss placement is critically poor")
        if rr_rating.score < 3.0:
            critical_issues.append("Risk/reward ratio is critically poor")
        if tp_rating.score < 3.0:
            critical_issues.append("Take-profit alignment is critically poor")

        # ── Recommendation ────────────────────────────────────
        recommendation = self.get_recommendation(overall_score, critical_issues)

        # ── Summary ───────────────────────────────────────────
        summary = self._build_summary(
            direction, overall_score, recommendation,
            entry_rating, sl_rating, rr_rating, tp_rating,
            risk, tp_levels,
        )

        result = GeometryRatingResult(
            signal_type=direction,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_levels=tp_levels,
            entry_rating=entry_rating,
            sl_rating=sl_rating,
            rr_rating=rr_rating,
            tp_rating=tp_rating,
            overall_score=overall_score,
            overall_label=_score_label(overall_score),
            recommendation=recommendation,
            critical_issues=critical_issues,
            summary=summary,
            rated_at=datetime.utcnow().isoformat(),
        )

        logger.info(
            "GeometryRating [%s] entry=%.2f sl=%.2f tp=%s → "
            "score=%.1f (%s) recommendation=%s",
            direction, entry_price, sl_price,
            [round(t, 2) for t in tp_levels],
            overall_score, _score_label(overall_score), recommendation,
        )

        return result.to_dict()

    # ── Component raters ──────────────────────────────────────

    def rate_entry_price(
        self,
        signal_type: str,
        entry_price: float,
        sl_price: float,
        tp_levels: List[float],
        *,
        current_price: Optional[float] = None,
        atr: Optional[float] = None,
        support_level: Optional[float] = None,
        resistance_level: Optional[float] = None,
    ) -> ComponentRating:
        """
        Rate entry price placement on a 1–10 scale.

        Scoring criteria:
          10 — Entry is exactly at a key structural level (support for BUY,
               resistance for SELL) with price confirming the zone.
           8 — Entry is within ¼ ATR of the structural level.
           6 — Entry is within ½ ATR of the structural level.
           4 — Entry is between ½ and 1 ATR from the structural level.
           2 — Entry is more than 1 ATR away from any structural level.
           1 — Entry is on the wrong side of structure (chasing price).

        When ATR / structure levels are not provided the score is derived
        from the entry's position relative to the SL and TP1 distances.
        """
        direction = signal_type.upper()
        risk = abs(entry_price - sl_price)
        tp1  = tp_levels[0]
        reward_tp1 = abs(tp1 - entry_price)

        details: Dict[str, Any] = {
            "direction":        direction,
            "entry_price":      entry_price,
            "sl_price":         sl_price,
            "tp1":              tp1,
            "risk_distance":    round(risk, 5),
            "reward_tp1":       round(reward_tp1, 5),
        }
        adjustments: List[str] = []

        # ── Structural context scoring ────────────────────────
        if atr and atr > 0 and (support_level or resistance_level):
            score, explanation = self._rate_entry_with_structure(
                direction, entry_price, atr,
                support_level, resistance_level,
                details, adjustments,
            )
        else:
            # Fallback: ratio-based scoring
            score, explanation = self._rate_entry_ratio_based(
                direction, entry_price, sl_price, tp1, risk, reward_tp1,
                current_price, details, adjustments,
            )

        score = _clamp(score)
        return ComponentRating(
            score=score,
            label=_score_label(score),
            explanation=explanation,
            adjustments=adjustments,
            details=details,
        )

    def rate_stop_loss(
        self,
        signal_type: str,
        entry_price: float,
        sl_price: float,
        *,
        atr: Optional[float] = None,
        support_level: Optional[float] = None,
        resistance_level: Optional[float] = None,
    ) -> ComponentRating:
        """
        Rate stop-loss placement on a 1–10 scale.

        Scoring criteria:
          10 — SL is placed just beyond a key structural level (below support
               for BUY, above resistance for SELL) within 1.0–1.5× ATR.
           8 — SL is within 1.5–2.0× ATR and beyond a structural level.
           6 — SL is within 2.0–2.5× ATR or lacks structural alignment.
           4 — SL is within 2.5–3.0× ATR (wide but not catastrophic).
           2 — SL is > 3× ATR (dangerously wide, destroys R:R).
           1 — SL is on the wrong side of entry (invalid geometry).
        """
        direction = signal_type.upper()
        risk = abs(entry_price - sl_price)

        details: Dict[str, Any] = {
            "direction":     direction,
            "entry_price":   entry_price,
            "sl_price":      sl_price,
            "risk_distance": round(risk, 5),
        }
        adjustments: List[str] = []

        # Validate direction
        if direction == "BUY" and sl_price >= entry_price:
            return ComponentRating(
                score=1.0,
                label="CRITICAL",
                explanation="BUY stop-loss must be below entry price — geometry is invalid.",
                adjustments=["Move SL below entry price immediately."],
                details=details,
            )
        if direction == "SELL" and sl_price <= entry_price:
            return ComponentRating(
                score=1.0,
                label="CRITICAL",
                explanation="SELL stop-loss must be above entry price — geometry is invalid.",
                adjustments=["Move SL above entry price immediately."],
                details=details,
            )

        if atr and atr > 0:
            score, explanation = self._rate_sl_with_atr(
                direction, entry_price, sl_price, risk, atr,
                support_level, resistance_level,
                details, adjustments,
            )
        else:
            score, explanation = self._rate_sl_ratio_based(
                direction, entry_price, sl_price, risk,
                support_level, resistance_level,
                details, adjustments,
            )

        score = _clamp(score)
        return ComponentRating(
            score=score,
            label=_score_label(score),
            explanation=explanation,
            adjustments=adjustments,
            details=details,
        )

    def rate_risk_reward(
        self,
        signal_type: str,
        entry_price: float,
        sl_price: float,
        tp_levels: List[float],
    ) -> ComponentRating:
        """
        Rate the risk/reward ratio on a 1–10 scale.

        Scoring is based on the R:R of TP1 (the primary target) with bonus
        points for additional TP levels that extend the reward profile.

        Scale:
          10 — TP1 R:R ≥ 4.0  (exceptional)
           9 — TP1 R:R ≥ 3.5
           8 — TP1 R:R ≥ 3.0
           7 — TP1 R:R ≥ 2.5
           6 — TP1 R:R ≥ 2.0  (minimum recommended)
           5 — TP1 R:R ≥ 1.75
           4 — TP1 R:R ≥ 1.5
           3 — TP1 R:R ≥ 1.25
           2 — TP1 R:R ≥ 1.0
           1 — TP1 R:R < 1.0  (reward less than risk — unacceptable)
        """
        direction = signal_type.upper()
        risk = abs(entry_price - sl_price)

        if risk <= 0:
            return ComponentRating(
                score=1.0,
                label="CRITICAL",
                explanation="Risk distance is zero — entry and SL are at the same price.",
                adjustments=["Separate entry and SL prices to create a valid risk distance."],
                details={"risk": 0},
            )

        rr_ratios: List[float] = []
        for tp in tp_levels:
            reward = abs(tp - entry_price)
            rr_ratios.append(round(reward / risk, 2))

        tp1_rr = rr_ratios[0]
        avg_rr = round(sum(rr_ratios) / len(rr_ratios), 2)

        details: Dict[str, Any] = {
            "direction":  direction,
            "risk":       round(risk, 5),
            "rr_per_tp":  rr_ratios,
            "tp1_rr":     tp1_rr,
            "avg_rr":     avg_rr,
        }
        adjustments: List[str] = []

        # Score based on TP1 R:R
        if tp1_rr >= 4.0:
            score = 10.0
            explanation = (
                f"Exceptional R:R of {tp1_rr:.1f}:1 on TP1. "
                "The reward far outweighs the risk — ideal geometry."
            )
        elif tp1_rr >= 3.5:
            score = 9.0
            explanation = (
                f"Excellent R:R of {tp1_rr:.1f}:1 on TP1. "
                "Strong reward profile with comfortable margin."
            )
        elif tp1_rr >= 3.0:
            score = 8.0
            explanation = (
                f"Very good R:R of {tp1_rr:.1f}:1 on TP1. "
                "Solid reward profile that justifies the risk."
            )
        elif tp1_rr >= 2.5:
            score = 7.0
            explanation = (
                f"Good R:R of {tp1_rr:.1f}:1 on TP1. "
                "Acceptable reward profile for live trading."
            )
        elif tp1_rr >= 2.0:
            score = 6.0
            explanation = (
                f"Acceptable R:R of {tp1_rr:.1f}:1 on TP1 — at the recommended minimum. "
                "Consider whether TP1 can be extended."
            )
            adjustments.append(
                f"TP1 R:R is at the minimum threshold ({tp1_rr:.1f}:1). "
                "Try to extend TP1 to achieve ≥ 2.5:1."
            )
        elif tp1_rr >= 1.75:
            score = 5.0
            explanation = (
                f"Below-average R:R of {tp1_rr:.1f}:1 on TP1. "
                "Marginal reward profile — adjustment recommended."
            )
            adjustments.append(
                f"TP1 R:R of {tp1_rr:.1f}:1 is below the 2.0:1 minimum. "
                "Move TP1 further from entry or tighten the SL."
            )
        elif tp1_rr >= 1.5:
            score = 4.0
            explanation = (
                f"Poor R:R of {tp1_rr:.1f}:1 on TP1. "
                "Reward barely justifies the risk — significant adjustment needed."
            )
            adjustments.append(
                f"TP1 R:R of {tp1_rr:.1f}:1 is well below the 2.0:1 minimum. "
                "Either tighten the SL or extend TP1 significantly."
            )
        elif tp1_rr >= 1.25:
            score = 3.0
            explanation = (
                f"Very poor R:R of {tp1_rr:.1f}:1 on TP1. "
                "This trade risks more than it stands to gain — not recommended."
            )
            adjustments.append(
                "R:R is critically low. Reconsider the entire trade structure."
            )
        elif tp1_rr >= 1.0:
            score = 2.0
            explanation = (
                f"Unacceptable R:R of {tp1_rr:.1f}:1 on TP1. "
                "Risk equals reward — no statistical edge."
            )
            adjustments.append(
                "R:R of 1:1 provides no edge. Reject or completely restructure."
            )
        else:
            score = 1.0
            explanation = (
                f"Critical R:R failure: {tp1_rr:.1f}:1 on TP1. "
                "Risk exceeds reward — this trade should be rejected."
            )
            adjustments.append(
                "R:R below 1:1 is never acceptable. Reject this signal."
            )

        # Bonus for multiple TP levels with good R:R
        if len(rr_ratios) >= 3 and all(r >= PREFERRED_RR_TP1 for r in rr_ratios):
            score = min(10.0, score + 0.5)
            explanation += " All TP levels achieve ≥ 2.0:1 R:R — excellent reward profile."

        return ComponentRating(
            score=_clamp(score),
            label=_score_label(score),
            explanation=explanation,
            adjustments=adjustments,
            details=details,
        )

    def rate_take_profits(
        self,
        signal_type: str,
        entry_price: float,
        sl_price: float,
        tp_levels: List[float],
        *,
        resistance_level: Optional[float] = None,
        support_level: Optional[float] = None,
        atr: Optional[float] = None,
    ) -> ComponentRating:
        """
        Rate take-profit alignment on a 1–10 scale.

        Scoring criteria:
          10 — All TPs align with key structural levels (resistance for BUY,
               support for SELL) and are evenly spaced.
           8 — Most TPs align with structure; spacing is logical.
           6 — TPs are directionally correct but lack structural alignment.
           4 — TPs are too close together or too close to entry.
           2 — TPs are on the wrong side of entry or overlap with SL.
           1 — TP geometry is invalid (TP below entry for BUY, etc.).
        """
        direction = signal_type.upper()
        risk = abs(entry_price - sl_price)

        details: Dict[str, Any] = {
            "direction":  direction,
            "entry_price": entry_price,
            "sl_price":   sl_price,
            "tp_levels":  tp_levels,
            "risk":       round(risk, 5),
        }
        adjustments: List[str] = []

        # ── Validate TP direction ─────────────────────────────
        invalid_tps = []
        for i, tp in enumerate(tp_levels):
            if direction == "BUY" and tp <= entry_price:
                invalid_tps.append(i)
            elif direction == "SELL" and tp >= entry_price:
                invalid_tps.append(i)

        if invalid_tps:
            return ComponentRating(
                score=1.0,
                label="CRITICAL",
                explanation=(
                    f"TP levels {invalid_tps} are on the wrong side of entry "
                    f"for a {direction} trade — geometry is invalid."
                ),
                adjustments=[
                    f"Move TP{i+1} to the correct side of entry for a {direction} trade."
                    for i in invalid_tps
                ],
                details=details,
            )

        # ── Validate TP ordering ──────────────────────────────
        for i in range(1, len(tp_levels)):
            if direction == "BUY" and tp_levels[i] < tp_levels[i - 1]:
                adjustments.append(
                    f"TP{i+1} ({tp_levels[i]}) is below TP{i} ({tp_levels[i-1]}) "
                    "for a BUY — TPs must be ascending."
                )
            elif direction == "SELL" and tp_levels[i] > tp_levels[i - 1]:
                adjustments.append(
                    f"TP{i+1} ({tp_levels[i]}) is above TP{i} ({tp_levels[i-1]}) "
                    "for a SELL — TPs must be descending."
                )

        # ── Score TP spacing and structural alignment ─────────
        score, explanation = self._rate_tp_alignment(
            direction, entry_price, sl_price, tp_levels, risk,
            resistance_level, support_level, atr,
            details, adjustments,
        )

        score = _clamp(score)
        return ComponentRating(
            score=score,
            label=_score_label(score),
            explanation=explanation,
            adjustments=adjustments,
            details=details,
        )

    def calculate_overall_score(
        self,
        entry_score: float,
        sl_score: float,
        rr_score: float,
        tp_score: float,
    ) -> float:
        """
        Calculate the overall geometry score as the unweighted average of
        the four component scores, rounded to one decimal place.

        Args:
            entry_score: Entry price rating (1–10).
            sl_score:    Stop-loss rating (1–10).
            rr_score:    Risk/reward rating (1–10).
            tp_score:    Take-profit rating (1–10).

        Returns:
            Overall score (1.0–10.0).
        """
        raw = (entry_score + sl_score + rr_score + tp_score) / 4.0
        return round(_clamp(raw), 1)

    def get_recommendation(
        self,
        overall_score: float,
        critical_issues: Optional[List[str]] = None,
    ) -> str:
        """
        Derive an approval recommendation from the overall score and any
        critical issues.

        Rules:
          APPROVE — overall_score ≥ 7.0 AND no critical issues.
          ADJUST  — overall_score ≥ 5.0 OR has critical issues that are fixable.
          REJECT  — overall_score < 5.0 OR has multiple critical issues.

        Args:
            overall_score:   Calculated overall geometry score (1–10).
            critical_issues: List of critical issue descriptions (may be empty).

        Returns:
            "APPROVE", "ADJUST", or "REJECT".
        """
        issues = critical_issues or []
        n_critical = len(issues)

        if n_critical >= 2:
            return "REJECT"
        if n_critical == 1:
            return "ADJUST"
        if overall_score >= APPROVE_THRESHOLD:
            return "APPROVE"
        if overall_score >= ADJUST_THRESHOLD:
            return "ADJUST"
        return "REJECT"

    # ── Private helpers ───────────────────────────────────────

    def _rate_entry_with_structure(
        self,
        direction: str,
        entry_price: float,
        atr: float,
        support_level: Optional[float],
        resistance_level: Optional[float],
        details: Dict[str, Any],
        adjustments: List[str],
    ) -> Tuple[float, str]:
        """Score entry placement when structural levels and ATR are available."""
        tolerance = atr * ENTRY_STRUCTURE_TOLERANCE

        if direction == "BUY":
            ideal_level = support_level
            label = "support"
        else:
            ideal_level = resistance_level
            label = "resistance"

        if ideal_level is None:
            # Fall back to half-ATR heuristic
            details["structural_context"] = "no_level_provided"
            return 5.0, (
                "No structural level provided for context. "
                "Score defaulted to 5 — verify entry against chart structure."
            )

        distance = abs(entry_price - ideal_level)
        details["structural_level"] = ideal_level
        details["distance_from_structure"] = round(distance, 5)
        details["atr"] = round(atr, 5)
        details["tolerance"] = round(tolerance, 5)

        if distance <= tolerance:
            adjustments_needed = []
            return 10.0, (
                f"Entry is within {distance:.2f} of the {label} level at {ideal_level:.2f} "
                f"(tolerance: {tolerance:.2f}). Ideal structural placement."
            )
        elif distance <= atr * 0.5:
            return 8.0, (
                f"Entry is {distance:.2f} from the {label} level at {ideal_level:.2f} "
                f"— within ½ ATR. Good structural proximity."
            )
        elif distance <= atr * 1.0:
            adjustments.append(
                f"Entry is {distance:.2f} from the {label} level. "
                f"Consider moving entry closer to {ideal_level:.2f}."
            )
            return 6.0, (
                f"Entry is {distance:.2f} from the {label} level at {ideal_level:.2f} "
                f"— within 1 ATR. Acceptable but not ideal."
            )
        elif distance <= atr * 2.0:
            adjustments.append(
                f"Entry is {distance:.2f} from the {label} level — more than 1 ATR away. "
                f"Move entry closer to {ideal_level:.2f} for better geometry."
            )
            return 4.0, (
                f"Entry is {distance:.2f} from the {label} level at {ideal_level:.2f} "
                f"— between 1–2 ATR. Poor structural placement."
            )
        else:
            adjustments.append(
                f"Entry is {distance:.2f} from the {label} level — more than 2 ATR away. "
                "This entry is chasing price. Reject or wait for a pullback."
            )
            return 2.0, (
                f"Entry is {distance:.2f} from the {label} level at {ideal_level:.2f} "
                f"— more than 2 ATR away. Entry is chasing price."
            )

    def _rate_entry_ratio_based(
        self,
        direction: str,
        entry_price: float,
        sl_price: float,
        tp1: float,
        risk: float,
        reward_tp1: float,
        current_price: Optional[float],
        details: Dict[str, Any],
        adjustments: List[str],
    ) -> Tuple[float, str]:
        """Score entry placement using price-ratio heuristics (no ATR/structure)."""
        details["structural_context"] = "ratio_based"

        # If current price is available, check how far entry is from market
        if current_price and current_price > 0:
            entry_vs_market = abs(entry_price - current_price)
            market_pct = entry_vs_market / current_price * 100
            details["entry_vs_market_pct"] = round(market_pct, 3)

            if market_pct > 1.0:
                adjustments.append(
                    f"Entry is {market_pct:.2f}% away from current market price "
                    f"({current_price:.2f}). Verify this is a limit order, not a market order."
                )

        # Score based on reward-to-risk ratio at TP1
        if risk <= 0:
            return 1.0, "Risk distance is zero — entry and SL are at the same price."

        rr = reward_tp1 / risk
        if rr >= 3.0:
            return 9.0, (
                f"Entry creates a strong TP1 R:R of {rr:.1f}:1 — "
                "excellent entry placement relative to targets."
            )
        elif rr >= 2.0:
            return 7.5, (
                f"Entry creates a good TP1 R:R of {rr:.1f}:1 — "
                "solid entry placement."
            )
        elif rr >= 1.5:
            return 6.0, (
                f"Entry creates an acceptable TP1 R:R of {rr:.1f}:1 — "
                "entry placement is marginal."
            )
            adjustments.append("Consider tightening SL or extending TP1 to improve entry geometry.")
        else:
            adjustments.append(
                f"Entry creates a poor TP1 R:R of {rr:.1f}:1. "
                "Reconsider entry placement or price levels."
            )
            return 3.0, (
                f"Entry creates a poor TP1 R:R of {rr:.1f}:1 — "
                "entry placement is unfavourable."
            )

    def _rate_sl_with_atr(
        self,
        direction: str,
        entry_price: float,
        sl_price: float,
        risk: float,
        atr: float,
        support_level: Optional[float],
        resistance_level: Optional[float],
        details: Dict[str, Any],
        adjustments: List[str],
    ) -> Tuple[float, str]:
        """Score SL placement when ATR is available."""
        atr_multiple = risk / atr
        details["atr"] = round(atr, 5)
        details["atr_multiple"] = round(atr_multiple, 2)

        # Check structural alignment
        structural_bonus = 0.0
        if direction == "BUY" and support_level:
            # SL should be just below support
            if sl_price < support_level:
                structural_bonus = 1.0
                details["structural_alignment"] = "SL is below support — correct"
            else:
                adjustments.append(
                    f"SL ({sl_price:.2f}) is above support ({support_level:.2f}). "
                    "Move SL below the support level for structural protection."
                )
                details["structural_alignment"] = "SL is above support — incorrect"
        elif direction == "SELL" and resistance_level:
            # SL should be just above resistance
            if sl_price > resistance_level:
                structural_bonus = 1.0
                details["structural_alignment"] = "SL is above resistance — correct"
            else:
                adjustments.append(
                    f"SL ({sl_price:.2f}) is below resistance ({resistance_level:.2f}). "
                    "Move SL above the resistance level for structural protection."
                )
                details["structural_alignment"] = "SL is below resistance — incorrect"

        # Score based on ATR multiple
        if atr_multiple <= 1.0:
            base_score = 9.0
            explanation = (
                f"SL is {atr_multiple:.1f}× ATR from entry — tight and precise. "
                "Excellent stop placement."
            )
        elif atr_multiple <= IDEAL_SL_ATR_MULTIPLE:
            base_score = 8.0
            explanation = (
                f"SL is {atr_multiple:.1f}× ATR from entry — within the ideal range. "
                "Good stop placement."
            )
        elif atr_multiple <= 2.0:
            base_score = 6.5
            explanation = (
                f"SL is {atr_multiple:.1f}× ATR from entry — slightly wide. "
                "Acceptable but consider tightening."
            )
            adjustments.append(
                f"SL distance of {atr_multiple:.1f}× ATR is wider than ideal (1.5×). "
                "Tighten SL to improve R:R."
            )
        elif atr_multiple <= MAX_SL_ATR_MULTIPLE:
            base_score = 4.0
            explanation = (
                f"SL is {atr_multiple:.1f}× ATR from entry — wide stop. "
                "R:R is being compressed significantly."
            )
            adjustments.append(
                f"SL distance of {atr_multiple:.1f}× ATR is too wide. "
                "Tighten SL to ≤ 1.5× ATR or reconsider the trade."
            )
        else:
            base_score = 2.0
            explanation = (
                f"SL is {atr_multiple:.1f}× ATR from entry — dangerously wide. "
                "This stop destroys the R:R ratio."
            )
            adjustments.append(
                f"SL distance of {atr_multiple:.1f}× ATR is unacceptably wide (max {MAX_SL_ATR_MULTIPLE}×). "
                "Reject or significantly tighten the SL."
            )

        return min(10.0, base_score + structural_bonus), explanation

    def _rate_sl_ratio_based(
        self,
        direction: str,
        entry_price: float,
        sl_price: float,
        risk: float,
        support_level: Optional[float],
        resistance_level: Optional[float],
        details: Dict[str, Any],
        adjustments: List[str],
    ) -> Tuple[float, str]:
        """Score SL placement using price-ratio heuristics (no ATR)."""
        details["structural_context"] = "ratio_based"

        # Express SL as % of entry price
        sl_pct = risk / entry_price * 100
        details["sl_pct_of_entry"] = round(sl_pct, 3)

        # For Gold (XAUUSD) typical ATR is ~0.5–1.0% of price
        # Use 0.75% as a proxy for 1 ATR
        proxy_atr_pct = 0.75
        atr_multiple_proxy = sl_pct / proxy_atr_pct
        details["atr_multiple_proxy"] = round(atr_multiple_proxy, 2)

        structural_note = ""
        if direction == "BUY" and support_level:
            if sl_price < support_level:
                structural_note = " SL is correctly placed below support."
            else:
                adjustments.append(
                    f"SL ({sl_price:.2f}) should be below support ({support_level:.2f})."
                )
                structural_note = " SL is above support — incorrect placement."
        elif direction == "SELL" and resistance_level:
            if sl_price > resistance_level:
                structural_note = " SL is correctly placed above resistance."
            else:
                adjustments.append(
                    f"SL ({sl_price:.2f}) should be above resistance ({resistance_level:.2f})."
                )
                structural_note = " SL is below resistance — incorrect placement."

        if atr_multiple_proxy <= 1.0:
            return 8.5, f"SL distance of {sl_pct:.2f}% is tight and precise.{structural_note}"
        elif atr_multiple_proxy <= 1.5:
            return 7.5, f"SL distance of {sl_pct:.2f}% is within the ideal range.{structural_note}"
        elif atr_multiple_proxy <= 2.0:
            adjustments.append(f"SL distance of {sl_pct:.2f}% is slightly wide. Consider tightening.")
            return 6.0, f"SL distance of {sl_pct:.2f}% is acceptable but slightly wide.{structural_note}"
        elif atr_multiple_proxy <= 3.0:
            adjustments.append(f"SL distance of {sl_pct:.2f}% is wide. Tighten to improve R:R.")
            return 4.0, f"SL distance of {sl_pct:.2f}% is wide — R:R is being compressed.{structural_note}"
        else:
            adjustments.append(f"SL distance of {sl_pct:.2f}% is dangerously wide. Reject or restructure.")
            return 2.0, f"SL distance of {sl_pct:.2f}% is unacceptably wide.{structural_note}"

    def _rate_tp_alignment(
        self,
        direction: str,
        entry_price: float,
        sl_price: float,
        tp_levels: List[float],
        risk: float,
        resistance_level: Optional[float],
        support_level: Optional[float],
        atr: Optional[float],
        details: Dict[str, Any],
        adjustments: List[str],
    ) -> Tuple[float, str]:
        """Score TP alignment with structure and spacing quality."""
        n_tps = len(tp_levels)
        score = 5.0  # Baseline

        # ── Structural alignment bonus ────────────────────────
        structural_target = resistance_level if direction == "BUY" else support_level
        structural_label  = "resistance" if direction == "BUY" else "support"
        structural_bonus  = 0.0

        if structural_target and atr and atr > 0:
            # Check if TP1 is near or beyond the structural target
            tp1 = tp_levels[0]
            dist_to_structure = abs(tp1 - structural_target)
            details["structural_target"] = structural_target
            details["tp1_distance_from_structure"] = round(dist_to_structure, 5)

            if direction == "BUY":
                if tp1 >= structural_target * 0.995:
                    structural_bonus = 2.0
                    details["tp1_structural_alignment"] = "TP1 at or beyond resistance — excellent"
                elif dist_to_structure <= atr:
                    structural_bonus = 1.0
                    details["tp1_structural_alignment"] = "TP1 within 1 ATR of resistance — good"
                else:
                    adjustments.append(
                        f"TP1 ({tp1:.2f}) is {dist_to_structure:.2f} below resistance "
                        f"({structural_target:.2f}). Consider extending TP1 to the resistance level."
                    )
                    details["tp1_structural_alignment"] = "TP1 below resistance — suboptimal"
            else:  # SELL
                if tp1 <= structural_target * 1.005:
                    structural_bonus = 2.0
                    details["tp1_structural_alignment"] = "TP1 at or beyond support — excellent"
                elif dist_to_structure <= atr:
                    structural_bonus = 1.0
                    details["tp1_structural_alignment"] = "TP1 within 1 ATR of support — good"
                else:
                    adjustments.append(
                        f"TP1 ({tp1:.2f}) is {dist_to_structure:.2f} above support "
                        f"({structural_target:.2f}). Consider extending TP1 to the support level."
                    )
                    details["tp1_structural_alignment"] = "TP1 above support — suboptimal"

        score += structural_bonus

        # ── Spacing quality ───────────────────────────────────
        spacing_score = 0.0
        if n_tps >= 2:
            gaps = []
            for i in range(1, n_tps):
                gaps.append(abs(tp_levels[i] - tp_levels[i - 1]))

            min_gap = min(gaps)
            details["tp_gaps"] = [round(g, 5) for g in gaps]

            # TPs should be spaced at least 0.5× risk apart
            if min_gap >= risk * 1.0:
                spacing_score = 2.0
                details["tp_spacing"] = "well_spaced"
            elif min_gap >= risk * 0.5:
                spacing_score = 1.0
                details["tp_spacing"] = "adequately_spaced"
            else:
                adjustments.append(
                    f"TP levels are too close together (min gap: {min_gap:.2f}, "
                    f"risk: {risk:.2f}). Space TPs at least 0.5× risk apart."
                )
                details["tp_spacing"] = "too_close"
        else:
            # Single TP — no spacing to evaluate, small bonus for simplicity
            spacing_score = 1.0
            details["tp_spacing"] = "single_tp"

        score += spacing_score

        # ── Multiple TP bonus ─────────────────────────────────
        if n_tps >= 3:
            score += 1.0
            details["multiple_tp_bonus"] = True
        elif n_tps == 2:
            score += 0.5
            details["multiple_tp_bonus"] = False

        # ── Build explanation ─────────────────────────────────
        explanation_parts = [
            f"{n_tps} TP level{'s' if n_tps > 1 else ''} defined."
        ]
        if structural_bonus >= 2.0:
            explanation_parts.append(
                f"TP1 aligns with the {structural_label} level — excellent structural targeting."
            )
        elif structural_bonus >= 1.0:
            explanation_parts.append(
                f"TP1 is near the {structural_label} level — good structural proximity."
            )
        elif structural_target:
            explanation_parts.append(
                f"TP1 does not align with the {structural_label} level at {structural_target:.2f}."
            )

        if n_tps >= 2:
            if spacing_score >= 2.0:
                explanation_parts.append("TP levels are well-spaced relative to risk.")
            elif spacing_score >= 1.0:
                explanation_parts.append("TP spacing is adequate.")
            else:
                explanation_parts.append("TP levels are too tightly clustered.")

        explanation = " ".join(explanation_parts)
        return score, explanation

    def _build_summary(
        self,
        direction: str,
        overall_score: float,
        recommendation: str,
        entry_rating: ComponentRating,
        sl_rating: ComponentRating,
        rr_rating: ComponentRating,
        tp_rating: ComponentRating,
        risk: float,
        tp_levels: List[float],
    ) -> str:
        """Build a concise human-readable summary for the manager."""
        rr_tp1 = abs(tp_levels[0] - (entry_rating.details.get("entry_price", 0))) / risk if risk > 0 else 0
        # Recalculate from details to be safe
        rr_tp1 = rr_rating.details.get("tp1_rr", 0)

        lines = [
            f"Overall Geometry Score: {overall_score}/10 ({_score_label(overall_score)}) "
            f"— Recommendation: {recommendation}",
            "",
            f"  Entry  : {entry_rating.score}/10 ({entry_rating.label}) — {entry_rating.explanation}",
            f"  SL     : {sl_rating.score}/10 ({sl_rating.label}) — {sl_rating.explanation}",
            f"  R:R    : {rr_rating.score}/10 ({rr_rating.label}) — TP1 R:R {rr_tp1:.1f}:1",
            f"  TPs    : {tp_rating.score}/10 ({tp_rating.label}) — {tp_rating.explanation}",
        ]

        all_adjustments = (
            entry_rating.adjustments
            + sl_rating.adjustments
            + rr_rating.adjustments
            + tp_rating.adjustments
        )
        if all_adjustments:
            lines.append("")
            lines.append("Adjustments needed:")
            for adj in all_adjustments:
                lines.append(f"  • {adj}")

        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────

geometry_rater = GeometryRating()

"""
tp_calculator.py — ATR-based TP level calculator for Forex pairs.

Strategy
--------
Each pair is classified by its DNA (USD_LED, USD_FOLLOW, CROSS, GOLD).
ATR is calculated on H4 candles (14-period) to capture the pair's true
intraday volatility range.  Pair-type multipliers are applied to that
ATR to produce TP1/TP2/TP3 distances.

A hard cap of MAX_PIP_DISTANCE (5 pips) is enforced between consecutive
TP levels.  If the raw ATR-derived gaps exceed this cap, all multipliers
are scaled down proportionally so the ratio is preserved while the
absolute distance stays within the limit.

JPY pairs receive an additional 30 % scale-down because their ATR is
expressed in a different price unit (0.01 pip vs 0.0001 pip).
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import numpy as np

from pair_profiles import PairProfile, PairType, get_pair_profile

logger = logging.getLogger(__name__)


class TPCalculator:
    """ATR-based TP level calculator for Forex (and Gold) pairs."""

    # ── Pair-type multipliers (ATR × multiplier = price distance) ────────────
    MULTIPLIERS: dict[PairType, dict[str, float]] = {
        PairType.USD_LED: {
            "tp1": 0.8,
            "tp2": 1.3,
            "tp3": 1.8,
        },
        PairType.USD_FOLLOW: {
            "tp1": 0.7,
            "tp2": 1.2,
            "tp3": 1.7,
        },
        PairType.CROSS: {
            "tp1": 0.6,
            "tp2": 1.1,
            "tp3": 1.6,
        },
        PairType.GOLD: {
            "tp1": 0.8,
            "tp2": 1.5,
            "tp3": 2.2,
        },
    }

    # JPY pairs: ATR is ~100× larger in price units than non-JPY pairs,
    # so we scale multipliers down to keep pip distances comparable.
    JPY_MULTIPLIER_SCALE: float = 0.7

    # Hard cap: no more than this many pips between consecutive TP levels.
    MAX_PIP_DISTANCE: float = 5.0

    # ── Public API ────────────────────────────────────────────────────────────

    @staticmethod
    def calculate_atr(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int = 14,
    ) -> float:
        """
        Calculate a simple 14-period ATR from H4 OHLC lists.

        Falls back to a close-only approximation when only closes are
        available (highs == lows == closes).

        Returns 0.0 when there is insufficient data.
        """
        n = len(closes)
        if n < period + 1:
            logger.warning(
                f"TPCalculator.calculate_atr: need {period + 1} candles, got {n}"
            )
            return 0.0

        tr_values: List[float] = []
        for i in range(1, n):
            high_i  = highs[i]  if highs  else closes[i]
            low_i   = lows[i]   if lows   else closes[i]
            prev_c  = closes[i - 1]
            tr = max(
                high_i - low_i,
                abs(high_i - prev_c),
                abs(low_i  - prev_c),
            )
            tr_values.append(tr)

        atr = float(np.mean(tr_values[-period:]))
        return atr

    @staticmethod
    def calculate_tp_levels(
        pair:             str,
        entry_price:      float,
        signal_direction: str,
        atr:              float,
        decimal_places:   Optional[int] = None,
    ) -> Tuple[List[float], float]:
        """
        Calculate TP1 / TP2 / TP3 levels based on ATR and pair DNA.

        Parameters
        ----------
        pair             : e.g. "EURUSD"
        entry_price      : confirmed entry price
        signal_direction : "BUY" or "SELL"
        atr              : 14-period H4 ATR (price units)
        decimal_places   : override rounding precision (uses profile default)

        Returns
        -------
        (tp_levels, atr_used)
            tp_levels  — [TP1, TP2, TP3] as floats, or [] on failure
            atr_used   — the ATR value that was applied
        """
        if atr <= 0.0:
            logger.warning(f"TPCalculator: invalid ATR={atr} for {pair} — skipping")
            return [], 0.0

        profile: Optional[PairProfile] = get_pair_profile(pair)
        if profile is None:
            logger.warning(f"TPCalculator: no profile for {pair} — skipping")
            return [], 0.0

        # ── Resolve multipliers for this pair type ────────────────────────────
        base_mults = dict(TPCalculator.MULTIPLIERS.get(profile.pair_type, {}))
        if not base_mults:
            logger.warning(
                f"TPCalculator: no multipliers for pair_type={profile.pair_type} ({pair})"
            )
            return [], 0.0

        # JPY pairs: scale down so pip distances stay realistic
        if profile.is_jpy:
            base_mults = {k: v * TPCalculator.JPY_MULTIPLIER_SCALE for k, v in base_mults.items()}

        # ── Raw TP distances (price units) ───────────────────────────────────
        tp1_dist = atr * base_mults["tp1"]
        tp2_dist = atr * base_mults["tp2"]
        tp3_dist = atr * base_mults["tp3"]

        # ── 5-pip gap constraint ──────────────────────────────────────────────
        pip_size = profile.pip_size
        gap_12_pips = (tp2_dist - tp1_dist) / pip_size
        gap_23_pips = (tp3_dist - tp2_dist) / pip_size

        max_gap = max(gap_12_pips, gap_23_pips)
        if max_gap > TPCalculator.MAX_PIP_DISTANCE:
            scale = TPCalculator.MAX_PIP_DISTANCE / max_gap
            tp1_dist *= scale
            tp2_dist *= scale
            tp3_dist *= scale
            logger.debug(
                f"TPCalculator {pair}: gap {max_gap:.2f} pips > {TPCalculator.MAX_PIP_DISTANCE} "
                f"→ scaled by {scale:.4f}"
            )

        # ── Build TP price levels ─────────────────────────────────────────────
        dp = decimal_places if decimal_places is not None else profile.decimal_places

        if signal_direction.upper() == "BUY":
            tp_levels = [
                round(entry_price + tp1_dist, dp),
                round(entry_price + tp2_dist, dp),
                round(entry_price + tp3_dist, dp),
            ]
        else:  # SELL
            tp_levels = [
                round(entry_price - tp1_dist, dp),
                round(entry_price - tp2_dist, dp),
                round(entry_price - tp3_dist, dp),
            ]

        logger.debug(
            f"TPCalculator {pair} {signal_direction}: "
            f"ATR={atr:.5f} | "
            f"TP1={tp_levels[0]} TP2={tp_levels[1]} TP3={tp_levels[2]} | "
            f"gaps={gap_12_pips:.1f}/{gap_23_pips:.1f} pips"
        )

        return tp_levels, atr

"""
dxy_correlation_engine.py — DXY correlation boost/penalty for the Forex signal engine.

Provides:
  - DXYCorrelationEngine.get_dxy_signal_direction(pair, dxy_score) → str
  - DXYCorrelationEngine.get_dxy_label(dxy_signal, pair) → str
  - DXYCorrelationEngine.apply_dxy_correlation_multiplier(...) → float

DXY score convention (0–100):
    > 60  → DXY BULLISH  (USD strengthening)
    < 40  → DXY BEARISH  (USD weakening)
    40–60 → DXY NEUTRAL
"""

from __future__ import annotations
from pair_profiles import PairProfile, PairType


# ── DXY signal direction constants ────────────────────────────────────────────
DXY_BULLISH = "BULLISH"
DXY_BEARISH = "BEARISH"
DXY_NEUTRAL = "NEUTRAL"

# ── Multiplier table ──────────────────────────────────────────────────────────
# (pair_type, signal_direction, dxy_signal) → confidence multiplier
# Aligned  : DXY confirms signal direction → boost
# Opposed  : DXY contradicts signal direction → penalty
# Neutral  : no DXY edge → no change
_MULTIPLIER_TABLE: dict[tuple[str, str, str], float] = {
    # USD_FOLLOW pairs (e.g. EURUSD, GBPUSD) — inverse DXY relationship
    # DXY BULLISH → USD strong → SELL EUR/GBP is aligned
    (PairType.USD_FOLLOW, "SELL", DXY_BULLISH): 1.10,
    (PairType.USD_FOLLOW, "BUY",  DXY_BULLISH): 0.88,
    (PairType.USD_FOLLOW, "SELL", DXY_BEARISH): 0.88,
    (PairType.USD_FOLLOW, "BUY",  DXY_BEARISH): 1.10,
    (PairType.USD_FOLLOW, "SELL", DXY_NEUTRAL): 1.00,
    (PairType.USD_FOLLOW, "BUY",  DXY_NEUTRAL): 1.00,

    # USD_LED pairs (e.g. USDCAD, USDCHF, USDJPY) — positive DXY relationship
    # DXY BULLISH → USD strong → BUY USD is aligned
    (PairType.USD_LED, "BUY",  DXY_BULLISH): 1.10,
    (PairType.USD_LED, "SELL", DXY_BULLISH): 0.88,
    (PairType.USD_LED, "BUY",  DXY_BEARISH): 0.88,
    (PairType.USD_LED, "SELL", DXY_BEARISH): 1.10,
    (PairType.USD_LED, "BUY",  DXY_NEUTRAL): 1.00,
    (PairType.USD_LED, "SELL", DXY_NEUTRAL): 1.00,

    # CROSS pairs — no direct USD leg → DXY has minimal impact
    (PairType.CROSS, "BUY",  DXY_BULLISH): 1.00,
    (PairType.CROSS, "SELL", DXY_BULLISH): 1.00,
    (PairType.CROSS, "BUY",  DXY_BEARISH): 1.00,
    (PairType.CROSS, "SELL", DXY_BEARISH): 1.00,
    (PairType.CROSS, "BUY",  DXY_NEUTRAL): 1.00,
    (PairType.CROSS, "SELL", DXY_NEUTRAL): 1.00,

    # GOLD — inverse DXY relationship (safe-haven / USD hedge)
    (PairType.GOLD, "BUY",  DXY_BEARISH): 1.12,
    (PairType.GOLD, "SELL", DXY_BULLISH): 1.12,
    (PairType.GOLD, "BUY",  DXY_BULLISH): 0.85,
    (PairType.GOLD, "SELL", DXY_BEARISH): 0.85,
    (PairType.GOLD, "BUY",  DXY_NEUTRAL): 1.00,
    (PairType.GOLD, "SELL", DXY_NEUTRAL): 1.00,
}

# ── Confidence clamp ──────────────────────────────────────────────────────────
_MIN_CONFIDENCE = 0.0
_MAX_CONFIDENCE = 100.0


class DXYCorrelationEngine:
    """
    Stateless utility class for DXY-based confidence adjustment.

    The engine converts a DXY score (0–100) into a directional signal
    (BULLISH / BEARISH / NEUTRAL) and then applies a multiplier to the
    base confidence score based on the pair's USD relationship.
    """

    @staticmethod
    def get_dxy_signal_direction(pair: str, dxy_score: float) -> str:
        """
        Convert a numeric DXY score to a directional string.

        Args:
            pair      : Currency pair symbol (used for JPY exception).
            dxy_score : 0–100 score where >60 = bullish, <40 = bearish.

        Returns:
            "BULLISH" | "BEARISH" | "NEUTRAL"
        """
        if dxy_score > 60.0:
            return DXY_BULLISH
        elif dxy_score < 40.0:
            return DXY_BEARISH
        return DXY_NEUTRAL

    @staticmethod
    def get_dxy_label(dxy_signal: str, pair: str) -> str:
        """
        Return a human-readable DXY label for Telegram display.

        For JPY pairs the label notes the JPY safe-haven exception.
        """
        s = pair.upper()
        is_jpy = "JPY" in s
        is_gold = "XAU" in s

        if dxy_signal == DXY_BULLISH:
            if is_jpy:
                return "DXY BULLISH (JPY safe-haven — mixed)"
            if is_gold:
                return "DXY BULLISH (Gold headwind)"
            return "DXY BULLISH (USD strength)"
        elif dxy_signal == DXY_BEARISH:
            if is_jpy:
                return "DXY BEARISH (JPY safe-haven — mixed)"
            if is_gold:
                return "DXY BEARISH (Gold tailwind)"
            return "DXY BEARISH (USD weakness)"
        return "DXY NEUTRAL"

    @staticmethod
    def apply_dxy_correlation_multiplier(
        signal_direction: str,
        dxy_signal:       str,
        base_confidence:  float,
        pair_profile:     PairProfile,
    ) -> float:
        """
        Apply a DXY-based multiplier to *base_confidence*.

        Args:
            signal_direction : "BUY" or "SELL"
            dxy_signal       : "BULLISH" | "BEARISH" | "NEUTRAL"
            base_confidence  : Weighted score (0–100)
            pair_profile     : PairProfile for the pair being evaluated

        Returns:
            Adjusted confidence clamped to [0, 100].
        """
        key = (pair_profile.pair_type, signal_direction.upper(), dxy_signal)
        multiplier = _MULTIPLIER_TABLE.get(key, 1.0)
        adjusted = base_confidence * multiplier
        return round(max(_MIN_CONFIDENCE, min(_MAX_CONFIDENCE, adjusted)), 2)

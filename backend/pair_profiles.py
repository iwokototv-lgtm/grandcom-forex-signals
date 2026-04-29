"""
pair_profiles.py — Pair DNA definitions for ATR-based TP calculation.

Each Forex pair is classified into one of four types that drive the
ATR multiplier selection in TPCalculator.  Gold pairs are handled
separately but share the same enum so the calculator can be used
uniformly across all assets.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional


class PairType(Enum):
    USD_LED    = "USD_LED"     # USD is the base  (USDJPY, USDCAD, USDCHF)
    USD_FOLLOW = "USD_FOLLOW"  # USD is the quote (EURUSD, GBPUSD, AUDUSD, NZDUSD)
    CROSS      = "CROSS"       # No USD leg       (EURJPY, GBPJPY, EURGBP, …)
    GOLD       = "GOLD"        # XAU pairs


@dataclass
class PairProfile:
    symbol:    str
    pair_type: PairType
    pip_size:  float   # 1 pip in price units (0.0001 for 4-dp pairs, 0.01 for JPY)
    is_jpy:    bool    # True → apply JPY multiplier scale-down
    decimal_places: int


# ── Master pair registry ──────────────────────────────────────────────────────
_PROFILES: dict[str, PairProfile] = {
    # USD-led (USD is base currency)
    "USDJPY": PairProfile("USDJPY", PairType.USD_LED,    0.01,   True,  3),
    "USDCAD": PairProfile("USDCAD", PairType.USD_LED,    0.0001, False, 5),
    "USDCHF": PairProfile("USDCHF", PairType.USD_LED,    0.0001, False, 5),

    # USD-follow (USD is quote currency)
    "EURUSD": PairProfile("EURUSD", PairType.USD_FOLLOW, 0.0001, False, 5),
    "GBPUSD": PairProfile("GBPUSD", PairType.USD_FOLLOW, 0.0001, False, 5),
    "AUDUSD": PairProfile("AUDUSD", PairType.USD_FOLLOW, 0.0001, False, 5),
    "NZDUSD": PairProfile("NZDUSD", PairType.USD_FOLLOW, 0.0001, False, 5),

    # Cross pairs — JPY crosses
    "EURJPY": PairProfile("EURJPY", PairType.CROSS,      0.01,   True,  3),
    "GBPJPY": PairProfile("GBPJPY", PairType.CROSS,      0.01,   True,  3),
    "AUDJPY": PairProfile("AUDJPY", PairType.CROSS,      0.01,   True,  3),
    "CADJPY": PairProfile("CADJPY", PairType.CROSS,      0.01,   True,  3),
    "CHFJPY": PairProfile("CHFJPY", PairType.CROSS,      0.01,   True,  3),
    "NZDJPY": PairProfile("NZDJPY", PairType.CROSS,      0.01,   True,  3),

    # Cross pairs — non-JPY
    "EURGBP": PairProfile("EURGBP", PairType.CROSS,      0.0001, False, 5),
    "EURCHF": PairProfile("EURCHF", PairType.CROSS,      0.0001, False, 5),
    "EURAUD": PairProfile("EURAUD", PairType.CROSS,      0.0001, False, 5),
    "EURCAD": PairProfile("EURCAD", PairType.CROSS,      0.0001, False, 5),
    "GBPAUD": PairProfile("GBPAUD", PairType.CROSS,      0.0001, False, 5),
    "GBPCAD": PairProfile("GBPCAD", PairType.CROSS,      0.0001, False, 5),
    "GBPCHF": PairProfile("GBPCHF", PairType.CROSS,      0.0001, False, 5),
    "AUDNZD": PairProfile("AUDNZD", PairType.CROSS,      0.0001, False, 5),
    "AUDCAD": PairProfile("AUDCAD", PairType.CROSS,      0.0001, False, 5),
    "AUDCHF": PairProfile("AUDCHF", PairType.CROSS,      0.0001, False, 5),
    "NZDCAD": PairProfile("NZDCAD", PairType.CROSS,      0.0001, False, 5),
    "NZDCHF": PairProfile("NZDCHF", PairType.CROSS,      0.0001, False, 5),
    "CADCHF": PairProfile("CADCHF", PairType.CROSS,      0.0001, False, 5),

    # Gold
    "XAUUSD": PairProfile("XAUUSD", PairType.GOLD,       0.10,   False, 2),
    "XAUEUR": PairProfile("XAUEUR", PairType.GOLD,       0.10,   False, 2),
}


def get_pair_profile(symbol: str) -> Optional[PairProfile]:
    """Return the PairProfile for *symbol*, or None if unknown."""
    return _PROFILES.get(symbol.upper())

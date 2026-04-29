from enum import Enum
from dataclasses import dataclass
from typing import Dict


class PairType(Enum):
    USD_LED = "USD_LED"      # USDJPY, USDCAD, USDCHF (Strong DXY = BUY)
    USD_FOLLOW = "USD_FOLLOW"  # EURUSD, GBPUSD, AUDUSD, NZDUSD (Strong DXY = SELL)
    CROSS = "CROSS"          # EURGBP, EURJPY, GBPJPY, etc (DXY neutral)


class SessionType(Enum):
    ASIAN = "ASIAN"      # 00:00-08:00 UTC
    LONDON = "LONDON"    # 08:00-16:00 UTC
    NEWYORK = "NEWYORK"  # 13:00-21:00 UTC
    OVERLAP = "OVERLAP"  # 13:00-16:00 UTC (London-NY Golden Window)


@dataclass
class PairProfile:
    symbol: str
    pair_type: PairType
    primary_session: SessionType
    pip_size: float          # 0.01 for JPY, 0.0001 for others
    decimal_places: int      # 3 for JPY, 5 for others
    spread_typical: float    # Typical spread in pips
    sl_multiplier: float     # 1.2 × ATR (M1)
    tp1_spread_multiplier: float   # 2 × Spread + 0.5
    tp2_atr_multiplier: float      # 0.5 × ATR (M15)
    tp3_atr_multiplier: float      # 1.0 × ATR (H1)
    gate_score_overlap: int        # Lower gate score during overlap (e.g., 55 for EURUSD)
    confidence_session_penalty: float  # 0.8 = 20% penalty outside primary session
    dxy_sensitivity: float         # How much DXY affects this pair (0.0-1.0)


# Define all 21 pairs
PAIR_PROFILES: Dict[str, PairProfile] = {
    # ── USD-Led (Strong DXY = BUY) ────────────────────────────────────
    "USDJPY": PairProfile(
        symbol="USDJPY",
        pair_type=PairType.USD_LED,
        primary_session=SessionType.ASIAN,
        pip_size=0.01,
        decimal_places=3,
        spread_typical=1.5,
        sl_multiplier=1.2,
        tp1_spread_multiplier=2.0,
        tp2_atr_multiplier=0.5,
        tp3_atr_multiplier=1.0,
        gate_score_overlap=60,
        confidence_session_penalty=0.8,
        dxy_sensitivity=0.9,
    ),
    "USDCAD": PairProfile(
        symbol="USDCAD",
        pair_type=PairType.USD_LED,
        primary_session=SessionType.NEWYORK,
        pip_size=0.0001,
        decimal_places=5,
        spread_typical=1.2,
        sl_multiplier=1.2,
        tp1_spread_multiplier=2.0,
        tp2_atr_multiplier=0.5,
        tp3_atr_multiplier=1.0,
        gate_score_overlap=60,
        confidence_session_penalty=0.8,
        dxy_sensitivity=0.85,
    ),
    "USDCHF": PairProfile(
        symbol="USDCHF",
        pair_type=PairType.USD_LED,
        primary_session=SessionType.LONDON,
        pip_size=0.0001,
        decimal_places=5,
        spread_typical=1.3,
        sl_multiplier=1.2,
        tp1_spread_multiplier=2.0,
        tp2_atr_multiplier=0.5,
        tp3_atr_multiplier=1.0,
        gate_score_overlap=60,
        confidence_session_penalty=0.8,
        dxy_sensitivity=0.8,
    ),

    # ── USD-Follow (Strong DXY = SELL) ────────────────────────────────
    "EURUSD": PairProfile(
        symbol="EURUSD",
        pair_type=PairType.USD_FOLLOW,
        primary_session=SessionType.LONDON,
        pip_size=0.0001,
        decimal_places=5,
        spread_typical=1.0,
        sl_multiplier=1.2,
        tp1_spread_multiplier=2.0,
        tp2_atr_multiplier=0.5,
        tp3_atr_multiplier=1.0,
        gate_score_overlap=55,   # Lower during overlap = more sensitive
        confidence_session_penalty=0.8,
        dxy_sensitivity=0.95,
    ),
    "GBPUSD": PairProfile(
        symbol="GBPUSD",
        pair_type=PairType.USD_FOLLOW,
        primary_session=SessionType.LONDON,
        pip_size=0.0001,
        decimal_places=5,
        spread_typical=1.2,
        sl_multiplier=1.2,
        tp1_spread_multiplier=2.0,
        tp2_atr_multiplier=0.5,
        tp3_atr_multiplier=1.0,
        gate_score_overlap=55,
        confidence_session_penalty=0.8,
        dxy_sensitivity=0.9,
    ),
    "AUDUSD": PairProfile(
        symbol="AUDUSD",
        pair_type=PairType.USD_FOLLOW,
        primary_session=SessionType.ASIAN,
        pip_size=0.0001,
        decimal_places=5,
        spread_typical=1.3,
        sl_multiplier=1.2,
        tp1_spread_multiplier=2.0,
        tp2_atr_multiplier=0.5,
        tp3_atr_multiplier=1.0,
        gate_score_overlap=60,
        confidence_session_penalty=0.8,
        dxy_sensitivity=0.85,
    ),
    "NZDUSD": PairProfile(
        symbol="NZDUSD",
        pair_type=PairType.USD_FOLLOW,
        primary_session=SessionType.ASIAN,
        pip_size=0.0001,
        decimal_places=5,
        spread_typical=1.5,
        sl_multiplier=1.2,
        tp1_spread_multiplier=2.0,
        tp2_atr_multiplier=0.5,
        tp3_atr_multiplier=1.0,
        gate_score_overlap=60,
        confidence_session_penalty=0.8,
        dxy_sensitivity=0.8,
    ),

    # ── Crosses (DXY neutral) ─────────────────────────────────────────
    "EURGBP": PairProfile(
        symbol="EURGBP",
        pair_type=PairType.CROSS,
        primary_session=SessionType.LONDON,
        pip_size=0.0001,
        decimal_places=5,
        spread_typical=1.0,
        sl_multiplier=1.2,
        tp1_spread_multiplier=2.0,
        tp2_atr_multiplier=0.5,
        tp3_atr_multiplier=1.0,
        gate_score_overlap=60,
        confidence_session_penalty=0.8,
        dxy_sensitivity=0.3,
    ),
    "EURJPY": PairProfile(
        symbol="EURJPY",
        pair_type=PairType.CROSS,
        primary_session=SessionType.LONDON,
        pip_size=0.01,
        decimal_places=3,
        spread_typical=2.0,
        sl_multiplier=1.2,
        tp1_spread_multiplier=2.0,
        tp2_atr_multiplier=0.5,
        tp3_atr_multiplier=1.0,
        gate_score_overlap=60,
        confidence_session_penalty=0.8,
        dxy_sensitivity=0.4,
    ),
    "GBPJPY": PairProfile(
        symbol="GBPJPY",
        pair_type=PairType.CROSS,
        primary_session=SessionType.LONDON,
        pip_size=0.01,
        decimal_places=3,
        spread_typical=2.5,
        sl_multiplier=1.2,
        tp1_spread_multiplier=2.0,
        tp2_atr_multiplier=0.5,
        tp3_atr_multiplier=1.0,
        gate_score_overlap=60,
        confidence_session_penalty=0.8,
        dxy_sensitivity=0.35,
    ),
    "EURCAD": PairProfile(
        symbol="EURCAD",
        pair_type=PairType.CROSS,
        primary_session=SessionType.LONDON,
        pip_size=0.0001,
        decimal_places=5,
        spread_typical=1.5,
        sl_multiplier=1.2,
        tp1_spread_multiplier=2.0,
        tp2_atr_multiplier=0.5,
        tp3_atr_multiplier=1.0,
        gate_score_overlap=60,
        confidence_session_penalty=0.8,
        dxy_sensitivity=0.5,
    ),
    "EURCHF": PairProfile(
        symbol="EURCHF",
        pair_type=PairType.CROSS,
        primary_session=SessionType.LONDON,
        pip_size=0.0001,
        decimal_places=5,
        spread_typical=1.3,
        sl_multiplier=1.2,
        tp1_spread_multiplier=2.0,
        tp2_atr_multiplier=0.5,
        tp3_atr_multiplier=1.0,
        gate_score_overlap=60,
        confidence_session_penalty=0.8,
        dxy_sensitivity=0.45,
    ),
    "GBPCHF": PairProfile(
        symbol="GBPCHF",
        pair_type=PairType.CROSS,
        primary_session=SessionType.LONDON,
        pip_size=0.0001,
        decimal_places=5,
        spread_typical=1.5,
        sl_multiplier=1.2,
        tp1_spread_multiplier=2.0,
        tp2_atr_multiplier=0.5,
        tp3_atr_multiplier=1.0,
        gate_score_overlap=60,
        confidence_session_penalty=0.8,
        dxy_sensitivity=0.4,
    ),
    "AUDNZD": PairProfile(
        symbol="AUDNZD",
        pair_type=PairType.CROSS,
        primary_session=SessionType.ASIAN,
        pip_size=0.0001,
        decimal_places=5,
        spread_typical=1.8,
        sl_multiplier=1.2,
        tp1_spread_multiplier=2.0,
        tp2_atr_multiplier=0.5,
        tp3_atr_multiplier=1.0,
        gate_score_overlap=60,
        confidence_session_penalty=0.8,
        dxy_sensitivity=0.2,
    ),
    "AUDCAD": PairProfile(
        symbol="AUDCAD",
        pair_type=PairType.CROSS,
        primary_session=SessionType.ASIAN,
        pip_size=0.0001,
        decimal_places=5,
        spread_typical=1.6,
        sl_multiplier=1.2,
        tp1_spread_multiplier=2.0,
        tp2_atr_multiplier=0.5,
        tp3_atr_multiplier=1.0,
        gate_score_overlap=60,
        confidence_session_penalty=0.8,
        dxy_sensitivity=0.35,
    ),
    "NZDCAD": PairProfile(
        symbol="NZDCAD",
        pair_type=PairType.CROSS,
        primary_session=SessionType.ASIAN,
        pip_size=0.0001,
        decimal_places=5,
        spread_typical=1.8,
        sl_multiplier=1.2,
        tp1_spread_multiplier=2.0,
        tp2_atr_multiplier=0.5,
        tp3_atr_multiplier=1.0,
        gate_score_overlap=60,
        confidence_session_penalty=0.8,
        dxy_sensitivity=0.3,
    ),
    "CADJPY": PairProfile(
        symbol="CADJPY",
        pair_type=PairType.CROSS,
        primary_session=SessionType.NEWYORK,
        pip_size=0.01,
        decimal_places=3,
        spread_typical=2.2,
        sl_multiplier=1.2,
        tp1_spread_multiplier=2.0,
        tp2_atr_multiplier=0.5,
        tp3_atr_multiplier=1.0,
        gate_score_overlap=60,
        confidence_session_penalty=0.8,
        dxy_sensitivity=0.4,
    ),
    "CHFJPY": PairProfile(
        symbol="CHFJPY",
        pair_type=PairType.CROSS,
        primary_session=SessionType.LONDON,
        pip_size=0.01,
        decimal_places=3,
        spread_typical=2.3,
        sl_multiplier=1.2,
        tp1_spread_multiplier=2.0,
        tp2_atr_multiplier=0.5,
        tp3_atr_multiplier=1.0,
        gate_score_overlap=60,
        confidence_session_penalty=0.8,
        dxy_sensitivity=0.35,
    ),
    "GBPAUD": PairProfile(
        symbol="GBPAUD",
        pair_type=PairType.CROSS,
        primary_session=SessionType.LONDON,
        pip_size=0.0001,
        decimal_places=5,
        spread_typical=1.8,
        sl_multiplier=1.2,
        tp1_spread_multiplier=2.0,
        tp2_atr_multiplier=0.5,
        tp3_atr_multiplier=1.0,
        gate_score_overlap=60,
        confidence_session_penalty=0.8,
        dxy_sensitivity=0.3,
    ),
    "NZDJPY": PairProfile(
        symbol="NZDJPY",
        pair_type=PairType.CROSS,
        primary_session=SessionType.ASIAN,
        pip_size=0.01,
        decimal_places=3,
        spread_typical=2.0,
        sl_multiplier=1.2,
        tp1_spread_multiplier=2.0,
        tp2_atr_multiplier=0.5,
        tp3_atr_multiplier=1.0,
        gate_score_overlap=60,
        confidence_session_penalty=0.8,
        dxy_sensitivity=0.3,
    ),
    "AUDJPY": PairProfile(
        symbol="AUDJPY",
        pair_type=PairType.CROSS,
        primary_session=SessionType.ASIAN,
        pip_size=0.01,
        decimal_places=3,
        spread_typical=2.0,
        sl_multiplier=1.2,
        tp1_spread_multiplier=2.0,
        tp2_atr_multiplier=0.5,
        tp3_atr_multiplier=1.0,
        gate_score_overlap=60,
        confidence_session_penalty=0.8,
        dxy_sensitivity=0.35,
    ),
}


def get_pair_profile(symbol: str) -> PairProfile:
    """Return the PairProfile for a symbol, or None if not found."""
    return PAIR_PROFILES.get(symbol.upper())


def get_pip_size(symbol: str) -> float:
    """Return pip size for a symbol (0.01 for JPY pairs, 0.0001 for others)."""
    profile = get_pair_profile(symbol)
    return profile.pip_size if profile else 0.0001


def get_decimal_places(symbol: str) -> int:
    """Return decimal places for a symbol (3 for JPY pairs, 5 for others)."""
    profile = get_pair_profile(symbol)
    return profile.decimal_places if profile else 5

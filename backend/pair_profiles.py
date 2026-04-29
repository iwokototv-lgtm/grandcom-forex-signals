"""
pair_profiles.py — Institutional pair metadata for the Forex signal engine.

Provides:
  - PairProfile dataclass with pair_type, primary_sessions, pip_size, decimal_places
  - get_pair_profile(symbol) → PairProfile | None
  - get_pip_size(symbol) → float
  - get_decimal_places(symbol) → int
  - PairType / SessionType enums
  - PAIR_PROFILES dict (21 pairs)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class PairType(str, Enum):
    """Institutional classification of a currency pair by USD relationship."""
    USD_LED    = "USD_LED"     # USD is the base or quote — DXY directly drives price
    USD_FOLLOW = "USD_FOLLOW"  # USD is quote — inverse DXY relationship
    CROSS      = "CROSS"       # No USD leg — driven by relative currency strength
    GOLD       = "GOLD"        # XAU/USD — commodity, safe-haven, DXY inverse


class SessionType(str, Enum):
    """Forex trading session windows (UTC)."""
    ASIAN    = "ASIAN"     # 00:00–08:00 UTC  (Tokyo)
    LONDON   = "LONDON"    # 08:00–16:00 UTC
    NEWYORK  = "NEWYORK"   # 13:00–21:00 UTC
    OVERLAP  = "OVERLAP"   # London/NY overlap 13:00–16:00 UTC (highest liquidity)


@dataclass
class PairProfile:
    """
    Institutional metadata for a single currency pair.

    Attributes:
        symbol           : e.g. "EURUSD"
        pair_type        : PairType enum — USD_LED / USD_FOLLOW / CROSS / GOLD
        primary_sessions : Sessions where this pair has highest liquidity
        pip_size         : Raw pip size (0.0001 for most FX, 0.01 for JPY/Gold)
        decimal_places   : Price decimal places for display
        dxy_correlation  : +1.0 (positive) / -1.0 (inverse) / 0.0 (none)
        is_jpy           : True if JPY is in the pair (special pip handling)
        is_gold          : True if XAU is in the pair
        session_gate_pct : Minimum session confidence multiplier before skipping
    """
    symbol:           str
    pair_type:        PairType
    primary_sessions: List[SessionType]
    pip_size:         float
    decimal_places:   int
    dxy_correlation:  float = 0.0
    is_jpy:           bool  = False
    is_gold:          bool  = False
    session_gate_pct: float = 0.7   # multiplier below which off-session penalty applies


# ── 21-pair profile registry ──────────────────────────────────────────────────
PAIR_PROFILES: dict[str, PairProfile] = {

    # ── Major USD pairs ───────────────────────────────────────────────────────
    "EURUSD": PairProfile(
        symbol="EURUSD", pair_type=PairType.USD_FOLLOW,
        primary_sessions=[SessionType.LONDON, SessionType.NEWYORK, SessionType.OVERLAP],
        pip_size=0.0001, decimal_places=5, dxy_correlation=-1.0,
    ),
    "GBPUSD": PairProfile(
        symbol="GBPUSD", pair_type=PairType.USD_FOLLOW,
        primary_sessions=[SessionType.LONDON, SessionType.NEWYORK, SessionType.OVERLAP],
        pip_size=0.0001, decimal_places=5, dxy_correlation=-1.0,
    ),
    "AUDUSD": PairProfile(
        symbol="AUDUSD", pair_type=PairType.USD_FOLLOW,
        primary_sessions=[SessionType.ASIAN, SessionType.LONDON],
        pip_size=0.0001, decimal_places=5, dxy_correlation=-1.0,
    ),
    "NZDUSD": PairProfile(
        symbol="NZDUSD", pair_type=PairType.USD_FOLLOW,
        primary_sessions=[SessionType.ASIAN, SessionType.LONDON],
        pip_size=0.0001, decimal_places=5, dxy_correlation=-1.0,
    ),
    "USDCAD": PairProfile(
        symbol="USDCAD", pair_type=PairType.USD_LED,
        primary_sessions=[SessionType.NEWYORK, SessionType.OVERLAP],
        pip_size=0.0001, decimal_places=5, dxy_correlation=1.0,
    ),
    "USDCHF": PairProfile(
        symbol="USDCHF", pair_type=PairType.USD_LED,
        primary_sessions=[SessionType.LONDON, SessionType.NEWYORK, SessionType.OVERLAP],
        pip_size=0.0001, decimal_places=5, dxy_correlation=1.0,
    ),
    "USDJPY": PairProfile(
        symbol="USDJPY", pair_type=PairType.USD_LED,
        primary_sessions=[SessionType.ASIAN, SessionType.LONDON, SessionType.NEWYORK],
        pip_size=0.01, decimal_places=3, dxy_correlation=1.0, is_jpy=True,
    ),

    # ── JPY cross pairs ───────────────────────────────────────────────────────
    "EURJPY": PairProfile(
        symbol="EURJPY", pair_type=PairType.CROSS,
        primary_sessions=[SessionType.ASIAN, SessionType.LONDON],
        pip_size=0.01, decimal_places=3, dxy_correlation=0.0, is_jpy=True,
    ),
    "GBPJPY": PairProfile(
        symbol="GBPJPY", pair_type=PairType.CROSS,
        primary_sessions=[SessionType.ASIAN, SessionType.LONDON],
        pip_size=0.01, decimal_places=3, dxy_correlation=0.0, is_jpy=True,
    ),
    "AUDJPY": PairProfile(
        symbol="AUDJPY", pair_type=PairType.CROSS,
        primary_sessions=[SessionType.ASIAN],
        pip_size=0.01, decimal_places=3, dxy_correlation=0.0, is_jpy=True,
    ),
    "CADJPY": PairProfile(
        symbol="CADJPY", pair_type=PairType.CROSS,
        primary_sessions=[SessionType.ASIAN, SessionType.NEWYORK],
        pip_size=0.01, decimal_places=3, dxy_correlation=0.0, is_jpy=True,
    ),
    "CHFJPY": PairProfile(
        symbol="CHFJPY", pair_type=PairType.CROSS,
        primary_sessions=[SessionType.ASIAN, SessionType.LONDON],
        pip_size=0.01, decimal_places=3, dxy_correlation=0.0, is_jpy=True,
    ),

    # ── EUR cross pairs ───────────────────────────────────────────────────────
    "EURGBP": PairProfile(
        symbol="EURGBP", pair_type=PairType.CROSS,
        primary_sessions=[SessionType.LONDON],
        pip_size=0.0001, decimal_places=5, dxy_correlation=0.0,
    ),
    "EURAUD": PairProfile(
        symbol="EURAUD", pair_type=PairType.CROSS,
        primary_sessions=[SessionType.ASIAN, SessionType.LONDON],
        pip_size=0.0001, decimal_places=5, dxy_correlation=0.0,
    ),
    "EURCAD": PairProfile(
        symbol="EURCAD", pair_type=PairType.CROSS,
        primary_sessions=[SessionType.LONDON, SessionType.NEWYORK],
        pip_size=0.0001, decimal_places=5, dxy_correlation=0.0,
    ),
    "EURCHF": PairProfile(
        symbol="EURCHF", pair_type=PairType.CROSS,
        primary_sessions=[SessionType.LONDON],
        pip_size=0.0001, decimal_places=5, dxy_correlation=0.0,
    ),

    # ── GBP cross pairs ───────────────────────────────────────────────────────
    "GBPAUD": PairProfile(
        symbol="GBPAUD", pair_type=PairType.CROSS,
        primary_sessions=[SessionType.ASIAN, SessionType.LONDON],
        pip_size=0.0001, decimal_places=5, dxy_correlation=0.0,
    ),
    "GBPCAD": PairProfile(
        symbol="GBPCAD", pair_type=PairType.CROSS,
        primary_sessions=[SessionType.LONDON, SessionType.NEWYORK],
        pip_size=0.0001, decimal_places=5, dxy_correlation=0.0,
    ),

    # ── AUD/NZD cross ─────────────────────────────────────────────────────────
    "AUDNZD": PairProfile(
        symbol="AUDNZD", pair_type=PairType.CROSS,
        primary_sessions=[SessionType.ASIAN],
        pip_size=0.0001, decimal_places=5, dxy_correlation=0.0,
    ),

    # ── Gold ─────────────────────────────────────────────────────────────────
    "XAUUSD": PairProfile(
        symbol="XAUUSD", pair_type=PairType.GOLD,
        primary_sessions=[SessionType.LONDON, SessionType.NEWYORK, SessionType.OVERLAP],
        pip_size=0.01, decimal_places=2, dxy_correlation=-1.0, is_gold=True,
    ),
    "XAUEUR": PairProfile(
        symbol="XAUEUR", pair_type=PairType.GOLD,
        primary_sessions=[SessionType.LONDON, SessionType.OVERLAP],
        pip_size=0.01, decimal_places=2, dxy_correlation=-0.7, is_gold=True,
    ),
}


# ── Public helpers ────────────────────────────────────────────────────────────

def get_pair_profile(symbol: str) -> Optional[PairProfile]:
    """Return the PairProfile for *symbol*, or None if not registered."""
    return PAIR_PROFILES.get(symbol.upper())


def get_pip_size(symbol: str) -> float:
    """Return the pip size for *symbol* (0.0001 for FX, 0.01 for JPY/Gold)."""
    profile = get_pair_profile(symbol)
    if profile:
        return profile.pip_size
    # Fallback heuristic
    s = symbol.upper()
    if "JPY" in s or "XAU" in s:
        return 0.01
    return 0.0001


def get_decimal_places(symbol: str) -> int:
    """Return the number of decimal places for *symbol*."""
    profile = get_pair_profile(symbol)
    if profile:
        return profile.decimal_places
    s = symbol.upper()
    if "JPY" in s:
        return 3
    if "XAU" in s:
        return 2
    return 5

"""
session_manager.py — Session-aware confidence multipliers for the Forex signal engine.

Provides:
  - SessionManager.get_current_session() → SessionType
  - SessionManager.get_session_label() → str
  - SessionManager.is_high_liquidity_session(pair_profile) → bool
  - SessionManager.get_session_confidence_multiplier(pair_profile) → float
  - SessionManager.get_gate_score_threshold(pair_profile) → int
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

from pair_profiles import PairProfile, SessionType


class SessionManager:
    """
    Stateless utility class for session-aware signal filtering.

    Session windows (UTC):
        ASIAN    : 00:00–08:00
        LONDON   : 08:00–13:00
        OVERLAP  : 13:00–16:00  (London/NY — highest liquidity)
        NEWYORK  : 16:00–21:00
        OFF      : 21:00–00:00  (dead zone — no signals)
    """

    # ── Session hour boundaries (UTC) ─────────────────────────────────────────
    _SESSIONS = [
        (0,  8,  SessionType.ASIAN),
        (8,  13, SessionType.LONDON),
        (13, 16, SessionType.OVERLAP),
        (16, 21, SessionType.NEWYORK),
    ]

    # ── Confidence multipliers for off-session pairs ──────────────────────────
    # A pair trading outside its primary session gets a penalty multiplier.
    # Pairs IN their primary session (or OVERLAP) get 1.0 (no penalty).
    _OFF_SESSION_MULTIPLIER = 0.80   # 20% confidence penalty
    _OVERLAP_BONUS          = 1.05   # 5% bonus during London/NY overlap

    # ── Gate score thresholds ─────────────────────────────────────────────────
    _GATE_IN_SESSION  = 60   # standard gate when pair is in primary session
    _GATE_OFF_SESSION = 68   # raised gate when pair is outside primary session

    @classmethod
    def get_current_session(cls) -> Optional[SessionType]:
        """Return the current SessionType based on UTC hour, or None if off-hours."""
        hour = datetime.now(timezone.utc).hour
        for start, end, session in cls._SESSIONS:
            if start <= hour < end:
                return session
        return None  # 21:00–00:00 UTC — dead zone

    @classmethod
    def get_session_label(cls) -> str:
        """Return a human-readable session label for Telegram display."""
        session = cls.get_current_session()
        if session is None:
            return "Off-Hours (21–00 UTC)"
        labels = {
            SessionType.ASIAN:   "Asian (00–08 UTC)",
            SessionType.LONDON:  "London (08–13 UTC)",
            SessionType.OVERLAP: "London/NY Overlap (13–16 UTC)",
            SessionType.NEWYORK: "New York (16–21 UTC)",
        }
        return labels.get(session, str(session))

    @classmethod
    def is_high_liquidity_session(cls, pair_profile: PairProfile) -> bool:
        """
        Return True if the current session is a primary session for this pair,
        or if we are in the London/NY overlap (universally high liquidity).
        """
        current = cls.get_current_session()
        if current is None:
            return False
        if current == SessionType.OVERLAP:
            return True  # Overlap is high-liquidity for all pairs
        return current in pair_profile.primary_sessions

    @classmethod
    def get_session_confidence_multiplier(cls, pair_profile: PairProfile) -> float:
        """
        Return a confidence multiplier based on whether the pair is in its
        primary session.

        Returns:
            1.05  — London/NY overlap (bonus)
            1.0   — pair is in a primary session
            0.80  — pair is outside its primary session (penalty)
        """
        current = cls.get_current_session()
        if current is None:
            # Dead zone — apply maximum penalty
            return cls._OFF_SESSION_MULTIPLIER
        if current == SessionType.OVERLAP:
            return cls._OVERLAP_BONUS
        if current in pair_profile.primary_sessions:
            return 1.0
        return cls._OFF_SESSION_MULTIPLIER

    @classmethod
    def get_gate_score_threshold(cls, pair_profile: PairProfile) -> int:
        """
        Return the weighted-score gate threshold for this pair in the current session.

        In-session pairs use the standard 60% gate.
        Off-session pairs use a raised 68% gate to compensate for lower liquidity.
        """
        if cls.is_high_liquidity_session(pair_profile):
            return cls._GATE_IN_SESSION
        return cls._GATE_OFF_SESSION

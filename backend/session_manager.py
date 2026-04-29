from datetime import datetime
from pair_profiles import SessionType, PairProfile


class SessionManager:
    """
    Manages Forex trading session windows and determines liquidity quality
    for a given pair at the current UTC time.

    Sessions:
        ASIAN   : 00:00-08:00 UTC  (Tokyo)
        LONDON  : 08:00-16:00 UTC
        NEWYORK : 13:00-21:00 UTC
        OVERLAP : 13:00-16:00 UTC  (London-NY Golden Window — highest liquidity)
    """

    SESSIONS = {
        SessionType.ASIAN:   (0,  8),
        SessionType.LONDON:  (8,  16),
        SessionType.NEWYORK: (13, 21),
        SessionType.OVERLAP: (13, 16),
    }

    @staticmethod
    def get_current_session() -> SessionType:
        """
        Return the most specific active session for the current UTC hour.
        OVERLAP takes priority over LONDON and NEWYORK when all three overlap.
        """
        hour = datetime.utcnow().hour
        if 13 <= hour < 16:
            return SessionType.OVERLAP
        elif 8 <= hour < 16:
            return SessionType.LONDON
        elif 13 <= hour < 21:
            # This branch is only reached for hours 16-20 (post-overlap NY)
            return SessionType.NEWYORK
        else:
            return SessionType.ASIAN

    @staticmethod
    def is_high_liquidity_session(pair_profile: PairProfile) -> bool:
        """
        Return True if the current session is the pair's primary session
        or the London-NY overlap (always high liquidity).
        """
        current_session = SessionManager.get_current_session()
        return (
            current_session == pair_profile.primary_session
            or current_session == SessionType.OVERLAP
        )

    @staticmethod
    def get_session_confidence_multiplier(pair_profile: PairProfile) -> float:
        """
        Return a confidence multiplier based on session quality:
          - OVERLAP or primary session → 1.0 (no penalty)
          - Outside primary session   → pair_profile.confidence_session_penalty (e.g. 0.8)
        """
        current_session = SessionManager.get_current_session()
        if current_session in (SessionType.OVERLAP, pair_profile.primary_session):
            return 1.0
        return pair_profile.confidence_session_penalty

    @staticmethod
    def get_gate_score_threshold(pair_profile: PairProfile) -> int:
        """
        Return the minimum gate score required to pass a signal.
        During the London-NY overlap the threshold is lowered (more permissive)
        to capture the Golden Window's extra liquidity.
        """
        current_session = SessionManager.get_current_session()
        if current_session == SessionType.OVERLAP:
            return pair_profile.gate_score_overlap
        return 60

    @staticmethod
    def get_session_label() -> str:
        """Return a human-readable label for the current session."""
        session = SessionManager.get_current_session()
        labels = {
            SessionType.OVERLAP: "🔥 London-NY Overlap",
            SessionType.LONDON:  "🇬🇧 London",
            SessionType.NEWYORK: "🗽 New York",
            SessionType.ASIAN:   "🌏 Asian",
        }
        return labels.get(session, "Unknown")

"""
Session Quality Detection — Grandcom Gold Signals v3.0.2
Phase 2 Enhancement: Session-aware signal filtering

Provides:
- Session detection (London, NY, Asia, Off)
- London open recommendation (07:00 UTC)
- Post-NY close detection (22:00 UTC)
- Session-based MTF weighting
- Liquidity scoring per session
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Session windows (UTC hours)
# ─────────────────────────────────────────────────────────────

SESSIONS = {
    "LONDON": {"start": 7,  "end": 16, "peak_start": 7,  "peak_end": 9},
    "NY":     {"start": 13, "end": 22, "peak_start": 13, "peak_end": 15},
    "ASIA":   {"start": 0,  "end": 8,  "peak_start": 2,  "peak_end": 4},
}

# Liquidity scores per session (0–1)
SESSION_LIQUIDITY = {
    "LONDON_PEAK": 1.00,
    "NY_PEAK":     0.95,
    "LONDON":      0.85,
    "NY":          0.80,
    "OVERLAP":     1.00,   # London + NY overlap (13:00–16:00 UTC)
    "ASIA":        0.55,
    "OFF":         0.25,
}

# MTF weight adjustments per session
SESSION_MTF_WEIGHTS = {
    "LONDON_PEAK": {"H4": 0.35, "H1": 0.35, "M15": 0.30},
    "NY_PEAK":     {"H4": 0.35, "H1": 0.35, "M15": 0.30},
    "LONDON":      {"H4": 0.38, "H1": 0.35, "M15": 0.27},
    "NY":          {"H4": 0.38, "H1": 0.35, "M15": 0.27},
    "OVERLAP":     {"H4": 0.33, "H1": 0.34, "M15": 0.33},
    "ASIA":        {"H4": 0.50, "H1": 0.35, "M15": 0.15},
    "OFF":         {"H4": 0.60, "H1": 0.30, "M15": 0.10},
}


@dataclass
class SessionQualityResult:
    """Session quality assessment result."""
    session:          str        # "LONDON", "NY", "ASIA", "OVERLAP", "OFF"
    session_phase:    str        # "PEAK", "NORMAL", "OFF"
    quality:          str        # "OPTIMAL", "GOOD", "POOR", "AVOID"
    liquidity_score:  float      # 0–1
    utc_hour:         int
    utc_minute:       int
    is_london_open:   bool
    is_ny_open:       bool
    is_overlap:       bool
    is_post_ny:       bool
    is_asia:          bool
    minutes_to_london: float     # Minutes until next London open
    mtf_weights:      Dict[str, float]
    recommendation:   str
    trade_allowed:    bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session":           self.session,
            "session_phase":     self.session_phase,
            "quality":           self.quality,
            "liquidity_score":   round(self.liquidity_score, 2),
            "utc_hour":          self.utc_hour,
            "utc_minute":        self.utc_minute,
            "is_london_open":    self.is_london_open,
            "is_ny_open":        self.is_ny_open,
            "is_overlap":        self.is_overlap,
            "is_post_ny":        self.is_post_ny,
            "is_asia":           self.is_asia,
            "minutes_to_london": round(self.minutes_to_london, 1),
            "mtf_weights":       self.mtf_weights,
            "recommendation":    self.recommendation,
            "trade_allowed":     self.trade_allowed,
        }


class SessionQualityDetector:
    """
    Session quality detection for gold trading.

    Identifies the current trading session, assesses liquidity quality,
    and provides MTF weight adjustments to filter low-liquidity noise.

    London open (07:00 UTC) is the recommended entry window for gold.
    Post-NY close (22:00–07:00 UTC) should be avoided for new entries.
    """

    def __init__(self) -> None:
        self.version = "2.0.0"

    def assess(
        self,
        check_time: Optional[datetime] = None,
    ) -> SessionQualityResult:
        """
        Assess current session quality.

        Args:
            check_time: UTC datetime to assess (defaults to now).

        Returns:
            SessionQualityResult with full session analysis.
        """
        now = check_time or datetime.now(timezone.utc)
        hour   = now.hour
        minute = now.minute

        # ── Session detection ─────────────────────────────────
        in_london  = SESSIONS["LONDON"]["start"] <= hour < SESSIONS["LONDON"]["end"]
        in_ny      = SESSIONS["NY"]["start"]     <= hour < SESSIONS["NY"]["end"]
        in_asia    = SESSIONS["ASIA"]["start"]   <= hour < SESSIONS["ASIA"]["end"]
        is_overlap = in_london and in_ny  # 13:00–16:00 UTC

        is_london_peak = (
            SESSIONS["LONDON"]["peak_start"] <= hour < SESSIONS["LONDON"]["peak_end"]
        )
        is_ny_peak = (
            SESSIONS["NY"]["peak_start"] <= hour < SESSIONS["NY"]["peak_end"]
        )
        is_post_ny = hour >= SESSIONS["NY"]["end"] or hour < SESSIONS["LONDON"]["start"]

        # ── Session name ──────────────────────────────────────
        if is_overlap:
            session = "OVERLAP"
        elif in_london:
            session = "LONDON"
        elif in_ny:
            session = "NY"
        elif in_asia:
            session = "ASIA"
        else:
            session = "OFF"

        # ── Session phase ─────────────────────────────────────
        if is_london_peak or is_ny_peak or is_overlap:
            phase = "PEAK"
        elif in_london or in_ny:
            phase = "NORMAL"
        else:
            phase = "OFF"

        # ── Liquidity score ───────────────────────────────────
        if is_overlap:
            liq_key = "OVERLAP"
        elif is_london_peak:
            liq_key = "LONDON_PEAK"
        elif is_ny_peak:
            liq_key = "NY_PEAK"
        elif in_london:
            liq_key = "LONDON"
        elif in_ny:
            liq_key = "NY"
        elif in_asia:
            liq_key = "ASIA"
        else:
            liq_key = "OFF"

        liquidity = SESSION_LIQUIDITY[liq_key]
        mtf_weights = SESSION_MTF_WEIGHTS[liq_key]

        # ── Quality classification ────────────────────────────
        if liquidity >= 0.90:
            quality = "OPTIMAL"
            trade_allowed = True
        elif liquidity >= 0.75:
            quality = "GOOD"
            trade_allowed = True
        elif liquidity >= 0.50:
            quality = "POOR"
            trade_allowed = True  # Allowed but with caution
        else:
            quality = "AVOID"
            trade_allowed = False

        # ── Minutes to next London open ───────────────────────
        if in_london:
            minutes_to_london = 0.0
        else:
            # Calculate minutes until 07:00 UTC
            london_open_today = now.replace(
                hour=SESSIONS["LONDON"]["start"], minute=0, second=0, microsecond=0
            )
            if now >= london_open_today:
                # Next London open is tomorrow
                from datetime import timedelta
                london_open_next = london_open_today + timedelta(days=1)
            else:
                london_open_next = london_open_today
            minutes_to_london = (london_open_next - now).total_seconds() / 60.0

        # ── Recommendation ────────────────────────────────────
        if quality == "OPTIMAL":
            rec = (
                f"✓ {session} {'peak ' if phase == 'PEAK' else ''}session — "
                f"optimal liquidity ({liquidity:.0%}). "
                f"Best time to enter gold trades."
            )
        elif quality == "GOOD":
            rec = (
                f"✓ {session} session — good liquidity ({liquidity:.0%}). "
                f"Normal position sizing."
            )
        elif quality == "POOR":
            rec = (
                f"⚠ {session} session — reduced liquidity ({liquidity:.0%}). "
                f"Reduce position size by 25–30%. "
                f"London open in {minutes_to_london:.0f} minutes (07:00 UTC)."
            )
        else:
            rec = (
                f"⛔ Off-session / post-NY close — very low liquidity ({liquidity:.0%}). "
                f"Avoid new entries. "
                f"London open in {minutes_to_london:.0f} minutes (07:00 UTC)."
            )

        return SessionQualityResult(
            session=session,
            session_phase=phase,
            quality=quality,
            liquidity_score=liquidity,
            utc_hour=hour,
            utc_minute=minute,
            is_london_open=in_london,
            is_ny_open=in_ny,
            is_overlap=is_overlap,
            is_post_ny=is_post_ny,
            is_asia=in_asia,
            minutes_to_london=minutes_to_london,
            mtf_weights=mtf_weights,
            recommendation=rec,
            trade_allowed=trade_allowed,
        )

    def get_session_schedule(self) -> Dict[str, Any]:
        """Return the full session schedule in UTC."""
        return {
            "sessions": {
                "LONDON": {
                    "open":  "07:00 UTC",
                    "close": "16:00 UTC",
                    "peak":  "07:00–09:00 UTC",
                    "note":  "Primary gold session — highest liquidity",
                },
                "NY": {
                    "open":  "13:00 UTC",
                    "close": "22:00 UTC",
                    "peak":  "13:00–15:00 UTC",
                    "note":  "Second most liquid session for gold",
                },
                "OVERLAP": {
                    "window": "13:00–16:00 UTC",
                    "note":   "London + NY overlap — maximum liquidity",
                },
                "ASIA": {
                    "open":  "00:00 UTC",
                    "close": "08:00 UTC",
                    "note":  "Low liquidity for gold — reduce size",
                },
                "OFF": {
                    "window": "22:00–07:00 UTC",
                    "note":   "Post-NY close — avoid new entries",
                },
            },
            "recommendation": "Enter gold trades at London open (07:00 UTC) for best results.",
            "version": self.version,
        }


# ─────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────

session_quality_detector = SessionQualityDetector()

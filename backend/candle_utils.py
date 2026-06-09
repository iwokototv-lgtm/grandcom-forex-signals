"""
Candle Utilities — V4.0 Gold Signals
=====================================
Provides candle-close confirmation, timestamp validation, and interval
boundary helpers.  Used by gold_server_v4.py to prevent mid-candle signals
that repaint before the bar is fully closed.

Key rule: a 4H candle is only considered "closed" when the current UTC time
is at least 5 minutes past the expected close boundary.  This 5-minute buffer
absorbs minor clock skew and API delivery lag.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

logger = logging.getLogger("candle_utils")

# Interval durations in minutes
_INTERVAL_MINUTES: dict[str, int] = {
    "1m":   1,
    "5m":   5,
    "15m":  15,
    "30m":  30,
    "1h":   60,
    "2h":   120,
    "4h":   240,
    "6h":   360,
    "8h":   480,
    "12h":  720,
    "1day": 1440,
    "1d":   1440,
}

# Buffer added after the expected close time before we treat a candle as closed
CANDLE_CLOSE_BUFFER_SECONDS: int = 300   # 5 minutes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_candle_closed(df: pd.DataFrame, interval: str = "4h") -> bool:
    """
    Return True if the last candle in *df* is fully closed.

    A candle is considered closed when:
      current UTC time > candle_close_time + CANDLE_CLOSE_BUFFER_SECONDS (5 min)

    This prevents mid-candle signal generation and repainting.

    Parameters
    ----------
    df       : DataFrame with a 'datetime' column (or index) containing candle
               open timestamps.  TwelveData returns the candle *open* time.
    interval : Candle interval string, e.g. "4h", "1h", "1day".

    Returns
    -------
    bool — True if the last candle is closed, False if it is still forming.
    """
    try:
        last_ts = _extract_last_timestamp(df)
        if last_ts is None:
            logger.warning("is_candle_closed: could not extract last timestamp — defaulting to False")
            return False

        if not validate_candle_timestamp(last_ts):
            logger.warning(
                f"is_candle_closed: invalid timestamp {last_ts.isoformat()} — defaulting to False"
            )
            return False

        close_time = get_candle_close_time(last_ts, interval)
        now        = datetime.now(timezone.utc)
        cutoff     = close_time + timedelta(seconds=CANDLE_CLOSE_BUFFER_SECONDS)

        closed = now >= cutoff

        logger.info(
            f"is_candle_closed [{interval}]: "
            f"open={last_ts.isoformat()} "
            f"close={close_time.isoformat()} "
            f"cutoff={cutoff.isoformat()} "
            f"now={now.isoformat()} "
            f"→ {'CLOSED ✅' if closed else 'FORMING ⏳'}"
        )
        return closed

    except Exception as exc:
        # Re-raise so the caller's fail-closed handler can catch and reject
        logger.error(f"is_candle_closed failed: {exc}", exc_info=True)
        raise


def get_candle_close_time(timestamp: datetime, interval: str) -> datetime:
    """
    Calculate the UTC close time of a candle that opened at *timestamp*.

    For 4H candles TwelveData aligns open times to 00:00, 04:00, 08:00,
    12:00, 16:00, 20:00 UTC.  We snap the timestamp to the nearest lower
    boundary of the given interval, then add the interval duration.

    Parameters
    ----------
    timestamp : Candle open time (timezone-aware or naive UTC).
    interval  : Interval string, e.g. "4h".

    Returns
    -------
    datetime — Expected close time (UTC, timezone-aware).
    """
    # Ensure UTC-aware
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    interval_key = interval.lower()
    duration_min = _INTERVAL_MINUTES.get(interval_key)

    if duration_min is None:
        # Unknown interval — fall back to treating the timestamp as the open
        # and adding 4 hours (safe default for this service)
        logger.warning(
            f"get_candle_close_time: unknown interval '{interval}', defaulting to 4h"
        )
        duration_min = 240

    # Snap to the nearest lower boundary aligned to the interval
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    elapsed_min = int((timestamp - epoch).total_seconds() // 60)
    boundary_min = (elapsed_min // duration_min) * duration_min
    open_boundary = epoch + timedelta(minutes=boundary_min)

    close_time = open_boundary + timedelta(minutes=duration_min)
    return close_time


def validate_candle_timestamp(timestamp: datetime) -> bool:
    """
    Return True if *timestamp* is a plausible candle open time.

    Validity rules:
      - timestamp must not be in the future (> now + 1 min tolerance)
      - timestamp must not be older than 24 hours

    Parameters
    ----------
    timestamp : Candle open time (timezone-aware or naive UTC).

    Returns
    -------
    bool — True if valid, False otherwise.
    """
    try:
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)

        if timestamp > now + timedelta(minutes=1):
            logger.warning(
                f"validate_candle_timestamp: timestamp {timestamp.isoformat()} "
                f"is in the future (now={now.isoformat()})"
            )
            return False

        if timestamp < now - timedelta(hours=24):
            logger.warning(
                f"validate_candle_timestamp: timestamp {timestamp.isoformat()} "
                f"is older than 24 hours (now={now.isoformat()})"
            )
            return False

        return True

    except Exception as exc:
        logger.error(f"validate_candle_timestamp failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_last_timestamp(df: pd.DataFrame) -> Optional[datetime]:
    """
    Extract the last candle's open timestamp from a DataFrame.

    Tries, in order:
      1. 'datetime' column
      2. 'date' column
      3. DatetimeIndex
    """
    try:
        if "datetime" in df.columns:
            raw = df["datetime"].iloc[-1]
        elif "date" in df.columns:
            raw = df["date"].iloc[-1]
        elif isinstance(df.index, pd.DatetimeIndex):
            raw = df.index[-1]
        else:
            logger.warning("_extract_last_timestamp: no datetime column or DatetimeIndex found")
            return None

        if isinstance(raw, pd.Timestamp):
            ts = raw.to_pydatetime()
        elif isinstance(raw, str):
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        elif isinstance(raw, datetime):
            ts = raw
        else:
            logger.warning(f"_extract_last_timestamp: unexpected type {type(raw)}")
            return None

        # Ensure UTC-aware
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        return ts

    except Exception as exc:
        logger.error(f"_extract_last_timestamp failed: {exc}")
        return None

"""
Data Freshness Guard — V4.0 Gold Signals
==========================================
Validates that OHLCV DataFrames contain recent, well-ordered data before
they are used for signal generation.  Stale or future-dated data can trigger
false signals and must be rejected early in the pipeline.

Rules
-----
  - Data is "fresh" if the API response timestamp is < max_age_seconds old
    (default: 300 s / 5 minutes).  The API response timestamp reflects when
    the feed last delivered data — NOT the candle's open time.  This detects
    a dead feed (no new data for 5+ minutes) without incorrectly rejecting
    recently-closed candles whose open time is hours in the past.
  - Timestamps must be monotonically increasing (no out-of-order candles).
  - No timestamp may be in the future (> now + 1 min tolerance).
  - The last timestamp must be ≤ now.

Typical usage
-------------
    response_ts = datetime.now(timezone.utc)   # captured right after API call
    guard = DataFreshnessGuard()
    if not guard.is_fresh(df, response_timestamp=response_ts):
        logger.warning("Dead feed — skipping signal generation")
        return
    if not guard.validate_timestamps(df):
        logger.warning("Invalid timestamps — skipping signal generation")
        return
"""



from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

logger = logging.getLogger("data_freshness")

# Default maximum age for "fresh" data (seconds)
DEFAULT_MAX_AGE_SECONDS: int = 300   # 5 minutes

# Tolerance for future timestamps (to absorb minor clock skew)
FUTURE_TOLERANCE_SECONDS: int = 60   # 1 minute


class DataFreshnessGuard:
    """
    Validate data freshness and reject stale or malformed OHLCV DataFrames.

    All methods are synchronous and stateless — safe to call from any context.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_fresh(
        self,
        df: pd.DataFrame,
        max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
        response_timestamp: Optional[datetime] = None,
    ) -> bool:
        """
        Return True if the data feed is fresh (< max_age_seconds old).

        Staleness is measured against *response_timestamp* — the moment the
        API call returned — NOT the candle's open time.  This correctly
        detects a dead feed (no new data arriving for 5+ minutes) without
        penalising recently-closed candles whose open time is hours in the
        past.

        Example: a 4H bar that opened at 00:00 UTC and closed at 04:00 UTC.
        At 04:05 UTC the API responds with that bar.  The candle open is
        4 h 5 min old, but the feed age is only 5 seconds — fresh.

        Parameters
        ----------
        df                 : OHLCV DataFrame with a datetime column or index
                             (used only for candle_open_time debug logging).
        max_age_seconds    : Maximum acceptable feed age in seconds (default: 300).
        response_timestamp : UTC datetime captured immediately after the API
                             call returned.  Falls back to now() if omitted
                             (treats the feed as instantaneously fresh — use
                             only when a timestamp cannot be captured).

        Returns
        -------
        bool — True if fresh, False if stale (dead feed).
        """
        try:
            now = datetime.now(timezone.utc)

            # Determine feed age from API response time
            if response_timestamp is not None:
                # Ensure timezone-aware
                rt = response_timestamp
                if rt.tzinfo is None:
                    rt = rt.replace(tzinfo=timezone.utc)
                feed_age = (now - rt).total_seconds()
                feed_age = max(0.0, feed_age)
            else:
                # No response timestamp provided — cannot verify freshness.
                # Fail-closed: reject the signal rather than assume it is fresh.
                logger.warning(
                    "is_fresh: response_timestamp not provided — "
                    "feed age cannot be measured; treating as STALE (fail-closed). "
                    "Pass response_timestamp=datetime.now(timezone.utc) "
                    "immediately after the API call."
                )
                return False

            # Log candle open time alongside feed age for debugging
            candle_open_time: Optional[datetime] = None
            try:
                ts_series = self._extract_timestamp_series(df)
                if ts_series is not None and not ts_series.empty:
                    candle_open_time = self._to_utc_datetime(ts_series.iloc[-1])
            except Exception:
                pass

            if candle_open_time is not None:
                candle_age = (now - candle_open_time).total_seconds()
                logger.info(
                    f"is_fresh: candle_open={candle_open_time.isoformat()} "
                    f"(age={candle_age:.0f}s) | "
                    f"response_ts={response_timestamp.isoformat() if response_timestamp else 'N/A'} "
                    f"(feed_age={feed_age:.0f}s)"
                )

            fresh = feed_age < max_age_seconds
            if not fresh:
                logger.warning(
                    f"is_fresh: FAIL-CLOSED — dead feed detected — "
                    f"feed_age={feed_age:.0f}s exceeds max={max_age_seconds}s — "
                    f"signal rejected "
                    f"(candle_open={candle_open_time.isoformat() if candle_open_time else 'unknown'}, "
                    f"response_ts={response_timestamp.isoformat() if response_timestamp else 'N/A'})"
                )
            else:
                logger.info(
                    f"is_fresh: PASS — feed_age={feed_age:.0f}s < max={max_age_seconds}s — "
                    f"signal allowed "
                    f"(candle_open={candle_open_time.isoformat() if candle_open_time else 'unknown'})"
                )

            return fresh

        except Exception as exc:
            logger.error(f"is_fresh failed: {exc}", exc_info=True)
            return False

    def validate_timestamps(self, df: pd.DataFrame) -> bool:
        """
        Validate all timestamps in *df* for correctness.

        Checks:
          1. Last timestamp ≤ now (not in future beyond tolerance)
          2. Timestamps are monotonically increasing (no out-of-order candles)
          3. No individual timestamp is in the future

        Returns True only if all checks pass.
        """
        try:
            if df is None or df.empty:
                logger.warning("validate_timestamps: empty DataFrame")
                return False

            ts_series = self._extract_timestamp_series(df)
            if ts_series is None or ts_series.empty:
                logger.warning("validate_timestamps: could not extract timestamp series")
                return False

            now = datetime.now(timezone.utc)
            future_cutoff = now + timedelta(seconds=FUTURE_TOLERANCE_SECONDS)

            # Check 1: last timestamp not in future
            last_ts = self._to_utc_datetime(ts_series.iloc[-1])
            if last_ts is not None and last_ts > future_cutoff:
                logger.warning(
                    f"validate_timestamps: last timestamp {last_ts.isoformat()} "
                    f"is in the future (now={now.isoformat()})"
                )
                return False

            # Check 2: monotonically increasing
            if not ts_series.is_monotonic_increasing:
                logger.warning("validate_timestamps: timestamps are not monotonically increasing")
                return False

            # Check 3: no future timestamps anywhere
            if not self.check_future_timestamps(df):
                return False

            logger.debug(
                f"validate_timestamps: OK — {len(ts_series)} candles, "
                f"last={last_ts.isoformat() if last_ts else 'unknown'}"
            )
            return True

        except Exception as exc:
            logger.error(f"validate_timestamps failed: {exc}", exc_info=True)
            return False

    def get_data_age(self, df: pd.DataFrame) -> Optional[float]:
        """
        Return the age of the last candle's timestamp in seconds.

        Returns None if the timestamp cannot be determined.
        """
        try:
            ts_series = self._extract_timestamp_series(df)
            if ts_series is None or ts_series.empty:
                return None

            last_ts = self._to_utc_datetime(ts_series.iloc[-1])
            if last_ts is None:
                return None

            now = datetime.now(timezone.utc)
            age = (now - last_ts).total_seconds()
            return max(0.0, age)   # Never return negative age

        except Exception as exc:
            logger.error(f"get_data_age failed: {exc}")
            return None

    def check_future_timestamps(self, df: pd.DataFrame) -> bool:
        """
        Return True if no timestamps in *df* are in the future.

        Logs a warning for each future timestamp found.
        Any future timestamp is a data integrity issue and should be rejected.
        """
        try:
            ts_series = self._extract_timestamp_series(df)
            if ts_series is None or ts_series.empty:
                return True   # Nothing to check

            now = datetime.now(timezone.utc)
            future_cutoff = now + timedelta(seconds=FUTURE_TOLERANCE_SECONDS)
            all_valid = True

            for idx, raw_ts in ts_series.items():
                ts = self._to_utc_datetime(raw_ts)
                if ts is not None and ts > future_cutoff:
                    logger.warning(
                        f"check_future_timestamps: future timestamp at index {idx}: "
                        f"{ts.isoformat()} (now={now.isoformat()})"
                    )
                    all_valid = False

            return all_valid

        except Exception as exc:
            logger.error(f"check_future_timestamps failed: {exc}")
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_timestamp_series(self, df: pd.DataFrame) -> Optional[pd.Series]:
        """Extract the timestamp column as a pandas Series."""
        try:
            if df is None or df.empty:
                return None

            if "datetime" in df.columns:
                return df["datetime"]
            elif "date" in df.columns:
                return df["date"]
            elif isinstance(df.index, pd.DatetimeIndex):
                return df.index.to_series()
            else:
                logger.warning("_extract_timestamp_series: no datetime column or DatetimeIndex")
                return None

        except Exception as exc:
            logger.error(f"_extract_timestamp_series failed: {exc}")
            return None

    @staticmethod
    def _to_utc_datetime(raw) -> Optional[datetime]:
        """Convert a raw timestamp value to a UTC-aware datetime."""
        try:
            if isinstance(raw, pd.Timestamp):
                ts = raw.to_pydatetime()
            elif isinstance(raw, str):
                ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            elif isinstance(raw, datetime):
                ts = raw
            else:
                return None

            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts

        except Exception:
            return None


# Module-level singleton
_freshness_guard = DataFreshnessGuard()


def get_freshness_guard() -> DataFreshnessGuard:
    """Return the module-level DataFreshnessGuard singleton."""
    return _freshness_guard

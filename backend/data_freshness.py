"""
Data Freshness Guard — V4.0 Gold Signals
==========================================
Validates that OHLCV DataFrames contain recent, well-ordered data before
they are used for signal generation.  Stale or future-dated data can trigger
false signals and must be rejected early in the pipeline.

Rules
-----
  - Data is "fresh" if the last candle timestamp is < max_age_seconds old
    (default: 300 s / 5 minutes).
  - Timestamps must be monotonically increasing (no out-of-order candles).
  - No timestamp may be in the future (> now + 1 min tolerance).
  - The last timestamp must be ≤ now.

Typical usage
-------------
    guard = DataFreshnessGuard()
    if not guard.is_fresh(df):
        logger.warning("Stale data — skipping signal generation")
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
    ) -> bool:
        """
        Return True if the last candle in *df* is fresh (< max_age_seconds old).

        "Age" is measured from the candle's open timestamp to now.  For a
        4H candle that opened at 08:00 UTC and it is currently 08:03 UTC,
        the age is 3 minutes — well within the 5-minute default.

        Parameters
        ----------
        df              : OHLCV DataFrame with a datetime column or index.
        max_age_seconds : Maximum acceptable age in seconds (default: 300).

        Returns
        -------
        bool — True if fresh, False if stale.
        """
        try:
            age = self.get_data_age(df)
            if age is None:
                logger.warning("is_fresh: could not determine data age — treating as stale")
                return False

            fresh = age < max_age_seconds
            if not fresh:
                logger.warning(
                    f"is_fresh: data is stale — age={age:.0f}s > max={max_age_seconds}s"
                )
            else:
                logger.debug(f"is_fresh: data age={age:.0f}s — OK")

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

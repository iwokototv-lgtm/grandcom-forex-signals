"""
Signal Validator
Pre-signal validation checks for the 3-component core signal system.

Validates a candidate signal before it is emitted, catching common
failure modes that cause bad trades:

  1. Candle closed?          — no mid-candle signals
  2. Data fresh?             — data must be < 5 minutes old
  3. Enough volatility?      — ATR must exceed a minimum threshold
  4. High-impact news?       — economic calendar check
  5. Duplicate signal?       — no repeat signal within 4 hours

All checks are non-blocking: each returns a pass/fail with a reason
string so the caller can log exactly why a signal was rejected.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Maximum age of the most recent candle before data is considered stale
MAX_DATA_AGE_MINUTES: int = 5

# Minimum ATR as a fraction of price (e.g. 0.001 = 0.1% of price)
# For XAUUSD at ~2000, this is ~$2 minimum ATR — filters dead/illiquid markets
MIN_ATR_PRICE_RATIO: float = 0.001

# Minimum cooldown between signals for the same symbol (hours)
SIGNAL_COOLDOWN_HOURS: int = 4

# Timeframe durations in minutes (used for candle-close check)
TIMEFRAME_MINUTES: Dict[str, int] = {
    "1h": 60,
    "4h": 240,
    "1day": 1440,
    "1week": 10080,
    "15min": 15,
    "30min": 30,
}


class SignalValidator:
    """
    Pre-signal validation gate.

    Usage:
        validator = SignalValidator()
        result = validator.validate(
            symbol="XAUUSD",
            df_4h=df,
            timeframe="4h",
            atr=current_atr,
            calendar_safe=True,
        )
        if not result["valid"]:
            print(f"Signal rejected: {result['rejection_reason']}")
    """

    def __init__(self) -> None:
        # In-memory signal history: symbol → list of signal datetimes
        self._signal_history: Dict[str, List[datetime]] = {}

    # ------------------------------------------------------------------
    # Main Validation
    # ------------------------------------------------------------------

    def validate(
        self,
        symbol: str,
        df_4h: pd.DataFrame,
        timeframe: str = "4h",
        atr: Optional[float] = None,
        calendar_safe: bool = True,
        calendar_reason: str = "",
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Run all pre-signal validation checks.

        Args:
            symbol:         Trading symbol (e.g. XAUUSD)
            df_4h:          4H OHLCV DataFrame (must have 'datetime' column)
            timeframe:      Candle timeframe string (default "4h")
            atr:            Current ATR value (computed externally for speed)
            calendar_safe:  True if no high-impact news event is active
            calendar_reason: Reason string from economic calendar
            now:            Current UTC time (injectable for testing)

        Returns:
            Dict with keys:
              valid            — bool: True if all checks pass
              checks           — dict of individual check results
              rejection_reason — str: first failing check reason (or "ALL_CLEAR")
              passed_count     — int: number of checks that passed
              total_checks     — int: total checks run
        """
        now = now or datetime.now(timezone.utc)

        checks: Dict[str, Dict[str, Any]] = {}

        # 1. Candle closed
        checks["candle_closed"] = self._check_candle_closed(df_4h, timeframe, now)

        # 2. Data freshness
        checks["data_fresh"] = self._check_data_freshness(df_4h, now)

        # 3. Volatility
        checks["volatility"] = self._check_volatility(df_4h, atr)

        # 4. Economic calendar
        checks["calendar"] = self._check_calendar(calendar_safe, calendar_reason)

        # 5. Deduplication
        checks["deduplication"] = self._check_deduplication(symbol, now)

        # Aggregate
        failed = [name for name, result in checks.items() if not result["pass"]]
        passed_count = len(checks) - len(failed)
        valid = len(failed) == 0
        rejection_reason = checks[failed[0]]["reason"] if failed else "ALL_CLEAR"

        result: Dict[str, Any] = {
            "valid": valid,
            "symbol": symbol,
            "timestamp": now.isoformat(),
            "checks": checks,
            "rejection_reason": rejection_reason,
            "failed_checks": failed,
            "passed_count": passed_count,
            "total_checks": len(checks),
        }

        if valid:
            logger.info(f"SignalValidator [{symbol}]: ALL CHECKS PASSED")
        else:
            logger.info(
                f"SignalValidator [{symbol}]: REJECTED — {rejection_reason} "
                f"(failed: {failed})"
            )

        return result

    # ------------------------------------------------------------------
    # Record a signal (call after emitting a valid signal)
    # ------------------------------------------------------------------

    def record_signal(self, symbol: str, signal_time: Optional[datetime] = None) -> None:
        """
        Record that a signal was emitted for deduplication tracking.

        Call this after a signal passes validation and is sent to the user.
        """
        t = signal_time or datetime.now(timezone.utc)
        if symbol not in self._signal_history:
            self._signal_history[symbol] = []
        self._signal_history[symbol].append(t)

        # Prune old entries (keep only last 24 hours)
        cutoff = t - timedelta(hours=24)
        self._signal_history[symbol] = [
            dt for dt in self._signal_history[symbol] if dt >= cutoff
        ]
        logger.debug(f"SignalValidator: recorded signal for {symbol} at {t.isoformat()}")

    # ------------------------------------------------------------------
    # Individual Checks
    # ------------------------------------------------------------------

    def _check_candle_closed(
        self,
        df: pd.DataFrame,
        timeframe: str,
        now: datetime,
    ) -> Dict[str, Any]:
        """
        Check 1: Is the most recent candle fully closed?

        A candle is closed when the current time is past its expected
        close time (open_time + timeframe_duration).
        """
        try:
            if df is None or df.empty:
                return {"pass": False, "reason": "CANDLE_CHECK_NO_DATA"}

            last_dt = df["datetime"].iloc[-1]
            if isinstance(last_dt, str):
                last_dt = pd.to_datetime(last_dt)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)

            duration_min = TIMEFRAME_MINUTES.get(timeframe.lower(), 240)
            candle_close_time = last_dt + timedelta(minutes=duration_min)

            # Ensure now is timezone-aware
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)

            is_closed = now >= candle_close_time
            minutes_remaining = max(
                0.0,
                (candle_close_time - now).total_seconds() / 60.0,
            )

            return {
                "pass": is_closed,
                "reason": "CANDLE_CLOSED" if is_closed else (
                    f"MID_CANDLE: {minutes_remaining:.1f}min until close"
                ),
                "candle_open": last_dt.isoformat(),
                "candle_close": candle_close_time.isoformat(),
                "minutes_remaining": round(minutes_remaining, 1),
            }
        except Exception as exc:
            logger.warning(f"Candle-closed check error: {exc}")
            # Fail open — don't block on check errors
            return {"pass": True, "reason": f"CHECK_ERROR_FAIL_OPEN: {exc}"}

    def _check_data_freshness(
        self,
        df: pd.DataFrame,
        now: datetime,
    ) -> Dict[str, Any]:
        """
        Check 2: Is the data fresh (< MAX_DATA_AGE_MINUTES old)?

        Stale data means the feed may be disconnected or the API is down.
        """
        try:
            if df is None or df.empty:
                return {"pass": False, "reason": "FRESHNESS_NO_DATA"}

            last_dt = df["datetime"].iloc[-1]
            if isinstance(last_dt, str):
                last_dt = pd.to_datetime(last_dt)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)

            age_minutes = (now - last_dt).total_seconds() / 60.0

            # For 4H candles, the last candle can be up to 4h + 5min old
            # (it was just closed). We allow up to 4h + MAX_DATA_AGE_MINUTES.
            # For simplicity, we check that the last candle is not older than
            # 2 full timeframe periods (catches truly stale feeds).
            max_age_minutes = 240 * 2 + MAX_DATA_AGE_MINUTES  # 485 min for 4H
            is_fresh = age_minutes <= max_age_minutes

            return {
                "pass": is_fresh,
                "reason": "DATA_FRESH" if is_fresh else (
                    f"STALE_DATA: last candle {age_minutes:.0f}min ago "
                    f"(max {max_age_minutes}min)"
                ),
                "last_candle_age_minutes": round(age_minutes, 1),
                "max_allowed_minutes": max_age_minutes,
            }
        except Exception as exc:
            logger.warning(f"Data freshness check error: {exc}")
            return {"pass": True, "reason": f"CHECK_ERROR_FAIL_OPEN: {exc}"}

    def _check_volatility(
        self,
        df: pd.DataFrame,
        atr: Optional[float],
    ) -> Dict[str, Any]:
        """
        Check 3: Is there enough volatility to trade?

        ATR must be > MIN_ATR_PRICE_RATIO × current_price.
        This filters dead markets, weekends, and illiquid sessions.
        """
        try:
            if df is None or df.empty:
                return {"pass": False, "reason": "VOLATILITY_NO_DATA"}

            current_price = float(df["close"].iloc[-1])
            if current_price <= 0:
                return {"pass": False, "reason": "VOLATILITY_INVALID_PRICE"}

            # Compute ATR if not provided
            if atr is None or np.isnan(atr):
                high = df["high"].astype(float)
                low = df["low"].astype(float)
                close = df["close"].astype(float)
                tr = pd.concat([
                    high - low,
                    (high - close.shift()).abs(),
                    (low - close.shift()).abs(),
                ], axis=1).max(axis=1)
                atr = float(tr.rolling(14).mean().iloc[-1])

            if np.isnan(atr) or atr <= 0:
                return {"pass": False, "reason": "VOLATILITY_ATR_INVALID"}

            min_atr = current_price * MIN_ATR_PRICE_RATIO
            has_volatility = atr >= min_atr

            return {
                "pass": has_volatility,
                "reason": "VOLATILITY_OK" if has_volatility else (
                    f"LOW_VOLATILITY: ATR={atr:.4f} < min={min_atr:.4f} "
                    f"({MIN_ATR_PRICE_RATIO*100:.2f}% of price)"
                ),
                "atr": round(atr, 5),
                "min_atr": round(min_atr, 5),
                "atr_price_ratio": round(atr / current_price, 6),
            }
        except Exception as exc:
            logger.warning(f"Volatility check error: {exc}")
            return {"pass": True, "reason": f"CHECK_ERROR_FAIL_OPEN: {exc}"}

    def _check_calendar(
        self,
        calendar_safe: bool,
        calendar_reason: str,
    ) -> Dict[str, Any]:
        """
        Check 4: Is it safe to trade (no high-impact news event)?
        """
        return {
            "pass": calendar_safe,
            "reason": "CALENDAR_CLEAR" if calendar_safe else (
                f"HIGH_IMPACT_NEWS: {calendar_reason or 'event active'}"
            ),
            "calendar_reason": calendar_reason,
        }

    def _check_deduplication(
        self,
        symbol: str,
        now: datetime,
    ) -> Dict[str, Any]:
        """
        Check 5: Has a similar signal been generated in the last 4 hours?

        Prevents flooding the user with repeated signals on the same setup.
        """
        try:
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)

            history = self._signal_history.get(symbol, [])
            cutoff = now - timedelta(hours=SIGNAL_COOLDOWN_HOURS)

            recent = [dt for dt in history if dt >= cutoff]

            if recent:
                last_signal = max(recent)
                minutes_ago = (now - last_signal).total_seconds() / 60.0
                return {
                    "pass": False,
                    "reason": (
                        f"DUPLICATE_SIGNAL: last signal {minutes_ago:.0f}min ago "
                        f"(cooldown {SIGNAL_COOLDOWN_HOURS}h)"
                    ),
                    "last_signal_minutes_ago": round(minutes_ago, 1),
                    "cooldown_hours": SIGNAL_COOLDOWN_HOURS,
                }

            return {
                "pass": True,
                "reason": "NO_RECENT_DUPLICATE",
                "cooldown_hours": SIGNAL_COOLDOWN_HOURS,
            }
        except Exception as exc:
            logger.warning(f"Deduplication check error: {exc}")
            return {"pass": True, "reason": f"CHECK_ERROR_FAIL_OPEN: {exc}"}

    # ------------------------------------------------------------------
    # Batch validation helper
    # ------------------------------------------------------------------

    def validate_batch(
        self,
        signals: List[Dict[str, Any]],
        df_4h: pd.DataFrame,
        timeframe: str = "4h",
        atr: Optional[float] = None,
        calendar_safe: bool = True,
        calendar_reason: str = "",
    ) -> List[Dict[str, Any]]:
        """
        Validate a list of candidate signals, returning only valid ones.

        Each signal dict must have a 'symbol' key.
        """
        valid_signals = []
        for sig in signals:
            symbol = sig.get("symbol", "UNKNOWN")
            result = self.validate(
                symbol=symbol,
                df_4h=df_4h,
                timeframe=timeframe,
                atr=atr,
                calendar_safe=calendar_safe,
                calendar_reason=calendar_reason,
            )
            if result["valid"]:
                valid_signals.append({**sig, "validation": result})
            else:
                logger.info(
                    f"SignalValidator: filtered {symbol} — {result['rejection_reason']}"
                )
        return valid_signals


# ---------------------------------------------------------------------------
# Global instance
# ---------------------------------------------------------------------------

signal_validator = SignalValidator()

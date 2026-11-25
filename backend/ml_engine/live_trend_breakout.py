"""
Live helpers for the trend_breakout strategy (see trend_breakout.py).

Kept free of network and database code so they can be unit tested.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

from ml_engine.trend_breakout import trend_breakout_series

CANDLE_HOURS = 1
# Matches the backtest: a trade that has not hit a target or stop after this
# many 1H candles is treated as finished.
TIMEOUT_CANDLES = 60


def _to_utc(ts) -> datetime:
    t = pd.to_datetime(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return t.to_pydatetime().astimezone(timezone.utc)


def closed_candles(df: pd.DataFrame, now: Optional[datetime] = None) -> pd.DataFrame:
    """Drop the still-forming last candle. df is oldest-first with a 'datetime' column."""
    now = now or datetime.now(timezone.utc)
    if df is None or len(df) == 0:
        return df
    last_open = _to_utc(df["datetime"].iloc[-1])
    if last_open + timedelta(hours=CANDLE_HOURS) > now:
        return df.iloc[:-1].reset_index(drop=True)
    return df.reset_index(drop=True)


def latest_signal(df_closed: pd.DataFrame, lookback: int = 55, ema_period: int = 100) -> str:
    """BUY / SELL / NEUTRAL decided on the last closed candle."""
    if df_closed is None or len(df_closed) < max(lookback, ema_period) + 2:
        return "NEUTRAL"
    return str(trend_breakout_series(df_closed, lookback, ema_period).iloc[-1])


def trade_still_open(
    signal: str,
    entry: float,
    sl: float,
    tp3: float,
    entry_candle_time,
    df_closed: pd.DataFrame,
    be_r: float = 1.0,
) -> bool:
    """
    Walk the closed candles after the entry candle and report whether the trade
    is still running. A trade ends at its stop (moved to entry once price has
    moved be_r times the initial risk), at TP3, or after TIMEOUT_CANDLES.
    """
    t0 = _to_utc(entry_candle_time)
    times = df_closed["datetime"].map(_to_utc)
    after = df_closed[times > t0]
    if len(after) >= TIMEOUT_CANDLES:
        return False
    risk = abs(entry - sl)
    stop = sl
    buy = signal == "BUY"
    for _, c in after.iterrows():
        hi, lo = float(c["high"]), float(c["low"])
        if buy:
            if lo <= stop or hi >= tp3:
                return False
            if hi >= entry + be_r * risk:
                stop = entry
        else:
            if hi >= stop or lo <= tp3:
                return False
            if lo <= entry - be_r * risk:
                stop = entry
    return True

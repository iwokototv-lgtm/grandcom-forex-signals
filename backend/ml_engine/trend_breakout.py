"""
Trend-following breakout signal (research candidate, backtest only).

Rules, deliberately few:
  BUY  when the 4H close breaks above the highest high of the previous
       `lookback` candles AND the close is above the EMA(`ema_period`).
  SELL when the 4H close breaks below the lowest low of the previous
       `lookback` candles AND the close is below the EMA.

The channel is shifted by one candle, so each decision uses only candles that
had closed before the current one (no lookahead). Exits (ATR stop and ATR
targets) are applied by the caller.
"""
from __future__ import annotations

import pandas as pd


def trend_breakout_series(df: pd.DataFrame, lookback: int = 20, ema_period: int = 100) -> pd.Series:
    """Return a Series of "BUY" / "SELL" / "NEUTRAL" aligned to df's index."""
    close = df["close"].astype(float)
    channel_high = df["high"].astype(float).rolling(lookback).max().shift(1)
    channel_low = df["low"].astype(float).rolling(lookback).min().shift(1)
    ema = close.ewm(span=ema_period, adjust=False).mean()

    buy = (close > channel_high) & (close > ema)
    sell = (close < channel_low) & (close < ema)

    out = pd.Series("NEUTRAL", index=df.index, dtype=object)
    out[buy] = "BUY"
    out[sell] = "SELL"
    return out

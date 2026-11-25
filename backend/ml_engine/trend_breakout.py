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


def trend_breakout_series(
    df: pd.DataFrame,
    lookback: int = 20,
    ema_period: int = 100,
    buffer_atr: float = 0.0,
    atr_period: int = 14,
) -> pd.Series:
    """Return a Series of "BUY" / "SELL" / "NEUTRAL" aligned to df's index.

    buffer_atr > 0 requires the close to clear the channel by buffer_atr * ATR
    (filters tiny pokes past the level). 0 keeps the original rule.
    """
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    channel_high = high.rolling(lookback).max().shift(1)
    channel_low = low.rolling(lookback).min().shift(1)
    ema = close.ewm(span=ema_period, adjust=False).mean()

    if buffer_atr > 0:
        prev_close = close.shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
        ).max(axis=1)
        atr = tr.rolling(atr_period).mean()
        channel_high = channel_high + buffer_atr * atr
        channel_low = channel_low - buffer_atr * atr

    buy = (close > channel_high) & (close > ema)
    sell = (close < channel_low) & (close < ema)

    out = pd.Series("NEUTRAL", index=df.index, dtype=object)
    out[buy] = "BUY"
    out[sell] = "SELL"
    return out

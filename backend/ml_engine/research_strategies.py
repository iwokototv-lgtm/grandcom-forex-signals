"""
Research candidates (backtest only). Each returns a Series of "BUY" / "SELL" /
"NEUTRAL" aligned to df's index. Every value uses only candles closed at or
before that row (no lookahead). Exits are applied by the caller.

ma_adx:   EMA(fast) crosses EMA(slow) on this candle AND ADX(14) > adx_min.
pullback: in an uptrend (EMA50 > EMA200, close > EMA200) the previous close was
          below EMA50 and this close is back above it (mirror for downtrends).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high, low, close = (df[c].astype(float) for c in ("high", "low", "close"))
    up, down = high.diff(), -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    a = 1.0 / n
    atr = tr.ewm(alpha=a, adjust=False).mean()
    pdi = 100 * plus_dm.ewm(alpha=a, adjust=False).mean() / atr
    mdi = 100 * minus_dm.ewm(alpha=a, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=a, adjust=False).mean()


def ma_adx_series(df: pd.DataFrame, fast: int = 20, slow: int = 50, adx_min: float = 25.0) -> pd.Series:
    close = df["close"].astype(float)
    ef = close.ewm(span=fast, adjust=False).mean()
    es = close.ewm(span=slow, adjust=False).mean()
    adx = _adx(df)
    cross_up = (ef > es) & (ef.shift(1) <= es.shift(1))
    cross_dn = (ef < es) & (ef.shift(1) >= es.shift(1))
    out = pd.Series("NEUTRAL", index=df.index, dtype=object)
    out[cross_up & (adx > adx_min)] = "BUY"
    out[cross_dn & (adx > adx_min)] = "SELL"
    return out


def pullback_series(df: pd.DataFrame, mid: int = 50, slow: int = 200) -> pd.Series:
    close = df["close"].astype(float)
    em = close.ewm(span=mid, adjust=False).mean()
    es = close.ewm(span=slow, adjust=False).mean()
    up = (em > es) & (close > es)
    dn = (em < es) & (close < es)
    buy = up & (close.shift(1) < em.shift(1)) & (close > em)
    sell = dn & (close.shift(1) > em.shift(1)) & (close < em)
    out = pd.Series("NEUTRAL", index=df.index, dtype=object)
    out[buy] = "BUY"
    out[sell] = "SELL"
    return out

"""
Mean Reversion Core Engine
==========================
Trades overbought/oversold conditions by detecting when price has moved
FAR from its moving average and momentum is exhausted.

Core logic:
  BUY  when: price < EMA20 - (2 × ATR)  AND  RSI < 30  (oversold)
  SELL when: price > EMA20 + (2 × ATR)  AND  RSI > 70  (overbought)

Confidence scoring:
  Base  : 60%  (mean reversion is less certain than trend following)
  +10%  : price is > 3 ATR away from EMA20 (extreme condition)
  +5%   : RSI in extreme zone (< 20 or > 80)
  -10%  : daily trend is against the signal (counter-trend risk)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class MeanReversionCore:
    """
    Mean Reversion Signal Engine.

    Generates BUY/SELL signals when price is statistically far from its
    EMA20 baseline AND RSI confirms momentum exhaustion.  An optional
    daily-trend filter prevents trading against the dominant trend.

    Parameters
    ----------
    atr_period : int
        Period for ATR calculation (default 14).
    ema_period : int
        Period for the baseline EMA (default 20).
    rsi_period : int
        Period for RSI calculation (default 14).
    atr_entry_mult : float
        Minimum ATR distance from EMA20 required to enter (default 2.0).
    atr_extreme_mult : float
        ATR distance that qualifies as an extreme condition (default 3.0).
    rsi_oversold : float
        RSI threshold for oversold (default 30).
    rsi_overbought : float
        RSI threshold for overbought (default 70).
    rsi_extreme_oversold : float
        RSI threshold for extreme oversold bonus (default 20).
    rsi_extreme_overbought : float
        RSI threshold for extreme overbought bonus (default 80).
    min_atr_pct : float
        Minimum ATR as a percentage of price to avoid dead markets (default 0.001).
    """

    def __init__(
        self,
        atr_period: int = 14,
        ema_period: int = 20,
        rsi_period: int = 14,
        atr_entry_mult: float = 2.0,
        atr_extreme_mult: float = 3.0,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        rsi_extreme_oversold: float = 20.0,
        rsi_extreme_overbought: float = 80.0,
        min_atr_pct: float = 0.001,
    ) -> None:
        self.atr_period = atr_period
        self.ema_period = ema_period
        self.rsi_period = rsi_period
        self.atr_entry_mult = atr_entry_mult
        self.atr_extreme_mult = atr_extreme_mult
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.rsi_extreme_oversold = rsi_extreme_oversold
        self.rsi_extreme_overbought = rsi_extreme_overbought
        self.min_atr_pct = min_atr_pct
        self.version = "1.0.0"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        df: pd.DataFrame,
        symbol: str,
        df_daily: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Any]:
        """
        Run mean reversion analysis on *df* (primary timeframe).

        Parameters
        ----------
        df       : OHLCV DataFrame for the primary timeframe (e.g. 4H).
        symbol   : Trading symbol string (e.g. "XAUUSD").
        df_daily : Optional daily OHLCV DataFrame used for the trend filter.
                   When provided, signals that oppose the daily trend receive
                   a -10% confidence penalty.

        Returns
        -------
        Dict with keys: vote, confidence, signal_details, valid, …
        """
        try:
            min_rows = max(self.ema_period, self.rsi_period, self.atr_period) + 10
            if df is None or len(df) < min_rows:
                return self._neutral("INSUFFICIENT_DATA")

            df = df.copy()
            close = df["close"].astype(float)
            high  = df["high"].astype(float)
            low   = df["low"].astype(float)

            # ── Indicators ────────────────────────────────────────────
            ema20 = float(close.ewm(span=self.ema_period, adjust=False).mean().iloc[-1])
            atr   = self._calc_atr(high, low, close)
            rsi   = self._calc_rsi(close)

            if np.isnan(atr) or atr <= 0 or np.isnan(rsi):
                return self._neutral("INDICATOR_NAN")

            current_price = float(close.iloc[-1])

            # ── Volatility filter ─────────────────────────────────────
            # Avoid dead / illiquid markets where ATR is negligible
            min_atr = current_price * self.min_atr_pct
            if atr < min_atr:
                return self._neutral(
                    f"LOW_VOLATILITY: ATR={atr:.5f} < min={min_atr:.5f}"
                )

            # ── Entry conditions ──────────────────────────────────────
            lower_band = ema20 - self.atr_entry_mult * atr
            upper_band = ema20 + self.atr_entry_mult * atr

            oversold   = current_price < lower_band and rsi < self.rsi_oversold
            overbought = current_price > upper_band and rsi > self.rsi_overbought

            if not oversold and not overbought:
                return self._neutral(
                    f"NO_EXTREME: price={current_price:.5f} "
                    f"lower={lower_band:.5f} upper={upper_band:.5f} RSI={rsi:.1f}"
                )

            vote = "BUY" if oversold else "SELL"

            # ── Confidence scoring ────────────────────────────────────
            confidence = 0.60  # base

            # +10% if price is > 3 ATR away from EMA20 (extreme)
            distance_atr = abs(current_price - ema20) / atr
            if distance_atr > self.atr_extreme_mult:
                confidence += 0.10

            # +5% if RSI is in extreme zone (< 20 or > 80)
            if rsi < self.rsi_extreme_oversold or rsi > self.rsi_extreme_overbought:
                confidence += 0.05

            # Daily trend filter: -10% if daily trend opposes the signal
            daily_trend = self._daily_trend(df_daily)
            counter_trend = (
                (vote == "BUY"  and daily_trend == "BEARISH") or
                (vote == "SELL" and daily_trend == "BULLISH")
            )
            if counter_trend:
                confidence -= 0.10

            confidence = round(max(0.0, min(1.0, confidence)), 4)

            result = {
                "vote":           vote,
                "confidence":     confidence,
                "valid":          True,
                "symbol":         symbol,
                # Indicator values
                "current_price":  round(current_price, 5),
                "ema20":          round(ema20, 5),
                "atr":            round(atr, 5),
                "rsi":            round(rsi, 2),
                "lower_band":     round(lower_band, 5),
                "upper_band":     round(upper_band, 5),
                "distance_atr":   round(distance_atr, 3),
                # Condition flags
                "oversold":       oversold,
                "overbought":     overbought,
                "extreme_price":  distance_atr > self.atr_extreme_mult,
                "extreme_rsi":    rsi < self.rsi_extreme_oversold or rsi > self.rsi_extreme_overbought,
                "daily_trend":    daily_trend,
                "counter_trend":  counter_trend,
            }

            logger.debug(
                f"MeanReversionCore [{symbol}]: vote={vote} "
                f"conf={confidence:.2f} RSI={rsi:.1f} dist={distance_atr:.2f}ATR "
                f"daily={daily_trend} counter={counter_trend}"
            )
            return result

        except Exception as exc:
            logger.error(f"MeanReversionCore error [{symbol}]: {exc}", exc_info=True)
            return self._neutral(f"EXCEPTION: {exc}")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _calc_atr(
        self,
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
    ) -> float:
        """Compute the most recent ATR value (simple rolling mean of TR)."""
        tr = pd.concat(
            [
                high - low,
                (high - close.shift()).abs(),
                (low  - close.shift()).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr_series = tr.rolling(self.atr_period).mean()
        val = float(atr_series.iloc[-1])
        return val if not np.isnan(val) else float("nan")

    def _calc_rsi(self, close: pd.Series) -> float:
        """Compute the most recent RSI value."""
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(self.rsi_period).mean()
        loss  = (-delta.clip(upper=0)).rolling(self.rsi_period).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi_series = 100 - (100 / (1 + rs))
        val = float(rsi_series.iloc[-1])
        return val if not np.isnan(val) else float("nan")

    def _daily_trend(self, df_daily: Optional[pd.DataFrame]) -> str:
        """
        Determine the daily trend direction using EMA20 vs EMA50.

        Returns "BULLISH", "BEARISH", or "NEUTRAL".
        """
        if df_daily is None or len(df_daily) < 55:
            return "NEUTRAL"
        try:
            close  = df_daily["close"].astype(float)
            ema20d = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
            ema50d = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
            if ema20d > ema50d:
                return "BULLISH"
            elif ema20d < ema50d:
                return "BEARISH"
            return "NEUTRAL"
        except Exception:
            return "NEUTRAL"

    @staticmethod
    def _neutral(reason: str) -> Dict[str, Any]:
        return {
            "vote":       "NEUTRAL",
            "confidence": 0.0,
            "valid":      True,
            "reason":     reason,
        }


# Module-level singleton
mean_reversion_core = MeanReversionCore()

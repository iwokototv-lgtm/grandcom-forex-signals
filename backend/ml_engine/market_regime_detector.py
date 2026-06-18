"""
Market Regime Detector (v3.4)
==============================
Detects the current market condition and returns adaptive engine weights.

Conditions:
  - TRENDING   : Trend=60%, MR=10%, S/R=30%
  - RANGING    : Trend=20%, MR=60%, S/R=20%
  - HIGH_VOL   : Trend=50%, MR=20%, S/R=30%
  - LOW_VOL    : Trend=30%, MR=40%, S/R=30%

Detection logic:
  - ATR ratio  : current ATR vs 20-period average ATR
  - Trend strength : ADX-like directional movement index
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Default weights used when regime detection fails
_DEFAULT_WEIGHTS: Dict[str, float] = {
    "trend": 0.35,
    "mean_reversion": 0.25,
    "support_resistance": 0.40,
}


class MarketRegimeDetector:
    """
    Detects current market condition and adapts engine weights.

    Conditions:
      - TRENDING   : strong directional move + above-average volatility
      - RANGING    : weak trend + below-average volatility
      - HIGH_VOL   : very high volatility regardless of trend
      - LOW_VOL    : very low volatility (default fallback)

    Weights returned:
      - trend              : weight for Trend Confirmation engine
      - mean_reversion     : weight for Mean Reversion engine
      - support_resistance : weight for S/R engine
    """

    # Regime weight presets
    _REGIME_WEIGHTS: Dict[str, Dict[str, float]] = {
        "TRENDING": {
            "trend": 0.60,
            "mean_reversion": 0.10,
            "support_resistance": 0.30,
        },
        "RANGING": {
            "trend": 0.20,
            "mean_reversion": 0.60,
            "support_resistance": 0.20,
        },
        "HIGH_VOL": {
            "trend": 0.50,
            "mean_reversion": 0.20,
            "support_resistance": 0.30,
        },
        "LOW_VOL": {
            "trend": 0.30,
            "mean_reversion": 0.40,
            "support_resistance": 0.30,
        },
    }

    def __init__(self, atr_period: int = 14, atr_avg_period: int = 20) -> None:
        self.atr_period = atr_period
        self.atr_avg_period = atr_avg_period
        self.version = "1.0.0"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def detect(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Detect market regime from OHLCV data.

        Args:
            df: OHLCV DataFrame (primary timeframe, e.g. 4H).

        Returns:
            {
                "regime":         "TRENDING" | "RANGING" | "HIGH_VOL" | "LOW_VOL",
                "weights": {
                    "trend":              float,
                    "mean_reversion":     float,
                    "support_resistance": float,
                },
                "atr_ratio":      float,
                "trend_strength": float,
                "valid":          bool,
            }
        """
        try:
            if df is None or len(df) < max(self.atr_period, self.atr_avg_period) + 5:
                return self._fallback("INSUFFICIENT_DATA")

            atr_current = self._calculate_atr(df, self.atr_period)
            # Use the last atr_avg_period candles to compute the baseline ATR
            atr_baseline = self._calculate_atr(df.iloc[-self.atr_avg_period :], self.atr_period)

            if atr_baseline <= 0:
                return self._fallback("ATR_BASELINE_ZERO")

            atr_ratio = atr_current / atr_baseline
            trend_strength = self._calculate_trend_strength(df)

            # Regime classification
            if trend_strength > 0.6 and atr_ratio > 1.1:
                regime = "TRENDING"
            elif trend_strength < 0.4 and atr_ratio < 0.9:
                regime = "RANGING"
            elif atr_ratio > 1.3:
                regime = "HIGH_VOL"
            else:
                regime = "LOW_VOL"

            weights = self._REGIME_WEIGHTS[regime]

            logger.info(
                f"MarketRegimeDetector: regime={regime} "
                f"atr_ratio={atr_ratio:.3f} trend_strength={trend_strength:.3f}"
            )

            return {
                "regime": regime,
                "weights": weights,
                "atr_ratio": round(atr_ratio, 3),
                "trend_strength": round(trend_strength, 3),
                "valid": True,
            }

        except Exception as exc:
            logger.error(f"MarketRegimeDetector.detect error: {exc}", exc_info=True)
            return self._fallback(f"EXCEPTION: {exc}")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _calculate_atr(self, df: pd.DataFrame, period: int) -> float:
        """Compute the most recent ATR value using a simple rolling mean of TR."""
        try:
            high = df["high"].astype(float)
            low = df["low"].astype(float)
            close = df["close"].astype(float)

            tr = pd.concat(
                [
                    high - low,
                    (high - close.shift()).abs(),
                    (low - close.shift()).abs(),
                ],
                axis=1,
            ).max(axis=1)

            atr_val = float(tr.rolling(period).mean().iloc[-1])
            return atr_val if not np.isnan(atr_val) else 0.0
        except Exception:
            return 0.0

    def _calculate_trend_strength(self, df: pd.DataFrame) -> float:
        """
        Compute a normalised trend-strength score in [0, 1].

        Uses a simplified ADX-like approach:
          1. Directional movement (+DM, -DM) over the ATR period.
          2. Smoothed +DI and -DI.
          3. DX = |+DI - -DI| / (+DI + -DI).
          4. Normalise DX to [0, 1].

        Returns 0.5 on any calculation error (neutral).
        """
        try:
            period = self.atr_period
            if len(df) < period + 5:
                return 0.5

            high = df["high"].astype(float)
            low = df["low"].astype(float)
            close = df["close"].astype(float)

            # True Range
            tr = pd.concat(
                [
                    high - low,
                    (high - close.shift()).abs(),
                    (low - close.shift()).abs(),
                ],
                axis=1,
            ).max(axis=1)

            # Directional movement
            up_move = high.diff()
            down_move = -low.diff()

            plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
            minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

            atr_smooth = tr.rolling(period).mean()
            plus_di = 100 * plus_dm.rolling(period).mean() / atr_smooth.replace(0, np.nan)
            minus_di = 100 * minus_dm.rolling(period).mean() / atr_smooth.replace(0, np.nan)

            di_sum = (plus_di + minus_di).replace(0, np.nan)
            dx = (plus_di - minus_di).abs() / di_sum * 100

            adx = float(dx.rolling(period).mean().iloc[-1])
            if np.isnan(adx):
                return 0.5

            # Normalise: ADX 0-25 → weak (0-0.4), 25-50 → moderate (0.4-0.7), 50+ → strong (0.7-1.0)
            normalised = min(adx / 50.0, 1.0)
            return round(normalised, 4)

        except Exception:
            return 0.5

    def _fallback(self, reason: str) -> Dict[str, Any]:
        logger.warning(f"MarketRegimeDetector fallback: {reason}")
        return {
            "regime": "LOW_VOL",
            "weights": _DEFAULT_WEIGHTS,
            "atr_ratio": 1.0,
            "trend_strength": 0.5,
            "valid": False,
            "reason": reason,
        }


# Module-level singleton
market_regime_detector = MarketRegimeDetector()

"""
Volatility Adjustment Module — v3.0
Dynamic position sizing based on current vs historical volatility.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
import ta

logger = logging.getLogger(__name__)


class VolatilityAdjuster:
    """
    Adjusts position sizes and risk parameters dynamically based on
    current market volatility relative to historical norms.

    High volatility  → reduce position size (protect capital)
    Low volatility   → increase position size (capture more return)
    Normal volatility → use base position size

    Methods:
    - ATR ratio (current ATR / rolling average ATR)
    - Realised volatility ratio
    - VIX-proxy (XAUUSD implied vol approximation)
    - Bollinger Band width percentile
    """

    def __init__(
        self,
        lookback: int = 20,
        scale_min: float = 0.5,
        scale_max: float = 1.5,
        high_vol_threshold: float = 1.5,
        low_vol_threshold: float = 0.7,
    ) -> None:
        self.lookback = lookback
        self.scale_min = scale_min
        self.scale_max = scale_max
        self.high_vol_threshold = high_vol_threshold
        self.low_vol_threshold = low_vol_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_adjustment(
        self, df: pd.DataFrame, symbol: str = ""
    ) -> dict[str, Any]:
        """
        Compute volatility adjustment factor for position sizing.

        Args:
            df:     OHLCV DataFrame (chronological, oldest first).
            symbol: Optional symbol name for logging.

        Returns:
            dict with scale_factor, vol_regime, atr_ratio, realised_vol_ratio,
            bb_width_pct, and recommendation.
        """
        try:
            if len(df) < self.lookback + 5:
                return self._default_result()

            atr_ratio = self._atr_ratio(df)
            rv_ratio = self._realised_vol_ratio(df)
            bb_width_pct = self._bb_width_percentile(df)

            # Composite vol score (weighted average of three measures)
            composite = (
                atr_ratio * 0.50
                + rv_ratio * 0.30
                + (bb_width_pct / 50.0) * 0.20  # Normalise percentile to ~1.0
            )

            # Determine regime
            if composite > self.high_vol_threshold:
                vol_regime = "HIGH"
                scale_factor = self.scale_min
            elif composite < self.low_vol_threshold:
                vol_regime = "LOW"
                scale_factor = self.scale_max
            else:
                vol_regime = "NORMAL"
                # Linear interpolation between min and max
                norm = (composite - self.low_vol_threshold) / (
                    self.high_vol_threshold - self.low_vol_threshold
                )
                scale_factor = self.scale_max - norm * (self.scale_max - self.scale_min)

            scale_factor = round(
                max(self.scale_min, min(self.scale_max, scale_factor)), 3
            )

            recommendation = self._recommendation(vol_regime, scale_factor)

            return {
                "scale_factor": scale_factor,
                "vol_regime": vol_regime,
                "atr_ratio": round(atr_ratio, 3),
                "realised_vol_ratio": round(rv_ratio, 3),
                "bb_width_percentile": round(bb_width_pct, 1),
                "composite_score": round(composite, 3),
                "recommendation": recommendation,
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as exc:
            logger.error(f"[VolAdjuster] Error for {symbol}: {exc}")
            return self._default_result()

    def adjust_position_size(
        self, base_lots: float, df: pd.DataFrame, symbol: str = ""
    ) -> dict[str, Any]:
        """
        Apply volatility adjustment to a base position size.

        Returns:
            dict with adjusted_lots, scale_factor, vol_regime.
        """
        adj = self.compute_adjustment(df, symbol)
        adjusted = round(base_lots * adj["scale_factor"], 2)
        adjusted = max(0.01, adjusted)

        return {
            "base_lots": base_lots,
            "adjusted_lots": adjusted,
            "scale_factor": adj["scale_factor"],
            "vol_regime": adj["vol_regime"],
            "atr_ratio": adj["atr_ratio"],
        }

    def adjust_sl_distance(
        self, base_sl_atr_mult: float, df: pd.DataFrame, symbol: str = ""
    ) -> float:
        """
        Widen SL in high-vol environments, tighten in low-vol.
        Returns adjusted ATR multiplier for SL.
        """
        adj = self.compute_adjustment(df, symbol)
        if adj["vol_regime"] == "HIGH":
            return round(base_sl_atr_mult * 1.3, 2)
        if adj["vol_regime"] == "LOW":
            return round(base_sl_atr_mult * 0.85, 2)
        return base_sl_atr_mult

    # ------------------------------------------------------------------
    # Volatility Measures
    # ------------------------------------------------------------------

    def _atr_ratio(self, df: pd.DataFrame) -> float:
        """Current ATR / rolling mean ATR over lookback period."""
        try:
            atr_series = ta.volatility.AverageTrueRange(
                df["high"], df["low"], df["close"], window=14
            ).average_true_range()
            current_atr = float(atr_series.iloc[-1])
            mean_atr = float(atr_series.tail(self.lookback).mean())
            if mean_atr > 0:
                return current_atr / mean_atr
        except Exception:
            pass
        return 1.0

    def _realised_vol_ratio(self, df: pd.DataFrame) -> float:
        """
        Short-term realised vol (5-period) / long-term realised vol (lookback).
        """
        try:
            returns = df["close"].pct_change().dropna()
            if len(returns) < self.lookback:
                return 1.0
            short_vol = float(returns.tail(5).std())
            long_vol = float(returns.tail(self.lookback).std())
            if long_vol > 0:
                return short_vol / long_vol
        except Exception:
            pass
        return 1.0

    def _bb_width_percentile(self, df: pd.DataFrame) -> float:
        """
        Current Bollinger Band width percentile vs lookback history.
        Returns 0-100 percentile (50 = median, 100 = widest).
        """
        try:
            bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
            upper = bb.bollinger_hband()
            lower = bb.bollinger_lband()
            mid = bb.bollinger_mavg()
            width = ((upper - lower) / mid.replace(0, np.nan)).dropna()

            if len(width) < 5:
                return 50.0

            current_width = float(width.iloc[-1])
            historical = width.tail(self.lookback).values
            pct = float(np.sum(historical <= current_width) / len(historical) * 100)
            return pct
        except Exception:
            return 50.0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _recommendation(vol_regime: str, scale_factor: float) -> str:
        if vol_regime == "HIGH":
            return f"Reduce position size to {scale_factor:.0%} of base (high volatility)"
        if vol_regime == "LOW":
            return f"Increase position size to {scale_factor:.0%} of base (low volatility)"
        return f"Use {scale_factor:.0%} of base position size (normal volatility)"

    def _default_result(self) -> dict[str, Any]:
        return {
            "scale_factor": 1.0,
            "vol_regime": "NORMAL",
            "atr_ratio": 1.0,
            "realised_vol_ratio": 1.0,
            "bb_width_percentile": 50.0,
            "composite_score": 1.0,
            "recommendation": "Use 100% of base position size (insufficient data)",
            "timestamp": datetime.utcnow().isoformat(),
        }


# Module-level singleton
volatility_adjuster = VolatilityAdjuster()

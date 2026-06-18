"""
Volume Confirmation Strategy — Component D (15% weight)

Confirms a signal direction by checking:
  - Volume trend (increasing on signal direction)
  - Volume vs 20-period average (above 1.2x average)
  - Volume momentum (accelerating)

Used as the 4th strategy in the 4-layer confidence boost system.
"""

import logging
from typing import Dict, Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class VolumeConfirmationStrategy:
    """
    4th strategy: Volume Confirmation (Component D, 15% weight).

    Confirms a signal by checking:
      - Volume trend (increasing on signal direction over last 5 candles)
      - Volume vs average (current volume >= 1.2x 20-period average)
      - Volume momentum (acceleration of volume changes)

    Returns a signal dict compatible with the weighted voting system.
    """

    def __init__(self):
        self.name = "Volume Confirmation"
        self.weight = 0.15

    async def analyze(
        self,
        df: pd.DataFrame,
        signal_direction: str,
    ) -> Dict[str, Any]:
        """
        Analyze volume confirmation for a given signal direction.

        Args:
            df:               OHLCV DataFrame (must contain a 'volume' column).
            signal_direction: Expected direction — "BUY", "SELL", or "NEUTRAL".

        Returns:
            {
                "signal":          "BUY" | "SELL" | "NEUTRAL",
                "confidence":      0.0–1.0,
                "volume_trend":    "INCREASING" | "DECREASING" | "NEUTRAL",
                "volume_ratio":    float  (current_vol / avg_vol),
                "volume_momentum": "ACCELERATING" | "STABLE" | "DECELERATING",
                "valid":           bool,
            }
        """
        try:
            if "volume" not in df.columns or len(df) < 20:
                logger.warning(
                    "VolumeConfirmation: insufficient data "
                    f"(rows={len(df)}, has_volume={'volume' in df.columns})"
                )
                return self._neutral_result("INSUFFICIENT_DATA")

            volume = float(df["volume"].iloc[-1])
            volume_avg = float(df["volume"].iloc[-20:].mean())
            volume_ratio = volume / volume_avg if volume_avg > 0 else 1.0

            # Volume trend — compare last candle vs 5 candles ago
            recent_volumes = df["volume"].iloc[-5:].values.astype(float)
            if recent_volumes[-1] > recent_volumes[0]:
                volume_trend = "INCREASING"
            elif recent_volumes[-1] < recent_volumes[0]:
                volume_trend = "DECREASING"
            else:
                volume_trend = "NEUTRAL"

            # Volume momentum — direction of change acceleration
            vol_changes = np.diff(recent_volumes)
            if len(vol_changes) >= 2:
                if vol_changes[-1] > vol_changes[0]:
                    volume_momentum = "ACCELERATING"
                elif vol_changes[-1] < vol_changes[0]:
                    volume_momentum = "DECELERATING"
                else:
                    volume_momentum = "STABLE"
            else:
                volume_momentum = "STABLE"

            # Determine signal and confidence
            signal = "NEUTRAL"
            confidence = 0.0

            if signal_direction in ("BUY", "SELL"):
                if volume_trend == "INCREASING" and volume_ratio >= 1.2:
                    # Strong confirmation: increasing volume above average
                    signal = signal_direction
                    confidence = min(0.9, 0.5 + (volume_ratio - 1.0) * 0.5)
                elif volume_ratio >= 1.0:
                    # Weak confirmation: volume at or above average
                    signal = signal_direction
                    confidence = 0.5

            logger.debug(
                f"VolumeConfirmation: direction={signal_direction} "
                f"signal={signal} confidence={confidence:.3f} "
                f"volume_ratio={volume_ratio:.2f} trend={volume_trend} "
                f"momentum={volume_momentum}"
            )

            return {
                "signal": signal,
                "confidence": round(confidence, 4),
                "volume_trend": volume_trend,
                "volume_ratio": round(volume_ratio, 2),
                "volume_momentum": volume_momentum,
                "valid": True,
            }

        except Exception as exc:
            logger.warning(f"VolumeConfirmation error: {exc}")
            return self._neutral_result(str(exc))

    @staticmethod
    def _neutral_result(reason: str = "") -> Dict[str, Any]:
        return {
            "signal": "NEUTRAL",
            "confidence": 0.0,
            "volume_trend": "UNKNOWN",
            "volume_ratio": 1.0,
            "volume_momentum": "UNKNOWN",
            "valid": False,
            "reason": reason,
        }

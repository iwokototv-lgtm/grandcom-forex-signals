"""
Mean Reversion Trading Strategy
Statistical mean reversion using Z-score, Bollinger Bands, and RSI extremes
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class MeanReversionStrategy:
    """
    Mean Reversion Strategy for ranging / low-volatility market regimes.

    Signals are generated when price deviates significantly from its
    statistical mean and momentum indicators confirm exhaustion.

    Methods:
    - Z-score deviation (primary)
    - Bollinger Band extremes (confirmation)
    - RSI divergence (momentum exhaustion)
    - Keltner Channel squeeze (volatility filter)
    - Stochastic extremes (entry timing)
    """

    def __init__(
        self,
        zscore_window: int = 20,
        zscore_entry: float = 2.0,
        zscore_exit: float = 0.5,
        bb_window: int = 20,
        bb_std: float = 2.0,
        rsi_window: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        kc_window: int = 20,
        kc_atr_mult: float = 1.5,
    ):
        self.zscore_window = zscore_window
        self.zscore_entry = zscore_entry
        self.zscore_exit = zscore_exit
        self.bb_window = bb_window
        self.bb_std = bb_std
        self.rsi_window = rsi_window
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.kc_window = kc_window
        self.kc_atr_mult = kc_atr_mult
        self.version = "3.0.0"

    # ------------------------------------------------------------------
    # Main Analysis
    # ------------------------------------------------------------------

    def analyze(self, df: pd.DataFrame, symbol: str) -> Dict[str, Any]:
        """
        Full mean reversion analysis.

        Returns:
            Signal dict with direction, confidence, entry/exit levels
        """
        try:
            if len(df) < max(self.zscore_window, self.bb_window, self.rsi_window) + 10:
                return {"error": "Insufficient data", "valid": False}

            df = df.copy()
            self._compute_indicators(df)

            result: Dict[str, Any] = {
                "symbol": symbol,
                "timestamp": datetime.utcnow().isoformat(),
                "valid": True,
                "version": self.version,
            }

            latest = df.iloc[-1]
            current_price = float(latest["close"])

            # Core signals
            result["zscore"] = self._zscore_signal(df)
            result["bollinger"] = self._bollinger_signal(df)
            result["rsi_signal"] = self._rsi_signal(df)
            result["keltner"] = self._keltner_signal(df)
            result["stochastic"] = self._stochastic_signal(df)

            # Composite
            result["composite"] = self._composite_signal(result)
            result["mean_level"] = round(float(df["close"].rolling(self.zscore_window).mean().iloc[-1]), 5)
            result["current_price"] = round(current_price, 5)
            result["deviation_pct"] = round(
                abs(current_price - result["mean_level"]) / result["mean_level"] * 100, 3
            )

            logger.info(
                f"MeanReversion [{symbol}]: signal={result['composite']['signal']} "
                f"confidence={result['composite']['confidence']:.2f} "
                f"zscore={result['zscore']['value']:.2f}"
            )
            return result

        except Exception as exc:
            logger.error(f"MeanReversion analysis error [{symbol}]: {exc}", exc_info=True)
            return {"error": str(exc), "valid": False}

    # ------------------------------------------------------------------
    # Indicator Computation
    # ------------------------------------------------------------------

    def _compute_indicators(self, df: pd.DataFrame) -> None:
        """Compute all required indicators in-place."""
        close = df["close"]
        high = df["high"]
        low = df["low"]

        # Z-score
        roll_mean = close.rolling(self.zscore_window).mean()
        roll_std = close.rolling(self.zscore_window).std()
        df["zscore"] = (close - roll_mean) / roll_std.replace(0, np.nan)

        # Bollinger Bands
        df["bb_mid"] = roll_mean
        df["bb_upper"] = roll_mean + self.bb_std * roll_std
        df["bb_lower"] = roll_mean - self.bb_std * roll_std
        df["bb_pct"] = (close - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"]).replace(0, np.nan)

        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(self.rsi_window).mean()
        loss = (-delta.clip(upper=0)).rolling(self.rsi_window).mean()
        rs = gain / loss.replace(0, np.nan)
        df["rsi"] = 100 - (100 / (1 + rs))

        # ATR
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        df["atr"] = tr.rolling(self.kc_window).mean()

        # Keltner Channel
        kc_mid = close.rolling(self.kc_window).mean()
        df["kc_upper"] = kc_mid + self.kc_atr_mult * df["atr"]
        df["kc_lower"] = kc_mid - self.kc_atr_mult * df["atr"]

        # Stochastic
        low_min = low.rolling(14).min()
        high_max = high.rolling(14).max()
        df["stoch_k"] = 100 * (close - low_min) / (high_max - low_min).replace(0, np.nan)
        df["stoch_d"] = df["stoch_k"].rolling(3).mean()

    # ------------------------------------------------------------------
    # Individual Signals
    # ------------------------------------------------------------------

    def _zscore_signal(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Z-score deviation signal."""
        z = float(df["zscore"].iloc[-1])
        if np.isnan(z):
            return {"signal": "NEUTRAL", "value": 0.0, "confidence": 0.0}

        if z <= -self.zscore_entry:
            signal = "BUY"
            confidence = min(abs(z) / (self.zscore_entry * 2), 1.0)
        elif z >= self.zscore_entry:
            signal = "SELL"
            confidence = min(abs(z) / (self.zscore_entry * 2), 1.0)
        else:
            signal = "NEUTRAL"
            confidence = 0.0

        return {
            "signal": signal,
            "value": round(z, 3),
            "confidence": round(confidence, 3),
            "entry_threshold": self.zscore_entry,
            "exit_threshold": self.zscore_exit,
        }

    def _bollinger_signal(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Bollinger Band extreme signal."""
        latest = df.iloc[-1]
        bb_pct = float(latest.get("bb_pct", 0.5))
        close = float(latest["close"])
        bb_upper = float(latest["bb_upper"])
        bb_lower = float(latest["bb_lower"])
        bb_mid = float(latest["bb_mid"])

        if bb_pct <= 0.05:
            signal = "BUY"
            confidence = min((0.05 - bb_pct) / 0.05 + 0.5, 1.0)
        elif bb_pct >= 0.95:
            signal = "SELL"
            confidence = min((bb_pct - 0.95) / 0.05 + 0.5, 1.0)
        else:
            signal = "NEUTRAL"
            confidence = 0.0

        return {
            "signal": signal,
            "confidence": round(confidence, 3),
            "bb_pct": round(bb_pct, 3),
            "bb_upper": round(bb_upper, 5),
            "bb_lower": round(bb_lower, 5),
            "bb_mid": round(bb_mid, 5),
            "bandwidth": round((bb_upper - bb_lower) / bb_mid * 100, 3) if bb_mid > 0 else 0,
        }

    def _rsi_signal(self, df: pd.DataFrame) -> Dict[str, Any]:
        """RSI extreme signal."""
        rsi = float(df["rsi"].iloc[-1])
        if np.isnan(rsi):
            return {"signal": "NEUTRAL", "value": 50.0, "confidence": 0.0}

        if rsi <= self.rsi_oversold:
            signal = "BUY"
            confidence = min((self.rsi_oversold - rsi) / self.rsi_oversold + 0.5, 1.0)
        elif rsi >= self.rsi_overbought:
            signal = "SELL"
            confidence = min((rsi - self.rsi_overbought) / (100 - self.rsi_overbought) + 0.5, 1.0)
        else:
            signal = "NEUTRAL"
            confidence = 0.0

        return {
            "signal": signal,
            "value": round(rsi, 2),
            "confidence": round(confidence, 3),
            "oversold_threshold": self.rsi_oversold,
            "overbought_threshold": self.rsi_overbought,
        }

    def _keltner_signal(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Keltner Channel squeeze / breakout filter."""
        latest = df.iloc[-1]
        close = float(latest["close"])
        kc_upper = float(latest["kc_upper"])
        kc_lower = float(latest["kc_lower"])
        bb_upper = float(latest["bb_upper"])
        bb_lower = float(latest["bb_lower"])

        # Squeeze: BB inside KC = low volatility, mean reversion favored
        squeeze = bb_upper < kc_upper and bb_lower > kc_lower

        if close < kc_lower:
            signal = "BUY"
        elif close > kc_upper:
            signal = "SELL"
        else:
            signal = "NEUTRAL"

        return {
            "signal": signal,
            "squeeze": squeeze,
            "kc_upper": round(kc_upper, 5),
            "kc_lower": round(kc_lower, 5),
            "mean_reversion_favored": squeeze,
        }

    def _stochastic_signal(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Stochastic oscillator signal."""
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        k = float(latest["stoch_k"])
        d = float(latest["stoch_d"])
        prev_k = float(prev["stoch_k"])
        prev_d = float(prev["stoch_d"])

        if np.isnan(k) or np.isnan(d):
            return {"signal": "NEUTRAL", "k": 50.0, "d": 50.0}

        bull_cross = k > d and prev_k <= prev_d and k < 30
        bear_cross = k < d and prev_k >= prev_d and k > 70

        if bull_cross or k < 20:
            signal = "BUY"
        elif bear_cross or k > 80:
            signal = "SELL"
        else:
            signal = "NEUTRAL"

        return {
            "signal": signal,
            "k": round(k, 2),
            "d": round(d, 2),
            "bullish_cross": bull_cross,
            "bearish_cross": bear_cross,
        }

    # ------------------------------------------------------------------
    # Composite Signal
    # ------------------------------------------------------------------

    def _composite_signal(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Aggregate all mean reversion signals into a composite."""
        signals = [
            result["zscore"]["signal"],
            result["bollinger"]["signal"],
            result["rsi_signal"]["signal"],
            result["keltner"]["signal"],
            result["stochastic"]["signal"],
        ]
        confidences = [
            result["zscore"]["confidence"],
            result["bollinger"]["confidence"],
            result["rsi_signal"]["confidence"],
            0.6 if result["keltner"]["signal"] != "NEUTRAL" else 0.0,
            0.6 if result["stochastic"]["signal"] != "NEUTRAL" else 0.0,
        ]

        buy_count = signals.count("BUY")
        sell_count = signals.count("SELL")
        total = len(signals)

        if buy_count >= 3:
            signal = "BUY"
            confidence = sum(c for s, c in zip(signals, confidences) if s == "BUY") / max(buy_count, 1)
        elif sell_count >= 3:
            signal = "SELL"
            confidence = sum(c for s, c in zip(signals, confidences) if s == "SELL") / max(sell_count, 1)
        else:
            signal = "NEUTRAL"
            confidence = 0.0

        # Squeeze bonus
        if result["keltner"].get("squeeze") and signal != "NEUTRAL":
            confidence = min(confidence * 1.2, 1.0)

        return {
            "signal": signal,
            "confidence": round(confidence, 3),
            "buy_votes": buy_count,
            "sell_votes": sell_count,
            "total_signals": total,
            "squeeze_active": result["keltner"].get("squeeze", False),
        }


# Global instance
mean_reversion_strategy = MeanReversionStrategy()

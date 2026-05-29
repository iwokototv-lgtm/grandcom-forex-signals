"""
Mean Reversion Strategy Module — v3.0
Overbought/oversold trading using statistical and technical mean-reversion signals.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
import ta

logger = logging.getLogger(__name__)


class MeanReversionStrategy:
    """
    Mean reversion strategy for ranging / low-volatility market regimes.

    Signal sources:
    - RSI extremes (oversold < 30, overbought > 70)
    - Bollinger Band touches / squeezes
    - Z-score deviation from rolling mean
    - Stochastic oscillator extremes
    - CCI (Commodity Channel Index) extremes
    - Keltner Channel touches
    """

    def __init__(self) -> None:
        self.rsi_oversold: float = 30.0
        self.rsi_overbought: float = 70.0
        self.zscore_threshold: float = 2.0
        self.cci_threshold: float = 100.0
        self.bb_window: int = 20
        self.bb_std: float = 2.0
        self.kc_window: int = 20
        self.kc_atr_mult: float = 1.5

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, regime: dict | None = None
    ) -> dict[str, Any]:
        """
        Generate a mean-reversion trading signal.

        Args:
            df:      OHLCV DataFrame (chronological, oldest first).
            symbol:  Trading pair identifier.
            regime:  Optional regime dict from RegimeDetector.

        Returns:
            Signal dict with keys: signal, confidence, entry, tp_levels,
            sl, analysis, strategy, mr_context.
        """
        try:
            if len(df) < 60:
                return self._neutral("Insufficient data", symbol)

            # Regime gate — mean reversion only in RANGE / LOW_VOL
            if regime:
                regime_name = regime.get("regime_name", "")
                active = regime.get("active_strategies", [])
                if "mean_reversion" not in active and "reversal" not in active:
                    return self._neutral(
                        f"Regime {regime_name} not suitable for mean reversion", symbol
                    )

            indicators = self._compute_indicators(df)
            signal, confidence, context = self._evaluate_signals(indicators)

            if signal == "NEUTRAL":
                return self._neutral("No mean-reversion confluence", symbol)

            entry, tp_levels, sl = self._build_levels(df, signal, indicators)
            analysis = self._build_analysis(signal, context, indicators)

            return {
                "signal": signal,
                "confidence": round(confidence, 1),
                "entry": round(entry, 2),
                "tp_levels": [round(t, 2) for t in tp_levels],
                "sl": round(sl, 2),
                "analysis": analysis,
                "strategy": "MEAN_REVERSION",
                "mr_context": context,
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as exc:
            logger.error(f"[MeanReversion] Signal error for {symbol}: {exc}")
            return self._neutral(f"Error: {exc}", symbol)

    # ------------------------------------------------------------------
    # Indicator Computation
    # ------------------------------------------------------------------

    def _compute_indicators(self, df: pd.DataFrame) -> dict[str, Any]:
        close = df["close"]
        high = df["high"]
        low = df["low"]

        # RSI
        rsi = ta.momentum.RSIIndicator(close, window=14).rsi()

        # Bollinger Bands
        bb = ta.volatility.BollingerBands(close, window=self.bb_window, window_dev=self.bb_std)
        bb_upper = bb.bollinger_hband()
        bb_lower = bb.bollinger_lband()
        bb_mid = bb.bollinger_mavg()
        bb_width = (bb_upper - bb_lower) / bb_mid

        # Z-score
        rolling_mean = close.rolling(20).mean()
        rolling_std = close.rolling(20).std()
        zscore = (close - rolling_mean) / rolling_std.replace(0, np.nan)

        # Stochastic
        stoch = ta.momentum.StochasticOscillator(high, low, close, window=14, smooth_window=3)
        stoch_k = stoch.stoch()
        stoch_d = stoch.stoch_signal()

        # CCI
        cci = ta.trend.CCIIndicator(high, low, close, window=20).cci()

        # ATR
        atr = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()

        # Keltner Channel
        kc = ta.volatility.KeltnerChannel(
            high, low, close, window=self.kc_window, window_atr=self.kc_window
        )
        kc_upper = kc.keltner_channel_hband()
        kc_lower = kc.keltner_channel_lband()

        latest_price = float(close.iloc[-1])

        return {
            "price": latest_price,
            "rsi": float(rsi.iloc[-1]),
            "rsi_prev": float(rsi.iloc[-2]),
            "bb_upper": float(bb_upper.iloc[-1]),
            "bb_lower": float(bb_lower.iloc[-1]),
            "bb_mid": float(bb_mid.iloc[-1]),
            "bb_width": float(bb_width.iloc[-1]),
            "bb_pct": float(bb.bollinger_pband().iloc[-1]),
            "zscore": float(zscore.iloc[-1]),
            "stoch_k": float(stoch_k.iloc[-1]),
            "stoch_d": float(stoch_d.iloc[-1]),
            "stoch_k_prev": float(stoch_k.iloc[-2]),
            "stoch_d_prev": float(stoch_d.iloc[-2]),
            "cci": float(cci.iloc[-1]),
            "atr": float(atr.iloc[-1]),
            "kc_upper": float(kc_upper.iloc[-1]),
            "kc_lower": float(kc_lower.iloc[-1]),
        }

    # ------------------------------------------------------------------
    # Signal Evaluation
    # ------------------------------------------------------------------

    def _evaluate_signals(
        self, ind: dict[str, Any]
    ) -> tuple[str, float, dict[str, Any]]:
        bullish_score = 0.0
        bearish_score = 0.0
        context: dict[str, Any] = {}

        # RSI (weight: 2)
        if ind["rsi"] < self.rsi_oversold:
            bullish_score += 2.0
            context["rsi"] = f"OVERSOLD ({ind['rsi']:.1f})"
        elif ind["rsi"] > self.rsi_overbought:
            bearish_score += 2.0
            context["rsi"] = f"OVERBOUGHT ({ind['rsi']:.1f})"
        else:
            context["rsi"] = f"NEUTRAL ({ind['rsi']:.1f})"

        # RSI reversal confirmation (weight: 1)
        if ind["rsi"] > ind["rsi_prev"] and ind["rsi_prev"] < self.rsi_oversold:
            bullish_score += 1.0
            context["rsi_reversal"] = "BULLISH_HOOK"
        elif ind["rsi"] < ind["rsi_prev"] and ind["rsi_prev"] > self.rsi_overbought:
            bearish_score += 1.0
            context["rsi_reversal"] = "BEARISH_HOOK"

        # Bollinger Band (weight: 2)
        if ind["price"] <= ind["bb_lower"] * 1.001:
            bullish_score += 2.0
            context["bb"] = "AT_LOWER_BAND"
        elif ind["price"] >= ind["bb_upper"] * 0.999:
            bearish_score += 2.0
            context["bb"] = "AT_UPPER_BAND"
        else:
            context["bb"] = f"MID ({ind['bb_pct']:.2f})"

        # Z-score (weight: 1.5)
        if ind["zscore"] < -self.zscore_threshold:
            bullish_score += 1.5
            context["zscore"] = f"EXTREME_LOW ({ind['zscore']:.2f})"
        elif ind["zscore"] > self.zscore_threshold:
            bearish_score += 1.5
            context["zscore"] = f"EXTREME_HIGH ({ind['zscore']:.2f})"
        else:
            context["zscore"] = f"NORMAL ({ind['zscore']:.2f})"

        # Stochastic (weight: 1)
        stoch_bull_cross = (
            ind["stoch_k"] > ind["stoch_d"]
            and ind["stoch_k_prev"] <= ind["stoch_d_prev"]
            and ind["stoch_k"] < 30
        )
        stoch_bear_cross = (
            ind["stoch_k"] < ind["stoch_d"]
            and ind["stoch_k_prev"] >= ind["stoch_d_prev"]
            and ind["stoch_k"] > 70
        )
        if stoch_bull_cross:
            bullish_score += 1.0
            context["stoch"] = "BULLISH_CROSS_OVERSOLD"
        elif stoch_bear_cross:
            bearish_score += 1.0
            context["stoch"] = "BEARISH_CROSS_OVERBOUGHT"
        elif ind["stoch_k"] < 20:
            bullish_score += 0.5
            context["stoch"] = f"OVERSOLD ({ind['stoch_k']:.1f})"
        elif ind["stoch_k"] > 80:
            bearish_score += 0.5
            context["stoch"] = f"OVERBOUGHT ({ind['stoch_k']:.1f})"
        else:
            context["stoch"] = f"NEUTRAL ({ind['stoch_k']:.1f})"

        # CCI (weight: 1)
        if ind["cci"] < -self.cci_threshold:
            bullish_score += 1.0
            context["cci"] = f"OVERSOLD ({ind['cci']:.1f})"
        elif ind["cci"] > self.cci_threshold:
            bearish_score += 1.0
            context["cci"] = f"OVERBOUGHT ({ind['cci']:.1f})"
        else:
            context["cci"] = f"NEUTRAL ({ind['cci']:.1f})"

        # Keltner Channel (weight: 0.5)
        if ind["price"] <= ind["kc_lower"]:
            bullish_score += 0.5
            context["keltner"] = "BELOW_LOWER"
        elif ind["price"] >= ind["kc_upper"]:
            bearish_score += 0.5
            context["keltner"] = "ABOVE_UPPER"
        else:
            context["keltner"] = "INSIDE"

        # Minimum score threshold
        min_score = 4.0
        total = bullish_score + bearish_score
        if total == 0:
            return "NEUTRAL", 50.0, context

        if bullish_score >= min_score and bullish_score > bearish_score:
            confidence = min(50.0 + (bullish_score / total) * 45.0, 92.0)
            return "BUY", confidence, context
        if bearish_score >= min_score and bearish_score > bullish_score:
            confidence = min(50.0 + (bearish_score / total) * 45.0, 92.0)
            return "SELL", confidence, context

        return "NEUTRAL", 50.0, context

    # ------------------------------------------------------------------
    # Level Construction
    # ------------------------------------------------------------------

    def _build_levels(
        self,
        df: pd.DataFrame,
        signal: str,
        ind: dict[str, Any],
    ) -> tuple[float, list[float], float]:
        entry = ind["price"]
        atr = ind["atr"]

        if signal == "BUY":
            # Target: BB mid, then BB upper, then beyond
            tp1 = round(ind["bb_mid"], 2)
            tp2 = round(ind["bb_upper"], 2)
            tp3 = round(entry + atr * 4.0, 2)
            sl = round(entry - atr * 1.5, 2)
        else:
            tp1 = round(ind["bb_mid"], 2)
            tp2 = round(ind["bb_lower"], 2)
            tp3 = round(entry - atr * 4.0, 2)
            sl = round(entry + atr * 1.5, 2)

        return entry, [tp1, tp2, tp3], sl

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_analysis(
        self,
        signal: str,
        context: dict[str, Any],
        ind: dict[str, Any],
    ) -> str:
        parts = [f"Mean Reversion {signal}."]
        for key in ("rsi", "bb", "zscore", "stoch", "cci"):
            if key in context:
                parts.append(f"{key.upper()}: {context[key]}.")
        return " ".join(parts)[:200]

    @staticmethod
    def _neutral(reason: str, symbol: str) -> dict[str, Any]:
        return {
            "signal": "NEUTRAL",
            "confidence": 50.0,
            "entry": 0.0,
            "tp_levels": [],
            "sl": 0.0,
            "analysis": reason,
            "strategy": "MEAN_REVERSION",
            "mr_context": {},
            "timestamp": datetime.utcnow().isoformat(),
        }


# Module-level singleton
mean_reversion_strategy = MeanReversionStrategy()

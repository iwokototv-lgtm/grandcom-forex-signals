"""
Reversal Detection Engine
Detects regime changes (SELL→BUY or BUY→SELL) and triggers CLOSE_ALL.

Detection logic uses RSI, MACD cross, and MA trend direction.
A reversal is confirmed when ≥2 of 3 indicators flip against the
current dominant regime.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Minimum number of consecutive confirming bars before a reversal is declared
REVERSAL_CONFIRM_BARS: int = 2


class ReversalDetector:
    """
    Monitors market regime and fires CLOSE_ALL when a reversal is detected.

    State is kept in-memory per pair so the detector can track regime
    history across signal cycles without a DB round-trip.
    """

    def __init__(self):
        # {pair: {"regime": "BUY"|"SELL"|"NEUTRAL", "confirm_count": int, "last_reversal": str}}
        self._state: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def detect_reversal(
        self,
        pair: str,
        df: pd.DataFrame,
        current_signal: str,
    ) -> Dict[str, Any]:
        """
        Analyse df and current_signal to decide if a reversal has occurred.

        Returns:
            {
                "reversal_detected": bool,
                "previous_regime": str,
                "new_regime": str,
                "reason": str,
                "indicators": {...},
            }
        """
        try:
            indicators = self._compute_reversal_indicators(df)
            regime_vote = self._vote_regime(indicators)

            state = self._state.setdefault(
                pair,
                {"regime": current_signal, "confirm_count": 0, "last_reversal": None},
            )
            previous_regime = state["regime"]

            # No previous regime — just initialise
            if previous_regime == "NEUTRAL":
                state["regime"] = regime_vote
                return self._no_reversal(previous_regime, regime_vote, indicators)

            # Same direction — reset counter
            if regime_vote == previous_regime or regime_vote == "NEUTRAL":
                state["confirm_count"] = 0
                return self._no_reversal(previous_regime, regime_vote, indicators)

            # Opposite direction — increment confirmation counter
            state["confirm_count"] += 1

            if state["confirm_count"] >= REVERSAL_CONFIRM_BARS:
                # Confirmed reversal
                state["regime"] = regime_vote
                state["confirm_count"] = 0
                state["last_reversal"] = datetime.now(timezone.utc).isoformat()

                reason = (
                    f"Regime flipped {previous_regime}→{regime_vote} "
                    f"(RSI={indicators['rsi']:.1f}, "
                    f"MACD_cross={'YES' if indicators['macd_cross'] else 'NO'}, "
                    f"trend={indicators['trend']})"
                )
                logger.warning(f"[{pair}] REVERSAL DETECTED: {reason}")

                return {
                    "reversal_detected": True,
                    "previous_regime": previous_regime,
                    "new_regime": regime_vote,
                    "reason": reason,
                    "indicators": indicators,
                    "timestamp": state["last_reversal"],
                }

            return self._no_reversal(previous_regime, regime_vote, indicators)

        except Exception as exc:
            logger.error(f"[{pair}] ReversalDetector error: {exc}", exc_info=True)
            return {
                "reversal_detected": False,
                "previous_regime": "UNKNOWN",
                "new_regime": "UNKNOWN",
                "reason": f"ERROR: {exc}",
                "indicators": {},
            }

    # ------------------------------------------------------------------
    # Regime check (without full reversal state machine)
    # ------------------------------------------------------------------

    async def check_regime_change(
        self, pair: str, df: pd.DataFrame
    ) -> Dict[str, Any]:
        """
        Lightweight check — returns current regime vote without updating state.
        """
        try:
            indicators = self._compute_reversal_indicators(df)
            regime = self._vote_regime(indicators)
            return {"pair": pair, "regime": regime, "indicators": indicators}
        except Exception as exc:
            logger.error(f"[{pair}] check_regime_change error: {exc}")
            return {"pair": pair, "regime": "NEUTRAL", "indicators": {}}

    # ------------------------------------------------------------------
    # Indicator computation
    # ------------------------------------------------------------------

    def _compute_reversal_indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Compute RSI, MACD cross, and MA trend from the last N candles."""
        close = df["close"].astype(float)

        # RSI (14)
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1e-9)
        rsi_series = 100 - (100 / (1 + rs))
        rsi = float(rsi_series.iloc[-1])

        # MACD (12/26/9)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_val = float(macd_line.iloc[-1])
        signal_val = float(signal_line.iloc[-1])
        prev_macd = float(macd_line.iloc[-2]) if len(macd_line) > 1 else macd_val
        prev_signal = float(signal_line.iloc[-2]) if len(signal_line) > 1 else signal_val

        # Bullish cross: MACD crosses above signal
        macd_bull_cross = (prev_macd < prev_signal) and (macd_val > signal_val)
        # Bearish cross: MACD crosses below signal
        macd_bear_cross = (prev_macd > prev_signal) and (macd_val < signal_val)
        macd_cross = macd_bull_cross or macd_bear_cross

        # MA trend (20 vs 50 SMA)
        ma20 = float(close.rolling(20).mean().iloc[-1])
        ma50 = float(close.rolling(50).mean().iloc[-1])
        trend = "BULLISH" if ma20 > ma50 else "BEARISH"

        return {
            "rsi": rsi,
            "macd": macd_val,
            "macd_signal": signal_val,
            "macd_cross": macd_cross,
            "macd_bull_cross": macd_bull_cross,
            "macd_bear_cross": macd_bear_cross,
            "ma20": ma20,
            "ma50": ma50,
            "trend": trend,
            "price": float(close.iloc[-1]),
        }

    def _vote_regime(self, ind: Dict[str, Any]) -> str:
        """
        Vote on regime direction using 3 indicators.
        Requires ≥2 bullish votes for BUY, ≥2 bearish for SELL.
        """
        bull_votes = 0
        bear_votes = 0

        # RSI
        if ind["rsi"] < 40:
            bear_votes += 1
        elif ind["rsi"] > 60:
            bull_votes += 1

        # MACD cross
        if ind.get("macd_bull_cross"):
            bull_votes += 1
        elif ind.get("macd_bear_cross"):
            bear_votes += 1
        elif ind["macd"] > ind["macd_signal"]:
            bull_votes += 1
        else:
            bear_votes += 1

        # MA trend
        if ind["trend"] == "BULLISH":
            bull_votes += 1
        else:
            bear_votes += 1

        if bull_votes >= 2:
            return "BUY"
        if bear_votes >= 2:
            return "SELL"
        return "NEUTRAL"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _no_reversal(
        previous: str, current: str, indicators: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {
            "reversal_detected": False,
            "previous_regime": previous,
            "new_regime": current,
            "reason": "NO_REVERSAL",
            "indicators": indicators,
        }

    def get_state(self, pair: str) -> Dict[str, Any]:
        """Return current detector state for a pair."""
        return self._state.get(pair, {})

    def reset_state(self, pair: str) -> None:
        """Reset state for a pair (e.g. after manual close-all)."""
        self._state.pop(pair, None)


# Global singleton
reversal_detector = ReversalDetector()

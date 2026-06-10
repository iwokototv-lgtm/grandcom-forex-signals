"""
Price Action Core Engine
========================
Generates signals from structural price action patterns rather than
lagging indicators.  Three sub-strategies are evaluated and combined:

  1. S/R Break  — price breaks above/below a key pivot level with
                  volume confirmation.
  2. Order Block Rejection — price rejects a previous swing high/low
                  and bounces back into the order block zone.
  3. Liquidity Sweep — price sweeps a recent swing high/low and
                  immediately reverses (stop-hunt reversal).

Confidence scoring:
  Base  : 65%  (price action is more reliable than lagging indicators)
  +10%  : break confirmed by above-average volume
  +5%   : multiple timeframes confirm the level (df_daily provided)
  +5%   : price has tested the level ≥ 2 times (stronger level)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class PriceActionCore:
    """
    Price Action Signal Engine.

    Combines three structural price-action patterns into a single
    directional vote with a confidence score.

    Parameters
    ----------
    swing_lookback : int
        Number of candles to look back when identifying swing highs/lows
        (default 10).
    sr_proximity_atr : float
        Maximum ATR distance from a S/R level to consider price "at the
        level" (default 0.5).
    ob_lookback : int
        Number of candles to scan for order block formation (default 20).
    volume_avg_period : int
        Period for the rolling average volume used in volume confirmation
        (default 20).
    volume_mult : float
        Minimum multiple of average volume required for volume confirmation
        (default 1.2).
    min_swing_tests : int
        Minimum number of times a level must have been tested to earn the
        multi-test bonus (default 2).
    """

    def __init__(
        self,
        swing_lookback: int = 10,
        sr_proximity_atr: float = 0.5,
        ob_lookback: int = 20,
        volume_avg_period: int = 20,
        volume_mult: float = 1.2,
        min_swing_tests: int = 2,
    ) -> None:
        self.swing_lookback    = swing_lookback
        self.sr_proximity_atr  = sr_proximity_atr
        self.ob_lookback       = ob_lookback
        self.volume_avg_period = volume_avg_period
        self.volume_mult       = volume_mult
        self.min_swing_tests   = min_swing_tests
        self.version           = "1.0.0"

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
        Run price action analysis on *df* (primary timeframe).

        Parameters
        ----------
        df       : OHLCV DataFrame for the primary timeframe (e.g. 4H).
        symbol   : Trading symbol string (e.g. "XAUUSD").
        df_daily : Optional daily OHLCV DataFrame used for multi-timeframe
                   level confirmation.

        Returns
        -------
        Dict with keys: vote, confidence, sub_signals, valid, …
        """
        try:
            if df is None or len(df) < max(self.swing_lookback * 3, self.ob_lookback) + 10:
                return self._neutral("INSUFFICIENT_DATA")

            df = df.copy()
            close  = df["close"].astype(float)
            high   = df["high"].astype(float)
            low    = df["low"].astype(float)
            volume = df["volume"].astype(float) if "volume" in df.columns else pd.Series(
                np.zeros(len(df)), index=df.index
            )

            current_price = float(close.iloc[-1])
            atr           = self._calc_atr(high, low, close)

            if np.isnan(atr) or atr <= 0:
                return self._neutral("ATR_INVALID")

            # ── Identify swing highs / lows ───────────────────────────
            swing_highs, swing_lows = self._find_swings(high, low)

            # ── Sub-strategy 1: S/R Break ─────────────────────────────
            sr_signal = self._sr_break(
                df, current_price, atr, swing_highs, swing_lows, volume
            )

            # ── Sub-strategy 2: Order Block Rejection ─────────────────
            ob_signal = self._order_block_rejection(
                df, current_price, atr, swing_highs, swing_lows
            )

            # ── Sub-strategy 3: Liquidity Sweep ───────────────────────
            liq_signal = self._liquidity_sweep(
                df, current_price, atr, swing_highs, swing_lows
            )

            # ── Aggregate votes ───────────────────────────────────────
            sub_signals = {
                "sr_break":    sr_signal,
                "order_block": ob_signal,
                "liq_sweep":   liq_signal,
            }

            buy_votes  = sum(1 for s in sub_signals.values() if s["vote"] == "BUY")
            sell_votes = sum(1 for s in sub_signals.values() if s["vote"] == "SELL")

            if buy_votes == 0 and sell_votes == 0:
                return {
                    **self._neutral("NO_PA_PATTERN"),
                    "sub_signals": sub_signals,
                    "swing_highs": swing_highs[-3:],
                    "swing_lows":  swing_lows[-3:],
                }

            # Majority vote (or first non-neutral if tied)
            if buy_votes > sell_votes:
                vote = "BUY"
                active = [s for s in sub_signals.values() if s["vote"] == "BUY"]
            elif sell_votes > buy_votes:
                vote = "SELL"
                active = [s for s in sub_signals.values() if s["vote"] == "SELL"]
            else:
                # Tie — prefer the signal with the highest individual confidence
                all_active = [s for s in sub_signals.values() if s["vote"] != "NEUTRAL"]
                best = max(all_active, key=lambda s: s.get("confidence", 0.0))
                vote   = best["vote"]
                active = [best]

            # ── Confidence scoring ────────────────────────────────────
            confidence = 0.65  # base

            # +10% if any active signal has volume confirmation
            if any(s.get("volume_confirmed", False) for s in active):
                confidence += 0.10

            # +5% if daily timeframe confirms the level
            if df_daily is not None and self._daily_confirms(df_daily, vote, current_price, atr):
                confidence += 0.05

            # +5% if any active signal has a multi-tested level
            if any(s.get("multi_tested", False) for s in active):
                confidence += 0.05

            confidence = round(max(0.0, min(1.0, confidence)), 4)

            result = {
                "vote":        vote,
                "confidence":  confidence,
                "valid":       True,
                "symbol":      symbol,
                "buy_votes":   buy_votes,
                "sell_votes":  sell_votes,
                "sub_signals": sub_signals,
                "swing_highs": swing_highs[-3:],
                "swing_lows":  swing_lows[-3:],
                "atr":         round(atr, 5),
                "current_price": round(current_price, 5),
            }

            logger.debug(
                f"PriceActionCore [{symbol}]: vote={vote} conf={confidence:.2f} "
                f"buy_votes={buy_votes} sell_votes={sell_votes}"
            )
            return result

        except Exception as exc:
            logger.error(f"PriceActionCore error [{symbol}]: {exc}", exc_info=True)
            return self._neutral(f"EXCEPTION: {exc}")

    # ------------------------------------------------------------------
    # Sub-strategy 1: S/R Break
    # ------------------------------------------------------------------

    def _sr_break(
        self,
        df: pd.DataFrame,
        current_price: float,
        atr: float,
        swing_highs: List[float],
        swing_lows: List[float],
        volume: pd.Series,
    ) -> Dict[str, Any]:
        """
        Detect a confirmed break above resistance or below support.

        A break is valid when:
          - The previous candle closed beyond the level.
          - The current candle is still beyond the level (no immediate reversal).
          - Volume on the break candle is above average (optional bonus).
        """
        if not swing_highs or not swing_lows:
            return self._sub_neutral("NO_SWINGS")

        # Use the most recent swing high as resistance, most recent swing low as support
        resistance = swing_highs[-1]
        support    = swing_lows[-1]

        # Volume confirmation
        vol_avg = float(volume.rolling(self.volume_avg_period).mean().iloc[-1])
        vol_now = float(volume.iloc[-1])
        vol_confirmed = vol_avg > 0 and vol_now >= vol_avg * self.volume_mult

        # Multi-test: count how many times price has touched the level (within 0.5 ATR)
        close = df["close"].astype(float)
        resistance_tests = int(((close - resistance).abs() <= atr * 0.5).sum())
        support_tests    = int(((close - support).abs()    <= atr * 0.5).sum())

        prev_close = float(close.iloc[-2]) if len(close) >= 2 else current_price

        # Bullish break: previous close was below resistance, current is above
        if prev_close < resistance and current_price > resistance:
            return {
                "vote":             "BUY",
                "confidence":       0.65,
                "pattern":          "SR_BREAK_BULLISH",
                "level":            round(resistance, 5),
                "volume_confirmed": vol_confirmed,
                "multi_tested":     resistance_tests >= self.min_swing_tests,
                "level_tests":      resistance_tests,
            }

        # Bearish break: previous close was above support, current is below
        if prev_close > support and current_price < support:
            return {
                "vote":             "SELL",
                "confidence":       0.65,
                "pattern":          "SR_BREAK_BEARISH",
                "level":            round(support, 5),
                "volume_confirmed": vol_confirmed,
                "multi_tested":     support_tests >= self.min_swing_tests,
                "level_tests":      support_tests,
            }

        return self._sub_neutral("NO_SR_BREAK")

    # ------------------------------------------------------------------
    # Sub-strategy 2: Order Block Rejection
    # ------------------------------------------------------------------

    def _order_block_rejection(
        self,
        df: pd.DataFrame,
        current_price: float,
        atr: float,
        swing_highs: List[float],
        swing_lows: List[float],
    ) -> Dict[str, Any]:
        """
        Detect when price rejects a previous swing high/low (order block).

        An order block rejection occurs when:
          - Price enters the zone of a previous swing high/low (within 0.5 ATR).
          - The current candle closes back away from the zone (rejection wick).
          - The rejection is in the direction away from the zone.
        """
        if not swing_highs or not swing_lows:
            return self._sub_neutral("NO_SWINGS")

        close = df["close"].astype(float)
        high  = df["high"].astype(float)
        low   = df["low"].astype(float)

        # Bearish OB rejection: price wicked into a swing high zone and closed lower
        ob_resistance = swing_highs[-1]
        if (
            float(high.iloc[-1]) >= ob_resistance - atr * self.sr_proximity_atr
            and current_price < ob_resistance - atr * 0.2
        ):
            wick_size = float(high.iloc[-1]) - current_price
            if wick_size >= atr * 0.3:   # meaningful rejection wick
                return {
                    "vote":             "SELL",
                    "confidence":       0.65,
                    "pattern":          "OB_REJECTION_BEARISH",
                    "ob_level":         round(ob_resistance, 5),
                    "wick_size":        round(wick_size, 5),
                    "volume_confirmed": False,
                    "multi_tested":     False,
                }

        # Bullish OB rejection: price wicked into a swing low zone and closed higher
        ob_support = swing_lows[-1]
        if (
            float(low.iloc[-1]) <= ob_support + atr * self.sr_proximity_atr
            and current_price > ob_support + atr * 0.2
        ):
            wick_size = current_price - float(low.iloc[-1])
            if wick_size >= atr * 0.3:
                return {
                    "vote":             "BUY",
                    "confidence":       0.65,
                    "pattern":          "OB_REJECTION_BULLISH",
                    "ob_level":         round(ob_support, 5),
                    "wick_size":        round(wick_size, 5),
                    "volume_confirmed": False,
                    "multi_tested":     False,
                }

        return self._sub_neutral("NO_OB_REJECTION")

    # ------------------------------------------------------------------
    # Sub-strategy 3: Liquidity Sweep
    # ------------------------------------------------------------------

    def _liquidity_sweep(
        self,
        df: pd.DataFrame,
        current_price: float,
        atr: float,
        swing_highs: List[float],
        swing_lows: List[float],
    ) -> Dict[str, Any]:
        """
        Detect a liquidity sweep: price briefly exceeds a swing level then
        reverses sharply (stop-hunt pattern).

        A sweep is valid when:
          - The current candle's high/low exceeded the swing level.
          - The current candle closed back on the other side of the level.
          - The reversal body is at least 0.4 ATR (strong rejection).
        """
        if not swing_highs or not swing_lows:
            return self._sub_neutral("NO_SWINGS")

        high  = df["high"].astype(float)
        low   = df["low"].astype(float)
        close = df["close"].astype(float)
        open_ = df["open"].astype(float)

        cur_high  = float(high.iloc[-1])
        cur_low   = float(low.iloc[-1])
        cur_close = float(close.iloc[-1])
        cur_open  = float(open_.iloc[-1])

        # Bearish sweep: wick above swing high, closed below it
        sweep_high = swing_highs[-1]
        if cur_high > sweep_high and cur_close < sweep_high:
            reversal_body = abs(cur_close - cur_open)
            if reversal_body >= atr * 0.4:
                return {
                    "vote":             "SELL",
                    "confidence":       0.65,
                    "pattern":          "LIQUIDITY_SWEEP_BEARISH",
                    "swept_level":      round(sweep_high, 5),
                    "sweep_extent":     round(cur_high - sweep_high, 5),
                    "reversal_body":    round(reversal_body, 5),
                    "volume_confirmed": False,
                    "multi_tested":     False,
                }

        # Bullish sweep: wick below swing low, closed above it
        sweep_low = swing_lows[-1]
        if cur_low < sweep_low and cur_close > sweep_low:
            reversal_body = abs(cur_close - cur_open)
            if reversal_body >= atr * 0.4:
                return {
                    "vote":             "BUY",
                    "confidence":       0.65,
                    "pattern":          "LIQUIDITY_SWEEP_BULLISH",
                    "swept_level":      round(sweep_low, 5),
                    "sweep_extent":     round(sweep_low - cur_low, 5),
                    "reversal_body":    round(reversal_body, 5),
                    "volume_confirmed": False,
                    "multi_tested":     False,
                }

        return self._sub_neutral("NO_LIQUIDITY_SWEEP")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_swings(
        self,
        high: pd.Series,
        low: pd.Series,
    ) -> Tuple[List[float], List[float]]:
        """
        Identify swing highs and lows using a simple pivot detection.

        A swing high at index i: high[i] is the maximum over the window
        [i - lookback, i + lookback].  Swing lows are the symmetric inverse.
        We exclude the last candle (still forming) from pivot detection.
        """
        n = len(high)
        lb = self.swing_lookback
        swing_highs: List[float] = []
        swing_lows:  List[float] = []

        # Scan up to n-2 so we don't include the current (potentially open) candle
        for i in range(lb, n - 1):
            window_h = high.iloc[max(0, i - lb): i + lb + 1]
            window_l = low.iloc[max(0, i - lb):  i + lb + 1]
            if float(high.iloc[i]) == float(window_h.max()):
                swing_highs.append(float(high.iloc[i]))
            if float(low.iloc[i]) == float(window_l.min()):
                swing_lows.append(float(low.iloc[i]))

        return swing_highs, swing_lows

    def _calc_atr(
        self,
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        period: int = 14,
    ) -> float:
        """Compute the most recent ATR value."""
        tr = pd.concat(
            [
                high - low,
                (high - close.shift()).abs(),
                (low  - close.shift()).abs(),
            ],
            axis=1,
        ).max(axis=1)
        val = float(tr.rolling(period).mean().iloc[-1])
        return val if not np.isnan(val) else float("nan")

    def _daily_confirms(
        self,
        df_daily: pd.DataFrame,
        vote: str,
        current_price: float,
        atr: float,
    ) -> bool:
        """
        Check whether the daily timeframe confirms the signal direction.

        Uses EMA20 vs EMA50 on the daily chart.
        """
        try:
            if len(df_daily) < 55:
                return False
            close  = df_daily["close"].astype(float)
            ema20d = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
            ema50d = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
            daily_bull = ema20d > ema50d
            return (vote == "BUY" and daily_bull) or (vote == "SELL" and not daily_bull)
        except Exception:
            return False

    @staticmethod
    def _neutral(reason: str) -> Dict[str, Any]:
        return {
            "vote":       "NEUTRAL",
            "confidence": 0.0,
            "valid":      True,
            "reason":     reason,
        }

    @staticmethod
    def _sub_neutral(reason: str) -> Dict[str, Any]:
        return {
            "vote":       "NEUTRAL",
            "confidence": 0.0,
            "reason":     reason,
        }


# Module-level singleton
price_action_core = PriceActionCore()

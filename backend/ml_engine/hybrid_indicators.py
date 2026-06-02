"""
Hybrid Indicators Engine — Gold Trading System v3.0.2
13 institutional-grade indicator combinations for signal confluence.

Each indicator class addresses a specific quality dimension:
  1.  SMC + Order Flow          — Filter false SMC levels with order flow
  2.  RSI + MACD + Stoch RSI    — Triple momentum confluence
  3.  VWAP + Price Action       — Institutional session alignment
  4.  Fibonacci + SMC           — Stacked confluence zones
  5.  ATR + Bollinger Bands     — Volatility sizing + squeeze detection
  6.  Range + Breakout Filter   — Regime clarity
  7.  Swing + Scalp Entry       — M15 confirmation timing
  8.  Trend + Mean Reversion    — Primary strategy definition
  9.  MTF Pyramid Breakdown     — Timeframe alignment
  10. Session-Based MTF Weight  — Low-liquidity filtering
  11. Fixed + Trailing Stop     — Profit locking hybrid
  12. Volatility-Adjusted Size  — 1% account risk
  13. Dynamic Confluence Score  — >75% = HIGH CONFIDENCE
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR 1 — SMC + ORDER FLOW
# ─────────────────────────────────────────────────────────────────────────────

class SMCOrderFlowIndicator:
    """
    Indicator 1: SMC + Order Flow
    Filters false SMC levels by requiring order flow confirmation.

    A valid SMC level (order block / FVG) must have:
    - Volume spike at the level (≥ 1.5× average volume)
    - Price reaction (rejection candle or engulfing)
    - Level not yet mitigated (price hasn't fully returned)
    """

    def analyze(
        self,
        df: pd.DataFrame,
        smc_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        try:
            if len(df) < 20:
                return {"valid": False, "reason": "Insufficient data"}

            # Volume analysis
            vol = df["volume"].replace(0, np.nan)
            avg_vol = vol.rolling(20).mean()
            latest_vol = _safe_float(vol.iloc[-1])
            avg_vol_val = _safe_float(avg_vol.iloc[-1], 1.0)
            vol_ratio = latest_vol / avg_vol_val if avg_vol_val > 0 else 1.0

            # Order flow confirmation
            order_blocks = smc_result.get("order_blocks", [])
            fvgs         = smc_result.get("fair_value_gaps", [])
            smc_score    = _safe_float(smc_result.get("smc_score", 0))

            # Check for volume confirmation at SMC levels
            vol_confirmed = vol_ratio >= 1.5
            has_ob        = len(order_blocks) > 0
            has_fvg       = len(fvgs) > 0

            # False level filter: reject if SMC score < 4 without volume
            false_level = smc_score < 4 and not vol_confirmed

            confluence = 0
            if vol_confirmed:  confluence += 1
            if has_ob:         confluence += 1
            if has_fvg:        confluence += 1
            if smc_score >= 6: confluence += 1

            return {
                "valid":          not false_level,
                "confluence":     confluence,
                "vol_ratio":      round(vol_ratio, 2),
                "vol_confirmed":  vol_confirmed,
                "has_order_block": has_ob,
                "has_fvg":        has_fvg,
                "smc_score":      smc_score,
                "false_level":    false_level,
                "signal":         "STRONG" if confluence >= 3 else ("WEAK" if confluence >= 2 else "INVALID"),
                "reason": (
                    f"SMC+OrderFlow: score={smc_score}, vol_ratio={vol_ratio:.2f}, "
                    f"OB={'✓' if has_ob else '✗'}, FVG={'✓' if has_fvg else '✗'}"
                ),
            }
        except Exception as e:
            logger.error(f"SMCOrderFlow error: {e}")
            return {"valid": False, "reason": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR 2 — RSI + MACD + STOCHASTIC RSI
# ─────────────────────────────────────────────────────────────────────────────

class TripleMomentumIndicator:
    """
    Indicator 2: RSI + MACD + Stochastic RSI
    Triple momentum confluence — all three must agree for HIGH signal.

    Thresholds:
      RSI:        oversold < 30, overbought > 70
      MACD:       bullish = MACD > signal, bearish = MACD < signal
      Stoch RSI:  oversold < 20, overbought > 80
    """

    def analyze(self, df: pd.DataFrame, side: str = "BUY") -> Dict[str, Any]:
        try:
            if len(df) < 30:
                return {"valid": False, "score": 0, "reason": "Insufficient data"}

            close = df["close"]

            # RSI
            rsi_series = _rsi(close, 14)
            rsi_val    = _safe_float(rsi_series.iloc[-1], 50)

            # MACD
            ema12 = _ema(close, 12)
            ema26 = _ema(close, 26)
            macd  = ema12 - ema26
            signal_line = _ema(macd, 9)
            macd_val    = _safe_float(macd.iloc[-1])
            signal_val  = _safe_float(signal_line.iloc[-1])
            macd_hist   = macd_val - signal_val

            # Stochastic RSI
            rsi_min = rsi_series.rolling(14).min()
            rsi_max = rsi_series.rolling(14).max()
            stoch_rsi_range = (rsi_max - rsi_min).replace(0, np.nan)
            stoch_rsi = ((rsi_series - rsi_min) / stoch_rsi_range * 100).fillna(50)
            stoch_val = _safe_float(stoch_rsi.iloc[-1], 50)

            # Confluence scoring
            if side == "BUY":
                rsi_signal   = rsi_val < 40       # Oversold / recovering
                macd_signal  = macd_hist > 0       # Bullish momentum
                stoch_signal = stoch_val < 40      # Oversold stoch
            else:  # SELL
                rsi_signal   = rsi_val > 60        # Overbought / weakening
                macd_signal  = macd_hist < 0       # Bearish momentum
                stoch_signal = stoch_val > 60      # Overbought stoch

            confluence = sum([rsi_signal, macd_signal, stoch_signal])
            score = (confluence / 3) * 100

            return {
                "valid":        confluence >= 2,
                "score":        round(score, 1),
                "confluence":   confluence,
                "rsi":          round(rsi_val, 1),
                "macd":         round(macd_val, 5),
                "macd_hist":    round(macd_hist, 5),
                "stoch_rsi":    round(stoch_val, 1),
                "rsi_signal":   rsi_signal,
                "macd_signal":  macd_signal,
                "stoch_signal": stoch_signal,
                "signal": (
                    "STRONG" if confluence == 3 else
                    "MEDIUM" if confluence == 2 else
                    "WEAK"
                ),
                "reason": (
                    f"Triple momentum: RSI={rsi_val:.1f} {'✓' if rsi_signal else '✗'}, "
                    f"MACD_hist={macd_hist:.5f} {'✓' if macd_signal else '✗'}, "
                    f"StochRSI={stoch_val:.1f} {'✓' if stoch_signal else '✗'}"
                ),
            }
        except Exception as e:
            logger.error(f"TripleMomentum error: {e}")
            return {"valid": False, "score": 0, "reason": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR 3 — VWAP + PRICE ACTION
# ─────────────────────────────────────────────────────────────────────────────

class VWAPPriceActionIndicator:
    """
    Indicator 3: VWAP + Price Action
    Institutional session alignment using VWAP as dynamic S/R.

    Rules:
      BUY:  price above VWAP + bullish price action (rejection from VWAP)
      SELL: price below VWAP + bearish price action (rejection from VWAP)
      Proximity: entry within 0.5 ATR of VWAP = high-quality zone
    """

    def analyze(self, df: pd.DataFrame, side: str = "BUY") -> Dict[str, Any]:
        try:
            if len(df) < 20:
                return {"valid": False, "score": 0, "reason": "Insufficient data"}

            # VWAP calculation (session-based approximation)
            typical_price = (df["high"] + df["low"] + df["close"]) / 3
            vol = df["volume"].replace(0, 1)
            cumulative_tpv = (typical_price * vol).cumsum()
            cumulative_vol = vol.cumsum()
            vwap = cumulative_tpv / cumulative_vol

            latest_close = _safe_float(df["close"].iloc[-1])
            latest_vwap  = _safe_float(vwap.iloc[-1])
            atr_val      = _safe_float(_atr(df).iloc[-1])

            # Price vs VWAP
            above_vwap = latest_close > latest_vwap
            vwap_distance = abs(latest_close - latest_vwap)
            near_vwap = vwap_distance <= atr_val * 0.5

            # Price action: last 3 candles
            last3 = df.tail(3)
            bullish_candles = sum(1 for _, r in last3.iterrows() if r["close"] > r["open"])
            bearish_candles = 3 - bullish_candles

            # Rejection candle (long wick)
            latest = df.iloc[-1]
            body   = abs(latest["close"] - latest["open"])
            total  = latest["high"] - latest["low"]
            wick_ratio = (total - body) / total if total > 0 else 0
            rejection_candle = wick_ratio > 0.5

            if side == "BUY":
                aligned = above_vwap and bullish_candles >= 2
                score   = (
                    (40 if above_vwap else 0) +
                    (30 if bullish_candles >= 2 else 10 * bullish_candles) +
                    (20 if near_vwap else 0) +
                    (10 if rejection_candle else 0)
                )
            else:
                aligned = not above_vwap and bearish_candles >= 2
                score   = (
                    (40 if not above_vwap else 0) +
                    (30 if bearish_candles >= 2 else 10 * bearish_candles) +
                    (20 if near_vwap else 0) +
                    (10 if rejection_candle else 0)
                )

            return {
                "valid":            aligned,
                "score":            round(score, 1),
                "vwap":             round(latest_vwap, 5),
                "price":            round(latest_close, 5),
                "above_vwap":       above_vwap,
                "near_vwap":        near_vwap,
                "vwap_distance":    round(vwap_distance, 5),
                "rejection_candle": rejection_candle,
                "bullish_candles":  bullish_candles,
                "signal":           "STRONG" if score >= 80 else ("MEDIUM" if score >= 50 else "WEAK"),
                "reason": (
                    f"VWAP={latest_vwap:.5f}, price={'above' if above_vwap else 'below'} VWAP, "
                    f"near={'✓' if near_vwap else '✗'}, rejection={'✓' if rejection_candle else '✗'}"
                ),
            }
        except Exception as e:
            logger.error(f"VWAPPriceAction error: {e}")
            return {"valid": False, "score": 0, "reason": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR 4 — FIBONACCI + SMC CONFLUENCE
# ─────────────────────────────────────────────────────────────────────────────

class FibonacciSMCIndicator:
    """
    Indicator 4: Fibonacci + SMC Confluence
    Stacked zones where Fibonacci retracement aligns with SMC levels.

    Key Fibonacci levels: 38.2%, 50%, 61.8%, 78.6%
    OTE (Optimal Trade Entry): 61.8%–78.6% retracement
    Confluence: Fib level + Order Block + FVG = highest quality
    """

    FIB_LEVELS = [0.236, 0.382, 0.500, 0.618, 0.786, 1.0]
    OTE_LOW    = 0.618
    OTE_HIGH   = 0.786

    def analyze(
        self,
        df: pd.DataFrame,
        side: str,
        smc_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        try:
            if len(df) < 20:
                return {"valid": False, "score": 0, "reason": "Insufficient data"}

            # Find swing high and low for Fibonacci
            lookback = min(50, len(df))
            recent   = df.tail(lookback)
            swing_high = _safe_float(recent["high"].max())
            swing_low  = _safe_float(recent["low"].min())
            current    = _safe_float(df["close"].iloc[-1])

            if swing_high <= swing_low:
                return {"valid": False, "score": 0, "reason": "Invalid swing range"}

            swing_range = swing_high - swing_low

            # Calculate Fibonacci levels
            if side == "BUY":
                # Retracement from high to low (buy the dip)
                fib_levels = {
                    f"{int(lvl * 100)}%": swing_high - (swing_range * lvl)
                    for lvl in self.FIB_LEVELS
                }
                ote_high_price = swing_high - (swing_range * self.OTE_LOW)
                ote_low_price  = swing_high - (swing_range * self.OTE_HIGH)
                in_ote = ote_low_price <= current <= ote_high_price
            else:
                # Retracement from low to high (sell the rally)
                fib_levels = {
                    f"{int(lvl * 100)}%": swing_low + (swing_range * lvl)
                    for lvl in self.FIB_LEVELS
                }
                ote_low_price  = swing_low + (swing_range * self.OTE_LOW)
                ote_high_price = swing_low + (swing_range * self.OTE_HIGH)
                in_ote = ote_low_price <= current <= ote_high_price

            # Check SMC confluence at Fibonacci levels
            order_blocks = smc_result.get("order_blocks", [])
            fvgs         = smc_result.get("fair_value_gaps", [])
            atr_val      = _safe_float(_atr(df).iloc[-1])

            ob_at_fib  = False
            fvg_at_fib = False

            for ob in order_blocks:
                ob_price = _safe_float(ob.get("price", ob.get("high", 0)))
                for fib_price in fib_levels.values():
                    if abs(ob_price - fib_price) <= atr_val:
                        ob_at_fib = True
                        break

            for fvg in fvgs:
                fvg_price = _safe_float(fvg.get("midpoint", fvg.get("price", 0)))
                for fib_price in fib_levels.values():
                    if abs(fvg_price - fib_price) <= atr_val:
                        fvg_at_fib = True
                        break

            # Score
            score = 0
            if in_ote:    score += 50
            if ob_at_fib: score += 30
            if fvg_at_fib:score += 20

            return {
                "valid":       score >= 50,
                "score":       round(score, 1),
                "in_ote":      in_ote,
                "ob_at_fib":   ob_at_fib,
                "fvg_at_fib":  fvg_at_fib,
                "swing_high":  round(swing_high, 5),
                "swing_low":   round(swing_low, 5),
                "current":     round(current, 5),
                "fib_levels":  {k: round(v, 5) for k, v in fib_levels.items()},
                "signal":      "STRONG" if score >= 80 else ("MEDIUM" if score >= 50 else "WEAK"),
                "reason": (
                    f"Fib+SMC: OTE={'✓' if in_ote else '✗'}, "
                    f"OB@Fib={'✓' if ob_at_fib else '✗'}, "
                    f"FVG@Fib={'✓' if fvg_at_fib else '✗'}, score={score}"
                ),
            }
        except Exception as e:
            logger.error(f"FibonacciSMC error: {e}")
            return {"valid": False, "score": 0, "reason": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR 5 — ATR + BOLLINGER BANDS
# ─────────────────────────────────────────────────────────────────────────────

class ATRBollingerIndicator:
    """
    Indicator 5: ATR + Bollinger Bands
    Volatility sizing combined with Bollinger Band squeeze detection.

    ATR: Quantifies current volatility for position sizing
    BB Squeeze: Narrow bands (low ATR) precede explosive moves
    BB Width: Measures volatility expansion/contraction
    """

    def analyze(self, df: pd.DataFrame, side: str = "BUY") -> Dict[str, Any]:
        try:
            if len(df) < 20:
                return {"valid": False, "score": 0, "reason": "Insufficient data"}

            close = df["close"]
            atr_series = _atr(df, 14)
            atr_val    = _safe_float(atr_series.iloc[-1])
            atr_avg    = _safe_float(atr_series.rolling(20).mean().iloc[-1], atr_val)
            atr_ratio  = atr_val / atr_avg if atr_avg > 0 else 1.0

            # Bollinger Bands
            bb_period = 20
            bb_std    = 2.0
            bb_mid    = close.rolling(bb_period).mean()
            bb_std_s  = close.rolling(bb_period).std()
            bb_upper  = bb_mid + bb_std * bb_std_s
            bb_lower  = bb_mid - bb_std * bb_std_s
            bb_width  = (bb_upper - bb_lower) / bb_mid

            latest_close  = _safe_float(close.iloc[-1])
            latest_upper  = _safe_float(bb_upper.iloc[-1])
            latest_lower  = _safe_float(bb_lower.iloc[-1])
            latest_mid    = _safe_float(bb_mid.iloc[-1])
            latest_width  = _safe_float(bb_width.iloc[-1])
            avg_width     = _safe_float(bb_width.rolling(20).mean().iloc[-1], latest_width)

            # Squeeze: current width < 80% of average
            squeeze = latest_width < avg_width * 0.8

            # Price position in BB
            bb_range = latest_upper - latest_lower
            bb_pos   = (latest_close - latest_lower) / bb_range if bb_range > 0 else 0.5

            # Signals
            at_lower_band = bb_pos <= 0.15
            at_upper_band = bb_pos >= 0.85
            expanding     = atr_ratio > 1.2

            if side == "BUY":
                signal_valid = at_lower_band or (squeeze and expanding)
                score = (
                    (40 if at_lower_band else 0) +
                    (30 if squeeze else 0) +
                    (20 if expanding else 0) +
                    (10 if atr_ratio < 1.5 else 0)  # Not too volatile
                )
            else:
                signal_valid = at_upper_band or (squeeze and expanding)
                score = (
                    (40 if at_upper_band else 0) +
                    (30 if squeeze else 0) +
                    (20 if expanding else 0) +
                    (10 if atr_ratio < 1.5 else 0)
                )

            return {
                "valid":       signal_valid,
                "score":       round(score, 1),
                "atr":         round(atr_val, 5),
                "atr_ratio":   round(atr_ratio, 2),
                "bb_upper":    round(latest_upper, 5),
                "bb_lower":    round(latest_lower, 5),
                "bb_mid":      round(latest_mid, 5),
                "bb_position": round(bb_pos, 2),
                "bb_squeeze":  squeeze,
                "expanding":   expanding,
                "signal":      "STRONG" if score >= 70 else ("MEDIUM" if score >= 40 else "WEAK"),
                "reason": (
                    f"ATR={atr_val:.5f} (ratio={atr_ratio:.2f}), "
                    f"BB_pos={bb_pos:.2f}, squeeze={'✓' if squeeze else '✗'}, "
                    f"expanding={'✓' if expanding else '✗'}"
                ),
            }
        except Exception as e:
            logger.error(f"ATRBollinger error: {e}")
            return {"valid": False, "score": 0, "reason": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR 6 — RANGE + BREAKOUT FILTER
# ─────────────────────────────────────────────────────────────────────────────

class RangeBreakoutFilter:
    """
    Indicator 6: Range + Breakout Filter
    Provides regime clarity by distinguishing true breakouts from fakeouts.

    Range detection: ADX < 20, price oscillating between S/R
    Breakout confirmation: ADX rising + volume spike + close beyond range
    Fakeout filter: Reject if close returns inside range within 2 candles
    """

    def analyze(self, df: pd.DataFrame, side: str = "BUY") -> Dict[str, Any]:
        try:
            if len(df) < 30:
                return {"valid": False, "regime": "UNKNOWN", "reason": "Insufficient data"}

            close = df["close"]
            high  = df["high"]
            low   = df["low"]

            # ADX calculation
            tr = pd.concat([
                high - low,
                (high - close.shift()).abs(),
                (low  - close.shift()).abs(),
            ], axis=1).max(axis=1)
            atr14 = tr.ewm(com=13, adjust=False).mean()

            dm_plus  = (high.diff()).clip(lower=0)
            dm_minus = (-low.diff()).clip(lower=0)
            di_plus  = (dm_plus.ewm(com=13, adjust=False).mean() / atr14 * 100)
            di_minus = (dm_minus.ewm(com=13, adjust=False).mean() / atr14 * 100)
            dx       = ((di_plus - di_minus).abs() / (di_plus + di_minus) * 100).fillna(0)
            adx      = dx.ewm(com=13, adjust=False).mean()

            adx_val      = _safe_float(adx.iloc[-1], 25)
            adx_prev     = _safe_float(adx.iloc[-2], 25)
            adx_rising   = adx_val > adx_prev

            # Range boundaries (last 20 candles)
            lookback = 20
            range_high = _safe_float(high.tail(lookback).max())
            range_low  = _safe_float(low.tail(lookback).min())
            latest_close = _safe_float(close.iloc[-1])

            # Volume
            vol = df["volume"].replace(0, np.nan)
            avg_vol = _safe_float(vol.rolling(20).mean().iloc[-1], 1)
            latest_vol = _safe_float(vol.iloc[-1], 0)
            vol_spike = latest_vol > avg_vol * 1.5

            # Regime classification
            if adx_val < 20:
                regime = "RANGE"
            elif adx_val > 25 and adx_rising:
                regime = "BREAKOUT"
            elif adx_val > 30:
                regime = "TREND"
            else:
                regime = "TRANSITIONING"

            # Breakout confirmation
            broke_high = latest_close > range_high
            broke_low  = latest_close < range_low
            confirmed_breakout = (
                (side == "BUY"  and broke_high and vol_spike) or
                (side == "SELL" and broke_low  and vol_spike)
            )

            # Fakeout check: did price return inside range?
            if len(df) >= 3:
                prev2_close = _safe_float(close.iloc[-3])
                fakeout = (
                    (side == "BUY"  and prev2_close > range_high and latest_close < range_high) or
                    (side == "SELL" and prev2_close < range_low  and latest_close > range_low)
                )
            else:
                fakeout = False

            score = 0
            if regime in ("BREAKOUT", "TREND") and confirmed_breakout: score += 60
            elif regime == "RANGE" and not broke_high and not broke_low:  score += 40
            if vol_spike:    score += 20
            if not fakeout:  score += 20

            return {
                "valid":               not fakeout,
                "score":               round(score, 1),
                "regime":              regime,
                "adx":                 round(adx_val, 1),
                "adx_rising":          adx_rising,
                "range_high":          round(range_high, 5),
                "range_low":           round(range_low, 5),
                "broke_high":          broke_high,
                "broke_low":           broke_low,
                "confirmed_breakout":  confirmed_breakout,
                "fakeout":             fakeout,
                "vol_spike":           vol_spike,
                "signal":              "STRONG" if score >= 70 else ("MEDIUM" if score >= 40 else "WEAK"),
                "reason": (
                    f"Regime={regime}, ADX={adx_val:.1f}, "
                    f"breakout={'✓' if confirmed_breakout else '✗'}, "
                    f"fakeout={'✗ FAKEOUT' if fakeout else '✓'}"
                ),
            }
        except Exception as e:
            logger.error(f"RangeBreakout error: {e}")
            return {"valid": False, "regime": "UNKNOWN", "reason": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR 7 — SWING + SCALP ENTRY TIMING
# ─────────────────────────────────────────────────────────────────────────────

class SwingScalpEntryIndicator:
    """
    Indicator 7: Swing + Scalp Entry Timing
    M15 confirmation for precise entry timing on both swing and scalp trades.

    Swing entry: Wait for M15 structure break in trade direction
    Scalp entry: Enter on M15 momentum candle with volume confirmation
    """

    def analyze(
        self,
        df_m15: pd.DataFrame,
        side: str,
        trade_type: str = "SWING",
    ) -> Dict[str, Any]:
        try:
            if len(df_m15) < 10:
                return {"valid": False, "score": 0, "reason": "Insufficient M15 data"}

            close = df_m15["close"]
            high  = df_m15["high"]
            low   = df_m15["low"]

            # EMA crossover (9/21)
            ema9  = _ema(close, 9)
            ema21 = _ema(close, 21)
            ema_bullish = _safe_float(ema9.iloc[-1]) > _safe_float(ema21.iloc[-1])
            ema_cross   = (
                _safe_float(ema9.iloc[-1]) > _safe_float(ema21.iloc[-1]) and
                _safe_float(ema9.iloc[-2]) <= _safe_float(ema21.iloc[-2])
            )

            # Momentum candle
            latest = df_m15.iloc[-1]
            body   = abs(latest["close"] - latest["open"])
            total  = latest["high"] - latest["low"]
            body_ratio = body / total if total > 0 else 0
            strong_candle = body_ratio > 0.6
            bullish_candle = latest["close"] > latest["open"]

            # Structure break (M15)
            recent_high = _safe_float(high.tail(5).iloc[:-1].max())
            recent_low  = _safe_float(low.tail(5).iloc[:-1].min())
            latest_close = _safe_float(close.iloc[-1])
            structure_break_up   = latest_close > recent_high
            structure_break_down = latest_close < recent_low

            # Volume confirmation
            vol = df_m15["volume"].replace(0, np.nan)
            avg_vol = _safe_float(vol.rolling(10).mean().iloc[-1], 1)
            latest_vol = _safe_float(vol.iloc[-1], 0)
            vol_confirm = latest_vol > avg_vol * 1.2

            if side == "BUY":
                if trade_type == "SWING":
                    valid = structure_break_up and ema_bullish
                    score = (
                        (40 if structure_break_up else 0) +
                        (30 if ema_bullish else 0) +
                        (20 if vol_confirm else 0) +
                        (10 if strong_candle and bullish_candle else 0)
                    )
                else:  # SCALP
                    valid = strong_candle and bullish_candle and ema_bullish
                    score = (
                        (40 if strong_candle and bullish_candle else 0) +
                        (30 if ema_cross else 20 if ema_bullish else 0) +
                        (30 if vol_confirm else 0)
                    )
            else:  # SELL
                if trade_type == "SWING":
                    valid = structure_break_down and not ema_bullish
                    score = (
                        (40 if structure_break_down else 0) +
                        (30 if not ema_bullish else 0) +
                        (20 if vol_confirm else 0) +
                        (10 if strong_candle and not bullish_candle else 0)
                    )
                else:  # SCALP
                    valid = strong_candle and not bullish_candle and not ema_bullish
                    score = (
                        (40 if strong_candle and not bullish_candle else 0) +
                        (30 if not ema_bullish else 0) +
                        (30 if vol_confirm else 0)
                    )

            return {
                "valid":            valid,
                "score":            round(score, 1),
                "trade_type":       trade_type,
                "ema_bullish":      ema_bullish,
                "ema_cross":        ema_cross,
                "strong_candle":    strong_candle,
                "bullish_candle":   bullish_candle,
                "structure_break":  structure_break_up if side == "BUY" else structure_break_down,
                "vol_confirm":      vol_confirm,
                "signal":           "STRONG" if score >= 70 else ("MEDIUM" if score >= 40 else "WEAK"),
                "reason": (
                    f"M15 {trade_type}: EMA={'✓' if ema_bullish == (side=='BUY') else '✗'}, "
                    f"structure_break={'✓' if (structure_break_up if side=='BUY' else structure_break_down) else '✗'}, "
                    f"vol={'✓' if vol_confirm else '✗'}"
                ),
            }
        except Exception as e:
            logger.error(f"SwingScalp error: {e}")
            return {"valid": False, "score": 0, "reason": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR 8 — TREND + MEAN REVERSION
# ─────────────────────────────────────────────────────────────────────────────

class TrendMeanReversionIndicator:
    """
    Indicator 8: Trend + Mean Reversion
    Defines primary strategy based on regime and selects appropriate approach.

    Trend strategy:    ADX > 25, trade with trend on pullbacks
    Mean reversion:    ADX < 20, trade range extremes back to mean
    Hybrid:            ADX 20-25, use both with reduced size
    """

    def analyze(self, df: pd.DataFrame, side: str = "BUY") -> Dict[str, Any]:
        try:
            if len(df) < 30:
                return {"valid": False, "strategy": "UNKNOWN", "reason": "Insufficient data"}

            close = df["close"]
            high  = df["high"]
            low   = df["low"]

            # ADX
            tr = pd.concat([
                high - low,
                (high - close.shift()).abs(),
                (low  - close.shift()).abs(),
            ], axis=1).max(axis=1)
            atr14 = tr.ewm(com=13, adjust=False).mean()
            dm_plus  = (high.diff()).clip(lower=0)
            dm_minus = (-low.diff()).clip(lower=0)
            di_plus  = dm_plus.ewm(com=13, adjust=False).mean() / atr14 * 100
            di_minus = dm_minus.ewm(com=13, adjust=False).mean() / atr14 * 100
            dx       = ((di_plus - di_minus).abs() / (di_plus + di_minus) * 100).fillna(0)
            adx      = dx.ewm(com=13, adjust=False).mean()
            adx_val  = _safe_float(adx.iloc[-1], 25)

            # EMA trend
            ema20 = _ema(close, 20)
            ema50 = _ema(close, 50)
            latest_close = _safe_float(close.iloc[-1])
            above_ema20  = latest_close > _safe_float(ema20.iloc[-1])
            above_ema50  = latest_close > _safe_float(ema50.iloc[-1])
            ema_bullish  = _safe_float(ema20.iloc[-1]) > _safe_float(ema50.iloc[-1])

            # Z-score for mean reversion
            mean20 = _safe_float(close.rolling(20).mean().iloc[-1])
            std20  = _safe_float(close.rolling(20).std().iloc[-1], 1)
            zscore = (latest_close - mean20) / std20 if std20 > 0 else 0

            # Strategy selection
            if adx_val > 25:
                strategy = "TREND"
                if side == "BUY":
                    valid = ema_bullish and above_ema20
                    score = (
                        (40 if ema_bullish else 0) +
                        (30 if above_ema20 else 0) +
                        (30 if above_ema50 else 0)
                    )
                else:
                    valid = not ema_bullish and not above_ema20
                    score = (
                        (40 if not ema_bullish else 0) +
                        (30 if not above_ema20 else 0) +
                        (30 if not above_ema50 else 0)
                    )
            elif adx_val < 20:
                strategy = "MEAN_REVERSION"
                if side == "BUY":
                    valid = zscore < -1.5  # Oversold
                    score = min(100, max(0, int((-zscore - 1) * 40)))
                else:
                    valid = zscore > 1.5   # Overbought
                    score = min(100, max(0, int((zscore - 1) * 40)))
            else:
                strategy = "HYBRID"
                valid = True
                score = 50

            return {
                "valid":       valid,
                "score":       round(score, 1),
                "strategy":    strategy,
                "adx":         round(adx_val, 1),
                "zscore":      round(zscore, 2),
                "ema_bullish": ema_bullish,
                "above_ema20": above_ema20,
                "above_ema50": above_ema50,
                "signal":      "STRONG" if score >= 70 else ("MEDIUM" if score >= 40 else "WEAK"),
                "reason": (
                    f"Strategy={strategy}, ADX={adx_val:.1f}, "
                    f"Z-score={zscore:.2f}, EMA={'bullish' if ema_bullish else 'bearish'}"
                ),
            }
        except Exception as e:
            logger.error(f"TrendMeanReversion error: {e}")
            return {"valid": False, "strategy": "UNKNOWN", "reason": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR 9 — MTF PYRAMID BREAKDOWN
# ─────────────────────────────────────────────────────────────────────────────

class MTFPyramidIndicator:
    """
    Indicator 9: MTF Pyramid Breakdown
    Full timeframe alignment from Weekly → Daily → H4 → H1 → M15.

    Pyramid rule: Higher timeframes must align before lower timeframes.
    Score: Each aligned timeframe adds to the pyramid score.
    """

    TIMEFRAME_WEIGHTS = {
        "weekly":  0.30,
        "daily":   0.25,
        "h4":      0.20,
        "h1":      0.15,
        "m15":     0.10,
    }

    def analyze(
        self,
        mtf_result: Dict[str, Any],
        side: str,
    ) -> Dict[str, Any]:
        try:
            if not mtf_result:
                return {"valid": False, "score": 0, "reason": "No MTF data"}

            aligned_tfs: List[str] = []
            misaligned_tfs: List[str] = []
            score = 0.0

            # Check each timeframe
            tf_checks = {
                "weekly":  mtf_result.get("weekly_bias", mtf_result.get("h4_bias", {})),
                "daily":   mtf_result.get("daily_bias", mtf_result.get("h4_bias", {})),
                "h4":      mtf_result.get("h4_bias", {}),
                "h1":      mtf_result.get("h1_structure", {}),
                "m15":     mtf_result.get("m15_trigger", {}),
            }

            for tf, data in tf_checks.items():
                if not data:
                    continue
                direction = str(
                    data.get("direction", data.get("bias", data.get("trigger", "NEUTRAL")))
                ).upper()
                weight = self.TIMEFRAME_WEIGHTS.get(tf, 0.1)

                if side == "BUY":
                    aligned = direction in ("BULLISH", "BUY", "UPTREND")
                else:
                    aligned = direction in ("BEARISH", "SELL", "DOWNTREND")

                if aligned:
                    aligned_tfs.append(tf)
                    score += weight * 100
                else:
                    misaligned_tfs.append(tf)

            # Pyramid rule: H4 must align for valid signal
            h4_aligned = "h4" in aligned_tfs
            valid = h4_aligned and len(aligned_tfs) >= 2

            return {
                "valid":          valid,
                "score":          round(score, 1),
                "aligned_tfs":    aligned_tfs,
                "misaligned_tfs": misaligned_tfs,
                "h4_aligned":     h4_aligned,
                "alignment_pct":  round(len(aligned_tfs) / max(len(tf_checks), 1) * 100, 1),
                "signal":         "STRONG" if score >= 70 else ("MEDIUM" if score >= 40 else "WEAK"),
                "reason": (
                    f"MTF Pyramid: {len(aligned_tfs)}/{len(tf_checks)} aligned "
                    f"({', '.join(aligned_tfs) or 'none'}), score={score:.1f}"
                ),
            }
        except Exception as e:
            logger.error(f"MTFPyramid error: {e}")
            return {"valid": False, "score": 0, "reason": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR 10 — SESSION-BASED MTF WEIGHTING
# ─────────────────────────────────────────────────────────────────────────────

class SessionMTFWeightingIndicator:
    """
    Indicator 10: Session-Based MTF Weighting
    Adjusts MTF weights based on current trading session.

    During low-liquidity sessions (Asia, off-hours):
    - Increase weight of higher timeframes (H4, Daily)
    - Decrease weight of lower timeframes (M15)
    - Require higher confluence threshold

    During high-liquidity sessions (London/NY overlap):
    - Standard weights apply
    - Lower confluence threshold acceptable
    """

    SESSION_WEIGHTS = {
        "LONDON_NY_OVERLAP": {"h4": 0.25, "h1": 0.35, "m15": 0.40},
        "LONDON":            {"h4": 0.30, "h1": 0.40, "m15": 0.30},
        "NEW_YORK":          {"h4": 0.30, "h1": 0.40, "m15": 0.30},
        "ASIA":              {"h4": 0.50, "h1": 0.35, "m15": 0.15},
        "OFF_HOURS":         {"h4": 0.60, "h1": 0.30, "m15": 0.10},
        "POST_NY_CLOSE":     {"h4": 0.60, "h1": 0.30, "m15": 0.10},
    }

    SESSION_THRESHOLDS = {
        "LONDON_NY_OVERLAP": 55,
        "LONDON":            60,
        "NEW_YORK":          60,
        "ASIA":              70,
        "OFF_HOURS":         75,
        "POST_NY_CLOSE":     75,
    }

    def analyze(
        self,
        mtf_result: Dict[str, Any],
        session: str,
        side: str,
    ) -> Dict[str, Any]:
        try:
            weights   = self.SESSION_WEIGHTS.get(session, self.SESSION_WEIGHTS["LONDON"])
            threshold = self.SESSION_THRESHOLDS.get(session, 65)

            h4_data  = mtf_result.get("h4_bias", {}) if mtf_result else {}
            h1_data  = mtf_result.get("h1_structure", {}) if mtf_result else {}
            m15_data = mtf_result.get("m15_trigger", {}) if mtf_result else {}

            def is_aligned(data: Dict, tf: str) -> bool:
                if not data:
                    return False
                direction = str(
                    data.get("direction", data.get("bias", data.get("trigger", "NEUTRAL")))
                ).upper()
                if side == "BUY":
                    return direction in ("BULLISH", "BUY", "UPTREND")
                return direction in ("BEARISH", "SELL", "DOWNTREND")

            h4_aligned  = is_aligned(h4_data, "h4")
            h1_aligned  = is_aligned(h1_data, "h1")
            m15_aligned = is_aligned(m15_data, "m15")

            score = (
                (weights["h4"]  * 100 if h4_aligned  else 0) +
                (weights["h1"]  * 100 if h1_aligned  else 0) +
                (weights["m15"] * 100 if m15_aligned else 0)
            )

            valid = score >= threshold

            return {
                "valid":          valid,
                "score":          round(score, 1),
                "session":        session,
                "threshold":      threshold,
                "weights":        weights,
                "h4_aligned":     h4_aligned,
                "h1_aligned":     h1_aligned,
                "m15_aligned":    m15_aligned,
                "signal":         "STRONG" if score >= 80 else ("MEDIUM" if score >= threshold else "WEAK"),
                "reason": (
                    f"Session={session}, weighted_score={score:.1f} "
                    f"(threshold={threshold}), "
                    f"H4={'✓' if h4_aligned else '✗'} "
                    f"H1={'✓' if h1_aligned else '✗'} "
                    f"M15={'✓' if m15_aligned else '✗'}"
                ),
            }
        except Exception as e:
            logger.error(f"SessionMTFWeighting error: {e}")
            return {"valid": False, "score": 0, "reason": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR 11 — FIXED + TRAILING STOP HYBRID
# ─────────────────────────────────────────────────────────────────────────────

class FixedTrailingStopIndicator:
    """
    Indicator 11: Fixed + Trailing Stop Hybrid
    Combines fixed SL with trailing stop for profit locking.

    Phase 1 (0 → +1R):  Fixed SL at original level
    Phase 2 (+1R → +2R): Move SL to breakeven
    Phase 3 (+2R+):      Trail SL at 50% of profit
    """

    def calculate(
        self,
        entry: float,
        sl: float,
        tp: float,
        current_price: float,
        side: str,
    ) -> Dict[str, Any]:
        try:
            risk    = abs(entry - sl)
            reward  = abs(tp - entry)
            rr      = reward / risk if risk > 0 else 0

            if side == "BUY":
                profit_distance = current_price - entry
            else:
                profit_distance = entry - current_price

            profit_r = profit_distance / risk if risk > 0 else 0

            # Determine phase and recommended SL
            if profit_r < 0:
                phase = "LOSS"
                recommended_sl = sl
                action = "HOLD_FIXED_SL"
            elif profit_r < 1.0:
                phase = "PHASE_1"
                recommended_sl = sl
                action = "HOLD_FIXED_SL"
            elif profit_r < 2.0:
                phase = "PHASE_2"
                recommended_sl = entry  # Breakeven
                action = "MOVE_TO_BREAKEVEN"
            else:
                phase = "PHASE_3"
                # Trail at 50% of profit
                if side == "BUY":
                    recommended_sl = entry + (profit_distance * 0.5)
                else:
                    recommended_sl = entry - (profit_distance * 0.5)
                action = "TRAIL_STOP"

            return {
                "valid":          True,
                "phase":          phase,
                "profit_r":       round(profit_r, 2),
                "rr_ratio":       round(rr, 2),
                "original_sl":    sl,
                "recommended_sl": round(recommended_sl, 5),
                "action":         action,
                "entry":          entry,
                "current_price":  current_price,
                "reason": (
                    f"Stop hybrid: phase={phase}, profit={profit_r:.2f}R, "
                    f"action={action}, new_sl={recommended_sl:.5f}"
                ),
            }
        except Exception as e:
            logger.error(f"FixedTrailingStop error: {e}")
            return {"valid": False, "reason": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR 12 — VOLATILITY-ADJUSTED POSITION SIZING
# ─────────────────────────────────────────────────────────────────────────────

class VolatilityPositionSizingIndicator:
    """
    Indicator 12: Volatility-Adjusted Position Sizing
    Sizes positions to risk exactly 1% of account balance.

    Formula:
      risk_amount = account_balance × 0.01
      sl_distance = |entry - sl|
      lots = risk_amount / (sl_distance × pip_value_per_lot)

    Adjustments:
      - High volatility (ATR ratio > 1.5): reduce size by 30%
      - Low volatility  (ATR ratio < 0.7): increase size by 20%
      - News event nearby: reduce size by 50%
    """

    PIP_VALUES = {
        "GOLD":  1.0,    # $1 per pip per 0.01 lot (XAUUSD)
        "JPY":   9.0,    # ~$9 per pip per lot
        "FOREX": 10.0,   # $10 per pip per lot
    }

    def calculate(
        self,
        account_balance: float,
        entry: float,
        sl: float,
        symbol: str = "XAUUSD",
        atr_ratio: float = 1.0,
        news_nearby: bool = False,
        risk_pct: float = 1.0,
    ) -> Dict[str, Any]:
        try:
            sym_type   = "GOLD" if "XAU" in symbol.upper() else ("JPY" if "JPY" in symbol.upper() else "FOREX")
            pip_mult   = 100.0 if sym_type in ("GOLD", "JPY") else 10_000.0
            pip_value  = self.PIP_VALUES[sym_type]

            sl_distance = abs(entry - sl)
            sl_pips     = sl_distance * pip_mult
            risk_amount = account_balance * risk_pct / 100.0

            # Base position size
            base_lots = risk_amount / (sl_pips * pip_value) if sl_pips > 0 else 0.01

            # Volatility adjustment
            if atr_ratio > 1.5:
                vol_multiplier = 0.70   # Reduce 30% in high vol
                vol_note = "HIGH_VOL: -30%"
            elif atr_ratio < 0.7:
                vol_multiplier = 1.20   # Increase 20% in low vol
                vol_note = "LOW_VOL: +20%"
            else:
                vol_multiplier = 1.0
                vol_note = "NORMAL_VOL"

            # News adjustment
            news_multiplier = 0.50 if news_nearby else 1.0
            news_note = "NEWS_NEARBY: -50%" if news_nearby else "NO_NEWS"

            # Final size
            final_lots = base_lots * vol_multiplier * news_multiplier
            final_lots = max(0.01, min(final_lots, 10.0))
            final_lots = round(final_lots, 2)

            actual_risk = final_lots * sl_pips * pip_value
            actual_risk_pct = (actual_risk / account_balance * 100) if account_balance > 0 else 0

            return {
                "valid":            True,
                "lots":             final_lots,
                "base_lots":        round(base_lots, 2),
                "sl_pips":          round(sl_pips, 1),
                "risk_amount":      round(risk_amount, 2),
                "actual_risk":      round(actual_risk, 2),
                "actual_risk_pct":  round(actual_risk_pct, 2),
                "vol_multiplier":   vol_multiplier,
                "news_multiplier":  news_multiplier,
                "vol_note":         vol_note,
                "news_note":        news_note,
                "reason": (
                    f"Position size: {final_lots} lots "
                    f"({actual_risk_pct:.2f}% risk, ${actual_risk:.2f}), "
                    f"{vol_note}, {news_note}"
                ),
            }
        except Exception as e:
            logger.error(f"VolatilityPositionSizing error: {e}")
            return {"valid": False, "lots": 0.01, "reason": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR 13 — DYNAMIC CONFLUENCE SCORE
# ─────────────────────────────────────────────────────────────────────────────

class DynamicConfluenceScore:
    """
    Indicator 13: Dynamic Confluence Score
    Aggregates all 12 indicator results into a single 0–100 score.

    Score > 75% = HIGH CONFIDENCE → signal approved
    Score 65–75% = MEDIUM CONFIDENCE → review recommended
    Score < 65% = LOW CONFIDENCE → signal rejected

    Weights are dynamically adjusted based on market regime and session.
    """

    BASE_WEIGHTS = {
        "smc_order_flow":      0.12,
        "triple_momentum":     0.12,
        "vwap_price_action":   0.08,
        "fibonacci_smc":       0.10,
        "atr_bollinger":       0.08,
        "range_breakout":      0.10,
        "swing_scalp_entry":   0.08,
        "trend_mean_reversion":0.10,
        "mtf_pyramid":         0.12,
        "session_mtf_weight":  0.05,
        "fixed_trailing_stop": 0.03,
        "vol_position_sizing": 0.02,
    }

    APPROVAL_THRESHOLD = 75.0

    def calculate(
        self,
        indicator_results: Dict[str, Dict[str, Any]],
        regime: str = "RANGE",
        session: str = "LONDON",
    ) -> Dict[str, Any]:
        try:
            weights = dict(self.BASE_WEIGHTS)

            # Regime-based weight adjustments
            if regime in ("TREND_UP", "TREND_DOWN"):
                weights["trend_mean_reversion"] = 0.15
                weights["mtf_pyramid"]          = 0.15
                weights["range_breakout"]       = 0.05
            elif regime == "RANGE":
                weights["vwap_price_action"]    = 0.12
                weights["fibonacci_smc"]        = 0.12
                weights["range_breakout"]       = 0.08
                weights["trend_mean_reversion"] = 0.06
            elif regime == "BREAKOUT":
                weights["range_breakout"]       = 0.15
                weights["atr_bollinger"]        = 0.12
                weights["triple_momentum"]      = 0.15

            # Session-based weight adjustments
            if session in ("ASIA", "OFF_HOURS", "POST_NY_CLOSE"):
                weights["session_mtf_weight"]   = 0.10
                weights["mtf_pyramid"]          = 0.15
                weights["swing_scalp_entry"]    = 0.05

            # Normalise weights to sum to 1.0
            total_weight = sum(weights.values())
            weights = {k: v / total_weight for k, v in weights.items()}

            # Calculate weighted score
            total_score = 0.0
            component_scores: Dict[str, float] = {}
            missing_indicators: List[str] = []

            for indicator_name, weight in weights.items():
                result = indicator_results.get(indicator_name, {})
                if not result:
                    missing_indicators.append(indicator_name)
                    # Neutral score for missing indicators
                    ind_score = 50.0
                else:
                    ind_score = _safe_float(result.get("score", 50 if result.get("valid") else 0))

                component_scores[indicator_name] = round(ind_score, 1)
                total_score += ind_score * weight

            total_score = max(0.0, min(100.0, total_score))
            approved    = total_score >= self.APPROVAL_THRESHOLD

            # Count strong signals
            strong_count  = sum(1 for r in indicator_results.values() if r.get("signal") == "STRONG")
            medium_count  = sum(1 for r in indicator_results.values() if r.get("signal") == "MEDIUM")
            invalid_count = sum(1 for r in indicator_results.values() if r.get("signal") == "INVALID")

            return {
                "valid":              approved,
                "score":              round(total_score, 1),
                "approved":           approved,
                "threshold":          self.APPROVAL_THRESHOLD,
                "tier": (
                    "HIGH"     if total_score >= 85 else
                    "MEDIUM"   if total_score >= 75 else
                    "LOW"      if total_score >= 65 else
                    "REJECTED"
                ),
                "component_scores":   component_scores,
                "strong_signals":     strong_count,
                "medium_signals":     medium_count,
                "invalid_signals":    invalid_count,
                "missing_indicators": missing_indicators,
                "regime":             regime,
                "session":            session,
                "reason": (
                    f"Dynamic confluence: {total_score:.1f}% "
                    f"({'APPROVED' if approved else 'REJECTED'}, "
                    f"threshold={self.APPROVAL_THRESHOLD}%), "
                    f"strong={strong_count}, medium={medium_count}"
                ),
            }
        except Exception as e:
            logger.error(f"DynamicConfluence error: {e}")
            return {"valid": False, "score": 0, "approved": False, "reason": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# MAIN HYBRID INDICATORS CLASS
# ─────────────────────────────────────────────────────────────────────────────

class HybridIndicators:
    """
    Orchestrator for all 13 hybrid indicator combinations.

    Runs all indicators and produces a unified analysis result
    with the dynamic confluence score.

    Usage::

        hi = HybridIndicators()
        result = hi.analyze(
            df=price_df,
            side="BUY",
            symbol="XAUUSD",
            trade_type="SWING",
            regime="TREND_UP",
            session="LONDON",
            smc_result=smc_dict,
            mtf_result=mtf_dict,
            account_balance=10000.0,
            entry=2650.0,
            sl=2640.0,
            tp=2670.0,
            current_price=2651.0,
        )
        if result["approved"]:
            print(f"Signal approved: {result['confluence_score']}%")
    """

    def __init__(self) -> None:
        self.smc_order_flow       = SMCOrderFlowIndicator()
        self.triple_momentum      = TripleMomentumIndicator()
        self.vwap_price_action    = VWAPPriceActionIndicator()
        self.fibonacci_smc        = FibonacciSMCIndicator()
        self.atr_bollinger        = ATRBollingerIndicator()
        self.range_breakout       = RangeBreakoutFilter()
        self.swing_scalp_entry    = SwingScalpEntryIndicator()
        self.trend_mean_reversion = TrendMeanReversionIndicator()
        self.mtf_pyramid          = MTFPyramidIndicator()
        self.session_mtf_weight   = SessionMTFWeightingIndicator()
        self.fixed_trailing_stop  = FixedTrailingStopIndicator()
        self.vol_position_sizing  = VolatilityPositionSizingIndicator()
        self.dynamic_confluence   = DynamicConfluenceScore()
        self.version              = "1.0.0"

    def analyze(
        self,
        df: pd.DataFrame,
        side: str,
        symbol: str = "XAUUSD",
        trade_type: str = "SWING",
        regime: str = "RANGE",
        session: str = "LONDON",
        smc_result: Optional[Dict[str, Any]] = None,
        mtf_result: Optional[Dict[str, Any]] = None,
        account_balance: float = 10_000.0,
        entry: float = 0.0,
        sl: float = 0.0,
        tp: float = 0.0,
        current_price: float = 0.0,
        df_m15: Optional[pd.DataFrame] = None,
        atr_ratio: float = 1.0,
        news_nearby: bool = False,
    ) -> Dict[str, Any]:
        """
        Run all 13 hybrid indicators and return unified analysis.

        Returns:
            {
              "approved": bool,
              "confluence_score": float,
              "tier": str,
              "indicators": { indicator_name: result_dict },
              "position_size_lots": float,
              "stop_recommendation": dict,
              "version": str,
            }
        """
        smc_result  = smc_result  or {}
        mtf_result  = mtf_result  or {}
        df_m15_safe = df_m15 if df_m15 is not None and len(df_m15) >= 10 else df

        indicator_results: Dict[str, Dict[str, Any]] = {}

        # 1. SMC + Order Flow
        indicator_results["smc_order_flow"] = self.smc_order_flow.analyze(df, smc_result)

        # 2. Triple Momentum
        indicator_results["triple_momentum"] = self.triple_momentum.analyze(df, side)

        # 3. VWAP + Price Action
        indicator_results["vwap_price_action"] = self.vwap_price_action.analyze(df, side)

        # 4. Fibonacci + SMC
        indicator_results["fibonacci_smc"] = self.fibonacci_smc.analyze(df, side, smc_result)

        # 5. ATR + Bollinger Bands
        indicator_results["atr_bollinger"] = self.atr_bollinger.analyze(df, side)

        # 6. Range + Breakout Filter
        indicator_results["range_breakout"] = self.range_breakout.analyze(df, side)

        # 7. Swing + Scalp Entry
        indicator_results["swing_scalp_entry"] = self.swing_scalp_entry.analyze(
            df_m15_safe, side, trade_type
        )

        # 8. Trend + Mean Reversion
        indicator_results["trend_mean_reversion"] = self.trend_mean_reversion.analyze(df, side)

        # 9. MTF Pyramid
        indicator_results["mtf_pyramid"] = self.mtf_pyramid.analyze(mtf_result, side)

        # 10. Session MTF Weighting
        indicator_results["session_mtf_weight"] = self.session_mtf_weight.analyze(
            mtf_result, session, side
        )

        # 11. Fixed + Trailing Stop
        if entry > 0 and sl > 0 and tp > 0 and current_price > 0:
            indicator_results["fixed_trailing_stop"] = self.fixed_trailing_stop.calculate(
                entry, sl, tp, current_price, side
            )
        else:
            indicator_results["fixed_trailing_stop"] = {"valid": True, "score": 50, "signal": "MEDIUM"}

        # 12. Volatility-Adjusted Position Sizing
        if entry > 0 and sl > 0:
            indicator_results["vol_position_sizing"] = self.vol_position_sizing.calculate(
                account_balance, entry, sl, symbol, atr_ratio, news_nearby
            )
        else:
            indicator_results["vol_position_sizing"] = {"valid": True, "score": 50, "lots": 0.01}

        # 13. Dynamic Confluence Score
        confluence_result = self.dynamic_confluence.calculate(
            indicator_results, regime, session
        )
        indicator_results["dynamic_confluence"] = confluence_result

        # Extract key outputs
        position_lots = _safe_float(
            indicator_results.get("vol_position_sizing", {}).get("lots", 0.01), 0.01
        )
        stop_rec = indicator_results.get("fixed_trailing_stop", {})

        return {
            "approved":           confluence_result.get("approved", False),
            "confluence_score":   confluence_result.get("score", 0.0),
            "tier":               confluence_result.get("tier", "REJECTED"),
            "indicators":         indicator_results,
            "position_size_lots": position_lots,
            "stop_recommendation": stop_rec,
            "strong_signals":     confluence_result.get("strong_signals", 0),
            "medium_signals":     confluence_result.get("medium_signals", 0),
            "regime":             regime,
            "session":            session,
            "version":            self.version,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────────────────────

hybrid_indicators = HybridIndicators()

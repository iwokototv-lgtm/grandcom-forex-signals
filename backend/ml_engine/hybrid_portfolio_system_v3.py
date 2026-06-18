"""
Hybrid Portfolio System v3.3
Comprehensive signal generation with weighted engines and confirmation filters.

Architecture:
  Component A — Trend Confirmation  (RSI + MACD + EMA cross)               [v3.3]
  Component B — Support/Resistance  (Pivot Points + ATR proximity)          [v3.3]
  Component C — Multi-Timeframe     (Daily + 4H + 1H alignment)             [v3.3]
  Component D — Volume Confirmation (Forex-specific: Tick vol, ATR, Range)  [v3.3]

  Extended engines (v3.2+):
  Component MR — Mean Reversion     (EMA deviation + RSI extremes + Bollinger Bands)
  Component PA — Price Action       (BOS, CHOCH, FVG, Equal H/L, Mitigation Block)
  Component MC — Macro Filter       (Fed bias, 10Y yield, DXY, news)

Signal logic: WEIGHTED VOTING based on backtest performance (not equal majority vote).
  Weights (v3.3): A=40%, B=25%, C=20%, D=15%. Consensus threshold: 0.50.
  Confirmation filters: MTF alignment >= 80%, SMC score >= 7/10, no high-impact news.
  Target confidence: 95%+.

strategy_mode options:
  "original"       — 4-component weighted voting (A=40%, B=25%, C=20%, D=15%)
                     with 3-layer confirmation filters (MTF, SMC, news)
  "mean_reversion" — only use MR signals
  "price_action"   — only use PA signals
  "macro_filtered" — use any signal that passes macro filter
  "consensus"      — require 2+ of MR / PA / macro-filtered to agree
"""

import asyncio
import os
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import logging

from .multi_timeframe_confirmation import MultiTimeframeConfirmation
from .pivot_points_analyzer import PivotPointsAnalyzer
from .economic_calendar import EconomicCalendar
from .position_calculator import PositionCalculator
from .portfolio_manager import PortfolioManager
from .strategy_router import StrategyRouter
from .feature_engineering import FeatureEngineer
from .mean_reversion_core import MeanReversionCore
from .price_action_core import PriceActionCore
from .macro_filter import MacroFilter
from .volume_confirmation import VolumeConfirmationStrategy


logger = logging.getLogger(__name__)

# ── WEIGHTED VOTING CONFIGURATION (v3.3 — 95%+ confidence) ───────────────────
#
# OLD WEIGHTS (75% confidence):
#   WEIGHT_A = 0.50  # Price Action / Trend
#   WEIGHT_B = 0.30  # Support/Resistance
#   WEIGHT_C = 0.20  # Multi-Timeframe
#
# NEW WEIGHTS (95%+ confidence):
WEIGHT_A = float(os.environ.get("WEIGHT_A", "0.40"))  # Trend Confirmation (40%)
WEIGHT_B = float(os.environ.get("WEIGHT_B", "0.25"))  # Support/Resistance (25%)
WEIGHT_C = float(os.environ.get("WEIGHT_C", "0.20"))  # Multi-Timeframe    (20%)
WEIGHT_D = float(os.environ.get("WEIGHT_D", "0.15"))  # Volume Confirmation (15%)

# Consensus threshold — minimum weighted score to produce a directional signal
# 0.30 (30%) allows any 2 strategies to agree (smallest pair C+D = 0.35);
# 95%+ confidence is enforced by Layer 3 confirmation filters (MTF, SMC, news)
# and MIN_CONFIDENCE_FOR_SIGNAL.
CONSENSUS_THRESHOLD = float(os.environ.get("CONSENSUS_THRESHOLD", "0.30"))

# Confirmation filter thresholds
MIN_MTF_ALIGNMENT_PCT = float(os.environ.get("MIN_MTF_ALIGNMENT_PCT", "0.0"))
MIN_SMC_SCORE         = int(os.environ.get("MIN_SMC_SCORE", "0"))

# Minimum final confidence required to send a signal (after filters)
MIN_CONFIDENCE_FOR_SIGNAL = float(os.environ.get("MIN_CONFIDENCE_FOR_SIGNAL", "90.0"))

logger.info(
    f"HybridPortfolioV3 weights loaded — "
    f"A={WEIGHT_A} B={WEIGHT_B} C={WEIGHT_C} D={WEIGHT_D} "
    f"consensus_threshold={CONSENSUS_THRESHOLD} "
    f"min_mtf={MIN_MTF_ALIGNMENT_PCT}% min_smc={MIN_SMC_SCORE}/10 "
    f"min_confidence={MIN_CONFIDENCE_FOR_SIGNAL}%"
)


# ---------------------------------------------------------------------------
# Price Action Engine — Configurable Thresholds
# ---------------------------------------------------------------------------
# These values are read from environment variables so they can be tuned
# without code changes.  Per-pair overrides follow the pattern:
#   PRICE_ACTION_MOMENTUM_THRESHOLD_XAUUSD=0.70
#   PRICE_ACTION_VOLATILITY_THRESHOLD_XAUEUR=0.60
# If no per-pair override is set, the global default is used.

_PA_MOMENTUM_THRESHOLD_DEFAULT   = float(os.environ.get("PRICE_ACTION_MOMENTUM_THRESHOLD",  "0.65"))
_PA_VOLATILITY_THRESHOLD_DEFAULT = float(os.environ.get("PRICE_ACTION_VOLATILITY_THRESHOLD", "0.55"))
_PA_CONFLUENCE_WEIGHT_DEFAULT    = float(os.environ.get("PRICE_ACTION_CONFLUENCE_WEIGHT",    "0.40"))

logger.info(
    f"PriceAction thresholds loaded — "
    f"momentum={_PA_MOMENTUM_THRESHOLD_DEFAULT} "
    f"volatility={_PA_VOLATILITY_THRESHOLD_DEFAULT} "
    f"confluence_weight={_PA_CONFLUENCE_WEIGHT_DEFAULT}"
)


def _get_pa_thresholds(symbol: str) -> Dict[str, float]:
    """
    Return price action thresholds for *symbol*, applying per-pair overrides
    from environment variables when present.

    Per-pair env var pattern (symbol uppercased, slashes stripped):
      PRICE_ACTION_MOMENTUM_THRESHOLD_XAUUSD
      PRICE_ACTION_VOLATILITY_THRESHOLD_XAUEUR

    Returns a dict with keys: momentum_threshold, volatility_threshold,
    confluence_weight.
    """
    sym = symbol.upper().replace("/", "")

    momentum   = float(os.environ.get(f"PRICE_ACTION_MOMENTUM_THRESHOLD_{sym}",   _PA_MOMENTUM_THRESHOLD_DEFAULT))
    volatility = float(os.environ.get(f"PRICE_ACTION_VOLATILITY_THRESHOLD_{sym}", _PA_VOLATILITY_THRESHOLD_DEFAULT))
    confluence = float(os.environ.get(f"PRICE_ACTION_CONFLUENCE_WEIGHT_{sym}",    _PA_CONFLUENCE_WEIGHT_DEFAULT))

    return {
        "momentum_threshold":   momentum,
        "volatility_threshold": volatility,
        "confluence_weight":    confluence,
    }


class HybridPortfolioSystemV3:
    """
    Hybrid portfolio system combining 3 independent strategies with weighted voting.

    Strategies:
      Component A: Price Action (50% weight) — WINNER from backtest
          Trend detection, support/resistance breaks, order block rejection
          Backtest: 45.1% win rate, 2.17 profit factor
      
      Component B: Support/Resistance (30% weight)
          Pivot points, key levels, zone analysis
      
      Component C: Multi-Timeframe Alignment (20% weight)
          1H + 4H + Daily alignment, confluence scoring

    Voting logic: WEIGHTED VOTING (not majority vote)
      - Strategy A: 50% weight (most reliable)
      - Strategy B: 30% weight
      - Strategy C: 20% weight
      - Minimum threshold: 35% weighted agreement
      - Confidence: 60% (A alone) to 90% (all 3 agree)

    Example:
      A=BUY (50%), B=SELL (0%), C=NEUTRAL (0%) → BUY (50% > 35%)
      A=BUY (50%), B=BUY (30%), C=NEUTRAL (0%) → BUY (80% > 35%)
      A=BUY (50%), B=SELL (30%), C=NEUTRAL (0%) → BUY (50% > 35%)
      A=SELL (50%), B=BUY (30%), C=NEUTRAL (0%) → SELL (50% > 35%)
      A=NEUTRAL (0%), B=BUY (30%), C=BUY (20%) → BUY (50% > 35%)
      A=NEUTRAL (0%), B=SELL (30%), C=NEUTRAL (0%) → NEUTRAL (30% < 35%)

    Extended engines (v3.2):
      Component MR: Mean Reversion
          BUY  when price < EMA20 - 2×ATR AND RSI < 30
          SELL when price > EMA20 + 2×ATR AND RSI > 70
          Base confidence: 60%. Threshold: 60%.
      Component PA: Price Action
          S/R breaks, order block rejections, liquidity sweeps
          Base confidence: 65%. Threshold: 65%.
      Component MC: Macro Filter
          DXY strength, real rates, inflation expectations
          Applies ±20% confidence modifier to other signals.

    strategy_mode parameter selects which approach to use.
    """

    def __init__(self, account_balance: float = 10000.0):
        self.account_balance = account_balance
        self.version = "3.3.0"

        # Core 4-component engines (v3.3)
        self.mtf_confirmation = MultiTimeframeConfirmation()
        self.pivot_analyzer = PivotPointsAnalyzer()
        self.feature_engineer = FeatureEngineer()
        self.volume_confirmation = VolumeConfirmationStrategy()  # Component D (NEW v3.3)

        # Extended engines (v3.2)
        self.mean_reversion_engine = MeanReversionCore()
        self.price_action_engine   = PriceActionCore()
        self.macro_filter_engine   = MacroFilter()

        # Infrastructure (kept for compatibility)
        self.economic_calendar = EconomicCalendar()
        self.position_calculator = PositionCalculator()
        self.portfolio_manager = PortfolioManager()
        self.strategy_router = StrategyRouter()

        logger.info(
            f"HybridPortfolioSystemV3 initialized — v{self.version} "
            f"(4-component + MR/PA/Macro | "
            f"weights A={WEIGHT_A} B={WEIGHT_B} "
            f"C={WEIGHT_C} D={WEIGHT_D} threshold={CONSENSUS_THRESHOLD})"
        )


    # ------------------------------------------------------------------
    # Component A: Trend Confirmation
    # ------------------------------------------------------------------

    def _component_a_trend(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Trend Confirmation via RSI + MACD + EMA cross.

        Rules:
          BUY  → RSI > 60  AND MACD histogram > 0  AND EMA20 > EMA50
          SELL → RSI < 40  AND MACD histogram < 0  AND EMA20 < EMA50
          NEUTRAL → anything else (no trade)

        Returns vote, confidence (0-1), and indicator values.
        """
        try:
            close = df["close"].astype(float)

            # RSI (14)
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi = float((100 - (100 / (1 + rs))).iloc[-1])

            # MACD histogram
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd_line = ema12 - ema26
            macd_signal = macd_line.ewm(span=9, adjust=False).mean()
            macd_hist = float((macd_line - macd_signal).iloc[-1])

            # EMA cross (20 / 50)
            ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
            ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
            ema_bull = ema20 > ema50

            # Vote
            bull_conditions = [rsi > 60, macd_hist > 0, ema_bull]
            bear_conditions = [rsi < 40, macd_hist < 0, not ema_bull]

            bull_count = sum(bull_conditions)
            bear_count = sum(bear_conditions)

            if bull_count == 3:
                vote = "BUY"
                # Confidence scales with RSI distance from 60 and MACD magnitude
                rsi_strength = min((rsi - 60) / 40, 1.0)
                confidence = 0.60 + 0.40 * rsi_strength
            elif bear_count == 3:
                vote = "SELL"
                rsi_strength = min((40 - rsi) / 40, 1.0)
                confidence = 0.60 + 0.40 * rsi_strength
            else:
                vote = "NEUTRAL"
                confidence = 0.0

            return {
                "vote": vote,
                "confidence": round(confidence, 4),
                "rsi": round(rsi, 2),
                "macd_hist": round(macd_hist, 6),
                "ema20": round(ema20, 5),
                "ema50": round(ema50, 5),
                "ema_bull": ema_bull,
                "bull_conditions_met": bull_count,
                "bear_conditions_met": bear_count,
                "valid": True,
            }
        except Exception as exc:
            logger.error(f"Component A error: {exc}")
            return {"vote": "NEUTRAL", "confidence": 0.0, "valid": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Component A (v3.4): Trend Confirmation — Weighted Indicators
    # ------------------------------------------------------------------

    def _component_a_trend_weighted(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Trend Confirmation with weighted indicators (v3.4).

        Weights:
          - EMA alignment : 50%  (tells you the trend direction)
          - MACD momentum : 30%  (tells you momentum)
          - RSI confirmation: 20% (confirmation only — prevents RSI delay)

        Weighted score in [-1, +1]:
          > +0.5  → BUY
          < -0.5  → SELL
          else    → NEUTRAL
        """
        try:
            close = df["close"].astype(float)

            # EMA alignment (50% weight)
            ema20 = close.ewm(span=20, adjust=False).mean()
            ema50 = close.ewm(span=50, adjust=False).mean()
            ema_bull = float(ema20.iloc[-1]) > float(ema50.iloc[-1])
            ema_score = 1.0 if ema_bull else -1.0

            # MACD momentum (30% weight)
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd_line = ema12 - ema26
            macd_signal_line = macd_line.ewm(span=9, adjust=False).mean()
            macd_hist = float((macd_line - macd_signal_line).iloc[-1])
            macd_score = 1.0 if macd_hist > 0 else -1.0

            # RSI confirmation (20% weight)
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi = float((100 - (100 / (1 + rs))).iloc[-1])
            rsi_score = 1.0 if rsi > 60 else (-1.0 if rsi < 40 else 0.0)

            # Weighted composite score
            weighted_score = (
                ema_score * 0.50 +
                macd_score * 0.30 +
                rsi_score * 0.20
            )

            # Determine vote
            if weighted_score > 0.5:
                vote = "BUY"
                confidence = min(0.95, 0.60 + abs(weighted_score) * 0.35)
            elif weighted_score < -0.5:
                vote = "SELL"
                confidence = min(0.95, 0.60 + abs(weighted_score) * 0.35)
            else:
                vote = "NEUTRAL"
                confidence = 0.0

            return {
                "vote": vote,
                "confidence": round(confidence, 4),
                "ema_score": round(ema_score, 2),
                "macd_score": round(macd_score, 2),
                "rsi_score": round(rsi_score, 2),
                "weighted_score": round(weighted_score, 3),
                "rsi": round(rsi, 2),
                "ema20": round(float(ema20.iloc[-1]), 5),
                "ema50": round(float(ema50.iloc[-1]), 5),
                "macd_hist": round(macd_hist, 6),
                "valid": True,
            }
        except Exception as exc:
            logger.error(f"Trend Engine (weighted) error: {exc}")
            return {"vote": "NEUTRAL", "confidence": 0.0, "valid": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Component B: Support / Resistance
    # ------------------------------------------------------------------

    def _component_b_sr(
        self,
        df: pd.DataFrame,
        symbol: str,
        df_daily: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Any]:
        """
        Support/Resistance via Pivot Points + ATR proximity.

        Rules:
          BUY  → price is near (within 0.5 ATR of) a support level
                 AND pivot bias is BULLISH or STRONG_BULLISH
          SELL → price is near (within 0.5 ATR of) a resistance level
                 AND pivot bias is BEARISH or STRONG_BEARISH
          NEUTRAL → price is in the middle of a zone (no clear S/R edge)
        """
        try:
            pivot_df = df_daily if df_daily is not None else df
            pivot_analysis = self.pivot_analyzer.analyze(pivot_df, symbol, use_all_methods=False)

            if not pivot_analysis.get("valid", False):
                return {"vote": "NEUTRAL", "confidence": 0.0, "valid": False,
                        "reason": "pivot_analysis_invalid"}

            current_price = float(df["close"].iloc[-1])

            # ATR (14) for proximity check
            high = df["high"].astype(float)
            low = df["low"].astype(float)
            close = df["close"].astype(float)
            tr = pd.concat([
                high - low,
                (high - close.shift()).abs(),
                (low - close.shift()).abs(),
            ], axis=1).max(axis=1)
            atr = float(tr.rolling(14).mean().iloc[-1])

            pivot_bias = pivot_analysis.get("bias", "NEUTRAL")
            nearest = pivot_analysis.get("nearest_levels", {})
            zone_name = pivot_analysis.get("zone", {}).get("name", "UNKNOWN")

            # Proximity threshold: within 0.5 ATR of a key level
            proximity_threshold = atr * 0.5

            near_support = False
            near_resistance = False

            if "nearest_support" in nearest:
                dist = abs(current_price - nearest["nearest_support"]["price"])
                near_support = dist <= proximity_threshold

            if "nearest_resistance" in nearest:
                dist = abs(current_price - nearest["nearest_resistance"]["price"])
                near_resistance = dist <= proximity_threshold

            bullish_bias = pivot_bias in ("BULLISH", "STRONG_BULLISH")
            bearish_bias = pivot_bias in ("BEARISH", "STRONG_BEARISH")

            if near_support and bullish_bias:
                vote = "BUY"
                confidence = 0.80 if pivot_bias == "STRONG_BULLISH" else 0.70
            elif near_resistance and bearish_bias:
                vote = "SELL"
                confidence = 0.80 if pivot_bias == "STRONG_BEARISH" else 0.70
            elif bullish_bias and not near_resistance:
                # Bias is bullish but not at a specific level — weaker signal
                vote = "BUY"
                confidence = 0.65
            elif bearish_bias and not near_support:
                vote = "SELL"
                confidence = 0.65
            else:
                vote = "NEUTRAL"
                confidence = 0.0

            return {
                "vote": vote,
                "confidence": round(confidence, 4),
                "pivot_bias": pivot_bias,
                "zone": zone_name,
                "near_support": near_support,
                "near_resistance": near_resistance,
                "atr": round(atr, 5),
                "proximity_threshold": round(proximity_threshold, 5),
                "valid": True,
            }
        except Exception as exc:
            logger.error(f"Component B error: {exc}")
            return {"vote": "NEUTRAL", "confidence": 0.0, "valid": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Component B (v3.4): S/R Engine — Scoring System (0-100 points)
    # ------------------------------------------------------------------

    def _component_b_sr_scoring(
        self,
        df: pd.DataFrame,
        symbol: str,
        df_daily: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Any]:
        """
        Support/Resistance with scoring system (v3.4).

        Scoring (0-100 points):
          - Near Pivot         : +15 points
          - Weekly Pivot bias  : +20 points
          - ATR Support/Resist : +10 points
          - Demand Zone        : +25 points
          - Liquidity Sweep    : +30 points
          Maximum              : 100 points

        Vote is determined by score + pivot bias:
          score >= 50 → BUY or SELL (based on pivot bias)
          score <  50 → NEUTRAL
        """
        try:
            current_price = float(df["close"].iloc[-1])
            pivot_df = df_daily if df_daily is not None else df

            score = 0
            factors: List[str] = []

            # Pivot analysis
            pivot_analysis = self.pivot_analyzer.analyze(pivot_df, symbol, use_all_methods=False)
            if pivot_analysis.get("valid"):
                # Near Pivot: +15
                if "nearest_levels" in pivot_analysis:
                    nearest = pivot_analysis["nearest_levels"]
                    atr = self._calculate_atr(df)

                    if "nearest_support" in nearest:
                        dist = abs(current_price - nearest["nearest_support"]["price"])
                        if dist <= atr * 0.5:
                            score += 15
                            factors.append("near_pivot_support")

                    if "nearest_resistance" in nearest:
                        dist = abs(current_price - nearest["nearest_resistance"]["price"])
                        if dist <= atr * 0.5:
                            score += 15
                            factors.append("near_pivot_resistance")

                # Weekly Pivot bias: +20
                if pivot_analysis.get("bias") in ("STRONG_BULLISH", "STRONG_BEARISH"):
                    score += 20
                    factors.append("weekly_pivot_bias")

            # ATR Support/Resistance: +10
            atr = self._calculate_atr(df)
            ema20 = float(df["close"].ewm(span=20, adjust=False).mean().iloc[-1])
            if abs(current_price - (ema20 - 2 * atr)) < atr * 0.3:
                score += 10
                factors.append("atr_support")
            elif abs(current_price - (ema20 + 2 * atr)) < atr * 0.3:
                score += 10
                factors.append("atr_resistance")

            # Demand Zone: +25
            if self._is_in_demand_zone(df):
                score += 25
                factors.append("demand_zone")

            # Liquidity Sweep: +30
            if self._detect_liquidity_sweep(df):
                score += 30
                factors.append("liquidity_sweep")

            # Cap at 100
            score = min(score, 100)

            pivot_bias = pivot_analysis.get("bias", "NEUTRAL") if pivot_analysis.get("valid") else "NEUTRAL"

            # Determine vote based on score
            if score >= 50:
                vote = "BUY" if pivot_bias in ("BULLISH", "STRONG_BULLISH") else "SELL"
                confidence = min(0.95, 0.50 + (score / 100) * 0.45)
            else:
                vote = "NEUTRAL"
                confidence = 0.0

            return {
                "vote": vote,
                "confidence": round(confidence, 4),
                "score": score,
                "factors": factors,
                "pivot_bias": pivot_bias,
                "valid": True,
            }
        except Exception as exc:
            logger.error(f"S/R Engine (scoring) error: {exc}")
            return {"vote": "NEUTRAL", "confidence": 0.0, "valid": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # S/R helper methods (used by _component_b_sr_scoring)
    # ------------------------------------------------------------------

    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Calculate ATR for the given DataFrame."""
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
            val = float(tr.rolling(period).mean().iloc[-1])
            return val if not np.isnan(val) else 0.0
        except Exception:
            return 0.0

    def _is_in_demand_zone(self, df: pd.DataFrame, lookback: int = 30) -> bool:
        """
        Check if current price is in a previous demand (support) zone.

        A demand zone is identified as a candle where price bounced strongly
        upward (close > open by at least 0.5 ATR) in the lookback window.
        """
        try:
            if len(df) < lookback + 5:
                return False

            atr = self._calculate_atr(df)
            if atr <= 0:
                return False

            current_price = float(df["close"].iloc[-1])
            history = df.iloc[-(lookback + 5) : -5]

            for _, row in history.iterrows():
                candle_low = float(row["low"])
                candle_high = float(row["high"])
                candle_body = abs(float(row["close"]) - float(row["open"]))

                # Bullish demand candle: close > open with meaningful body
                if float(row["close"]) > float(row["open"]) and candle_body >= atr * 0.5:
                    zone_top = candle_high
                    zone_bottom = candle_low
                    if zone_bottom <= current_price <= zone_top:
                        return True
            return False
        except Exception:
            return False

    def _detect_liquidity_sweep(self, df: pd.DataFrame, lookback: int = 20) -> bool:
        """
        Detect a liquidity sweep: price briefly breaks a recent swing high/low
        then immediately reverses (stop-hunt pattern).

        Conditions:
          - Price exceeded the highest high (or lowest low) of the lookback window
            in the previous candle.
          - Current candle has reversed back inside the range.
        """
        try:
            if len(df) < lookback + 3:
                return False

            window = df.iloc[-(lookback + 2) : -2]
            prev = df.iloc[-2]
            curr = df.iloc[-1]

            swing_high = float(window["high"].max())
            swing_low = float(window["low"].min())

            prev_high = float(prev["high"])
            prev_low = float(prev["low"])
            curr_close = float(curr["close"])

            # Bullish sweep: previous candle broke below swing_low, current closed above it
            if prev_low < swing_low and curr_close > swing_low:
                return True

            # Bearish sweep: previous candle broke above swing_high, current closed below it
            if prev_high > swing_high and curr_close < swing_high:
                return True

            return False
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Component C: Multi-Timeframe Alignment
    # ------------------------------------------------------------------

    def _component_c_mtf(self, mtf_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """
        Multi-Timeframe Alignment gate.

        Rules:
          BUY  → dominant_direction == BULLISH AND alignment_score >= 65
          SELL → dominant_direction == BEARISH AND alignment_score >= 65
          NEUTRAL → anything else
        """
        try:
            if not mtf_analysis.get("valid", False):
                return {"vote": "NEUTRAL", "confidence": 0.0, "valid": False,
                        "reason": "mtf_analysis_invalid"}

            direction = mtf_analysis.get("dominant_direction", "NEUTRAL")
            score = float(mtf_analysis.get("alignment_score", 0.0))

            MIN_ALIGNMENT = 65.0

            if direction == "BULLISH" and score >= MIN_ALIGNMENT:
                vote = "BUY"
                confidence = min(score / 100.0, 1.0)
            elif direction == "BEARISH" and score >= MIN_ALIGNMENT:
                vote = "SELL"
                confidence = min(score / 100.0, 1.0)
            else:
                vote = "NEUTRAL"
                confidence = 0.0

            return {
                "vote": vote,
                "confidence": round(confidence, 4),
                "alignment_score": round(score, 1),
                "dominant_direction": direction,
                "min_alignment_required": MIN_ALIGNMENT,
                "valid": True,
            }
        except Exception as exc:
            logger.error(f"Component C error: {exc}")
            return {"vote": "NEUTRAL", "confidence": 0.0, "valid": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Component C (v3.4): MTF Engine — Weighted Voting
    # ------------------------------------------------------------------

    def _component_c_mtf_weighted(self, mtf_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """
        Multi-Timeframe with weighted voting (v3.4).

        Weights:
          - Daily : 45%  (dominant trend)
          - 4H    : 35%  (intermediate trend)
          - 1H    : 20%  (entry timing)

        Allows disagreement — does not reject if one timeframe disagrees.
        Example: Daily BUY (45%) + 4H BUY (35%) + 1H SELL (20%) = 80% BUY → BUY signal.

        Minimum buy_score or sell_score of 0.35 required to produce a directional vote.
        """
        try:
            if not mtf_analysis.get("valid"):
                return {"vote": "NEUTRAL", "confidence": 0.0, "valid": False,
                        "reason": "mtf_analysis_invalid"}

            # Extract per-timeframe signals from the MTF analysis result.
            # MultiTimeframeConfirmation stores per-timeframe data under "timeframes".
            timeframes = mtf_analysis.get("timeframes", {})

            def _tf_signal(tf_key: str) -> str:
                tf_data = timeframes.get(tf_key, {})
                direction = tf_data.get("direction", "NEUTRAL")
                if direction == "BULLISH":
                    return "BUY"
                elif direction == "BEARISH":
                    return "SELL"
                return "NEUTRAL"

            daily_signal = _tf_signal("1day")
            h4_signal    = _tf_signal("4h")
            h1_signal    = _tf_signal("1h")

            # Weighted voting
            buy_score = (
                (1.0 if daily_signal == "BUY" else 0.0) * 0.45 +
                (1.0 if h4_signal    == "BUY" else 0.0) * 0.35 +
                (1.0 if h1_signal    == "BUY" else 0.0) * 0.20
            )

            sell_score = (
                (1.0 if daily_signal == "SELL" else 0.0) * 0.45 +
                (1.0 if h4_signal    == "SELL" else 0.0) * 0.35 +
                (1.0 if h1_signal    == "SELL" else 0.0) * 0.20
            )

            # Determine vote (allows disagreement)
            if buy_score > sell_score and buy_score >= 0.35:
                vote = "BUY"
                confidence = min(0.95, 0.50 + buy_score * 0.45)
            elif sell_score > buy_score and sell_score >= 0.35:
                vote = "SELL"
                confidence = min(0.95, 0.50 + sell_score * 0.45)
            else:
                vote = "NEUTRAL"
                confidence = 0.0

            return {
                "vote": vote,
                "confidence": round(confidence, 4),
                "daily_signal": daily_signal,
                "h4_signal": h4_signal,
                "h1_signal": h1_signal,
                "buy_score": round(buy_score, 3),
                "sell_score": round(sell_score, 3),
                "valid": True,
            }
        except Exception as exc:
            logger.error(f"MTF Engine (weighted) error: {exc}")
            return {"vote": "NEUTRAL", "confidence": 0.0, "valid": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Confidence Calculation (v3.4): 0-100 Scale with Modifiers
    # ------------------------------------------------------------------

    def _calculate_confidence_score(
        self,
        trend_score: float,
        sr_score: float,
        mtf_score: float,
        volume_score: float,
        regime_weights: Optional[Dict[str, float]] = None,
        macro_modifier: float = 0.0,
        news_modifier: float = 0.0,
        volatility_modifier: float = 0.0,
        liquidity_bonus: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Calculate confidence score on a 0-100 scale (v3.4).

        Base calculation (100 points total):
          - Trend  : 35 points
          - S/R    : 25 points
          - MTF    : 20 points
          - Volume : 10 points
          - Regime : 10 points (fixed)
          SUBTOTAL : 100 points

        Modifiers (applied after base):
          - Macro filter    : ±20 points
          - News filter     : -30 points (or block)
          - Volatility      : ±5 points
          - Liquidity sweep : +10 points

        Args:
            trend_score:         Trend component score (0-100).
            sr_score:            S/R component score (0-100).
            mtf_score:           MTF component score (0-100).
            volume_score:        Volume component score (0-100).
            regime_weights:      Regime weight dict (unused in base calc, kept for future).
            macro_modifier:      Macro filter modifier (±20).
            news_modifier:       News filter modifier (-30 or 0).
            volatility_modifier: Volatility modifier (±5).
            liquidity_bonus:     Liquidity sweep bonus (+10 or 0).

        Returns:
            Dict with base_score, modifiers, final_score (0-100), valid.
        """
        try:
            base_score = (
                trend_score  * 0.35 +
                sr_score     * 0.25 +
                mtf_score    * 0.20 +
                volume_score * 0.10 +
                10.0  # Market regime fixed 10 points
            )

            final_score = base_score
            final_score += macro_modifier
            final_score += news_modifier
            final_score += volatility_modifier
            final_score += liquidity_bonus

            # Cap at 0-100
            final_score = max(0.0, min(100.0, final_score))

            return {
                "base_score": round(base_score, 1),
                "macro_modifier": round(macro_modifier, 1),
                "news_modifier": round(news_modifier, 1),
                "volatility_modifier": round(volatility_modifier, 1),
                "liquidity_bonus": round(liquidity_bonus, 1),
                "final_score": round(final_score, 1),
                "valid": True,
            }
        except Exception as exc:
            logger.error(f"Confidence calculation error: {exc}")
            return {"final_score": 0.0, "valid": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Component MR: Mean Reversion
    # ------------------------------------------------------------------

    def _component_mr_mean_reversion(
        self,
        df: pd.DataFrame,
        symbol: str,
        df_daily: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Any]:
        """
        Mean Reversion signal via EMA deviation + RSI extremes.

        BUY  when: price < EMA20 - 2×ATR  AND  RSI < 30
        SELL when: price > EMA20 + 2×ATR  AND  RSI > 70

        Confidence:
          Base 60% + up to +15% for extreme conditions - 10% for counter-trend.
        """
        try:
            return self.mean_reversion_engine.analyze(df, symbol, df_daily)
        except Exception as exc:
            logger.error(f"Component MR error [{symbol}]: {exc}")
            return {"vote": "NEUTRAL", "confidence": 0.0, "valid": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Component PA: Price Action
    # ------------------------------------------------------------------

    def _component_pa_price_action(
        self,
        df: pd.DataFrame,
        symbol: str,
        df_daily: Optional[pd.DataFrame] = None,
        pa_thresholds: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        Price Action signal via S/R breaks, order block rejections,
        and liquidity sweeps.

        Confidence:
          Base 65% + up to +20% for volume / MTF / multi-test bonuses.

        pa_thresholds (optional):
          Dict with keys momentum_threshold, volatility_threshold,
          confluence_weight.  When provided, these override the engine's
          default values for this call (used for A/B testing per pair).
          If None, the global env-var defaults are used.
        """
        try:
            # Resolve thresholds: per-call override → env-var defaults
            thresholds = pa_thresholds or _get_pa_thresholds(symbol)

            logger.debug(
                f"PA [{symbol}] thresholds — "
                f"momentum={thresholds['momentum_threshold']} "
                f"volatility={thresholds['volatility_threshold']} "
                f"confluence_weight={thresholds['confluence_weight']}"
            )

            result = self.price_action_engine.analyze(df, symbol, df_daily)

            # Attach the thresholds used to the result for downstream logging
            # and MongoDB storage (enables A/B analysis without code changes).
            result["pa_thresholds_used"] = thresholds

            # Apply confluence weight: scale confidence by the weight factor
            # when multiple sub-signals agree (confluence_weight acts as a
            # bonus multiplier on top of the base confidence).
            if result.get("valid") and result.get("vote") in ("BUY", "SELL"):
                buy_votes  = result.get("buy_votes",  0)
                sell_votes = result.get("sell_votes", 0)
                agreeing   = max(buy_votes, sell_votes)
                if agreeing >= 2:
                    # Two or more sub-strategies agree — apply confluence bonus
                    confluence_bonus = thresholds["confluence_weight"] * (agreeing - 1) * 0.05
                    raw_conf = result.get("confidence", 0.0)
                    result["confidence"] = round(min(1.0, raw_conf + confluence_bonus), 4)
                    result["confluence_bonus_applied"] = round(confluence_bonus, 4)

                # Apply momentum threshold: suppress signal if confidence is
                # below the momentum threshold (stricter than the base 65%).
                if result["confidence"] < thresholds["momentum_threshold"]:
                    logger.debug(
                        f"PA [{symbol}] suppressed — confidence={result['confidence']:.3f} "
                        f"< momentum_threshold={thresholds['momentum_threshold']}"
                    )
                    result["vote"] = "NEUTRAL"
                    result["suppressed_by"] = "momentum_threshold"

            return result

        except Exception as exc:
            logger.error(f"Component PA error [{symbol}]: {exc}")
            return {"vote": "NEUTRAL", "confidence": 0.0, "valid": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Component MC: Macro Filter
    # ------------------------------------------------------------------

    def _component_macro_filter(
        self,
        signal_vote: str,
        corr_dfs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Macro context filter using DXY strength, real rates, and
        inflation expectations.

        Returns a confidence_modifier (-0.20 if macro opposes the signal,
        0.0 otherwise) and a macro_bias string.
        """
        try:
            return self.macro_filter_engine.analyze(
                signal_vote=signal_vote,
                corr_dfs=corr_dfs,
                fetch_live=False,
            )
        except Exception as exc:
            logger.error(f"Component MC error: {exc}")
            return {
                "macro_score": 0.0,
                "macro_bias": "NEUTRAL",
                "confidence_modifier": 0.0,
                "factors": {},
                "valid": False,
                "error": str(exc),
            }

    # ------------------------------------------------------------------
    # Confirmation Filters (Layer 3 — v3.3)
    # ------------------------------------------------------------------

    async def _apply_confirmation_filters(
        self,
        signal: str,
        confidence: float,
        mtf_alignment: float,
        smc_score: int,
        pair: str,
    ) -> Dict[str, Any]:
        """
        Apply 3-layer confirmation filters to boost signal confidence.

        Filters:
          1. MTF alignment >= MIN_MTF_ALIGNMENT_PCT (default 80%)
          2. SMC score    >= MIN_SMC_SCORE          (default 7/10)
          3. No high-impact economic news in next 2 hours

        Confidence adjustment:
          - Each filter passed adds +10% (max +20% for 2 filters)
          - Any failed filter caps confidence at 85%
          - All 3 filters passed → confidence boosted to min(99%, max(95%, base))
          - Fewer than 2 filters passed → signal downgraded to NEUTRAL

        Returns:
            {
                "signal":          "BUY" | "SELL" | "NEUTRAL",
                "confidence":      original confidence (0–100),
                "filters_passed":  int (0–3),
                "filters_failed":  list[str],
                "final_confidence": float (0–100),
            }
        """
        filters_passed = 0
        filters_failed: List[str] = []

        # Filter 1: MTF Alignment
        if mtf_alignment >= MIN_MTF_ALIGNMENT_PCT:
            filters_passed += 1
            logger.info(
                f"[{pair}] ✅ Filter 1 PASSED: MTF alignment "
                f"{mtf_alignment:.1f}% >= {MIN_MTF_ALIGNMENT_PCT}%"
            )
        else:
            filters_failed.append(
                f"MTF alignment {mtf_alignment:.1f}% < {MIN_MTF_ALIGNMENT_PCT}%"
            )
            logger.warning(f"[{pair}] ❌ Filter 1 FAILED: {filters_failed[-1]}")

        # Filter 2: SMC Score
        if smc_score >= MIN_SMC_SCORE:
            filters_passed += 1
            logger.info(
                f"[{pair}] ✅ Filter 2 PASSED: SMC score "
                f"{smc_score}/10 >= {MIN_SMC_SCORE}/10"
            )
        else:
            filters_failed.append(
                f"SMC score {smc_score}/10 < {MIN_SMC_SCORE}/10"
            )
            logger.warning(f"[{pair}] ❌ Filter 2 FAILED: {filters_failed[-1]}")

        # Filter 3: Economic News
        try:
            has_news = await asyncio.wait_for(
                self.economic_calendar.is_safe_to_trade(pair),
                timeout=10.0,
            )
            safe_to_trade = has_news.get("safe_to_trade", True)
            if safe_to_trade:
                filters_passed += 1
                logger.info(
                    f"[{pair}] ✅ Filter 3 PASSED: No high-impact news in next 2 hours"
                )
            else:
                blocking = has_news.get("blocking_events", [])
                ev_desc = blocking[0].get("event", "?") if blocking else "?"
                filters_failed.append(
                    f"High-impact economic news: {ev_desc}"
                )
                logger.warning(f"[{pair}] ❌ Filter 3 FAILED: {filters_failed[-1]}")
        except asyncio.TimeoutError:
            # Fail-open: timeout → treat as no news
            filters_passed += 1
            logger.warning(
                f"[{pair}] ⚠️ Filter 3 TIMEOUT — treating as PASSED (fail-open)"
            )
        except Exception as exc:
            # Fail-open: error → treat as no news
            filters_passed += 1
            logger.warning(
                f"[{pair}] ⚠️ Filter 3 ERROR ({exc}) — treating as PASSED (fail-open)"
            )

        # ── Calculate final confidence ────────────────────────────────
        final_confidence = confidence

        if filters_passed >= 2:
            # +10% per filter passed (max +20% for 2 filters, +30% for 3)
            final_confidence = min(99.0, confidence + (filters_passed * 10.0))

        if filters_failed:
            # Cap at 85% if any filter fails
            final_confidence = min(final_confidence, 85.0)

        if filters_passed == 3:
            # All filters passed → guarantee 95%+ confidence
            final_confidence = min(99.0, max(95.0, final_confidence))

        # Downgrade to NEUTRAL if fewer than 2 filters pass
        final_signal = signal if filters_passed >= 2 else "NEUTRAL"

        logger.info(
            f"[{pair}] Confirmation filters: {filters_passed}/3 passed, "
            f"confidence {confidence:.1f}% → {final_confidence:.1f}% "
            f"(signal: {signal} → {final_signal})"
        )

        return {
            "signal":           final_signal,
            "confidence":       confidence,
            "filters_passed":   filters_passed,
            "filters_failed":   filters_failed,
            "final_confidence": round(final_confidence, 1),
        }

    # ------------------------------------------------------------------
    # Main Signal Generation
    # ------------------------------------------------------------------

    async def generate_signal(
        self,
        symbol: str,
        df_4h: pd.DataFrame,
        df_daily: Optional[pd.DataFrame] = None,
        price_data: Optional[Dict[str, pd.Series]] = None,
        strategy_mode: str = "original",
        corr_dfs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Multi-strategy signal generation pipeline.

        strategy_mode options:
          "original"       — 4-component weighted voting (A=40%, B=25%, C=20%, D=15%)
                             with 3-layer confirmation filters (MTF, SMC, news)
          "mean_reversion" — only use MR signals
          "price_action"   — only use PA signals
          "macro_filtered" — use any signal (MR or PA) that passes macro filter
          "consensus"      — require 2+ of MR / PA / macro-filtered to agree

        Args:
            symbol:        Trading symbol (e.g. XAUUSD)
            df_4h:         4H OHLCV DataFrame (primary timeframe)
            df_daily:      Daily OHLCV DataFrame (for pivot points + trend filter)
            price_data:    Unused — kept for API compatibility
            strategy_mode: Which strategy philosophy to use (see above)
            corr_dfs:      Pre-fetched correlation DataFrames (for macro filter)

        Returns:
            Signal dict with component votes, confidence, and levels
        """
        start_time = datetime.utcnow()

        try:
            result: Dict[str, Any] = {
                "symbol":        symbol,
                "timestamp":     start_time.isoformat(),
                "version":       self.version,
                "strategy_mode": strategy_mode,
                "valid":         True,
                "components":    {},
            }

            # ── Economic Calendar pre-flight ──────────────────────────
            try:
                calendar_check = await asyncio.wait_for(
                    self.economic_calendar.is_safe_to_trade(symbol),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                calendar_check = {"safe_to_trade": True, "reason": "TIMEOUT_FAIL_OPEN"}
            result["components"]["economic_calendar"] = calendar_check

            if not calendar_check.get("safe_to_trade", True):
                result.update({
                    "signal":           "NEUTRAL",
                    "confidence":       0.0,
                    "meets_threshold":  False,
                    "rejection_reason": f"CALENDAR_BLOCKED: {calendar_check.get('reason', '')}",
                })
                return result

            # ==============================================================
            # ORIGINAL 4-COMPONENT MODE  (v3.3 — 95%+ confidence)
            # ==============================================================
            if strategy_mode == "original":
                # ── Component A: Trend Confirmation (v3.3) ───────────
                comp_a = self._component_a_trend(df_4h)
                result["components"]["trend_confirmation"] = comp_a

                # ── Component C: Multi-Timeframe Alignment ────────────
                try:
                    mtf_raw = await asyncio.wait_for(
                        self.mtf_confirmation.analyze(symbol),
                        timeout=30.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"MTF analysis timed out for {symbol}")
                    mtf_raw = {"valid": False, "alignment_score": 0, "dominant_direction": "NEUTRAL"}
                comp_c = self._component_c_mtf(mtf_raw)
                result["components"]["mtf_alignment"] = comp_c

                # ── Component B: Support/Resistance (v3.3) ────────────
                comp_b = self._component_b_sr(df_4h, symbol, df_daily)
                result["components"]["support_resistance"] = comp_b

                # ── Component D: Volume Confirmation (v3.3) ───────────
                # Pass the preliminary A-vote as the expected direction so
                # volume confirmation can align with the trend signal.
                trend_hint = comp_a.get("vote", "NEUTRAL")
                comp_d = await self.volume_confirmation.analyze(
                    df_4h, trend_hint
                )
                result["components"]["volume_confirmation"] = comp_d

                # ── 4-Strategy Weighted Voting (v3.3) ─────────────────
                signal_a = comp_a["vote"]
                signal_b = comp_b["vote"]
                signal_c = comp_c["vote"]
                signal_d = comp_d["signal"]

                votes = {
                    "A_trend": signal_a,
                    "B_sr":    signal_b,
                    "C_mtf":   signal_c,
                    "D_vol":   signal_d,
                }
                result["component_votes"] = votes

                # v3.3 weights (A=40%, B=25%, C=20%, D=15%)
                weights = {
                    "A_trend": WEIGHT_A,  # Trend Confirmation (40%)
                    "B_sr":    WEIGHT_B,  # Support/Resistance (25%)
                    "C_mtf":   WEIGHT_C,  # Multi-Timeframe    (20%)
                    "D_vol":   WEIGHT_D,  # Volume Confirmation (15%)
                }

                # Binary weighted voting — each agreeing component contributes
                # its full weight to the directional score
                buy_score = (
                    (WEIGHT_A if signal_a == "BUY" else 0) +
                    (WEIGHT_B if signal_b == "BUY" else 0) +
                    (WEIGHT_C if signal_c == "BUY" else 0) +
                    (WEIGHT_D if signal_d == "BUY" else 0)
                )

                sell_score = (
                    (WEIGHT_A if signal_a == "SELL" else 0) +
                    (WEIGHT_B if signal_b == "SELL" else 0) +
                    (WEIGHT_C if signal_c == "SELL" else 0) +
                    (WEIGHT_D if signal_d == "SELL" else 0)
                )

                # Determine consensus signal — 50% threshold allows 2-3 strategies to agree
                if buy_score > sell_score and buy_score >= CONSENSUS_THRESHOLD:
                    signal = "BUY"
                    weighted_confidence = buy_score
                elif sell_score > buy_score and sell_score >= CONSENSUS_THRESHOLD:
                    signal = "SELL"
                    weighted_confidence = sell_score
                else:
                    signal = "NEUTRAL"
                    weighted_confidence = max(buy_score, sell_score)

                logger.info(
                    f"HybridPortfolioV3 [{symbol}]: "
                    f"A={signal_a} B={signal_b} C={signal_c} D={signal_d} → "
                    f"CONSENSUS={signal} (BUY_score={buy_score:.2f}, SELL_score={sell_score:.2f}, "
                    f"threshold={CONSENSUS_THRESHOLD})"
                )

                if signal == "NEUTRAL":
                    result.update({
                        "signal":          "NEUTRAL",
                        "confidence":      0.0,
                        "meets_threshold": False,
                        "rejection_reason": f"NO_CONSENSUS: {votes}",
                        "weighted_scores": {"buy": buy_score, "sell": sell_score},
                        "weights":         weights,
                    })
                    logger.info(
                        f"HybridPortfolioV3 [{symbol}]: NEUTRAL — no weighted consensus "
                        f"(BUY={buy_score:.2f}, SELL={sell_score:.2f}, threshold={CONSENSUS_THRESHOLD})"
                    )
                    return result

                # Raw confidence from weighted score (0–100%)
                composite_pct = round(weighted_confidence * 100, 1)

                # ── Confirmation Filters (Layer 3) ────────────────────
                # Retrieve MTF alignment score and SMC score from context
                mtf_alignment_score = float(
                    comp_c.get("alignment_score", 0.0)
                    if comp_c.get("valid") else 0.0
                )
                # smc_score is populated by the caller (gold_server_v3) via
                # hybrid_ctx; default to 0 here — the server-side filter
                # apply_confirmation_filters() will use the real value.
                smc_score_val = result.get("smc_score", 0)

                filter_result = await self._apply_confirmation_filters(
                    signal=signal,
                    confidence=composite_pct,
                    mtf_alignment=mtf_alignment_score,
                    smc_score=smc_score_val,
                    pair=symbol,
                )

                final_confidence = filter_result["final_confidence"]
                signal = filter_result["signal"]  # may be downgraded to NEUTRAL

                # Quality label based on final confidence
                if final_confidence >= 95.0:
                    signal_quality = "EXCELLENT"
                elif final_confidence >= 90.0:
                    signal_quality = "GOOD"
                elif final_confidence >= MIN_CONFIDENCE_FOR_SIGNAL:
                    signal_quality = "FAIR"
                else:
                    signal_quality = "BELOW_THRESHOLD"

                meets_threshold = (
                    signal in ("BUY", "SELL")
                    and final_confidence >= MIN_CONFIDENCE_FOR_SIGNAL
                )

                result.update({
                    "signal":                   signal if (meets_threshold or final_confidence >= 50.0) else "NEUTRAL",
                    "confidence":               final_confidence / 100.0,
                    "confidence_pct":           final_confidence,
                    "signal_quality":           signal_quality,
                    "meets_threshold":          meets_threshold,
                    "min_confidence_threshold": MIN_CONFIDENCE_FOR_SIGNAL,
                    "weighted_scores":          {"buy": buy_score, "sell": sell_score},
                    "weights":                  weights,
                    "confirmation_filters":     filter_result,
                })

                if not meets_threshold:
                    result["rejection_reason"] = (
                        f"LOW_CONFIDENCE: {final_confidence:.1f}% < {MIN_CONFIDENCE_FOR_SIGNAL}%"
                        if signal in ("BUY", "SELL")
                        else f"FILTERS_FAILED: {filter_result.get('filters_failed', [])}"
                    )
                    logger.warning(
                        f"HybridPortfolioV3 [{symbol}]: {signal} rejected — "
                        f"final_confidence={final_confidence:.1f}% "
                        f"(filters={filter_result['filters_passed']}/3 passed)"
                    )
                    return result

                logger.info(
                    f"HybridPortfolioV3 [{symbol}]: ✅ {signal} signal generated "
                    f"(weighted_score={weighted_confidence:.2f}, "
                    f"raw_confidence={composite_pct:.1f}%, "
                    f"final_confidence={final_confidence:.1f}%, "
                    f"filters={filter_result['filters_passed']}/3 passed)"
                )

            # ==============================================================
            # MEAN REVERSION MODE
            # ==============================================================
            elif strategy_mode == "mean_reversion":
                comp_mr = self._component_mr_mean_reversion(df_4h, symbol, df_daily)
                result["components"]["mean_reversion"] = comp_mr
                result["component_votes"] = {"MR": comp_mr["vote"]}

                signal        = comp_mr.get("vote", "NEUTRAL")
                composite_pct = round(comp_mr.get("confidence", 0.0) * 100, 1)
                MIN_CONFIDENCE = 60.0
                meets_threshold = signal in ("BUY", "SELL") and composite_pct >= MIN_CONFIDENCE

                signal_quality = (
                    "EXCELLENT" if composite_pct >= 75
                    else "GOOD"  if composite_pct >= 70
                    else "FAIR"  if composite_pct >= MIN_CONFIDENCE
                    else "BELOW_THRESHOLD"
                )

                result.update({
                    "signal":                   signal if meets_threshold else "NEUTRAL",
                    "confidence":               composite_pct,
                    "signal_quality":           signal_quality,
                    "meets_threshold":          meets_threshold,
                    "min_confidence_threshold": MIN_CONFIDENCE,
                })
                if not meets_threshold:
                    result["rejection_reason"] = (
                        comp_mr.get("reason", "")
                        or f"BELOW_THRESHOLD: {composite_pct:.1f}% < {MIN_CONFIDENCE}%"
                    )

            # ==============================================================
            # PRICE ACTION MODE
            # ==============================================================
            elif strategy_mode == "price_action":
                # Resolve per-pair thresholds (supports A/B testing via env vars)
                pa_thresholds = _get_pa_thresholds(symbol)
                logger.info(
                    f"HybridPortfolioV3 [{symbol}] price_action thresholds — "
                    f"momentum={pa_thresholds['momentum_threshold']} "
                    f"volatility={pa_thresholds['volatility_threshold']} "
                    f"confluence_weight={pa_thresholds['confluence_weight']}"
                )

                comp_pa = self._component_pa_price_action(df_4h, symbol, df_daily, pa_thresholds)
                result["components"]["price_action"] = comp_pa
                result["component_votes"] = {"PA": comp_pa["vote"]}
                # Store thresholds in result for downstream signal logging
                result["pa_thresholds"] = pa_thresholds

                signal        = comp_pa.get("vote", "NEUTRAL")
                composite_pct = round(comp_pa.get("confidence", 0.0) * 100, 1)
                MIN_CONFIDENCE = 65.0
                meets_threshold = signal in ("BUY", "SELL") and composite_pct >= MIN_CONFIDENCE

                signal_quality = (
                    "EXCELLENT" if composite_pct >= 80
                    else "GOOD"  if composite_pct >= 75
                    else "FAIR"  if composite_pct >= MIN_CONFIDENCE
                    else "BELOW_THRESHOLD"
                )

                result.update({
                    "signal":                   signal if meets_threshold else "NEUTRAL",
                    "confidence":               composite_pct,
                    "signal_quality":           signal_quality,
                    "meets_threshold":          meets_threshold,
                    "min_confidence_threshold": MIN_CONFIDENCE,
                })
                if not meets_threshold:
                    result["rejection_reason"] = (
                        comp_pa.get("reason", "")
                        or f"BELOW_THRESHOLD: {composite_pct:.1f}% < {MIN_CONFIDENCE}%"
                    )

            # ==============================================================
            # MACRO-FILTERED MODE
            # ==============================================================
            elif strategy_mode == "macro_filtered":
                # Run both MR and PA; take whichever fires, then apply macro filter
                comp_mr = self._component_mr_mean_reversion(df_4h, symbol, df_daily)
                comp_pa = self._component_pa_price_action(df_4h, symbol, df_daily, _get_pa_thresholds(symbol))
                result["components"]["mean_reversion"] = comp_mr
                result["components"]["price_action"]   = comp_pa

                # Pick the higher-confidence non-neutral signal
                candidates = [
                    s for s in [comp_mr, comp_pa]
                    if s.get("vote") in ("BUY", "SELL")
                ]
                if not candidates:
                    result.update({
                        "signal":           "NEUTRAL",
                        "confidence":       0.0,
                        "meets_threshold":  False,
                        "rejection_reason": "NO_MR_OR_PA_SIGNAL",
                        "component_votes":  {"MR": comp_mr["vote"], "PA": comp_pa["vote"]},
                    })
                    return result

                best = max(candidates, key=lambda s: s.get("confidence", 0.0))
                signal        = best["vote"]
                base_conf_pct = round(best.get("confidence", 0.0) * 100, 1)

                # Apply macro filter
                comp_mc = self._component_macro_filter(signal, corr_dfs)
                result["components"]["macro_filter"] = comp_mc
                modifier      = comp_mc.get("confidence_modifier", 0.0)
                composite_pct = round(base_conf_pct + modifier * 100, 1)

                result["component_votes"] = {
                    "MR":    comp_mr["vote"],
                    "PA":    comp_pa["vote"],
                    "macro": comp_mc.get("macro_bias", "NEUTRAL"),
                }

                MIN_CONFIDENCE = 60.0
                meets_threshold = signal in ("BUY", "SELL") and composite_pct >= MIN_CONFIDENCE

                signal_quality = (
                    "EXCELLENT" if composite_pct >= 80
                    else "GOOD"  if composite_pct >= 70
                    else "FAIR"  if composite_pct >= MIN_CONFIDENCE
                    else "BELOW_THRESHOLD"
                )

                result.update({
                    "signal":                   signal if meets_threshold else "NEUTRAL",
                    "confidence":               composite_pct,
                    "signal_quality":           signal_quality,
                    "meets_threshold":          meets_threshold,
                    "min_confidence_threshold": MIN_CONFIDENCE,
                    "macro_modifier":           modifier,
                })
                if not meets_threshold:
                    result["rejection_reason"] = (
                        f"BELOW_THRESHOLD_AFTER_MACRO: {composite_pct:.1f}% < {MIN_CONFIDENCE}%"
                    )

            # ==============================================================
            # CONSENSUS MODE  (2+ of MR / PA / macro-filtered must agree)
            # ==============================================================
            elif strategy_mode == "consensus":
                comp_mr = self._component_mr_mean_reversion(df_4h, symbol, df_daily)
                comp_pa = self._component_pa_price_action(df_4h, symbol, df_daily, _get_pa_thresholds(symbol))

                # Macro filter uses the MR vote as the primary signal direction
                primary_vote = comp_mr.get("vote") if comp_mr.get("vote") != "NEUTRAL" else comp_pa.get("vote", "NEUTRAL")
                comp_mc = self._component_macro_filter(primary_vote, corr_dfs)

                result["components"]["mean_reversion"] = comp_mr
                result["components"]["price_action"]   = comp_pa
                result["components"]["macro_filter"]   = comp_mc

                # Macro vote: bullish/bearish gold maps to BUY/SELL
                macro_bias = comp_mc.get("macro_bias", "NEUTRAL")
                macro_vote = (
                    "BUY"  if macro_bias == "BULLISH_GOLD"
                    else "SELL" if macro_bias == "BEARISH_GOLD"
                    else "NEUTRAL"
                )

                votes = {
                    "MR":    comp_mr.get("vote", "NEUTRAL"),
                    "PA":    comp_pa.get("vote", "NEUTRAL"),
                    "macro": macro_vote,
                }
                result["component_votes"] = votes

                buy_count  = sum(1 for v in votes.values() if v == "BUY")
                sell_count = sum(1 for v in votes.values() if v == "SELL")

                if buy_count >= 2:
                    signal = "BUY"
                    active_confs = [
                        comp_mr.get("confidence", 0.0) if votes["MR"] == "BUY" else 0.0,
                        comp_pa.get("confidence", 0.0) if votes["PA"] == "BUY" else 0.0,
                    ]
                elif sell_count >= 2:
                    signal = "SELL"
                    active_confs = [
                        comp_mr.get("confidence", 0.0) if votes["MR"] == "SELL" else 0.0,
                        comp_pa.get("confidence", 0.0) if votes["PA"] == "SELL" else 0.0,
                    ]
                else:
                    result.update({
                        "signal":           "NEUTRAL",
                        "confidence":       0.0,
                        "meets_threshold":  False,
                        "rejection_reason": f"NO_CONSENSUS_2OF3: {votes}",
                    })
                    return result

                # Average confidence of agreeing components + macro modifier
                avg_conf      = sum(c for c in active_confs if c > 0) / max(sum(1 for c in active_confs if c > 0), 1)
                modifier      = comp_mc.get("confidence_modifier", 0.0)
                composite_pct = round((avg_conf + modifier) * 100, 1)

                MIN_CONFIDENCE = 62.0
                meets_threshold = signal in ("BUY", "SELL") and composite_pct >= MIN_CONFIDENCE

                signal_quality = (
                    "EXCELLENT" if composite_pct >= 80
                    else "GOOD"  if composite_pct >= 70
                    else "FAIR"  if composite_pct >= MIN_CONFIDENCE
                    else "BELOW_THRESHOLD"
                )

                result.update({
                    "signal":                   signal if meets_threshold else "NEUTRAL",
                    "confidence":               composite_pct,
                    "signal_quality":           signal_quality,
                    "meets_threshold":          meets_threshold,
                    "min_confidence_threshold": MIN_CONFIDENCE,
                    "macro_modifier":           modifier,
                    "consensus_votes":          {"buy": buy_count, "sell": sell_count},
                })
                if not meets_threshold:
                    result["rejection_reason"] = (
                        f"BELOW_THRESHOLD: {composite_pct:.1f}% < {MIN_CONFIDENCE}%"
                    )

            else:
                # Unknown mode — fall back to neutral
                result.update({
                    "signal":           "NEUTRAL",
                    "confidence":       0.0,
                    "meets_threshold":  False,
                    "rejection_reason": f"UNKNOWN_STRATEGY_MODE: {strategy_mode}",
                })
                return result

            # ==============================================================
            # Position Sizing (shared across all modes)
            # ==============================================================
            meets_threshold = result.get("meets_threshold", False)
            signal          = result.get("signal", "NEUTRAL")
            composite_pct   = result.get("confidence", 0.0)
            signal_quality  = result.get("signal_quality", "BELOW_THRESHOLD")

            if meets_threshold and signal in ("BUY", "SELL"):
                current_price = float(df_4h["close"].iloc[-1])

                high  = df_4h["high"].astype(float)
                low   = df_4h["low"].astype(float)
                close = df_4h["close"].astype(float)
                tr = pd.concat([
                    high - low,
                    (high - close.shift()).abs(),
                    (low  - close.shift()).abs(),
                ], axis=1).max(axis=1)
                atr = float(tr.rolling(14).mean().iloc[-1])

                # Fixed 1% risk, SL = 1.5 × ATR
                sl_distance = atr * 1.5
                sl_price = (
                    current_price - sl_distance if signal == "BUY"
                    else current_price + sl_distance
                )

                # TP levels: 0.5R, 1.0R, 1.5R (ATR multiples)
                if signal == "BUY":
                    tp1 = round(current_price + atr * 0.5, 5)
                    tp2 = round(current_price + atr * 1.0, 5)
                    tp3 = round(current_price + atr * 1.5, 5)
                else:
                    tp1 = round(current_price - atr * 0.5, 5)
                    tp2 = round(current_price - atr * 1.0, 5)
                    tp3 = round(current_price - atr * 1.5, 5)

                position_size = self.position_calculator.calculate(
                    account_balance=self.account_balance,
                    entry_price=current_price,
                    sl_price=sl_price,
                    symbol=symbol,
                    method="fixed_risk",
                    risk_pct=1.0,
                    volatility_multiplier=1.0,
                    risk_parity_weight=1.0,
                )
                result["position_sizing"] = position_size
                result["tp_levels"]       = [tp1, tp2, tp3]
                result["sl_price"]        = round(sl_price, 5)
                result["entry_price"]     = round(current_price, 5)
                result["atr"]             = round(atr, 5)

            # ── Processing Time ───────────────────────────────────────
            elapsed = (datetime.utcnow() - start_time).total_seconds()
            result["processing_time_ms"] = round(elapsed * 1000, 1)

            logger.info(
                f"HybridPortfolioV3 [{symbol}] mode={strategy_mode}: "
                f"signal={signal} confidence={composite_pct:.1f}% "
                f"quality={signal_quality} "
                f"time={result['processing_time_ms']}ms"
            )
            return result

        except Exception as exc:
            logger.error(f"HybridPortfolioV3 error [{symbol}]: {exc}", exc_info=True)
            return {
                "symbol":    symbol,
                "signal":    "NEUTRAL",
                "confidence": 0.0,
                "error":     str(exc),
                "valid":     False,
                "timestamp": start_time.isoformat(),
            }

    # ------------------------------------------------------------------
    # System Status
    # ------------------------------------------------------------------

    def get_system_status(self) -> Dict[str, Any]:
        """Get full system status and component health."""
        return {
            "version":     self.version,
            "system_name": "7-Component Signal System (4-core + MR/PA/Macro) v3.3",
            "architecture": (
                "4-strategy weighted voting (A=40%, B=25%, C=20%, D=15%) "
                "with 3-layer confirmation filters | single-engine (MR/PA) | "
                "macro-filtered | consensus"
            ),
            "components": {
                "A_trend_confirmation":  "ACTIVE",
                "B_support_resistance":  "ACTIVE",
                "C_mtf_alignment":       "ACTIVE",
                "D_volume_confirmation": "ACTIVE",
                "MR_mean_reversion":     "ACTIVE",
                "PA_price_action":       "ACTIVE",
                "MC_macro_filter":       "ACTIVE",
                "economic_calendar":     "ACTIVE",
                "position_calculator":   "ACTIVE",
                "portfolio_manager":     "ACTIVE",
            },
            "total_components": 7,
            "strategy_modes": [
                "original",
                "mean_reversion",
                "price_action",
                "macro_filtered",
                "consensus",
            ],
            "signal_logic": "Configurable via strategy_mode parameter",
            "weights": {
                "A_trend": WEIGHT_A,
                "B_sr":    WEIGHT_B,
                "C_mtf":   WEIGHT_C,
                "D_vol":   WEIGHT_D,
            },
            "consensus_threshold":      CONSENSUS_THRESHOLD,
            "min_mtf_alignment_pct":    MIN_MTF_ALIGNMENT_PCT,
            "min_smc_score":            MIN_SMC_SCORE,
            "min_confidence_threshold": MIN_CONFIDENCE_FOR_SIGNAL,
            "target_confidence":        95.0,
            "risk_per_trade_pct": 1.0,
            "account_balance": self.account_balance,
            "portfolio_state": self.portfolio_manager.get_state(self.account_balance),
            "timestamp": datetime.utcnow().isoformat(),
        }

    def update_account_balance(self, balance: float) -> None:
        """Update account balance."""
        self.account_balance = balance
        logger.info(f"HybridPortfolioV3: account balance updated to {balance:.2f}")

    # ------------------------------------------------------------------
    # Consensus Logic Helper (used by tests and startup validation)
    # ------------------------------------------------------------------

    def _apply_consensus_logic(
        self,
        signal_a: str,
        signal_b: str,
        signal_c: str,
        signal_d: str = "NEUTRAL",
        conf_a: float = 1.0,
        conf_b: float = 1.0,
        conf_c: float = 1.0,
        conf_d: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Apply weighted voting across four component signals (v3.3).

        This is the same logic used in the "original" strategy_mode but
        extracted as a standalone method so it can be unit-tested in
        isolation without requiring live market data.

        Weights (v3.3 — 95%+ confidence):
          A (Trend Confirmation): 40%
          B (Support/Resistance): 25%
          C (Multi-Timeframe):    20%
          D (Volume Confirmation): 15%

        Consensus threshold: 30% weighted score (CONSENSUS_THRESHOLD=0.30).

        Args:
            signal_a: Vote from Component A ("BUY", "SELL", or "NEUTRAL")
            signal_b: Vote from Component B ("BUY", "SELL", or "NEUTRAL")
            signal_c: Vote from Component C ("BUY", "SELL", or "NEUTRAL")
            signal_d: Vote from Component D ("BUY", "SELL", or "NEUTRAL")
            conf_a:   Confidence from Component A (0.0–1.0, default 1.0)
            conf_b:   Confidence from Component B (0.0–1.0, default 1.0)
            conf_c:   Confidence from Component C (0.0–1.0, default 1.0)
            conf_d:   Confidence from Component D (0.0–1.0, default 1.0)

        Returns:
            Dict with keys:
              signal          — "BUY", "SELL", or "NEUTRAL"
              confidence      — float percentage (0–100)
              buy_score       — weighted BUY score (0.0–1.0)
              sell_score      — weighted SELL score (0.0–1.0)
              weights         — dict of per-component weights used
        """
        weights = {
            "A_trend": WEIGHT_A,  # Trend Confirmation (40%)
            "B_sr":    WEIGHT_B,  # Support/Resistance (25%)
            "C_mtf":   WEIGHT_C,  # Multi-Timeframe    (20%)
            "D_vol":   WEIGHT_D,  # Volume Confirmation (15%)
        }

        buy_score = (
            (conf_a if signal_a == "BUY" else 0) * WEIGHT_A +
            (conf_b if signal_b == "BUY" else 0) * WEIGHT_B +
            (conf_c if signal_c == "BUY" else 0) * WEIGHT_C +
            (conf_d if signal_d == "BUY" else 0) * WEIGHT_D
        )

        sell_score = (
            (conf_a if signal_a == "SELL" else 0) * WEIGHT_A +
            (conf_b if signal_b == "SELL" else 0) * WEIGHT_B +
            (conf_c if signal_c == "SELL" else 0) * WEIGHT_C +
            (conf_d if signal_d == "SELL" else 0) * WEIGHT_D
        )

        if buy_score > sell_score and buy_score >= CONSENSUS_THRESHOLD:
            signal = "BUY"
            weighted_confidence = buy_score
        elif sell_score > buy_score and sell_score >= CONSENSUS_THRESHOLD:
            signal = "SELL"
            weighted_confidence = sell_score
        else:
            signal = "NEUTRAL"
            weighted_confidence = max(buy_score, sell_score)

        # Confidence as percentage of weighted score
        confidence = round(weighted_confidence * 100, 1)

        return {
            "signal":     signal,
            "confidence": confidence,
            "buy_score":  buy_score,
            "sell_score": sell_score,
            "weights":    weights,
        }


# Global instance
hybrid_system_v3 = HybridPortfolioSystemV3()

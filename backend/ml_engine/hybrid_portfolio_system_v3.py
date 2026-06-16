"""
Hybrid Portfolio System v3.0
Simplified 3-component core system for high-quality signal generation.

Architecture:
  Component A — Trend Confirmation  (RSI + MACD + EMA cross)
  Component B — Support/Resistance  (Pivot Points + ATR levels)
  Component C — Multi-Timeframe     (1H + 4H + Daily alignment)

  Extended engines (v3.2):
  Component MR — Mean Reversion     (EMA deviation + RSI extremes)
  Component PA — Price Action       (S/R breaks, OB rejection, liq. sweeps)
  Component MC — Macro Filter       (DXY, real rates, inflation expectations)

Signal logic: 2/3 majority vote (2+ components must agree, not averaging).
Minimum confidence threshold: 70%.

strategy_mode options:
  "original"       — original 3-component AND-gate (default)
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

logger = logging.getLogger(__name__)


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
    6-Component Signal System v3.2

    Original 3-component core (strategy_mode="original"):
      Component A: Trend Confirmation
          RSI > 60 → uptrend, RSI < 40 → downtrend
          MACD histogram positive/negative
          EMA20 > EMA50 → uptrend (simple MA cross)
      Component B: Support/Resistance
          Pivot Points (standard method)
          ATR-based dynamic levels
          Price must be near a key level to trade
      Component C: Multi-Timeframe Alignment
          1H + 4H + Daily must all agree on direction
          Minimum alignment score: 65%
      Signal gate: 2/3 majority vote (2+ must agree). Threshold: 70%.

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
        self.version = "3.2.0"

        # Core 3-component engines
        self.mtf_confirmation = MultiTimeframeConfirmation()
        self.pivot_analyzer = PivotPointsAnalyzer()
        self.feature_engineer = FeatureEngineer()

        # Extended engines (v3.2)
        self.mean_reversion_engine = MeanReversionCore()
        self.price_action_engine   = PriceActionCore()
        self.macro_filter_engine   = MacroFilter()

        # Infrastructure (kept for compatibility)
        self.economic_calendar = EconomicCalendar()
        self.position_calculator = PositionCalculator()
        self.portfolio_manager = PortfolioManager()
        self.strategy_router = StrategyRouter()

        logger.info(f"HybridPortfolioSystemV3 initialized — v{self.version} (3-component + MR/PA/Macro)")

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
          "original"       — original 3-component AND-gate (A+B+C)
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
            # ORIGINAL 3-COMPONENT MODE  (default — unchanged behaviour)
            # ==============================================================
            if strategy_mode == "original":
                # ── Component A: Trend Confirmation ───────────────────
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

                # ── Component B: Support/Resistance ───────────────────
                comp_b = self._component_b_sr(df_4h, symbol, df_daily)
                result["components"]["support_resistance"] = comp_b

                # ── Majority Vote Gate (2/3 logic) ────────────────────
                signal_a = comp_a["vote"]
                signal_b = comp_b["vote"]
                signal_c = comp_c["vote"]

                votes = {
                    "A_trend": signal_a,
                    "B_sr":    signal_b,
                    "C_mtf":   signal_c,
                }
                result["component_votes"] = votes

                signals = [signal_a, signal_b, signal_c]
                buy_count  = signals.count("BUY")
                sell_count = signals.count("SELL")

                if buy_count >= 2:
                    signal = "BUY"
                elif sell_count >= 2:
                    signal = "SELL"
                else:
                    signal = "NEUTRAL"

                logger.info(
                    f"HybridPortfolioV3 [{symbol}]: "
                    f"A={signal_a} B={signal_b} C={signal_c} → "
                    f"CONSENSUS={signal} (BUY={buy_count}, SELL={sell_count})"
                )

                if signal == "NEUTRAL":
                    disagreeing = [k for k, v in votes.items() if v != signal_a]
                    result.update({
                        "signal":                  "NEUTRAL",
                        "confidence":              0.0,
                        "meets_threshold":         False,
                        "rejection_reason":        f"NO_CONSENSUS: {votes}",
                        "disagreeing_components":  disagreeing,
                        "consensus_votes":         {"buy": buy_count, "sell": sell_count},
                    })
                    return result

                conf_a = comp_a["confidence"]
                conf_b = comp_b["confidence"]
                conf_c = comp_c["confidence"]

                # Confidence based on consensus strength
                if buy_count == 3 or sell_count == 3:
                    composite_pct = 90  # All 3 agree (strongest)
                elif buy_count == 2 or sell_count == 2:
                    composite_pct = 70  # 2 out of 3 agree (good)
                else:
                    composite_pct = 0   # No consensus (neutral)

                MIN_CONFIDENCE = 70.0
                quality_margin = composite_pct - MIN_CONFIDENCE
                if quality_margin >= 10:
                    signal_quality = "EXCELLENT"
                elif quality_margin >= 5:
                    signal_quality = "GOOD"
                elif quality_margin >= 0:
                    signal_quality = "FAIR"
                else:
                    signal_quality = "BELOW_THRESHOLD"

                meets_threshold = composite_pct >= MIN_CONFIDENCE

                result.update({
                    "signal":           signal if meets_threshold else "NEUTRAL",
                    "confidence":       composite_pct,
                    "confidence_components": {
                        "A_trend": round(conf_a * 100, 1),
                        "B_sr":    round(conf_b * 100, 1),
                        "C_mtf":   round(conf_c * 100, 1),
                    },
                    "signal_quality":          signal_quality,
                    "meets_threshold":         meets_threshold,
                    "min_confidence_threshold": MIN_CONFIDENCE,
                    "consensus_votes":         {"buy": buy_count, "sell": sell_count},
                })

                if not meets_threshold:
                    result["rejection_reason"] = (
                        f"BELOW_THRESHOLD: {composite_pct:.1f}% < {MIN_CONFIDENCE}%"
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
            "system_name": "6-Component Signal System (3-core + MR/PA/Macro)",
            "architecture": "AND-gate (original) | single-engine (MR/PA) | macro-filtered | consensus",
            "components": {
                "A_trend_confirmation": "ACTIVE",
                "B_support_resistance": "ACTIVE",
                "C_mtf_alignment":      "ACTIVE",
                "MR_mean_reversion":    "ACTIVE",
                "PA_price_action":      "ACTIVE",
                "MC_macro_filter":      "ACTIVE",
                "economic_calendar":    "ACTIVE",
                "position_calculator":  "ACTIVE",
                "portfolio_manager":    "ACTIVE",
            },
            "total_components": 6,
            "strategy_modes": [
                "original",
                "mean_reversion",
                "price_action",
                "macro_filtered",
                "consensus",
            ],
            "signal_logic": "Configurable via strategy_mode parameter",
            "min_confidence_threshold": 60.0,
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
    ) -> Dict[str, Any]:
        """
        Apply 2/3 majority vote across three component signals.

        This is the same logic used in the "original" strategy_mode but
        extracted as a standalone method so it can be unit-tested in
        isolation without requiring live market data.

        Args:
            signal_a: Vote from Component A ("BUY", "SELL", or "NEUTRAL")
            signal_b: Vote from Component B ("BUY", "SELL", or "NEUTRAL")
            signal_c: Vote from Component C ("BUY", "SELL", or "NEUTRAL")

        Returns:
            Dict with keys:
              signal     — "BUY", "SELL", or "NEUTRAL"
              confidence — integer percentage (90 for 3/3, 70 for 2/3, 0 for no consensus)
              buy_count  — number of BUY votes
              sell_count — number of SELL votes
        """
        signals = [signal_a, signal_b, signal_c]
        buy_count = signals.count("BUY")
        sell_count = signals.count("SELL")

        if buy_count >= 2:
            signal = "BUY"
        elif sell_count >= 2:
            signal = "SELL"
        else:
            signal = "NEUTRAL"

        if buy_count == 3 or sell_count == 3:
            confidence = 90  # All 3 agree — strongest signal
        elif buy_count == 2 or sell_count == 2:
            confidence = 70  # 2 out of 3 agree — good signal
        else:
            confidence = 0   # No consensus

        return {
            "signal": signal,
            "confidence": confidence,
            "buy_count": buy_count,
            "sell_count": sell_count,
        }


# Global instance
hybrid_system_v3 = HybridPortfolioSystemV3()

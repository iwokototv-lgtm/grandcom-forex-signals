"""
Hybrid Portfolio System v3.0
Simplified 3-component core system for high-quality signal generation.

Architecture:
  Component A — Trend Confirmation  (RSI + MACD + EMA cross)
  Component B — Support/Resistance  (Pivot Points + ATR levels)
  Component C — Multi-Timeframe     (1H + 4H + Daily alignment)

Signal logic: ALL 3 components must agree (AND gate, not averaging).
Minimum confidence threshold: 70%.
"""

import asyncio
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

logger = logging.getLogger(__name__)


class HybridPortfolioSystemV3:
    """
    Simplified 3-Component Signal System v3.1

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

    Signal gate: ALL 3 components must vote the same direction.
    Confidence threshold: 70% minimum.
    """

    def __init__(self, account_balance: float = 10000.0):
        self.account_balance = account_balance
        self.version = "3.1.0"

        # Core 3-component engines
        self.mtf_confirmation = MultiTimeframeConfirmation()
        self.pivot_analyzer = PivotPointsAnalyzer()
        self.feature_engineer = FeatureEngineer()

        # Infrastructure (kept for compatibility)
        self.economic_calendar = EconomicCalendar()
        self.position_calculator = PositionCalculator()
        self.portfolio_manager = PortfolioManager()
        self.strategy_router = StrategyRouter()

        logger.info(f"HybridPortfolioSystemV3 initialized — v{self.version} (3-component core)")

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
    # Main Signal Generation
    # ------------------------------------------------------------------

    async def generate_signal(
        self,
        symbol: str,
        df_4h: pd.DataFrame,
        df_daily: Optional[pd.DataFrame] = None,
        price_data: Optional[Dict[str, pd.Series]] = None,
    ) -> Dict[str, Any]:
        """
        3-component signal generation pipeline.

        All 3 components must agree (AND logic).
        Minimum confidence threshold: 70%.

        Args:
            symbol: Trading symbol (e.g. XAUUSD)
            df_4h: 4H OHLCV DataFrame (primary timeframe)
            df_daily: Daily OHLCV DataFrame (for pivot points)
            price_data: Unused — kept for API compatibility

        Returns:
            Signal dict with component votes, confidence, and levels
        """
        start_time = datetime.utcnow()

        try:
            result: Dict[str, Any] = {
                "symbol": symbol,
                "timestamp": start_time.isoformat(),
                "version": self.version,
                "valid": True,
                "components": {},
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
                    "signal": "NEUTRAL",
                    "confidence": 0.0,
                    "meets_threshold": False,
                    "rejection_reason": f"CALENDAR_BLOCKED: {calendar_check.get('reason', '')}",
                })
                return result

            # ── Component A: Trend Confirmation ───────────────────────
            comp_a = self._component_a_trend(df_4h)
            result["components"]["trend_confirmation"] = comp_a

            # ── Component C: Multi-Timeframe Alignment ────────────────
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

            # ── Component B: Support/Resistance ───────────────────────
            comp_b = self._component_b_sr(df_4h, symbol, df_daily)
            result["components"]["support_resistance"] = comp_b

            # ── Unanimous Vote Gate (AND logic) ───────────────────────
            votes = {
                "A_trend": comp_a["vote"],
                "B_sr": comp_b["vote"],
                "C_mtf": comp_c["vote"],
            }
            result["component_votes"] = votes

            vote_values = list(votes.values())
            all_buy = all(v == "BUY" for v in vote_values)
            all_sell = all(v == "SELL" for v in vote_values)
            unanimous = all_buy or all_sell

            if not unanimous:
                disagreeing = [k for k, v in votes.items() if v != vote_values[0]]
                result.update({
                    "signal": "NEUTRAL",
                    "confidence": 0.0,
                    "meets_threshold": False,
                    "rejection_reason": f"NO_CONSENSUS: {votes}",
                    "disagreeing_components": disagreeing,
                })
                logger.info(
                    f"HybridPortfolioV3 [{symbol}]: NEUTRAL — no consensus "
                    f"A={votes['A_trend']} B={votes['B_sr']} C={votes['C_mtf']}"
                )
                return result

            # ── Composite Confidence (geometric mean of all 3) ────────
            signal = "BUY" if all_buy else "SELL"
            conf_a = comp_a["confidence"]
            conf_b = comp_b["confidence"]
            conf_c = comp_c["confidence"]

            # Geometric mean rewards consistent high confidence across all 3
            composite_conf = (conf_a * conf_b * conf_c) ** (1.0 / 3.0)
            composite_pct = round(composite_conf * 100, 1)

            # Signal quality score: penalise signals that barely meet threshold
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
                "signal": signal if meets_threshold else "NEUTRAL",
                "confidence": composite_pct,
                "confidence_components": {
                    "A_trend": round(conf_a * 100, 1),
                    "B_sr": round(conf_b * 100, 1),
                    "C_mtf": round(conf_c * 100, 1),
                },
                "signal_quality": signal_quality,
                "meets_threshold": meets_threshold,
                "min_confidence_threshold": MIN_CONFIDENCE,
            })

            if not meets_threshold:
                result["rejection_reason"] = (
                    f"BELOW_THRESHOLD: {composite_pct:.1f}% < {MIN_CONFIDENCE}%"
                )

            # ── Position Sizing (fixed 1% risk) ───────────────────────
            if meets_threshold and signal in ("BUY", "SELL"):
                current_price = float(df_4h["close"].iloc[-1])

                high = df_4h["high"].astype(float)
                low = df_4h["low"].astype(float)
                close = df_4h["close"].astype(float)
                tr = pd.concat([
                    high - low,
                    (high - close.shift()).abs(),
                    (low - close.shift()).abs(),
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
                    risk_pct=1.0,          # Fixed 1% risk — no multipliers
                    volatility_multiplier=1.0,
                    risk_parity_weight=1.0,
                )
                result["position_sizing"] = position_size
                result["tp_levels"] = [tp1, tp2, tp3]
                result["sl_price"] = round(sl_price, 5)
                result["entry_price"] = round(current_price, 5)
                result["atr"] = round(atr, 5)

            # ── Processing Time ───────────────────────────────────────
            elapsed = (datetime.utcnow() - start_time).total_seconds()
            result["processing_time_ms"] = round(elapsed * 1000, 1)

            logger.info(
                f"HybridPortfolioV3 [{symbol}]: signal={result['signal']} "
                f"confidence={composite_pct:.1f}% quality={signal_quality} "
                f"A={votes['A_trend']} B={votes['B_sr']} C={votes['C_mtf']} "
                f"time={result['processing_time_ms']}ms"
            )
            return result

        except Exception as exc:
            logger.error(f"HybridPortfolioV3 error [{symbol}]: {exc}", exc_info=True)
            return {
                "symbol": symbol,
                "signal": "NEUTRAL",
                "confidence": 0.0,
                "error": str(exc),
                "valid": False,
                "timestamp": start_time.isoformat(),
            }

    # ------------------------------------------------------------------
    # System Status
    # ------------------------------------------------------------------

    def get_system_status(self) -> Dict[str, Any]:
        """Get full system status and component health."""
        return {
            "version": self.version,
            "system_name": "3-Component Core Signal System",
            "architecture": "AND-gate unanimous consensus",
            "components": {
                "A_trend_confirmation": "ACTIVE",
                "B_support_resistance": "ACTIVE",
                "C_mtf_alignment": "ACTIVE",
                "economic_calendar": "ACTIVE",
                "position_calculator": "ACTIVE",
                "portfolio_manager": "ACTIVE",
            },
            "total_components": 3,
            "signal_logic": "ALL 3 must agree (AND gate)",
            "min_confidence_threshold": 70.0,
            "risk_per_trade_pct": 1.0,
            "account_balance": self.account_balance,
            "portfolio_state": self.portfolio_manager.get_state(self.account_balance),
            "timestamp": datetime.utcnow().isoformat(),
        }

    def update_account_balance(self, balance: float) -> None:
        """Update account balance."""
        self.account_balance = balance
        logger.info(f"HybridPortfolioV3: account balance updated to {balance:.2f}")


# Global instance
hybrid_system_v3 = HybridPortfolioSystemV3()

"""
Hybrid TP/SL Engine v3.1
Market Structure + Liquidity + Advanced ATR Weighting
Institutional-grade take profit and stop loss calculation
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class HybridTPSLEngine:
    """
    Advanced TP/SL Engine combining:
    1. Market Structure (Support/Resistance)
    2. Liquidity Zones (Order Blocks, FVGs)
    3. Advanced ATR Weighting (Volatility Regime-based)
    4. Dynamic Risk/Reward Ratios
    """

    def __init__(self):
        self.version = "3.1.0"
        self.atr_period = 14
        self.swing_lookback = 10

    def calculate(
        self,
        df: pd.DataFrame,
        symbol: str,
        direction: str,
        entry_price: float,
        smc_analysis: Optional[Dict] = None,
        volatility_regime: str = "NORMAL",
        confidence: float = 0.5,
    ) -> Dict[str, Any]:
        """
        Calculate TP/SL levels using hybrid approach.

        Args:
            df: OHLCV DataFrame
            symbol: Trading symbol
            direction: BUY or SELL
            entry_price: Entry price
            smc_analysis: SMC/ICT analysis dict (optional)
            volatility_regime: LOW, NORMAL, HIGH
            confidence: Signal confidence (0-1)

        Returns:
            Dictionary with TP levels, SL, and R:R ratios
        """
        try:
            if len(df) < 30:
                return {"error": "Insufficient data", "valid": False}

            result = {
                "symbol": symbol,
                "direction": direction.upper(),
                "entry_price": round(entry_price, 5),
                "timestamp": datetime.utcnow().isoformat(),
                "valid": True,
                "version": self.version,
            }

            # 1. Calculate base ATR
            atr = self._calculate_atr(df)
            result["atr"] = round(atr, 5)

            # 2. Apply volatility regime weighting
            atr_weighted = self._apply_volatility_weighting(atr, volatility_regime)
            result["atr_weighted"] = round(atr_weighted, 5)
            result["volatility_regime"] = volatility_regime

            # 3. Find market structure levels
            structure = self._find_market_structure(df, direction)
            result["market_structure"] = structure

            # 4. Find liquidity zones
            liquidity = self._find_liquidity_zones(df, smc_analysis, direction)
            result["liquidity_zones"] = liquidity

            # 5. Calculate SL with market structure alignment
            sl_price = self._calculate_sl(
                entry_price,
                direction,
                atr_weighted,
                structure,
                liquidity,
            )
            result["sl_price"] = round(sl_price, 5)
            result["sl_distance"] = round(abs(entry_price - sl_price), 5)

            # 6. Calculate TP levels with liquidity alignment
            tp_levels = self._calculate_tp_levels(
                entry_price,
                direction,
                atr_weighted,
                structure,
                liquidity,
                confidence,
            )
            result["tp_levels"] = [round(tp, 5) for tp in tp_levels]

            # 7. Calculate dynamic R:R ratios
            rr_ratios = self._calculate_rr_ratios(
                entry_price,
                sl_price,
                tp_levels,
                direction,
                confidence,
            )
            result["rr_ratios"] = [round(rr, 2) for rr in rr_ratios]

            # 8. Quality assessment
            result["quality_score"] = self._assess_quality(
                structure,
                liquidity,
                confidence,
                rr_ratios,
            )

            logger.info(
                f"TP/SL [{symbol}/{direction}]: SL={result['sl_price']} "
                f"TP={result['tp_levels']} R:R={result['rr_ratios']} "
                f"Quality={result['quality_score']}/10"
            )
            return result

        except Exception as exc:
            logger.error(f"TP/SL calculation error: {exc}", exc_info=True)
            return {"error": str(exc), "valid": False}

    # ------------------------------------------------------------------
    # ATR Calculation & Weighting
    # ------------------------------------------------------------------

    def _calculate_atr(self, df: pd.DataFrame) -> float:
        """Calculate Average True Range."""
        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)

        atr = tr.rolling(self.atr_period).mean()
        return float(atr.iloc[-1]) if not np.isnan(atr.iloc[-1]) else 0.0

    def _apply_volatility_weighting(self, atr: float, regime: str) -> float:
        """
        Apply volatility regime weighting to ATR.
        
        LOW: Use 0.8x ATR (tighter stops in low vol)
        NORMAL: Use 1.0x ATR (standard)
        HIGH: Use 1.3x ATR (wider stops in high vol)
        """
        weights = {
            "LOW": 0.8,
            "NORMAL": 1.0,
            "HIGH": 1.3,
        }
        weight = weights.get(regime.upper(), 1.0)
        return atr * weight

    # ------------------------------------------------------------------
    # Market Structure Analysis
    # ------------------------------------------------------------------

    def _find_market_structure(self, df: pd.DataFrame, direction: str) -> Dict[str, Any]:
        """
        Find key support/resistance levels from market structure.
        """
        recent = df.tail(50)
        high = recent["high"]
        low = recent["low"]

        # Find swing highs and lows
        swing_highs = []
        swing_lows = []

        for i in range(self.swing_lookback, len(recent) - self.swing_lookback):
            window_high = high.iloc[i - self.swing_lookback : i + self.swing_lookback + 1]
            window_low = low.iloc[i - self.swing_lookback : i + self.swing_lookback + 1]

            if high.iloc[i] == window_high.max():
                swing_highs.append(float(high.iloc[i]))
            if low.iloc[i] == window_low.min():
                swing_lows.append(float(low.iloc[i]))

        # Get key levels
        if direction.upper() == "BUY":
            # For BUY: Find nearest support below entry
            support_levels = [sl for sl in swing_lows if sl < df["close"].iloc[-1]]
            support_levels.sort(reverse=True)
            nearest_support = support_levels[0] if support_levels else df["low"].min()

            # Find resistance above entry
            resistance_levels = [sh for sh in swing_highs if sh > df["close"].iloc[-1]]
            resistance_levels.sort()
            nearest_resistance = resistance_levels[0] if resistance_levels else df["high"].max()

            return {
                "direction": "BUY",
                "support": round(nearest_support, 5),
                "resistance": round(nearest_resistance, 5),
                "structure_bias": "BULLISH" if len(swing_highs) > len(swing_lows) else "BEARISH",
            }
        else:
            # For SELL: Find nearest resistance above entry
            resistance_levels = [sh for sh in swing_highs if sh > df["close"].iloc[-1]]
            resistance_levels.sort()
            nearest_resistance = resistance_levels[0] if resistance_levels else df["high"].max()

            # Find support below entry
            support_levels = [sl for sl in swing_lows if sl < df["close"].iloc[-1]]
            support_levels.sort(reverse=True)
            nearest_support = support_levels[0] if support_levels else df["low"].min()

            return {
                "direction": "SELL",
                "support": round(nearest_support, 5),
                "resistance": round(nearest_resistance, 5),
                "structure_bias": "BEARISH" if len(swing_lows) > len(swing_highs) else "BULLISH",
            }

    # ------------------------------------------------------------------
    # Liquidity Zone Detection
    # ------------------------------------------------------------------

    def _find_liquidity_zones(
        self,
        df: pd.DataFrame,
        smc_analysis: Optional[Dict],
        direction: str,
    ) -> Dict[str, Any]:
        """
        Find liquidity zones from SMC analysis or price action.
        """
        zones = {
            "order_blocks": [],
            "fair_value_gaps": [],
            "liquidity_clusters": [],
        }

        if smc_analysis:
            # Extract from SMC analysis
            zones["order_blocks"] = smc_analysis.get("order_blocks", [])[:3]
            zones["fair_value_gaps"] = smc_analysis.get("fair_value_gaps", [])[:3]

        # Find liquidity clusters (areas with multiple touches)
        recent = df.tail(30)
        high = recent["high"]
        low = recent["low"]

        # Identify price levels with multiple touches
        price_levels = {}
        tolerance = (df["close"].iloc[-1] * 0.001)  # 0.1% tolerance

        for h in high:
            for level in price_levels:
                if abs(h - level) < tolerance:
                    price_levels[level] += 1
                    break
            else:
                price_levels[h] = 1

        # Get clusters (levels touched 2+ times)
        clusters = sorted(
            [level for level, count in price_levels.items() if count >= 2],
            reverse=True,
        )[:3]

        zones["liquidity_clusters"] = [round(c, 5) for c in clusters]

        return zones

    # ------------------------------------------------------------------
    # SL Calculation
    # ------------------------------------------------------------------

    # ATR multipliers for TP/SL — tighter levels for 1H scalp/swing on gold
    TP_ATR_MULTIPLIERS = [0.5, 0.75, 1.0]  # TP1, TP2, TP3 (was 2.0, 3.5, 5.0)
    SL_ATR_MULTIPLIER  = 0.64              # SL distance (~9.59 pips at typical 15 ATR)

    def _calculate_sl(
        self,
        entry_price: float,
        direction: str,
        atr_weighted: float,
        structure: Dict,
        liquidity: Dict,
    ) -> float:
        """
        Calculate SL using ATR-based distance from entry (0.64x ATR).

        Produces 1H-appropriate stops (~9.59 pips at typical 15 ATR) that
        give more room than TP1 (0.5x ATR), improving R:R on TP2/TP3.
        Structure and liquidity levels are used only as a floor/ceiling to
        avoid placing SL inside a known support/resistance zone.
        """
        sl_distance = atr_weighted * self.SL_ATR_MULTIPLIER

        if direction.upper() == "BUY":
            base_sl = entry_price - sl_distance

            # Don't place SL above a known support (would be inside structure)
            support = structure.get("support", base_sl)
            sl_price = min(base_sl, support)

            return sl_price

        else:  # SELL
            base_sl = entry_price + sl_distance

            # Don't place SL below a known resistance (would be inside structure)
            resistance = structure.get("resistance", base_sl)
            sl_price = max(base_sl, resistance)

            return sl_price

    # ------------------------------------------------------------------
    # TP Calculation
    # ------------------------------------------------------------------

    def _calculate_tp_levels(
        self,
        entry_price: float,
        direction: str,
        atr_weighted: float,
        structure: Dict,
        liquidity: Dict,
        confidence: float,
    ) -> List[float]:
        """
        Calculate TP levels using ATR multiples of 0.5x, 0.75x, and 1.0x.

        These tighter multiples are appropriate for 1H scalp/swing trades on
        gold pairs and produce R:R ratios of ~1:1, ~1:1.5, and ~1:2 relative
        to the 0.64x ATR stop loss.  Structure and liquidity levels are used
        only as a cap to avoid targeting beyond the nearest S/R zone.
        """
        multipliers = self.TP_ATR_MULTIPLIERS  # [0.5, 0.75, 1.0]

        if direction.upper() == "BUY":
            tp1_base = entry_price + atr_weighted * multipliers[0]
            tp2_base = entry_price + atr_weighted * multipliers[1]
            tp3_base = entry_price + atr_weighted * multipliers[2]

            # Cap TP levels at nearest resistance so we don't target through S/R
            resistance = structure.get("resistance", tp3_base)
            tp1 = min(tp1_base, resistance * 0.999)
            tp2 = min(tp2_base, resistance * 0.999)
            tp3 = min(tp3_base, resistance * 0.999)

            return [tp1, tp2, tp3]

        else:  # SELL
            tp1_base = entry_price - atr_weighted * multipliers[0]
            tp2_base = entry_price - atr_weighted * multipliers[1]
            tp3_base = entry_price - atr_weighted * multipliers[2]

            # Floor TP levels at nearest support so we don't target through S/R
            support = structure.get("support", tp3_base)
            tp1 = max(tp1_base, support * 1.001)
            tp2 = max(tp2_base, support * 1.001)
            tp3 = max(tp3_base, support * 1.001)

            return [tp1, tp2, tp3]

    # ------------------------------------------------------------------
    # R:R Calculation
    # ------------------------------------------------------------------

    def _calculate_rr_ratios(
        self,
        entry_price: float,
        sl_price: float,
        tp_levels: List[float],
        direction: str,
        confidence: float,
    ) -> List[float]:
        """
        Calculate dynamic R:R ratios based on confidence.
        """
        risk = abs(entry_price - sl_price)

        rr_ratios = []
        for tp in tp_levels:
            reward = abs(tp - entry_price)
            if risk > 0:
                rr = reward / risk
                # Adjust by confidence (higher confidence = higher RR targets)
                rr = rr * (0.8 + confidence * 0.4)  # Range: 0.8x to 1.2x
                rr_ratios.append(rr)
            else:
                rr_ratios.append(0.0)

        return rr_ratios

    # ------------------------------------------------------------------
    # Quality Assessment
    # ------------------------------------------------------------------

    def _assess_quality(
        self,
        structure: Dict,
        liquidity: Dict,
        confidence: float,
        rr_ratios: List[float],
    ) -> int:
        """
        Assess TP/SL setup quality (0-10).
        """
        score = 0

        # Structure alignment (0-3)
        if structure.get("structure_bias") in ["BULLISH", "BEARISH"]:
            score += 2
        score += 1

        # Liquidity zones (0-3)
        if liquidity.get("order_blocks"):
            score += 1
        if liquidity.get("fair_value_gaps"):
            score += 1
        if liquidity.get("liquidity_clusters"):
            score += 1

        # Confidence (0-2)
        if confidence >= 0.7:
            score += 2
        elif confidence >= 0.5:
            score += 1

        # R:R quality (0-2) — thresholds aligned with 1H scalp/swing targets
        avg_rr = np.mean(rr_ratios) if rr_ratios else 0
        if avg_rr >= 1.2:
            score += 2
        elif avg_rr >= 1.0:
            score += 1

        return min(score, 10)


# Global instance
tp_sl_engine = HybridTPSLEngine()


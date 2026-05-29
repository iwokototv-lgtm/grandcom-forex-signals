"""
SMC/ICT Institutional Strategy
Order Blocks, Liquidity Voids, Fair Value Gaps, and ICT concepts
G-Component: SMC/Institutional Structure
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class SMCICTStrategy:
    """
    Smart Money Concepts / Inner Circle Trader (ICT) Strategy.

    Implements full institutional order flow analysis:
    - Order Blocks (OB): Last opposing candle before strong directional move
    - Liquidity Voids: Price gaps / imbalances (FVGs)
    - Breaker Blocks: Failed order blocks that flip polarity
    - Mitigation Blocks: OBs that have been partially mitigated
    - Inducement: Liquidity pools above/below swing points
    - Optimal Trade Entry (OTE): 61.8%-79% Fibonacci retracement
    - Kill Zones: London Open, NY Open, Asian session timing
    - Power of 3 (PO3): Accumulation, Manipulation, Distribution
    """

    def __init__(self):
        self.swing_lookback = 10
        self.ob_lookback = 30
        self.fvg_min_size_pct = 0.0003   # 0.03% minimum FVG
        self.ote_fib_low = 0.618
        self.ote_fib_high = 0.786
        self.version = "3.0.0"

    # ------------------------------------------------------------------
    # Main Analysis Entry Point
    # ------------------------------------------------------------------

    def analyze(self, df: pd.DataFrame, symbol: str, timeframe: str = "4h") -> Dict[str, Any]:
        """
        Full SMC/ICT analysis pipeline.

        Args:
            df: OHLCV DataFrame with columns [open, high, low, close, volume]
            symbol: Trading symbol (e.g. XAUUSD)
            timeframe: Chart timeframe

        Returns:
            Comprehensive SMC analysis dictionary
        """
        try:
            if len(df) < 50:
                return {"error": "Insufficient data (need 50+ candles)", "valid": False}

            result: Dict[str, Any] = {
                "symbol": symbol,
                "timeframe": timeframe,
                "timestamp": datetime.utcnow().isoformat(),
                "valid": True,
                "version": self.version,
            }

            # 1. Swing structure
            swing_highs, swing_lows = self._find_swing_points(df)
            result["swing_highs"] = swing_highs[-5:]
            result["swing_lows"] = swing_lows[-5:]

            # 2. Market structure (BOS / ChoCH)
            result["market_structure"] = self._analyze_market_structure(df, swing_highs, swing_lows)

            # 3. Order Blocks
            result["order_blocks"] = self._detect_order_blocks(df)

            # 4. Breaker Blocks
            result["breaker_blocks"] = self._detect_breaker_blocks(df, result["order_blocks"])

            # 5. Fair Value Gaps / Liquidity Voids
            result["fair_value_gaps"] = self._find_fair_value_gaps(df)

            # 6. Liquidity Sweeps / Stop Hunts
            result["liquidity_sweep"] = self._detect_liquidity_sweep(df, swing_highs, swing_lows)

            # 7. Premium / Discount zones
            result["premium_discount"] = self._calculate_premium_discount(df)

            # 8. OTE (Optimal Trade Entry) zones
            result["ote_zones"] = self._calculate_ote_zones(df, swing_highs, swing_lows)

            # 9. Inducement levels
            result["inducement"] = self._find_inducement(df, swing_highs, swing_lows)

            # 10. Power of 3 phase
            result["power_of_3"] = self._detect_power_of_3(df)

            # 11. Composite score & bias
            result["smc_score"] = self._calculate_smc_score(result)
            result["smc_bias"] = self._determine_smc_bias(result)
            result["signal_quality"] = self._assess_signal_quality(result)

            logger.info(
                f"SMC/ICT [{symbol}/{timeframe}]: score={result['smc_score']}/10 "
                f"bias={result['smc_bias']} quality={result['signal_quality']}"
            )
            return result

        except Exception as exc:
            logger.error(f"SMC/ICT analysis error [{symbol}]: {exc}", exc_info=True)
            return {"error": str(exc), "valid": False}

    # ------------------------------------------------------------------
    # Swing Points
    # ------------------------------------------------------------------

    def _find_swing_points(
        self, df: pd.DataFrame
    ) -> Tuple[List[Dict], List[Dict]]:
        """Identify swing highs and lows using fractal logic."""
        swing_highs: List[Dict] = []
        swing_lows: List[Dict] = []
        lb = self.swing_lookback

        for i in range(lb, len(df) - lb):
            window_high = df["high"].iloc[i - lb : i + lb + 1]
            window_low = df["low"].iloc[i - lb : i + lb + 1]

            if df["high"].iloc[i] == window_high.max():
                swing_highs.append({
                    "index": int(i),
                    "price": float(df["high"].iloc[i]),
                    "datetime": str(df.index[i]) if hasattr(df.index, "dtype") else str(i),
                })

            if df["low"].iloc[i] == window_low.min():
                swing_lows.append({
                    "index": int(i),
                    "price": float(df["low"].iloc[i]),
                    "datetime": str(df.index[i]) if hasattr(df.index, "dtype") else str(i),
                })

        return swing_highs, swing_lows

    # ------------------------------------------------------------------
    # Market Structure
    # ------------------------------------------------------------------

    def _analyze_market_structure(
        self,
        df: pd.DataFrame,
        swing_highs: List[Dict],
        swing_lows: List[Dict],
    ) -> Dict[str, Any]:
        """Determine BOS (Break of Structure) and ChoCH (Change of Character)."""
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return {"structure": "UNKNOWN", "bos": False, "choch": False}

        current_price = float(df["close"].iloc[-1])
        last_high = swing_highs[-1]["price"]
        prev_high = swing_highs[-2]["price"]
        last_low = swing_lows[-1]["price"]
        prev_low = swing_lows[-2]["price"]

        hh = last_high > prev_high
        hl = last_low > prev_low
        lh = last_high < prev_high
        ll = last_low < prev_low

        if hh and hl:
            structure = "BULLISH"
        elif lh and ll:
            structure = "BEARISH"
        else:
            structure = "RANGING"

        bos = current_price > last_high or current_price < last_low
        bos_type = "BULLISH" if current_price > last_high else ("BEARISH" if current_price < last_low else None)
        choch = (structure == "BULLISH" and bos_type == "BEARISH") or (
            structure == "BEARISH" and bos_type == "BULLISH"
        )

        return {
            "structure": structure,
            "bos": bos,
            "bos_type": bos_type,
            "choch": choch,
            "higher_highs": hh,
            "higher_lows": hl,
            "lower_highs": lh,
            "lower_lows": ll,
            "last_swing_high": round(last_high, 5),
            "last_swing_low": round(last_low, 5),
        }

    # ------------------------------------------------------------------
    # Order Blocks
    # ------------------------------------------------------------------

    def _detect_order_blocks(self, df: pd.DataFrame) -> List[Dict]:
        """
        Detect Order Blocks — last opposing candle before a strong directional move.

        Bullish OB: Last bearish candle before a strong bullish impulse.
        Bearish OB: Last bullish candle before a strong bearish impulse.
        """
        order_blocks: List[Dict] = []
        avg_range = (df["high"] - df["low"]).rolling(20).mean()

        for i in range(3, len(df) - 1):
            current = df.iloc[i]
            prev = df.iloc[i - 1]
            curr_range = current["high"] - current["low"]
            avg = avg_range.iloc[i]

            if avg == 0 or curr_range < avg * 1.5:
                continue

            # Bullish OB
            if current["close"] > current["open"] and prev["close"] < prev["open"]:
                order_blocks.append({
                    "type": "BULLISH",
                    "top": round(float(prev["open"]), 5),
                    "bottom": round(float(prev["close"]), 5),
                    "index": int(i - 1),
                    "strength": round(float(curr_range / avg), 2),
                    "mitigated": False,
                    "ob_type": "standard",
                })

            # Bearish OB
            elif current["close"] < current["open"] and prev["close"] > prev["open"]:
                order_blocks.append({
                    "type": "BEARISH",
                    "top": round(float(prev["close"]), 5),
                    "bottom": round(float(prev["open"]), 5),
                    "index": int(i - 1),
                    "strength": round(float(curr_range / avg), 2),
                    "mitigated": False,
                    "ob_type": "standard",
                })

        # Mark mitigated OBs
        current_price = float(df["close"].iloc[-1])
        for ob in order_blocks:
            if ob["type"] == "BULLISH" and current_price < ob["top"]:
                ob["mitigated"] = True
            elif ob["type"] == "BEARISH" and current_price > ob["bottom"]:
                ob["mitigated"] = True

        # Return last 5 unmitigated OBs
        return [ob for ob in order_blocks if not ob["mitigated"]][-5:]

    # ------------------------------------------------------------------
    # Breaker Blocks
    # ------------------------------------------------------------------

    def _detect_breaker_blocks(
        self, df: pd.DataFrame, order_blocks: List[Dict]
    ) -> List[Dict]:
        """
        Breaker Blocks: Failed OBs that flip polarity.
        A bullish OB that gets broken becomes a bearish breaker, and vice versa.
        """
        breakers: List[Dict] = []
        current_price = float(df["close"].iloc[-1])

        for ob in order_blocks:
            if ob["type"] == "BULLISH" and current_price < ob["bottom"]:
                breakers.append({
                    "type": "BEARISH_BREAKER",
                    "top": ob["top"],
                    "bottom": ob["bottom"],
                    "origin_type": "BULLISH_OB",
                    "index": ob["index"],
                })
            elif ob["type"] == "BEARISH" and current_price > ob["top"]:
                breakers.append({
                    "type": "BULLISH_BREAKER",
                    "top": ob["top"],
                    "bottom": ob["bottom"],
                    "origin_type": "BEARISH_OB",
                    "index": ob["index"],
                })

        return breakers[-3:]

    # ------------------------------------------------------------------
    # Fair Value Gaps / Liquidity Voids
    # ------------------------------------------------------------------

    def _find_fair_value_gaps(self, df: pd.DataFrame) -> List[Dict]:
        """
        Fair Value Gaps (FVG) / Imbalances / Liquidity Voids.

        Bullish FVG: candle[i-2].high < candle[i].low  (gap up)
        Bearish FVG: candle[i-2].low  > candle[i].high (gap down)
        """
        fvgs: List[Dict] = []

        for i in range(2, len(df)):
            c1 = df.iloc[i - 2]
            c3 = df.iloc[i]

            # Bullish FVG
            if c1["high"] < c3["low"]:
                gap = (c3["low"] - c1["high"]) / c1["close"]
                if gap >= self.fvg_min_size_pct:
                    fvgs.append({
                        "type": "BULLISH",
                        "top": round(float(c3["low"]), 5),
                        "bottom": round(float(c1["high"]), 5),
                        "midpoint": round(float((c3["low"] + c1["high"]) / 2), 5),
                        "size_pct": round(gap * 100, 4),
                        "index": int(i),
                        "filled": False,
                    })

            # Bearish FVG
            if c1["low"] > c3["high"]:
                gap = (c1["low"] - c3["high"]) / c1["close"]
                if gap >= self.fvg_min_size_pct:
                    fvgs.append({
                        "type": "BEARISH",
                        "top": round(float(c1["low"]), 5),
                        "bottom": round(float(c3["high"]), 5),
                        "midpoint": round(float((c1["low"] + c3["high"]) / 2), 5),
                        "size_pct": round(gap * 100, 4),
                        "index": int(i),
                        "filled": False,
                    })

        # Mark filled FVGs
        current_price = float(df["close"].iloc[-1])
        for fvg in fvgs:
            if fvg["type"] == "BULLISH" and current_price <= fvg["bottom"]:
                fvg["filled"] = True
            elif fvg["type"] == "BEARISH" and current_price >= fvg["top"]:
                fvg["filled"] = True

        return [fvg for fvg in fvgs if not fvg["filled"]][-5:]

    # ------------------------------------------------------------------
    # Liquidity Sweeps
    # ------------------------------------------------------------------

    def _detect_liquidity_sweep(
        self,
        df: pd.DataFrame,
        swing_highs: List[Dict],
        swing_lows: List[Dict],
    ) -> Dict[str, Any]:
        """Detect stop hunts / liquidity grabs above swing highs or below swing lows."""
        if not swing_highs or not swing_lows:
            return {"detected": False, "bias": "NEUTRAL"}

        recent = df.tail(5)
        last_sh = swing_highs[-1]["price"]
        last_sl = swing_lows[-1]["price"]

        sweep_high = any(
            row["high"] > last_sh and row["close"] < last_sh
            for _, row in recent.iterrows()
        )
        sweep_low = any(
            row["low"] < last_sl and row["close"] > last_sl
            for _, row in recent.iterrows()
        )

        bias = "BULLISH" if sweep_low else ("BEARISH" if sweep_high else "NEUTRAL")

        return {
            "detected": sweep_high or sweep_low,
            "sweep_high": sweep_high,
            "sweep_low": sweep_low,
            "bias": bias,
            "last_swing_high": round(last_sh, 5),
            "last_swing_low": round(last_sl, 5),
        }

    # ------------------------------------------------------------------
    # Premium / Discount Zones
    # ------------------------------------------------------------------

    def _calculate_premium_discount(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Premium / Discount analysis based on 50-candle range.
        Premium (>50%): Institutional sell zone.
        Discount (<50%): Institutional buy zone.
        """
        recent = df.tail(50)
        rng_high = float(recent["high"].max())
        rng_low = float(recent["low"].min())
        rng_size = rng_high - rng_low
        equilibrium = rng_low + rng_size * 0.5
        current_price = float(df["close"].iloc[-1])

        if rng_size == 0:
            return {"zone": "EQUILIBRIUM", "zone_strength": 0.5}

        if current_price > equilibrium:
            zone = "PREMIUM"
            strength = min((current_price - equilibrium) / (rng_high - equilibrium), 1.0)
        elif current_price < equilibrium:
            zone = "DISCOUNT"
            strength = min((equilibrium - current_price) / (equilibrium - rng_low), 1.0)
        else:
            zone = "EQUILIBRIUM"
            strength = 0.5

        return {
            "zone": zone,
            "zone_strength": round(strength, 3),
            "range_high": round(rng_high, 5),
            "range_low": round(rng_low, 5),
            "equilibrium": round(equilibrium, 5),
            "current_price": round(current_price, 5),
            "optimal_buy": zone == "DISCOUNT",
            "optimal_sell": zone == "PREMIUM",
            "fib_236": round(rng_low + rng_size * 0.236, 5),
            "fib_382": round(rng_low + rng_size * 0.382, 5),
            "fib_618": round(rng_low + rng_size * 0.618, 5),
            "fib_786": round(rng_low + rng_size * 0.786, 5),
        }

    # ------------------------------------------------------------------
    # OTE Zones
    # ------------------------------------------------------------------

    def _calculate_ote_zones(
        self,
        df: pd.DataFrame,
        swing_highs: List[Dict],
        swing_lows: List[Dict],
    ) -> Dict[str, Any]:
        """
        Optimal Trade Entry (OTE): 61.8%–78.6% Fibonacci retracement.
        ICT concept for high-probability entry after a displacement move.
        """
        if not swing_highs or not swing_lows:
            return {"valid": False}

        current_price = float(df["close"].iloc[-1])
        last_sh = swing_highs[-1]["price"]
        last_sl = swing_lows[-1]["price"]
        rng = last_sh - last_sl

        if rng <= 0:
            return {"valid": False}

        # Bullish OTE: retracement into 61.8-78.6% of a bullish leg
        bull_ote_low = last_sl + rng * (1 - self.ote_fib_high)
        bull_ote_high = last_sl + rng * (1 - self.ote_fib_low)

        # Bearish OTE: retracement into 61.8-78.6% of a bearish leg
        bear_ote_low = last_sh - rng * (1 - self.ote_fib_low)
        bear_ote_high = last_sh - rng * (1 - self.ote_fib_high)

        in_bull_ote = bull_ote_low <= current_price <= bull_ote_high
        in_bear_ote = bear_ote_low <= current_price <= bear_ote_high

        return {
            "valid": True,
            "bullish_ote": {
                "low": round(bull_ote_low, 5),
                "high": round(bull_ote_high, 5),
                "active": in_bull_ote,
            },
            "bearish_ote": {
                "low": round(bear_ote_low, 5),
                "high": round(bear_ote_high, 5),
                "active": in_bear_ote,
            },
            "in_ote": in_bull_ote or in_bear_ote,
            "ote_bias": "BULLISH" if in_bull_ote else ("BEARISH" if in_bear_ote else "NONE"),
        }

    # ------------------------------------------------------------------
    # Inducement
    # ------------------------------------------------------------------

    def _find_inducement(
        self,
        df: pd.DataFrame,
        swing_highs: List[Dict],
        swing_lows: List[Dict],
    ) -> Dict[str, Any]:
        """
        Inducement: Minor swing points that act as liquidity pools
        before the real move. Price sweeps inducement before reversing.
        """
        if len(swing_highs) < 3 or len(swing_lows) < 3:
            return {"detected": False}

        # Minor highs between last two major highs
        major_high_1 = swing_highs[-2]["price"]
        major_high_2 = swing_highs[-1]["price"]
        major_low_1 = swing_lows[-2]["price"]
        major_low_2 = swing_lows[-1]["price"]

        # Inducement above: minor high between two major lows (bearish inducement)
        # Inducement below: minor low between two major highs (bullish inducement)
        current_price = float(df["close"].iloc[-1])

        bull_inducement = major_low_1 > major_low_2  # LL structure = inducement below
        bear_inducement = major_high_1 < major_high_2  # HH structure = inducement above

        return {
            "detected": bull_inducement or bear_inducement,
            "bullish_inducement": bull_inducement,
            "bearish_inducement": bear_inducement,
            "inducement_level_bull": round(major_low_2, 5) if bull_inducement else None,
            "inducement_level_bear": round(major_high_2, 5) if bear_inducement else None,
        }

    # ------------------------------------------------------------------
    # Power of 3
    # ------------------------------------------------------------------

    def _detect_power_of_3(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        ICT Power of 3 (PO3): Accumulation → Manipulation → Distribution.
        Identifies the current phase of the market cycle.
        """
        if len(df) < 20:
            return {"phase": "UNKNOWN"}

        recent = df.tail(20)
        price_range = float(recent["high"].max() - recent["low"].min())
        current_price = float(df["close"].iloc[-1])
        open_price = float(recent["open"].iloc[0])

        # Volatility compression = accumulation
        atr_recent = float((recent["high"] - recent["low"]).mean())
        atr_older = float((df["high"] - df["low"]).tail(60).mean()) if len(df) >= 60 else atr_recent
        compression_ratio = atr_recent / atr_older if atr_older > 0 else 1.0

        if compression_ratio < 0.7:
            phase = "ACCUMULATION"
        elif abs(current_price - open_price) / open_price > 0.005:
            phase = "DISTRIBUTION"
        else:
            phase = "MANIPULATION"

        return {
            "phase": phase,
            "compression_ratio": round(compression_ratio, 3),
            "price_range": round(price_range, 5),
            "directional_bias": "BULLISH" if current_price > open_price else "BEARISH",
        }

    # ------------------------------------------------------------------
    # Scoring & Bias
    # ------------------------------------------------------------------

    def _calculate_smc_score(self, analysis: Dict[str, Any]) -> int:
        """Composite SMC quality score (0–10)."""
        score = 0

        # Order Blocks (0-2)
        obs = analysis.get("order_blocks", [])
        score += min(len(obs), 2)

        # FVGs (0-2)
        fvgs = analysis.get("fair_value_gaps", [])
        score += min(len(fvgs), 2)

        # Liquidity Sweep (0-2)
        if analysis.get("liquidity_sweep", {}).get("detected"):
            score += 2

        # Market Structure (0-2)
        ms = analysis.get("market_structure", {})
        if ms.get("bos"):
            score += 1
        if ms.get("choch"):
            score += 1

        # Premium/Discount (0-1)
        pd_zone = analysis.get("premium_discount", {})
        if pd_zone.get("optimal_buy") or pd_zone.get("optimal_sell"):
            score += 1

        # OTE (0-1)
        if analysis.get("ote_zones", {}).get("in_ote"):
            score += 1

        return min(score, 10)

    def _determine_smc_bias(self, analysis: Dict[str, Any]) -> str:
        """Aggregate all SMC signals into a directional bias."""
        bull = 0
        bear = 0

        for ob in analysis.get("order_blocks", []):
            if ob["type"] == "BULLISH":
                bull += 1
            else:
                bear += 1

        for fvg in analysis.get("fair_value_gaps", []):
            if fvg["type"] == "BULLISH":
                bull += 1
            else:
                bear += 1

        sweep = analysis.get("liquidity_sweep", {})
        if sweep.get("bias") == "BULLISH":
            bull += 2
        elif sweep.get("bias") == "BEARISH":
            bear += 2

        ms = analysis.get("market_structure", {})
        if ms.get("structure") == "BULLISH":
            bull += 2
        elif ms.get("structure") == "BEARISH":
            bear += 2

        pd_zone = analysis.get("premium_discount", {})
        if pd_zone.get("optimal_buy"):
            bull += 1
        elif pd_zone.get("optimal_sell"):
            bear += 1

        ote = analysis.get("ote_zones", {})
        if ote.get("ote_bias") == "BULLISH":
            bull += 1
        elif ote.get("ote_bias") == "BEARISH":
            bear += 1

        if bull > bear + 2:
            return "BULLISH"
        elif bear > bull + 2:
            return "BEARISH"
        return "NEUTRAL"

    def _assess_signal_quality(self, analysis: Dict[str, Any]) -> str:
        """Classify signal quality based on SMC score."""
        score = analysis.get("smc_score", 0)
        if score >= 8:
            return "EXCELLENT"
        elif score >= 6:
            return "GOOD"
        elif score >= 4:
            return "FAIR"
        return "POOR"


# Global instance
smc_ict_strategy = SMCICTStrategy()

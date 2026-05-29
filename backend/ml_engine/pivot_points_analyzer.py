"""
G1: Daily Pivot Points Analyzer
4 calculation methods, 6 support/resistance levels, 6 price zones
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, date
import logging

logger = logging.getLogger(__name__)


class PivotPointsAnalyzer:
    """
    G1: Daily Pivot Points Analyzer.

    Supports 4 calculation methods:
    1. Standard (Classic) - Most widely used
    2. Fibonacci - Fibonacci retracement levels
    3. Woodie - Weighted toward close price
    4. Camarilla - Tighter levels, mean reversion focus

    Outputs:
    - 6 levels: S3, S2, S1, R1, R2, R3
    - 6 zones: Deep Support, Support, Near Support, Near Resistance, Resistance, Deep Resistance
    - Zone classification for current price
    - Nearest support/resistance levels
    """

    def __init__(self):
        self.methods = ["standard", "fibonacci", "woodie", "camarilla"]
        self.version = "3.0.0"

    # ------------------------------------------------------------------
    # Main Analysis
    # ------------------------------------------------------------------

    def analyze(
        self,
        df: pd.DataFrame,
        symbol: str,
        method: str = "standard",
        use_all_methods: bool = True,
    ) -> Dict[str, Any]:
        """
        Calculate pivot points and classify current price zone.

        Args:
            df: OHLCV DataFrame (daily candles preferred)
            symbol: Trading symbol
            method: Calculation method (standard/fibonacci/woodie/camarilla)
            use_all_methods: If True, calculate all 4 methods

        Returns:
            Pivot analysis with levels, zones, and price classification
        """
        try:
            if len(df) < 2:
                return {"error": "Need at least 2 candles", "valid": False}

            # Use previous day's OHLC for pivot calculation
            prev = df.iloc[-2]
            current = df.iloc[-1]

            prev_high = float(prev["high"])
            prev_low = float(prev["low"])
            prev_close = float(prev["close"])
            prev_open = float(prev.get("open", prev_close))
            current_price = float(current["close"])

            result: Dict[str, Any] = {
                "symbol": symbol,
                "timestamp": datetime.utcnow().isoformat(),
                "current_price": round(current_price, 5),
                "prev_high": round(prev_high, 5),
                "prev_low": round(prev_low, 5),
                "prev_close": round(prev_close, 5),
                "valid": True,
                "version": self.version,
            }

            if use_all_methods:
                result["pivots"] = {}
                for m in self.methods:
                    result["pivots"][m] = self._calculate_pivots(
                        prev_high, prev_low, prev_close, prev_open, m
                    )
                # Primary method
                result["primary"] = result["pivots"][method]
                result["primary_method"] = method
            else:
                result["primary"] = self._calculate_pivots(
                    prev_high, prev_low, prev_close, prev_open, method
                )
                result["primary_method"] = method

            # Zone classification
            result["zone"] = self._classify_zone(current_price, result["primary"])
            result["nearest_levels"] = self._find_nearest_levels(current_price, result["primary"])
            result["bias"] = self._determine_bias(current_price, result["primary"])
            result["zones_map"] = self._build_zones_map(result["primary"])

            logger.info(
                f"Pivot [{symbol}/{method}]: PP={result['primary']['pp']:.5f} "
                f"zone={result['zone']['name']} bias={result['bias']}"
            )
            return result

        except Exception as exc:
            logger.error(f"Pivot analysis error [{symbol}]: {exc}", exc_info=True)
            return {"error": str(exc), "valid": False}

    # ------------------------------------------------------------------
    # Calculation Methods
    # ------------------------------------------------------------------

    def _calculate_pivots(
        self,
        high: float,
        low: float,
        close: float,
        open_: float,
        method: str,
    ) -> Dict[str, float]:
        """Calculate pivot levels using specified method."""
        if method == "standard":
            return self._standard_pivots(high, low, close)
        elif method == "fibonacci":
            return self._fibonacci_pivots(high, low, close)
        elif method == "woodie":
            return self._woodie_pivots(high, low, close, open_)
        elif method == "camarilla":
            return self._camarilla_pivots(high, low, close)
        else:
            return self._standard_pivots(high, low, close)

    def _standard_pivots(self, high: float, low: float, close: float) -> Dict[str, float]:
        """
        Standard (Classic) Pivot Points.
        PP = (H + L + C) / 3
        """
        pp = (high + low + close) / 3
        r1 = 2 * pp - low
        r2 = pp + (high - low)
        r3 = high + 2 * (pp - low)
        s1 = 2 * pp - high
        s2 = pp - (high - low)
        s3 = low - 2 * (high - pp)

        return {
            "method": "standard",
            "pp": round(pp, 5),
            "r1": round(r1, 5),
            "r2": round(r2, 5),
            "r3": round(r3, 5),
            "s1": round(s1, 5),
            "s2": round(s2, 5),
            "s3": round(s3, 5),
        }

    def _fibonacci_pivots(self, high: float, low: float, close: float) -> Dict[str, float]:
        """
        Fibonacci Pivot Points.
        Uses Fibonacci ratios: 0.382, 0.618, 1.000
        """
        pp = (high + low + close) / 3
        rng = high - low

        r1 = pp + 0.382 * rng
        r2 = pp + 0.618 * rng
        r3 = pp + 1.000 * rng
        s1 = pp - 0.382 * rng
        s2 = pp - 0.618 * rng
        s3 = pp - 1.000 * rng

        return {
            "method": "fibonacci",
            "pp": round(pp, 5),
            "r1": round(r1, 5),
            "r2": round(r2, 5),
            "r3": round(r3, 5),
            "s1": round(s1, 5),
            "s2": round(s2, 5),
            "s3": round(s3, 5),
            "fib_382": round(r1, 5),
            "fib_618": round(r2, 5),
            "fib_100": round(r3, 5),
        }

    def _woodie_pivots(
        self, high: float, low: float, close: float, open_: float
    ) -> Dict[str, float]:
        """
        Woodie Pivot Points.
        PP = (H + L + 2*C) / 4  — weighted toward close
        """
        pp = (high + low + 2 * close) / 4
        r1 = 2 * pp - low
        r2 = pp + high - low
        r3 = r1 + high - low
        s1 = 2 * pp - high
        s2 = pp - high + low
        s3 = s1 - high + low

        return {
            "method": "woodie",
            "pp": round(pp, 5),
            "r1": round(r1, 5),
            "r2": round(r2, 5),
            "r3": round(r3, 5),
            "s1": round(s1, 5),
            "s2": round(s2, 5),
            "s3": round(s3, 5),
        }

    def _camarilla_pivots(self, high: float, low: float, close: float) -> Dict[str, float]:
        """
        Camarilla Pivot Points.
        Tighter levels, designed for mean reversion.
        Uses multipliers: 1.1/12, 1.1/6, 1.1/4, 1.1/2
        """
        rng = high - low

        r1 = close + rng * (1.1 / 12)
        r2 = close + rng * (1.1 / 6)
        r3 = close + rng * (1.1 / 4)
        r4 = close + rng * (1.1 / 2)
        s1 = close - rng * (1.1 / 12)
        s2 = close - rng * (1.1 / 6)
        s3 = close - rng * (1.1 / 4)
        s4 = close - rng * (1.1 / 2)

        # PP for Camarilla is still standard
        pp = (high + low + close) / 3

        return {
            "method": "camarilla",
            "pp": round(pp, 5),
            "r1": round(r1, 5),
            "r2": round(r2, 5),
            "r3": round(r3, 5),
            "r4": round(r4, 5),
            "s1": round(s1, 5),
            "s2": round(s2, 5),
            "s3": round(s3, 5),
            "s4": round(s4, 5),
        }

    # ------------------------------------------------------------------
    # Zone Classification
    # ------------------------------------------------------------------

    def _classify_zone(self, price: float, pivots: Dict[str, float]) -> Dict[str, Any]:
        """
        Classify current price into one of 6 zones:
        1. Deep Support (below S3)
        2. Support (S2-S3)
        3. Near Support (S1-S2)
        4. Near Resistance (PP-R1)
        5. Resistance (R1-R2)
        6. Deep Resistance (above R2)
        """
        pp = pivots["pp"]
        r1 = pivots["r1"]
        r2 = pivots["r2"]
        r3 = pivots.get("r3", r2 + (r2 - r1))
        s1 = pivots["s1"]
        s2 = pivots["s2"]
        s3 = pivots.get("s3", s2 - (s1 - s2))

        if price > r3:
            zone_name = "EXTREME_RESISTANCE"
            zone_id = 7
            bias = "BEARISH"
        elif price > r2:
            zone_name = "DEEP_RESISTANCE"
            zone_id = 6
            bias = "BEARISH"
        elif price > r1:
            zone_name = "RESISTANCE"
            zone_id = 5
            bias = "BEARISH"
        elif price > pp:
            zone_name = "NEAR_RESISTANCE"
            zone_id = 4
            bias = "NEUTRAL_BULLISH"
        elif price > s1:
            zone_name = "NEAR_SUPPORT"
            zone_id = 3
            bias = "NEUTRAL_BEARISH"
        elif price > s2:
            zone_name = "SUPPORT"
            zone_id = 2
            bias = "BULLISH"
        elif price > s3:
            zone_name = "DEEP_SUPPORT"
            zone_id = 1
            bias = "BULLISH"
        else:
            zone_name = "EXTREME_SUPPORT"
            zone_id = 0
            bias = "BULLISH"

        # Distance to nearest pivot
        all_levels = {
            "pp": pp, "r1": r1, "r2": r2, "r3": r3,
            "s1": s1, "s2": s2, "s3": s3,
        }
        nearest_level = min(all_levels.items(), key=lambda x: abs(x[1] - price))
        distance_pct = abs(price - nearest_level[1]) / price * 100

        return {
            "name": zone_name,
            "id": zone_id,
            "bias": bias,
            "nearest_pivot": nearest_level[0],
            "nearest_pivot_price": round(nearest_level[1], 5),
            "distance_to_nearest_pct": round(distance_pct, 4),
            "above_pp": price > pp,
        }

    def _find_nearest_levels(
        self, price: float, pivots: Dict[str, float]
    ) -> Dict[str, Any]:
        """Find nearest support and resistance levels."""
        levels = {k: v for k, v in pivots.items() if k not in ("method",)}

        supports = {k: v for k, v in levels.items() if v < price}
        resistances = {k: v for k, v in levels.items() if v > price}

        nearest_support = max(supports.items(), key=lambda x: x[1]) if supports else None
        nearest_resistance = min(resistances.items(), key=lambda x: x[1]) if resistances else None

        result: Dict[str, Any] = {}
        if nearest_support:
            result["nearest_support"] = {
                "level": nearest_support[0],
                "price": round(nearest_support[1], 5),
                "distance_pct": round(abs(price - nearest_support[1]) / price * 100, 4),
            }
        if nearest_resistance:
            result["nearest_resistance"] = {
                "level": nearest_resistance[0],
                "price": round(nearest_resistance[1], 5),
                "distance_pct": round(abs(nearest_resistance[1] - price) / price * 100, 4),
            }

        # Risk/reward to nearest levels
        if nearest_support and nearest_resistance:
            risk = price - nearest_support[1]
            reward = nearest_resistance[1] - price
            result["risk_reward"] = round(reward / risk, 2) if risk > 0 else 0.0

        return result

    def _determine_bias(self, price: float, pivots: Dict[str, float]) -> str:
        """Determine directional bias based on pivot position."""
        pp = pivots["pp"]
        r1 = pivots["r1"]
        s1 = pivots["s1"]

        if price > r1:
            return "STRONG_BULLISH"
        elif price > pp:
            return "BULLISH"
        elif price < s1:
            return "STRONG_BEARISH"
        elif price < pp:
            return "BEARISH"
        return "NEUTRAL"

    def _build_zones_map(self, pivots: Dict[str, float]) -> List[Dict[str, Any]]:
        """Build ordered list of all 6 zones with price ranges."""
        pp = pivots["pp"]
        r1 = pivots["r1"]
        r2 = pivots["r2"]
        r3 = pivots.get("r3", r2 + (r2 - r1))
        s1 = pivots["s1"]
        s2 = pivots["s2"]
        s3 = pivots.get("s3", s2 - (s1 - s2))

        return [
            {"zone": "DEEP_SUPPORT", "from": round(s3, 5), "to": round(s2, 5), "bias": "STRONG_BUY"},
            {"zone": "SUPPORT", "from": round(s2, 5), "to": round(s1, 5), "bias": "BUY"},
            {"zone": "NEAR_SUPPORT", "from": round(s1, 5), "to": round(pp, 5), "bias": "WEAK_BUY"},
            {"zone": "NEAR_RESISTANCE", "from": round(pp, 5), "to": round(r1, 5), "bias": "WEAK_SELL"},
            {"zone": "RESISTANCE", "from": round(r1, 5), "to": round(r2, 5), "bias": "SELL"},
            {"zone": "DEEP_RESISTANCE", "from": round(r2, 5), "to": round(r3, 5), "bias": "STRONG_SELL"},
        ]

    # ------------------------------------------------------------------
    # Multi-Symbol Analysis
    # ------------------------------------------------------------------

    def analyze_multiple(
        self, dfs: Dict[str, pd.DataFrame], method: str = "standard"
    ) -> Dict[str, Dict]:
        """Analyze pivot points for multiple symbols."""
        results = {}
        for symbol, df in dfs.items():
            results[symbol] = self.analyze(df, symbol, method=method)
        return results


# Global instance
pivot_analyzer = PivotPointsAnalyzer()

"""
Smart Money Concepts (SMC) Analysis
Implements institutional trading concepts for better entry precision
"""
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class SmartMoneyAnalyzer:
    """
    Smart Money Concepts (SMC) Analysis for institutional-grade entries.
    
    Implements:
    - Order Blocks (OB) detection
    - Fair Value Gaps (FVG) / Imbalances
    - Liquidity Sweeps
    - Break of Structure (BOS)
    - Change of Character (ChoCH)
    - Premium/Discount Zones
    """
    
    def __init__(self):
        self.swing_lookback = 10
        self.ob_lookback = 20
        self.fvg_min_size = 0.0005  # Minimum FVG size as percentage
    
    def analyze(self, df: pd.DataFrame, symbol: str) -> Dict[str, Any]:
        """
        Complete SMC analysis on price data.
        
        Returns:
            Dictionary with all SMC components
        """
        try:
            if len(df) < 50:
                return {"error": "Insufficient data", "valid": False}
            
            result = {
                "symbol": symbol,
                "timestamp": datetime.utcnow().isoformat(),
                "valid": True
            }
            
            # 1. Find swing points
            swing_highs, swing_lows = self._find_swing_points(df)
            result["swing_highs"] = swing_highs[-3:] if swing_highs else []
            result["swing_lows"] = swing_lows[-3:] if swing_lows else []
            
            # 2. Detect Order Blocks
            result["order_blocks"] = self._detect_order_blocks(df)
            
            # 3. Find Fair Value Gaps
            result["fair_value_gaps"] = self._find_fair_value_gaps(df)
            
            # 4. Check for Liquidity Sweeps
            result["liquidity_sweep"] = self._detect_liquidity_sweep(df, swing_highs, swing_lows)
            
            # 5. Determine Market Structure
            result["market_structure"] = self._analyze_market_structure(df, swing_highs, swing_lows)
            
            # 6. Calculate Premium/Discount zones
            result["premium_discount"] = self._calculate_premium_discount(df)
            
            # 7. Generate SMC score
            result["smc_score"] = self._calculate_smc_score(result)
            
            # 8. SMC bias
            result["smc_bias"] = self._determine_smc_bias(result)
            
            logger.info(f"SMC Analysis {symbol}: Score={result['smc_score']}, Bias={result['smc_bias']}")
            
            return result
            
        except Exception as e:
            logger.error(f"SMC analysis error for {symbol}: {e}")
            return {"error": str(e), "valid": False}
    
    def _find_swing_points(self, df: pd.DataFrame) -> Tuple[List[Dict], List[Dict]]:
        """Find swing highs and lows"""
        swing_highs = []
        swing_lows = []
        
        lookback = self.swing_lookback
        
        for i in range(lookback, len(df) - lookback):
            # Swing High: highest point in lookback window
            if df['high'].iloc[i] == df['high'].iloc[i-lookback:i+lookback+1].max():
                swing_highs.append({
                    "index": i,
                    "price": float(df['high'].iloc[i]),
                    "datetime": str(df['datetime'].iloc[i])
                })
            
            # Swing Low: lowest point in lookback window
            if df['low'].iloc[i] == df['low'].iloc[i-lookback:i+lookback+1].min():
                swing_lows.append({
                    "index": i,
                    "price": float(df['low'].iloc[i]),
                    "datetime": str(df['datetime'].iloc[i])
                })
        
        return swing_highs, swing_lows
    
    def _detect_order_blocks(self, df: pd.DataFrame) -> List[Dict]:
        """
        Detect Order Blocks (last up/down candle before strong move).
        
        Bullish OB: Last bearish candle before strong bullish move
        Bearish OB: Last bullish candle before strong bearish move
        """
        order_blocks = []
        
        for i in range(3, len(df) - 1):
            current = df.iloc[i]
            prev = df.iloc[i-1]
            
            # Check for strong move (at least 2x average range)
            avg_range = (df['high'] - df['low']).rolling(20).mean().iloc[i]
            current_range = current['high'] - current['low']
            
            if current_range < avg_range * 1.5:
                continue
            
            # Bullish Order Block
            if current['close'] > current['open']:  # Bullish candle
                if prev['close'] < prev['open']:  # Previous was bearish
                    order_blocks.append({
                        "type": "BULLISH",
                        "top": float(prev['open']),
                        "bottom": float(prev['close']),
                        "index": i - 1,
                        "mitigated": False
                    })
            
            # Bearish Order Block
            elif current['close'] < current['open']:  # Bearish candle
                if prev['close'] > prev['open']:  # Previous was bullish
                    order_blocks.append({
                        "type": "BEARISH",
                        "top": float(prev['close']),
                        "bottom": float(prev['open']),
                        "index": i - 1,
                        "mitigated": False
                    })
        
        # Check which OBs have been mitigated (price returned to them)
        current_price = df['close'].iloc[-1]
        for ob in order_blocks:
            if ob["type"] == "BULLISH" and current_price < ob["top"]:
                ob["mitigated"] = True
            elif ob["type"] == "BEARISH" and current_price > ob["bottom"]:
                ob["mitigated"] = True
        
        # Return only unmitigated OBs (still valid)
        return [ob for ob in order_blocks if not ob["mitigated"]][-5:]
    
    def _find_fair_value_gaps(self, df: pd.DataFrame) -> List[Dict]:
        """
        Find Fair Value Gaps (FVG) / Imbalances.
        
        Bullish FVG: Gap between candle 1 high and candle 3 low
        Bearish FVG: Gap between candle 1 low and candle 3 high
        """
        fvgs = []
        
        for i in range(2, len(df)):
            candle1 = df.iloc[i-2]
            candle2 = df.iloc[i-1]
            candle3 = df.iloc[i]
            
            # Bullish FVG: candle1 high < candle3 low (gap up)
            if candle1['high'] < candle3['low']:
                gap_size = (candle3['low'] - candle1['high']) / candle1['close']
                if gap_size >= self.fvg_min_size:
                    fvgs.append({
                        "type": "BULLISH",
                        "top": float(candle3['low']),
                        "bottom": float(candle1['high']),
                        "size_pct": round(gap_size * 100, 3),
                        "index": i,
                        "filled": False
                    })
            
            # Bearish FVG: candle1 low > candle3 high (gap down)
            if candle1['low'] > candle3['high']:
                gap_size = (candle1['low'] - candle3['high']) / candle1['close']
                if gap_size >= self.fvg_min_size:
                    fvgs.append({
                        "type": "BEARISH",
                        "top": float(candle1['low']),
                        "bottom": float(candle3['high']),
                        "size_pct": round(gap_size * 100, 3),
                        "index": i,
                        "filled": False
                    })
        
        # Check which FVGs have been filled
        current_price = df['close'].iloc[-1]
        for fvg in fvgs:
            if fvg["type"] == "BULLISH":
                if current_price <= fvg["bottom"]:
                    fvg["filled"] = True
            else:
                if current_price >= fvg["top"]:
                    fvg["filled"] = True
        
        # Return only unfilled FVGs
        return [fvg for fvg in fvgs if not fvg["filled"]][-5:]
    
    def _detect_liquidity_sweep(
        self, 
        df: pd.DataFrame, 
        swing_highs: List[Dict], 
        swing_lows: List[Dict]
    ) -> Dict[str, Any]:
        """
        Detect if recent price action swept liquidity (stop hunts).
        """
        if not swing_highs or not swing_lows:
            return {"detected": False}
        
        recent_candles = df.tail(5)
        last_swing_high = swing_highs[-1]["price"] if swing_highs else None
        last_swing_low = swing_lows[-1]["price"] if swing_lows else None
        
        sweep_high = False
        sweep_low = False
        
        # Check if price swept above swing high then reversed
        if last_swing_high:
            for _, candle in recent_candles.iterrows():
                if candle['high'] > last_swing_high and candle['close'] < last_swing_high:
                    sweep_high = True
                    break
        
        # Check if price swept below swing low then reversed
        if last_swing_low:
            for _, candle in recent_candles.iterrows():
                if candle['low'] < last_swing_low and candle['close'] > last_swing_low:
                    sweep_low = True
                    break
        
        return {
            "detected": sweep_high or sweep_low,
            "sweep_high": sweep_high,
            "sweep_low": sweep_low,
            "bias": "BULLISH" if sweep_low else ("BEARISH" if sweep_high else "NEUTRAL")
        }
    
    def _analyze_market_structure(
        self, 
        df: pd.DataFrame,
        swing_highs: List[Dict],
        swing_lows: List[Dict]
    ) -> Dict[str, Any]:
        """
        Analyze market structure for BOS (Break of Structure) and ChoCH (Change of Character).
        """
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return {"structure": "UNKNOWN", "bos": False, "choch": False}
        
        current_price = df['close'].iloc[-1]
        
        # Get last two swing points
        last_high = swing_highs[-1]["price"]
        prev_high = swing_highs[-2]["price"] if len(swing_highs) >= 2 else last_high
        last_low = swing_lows[-1]["price"]
        prev_low = swing_lows[-2]["price"] if len(swing_lows) >= 2 else last_low
        
        # Determine structure
        higher_highs = last_high > prev_high
        higher_lows = last_low > prev_low
        lower_highs = last_high < prev_high
        lower_lows = last_low < prev_low
        
        if higher_highs and higher_lows:
            structure = "BULLISH"
        elif lower_highs and lower_lows:
            structure = "BEARISH"
        else:
            structure = "RANGING"
        
        # Check for BOS (Break of Structure)
        bos = False
        bos_type = None
        if current_price > last_high:
            bos = True
            bos_type = "BULLISH"
        elif current_price < last_low:
            bos = True
            bos_type = "BEARISH"
        
        # Check for ChoCH (Change of Character) - structure break in opposite direction
        choch = False
        if structure == "BULLISH" and bos_type == "BEARISH":
            choch = True
        elif structure == "BEARISH" and bos_type == "BULLISH":
            choch = True
        
        return {
            "structure": structure,
            "bos": bos,
            "bos_type": bos_type,
            "choch": choch,
            "higher_highs": higher_highs,
            "higher_lows": higher_lows,
            "lower_highs": lower_highs,
            "lower_lows": lower_lows
        }
    
    def _calculate_premium_discount(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Calculate Premium/Discount zones based on recent range.
        
        Premium: Upper 50% of range (sell zone)
        Discount: Lower 50% of range (buy zone)
        Equilibrium: Middle 50% (50% level)
        """
        recent = df.tail(50)
        range_high = recent['high'].max()
        range_low = recent['low'].min()
        range_size = range_high - range_low
        
        equilibrium = range_low + (range_size * 0.5)
        premium_start = range_low + (range_size * 0.5)
        discount_end = range_low + (range_size * 0.5)
        
        current_price = df['close'].iloc[-1]
        
        # Determine zone
        if current_price > premium_start:
            zone = "PREMIUM"
            zone_strength = (current_price - equilibrium) / (range_high - equilibrium)
        elif current_price < discount_end:
            zone = "DISCOUNT"
            zone_strength = (equilibrium - current_price) / (equilibrium - range_low)
        else:
            zone = "EQUILIBRIUM"
            zone_strength = 0.5
        
        return {
            "zone": zone,
            "zone_strength": round(min(zone_strength, 1.0), 2),
            "range_high": round(float(range_high), 5),
            "range_low": round(float(range_low), 5),
            "equilibrium": round(float(equilibrium), 5),
            "current_price": round(float(current_price), 5),
            "optimal_buy": zone == "DISCOUNT",
            "optimal_sell": zone == "PREMIUM"
        }
    
    def _calculate_smc_score(self, analysis: Dict[str, Any]) -> int:
        """
        Calculate overall SMC score (0-10).
        
        Higher score = better setup quality.
        """
        score = 0
        
        # Order Blocks (0-2 points)
        obs = analysis.get("order_blocks", [])
        if len(obs) >= 1:
            score += 1
        if len(obs) >= 2:
            score += 1
        
        # Fair Value Gaps (0-2 points)
        fvgs = analysis.get("fair_value_gaps", [])
        if len(fvgs) >= 1:
            score += 1
        if len(fvgs) >= 2:
            score += 1
        
        # Liquidity Sweep (0-2 points)
        sweep = analysis.get("liquidity_sweep", {})
        if sweep.get("detected"):
            score += 2
        
        # Market Structure (0-2 points)
        structure = analysis.get("market_structure", {})
        if structure.get("bos"):
            score += 1
        if structure.get("choch"):
            score += 1
        
        # Premium/Discount Zone (0-2 points)
        pd_zone = analysis.get("premium_discount", {})
        if pd_zone.get("optimal_buy") or pd_zone.get("optimal_sell"):
            score += 2
        
        return min(score, 10)
    
    def _determine_smc_bias(self, analysis: Dict[str, Any]) -> str:
        """Determine overall SMC bias"""
        bullish_signals = 0
        bearish_signals = 0
        
        # Order Blocks
        for ob in analysis.get("order_blocks", []):
            if ob["type"] == "BULLISH":
                bullish_signals += 1
            else:
                bearish_signals += 1
        
        # FVGs
        for fvg in analysis.get("fair_value_gaps", []):
            if fvg["type"] == "BULLISH":
                bullish_signals += 1
            else:
                bearish_signals += 1
        
        # Liquidity Sweep
        sweep = analysis.get("liquidity_sweep", {})
        if sweep.get("bias") == "BULLISH":
            bullish_signals += 2
        elif sweep.get("bias") == "BEARISH":
            bearish_signals += 2
        
        # Market Structure
        structure = analysis.get("market_structure", {})
        if structure.get("structure") == "BULLISH":
            bullish_signals += 2
        elif structure.get("structure") == "BEARISH":
            bearish_signals += 2
        
        # Premium/Discount
        pd_zone = analysis.get("premium_discount", {})
        if pd_zone.get("optimal_buy"):
            bullish_signals += 1
        elif pd_zone.get("optimal_sell"):
            bearish_signals += 1
        
        if bullish_signals > bearish_signals + 2:
            return "BULLISH"
        elif bearish_signals > bullish_signals + 2:
            return "BEARISH"
        else:
            return "NEUTRAL"


# Global instance
smc_analyzer = SmartMoneyAnalyzer()

"""
Multi-Timeframe Analysis Engine
Implements H4 bias, H1 structure, M15 trigger methodology
"""
import pandas as pd
import numpy as np
import ta
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
import logging
import aiohttp
import os

logger = logging.getLogger(__name__)

TWELVE_DATA_API_KEY = os.environ.get('TWELVE_DATA_API_KEY', 'demo')

# Symbol mapping for Twelve Data API
SYMBOL_MAP = {
    "XAUUSD": "XAU/USD",
    "XAUEUR": "XAU/EUR",
    "BTCUSD": "BTC/USD",
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "USDJPY": "USD/JPY",
    "EURJPY": "EUR/JPY",
    "GBPJPY": "GBP/JPY",
    "AUDUSD": "AUD/USD",
    "USDCAD": "USD/CAD",
    "USDCHF": "USD/CHF"
}

def serialize_value(val):
    """Convert numpy types to Python native types for JSON serialization"""
    if isinstance(val, (np.integer, np.int64, np.int32)):
        return int(val)
    elif isinstance(val, (np.floating, np.float64, np.float32)):
        return float(val)
    elif isinstance(val, np.bool_):
        return bool(val)
    elif isinstance(val, np.ndarray):
        return val.tolist()
    elif isinstance(val, dict):
        return {k: serialize_value(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [serialize_value(v) for v in val]
    return val


class MultiTimeframeAnalyzer:
    """
    Multi-timeframe analysis following institutional methodology:
    - H4: Determines overall bias (trend direction)
    - H1: Identifies market structure (key levels, patterns)
    - M15: Finds precise entry triggers
    """
    
    def __init__(self):
        self.timeframes = {
            'H4': '4h',
            'H1': '1h',
            'M15': '15min'
        }
        self.cache = {}  # Simple cache for rate limiting
    
    async def fetch_timeframe_data(self, symbol: str, interval: str, outputsize: int = 100) -> Optional[pd.DataFrame]:
        """Fetch price data for specific timeframe"""
        try:
            api_symbol = SYMBOL_MAP.get(symbol, symbol)
            
            url = "https://api.twelvedata.com/time_series"
            params = {
                "symbol": api_symbol,
                "interval": interval,
                "apikey": TWELVE_DATA_API_KEY,
                "outputsize": outputsize
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as response:
                    data = await response.json()
                    
                    if "values" not in data:
                        logger.warning(f"No data for {symbol} {interval}: {data.get('message', 'Unknown error')}")
                        return None
                    
                    df = pd.DataFrame(data["values"])
                    df["datetime"] = pd.to_datetime(df["datetime"])
                    df = df.sort_values("datetime")
                    
                    for col in ["open", "high", "low", "close"]:
                        df[col] = pd.to_numeric(df[col])
                    
                    if "volume" in df.columns:
                        df["volume"] = pd.to_numeric(df["volume"])
                    else:
                        df["volume"] = 0
                    
                    return df
                    
        except Exception as e:
            logger.error(f"Error fetching {symbol} {interval}: {e}")
            return None
    
    async def analyze(self, symbol: str) -> Dict[str, Any]:
        """
        Perform complete multi-timeframe analysis.
        
        Returns:
            Dictionary with H4 bias, H1 structure, M15 trigger, and confluence score
        """
        try:
            result = {
                'symbol': symbol,
                'timestamp': datetime.utcnow().isoformat(),
                'h4_bias': None,
                'h1_structure': None,
                'm15_trigger': None,
                'confluence_score': 0,
                'trade_direction': 'NEUTRAL',
                'valid_setup': False
            }
            
            # Fetch all timeframes (with small delays for rate limiting)
            import asyncio
            
            h4_data = await self.fetch_timeframe_data(symbol, '4h', 50)
            await asyncio.sleep(0.5)
            
            h1_data = await self.fetch_timeframe_data(symbol, '1h', 100)
            await asyncio.sleep(0.5)
            
            m15_data = await self.fetch_timeframe_data(symbol, '15min', 50)
            
            # Analyze each timeframe
            if h4_data is not None and len(h4_data) >= 20:
                result['h4_bias'] = self._analyze_h4_bias(h4_data)
            
            if h1_data is not None and len(h1_data) >= 50:
                result['h1_structure'] = self._analyze_h1_structure(h1_data)
            
            if m15_data is not None and len(m15_data) >= 20:
                result['m15_trigger'] = self._analyze_m15_trigger(m15_data)
            
            # Calculate confluence
            result['confluence_score'], result['trade_direction'] = self._calculate_confluence(result)
            result['valid_setup'] = result['confluence_score'] >= 2
            
            logger.info(f"MTF Analysis {symbol}: H4={result['h4_bias'].get('direction') if result['h4_bias'] else 'N/A'}, "
                       f"H1={result['h1_structure'].get('bias') if result['h1_structure'] else 'N/A'}, "
                       f"Confluence={result['confluence_score']}/3")
            
            # Serialize all numpy types before returning
            return serialize_value(result)
            
        except Exception as e:
            logger.error(f"MTF analysis error for {symbol}: {e}")
            return {
                'symbol': symbol,
                'error': str(e),
                'valid_setup': False
            }
    
    def _analyze_h4_bias(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        H4 Bias Analysis - Determines overall market direction
        
        Uses:
        - 50 EMA position
        - ADX trend strength
        - Higher highs/Lower lows
        """
        try:
            # Calculate indicators
            df['ema_50'] = ta.trend.EMAIndicator(df['close'], window=50).ema_indicator()
            df['ema_20'] = ta.trend.EMAIndicator(df['close'], window=20).ema_indicator()
            
            adx_indicator = ta.trend.ADXIndicator(df['high'], df['low'], df['close'], window=14)
            df['adx'] = adx_indicator.adx()
            df['adx_pos'] = adx_indicator.adx_pos()
            df['adx_neg'] = adx_indicator.adx_neg()
            
            latest = df.iloc[-1]
            
            # Determine bias
            price_vs_ema50 = latest['close'] > latest['ema_50']
            ema_alignment = latest['ema_20'] > latest['ema_50']
            strong_trend = latest['adx'] > 25
            di_bullish = latest['adx_pos'] > latest['adx_neg']
            
            # Count higher highs / lower lows
            highs = df['high'].tail(10).values
            lows = df['low'].tail(10).values
            hh_count = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i-1])
            ll_count = sum(1 for i in range(1, len(lows)) if lows[i] < lows[i-1])
            
            # Determine direction
            bullish_signals = sum([price_vs_ema50, ema_alignment, di_bullish, hh_count > ll_count])
            bearish_signals = sum([not price_vs_ema50, not ema_alignment, not di_bullish, ll_count > hh_count])
            
            if bullish_signals >= 3:
                direction = 'BULLISH'
                strength = min(bullish_signals / 4, 1.0)
            elif bearish_signals >= 3:
                direction = 'BEARISH'
                strength = min(bearish_signals / 4, 1.0)
            else:
                direction = 'NEUTRAL'
                strength = 0.5
            
            return {
                'direction': direction,
                'strength': round(strength, 2),
                'adx': round(float(latest['adx']), 2),
                'price_vs_ema50': 'ABOVE' if price_vs_ema50 else 'BELOW',
                'ema_alignment': 'BULLISH' if ema_alignment else 'BEARISH',
                'structure': f"HH:{hh_count} LL:{ll_count}",
                'trending': strong_trend
            }
            
        except Exception as e:
            logger.error(f"H4 bias analysis error: {e}")
            return {'direction': 'NEUTRAL', 'strength': 0.5, 'error': str(e)}
    
    def _analyze_h1_structure(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        H1 Structure Analysis - Identifies key levels and market structure
        
        Looks for:
        - Support/Resistance levels
        - Break of structure (BOS)
        - Change of character (ChoCH)
        - Order blocks
        """
        try:
            # Calculate indicators
            df['ema_20'] = ta.trend.EMAIndicator(df['close'], window=20).ema_indicator()
            df['ema_50'] = ta.trend.EMAIndicator(df['close'], window=50).ema_indicator()
            
            # Bollinger Bands for volatility
            bb = ta.volatility.BollingerBands(df['close'], window=20, window_dev=2)
            df['bb_upper'] = bb.bollinger_hband()
            df['bb_lower'] = bb.bollinger_lband()
            df['bb_middle'] = bb.bollinger_mavg()
            
            # RSI for momentum
            df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
            
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            
            # Find swing highs and lows (last 20 candles)
            recent = df.tail(20)
            swing_high = recent['high'].max()
            swing_low = recent['low'].min()
            
            # Determine structure
            price = latest['close']
            range_size = swing_high - swing_low
            position_in_range = (price - swing_low) / range_size if range_size > 0 else 0.5
            
            # Check for break of structure
            broke_high = price > swing_high * 0.999  # Within 0.1% of high
            broke_low = price < swing_low * 1.001   # Within 0.1% of low
            
            # Determine bias
            if position_in_range > 0.7:
                bias = 'BULLISH'
            elif position_in_range < 0.3:
                bias = 'BEARISH'
            else:
                bias = 'NEUTRAL'
            
            # Check for reversal signals
            rsi_oversold = latest['rsi'] < 30
            rsi_overbought = latest['rsi'] > 70
            at_bb_lower = price <= latest['bb_lower'] * 1.001
            at_bb_upper = price >= latest['bb_upper'] * 0.999
            
            return {
                'bias': bias,
                'swing_high': round(float(swing_high), 5),
                'swing_low': round(float(swing_low), 5),
                'position_in_range': round(position_in_range, 2),
                'broke_structure': 'HIGH' if broke_high else ('LOW' if broke_low else 'NONE'),
                'rsi': round(float(latest['rsi']), 2),
                'bb_position': 'UPPER' if at_bb_upper else ('LOWER' if at_bb_lower else 'MIDDLE'),
                'reversal_signal': rsi_oversold or rsi_overbought,
                'key_levels': {
                    'resistance': round(float(swing_high), 5),
                    'support': round(float(swing_low), 5),
                    'pivot': round(float((swing_high + swing_low) / 2), 5)
                }
            }
            
        except Exception as e:
            logger.error(f"H1 structure analysis error: {e}")
            return {'bias': 'NEUTRAL', 'error': str(e)}
    
    def _analyze_m15_trigger(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        M15 Trigger Analysis - Finds precise entry signals
        
        Looks for:
        - Candlestick patterns
        - Momentum shifts
        - Entry confirmation
        """
        try:
            # Calculate indicators
            df['ema_9'] = ta.trend.EMAIndicator(df['close'], window=9).ema_indicator()
            df['ema_21'] = ta.trend.EMAIndicator(df['close'], window=21).ema_indicator()
            
            # MACD for momentum
            macd = ta.trend.MACD(df['close'])
            df['macd'] = macd.macd()
            df['macd_signal'] = macd.macd_signal()
            df['macd_diff'] = macd.macd_diff()
            
            # Stochastic
            stoch = ta.momentum.StochasticOscillator(df['high'], df['low'], df['close'])
            df['stoch_k'] = stoch.stoch()
            df['stoch_d'] = stoch.stoch_signal()
            
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            
            # Candlestick analysis
            body = abs(latest['close'] - latest['open'])
            total_range = latest['high'] - latest['low']
            body_ratio = body / total_range if total_range > 0 else 0.5
            
            is_bullish_candle = latest['close'] > latest['open']
            is_bearish_candle = latest['close'] < latest['open']
            
            # Strong candle (large body)
            strong_candle = body_ratio > 0.6
            
            # EMA crossover
            ema_bullish = latest['ema_9'] > latest['ema_21'] and prev['ema_9'] <= prev['ema_21']
            ema_bearish = latest['ema_9'] < latest['ema_21'] and prev['ema_9'] >= prev['ema_21']
            
            # MACD crossover
            macd_bullish = latest['macd'] > latest['macd_signal'] and latest['macd_diff'] > 0
            macd_bearish = latest['macd'] < latest['macd_signal'] and latest['macd_diff'] < 0
            
            # Stochastic signals
            stoch_oversold = latest['stoch_k'] < 20
            stoch_overbought = latest['stoch_k'] > 80
            stoch_bullish_cross = latest['stoch_k'] > latest['stoch_d'] and prev['stoch_k'] <= prev['stoch_d']
            stoch_bearish_cross = latest['stoch_k'] < latest['stoch_d'] and prev['stoch_k'] >= prev['stoch_d']
            
            # Determine trigger
            bullish_triggers = sum([is_bullish_candle, ema_bullish or macd_bullish, 
                                   stoch_oversold or stoch_bullish_cross])
            bearish_triggers = sum([is_bearish_candle, ema_bearish or macd_bearish,
                                   stoch_overbought or stoch_bearish_cross])
            
            if bullish_triggers >= 2:
                trigger = 'BUY'
                confidence = min(bullish_triggers / 3, 1.0)
            elif bearish_triggers >= 2:
                trigger = 'SELL'
                confidence = min(bearish_triggers / 3, 1.0)
            else:
                trigger = 'NONE'
                confidence = 0.5
            
            return {
                'trigger': trigger,
                'confidence': round(confidence, 2),
                'candle_type': 'BULLISH' if is_bullish_candle else 'BEARISH',
                'candle_strength': 'STRONG' if strong_candle else 'WEAK',
                'macd_signal': 'BULLISH' if macd_bullish else ('BEARISH' if macd_bearish else 'NEUTRAL'),
                'stoch_signal': 'OVERSOLD' if stoch_oversold else ('OVERBOUGHT' if stoch_overbought else 'NEUTRAL'),
                'ema_cross': 'BULLISH' if ema_bullish else ('BEARISH' if ema_bearish else 'NONE')
            }
            
        except Exception as e:
            logger.error(f"M15 trigger analysis error: {e}")
            return {'trigger': 'NONE', 'confidence': 0.5, 'error': str(e)}
    
    def _calculate_confluence(self, result: Dict[str, Any]) -> Tuple[int, str]:
        """
        Calculate confluence score from all timeframes.
        
        Returns:
            Tuple of (score 0-3, direction)
        """
        score = 0
        bullish = 0
        bearish = 0
        
        # H4 Bias
        h4 = result.get('h4_bias')
        if h4:
            if h4.get('direction') == 'BULLISH':
                bullish += 1
                score += 1
            elif h4.get('direction') == 'BEARISH':
                bearish += 1
                score += 1
        
        # H1 Structure
        h1 = result.get('h1_structure')
        if h1:
            if h1.get('bias') == 'BULLISH':
                bullish += 1
                score += 1
            elif h1.get('bias') == 'BEARISH':
                bearish += 1
                score += 1
        
        # M15 Trigger
        m15 = result.get('m15_trigger')
        if m15:
            if m15.get('trigger') == 'BUY':
                bullish += 1
                score += 1
            elif m15.get('trigger') == 'SELL':
                bearish += 1
                score += 1
        
        # Determine direction based on alignment
        if bullish >= 2 and bullish > bearish:
            direction = 'BUY'
        elif bearish >= 2 and bearish > bullish:
            direction = 'SELL'
        else:
            direction = 'NEUTRAL'
            score = 0  # Reset score if no clear direction
        
        return score, direction


# Global instance
mtf_analyzer = MultiTimeframeAnalyzer()

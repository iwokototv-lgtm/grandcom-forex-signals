"""
Feature Engineering Framework
Extracts advanced technical features for ML regime detection
"""
import pandas as pd
import numpy as np
import ta
from typing import Dict, Any, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class FeatureEngineer:
    """
    Comprehensive feature engineering for trading signals.
    Extracts 30+ features across multiple categories.
    """
    
    def __init__(self):
        self.feature_names = []
    
    def extract_features(self, df: pd.DataFrame, symbol: str = "") -> Optional[Dict[str, Any]]:
        """
        Extract all features from OHLCV data.
        
        Args:
            df: DataFrame with columns [datetime, open, high, low, close, volume]
            symbol: Trading pair symbol
            
        Returns:
            Dictionary of extracted features
        """
        try:
            if len(df) < 60:
                logger.warning(f"Insufficient data for feature extraction: {len(df)} rows")
                return None
            
            features = {}
            
            # 1. Volatility Features
            features.update(self._extract_volatility_features(df))
            
            # 2. Trend Features
            features.update(self._extract_trend_features(df))
            
            # 3. Momentum Features
            features.update(self._extract_momentum_features(df))
            
            # 4. Mean Reversion Features
            features.update(self._extract_mean_reversion_features(df))
            
            # 5. Session Features
            features.update(self._extract_session_features(df))
            
            # 6. Structure Features
            features.update(self._extract_structure_features(df))
            
            # 7. Price Action Features
            features.update(self._extract_price_action_features(df))
            
            # Add symbol
            features['symbol'] = symbol
            
            # Store feature names
            self.feature_names = [k for k in features.keys() if k != 'symbol']
            
            return features
            
        except Exception as e:
            logger.error(f"Feature extraction error: {e}")
            return None
    
    def _extract_volatility_features(self, df: pd.DataFrame) -> Dict[str, float]:
        """ATR volatility ratios and clustering metrics"""
        features = {}
        
        # ATR calculations
        df['atr_14'] = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range()
        df['atr_20'] = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=20).average_true_range()
        
        latest = df.iloc[-1]
        
        # Current ATR
        features['atr_current'] = float(latest['atr_14'])
        
        # ATR ratio vs 20-day average
        atr_20_mean = df['atr_20'].rolling(20).mean().iloc[-1]
        features['atr_ratio_20'] = float(latest['atr_14'] / atr_20_mean) if atr_20_mean > 0 else 1.0
        
        # ATR ratio vs 60-day average
        if len(df) >= 60:
            atr_60_mean = df['atr_14'].rolling(60).mean().iloc[-1]
            features['atr_ratio_60'] = float(latest['atr_14'] / atr_60_mean) if atr_60_mean > 0 else 1.0
        else:
            features['atr_ratio_60'] = 1.0
        
        # Realized volatility (std of returns)
        df['returns'] = df['close'].pct_change()
        features['realized_vol_5'] = float(df['returns'].rolling(5).std().iloc[-1] * np.sqrt(252) * 100)
        features['realized_vol_20'] = float(df['returns'].rolling(20).std().iloc[-1] * np.sqrt(252) * 100)
        
        # Volatility clustering (autocorrelation of squared returns)
        squared_returns = df['returns'] ** 2
        if len(squared_returns.dropna()) > 10:
            features['vol_clustering'] = float(squared_returns.dropna().autocorr(lag=1))
        else:
            features['vol_clustering'] = 0.0
        
        # Bollinger bandwidth
        bb = ta.volatility.BollingerBands(df['close'], window=20, window_dev=2)
        features['bb_bandwidth'] = float((bb.bollinger_hband().iloc[-1] - bb.bollinger_lband().iloc[-1]) / bb.bollinger_mavg().iloc[-1])
        
        return features
    
    def _extract_trend_features(self, df: pd.DataFrame) -> Dict[str, float]:
        """ADX trend strength and moving average features"""
        features = {}
        
        # ADX
        adx = ta.trend.ADXIndicator(df['high'], df['low'], df['close'], window=14)
        features['adx'] = float(adx.adx().iloc[-1])
        features['adx_pos'] = float(adx.adx_pos().iloc[-1])
        features['adx_neg'] = float(adx.adx_neg().iloc[-1])
        
        # Moving averages
        df['ma_10'] = ta.trend.SMAIndicator(df['close'], window=10).sma_indicator()
        df['ma_20'] = ta.trend.SMAIndicator(df['close'], window=20).sma_indicator()
        df['ma_50'] = ta.trend.SMAIndicator(df['close'], window=50).sma_indicator()
        df['ema_12'] = ta.trend.EMAIndicator(df['close'], window=12).ema_indicator()
        df['ema_26'] = ta.trend.EMAIndicator(df['close'], window=26).ema_indicator()
        
        latest = df.iloc[-1]
        
        # Price relative to MAs
        features['price_vs_ma20'] = float((latest['close'] - latest['ma_20']) / latest['ma_20'] * 100)
        features['price_vs_ma50'] = float((latest['close'] - latest['ma_50']) / latest['ma_50'] * 100)
        
        # MA slopes (rate of change)
        features['ma20_slope'] = float((latest['ma_20'] - df['ma_20'].iloc[-5]) / df['ma_20'].iloc[-5] * 100)
        features['ma50_slope'] = float((latest['ma_50'] - df['ma_50'].iloc[-5]) / df['ma_50'].iloc[-5] * 100)
        
        # MA crossover distance
        features['ma_cross_dist'] = float((latest['ma_20'] - latest['ma_50']) / latest['ma_50'] * 100)
        
        # EMA trend
        features['ema_trend'] = 1.0 if latest['ema_12'] > latest['ema_26'] else -1.0
        
        return features
    
    def _extract_momentum_features(self, df: pd.DataFrame) -> Dict[str, float]:
        """RSI, MACD and momentum indicators"""
        features = {}
        
        # RSI
        rsi = ta.momentum.RSIIndicator(df['close'], window=14)
        features['rsi'] = float(rsi.rsi().iloc[-1])
        
        # RSI zones
        features['rsi_oversold'] = 1.0 if features['rsi'] < 30 else 0.0
        features['rsi_overbought'] = 1.0 if features['rsi'] > 70 else 0.0
        
        # MACD
        macd = ta.trend.MACD(df['close'])
        features['macd'] = float(macd.macd().iloc[-1])
        features['macd_signal'] = float(macd.macd_signal().iloc[-1])
        features['macd_diff'] = float(macd.macd_diff().iloc[-1])
        features['macd_cross'] = 1.0 if features['macd'] > features['macd_signal'] else -1.0
        
        # Stochastic
        stoch = ta.momentum.StochasticOscillator(df['high'], df['low'], df['close'])
        features['stoch_k'] = float(stoch.stoch().iloc[-1])
        features['stoch_d'] = float(stoch.stoch_signal().iloc[-1])
        
        # Williams %R
        williams = ta.momentum.WilliamsRIndicator(df['high'], df['low'], df['close'])
        features['williams_r'] = float(williams.williams_r().iloc[-1])
        
        # Rate of Change
        roc = ta.momentum.ROCIndicator(df['close'], window=10)
        features['roc_10'] = float(roc.roc().iloc[-1])
        
        return features
    
    def _extract_mean_reversion_features(self, df: pd.DataFrame) -> Dict[str, float]:
        """Bollinger bands and mean reversion z-scores"""
        features = {}
        
        # Bollinger Bands
        bb = ta.volatility.BollingerBands(df['close'], window=20, window_dev=2)
        
        latest_close = df['close'].iloc[-1]
        bb_upper = bb.bollinger_hband().iloc[-1]
        bb_lower = bb.bollinger_lband().iloc[-1]
        bb_middle = bb.bollinger_mavg().iloc[-1]
        
        features['bb_upper'] = float(bb_upper)
        features['bb_lower'] = float(bb_lower)
        features['bb_middle'] = float(bb_middle)
        
        # BB position (0 = at lower band, 1 = at upper band)
        bb_range = bb_upper - bb_lower
        features['bb_position'] = float((latest_close - bb_lower) / bb_range) if bb_range > 0 else 0.5
        
        # Z-score from 20-period mean
        mean_20 = df['close'].rolling(20).mean().iloc[-1]
        std_20 = df['close'].rolling(20).std().iloc[-1]
        features['zscore_20'] = float((latest_close - mean_20) / std_20) if std_20 > 0 else 0.0
        
        # Distance from recent high/low
        high_20 = df['high'].rolling(20).max().iloc[-1]
        low_20 = df['low'].rolling(20).min().iloc[-1]
        range_20 = high_20 - low_20
        features['range_position'] = float((latest_close - low_20) / range_20) if range_20 > 0 else 0.5
        
        return features
    
    def _extract_session_features(self, df: pd.DataFrame) -> Dict[str, float]:
        """Trading session identification"""
        features = {}
        
        # Get latest timestamp
        latest_time = df['datetime'].iloc[-1]
        if isinstance(latest_time, str):
            latest_time = pd.to_datetime(latest_time)
        
        hour = latest_time.hour
        
        # Session identification (UTC times)
        # Asia: 00:00 - 08:00 UTC
        # London: 08:00 - 16:00 UTC
        # New York: 13:00 - 21:00 UTC
        # Overlap (London/NY): 13:00 - 16:00 UTC
        
        features['session_asia'] = 1.0 if 0 <= hour < 8 else 0.0
        features['session_london'] = 1.0 if 8 <= hour < 16 else 0.0
        features['session_newyork'] = 1.0 if 13 <= hour < 21 else 0.0
        features['session_overlap'] = 1.0 if 13 <= hour < 16 else 0.0
        
        # Minutes since session open
        if 0 <= hour < 8:
            features['minutes_since_open'] = float(hour * 60 + latest_time.minute)
        elif 8 <= hour < 16:
            features['minutes_since_open'] = float((hour - 8) * 60 + latest_time.minute)
        else:
            features['minutes_since_open'] = float((hour - 13) * 60 + latest_time.minute)
        
        # Day of week (0 = Monday)
        features['day_of_week'] = float(latest_time.weekday())
        
        return features
    
    def _extract_structure_features(self, df: pd.DataFrame) -> Dict[str, float]:
        """Higher-high/lower-low structural analysis"""
        features = {}
        
        # Count recent higher highs and lower lows
        highs = df['high'].tail(20).values
        lows = df['low'].tail(20).values
        
        higher_highs = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i-1])
        lower_lows = sum(1 for i in range(1, len(lows)) if lows[i] < lows[i-1])
        
        features['higher_high_count'] = float(higher_highs)
        features['lower_low_count'] = float(lower_lows)
        features['structure_bias'] = float(higher_highs - lower_lows)  # Positive = uptrend structure
        
        # Swing high/low detection (simplified)
        recent_high = df['high'].tail(10).max()
        recent_low = df['low'].tail(10).min()
        current_close = df['close'].iloc[-1]
        
        features['dist_from_high'] = float((recent_high - current_close) / current_close * 100)
        features['dist_from_low'] = float((current_close - recent_low) / current_close * 100)
        
        return features
    
    def _extract_price_action_features(self, df: pd.DataFrame) -> Dict[str, float]:
        """Candlestick patterns and price action"""
        features = {}
        
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        # Candle body and wick analysis
        body = abs(latest['close'] - latest['open'])
        upper_wick = latest['high'] - max(latest['open'], latest['close'])
        lower_wick = min(latest['open'], latest['close']) - latest['low']
        total_range = latest['high'] - latest['low']
        
        features['body_ratio'] = float(body / total_range) if total_range > 0 else 0.5
        features['upper_wick_ratio'] = float(upper_wick / total_range) if total_range > 0 else 0.0
        features['lower_wick_ratio'] = float(lower_wick / total_range) if total_range > 0 else 0.0
        
        # Bullish/Bearish candle
        features['candle_direction'] = 1.0 if latest['close'] > latest['open'] else -1.0
        
        # Gap
        features['gap'] = float((latest['open'] - prev['close']) / prev['close'] * 100)
        
        # Consecutive candles
        consecutive_up = 0
        consecutive_down = 0
        for i in range(-1, -6, -1):
            if df.iloc[i]['close'] > df.iloc[i]['open']:
                consecutive_up += 1
            else:
                break
        for i in range(-1, -6, -1):
            if df.iloc[i]['close'] < df.iloc[i]['open']:
                consecutive_down += 1
            else:
                break
        
        features['consecutive_bullish'] = float(consecutive_up)
        features['consecutive_bearish'] = float(consecutive_down)
        
        return features
    
    def get_feature_vector(self, features: Dict[str, Any]) -> np.ndarray:
        """Convert features dict to numpy array for ML model"""
        numeric_features = [v for k, v in features.items() if k != 'symbol' and isinstance(v, (int, float))]
        return np.array(numeric_features).reshape(1, -1)

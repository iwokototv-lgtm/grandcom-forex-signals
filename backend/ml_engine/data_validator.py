"""
Data Validation Layer - Ensures data integrity across all operations
Prevents corruption and invalid data from propagating through the system
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Tuple
import logging

logger = logging.getLogger(__name__)


class DataValidator:
    """Comprehensive data validation for OHLC, signals, and system state"""

    @staticmethod
    def validate_ohlc(df: pd.DataFrame, symbol: str = "UNKNOWN") -> Tuple[bool, str]:
        """
        Validate OHLC data integrity
        
        Returns:
            (is_valid, error_message)
        """
        try:
            # Check 1: Not empty
            if len(df) == 0:
                return False, "DataFrame is empty"
            
            # Check 2: Required columns exist
            required_cols = ['open', 'high', 'low', 'close']
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols:
                return False, f"Missing columns: {missing_cols}"
            
            # Check 3: No NaN values in OHLC
            if df[required_cols].isna().any().any():
                nan_count = df[required_cols].isna().sum().sum()
                return False, f"Found {nan_count} NaN values in OHLC"
            
            # Check 4: All values are numeric
            for col in required_cols:
                if not pd.api.types.is_numeric_dtype(df[col]):
                    return False, f"Column {col} is not numeric"
            
            # Check 5: All values are positive
            if (df[required_cols] <= 0).any().any():
                return False, "Found non-positive values in OHLC"
            
            # Check 6: High >= Low
            if not (df['high'] >= df['low']).all():
                invalid_count = (~(df['high'] >= df['low'])).sum()
                return False, f"{invalid_count} rows have high < low"
            
            # Check 7: High >= Open and Close
            if not (df['high'] >= df['open']).all():
                return False, "High < Open in some rows"
            if not (df['high'] >= df['close']).all():
                return False, "High < Close in some rows"
            
            # Check 8: Low <= Open and Close
            if not (df['low'] <= df['open']).all():
                return False, "Low > Open in some rows"
            if not (df['low'] <= df['close']).all():
                return False, "Low > Close in some rows"
            
            # Check 9: Reasonable price ranges (no 1000x jumps)
            for col in required_cols:
                pct_change = df[col].pct_change().abs()
                if (pct_change > 0.5).any():  # More than 50% change
                    return False, f"Unrealistic price jump in {col}"
            
            # Check 10: Datetime column if present
            if 'datetime' in df.columns:
                if not pd.api.types.is_datetime64_any_dtype(df['datetime']):
                    return False, "Datetime column is not datetime type"
                if not df['datetime'].is_monotonic_increasing:
                    return False, "Datetime is not monotonically increasing"
            
            logger.info(f"✅ OHLC validation passed for {symbol} ({len(df)} candles)")
            return True, "Valid"
        
        except Exception as e:
            logger.error(f"OHLC validation error for {symbol}: {e}")
            return False, f"Validation error: {str(e)}"

    @staticmethod
    def validate_signal(signal: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate signal structure and values
        
        Returns:
            (is_valid, error_message)
        """
        try:
            # Check 1: Required fields
            required_fields = [
                'symbol', 'signal', 'confidence', 'entry_price',
                'tp_levels', 'sl_price', 'regime', 'smc_score'
            ]
            missing = [f for f in required_fields if f not in signal]
            if missing:
                return False, f"Missing fields: {missing}"
            
            # Check 2: Signal type
            if signal['signal'] not in ['BUY', 'SELL', 'NEUTRAL']:
                return False, f"Invalid signal type: {signal['signal']}"
            
            # Check 3: Confidence range (0-100)
            conf = signal['confidence']
            if not isinstance(conf, (int, float)) or conf < 0 or conf > 100:
                return False, f"Invalid confidence: {conf}"
            
            # Check 4: Prices are positive
            if signal['entry_price'] <= 0:
                return False, "Entry price must be positive"
            if signal['sl_price'] <= 0:
                return False, "SL price must be positive"
            
            # Check 5: TP levels are valid
            if not isinstance(signal['tp_levels'], list) or len(signal['tp_levels']) == 0:
                return False, "TP levels must be non-empty list"
            for i, tp in enumerate(signal['tp_levels']):
                if tp <= 0:
                    return False, f"TP{i+1} must be positive"
            
            # Check 6: SL is on correct side
            if signal['signal'] == 'BUY':
                if signal['sl_price'] >= signal['entry_price']:
                    return False, "BUY: SL must be below entry"
                for tp in signal['tp_levels']:
                    if tp <= signal['entry_price']:
                        return False, "BUY: TP must be above entry"
            elif signal['signal'] == 'SELL':
                if signal['sl_price'] <= signal['entry_price']:
                    return False, "SELL: SL must be above entry"
                for tp in signal['tp_levels']:
                    if tp >= signal['entry_price']:
                        return False, "SELL: TP must be below entry"
            
            # Check 7: SMC score range
            if not isinstance(signal['smc_score'], (int, float)) or signal['smc_score'] < 0 or signal['smc_score'] > 10:
                return False, f"Invalid SMC score: {signal['smc_score']}"
            
            # Check 8: Regime is valid
            valid_regimes = ['TREND_UP', 'TREND_DOWN', 'RANGE', 'BREAKOUT', 'CONSOLIDATION']
            if signal['regime'] not in valid_regimes:
                return False, f"Invalid regime: {signal['regime']}"
            
            logger.info(f"✅ Signal validation passed: {signal['symbol']} {signal['signal']}")
            return True, "Valid"
        
        except Exception as e:
            logger.error(f"Signal validation error: {e}")
            return False, f"Validation error: {str(e)}"

    @staticmethod
    def validate_mtf_result(result: Dict[str, Any]) -> Tuple[bool, str]:
        """Validate MTF analysis result"""
        try:
            if not isinstance(result, dict):
                return False, "MTF result must be dict"
            
            if 'alignment_score' not in result:
                return False, "Missing alignment_score"
            
            score = result['alignment_score']
            if not isinstance(score, (int, float)) or score < 0 or score > 100:
                return False, f"Invalid alignment score: {score}"
            
            if 'dominant_direction' not in result:
                return False, "Missing dominant_direction"
            
            direction = result['dominant_direction']
            if direction not in ['BULLISH', 'BEARISH', 'NEUTRAL']:
                return False, f"Invalid direction: {direction}"
            
            logger.info(f"✅ MTF validation passed: {score}% {direction}")
            return True, "Valid"
        
        except Exception as e:
            logger.error(f"MTF validation error: {e}")
            return False, f"Validation error: {str(e)}"

    @staticmethod
    def validate_smc_result(result: Dict[str, Any]) -> Tuple[bool, str]:
        """Validate SMC/ICT analysis result"""
        try:
            if not isinstance(result, dict):
                return False, "SMC result must be dict"
            
            if 'smc_score' not in result:
                return False, "Missing smc_score"
            
            score = result['smc_score']
            if not isinstance(score, (int, float)) or score < 0 or score > 10:
                return False, f"Invalid SMC score: {score}"
            
            if 'bias' not in result:
                return False, "Missing bias"
            
            bias = result['bias']
            if bias not in ['BULLISH', 'BEARISH', 'NEUTRAL']:
                return False, f"Invalid bias: {bias}"
            
            logger.info(f"✅ SMC validation passed: {score}/10 {bias}")
            return True, "Valid"
        
        except Exception as e:
            logger.error(f"SMC validation error: {e}")
            return False, f"Validation error: {str(e)}"

    @staticmethod
    def sanitize_ohlc(df: pd.DataFrame) -> pd.DataFrame:
        """
        Sanitize OHLC data - fix common issues
        
        Returns:
            Cleaned DataFrame
        """
        df = df.copy()
        
        # Convert to numeric
        for col in ['open', 'high', 'low', 'close']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Remove NaN rows
        df = df.dropna(subset=['open', 'high', 'low', 'close'])
        
        # Fix high/low if needed
        df['high'] = df[['open', 'high', 'low', 'close']].max(axis=1)
        df['low'] = df[['open', 'high', 'low', 'close']].min(axis=1)
        
        # Handle volume
        if 'volume' in df.columns:
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)
        else:
            df['volume'] = 0
        
        logger.info(f"✅ Sanitized OHLC: {len(df)} valid candles")
        return df


# Global validator instance
data_validator = DataValidator()


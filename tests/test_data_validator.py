"""
Unit tests for data validation
Tests all validation functions to ensure data integrity
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from ml_engine.data_validator import DataValidator


class TestOHLCValidation:
    """Test OHLC data validation"""
    
    def test_valid_ohlc(self):
        """Test valid OHLC data"""
        df = pd.DataFrame({
            'open': [100.0, 101.0, 102.0],
            'high': [102.0, 103.0, 104.0],
            'low': [99.0, 100.0, 101.0],
            'close': [101.0, 102.0, 103.0],
        })
        is_valid, msg = DataValidator.validate_ohlc(df, "TEST")
        assert is_valid, msg
    
    def test_empty_dataframe(self):
        """Test empty DataFrame"""
        df = pd.DataFrame()
        is_valid, msg = DataValidator.validate_ohlc(df, "TEST")
        assert not is_valid
        assert "empty" in msg.lower()
    
    def test_missing_columns(self):
        """Test missing required columns"""
        df = pd.DataFrame({
            'open': [100.0],
            'high': [102.0],
            # Missing 'low' and 'close'
        })
        is_valid, msg = DataValidator.validate_ohlc(df, "TEST")
        assert not is_valid
        assert "missing" in msg.lower()
    
    def test_nan_values(self):
        """Test NaN values in OHLC"""
        df = pd.DataFrame({
            'open': [100.0, np.nan],
            'high': [102.0, 103.0],
            'low': [99.0, 100.0],
            'close': [101.0, 102.0],
        })
        is_valid, msg = DataValidator.validate_ohlc(df, "TEST")
        assert not is_valid
        assert "nan" in msg.lower()
    
    def test_non_numeric_values(self):
        """Test non-numeric values"""
        df = pd.DataFrame({
            'open': ['100', '101'],
            'high': [102.0, 103.0],
            'low': [99.0, 100.0],
            'close': [101.0, 102.0],
        })
        is_valid, msg = DataValidator.validate_ohlc(df, "TEST")
        # Should fail because 'open' is string
        assert not is_valid
    
    def test_negative_prices(self):
        """Test negative prices"""
        df = pd.DataFrame({
            'open': [100.0, -101.0],
            'high': [102.0, 103.0],
            'low': [99.0, 100.0],
            'close': [101.0, 102.0],
        })
        is_valid, msg = DataValidator.validate_ohlc(df, "TEST")
        assert not is_valid
        assert "positive" in msg.lower()
    
    def test_high_less_than_low(self):
        """Test high < low"""
        df = pd.DataFrame({
            'open': [100.0, 101.0],
            'high': [98.0, 103.0],  # First high < low
            'low': [99.0, 100.0],
            'close': [101.0, 102.0],
        })
        is_valid, msg = DataValidator.validate_ohlc(df, "TEST")
        assert not is_valid
        assert "high" in msg.lower() and "low" in msg.lower()
    
    def test_unrealistic_price_jump(self):
        """Test unrealistic price jumps"""
        df = pd.DataFrame({
            'open': [100.0, 100.0],
            'high': [102.0, 300.0],  # 200% jump
            'low': [99.0, 99.0],
            'close': [101.0, 250.0],
        })
        is_valid, msg = DataValidator.validate_ohlc(df, "TEST")
        assert not is_valid
        assert "jump" in msg.lower()


class TestSignalValidation:
    """Test signal validation"""
    
    def test_valid_buy_signal(self):
        """Test valid BUY signal"""
        signal = {
            'symbol': 'XAUUSD',
            'signal': 'BUY',
            'confidence': 75.0,
            'entry_price': 4542.56,
            'tp_levels': [4577.12, 4603.04, 4628.96],
            'sl_price': 4516.64,
            'regime': 'RANGE',
            'smc_score': 8
        }
        is_valid, msg = DataValidator.validate_signal(signal)
        assert is_valid, msg
    
    def test_valid_sell_signal(self):
        """Test valid SELL signal"""
        signal = {
            'symbol': 'XAUUSD',
            'signal': 'SELL',
            'confidence': 70.0,
            'entry_price': 4542.56,
            'tp_levels': [4507.99, 4482.07, 4456.15],
            'sl_price': 4568.47,
            'regime': 'TREND_DOWN',
            'smc_score': 7
        }
        is_valid, msg = DataValidator.validate_signal(signal)
        assert is_valid, msg
    
    def test_missing_fields(self):
        """Test missing required fields"""
        signal = {
            'symbol': 'XAUUSD',
            'signal': 'BUY',
            # Missing other fields
        }
        is_valid, msg = DataValidator.validate_signal(signal)
        assert not is_valid
        assert "missing" in msg.lower()
    
    def test_invalid_signal_type(self):
        """Test invalid signal type"""
        signal = {
            'symbol': 'XAUUSD',
            'signal': 'INVALID',
            'confidence': 75.0,
            'entry_price': 4542.56,
            'tp_levels': [4577.12],
            'sl_price': 4516.64,
            'regime': 'RANGE',
            'smc_score': 8
        }
        is_valid, msg = DataValidator.validate_signal(signal)
        assert not is_valid
        assert "invalid" in msg.lower()
    
    def test_confidence_out_of_range(self):
        """Test confidence out of range"""
        signal = {
            'symbol': 'XAUUSD',
            'signal': 'BUY',
            'confidence': 150.0,  # > 100
            'entry_price': 4542.56,
            'tp_levels': [4577.12],
            'sl_price': 4516.64,
            'regime': 'RANGE',
            'smc_score': 8
        }
        is_valid, msg = DataValidator.validate_signal(signal)
        assert not is_valid
        assert "confidence" in msg.lower()
    
    def test_buy_sl_above_entry(self):
        """Test BUY signal with SL above entry"""
        signal = {
            'symbol': 'XAUUSD',
            'signal': 'BUY',
            'confidence': 75.0,
            'entry_price': 4542.56,
            'tp_levels': [4577.12],
            'sl_price': 4550.0,  # Above entry
            'regime': 'RANGE',
            'smc_score': 8
        }
        is_valid, msg = DataValidator.validate_signal(signal)
        assert not is_valid
        assert "sl" in msg.lower()
    
    def test_sell_sl_below_entry(self):
        """Test SELL signal with SL below entry"""
        signal = {
            'symbol': 'XAUUSD',
            'signal': 'SELL',
            'confidence': 75.0,
            'entry_price': 4542.56,
            'tp_levels': [4500.0],
            'sl_price': 4500.0,  # Below entry
            'regime': 'RANGE',
            'smc_score': 8
        }
        is_valid, msg = DataValidator.validate_signal(signal)
        assert not is_valid
        assert "sl" in msg.lower()


class TestMTFValidation:
    """Test MTF result validation"""
    
    def test_valid_mtf_result(self):
        """Test valid MTF result"""
        result = {
            'alignment_score': 47.0,
            'dominant_direction': 'NEUTRAL',
            'valid': True
        }
        is_valid, msg = DataValidator.validate_mtf_result(result)
        assert is_valid, msg
    
    def test_invalid_alignment_score(self):
        """Test invalid alignment score"""
        result = {
            'alignment_score': 150.0,  # > 100
            'dominant_direction': 'NEUTRAL',
            'valid': True
        }
        is_valid, msg = DataValidator.validate_mtf_result(result)
        assert not is_valid
        assert "alignment" in msg.lower()
    
    def test_invalid_direction(self):
        """Test invalid direction"""
        result = {
            'alignment_score': 47.0,
            'dominant_direction': 'INVALID',
            'valid': True
        }
        is_valid, msg = DataValidator.validate_mtf_result(result)
        assert not is_valid
        assert "direction" in msg.lower()


class TestDataSanitization:
    """Test data sanitization"""
    
    def test_sanitize_with_nan(self):
        """Test sanitization removes NaN"""
        df = pd.DataFrame({
            'open': [100.0, np.nan, 102.0],
            'high': [102.0, 103.0, 104.0],
            'low': [99.0, 100.0, 101.0],
            'close': [101.0, 102.0, 103.0],
        })
        sanitized = DataValidator.sanitize_ohlc(df)
        assert len(sanitized) == 2  # NaN row removed
        assert not sanitized.isna().any().any()
    
    def test_sanitize_converts_to_numeric(self):
        """Test sanitization converts to numeric"""
        df = pd.DataFrame({
            'open': ['100', '101', '102'],
            'high': ['102', '103', '104'],
            'low': ['99', '100', '101'],
            'close': ['101', '102', '103'],
        })
        sanitized = DataValidator.sanitize_ohlc(df)
        assert pd.api.types.is_numeric_dtype(sanitized['open'])
    
    def test_sanitize_adds_volume(self):
        """Test sanitization adds volume if missing"""
        df = pd.DataFrame({
            'open': [100.0, 101.0],
            'high': [102.0, 103.0],
            'low': [99.0, 100.0],
            'close': [101.0, 102.0],
        })
        sanitized = DataValidator.sanitize_ohlc(df)
        assert 'volume' in sanitized.columns
        assert (sanitized['volume'] == 0).all()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])


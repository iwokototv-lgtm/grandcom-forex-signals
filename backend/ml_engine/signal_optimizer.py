"""
Signal Optimizer
Combines ML regime detection with signal generation for optimized trading signals
"""
import numpy as np
from typing import Dict, Any, Optional, List
from datetime import datetime
import logging

from .feature_engineering import FeatureEngineer
from .regime_detector import RegimeDetector, MarketRegime
from .risk_manager import RiskManager

logger = logging.getLogger(__name__)


class SignalOptimizer:
    """
    Optimizes trading signals using ML regime detection and risk management.
    
    Workflow:
    1. Extract features from price data
    2. Detect market regime
    3. Apply strategy filters based on regime
    4. Optimize entry/exit levels
    5. Calculate position size with risk management
    """
    
    def __init__(self):
        self.feature_engineer = FeatureEngineer()
        self.regime_detector = RegimeDetector()
        self.risk_manager = RiskManager()
        
        # Strategy performance tracking
        self.strategy_stats = {
            'breakout': {'wins': 0, 'losses': 0},
            'pullback': {'wins': 0, 'losses': 0},
            'reversal': {'wins': 0, 'losses': 0},
            'mean_reversion': {'wins': 0, 'losses': 0}
        }
    
    def optimize_signal(
        self,
        df,  # pandas DataFrame with OHLCV data
        symbol: str,
        ai_signal: Dict[str, Any],
        pair_params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Optimize a trading signal using ML analysis.
        
        Args:
            df: Price data DataFrame
            symbol: Trading pair symbol
            ai_signal: Raw AI-generated signal
            pair_params: Pair-specific optimization parameters
            
        Returns:
            Optimized signal with regime context and risk parameters
        """
        try:
            # Step 1: Extract features
            features = self.feature_engineer.extract_features(df, symbol)
            if not features:
                logger.warning(f"Feature extraction failed for {symbol}")
                return self._add_default_optimization(ai_signal, symbol)
            
            # Step 2: Detect regime
            regime_result = self.regime_detector.detect_regime(features)
            
            # Step 3: Check if trading is allowed
            risk_check = self.risk_manager.check_trading_allowed()
            if not risk_check['allowed']:
                logger.warning(f"Trading blocked: {risk_check['restrictions']}")
                return {
                    **ai_signal,
                    'optimized': True,
                    'blocked': True,
                    'block_reason': risk_check['restrictions'],
                    'regime': regime_result
                }
            
            # Step 4: Apply regime-based filtering
            should_trade = self._should_trade_in_regime(
                ai_signal['signal'],
                regime_result
            )
            
            if not should_trade['approved']:
                return {
                    **ai_signal,
                    'optimized': True,
                    'filtered': True,
                    'filter_reason': should_trade['reason'],
                    'regime': regime_result
                }
            
            # Step 5: Optimize levels based on regime
            optimized_levels = self._optimize_levels(
                ai_signal,
                features,
                regime_result,
                pair_params
            )
            
            # Step 6: Calculate position size
            position_sizing = self.risk_manager.calculate_position_size(
                symbol=symbol,
                entry_price=optimized_levels['entry_price'],
                stop_loss=optimized_levels['sl_price'],
                regime_multiplier=regime_result['risk_multiplier'],
                volatility_multiplier=features.get('atr_ratio_20', 1.0)
            )
            
            # Compile optimized signal
            optimized_signal = {
                **ai_signal,
                'optimized': True,
                'entry_price': optimized_levels['entry_price'],
                'sl_price': optimized_levels['sl_price'],
                'tp_levels': optimized_levels['tp_levels'],
                'regime': {
                    'name': regime_result['regime_name'],
                    'confidence': regime_result['confidence'],
                    'risk_multiplier': regime_result['risk_multiplier']
                },
                'position_sizing': position_sizing,
                'features_summary': {
                    'adx': features.get('adx'),
                    'rsi': features.get('rsi'),
                    'atr_ratio': features.get('atr_ratio_20'),
                    'trend': 'UP' if features.get('ma20_slope', 0) > 0 else 'DOWN'
                },
                'strategy_used': should_trade.get('strategy', 'default'),
                'optimization_timestamp': datetime.utcnow().isoformat()
            }
            
            logger.info(
                f"Signal optimized for {symbol}: "
                f"Regime={regime_result['regime_name']}, "
                f"Strategy={should_trade.get('strategy')}, "
                f"Risk Mult={regime_result['risk_multiplier']}"
            )
            
            return optimized_signal
            
        except Exception as e:
            logger.error(f"Signal optimization error for {symbol}: {e}")
            return self._add_default_optimization(ai_signal, symbol)
    
    def _should_trade_in_regime(
        self,
        signal_type: str,
        regime_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Determine if trade should be taken based on regime.
        """
        regime = regime_result['regime']
        active_strategies = regime_result['active_strategies']
        confidence = regime_result['confidence']
        
        # No trading in chaos regime
        if regime == MarketRegime.CHAOS:
            return {
                'approved': False,
                'reason': 'CHAOS regime detected - no trading'
            }
        
        # Low confidence = no trade
        if confidence < 0.6:
            return {
                'approved': False,
                'reason': f'Low regime confidence ({confidence:.2f})'
            }
        
        # Determine strategy type based on signal
        if regime in [MarketRegime.TREND_UP, MarketRegime.TREND_DOWN]:
            if signal_type in ['BUY', 'SELL']:
                # In trends, allow breakout and pullback strategies
                return {
                    'approved': True,
                    'strategy': 'breakout' if regime_result.get('atr_ratio', 1) > 1.2 else 'pullback'
                }
        
        if regime == MarketRegime.RANGE:
            # In range, prefer reversal signals
            return {
                'approved': True,
                'strategy': 'reversal'
            }
        
        if regime in [MarketRegime.HIGH_VOLATILITY, MarketRegime.LOW_VOLATILITY]:
            return {
                'approved': True,
                'strategy': 'breakout' if regime == MarketRegime.HIGH_VOLATILITY else 'mean_reversion'
            }
        
        return {'approved': True, 'strategy': 'default'}
    
    def _optimize_levels(
        self,
        signal: Dict[str, Any],
        features: Dict[str, Any],
        regime_result: Dict[str, Any],
        pair_params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Optimize entry, SL, and TP levels based on regime and volatility.
        For fixed pip pairs, only optimize SL (TPs are fixed).
        """
        entry_price = signal['entry_price']
        signal_type = signal['signal']
        atr = features.get('atr_current', 0)
        regime = regime_result['regime']
        decimal_places = pair_params.get('decimal_places', 5)
        
        # Check if this is a fixed pip pair
        use_fixed_pips = pair_params.get('use_fixed_pips', False)
        
        if use_fixed_pips:
            # For fixed pip pairs, only optimize SL, keep TPs fixed
            pip_value = pair_params.get('pip_value', 0.0001)
            tp1_pips = pair_params.get('fixed_tp1_pips', 5)
            tp2_pips = pair_params.get('fixed_tp2_pips', 10)
            tp3_pips = pair_params.get('fixed_tp3_pips', 15)
            
            # Calculate fixed TPs
            if signal_type == 'BUY':
                tp1 = round(entry_price + (tp1_pips * pip_value), decimal_places)
                tp2 = round(entry_price + (tp2_pips * pip_value), decimal_places)
                tp3 = round(entry_price + (tp3_pips * pip_value), decimal_places)
                # SL from ATR
                sl_mult = pair_params.get('atr_multiplier_sl', 1.2)
                sl_price = round(entry_price - (atr * sl_mult), decimal_places)
            else:  # SELL
                tp1 = round(entry_price - (tp1_pips * pip_value), decimal_places)
                tp2 = round(entry_price - (tp2_pips * pip_value), decimal_places)
                tp3 = round(entry_price - (tp3_pips * pip_value), decimal_places)
                # SL from ATR
                sl_mult = pair_params.get('atr_multiplier_sl', 1.2)
                sl_price = round(entry_price + (atr * sl_mult), decimal_places)
            
            return {
                'entry_price': entry_price,
                'sl_price': sl_price,
                'tp_levels': [tp1, tp2, tp3]
            }
        
        # ATR-based optimization for non-fixed-pip pairs
        # Adjust ATR multipliers based on regime
        if regime == MarketRegime.HIGH_VOLATILITY:
            sl_mult = pair_params.get('atr_multiplier_sl', 1.5) * 1.3  # Wider SL in high vol
            tp_mult_1 = pair_params.get('atr_multiplier_tp1', 1.0) * 0.8  # Tighter TP1
            tp_mult_2 = pair_params.get('atr_multiplier_tp2', 2.0) * 0.9
            tp_mult_3 = pair_params.get('atr_multiplier_tp3', 3.0) * 0.9
        elif regime == MarketRegime.LOW_VOLATILITY:
            sl_mult = pair_params.get('atr_multiplier_sl', 1.5) * 0.8  # Tighter SL
            tp_mult_1 = pair_params.get('atr_multiplier_tp1', 1.0) * 1.2  # Extended TP
            tp_mult_2 = pair_params.get('atr_multiplier_tp2', 2.0) * 1.2
            tp_mult_3 = pair_params.get('atr_multiplier_tp3', 3.0) * 1.2
        else:
            sl_mult = pair_params.get('atr_multiplier_sl', 1.5)
            tp_mult_1 = pair_params.get('atr_multiplier_tp1', 1.0)
            tp_mult_2 = pair_params.get('atr_multiplier_tp2', 2.0)
            tp_mult_3 = pair_params.get('atr_multiplier_tp3', 3.0)
        
        # Calculate optimized levels
        if signal_type == 'BUY':
            sl_price = round(entry_price - (atr * sl_mult), decimal_places)
            tp1 = round(entry_price + (atr * tp_mult_1), decimal_places)
            tp2 = round(entry_price + (atr * tp_mult_2), decimal_places)
            tp3 = round(entry_price + (atr * tp_mult_3), decimal_places)
        else:  # SELL
            sl_price = round(entry_price + (atr * sl_mult), decimal_places)
            tp1 = round(entry_price - (atr * tp_mult_1), decimal_places)
            tp2 = round(entry_price - (atr * tp_mult_2), decimal_places)
            tp3 = round(entry_price - (atr * tp_mult_3), decimal_places)
        
        # Ensure minimum RR
        min_rr = pair_params.get('min_rr', 2.0)
        sl_distance = abs(entry_price - sl_price)
        tp1_distance = abs(tp1 - entry_price)
        
        if sl_distance > 0 and tp1_distance / sl_distance < min_rr:
            # Adjust TP1 to meet minimum RR
            if signal_type == 'BUY':
                tp1 = round(entry_price + (sl_distance * min_rr), decimal_places)
            else:
                tp1 = round(entry_price - (sl_distance * min_rr), decimal_places)
        
        return {
            'entry_price': entry_price,
            'sl_price': sl_price,
            'tp_levels': [tp1, tp2, tp3]
        }
    
    def _add_default_optimization(self, signal: Dict[str, Any], symbol: str) -> Dict[str, Any]:
        """Add default optimization metadata when ML fails"""
        return {
            **signal,
            'optimized': False,
            'regime': {
                'name': 'UNKNOWN',
                'confidence': 0.5,
                'risk_multiplier': 0.5
            },
            'optimization_error': True
        }
    
    def record_signal_result(self, symbol: str, strategy: str, result: str, pnl: float):
        """
        Record signal result for performance tracking.
        """
        if strategy in self.strategy_stats:
            if result == 'WIN':
                self.strategy_stats[strategy]['wins'] += 1
            else:
                self.strategy_stats[strategy]['losses'] += 1
        
        self.risk_manager.record_trade_result(result, pnl)
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance statistics"""
        stats = {}
        
        for strategy, data in self.strategy_stats.items():
            total = data['wins'] + data['losses']
            win_rate = data['wins'] / total if total > 0 else 0
            stats[strategy] = {
                'wins': data['wins'],
                'losses': data['losses'],
                'total': total,
                'win_rate': round(win_rate * 100, 2)
            }
        
        stats['risk_metrics'] = self.risk_manager.get_risk_metrics()
        stats['regime_stats'] = self.regime_detector.get_regime_stats()
        
        return stats

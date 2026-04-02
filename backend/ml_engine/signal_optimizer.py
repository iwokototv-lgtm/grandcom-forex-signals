"""
Signal Optimizer
Combines ML regime detection with signal generation for optimized trading signals.

Phase 2 additions:
- Pair-specific confidence thresholds (gold 75%, institutional 70%, JPY 70%, standard 65-70%)
- Order block proximity detection (price must be near a valid order block)
- Signal quality scoring 0-100 (only signals scoring 70+ are traded)
- Pair-specific filtering rules per category
"""
import numpy as np
import pandas as pd
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
import logging

from .feature_engineering import FeatureEngineer
from .regime_detector import RegimeDetector, MarketRegime
from .risk_manager import RiskManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pair category definitions (Phase 2: pair-specific optimization)
# ---------------------------------------------------------------------------

# Gold pairs — premium filtering, strictest thresholds
GOLD_PAIRS: List[str] = ["XAUUSD", "XAUEUR"]

# Institutional / low-volatility pairs — conservative entry rules
INSTITUTIONAL_PAIRS: List[str] = ["EURGBP", "EURCHF"]

# JPY crosses — volatile, require higher confidence
JPY_PAIRS: List[str] = ["USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY"]

# Pair-specific minimum confidence thresholds (Phase 2)
PAIR_CONFIDENCE_THRESHOLDS: Dict[str, float] = {
    # Gold pairs: 75% — premium filtering for maximum reliability
    "XAUUSD": 75.0,
    "XAUEUR": 75.0,
    # Institutional pairs: 70% — conservative entry rules
    "EURGBP": 70.0,
    "EURCHF": 70.0,
    # JPY crosses: 70% — volatile, need stronger confirmation
    "USDJPY": 70.0,
    "EURJPY": 70.0,
    "GBPJPY": 70.0,
    "AUDJPY": 70.0,
    "CADJPY": 70.0,
    "CHFJPY": 70.0,
    # Standard forex pairs: 65%
    "EURUSD": 65.0,
    "GBPUSD": 65.0,
    "AUDUSD": 65.0,
    "USDCAD": 65.0,
    "USDCHF": 65.0,
    "NZDUSD": 65.0,
    "EURAUD": 65.0,
    "GBPCAD": 65.0,
    "EURCAD": 65.0,
    "GBPAUD": 65.0,
    "AUDNZD": 65.0,
}

# Minimum signal quality score required to trade (Phase 2)
MIN_SIGNAL_QUALITY_SCORE: int = 70

# Order block proximity threshold — price must be within this fraction of ATR
# to be considered "near" an order block
ORDER_BLOCK_PROXIMITY_ATR_MULT: float = 0.5


class SignalOptimizer:
    """
    Optimizes trading signals using ML regime detection and risk management.

    Phase 2 enhancements:
    - Pair-specific confidence thresholds
    - Order block proximity detection
    - Signal quality scoring (0-100)
    - Pair-specific filtering rules

    Workflow:
    1. Extract features from price data
    2. Detect market regime
    3. Apply strategy filters based on regime
    4. Optimize entry/exit levels
    5. Calculate position size with risk management
    6. [Phase 2] Score signal quality and apply pair-specific filters
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
        pair_params: Dict[str, Any],
        mtf_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Optimize a trading signal using ML analysis.

        Phase 2 additions:
        - Pair-specific confidence threshold check (step 2b)
        - Order block proximity detection (step 5b)
        - Signal quality scoring 0-100 (step 7)
        - Pair-specific filtering rules (step 8)

        Args:
            df: Price data DataFrame
            symbol: Trading pair symbol
            ai_signal: Raw AI-generated signal
            pair_params: Pair-specific optimization parameters
            mtf_result: Optional multi-timeframe analysis result

        Returns:
            Optimized signal with regime context, risk parameters, and quality score
        """
        try:
            # Step 1: Extract features
            features = self.feature_engineer.extract_features(df, symbol)
            if not features:
                logger.warning(f"Feature extraction failed for {symbol}")
                return self._add_default_optimization(ai_signal, symbol)

            # Step 2: Detect regime
            regime_result = self.regime_detector.detect_regime(features)

            # Step 2b: Pair-specific confidence threshold (Phase 2)
            confidence = ai_signal.get('confidence', 0)
            pair_min_confidence = self.get_pair_confidence_threshold(symbol)
            if confidence < pair_min_confidence:
                logger.info(
                    f"[Phase2] {symbol} filtered — confidence {confidence}% "
                    f"< pair threshold {pair_min_confidence}%"
                )
                return {
                    **ai_signal,
                    'optimized': True,
                    'filtered': True,
                    'filter_reason': (
                        f"Confidence {confidence}% below pair-specific threshold "
                        f"{pair_min_confidence}% for {symbol}"
                    ),
                    'regime': regime_result,
                    'pair_category': self._get_pair_category(symbol),
                    'pair_confidence_threshold': pair_min_confidence,
                }

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

            # Step 5b: Order block proximity detection (Phase 2)
            atr_current = features.get('atr_current', 0)
            ob_result = self.detect_order_block_proximity(
                df=df,
                entry_price=optimized_levels['entry_price'],
                signal_type=ai_signal['signal'],
                atr=atr_current,
            )

            # Step 6: Calculate position size
            position_sizing = self.risk_manager.calculate_position_size(
                symbol=symbol,
                entry_price=optimized_levels['entry_price'],
                stop_loss=optimized_levels['sl_price'],
                regime_multiplier=regime_result['risk_multiplier'],
                volatility_multiplier=features.get('atr_ratio_20', 1.0)
            )

            # Step 7: Signal quality scoring 0-100 (Phase 2)
            quality_score = self.calculate_signal_quality_score(
                symbol=symbol,
                confidence=confidence,
                regime_result=regime_result,
                ob_result=ob_result,
                entry_price=optimized_levels['entry_price'],
                sl_price=optimized_levels['sl_price'],
                tp_levels=optimized_levels['tp_levels'],
                mtf_result=mtf_result,
            )

            # Step 8: Pair-specific filtering (Phase 2)
            pair_filter_result = self.apply_pair_specific_filter(
                symbol=symbol,
                signal_type=ai_signal['signal'],
                confidence=confidence,
                quality_score=quality_score,
                ob_result=ob_result,
                regime_result=regime_result,
            )

            if not pair_filter_result['approved']:
                logger.info(
                    f"[Phase2] {symbol} filtered by pair-specific rules: "
                    f"{pair_filter_result['reason']}"
                )
                return {
                    **ai_signal,
                    'optimized': True,
                    'filtered': True,
                    'filter_reason': pair_filter_result['reason'],
                    'regime': regime_result,
                    'quality_score': quality_score,
                    'pair_category': self._get_pair_category(symbol),
                }

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
                'optimization_timestamp': datetime.utcnow().isoformat(),
                # Phase 2 additions
                'quality_score': quality_score,
                'order_block': ob_result,
                'pair_category': self._get_pair_category(symbol),
                'pair_confidence_threshold': pair_min_confidence,
            }

            logger.info(
                f"Signal optimized for {symbol}: "
                f"Regime={regime_result['regime_name']}, "
                f"Strategy={should_trade.get('strategy')}, "
                f"Risk Mult={regime_result['risk_multiplier']}, "
                f"QualityScore={quality_score}, "
                f"OB_near={ob_result.get('near_order_block', False)}"
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

    # -----------------------------------------------------------------------
    # Phase 2: Pair-specific optimization helpers
    # -----------------------------------------------------------------------

    def get_pair_confidence_threshold(self, symbol: str) -> float:
        """
        Return the minimum confidence threshold for a given trading pair.

        Hierarchy:
        - Gold pairs (XAUUSD, XAUEUR): 75%
        - Institutional pairs (EURGBP, EURCHF): 70%
        - JPY crosses: 70%
        - Standard forex: 65%
        - Unknown pairs: 70% (conservative default)
        """
        return PAIR_CONFIDENCE_THRESHOLDS.get(symbol, 70.0)

    def _get_pair_category(self, symbol: str) -> str:
        """Return the category label for a trading pair."""
        if symbol in GOLD_PAIRS:
            return 'GOLD'
        if symbol in INSTITUTIONAL_PAIRS:
            return 'INSTITUTIONAL'
        if symbol in JPY_PAIRS:
            return 'JPY_CROSS'
        if symbol in PAIR_CONFIDENCE_THRESHOLDS:
            return 'STANDARD_FOREX'
        return 'UNKNOWN'

    def detect_order_block_proximity(
        self,
        df,  # pandas DataFrame with OHLCV data
        entry_price: float,
        signal_type: str,
        atr: float,
    ) -> Dict[str, Any]:
        """
        Detect whether the current entry price is near a valid order block.

        An order block is the last opposing candle before a strong impulsive move:
        - Bullish OB: last bearish candle before a strong bullish impulse
        - Bearish OB: last bullish candle before a strong bearish impulse

        A signal is considered "near" an order block when the entry price is
        within ORDER_BLOCK_PROXIMITY_ATR_MULT × ATR of the order block zone.

        Returns a dict with:
            near_order_block (bool): True if price is near a valid OB
            ob_type (str): 'BULLISH' | 'BEARISH' | 'NONE'
            ob_top (float | None): top of the nearest OB zone
            ob_bottom (float | None): bottom of the nearest OB zone
            distance_atr (float): distance to nearest OB in ATR units
            details (str): human-readable explanation
        """
        result: Dict[str, Any] = {
            'near_order_block': False,
            'ob_type': 'NONE',
            'ob_top': None,
            'ob_bottom': None,
            'distance_atr': float('inf'),
            'details': 'No order block detected',
        }

        try:
            if df is None or len(df) < 10:
                result['details'] = 'Insufficient data for OB detection'
                return result

            df = df.copy()
            avg_range = (df['high'] - df['low']).rolling(20).mean()

            order_blocks: List[Dict[str, Any]] = []

            for i in range(3, len(df) - 1):
                current = df.iloc[i]
                prev = df.iloc[i - 1]
                avg = avg_range.iloc[i]

                if avg == 0 or pd.isna(avg):
                    continue

                current_range = current['high'] - current['low']
                # Only consider strong impulsive candles (≥ 1.5× average range)
                if current_range < avg * 1.5:
                    continue

                # Bullish OB: strong bullish candle preceded by a bearish candle
                if current['close'] > current['open'] and prev['close'] < prev['open']:
                    order_blocks.append({
                        'type': 'BULLISH',
                        'top': float(prev['open']),
                        'bottom': float(prev['close']),
                        'index': i - 1,
                    })

                # Bearish OB: strong bearish candle preceded by a bullish candle
                elif current['close'] < current['open'] and prev['close'] > prev['open']:
                    order_blocks.append({
                        'type': 'BEARISH',
                        'top': float(prev['close']),
                        'bottom': float(prev['open']),
                        'index': i - 1,
                    })

            if not order_blocks:
                result['details'] = 'No order blocks found in recent price action'
                return result

            # Filter to OBs that match the signal direction and are not mitigated
            relevant_obs = [
                ob for ob in order_blocks
                if ob['type'] == ('BULLISH' if signal_type == 'BUY' else 'BEARISH')
            ]

            if not relevant_obs:
                result['details'] = (
                    f'No {signal_type}-aligned order blocks found'
                )
                return result

            # Find the nearest unmitigated OB to the entry price
            proximity_threshold = ORDER_BLOCK_PROXIMITY_ATR_MULT * atr if atr > 0 else float('inf')
            best_ob: Optional[Dict[str, Any]] = None
            best_distance = float('inf')

            for ob in relevant_obs[-5:]:  # Check last 5 relevant OBs
                ob_mid = (ob['top'] + ob['bottom']) / 2
                distance = abs(entry_price - ob_mid)
                if distance < best_distance:
                    best_distance = distance
                    best_ob = ob

            if best_ob is None:
                return result

            distance_atr = best_distance / atr if atr > 0 else float('inf')
            near = distance_atr <= ORDER_BLOCK_PROXIMITY_ATR_MULT

            result.update({
                'near_order_block': near,
                'ob_type': best_ob['type'],
                'ob_top': best_ob['top'],
                'ob_bottom': best_ob['bottom'],
                'distance_atr': round(distance_atr, 2),
                'details': (
                    f"{'Near' if near else 'Far from'} {best_ob['type']} OB "
                    f"[{best_ob['bottom']:.5f}–{best_ob['top']:.5f}], "
                    f"distance={distance_atr:.2f}×ATR"
                ),
            })

        except Exception as e:
            logger.warning(f"Order block detection error: {e}")
            result['details'] = f'OB detection error: {e}'

        return result

    def calculate_signal_quality_score(
        self,
        symbol: str,
        confidence: float,
        regime_result: Dict[str, Any],
        ob_result: Dict[str, Any],
        entry_price: float,
        sl_price: float,
        tp_levels: List[float],
        mtf_result: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Score a signal from 0 to 100 based on four weighted components.

        Scoring breakdown (total 100 pts):
        ┌─────────────────────────────────┬────────┐
        │ Component                       │ Weight │
        ├─────────────────────────────────┼────────┤
        │ 1. Confidence level             │  30 pts│
        │ 2. Multi-timeframe alignment    │  25 pts│
        │ 3. Order block proximity        │  25 pts│
        │ 4. Risk/reward ratio            │  20 pts│
        └─────────────────────────────────┴────────┘

        Only signals scoring ≥ MIN_SIGNAL_QUALITY_SCORE (70) are traded.
        """
        score = 0

        # --- Component 1: Confidence level (0–30 pts) ---
        # Scale: 65% → 0 pts, 75% → 15 pts, 85%+ → 30 pts
        pair_threshold = self.get_pair_confidence_threshold(symbol)
        conf_range = 85.0 - pair_threshold  # full range above threshold
        conf_excess = max(0.0, confidence - pair_threshold)
        conf_score = int(min(30, round((conf_excess / conf_range) * 30))) if conf_range > 0 else 0
        score += conf_score

        # --- Component 2: Multi-timeframe alignment (0–25 pts) ---
        mtf_score = 0
        if mtf_result:
            confluence = mtf_result.get('confluence_score', 0)
            # confluence_score is 0–3 (H4, H1, M15)
            mtf_score = int(min(25, round((confluence / 3) * 25)))
        else:
            # No MTF data — award partial credit (regime confidence as proxy)
            regime_conf = regime_result.get('confidence', 0.5)
            mtf_score = int(round(regime_conf * 12))  # max 12 pts without MTF
        score += mtf_score

        # --- Component 3: Order block proximity (0–25 pts) ---
        ob_score = 0
        if ob_result.get('near_order_block'):
            # Full 25 pts if price is right at the OB
            distance_atr = ob_result.get('distance_atr', float('inf'))
            if distance_atr <= 0.2:
                ob_score = 25
            elif distance_atr <= ORDER_BLOCK_PROXIMITY_ATR_MULT:
                # Linear decay from 25 → 10 as distance grows to threshold
                ratio = 1.0 - (distance_atr / ORDER_BLOCK_PROXIMITY_ATR_MULT)
                ob_score = int(10 + round(ratio * 15))
        score += ob_score

        # --- Component 4: Risk/reward ratio (0–20 pts) ---
        rr_score = 0
        if tp_levels and sl_price and entry_price:
            sl_dist = abs(entry_price - sl_price)
            if sl_dist > 0 and len(tp_levels) >= 1:
                tp1_dist = abs(tp_levels[0] - entry_price)
                rr = tp1_dist / sl_dist
                # RR 1.0 → 5 pts, 1.5 → 10 pts, 2.0 → 15 pts, 2.5+ → 20 pts
                if rr >= 2.5:
                    rr_score = 20
                elif rr >= 2.0:
                    rr_score = 15
                elif rr >= 1.5:
                    rr_score = 10
                elif rr >= 1.0:
                    rr_score = 5
        score += rr_score

        final_score = min(100, max(0, score))
        logger.debug(
            f"[Phase2] {symbol} quality score={final_score} "
            f"(conf={conf_score}, mtf={mtf_score}, ob={ob_score}, rr={rr_score})"
        )
        return final_score

    def apply_pair_specific_filter(
        self,
        symbol: str,
        signal_type: str,
        confidence: float,
        quality_score: int,
        ob_result: Dict[str, Any],
        regime_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Apply pair-category-specific filtering rules (Phase 2).

        Rules per category:
        - GOLD: quality_score ≥ 75, OB proximity required, no HIGH_VOL entries
        - INSTITUTIONAL: quality_score ≥ 72, conservative (no CHAOS/HIGH_VOL)
        - JPY_CROSS: quality_score ≥ 70, volatility-adjusted (skip LOW_VOL)
        - STANDARD_FOREX: quality_score ≥ MIN_SIGNAL_QUALITY_SCORE (70)

        Returns dict with:
            approved (bool): whether the signal passes pair-specific rules
            reason (str): explanation if rejected
            category (str): pair category
        """
        category = self._get_pair_category(symbol)
        regime_name = regime_result.get('regime_name', 'UNKNOWN')

        # ---- GOLD pairs: strictest rules ----
        if category == 'GOLD':
            if quality_score < 75:
                return {
                    'approved': False,
                    'reason': (
                        f"Gold pair {symbol} requires quality score ≥ 75 "
                        f"(got {quality_score})"
                    ),
                    'category': category,
                }
            if not ob_result.get('near_order_block'):
                return {
                    'approved': False,
                    'reason': (
                        f"Gold pair {symbol} requires price near an order block "
                        f"({ob_result.get('details', 'no OB')})"
                    ),
                    'category': category,
                }
            if regime_name in ('HIGH_VOL', 'CHAOS'):
                return {
                    'approved': False,
                    'reason': (
                        f"Gold pair {symbol} blocks entries in {regime_name} regime "
                        f"(premium filtering)"
                    ),
                    'category': category,
                }

        # ---- INSTITUTIONAL pairs: conservative rules ----
        elif category == 'INSTITUTIONAL':
            if quality_score < 72:
                return {
                    'approved': False,
                    'reason': (
                        f"Institutional pair {symbol} requires quality score ≥ 72 "
                        f"(got {quality_score})"
                    ),
                    'category': category,
                }
            if regime_name in ('CHAOS', 'HIGH_VOL'):
                return {
                    'approved': False,
                    'reason': (
                        f"Institutional pair {symbol} blocks entries in "
                        f"{regime_name} regime (conservative rules)"
                    ),
                    'category': category,
                }

        # ---- JPY crosses: volatility-adjusted rules ----
        elif category == 'JPY_CROSS':
            if quality_score < MIN_SIGNAL_QUALITY_SCORE:
                return {
                    'approved': False,
                    'reason': (
                        f"JPY cross {symbol} requires quality score ≥ "
                        f"{MIN_SIGNAL_QUALITY_SCORE} (got {quality_score})"
                    ),
                    'category': category,
                }
            if regime_name == 'LOW_VOL':
                return {
                    'approved': False,
                    'reason': (
                        f"JPY cross {symbol} skips LOW_VOL regime "
                        f"(insufficient momentum for JPY pairs)"
                    ),
                    'category': category,
                }

        # ---- Standard forex: balanced approach ----
        else:
            if quality_score < MIN_SIGNAL_QUALITY_SCORE:
                return {
                    'approved': False,
                    'reason': (
                        f"{symbol} requires quality score ≥ "
                        f"{MIN_SIGNAL_QUALITY_SCORE} (got {quality_score})"
                    ),
                    'category': category,
                }

        return {
            'approved': True,
            'reason': f'{category} pair-specific filter passed (score={quality_score})',
            'category': category,
        }

"""
ML Regime Detection Engine
Uses Gradient Boosting + HMM for market regime classification
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import cross_val_score
import joblib
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime
import logging
import os

logger = logging.getLogger(__name__)

# Regime definitions
class MarketRegime:
    TREND_UP = 0
    TREND_DOWN = 1
    RANGE = 2
    HIGH_VOLATILITY = 3
    LOW_VOLATILITY = 4
    CHAOS = 5  # News/event driven
    
    NAMES = {
        0: "TREND_UP",
        1: "TREND_DOWN",
        2: "RANGE",
        3: "HIGH_VOL",
        4: "LOW_VOL",
        5: "CHAOS"
    }
    
    @classmethod
    def get_name(cls, regime_id: int) -> str:
        return cls.NAMES.get(regime_id, "UNKNOWN")


class RegimeDetector:
    """
    Hybrid ML regime detection using Gradient Boosting + HMM smoothing.
    
    Outputs:
    - regime: Current market regime classification
    - confidence: Prediction confidence (0-1)
    - strategy_gate: Which strategies should be active
    - risk_multiplier: Risk adjustment factor (0.0 - 1.2)
    """
    
    def __init__(self, model_path: str = "models/"):
        self.model_path = model_path
        self.scaler = StandardScaler()
        self.classifier = None
        self.regime_history: List[int] = []
        self.hysteresis_threshold = 3  # Minimum consecutive predictions to change regime
        self.current_regime = MarketRegime.RANGE
        self.model_version = "1.0.0"
        
        # Strategy gates per regime
        self.strategy_gates = {
            MarketRegime.TREND_UP: ['breakout', 'pullback'],
            MarketRegime.TREND_DOWN: ['breakout', 'pullback'],
            MarketRegime.RANGE: ['reversal', 'mean_reversion'],
            MarketRegime.HIGH_VOLATILITY: ['breakout'],
            MarketRegime.LOW_VOLATILITY: ['mean_reversion'],
            MarketRegime.CHAOS: []  # No trading in chaos
        }
        
        # Risk multipliers per regime
        self.risk_multipliers = {
            MarketRegime.TREND_UP: 1.0,
            MarketRegime.TREND_DOWN: 1.0,
            MarketRegime.RANGE: 0.8,
            MarketRegime.HIGH_VOLATILITY: 0.6,
            MarketRegime.LOW_VOLATILITY: 1.2,
            MarketRegime.CHAOS: 0.0
        }
        
        # Initialize or load model
        self._initialize_model()
    
    def _initialize_model(self):
        """Initialize or load pre-trained model"""
        model_file = os.path.join(self.model_path, "regime_classifier.joblib")
        
        if os.path.exists(model_file):
            try:
                self.classifier = joblib.load(model_file)
                logger.info(f"Loaded regime classifier from {model_file}")
            except Exception as e:
                logger.warning(f"Failed to load model: {e}. Creating new model.")
                self._create_default_model()
        else:
            self._create_default_model()
    
    def _create_default_model(self):
        """Create default classifier with rule-based initialization"""
        self.classifier = GradientBoostingClassifier(
            n_estimators=100,
            max_depth=5,
            learning_rate=0.1,
            random_state=42
        )
        logger.info("Created new Gradient Boosting classifier")
    
    def detect_regime(self, features: Dict[str, Any]) -> Dict[str, Any]:
        """
        Detect market regime from extracted features.
        
        Uses hybrid approach:
        1. Rule-based initial classification
        2. ML refinement (when trained)
        3. HMM-style smoothing via hysteresis
        
        Returns:
            Dictionary with regime, confidence, strategy_gate, risk_multiplier
        """
        try:
            # Rule-based regime detection
            regime, confidence = self._rule_based_detection(features)
            
            # Apply hysteresis for stability
            stable_regime = self._apply_hysteresis(regime)
            
            # Get strategy gates and risk multiplier
            active_strategies = self.strategy_gates.get(stable_regime, [])
            risk_multiplier = self.risk_multipliers.get(stable_regime, 0.5)
            
            result = {
                'regime': stable_regime,
                'regime_name': MarketRegime.get_name(stable_regime),
                'raw_regime': regime,
                'confidence': confidence,
                'active_strategies': active_strategies,
                'risk_multiplier': min(risk_multiplier, 1.2),  # Cap at 1.2
                'should_trade': len(active_strategies) > 0 and confidence >= 0.6,
                'model_version': self.model_version
            }
            
            logger.info(f"Regime detected: {result['regime_name']} (conf: {confidence:.2f})")
            return result
            
        except Exception as e:
            logger.error(f"Regime detection error: {e}")
            return {
                'regime': MarketRegime.RANGE,
                'regime_name': 'RANGE',
                'confidence': 0.5,
                'active_strategies': ['reversal'],
                'risk_multiplier': 0.5,
                'should_trade': True,
                'model_version': self.model_version
            }
    
    def _rule_based_detection(self, features: Dict[str, Any]) -> Tuple[int, float]:
        """
        Rule-based regime detection using technical indicators.
        
        Returns:
            Tuple of (regime_id, confidence)
        """
        confidence = 0.7  # Base confidence
        
        # Extract key features
        adx = features.get('adx', 25)
        atr_ratio = features.get('atr_ratio_20', 1.0)
        rsi = features.get('rsi', 50)
        bb_position = features.get('bb_position', 0.5)
        structure_bias = features.get('structure_bias', 0)
        ma20_slope = features.get('ma20_slope', 0)
        vol_clustering = features.get('vol_clustering', 0)
        zscore = features.get('zscore_20', 0)
        
        # HIGH VOLATILITY CHECK
        if atr_ratio > 1.5 and vol_clustering > 0.3:
            return MarketRegime.HIGH_VOLATILITY, min(0.6 + atr_ratio * 0.1, 0.9)
        
        # LOW VOLATILITY CHECK
        if atr_ratio < 0.6 and adx < 20:
            return MarketRegime.LOW_VOLATILITY, min(0.7 + (1 - atr_ratio) * 0.2, 0.9)
        
        # CHAOS CHECK (extreme conditions)
        if abs(zscore) > 3 or (atr_ratio > 2.0):
            return MarketRegime.CHAOS, 0.6
        
        # TREND DETECTION
        if adx > 25:
            trend_confidence = min(0.6 + adx * 0.01, 0.95)
            
            # Determine trend direction
            if structure_bias > 3 and ma20_slope > 0.1:
                return MarketRegime.TREND_UP, trend_confidence
            elif structure_bias < -3 and ma20_slope < -0.1:
                return MarketRegime.TREND_DOWN, trend_confidence
            elif ma20_slope > 0:
                return MarketRegime.TREND_UP, trend_confidence * 0.8
            else:
                return MarketRegime.TREND_DOWN, trend_confidence * 0.8
        
        # RANGE DETECTION (default)
        range_confidence = 0.7
        if adx < 20 and abs(zscore) < 1.5:
            range_confidence = 0.85
        
        return MarketRegime.RANGE, range_confidence
    
    def _apply_hysteresis(self, new_regime: int) -> int:
        """
        Apply hysteresis to prevent rapid regime flipping.
        
        Requires N consecutive same predictions before changing regime.
        """
        self.regime_history.append(new_regime)
        
        # Keep only recent history
        if len(self.regime_history) > 10:
            self.regime_history = self.regime_history[-10:]
        
        # Check for consistent predictions
        if len(self.regime_history) >= self.hysteresis_threshold:
            recent = self.regime_history[-self.hysteresis_threshold:]
            if all(r == new_regime for r in recent):
                self.current_regime = new_regime
        
        return self.current_regime
    
    def train(self, X: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
        """
        Train the classifier on labeled data.
        
        Args:
            X: Feature matrix (n_samples, n_features)
            y: Regime labels
            
        Returns:
            Training metrics
        """
        try:
            # Scale features
            X_scaled = self.scaler.fit_transform(X)
            
            # Train classifier
            self.classifier.fit(X_scaled, y)
            
            # Cross-validation
            cv_scores = cross_val_score(self.classifier, X_scaled, y, cv=5)
            
            # Save model
            os.makedirs(self.model_path, exist_ok=True)
            joblib.dump(self.classifier, os.path.join(self.model_path, "regime_classifier.joblib"))
            joblib.dump(self.scaler, os.path.join(self.model_path, "scaler.joblib"))
            
            metrics = {
                'cv_mean': float(cv_scores.mean()),
                'cv_std': float(cv_scores.std()),
                'n_samples': len(y),
                'model_version': self.model_version
            }
            
            logger.info(f"Model trained: CV accuracy = {metrics['cv_mean']:.3f} (+/- {metrics['cv_std']:.3f})")
            return metrics
            
        except Exception as e:
            logger.error(f"Training error: {e}")
            return {'error': str(e)}
    
    def get_regime_stats(self) -> Dict[str, Any]:
        """Get statistics about regime history"""
        if not self.regime_history:
            return {'history_length': 0}
        
        from collections import Counter
        regime_counts = Counter(self.regime_history)
        
        return {
            'current_regime': MarketRegime.get_name(self.current_regime),
            'history_length': len(self.regime_history),
            'regime_distribution': {
                MarketRegime.get_name(k): v for k, v in regime_counts.items()
            }
        }

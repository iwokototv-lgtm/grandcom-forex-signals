"""
ML Model Training & Optimization Module

Uses historical data and backtest results to train and optimize the regime detection model.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.model_selection import cross_val_score, GridSearchCV
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import classification_report, confusion_matrix
import joblib
import logging
import os
from typing import Dict, Any, List, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class MLModelTrainer:
    """
    Trains and optimizes ML models using historical market data and backtest results.
    """
    
    def __init__(self, model_path: str = "models/"):
        self.model_path = model_path
        self.scaler = StandardScaler()
        os.makedirs(model_path, exist_ok=True)
    
    def prepare_training_data(self, signals_history: List[Dict], market_data: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        """
        Prepare training data from historical signals and market data.
        
        Label strategy:
        - Signals that hit TP1+ = GOOD for that regime
        - Signals that hit SL = BAD for that regime
        """
        X = []
        y = []
        
        for signal in signals_history:
            if signal.get('result') not in ['WIN', 'LOSS']:
                continue
                
            # Extract features from signal context
            features = self._extract_features_from_signal(signal, market_data)
            if features is not None:
                X.append(features)
                
                # Label based on result
                # If WIN in certain regime -> that regime was correct
                # If LOSS -> regime might have been wrong
                result = 1 if signal.get('result') == 'WIN' else 0
                regime = signal.get('regime', 'RANGE')
                y.append((regime, result))
        
        return np.array(X), np.array(y)
    
    def _extract_features_from_signal(self, signal: Dict, market_data: pd.DataFrame) -> np.ndarray:
        """Extract features from signal context"""
        try:
            # Get price data around signal time
            pair = signal.get('pair')
            timestamp = signal.get('created_at')
            
            # Use available features from signal
            features = [
                signal.get('confidence', 50) / 100,
                signal.get('confluence_score', 0.5),
                1 if signal.get('type') == 'BUY' else 0,
                signal.get('entry_price', 0),
                signal.get('pips', 0) if signal.get('pips') else 0,
            ]
            
            return np.array(features)
        except Exception as e:
            logger.error(f"Feature extraction error: {e}")
            return None
    
    def optimize_hyperparameters(self, X: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
        """
        Optimize model hyperparameters using GridSearchCV.
        """
        logger.info("Starting hyperparameter optimization...")
        
        param_grid = {
            'n_estimators': [50, 100, 150],
            'max_depth': [3, 5, 7],
            'learning_rate': [0.05, 0.1, 0.2],
            'min_samples_split': [2, 5, 10]
        }
        
        base_model = GradientBoostingClassifier(random_state=42)
        
        grid_search = GridSearchCV(
            base_model,
            param_grid,
            cv=5,
            scoring='accuracy',
            n_jobs=-1,
            verbose=1
        )
        
        X_scaled = self.scaler.fit_transform(X)
        grid_search.fit(X_scaled, y)
        
        results = {
            'best_params': grid_search.best_params_,
            'best_score': grid_search.best_score_,
            'cv_results': {
                'mean_scores': grid_search.cv_results_['mean_test_score'].tolist(),
                'std_scores': grid_search.cv_results_['std_test_score'].tolist()
            }
        }
        
        # Save optimized model
        joblib.dump(grid_search.best_estimator_, os.path.join(self.model_path, "optimized_classifier.joblib"))
        joblib.dump(self.scaler, os.path.join(self.model_path, "optimized_scaler.joblib"))
        
        logger.info(f"Best parameters: {results['best_params']}")
        logger.info(f"Best CV score: {results['best_score']:.4f}")
        
        return results
    
    def train_regime_classifier(self, X: np.ndarray, y: np.ndarray, optimize: bool = True) -> Dict[str, Any]:
        """
        Train regime classification model.
        """
        logger.info(f"Training regime classifier on {len(X)} samples...")
        
        X_scaled = self.scaler.fit_transform(X)
        
        if optimize:
            # Use optimized model
            classifier = GradientBoostingClassifier(
                n_estimators=100,
                max_depth=5,
                learning_rate=0.1,
                min_samples_split=5,
                random_state=42
            )
        else:
            classifier = GradientBoostingClassifier(random_state=42)
        
        # Train with cross-validation
        cv_scores = cross_val_score(classifier, X_scaled, y, cv=5)
        
        # Fit final model
        classifier.fit(X_scaled, y)
        
        # Save models
        joblib.dump(classifier, os.path.join(self.model_path, "regime_classifier.joblib"))
        joblib.dump(self.scaler, os.path.join(self.model_path, "scaler.joblib"))
        
        # Get feature importances
        importances = classifier.feature_importances_.tolist()
        
        results = {
            'cv_mean': float(cv_scores.mean()),
            'cv_std': float(cv_scores.std()),
            'n_samples': len(X),
            'feature_importances': importances,
            'model_saved': True,
            'timestamp': datetime.utcnow().isoformat()
        }
        
        logger.info(f"Model trained: CV accuracy = {results['cv_mean']:.4f} (+/- {results['cv_std']:.4f})")
        
        return results


class SignalOptimizationEngine:
    """
    Optimizes signal parameters based on historical performance.
    """
    
    def __init__(self):
        self.optimization_results = {}
    
    def analyze_performance_by_pair(self, signals: List[Dict]) -> Dict[str, Dict]:
        """
        Analyze win rate and performance metrics by trading pair.
        """
        pair_stats = {}
        
        for signal in signals:
            pair = signal.get('pair')
            if not pair:
                continue
                
            if pair not in pair_stats:
                pair_stats[pair] = {
                    'total': 0,
                    'wins': 0,
                    'losses': 0,
                    'total_pips': 0,
                    'win_pips': 0,
                    'loss_pips': 0
                }
            
            pair_stats[pair]['total'] += 1
            
            result = signal.get('result')
            pips = signal.get('pips', 0) or 0
            
            if result == 'WIN':
                pair_stats[pair]['wins'] += 1
                pair_stats[pair]['win_pips'] += pips
            elif result == 'LOSS':
                pair_stats[pair]['losses'] += 1
                pair_stats[pair]['loss_pips'] += pips
            
            pair_stats[pair]['total_pips'] += pips
        
        # Calculate derived metrics
        for pair, stats in pair_stats.items():
            if stats['total'] > 0:
                stats['win_rate'] = stats['wins'] / stats['total'] * 100
                stats['avg_pips'] = stats['total_pips'] / stats['total']
                
                if stats['losses'] > 0 and stats['wins'] > 0:
                    avg_win = stats['win_pips'] / stats['wins']
                    avg_loss = abs(stats['loss_pips'] / stats['losses'])
                    stats['profit_factor'] = (stats['wins'] * avg_win) / (stats['losses'] * avg_loss) if avg_loss > 0 else 0
                else:
                    stats['profit_factor'] = 0
        
        return pair_stats
    
    def analyze_performance_by_regime(self, signals: List[Dict]) -> Dict[str, Dict]:
        """
        Analyze performance by market regime.
        """
        regime_stats = {}
        
        for signal in signals:
            regime = signal.get('regime', 'UNKNOWN')
            
            if regime not in regime_stats:
                regime_stats[regime] = {
                    'total': 0,
                    'wins': 0,
                    'losses': 0,
                    'total_pips': 0
                }
            
            regime_stats[regime]['total'] += 1
            
            result = signal.get('result')
            pips = signal.get('pips', 0) or 0
            
            if result == 'WIN':
                regime_stats[regime]['wins'] += 1
            elif result == 'LOSS':
                regime_stats[regime]['losses'] += 1
            
            regime_stats[regime]['total_pips'] += pips
        
        # Calculate derived metrics
        for regime, stats in regime_stats.items():
            if stats['total'] > 0:
                stats['win_rate'] = stats['wins'] / stats['total'] * 100
                stats['avg_pips'] = stats['total_pips'] / stats['total']
        
        return regime_stats
    
    def recommend_pair_settings(self, pair_stats: Dict[str, Dict]) -> Dict[str, Dict]:
        """
        Recommend optimal TP/SL settings based on performance analysis.
        """
        recommendations = {}
        
        for pair, stats in pair_stats.items():
            win_rate = stats.get('win_rate', 50)
            profit_factor = stats.get('profit_factor', 1)
            avg_pips = stats.get('avg_pips', 0)
            
            # Base recommendation on performance
            if win_rate >= 55 and profit_factor >= 1.2:
                # High performance - can use larger TPs
                tp_multiplier = 1.2
                sl_multiplier = 1.0
                note = "High performer - extended TP recommended"
            elif win_rate >= 50 and profit_factor >= 1.0:
                # Average performance - standard settings
                tp_multiplier = 1.0
                sl_multiplier = 1.0
                note = "Standard performer - keep current settings"
            elif win_rate >= 45:
                # Below average - tighter TPs
                tp_multiplier = 0.8
                sl_multiplier = 0.9
                note = "Below average - consider tighter TPs"
            else:
                # Poor performance - conservative approach
                tp_multiplier = 0.6
                sl_multiplier = 0.8
                note = "Low performer - conservative settings recommended"
            
            recommendations[pair] = {
                'tp_multiplier': tp_multiplier,
                'sl_multiplier': sl_multiplier,
                'current_win_rate': win_rate,
                'current_profit_factor': profit_factor,
                'recommendation': note
            }
        
        return recommendations


async def run_model_optimization(db) -> Dict[str, Any]:
    """
    Main function to run model optimization using historical signals.
    """
    logger.info("Starting ML model optimization...")
    
    try:
        # Fetch historical signals with results
        signals_cursor = db.signals.find({
            'result': {'$in': ['WIN', 'LOSS']}
        }).sort('created_at', -1).limit(1000)
        
        signals = []
        async for signal in signals_cursor:
            signal['id'] = str(signal.pop('_id'))
            signals.append(signal)
        
        if len(signals) < 50:
            return {
                'success': False,
                'error': 'Not enough historical data for optimization. Need at least 50 completed signals.',
                'signals_found': len(signals)
            }
        
        # Run performance analysis
        optimizer = SignalOptimizationEngine()
        
        pair_analysis = optimizer.analyze_performance_by_pair(signals)
        regime_analysis = optimizer.analyze_performance_by_regime(signals)
        recommendations = optimizer.recommend_pair_settings(pair_analysis)
        
        # Summary statistics
        total_signals = len(signals)
        total_wins = sum(1 for s in signals if s.get('result') == 'WIN')
        total_pips = sum(s.get('pips', 0) or 0 for s in signals)
        
        results = {
            'success': True,
            'timestamp': datetime.utcnow().isoformat(),
            'summary': {
                'total_signals_analyzed': total_signals,
                'overall_win_rate': total_wins / total_signals * 100 if total_signals > 0 else 0,
                'total_pips': total_pips,
                'avg_pips_per_trade': total_pips / total_signals if total_signals > 0 else 0
            },
            'pair_analysis': pair_analysis,
            'regime_analysis': regime_analysis,
            'recommendations': recommendations
        }
        
        logger.info(f"Optimization complete. Analyzed {total_signals} signals.")
        return results
        
    except Exception as e:
        logger.error(f"Model optimization error: {e}")
        return {
            'success': False,
            'error': str(e)
        }

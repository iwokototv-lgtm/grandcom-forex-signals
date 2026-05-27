"""
Strategy Router & Signal Aggregator
Routes signals from multiple strategies through portfolio manager
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class StrategyType(Enum):
    """Available strategy types"""
    TREND_FOLLOWING = "trend_following"
    MEAN_REVERSION = "mean_reversion"
    BREAKOUT = "breakout"
    MOMENTUM = "momentum"
    ARBITRAGE = "arbitrage"


class SignalAggregator:
    """
    Aggregates signals from multiple strategies and applies portfolio-level filters.
    """
    
    def __init__(self, portfolio_manager):
        """
        Initialize signal aggregator.
        
        Args:
            portfolio_manager: PortfolioManager instance
        """
        self.portfolio_manager = portfolio_manager
        self.signal_history: List[Dict[str, Any]] = []
        self.rejected_signals: List[Dict[str, Any]] = []
        
        logger.info("Signal Aggregator initialized")
    
    def process_signal(
        self,
        strategy_name: str,
        pair: str,
        signal_type: str,  # BUY, SELL, NEUTRAL
        confidence: float,
        entry_price: float,
        stop_loss: float,
        take_profits: List[float],
        analysis: str,
        risk_reward: float,
    ) -> Dict[str, Any]:
        """
        Process a signal through portfolio filters.
        
        Args:
            strategy_name: Name of strategy generating signal
            pair: Trading pair
            signal_type: BUY, SELL, or NEUTRAL
            confidence: Confidence level (0-100)
            entry_price: Entry price
            stop_loss: Stop loss price
            take_profits: List of take profit levels
            analysis: Analysis text
            risk_reward: Risk/reward ratio
            
        Returns:
            Dictionary with signal status and decision
        """
        try:
            # 1. Check if strategy is allowed to trade
            strategy_check = self.portfolio_manager.check_strategy_allowed(strategy_name)
            
            if not strategy_check['allowed']:
                rejection = {
                    'strategy': strategy_name,
                    'pair': pair,
                    'signal_type': signal_type,
                    'confidence': confidence,
                    'reason': 'Strategy not allowed to trade',
                    'restrictions': strategy_check['restrictions'],
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
                self.rejected_signals.append(rejection)
                logger.warning(
                    f"[{strategy_name}] Signal rejected: {rejection['reason']} | "
                    f"Restrictions: {rejection['restrictions']}"
                )
                return {
                    'approved': False,
                    'reason': 'Strategy not allowed to trade',
                    'restrictions': strategy_check['restrictions']
                }
            
            # 2. Filter NEUTRAL signals
            if signal_type == "NEUTRAL":
                logger.info(f"[{strategy_name}] NEUTRAL signal — no trade")
                return {'approved': False, 'reason': 'NEUTRAL signal'}
            
            # 3. Check confidence threshold
            min_confidence = 60
            if confidence < min_confidence:
                rejection = {
                    'strategy': strategy_name,
                    'pair': pair,
                    'signal_type': signal_type,
                    'confidence': confidence,
                    'reason': f'Confidence {confidence}% < {min_confidence}%',
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
                self.rejected_signals.append(rejection)
                logger.info(f"[{strategy_name}] Signal rejected: Low confidence {confidence}%")
                return {'approved': False, 'reason': 'Low confidence'}
            
            # 4. Check risk/reward ratio
            min_rr = 1.0
            if risk_reward < min_rr:
                rejection = {
                    'strategy': strategy_name,
                    'pair': pair,
                    'signal_type': signal_type,
                    'risk_reward': risk_reward,
                    'reason': f'R:R {risk_reward} < {min_rr}',
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
                self.rejected_signals.append(rejection)
                logger.info(f"[{strategy_name}] Signal rejected: Low R:R {risk_reward}")
                return {'approved': False, 'reason': 'Low risk/reward ratio'}
            
            # 5. Check correlation with other active strategies
            correlation_check = self._check_strategy_correlation(strategy_name)
            if not correlation_check['approved']:
                rejection = {
                    'strategy': strategy_name,
                    'pair': pair,
                    'signal_type': signal_type,
                    'reason': 'High correlation with active strategies',
                    'correlated_with': correlation_check['correlated_with'],
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
                self.rejected_signals.append(rejection)
                logger.warning(
                    f"[{strategy_name}] Signal rejected: High correlation with "
                    f"{correlation_check['correlated_with']}"
                )
                return {
                    'approved': False,
                    'reason': 'High correlation with active strategies',
                    'correlated_with': correlation_check['correlated_with']
                }
            
            # 6. APPROVED - Create signal record
            signal = {
                'strategy': strategy_name,
                'pair': pair,
                'type': signal_type,
                'confidence': confidence,
                'entry_price': entry_price,
                'stop_loss': stop_loss,
                'take_profits': take_profits,
                'analysis': analysis,
                'risk_reward': risk_reward,
                'status': 'APPROVED',
                'strategy_capital': strategy_check['strategy_capital'],
                'portfolio_drawdown_pct': strategy_check['portfolio_drawdown_pct'],
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            
            self.signal_history.append(signal)
            
            logger.info(
                f"[{strategy_name}] ✅ Signal APPROVED | {pair} {signal_type} @ "
                f"{entry_price} | Conf: {confidence}% | R:R 1:{risk_reward}"
            )
            
            return {
                'approved': True,
                'signal': signal,
                'strategy_capital': strategy_check['strategy_capital'],
                'portfolio_drawdown_pct': strategy_check['portfolio_drawdown_pct']
            }
            
        except Exception as e:
            logger.error(f"Error processing signal: {e}")
            return {'approved': False, 'error': str(e)}
    
    def _check_strategy_correlation(self, strategy_name: str) -> Dict[str, Any]:
        """
        Check if strategy is too correlated with other active strategies.
        
        Returns:
            Dictionary with correlation check result
        """
        # Placeholder for correlation check
        # In production, calculate based on strategy returns
        
        correlation_threshold = 0.7
        correlated_strategies = []
        
        # Get correlation matrix
        correlation_matrix = self.portfolio_manager.get_correlation_matrix()
        
        if strategy_name in correlation_matrix:
            for other_strategy, corr in correlation_matrix[strategy_name].items():
                if other_strategy != strategy_name and corr > correlation_threshold:
                    correlated_strategies.append(other_strategy)
        
        return {
            'approved': len(correlated_strategies) == 0,
            'correlated_with': correlated_strategies
        }
    
    def get_signal_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent signal history."""
        return self.signal_history[-limit:]
    
    def get_rejected_signals(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recently rejected signals."""
        return self.rejected_signals[-limit:]
    
    def get_signal_statistics(self) -> Dict[str, Any]:
        """Get signal processing statistics."""
        total_signals = len(self.signal_history) + len(self.rejected_signals)
        approved = len(self.signal_history)
        rejected = len(self.rejected_signals)
        
        approval_rate = (approved / total_signals * 100) if total_signals > 0 else 0
        
        return {
            'total_signals': total_signals,
            'approved': approved,
            'rejected': rejected,
            'approval_rate': round(approval_rate, 2),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }


class StrategyRouter:
    """
    Routes signals to appropriate strategies and manages signal flow.
    """
    
    def __init__(self, portfolio_manager, signal_aggregator):
        """
        Initialize strategy router.
        
        Args:
            portfolio_manager: PortfolioManager instance
            signal_aggregator: SignalAggregator instance
        """
        self.portfolio_manager = portfolio_manager
        self.signal_aggregator = signal_aggregator
        self.strategy_handlers: Dict[str, callable] = {}
        
        logger.info("Strategy Router initialized")
    
    def register_strategy_handler(self, strategy_name: str, handler: callable):
        """
        Register a handler function for a strategy.
        
        Args:
            strategy_name: Name of strategy
            handler: Callable that processes strategy signals
        """
        self.strategy_handlers[strategy_name] = handler
        logger.info(f"Strategy handler registered: {strategy_name}")
    
    def route_signal(
        self,
        strategy_name: str,
        signal_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Route a signal through the system.
        
        Args:
            strategy_name: Name of strategy
            signal_data: Signal data dictionary
            
        Returns:
            Routing result
        """
        try:
            # Process through aggregator
            result = self.signal_aggregator.process_signal(
                strategy_name=strategy_name,
                pair=signal_data.get('pair'),
                signal_type=signal_data.get('type'),
                confidence=signal_data.get('confidence'),
                entry_price=signal_data.get('entry_price'),
                stop_loss=signal_data.get('stop_loss'),
                take_profits=signal_data.get('take_profits', []),
                analysis=signal_data.get('analysis', ''),
                risk_reward=signal_data.get('risk_reward', 1.0),
            )
            
            if not result.get('approved'):
                return result
            
            # Call strategy handler if registered
            if strategy_name in self.strategy_handlers:
                handler_result = self.strategy_handlers[strategy_name](result['signal'])
                return {**result, 'handler_result': handler_result}
            
            return result
            
        except Exception as e:
            logger.error(f"Error routing signal: {e}")
            return {'approved': False, 'error': str(e)}
    
    def get_routing_statistics(self) -> Dict[str, Any]:
        """Get routing statistics."""
        return {
            'signal_stats': self.signal_aggregator.get_signal_statistics(),
            'portfolio_metrics': self.portfolio_manager.get_portfolio_metrics(),
            'strategies': self.portfolio_manager.get_all_strategies_metrics(),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }


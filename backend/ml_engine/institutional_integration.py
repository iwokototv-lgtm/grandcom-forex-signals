"""
Institutional Integration Layer
Bridges current gold trading system with multi-strategy portfolio system
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from ml_engine.portfolio_manager import PortfolioManager, Strategy
from ml_engine.strategy_router import StrategyRouter, SignalAggregator
from ml_engine.position_calculator import PositionCalculator

logger = logging.getLogger(__name__)


class InstitutionalTradingSystem:
    """
    Unified institutional trading system combining:
    - Current gold trading (XAUUSD/XAUEUR)
    - Multi-strategy portfolio management
    - Position sizing & risk management
    - Signal routing & aggregation
    """
    
    def __init__(self, total_capital: float = 1000.0):
        """
        Initialize institutional trading system.
        
        Args:
            total_capital: Total portfolio capital
        """
        self.total_capital = total_capital
        
        # Initialize components
        self.portfolio_manager = PortfolioManager(total_capital=total_capital)
        self.signal_aggregator = SignalAggregator(self.portfolio_manager)
        self.strategy_router = StrategyRouter(
            self.portfolio_manager,
            self.signal_aggregator
        )
        
        # Position calculators per strategy
        self.position_calculators: Dict[str, PositionCalculator] = {}
        
        # Initialize default gold strategy
        self._initialize_gold_strategy()
        
        logger.info(
            f"Institutional Trading System initialized | "
            f"Capital: ${total_capital:.2f}"
        )
    
    def _initialize_gold_strategy(self):
        """Initialize the current gold trading strategy."""
        gold_strategy = Strategy(
            name="Gold_Trend_Following",
            asset_class="gold",
            allocation=0.40,  # 40% of portfolio
            max_drawdown=0.10,  # 10% max DD
            risk_per_trade=0.05,  # 5% risk per trade
            enabled=True,
            description="XAUUSD & XAUEUR trend following with GPT-4o analysis"
        )
        
        self.portfolio_manager.add_strategy(gold_strategy)
        
        # Create position calculator for gold strategy
        strategy_capital = self.total_capital * gold_strategy.allocation
        risk_amount = strategy_capital * gold_strategy.risk_per_trade
        
        self.position_calculators["Gold_Trend_Following"] = PositionCalculator(
            account_balance=strategy_capital,
            risk_per_trade=gold_strategy.risk_per_trade
        )
        
        logger.info(
            f"Gold strategy initialized | "
            f"Capital: ${strategy_capital:.2f} | "
            f"Risk/Trade: ${risk_amount:.2f}"
        )
    
    def add_strategy(
        self,
        name: str,
        asset_class: str,
        allocation: float,
        max_drawdown: float = 0.10,
        risk_per_trade: float = 0.05,
        description: str = ""
    ) -> bool:
        """
        Add a new strategy to the system.
        
        Args:
            name: Strategy name
            asset_class: Asset class (gold, forex, crypto, commodities)
            allocation: Portfolio allocation (0-1)
            max_drawdown: Max drawdown for strategy
            risk_per_trade: Risk per trade
            description: Strategy description
            
        Returns:
            True if added successfully
        """
        try:
            strategy = Strategy(
                name=name,
                asset_class=asset_class,
                allocation=allocation,
                max_drawdown=max_drawdown,
                risk_per_trade=risk_per_trade,
                enabled=True,
                description=description
            )
            
            if not self.portfolio_manager.add_strategy(strategy):
                return False
            
            # Create position calculator for new strategy
            strategy_capital = self.total_capital * allocation
            self.position_calculators[name] = PositionCalculator(
                account_balance=strategy_capital,
                risk_per_trade=risk_per_trade
            )
            
            logger.info(f"Strategy added to system: {name}")
            return True
            
        except Exception as e:
            logger.error(f"Error adding strategy: {e}")
            return False
    
    def process_gold_signal(
        self,
        pair: str,
        signal_type: str,
        confidence: float,
        entry_price: float,
        stop_loss: float,
        take_profits: list,
        analysis: str,
        risk_reward: float,
    ) -> Dict[str, Any]:
        """
        Process a gold trading signal through the institutional system.
        
        Args:
            pair: XAUUSD or XAUEUR
            signal_type: BUY, SELL, or NEUTRAL
            confidence: Confidence level (0-100)
            entry_price: Entry price
            stop_loss: Stop loss price
            take_profits: List of TP levels
            analysis: Analysis text
            risk_reward: Risk/reward ratio
            
        Returns:
            Signal processing result
        """
        try:
            # Route through strategy router
            signal_data = {
                'pair': pair,
                'type': signal_type,
                'confidence': confidence,
                'entry_price': entry_price,
                'stop_loss': stop_loss,
                'take_profits': take_profits,
                'analysis': analysis,
                'risk_reward': risk_reward,
            }
            
            result = self.strategy_router.route_signal(
                strategy_name="Gold_Trend_Following",
                signal_data=signal_data
            )
            
            if result.get('approved'):
                # Calculate position size
                position_calc = self.position_calculators["Gold_Trend_Following"]
                position_info = position_calc.calculate_position_size(
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    pair=pair
                )
                
                result['position_sizing'] = position_info
                
                logger.info(
                    f"[Gold] Signal approved | {pair} {signal_type} | "
                    f"Lots: {position_info.get('adjusted_lot_size'):.4f}"
                )
            
            return result
            
        except Exception as e:
            logger.error(f"Error processing gold signal: {e}")
            return {'approved': False, 'error': str(e)}
    
    def record_trade(
        self,
        strategy_name: str,
        pair: str,
        signal_type: str,
        entry_price: float,
        exit_price: float,
        position_size: float,
        result: str,
        pnl: float,
    ) -> Dict[str, Any]:
        """
        Record a trade across the system.
        
        Args:
            strategy_name: Strategy name
            pair: Trading pair
            signal_type: BUY or SELL
            entry_price: Entry price
            exit_price: Exit price
            position_size: Position size
            result: WIN or LOSS
            pnl: Profit/loss
            
        Returns:
            Trade record
        """
        try:
            # Record in portfolio manager
            portfolio_trade = self.portfolio_manager.record_trade(
                strategy_name=strategy_name,
                pair=pair,
                signal_type=signal_type,
                entry_price=entry_price,
                exit_price=exit_price,
                position_size=position_size,
                result=result,
                pnl=pnl,
            )
            
            # Record in strategy position calculator
            if strategy_name in self.position_calculators:
                position_calc = self.position_calculators[strategy_name]
                position_calc.record_trade(
                    pair=pair,
                    signal_type=signal_type,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    position_size=position_size,
                    result=result
                )
            
            logger.info(
                f"[{strategy_name}] Trade recorded | {pair} {result} | "
                f"PnL: ${pnl:.2f}"
            )
            
            return portfolio_trade
            
        except Exception as e:
            logger.error(f"Error recording trade: {e}")
            return {'error': str(e)}
    
    def get_system_metrics(self) -> Dict[str, Any]:
        """Get comprehensive system metrics."""
        return {
            'portfolio': self.portfolio_manager.get_portfolio_metrics(),
            'strategies': self.portfolio_manager.get_all_strategies_metrics(),
            'signal_stats': self.signal_aggregator.get_signal_statistics(),
            'routing_stats': self.strategy_router.get_routing_statistics(),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
    
    def get_strategy_capital(self, strategy_name: str) -> float:
        """Get allocated capital for a strategy."""
        return self.portfolio_manager.get_strategy_capital(strategy_name)
    
    def get_portfolio_state(self) -> Dict[str, Any]:
        """Export complete portfolio state."""
        return self.portfolio_manager.export_portfolio_state()
    
    def rebalance_portfolio(self) -> Dict[str, float]:
        """Rebalance portfolio to target allocations."""
        return self.portfolio_manager.rebalance_portfolio()
    
    def reset_daily(self):
        """Reset daily metrics."""
        self.portfolio_manager.reset_daily()
        for calc in self.position_calculators.values():
            calc.reset_daily()
    
    def reset_weekly(self):
        """Reset weekly metrics."""
        self.portfolio_manager.reset_weekly()
        for calc in self.position_calculators.values():
            calc.reset_weekly()
    
    def reset_monthly(self):
        """Reset monthly metrics."""
        self.portfolio_manager.reset_monthly()
        for calc in self.position_calculators.values():
            calc.reset_monthly()


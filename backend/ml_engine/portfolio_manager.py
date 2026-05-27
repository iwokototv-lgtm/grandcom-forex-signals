"""
Institutional Multi-Strategy Hybrid Portfolio Manager
Manages multiple trading strategies with portfolio-level risk controls
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
import json

logger = logging.getLogger(__name__)


@dataclass
class Strategy:
    """Strategy configuration"""
    name: str
    asset_class: str  # gold, forex, crypto, commodities
    allocation: float  # % of portfolio
    max_drawdown: float  # strategy-specific max DD
    risk_per_trade: float  # strategy-specific risk %
    enabled: bool = True
    description: str = ""


@dataclass
class StrategyPerformance:
    """Strategy performance metrics"""
    strategy_name: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    max_drawdown: float
    sharpe_ratio: float
    sortino_ratio: float
    profit_factor: float


class PortfolioManager:
    """
    Institutional-grade portfolio manager supporting:
    - Multiple strategies
    - Multiple asset classes
    - Portfolio-level risk management
    - Correlation analysis
    - Dynamic capital allocation
    - Performance attribution
    """
    
    def __init__(self, total_capital: float = 1000.0):
        """
        Initialize portfolio manager.
        
        Args:
            total_capital: Total portfolio capital in USD
        """
        self.total_capital = total_capital
        self.current_equity = total_capital
        self.peak_equity = total_capital
        
        # Strategy management
        self.strategies: Dict[str, Strategy] = {}
        self.strategy_allocations: Dict[str, float] = {}
        self.strategy_performance: Dict[str, StrategyPerformance] = {}
        
        # Portfolio tracking
        self.portfolio_trades: List[Dict[str, Any]] = []
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        self.monthly_pnl = 0.0
        
        # Risk management
        self.max_portfolio_drawdown = 0.15  # 15% max portfolio DD
        self.max_daily_loss = 0.05  # 5% max daily loss
        self.correlation_threshold = 0.7  # Max correlation between strategies
        
        # Asset class exposure limits
        self.exposure_limits = {
            'gold': 0.40,        # 40% max gold
            'forex': 0.30,       # 30% max forex
            'crypto': 0.15,      # 15% max crypto
            'commodities': 0.25, # 25% max commodities
        }
        
        self.current_exposure = {
            'gold': 0.0,
            'forex': 0.0,
            'crypto': 0.0,
            'commodities': 0.0,
        }
        
        logger.info(f"Portfolio Manager initialized: Capital=${total_capital:.2f}")
    
    def add_strategy(self, strategy: Strategy) -> bool:
        """
        Add a new strategy to the portfolio.
        
        Args:
            strategy: Strategy configuration
            
        Returns:
            True if added successfully
        """
        try:
            # Validate allocation
            total_allocation = sum(s.allocation for s in self.strategies.values())
            if total_allocation + strategy.allocation > 1.0:
                logger.error(
                    f"Strategy allocation {strategy.allocation} exceeds available "
                    f"({1.0 - total_allocation})"
                )
                return False
            
            self.strategies[strategy.name] = strategy
            self.strategy_allocations[strategy.name] = strategy.allocation
            
            # Initialize performance tracking
            self.strategy_performance[strategy.name] = StrategyPerformance(
                strategy_name=strategy.name,
                total_trades=0,
                wins=0,
                losses=0,
                win_rate=0.0,
                total_pnl=0.0,
                max_drawdown=0.0,
                sharpe_ratio=0.0,
                sortino_ratio=0.0,
                profit_factor=0.0,
            )
            
            logger.info(
                f"Strategy added: {strategy.name} | "
                f"Asset: {strategy.asset_class} | "
                f"Allocation: {strategy.allocation*100}%"
            )
            return True
            
        except Exception as e:
            logger.error(f"Error adding strategy: {e}")
            return False
    
    def get_strategy_capital(self, strategy_name: str) -> float:
        """Get allocated capital for a strategy."""
        if strategy_name not in self.strategies:
            return 0.0
        
        allocation = self.strategy_allocations[strategy_name]
        return self.current_equity * allocation
    
    def check_strategy_allowed(self, strategy_name: str) -> Dict[str, Any]:
        """
        Check if strategy is allowed to trade based on risk limits.
        
        Returns:
            Dictionary with trading status and restrictions
        """
        if strategy_name not in self.strategies:
            return {'allowed': False, 'reason': 'Strategy not found'}
        
        strategy = self.strategies[strategy_name]
        restrictions = []
        
        # Check if strategy is enabled
        if not strategy.enabled:
            restrictions.append("Strategy is disabled")
        
        # Check portfolio drawdown
        portfolio_dd = (self.peak_equity - self.current_equity) / self.peak_equity
        if portfolio_dd >= self.max_portfolio_drawdown:
            restrictions.append(
                f"Portfolio drawdown {portfolio_dd*100:.1f}% >= "
                f"limit {self.max_portfolio_drawdown*100}%"
            )
        
        # Check daily loss limit
        if self.daily_pnl <= -self.max_daily_loss * self.current_equity:
            restrictions.append(
                f"Daily loss limit reached ({self.max_daily_loss*100}%)"
            )
        
        # Check asset class exposure
        asset_class = strategy.asset_class
        if self.current_exposure[asset_class] >= self.exposure_limits[asset_class]:
            restrictions.append(
                f"{asset_class} exposure limit reached "
                f"({self.current_exposure[asset_class]*100:.1f}%)"
            )
        
        return {
            'allowed': len(restrictions) == 0,
            'restrictions': restrictions,
            'strategy_capital': self.get_strategy_capital(strategy_name),
            'portfolio_drawdown_pct': round(portfolio_dd * 100, 2),
        }
    
    def record_trade(
        self,
        strategy_name: str,
        pair: str,
        signal_type: str,
        entry_price: float,
        exit_price: float,
        position_size: float,
        result: str,  # WIN or LOSS
        pnl: float,
    ) -> Dict[str, Any]:
        """
        Record a trade across the portfolio.
        
        Args:
            strategy_name: Strategy that generated the signal
            pair: Trading pair
            signal_type: BUY or SELL
            entry_price: Entry price
            exit_price: Exit price
            position_size: Position size
            result: WIN or LOSS
            pnl: Profit/loss in dollars
            
        Returns:
            Trade record
        """
        try:
            # Update portfolio equity
            self.current_equity += pnl
            self.daily_pnl += pnl
            self.weekly_pnl += pnl
            self.monthly_pnl += pnl
            
            # Update peak equity
            if self.current_equity > self.peak_equity:
                self.peak_equity = self.current_equity
            
            # Update strategy performance
            if strategy_name in self.strategy_performance:
                perf = self.strategy_performance[strategy_name]
                perf.total_trades += 1
                perf.total_pnl += pnl
                
                if result == "WIN":
                    perf.wins += 1
                else:
                    perf.losses += 1
                
                perf.win_rate = (perf.wins / perf.total_trades * 100) if perf.total_trades > 0 else 0
            
            # Record trade
            trade = {
                'strategy': strategy_name,
                'pair': pair,
                'type': signal_type,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'position_size': position_size,
                'pnl': round(pnl, 2),
                'result': result,
                'portfolio_equity': round(self.current_equity, 2),
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            
            self.portfolio_trades.append(trade)
            
            logger.info(
                f"[{strategy_name}] Trade recorded: {pair} {result} | "
                f"PnL: ${pnl:.2f} | Portfolio Equity: ${self.current_equity:.2f}"
            )
            
            return trade
            
        except Exception as e:
            logger.error(f"Error recording trade: {e}")
            return {'error': str(e)}
    
    def get_portfolio_metrics(self) -> Dict[str, Any]:
        """Get comprehensive portfolio metrics."""
        drawdown = (self.peak_equity - self.current_equity) / self.peak_equity
        
        # Calculate portfolio-level metrics
        total_trades = len(self.portfolio_trades)
        wins = sum(1 for t in self.portfolio_trades if t.get('result') == 'WIN')
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        # Calculate profit factor
        winning_trades = [t for t in self.portfolio_trades if t.get('result') == 'WIN']
        losing_trades = [t for t in self.portfolio_trades if t.get('result') == 'LOSS']
        
        total_wins = sum(t.get('pnl', 0) for t in winning_trades)
        total_losses = abs(sum(t.get('pnl', 0) for t in losing_trades))
        
        profit_factor = (total_wins / total_losses) if total_losses > 0 else 0
        
        return {
            'total_capital': round(self.total_capital, 2),
            'current_equity': round(self.current_equity, 2),
            'peak_equity': round(self.peak_equity, 2),
            'drawdown_pct': round(drawdown * 100, 2),
            'daily_pnl': round(self.daily_pnl, 2),
            'weekly_pnl': round(self.weekly_pnl, 2),
            'monthly_pnl': round(self.monthly_pnl, 2),
            'total_trades': total_trades,
            'wins': wins,
            'losses': total_trades - wins,
            'win_rate': round(win_rate, 2),
            'profit_factor': round(profit_factor, 2),
            'active_strategies': len([s for s in self.strategies.values() if s.enabled]),
            'total_strategies': len(self.strategies),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
    
    def get_strategy_metrics(self, strategy_name: str) -> Optional[Dict[str, Any]]:
        """Get metrics for a specific strategy."""
        if strategy_name not in self.strategy_performance:
            return None
        
        perf = self.strategy_performance[strategy_name]
        return asdict(perf)
    
    def get_all_strategies_metrics(self) -> List[Dict[str, Any]]:
        """Get metrics for all strategies."""
        return [asdict(perf) for perf in self.strategy_performance.values()]
    
    def rebalance_portfolio(self) -> Dict[str, float]:
        """
        Rebalance portfolio to target allocations.
        
        Returns:
            Dictionary with new allocations
        """
        logger.info("Rebalancing portfolio to target allocations...")
        
        new_allocations = {}
        for strategy_name, strategy in self.strategies.items():
            new_allocations[strategy_name] = strategy.allocation
        
        logger.info(f"Portfolio rebalanced: {new_allocations}")
        return new_allocations
    
    def get_correlation_matrix(self) -> Dict[str, Dict[str, float]]:
        """
        Calculate correlation between strategies.
        
        Returns:
            Correlation matrix
        """
        # Placeholder for correlation calculation
        # In production, calculate based on strategy returns
        correlation = {}
        for s1 in self.strategies.keys():
            correlation[s1] = {}
            for s2 in self.strategies.keys():
                if s1 == s2:
                    correlation[s1][s2] = 1.0
                else:
                    correlation[s1][s2] = 0.0  # Placeholder
        
        return correlation
    
    def reset_daily(self):
        """Reset daily PnL."""
        self.daily_pnl = 0.0
        logger.info("Daily PnL reset")
    
    def reset_weekly(self):
        """Reset weekly PnL."""
        self.weekly_pnl = 0.0
        logger.info("Weekly PnL reset")
    
    def reset_monthly(self):
        """Reset monthly stats."""
        self.monthly_pnl = 0.0
        self.peak_equity = self.current_equity
        logger.info("Monthly stats reset")
    
    def export_portfolio_state(self) -> Dict[str, Any]:
        """Export complete portfolio state for persistence."""
        return {
            'total_capital': self.total_capital,
            'current_equity': self.current_equity,
            'peak_equity': self.peak_equity,
            'strategies': {
                name: asdict(strategy)
                for name, strategy in self.strategies.items()
            },
            'strategy_performance': {
                name: asdict(perf)
                for name, perf in self.strategy_performance.items()
            },
            'portfolio_metrics': self.get_portfolio_metrics(),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }


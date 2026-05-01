"""
Advanced Risk & Exposure Engine
Implements institutional-grade risk management
"""
import numpy as np
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Advanced risk management system implementing:
    - Dynamic position sizing
    - Drawdown controls
    - Exposure limits
    - Consecutive loss handling
    """
    
    def __init__(self, config: Optional[Dict] = None):
        # Default configuration
        self.config = config or {
            # Base risk parameters
            'base_risk_per_trade': 0.01,  # 1% base risk
            'min_risk_per_trade': 0.005,  # 0.5% minimum
            'max_risk_per_trade': 0.02,   # 2% maximum
            
            # Drawdown limits
            'daily_loss_limit': 0.03,     # -3% daily limit (3R)
            'weekly_loss_limit': 0.06,    # -6% weekly limit (6R)
            'monthly_drawdown_cap': 0.12, # -12% monthly cap
            
            # Exposure limits by category
            'max_usd_exposure': 0.30,     # 30% max USD pairs
            'max_jpy_exposure': 0.25,     # 25% max JPY pairs
            'max_gold_exposure': 0.25,    # 25% max Gold
            'max_crypto_exposure': 0.15,  # 15% max Crypto
            
            # Recovery rules
            'consecutive_loss_reduction': 0.25,  # Reduce risk 25% per consecutive loss
            'max_consecutive_losses': 3,  # After 3 losses, pause
            
            # Correlation limits
            'max_correlated_positions': 3,
        }
        
        # State tracking
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        self.monthly_pnl = 0.0
        self.consecutive_losses = 0
        self.equity_peak = 100000  # Assume $100k starting
        self.current_equity = 100000
        self.open_positions: List[Dict] = []
        self.trade_history: List[Dict] = []
        
    def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        regime_multiplier: float = 1.0,
        volatility_multiplier: float = 1.0
    ) -> Dict[str, Any]:
        """
        Calculate optimal position size based on risk parameters.
        
        Formula: Position Size = (Account Risk $) / (Trade Risk in $)
        
        Args:
            symbol: Trading pair
            entry_price: Entry price
            stop_loss: Stop loss price
            regime_multiplier: ML regime risk adjustment (0.0-1.2)
            volatility_multiplier: ATR-based volatility adjustment
            
        Returns:
            Dictionary with position sizing details
        """
        try:
            # Calculate risk per trade
            base_risk = self.config['base_risk_per_trade']
            
            # Apply regime multiplier
            adjusted_risk = base_risk * regime_multiplier
            
            # Apply volatility adjustment (inverse - higher vol = lower size)
            if volatility_multiplier > 1.0:
                adjusted_risk = adjusted_risk / volatility_multiplier
            
            # Apply consecutive loss reduction
            if self.consecutive_losses > 0:
                loss_reduction = 1 - (self.consecutive_losses * self.config['consecutive_loss_reduction'])
                adjusted_risk = adjusted_risk * max(loss_reduction, 0.25)
            
            # Apply drawdown adjustment
            drawdown_pct = (self.equity_peak - self.current_equity) / self.equity_peak
            if drawdown_pct > 0.05:
                drawdown_factor = 1 - (drawdown_pct * 2)  # Reduce more as drawdown increases
                adjusted_risk = adjusted_risk * max(drawdown_factor, 0.3)
            
            # Clamp to min/max
            final_risk = max(
                self.config['min_risk_per_trade'],
                min(adjusted_risk, self.config['max_risk_per_trade'])
            )
            
            # Calculate dollar risk
            dollar_risk = self.current_equity * final_risk
            
            # Calculate pip/point risk
            price_risk = abs(entry_price - stop_loss)
            pip_risk = price_risk / entry_price  # As percentage
            
            # Calculate position size
            if price_risk > 0:
                position_size = dollar_risk / price_risk
            else:
                position_size = 0
            
            # Check exposure limits
            exposure_check = self._check_exposure_limits(symbol, position_size * entry_price)
            
            result = {
                'symbol': symbol,
                'position_size': round(position_size, 4),
                'dollar_risk': round(dollar_risk, 2),
                'risk_percentage': round(final_risk * 100, 2),
                'price_risk': round(price_risk, 5),
                'approved': exposure_check['approved'],
                'exposure_warning': exposure_check.get('warning'),
                'adjustments': {
                    'regime': regime_multiplier,
                    'volatility': volatility_multiplier,
                    'consecutive_loss': self.consecutive_losses,
                    'drawdown': round(drawdown_pct * 100, 2)
                }
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Position sizing error: {e}")
            return {
                'symbol': symbol,
                'position_size': 0,
                'approved': False,
                'error': str(e)
            }
    
    def _check_exposure_limits(self, symbol: str, new_exposure: float) -> Dict[str, Any]:
        """
        Check if new position would exceed exposure limits.
        """
        # Categorize symbol
        category = self._get_symbol_category(symbol)
        
        # Calculate current exposure in category
        current_exposure = sum(
            pos['exposure'] for pos in self.open_positions
            if self._get_symbol_category(pos['symbol']) == category
        )
        
        # Get limit for category
        limit_key = f'max_{category}_exposure'
        limit = self.config.get(limit_key, 0.25)
        max_exposure = self.current_equity * limit
        
        total_exposure = current_exposure + new_exposure
        
        if total_exposure > max_exposure:
            return {
                'approved': False,
                'warning': f"{category.upper()} exposure limit exceeded ({total_exposure/self.current_equity*100:.1f}% > {limit*100}%)"
            }
        
        return {'approved': True}
    
    def _get_symbol_category(self, symbol: str) -> str:
        """Categorize symbol for exposure tracking"""
        symbol = symbol.upper()
        
        if 'XAU' in symbol:
            return 'gold'
        elif 'BTC' in symbol or 'ETH' in symbol:
            return 'crypto'
        elif 'JPY' in symbol:
            return 'jpy'
        elif 'USD' in symbol:
            return 'usd'
        else:
            return 'other'
    
    def check_trading_allowed(self) -> Dict[str, Any]:
        """
        Check if trading is allowed based on risk limits.
        
        Returns:
            Dictionary with trading status and any restrictions
        """
        restrictions = []
        
        # Check daily loss limit
        if self.daily_pnl <= -self.config['daily_loss_limit'] * self.current_equity:
            restrictions.append(f"Daily loss limit reached ({self.config['daily_loss_limit']*100}%)")
        
        # Check weekly loss limit
        if self.weekly_pnl <= -self.config['weekly_loss_limit'] * self.current_equity:
            restrictions.append(f"Weekly loss limit reached ({self.config['weekly_loss_limit']*100}%)")
        
        # Check monthly drawdown
        monthly_dd = (self.equity_peak - self.current_equity) / self.equity_peak
        if monthly_dd >= self.config['monthly_drawdown_cap']:
            restrictions.append(f"Monthly drawdown cap reached ({self.config['monthly_drawdown_cap']*100}%)")
        
        # Check consecutive losses
        if self.consecutive_losses >= self.config['max_consecutive_losses']:
            restrictions.append(f"Max consecutive losses reached ({self.consecutive_losses})")
        
        return {
            'allowed': len(restrictions) == 0,
            'restrictions': restrictions,
            'risk_status': {
                'daily_pnl': round(self.daily_pnl, 2),
                'weekly_pnl': round(self.weekly_pnl, 2),
                'drawdown_pct': round(monthly_dd * 100, 2),
                'consecutive_losses': self.consecutive_losses
            }
        }
    
    def record_trade_result(self, result: str, pnl: float):
        """
        Record trade result and update risk state.
        
        Args:
            result: 'WIN' or 'LOSS'
            pnl: Profit/loss in dollars
        """
        self.daily_pnl += pnl
        self.weekly_pnl += pnl
        self.monthly_pnl += pnl
        self.current_equity += pnl
        
        if result == 'WIN':
            self.consecutive_losses = 0
            if self.current_equity > self.equity_peak:
                self.equity_peak = self.current_equity
        else:
            self.consecutive_losses += 1
        
        self.trade_history.append({
            'result': result,
            'pnl': pnl,
            'timestamp': datetime.utcnow().isoformat(),
            'equity': self.current_equity
        })
        
        logger.info(f"Trade recorded: {result}, PnL: ${pnl:.2f}, Equity: ${self.current_equity:.2f}")
    
    def reset_daily(self):
        """Reset daily PnL (call at day start)"""
        self.daily_pnl = 0.0
    
    def reset_weekly(self):
        """Reset weekly PnL (call at week start)"""
        self.weekly_pnl = 0.0
    
    def reset_monthly(self):
        """Reset monthly stats (call at month start)"""
        self.monthly_pnl = 0.0
        self.equity_peak = self.current_equity
    
    def get_risk_metrics(self) -> Dict[str, Any]:
        """Get current risk metrics"""
        return {
            'current_equity': self.current_equity,
            'equity_peak': self.equity_peak,
            'drawdown_pct': round((self.equity_peak - self.current_equity) / self.equity_peak * 100, 2),
            'daily_pnl': round(self.daily_pnl, 2),
            'weekly_pnl': round(self.weekly_pnl, 2),
            'monthly_pnl': round(self.monthly_pnl, 2),
            'consecutive_losses': self.consecutive_losses,
            'open_positions': len(self.open_positions),
            'total_trades': len(self.trade_history)
        }

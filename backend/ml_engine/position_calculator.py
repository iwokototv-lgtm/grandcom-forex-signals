"""
Position Sizing & Risk Management Calculator
Integrated with live trading account
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class PositionCalculator:
    """
    Calculate position sizes based on:
    - Account balance
    - Risk per trade (%)
    - Stop loss distance
    - Current equity
    """
    
    def __init__(self, account_balance: float = 1000.0, risk_per_trade: float = 0.05):
        """
        Initialize position calculator.
        
        Args:
            account_balance: Live account size in USD (default: $1,000)
            risk_per_trade: Risk percentage per trade (default: 5% = $50)
        """
        self.account_balance = account_balance
        self.risk_per_trade = risk_per_trade
        self.risk_amount = account_balance * risk_per_trade
        
        # Trade tracking
        self.trades = []
        self.current_equity = account_balance
        self.peak_equity = account_balance
        self.consecutive_losses = 0
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        
        logger.info(
            f"Position Calculator initialized: "
            f"Account=${account_balance:.2f}, Risk/Trade=${self.risk_amount:.2f} ({risk_per_trade*100}%)"
        )
    
    def calculate_position_size(
        self,
        entry_price: float,
        stop_loss: float,
        pair: str = "XAUUSD"
    ) -> Dict[str, Any]:
        """
        Calculate position size for a trade.
        
        Formula: Position Size = Risk Amount / (Entry - SL)
        
        Args:
            entry_price: Entry price
            stop_loss: Stop loss price
            pair: Trading pair (XAUUSD, XAUEUR)
            
        Returns:
            Dictionary with position sizing details
        """
        try:
            # Calculate price risk (distance from entry to SL)
            price_risk = abs(entry_price - stop_loss)
            
            if price_risk <= 0:
                logger.error(f"Invalid price risk: entry={entry_price}, sl={stop_loss}")
                return {
                    'pair': pair,
                    'position_size': 0,
                    'lot_size': 0,
                    'approved': False,
                    'error': 'Invalid stop loss distance'
                }
            
            # Position size = Risk Amount / Price Risk
            position_size = self.risk_amount / price_risk
            
            # For gold (XAUUSD/XAUEUR), typically traded in ounces
            # 1 standard lot = 100 ounces
            lot_size = position_size / 100
            
            # Calculate potential profit/loss
            potential_loss = self.risk_amount
            
            # Apply risk reduction if on losing streak
            risk_reduction_factor = 1.0
            if self.consecutive_losses > 0:
                risk_reduction_factor = max(0.5, 1.0 - (self.consecutive_losses * 0.25))
            
            adjusted_position_size = position_size * risk_reduction_factor
            adjusted_lot_size = adjusted_position_size / 100
            
            # Check drawdown limits
            current_drawdown = (self.peak_equity - self.current_equity) / self.peak_equity
            max_drawdown = 0.10  # 10% max drawdown
            
            approved = current_drawdown < max_drawdown
            
            result = {
                'pair': pair,
                'entry_price': round(entry_price, 2),
                'stop_loss': round(stop_loss, 2),
                'price_risk': round(price_risk, 2),
                'position_size': round(position_size, 4),
                'lot_size': round(lot_size, 4),
                'adjusted_position_size': round(adjusted_position_size, 4),
                'adjusted_lot_size': round(adjusted_lot_size, 4),
                'risk_amount': round(self.risk_amount, 2),
                'risk_reduction_factor': round(risk_reduction_factor, 2),
                'consecutive_losses': self.consecutive_losses,
                'current_drawdown_pct': round(current_drawdown * 100, 2),
                'approved': approved,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            
            if not approved:
                result['warning'] = f"Drawdown {current_drawdown*100:.1f}% exceeds limit {max_drawdown*100}%"
            
            logger.info(
                f"[{pair}] Position Size: {adjusted_lot_size:.4f} lots | "
                f"Risk: ${self.risk_amount:.2f} | "
                f"Approved: {approved}"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Position calculation error: {e}")
            return {
                'pair': pair,
                'position_size': 0,
                'approved': False,
                'error': str(e)
            }
    
    def record_trade(
        self,
        pair: str,
        signal_type: str,
        entry_price: float,
        exit_price: float,
        position_size: float,
        result: str  # 'WIN' or 'LOSS'
    ) -> Dict[str, Any]:
        """
        Record a completed trade and update equity.
        
        Args:
            pair: Trading pair
            signal_type: BUY or SELL
            entry_price: Entry price
            exit_price: Exit price (TP or SL)
            position_size: Position size in ounces
            result: WIN or LOSS
            
        Returns:
            Trade record with updated equity
        """
        try:
            # Calculate PnL
            if signal_type == "BUY":
                pnl = (exit_price - entry_price) * position_size
            else:  # SELL
                pnl = (entry_price - exit_price) * position_size
            
            # Update equity
            self.current_equity += pnl
            self.daily_pnl += pnl
            self.weekly_pnl += pnl
            
            # Update peak equity
            if self.current_equity > self.peak_equity:
                self.peak_equity = self.current_equity
            
            # Update consecutive losses
            if result == "WIN":
                self.consecutive_losses = 0
            else:
                self.consecutive_losses += 1
            
            # Record trade
            trade = {
                'pair': pair,
                'type': signal_type,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'position_size': position_size,
                'pnl': round(pnl, 2),
                'result': result,
                'equity_after': round(self.current_equity, 2),
                'consecutive_losses': self.consecutive_losses,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            
            self.trades.append(trade)
            
            logger.info(
                f"[{pair}] Trade recorded: {result} | "
                f"PnL: ${pnl:.2f} | "
                f"Equity: ${self.current_equity:.2f}"
            )
            
            return trade
            
        except Exception as e:
            logger.error(f"Trade recording error: {e}")
            return {'error': str(e)}
    
    def get_risk_metrics(self) -> Dict[str, Any]:
        """Get current risk and equity metrics."""
        drawdown = (self.peak_equity - self.current_equity) / self.peak_equity
        
        return {
            'account_balance': round(self.account_balance, 2),
            'current_equity': round(self.current_equity, 2),
            'peak_equity': round(self.peak_equity, 2),
            'drawdown_pct': round(drawdown * 100, 2),
            'daily_pnl': round(self.daily_pnl, 2),
            'weekly_pnl': round(self.weekly_pnl, 2),
            'risk_per_trade': round(self.risk_amount, 2),
            'consecutive_losses': self.consecutive_losses,
            'total_trades': len(self.trades),
            'win_rate': self._calculate_win_rate(),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
    
    def _calculate_win_rate(self) -> float:
        """Calculate win rate percentage."""
        if not self.trades:
            return 0.0
        
        wins = sum(1 for t in self.trades if t.get('result') == 'WIN')
        return round((wins / len(self.trades)) * 100, 2)
    
    def reset_daily(self):
        """Reset daily PnL."""
        self.daily_pnl = 0.0
        logger.info("Daily PnL reset")
    
    def reset_weekly(self):
        """Reset weekly PnL."""
        self.weekly_pnl = 0.0
        logger.info("Weekly PnL reset")
    
    def get_trade_history(self, limit: int = 10) -> list:
        """Get recent trade history."""
        return self.trades[-limit:]


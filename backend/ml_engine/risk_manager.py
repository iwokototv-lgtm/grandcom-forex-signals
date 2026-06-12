"""
Advanced Risk & Exposure Engine
Implements institutional-grade risk management with:
- Real-time daily P&L tracking (MongoDB-backed)
- Auto-stop at 5% daily loss
- Auto-stop at 15% account drawdown
- Telegram alerts at 2%, 4%, 5% loss thresholds
- Midnight UTC daily counter reset
"""
import numpy as np
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta, timezone
import logging

logger = logging.getLogger(__name__)

# Alert thresholds (fraction of account balance)
DAILY_LOSS_ALERT_THRESHOLDS = [0.02, 0.04, 0.05]   # 2%, 4%, 5%
DAILY_LOSS_HARD_STOP = 0.05                          # 5%  — halt trading
DRAWDOWN_HARD_STOP = 0.15                            # 15% — halt trading


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
        self.equity_peak = 0.0  # Initialised on first set_account_balance call
        self.current_equity = 0.0
        self.open_positions: List[Dict] = []
        self.trade_history: List[Dict] = []

        # New: real-time risk state
        self._db = None
        self._bot = None
        self._telegram_channel = None
        self._trading_halted: bool = False
        self._halt_reason: str = ""
        self._daily_reset_date: Optional[str] = None  # "YYYY-MM-DD"
        self._alerted_thresholds: List[float] = []    # thresholds already notified today

    # ------------------------------------------------------------------
    # DB / Telegram injection
    # ------------------------------------------------------------------

    def set_db(self, db) -> None:
        self._db = db

    def set_telegram(self, bot, channel) -> None:
        self._bot = bot
        self._telegram_channel = channel

    def set_account_balance(self, balance: float) -> None:
        self.current_equity = balance
        # Initialise peak on first call, or update if balance is a new high
        if self.equity_peak == 0.0 or balance > self.equity_peak:
            self.equity_peak = balance
            logger.info(f"RiskManager: equity_peak initialised/updated to {self.equity_peak:.2f}")

    # ------------------------------------------------------------------
    # Daily reset (call at midnight UTC or on startup)
    # ------------------------------------------------------------------

    def _maybe_reset_daily(self) -> None:
        """Auto-reset daily counters at midnight UTC."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily_reset_date != today:
            self.daily_pnl = 0.0
            self._alerted_thresholds = []
            # Only lift halt if it was a daily-loss halt (not drawdown)
            if self._trading_halted and "DAILY" in self._halt_reason:
                self._trading_halted = False
                self._halt_reason = ""
                logger.info("RiskManager: Daily reset — trading halt lifted")
            self._daily_reset_date = today
            logger.info(f"RiskManager: Daily counters reset for {today}")

    # ------------------------------------------------------------------
    # Core risk checks (async — can write to MongoDB)
    # ------------------------------------------------------------------

    async def check_daily_loss(self) -> Dict[str, Any]:
        """
        Check daily P&L against hard-stop threshold.
        Sends Telegram alerts at 2%, 4%, 5% loss levels.
        """
        self._maybe_reset_daily()
        loss_pct = abs(self.daily_pnl) / max(self.current_equity, 1) if self.daily_pnl < 0 else 0.0

        # Alert thresholds
        for threshold in DAILY_LOSS_ALERT_THRESHOLDS:
            if loss_pct >= threshold and threshold not in self._alerted_thresholds:
                self._alerted_thresholds.append(threshold)
                await self._send_risk_alert(
                    f"⚠️ DAILY LOSS ALERT: {loss_pct * 100:.1f}% "
                    f"(threshold {threshold * 100:.0f}%)\n"
                    f"Daily P&L: ${self.daily_pnl:.2f}"
                )

        # Hard stop
        if loss_pct >= DAILY_LOSS_HARD_STOP and not self._trading_halted:
            self._trading_halted = True
            self._halt_reason = f"DAILY_LOSS_LIMIT ({loss_pct * 100:.1f}%)"
            await self._send_risk_alert(
                f"🛑 TRADING HALTED — Daily loss limit hit: {loss_pct * 100:.1f}%\n"
                f"Daily P&L: ${self.daily_pnl:.2f}\n"
                f"Will resume at midnight UTC."
            )
            await self._log_risk_event("DAILY_LOSS_HALT", loss_pct)

        return {
            "halted": self._trading_halted,
            "halt_reason": self._halt_reason,
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_loss_pct": round(loss_pct * 100, 2),
            "hard_stop_pct": DAILY_LOSS_HARD_STOP * 100,
        }

    async def check_account_drawdown(self) -> Dict[str, Any]:
        """
        Check total drawdown from equity peak.
        Halts trading if drawdown ≥ 15%.
        """
        if self.equity_peak <= 0:
            return {"halted": False, "drawdown_pct": 0.0}

        drawdown_pct = (self.equity_peak - self.current_equity) / self.equity_peak

        if drawdown_pct >= DRAWDOWN_HARD_STOP and not self._trading_halted:
            self._trading_halted = True
            self._halt_reason = f"DRAWDOWN_LIMIT ({drawdown_pct * 100:.1f}%)"
            await self._send_risk_alert(
                f"🛑 TRADING HALTED — Account drawdown limit hit: "
                f"{drawdown_pct * 100:.1f}%\n"
                f"Peak: ${self.equity_peak:.2f} | Current: ${self.current_equity:.2f}"
            )
            await self._log_risk_event("DRAWDOWN_HALT", drawdown_pct)

        return {
            "halted": self._trading_halted,
            "halt_reason": self._halt_reason,
            "drawdown_pct": round(drawdown_pct * 100, 2),
            "hard_stop_pct": DRAWDOWN_HARD_STOP * 100,
            "equity_peak": round(self.equity_peak, 2),
            "current_equity": round(self.current_equity, 2),
        }

    async def check_position_limits(self, position_count: int, max_positions: int = 5) -> Dict[str, Any]:
        """Check if position count is within limits."""
        allowed = position_count < max_positions
        return {
            "allowed": allowed,
            "position_count": position_count,
            "max_positions": max_positions,
        }

    async def enforce_risk_limits(self) -> Dict[str, Any]:
        """
        Master risk check — call before every signal generation cycle.
        Returns ``{"trading_allowed": bool, "reason": str, ...}``.
        """
        self._maybe_reset_daily()

        daily = await self.check_daily_loss()
        drawdown = await self.check_account_drawdown()

        if self._trading_halted:
            return {
                "trading_allowed": False,
                "reason": self._halt_reason,
                "daily": daily,
                "drawdown": drawdown,
            }

        return {
            "trading_allowed": True,
            "reason": "OK",
            "daily": daily,
            "drawdown": drawdown,
        }

    # ------------------------------------------------------------------
    # Telegram / MongoDB helpers
    # ------------------------------------------------------------------

    async def _send_risk_alert(self, message: str) -> None:
        """Send a risk alert via Telegram."""
        if self._bot is None or self._telegram_channel is None:
            logger.warning(f"RiskManager alert (no Telegram): {message}")
            return
        try:
            await self._bot.send_message(
                chat_id=self._telegram_channel,
                text=message,
            )
        except Exception as exc:
            logger.error(f"RiskManager Telegram alert failed: {exc}")

    async def _log_risk_event(self, event_type: str, value: float) -> None:
        """Persist a risk event to MongoDB."""
        if self._db is None:
            return
        try:
            await self._db.risk_events.insert_one({
                "event_type": event_type,
                "value": round(value * 100, 2),
                "daily_pnl": round(self.daily_pnl, 2),
                "current_equity": round(self.current_equity, 2),
                "equity_peak": round(self.equity_peak, 2),
                "timestamp": datetime.now(timezone.utc),
            })
        except Exception as exc:
            logger.error(f"risk_events insert failed: {exc}")

    def get_risk_status(self) -> Dict[str, Any]:
        """Return a compact risk status dict for Telegram alerts."""
        self._maybe_reset_daily()
        loss_pct = abs(self.daily_pnl) / max(self.current_equity, 1) if self.daily_pnl < 0 else 0.0
        drawdown_pct = (
            (self.equity_peak - self.current_equity) / self.equity_peak
            if self.equity_peak > 0 else 0.0
        )

        if drawdown_pct >= 0.10 or loss_pct >= 0.04:
            risk_level = "RED"
        elif drawdown_pct >= 0.05 or loss_pct >= 0.02:
            risk_level = "YELLOW"
        else:
            risk_level = "GREEN"

        return {
            "trading_halted": self._trading_halted,
            "halt_reason": self._halt_reason,
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_loss_pct": round(loss_pct * 100, 2),
            "drawdown_pct": round(drawdown_pct * 100, 2),
            "risk_level": risk_level,
            "equity_peak": round(self.equity_peak, 2),
            "current_equity": round(self.current_equity, 2),
        }
        
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

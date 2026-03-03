"""
Signal Outcome Tracker - Automatically monitors active signals and closes them when TP/SL is hit.
This is the critical missing feature that enables automatic profit-taking.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
import aiohttp
import os

logger = logging.getLogger(__name__)

# Price fetch configuration
SYMBOL_MAP = {
    "XAUUSD": "XAU/USD",
    "XAUEUR": "XAU/EUR",
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "USDJPY": "USD/JPY",
    "EURJPY": "EUR/JPY",
    "GBPJPY": "GBP/JPY",
    "AUDUSD": "AUD/USD",
    "USDCAD": "USD/CAD",
    "USDCHF": "USD/CHF",
    "BTCUSD": "BTC/USD"
}


class SignalOutcomeTracker:
    """
    Tracks active signals and automatically closes them when TP/SL levels are hit.
    This solves the critical "not taking profit" issue.
    """
    
    def __init__(self, db, twelve_data_api_key: str, telegram_bot_token: str = None, telegram_channel_id: str = None):
        self.db = db
        self.twelve_data_api_key = twelve_data_api_key
        self.telegram_bot_token = telegram_bot_token
        self.telegram_channel_id = telegram_channel_id
        self.is_running = False
        self._task = None
        
    async def get_live_price(self, symbol: str) -> Optional[float]:
        """Fetch current live price for a symbol"""
        try:
            api_symbol = SYMBOL_MAP.get(symbol, symbol)
            url = "https://api.twelvedata.com/price"
            params = {
                "symbol": api_symbol,
                "apikey": self.twelve_data_api_key
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as response:
                    data = await response.json()
                    
                    if "price" in data:
                        return float(data["price"])
                    else:
                        logger.warning(f"No price data for {symbol}: {data}")
                        return None
        except Exception as e:
            logger.error(f"Error fetching price for {symbol}: {e}")
            return None
    
    async def check_signal_outcome(self, signal: Dict[str, Any], current_price: float) -> Optional[Dict[str, Any]]:
        """
        Check if a signal has hit any TP or SL level.
        Returns outcome details if hit, None otherwise.
        """
        try:
            signal_type = signal.get("type", "").upper()
            entry_price = signal.get("entry_price", 0)
            sl_price = signal.get("sl_price", 0)
            tp_levels = signal.get("tp_levels", [])
            
            if not signal_type or not entry_price or not sl_price or not tp_levels:
                return None
            
            outcome = None
            
            if signal_type == "BUY":
                # For BUY: TP is above entry, SL is below entry
                # Check Stop Loss first (price went down)
                if current_price <= sl_price:
                    pips = self._calculate_pips(signal.get("pair", ""), entry_price, current_price, signal_type)
                    outcome = {
                        "status": "CLOSED_SL",
                        "result": "LOSS",
                        "exit_price": current_price,
                        "pips": pips,
                        "tp_hit": None,
                        "message": f"Stop Loss hit at {current_price}"
                    }
                # Check Take Profits (price went up)
                elif len(tp_levels) >= 3 and current_price >= tp_levels[2]:
                    pips = self._calculate_pips(signal.get("pair", ""), entry_price, current_price, signal_type)
                    outcome = {
                        "status": "CLOSED_TP3",
                        "result": "WIN",
                        "exit_price": current_price,
                        "pips": pips,
                        "tp_hit": 3,
                        "message": f"TP3 hit at {current_price}"
                    }
                elif len(tp_levels) >= 2 and current_price >= tp_levels[1]:
                    pips = self._calculate_pips(signal.get("pair", ""), entry_price, current_price, signal_type)
                    outcome = {
                        "status": "CLOSED_TP2",
                        "result": "WIN",
                        "exit_price": current_price,
                        "pips": pips,
                        "tp_hit": 2,
                        "message": f"TP2 hit at {current_price}"
                    }
                elif len(tp_levels) >= 1 and current_price >= tp_levels[0]:
                    pips = self._calculate_pips(signal.get("pair", ""), entry_price, current_price, signal_type)
                    outcome = {
                        "status": "CLOSED_TP1",
                        "result": "WIN",
                        "exit_price": current_price,
                        "pips": pips,
                        "tp_hit": 1,
                        "message": f"TP1 hit at {current_price}"
                    }
                    
            elif signal_type == "SELL":
                # For SELL: TP is below entry, SL is above entry
                # Check Stop Loss first (price went up)
                if current_price >= sl_price:
                    pips = self._calculate_pips(signal.get("pair", ""), entry_price, current_price, signal_type)
                    outcome = {
                        "status": "CLOSED_SL",
                        "result": "LOSS",
                        "exit_price": current_price,
                        "pips": pips,
                        "tp_hit": None,
                        "message": f"Stop Loss hit at {current_price}"
                    }
                # Check Take Profits (price went down)
                elif len(tp_levels) >= 3 and current_price <= tp_levels[2]:
                    pips = self._calculate_pips(signal.get("pair", ""), entry_price, current_price, signal_type)
                    outcome = {
                        "status": "CLOSED_TP3",
                        "result": "WIN",
                        "exit_price": current_price,
                        "pips": pips,
                        "tp_hit": 3,
                        "message": f"TP3 hit at {current_price}"
                    }
                elif len(tp_levels) >= 2 and current_price <= tp_levels[1]:
                    pips = self._calculate_pips(signal.get("pair", ""), entry_price, current_price, signal_type)
                    outcome = {
                        "status": "CLOSED_TP2",
                        "result": "WIN",
                        "exit_price": current_price,
                        "pips": pips,
                        "tp_hit": 2,
                        "message": f"TP2 hit at {current_price}"
                    }
                elif len(tp_levels) >= 1 and current_price <= tp_levels[0]:
                    pips = self._calculate_pips(signal.get("pair", ""), entry_price, current_price, signal_type)
                    outcome = {
                        "status": "CLOSED_TP1",
                        "result": "WIN",
                        "exit_price": current_price,
                        "pips": pips,
                        "tp_hit": 1,
                        "message": f"TP1 hit at {current_price}"
                    }
            
            return outcome
            
        except Exception as e:
            logger.error(f"Error checking signal outcome: {e}")
            return None
    
    def _calculate_pips(self, pair: str, entry_price: float, exit_price: float, signal_type: str) -> float:
        """Calculate pips gained/lost based on pair type"""
        try:
            # Determine pip value based on pair
            if pair in ["XAUUSD", "XAUEUR"]:
                pip_value = 0.1  # Gold
            elif pair == "BTCUSD":
                pip_value = 1.0  # Bitcoin
            elif pair in ["USDJPY", "EURJPY", "GBPJPY"]:
                pip_value = 0.01  # JPY pairs
            else:
                pip_value = 0.0001  # Standard forex pairs
            
            price_diff = exit_price - entry_price
            
            if signal_type == "SELL":
                price_diff = -price_diff  # Invert for sell
            
            pips = price_diff / pip_value
            return round(pips, 1)
            
        except Exception as e:
            logger.error(f"Error calculating pips: {e}")
            return 0.0
    
    async def close_signal(self, signal_id: str, outcome: Dict[str, Any], on_close_callback=None) -> bool:
        """Update signal status in database and send Telegram notification"""
        try:
            # Update in database
            update_result = await self.db.signals.update_one(
                {"_id": ObjectId(signal_id)},
                {"$set": {
                    "status": outcome["status"],
                    "result": outcome["result"],
                    "exit_price": outcome["exit_price"],
                    "pips": outcome["pips"],
                    "closed_at": datetime.now(timezone.utc)
                }}
            )
            
            if update_result.modified_count > 0:
                # Get full signal details for notification
                signal = await self.db.signals.find_one({"_id": ObjectId(signal_id)})
                if signal:
                    await self.send_close_notification(signal, outcome)
                    
                    # Call the callback to record result for drawdown protection
                    if on_close_callback:
                        try:
                            on_close_callback(signal.get("pair"), outcome["result"], outcome["pips"])
                        except Exception as cb_err:
                            logger.warning(f"Callback error: {cb_err}")
                            
                logger.info(f"Signal {signal_id} closed: {outcome['status']} - {outcome['result']}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error closing signal {signal_id}: {e}")
            return False
    
    async def send_close_notification(self, signal: Dict[str, Any], outcome: Dict[str, Any]):
        """Send trade closed notification to Telegram with rate limiting"""
        try:
            if not self.telegram_bot_token or not self.telegram_channel_id:
                logger.warning("Telegram not configured for close notifications")
                return
            
            from telegram import Bot
            from telegram.error import RetryAfter
            
            bot = Bot(token=self.telegram_bot_token)
            
            # Determine emoji based on result
            result_emoji = "✅" if outcome["result"] == "WIN" else "❌"
            pips_emoji = "📈" if outcome["pips"] > 0 else "📉"
            
            tp_info = ""
            if outcome.get("tp_hit"):
                tp_info = f"\n<b>Target Hit:</b> TP{outcome['tp_hit']}"
            
            message = f"""
{result_emoji} <b>TRADE CLOSED: {signal.get('pair', 'N/A')}</b> {result_emoji}

<b>📊 Direction:</b> {signal.get('type', 'N/A')}
<b>💰 Entry:</b> {signal.get('entry_price', 'N/A')}
<b>🎯 Exit:</b> {outcome['exit_price']}
<b>{pips_emoji} Pips:</b> {outcome['pips']:+.1f}
<b>📋 Result:</b> {outcome['result']}{tp_info}

<b>⏰ Closed:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

<i>🤖 Auto-tracked by Grandcom ML Engine</i>
            """
            
            try:
                await bot.send_message(
                    chat_id=self.telegram_channel_id,
                    text=message,
                    parse_mode="HTML"
                )
                logger.info(f"Close notification sent for {signal.get('pair')}")
                # Rate limiting - wait 1 second between messages
                await asyncio.sleep(1)
            except RetryAfter as e:
                # Telegram flood control - wait and don't retry (signal is already closed in DB)
                logger.warning(f"Telegram rate limited, notification skipped (signal already closed): {e.retry_after}s")
            
        except Exception as e:
            logger.error(f"Error sending close notification: {e}")
    
    async def check_all_active_signals(self) -> Dict[str, Any]:
        """Check all active signals against current prices"""
        results = {
            "checked": 0,
            "closed": 0,
            "errors": 0,
            "details": []
        }
        
        try:
            # Get all active signals
            active_signals = await self.db.signals.find({
                "status": "ACTIVE"
            }).to_list(length=100)
            
            if not active_signals:
                logger.info("No active signals to check")
                return results
            
            logger.info(f"Checking {len(active_signals)} active signals...")
            
            # Group signals by pair to minimize API calls
            signals_by_pair = {}
            for signal in active_signals:
                pair = signal.get("pair")
                if pair not in signals_by_pair:
                    signals_by_pair[pair] = []
                signals_by_pair[pair].append(signal)
            
            # Check each pair
            for pair, signals in signals_by_pair.items():
                try:
                    # Get live price for this pair
                    current_price = await self.get_live_price(pair)
                    
                    if current_price is None:
                        logger.warning(f"Could not get price for {pair}")
                        results["errors"] += len(signals)
                        continue
                    
                    # Check each signal for this pair
                    for signal in signals:
                        results["checked"] += 1
                        signal_id = str(signal["_id"])
                        
                        outcome = await self.check_signal_outcome(signal, current_price)
                        
                        if outcome:
                            # Signal hit TP or SL - close it
                            closed = await self.close_signal(signal_id, outcome)
                            if closed:
                                results["closed"] += 1
                                results["details"].append({
                                    "signal_id": signal_id,
                                    "pair": pair,
                                    "outcome": outcome["status"],
                                    "pips": outcome["pips"]
                                })
                    
                    # Rate limiting between pairs
                    await asyncio.sleep(0.5)
                    
                except Exception as e:
                    logger.error(f"Error checking pair {pair}: {e}")
                    results["errors"] += 1
            
            logger.info(f"Outcome check complete: {results['checked']} checked, {results['closed']} closed")
            return results
            
        except Exception as e:
            logger.error(f"Error in check_all_active_signals: {e}")
            results["errors"] += 1
            return results
    
    async def run_tracker_loop(self, interval_seconds: int = 60):
        """Main loop that runs the tracker periodically"""
        self.is_running = True
        logger.info(f"Starting Signal Outcome Tracker (interval: {interval_seconds}s)")
        
        while self.is_running:
            try:
                await self.check_all_active_signals()
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                logger.info("Signal Outcome Tracker cancelled")
                break
            except Exception as e:
                logger.error(f"Error in tracker loop: {e}")
                await asyncio.sleep(30)  # Wait before retry
        
        logger.info("Signal Outcome Tracker stopped")
    
    def start(self, interval_seconds: int = 60):
        """Start the tracker as a background task"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run_tracker_loop(interval_seconds))
            logger.info("Signal Outcome Tracker started")
        return self._task
    
    def stop(self):
        """Stop the tracker"""
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("Signal Outcome Tracker stop requested")


# Global instance (will be initialized in server.py)
outcome_tracker: Optional[SignalOutcomeTracker] = None


def init_outcome_tracker(db, twelve_data_api_key: str, telegram_bot_token: str = None, telegram_channel_id: str = None) -> SignalOutcomeTracker:
    """Initialize the global outcome tracker instance"""
    global outcome_tracker
    outcome_tracker = SignalOutcomeTracker(
        db=db,
        twelve_data_api_key=twelve_data_api_key,
        telegram_bot_token=telegram_bot_token,
        telegram_channel_id=telegram_channel_id
    )
    return outcome_tracker


def get_outcome_tracker() -> Optional[SignalOutcomeTracker]:
    """Get the global outcome tracker instance"""
    return outcome_tracker

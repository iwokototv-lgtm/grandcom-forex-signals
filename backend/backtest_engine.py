"""
Historical Backtesting Engine for Grandcom Forex Signals Pro
Supports backtesting over 3-10 years of historical data
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import pandas as pd
import numpy as np
import aiohttp
import json

logger = logging.getLogger(__name__)


class TradeResult(Enum):
    WIN_TP1 = "WIN_TP1"
    WIN_TP2 = "WIN_TP2"
    WIN_TP3 = "WIN_TP3"
    LOSS_SL = "LOSS_SL"
    TIMEOUT = "TIMEOUT"
    ACTIVE = "ACTIVE"


@dataclass
class BacktestTrade:
    """Represents a single trade in the backtest"""
    pair: str
    direction: str  # BUY or SELL
    entry_price: float
    entry_time: datetime
    sl_price: float
    tp1_price: float
    tp2_price: float
    tp3_price: float
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    result: TradeResult = TradeResult.ACTIVE
    pips: float = 0.0
    pip_value: float = 0.0001
    max_drawdown: float = 0.0
    max_profit: float = 0.0


@dataclass
class BacktestConfig:
    """Configuration for backtesting"""
    pair: str
    start_date: datetime
    end_date: datetime
    timeframe: str = "1h"  # 1h, 4h, 1day
    initial_balance: float = 10000.0
    risk_per_trade: float = 0.02  # 2% risk per trade
    tp1_pips: float = 5.0
    tp2_pips: float = 10.0
    tp3_pips: float = 15.0
    sl_pips: float = 15.0
    use_atr_for_sl: bool = True
    atr_sl_multiplier: float = 1.5
    max_trades_per_day: int = 3
    partial_close_tp1: float = 0.33  # Close 33% at TP1
    partial_close_tp2: float = 0.33  # Close 33% at TP2
    # Remaining 34% closes at TP3 or SL


@dataclass
class BacktestResults:
    """Results from a backtest run"""
    config: BacktestConfig
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pips: float = 0.0
    average_pips_per_trade: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    max_drawdown_pips: float = 0.0
    max_drawdown_percent: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    final_balance: float = 0.0
    return_percent: float = 0.0
    trades: List[BacktestTrade] = field(default_factory=list)
    monthly_performance: Dict[str, float] = field(default_factory=dict)
    yearly_performance: Dict[str, float] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "config": {
                "pair": self.config.pair,
                "start_date": self.config.start_date.isoformat(),
                "end_date": self.config.end_date.isoformat(),
                "timeframe": self.config.timeframe,
                "initial_balance": self.config.initial_balance,
                "tp1_pips": self.config.tp1_pips,
                "tp2_pips": self.config.tp2_pips,
                "tp3_pips": self.config.tp3_pips,
                "sl_pips": self.config.sl_pips,
            },
            "summary": {
                "total_trades": self.total_trades,
                "winning_trades": self.winning_trades,
                "losing_trades": self.losing_trades,
                "win_rate": round(self.win_rate, 2),
                "total_pips": round(self.total_pips, 1),
                "average_pips_per_trade": round(self.average_pips_per_trade, 2),
                "max_consecutive_wins": self.max_consecutive_wins,
                "max_consecutive_losses": self.max_consecutive_losses,
                "max_drawdown_pips": round(self.max_drawdown_pips, 1),
                "max_drawdown_percent": round(self.max_drawdown_percent, 2),
                "profit_factor": round(self.profit_factor, 2),
                "sharpe_ratio": round(self.sharpe_ratio, 2),
                "final_balance": round(self.final_balance, 2),
                "return_percent": round(self.return_percent, 2),
            },
            "monthly_performance": self.monthly_performance,
            "yearly_performance": self.yearly_performance,
            "trades_sample": [
                {
                    "pair": t.pair,
                    "direction": t.direction,
                    "entry_price": t.entry_price,
                    "entry_time": t.entry_time.isoformat(),
                    "exit_price": t.exit_price,
                    "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                    "result": t.result.value,
                    "pips": round(t.pips, 1),
                }
                for t in self.trades[-50:]  # Last 50 trades
            ]
        }


class BacktestEngine:
    """
    Historical backtesting engine that simulates trading strategies
    on historical market data spanning 3-10 years.
    """
    
    # Pair configurations
    PAIR_CONFIG = {
        "XAUUSD": {"pip_value": 0.1, "decimals": 2, "symbol": "XAU/USD"},
        "XAUEUR": {"pip_value": 0.1, "decimals": 2, "symbol": "XAU/EUR"},
        "BTCUSD": {"pip_value": 1.0, "decimals": 2, "symbol": "BTC/USD"},
        "EURUSD": {"pip_value": 0.0001, "decimals": 5, "symbol": "EUR/USD"},
        "GBPUSD": {"pip_value": 0.0001, "decimals": 5, "symbol": "GBP/USD"},
        "USDJPY": {"pip_value": 0.01, "decimals": 3, "symbol": "USD/JPY"},
        "EURJPY": {"pip_value": 0.01, "decimals": 3, "symbol": "EUR/JPY"},
        "GBPJPY": {"pip_value": 0.01, "decimals": 3, "symbol": "GBP/JPY"},
        "AUDUSD": {"pip_value": 0.0001, "decimals": 5, "symbol": "AUD/USD"},
        "USDCAD": {"pip_value": 0.0001, "decimals": 5, "symbol": "USD/CAD"},
        "USDCHF": {"pip_value": 0.0001, "decimals": 5, "symbol": "USD/CHF"},
        # Asian session pairs
        "NZDUSD": {"pip_value": 0.0001, "decimals": 5, "symbol": "NZD/USD"},
        "AUDJPY": {"pip_value": 0.01, "decimals": 3, "symbol": "AUD/JPY"},
        "CADJPY": {"pip_value": 0.01, "decimals": 3, "symbol": "CAD/JPY"},
        # NEW Institutional pairs
        "CHFJPY": {"pip_value": 0.01, "decimals": 3, "symbol": "CHF/JPY"},
        "EURAUD": {"pip_value": 0.0001, "decimals": 5, "symbol": "EUR/AUD"},
        "GBPCAD": {"pip_value": 0.0001, "decimals": 5, "symbol": "GBP/CAD"},
        "EURCAD": {"pip_value": 0.0001, "decimals": 5, "symbol": "EUR/CAD"},
        "GBPAUD": {"pip_value": 0.0001, "decimals": 5, "symbol": "GBP/AUD"},
        "AUDNZD": {"pip_value": 0.0001, "decimals": 5, "symbol": "AUD/NZD"},
        "EURGBP": {"pip_value": 0.0001, "decimals": 5, "symbol": "EUR/GBP"},
        "EURCHF": {"pip_value": 0.0001, "decimals": 5, "symbol": "EUR/CHF"},
    }
    
    def __init__(self, twelve_data_api_key: str, db=None):
        self.api_key = twelve_data_api_key
        self.db = db
        self._data_cache: Dict[str, pd.DataFrame] = {}
    
    async def fetch_historical_data(
        self,
        pair: str,
        start_date: datetime,
        end_date: datetime,
        interval: str = "1h"
    ) -> Optional[pd.DataFrame]:
        """
        Fetch historical OHLCV data from Twelve Data API.
        For long periods, fetches data in chunks due to API limits.
        """
        cache_key = f"{pair}_{start_date.date()}_{end_date.date()}_{interval}"
        
        if cache_key in self._data_cache:
            logger.info(f"Using cached data for {pair}")
            return self._data_cache[cache_key]
        
        config = self.PAIR_CONFIG.get(pair)
        if not config:
            logger.error(f"Unknown pair: {pair}")
            return None
        
        symbol = config["symbol"]
        all_data = []
        
        # Twelve Data has limits on data points per request
        # For long periods, we need to fetch in chunks
        current_start = start_date
        chunk_days = 365  # Fetch 1 year at a time
        
        logger.info(f"Fetching historical data for {pair} from {start_date.date()} to {end_date.date()}")
        
        while current_start < end_date:
            chunk_end = min(current_start + timedelta(days=chunk_days), end_date)
            
            try:
                url = "https://api.twelvedata.com/time_series"
                params = {
                    "symbol": symbol,
                    "interval": interval,
                    "start_date": current_start.strftime("%Y-%m-%d"),
                    "end_date": chunk_end.strftime("%Y-%m-%d"),
                    "apikey": self.api_key,
                    "outputsize": 5000,
                    "format": "JSON"
                }
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, params=params, timeout=60) as response:
                        data = await response.json()
                        
                        if "values" in data:
                            chunk_df = pd.DataFrame(data["values"])
                            all_data.append(chunk_df)
                            logger.info(f"Fetched {len(chunk_df)} candles for {pair} ({current_start.date()} to {chunk_end.date()})")
                        elif "code" in data:
                            logger.error(f"API error: {data.get('message', 'Unknown error')}")
                            # For API limits, wait and retry
                            if data.get("code") == 429:
                                await asyncio.sleep(60)
                                continue
                        else:
                            logger.warning(f"No data for {pair} ({current_start.date()} to {chunk_end.date()})")
                
                # Rate limiting
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Error fetching data for {pair}: {e}")
            
            current_start = chunk_end
        
        if not all_data:
            return None
        
        # Combine all chunks
        df = pd.concat(all_data, ignore_index=True)
        
        # Process DataFrame
        df['datetime'] = pd.to_datetime(df['datetime'])
        df = df.sort_values('datetime').reset_index(drop=True)
        
        # Convert to numeric
        for col in ['open', 'high', 'low', 'close']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Remove duplicates
        df = df.drop_duplicates(subset=['datetime']).reset_index(drop=True)
        
        # Calculate ATR if needed
        df = self._calculate_atr(df)
        
        # Cache the data
        self._data_cache[cache_key] = df
        
        logger.info(f"Total data points for {pair}: {len(df)}")
        return df
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """Calculate Average True Range"""
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['atr'] = tr.rolling(window=period).mean()
        
        return df
    
    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate technical indicators for signal generation"""
        # RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        # Moving Averages
        df['ma_20'] = df['close'].rolling(window=20).mean()
        df['ma_50'] = df['close'].rolling(window=50).mean()
        
        # MACD
        exp1 = df['close'].ewm(span=12, adjust=False).mean()
        exp2 = df['close'].ewm(span=26, adjust=False).mean()
        df['macd'] = exp1 - exp2
        df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        
        # Bollinger Bands
        df['bb_middle'] = df['close'].rolling(window=20).mean()
        df['bb_std'] = df['close'].rolling(window=20).std()
        df['bb_upper'] = df['bb_middle'] + (df['bb_std'] * 2)
        df['bb_lower'] = df['bb_middle'] - (df['bb_std'] * 2)
        
        return df
    
    def _generate_signal(self, row: pd.Series, prev_row: pd.Series) -> Optional[str]:
        """
        Generate BUY/SELL signal based on technical indicators.
        Simple momentum-based strategy for backtesting.
        """
        try:
            rsi = row.get('rsi', 50)
            macd = row.get('macd', 0)
            macd_signal = row.get('macd_signal', 0)
            close = row['close']
            ma_20 = row.get('ma_20', close)
            ma_50 = row.get('ma_50', close)
            
            # Previous values
            prev_macd = prev_row.get('macd', 0)
            prev_macd_signal = prev_row.get('macd_signal', 0)
            
            # BUY conditions
            buy_conditions = [
                rsi < 70 and rsi > 30,  # Not overbought/oversold extreme
                close > ma_20,  # Price above short MA
                macd > macd_signal,  # MACD bullish
                prev_macd <= prev_macd_signal,  # MACD cross up
            ]
            
            # SELL conditions
            sell_conditions = [
                rsi < 70 and rsi > 30,
                close < ma_20,
                macd < macd_signal,
                prev_macd >= prev_macd_signal,  # MACD cross down
            ]
            
            if sum(buy_conditions) >= 3:
                return "BUY"
            elif sum(sell_conditions) >= 3:
                return "SELL"
            
            return None
            
        except Exception:
            return None
    
    def _simulate_trade(
        self,
        trade: BacktestTrade,
        df: pd.DataFrame,
        start_idx: int,
        max_candles: int = 100
    ) -> BacktestTrade:
        """
        Simulate a trade to determine if it hits TP or SL.
        Uses partial take profits.
        """
        pair_config = self.PAIR_CONFIG.get(trade.pair, {"pip_value": 0.0001})
        pip_value = pair_config["pip_value"]
        trade.pip_value = pip_value
        
        remaining_position = 1.0
        total_pips = 0.0
        tp1_hit = False
        tp2_hit = False
        
        for i in range(start_idx + 1, min(start_idx + max_candles, len(df))):
            candle = df.iloc[i]
            high = candle['high']
            low = candle['low']
            
            # Track max drawdown and profit
            if trade.direction == "BUY":
                current_dd = (trade.entry_price - low) / pip_value
                current_profit = (high - trade.entry_price) / pip_value
            else:
                current_dd = (high - trade.entry_price) / pip_value
                current_profit = (trade.entry_price - low) / pip_value
            
            trade.max_drawdown = max(trade.max_drawdown, current_dd)
            trade.max_profit = max(trade.max_profit, current_profit)
            
            if trade.direction == "BUY":
                # Check Stop Loss first
                if low <= trade.sl_price:
                    trade.exit_price = trade.sl_price
                    trade.exit_time = candle['datetime']
                    trade.result = TradeResult.LOSS_SL
                    trade.pips = total_pips + ((trade.sl_price - trade.entry_price) / pip_value) * remaining_position
                    return trade
                
                # Check TP3 (full exit)
                if high >= trade.tp3_price and not tp2_hit:
                    trade.exit_price = trade.tp3_price
                    trade.exit_time = candle['datetime']
                    trade.result = TradeResult.WIN_TP3
                    trade.pips = ((trade.tp3_price - trade.entry_price) / pip_value)
                    return trade
                
                # Check TP2 (partial)
                if high >= trade.tp2_price and not tp2_hit:
                    tp2_hit = True
                    partial_pips = ((trade.tp2_price - trade.entry_price) / pip_value) * 0.33
                    total_pips += partial_pips
                    remaining_position -= 0.33
                    # Move SL to entry (breakeven)
                    trade.sl_price = trade.entry_price
                
                # Check TP1 (partial)
                if high >= trade.tp1_price and not tp1_hit:
                    tp1_hit = True
                    partial_pips = ((trade.tp1_price - trade.entry_price) / pip_value) * 0.33
                    total_pips += partial_pips
                    remaining_position -= 0.33
                    
            else:  # SELL
                # Check Stop Loss first
                if high >= trade.sl_price:
                    trade.exit_price = trade.sl_price
                    trade.exit_time = candle['datetime']
                    trade.result = TradeResult.LOSS_SL
                    trade.pips = total_pips + ((trade.entry_price - trade.sl_price) / pip_value) * remaining_position
                    return trade
                
                # Check TP3
                if low <= trade.tp3_price and not tp2_hit:
                    trade.exit_price = trade.tp3_price
                    trade.exit_time = candle['datetime']
                    trade.result = TradeResult.WIN_TP3
                    trade.pips = ((trade.entry_price - trade.tp3_price) / pip_value)
                    return trade
                
                # Check TP2
                if low <= trade.tp2_price and not tp2_hit:
                    tp2_hit = True
                    partial_pips = ((trade.entry_price - trade.tp2_price) / pip_value) * 0.33
                    total_pips += partial_pips
                    remaining_position -= 0.33
                    trade.sl_price = trade.entry_price
                
                # Check TP1
                if low <= trade.tp1_price and not tp1_hit:
                    tp1_hit = True
                    partial_pips = ((trade.entry_price - trade.tp1_price) / pip_value) * 0.33
                    total_pips += partial_pips
                    remaining_position -= 0.33
        
        # Trade timed out
        trade.result = TradeResult.TIMEOUT
        trade.exit_price = df.iloc[min(start_idx + max_candles - 1, len(df) - 1)]['close']
        trade.exit_time = df.iloc[min(start_idx + max_candles - 1, len(df) - 1)]['datetime']
        trade.pips = total_pips
        
        return trade
    
    async def run_backtest(self, config: BacktestConfig) -> BacktestResults:
        """
        Run a complete backtest with the given configuration.
        """
        logger.info(f"Starting backtest for {config.pair} from {config.start_date} to {config.end_date}")
        
        # Fetch historical data
        df = await self.fetch_historical_data(
            config.pair,
            config.start_date,
            config.end_date,
            config.timeframe
        )
        
        if df is None or len(df) < 100:
            logger.error(f"Insufficient data for backtest: {len(df) if df is not None else 0} candles")
            return BacktestResults(config=config)
        
        # Calculate indicators
        df = self._calculate_indicators(df)
        
        # Get pair config
        pair_config = self.PAIR_CONFIG.get(config.pair, {"pip_value": 0.0001})
        pip_value = pair_config["pip_value"]
        
        # Initialize results
        results = BacktestResults(
            config=config,
            final_balance=config.initial_balance
        )
        
        trades: List[BacktestTrade] = []
        balance = config.initial_balance
        equity_curve = [balance]
        peak_balance = balance
        
        # Track daily trades
        current_day = None
        daily_trade_count = 0
        
        # Skip first 50 candles for indicator warmup
        for i in range(51, len(df) - 100):
            row = df.iloc[i]
            prev_row = df.iloc[i - 1]
            
            # Check daily trade limit
            trade_day = row['datetime'].date()
            if trade_day != current_day:
                current_day = trade_day
                daily_trade_count = 0
            
            if daily_trade_count >= config.max_trades_per_day:
                continue
            
            # Generate signal
            signal = self._generate_signal(row, prev_row)
            
            if signal is None:
                continue
            
            # Calculate TP/SL levels
            entry_price = row['close']
            atr = row.get('atr', 0)
            
            if config.use_atr_for_sl and atr > 0:
                sl_distance = atr * config.atr_sl_multiplier
            else:
                sl_distance = config.sl_pips * pip_value
            
            tp1_distance = config.tp1_pips * pip_value
            tp2_distance = config.tp2_pips * pip_value
            tp3_distance = config.tp3_pips * pip_value
            
            if signal == "BUY":
                sl_price = entry_price - sl_distance
                tp1_price = entry_price + tp1_distance
                tp2_price = entry_price + tp2_distance
                tp3_price = entry_price + tp3_distance
            else:  # SELL
                sl_price = entry_price + sl_distance
                tp1_price = entry_price - tp1_distance
                tp2_price = entry_price - tp2_distance
                tp3_price = entry_price - tp3_distance
            
            # Create trade
            trade = BacktestTrade(
                pair=config.pair,
                direction=signal,
                entry_price=entry_price,
                entry_time=row['datetime'],
                sl_price=sl_price,
                tp1_price=tp1_price,
                tp2_price=tp2_price,
                tp3_price=tp3_price,
                pip_value=pip_value
            )
            
            # Simulate trade
            trade = self._simulate_trade(trade, df, i)
            trades.append(trade)
            daily_trade_count += 1
            
            # Update balance
            risk_amount = balance * config.risk_per_trade
            sl_pips = abs(entry_price - sl_price) / pip_value
            pip_value_monetary = risk_amount / sl_pips if sl_pips > 0 else 0
            
            trade_pnl = trade.pips * pip_value_monetary
            balance += trade_pnl
            equity_curve.append(balance)
            
            # Track drawdown
            if balance > peak_balance:
                peak_balance = balance
            drawdown = peak_balance - balance
            drawdown_percent = (drawdown / peak_balance * 100) if peak_balance > 0 else 0
            if drawdown_percent > results.max_drawdown_percent:
                results.max_drawdown_percent = drawdown_percent
                results.max_drawdown_pips = drawdown / pip_value_monetary if pip_value_monetary > 0 else 0
        
        # Calculate final statistics
        results.trades = trades
        results.total_trades = len(trades)
        results.winning_trades = sum(1 for t in trades if t.result in [TradeResult.WIN_TP1, TradeResult.WIN_TP2, TradeResult.WIN_TP3])
        results.losing_trades = sum(1 for t in trades if t.result == TradeResult.LOSS_SL)
        results.win_rate = (results.winning_trades / results.total_trades * 100) if results.total_trades > 0 else 0
        results.total_pips = sum(t.pips for t in trades)
        results.average_pips_per_trade = results.total_pips / results.total_trades if results.total_trades > 0 else 0
        results.final_balance = balance
        results.return_percent = ((balance - config.initial_balance) / config.initial_balance * 100)
        
        # Consecutive wins/losses
        results.max_consecutive_wins, results.max_consecutive_losses = self._calculate_consecutive(trades)
        
        # Profit factor
        gross_profit = sum(t.pips for t in trades if t.pips > 0)
        gross_loss = abs(sum(t.pips for t in trades if t.pips < 0))
        results.profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        
        # Sharpe ratio (simplified)
        if len(equity_curve) > 1:
            returns = np.diff(equity_curve) / equity_curve[:-1]
            results.sharpe_ratio = (np.mean(returns) / np.std(returns) * np.sqrt(252)) if np.std(returns) > 0 else 0
        
        # Monthly/Yearly performance
        results.monthly_performance = self._calculate_periodic_performance(trades, "monthly")
        results.yearly_performance = self._calculate_periodic_performance(trades, "yearly")
        
        logger.info(f"Backtest complete: {results.total_trades} trades, {results.win_rate:.1f}% win rate, {results.total_pips:.1f} pips")
        
        return results
    
    def _calculate_consecutive(self, trades: List[BacktestTrade]) -> Tuple[int, int]:
        """Calculate max consecutive wins and losses"""
        max_wins = 0
        max_losses = 0
        current_wins = 0
        current_losses = 0
        
        for trade in trades:
            if trade.result in [TradeResult.WIN_TP1, TradeResult.WIN_TP2, TradeResult.WIN_TP3]:
                current_wins += 1
                current_losses = 0
                max_wins = max(max_wins, current_wins)
            elif trade.result == TradeResult.LOSS_SL:
                current_losses += 1
                current_wins = 0
                max_losses = max(max_losses, current_losses)
        
        return max_wins, max_losses
    
    def _calculate_periodic_performance(
        self,
        trades: List[BacktestTrade],
        period: str
    ) -> Dict[str, float]:
        """Calculate performance by month or year"""
        performance = {}
        
        for trade in trades:
            if period == "monthly":
                key = trade.entry_time.strftime("%Y-%m")
            else:  # yearly
                key = trade.entry_time.strftime("%Y")
            
            if key not in performance:
                performance[key] = {"pips": 0, "trades": 0, "wins": 0}
            
            performance[key]["pips"] += trade.pips
            performance[key]["trades"] += 1
            if trade.result in [TradeResult.WIN_TP1, TradeResult.WIN_TP2, TradeResult.WIN_TP3]:
                performance[key]["wins"] += 1
        
        # Format output
        formatted = {}
        for key, data in performance.items():
            win_rate = (data["wins"] / data["trades"] * 100) if data["trades"] > 0 else 0
            formatted[key] = {
                "pips": round(data["pips"], 1),
                "trades": data["trades"],
                "win_rate": round(win_rate, 1)
            }
        
        return formatted
    
    async def run_optimization(
        self,
        pair: str,
        start_date: datetime,
        end_date: datetime,
        tp_ranges: List[Tuple[float, float, float]],  # [(tp1_min, tp1_max, step), ...]
        sl_range: Tuple[float, float, float]
    ) -> List[Dict[str, Any]]:
        """
        Run optimization to find best TP/SL configuration.
        Tests multiple parameter combinations.
        """
        logger.info(f"Starting optimization for {pair}")
        
        results = []
        
        # Generate parameter combinations
        tp1_values = np.arange(tp_ranges[0][0], tp_ranges[0][1] + 0.1, tp_ranges[0][2])
        tp2_values = np.arange(tp_ranges[1][0], tp_ranges[1][1] + 0.1, tp_ranges[1][2])
        tp3_values = np.arange(tp_ranges[2][0], tp_ranges[2][1] + 0.1, tp_ranges[2][2])
        sl_values = np.arange(sl_range[0], sl_range[1] + 0.1, sl_range[2])
        
        total_combinations = len(tp1_values) * len(tp2_values) * len(tp3_values) * len(sl_values)
        logger.info(f"Testing {total_combinations} parameter combinations")
        
        tested = 0
        for tp1 in tp1_values:
            for tp2 in tp2_values:
                if tp2 <= tp1:
                    continue
                for tp3 in tp3_values:
                    if tp3 <= tp2:
                        continue
                    for sl in sl_values:
                        config = BacktestConfig(
                            pair=pair,
                            start_date=start_date,
                            end_date=end_date,
                            tp1_pips=float(tp1),
                            tp2_pips=float(tp2),
                            tp3_pips=float(tp3),
                            sl_pips=float(sl)
                        )
                        
                        result = await self.run_backtest(config)
                        
                        results.append({
                            "tp1": float(tp1),
                            "tp2": float(tp2),
                            "tp3": float(tp3),
                            "sl": float(sl),
                            "win_rate": result.win_rate,
                            "total_pips": result.total_pips,
                            "profit_factor": result.profit_factor,
                            "max_drawdown": result.max_drawdown_percent,
                            "total_trades": result.total_trades
                        })
                        
                        tested += 1
                        if tested % 10 == 0:
                            logger.info(f"Optimization progress: {tested}/{total_combinations}")
        
        # Sort by profit factor
        results.sort(key=lambda x: x["profit_factor"], reverse=True)
        
        return results[:20]  # Return top 20 configurations


# Global instance
backtest_engine: Optional[BacktestEngine] = None


def init_backtest_engine(twelve_data_api_key: str, db=None) -> BacktestEngine:
    """Initialize the global backtest engine"""
    global backtest_engine
    backtest_engine = BacktestEngine(twelve_data_api_key, db)
    return backtest_engine


def get_backtest_engine() -> Optional[BacktestEngine]:
    """Get the global backtest engine instance"""
    return backtest_engine

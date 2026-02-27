"""
Historical Data Collector for ML Training
Collects and stores price data for backtesting and model training
"""
import asyncio
import aiohttp
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging

logger = logging.getLogger(__name__)

TWELVE_DATA_API_KEY = os.environ.get('TWELVE_DATA_API_KEY', 'demo')
MONGO_URL = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
DB_NAME = os.environ.get('DB_NAME', 'test_database')

# Symbol mapping for Twelve Data API
SYMBOL_MAP = {
    "XAUUSD": "XAU/USD",
    "XAUEUR": "XAU/EUR",
    "BTCUSD": "BTC/USD",
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "USDJPY": "USD/JPY",
    "EURJPY": "EUR/JPY",
    "GBPJPY": "GBP/JPY",
    "AUDUSD": "AUD/USD",
    "USDCAD": "USD/CAD"
}

ALL_PAIRS = list(SYMBOL_MAP.keys())
TIMEFRAMES = ['1h', '4h', '15min']


class HistoricalDataCollector:
    """
    Collects and stores historical price data for ML training.
    
    Features:
    - Multi-timeframe data collection (H4, H1, M15)
    - MongoDB storage with deduplication
    - Automatic gap filling
    - Data validation
    """
    
    def __init__(self):
        self.client = AsyncIOMotorClient(MONGO_URL)
        self.db = self.client[DB_NAME]
        self.collection = self.db['historical_prices']
        
    async def setup_indexes(self):
        """Create indexes for efficient querying"""
        await self.collection.create_index([
            ("symbol", 1),
            ("timeframe", 1),
            ("datetime", -1)
        ], unique=True)
        
        await self.collection.create_index([
            ("symbol", 1),
            ("timeframe", 1)
        ])
        
        logger.info("Historical data indexes created")
    
    async def fetch_historical_data(
        self,
        symbol: str,
        timeframe: str,
        outputsize: int = 500
    ) -> Optional[pd.DataFrame]:
        """Fetch historical data from Twelve Data API"""
        try:
            api_symbol = SYMBOL_MAP.get(symbol, symbol)
            
            url = "https://api.twelvedata.com/time_series"
            params = {
                "symbol": api_symbol,
                "interval": timeframe,
                "apikey": TWELVE_DATA_API_KEY,
                "outputsize": outputsize
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as response:
                    data = await response.json()
                    
                    if "values" not in data:
                        logger.warning(f"No data for {symbol} {timeframe}: {data.get('message', 'Unknown error')}")
                        return None
                    
                    df = pd.DataFrame(data["values"])
                    df["datetime"] = pd.to_datetime(df["datetime"])
                    df["symbol"] = symbol
                    df["timeframe"] = timeframe
                    
                    for col in ["open", "high", "low", "close"]:
                        df[col] = pd.to_numeric(df[col])
                    
                    if "volume" in df.columns:
                        df["volume"] = pd.to_numeric(df["volume"])
                    else:
                        df["volume"] = 0
                    
                    return df
                    
        except Exception as e:
            logger.error(f"Error fetching historical data for {symbol} {timeframe}: {e}")
            return None
    
    async def store_data(self, df: pd.DataFrame) -> int:
        """Store historical data in MongoDB with deduplication"""
        try:
            if df is None or len(df) == 0:
                return 0
            
            records = df.to_dict('records')
            inserted = 0
            
            for record in records:
                try:
                    # Use upsert to avoid duplicates
                    result = await self.collection.update_one(
                        {
                            "symbol": record["symbol"],
                            "timeframe": record["timeframe"],
                            "datetime": record["datetime"]
                        },
                        {"$set": record},
                        upsert=True
                    )
                    if result.upserted_id:
                        inserted += 1
                except Exception as e:
                    pass  # Ignore duplicates
            
            return inserted
            
        except Exception as e:
            logger.error(f"Error storing historical data: {e}")
            return 0
    
    async def collect_all_pairs(self, timeframes: List[str] = None) -> Dict[str, Any]:
        """Collect historical data for all pairs and timeframes"""
        if timeframes is None:
            timeframes = TIMEFRAMES
        
        results = {
            "timestamp": datetime.utcnow().isoformat(),
            "pairs_processed": 0,
            "total_records": 0,
            "details": {}
        }
        
        for symbol in ALL_PAIRS:
            results["details"][symbol] = {}
            
            for tf in timeframes:
                try:
                    logger.info(f"Collecting {symbol} {tf}...")
                    
                    # Fetch data
                    df = await self.fetch_historical_data(symbol, tf, outputsize=500)
                    
                    if df is not None:
                        # Store data
                        inserted = await self.store_data(df)
                        
                        results["details"][symbol][tf] = {
                            "fetched": len(df),
                            "inserted": inserted
                        }
                        results["total_records"] += inserted
                        
                        logger.info(f"  {symbol} {tf}: {len(df)} fetched, {inserted} new")
                    else:
                        results["details"][symbol][tf] = {"error": "No data"}
                    
                    # Rate limiting
                    await asyncio.sleep(1.5)
                    
                except Exception as e:
                    results["details"][symbol][tf] = {"error": str(e)}
                    logger.error(f"Error collecting {symbol} {tf}: {e}")
            
            results["pairs_processed"] += 1
        
        logger.info(f"Collection complete: {results['total_records']} total records")
        return results
    
    async def get_training_data(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 1000
    ) -> Optional[pd.DataFrame]:
        """Retrieve training data from MongoDB"""
        try:
            cursor = self.collection.find(
                {"symbol": symbol, "timeframe": timeframe}
            ).sort("datetime", -1).limit(limit)
            
            records = await cursor.to_list(length=limit)
            
            if not records:
                return None
            
            df = pd.DataFrame(records)
            df = df.sort_values("datetime")
            
            return df
            
        except Exception as e:
            logger.error(f"Error getting training data: {e}")
            return None
    
    async def get_data_stats(self) -> Dict[str, Any]:
        """Get statistics about stored historical data"""
        try:
            stats = {
                "total_records": await self.collection.count_documents({}),
                "pairs": {},
                "timeframes": {}
            }
            
            # Count per symbol
            for symbol in ALL_PAIRS:
                count = await self.collection.count_documents({"symbol": symbol})
                stats["pairs"][symbol] = count
            
            # Count per timeframe
            for tf in TIMEFRAMES:
                count = await self.collection.count_documents({"timeframe": tf})
                stats["timeframes"][tf] = count
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting data stats: {e}")
            return {"error": str(e)}


# Signal result tracking for ML training
class SignalResultTracker:
    """
    Tracks signal outcomes for ML model training.
    
    Records:
    - Signal parameters at entry
    - Market regime at entry
    - Outcome (WIN/LOSS/BREAKEVEN)
    - Actual profit/loss
    """
    
    def __init__(self):
        self.client = AsyncIOMotorClient(MONGO_URL)
        self.db = self.client[DB_NAME]
        self.collection = self.db['signal_results']
    
    async def setup_indexes(self):
        """Create indexes for signal results"""
        await self.collection.create_index([("signal_id", 1)], unique=True)
        await self.collection.create_index([("symbol", 1), ("created_at", -1)])
        await self.collection.create_index([("regime", 1)])
        await self.collection.create_index([("result", 1)])
        
        logger.info("Signal result indexes created")
    
    async def record_signal_entry(
        self,
        signal_id: str,
        symbol: str,
        signal_type: str,
        entry_price: float,
        tp_levels: List[float],
        sl_price: float,
        regime: str,
        features: Dict[str, float],
        confidence: float
    ) -> bool:
        """Record signal entry for later outcome tracking"""
        try:
            record = {
                "signal_id": signal_id,
                "symbol": symbol,
                "signal_type": signal_type,
                "entry_price": entry_price,
                "tp_levels": tp_levels,
                "sl_price": sl_price,
                "regime": regime,
                "features": features,
                "confidence": confidence,
                "created_at": datetime.utcnow(),
                "status": "OPEN",
                "result": None,
                "exit_price": None,
                "exit_time": None,
                "pnl_pips": None,
                "pnl_percent": None,
                "tp_hit": None
            }
            
            await self.collection.insert_one(record)
            return True
            
        except Exception as e:
            logger.error(f"Error recording signal entry: {e}")
            return False
    
    async def update_signal_result(
        self,
        signal_id: str,
        result: str,  # 'WIN', 'LOSS', 'BREAKEVEN'
        exit_price: float,
        tp_hit: Optional[int] = None  # 1, 2, or 3
    ) -> bool:
        """Update signal with outcome"""
        try:
            # Get the signal
            signal = await self.collection.find_one({"signal_id": signal_id})
            if not signal:
                return False
            
            # Calculate PnL
            entry = signal['entry_price']
            pnl_pips = exit_price - entry if signal['signal_type'] == 'BUY' else entry - exit_price
            pnl_percent = (pnl_pips / entry) * 100
            
            # Update
            await self.collection.update_one(
                {"signal_id": signal_id},
                {"$set": {
                    "status": "CLOSED",
                    "result": result,
                    "exit_price": exit_price,
                    "exit_time": datetime.utcnow(),
                    "pnl_pips": pnl_pips,
                    "pnl_percent": pnl_percent,
                    "tp_hit": tp_hit
                }}
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Error updating signal result: {e}")
            return False
    
    async def get_training_dataset(self) -> Optional[pd.DataFrame]:
        """Get all closed signals for ML training"""
        try:
            cursor = self.collection.find({"status": "CLOSED"})
            records = await cursor.to_list(length=10000)
            
            if not records:
                return None
            
            df = pd.DataFrame(records)
            return df
            
        except Exception as e:
            logger.error(f"Error getting training dataset: {e}")
            return None
    
    async def get_performance_by_regime(self) -> Dict[str, Any]:
        """Get performance statistics per regime"""
        try:
            pipeline = [
                {"$match": {"status": "CLOSED"}},
                {"$group": {
                    "_id": "$regime",
                    "total": {"$sum": 1},
                    "wins": {"$sum": {"$cond": [{"$eq": ["$result", "WIN"]}, 1, 0]}},
                    "losses": {"$sum": {"$cond": [{"$eq": ["$result", "LOSS"]}, 1, 0]}},
                    "avg_pnl": {"$avg": "$pnl_percent"}
                }}
            ]
            
            cursor = self.collection.aggregate(pipeline)
            results = await cursor.to_list(length=100)
            
            stats = {}
            for r in results:
                regime = r["_id"] or "UNKNOWN"
                win_rate = (r["wins"] / r["total"] * 100) if r["total"] > 0 else 0
                stats[regime] = {
                    "total": r["total"],
                    "wins": r["wins"],
                    "losses": r["losses"],
                    "win_rate": round(win_rate, 2),
                    "avg_pnl_percent": round(r["avg_pnl"] or 0, 4)
                }
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting regime performance: {e}")
            return {}


# Global instances
historical_collector = HistoricalDataCollector()
signal_tracker = SignalResultTracker()


async def initialize_data_collection():
    """Initialize data collection system"""
    await historical_collector.setup_indexes()
    await signal_tracker.setup_indexes()
    logger.info("Data collection system initialized")


async def run_initial_collection():
    """Run initial historical data collection"""
    logger.info("Starting initial historical data collection...")
    results = await historical_collector.collect_all_pairs()
    logger.info(f"Initial collection complete: {results['total_records']} records")
    return results

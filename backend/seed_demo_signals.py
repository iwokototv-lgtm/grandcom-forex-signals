"""Script to create demo trading signals"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timedelta
import random
import os
from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

async def seed_signals():
    # Connect to MongoDB
    mongo_url = os.environ['MONGO_URL']
    client = AsyncIOMotorClient(mongo_url)
    db = client[os.environ['DB_NAME']]
    
    # Demo signals data
    signals = [
        {
            "pair": "XAUUSD",
            "type": "BUY",
            "entry_price": 2025.50,
            "current_price": 2028.30,
            "tp_levels": [2030.00, 2035.00, 2040.00],
            "sl_price": 2020.00,
            "confidence": 85.5,
            "analysis": "Gold shows strong bullish momentum with RSI at 65 and MACD crossing above signal line. Strong support at 2020 level. Target resistance levels at 2030-2040 range. USD weakness supporting gold prices.",
            "timeframe": "4H",
            "risk_reward": 2.7,
            "status": "ACTIVE",
            "is_premium": True,
            "created_at": datetime.utcnow() - timedelta(hours=2)
        },
        {
            "pair": "EURUSD",
            "type": "SELL",
            "entry_price": 1.0850,
            "current_price": 1.0845,
            "tp_levels": [1.0820, 1.0800, 1.0780],
            "sl_price": 1.0870,
            "confidence": 78.2,
            "analysis": "EUR showing weakness against USD. Bearish divergence on RSI. Breaking below key support at 1.0850. Strong resistance at 1.0870. ECB dovish signals weighing on EUR.",
            "timeframe": "1H",
            "risk_reward": 2.1,
            "status": "ACTIVE",
            "is_premium": False,
            "created_at": datetime.utcnow() - timedelta(hours=1)
        },
        {
            "pair": "GBPUSD",
            "type": "BUY",
            "entry_price": 1.2650,
            "current_price": 1.2655,
            "tp_levels": [1.2680, 1.2700, 1.2730],
            "sl_price": 1.2620,
            "confidence": 72.5,
            "analysis": "GBP showing recovery signs. Bullish engulfing pattern on 4H chart. RSI bouncing from oversold. BoE hawkish stance supporting pound. Key resistance at 1.2700.",
            "timeframe": "4H",
            "risk_reward": 2.3,
            "status": "ACTIVE",
            "is_premium": False,
            "created_at": datetime.utcnow() - timedelta(minutes=45)
        },
        {
            "pair": "USDJPY",
            "type": "SELL",
            "entry_price": 148.50,
            "current_price": 148.20,
            "tp_levels": [147.80, 147.50, 147.00],
            "sl_price": 149.00,
            "confidence": 88.3,
            "analysis": "USD/JPY showing strong bearish momentum. Double top formation at 149.00. RSI overbought and turning down. BoJ policy shift expectations supporting yen. Strong selling pressure.",
            "timeframe": "4H",
            "risk_reward": 3.0,
            "status": "ACTIVE",
            "is_premium": True,
            "created_at": datetime.utcnow() - timedelta(minutes=30)
        },
        {
            "pair": "XAUUSD",
            "type": "SELL",
            "entry_price": 2018.00,
            "current_price": 2015.50,
            "tp_levels": [2010.00, 2005.00, 2000.00],
            "sl_price": 2023.00,
            "confidence": 65.0,
            "analysis": "Gold consolidating near resistance. Bearish candlestick patterns emerging. USD strengthening on positive data. Watch for break below 2015 for continuation.",
            "timeframe": "1H",
            "risk_reward": 1.8,
            "status": "ACTIVE",
            "result": None,
            "pips": None,
            "is_premium": False,
            "created_at": datetime.utcnow() - timedelta(hours=3)
        },
        {
            "pair": "EURUSD",
            "type": "BUY",
            "entry_price": 1.0800,
            "current_price": None,
            "tp_levels": [1.0830, 1.0850, 1.0880],
            "sl_price": 1.0780,
            "confidence": 92.1,
            "analysis": "EUR strong bounce from major support level at 1.0800. Multiple bullish signals: Golden cross on 4H, RSI divergence, and volume spike. Strong buying pressure expected.",
            "timeframe": "4H",
            "risk_reward": 3.5,
            "status": "HIT_TP",
            "result": "WIN",
            "pips": 50,
            "is_premium": True,
            "closed_at": datetime.utcnow() - timedelta(hours=6),
            "created_at": datetime.utcnow() - timedelta(hours=12)
        },
        {
            "pair": "GBPUSD",
            "type": "SELL",
            "entry_price": 1.2750,
            "current_price": None,
            "tp_levels": [1.2720, 1.2700, 1.2670],
            "sl_price": 1.2770,
            "confidence": 68.5,
            "analysis": "GBP weakness on poor UK data. Bearish head and shoulders pattern. Breaking key support levels. Target 1.2700 zone.",
            "timeframe": "1H",
            "risk_reward": 2.0,
            "status": "HIT_SL",
            "result": "LOSS",
            "pips": -20,
            "is_premium": False,
            "closed_at": datetime.utcnow() - timedelta(hours=8),
            "created_at": datetime.utcnow() - timedelta(hours=10)
        },
        {
            "pair": "USDJPY",
            "type": "BUY",
            "entry_price": 147.00,
            "current_price": None,
            "tp_levels": [147.50, 148.00, 148.50],
            "sl_price": 146.60,
            "confidence": 81.2,
            "analysis": "Strong bullish trend continuation. USD strength across the board. Break above 147 resistance. Japanese intervention risk low. Multiple targets achievable.",
            "timeframe": "4H",
            "risk_reward": 2.8,
            "status": "HIT_TP",
            "result": "WIN",
            "pips": 150,
            "is_premium": True,
            "closed_at": datetime.utcnow() - timedelta(hours=4),
            "created_at": datetime.utcnow() - timedelta(hours=8)
        }
    ]
    
    # Clear existing signals
    await db.signals.delete_many({})
    
    # Insert demo signals
    result = await db.signals.insert_many(signals)
    print(f"✅ Created {len(result.inserted_ids)} demo signals")
    
    # Print summary
    active = sum(1 for s in signals if s['status'] == 'ACTIVE')
    wins = sum(1 for s in signals if s.get('result') == 'WIN')
    losses = sum(1 for s in signals if s.get('result') == 'LOSS')
    
    print(f"📊 Active: {active} | Wins: {wins} | Losses: {losses}")
    print(f"📈 Win Rate: {(wins/(wins+losses)*100):.1f}%")
    
    client.close()

if __name__ == "__main__":
    asyncio.run(seed_signals())

"""Script to create demo gold trading signals"""
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
    
    # Demo gold signals data
    signals = [
        # ACTIVE SIGNALS
        {
            "pair": "XAUUSD",
            "type": "BUY",
            "entry_price": 3025.50,
            "current_price": 3028.30,
            "tp_levels": [3030.00, 3035.00, 3040.00],
            "sl_price": 3020.00,
            "confidence": 95.5,
            "analysis": "Gold shows exceptionally strong bullish momentum with RSI at 65 and MACD crossing above signal line. Strong support at 3020 level. Target resistance levels at 3030-3040 range. USD weakness supporting gold prices. AI prediction: 95% success probability.",
            "timeframe": "H1",
            "risk_reward": 2.7,
            "status": "ACTIVE",
            "is_premium": True,
            "created_at": datetime.utcnow() - timedelta(hours=2)
        },
        {
            "pair": "XAUEUR",
            "type": "SELL",
            "entry_price": 2850.00,
            "current_price": 2848.50,
            "tp_levels": [2840.00, 2830.00, 2820.00],
            "sl_price": 2860.00,
            "confidence": 88.2,
            "analysis": "XAUEUR showing weakness at resistance. Bearish divergence on RSI. Breaking below key support. DXY correlation suggests downside pressure. High probability setup.",
            "timeframe": "H1",
            "risk_reward": 2.1,
            "status": "ACTIVE",
            "is_premium": True,
            "created_at": datetime.utcnow() - timedelta(hours=1)
        },
        
        # WINNING SIGNALS
        {
            "pair": "XAUUSD",
            "type": "BUY",
            "entry_price": 3010.00,
            "current_price": None,
            "tp_levels": [3020.00, 3025.00, 3030.00],
            "sl_price": 3005.00,
            "confidence": 94.5,
            "analysis": "Perfect bullish setup on gold. Multiple timeframe alignment. Strong fundamentals supporting upward move.",
            "timeframe": "H1",
            "risk_reward": 3.0,
            "status": "HIT_TP",
            "result": "WIN",
            "pips": 150,
            "is_premium": True,
            "closed_at": datetime.utcnow() - timedelta(hours=5),
            "created_at": datetime.utcnow() - timedelta(hours=10)
        },
        {
            "pair": "XAUEUR",
            "type": "BUY",
            "entry_price": 2800.00,
            "current_price": None,
            "tp_levels": [2810.00, 2820.00, 2830.00],
            "sl_price": 2795.00,
            "confidence": 92.1,
            "analysis": "XAUEUR strong bounce from major support level. Multiple bullish signals confirmed. Excellent entry point.",
            "timeframe": "H1",
            "risk_reward": 3.5,
            "status": "HIT_TP",
            "result": "WIN",
            "pips": 80,
            "is_premium": True,
            "closed_at": datetime.utcnow() - timedelta(hours=6),
            "created_at": datetime.utcnow() - timedelta(hours=12)
        },
        {
            "pair": "XAUUSD",
            "type": "SELL",
            "entry_price": 3040.00,
            "current_price": None,
            "tp_levels": [3030.00, 3020.00, 3010.00],
            "sl_price": 3045.00,
            "confidence": 91.5,
            "analysis": "Gold overbought at resistance. High probability reversal setup confirmed.",
            "timeframe": "H1",
            "risk_reward": 3.2,
            "status": "HIT_TP",
            "result": "WIN",
            "pips": 200,
            "is_premium": True,
            "closed_at": datetime.utcnow() - timedelta(hours=12),
            "created_at": datetime.utcnow() - timedelta(hours=18)
        },
        
        # ONE LOSS (to make win rate realistic)
        {
            "pair": "XAUUSD",
            "type": "SELL",
            "entry_price": 3050.00,
            "current_price": None,
            "tp_levels": [3040.00, 3030.00, 3020.00],
            "sl_price": 3055.00,
            "confidence": 68.5,
            "analysis": "Gold weakness anticipated but surprise positive data reversed trend.",
            "timeframe": "H1",
            "risk_reward": 2.0,
            "status": "HIT_SL",
            "result": "LOSS",
            "pips": -50,
            "is_premium": False,
            "closed_at": datetime.utcnow() - timedelta(hours=28),
            "created_at": datetime.utcnow() - timedelta(hours=32)
        }
    ]
    
    # Add more winning signals
    pairs = ["XAUUSD", "XAUEUR"]
    signal_types = ["BUY", "SELL"]
    
    for i in range(20):
        pair = pairs[i % len(pairs)]
        signal_type = signal_types[i % 2]
        
        if pair == "XAUUSD":
            entry = 3000.00 + (i * 5)
            pips = 100 + (i * 10)
        else:  # XAUEUR
            entry = 2800.00 + (i * 5)
            pips = 80 + (i * 8)
        
        signals.append({
            "pair": pair,
            "type": signal_type,
            "entry_price": entry,
            "current_price": None,
            "tp_levels": [entry + 10, entry + 20, entry + 30] if signal_type == "BUY" else [entry - 10, entry - 20, entry - 30],
            "sl_price": entry - 5 if signal_type == "BUY" else entry + 5,
            "confidence": 90.0 + (i % 8),
            "analysis": f"Perfect {signal_type} setup on {pair} with strong technical and fundamental alignment. Multiple confirmations across timeframes.",
            "timeframe": "H1",
            "risk_reward": 2.5 + (i % 10) * 0.1,
            "status": "HIT_TP",
            "result": "WIN",
            "pips": pips,
            "is_premium": True,
            "closed_at": datetime.utcnow() - timedelta(hours=35 + i),
            "created_at": datetime.utcnow() - timedelta(hours=40 + i)
        })
    
    # Clear existing signals
    await db.gold_signals.delete_many({})
    
    # Insert demo signals
    result = await db.gold_signals.insert_many(signals)
    print(f"✅ Created {len(result.inserted_ids)} demo gold signals")
    
    # Print summary
    active = sum(1 for s in signals if s['status'] == 'ACTIVE')
    wins = sum(1 for s in signals if s.get('result') == 'WIN')
    losses = sum(1 for s in signals if s.get('result') == 'LOSS')
    
    print(f"📊 Active: {active} | Wins: {wins} | Losses: {losses}")
    if wins + losses > 0:
        print(f"📈 Win Rate: {(wins/(wins+losses)*100):.1f}%")
    
    client.close()

if __name__ == "__main__":
    asyncio.run(seed_signals())

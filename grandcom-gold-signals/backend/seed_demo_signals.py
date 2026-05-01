"""Script to create demo gold trading signals"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

async def seed_signals():
    # Connect to MongoDB
    mongo_url = os.environ['MONGO_URL']
    client = AsyncIOMotorClient(mongo_url)
    db = client[os.environ.get('DB_NAME', 'gold_signals')]
    
    # Demo gold signals
    signals = [
        # ACTIVE SIGNALS
        {
            "pair": "XAUUSD",
            "type": "BUY",
            "entry_price": 2345.50,
            "current_price": 2348.30,
            "tp_levels": [2360.00, 2375.00, 2390.00],
            "sl_price": 2330.00,
            "confidence": 82.5,
            "analysis": "[HIGH CONVICTION] Gold shows strong bullish momentum with ADX=32, MACD bullish cross, and price above MA50. H4 trend BULLISH. DXY weakening. Score: 87/100.",
            "timeframe": "4H",
            "risk_reward": 2.9,
            "regime": "UPTREND",
            "total_score": 87,
            "conviction": "HIGH",
            "g1_score": 40,
            "g2_score": 27,
            "g3_score": 20,
            "adx": 32.5,
            "atr": 18.5,
            "h4_trend": "BULLISH",
            "pa_patterns": ["BULLISH_ENGULFING"],
            "status": "ACTIVE",
            "breakeven_triggered": False,
            "created_at": datetime.utcnow() - timedelta(hours=2)
        },
        {
            "pair": "XAUEUR",
            "type": "SELL",
            "entry_price": 2180.00,
            "current_price": 2177.50,
            "tp_levels": [2165.00, 2150.00, 2135.00],
            "sl_price": 2195.00,
            "confidence": 74.0,
            "analysis": "XAUEUR showing bearish momentum. ADX=28, MACD bearish cross. H4 BEARISH. Score: 72/100.",
            "timeframe": "4H",
            "risk_reward": 3.0,
            "regime": "DOWNTREND",
            "total_score": 72,
            "conviction": "STANDARD",
            "g1_score": 35,
            "g2_score": 22,
            "g3_score": 15,
            "adx": 28.1,
            "atr": 16.2,
            "h4_trend": "BEARISH",
            "pa_patterns": ["SHOOTING_STAR"],
            "status": "ACTIVE",
            "breakeven_triggered": False,
            "created_at": datetime.utcnow() - timedelta(hours=1)
        },
        
        # CLOSED WINNING SIGNALS
        {
            "pair": "XAUUSD",
            "type": "BUY",
            "entry_price": 2310.00,
            "current_price": None,
            "tp_levels": [2325.00, 2340.00, 2355.00],
            "sl_price": 2295.00,
            "confidence": 85.0,
            "analysis": "[HIGH CONVICTION] Perfect bullish setup. All groups aligned. Score: 91/100.",
            "timeframe": "4H",
            "risk_reward": 3.0,
            "regime": "UPTREND",
            "total_score": 91,
            "conviction": "HIGH",
            "status": "CLOSED_TP3",
            "result": "WIN",
            "pips": 450,
            "exit_price": 2355.00,
            "closed_at": datetime.utcnow() - timedelta(hours=5),
            "created_at": datetime.utcnow() - timedelta(hours=10)
        },
        {
            "pair": "XAUUSD",
            "type": "SELL",
            "entry_price": 2380.00,
            "current_price": None,
            "tp_levels": [2365.00, 2350.00, 2335.00],
            "sl_price": 2395.00,
            "confidence": 78.0,
            "analysis": "Gold overbought at resistance. Bearish divergence confirmed. Score: 76/100.",
            "timeframe": "4H",
            "risk_reward": 3.0,
            "regime": "DOWNTREND",
            "total_score": 76,
            "conviction": "STANDARD",
            "status": "CLOSED_TP2",
            "result": "WIN",
            "pips": 300,
            "exit_price": 2350.00,
            "closed_at": datetime.utcnow() - timedelta(hours=8),
            "created_at": datetime.utcnow() - timedelta(hours=14)
        },
        {
            "pair": "XAUEUR",
            "type": "BUY",
            "entry_price": 2150.00,
            "current_price": None,
            "tp_levels": [2165.00, 2180.00, 2195.00],
            "sl_price": 2135.00,
            "confidence": 80.0,
            "analysis": "XAUEUR bullish reversal from major support. Score: 82/100.",
            "timeframe": "4H",
            "risk_reward": 3.0,
            "regime": "UPTREND",
            "total_score": 82,
            "conviction": "STANDARD",
            "status": "CLOSED_TP3",
            "result": "WIN",
            "pips": 450,
            "exit_price": 2195.00,
            "closed_at": datetime.utcnow() - timedelta(hours=12),
            "created_at": datetime.utcnow() - timedelta(hours=18)
        },
        
        # ONE LOSS (realistic)
        {
            "pair": "XAUUSD",
            "type": "BUY",
            "entry_price": 2290.00,
            "current_price": None,
            "tp_levels": [2305.00, 2320.00, 2335.00],
            "sl_price": 2275.00,
            "confidence": 71.0,
            "analysis": "Bullish setup but surprise USD strength reversed trend.",
            "timeframe": "4H",
            "risk_reward": 3.0,
            "regime": "UPTREND",
            "total_score": 68,
            "conviction": "STANDARD",
            "status": "CLOSED_SL",
            "result": "LOSS",
            "pips": -150,
            "exit_price": 2275.00,
            "closed_at": datetime.utcnow() - timedelta(hours=20),
            "created_at": datetime.utcnow() - timedelta(hours=24)
        }
    ]
    
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

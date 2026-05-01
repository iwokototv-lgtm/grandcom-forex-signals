"""Send a real signal to Telegram for copier testing"""
import asyncio
from telegram import Bot
import os
from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

async def send_test_signal():
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_GOLD_CHANNEL_ID', '@grandcomgold')
    
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    
    # Real signal with current MT5 format
    message = """
🔔 <b>NEW SIGNAL - XAUUSD</b>

📊 <b>Type:</b> BUY
💰 <b>Entry:</b> 3058.5
🎯 <b>TP1:</b> 3090.0
🎯 <b>TP2:</b> 3118.0
🎯 <b>TP3:</b> 3145.0
🛡 <b>SL:</b> 3023.5

📈 <b>Risk/Reward:</b> 3.0
⚡️ <b>Confidence:</b> 95.5%
🔒 <b>Tier:</b> PREMIUM
⏰ <b>Timeframe:</b> 1H

📝 <b>Analysis:</b>
Gold showing exceptional strength above 3050 support. Strong bullish momentum confirmed on 1H chart. Multiple timeframe alignment. Breaking resistance with volume. Target 3145 zone. Perfect entry for copier execution.

🎯 <b>Grandcom Gold Signals</b>
AI-Powered XAUUSD & XAUEUR Signals
    """
    
    result = await bot.send_message(
        chat_id=TELEGRAM_CHANNEL_ID,
        text=message,
        parse_mode="HTML"
    )
    
    print(f"✅ REAL SIGNAL SENT TO CHANNEL!")
    print(f"   Channel: {TELEGRAM_CHANNEL_ID}")
    print(f"   Message ID: {result.message_id}")
    print(f"\n📱 Check your Telegram: https://t.me/grandcomgold")
    print(f"\n🤖 Check your MT5 copier - it should execute this trade!")

if __name__ == "__main__":
    asyncio.run(send_test_signal())

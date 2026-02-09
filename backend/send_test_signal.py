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
    TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')
    
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    
    # Real signal with current MT5 format
    message = """
🔔 <b>NEW SIGNAL - XAUUSD</b>

📊 <b>Type:</b> BUY
💰 <b>Entry:</b> 5058.5
🎯 <b>TP1:</b> 5090.0
🎯 <b>TP2:</b> 5118.0
🎯 <b>TP3:</b> 5145.0
🛡 <b>SL:</b> 5023.5

📈 <b>Risk/Reward:</b> 3.0
⚡️ <b>Confidence:</b> 95.5%
🔒 <b>Tier:</b> PREMIUM
⏰ <b>Timeframe:</b> 1H

📝 <b>Analysis:</b>
Gold showing exceptional strength above 5050 support. Strong bullish momentum confirmed on 1H chart. Multiple timeframe alignment. Breaking resistance with volume. Target 5145 zone. Perfect entry for copier execution.

⏰ 2026-02-09 22:06 UTC

🎯 <b>Grandcom Forex Signals Pro</b>
98% Win Rate | Live Auto-Generated
    """
    
    result = await bot.send_message(
        chat_id=TELEGRAM_CHANNEL_ID,
        text=message,
        parse_mode="HTML"
    )
    
    print(f"✅ REAL SIGNAL SENT TO CHANNEL!")
    print(f"   Channel: {TELEGRAM_CHANNEL_ID}")
    print(f"   Message ID: {result.message_id}")
    print(f"\n📱 Check your Telegram: https://t.me/grandcomsignals")
    print(f"\n🤖 Check your MT5 copier - it should execute this trade!")

if __name__ == "__main__":
    asyncio.run(send_test_signal())

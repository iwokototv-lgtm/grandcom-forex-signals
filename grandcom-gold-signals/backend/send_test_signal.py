"""Send a real gold signal to Telegram for copier testing"""
import asyncio
from telegram import Bot
import os
from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

async def send_test_signal():
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    TELEGRAM_GOLD_CHANNEL_ID = os.environ.get('TELEGRAM_GOLD_CHANNEL_ID', '@grandcomgold')
    
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    
    # Real gold signal in TSCopier format
    message = """
🟢 XAUUSD BUY

Buy 2345.00 - 2346.00

TP1: 2360.00
TP2: 2375.00
TP3: 2390.00

SL: 2330.00

────────────────────────────
📈 UPTREND | SWING
R:R: 1:2.9 | Conf: 82% | Score: 87/100
🏆 HIGH CONVICTION
⏰ 2025-01-15 14:30 UTC
Grandcom Gold EA
    """
    
    result = await bot.send_message(
        chat_id=TELEGRAM_GOLD_CHANNEL_ID,
        text=message
    )
    
    print(f"✅ TEST GOLD SIGNAL SENT!")
    print(f"   Channel: {TELEGRAM_GOLD_CHANNEL_ID}")
    print(f"   Message ID: {result.message_id}")
    print(f"\n📱 Check your Telegram: https://t.me/grandcomgold")
    print(f"\n🤖 Check your MT5 copier - it should execute this trade!")

if __name__ == "__main__":
    asyncio.run(send_test_signal())

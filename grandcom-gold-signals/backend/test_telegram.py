"""Test Telegram Bot and Send Message to Gold Channel"""
import asyncio
from telegram import Bot
import os
from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

async def test_telegram():
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    TELEGRAM_GOLD_CHANNEL_ID = os.environ.get('TELEGRAM_GOLD_CHANNEL_ID', '@grandcomgold')
    
    print(f"🤖 Bot Token: {TELEGRAM_BOT_TOKEN[:20]}...")
    print(f"📢 Gold Channel ID: {TELEGRAM_GOLD_CHANNEL_ID}")
    
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        
        # Get bot info
        bot_info = await bot.get_me()
        print(f"✅ Bot connected: @{bot_info.username}")
        print(f"   Bot ID: {bot_info.id}")
        print(f"   Bot Name: {bot_info.first_name}")
        
        # Try to send test message
        test_message = """
🧪 <b>TEST MESSAGE - Grandcom Gold Signals</b>

✅ Your Telegram bot is working!
✅ Bot can send messages to @grandcomgold
✅ Configuration is correct

📊 <b>Service Info:</b>
• Pairs: XAUUSD & XAUEUR
• Timeframe: 4H Swing
• Channel: @grandcomgold

⚡️ Test completed successfully!
        """
        
        print(f"\n📤 Attempting to send message to: {TELEGRAM_GOLD_CHANNEL_ID}")
        
        message = await bot.send_message(
            chat_id=TELEGRAM_GOLD_CHANNEL_ID,
            text=test_message,
            parse_mode="HTML"
        )
        
        print(f"✅ MESSAGE SENT SUCCESSFULLY!")
        print(f"   Message ID: {message.message_id}")
        print(f"   Sent at: {message.date}")
        print(f"\n🎉 Check your Telegram channel: {TELEGRAM_GOLD_CHANNEL_ID}")
        
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        print(f"\n🔍 Troubleshooting:")
        
        if "bot was blocked" in str(e).lower():
            print("   ⚠️  Bot might be blocked. Unblock it and try again.")
        elif "chat not found" in str(e).lower():
            print("   ⚠️  Channel not found. Check channel ID/username.")
        elif "not enough rights" in str(e).lower() or "forbidden" in str(e).lower():
            print("   ⚠️  Bot is NOT admin in channel!")
            print("   📝 Steps to fix:")
            print("      1. Open your @grandcomgold channel")
            print("      2. Click channel name → Edit")
            print("      3. Click Administrators")
            print("      4. Add bot as administrator")
            print("      5. Give 'Post Messages' permission")
        else:
            print(f"   ⚠️  Unknown error: {e}")

if __name__ == "__main__":
    asyncio.run(test_telegram())

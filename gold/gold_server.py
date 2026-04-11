"""
Grandcom Gold Signals Server
Standalone backend for XAUUSD & XAUEUR signals
Sends to @grandcomgold Telegram channel
Designed for Railway deployment (no emergentintegrations dependency)
"""
from fastapi import FastAPI
from contextlib import asynccontextmanager
import os
import logging
import json
import re
import asyncio
import aiohttp
import ta
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from telegram import Bot
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from motor.motor_asyncio import AsyncIOMotorClient
from emergentintegrations.llm.chat import LlmChat, UserMessage

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gold_server")

# ============ CONFIG ============
MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME", "gold_signals")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_GOLD_CHANNEL_ID = os.environ.get("TELEGRAM_GOLD_CHANNEL_ID", "@grandcomgold")
TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("EMERGENT_LLM_KEY")

# Gold pair configuration — ATR-based swing strategy
GOLD_PAIRS = {
    "XAUUSD": {
        "twelve_data_symbol": "XAU/USD",
        "pip_value": 0.10,
        "decimal_places": 2,
        "atr_multiplier_sl": 1.5,
        "atr_multiplier_tp1": 2.0,
        "atr_multiplier_tp2": 3.5,
        "atr_multiplier_tp3": 5.0,
        "min_rr": 1.8,
        "min_confidence": 60,
    },
    "XAUEUR": {
        "twelve_data_symbol": "XAU/EUR",
        "pip_value": 0.10,
        "decimal_places": 2,
        "atr_multiplier_sl": 1.5,
        "atr_multiplier_tp1": 2.0,
        "atr_multiplier_tp2": 3.5,
        "atr_multiplier_tp3": 5.0,
        "min_rr": 1.8,
        "min_confidence": 60,
    },
}

SIGNAL_INTERVAL_MINUTES = 2
MIN_CONFIDENCE = 60

# ============ DB ============
client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]

# ============ PRICE DATA ============
async def get_price_data(pair: str, interval: str = "1h", outputsize: int = 100):
    symbol = GOLD_PAIRS[pair]["twelve_data_symbol"]
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize={outputsize}&apikey={TWELVE_DATA_API_KEY}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
                if "values" not in data:
                    logger.error(f"No data for {pair}: {data.get('message', 'Unknown error')}")
                    return None
                df = pd.DataFrame(data["values"])
                for col in ["open", "high", "low", "close"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.sort_index(ascending=False).reset_index(drop=True)
                return df
    except Exception as e:
        logger.error(f"Error fetching {pair}: {e}")
        return None

def calculate_indicators(df, params):
    try:
        df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
        macd = ta.trend.MACD(df["close"])
        df["macd"] = macd.macd()
        df["macd_signal"] = macd.macd_signal()
        df["ma_20"] = ta.trend.SMAIndicator(df["close"], window=20).sma_indicator()
        df["ma_50"] = ta.trend.SMAIndicator(df["close"], window=50).sma_indicator()
        bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
        df["bb_upper"] = bb.bollinger_hband()
        df["bb_lower"] = bb.bollinger_lband()
        atr_ind = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14)
        df["atr"] = atr_ind.average_true_range()
        latest = df.iloc[-1]
        dp = params["decimal_places"]
        trend = "BULLISH" if latest["close"] > latest["ma_50"] else "BEARISH"
        return {
            "current_price": round(float(latest["close"]), dp),
            "rsi": float(latest["rsi"]),
            "macd": float(latest["macd"]),
            "macd_signal": float(latest["macd_signal"]),
            "ma_20": round(float(latest["ma_20"]), dp),
            "ma_50": round(float(latest["ma_50"]), dp),
            "bb_upper": round(float(latest["bb_upper"]), dp),
            "bb_lower": round(float(latest["bb_lower"]), dp),
            "atr": round(float(latest["atr"]), dp),
            "trend": trend,
        }
    except Exception as e:
        logger.error(f"Indicator calc error: {e}")
        return None

# ============ AI ANALYSIS ============
async def generate_ai_analysis(symbol: str, indicators: dict, params: dict):
    try:
        system_message = "You are an elite institutional gold trader. Provide precise, actionable trading signals with strict risk management."
        prompt = f"""
        Analyze {symbol} market data and provide a professional trading signal:

        === MARKET DATA ===
        Current Price: {indicators['current_price']}
        RSI: {indicators['rsi']:.2f} | MACD: {indicators['macd']:.6f}
        MA50: {indicators['ma_50']:.{params['decimal_places']}f}
        ATR: {indicators['atr']:.{params['decimal_places']}f}
        Trend: {indicators['trend']}

        === ATR MULTIPLIERS ===
        SL: {params['atr_multiplier_sl']} | TP1: {params['atr_multiplier_tp1']} | TP2: {params['atr_multiplier_tp2']} | TP3: {params['atr_multiplier_tp3']}
        Min R:R: {params['min_rr']}

        === OUTPUT FORMAT (JSON ONLY) ===
        {{"signal":"BUY"or"SELL"or"NEUTRAL","confidence":0-100,"entry_price":numeric,"tp_levels":[tp1,tp2,tp3],"sl_price":numeric,"analysis":"<150 words","risk_reward":numeric}}
        RESPOND ONLY WITH VALID JSON. NO OTHER TEXT.
        """

        ai_response = None
        for attempt in range(3):
            try:
                chat = LlmChat(
                    api_key=OPENAI_API_KEY,
                    session_id=f"gold_{symbol}_{datetime.now(timezone.utc).timestamp()}_{attempt}",
                    system_message=system_message
                ).with_model("openai", "gpt-4o-mini")
                
                user_msg = UserMessage(text=prompt)
                ai_response = await chat.send_message(user_msg)
                if ai_response and len(ai_response.strip()) > 10:
                    break
            except Exception as e:
                logger.warning(f"LLM attempt {attempt+1}/3 for {symbol}: {e}")
                await asyncio.sleep(1)

        if not ai_response or len(ai_response.strip()) < 10:
            logger.error(f"No AI response for {symbol}")
            return None

        # Parse JSON — handle markdown fences and malformed JSON
        raw = ai_response.strip()
        fence_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', raw, re.DOTALL)
        if fence_match:
            raw = fence_match.group(1).strip()
        if not raw.startswith('{'):
            brace_match = re.search(r'\{.*\}', raw, re.DOTALL)
            if brace_match:
                raw = brace_match.group(0)

        ai_data = None
        for parse_attempt in range(3):
            try:
                if parse_attempt == 0:
                    ai_data = json.loads(raw)
                elif parse_attempt == 1:
                    fixed = re.sub(r',\s*}', '}', raw)
                    fixed = re.sub(r',\s*]', ']', fixed)
                    fixed = re.sub(r'"\s*\n\s*"', '",\n"', fixed)
                    fixed = re.sub(r'(\d)\s*\n\s*"', r'\1,\n"', fixed)
                    fixed = fixed.replace("'", '"')
                    ai_data = json.loads(fixed)
                else:
                    signal_m = re.search(r'"signal"\s*:\s*"(\w+)"', raw)
                    conf_m = re.search(r'"confidence"\s*:\s*([\d.]+)', raw)
                    entry_m = re.search(r'"entry_price"\s*:\s*([\d.]+)', raw)
                    analysis_m = re.search(r'"analysis"\s*:\s*"([^"]*)"', raw)
                    ai_data = {
                        "signal": signal_m.group(1) if signal_m else "NEUTRAL",
                        "confidence": float(conf_m.group(1)) if conf_m else 50.0,
                        "entry_price": float(entry_m.group(1)) if entry_m else indicators['current_price'],
                        "analysis": analysis_m.group(1) if analysis_m else "AI analysis unavailable",
                        "tp_levels": [], "sl_price": 0
                    }
                break
            except Exception:
                if parse_attempt == 2:
                    logger.warning(f"All JSON parsing failed for {symbol}")

        if not ai_data:
            return None

        # Fix TP levels using ATR if AI returned bad values
        entry = ai_data.get("entry_price", indicators['current_price'])
        signal_type = ai_data.get("signal", "NEUTRAL")
        tp_levels = ai_data.get("tp_levels", [])
        atr = indicators["atr"]
        dp = params["decimal_places"]

        if signal_type != "NEUTRAL" and (len(tp_levels) != 3 or len(set(tp_levels)) != 3):
            if signal_type == "BUY":
                tp_levels = [
                    round(entry + atr * params["atr_multiplier_tp1"], dp),
                    round(entry + atr * params["atr_multiplier_tp2"], dp),
                    round(entry + atr * params["atr_multiplier_tp3"], dp),
                ]
            else:
                tp_levels = [
                    round(entry - atr * params["atr_multiplier_tp1"], dp),
                    round(entry - atr * params["atr_multiplier_tp2"], dp),
                    round(entry - atr * params["atr_multiplier_tp3"], dp),
                ]
            ai_data["tp_levels"] = tp_levels

        # Fix SL using ATR if needed
        sl_price = ai_data.get("sl_price", 0)
        if signal_type == "BUY" and (sl_price >= entry or sl_price == 0):
            sl_price = round(entry - atr * params["atr_multiplier_sl"], dp)
        elif signal_type == "SELL" and (sl_price <= entry or sl_price == 0):
            sl_price = round(entry + atr * params["atr_multiplier_sl"], dp)
        ai_data["sl_price"] = sl_price

        risk_reward = ai_data.get("risk_reward", params["min_rr"])
        if not isinstance(risk_reward, (int, float)):
            risk_reward = params["min_rr"]
        ai_data["risk_reward"] = risk_reward

        return ai_data
    except Exception as e:
        logger.error(f"Error generating AI analysis for {symbol}: {e}")
        return None

# ============ TELEGRAM ============
def sanitize_html(text: str) -> str:
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

async def send_signal_to_telegram(pair, signal_type, entry_price, tp_levels, sl_price, confidence, risk_reward, analysis, regime="SWING"):
    try:
        if not TELEGRAM_BOT_TOKEN:
            logger.warning("Telegram bot token not configured")
            return
        bot = Bot(token=TELEGRAM_BOT_TOKEN)

        signal_emoji = "🟢" if signal_type == "BUY" else "🔴"
        action = signal_type.capitalize()

        # Entry range for TSCopier smart entry
        entry_lo = round(entry_price - 0.50, 2)
        entry_hi = round(entry_price + 0.50, 2)

        copier_message = (
            f"{signal_emoji} #{pair} [SWING]\n"
            f"\n"
            f"{action} {entry_lo} - {entry_hi}\n"
            f"\n"
            f"TP1: {tp_levels[0]}\n"
            f"TP2: {tp_levels[1]}\n"
            f"TP3: {tp_levels[2]}\n"
            f"\n"
            f"SL: {sl_price}\n"
        )

        safe_analysis = sanitize_html(analysis)
        info_message = (
            f"<b>📊 R:R:</b> 1:{risk_reward}  "
            f"<b>⚡ AI Confidence:</b> {confidence}%\n"
            f"<b>📝</b> {safe_analysis}\n"
            f"<i>⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
            f"| Grandcom Gold ML Engine</i>"
        )

        await bot.send_message(chat_id=TELEGRAM_GOLD_CHANNEL_ID, text=copier_message)
        await bot.send_message(chat_id=TELEGRAM_GOLD_CHANNEL_ID, text=info_message, parse_mode="HTML")
        logger.info(f"✅ Gold signal sent to {TELEGRAM_GOLD_CHANNEL_ID}: {pair} {signal_type}")
    except Exception as e:
        logger.error(f"❌ Error sending gold signal to Telegram: {e}")

# ============ SIGNAL GENERATION ============
async def generate_gold_signal(pair: str):
    try:
        params = GOLD_PAIRS[pair]
        logger.info(f"📊 Generating gold signal for {pair}")

        df = await get_price_data(pair, interval="4h", outputsize=100)
        if df is None or len(df) < 20:
            logger.warning(f"Insufficient data for {pair}")
            return

        indicators = calculate_indicators(df, params)
        if not indicators:
            return

        ai_analysis = await generate_ai_analysis(pair, indicators, params)
        if not ai_analysis:
            return

        signal_type = ai_analysis.get("signal", "NEUTRAL")
        if signal_type == "NEUTRAL":
            logger.info(f"No trade signal for {pair} (NEUTRAL)")
            return

        confidence = float(ai_analysis.get("confidence", 0))
        if confidence < params["min_confidence"]:
            logger.info(f"{pair} skipped — confidence {confidence}% < {params['min_confidence']}%")
            return

        entry_price = ai_analysis["entry_price"]
        tp_levels = ai_analysis["tp_levels"]
        sl_price = ai_analysis["sl_price"]
        risk_reward = ai_analysis.get("risk_reward", params["min_rr"])

        # Store in DB
        signal_doc = {
            "pair": pair,
            "type": signal_type,
            "entry_price": entry_price,
            "current_price": indicators["current_price"],
            "tp_levels": tp_levels,
            "sl_price": sl_price,
            "confidence": round(confidence, 1),
            "analysis": ai_analysis.get("analysis", ""),
            "risk_reward": risk_reward,
            "timeframe": "4H",
            "status": "ACTIVE",
            "created_at": datetime.now(timezone.utc),
        }
        await db.gold_signals.insert_one(signal_doc)

        # Send to Telegram
        await send_signal_to_telegram(
            pair=pair,
            signal_type=signal_type,
            entry_price=entry_price,
            tp_levels=tp_levels,
            sl_price=sl_price,
            confidence=round(confidence, 1),
            risk_reward=risk_reward,
            analysis=ai_analysis.get("analysis", ""),
        )

        logger.info(f"✅ {pair} {signal_type} @ {entry_price} | TP: {tp_levels} | SL: {sl_price} | Conf: {confidence}%")
    except Exception as e:
        logger.error(f"Error generating gold signal for {pair}: {e}")

async def run_gold_signals():
    logger.info("🥇 Running gold signal generation...")
    for pair in GOLD_PAIRS:
        await generate_gold_signal(pair)
        await asyncio.sleep(2)
    logger.info("🥇 Gold signal generation complete")

# ============ APP ============
scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(run_gold_signals, "interval", minutes=SIGNAL_INTERVAL_MINUTES, id="gold_signals")
    scheduler.start()
    logger.info(f"🥇 Gold Signals Server started — {list(GOLD_PAIRS.keys())} every {SIGNAL_INTERVAL_MINUTES}min")
    asyncio.create_task(run_gold_signals())
    yield
    scheduler.shutdown()
    client.close()

app = FastAPI(title="Grandcom Gold Signals", lifespan=lifespan)

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "gold_signals", "pairs": list(GOLD_PAIRS.keys())}

@app.get("/api/gold/signals")
async def get_gold_signals(status: str = None, limit: int = 50):
    query = {}
    if status:
        query["status"] = status.upper()
    signals = await db.gold_signals.find(query, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    return {"signals": signals, "count": len(signals)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8002)))

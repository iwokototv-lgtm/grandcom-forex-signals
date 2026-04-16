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

# Railway-safe LLM import: emergentintegrations on Emergent pod, litellm fallback on Railway
HAS_EMERGENT_LLM = False
try:
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    HAS_EMERGENT_LLM = True
except ImportError:
    pass

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

        rsi_val = float(latest["rsi"])
        macd_val = float(latest["macd"])
        macd_sig = float(latest["macd_signal"])
        close = float(latest["close"])
        bb_up = float(latest["bb_upper"])
        bb_low = float(latest["bb_lower"])
        bb_range = bb_up - bb_low if bb_up != bb_low else 1.0
        ma20 = float(latest["ma_20"])
        ma50 = float(latest["ma_50"])
        bb_position = (close - bb_low) / bb_range

        # --- TREND LABEL (reference only — AI decides signal direction) ---
        trend = "BULLISH" if close > ma50 else "BEARISH"

        # RSI zone label
        if rsi_val > 70:
            rsi_zone = "OVERBOUGHT"
        elif rsi_val < 30:
            rsi_zone = "OVERSOLD"
        else:
            rsi_zone = "NEUTRAL"

        logger.info(f"Trend: {trend} | RSI={rsi_val:.1f}({rsi_zone}) | BB_pos={bb_position:.2f} | Price={close} vs MA50={ma50:.2f}")

        return {
            "current_price": round(close, dp),
            "rsi": rsi_val,
            "rsi_zone": rsi_zone,
            "macd": macd_val,
            "macd_signal": macd_sig,
            "ma_20": round(ma20, dp),
            "ma_50": round(ma50, dp),
            "bb_upper": round(bb_up, dp),
            "bb_lower": round(bb_low, dp),
            "bb_position": round(bb_position, 2),
            "atr": round(float(latest["atr"]), dp),
            "trend": trend,
        }
    except Exception as e:
        logger.error(f"Indicator calc error: {e}")
        return None

# ============ AI ANALYSIS ============
async def generate_ai_analysis(symbol: str, indicators: dict, params: dict):
    try:
        system_message = (
            "You are an elite institutional gold trader specialising in mean-reversion and "
            "momentum setups. Analyse the provided technical indicators and independently decide "
            "whether to BUY, SELL, or stay NEUTRAL. Do NOT blindly follow the trend — look for "
            "overbought/oversold extremes, MACD divergence, and Bollinger Band positioning to "
            "identify high-probability counter-trend and trend-continuation entries. "
            "Set confidence below 60 if no clear setup exists."
        )

        prompt = f"""
Analyse {symbol} and decide the best trade direction (BUY, SELL, or NEUTRAL).

=== MARKET DATA ===
Current Price: {indicators['current_price']}
Trend (Price vs MA50): {indicators['trend']}
RSI: {indicators['rsi']:.2f} ({indicators['rsi_zone']})
MACD: {indicators['macd']:.6f} | MACD Signal: {indicators['macd_signal']:.6f}
MA20: {indicators['ma_20']} | MA50: {indicators['ma_50']}
BB Upper: {indicators['bb_upper']} | BB Lower: {indicators['bb_lower']} | BB Position: {indicators['bb_position']:.2f} (0=lower band, 1=upper band)
ATR: {indicators['atr']}

=== DECISION GUIDELINES ===
- BUY when: RSI oversold (<30), price near lower Bollinger Band, MACD turning up, or strong bullish momentum
- SELL when: RSI overbought (>70), price near upper Bollinger Band, MACD turning down, or strong bearish momentum
- NEUTRAL when: no clear setup — set confidence below 60
- Counter-trend trades are valid: oversold in a downtrend or overbought in an uptrend are often the best setups
- Trend label is for reference only — you are NOT required to follow it

=== ATR MULTIPLIERS ===
SL: {params['atr_multiplier_sl']} | TP1: {params['atr_multiplier_tp1']} | TP2: {params['atr_multiplier_tp2']} | TP3: {params['atr_multiplier_tp3']}

=== RULES ===
- Choose signal direction independently based on the indicators above
- BUY: TP above entry, SL below entry
- SELL: TP below entry, SL above entry
- Set confidence 0-100 reflecting setup quality; below 60 = no trade

=== OUTPUT FORMAT (JSON ONLY) ===
{{"signal":"BUY|SELL|NEUTRAL","confidence":0-100,"entry_price":numeric,"tp_levels":[tp1,tp2,tp3],"sl_price":numeric,"analysis":"<150 words","risk_reward":numeric}}
RESPOND ONLY WITH VALID JSON. NO OTHER TEXT.
"""

        ai_response = None
        for attempt in range(3):
            try:
                if HAS_EMERGENT_LLM:
                    chat = LlmChat(
                        api_key=OPENAI_API_KEY,
                        session_id=f"gold_{symbol}_{datetime.now(timezone.utc).timestamp()}_{attempt}",
                        system_message=system_message
                    ).with_model("openai", "gpt-4o-mini")
                    user_msg = UserMessage(text=prompt)
                    ai_response = await chat.send_message(user_msg)
                else:
                    import litellm
                    response = await litellm.acompletion(
                        model="gpt-4o-mini",
                        messages=[
                            {"role": "system", "content": system_message},
                            {"role": "user", "content": prompt}
                        ],
                        api_key=OPENAI_API_KEY,
                    )
                    ai_response = response.choices[0].message.content
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

        async with Bot(token=TELEGRAM_BOT_TOKEN) as bot:
            await bot.send_message(chat_id=TELEGRAM_GOLD_CHANNEL_ID, text=copier_message)
            await bot.send_message(chat_id=TELEGRAM_GOLD_CHANNEL_ID, text=info_message, parse_mode="HTML")
        logger.info(f"✅ Gold signal sent to {TELEGRAM_GOLD_CHANNEL_ID}: {pair} {signal_type}")
    except Exception as e:
        logger.error(f"❌ Error sending gold signal to Telegram: {e}", exc_info=True)

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

        # Direction is decided by the AI — use its signal directly
        signal_type = ai_analysis.get("signal", "NEUTRAL")

        confidence = float(ai_analysis.get("confidence", 0))
        if confidence < params["min_confidence"]:
            logger.info(f"{pair} skipped — confidence {confidence}% < {params['min_confidence']}%")
            return

        # Use AI entry price, but force correct TP/SL direction using ATR
        entry_price = ai_analysis.get("entry_price", indicators["current_price"])
        atr = indicators["atr"]
        dp = params["decimal_places"]

        if signal_type == "BUY":
            tp_levels = [
                round(entry_price + atr * params["atr_multiplier_tp1"], dp),
                round(entry_price + atr * params["atr_multiplier_tp2"], dp),
                round(entry_price + atr * params["atr_multiplier_tp3"], dp),
            ]
            sl_price = round(entry_price - atr * params["atr_multiplier_sl"], dp)
        else:
            tp_levels = [
                round(entry_price - atr * params["atr_multiplier_tp1"], dp),
                round(entry_price - atr * params["atr_multiplier_tp2"], dp),
                round(entry_price - atr * params["atr_multiplier_tp3"], dp),
            ]
            sl_price = round(entry_price + atr * params["atr_multiplier_sl"], dp)

        risk_reward = ai_analysis.get("risk_reward", params["min_rr"])
        if not isinstance(risk_reward, (int, float)):
            risk_reward = params["min_rr"]

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

# ============ OUTCOME TRACKER ============
GOLD_SYMBOL_MAP = {"XAUUSD": "XAU/USD", "XAUEUR": "XAU/EUR"}

async def get_live_price(pair: str):
    try:
        api_symbol = GOLD_SYMBOL_MAP.get(pair, pair)
        url = f"https://api.twelvedata.com/price?symbol={api_symbol}&apikey={TWELVE_DATA_API_KEY}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                if "price" in data:
                    return float(data["price"])
                logger.warning(f"No live price for {pair}: {data}")
                return None
    except Exception as e:
        logger.error(f"Error fetching live price for {pair}: {e}")
        return None

def calculate_pips(pair: str, entry: float, exit_price: float, signal_type: str) -> float:
    pip_value = 0.1  # Gold pip
    diff = exit_price - entry
    if signal_type == "SELL":
        diff = -diff
    return round(diff / pip_value, 1)

async def check_signal_outcome(signal: dict, current_price: float):
    signal_type = signal.get("type", "").upper()
    entry = signal.get("entry_price", 0)
    sl = signal.get("sl_price", 0)
    tps = signal.get("tp_levels", [])
    if not signal_type or not entry or not sl or not tps:
        return None

    pair = signal.get("pair", "XAUUSD")

    if signal_type == "BUY":
        if current_price <= sl:
            return {"status": "CLOSED_SL", "result": "LOSS", "exit_price": current_price,
                    "pips": calculate_pips(pair, entry, current_price, signal_type), "tp_hit": None}
        for i in reversed(range(len(tps))):
            if current_price >= tps[i]:
                return {"status": f"CLOSED_TP{i+1}", "result": "WIN", "exit_price": current_price,
                        "pips": calculate_pips(pair, entry, current_price, signal_type), "tp_hit": i+1}
    elif signal_type == "SELL":
        if current_price >= sl:
            return {"status": "CLOSED_SL", "result": "LOSS", "exit_price": current_price,
                    "pips": calculate_pips(pair, entry, current_price, signal_type), "tp_hit": None}
        for i in reversed(range(len(tps))):
            if current_price <= tps[i]:
                return {"status": f"CLOSED_TP{i+1}", "result": "WIN", "exit_price": current_price,
                        "pips": calculate_pips(pair, entry, current_price, signal_type), "tp_hit": i+1}
    return None

async def send_close_notification(signal: dict, outcome: dict):
    try:
        if not TELEGRAM_BOT_TOKEN:
            return
        result_emoji = "✅" if outcome["result"] == "WIN" else "❌"
        pips_emoji = "📈" if outcome["pips"] > 0 else "📉"
        tp_info = f"\n<b>Target Hit:</b> TP{outcome['tp_hit']}" if outcome.get("tp_hit") else ""
        message = (
            f"{result_emoji} <b>TRADE CLOSED: {signal.get('pair', 'N/A')}</b> {result_emoji}\n\n"
            f"<b>📊 Direction:</b> {signal.get('type', 'N/A')}\n"
            f"<b>💰 Entry:</b> {signal.get('entry_price', 'N/A')}\n"
            f"<b>🎯 Exit:</b> {outcome['exit_price']}\n"
            f"<b>{pips_emoji} Pips:</b> {outcome['pips']:+.1f}\n"
            f"<b>📋 Result:</b> {outcome['result']}{tp_info}\n\n"
            f"<b>⏰ Closed:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            f"<i>🤖 Auto-tracked by Grandcom Gold ML Engine</i>"
        )
        async with Bot(token=TELEGRAM_BOT_TOKEN) as bot:
            await bot.send_message(chat_id=TELEGRAM_GOLD_CHANNEL_ID, text=message, parse_mode="HTML")
        logger.info(f"📩 Close notification sent for {signal.get('pair')}")
    except Exception as e:
        logger.error(f"Error sending close notification: {e}", exc_info=True)

async def check_all_gold_outcomes():
    try:
        from bson import ObjectId
        active = await db.gold_signals.find({"status": "ACTIVE"}).to_list(length=100)
        if not active:
            return
        logger.info(f"🔍 Checking {len(active)} active gold signals...")
        checked, closed = 0, 0
        signals_by_pair = {}
        for s in active:
            p = s.get("pair")
            if p not in signals_by_pair:
                signals_by_pair[p] = []
            signals_by_pair[p].append(s)

        for pair, signals in signals_by_pair.items():
            price = await get_live_price(pair)
            if price is None:
                continue
            for signal in signals:
                checked += 1
                outcome = await check_signal_outcome(signal, price)
                if outcome:
                    await db.gold_signals.update_one(
                        {"_id": signal["_id"]},
                        {"$set": {
                            "status": outcome["status"],
                            "result": outcome["result"],
                            "exit_price": outcome["exit_price"],
                            "pips": outcome["pips"],
                            "closed_at": datetime.now(timezone.utc)
                        }}
                    )
                    await send_close_notification(signal, outcome)
                    closed += 1
                    logger.info(f"📊 {pair} signal closed: {outcome['status']} | {outcome['pips']:+.1f} pips")
            await asyncio.sleep(0.5)
        logger.info(f"🔍 Outcome check: {checked} checked, {closed} closed")
    except Exception as e:
        logger.error(f"Error in gold outcome check: {e}")

# ============ APP ============
scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(run_gold_signals, "interval", minutes=SIGNAL_INTERVAL_MINUTES, id="gold_signals")
    scheduler.add_job(check_all_gold_outcomes, "interval", seconds=60, id="gold_outcome_tracker")
    scheduler.start()
    logger.info(f"🥇 Gold Signals Server started — {list(GOLD_PAIRS.keys())} every {SIGNAL_INTERVAL_MINUTES}min")
    logger.info("🔍 Gold Outcome Tracker started — checking every 60s")
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

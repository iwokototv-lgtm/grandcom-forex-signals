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
        prev = df.iloc[-2]
        dp = params["decimal_places"]

        rsi_val = float(latest["rsi"])
        macd_val = float(latest["macd"])
        macd_sig = float(latest["macd_signal"])
        prev_macd = float(prev["macd"])
        prev_macd_sig = float(prev["macd_signal"])
        close = float(latest["close"])
        bb_up = float(latest["bb_upper"])
        bb_low = float(latest["bb_lower"])
        bb_range = bb_up - bb_low if bb_up != bb_low else 1.0
        ma20 = float(latest["ma_20"])
        ma50 = float(latest["ma_50"])

        # --- Technical direction scoring ---
        # Score > 0 = bullish bias, Score < 0 = bearish bias
        score = 0

        # RSI: overbought (>70) = sell signal, oversold (<30) = buy signal
        if rsi_val > 75:
            score -= 3
        elif rsi_val > 70:
            score -= 2
        elif rsi_val > 65:
            score -= 1
        elif rsi_val < 25:
            score += 3
        elif rsi_val < 30:
            score += 2
        elif rsi_val < 35:
            score += 1

        # MACD crossover detection
        macd_bullish_cross = prev_macd <= prev_macd_sig and macd_val > macd_sig
        macd_bearish_cross = prev_macd >= prev_macd_sig and macd_val < macd_sig
        if macd_bullish_cross:
            score += 2
        elif macd_bearish_cross:
            score -= 2
        elif macd_val > macd_sig:
            score += 1
        elif macd_val < macd_sig:
            score -= 1

        # Bollinger Band position
        bb_position = (close - bb_low) / bb_range  # 0=lower band, 1=upper band
        if bb_position > 0.95:
            score -= 2  # At/above upper band = overbought
        elif bb_position > 0.80:
            score -= 1
        elif bb_position < 0.05:
            score += 2  # At/below lower band = oversold
        elif bb_position < 0.20:
            score += 1

        # Price vs MAs
        if close < ma20 and close < ma50:
            score -= 1  # Below both MAs = bearish
        elif close > ma20 and close > ma50:
            score += 1  # Above both MAs = bullish

        # Determine technical direction
        if score >= 2:
            tech_direction = "BUY"
        elif score <= -2:
            tech_direction = "SELL"
        else:
            tech_direction = "NEUTRAL"

        trend = "BULLISH" if close > ma50 else "BEARISH"

        # RSI zone description
        if rsi_val > 70:
            rsi_zone = "OVERBOUGHT"
        elif rsi_val < 30:
            rsi_zone = "OVERSOLD"
        else:
            rsi_zone = "NEUTRAL"

        logger.info(f"Technical scoring: score={score}, direction={tech_direction}, RSI={rsi_val:.1f}({rsi_zone}), MACD_cross={'BULL' if macd_bullish_cross else 'BEAR' if macd_bearish_cross else 'NONE'}, BB_pos={bb_position:.2f}")

        return {
            "current_price": round(close, dp),
            "rsi": rsi_val,
            "rsi_zone": rsi_zone,
            "macd": macd_val,
            "macd_signal": macd_sig,
            "macd_bullish_cross": macd_bullish_cross,
            "macd_bearish_cross": macd_bearish_cross,
            "ma_20": round(ma20, dp),
            "ma_50": round(ma50, dp),
            "bb_upper": round(bb_up, dp),
            "bb_lower": round(bb_low, dp),
            "bb_position": round(bb_position, 2),
            "atr": round(float(latest["atr"]), dp),
            "trend": trend,
            "tech_direction": tech_direction,
            "tech_score": score,
        }
    except Exception as e:
        logger.error(f"Indicator calc error: {e}")
        return None

# ============ AI ANALYSIS ============
async def generate_ai_analysis(symbol: str, indicators: dict, params: dict):
    try:
        system_message = (
            "You are an elite institutional gold trader who trades BOTH directions. "
            "You are equally comfortable going LONG (BUY) and SHORT (SELL). "
            "Your edge comes from identifying reversals at overbought/oversold extremes, not just following trends. "
            "When indicators show overbought conditions (RSI>70, price at upper Bollinger), you MUST recommend SELL. "
            "When indicators show oversold conditions (RSI<30, price at lower Bollinger), you MUST recommend BUY. "
            "Never be biased toward one direction."
        )

        # Build balanced indicator summary for the AI
        rsi_val = indicators['rsi']
        rsi_zone = indicators['rsi_zone']
        bb_pos = indicators['bb_position']
        tech_dir = indicators['tech_direction']
        tech_score = indicators['tech_score']

        # Create explicit bearish/bullish arguments
        bearish_args = []
        bullish_args = []

        if rsi_val > 70:
            bearish_args.append(f"RSI at {rsi_val:.1f} is OVERBOUGHT — historically signals pullback/reversal")
        elif rsi_val > 60:
            bearish_args.append(f"RSI at {rsi_val:.1f} is approaching overbought territory")
        if rsi_val < 30:
            bullish_args.append(f"RSI at {rsi_val:.1f} is OVERSOLD — historically signals bounce/reversal")
        elif rsi_val < 40:
            bullish_args.append(f"RSI at {rsi_val:.1f} is approaching oversold territory")

        if indicators['macd_bearish_cross']:
            bearish_args.append("MACD just crossed BELOW signal line — bearish momentum shift")
        elif indicators['macd'] < indicators['macd_signal']:
            bearish_args.append("MACD is below signal line — bearish momentum")
        if indicators['macd_bullish_cross']:
            bullish_args.append("MACD just crossed ABOVE signal line — bullish momentum shift")
        elif indicators['macd'] > indicators['macd_signal']:
            bullish_args.append("MACD is above signal line — bullish momentum")

        if bb_pos > 0.90:
            bearish_args.append(f"Price is at {bb_pos*100:.0f}% of Bollinger range — near UPPER band, expect mean reversion DOWN")
        elif bb_pos > 0.75:
            bearish_args.append(f"Price at {bb_pos*100:.0f}% of Bollinger range — approaching upper band resistance")
        if bb_pos < 0.10:
            bullish_args.append(f"Price is at {bb_pos*100:.0f}% of Bollinger range — near LOWER band, expect mean reversion UP")
        elif bb_pos < 0.25:
            bullish_args.append(f"Price at {bb_pos*100:.0f}% of Bollinger range — approaching lower band support")

        bearish_text = "\n".join(f"  - {a}" for a in bearish_args) if bearish_args else "  - No strong bearish signals"
        bullish_text = "\n".join(f"  - {a}" for a in bullish_args) if bullish_args else "  - No strong bullish signals"

        # The tech_direction strongly guides the AI
        direction_instruction = ""
        if tech_dir == "SELL":
            direction_instruction = f"TECHNICAL ANALYSIS STRONGLY SUGGESTS: SELL (score: {tech_score}). Multiple indicators confirm bearish conditions. You should output SELL unless you have an extremely compelling reason not to."
        elif tech_dir == "BUY":
            direction_instruction = f"TECHNICAL ANALYSIS STRONGLY SUGGESTS: BUY (score: {tech_score}). Multiple indicators confirm bullish conditions. You should output BUY unless you have an extremely compelling reason not to."
        else:
            direction_instruction = f"Technical indicators are MIXED (score: {tech_score}). Weigh both bearish and bullish arguments equally. Choose whichever direction has the strongest technical confluence. Do not default to either BUY or SELL — let the indicators decide."

        prompt = f"""
Analyze {symbol} and provide a trading signal. Consider BOTH directions equally.

=== MARKET DATA ===
Current Price: {indicators['current_price']}
RSI: {rsi_val:.2f} ({rsi_zone}) | MACD: {indicators['macd']:.6f} | MACD Signal: {indicators['macd_signal']:.6f}
MA20: {indicators['ma_20']} | MA50: {indicators['ma_50']}
BB Upper: {indicators['bb_upper']} | BB Lower: {indicators['bb_lower']} | BB Position: {bb_pos*100:.0f}%
ATR: {indicators['atr']}

=== BEARISH ARGUMENTS (reasons to SELL) ===
{bearish_text}

=== BULLISH ARGUMENTS (reasons to BUY) ===
{bullish_text}

=== {direction_instruction} ===

=== ATR MULTIPLIERS ===
SL: {params['atr_multiplier_sl']} | TP1: {params['atr_multiplier_tp1']} | TP2: {params['atr_multiplier_tp2']} | TP3: {params['atr_multiplier_tp3']}
Min R:R: {params['min_rr']}

=== RULES ===
- If RSI > 70 AND price near upper Bollinger: signal MUST be SELL
- If RSI < 30 AND price near lower Bollinger: signal MUST be BUY
- Do NOT default to BUY just because the overall trend is up

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

        # Technical override: if tech scoring strongly disagrees with AI, use tech direction
        tech_dir = indicators.get("tech_direction", "NEUTRAL")
        tech_score = indicators.get("tech_score", 0)

        if signal_type != "NEUTRAL" and tech_dir != "NEUTRAL" and signal_type != tech_dir:
            if abs(tech_score) >= 3:
                logger.info(f"{pair} OVERRIDE: AI said {signal_type} but tech_score={tech_score} -> forcing {tech_dir}")
                signal_type = tech_dir
                ai_analysis["signal"] = tech_dir
                # Recalculate TP/SL for new direction
                entry = ai_analysis.get("entry_price", indicators["current_price"])
                atr = indicators["atr"]
                dp = params["decimal_places"]
                if tech_dir == "SELL":
                    ai_analysis["tp_levels"] = [
                        round(entry - atr * params["atr_multiplier_tp1"], dp),
                        round(entry - atr * params["atr_multiplier_tp2"], dp),
                        round(entry - atr * params["atr_multiplier_tp3"], dp),
                    ]
                    ai_analysis["sl_price"] = round(entry + atr * params["atr_multiplier_sl"], dp)
                else:
                    ai_analysis["tp_levels"] = [
                        round(entry + atr * params["atr_multiplier_tp1"], dp),
                        round(entry + atr * params["atr_multiplier_tp2"], dp),
                        round(entry + atr * params["atr_multiplier_tp3"], dp),
                    ]
                    ai_analysis["sl_price"] = round(entry - atr * params["atr_multiplier_sl"], dp)

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

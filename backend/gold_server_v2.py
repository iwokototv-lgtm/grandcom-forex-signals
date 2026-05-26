"""
Grandcom Gold Signals Server v2.0
With integrated Position Sizing & Risk Management
Railway deployment ready
"""

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiohttp
import pandas as pd
import ta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import Bot

# Import position calculator
from ml_engine.position_calculator import PositionCalculator

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gold_server")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MONGO_URL = os.environ.get("MONGO_URL", "")
DB_NAME = os.environ.get("DB_NAME", "gold_signals")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("EMERGENT_LLM_KEY", "")

# Trading account config
ACCOUNT_BALANCE = float(os.environ.get("ACCOUNT_BALANCE", "1000"))
RISK_PER_TRADE = float(os.environ.get("RISK_PER_TRADE", "0.05"))  # 5%

# Telegram channel
_raw_channel = os.environ.get("TELEGRAM_GOLD_CHANNEL_ID", "-1003834233408")
try:
    TELEGRAM_CHANNEL_ID: int | str = int(_raw_channel)
except ValueError:
    TELEGRAM_CHANNEL_ID = _raw_channel

SIGNAL_INTERVAL_MINUTES = 2
MIN_CONFIDENCE = 60

# ---------------------------------------------------------------------------
# Pairs
# ---------------------------------------------------------------------------
PAIRS = {
    "XAUUSD": {
        "symbol": "XAU/USD",
        "decimals": 2,
        "atr_sl": 1.5,
        "atr_tp1": 2.0,
        "atr_tp2": 3.5,
        "atr_tp3": 5.0,
    },
    "XAUEUR": {
        "symbol": "XAU/EUR",
        "decimals": 2,
        "atr_sl": 1.5,
        "atr_tp1": 2.0,
        "atr_tp2": 3.5,
        "atr_tp3": 5.0,
    },
}

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_mongo_client: AsyncIOMotorClient | None = None
_db = None
_bot: Bot | None = None
_position_calculator: PositionCalculator | None = None


def get_db():
    return _db


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        if not TELEGRAM_BOT_TOKEN:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
        _bot = Bot(token=TELEGRAM_BOT_TOKEN)
    return _bot


def get_position_calculator() -> PositionCalculator:
    global _position_calculator
    if _position_calculator is None:
        _position_calculator = PositionCalculator(
            account_balance=ACCOUNT_BALANCE,
            risk_per_trade=RISK_PER_TRADE
        )
    return _position_calculator


# ---------------------------------------------------------------------------
# Price data
# ---------------------------------------------------------------------------
async def fetch_ohlcv(pair: str, outputsize: int = 100) -> pd.DataFrame | None:
    """Fetch 4H OHLCV from TwelveData."""
    cfg = PAIRS[pair]
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={cfg['symbol']}&interval=4h&outputsize={outputsize}"
        f"&apikey={TWELVE_DATA_API_KEY}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()

        if "values" not in data:
            logger.error(f"[{pair}] TwelveData error: {data.get('message', data)}")
            return None

        df = pd.DataFrame(data["values"])
        for col in ("open", "high", "low", "close"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.iloc[::-1].reset_index(drop=True)
        logger.info(f"[{pair}] Fetched {len(df)} 4H candles")
        return df

    except Exception as exc:
        logger.error(f"[{pair}] fetch_ohlcv failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Technical indicators
# ---------------------------------------------------------------------------
def compute_indicators(df: pd.DataFrame, decimals: int) -> dict | None:
    """Compute RSI, MACD, MA20/50, ATR."""
    try:
        close = df["close"]
        high = df["high"]
        low = df["low"]

        rsi = ta.momentum.RSIIndicator(close, window=14).rsi()
        macd_obj = ta.trend.MACD(close)
        ma20 = ta.trend.SMAIndicator(close, window=20).sma_indicator()
        ma50 = ta.trend.SMAIndicator(close, window=50).sma_indicator()
        atr = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()

        last = df.iloc[-1]
        dp = decimals

        return {
            "price":      round(float(last["close"]), dp),
            "rsi":        round(float(rsi.iloc[-1]), 2),
            "macd":       round(float(macd_obj.macd().iloc[-1]), 6),
            "macd_sig":   round(float(macd_obj.macd_signal().iloc[-1]), 6),
            "ma20":       round(float(ma20.iloc[-1]), dp),
            "ma50":       round(float(ma50.iloc[-1]), dp),
            "atr":        round(float(atr.iloc[-1]), dp),
            "trend":      "BULLISH" if float(last["close"]) > float(ma50.iloc[-1]) else "BEARISH",
        }

    except Exception as exc:
        logger.error(f"compute_indicators failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# GPT signal analysis
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = (
    "You are an elite institutional gold trader. "
    "Analyse the provided market data and return a JSON trading signal. "
    "Respond ONLY with valid JSON — no markdown, no extra text."
)

_USER_TEMPLATE = """\
Analyse {pair} (4H timeframe) and provide a trading signal.

MARKET DATA
-----------
Price : {price}
RSI   : {rsi}
MACD  : {macd}  |  Signal: {macd_sig}
MA20  : {ma20}  |  MA50: {ma50}
ATR   : {atr}
Trend : {trend}

ATR MULTIPLIERS  (SL: {atr_sl}x | TP1: {atr_tp1}x | TP2: {atr_tp2}x | TP3: {atr_tp3}x)

OUTPUT FORMAT — return exactly this JSON structure:
{{
  "signal": "BUY" | "SELL" | "NEUTRAL",
  "confidence": <integer 0-100>,
  "entry_price": <number>,
  "tp_levels": [<tp1>, <tp2>, <tp3>],
  "sl_price": <number>,
  "analysis": "<max 120 words>",
  "risk_reward": <number>
}}
"""


async def gpt_signal(pair: str, ind: dict, cfg: dict) -> dict | None:
    """Call GPT-4o-mini and return parsed signal dict."""
    import litellm

    prompt = _USER_TEMPLATE.format(
        pair=pair,
        price=ind["price"],
        rsi=ind["rsi"],
        macd=ind["macd"],
        macd_sig=ind["macd_sig"],
        ma20=ind["ma20"],
        ma50=ind["ma50"],
        atr=ind["atr"],
        trend=ind["trend"],
        atr_sl=cfg["atr_sl"],
        atr_tp1=cfg["atr_tp1"],
        atr_tp2=cfg["atr_tp2"],
        atr_tp3=cfg["atr_tp3"],
    )

    raw_response = None
    for attempt in range(3):
        try:
            resp = await litellm.acompletion(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                api_key=OPENAI_API_KEY,
                timeout=30,
            )
            raw_response = resp.choices[0].message.content
            if raw_response and len(raw_response.strip()) > 10:
                break
        except Exception as exc:
            logger.warning(f"[{pair}] GPT attempt {attempt + 1}/3 failed: {exc}")
            await asyncio.sleep(2)

    if not raw_response:
        logger.error(f"[{pair}] No GPT response after 3 attempts")
        return None

    return _parse_gpt_response(pair, raw_response)


def _parse_gpt_response(pair: str, raw: str) -> dict | None:
    """Extract JSON from GPT response."""
    text = raw.strip()

    # Strip markdown code fences
    fence = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    # Find first JSON object
    if not text.startswith("{"):
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if brace:
            text = brace.group(0)

    # Attempt 1: direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Attempt 2: light cleanup
    try:
        fixed = re.sub(r",\s*}", "}", text)
        fixed = re.sub(r",\s*]", "]", fixed)
        fixed = fixed.replace("'", '"')
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # Attempt 3: regex field extraction
    try:
        sig_m   = re.search(r'"signal"\s*:\s*"(\w+)"', text)
        conf_m  = re.search(r'"confidence"\s*:\s*([\d.]+)', text)
        entry_m = re.search(r'"entry_price"\s*:\s*([\d.]+)', text)
        anal_m  = re.search(r'"analysis"\s*:\s*"([^"]*)"', text)
        rr_m    = re.search(r'"risk_reward"\s*:\s*([\d.]+)', text)
        return {
            "signal":       sig_m.group(1)   if sig_m   else "NEUTRAL",
            "confidence":   float(conf_m.group(1))  if conf_m  else 50.0,
            "entry_price":  float(entry_m.group(1)) if entry_m else 0.0,
            "analysis":     anal_m.group(1)  if anal_m  else "",
            "risk_reward":  float(rr_m.group(1))    if rr_m    else 2.0,
            "tp_levels":    [],
            "sl_price":     0.0,
        }
    except Exception as exc:
        logger.error(f"[{pair}] JSON parse failed entirely: {exc}\nRaw: {raw[:300]}")
        return None


# ---------------------------------------------------------------------------
# TP / SL calculation
# ---------------------------------------------------------------------------
def build_levels(signal: str, entry: float, atr: float, cfg: dict) -> tuple[list[float], float]:
    """Return (tp_levels, sl_price) using ATR multipliers."""
    dp = cfg["decimals"]
    if signal == "BUY":
        tps = [
            round(entry + atr * cfg["atr_tp1"], dp),
            round(entry + atr * cfg["atr_tp2"], dp),
            round(entry + atr * cfg["atr_tp3"], dp),
        ]
        sl = round(entry - atr * cfg["atr_sl"], dp)
    else:  # SELL
        tps = [
            round(entry - atr * cfg["atr_tp1"], dp),
            round(entry - atr * cfg["atr_tp2"], dp),
            round(entry - atr * cfg["atr_tp3"], dp),
        ]
        sl = round(entry + atr * cfg["atr_sl"], dp)
    return tps, sl


# ---------------------------------------------------------------------------
# Telegram delivery
# ---------------------------------------------------------------------------
def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def send_to_telegram(
    pair: str,
    signal: str,
    entry: float,
    tps: list[float],
    sl: float,
    confidence: float,
    rr: float,
    analysis: str,
    position_info: dict | None = None,
) -> None:
    """Send signal + position sizing info to Telegram."""
    try:
        bot = get_bot()
        emoji = "🟢" if signal == "BUY" else "🔴"
        action = signal.capitalize()
        lo = round(entry - 0.50, 2)
        hi = round(entry + 0.50, 2)

        copier_msg = (
            f"{emoji} #{pair} [SWING]\n"
            f"\n"
            f"{action} {lo} - {hi}\n"
            f"\n"
            f"TP1: {tps[0]}\n"
            f"TP2: {tps[1]}\n"
            f"TP3: {tps[2]}\n"
            f"\n"
            f"SL: {sl}\n"
        )

        info_msg = (
            f"<b>📊 R:R:</b> 1:{rr}  "
            f"<b>⚡ Confidence:</b> {confidence}%\n"
            f"<b>📝</b> {_html_escape(analysis)}\n"
        )
        
        # Add position sizing info if available
        if position_info and position_info.get('approved'):
            info_msg += (
                f"\n<b>💰 Position Size:</b> {position_info.get('adjusted_lot_size', 0):.4f} lots\n"
                f"<b>📉 Risk:</b> ${position_info.get('risk_amount', 0):.2f}\n"
                f"<b>📈 Equity:</b> ${position_info.get('current_equity', 0):.2f}\n"
            )
        
        info_msg += (
            f"<i>⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
            f"| Grandcom Gold Engine v2.0</i>"
        )

        await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=copier_msg)
        await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=info_msg, parse_mode="HTML")
        logger.info(f"[{pair}] Signal sent to Telegram channel {TELEGRAM_CHANNEL_ID}")

    except Exception as exc:
        logger.error(f"[{pair}] Telegram delivery failed: {exc}")


# ---------------------------------------------------------------------------
# Core signal generation
# ---------------------------------------------------------------------------
async def generate_signal(pair: str) -> None:
    """Full pipeline: fetch → indicators → GPT → position sizing → store → send."""
    cfg = PAIRS[pair]
    logger.info(f"[{pair}] Starting signal generation")

    # 1. Price data
    df = await fetch_ohlcv(pair)
    if df is None or len(df) < 52:
        logger.warning(f"[{pair}] Insufficient candles ({len(df) if df is not None else 0}), skipping")
        return

    # 2. Indicators
    ind = compute_indicators(df, cfg["decimals"])
    if ind is None:
        logger.warning(f"[{pair}] Indicator computation failed, skipping")
        return

    logger.info(
        f"[{pair}] price={ind['price']} rsi={ind['rsi']} "
        f"macd={ind['macd']} trend={ind['trend']} atr={ind['atr']}"
    )

    # 3. GPT analysis
    gpt = await gpt_signal(pair, ind, cfg)
    if gpt is None:
        logger.warning(f"[{pair}] GPT returned no signal, skipping")
        return

    signal_type = str(gpt.get("signal", "NEUTRAL")).upper()
    confidence  = float(gpt.get("confidence", 0))
    analysis    = str(gpt.get("analysis", ""))

    logger.info(f"[{pair}] GPT → signal={signal_type} confidence={confidence}")

    # 4. Filter NEUTRAL and low-confidence
    if signal_type == "NEUTRAL":
        logger.info(f"[{pair}] NEUTRAL signal — no trade")
        return

    if signal_type not in ("BUY", "SELL"):
        logger.warning(f"[{pair}] Unexpected signal value '{signal_type}' — skipping")
        return

    if confidence < MIN_CONFIDENCE:
        logger.info(f"[{pair}] Confidence {confidence}% < {MIN_CONFIDENCE}% threshold — skipping")
        return

    # 5. Build ATR-based levels
    entry = float(gpt.get("entry_price") or ind["price"])
    if entry <= 0:
        entry = ind["price"]

    tps, sl = build_levels(signal_type, entry, ind["atr"], cfg)

    # Sanity check
    if signal_type == "BUY" and (tps[0] <= entry or sl >= entry):
        logger.warning(f"[{pair}] BUY level geometry invalid — skipping")
        return
    if signal_type == "SELL" and (tps[0] >= entry or sl <= entry):
        logger.warning(f"[{pair}] SELL level geometry invalid — skipping")
        return

    # 6. Risk/reward
    risk   = abs(entry - sl)
    reward = abs(tps[0] - entry)
    rr     = round(reward / risk, 1) if risk > 0 else 2.0

    # 7. POSITION SIZING
    position_calc = get_position_calculator()
    position_info = position_calc.calculate_position_size(
        entry_price=entry,
        stop_loss=sl,
        pair=pair
    )
    
    if not position_info.get('approved'):
        logger.warning(f"[{pair}] Position sizing not approved: {position_info.get('warning')}")
        return

    # 8. Store in MongoDB
    db = get_db()
    if db is not None:
        try:
            doc = {
                "pair":          pair,
                "type":          signal_type,
                "entry_price":   entry,
                "current_price": ind["price"],
                "tp_levels":     tps,
                "sl_price":      sl,
                "confidence":    round(confidence, 1),
                "analysis":      analysis,
                "risk_reward":   rr,
                "timeframe":     "4H",
                "status":        "ACTIVE",
                "indicators":    ind,
                "position_sizing": {
                    "lot_size": position_info.get('adjusted_lot_size'),
                    "risk_amount": position_info.get('risk_amount'),
                    "position_size": position_info.get('adjusted_position_size'),
                },
                "created_at":    datetime.now(timezone.utc),
            }
            result = await db.gold_signals.insert_one(doc)
            logger.info(f"[{pair}] Signal stored — id={result.inserted_id}")
        except Exception as exc:
            logger.error(f"[{pair}] MongoDB insert failed: {exc}")
    else:
        logger.warning(f"[{pair}] MongoDB not available — signal not stored")

    # 9. Send to Telegram with position info
    await send_to_telegram(
        pair, signal_type, entry, tps, sl, 
        round(confidence, 1), rr, analysis,
        position_info
    )

    logger.info(
        f"[{pair}] ✅ {signal_type} @ {entry} | "
        f"TP: {tps} | SL: {sl} | R:R 1:{rr} | Conf: {confidence}% | "
        f"Lots: {position_info.get('adjusted_lot_size'):.4f}"
    )


# ---------------------------------------------------------------------------
# Scheduler job
# ---------------------------------------------------------------------------
async def run_all_signals() -> None:
    logger.info("=== Signal generation cycle START ===")
    for pair in PAIRS:
        try:
            await generate_signal(pair)
        except Exception as exc:
            logger.error(f"[{pair}] Unhandled error in generate_signal: {exc}", exc_info=True)
        await asyncio.sleep(2)
    logger.info("=== Signal generation cycle END ===")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mongo_client, _db, _position_calculator

    # --- Startup validation ---
    missing = []
    if not MONGO_URL:
        missing.append("MONGO_URL")
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TWELVE_DATA_API_KEY:
        missing.append("TWELVE_DATA_API_KEY")
    if not OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY / EMERGENT_LLM_KEY")

    if missing:
        logger.error(f"❌ Missing required environment variables: {missing}")
    else:
        logger.info("✅ All required environment variables present")

    # --- MongoDB ---
    if MONGO_URL:
        try:
            _mongo_client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
            _db = _mongo_client[DB_NAME]
            await _db.command("ping")
            logger.info(f"✅ MongoDB connected — db={DB_NAME}")
        except Exception as exc:
            logger.error(f"❌ MongoDB connection failed: {exc}")
            _db = None

    # --- Telegram bot ---
    if TELEGRAM_BOT_TOKEN:
        try:
            bot = get_bot()
            me = await bot.get_me()
            logger.info(f"✅ Telegram bot ready — @{me.username} → channel {TELEGRAM_CHANNEL_ID}")
        except Exception as exc:
            logger.error(f"❌ Telegram bot init failed: {exc}")

    # --- Position Calculator ---
    _position_calculator = PositionCalculator(
        account_balance=ACCOUNT_BALANCE,
        risk_per_trade=RISK_PER_TRADE
    )
    logger.info(
        f"✅ Position Calculator initialized — "
        f"Account: ${ACCOUNT_BALANCE:.2f}, Risk/Trade: {RISK_PER_TRADE*100}%"
    )

    # --- Scheduler ---
    scheduler.add_job(
        run_all_signals,
        "interval",
        minutes=SIGNAL_INTERVAL_MINUTES,
        id="gold_signals",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        f"✅ Scheduler started — pairs={list(PAIRS.keys())} "
        f"interval={SIGNAL_INTERVAL_MINUTES}min"
    )

    # Run immediately on startup
    asyncio.create_task(run_all_signals())

    yield

    # --- Shutdown ---
    scheduler.shutdown(wait=False)
    if _mongo_client:
        _mongo_client.close()
    logger.info("Gold Signals Server shut down")


app = FastAPI(title="Grandcom Gold Signals v2.0", version="2.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    """Railway health check endpoint."""
    db = get_db()
    mongo_ok = False
    if db is not None:
        try:
            await db.command("ping")
            mongo_ok = True
        except Exception:
            pass

    jobs = [
        {"id": j.id, "next_run": str(j.next_run_time)}
        for j in scheduler.get_jobs()
    ]

    return {
        "status":            "ok",
        "service":           "gold_signals_v2",
        "version":           "2.0.0",
        "pairs":             list(PAIRS.keys()),
        "telegram_channel":  TELEGRAM_CHANNEL_ID,
        "scheduler_running": scheduler.running,
        "scheduler_jobs":    jobs,
        "mongo_connected":   mongo_ok,
        "timestamp":         datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/signals")
async def get_signals(status: str | None = None, limit: int = 50):
    """Return stored signals, optionally filtered by status."""
    db = get_db()
    if db is None:
        return {"error": "MongoDB not connected", "signals": [], "count": 0}

    query: dict = {}
    if status:
        query["status"] = status.upper()

    signals = (
        await db.gold_signals
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
        .to_list(limit)
    )
    return {"signals": signals, "count": len(signals)}


@app.get("/api/risk-metrics")
async def get_risk_metrics():
    """Get current risk and equity metrics."""
    position_calc = get_position_calculator()
    return position_calc.get_risk_metrics()


@app.get("/api/trade-history")
async def get_trade_history(limit: int = 10):
    """Get recent trade history."""
    position_calc = get_position_calculator()
    return {"trades": position_calc.get_trade_history(limit), "count": len(position_calc.trades)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8002)))


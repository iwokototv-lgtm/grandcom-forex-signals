"""
Grandcom Gold Signals Server
============================
Standalone backend for XAUUSD & XAUEUR signals.
Sends formatted signals to a Telegram channel and stores them in MongoDB.
Designed for Railway deployment (Python 3.11, RAILPACK builder).

Key design decisions
--------------------
- Telegram channel is addressed by numeric ID (-100...) to avoid
  Peer_id_invalid errors that occur with @username strings on some
  bot/channel configurations.
- A single Bot instance is created at startup and reused for all sends
  (avoids repeated handshake overhead and connection leaks).
- Price data is sorted oldest→newest before indicator calculation so
  df.iloc[-1] always returns the most-recent candle.
- aiohttp requests carry an explicit timeout to prevent indefinite hangs.
- MongoDB client is initialised inside the lifespan context so missing
  env vars are caught early with a clear error message.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiohttp
import litellm
import pandas as pd
import ta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import Bot
from telegram.error import TelegramError

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("gold_server")

# ---------------------------------------------------------------------------
# Configuration — all values come from environment variables
# ---------------------------------------------------------------------------

MONGO_URL: str = os.environ.get("MONGO_URL", "")
DB_NAME: str = os.environ.get("DB_NAME", "gold_signals")

TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
# Use the numeric channel ID (format: -100<channel_id>) to avoid
# Peer_id_invalid errors.  The env var overrides the hard-coded default.
TELEGRAM_GOLD_CHANNEL_ID: int | str = int(
    os.environ.get("TELEGRAM_GOLD_CHANNEL_ID", "-1003834233408")
)

TWELVE_DATA_API_KEY: str = os.environ.get("TWELVE_DATA_API_KEY", "")
OPENAI_API_KEY: str = (
    os.environ.get("OPENAI_API_KEY") or os.environ.get("EMERGENT_LLM_KEY") or ""
)

SIGNAL_INTERVAL_MINUTES: int = int(os.environ.get("SIGNAL_INTERVAL_MINUTES", "2"))

# HTTP request timeout for TwelveData calls (seconds)
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30)

# ---------------------------------------------------------------------------
# Gold pair configuration — ATR-based swing strategy
# ---------------------------------------------------------------------------

GOLD_PAIRS: dict = {
    "XAUUSD": {
        "twelve_data_symbol": "XAU/USD",
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
        "decimal_places": 2,
        "atr_multiplier_sl": 1.5,
        "atr_multiplier_tp1": 2.0,
        "atr_multiplier_tp2": 3.5,
        "atr_multiplier_tp3": 5.0,
        "min_rr": 1.8,
        "min_confidence": 60,
    },
}

# ---------------------------------------------------------------------------
# Module-level singletons (populated inside lifespan)
# ---------------------------------------------------------------------------

_mongo_client: AsyncIOMotorClient | None = None
_db = None
_telegram_bot: Bot | None = None


def get_db():
    """Return the active MongoDB database handle."""
    if _db is None:
        raise RuntimeError("Database not initialised — check MONGO_URL env var")
    return _db


# ---------------------------------------------------------------------------
# Price data
# ---------------------------------------------------------------------------


async def get_price_data(pair: str, interval: str = "4h", outputsize: int = 100):
    """
    Fetch OHLCV candles from TwelveData.

    Returns a DataFrame sorted oldest→newest (required for indicator
    libraries that expect chronological order), or None on failure.
    """
    symbol = GOLD_PAIRS[pair]["twelve_data_symbol"]
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={symbol}&interval={interval}"
        f"&outputsize={outputsize}&apikey={TWELVE_DATA_API_KEY}"
    )
    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            async with session.get(url) as resp:
                data = await resp.json()

        if "values" not in data:
            logger.error(
                f"TwelveData returned no values for {pair}: "
                f"{data.get('message', data.get('status', 'unknown error'))}"
            )
            return None

        df = pd.DataFrame(data["values"])
        for col in ("open", "high", "low", "close"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # TwelveData returns newest-first; reverse so iloc[-1] == latest candle
        df = df.iloc[::-1].reset_index(drop=True)
        logger.debug(f"Fetched {len(df)} candles for {pair} ({interval})")
        return df

    except asyncio.TimeoutError:
        logger.error(f"Timeout fetching price data for {pair}")
        return None
    except Exception as exc:
        logger.error(f"Error fetching price data for {pair}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Technical indicators
# ---------------------------------------------------------------------------


def calculate_indicators(df: pd.DataFrame, params: dict) -> dict | None:
    """
    Compute RSI, MACD, SMA-20/50, Bollinger Bands, and ATR.

    Returns a flat dict of the latest values, or None on error.
    """
    try:
        df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()

        macd_obj = ta.trend.MACD(df["close"])
        df["macd"] = macd_obj.macd()
        df["macd_signal"] = macd_obj.macd_signal()

        df["ma_20"] = ta.trend.SMAIndicator(df["close"], window=20).sma_indicator()
        df["ma_50"] = ta.trend.SMAIndicator(df["close"], window=50).sma_indicator()

        bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
        df["bb_upper"] = bb.bollinger_hband()
        df["bb_lower"] = bb.bollinger_lband()

        df["atr"] = ta.volatility.AverageTrueRange(
            df["high"], df["low"], df["close"], window=14
        ).average_true_range()

        latest = df.iloc[-1]  # most-recent candle (oldest→newest sort)
        dp = params["decimal_places"]
        trend = "BULLISH" if latest["close"] > latest["ma_50"] else "BEARISH"

        return {
            "current_price": round(float(latest["close"]), dp),
            "rsi": round(float(latest["rsi"]), 2),
            "macd": float(latest["macd"]),
            "macd_signal": float(latest["macd_signal"]),
            "ma_20": round(float(latest["ma_20"]), dp),
            "ma_50": round(float(latest["ma_50"]), dp),
            "bb_upper": round(float(latest["bb_upper"]), dp),
            "bb_lower": round(float(latest["bb_lower"]), dp),
            "atr": round(float(latest["atr"]), dp),
            "trend": trend,
        }

    except Exception as exc:
        logger.error(f"Indicator calculation error: {exc}")
        return None


# ---------------------------------------------------------------------------
# AI signal analysis
# ---------------------------------------------------------------------------


async def generate_ai_analysis(
    symbol: str, indicators: dict, params: dict
) -> dict | None:
    """
    Call GPT-4o-mini to produce a structured trading signal.

    Retries up to 3 times on transient LLM errors.  Falls back to ATR-based
    TP/SL values if the model returns malformed or incomplete levels.
    """
    dp = params["decimal_places"]
    system_message = (
        "You are an elite institutional gold trader. "
        "Provide precise, actionable trading signals with strict risk management."
    )
    prompt = f"""Analyze {symbol} market data and provide a professional trading signal.

=== MARKET DATA ===
Current Price : {indicators['current_price']}
RSI           : {indicators['rsi']:.2f}
MACD          : {indicators['macd']:.6f}
MA-50         : {indicators['ma_50']:.{dp}f}
ATR (14)      : {indicators['atr']:.{dp}f}
Trend         : {indicators['trend']}

=== ATR MULTIPLIERS ===
SL ×{params['atr_multiplier_sl']}  TP1 ×{params['atr_multiplier_tp1']}  TP2 ×{params['atr_multiplier_tp2']}  TP3 ×{params['atr_multiplier_tp3']}
Minimum R:R   : {params['min_rr']}

=== REQUIRED OUTPUT (JSON ONLY — no markdown, no extra text) ===
{{"signal":"BUY"|"SELL"|"NEUTRAL","confidence":0-100,"entry_price":<number>,"tp_levels":[tp1,tp2,tp3],"sl_price":<number>,"analysis":"<150 words","risk_reward":<number>}}"""

    ai_response: str | None = None
    for attempt in range(1, 4):
        try:
            response = await litellm.acompletion(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt},
                ],
                api_key=OPENAI_API_KEY,
                timeout=30,
            )
            candidate = response.choices[0].message.content
            if candidate and len(candidate.strip()) > 10:
                ai_response = candidate
                break
            logger.warning(f"LLM attempt {attempt}/3 for {symbol}: empty response")
        except Exception as exc:
            logger.warning(f"LLM attempt {attempt}/3 for {symbol}: {exc}")
            await asyncio.sleep(1)

    if not ai_response:
        logger.error(f"All LLM attempts failed for {symbol} — skipping signal")
        return None

    # ------------------------------------------------------------------
    # Parse JSON — handle markdown fences and minor formatting issues
    # ------------------------------------------------------------------
    raw = ai_response.strip()

    # Strip ```json ... ``` fences if present
    fence = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()

    # If there's preamble text, extract the first {...} block
    if not raw.startswith("{"):
        brace = re.search(r"\{.*\}", raw, re.DOTALL)
        if brace:
            raw = brace.group(0)

    ai_data: dict | None = None
    for parse_attempt in range(3):
        try:
            if parse_attempt == 0:
                ai_data = json.loads(raw)
            elif parse_attempt == 1:
                # Fix common JSON issues: trailing commas, smart quotes
                fixed = re.sub(r",\s*}", "}", raw)
                fixed = re.sub(r",\s*]", "]", fixed)
                fixed = re.sub(r'"\s*\n\s*"', '",\n"', fixed)
                fixed = re.sub(r"(\d)\s*\n\s*\"", r'\1,\n"', fixed)
                fixed = fixed.replace("'", '"')
                ai_data = json.loads(fixed)
            else:
                # Last resort: regex extraction of individual fields
                sig_m = re.search(r'"signal"\s*:\s*"(\w+)"', raw)
                conf_m = re.search(r'"confidence"\s*:\s*([\d.]+)', raw)
                entry_m = re.search(r'"entry_price"\s*:\s*([\d.]+)', raw)
                analysis_m = re.search(r'"analysis"\s*:\s*"([^"]*)"', raw)
                ai_data = {
                    "signal": sig_m.group(1) if sig_m else "NEUTRAL",
                    "confidence": float(conf_m.group(1)) if conf_m else 50.0,
                    "entry_price": (
                        float(entry_m.group(1))
                        if entry_m
                        else indicators["current_price"]
                    ),
                    "analysis": (
                        analysis_m.group(1) if analysis_m else "AI analysis unavailable"
                    ),
                    "tp_levels": [],
                    "sl_price": 0,
                }
            break
        except Exception:
            if parse_attempt == 2:
                logger.warning(f"All JSON parse attempts failed for {symbol}")

    if not ai_data:
        return None

    # ------------------------------------------------------------------
    # Validate / repair TP levels and SL using ATR fallback
    # ------------------------------------------------------------------
    entry = float(ai_data.get("entry_price") or indicators["current_price"])
    signal_type: str = str(ai_data.get("signal", "NEUTRAL")).upper()
    tp_levels: list = ai_data.get("tp_levels", [])
    atr = indicators["atr"]

    # Rebuild TP levels if missing, wrong count, or all identical
    if signal_type != "NEUTRAL" and (
        len(tp_levels) != 3 or len(set(tp_levels)) != 3
    ):
        direction = 1 if signal_type == "BUY" else -1
        tp_levels = [
            round(entry + direction * atr * params["atr_multiplier_tp1"], dp),
            round(entry + direction * atr * params["atr_multiplier_tp2"], dp),
            round(entry + direction * atr * params["atr_multiplier_tp3"], dp),
        ]
        logger.debug(f"ATR fallback TP levels applied for {symbol}: {tp_levels}")

    ai_data["tp_levels"] = tp_levels
    ai_data["signal"] = signal_type
    ai_data["entry_price"] = entry

    # Repair SL if it is on the wrong side of entry or missing
    sl_price = float(ai_data.get("sl_price") or 0)
    if signal_type == "BUY" and (sl_price == 0 or sl_price >= entry):
        sl_price = round(entry - atr * params["atr_multiplier_sl"], dp)
        logger.debug(f"ATR fallback SL applied for {symbol} BUY: {sl_price}")
    elif signal_type == "SELL" and (sl_price == 0 or sl_price <= entry):
        sl_price = round(entry + atr * params["atr_multiplier_sl"], dp)
        logger.debug(f"ATR fallback SL applied for {symbol} SELL: {sl_price}")
    ai_data["sl_price"] = sl_price

    # Ensure risk_reward is a number
    rr = ai_data.get("risk_reward", params["min_rr"])
    if not isinstance(rr, (int, float)):
        rr = params["min_rr"]
    ai_data["risk_reward"] = rr

    return ai_data


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


def _sanitize_html(text: str) -> str:
    """Escape HTML special characters for Telegram HTML parse mode."""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


async def send_signal_to_telegram(
    *,
    pair: str,
    signal_type: str,
    entry_price: float,
    tp_levels: list,
    sl_price: float,
    confidence: float,
    risk_reward: float,
    analysis: str,
) -> None:
    """
    Send a two-part signal message to the configured Telegram channel.

    Part 1 — plain-text copier-compatible block (no parse_mode).
    Part 2 — HTML-formatted analysis block.

    Uses the module-level Bot singleton to avoid repeated handshakes.
    Errors are logged but never raised so a Telegram failure never
    prevents the signal from being stored in MongoDB.
    """
    if not _telegram_bot:
        logger.warning("Telegram bot not initialised — signal not sent")
        return

    signal_emoji = "🟢" if signal_type == "BUY" else "🔴"
    action = signal_type.capitalize()

    # ±0.50 entry range for TSCopier smart-entry execution
    entry_lo = round(entry_price - 0.50, 2)
    entry_hi = round(entry_price + 0.50, 2)

    copier_block = (
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

    safe_analysis = _sanitize_html(analysis or "")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    info_block = (
        f"<b>📊 R:R:</b> 1:{risk_reward}  "
        f"<b>⚡ AI Confidence:</b> {confidence}%\n"
        f"<b>📝</b> {safe_analysis}\n"
        f"<i>⏰ {timestamp} | Grandcom Gold ML Engine</i>"
    )

    try:
        await _telegram_bot.send_message(
            chat_id=TELEGRAM_GOLD_CHANNEL_ID,
            text=copier_block,
        )
        await _telegram_bot.send_message(
            chat_id=TELEGRAM_GOLD_CHANNEL_ID,
            text=info_block,
            parse_mode="HTML",
        )
        logger.info(
            f"✅ Telegram signal sent → channel {TELEGRAM_GOLD_CHANNEL_ID}: "
            f"{pair} {signal_type} @ {entry_price}"
        )
    except TelegramError as exc:
        logger.error(
            f"❌ Telegram send failed for {pair} {signal_type}: {exc} "
            f"(channel_id={TELEGRAM_GOLD_CHANNEL_ID})"
        )
    except Exception as exc:
        logger.error(f"❌ Unexpected error sending Telegram message: {exc}")


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------


async def generate_gold_signal(pair: str) -> None:
    """
    Full pipeline for one pair:
      1. Fetch 4H candles from TwelveData
      2. Calculate technical indicators
      3. Ask GPT-4o-mini for a signal
      4. Validate confidence threshold
      5. Persist to MongoDB
      6. Send to Telegram
    """
    params = GOLD_PAIRS[pair]
    logger.info(f"📊 [{pair}] Starting signal generation")

    # 1. Price data
    df = await get_price_data(pair, interval="4h", outputsize=100)
    if df is None or len(df) < 52:  # need ≥52 candles for MA-50 + buffer
        logger.warning(f"[{pair}] Insufficient candle data — skipping")
        return

    # 2. Indicators
    indicators = calculate_indicators(df, params)
    if not indicators:
        logger.warning(f"[{pair}] Indicator calculation failed — skipping")
        return

    logger.info(
        f"[{pair}] Price={indicators['current_price']}  "
        f"RSI={indicators['rsi']}  ATR={indicators['atr']}  "
        f"Trend={indicators['trend']}"
    )

    # 3. AI analysis
    ai_analysis = await generate_ai_analysis(pair, indicators, params)
    if not ai_analysis:
        logger.warning(f"[{pair}] AI analysis returned nothing — skipping")
        return

    signal_type: str = ai_analysis.get("signal", "NEUTRAL")
    if signal_type == "NEUTRAL":
        logger.info(f"[{pair}] AI returned NEUTRAL — no trade")
        return

    # 4. Confidence gate
    confidence = float(ai_analysis.get("confidence", 0))
    if confidence < params["min_confidence"]:
        logger.info(
            f"[{pair}] Confidence {confidence:.1f}% below threshold "
            f"{params['min_confidence']}% — skipping"
        )
        return

    entry_price: float = ai_analysis["entry_price"]
    tp_levels: list = ai_analysis["tp_levels"]
    sl_price: float = ai_analysis["sl_price"]
    risk_reward: float = ai_analysis.get("risk_reward", params["min_rr"])
    analysis_text: str = ai_analysis.get("analysis", "")

    # 5. Persist to MongoDB
    signal_doc = {
        "pair": pair,
        "type": signal_type,
        "entry_price": entry_price,
        "current_price": indicators["current_price"],
        "tp_levels": tp_levels,
        "sl_price": sl_price,
        "confidence": round(confidence, 1),
        "analysis": analysis_text,
        "risk_reward": risk_reward,
        "timeframe": "4H",
        "status": "ACTIVE",
        "created_at": datetime.now(timezone.utc),
    }
    try:
        await get_db().gold_signals.insert_one(signal_doc)
        logger.info(f"[{pair}] Signal stored in MongoDB")
    except Exception as exc:
        logger.error(f"[{pair}] MongoDB insert failed: {exc}")
        # Continue — still attempt Telegram delivery

    # 6. Telegram
    await send_signal_to_telegram(
        pair=pair,
        signal_type=signal_type,
        entry_price=entry_price,
        tp_levels=tp_levels,
        sl_price=sl_price,
        confidence=round(confidence, 1),
        risk_reward=risk_reward,
        analysis=analysis_text,
    )

    logger.info(
        f"✅ [{pair}] {signal_type} @ {entry_price} | "
        f"TP={tp_levels} | SL={sl_price} | "
        f"Conf={confidence:.1f}% | R:R=1:{risk_reward}"
    )


async def run_gold_signals() -> None:
    """Run signal generation for all configured pairs sequentially."""
    logger.info("🥇 Gold signal cycle starting…")
    for pair in GOLD_PAIRS:
        try:
            await generate_gold_signal(pair)
        except Exception as exc:
            logger.error(f"Unhandled error in generate_gold_signal({pair}): {exc}")
        # Brief pause between pairs to avoid rate-limiting
        await asyncio.sleep(3)
    logger.info("🥇 Gold signal cycle complete")


# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------

scheduler = AsyncIOScheduler(timezone="UTC")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup:
      - Validate required env vars
      - Connect to MongoDB
      - Initialise Telegram Bot singleton
      - Schedule recurring signal generation
      - Run one immediate cycle

    Shutdown:
      - Stop scheduler
      - Close MongoDB connection
    """
    global _mongo_client, _db, _telegram_bot

    # --- Validate env vars ---
    missing = [
        name
        for name, val in [
            ("MONGO_URL", MONGO_URL),
            ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
            ("TWELVE_DATA_API_KEY", TWELVE_DATA_API_KEY),
            ("OPENAI_API_KEY", OPENAI_API_KEY),
        ]
        if not val
    ]
    if missing:
        logger.error(f"Missing required environment variables: {missing}")
        # Continue startup so Railway health checks can still pass,
        # but signal generation will be a no-op.

    # --- MongoDB ---
    if MONGO_URL:
        try:
            _mongo_client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
            _db = _mongo_client[DB_NAME]
            # Ping to confirm connectivity
            await _db.command("ping")
            logger.info(f"✅ MongoDB connected — database: {DB_NAME}")
        except Exception as exc:
            logger.error(f"❌ MongoDB connection failed: {exc}")
            _mongo_client = None
            _db = None
    else:
        logger.warning("MONGO_URL not set — signals will not be persisted")

    # --- Telegram Bot ---
    if TELEGRAM_BOT_TOKEN:
        try:
            _telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN)
            bot_info = await _telegram_bot.get_me()
            logger.info(
                f"✅ Telegram bot connected: @{bot_info.username} (id={bot_info.id})"
            )
            logger.info(
                f"   Sending signals to channel_id={TELEGRAM_GOLD_CHANNEL_ID}"
            )
        except TelegramError as exc:
            logger.error(f"❌ Telegram bot initialisation failed: {exc}")
            _telegram_bot = None
    else:
        logger.warning("TELEGRAM_BOT_TOKEN not set — Telegram delivery disabled")

    # --- Scheduler ---
    scheduler.add_job(
        run_gold_signals,
        "interval",
        minutes=SIGNAL_INTERVAL_MINUTES,
        id="gold_signals",
        max_instances=1,  # prevent overlapping runs
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        f"🥇 Gold Signals Server ready — pairs={list(GOLD_PAIRS.keys())} "
        f"interval={SIGNAL_INTERVAL_MINUTES}min "
        f"channel={TELEGRAM_GOLD_CHANNEL_ID}"
    )

    # Run one cycle immediately on startup (non-blocking)
    asyncio.create_task(run_gold_signals())

    yield  # ← application runs here

    # --- Shutdown ---
    logger.info("🛑 Shutting down Gold Signals Server…")
    scheduler.shutdown(wait=False)
    if _mongo_client:
        _mongo_client.close()
    logger.info("🛑 Shutdown complete")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Grandcom Gold Signals",
    description="XAUUSD & XAUEUR swing trading signals via TwelveData + GPT-4o-mini",
    version="2.0.0",
    lifespan=lifespan,
)


@app.get("/api/health", tags=["monitoring"])
async def health():
    """
    Railway health-check endpoint.
    Returns service status, configured pairs, and scheduler state.
    """
    return {
        "status": "ok",
        "service": "gold_signals",
        "version": "2.0.0",
        "pairs": list(GOLD_PAIRS.keys()),
        "channel_id": TELEGRAM_GOLD_CHANNEL_ID,
        "interval_minutes": SIGNAL_INTERVAL_MINUTES,
        "scheduler_running": scheduler.running,
        "db_connected": _db is not None,
        "telegram_ready": _telegram_bot is not None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/gold/signals", tags=["signals"])
async def get_gold_signals(status: str | None = None, limit: int = 50):
    """
    Retrieve stored gold signals from MongoDB.

    Query params:
      - status: filter by signal status (ACTIVE, CLOSED, etc.)
      - limit:  max number of results (default 50, max 200)
    """
    db = get_db()
    limit = min(limit, 200)
    query: dict = {}
    if status:
        query["status"] = status.upper()

    signals = (
        await db.gold_signals.find(query, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
        .to_list(limit)
    )
    return {"signals": signals, "count": len(signals)}


# ---------------------------------------------------------------------------
# Entry point (local development)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "gold_server:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8002)),
        reload=False,
        log_level="info",
    )


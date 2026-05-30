"""
Grandcom Gold Signals Server v3.0
Institutional Multi-Strategy Hybrid Portfolio System
FastAPI application with 11 API endpoints
"""

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import pandas as pd
import ta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import Bot

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gold_server_v3")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MONGO_URL = os.environ.get("MONGO_URL", "")
DB_NAME = os.environ.get("DB_NAME", "gold_signals_v3")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("EMERGENT_LLM_KEY", "")

_raw_channel = os.environ.get("TELEGRAM_GOLD_CHANNEL_ID", "-1003834233408")
try:
    TELEGRAM_CHANNEL_ID: int | str = int(_raw_channel)
except ValueError:
    TELEGRAM_CHANNEL_ID = _raw_channel

SIGNAL_INTERVAL_MINUTES = int(os.environ.get("SIGNAL_INTERVAL_MINUTES", "2"))
MIN_CONFIDENCE = int(os.environ.get("MIN_CONFIDENCE", "60"))
ACCOUNT_BALANCE = float(os.environ.get("DEFAULT_ACCOUNT_BALANCE", "10000.0"))

# ---------------------------------------------------------------------------
# Trading Pairs
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
# MongoDB
# ---------------------------------------------------------------------------
_mongo_client: AsyncIOMotorClient | None = None
_db = None


def get_db():
    return _db


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
_bot: Bot | None = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        if not TELEGRAM_BOT_TOKEN:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
        _bot = Bot(token=TELEGRAM_BOT_TOKEN)
    return _bot


# ---------------------------------------------------------------------------
# Hybrid System (lazy import to avoid circular deps at module load)
# ---------------------------------------------------------------------------
_hybrid_system = None


def get_hybrid_system():
    global _hybrid_system
    if _hybrid_system is None:
        try:
            from ml_engine.hybrid_portfolio_system_v3 import HybridPortfolioSystemV3
            _hybrid_system = HybridPortfolioSystemV3(account_balance=ACCOUNT_BALANCE)
            logger.info("✅ HybridPortfolioSystemV3 loaded")
        except Exception as exc:
            logger.error(f"❌ Failed to load HybridPortfolioSystemV3: {exc}")
            _hybrid_system = None
    return _hybrid_system


# ---------------------------------------------------------------------------
# Price Data
# ---------------------------------------------------------------------------
async def fetch_ohlcv(pair: str, interval: str = "4h", outputsize: int = 100) -> pd.DataFrame | None:
    """Fetch OHLCV from TwelveData."""
    cfg = PAIRS[pair]
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={cfg['symbol']}&interval={interval}&outputsize={outputsize}"
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
        logger.info(f"[{pair}] Fetched {len(df)} {interval} candles")
        return df

    except Exception as exc:
        logger.error(f"[{pair}] fetch_ohlcv failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Technical Indicators
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
            "price":    round(float(last["close"]), dp),
            "rsi":      round(float(rsi.iloc[-1]), 2),
            "macd":     round(float(macd_obj.macd().iloc[-1]), 6),
            "macd_sig": round(float(macd_obj.macd_signal().iloc[-1]), 6),
            "ma20":     round(float(ma20.iloc[-1]), dp),
            "ma50":     round(float(ma50.iloc[-1]), dp),
            "atr":      round(float(atr.iloc[-1]), dp),
            "trend":    "BULLISH" if float(last["close"]) > float(ma50.iloc[-1]) else "BEARISH",
        }
    except Exception as exc:
        logger.error(f"compute_indicators failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# GPT Signal
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = (
    "You are an elite institutional gold trader using the Hybrid Portfolio System v3.0. "
    "Analyse the provided market data and return a JSON trading signal. "
    "Respond ONLY with valid JSON — no markdown, no extra text."
)

_USER_TEMPLATE = """\
Analyse {pair} (4H timeframe) — Hybrid Portfolio System v3.0

MARKET DATA
-----------
Price : {price}
RSI   : {rsi}
MACD  : {macd}  |  Signal: {macd_sig}
MA20  : {ma20}  |  MA50: {ma50}
ATR   : {atr}
Trend : {trend}
Regime: {regime}
SMC Score: {smc_score}/10
MTF Alignment: {mtf_alignment}%
Pivot Zone: {pivot_zone}

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


async def gpt_signal(pair: str, ind: dict, cfg: dict, hybrid_ctx: dict) -> dict | None:
    """Call GPT-4o-mini with hybrid system context."""
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
        regime=hybrid_ctx.get("regime", "UNKNOWN"),
        smc_score=hybrid_ctx.get("smc_score", 0),
        mtf_alignment=hybrid_ctx.get("mtf_alignment", 0),
        pivot_zone=hybrid_ctx.get("pivot_zone", "UNKNOWN"),
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
        return None

    return _parse_gpt_response(pair, raw_response)


def _parse_gpt_response(pair: str, raw: str) -> dict | None:
    """Parse GPT JSON response."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("{"):
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if brace:
            text = brace.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        fixed = re.sub(r",\s*}", "}", text)
        fixed = re.sub(r",\s*]", "]", fixed)
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    try:
        sig_m   = re.search(r'"signal"\s*:\s*"(\w+)"', text)
        conf_m  = re.search(r'"confidence"\s*:\s*([\d.]+)', text)
        entry_m = re.search(r'"entry_price"\s*:\s*([\d.]+)', text)
        anal_m  = re.search(r'"analysis"\s*:\s*"([^"]*)"', text)
        rr_m    = re.search(r'"risk_reward"\s*:\s*([\d.]+)', text)
        return {
            "signal":      sig_m.group(1)   if sig_m   else "NEUTRAL",
            "confidence":  float(conf_m.group(1))  if conf_m  else 50.0,
            "entry_price": float(entry_m.group(1)) if entry_m else 0.0,
            "analysis":    anal_m.group(1)  if anal_m  else "",
            "risk_reward": float(rr_m.group(1))    if rr_m    else 2.0,
            "tp_levels":   [],
            "sl_price":    0.0,
        }
    except Exception as exc:
        logger.error(f"[{pair}] JSON parse failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# TP/SL Levels
# ---------------------------------------------------------------------------
def build_levels(signal: str, entry: float, atr: float, cfg: dict) -> tuple[list[float], float]:
    dp = cfg["decimals"]
    if signal == "BUY":
        tps = [
            round(entry + atr * cfg["atr_tp1"], dp),
            round(entry + atr * cfg["atr_tp2"], dp),
            round(entry + atr * cfg["atr_tp3"], dp),
        ]
        sl = round(entry - atr * cfg["atr_sl"], dp)
    else:
        tps = [
            round(entry - atr * cfg["atr_tp1"], dp),
            round(entry - atr * cfg["atr_tp2"], dp),
            round(entry - atr * cfg["atr_tp3"], dp),
        ]
        sl = round(entry + atr * cfg["atr_sl"], dp)
    return tps, sl


# ---------------------------------------------------------------------------
# Telegram
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
    regime: str = "UNKNOWN",
    smc_score: int = 0,
    mtf_alignment: float = 0.0,
) -> None:
    """Send signal to Telegram with v3.0 context."""
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
            f"<b>🎯 Regime:</b> {regime}  "
            f"<b>📐 SMC:</b> {smc_score}/10  "
            f"<b>🔗 MTF:</b> {mtf_alignment:.0f}%\n"
            f"<b>📝</b> {_html_escape(analysis)}\n"
            f"<i>⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
            f"| Grandcom Gold Engine v3.0</i>"
        )

        await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=copier_msg)
        await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=info_msg, parse_mode="HTML")
        logger.info(f"[{pair}] Signal sent to Telegram channel {TELEGRAM_CHANNEL_ID}")

    except Exception as exc:
        logger.error(f"[{pair}] Telegram delivery failed: {exc}")


# ---------------------------------------------------------------------------
# Core Signal Generation
# ---------------------------------------------------------------------------
async def generate_signal(pair: str) -> None:
    """Full v3.0 pipeline: fetch → hybrid analysis → GPT → validate → store → send."""
    cfg = PAIRS[pair]
    logger.info(f"[{pair}] Starting v3.0 signal generation")

    # 1. Price data
    df = await fetch_ohlcv(pair, interval="4h", outputsize=100)
    if df is None or len(df) < 52:
        logger.warning(f"[{pair}] Insufficient candles, skipping")
        return

    # 2. Indicators
    ind = compute_indicators(df, cfg["decimals"])
    if ind is None:
        return

    # 3. Hybrid system analysis
    hybrid_ctx = {"regime": "UNKNOWN", "smc_score": 0, "mtf_alignment": 0, "pivot_zone": "UNKNOWN"}
    hybrid = get_hybrid_system()
    if hybrid is not None:
        try:
            hybrid_result = await hybrid.generate_signal(symbol=pair, df_4h=df)
            hybrid_ctx = {
                "regime": hybrid_result.get("regime", "UNKNOWN"),
                "smc_score": hybrid_result.get("smc_score", 0),
                "mtf_alignment": hybrid_result.get("mtf_alignment", 0),
                "pivot_zone": hybrid_result.get("pivot_zone", "UNKNOWN"),
                "hybrid_signal": hybrid_result.get("signal", "NEUTRAL"),
                "hybrid_confidence": hybrid_result.get("confidence", 0),
            }
            logger.info(
                f"[{pair}] Hybrid: signal={hybrid_ctx['hybrid_signal']} "
                f"regime={hybrid_ctx['regime']} smc={hybrid_ctx['smc_score']}/10 "
                f"mtf={hybrid_ctx['mtf_alignment']:.0f}%"
            )
        except Exception as exc:
            logger.error(f"[{pair}] Hybrid system error: {exc}")

    # 4. GPT analysis
    gpt = await gpt_signal(pair, ind, cfg, hybrid_ctx)
    if gpt is None:
        return

    signal_type = str(gpt.get("signal", "NEUTRAL")).upper()
    confidence  = float(gpt.get("confidence", 0))
    analysis    = str(gpt.get("analysis", ""))

    # 5. Filter
    if signal_type == "NEUTRAL" or signal_type not in ("BUY", "SELL"):
        logger.info(f"[{pair}] {signal_type} signal — no trade")
        return

    if confidence < MIN_CONFIDENCE:
        logger.info(f"[{pair}] Confidence {confidence}% < {MIN_CONFIDENCE}% — skipping")
        return

    # 6. Levels
    entry = float(gpt.get("entry_price") or ind["price"])
    if entry <= 0:
        entry = ind["price"]

    tps, sl = build_levels(signal_type, entry, ind["atr"], cfg)

    if signal_type == "BUY" and (tps[0] <= entry or sl >= entry):
        logger.warning(f"[{pair}] BUY geometry invalid — skipping")
        return
    if signal_type == "SELL" and (tps[0] >= entry or sl <= entry):
        logger.warning(f"[{pair}] SELL geometry invalid — skipping")
        return

    # 7. Risk/reward
    risk   = abs(entry - sl)
    reward = abs(tps[0] - entry)
    rr     = round(reward / risk, 1) if risk > 0 else 2.0

    # 8. Store in MongoDB
    db = get_db()
    if db is not None:
        try:
            doc = {
                "pair":           pair,
                "type":           signal_type,
                "entry_price":    entry,
                "current_price":  ind["price"],
                "tp_levels":      tps,
                "sl_price":       sl,
                "confidence":     round(confidence, 1),
                "analysis":       analysis,
                "risk_reward":    rr,
                "timeframe":      "4H",
                "status":         "ACTIVE",
                "indicators":     ind,
                "regime":         hybrid_ctx.get("regime", "UNKNOWN"),
                "smc_score":      hybrid_ctx.get("smc_score", 0),
                "mtf_alignment":  hybrid_ctx.get("mtf_alignment", 0),
                "pivot_zone":     hybrid_ctx.get("pivot_zone", "UNKNOWN"),
                "system_version": "3.0.0",
                "created_at":     datetime.now(timezone.utc),
            }
            result = await db.gold_signals.insert_one(doc)
            logger.info(f"[{pair}] Signal stored — id={result.inserted_id}")
        except Exception as exc:
            logger.error(f"[{pair}] MongoDB insert failed: {exc}")

    # 9. Send to Telegram
    await send_to_telegram(
        pair, signal_type, entry, tps, sl,
        round(confidence, 1), rr, analysis,
        regime=hybrid_ctx.get("regime", "UNKNOWN"),
        smc_score=hybrid_ctx.get("smc_score", 0),
        mtf_alignment=hybrid_ctx.get("mtf_alignment", 0),
    )

    logger.info(
        f"[{pair}] ✅ {signal_type} @ {entry} | "
        f"TP: {tps} | SL: {sl} | R:R 1:{rr} | Conf: {confidence}%"
    )


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
async def run_all_signals() -> None:
    logger.info("=== v3.0 Signal generation cycle START ===")
    for pair in PAIRS:
        try:
            await generate_signal(pair)
        except Exception as exc:
            logger.error(f"[{pair}] Unhandled error: {exc}", exc_info=True)
        await asyncio.sleep(2)
    logger.info("=== v3.0 Signal generation cycle END ===")


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mongo_client, _db

    # Startup validation
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
        logger.error(f"❌ Missing env vars: {missing}")
    else:
        logger.info("✅ All required environment variables present")

    # MongoDB
    if MONGO_URL:
        try:
            _mongo_client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
            _db = _mongo_client[DB_NAME]
            await _db.command("ping")
            logger.info(f"✅ MongoDB connected — db={DB_NAME}")
        except Exception as exc:
            logger.error(f"❌ MongoDB connection failed: {exc}")
            _db = None

    # Telegram
    if TELEGRAM_BOT_TOKEN:
        try:
            bot = get_bot()
            me = await bot.get_me()
            logger.info(f"✅ Telegram bot ready — @{me.username}")
        except Exception as exc:
            logger.error(f"❌ Telegram bot init failed: {exc}")

    # Hybrid system
    get_hybrid_system()

    # Scheduler
    scheduler.add_job(
        run_all_signals,
        "interval",
        minutes=SIGNAL_INTERVAL_MINUTES,
        id="gold_signals_v3",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        f"✅ Scheduler started — pairs={list(PAIRS.keys())} "
        f"interval={SIGNAL_INTERVAL_MINUTES}min"
    )

    asyncio.create_task(run_all_signals())

    yield

    scheduler.shutdown(wait=False)
    if _mongo_client:
        _mongo_client.close()
    logger.info("Gold Signals Server v3.0 shut down")


app = FastAPI(
    title="Grandcom Gold Signals v3.0",
    description="Institutional Multi-Strategy Hybrid Portfolio System",
    version="3.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Endpoint 1: Health Check
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

    hybrid = get_hybrid_system()
    system_status = hybrid.get_system_status() if hybrid else {"status": "unavailable"}

    jobs = [
        {"id": j.id, "next_run": str(j.next_run_time)}
        for j in scheduler.get_jobs()
    ]

    return {
        "status":            "ok",
        "service":           "gold_signals_v3",
        "version":           "3.0.0",
        "pairs":             list(PAIRS.keys()),
        "telegram_channel":  TELEGRAM_CHANNEL_ID,
        "scheduler_running": scheduler.running,
        "scheduler_jobs":    jobs,
        "mongo_connected":   mongo_ok,
        "system_components": system_status.get("total_components", 0),
        "timestamp":         datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 2: Get Signals
# ---------------------------------------------------------------------------
@app.get("/api/signals")
async def get_signals(
    status: Optional[str] = None,
    pair: Optional[str] = None,
    limit: int = Query(default=50, le=200),
):
    """Return stored signals with optional filtering."""
    db = get_db()
    if db is None:
        return {"error": "MongoDB not connected", "signals": [], "count": 0}

    query: dict = {}
    if status:
        query["status"] = status.upper()
    if pair:
        query["pair"] = pair.upper()

    signals = (
        await db.gold_signals
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
        .to_list(limit)
    )
    return {"signals": signals, "count": len(signals)}


# ---------------------------------------------------------------------------
# Endpoint 3: System Status
# ---------------------------------------------------------------------------
@app.get("/api/system/status")
async def system_status():
    """Get full hybrid system status."""
    hybrid = get_hybrid_system()
    if hybrid is None:
        return {"error": "Hybrid system not available", "version": "3.0.0"}
    return hybrid.get_system_status()


# ---------------------------------------------------------------------------
# Endpoint 4: Regime Analysis
# ---------------------------------------------------------------------------
@app.get("/api/analysis/regime/{pair}")
async def get_regime_analysis(pair: str):
    """Get current market regime for a pair."""
    pair = pair.upper()
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Pair {pair} not found")

    df = await fetch_ohlcv(pair, interval="4h", outputsize=100)
    if df is None:
        raise HTTPException(status_code=503, detail="Failed to fetch price data")

    hybrid = get_hybrid_system()
    if hybrid is None:
        raise HTTPException(status_code=503, detail="Hybrid system not available")

    try:
        features = hybrid.feature_engineer.extract_features(df)
        regime = hybrid.regime_detector.detect_regime(features)
        return {"pair": pair, "regime": regime, "timestamp": datetime.utcnow().isoformat()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Endpoint 5: SMC Analysis
# ---------------------------------------------------------------------------
@app.get("/api/analysis/smc/{pair}")
async def get_smc_analysis(pair: str):
    """Get SMC/ICT analysis for a pair."""
    pair = pair.upper()
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Pair {pair} not found")

    df = await fetch_ohlcv(pair, interval="4h", outputsize=100)
    if df is None:
        raise HTTPException(status_code=503, detail="Failed to fetch price data")

    hybrid = get_hybrid_system()
    if hybrid is None:
        raise HTTPException(status_code=503, detail="Hybrid system not available")

    return hybrid.smc_ict.analyze(df, pair, timeframe="4h")


# ---------------------------------------------------------------------------
# Endpoint 6: Pivot Points
# ---------------------------------------------------------------------------
@app.get("/api/analysis/pivots/{pair}")
async def get_pivot_analysis(
    pair: str,
    method: str = Query(default="standard", regex="^(standard|fibonacci|woodie|camarilla)$"),
):
    """Get pivot point analysis for a pair."""
    pair = pair.upper()
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Pair {pair} not found")

    df = await fetch_ohlcv(pair, interval="1day", outputsize=10)
    if df is None:
        df = await fetch_ohlcv(pair, interval="4h", outputsize=50)
    if df is None:
        raise HTTPException(status_code=503, detail="Failed to fetch price data")

    hybrid = get_hybrid_system()
    if hybrid is None:
        raise HTTPException(status_code=503, detail="Hybrid system not available")

    return hybrid.pivot_analyzer.analyze(df, pair, method=method, use_all_methods=True)


# ---------------------------------------------------------------------------
# Endpoint 7: MTF Confirmation
# ---------------------------------------------------------------------------
@app.get("/api/analysis/mtf/{pair}")
async def get_mtf_analysis(pair: str):
    """Get multi-timeframe confirmation analysis."""
    pair = pair.upper()
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Pair {pair} not found")

    hybrid = get_hybrid_system()
    if hybrid is None:
        raise HTTPException(status_code=503, detail="Hybrid system not available")

    try:
        result = await hybrid.mtf_confirmation.analyze(pair)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Endpoint 8: Full Hybrid Analysis
# ---------------------------------------------------------------------------
@app.get("/api/analysis/hybrid/{pair}")
async def get_hybrid_analysis(pair: str):
    """Run full hybrid portfolio system analysis for a pair."""
    pair = pair.upper()
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Pair {pair} not found")

    df = await fetch_ohlcv(pair, interval="4h", outputsize=100)
    if df is None:
        raise HTTPException(status_code=503, detail="Failed to fetch price data")

    hybrid = get_hybrid_system()
    if hybrid is None:
        raise HTTPException(status_code=503, detail="Hybrid system not available")

    try:
        result = await hybrid.generate_signal(symbol=pair, df_4h=df)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Endpoint 9: Portfolio State
# ---------------------------------------------------------------------------
@app.get("/api/portfolio/state")
async def get_portfolio_state():
    """Get current portfolio state."""
    hybrid = get_hybrid_system()
    if hybrid is None:
        return {"error": "Hybrid system not available"}
    return hybrid.portfolio_manager.get_state(ACCOUNT_BALANCE)


# ---------------------------------------------------------------------------
# Endpoint 10: Performance Attribution
# ---------------------------------------------------------------------------
@app.get("/api/performance")
async def get_performance(lookback_days: int = Query(default=30, ge=1, le=365)):
    """Get performance attribution analysis."""
    db = get_db()
    if db is None:
        return {"error": "MongoDB not connected"}

    try:
        trades = (
            await db.gold_signals
            .find({"status": {"$in": ["CLOSED", "WIN", "LOSS"]}}, {"_id": 0})
            .sort("created_at", -1)
            .limit(500)
            .to_list(500)
        )

        hybrid = get_hybrid_system()
        if hybrid is None:
            return {"error": "Hybrid system not available"}

        return hybrid.performance.analyze(trades, ACCOUNT_BALANCE, lookback_days)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Endpoint 11: Trigger Signal Now
# ---------------------------------------------------------------------------
@app.post("/api/signals/trigger")
async def trigger_signal(pair: Optional[str] = None):
    """Manually trigger signal generation."""
    if pair:
        pair = pair.upper()
        if pair not in PAIRS:
            raise HTTPException(status_code=404, detail=f"Pair {pair} not found")
        asyncio.create_task(generate_signal(pair))
        return {"message": f"Signal generation triggered for {pair}", "timestamp": datetime.utcnow().isoformat()}
    else:
        asyncio.create_task(run_all_signals())
        return {"message": "Signal generation triggered for all pairs", "timestamp": datetime.utcnow().isoformat()}


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8002)))

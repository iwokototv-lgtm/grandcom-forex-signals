"""
Grandcom Gold Signals Server v3.0
Institutional Multi-Strategy Hybrid Portfolio System
XAUUSD & XAUEUR — Railway deployment ready
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiohttp
import pandas as pd
import ta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import Bot

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
from config import (
    MONGO_URL, DB_NAME, TELEGRAM_BOT_TOKEN, TWELVE_DATA_API_KEY,
    OPENAI_API_KEY, TELEGRAM_CHANNEL_ID, PAIRS, SIGNAL_INTERVAL_MINUTES,
    MIN_CONFIDENCE, MTF_MIN_CONFLUENCE, ACCOUNT_BALANCE, VERSION, SERVICE_NAME,
    LOG_LEVEL, PORT,
)

# ---------------------------------------------------------------------------
# ML Engine
# ---------------------------------------------------------------------------
from ml_engine.hybrid_portfolio_v2 import HybridPortfolioSystemV2
from ml_engine.performance_attributor import performance_attributor
from ml_engine.trade_journal import trade_journal
from ml_engine.economic_calendar import economic_calendar

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gold_server_v3")

# ---------------------------------------------------------------------------
# Global singletons
# ---------------------------------------------------------------------------
_mongo_client: AsyncIOMotorClient | None = None
_db = None
_bot: Bot | None = None
_scheduler = AsyncIOScheduler()

# One HybridPortfolioSystem per server instance
_hybrid_system: HybridPortfolioSystemV2 | None = None


def get_db():
    return _db


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        if not TELEGRAM_BOT_TOKEN:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
        _bot = Bot(token=TELEGRAM_BOT_TOKEN)
    return _bot


def get_hybrid() -> HybridPortfolioSystemV2:
    global _hybrid_system
    if _hybrid_system is None:
        _hybrid_system = HybridPortfolioSystemV2(
            account_equity=ACCOUNT_BALANCE,
            min_confidence=MIN_CONFIDENCE,
            min_mtf_confluence=MTF_MIN_CONFLUENCE,
        )
    return _hybrid_system


# ---------------------------------------------------------------------------
# Price Data Fetching
# ---------------------------------------------------------------------------
SYMBOL_MAP = {
    "XAUUSD": "XAU/USD",
    "XAUEUR": "XAU/EUR",
}

TIMEFRAME_MAP = {
    "4h": "4h",
    "1h": "1h",
    "1day": "1day",
    "1week": "1week",
}


async def fetch_ohlcv(
    pair: str,
    interval: str = "4h",
    outputsize: int = 100,
) -> pd.DataFrame | None:
    """Fetch OHLCV data from TwelveData for a given pair and interval."""
    symbol = SYMBOL_MAP.get(pair, pair.replace("XAU", "XAU/"))
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={symbol}&interval={interval}&outputsize={outputsize}"
        f"&apikey={TWELVE_DATA_API_KEY}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                data = await resp.json()

        if "values" not in data:
            logger.error(f"[{pair}/{interval}] TwelveData error: {data.get('message', data)}")
            return None

        df = pd.DataFrame(data["values"])
        for col in ("open", "high", "low", "close"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.iloc[::-1].reset_index(drop=True)  # oldest → newest
        logger.info(f"[{pair}/{interval}] Fetched {len(df)} candles")
        return df

    except Exception as exc:
        logger.error(f"[{pair}/{interval}] fetch_ohlcv failed: {exc}")
        return None


async def fetch_all_timeframes(pair: str) -> dict[str, pd.DataFrame | None]:
    """Fetch 4H, 1H, Daily, and Weekly data for a pair concurrently."""
    tasks = {
        "4h": fetch_ohlcv(pair, "4h", 100),
        "1h": fetch_ohlcv(pair, "1h", 100),
        "1day": fetch_ohlcv(pair, "1day", 60),
        "1week": fetch_ohlcv(pair, "1week", 52),
    }
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    return {
        tf: (r if isinstance(r, pd.DataFrame) else None)
        for tf, r in zip(tasks.keys(), results)
    }


# ---------------------------------------------------------------------------
# Indicators (for legacy compatibility and health checks)
# ---------------------------------------------------------------------------
def compute_indicators(df: pd.DataFrame, decimals: int = 2) -> dict | None:
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
        return {
            "price": round(float(last["close"]), decimals),
            "rsi": round(float(rsi.iloc[-1]), 2),
            "macd": round(float(macd_obj.macd().iloc[-1]), 6),
            "macd_sig": round(float(macd_obj.macd_signal().iloc[-1]), 6),
            "ma20": round(float(ma20.iloc[-1]), decimals),
            "ma50": round(float(ma50.iloc[-1]), decimals),
            "atr": round(float(atr.iloc[-1]), decimals),
            "trend": "BULLISH" if float(last["close"]) > float(ma50.iloc[-1]) else "BEARISH",
        }
    except Exception as exc:
        logger.error(f"compute_indicators failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Telegram Delivery
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
    strategy: str = "",
    regime: str = "",
    lots: float = 0.0,
) -> None:
    """Send signal to Telegram channel in copier + analysis format."""
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
            f"TP1: {tps[0] if len(tps) > 0 else 'N/A'}\n"
            f"TP2: {tps[1] if len(tps) > 1 else 'N/A'}\n"
            f"TP3: {tps[2] if len(tps) > 2 else 'N/A'}\n"
            f"\n"
            f"SL: {sl}\n"
        )

        strategy_line = f" | <b>Strategy:</b> {strategy}" if strategy else ""
        regime_line = f" | <b>Regime:</b> {regime}" if regime else ""
        lots_line = f" | <b>Lots:</b> {lots}" if lots > 0 else ""

        info_msg = (
            f"<b>📊 R:R:</b> 1:{rr}  "
            f"<b>⚡ Confidence:</b> {confidence}%"
            f"{strategy_line}{regime_line}{lots_line}\n"
            f"<b>📝</b> {_html_escape(analysis)}\n"
            f"<i>⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
            f"| Grandcom Gold Engine v{VERSION}</i>"
        )

        await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=copier_msg)
        await bot.send_message(
            chat_id=TELEGRAM_CHANNEL_ID, text=info_msg, parse_mode="HTML"
        )
        logger.info(f"[{pair}] Signal sent to Telegram channel {TELEGRAM_CHANNEL_ID}")

    except Exception as exc:
        logger.error(f"[{pair}] Telegram delivery failed: {exc}")


# ---------------------------------------------------------------------------
# Core Signal Generation — v3 Pipeline
# ---------------------------------------------------------------------------
async def generate_signal_v3(pair: str) -> None:
    """
    Full v3 pipeline: fetch all timeframes → hybrid portfolio system → store → send.
    """
    cfg = PAIRS[pair]
    logger.info(f"[{pair}] v3 signal generation start")

    # 1. Fetch all timeframes
    frames = await fetch_all_timeframes(pair)
    df_4h = frames.get("4h")
    df_1h = frames.get("1h")
    df_daily = frames.get("1day")
    df_weekly = frames.get("1week")

    if df_4h is None or len(df_4h) < 52:
        logger.warning(f"[{pair}] Insufficient 4H data, skipping")
        return

    # 2. Update correlation engine with latest prices
    hybrid = get_hybrid()
    if df_4h is not None:
        hybrid.corr_engine.update_prices(pair, df_4h["close"])

    # 3. Run hybrid portfolio pipeline
    result = await hybrid.generate_signal(
        symbol=pair,
        df_4h=df_4h,
        df_1h=df_1h,
        df_daily=df_daily,
        df_weekly=df_weekly,
    )

    if not result.get("approved"):
        logger.info(
            f"[{pair}] Signal rejected [{result.get('stage', '?')}]: "
            f"{result.get('reason', 'unknown')}"
        )
        return

    signal_type = result["signal"]
    confidence = result["confidence"]
    entry = result["entry"]
    tps = result["tp_levels"]
    sl = result["sl"]
    lots = result.get("lots", 0.01)
    strategy = result.get("strategy", "")
    regime = result.get("regime", "")
    analysis = result.get("analysis", "")

    # Ensure we have valid levels
    if entry <= 0:
        ind = compute_indicators(df_4h, cfg["decimals"])
        if ind is None:
            logger.warning(f"[{pair}] Cannot compute fallback indicators")
            return
        entry = ind["price"]
        atr = ind["atr"]
        if signal_type == "BUY":
            tps = [
                round(entry + atr * cfg["atr_tp1"], 2),
                round(entry + atr * cfg["atr_tp2"], 2),
                round(entry + atr * cfg["atr_tp3"], 2),
            ]
            sl = round(entry - atr * cfg["atr_sl"], 2)
        else:
            tps = [
                round(entry - atr * cfg["atr_tp1"], 2),
                round(entry - atr * cfg["atr_tp2"], 2),
                round(entry - atr * cfg["atr_tp3"], 2),
            ]
            sl = round(entry + atr * cfg["atr_sl"], 2)

    # Geometry validation
    if signal_type == "BUY" and (not tps or tps[0] <= entry or sl >= entry):
        logger.warning(f"[{pair}] BUY geometry invalid — skipping")
        return
    if signal_type == "SELL" and (not tps or tps[0] >= entry or sl <= entry):
        logger.warning(f"[{pair}] SELL geometry invalid — skipping")
        return

    # Risk/reward
    risk = abs(entry - sl)
    reward = abs(tps[0] - entry) if tps else 0
    rr = round(reward / risk, 1) if risk > 0 else 2.0

    # 4. Store in MongoDB
    db = get_db()
    trade_id = str(uuid.uuid4())
    if db is not None:
        try:
            doc = {
                "trade_id": trade_id,
                "pair": pair,
                "type": signal_type,
                "entry_price": entry,
                "current_price": entry,
                "tp_levels": tps,
                "sl_price": sl,
                "confidence": round(confidence, 1),
                "analysis": analysis,
                "risk_reward": rr,
                "lots": lots,
                "strategy": strategy,
                "regime": regime,
                "mtf_confluence": result.get("mtf_confluence", 0),
                "mtf_direction": result.get("mtf_direction", ""),
                "timeframe": "4H",
                "status": "ACTIVE",
                "version": VERSION,
                "created_at": datetime.now(timezone.utc),
            }
            await db.gold_signals.insert_one(doc)
            logger.info(f"[{pair}] Signal stored — trade_id={trade_id}")
        except Exception as exc:
            logger.error(f"[{pair}] MongoDB insert failed: {exc}")
    else:
        logger.warning(f"[{pair}] MongoDB not available — signal not stored")

    # 5. Register in portfolio manager
    hybrid.portfolio_mgr.add_position(
        {
            "trade_id": trade_id,
            "symbol": pair,
            "direction": signal_type,
            "lots": lots,
            "risk_usd": result.get("risk_usd", 0.0),
            "strategy": strategy,
            "entry_price": entry,
        }
    )

    # 6. Open in trade journal
    trade_journal.open_trade(
        trade_id=trade_id,
        symbol=pair,
        direction=signal_type,
        entry_price=entry,
        tp_levels=tps,
        sl=sl,
        lots=lots,
        strategy=strategy,
        regime=regime,
        confidence=confidence,
        timeframe="4H",
        signal_analysis=analysis,
    )

    # 7. Send to Telegram
    await send_to_telegram(
        pair, signal_type, entry, tps, sl,
        round(confidence, 1), rr, analysis,
        strategy=strategy, regime=regime, lots=lots,
    )

    logger.info(
        f"[{pair}] ✅ {signal_type} @ {entry} | "
        f"TP: {tps} | SL: {sl} | R:R 1:{rr} | "
        f"Conf: {confidence}% | Strategy: {strategy} | Regime: {regime} | "
        f"Lots: {lots}"
    )


# ---------------------------------------------------------------------------
# Scheduler Job
# ---------------------------------------------------------------------------
async def run_all_signals() -> None:
    logger.info("=== v3 Signal generation cycle START ===")
    for pair in PAIRS:
        try:
            await generate_signal_v3(pair)
        except Exception as exc:
            logger.error(
                f"[{pair}] Unhandled error in generate_signal_v3: {exc}",
                exc_info=True,
            )
        await asyncio.sleep(3)
    logger.info("=== v3 Signal generation cycle END ===")


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
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
        logger.error(f"❌ Missing required environment variables: {missing}")
        logger.error("Server will start but signal generation will fail until these are set.")
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
            logger.info(f"✅ Telegram bot ready — @{me.username} → channel {TELEGRAM_CHANNEL_ID}")
        except Exception as exc:
            logger.error(f"❌ Telegram bot init failed: {exc}")

    # Hybrid portfolio system
    try:
        hybrid = get_hybrid()
        logger.info(
            f"✅ Hybrid Portfolio System v2.0 initialised — "
            f"equity={hybrid.account_equity:,.0f} "
            f"min_confidence={hybrid.min_confidence}%"
        )
    except Exception as exc:
        logger.error(f"❌ Hybrid system init failed: {exc}")

    # Scheduler
    _scheduler.add_job(
        run_all_signals,
        "interval",
        minutes=SIGNAL_INTERVAL_MINUTES,
        id="gold_signals_v3",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info(
        f"✅ Scheduler started — pairs={list(PAIRS.keys())} "
        f"interval={SIGNAL_INTERVAL_MINUTES}min"
    )

    # Run immediately on startup
    asyncio.create_task(run_all_signals())

    yield

    # Shutdown
    _scheduler.shutdown(wait=False)
    if _mongo_client:
        _mongo_client.close()
    logger.info("Gold Signals Server v3.0 shut down")


app = FastAPI(
    title="Grandcom Gold Signals",
    version=VERSION,
    description="Institutional Multi-Strategy Hybrid Portfolio System — XAUUSD & XAUEUR",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# API Endpoints
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
        for j in _scheduler.get_jobs()
    ]

    hybrid = get_hybrid()
    dd_status = hybrid.dd_manager.get_status()

    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": VERSION,
        "pairs": list(PAIRS.keys()),
        "telegram_channel": TELEGRAM_CHANNEL_ID,
        "scheduler_running": _scheduler.running,
        "scheduler_jobs": jobs,
        "mongo_connected": mongo_ok,
        "drawdown_status": dd_status.get("drawdown_regime", "NORMAL"),
        "can_trade": dd_status.get("can_trade", True),
        "open_positions": len(hybrid.portfolio_mgr.get_open_positions()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/signals")
async def get_signals(
    status: str | None = None,
    pair: str | None = None,
    strategy: str | None = None,
    limit: int = Query(default=50, le=200),
):
    """Return stored signals with optional filters."""
    db = get_db()
    if db is None:
        return {"error": "MongoDB not connected", "signals": [], "count": 0}

    query: dict = {}
    if status:
        query["status"] = status.upper()
    if pair:
        query["pair"] = pair.upper()
    if strategy:
        query["strategy"] = strategy.upper()

    signals = (
        await db.gold_signals
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
        .to_list(limit)
    )
    return {"signals": signals, "count": len(signals)}


@app.get("/api/portfolio")
async def get_portfolio():
    """Return current portfolio state."""
    hybrid = get_hybrid()
    return hybrid.portfolio_mgr.portfolio_report()


@app.get("/api/portfolio/status")
async def get_portfolio_status():
    """Return full hybrid system status."""
    hybrid = get_hybrid()
    return hybrid.get_system_status()


@app.get("/api/regime")
async def get_regime(pair: str = "XAUUSD"):
    """Return current market regime for a pair."""
    if pair not in PAIRS:
        raise HTTPException(status_code=400, detail=f"Unknown pair: {pair}")

    df = await fetch_ohlcv(pair, "4h", 100)
    if df is None or len(df) < 60:
        raise HTTPException(status_code=503, detail="Insufficient price data")

    hybrid = get_hybrid()
    features = hybrid.feature_engineer.extract_features(df, pair)
    if features is None:
        raise HTTPException(status_code=503, detail="Feature extraction failed")

    regime = hybrid.regime_detector.detect_regime(features)
    return {
        "pair": pair,
        "regime": regime,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/mtf")
async def get_mtf(pair: str = "XAUUSD"):
    """Return multi-timeframe analysis for a pair."""
    if pair not in PAIRS:
        raise HTTPException(status_code=400, detail=f"Unknown pair: {pair}")

    hybrid = get_hybrid()
    result = await hybrid.mtf_analyzer.analyze(pair)
    return result


@app.get("/api/correlation")
async def get_correlation():
    """Return correlation engine summary and portfolio correlation matrix."""
    hybrid = get_hybrid()
    symbols = list(PAIRS.keys())
    matrix = hybrid.corr_engine.portfolio_correlation_matrix(symbols)
    summary = hybrid.corr_engine.get_summary(symbols)
    return {
        "correlation_matrix": matrix,
        "summary": summary,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/performance")
async def get_performance(days: int = Query(default=30, le=365)):
    """Return performance attribution report."""
    report = performance_attributor.full_report(lookback_days=days)
    return report


@app.get("/api/journal")
async def get_journal(limit: int = Query(default=50, le=200)):
    """Return trade journal summary and recent trades."""
    return {
        "summary": trade_journal.summary(),
        "open_trades": trade_journal.get_open_trades(),
        "recent_closed": trade_journal.get_closed_trades(limit=limit),
        "patterns": trade_journal.pattern_analysis(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/calendar")
async def get_calendar(pair: str = "XAUUSD", hours: int = 24):
    """Return upcoming economic events relevant to the pair."""
    events = await economic_calendar.get_upcoming_events(hours_ahead=hours, symbol=pair)
    safe_check = await economic_calendar.is_safe_to_trade(pair)
    return {
        "pair": pair,
        "safe_to_trade": safe_check,
        "upcoming_events": [
            {k: v for k, v in e.items() if k != "datetime_utc"}
            | {"datetime_utc": e["datetime_utc"].isoformat() if e.get("datetime_utc") else None}
            for e in events
        ],
        "hours_ahead": hours,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/signals/generate")
async def trigger_signal_generation():
    """Manually trigger a signal generation cycle."""
    asyncio.create_task(run_all_signals())
    return {
        "status": "triggered",
        "message": "Signal generation cycle started",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/drawdown")
async def get_drawdown():
    """Return drawdown recovery manager status."""
    hybrid = get_hybrid()
    status = hybrid.dd_manager.get_status()
    stats = hybrid.dd_manager.get_trade_stats()
    return {
        "status": status,
        "trade_stats": stats,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)

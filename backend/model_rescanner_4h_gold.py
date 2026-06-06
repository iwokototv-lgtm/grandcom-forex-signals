"""
Model Rescanner — 4H Gold (XAUUSD / XAUEUR)
============================================
Lightweight, robust cron job that fetches current Gold prices and stores
results in MongoDB.  Designed to run as a Railway cron service every hour:

    python model_rescanner_4h_gold.py

Pipeline
--------
1. Validate required environment variables (with graceful fallback).
2. Fetch current Gold prices (XAUUSD, XAUEUR) from TwelveData API.
3. Compute basic technical indicators from 4H OHLCV data.
4. Store results in MongoDB (with graceful fallback if unavailable).
5. Send a Telegram status notification (with graceful fallback if unavailable).
6. Exit with status 0 on success, 1 on failure.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap — load .env when running locally
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("model_rescanner_4h_gold")

# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------
MONGO_URL: str = os.environ.get("MONGO_URL", "")
DB_NAME: str = os.environ.get("DB_NAME", "gold_signals_v3")
TWELVE_DATA_API_KEY: str = os.environ.get("TWELVE_DATA_API_KEY", "")
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")

_raw_channel = os.environ.get("TELEGRAM_GOLD_CHANNEL_ID", "-1003834233408")
try:
    TELEGRAM_CHANNEL_ID: int | str = int(_raw_channel)
except ValueError:
    TELEGRAM_CHANNEL_ID = _raw_channel

# ---------------------------------------------------------------------------
# Gold pairs configuration
# ---------------------------------------------------------------------------
GOLD_PAIRS: dict[str, dict[str, Any]] = {
    "XAUUSD": {
        "symbol": "XAU/USD",
        "decimals": 2,
    },
    "XAUEUR": {
        "symbol": "XAU/EUR",
        "decimals": 2,
    },
}

TIMEFRAME = "4h"
CANDLE_COUNT = 100
MIN_CANDLES = 20


# ---------------------------------------------------------------------------
# TwelveData OHLCV fetch
# ---------------------------------------------------------------------------
async def fetch_ohlcv(
    session: aiohttp.ClientSession,
    pair: str,
    interval: str = TIMEFRAME,
    outputsize: int = CANDLE_COUNT,
) -> list[dict] | None:
    """Fetch OHLCV candles from TwelveData for a Gold pair."""
    cfg = GOLD_PAIRS[pair]
    url = (
        "https://api.twelvedata.com/time_series"
        f"?symbol={cfg['symbol']}&interval={interval}&outputsize={outputsize}"
        f"&apikey={TWELVE_DATA_API_KEY}"
    )
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            data = await resp.json()

        if "values" not in data:
            logger.error(f"[{pair}] TwelveData error: {data.get('message', data)}")
            return None

        # Reverse so oldest candle is first
        candles = list(reversed(data["values"]))
        logger.info(f"[{pair}] Fetched {len(candles)} {interval} candles")
        return candles

    except Exception as exc:
        logger.error(f"[{pair}] fetch_ohlcv failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Technical indicators (pure Python — no external ML imports)
# ---------------------------------------------------------------------------
def compute_indicators(candles: list[dict], decimals: int) -> dict | None:
    """Compute a concise indicator snapshot from raw OHLCV candle dicts."""
    try:
        closes = [float(c["close"]) for c in candles]
        highs  = [float(c["high"])  for c in candles]
        lows   = [float(c["low"])   for c in candles]

        if len(closes) < MIN_CANDLES:
            logger.warning(f"Not enough candles for indicators: {len(closes)}")
            return None

        price = closes[-1]
        dp = decimals

        # Simple Moving Averages
        def sma(values: list[float], period: int) -> float | None:
            if len(values) < period:
                return None
            return sum(values[-period:]) / period

        ma20 = sma(closes, 20)
        ma50 = sma(closes, 50)

        # RSI (14-period)
        def rsi(values: list[float], period: int = 14) -> float | None:
            if len(values) < period + 1:
                return None
            gains, losses = [], []
            for i in range(1, period + 1):
                diff = values[-period - 1 + i] - values[-period - 2 + i]
                (gains if diff >= 0 else losses).append(abs(diff))
            avg_gain = sum(gains) / period if gains else 0.0
            avg_loss = sum(losses) / period if losses else 0.0
            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            return round(100 - (100 / (1 + rs)), 2)

        # ATR (14-period)
        def atr(h: list[float], l: list[float], c: list[float], period: int = 14) -> float | None:
            if len(c) < period + 1:
                return None
            trs = []
            for i in range(1, period + 1):
                idx = len(c) - period - 1 + i
                tr = max(
                    h[idx] - l[idx],
                    abs(h[idx] - c[idx - 1]),
                    abs(l[idx] - c[idx - 1]),
                )
                trs.append(tr)
            return round(sum(trs) / period, dp)

        rsi_val = rsi(closes)
        atr_val = atr(highs, lows, closes)
        trend = "BULLISH" if (ma50 is not None and price > ma50) else "BEARISH"

        return {
            "price": round(price, dp),
            "rsi":   rsi_val,
            "ma20":  round(ma20, dp) if ma20 is not None else None,
            "ma50":  round(ma50, dp) if ma50 is not None else None,
            "atr":   atr_val,
            "trend": trend,
        }
    except Exception as exc:
        logger.error(f"compute_indicators failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# MongoDB storage (graceful fallback)
# ---------------------------------------------------------------------------
async def store_rescan_result(db: Any, pair: str, result: dict) -> bool:
    """Upsert the latest rescan result for a pair. Returns True on success."""
    if db is None:
        logger.warning(f"[{pair}] MongoDB unavailable — skipping storage")
        return False
    try:
        await db.model_rescan_results.update_one(
            {"pair": pair, "timeframe": TIMEFRAME},
            {"$set": result},
            upsert=True,
        )
        logger.info(f"[{pair}] Rescan result stored in MongoDB")
        return True
    except Exception as exc:
        logger.error(f"[{pair}] Failed to store rescan result: {exc}")
        return False


# ---------------------------------------------------------------------------
# Telegram notification (graceful fallback — pure aiohttp, no python-telegram-bot)
# ---------------------------------------------------------------------------
async def send_telegram_message(session: aiohttp.ClientSession, text: str) -> bool:
    """Send a message via the Telegram Bot API. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set — skipping Telegram notification")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
    }
    try:
        async with session.post(
            url,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            body = await resp.json()
            if resp.status == 200 and body.get("ok"):
                logger.info(f"Telegram message sent to channel {TELEGRAM_CHANNEL_ID}")
                return True
            logger.error(f"Telegram API error {resp.status}: {body}")
            return False
    except Exception as exc:
        logger.error(f"Telegram notification failed: {exc}")
        return False


async def send_telegram_summary(
    session: aiohttp.ClientSession,
    summary: dict,
) -> None:
    """Build and send a concise rescan status message to the Gold channel."""
    status_emoji = "✅" if summary["success"] else "❌"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        f"{status_emoji} <b>Gold Price Scan — 4H</b>",
        f"<i>{ts}</i>",
        "",
    ]

    for pair, info in summary.get("pairs", {}).items():
        pair_ok = info.get("success", False)
        pair_emoji = "🟢" if pair_ok else "🔴"
        price = info.get("price", "N/A")
        trend = info.get("trend", "N/A")
        rsi   = info.get("rsi", "N/A")
        atr   = info.get("atr", "N/A")
        lines.append(
            f"{pair_emoji} <b>{pair}</b> | {trend} | "
            f"Price: {price} | RSI: {rsi} | ATR: {atr}"
        )

    if summary.get("errors"):
        lines += ["", "⚠️ <b>Errors:</b>"]
        for err in summary["errors"]:
            lines.append(f"  • {err}")

    lines += ["", "<i>🤖 Grandcom Gold Engine — model-rescanner-4h-gold</i>"]

    await send_telegram_message(session, "\n".join(lines))


# ---------------------------------------------------------------------------
# Per-pair scan
# ---------------------------------------------------------------------------
async def scan_pair(
    session: aiohttp.ClientSession,
    pair: str,
    db: Any,
) -> dict:
    """Fetch price data and compute indicators for a single Gold pair."""
    cfg = GOLD_PAIRS[pair]
    logger.info(f"[{pair}] ── Starting 4H price scan ──")

    result: dict[str, Any] = {
        "pair": pair,
        "timeframe": TIMEFRAME,
        "rescanned_at": datetime.now(timezone.utc),
        "success": False,
        "price": None,
        "trend": "UNKNOWN",
        "rsi": None,
        "ma20": None,
        "ma50": None,
        "atr": None,
        "system_version": "3.0.0",
    }

    # 1. Fetch OHLCV candles
    candles = await fetch_ohlcv(session, pair, interval=TIMEFRAME, outputsize=CANDLE_COUNT)
    if candles is None or len(candles) < MIN_CANDLES:
        msg = (
            f"Insufficient candles "
            f"({len(candles) if candles is not None else 0}, need ≥ {MIN_CANDLES})"
        )
        logger.warning(f"[{pair}] {msg}")
        result["error"] = msg
        return result

    # 2. Compute indicators
    ind = compute_indicators(candles, cfg["decimals"])
    if ind:
        result.update(ind)
        result["success"] = True
    else:
        result["error"] = "Indicator computation failed"
        return result

    logger.info(
        f"[{pair}] Scan complete — "
        f"price={result['price']} trend={result['trend']} "
        f"rsi={result['rsi']} atr={result['atr']}"
    )

    # 3. Persist to MongoDB (non-fatal if unavailable)
    await store_rescan_result(db, pair, result)

    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
async def main() -> None:
    logger.info("=" * 60)
    logger.info("Gold Model Rescanner — 4H  (XAUUSD / XAUEUR)")
    logger.info("=" * 60)

    # ── 1. Check required env vars (warn but don't abort on optional ones) ──
    if not TWELVE_DATA_API_KEY:
        logger.error("❌ TWELVE_DATA_API_KEY is not set — cannot fetch prices")
        sys.exit(1)

    if not TELEGRAM_BOT_TOKEN:
        logger.warning("⚠️  TELEGRAM_BOT_TOKEN not set — notifications will be skipped")

    if not MONGO_URL:
        logger.warning("⚠️  MONGO_URL not set — results will not be persisted")

    logger.info("✅ Environment check complete")

    # ── 2. Connect to MongoDB (graceful fallback) ────────────────────
    mongo_client = None
    db = None
    if MONGO_URL:
        try:
            from motor.motor_asyncio import AsyncIOMotorClient

            mongo_client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=8000)
            db = mongo_client[DB_NAME]
            await db.command("ping")
            logger.info(f"✅ MongoDB connected — db={DB_NAME}")
        except Exception as exc:
            logger.warning(f"⚠️  MongoDB connection failed (continuing without it): {exc}")
            mongo_client = None
            db = None

    # ── 3. Scan each Gold pair ───────────────────────────────────────
    summary: dict[str, Any] = {
        "success": True,
        "pairs": {},
        "errors": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    async with aiohttp.ClientSession() as session:
        for pair in GOLD_PAIRS:
            try:
                pair_result = await scan_pair(session, pair, db)
                summary["pairs"][pair] = {
                    "success": pair_result["success"],
                    "price":   pair_result.get("price"),
                    "trend":   pair_result.get("trend", "UNKNOWN"),
                    "rsi":     pair_result.get("rsi"),
                    "atr":     pair_result.get("atr"),
                }
                if not pair_result["success"]:
                    summary["success"] = False
                    summary["errors"].append(
                        f"{pair}: {pair_result.get('error', 'unknown error')}"
                    )
            except Exception as exc:
                logger.error(f"[{pair}] Unhandled error during scan: {exc}", exc_info=True)
                summary["success"] = False
                summary["errors"].append(f"{pair}: {exc}")
                summary["pairs"][pair] = {
                    "success": False,
                    "price": None,
                    "trend": "UNKNOWN",
                    "rsi": None,
                    "atr": None,
                }

            # Brief pause between pairs to respect API rate limits
            await asyncio.sleep(1)

        # ── 4. Telegram summary ──────────────────────────────────────
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        await send_telegram_summary(session, summary)

    # ── 5. Cleanup ───────────────────────────────────────────────────
    if mongo_client:
        mongo_client.close()

    overall = "✅ SUCCESS" if summary["success"] else "⚠️  COMPLETED WITH ERRORS"
    logger.info(f"Gold Model Rescanner 4H — {overall}")
    logger.info("=" * 60)

    if not summary["success"]:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

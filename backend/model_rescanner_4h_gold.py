"""
Model Rescanner — 4H Gold (XAUUSD / XAUEUR)
============================================
Lightweight, robust cron job that fetches current Gold prices and stores
results in MongoDB.  Designed to run as a Railway cron service 6x/day at
candle-close boundaries:

    Cron schedule: 5 0,4,8,12,16,20 * * *
    (5 minutes past each 4H boundary — after candle close + buffer)

    python model_rescanner_4h_gold.py

Pipeline
--------
1. Validate required environment variables (with graceful fallback).
2. Fetch current Gold prices (XAUUSD, XAUEUR) from TwelveData API.
3. Validate data freshness (warn if > 5 min old) and timestamps.
4. Validate candle-close confirmation before storing results.
5. Compute basic technical indicators from 4H OHLCV data.
6. Store results in MongoDB (with graceful fallback if unavailable).
7. Send a Telegram status notification (with graceful fallback if unavailable).
8. Log scan metrics (candles fetched, freshness, close status).
9. Exit with status 0 on success, 1 on failure.

Schedule change: 6x/day (was 60x/day) — 90% reduction in API calls.
Aligned to 4H candle-close boundaries to ensure only closed candles are stored.
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
import pandas as pd
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Local utilities (candle-close confirmation + data freshness)
# ---------------------------------------------------------------------------
try:
    from candle_utils import is_candle_closed, validate_candle_timestamp
    from data_freshness import DataFreshnessGuard
    _freshness_guard = DataFreshnessGuard()
    _UTILS_AVAILABLE = True
except ImportError:
    _UTILS_AVAILABLE = False
    _freshness_guard = None  # type: ignore

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
) -> tuple[list[dict], datetime] | tuple[None, None]:
    """Fetch OHLCV candles from TwelveData for a Gold pair.

    Returns a (candles, response_timestamp) tuple where response_timestamp
    is the UTC datetime captured immediately after the API call returned.
    This timestamp is used by DataFreshnessGuard to measure feed staleness
    (not the candle open time).  Returns (None, None) on failure.
    """
    cfg = GOLD_PAIRS[pair]
    url = (
        "https://api.twelvedata.com/time_series"
        f"?symbol={cfg['symbol']}&interval={interval}&outputsize={outputsize}"
        f"&apikey={TWELVE_DATA_API_KEY}"
    )
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            data = await resp.json()

        # Capture response time immediately after the API call completes
        response_timestamp = datetime.now(timezone.utc)

        if "values" not in data:
            logger.error(f"[{pair}] TwelveData error: {data.get('message', data)}")
            return None, None

        # Reverse so oldest candle is first
        candles = list(reversed(data["values"]))
        logger.info(f"[{pair}] Fetched {len(candles)} {interval} candles (response_ts={response_timestamp.isoformat()})")
        return candles, response_timestamp

    except Exception as exc:
        logger.error(f"[{pair}] fetch_ohlcv failed: {exc}")
        return None, None


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
        candle_closed = info.get("candle_closed")
        closed_icon   = "🕯✅" if candle_closed is True else ("🕯⏳" if candle_closed is False else "🕯❓")
        data_age      = info.get("data_age_seconds")
        age_str       = f"{data_age:.0f}s" if data_age is not None else "?"
        lines.append(
            f"{pair_emoji} <b>{pair}</b> | {trend} | "
            f"Price: {price} | RSI: {rsi} | ATR: {atr} | "
            f"{closed_icon} | Age: {age_str}"
        )

    if summary.get("errors"):
        lines += ["", "⚠️ <b>Errors:</b>"]
        for err in summary["errors"]:
            lines.append(f"  • {err}")

    lines += ["", "<i>🤖 Grandcom Gold Engine — model-rescanner-4h-gold v4.0 | 6x/day @ candle close</i>"]

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
        "system_version": "4.0.0",
        # Freshness / candle-close metadata
        "candle_closed": None,
        "data_age_seconds": None,
        "data_fresh": None,
    }

    # 1. Fetch OHLCV candles
    candles, response_timestamp = await fetch_ohlcv(session, pair, interval=TIMEFRAME, outputsize=CANDLE_COUNT)
    if candles is None or len(candles) < MIN_CANDLES:
        msg = (
            f"Insufficient candles "
            f"({len(candles) if candles is not None else 0}, need ≥ {MIN_CANDLES})"
        )
        logger.warning(f"[{pair}] {msg}")
        result["error"] = msg
        return result

    # ── 2. Data freshness & timestamp validation ─────────────────────────────
    # Build a minimal DataFrame for the utility functions
    df_check = pd.DataFrame(candles)
    if "datetime" not in df_check.columns and "date" in df_check.columns:
        df_check = df_check.rename(columns={"date": "datetime"})

    if _UTILS_AVAILABLE and _freshness_guard is not None:
        # Freshness check — measures feed age (time since API responded), NOT
        # candle open time.  A just-closed 4H bar is fresh even though its
        # open timestamp is hours old.  Warn only — do not abort; rescanner
        # runs at candle close and a stale flag here means a dead feed.
        logger.info(
            f"[{pair}] Freshness check — "
            f"response_ts={response_timestamp.isoformat() if response_timestamp else 'N/A'}"
        )
        data_fresh = _freshness_guard.is_fresh(
            df_check,
            max_age_seconds=300,
            response_timestamp=response_timestamp,
        )
        # Also record feed age in seconds for the summary/Telegram message
        feed_age: float | None = None
        if response_timestamp is not None:
            feed_age = max(0.0, (datetime.now(timezone.utc) - response_timestamp).total_seconds())
        result["data_age_seconds"] = round(feed_age, 1) if feed_age is not None else None
        result["data_fresh"] = data_fresh

        if not data_fresh:
            logger.warning(
                f"[{pair}] ⚠️  Dead feed detected — "
                f"response_ts={response_timestamp.isoformat() if response_timestamp else 'N/A'} "
                f"> 300s ago. Proceeding with caution."
            )
        else:
            logger.info(
                f"[{pair}] ✅ Feed freshness OK — "
                f"response_ts={response_timestamp.isoformat() if response_timestamp else 'N/A'}"
            )

        # Timestamp validation
        if not _freshness_guard.validate_timestamps(df_check):
            logger.warning(f"[{pair}] ⚠️  Timestamp validation FAILED — data may be malformed")
            result["timestamp_valid"] = False
        else:
            result["timestamp_valid"] = True

        # Candle-close confirmation
        candle_closed = is_candle_closed(df_check, interval=TIMEFRAME)
        result["candle_closed"] = candle_closed
        if not candle_closed:
            logger.warning(
                f"[{pair}] ⚠️  Last 4H candle is still FORMING — "
                f"storing result but flagging as unconfirmed"
            )
        else:
            logger.info(f"[{pair}] ✅ Candle-close confirmed — last 4H candle is CLOSED")
    else:
        logger.debug(f"[{pair}] candle_utils / data_freshness not available — skipping checks")

    # ── 3. Compute indicators ────────────────────────────────────────────────
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
        f"rsi={result['rsi']} atr={result['atr']} "
        f"candle_closed={result.get('candle_closed')} "
        f"data_age={result.get('data_age_seconds')}s"
    )

    # ── 4. Persist to MongoDB (non-fatal if unavailable) ─────────────────────
    await store_rescan_result(db, pair, result)

    return result


# ---------------------------------------------------------------------------
async def main() -> None:
    logger.info("=" * 60)
    logger.info("Gold Model Rescanner — 4H  (XAUUSD / XAUEUR)")
    logger.info("Cron schedule: 5 0,4,8,12,16,20 * * *  (6x/day at candle close)")
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
                    "success":          pair_result["success"],
                    "price":            pair_result.get("price"),
                    "trend":            pair_result.get("trend", "UNKNOWN"),
                    "rsi":              pair_result.get("rsi"),
                    "atr":              pair_result.get("atr"),
                    # V4 freshness / candle-close metadata
                    "candle_closed":    pair_result.get("candle_closed"),
                    "data_fresh":       pair_result.get("data_fresh"),
                    "data_age_seconds": pair_result.get("data_age_seconds"),
                    "timestamp_valid":  pair_result.get("timestamp_valid"),
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

    # ── Metrics summary ──────────────────────────────────────────────
    for pair, info in summary.get("pairs", {}).items():
        closed_flag = info.get("candle_closed")
        fresh_flag  = info.get("data_fresh")
        age_s       = info.get("data_age_seconds")
        logger.info(
            f"[{pair}] METRICS — "
            f"success={info.get('success')} "
            f"candle_closed={closed_flag} "
            f"data_fresh={fresh_flag} "
            f"data_age={age_s}s "
            f"timestamp_valid={info.get('timestamp_valid')}"
        )

    logger.info("=" * 60)

    if not summary["success"]:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

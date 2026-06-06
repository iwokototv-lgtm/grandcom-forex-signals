"""
Model Rescanner — 4H Gold (XAUUSD / XAUEUR)
============================================
Scheduled cron job that rescans and updates Gold trading models on the 4H
timeframe.  Designed to run as a Railway cron service (one-shot execution):

    python model_rescanner_4h_gold.py

Pipeline
--------
1. Validate required environment variables.
2. Connect to MongoDB.
3. For each Gold pair (XAUUSD, XAUEUR):
   a. Fetch 4H OHLCV candles from TwelveData.
   b. Extract ML features via FeatureEngineer.
   c. Run regime detection via RegimeDetector.
   d. Run full HybridPortfolioSystemV3 analysis.
   e. Run model optimisation against historical signal outcomes.
   f. Persist rescan results to MongoDB (collection: model_rescan_results).
4. Send a Telegram status summary (success or failure).
5. Exit cleanly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import pandas as pd
import ta
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

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
REQUIRED_ENV_VARS = [
    "MONGO_URL",
    "TWELVE_DATA_API_KEY",
    "TELEGRAM_BOT_TOKEN",
]

MONGO_URL: str = os.environ.get("MONGO_URL", "")
DB_NAME: str = os.environ.get("DB_NAME", "gold_signals_v3")
TWELVE_DATA_API_KEY: str = os.environ.get("TWELVE_DATA_API_KEY", "")
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ACCOUNT_BALANCE: float = float(os.environ.get("DEFAULT_ACCOUNT_BALANCE", "10000.0"))

_raw_channel = os.environ.get("TELEGRAM_GOLD_CHANNEL_ID", "-1003834233408")
try:
    TELEGRAM_CHANNEL_ID: int | str = int(_raw_channel)
except ValueError:
    TELEGRAM_CHANNEL_ID = _raw_channel

# ---------------------------------------------------------------------------
# Gold pairs — mirrors gold_server_v3.py configuration
# ---------------------------------------------------------------------------
GOLD_PAIRS: dict[str, dict[str, Any]] = {
    "XAUUSD": {
        "symbol": "XAU/USD",
        "decimals": 2,
        "atr_sl": 0.64,
        "atr_tp1": 0.5,
        "atr_tp2": 0.75,
        "atr_tp3": 1.0,
    },
    "XAUEUR": {
        "symbol": "XAU/EUR",
        "decimals": 2,
        "atr_sl": 0.64,
        "atr_tp1": 0.5,
        "atr_tp2": 0.75,
        "atr_tp3": 1.0,
    },
}

TIMEFRAME = "4h"
CANDLE_COUNT = 200  # Enough history for feature engineering (needs ≥ 60)
MIN_CANDLES = 60


# ---------------------------------------------------------------------------
# Environment validation
# ---------------------------------------------------------------------------
def validate_env() -> list[str]:
    """Return a list of missing required environment variable names."""
    missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
    return missing


# ---------------------------------------------------------------------------
# TwelveData OHLCV fetch
# ---------------------------------------------------------------------------
async def fetch_ohlcv(pair: str, interval: str = TIMEFRAME, outputsize: int = CANDLE_COUNT) -> pd.DataFrame | None:
    """Fetch OHLCV candles from TwelveData for a Gold pair."""
    cfg = GOLD_PAIRS[pair]
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={cfg['symbol']}&interval={interval}&outputsize={outputsize}"
        f"&apikey={TWELVE_DATA_API_KEY}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                data = await resp.json()

        if "values" not in data:
            logger.error(f"[{pair}] TwelveData error: {data.get('message', data)}")
            return None

        df = pd.DataFrame(data["values"])
        for col in ("open", "high", "low", "close"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.iloc[::-1].reset_index(drop=True)  # oldest → newest
        logger.info(f"[{pair}] Fetched {len(df)} {interval} candles")
        return df

    except Exception as exc:
        logger.error(f"[{pair}] fetch_ohlcv failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Technical indicators (summary snapshot for the rescan record)
# ---------------------------------------------------------------------------
def compute_indicators(df: pd.DataFrame, decimals: int) -> dict | None:
    """Compute a concise indicator snapshot from the latest candle."""
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
# Model optimisation (wraps ml_engine.model_trainer)
# ---------------------------------------------------------------------------
async def run_optimisation(db) -> dict:
    """
    Pull historical Gold signal outcomes from MongoDB and run the
    SignalOptimizationEngine against them.  Returns a summary dict.
    """
    try:
        from ml_engine.model_trainer import SignalOptimizationEngine

        cursor = db.gold_signals.find(
            {"result": {"$in": ["WIN", "LOSS"]}}
        ).sort("created_at", -1).limit(500)

        signals: list[dict] = []
        async for doc in cursor:
            doc["id"] = str(doc.pop("_id"))
            signals.append(doc)

        if len(signals) < 10:
            logger.warning(
                f"Only {len(signals)} completed Gold signals found — "
                "skipping optimisation (need ≥ 10)"
            )
            return {
                "skipped": True,
                "reason": f"Insufficient data ({len(signals)} signals, need ≥ 10)",
                "signals_found": len(signals),
            }

        optimizer = SignalOptimizationEngine()
        pair_analysis = optimizer.analyze_performance_by_pair(signals)
        regime_analysis = optimizer.analyze_performance_by_regime(signals)
        recommendations = optimizer.recommend_pair_settings(pair_analysis)

        total = len(signals)
        wins = sum(1 for s in signals if s.get("result") == "WIN")
        total_pips = sum(s.get("pips", 0) or 0 for s in signals)

        result = {
            "skipped": False,
            "signals_analysed": total,
            "overall_win_rate": round(wins / total * 100, 1) if total else 0,
            "total_pips": round(total_pips, 1),
            "avg_pips_per_trade": round(total_pips / total, 1) if total else 0,
            "pair_analysis": pair_analysis,
            "regime_analysis": regime_analysis,
            "recommendations": recommendations,
        }
        logger.info(
            f"Optimisation complete — {total} signals, "
            f"win rate {result['overall_win_rate']}%, "
            f"total pips {result['total_pips']}"
        )
        return result

    except ImportError as exc:
        logger.error(f"Could not import ml_engine.model_trainer: {exc}")
        return {"skipped": True, "reason": f"Import error: {exc}"}
    except Exception as exc:
        logger.error(f"Optimisation error: {exc}", exc_info=True)
        return {"skipped": True, "reason": str(exc)}


# ---------------------------------------------------------------------------
# Hybrid system analysis
# ---------------------------------------------------------------------------
async def run_hybrid_analysis(pair: str, df: pd.DataFrame) -> dict:
    """
    Run the full HybridPortfolioSystemV3 pipeline for a single pair.
    Returns the analysis dict (or an error dict on failure).
    """
    try:
        from ml_engine.hybrid_portfolio_system_v3 import HybridPortfolioSystemV3

        system = HybridPortfolioSystemV3(account_balance=ACCOUNT_BALANCE)
        result = await system.generate_signal(symbol=pair, df_4h=df)
        logger.info(
            f"[{pair}] Hybrid analysis — signal={result.get('signal', 'N/A')} "
            f"regime={result.get('regime', 'N/A')} "
            f"confidence={result.get('confidence', 0):.1f}%"
        )
        return result
    except Exception as exc:
        logger.error(f"[{pair}] Hybrid analysis failed: {exc}", exc_info=True)
        return {"error": str(exc), "valid": False}


# ---------------------------------------------------------------------------
# Feature extraction + regime detection
# ---------------------------------------------------------------------------
def run_regime_detection(pair: str, df: pd.DataFrame) -> dict:
    """
    Extract ML features and run regime detection for a single pair.
    Returns the regime analysis dict (or an error dict on failure).
    """
    try:
        from ml_engine.feature_engineering import FeatureEngineer
        from ml_engine.regime_detector import RegimeDetector

        engineer = FeatureEngineer()
        detector = RegimeDetector()

        features = engineer.extract_features(df, symbol=pair)
        if features is None:
            return {"error": "Feature extraction returned None", "valid": False}

        regime_result = detector.detect_regime(features)
        logger.info(f"[{pair}] Regime detected: {regime_result.get('regime', 'UNKNOWN')}")
        return regime_result
    except Exception as exc:
        logger.error(f"[{pair}] Regime detection failed: {exc}", exc_info=True)
        return {"error": str(exc), "valid": False}


# ---------------------------------------------------------------------------
# Persist rescan result to MongoDB
# ---------------------------------------------------------------------------
async def store_rescan_result(db, pair: str, result: dict) -> None:
    """Upsert the latest rescan result for a pair into model_rescan_results."""
    try:
        await db.model_rescan_results.update_one(
            {"pair": pair, "timeframe": TIMEFRAME},
            {"$set": result},
            upsert=True,
        )
        logger.info(f"[{pair}] Rescan result stored in MongoDB")
    except Exception as exc:
        logger.error(f"[{pair}] Failed to store rescan result: {exc}")


# ---------------------------------------------------------------------------
# Telegram notification
# ---------------------------------------------------------------------------
async def send_telegram_summary(summary: dict) -> None:
    """Send a concise rescan status message to the Gold Telegram channel."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set — skipping Telegram notification")
        return

    try:
        from telegram import Bot

        bot = Bot(token=TELEGRAM_BOT_TOKEN)

        status_emoji = "✅" if summary["success"] else "❌"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        lines = [
            f"{status_emoji} <b>Gold Model Rescan — 4H</b>",
            f"<i>{ts}</i>",
            "",
        ]

        for pair, info in summary.get("pairs", {}).items():
            pair_ok = info.get("success", False)
            pair_emoji = "🟢" if pair_ok else "🔴"
            regime = info.get("regime", "N/A")
            signal = info.get("signal", "N/A")
            confidence = info.get("confidence", 0)
            price = info.get("price", "N/A")
            lines.append(
                f"{pair_emoji} <b>{pair}</b> | {regime} | {signal} "
                f"({confidence:.0f}%) @ {price}"
            )

        opt = summary.get("optimisation", {})
        if not opt.get("skipped"):
            win_rate = opt.get("overall_win_rate", 0)
            total_pips = opt.get("total_pips", 0)
            n = opt.get("signals_analysed", 0)
            lines += [
                "",
                f"📊 <b>Optimisation:</b> {n} signals | WR {win_rate}% | {total_pips:+.1f} pips",
            ]
        else:
            lines += ["", f"⚠️ Optimisation skipped: {opt.get('reason', 'unknown')}"]

        if summary.get("errors"):
            lines += ["", "⚠️ <b>Errors:</b>"]
            for err in summary["errors"]:
                lines.append(f"  • {err}")

        lines += ["", "<i>🤖 Grandcom Gold Engine v3.0 — model-rescanner-4h-gold</i>"]

        message = "\n".join(lines)
        await bot.send_message(
            chat_id=TELEGRAM_CHANNEL_ID,
            text=message,
            parse_mode="HTML",
        )
        logger.info(f"Telegram summary sent to channel {TELEGRAM_CHANNEL_ID}")

    except Exception as exc:
        logger.error(f"Telegram notification failed: {exc}")


# ---------------------------------------------------------------------------
# Per-pair rescan
# ---------------------------------------------------------------------------
async def rescan_pair(pair: str, db) -> dict:
    """
    Full rescan pipeline for a single Gold pair.

    Returns a result dict that is both stored in MongoDB and included in the
    Telegram summary.
    """
    cfg = GOLD_PAIRS[pair]
    logger.info(f"[{pair}] ── Starting 4H model rescan ──")

    result: dict[str, Any] = {
        "pair": pair,
        "timeframe": TIMEFRAME,
        "rescanned_at": datetime.now(timezone.utc),
        "success": False,
        "regime": "UNKNOWN",
        "signal": "NEUTRAL",
        "confidence": 0.0,
        "price": None,
        "indicators": None,
        "regime_analysis": None,
        "hybrid_analysis": None,
        "system_version": "3.0.0",
    }

    # 1. Fetch OHLCV
    df = await fetch_ohlcv(pair, interval=TIMEFRAME, outputsize=CANDLE_COUNT)
    if df is None or len(df) < MIN_CANDLES:
        msg = f"Insufficient candles ({len(df) if df is not None else 0}, need ≥ {MIN_CANDLES})"
        logger.warning(f"[{pair}] {msg}")
        result["error"] = msg
        return result

    # 2. Indicators snapshot
    ind = compute_indicators(df, cfg["decimals"])
    if ind:
        result["indicators"] = ind
        result["price"] = ind["price"]

    # 3. Regime detection
    regime_analysis = run_regime_detection(pair, df)
    result["regime_analysis"] = regime_analysis
    if not regime_analysis.get("error"):
        result["regime"] = regime_analysis.get("regime", "UNKNOWN")

    # 4. Full hybrid analysis
    hybrid_analysis = await run_hybrid_analysis(pair, df)
    result["hybrid_analysis"] = hybrid_analysis
    if not hybrid_analysis.get("error"):
        result["signal"] = hybrid_analysis.get("signal", "NEUTRAL")
        result["confidence"] = float(hybrid_analysis.get("confidence", 0))
        # Prefer regime from hybrid if available
        if hybrid_analysis.get("regime"):
            result["regime"] = hybrid_analysis["regime"]

    result["success"] = not bool(result.get("error"))
    logger.info(
        f"[{pair}] Rescan complete — "
        f"regime={result['regime']} signal={result['signal']} "
        f"confidence={result['confidence']:.1f}% price={result['price']}"
    )

    # 5. Persist to MongoDB
    await store_rescan_result(db, pair, result)

    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
async def main() -> None:
    logger.info("=" * 60)
    logger.info("Gold Model Rescanner — 4H  (XAUUSD / XAUEUR)")
    logger.info("=" * 60)

    # ── 1. Validate environment ──────────────────────────────────────
    missing = validate_env()
    if missing:
        logger.error(f"❌ Missing required environment variables: {missing}")
        logger.error("Aborting rescan — fix the missing variables and retry.")
        sys.exit(1)

    logger.info("✅ All required environment variables present")

    # ── 2. Connect to MongoDB ────────────────────────────────────────
    mongo_client: AsyncIOMotorClient | None = None
    db = None
    try:
        mongo_client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=8000)
        db = mongo_client[DB_NAME]
        await db.command("ping")
        logger.info(f"✅ MongoDB connected — db={DB_NAME}")
    except Exception as exc:
        logger.error(f"❌ MongoDB connection failed: {exc}")
        logger.error("Aborting rescan — cannot persist results without MongoDB.")
        sys.exit(1)

    # ── 3. Rescan each Gold pair ─────────────────────────────────────
    summary: dict[str, Any] = {
        "success": True,
        "pairs": {},
        "optimisation": {},
        "errors": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    for pair in GOLD_PAIRS:
        try:
            pair_result = await rescan_pair(pair, db)
            summary["pairs"][pair] = {
                "success":    pair_result["success"],
                "regime":     pair_result["regime"],
                "signal":     pair_result["signal"],
                "confidence": pair_result["confidence"],
                "price":      pair_result["price"],
            }
            if not pair_result["success"]:
                summary["success"] = False
                summary["errors"].append(
                    f"{pair}: {pair_result.get('error', 'unknown error')}"
                )
        except Exception as exc:
            logger.error(f"[{pair}] Unhandled error during rescan: {exc}", exc_info=True)
            summary["success"] = False
            summary["errors"].append(f"{pair}: {exc}")
            summary["pairs"][pair] = {
                "success": False,
                "regime": "UNKNOWN",
                "signal": "NEUTRAL",
                "confidence": 0,
                "price": None,
            }

        # Brief pause between pairs to respect API rate limits
        await asyncio.sleep(2)

    # ── 4. Model optimisation ────────────────────────────────────────
    logger.info("Running model optimisation against historical Gold signals…")
    opt_result = await run_optimisation(db)
    summary["optimisation"] = opt_result

    # Persist optimisation result
    try:
        await db.model_optimisation_results.update_one(
            {"service": "model-rescanner-4h-gold"},
            {
                "$set": {
                    "service": "model-rescanner-4h-gold",
                    "timeframe": TIMEFRAME,
                    "pairs": list(GOLD_PAIRS.keys()),
                    "result": opt_result,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
        logger.info("Optimisation result persisted to MongoDB")
    except Exception as exc:
        logger.error(f"Failed to persist optimisation result: {exc}")

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()

    # ── 5. Telegram summary ──────────────────────────────────────────
    await send_telegram_summary(summary)

    # ── 6. Cleanup ───────────────────────────────────────────────────
    if mongo_client:
        mongo_client.close()

    overall = "✅ SUCCESS" if summary["success"] else "⚠️  COMPLETED WITH ERRORS"
    logger.info(f"Gold Model Rescanner 4H — {overall}")
    logger.info("=" * 60)

    if not summary["success"]:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

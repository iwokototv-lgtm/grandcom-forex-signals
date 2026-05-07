"""
Model Rescanner — 4H Momentum & Regime Monitor (Gold Pairs)
============================================================
Runs every 4 hours (cron: 0 */4 * * *) to detect momentum shifts,
volatility extremes, and DXY correlation conflicts for XAUUSD and XAUEUR.

Actions taken on regime change:
  - Sets momentum_paused=true in MongoDB for the affected pair
  - Closes all ACTIVE signals for that pair (marks as PAUSED or VOLATILITY_STOP)
  - Sends Telegram alert to the Gold channel with before/after regime

Collections written:
  - regime_state        : { pair, regime, volatility, correlation_strength, updated_at }
  - signals             : status updated to PAUSED / VOLATILITY_STOP / CORRELATION_ADJUSTED
  - rescanner_log       : { timestamp, pair, action, reason, details }
"""

import asyncio
import logging
import os
import signal as _signal
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import numpy as np
import pandas as pd
import ta
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import Bot
from telegram.error import RetryAfter, TelegramError

# ── Environment ──────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

MONGO_URL               = os.environ["MONGO_URL"]
DB_NAME                 = os.environ.get("DB_NAME", "grandcom_signals")
TWELVE_DATA_API_KEY     = os.environ.get("TWELVE_DATA_API_KEY", "demo")
TELEGRAM_BOT_TOKEN      = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_GOLD_CHANNEL_ID = os.environ.get("TELEGRAM_GOLD_CHANNEL_ID", "@grandcomgold")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("rescanner_4h_gold")

# ── Pair universe ─────────────────────────────────────────────────────────────
# Gold pairs only: XAUUSD and XAUEUR
SYMBOL_MAP: Dict[str, str] = {
    "XAUUSD": "XAU/USD",
    "XAUEUR": "XAU/EUR",
    # DXY proxy (used for correlation only — not traded)
    "DXY":    "DXY",
}

ALL_PAIRS: List[str] = [p for p in SYMBOL_MAP if p != "DXY"]

# DXY relationship per pair
DXY_ROLE: Dict[str, str] = {
    "XAUUSD": "USD_FOLLOW",  # Strong DXY → gold in USD should weaken
    "XAUEUR": "CROSS",       # EUR-denominated gold — no direct DXY conflict
}

# ── Regime constants ──────────────────────────────────────────────────────────
REGIME_BULLISH  = "BULLISH"
REGIME_BEARISH  = "BEARISH"
REGIME_NEUTRAL  = "NEUTRAL"
REGIME_CHOPPY   = "CHOPPY"

# Volatility thresholds (ATR ratio vs rolling average)
ATR_HIGH_THRESHOLD = 2.0   # ATR > 2× average → too volatile
ATR_LOW_THRESHOLD  = 0.5   # ATR < 0.5× average → too choppy

# Per-pair API timeout (seconds)
PAIR_TIMEOUT = 30

# Candles to fetch on 4H timeframe
OUTPUTSIZE = 100  # ~17 days of 4H candles — enough for ATR(14) + MACD(26)

# Telegram rate-limit guard (seconds between messages)
TG_SEND_DELAY = 1.5


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — DATA FETCHING
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch_4h_candles(
    session: aiohttp.ClientSession,
    pair: str,
) -> Optional[pd.DataFrame]:
    """
    Fetch 4H OHLCV candles from Twelve Data for a single pair.
    Returns a DataFrame sorted oldest→newest, or None on failure.
    """
    api_symbol = SYMBOL_MAP.get(pair, pair)
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol":     api_symbol,
        "interval":   "4h",
        "apikey":     TWELVE_DATA_API_KEY,
        "outputsize": OUTPUTSIZE,
    }

    try:
        async with session.get(
            url, params=params, timeout=aiohttp.ClientTimeout(total=PAIR_TIMEOUT)
        ) as resp:
            data = await resp.json()

            if "values" not in data:
                msg = data.get("message", data.get("status", "unknown error"))
                logger.warning(f"[{pair}] Twelve Data error: {msg}")
                return None

            df = pd.DataFrame(data["values"])
            df["datetime"] = pd.to_datetime(df["datetime"])
            df = df.sort_values("datetime").reset_index(drop=True)

            for col in ("open", "high", "low", "close"):
                df[col] = pd.to_numeric(df[col], errors="coerce")

            df["volume"] = (
                pd.to_numeric(df["volume"], errors="coerce")
                if "volume" in df.columns
                else 0.0
            )

            if len(df) < 30:
                logger.warning(f"[{pair}] Insufficient candles: {len(df)}")
                return None

            return df

    except asyncio.TimeoutError:
        logger.warning(f"[{pair}] Timeout fetching 4H candles")
        return None
    except Exception as exc:
        logger.error(f"[{pair}] Fetch error: {exc}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — INDICATOR CALCULATION
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_indicators(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """
    Calculate ADX(14), MACD(12,26,9), RSI(14), ATR(14) on 4H data.
    Returns a dict of latest indicator values, or None on error.
    """
    try:
        df = df.copy()

        # ADX(14)
        adx_ind       = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14)
        df["adx"]     = adx_ind.adx()
        df["adx_pos"] = adx_ind.adx_pos()
        df["adx_neg"] = adx_ind.adx_neg()

        # MACD(12, 26, 9)
        macd_ind          = ta.trend.MACD(df["close"], window_slow=26, window_fast=12, window_sign=9)
        df["macd"]        = macd_ind.macd()
        df["macd_signal"] = macd_ind.macd_signal()
        df["macd_diff"]   = macd_ind.macd_diff()

        # RSI(14)
        df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()

        # ATR(14)
        df["atr"] = ta.volatility.AverageTrueRange(
            df["high"], df["low"], df["close"], window=14
        ).average_true_range()

        # ATR rolling average (last 50 candles ≈ 8 days on 4H)
        df["atr_avg"] = df["atr"].rolling(50, min_periods=14).mean()

        latest = df.iloc[-1]
        prev   = df.iloc[-2]

        atr_current = float(latest["atr"])
        atr_avg     = float(latest["atr_avg"]) if not pd.isna(latest["atr_avg"]) else atr_current
        atr_ratio   = atr_current / atr_avg if atr_avg > 0 else 1.0

        return {
            "adx":          float(latest["adx"]),
            "adx_pos":      float(latest["adx_pos"]),
            "adx_neg":      float(latest["adx_neg"]),
            "macd":         float(latest["macd"]),
            "macd_signal":  float(latest["macd_signal"]),
            "macd_diff":    float(latest["macd_diff"]),
            "macd_diff_prev": float(prev["macd_diff"]),
            "rsi":          float(latest["rsi"]),
            "atr":          atr_current,
            "atr_avg":      atr_avg,
            "atr_ratio":    atr_ratio,
            "close":        float(latest["close"]),
            "prev_close":   float(prev["close"]),
        }

    except Exception as exc:
        logger.error(f"Indicator calculation error: {exc}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — REGIME CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

def classify_regime(ind: Dict[str, Any]) -> Tuple[str, float]:
    """
    Determine market regime from 4H indicators.

    Rules (in priority order):
      CHOPPY   — ADX < 18 (no trend, directionless)
      BULLISH  — ADX ≥ 18, ADX+ > ADX−, MACD diff > 0, RSI > 50
      BEARISH  — ADX ≥ 18, ADX− > ADX+, MACD diff < 0, RSI < 50
      NEUTRAL  — ADX ≥ 18 but mixed signals

    Returns (regime_name, confidence 0–1).
    """
    adx      = ind["adx"]
    adx_pos  = ind["adx_pos"]
    adx_neg  = ind["adx_neg"]
    macd_d   = ind["macd_diff"]
    rsi      = ind["rsi"]

    # Choppy / ranging market
    if adx < 18:
        confidence = max(0.55, 0.85 - adx / 100)
        return REGIME_CHOPPY, round(confidence, 2)

    # Trend strength base confidence
    trend_conf = min(0.95, 0.60 + adx * 0.01)

    bullish_votes = 0
    bearish_votes = 0

    if adx_pos > adx_neg:
        bullish_votes += 1
    else:
        bearish_votes += 1

    if macd_d > 0:
        bullish_votes += 1
    else:
        bearish_votes += 1

    if rsi > 55:
        bullish_votes += 1
    elif rsi < 45:
        bearish_votes += 1

    if bullish_votes >= 2:
        # Scale confidence by vote unanimity
        conf = trend_conf * (0.8 + 0.1 * bullish_votes)
        return REGIME_BULLISH, round(min(conf, 0.97), 2)

    if bearish_votes >= 2:
        conf = trend_conf * (0.8 + 0.1 * bearish_votes)
        return REGIME_BEARISH, round(min(conf, 0.97), 2)

    return REGIME_NEUTRAL, 0.55


def classify_volatility(atr_ratio: float) -> str:
    """
    Map ATR ratio to a volatility label.
      HIGH   — ATR > 2× average
      LOW    — ATR < 0.5× average
      NORMAL — everything else
    """
    if atr_ratio > ATR_HIGH_THRESHOLD:
        return "HIGH"
    if atr_ratio < ATR_LOW_THRESHOLD:
        return "LOW"
    return "NORMAL"


def classify_dxy_conflict(
    pair: str,
    pair_regime: str,
    dxy_regime: Optional[str],
) -> bool:
    """
    Return True when the DXY trend conflicts with the pair's signal direction.

    Logic:
      USD_FOLLOW pairs (XAUUSD):
        DXY BULLISH → pair should be BEARISH; conflict if pair is BULLISH
        DXY BEARISH → pair should be BULLISH; conflict if pair is BEARISH
      CROSS (XAUEUR):
        No direct DXY conflict
    """
    if dxy_regime is None or dxy_regime == REGIME_NEUTRAL or dxy_regime == REGIME_CHOPPY:
        return False

    role = DXY_ROLE.get(pair, "CROSS")

    if role == "USD_FOLLOW":
        # Strong DXY → USD-follow pairs should be BEARISH
        if dxy_regime == REGIME_BULLISH and pair_regime == REGIME_BULLISH:
            return True
        if dxy_regime == REGIME_BEARISH and pair_regime == REGIME_BEARISH:
            return True

    return False


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — MONGODB HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

async def get_previous_regime(db, pair: str) -> Optional[str]:
    """Retrieve the last stored regime for a pair from regime_state."""
    try:
        doc = await db.regime_state.find_one({"pair": pair})
        return doc.get("regime") if doc else None
    except Exception as exc:
        logger.error(f"[{pair}] DB read error (regime_state): {exc}")
        return None


async def upsert_regime_state(
    db,
    pair: str,
    regime: str,
    volatility: str,
    atr_ratio: float,
    correlation_strength: float,
    momentum_paused: bool,
    sl_multiplier: float,
) -> None:
    """Upsert the regime_state document for a pair."""
    try:
        await db.regime_state.update_one(
            {"pair": pair},
            {
                "$set": {
                    "pair":                 pair,
                    "regime":               regime,
                    "volatility":           volatility,
                    "atr_ratio":            round(atr_ratio, 4),
                    "correlation_strength": round(correlation_strength, 4),
                    "momentum_paused":      momentum_paused,
                    "sl_multiplier":        round(sl_multiplier, 4),
                    "updated_at":           datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
    except Exception as exc:
        logger.error(f"[{pair}] DB write error (regime_state): {exc}")


async def close_active_signals(
    db,
    pair: str,
    new_status: str,
    reason: str,
) -> int:
    """
    Mark all ACTIVE signals for a pair with new_status.
    Returns the count of signals closed.
    """
    try:
        result = await db.signals.update_many(
            {"pair": pair, "status": "ACTIVE"},
            {
                "$set": {
                    "status":    new_status,
                    "result":    "PAUSED",
                    "closed_at": datetime.now(timezone.utc),
                    "close_reason": reason,
                }
            },
        )
        return result.modified_count
    except Exception as exc:
        logger.error(f"[{pair}] DB write error (signals close): {exc}")
        return 0


async def reduce_signal_confidence(db, pair: str, reduction: float = 0.20) -> int:
    """
    Reduce confidence by `reduction` fraction for all ACTIVE signals of a pair.
    Marks them as CORRELATION_ADJUSTED.
    Returns count updated.
    """
    try:
        active = await db.signals.find(
            {"pair": pair, "status": "ACTIVE"}
        ).to_list(length=200)

        updated = 0
        for sig in active:
            old_conf = sig.get("confidence", 100.0)
            new_conf = round(old_conf * (1.0 - reduction), 2)
            await db.signals.update_one(
                {"_id": sig["_id"]},
                {
                    "$set": {
                        "confidence":           new_conf,
                        "correlation_adjusted": True,
                        "status":               "CORRELATION_ADJUSTED",
                    }
                },
            )
            updated += 1

        return updated
    except Exception as exc:
        logger.error(f"[{pair}] DB write error (confidence reduce): {exc}")
        return 0


async def log_rescanner_action(
    db,
    pair: str,
    action: str,
    reason: str,
    details: Dict[str, Any],
) -> None:
    """Append a record to rescanner_log."""
    try:
        await db.rescanner_log.insert_one(
            {
                "timestamp": datetime.now(timezone.utc),
                "pair":      pair,
                "action":    action,
                "reason":    reason,
                "details":   details,
            }
        )
    except Exception as exc:
        logger.error(f"[{pair}] DB write error (rescanner_log): {exc}")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — TELEGRAM ALERTS
# ═══════════════════════════════════════════════════════════════════════════════

async def send_telegram(bot: Optional[Bot], message: str) -> None:
    """
    Send a plain-text Telegram message to the Gold channel with basic retry
    on flood control. Silently skips if bot is not configured.
    """
    if bot is None:
        logger.info(f"[Telegram] (not configured) {message[:80]}")
        return

    try:
        await bot.send_message(
            chat_id=TELEGRAM_GOLD_CHANNEL_ID,
            text=message,
            parse_mode="HTML",
        )
        await asyncio.sleep(TG_SEND_DELAY)
    except RetryAfter as exc:
        logger.warning(f"[Telegram] Flood control — waiting {exc.retry_after}s")
        await asyncio.sleep(exc.retry_after + 1)
        try:
            await bot.send_message(
                chat_id=TELEGRAM_GOLD_CHANNEL_ID,
                text=message,
                parse_mode="HTML",
            )
        except TelegramError as retry_exc:
            logger.error(f"[Telegram] Retry failed: {retry_exc}")
    except TelegramError as exc:
        logger.error(f"[Telegram] Send error: {exc}")


def fmt_momentum_shift(
    pair: str,
    old_regime: str,
    new_regime: str,
    signals_closed: int,
) -> str:
    return (
        f"⚠️ <b>MOMENTUM SHIFT: {pair}</b>\n"
        f"📊 Regime: <b>{old_regime} → {new_regime}</b>\n"
        f"🔴 Signals paused: <b>{signals_closed}</b>\n"
        f"⏸ Signals paused for 4h — no new entries until regime stabilises.\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )


def fmt_volatility_alert(pair: str, vol_state: str, atr_ratio: float, signals_closed: int) -> str:
    emoji = "🔥" if vol_state == "HIGH" else "😴"
    label = "TOO VOLATILE" if vol_state == "HIGH" else "TOO CHOPPY"
    return (
        f"{emoji} <b>VOLATILITY ALERT: {pair}</b>\n"
        f"📈 ATR ratio: <b>{atr_ratio:.2f}×</b> ({label})\n"
        f"🛑 Signals closed: <b>{signals_closed}</b>\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )


def fmt_correlation_alert(pair: str, dxy_regime: str, pair_regime: str, adjusted: int) -> str:
    return (
        f"🔗 <b>CORRELATION CONFLICT: {pair}</b>\n"
        f"📊 DXY: <b>{dxy_regime}</b> | Pair: <b>{pair_regime}</b>\n"
        f"⚡ Confidence reduced 20% on <b>{adjusted}</b> signal(s).\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )


def fmt_summary(
    total: int,
    regime_changes: int,
    vol_pauses: int,
    corr_adjustments: int,
    signals_closed: int,
    duration_s: float,
) -> str:
    return (
        f"✅ <b>4H Gold Rescanner Complete</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 Pairs scanned:       <b>{total}</b>\n"
        f"🔄 Regime changes:      <b>{regime_changes}</b>\n"
        f"⏸ Volatility pauses:   <b>{vol_pauses}</b>\n"
        f"🔗 Correlation adjusts: <b>{corr_adjustments}</b>\n"
        f"🛑 Signals closed:      <b>{signals_closed}</b>\n"
        f"⏱ Duration:            <b>{duration_s:.1f}s</b>\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — DYNAMIC MULTIPLIER CALCULATION
# ═══════════════════════════════════════════════════════════════════════════════

def compute_sl_multiplier(base: float, vol_state: str) -> float:
    """
    Adjust the SL ATR multiplier based on current volatility state.
      HIGH volatility → widen SL by 1.2×
      LOW  volatility → tighten SL by 0.8×
      NORMAL          → keep base
    """
    if vol_state == "HIGH":
        return round(base * 1.2, 4)
    if vol_state == "LOW":
        return round(base * 0.8, 4)
    return base


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — PER-PAIR PROCESSING
# ═══════════════════════════════════════════════════════════════════════════════

async def process_pair(
    db,
    session: aiohttp.ClientSession,
    bot: Optional[Bot],
    pair: str,
    dxy_regime: Optional[str],
) -> Dict[str, Any]:
    """
    Full 4H rescan for a single gold pair.

    Returns a result dict with keys:
      pair, regime, volatility, regime_changed, vol_paused,
      corr_conflict, signals_closed, corr_adjusted, error
    """
    result: Dict[str, Any] = {
        "pair":           pair,
        "regime":         REGIME_NEUTRAL,
        "volatility":     "NORMAL",
        "regime_changed": False,
        "vol_paused":     False,
        "corr_conflict":  False,
        "signals_closed": 0,
        "corr_adjusted":  0,
        "error":          None,
    }

    # ── 1. Fetch candles ─────────────────────────────────────────────────────
    df = await fetch_4h_candles(session, pair)
    if df is None:
        result["error"] = "fetch_failed"
        logger.warning(f"[{pair}] Skipping — no candle data")
        return result

    # ── 2. Calculate indicators ──────────────────────────────────────────────
    ind = calculate_indicators(df)
    if ind is None:
        result["error"] = "indicator_failed"
        logger.warning(f"[{pair}] Skipping — indicator calculation failed")
        return result

    # ── 3. Classify regime & volatility ─────────────────────────────────────
    new_regime, confidence = classify_regime(ind)
    vol_state              = classify_volatility(ind["atr_ratio"])
    result["regime"]       = new_regime
    result["volatility"]   = vol_state

    logger.info(
        f"[{pair}] Regime={new_regime} ({confidence:.0%}) | "
        f"ADX={ind['adx']:.1f} | RSI={ind['rsi']:.1f} | "
        f"ATR_ratio={ind['atr_ratio']:.2f} | Vol={vol_state}"
    )

    # ── 4. Retrieve previous regime ──────────────────────────────────────────
    prev_regime = await get_previous_regime(db, pair)

    # ── 5. DXY correlation conflict check ───────────────────────────────────
    corr_conflict = classify_dxy_conflict(pair, new_regime, dxy_regime)
    result["corr_conflict"] = corr_conflict

    # Correlation strength: 1.0 if conflict, 0.0 if aligned, 0.5 if neutral
    if corr_conflict:
        corr_strength = 1.0
    elif dxy_regime in (REGIME_NEUTRAL, REGIME_CHOPPY, None):
        corr_strength = 0.5
    else:
        corr_strength = 0.0

    # ── 6. Dynamic SL multiplier ─────────────────────────────────────────────
    base_sl = 1.5  # Default ATR multiplier
    sl_multiplier = compute_sl_multiplier(base_sl, vol_state)

    # ── 7. Determine momentum_paused ─────────────────────────────────────────
    regime_changed  = (prev_regime is not None) and (prev_regime != new_regime)
    vol_extreme     = vol_state in ("HIGH", "LOW")
    momentum_paused = regime_changed or vol_extreme
    result["regime_changed"] = regime_changed
    result["vol_paused"]     = vol_extreme

    # ── 8. Persist regime state ──────────────────────────────────────────────
    await upsert_regime_state(
        db,
        pair=pair,
        regime=new_regime,
        volatility=vol_state,
        atr_ratio=ind["atr_ratio"],
        correlation_strength=corr_strength,
        momentum_paused=momentum_paused,
        sl_multiplier=sl_multiplier,
    )

    # ── 9. Handle regime change ──────────────────────────────────────────────
    if regime_changed:
        closed = await close_active_signals(
            db, pair, new_status="PAUSED",
            reason=f"Regime shift {prev_regime}→{new_regime}"
        )
        result["signals_closed"] += closed

        await log_rescanner_action(
            db, pair=pair, action="REGIME_CHANGE",
            reason=f"{prev_regime} → {new_regime}",
            details={
                "prev_regime":    prev_regime,
                "new_regime":     new_regime,
                "confidence":     confidence,
                "adx":            ind["adx"],
                "rsi":            ind["rsi"],
                "macd_diff":      ind["macd_diff"],
                "signals_closed": closed,
            },
        )

        alert = fmt_momentum_shift(pair, prev_regime, new_regime, closed)
        await send_telegram(bot, alert)
        logger.info(f"[{pair}] ⚠️  Regime change {prev_regime}→{new_regime} | {closed} signal(s) paused")

    # ── 10. Handle volatility extreme ────────────────────────────────────────
    if vol_extreme:
        close_status = "VOLATILITY_STOP"
        reason_str   = (
            f"ATR ratio {ind['atr_ratio']:.2f}× — {'too volatile' if vol_state == 'HIGH' else 'too choppy'}"
        )
        closed = await close_active_signals(db, pair, new_status=close_status, reason=reason_str)
        result["signals_closed"] += closed

        await log_rescanner_action(
            db, pair=pair, action="VOLATILITY_STOP",
            reason=reason_str,
            details={
                "vol_state":      vol_state,
                "atr_ratio":      ind["atr_ratio"],
                "atr":            ind["atr"],
                "atr_avg":        ind["atr_avg"],
                "signals_closed": closed,
            },
        )

        if closed > 0:
            alert = fmt_volatility_alert(pair, vol_state, ind["atr_ratio"], closed)
            await send_telegram(bot, alert)
            logger.info(f"[{pair}] 🔥 Volatility {vol_state} ({ind['atr_ratio']:.2f}×) | {closed} signal(s) stopped")

    # ── 11. Handle DXY correlation conflict ──────────────────────────────────
    if corr_conflict and not momentum_paused:
        adjusted = await reduce_signal_confidence(db, pair, reduction=0.20)
        result["corr_adjusted"] = adjusted

        if adjusted > 0:
            await log_rescanner_action(
                db, pair=pair, action="CORRELATION_ADJUSTED",
                reason=f"DXY {dxy_regime} conflicts with pair {new_regime}",
                details={
                    "dxy_regime":  dxy_regime,
                    "pair_regime": new_regime,
                    "dxy_role":    DXY_ROLE.get(pair, "CROSS"),
                    "adjusted":    adjusted,
                },
            )
            alert = fmt_correlation_alert(pair, dxy_regime, new_regime, adjusted)
            await send_telegram(bot, alert)
            logger.info(f"[{pair}] 🔗 DXY conflict — {adjusted} signal(s) confidence reduced 20%")

    # ── 12. Resume signals if regime stable & volatility normal ──────────────
    if not momentum_paused and not corr_conflict and prev_regime == new_regime:
        # Ensure momentum_paused is cleared in DB (already done via upsert above)
        logger.info(f"[{pair}] ✅ Regime stable ({new_regime}) — signals active")

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

async def run_rescanner() -> None:
    """
    Main entry point.
    1. Connect to MongoDB
    2. Initialise Telegram bot (Gold channel)
    3. Fetch DXY regime (used for XAUUSD correlation check)
    4. Process XAUUSD and XAUEUR sequentially
    5. Send summary alert to Gold channel
    """
    start_time = datetime.now(timezone.utc)
    logger.info("=" * 60)
    logger.info("🥇 4H Gold Rescanner starting")
    logger.info(f"   Pairs: {ALL_PAIRS} | DB: {DB_NAME}")
    logger.info("=" * 60)

    # ── MongoDB ───────────────────────────────────────────────────────────────
    mongo_client = AsyncIOMotorClient(
        MONGO_URL,
        serverSelectionTimeoutMS=10_000,
        connectTimeoutMS=10_000,
        socketTimeoutMS=30_000,
    )
    db = mongo_client[DB_NAME]

    # Ensure indexes exist (idempotent)
    try:
        await db.regime_state.create_index([("pair", 1)], unique=True)
        await db.rescanner_log.create_index([("timestamp", -1)])
        await db.rescanner_log.create_index([("pair", 1), ("timestamp", -1)])
    except Exception as exc:
        logger.warning(f"Index creation warning (non-fatal): {exc}")

    # ── Telegram ──────────────────────────────────────────────────────────────
    bot: Optional[Bot] = None
    if TELEGRAM_BOT_TOKEN:
        try:
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            bot_info = await bot.get_me()
            logger.info(f"✅ Telegram bot connected: @{bot_info.username}")
            logger.info(f"   Alerts → Gold channel: {TELEGRAM_GOLD_CHANNEL_ID}")
        except TelegramError as exc:
            logger.warning(f"Telegram init failed (alerts disabled): {exc}")
            bot = None
    else:
        logger.warning("TELEGRAM_BOT_TOKEN not set — alerts disabled")

    # ── HTTP session (shared across all pair fetches) ─────────────────────────
    connector = aiohttp.TCPConnector(limit=5)
    async with aiohttp.ClientSession(connector=connector) as session:

        # ── Step 1: Fetch DXY regime ─────────────────────────────────────────
        dxy_regime: Optional[str] = None
        logger.info("Fetching DXY 4H candles for XAUUSD correlation baseline...")
        dxy_df = await fetch_4h_candles(session, "DXY")
        if dxy_df is not None:
            dxy_ind = calculate_indicators(dxy_df)
            if dxy_ind:
                dxy_regime, dxy_conf = classify_regime(dxy_ind)
                logger.info(
                    f"DXY regime: {dxy_regime} ({dxy_conf:.0%}) | "
                    f"ADX={dxy_ind['adx']:.1f} | RSI={dxy_ind['rsi']:.1f}"
                )
        else:
            logger.warning("DXY data unavailable — XAUUSD correlation check skipped")

        # ── Step 2: Process each gold pair ────────────────────────────────────
        totals = {
            "regime_changes":    0,
            "vol_pauses":        0,
            "corr_adjustments":  0,
            "signals_closed":    0,
            "errors":            0,
        }

        for pair in ALL_PAIRS:
            try:
                res = await process_pair(db, session, bot, pair, dxy_regime)

                if res["error"]:
                    totals["errors"] += 1
                else:
                    if res["regime_changed"]:
                        totals["regime_changes"] += 1
                    if res["vol_paused"]:
                        totals["vol_pauses"] += 1
                    if res["corr_adjusted"] > 0:
                        totals["corr_adjustments"] += 1
                    totals["signals_closed"] += res["signals_closed"]

            except Exception as exc:
                logger.error(f"[{pair}] Unhandled error in process_pair: {exc}", exc_info=True)
                totals["errors"] += 1

            # Polite rate-limiting between pairs (Twelve Data free tier: 8 req/min)
            await asyncio.sleep(8)

    # ── Step 3: Summary ───────────────────────────────────────────────────────
    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()

    logger.info("=" * 60)
    logger.info(f"✅ Gold Rescanner complete in {elapsed:.1f}s")
    logger.info(f"   Regime changes:      {totals['regime_changes']}")
    logger.info(f"   Volatility pauses:   {totals['vol_pauses']}")
    logger.info(f"   Correlation adjusts: {totals['corr_adjustments']}")
    logger.info(f"   Signals closed:      {totals['signals_closed']}")
    logger.info(f"   Errors:              {totals['errors']}")
    logger.info("=" * 60)

    # Send summary only if something notable happened
    notable = (
        totals["regime_changes"] > 0
        or totals["vol_pauses"] > 0
        or totals["corr_adjustments"] > 0
        or totals["signals_closed"] > 0
    )
    if notable:
        summary_msg = fmt_summary(
            total=len(ALL_PAIRS),
            regime_changes=totals["regime_changes"],
            vol_pauses=totals["vol_pauses"],
            corr_adjustments=totals["corr_adjustments"],
            signals_closed=totals["signals_closed"],
            duration_s=elapsed,
        )
        await send_telegram(bot, summary_msg)

    # ── Cleanup ───────────────────────────────────────────────────────────────
    mongo_client.close()
    logger.info("MongoDB connection closed. Gold Rescanner done.")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def _handle_sigterm(signum, frame):
    logger.info("SIGTERM received — shutting down gracefully")
    sys.exit(0)


if __name__ == "__main__":
    _signal.signal(_signal.SIGTERM, _handle_sigterm)

    try:
        asyncio.run(run_rescanner())
    except KeyboardInterrupt:
        logger.info("Interrupted by user — exiting")
        sys.exit(0)
    except Exception as exc:
        logger.critical(f"Fatal error in gold rescanner: {exc}", exc_info=True)
        sys.exit(1)

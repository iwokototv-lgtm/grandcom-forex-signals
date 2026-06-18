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

# Risk & position management modules
from ml_engine.position_manager import PositionManager, position_manager as _pm_singleton
from ml_engine.reversal_detector import ReversalDetector, reversal_detector as _rd_singleton
from ml_engine.risk_manager import RiskManager
from ml_engine.economic_calendar_filter import EconomicCalendarFilter, economic_calendar_filter as _ecf_singleton
from ml_engine.drawdown_recovery import DrawdownRecoveryManager, drawdown_recovery as _ddr_singleton
from ml_engine.position_monitor import PositionMonitor, position_monitor as _position_monitor_singleton
from ml_engine.candle_tracker import CandleTracker, candle_tracker as _candle_tracker_singleton
from ml_engine.signal_validator import SignalValidator, signal_validator as _signal_validator_singleton

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
# Hybrid scheduler: separate signal generation (30 min) from position monitoring (2 min)
SIGNAL_GENERATION_INTERVAL_MINUTES = int(
    os.environ.get("SIGNAL_GENERATION_INTERVAL_MINUTES", "30")
)
POSITION_MONITORING_INTERVAL_MINUTES = int(
    os.environ.get("POSITION_MONITORING_INTERVAL_MINUTES", "2")
)
MIN_CONFIDENCE = int(os.environ.get("MIN_CONFIDENCE", "60"))
ACCOUNT_BALANCE = float(os.environ.get("DEFAULT_ACCOUNT_BALANCE", "10000.0"))
# Smart 4H candle detection — skip signal if same candle as last processed
CANDLE_TRACKING_ENABLED = os.environ.get("CANDLE_TRACKING_ENABLED", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Trading Pairs
# ---------------------------------------------------------------------------
PAIRS = {
    "XAUUSD": {
        "symbol": "XAU/USD",
        "decimals": 2,
        "atr_sl": 0.97,   # SL: 0.97x ATR (~14.59 pips at typical 15 ATR) — 4H swing
        "atr_tp1": 0.33,  # TP1: 0.33x ATR (~5.0 pips at typical 15 ATR) — quick exit
        "atr_tp2": 0.67,  # TP2: 0.67x ATR (~10.0 pips at typical 15 ATR) — mid target
        "atr_tp3": 1.0,   # TP3: 1.0x ATR (~15.0 pips at typical 15 ATR) — full target
    },
    "XAUEUR": {
        "symbol": "XAU/EUR",
        "decimals": 2,
        "atr_sl": 0.97,   # SL: 0.97x ATR (~14.59 pips at typical 15 ATR) — 4H swing
        "atr_tp1": 0.33,  # TP1: 0.33x ATR (~5.0 pips at typical 15 ATR) — quick exit
        "atr_tp2": 0.67,  # TP2: 0.67x ATR (~10.0 pips at typical 15 ATR) — mid target
        "atr_tp3": 1.0,   # TP3: 1.0x ATR (~15.0 pips at typical 15 ATR) — full target
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
# Risk / Position Management Singletons
# ---------------------------------------------------------------------------
_position_manager: PositionManager = _pm_singleton
_reversal_detector: ReversalDetector = _rd_singleton
_risk_manager: RiskManager = RiskManager()
_calendar_filter: EconomicCalendarFilter = _ecf_singleton
_drawdown_recovery: DrawdownRecoveryManager = _ddr_singleton
_pos_monitor: PositionMonitor = _position_monitor_singleton
_candle_tracker: CandleTracker = _candle_tracker_singleton
_signal_validator: SignalValidator = _signal_validator_singleton


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


async def fetch_ohlcv_with_retry(
    pair: str,
    interval: str = "4h",
    outputsize: int = 100,
    max_retries: int = 3,
    backoff_factor: float = 2.0,
) -> pd.DataFrame | None:
    """
    Fetch OHLCV from TwelveData with exponential backoff retry.

    Retries up to 3 times with exponential backoff:
    - Attempt 1: Immediate
    - Attempt 2: Wait 1 second
    - Attempt 3: Wait 2 seconds
    - Attempt 4: Wait 4 seconds

    Total max time: ~7 seconds (well within 30-min cycle)
    """
    cfg = PAIRS[pair]
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={cfg['symbol']}&interval={interval}&outputsize={outputsize}"
        f"&apikey={TWELVE_DATA_API_KEY}"
    )

    last_error = None

    for attempt in range(max_retries):
        try:
            logger.info(f"[{pair}] Fetching OHLCV (attempt {attempt + 1}/{max_retries})")

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=30),  # Increased from 15s to 30s
                ) as resp:
                    data = await resp.json()

            # Check for API error response
            if "values" not in data:
                error_msg = data.get("message", str(data))
                logger.warning(
                    f"[{pair}] TwelveData API error: {error_msg} "
                    f"(attempt {attempt + 1}/{max_retries})"
                )
                last_error = error_msg

                # Retry on API error
                if attempt < max_retries - 1:
                    wait_time = backoff_factor ** attempt
                    logger.info(f"[{pair}] Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    continue

                # All retries exhausted
                return None

            # Success
            df = pd.DataFrame(data["values"])
            for col in ("open", "high", "low", "close"):
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.iloc[::-1].reset_index(drop=True)

            logger.info(
                f"[{pair}] Fetched {len(df)} {interval} candles "
                f"(attempt {attempt + 1}/{max_retries})"
            )
            return df

        except asyncio.TimeoutError:
            logger.warning(
                f"[{pair}] API timeout (30s) "
                f"(attempt {attempt + 1}/{max_retries})"
            )
            last_error = "API timeout (30s)"

            if attempt < max_retries - 1:
                wait_time = backoff_factor ** attempt
                logger.info(f"[{pair}] Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
                continue

            return None

        except aiohttp.ClientError as exc:
            logger.warning(
                f"[{pair}] HTTP client error: {exc} "
                f"(attempt {attempt + 1}/{max_retries})"
            )
            last_error = str(exc)

            if attempt < max_retries - 1:
                wait_time = backoff_factor ** attempt
                logger.info(f"[{pair}] Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
                continue

            return None

        except Exception as exc:
            logger.error(
                f"[{pair}] Unexpected error: {exc} "
                f"(attempt {attempt + 1}/{max_retries})"
            )
            last_error = str(exc)

            if attempt < max_retries - 1:
                wait_time = backoff_factor ** attempt
                logger.info(f"[{pair}] Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
                continue

            return None

    logger.error(f"[{pair}] All {max_retries} fetch attempts failed: {last_error}")
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
    position_count: int = 0,
    exposure_pct: float = 0.0,
    risk_status: Optional[dict] = None,
    max_retries: int = 3,
) -> bool:
    """Send signal to Telegram with v3.0 context + risk/position data.

    Returns True if the signal was delivered successfully, False otherwise.
    Retries up to max_retries times with exponential backoff (1s, 2s, 4s).
    """
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

    rs = risk_status or {}
    daily_pnl = rs.get("daily_pnl", 0.0)
    daily_loss_pct = rs.get("daily_loss_pct", 0.0)
    drawdown_pct = rs.get("drawdown_pct", 0.0)
    risk_level = rs.get("risk_level", "GREEN")
    risk_emoji = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(risk_level, "⚪")

    info_msg = (
        f"<b>📊 R:R:</b> 1:{rr}  "
        f"<b>⚡ Confidence:</b> {confidence}%\n"
        f"<b>🎯 Regime:</b> {regime}  "
        f"<b>📐 SMC:</b> {smc_score}/10  "
        f"<b>🔗 MTF:</b> {mtf_alignment:.0f}%\n"
        f"<b>📈 Positions:</b> {position_count}/5  "
        f"<b>💰 Exposure:</b> {exposure_pct:.1f}%\n"
        f"<b>📉 Daily P&L:</b> ${daily_pnl:+.2f} ({daily_loss_pct:.1f}%)  "
        f"<b>📉 Drawdown:</b> {drawdown_pct:.1f}%\n"
        f"<b>🛡 Risk:</b> {risk_emoji} {risk_level}\n"
        f"<b>📝</b> {_html_escape(analysis)}\n"
        f"<i>⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
        f"| Grandcom Gold Engine v3.0</i>"
    )

    for attempt in range(max_retries):
        try:
            bot = get_bot()
            await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=copier_msg)
            await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=info_msg, parse_mode="HTML")
            logger.info(
                f"[{pair}] ✅ Signal sent to Telegram (attempt {attempt + 1}/{max_retries}) "
                f"— {signal} confidence={confidence}%"
            )
            return True
        except asyncio.TimeoutError:
            logger.warning(f"[{pair}] Telegram timeout (attempt {attempt + 1}/{max_retries})")
        except Exception as exc:
            logger.warning(f"[{pair}] Telegram delivery error (attempt {attempt + 1}/{max_retries}): {exc}")

        if attempt < max_retries - 1:
            wait_time = 2 ** attempt  # 1s, 2s, 4s
            logger.info(f"[{pair}] Retrying Telegram send in {wait_time}s...")
            await asyncio.sleep(wait_time)

    logger.error(
        f"[{pair}] ❌ Failed to send signal to Telegram after {max_retries} attempts "
        f"— {signal} confidence={confidence}%"
    )
    return False


async def send_reversal_alert(pair: str, reason: str, closed_count: int, total_pnl: float) -> None:
    """Send an immediate reversal / close-all alert to Telegram.

    Only sends alert if positions were actually closed (closed_count > 0).
    """
    # Guard: Don't send alert if no positions were closed
    if closed_count == 0:
        logger.debug(f"[{pair}] Reversal detected but no positions closed, skipping alert")
        return

    try:
        bot = get_bot()
        msg = (
            f"🔄 <b>REVERSAL DETECTED — {pair}</b>\n"
            f"\n"
            f"<b>Reason:</b> {_html_escape(reason)}\n"
            f"<b>Positions closed:</b> {closed_count}\n"
            f"<b>Total P&L:</b> ${total_pnl:+.2f}\n"
            f"<i>⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</i>"
        )
        await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=msg, parse_mode="HTML")
        logger.info(f"[{pair}] Reversal alert sent: {reason} (closed {closed_count})")
    except Exception as exc:
        logger.error(f"[{pair}] Reversal alert failed: {exc}")


async def send_position_monitor_alert(msg: str) -> None:
    """Send a position-monitor close alert to Telegram (HTML parse mode)."""
    try:
        bot = get_bot()
        await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=msg, parse_mode="HTML")
    except Exception as exc:
        logger.error(f"[POSITION_MON] Telegram alert failed: {exc}")


async def send_signal_failure_alert(
    pair: str,
    reason: str,
    cycle_time: datetime,
) -> None:
    """Send alert when signal generation fails."""
    try:
        bot = get_bot()
        msg = (
            f"⚠️ <b>SIGNAL GENERATION FAILED — {pair}</b>\n"
            f"\n"
            f"<b>Reason:</b> {_html_escape(reason)}\n"
            f"<b>Cycle Time:</b> {cycle_time.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"<b>Action:</b> Waiting for next cycle...\n"
            f"<i>⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</i>"
        )
        await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=msg, parse_mode="HTML")
        logger.warning(f"[{pair}] Failure alert sent: {reason}")
    except Exception as exc:
        logger.error(f"[{pair}] Failure alert failed: {exc}")


async def send_signal_rejection_alert(pair: str, validation_result: dict) -> None:
    """Send alert when a signal is rejected during validation."""
    try:
        bot = get_bot()
        checks_failed = ", ".join(validation_result.get("checks_failed", [])) or "none"
        checks_passed = ", ".join(validation_result.get("checks_passed", [])) or "none"
        msg = (
            f"⚠️ <b>SIGNAL REJECTED — {pair}</b>\n"
            f"\n"
            f"<b>Reason:</b> {_html_escape(validation_result.get('reason', 'Unknown'))}\n"
            f"<b>Original Signal:</b> {validation_result.get('signal', 'UNKNOWN')}\n"
            f"<b>Checks Failed:</b> {_html_escape(checks_failed)}\n"
            f"<b>Checks Passed:</b> {_html_escape(checks_passed)}\n"
            f"<i>⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</i>"
        )
        await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=msg, parse_mode="HTML")
        logger.info(f"[{pair}] Signal rejection alert sent to Telegram")
    except Exception as exc:
        logger.error(f"[{pair}] Failed to send rejection alert: {exc}")


async def log_signal_event(
    pair: str,
    event_type: str,  # "generated", "validated", "rejected", "sent"
    signal: str,
    confidence: float,
    reason: str = "",
    metadata: dict = None,
) -> None:
    """Log signal event to MongoDB for audit trail."""
    if _db is None:
        return

    try:
        event = {
            "timestamp": datetime.now(timezone.utc),
            "pair": pair,
            "event_type": event_type,
            "signal": signal,
            "confidence": confidence,
            "reason": reason,
            "metadata": metadata or {},
        }
        await _db.signal_events.insert_one(event)
        logger.debug(f"[{pair}] Signal event logged: {event_type}")
    except Exception as exc:
        logger.warning(f"[{pair}] Failed to log signal event: {exc}")


# ---------------------------------------------------------------------------
# Signal Metrics
# ---------------------------------------------------------------------------
class SignalMetrics:
    """Track signal generation metrics."""

    def __init__(self):
        self.total_cycles = 0
        self.successful_signals = 0
        self.failed_cycles = 0
        self.retry_attempts = 0
        self.api_timeouts = 0
        self.api_errors = 0

    async def log_metrics(self) -> dict:
        """Return current metrics."""
        success_rate = (
            (self.successful_signals / self.total_cycles * 100)
            if self.total_cycles > 0
            else 0
        )
        return {
            "total_cycles": self.total_cycles,
            "successful_signals": self.successful_signals,
            "failed_cycles": self.failed_cycles,
            "success_rate": f"{success_rate:.1f}%",
            "retry_attempts": self.retry_attempts,
            "api_timeouts": self.api_timeouts,
            "api_errors": self.api_errors,
        }


# Singleton metrics tracker
_signal_metrics = SignalMetrics()


# ---------------------------------------------------------------------------
# Close All Positions
# ---------------------------------------------------------------------------
async def close_all_positions(reason: str = "SYSTEM") -> dict:
    """
    Close every open position across all pairs.
    Called on reversal detection, daily loss limit, or drawdown limit.
    Sends a Telegram notification only if positions were actually closed.
    """
    # Fetch current prices for P&L calculation
    price_map: dict = {}
    for pair in PAIRS:
        try:
            df = await fetch_ohlcv(pair, interval="4h", outputsize=5)
            if df is not None and len(df) > 0:
                price_map[pair] = float(df.iloc[-1]["close"])
        except Exception:
            pass

    result = await _position_manager.close_all_positions(
        exit_price_map=price_map,
        reason=reason,
    )

    closed = result.get("closed", 0)
    total_pnl = result.get("total_pnl", 0.0)

    # Log with appropriate level based on whether positions were closed
    if closed > 0:
        logger.warning(
            f"close_all_positions: reason={reason} closed={closed} pnl={total_pnl:.2f}"
        )
    else:
        logger.info(
            f"close_all_positions: reason={reason} closed={closed} (no positions to close)"
        )

    # Only send Telegram notification if positions were actually closed
    if closed > 0:
        try:
            bot = get_bot()
            msg = (
                f"🛑 <b>ALL POSITIONS CLOSED</b>\n"
                f"\n"
                f"<b>Reason:</b> {_html_escape(reason)}\n"
                f"<b>Positions closed:</b> {closed}\n"
                f"<b>Total P&L:</b> ${total_pnl:+.2f}\n"
                f"<i>⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</i>"
            )
            await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=msg, parse_mode="HTML")
            logger.info(f"close_all_positions: Telegram alert sent")
        except Exception as exc:
            logger.error(f"close_all_positions Telegram alert failed: {exc}")

    return result


# ---------------------------------------------------------------------------
# Core Signal Generation
# ---------------------------------------------------------------------------
async def generate_signal(pair: str) -> None:
    """
    Signal generation pipeline:
    1. Generate signal from hybrid system
    2. Validate signal (5 checks)
    3. Send Telegram notification  ← BEFORE position check
    4. Attempt position registration
    5. If blocked, don't execute trade (but signal was already sent)
    """
    cfg = PAIRS[pair]
    cycle_time = datetime.now(timezone.utc)
    logger.info(f"[{pair}] Starting v3.0 signal generation at {cycle_time.isoformat()}")

    _signal_metrics.total_cycles += 1

    # ── PRE-SIGNAL GUARDS ────────────────────────────────────────────────────

    # Guard 1: Economic calendar blackout
    try:
        if await _calendar_filter.is_blackout_period(pair):
            next_ev = await _calendar_filter.get_next_high_impact_event(pair)
            ev_name = next_ev.get("event", "?") if next_ev else "?"
            ev_min = next_ev.get("minutes_away", "?") if next_ev else "?"
            logger.info(
                f"[{pair}] NEWS BLACKOUT — skipping signal "
                f"(next event: {ev_name} in {ev_min} min)"
            )
            return
    except Exception as exc:
        logger.warning(f"[{pair}] Calendar check error (fail-open): {exc}")

    # Guard 2: Daily loss / drawdown limits
    try:
        risk_check = await _risk_manager.enforce_risk_limits()
        if not risk_check.get("trading_allowed", True):
            reason = risk_check.get("reason", "RISK_LIMIT")
            logger.warning(f"[{pair}] Trading halted by risk manager: {reason}")
            # Close all open positions if not already done
            open_count = await _position_manager.get_position_count()
            if open_count > 0:
                await close_all_positions(reason=reason)
            return
    except Exception as exc:
        logger.warning(f"[{pair}] Risk manager check error (fail-open): {exc}")

    # ── PRICE DATA ───────────────────────────────────────────────────────────

    # 1. Price data — with retry and exponential backoff
    df = await fetch_ohlcv_with_retry(pair, interval="4h", outputsize=100)
    if df is None or len(df) < 52:
        reason = "Insufficient candles or API failure"
        logger.error(f"[{pair}] {reason}")
        _signal_metrics.failed_cycles += 1
        await send_signal_failure_alert(pair, reason, cycle_time)
        return

    # 2. Indicators
    ind = compute_indicators(df, cfg["decimals"])
    if ind is None:
        return

    # ── REVERSAL DETECTION ───────────────────────────────────────────────────

    # Guard 3: Reversal detection — ONLY if positions exist
    try:
        # Check if any positions exist BEFORE running reversal detection
        open_count = await _position_manager.get_position_count()

        if open_count > 0:
            # Only run reversal detection if positions exist
            current_regime = ind.get("trend", "NEUTRAL")
            reversal = await _reversal_detector.detect_reversal(pair, df, current_regime)

            if reversal.get("reversal_detected"):
                rev_reason = reversal.get("reason", "REVERSAL")
                logger.warning(f"[{pair}] {rev_reason} — closing all positions")
                close_result = await close_all_positions(reason=f"REVERSAL: {rev_reason}")

                # Only send alert if positions were actually closed
                closed_count = close_result.get("closed", 0)
                if closed_count > 0:
                    await send_reversal_alert(
                        pair,
                        rev_reason,
                        closed_count,
                        close_result.get("total_pnl", 0.0),
                    )
                    logger.info(f"[{pair}] Reversal alert sent (closed {closed_count} positions)")
                else:
                    logger.warning(f"[{pair}] Reversal detected but no positions to close")

                # Don't open new positions this cycle — let the market settle
                return
        else:
            # No positions exist — skip reversal detection entirely
            logger.debug(f"[{pair}] No open positions, skipping reversal detection")

    except Exception as exc:
        logger.warning(f"[{pair}] Reversal detection error (fail-open): {exc}")

    # ── HYBRID ANALYSIS ──────────────────────────────────────────────────────

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
                # Prefer confidence_pct (0–100 scale); fall back to confidence * 100
                "hybrid_confidence": (
                    hybrid_result.get("confidence_pct")
                    or round(float(hybrid_result.get("confidence", 0)) * 100, 1)
                ),
                "entry": hybrid_result.get("entry_price", 0),
                "analysis": hybrid_result.get("analysis", ""),
            }
            logger.info(
                f"[{pair}] Hybrid: signal={hybrid_ctx['hybrid_signal']} "
                f"confidence={hybrid_ctx['hybrid_confidence']:.1f}% "
                f"regime={hybrid_ctx['regime']} smc={hybrid_ctx['smc_score']}/10 "
                f"mtf={hybrid_ctx['mtf_alignment']:.0f}%"
            )
        except Exception as exc:
            logger.error(f"[{pair}] Hybrid system error: {exc}")

    # ── STAGE 1: GENERATE SIGNAL ─────────────────────────────────────────────

    # Use hybrid signal directly — GPT override removed.
    # The hybrid system is backtest-proven (45.1% win rate, 2.17 profit factor)
    # and GPT was weakening signals (75% → 40%) instead of confirming them.
    signal_type = str(hybrid_ctx.get("hybrid_signal", "NEUTRAL")).upper()
    confidence  = float(hybrid_ctx.get("hybrid_confidence", 0.0))
    entry       = float(hybrid_ctx.get("entry", 0) or ind["price"])
    if entry <= 0:
        entry = ind["price"]

    # Analysis from hybrid system
    analysis = hybrid_ctx.get("analysis", "")
    if not analysis:
        analysis = f"Hybrid signal: {signal_type} (confidence={confidence}%)"

    logger.info(
        f"[{pair}] ✅ STAGE 1 - SIGNAL GENERATED: {signal_type} "
        f"(confidence={confidence}%, entry={entry})"
    )
    await log_signal_event(
        pair=pair,
        event_type="generated",
        signal=signal_type,
        confidence=confidence,
        reason="Hybrid signal generated",
        metadata={"hybrid_signal": signal_type, "hybrid_confidence": confidence},
    )

    # ── STAGE 2: VALIDATE SIGNAL ─────────────────────────────────────────────

    # Pre-validation: NEUTRAL or unknown signal type
    if signal_type == "NEUTRAL" or signal_type not in ("BUY", "SELL"):
        reason = f"Hybrid returned {signal_type} — no actionable trade direction"
        logger.info(f"[{pair}] ❌ STAGE 2 - VALIDATION FAILED: {reason}")
        await log_signal_event(
            pair=pair,
            event_type="rejected",
            signal=signal_type,
            confidence=confidence,
            reason=reason,
        )
        return

    # Pre-validation: confidence below minimum
    if confidence < MIN_CONFIDENCE:
        reason = f"Confidence {confidence}% below {MIN_CONFIDENCE}% minimum threshold"
        logger.warning(f"[{pair}] ❌ STAGE 2 - VALIDATION FAILED: {reason}")
        await log_signal_event(
            pair=pair,
            event_type="rejected",
            signal=signal_type,
            confidence=confidence,
            reason=reason,
        )
        return

    # Calculate levels
    logger.info(f"[{pair}] Entry price: {entry} (from hybrid system)")
    tps, sl = build_levels(signal_type, entry, ind["atr"], cfg)

    # Geometry validation
    if signal_type == "BUY" and (tps[0] <= entry or sl >= entry):
        reason = f"BUY geometry invalid (TP1={tps[0]} <= entry={entry} or SL={sl} >= entry)"
        logger.warning(f"[{pair}] ❌ STAGE 2 - VALIDATION FAILED: {reason}")
        await log_signal_event(
            pair=pair,
            event_type="rejected",
            signal=signal_type,
            confidence=confidence,
            reason=reason,
        )
        return

    if signal_type == "SELL" and (tps[0] >= entry or sl <= entry):
        reason = f"SELL geometry invalid (TP1={tps[0]} >= entry={entry} or SL={sl} <= entry)"
        logger.warning(f"[{pair}] ❌ STAGE 2 - VALIDATION FAILED: {reason}")
        await log_signal_event(
            pair=pair,
            event_type="rejected",
            signal=signal_type,
            confidence=confidence,
            reason=reason,
        )
        return

    # Full signal validation pipeline
    signal_data_for_validation = {
        "pair": pair,
        "signal": signal_type,
        "confidence": confidence,
        "entry": entry,
        "tp_levels": tps,
        "sl": sl,
        "analysis": analysis,
    }
    validation_result = await _signal_validator.validate(signal_data_for_validation)

    if not validation_result["valid"]:
        logger.warning(
            f"[{pair}] ❌ STAGE 2 - VALIDATION FAILED: {validation_result['reason']} "
            f"(checks_failed={validation_result['checks_failed']})"
        )
        await log_signal_event(
            pair=pair,
            event_type="rejected",
            signal=signal_type,
            confidence=confidence,
            reason=validation_result["reason"],
            metadata={
                "checks_failed": validation_result["checks_failed"],
                "checks_passed": validation_result["checks_passed"],
            },
        )
        await send_signal_rejection_alert(pair, validation_result)
        return

    logger.info(
        f"[{pair}] ✅ STAGE 2 - VALIDATION PASSED: {signal_type} signal "
        f"(confidence={confidence}%, entry={entry}, checks={len(validation_result['checks_passed'])})"
    )
    await log_signal_event(
        pair=pair,
        event_type="validated",
        signal=signal_type,
        confidence=confidence,
        reason=validation_result["reason"],
        metadata={"checks_passed": validation_result["checks_passed"]},
    )

    # ── STAGE 3: SEND TELEGRAM NOTIFICATION ──────────────────────────────────

    # Calculate risk/reward
    risk   = abs(entry - sl)
    reward = abs(tps[0] - entry)
    rr     = round(reward / risk, 2) if risk > 0 else 0.0

    # Gather risk/position context for the notification
    pos_summary = await _position_manager.get_positions_summary()
    risk_status = _risk_manager.get_risk_status()

    logger.info(
        f"[{pair}] ✅ STAGE 3 - SENDING TELEGRAM NOTIFICATION: "
        f"{signal_type} confidence={confidence}%"
    )

    tg_sent = await send_to_telegram(
        pair, signal_type, entry, tps, sl,
        round(confidence, 1), rr, analysis,
        regime=hybrid_ctx.get("regime", "UNKNOWN"),
        smc_score=hybrid_ctx.get("smc_score", 0),
        mtf_alignment=hybrid_ctx.get("mtf_alignment", 0),
        position_count=pos_summary.get("total_open", 0),
        exposure_pct=pos_summary.get("exposure_pct", 0.0),
        risk_status=risk_status,
    )

    if tg_sent:
        logger.info(f"[{pair}] ✅ STAGE 3 - TELEGRAM NOTIFICATION DELIVERED")
        await log_signal_event(
            pair=pair,
            event_type="sent",
            signal=signal_type,
            confidence=round(confidence, 1),
            reason="Signal delivered to Telegram",
            metadata={"entry": entry, "tps": tps, "sl": sl, "rr": rr},
        )
    else:
        logger.error(f"[{pair}] ❌ STAGE 3 - TELEGRAM NOTIFICATION FAILED")
        await log_signal_event(
            pair=pair,
            event_type="rejected",
            signal=signal_type,
            confidence=round(confidence, 1),
            reason="Telegram delivery failed after all retries",
            metadata={"entry": entry, "tps": tps, "sl": sl, "rr": rr},
        )
        # Signal was generated and validated, but Telegram failed.
        # Don't attempt position registration if notification failed.
        return

    # ── STAGE 4: ATTEMPT POSITION REGISTRATION ───────────────────────────────

    # Drawdown recovery size multiplier
    size_multiplier = 1.0
    try:
        dd_assessment = _drawdown_recovery.assess(
            current_balance=_risk_manager.current_equity
        )
        if dd_assessment.get("trading_halted"):
            halt_reason = dd_assessment.get("halt_reason", "DRAWDOWN_HALT")
            logger.warning(f"[{pair}] DrawdownRecovery halt: {halt_reason}")
            await close_all_positions(reason=halt_reason)
            return
        size_multiplier = dd_assessment.get("size_multiplier", 1.0)
    except Exception as exc:
        logger.warning(f"[{pair}] Drawdown recovery check error: {exc}")

    position_size = round(1.0 * size_multiplier, 4)  # base 1 unit × recovery multiplier

    logger.info(
        f"[{pair}] ✅ STAGE 4 - ATTEMPTING POSITION REGISTRATION: "
        f"{signal_type} {entry} (size={position_size})"
    )

    pos_result = await _position_manager.add_position(
        pair=pair,
        entry=entry,
        tp_levels=tps,
        sl=sl,
        size=position_size,
        confidence=confidence,
        signal_type=signal_type,
        analysis=analysis,
    )

    # ── STAGE 5: HANDLE POSITION REGISTRATION RESULT ─────────────────────────

    if pos_result.get("allowed", True):
        logger.info(
            f"[{pair}] ✅ STAGE 5 - POSITION REGISTERED: "
            f"position_id={pos_result.get('position_id')}"
        )
        await log_signal_event(
            pair=pair,
            event_type="position_registered",
            signal=signal_type,
            confidence=round(confidence, 1),
            reason="Position registered successfully",
            metadata={
                "position_id": pos_result.get("position_id"),
                "entry": entry,
                "size": position_size,
            },
        )

        # Store signal in MongoDB only when position is successfully registered
        db = get_db()
        if db is not None:
            try:
                doc = {
                    "pair":             pair,
                    "type":             signal_type,
                    "entry_price":      entry,
                    "current_price":    ind["price"],
                    "tp_levels":        tps,
                    "sl_price":         sl,
                    "confidence":       round(confidence, 1),
                    "analysis":         analysis,
                    "risk_reward":      rr,
                    "timeframe":        "4H",
                    "status":           "ACTIVE",
                    "indicators":       ind,
                    "regime":           hybrid_ctx.get("regime", "UNKNOWN"),
                    "smc_score":        hybrid_ctx.get("smc_score", 0),
                    "mtf_alignment":    hybrid_ctx.get("mtf_alignment", 0),
                    "pivot_zone":       hybrid_ctx.get("pivot_zone", "UNKNOWN"),
                    "position_id":      pos_result.get("position_id"),
                    "position_size":    position_size,
                    "size_multiplier":  size_multiplier,
                    "system_version":   "3.0.0",
                    "created_at":       datetime.now(timezone.utc),
                }
                result = await db.gold_signals.insert_one(doc)
                logger.info(f"[{pair}] Signal stored — id={result.inserted_id}")
            except Exception as exc:
                logger.error(f"[{pair}] MongoDB insert failed: {exc}")

    else:
        # Position was blocked, but signal was already sent to Telegram!
        block_reason = pos_result.get("reason", "Unknown reason")
        logger.warning(
            f"[{pair}] ⚠️ STAGE 5 - POSITION BLOCKED: {block_reason} "
            f"(but signal was already sent to Telegram)"
        )
        await log_signal_event(
            pair=pair,
            event_type="position_blocked",
            signal=signal_type,
            confidence=round(confidence, 1),
            reason=f"Position registration blocked: {block_reason}",
            metadata={"entry": entry, "size": position_size},
        )
        # Signal was sent, position was blocked — this is OK!
        # User knows about the signal, just can't trade it right now.
        return

    _signal_metrics.successful_signals += 1
    logger.info(
        f"[{pair}] ✅ SIGNAL COMPLETE: {signal_type} "
        f"(generated → validated → sent → registered)"
    )


# ---------------------------------------------------------------------------
# Scheduler — Signal Generation (cron-based, aligned to 4H candle closes)
# ---------------------------------------------------------------------------
async def run_signal_generation() -> None:
    """Generate signals only on NEW H4 candle confirmation.

    The scheduler fires via cron at exact 4H candle close times
    (03:00, 07:00, 11:00, 15:00, 19:00, 23:00 UTC + 5 seconds), but the
    full signal pipeline is only executed when a *new* 4H candle has closed
    since the last processed one.  This eliminates redundant signals, wasted
    API calls, and Telegram spam.

    Set ``CANDLE_TRACKING_ENABLED=false`` to revert to the legacy behaviour
    (signal on every cron tick regardless of candle state).
    """
    logger.info("[SIGNAL_GEN] Starting cron-triggered market scan")

    for pair in PAIRS:
        try:
            if CANDLE_TRACKING_ENABLED:
                # ── Fetch just the latest few candles to check the timestamp ──
                logger.info(f"[SIGNAL_GEN] [{pair}] Fetching latest 4H candle")
                df_check = await fetch_ohlcv(pair, interval="4h", outputsize=5)
                if df_check is None or len(df_check) < 1:
                    logger.warning(
                        f"[SIGNAL_GEN] [{pair}] Could not fetch candle data — skipping"
                    )
                    await asyncio.sleep(2)
                    continue

                # TwelveData returns candles newest-first before our reversal;
                # after fetch_ohlcv the df is oldest-first, so iloc[-1] is the
                # most-recently *closed* candle.
                raw_time = df_check.iloc[-1].get("time") or df_check.iloc[-1].get("datetime")
                if raw_time is None:
                    logger.warning(
                        f"[SIGNAL_GEN] [{pair}] Candle has no 'time' field — skipping"
                    )
                    await asyncio.sleep(2)
                    continue

                # Normalise to a timezone-aware datetime
                if isinstance(raw_time, str):
                    try:
                        current_candle_time = datetime.fromisoformat(raw_time)
                    except ValueError:
                        import dateutil.parser
                        current_candle_time = dateutil.parser.parse(raw_time)
                else:
                    current_candle_time = raw_time  # already a datetime

                if current_candle_time.tzinfo is None:
                    current_candle_time = current_candle_time.replace(tzinfo=timezone.utc)

                logger.info(
                    f"[SIGNAL_GEN] [{pair}] Current candle: {current_candle_time}"
                )

                last_time = await _candle_tracker.get_last_candle_time(pair)
                if last_time is not None:
                    logger.info(
                        f"[SIGNAL_GEN] [{pair}] Last processed: {last_time}"
                    )

                # ── Gate: skip if same candle ──────────────────────────────
                is_new = await _candle_tracker.is_new_candle(pair, current_candle_time)
                if not is_new:
                    logger.info(
                        f"[SIGNAL_GEN] [{pair}] Same 4H candle as last signal — skipping"
                    )
                    await asyncio.sleep(2)
                    continue

                logger.info(
                    f"[SIGNAL_GEN] [{pair}] NEW 4H candle detected — generating signal"
                )

            # ── Full signal pipeline ───────────────────────────────────────
            await generate_signal(pair)

            # ── Update tracker after successful pipeline run ───────────────
            if CANDLE_TRACKING_ENABLED:
                await _candle_tracker.update_candle_time(pair, current_candle_time)
                logger.info(f"[SIGNAL_GEN] [{pair}] Candle tracker updated")

        except Exception as exc:
            logger.error(
                f"[SIGNAL_GEN] [{pair}] Unhandled error: {exc}", exc_info=True
            )

        await asyncio.sleep(2)

    logger.info("[SIGNAL_GEN] Cron-triggered market scan complete")


# Keep legacy alias so any external callers / manual triggers still work
async def run_all_signals() -> None:
    """Legacy alias for run_signal_generation — kept for backward compatibility."""
    await run_signal_generation()


# ---------------------------------------------------------------------------
# Scheduler — Validation Cycle (every 5 min)
# ---------------------------------------------------------------------------
async def run_validation_cycle() -> None:
    """Run validation checks every 5 minutes."""
    logger.info("[VALIDATION] Starting 5-min validation cycle")

    try:
        validation = await _risk_manager.validate_state()

        for check, result in validation.items():
            if not result["valid"]:
                logger.error(f"[VALIDATION] {check} FAILED: {result['errors']}")
                # Attempt auto-recovery and send alert
                await _risk_manager.auto_recover_from_invalid_state()
                try:
                    bot = get_bot()
                    await bot.send_message(
                        chat_id=TELEGRAM_CHANNEL_ID,
                        text=f"🚨 Validation failed: {check} — {result['errors']}",
                    )
                except Exception as exc:
                    logger.warning(f"[VALIDATION] Telegram alert failed: {exc}")

            if result.get("warnings"):
                logger.warning(f"[VALIDATION] {check}: {result['warnings']}")

    except Exception as exc:
        logger.error(f"[VALIDATION] Cycle error: {exc}", exc_info=True)

    logger.info("[VALIDATION] 5-min validation cycle complete")


# ---------------------------------------------------------------------------
# Database Integrity Check
# ---------------------------------------------------------------------------
async def check_database_integrity() -> dict:
    """Check MongoDB data integrity."""
    db = get_db()
    if db is None:
        return {"valid": False, "error": "MongoDB not connected"}

    try:
        # Check positions collection
        positions = await db.positions.find({}).to_list(None)

        # Validate each position
        for pos in positions:
            if not pos.get("entry_price") or pos["entry_price"] <= 0:
                logger.error(f"[DB_INTEGRITY] Invalid position entry_price: {pos.get('_id')}")

        # Check signals collection
        signals = await db.gold_signals.find({}).to_list(None)

        return {
            "valid": True,
            "positions_count": len(positions),
            "signals_count": len(signals),
        }
    except Exception as exc:
        logger.error(f"[DB_INTEGRITY] Check failed: {exc}")
        return {"valid": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Scheduler — Position Monitoring (every 30 min)
# ---------------------------------------------------------------------------
async def run_position_monitoring() -> None:
    """Monitor all open positions every 30 minutes for SL/TP/reversal/risk."""
    logger.info("[POSITION_MON] Starting 30-min position monitoring cycle")
    try:
        summary = await _pos_monitor.run_cycle()
        logger.info(
            f"[POSITION_MON] Cycle complete — "
            f"checked={summary.get('checked', 0)} "
            f"closed={summary.get('closed', 0)} "
            f"errors={summary.get('errors', 0)}"
        )
    except Exception as exc:
        logger.error(f"[POSITION_MON] Unhandled error in monitoring cycle: {exc}", exc_info=True)


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
            await bot.initialize()  # ✅ Opens httpx session — must be called before use
            me = await bot.get_me()
            logger.info(f"✅ Telegram bot ready — @{me.username}")
        except Exception as exc:
            logger.error(f"❌ Telegram bot init failed: {exc}")

    # Hybrid system
    get_hybrid_system()

    # ── Inject DB + Telegram into risk/position modules ──────────────────────
    if _db is not None:
        _position_manager.set_db(_db)
        _calendar_filter.set_db(_db)
        _risk_manager.set_db(_db)
        _candle_tracker.set_db(_db)
        logger.info("✅ Risk/position modules connected to MongoDB")

    # Reset position manager on startup to clear phantom positions from previous
    # runs. set_db() must be called first so reset() can reach MongoDB.
    # Without this, stale open_positions consume the 10% exposure cap and block
    # every new signal immediately after restart.
    await _position_manager.reset()
    logger.info("✅ Position manager reset (phantom positions cleared)")

    # Reset candle tracker on startup to clear stale state from previous run.
    # set_db() must be called first so reset() can also clear MongoDB.
    # Without this, the tracker loads the old timestamp from MongoDB and blocks
    # the first signal after every restart (same candle → skip).
    await _candle_tracker.reset()
    logger.info("✅ Candle tracker reset (cache + MongoDB)")

    if TELEGRAM_BOT_TOKEN:
        try:
            _risk_manager.set_telegram(get_bot(), TELEGRAM_CHANNEL_ID)
            logger.info("✅ Risk manager Telegram alerts enabled")
        except Exception as exc:
            logger.warning(f"Risk manager Telegram setup failed: {exc}")

    # Initialise risk manager with account balance
    _risk_manager.set_account_balance(ACCOUNT_BALANCE)
    _drawdown_recovery.reset_all(ACCOUNT_BALANCE)

    # Pre-fetch economic calendar
    try:
        await _calendar_filter.fetch_calendar()
        logger.info("✅ Economic calendar pre-fetched")
    except Exception as exc:
        logger.warning(f"Economic calendar pre-fetch failed: {exc}")

    # ── Configure position monitor ────────────────────────────────────────────
    _pos_monitor.configure(
        position_manager=_position_manager,
        reversal_detector=_reversal_detector,
        risk_manager=_risk_manager,
        drawdown_recovery=_drawdown_recovery,
        fetch_ohlcv=fetch_ohlcv,
        close_position_fn=_position_manager.close_position,
        send_alert_fn=send_position_monitor_alert,
        pairs=PAIRS,
    )
    logger.info("✅ Position monitor configured")

    # ── Scheduler: cron-based jobs aligned to 4H candle closes ───────────────
    # Job 1 — Signal generation: cron at exact 4H candle close times (UTC)
    scheduler.add_job(
        run_signal_generation,
        "cron",
        hour="3,7,11,15,19,23",  # Exact 4H candle close times
        minute=0,
        second=5,  # 5 seconds after candle closes
        id="signal_generation_cron",
        max_instances=1,
        coalesce=True,
    )
    # Job 2 — Position monitoring: 30 min after each candle close
    scheduler.add_job(
        run_position_monitoring,
        "cron",
        hour="3,7,11,15,19,23",
        minute=30,  # 30 min after candle closes
        second=0,
        id="position_monitoring_cron",
        max_instances=1,
        coalesce=True,
    )
    # Job 3 — Validation cycle: every 5 minutes
    scheduler.add_job(
        run_validation_cycle,
        "interval",
        minutes=5,
        id="validation_cycle_5min",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        f"✅ Scheduler started — pairs={list(PAIRS.keys())} | "
        f"signal_generation=cron(03,07,11,15,19,23:00:05 UTC) | "
        f"position_monitoring=cron(03,07,11,15,19,23:30:00 UTC) | "
        f"validation=5min"
    )

    # Validate system state on startup (risk manager checks)
    logger.info("🔍 Running risk manager startup validation...")
    try:
        startup_validation = await _risk_manager.validate_state()
        if not all(v["valid"] for v in startup_validation.values()):
            logger.error("❌ Risk manager startup validation FAILED!")
            for check, result in startup_validation.items():
                if not result["valid"]:
                    logger.error(f"  {check}: {result['errors']}")
            # Attempt auto-recovery rather than hard-stopping
            await _risk_manager.auto_recover_from_invalid_state()
        else:
            logger.info("✅ Risk manager startup validation passed")
    except Exception as exc:
        logger.warning(f"Risk manager startup validation error (non-fatal): {exc}")

    # Run comprehensive startup validation (edge-case checks for Bugs #188/#191/#192)
    try:
        from startup_validation import run_startup_validation
        validation_results = await run_startup_validation()
        if not validation_results["all_passed"]:
            logger.error(
                "❌ Startup validation failed — system may not work correctly. "
                "Check logs above for details."
            )
            # Non-fatal: log and continue rather than preventing startup
    except Exception as exc:
        logger.warning(f"Startup validation error (non-fatal): {exc}")

    # Run an immediate signal generation cycle on startup
    asyncio.create_task(run_signal_generation())

    yield

    scheduler.shutdown(wait=False)
    if _mongo_client:
        _mongo_client.close()
    if _bot is not None:
        try:
            await _bot.shutdown()  # ✅ Closes httpx session — prevents unclosed client warnings
            logger.info("✅ Telegram bot shut down cleanly")
        except Exception as exc:
            logger.warning(f"Telegram bot shutdown error (non-fatal): {exc}")
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

    # Build enriched job list with schedule labels
    _schedule_labels = {
        "signal_generation_cron": "cron(03,07,11,15,19,23:00:05 UTC)",
        "position_monitoring_cron": "cron(03,07,11,15,19,23:30:00 UTC)",
        "validation_cycle_5min": "interval(5 minutes)",
    }
    jobs = [
        {
            "id": j.id,
            "next_run": str(j.next_run_time),
            "schedule": _schedule_labels.get(j.id, "unknown"),
        }
        for j in scheduler.get_jobs()
    ]

    candle_tracker_state = _candle_tracker.get_state()

    return {
        "status":            "ok",
        "service":           "gold_signals_v3",
        "version":           "3.0.0",
        "pairs":             list(PAIRS.keys()),
        "telegram_channel":  TELEGRAM_CHANNEL_ID,
        "scheduler_running": scheduler.running,
        "scheduler_jobs":    jobs,
        "signal_generation_schedule": "cron(03,07,11,15,19,23:00:05 UTC)",
        "position_monitoring_schedule": "cron(03,07,11,15,19,23:30:00 UTC)",
        "mongo_connected":   mongo_ok,
        "system_components": system_status.get("total_components", 0),
        "candle_tracker_state": {
            pair: str(timestamp) for pair, timestamp in candle_tracker_state.items()
        },
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
# Endpoint 12: Open Positions
# ---------------------------------------------------------------------------
@app.get("/api/positions")
async def get_positions(pair: Optional[str] = None):
    """List all open positions, optionally filtered by pair."""
    positions = await _position_manager.get_open_positions(pair=pair.upper() if pair else None)
    summary = await _position_manager.get_positions_summary()
    # Strip MongoDB _id for JSON serialisation
    clean = []
    for pos in positions:
        p = {k: v for k, v in pos.items() if k != "_id"}
        if "opened_at" in p and hasattr(p["opened_at"], "isoformat"):
            p["opened_at"] = p["opened_at"].isoformat()
        clean.append(p)
    return {
        "positions": clean,
        "summary": summary,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 13: Risk Status
# ---------------------------------------------------------------------------
@app.get("/api/risk-status")
async def get_risk_status():
    """Return current daily P&L, drawdown, exposure, and risk level."""
    risk = _risk_manager.get_risk_status()
    pos_summary = await _position_manager.get_positions_summary()
    calendar_status = await _calendar_filter.get_blackout_status("XAUUSD")
    dd_assessment = _drawdown_recovery.assess(
        current_balance=_risk_manager.current_equity
    )
    return {
        "risk": risk,
        "positions": pos_summary,
        "calendar": {
            "safe_to_trade": calendar_status.get("safe_to_trade", True),
            "reason": calendar_status.get("reason", "CLEAR"),
            "next_event": calendar_status.get("next_event"),
        },
        "drawdown_recovery": {
            "size_multiplier": dd_assessment.get("size_multiplier", 1.0),
            "recovery_level": dd_assessment.get("recovery_level", "FULL_CAPACITY"),
            "trading_halted": dd_assessment.get("trading_halted", False),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 14: Close All Positions (manual)
# ---------------------------------------------------------------------------
@app.post("/api/close-all")
async def manual_close_all(reason: str = "MANUAL"):
    """Manually close all open positions."""
    result = await close_all_positions(reason=f"MANUAL: {reason}")
    return {
        "success": result.get("success", False),
        "closed": result.get("closed", 0),
        "total_pnl": result.get("total_pnl", 0.0),
        "reason": result.get("reason"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 15: Validation Health Check
# ---------------------------------------------------------------------------
@app.get("/api/health/validation")
async def health_validation():
    """Comprehensive validation health check."""
    try:
        validation = await _risk_manager.validate_state()
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    all_valid = all(v["valid"] for v in validation.values())

    return {
        "status": "healthy" if all_valid else "unhealthy",
        "validation": validation,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 16: Database Integrity Check
# ---------------------------------------------------------------------------
@app.get("/api/health/database")
async def health_database():
    """Check MongoDB data integrity."""
    result = await check_database_integrity()
    return {
        "status": "healthy" if result.get("valid") else "unhealthy",
        "integrity": result,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 17: Signal Generation Metrics
# ---------------------------------------------------------------------------
@app.get("/api/metrics/signals")
async def get_signal_metrics():
    """Get signal generation metrics (success rate, retries, API errors)."""
    return await _signal_metrics.log_metrics()


# ---------------------------------------------------------------------------
# Endpoint 18: Signal Health Check
# ---------------------------------------------------------------------------
@app.get("/api/health/signals")
async def health_signals():
    """
    Health check for the signal generation system.

    Evaluates success rate and flags anomalies:
      - CRITICAL: cycles ran but 0% success rate (no signals in 24 h)
      - WARNING:  success rate below 50% (high failure rate)
      - HEALTHY:  everything nominal
    """
    metrics = await _signal_metrics.log_metrics()

    # Parse success_rate string (e.g. "87.5%") to float
    try:
        success_rate = float(metrics["success_rate"].rstrip("%"))
    except (ValueError, AttributeError):
        success_rate = 0.0

    health_status = "HEALTHY"
    alerts = []

    # Alert: 0 signals generated despite cycles running
    if metrics["total_cycles"] > 0 and success_rate == 0.0:
        health_status = "CRITICAL"
        alerts.append("No signals generated in 24 hours")

    # Alert: high failure rate (but not zero — already caught above)
    if 0.0 < success_rate < 50.0:
        health_status = "WARNING"
        alerts.append(f"Low success rate: {success_rate:.1f}%")

    return {
        "status": health_status,
        "metrics": metrics,
        "alerts": alerts,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 19: Admin — Reset Candle Tracker
# ---------------------------------------------------------------------------
@app.post("/api/admin/candle-tracker/reset")
async def reset_candle_tracker(pair: Optional[str] = None):
    """
    Manually reset candle tracker state.

    Use this if signals are being blocked by a stale candle timestamp
    (e.g. after an unexpected restart mid-candle).

    Query params:
      - pair: Optional pair name (e.g. "XAUUSD").
              If omitted, resets ALL pairs.

    Examples:
      POST /api/admin/candle-tracker/reset
      POST /api/admin/candle-tracker/reset?pair=XAUUSD
    """
    if pair:
        pair = pair.upper()
        _candle_tracker.reset_pair(pair)
        logger.info(f"[admin] Candle tracker reset for {pair} via API")
        return {
            "status": "success",
            "message": f"Candle tracker reset for {pair}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    else:
        await _candle_tracker.reset()
        logger.info("[admin] Candle tracker reset for all pairs via API")
        return {
            "status": "success",
            "message": "Candle tracker reset for all pairs",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8002)))

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
    position_count: int = 0,
    exposure_pct: float = 0.0,
    risk_status: Optional[dict] = None,
) -> None:
    """Send signal to Telegram with v3.0 context + risk/position data."""
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

        await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=copier_msg)
        await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=info_msg, parse_mode="HTML")
        logger.info(f"[{pair}] Signal sent to Telegram channel {TELEGRAM_CHANNEL_ID}")

    except Exception as exc:
        logger.error(f"[{pair}] Telegram delivery failed: {exc}")


async def send_reversal_alert(pair: str, reason: str, closed_count: int, total_pnl: float) -> None:
    """Send an immediate reversal / close-all alert to Telegram."""
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
    except Exception as exc:
        logger.error(f"Reversal alert failed: {exc}")


async def send_position_monitor_alert(msg: str) -> None:
    """Send a position-monitor close alert to Telegram (HTML parse mode)."""
    try:
        bot = get_bot()
        await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=msg, parse_mode="HTML")
    except Exception as exc:
        logger.error(f"[POSITION_MON] Telegram alert failed: {exc}")


# ---------------------------------------------------------------------------
# Close All Positions
# ---------------------------------------------------------------------------
async def close_all_positions(reason: str = "SYSTEM") -> dict:
    """
    Close every open position across all pairs.
    Called on reversal detection, daily loss limit, or drawdown limit.
    Sends a Telegram notification for each pair closed.
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

    logger.warning(
        f"close_all_positions: reason={reason} closed={closed} pnl={total_pnl:.2f}"
    )

    # Telegram notification
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
    except Exception as exc:
        logger.error(f"close_all_positions Telegram alert failed: {exc}")

    return result


# ---------------------------------------------------------------------------
# Core Signal Generation
# ---------------------------------------------------------------------------
async def generate_signal(pair: str) -> None:
    """Full v3.0 pipeline: fetch → risk checks → hybrid analysis → GPT → validate → store → send."""
    cfg = PAIRS[pair]
    logger.info(f"[{pair}] Starting v3.0 signal generation")

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

    # 1. Price data
    df = await fetch_ohlcv(pair, interval="4h", outputsize=100)
    if df is None or len(df) < 52:
        logger.warning(f"[{pair}] Insufficient candles, skipping")
        return

    # 2. Indicators
    ind = compute_indicators(df, cfg["decimals"])
    if ind is None:
        return

    # ── REVERSAL DETECTION ───────────────────────────────────────────────────

    # Guard 3: Reversal detection — check BEFORE generating new signal
    try:
        # Use hybrid signal as the "current" regime if available; else use trend
        current_regime = ind.get("trend", "NEUTRAL")
        reversal = await _reversal_detector.detect_reversal(pair, df, current_regime)
        if reversal.get("reversal_detected"):
            rev_reason = reversal.get("reason", "REVERSAL")
            logger.warning(f"[{pair}] {rev_reason} — closing all positions")
            close_result = await close_all_positions(reason=f"REVERSAL: {rev_reason}")
            await send_reversal_alert(
                pair,
                rev_reason,
                close_result.get("closed", 0),
                close_result.get("total_pnl", 0.0),
            )
            # Don't open new positions this cycle — let the market settle
            return
    except Exception as exc:
        logger.warning(f"[{pair}] Reversal detection error (fail-open): {exc}")

    # Guard 4: Position count hard cap
    try:
        pos_count = await _position_manager.get_position_count(pair)
        if pos_count >= 5:
            logger.info(
                f"[{pair}] Position cap reached ({pos_count}/5) — skipping new signal"
            )
            return
    except Exception as exc:
        logger.warning(f"[{pair}] Position count check error: {exc}")

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
                "hybrid_confidence": hybrid_result.get("confidence", 0),
            }
            logger.info(
                f"[{pair}] Hybrid: signal={hybrid_ctx['hybrid_signal']} "
                f"regime={hybrid_ctx['regime']} smc={hybrid_ctx['smc_score']}/10 "
                f"mtf={hybrid_ctx['mtf_alignment']:.0f}%"
            )
        except Exception as exc:
            logger.error(f"[{pair}] Hybrid system error: {exc}")

    # ── GPT SIGNAL ───────────────────────────────────────────────────────────

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

    # ── LEVELS & GEOMETRY ────────────────────────────────────────────────────

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

    # ── DRAWDOWN RECOVERY SIZE MULTIPLIER ────────────────────────────────────

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

    # ── POSITION MANAGEMENT ──────────────────────────────────────────────────

    # 8. Register position with position manager
    position_size = round(1.0 * size_multiplier, 4)  # base 1 unit × recovery multiplier
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
    if not pos_result.get("allowed", True):
        logger.warning(f"[{pair}] Position rejected: {pos_result.get('reason')}")
        return

    # ── STORE IN MONGODB ─────────────────────────────────────────────────────

    # 9. Store signal in MongoDB
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

    # ── TELEGRAM ─────────────────────────────────────────────────────────────

    # 10. Gather risk context for alert
    pos_summary = await _position_manager.get_positions_summary()
    risk_status = _risk_manager.get_risk_status()

    # 11. Send to Telegram
    await send_to_telegram(
        pair, signal_type, entry, tps, sl,
        round(confidence, 1), rr, analysis,
        regime=hybrid_ctx.get("regime", "UNKNOWN"),
        smc_score=hybrid_ctx.get("smc_score", 0),
        mtf_alignment=hybrid_ctx.get("mtf_alignment", 0),
        position_count=pos_summary.get("total_open", 0),
        exposure_pct=pos_summary.get("exposure_pct", 0.0),
        risk_status=risk_status,
    )

    logger.info(
        f"[{pair}] ✅ {signal_type} @ {entry} | "
        f"TP: {tps} | SL: {sl} | R:R 1:{rr} | Conf: {confidence}% | "
        f"Positions: {pos_summary.get('total_open', 0)}/5 | "
        f"Risk: {risk_status.get('risk_level', 'GREEN')}"
    )


# ---------------------------------------------------------------------------
# Scheduler — Signal Generation (every 30 min, NEW 4H candle gate)
# ---------------------------------------------------------------------------
async def run_signal_generation() -> None:
    """Generate signals only on NEW H4 candle confirmation.

    The scheduler fires every 30 minutes, but the full signal pipeline is
    only executed when a *new* 4H candle has closed since the last processed
    one.  This eliminates redundant signals, wasted API calls, and Telegram
    spam while keeping the 30-min scan cadence for responsiveness.

    Set ``CANDLE_TRACKING_ENABLED=false`` to revert to the legacy behaviour
    (signal on every 30-min tick regardless of candle state).
    """
    logger.info("[SIGNAL_GEN] Starting 30-min market scan")

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

    logger.info("[SIGNAL_GEN] 30-min market scan complete")


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

    # ── Scheduler: two separate jobs ─────────────────────────────────────────
    # Job 1 — Signal generation: every 30 minutes (high-quality signals only)
    scheduler.add_job(
        run_signal_generation,
        "interval",
        minutes=SIGNAL_GENERATION_INTERVAL_MINUTES,
        id="signal_generation_30min",
        max_instances=1,
        coalesce=True,
    )
    # Job 2 — Position monitoring: every 30 minutes (aligned with signal generation)
    scheduler.add_job(
        run_position_monitoring,
        "interval",
        minutes=POSITION_MONITORING_INTERVAL_MINUTES,
        id="position_monitoring_30min",
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
        f"signal_generation={SIGNAL_GENERATION_INTERVAL_MINUTES}min | "
        f"position_monitoring={POSITION_MONITORING_INTERVAL_MINUTES}min | "
        f"validation=5min"
    )

    # Validate system state on startup
    logger.info("🔍 Running startup validation checks...")
    try:
        startup_validation = await _risk_manager.validate_state()
        if not all(v["valid"] for v in startup_validation.values()):
            logger.error("❌ Startup validation FAILED!")
            for check, result in startup_validation.items():
                if not result["valid"]:
                    logger.error(f"  {check}: {result['errors']}")
            # Attempt auto-recovery rather than hard-stopping
            await _risk_manager.auto_recover_from_invalid_state()
        else:
            logger.info("✅ All startup validation checks passed")
    except Exception as exc:
        logger.warning(f"Startup validation error (non-fatal): {exc}")

    # Run an immediate signal generation cycle on startup
    asyncio.create_task(run_signal_generation())

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

    # Build enriched job list with interval labels
    _interval_labels = {
        "signal_generation_30min": f"{SIGNAL_GENERATION_INTERVAL_MINUTES} minutes",
        "position_monitoring_30min": f"{POSITION_MONITORING_INTERVAL_MINUTES} minutes",
    }
    jobs = [
        {
            "id": j.id,
            "next_run": str(j.next_run_time),
            "interval": _interval_labels.get(j.id, "unknown"),
        }
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
        "signal_generation_interval_minutes":  SIGNAL_GENERATION_INTERVAL_MINUTES,
        "position_monitoring_interval_minutes": POSITION_MONITORING_INTERVAL_MINUTES,
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
# Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8002)))

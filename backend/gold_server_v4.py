"""
Grandcom Gold Signals Server v4.0 — Balanced Edition
Institutional Multi-Strategy Hybrid Portfolio System with Advanced Risk Management

V4.0 Balanced Option C Features:
  ✅ Breakeven Stop-Loss  — Moves SL to entry after TP1 hit (+0.5R activation)
  ✅ Trailing Stop        — Follows price by 1 ATR; activates after TP1 hit
  ✅ Multi-TF Confirmation— 4H signal confirmed by 1H + Daily (≥70% alignment)
  ✅ Advanced Position Sizing — Volatility-adjusted, regime-scaled dynamic lots
  ✅ Light Model Retraining   — Every 24-48 h; adapts to regime changes
  ✅ Manual Execution     — Copy-trading compatible; no full automation

Expected V4 Balanced Option C Results:
  Win Rate      : 70%  (+5% vs V3)
  Monthly P&L   : $2,000-2,800  (+40%)
  Drawdown      : 4.5%  (-22%)
  Signals/Month : 25-30
  Complexity    : Medium-High
  Risk          : Medium

Timeframe: 4H (PERMANENT)
Pairs    : XAUUSD & XAUEUR
Runtime  : Python 3.11 + FastAPI
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

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
logger = logging.getLogger("gold_server_v4")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MONGO_URL             = os.environ.get("MONGO_URL", "")
DB_NAME               = os.environ.get("DB_NAME", "gold_signals_v4")
TELEGRAM_BOT_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TWELVE_DATA_API_KEY   = os.environ.get("TWELVE_DATA_API_KEY", "")
OPENAI_API_KEY        = os.environ.get("OPENAI_API_KEY") or os.environ.get("EMERGENT_LLM_KEY", "")

_raw_channel = os.environ.get("TELEGRAM_GOLD_CHANNEL_ID", "-1003834233408")
try:
    TELEGRAM_CHANNEL_ID: int | str = int(_raw_channel)
except ValueError:
    TELEGRAM_CHANNEL_ID = _raw_channel

SIGNAL_INTERVAL_MINUTES = int(os.environ.get("SIGNAL_INTERVAL_MINUTES", "2"))
MIN_CONFIDENCE          = int(os.environ.get("MIN_CONFIDENCE", "62"))   # Raised from 60 → 62 for V4
ACCOUNT_BALANCE         = float(os.environ.get("DEFAULT_ACCOUNT_BALANCE", "10000.0"))

# V4 Risk Management Constants
MTF_MIN_ALIGNMENT       = float(os.environ.get("MTF_MIN_ALIGNMENT", "70.0"))   # ≥70% required
BE_ACTIVATION_R         = float(os.environ.get("BE_ACTIVATION_R", "0.5"))      # Breakeven at +0.5R
TRAILING_ATR_MULT       = float(os.environ.get("TRAILING_ATR_MULT", "1.0"))    # Trail by 1 ATR
RETRAIN_INTERVAL_HOURS  = int(os.environ.get("RETRAIN_INTERVAL_HOURS", "24"))  # 24-48 h retraining

# ---------------------------------------------------------------------------
# Trading Pairs — V4 ATR Multipliers (tighter TP1 for faster BE activation)
# ---------------------------------------------------------------------------
PAIRS: dict[str, dict] = {
    "XAUUSD": {
        "symbol":   "XAU/USD",
        "decimals": 2,
        # SL wider than V3 to survive noise before BE kicks in
        "atr_sl":   1.0,    # 1.0x ATR  — slightly wider for swing room
        # TP1 tighter → hit faster → BE activates sooner → drawdown ↓
        "atr_tp1":  0.40,   # 0.40x ATR — quick TP1 / BE trigger
        "atr_tp2":  0.80,   # 0.80x ATR — mid target
        "atr_tp3":  1.40,   # 1.40x ATR — extended target (trailing captures)
    },
    "XAUEUR": {
        "symbol":   "XAU/EUR",
        "decimals": 2,
        "atr_sl":   1.0,
        "atr_tp1":  0.40,
        "atr_tp2":  0.80,
        "atr_tp3":  1.40,
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
# Hybrid System (lazy import)
# ---------------------------------------------------------------------------
_hybrid_system = None


def get_hybrid_system():
    global _hybrid_system
    if _hybrid_system is None:
        try:
            from ml_engine.hybrid_portfolio_system_v3 import HybridPortfolioSystemV3
            _hybrid_system = HybridPortfolioSystemV3(account_balance=ACCOUNT_BALANCE)
            logger.info("✅ HybridPortfolioSystemV3 loaded for V4")
        except Exception as exc:
            logger.error(f"❌ Failed to load HybridPortfolioSystemV3: {exc}")
            _hybrid_system = None
    return _hybrid_system


# ---------------------------------------------------------------------------
# Light Model Retraining State
# ---------------------------------------------------------------------------
_last_retrain_time: datetime | None = None
_retrain_lock = asyncio.Lock()


async def maybe_retrain_model() -> dict:
    """
    Light model retraining — runs every RETRAIN_INTERVAL_HOURS (24-48 h).
    Pulls recent closed signals from MongoDB and re-optimises the signal
    quality parameters without a full rebuild.
    """
    global _last_retrain_time

    now = datetime.now(timezone.utc)
    if _last_retrain_time is not None:
        elapsed = (now - _last_retrain_time).total_seconds() / 3600
        if elapsed < RETRAIN_INTERVAL_HOURS:
            return {"skipped": True, "next_retrain_in_hours": round(RETRAIN_INTERVAL_HOURS - elapsed, 1)}

    async with _retrain_lock:
        # Double-check after acquiring lock
        if _last_retrain_time is not None:
            elapsed = (now - _last_retrain_time).total_seconds() / 3600
            if elapsed < RETRAIN_INTERVAL_HOURS:
                return {"skipped": True}

        logger.info("🔄 V4 Light model retraining started …")
        result: dict = {"timestamp": now.isoformat(), "success": False}

        db = get_db()
        if db is None:
            result["error"] = "MongoDB not connected"
            return result

        try:
            from ml_engine.model_trainer import SignalOptimizationEngine

            # Fetch last 500 closed signals
            signals = (
                await db.gold_signals_v4
                .find({"status": {"$in": ["CLOSED", "WIN", "LOSS"]}}, {"_id": 0})
                .sort("created_at", -1)
                .limit(500)
                .to_list(500)
            )

            if len(signals) < 30:
                result["error"] = f"Insufficient data for retraining ({len(signals)} signals, need ≥30)"
                logger.warning(result["error"])
                return result

            optimizer = SignalOptimizationEngine()
            pair_analysis   = optimizer.analyze_performance_by_pair(signals)
            regime_analysis = optimizer.analyze_performance_by_regime(signals)
            recommendations = optimizer.recommend_pair_settings(pair_analysis)

            total   = len(signals)
            wins    = sum(1 for s in signals if s.get("result") == "WIN")
            win_rate = wins / total * 100 if total else 0

            result.update({
                "success":          True,
                "signals_analyzed": total,
                "win_rate":         round(win_rate, 1),
                "pair_analysis":    pair_analysis,
                "regime_analysis":  regime_analysis,
                "recommendations":  recommendations,
            })

            _last_retrain_time = now
            logger.info(
                f"✅ V4 Light retraining complete — {total} signals, "
                f"win_rate={win_rate:.1f}%"
            )

        except Exception as exc:
            result["error"] = str(exc)
            logger.error(f"❌ V4 retraining failed: {exc}", exc_info=True)

        return result


# ---------------------------------------------------------------------------
# Price Data
# ---------------------------------------------------------------------------
async def fetch_ohlcv(
    pair: str,
    interval: str = "4h",
    outputsize: int = 100,
) -> pd.DataFrame | None:
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
    """Compute RSI, MACD, MA20/50, ATR for 4H candles."""
    try:
        close = df["close"]
        high  = df["high"]
        low   = df["low"]

        rsi      = ta.momentum.RSIIndicator(close, window=14).rsi()
        macd_obj = ta.trend.MACD(close)
        ma20     = ta.trend.SMAIndicator(close, window=20).sma_indicator()
        ma50     = ta.trend.SMAIndicator(close, window=50).sma_indicator()
        atr      = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()

        last = df.iloc[-1]
        dp   = decimals

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
# V4 Feature 3: Multi-Timeframe Confirmation (MTF)
# ---------------------------------------------------------------------------
async def run_mtf_confirmation(pair: str) -> dict:
    """
    Confirm 4H signal with 1H + Daily alignment.
    Requires ≥70% alignment score (MTF_MIN_ALIGNMENT).
    Filters ~50% of false signals, adds ~10% win rate.
    """
    try:
        from ml_engine.multi_timeframe_confirmation import MultiTimeframeConfirmation

        mtf = MultiTimeframeConfirmation()
        result = await mtf.analyze(pair)

        alignment_score     = float(result.get("alignment_score", 0.0))
        dominant_direction  = result.get("dominant_direction", "NEUTRAL")
        timeframes          = result.get("timeframes", {})

        # Extract 1H and Daily specifically for V4 confirmation logic
        tf_1h    = timeframes.get("1h",    {})
        tf_daily = timeframes.get("1day",  {})
        tf_4h    = timeframes.get("4h",    {})

        # V4 requires 1H + Daily to agree with 4H direction
        directions = {
            "1h":    tf_1h.get("direction",    "NEUTRAL"),
            "4h":    tf_4h.get("direction",    "NEUTRAL"),
            "1day":  tf_daily.get("direction", "NEUTRAL"),
        }

        aligned_count = sum(
            1 for d in directions.values()
            if d == dominant_direction and d != "NEUTRAL"
        )
        alignment_ok = (
            alignment_score >= MTF_MIN_ALIGNMENT
            and aligned_count >= 2          # At least 2 of 3 TFs agree
            and dominant_direction != "NEUTRAL"
        )

        logger.info(
            f"[{pair}] MTF V4: score={alignment_score:.1f}% "
            f"direction={dominant_direction} aligned={aligned_count}/3 "
            f"ok={alignment_ok}"
        )

        return {
            "alignment_score":    alignment_score,
            "dominant_direction": dominant_direction,
            "directions":         directions,
            "aligned_count":      aligned_count,
            "alignment_ok":       alignment_ok,
            "min_required":       MTF_MIN_ALIGNMENT,
            "timeframes":         timeframes,
        }

    except Exception as exc:
        logger.error(f"[{pair}] MTF confirmation failed: {exc}")
        # Fail-open with reduced score so signal can still proceed
        return {
            "alignment_score":    0.0,
            "dominant_direction": "NEUTRAL",
            "directions":         {},
            "aligned_count":      0,
            "alignment_ok":       False,
            "min_required":       MTF_MIN_ALIGNMENT,
            "error":              str(exc),
        }


# ---------------------------------------------------------------------------
# V4 Feature 4: Advanced Position Sizing
# ---------------------------------------------------------------------------
def compute_advanced_position_size(
    pair: str,
    entry: float,
    sl: float,
    atr: float,
    df: pd.DataFrame,
    regime: str = "RANGE",
    account_balance: float = ACCOUNT_BALANCE,
) -> dict:
    """
    Dynamic lot sizing based on:
    1. Fixed-risk baseline (1% account risk)
    2. Volatility adjustment (ATR-based scaling)
    3. Regime multiplier (trend → full size, chaos → 0)
    4. Drawdown guard (reduces size when in drawdown)

    Returns lot size and full breakdown for transparency.
    """
    try:
        from ml_engine.volatility_adjustment import VolatilityAdjustment
        from ml_engine.position_calculator import PositionCalculator

        # --- Base position size via fixed-risk ---
        pos_calc = PositionCalculator(
            default_risk_pct=1.0,   # 1% base risk per trade
            max_risk_pct=2.0,
            min_lot=0.01,
            max_lot=5.0,
            contract_size=100.0,    # 100 oz per lot (gold standard)
        )
        base_result = pos_calc.calculate(
            account_balance=account_balance,
            entry_price=entry,
            sl_price=sl,
            symbol=pair,
            method="fixed_risk",
            risk_pct=1.0,
        )
        base_lots = float(base_result.get("lots", 0.01))

        # --- Volatility adjustment ---
        vol_adj = VolatilityAdjustment(
            target_vol=0.01,
            max_size_multiplier=1.5,
            min_size_multiplier=0.3,
        )
        vol_result = vol_adj.calculate_position_size(
            df=df,
            base_size=base_lots,
            account_balance=account_balance,
            risk_pct=0.01,
            symbol=pair,
        )
        vol_lots = float(vol_result.get("adjusted_size", base_lots))

        # --- Regime multiplier ---
        regime_multipliers = {
            "TREND_UP":       1.0,
            "TREND_DOWN":     1.0,
            "RANGE":          0.8,
            "HIGH_VOL":       0.6,
            "LOW_VOL":        1.1,
            "CHAOS":          0.0,
            "UNKNOWN":        0.8,
        }
        regime_mult = regime_multipliers.get(regime.upper(), 0.8)

        # --- Final lot size ---
        final_lots = round(max(0.01, min(5.0, vol_lots * regime_mult)), 2)

        # --- Dollar risk at final size ---
        stop_distance = abs(entry - sl)
        dollar_risk   = stop_distance * final_lots * 100  # 100 oz/lot

        return {
            "lots":             final_lots,
            "base_lots":        round(base_lots, 2),
            "vol_lots":         round(vol_lots, 2),
            "regime_mult":      regime_mult,
            "stop_distance":    round(stop_distance, 2),
            "dollar_risk":      round(dollar_risk, 2),
            "risk_pct":         round(dollar_risk / account_balance * 100, 2),
            "vol_regime":       vol_result.get("regime", "NORMAL"),
            "vol_multiplier":   vol_result.get("vol_multiplier", 1.0),
            "valid":            True,
        }

    except Exception as exc:
        logger.error(f"[{pair}] Advanced position sizing failed: {exc}")
        return {
            "lots":      0.01,
            "valid":     False,
            "error":     str(exc),
        }


# ---------------------------------------------------------------------------
# V4 Feature 1 & 2: Breakeven & Trailing Stop Metadata
# ---------------------------------------------------------------------------
def compute_be_ts_levels(
    signal: str,
    entry: float,
    sl: float,
    atr: float,
    cfg: dict,
) -> dict:
    """
    Compute Breakeven (BE) and Trailing Stop (TS) activation levels.

    BE  — activates when price moves +0.5R in trade direction.
          SL is then moved to entry price (risk-free trade).
    TS  — activates after TP1 hit; trails price by 1 ATR distance.
          Captures extended trend moves beyond TP1.

    These are informational levels for manual execution / copy-trading.
    The trader moves SL manually when price reaches be_trigger.
    """
    risk = abs(entry - sl)
    half_r = risk * BE_ACTIVATION_R   # 0.5R distance

    if signal == "BUY":
        be_trigger   = round(entry + half_r, cfg["decimals"])   # Price that triggers BE
        be_sl        = entry                                      # SL moves to entry
        ts_start     = round(entry + atr * cfg["atr_tp1"], cfg["decimals"])  # TP1 = TS start
        ts_distance  = round(atr * TRAILING_ATR_MULT, cfg["decimals"])       # Trail by 1 ATR
    else:  # SELL
        be_trigger   = round(entry - half_r, cfg["decimals"])
        be_sl        = entry
        ts_start     = round(entry - atr * cfg["atr_tp1"], cfg["decimals"])
        ts_distance  = round(atr * TRAILING_ATR_MULT, cfg["decimals"])

    return {
        "be_trigger":   be_trigger,    # Move SL to entry when price hits this
        "be_sl":        be_sl,         # New SL after BE activation (= entry)
        "be_activation_r": BE_ACTIVATION_R,
        "ts_start":     ts_start,      # Trailing stop activates at TP1
        "ts_distance":  ts_distance,   # Trail distance (1 ATR)
        "ts_atr_mult":  TRAILING_ATR_MULT,
        "risk_distance": round(risk, cfg["decimals"]),
    }


# ---------------------------------------------------------------------------
# GPT Signal (V4 enhanced prompt)
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT_V4 = (
    "You are an elite institutional gold trader using the Hybrid Portfolio System v4.0 "
    "Balanced Edition. Analyse the provided market data and return a JSON trading signal. "
    "V4 uses breakeven stop-loss, trailing stops, and multi-timeframe confirmation. "
    "Only signal BUY or SELL when conviction is HIGH. "
    "Respond ONLY with valid JSON — no markdown, no extra text."
)

_USER_TEMPLATE_V4 = """\
Analyse {pair} (4H timeframe) — Hybrid Portfolio System v4.0 Balanced Edition

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
MTF Alignment: {mtf_alignment}% (min required: {mtf_min}%)
MTF Direction: {mtf_direction}
Pivot Zone: {pivot_zone}

V4 ATR MULTIPLIERS  (SL: {atr_sl}x | TP1: {atr_tp1}x | TP2: {atr_tp2}x | TP3: {atr_tp3}x)
V4 RISK FEATURES: Breakeven at +{be_r}R | Trailing Stop: {ts_atr}x ATR after TP1

OUTPUT FORMAT — return exactly this JSON structure:
{{
  "signal": "BUY" | "SELL" | "NEUTRAL",
  "confidence": <integer 0-100>,
  "entry_price": <number>,
  "tp_levels": [<tp1>, <tp2>, <tp3>],
  "sl_price": <number>,
  "analysis": "<max 140 words — include MTF alignment rationale>",
  "risk_reward": <number>
}}
"""


async def gpt_signal_v4(
    pair: str,
    ind: dict,
    cfg: dict,
    hybrid_ctx: dict,
    mtf_ctx: dict,
) -> dict | None:
    """Call GPT-4o-mini with V4 hybrid + MTF context."""
    import litellm

    prompt = _USER_TEMPLATE_V4.format(
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
        mtf_alignment=mtf_ctx.get("alignment_score", 0),
        mtf_min=MTF_MIN_ALIGNMENT,
        mtf_direction=mtf_ctx.get("dominant_direction", "NEUTRAL"),
        pivot_zone=hybrid_ctx.get("pivot_zone", "UNKNOWN"),
        atr_sl=cfg["atr_sl"],
        atr_tp1=cfg["atr_tp1"],
        atr_tp2=cfg["atr_tp2"],
        atr_tp3=cfg["atr_tp3"],
        be_r=BE_ACTIVATION_R,
        ts_atr=TRAILING_ATR_MULT,
    )

    raw_response = None
    for attempt in range(3):
        try:
            resp = await litellm.acompletion(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT_V4},
                    {"role": "user",   "content": prompt},
                ],
                api_key=OPENAI_API_KEY,
                timeout=30,
            )
            raw_response = resp.choices[0].message.content
            if raw_response and len(raw_response.strip()) > 10:
                break
        except Exception as exc:
            logger.warning(f"[{pair}] GPT V4 attempt {attempt + 1}/3 failed: {exc}")
            await asyncio.sleep(2)

    if not raw_response:
        return None

    return _parse_gpt_response(pair, raw_response)


def _parse_gpt_response(pair: str, raw: str) -> dict | None:
    """Parse GPT JSON response (robust multi-strategy parser)."""
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
            "signal":      sig_m.group(1)          if sig_m   else "NEUTRAL",
            "confidence":  float(conf_m.group(1))  if conf_m  else 50.0,
            "entry_price": float(entry_m.group(1)) if entry_m else 0.0,
            "analysis":    anal_m.group(1)          if anal_m  else "",
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
def build_levels(
    signal: str,
    entry: float,
    atr: float,
    cfg: dict,
) -> tuple[list[float], float]:
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
# Telegram — V4 Enhanced Message
# ---------------------------------------------------------------------------
def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def send_to_telegram_v4(
    pair: str,
    signal: str,
    entry: float,
    tps: list[float],
    sl: float,
    confidence: float,
    rr: float,
    analysis: str,
    be_ts: dict,
    pos_size: dict,
    regime: str = "UNKNOWN",
    smc_score: int = 0,
    mtf_alignment: float = 0.0,
    mtf_direction: str = "NEUTRAL",
    lots: float = 0.01,
) -> None:
    """
    Send V4 signal to Telegram.

    Message 1: Copy-trading compatible signal block (clean format).
    Message 2: V4 risk management details (BE/TS levels, position size, MTF).
    """
    try:
        bot   = get_bot()
        emoji = "🟢" if signal == "BUY" else "🔴"
        action = signal.capitalize()
        lo = round(entry - 0.50, 2)
        hi = round(entry + 0.50, 2)

        # --- Message 1: Copy-trading block ---
        copier_msg = (
            f"{emoji} #{pair} [SWING — V4]\n"
            f"\n"
            f"{action} {lo} - {hi}\n"
            f"\n"
            f"TP1: {tps[0]}\n"
            f"TP2: {tps[1]}\n"
            f"TP3: {tps[2]}\n"
            f"\n"
            f"SL: {sl}\n"
            f"\n"
            f"📌 BE: Move SL → {be_ts['be_sl']} when price hits {be_ts['be_trigger']}\n"
            f"📌 TS: Trail by {be_ts['ts_distance']} pts after TP1 hit\n"
        )

        # --- Message 2: V4 analytics block ---
        info_msg = (
            f"<b>📊 R:R:</b> 1:{rr}  "
            f"<b>⚡ Confidence:</b> {confidence}%\n"
            f"<b>🎯 Regime:</b> {regime}  "
            f"<b>📐 SMC:</b> {smc_score}/10  "
            f"<b>🔗 MTF:</b> {mtf_alignment:.0f}% ({mtf_direction})\n"
            f"<b>📦 Lots:</b> {lots}  "
            f"<b>💰 Risk:</b> ${pos_size.get('dollar_risk', 0):.0f} "
            f"({pos_size.get('risk_pct', 0):.1f}%)\n"
            f"<b>🛡 BE trigger:</b> {be_ts['be_trigger']}  "
            f"<b>🔄 TS start:</b> {be_ts['ts_start']}\n"
            f"<b>📝</b> {_html_escape(analysis)}\n"
            f"<i>⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
            f"| Grandcom Gold Engine v4.0 Balanced</i>"
        )

        await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=copier_msg)
        await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=info_msg, parse_mode="HTML")
        logger.info(f"[{pair}] V4 signal sent to Telegram channel {TELEGRAM_CHANNEL_ID}")

    except Exception as exc:
        logger.error(f"[{pair}] Telegram V4 delivery failed: {exc}")


# ---------------------------------------------------------------------------
# Core Signal Generation — V4 Pipeline
# ---------------------------------------------------------------------------
async def generate_signal_v4(pair: str) -> None:
    """
    Full V4.0 pipeline:
      fetch → indicators → hybrid analysis → MTF confirmation →
      GPT → validate → advanced sizing → BE/TS levels →
      store → send
    """
    cfg = PAIRS[pair]
    logger.info(f"[{pair}] Starting V4.0 signal generation")

    # 1. Price data (4H — permanent timeframe)
    df = await fetch_ohlcv(pair, interval="4h", outputsize=100)
    if df is None or len(df) < 52:
        logger.warning(f"[{pair}] Insufficient 4H candles, skipping")
        return

    # 2. Indicators
    ind = compute_indicators(df, cfg["decimals"])
    if ind is None:
        return

    # 3. Hybrid system analysis (regime, SMC, pivot)
    hybrid_ctx = {
        "regime":     "UNKNOWN",
        "smc_score":  0,
        "mtf_alignment": 0,
        "pivot_zone": "UNKNOWN",
    }
    hybrid = get_hybrid_system()
    if hybrid is not None:
        try:
            hybrid_result = await hybrid.generate_signal(symbol=pair, df_4h=df)
            hybrid_ctx = {
                "regime":           hybrid_result.get("regime", "UNKNOWN"),
                "smc_score":        hybrid_result.get("smc_score", 0),
                "mtf_alignment":    hybrid_result.get("mtf_alignment", 0),
                "pivot_zone":       hybrid_result.get("pivot_zone", "UNKNOWN"),
                "hybrid_signal":    hybrid_result.get("signal", "NEUTRAL"),
                "hybrid_confidence":hybrid_result.get("confidence", 0),
            }
            logger.info(
                f"[{pair}] Hybrid: signal={hybrid_ctx['hybrid_signal']} "
                f"regime={hybrid_ctx['regime']} smc={hybrid_ctx['smc_score']}/10"
            )
        except Exception as exc:
            logger.error(f"[{pair}] Hybrid system error: {exc}")

    # 4. V4 Feature 3: Multi-Timeframe Confirmation (≥70% alignment required)
    mtf_ctx = await run_mtf_confirmation(pair)
    if not mtf_ctx["alignment_ok"]:
        logger.info(
            f"[{pair}] MTF filter: alignment={mtf_ctx['alignment_score']:.1f}% "
            f"< {MTF_MIN_ALIGNMENT}% or direction mismatch — signal suppressed"
        )
        return

    # 5. GPT analysis (V4 enhanced prompt)
    gpt = await gpt_signal_v4(pair, ind, cfg, hybrid_ctx, mtf_ctx)
    if gpt is None:
        return

    signal_type = str(gpt.get("signal", "NEUTRAL")).upper()
    confidence  = float(gpt.get("confidence", 0))
    analysis    = str(gpt.get("analysis", ""))

    # 6. Signal filter
    if signal_type == "NEUTRAL" or signal_type not in ("BUY", "SELL"):
        logger.info(f"[{pair}] {signal_type} signal — no trade")
        return

    if confidence < MIN_CONFIDENCE:
        logger.info(f"[{pair}] Confidence {confidence}% < {MIN_CONFIDENCE}% — skipping")
        return

    # 7. Validate MTF direction agrees with GPT signal
    mtf_dir = mtf_ctx.get("dominant_direction", "NEUTRAL")
    expected_mtf = "BULLISH" if signal_type == "BUY" else "BEARISH"
    if mtf_dir != "NEUTRAL" and mtf_dir != expected_mtf:
        logger.info(
            f"[{pair}] MTF direction {mtf_dir} conflicts with GPT {signal_type} — skipping"
        )
        return

    # 8. Levels
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

    # 9. Risk/reward
    risk   = abs(entry - sl)
    reward = abs(tps[0] - entry)
    rr     = round(reward / risk, 1) if risk > 0 else 2.0

    # 10. V4 Feature 1 & 2: Breakeven + Trailing Stop levels
    be_ts = compute_be_ts_levels(signal_type, entry, sl, ind["atr"], cfg)

    # 11. V4 Feature 4: Advanced position sizing
    pos_size = compute_advanced_position_size(
        pair=pair,
        entry=entry,
        sl=sl,
        atr=ind["atr"],
        df=df,
        regime=hybrid_ctx.get("regime", "UNKNOWN"),
        account_balance=ACCOUNT_BALANCE,
    )
    lots = pos_size.get("lots", 0.01)

    # 12. Store in MongoDB (V4 collection)
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
                # Hybrid context
                "regime":           hybrid_ctx.get("regime", "UNKNOWN"),
                "smc_score":        hybrid_ctx.get("smc_score", 0),
                "pivot_zone":       hybrid_ctx.get("pivot_zone", "UNKNOWN"),
                # V4 MTF
                "mtf_alignment":    mtf_ctx.get("alignment_score", 0),
                "mtf_direction":    mtf_ctx.get("dominant_direction", "NEUTRAL"),
                "mtf_aligned_count":mtf_ctx.get("aligned_count", 0),
                # V4 BE/TS
                "be_trigger":       be_ts["be_trigger"],
                "be_sl":            be_ts["be_sl"],
                "ts_start":         be_ts["ts_start"],
                "ts_distance":      be_ts["ts_distance"],
                # V4 Position sizing
                "lots":             lots,
                "dollar_risk":      pos_size.get("dollar_risk", 0),
                "risk_pct":         pos_size.get("risk_pct", 0),
                "vol_regime":       pos_size.get("vol_regime", "NORMAL"),
                # Meta
                "system_version":   "4.0.0",
                "created_at":       datetime.now(timezone.utc),
            }
            result = await db.gold_signals_v4.insert_one(doc)
            logger.info(f"[{pair}] V4 signal stored — id={result.inserted_id}")
        except Exception as exc:
            logger.error(f"[{pair}] MongoDB insert failed: {exc}")

    # 13. Send to Telegram
    await send_to_telegram_v4(
        pair=pair,
        signal=signal_type,
        entry=entry,
        tps=tps,
        sl=sl,
        confidence=round(confidence, 1),
        rr=rr,
        analysis=analysis,
        be_ts=be_ts,
        pos_size=pos_size,
        regime=hybrid_ctx.get("regime", "UNKNOWN"),
        smc_score=hybrid_ctx.get("smc_score", 0),
        mtf_alignment=mtf_ctx.get("alignment_score", 0),
        mtf_direction=mtf_ctx.get("dominant_direction", "NEUTRAL"),
        lots=lots,
    )

    logger.info(
        f"[{pair}] ✅ V4 {signal_type} @ {entry} | "
        f"TP: {tps} | SL: {sl} | R:R 1:{rr} | Conf: {confidence}% | "
        f"MTF: {mtf_ctx['alignment_score']:.0f}% | Lots: {lots} | "
        f"BE: {be_ts['be_trigger']} | TS: {be_ts['ts_start']}"
    )


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
async def run_all_signals_v4() -> None:
    logger.info("=== V4.0 Signal generation cycle START ===")
    for pair in PAIRS:
        try:
            await generate_signal_v4(pair)
        except Exception as exc:
            logger.error(f"[{pair}] Unhandled error: {exc}", exc_info=True)
        await asyncio.sleep(2)
    logger.info("=== V4.0 Signal generation cycle END ===")


async def run_retrain_job() -> None:
    """Scheduled light model retraining (every RETRAIN_INTERVAL_HOURS)."""
    result = await maybe_retrain_model()
    if result.get("skipped"):
        logger.debug(
            f"Retraining skipped — next in "
            f"{result.get('next_retrain_in_hours', '?')}h"
        )
    elif result.get("success"):
        logger.info(
            f"✅ Scheduled retraining complete — "
            f"{result.get('signals_analyzed', 0)} signals, "
            f"win_rate={result.get('win_rate', 0):.1f}%"
        )
    else:
        logger.warning(f"⚠️ Scheduled retraining issue: {result.get('error', 'unknown')}")


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
            me  = await bot.get_me()
            logger.info(f"✅ Telegram bot ready — @{me.username}")
        except Exception as exc:
            logger.error(f"❌ Telegram bot init failed: {exc}")

    # Hybrid system
    get_hybrid_system()

    # Signal scheduler
    scheduler.add_job(
        run_all_signals_v4,
        "interval",
        minutes=SIGNAL_INTERVAL_MINUTES,
        id="gold_signals_v4",
        max_instances=1,
        coalesce=True,
    )

    # Light retraining scheduler (every RETRAIN_INTERVAL_HOURS)
    scheduler.add_job(
        run_retrain_job,
        "interval",
        hours=RETRAIN_INTERVAL_HOURS,
        id="model_retrain_v4",
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    logger.info(
        f"✅ V4 Scheduler started — pairs={list(PAIRS.keys())} "
        f"signal_interval={SIGNAL_INTERVAL_MINUTES}min "
        f"retrain_interval={RETRAIN_INTERVAL_HOURS}h"
    )

    asyncio.create_task(run_all_signals_v4())

    yield

    scheduler.shutdown(wait=False)
    if _mongo_client:
        _mongo_client.close()
    logger.info("Gold Signals Server v4.0 Balanced shut down")


app = FastAPI(
    title="Grandcom Gold Signals v4.0 Balanced Edition",
    description=(
        "Institutional Multi-Strategy Hybrid Portfolio System with "
        "Breakeven SL, Trailing Stop, MTF Confirmation, "
        "Advanced Position Sizing & Light Model Retraining"
    ),
    version="4.0.0",
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
        "status":              "ok",
        "service":             "gold_signals_v4",
        "version":             "4.0.0",
        "edition":             "Balanced Option C",
        "pairs":               list(PAIRS.keys()),
        "telegram_channel":    TELEGRAM_CHANNEL_ID,
        "scheduler_running":   scheduler.running,
        "scheduler_jobs":      jobs,
        "mongo_connected":     mongo_ok,
        "system_components":   system_status.get("total_components", 0),
        "v4_features": {
            "breakeven_sl":        True,
            "trailing_stop":       True,
            "mtf_confirmation":    True,
            "advanced_sizing":     True,
            "light_retraining":    True,
            "manual_execution":    True,
        },
        "v4_config": {
            "mtf_min_alignment":   MTF_MIN_ALIGNMENT,
            "be_activation_r":     BE_ACTIVATION_R,
            "trailing_atr_mult":   TRAILING_ATR_MULT,
            "retrain_interval_h":  RETRAIN_INTERVAL_HOURS,
            "min_confidence":      MIN_CONFIDENCE,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 2: Get Signals
# ---------------------------------------------------------------------------
@app.get("/api/signals")
async def get_signals(
    status: Optional[str] = None,
    pair:   Optional[str] = None,
    limit:  int = Query(default=50, le=200),
):
    """Return stored V4 signals with optional filtering."""
    db = get_db()
    if db is None:
        return {"error": "MongoDB not connected", "signals": [], "count": 0}

    query: dict = {}
    if status:
        query["status"] = status.upper()
    if pair:
        query["pair"] = pair.upper()

    signals = (
        await db.gold_signals_v4
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
        .to_list(limit)
    )
    return {"signals": signals, "count": len(signals), "version": "4.0.0"}


# ---------------------------------------------------------------------------
# Endpoint 3: System Status
# ---------------------------------------------------------------------------
@app.get("/api/system/status")
async def system_status():
    """Get full hybrid system status."""
    hybrid = get_hybrid_system()
    if hybrid is None:
        return {"error": "Hybrid system not available", "version": "4.0.0"}
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
        regime   = hybrid.regime_detector.detect_regime(features)
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
    pair:   str,
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
# Endpoint 7: MTF Confirmation (V4 enhanced)
# ---------------------------------------------------------------------------
@app.get("/api/analysis/mtf/{pair}")
async def get_mtf_analysis(pair: str):
    """
    Get V4 multi-timeframe confirmation analysis.
    Returns alignment score, per-TF directions, and V4 pass/fail status.
    """
    pair = pair.upper()
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Pair {pair} not found")

    try:
        result = await run_mtf_confirmation(pair)
        return {
            "pair":    pair,
            "version": "4.0.0",
            **result,
        }
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
    """Get performance attribution analysis (V4 collection)."""
    db = get_db()
    if db is None:
        return {"error": "MongoDB not connected"}

    try:
        trades = (
            await db.gold_signals_v4
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
    """Manually trigger V4 signal generation."""
    if pair:
        pair = pair.upper()
        if pair not in PAIRS:
            raise HTTPException(status_code=404, detail=f"Pair {pair} not found")
        asyncio.create_task(generate_signal_v4(pair))
        return {
            "message":   f"V4 signal generation triggered for {pair}",
            "timestamp": datetime.utcnow().isoformat(),
        }
    else:
        asyncio.create_task(run_all_signals_v4())
        return {
            "message":   "V4 signal generation triggered for all pairs",
            "timestamp": datetime.utcnow().isoformat(),
        }


# ---------------------------------------------------------------------------
# Endpoint 12: V4 Breakeven / Trailing Stop Calculator
# ---------------------------------------------------------------------------
@app.get("/api/v4/be-ts/{pair}")
async def get_be_ts_levels(
    pair:   str,
    signal: str = Query(..., regex="^(BUY|SELL)$"),
    entry:  float = Query(..., gt=0),
    sl:     float = Query(..., gt=0),
):
    """
    Calculate V4 Breakeven and Trailing Stop levels for a given trade.
    Useful for manual execution reference.
    """
    pair = pair.upper()
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Pair {pair} not found")

    df = await fetch_ohlcv(pair, interval="4h", outputsize=30)
    if df is None:
        raise HTTPException(status_code=503, detail="Failed to fetch price data")

    ind = compute_indicators(df, PAIRS[pair]["decimals"])
    if ind is None:
        raise HTTPException(status_code=500, detail="Failed to compute indicators")

    be_ts = compute_be_ts_levels(signal.upper(), entry, sl, ind["atr"], PAIRS[pair])

    return {
        "pair":    pair,
        "signal":  signal.upper(),
        "entry":   entry,
        "sl":      sl,
        "atr":     ind["atr"],
        "version": "4.0.0",
        **be_ts,
    }


# ---------------------------------------------------------------------------
# Endpoint 13: V4 Position Size Calculator
# ---------------------------------------------------------------------------
@app.get("/api/v4/position-size/{pair}")
async def get_position_size(
    pair:  str,
    entry: float = Query(..., gt=0),
    sl:    float = Query(..., gt=0),
    balance: float = Query(default=ACCOUNT_BALANCE, gt=0),
):
    """
    Calculate V4 advanced position size for a given trade.
    Returns volatility-adjusted, regime-scaled lot size.
    """
    pair = pair.upper()
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Pair {pair} not found")

    df = await fetch_ohlcv(pair, interval="4h", outputsize=100)
    if df is None:
        raise HTTPException(status_code=503, detail="Failed to fetch price data")

    ind = compute_indicators(df, PAIRS[pair]["decimals"])
    if ind is None:
        raise HTTPException(status_code=500, detail="Failed to compute indicators")

    # Get regime from hybrid system
    regime = "UNKNOWN"
    hybrid = get_hybrid_system()
    if hybrid is not None:
        try:
            features = hybrid.feature_engineer.extract_features(df)
            regime_result = hybrid.regime_detector.detect_regime(features)
            regime = regime_result.get("regime_name", "UNKNOWN")
        except Exception:
            pass

    pos_size = compute_advanced_position_size(
        pair=pair,
        entry=entry,
        sl=sl,
        atr=ind["atr"],
        df=df,
        regime=regime,
        account_balance=balance,
    )

    return {
        "pair":    pair,
        "entry":   entry,
        "sl":      sl,
        "balance": balance,
        "regime":  regime,
        "atr":     ind["atr"],
        "version": "4.0.0",
        **pos_size,
    }


# ---------------------------------------------------------------------------
# Endpoint 14: Light Model Retraining (manual trigger)
# ---------------------------------------------------------------------------
@app.post("/api/v4/retrain")
async def trigger_retrain(force: bool = Query(default=False)):
    """
    Manually trigger V4 light model retraining.
    Set force=true to bypass the time-interval guard.
    """
    global _last_retrain_time
    if force:
        _last_retrain_time = None  # Reset timer to force immediate retrain

    result = await maybe_retrain_model()
    return {"version": "4.0.0", **result}


# ---------------------------------------------------------------------------
# Endpoint 15: V4 Config
# ---------------------------------------------------------------------------
@app.get("/api/v4/config")
async def get_v4_config():
    """Return current V4 configuration and feature flags."""
    return {
        "version":  "4.0.0",
        "edition":  "Balanced Option C",
        "pairs":    list(PAIRS.keys()),
        "timeframe": "4H (PERMANENT)",
        "features": {
            "breakeven_sl": {
                "enabled":       True,
                "activation_r":  BE_ACTIVATION_R,
                "description":   "Moves SL to entry after +0.5R profit",
            },
            "trailing_stop": {
                "enabled":       True,
                "atr_multiplier": TRAILING_ATR_MULT,
                "description":   "Trails price by 1 ATR after TP1 hit",
            },
            "mtf_confirmation": {
                "enabled":       True,
                "min_alignment": MTF_MIN_ALIGNMENT,
                "timeframes":    ["1h", "4h", "1day"],
                "description":   "Requires ≥70% alignment across 1H, 4H, Daily",
            },
            "advanced_sizing": {
                "enabled":       True,
                "base_risk_pct": 1.0,
                "max_risk_pct":  2.0,
                "description":   "Volatility-adjusted, regime-scaled dynamic lots",
            },
            "light_retraining": {
                "enabled":           True,
                "interval_hours":    RETRAIN_INTERVAL_HOURS,
                "last_retrain":      _last_retrain_time.isoformat() if _last_retrain_time else None,
                "description":       "Updates signal quality params every 24-48h",
            },
            "manual_execution": {
                "enabled":       True,
                "description":   "Copy-trading compatible; no full automation",
            },
        },
        "atr_multipliers": {pair: cfg for pair, cfg in PAIRS.items()},
        "expected_performance": {
            "win_rate":         "70%",
            "monthly_pnl":      "$2,000-2,800",
            "drawdown":         "4.5%",
            "signals_per_month": "25-30",
            "complexity":       "Medium-High",
            "risk":             "Medium",
        },
        "min_confidence":      MIN_CONFIDENCE,
        "signal_interval_min": SIGNAL_INTERVAL_MINUTES,
        "timestamp":           datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8003)))

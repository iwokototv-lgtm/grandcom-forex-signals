"""
Grandcom Gold Signals Server v4.0 — Balanced Edition
Institutional Multi-Strategy Hybrid Portfolio System with Advanced Risk Management

New in V4.0 vs V3.0:
  ✅ Breakeven Stop-Loss  — auto-moves SL to entry after TP1 hit (+0.5R trigger)
  ✅ Trailing Stop        — 1-ATR trail on strong moves (env-flag: ENABLE_TRAILING_STOP)
  ✅ Multi-Timeframe Confirmation — 4H signal confirmed by 1H + Daily (2/3 required)
  ✅ Advanced Position Sizing    — volatility-regime-aware lot sizing (hard cap 1.5x)
  ✅ Light Model Retraining      — dynamic signal-weight adaptation from closed trades
  ✅ Manual Execution            — copy-trading-compatible, no full automation

Target metrics (vs V3.0):
  Win Rate   : 65% → 70%   (+5%)
  Monthly P&L: $1,500-2,000 → $2,000-2,800  (+40%)
  Drawdown   : 5.76% → 4.5%  (-22%)
  Signals    : ~40 → 25-30   (-37%, higher quality)

Runtime : Python 3.11 / FastAPI
Timeframe: 4H (PERMANENT)
Pairs    : XAUUSD & XAUEUR
"""

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional

import aiohttp
import numpy as np
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
# Config — environment variables
# ---------------------------------------------------------------------------
MONGO_URL              = os.environ.get("MONGO_URL", "")
DB_NAME                = os.environ.get("DB_NAME", "gold_signals_v4")
TELEGRAM_BOT_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TWELVE_DATA_API_KEY    = os.environ.get("TWELVE_DATA_API_KEY", "")
OPENAI_API_KEY         = os.environ.get("OPENAI_API_KEY") or os.environ.get("EMERGENT_LLM_KEY", "")

_raw_channel = os.environ.get("TELEGRAM_GOLD_CHANNEL_ID", "-1003834233408")
try:
    TELEGRAM_CHANNEL_ID: int | str = int(_raw_channel)
except ValueError:
    TELEGRAM_CHANNEL_ID = _raw_channel

SIGNAL_INTERVAL_MINUTES = int(os.environ.get("SIGNAL_INTERVAL_MINUTES", "2"))
MIN_CONFIDENCE          = int(os.environ.get("MIN_CONFIDENCE", "65"))       # raised from 60
ACCOUNT_BALANCE         = float(os.environ.get("DEFAULT_ACCOUNT_BALANCE", "10000.0"))

# V4.0 feature flags
ENABLE_TRAILING_STOP    = os.environ.get("ENABLE_TRAILING_STOP", "false").lower() == "true"
ENABLE_MTF_FILTER       = os.environ.get("ENABLE_MTF_FILTER", "true").lower() == "true"
MTF_MIN_ALIGNED         = int(os.environ.get("MTF_MIN_ALIGNED", "2"))       # 2 of 3 TFs required
BE_TRIGGER_R            = float(os.environ.get("BE_TRIGGER_R", "0.5"))      # breakeven at +0.5R
TRAILING_ATR_MULT       = float(os.environ.get("TRAILING_ATR_MULT", "1.0")) # trail = 1x ATR
MAX_LOT_MULTIPLIER      = float(os.environ.get("MAX_LOT_MULTIPLIER", "1.5"))# hard cap on sizing
RETRAIN_MIN_TRADES      = int(os.environ.get("RETRAIN_MIN_TRADES", "20"))   # min closed trades to retrain

# ---------------------------------------------------------------------------
# Trading Pairs — V4.0 ATR multipliers (tighter TP1 for faster BE trigger)
# ---------------------------------------------------------------------------
PAIRS = {
    "XAUUSD": {
        "symbol":   "XAU/USD",
        "decimals": 2,
        "atr_sl":   1.0,    # SL: 1.0x ATR  — slightly wider than V3 for fewer stop-outs
        "atr_tp1":  0.5,    # TP1: 0.5x ATR — quick exit, triggers breakeven
        "atr_tp2":  1.0,    # TP2: 1.0x ATR — mid target
        "atr_tp3":  1.8,    # TP3: 1.8x ATR — full target (1.8R)
        "base_risk_pct": 1.5,  # % of account risked per trade (V4 default)
    },
    "XAUEUR": {
        "symbol":   "XAU/EUR",
        "decimals": 2,
        "atr_sl":   1.0,
        "atr_tp1":  0.5,
        "atr_tp2":  1.0,
        "atr_tp3":  1.8,
        "base_risk_pct": 1.5,
    },
}

# ---------------------------------------------------------------------------
# Volatility regime → position-size multiplier  (V4 Advanced Position Sizing)
# ---------------------------------------------------------------------------
VOL_REGIME_MULTIPLIERS = {
    "SQUEEZE":         1.2,   # Low vol → slightly larger size
    "NORMAL":          1.0,   # Baseline
    "EXPANDING":       0.75,  # Rising vol → reduce size
    "HIGH_EXPANDING":  0.5,   # High vol → half size
    "EXTREME_HIGH":    0.25,  # Extreme vol → quarter size
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
            logger.info("✅ HybridPortfolioSystemV3 loaded for V4.0")
        except Exception as exc:
            logger.error(f"❌ Failed to load HybridPortfolioSystemV3: {exc}")
            _hybrid_system = None
    return _hybrid_system


# ---------------------------------------------------------------------------
# Light Model Retrainer  (V4.0 — adapts signal weights from closed trades)
# ---------------------------------------------------------------------------
class LightModelRetrainer:
    """
    Lightweight online-learning layer that adjusts signal confidence weights
    based on recent closed-trade outcomes.  No heavy ML refit — uses an
    exponentially-weighted win-rate per (regime, signal_type) bucket.

    Weight update rule:
        w_new = alpha * outcome + (1 - alpha) * w_old
        where outcome = 1.0 for WIN, 0.0 for LOSS, alpha = 0.15 (fast decay)

    The resulting weight is applied as a confidence multiplier in [0.80, 1.20].
    """

    ALPHA = 0.15          # EWA learning rate
    DEFAULT_WEIGHT = 1.0  # Neutral multiplier before any data

    def __init__(self):
        # bucket key: (regime, signal_type) → EWA win-rate in [0, 1]
        self._weights: dict[tuple[str, str], float] = {}
        self._trade_counts: dict[tuple[str, str], int] = {}
        self._last_retrain: datetime | None = None

    def update(self, regime: str, signal_type: str, result: str) -> None:
        """Record a closed trade outcome and update the EWA weight."""
        key = (regime.upper(), signal_type.upper())
        outcome = 1.0 if result.upper() == "WIN" else 0.0
        old = self._weights.get(key, 0.65)  # seed at 65% win-rate prior
        self._weights[key] = self.ALPHA * outcome + (1 - self.ALPHA) * old
        self._trade_counts[key] = self._trade_counts.get(key, 0) + 1
        logger.debug(f"[Retrainer] {key} → win_rate={self._weights[key]:.3f}")

    def get_confidence_multiplier(self, regime: str, signal_type: str) -> float:
        """
        Return a confidence multiplier in [0.80, 1.20] based on recent
        win-rate for this (regime, signal_type) bucket.

        Neutral (no data) → 1.0
        Win-rate 0.70+    → up to 1.20
        Win-rate 0.50-    → down to 0.80
        """
        key = (regime.upper(), signal_type.upper())
        if key not in self._weights:
            return self.DEFAULT_WEIGHT
        win_rate = self._weights[key]
        # Linear map: 0.50 → 0.80, 0.65 → 1.00, 0.80 → 1.20
        multiplier = 0.80 + (win_rate - 0.50) * (0.40 / 0.30)
        return round(max(0.80, min(1.20, multiplier)), 4)

    def bulk_update_from_db(self, closed_trades: list[dict]) -> int:
        """Ingest a batch of closed trades from MongoDB. Returns count processed."""
        count = 0
        for trade in closed_trades:
            regime = trade.get("regime", "UNKNOWN")
            signal_type = trade.get("type", "")
            result = trade.get("result", trade.get("status", ""))
            if signal_type in ("BUY", "SELL") and result in ("WIN", "LOSS"):
                self.update(regime, signal_type, result)
                count += 1
        if count:
            self._last_retrain = datetime.now(timezone.utc)
            logger.info(f"[Retrainer] Bulk update: {count} trades processed")
        return count

    def get_stats(self) -> dict:
        return {
            "buckets": {
                f"{k[0]}_{k[1]}": {
                    "win_rate": round(v, 4),
                    "trades": self._trade_counts.get(k, 0),
                    "multiplier": self.get_confidence_multiplier(k[0], k[1]),
                }
                for k, v in self._weights.items()
            },
            "last_retrain": self._last_retrain.isoformat() if self._last_retrain else None,
        }


# Global retrainer instance
_retrainer = LightModelRetrainer()


# ---------------------------------------------------------------------------
# Price Data
# ---------------------------------------------------------------------------
async def fetch_ohlcv(
    pair: str, interval: str = "4h", outputsize: int = 100
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
        logger.error(f"[{pair}] fetch_ohlcv({interval}) failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Technical Indicators
# ---------------------------------------------------------------------------
def compute_indicators(df: pd.DataFrame, decimals: int) -> dict | None:
    """Compute RSI, MACD, MA20/50, ATR."""
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
# V4.0 — Multi-Timeframe Confirmation  (1H + 4H + Daily, 2/3 required)
# ---------------------------------------------------------------------------
def _tf_direction(df: pd.DataFrame) -> str:
    """
    Determine directional bias for a single timeframe DataFrame.
    Uses EMA20/50 alignment + RSI side.  Returns 'BULLISH', 'BEARISH', or 'NEUTRAL'.
    """
    try:
        close = df["close"]
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()

        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi   = (100 - (100 / (1 + rs))).iloc[-1]

        price   = float(close.iloc[-1])
        e20     = float(ema20.iloc[-1])
        e50     = float(ema50.iloc[-1])

        bull = 0
        bear = 0
        if price > e20:  bull += 1
        else:            bear += 1
        if e20 > e50:    bull += 1
        else:            bear += 1
        if rsi > 50:     bull += 1
        else:            bear += 1

        if bull >= 2:    return "BULLISH"
        if bear >= 2:    return "BEARISH"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


async def mtf_confirm(pair: str, primary_signal: str) -> dict:
    """
    Fetch 1H and Daily candles and check alignment with the 4H signal.
    Returns a dict with per-TF directions and whether the signal passes the
    2/3 alignment requirement.

    primary_signal: 'BUY' or 'SELL'
    """
    if not ENABLE_MTF_FILTER:
        return {
            "enabled": False,
            "passed": True,
            "aligned": 3,
            "required": MTF_MIN_ALIGNED,
            "directions": {},
        }

    required_direction = "BULLISH" if primary_signal == "BUY" else "BEARISH"
    directions: dict[str, str] = {}

    # Fetch 1H and Daily concurrently
    df_1h_task    = asyncio.create_task(fetch_ohlcv(pair, interval="1h",    outputsize=80))
    df_daily_task = asyncio.create_task(fetch_ohlcv(pair, interval="1day",  outputsize=60))

    df_1h    = await df_1h_task
    df_daily = await df_daily_task

    if df_1h is not None and len(df_1h) >= 30:
        directions["1H"] = _tf_direction(df_1h)
    else:
        directions["1H"] = "NEUTRAL"

    if df_daily is not None and len(df_daily) >= 30:
        directions["Daily"] = _tf_direction(df_daily)
    else:
        directions["Daily"] = "NEUTRAL"

    # 4H is the primary signal — count it as aligned
    directions["4H"] = required_direction

    aligned = sum(1 for d in directions.values() if d == required_direction)
    passed  = aligned >= MTF_MIN_ALIGNED

    logger.info(
        f"[{pair}] MTF confirm: signal={primary_signal} "
        f"1H={directions['1H']} 4H={directions['4H']} Daily={directions['Daily']} "
        f"aligned={aligned}/{len(directions)} passed={passed}"
    )

    return {
        "enabled":   True,
        "passed":    passed,
        "aligned":   aligned,
        "required":  MTF_MIN_ALIGNED,
        "directions": directions,
    }


# ---------------------------------------------------------------------------
# V4.0 — Advanced Position Sizing
# ---------------------------------------------------------------------------
def compute_vol_regime(df: pd.DataFrame) -> str:
    """
    Classify current volatility regime from the 4H DataFrame.
    Uses short/long ATR ratio (same logic as VolatilityAdjustment engine).
    """
    try:
        high  = df["high"]
        low   = df["low"]
        close = df["close"]

        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs(),
        ], axis=1).max(axis=1)

        atr_short = float(tr.rolling(5).mean().iloc[-1])
        atr_long  = float(tr.rolling(20).mean().iloc[-1])
        ratio     = atr_short / atr_long if atr_long > 0 else 1.0

        returns   = close.pct_change().dropna()
        realized  = float(returns.tail(20).std())

        if realized > 0.025:
            return "EXTREME_HIGH"
        if ratio > 1.5:
            return "HIGH_EXPANDING"
        if ratio > 1.2:
            return "EXPANDING"
        if ratio < 0.7:
            return "SQUEEZE"
        return "NORMAL"
    except Exception:
        return "NORMAL"


def advanced_position_size(
    account_balance: float,
    entry_price: float,
    sl_price: float,
    atr: float,
    vol_regime: str,
    base_risk_pct: float = 1.5,
    regime_risk_mult: float = 1.0,
) -> dict:
    """
    V4.0 Advanced Position Sizing.

    Steps:
    1. Dollar risk = account_balance × base_risk_pct% × regime_risk_mult
    2. Stop distance in price = |entry - sl|
    3. Raw lots = dollar_risk / (stop_distance × contract_size)
    4. Apply volatility-regime multiplier (SQUEEZE/NORMAL/EXPANDING)
    5. Hard cap at MAX_LOT_MULTIPLIER × base lots

    Returns dict with lots, dollar_risk, risk_pct, vol_regime, multiplier.
    """
    CONTRACT_SIZE = 100.0   # oz per lot for gold
    MIN_LOT       = 0.01
    MAX_LOT       = 10.0

    stop_distance = abs(entry_price - sl_price)
    if stop_distance <= 0:
        return {"lots": MIN_LOT, "valid": False, "error": "Zero stop distance"}

    # Step 1-3: base lots from fixed-risk formula
    dollar_risk = account_balance * (base_risk_pct / 100.0) * regime_risk_mult
    base_lots   = dollar_risk / (stop_distance * CONTRACT_SIZE)

    # Step 4: volatility-regime multiplier
    vol_mult = VOL_REGIME_MULTIPLIERS.get(vol_regime, 1.0)
    adjusted_lots = base_lots * vol_mult

    # Step 5: hard cap at 1.5× base lots
    cap_lots  = base_lots * MAX_LOT_MULTIPLIER
    final_lots = min(adjusted_lots, cap_lots)
    final_lots = max(MIN_LOT, min(MAX_LOT, round(final_lots, 2)))

    actual_dollar_risk = stop_distance * final_lots * CONTRACT_SIZE
    actual_risk_pct    = (actual_dollar_risk / account_balance) * 100 if account_balance > 0 else 0

    return {
        "valid":          True,
        "lots":           final_lots,
        "base_lots":      round(base_lots, 4),
        "vol_regime":     vol_regime,
        "vol_multiplier": vol_mult,
        "dollar_risk":    round(actual_dollar_risk, 2),
        "risk_pct":       round(actual_risk_pct, 3),
        "stop_distance":  round(stop_distance, 5),
        "atr":            round(atr, 5),
    }


# ---------------------------------------------------------------------------
# V4.0 — Breakeven & Trailing Stop Levels
# ---------------------------------------------------------------------------
def compute_be_and_trail(
    signal: str,
    entry: float,
    sl: float,
    atr: float,
    decimals: int,
) -> dict:
    """
    Compute breakeven trigger price and trailing stop parameters.

    Breakeven:
        Trigger = entry ± (BE_TRIGGER_R × risk)   (default +0.5R)
        New SL   = entry (zero-risk after trigger)

    Trailing Stop (if ENABLE_TRAILING_STOP):
        Trail distance = TRAILING_ATR_MULT × ATR
        Activates once price moves > 1R in favour
    """
    risk = abs(entry - sl)
    dp   = decimals

    if signal == "BUY":
        be_trigger  = round(entry + BE_TRIGGER_R * risk, dp)
        trail_start = round(entry + risk, dp)           # activates at +1R
        trail_dist  = round(TRAILING_ATR_MULT * atr, dp)
    else:
        be_trigger  = round(entry - BE_TRIGGER_R * risk, dp)
        trail_start = round(entry - risk, dp)
        trail_dist  = round(TRAILING_ATR_MULT * atr, dp)

    return {
        "be_trigger_price":  be_trigger,
        "be_new_sl":         round(entry, dp),
        "be_trigger_r":      BE_TRIGGER_R,
        "trailing_enabled":  ENABLE_TRAILING_STOP,
        "trail_start_price": trail_start,
        "trail_distance":    trail_dist,
        "trail_atr_mult":    TRAILING_ATR_MULT,
    }


# ---------------------------------------------------------------------------
# TP/SL Levels
# ---------------------------------------------------------------------------
def build_levels(
    signal: str, entry: float, atr: float, cfg: dict
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
# GPT Signal
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT_V4 = (
    "You are an elite institutional gold trader using the Hybrid Portfolio System v4.0 "
    "Balanced Edition. Analyse the provided market data and return a JSON trading signal. "
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
Vol Regime: {vol_regime}
SMC Score: {smc_score}/10
MTF Alignment: {mtf_alignment}%
MTF Directions: 1H={mtf_1h} | 4H={mtf_4h} | Daily={mtf_daily}
Pivot Zone: {pivot_zone}
Confidence Multiplier (model): {conf_mult}x

ATR MULTIPLIERS  (SL: {atr_sl}x | TP1: {atr_tp1}x | TP2: {atr_tp2}x | TP3: {atr_tp3}x)
Breakeven triggers at +{be_r}R profit → SL moves to entry (zero-risk)
Trailing Stop: {trailing_status}

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


async def gpt_signal(
    pair: str, ind: dict, cfg: dict, hybrid_ctx: dict, v4_ctx: dict
) -> dict | None:
    """Call GPT-4o-mini with V4.0 context (vol regime, MTF directions, BE info)."""
    import litellm

    mtf_dirs = v4_ctx.get("mtf_directions", {})
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
        vol_regime=v4_ctx.get("vol_regime", "NORMAL"),
        smc_score=hybrid_ctx.get("smc_score", 0),
        mtf_alignment=hybrid_ctx.get("mtf_alignment", 0),
        mtf_1h=mtf_dirs.get("1H", "N/A"),
        mtf_4h=mtf_dirs.get("4H", "N/A"),
        mtf_daily=mtf_dirs.get("Daily", "N/A"),
        pivot_zone=hybrid_ctx.get("pivot_zone", "UNKNOWN"),
        conf_mult=v4_ctx.get("conf_multiplier", 1.0),
        atr_sl=cfg["atr_sl"],
        atr_tp1=cfg["atr_tp1"],
        atr_tp2=cfg["atr_tp2"],
        atr_tp3=cfg["atr_tp3"],
        be_r=BE_TRIGGER_R,
        trailing_status="ENABLED (1 ATR trail)" if ENABLE_TRAILING_STOP else "DISABLED",
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
            logger.warning(f"[{pair}] GPT attempt {attempt + 1}/3 failed: {exc}")
            await asyncio.sleep(2)

    if not raw_response:
        return None

    return _parse_gpt_response(pair, raw_response)


def _parse_gpt_response(pair: str, raw: str) -> dict | None:
    """Parse GPT JSON response (identical robust parser from V3)."""
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
    vol_regime: str = "NORMAL",
    smc_score: int = 0,
    mtf_alignment: float = 0.0,
    mtf_directions: dict | None = None,
    be_info: dict | None = None,
    position_info: dict | None = None,
) -> None:
    """Send V4.0 signal to Telegram with breakeven, trailing, and sizing info."""
    try:
        bot = get_bot()
        emoji  = "🟢" if signal == "BUY" else "🔴"
        action = signal.capitalize()
        lo     = round(entry - 0.50, 2)
        hi     = round(entry + 0.50, 2)

        # ── Copy-trader block (clean, no HTML) ──────────────────────
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

        # ── Breakeven / trailing info ────────────────────────────────
        be_line = ""
        if be_info:
            be_line = (
                f"<b>🔒 BE Trigger:</b> {be_info['be_trigger_price']} "
                f"→ SL moves to {be_info['be_new_sl']} (+{be_info['be_trigger_r']}R)\n"
            )
            if be_info.get("trailing_enabled"):
                be_line += (
                    f"<b>📈 Trail:</b> Activates @ {be_info['trail_start_price']} "
                    f"| Distance: {be_info['trail_distance']}\n"
                )

        # ── Position sizing line ─────────────────────────────────────
        size_line = ""
        if position_info and position_info.get("valid"):
            size_line = (
                f"<b>📦 Lots:</b> {position_info['lots']} "
                f"| Risk: ${position_info['dollar_risk']} ({position_info['risk_pct']:.2f}%) "
                f"| Vol: {position_info['vol_regime']}\n"
            )

        # ── MTF directions ───────────────────────────────────────────
        mtf_line = ""
        if mtf_directions:
            mtf_line = (
                f"<b>🔗 MTF:</b> "
                f"1H={mtf_directions.get('1H','?')} | "
                f"4H={mtf_directions.get('4H','?')} | "
                f"Daily={mtf_directions.get('Daily','?')}\n"
            )

        info_msg = (
            f"<b>📊 R:R:</b> 1:{rr}  "
            f"<b>⚡ Confidence:</b> {confidence}%\n"
            f"<b>🎯 Regime:</b> {regime}  "
            f"<b>📐 SMC:</b> {smc_score}/10  "
            f"<b>📉 MTF:</b> {mtf_alignment:.0f}%\n"
            f"{mtf_line}"
            f"{be_line}"
            f"{size_line}"
            f"<b>📝</b> {_html_escape(analysis)}\n"
            f"<i>⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
            f"| Grandcom Gold Engine v4.0</i>"
        )

        await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=copier_msg)
        await bot.send_message(
            chat_id=TELEGRAM_CHANNEL_ID, text=info_msg, parse_mode="HTML"
        )
        logger.info(f"[{pair}] V4.0 signal sent to Telegram channel {TELEGRAM_CHANNEL_ID}")

    except Exception as exc:
        logger.error(f"[{pair}] Telegram delivery failed: {exc}")


# ---------------------------------------------------------------------------
# Light Retrainer — periodic DB sync
# ---------------------------------------------------------------------------
async def sync_retrainer_from_db() -> None:
    """Pull closed trades from MongoDB and update the light model."""
    db = get_db()
    if db is None:
        return
    try:
        closed = (
            await db.gold_signals_v4
            .find(
                {"status": {"$in": ["WIN", "LOSS"]}, "system_version": {"$regex": "^4\\."}},
                {"_id": 0, "regime": 1, "type": 1, "status": 1},
            )
            .sort("created_at", -1)
            .limit(200)
            .to_list(200)
        )
        count = _retrainer.bulk_update_from_db(closed)
        logger.info(f"[Retrainer] Synced {count} closed trades from DB")
    except Exception as exc:
        logger.error(f"[Retrainer] DB sync failed: {exc}")


# ---------------------------------------------------------------------------
# Core Signal Generation  (V4.0 pipeline)
# ---------------------------------------------------------------------------
async def generate_signal(pair: str) -> None:
    """
    Full V4.0 pipeline:
    fetch → hybrid analysis → MTF confirm → vol regime → advanced sizing
    → GPT → validate → BE/trail levels → store → send
    """
    cfg = PAIRS[pair]
    logger.info(f"[{pair}] Starting v4.0 signal generation")

    # ── 1. Price data (4H primary) ───────────────────────────────────────
    df = await fetch_ohlcv(pair, interval="4h", outputsize=100)
    if df is None or len(df) < 52:
        logger.warning(f"[{pair}] Insufficient 4H candles, skipping")
        return

    # ── 2. Indicators ────────────────────────────────────────────────────
    ind = compute_indicators(df, cfg["decimals"])
    if ind is None:
        return

    # ── 3. Volatility regime (V4 advanced sizing) ────────────────────────
    vol_regime = compute_vol_regime(df)
    logger.info(f"[{pair}] Vol regime: {vol_regime}")

    # ── 4. Hybrid system analysis ────────────────────────────────────────
    hybrid_ctx = {
        "regime": "UNKNOWN", "smc_score": 0,
        "mtf_alignment": 0, "pivot_zone": "UNKNOWN",
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
                "regime_risk_mult": hybrid_result.get(
                    "components", {}
                ).get("regime", {}).get("risk_multiplier", 1.0),
            }
            logger.info(
                f"[{pair}] Hybrid: signal={hybrid_ctx['hybrid_signal']} "
                f"regime={hybrid_ctx['regime']} smc={hybrid_ctx['smc_score']}/10 "
                f"mtf={hybrid_ctx['mtf_alignment']:.0f}%"
            )
        except Exception as exc:
            logger.error(f"[{pair}] Hybrid system error: {exc}")

    # ── 5. Light model confidence multiplier ─────────────────────────────
    #    We need a preliminary signal direction to look up the bucket.
    #    Use hybrid signal if available, else defer to GPT.
    prelim_signal = hybrid_ctx.get("hybrid_signal", "NEUTRAL")
    conf_mult = _retrainer.get_confidence_multiplier(
        hybrid_ctx.get("regime", "UNKNOWN"), prelim_signal
    )

    # ── 6. GPT analysis ──────────────────────────────────────────────────
    v4_ctx = {
        "vol_regime":     vol_regime,
        "conf_multiplier": conf_mult,
        "mtf_directions": {},   # filled after MTF check below
    }
    gpt = await gpt_signal(pair, ind, cfg, hybrid_ctx, v4_ctx)
    if gpt is None:
        return

    signal_type = str(gpt.get("signal", "NEUTRAL")).upper()
    confidence  = float(gpt.get("confidence", 0))
    analysis    = str(gpt.get("analysis", ""))

    # Apply light-model multiplier to GPT confidence
    confidence = round(min(100.0, confidence * conf_mult), 1)

    # ── 7. Basic signal filter ───────────────────────────────────────────
    if signal_type == "NEUTRAL" or signal_type not in ("BUY", "SELL"):
        logger.info(f"[{pair}] {signal_type} signal — no trade")
        return

    if confidence < MIN_CONFIDENCE:
        logger.info(f"[{pair}] Confidence {confidence}% < {MIN_CONFIDENCE}% — skipping")
        return

    # ── 8. V4.0 Multi-Timeframe Confirmation ─────────────────────────────
    mtf_result = await mtf_confirm(pair, signal_type)
    v4_ctx["mtf_directions"] = mtf_result.get("directions", {})

    if not mtf_result["passed"]:
        logger.info(
            f"[{pair}] MTF filter BLOCKED: aligned={mtf_result['aligned']}/"
            f"{len(mtf_result.get('directions', {}))} "
            f"(required {mtf_result['required']}) — skipping"
        )
        return

    # ── 9. Levels ────────────────────────────────────────────────────────
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

    # ── 10. Risk/reward ──────────────────────────────────────────────────
    risk   = abs(entry - sl)
    reward = abs(tps[0] - entry)
    rr     = round(reward / risk, 1) if risk > 0 else 2.0

    # ── 11. V4.0 Advanced Position Sizing ───────────────────────────────
    regime_risk_mult = float(hybrid_ctx.get("regime_risk_mult", 1.0))
    position_info = advanced_position_size(
        account_balance=ACCOUNT_BALANCE,
        entry_price=entry,
        sl_price=sl,
        atr=ind["atr"],
        vol_regime=vol_regime,
        base_risk_pct=cfg["base_risk_pct"],
        regime_risk_mult=regime_risk_mult,
    )

    # ── 12. V4.0 Breakeven & Trailing Stop levels ────────────────────────
    be_info = compute_be_and_trail(
        signal=signal_type,
        entry=entry,
        sl=sl,
        atr=ind["atr"],
        decimals=cfg["decimals"],
    )

    # ── 13. Store in MongoDB ─────────────────────────────────────────────
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
                "vol_regime":       vol_regime,
                "smc_score":        hybrid_ctx.get("smc_score", 0),
                "mtf_alignment":    hybrid_ctx.get("mtf_alignment", 0),
                "mtf_directions":   mtf_result.get("directions", {}),
                "mtf_aligned":      mtf_result.get("aligned", 0),
                "pivot_zone":       hybrid_ctx.get("pivot_zone", "UNKNOWN"),
                "be_trigger_price": be_info["be_trigger_price"],
                "be_new_sl":        be_info["be_new_sl"],
                "trailing_enabled": be_info["trailing_enabled"],
                "trail_distance":   be_info.get("trail_distance"),
                "position_lots":    position_info.get("lots"),
                "position_risk_pct":position_info.get("risk_pct"),
                "conf_multiplier":  conf_mult,
                "system_version":   "4.0.0",
                "created_at":       datetime.now(timezone.utc),
            }
            result = await db.gold_signals_v4.insert_one(doc)
            logger.info(f"[{pair}] Signal stored — id={result.inserted_id}")
        except Exception as exc:
            logger.error(f"[{pair}] MongoDB insert failed: {exc}")

    # ── 14. Send to Telegram ─────────────────────────────────────────────
    await send_to_telegram(
        pair=pair,
        signal=signal_type,
        entry=entry,
        tps=tps,
        sl=sl,
        confidence=round(confidence, 1),
        rr=rr,
        analysis=analysis,
        regime=hybrid_ctx.get("regime", "UNKNOWN"),
        vol_regime=vol_regime,
        smc_score=hybrid_ctx.get("smc_score", 0),
        mtf_alignment=hybrid_ctx.get("mtf_alignment", 0),
        mtf_directions=mtf_result.get("directions", {}),
        be_info=be_info,
        position_info=position_info,
    )

    logger.info(
        f"[{pair}] ✅ v4.0 {signal_type} @ {entry} | "
        f"TP: {tps} | SL: {sl} | R:R 1:{rr} | Conf: {confidence}% | "
        f"Lots: {position_info.get('lots')} | Vol: {vol_regime} | "
        f"BE@{be_info['be_trigger_price']} | MTF {mtf_result['aligned']}/3"
    )


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
async def run_all_signals() -> None:
    logger.info("=== v4.0 Signal generation cycle START ===")
    for pair in PAIRS:
        try:
            await generate_signal(pair)
        except Exception as exc:
            logger.error(f"[{pair}] Unhandled error: {exc}", exc_info=True)
        await asyncio.sleep(2)
    logger.info("=== v4.0 Signal generation cycle END ===")


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mongo_client, _db

    # Startup validation
    missing = []
    if not MONGO_URL:            missing.append("MONGO_URL")
    if not TELEGRAM_BOT_TOKEN:   missing.append("TELEGRAM_BOT_TOKEN")
    if not TWELVE_DATA_API_KEY:  missing.append("TWELVE_DATA_API_KEY")
    if not OPENAI_API_KEY:       missing.append("OPENAI_API_KEY / EMERGENT_LLM_KEY")

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

    # Seed light retrainer from DB
    await sync_retrainer_from_db()

    # Scheduler — signal generation
    scheduler.add_job(
        run_all_signals,
        "interval",
        minutes=SIGNAL_INTERVAL_MINUTES,
        id="gold_signals_v4",
        max_instances=1,
        coalesce=True,
    )

    # Scheduler — retrainer sync (every 6 hours)
    scheduler.add_job(
        sync_retrainer_from_db,
        "interval",
        hours=6,
        id="retrainer_sync",
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    logger.info(
        f"✅ V4.0 Scheduler started — pairs={list(PAIRS.keys())} "
        f"interval={SIGNAL_INTERVAL_MINUTES}min | "
        f"MTF={'ON' if ENABLE_MTF_FILTER else 'OFF'} | "
        f"Trail={'ON' if ENABLE_TRAILING_STOP else 'OFF'}"
    )

    asyncio.create_task(run_all_signals())

    yield

    scheduler.shutdown(wait=False)
    if _mongo_client:
        _mongo_client.close()
    logger.info("Gold Signals Server v4.0 shut down")


app = FastAPI(
    title="Grandcom Gold Signals v4.0 — Balanced Edition",
    description=(
        "Institutional Multi-Strategy Hybrid Portfolio System with "
        "Breakeven Stops, Trailing Stops, MTF Confirmation, and Advanced Position Sizing"
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
        "pairs":               list(PAIRS.keys()),
        "telegram_channel":    TELEGRAM_CHANNEL_ID,
        "scheduler_running":   scheduler.running,
        "scheduler_jobs":      jobs,
        "mongo_connected":     mongo_ok,
        "system_components":   system_status.get("total_components", 0),
        "features": {
            "breakeven_stop":    True,
            "trailing_stop":     ENABLE_TRAILING_STOP,
            "mtf_confirmation":  ENABLE_MTF_FILTER,
            "mtf_min_aligned":   MTF_MIN_ALIGNED,
            "advanced_sizing":   True,
            "light_retraining":  True,
            "be_trigger_r":      BE_TRIGGER_R,
            "trailing_atr_mult": TRAILING_ATR_MULT,
            "max_lot_multiplier":MAX_LOT_MULTIPLIER,
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
    """Return stored V4.0 signals with optional filtering."""
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
    return {"signals": signals, "count": len(signals)}


# ---------------------------------------------------------------------------
# Endpoint 3: System Status
# ---------------------------------------------------------------------------
@app.get("/api/system/status")
async def system_status():
    """Get full hybrid system status."""
    hybrid = get_hybrid_system()
    if hybrid is None:
        return {"error": "Hybrid system not available", "version": "4.0.0"}
    status = hybrid.get_system_status()
    status["v4_features"] = {
        "breakeven_stop":    True,
        "trailing_stop":     ENABLE_TRAILING_STOP,
        "mtf_confirmation":  ENABLE_MTF_FILTER,
        "advanced_sizing":   True,
        "light_retraining":  True,
    }
    return status


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
        vol_reg  = compute_vol_regime(df)
        return {
            "pair":        pair,
            "regime":      regime,
            "vol_regime":  vol_reg,
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        }
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
# Endpoint 7: MTF Confirmation  (V4.0 — 1H + 4H + Daily)
# ---------------------------------------------------------------------------
@app.get("/api/analysis/mtf/{pair}")
async def get_mtf_analysis(pair: str, signal: str = Query(default="BUY")):
    """
    Get V4.0 multi-timeframe confirmation analysis.
    Pass ?signal=BUY or ?signal=SELL to check alignment.
    """
    pair   = pair.upper()
    signal = signal.upper()
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Pair {pair} not found")
    if signal not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="signal must be BUY or SELL")

    try:
        result = await mtf_confirm(pair, signal)
        return {
            "pair":    pair,
            "signal":  signal,
            "result":  result,
            "version": "4.0.0",
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
        result["vol_regime"] = compute_vol_regime(df)
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
    """Manually trigger signal generation."""
    if pair:
        pair = pair.upper()
        if pair not in PAIRS:
            raise HTTPException(status_code=404, detail=f"Pair {pair} not found")
        asyncio.create_task(generate_signal(pair))
        return {
            "message":   f"V4.0 signal generation triggered for {pair}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    else:
        asyncio.create_task(run_all_signals())
        return {
            "message":   "V4.0 signal generation triggered for all pairs",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# Endpoint 12: V4.0 — Breakeven & Trailing Stop Calculator
# ---------------------------------------------------------------------------
@app.get("/api/v4/risk/be-trail")
async def get_be_trail(
    pair:   str   = Query(...),
    signal: str   = Query(...),
    entry:  float = Query(...),
    sl:     float = Query(...),
):
    """
    Calculate breakeven trigger and trailing stop parameters for a given trade.
    Useful for manual execution — copy the levels into your broker platform.
    """
    pair   = pair.upper()
    signal = signal.upper()
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Pair {pair} not found")
    if signal not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="signal must be BUY or SELL")

    df = await fetch_ohlcv(pair, interval="4h", outputsize=20)
    atr = 15.0  # fallback
    if df is not None and len(df) >= 15:
        ind = compute_indicators(df, PAIRS[pair]["decimals"])
        if ind:
            atr = ind["atr"]

    be_info = compute_be_and_trail(
        signal=signal,
        entry=entry,
        sl=sl,
        atr=atr,
        decimals=PAIRS[pair]["decimals"],
    )
    return {
        "pair":    pair,
        "signal":  signal,
        "entry":   entry,
        "sl":      sl,
        "atr":     atr,
        "be_info": be_info,
        "version": "4.0.0",
    }


# ---------------------------------------------------------------------------
# Endpoint 13: V4.0 — Advanced Position Sizing Calculator
# ---------------------------------------------------------------------------
@app.get("/api/v4/risk/position-size")
async def get_position_size(
    pair:    str   = Query(...),
    entry:   float = Query(...),
    sl:      float = Query(...),
    balance: float = Query(default=None),
):
    """
    Calculate V4.0 volatility-adjusted position size for a given trade.
    Useful for manual execution — tells you exactly how many lots to open.
    """
    pair = pair.upper()
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Pair {pair} not found")

    account = balance or ACCOUNT_BALANCE

    df = await fetch_ohlcv(pair, interval="4h", outputsize=30)
    vol_regime = "NORMAL"
    atr        = 15.0
    if df is not None and len(df) >= 20:
        vol_regime = compute_vol_regime(df)
        ind = compute_indicators(df, PAIRS[pair]["decimals"])
        if ind:
            atr = ind["atr"]

    cfg    = PAIRS[pair]
    result = advanced_position_size(
        account_balance=account,
        entry_price=entry,
        sl_price=sl,
        atr=atr,
        vol_regime=vol_regime,
        base_risk_pct=cfg["base_risk_pct"],
    )
    return {
        "pair":          pair,
        "entry":         entry,
        "sl":            sl,
        "account":       account,
        "position_info": result,
        "version":       "4.0.0",
    }


# ---------------------------------------------------------------------------
# Endpoint 14: V4.0 — Light Model Retrainer Stats
# ---------------------------------------------------------------------------
@app.get("/api/v4/model/stats")
async def get_model_stats():
    """Get light model retrainer statistics and per-bucket win rates."""
    return {
        "retrainer": _retrainer.get_stats(),
        "config": {
            "alpha":           LightModelRetrainer.ALPHA,
            "min_trades_retrain": RETRAIN_MIN_TRADES,
            "be_trigger_r":    BE_TRIGGER_R,
            "trailing_enabled":ENABLE_TRAILING_STOP,
            "trailing_atr_mult":TRAILING_ATR_MULT,
            "mtf_filter":      ENABLE_MTF_FILTER,
            "mtf_min_aligned": MTF_MIN_ALIGNED,
            "max_lot_mult":    MAX_LOT_MULTIPLIER,
        },
        "version": "4.0.0",
    }


# ---------------------------------------------------------------------------
# Endpoint 15: V4.0 — Record Trade Result (for retrainer)
# ---------------------------------------------------------------------------
@app.post("/api/v4/trades/{signal_id}/result")
async def record_trade_result(
    signal_id: str,
    result:    str = Query(..., regex="^(WIN|LOSS)$"),
):
    """
    Mark a signal as WIN or LOSS and update the light model retrainer.
    Also updates the MongoDB document status field.
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="MongoDB not connected")

    from bson import ObjectId
    try:
        oid = ObjectId(signal_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signal_id format")

    doc = await db.gold_signals_v4.find_one({"_id": oid})
    if doc is None:
        raise HTTPException(status_code=404, detail="Signal not found")

    await db.gold_signals_v4.update_one(
        {"_id": oid},
        {"$set": {"status": result, "closed_at": datetime.now(timezone.utc)}},
    )

    _retrainer.update(
        regime=doc.get("regime", "UNKNOWN"),
        signal_type=doc.get("type", ""),
        result=result,
    )

    logger.info(f"Trade {signal_id} recorded as {result} — retrainer updated")
    return {
        "signal_id": signal_id,
        "result":    result,
        "pair":      doc.get("pair"),
        "type":      doc.get("type"),
        "regime":    doc.get("regime"),
        "retrainer_stats": _retrainer.get_stats(),
    }


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8002)))

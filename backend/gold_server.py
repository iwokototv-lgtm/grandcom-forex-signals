"""
Grandcom Gold Signals Server — A+ Institutional Elite Edition
Standalone backend for XAUUSD & XAUEUR signals
Sends to @grandcomgold Telegram channel
Designed for Railway deployment

v3 — A+ Elite Edition:
    - Market DNA System (EMA-updated ATR/spread/volatility class, MongoDB persistence)
    - Hurst Exponent (regime detection: TRENDING / MEAN-REVERTING / RANDOM WALK)
    - Shannon Entropy Filter (chaos detection, Gate 08)
    - Keltner Channels (overextension guard, Gate 10)
    - Liquidity Sweep Detection (stop-hunt identification, informational)
    - Gold-Silver Ratio (GSR correlation, Gate 14)
    - Volume Profile POC (Point of Control confirmation, Gate 18)
    - Kelly Criterion (dynamic position sizing, Gate 19)
    - 20-Gate Modular System (toggle on/off per gate)
    - Risk Commander (global kill switch, 5% drawdown protection)
    - Circuit Breaker (flash crash guard, Gate 06)
    - Session Confidence Requirements (Gate 07)
    - Blackbox Logging (JSONL audit trail + CSV denial log)
    - Breakeven Monitor (TP1 hit → SL to entry)
    - Trailing Stop Monitor (ATR × 2.5 trail)
    - Outcome Tracker (SL/TP hit detection)
    - Daily Intelligence Report (07:00 UTC)
    - Telegram Admin Commands (/status /gate /dna /kill /report)
    - Dynamic ATR Settings (volatility-class + Hurst + session aware)
    - Correlation Matrix Monitor (XAUUSD vs DXY auto-gate)
    - Full 27-step signal generation pipeline
"""
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
import os
import csv
import logging
import json
import re
import asyncio
import aiohttp
import ta
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from motor.motor_asyncio import AsyncIOMotorClient
import litellm

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gold_server")

# ============ CONFIG ============
MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME", "gold_signals")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_GOLD_CHANNEL_ID = os.environ.get("TELEGRAM_GOLD_CHANNEL_ID", "@grandcomgold")
TELEGRAM_ADMIN_CHAT_ID = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")
TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("EMERGENT_LLM_KEY")

# Blackbox log paths
BLACKBOX_JSONL = "blackbox_audit.jsonl"
BLACKBOX_DENIAL_CSV = "blackbox_denials.csv"

# Gold pair configuration — ATR-based swing strategy
GOLD_PAIRS = {
    "XAUUSD": {
        "twelve_data_symbol": "XAU/USD",
        "pip_value": 0.10,
        "decimal_places": 2,
        "atr_multiplier_sl": 1.5,
        "atr_multiplier_tp1": 1.0,
        "atr_multiplier_tp2": 2.0,
        "atr_multiplier_tp3": 3.0,
        "min_rr": 1.8,
        "min_confidence": 60,
    },
    "XAUEUR": {
        "twelve_data_symbol": "XAU/EUR",
        "pip_value": 0.10,
        "decimal_places": 2,
        "atr_multiplier_sl": 1.5,
        "atr_multiplier_tp1": 1.0,
        "atr_multiplier_tp2": 2.0,
        "atr_multiplier_tp3": 3.0,
        "min_rr": 1.8,
        "min_confidence": 60,
    },
}

SIGNAL_INTERVAL_HOURS = 4
MIN_CONFIDENCE = 60

# ============ 20-GATE MODULAR SYSTEM ============
GATE_CONFIG = {
    "gate_01_news_guard":        {"enabled": True,  "desc": "High Impact News Filter"},
    "gate_02_h4_mtf":            {"enabled": True,  "desc": "H4 Trend Alignment"},
    "gate_03_dxy_correlation":   {"enabled": True,  "desc": "DXY Correlation Block"},
    "gate_04_candlestick_pa":    {"enabled": True,  "desc": "Price Action Confirmation"},
    "gate_05_regime_chop":       {"enabled": True,  "desc": "Choppy Market Guard"},
    "gate_06_circuit_breaker":   {"enabled": True,  "desc": "Flash Crash Circuit Breaker"},
    "gate_07_session_filter":    {"enabled": True,  "desc": "Session Confidence Filter"},
    "gate_08_entropy_filter":    {"enabled": True,  "desc": "Shannon Entropy (Chaos) Filter"},
    "gate_09_hurst_regime":      {"enabled": True,  "desc": "Hurst Exponent Regime Check"},
    "gate_10_keltner_channel":   {"enabled": True,  "desc": "Keltner Channel Overextension"},
    "gate_11_obv_divergence":    {"enabled": False, "desc": "OBV Volume Divergence (DISABLED)"},
    "gate_12_liquidity_sweep":   {"enabled": True,  "desc": "Liquidity Sweep Detection"},
    "gate_13_order_block":       {"enabled": True,  "desc": "Order Block Strength"},
    "gate_14_gsr_correlation":   {"enabled": True,  "desc": "Gold-Silver Ratio Filter"},
    "gate_15_vw_macd":           {"enabled": False, "desc": "Volume Weighted MACD (DISABLED)"},
    "gate_16_drawdown_guard":    {"enabled": True,  "desc": "Daily Drawdown Protection"},
    "gate_17_throttle_guard":    {"enabled": True,  "desc": "Signal Throttle (6h)"},
    "gate_18_poc_confirmation":  {"enabled": True,  "desc": "Volume Profile POC Check"},
    "gate_19_kelly_sizing":      {"enabled": True,  "desc": "Kelly Criterion Position Sizing"},
    "gate_20_correlation_matrix":{"enabled": True,  "desc": "Multi-Asset Correlation Monitor"},
}

# ============ SESSION CONFIDENCE ============
SESSION_CONFIDENCE = {
    "LONDON_NY_OVERLAP": {"hours": (12, 16), "min_score": 60},
    "LONDON":            {"hours": (7,  12), "min_score": 65},
    "NEW_YORK":          {"hours": (16, 21), "min_score": 65},
    "ASIAN":             {"hours": (0,   7), "min_score": 78},
    "DEAD_ZONE":         {"hours": (21, 24), "min_score": 85},
}

# ============ GLOBAL STATE ============
_kill_switch_active = False
_last_signal_time: dict[str, datetime] = {}
_circuit_breaker_until: datetime | None = None
_daily_loss_pips: float = 0.0
_daily_loss_date: str = ""

# ============ DB ============
client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]


# ============================================================
# MARKET DNA SYSTEM
# ============================================================
class MarketDNA:
    """
    Self-adapting market profile for a single pair.
    Persisted in MongoDB collection 'market_dna'.
    EMA update: 90% old + 10% new observation.
    """

    VOLATILITY_CLASSES = ["LOW", "MEDIUM", "HIGH", "EXTREME"]

    def __init__(self, pair: str):
        self.pair = pair
        self.avg_spread: float = 0.30
        self.atr_14: float = 5.0
        self.volatility_class: str = "MEDIUM"
        self.sl_clamp_multiplier: float = 1.5
        self.avg_slippage: float = 0.0
        self.tp_net_loss_count: int = 0
        self.tp_buffer: float = 0.0
        self.spread_guard: float = 0.0

    async def load(self):
        doc = await db.market_dna.find_one({"pair": self.pair})
        if doc:
            self.avg_spread = doc.get("avg_spread", self.avg_spread)
            self.atr_14 = doc.get("atr_14", self.atr_14)
            self.volatility_class = doc.get("volatility_class", self.volatility_class)
            self.sl_clamp_multiplier = doc.get("sl_clamp_multiplier", self.sl_clamp_multiplier)
            self.avg_slippage = doc.get("avg_slippage", self.avg_slippage)
            self.tp_net_loss_count = doc.get("tp_net_loss_count", self.tp_net_loss_count)
            self.tp_buffer = doc.get("tp_buffer", self.tp_buffer)
            self.spread_guard = doc.get("spread_guard", self.spread_guard)

    async def update(self, new_atr: float, new_spread: float = None):
        """EMA update: 90% old + 10% new."""
        old_atr = self.atr_14
        self.atr_14 = round(0.90 * self.atr_14 + 0.10 * new_atr, 4)
        if new_spread is not None:
            self.avg_spread = round(0.90 * self.avg_spread + 0.10 * new_spread, 4)

        # Detect volatility shift (30% ATR increase → class upgrade)
        atr_change_pct = (new_atr - old_atr) / max(old_atr, 0.001)
        self._update_volatility_class(atr_change_pct)
        self._update_sl_clamp()
        await self._save()

    def _update_volatility_class(self, atr_change_pct: float):
        atr = self.atr_14
        if atr < 3.0:
            base = "LOW"
        elif atr < 8.0:
            base = "MEDIUM"
        elif atr < 15.0:
            base = "HIGH"
        else:
            base = "EXTREME"

        # Upgrade class if 30%+ ATR spike
        if atr_change_pct >= 0.30:
            idx = self.VOLATILITY_CLASSES.index(base)
            if idx < len(self.VOLATILITY_CLASSES) - 1:
                base = self.VOLATILITY_CLASSES[idx + 1]
        self.volatility_class = base

    def _update_sl_clamp(self):
        mapping = {"LOW": 1.2, "MEDIUM": 1.5, "HIGH": 2.0, "EXTREME": 2.5}
        self.sl_clamp_multiplier = mapping.get(self.volatility_class, 1.5)

    def record_slippage(self, slippage: float):
        self.avg_slippage = round(0.90 * self.avg_slippage + 0.10 * slippage, 4)
        self.spread_guard = round(self.avg_slippage * 1.5, 4)

    def record_tp_net_loss(self):
        self.tp_net_loss_count += 1
        self.tp_buffer = round(min(self.tp_buffer + 0.10, 1.0), 4)

    async def _save(self):
        await db.market_dna.update_one(
            {"pair": self.pair},
            {"$set": {
                "pair": self.pair,
                "avg_spread": self.avg_spread,
                "atr_14": self.atr_14,
                "volatility_class": self.volatility_class,
                "sl_clamp_multiplier": self.sl_clamp_multiplier,
                "avg_slippage": self.avg_slippage,
                "tp_net_loss_count": self.tp_net_loss_count,
                "tp_buffer": self.tp_buffer,
                "spread_guard": self.spread_guard,
                "updated_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )

    def to_dict(self) -> dict:
        return {
            "pair": self.pair,
            "avg_spread": self.avg_spread,
            "atr_14": self.atr_14,
            "volatility_class": self.volatility_class,
            "sl_clamp_multiplier": self.sl_clamp_multiplier,
            "avg_slippage": self.avg_slippage,
            "tp_net_loss_count": self.tp_net_loss_count,
            "tp_buffer": self.tp_buffer,
            "spread_guard": self.spread_guard,
        }


# Singleton DNA instances
_market_dna: dict[str, MarketDNA] = {}


async def get_market_dna(pair: str) -> MarketDNA:
    if pair not in _market_dna:
        dna = MarketDNA(pair)
        await dna.load()
        _market_dna[pair] = dna
    return _market_dna[pair]


# ============================================================
# RISK COMMANDER (GLOBAL KILL SWITCH)
# ============================================================
class RiskCommander:
    """
    Monitors account equity and triggers kill switch if drawdown > 5%.
    Connects to MT5 bridge via environment variable MT5_BRIDGE_URL.
    """

    DRAWDOWN_THRESHOLD = 0.05  # 5%

    def __init__(self):
        self.bridge_url = os.environ.get("MT5_BRIDGE_URL", "")
        self.initial_equity: float | None = None

    async def check_equity(self):
        global _kill_switch_active
        if not self.bridge_url:
            return
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.bridge_url}/equity",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    data = await resp.json()
                    equity = float(data.get("equity", 0))
                    balance = float(data.get("balance", equity))

            if self.initial_equity is None:
                self.initial_equity = balance

            drawdown = (self.initial_equity - equity) / max(self.initial_equity, 1)
            if drawdown >= self.DRAWDOWN_THRESHOLD and not _kill_switch_active:
                _kill_switch_active = True
                logger.critical(f"🚨 KILL SWITCH ACTIVATED — drawdown {drawdown*100:.1f}%")
                await _send_admin_alert(
                    f"🚨 KILL SWITCH ACTIVATED\nDrawdown: {drawdown*100:.1f}%\n"
                    f"Equity: {equity:.2f} | Balance: {balance:.2f}\n"
                    "All signal generation PAUSED."
                )
                await self._close_all_positions()
        except Exception as e:
            logger.warning(f"RiskCommander equity check failed: {e}")

    async def _close_all_positions(self):
        if not self.bridge_url:
            return
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    f"{self.bridge_url}/close_all",
                    timeout=aiohttp.ClientTimeout(total=10),
                )
            logger.info("RiskCommander: close_all sent to MT5 bridge")
        except Exception as e:
            logger.error(f"RiskCommander close_all failed: {e}")


risk_commander = RiskCommander()


# ============================================================
# HELPER: SEND ADMIN ALERT
# ============================================================
async def _send_admin_alert(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_CHAT_ID:
        return
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(chat_id=TELEGRAM_ADMIN_CHAT_ID, text=message)
    except Exception as e:
        logger.error(f"Admin alert failed: {e}")


# ============================================================
# BLACKBOX LOGGING
# ============================================================
def log_blackbox(
    pair: str,
    signal: str,
    gate_blocked: str | None,
    indicators: dict,
    extra: dict | None = None,
):
    """Write JSONL audit trail and CSV denial log."""
    try:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pair": pair,
            "signal": signal,
            "gate_blocked": gate_blocked,
            "indicators": {k: v for k, v in indicators.items() if not k.startswith("_")},
            **(extra or {}),
        }
        with open(BLACKBOX_JSONL, "a") as f:
            f.write(json.dumps(record) + "\n")

        if gate_blocked:
            file_exists = os.path.isfile(BLACKBOX_DENIAL_CSV)
            with open(BLACKBOX_DENIAL_CSV, "a", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["timestamp", "pair", "signal", "gate_blocked"],
                )
                if not file_exists:
                    writer.writeheader()
                writer.writerow({
                    "timestamp": record["timestamp"],
                    "pair": pair,
                    "signal": signal,
                    "gate_blocked": gate_blocked,
                })
    except Exception as e:
        logger.error(f"Blackbox log error: {e}")


# ============================================================
# PRICE DATA
# ============================================================
async def get_price_data(pair: str, interval: str = "1h", outputsize: int = 100):
    symbol = GOLD_PAIRS[pair]["twelve_data_symbol"]
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={symbol}&interval={interval}&outputsize={outputsize}"
        f"&apikey={TWELVE_DATA_API_KEY}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
                if "values" not in data:
                    logger.error(f"No data for {pair}: {data.get('message', 'Unknown error')}")
                    return None
                df = pd.DataFrame(data["values"])
                for col in ["open", "high", "low", "close"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.sort_index(ascending=False).reset_index(drop=True)
                return df
    except Exception as e:
        logger.error(f"Error fetching {pair}: {e}")
        return None


async def get_generic_price_data(symbol: str, interval: str = "1h", outputsize: int = 100):
    """Fetch price data for any TwelveData symbol (e.g. DXY, XAG/USD)."""
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={symbol}&interval={interval}&outputsize={outputsize}"
        f"&apikey={TWELVE_DATA_API_KEY}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
                if "values" not in data:
                    logger.warning(f"No data for {symbol}: {data.get('message', 'Unknown error')}")
                    return None
                df = pd.DataFrame(data["values"])
                for col in ["open", "high", "low", "close"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.sort_index(ascending=False).reset_index(drop=True)
                return df
    except Exception as e:
        logger.error(f"Error fetching generic symbol {symbol}: {e}")
        return None


# ============================================================
# HURST EXPONENT
# ============================================================
def calculate_hurst_exponent(prices: np.ndarray) -> float:
    """
    Compute Hurst exponent via R/S analysis.
    H > 0.55 → TRENDING
    H < 0.45 → MEAN-REVERTING
    else     → RANDOM WALK
    """
    try:
        n = len(prices)
        if n < 20:
            return 0.5
        lags = range(2, min(n // 2, 20))
        rs_values = []
        for lag in lags:
            sub = prices[:lag]
            mean = np.mean(sub)
            deviations = np.cumsum(sub - mean)
            r = np.max(deviations) - np.min(deviations)
            s = np.std(sub, ddof=1)
            if s > 0:
                rs_values.append(np.log(r / s))
        if len(rs_values) < 2:
            return 0.5
        log_lags = np.log(list(lags[: len(rs_values)]))
        hurst = float(np.polyfit(log_lags, rs_values, 1)[0])
        return round(max(0.0, min(1.0, hurst)), 4)
    except Exception as e:
        logger.error(f"Hurst exponent error: {e}")
        return 0.5


def get_hurst_regime(h: float) -> str:
    if h > 0.55:
        return "TRENDING"
    elif h < 0.45:
        return "MEAN-REVERTING"
    return "RANDOM_WALK"


# ============================================================
# SHANNON ENTROPY FILTER
# ============================================================
def calculate_shannon_entropy(prices: np.ndarray, bins: int = 10) -> float:
    """
    Compute Shannon entropy ratio (0–1) of price returns.
    High entropy (> 0.85) = chaotic/disordered price action → block signal.
    """
    try:
        if len(prices) < 10:
            return 0.5
        returns = np.diff(prices) / np.where(prices[:-1] != 0, prices[:-1], 1e-9)
        counts, _ = np.histogram(returns, bins=bins)
        counts = counts[counts > 0]
        probs = counts / counts.sum()
        entropy = -np.sum(probs * np.log2(probs))
        max_entropy = np.log2(bins)
        return round(float(entropy / max_entropy), 4) if max_entropy > 0 else 0.5
    except Exception as e:
        logger.error(f"Shannon entropy error: {e}")
        return 0.5


# ============================================================
# KELTNER CHANNELS
# ============================================================
def calculate_keltner_channels(df: pd.DataFrame, atr_mult: float = 2.0) -> dict:
    """
    EMA(20) ± ATR(14) × 2.0
    Returns position ratio (0–1) and overextension flag.
    """
    try:
        close = df["close"]
        high = df["high"]
        low = df["low"]
        ema20 = ta.trend.EMAIndicator(close, window=20).ema_indicator()
        atr = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()
        upper = ema20 + atr * atr_mult
        lower = ema20 - atr * atr_mult
        latest_close = float(close.iloc[-1])
        latest_upper = float(upper.iloc[-1])
        latest_lower = float(lower.iloc[-1])
        band_range = latest_upper - latest_lower
        position = (latest_close - latest_lower) / band_range if band_range > 0 else 0.5
        return {
            "kc_upper": round(latest_upper, 2),
            "kc_lower": round(latest_lower, 2),
            "kc_position": round(position, 4),
            "extended_high": position > 0.95,
            "extended_low": position < 0.05,
        }
    except Exception as e:
        logger.error(f"Keltner channel error: {e}")
        return {"kc_upper": 0, "kc_lower": 0, "kc_position": 0.5, "extended_high": False, "extended_low": False}


# ============================================================
# LIQUIDITY SWEEP DETECTION
# ============================================================
def detect_liquidity_sweep(df: pd.DataFrame, lookback: int = 20) -> dict:
    """
    Detect stop hunts: price sweeps above recent high or below recent low
    then reverses. Informational — not blocking.
    """
    try:
        if len(df) < lookback + 2:
            return {"sweep_detected": False, "sweep_type": None, "pressure": "NEUTRAL"}
        recent = df.iloc[-(lookback + 1):-1]
        current = df.iloc[-1]
        prev_high = float(recent["high"].max())
        prev_low = float(recent["low"].min())
        c_high = float(current["high"])
        c_low = float(current["low"])
        c_close = float(current["close"])
        c_open = float(current["open"])

        bullish_sweep = c_low < prev_low and c_close > c_open  # swept lows, closed bullish
        bearish_sweep = c_high > prev_high and c_close < c_open  # swept highs, closed bearish

        if bullish_sweep:
            return {"sweep_detected": True, "sweep_type": "BULLISH_SWEEP", "pressure": "BULLISH"}
        if bearish_sweep:
            return {"sweep_detected": True, "sweep_type": "BEARISH_SWEEP", "pressure": "BEARISH"}
        return {"sweep_detected": False, "sweep_type": None, "pressure": "NEUTRAL"}
    except Exception as e:
        logger.error(f"Liquidity sweep error: {e}")
        return {"sweep_detected": False, "sweep_type": None, "pressure": "NEUTRAL"}


# ============================================================
# GOLD-SILVER RATIO (GSR)
# ============================================================
async def get_gold_silver_ratio(gold_price: float) -> dict:
    """
    Fetch XAG/USD and compute GSR = Gold / Silver.
    GSR > 80 = extreme (reduces conviction).
    """
    try:
        df = await get_generic_price_data("XAG/USD", interval="1h", outputsize=5)
        if df is None or len(df) < 1:
            return {"gsr": None, "gsr_extreme": False, "silver_price": None}
        silver_price = float(df["close"].iloc[-1])
        gsr = round(gold_price / silver_price, 2) if silver_price > 0 else None
        return {
            "gsr": gsr,
            "gsr_extreme": gsr is not None and gsr > 80,
            "silver_price": round(silver_price, 4),
        }
    except Exception as e:
        logger.error(f"GSR fetch error: {e}")
        return {"gsr": None, "gsr_extreme": False, "silver_price": None}


# ============================================================
# VOLUME PROFILE POC
# ============================================================
def calculate_volume_profile(df: pd.DataFrame, bins: int = 20) -> dict:
    """
    Approximate volume profile using price histogram (no tick volume).
    Returns POC (Point of Control) and Value Area (VA Low / VA High).
    """
    try:
        if len(df) < 10:
            return {"poc": None, "va_low": None, "va_high": None}
        prices = df["close"].dropna().values
        counts, edges = np.histogram(prices, bins=bins)
        poc_idx = int(np.argmax(counts))
        poc = round(float((edges[poc_idx] + edges[poc_idx + 1]) / 2), 2)

        # Value Area = 70% of total volume around POC
        total = counts.sum()
        target = total * 0.70
        accumulated = counts[poc_idx]
        lo_idx, hi_idx = poc_idx, poc_idx
        while accumulated < target:
            lo_expand = counts[lo_idx - 1] if lo_idx > 0 else 0
            hi_expand = counts[hi_idx + 1] if hi_idx < len(counts) - 1 else 0
            if lo_expand >= hi_expand and lo_idx > 0:
                lo_idx -= 1
                accumulated += lo_expand
            elif hi_idx < len(counts) - 1:
                hi_idx += 1
                accumulated += hi_expand
            else:
                break

        va_low = round(float(edges[lo_idx]), 2)
        va_high = round(float(edges[hi_idx + 1]), 2)
        return {"poc": poc, "va_low": va_low, "va_high": va_high}
    except Exception as e:
        logger.error(f"Volume profile error: {e}")
        return {"poc": None, "va_low": None, "va_high": None}


# ============================================================
# KELLY CRITERION
# ============================================================
async def calculate_kelly_fraction(pair: str) -> dict:
    """
    Analyse last 50 closed trades for the pair.
    Returns Kelly fraction (capped at 2% risk).
    """
    try:
        trades = await db.gold_signals.find(
            {"pair": pair, "status": {"$in": ["CLOSED_TP1", "CLOSED_TP2", "CLOSED_TP3", "CLOSED_SL"]}},
            {"result": 1, "pips": 1},
        ).sort("created_at", -1).limit(50).to_list(50)

        if len(trades) < 5:
            return {"kelly_fraction": 0.01, "win_rate": None, "avg_win": None, "avg_loss": None, "sample_size": len(trades)}

        wins = [t for t in trades if t.get("result") == "WIN"]
        losses = [t for t in trades if t.get("result") == "LOSS"]
        win_rate = len(wins) / len(trades)
        avg_win = float(np.mean([t.get("pips", 0) for t in wins])) if wins else 0
        avg_loss = abs(float(np.mean([t.get("pips", 0) for t in losses]))) if losses else 1

        if avg_loss == 0:
            avg_loss = 1.0
        b = avg_win / avg_loss  # win/loss ratio
        kelly = win_rate - (1 - win_rate) / b if b > 0 else 0
        kelly = max(0.0, min(kelly, 0.02))  # cap at 2%

        return {
            "kelly_fraction": round(kelly, 4),
            "win_rate": round(win_rate, 4),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "sample_size": len(trades),
        }
    except Exception as e:
        logger.error(f"Kelly criterion error: {e}")
        return {"kelly_fraction": 0.01, "win_rate": None, "avg_win": None, "avg_loss": None, "sample_size": 0}


# ============================================================
# CIRCUIT BREAKER
# ============================================================
async def check_circuit_breaker(pair: str) -> dict:
    """
    Detect flash crashes: >$15 move in 2 minutes.
    Pauses trading for 30 minutes.
    Gate 06: CIRCUIT_BREAKER
    """
    global _circuit_breaker_until
    try:
        if _circuit_breaker_until and datetime.now(timezone.utc) < _circuit_breaker_until:
            remaining = int((_circuit_breaker_until - datetime.now(timezone.utc)).total_seconds() / 60)
            return {"triggered": True, "reason": f"Circuit breaker active ({remaining} min remaining)"}

        df = await get_price_data(pair, interval="1min", outputsize=5)
        if df is None or len(df) < 3:
            return {"triggered": False, "reason": "Insufficient data"}

        recent_high = float(df["high"].iloc[:3].max())
        recent_low = float(df["low"].iloc[:3].min())
        move = recent_high - recent_low

        if move > 15.0:
            _circuit_breaker_until = datetime.now(timezone.utc) + timedelta(minutes=30)
            logger.warning(f"⚡ Circuit breaker triggered: ${move:.2f} move in 2 min")
            await _send_admin_alert(
                f"⚡ CIRCUIT BREAKER TRIGGERED — {pair}\n"
                f"Move: ${move:.2f} in 2 min\nTrading paused 30 minutes."
            )
            return {"triggered": True, "reason": f"Flash crash detected: ${move:.2f} move"}

        return {"triggered": False, "reason": "Clear"}
    except Exception as e:
        logger.error(f"Circuit breaker error: {e}")
        return {"triggered": False, "reason": "Error (pass-through)"}


# ============================================================
# SESSION FILTER
# ============================================================
def get_session_confidence(hour_utc: int) -> dict:
    """Return current session name and minimum confidence requirement."""
    for session, cfg in SESSION_CONFIDENCE.items():
        h_start, h_end = cfg["hours"]
        if h_start <= hour_utc < h_end:
            return {"session": session, "min_score": cfg["min_score"]}
    return {"session": "DEAD_ZONE", "min_score": 85}


# ============================================================
# DYNAMIC ATR SETTINGS
# ============================================================
def get_dynamic_atr_settings(
    volatility_class: str,
    hurst: float,
    hour_utc: int,
) -> dict:
    """
    Adjust TP/SL multipliers based on:
    - Volatility class (LOW/MEDIUM/HIGH/EXTREME)
    - Hurst exponent (trending = wider TP3, mean-reverting = tighter TP1)
    - Session (Asian = tighter, London/NY overlap = wider)
    """
    base = {
        "LOW":     {"sl": 1.2, "tp1": 0.8,  "tp2": 1.6, "tp3": 2.4},
        "MEDIUM":  {"sl": 1.5, "tp1": 1.0,  "tp2": 2.0, "tp3": 3.0},
        "HIGH":    {"sl": 2.0, "tp1": 1.2,  "tp2": 2.5, "tp3": 4.0},
        "EXTREME": {"sl": 2.5, "tp1": 1.5,  "tp2": 3.0, "tp3": 5.0},
    }.get(volatility_class, {"sl": 1.5, "tp1": 1.0, "tp2": 2.0, "tp3": 3.0})

    # Hurst adjustment
    if hurst > 0.55:  # TRENDING — wider TP3
        base["tp3"] = round(base["tp3"] * 1.25, 2)
    elif hurst < 0.45:  # MEAN-REVERTING — tighter TP1
        base["tp1"] = round(base["tp1"] * 0.75, 2)

    # Session adjustment
    if 12 <= hour_utc < 16:  # London/NY overlap — wider
        base["tp2"] = round(base["tp2"] * 1.10, 2)
        base["tp3"] = round(base["tp3"] * 1.10, 2)
    elif 0 <= hour_utc < 7:  # Asian — tighter
        base["tp1"] = round(base["tp1"] * 0.90, 2)
        base["tp2"] = round(base["tp2"] * 0.90, 2)

    return base


# ============================================================
# CORRELATION MATRIX MONITOR
# ============================================================
async def monitor_correlation_matrix():
    """
    Monitor XAUUSD vs DXY correlation.
    Auto-disables gate_03 if correlation breaks (> -0.3).
    Auto-enables when restored.
    """
    try:
        xau_df = await get_price_data("XAUUSD", interval="1h", outputsize=50)
        dxy_df = await get_generic_price_data("DXY", interval="1h", outputsize=50)
        if xau_df is None or dxy_df is None:
            return

        min_len = min(len(xau_df), len(dxy_df))
        xau_close = xau_df["close"].iloc[:min_len].values.astype(float)
        dxy_close = dxy_df["close"].iloc[:min_len].values.astype(float)
        corr = float(np.corrcoef(xau_close, dxy_close)[0, 1])

        if corr > -0.3:
            if GATE_CONFIG["gate_03_dxy_correlation"]["enabled"]:
                GATE_CONFIG["gate_03_dxy_correlation"]["enabled"] = False
                logger.warning(f"📊 Correlation matrix: DXY correlation broken ({corr:.3f}) — gate_03 DISABLED")
                await _send_admin_alert(
                    f"📊 Correlation Monitor: XAUUSD/DXY correlation = {corr:.3f}\n"
                    "Correlation broken (> -0.3). Gate 03 (DXY) AUTO-DISABLED."
                )
        else:
            if not GATE_CONFIG["gate_03_dxy_correlation"]["enabled"]:
                GATE_CONFIG["gate_03_dxy_correlation"]["enabled"] = True
                logger.info(f"📊 Correlation matrix: DXY correlation restored ({corr:.3f}) — gate_03 ENABLED")
                await _send_admin_alert(
                    f"📊 Correlation Monitor: XAUUSD/DXY correlation = {corr:.3f}\n"
                    "Correlation restored. Gate 03 (DXY) AUTO-ENABLED."
                )
    except Exception as e:
        logger.error(f"Correlation matrix monitor error: {e}")


# ============================================================
# INDICATORS (ENHANCED)
# ============================================================
def calculate_indicators(df: pd.DataFrame, params: dict) -> dict | None:
    """
    Calculate all technical indicators with G1/G2/G3 scoring,
    alignment percentage, hold logic, Hurst integration, and regime detection.
    """
    try:
        close = df["close"]
        high = df["high"]
        low = df["low"]

        # ── Core indicators ──────────────────────────────────────────────────
        df["rsi"] = ta.momentum.RSIIndicator(close, window=14).rsi()
        macd_ind = ta.trend.MACD(close)
        df["macd"] = macd_ind.macd()
        df["macd_signal"] = macd_ind.macd_signal()
        df["ma_20"] = ta.trend.SMAIndicator(close, window=20).sma_indicator()
        df["ma_50"] = ta.trend.SMAIndicator(close, window=50).sma_indicator()
        bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        df["bb_upper"] = bb.bollinger_hband()
        df["bb_lower"] = bb.bollinger_lband()
        atr_ind = ta.volatility.AverageTrueRange(high, low, close, window=14)
        df["atr"] = atr_ind.average_true_range()

        # ADX(14)
        adx_ind = ta.trend.ADXIndicator(high, low, close, window=14)
        df["adx"] = adx_ind.adx()

        # Stochastic(9,6)
        stoch_ind = ta.momentum.StochasticOscillator(high, low, close, window=9, smooth_window=6)
        df["stoch_k"] = stoch_ind.stoch()
        df["stoch_d"] = stoch_ind.stoch_signal()

        # StochRSI(14)
        stochrsi_ind = ta.momentum.StochRSIIndicator(close, window=14, smooth1=3, smooth2=3)
        df["stochrsi_k"] = stochrsi_ind.stochrsi_k()
        df["stochrsi_d"] = stochrsi_ind.stochrsi_d()

        # CCI(14)
        cci_ind = ta.trend.CCIIndicator(high, low, close, window=14)
        df["cci"] = cci_ind.cci()

        # Williams%R(14)
        wr_ind = ta.momentum.WilliamsRIndicator(high, low, close, lbp=14)
        df["williams_r"] = wr_ind.williams_r()

        latest = df.iloc[-1]
        dp = params["decimal_places"]

        # ── G1: Trend score (40%) ────────────────────────────────────────────
        g1 = 0
        trend = "BULLISH" if float(latest["close"]) > float(latest["ma_50"]) else "BEARISH"
        if trend == "BULLISH":
            g1 += 50
        adx_val = float(latest["adx"])
        if adx_val > 25:
            g1 += 30
        elif adx_val > 20:
            g1 += 15
        if float(latest["macd"]) > float(latest["macd_signal"]):
            g1 += 20
        g1 = min(g1, 100)

        # ── G2: Momentum score (30%) ─────────────────────────────────────────
        g2 = 0
        rsi_val = float(latest["rsi"])
        if 40 <= rsi_val <= 60:
            g2 += 40
        elif 30 <= rsi_val < 40 or 60 < rsi_val <= 70:
            g2 += 60
        else:
            g2 += 20
        macd_hist = float(latest["macd"]) - float(latest["macd_signal"])
        if macd_hist > 0:
            g2 += 30
        elif macd_hist > -0.5:
            g2 += 15
        cci_val = float(latest["cci"])
        if -100 <= cci_val <= 100:
            g2 += 30
        elif -200 <= cci_val <= 200:
            g2 += 15
        g2 = min(g2, 100)

        # ── G3: Trigger score (30%) ──────────────────────────────────────────
        g3 = 0
        stoch_k_val = float(latest["stoch_k"])
        wr_val = float(latest["williams_r"])
        if 20 < stoch_k_val < 80:
            g3 += 40
        else:
            g3 += 20
        if -80 < wr_val < -20:
            g3 += 40
        else:
            g3 += 20
        g3 = min(g3, 100)

        weighted_score = round(g1 * 0.40 + g2 * 0.30 + g3 * 0.30, 1)
        alignment_pct = round((g1 + g2 + g3) / 3, 1)

        # ── Hurst exponent ───────────────────────────────────────────────────
        close_arr = close.dropna().values.astype(float)
        hurst = calculate_hurst_exponent(close_arr[-50:] if len(close_arr) >= 50 else close_arr)
        hurst_regime = get_hurst_regime(hurst)

        # ── Hold logic: suppress counter-trend G3 triggers in strong trends ──
        hold_signal = False
        if adx_val > 30 and hurst > 0.60:
            # Strong trend — suppress G3 reversals
            if trend == "BULLISH" and (stoch_k_val >= 80 or wr_val >= -20):
                hold_signal = True  # overbought in strong uptrend — hold, don't sell
            elif trend == "BEARISH" and (stoch_k_val <= 20 or wr_val <= -80):
                hold_signal = True  # oversold in strong downtrend — hold, don't buy

        # ── Regime detection ─────────────────────────────────────────────────
        if adx_val > 25:
            regime = "UPTREND" if trend == "BULLISH" else "DOWNTREND"
        else:
            regime = "RANGE"

        return {
            "current_price": round(float(latest["close"]), dp),
            "rsi": round(rsi_val, 2),
            "macd": float(latest["macd"]),
            "macd_signal": float(latest["macd_signal"]),
            "ma_20": round(float(latest["ma_20"]), dp),
            "ma_50": round(float(latest["ma_50"]), dp),
            "bb_upper": round(float(latest["bb_upper"]), dp),
            "bb_lower": round(float(latest["bb_lower"]), dp),
            "atr": round(float(latest["atr"]), dp),
            "trend": trend,
            "adx": round(adx_val, 2),
            "stoch_k": round(stoch_k_val, 2),
            "stoch_d": round(float(latest["stoch_d"]), 2),
            "stochrsi_k": round(float(latest["stochrsi_k"]) * 100, 2),
            "stochrsi_d": round(float(latest["stochrsi_d"]) * 100, 2),
            "cci": round(cci_val, 2),
            "williams_r": round(wr_val, 2),
            # Enhanced
            "g1_trend_score": g1,
            "g2_momentum_score": g2,
            "g3_trigger_score": g3,
            "weighted_score": weighted_score,
            "alignment_pct": alignment_pct,
            "hurst": hurst,
            "hurst_regime": hurst_regime,
            "hold_signal": hold_signal,
            "regime": regime,
        }
    except Exception as e:
        logger.error(f"Indicator calc error: {e}")
        return None


# ============================================================
# ALIGNMENT SCORE
# ============================================================
def calculate_alignment_score(indicators: dict) -> dict:
    try:
        wr = indicators.get("williams_r", -50.0)
        srsi_k = indicators.get("stochrsi_k", 50.0)

        if wr <= -80:
            wr_bias = "BULLISH"
        elif wr >= -20:
            wr_bias = "BEARISH"
        else:
            wr_bias = "NEUTRAL"

        if srsi_k <= 20:
            srsi_bias = "BULLISH"
        elif srsi_k >= 80:
            srsi_bias = "BEARISH"
        else:
            srsi_bias = "NEUTRAL"

        if wr_bias == "NEUTRAL" and srsi_bias == "NEUTRAL":
            alignment_score, confidence_boost, aligned = 50.0, 0.0, False
        elif wr_bias == srsi_bias and wr_bias != "NEUTRAL":
            alignment_score, confidence_boost, aligned = 100.0, 20.0, True
        elif wr_bias != srsi_bias and "NEUTRAL" not in (wr_bias, srsi_bias):
            alignment_score, confidence_boost, aligned = 0.0, 0.0, False
        else:
            alignment_score, confidence_boost, aligned = 50.0, 5.0, False

        return {
            "alignment_score": alignment_score,
            "confidence_boost": confidence_boost,
            "wr_bias": wr_bias,
            "srsi_bias": srsi_bias,
            "aligned": aligned,
        }
    except Exception as e:
        logger.error(f"Alignment score error: {e}")
        return {"alignment_score": 50.0, "confidence_boost": 0.0, "wr_bias": "NEUTRAL", "srsi_bias": "NEUTRAL", "aligned": False}


# ============================================================
# H4 TREND CHECK
# ============================================================
async def check_h4_trend(pair: str) -> dict:
    try:
        df = await get_price_data(pair, interval="4h", outputsize=60)
        if df is None or len(df) < 51:
            return {"h4_trend": "UNKNOWN", "buy_allowed": True, "sell_allowed": True}
        ma50 = ta.trend.SMAIndicator(df["close"], window=50).sma_indicator()
        latest_close = float(df["close"].iloc[-1])
        latest_ma50 = float(ma50.iloc[-1])
        h4_trend = "BULLISH" if latest_close > latest_ma50 else "BEARISH"
        logger.info(f"H4 trend for {pair}: {h4_trend}")
        return {"h4_trend": h4_trend, "buy_allowed": h4_trend == "BULLISH", "sell_allowed": h4_trend == "BEARISH"}
    except Exception as e:
        logger.error(f"H4 trend check error for {pair}: {e}")
        return {"h4_trend": "UNKNOWN", "buy_allowed": True, "sell_allowed": True}


# ============================================================
# DXY CORRELATION
# ============================================================
async def check_dxy_correlation() -> dict:
    try:
        df = await get_generic_price_data("DXY", interval="1h", outputsize=30)
        if df is None or len(df) < 21:
            return {"dxy_trend": "UNKNOWN", "buy_allowed": True, "dxy_ma20": None, "dxy_price": None}
        ma20 = ta.trend.SMAIndicator(df["close"], window=20).sma_indicator()
        latest_close = float(df["close"].iloc[-1])
        latest_ma20 = float(ma20.iloc[-1])
        if latest_close > latest_ma20 * 1.002:
            dxy_trend, buy_allowed = "UPTREND", False
        elif latest_close < latest_ma20 * 0.998:
            dxy_trend, buy_allowed = "DOWNTREND", True
        else:
            dxy_trend, buy_allowed = "NEUTRAL", True
        logger.info(f"DXY: {dxy_trend}")
        return {"dxy_trend": dxy_trend, "buy_allowed": buy_allowed, "dxy_ma20": round(latest_ma20, 3), "dxy_price": round(latest_close, 3)}
    except Exception as e:
        logger.error(f"DXY correlation check error: {e}")
        return {"dxy_trend": "UNKNOWN", "buy_allowed": True, "dxy_ma20": None, "dxy_price": None}


# ============================================================
# NEWS GUARD
# ============================================================
async def check_news_impact(symbol: str = "XAU/USD") -> dict:
    try:
        url = f"https://api.twelvedata.com/news?symbol={symbol}&apikey={TWELVE_DATA_API_KEY}&outputsize=20"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
        articles = data.get("data", []) if isinstance(data, dict) else []
        if not articles:
            return {"news_nearby": False, "signal_allowed": True, "nearest_event": None, "minutes_away": None}
        now_utc = datetime.now(timezone.utc)
        nearest_event, min_minutes = None, None
        for article in articles:
            pub_str = article.get("datetime") or article.get("published_at") or ""
            if not pub_str:
                continue
            try:
                pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                diff = abs((pub_dt - now_utc).total_seconds() / 60)
                if diff <= 60 and (min_minutes is None or diff < min_minutes):
                    min_minutes = int(diff)
                    nearest_event = article.get("title", "Unknown news event")
            except Exception:
                continue
        news_nearby = min_minutes is not None
        if news_nearby:
            logger.warning(f"⚠️ News guard: '{nearest_event}' ({min_minutes} min away)")
        return {"news_nearby": news_nearby, "signal_allowed": not news_nearby, "nearest_event": nearest_event, "minutes_away": min_minutes}
    except Exception as e:
        logger.error(f"News impact check error: {e}")
        return {"news_nearby": False, "signal_allowed": True, "nearest_event": None, "minutes_away": None}


# ============================================================
# WEIGHTED CONFIDENCE
# ============================================================
def calculate_weighted_confidence(indicators: dict, alignment: dict) -> dict:
    try:
        g1 = indicators.get("g1_trend_score", 50)
        g2 = indicators.get("g2_momentum_score", 50)
        g3 = indicators.get("g3_trigger_score", 50) + alignment.get("confidence_boost", 0)
        g3 = min(g3, 100)
        weighted_score = round(g1 * 0.40 + g2 * 0.30 + g3 * 0.30, 1)
        if weighted_score >= 85:
            conviction_level = "HIGH"
        elif weighted_score >= 60:
            conviction_level = "MEDIUM"
        else:
            conviction_level = "LOW"
        return {
            "trend_score": float(g1),
            "momentum_score": float(g2),
            "trigger_score": float(g3),
            "weighted_score": weighted_score,
            "conviction_level": conviction_level,
        }
    except Exception as e:
        logger.error(f"Weighted confidence error: {e}")
        return {"trend_score": 50.0, "momentum_score": 50.0, "trigger_score": 50.0, "weighted_score": 50.0, "conviction_level": "LOW"}


# ============================================================
# CANDLESTICK PATTERNS
# ============================================================
def detect_candlestick_patterns(df: pd.DataFrame) -> dict:
    try:
        if len(df) < 2:
            return {"pattern": "NONE", "pattern_strength": "WEAK", "bullish": None}
        c1, c0 = df.iloc[-2], df.iloc[-1]
        o1, h1, l1, cl1 = float(c1["open"]), float(c1["high"]), float(c1["low"]), float(c1["close"])
        o0, h0, l0, cl0 = float(c0["open"]), float(c0["high"]), float(c0["low"]), float(c0["close"])
        body0 = abs(cl0 - o0)
        range0 = h0 - l0 if h0 != l0 else 1e-9
        if body0 / range0 < 0.1:
            return {"pattern": "DOJI", "pattern_strength": "MODERATE", "bullish": None}
        if (cl1 < o1) and (cl0 > o0) and (cl0 > o1) and (o0 < cl1):
            return {"pattern": "ENGULFING", "pattern_strength": "STRONG", "bullish": True}
        if (cl1 > o1) and (cl0 < o0) and (cl0 < o1) and (o0 > cl1):
            return {"pattern": "ENGULFING", "pattern_strength": "STRONG", "bullish": False}
        upper_wick = h0 - max(o0, cl0)
        lower_wick = min(o0, cl0) - l0
        if lower_wick >= 2 * body0 and upper_wick <= 0.3 * range0:
            return {"pattern": "PIN_BAR", "pattern_strength": "STRONG", "bullish": True}
        if upper_wick >= 2 * body0 and lower_wick <= 0.3 * range0:
            return {"pattern": "PIN_BAR", "pattern_strength": "STRONG", "bullish": False}
        return {"pattern": "NONE", "pattern_strength": "WEAK", "bullish": None}
    except Exception as e:
        logger.error(f"Candlestick pattern error: {e}")
        return {"pattern": "NONE", "pattern_strength": "WEAK", "bullish": None}


# ============================================================
# SAFETY SWITCH
# ============================================================
def apply_safety_switch(signal_type: str, indicators: dict, alignment: dict, weighted: dict) -> dict:
    try:
        trend_ok = weighted["trend_score"] >= 60
        momentum_ok = weighted["momentum_score"] >= 60
        stoch_k = indicators["stoch_k"]
        wr = indicators["williams_r"]
        if signal_type == "SELL" and trend_ok and momentum_ok and (stoch_k <= 20 or wr <= -80):
            return {"signal_allowed": False, "reason": "Safety switch: SELL but triggers oversold"}
        if signal_type == "BUY" and trend_ok and momentum_ok and (stoch_k >= 80 or wr >= -20):
            return {"signal_allowed": False, "reason": "Safety switch: BUY but triggers overbought"}
        return {"signal_allowed": True, "reason": "Safety switch: clear"}
    except Exception as e:
        logger.error(f"Safety switch error: {e}")
        return {"signal_allowed": True, "reason": "Safety switch: error (pass-through)"}


# ============================================================
# AI ANALYSIS
# ============================================================
async def generate_ai_analysis(symbol: str, indicators: dict, params: dict):
    try:
        alignment = calculate_alignment_score(indicators)
        h4_data = await check_h4_trend(symbol)
        dxy_data = await check_dxy_correlation()
        td_symbol = GOLD_PAIRS.get(symbol, {}).get("twelve_data_symbol", "XAU/USD")
        news_data = await check_news_impact(td_symbol)
        weighted = calculate_weighted_confidence(indicators, alignment)
        df_h1 = await get_price_data(symbol, interval="1h", outputsize=10)
        pattern_data = detect_candlestick_patterns(df_h1) if df_h1 is not None else {"pattern": "NONE", "pattern_strength": "WEAK", "bullish": None}

        dp = params["decimal_places"]
        system_message = (
            "You are an elite institutional gold trader. "
            "Provide precise, actionable trading signals with strict risk management. "
            "Consider all provided multi-indicator context carefully."
        )
        dxy_label = "NEUTRAL" if dxy_data["dxy_trend"] in ("NEUTRAL", "UNKNOWN") else dxy_data["dxy_trend"]
        news_label = "BLOCKED ⚠️" if news_data["news_nearby"] else "Clear ✅"
        pattern_label = (
            f"{pattern_data['pattern']} "
            f"({'Bullish' if pattern_data['bullish'] else 'Bearish' if pattern_data['bullish'] is False else 'Neutral'}) "
            f"[{pattern_data['pattern_strength']}]"
        )

        prompt = f"""
Analyze {symbol} market data and provide a professional trading signal.

=== CORE MARKET DATA (H1) ===
Current Price : {indicators['current_price']}
Trend (MA50)  : {indicators['trend']}
ATR(14)       : {indicators['atr']:.{dp}f}
BB Upper/Lower: {indicators['bb_upper']:.{dp}f} / {indicators['bb_lower']:.{dp}f}
Regime        : {indicators.get('regime', 'UNKNOWN')}
Hurst         : {indicators.get('hurst', 0.5):.4f} ({indicators.get('hurst_regime', 'RANDOM_WALK')})

=== INDICATORS ===
RSI(14)       : {indicators['rsi']:.2f}
MACD          : {indicators['macd']:.6f}  |  Signal: {indicators['macd_signal']:.6f}
MA20 / MA50   : {indicators['ma_20']:.{dp}f} / {indicators['ma_50']:.{dp}f}
ADX(14)       : {indicators['adx']:.2f}  {'(TRENDING)' if indicators['adx'] > 25 else '(WEAK TREND)'}
Stochastic(9,6): K={indicators['stoch_k']:.2f}  D={indicators['stoch_d']:.2f}
StochRSI(14)  : K={indicators['stochrsi_k']:.2f}  D={indicators['stochrsi_d']:.2f}
CCI(14)       : {indicators['cci']:.2f}
Williams%R(14): {indicators['williams_r']:.2f}

=== SCORING (G1/G2/G3 — 40/30/30) ===
G1 Trend      : {indicators.get('g1_trend_score', 50)}/100
G2 Momentum   : {indicators.get('g2_momentum_score', 50)}/100
G3 Triggers   : {indicators.get('g3_trigger_score', 50)}/100
Weighted Score: {indicators.get('weighted_score', 50)}/100
Alignment     : {indicators.get('alignment_pct', 50)}%

=== MULTI-TIMEFRAME (H4) ===
H4 Trend      : {h4_data['h4_trend']}
BUY Allowed   : {'YES' if h4_data['buy_allowed'] else 'NO — H4 BEARISH'}
SELL Allowed  : {'YES' if h4_data['sell_allowed'] else 'NO — H4 BULLISH'}

=== DXY CORRELATION ===
DXY Trend     : {dxy_label}
BUY Allowed   : {'YES' if dxy_data['buy_allowed'] else 'NO — DXY UPTREND'}

=== NEWS IMPACT ===
Status        : {news_label}
{f"Nearest Event : {news_data['nearest_event']} ({news_data['minutes_away']} min away)" if news_data['news_nearby'] else "No high-impact events within ±60 min"}

=== PRICE ACTION ===
Pattern       : {pattern_label}

=== ATR MULTIPLIERS ===
SL: {params['atr_multiplier_sl']} | TP1: {params['atr_multiplier_tp1']} | TP2: {params['atr_multiplier_tp2']} | TP3: {params['atr_multiplier_tp3']}
Min R:R: {params['min_rr']}

=== OUTPUT FORMAT (JSON ONLY) ===
{{"signal":"BUY"or"SELL"or"NEUTRAL","confidence":0-100,"entry_price":numeric,"tp_levels":[tp1,tp2,tp3],"sl_price":numeric,"analysis":"<150 words","risk_reward":numeric}}
RESPOND ONLY WITH VALID JSON. NO OTHER TEXT.
"""

        ai_response = None
        for attempt in range(3):
            try:
                response = await litellm.acompletion(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": prompt},
                    ],
                    api_key=OPENAI_API_KEY,
                )
                ai_response = response.choices[0].message.content
                if ai_response and len(ai_response.strip()) > 10:
                    break
            except Exception as e:
                logger.warning(f"LLM attempt {attempt+1}/3 for {symbol}: {e}")
                await asyncio.sleep(1)

        if not ai_response or len(ai_response.strip()) < 10:
            return None

        raw = ai_response.strip()
        fence_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', raw, re.DOTALL)
        if fence_match:
            raw = fence_match.group(1).strip()
        if not raw.startswith('{'):
            brace_match = re.search(r'\{.*\}', raw, re.DOTALL)
            if brace_match:
                raw = brace_match.group(0)

        ai_data = None
        for parse_attempt in range(3):
            try:
                if parse_attempt == 0:
                    ai_data = json.loads(raw)
                elif parse_attempt == 1:
                    fixed = re.sub(r',\s*}', '}', raw)
                    fixed = re.sub(r',\s*]', ']', fixed)
                    fixed = fixed.replace("'", '"')
                    ai_data = json.loads(fixed)
                else:
                    signal_m = re.search(r'"signal"\s*:\s*"(\w+)"', raw)
                    conf_m = re.search(r'"confidence"\s*:\s*([\d.]+)', raw)
                    entry_m = re.search(r'"entry_price"\s*:\s*([\d.]+)', raw)
                    analysis_m = re.search(r'"analysis"\s*:\s*"([^"]*)"', raw)
                    ai_data = {
                        "signal": signal_m.group(1) if signal_m else "NEUTRAL",
                        "confidence": float(conf_m.group(1)) if conf_m else 50.0,
                        "entry_price": float(entry_m.group(1)) if entry_m else indicators['current_price'],
                        "analysis": analysis_m.group(1) if analysis_m else "AI analysis unavailable",
                        "tp_levels": [], "sl_price": 0,
                    }
                break
            except Exception:
                if parse_attempt == 2:
                    logger.warning(f"All JSON parsing failed for {symbol}")

        if not ai_data:
            return None

        entry = ai_data.get("entry_price", indicators['current_price'])
        signal_type = ai_data.get("signal", "NEUTRAL")
        tp_levels = ai_data.get("tp_levels", [])
        atr = indicators["atr"]

        if signal_type != "NEUTRAL" and (len(tp_levels) != 3 or len(set(tp_levels)) != 3):
            if signal_type == "BUY":
                tp_levels = [
                    round(entry + atr * params["atr_multiplier_tp1"], dp),
                    round(entry + atr * params["atr_multiplier_tp2"], dp),
                    round(entry + atr * params["atr_multiplier_tp3"], dp),
                ]
            else:
                tp_levels = [
                    round(entry - atr * params["atr_multiplier_tp1"], dp),
                    round(entry - atr * params["atr_multiplier_tp2"], dp),
                    round(entry - atr * params["atr_multiplier_tp3"], dp),
                ]
            ai_data["tp_levels"] = tp_levels

        sl_price = ai_data.get("sl_price", 0)
        if signal_type == "BUY" and (sl_price >= entry or sl_price == 0):
            sl_price = round(entry - atr * params["atr_multiplier_sl"], dp)
        elif signal_type == "SELL" and (sl_price <= entry or sl_price == 0):
            sl_price = round(entry + atr * params["atr_multiplier_sl"], dp)
        ai_data["sl_price"] = sl_price

        risk_reward = ai_data.get("risk_reward", params["min_rr"])
        if not isinstance(risk_reward, (int, float)):
            risk_reward = params["min_rr"]
        ai_data["risk_reward"] = risk_reward

        ai_data["_alignment"] = alignment
        ai_data["_h4"] = h4_data
        ai_data["_dxy"] = dxy_data
        ai_data["_news"] = news_data
        ai_data["_weighted"] = weighted
        ai_data["_pattern"] = pattern_data

        return ai_data
    except Exception as e:
        logger.error(f"Error generating AI analysis for {symbol}: {e}")
        return None


# ============================================================
# TELEGRAM
# ============================================================
def sanitize_html(text: str) -> str:
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def send_signal_to_telegram(
    pair, signal_type, entry_price, tp_levels, sl_price,
    confidence, risk_reward, analysis,
    regime="SWING",
    conviction_level="MEDIUM",
    alignment_score=50.0,
    technical_score=50.0,
    h4_trend="UNKNOWN",
    dxy_status="NEUTRAL",
    news_status="Clear",
    kelly_fraction=0.01,
    hurst_regime="RANDOM_WALK",
    volatility_class="MEDIUM",
    session="UNKNOWN",
):
    try:
        if not TELEGRAM_BOT_TOKEN:
            logger.warning("Telegram bot token not configured")
            return
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        signal_emoji = "🟢" if signal_type == "BUY" else "🔴"
        action = signal_type.capitalize()
        conviction_tag = " [HIGH CONVICTION 🔥]" if conviction_level == "HIGH" else ""
        entry_lo = round(entry_price - 0.50, 2)
        entry_hi = round(entry_price + 0.50, 2)

        copier_message = (
            f"{signal_emoji} #{pair} [SWING]{conviction_tag}\n\n"
            f"{action} {entry_lo} - {entry_hi}\n\n"
            f"TP1: {tp_levels[0]}\n"
            f"TP2: {tp_levels[1]}\n"
            f"TP3: {tp_levels[2]}\n\n"
            f"SL: {sl_price}\n"
        )

        safe_analysis = sanitize_html(analysis)
        info_message = (
            f"<b>📊 R:R:</b> 1:{risk_reward}  "
            f"<b>⚡ AI Confidence:</b> {confidence}%\n"
            f"<b>🎯 Technical Score:</b> {technical_score:.0f}/100  "
            f"<b>🔗 Alignment:</b> {alignment_score:.0f}%\n"
            f"<b>📈 H4 Trend:</b> {h4_trend}  "
            f"<b>💵 DXY:</b> {dxy_status}  "
            f"<b>📰 News:</b> {news_status}\n"
            f"<b>🌊 Hurst:</b> {hurst_regime}  "
            f"<b>⚡ Volatility:</b> {volatility_class}  "
            f"<b>🕐 Session:</b> {session}\n"
            f"<b>💰 Kelly Size:</b> {kelly_fraction*100:.2f}% risk\n"
            f"<b>📝</b> {safe_analysis}\n"
            f"<i>⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
            f"| Grandcom Gold Elite v3</i>"
        )

        await bot.send_message(chat_id=TELEGRAM_GOLD_CHANNEL_ID, text=copier_message)
        await bot.send_message(chat_id=TELEGRAM_GOLD_CHANNEL_ID, text=info_message, parse_mode="HTML")
        logger.info(f"✅ Gold signal sent: {pair} {signal_type}")
    except Exception as e:
        logger.error(f"❌ Error sending gold signal to Telegram: {e}")


# ============================================================
# OUTCOME TRACKER
# ============================================================
async def check_all_gold_outcomes():
    """
    Monitor active signals for SL/TP hits.
    Updates signal status and records exit price, pips, result.
    """
    global _daily_loss_pips, _daily_loss_date
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if _daily_loss_date != today:
            _daily_loss_pips = 0.0
            _daily_loss_date = today

        active = await db.gold_signals.find({"status": "ACTIVE"}).to_list(100)
        for signal in active:
            pair = signal.get("pair", "XAUUSD")
            df = await get_price_data(pair, interval="1min", outputsize=5)
            if df is None or len(df) < 1:
                continue
            current_price = float(df["close"].iloc[-1])
            entry = float(signal.get("entry_price", current_price))
            sl = float(signal.get("sl_price", 0))
            tp_levels = signal.get("tp_levels", [])
            signal_type = signal.get("type", "BUY")
            sid = signal["_id"]

            if signal_type == "BUY":
                if sl > 0 and current_price <= sl:
                    pips = round(current_price - entry, 2)
                    await db.gold_signals.update_one(
                        {"_id": sid},
                        {"$set": {"status": "CLOSED_SL", "exit_price": current_price, "pips": pips, "result": "LOSS", "closed_at": datetime.now(timezone.utc)}},
                    )
                    _daily_loss_pips += abs(pips)
                    logger.info(f"📉 {pair} BUY SL hit @ {current_price}")
                elif tp_levels and current_price >= float(tp_levels[-1]):
                    pips = round(current_price - entry, 2)
                    await db.gold_signals.update_one(
                        {"_id": sid},
                        {"$set": {"status": "CLOSED_TP3", "exit_price": current_price, "pips": pips, "result": "WIN", "closed_at": datetime.now(timezone.utc)}},
                    )
                    logger.info(f"🎯 {pair} BUY TP3 hit @ {current_price}")
                elif tp_levels and current_price >= float(tp_levels[1]):
                    await db.gold_signals.update_one({"_id": sid}, {"$set": {"status": "CLOSED_TP2"}})
                elif tp_levels and current_price >= float(tp_levels[0]):
                    await db.gold_signals.update_one({"_id": sid}, {"$set": {"status": "CLOSED_TP1"}})
            else:  # SELL
                if sl > 0 and current_price >= sl:
                    pips = round(entry - current_price, 2)
                    await db.gold_signals.update_one(
                        {"_id": sid},
                        {"$set": {"status": "CLOSED_SL", "exit_price": current_price, "pips": pips, "result": "LOSS", "closed_at": datetime.now(timezone.utc)}},
                    )
                    _daily_loss_pips += abs(pips)
                    logger.info(f"📉 {pair} SELL SL hit @ {current_price}")
                elif tp_levels and current_price <= float(tp_levels[-1]):
                    pips = round(entry - current_price, 2)
                    await db.gold_signals.update_one(
                        {"_id": sid},
                        {"$set": {"status": "CLOSED_TP3", "exit_price": current_price, "pips": pips, "result": "WIN", "closed_at": datetime.now(timezone.utc)}},
                    )
                    logger.info(f"🎯 {pair} SELL TP3 hit @ {current_price}")
                elif tp_levels and current_price <= float(tp_levels[1]):
                    await db.gold_signals.update_one({"_id": sid}, {"$set": {"status": "CLOSED_TP2"}})
                elif tp_levels and current_price <= float(tp_levels[0]):
                    await db.gold_signals.update_one({"_id": sid}, {"$set": {"status": "CLOSED_TP1"}})
    except Exception as e:
        logger.error(f"Outcome tracker error: {e}")


# ============================================================
# BREAKEVEN MONITOR
# ============================================================
async def check_breakeven():
    """
    Monitor active signals for TP1 hit → move SL to entry (breakeven).
    """
    try:
        tp1_hit = await db.gold_signals.find(
            {"status": "CLOSED_TP1", "breakeven_set": {"$ne": True}}
        ).to_list(50)
        for signal in tp1_hit:
            entry = float(signal.get("entry_price", 0))
            sid = signal["_id"]
            pair = signal.get("pair", "XAUUSD")
            await db.gold_signals.update_one(
                {"_id": sid},
                {"$set": {"sl_price": entry, "breakeven_set": True}},
            )
            logger.info(f"🔒 Breakeven set for {pair} signal — SL moved to {entry}")
            await _send_admin_alert(
                f"🔒 BREAKEVEN SET — {pair}\n"
                f"TP1 hit. SL moved to entry: {entry}"
            )
    except Exception as e:
        logger.error(f"Breakeven monitor error: {e}")


# ============================================================
# TRAILING STOP MONITOR
# ============================================================
async def update_trailing_stops():
    """
    For signals with breakeven set, trail SL by ATR × 2.5.
    Only moves SL in profit direction.
    """
    try:
        be_signals = await db.gold_signals.find(
            {"breakeven_set": True, "status": {"$in": ["CLOSED_TP1", "CLOSED_TP2", "ACTIVE"]}}
        ).to_list(50)
        for signal in be_signals:
            pair = signal.get("pair", "XAUUSD")
            df = await get_price_data(pair, interval="1h", outputsize=20)
            if df is None or len(df) < 15:
                continue
            atr = float(ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range().iloc[-1])
            current_price = float(df["close"].iloc[-1])
            signal_type = signal.get("type", "BUY")
            current_sl = float(signal.get("sl_price", 0))
            sid = signal["_id"]

            if signal_type == "BUY":
                new_sl = round(current_price - atr * 2.5, 2)
                if new_sl > current_sl:
                    await db.gold_signals.update_one({"_id": sid}, {"$set": {"sl_price": new_sl}})
                    logger.info(f"📈 Trailing stop updated for {pair} BUY: {current_sl} → {new_sl}")
            else:
                new_sl = round(current_price + atr * 2.5, 2)
                if new_sl < current_sl or current_sl == 0:
                    await db.gold_signals.update_one({"_id": sid}, {"$set": {"sl_price": new_sl}})
                    logger.info(f"📉 Trailing stop updated for {pair} SELL: {current_sl} → {new_sl}")
    except Exception as e:
        logger.error(f"Trailing stop monitor error: {e}")


# ============================================================
# DAILY INTELLIGENCE REPORT
# ============================================================
async def send_daily_intelligence_report():
    """Send comprehensive daily report at 07:00 UTC."""
    try:
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=24)
        signals = await db.gold_signals.find(
            {"created_at": {"$gte": since}}
        ).to_list(500)

        total = len(signals)
        wins = sum(1 for s in signals if s.get("result") == "WIN")
        losses = sum(1 for s in signals if s.get("result") == "LOSS")
        win_rate = round(wins / total * 100, 1) if total > 0 else 0
        net_pips = sum(s.get("pips", 0) for s in signals if s.get("pips") is not None)

        be_count = await db.gold_signals.count_documents({"breakeven_set": True, "created_at": {"$gte": since}})
        trail_count = await db.gold_signals.count_documents({"breakeven_set": True, "status": {"$in": ["CLOSED_TP1", "CLOSED_TP2"]}})

        disabled_gates = [k for k, v in GATE_CONFIG.items() if not v["enabled"]]

        dna_lines = []
        for pair in GOLD_PAIRS:
            dna = await get_market_dna(pair)
            dna_lines.append(
                f"  {pair}: ATR={dna.atr_14:.2f} | Vol={dna.volatility_class} | Spread={dna.avg_spread:.2f}"
            )

        kelly_lines = []
        for pair in GOLD_PAIRS:
            k = await calculate_kelly_fraction(pair)
            kelly_lines.append(f"  {pair}: {k['kelly_fraction']*100:.2f}% risk (WR={k['win_rate']})")

        report = (
            f"📊 <b>GRANDCOM GOLD — DAILY INTELLIGENCE REPORT</b>\n"
            f"<i>{now.strftime('%Y-%m-%d %H:%M UTC')}</i>\n\n"
            f"<b>📈 24H PERFORMANCE</b>\n"
            f"Signals: {total} | Wins: {wins} | Losses: {losses}\n"
            f"Win Rate: {win_rate}% | Net Pips: {net_pips:+.1f}\n\n"
            f"<b>🧬 MARKET DNA</b>\n"
            + "\n".join(dna_lines) + "\n\n"
            f"<b>💰 KELLY SIZING</b>\n"
            + "\n".join(kelly_lines) + "\n\n"
            f"<b>🔒 RISK MANAGEMENT</b>\n"
            f"Breakeven Set: {be_count} | Trailing Active: {trail_count}\n"
            f"Kill Switch: {'🔴 ACTIVE' if _kill_switch_active else '🟢 INACTIVE'}\n\n"
            f"<b>🚪 DISABLED GATES</b>\n"
            + (", ".join(disabled_gates) if disabled_gates else "All gates active") + "\n\n"
            f"<i>Grandcom Gold Elite v3 | Institutional Grade</i>"
        )

        if TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_CHAT_ID:
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            await bot.send_message(chat_id=TELEGRAM_ADMIN_CHAT_ID, text=report, parse_mode="HTML")
        logger.info("📊 Daily intelligence report sent")
    except Exception as e:
        logger.error(f"Daily report error: {e}")


# ============================================================
# DNA UPDATER
# ============================================================
async def update_market_dna():
    """Update Market DNA for all pairs (runs Monday 00:05 UTC)."""
    for pair in GOLD_PAIRS:
        try:
            df = await get_price_data(pair, interval="1h", outputsize=50)
            if df is None or len(df) < 15:
                continue
            atr = float(ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range().iloc[-1])
            dna = await get_market_dna(pair)
            await dna.update(new_atr=atr)
            logger.info(f"🧬 Market DNA updated for {pair}: ATR={dna.atr_14:.2f} | Vol={dna.volatility_class}")
        except Exception as e:
            logger.error(f"DNA update error for {pair}: {e}")


# ============================================================
# FRIDAY VOLATILITY SNAPSHOT
# ============================================================
async def friday_volatility_snapshot():
    """Capture end-of-week volatility snapshot (Friday 21:00 UTC)."""
    for pair in GOLD_PAIRS:
        try:
            df = await get_price_data(pair, interval="1h", outputsize=100)
            if df is None or len(df) < 20:
                continue
            atr = float(ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range().iloc[-1])
            weekly_range = float(df["high"].max() - df["low"].min())
            dna = await get_market_dna(pair)
            await db.volatility_snapshots.insert_one({
                "pair": pair,
                "atr": atr,
                "weekly_range": weekly_range,
                "volatility_class": dna.volatility_class,
                "snapshot_at": datetime.now(timezone.utc),
            })
            logger.info(f"📸 Friday volatility snapshot: {pair} ATR={atr:.2f} Range={weekly_range:.2f}")
        except Exception as e:
            logger.error(f"Friday snapshot error for {pair}: {e}")


# ============================================================
# SIGNAL GENERATION PIPELINE (27 STEPS)
# ============================================================
async def generate_gold_signal(pair: str):
    global _kill_switch_active, _last_signal_time, _daily_loss_pips

    try:
        params = GOLD_PAIRS[pair]
        logger.info(f"📊 Generating gold signal for {pair}")

        # ── Step 1: Kill switch ──────────────────────────────────────────────
        if _kill_switch_active:
            logger.warning(f"🚨 Kill switch active — {pair} signal blocked")
            return

        # ── Step 2: Throttle check (Gate 17) ────────────────────────────────
        if GATE_CONFIG["gate_17_throttle_guard"]["enabled"]:
            last = _last_signal_time.get(pair)
            if last and (datetime.now(timezone.utc) - last).total_seconds() < 6 * 3600:
                logger.info(f"{pair} throttled — last signal < 6h ago")
                return

        # ── Step 3: Drawdown check (Gate 16) ────────────────────────────────
        if GATE_CONFIG["gate_16_drawdown_guard"]["enabled"]:
            if _daily_loss_pips > 50:
                logger.warning(f"{pair} blocked — daily loss pips {_daily_loss_pips:.1f} > 50")
                return

        # ── Step 4: News guard (Gate 01) ────────────────────────────────────
        news_data = {"news_nearby": False, "signal_allowed": True, "nearest_event": None, "minutes_away": None}
        if GATE_CONFIG["gate_01_news_guard"]["enabled"]:
            td_symbol = params.get("twelve_data_symbol", "XAU/USD")
            news_data = await check_news_impact(td_symbol)
            if not news_data["signal_allowed"]:
                logger.info(f"{pair} blocked — news guard: {news_data.get('nearest_event')}")
                log_blackbox(pair, "BLOCKED", "gate_01_news_guard", {}, {"news_event": news_data.get("nearest_event")})
                return

        # ── Step 5: Fetch price data ─────────────────────────────────────────
        df = await get_price_data(pair, interval="1h", outputsize=100)
        if df is None or len(df) < 20:
            logger.warning(f"Insufficient data for {pair}")
            return

        # ── Step 6: Calculate indicators ─────────────────────────────────────
        indicators = calculate_indicators(df, params)
        if not indicators:
            return

        # ── Step 7: Tech score gate ──────────────────────────────────────────
        w_score = indicators.get("weighted_score", 50.0)
        if w_score < 60:
            logger.info(f"{pair} skipped — weighted score {w_score:.1f} < 60")
            log_blackbox(pair, "BLOCKED", "tech_score_gate", indicators)
            return

        # ── Step 8: Choppy/range gate (Gate 05) ─────────────────────────────
        if GATE_CONFIG["gate_05_regime_chop"]["enabled"]:
            if indicators.get("regime") == "RANGE" and indicators.get("adx", 0) < 18:
                logger.info(f"{pair} blocked — choppy market (ADX={indicators.get('adx'):.1f})")
                log_blackbox(pair, "BLOCKED", "gate_05_regime_chop", indicators)
                return

        # ── Step 9: HOLD/NEUTRAL gate ────────────────────────────────────────
        if indicators.get("hold_signal"):
            logger.info(f"{pair} blocked — hold signal (strong trend, counter-trend trigger suppressed)")
            log_blackbox(pair, "BLOCKED", "hold_signal", indicators)
            return

        # ── Step 10: H4 MTF gate (Gate 02) ──────────────────────────────────
        h4_data = {"h4_trend": "UNKNOWN", "buy_allowed": True, "sell_allowed": True}
        if GATE_CONFIG["gate_02_h4_mtf"]["enabled"]:
            h4_data = await check_h4_trend(pair)

        # ── Step 11: DXY correlation gate (Gate 03) ──────────────────────────
        dxy_data = {"dxy_trend": "UNKNOWN", "buy_allowed": True, "dxy_ma20": None, "dxy_price": None}
        if GATE_CONFIG["gate_03_dxy_correlation"]["enabled"]:
            dxy_data = await check_dxy_correlation()

        # ── Step 12: Candlestick PA (Gate 04) ────────────────────────────────
        pattern_data = {"pattern": "NONE", "pattern_strength": "WEAK", "bullish": None}
        if GATE_CONFIG["gate_04_candlestick_pa"]["enabled"]:
            df_h1 = await get_price_data(pair, interval="1h", outputsize=10)
            if df_h1 is not None:
                pattern_data = detect_candlestick_patterns(df_h1)

        # ── Step 13: Circuit breaker (Gate 06) ───────────────────────────────
        if GATE_CONFIG["gate_06_circuit_breaker"]["enabled"]:
            cb = await check_circuit_breaker(pair)
            if cb["triggered"]:
                logger.warning(f"{pair} blocked — circuit breaker: {cb['reason']}")
                log_blackbox(pair, "BLOCKED", "gate_06_circuit_breaker", indicators, {"reason": cb["reason"]})
                return

        # ── Step 14: Session filter (Gate 07) ────────────────────────────────
        hour_utc = datetime.now(timezone.utc).hour
        session_info = get_session_confidence(hour_utc)
        if GATE_CONFIG["gate_07_session_filter"]["enabled"]:
            if w_score < session_info["min_score"]:
                logger.info(f"{pair} blocked — session {session_info['session']} requires score {session_info['min_score']}, got {w_score}")
                log_blackbox(pair, "BLOCKED", "gate_07_session_filter", indicators, {"session": session_info["session"]})
                return

        # ── Step 15: Shannon entropy filter (Gate 08) ────────────────────────
        entropy_ratio = 0.5
        if GATE_CONFIG["gate_08_entropy_filter"]["enabled"]:
            close_arr = df["close"].dropna().values.astype(float)
            entropy_ratio = calculate_shannon_entropy(close_arr[-50:] if len(close_arr) >= 50 else close_arr)
            if entropy_ratio > 0.85:
                logger.info(f"{pair} blocked — entropy too high ({entropy_ratio:.3f} > 0.85)")
                log_blackbox(pair, "BLOCKED", "gate_08_entropy_filter", indicators, {"entropy": entropy_ratio})
                return

        # ── Step 16: Hurst regime check (Gate 09) ────────────────────────────
        hurst = indicators.get("hurst", 0.5)
        hurst_regime = indicators.get("hurst_regime", "RANDOM_WALK")
        if GATE_CONFIG["gate_09_hurst_regime"]["enabled"]:
            # In strong mean-reverting regime, suppress trend-following signals
            if hurst_regime == "MEAN-REVERTING" and indicators.get("adx", 0) < 20:
                logger.info(f"{pair} blocked — mean-reverting regime with weak trend (Hurst={hurst:.3f})")
                log_blackbox(pair, "BLOCKED", "gate_09_hurst_regime", indicators, {"hurst": hurst})
                return

        # ── Step 17: Keltner channel gate (Gate 10) ──────────────────────────
        keltner = {"kc_position": 0.5, "extended_high": False, "extended_low": False}
        if GATE_CONFIG["gate_10_keltner_channel"]["enabled"]:
            keltner = calculate_keltner_channels(df)

        # ── Step 18: Liquidity sweep (Gate 12 — informational) ───────────────
        sweep_data = {"sweep_detected": False, "sweep_type": None, "pressure": "NEUTRAL"}
        if GATE_CONFIG["gate_12_liquidity_sweep"]["enabled"]:
            sweep_data = detect_liquidity_sweep(df)
            if sweep_data["sweep_detected"]:
                logger.info(f"{pair} liquidity sweep detected: {sweep_data['sweep_type']}")

        # ── Step 19: GSR correlation (Gate 14 — informational) ───────────────
        gsr_data = {"gsr": None, "gsr_extreme": False, "silver_price": None}
        if GATE_CONFIG["gate_14_gsr_correlation"]["enabled"]:
            gsr_data = await get_gold_silver_ratio(indicators["current_price"])
            if gsr_data["gsr_extreme"]:
                logger.info(f"{pair} GSR extreme ({gsr_data['gsr']:.1f}) — reduced conviction")

        # ── Step 20: Volume Profile POC (Gate 18) ────────────────────────────
        poc_data = {"poc": None, "va_low": None, "va_high": None}
        if GATE_CONFIG["gate_18_poc_confirmation"]["enabled"]:
            poc_data = calculate_volume_profile(df)

        # ── Step 21: AI analysis ─────────────────────────────────────────────
        # Apply dynamic ATR settings before AI call
        dyn_atr = get_dynamic_atr_settings(
            volatility_class=indicators.get("_vol_class", "MEDIUM"),
            hurst=hurst,
            hour_utc=hour_utc,
        )
        dynamic_params = {**params, **{
            "atr_multiplier_sl": dyn_atr["sl"],
            "atr_multiplier_tp1": dyn_atr["tp1"],
            "atr_multiplier_tp2": dyn_atr["tp2"],
            "atr_multiplier_tp3": dyn_atr["tp3"],
        }}

        ai_analysis = await generate_ai_analysis(pair, indicators, dynamic_params)
        if not ai_analysis:
            return

        signal_type = ai_analysis.get("signal", "NEUTRAL")
        if signal_type == "NEUTRAL":
            logger.info(f"No trade signal for {pair} (NEUTRAL)")
            return

        # ── Extract enriched context ─────────────────────────────────────────
        alignment = ai_analysis.get("_alignment", {})
        h4_data = ai_analysis.get("_h4", h4_data)
        dxy_data = ai_analysis.get("_dxy", dxy_data)
        news_data = ai_analysis.get("_news", news_data)
        weighted = ai_analysis.get("_weighted", {})
        pattern_data = ai_analysis.get("_pattern", pattern_data)

        # ── Step 22: Confidence gate ─────────────────────────────────────────
        confidence = float(ai_analysis.get("confidence", 0))
        if confidence < params["min_confidence"]:
            logger.info(f"{pair} skipped — AI confidence {confidence}% < {params['min_confidence']}%")
            log_blackbox(pair, signal_type, "confidence_gate", indicators, {"confidence": confidence})
            return

        # ── Step 23: H4 MTF gate (post-AI) ──────────────────────────────────
        if GATE_CONFIG["gate_02_h4_mtf"]["enabled"]:
            if signal_type == "BUY" and not h4_data.get("buy_allowed", True):
                logger.info(f"{pair} BUY blocked — H4 BEARISH")
                log_blackbox(pair, signal_type, "gate_02_h4_mtf", indicators)
                return
            if signal_type == "SELL" and not h4_data.get("sell_allowed", True):
                logger.info(f"{pair} SELL blocked — H4 BULLISH")
                log_blackbox(pair, signal_type, "gate_02_h4_mtf", indicators)
                return

        # ── Step 24: DXY gate (post-AI) ──────────────────────────────────────
        if GATE_CONFIG["gate_03_dxy_correlation"]["enabled"]:
            if signal_type == "BUY" and not dxy_data.get("buy_allowed", True):
                logger.info(f"{pair} BUY blocked — DXY uptrend")
                log_blackbox(pair, signal_type, "gate_03_dxy_correlation", indicators)
                return

        # ── Step 25: Keltner gate (post-AI) ──────────────────────────────────
        if GATE_CONFIG["gate_10_keltner_channel"]["enabled"]:
            if signal_type == "BUY" and keltner.get("extended_high"):
                logger.info(f"{pair} BUY blocked — Keltner overextended high")
                log_blackbox(pair, signal_type, "gate_10_keltner_channel", indicators)
                return
            if signal_type == "SELL" and keltner.get("extended_low"):
                logger.info(f"{pair} SELL blocked — Keltner overextended low")
                log_blackbox(pair, signal_type, "gate_10_keltner_channel", indicators)
                return

        # ── Step 26: POC gate (post-AI) ──────────────────────────────────────
        if GATE_CONFIG["gate_18_poc_confirmation"]["enabled"] and poc_data.get("va_low") and poc_data.get("va_high"):
            current_price = indicators["current_price"]
            if signal_type == "BUY" and current_price < poc_data["va_low"]:
                logger.info(f"{pair} BUY blocked — price below VA low ({poc_data['va_low']})")
                log_blackbox(pair, signal_type, "gate_18_poc_confirmation", indicators)
                return
            if signal_type == "SELL" and current_price > poc_data["va_high"]:
                logger.info(f"{pair} SELL blocked — price above VA high ({poc_data['va_high']})")
                log_blackbox(pair, signal_type, "gate_18_poc_confirmation", indicators)
                return

        # ── Step 27: Safety switch ────────────────────────────────────────────
        safety = apply_safety_switch(signal_type, indicators, alignment, weighted)
        if not safety["signal_allowed"]:
            logger.info(f"{pair} blocked — {safety['reason']}")
            log_blackbox(pair, signal_type, "safety_switch", indicators)
            return

        # ── Step 28: Duplicate guard ──────────────────────────────────────────
        recent_dupe = await db.gold_signals.find_one({
            "pair": pair,
            "type": signal_type,
            "status": "ACTIVE",
            "created_at": {"$gte": datetime.now(timezone.utc) - timedelta(hours=4)},
        })
        if recent_dupe:
            logger.info(f"{pair} duplicate signal suppressed")
            return

        # ── Step 29: Kelly sizing (Gate 19) ──────────────────────────────────
        kelly = {"kelly_fraction": 0.01, "win_rate": None, "avg_win": None, "avg_loss": None, "sample_size": 0}
        if GATE_CONFIG["gate_19_kelly_sizing"]["enabled"]:
            kelly = await calculate_kelly_fraction(pair)

        # ── Step 30: Market DNA update ────────────────────────────────────────
        dna = await get_market_dna(pair)
        await dna.update(new_atr=indicators["atr"])
        volatility_class = dna.volatility_class

        # ── Prepare signal data ───────────────────────────────────────────────
        entry_price = ai_analysis["entry_price"]
        tp_levels = ai_analysis["tp_levels"]
        sl_price = ai_analysis["sl_price"]
        risk_reward = ai_analysis.get("risk_reward", params["min_rr"])
        conviction_level = weighted.get("conviction_level", "MEDIUM")
        dxy_trend = dxy_data.get("dxy_trend", "UNKNOWN")
        dxy_status = "CONFLICT ⚠️" if dxy_trend == "UPTREND" and signal_type == "BUY" else dxy_trend
        news_status = "BLOCKED ⚠️" if news_data.get("news_nearby") else "Clear ✅"
        h4_trend = h4_data.get("h4_trend", "UNKNOWN")

        # ── Save to DB ────────────────────────────────────────────────────────
        signal_doc = {
            "pair": pair,
            "type": signal_type,
            "entry_price": entry_price,
            "current_price": indicators["current_price"],
            "tp_levels": tp_levels,
            "sl_price": sl_price,
            "confidence": round(confidence, 1),
            "analysis": ai_analysis.get("analysis", ""),
            "risk_reward": risk_reward,
            "timeframe": "H1",
            "status": "ACTIVE",
            "created_at": datetime.now(timezone.utc),
            "conviction_level": conviction_level,
            "weighted_score": w_score,
            "alignment_score": alignment.get("alignment_score", 50.0),
            "h4_trend": h4_trend,
            "dxy_trend": dxy_trend,
            "adx": indicators.get("adx"),
            "stoch_k": indicators.get("stoch_k"),
            "williams_r": indicators.get("williams_r"),
            "cci": indicators.get("cci"),
            "pattern": pattern_data.get("pattern", "NONE"),
            "hurst": hurst,
            "hurst_regime": hurst_regime,
            "volatility_class": volatility_class,
            "entropy": entropy_ratio,
            "kc_position": keltner.get("kc_position"),
            "sweep_detected": sweep_data.get("sweep_detected"),
            "gsr": gsr_data.get("gsr"),
            "poc": poc_data.get("poc"),
            "kelly_fraction": kelly.get("kelly_fraction"),
            "session": session_info.get("session"),
            "regime": indicators.get("regime"),
        }
        await db.gold_signals.insert_one(signal_doc)

        # ── Record throttle ───────────────────────────────────────────────────
        _last_signal_time[pair] = datetime.now(timezone.utc)

        # ── Send to Telegram ──────────────────────────────────────────────────
        await send_signal_to_telegram(
            pair=pair,
            signal_type=signal_type,
            entry_price=entry_price,
            tp_levels=tp_levels,
            sl_price=sl_price,
            confidence=round(confidence, 1),
            risk_reward=risk_reward,
            analysis=ai_analysis.get("analysis", ""),
            conviction_level=conviction_level,
            alignment_score=alignment.get("alignment_score", 50.0),
            technical_score=w_score,
            h4_trend=h4_trend,
            dxy_status=dxy_status,
            news_status=news_status,
            kelly_fraction=kelly.get("kelly_fraction", 0.01),
            hurst_regime=hurst_regime,
            volatility_class=volatility_class,
            session=session_info.get("session", "UNKNOWN"),
        )

        # ── Blackbox log ──────────────────────────────────────────────────────
        log_blackbox(pair, signal_type, None, indicators, {
            "confidence": confidence,
            "conviction": conviction_level,
            "kelly": kelly.get("kelly_fraction"),
            "hurst": hurst,
            "entropy": entropy_ratio,
            "gsr": gsr_data.get("gsr"),
            "poc": poc_data.get("poc"),
        })

        logger.info(
            f"✅ {pair} {signal_type} @ {entry_price} | TP: {tp_levels} | SL: {sl_price} | "
            f"Conf: {confidence}% | Score: {w_score:.1f} | Conviction: {conviction_level} | "
            f"Kelly: {kelly.get('kelly_fraction', 0.01)*100:.2f}% | Hurst: {hurst:.3f}"
        )
    except Exception as e:
        logger.error(f"Error generating gold signal for {pair}: {e}")


async def run_gold_signals():
    logger.info("🥇 Running gold signal generation...")
    for pair in GOLD_PAIRS:
        await generate_gold_signal(pair)
        await asyncio.sleep(2)
    logger.info("🥇 Gold signal generation complete")


# ============================================================
# TELEGRAM ADMIN COMMANDS
# ============================================================
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _kill_switch_active
    active_gates = sum(1 for v in GATE_CONFIG.values() if v["enabled"])
    disabled_gates = [k for k, v in GATE_CONFIG.items() if not v["enabled"]]
    text = (
        f"🤖 <b>GRANDCOM GOLD ELITE v3 — STATUS</b>\n\n"
        f"Kill Switch: {'🔴 ACTIVE' if _kill_switch_active else '🟢 INACTIVE'}\n"
        f"Active Gates: {active_gates}/20\n"
        f"Disabled: {', '.join(disabled_gates) if disabled_gates else 'None'}\n"
        f"Daily Loss Pips: {_daily_loss_pips:.1f}\n"
        f"Circuit Breaker: {'🔴 ACTIVE' if _circuit_breaker_until and datetime.now(timezone.utc) < _circuit_breaker_until else '🟢 CLEAR'}\n"
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /gate <gate_name> <on|off>")
        return
    gate_name = args[0].lower()
    action = args[1].lower()
    if gate_name not in GATE_CONFIG:
        await update.message.reply_text(f"Unknown gate: {gate_name}\nAvailable: {', '.join(GATE_CONFIG.keys())}")
        return
    GATE_CONFIG[gate_name]["enabled"] = action == "on"
    status = "ENABLED ✅" if action == "on" else "DISABLED ❌"
    await update.message.reply_text(f"Gate {gate_name} → {status}")
    logger.info(f"Admin: gate {gate_name} set to {action}")


async def cmd_dna(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    pair = args[0].upper() if args else "XAUUSD"
    if pair not in GOLD_PAIRS:
        await update.message.reply_text(f"Unknown pair: {pair}")
        return
    dna = await get_market_dna(pair)
    text = (
        f"🧬 <b>Market DNA — {pair}</b>\n\n"
        f"ATR(14): {dna.atr_14:.4f}\n"
        f"Avg Spread: {dna.avg_spread:.4f}\n"
        f"Volatility Class: {dna.volatility_class}\n"
        f"SL Clamp Multiplier: {dna.sl_clamp_multiplier}\n"
        f"Avg Slippage: {dna.avg_slippage:.4f}\n"
        f"Spread Guard: {dna.spread_guard:.4f}\n"
        f"TP Buffer: {dna.tp_buffer:.4f}\n"
        f"TP Net Loss Count: {dna.tp_net_loss_count}"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_kill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _kill_switch_active
    _kill_switch_active = True
    await update.message.reply_text("🚨 KILL SWITCH ACTIVATED — All signal generation PAUSED.")
    await _send_admin_alert("🚨 KILL SWITCH manually activated via /kill command.")
    logger.critical("Kill switch manually activated via Telegram /kill command")


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Generating daily intelligence report...")
    await send_daily_intelligence_report()


# ============================================================
# APP & SCHEDULER
# ============================================================
scheduler = AsyncIOScheduler()
_tg_app: Application | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tg_app

    # Load Market DNA
    for pair in GOLD_PAIRS:
        await get_market_dna(pair)

    # Scheduler jobs
    scheduler.add_job(run_gold_signals, "interval", hours=SIGNAL_INTERVAL_HOURS, id="gold_signals")
    scheduler.add_job(check_breakeven, "interval", minutes=5, id="breakeven_monitor")
    scheduler.add_job(check_all_gold_outcomes, "interval", seconds=60, id="outcome_tracker")
    scheduler.add_job(update_trailing_stops, "interval", minutes=5, id="trailing_stops")
    scheduler.add_job(risk_commander.check_equity, "interval", seconds=30, id="kill_switch_monitor")
    scheduler.add_job(update_market_dna, "cron", day_of_week="mon", hour=0, minute=5, id="dna_updater")
    scheduler.add_job(friday_volatility_snapshot, "cron", day_of_week="fri", hour=21, minute=0, id="friday_snapshot")
    scheduler.add_job(monitor_correlation_matrix, "interval", hours=4, id="correlation_monitor")
    scheduler.add_job(send_daily_intelligence_report, "cron", hour=7, minute=0, id="daily_report")
    scheduler.start()

    # Telegram bot (polling for admin commands)
    if TELEGRAM_BOT_TOKEN:
        try:
            _tg_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
            _tg_app.add_handler(CommandHandler("status", cmd_status))
            _tg_app.add_handler(CommandHandler("gate", cmd_gate))
            _tg_app.add_handler(CommandHandler("dna", cmd_dna))
            _tg_app.add_handler(CommandHandler("kill", cmd_kill))
            _tg_app.add_handler(CommandHandler("report", cmd_report))
            await _tg_app.initialize()
            await _tg_app.start()
            await _tg_app.updater.start_polling(drop_pending_updates=True)
            logger.info("🤖 Telegram admin bot started (polling)")
        except Exception as e:
            logger.warning(f"Telegram admin bot failed to start: {e}")

    logger.info(f"🥇 Gold Signals Elite v3 started — {list(GOLD_PAIRS.keys())} every {SIGNAL_INTERVAL_HOURS}h")
    asyncio.create_task(run_gold_signals())
    yield

    # Shutdown
    if _tg_app:
        try:
            await _tg_app.updater.stop()
            await _tg_app.stop()
            await _tg_app.shutdown()
        except Exception:
            pass
    scheduler.shutdown()
    client.close()


app = FastAPI(title="Grandcom Gold Signals — Elite v3", lifespan=lifespan)


# ============================================================
# API ENDPOINTS
# ============================================================
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "service": "gold_signals_elite_v3",
        "pairs": list(GOLD_PAIRS.keys()),
        "kill_switch": _kill_switch_active,
        "active_gates": sum(1 for v in GATE_CONFIG.values() if v["enabled"]),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/gold/signals")
async def get_gold_signals(status: str = None, limit: int = 50):
    query = {}
    if status:
        query["status"] = status.upper()
    signals = await db.gold_signals.find(query, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    return {"signals": signals, "count": len(signals)}


@app.get("/api/gold/stats")
async def get_gold_stats():
    try:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        signals = await db.gold_signals.find({"created_at": {"$gte": since}}).to_list(500)
        total = len(signals)
        wins = sum(1 for s in signals if s.get("result") == "WIN")
        losses = sum(1 for s in signals if s.get("result") == "LOSS")
        net_pips = sum(s.get("pips", 0) for s in signals if s.get("pips") is not None)
        return {
            "period": "24h",
            "total_signals": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            "net_pips": round(net_pips, 2),
            "kill_switch": _kill_switch_active,
            "daily_loss_pips": round(_daily_loss_pips, 2),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/gold/dna")
async def get_gold_dna():
    result = {}
    for pair in GOLD_PAIRS:
        dna = await get_market_dna(pair)
        result[pair] = dna.to_dict()
    return result


@app.get("/api/gold/gates")
async def get_gold_gates():
    return {"gates": GATE_CONFIG}


@app.post("/api/gold/gates/toggle")
async def toggle_gate(payload: dict):
    gate_name = payload.get("gate")
    enabled = payload.get("enabled")
    if gate_name not in GATE_CONFIG:
        raise HTTPException(status_code=404, detail=f"Gate '{gate_name}' not found")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="'enabled' must be a boolean")
    GATE_CONFIG[gate_name]["enabled"] = enabled
    logger.info(f"API: gate {gate_name} set to {'enabled' if enabled else 'disabled'}")
    return {"gate": gate_name, "enabled": enabled, "desc": GATE_CONFIG[gate_name]["desc"]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8002)))

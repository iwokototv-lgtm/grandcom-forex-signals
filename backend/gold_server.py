"""
Grandcom Gold Signals Server
Standalone backend for XAUUSD & XAUEUR signals
Sends to @grandcomgold Telegram channel
Designed for Railway deployment (no emergentintegrations dependency)

v2: Multi-indicator system with correlation, news guard, and weighted confidence logic
    - Advanced momentum: Stochastic(9,6), StochRSI(14), CCI(14), Williams%R(14), ADX(14)
    - Alignment scoring (Williams%R + StochRSI team vote)
    - Multi-timeframe analysis (H1 vs H4)
    - News guard (block signals ±60 min around high-impact events)
    - DXY correlation engine (inverse relationship check)
    - Weighted confidence: 40% Trend / 30% Momentum / 30% Triggers
    - Price action pattern detection (Engulfing, Pin Bar, Doji)
    - Safety switch (oversold pullback guard)
"""
from fastapi import FastAPI
from contextlib import asynccontextmanager
import os
import logging
import json
import re
import asyncio
import aiohttp
import ta
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from telegram import Bot
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
TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("EMERGENT_LLM_KEY")

# Gold pair configuration — ATR-based swing strategy
GOLD_PAIRS = {
    "XAUUSD": {
        "twelve_data_symbol": "XAU/USD",
        "pip_value": 0.10,
        "decimal_places": 2,
        "atr_multiplier_sl": 1.5,
        "atr_multiplier_tp1": 0.05,  # ~0.5 pips — scalping/tight swing
        "atr_multiplier_tp2": 0.10,  # ~1.0 pips
        "atr_multiplier_tp3": 0.15,  # ~1.5 pips
        "min_rr": 1.8,
        "min_confidence": 60,
    },
    "XAUEUR": {
        "twelve_data_symbol": "XAU/EUR",
        "pip_value": 0.10,
        "decimal_places": 2,
        "atr_multiplier_sl": 1.5,
        "atr_multiplier_tp1": 0.05,  # ~0.5 pips — scalping/tight swing
        "atr_multiplier_tp2": 0.10,  # ~1.0 pips
        "atr_multiplier_tp3": 0.15,  # ~1.5 pips
        "min_rr": 1.8,
        "min_confidence": 60,
    },
}

SIGNAL_INTERVAL_MINUTES = 2
MIN_CONFIDENCE = 60

# ============ DB ============
client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]

# ============ PRICE DATA ============
async def get_price_data(pair: str, interval: str = "1h", outputsize: int = 100):
    symbol = GOLD_PAIRS[pair]["twelve_data_symbol"]
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize={outputsize}&apikey={TWELVE_DATA_API_KEY}"
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
    """Fetch price data for any TwelveData symbol (e.g. DXY)."""
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


# ============ INDICATORS ============
def calculate_indicators(df: pd.DataFrame, params: dict) -> dict | None:
    """
    Calculate all technical indicators including the new advanced set:
    RSI, MACD, MA20/50, Bollinger Bands, ATR (existing)
    + ADX(14), Stochastic(9,6), StochRSI(14), CCI(14), Williams%R(14) (new)
    """
    try:
        close = df["close"]
        high = df["high"]
        low = df["low"]

        # ── Existing indicators ──────────────────────────────────────────────
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

        # ── New advanced indicators ──────────────────────────────────────────
        # ADX(14) — trend strength
        adx_ind = ta.trend.ADXIndicator(high, low, close, window=14)
        df["adx"] = adx_ind.adx()

        # Stochastic(9,6) — fast momentum
        stoch_ind = ta.momentum.StochasticOscillator(high, low, close, window=9, smooth_window=6)
        df["stoch_k"] = stoch_ind.stoch()
        df["stoch_d"] = stoch_ind.stoch_signal()

        # StochRSI(14) — stochastic applied to RSI
        stochrsi_ind = ta.momentum.StochRSIIndicator(close, window=14, smooth1=3, smooth2=3)
        df["stochrsi_k"] = stochrsi_ind.stochrsi_k()
        df["stochrsi_d"] = stochrsi_ind.stochrsi_d()

        # CCI(14) — commodity channel index
        cci_ind = ta.trend.CCIIndicator(high, low, close, window=14)
        df["cci"] = cci_ind.cci()

        # Williams%R(14) — overbought/oversold (-100 to 0)
        wr_ind = ta.momentum.WilliamsRIndicator(high, low, close, lbp=14)
        df["williams_r"] = wr_ind.williams_r()

        latest = df.iloc[-1]
        dp = params["decimal_places"]
        trend = "BULLISH" if latest["close"] > latest["ma_50"] else "BEARISH"

        return {
            # ── Existing ────────────────────────────────────────────────────
            "current_price": round(float(latest["close"]), dp),
            "rsi": float(latest["rsi"]),
            "macd": float(latest["macd"]),
            "macd_signal": float(latest["macd_signal"]),
            "ma_20": round(float(latest["ma_20"]), dp),
            "ma_50": round(float(latest["ma_50"]), dp),
            "bb_upper": round(float(latest["bb_upper"]), dp),
            "bb_lower": round(float(latest["bb_lower"]), dp),
            "atr": round(float(latest["atr"]), dp),
            "trend": trend,
            # ── New ─────────────────────────────────────────────────────────
            "adx": round(float(latest["adx"]), 2),
            "stoch_k": round(float(latest["stoch_k"]), 2),
            "stoch_d": round(float(latest["stoch_d"]), 2),
            "stochrsi_k": round(float(latest["stochrsi_k"]) * 100, 2),   # normalise to 0-100
            "stochrsi_d": round(float(latest["stochrsi_d"]) * 100, 2),
            "cci": round(float(latest["cci"]), 2),
            "williams_r": round(float(latest["williams_r"]), 2),
        }
    except Exception as e:
        logger.error(f"Indicator calc error: {e}")
        return None


# ============ ALIGNMENT SCORE ============
def calculate_alignment_score(indicators: dict) -> dict:
    """
    Calculate alignment between Williams%R and StochRSI (team vote).
    Both indicators must agree on direction to boost confidence.

    Returns:
        alignment_score  : 0–100 (% agreement)
        confidence_boost : 0–20 (extra confidence points when aligned)
        wr_bias          : "BULLISH" | "BEARISH" | "NEUTRAL"
        srsi_bias        : "BULLISH" | "BEARISH" | "NEUTRAL"
        aligned          : bool
    """
    try:
        wr = indicators.get("williams_r", -50.0)
        srsi_k = indicators.get("stochrsi_k", 50.0)

        # Williams%R: below -80 = oversold (bullish), above -20 = overbought (bearish)
        if wr <= -80:
            wr_bias = "BULLISH"
        elif wr >= -20:
            wr_bias = "BEARISH"
        else:
            wr_bias = "NEUTRAL"

        # StochRSI: below 20 = oversold (bullish), above 80 = overbought (bearish)
        if srsi_k <= 20:
            srsi_bias = "BULLISH"
        elif srsi_k >= 80:
            srsi_bias = "BEARISH"
        else:
            srsi_bias = "NEUTRAL"

        # Score agreement
        if wr_bias == "NEUTRAL" and srsi_bias == "NEUTRAL":
            alignment_score = 50.0
            confidence_boost = 0.0
            aligned = False
        elif wr_bias == srsi_bias and wr_bias != "NEUTRAL":
            alignment_score = 100.0
            confidence_boost = 20.0
            aligned = True
        elif wr_bias != srsi_bias and "NEUTRAL" not in (wr_bias, srsi_bias):
            # Direct disagreement
            alignment_score = 0.0
            confidence_boost = 0.0
            aligned = False
        else:
            # One neutral, one directional — partial agreement
            alignment_score = 50.0
            confidence_boost = 5.0
            aligned = False

        return {
            "alignment_score": alignment_score,
            "confidence_boost": confidence_boost,
            "wr_bias": wr_bias,
            "srsi_bias": srsi_bias,
            "aligned": aligned,
        }
    except Exception as e:
        logger.error(f"Alignment score error: {e}")
        return {
            "alignment_score": 50.0,
            "confidence_boost": 0.0,
            "wr_bias": "NEUTRAL",
            "srsi_bias": "NEUTRAL",
            "aligned": False,
        }


# ============ H4 TREND CHECK ============
async def check_h4_trend(pair: str) -> dict:
    """
    Fetch H4 data and determine the higher-timeframe trend via MA50.
    BUY signals are blocked when H4 is BEARISH; SELL signals when H4 is BULLISH.

    Returns:
        h4_trend       : "BULLISH" | "BEARISH" | "UNKNOWN"
        buy_allowed    : bool
        sell_allowed   : bool
    """
    try:
        df = await get_price_data(pair, interval="4h", outputsize=60)
        if df is None or len(df) < 51:
            logger.warning(f"H4 data insufficient for {pair}, skipping H4 filter")
            return {"h4_trend": "UNKNOWN", "buy_allowed": True, "sell_allowed": True}

        ma50 = ta.trend.SMAIndicator(df["close"], window=50).sma_indicator()
        latest_close = float(df["close"].iloc[-1])
        latest_ma50 = float(ma50.iloc[-1])

        h4_trend = "BULLISH" if latest_close > latest_ma50 else "BEARISH"
        buy_allowed = h4_trend == "BULLISH"
        sell_allowed = h4_trend == "BEARISH"

        logger.info(f"H4 trend for {pair}: {h4_trend} (close={latest_close:.2f}, MA50={latest_ma50:.2f})")
        return {"h4_trend": h4_trend, "buy_allowed": buy_allowed, "sell_allowed": sell_allowed}
    except Exception as e:
        logger.error(f"H4 trend check error for {pair}: {e}")
        return {"h4_trend": "UNKNOWN", "buy_allowed": True, "sell_allowed": True}


# ============ DXY CORRELATION ============
async def check_dxy_correlation() -> dict:
    """
    Fetch DXY H1 data and assess its trend.
    XAUUSD is inversely correlated with DXY:
      - DXY strong uptrend → gold likely to fall → block BUY signals.

    Returns:
        dxy_trend   : "UPTREND" | "DOWNTREND" | "NEUTRAL" | "UNKNOWN"
        buy_allowed : bool  (False when DXY is in strong uptrend)
        dxy_ma20    : float | None
        dxy_price   : float | None
    """
    try:
        df = await get_generic_price_data("DXY", interval="1h", outputsize=30)
        if df is None or len(df) < 21:
            logger.warning("DXY data unavailable, skipping DXY filter")
            return {"dxy_trend": "UNKNOWN", "buy_allowed": True, "dxy_ma20": None, "dxy_price": None}

        ma20 = ta.trend.SMAIndicator(df["close"], window=20).sma_indicator()
        latest_close = float(df["close"].iloc[-1])
        latest_ma20 = float(ma20.iloc[-1])

        # Determine DXY trend
        if latest_close > latest_ma20 * 1.002:   # >0.2% above MA20 = strong uptrend
            dxy_trend = "UPTREND"
            buy_allowed = False   # DXY up → gold down → block BUY
        elif latest_close < latest_ma20 * 0.998:  # >0.2% below MA20 = downtrend
            dxy_trend = "DOWNTREND"
            buy_allowed = True
        else:
            dxy_trend = "NEUTRAL"
            buy_allowed = True

        logger.info(f"DXY: {dxy_trend} (price={latest_close:.3f}, MA20={latest_ma20:.3f})")
        return {
            "dxy_trend": dxy_trend,
            "buy_allowed": buy_allowed,
            "dxy_ma20": round(latest_ma20, 3),
            "dxy_price": round(latest_close, 3),
        }
    except Exception as e:
        logger.error(f"DXY correlation check error: {e}")
        return {"dxy_trend": "UNKNOWN", "buy_allowed": True, "dxy_ma20": None, "dxy_price": None}


# ============ NEWS GUARD ============
async def check_news_impact(symbol: str = "XAU/USD") -> dict:
    """
    Query TwelveData news endpoint and block signals if a high-impact
    news event is within ±60 minutes of now.

    Returns:
        news_nearby     : bool
        signal_allowed  : bool
        nearest_event   : str | None  (description of nearest event)
        minutes_away    : int | None
    """
    try:
        url = (
            f"https://api.twelvedata.com/news"
            f"?symbol={symbol}&apikey={TWELVE_DATA_API_KEY}&outputsize=20"
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()

        articles = data.get("data", []) if isinstance(data, dict) else []
        if not articles:
            logger.info("No news data returned — news guard cleared")
            return {"news_nearby": False, "signal_allowed": True, "nearest_event": None, "minutes_away": None}

        now_utc = datetime.now(timezone.utc)
        window = timedelta(minutes=60)
        nearest_event = None
        min_minutes = None

        for article in articles:
            # TwelveData news uses "datetime" field (ISO 8601)
            pub_str = article.get("datetime") or article.get("published_at") or ""
            if not pub_str:
                continue
            try:
                pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                diff = abs((pub_dt - now_utc).total_seconds() / 60)
                if diff <= 60:
                    if min_minutes is None or diff < min_minutes:
                        min_minutes = int(diff)
                        nearest_event = article.get("title", "Unknown news event")
            except Exception:
                continue

        news_nearby = min_minutes is not None
        if news_nearby:
            logger.warning(f"⚠️ News guard triggered: '{nearest_event}' ({min_minutes} min away)")
        else:
            logger.info("News guard: clear")

        return {
            "news_nearby": news_nearby,
            "signal_allowed": not news_nearby,
            "nearest_event": nearest_event,
            "minutes_away": min_minutes,
        }
    except Exception as e:
        logger.error(f"News impact check error: {e}")
        # On error, allow signal (fail-open) but log it
        return {"news_nearby": False, "signal_allowed": True, "nearest_event": None, "minutes_away": None}


# ============ WEIGHTED CONFIDENCE ============
def calculate_weighted_confidence(indicators: dict, alignment: dict) -> dict:
    """
    Weighted confidence scoring:
      40% Trend     — MA50 position + ADX strength
      30% Momentum  — MACD cross + RSI not extreme
      30% Triggers  — Stochastic + Williams%R off extreme levels

    Gates:
      < 60  → skip (LOW conviction)
      60–84 → MEDIUM conviction
      ≥ 85  → HIGH CONVICTION

    Returns:
        trend_score, momentum_score, trigger_score,
        weighted_score, conviction_level
    """
    try:
        # ── Trend score (0–100) ──────────────────────────────────────────────
        trend_pts = 0
        # MA50 alignment
        if indicators["trend"] == "BULLISH":
            trend_pts += 50
        # ADX > 25 = trending market
        if indicators["adx"] > 25:
            trend_pts += 30
        elif indicators["adx"] > 20:
            trend_pts += 15
        # MACD above signal line
        if indicators["macd"] > indicators["macd_signal"]:
            trend_pts += 20
        trend_score = min(trend_pts, 100)

        # ── Momentum score (0–100) ───────────────────────────────────────────
        momentum_pts = 0
        rsi = indicators["rsi"]
        # RSI in healthy zone (not extreme)
        if 40 <= rsi <= 60:
            momentum_pts += 40   # neutral zone — momentum building
        elif 30 <= rsi < 40 or 60 < rsi <= 70:
            momentum_pts += 60   # directional momentum
        elif rsi < 30 or rsi > 70:
            momentum_pts += 20   # extreme — caution
        # MACD histogram positive
        macd_hist = indicators["macd"] - indicators["macd_signal"]
        if macd_hist > 0:
            momentum_pts += 30
        elif macd_hist > -0.5:
            momentum_pts += 15
        # CCI in moderate zone
        cci = indicators["cci"]
        if -100 <= cci <= 100:
            momentum_pts += 30
        elif -200 <= cci <= 200:
            momentum_pts += 15
        momentum_score = min(momentum_pts, 100)

        # ── Trigger score (0–100) ────────────────────────────────────────────
        trigger_pts = 0
        stoch_k = indicators["stoch_k"]
        wr = indicators["williams_r"]
        # Stochastic off extreme levels (not in overbought/oversold)
        if 20 < stoch_k < 80:
            trigger_pts += 40
        elif stoch_k <= 20 or stoch_k >= 80:
            trigger_pts += 20   # at extreme — potential reversal trigger
        # Williams%R off extreme
        if -80 < wr < -20:
            trigger_pts += 40
        elif wr <= -80 or wr >= -20:
            trigger_pts += 20
        # Alignment bonus
        trigger_pts += alignment["confidence_boost"]
        trigger_score = min(trigger_pts, 100)

        # ── Weighted composite ───────────────────────────────────────────────
        weighted_score = (
            trend_score * 0.40
            + momentum_score * 0.30
            + trigger_score * 0.30
        )
        weighted_score = round(weighted_score, 1)

        if weighted_score >= 85:
            conviction_level = "HIGH"
        elif weighted_score >= 60:
            conviction_level = "MEDIUM"
        else:
            conviction_level = "LOW"

        return {
            "trend_score": round(trend_score, 1),
            "momentum_score": round(momentum_score, 1),
            "trigger_score": round(trigger_score, 1),
            "weighted_score": weighted_score,
            "conviction_level": conviction_level,
        }
    except Exception as e:
        logger.error(f"Weighted confidence error: {e}")
        return {
            "trend_score": 50.0,
            "momentum_score": 50.0,
            "trigger_score": 50.0,
            "weighted_score": 50.0,
            "conviction_level": "LOW",
        }


# ============ CANDLESTICK PATTERNS ============
def detect_candlestick_patterns(df: pd.DataFrame) -> dict:
    """
    Detect common price-action patterns on the last two candles.
    Patterns: ENGULFING, PIN_BAR, DOJI, NONE

    Returns:
        pattern          : str
        pattern_strength : "STRONG" | "MODERATE" | "WEAK"
        bullish          : bool | None
    """
    try:
        if len(df) < 2:
            return {"pattern": "NONE", "pattern_strength": "WEAK", "bullish": None}

        c1 = df.iloc[-2]   # previous candle
        c0 = df.iloc[-1]   # current candle

        o1, h1, l1, cl1 = float(c1["open"]), float(c1["high"]), float(c1["low"]), float(c1["close"])
        o0, h0, l0, cl0 = float(c0["open"]), float(c0["high"]), float(c0["low"]), float(c0["close"])

        body1 = abs(cl1 - o1)
        body0 = abs(cl0 - o0)
        range0 = h0 - l0 if h0 != l0 else 1e-9

        # ── Doji ────────────────────────────────────────────────────────────
        if body0 / range0 < 0.1:
            return {"pattern": "DOJI", "pattern_strength": "MODERATE", "bullish": None}

        # ── Engulfing ────────────────────────────────────────────────────────
        bullish_engulf = (cl1 < o1) and (cl0 > o0) and (cl0 > o1) and (o0 < cl1)
        bearish_engulf = (cl1 > o1) and (cl0 < o0) and (cl0 < o1) and (o0 > cl1)
        if bullish_engulf:
            return {"pattern": "ENGULFING", "pattern_strength": "STRONG", "bullish": True}
        if bearish_engulf:
            return {"pattern": "ENGULFING", "pattern_strength": "STRONG", "bullish": False}

        # ── Pin Bar ──────────────────────────────────────────────────────────
        upper_wick = h0 - max(o0, cl0)
        lower_wick = min(o0, cl0) - l0
        # Bullish pin bar: long lower wick (≥2× body), small upper wick
        if lower_wick >= 2 * body0 and upper_wick <= 0.3 * range0:
            return {"pattern": "PIN_BAR", "pattern_strength": "STRONG", "bullish": True}
        # Bearish pin bar: long upper wick (≥2× body), small lower wick
        if upper_wick >= 2 * body0 and lower_wick <= 0.3 * range0:
            return {"pattern": "PIN_BAR", "pattern_strength": "STRONG", "bullish": False}

        return {"pattern": "NONE", "pattern_strength": "WEAK", "bullish": None}
    except Exception as e:
        logger.error(f"Candlestick pattern detection error: {e}")
        return {"pattern": "NONE", "pattern_strength": "WEAK", "bullish": None}


# ============ SAFETY SWITCH ============
def apply_safety_switch(
    signal_type: str,
    indicators: dict,
    alignment: dict,
    weighted: dict,
) -> dict:
    """
    Safety switch: if Group1 (Trend) + Group2 (Momentum) agree on direction
    but Group3 (Triggers) shows the market is still oversold on a SELL signal
    (or overbought on a BUY), hold for a pullback rather than chasing.

    Returns:
        signal_allowed : bool
        reason         : str
    """
    try:
        trend_ok = weighted["trend_score"] >= 60
        momentum_ok = weighted["momentum_score"] >= 60
        stoch_k = indicators["stoch_k"]
        wr = indicators["williams_r"]

        if signal_type == "SELL":
            # Groups 1+2 agree bearish but triggers show oversold → wait for bounce
            if trend_ok and momentum_ok and (stoch_k <= 20 or wr <= -80):
                return {
                    "signal_allowed": False,
                    "reason": "Safety switch: SELL signal but triggers oversold — waiting for pullback",
                }

        if signal_type == "BUY":
            # Groups 1+2 agree bullish but triggers show overbought → wait for dip
            if trend_ok and momentum_ok and (stoch_k >= 80 or wr >= -20):
                return {
                    "signal_allowed": False,
                    "reason": "Safety switch: BUY signal but triggers overbought — waiting for dip",
                }

        return {"signal_allowed": True, "reason": "Safety switch: clear"}
    except Exception as e:
        logger.error(f"Safety switch error: {e}")
        return {"signal_allowed": True, "reason": "Safety switch: error (pass-through)"}


# ============ AI ANALYSIS ============
async def generate_ai_analysis(symbol: str, indicators: dict, params: dict):
    """
    Full multi-indicator AI analysis pipeline:
    1. Calculate alignment score
    2. Check H4 trend
    3. Check DXY correlation
    4. Check news impact
    5. Calculate weighted confidence
    6. Detect candlestick patterns
    7. Build enriched prompt → call LLM
    8. Apply final gates
    """
    try:
        # ── Step 1: Alignment score ──────────────────────────────────────────
        alignment = calculate_alignment_score(indicators)

        # ── Step 2: H4 trend ────────────────────────────────────────────────
        h4_data = await check_h4_trend(symbol)

        # ── Step 3: DXY correlation ──────────────────────────────────────────
        dxy_data = await check_dxy_correlation()

        # ── Step 4: News guard ───────────────────────────────────────────────
        td_symbol = GOLD_PAIRS.get(symbol, {}).get("twelve_data_symbol", "XAU/USD")
        news_data = await check_news_impact(td_symbol)

        # ── Step 5: Weighted confidence ──────────────────────────────────────
        weighted = calculate_weighted_confidence(indicators, alignment)

        # ── Step 6: Candlestick patterns ─────────────────────────────────────
        # We need the raw df here — fetch H1 data for pattern detection
        df_h1 = await get_price_data(symbol, interval="1h", outputsize=10)
        pattern_data = detect_candlestick_patterns(df_h1) if df_h1 is not None else {
            "pattern": "NONE", "pattern_strength": "WEAK", "bullish": None
        }

        # ── Step 7: Build enriched prompt ────────────────────────────────────
        dp = params["decimal_places"]
        system_message = (
            "You are an elite institutional gold trader. "
            "Provide precise, actionable trading signals with strict risk management. "
            "Consider all provided multi-indicator context carefully."
        )

        dxy_label = "NEUTRAL" if dxy_data["dxy_trend"] in ("NEUTRAL", "UNKNOWN") else dxy_data["dxy_trend"]
        news_label = "BLOCKED ⚠️" if news_data["news_nearby"] else "Clear ✅"
        pattern_label = (
            f"{pattern_data['pattern']} ({'Bullish' if pattern_data['bullish'] else 'Bearish' if pattern_data['bullish'] is False else 'Neutral'}) "
            f"[{pattern_data['pattern_strength']}]"
        )

        prompt = f"""
Analyze {symbol} market data and provide a professional trading signal.

=== CORE MARKET DATA (H1) ===
Current Price : {indicators['current_price']}
Trend (MA50)  : {indicators['trend']}
ATR(14)       : {indicators['atr']:.{dp}f}
BB Upper/Lower: {indicators['bb_upper']:.{dp}f} / {indicators['bb_lower']:.{dp}f}

=== EXISTING INDICATORS ===
RSI(14)       : {indicators['rsi']:.2f}
MACD          : {indicators['macd']:.6f}  |  Signal: {indicators['macd_signal']:.6f}
MA20 / MA50   : {indicators['ma_20']:.{dp}f} / {indicators['ma_50']:.{dp}f}

=== NEW ADVANCED INDICATORS ===
ADX(14)       : {indicators['adx']:.2f}  {'(TRENDING)' if indicators['adx'] > 25 else '(WEAK TREND)'}
Stochastic(9,6): K={indicators['stoch_k']:.2f}  D={indicators['stoch_d']:.2f}
StochRSI(14)  : K={indicators['stochrsi_k']:.2f}  D={indicators['stochrsi_d']:.2f}
CCI(14)       : {indicators['cci']:.2f}
Williams%R(14): {indicators['williams_r']:.2f}

=== ALIGNMENT SCORE (Williams%R + StochRSI) ===
Alignment     : {alignment['alignment_score']:.0f}%  ({'ALIGNED ✅' if alignment['aligned'] else 'DIVERGED ⚠️'})
Williams%R    : {alignment['wr_bias']}
StochRSI      : {alignment['srsi_bias']}
Confidence Boost: +{alignment['confidence_boost']:.0f}pts

=== MULTI-TIMEFRAME (H4) ===
H4 Trend      : {h4_data['h4_trend']}
BUY Allowed   : {'YES' if h4_data['buy_allowed'] else 'NO — H4 BEARISH'}
SELL Allowed  : {'YES' if h4_data['sell_allowed'] else 'NO — H4 BULLISH'}

=== DXY CORRELATION ===
DXY Trend     : {dxy_label}
BUY Allowed   : {'YES' if dxy_data['buy_allowed'] else 'NO — DXY UPTREND (inverse pressure)'}

=== NEWS IMPACT ===
Status        : {news_label}
{f"Nearest Event : {news_data['nearest_event']} ({news_data['minutes_away']} min away)" if news_data['news_nearby'] else "No high-impact events within ±60 min"}

=== WEIGHTED CONFIDENCE (40/30/30) ===
Trend Score   : {weighted['trend_score']:.1f}/100  (weight 40%)
Momentum Score: {weighted['momentum_score']:.1f}/100  (weight 30%)
Trigger Score : {weighted['trigger_score']:.1f}/100  (weight 30%)
TOTAL SCORE   : {weighted['weighted_score']:.1f}/100  → {weighted['conviction_level']} CONVICTION

=== PRICE ACTION ===
Pattern       : {pattern_label}

=== ATR MULTIPLIERS ===
SL: {params['atr_multiplier_sl']} | TP1: {params['atr_multiplier_tp1']} | TP2: {params['atr_multiplier_tp2']} | TP3: {params['atr_multiplier_tp3']}
Min R:R: {params['min_rr']}

=== OUTPUT FORMAT (JSON ONLY) ===
{{"signal":"BUY"or"SELL"or"NEUTRAL","confidence":0-100,"entry_price":numeric,"tp_levels":[tp1,tp2,tp3],"sl_price":numeric,"analysis":"<150 words","risk_reward":numeric}}
RESPOND ONLY WITH VALID JSON. NO OTHER TEXT.
"""

        # ── Step 8: Call LLM ─────────────────────────────────────────────────
        ai_response = None
        for attempt in range(3):
            try:
                response = await litellm.acompletion(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": prompt}
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
            logger.error(f"No AI response for {symbol}")
            return None

        # ── Parse JSON — handle markdown fences and malformed JSON ───────────
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
                    fixed = re.sub(r'"\s*\n\s*"', '",\n"', fixed)
                    fixed = re.sub(r'(\d)\s*\n\s*"', r'\1,\n"', fixed)
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
                        "tp_levels": [], "sl_price": 0
                    }
                break
            except Exception:
                if parse_attempt == 2:
                    logger.warning(f"All JSON parsing failed for {symbol}")

        if not ai_data:
            return None

        # ── Fix TP levels using ATR if AI returned bad values ─────────────────
        entry = ai_data.get("entry_price", indicators['current_price'])
        signal_type = ai_data.get("signal", "NEUTRAL")
        tp_levels = ai_data.get("tp_levels", [])
        atr = indicators["atr"]
        dp = params["decimal_places"]

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

        # ── Fix SL using ATR if needed ────────────────────────────────────────
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

        # ── Attach enriched context for downstream use ────────────────────────
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


# ============ TELEGRAM ============
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
):
    try:
        if not TELEGRAM_BOT_TOKEN:
            logger.warning("Telegram bot token not configured")
            return
        bot = Bot(token=TELEGRAM_BOT_TOKEN)

        signal_emoji = "🟢" if signal_type == "BUY" else "🔴"
        action = signal_type.capitalize()

        # HIGH CONVICTION label
        conviction_tag = " [HIGH CONVICTION 🔥]" if conviction_level == "HIGH" else ""

        # Entry range for TSCopier smart entry
        entry_lo = round(entry_price - 0.50, 2)
        entry_hi = round(entry_price + 0.50, 2)

        copier_message = (
            f"{signal_emoji} #{pair} [SWING]{conviction_tag}\n"
            f"\n"
            f"{action} {entry_lo} - {entry_hi}\n"
            f"\n"
            f"TP1: {tp_levels[0]}\n"
            f"TP2: {tp_levels[1]}\n"
            f"TP3: {tp_levels[2]}\n"
            f"\n"
            f"SL: {sl_price}\n"
        )

        safe_analysis = sanitize_html(analysis)
        info_message = (
            f"<b>📊 R:R:</b> 1:{risk_reward}  "
            f"<b>⚡ AI Confidence:</b> {confidence}%\n"
            f"<b>🎯 Technical Score:</b> {technical_score:.0f}/100  "
            f"<b>🔗 Alignment:</b> {alignment_score:.0f}% (W%R + StochRSI)\n"
            f"<b>📈 H4 Trend:</b> {h4_trend}  "
            f"<b>💵 DXY:</b> {dxy_status}  "
            f"<b>📰 News:</b> {news_status}\n"
            f"<b>📝</b> {safe_analysis}\n"
            f"<i>⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
            f"| Grandcom Gold ML Engine v2</i>"
        )

        await bot.send_message(chat_id=TELEGRAM_GOLD_CHANNEL_ID, text=copier_message)
        await bot.send_message(chat_id=TELEGRAM_GOLD_CHANNEL_ID, text=info_message, parse_mode="HTML")
        logger.info(f"✅ Gold signal sent to {TELEGRAM_GOLD_CHANNEL_ID}: {pair} {signal_type}")
    except Exception as e:
        logger.error(f"❌ Error sending gold signal to Telegram: {e}")


# ============ SIGNAL GENERATION ============
async def generate_gold_signal(pair: str):
    try:
        params = GOLD_PAIRS[pair]
        logger.info(f"📊 Generating gold signal for {pair}")

        # Fetch H1 data for primary indicator calculation
        df = await get_price_data(pair, interval="1h", outputsize=100)
        if df is None or len(df) < 20:
            logger.warning(f"Insufficient data for {pair}")
            return

        indicators = calculate_indicators(df, params)
        if not indicators:
            return

        ai_analysis = await generate_ai_analysis(pair, indicators, params)
        if not ai_analysis:
            return

        signal_type = ai_analysis.get("signal", "NEUTRAL")
        if signal_type == "NEUTRAL":
            logger.info(f"No trade signal for {pair} (NEUTRAL)")
            return

        # ── Extract enriched context ─────────────────────────────────────────
        alignment = ai_analysis.get("_alignment", {})
        h4_data = ai_analysis.get("_h4", {})
        dxy_data = ai_analysis.get("_dxy", {})
        news_data = ai_analysis.get("_news", {})
        weighted = ai_analysis.get("_weighted", {})
        pattern_data = ai_analysis.get("_pattern", {})

        # ── Gate 1: Weighted score < 60 → skip ──────────────────────────────
        w_score = weighted.get("weighted_score", 50.0)
        if w_score < 60:
            logger.info(f"{pair} skipped — weighted score {w_score:.1f} < 60 (LOW conviction)")
            return

        # ── Gate 2: H4 trend conflict → skip ────────────────────────────────
        if signal_type == "BUY" and not h4_data.get("buy_allowed", True):
            logger.info(f"{pair} BUY skipped — H4 trend is BEARISH (multi-timeframe conflict)")
            return
        if signal_type == "SELL" and not h4_data.get("sell_allowed", True):
            logger.info(f"{pair} SELL skipped — H4 trend is BULLISH (multi-timeframe conflict)")
            return

        # ── Gate 3: DXY blocks BUY → skip ───────────────────────────────────
        if signal_type == "BUY" and not dxy_data.get("buy_allowed", True):
            logger.info(f"{pair} BUY skipped — DXY in strong uptrend (inverse correlation)")
            return

        # ── Gate 4: News within 1h → skip ───────────────────────────────────
        if not news_data.get("signal_allowed", True):
            logger.info(
                f"{pair} skipped — news guard: '{news_data.get('nearest_event', 'unknown')}' "
                f"({news_data.get('minutes_away', '?')} min away)"
            )
            return

        # ── Gate 5: Safety switch ────────────────────────────────────────────
        safety = apply_safety_switch(signal_type, indicators, alignment, weighted)
        if not safety["signal_allowed"]:
            logger.info(f"{pair} skipped — {safety['reason']}")
            return

        # ── Confidence gate (original) ───────────────────────────────────────
        confidence = float(ai_analysis.get("confidence", 0))
        if confidence < params["min_confidence"]:
            logger.info(f"{pair} skipped — AI confidence {confidence}% < {params['min_confidence']}%")
            return

        entry_price = ai_analysis["entry_price"]
        tp_levels = ai_analysis["tp_levels"]
        sl_price = ai_analysis["sl_price"]
        risk_reward = ai_analysis.get("risk_reward", params["min_rr"])
        conviction_level = weighted.get("conviction_level", "MEDIUM")

        # ── Prepare Telegram metadata ────────────────────────────────────────
        dxy_trend = dxy_data.get("dxy_trend", "UNKNOWN")
        dxy_status = "CONFLICT ⚠️" if dxy_trend == "UPTREND" and signal_type == "BUY" else dxy_trend
        news_status = "BLOCKED ⚠️" if news_data.get("news_nearby") else "Clear ✅"
        h4_trend = h4_data.get("h4_trend", "UNKNOWN")

        # ── Store in DB ──────────────────────────────────────────────────────
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
            # New fields
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
        }
        await db.gold_signals.insert_one(signal_doc)

        # ── Send to Telegram ─────────────────────────────────────────────────
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
        )

        logger.info(
            f"✅ {pair} {signal_type} @ {entry_price} | TP: {tp_levels} | SL: {sl_price} | "
            f"Conf: {confidence}% | Score: {w_score:.1f} | Conviction: {conviction_level}"
        )
    except Exception as e:
        logger.error(f"Error generating gold signal for {pair}: {e}")


async def run_gold_signals():
    logger.info("🥇 Running gold signal generation...")
    for pair in GOLD_PAIRS:
        await generate_gold_signal(pair)
        await asyncio.sleep(2)
    logger.info("🥇 Gold signal generation complete")


# ============ APP ============
scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(run_gold_signals, "interval", minutes=SIGNAL_INTERVAL_MINUTES, id="gold_signals")
    scheduler.start()
    logger.info(f"🥇 Gold Signals Server started — {list(GOLD_PAIRS.keys())} every {SIGNAL_INTERVAL_MINUTES}min")
    asyncio.create_task(run_gold_signals())
    yield
    scheduler.shutdown()
    client.close()

app = FastAPI(title="Grandcom Gold Signals", lifespan=lifespan)

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "gold_signals", "pairs": list(GOLD_PAIRS.keys())}

@app.get("/api/gold/signals")
async def get_gold_signals(status: str = None, limit: int = 50):
    query = {}
    if status:
        query["status"] = status.upper()
    signals = await db.gold_signals.find(query, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    return {"signals": signals, "count": len(signals)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8002)))

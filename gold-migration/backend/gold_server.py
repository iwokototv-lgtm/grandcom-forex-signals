"""
Grandcom Gold Signals Server — Elite Edition
=============================================
XAUUSD & XAUEUR  →  @grandcomgold Telegram channel
Railway deployment  |  litellm + emergentintegrations fallback

UPGRADE FEATURES (v3)
──────────────────────
GROUP 1 — TREND     : ADX(14) + MA50 + H4 Trend Alignment   [weight 40%]
GROUP 2 — MOMENTUM  : RSI(14) + MACD + CCI(14)              [weight 30%]
GROUP 3 — TRIGGER   : Williams%R + StochRSI(14) [team]      [weight 30%]
GROUP 4 — SIZING    : ATR(14) [always on]

SAFETY SYSTEMS
──────────────
✅ Weighted Confidence Score (40/30/30 split)
✅ Alignment Score → AI confidence boost/reduction
✅ HIGH CONVICTION label when score ≥ 85%
✅ HOLD logic — G1+G2 agree but G3 exhausted → wait for pullback
✅ H4 Multi-Timeframe alignment — H4 blocks contra-trend signals
✅ DXY Correlation Engine — strong USD blocks Gold BUYs
✅ News Guard (Volatility Shield) — blocks trades near High Impact events
✅ Candlestick Price Action confirmation
✅ Dynamic SL (ATR-based)
✅ Regime enforcement + choppy market guard
✅ Per-pair throttle + drawdown protection
✅ Duplicate guard + breakeven monitor
✅ Single TSCopier plain-text message
"""

from fastapi import FastAPI
from contextlib import asynccontextmanager
import os, logging, json, re, asyncio, aiohttp
import ta, pandas as pd, numpy as np
from datetime import datetime, timezone, timedelta
from telegram import Bot
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from motor.motor_asyncio import AsyncIOMotorClient

HAS_EMERGENT_LLM = False
try:
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    HAS_EMERGENT_LLM = True
except ImportError:
    pass

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gold_server")

# ============ CONFIG ============
MONGO_URL             = os.environ.get("MONGO_URL")
DB_NAME               = os.environ.get("DB_NAME", "gold_signals")
TELEGRAM_BOT_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_GOLD_CHANNEL = os.environ.get("TELEGRAM_GOLD_CHANNEL_ID", "@grandcomgold")
TWELVE_DATA_API_KEY   = os.environ.get("TWELVE_DATA_API_KEY")
OPENAI_API_KEY        = os.environ.get("OPENAI_API_KEY") or os.environ.get("EMERGENT_LLM_KEY")

SIGNAL_INTERVAL_MINUTES = 240
THROTTLE_HOURS          = 6
MIN_CONFIDENCE          = 70
MIN_RR                  = 1.8
MIN_TP_DISTANCE         = 8.0
MIN_SL_DISTANCE         = 8.0
MAX_SL_DISTANCE         = 150.0
MIN_TECH_SCORE          = 60
HIGH_CONVICTION_SCORE   = 85
MAX_DAILY_LOSSES        = 2
MAX_DAILY_PIPS          = 40
PAUSE_HOURS             = 12

last_signal_time: dict = {}
daily_losses:     dict = {}

GOLD_PAIRS = {
    "XAUUSD": {
        "twelve_data_symbol": "XAU/USD",
        "pip_value":          0.10,
        "decimal_places":     2,
        "atr_multiplier_sl":  0.4,
        "atr_multiplier_tp1": 0.5,
        "atr_multiplier_tp2": 1.0,
        "atr_multiplier_tp3": 1.5,
        "min_rr":             1.8,
        "min_confidence":     70,
    },
    "XAUEUR": {
        "twelve_data_symbol": "XAU/EUR",
        "pip_value":          0.10,
        "decimal_places":     2,
        "atr_multiplier_sl":  0.4,
        "atr_multiplier_tp1": 0.5,
        "atr_multiplier_tp2": 1.0,
        "atr_multiplier_tp3": 1.5,
        "min_rr":             1.8,
        "min_confidence":     70,
    },
}

client = AsyncIOMotorClient(MONGO_URL)
db     = client[DB_NAME]

# ============ PRICE DATA ============
async def get_price_data(pair: str, interval: str = "4h", outputsize: int = 120):
    symbol = GOLD_PAIRS[pair]["twelve_data_symbol"]
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={symbol}&interval={interval}"
        f"&outputsize={outputsize}&apikey={TWELVE_DATA_API_KEY}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
                if "values" not in data:
                    logger.error(f"No data for {pair}: {data.get('message','Unknown')}")
                    return None
                df = pd.DataFrame(data["values"])
                for col in ["open", "high", "low", "close"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df["datetime"] = pd.to_datetime(df["datetime"])
                df = df.sort_values("datetime").reset_index(drop=True)
                return df
    except Exception as e:
        logger.error(f"Error fetching {pair}: {e}")
        return None

async def get_generic_price_data(symbol: str, interval: str = "1h", outputsize: int = 60):
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={symbol}&interval={interval}"
        f"&outputsize={outputsize}&apikey={TWELVE_DATA_API_KEY}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
                if "values" not in data:
                    return None
                df = pd.DataFrame(data["values"])
                for col in ["open", "high", "low", "close"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df["datetime"] = pd.to_datetime(df["datetime"])
                df = df.sort_values("datetime").reset_index(drop=True)
                return df
    except Exception as e:
        logger.error(f"Error fetching {symbol}: {e}")
        return None

# ============ NEWS GUARD ============
async def is_high_impact_news_near() -> tuple[bool, str]:
    try:
        url     = f"https://api.twelvedata.com/economic_calendar?apikey={TWELVE_DATA_API_KEY}&importance=high"
        now_utc = datetime.now(timezone.utc)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                data   = await resp.json()
                events = data.get("result", {}).get("list", []) if isinstance(data.get("result"), dict) else data.get("data", [])
                for event in events:
                    event_time_str = event.get("datetime", event.get("date", ""))
                    if not event_time_str:
                        continue
                    try:
                        event_time   = pd.to_datetime(event_time_str, utc=True)
                        diff_minutes = abs((event_time - now_utc).total_seconds() / 60)
                        if diff_minutes <= 60:
                            name = event.get("event", event.get("name", "Unknown"))
                            return True, f"High impact news in {diff_minutes:.0f} min: {name}"
                    except Exception:
                        continue
        return False, ""
    except Exception as e:
        logger.warning(f"News guard failed (allowing): {e}")
        return False, ""

# ============ H4 TREND CHECK (MTF) ============
async def check_h4_trend(pair: str) -> tuple[str, str]:
    try:
        symbol = GOLD_PAIRS[pair]["twelve_data_symbol"]
        df     = await get_generic_price_data(symbol, interval="4h", outputsize=55)
        if df is None or len(df) < 50:
            return "NEUTRAL", "Insufficient H4 data"
        df["ma50"] = ta.trend.SMAIndicator(df["close"], window=50).sma_indicator()
        adx_ind    = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14)
        df["adx"]  = adx_ind.adx()
        latest     = df.iloc[-1]
        close      = float(latest["close"])
        ma50       = float(latest["ma50"])
        adx        = float(latest["adx"])
        if adx < 18:
            return "NEUTRAL", f"H4 ADX={adx:.1f} weak — no strong trend"
        if close > ma50:
            return "BULLISH", f"H4 BULLISH: {close:.2f} > MA50 {ma50:.2f} ADX={adx:.1f}"
        return "BEARISH", f"H4 BEARISH: {close:.2f} < MA50 {ma50:.2f} ADX={adx:.1f}"
    except Exception as e:
        logger.warning(f"H4 check failed: {e}")
        return "NEUTRAL", f"H4 error: {e}"

# ============ DXY CORRELATION ============
async def check_dxy_correlation(signal_type: str) -> tuple[bool, str]:
    try:
        df = await get_generic_price_data("DXY", interval="1h", outputsize=30)
        if df is None or len(df) < 20:
            return True, "DXY unavailable — allowing"
        df["ma20"] = ta.trend.SMAIndicator(df["close"], window=20).sma_indicator()
        adx_ind    = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14)
        df["adx"]  = adx_ind.adx()
        latest     = df.iloc[-1]
        close      = float(latest["close"])
        ma20       = float(latest["ma20"])
        adx        = float(latest["adx"])
        dxy_up     = close > ma20 and adx > 22
        dxy_down   = close < ma20 and adx > 22
        if signal_type == "BUY" and dxy_up:
            return False, f"DXY strong UP ({close:.2f}>MA20 {ma20:.2f} ADX={adx:.1f}) — blocks Gold BUY"
        if signal_type == "SELL" and dxy_down:
            return False, f"DXY strong DOWN ({close:.2f}<MA20 {ma20:.2f} ADX={adx:.1f}) — blocks Gold SELL"
        return True, f"DXY={close:.2f} ADX={adx:.1f} — no conflict"
    except Exception as e:
        logger.warning(f"DXY check failed: {e}")
        return True, f"DXY error: {e}"

# ============ CANDLESTICK PRICE ACTION ============
def detect_candlestick_patterns(df: pd.DataFrame) -> dict:
    try:
        if len(df) < 3:
            return {"patterns": [], "bias": "NEUTRAL", "buy_votes": 0, "sell_votes": 0}
        c  = df.iloc[-1]
        p1 = df.iloc[-2]
        o,  h,  l,  cl = float(c["open"]),  float(c["high"]),  float(c["low"]),  float(c["close"])
        po, ph, pl, pc = float(p1["open"]), float(p1["high"]), float(p1["low"]), float(p1["close"])
        body         = abs(cl - o)
        rng          = h - l if h != l else 0.0001
        upper_shadow = h - max(o, cl)
        lower_shadow = min(o, cl) - l
        prev_body    = abs(pc - po)
        patterns, bias_votes = [], []

        if lower_shadow >= 2 * body and upper_shadow < body and body / rng < 0.35 and cl > o:
            patterns.append("HAMMER"); bias_votes.append("BUY")
        if upper_shadow >= 2 * body and lower_shadow < body and body / rng < 0.35 and cl < o:
            patterns.append("SHOOTING_STAR"); bias_votes.append("SELL")
        if pc > po and cl > o and o < pc and cl > po and body > prev_body * 1.1:
            patterns.append("BULLISH_ENGULFING"); bias_votes.append("BUY")
        if pc < po and cl < o and o > pc and cl < po and body > prev_body * 1.1:
            patterns.append("BEARISH_ENGULFING"); bias_votes.append("SELL")
        if body / rng < 0.1:
            patterns.append("DOJI"); bias_votes.append("NEUTRAL")
        if lower_shadow >= 2.5 * body and upper_shadow < 0.5 * body:
            patterns.append("PIN_BAR_BULL"); bias_votes.append("BUY")
        if upper_shadow >= 2.5 * body and lower_shadow < 0.5 * body:
            patterns.append("PIN_BAR_BEAR"); bias_votes.append("SELL")

        buy_v  = bias_votes.count("BUY")
        sell_v = bias_votes.count("SELL")
        bias   = "BUY" if buy_v > sell_v else "SELL" if sell_v > buy_v else "NEUTRAL"
        return {"patterns": patterns, "bias": bias, "buy_votes": buy_v, "sell_votes": sell_v}
    except Exception as e:
        logger.warning(f"Candlestick error: {e}")
        return {"patterns": [], "bias": "NEUTRAL", "buy_votes": 0, "sell_votes": 0}

# ============ TECHNICAL INDICATORS (GROUPED) ============
def calculate_indicators(df: pd.DataFrame, params: dict) -> dict | None:
    """
    G1 — TREND    : ADX(14) + MACD(12,26) + MA50     [40%]
    G2 — MOMENTUM : RSI(14) + CCI(14)                [30%]
    G3 — TRIGGER  : Williams%R + StochRSI(14) [team] [30%]
    G4 — SIZING   : ATR(14)
    """
    try:
        if len(df) < 50:
            return None
        df = df.copy()

        # G1
        adx_ind          = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14)
        df["adx"]        = adx_ind.adx()
        df["adx_pos"]    = adx_ind.adx_pos()
        df["adx_neg"]    = adx_ind.adx_neg()
        macd_ind         = ta.trend.MACD(df["close"], window_slow=26, window_fast=12, window_sign=9)
        df["macd"]       = macd_ind.macd()
        df["macd_sig"]   = macd_ind.macd_signal()
        df["ma_20"]      = ta.trend.SMAIndicator(df["close"], window=20).sma_indicator()
        df["ma_50"]      = ta.trend.SMAIndicator(df["close"], window=50).sma_indicator()
        df["ema_50"]     = ta.trend.EMAIndicator(df["close"], window=50).ema_indicator()

        # G2
        df["rsi"]        = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
        tp               = (df["high"] + df["low"] + df["close"]) / 3
        sma_tp           = tp.rolling(14).mean()
        mean_dev         = tp.rolling(14).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
        df["cci"]        = (tp - sma_tp) / (0.015 * mean_dev)

        # G3 — Trigger Team
        high_14          = df["high"].rolling(14).max()
        low_14           = df["low"].rolling(14).min()
        df["wpr"]        = -100 * (high_14 - df["close"]) / (high_14 - low_14 + 1e-10)
        stochrsi_ind     = ta.momentum.StochRSIIndicator(df["close"], window=14, smooth1=3, smooth2=3)
        df["srsi_k"]     = stochrsi_ind.stochrsi_k() * 100
        df["srsi_d"]     = stochrsi_ind.stochrsi_d() * 100
        stoch_ind        = ta.momentum.StochasticOscillator(
                               df["high"], df["low"], df["close"], window=9, smooth_window=6)
        df["stoch_k"]    = stoch_ind.stoch()
        df["stoch_d"]    = stoch_ind.stoch_signal()

        # G4
        df["atr"]        = ta.volatility.AverageTrueRange(
                               df["high"], df["low"], df["close"], window=14).average_true_range()
        bb               = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
        df["bb_upper"]   = bb.bollinger_hband()
        df["bb_lower"]   = bb.bollinger_lband()
        df["bb_width"]   = (df["bb_upper"] - df["bb_lower"]) / df["close"] * 100

        latest = df.iloc[-1]
        prev   = df.iloc[-2]
        dp     = params["decimal_places"]

        close   = float(latest["close"])
        adx     = float(latest["adx"])
        adx_pos = float(latest["adx_pos"])
        adx_neg = float(latest["adx_neg"])
        macd_v  = float(latest["macd"])
        macd_s  = float(latest["macd_sig"])
        ma50    = float(latest["ma_50"])
        rsi     = float(latest["rsi"])
        cci     = float(latest["cci"])
        wpr     = float(latest["wpr"])
        srsi_k  = float(latest["srsi_k"])
        srsi_d  = float(latest["srsi_d"])
        stoch_k = float(latest["stoch_k"])
        stoch_d = float(latest["stoch_d"])
        atr     = float(latest["atr"])
        bb_w    = float(latest["bb_width"])
        bb_up   = float(latest["bb_upper"])
        bb_lo   = float(latest["bb_lower"])

        macd_bull_x = float(prev["macd"]) <= float(prev["macd_sig"]) and macd_v > macd_s
        macd_bear_x = float(prev["macd"]) >= float(prev["macd_sig"]) and macd_v < macd_s

        # ── G1 VERDICT + SCORE (40%) ──────────────────────────────
        g1_buy  = adx >= 25 and macd_v > macd_s and adx_pos > adx_neg and close > ma50
        g1_sell = adx >= 25 and macd_v < macd_s and adx_neg > adx_pos and close < ma50
        g1_verdict = "BUY" if g1_buy else "SELL" if g1_sell else "NEUTRAL"
        g1_score = 0
        if adx >= 25: g1_score += 15
        if (close > ma50 and macd_v > macd_s) or (close < ma50 and macd_v < macd_s): g1_score += 15
        if macd_bull_x or macd_bear_x: g1_score += 10
        g1_score = min(g1_score, 40)

        # ── G2 VERDICT + SCORE (30%) ──────────────────────────────
        rsi_zone = "OVERBOUGHT" if rsi > 70 else "OVERSOLD" if rsi < 30 else "NEUTRAL"
        cci_zone = "OVERBOUGHT" if cci > 100 else "OVERSOLD" if cci < -100 else "NEUTRAL"
        g2_buy   = rsi < 70 and cci < 100 and macd_v > macd_s
        g2_sell  = rsi > 30 and cci > -100 and macd_v < macd_s
        g2_verdict = "BUY" if g2_buy else "SELL" if g2_sell else "NEUTRAL"
        g2_score = 0
        if 30 < rsi < 70: g2_score += 10
        if -100 < cci < 100: g2_score += 10
        if macd_bull_x or macd_bear_x: g2_score += 5
        elif macd_v != macd_s: g2_score += 5
        g2_score = min(g2_score, 30)

        # ── G3 VERDICT + SCORE (30%) — TRIGGER TEAM ──────────────
        wpr_buy    = wpr < -80
        wpr_sell   = wpr > -20
        srsi_buy   = srsi_k < 20
        srsi_sell  = srsi_k > 80
        stoch_buy  = stoch_k < 20
        stoch_sell = stoch_k > 80
        trigger_agree_buy  = wpr_buy  and srsi_buy
        trigger_agree_sell = wpr_sell and srsi_sell
        trigger_disagree   = not trigger_agree_buy and not trigger_agree_sell
        g3_verdict = "BUY" if trigger_agree_buy else "SELL" if trigger_agree_sell else "NEUTRAL"
        g3_score = 0
        if trigger_agree_buy or trigger_agree_sell: g3_score += 20
        elif not trigger_disagree: g3_score += 10
        if stoch_buy or stoch_sell: g3_score += 10
        g3_score = min(g3_score, 30)

        # ── TOTAL WEIGHTED SCORE ──────────────────────────────────
        total_score = g1_score + g2_score + g3_score

        # ── ALIGNMENT SCORE ───────────────────────────────────────
        groups_agree = sum([
            g1_verdict != "NEUTRAL",
            g2_verdict != "NEUTRAL",
            g3_verdict != "NEUTRAL",
            g1_verdict == g2_verdict,
            g2_verdict == g3_verdict,
        ])
        alignment_pct = round(groups_agree / 5 * 100, 0)

        # ── HOLD LOGIC ────────────────────────────────────────────
        hold_reason = None
        if g1_verdict == "BUY" and g2_verdict == "BUY" and g3_verdict == "SELL":
            hold_reason = (
                f"HOLD — Trend+Momentum=BUY but Trigger exhausted "
                f"(WPR={wpr:.1f} overbought, StochRSI_K={srsi_k:.1f} overbought). "
                f"Wait for pullback."
            )
        elif g1_verdict == "SELL" and g2_verdict == "SELL" and g3_verdict == "BUY":
            hold_reason = (
                f"HOLD — Trend+Momentum=SELL but Trigger exhausted "
                f"(WPR={wpr:.1f} oversold, StochRSI_K={srsi_k:.1f} oversold). "
                f"Wait for bounce."
            )

        # ── FINAL DIRECTION ───────────────────────────────────────
        if hold_reason:
            final_direction = "HOLD"
        elif g1_verdict == "BUY" and g2_verdict in ("BUY","NEUTRAL") and g3_verdict == "BUY":
            final_direction = "BUY"
        elif g1_verdict == "SELL" and g2_verdict in ("SELL","NEUTRAL") and g3_verdict == "SELL":
            final_direction = "SELL"
        else:
            final_direction = "NEUTRAL"

        is_choppy = (bb_w < 0.8) and (adx < 20)
        trend     = "BULLISH" if close > ma50 else "BEARISH"

        return {
            "current_price":    round(close, dp),
            "trend":            trend,
            "ma_50":            round(ma50, dp),
            "bb_upper":         round(bb_up, dp),
            "bb_lower":         round(bb_lo, dp),
            "bb_width":         round(bb_w, 2),
            "atr":              round(atr, dp),
            "adx":              round(adx, 2),
            "adx_pos":          round(adx_pos, 2),
            "adx_neg":          round(adx_neg, 2),
            "macd":             round(macd_v, 4),
            "macd_signal":      round(macd_s, 4),
            "macd_bull_cross":  macd_bull_x,
            "macd_bear_cross":  macd_bear_x,
            "g1_trend":         g1_verdict,
            "g1_score":         g1_score,
            "rsi":              round(rsi, 2),
            "rsi_zone":         rsi_zone,
            "cci":              round(cci, 2),
            "cci_zone":         cci_zone,
            "g2_momentum":      g2_verdict,
            "g2_score":         g2_score,
            "wpr":              round(wpr, 2),
            "srsi_k":           round(srsi_k, 2),
            "srsi_d":           round(srsi_d, 2),
            "stoch_k":          round(stoch_k, 2),
            "stoch_d":          round(stoch_d, 2),
            "g3_trigger":       g3_verdict,
            "g3_score":         g3_score,
            "trigger_agree":    trigger_agree_buy or trigger_agree_sell,
            "trigger_disagree": trigger_disagree,
            "total_score":      total_score,
            "alignment_pct":    alignment_pct,
            "tech_direction":   final_direction,
            "hold_reason":      hold_reason,
            "is_choppy":        is_choppy,
            "regime":           "UPTREND" if g1_verdict=="BUY" else "DOWNTREND" if g1_verdict=="SELL" else "RANGE",
        }
    except Exception as e:
        logger.error(f"Indicator calc error: {e}")
        return None

# ============ DRAWDOWN PROTECTION ============
def check_drawdown(pair: str) -> tuple[bool, str]:
    today = datetime.utcnow().date().isoformat()
    key   = f"{pair}_{today}"
    if key not in daily_losses:
        daily_losses[key] = {"losses": 0, "pips": 0.0, "paused_until": None}
    rec = daily_losses[key]
    if rec["paused_until"] and datetime.utcnow() < rec["paused_until"]:
        rem = int((rec["paused_until"] - datetime.utcnow()).total_seconds() / 60)
        return False, f"Drawdown pause — {rem} min remaining"
    rec["paused_until"] = None
    if rec["losses"] >= MAX_DAILY_LOSSES:
        rec["paused_until"] = datetime.utcnow() + timedelta(hours=PAUSE_HOURS)
        return False, f"Max daily losses ({rec['losses']}) — paused {PAUSE_HOURS}h"
    if rec["pips"] >= MAX_DAILY_PIPS:
        rec["paused_until"] = datetime.utcnow() + timedelta(hours=PAUSE_HOURS)
        return False, f"Max daily pips ({rec['pips']:.1f}) — paused {PAUSE_HOURS}h"
    return True, ""

# ============ SIGNAL VALIDATION ============
def validate_gold_signal(signal_type, entry, sl, tp_levels, confidence, params):
    if not tp_levels or len(tp_levels) < 3:
        return False, "Less than 3 TP levels"
    final_tp = tp_levels[-1]
    tp_dist  = abs(final_tp - entry)
    sl_dist  = abs(entry - sl)
    if signal_type == "BUY" and not (final_tp > entry > sl):
        return False, f"Invalid BUY structure"
    if signal_type == "SELL" and not (final_tp < entry < sl):
        return False, f"Invalid SELL structure"
    if tp_dist < MIN_TP_DISTANCE:
        return False, f"TP too small: {tp_dist:.2f}"
    if sl_dist < MIN_SL_DISTANCE:
        return False, f"SL too small: {sl_dist:.2f}"
    if sl_dist > MAX_SL_DISTANCE:
        return False, f"SL too wide: {sl_dist:.2f}"
    rr = tp_dist / sl_dist if sl_dist > 0 else 0
    if rr < MIN_RR:
        return False, f"R:R too low: {rr:.2f}"
    if confidence < MIN_CONFIDENCE:
        return False, f"Confidence too low: {confidence:.1f}%"
    return True, f"Valid R:R={rr:.2f} conf={confidence:.0f}%"

async def is_duplicate_active(pair: str, signal_type: str) -> bool:
    existing = await db.gold_signals.find_one({"pair": pair, "type": signal_type, "status": "ACTIVE"})
    return existing is not None

# ============ AI ANALYSIS ============
async def generate_ai_analysis(symbol, indicators, params, h4_trend, pa_result, total_score, alignment_pct):
    try:
        signal_direction = indicators["tech_direction"]
        if signal_direction in ("NEUTRAL", "HOLD"):
            return None

        atr   = indicators["atr"]
        m_sl  = params["atr_multiplier_sl"]
        m_tp1 = params["atr_multiplier_tp1"]
        m_tp2 = params["atr_multiplier_tp2"]
        m_tp3 = params["atr_multiplier_tp3"]

        if alignment_pct >= 80:
            conf_guidance = f"Alignment {alignment_pct:.0f}% — confidence up to 90%."
        elif alignment_pct >= 60:
            conf_guidance = f"Alignment {alignment_pct:.0f}% — moderate, confidence 65-75%."
        elif alignment_pct >= 40:
            conf_guidance = f"Alignment {alignment_pct:.0f}% — weak, confidence 50-65%."
        else:
            conf_guidance = f"Alignment only {alignment_pct:.0f}% — very weak, output NEUTRAL or <50%."

        conviction    = "HIGH CONVICTION" if total_score >= HIGH_CONVICTION_SCORE else "STANDARD"
        pa_text       = f"Patterns: {', '.join(pa_result['patterns']) if pa_result['patterns'] else 'None'}. Bias: {pa_result['bias']}."
        trigger_text  = (
            "Williams%R and StochRSI AGREE — Trigger confidence BOOSTED."
            if indicators["trigger_agree"] else
            "Williams%R and StochRSI DISAGREE — AI stays cautious."
        )

        system_message = (
            "You are an elite institutional gold swing trader. "
            "You receive pre-computed grouped indicator scores and alignment data. "
            "Your job: assess confidence and provide analysis. Follow group logic strictly. "
            "Respond with valid JSON only — no markdown, no extra text."
        )

        prompt = f"""
Analyze {symbol} gold swing setup. Direction pre-determined by indicators.

=== MARKET DATA ===
Price: {indicators['current_price']} | ATR(14): {atr} | H4 Trend: {h4_trend}
BB Upper: {indicators['bb_upper']} | BB Lower: {indicators['bb_lower']}

=== GROUP 1 — TREND [40%] Score: {indicators['g1_score']}/40 ===
ADX(14): {indicators['adx']} (+DI:{indicators['adx_pos']} -DI:{indicators['adx_neg']})
MACD: {indicators['macd']} | Signal: {indicators['macd_signal']}
MA50: {indicators['ma_50']} | Trend: {indicators['trend']} | G1: {indicators['g1_trend']}
{"⚡ MACD BULLISH CROSS" if indicators['macd_bull_cross'] else "⚡ MACD BEARISH CROSS" if indicators['macd_bear_cross'] else ""}

=== GROUP 2 — MOMENTUM [30%] Score: {indicators['g2_score']}/30 ===
RSI(14): {indicators['rsi']} ({indicators['rsi_zone']})
CCI(14): {indicators['cci']} ({indicators['cci_zone']})
G2 Verdict: {indicators['g2_momentum']}

=== GROUP 3 — TRIGGER TEAM [30%] Score: {indicators['g3_score']}/30 ===
Williams%R: {indicators['wpr']} | StochRSI_K: {indicators['srsi_k']} | Stoch(9,6) K:{indicators['stoch_k']} D:{indicators['stoch_d']}
{trigger_text}
G3 Verdict: {indicators['g3_trigger']}

=== ALIGNMENT & SCORE ===
Total Score: {total_score}/100 ({conviction}) | Alignment: {alignment_pct:.0f}%
{conf_guidance}

=== PRICE ACTION ===
{pa_text}

=== SAFETY GATE ===
If total_score < {MIN_TECH_SCORE}: output NEUTRAL
If total_score >= {HIGH_CONVICTION_SCORE}: add [HIGH CONVICTION] to analysis
{indicators.get('hold_reason') or 'No hold condition.'}

=== ATR SWING TARGETS — Direction: {signal_direction} ===
SL={m_sl}xATR={round(atr*m_sl,params['decimal_places'])} | TP1={m_tp1}xATR={round(atr*m_tp1,params['decimal_places'])} | TP2={m_tp2}xATR={round(atr*m_tp2,params['decimal_places'])} | TP3={m_tp3}xATR={round(atr*m_tp3,params['decimal_places'])}
R:R → TP1={round(m_tp1/m_sl,2)} | TP2={round(m_tp2/m_sl,2)} | TP3={round(m_tp3/m_sl,2)}

=== RULES ===
- Signal MUST be {signal_direction}
- {"BUY: TP above entry, SL below" if signal_direction=="BUY" else "SELL: TP below entry, SL above"}
- Round to {params['decimal_places']} decimal places

=== OUTPUT (JSON ONLY) ===
{{"signal":"{signal_direction}","confidence":0-100,"entry_price":numeric,"tp_levels":[tp1,tp2,tp3],"sl_price":numeric,"analysis":"<150 words","risk_reward":numeric}}
"""

        ai_response = None
        for attempt in range(3):
            try:
                if HAS_EMERGENT_LLM:
                    chat = LlmChat(
                        api_key=OPENAI_API_KEY,
                        session_id=f"gold_{symbol}_{datetime.now(timezone.utc).timestamp()}_{attempt}",
                        system_message=system_message,
                    ).with_model("openai", "gpt-4o-mini")
                    user_msg    = UserMessage(text=prompt)
                    ai_response = await chat.send_message(user_msg)
                else:
                    import litellm
                    resp        = await litellm.acompletion(
                        model="gpt-4o-mini",
                        messages=[{"role":"system","content":system_message},{"role":"user","content":prompt}],
                        api_key=OPENAI_API_KEY,
                    )
                    ai_response = resp.choices[0].message.content
                if ai_response and len(ai_response.strip()) > 10:
                    break
            except Exception as e:
                logger.warning(f"LLM attempt {attempt+1}/3: {e}")
                await asyncio.sleep(2)

        if not ai_response:
            return None

        raw   = ai_response.strip()
        fence = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', raw, re.DOTALL)
        if fence: raw = fence.group(1).strip()
        if not raw.startswith('{'):
            brace = re.search(r'\{.*\}', raw, re.DOTALL)
            if brace: raw = brace.group(0)

        ai_data = None
        for i in range(3):
            try:
                ai_data = json.loads(raw if i == 0 else re.sub(r',\s*([}\]])', r'\1', raw).replace("'",'"'))
                break
            except Exception:
                if i == 2:
                    conf  = re.search(r'"confidence"\s*:\s*([\d.]+)', raw)
                    entry = re.search(r'"entry_price"\s*:\s*([\d.]+)', raw)
                    ai_data = {
                        "signal": signal_direction,
                        "confidence": float(conf.group(1)) if conf else 50.0,
                        "entry_price": float(entry.group(1)) if entry else indicators["current_price"],
                        "tp_levels": [], "sl_price": 0, "analysis": "AI analysis unavailable",
                    }

        if not ai_data:
            return None

        ai_data["signal"] = signal_direction
        entry  = ai_data.get("entry_price", indicators["current_price"])
        dp     = params["decimal_places"]

        if signal_direction == "BUY":
            tp_levels = [round(entry+atr*m_tp1,dp), round(entry+atr*m_tp2,dp), round(entry+atr*m_tp3,dp)]
            sl_price  = round(entry - atr*m_sl, dp)
        else:
            tp_levels = [round(entry-atr*m_tp1,dp), round(entry-atr*m_tp2,dp), round(entry-atr*m_tp3,dp)]
            sl_price  = round(entry + atr*m_sl, dp)

        ai_data["tp_levels"]   = tp_levels
        ai_data["sl_price"]    = sl_price
        tp_dist = abs(tp_levels[2] - entry)
        sl_dist = abs(entry - sl_price)
        ai_data["risk_reward"] = round(tp_dist / sl_dist, 2) if sl_dist > 0 else params["min_rr"]

        analysis = ai_data.get("analysis", "")
        if total_score >= HIGH_CONVICTION_SCORE and "[HIGH CONVICTION]" not in analysis:
            ai_data["analysis"] = f"[HIGH CONVICTION] {analysis}"
        if total_score < MIN_TECH_SCORE:
            ai_data["confidence"] = min(ai_data.get("confidence", 50), 49)

        logger.info(f"🪙 {symbol} entry={entry} SL={sl_price} TP={tp_levels} Score={total_score}/100 RR={ai_data['risk_reward']}")
        return ai_data

    except Exception as e:
        logger.error(f"AI analysis error for {symbol}: {e}")
        return None

# ============ TELEGRAM ============
async def send_signal_to_telegram(pair, signal_type, entry_price, tp_levels, sl_price,
                                   confidence, risk_reward, regime, analysis, total_score, pa_patterns):
    try:
        if not TELEGRAM_BOT_TOKEN:
            return
        bot          = Bot(token=TELEGRAM_BOT_TOKEN)
        emoji        = "🟢" if signal_type == "BUY" else "🔴"
        action       = signal_type.capitalize()
        entry_lo     = round(entry_price - 0.50, 2)
        entry_hi     = round(entry_price + 0.50, 2)
        regime_emoji = "📈" if regime == "UPTREND" else "📉" if regime == "DOWNTREND" else "⚡"
        conv_tag     = "🏆 HIGH CONVICTION\n" if total_score >= HIGH_CONVICTION_SCORE else ""
        pa_tag       = f"🕯 {', '.join(pa_patterns[:2])}\n" if pa_patterns else ""

        message = (
            f"{emoji} {pair} {signal_type}\n\n"
            f"{action} {entry_lo} - {entry_hi}\n\n"
            f"TP1: {tp_levels[0]}\n"
            f"TP2: {tp_levels[1]}\n"
            f"TP3: {tp_levels[2]}\n\n"
            f"SL: {sl_price}\n\n"
            f"{'─'*28}\n"
            f"{regime_emoji} {regime} | SWING\n"
            f"R:R: 1:{risk_reward} | Conf: {confidence}% | Score: {total_score}/100\n"
            f"{conv_tag}{pa_tag}"
            f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Grandcom Gold EA"
        )

        await bot.send_message(chat_id=TELEGRAM_GOLD_CHANNEL, text=message)
        logger.info(f"✅ Gold signal sent: {pair} {signal_type}")
    except Exception as e:
        logger.error(f"❌ Telegram error: {e}")

# ============ MAIN SIGNAL GENERATION ============
async def generate_gold_signal(pair: str):
    try:
        params = GOLD_PAIRS[pair]
        logger.info(f"🥇 Generating signal for {pair}")

        # 1. Throttle
        last_ts = last_signal_time.get(pair)
        if last_ts and (datetime.utcnow() - last_ts).total_seconds() / 3600 < THROTTLE_HOURS:
            logger.info(f"⏳ {pair} throttled")
            return

        # 2. Drawdown
        can_trade, dd_reason = check_drawdown(pair)
        if not can_trade:
            logger.warning(f"🛑 {pair}: {dd_reason}")
            return

        # 3. News Guard
        news_blocked, news_reason = await is_high_impact_news_near()
        if news_blocked:
            logger.warning(f"📰 {pair} news blocked: {news_reason}")
            return

        # 4. Price data
        df = await get_price_data(pair, interval="4h", outputsize=120)
        if df is None or len(df) < 50:
            logger.warning(f"Insufficient data for {pair}")
            return

        # 5. Indicators
        indicators = calculate_indicators(df, params)
        if not indicators:
            return

        total_score = indicators["total_score"]
        alignment   = indicators["alignment_pct"]
        regime      = indicators["regime"]

        logger.info(
            f"📊 {pair} G1={indicators['g1_trend']}({indicators['g1_score']}) "
            f"G2={indicators['g2_momentum']}({indicators['g2_score']}) "
            f"G3={indicators['g3_trigger']}({indicators['g3_score']}) "
            f"Score={total_score}/100 Align={alignment:.0f}%"
        )

        # 6. Tech score gate
        if total_score < MIN_TECH_SCORE:
            logger.info(f"📉 {pair} score {total_score} < {MIN_TECH_SCORE} — blocked")
            return

        # 7. Regime + choppy gate
        if regime == "RANGE" or indicators["is_choppy"]:
            logger.info(f"⛔ {pair} skipped — {regime}/choppy")
            return

        # 8. HOLD / NEUTRAL gate
        if indicators["tech_direction"] in ("HOLD", "NEUTRAL"):
            logger.info(f"⏸ {pair}: {indicators.get('hold_reason') or 'NEUTRAL'}")
            return

        signal_type = indicators["tech_direction"]

        # 9. H4 MTF
        h4_trend, h4_reason = await check_h4_trend(pair)
        logger.info(f"📐 {pair} H4: {h4_reason}")
        if h4_trend == "BEARISH" and signal_type == "BUY":
            logger.info(f"🚫 {pair} BUY blocked by H4 BEARISH")
            return
        if h4_trend == "BULLISH" and signal_type == "SELL":
            logger.info(f"🚫 {pair} SELL blocked by H4 BULLISH")
            return

        # 10. DXY correlation
        dxy_ok, dxy_reason = await check_dxy_correlation(signal_type)
        logger.info(f"💵 DXY: {dxy_reason}")
        if not dxy_ok:
            logger.warning(f"🚫 {pair} blocked by DXY")
            return

        # 11. Candlestick PA
        pa_result = detect_candlestick_patterns(df)
        logger.info(f"🕯 {pair} PA: {pa_result['patterns']} bias={pa_result['bias']}")

        # 12. INSTITUTIONAL GATE BLOCK — all advanced features
        inst_ok, inst_reason = await run_full_gate_check(pair, indicators, signal_type, df)
        if not inst_ok:
            logger.warning(f"🏛 {pair} BLOCKED — {inst_reason}")
            return

        # 13. AI analysis
        ai_analysis = await generate_ai_analysis(
            symbol=pair, indicators=indicators, params=params,
            h4_trend=h4_trend, pa_result=pa_result,
            total_score=total_score, alignment_pct=alignment,
        )
        if not ai_analysis:
            return

        confidence  = float(ai_analysis.get("confidence", 0))
        entry_price = ai_analysis["entry_price"]
        tp_levels   = ai_analysis["tp_levels"]
        sl_price    = ai_analysis["sl_price"]
        risk_reward = ai_analysis.get("risk_reward", params["min_rr"])
        analysis    = ai_analysis.get("analysis", "")

        # 14. Confidence gate
        if confidence < MIN_CONFIDENCE:
            logger.info(f"📊 {pair} confidence {confidence}% < {MIN_CONFIDENCE}% — blocked")
            return

        # 15. Signal validation
        valid, reason = validate_gold_signal(signal_type, entry_price, sl_price, tp_levels, confidence, params)
        if not valid:
            logger.warning(f"🚫 {pair} validation failed: {reason}")
            return

        # 16. Duplicate guard
        if await is_duplicate_active(pair, signal_type):
            logger.info(f"⚠️ {pair} {signal_type} already ACTIVE")
            return

        # 17. Save to DB
        await db.gold_signals.insert_one({
            "pair":             pair,
            "type":             signal_type,
            "entry_price":      entry_price,
            "current_price":    indicators["current_price"],
            "tp_levels":        tp_levels,
            "sl_price":         sl_price,
            "confidence":       round(confidence, 1),
            "analysis":         analysis,
            "risk_reward":      risk_reward,
            "timeframe":        "4H",
            "regime":           regime,
            "adx":              indicators["adx"],
            "atr":              indicators["atr"],
            "total_score":      total_score,
            "alignment_pct":    alignment,
            "h4_trend":         h4_trend,
            "pa_patterns":      pa_result["patterns"],
            "conviction":       "HIGH" if total_score >= HIGH_CONVICTION_SCORE else "STANDARD",
            "g1_score":         indicators["g1_score"],
            "g2_score":         indicators["g2_score"],
            "g3_score":         indicators["g3_score"],
            "status":           "ACTIVE",
            "breakeven_triggered": False,
            "created_at":       datetime.now(timezone.utc),
        })

        # 18. Throttle record
        last_signal_time[pair] = datetime.utcnow()

        # 19. Send Telegram
        await send_signal_to_telegram(
            pair=pair, signal_type=signal_type, entry_price=entry_price,
            tp_levels=tp_levels, sl_price=sl_price, confidence=round(confidence,1),
            risk_reward=risk_reward, regime=regime, analysis=analysis,
            total_score=total_score, pa_patterns=pa_result["patterns"],
        )

        logger.info(
            f"🏆 {pair} {signal_type} @ {entry_price} | TP={tp_levels} | SL={sl_price} | "
            f"RR={risk_reward} | Conf={confidence:.0f}% | Score={total_score}/100"
        )

    except Exception as e:
        logger.error(f"Error generating gold signal for {pair}: {e}")

async def run_gold_signals():
    logger.info("🥇 Gold signal cycle starting...")
    for pair in GOLD_PAIRS:
        await generate_gold_signal(pair)
        await asyncio.sleep(5)
    logger.info("🥇 Gold signal cycle complete")

# ============ BREAKEVEN MONITOR ============
async def check_breakeven():
    try:
        active = await db.gold_signals.find(
            {"status": "ACTIVE", "breakeven_triggered": {"$ne": True}}
        ).to_list(length=50)
        for sig in active:
            pair        = sig.get("pair")
            signal_type = sig.get("type")
            entry_price = sig.get("entry_price", 0)
            tp_levels   = sig.get("tp_levels", [])
            if not all([pair, signal_type, entry_price, tp_levels]):
                continue
            tp1 = tp_levels[0]
            df  = await get_price_data(pair, interval="1h", outputsize=3)
            if df is None or len(df) == 0:
                continue
            current_price = float(df.iloc[-1]["close"])
            tp1_hit = (signal_type == "BUY" and current_price >= tp1) or \
                      (signal_type == "SELL" and current_price <= tp1)
            if not tp1_hit:
                continue
            logger.info(f"🎯 {pair} TP1 HIT @ {tp1} → Breakeven {entry_price}")
            await db.gold_signals.update_one(
                {"_id": sig["_id"]},
                {"$set": {
                    "breakeven_triggered": True, "sl_price": entry_price,
                    "breakeven_at": datetime.now(timezone.utc), "breakeven_price": current_price,
                }}
            )
            if TELEGRAM_BOT_TOKEN:
                bot = Bot(token=TELEGRAM_BOT_TOKEN)
                tp2 = tp_levels[1] if len(tp_levels) > 1 else "N/A"
                tp3 = tp_levels[2] if len(tp_levels) > 2 else "N/A"
                try:
                    await bot.send_message(
                        chat_id=TELEGRAM_GOLD_CHANNEL,
                        text=(
                            f"🔔 BREAKEVEN — {pair} {signal_type}\n\n"
                            f"✅ TP1 hit @ {tp1}\n"
                            f"📌 Move SL → {entry_price} (breakeven)\n\n"
                            f"Remaining:\nTP2: {tp2}\nTP3: {tp3}\n\n"
                            f"🛡 Trade is now risk-free\n"
                            f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
                            f"Grandcom Gold EA"
                        )
                    )
                except Exception as tg_err:
                    logger.error(f"Breakeven Telegram error: {tg_err}")
    except Exception as e:
        logger.error(f"Breakeven check error: {e}")

# ============ OUTCOME TRACKER ============
async def get_live_price(pair: str) -> float | None:
    try:
        symbol = GOLD_PAIRS[pair]["twelve_data_symbol"]
        url    = f"https://api.twelvedata.com/price?symbol={symbol}&apikey={TWELVE_DATA_API_KEY}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                return float(data["price"]) if "price" in data else None
    except Exception as e:
        logger.error(f"Live price error {pair}: {e}")
        return None

async def check_all_gold_outcomes():
    try:
        active = await db.gold_signals.find({"status": "ACTIVE"}).to_list(length=100)
        for sig in active:
            pair  = sig.get("pair")
            price = await get_live_price(pair)
            if price is None:
                continue
            stype = sig.get("type", "").upper()
            entry = sig.get("entry_price", 0)
            sl    = sig.get("sl_price", 0)
            tps   = sig.get("tp_levels", [])
            outcome = None
            if stype == "BUY":
                if price <= sl:
                    outcome = {"status":"CLOSED_SL","result":"LOSS","pips":round((price-entry)/0.1,1)}
                else:
                    for i in reversed(range(len(tps))):
                        if price >= tps[i]:
                            outcome = {"status":f"CLOSED_TP{i+1}","result":"WIN","pips":round((price-entry)/0.1,1),"tp_hit":i+1}
                            break
            elif stype == "SELL":
                if price >= sl:
                    outcome = {"status":"CLOSED_SL","result":"LOSS","pips":round((entry-price)/0.1,1)}
                else:
                    for i in reversed(range(len(tps))):
                        if price <= tps[i]:
                            outcome = {"status":f"CLOSED_TP{i+1}","result":"WIN","pips":round((entry-price)/0.1,1),"tp_hit":i+1}
                            break
            if outcome:
                await db.gold_signals.update_one(
                    {"_id": sig["_id"]},
                    {"$set": {**outcome, "exit_price": price, "closed_at": datetime.now(timezone.utc)}}
                )
                logger.info(f"📊 {pair} closed: {outcome['status']} | {outcome['pips']:+.1f} pips")
    except Exception as e:
        logger.error(f"Outcome check error: {e}")

# ============================================================
# INSTITUTIONAL UPGRADE BLOCK — v4
# ============================================================

import json as _json_log
import csv
import hashlib
from pathlib import Path

# ── Black box log files ───────────────────────────────────────
BLACKBOX_LOG  = os.getenv("BLACKBOX_LOG",  "blackbox_trades.jsonl")
DENIAL_LOG    = os.getenv("DENIAL_LOG",    "denial_log.csv")
TRAILING_LOG  = os.getenv("TRAILING_LOG",  "trailing_stops.jsonl")

# ── Circuit breaker state ─────────────────────────────────────
circuit_breaker: dict = {"paused_until": None, "last_price": None, "last_price_time": None}

# ── Session confidence requirements ───────────────────────────
SESSION_CONFIDENCE = {
    "LONDON_NY_OVERLAP": {"hours": (12, 16), "min_score": 60},
    "LONDON":            {"hours": (7,  12), "min_score": 65},
    "NEW_YORK":          {"hours": (16, 21), "min_score": 65},
    "ASIAN":             {"hours": (0,   7), "min_score": 78},
    "DEAD_ZONE":         {"hours": (21, 24), "min_score": 85},
}

# ============================================================
# GATE: BLACK BOX LOGGING
# ============================================================
async def log_blackbox(
    pair: str, signal_type: str, decision: str,
    gate_blocked: str, gate_num: int, indicators: dict,
    extra: dict = None,
):
    try:
        entry = {
            "ts":             datetime.now(timezone.utc).isoformat(),
            "pair":           pair,
            "signal_type":    signal_type,
            "decision":       decision,
            "gate_blocked":   gate_blocked,
            "gate_num":       gate_num,
            "snapshot": {
                "price":       indicators.get("current_price"),
                "adx":         indicators.get("adx"),
                "rsi":         indicators.get("rsi"),
                "cci":         indicators.get("cci"),
                "wpr":         indicators.get("wpr"),
                "srsi_k":      indicators.get("srsi_k"),
                "stoch_k":     indicators.get("stoch_k"),
                "macd":        indicators.get("macd"),
                "atr":         indicators.get("atr"),
                "total_score": indicators.get("total_score"),
                "g1":          indicators.get("g1_trend"),
                "g2":          indicators.get("g2_momentum"),
                "g3":          indicators.get("g3_trigger"),
                "alignment":   indicators.get("alignment_pct"),
                "regime":      indicators.get("regime"),
                "is_choppy":   indicators.get("is_choppy"),
            },
            **(extra or {}),
        }
        with open(BLACKBOX_LOG, "a", encoding="utf-8") as f:
            f.write(_json_log.dumps(entry) + "\n")
        if decision == "BLOCKED":
            csv_exists = Path(DENIAL_LOG).exists()
            with open(DENIAL_LOG, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if not csv_exists:
                    writer.writerow(["ts","pair","signal","gate_num","gate_blocked",
                                     "price","adx","rsi","total_score"])
                writer.writerow([
                    entry["ts"], pair, signal_type, gate_num, gate_blocked,
                    entry["snapshot"]["price"], entry["snapshot"]["adx"],
                    entry["snapshot"]["rsi"], entry["snapshot"]["total_score"],
                ])
    except Exception as e:
        logger.warning(f"Blackbox log error: {e}")


# ============================================================
# GATE: FLASH CRASH CIRCUIT BREAKER
# ============================================================
async def check_circuit_breaker(pair: str, current_price: float) -> tuple[bool, str]:
    global circuit_breaker
    now = datetime.now(timezone.utc)
    if circuit_breaker["paused_until"] and now < circuit_breaker["paused_until"]:
        rem = int((circuit_breaker["paused_until"] - now).total_seconds() / 60)
        return False, f"Circuit breaker ACTIVE — {rem} min remaining (flash crash detected)"
    if circuit_breaker["paused_until"] and now >= circuit_breaker["paused_until"]:
        circuit_breaker["paused_until"] = None
        logger.info(f"⚡ Circuit breaker reset for {pair}")
    if circuit_breaker["last_price"] and circuit_breaker["last_price_time"]:
        elapsed_sec = (now - circuit_breaker["last_price_time"]).total_seconds()
        price_move  = abs(current_price - circuit_breaker["last_price"])
        if elapsed_sec <= 120 and price_move > 15.0:
            circuit_breaker["paused_until"] = now + timedelta(minutes=30)
            msg = (
                f"⚡ FLASH CRASH DETECTED: ${price_move:.2f} move in "
                f"{elapsed_sec:.0f}s — pausing 30 min"
            )
            logger.warning(msg)
            return False, msg
    circuit_breaker["last_price"]      = current_price
    circuit_breaker["last_price_time"] = now
    return True, ""


# ============================================================
# GATE: SESSION-BASED CONFIDENCE FILTER
# ============================================================
def get_session_min_score() -> tuple[int, str]:
    hour = datetime.now(timezone.utc).hour
    for session_name, cfg in SESSION_CONFIDENCE.items():
        start, end = cfg["hours"]
        if start <= hour < end:
            return cfg["min_score"], session_name
    return 70, "UNKNOWN"


# ============================================================
# GATE: SHANNON ENTROPY FILTER
# ============================================================
def calculate_shannon_entropy(df: pd.DataFrame, window: int = 20) -> tuple[bool, float, str]:
    try:
        if len(df) < window + 5:
            return False, 0.0, "Insufficient data for entropy"
        returns = df["close"].pct_change().dropna().tail(window)
        bins    = pd.cut(returns, bins=10, labels=False, duplicates="drop")
        counts  = bins.value_counts(normalize=True)
        counts  = counts[counts > 0]
        entropy = float(-np.sum(counts * np.log2(counts + 1e-10)))
        max_entropy    = np.log2(10)
        entropy_ratio  = entropy / max_entropy
        is_chaotic = entropy_ratio > 0.85
        reason     = (
            f"Entropy={entropy:.3f} ({entropy_ratio*100:.1f}% of max) — "
            f"{'⚠️ HIGH ENTROPY (chaotic)' if is_chaotic else '✅ Organised'}"
        )
        return is_chaotic, entropy, reason
    except Exception as e:
        logger.warning(f"Entropy calc error: {e}")
        return False, 0.0, f"Entropy error: {e}"


# ============================================================
# GATE: FAIR VALUE GAP DETECTION
# ============================================================
def detect_fair_value_gaps(df: pd.DataFrame, lookback: int = 20) -> dict:
    try:
        if len(df) < lookback + 3:
            return {"fvg_detected": False, "nearest_fvg": None}
        recent    = df.tail(lookback).reset_index(drop=True)
        current   = float(df.iloc[-1]["close"])
        fvgs      = []
        for i in range(1, len(recent) - 1):
            prev_high = float(recent.iloc[i-1]["high"])
            prev_low  = float(recent.iloc[i-1]["low"])
            next_high = float(recent.iloc[i+1]["high"])
            next_low  = float(recent.iloc[i+1]["low"])
            if next_low > prev_high:
                gap_size = next_low - prev_high
                fvgs.append({
                    "type":     "BULLISH",
                    "top":      next_low,
                    "bottom":   prev_high,
                    "mid":      (next_low + prev_high) / 2,
                    "size":     gap_size,
                    "distance": abs(current - (next_low + prev_high) / 2),
                    "index":    i,
                })
            elif next_high < prev_low:
                gap_size = prev_low - next_high
                fvgs.append({
                    "type":     "BEARISH",
                    "top":      prev_low,
                    "bottom":   next_high,
                    "mid":      (prev_low + next_high) / 2,
                    "size":     gap_size,
                    "distance": abs(current - (prev_low + next_high) / 2),
                    "index":    i,
                })
        if not fvgs:
            return {"fvg_detected": False, "nearest_fvg": None, "all_fvgs": []}
        nearest = min(fvgs, key=lambda x: x["distance"])
        return {
            "fvg_detected": True,
            "nearest_fvg":  nearest,
            "fvg_count":    len(fvgs),
            "all_fvgs":     fvgs[-3:],
        }
    except Exception as e:
        logger.warning(f"FVG detection error: {e}")
        return {"fvg_detected": False, "nearest_fvg": None}


# ============================================================
# GATE: HURST EXPONENT — REGIME DETECTION
# ============================================================
def calculate_hurst_exponent(df: pd.DataFrame, lags: list = None) -> tuple[float, str]:
    try:
        if lags is None:
            lags = [2, 4, 8, 16, 32]
        prices = df["close"].dropna().values
        if len(prices) < 100:
            return 0.5, "Insufficient data — assuming random walk"
        tau, lagvec = [], []
        for lag in lags:
            if lag >= len(prices):
                continue
            pp  = np.subtract(prices[lag:], prices[:-lag])
            lagvec.append(lag)
            tau.append(np.std(pp))
        if len(tau) < 3:
            return 0.5, "Too few lags"
        poly = np.polyfit(np.log(lagvec), np.log(tau), 1)
        H    = poly[0] * 2.0
        if H > 0.55:
            regime = "TRENDING — allow wide TP, let winners run"
        elif H < 0.45:
            regime = "MEAN-REVERTING — take profits early at range edges"
        else:
            regime = "RANDOM WALK — high risk, reduce position size"
        return round(H, 3), f"H={H:.3f} → {regime}"
    except Exception as e:
        logger.warning(f"Hurst error: {e}")
        return 0.5, f"Hurst error: {e}"


# ============================================================
# GATE: KELTNER CHANNELS
# ============================================================
def calculate_keltner_channels(df: pd.DataFrame, ema_period: int = 20, atr_mult: float = 2.0) -> dict:
    try:
        ema     = ta.trend.EMAIndicator(df["close"], window=ema_period).ema_indicator()
        atr     = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
        kc_up   = ema + atr * atr_mult
        kc_low  = ema - atr * atr_mult
        latest  = df.iloc[-1]
        close   = float(latest["close"])
        kc_u    = float(kc_up.iloc[-1])
        kc_l    = float(kc_low.iloc[-1])
        kc_mid  = float(ema.iloc[-1])
        position = (close - kc_l) / (kc_u - kc_l + 1e-10)
        if position > 0.95:
            kc_zone = "EXTENDED_HIGH"
        elif position < 0.05:
            kc_zone = "EXTENDED_LOW"
        else:
            kc_zone = "WITHIN_CHANNEL"
        return {
            "kc_upper":   round(kc_u, 2),
            "kc_lower":   round(kc_l, 2),
            "kc_mid":     round(kc_mid, 2),
            "kc_position": round(position, 3),
            "kc_zone":    kc_zone,
        }
    except Exception as e:
        logger.warning(f"Keltner error: {e}")
        return {"kc_zone": "UNKNOWN", "kc_position": 0.5}


# ============================================================
# GATE: OBV VOLUME-PRICE DIVERGENCE
# ============================================================
def check_obv_divergence(df: pd.DataFrame) -> dict:
    try:
        if len(df) < 20:
            return {"divergence": "NONE", "obv_trend": "NEUTRAL"}
        df    = df.copy()
        df["obv"] = ta.volume.OnBalanceVolumeIndicator(df["close"], df["volume"]).on_balance_volume() \
                    if "volume" in df.columns and df["volume"].sum() > 0 \
                    else pd.Series([0] * len(df))
        recent   = df.tail(10)
        price_hh = float(recent["close"].iloc[-1]) > float(recent["close"].iloc[-5])
        price_ll = float(recent["close"].iloc[-1]) < float(recent["close"].iloc[-5])
        obv_hh   = float(recent["obv"].iloc[-1]) > float(recent["obv"].iloc[-5])
        obv_ll   = float(recent["obv"].iloc[-1]) < float(recent["obv"].iloc[-5])
        if price_hh and not obv_hh:
            divergence = "BEARISH"
            note       = "⚠️ Price Higher High but OBV Lower High — exhaustion move"
        elif price_ll and not obv_ll:
            divergence = "BULLISH"
            note       = "⚠️ Price Lower Low but OBV Higher Low — selling exhaustion"
        else:
            divergence = "NONE"
            note       = "✅ Price and OBV aligned — no divergence"
        return {
            "divergence":  divergence,
            "obv_latest":  float(recent["obv"].iloc[-1]),
            "note":        note,
        }
    except Exception as e:
        logger.warning(f"OBV error: {e}")
        return {"divergence": "NONE", "note": f"OBV error: {e}"}


# ============================================================
# GATE: LIQUIDITY SWEEP DETECTION
# ============================================================
def detect_liquidity_sweep(df: pd.DataFrame) -> dict:
    try:
        if len(df) < 15:
            return {"sweep_detected": False, "sweep_type": None}
        recent    = df.tail(15).reset_index(drop=True)
        latest    = recent.iloc[-1]
        c_high    = float(latest["high"])
        c_low     = float(latest["low"])
        c_close   = float(latest["close"])
        c_open    = float(latest["open"])
        prior     = recent.iloc[:-2]
        prev_high_max = float(prior["high"].max())
        prev_low_min  = float(prior["low"].min())
        sweep_up   = (c_high > prev_high_max and
                      c_close < prev_high_max and
                      (c_high - c_close) > 0.5 * (c_high - c_low))
        sweep_down = (c_low < prev_low_min and
                      c_close > prev_low_min and
                      (c_close - c_low) > 0.5 * (c_high - c_low))
        if sweep_up:
            return {
                "sweep_detected": True,
                "sweep_type":     "BEARISH_SWEEP",
                "swept_level":    round(prev_high_max, 2),
                "note":           "🔴 Liquidity sweep above equal highs — institutions filling SELL orders",
            }
        elif sweep_down:
            return {
                "sweep_detected": True,
                "sweep_type":     "BULLISH_SWEEP",
                "swept_level":    round(prev_low_min, 2),
                "note":           "🟢 Liquidity sweep below equal lows — institutions filling BUY orders",
            }
        return {"sweep_detected": False, "sweep_type": None, "note": "No sweep detected"}
    except Exception as e:
        logger.warning(f"Sweep detection error: {e}")
        return {"sweep_detected": False, "sweep_type": None}


# ============================================================
# GATE: ORDER BLOCK STRENGTH SCORING
# ============================================================
def score_order_blocks(df: pd.DataFrame) -> dict:
    try:
        if len(df) < 10:
            return {"ob_detected": False, "nearest_ob": None}
        recent  = df.tail(20).reset_index(drop=True)
        current = float(df.iloc[-1]["close"])
        obs     = []
        for i in range(1, len(recent) - 1):
            c      = recent.iloc[i]
            o, h, l, cl = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
            body   = abs(cl - o)
            rng    = h - l if h != l else 0.0001
            if body / rng > 0.60:
                strength = min(100, int(body / rng * 100))
                obs.append({
                    "type":     "BULLISH_OB" if cl > o else "BEARISH_OB",
                    "high":     h,
                    "low":      l,
                    "mid":      (h + l) / 2,
                    "strength": strength,
                    "distance": abs(current - (h + l) / 2),
                    "index":    i,
                })
        if not obs:
            return {"ob_detected": False, "nearest_ob": None}
        strong_obs = [ob for ob in obs if ob["strength"] >= 70]
        if not strong_obs:
            return {"ob_detected": False, "nearest_ob": None, "weak_obs": len(obs)}
        nearest = min(strong_obs, key=lambda x: x["distance"])
        return {
            "ob_detected":  True,
            "nearest_ob":   nearest,
            "ob_count":     len(strong_obs),
            "near_ob":      nearest["distance"] < float(df.iloc[-1]["atr"] if "atr" in df.columns else 20),
        }
    except Exception as e:
        logger.warning(f"Order block error: {e}")
        return {"ob_detected": False, "nearest_ob": None}


# ============================================================
# GATE: FEAR & GREED + GOLD-SILVER RATIO
# ============================================================
async def get_fear_greed_index() -> dict:
    try:
        url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                data  = await resp.json()
                score = data.get("fear_and_greed", {}).get("score", 50)
                rating= data.get("fear_and_greed", {}).get("rating", "Neutral")
                return {"score": float(score), "rating": rating, "available": True}
    except Exception as e:
        logger.warning(f"Fear & Greed unavailable: {e}")
        return {"score": 50.0, "rating": "Neutral", "available": False}

async def get_gold_silver_ratio() -> dict:
    try:
        url = f"https://api.twelvedata.com/price?symbol=XAG/USD&apikey={TWELVE_DATA_API_KEY}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                data = await resp.json()
                if "price" in data:
                    silver_price = float(data["price"])
                    xauusd_price = circuit_breaker.get("last_price", 3300.0)
                    gsr          = round(xauusd_price / silver_price, 2)
                    extreme      = gsr > 80
                    return {
                        "gsr":       gsr,
                        "silver":    silver_price,
                        "extreme":   extreme,
                        "note":      f"GSR={gsr} {'⚠️ EXTREME (>80) — lower BUY confidence' if extreme else '✅ Normal range'}",
                        "available": True,
                    }
        return {"gsr": 70.0, "extreme": False, "available": False}
    except Exception as e:
        logger.warning(f"GSR unavailable: {e}")
        return {"gsr": 70.0, "extreme": False, "available": False}


# ============================================================
# GATE: VOLUME-WEIGHTED MACD
# ============================================================
def calculate_vw_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    try:
        has_volume = "volume" in df.columns and df["volume"].sum() > 0
        if has_volume:
            vp      = df["close"] * df["volume"]
            vwap_f  = vp.ewm(span=fast,  adjust=False).mean() / df["volume"].ewm(span=fast,  adjust=False).mean()
            vwap_s  = vp.ewm(span=slow,  adjust=False).mean() / df["volume"].ewm(span=slow, adjust=False).mean()
            vw_line = vwap_f - vwap_s
            vw_sig  = vw_line.ewm(span=signal, adjust=False).mean()
        else:
            macd_ind = ta.trend.MACD(df["close"], window_fast=fast, window_slow=slow, window_sign=signal)
            vw_line  = macd_ind.macd()
            vw_sig   = macd_ind.macd_signal()
        vw_v    = float(vw_line.iloc[-1])
        vw_s    = float(vw_sig.iloc[-1])
        vw_prev = float(vw_line.iloc[-2])
        vs_prev = float(vw_sig.iloc[-2])
        bull_x  = vw_prev <= vs_prev and vw_v > vw_s
        bear_x  = vw_prev >= vs_prev and vw_v < vw_s
        return {
            "vw_macd":      round(vw_v, 6),
            "vw_signal":    round(vw_s, 6),
            "vw_bull_cross": bull_x,
            "vw_bear_cross": bear_x,
            "vw_direction": "BUY" if vw_v > vw_s else "SELL",
            "volume_backed": has_volume,
        }
    except Exception as e:
        logger.warning(f"VW-MACD error: {e}")
        return {"vw_direction": "NEUTRAL", "volume_backed": False}


# ============================================================
# GATE: ATR TRAILING STOP
# ============================================================
async def update_trailing_stops():
    try:
        active = await db.gold_signals.find(
            {"status": "ACTIVE", "breakeven_triggered": True}
        ).to_list(length=50)
        for sig in active:
            pair        = sig.get("pair")
            signal_type = sig.get("type")
            entry       = sig.get("entry_price", 0)
            current_sl  = sig.get("sl_price", 0)
            atr_at_entry= sig.get("atr", 20.0)
            df = await get_price_data(pair, interval="1h", outputsize=5)
            if df is None or len(df) == 0:
                continue
            current_price = float(df.iloc[-1]["close"])
            trail_dist    = atr_at_entry * 2.5
            if signal_type == "BUY":
                new_sl = round(current_price - trail_dist, 2)
                if new_sl > current_sl and new_sl > entry:
                    await db.gold_signals.update_one(
                        {"_id": sig["_id"]},
                        {"$set": {"sl_price": new_sl, "trailing_sl": True, "trailing_updated_at": datetime.now(timezone.utc)}}
                    )
                    logger.info(f"📈 {pair} Trailing SL moved UP: {current_sl} → {new_sl}")
            elif signal_type == "SELL":
                new_sl = round(current_price + trail_dist, 2)
                if new_sl < current_sl and new_sl < entry:
                    await db.gold_signals.update_one(
                        {"_id": sig["_id"]},
                        {"$set": {"sl_price": new_sl, "trailing_sl": True, "trailing_updated_at": datetime.now(timezone.utc)}}
                    )
                    logger.info(f"📉 {pair} Trailing SL moved DOWN: {current_sl} → {new_sl}")
    except Exception as e:
        logger.error(f"Trailing stop error: {e}")


# ============================================================
# HELPER: Full institutional gate runner
# ============================================================
async def run_full_gate_check(pair: str, indicators: dict, signal_type: str, df: pd.DataFrame) -> tuple[bool, str]:
    price = indicators.get("current_price", 0)

    # Gate A: Flash Crash Circuit Breaker
    ok, reason = await check_circuit_breaker(pair, price)
    if not ok:
        await log_blackbox(pair, signal_type, "BLOCKED", reason, 101, indicators)
        return False, f"Gate 101 (Circuit Breaker): {reason}"

    # Gate B: Session Confidence
    session_min, session_name = get_session_min_score()
    score = indicators.get("total_score", 0)
    if score < session_min:
        reason = f"Score {score}/100 < {session_min} required during {session_name}"
        await log_blackbox(pair, signal_type, "BLOCKED", reason, 102, indicators,
                           {"session": session_name, "required": session_min})
        return False, f"Gate 102 (Session Filter): {reason}"

    # Gate C: Shannon Entropy
    is_chaotic, entropy, e_reason = calculate_shannon_entropy(df)
    if is_chaotic:
        await log_blackbox(pair, signal_type, "BLOCKED", e_reason, 103, indicators,
                           {"entropy": entropy})
        return False, f"Gate 103 (Entropy): {e_reason} — suppressed mode"

    # Gate D: Hurst Exponent
    H, h_reason = calculate_hurst_exponent(df)
    logger.info(f"📐 {pair} Hurst: {h_reason}")
    if 0.45 <= H <= 0.55:
        logger.warning(f"⚠️ {pair} Hurst={H} — random walk zone, proceed with caution")

    # Gate E: Keltner Channel check
    kc = calculate_keltner_channels(df)
    if kc["kc_zone"] == "EXTENDED_HIGH" and signal_type == "BUY":
        reason = f"Price at KC upper ({kc['kc_position']*100:.0f}%) — extended, wait for pullback"
        await log_blackbox(pair, signal_type, "BLOCKED", reason, 104, indicators, {"kc": kc})
        return False, f"Gate 104 (Keltner): {reason}"
    if kc["kc_zone"] == "EXTENDED_LOW" and signal_type == "SELL":
        reason = f"Price at KC lower ({kc['kc_position']*100:.0f}%) — extended, wait for bounce"
        await log_blackbox(pair, signal_type, "BLOCKED", reason, 104, indicators, {"kc": kc})
        return False, f"Gate 104 (Keltner): {reason}"

    # Gate F: OBV Divergence
    obv = check_obv_divergence(df)
    if obv["divergence"] == "BEARISH" and signal_type == "BUY":
        reason = f"OBV bearish divergence — {obv['note']}"
        await log_blackbox(pair, signal_type, "BLOCKED", reason, 105, indicators, {"obv": obv})
        return False, f"Gate 105 (OBV Divergence): {reason}"
    if obv["divergence"] == "BULLISH" and signal_type == "SELL":
        reason = f"OBV bullish divergence — {obv['note']}"
        await log_blackbox(pair, signal_type, "BLOCKED", reason, 105, indicators, {"obv": obv})
        return False, f"Gate 105 (OBV Divergence): {reason}"

    # Gate G: Liquidity Sweep
    sweep = detect_liquidity_sweep(df)
    if sweep["sweep_detected"]:
        logger.info(f"🎯 {pair} {sweep['note']}")
        if (sweep["sweep_type"] == "BEARISH_SWEEP" and signal_type == "SELL") or \
           (sweep["sweep_type"] == "BULLISH_SWEEP" and signal_type == "BUY"):
            logger.info(f"✅ {pair} Liquidity sweep CONFIRMS {signal_type} signal")

    # Gate H: Fair Value Gap
    fvg = detect_fair_value_gaps(df)
    if fvg["fvg_detected"] and fvg["nearest_fvg"]:
        fvg_type = fvg["nearest_fvg"]["type"]
        fvg_dist = fvg["nearest_fvg"]["distance"]
        logger.info(f"🔲 {pair} FVG detected: {fvg_type} dist={fvg_dist:.2f}")

    # Gate I: Gold-Silver Ratio
    gsr = await get_gold_silver_ratio()
    if gsr["extreme"] and signal_type == "BUY":
        logger.warning(f"⚠️ {pair} GSR={gsr['gsr']} extreme — reducing BUY conviction")

    # Gate J: VW-MACD confirmation
    vw = calculate_vw_macd(df)
    if vw["vw_direction"] != signal_type and vw["volume_backed"]:
        reason = f"VW-MACD={vw['vw_direction']} conflicts with signal={signal_type} (volume-backed)"
        await log_blackbox(pair, signal_type, "BLOCKED", reason, 106, indicators, {"vw_macd": vw})
        return False, f"Gate 106 (VW-MACD): {reason}"

    # All gates passed
    await log_blackbox(pair, signal_type, "PASSED", "All institutional gates cleared", 0, indicators, {
        "hurst": H, "entropy": entropy, "kc_zone": kc["kc_zone"],
        "obv": obv["divergence"], "sweep": sweep.get("sweep_type"),
        "fvg": fvg["fvg_detected"], "gsr": gsr.get("gsr"), "vw_dir": vw["vw_direction"],
    })
    return True, "All gates passed"


# ============ APP ============
scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(run_gold_signals,        "interval", minutes=SIGNAL_INTERVAL_MINUTES, id="gold_signals")
    scheduler.add_job(check_breakeven,         "interval", minutes=5,                        id="breakeven_monitor")
    scheduler.add_job(check_all_gold_outcomes, "interval", seconds=60,                       id="outcome_tracker")
    scheduler.add_job(update_trailing_stops,   "interval", minutes=5,                        id="trailing_stops")
    scheduler.start()
    logger.info(
        f"🥇 Gold Signals Server — Institutional Elite Edition\n"
        f"   Pairs     : {list(GOLD_PAIRS.keys())} | Interval: {SIGNAL_INTERVAL_MINUTES}min\n"
        f"   Min Score : {MIN_TECH_SCORE}/100 | High Conviction: {HIGH_CONVICTION_SCORE}/100\n"
        f"   Confidence: ≥{MIN_CONFIDENCE}% | Channel: {TELEGRAM_GOLD_CHANNEL}\n"
        f"   Core Gates: News Guard | DXY Corr | H4 MTF | Breakeven\n"
        f"   New Gates : Circuit Breaker | Session Filter | Entropy\n"
        f"               Hurst Exponent | Keltner | OBV Divergence\n"
        f"               Liquidity Sweep | FVG | Gold-Silver Ratio\n"
        f"               VW-MACD | Trailing Stop | Black Box Log\n"
        f"   Log Files : {BLACKBOX_LOG} | {DENIAL_LOG}"
    )
    asyncio.create_task(run_gold_signals())
    yield
    scheduler.shutdown()
    client.close()

app = FastAPI(title="Grandcom Gold Signals — Elite Edition", lifespan=lifespan)

@app.get("/api/health")
async def health():
    return {
        "status": "ok", "service": "gold_signals_elite",
        "pairs": list(GOLD_PAIRS.keys()),
        "min_score": MIN_TECH_SCORE, "high_conviction": HIGH_CONVICTION_SCORE,
        "throttle_h": THROTTLE_HOURS, "min_confidence": MIN_CONFIDENCE,
    }

@app.get("/api/gold/signals")
async def get_gold_signals(status: str = None, limit: int = 50):
    query = {"status": status.upper()} if status else {}
    signals = await db.gold_signals.find(query, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    return {"signals": signals, "count": len(signals)}

@app.get("/api/gold/stats")
async def get_gold_stats():
    total    = await db.gold_signals.count_documents({})
    active   = await db.gold_signals.count_documents({"status": "ACTIVE"})
    wins     = await db.gold_signals.count_documents({"result": "WIN"})
    losses   = await db.gold_signals.count_documents({"result": "LOSS"})
    closed   = wins + losses
    high_con = await db.gold_signals.count_documents({"conviction": "HIGH"})
    return {
        "total": total, "active": active, "wins": wins, "losses": losses,
        "win_rate_pct": round(wins/closed*100,1) if closed > 0 else 0,
        "high_conviction_signals": high_con,
        "throttle_state": {p: last_signal_time[p].isoformat() if p in last_signal_time else "never" for p in GOLD_PAIRS},
    }

@app.get("/api/gold/breakeven")
async def get_breakeven_signals():
    signals = await db.gold_signals.find(
        {"status": "ACTIVE", "breakeven_triggered": True},
        {"_id": 0, "pair": 1, "type": 1, "entry_price": 1, "tp_levels": 1, "sl_price": 1, "breakeven_price": 1}
    ).sort("breakeven_at", -1).limit(20).to_list(20)
    return {"breakeven_signals": signals, "count": len(signals)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8002)))

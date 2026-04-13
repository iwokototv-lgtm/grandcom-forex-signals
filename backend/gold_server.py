"""
Grandcom Gold Signals Server  —  Profitable Edition
====================================================
XAUUSD & XAUEUR  →  @grandcomgold Telegram channel
Railway deployment  |  litellm (no emergentintegrations)

PROFITABILITY STRATEGY
──────────────────────
1. 4H candles  — swing signals only, no noise
2. Regime enforcement  — UPTREND=BUY only, DOWNTREND=SELL only
3. Choppy market guard  — ADX + Bollinger Width + Choppiness Index
4. Per-pair throttle  — 6h minimum between signals (4H swing)
5. Drawdown protection  — stop after 2 losses / 40 pips, pause 12h
6. Confidence ≥ 70%  — strict AI quality gate
7. R:R ≥ 1.8  — minimum before gatekeeper approves
8. TP/SL sanity  — TP dist ≥ 8.0, SL dist ≥ 8.0, SL ≤ 150
9. Duplicate guard  — no same pair+direction if already ACTIVE
10. Single plain-text message  — TSCopier compatible, no HTML split
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
import litellm

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gold_server")

# ============ CONFIG ============
MONGO_URL              = os.environ.get("MONGO_URL")
DB_NAME                = os.environ.get("DB_NAME", "gold_signals")
TELEGRAM_BOT_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_GOLD_CHANNEL  = os.environ.get("TELEGRAM_GOLD_CHANNEL_ID", "@grandcomgold")
TWELVE_DATA_API_KEY    = os.environ.get("TWELVE_DATA_API_KEY")
OPENAI_API_KEY         = os.environ.get("OPENAI_API_KEY") or os.environ.get("EMERGENT_LLM_KEY")

# ── Signal frequency ─────────────────────────────────────────
SIGNAL_INTERVAL_MINUTES = 240   # Check every 4 hours (not 2 min)
THROTTLE_HOURS          = 6     # Minimum 6h between signals per pair

# ── Quality thresholds ────────────────────────────────────────
MIN_CONFIDENCE    = 70     # Minimum AI confidence
MIN_RR            = 1.8    # Minimum Risk:Reward
MIN_TP_DISTANCE   = 8.0    # Minimum TP distance in price units (Gold)
MIN_SL_DISTANCE   = 8.0    # Minimum SL distance in price units
MAX_SL_DISTANCE   = 150.0  # Maximum SL distance (prevents runaway risk)

# ── Drawdown protection ───────────────────────────────────────
MAX_DAILY_LOSSES  = 2
MAX_DAILY_PIPS    = 40
PAUSE_HOURS       = 12

# ── Per-pair state ────────────────────────────────────────────
last_signal_time: dict  = {}    # {pair: datetime}
daily_losses:     dict  = {}    # {pair_date: {losses, pips, paused_until}}

# ── Gold pair parameters ──────────────────────────────────────
GOLD_PAIRS = {
    "XAUUSD": {
        "twelve_data_symbol": "XAU/USD",
        "pip_value":          0.10,
        "decimal_places":     2,
        "atr_multiplier_sl":  1.8,    # slightly wider — Gold is volatile
        "atr_multiplier_tp1": 2.5,
        "atr_multiplier_tp2": 4.0,
        "atr_multiplier_tp3": 6.0,
        "min_rr":             1.8,
        "min_confidence":     70,
    },
    "XAUEUR": {
        "twelve_data_symbol": "XAU/EUR",
        "pip_value":          0.10,
        "decimal_places":     2,
        "atr_multiplier_sl":  1.8,
        "atr_multiplier_tp1": 2.5,
        "atr_multiplier_tp2": 4.0,
        "atr_multiplier_tp3": 6.0,
        "min_rr":             1.8,
        "min_confidence":     70,
    },
}

# ============ DB ============
client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]

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

# ============ TECHNICAL INDICATORS ============
def calculate_indicators(df: pd.DataFrame, params: dict) -> dict | None:
    try:
        if len(df) < 50:
            return None
        df = df.copy()
        df["rsi"]        = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
        macd             = ta.trend.MACD(df["close"])
        df["macd"]       = macd.macd()
        df["macd_signal"]= macd.macd_signal()
        df["ma_20"]      = ta.trend.SMAIndicator(df["close"], window=20).sma_indicator()
        df["ma_50"]      = ta.trend.SMAIndicator(df["close"], window=50).sma_indicator()
        df["ema_50"]     = ta.trend.EMAIndicator(df["close"], window=50).ema_indicator()
        bb               = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
        df["bb_upper"]   = bb.bollinger_hband()
        df["bb_lower"]   = bb.bollinger_lband()
        df["bb_width"]   = (df["bb_upper"] - df["bb_lower"]) / df["close"] * 100
        adx_ind          = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14)
        df["adx"]        = adx_ind.adx()
        df["adx_pos"]    = adx_ind.adx_pos()
        df["adx_neg"]    = adx_ind.adx_neg()
        atr_ind          = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14)
        df["atr"]        = atr_ind.average_true_range()

        latest = df.iloc[-1]
        prev   = df.iloc[-2]
        dp     = params["decimal_places"]

        close  = float(latest["close"])
        rsi    = float(latest["rsi"])
        adx    = float(latest["adx"])
        adx_p  = float(latest["adx_pos"])
        adx_n  = float(latest["adx_neg"])
        macd_v = float(latest["macd"])
        macd_s = float(latest["macd_signal"])
        ma50   = float(latest["ma_50"])
        bb_up  = float(latest["bb_upper"])
        bb_lo  = float(latest["bb_lower"])
        bb_w   = float(latest["bb_width"])
        atr    = float(latest["atr"])

        # ── Regime detection ─────────────────────────────────
        # Uses ADX (trend strength) + price vs MA50 + DMI
        if adx >= 25:
            if adx_p > adx_n and close > ma50:
                regime = "UPTREND"
            elif adx_n > adx_p and close < ma50:
                regime = "DOWNTREND"
            else:
                regime = "TRANSITIONING"
        else:
            regime = "RANGE"

        # ── Choppy market score ───────────────────────────────
        # 3 signals needed to mark as choppy
        chop_signals = 0
        chop_reasons = []

        n = 14
        high_n    = df["high"].rolling(n).max()
        low_n     = df["low"].rolling(n).min()
        atr_1     = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=1).average_true_range()
        atr_sum_n = atr_1.rolling(n).sum()
        hl_range  = high_n - low_n
        chop_arr  = np.where(hl_range > 0, 100.0 * np.log10(atr_sum_n / hl_range) / np.log10(n), 50.0)
        chop_idx  = float(pd.Series(chop_arr).iloc[-1])

        if chop_idx > 61.8:
            chop_signals += 1
            chop_reasons.append(f"ChopIdx={chop_idx:.1f}>61.8")
        if bb_w < 0.8:   # tighter for Gold — normal BB width is wider
            chop_signals += 1
            chop_reasons.append(f"BB_width={bb_w:.2f}%<0.8%")
        if adx < 20:
            chop_signals += 1
            chop_reasons.append(f"ADX={adx:.1f}<20")

        is_choppy   = chop_signals >= 2
        chop_reason = ", ".join(chop_reasons) if chop_reasons else "OK"

        trend = "BULLISH" if close > ma50 else "BEARISH"

        return {
            "current_price": round(close, dp),
            "rsi":           round(rsi, 2),
            "macd":          round(macd_v, 6),
            "macd_signal":   round(macd_s, 6),
            "ma_50":         round(ma50, dp),
            "bb_upper":      round(bb_up, dp),
            "bb_lower":      round(bb_lo, dp),
            "bb_width":      round(bb_w, 2),
            "atr":           round(atr, dp),
            "adx":           round(adx, 2),
            "adx_pos":       round(adx_p, 2),
            "adx_neg":       round(adx_n, 2),
            "trend":         trend,
            "regime":        regime,
            "is_choppy":     is_choppy,
            "chop_reason":   chop_reason,
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
    else:
        rec["paused_until"] = None
    if rec["losses"] >= MAX_DAILY_LOSSES:
        rec["paused_until"] = datetime.utcnow() + timedelta(hours=PAUSE_HOURS)
        return False, f"Max daily losses ({rec['losses']}) hit — paused {PAUSE_HOURS}h"
    if rec["pips"] >= MAX_DAILY_PIPS:
        rec["paused_until"] = datetime.utcnow() + timedelta(hours=PAUSE_HOURS)
        return False, f"Max daily pips ({rec['pips']:.1f}) hit — paused {PAUSE_HOURS}h"
    return True, ""

def record_loss(pair: str, pips: float = 10.0):
    today = datetime.utcnow().date().isoformat()
    key   = f"{pair}_{today}"
    if key not in daily_losses:
        daily_losses[key] = {"losses": 0, "pips": 0.0, "paused_until": None}
    daily_losses[key]["losses"] += 1
    daily_losses[key]["pips"]   += abs(pips)

# ============ SIGNAL VALIDATION ============
def validate_gold_signal(
    signal_type: str,
    entry: float,
    sl: float,
    tp_levels: list,
    confidence: float,
    params: dict,
) -> tuple[bool, str]:
    """
    Full pre-send validation:
      1. Direction structure (BUY: TP > entry > SL, SELL: TP < entry < SL)
      2. TP distance ≥ MIN_TP_DISTANCE
      3. SL distance ≥ MIN_SL_DISTANCE and ≤ MAX_SL_DISTANCE
      4. R:R ≥ MIN_RR (using furthest TP)
      5. Confidence ≥ MIN_CONFIDENCE
    """
    if not tp_levels or len(tp_levels) < 3:
        return False, "Less than 3 TP levels"

    final_tp  = tp_levels[-1]
    tp_dist   = abs(final_tp - entry)
    sl_dist   = abs(entry - sl)

    if signal_type == "BUY":
        if not (final_tp > entry > sl):
            return False, f"Invalid BUY structure: entry={entry} TP3={final_tp} SL={sl}"
    elif signal_type == "SELL":
        if not (final_tp < entry < sl):
            return False, f"Invalid SELL structure: entry={entry} TP3={final_tp} SL={sl}"
    else:
        return False, f"Unknown signal type: {signal_type}"

    if tp_dist < MIN_TP_DISTANCE:
        return False, f"TP too small: {tp_dist:.2f} (min {MIN_TP_DISTANCE})"
    if sl_dist < MIN_SL_DISTANCE:
        return False, f"SL too small: {sl_dist:.2f} (min {MIN_SL_DISTANCE})"
    if sl_dist > MAX_SL_DISTANCE:
        return False, f"SL too wide: {sl_dist:.2f} (max {MAX_SL_DISTANCE})"

    rr = tp_dist / sl_dist if sl_dist > 0 else 0
    if rr < MIN_RR:
        return False, f"R:R too low: {rr:.2f} (min {MIN_RR})"

    if confidence < MIN_CONFIDENCE:
        return False, f"Confidence too low: {confidence:.1f}% (min {MIN_CONFIDENCE}%)"

    return True, f"Valid — R:R={rr:.2f} conf={confidence:.0f}%"

# ============ AI ANALYSIS ============
async def generate_ai_analysis(symbol: str, indicators: dict, params: dict) -> dict | None:
    try:
        rsi    = indicators["rsi"]
        regime = indicators["regime"]

        # Only ask AI for the regime-consistent direction
        if regime == "UPTREND":
            direction_instruction = (
                "The market is in an UPTREND (ADX>25, +DI>-DI, price>MA50). "
                "You MUST output BUY. Only output NEUTRAL if there is extremely strong bearish reversal evidence."
            )
        elif regime == "DOWNTREND":
            direction_instruction = (
                "The market is in a DOWNTREND (ADX>25, -DI>+DI, price<MA50). "
                "You MUST output SELL. Only output NEUTRAL if there is extremely strong bullish reversal evidence."
            )
        else:
            direction_instruction = (
                "The market regime is unclear (RANGE or TRANSITIONING). "
                "Output NEUTRAL unless you see a very strong setup."
            )

        system_message = (
            "You are an elite institutional gold trader. "
            "Your signals are based on ATR-derived swing targets. "
            "You follow trend direction strictly. "
            "Respond with valid JSON only — no markdown, no extra text."
        )

        prompt = f"""
Analyze {symbol} and provide a gold swing trading signal.

=== MARKET DATA (4H CANDLES) ===
Current Price: {indicators['current_price']}
RSI: {rsi:.2f} | MACD: {indicators['macd']:.4f} | MACD Signal: {indicators['macd_signal']:.4f}
MA50: {indicators['ma_50']} | BB Upper: {indicators['bb_upper']} | BB Lower: {indicators['bb_lower']}
ATR(14): {indicators['atr']} | ADX: {indicators['adx']} | +DI: {indicators['adx_pos']} | -DI: {indicators['adx_neg']}

=== REGIME INSTRUCTION ===
{direction_instruction}

=== ATR SWING TARGETS ===
SL multiplier : {params['atr_multiplier_sl']}  → SL distance ≈ {round(indicators['atr'] * params['atr_multiplier_sl'], 2)}
TP1 multiplier: {params['atr_multiplier_tp1']} → TP1 distance ≈ {round(indicators['atr'] * params['atr_multiplier_tp1'], 2)}
TP2 multiplier: {params['atr_multiplier_tp2']} → TP2 distance ≈ {round(indicators['atr'] * params['atr_multiplier_tp2'], 2)}
TP3 multiplier: {params['atr_multiplier_tp3']} → TP3 distance ≈ {round(indicators['atr'] * params['atr_multiplier_tp3'], 2)}
Min R:R: {params['min_rr']}

=== RULES ===
- BUY: all TP levels ABOVE entry, SL BELOW entry
- SELL: all TP levels BELOW entry, SL ABOVE entry
- Use ATR multipliers above to calculate exact price levels
- confidence = your conviction 0-100

=== OUTPUT FORMAT (JSON ONLY) ===
{{"signal":"BUY"or"SELL"or"NEUTRAL","confidence":0-100,"entry_price":numeric,"tp_levels":[tp1,tp2,tp3],"sl_price":numeric,"analysis":"<100 words","risk_reward":numeric}}
"""

        ai_response = None
        for attempt in range(3):
            try:
                response = await litellm.acompletion(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system_message},
                        {"role": "user",   "content": prompt},
                    ],
                    api_key=OPENAI_API_KEY,
                )
                ai_response = response.choices[0].message.content
                if ai_response and len(ai_response.strip()) > 10:
                    break
            except Exception as e:
                logger.warning(f"LLM attempt {attempt+1}/3 for {symbol}: {e}")
                await asyncio.sleep(2)

        if not ai_response:
            return None

        # Parse JSON
        raw = ai_response.strip()
        fence = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', raw, re.DOTALL)
        if fence:
            raw = fence.group(1).strip()
        if not raw.startswith('{'):
            brace = re.search(r'\{.*\}', raw, re.DOTALL)
            if brace:
                raw = brace.group(0)

        ai_data = None
        for i in range(3):
            try:
                if i == 0:
                    ai_data = json.loads(raw)
                elif i == 1:
                    fixed = re.sub(r',\s*([}\]])', r'\1', raw)
                    fixed = fixed.replace("'", '"')
                    ai_data = json.loads(fixed)
                else:
                    sig  = re.search(r'"signal"\s*:\s*"(\w+)"', raw)
                    conf = re.search(r'"confidence"\s*:\s*([\d.]+)', raw)
                    entr = re.search(r'"entry_price"\s*:\s*([\d.]+)', raw)
                    ai_data = {
                        "signal":      sig.group(1)  if sig  else "NEUTRAL",
                        "confidence":  float(conf.group(1)) if conf else 50.0,
                        "entry_price": float(entr.group(1)) if entr else indicators['current_price'],
                        "tp_levels":   [], "sl_price": 0,
                        "analysis":    "AI analysis unavailable",
                    }
                break
            except Exception:
                pass

        if not ai_data:
            logger.error(f"Failed to parse AI response for {symbol}")
            return None

        # ── ATR override — always recalculate TP/SL from ATR ──────
        # Never trust AI-generated price levels for Gold — override
        entry  = ai_data.get("entry_price", indicators["current_price"])
        sig    = ai_data.get("signal", "NEUTRAL")
        atr    = indicators["atr"]
        dp     = params["decimal_places"]

        if sig == "BUY":
            tp_levels = [
                round(entry + atr * params["atr_multiplier_tp1"], dp),
                round(entry + atr * params["atr_multiplier_tp2"], dp),
                round(entry + atr * params["atr_multiplier_tp3"], dp),
            ]
            sl_price  = round(entry - atr * params["atr_multiplier_sl"], dp)
        elif sig == "SELL":
            tp_levels = [
                round(entry - atr * params["atr_multiplier_tp1"], dp),
                round(entry - atr * params["atr_multiplier_tp2"], dp),
                round(entry - atr * params["atr_multiplier_tp3"], dp),
            ]
            sl_price  = round(entry + atr * params["atr_multiplier_sl"], dp)
        else:
            return None  # NEUTRAL — no trade

        ai_data["tp_levels"] = tp_levels
        ai_data["sl_price"]  = sl_price

        # Recalculate R:R from actual ATR distances
        tp_dist  = abs(tp_levels[2] - entry)
        sl_dist  = abs(entry - sl_price)
        ai_data["risk_reward"] = round(tp_dist / sl_dist, 2) if sl_dist > 0 else params["min_rr"]

        logger.info(
            f"🪙 {symbol} ATR-override: entry={entry} SL={sl_price} "
            f"TP={tp_levels} ATR={atr:.2f} R:R={ai_data['risk_reward']}"
        )
        return ai_data

    except Exception as e:
        logger.error(f"Error in generate_ai_analysis for {symbol}: {e}")
        return None

# ============ TELEGRAM  (single plain-text message) ============
async def send_signal_to_telegram(
    pair: str, signal_type: str, entry_price: float,
    tp_levels: list, sl_price: float, confidence: float,
    risk_reward: float, regime: str, analysis: str,
):
    try:
        if not TELEGRAM_BOT_TOKEN:
            logger.warning("No Telegram bot token")
            return

        bot    = Bot(token=TELEGRAM_BOT_TOKEN)
        emoji  = "🟢" if signal_type == "BUY" else "🔴"
        action = signal_type.capitalize()

        # ±0.50 entry range for TSCopier Smart Entry Mode
        entry_lo = round(entry_price - 0.50, 2)
        entry_hi = round(entry_price + 0.50, 2)

        regime_emoji = "📈" if regime == "UPTREND" else "📉" if regime == "DOWNTREND" else "⚡"

        # ONE plain-text message — TSCopier reads top block, humans read all
        message = (
            f"{emoji} {pair} {signal_type}\n"
            f"\n"
            f"{action} {entry_lo} - {entry_hi}\n"
            f"\n"
            f"TP1: {tp_levels[0]}\n"
            f"TP2: {tp_levels[1]}\n"
            f"TP3: {tp_levels[2]}\n"
            f"\n"
            f"SL: {sl_price}\n"
            f"\n"
            f"----------------------------\n"
            f"{regime_emoji} {regime} | SWING\n"
            f"R:R: 1:{risk_reward} | Conf: {confidence}%\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Grandcom Gold EA\n"
        )

        await bot.send_message(chat_id=TELEGRAM_GOLD_CHANNEL, text=message)
        logger.info(f"✅ Gold signal sent → {TELEGRAM_GOLD_CHANNEL}: {pair} {signal_type}")

    except Exception as e:
        logger.error(f"❌ Telegram send error: {e}")

# ============ DUPLICATE GUARD ============
async def is_duplicate_active(pair: str, signal_type: str) -> bool:
    """Returns True if same pair+direction is already ACTIVE in DB."""
    existing = await db.gold_signals.find_one({
        "pair":   pair,
        "type":   signal_type,
        "status": "ACTIVE",
    })
    return existing is not None

# ============ MAIN SIGNAL GENERATION ============
async def generate_gold_signal(pair: str):
    try:
        params = GOLD_PAIRS[pair]
        logger.info(f"🥇 Generating signal for {pair}")

        # ── 1. Throttle check ─────────────────────────────────
        last_ts = last_signal_time.get(pair)
        if last_ts:
            elapsed_hours = (datetime.utcnow() - last_ts).total_seconds() / 3600
            if elapsed_hours < THROTTLE_HOURS:
                remaining = round(THROTTLE_HOURS - elapsed_hours, 1)
                logger.info(f"⏳ {pair} throttled — {elapsed_hours:.1f}h since last, wait {remaining}h more")
                return

        # ── 2. Drawdown protection ────────────────────────────
        can_trade, dd_reason = check_drawdown(pair)
        if not can_trade:
            logger.warning(f"🛑 {pair} drawdown pause: {dd_reason}")
            return

        # ── 3. Fetch 4H price data ────────────────────────────
        df = await get_price_data(pair, interval="4h", outputsize=120)
        if df is None or len(df) < 50:
            logger.warning(f"Insufficient data for {pair}")
            return

        # ── 4. Calculate indicators + regime ─────────────────
        indicators = calculate_indicators(df, params)
        if not indicators:
            return

        regime = indicators["regime"]
        logger.info(f"📊 {pair} Regime={regime} ADX={indicators['adx']:.1f} Choppy={indicators['is_choppy']} ({indicators['chop_reason']})")

        # ── 5. Regime gate — RANGE and TRANSITIONING blocked ──
        if regime in ("RANGE", "TRANSITIONING"):
            logger.info(f"⛔ {pair} skipped — {regime} market (no clean trend)")
            return

        # ── 6. Choppy market gate ─────────────────────────────
        if indicators["is_choppy"]:
            logger.info(f"📉 {pair} skipped — choppy: {indicators['chop_reason']}")
            return

        # ── 7. AI analysis ────────────────────────────────────
        ai_analysis = await generate_ai_analysis(pair, indicators, params)
        if not ai_analysis:
            logger.info(f"No AI signal for {pair}")
            return

        signal_type = ai_analysis.get("signal", "NEUTRAL")
        if signal_type == "NEUTRAL":
            logger.info(f"No trade signal for {pair} (NEUTRAL)")
            return

        # ── 8. Regime direction enforcement ──────────────────
        if regime == "UPTREND" and signal_type != "BUY":
            logger.info(f"📈 {pair} REJECTED — UPTREND=BUY only (got {signal_type})")
            return
        if regime == "DOWNTREND" and signal_type != "SELL":
            logger.info(f"📉 {pair} REJECTED — DOWNTREND=SELL only (got {signal_type})")
            return

        confidence  = float(ai_analysis.get("confidence", 0))
        entry_price = ai_analysis["entry_price"]
        tp_levels   = ai_analysis["tp_levels"]
        sl_price    = ai_analysis["sl_price"]
        risk_reward = ai_analysis.get("risk_reward", params["min_rr"])
        analysis    = ai_analysis.get("analysis", "")

        # ── 9. Full signal validation ─────────────────────────
        valid, reason = validate_gold_signal(
            signal_type, entry_price, sl_price, tp_levels, confidence, params
        )
        if not valid:
            logger.warning(f"🚫 {pair} validation failed: {reason}")
            return
        logger.info(f"✅ {pair} validation passed: {reason}")

        # ── 10. Duplicate guard ───────────────────────────────
        if await is_duplicate_active(pair, signal_type):
            logger.info(f"⚠️ {pair} {signal_type} already ACTIVE — skipping duplicate")
            return

        # ── 11. Save to DB ────────────────────────────────────
        signal_doc = {
            "pair":          pair,
            "type":          signal_type,
            "entry_price":   entry_price,
            "current_price": indicators["current_price"],
            "tp_levels":     tp_levels,
            "sl_price":      sl_price,
            "confidence":    round(confidence, 1),
            "analysis":      analysis,
            "risk_reward":   risk_reward,
            "timeframe":     "4H",
            "regime":        regime,
            "adx":           indicators["adx"],
            "atr":           indicators["atr"],
            "status":        "ACTIVE",
            "created_at":    datetime.now(timezone.utc),
        }
        await db.gold_signals.insert_one(signal_doc)

        # ── 12. Record throttle time ──────────────────────────
        last_signal_time[pair] = datetime.utcnow()

        # ── 13. Send to Telegram ──────────────────────────────
        await send_signal_to_telegram(
            pair=pair, signal_type=signal_type,
            entry_price=entry_price, tp_levels=tp_levels,
            sl_price=sl_price, confidence=round(confidence, 1),
            risk_reward=risk_reward, regime=regime, analysis=analysis,
        )

        logger.info(
            f"🏆 {pair} {signal_type} @ {entry_price} | "
            f"TP={tp_levels} | SL={sl_price} | "
            f"R:R={risk_reward} | Conf={confidence:.0f}% | Regime={regime}"
        )

    except Exception as e:
        logger.error(f"Error generating gold signal for {pair}: {e}")

async def run_gold_signals():
    logger.info("🥇 Gold signal cycle starting...")
    for pair in GOLD_PAIRS:
        await generate_gold_signal(pair)
        await asyncio.sleep(5)   # small gap between pairs
    logger.info("🥇 Gold signal cycle complete")

# ============ APP ============
scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(
        run_gold_signals, "interval",
        minutes=SIGNAL_INTERVAL_MINUTES,
        id="gold_signals",
    )
    scheduler.start()
    logger.info(
        f"🥇 Gold Signals Server started\n"
        f"   Pairs:      {list(GOLD_PAIRS.keys())}\n"
        f"   Interval:   every {SIGNAL_INTERVAL_MINUTES} min\n"
        f"   Throttle:   {THROTTLE_HOURS}h min between signals\n"
        f"   Confidence: ≥{MIN_CONFIDENCE}%\n"
        f"   Min R:R:    {MIN_RR}\n"
        f"   Channel:    {TELEGRAM_GOLD_CHANNEL}\n"
    )
    asyncio.create_task(run_gold_signals())
    yield
    scheduler.shutdown()
    client.close()

app = FastAPI(title="Grandcom Gold Signals — Profitable Edition", lifespan=lifespan)

@app.get("/api/health")
async def health():
    return {
        "status":     "ok",
        "service":    "gold_signals",
        "pairs":      list(GOLD_PAIRS.keys()),
        "channel":    TELEGRAM_GOLD_CHANNEL,
        "throttle_h": THROTTLE_HOURS,
        "min_conf":   MIN_CONFIDENCE,
        "min_rr":     MIN_RR,
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
    total   = await db.gold_signals.count_documents({})
    active  = await db.gold_signals.count_documents({"status": "ACTIVE"})
    wins    = await db.gold_signals.count_documents({"status": "WIN"})
    losses  = await db.gold_signals.count_documents({"status": "LOSS"})
    closed  = wins + losses
    win_rate = round(wins / closed * 100, 1) if closed > 0 else 0
    return {
        "total": total, "active": active,
        "wins": wins, "losses": losses,
        "win_rate_pct": win_rate,
        "throttle_state": {
            p: last_signal_time[p].isoformat() if p in last_signal_time else "never"
            for p in GOLD_PAIRS
        },
        "drawdown_state": {
            k: v for k, v in daily_losses.items()
            if datetime.utcnow().date().isoformat() in k
        },
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8002)))

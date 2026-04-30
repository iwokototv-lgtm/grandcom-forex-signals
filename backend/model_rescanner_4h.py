"""
4-Hour Model Rescanner
Runs every 4 hours to adapt trading models to current market conditions.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
import pandas as pd
import ta
from telegram import Bot
from dotenv import load_dotenv

load_dotenv()

# Configuration
MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME", "grandcom_signals")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "@grandcomsignals")
TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY")

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database
client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]


# ============ MARKET ANALYSIS ============

async def get_price_data(symbol: str, interval: str = "1h", outputsize: int = 100) -> pd.DataFrame:
    """Fetch price data from Twelve Data API."""
    try:
        import aiohttp
        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "apikey": TWELVE_DATA_API_KEY,
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if "values" in data:
                        df = pd.DataFrame(data["values"])
                        df["close"] = df["close"].astype(float)
                        df["high"] = df["high"].astype(float)
                        df["low"] = df["low"].astype(float)
                        df["volume"] = df["volume"].astype(float)
                        return df
    except Exception as e:
        logger.error(f"Error fetching {symbol} data: {e}")
    return None


def analyze_market_regime(df: pd.DataFrame) -> dict:
    """Analyze current market regime: TRENDING, CHOPPY, RANGING"""
    if df is None or len(df) < 20:
        return {"regime": "UNKNOWN", "confidence": 0.0, "adx": 0.0, "atr": 0.0}
    
    try:
        adx = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14).adx()
        current_adx = float(adx.iloc[-1])
        
        atr = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
        current_atr = float(atr.iloc[-1])
        atr_avg = float(atr.tail(20).mean())
        
        if current_adx > 30:
            regime = "TRENDING"
            confidence = min(100, (current_adx - 30) * 2)
        elif current_adx < 20:
            regime = "CHOPPY"
            confidence = min(100, (20 - current_adx) * 2)
        else:
            regime = "RANGING"
            confidence = 50.0
        
        return {
            "regime": regime,
            "confidence": confidence,
            "adx": current_adx,
            "atr": current_atr,
            "volatility_ratio": current_atr / atr_avg if atr_avg > 0 else 1.0,
        }
    except Exception as e:
        logger.error(f"Error analyzing regime: {e}")
        return {"regime": "UNKNOWN", "confidence": 0.0, "adx": 0.0, "atr": 0.0}


def analyze_volatility_shift(df: pd.DataFrame) -> dict:
    """Detect volatility shifts: HIGH, LOW, NORMAL"""
    if df is None or len(df) < 50:
        return {"volatility_level": "NORMAL", "shift": 0.0, "current_atr": 0.0}
    
    try:
        atr = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
        current_atr = float(atr.iloc[-1])
        atr_20d_avg = float(atr.tail(20).mean())
        atr_50d_avg = float(atr.tail(50).mean())
        
        shift = (current_atr - atr_50d_avg) / atr_50d_avg if atr_50d_avg > 0 else 0.0
        
        if current_atr > atr_20d_avg * 1.3:
            level = "HIGH"
        elif current_atr < atr_20d_avg * 0.7:
            level = "LOW"
        else:
            level = "NORMAL"
        
        return {
            "volatility_level": level,
            "shift": shift,
            "current_atr": current_atr,
            "avg_atr_20d": atr_20d_avg,
            "avg_atr_50d": atr_50d_avg,
        }
    except Exception as e:
        logger.error(f"Error analyzing volatility: {e}")
        return {"volatility_level": "NORMAL", "shift": 0.0, "current_atr": 0.0}


def analyze_correlation_shift(df_gold: pd.DataFrame, df_dxy: pd.DataFrame) -> dict:
    """Detect correlation shifts between Gold and DXY"""
    if df_gold is None or df_dxy is None or len(df_gold) < 20 or len(df_dxy) < 20:
        return {"correlation": 0.0, "strength": "UNKNOWN", "direction": "UNKNOWN"}
    
    try:
        gold_returns = df_gold["close"].pct_change().dropna()
        dxy_returns = df_dxy["close"].pct_change().dropna()
        
        min_len = min(len(gold_returns), len(dxy_returns))
        if min_len > 0:
            correlation = gold_returns.tail(min_len).corr(dxy_returns.tail(min_len))
        else:
            correlation = 0.0
        
        if abs(correlation) > 0.7:
            strength = "STRONG"
        elif abs(correlation) > 0.4:
            strength = "MODERATE"
        else:
            strength = "WEAK"
        
        return {
            "correlation": correlation,
            "strength": strength,
            "direction": "INVERSE" if correlation < 0 else "DIRECT",
        }
    except Exception as e:
        logger.error(f"Error analyzing correlation: {e}")
        return {"correlation": 0.0, "strength": "UNKNOWN", "direction": "UNKNOWN"}


# ============ MODEL ADAPTATION ============

async def update_model_parameters(analysis: dict) -> dict:
    """Update trading model parameters based on market analysis."""
    updates = {
        "timestamp": datetime.now(timezone.utc),
        "regime": analysis.get("regime", {}).get("regime", "UNKNOWN"),
        "volatility_level": analysis.get("volatility", {}).get("volatility_level", "NORMAL"),
        "correlation_strength": analysis.get("correlation", {}).get("strength", "UNKNOWN"),
    }
    
    regime = analysis.get("regime", {}).get("regime", "UNKNOWN")
    if regime == "TRENDING":
        updates["min_confidence"] = 65
        updates["position_size_multiplier"] = 1.2
    elif regime == "CHOPPY":
        updates["min_confidence"] = 75
        updates["position_size_multiplier"] = 0.8
    else:
        updates["min_confidence"] = 70
        updates["position_size_multiplier"] = 1.0
    
    volatility = analysis.get("volatility", {}).get("volatility_level", "NORMAL")
    if volatility == "HIGH":
        updates["sl_multiplier"] = 1.3
        updates["tp_multiplier"] = 1.2
    elif volatility == "LOW":
        updates["sl_multiplier"] = 0.8
        updates["tp_multiplier"] = 0.9
    else:
        updates["sl_multiplier"] = 1.0
        updates["tp_multiplier"] = 1.0
    
    correlation = analysis.get("correlation", {}).get("strength", "UNKNOWN")
    if correlation == "STRONG":
        updates["dxy_filter_enabled"] = True
        updates["dxy_filter_strictness"] = "HIGH"
    elif correlation == "WEAK":
        updates["dxy_filter_enabled"] = False
        updates["dxy_filter_strictness"] = "NONE"
    else:
        updates["dxy_filter_enabled"] = True
        updates["dxy_filter_strictness"] = "MEDIUM"
    
    return updates


# ============ TELEGRAM ALERTS ============

async def send_rescan_alert(analysis: dict, updates: dict):
    """Send 4-hour rescan alert to Telegram."""
    try:
        if not TELEGRAM_BOT_TOKEN:
            return
        
        regime = analysis.get("regime", {})
        volatility = analysis.get("volatility", {})
        correlation = analysis.get("correlation", {})
        
        msg = (
            f"📊 <b>4-HOUR MODEL RESCAN</b>\n"
            f"\n"
            f"<b>Market Regime:</b> {regime.get('regime', 'UNKNOWN')} "
            f"(ADX: {regime.get('adx', 0):.1f})\n"
            f"<b>Volatility:</b> {volatility.get('volatility_level', 'NORMAL')} "
            f"(ATR shift: {volatility.get('shift', 0)*100:.1f}%)\n"
            f"<b>Gold-DXY Correlation:</b> {correlation.get('strength', 'UNKNOWN')} "
            f"({correlation.get('correlation', 0):.2f})\n"
            f"\n"
            f"<b>Model Updates:</b>\n"
            f"• Min Confidence: {updates.get('min_confidence', 70)}%\n"
            f"• Position Size: {updates.get('position_size_multiplier', 1.0)}×\n"
            f"• SL Multiplier: {updates.get('sl_multiplier', 1.0)}×\n"
            f"• DXY Filter: {updates.get('dxy_filter_enabled', True)} "
            f"({updates.get('dxy_filter_strictness', 'MEDIUM')})\n"
            f"\n"
            f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Grandcom Model Rescanner\n"
        )
        
        async with Bot(token=TELEGRAM_BOT_TOKEN) as bot:
            await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=msg, parse_mode="HTML")
        
        logger.info("✅ Rescan alert sent to Telegram")
    except Exception as e:
        logger.error(f"Error sending Telegram alert: {e}")


# ============ MAIN RESCAN FUNCTION ============

async def run_4h_rescan():
    """Main 4-hour rescan function."""
    logger.info("🔄 Starting 4-hour model rescan...")
    
    try:
        logger.info("📊 Fetching market data...")
        df_gold = await get_price_data("XAU/USD", interval="1h", outputsize=100)
        df_dxy = await get_price_data("DXY", interval="1h", outputsize=100)
        
        if df_gold is None:
            logger.error("Failed to fetch Gold data")
            return
        
        logger.info("🔍 Analyzing market conditions...")
        regime_analysis = analyze_market_regime(df_gold)
        volatility_analysis = analyze_volatility_shift(df_gold)
        correlation_analysis = analyze_correlation_shift(df_gold, df_dxy) if df_dxy is not None else {"correlation": 0.0, "strength": "UNKNOWN"}
        
        analysis = {
            "regime": regime_analysis,
            "volatility": volatility_analysis,
            "correlation": correlation_analysis,
        }
        
        logger.info(f"📈 Regime: {regime_analysis['regime']} (ADX: {regime_analysis['adx']:.1f})")
        logger.info(f"📊 Volatility: {volatility_analysis['volatility_level']} (shift: {volatility_analysis['shift']*100:.1f}%)")
        logger.info(f"🔗 Correlation: {correlation_analysis['strength']} ({correlation_analysis['correlation']:.2f})")
        
        logger.info("🔧 Updating model parameters...")
        updates = await update_model_parameters(analysis)
        
        logger.info("💾 Storing rescan results...")
        rescan_doc = {
            "timestamp": datetime.now(timezone.utc),
            "analysis": analysis,
            "updates": updates,
            "type": "4H_RESCAN",
        }
        await db.model_rescans.insert_one(rescan_doc)
        
        logger.info("📱 Sending Telegram alert...")
        await send_rescan_alert(analysis, updates)
        
        logger.info("✅ 4-hour rescan complete!")
        
    except Exception as e:
        logger.error(f"❌ Error in 4-hour rescan: {e}", exc_info=True)


# ============ ENTRY POINT ============

if __name__ == "__main__":
    logger.info("🚀 4-Hour Model Rescanner started")
    asyncio.run(run_4h_rescan())
    logger.info("✅ Rescan cycle complete")

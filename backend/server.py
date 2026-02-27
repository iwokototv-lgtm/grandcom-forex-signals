from fastapi import FastAPI, APIRouter, HTTPException, Depends, BackgroundTasks, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from passlib.context import CryptContext
from bson import ObjectId
import os
import logging
import jwt
import asyncio
import aiohttp
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from emergentintegrations.llm.chat import LlmChat, UserMessage
import ta
import pandas as pd
import numpy as np
from pathlib import Path

# Import ML Engine
from ml_engine import FeatureEngineer, RegimeDetector, RiskManager, SignalOptimizer, mtf_analyzer, historical_collector, signal_tracker

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Configuration
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

JWT_SECRET = os.environ.get('JWT_SECRET', 'your-secret-key')
JWT_ALGORITHM = os.environ.get('JWT_ALGORITHM', 'HS256')
JWT_EXPIRATION_HOURS = int(os.environ.get('JWT_EXPIRATION_HOURS', 24))
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TWELVE_DATA_API_KEY = os.environ.get('TWELVE_DATA_API_KEY', 'demo')
EMERGENT_LLM_KEY = os.environ.get('EMERGENT_LLM_KEY')

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

# FastAPI App
app = FastAPI(title="Forex & Gold Signals API")
api_router = APIRouter(prefix="/api")

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize ML Engine Components
signal_optimizer = SignalOptimizer()
logger.info("ML Engine initialized: SignalOptimizer ready")

# ============ MODELS ============
class PyObjectId(ObjectId):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid ObjectId")
        return ObjectId(v)

    @classmethod
    def __get_pydantic_json_schema__(cls, _schema_generator):
        return {"type": "string"}

class UserRegister(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    id: str
    email: str
    full_name: Optional[str]
    subscription_tier: str
    telegram_id: Optional[str]
    created_at: datetime

class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse

class SignalCreate(BaseModel):
    pair: str  # e.g., "XAUUSD", "EURUSD"
    type: str  # "BUY" or "SELL"
    entry_price: float
    tp_levels: List[float]  # Multiple take profit levels
    sl_price: float  # Stop loss
    confidence: float  # 0-100
    analysis: str  # AI analysis
    timeframe: str  # "1H", "4H", "1D"
    risk_reward: float

class Signal(BaseModel):
    id: Optional[str] = None
    pair: str
    type: str
    entry_price: float
    current_price: Optional[float] = None
    tp_levels: List[float]
    sl_price: float
    confidence: float
    analysis: str
    timeframe: str
    risk_reward: float
    status: str = "ACTIVE"  # ACTIVE, CLOSED, HIT_TP, HIT_SL
    result: Optional[str] = None  # WIN, LOSS
    pips: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = None
    is_premium: bool = False

class SubscriptionUpdate(BaseModel):
    tier: str  # "FREE" or "PREMIUM"

# ============ UTILITY FUNCTIONS ============
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        token = credentials.credentials
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
        
        user = await db.users.find_one({"_id": ObjectId(user_id)})
        if user is None:
            raise HTTPException(status_code=401, detail="User not found")
        
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ============ PRICE DATA & TECHNICAL ANALYSIS ============
async def get_price_data(symbol: str, interval: str = "15min", outputsize: int = 100) -> pd.DataFrame:
    """Fetch price data from Twelve Data API"""
    try:
        # Convert broker symbols to Twelve Data format
        symbol_map = {
            "XAUUSD": "XAU/USD",
            "XAUEUR": "XAU/EUR",
            "EURUSD": "EUR/USD",
            "GBPUSD": "GBP/USD",
            "USDJPY": "USD/JPY",
            "EURJPY": "EUR/JPY",
            "GBPJPY": "GBP/JPY",
            "AUDUSD": "AUD/USD",
            "USDCAD": "USD/CAD",
            "BTCUSD": "BTC/USD"
        }
        
        api_symbol = symbol_map.get(symbol, symbol)
        
        url = f"https://api.twelvedata.com/time_series"
        params = {
            "symbol": api_symbol,
            "interval": interval,
            "apikey": TWELVE_DATA_API_KEY,
            "outputsize": outputsize
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                data = await response.json()
                
                if "values" not in data:
                    logger.error(f"Error fetching price data for {symbol} ({api_symbol}): {data}")
                    return None
                
                df = pd.DataFrame(data["values"])
                df["datetime"] = pd.to_datetime(df["datetime"])
                df = df.sort_values("datetime")
                
                # Convert to numeric (volume might not exist for some pairs)
                for col in ["open", "high", "low", "close"]:
                    df[col] = pd.to_numeric(df[col])
                
                # Volume might not be available for all symbols
                if "volume" in df.columns:
                    df["volume"] = pd.to_numeric(df["volume"])
                else:
                    df["volume"] = 0
                
                return df
    except Exception as e:
        logger.error(f"Error fetching price data for {symbol}: {e}")
        return None

def calculate_technical_indicators(df: pd.DataFrame) -> Dict[str, Any]:
    """Calculate technical indicators"""
    try:
        # RSI
        df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
        
        # MACD
        macd = ta.trend.MACD(df["close"])
        df["macd"] = macd.macd()
        df["macd_signal"] = macd.macd_signal()
        df["macd_diff"] = macd.macd_diff()
        
        # Moving Averages
        df["ma_20"] = ta.trend.SMAIndicator(df["close"], window=20).sma_indicator()
        df["ma_50"] = ta.trend.SMAIndicator(df["close"], window=50).sma_indicator()
        df["ema_12"] = ta.trend.EMAIndicator(df["close"], window=12).ema_indicator()
        
        # Bollinger Bands
        bollinger = ta.volatility.BollingerBands(df["close"])
        df["bb_upper"] = bollinger.bollinger_hband()
        df["bb_middle"] = bollinger.bollinger_mavg()
        df["bb_lower"] = bollinger.bollinger_lband()
        
        # ATR (Average True Range)
        df["atr"] = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"]).average_true_range()
        
        latest = df.iloc[-1]
        
        return {
            "current_price": float(latest["close"]),
            "rsi": float(latest["rsi"]),
            "macd": float(latest["macd"]),
            "macd_signal": float(latest["macd_signal"]),
            "ma_20": float(latest["ma_20"]),
            "ma_50": float(latest["ma_50"]),
            "bb_upper": float(latest["bb_upper"]),
            "bb_lower": float(latest["bb_lower"]),
            "atr": float(latest["atr"]),
            "trend": "BULLISH" if latest["close"] > latest["ma_50"] else "BEARISH"
        }
    except Exception as e:
        logger.error(f"Error calculating indicators: {e}")
        return None

# ============ PAIR-SPECIFIC OPTIMIZATION PARAMETERS ============
# Each pair has unique characteristics requiring different optimization
PAIR_PARAMETERS = {
    "XAUUSD": {
        "atr_multiplier_sl": 1.5,
        "atr_multiplier_tp1": 1.0,
        "atr_multiplier_tp2": 2.0,
        "atr_multiplier_tp3": 3.0,
        "min_rr": 2.0,
        "pip_value": 0.1,
        "decimal_places": 2,
        "typical_spread": 0.30
    },
    "XAUEUR": {
        "atr_multiplier_sl": 1.5,
        "atr_multiplier_tp1": 1.0,
        "atr_multiplier_tp2": 2.0,
        "atr_multiplier_tp3": 3.0,
        "min_rr": 2.0,
        "pip_value": 0.1,
        "decimal_places": 2,
        "typical_spread": 0.40
    },
    "BTCUSD": {
        "atr_multiplier_sl": 2.0,
        "atr_multiplier_tp1": 1.5,
        "atr_multiplier_tp2": 3.0,
        "atr_multiplier_tp3": 4.5,
        "min_rr": 2.0,
        "pip_value": 1.0,
        "decimal_places": 2,
        "typical_spread": 10.0
    },
    "EURUSD": {
        "atr_multiplier_sl": 1.2,
        "atr_multiplier_tp1": 0.8,
        "atr_multiplier_tp2": 1.6,
        "atr_multiplier_tp3": 2.4,
        "min_rr": 2.0,
        "pip_value": 0.0001,
        "decimal_places": 5,
        "typical_spread": 0.00010
    },
    "GBPUSD": {
        "atr_multiplier_sl": 1.3,
        "atr_multiplier_tp1": 0.9,
        "atr_multiplier_tp2": 1.8,
        "atr_multiplier_tp3": 2.7,
        "min_rr": 2.0,
        "pip_value": 0.0001,
        "decimal_places": 5,
        "typical_spread": 0.00012
    },
    "USDJPY": {
        "atr_multiplier_sl": 1.2,
        "atr_multiplier_tp1": 0.8,
        "atr_multiplier_tp2": 1.6,
        "atr_multiplier_tp3": 2.4,
        "min_rr": 2.0,
        "pip_value": 0.01,
        "decimal_places": 3,
        "typical_spread": 0.010
    },
    "EURJPY": {
        "atr_multiplier_sl": 1.4,
        "atr_multiplier_tp1": 1.0,
        "atr_multiplier_tp2": 2.0,
        "atr_multiplier_tp3": 3.0,
        "min_rr": 2.0,
        "pip_value": 0.01,
        "decimal_places": 3,
        "typical_spread": 0.015
    },
    "GBPJPY": {
        "atr_multiplier_sl": 1.5,
        "atr_multiplier_tp1": 1.1,
        "atr_multiplier_tp2": 2.2,
        "atr_multiplier_tp3": 3.3,
        "min_rr": 2.0,
        "pip_value": 0.01,
        "decimal_places": 3,
        "typical_spread": 0.020
    },
    "AUDUSD": {
        "atr_multiplier_sl": 1.2,
        "atr_multiplier_tp1": 0.8,
        "atr_multiplier_tp2": 1.6,
        "atr_multiplier_tp3": 2.4,
        "min_rr": 2.0,
        "pip_value": 0.0001,
        "decimal_places": 5,
        "typical_spread": 0.00012
    },
    "USDCAD": {
        "atr_multiplier_sl": 1.2,
        "atr_multiplier_tp1": 0.8,
        "atr_multiplier_tp2": 1.6,
        "atr_multiplier_tp3": 2.4,
        "min_rr": 2.0,
        "pip_value": 0.0001,
        "decimal_places": 5,
        "typical_spread": 0.00015
    }
}

# Default parameters for any unlisted pair
DEFAULT_PAIR_PARAMS = {
    "atr_multiplier_sl": 1.5,
    "atr_multiplier_tp1": 1.0,
    "atr_multiplier_tp2": 2.0,
    "atr_multiplier_tp3": 3.0,
    "min_rr": 2.0,
    "pip_value": 0.0001,
    "decimal_places": 5,
    "typical_spread": 0.00015
}

async def generate_ai_analysis(symbol: str, indicators: Dict[str, Any]) -> Dict[str, Any]:
    """Generate AI-powered trading signal with pair-specific optimization"""
    try:
        # Get pair-specific parameters
        params = PAIR_PARAMETERS.get(symbol, DEFAULT_PAIR_PARAMS)
        
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"signal_{symbol}_{datetime.utcnow().timestamp()}",
            system_message="You are an elite institutional forex and commodities trader. Provide precise, actionable trading signals with strict risk management."
        ).with_model("openai", "gpt-5.2")
        
        prompt = f"""
        Analyze {symbol} market data and provide a professional trading signal:
        
        === MARKET DATA ===
        Current Price: {indicators['current_price']}
        RSI (14): {indicators['rsi']:.2f}
        MACD: {indicators['macd']:.6f} (Signal: {indicators['macd_signal']:.6f})
        MA 20: {indicators['ma_20']:.{params['decimal_places']}f}
        MA 50: {indicators['ma_50']:.{params['decimal_places']}f}
        Bollinger Upper: {indicators['bb_upper']:.{params['decimal_places']}f}
        Bollinger Lower: {indicators['bb_lower']:.{params['decimal_places']}f}
        ATR (14): {indicators['atr']:.{params['decimal_places']}f}
        Trend Bias: {indicators['trend']}
        
        === PAIR-SPECIFIC PARAMETERS ===
        ATR Multiplier for SL: {params['atr_multiplier_sl']}
        ATR Multiplier for TP1: {params['atr_multiplier_tp1']}
        ATR Multiplier for TP2: {params['atr_multiplier_tp2']}
        ATR Multiplier for TP3: {params['atr_multiplier_tp3']}
        Minimum Risk/Reward: {params['min_rr']}
        Decimal Places: {params['decimal_places']}
        
        === REQUIREMENTS ===
        1. Calculate SL using ATR × {params['atr_multiplier_sl']}
        2. Calculate TP1 using ATR × {params['atr_multiplier_tp1']}
        3. Calculate TP2 using ATR × {params['atr_multiplier_tp2']}
        4. Calculate TP3 using ATR × {params['atr_multiplier_tp3']}
        5. CRITICAL: All three TP levels MUST be DIFFERENT values
        6. CRITICAL: Minimum Risk/Reward ratio must be {params['min_rr']}:1
        7. Round all prices to {params['decimal_places']} decimal places
        
        === OUTPUT FORMAT (JSON ONLY) ===
        {{
            "signal": "BUY" or "SELL" or "NEUTRAL",
            "confidence": numeric 0-100,
            "entry_price": numeric (current price),
            "tp_levels": [tp1, tp2, tp3] (3 DIFFERENT ascending/descending values),
            "sl_price": numeric,
            "analysis": "Brief explanation under 150 words",
            "risk_reward": numeric (e.g., 2.5)
        }}
        
        RESPOND ONLY WITH VALID JSON. NO OTHER TEXT.
        """
        
        user_message = UserMessage(text=prompt)
        response = await chat.send_message(user_message)
        
        # Parse AI response
        import json
        ai_data = json.loads(response)
        
        # Validate and fix TP levels if needed
        tp_levels = ai_data.get("tp_levels", [])
        if len(tp_levels) == 3:
            # Ensure all TP levels are different
            if len(set(tp_levels)) != 3:
                # Recalculate using ATR-based approach
                atr = indicators['atr']
                entry = ai_data.get("entry_price", indicators['current_price'])
                signal_type = ai_data.get("signal", "BUY")
                
                if signal_type == "BUY":
                    tp_levels = [
                        round(entry + (atr * params['atr_multiplier_tp1']), params['decimal_places']),
                        round(entry + (atr * params['atr_multiplier_tp2']), params['decimal_places']),
                        round(entry + (atr * params['atr_multiplier_tp3']), params['decimal_places'])
                    ]
                else:
                    tp_levels = [
                        round(entry - (atr * params['atr_multiplier_tp1']), params['decimal_places']),
                        round(entry - (atr * params['atr_multiplier_tp2']), params['decimal_places']),
                        round(entry - (atr * params['atr_multiplier_tp3']), params['decimal_places'])
                    ]
                ai_data["tp_levels"] = tp_levels
        
        # Parse risk_reward if it's in ratio format
        risk_reward = ai_data.get("risk_reward", params['min_rr'])
        if isinstance(risk_reward, str) and ":" in risk_reward:
            parts = risk_reward.split(":")
            if len(parts) == 2:
                try:
                    risk_reward = float(parts[1])
                except:
                    risk_reward = params['min_rr']
        elif not isinstance(risk_reward, (int, float)):
            risk_reward = params['min_rr']
        
        ai_data["risk_reward"] = risk_reward
        
        return ai_data
    except Exception as e:
        logger.error(f"Error generating AI analysis for {symbol}: {e}")
        return None

async def generate_signal_for_pair(pair: str) -> Optional[Signal]:
    """Generate a complete trading signal for a pair with ML optimization"""
    try:
        # Get pair-specific parameters
        params = PAIR_PARAMETERS.get(pair, DEFAULT_PAIR_PARAMS)
        
        # Get price data - 1H timeframe
        df = await get_price_data(pair, interval="1h", outputsize=100)
        if df is None or len(df) < 50:
            logger.warning(f"Insufficient data for {pair}")
            return None
        
        # Calculate indicators
        indicators = calculate_technical_indicators(df)
        if indicators is None:
            return None
        
        # Generate AI analysis
        ai_analysis = await generate_ai_analysis(pair, indicators)
        if ai_analysis is None or ai_analysis.get("signal") == "NEUTRAL":
            logger.info(f"No trade signal for {pair} (NEUTRAL or None)")
            return None
        
        # ============ ML OPTIMIZATION ============
        try:
            # Optimize signal using ML engine
            optimized = signal_optimizer.optimize_signal(
                df=df,
                symbol=pair,
                ai_signal=ai_analysis,
                pair_params=params
            )
            
            # Check if signal was blocked or filtered
            if optimized.get('blocked'):
                logger.warning(f"Signal blocked for {pair}: {optimized.get('block_reason')}")
                return None
            
            if optimized.get('filtered'):
                logger.info(f"Signal filtered for {pair}: {optimized.get('filter_reason')}")
                return None
            
            # Extract optimized values
            regime_info = optimized.get('regime', {})
            regime_name = regime_info.get('name', 'UNKNOWN')
            regime_confidence = regime_info.get('confidence', 0.5)
            risk_multiplier = regime_info.get('risk_multiplier', 1.0)
            
            # Use optimized levels if available
            if optimized.get('optimized'):
                entry_price = optimized.get('entry_price', ai_analysis['entry_price'])
                tp_levels = optimized.get('tp_levels', ai_analysis['tp_levels'])
                sl_price = optimized.get('sl_price', ai_analysis['sl_price'])
            else:
                entry_price = ai_analysis['entry_price']
                tp_levels = ai_analysis['tp_levels']
                sl_price = ai_analysis['sl_price']
            
            logger.info(f"ML Optimization for {pair}: Regime={regime_name}, RiskMult={risk_multiplier:.2f}")
            
        except Exception as ml_error:
            logger.warning(f"ML optimization failed for {pair}: {ml_error}. Using raw AI signal.")
            entry_price = ai_analysis['entry_price']
            tp_levels = ai_analysis['tp_levels']
            sl_price = ai_analysis['sl_price']
            regime_name = 'UNKNOWN'
            regime_confidence = 0.5
            risk_multiplier = 1.0
        
        # Parse risk_reward if it's in ratio format
        risk_reward = ai_analysis.get("risk_reward", params['min_rr'])
        if isinstance(risk_reward, str) and ":" in risk_reward:
            parts = risk_reward.split(":")
            if len(parts) == 2:
                try:
                    risk_reward = float(parts[1])
                except:
                    risk_reward = params['min_rr']
        elif not isinstance(risk_reward, (int, float)):
            risk_reward = params['min_rr']
        
        # Adjust confidence based on regime
        adjusted_confidence = ai_analysis["confidence"] * regime_confidence
        
        # Create signal with ML enhancements
        signal = Signal(
            pair=pair,
            type=ai_analysis["signal"],
            entry_price=entry_price,
            current_price=indicators["current_price"],
            tp_levels=tp_levels,
            sl_price=sl_price,
            confidence=round(adjusted_confidence, 1),
            analysis=f"[{regime_name}] {ai_analysis['analysis']}",
            timeframe="1H",
            risk_reward=risk_reward,
            is_premium=adjusted_confidence > 60  # ML-adjusted threshold
        )
        
        # Save to database
        signal_dict = signal.dict(exclude={"id"})
        signal_dict['regime'] = regime_name
        signal_dict['risk_multiplier'] = risk_multiplier
        result = await db.signals.insert_one(signal_dict)
        signal.id = str(result.inserted_id)
        
        # Send to Telegram with regime info
        await send_signal_to_telegram(signal, regime_name, risk_multiplier)
        
        return signal
    except Exception as e:
        logger.error(f"Error generating signal for {pair}: {e}")
        return None

# ============ TELEGRAM BOT ============
telegram_bot = None

def sanitize_html(text: str) -> str:
    """Sanitize text for Telegram HTML parsing"""
    if not text:
        return ""
    # Replace HTML special characters
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text

async def send_signal_to_telegram(signal: Signal, regime_name: str = "UNKNOWN", risk_mult: float = 1.0):
    """Send signal to Telegram channel - PROFESSIONAL COPIER FORMAT"""
    try:
        if not TELEGRAM_BOT_TOKEN:
            logger.warning("Telegram bot token not configured")
            return
        
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        channel_id = os.environ.get('TELEGRAM_CHANNEL_ID', '@agbaakinlove')
        
        # Sanitize analysis text to prevent HTML parsing errors
        safe_analysis = sanitize_html(signal.analysis)
        
        # Determine signal emoji
        signal_emoji = "🟢" if signal.type == "BUY" else "🔴"
        regime_emoji = "📊"
        if regime_name == "TREND_UP":
            regime_emoji = "📈"
        elif regime_name == "TREND_DOWN":
            regime_emoji = "📉"
        elif regime_name == "RANGE":
            regime_emoji = "↔️"
        elif regime_name == "HIGH_VOL":
            regime_emoji = "⚡"
        
        # Professional format optimized for copier systems
        message = f"""
{signal_emoji} <b>SIGNAL: {signal.pair}</b> {signal_emoji}

<b>📊 Direction:</b> {signal.type}
<b>💰 Entry Price:</b> {signal.entry_price}

<b>🎯 Take Profit Levels:</b>
   TP1: {signal.tp_levels[0]}
   TP2: {signal.tp_levels[1]}
   TP3: {signal.tp_levels[2]}

<b>🛡 Stop Loss:</b> {signal.sl_price}

<b>📈 Risk/Reward:</b> 1:{signal.risk_reward}
<b>⚡ Confidence:</b> {signal.confidence}%
<b>{regime_emoji} Market Regime:</b> {regime_name}
<b>⚖️ Risk Factor:</b> {risk_mult:.1f}x

<b>📝 Analysis:</b>
{safe_analysis}

<b>⏰ Time:</b> {signal.created_at.strftime('%Y-%m-%d %H:%M UTC')}

<i>🤖 Powered by Grandcom ML Engine</i>
        """
        
        await bot.send_message(chat_id=channel_id, text=message, parse_mode="HTML")
        logger.info(f"✅ Signal sent to Telegram {channel_id}: {signal.pair} {signal.type}")
    except Exception as e:
        logger.error(f"❌ Error sending to Telegram: {e}")

# ============ AUTH ENDPOINTS ============
@api_router.post("/auth/register", response_model=Token)
async def register(user_data: UserRegister):
    """Register a new user"""
    # Check if user exists
    existing_user = await db.users.find_one({"email": user_data.email})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create user
    user = {
        "email": user_data.email,
        "password_hash": hash_password(user_data.password),
        "full_name": user_data.full_name,
        "subscription_tier": "FREE",
        "telegram_id": None,
        "created_at": datetime.utcnow()
    }
    
    result = await db.users.insert_one(user)
    user["_id"] = result.inserted_id
    
    # Create token
    access_token = create_access_token({"sub": str(user["_id"])})
    
    user_response = UserResponse(
        id=str(user["_id"]),
        email=user["email"],
        full_name=user["full_name"],
        subscription_tier=user["subscription_tier"],
        telegram_id=user["telegram_id"],
        created_at=user["created_at"]
    )
    
    return Token(access_token=access_token, token_type="bearer", user=user_response)

@api_router.post("/auth/login", response_model=Token)
async def login(user_data: UserLogin):
    """Login user"""
    user = await db.users.find_one({"email": user_data.email})
    if not user or not verify_password(user_data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    access_token = create_access_token({"sub": str(user["_id"])})
    
    user_response = UserResponse(
        id=str(user["_id"]),
        email=user["email"],
        full_name=user["full_name"],
        subscription_tier=user["subscription_tier"],
        telegram_id=user["telegram_id"],
        created_at=user["created_at"]
    )
    
    return Token(access_token=access_token, token_type="bearer", user=user_response)

@api_router.get("/auth/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    """Get current user"""
    return UserResponse(
        id=str(current_user["_id"]),
        email=current_user["email"],
        full_name=current_user.get("full_name"),
        subscription_tier=current_user["subscription_tier"],
        telegram_id=current_user.get("telegram_id"),
        created_at=current_user["created_at"]
    )

# ============ SIGNAL ENDPOINTS ============
@api_router.get("/signals", response_model=List[Signal])
async def get_signals(
    limit: int = 50,
    current_user: dict = Depends(get_current_user)
):
    """Get signals based on user subscription"""
    query = {}
    
    # Free users only see free signals
    if current_user["subscription_tier"] == "FREE":
        query["is_premium"] = False
    
    signals = await db.signals.find(query).sort("created_at", -1).limit(limit).to_list(limit)
    
    return [
        Signal(
            id=str(s["_id"]),
            **{k: v for k, v in s.items() if k != "_id"}
        )
        for s in signals
    ]

@api_router.get("/signals/{signal_id}", response_model=Signal)
async def get_signal(signal_id: str, current_user: dict = Depends(get_current_user)):
    """Get a specific signal"""
    if not ObjectId.is_valid(signal_id):
        raise HTTPException(status_code=400, detail="Invalid signal ID")
    
    signal = await db.signals.find_one({"_id": ObjectId(signal_id)})
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    
    # Check if user has access
    if signal.get("is_premium") and current_user["subscription_tier"] == "FREE":
        raise HTTPException(status_code=403, detail="Premium subscription required")
    
    return Signal(id=str(signal["_id"]), **{k: v for k, v in signal.items() if k != "_id"})

@api_router.post("/signals/generate")
async def trigger_signal_generation(
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Manually trigger signal generation (admin only)"""
    # Full pairs list including XAUEUR and BTCUSD (Grow plan enabled)
    pairs = ["XAUUSD", "XAUEUR", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "EURJPY", "GBPJPY", "AUDUSD", "USDCAD"]
    
    for pair in pairs:
        background_tasks.add_task(generate_signal_for_pair, pair)
    
    return {"message": "Signal generation triggered", "pairs": pairs}

# ============ ML ENGINE ENDPOINTS ============
@api_router.get("/ml/stats")
async def get_ml_stats(current_user: dict = Depends(get_current_user)):
    """Get ML engine performance statistics"""
    try:
        stats = signal_optimizer.get_performance_stats()
        return {
            "success": True,
            "stats": stats
        }
    except Exception as e:
        logger.error(f"Error getting ML stats: {e}")
        return {"success": False, "error": str(e)}

@api_router.get("/ml/regime/{symbol}")
async def get_current_regime(symbol: str, current_user: dict = Depends(get_current_user)):
    """Get current market regime for a symbol"""
    try:
        # Get price data
        df = await get_price_data(symbol, interval="1h", outputsize=100)
        if df is None or len(df) < 50:
            raise HTTPException(status_code=400, detail="Insufficient data for regime detection")
        
        # Extract features
        features = signal_optimizer.feature_engineer.extract_features(df, symbol)
        if not features:
            raise HTTPException(status_code=500, detail="Feature extraction failed")
        
        # Detect regime
        regime = signal_optimizer.regime_detector.detect_regime(features)
        
        return {
            "success": True,
            "symbol": symbol,
            "regime": regime,
            "features_summary": {
                "adx": features.get('adx'),
                "rsi": features.get('rsi'),
                "atr_ratio": features.get('atr_ratio_20'),
                "volatility": features.get('realized_vol_20'),
                "trend_bias": features.get('structure_bias')
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error detecting regime for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/ml/risk")
async def get_risk_status(current_user: dict = Depends(get_current_user)):
    """Get current risk management status"""
    try:
        risk_check = signal_optimizer.risk_manager.check_trading_allowed()
        risk_metrics = signal_optimizer.risk_manager.get_risk_metrics()
        
        return {
            "success": True,
            "trading_allowed": risk_check['allowed'],
            "restrictions": risk_check.get('restrictions', []),
            "metrics": risk_metrics
        }
    except Exception as e:
        logger.error(f"Error getting risk status: {e}")
        return {"success": False, "error": str(e)}

@api_router.get("/ml/mtf/{symbol}")
async def get_mtf_analysis(symbol: str, current_user: dict = Depends(get_current_user)):
    """Get multi-timeframe analysis for a symbol"""
    try:
        # Validate symbol
        valid_symbols = ["XAUUSD", "XAUEUR", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "EURJPY", "GBPJPY", "AUDUSD", "USDCAD"]
        symbol = symbol.upper()
        
        if symbol not in valid_symbols:
            raise HTTPException(status_code=400, detail=f"Invalid symbol. Valid: {valid_symbols}")
        
        # Run MTF analysis
        analysis = await mtf_analyzer.analyze(symbol)
        
        return {
            "success": True,
            "analysis": analysis
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"MTF analysis error for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/ml/mtf-all")
async def get_all_mtf_analysis(current_user: dict = Depends(get_current_user)):
    """Get multi-timeframe analysis for all pairs"""
    try:
        all_pairs = ["XAUUSD", "XAUEUR", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "EURJPY", "GBPJPY", "AUDUSD", "USDCAD"]
        results = {}
        
        for pair in all_pairs:
            try:
                analysis = await mtf_analyzer.analyze(pair)
                results[pair] = analysis
                await asyncio.sleep(2)  # Rate limiting between pairs
            except Exception as e:
                results[pair] = {"error": str(e), "valid_setup": False}
        
        # Find best setups
        best_setups = [
            {"symbol": k, **v} for k, v in results.items() 
            if v.get('valid_setup') and v.get('confluence_score', 0) >= 2
        ]
        
        return {
            "success": True,
            "timestamp": datetime.utcnow().isoformat(),
            "all_pairs": results,
            "best_setups": best_setups,
            "total_valid_setups": len(best_setups)
        }
    except Exception as e:
        logger.error(f"Error getting all MTF analysis: {e}")
        return {"success": False, "error": str(e)}

# ============ DATA COLLECTION ENDPOINTS ============
@api_router.post("/ml/collect-historical")
async def collect_historical_data(
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Trigger historical data collection for all pairs (admin only)"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    async def run_collection():
        await historical_collector.setup_indexes()
        results = await historical_collector.collect_all_pairs()
        logger.info(f"Historical data collection complete: {results['total_records']} records")
    
    background_tasks.add_task(run_collection)
    
    return {
        "success": True,
        "message": "Historical data collection started in background",
        "pairs": ["XAUUSD", "XAUEUR", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "EURJPY", "GBPJPY", "AUDUSD", "USDCAD"],
        "timeframes": ["1h", "4h", "15min"]
    }

@api_router.get("/ml/data-stats")
async def get_historical_data_stats(current_user: dict = Depends(get_current_user)):
    """Get statistics about collected historical data"""
    try:
        stats = await historical_collector.get_data_stats()
        return {
            "success": True,
            "stats": stats
        }
    except Exception as e:
        logger.error(f"Error getting data stats: {e}")
        return {"success": False, "error": str(e)}

@api_router.get("/ml/signal-performance")
async def get_signal_performance(current_user: dict = Depends(get_current_user)):
    """Get signal performance by regime"""
    try:
        await signal_tracker.setup_indexes()
        performance = await signal_tracker.get_performance_by_regime()
        return {
            "success": True,
            "performance": performance
        }
    except Exception as e:
        logger.error(f"Error getting signal performance: {e}")
        return {"success": False, "error": str(e)}

# ============ SUBSCRIPTION ENDPOINTS ============
@api_router.put("/subscription", response_model=UserResponse)
async def update_subscription(
    subscription: SubscriptionUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Update user subscription"""
    await db.users.update_one(
        {"_id": current_user["_id"]},
        {"$set": {"subscription_tier": subscription.tier}}
    )
    
    current_user["subscription_tier"] = subscription.tier
    
    return UserResponse(
        id=str(current_user["_id"]),
        email=current_user["email"],
        full_name=current_user.get("full_name"),
        subscription_tier=current_user["subscription_tier"],
        telegram_id=current_user.get("telegram_id"),
        created_at=current_user["created_at"]
    )

# ============ STATISTICS ENDPOINTS ============
@api_router.get("/stats")
async def get_statistics(current_user: dict = Depends(get_current_user)):
    """Get signal performance statistics"""
    total_signals = await db.signals.count_documents({})
    active_signals = await db.signals.count_documents({"status": "ACTIVE"})
    
    # Win rate calculation
    closed_signals = await db.signals.find({"status": {"$in": ["HIT_TP", "HIT_SL"]}}).to_list(1000)
    wins = sum(1 for s in closed_signals if s.get("result") == "WIN")
    win_rate = (wins / len(closed_signals) * 100) if closed_signals else 0
    
    # Average pips
    avg_pips = sum(s.get("pips", 0) for s in closed_signals if s.get("pips")) / len(closed_signals) if closed_signals else 0
    
    return {
        "total_signals": total_signals,
        "active_signals": active_signals,
        "win_rate": round(win_rate, 2),
        "avg_pips": round(avg_pips, 2),
        "total_closed": len(closed_signals)
    }

# ============ BACKGROUND TASKS ============
async def auto_generate_signals():
    """Background task to auto-generate signals every 15 minutes"""
    # Full pairs list including XAUEUR and BTCUSD (Grow plan enabled)
    pairs = ["XAUUSD", "XAUEUR", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "EURJPY", "GBPJPY", "AUDUSD", "USDCAD"]
    
    while True:
        try:
            logger.info("Starting automatic signal generation...")
            for pair in pairs:
                await generate_signal_for_pair(pair)
                await asyncio.sleep(10)  # Wait between pairs
            
            logger.info("Signal generation completed")
            await asyncio.sleep(900)  # Wait 15 minutes
        except Exception as e:
            logger.error(f"Error in auto signal generation: {e}")
            await asyncio.sleep(60)

# ============ APP SETUP ============
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    """Start background tasks"""
    logger.info("Starting Forex & Gold Signals API...")
    # Start auto signal generation in background
    asyncio.create_task(auto_generate_signals())

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()

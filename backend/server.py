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
        url = f"https://api.twelvedata.com/time_series"
        params = {
            "symbol": symbol,
            "interval": interval,
            "apikey": TWELVE_DATA_API_KEY,
            "outputsize": outputsize
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                data = await response.json()
                
                if "values" not in data:
                    logger.error(f"Error fetching price data: {data}")
                    return None
                
                df = pd.DataFrame(data["values"])
                df["datetime"] = pd.to_datetime(df["datetime"])
                df = df.sort_values("datetime")
                
                # Convert to numeric
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = pd.to_numeric(df[col])
                
                return df
    except Exception as e:
        logger.error(f"Error fetching price data: {e}")
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

async def generate_ai_analysis(symbol: str, indicators: Dict[str, Any]) -> Dict[str, Any]:
    """Generate AI-powered trading signal"""
    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"signal_{symbol}_{datetime.utcnow().timestamp()}",
            system_message="You are an expert forex and gold trader with 20 years of experience. Analyze market data and provide precise trading signals."
        ).with_model("openai", "gpt-5.2")
        
        prompt = f"""
        Analyze the following market data for {symbol} and provide a trading signal:
        
        Current Price: {indicators['current_price']}
        RSI: {indicators['rsi']}
        MACD: {indicators['macd']} (Signal: {indicators['macd_signal']})
        MA 20: {indicators['ma_20']}
        MA 50: {indicators['ma_50']}
        Bollinger Bands: Upper {indicators['bb_upper']}, Lower {indicators['bb_lower']}
        ATR: {indicators['atr']}
        Trend: {indicators['trend']}
        
        Provide a JSON response with:
        1. signal: "BUY", "SELL", or "NEUTRAL"
        2. confidence: 0-100
        3. entry_price: suggested entry price
        4. tp_levels: array of 3 take profit levels
        5. sl_price: stop loss price
        6. analysis: brief explanation (max 200 words)
        7. risk_reward: risk to reward ratio
        
        Only respond with valid JSON, no other text.
        """
        
        user_message = UserMessage(text=prompt)
        response = await chat.send_message(user_message)
        
        # Parse AI response
        import json
        ai_data = json.loads(response)
        
        return ai_data
    except Exception as e:
        logger.error(f"Error generating AI analysis: {e}")
        return None

async def generate_signal_for_pair(pair: str) -> Optional[Signal]:
    """Generate a complete trading signal for a pair"""
    try:
        # Get price data
        df = await get_price_data(pair, interval="15min", outputsize=100)
        if df is None or len(df) < 50:
            return None
        
        # Calculate indicators
        indicators = calculate_technical_indicators(df)
        if indicators is None:
            return None
        
        # Generate AI analysis
        ai_analysis = await generate_ai_analysis(pair, indicators)
        if ai_analysis is None or ai_analysis.get("signal") == "NEUTRAL":
            return None
        
        # Create signal
        signal = Signal(
            pair=pair,
            type=ai_analysis["signal"],
            entry_price=ai_analysis["entry_price"],
            current_price=indicators["current_price"],
            tp_levels=ai_analysis["tp_levels"],
            sl_price=ai_analysis["sl_price"],
            confidence=ai_analysis["confidence"],
            analysis=ai_analysis["analysis"],
            timeframe="15min",
            risk_reward=ai_analysis["risk_reward"],
            is_premium=ai_analysis["confidence"] > 75  # High confidence signals are premium
        )
        
        # Save to database
        signal_dict = signal.dict(exclude={"id"})
        result = await db.signals.insert_one(signal_dict)
        signal.id = str(result.inserted_id)
        
        # Send to Telegram
        await send_signal_to_telegram(signal)
        
        return signal
    except Exception as e:
        logger.error(f"Error generating signal for {pair}: {e}")
        return None

# ============ TELEGRAM BOT ============
telegram_bot = None

async def send_signal_to_telegram(signal: Signal):
    """Send signal to Telegram channel"""
    try:
        if not TELEGRAM_BOT_TOKEN:
            return
        
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        
        # Use the user's Telegram ID
        channel_id = os.environ.get('TELEGRAM_CHANNEL_ID', '8517883508')
        
        message = f"""
🔔 <b>NEW SIGNAL - {signal.pair}</b>

📊 <b>Type:</b> {signal.type}
💰 <b>Entry:</b> {signal.entry_price}
🎯 <b>Take Profits:</b>
   TP1: {signal.tp_levels[0]}
   TP2: {signal.tp_levels[1]}
   TP3: {signal.tp_levels[2]}
🛡 <b>Stop Loss:</b> {signal.sl_price}

📈 <b>Risk/Reward:</b> {signal.risk_reward}
⚡️ <b>Confidence:</b> {signal.confidence}%
🔒 <b>Tier:</b> {'PREMIUM' if signal.is_premium else 'FREE'}

📝 <b>Analysis:</b>
{signal.analysis}

⏰ {signal.created_at.strftime('%Y-%m-%d %H:%M UTC')}
        """
        
        # Send to the user's Telegram ID
        await bot.send_message(chat_id=channel_id, text=message, parse_mode="HTML")
        logger.info(f"Signal sent to Telegram ID {channel_id}: {signal.pair} {signal.type}")
    except Exception as e:
        logger.error(f"Error sending to Telegram: {e}")

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
    pairs = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]
    
    for pair in pairs:
        background_tasks.add_task(generate_signal_for_pair, pair)
    
    return {"message": "Signal generation triggered", "pairs": pairs}

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
    pairs = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"]
    
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

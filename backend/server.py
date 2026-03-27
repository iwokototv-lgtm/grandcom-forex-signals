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
from telegram import Bot
# from emergentintegrations.llm.chat import LlmChat, UserMessage
import ta
import pandas as pd
import numpy as np
from pathlib import Path

# Import Signal Outcome Tracker
# from signal_outcome_tracker import SignalOutcomeTracker, init_outcome_tracker, get_outcome_tracker

# Import Push Notification Service
# from notification_service import PushNotificationService, init_push_service, get_push_service

# Import Backtest Engine
# from backtest_engine import BacktestEngine, BacktestConfig, init_backtest_engine, get_backtest_engine

# Import Subscription Service
# from subscription_service import (
#     SubscriptionService, init_subscription_service, get_subscription_service,
#     SUBSCRIPTION_PACKAGES, TIER_FEATURES
# )

def serialize_numpy(obj):
    """Convert numpy types to native Python types for JSON serialization"""
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, dict):
        return {k: serialize_numpy(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [serialize_numpy(item) for item in obj]
    else:
        return obj

# Import ML Engine
# from ml_engine import (
#     FeatureEngineer, RegimeDetector, RiskManager, SignalOptimizer,
#     mtf_analyzer, historical_collector, signal_tracker,
#     smc_analyzer, signal_quality_filter, regime_enforced_tpsl
# )

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Configuration
mongo_url = os.environ['MONGO_URL']
# Add serverSelectionTimeoutMS for faster failure detection in production
client = AsyncIOMotorClient(
    mongo_url,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=10000,
    socketTimeoutMS=30000
)
db = client[os.environ['DB_NAME']]

JWT_SECRET = os.environ.get('JWT_SECRET', 'your-secret-key')
JWT_ALGORITHM = os.environ.get('JWT_ALGORITHM', 'HS256')
JWT_EXPIRATION_HOURS = int(os.environ.get('JWT_EXPIRATION_HOURS', 24))
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TWELVE_DATA_API_KEY = os.environ.get('TWELVE_DATA_API_KEY', 'demo')
EMERGENT_LLM_KEY = os.environ.get('EMERGENT_LLM_KEY')
STRIPE_API_KEY = os.environ.get('STRIPE_API_KEY')

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

# FastAPI App
app = FastAPI(title="Forex & Gold Signals API", version="2.0.0")
api_router = APIRouter(prefix="/api")

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Health check endpoint (no auth required)
@app.get("/api/health")
async def health_check():
    """Health check endpoint for monitoring"""
    try:
        await db.command("ping")
        db_status = "healthy"
    except Exception:
        db_status = "unhealthy"
    
    tracker = get_outcome_tracker()
    tracker_status = "running" if tracker and tracker.is_running else "stopped"
    
    return {
        "status": "healthy",
        "version": "2.0.0",
        "database": db_status,
        "signal_tracker": tracker_status,
        "timestamp": datetime.utcnow().isoformat()
    }

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
    role: str = "user"

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
            "USDCHF": "USD/CHF",
            "BTCUSD": "BTC/USD",
            # Asian session pairs
            "NZDUSD": "NZD/USD",
            "AUDJPY": "AUD/JPY",
            "CADJPY": "CAD/JPY",
            # NEW Institutional pairs
            "CHFJPY": "CHF/JPY",
            "EURAUD": "EUR/AUD",
            "GBPCAD": "GBP/CAD",
            "EURCAD": "EUR/CAD",
            "GBPAUD": "GBP/AUD",
            "AUDNZD": "AUD/NZD",
            "EURGBP": "EUR/GBP",
            "EURCHF": "EUR/CHF",
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
# OPTIMIZED based on 2020-2024 backtest analysis
# FOREX: Conservative (3/6/9) - Higher win rate, better profit factor
# GOLD: XAUUSD uses Balanced (7/15/25), XAUEUR keeps (5/10/15)
PAIR_PARAMETERS = {
    "XAUUSD": {
        "enabled": True,   # RE-ENABLED: User tested and confirmed OK
        "use_fixed_pips": True,
        "fixed_tp1_pips": 7,
        "fixed_tp2_pips": 15,
        "fixed_tp3_pips": 25,
        "atr_multiplier_sl": 1.5,
        "min_rr": 1.5,
        "pip_value": 0.1,
        "decimal_places": 2,
        "typical_spread": 0.30
    },
    "XAUEUR": {
        "enabled": True,   # RE-ENABLED: User tested and confirmed OK
        "use_fixed_pips": True,
        "fixed_tp1_pips": 5,
        "fixed_tp2_pips": 10,
        "fixed_tp3_pips": 15,
        "atr_multiplier_sl": 1.5,
        "min_rr": 1.5,
        "pip_value": 0.1,
        "decimal_places": 2,
        "typical_spread": 0.40
    },
    "BTCUSD": {
        "enabled": False,  # DISABLED: 17.5% win rate, PF 0.14 - too volatile
        "use_fixed_pips": False,
        "atr_multiplier_sl": 2.0,
        "atr_multiplier_tp1": 1.5,
        "atr_multiplier_tp2": 3.0,
        "atr_multiplier_tp3": 4.5,
        "min_rr": 2.0,
        "pip_value": 1.0,
        "decimal_places": 2,
        "typical_spread": 10.0
    },
    # ===== FOREX PAIRS - OPTIMIZED CONSERVATIVE (3/6/9) =====
    # Backtest showed ~11% higher profit factor with conservative settings
    "EURUSD": {
        "use_fixed_pips": True,
        "fixed_tp1_pips": 3,   # OPTIMIZED: PF 1.23, WR 45.9%
        "fixed_tp2_pips": 6,
        "fixed_tp3_pips": 9,
        "fixed_sl_pips": 10,
        "atr_multiplier_sl": 1.2,
        "min_rr": 1.5,
        "pip_value": 0.0001,
        "decimal_places": 5,
        "typical_spread": 0.00010
    },
    "GBPUSD": {
        "use_fixed_pips": True,
        "fixed_tp1_pips": 3,   # OPTIMIZED: PF 1.12, WR 54.4%
        "fixed_tp2_pips": 6,
        "fixed_tp3_pips": 9,
        "fixed_sl_pips": 10,
        "atr_multiplier_sl": 1.3,
        "min_rr": 1.5,
        "pip_value": 0.0001,
        "decimal_places": 5,
        "typical_spread": 0.00012
    },
    "USDJPY": {
        "use_fixed_pips": True,
        "fixed_tp1_pips": 3,   # OPTIMIZED: PF 1.27, WR 52.4%
        "fixed_tp2_pips": 6,
        "fixed_tp3_pips": 9,
        "fixed_sl_pips": 10,
        "atr_multiplier_sl": 1.2,
        "min_rr": 1.5,
        "pip_value": 0.01,
        "decimal_places": 3,
        "typical_spread": 0.010
    },
    "EURJPY": {
        "use_fixed_pips": True,
        "fixed_tp1_pips": 3,   # OPTIMIZED: PF 1.30, WR 58.3% (BEST)
        "fixed_tp2_pips": 6,
        "fixed_tp3_pips": 9,
        "fixed_sl_pips": 10,
        "atr_multiplier_sl": 1.4,
        "min_rr": 1.5,
        "pip_value": 0.01,
        "decimal_places": 3,
        "typical_spread": 0.015
    },
    "GBPJPY": {
        "enabled": True,   # RE-ENABLED: User tested and confirmed OK
        "use_fixed_pips": True,
        "fixed_tp1_pips": 3,
        "fixed_tp2_pips": 6,
        "fixed_tp3_pips": 9,
        "fixed_sl_pips": 10,
        "atr_multiplier_sl": 1.5,
        "min_rr": 1.5,
        "pip_value": 0.01,
        "decimal_places": 3,
        "typical_spread": 0.020
    },
    "AUDUSD": {
        "enabled": True,   # RE-ENABLED: User tested and confirmed OK
        "use_fixed_pips": True,
        "fixed_tp1_pips": 2,
        "fixed_tp2_pips": 4,
        "fixed_tp3_pips": 6,
        "fixed_sl_pips": 8,
        "atr_multiplier_sl": 1.0,
        "min_rr": 1.5,
        "pip_value": 0.0001,
        "decimal_places": 5,
        "typical_spread": 0.00012
    },
    "USDCAD": {
        "use_fixed_pips": True,
        "fixed_tp1_pips": 3,   # OPTIMIZED: PF 1.26, WR 52.9%
        "fixed_tp2_pips": 6,
        "fixed_tp3_pips": 9,
        "fixed_sl_pips": 10,
        "atr_multiplier_sl": 1.2,
        "min_rr": 1.5,
        "pip_value": 0.0001,
        "decimal_places": 5,
        "typical_spread": 0.00015
    },
    "USDCHF": {
        "use_fixed_pips": True,
        "fixed_tp1_pips": 3,   # OPTIMIZED: PF 1.14, WR 40.3%
        "fixed_tp2_pips": 6,
        "fixed_tp3_pips": 9,
        "fixed_sl_pips": 10,
        "atr_multiplier_sl": 1.2,
        "min_rr": 1.5,
        "pip_value": 0.0001,
        "decimal_places": 5,
        "typical_spread": 0.00012
    },
    # ===== NEW ASIAN SESSION PAIRS =====
    "NZDUSD": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 3,   # Conservative for new pair
        "fixed_tp2_pips": 6,
        "fixed_tp3_pips": 9,
        "fixed_sl_pips": 10,
        "atr_multiplier_sl": 1.2,
        "min_rr": 1.5,
        "pip_value": 0.0001,
        "decimal_places": 5,
        "typical_spread": 0.00015
    },
    "AUDJPY": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 3,   # JPY cross - conservative
        "fixed_tp2_pips": 6,
        "fixed_tp3_pips": 9,
        "fixed_sl_pips": 10,
        "atr_multiplier_sl": 1.3,
        "min_rr": 1.5,
        "pip_value": 0.01,    # JPY pair
        "decimal_places": 3,
        "typical_spread": 0.015
    },
    "CADJPY": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 3,   # JPY cross - conservative
        "fixed_tp2_pips": 6,
        "fixed_tp3_pips": 9,
        "fixed_sl_pips": 10,
        "atr_multiplier_sl": 1.3,
        "min_rr": 1.5,
        "pip_value": 0.01,    # JPY pair
        "decimal_places": 3,
        "typical_spread": 0.015
    },
    # ===== NEW INSTITUTIONAL PAIRS (Added per user request) =====
    "CHFJPY": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 3,
        "fixed_tp2_pips": 6,
        "fixed_tp3_pips": 9,
        "fixed_sl_pips": 10,
        "atr_multiplier_sl": 1.3,
        "min_rr": 1.5,
        "pip_value": 0.01,
        "decimal_places": 3,
        "typical_spread": 0.015
    },
    "EURAUD": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 4,
        "fixed_tp2_pips": 8,
        "fixed_tp3_pips": 12,
        "fixed_sl_pips": 12,
        "atr_multiplier_sl": 1.4,
        "min_rr": 1.5,
        "pip_value": 0.0001,
        "decimal_places": 5,
        "typical_spread": 0.00020
    },
    "GBPCAD": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 4,
        "fixed_tp2_pips": 8,
        "fixed_tp3_pips": 12,
        "fixed_sl_pips": 12,
        "atr_multiplier_sl": 1.4,
        "min_rr": 1.5,
        "pip_value": 0.0001,
        "decimal_places": 5,
        "typical_spread": 0.00025
    },
    "EURCAD": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 3,
        "fixed_tp2_pips": 6,
        "fixed_tp3_pips": 9,
        "fixed_sl_pips": 10,
        "atr_multiplier_sl": 1.3,
        "min_rr": 1.5,
        "pip_value": 0.0001,
        "decimal_places": 5,
        "typical_spread": 0.00020
    },
    "GBPAUD": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 4,
        "fixed_tp2_pips": 8,
        "fixed_tp3_pips": 12,
        "fixed_sl_pips": 12,
        "atr_multiplier_sl": 1.5,
        "min_rr": 1.5,
        "pip_value": 0.0001,
        "decimal_places": 5,
        "typical_spread": 0.00025
    },
    "AUDNZD": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 3,
        "fixed_tp2_pips": 6,
        "fixed_tp3_pips": 9,
        "fixed_sl_pips": 10,
        "atr_multiplier_sl": 1.2,
        "min_rr": 1.5,
        "pip_value": 0.0001,
        "decimal_places": 5,
        "typical_spread": 0.00018
    },
    "EURGBP": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 2,
        "fixed_tp2_pips": 4,
        "fixed_tp3_pips": 6,
        "fixed_sl_pips": 8,
        "atr_multiplier_sl": 1.0,
        "min_rr": 1.5,
        "pip_value": 0.0001,
        "decimal_places": 5,
        "typical_spread": 0.00012
    },
    "EURCHF": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 2,
        "fixed_tp2_pips": 4,
        "fixed_tp3_pips": 6,
        "fixed_sl_pips": 8,
        "atr_multiplier_sl": 1.0,
        "min_rr": 1.5,
        "pip_value": 0.0001,
        "decimal_places": 5,
        "typical_spread": 0.00015
    }
}

# ============ PROFITABILITY FILTERS ============
# UPDATED: Removed session restrictions, aligned with user's strategy

# 1. REGIME FILTER - Aligned with user's strategy:
# IF Uptrend: BUY only | IF Downtrend: SELL only | ELSE: Range Strategy
ALLOWED_REGIMES = ["TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOL"]  # Allow ALL regimes
SKIP_REGIME = []  # No regime restrictions - let strategy logic handle it

# 2. CONFIDENCE THRESHOLD
MIN_CONFIDENCE_THRESHOLD = 60  # Lowered to allow more signals
MIN_REGIME_CONFIDENCE = 0.55   # Lowered for more signals
HIGH_CONFIDENCE_THRESHOLD = 70

# 3. SESSION FILTER - DISABLED (No session restrictions as per user request)
# All pairs trade 24/7 - no time-based restrictions
SESSION_FILTERS = {}  # Empty = no restrictions, all pairs trade anytime

# 4. DRAWDOWN PROTECTION - Auto-pause losing pairs
DRAWDOWN_PROTECTION = {
    "enabled": True,
    "max_daily_losses": 3,      # Max losing trades per day before pause
    "max_daily_loss_pips": 50,  # Max pips lost per day before pause
    "pause_duration_hours": 4,  # How long to pause after hitting limit
}

# Track daily performance for drawdown protection
daily_pair_performance = {}

def is_session_optimal(pair: str) -> bool:
    """Check if current time is optimal for trading this pair based on institutional timing
    
    Institutional rules:
    - Block new entries 15 minutes before session close
    - Asian Session forms range, London sweeps liquidity, NY drives move
    """
    now = datetime.utcnow()
    current_hour = now.hour
    current_minute = now.minute
    
    if pair not in SESSION_FILTERS:
        return True  # Allow if no filter defined
    
    filter_config = SESSION_FILTERS[pair]
    optimal_hours = filter_config.get("optimal_hours", list(range(24)))
    block_before_close = filter_config.get("block_before_close", 15)
    
    # Check if within optimal hours
    if current_hour not in optimal_hours:
        return False
    
    # Block entries near session close (last 15 mins of each session block)
    # Sessions end at: Asian 8:00, London 16:00, NY 21:00
    session_end_hours = [8, 16, 21]
    for end_hour in session_end_hours:
        if current_hour == end_hour - 1 and current_minute >= (60 - block_before_close):
            logging.info(f"⏰ {pair} blocked - {block_before_close} mins before session close")
            return False
    
    return True

def check_drawdown_protection(pair: str) -> tuple[bool, str]:
    """Check if pair should be paused due to drawdown protection"""
    global daily_pair_performance
    
    if not DRAWDOWN_PROTECTION["enabled"]:
        return True, ""
    
    today = datetime.utcnow().date().isoformat()
    key = f"{pair}_{today}"
    
    if key not in daily_pair_performance:
        daily_pair_performance[key] = {
            "losses": 0,
            "loss_pips": 0,
            "paused_until": None
        }
    
    perf = daily_pair_performance[key]
    
    # Check if currently paused
    if perf["paused_until"]:
        if datetime.utcnow() < perf["paused_until"]:
            remaining = (perf["paused_until"] - datetime.utcnow()).seconds // 60
            return False, f"Paused for {remaining} more minutes (drawdown protection)"
        else:
            perf["paused_until"] = None  # Reset pause
    
    # Check if limits exceeded
    if perf["losses"] >= DRAWDOWN_PROTECTION["max_daily_losses"]:
        perf["paused_until"] = datetime.utcnow() + timedelta(hours=DRAWDOWN_PROTECTION["pause_duration_hours"])
        return False, f"Max daily losses ({perf['losses']}) reached"
    
    if perf["loss_pips"] >= DRAWDOWN_PROTECTION["max_daily_loss_pips"]:
        perf["paused_until"] = datetime.utcnow() + timedelta(hours=DRAWDOWN_PROTECTION["pause_duration_hours"])
        return False, f"Max daily loss pips ({perf['loss_pips']}) reached"
    
    return True, ""

def record_trade_result(pair: str, result: str, pips: float):
    """Record trade result for drawdown protection"""
    global daily_pair_performance
    
    today = datetime.utcnow().date().isoformat()
    key = f"{pair}_{today}"
    
    if key not in daily_pair_performance:
        daily_pair_performance[key] = {
            "losses": 0,
            "loss_pips": 0,
            "paused_until": None
        }
    
    if result == "LOSS":
        daily_pair_performance[key]["losses"] += 1
        daily_pair_performance[key]["loss_pips"] += abs(pips)

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
        use_fixed_pips = params.get('use_fixed_pips', False)
        
        system_message = "You are an elite institutional forex and commodities trader. Provide precise, actionable trading signals with strict risk management."
        
        # Build prompt based on whether using fixed pips or ATR-based
        if use_fixed_pips:
            # Fixed pip values for Forex pairs
            tp1_pips = params.get('fixed_tp1_pips', 5)
            tp2_pips = params.get('fixed_tp2_pips', 10)
            tp3_pips = params.get('fixed_tp3_pips', 15)
            pip_value = params['pip_value']
            
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
            
            === FIXED PIP TARGETS ===
            TP1: {tp1_pips} pips from entry
            TP2: {tp2_pips} pips from entry
            TP3: {tp3_pips} pips from entry
            SL: ATR × {params['atr_multiplier_sl']}
            Pip Value: {pip_value}
            
            === REQUIREMENTS ===
            1. Determine BUY or SELL based on technical analysis
            2. Entry price = Current price
            3. For BUY: TP1 = entry + ({tp1_pips} × {pip_value}), TP2 = entry + ({tp2_pips} × {pip_value}), TP3 = entry + ({tp3_pips} × {pip_value})
            4. For SELL: TP1 = entry - ({tp1_pips} × {pip_value}), TP2 = entry - ({tp2_pips} × {pip_value}), TP3 = entry - ({tp3_pips} × {pip_value})
            5. SL calculated from ATR × {params['atr_multiplier_sl']}
            6. Round all prices to {params['decimal_places']} decimal places
            
            === OUTPUT FORMAT (JSON ONLY) ===
            {{
                "signal": "BUY" or "SELL" or "NEUTRAL",
                "confidence": numeric 0-100,
                "entry_price": numeric (current price),
                "tp_levels": [tp1, tp2, tp3],
                "sl_price": numeric,
                "analysis": "Brief explanation under 150 words",
                "risk_reward": numeric
            }}
            
            RESPOND ONLY WITH VALID JSON. NO OTHER TEXT.
            """
        else:
            # ATR-based approach for XAUUSD, XAUEUR, BTCUSD
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
            ATR Multiplier for TP1: {params.get('atr_multiplier_tp1', 1.0)}
            ATR Multiplier for TP2: {params.get('atr_multiplier_tp2', 2.0)}
            ATR Multiplier for TP3: {params.get('atr_multiplier_tp3', 3.0)}
            Minimum Risk/Reward: {params['min_rr']}
            Decimal Places: {params['decimal_places']}
            
            === REQUIREMENTS ===
            1. Calculate SL using ATR × {params['atr_multiplier_sl']}
            2. Calculate TP1 using ATR × {params.get('atr_multiplier_tp1', 1.0)}
            3. Calculate TP2 using ATR × {params.get('atr_multiplier_tp2', 2.0)}
            4. Calculate TP3 using ATR × {params.get('atr_multiplier_tp3', 3.0)}
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
        
        user_message = prompt
        
        # Use Emergent LLM integration
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"signal_{symbol}_{datetime.utcnow().timestamp()}",
            system_message=system_message
        ).with_model("openai", "gpt-4o-mini")
        
        user_msg = UserMessage(text=user_message)
        ai_response = await chat.send_message(user_msg)
        
        # Parse AI response
        import json
        ai_data = json.loads(ai_response)
        
        # Validate and fix TP levels if needed
        entry = ai_data.get("entry_price", indicators['current_price'])
        signal_type = ai_data.get("signal", "BUY")
        tp_levels = ai_data.get("tp_levels", [])
        
        # Always recalculate TP levels for fixed pip pairs to ensure exactness
        if use_fixed_pips and signal_type != "NEUTRAL":
            tp1_pips = params.get('fixed_tp1_pips', 5)
            tp2_pips = params.get('fixed_tp2_pips', 10)
            tp3_pips = params.get('fixed_tp3_pips', 15)
            pip_value = params['pip_value']
            
            if signal_type == "BUY":
                tp_levels = [
                    round(entry + (tp1_pips * pip_value), params['decimal_places']),
                    round(entry + (tp2_pips * pip_value), params['decimal_places']),
                    round(entry + (tp3_pips * pip_value), params['decimal_places'])
                ]
            else:  # SELL
                tp_levels = [
                    round(entry - (tp1_pips * pip_value), params['decimal_places']),
                    round(entry - (tp2_pips * pip_value), params['decimal_places']),
                    round(entry - (tp3_pips * pip_value), params['decimal_places'])
                ]
            ai_data["tp_levels"] = tp_levels
            logger.info(f"Fixed pip TP for {symbol} {signal_type}: Entry={entry}, TP1={tp_levels[0]} (+{tp1_pips}pips), TP2={tp_levels[1]} (+{tp2_pips}pips), TP3={tp_levels[2]} (+{tp3_pips}pips)")
        
        # For ATR-based pairs, ensure all TP levels are different
        elif len(tp_levels) == 3 and len(set(tp_levels)) != 3:
            atr = indicators['atr']
            if signal_type == "BUY":
                tp_levels = [
                    round(entry + (atr * params.get('atr_multiplier_tp1', 1.0)), params['decimal_places']),
                    round(entry + (atr * params.get('atr_multiplier_tp2', 2.0)), params['decimal_places']),
                    round(entry + (atr * params.get('atr_multiplier_tp3', 3.0)), params['decimal_places'])
                ]
            else:
                tp_levels = [
                    round(entry - (atr * params.get('atr_multiplier_tp1', 1.0)), params['decimal_places']),
                    round(entry - (atr * params.get('atr_multiplier_tp2', 2.0)), params['decimal_places']),
                    round(entry - (atr * params.get('atr_multiplier_tp3', 3.0)), params['decimal_places'])
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
    """Generate a complete trading signal for a pair with ML optimization and profitability filters"""
    try:
        # Get pair-specific parameters
        params = PAIR_PARAMETERS.get(pair, DEFAULT_PAIR_PARAMS)
        
        # ============ FILTER 1: SESSION CHECK ============
        if not is_session_optimal(pair):
            current_hour = datetime.utcnow().hour
            logger.info(f"⏰ {pair} skipped - not in optimal session (current hour: {current_hour})")
            return None
        
        # ============ FILTER 2: DRAWDOWN PROTECTION ============
        can_trade, pause_reason = check_drawdown_protection(pair)
        if not can_trade:
            logger.warning(f"🛑 {pair} paused - {pause_reason}")
            return None
        
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
        
        # ============ FILTER 3: CONFIDENCE THRESHOLD ============
        ai_confidence = ai_analysis.get("confidence", 0)
        if ai_confidence < MIN_CONFIDENCE_THRESHOLD:
            logger.info(f"📊 {pair} skipped - confidence {ai_confidence}% < {MIN_CONFIDENCE_THRESHOLD}% threshold")
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
            
            # ============ FILTER 4: REGIME FILTER ============
            if regime_name in SKIP_REGIME:
                logger.info(f"📉 {pair} skipped - {regime_name} regime has lower win rate")
                return None
            
            # ============ FILTER 5: REGIME CONFIDENCE ============
            if regime_confidence < MIN_REGIME_CONFIDENCE:
                logger.info(f"🎯 {pair} skipped - regime confidence {regime_confidence:.2f} < {MIN_REGIME_CONFIDENCE} threshold")
                return None
            
            # ============ FILTER 6: REGIME-BASED DIRECTION ENFORCEMENT ============
            # User's Strategy: Uptrend=BUY only, Downtrend=SELL only, Range=Both
            signal_type = ai_analysis["signal"]
            if regime_name == "TREND_UP" and signal_type == "SELL":
                logger.info(f"📈 {pair} signal changed: SELL→BUY (TREND_UP regime = BUY only)")
                ai_analysis["signal"] = "BUY"
                ai_analysis["analysis"] = f"[TREND_UP - Aligned to BUY] {ai_analysis.get('analysis', '')}"
            elif regime_name == "TREND_DOWN" and signal_type == "BUY":
                logger.info(f"📉 {pair} signal changed: BUY→SELL (TREND_DOWN regime = SELL only)")
                ai_analysis["signal"] = "SELL"
                ai_analysis["analysis"] = f"[TREND_DOWN - Aligned to SELL] {ai_analysis.get('analysis', '')}"
            # RANGE regime: Allow both BUY and SELL (mean reversion strategy)
            
            # Use optimized levels if available
            if optimized.get('optimized'):
                entry_price = optimized.get('entry_price', ai_analysis['entry_price'])
                tp_levels = optimized.get('tp_levels', ai_analysis['tp_levels'])
                sl_price = optimized.get('sl_price', ai_analysis['sl_price'])
            else:
                entry_price = ai_analysis['entry_price']
                tp_levels = ai_analysis['tp_levels']
                sl_price = ai_analysis['sl_price']
            
            logger.info(f"✅ ML Optimization for {pair}: Regime={regime_name}, Conf={regime_confidence:.2f}, RiskMult={risk_multiplier:.2f}")
            
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
        
        # Send push notification to app users
        try:
            push_svc = get_push_service()
            if push_svc:
                await push_svc.send_new_signal_notification({
                    "id": signal.id,
                    "pair": signal.pair,
                    "type": signal.type,
                    "entry_price": signal.entry_price,
                    "confidence": signal.confidence,
                    "regime": regime_name
                })
        except Exception as push_err:
            logger.warning(f"Push notification failed for {pair}: {push_err}")
        
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
        created_at=user["created_at"],
        role=user.get("role", "user")
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
        created_at=user["created_at"],
        role=user.get("role", "user")
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
        created_at=current_user["created_at"],
        role=current_user.get("role", "user")
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

@api_router.get("/signals/history")
async def get_signals_history(
    limit: int = 50,
    pair: Optional[str] = None,
    result: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Get signals history with filters"""
    try:
        query = {}
        if pair:
            query["pair"] = pair.upper()
        if result:
            query["result"] = result.upper()
        
        cursor = db.signals.find(query).sort("created_at", -1).limit(limit)
        signals = await cursor.to_list(length=limit)
        
        # Calculate stats
        total = len(signals)
        wins = sum(1 for s in signals if s.get('result') == 'WIN')
        losses = sum(1 for s in signals if s.get('result') == 'LOSS')
        
        for signal in signals:
            signal['id'] = str(signal.pop('_id'))
        
        return {
            "signals": signals,
            "stats": {
                "total": total,
                "wins": wins,
                "losses": losses,
                "win_rate": round((wins / total * 100) if total > 0 else 0, 2)
            }
        }
    except Exception as e:
        logger.error(f"Error getting signals history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============ SIGNAL OUTCOME TRACKER ENDPOINTS ============
# NOTE: These must come BEFORE /signals/{signal_id} to avoid route conflicts

@api_router.post("/signals/check-outcomes")
async def manual_check_outcomes(current_user: dict = Depends(get_current_user)):
    """Manually trigger a check of all active signals for TP/SL hits"""
    try:
        tracker = get_outcome_tracker()
        if not tracker:
            raise HTTPException(status_code=500, detail="Outcome tracker not initialized")
        
        results = await tracker.check_all_active_signals()
        
        return {
            "success": True,
            "message": "Outcome check completed",
            "results": results
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in manual outcome check: {e}")
        return {"success": False, "error": str(e)}

@api_router.get("/signals/tracker-status")
async def get_tracker_status(current_user: dict = Depends(get_current_user)):
    """Get the status of the signal outcome tracker"""
    try:
        tracker = get_outcome_tracker()
        if not tracker:
            return {
                "success": True,
                "status": "not_initialized",
                "is_running": False
            }
        
        # Count active signals
        active_count = await db.signals.count_documents({"status": "ACTIVE"})
        closed_today = await db.signals.count_documents({
            "closed_at": {"$gte": datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)}
        })
        
        return {
            "success": True,
            "status": "running" if tracker.is_running else "stopped",
            "is_running": tracker.is_running,
            "active_signals": active_count,
            "closed_today": closed_today,
            "check_interval_seconds": 60
        }
    except Exception as e:
        logger.error(f"Error getting tracker status: {e}")
        return {"success": False, "error": str(e)}

@api_router.get("/signals/active")
async def get_active_signals(current_user: dict = Depends(get_current_user)):
    """Get all currently active signals with their current distance from TP/SL"""
    try:
        active_signals = await db.signals.find(
            {"status": "ACTIVE"},
            {"pair": 1, "type": 1, "entry_price": 1, "current_price": 1, "tp_levels": 1, "sl_price": 1, "created_at": 1, "regime": 1}
        ).sort("created_at", -1).to_list(length=100)
        
        signals_with_status = []
        for signal in active_signals:
            signal_data = {
                "id": str(signal["_id"]),
                "pair": signal.get("pair"),
                "type": signal.get("type"),
                "entry_price": signal.get("entry_price"),
                "current_price": signal.get("current_price"),
                "tp_levels": signal.get("tp_levels", []),
                "sl_price": signal.get("sl_price"),
                "created_at": signal.get("created_at").isoformat() if signal.get("created_at") else None,
                "regime": signal.get("regime", "UNKNOWN")
            }
            signals_with_status.append(signal_data)
        
        return {
            "success": True,
            "count": len(signals_with_status),
            "signals": signals_with_status
        }
    except Exception as e:
        logger.error(f"Error getting active signals: {e}")
        return {"success": False, "error": str(e)}

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
    pairs = ["XAUUSD", "XAUEUR", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "EURJPY", "GBPJPY", "AUDUSD", "USDCAD", "USDCHF"]
    
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
        valid_symbols = ["XAUUSD", "XAUEUR", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "EURJPY", "GBPJPY", "AUDUSD", "USDCAD", "USDCHF"]
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
        all_pairs = ["XAUUSD", "XAUEUR", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "EURJPY", "GBPJPY", "AUDUSD", "USDCAD", "USDCHF"]
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
        "pairs": ["XAUUSD", "XAUEUR", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "EURJPY", "GBPJPY", "AUDUSD", "USDCAD", "USDCHF"],
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

class SignalResultUpdate(BaseModel):
    signal_id: str
    result: str  # WIN, LOSS, BREAKEVEN
    exit_price: float
    tp_hit: Optional[int] = None  # 1, 2, or 3

@api_router.post("/ml/update-result")
async def update_signal_result(
    data: SignalResultUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Update signal result for ML training"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        success = await signal_tracker.update_signal_result(
            signal_id=data.signal_id,
            result=data.result,
            exit_price=data.exit_price,
            tp_hit=data.tp_hit
        )
        
        # Also update the signal in the main signals collection
        if success:
            await db.signals.update_one(
                {"_id": ObjectId(data.signal_id)},
                {"$set": {
                    "status": "closed",
                    "result": data.result,
                    "exit_price": data.exit_price,
                    "closed_at": datetime.utcnow()
                }}
            )
        
        return {"success": success}
    except Exception as e:
        logger.error(f"Error updating signal result: {e}")
        return {"success": False, "error": str(e)}

@api_router.get("/prices/live")
async def get_live_prices(current_user: dict = Depends(get_current_user)):
    """Get live prices for all trading pairs"""
    try:
        pairs = ["XAUUSD", "XAUEUR", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "EURJPY", "GBPJPY", "AUDUSD", "USDCAD", "USDCHF"]
        prices = {}
        
        for pair in pairs:
            try:
                df = await get_price_data(pair, interval="1min", outputsize=1)
                if df is not None and len(df) > 0:
                    latest = df.iloc[-1]
                    prices[pair] = {
                        "price": float(latest['close']),
                        "high": float(latest['high']),
                        "low": float(latest['low']),
                        "timestamp": latest['datetime'].isoformat() if hasattr(latest['datetime'], 'isoformat') else str(latest['datetime'])
                    }
                await asyncio.sleep(0.3)  # Rate limiting
            except Exception as e:
                prices[pair] = {"error": str(e)}
        
        return {
            "success": True,
            "timestamp": datetime.utcnow().isoformat(),
            "prices": prices
        }
    except Exception as e:
        logger.error(f"Error getting live prices: {e}")
        return {"success": False, "error": str(e)}

@api_router.get("/ml/smc/{symbol}")
async def get_smc_analysis(symbol: str, current_user: dict = Depends(get_current_user)):
    """Get Smart Money Concepts analysis for a symbol"""
    try:
        # Validate symbol
        valid_symbols = ["XAUUSD", "XAUEUR", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "EURJPY", "GBPJPY", "AUDUSD", "USDCAD", "USDCHF"]
        symbol = symbol.upper()
        
        if symbol not in valid_symbols:
            raise HTTPException(status_code=400, detail=f"Invalid symbol. Valid: {valid_symbols}")
        
        # Get price data
        df = await get_price_data(symbol, interval="1h", outputsize=100)
        if df is None or len(df) < 50:
            raise HTTPException(status_code=400, detail="Insufficient data for SMC analysis")
        
        # Run SMC analysis
        analysis = smc_analyzer.analyze(df, symbol)
        
        return {
            "success": True,
            "analysis": analysis
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"SMC analysis error for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/ml/quality-filter")
async def get_quality_filter_status(current_user: dict = Depends(get_current_user)):
    """Get signal quality filter status"""
    try:
        summary = signal_quality_filter.get_quality_summary()
        return {
            "success": True,
            "filter_status": summary
        }
    except Exception as e:
        logger.error(f"Error getting quality filter status: {e}")
        return {"success": False, "error": str(e)}

@api_router.get("/ml/full-analysis/{symbol}")
async def get_full_analysis(symbol: str, current_user: dict = Depends(get_current_user)):
    """Get comprehensive analysis for a symbol (Regime + MTF + SMC)"""
    try:
        valid_symbols = ["XAUUSD", "XAUEUR", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "EURJPY", "GBPJPY", "AUDUSD", "USDCAD", "USDCHF"]
        symbol = symbol.upper()
        
        if symbol not in valid_symbols:
            raise HTTPException(status_code=400, detail=f"Invalid symbol")
        
        # Get price data
        df = await get_price_data(symbol, interval="1h", outputsize=100)
        if df is None or len(df) < 50:
            raise HTTPException(status_code=400, detail="Insufficient data")
        
        # Run all analyses
        results = {
            "symbol": symbol,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        # 1. Regime Analysis
        features = signal_optimizer.feature_engineer.extract_features(df, symbol)
        if features:
            results["regime"] = signal_optimizer.regime_detector.detect_regime(features)
        
        # 2. MTF Analysis
        results["mtf"] = await mtf_analyzer.analyze(symbol)
        
        # 3. SMC Analysis
        results["smc"] = smc_analyzer.analyze(df, symbol)
        
        # 4. Quality Assessment
        if results.get("regime") and results.get("mtf") and results.get("smc"):
            should_trade, reason, quality = signal_quality_filter.should_take_signal(
                symbol=symbol,
                signal_type=results["mtf"].get("trade_direction", "NEUTRAL"),
                confidence=70,  # Placeholder
                regime_result=results["regime"],
                mtf_result=results["mtf"],
                smc_result=results["smc"]
            )
            results["quality_assessment"] = {
                "should_trade": should_trade,
                "reason": reason,
                "quality_score": quality.get("quality_score", 0),
                "checks_passed": quality.get("checks_passed", 0),
                "checks_total": quality.get("checks_total", 0)
            }
        
        # Serialize numpy types for JSON response
        serialized_results = serialize_numpy(results)
        
        return {
            "success": True,
            "analysis": serialized_results
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Full analysis error for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
    
    # Win rate calculation - count closed signals with new status format
    closed_statuses = ["CLOSED_TP1", "CLOSED_TP2", "CLOSED_TP3", "CLOSED_SL", "HIT_TP", "HIT_SL"]
    closed_signals = await db.signals.find(
        {"status": {"$in": closed_statuses}},
        {"result": 1, "pips": 1}
    ).to_list(5000)
    
    # Calculate wins and losses
    wins = sum(1 for s in closed_signals if s.get("result") == "WIN")
    losses = sum(1 for s in closed_signals if s.get("result") == "LOSS")
    total_closed = wins + losses
    
    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0
    
    # Average pips (only count signals with pips data)
    signals_with_pips = [s for s in closed_signals if s.get("pips") is not None]
    avg_pips = sum(s.get("pips", 0) for s in signals_with_pips) / len(signals_with_pips) if signals_with_pips else 0
    
    return {
        "total_signals": total_signals,
        "active_signals": active_signals,
        "win_rate": round(win_rate, 2),
        "avg_pips": round(avg_pips, 2),
        "total_closed": total_closed,
        "wins": wins,
        "losses": losses
    }

# ============ PUSH NOTIFICATION ENDPOINTS ============
class PushTokenRegister(BaseModel):
    push_token: str
    device_type: Optional[str] = "unknown"

@api_router.post("/notifications/register")
async def register_push_token(
    data: PushTokenRegister,
    current_user: dict = Depends(get_current_user)
):
    """Register a user's push notification token"""
    try:
        push_svc = get_push_service()
        if not push_svc:
            raise HTTPException(status_code=500, detail="Push service not initialized")
        
        success = await push_svc.register_push_token(
            user_id=str(current_user["_id"]),
            push_token=data.push_token,
            device_type=data.device_type
        )
        
        return {"success": success}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error registering push token: {e}")
        return {"success": False, "error": str(e)}

@api_router.delete("/notifications/unregister")
async def unregister_push_token(current_user: dict = Depends(get_current_user)):
    """Unregister a user's push notification token"""
    try:
        push_svc = get_push_service()
        if not push_svc:
            raise HTTPException(status_code=500, detail="Push service not initialized")
        
        success = await push_svc.unregister_push_token(str(current_user["_id"]))
        return {"success": success}
    except Exception as e:
        logger.error(f"Error unregistering push token: {e}")
        return {"success": False, "error": str(e)}

@api_router.post("/notifications/test")
async def test_push_notification(current_user: dict = Depends(get_current_user)):
    """Send a test push notification to the current user"""
    try:
        push_svc = get_push_service()
        if not push_svc:
            raise HTTPException(status_code=500, detail="Push service not initialized")
        
        # Get user's token
        token_doc = await db.push_tokens.find_one({
            "user_id": str(current_user["_id"]),
            "is_active": True
        })
        
        if not token_doc:
            return {"success": False, "error": "No push token registered"}
        
        result = await push_svc.send_notification(
            push_tokens=[token_doc["push_token"]],
            title="Test Notification",
            body="Push notifications are working!",
            data={"type": "test"}
        )
        
        return {"success": result["success"] > 0, "result": result}
    except Exception as e:
        logger.error(f"Error sending test notification: {e}")
        return {"success": False, "error": str(e)}

# ============ PERFORMANCE CHART ENDPOINTS ============
@api_router.get("/performance/daily")
async def get_daily_performance(
    days: int = 30,
    current_user: dict = Depends(get_current_user)
):
    """Get daily performance data for charts"""
    try:
        from_date = datetime.utcnow() - timedelta(days=days)
        
        # Aggregate signals by day
        pipeline = [
            {"$match": {
                "closed_at": {"$gte": from_date},
                "status": {"$in": ["CLOSED_TP1", "CLOSED_TP2", "CLOSED_TP3", "CLOSED_SL"]}
            }},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$closed_at"}},
                "total_trades": {"$sum": 1},
                "wins": {"$sum": {"$cond": [{"$eq": ["$result", "WIN"]}, 1, 0]}},
                "losses": {"$sum": {"$cond": [{"$eq": ["$result", "LOSS"]}, 1, 0]}},
                "total_pips": {"$sum": {"$ifNull": ["$pips", 0]}}
            }},
            {"$sort": {"_id": 1}}
        ]
        
        results = await db.signals.aggregate(pipeline).to_list(100)
        
        # Format for chart
        labels = []
        pips_data = []
        win_rate_data = []
        
        for r in results:
            labels.append(r["_id"][5:])  # MM-DD format
            pips_data.append(round(r["total_pips"], 1))
            wr = (r["wins"] / r["total_trades"] * 100) if r["total_trades"] > 0 else 0
            win_rate_data.append(round(wr, 1))
        
        return {
            "success": True,
            "labels": labels,
            "datasets": {
                "pips": pips_data,
                "win_rate": win_rate_data
            }
        }
    except Exception as e:
        logger.error(f"Error getting daily performance: {e}")
        return {"success": False, "error": str(e)}

@api_router.get("/performance/by-pair")
async def get_performance_by_pair(current_user: dict = Depends(get_current_user)):
    """Get performance breakdown by trading pair"""
    try:
        pipeline = [
            {"$match": {
                "status": {"$in": ["CLOSED_TP1", "CLOSED_TP2", "CLOSED_TP3", "CLOSED_SL"]}
            }},
            {"$group": {
                "_id": "$pair",
                "total_trades": {"$sum": 1},
                "wins": {"$sum": {"$cond": [{"$eq": ["$result", "WIN"]}, 1, 0]}},
                "total_pips": {"$sum": {"$ifNull": ["$pips", 0]}}
            }},
            {"$sort": {"total_trades": -1}}
        ]
        
        results = await db.signals.aggregate(pipeline).to_list(20)
        
        formatted = []
        for r in results:
            win_rate = (r["wins"] / r["total_trades"] * 100) if r["total_trades"] > 0 else 0
            formatted.append({
                "pair": r["_id"],
                "trades": r["total_trades"],
                "wins": r["wins"],
                "win_rate": round(win_rate, 1),
                "pips": round(r["total_pips"], 1)
            })
        
        return {
            "success": True,
            "pairs": formatted
        }
    except Exception as e:
        logger.error(f"Error getting performance by pair: {e}")
        return {"success": False, "error": str(e)}

# ============ BACKTEST ENGINE ENDPOINTS ============
class BacktestRequest(BaseModel):
    pair: str
    start_year: int = 2020
    end_year: int = 2025
    timeframe: str = "1h"
    tp1_pips: float = 5.0
    tp2_pips: float = 10.0
    tp3_pips: float = 15.0
    sl_pips: float = 15.0
    use_atr_for_sl: bool = True
    atr_sl_multiplier: float = 1.5
    initial_balance: float = 10000.0
    risk_per_trade: float = 0.02

@api_router.post("/backtest/run")
async def run_backtest(
    request: BacktestRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """
    Run a historical backtest for a trading pair.
    Supports 3-10 years of historical data analysis.
    """
    try:
        engine = get_backtest_engine()
        if not engine:
            raise HTTPException(status_code=500, detail="Backtest engine not initialized")
        
        # Validate date range
        years = request.end_year - request.start_year
        if years < 1 or years > 10:
            raise HTTPException(status_code=400, detail="Date range must be 1-10 years")
        
        # Create backtest config
        config = BacktestConfig(
            pair=request.pair,
            start_date=datetime(request.start_year, 1, 1),
            end_date=datetime(request.end_year, 12, 31),
            timeframe=request.timeframe,
            initial_balance=request.initial_balance,
            risk_per_trade=request.risk_per_trade,
            tp1_pips=request.tp1_pips,
            tp2_pips=request.tp2_pips,
            tp3_pips=request.tp3_pips,
            sl_pips=request.sl_pips,
            use_atr_for_sl=request.use_atr_for_sl,
            atr_sl_multiplier=request.atr_sl_multiplier
        )
        
        # Run backtest
        logger.info(f"Starting backtest for {request.pair} ({request.start_year}-{request.end_year})")
        results = await engine.run_backtest(config)
        
        # Save results to database
        result_doc = {
            "user_id": str(current_user["_id"]),
            "pair": request.pair,
            "config": {
                "start_year": request.start_year,
                "end_year": request.end_year,
                "timeframe": request.timeframe,
                "tp1_pips": request.tp1_pips,
                "tp2_pips": request.tp2_pips,
                "tp3_pips": request.tp3_pips,
                "sl_pips": request.sl_pips,
            },
            "results": results.to_dict(),
            "created_at": datetime.utcnow()
        }
        await db.backtest_results.insert_one(result_doc)
        
        return {
            "success": True,
            "message": f"Backtest completed for {request.pair}",
            "results": results.to_dict()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Backtest error: {e}")
        return {"success": False, "error": str(e)}

@api_router.get("/backtest/history")
async def get_backtest_history(
    limit: int = 10,
    current_user: dict = Depends(get_current_user)
):
    """Get user's backtest history"""
    try:
        history = await db.backtest_results.find(
            {"user_id": str(current_user["_id"])}
        ).sort("created_at", -1).limit(limit).to_list(limit)
        
        # Format for response
        formatted = []
        for item in history:
            formatted.append({
                "id": str(item["_id"]),
                "pair": item.get("pair"),
                "config": item.get("config"),
                "summary": item.get("results", {}).get("summary", {}),
                "created_at": item.get("created_at").isoformat() if item.get("created_at") else None
            })
        
        return {
            "success": True,
            "count": len(formatted),
            "history": formatted
        }
    except Exception as e:
        logger.error(f"Error getting backtest history: {e}")
        return {"success": False, "error": str(e)}

@api_router.get("/backtest/result/{result_id}")
async def get_backtest_result(
    result_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get detailed backtest result by ID"""
    try:
        result = await db.backtest_results.find_one({
            "_id": ObjectId(result_id),
            "user_id": str(current_user["_id"])
        })
        
        if not result:
            raise HTTPException(status_code=404, detail="Backtest result not found")
        
        return {
            "success": True,
            "result": {
                "id": str(result["_id"]),
                "pair": result.get("pair"),
                "config": result.get("config"),
                "results": result.get("results"),
                "created_at": result.get("created_at").isoformat() if result.get("created_at") else None
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting backtest result: {e}")
        return {"success": False, "error": str(e)}

@api_router.get("/backtest/pairs")
async def get_available_pairs(current_user: dict = Depends(get_current_user)):
    """Get list of pairs available for backtesting"""
    return {
        "success": True,
        "pairs": [
            {"symbol": "XAUUSD", "name": "Gold / US Dollar", "type": "commodity"},
            {"symbol": "XAUEUR", "name": "Gold / Euro", "type": "commodity"},
            {"symbol": "BTCUSD", "name": "Bitcoin / US Dollar", "type": "crypto"},
            {"symbol": "EURUSD", "name": "Euro / US Dollar", "type": "forex"},
            {"symbol": "GBPUSD", "name": "British Pound / US Dollar", "type": "forex"},
            {"symbol": "USDJPY", "name": "US Dollar / Japanese Yen", "type": "forex"},
            {"symbol": "EURJPY", "name": "Euro / Japanese Yen", "type": "forex"},
            {"symbol": "GBPJPY", "name": "British Pound / Japanese Yen", "type": "forex"},
            {"symbol": "AUDUSD", "name": "Australian Dollar / US Dollar", "type": "forex"},
            {"symbol": "USDCAD", "name": "US Dollar / Canadian Dollar", "type": "forex"},
            {"symbol": "USDCHF", "name": "US Dollar / Swiss Franc", "type": "forex"},
        ],
        "timeframes": [
            {"value": "1h", "label": "1 Hour"},
            {"value": "4h", "label": "4 Hours"},
            {"value": "1day", "label": "Daily"},
        ],
        "year_range": {"min": 2015, "max": 2025}
    }

# ============ ADMIN ENDPOINTS ============
def require_admin(current_user: dict = Depends(get_current_user)):
    """Dependency that requires admin role"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user

@api_router.get("/admin/users")
async def get_all_users(admin_user: dict = Depends(require_admin)):
    """Get all users (admin only)"""
    try:
        users = await db.users.find({}).to_list(1000)
        formatted = []
        for user in users:
            formatted.append({
                "id": str(user["_id"]),
                "email": user.get("email"),
                "role": user.get("role", "user"),
                "created_at": user.get("created_at").isoformat() if user.get("created_at") else None,
                "subscription_status": user.get("subscription_status", "free")
            })
        return {"success": True, "users": formatted}
    except Exception as e:
        logger.error(f"Error getting users: {e}")
        return {"success": False, "error": str(e)}

@api_router.post("/admin/signals/{signal_id}/close")
async def admin_close_signal(
    signal_id: str,
    data: dict,
    admin_user: dict = Depends(require_admin)
):
    """Manually close a signal (admin only)"""
    try:
        status = data.get("status", "CLOSED_MANUAL")
        result = "WIN" if "WIN" in status else "LOSS"
        
        update_result = await db.signals.update_one(
            {"_id": ObjectId(signal_id)},
            {"$set": {
                "status": status,
                "result": result,
                "closed_at": datetime.utcnow(),
                "closed_by": "admin"
            }}
        )
        
        if update_result.modified_count > 0:
            return {"success": True, "message": "Signal closed"}
        return {"success": False, "error": "Signal not found"}
    except Exception as e:
        logger.error(f"Error closing signal: {e}")
        return {"success": False, "error": str(e)}

@api_router.delete("/admin/signals/{signal_id}")
async def admin_delete_signal(
    signal_id: str,
    admin_user: dict = Depends(require_admin)
):
    """Delete a signal (admin only)"""
    try:
        delete_result = await db.signals.delete_one({"_id": ObjectId(signal_id)})
        if delete_result.deleted_count > 0:
            return {"success": True, "message": "Signal deleted"}
        return {"success": False, "error": "Signal not found"}
    except Exception as e:
        logger.error(f"Error deleting signal: {e}")
        return {"success": False, "error": str(e)}

# ============ MANUAL SIGNAL CREATION ============
class ManualSignalRequest(BaseModel):
    pair: str
    type: str  # BUY or SELL
    entry_price: float
    tp1: float
    tp2: float
    tp3: float
    sl: float
    send_telegram: bool = True

@api_router.post("/admin/signals/create")
async def admin_create_signal(
    signal: ManualSignalRequest,
    admin_user: dict = Depends(require_admin)
):
    """Create a manual trading signal (admin only)"""
    try:
        # Validate pair
        valid_pairs = list(PAIR_PARAMETERS.keys())
        if signal.pair not in valid_pairs:
            return {"success": False, "error": f"Invalid pair. Valid pairs: {valid_pairs}"}
        
        # Validate type
        if signal.type not in ["BUY", "SELL"]:
            return {"success": False, "error": "Type must be BUY or SELL"}
        
        # Create signal document
        signal_doc = {
            "pair": signal.pair,
            "type": signal.type,
            "entry_price": signal.entry_price,
            "tp_levels": [signal.tp1, signal.tp2, signal.tp3],
            "sl_price": signal.sl,
            "status": "ACTIVE",
            "created_at": datetime.utcnow(),
            "created_by": "admin_manual",
            "regime": "MANUAL",
            "confidence": 100.0,
            "ml_optimized": False
        }
        
        # Insert into database
        result = await db.signals.insert_one(signal_doc)
        signal_id = str(result.inserted_id)
        
        # Send to Telegram if requested
        if signal.send_telegram:
            try:
                message = f"""
🎯 *MANUAL SIGNAL* 🎯

📊 *{signal.pair}* - *{signal.type}*
💰 Entry: {signal.entry_price}

🎯 Take Profits:
   TP1: {signal.tp1}
   TP2: {signal.tp2}
   TP3: {signal.tp3}

🛡️ Stop Loss: {signal.sl}

📌 *Created by Admin*
⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
"""
                telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
                telegram_channel = os.environ.get("TELEGRAM_CHANNEL_ID", "@grandcomsignals")
                
                if telegram_token:
                    async with httpx.AsyncClient() as client:
                        await client.post(
                            f"https://api.telegram.org/bot{telegram_token}/sendMessage",
                            json={
                                "chat_id": telegram_channel,
                                "text": message,
                                "parse_mode": "Markdown"
                            }
                        )
                    logger.info(f"Manual signal sent to Telegram: {signal.pair} {signal.type}")
            except Exception as tg_error:
                logger.error(f"Failed to send to Telegram: {tg_error}")
        
        return {
            "success": True,
            "signal_id": signal_id,
            "message": f"Signal created for {signal.pair} {signal.type}"
        }
    except Exception as e:
        logger.error(f"Error creating manual signal: {e}")
        return {"success": False, "error": str(e)}

# ============ USER MANAGEMENT ============
class UserUpdateRequest(BaseModel):
    role: Optional[str] = None
    subscription_tier: Optional[str] = None

@api_router.put("/admin/users/{user_id}")
async def admin_update_user(
    user_id: str,
    update: UserUpdateRequest,
    admin_user: dict = Depends(require_admin)
):
    """Update user details (admin only)"""
    try:
        update_data = {}
        
        if update.role:
            if update.role not in ["user", "admin", "premium"]:
                return {"success": False, "error": "Invalid role. Must be: user, admin, or premium"}
            update_data["role"] = update.role
        
        if update.subscription_tier:
            if update.subscription_tier not in ["free", "pro", "premium"]:
                return {"success": False, "error": "Invalid tier. Must be: free, pro, or premium"}
            update_data["subscription_tier"] = update.subscription_tier.upper()
            update_data["subscription_status"] = "active" if update.subscription_tier != "free" else "free"
        
        if not update_data:
            return {"success": False, "error": "No update fields provided"}
        
        result = await db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": update_data}
        )
        
        if result.modified_count > 0:
            return {"success": True, "message": "User updated"}
        return {"success": False, "error": "User not found or no changes made"}
    except Exception as e:
        logger.error(f"Error updating user: {e}")
        return {"success": False, "error": str(e)}

@api_router.delete("/admin/users/{user_id}")
async def admin_delete_user(
    user_id: str,
    admin_user: dict = Depends(require_admin)
):
    """Delete a user (admin only)"""
    try:
        # Prevent deleting admin user
        user = await db.users.find_one({"_id": ObjectId(user_id)})
        if user and user.get("role") == "admin":
            return {"success": False, "error": "Cannot delete admin user"}
        
        result = await db.users.delete_one({"_id": ObjectId(user_id)})
        if result.deleted_count > 0:
            return {"success": True, "message": "User deleted"}
        return {"success": False, "error": "User not found"}
    except Exception as e:
        logger.error(f"Error deleting user: {e}")
        return {"success": False, "error": str(e)}

@api_router.get("/admin/pair-config")
async def get_pair_config(admin_user: dict = Depends(require_admin)):
    """Get current pair configuration (admin only)"""
    return {
        "success": True,
        "pairs": PAIR_PARAMETERS,
        "valid_pairs": list(PAIR_PARAMETERS.keys())
    }

@api_router.get("/admin/filters")
async def get_profitability_filters(admin_user: dict = Depends(require_admin)):
    """Get current profitability filter settings (admin only)"""
    return {
        "success": True,
        "filters": {
            "regime_filter": {
                "allowed_regimes": ALLOWED_REGIMES,
                "skip_regimes": SKIP_REGIME,
                "description": "Only trade in trending markets"
            },
            "confidence_filter": {
                "min_ai_confidence": MIN_CONFIDENCE_THRESHOLD,
                "min_regime_confidence": MIN_REGIME_CONFIDENCE,
                "description": "Require high ML confidence before trading"
            },
            "session_filter": {
                "pairs": SESSION_FILTERS,
                "current_hour_utc": datetime.utcnow().hour,
                "description": "Trade pairs only during optimal sessions"
            },
            "drawdown_protection": {
                **DRAWDOWN_PROTECTION,
                "current_status": daily_pair_performance,
                "description": "Auto-pause pairs after consecutive losses"
            }
        }
    }

@api_router.get("/admin/filter-stats")
async def get_filter_statistics(admin_user: dict = Depends(require_admin)):
    """Get filter impact statistics (admin only)"""
    # Get recent signals to analyze filter impact
    recent_signals = []
    async for signal in db.signals.find({
        "created_at": {"$gte": datetime.utcnow() - timedelta(hours=24)}
    }).sort("created_at", -1).limit(100):
        signal['id'] = str(signal.pop('_id'))
        recent_signals.append(signal)
    
    # Analyze regime distribution
    regime_counts = {}
    for signal in recent_signals:
        regime = signal.get('regime', 'UNKNOWN')
        if regime not in regime_counts:
            regime_counts[regime] = {'total': 0, 'wins': 0, 'losses': 0}
        regime_counts[regime]['total'] += 1
        if signal.get('result') == 'WIN':
            regime_counts[regime]['wins'] += 1
        elif signal.get('result') == 'LOSS':
            regime_counts[regime]['losses'] += 1
    
    return {
        "success": True,
        "last_24h": {
            "total_signals": len(recent_signals),
            "regime_distribution": regime_counts
        },
        "filter_impact": {
            "regime_filter": "Blocking RANGE and VOLATILE regimes",
            "confidence_filter": f"Requiring >{MIN_CONFIDENCE_THRESHOLD}% AI confidence",
            "session_filter": "Trading only during optimal hours"
        }
    }

@api_router.post("/admin/ml/optimize")
async def run_ml_optimization(admin_user: dict = Depends(require_admin)):
    """Run ML model optimization based on historical signals (admin only)"""
    try:
        from ml_engine.model_trainer import run_model_optimization
        results = await run_model_optimization(db)
        return results
    except Exception as e:
        logger.error(f"ML optimization error: {e}")
        return {"success": False, "error": str(e)}

@api_router.get("/admin/ml/performance")
async def get_ml_performance_analysis(admin_user: dict = Depends(require_admin)):
    """Get detailed ML performance analysis (admin only)"""
    try:
        from ml_engine.model_trainer import SignalOptimizationEngine
        
        # Fetch signals with results
        signals = []
        async for signal in db.signals.find({'result': {'$in': ['WIN', 'LOSS']}}).sort('created_at', -1).limit(500):
            signal['id'] = str(signal.pop('_id'))
            signals.append(signal)
        
        if len(signals) < 10:
            return {"success": True, "message": "Not enough data yet", "signals_analyzed": len(signals)}
        
        optimizer = SignalOptimizationEngine()
        
        pair_analysis = optimizer.analyze_performance_by_pair(signals)
        regime_analysis = optimizer.analyze_performance_by_regime(signals)
        recommendations = optimizer.recommend_pair_settings(pair_analysis)
        
        # Sort by win rate
        sorted_pairs = sorted(
            [(pair, stats) for pair, stats in pair_analysis.items()],
            key=lambda x: x[1].get('win_rate', 0),
            reverse=True
        )
        
        return {
            "success": True,
            "signals_analyzed": len(signals),
            "pair_rankings": [
                {
                    "pair": pair,
                    "win_rate": round(stats.get('win_rate', 0), 2),
                    "profit_factor": round(stats.get('profit_factor', 0), 2),
                    "total_trades": stats.get('total', 0),
                    "total_pips": round(stats.get('total_pips', 0), 1)
                }
                for pair, stats in sorted_pairs
            ],
            "regime_performance": {
                regime: {
                    "win_rate": round(stats.get('win_rate', 0), 2),
                    "total_trades": stats.get('total', 0)
                }
                for regime, stats in regime_analysis.items()
            },
            "recommendations": recommendations
        }
    except Exception as e:
        logger.error(f"Performance analysis error: {e}")
        return {"success": False, "error": str(e)}

@api_router.get("/admin/system-config")
async def get_system_config(admin_user: dict = Depends(require_admin)):
    """Get current system configuration (admin only)"""
    tracker = get_outcome_tracker()
    
    # Get active pairs count
    active_pairs = [p for p, c in PAIR_PARAMETERS.items() if c.get('enabled', True)]
    disabled_pairs = [p for p, c in PAIR_PARAMETERS.items() if not c.get('enabled', True)]
    
    return {
        "success": True,
        "config": {
            "signal_generation": {
                "interval_minutes": 15,
                "total_pairs": len(PAIR_PARAMETERS),
                "active_pairs": len(active_pairs),
                "active_pairs_list": active_pairs,
                "disabled_pairs": disabled_pairs
            },
            "tp_sl": {
                "forex": {"tp1": 3, "tp2": 6, "tp3": 9, "sl": 10, "note": "OPTIMIZED - Conservative"},
                "xauusd": {"tp1": 7, "tp2": 15, "tp3": 25, "sl": "ATR-based", "note": "MONITORING - 1 month trial"},
                "xaueur": {"tp1": 5, "tp2": 10, "tp3": 15, "sl": "ATR-based", "note": "TOP PERFORMER: +4847 pips, 96% WR"},
                "audusd": {"tp1": 2, "tp2": 4, "tp3": 6, "sl": 8, "note": "ADJUSTED - Ultra-conservative"},
                "btcusd": {"status": "DISABLED", "reason": "17.5% win rate, PF 0.14"}
            },
            "partial_close": {
                "tp1_percent": 33,
                "tp2_percent": 33,
                "tp3_percent": 34
            },
            "outcome_tracker": {
                "status": "running" if tracker and tracker.is_running else "stopped",
                "check_interval_seconds": 60
            },
            "optimization_notes": {
                "forex": "Conservative (3/6/9) - PF +11% avg, WR +15-20%",
                "xauusd": "Balanced (7/15/25) - PF 1.27, Return 1114%",
                "xaueur": "Current (5/10/15) - PF 1.27, WR 63.9%",
                "audusd": "Ultra-conservative (2/4/6) - Adjusted for low WR",
                "btcusd": "DISABLED due to poor performance"
            }
        }
    }

# ============ STRIPE SUBSCRIPTION ENDPOINTS ============
class CreateCheckoutRequest(BaseModel):
    package_id: str

@api_router.post("/subscriptions/create-checkout-session")
async def create_checkout_session(
    request: CreateCheckoutRequest,
    current_user: dict = Depends(get_current_user)
):
    """Create a Stripe checkout session for subscription"""
    try:
        sub_service = get_subscription_service()
        if not sub_service:
            raise HTTPException(status_code=500, detail="Subscription service not available")
        
        # Get the origin URL from environment (will be set by Emergent in production)
        origin_url = os.environ.get('FRONTEND_URL', os.environ.get('EXPO_PUBLIC_BACKEND_URL', ''))
        
        result = await sub_service.create_checkout_session(
            user_id=str(current_user["_id"]),
            package_id=request.package_id,
            origin_url=origin_url
        )
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating checkout session: {e}")
        return {"success": False, "error": str(e)}

@api_router.get("/subscriptions/packages")
async def get_subscription_packages():
    """Get available subscription packages"""
    return {
        "success": True,
        "packages": SUBSCRIPTION_PACKAGES,
        "tier_features": TIER_FEATURES
    }

@api_router.get("/subscriptions/current")
async def get_current_subscription(current_user: dict = Depends(get_current_user)):
    """Get current user's subscription status"""
    try:
        sub_service = get_subscription_service()
        if not sub_service:
            return {
                "success": True,
                "tier": current_user.get("subscription_tier", "FREE"),
                "features": TIER_FEATURES.get(current_user.get("subscription_tier", "FREE").lower(), TIER_FEATURES["free"])
            }
        
        subscription = await sub_service.get_user_subscription(str(current_user["_id"]))
        return {"success": True, **subscription}
    except Exception as e:
        logger.error(f"Error getting subscription: {e}")
        return {"success": False, "error": str(e)}

@api_router.get("/subscriptions/verify/{session_id}")
async def verify_subscription_payment(
    session_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Verify payment status after checkout"""
    try:
        sub_service = get_subscription_service()
        if not sub_service:
            raise HTTPException(status_code=500, detail="Subscription service not available")
        
        result = await sub_service.verify_payment(session_id)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error verifying payment: {e}")
        return {"success": False, "error": str(e)}

@api_router.post("/subscriptions/cancel")
async def cancel_subscription(current_user: dict = Depends(get_current_user)):
    """Cancel current subscription"""
    try:
        sub_service = get_subscription_service()
        if not sub_service:
            raise HTTPException(status_code=500, detail="Subscription service not available")
        
        result = await sub_service.cancel_subscription(str(current_user["_id"]))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling subscription: {e}")
        return {"success": False, "error": str(e)}

# Stripe webhook endpoint (no auth - called by Stripe)
from fastapi import Request

@app.post("/api/webhook/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events"""
    try:
        payload = await request.body()
        sig_header = request.headers.get('stripe-signature')
        
        # For now, just log the webhook - full implementation would verify signature
        logger.info(f"Received Stripe webhook")
        
        # Parse the event
        import json
        event = json.loads(payload)
        event_type = event.get('type', '')
        
        if event_type == 'checkout.session.completed':
            session = event.get('data', {}).get('object', {})
            session_id = session.get('id')
            
            if session_id:
                sub_service = get_subscription_service()
                if sub_service:
                    await sub_service.verify_payment(session_id)
                    logger.info(f"Processed checkout completion for session: {session_id}")
        
        return {"received": True}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"received": True, "error": str(e)}

# ============ BACKGROUND TASKS ============
async def auto_generate_signals():
    """Background task to auto-generate signals every 15 minutes"""
    # Get active pairs (filter out disabled ones)
    active_pairs = [
        pair for pair, config in PAIR_PARAMETERS.items()
        if config.get('enabled', True)  # Default to enabled if not specified
    ]
    
    logger.info(f"Active trading pairs: {active_pairs}")
    
    while True:
        try:
            logger.info("Starting automatic signal generation...")
            for pair in active_pairs:
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
    
    # Initialize and start Signal Outcome Tracker (checks TP/SL every 60 seconds)
    tracker = init_outcome_tracker(
        db=db,
        twelve_data_api_key=TWELVE_DATA_API_KEY,
        telegram_bot_token=TELEGRAM_BOT_TOKEN,
        telegram_channel_id=os.environ.get('TELEGRAM_CHANNEL_ID', '@grandcomsignals')
    )
    tracker.start(interval_seconds=60)  # Check every minute
    logger.info("Signal Outcome Tracker started - monitoring TP/SL levels every 60 seconds")
    
    # Initialize Push Notification Service
    init_push_service(db)
    logger.info("Push Notification Service initialized")
    
    # Initialize Backtest Engine
    init_backtest_engine(TWELVE_DATA_API_KEY, db)
    logger.info("Backtest Engine initialized - ready for historical analysis")
    
    # Initialize Subscription Service
    if STRIPE_API_KEY:
        init_subscription_service(db, STRIPE_API_KEY)
        logger.info("Subscription Service initialized")
    
    # Start auto signal generation in background
    asyncio.create_task(auto_generate_signals())

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()

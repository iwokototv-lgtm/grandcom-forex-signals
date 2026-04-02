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
from signal_outcome_tracker import SignalOutcomeTracker, init_outcome_tracker, get_outcome_tracker

# Import Push Notification Service
from notification_service import PushNotificationService, init_push_service, get_push_service

# Import Backtest Engine
from backtest_engine import BacktestEngine, BacktestConfig, init_backtest_engine, get_backtest_engine

# Import Subscription Service
from subscription_service import (
    SubscriptionService, init_subscription_service, get_subscription_service,
    SUBSCRIPTION_PACKAGES, TIER_FEATURES
)

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
from ml_engine import (
    FeatureEngineer, RegimeDetector, RiskManager, SignalOptimizer, 
    mtf_analyzer, historical_collector, signal_tracker,
    smc_analyzer, signal_quality_filter, regime_enforced_tpsl
)

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
MIN_CONFIDENCE_THRESHOLD = 70  # RAISED from 60 → 70 (Phase 1: false signal reduction)
MIN_REGIME_CONFIDENCE = 0.55   # Lowered for more signals
HIGH_CONFIDENCE_THRESHOLD = 75

# Gold pairs require stricter confidence for premium, reliable signals
GOLD_PAIRS = ["XAUUSD", "XAUEUR"]
GOLD_CONFIDENCE_THRESHOLD = 75  # Gold pairs: 75% confidence (premium filtering)

# 2b. SIGNAL THROTTLE - minimum minutes between signals on the same pair
SIGNAL_THROTTLE_MINUTES = 45  # RAISED from 30 → 45 min (Phase 1: false signal reduction)

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
        
        # Use Emergent LLM integration with retry logic
        max_retries = 3
        ai_response = None
        
        for attempt in range(max_retries):
            try:
                # chat = LlmChat(
                #     api_key=EMERGENT_LLM_KEY,
                #     session_id=f"signal_{symbol}_{datetime.utcnow().timestamp()}_{attempt}",
                #     system_message=system_message
                # ).with_model("openai", "gpt-4o-mini")
                #
                # user_msg = UserMessage(text=user_message)
                # ai_response = await chat.send_message(user_msg)
                raise NotImplementedError("emergentintegrations module is not available")
                
                if ai_response and len(ai_response.strip()) > 10:
                    break
                else:
                    logger.warning(f"Empty response for {symbol}, attempt {attempt + 1}/{max_retries}")
                    await asyncio.sleep(1)
            except Exception as retry_error:
                logger.warning(f"LLM retry {attempt + 1}/{max_retries} for {symbol}: {retry_error}")
                await asyncio.sleep(1)
        
        if not ai_response or len(ai_response.strip()) < 10:
            logger.error(f"Failed to get valid AI response for {symbol} after {max_retries} attempts")
            return None
        
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


# ============ SIGNAL QUALITY HELPERS (Phase 1: False Signal Reduction) ============

async def check_higher_timeframe_alignment(pair: str, signal_direction: str) -> tuple[bool, str]:
    """
    Multi-timeframe confirmation: require H4 and D1 to agree with the signal direction.
    
    Returns (confirmed: bool, reason: str)
    - confirmed=True  → H4 and D1 both align with signal_direction (BUY/SELL)
    - confirmed=False → H4 or D1 disagrees; signal should be skipped
    
    Alignment logic:
    - BUY signal: H4 trend must be BULLISH and D1 trend must be BULLISH
    - SELL signal: H4 trend must be BEARISH and D1 trend must be BEARISH
    - If either timeframe is NEUTRAL the signal is allowed (insufficient data to block)
    """
    try:
        # Fetch H4 data (50 candles ≈ ~8 days)
        h4_df = await get_price_data(pair, interval="4h", outputsize=50)
        await asyncio.sleep(0.3)
        # Fetch D1 data (30 candles ≈ 1 month)
        d1_df = await get_price_data(pair, interval="1day", outputsize=30)

        def get_trend(df: pd.DataFrame, label: str) -> str:
            """Determine trend direction using EMA-50 and price position."""
            if df is None or len(df) < 20:
                return "NEUTRAL"
            try:
                df = df.copy()
                df["ema_50"] = ta.trend.EMAIndicator(df["close"], window=min(50, len(df))).ema_indicator()
                df["ema_20"] = ta.trend.EMAIndicator(df["close"], window=min(20, len(df))).ema_indicator()
                adx_ind = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14)
                df["adx"] = adx_ind.adx()
                df["adx_pos"] = adx_ind.adx_pos()
                df["adx_neg"] = adx_ind.adx_neg()
                latest = df.iloc[-1]
                price_above_ema50 = latest["close"] > latest["ema_50"]
                ema_bullish = latest["ema_20"] > latest["ema_50"]
                di_bullish = latest["adx_pos"] > latest["adx_neg"]
                bullish_count = sum([price_above_ema50, ema_bullish, di_bullish])
                if bullish_count >= 2:
                    return "BULLISH"
                elif bullish_count <= 1:
                    return "BEARISH"
                return "NEUTRAL"
            except Exception as e:
                logger.warning(f"Trend calc error for {label}: {e}")
                return "NEUTRAL"

        h4_trend = get_trend(h4_df, f"{pair}/H4")
        d1_trend = get_trend(d1_df, f"{pair}/D1")

        logger.info(f"🕐 {pair} MTF check: H4={h4_trend}, D1={d1_trend}, signal={signal_direction}")

        # If both timeframes are NEUTRAL, allow the signal (insufficient data to block)
        if h4_trend == "NEUTRAL" and d1_trend == "NEUTRAL":
            return True, f"H4=NEUTRAL D1=NEUTRAL (insufficient data, allowing)"

        # Check alignment
        if signal_direction == "BUY":
            h4_ok = h4_trend in ("BULLISH", "NEUTRAL")
            d1_ok = d1_trend in ("BULLISH", "NEUTRAL")
            if h4_ok and d1_ok:
                return True, f"H4={h4_trend} D1={d1_trend} aligned BULLISH ✓"
            conflicts = []
            if not h4_ok:
                conflicts.append(f"H4={h4_trend}")
            if not d1_ok:
                conflicts.append(f"D1={d1_trend}")
            return False, f"BUY signal conflicts with higher TF: {', '.join(conflicts)}"

        elif signal_direction == "SELL":
            h4_ok = h4_trend in ("BEARISH", "NEUTRAL")
            d1_ok = d1_trend in ("BEARISH", "NEUTRAL")
            if h4_ok and d1_ok:
                return True, f"H4={h4_trend} D1={d1_trend} aligned BEARISH ✓"
            conflicts = []
            if not h4_ok:
                conflicts.append(f"H4={h4_trend}")
            if not d1_ok:
                conflicts.append(f"D1={d1_trend}")
            return False, f"SELL signal conflicts with higher TF: {', '.join(conflicts)}"

        # Unknown direction — allow
        return True, f"Direction={signal_direction} (unknown, allowing)"

    except Exception as e:
        logger.error(f"check_higher_timeframe_alignment error for {pair}: {e}")
        return True, f"MTF check error (allowing): {e}"


def detect_choppy_market(df: pd.DataFrame, pair: str) -> tuple[bool, str]:
    """
    Price action filter: detect choppy/ranging markets and block signals.
    
    Uses two complementary indicators:
    1. Choppiness Index (CI) — values near 100 = choppy, near 0 = trending
       CI = 100 * log10(sum(ATR(1), n) / (highest_high - lowest_low, n)) / log10(n)
       Threshold: CI > 61.8 (golden ratio) → choppy
    2. Bollinger Band Width squeeze — very narrow bands = low volatility / no direction
       Threshold: BB width < 0.5% of price → ranging/squeeze
    3. ADX < 20 combined with ATR below 14-period average → no clear trend
    
    Returns (is_choppy: bool, reason: str)
    """
    try:
        if df is None or len(df) < 20:
            return False, "Insufficient data for chop detection"

        df = df.copy()
        n = 14  # Lookback period

        # --- Choppiness Index ---
        high_n = df["high"].rolling(n).max()
        low_n = df["low"].rolling(n).min()
        atr_1 = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=1).average_true_range()
        atr_sum_n = atr_1.rolling(n).sum()
        hl_range = high_n - low_n
        # Avoid division by zero
        chop_index = np.where(
            hl_range > 0,
            100.0 * np.log10(atr_sum_n / hl_range) / np.log10(n),
            50.0
        )
        df["chop_index"] = chop_index
        latest_chop = float(df["chop_index"].iloc[-1])

        # --- Bollinger Band Width ---
        bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
        df["bb_upper"] = bb.bollinger_hband()
        df["bb_lower"] = bb.bollinger_lband()
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["close"] * 100  # as % of price
        latest_bb_width = float(df["bb_width"].iloc[-1])

        # --- ADX ---
        adx_ind = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14)
        df["adx"] = adx_ind.adx()
        latest_adx = float(df["adx"].iloc[-1])

        # --- ATR ratio (current vs 14-period average) ---
        df["atr"] = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
        atr_mean = float(df["atr"].tail(14).mean())
        latest_atr = float(df["atr"].iloc[-1])
        atr_ratio = latest_atr / atr_mean if atr_mean > 0 else 1.0

        logger.info(
            f"🔍 {pair} chop metrics: CI={latest_chop:.1f}, BB_width={latest_bb_width:.2f}%, "
            f"ADX={latest_adx:.1f}, ATR_ratio={atr_ratio:.2f}"
        )

        # Decision logic — flag as choppy if multiple indicators agree
        chop_signals = 0
        chop_reasons = []

        if latest_chop > 61.8:
            chop_signals += 1
            chop_reasons.append(f"CI={latest_chop:.1f}>61.8")

        if latest_bb_width < 0.5:
            chop_signals += 1
            chop_reasons.append(f"BB_width={latest_bb_width:.2f}%<0.5%")

        if latest_adx < 20 and atr_ratio < 0.85:
            chop_signals += 1
            chop_reasons.append(f"ADX={latest_adx:.1f}<20 + ATR_ratio={atr_ratio:.2f}<0.85")

        # Require at least 2 choppy signals to block (avoid false positives)
        if chop_signals >= 2:
            return True, f"Choppy market detected ({', '.join(chop_reasons)})"

        return False, f"Trending market confirmed (CI={latest_chop:.1f}, ADX={latest_adx:.1f}, BB_w={latest_bb_width:.2f}%)"

    except Exception as e:
        logger.error(f"detect_choppy_market error for {pair}: {e}")
        return False, f"Chop detection error (allowing): {e}"


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
        # Gold pairs require stricter 75% confidence threshold (premium filtering)
        effective_threshold = GOLD_CONFIDENCE_THRESHOLD if pair in GOLD_PAIRS else MIN_CONFIDENCE_THRESHOLD
        if ai_confidence < effective_threshold:
            logger.info(f"📊 {pair} skipped - confidence {ai_confidence}% < {effective_threshold}% threshold {'(gold premium)' if pair in GOLD_PAIRS else ''}")
            return None
        logger.info(f"✅ {pair} confidence check passed: {ai_confidence}% >= {effective_threshold}%")

        # ============ FILTER 3b: MULTI-TIMEFRAME CONFIRMATION (H4 + D1 alignment) ============
        try:
            signal_direction = ai_analysis.get("signal", "NEUTRAL")
            mtf_confirmed, mtf_reason = await check_higher_timeframe_alignment(pair, signal_direction)
            if not mtf_confirmed:
                logger.info(f"🕐 {pair} skipped - MTF confirmation failed: {mtf_reason}")
                return None
            logger.info(f"✅ {pair} MTF confirmation passed: {mtf_reason}")
        except Exception as mtf_err:
            logger.warning(f"⚠️ {pair} MTF check error (allowing signal): {mtf_err}")

        # ============ FILTER 3c: PRICE ACTION FILTER (choppy/ranging market detection) ============
        try:
            is_choppy, chop_reason = detect_choppy_market(df, pair)
            if is_choppy:
                logger.info(f"📉 {pair} skipped - choppy/ranging market: {chop_reason}")
                return None
            logger.info(f"✅ {pair} price action filter passed: {chop_reason}")
        except Exception as chop_err:
            logger.warning(f"⚠️ {pair} chop detection error (allowing signal): {chop_err}")

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
            is_premium=adjusted_confidence > 70  # ML-adjusted threshold (raised from 60 → 70)
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

# All 21 configured pairs with metadata for backtest UI
BACKTEST_PAIR_METADATA = {
    "XAUUSD": {"name": "Gold / US Dollar",              "type": "commodity"},
    "XAUEUR": {"name": "Gold / Euro",                   "type": "commodity"},
    "BTCUSD": {"name": "Bitcoin / US Dollar",           "type": "crypto"},
    "EURUSD": {"name": "Euro / US Dollar",              "type": "forex"},
    "GBPUSD": {"name": "British Pound / US Dollar",     "type": "forex"},
    "USDJPY": {"name": "US Dollar / Japanese Yen",      "type": "forex"},
    "EURJPY": {"name": "Euro / Japanese Yen",           "type": "forex"},
    "GBPJPY": {"name": "British Pound / Japanese Yen",  "type": "forex"},
    "AUDUSD": {"name": "Australian Dollar / US Dollar", "type": "forex"},
    "USDCAD": {"name": "US Dollar / Canadian Dollar",   "type": "forex"},
    "USDCHF": {"name": "US Dollar / Swiss Franc",       "type": "forex"},
    "NZDUSD": {"name": "New Zealand Dollar / US Dollar","type": "forex"},
    "AUDJPY": {"name": "Australian Dollar / Japanese Yen","type": "forex"},
    "CADJPY": {"name": "Canadian Dollar / Japanese Yen","type": "forex"},
    "CHFJPY": {"name": "Swiss Franc / Japanese Yen",    "type": "forex"},
    "EURAUD": {"name": "Euro / Australian Dollar",      "type": "forex"},
    "GBPCAD": {"name": "British Pound / Canadian Dollar","type": "forex"},
    "EURCAD": {"name": "Euro / Canadian Dollar",        "type": "forex"},
    "GBPAUD": {"name": "British Pound / Australian Dollar","type": "forex"},
    "AUDNZD": {"name": "Australian Dollar / New Zealand Dollar","type": "forex"},
    "EURGBP": {"name": "Euro / British Pound",          "type": "forex"},
    "EURCHF": {"name": "Euro / Swiss Franc",            "type": "forex"},
}


class BacktestRequest(BaseModel):
    """
    Request model for POST /api/backtest/run.

    - Set ``pair`` to a specific symbol (e.g. "EURUSD") OR leave it as "ALL"
      to backtest every active pair sequentially.
    - ``start_date`` / ``end_date`` default to the last 2 years when omitted.
    - When ``use_pair_parameters`` is True (default) the engine reads TP/SL
      values directly from PAIR_PARAMETERS so the backtest mirrors live
      signal generation exactly.
    """
    pair: str = "ALL"                        # symbol or "ALL"
    start_date: Optional[str] = None         # ISO date "YYYY-MM-DD"; default = 2 years ago
    end_date: Optional[str] = None           # ISO date "YYYY-MM-DD"; default = today
    timeframe: str = "1h"
    use_pair_parameters: bool = True         # Use PAIR_PARAMETERS for TP/SL (recommended)
    # Manual overrides – only used when use_pair_parameters=False
    tp1_pips: Optional[float] = None
    tp2_pips: Optional[float] = None
    tp3_pips: Optional[float] = None
    sl_pips: Optional[float] = None
    use_atr_for_sl: bool = True
    atr_sl_multiplier: float = 1.5
    initial_balance: float = 10000.0
    risk_per_trade: float = 0.02             # 2 % risk per trade
    skip_disabled: bool = True               # Skip pairs where enabled=False (e.g. BTCUSD)
    run_in_background: bool = False          # True → return job_id immediately


class BacktestResponse(BaseModel):
    """Summary response returned after a completed backtest."""
    pair: str
    enabled: bool
    skipped: bool = False
    skip_reason: Optional[str] = None
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pips: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_percent: float = 0.0
    return_percent: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    # Applied filter settings
    tp1_pips_used: float = 0.0
    tp2_pips_used: float = 0.0
    tp3_pips_used: float = 0.0
    sl_pips_used: float = 0.0
    atr_sl_multiplier_used: float = 0.0
    pip_value_used: float = 0.0
    # Periodic breakdowns
    monthly_performance: Dict[str, Any] = {}
    yearly_performance: Dict[str, Any] = {}
    result_id: Optional[str] = None


def _build_backtest_config_for_pair(
    pair: str,
    request: BacktestRequest,
    start_dt: datetime,
    end_dt: datetime,
) -> BacktestConfig:
    """
    Build a BacktestConfig for *pair* by merging PAIR_PARAMETERS with the
    request overrides.  When ``use_pair_parameters`` is True the TP/SL values
    come from the live PAIR_PARAMETERS dict so the backtest is consistent with
    actual signal generation.
    """
    params = PAIR_PARAMETERS.get(pair, DEFAULT_PAIR_PARAMS)

    if request.use_pair_parameters:
        # --- TP levels ---
        tp1 = float(params.get("fixed_tp1_pips", params.get("atr_multiplier_tp1", 5.0)))
        tp2 = float(params.get("fixed_tp2_pips", params.get("atr_multiplier_tp2", 10.0)))
        tp3 = float(params.get("fixed_tp3_pips", params.get("atr_multiplier_tp3", 15.0)))
        # --- SL ---
        sl  = float(params.get("fixed_sl_pips",  params.get("atr_multiplier_sl",  15.0)))
        use_atr = not params.get("use_fixed_pips", False)
        atr_mult = float(params.get("atr_multiplier_sl", request.atr_sl_multiplier))
    else:
        # Fall back to manual overrides or sensible defaults
        tp1 = request.tp1_pips or 5.0
        tp2 = request.tp2_pips or 10.0
        tp3 = request.tp3_pips or 15.0
        sl  = request.sl_pips  or 15.0
        use_atr  = request.use_atr_for_sl
        atr_mult = request.atr_sl_multiplier

    return BacktestConfig(
        pair=pair,
        start_date=start_dt,
        end_date=end_dt,
        timeframe=request.timeframe,
        initial_balance=request.initial_balance,
        risk_per_trade=request.risk_per_trade,
        tp1_pips=tp1,
        tp2_pips=tp2,
        tp3_pips=tp3,
        sl_pips=sl,
        use_atr_for_sl=use_atr,
        atr_sl_multiplier=atr_mult,
    )


def _result_to_response(
    pair: str,
    results,          # BacktestResults
    config: BacktestConfig,
    result_id: Optional[str] = None,
) -> BacktestResponse:
    """Convert a BacktestResults dataclass into a BacktestResponse Pydantic model."""
    params = PAIR_PARAMETERS.get(pair, DEFAULT_PAIR_PARAMS)
    pip_value = float(params.get("pip_value", 0.0001))

    return BacktestResponse(
        pair=pair,
        enabled=params.get("enabled", True),
        total_trades=results.total_trades,
        winning_trades=results.winning_trades,
        losing_trades=results.losing_trades,
        win_rate=round(results.win_rate, 2),
        total_pips=round(results.total_pips, 1),
        profit_factor=round(results.profit_factor, 2),
        sharpe_ratio=round(results.sharpe_ratio, 2),
        max_drawdown_percent=round(results.max_drawdown_percent, 2),
        return_percent=round(results.return_percent, 2),
        max_consecutive_wins=results.max_consecutive_wins,
        max_consecutive_losses=results.max_consecutive_losses,
        tp1_pips_used=config.tp1_pips,
        tp2_pips_used=config.tp2_pips,
        tp3_pips_used=config.tp3_pips,
        sl_pips_used=config.sl_pips,
        atr_sl_multiplier_used=config.atr_sl_multiplier,
        pip_value_used=pip_value,
        monthly_performance=results.monthly_performance,
        yearly_performance=results.yearly_performance,
        result_id=result_id,
    )


async def _run_single_pair_backtest(
    pair: str,
    request: BacktestRequest,
    start_dt: datetime,
    end_dt: datetime,
    engine,
    user_id: str,
) -> BacktestResponse:
    """
    Execute a backtest for one pair, persist the result to MongoDB, and
    return a BacktestResponse.  Applies all active filters:
      - PAIR_PARAMETERS (TP/SL, pip value, ATR multiplier)
      - ALLOWED_REGIMES / SKIP_REGIME awareness (noted in metadata)
      - MIN_CONFIDENCE_THRESHOLD (noted in metadata)
      - DRAWDOWN_PROTECTION settings (noted in metadata)
      - skip_disabled flag (skips pairs with enabled=False)
    """
    params = PAIR_PARAMETERS.get(pair, DEFAULT_PAIR_PARAMS)
    is_enabled = params.get("enabled", True)

    # --- Filter: skip disabled pairs ---
    if request.skip_disabled and not is_enabled:
        logger.info(f"[Backtest] Skipping disabled pair: {pair}")
        return BacktestResponse(
            pair=pair,
            enabled=False,
            skipped=True,
            skip_reason=f"{pair} is disabled in PAIR_PARAMETERS (poor historical performance)",
        )

    config = _build_backtest_config_for_pair(pair, request, start_dt, end_dt)

    logger.info(
        f"[Backtest] Running {pair} | {start_dt.date()} → {end_dt.date()} | "
        f"TF={config.timeframe} | TP={config.tp1_pips}/{config.tp2_pips}/{config.tp3_pips} "
        f"SL={config.sl_pips} ATR×{config.atr_sl_multiplier}"
    )

    results = await engine.run_backtest(config)

    # --- Persist to MongoDB ---
    pip_value = float(params.get("pip_value", 0.0001))
    result_doc = {
        "user_id": user_id,
        "pair": pair,
        "enabled": is_enabled,
        "timeframe": config.timeframe,
        "start_date": start_dt.isoformat(),
        "end_date": end_dt.isoformat(),
        "filters_applied": {
            "use_pair_parameters": request.use_pair_parameters,
            "skip_disabled": request.skip_disabled,
            "allowed_regimes": ALLOWED_REGIMES,
            "min_confidence_threshold": MIN_CONFIDENCE_THRESHOLD,
            "drawdown_protection": DRAWDOWN_PROTECTION,
            "partial_close": {"tp1": 0.33, "tp2": 0.33, "tp3": 0.34},
        },
        "config": {
            "tp1_pips": config.tp1_pips,
            "tp2_pips": config.tp2_pips,
            "tp3_pips": config.tp3_pips,
            "sl_pips": config.sl_pips,
            "use_atr_for_sl": config.use_atr_for_sl,
            "atr_sl_multiplier": config.atr_sl_multiplier,
            "pip_value": pip_value,
            "initial_balance": config.initial_balance,
            "risk_per_trade": config.risk_per_trade,
        },
        "results": results.to_dict(),
        "created_at": datetime.utcnow(),
    }
    insert_result = await db.backtest_results.insert_one(result_doc)
    result_id = str(insert_result.inserted_id)

    response = _result_to_response(pair, results, config, result_id)
    logger.info(
        f"[Backtest] {pair} done → {results.total_trades} trades | "
        f"WR={results.win_rate:.1f}% | PF={results.profit_factor:.2f} | "
        f"Pips={results.total_pips:.1f} | DD={results.max_drawdown_percent:.1f}%"
    )
    return response


async def _run_all_pairs_backtest_bg(
    request: BacktestRequest,
    start_dt: datetime,
    end_dt: datetime,
    engine,
    user_id: str,
    job_id: str,
):
    """Background task: run backtest for every active pair and store a
    consolidated summary document in ``backtest_jobs``."""
    pairs_to_run = list(PAIR_PARAMETERS.keys())
    responses = []

    await db.backtest_jobs.update_one(
        {"_id": ObjectId(job_id)},
        {"$set": {"status": "running", "total_pairs": len(pairs_to_run)}},
    )

    for idx, pair in enumerate(pairs_to_run):
        try:
            resp = await _run_single_pair_backtest(
                pair, request, start_dt, end_dt, engine, user_id
            )
            responses.append(resp.dict())
            await db.backtest_jobs.update_one(
                {"_id": ObjectId(job_id)},
                {"$set": {"pairs_completed": idx + 1, f"pair_results.{pair}": resp.dict()}},
            )
        except Exception as exc:
            logger.error(f"[Backtest BG] Error on {pair}: {exc}")
            responses.append({"pair": pair, "skipped": True, "skip_reason": str(exc)})
        # Small delay to avoid hammering the Twelve Data API
        await asyncio.sleep(2)

    # Build aggregate summary
    completed = [r for r in responses if not r.get("skipped")]
    summary = {
        "total_pairs_run": len(completed),
        "total_pairs_skipped": len(responses) - len(completed),
        "avg_win_rate": round(
            sum(r["win_rate"] for r in completed) / len(completed), 2
        ) if completed else 0,
        "avg_profit_factor": round(
            sum(r["profit_factor"] for r in completed) / len(completed), 2
        ) if completed else 0,
        "total_pips_all_pairs": round(sum(r["total_pips"] for r in completed), 1),
        "best_pair": max(completed, key=lambda r: r["profit_factor"])["pair"] if completed else None,
        "worst_pair": min(completed, key=lambda r: r["profit_factor"])["pair"] if completed else None,
    }

    await db.backtest_jobs.update_one(
        {"_id": ObjectId(job_id)},
        {"$set": {"status": "completed", "summary": summary, "completed_at": datetime.utcnow()}},
    )
    logger.info(f"[Backtest BG] Job {job_id} complete. {summary}")


# ---------------------------------------------------------------------------
# POST /api/backtest/run
# ---------------------------------------------------------------------------
@api_router.post("/backtest/run")
async def run_backtest(
    request: BacktestRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    """
    Trigger a backtest for a single pair or ALL active pairs.

    **Single pair** (synchronous):
    ```json
    { "pair": "EURUSD" }
    ```

    **All pairs – foreground** (waits for completion, may be slow):
    ```json
    { "pair": "ALL" }
    ```

    **All pairs – background** (returns a job_id immediately):
    ```json
    { "pair": "ALL", "run_in_background": true }
    ```

    Filters applied automatically:
    - PAIR_PARAMETERS (TP/SL, pip value, ATR multiplier)
    - skip_disabled=true skips pairs with enabled=False (e.g. BTCUSD)
    - Partial close logic: 33 % at TP1, 33 % at TP2, 34 % at TP3
    - ALLOWED_REGIMES, MIN_CONFIDENCE_THRESHOLD and DRAWDOWN_PROTECTION
      settings are recorded in the stored result for reference.

    Default date range is the **last 2 years** when start_date/end_date are
    omitted.
    """
    try:
        engine = get_backtest_engine()
        if not engine:
            raise HTTPException(status_code=500, detail="Backtest engine not initialized")

        # --- Resolve date range ---
        now = datetime.utcnow()
        if request.end_date:
            end_dt = datetime.fromisoformat(request.end_date)
        else:
            end_dt = now

        if request.start_date:
            start_dt = datetime.fromisoformat(request.start_date)
        else:
            start_dt = now - timedelta(days=730)  # default: 2 years

        if start_dt >= end_dt:
            raise HTTPException(status_code=400, detail="start_date must be before end_date")

        date_span_days = (end_dt - start_dt).days
        if date_span_days < 30:
            raise HTTPException(status_code=400, detail="Date range must be at least 30 days")
        if date_span_days > 3650:
            raise HTTPException(status_code=400, detail="Date range cannot exceed 10 years")

        user_id = str(current_user["_id"])

        # ------------------------------------------------------------------ #
        # ALL PAIRS
        # ------------------------------------------------------------------ #
        if request.pair.upper() == "ALL":
            if request.run_in_background:
                # Create a job document and run in background
                job_doc = {
                    "user_id": user_id,
                    "type": "all_pairs_backtest",
                    "status": "queued",
                    "start_date": start_dt.isoformat(),
                    "end_date": end_dt.isoformat(),
                    "timeframe": request.timeframe,
                    "pairs_completed": 0,
                    "total_pairs": len(PAIR_PARAMETERS),
                    "pair_results": {},
                    "created_at": datetime.utcnow(),
                }
                job_insert = await db.backtest_jobs.insert_one(job_doc)
                job_id = str(job_insert.inserted_id)

                background_tasks.add_task(
                    _run_all_pairs_backtest_bg,
                    request, start_dt, end_dt, engine, user_id, job_id,
                )
                return {
                    "success": True,
                    "mode": "background",
                    "job_id": job_id,
                    "message": (
                        f"Backtest job queued for {len(PAIR_PARAMETERS)} pairs "
                        f"({start_dt.date()} → {end_dt.date()}). "
                        f"Poll GET /api/backtest/job/{job_id} for progress."
                    ),
                    "pairs_queued": list(PAIR_PARAMETERS.keys()),
                }
            else:
                # Foreground – run all pairs sequentially
                pairs_to_run = list(PAIR_PARAMETERS.keys())
                all_responses = []
                errors = []

                for pair in pairs_to_run:
                    try:
                        resp = await _run_single_pair_backtest(
                            pair, request, start_dt, end_dt, engine, user_id
                        )
                        all_responses.append(resp.dict())
                    except Exception as exc:
                        logger.error(f"[Backtest] Error on {pair}: {exc}")
                        errors.append({"pair": pair, "error": str(exc)})
                    await asyncio.sleep(1)

                completed = [r for r in all_responses if not r.get("skipped")]
                summary = {
                    "total_pairs_run": len(completed),
                    "total_pairs_skipped": len(all_responses) - len(completed),
                    "avg_win_rate": round(
                        sum(r["win_rate"] for r in completed) / len(completed), 2
                    ) if completed else 0,
                    "avg_profit_factor": round(
                        sum(r["profit_factor"] for r in completed) / len(completed), 2
                    ) if completed else 0,
                    "total_pips_all_pairs": round(
                        sum(r["total_pips"] for r in completed), 1
                    ),
                    "best_pair": max(
                        completed, key=lambda r: r["profit_factor"]
                    )["pair"] if completed else None,
                    "worst_pair": min(
                        completed, key=lambda r: r["profit_factor"]
                    )["pair"] if completed else None,
                    "date_range": f"{start_dt.date()} → {end_dt.date()}",
                    "timeframe": request.timeframe,
                }

                return {
                    "success": True,
                    "mode": "foreground",
                    "summary": summary,
                    "results": all_responses,
                    "errors": errors,
                }

        # ------------------------------------------------------------------ #
        # SINGLE PAIR
        # ------------------------------------------------------------------ #
        pair = request.pair.upper()
        if pair not in PAIR_PARAMETERS:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown pair '{pair}'. Valid pairs: {list(PAIR_PARAMETERS.keys())}",
            )

        resp = await _run_single_pair_backtest(
            pair, request, start_dt, end_dt, engine, user_id
        )

        return {
            "success": True,
            "mode": "single",
            "pair": pair,
            "date_range": f"{start_dt.date()} → {end_dt.date()}",
            "result": resp.dict(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Backtest] Unexpected error: {e}")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# GET /api/backtest/results/{pair}
# ---------------------------------------------------------------------------
@api_router.get("/backtest/results/{pair}")
async def get_backtest_results_for_pair(
    pair: str,
    limit: int = 5,
    current_user: dict = Depends(get_current_user),
):
    """
    Retrieve the most recent backtest results stored in MongoDB for a
    specific pair.  Returns up to ``limit`` runs (default 5), newest first.
    """
    try:
        pair = pair.upper()
        if pair not in PAIR_PARAMETERS and pair != "ALL":
            raise HTTPException(
                status_code=400,
                detail=f"Unknown pair '{pair}'. Valid pairs: {list(PAIR_PARAMETERS.keys())}",
            )

        query: dict = {"user_id": str(current_user["_id"])}
        if pair != "ALL":
            query["pair"] = pair

        cursor = db.backtest_results.find(query).sort("created_at", -1).limit(limit)
        docs = await cursor.to_list(length=limit)

        formatted = []
        for doc in docs:
            formatted.append({
                "id": str(doc["_id"]),
                "pair": doc.get("pair"),
                "enabled": doc.get("enabled", True),
                "timeframe": doc.get("timeframe"),
                "start_date": doc.get("start_date"),
                "end_date": doc.get("end_date"),
                "filters_applied": doc.get("filters_applied", {}),
                "config": doc.get("config", {}),
                "summary": doc.get("results", {}).get("summary", {}),
                "monthly_performance": doc.get("results", {}).get("monthly_performance", {}),
                "yearly_performance": doc.get("results", {}).get("yearly_performance", {}),
                "created_at": doc["created_at"].isoformat() if doc.get("created_at") else None,
            })

        return {
            "success": True,
            "pair": pair,
            "count": len(formatted),
            "results": formatted,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Backtest] Error fetching results for {pair}: {e}")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# GET /api/backtest/summary
# ---------------------------------------------------------------------------
@api_router.get("/backtest/summary")
async def get_backtest_summary(current_user: dict = Depends(get_current_user)):
    """
    Return a cross-pair summary of the most recent backtest run for every
    pair stored in MongoDB.  Useful for comparing strategy consistency across
    all 21 configured pairs at a glance.

    Each pair entry includes:
    - Latest win rate, profit factor, Sharpe ratio, max drawdown
    - Total pips, return %, consecutive win/loss streaks
    - Monthly and yearly performance breakdowns
    - Applied filter settings (TP/SL, pip value, ATR multiplier)
    - Whether the pair is currently enabled in PAIR_PARAMETERS
    """
    try:
        user_id = str(current_user["_id"])
        summary_rows = []

        for pair in PAIR_PARAMETERS.keys():
            params = PAIR_PARAMETERS[pair]
            is_enabled = params.get("enabled", True)

            # Fetch the single most recent result for this pair
            doc = await db.backtest_results.find_one(
                {"user_id": user_id, "pair": pair},
                sort=[("created_at", -1)],
            )

            if doc is None:
                summary_rows.append({
                    "pair": pair,
                    "enabled": is_enabled,
                    "has_results": False,
                    "pair_type": BACKTEST_PAIR_METADATA.get(pair, {}).get("type", "forex"),
                })
                continue

            s = doc.get("results", {}).get("summary", {})
            cfg = doc.get("config", {})
            summary_rows.append({
                "pair": pair,
                "enabled": is_enabled,
                "has_results": True,
                "pair_type": BACKTEST_PAIR_METADATA.get(pair, {}).get("type", "forex"),
                "result_id": str(doc["_id"]),
                "backtest_date": doc["created_at"].isoformat() if doc.get("created_at") else None,
                "date_range": f"{doc.get('start_date', '')[:10]} → {doc.get('end_date', '')[:10]}",
                "timeframe": doc.get("timeframe", "1h"),
                # Core metrics
                "total_trades": s.get("total_trades", 0),
                "win_rate": s.get("win_rate", 0),
                "profit_factor": s.get("profit_factor", 0),
                "sharpe_ratio": s.get("sharpe_ratio", 0),
                "max_drawdown_percent": s.get("max_drawdown_percent", 0),
                "total_pips": s.get("total_pips", 0),
                "return_percent": s.get("return_percent", 0),
                "max_consecutive_wins": s.get("max_consecutive_wins", 0),
                "max_consecutive_losses": s.get("max_consecutive_losses", 0),
                # Applied settings
                "tp1_pips": cfg.get("tp1_pips"),
                "tp2_pips": cfg.get("tp2_pips"),
                "tp3_pips": cfg.get("tp3_pips"),
                "sl_pips": cfg.get("sl_pips"),
                "pip_value": cfg.get("pip_value"),
                "atr_sl_multiplier": cfg.get("atr_sl_multiplier"),
                # Periodic performance
                "monthly_performance": doc.get("results", {}).get("monthly_performance", {}),
                "yearly_performance": doc.get("results", {}).get("yearly_performance", {}),
            })

        # Sort: enabled pairs first, then by profit factor descending
        summary_rows.sort(
            key=lambda r: (
                0 if r.get("enabled") else 1,
                -(r.get("profit_factor") or 0),
            )
        )

        # Aggregate stats across pairs that have results
        with_results = [r for r in summary_rows if r.get("has_results")]
        aggregate = {}
        if with_results:
            aggregate = {
                "pairs_with_results": len(with_results),
                "pairs_without_results": len(summary_rows) - len(with_results),
                "avg_win_rate": round(
                    sum(r["win_rate"] for r in with_results) / len(with_results), 2
                ),
                "avg_profit_factor": round(
                    sum(r["profit_factor"] for r in with_results) / len(with_results), 2
                ),
                "avg_sharpe_ratio": round(
                    sum(r["sharpe_ratio"] for r in with_results) / len(with_results), 2
                ),
                "total_pips_all_pairs": round(
                    sum(r["total_pips"] for r in with_results), 1
                ),
                "best_pair_by_pf": max(
                    with_results, key=lambda r: r["profit_factor"]
                )["pair"],
                "worst_pair_by_pf": min(
                    with_results, key=lambda r: r["profit_factor"]
                )["pair"],
                "best_pair_by_wr": max(
                    with_results, key=lambda r: r["win_rate"]
                )["pair"],
            }

        return {
            "success": True,
            "total_pairs_configured": len(PAIR_PARAMETERS),
            "active_filters": {
                "allowed_regimes": ALLOWED_REGIMES,
                "min_confidence_threshold": MIN_CONFIDENCE_THRESHOLD,
                "drawdown_protection": DRAWDOWN_PROTECTION,
                "partial_close": {"tp1_pct": 33, "tp2_pct": 33, "tp3_pct": 34},
            },
            "aggregate": aggregate,
            "pairs": summary_rows,
        }
    except Exception as e:
        logger.error(f"[Backtest] Error building summary: {e}")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# GET /api/backtest/job/{job_id}  – poll background job progress
# ---------------------------------------------------------------------------
@api_router.get("/backtest/job/{job_id}")
async def get_backtest_job_status(
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Poll the status of a background all-pairs backtest job.

    Returns ``status`` = "queued" | "running" | "completed", plus
    ``pairs_completed`` / ``total_pairs`` for progress tracking and a
    ``summary`` once the job finishes.
    """
    try:
        if not ObjectId.is_valid(job_id):
            raise HTTPException(status_code=400, detail="Invalid job_id")

        job = await db.backtest_jobs.find_one({
            "_id": ObjectId(job_id),
            "user_id": str(current_user["_id"]),
        })
        if not job:
            raise HTTPException(status_code=404, detail="Backtest job not found")

        return {
            "success": True,
            "job_id": job_id,
            "status": job.get("status", "unknown"),
            "pairs_completed": job.get("pairs_completed", 0),
            "total_pairs": job.get("total_pairs", 0),
            "progress_pct": round(
                job.get("pairs_completed", 0) / max(job.get("total_pairs", 1), 1) * 100, 1
            ),
            "summary": job.get("summary"),
            "created_at": job["created_at"].isoformat() if job.get("created_at") else None,
            "completed_at": job["completed_at"].isoformat() if job.get("completed_at") else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Backtest] Error fetching job {job_id}: {e}")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# GET /api/backtest/history  – user's full backtest run history
# ---------------------------------------------------------------------------
@api_router.get("/backtest/history")
async def get_backtest_history(
    limit: int = 20,
    pair: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """
    Return the user's backtest run history, newest first.
    Optionally filter by ``pair`` query parameter.
    """
    try:
        query: dict = {"user_id": str(current_user["_id"])}
        if pair:
            query["pair"] = pair.upper()

        history = await db.backtest_results.find(query).sort(
            "created_at", -1
        ).limit(limit).to_list(limit)

        formatted = []
        for item in history:
            formatted.append({
                "id": str(item["_id"]),
                "pair": item.get("pair"),
                "enabled": item.get("enabled", True),
                "timeframe": item.get("timeframe"),
                "start_date": item.get("start_date"),
                "end_date": item.get("end_date"),
                "config": item.get("config", {}),
                "summary": item.get("results", {}).get("summary", {}),
                "filters_applied": item.get("filters_applied", {}),
                "created_at": item["created_at"].isoformat() if item.get("created_at") else None,
            })

        return {
            "success": True,
            "count": len(formatted),
            "history": formatted,
        }
    except Exception as e:
        logger.error(f"[Backtest] Error getting history: {e}")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# GET /api/backtest/result/{result_id}  – full detail for one run
# ---------------------------------------------------------------------------
@api_router.get("/backtest/result/{result_id}")
async def get_backtest_result(
    result_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Retrieve the full detail of a single backtest run by its MongoDB ID,
    including the last 50 individual trades and all periodic breakdowns.
    """
    try:
        if not ObjectId.is_valid(result_id):
            raise HTTPException(status_code=400, detail="Invalid result_id")

        result = await db.backtest_results.find_one({
            "_id": ObjectId(result_id),
            "user_id": str(current_user["_id"]),
        })

        if not result:
            raise HTTPException(status_code=404, detail="Backtest result not found")

        return {
            "success": True,
            "result": {
                "id": str(result["_id"]),
                "pair": result.get("pair"),
                "enabled": result.get("enabled", True),
                "timeframe": result.get("timeframe"),
                "start_date": result.get("start_date"),
                "end_date": result.get("end_date"),
                "filters_applied": result.get("filters_applied", {}),
                "config": result.get("config", {}),
                "results": result.get("results", {}),
                "created_at": result["created_at"].isoformat() if result.get("created_at") else None,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Backtest] Error fetching result {result_id}: {e}")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# GET /api/backtest/pairs  – metadata for all configurable pairs
# ---------------------------------------------------------------------------
@api_router.get("/backtest/pairs")
async def get_available_pairs(current_user: dict = Depends(get_current_user)):
    """
    Return metadata for all 21 configured trading pairs, including whether
    each is currently enabled, its live PAIR_PARAMETERS settings, and the
    pip value used for accurate pip calculations.
    """
    pairs_out = []
    for symbol, meta in BACKTEST_PAIR_METADATA.items():
        params = PAIR_PARAMETERS.get(symbol, DEFAULT_PAIR_PARAMS)
        pairs_out.append({
            "symbol": symbol,
            "name": meta["name"],
            "type": meta["type"],
            "enabled": params.get("enabled", True),
            "pip_value": params.get("pip_value", 0.0001),
            "decimal_places": params.get("decimal_places", 5),
            "tp1_pips": params.get("fixed_tp1_pips", params.get("atr_multiplier_tp1", 5.0)),
            "tp2_pips": params.get("fixed_tp2_pips", params.get("atr_multiplier_tp2", 10.0)),
            "tp3_pips": params.get("fixed_tp3_pips", params.get("atr_multiplier_tp3", 15.0)),
            "sl_pips": params.get("fixed_sl_pips", params.get("atr_multiplier_sl", 15.0)),
            "use_fixed_pips": params.get("use_fixed_pips", False),
            "atr_sl_multiplier": params.get("atr_multiplier_sl", 1.5),
            "min_rr": params.get("min_rr", 1.5),
            "typical_spread": params.get("typical_spread", 0.0001),
        })

    # Sort: enabled first, then alphabetically
    pairs_out.sort(key=lambda p: (0 if p["enabled"] else 1, p["symbol"]))

    return {
        "success": True,
        "total_pairs": len(pairs_out),
        "active_pairs": sum(1 for p in pairs_out if p["enabled"]),
        "disabled_pairs": sum(1 for p in pairs_out if not p["enabled"]),
        "pairs": pairs_out,
        "timeframes": [
            {"value": "1h",   "label": "1 Hour (recommended)"},
            {"value": "4h",   "label": "4 Hours"},
            {"value": "1day", "label": "Daily"},
        ],
        "default_date_range": "Last 2 years",
        "filters_active": {
            "allowed_regimes": ALLOWED_REGIMES,
            "min_confidence": MIN_CONFIDENCE_THRESHOLD,
            "drawdown_protection": DRAWDOWN_PROTECTION["enabled"],
            "partial_close_split": "33% TP1 / 33% TP2 / 34% TP3",
        },
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

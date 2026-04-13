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
import ta
import pandas as pd
import numpy as np
from pathlib import Path
import time  # ← needed by gatekeeper latency check

# Import Emergent LLM integration
from emergentintegrations.llm import LlmChat, UserMessage

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

# ============================================================
# EXECUTION GATEKEEPER  (Safe Execution Mode v2)
# Your exact class — symbol-aware pip multipliers, per-asset
# slippage/spread/EMA thresholds, duplicate-trade detection.
# ============================================================
import json as _json
from datetime import timezone as _tz

_gk_logger = logging.getLogger("execution_gatekeeper")
_GK_LOG_FILE: str = os.getenv("GK_LOG_FILE", "gatekeeper_trades.jsonl")


def _gk_log(symbol: str, side: str, result: dict) -> None:
    """Write every gatekeeper decision to the log file and Python logger."""
    approved = result.get("status") == "EXECUTE"
    entry = {
        "ts":       datetime.utcnow().isoformat(),
        "symbol":   symbol,
        "side":     side,
        "status":   result.get("status"),
        "reason":   result.get("reason", ""),
        "rr":       result.get("rr"),
        "symbol_type": result.get("symbol_type"),
    }
    _gk_logger.info(
        "%s %s %s — %s",
        result.get("status"), symbol, side,
        result.get("reason", f"R:R={result.get('rr')}")
    )
    if _GK_LOG_FILE:
        try:
            with open(_GK_LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(_json.dumps(entry) + "\n")
        except OSError:
            pass


class ExecutionGatekeeper:
    """
    Production-grade, symbol-aware trade validator.

    Checks (in order):
        1.  Signal age          — per-asset tolerance (Gold 10s / JPY 6s / FX 4s)
        2.  Future timestamp    — rejects clock-skewed / bad signals
        3.  Session filter      — London 07-16 UTC + New York 12-21 UTC
        4.  News filter         — placeholder (wire up ForexFactory API)
        5.  Confidence          — min 65% (GK_MIN_CONFIDENCE)
        6.  Max open trades     — hard cap (GK_MAX_OPEN_TRADES)
        7.  Duplicate trade     — same symbol + same side already open
        8.  Price sanity        — entry / sl / tp must all be > 0
        9.  Direction structure — BUY: tp>entry>sl  |  SELL: tp<entry<sl
        10. Risk / Reward       — Gold: min 1.8 via validate_gold_trade()
                                  FX/JPY: min 1.5 (GK_MIN_RR)
        11. Slippage (price)    — abs(entry - current_price) per-asset threshold
        12. Slippage (pips)     — secondary pip-based check
        13. Spread              — per-asset pip limit
        14. EMA-50 proximity    — per-asset pip distance

    Env-var overrides (all hot-reloadable via Railway):
        GK_MIN_RR                (default 1.5)
        GK_MAX_SIGNAL_AGE        (default 4s  — Forex base)
        GK_MAX_SIGNAL_AGE_GOLD   (default 10s)
        GK_MAX_SIGNAL_AGE_JPY    (default 6s)
        GK_MAX_OPEN_TRADES       (default 3)
        GK_MIN_CONFIDENCE        (default 65)
        GK_PRICE_THRESHOLD_GOLD  (default 0.50)
        GK_PRICE_THRESHOLD_JPY   (default 0.03)
        GK_PRICE_THRESHOLD_FX    (default 0.0002)
        GK_LOG_FILE              (default gatekeeper_trades.jsonl)
    """

    # ── Required signal keys — used for fast key-presence validation ──
    REQUIRED_KEYS = ("symbol", "side", "entry", "sl", "tp",
                     "current_price", "spread", "timestamp")

    def __init__(
        self,
        min_rr:             float = float(os.getenv("GK_MIN_RR",           "1.5")),
        max_signal_age_sec: float = float(os.getenv("GK_MAX_SIGNAL_AGE",   "4")),
        max_open_trades:    int   = int(  os.getenv("GK_MAX_OPEN_TRADES",  "2")),  # Max 2 concurrent trades
        min_confidence:     float = float(os.getenv("GK_MIN_CONFIDENCE",   "70")),
    ):
        self.min_rr          = min_rr
        self.max_signal_age  = max_signal_age_sec
        self.max_open_trades = max_open_trades
        self.min_confidence  = min_confidence

    # ================================================================
    # SYMBOL HANDLING
    # ================================================================

    def get_symbol_type(self, symbol: str) -> str:
        """Classify symbol into GOLD | JPY | FOREX."""
        s = symbol.upper()
        if "XAU" in s:
            return "GOLD"
        elif "JPY" in s:
            return "JPY"
        return "FOREX"

    def get_pip_multiplier(self, symbol: str) -> float:
        """
        Pip multiplier:
          FOREX : 10,000  (e.g. EURUSD  0.0001 = 1 pip)
          JPY   :    100  (e.g. USDJPY  0.01   = 1 pip)
          GOLD  :    100  (e.g. XAUUSD  0.01   = 1 pip — $0.01 move)
        """
        t = self.get_symbol_type(symbol)
        return 100.0 if t in ("JPY", "GOLD") else 10_000.0

    def get_thresholds(self, symbol: str) -> dict:
        """
        Per-asset thresholds.
        slippage / spread / ema_distance : in pips
        price_threshold                  : in raw price units
          Gold  0.50  (~5 pips on XAUUSD)
          JPY   0.03  (~3 pips on USDJPY)
          FX    0.0002 (~2 pips on EURUSD)
        """
        t = self.get_symbol_type(symbol)
        if t == "GOLD":
            return {
                "slippage":        10,
                "spread":          30,
                "ema_distance":    50,
                "price_threshold": float(os.getenv("GK_PRICE_THRESHOLD_GOLD", "0.50")),
            }
        elif t == "JPY":
            return {
                "slippage":        3,
                "spread":          3,
                "ema_distance":    15,
                "price_threshold": float(os.getenv("GK_PRICE_THRESHOLD_JPY",  "0.03")),
            }
        else:
            return {
                "slippage":        2,
                "spread":          2,
                "ema_distance":    10,
                "price_threshold": float(os.getenv("GK_PRICE_THRESHOLD_FX",   "0.0002")),
            }

    # ================================================================
    # CORE CALCULATIONS
    # ================================================================

    def price_to_pips(self, price_diff: float, symbol: str) -> float:
        """Convert a raw price difference to pips (always positive)."""
        return abs(price_diff) * self.get_pip_multiplier(symbol)

    def calculate_rr(
        self, entry: float, sl: float, tp: float, symbol: str
    ) -> float:
        """R:R using pip-based distances. Returns 0.0 if risk is zero."""
        risk   = self.price_to_pips(entry - sl, symbol)
        reward = self.price_to_pips(tp - entry, symbol)
        return round(reward / risk, 4) if risk > 0 else 0.0

    def is_valid_entry(self, entry: float, ema50: float, symbol: str) -> bool:
        """Return True if entry is within EMA-50 proximity threshold."""
        thresholds = self.get_thresholds(symbol)
        distance   = self.price_to_pips(entry - ema50, symbol)
        return distance <= thresholds["ema_distance"]

    def is_duplicate_trade(self, signal: dict, open_trades: list) -> bool:
        """Return True if the same symbol+side is already open.
        Guards against non-dict entries in open_trades list."""
        sym  = signal.get("symbol", "")
        side = signal.get("side", "")
        for trade in open_trades:
            if not isinstance(trade, dict):
                continue   # ← BUG FIX: skip malformed entries safely
            if trade.get("symbol") == sym and trade.get("side") == side:
                return True
        return False

    # ================================================================
    # ADVANCED FILTERS
    # ================================================================

    def is_valid_session(self, current_time: datetime, symbol: str = "") -> bool:
        """
        Gold (XAU): trades 24/7 — always allowed.
        Forex/JPY:  London 07-16 UTC or New York 12-21 UTC only.
        """
        if "XAU" in symbol.upper():
            return True   # Gold trades around the clock
        hour    = current_time.hour
        london  = 7  <= hour < 16
        newyork = 12 <= hour < 21
        return london or newyork

    def is_high_impact_news_near(self, current_time: datetime) -> bool:
        """
        Placeholder — wire up a ForexFactory / Investing.com API here.
        Return True when high-impact news is within ±15 min of current_time.
        """
        return False

    def is_confident_signal(self, confidence: float) -> bool:
        return confidence >= self.min_confidence

    # ================================================================
    # GOLD-SPECIFIC VALIDATION  (XAUUSD / XAUEUR)
    # ================================================================

    def validate_gold_trade(
        self, entry: float, sl: float, tp: float
    ) -> tuple:
        """
        Dedicated Gold validator using RAW PRICE distances (not pips).
        Called only after direction structure is confirmed.

        Rules:
          TP distance ≥ 3.0   — prevents noise trades
          SL distance ≥ 3.0   — prevents hairline stops
          SL distance ≤ 50.0  — caps maximum risk
          R:R ≥ 1.8            — stricter than Forex default (1.5)

        Returns:
          (True,  rr: float)   on approval
          (False, reason: str) on rejection
        """
        tp_distance = abs(tp - entry)
        sl_distance = abs(entry - sl)

        if tp_distance < 3.0 or sl_distance < 3.0:
            return False, f"Gold TP/SL too small (TP={tp_distance:.2f}, SL={sl_distance:.2f}, min=3.0)"

        if sl_distance > 50.0:
            return False, f"Gold SL too large: {sl_distance:.2f} (max 50.0)"

        rr = tp_distance / sl_distance   # sl_distance > 0 guaranteed above
        if rr < 1.8:
            return False, f"Gold RR too low: {rr:.2f} (min 1.8)"

        return True, round(rr, 4)

    # ================================================================
    # REJECT HELPER
    # ================================================================

    def reject(self, reason: str) -> dict:
        return {"status": "REJECT", "reason": reason}

    # ================================================================
    # MAIN VALIDATE()
    # ================================================================

    def validate(self, signal: dict, open_trades: list = None) -> dict:
        """
        Run all production checks against a signal dict.

        Required signal keys:
            symbol, side (BUY/SELL), entry, sl, tp,
            current_price, spread (pips), timestamp (ISO-8601)

        Optional:
            ema50      (float, defaults to entry — skips EMA proximity check)
            confidence (float 0-100, defaults to 0)

        Returns:
            {"status": "EXECUTE", "rr": float, "confidence": float, "symbol_type": str}
            {"status": "REJECT",  "reason": str}
        """
        # ── Safe mutable default ──────────────────────────────
        if open_trades is None:
            open_trades = []

        try:
            # ── Required key presence check ───────────────────
            # BUG FIX: explicit KeyError before float() conversion
            # gives a clear rejection reason instead of a cryptic exception
            for key in self.REQUIRED_KEYS:
                if key not in signal:
                    return self.reject(f"Missing required signal field: '{key}'")

            symbol        = str(signal["symbol"]).upper()
            side          = str(signal["side"]).upper()
            entry         = float(signal["entry"])
            sl            = float(signal["sl"])
            tp            = float(signal["tp"])
            current_price = float(signal["current_price"])
            spread        = float(signal["spread"])
            timestamp     = signal["timestamp"]
            ema50         = float(signal.get("ema50", entry))
            confidence    = float(signal.get("confidence", 0))

            thresholds = self.get_thresholds(symbol)
            _stype     = self.get_symbol_type(symbol)

            # ── 1. Timestamp parse — always UTC ──────────────
            signal_time = datetime.fromisoformat(timestamp).astimezone(_tz.utc)
            now         = datetime.now(_tz.utc)
            age         = (now - signal_time).total_seconds()

            # ── 2. Future timestamp guard (BUG FIX) ──────────
            # Negative age means signal is dated in the future —
            # indicates clock skew or bad data. Hard reject.
            if age < 0:
                return self.reject(
                    f"Signal timestamp is in the future by {abs(age):.2f}s — "
                    f"possible clock skew or bad data"
                )

            # ── 3. Signal age — per-asset tolerance ──────────
            if _stype == "GOLD":
                _max_age = float(os.getenv("GK_MAX_SIGNAL_AGE_GOLD", "30"))  # Gold 4H signals need more time
            elif _stype == "JPY":
                _max_age = float(os.getenv("GK_MAX_SIGNAL_AGE_JPY", "6"))
            else:
                _max_age = self.max_signal_age
            if age > _max_age:
                return self.reject(
                    f"Signal too old: {age:.2f}s (max {_max_age}s for {_stype})"
                )

            # ── 4. Session filter ─────────────────────────────
            if not self.is_valid_session(signal_time, symbol):
                return self.reject(
                    f"Outside trading session (London 07-16 / NY 12-21 UTC) "
                    f"— hour: {signal_time.hour} UTC"
                )

            # ── 5. News filter ────────────────────────────────
            if self.is_high_impact_news_near(signal_time):
                return self.reject("High-impact news nearby — trade blocked")

            # ── 6. Confidence filter ──────────────────────────
            if not self.is_confident_signal(confidence):
                return self.reject(
                    f"Low confidence: {confidence:.1f}% (min {self.min_confidence}%)"
                )

            # ── 7. Max open trades ────────────────────────────
            if len(open_trades) >= self.max_open_trades:
                return self.reject(
                    f"Max open trades reached ({len(open_trades)}/{self.max_open_trades})"
                )

            # ── 8. Duplicate trade ────────────────────────────
            if self.is_duplicate_trade(signal, open_trades):
                return self.reject(f"Duplicate trade: {symbol} {side} already open")

            # ── 9. Price sanity (BUG FIX) ────────────────────
            # Guards against zero / negative prices from bad broker feeds.
            # Must run before direction check to avoid misleading error messages.
            for label, val in (("entry", entry), ("sl", sl), ("tp", tp),
                               ("current_price", current_price)):
                if val <= 0:
                    return self.reject(
                        f"Invalid price: {label}={val} — must be > 0"
                    )

            # ── 10. Direction structure ───────────────────────
            if side == "BUY":
                if not (tp > entry > sl):
                    return self.reject(
                        f"Invalid BUY structure: entry={entry}, TP={tp}, SL={sl}"
                    )
            elif side == "SELL":
                if not (tp < entry < sl):
                    return self.reject(
                        f"Invalid SELL structure: entry={entry}, TP={tp}, SL={sl}"
                    )
            else:
                return self.reject(f"Invalid side: {side!r} — expected BUY or SELL")

            # ── 11. Risk / Reward ─────────────────────────────
            # Gold: dedicated raw-price validator (stricter, min R:R 1.8)
            # Forex / JPY: pip-based calculation (min R:R GK_MIN_RR)
            if _stype == "GOLD":
                gold_valid, gold_result = self.validate_gold_trade(entry, sl, tp)
                if not gold_valid:
                    return self.reject(gold_result)
                rr = gold_result
            else:
                rr = self.calculate_rr(entry, sl, tp, symbol)
                if rr < self.min_rr:
                    return self.reject(f"RR too low: {rr:.2f} (min {self.min_rr})")

            # ── 12. Slippage — price units (fast check) ───────
            allowed_threshold = thresholds["price_threshold"]
            price_distance    = abs(entry - current_price)
            if price_distance > allowed_threshold:
                return self.reject(
                    f"Price moved too far: |entry - current| = {price_distance:.5f} "
                    f"(max {allowed_threshold} for {_stype})"
                )

            # ── 13. Slippage — pips (secondary check) ─────────
            slippage = self.price_to_pips(current_price - entry, symbol)
            if slippage > thresholds["slippage"]:
                return self.reject(
                    f"High slippage: {slippage:.2f} pips "
                    f"(max {thresholds['slippage']} for {_stype})"
                )

            # ── 14. Spread ────────────────────────────────────
            if spread <= 0:
                return self.reject("Invalid spread: must be > 0")
            if spread > thresholds["spread"]:
                return self.reject(
                    f"High spread: {spread:.2f} pips "
                    f"(max {thresholds['spread']} for {_stype})"
                )

            # ── 15. EMA-50 proximity ──────────────────────────
            # Skipped automatically when ema50 == entry (not provided by caller)
            if ema50 != entry:
                if not self.is_valid_entry(entry, ema50, symbol):
                    distance = self.price_to_pips(entry - ema50, symbol)
                    return self.reject(
                        f"Bad entry — {distance:.1f} pips from EMA50 "
                        f"(max {thresholds['ema_distance']} for {_stype})"
                    )

            # ✅ All checks passed ─────────────────────────────
            return {
                "status":      "EXECUTE",
                "rr":          round(rr, 2),
                "confidence":  round(confidence, 1),
                "symbol_type": _stype,
            }

        except (ValueError, TypeError) as e:
            # Catches float() conversion failures on bad input data
            return self.reject(f"Invalid signal data — {type(e).__name__}: {e}")
        except Exception as e:
            # Catches any unexpected errors — never crash the trading loop
            return self.reject(f"Gatekeeper exception [{type(e).__name__}]: {e}")


# Module-level singleton — re-use across requests
_gatekeeper = ExecutionGatekeeper()


def run_execution_gatekeeper(
    pair:          str,
    signal_type:   str,    # "BUY" or "SELL"
    entry_price:   float,
    tp1:           float,  # furthest TP level used as final TP
    sl_price:      float,
    current_price: float,
    spread_pips:   float,  # spread already converted to pips
    ema50:         float,
    signal_ts_iso: str,    # ISO-8601 UTC timestamp string
    open_trades:   list,   # list of {"symbol":…, "side":…} dicts
    confidence:    float = 0.0,  # 0-100 — passed to confidence filter
) -> tuple[bool, str, str]:
    """
    Thin wrapper around ExecutionGatekeeper.validate().
    Returns (approved: bool, reason_code: str, reason: str).
    """
    signal = {
        "symbol":        pair,
        "side":          signal_type,
        "entry":         entry_price,
        "sl":            sl_price,
        "tp":            tp1,
        "current_price": current_price,
        "spread":        spread_pips,
        "timestamp":     signal_ts_iso,
        "ema50":         ema50,
        "confidence":    confidence,
    }

    result = _gatekeeper.validate(signal, open_trades)
    _gk_log(pair, signal_type, result)

    if result["status"] == "EXECUTE":
        return (
            True, "OK",
            f"Approved — R:R={result['rr']} conf={result['confidence']}% ({result['symbol_type']})"
        )
    else:
        return False, "REJECTED", result.get("reason", "Unknown rejection")

# ============================================================
# END EXECUTION GATEKEEPER
# ============================================================


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
    pair: str
    type: str
    entry_price: float
    tp_levels: List[float]
    sl_price: float
    confidence: float
    analysis: str
    timeframe: str
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
    status: str = "ACTIVE"
    result: Optional[str] = None
    pips: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = None
    is_premium: bool = False

class SubscriptionUpdate(BaseModel):
    tier: str

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
        symbol_map = {
            "EURUSD": "EUR/USD",
            "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY", "EURJPY": "EUR/JPY",
            "GBPJPY": "GBP/JPY", "AUDUSD": "AUD/USD", "USDCAD": "USD/CAD",
            "USDCHF": "USD/CHF", "BTCUSD": "BTC/USD", "NZDUSD": "NZD/USD",
            "AUDJPY": "AUD/JPY", "CADJPY": "CAD/JPY", "CHFJPY": "CHF/JPY",
            "EURAUD": "EUR/AUD", "GBPCAD": "GBP/CAD", "EURCAD": "EUR/CAD",
            "GBPAUD": "GBP/AUD", "AUDNZD": "AUD/NZD", "EURGBP": "EUR/GBP",
            "EURCHF": "EUR/CHF",
        }

        api_symbol = symbol_map.get(symbol, symbol)
        url = f"https://api.twelvedata.com/time_series"
        params = {
            "symbol": api_symbol, "interval": interval,
            "apikey": TWELVE_DATA_API_KEY, "outputsize": outputsize
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
                for col in ["open", "high", "low", "close"]:
                    df[col] = pd.to_numeric(df[col])
                df["volume"] = pd.to_numeric(df["volume"]) if "volume" in df.columns else 0
                return df
    except Exception as e:
        logger.error(f"Error fetching price data for {symbol}: {e}")
        return None

def calculate_technical_indicators(df: pd.DataFrame) -> Dict[str, Any]:
    """Calculate technical indicators"""
    try:
        df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
        macd = ta.trend.MACD(df["close"])
        df["macd"] = macd.macd()
        df["macd_signal"] = macd.macd_signal()
        df["macd_diff"] = macd.macd_diff()
        df["ma_20"] = ta.trend.SMAIndicator(df["close"], window=20).sma_indicator()
        df["ma_50"] = ta.trend.SMAIndicator(df["close"], window=50).sma_indicator()
        df["ema_12"] = ta.trend.EMAIndicator(df["close"], window=12).ema_indicator()
        df["ema_50"] = ta.trend.EMAIndicator(df["close"], window=50).ema_indicator()  # ← for gatekeeper
        bollinger = ta.volatility.BollingerBands(df["close"])
        df["bb_upper"] = bollinger.bollinger_hband()
        df["bb_middle"] = bollinger.bollinger_mavg()
        df["bb_lower"] = bollinger.bollinger_lband()
        df["atr"] = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"]).average_true_range()

        latest = df.iloc[-1]
        return {
            "current_price": float(latest["close"]),
            "rsi": float(latest["rsi"]),
            "macd": float(latest["macd"]),
            "macd_signal": float(latest["macd_signal"]),
            "ma_20": float(latest["ma_20"]),
            "ma_50": float(latest["ma_50"]),
            "ema_50": float(latest["ema_50"]),     # ← for gatekeeper
            "bb_upper": float(latest["bb_upper"]),
            "bb_lower": float(latest["bb_lower"]),
            "atr": float(latest["atr"]),
            "trend": "BULLISH" if latest["close"] > latest["ma_50"] else "BEARISH"
        }
    except Exception as e:
        logger.error(f"Error calculating indicators: {e}")
        return None

# ============ PAIR-SPECIFIC OPTIMIZATION PARAMETERS ============
PAIR_PARAMETERS = {
    "BTCUSD": {
        "enabled": False,
        "use_fixed_pips": False,
        "atr_multiplier_sl": 2.0, "atr_multiplier_tp1": 1.5,
        "atr_multiplier_tp2": 3.0, "atr_multiplier_tp3": 4.5,
        "min_rr": 2.0, "pip_value": 1.0, "decimal_places": 2, "typical_spread": 10.0
    },
    "EURUSD": {
        "use_fixed_pips": True,
        "fixed_tp1_pips": 5, "fixed_tp2_pips": 10, "fixed_tp3_pips": 15, "fixed_sl_pips": 15,
        "atr_multiplier_sl": 1.2, "min_rr": 1.8,
        "pip_value": 0.0001, "decimal_places": 5, "typical_spread": 0.00010
    },
    "GBPUSD": {
        "use_fixed_pips": True,
        "fixed_tp1_pips": 5, "fixed_tp2_pips": 10, "fixed_tp3_pips": 15, "fixed_sl_pips": 15,
        "atr_multiplier_sl": 1.3, "min_rr": 1.8,
        "pip_value": 0.0001, "decimal_places": 5, "typical_spread": 0.00012
    },
    "USDJPY": {
        "use_fixed_pips": True,
        "fixed_tp1_pips": 5, "fixed_tp2_pips": 10, "fixed_tp3_pips": 15, "fixed_sl_pips": 15,
        "atr_multiplier_sl": 1.2, "min_rr": 1.8,
        "pip_value": 0.01, "decimal_places": 3, "typical_spread": 0.010
    },
    "EURJPY": {
        "use_fixed_pips": True,
        "fixed_tp1_pips": 5, "fixed_tp2_pips": 10, "fixed_tp3_pips": 15, "fixed_sl_pips": 15,
        "atr_multiplier_sl": 1.4, "min_rr": 1.8,
        "pip_value": 0.01, "decimal_places": 3, "typical_spread": 0.015
    },
    "GBPJPY": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 5, "fixed_tp2_pips": 10, "fixed_tp3_pips": 15, "fixed_sl_pips": 15,
        "atr_multiplier_sl": 1.5, "min_rr": 1.8,
        "pip_value": 0.01, "decimal_places": 3, "typical_spread": 0.020
    },
    "AUDUSD": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 6, "fixed_tp2_pips": 12, "fixed_tp3_pips": 18, "fixed_sl_pips": 12,
        "atr_multiplier_sl": 1.0, "min_rr": 1.8,
        "pip_value": 0.0001, "decimal_places": 5, "typical_spread": 0.00012
    },
    "USDCAD": {
        "use_fixed_pips": True,
        "fixed_tp1_pips": 5, "fixed_tp2_pips": 10, "fixed_tp3_pips": 15, "fixed_sl_pips": 15,
        "atr_multiplier_sl": 1.2, "min_rr": 1.8,
        "pip_value": 0.0001, "decimal_places": 5, "typical_spread": 0.00015
    },
    "USDCHF": {
        "use_fixed_pips": True,
        "fixed_tp1_pips": 5, "fixed_tp2_pips": 10, "fixed_tp3_pips": 15, "fixed_sl_pips": 15,
        "atr_multiplier_sl": 1.2, "min_rr": 1.8,
        "pip_value": 0.0001, "decimal_places": 5, "typical_spread": 0.00012
    },
    "NZDUSD": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 5, "fixed_tp2_pips": 10, "fixed_tp3_pips": 15, "fixed_sl_pips": 15,
        "atr_multiplier_sl": 1.2, "min_rr": 1.8,
        "pip_value": 0.0001, "decimal_places": 5, "typical_spread": 0.00015
    },
    "AUDJPY": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 5, "fixed_tp2_pips": 10, "fixed_tp3_pips": 15, "fixed_sl_pips": 15,
        "atr_multiplier_sl": 1.3, "min_rr": 1.8,
        "pip_value": 0.01, "decimal_places": 3, "typical_spread": 0.015
    },
    "CADJPY": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 5, "fixed_tp2_pips": 10, "fixed_tp3_pips": 15, "fixed_sl_pips": 15,
        "atr_multiplier_sl": 1.3, "min_rr": 1.8,
        "pip_value": 0.01, "decimal_places": 3, "typical_spread": 0.015
    },
    "CHFJPY": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 5, "fixed_tp2_pips": 10, "fixed_tp3_pips": 15, "fixed_sl_pips": 15,
        "atr_multiplier_sl": 1.3, "min_rr": 1.8,
        "pip_value": 0.01, "decimal_places": 3, "typical_spread": 0.015
    },
    "EURAUD": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 6, "fixed_tp2_pips": 12, "fixed_tp3_pips": 18, "fixed_sl_pips": 12,
        "atr_multiplier_sl": 1.4, "min_rr": 1.8,
        "pip_value": 0.0001, "decimal_places": 5, "typical_spread": 0.00020
    },
    "GBPCAD": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 6, "fixed_tp2_pips": 12, "fixed_tp3_pips": 18, "fixed_sl_pips": 12,
        "atr_multiplier_sl": 1.4, "min_rr": 1.8,
        "pip_value": 0.0001, "decimal_places": 5, "typical_spread": 0.00025
    },
    "EURCAD": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 5, "fixed_tp2_pips": 10, "fixed_tp3_pips": 15, "fixed_sl_pips": 15,
        "atr_multiplier_sl": 1.3, "min_rr": 1.8,
        "pip_value": 0.0001, "decimal_places": 5, "typical_spread": 0.00020
    },
    "GBPAUD": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 6, "fixed_tp2_pips": 12, "fixed_tp3_pips": 18, "fixed_sl_pips": 12,
        "atr_multiplier_sl": 1.5, "min_rr": 1.8,
        "pip_value": 0.0001, "decimal_places": 5, "typical_spread": 0.00025
    },
    "AUDNZD": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 5, "fixed_tp2_pips": 10, "fixed_tp3_pips": 15, "fixed_sl_pips": 15,
        "atr_multiplier_sl": 1.2, "min_rr": 1.8,
        "pip_value": 0.0001, "decimal_places": 5, "typical_spread": 0.00018
    },
    "EURGBP": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 6, "fixed_tp2_pips": 12, "fixed_tp3_pips": 18, "fixed_sl_pips": 12,
        "atr_multiplier_sl": 1.0, "min_rr": 1.8,
        "pip_value": 0.0001, "decimal_places": 5, "typical_spread": 0.00012
    },
    "EURCHF": {
        "enabled": True,
        "use_fixed_pips": True,
        "fixed_tp1_pips": 6, "fixed_tp2_pips": 12, "fixed_tp3_pips": 18, "fixed_sl_pips": 12,
        "atr_multiplier_sl": 1.0, "min_rr": 1.8,
        "pip_value": 0.0001, "decimal_places": 5, "typical_spread": 0.00015
    },
}

# ============ PROFITABILITY FILTERS ============
# ============================================================
# MULTI-STRATEGY REGIME CONFIG
# ------------------------------------------------------------
# UPTREND   → BUY  only  (swing trend-following)
# DOWNTREND → SELL only  (swing trend-following)
# RANGE     → DISABLED   (no trades in sideways markets)
# HIGH_VOL  → Both BUY and SELL (breakout/momentum)
# ══════════════════════════════════════════════════════════════
ALLOWED_REGIMES = ["UPTREND", "DOWNTREND", "HIGH_VOL"]
SKIP_REGIME     = ["RANGE", "UNKNOWN"]  # No RANGE, no UNKNOWN regime trades

# ── Per-strategy type ─────────────────────────────────────────
# Each regime maps to a strategy which controls timeframe,
# confidence threshold, and TP/SL multipliers.
REGIME_STRATEGY = {
    "UPTREND":   "SWING",      # BUY only
    "DOWNTREND": "SWING",      # SELL only
    "HIGH_VOL":  "BREAKOUT",   # Both directions, momentum
}

# ── Per-strategy timeframes ────────────────────────────────────
STRATEGY_TIMEFRAME = {
    "SWING":    "4h",    # Gold and trending pairs: 4H for reliable swings
    "BREAKOUT": "1h",    # Breakout: 1H is fast enough
    "SCALP":    "15min", # Scalp: 15-min candles (not used by default)
}

# ── Per-strategy confidence minimums ─────────────────────────
STRATEGY_MIN_CONFIDENCE = {
    "SWING":    70,   # Minimum 70% AI confidence for swing trades
    "BREAKOUT": 70,   # Minimum 70% AI confidence for breakout trades
    "SCALP":    70,   # Minimum 70% AI confidence across all strategies
}

# ── Global thresholds ─────────────────────────────────────────
MIN_CONFIDENCE_THRESHOLD  = 70   # Minimum 70% AI confidence required
MIN_REGIME_CONFIDENCE     = 0.50 # Regime detector minimum confidence
HIGH_CONFIDENCE_THRESHOLD = 75
GOLD_PAIRS                = []  # Gold handled by gold_server.py → @grandcomgold
GOLD_CONFIDENCE_THRESHOLD = 70   # Gold swing — same as baseline
SIGNAL_THROTTLE_MINUTES   = 240  # Minimum 4h between signals per pair (enforced)
last_signal_time: dict = {}       # {pair: datetime} — tracks last signal sent
SESSION_FILTERS = {}
DRAWDOWN_PROTECTION = {
    "enabled":             True,
    "max_daily_losses":    2,     # Stop after 2 losses in a day (was 3)
    "max_daily_loss_pips": 30,    # Stop after 30 pips loss (was 50)
    "pause_duration_hours": 8,    # Pause 8h after hitting limit (was 4)
}
daily_pair_performance = {}

DEFAULT_PAIR_PARAMS = {
    "atr_multiplier_sl": 1.5, "atr_multiplier_tp1": 1.0,
    "atr_multiplier_tp2": 2.0, "atr_multiplier_tp3": 3.0,
    "min_rr": 2.0, "pip_value": 0.0001,
    "decimal_places": 5, "typical_spread": 0.00015
}

def is_session_optimal(pair: str) -> bool:
    now = datetime.utcnow()
    current_hour = now.hour
    current_minute = now.minute
    if pair not in SESSION_FILTERS:
        return True
    filter_config = SESSION_FILTERS[pair]
    optimal_hours = filter_config.get("optimal_hours", list(range(24)))
    block_before_close = filter_config.get("block_before_close", 15)
    if current_hour not in optimal_hours:
        return False
    session_end_hours = [8, 16, 21]
    for end_hour in session_end_hours:
        if current_hour == end_hour - 1 and current_minute >= (60 - block_before_close):
            logging.info(f"⏰ {pair} blocked - {block_before_close} mins before session close")
            return False
    return True

def check_drawdown_protection(pair: str) -> tuple[bool, str]:
    global daily_pair_performance
    if not DRAWDOWN_PROTECTION["enabled"]:
        return True, ""
    today = datetime.utcnow().date().isoformat()
    key = f"{pair}_{today}"
    if key not in daily_pair_performance:
        daily_pair_performance[key] = {"losses": 0, "loss_pips": 0, "paused_until": None}
    perf = daily_pair_performance[key]
    if perf["paused_until"]:
        if datetime.utcnow() < perf["paused_until"]:
            remaining = (perf["paused_until"] - datetime.utcnow()).seconds // 60
            return False, f"Paused for {remaining} more minutes (drawdown protection)"
        else:
            perf["paused_until"] = None
    if perf["losses"] >= DRAWDOWN_PROTECTION["max_daily_losses"]:
        perf["paused_until"] = datetime.utcnow() + timedelta(hours=DRAWDOWN_PROTECTION["pause_duration_hours"])
        return False, f"Max daily losses ({perf['losses']}) reached"
    if perf["loss_pips"] >= DRAWDOWN_PROTECTION["max_daily_loss_pips"]:
        perf["paused_until"] = datetime.utcnow() + timedelta(hours=DRAWDOWN_PROTECTION["pause_duration_hours"])
        return False, f"Max daily loss pips ({perf['loss_pips']}) reached"
    return True, ""

def record_trade_result(pair: str, result: str, pips: float):
    global daily_pair_performance
    today = datetime.utcnow().date().isoformat()
    key = f"{pair}_{today}"
    if key not in daily_pair_performance:
        daily_pair_performance[key] = {"losses": 0, "loss_pips": 0, "paused_until": None}
    if result == "LOSS":
        daily_pair_performance[key]["losses"] += 1
        daily_pair_performance[key]["loss_pips"] += abs(pips)

async def generate_ai_analysis(symbol: str, indicators: Dict[str, Any]) -> Dict[str, Any]:
    """Generate AI-powered trading signal with pair-specific optimization"""
    try:
        params = PAIR_PARAMETERS.get(symbol, DEFAULT_PAIR_PARAMS)
        use_fixed_pips = params.get('use_fixed_pips', False)

        system_message = "You are an elite institutional forex and commodities trader. Provide precise, actionable trading signals with strict risk management."

        if use_fixed_pips:
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
            TP1: {tp1_pips} pips | TP2: {tp2_pips} pips | TP3: {tp3_pips} pips
            SL: ATR × {params['atr_multiplier_sl']} | Pip Value: {pip_value}

            === REQUIREMENTS ===
            1. BUY: TP above entry, SL below entry
            2. SELL: TP below entry, SL above entry
            3. Round all prices to {params['decimal_places']} decimal places

            === OUTPUT FORMAT (JSON ONLY) ===
            {{"signal":"BUY"or"SELL"or"NEUTRAL","confidence":0-100,"entry_price":numeric,"tp_levels":[tp1,tp2,tp3],"sl_price":numeric,"analysis":"<150 words","risk_reward":numeric}}
            RESPOND ONLY WITH VALID JSON. NO OTHER TEXT.
            """
        else:
            prompt = f"""
            Analyze {symbol} market data and provide a professional trading signal:

            === MARKET DATA ===
            Current Price: {indicators['current_price']}
            RSI: {indicators['rsi']:.2f} | MACD: {indicators['macd']:.6f}
            MA50: {indicators['ma_50']:.{params['decimal_places']}f}
            ATR: {indicators['atr']:.{params['decimal_places']}f}
            Trend: {indicators['trend']}

            === ATR MULTIPLIERS ===
            SL: {params['atr_multiplier_sl']} | TP1: {params.get('atr_multiplier_tp1',1.0)} | TP2: {params.get('atr_multiplier_tp2',2.0)} | TP3: {params.get('atr_multiplier_tp3',3.0)}
            Min R:R: {params['min_rr']}

            === OUTPUT FORMAT (JSON ONLY) ===
            {{"signal":"BUY"or"SELL"or"NEUTRAL","confidence":0-100,"entry_price":numeric,"tp_levels":[tp1,tp2,tp3],"sl_price":numeric,"analysis":"<150 words","risk_reward":numeric}}
            RESPOND ONLY WITH VALID JSON. NO OTHER TEXT.
            """

        max_retries = 3
        ai_response = None
        for attempt in range(max_retries):
            try:
                chat = LlmChat(
                    api_key=EMERGENT_LLM_KEY,
                    session_id=f"signal_{symbol}_{datetime.utcnow().timestamp()}_{attempt}",
                    system_message=system_message
                ).with_model("openai", "gpt-4o-mini")
                user_msg = UserMessage(text=prompt)
                ai_response = await chat.send_message(user_msg)
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

        import json
        import re
        raw = ai_response.strip()
        # Remove markdown code fences
        fence_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', raw, re.DOTALL)
        if fence_match:
            raw = fence_match.group(1).strip()
        if not raw.startswith('{'):
            brace_match = re.search(r'\{.*\}', raw, re.DOTALL)
            if brace_match:
                raw = brace_match.group(0)
        try:
            ai_data = json.loads(raw)
        except json.JSONDecodeError:
            fixed = re.sub(r',\s*}', '}', raw)
            fixed = re.sub(r',\s*]', ']', fixed)
            fixed = re.sub(r'"\s*\n\s*"', '",\n"', fixed)
            fixed = fixed.replace("'", '"')
            ai_data = json.loads(fixed)

        entry = ai_data.get("entry_price", indicators['current_price'])
        signal_type = ai_data.get("signal", "BUY")
        tp_levels = ai_data.get("tp_levels", [])

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
            else:
                tp_levels = [
                    round(entry - (tp1_pips * pip_value), params['decimal_places']),
                    round(entry - (tp2_pips * pip_value), params['decimal_places']),
                    round(entry - (tp3_pips * pip_value), params['decimal_places'])
                ]
            ai_data["tp_levels"] = tp_levels

        # ── GOLD OVERRIDE: always recalculate TP/SL from ATR ─────────
        # The AI tends to return tiny fixed-pip-style values for Gold.
        # We force correct swing targets using ATR multipliers from PAIR_PARAMETERS.
        # This runs for XAUUSD and XAUEUR regardless of use_fixed_pips.
        if "XAU" in symbol and signal_type != "NEUTRAL":
            atr = indicators.get('atr', 0)
            if atr > 0:
                m_sl  = params.get('atr_multiplier_sl',  1.5)
                m_tp1 = params.get('atr_multiplier_tp1', 2.0)
                m_tp2 = params.get('atr_multiplier_tp2', 3.5)
                m_tp3 = params.get('atr_multiplier_tp3', 5.0)
                dp    = params.get('decimal_places', 2)
                if signal_type == "BUY":
                    tp_levels = [
                        round(entry + atr * m_tp1, dp),
                        round(entry + atr * m_tp2, dp),
                        round(entry + atr * m_tp3, dp),
                    ]
                    sl_gold = round(entry - atr * m_sl, dp)
                else:  # SELL
                    tp_levels = [
                        round(entry - atr * m_tp1, dp),
                        round(entry - atr * m_tp2, dp),
                        round(entry - atr * m_tp3, dp),
                    ]
                    sl_gold = round(entry + atr * m_sl, dp)
                ai_data["tp_levels"] = tp_levels
                ai_data["sl_price"]  = sl_gold
                # Recalculate R:R using raw distances
                tp_dist = abs(tp_levels[2] - entry)
                sl_dist = abs(entry - sl_gold)
                ai_data["risk_reward"] = round(tp_dist / sl_dist, 2) if sl_dist > 0 else params['min_rr']
                logger.info(
                    f"🪙 {symbol} ATR-override: entry={entry} SL={sl_gold} "
                    f"TP1={tp_levels[0]} TP2={tp_levels[1]} TP3={tp_levels[2]} "
                    f"ATR={atr:.2f} R:R={ai_data['risk_reward']}"
                )

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

        risk_reward = ai_data.get("risk_reward", params['min_rr'])
        if isinstance(risk_reward, str) and ":" in risk_reward:
            parts = risk_reward.split(":")
            try:
                risk_reward = float(parts[1]) if len(parts) == 2 else params['min_rr']
            except:
                risk_reward = params['min_rr']
        elif not isinstance(risk_reward, (int, float)):
            risk_reward = params['min_rr']
        ai_data["risk_reward"] = risk_reward

        return ai_data
    except Exception as e:
        logger.error(f"Error generating AI analysis for {symbol}: {e}")
        return None


# ============ SIGNAL QUALITY HELPERS ============

async def check_higher_timeframe_alignment(pair: str, signal_direction: str) -> tuple[bool, str]:
    try:
        h4_df = await get_price_data(pair, interval="4h", outputsize=50)
        await asyncio.sleep(0.3)
        d1_df = await get_price_data(pair, interval="1day", outputsize=30)

        def get_trend(df: pd.DataFrame, label: str) -> str:
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
                bullish_count = sum([
                    latest["close"] > latest["ema_50"],
                    latest["ema_20"] > latest["ema_50"],
                    latest["adx_pos"] > latest["adx_neg"]
                ])
                if bullish_count >= 2: return "BULLISH"
                elif bullish_count <= 1: return "BEARISH"
                return "NEUTRAL"
            except Exception as e:
                logger.warning(f"Trend calc error for {label}: {e}")
                return "NEUTRAL"

        h4_trend = get_trend(h4_df, f"{pair}/H4")
        d1_trend = get_trend(d1_df, f"{pair}/D1")
        logger.info(f"🕐 {pair} MTF check: H4={h4_trend}, D1={d1_trend}, signal={signal_direction}")

        if h4_trend == "NEUTRAL" and d1_trend == "NEUTRAL":
            return True, f"H4=NEUTRAL D1=NEUTRAL (allowing)"

        if signal_direction == "BUY":
            h4_ok = h4_trend in ("BULLISH", "NEUTRAL")
            d1_ok = d1_trend in ("BULLISH", "NEUTRAL")
            if h4_ok and d1_ok:
                return True, f"H4={h4_trend} D1={d1_trend} aligned BULLISH ✓"
            conflicts = [f"H4={h4_trend}" if not h4_ok else "", f"D1={d1_trend}" if not d1_ok else ""]
            return False, f"BUY conflicts: {', '.join(c for c in conflicts if c)}"

        elif signal_direction == "SELL":
            h4_ok = h4_trend in ("BEARISH", "NEUTRAL")
            d1_ok = d1_trend in ("BEARISH", "NEUTRAL")
            if h4_ok and d1_ok:
                return True, f"H4={h4_trend} D1={d1_trend} aligned BEARISH ✓"
            conflicts = [f"H4={h4_trend}" if not h4_ok else "", f"D1={d1_trend}" if not d1_ok else ""]
            return False, f"SELL conflicts: {', '.join(c for c in conflicts if c)}"

        return True, f"Direction={signal_direction} (allowing)"
    except Exception as e:
        logger.error(f"check_higher_timeframe_alignment error for {pair}: {e}")
        return True, f"MTF check error (allowing): {e}"


def detect_choppy_market(df: pd.DataFrame, pair: str) -> tuple[bool, str]:
    try:
        if df is None or len(df) < 20:
            return False, "Insufficient data for chop detection"
        df = df.copy()
        n = 14
        high_n = df["high"].rolling(n).max()
        low_n = df["low"].rolling(n).min()
        atr_1 = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=1).average_true_range()
        atr_sum_n = atr_1.rolling(n).sum()
        hl_range = high_n - low_n
        chop_index = np.where(hl_range > 0, 100.0 * np.log10(atr_sum_n / hl_range) / np.log10(n), 50.0)
        df["chop_index"] = chop_index
        latest_chop = float(df["chop_index"].iloc[-1])

        bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
        df["bb_upper"] = bb.bollinger_hband()
        df["bb_lower"] = bb.bollinger_lband()
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["close"] * 100
        latest_bb_width = float(df["bb_width"].iloc[-1])

        adx_ind = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14)
        df["adx"] = adx_ind.adx()
        latest_adx = float(df["adx"].iloc[-1])

        df["atr"] = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
        atr_mean = float(df["atr"].tail(14).mean())
        latest_atr = float(df["atr"].iloc[-1])
        atr_ratio = latest_atr / atr_mean if atr_mean > 0 else 1.0

        chop_signals = 0
        chop_reasons = []
        if latest_chop > 61.8:
            chop_signals += 1; chop_reasons.append(f"CI={latest_chop:.1f}>61.8")
        if latest_bb_width < 0.5:
            chop_signals += 1; chop_reasons.append(f"BB_width={latest_bb_width:.2f}%<0.5%")
        if latest_adx < 20 and atr_ratio < 0.85:
            chop_signals += 1; chop_reasons.append(f"ADX={latest_adx:.1f}<20 + ATR_ratio={atr_ratio:.2f}")

        if chop_signals >= 2:
            return True, f"Choppy market ({', '.join(chop_reasons)})"
        return False, f"Trending (CI={latest_chop:.1f}, ADX={latest_adx:.1f}, BB_w={latest_bb_width:.2f}%)"
    except Exception as e:
        logger.error(f"detect_choppy_market error for {pair}: {e}")
        return False, f"Chop detection error (allowing): {e}"


async def generate_signal_for_pair(pair: str) -> Optional[Signal]:
    """Generate a complete trading signal for a pair with ML optimization and Execution Gatekeeper"""
    try:
        params = PAIR_PARAMETERS.get(pair, DEFAULT_PAIR_PARAMS)

        # FILTER 1: SESSION
        if not is_session_optimal(pair):
            logger.info(f"⏰ {pair} skipped - not in optimal session")
            return None

        # FILTER 2: DRAWDOWN PROTECTION
        can_trade, pause_reason = check_drawdown_protection(pair)
        if not can_trade:
            logger.warning(f"🛑 {pair} paused - {pause_reason}")
            return None

        # FILTER 2b: PER-PAIR SIGNAL THROTTLE
        # Prevents the same pair from firing multiple times in a short window
        last_ts = last_signal_time.get(pair)
        if last_ts:
            elapsed_minutes = (datetime.utcnow() - last_ts).total_seconds() / 60
            if elapsed_minutes < SIGNAL_THROTTLE_MINUTES:
                remaining = int(SIGNAL_THROTTLE_MINUTES - elapsed_minutes)
                logger.info(f"⏳ {pair} throttled — last signal {elapsed_minutes:.0f} min ago, wait {remaining} more min")
                return None

        # ── Strategy-aware timeframe selection ───────────────────
        # Gold (SWING) → 4H candles  |  Forex → 1H  |  Scalp → 15min
        pair_strategy  = params.get("strategy", "SWING" if pair in GOLD_PAIRS else "SWING")
        pair_timeframe = params.get("timeframe",
                            STRATEGY_TIMEFRAME.get(pair_strategy, "1h"))
        min_candles    = params.get("min_candles", 50)
        fetch_size     = max(100, min_candles + 20)  # always fetch enough for indicators

        logger.info(f"📊 {pair} using {pair_strategy} strategy on {pair_timeframe} timeframe")

        df = await get_price_data(pair, interval=pair_timeframe, outputsize=fetch_size)
        if df is None or len(df) < min_candles:
            logger.warning(f"Insufficient data for {pair} (got {len(df) if df is not None else 0}, need {min_candles})")
            return None

        indicators = calculate_technical_indicators(df)
        if indicators is None:
            return None

        # Record the signal generation timestamp for latency check
        signal_generated_at = time.time()

        ai_analysis = await generate_ai_analysis(pair, indicators)
        if ai_analysis is None or ai_analysis.get("signal") == "NEUTRAL":
            logger.info(f"No trade signal for {pair} (NEUTRAL or None)")
            return None

        # ── Gold sanity check: reject tiny TP/SL immediately ─────────
        if pair in GOLD_PAIRS:
            _tp_check = ai_analysis.get("tp_levels", [])
            _sl_check = ai_analysis.get("sl_price", 0)
            _en_check = ai_analysis.get("entry_price", 0)
            if _tp_check and _en_check and _sl_check:
                _tp_dist = abs(_tp_check[-1] - _en_check)  # use furthest TP
                _sl_dist = abs(_en_check - _sl_check)
                if _tp_dist < 3.0 or _sl_dist < 3.0:
                    logger.warning(
                        f"🚫 {pair} REJECTED — Gold TP/SL too small: "
                        f"TP_dist={_tp_dist:.2f}, SL_dist={_sl_dist:.2f} (min 3.0). "
                        f"ATR override will fix this on next cycle."
                    )
                    return None

        # FILTER 3: CONFIDENCE — early gate (catches truly bad signals)
        ai_confidence   = float(ai_analysis.get("confidence", 0))
        # Use strategy-specific threshold
        strategy_type   = params.get("strategy", "SWING" if pair in GOLD_PAIRS else "SWING")
        strat_min_conf  = STRATEGY_MIN_CONFIDENCE.get(strategy_type, MIN_CONFIDENCE_THRESHOLD)
        early_threshold = max(strat_min_conf,
                              GOLD_CONFIDENCE_THRESHOLD if pair in GOLD_PAIRS else MIN_CONFIDENCE_THRESHOLD)
        if ai_confidence < early_threshold:
            logger.info(f"📊 {pair} [{strategy_type}] skipped — confidence {ai_confidence:.1f}% < {early_threshold}%")
            return None

        # FILTER 3b: MULTI-TIMEFRAME CONFIRMATION
        # Gold uses 4H as primary — skip redundant MTF check (it IS the 4H data)
        if pair not in GOLD_PAIRS:
            try:
                signal_direction = ai_analysis.get("signal", "NEUTRAL")
                mtf_confirmed, mtf_reason = await check_higher_timeframe_alignment(pair, signal_direction)
                if not mtf_confirmed:
                    logger.info(f"🕐 {pair} skipped - MTF failed: {mtf_reason}")
                    return None
            except Exception as mtf_err:
                logger.warning(f"⚠️ {pair} MTF check error (allowing): {mtf_err}")
        else:
            logger.info(f"🪙 {pair} MTF check skipped — already on 4H swing timeframe")

        # FILTER 3c: CHOPPY MARKET DETECTION
        try:
            is_choppy, chop_reason = detect_choppy_market(df, pair)
            if is_choppy:
                logger.info(f"📉 {pair} skipped - choppy: {chop_reason}")
                return None
        except Exception as chop_err:
            logger.warning(f"⚠️ {pair} chop detection error (allowing): {chop_err}")

        # FILTER 4-6: ML OPTIMIZATION
        try:
            optimized = signal_optimizer.optimize_signal(
                df=df, symbol=pair, ai_signal=ai_analysis, pair_params=params
            )
            if optimized.get('blocked'):
                logger.warning(f"Signal blocked for {pair}: {optimized.get('block_reason')}")
                return None
            if optimized.get('filtered'):
                logger.info(f"Signal filtered for {pair}: {optimized.get('filter_reason')}")
                return None

            regime_info      = optimized.get('regime', {})
            regime_name      = regime_info.get('name', 'UNKNOWN')
            regime_confidence= regime_info.get('confidence', 0.5)
            risk_multiplier  = regime_info.get('risk_multiplier', 1.0)

            if regime_name in SKIP_REGIME:
                logger.info(f"📉 {pair} skipped - {regime_name} regime")
                return None
            if regime_confidence < MIN_REGIME_CONFIDENCE:
                logger.info(f"🎯 {pair} skipped - regime conf {regime_confidence:.2f} < {MIN_REGIME_CONFIDENCE}")
                return None

            signal_type = ai_analysis["signal"]

            # ── Regime enforcement ──────────────────────────────────
            # UPTREND   → SWING → BUY only
            # DOWNTREND → SWING → SELL only
            # RANGE     → DISABLED (caught by SKIP_REGIME above)
            # HIGH_VOL  → BREAKOUT → both BUY and SELL
            active_strategy   = REGIME_STRATEGY.get(regime_name, "SWING")
            strategy_min_conf = STRATEGY_MIN_CONFIDENCE.get(active_strategy, MIN_CONFIDENCE_THRESHOLD)

            if regime_name == "UPTREND" and signal_type != "BUY":
                logger.info(f"📈 {pair} [SWING] REJECTED — UPTREND=BUY only (got {signal_type})")
                return None
            elif regime_name == "DOWNTREND" and signal_type != "SELL":
                logger.info(f"📉 {pair} [SWING] REJECTED — DOWNTREND=SELL only (got {signal_type})")
                return None
            # HIGH_VOL / UNKNOWN → both directions allowed

            # Per-strategy confidence check (after regime is known)
            if ai_confidence < strategy_min_conf:
                logger.info(
                    f"📊 {pair} REJECTED — {active_strategy} strategy needs "
                    f"{strategy_min_conf}% confidence (got {ai_confidence:.1f}%)"
                )
                return None

            logger.info(f"✅ {pair} strategy={active_strategy} regime={regime_name} conf={ai_confidence:.1f}%")

            if optimized.get('optimized'):
                entry_price = optimized.get('entry_price', ai_analysis['entry_price'])
                tp_levels   = optimized.get('tp_levels',   ai_analysis['tp_levels'])
                sl_price    = optimized.get('sl_price',    ai_analysis['sl_price'])
            else:
                entry_price = ai_analysis['entry_price']
                tp_levels   = ai_analysis['tp_levels']
                sl_price    = ai_analysis['sl_price']

        except Exception as ml_error:
            logger.warning(f"ML optimization failed for {pair}: {ml_error}. Using raw AI signal.")
            entry_price      = ai_analysis['entry_price']
            tp_levels        = ai_analysis['tp_levels']
            sl_price         = ai_analysis['sl_price']
            regime_name      = 'UNKNOWN'
            regime_confidence= 0.5
            risk_multiplier  = 1.0

        # ================================================================
        # EXECUTION GATEKEEPER  ← Symbol-aware validation layer
        # Uses ExecutionGatekeeper class with per-asset pip thresholds.
        # ================================================================
        try:
            # Build open-trades list in the format the gatekeeper expects
            active_docs = await db.signals.find(
                {"status": "ACTIVE"}, {"pair": 1, "type": 1}
            ).to_list(length=200)
            open_trades_list = [
                {"symbol": d["pair"], "side": d["type"]} for d in active_docs
            ]

            # Convert raw spread (price units) → pips using gatekeeper's multiplier
            raw_spread    = params.get("typical_spread", 0.0002)
            spread_pips   = _gatekeeper.price_to_pips(raw_spread, pair)
            ema50         = indicators.get("ema_50", 0.0)
            signal_iso_ts = datetime.now(_tz.utc).isoformat()

            # Use furthest TP (tp_levels[-1]) as the "final TP" for R:R calculation
            final_tp = tp_levels[-1] if tp_levels else entry_price

            gk_approved, gk_code, gk_reason = run_execution_gatekeeper(
                pair          = pair,
                signal_type   = ai_analysis["signal"],
                entry_price   = entry_price,
                tp1           = final_tp,
                sl_price      = sl_price,
                current_price = indicators["current_price"],
                spread_pips   = spread_pips,
                ema50         = ema50,
                signal_ts_iso = signal_iso_ts,
                open_trades   = open_trades_list,
                confidence    = ai_confidence,  # raw AI confidence — not multiplied by regime
            )

            if not gk_approved:
                logger.warning(f"🚫 GATEKEEPER REJECTED {pair} [{gk_code}]: {gk_reason}")
                return None

            logger.info(f"✅ GATEKEEPER APPROVED {pair} {ai_analysis['signal']} — {gk_reason}")

        except Exception as gk_err:
            logger.error(f"⚠️ Gatekeeper error for {pair} (allowing): {gk_err}")
        # ================================================================
        # END EXECUTION GATEKEEPER
        # ================================================================

        # Parse risk_reward
        risk_reward = ai_analysis.get("risk_reward", params['min_rr'])
        if isinstance(risk_reward, str) and ":" in risk_reward:
            parts = risk_reward.split(":")
            try:
                risk_reward = float(parts[1]) if len(parts) == 2 else params['min_rr']
            except:
                risk_reward = params['min_rr']
        elif not isinstance(risk_reward, (int, float)):
            risk_reward = params['min_rr']

        # ── Confidence separation ─────────────────────────────────────
        # ai_confidence  : raw score from AI (what subscribers/MT5 see)
        # adjusted_score : ai * regime_confidence (internal quality score)
        # Problem fixed  : previously ai*regime was used everywhere, causing
        #                  80% signals to appear as 40% and fail all thresholds.
        ai_confidence     = float(ai_analysis.get("confidence", 0))
        adjusted_score    = ai_confidence * regime_confidence

        # Gold pairs require minimum 75% raw AI confidence
        effective_min = GOLD_CONFIDENCE_THRESHOLD if pair in GOLD_PAIRS else MIN_CONFIDENCE_THRESHOLD
        display_confidence = round(ai_confidence, 1)   # always show raw AI confidence

        signal = Signal(
            pair         = pair,
            type         = ai_analysis["signal"],
            entry_price  = entry_price,
            current_price= indicators["current_price"],
            tp_levels    = tp_levels,
            sl_price     = sl_price,
            confidence   = display_confidence,          # raw AI % — shown in Telegram + MT5
            analysis     = f"[{regime_name} | score={adjusted_score:.0f}] {ai_analysis['analysis']}",
            timeframe    = pair_timeframe.upper(),
            risk_reward  = risk_reward,
            is_premium   = display_confidence >= effective_min  # premium = meets threshold
        )

        signal_dict = signal.dict(exclude={"id"})
        signal_dict['regime']          = regime_name
        signal_dict['risk_multiplier'] = risk_multiplier
        result = await db.signals.insert_one(signal_dict)
        signal.id = str(result.inserted_id)

        # Record signal time for per-pair throttle
        last_signal_time[pair] = datetime.utcnow()

        await send_signal_to_telegram(signal, regime_name, risk_multiplier)

        try:
            push_svc = get_push_service()
            if push_svc:
                await push_svc.send_new_signal_notification({
                    "id": signal.id, "pair": signal.pair, "type": signal.type,
                    "entry_price": signal.entry_price, "confidence": signal.confidence,
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
    if not text:
        return ""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text

async def send_signal_to_telegram(signal: Signal, regime_name: str = "UNKNOWN", risk_mult: float = 1.0):
    """
    Send ONE single plain-text message that works for both:
    - TSCopier AI parser (reads symbol, direction, entry, TP, SL)
    - Human subscribers (sees confidence, R:R, analysis)

    No HTML. No second message. One message only.
    TSCopier parses the top block; humans read the full thing.
    """
    try:
        if not TELEGRAM_BOT_TOKEN:
            logger.warning("Telegram bot token not configured")
            return

        bot              = Bot(token=TELEGRAM_BOT_TOKEN)
        forex_channel_id = os.environ.get('TELEGRAM_CHANNEL_ID', '@grandcomsignals')
        # Forex server only — Gold signals handled by gold_server.py
        # Forex server — all signals go to forex channel
        target_channel = forex_channel_id

        signal_emoji = "🟢" if signal.type == "BUY" else "🔴"
        action       = signal.type.capitalize()   # "Buy" / "Sell"
        is_gold      = "XAU" in signal.pair

        # Gold: ±0.50 range so TSCopier Smart Entry picks live price
        if is_gold:
            entry_lo   = round(signal.entry_price - 0.50, 2)
            entry_hi   = round(signal.entry_price + 0.50, 2)
            entry_line = f"{action} {entry_lo} - {entry_hi}"
        else:
            entry_line = f"{action} {signal.entry_price}"

        # Regime label
        regime_emoji = {
            "UPTREND":   "📈",
            "DOWNTREND": "📉",
            "HIGH_VOL":   "⚡",
        }.get(regime_name, "📊")

        strategy_label = REGIME_STRATEGY.get(regime_name, "SWING")

        # ── Single unified message ────────────────────────────────────────
        # Top section  → TSCopier reads this (symbol, direction, entry, TP, SL)
        # Bottom section → human info (confidence, R:R, analysis, time)
        # NO HTML tags — plain text only so TSCopier never gets confused
        message = (
            f"{signal_emoji} {signal.pair} {action.upper()}\n"
            f"\n"
            f"{entry_line}\n"
            f"\n"
            f"TP1: {signal.tp_levels[0]}\n"
            f"TP2: {signal.tp_levels[1]}\n"
            f"TP3: {signal.tp_levels[2]}\n"
            f"\n"
            f"SL: {signal.sl_price}\n"
            f"\n"
            f"----------------------------\n"
            f"{regime_emoji} Regime: {regime_name} | {strategy_label}\n"
            f"R:R: 1:{signal.risk_reward} | Conf: {signal.confidence}%\n"
            f"Time: {signal.created_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Grandcom Swing EA\n"
        )

        await bot.send_message(
            chat_id=target_channel,
            text=message
            # No parse_mode — pure plain text
        )

        logger.info(f"✅ Signal sent to Telegram {target_channel}: {signal.pair} {signal.type}")
    except Exception as e:
        logger.error(f"❌ Error sending to Telegram: {e}")

# ============ AUTH ENDPOINTS ============
@api_router.post("/auth/register", response_model=Token)
async def register(user_data: UserRegister):
    existing_user = await db.users.find_one({"email": user_data.email})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
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
    access_token = create_access_token({"sub": str(user["_id"])})
    user_response = UserResponse(
        id=str(user["_id"]), email=user["email"], full_name=user["full_name"],
        subscription_tier=user["subscription_tier"], telegram_id=user["telegram_id"],
        created_at=user["created_at"], role=user.get("role", "user")
    )
    return Token(access_token=access_token, token_type="bearer", user=user_response)

@api_router.post("/auth/login", response_model=Token)
async def login(user_data: UserLogin):
    user = await db.users.find_one({"email": user_data.email})
    if not user or not verify_password(user_data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    access_token = create_access_token({"sub": str(user["_id"])})
    user_response = UserResponse(
        id=str(user["_id"]), email=user["email"], full_name=user["full_name"],
        subscription_tier=user["subscription_tier"], telegram_id=user["telegram_id"],
        created_at=user["created_at"], role=user.get("role", "user")
    )
    return Token(access_token=access_token, token_type="bearer", user=user_response)

@api_router.get("/auth/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    return UserResponse(
        id=str(current_user["_id"]), email=current_user["email"],
        full_name=current_user.get("full_name"), subscription_tier=current_user["subscription_tier"],
        telegram_id=current_user.get("telegram_id"), created_at=current_user["created_at"],
        role=current_user.get("role", "user")
    )

# ============ SIGNAL ENDPOINTS ============
@api_router.get("/signals", response_model=List[Signal])
async def get_signals(limit: int = 50, current_user: dict = Depends(get_current_user)):
    query = {}
    if current_user["subscription_tier"] == "FREE":
        query["is_premium"] = False
    signals = await db.signals.find(query).sort("created_at", -1).limit(limit).to_list(limit)
    return [Signal(id=str(s["_id"]), **{k: v for k, v in s.items() if k != "_id"}) for s in signals]

@api_router.get("/signals/history")
async def get_signals_history(limit: int = 50, pair: Optional[str] = None, result: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    try:
        query = {}
        if pair: query["pair"] = pair.upper()
        if result: query["result"] = result.upper()
        cursor = db.signals.find(query).sort("created_at", -1).limit(limit)
        signals = await cursor.to_list(length=limit)
        total = len(signals)
        wins = sum(1 for s in signals if s.get('result') == 'WIN')
        losses = sum(1 for s in signals if s.get('result') == 'LOSS')
        for signal in signals:
            signal['id'] = str(signal.pop('_id'))
        return {"signals": signals, "stats": {"total": total, "wins": wins, "losses": losses, "win_rate": round((wins / total * 100) if total > 0 else 0, 2)}}
    except Exception as e:
        logger.error(f"Error getting signals history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/signals/check-outcomes")
async def manual_check_outcomes(current_user: dict = Depends(get_current_user)):
    try:
        tracker = get_outcome_tracker()
        if not tracker:
            raise HTTPException(status_code=500, detail="Outcome tracker not initialized")
        results = await tracker.check_all_active_signals()
        return {"success": True, "message": "Outcome check completed", "results": results}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in manual outcome check: {e}")
        return {"success": False, "error": str(e)}

@api_router.get("/signals/tracker-status")
async def get_tracker_status(current_user: dict = Depends(get_current_user)):
    try:
        tracker = get_outcome_tracker()
        if not tracker:
            return {"success": True, "status": "not_initialized", "is_running": False}
        active_count = await db.signals.count_documents({"status": "ACTIVE"})
        closed_today = await db.signals.count_documents({
            "closed_at": {"$gte": datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)}
        })
        return {"success": True, "status": "running" if tracker.is_running else "stopped",
                "is_running": tracker.is_running, "active_signals": active_count,
                "closed_today": closed_today, "check_interval_seconds": 60}
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.get("/signals/active")
async def get_active_signals(current_user: dict = Depends(get_current_user)):
    try:
        active_signals = await db.signals.find(
            {"status": "ACTIVE"},
            {"pair":1,"type":1,"entry_price":1,"current_price":1,"tp_levels":1,"sl_price":1,"created_at":1,"regime":1}
        ).sort("created_at", -1).to_list(length=100)
        signals_with_status = [{"id": str(s["_id"]), "pair": s.get("pair"), "type": s.get("type"),
            "entry_price": s.get("entry_price"), "current_price": s.get("current_price"),
            "tp_levels": s.get("tp_levels",[]), "sl_price": s.get("sl_price"),
            "created_at": s.get("created_at").isoformat() if s.get("created_at") else None,
            "regime": s.get("regime","UNKNOWN")} for s in active_signals]
        return {"success": True, "count": len(signals_with_status), "signals": signals_with_status}
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.get("/signals/{signal_id}", response_model=Signal)
async def get_signal(signal_id: str, current_user: dict = Depends(get_current_user)):
    if not ObjectId.is_valid(signal_id):
        raise HTTPException(status_code=400, detail="Invalid signal ID")
    signal = await db.signals.find_one({"_id": ObjectId(signal_id)})
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    if signal.get("is_premium") and current_user["subscription_tier"] == "FREE":
        raise HTTPException(status_code=403, detail="Premium subscription required")
    return Signal(id=str(signal["_id"]), **{k: v for k, v in signal.items() if k != "_id"})

@api_router.post("/signals/generate")
async def trigger_signal_generation(background_tasks: BackgroundTasks, current_user: dict = Depends(get_current_user)):
    pairs = ["BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "EURJPY", "GBPJPY", "AUDUSD", "USDCAD", "USDCHF"]
    for pair in pairs:
        background_tasks.add_task(generate_signal_for_pair, pair)
    return {"message": "Signal generation triggered", "pairs": pairs}

# ============ ML ENGINE ENDPOINTS ============
@api_router.get("/ml/stats")
async def get_ml_stats(current_user: dict = Depends(get_current_user)):
    try:
        stats = signal_optimizer.get_performance_stats()
        return {"success": True, "stats": stats}
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.get("/ml/regime/{symbol}")
async def get_current_regime(symbol: str, current_user: dict = Depends(get_current_user)):
    try:
        df = await get_price_data(symbol, interval="1h", outputsize=100)
        if df is None or len(df) < 50:
            raise HTTPException(status_code=400, detail="Insufficient data")
        features = signal_optimizer.feature_engineer.extract_features(df, symbol)
        if not features:
            raise HTTPException(status_code=500, detail="Feature extraction failed")
        regime = signal_optimizer.regime_detector.detect_regime(features)
        return {"success": True, "symbol": symbol, "regime": regime,
                "features_summary": {"adx": features.get('adx'), "rsi": features.get('rsi'),
                "atr_ratio": features.get('atr_ratio_20'), "volatility": features.get('realized_vol_20'),
                "trend_bias": features.get('structure_bias')}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/ml/risk")
async def get_risk_status(current_user: dict = Depends(get_current_user)):
    try:
        risk_check = signal_optimizer.risk_manager.check_trading_allowed()
        risk_metrics = signal_optimizer.risk_manager.get_risk_metrics()
        return {"success": True, "trading_allowed": risk_check['allowed'],
                "restrictions": risk_check.get('restrictions',[]), "metrics": risk_metrics}
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.get("/ml/mtf/{symbol}")
async def get_mtf_analysis(symbol: str, current_user: dict = Depends(get_current_user)):
    try:
        valid_symbols = list(PAIR_PARAMETERS.keys())
        symbol = symbol.upper()
        if symbol not in valid_symbols:
            raise HTTPException(status_code=400, detail=f"Invalid symbol. Valid: {valid_symbols}")
        analysis = await mtf_analyzer.analyze(symbol)
        return {"success": True, "analysis": analysis}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/ml/mtf-all")
async def get_all_mtf_analysis(current_user: dict = Depends(get_current_user)):
    try:
        all_pairs = list(PAIR_PARAMETERS.keys())
        results = {}
        for pair in all_pairs:
            try:
                results[pair] = await mtf_analyzer.analyze(pair)
                await asyncio.sleep(2)
            except Exception as e:
                results[pair] = {"error": str(e), "valid_setup": False}
        best_setups = [{"symbol": k, **v} for k, v in results.items()
                       if v.get('valid_setup') and v.get('confluence_score', 0) >= 2]
        return {"success": True, "timestamp": datetime.utcnow().isoformat(),
                "all_pairs": results, "best_setups": best_setups, "total_valid_setups": len(best_setups)}
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.post("/ml/collect-historical")
async def collect_historical_data(background_tasks: BackgroundTasks, current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    async def run_collection():
        await historical_collector.setup_indexes()
        results = await historical_collector.collect_all_pairs()
        logger.info(f"Historical data collection complete: {results['total_records']} records")
    background_tasks.add_task(run_collection)
    return {"success": True, "message": "Historical data collection started"}

@api_router.get("/ml/data-stats")
async def get_historical_data_stats(current_user: dict = Depends(get_current_user)):
    try:
        stats = await historical_collector.get_data_stats()
        return {"success": True, "stats": stats}
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.get("/ml/signal-performance")
async def get_signal_performance(current_user: dict = Depends(get_current_user)):
    try:
        await signal_tracker.setup_indexes()
        performance = await signal_tracker.get_performance_by_regime()
        return {"success": True, "performance": performance}
    except Exception as e:
        return {"success": False, "error": str(e)}

class SignalResultUpdate(BaseModel):
    signal_id: str
    result: str
    exit_price: float
    tp_hit: Optional[int] = None

@api_router.post("/ml/update-result")
async def update_signal_result(data: SignalResultUpdate, current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        success = await signal_tracker.update_signal_result(
            signal_id=data.signal_id, result=data.result,
            exit_price=data.exit_price, tp_hit=data.tp_hit
        )
        if success:
            await db.signals.update_one(
                {"_id": ObjectId(data.signal_id)},
                {"$set": {"status": "closed", "result": data.result,
                          "exit_price": data.exit_price, "closed_at": datetime.utcnow()}}
            )
        return {"success": success}
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.get("/prices/live")
async def get_live_prices(current_user: dict = Depends(get_current_user)):
    try:
        pairs = list(PAIR_PARAMETERS.keys())
        prices = {}
        for pair in pairs:
            try:
                df = await get_price_data(pair, interval="1min", outputsize=1)
                if df is not None and len(df) > 0:
                    latest = df.iloc[-1]
                    prices[pair] = {"price": float(latest['close']), "high": float(latest['high']),
                        "low": float(latest['low']),
                        "timestamp": latest['datetime'].isoformat() if hasattr(latest['datetime'], 'isoformat') else str(latest['datetime'])}
                await asyncio.sleep(0.3)
            except Exception as e:
                prices[pair] = {"error": str(e)}
        return {"success": True, "timestamp": datetime.utcnow().isoformat(), "prices": prices}
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.get("/ml/smc/{symbol}")
async def get_smc_analysis(symbol: str, current_user: dict = Depends(get_current_user)):
    try:
        symbol = symbol.upper()
        if symbol not in PAIR_PARAMETERS:
            raise HTTPException(status_code=400, detail=f"Invalid symbol")
        df = await get_price_data(symbol, interval="1h", outputsize=100)
        if df is None or len(df) < 50:
            raise HTTPException(status_code=400, detail="Insufficient data")
        analysis = smc_analyzer.analyze(df, symbol)
        return {"success": True, "analysis": analysis}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/ml/quality-filter")
async def get_quality_filter_status(current_user: dict = Depends(get_current_user)):
    try:
        summary = signal_quality_filter.get_quality_summary()
        return {"success": True, "filter_status": summary}
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.get("/ml/full-analysis/{symbol}")
async def get_full_analysis(symbol: str, current_user: dict = Depends(get_current_user)):
    try:
        symbol = symbol.upper()
        if symbol not in PAIR_PARAMETERS:
            raise HTTPException(status_code=400, detail="Invalid symbol")
        df = await get_price_data(symbol, interval="1h", outputsize=100)
        if df is None or len(df) < 50:
            raise HTTPException(status_code=400, detail="Insufficient data")
        results = {"symbol": symbol, "timestamp": datetime.utcnow().isoformat()}
        features = signal_optimizer.feature_engineer.extract_features(df, symbol)
        if features:
            results["regime"] = signal_optimizer.regime_detector.detect_regime(features)
        results["mtf"] = await mtf_analyzer.analyze(symbol)
        results["smc"] = smc_analyzer.analyze(df, symbol)
        if results.get("regime") and results.get("mtf") and results.get("smc"):
            should_trade, reason, quality = signal_quality_filter.should_take_signal(
                symbol=symbol, signal_type=results["mtf"].get("trade_direction","NEUTRAL"),
                confidence=70, regime_result=results["regime"],
                mtf_result=results["mtf"], smc_result=results["smc"]
            )
            results["quality_assessment"] = {"should_trade": should_trade, "reason": reason,
                "quality_score": quality.get("quality_score",0),
                "checks_passed": quality.get("checks_passed",0), "checks_total": quality.get("checks_total",0)}
        return {"success": True, "analysis": serialize_numpy(results)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============ SUBSCRIPTION ENDPOINTS ============
@api_router.put("/subscription", response_model=UserResponse)
async def update_subscription(subscription: SubscriptionUpdate, current_user: dict = Depends(get_current_user)):
    await db.users.update_one({"_id": current_user["_id"]}, {"$set": {"subscription_tier": subscription.tier}})
    current_user["subscription_tier"] = subscription.tier
    return UserResponse(id=str(current_user["_id"]), email=current_user["email"],
        full_name=current_user.get("full_name"), subscription_tier=current_user["subscription_tier"],
        telegram_id=current_user.get("telegram_id"), created_at=current_user["created_at"])

# ============ STATISTICS ENDPOINTS ============
@api_router.get("/stats")
async def get_statistics(current_user: dict = Depends(get_current_user)):
    total_signals = await db.signals.count_documents({})
    active_signals = await db.signals.count_documents({"status": "ACTIVE"})
    closed_statuses = ["CLOSED_TP1","CLOSED_TP2","CLOSED_TP3","CLOSED_SL","HIT_TP","HIT_SL"]
    closed_signals = await db.signals.find({"status": {"$in": closed_statuses}}, {"result":1,"pips":1}).to_list(5000)
    wins = sum(1 for s in closed_signals if s.get("result") == "WIN")
    losses = sum(1 for s in closed_signals if s.get("result") == "LOSS")
    total_closed = wins + losses
    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0
    signals_with_pips = [s for s in closed_signals if s.get("pips") is not None]
    avg_pips = sum(s.get("pips",0) for s in signals_with_pips) / len(signals_with_pips) if signals_with_pips else 0
    return {"total_signals": total_signals, "active_signals": active_signals,
            "win_rate": round(win_rate,2), "avg_pips": round(avg_pips,2),
            "total_closed": total_closed, "wins": wins, "losses": losses}

# ============ PUSH NOTIFICATION ENDPOINTS ============
class PushTokenRegister(BaseModel):
    push_token: str
    device_type: Optional[str] = "unknown"

@api_router.post("/notifications/register")
async def register_push_token(data: PushTokenRegister, current_user: dict = Depends(get_current_user)):
    try:
        push_svc = get_push_service()
        if not push_svc:
            raise HTTPException(status_code=500, detail="Push service not initialized")
        success = await push_svc.register_push_token(user_id=str(current_user["_id"]),
            push_token=data.push_token, device_type=data.device_type)
        return {"success": success}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.delete("/notifications/unregister")
async def unregister_push_token(current_user: dict = Depends(get_current_user)):
    try:
        push_svc = get_push_service()
        if not push_svc:
            raise HTTPException(status_code=500, detail="Push service not initialized")
        success = await push_svc.unregister_push_token(str(current_user["_id"]))
        return {"success": success}
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.post("/notifications/test")
async def test_push_notification(current_user: dict = Depends(get_current_user)):
    try:
        push_svc = get_push_service()
        if not push_svc:
            raise HTTPException(status_code=500, detail="Push service not initialized")
        token_doc = await db.push_tokens.find_one({"user_id": str(current_user["_id"]), "is_active": True})
        if not token_doc:
            return {"success": False, "error": "No push token registered"}
        result = await push_svc.send_notification(push_tokens=[token_doc["push_token"]],
            title="Test Notification", body="Push notifications are working!", data={"type": "test"})
        return {"success": result["success"] > 0, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ============ PERFORMANCE CHART ENDPOINTS ============
@api_router.get("/performance/daily")
async def get_daily_performance(days: int = 30, current_user: dict = Depends(get_current_user)):
    try:
        from_date = datetime.utcnow() - timedelta(days=days)
        pipeline = [
            {"$match": {"closed_at": {"$gte": from_date}, "status": {"$in": ["CLOSED_TP1","CLOSED_TP2","CLOSED_TP3","CLOSED_SL"]}}},
            {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$closed_at"}},
                "total_trades": {"$sum":1}, "wins": {"$sum": {"$cond":[{"$eq":["$result","WIN"]},1,0]}},
                "losses": {"$sum": {"$cond":[{"$eq":["$result","LOSS"]},1,0]}},
                "total_pips": {"$sum": {"$ifNull":["$pips",0]}}}},
            {"$sort": {"_id": 1}}
        ]
        results = await db.signals.aggregate(pipeline).to_list(100)
        labels, pips_data, win_rate_data = [], [], []
        for r in results:
            labels.append(r["_id"][5:])
            pips_data.append(round(r["total_pips"],1))
            wr = (r["wins"]/r["total_trades"]*100) if r["total_trades"] > 0 else 0
            win_rate_data.append(round(wr,1))
        return {"success": True, "labels": labels, "datasets": {"pips": pips_data, "win_rate": win_rate_data}}
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.get("/performance/by-pair")
async def get_performance_by_pair(current_user: dict = Depends(get_current_user)):
    try:
        pipeline = [
            {"$match": {"status": {"$in": ["CLOSED_TP1","CLOSED_TP2","CLOSED_TP3","CLOSED_SL"]}}},
            {"$group": {"_id": "$pair", "total_trades": {"$sum":1},
                "wins": {"$sum": {"$cond":[{"$eq":["$result","WIN"]},1,0]}},
                "total_pips": {"$sum": {"$ifNull":["$pips",0]}}}},
            {"$sort": {"total_trades":-1}}
        ]
        results = await db.signals.aggregate(pipeline).to_list(20)
        formatted = [{"pair": r["_id"], "trades": r["total_trades"], "wins": r["wins"],
            "win_rate": round((r["wins"]/r["total_trades"]*100) if r["total_trades"]>0 else 0,1),
            "pips": round(r["total_pips"],1)} for r in results]
        return {"success": True, "pairs": formatted}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ============ BACKTEST ENGINE ENDPOINTS ============
BACKTEST_PAIR_METADATA = {
    "BTCUSD": {"name":"Bitcoin / US Dollar","type":"crypto"},
    "EURUSD": {"name":"Euro / US Dollar","type":"forex"},
    "GBPUSD": {"name":"British Pound / US Dollar","type":"forex"},
    "USDJPY": {"name":"US Dollar / Japanese Yen","type":"forex"},
    "EURJPY": {"name":"Euro / Japanese Yen","type":"forex"},
    "GBPJPY": {"name":"British Pound / Japanese Yen","type":"forex"},
    "AUDUSD": {"name":"Australian Dollar / US Dollar","type":"forex"},
    "USDCAD": {"name":"US Dollar / Canadian Dollar","type":"forex"},
    "USDCHF": {"name":"US Dollar / Swiss Franc","type":"forex"},
    "NZDUSD": {"name":"New Zealand Dollar / US Dollar","type":"forex"},
    "AUDJPY": {"name":"Australian Dollar / Japanese Yen","type":"forex"},
    "CADJPY": {"name":"Canadian Dollar / Japanese Yen","type":"forex"},
    "CHFJPY": {"name":"Swiss Franc / Japanese Yen","type":"forex"},
    "EURAUD": {"name":"Euro / Australian Dollar","type":"forex"},
    "GBPCAD": {"name":"British Pound / Canadian Dollar","type":"forex"},
    "EURCAD": {"name":"Euro / Canadian Dollar","type":"forex"},
    "GBPAUD": {"name":"British Pound / Australian Dollar","type":"forex"},
    "AUDNZD": {"name":"Australian Dollar / New Zealand Dollar","type":"forex"},
    "EURGBP": {"name":"Euro / British Pound","type":"forex"},
    "EURCHF": {"name":"Euro / Swiss Franc","type":"forex"},
}

class BacktestRequest(BaseModel):
    pair: str = "ALL"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    timeframe: str = "1h"
    use_pair_parameters: bool = True
    tp1_pips: Optional[float] = None
    tp2_pips: Optional[float] = None
    tp3_pips: Optional[float] = None
    sl_pips: Optional[float] = None
    use_atr_for_sl: bool = True
    atr_sl_multiplier: float = 1.5
    initial_balance: float = 10000.0
    risk_per_trade: float = 0.02
    skip_disabled: bool = True
    run_in_background: bool = False

class BacktestResponse(BaseModel):
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
    tp1_pips_used: float = 0.0
    tp2_pips_used: float = 0.0
    tp3_pips_used: float = 0.0
    sl_pips_used: float = 0.0
    atr_sl_multiplier_used: float = 0.0
    pip_value_used: float = 0.0
    monthly_performance: Dict[str, Any] = {}
    yearly_performance: Dict[str, Any] = {}
    result_id: Optional[str] = None

def _build_backtest_config_for_pair(pair, request, start_dt, end_dt):
    params = PAIR_PARAMETERS.get(pair, DEFAULT_PAIR_PARAMS)
    if request.use_pair_parameters:
        tp1 = float(params.get("fixed_tp1_pips", params.get("atr_multiplier_tp1", 5.0)))
        tp2 = float(params.get("fixed_tp2_pips", params.get("atr_multiplier_tp2", 10.0)))
        tp3 = float(params.get("fixed_tp3_pips", params.get("atr_multiplier_tp3", 15.0)))
        sl  = float(params.get("fixed_sl_pips",  params.get("atr_multiplier_sl",  15.0)))
        use_atr = not params.get("use_fixed_pips", False)
        atr_mult = float(params.get("atr_multiplier_sl", request.atr_sl_multiplier))
    else:
        tp1 = request.tp1_pips or 5.0; tp2 = request.tp2_pips or 10.0
        tp3 = request.tp3_pips or 15.0; sl = request.sl_pips or 15.0
        use_atr = request.use_atr_for_sl; atr_mult = request.atr_sl_multiplier
    return BacktestConfig(pair=pair, start_date=start_dt, end_date=end_dt, timeframe=request.timeframe,
        initial_balance=request.initial_balance, risk_per_trade=request.risk_per_trade,
        tp1_pips=tp1, tp2_pips=tp2, tp3_pips=tp3, sl_pips=sl,
        use_atr_for_sl=use_atr, atr_sl_multiplier=atr_mult)

def _result_to_response(pair, results, config, result_id=None):
    params = PAIR_PARAMETERS.get(pair, DEFAULT_PAIR_PARAMS)
    pip_value = float(params.get("pip_value", 0.0001))
    return BacktestResponse(pair=pair, enabled=params.get("enabled",True),
        total_trades=results.total_trades, winning_trades=results.winning_trades,
        losing_trades=results.losing_trades, win_rate=round(results.win_rate,2),
        total_pips=round(results.total_pips,1), profit_factor=round(results.profit_factor,2),
        sharpe_ratio=round(results.sharpe_ratio,2), max_drawdown_percent=round(results.max_drawdown_percent,2),
        return_percent=round(results.return_percent,2),
        max_consecutive_wins=results.max_consecutive_wins,
        max_consecutive_losses=results.max_consecutive_losses,
        tp1_pips_used=config.tp1_pips, tp2_pips_used=config.tp2_pips,
        tp3_pips_used=config.tp3_pips, sl_pips_used=config.sl_pips,
        atr_sl_multiplier_used=config.atr_sl_multiplier, pip_value_used=pip_value,
        monthly_performance=results.monthly_performance,
        yearly_performance=results.yearly_performance, result_id=result_id)

async def _run_single_pair_backtest(pair, request, start_dt, end_dt, engine, user_id):
    params = PAIR_PARAMETERS.get(pair, DEFAULT_PAIR_PARAMS)
    is_enabled = params.get("enabled", True)
    if request.skip_disabled and not is_enabled:
        return BacktestResponse(pair=pair, enabled=False, skipped=True,
            skip_reason=f"{pair} is disabled in PAIR_PARAMETERS")
    config = _build_backtest_config_for_pair(pair, request, start_dt, end_dt)
    results = await engine.run_backtest(config)
    pip_value = float(params.get("pip_value", 0.0001))
    result_doc = {"user_id": user_id, "pair": pair, "enabled": is_enabled,
        "timeframe": config.timeframe, "start_date": start_dt.isoformat(), "end_date": end_dt.isoformat(),
        "filters_applied": {"use_pair_parameters": request.use_pair_parameters,
            "skip_disabled": request.skip_disabled, "allowed_regimes": ALLOWED_REGIMES,
            "min_confidence_threshold": MIN_CONFIDENCE_THRESHOLD,
            "drawdown_protection": DRAWDOWN_PROTECTION,
            "gatekeeper": {"min_rr": _gatekeeper.min_rr,
                           "max_signal_age_s": _gatekeeper.max_signal_age,
                           "max_slippage": f"per-asset (FOREX=2, JPY=3, GOLD=10 pips)",
                           "max_spread": f"per-asset (FOREX=2, JPY=3, GOLD=30 pips)",
                           "block_range_markets": True}},
        "config": {"tp1_pips": config.tp1_pips, "tp2_pips": config.tp2_pips,
            "tp3_pips": config.tp3_pips, "sl_pips": config.sl_pips,
            "use_atr_for_sl": config.use_atr_for_sl, "atr_sl_multiplier": config.atr_sl_multiplier,
            "pip_value": pip_value, "initial_balance": config.initial_balance,
            "risk_per_trade": config.risk_per_trade},
        "results": results.to_dict(), "created_at": datetime.utcnow()}
    insert_result = await db.backtest_results.insert_one(result_doc)
    return _result_to_response(pair, results, config, str(insert_result.inserted_id))

async def _run_all_pairs_backtest_bg(request, start_dt, end_dt, engine, user_id, job_id):
    pairs_to_run = list(PAIR_PARAMETERS.keys())
    responses = []
    await db.backtest_jobs.update_one({"_id": ObjectId(job_id)},
        {"$set": {"status": "running", "total_pairs": len(pairs_to_run)}})
    for idx, pair in enumerate(pairs_to_run):
        try:
            resp = await _run_single_pair_backtest(pair, request, start_dt, end_dt, engine, user_id)
            responses.append(resp.dict())
            await db.backtest_jobs.update_one({"_id": ObjectId(job_id)},
                {"$set": {"pairs_completed": idx+1, f"pair_results.{pair}": resp.dict()}})
        except Exception as exc:
            responses.append({"pair": pair, "skipped": True, "skip_reason": str(exc)})
        await asyncio.sleep(2)
    completed = [r for r in responses if not r.get("skipped")]
    summary = {"total_pairs_run": len(completed), "total_pairs_skipped": len(responses)-len(completed),
        "avg_win_rate": round(sum(r["win_rate"] for r in completed)/len(completed),2) if completed else 0,
        "avg_profit_factor": round(sum(r["profit_factor"] for r in completed)/len(completed),2) if completed else 0,
        "total_pips_all_pairs": round(sum(r["total_pips"] for r in completed),1),
        "best_pair": max(completed, key=lambda r: r["profit_factor"])["pair"] if completed else None,
        "worst_pair": min(completed, key=lambda r: r["profit_factor"])["pair"] if completed else None}
    await db.backtest_jobs.update_one({"_id": ObjectId(job_id)},
        {"$set": {"status": "completed", "summary": summary, "completed_at": datetime.utcnow()}})

@api_router.post("/backtest/run")
async def run_backtest(request: BacktestRequest, background_tasks: BackgroundTasks, current_user: dict = Depends(get_current_user)):
    try:
        engine = get_backtest_engine()
        if not engine:
            raise HTTPException(status_code=500, detail="Backtest engine not initialized")
        now = datetime.utcnow()
        end_dt = datetime.fromisoformat(request.end_date) if request.end_date else now
        start_dt = datetime.fromisoformat(request.start_date) if request.start_date else now - timedelta(days=730)
        if start_dt >= end_dt:
            raise HTTPException(status_code=400, detail="start_date must be before end_date")
        date_span_days = (end_dt - start_dt).days
        if date_span_days < 30:
            raise HTTPException(status_code=400, detail="Date range must be at least 30 days")
        user_id = str(current_user["_id"])
        if request.pair.upper() == "ALL":
            if request.run_in_background:
                job_doc = {"user_id": user_id, "type": "all_pairs_backtest", "status": "queued",
                    "start_date": start_dt.isoformat(), "end_date": end_dt.isoformat(),
                    "timeframe": request.timeframe, "pairs_completed": 0,
                    "total_pairs": len(PAIR_PARAMETERS), "pair_results": {}, "created_at": datetime.utcnow()}
                job_insert = await db.backtest_jobs.insert_one(job_doc)
                job_id = str(job_insert.inserted_id)
                background_tasks.add_task(_run_all_pairs_backtest_bg, request, start_dt, end_dt, engine, user_id, job_id)
                return {"success": True, "mode": "background", "job_id": job_id,
                    "message": f"Backtest job queued for {len(PAIR_PARAMETERS)} pairs",
                    "pairs_queued": list(PAIR_PARAMETERS.keys())}
            else:
                all_responses = []
                for pair in PAIR_PARAMETERS.keys():
                    try:
                        resp = await _run_single_pair_backtest(pair, request, start_dt, end_dt, engine, user_id)
                        all_responses.append(resp.dict())
                    except Exception as exc:
                        all_responses.append({"pair": pair, "skipped": True, "skip_reason": str(exc)})
                    await asyncio.sleep(1)
                completed = [r for r in all_responses if not r.get("skipped")]
                summary = {"total_pairs_run": len(completed), "avg_win_rate": round(sum(r["win_rate"] for r in completed)/len(completed),2) if completed else 0,
                    "best_pair": max(completed, key=lambda r: r["profit_factor"])["pair"] if completed else None}
                return {"success": True, "mode": "foreground", "summary": summary, "results": all_responses}
        pair = request.pair.upper()
        if pair not in PAIR_PARAMETERS:
            raise HTTPException(status_code=400, detail=f"Unknown pair '{pair}'")
        resp = await _run_single_pair_backtest(pair, request, start_dt, end_dt, engine, user_id)
        return {"success": True, "mode": "single", "pair": pair, "result": resp.dict()}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.get("/backtest/results/{pair}")
async def get_backtest_results_for_pair(pair: str, limit: int = 5, current_user: dict = Depends(get_current_user)):
    try:
        pair = pair.upper()
        query = {"user_id": str(current_user["_id"])}
        if pair != "ALL": query["pair"] = pair
        docs = await db.backtest_results.find(query).sort("created_at",-1).limit(limit).to_list(limit)
        formatted = [{"id": str(d["_id"]), "pair": d.get("pair"), "enabled": d.get("enabled",True),
            "timeframe": d.get("timeframe"), "start_date": d.get("start_date"), "end_date": d.get("end_date"),
            "filters_applied": d.get("filters_applied",{}), "config": d.get("config",{}),
            "summary": d.get("results",{}).get("summary",{}),
            "monthly_performance": d.get("results",{}).get("monthly_performance",{}),
            "yearly_performance": d.get("results",{}).get("yearly_performance",{}),
            "created_at": d["created_at"].isoformat() if d.get("created_at") else None} for d in docs]
        return {"success": True, "pair": pair, "count": len(formatted), "results": formatted}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.get("/backtest/summary")
async def get_backtest_summary(current_user: dict = Depends(get_current_user)):
    try:
        user_id = str(current_user["_id"])
        summary_rows = []
        for pair in PAIR_PARAMETERS.keys():
            params = PAIR_PARAMETERS[pair]
            is_enabled = params.get("enabled", True)
            doc = await db.backtest_results.find_one({"user_id": user_id, "pair": pair}, sort=[("created_at",-1)])
            if doc is None:
                summary_rows.append({"pair": pair, "enabled": is_enabled, "has_results": False,
                    "pair_type": BACKTEST_PAIR_METADATA.get(pair,{}).get("type","forex")})
                continue
            s = doc.get("results",{}).get("summary",{})
            cfg = doc.get("config",{})
            summary_rows.append({"pair": pair, "enabled": is_enabled, "has_results": True,
                "pair_type": BACKTEST_PAIR_METADATA.get(pair,{}).get("type","forex"),
                "result_id": str(doc["_id"]),
                "backtest_date": doc["created_at"].isoformat() if doc.get("created_at") else None,
                "total_trades": s.get("total_trades",0), "win_rate": s.get("win_rate",0),
                "profit_factor": s.get("profit_factor",0), "total_pips": s.get("total_pips",0),
                "return_percent": s.get("return_percent",0)})
        summary_rows.sort(key=lambda r: (0 if r.get("enabled") else 1, -(r.get("profit_factor") or 0)))
        with_results = [r for r in summary_rows if r.get("has_results")]
        aggregate = {}
        if with_results:
            aggregate = {"pairs_with_results": len(with_results),
                "avg_win_rate": round(sum(r["win_rate"] for r in with_results)/len(with_results),2),
                "avg_profit_factor": round(sum(r["profit_factor"] for r in with_results)/len(with_results),2),
                "best_pair_by_pf": max(with_results, key=lambda r: r["profit_factor"])["pair"]}
        return {"success": True, "total_pairs_configured": len(PAIR_PARAMETERS),
            "gatekeeper_settings": {"min_rr": _gatekeeper.min_rr,
                "max_signal_age_s": _gatekeeper.max_signal_age,
                "block_range_markets": True,
                "max_open_trades": _gatekeeper.max_open_trades},
            "aggregate": aggregate, "pairs": summary_rows}
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.get("/backtest/job/{job_id}")
async def get_backtest_job_status(job_id: str, current_user: dict = Depends(get_current_user)):
    try:
        if not ObjectId.is_valid(job_id):
            raise HTTPException(status_code=400, detail="Invalid job_id")
        job = await db.backtest_jobs.find_one({"_id": ObjectId(job_id), "user_id": str(current_user["_id"])})
        if not job:
            raise HTTPException(status_code=404, detail="Backtest job not found")
        return {"success": True, "job_id": job_id, "status": job.get("status","unknown"),
            "pairs_completed": job.get("pairs_completed",0), "total_pairs": job.get("total_pairs",0),
            "progress_pct": round(job.get("pairs_completed",0)/max(job.get("total_pairs",1),1)*100,1),
            "summary": job.get("summary"),
            "created_at": job["created_at"].isoformat() if job.get("created_at") else None,
            "completed_at": job["completed_at"].isoformat() if job.get("completed_at") else None}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.get("/backtest/history")
async def get_backtest_history(limit: int = 20, pair: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    try:
        query = {"user_id": str(current_user["_id"])}
        if pair: query["pair"] = pair.upper()
        history = await db.backtest_results.find(query).sort("created_at",-1).limit(limit).to_list(limit)
        formatted = [{"id": str(item["_id"]), "pair": item.get("pair"), "enabled": item.get("enabled",True),
            "timeframe": item.get("timeframe"), "start_date": item.get("start_date"), "end_date": item.get("end_date"),
            "config": item.get("config",{}), "summary": item.get("results",{}).get("summary",{}),
            "created_at": item["created_at"].isoformat() if item.get("created_at") else None} for item in history]
        return {"success": True, "count": len(formatted), "history": formatted}
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.get("/backtest/result/{result_id}")
async def get_backtest_result(result_id: str, current_user: dict = Depends(get_current_user)):
    try:
        if not ObjectId.is_valid(result_id):
            raise HTTPException(status_code=400, detail="Invalid result_id")
        result = await db.backtest_results.find_one({"_id": ObjectId(result_id), "user_id": str(current_user["_id"])})
        if not result:
            raise HTTPException(status_code=404, detail="Backtest result not found")
        return {"success": True, "result": {"id": str(result["_id"]), "pair": result.get("pair"),
            "enabled": result.get("enabled",True), "timeframe": result.get("timeframe"),
            "start_date": result.get("start_date"), "end_date": result.get("end_date"),
            "filters_applied": result.get("filters_applied",{}), "config": result.get("config",{}),
            "results": result.get("results",{}),
            "created_at": result["created_at"].isoformat() if result.get("created_at") else None}}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.get("/backtest/pairs")
async def get_available_pairs(current_user: dict = Depends(get_current_user)):
    pairs_out = []
    for symbol, meta in BACKTEST_PAIR_METADATA.items():
        params = PAIR_PARAMETERS.get(symbol, DEFAULT_PAIR_PARAMS)
        pairs_out.append({"symbol": symbol, "name": meta["name"], "type": meta["type"],
            "enabled": params.get("enabled",True), "pip_value": params.get("pip_value",0.0001),
            "decimal_places": params.get("decimal_places",5),
            "tp1_pips": params.get("fixed_tp1_pips", params.get("atr_multiplier_tp1",5.0)),
            "tp2_pips": params.get("fixed_tp2_pips", params.get("atr_multiplier_tp2",10.0)),
            "tp3_pips": params.get("fixed_tp3_pips", params.get("atr_multiplier_tp3",15.0)),
            "sl_pips": params.get("fixed_sl_pips", params.get("atr_multiplier_sl",15.0)),
            "use_fixed_pips": params.get("use_fixed_pips",False),
            "atr_sl_multiplier": params.get("atr_multiplier_sl",1.5), "min_rr": params.get("min_rr",1.5)})
    pairs_out.sort(key=lambda p: (0 if p["enabled"] else 1, p["symbol"]))
    return {"success": True, "total_pairs": len(pairs_out),
        "active_pairs": sum(1 for p in pairs_out if p["enabled"]),
        "gatekeeper_active": True,
        "gatekeeper_settings": {"min_rr": _gatekeeper.min_rr,
            "max_signal_age_s": _gatekeeper.max_signal_age,
            "block_range_markets": True,
            "max_open_trades": _gatekeeper.max_open_trades},
        "pairs": pairs_out}

# ============ ADMIN ENDPOINTS ============
def require_admin(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user

@api_router.get("/admin/users")
async def get_all_users(admin_user: dict = Depends(require_admin)):
    try:
        users = await db.users.find({}).to_list(1000)
        formatted = [{"id": str(u["_id"]), "email": u.get("email"), "role": u.get("role","user"),
            "created_at": u.get("created_at").isoformat() if u.get("created_at") else None,
            "subscription_status": u.get("subscription_status","free")} for u in users]
        return {"success": True, "users": formatted}
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.post("/admin/signals/{signal_id}/close")
async def admin_close_signal(signal_id: str, data: dict, admin_user: dict = Depends(require_admin)):
    try:
        status = data.get("status","CLOSED_MANUAL")
        result = "WIN" if "WIN" in status else "LOSS"
        update_result = await db.signals.update_one({"_id": ObjectId(signal_id)},
            {"$set": {"status": status, "result": result, "closed_at": datetime.utcnow(), "closed_by": "admin"}})
        if update_result.modified_count > 0:
            return {"success": True, "message": "Signal closed"}
        return {"success": False, "error": "Signal not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.delete("/admin/signals/{signal_id}")
async def admin_delete_signal(signal_id: str, admin_user: dict = Depends(require_admin)):
    try:
        delete_result = await db.signals.delete_one({"_id": ObjectId(signal_id)})
        if delete_result.deleted_count > 0:
            return {"success": True, "message": "Signal deleted"}
        return {"success": False, "error": "Signal not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}

class ManualSignalRequest(BaseModel):
    pair: str
    type: str
    entry_price: float
    tp1: float; tp2: float; tp3: float; sl: float
    send_telegram: bool = True

@api_router.post("/admin/signals/create")
async def admin_create_signal(signal: ManualSignalRequest, admin_user: dict = Depends(require_admin)):
    try:
        if signal.pair not in PAIR_PARAMETERS:
            return {"success": False, "error": f"Invalid pair"}
        if signal.type not in ["BUY","SELL"]:
            return {"success": False, "error": "Type must be BUY or SELL"}

        # ── Gatekeeper check for manual signals too ──
        active_docs = await db.signals.find(
            {"status": "ACTIVE"}, {"pair": 1, "type": 1}
        ).to_list(length=200)
        open_trades_list = [{"symbol": d["pair"], "side": d["type"]} for d in active_docs]
        params      = PAIR_PARAMETERS.get(signal.pair, DEFAULT_PAIR_PARAMS)
        spread_pips = _gatekeeper.price_to_pips(params.get("typical_spread", 0.0002), signal.pair)
        gk_approved, gk_code, gk_reason = run_execution_gatekeeper(
            pair          = signal.pair,
            signal_type   = signal.type,
            entry_price   = signal.entry_price,
            tp1           = signal.tp3,          # furthest TP for R:R
            sl_price      = signal.sl,
            current_price = signal.entry_price,  # admin entry = current price
            spread_pips   = spread_pips,
            ema50         = 0.0,                 # not available for manual entry
            signal_ts_iso = datetime.now(_tz.utc).isoformat(),
            open_trades   = open_trades_list,
            confidence    = float(signal.entry_price * 0 + 100),  # admin = full confidence
        )
        if not gk_approved:
            return {"success": False, "gatekeeper_rejected": True,
                    "reason_code": gk_code, "reason": gk_reason}

        signal_doc = {"pair": signal.pair, "type": signal.type, "entry_price": signal.entry_price,
            "tp_levels": [signal.tp1, signal.tp2, signal.tp3], "sl_price": signal.sl,
            "status": "ACTIVE", "created_at": datetime.utcnow(), "created_by": "admin_manual",
            "regime": "MANUAL", "confidence": 100.0, "ml_optimized": False}
        result = await db.signals.insert_one(signal_doc)
        signal_id = str(result.inserted_id)

        if signal.send_telegram:
            try:
                message = f"🎯 *MANUAL SIGNAL*\n\n📊 *{signal.pair}* - *{signal.type}*\n💰 Entry: {signal.entry_price}\n🎯 TP1: {signal.tp1} | TP2: {signal.tp2} | TP3: {signal.tp3}\n🛡️ SL: {signal.sl}\n\n✅ Gatekeeper Approved\n⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
                is_gold_pair = "XAU" in signal.pair
                telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
                telegram_channel = os.environ.get(
                    "TELEGRAM_GOLD_CHANNEL_ID" if is_gold_pair else "TELEGRAM_CHANNEL_ID",
                    "@grandcomgold" if is_gold_pair else "@grandcomsignals"
                )
                if telegram_token:
                    import httpx
                    async with httpx.AsyncClient() as client:
                        await client.post(f"https://api.telegram.org/bot{telegram_token}/sendMessage",
                            json={"chat_id": telegram_channel, "text": message, "parse_mode": "Markdown"})
            except Exception as tg_error:
                logger.error(f"Failed to send to Telegram: {tg_error}")

        return {"success": True, "signal_id": signal_id, "message": f"Signal created for {signal.pair} {signal.type}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

class UserUpdateRequest(BaseModel):
    role: Optional[str] = None
    subscription_tier: Optional[str] = None

@api_router.put("/admin/users/{user_id}")
async def admin_update_user(user_id: str, update: UserUpdateRequest, admin_user: dict = Depends(require_admin)):
    try:
        update_data = {}
        if update.role:
            if update.role not in ["user","admin","premium"]:
                return {"success": False, "error": "Invalid role"}
            update_data["role"] = update.role
        if update.subscription_tier:
            if update.subscription_tier not in ["free","pro","premium"]:
                return {"success": False, "error": "Invalid tier"}
            update_data["subscription_tier"] = update.subscription_tier.upper()
            update_data["subscription_status"] = "active" if update.subscription_tier != "free" else "free"
        if not update_data:
            return {"success": False, "error": "No update fields provided"}
        result = await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update_data})
        if result.modified_count > 0:
            return {"success": True, "message": "User updated"}
        return {"success": False, "error": "User not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.delete("/admin/users/{user_id}")
async def admin_delete_user(user_id: str, admin_user: dict = Depends(require_admin)):
    try:
        user = await db.users.find_one({"_id": ObjectId(user_id)})
        if user and user.get("role") == "admin":
            return {"success": False, "error": "Cannot delete admin user"}
        result = await db.users.delete_one({"_id": ObjectId(user_id)})
        if result.deleted_count > 0:
            return {"success": True, "message": "User deleted"}
        return {"success": False, "error": "User not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.get("/admin/pair-config")
async def get_pair_config(admin_user: dict = Depends(require_admin)):
    return {"success": True, "pairs": PAIR_PARAMETERS, "valid_pairs": list(PAIR_PARAMETERS.keys())}

@api_router.get("/admin/filters")
async def get_profitability_filters(admin_user: dict = Depends(require_admin)):
    return {"success": True, "filters": {
        "regime_filter": {"allowed_regimes": ALLOWED_REGIMES, "skip_regimes": SKIP_REGIME},
        "confidence_filter": {"min_ai_confidence": MIN_CONFIDENCE_THRESHOLD, "min_regime_confidence": MIN_REGIME_CONFIDENCE},
        "session_filter": {"pairs": SESSION_FILTERS, "current_hour_utc": datetime.utcnow().hour},
        "drawdown_protection": {**DRAWDOWN_PROTECTION, "current_status": daily_pair_performance},
        "execution_gatekeeper": {"min_rr_ratio": _gatekeeper.min_rr,
            "max_signal_age_seconds": _gatekeeper.max_signal_age,
            "max_slippage": f"per-asset (FOREX=2, JPY=3, GOLD=10 pips)", "max_spread": f"per-asset (FOREX=2, JPY=3, GOLD=30 pips)",
            "ema50_proximity_pct": f"per-asset (FOREX=10, JPY=15, GOLD=50 pips)",
            "max_open_trades": _gatekeeper.max_open_trades,
            "block_range_markets": True,
            "log_file": _GK_LOG_FILE,
            "description": "Validates every signal before execution. Env-var overridable."}
    }}

@api_router.get("/admin/filter-stats")
async def get_filter_statistics(admin_user: dict = Depends(require_admin)):
    recent_signals = []
    async for signal in db.signals.find({"created_at": {"$gte": datetime.utcnow()-timedelta(hours=24)}}).sort("created_at",-1).limit(100):
        signal['id'] = str(signal.pop('_id'))
        recent_signals.append(signal)
    regime_counts = {}
    for signal in recent_signals:
        regime = signal.get('regime','UNKNOWN')
        if regime not in regime_counts:
            regime_counts[regime] = {'total':0,'wins':0,'losses':0}
        regime_counts[regime]['total'] += 1
        if signal.get('result') == 'WIN': regime_counts[regime]['wins'] += 1
        elif signal.get('result') == 'LOSS': regime_counts[regime]['losses'] += 1
    return {"success": True,
        "last_24h": {"total_signals": len(recent_signals), "regime_distribution": regime_counts},
        "gatekeeper_log": _GK_LOG_FILE}

@api_router.post("/admin/ml/optimize")
async def run_ml_optimization(admin_user: dict = Depends(require_admin)):
    try:
        from ml_engine.model_trainer import run_model_optimization
        results = await run_model_optimization(db)
        return results
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.get("/admin/ml/performance")
async def get_ml_performance_analysis(admin_user: dict = Depends(require_admin)):
    try:
        from ml_engine.model_trainer import SignalOptimizationEngine
        signals = []
        async for signal in db.signals.find({'result':{'$in':['WIN','LOSS']}}).sort('created_at',-1).limit(500):
            signal['id'] = str(signal.pop('_id'))
            signals.append(signal)
        if len(signals) < 10:
            return {"success": True, "message": "Not enough data yet", "signals_analyzed": len(signals)}
        optimizer = SignalOptimizationEngine()
        pair_analysis = optimizer.analyze_performance_by_pair(signals)
        regime_analysis = optimizer.analyze_performance_by_regime(signals)
        recommendations = optimizer.recommend_pair_settings(pair_analysis)
        sorted_pairs = sorted(pair_analysis.items(), key=lambda x: x[1].get('win_rate',0), reverse=True)
        return {"success": True, "signals_analyzed": len(signals),
            "pair_rankings": [{"pair": p, "win_rate": round(s.get('win_rate',0),2),
                "profit_factor": round(s.get('profit_factor',0),2), "total_trades": s.get('total',0),
                "total_pips": round(s.get('total_pips',0),1)} for p,s in sorted_pairs],
            "regime_performance": {r: {"win_rate": round(s.get('win_rate',0),2), "total_trades": s.get('total',0)}
                for r,s in regime_analysis.items()},
            "recommendations": recommendations}
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.get("/admin/system-config")
async def get_system_config(admin_user: dict = Depends(require_admin)):
    tracker = get_outcome_tracker()
    active_pairs = [p for p,c in PAIR_PARAMETERS.items() if c.get('enabled',True)]
    disabled_pairs = [p for p,c in PAIR_PARAMETERS.items() if not c.get('enabled',True)]
    return {"success": True, "config": {
        "signal_generation": {"interval_minutes":15, "total_pairs":len(PAIR_PARAMETERS),
            "active_pairs":len(active_pairs), "active_pairs_list":active_pairs, "disabled_pairs":disabled_pairs},
        "execution_gatekeeper": {"enabled":True,
            "min_rr_ratio": _gatekeeper.min_rr,
            "max_signal_age_seconds": _gatekeeper.max_signal_age,
            "max_slippage": f"per-asset (FOREX=2, JPY=3, GOLD=10 pips)",
            "max_spread": f"per-asset (FOREX=2, JPY=3, GOLD=30 pips)",
            "ema50_proximity_pct": f"per-asset (FOREX=10, JPY=15, GOLD=50 pips)",
            "max_open_trades": _gatekeeper.max_open_trades,
            "block_range_markets": True,
            "override_via_env_vars": ["GK_MIN_RR_RATIO","GK_MAX_SIGNAL_AGE_S",
                "GK_MAX_SLIPPAGE","GK_MAX_SPREAD","GK_EMA50_PROXIMITY_PCT",
                "GK_MAX_OPEN_TRADES","GK_BLOCK_RANGE_MARKETS","GK_LOG_FILE"]},
        "outcome_tracker": {"status": "running" if tracker and tracker.is_running else "stopped"}
    }}

# ============ STRIPE SUBSCRIPTION ENDPOINTS ============
class CreateCheckoutRequest(BaseModel):
    package_id: str

@api_router.post("/subscriptions/create-checkout-session")
async def create_checkout_session(request: CreateCheckoutRequest, current_user: dict = Depends(get_current_user)):
    try:
        sub_service = get_subscription_service()
        if not sub_service:
            raise HTTPException(status_code=500, detail="Subscription service not available")
        origin_url = os.environ.get('FRONTEND_URL', os.environ.get('EXPO_PUBLIC_BACKEND_URL',''))
        result = await sub_service.create_checkout_session(user_id=str(current_user["_id"]),
            package_id=request.package_id, origin_url=origin_url)
        return result
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.get("/subscriptions/packages")
async def get_subscription_packages():
    return {"success": True, "packages": SUBSCRIPTION_PACKAGES, "tier_features": TIER_FEATURES}

@api_router.get("/subscriptions/current")
async def get_current_subscription(current_user: dict = Depends(get_current_user)):
    try:
        sub_service = get_subscription_service()
        if not sub_service:
            return {"success": True, "tier": current_user.get("subscription_tier","FREE"),
                "features": TIER_FEATURES.get(current_user.get("subscription_tier","FREE").lower(), TIER_FEATURES["free"])}
        subscription = await sub_service.get_user_subscription(str(current_user["_id"]))
        return {"success": True, **subscription}
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.get("/subscriptions/verify/{session_id}")
async def verify_subscription_payment(session_id: str, current_user: dict = Depends(get_current_user)):
    try:
        sub_service = get_subscription_service()
        if not sub_service:
            raise HTTPException(status_code=500, detail="Subscription service not available")
        return await sub_service.verify_payment(session_id)
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "error": str(e)}

@api_router.post("/subscriptions/cancel")
async def cancel_subscription(current_user: dict = Depends(get_current_user)):
    try:
        sub_service = get_subscription_service()
        if not sub_service:
            raise HTTPException(status_code=500, detail="Subscription service not available")
        return await sub_service.cancel_subscription(str(current_user["_id"]))
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "error": str(e)}

from fastapi import Request

@app.post("/api/webhook/stripe")
async def stripe_webhook(request: Request):
    try:
        payload = await request.body()
        import json
        event = json.loads(payload)
        event_type = event.get('type','')
        if event_type == 'checkout.session.completed':
            session = event.get('data',{}).get('object',{})
            session_id = session.get('id')
            if session_id:
                sub_service = get_subscription_service()
                if sub_service:
                    await sub_service.verify_payment(session_id)
        return {"received": True}
    except Exception as e:
        return {"received": True, "error": str(e)}

# ============ BACKGROUND TASKS ============
async def auto_generate_signals():
    """Background task to auto-generate signals every 15 minutes"""
    # Forex pairs only — Gold handled by gold_server.py
    active_pairs = [pair for pair, config in PAIR_PARAMETERS.items() if config.get('enabled', True)]
    logger.info(f"Active trading pairs: {active_pairs}")
    while True:
        try:
            logger.info("Starting automatic signal generation...")
            for pair in active_pairs:
                await generate_signal_for_pair(pair)
                await asyncio.sleep(10)
            logger.info("Signal generation completed")
            await asyncio.sleep(900)
        except Exception as e:
            logger.error(f"Error in auto signal generation: {e}")
            await asyncio.sleep(60)

# ============ APP SETUP ============
app.include_router(api_router)
app.add_middleware(CORSMiddleware, allow_credentials=True, allow_origins=["*"],
    allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
async def startup_event():
    logger.info("Starting Forex & Gold Signals API v2 + Safe Execution Mode...")
    tracker = init_outcome_tracker(db=db, twelve_data_api_key=TWELVE_DATA_API_KEY,
        telegram_bot_token=TELEGRAM_BOT_TOKEN,
        telegram_channel_id=os.environ.get('TELEGRAM_CHANNEL_ID','@grandcomsignals'))  # Forex channel
    tracker.start(interval_seconds=60)
    logger.info("Signal Outcome Tracker started")
    init_push_service(db)
    logger.info("Push Notification Service initialized")
    init_backtest_engine(TWELVE_DATA_API_KEY, db)
    logger.info("Backtest Engine initialized")
    if STRIPE_API_KEY:
        init_subscription_service(db, STRIPE_API_KEY)
        logger.info("Subscription Service initialized")
    logger.info(
        f"✅ Execution Gatekeeper active — "
        f"min R:R={_gatekeeper.min_rr} | "
        f"min conf={_gatekeeper.min_confidence}% | "
        f"max age=GOLD:10s/JPY:6s/FX:{_gatekeeper.max_signal_age}s | "
        f"max open trades={_gatekeeper.max_open_trades} | "
        f"session=London+NY | news filter=placeholder"
    )
    asyncio.create_task(auto_generate_signals())

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()

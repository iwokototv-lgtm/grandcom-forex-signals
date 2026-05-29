"""
Grandcom Gold Signals v3.0 — Centralized Configuration
All environment variables and system constants in one place.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Core Infrastructure
# ---------------------------------------------------------------------------
MONGO_URL: str = os.environ.get("MONGO_URL", "")
DB_NAME: str = os.environ.get("DB_NAME", "gold_signals_v3")
REDIS_URL: str = os.environ.get("REDIS_URL", "")

# ---------------------------------------------------------------------------
# API Keys
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TWELVE_DATA_API_KEY: str = os.environ.get("TWELVE_DATA_API_KEY", "")
OPENAI_API_KEY: str = (
    os.environ.get("OPENAI_API_KEY") or os.environ.get("EMERGENT_LLM_KEY", "")
)

# ---------------------------------------------------------------------------
# Telegram Channels
# ---------------------------------------------------------------------------
_raw_channel = os.environ.get("TELEGRAM_GOLD_CHANNEL_ID", "")
try:
    TELEGRAM_CHANNEL_ID: int | str = int(_raw_channel)
except (ValueError, TypeError):
    TELEGRAM_CHANNEL_ID = _raw_channel or ""

# ---------------------------------------------------------------------------
# Trading Pairs
# ---------------------------------------------------------------------------
PAIRS: dict = {
    "XAUUSD": {
        "symbol": "XAU/USD",
        "decimals": 2,
        "atr_sl": 1.5,
        "atr_tp1": 2.0,
        "atr_tp2": 3.5,
        "atr_tp3": 5.0,
        "pip_value": 0.01,
        "min_lot": 0.01,
        "max_lot": 10.0,
    },
    "XAUEUR": {
        "symbol": "XAU/EUR",
        "decimals": 2,
        "atr_sl": 1.5,
        "atr_tp1": 2.0,
        "atr_tp2": 3.5,
        "atr_tp3": 5.0,
        "pip_value": 0.01,
        "min_lot": 0.01,
        "max_lot": 10.0,
    },
}

# ---------------------------------------------------------------------------
# Signal Generation
# ---------------------------------------------------------------------------
SIGNAL_INTERVAL_MINUTES: int = int(os.environ.get("SIGNAL_INTERVAL_MINUTES", "30"))
MIN_CONFIDENCE: float = float(os.environ.get("MIN_CONFIDENCE", "65"))
MAX_SIGNALS_PER_DAY: int = int(os.environ.get("MAX_SIGNALS_PER_DAY", "8"))
MIN_SIGNAL_GAP_MINUTES: int = int(os.environ.get("MIN_SIGNAL_GAP_MINUTES", "30"))

# ---------------------------------------------------------------------------
# Multi-Timeframe Settings
# ---------------------------------------------------------------------------
MTF_TIMEFRAMES: list = ["1h", "4h", "1day", "1week"]
MTF_WEIGHTS: dict = {
    "1h": 0.15,
    "4h": 0.35,
    "1day": 0.35,
    "1week": 0.15,
}
MTF_MIN_CONFLUENCE: int = int(os.environ.get("MTF_MIN_CONFLUENCE", "3"))

# ---------------------------------------------------------------------------
# Regime Detection
# ---------------------------------------------------------------------------
REGIME_HYSTERESIS: int = 3          # Consecutive predictions before regime change
REGIME_CONFIDENCE_MIN: float = 0.60  # Minimum regime confidence to act

# ---------------------------------------------------------------------------
# Risk Management
# ---------------------------------------------------------------------------
BASE_RISK_PER_TRADE: float = float(os.environ.get("BASE_RISK_PER_TRADE", "0.01"))
MAX_RISK_PER_TRADE: float = float(os.environ.get("MAX_RISK_PER_TRADE", "0.02"))
MIN_RISK_PER_TRADE: float = float(os.environ.get("MIN_RISK_PER_TRADE", "0.005"))
DAILY_LOSS_LIMIT: float = float(os.environ.get("DAILY_LOSS_LIMIT", "0.03"))
WEEKLY_LOSS_LIMIT: float = float(os.environ.get("WEEKLY_LOSS_LIMIT", "0.06"))
MONTHLY_DRAWDOWN_CAP: float = float(os.environ.get("MONTHLY_DRAWDOWN_CAP", "0.12"))
MAX_CONSECUTIVE_LOSSES: int = int(os.environ.get("MAX_CONSECUTIVE_LOSSES", "3"))
ACCOUNT_BALANCE: float = float(os.environ.get("ACCOUNT_BALANCE", "100000"))

# ---------------------------------------------------------------------------
# Correlation Engine
# ---------------------------------------------------------------------------
CORRELATION_WINDOW: int = int(os.environ.get("CORRELATION_WINDOW", "30"))
CORRELATION_CAP: float = float(os.environ.get("CORRELATION_CAP", "0.70"))
BETA_LOOKBACK: int = int(os.environ.get("BETA_LOOKBACK", "60"))
USD_CLUSTER_THRESHOLD: float = float(os.environ.get("USD_CLUSTER_THRESHOLD", "0.65"))

# ---------------------------------------------------------------------------
# Risk Parity
# ---------------------------------------------------------------------------
RISK_PARITY_LOOKBACK: int = int(os.environ.get("RISK_PARITY_LOOKBACK", "20"))
RISK_PARITY_TARGET_VOL: float = float(os.environ.get("RISK_PARITY_TARGET_VOL", "0.10"))

# ---------------------------------------------------------------------------
# Volatility Adjustment
# ---------------------------------------------------------------------------
VOL_LOOKBACK: int = int(os.environ.get("VOL_LOOKBACK", "20"))
VOL_SCALE_MIN: float = 0.5
VOL_SCALE_MAX: float = 1.5

# ---------------------------------------------------------------------------
# Drawdown Recovery
# ---------------------------------------------------------------------------
DRAWDOWN_RECOVERY_THRESHOLD: float = float(
    os.environ.get("DRAWDOWN_RECOVERY_THRESHOLD", "0.05")
)
DRAWDOWN_RECOVERY_SCALE: float = float(
    os.environ.get("DRAWDOWN_RECOVERY_SCALE", "0.50")
)

# ---------------------------------------------------------------------------
# Economic Calendar
# ---------------------------------------------------------------------------
ECONOMIC_CALENDAR_ENABLED: bool = (
    os.environ.get("ECONOMIC_CALENDAR_ENABLED", "true").lower() == "true"
)
HIGH_IMPACT_BLACKOUT_MINUTES: int = int(
    os.environ.get("HIGH_IMPACT_BLACKOUT_MINUTES", "60")
)
MEDIUM_IMPACT_BLACKOUT_MINUTES: int = int(
    os.environ.get("MEDIUM_IMPACT_BLACKOUT_MINUTES", "30")
)

# ---------------------------------------------------------------------------
# Performance Tracking
# ---------------------------------------------------------------------------
PERFORMANCE_LOOKBACK_DAYS: int = int(
    os.environ.get("PERFORMANCE_LOOKBACK_DAYS", "30")
)
ATTRIBUTION_ENABLED: bool = (
    os.environ.get("ATTRIBUTION_ENABLED", "true").lower() == "true"
)

# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------
VERSION: str = "3.0.0"
SERVICE_NAME: str = "grandcom-gold-signals"
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
PORT: int = int(os.environ.get("PORT", "8000"))

"""
Centralized Configuration Management
Institutional Multi-Strategy Hybrid Portfolio System v3.0
"""

import os
from typing import Optional, Union
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Core Infrastructure
# ---------------------------------------------------------------------------
MONGO_URL: str = os.environ.get("MONGO_URL", "")
DB_NAME: str = os.environ.get("DB_NAME", "gold_signals_v3")
REDIS_URL: Optional[str] = os.environ.get("REDIS_URL")

# ---------------------------------------------------------------------------
# API Keys
# ---------------------------------------------------------------------------
TWELVE_DATA_API_KEY: str = os.environ.get("TWELVE_DATA_API_KEY", "")
OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY") or os.environ.get("EMERGENT_LLM_KEY", "")
ALPHA_VANTAGE_API_KEY: str = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
FRED_API_KEY: str = os.environ.get("FRED_API_KEY", "")

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_raw_channel = os.environ.get("TELEGRAM_GOLD_CHANNEL_ID", "-1003834233408")
try:
    TELEGRAM_CHANNEL_ID: Union[int, str] = int(_raw_channel)
except ValueError:
    TELEGRAM_CHANNEL_ID = _raw_channel

# ---------------------------------------------------------------------------
# Trading Pairs
# ---------------------------------------------------------------------------
PAIRS = {
    # Re-enabled for live testing — V4 backtest complete with correlation engine
    # (DXY, EURUSD, GBPUSD, USDJPY). Backtest result: -22.64% on real data.
    # Moving to live validation over 1-2 weeks. max_lot capped at 2 (PR #165).
    "XAUUSD": {
        "symbol": "XAU/USD",
        "decimals": 2,
        "atr_sl": 1.5,
        "atr_tp1": 2.0,
        "atr_tp2": 3.5,
        "atr_tp3": 5.0,
        "pip_value": 0.01,
        "min_lot": 0.01,
        "max_lot": 2.0,  # Hard cap — risk control (PR #165)
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

# Correlation assets for exposure engine
CORRELATION_ASSETS = ["XAUUSD", "XAUEUR", "DXY", "EURUSD", "GBPUSD", "USDJPY"]

# ---------------------------------------------------------------------------
# Signal Generation
# ---------------------------------------------------------------------------
SIGNAL_INTERVAL_MINUTES: int = int(os.environ.get("SIGNAL_INTERVAL_MINUTES", "2"))
MIN_CONFIDENCE: int = int(os.environ.get("MIN_CONFIDENCE", "60"))
MIN_SMC_SCORE: int = int(os.environ.get("MIN_SMC_SCORE", "4"))
MIN_MTF_CONFLUENCE: int = int(os.environ.get("MIN_MTF_CONFLUENCE", "2"))

# ---------------------------------------------------------------------------
# Risk Management
# ---------------------------------------------------------------------------
MAX_ACCOUNT_RISK_PCT: float = float(os.environ.get("MAX_ACCOUNT_RISK_PCT", "2.0"))
MAX_DAILY_DRAWDOWN_PCT: float = float(os.environ.get("MAX_DAILY_DRAWDOWN_PCT", "5.0"))
MAX_TOTAL_DRAWDOWN_PCT: float = float(os.environ.get("MAX_TOTAL_DRAWDOWN_PCT", "15.0"))
DEFAULT_ACCOUNT_BALANCE: float = float(os.environ.get("DEFAULT_ACCOUNT_BALANCE", "10000.0"))
RISK_PARITY_LOOKBACK: int = int(os.environ.get("RISK_PARITY_LOOKBACK", "60"))
VOLATILITY_LOOKBACK: int = int(os.environ.get("VOLATILITY_LOOKBACK", "20"))
DRAWDOWN_RECOVERY_FACTOR: float = float(os.environ.get("DRAWDOWN_RECOVERY_FACTOR", "0.5"))

# ---------------------------------------------------------------------------
# Regime Detection
# ---------------------------------------------------------------------------
REGIME_HYSTERESIS: int = int(os.environ.get("REGIME_HYSTERESIS", "3"))
ADX_TREND_THRESHOLD: float = float(os.environ.get("ADX_TREND_THRESHOLD", "25.0"))
ATR_HIGH_VOL_RATIO: float = float(os.environ.get("ATR_HIGH_VOL_RATIO", "1.5"))
ATR_LOW_VOL_RATIO: float = float(os.environ.get("ATR_LOW_VOL_RATIO", "0.6"))

# ---------------------------------------------------------------------------
# Multi-Timeframe
# ---------------------------------------------------------------------------
TIMEFRAMES = ["1h", "4h", "1day", "1week"]
MTF_WEIGHTS = {
    "1h": 0.15,
    "4h": 0.35,
    "1day": 0.35,
    "1week": 0.15,
}
MTF_MIN_ALIGNMENT: float = float(os.environ.get("MTF_MIN_ALIGNMENT", "60.0"))

# ---------------------------------------------------------------------------
# Pivot Points
# ---------------------------------------------------------------------------
PIVOT_METHODS = ["standard", "fibonacci", "woodie", "camarilla"]
PIVOT_LEVELS = 6  # S1-S3, R1-R3
PIVOT_ZONES = 6

# ---------------------------------------------------------------------------
# Correlation Engine
# ---------------------------------------------------------------------------
CORRELATION_WINDOWS = [20, 60, 120]
BETA_LOOKBACK: int = int(os.environ.get("BETA_LOOKBACK", "60"))
USD_CLUSTER_THRESHOLD: float = float(os.environ.get("USD_CLUSTER_THRESHOLD", "0.7"))

# ---------------------------------------------------------------------------
# Economic Calendar
# ---------------------------------------------------------------------------
ECONOMIC_CALENDAR_URL: str = os.environ.get(
    "ECONOMIC_CALENDAR_URL",
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
)
HIGH_IMPACT_BLACKOUT_MINUTES: int = int(os.environ.get("HIGH_IMPACT_BLACKOUT_MINUTES", "30"))
HIGH_IMPACT_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "XAU"]

# ---------------------------------------------------------------------------
# Performance & Journaling
# ---------------------------------------------------------------------------
PERFORMANCE_LOOKBACK_DAYS: int = int(os.environ.get("PERFORMANCE_LOOKBACK_DAYS", "30"))
JOURNAL_MAX_ENTRIES: int = int(os.environ.get("JOURNAL_MAX_ENTRIES", "1000"))

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
PORT: int = int(os.environ.get("PORT", "8002"))
HOST: str = os.environ.get("HOST", "0.0.0.0")
DEBUG: bool = os.environ.get("DEBUG", "false").lower() == "true"
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
ENVIRONMENT: str = os.environ.get("ENVIRONMENT", "production")

# ---------------------------------------------------------------------------
# System Version
# ---------------------------------------------------------------------------
SYSTEM_VERSION = "3.0.0"
SYSTEM_NAME = "Institutional Multi-Strategy Hybrid Portfolio System"

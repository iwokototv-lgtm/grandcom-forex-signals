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
# Hybrid scheduler intervals
# SIGNAL_GENERATION_INTERVAL_MINUTES: how often new signals are generated (default 30 min)
# POSITION_MONITORING_INTERVAL_MINUTES: how often open positions are checked (default 2 min)
SIGNAL_GENERATION_INTERVAL_MINUTES: int = int(
    os.environ.get("SIGNAL_GENERATION_INTERVAL_MINUTES", "30")
)
POSITION_MONITORING_INTERVAL_MINUTES: int = int(
    os.environ.get("POSITION_MONITORING_INTERVAL_MINUTES", "2")
)
MIN_CONFIDENCE: int = int(os.environ.get("MIN_CONFIDENCE", "60"))
MIN_SMC_SCORE: int = int(os.environ.get("MIN_SMC_SCORE", "4"))
MIN_MTF_CONFLUENCE: int = int(os.environ.get("MIN_MTF_CONFLUENCE", "2"))

# Strategy mode — controls which signal engine is active in the live system.
# Backtest results (out-of-sample, real TwelveData):
#   price_action   : +7.85% avg return, 45.1% win rate, 2.17 profit factor  ← WINNER
#   macro_filtered : +0.22% avg return (breakeven)
#   mean_reversion : -7.65% avg return (unprofitable)
#   original       : -21.48% avg return, 23% win rate
# Valid values: "original" | "mean_reversion" | "price_action" | "macro_filtered" | "consensus"
STRATEGY_MODE: str = os.environ.get("STRATEGY_MODE", "price_action")

# ---------------------------------------------------------------------------
# Account & Position Sizing
# ---------------------------------------------------------------------------
# Current live account balance — read from env var ACCOUNT_BALANCE.
# Used for dynamic position sizing: base = 1 unit per $1,000 balance.
ACCOUNT_BALANCE: float = float(os.environ.get("ACCOUNT_BALANCE", os.environ.get("DEFAULT_ACCOUNT_BALANCE", "10000.0")))

# Confidence-based position scale factors (applied on top of base size)
#   60-70% confidence → 0.5x  (cautious)
#   70-80% confidence → 1.0x  (baseline)
#   80-90% confidence → 1.5x  (elevated)
#   90-100% confidence → 2.0x (high conviction)
POSITION_SCALE_60_70: float = float(os.environ.get("POSITION_SCALE_60_70", "0.5"))
POSITION_SCALE_70_80: float = float(os.environ.get("POSITION_SCALE_70_80", "1.0"))
POSITION_SCALE_80_90: float = float(os.environ.get("POSITION_SCALE_80_90", "1.5"))
POSITION_SCALE_90_100: float = float(os.environ.get("POSITION_SCALE_90_100", "2.0"))

# Hard limits on position size (units)
POSITION_SIZE_MAX_UNITS: float = float(os.environ.get("POSITION_SIZE_MAX_UNITS", "10.0"))
POSITION_SIZE_MIN_UNITS: float = float(os.environ.get("POSITION_SIZE_MIN_UNITS", "0.1"))
POSITION_SIZE_BASE_PER_1K: float = float(os.environ.get("POSITION_SIZE_BASE_PER_1K", "1.0"))  # 1 unit per $1,000

# ---------------------------------------------------------------------------
# Risk Management
# ---------------------------------------------------------------------------
MAX_ACCOUNT_RISK_PCT: float = float(os.environ.get("MAX_ACCOUNT_RISK_PCT", "1.0"))
MAX_DAILY_DRAWDOWN_PCT: float = float(os.environ.get("MAX_DAILY_DRAWDOWN_PCT", "5.0"))
MAX_TOTAL_DRAWDOWN_PCT: float = float(os.environ.get("MAX_TOTAL_DRAWDOWN_PCT", "15.0"))
DEFAULT_ACCOUNT_BALANCE: float = float(os.environ.get("DEFAULT_ACCOUNT_BALANCE", "10000.0"))
RISK_PARITY_LOOKBACK: int = int(os.environ.get("RISK_PARITY_LOOKBACK", "60"))
VOLATILITY_LOOKBACK: int = int(os.environ.get("VOLATILITY_LOOKBACK", "20"))
DRAWDOWN_RECOVERY_FACTOR: float = float(os.environ.get("DRAWDOWN_RECOVERY_FACTOR", "0.5"))

# Stop-loss rules
SL_ATR_MULTIPLIER: float = float(os.environ.get("SL_ATR_MULTIPLIER", "2.0"))          # SL at 2x ATR from entry
TRAILING_STOP_ATR_MULT: float = float(os.environ.get("TRAILING_STOP_ATR_MULT", "0.5")) # Trail SL by 0.5x ATR after +1% profit
TRAILING_STOP_PROFIT_TRIGGER_PCT: float = float(os.environ.get("TRAILING_STOP_PROFIT_TRIGGER_PCT", "1.0"))  # Activate trailing at +1%

# Concurrent position limits
MAX_CONCURRENT_POSITIONS_PER_PAIR: int = int(os.environ.get("MAX_CONCURRENT_POSITIONS_PER_PAIR", "5"))

# ---------------------------------------------------------------------------
# Price Action Engine Thresholds (configurable for A/B testing)
# ---------------------------------------------------------------------------
# These thresholds control the price_action strategy engine in hybrid_portfolio_system_v3.
# They can be tuned without code changes via environment variables.
# Per-pair overrides use the pattern: PRICE_ACTION_MOMENTUM_THRESHOLD_XAUUSD, etc.
PRICE_ACTION_MOMENTUM_THRESHOLD: float = float(os.environ.get("PRICE_ACTION_MOMENTUM_THRESHOLD", "0.65"))
PRICE_ACTION_VOLATILITY_THRESHOLD: float = float(os.environ.get("PRICE_ACTION_VOLATILITY_THRESHOLD", "0.55"))
PRICE_ACTION_CONFLUENCE_WEIGHT: float = float(os.environ.get("PRICE_ACTION_CONFLUENCE_WEIGHT", "0.40"))

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
# A/B Testing Framework
# ---------------------------------------------------------------------------
# MongoDB collection name for A/B test configurations
AB_TEST_COLLECTION: str = os.environ.get("AB_TEST_COLLECTION", "ab_tests_v4")
# Maximum number of concurrent active A/B tests per pair
AB_TEST_MAX_ACTIVE_PER_PAIR: int = int(os.environ.get("AB_TEST_MAX_ACTIVE_PER_PAIR", "1"))
# Minimum signals required before an A/B test result is considered statistically meaningful
AB_TEST_MIN_SIGNALS: int = int(os.environ.get("AB_TEST_MIN_SIGNALS", "20"))

# ---------------------------------------------------------------------------
# Account Scaling Logic
# ---------------------------------------------------------------------------
# Starting account balance used as the baseline for scaling milestones
ACCOUNT_SCALING_BASE_BALANCE: float = float(
    os.environ.get("ACCOUNT_SCALING_BASE_BALANCE", "10000.0")
)
# Growth thresholds (%) that trigger a position-size increase:
#   10% growth  → increase POSITION_SIZE_BASE_PER_1K by 10%
#   25% growth  → increase by 25%
#   50% growth  → increase by 50%
ACCOUNT_SCALING_THRESHOLDS: list = [
    {"growth_pct": 10.0,  "size_increase_pct": 10.0},
    {"growth_pct": 25.0,  "size_increase_pct": 25.0},
    {"growth_pct": 50.0,  "size_increase_pct": 50.0},
]
# Hard cap on POSITION_SIZE_BASE_PER_1K after all scaling (units per $1k)
ACCOUNT_SCALING_MAX_BASE_PER_1K: float = float(
    os.environ.get("ACCOUNT_SCALING_MAX_BASE_PER_1K", "50.0")
)
# MongoDB collection for account balance history and scaling events
ACCOUNT_HISTORY_COLLECTION: str = os.environ.get(
    "ACCOUNT_HISTORY_COLLECTION", "account_history_v4"
)

# ---------------------------------------------------------------------------
# Backtest Benchmarks (Phase 2 results — used for live vs backtest comparison)
# ---------------------------------------------------------------------------
BACKTEST_WIN_RATE: float = float(os.environ.get("BACKTEST_WIN_RATE", "45.1"))
BACKTEST_PROFIT_FACTOR: float = float(os.environ.get("BACKTEST_PROFIT_FACTOR", "2.17"))
BACKTEST_AVG_RETURN_PCT: float = float(os.environ.get("BACKTEST_AVG_RETURN_PCT", "7.85"))

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

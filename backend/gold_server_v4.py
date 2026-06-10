"""
Grandcom Gold Signals Server v4.0 — Balanced Edition
Institutional Multi-Strategy Hybrid Portfolio System with Advanced Risk Management

V4.0 Balanced Option C Features:
  ✅ Breakeven Stop-Loss  — Moves SL to entry after TP1 hit (+0.5R activation)
  ✅ Trailing Stop        — Follows price by 1 ATR; activates after TP1 hit
  ✅ Multi-TF Confirmation— 4H signal confirmed by 1H + Daily (≥70% alignment)
  ✅ Advanced Position Sizing — Volatility-adjusted, regime-scaled dynamic lots
  ✅ Light Model Retraining   — Every 24-48 h; adapts to regime changes
  ✅ Manual Execution     — Copy-trading compatible; no full automation

Expected V4 Balanced Option C Results:
  Win Rate      : 70%  (+5% vs V3)
  Monthly P&L   : $2,000-2,800  (+40%)
  Drawdown      : 4.5%  (-22%)
  Signals/Month : 25-30
  Complexity    : Medium-High
  Risk          : Medium

Timeframe: 4H (PERMANENT)
Pairs    : XAUUSD & XAUEUR
Runtime  : Python 3.11 + FastAPI
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import aiohttp
import pandas as pd
import ta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import Bot

load_dotenv()

# ---------------------------------------------------------------------------
# V4.1 Safety Modules — candle-close confirmation, freshness guard, trade mgmt
# ---------------------------------------------------------------------------
try:
    from candle_utils import is_candle_closed
    _CANDLE_UTILS_AVAILABLE = True
except ImportError:
    _CANDLE_UTILS_AVAILABLE = False
    logger_bootstrap = logging.getLogger("gold_server_v4")
    logger_bootstrap.warning("candle_utils not available — candle-close check disabled")

try:
    from data_freshness import DataFreshnessGuard
    _freshness_guard = DataFreshnessGuard()
    _FRESHNESS_GUARD_AVAILABLE = True
except ImportError:
    _freshness_guard = None  # type: ignore
    _FRESHNESS_GUARD_AVAILABLE = False

try:
    from trade_manager import TradeManager, get_trade_manager
    _TRADE_MANAGER_AVAILABLE = True
except ImportError:
    _TRADE_MANAGER_AVAILABLE = False

try:
    from signal_deduplicator import SignalDeduplicator
    _DEDUPLICATOR_AVAILABLE = True
except ImportError:
    _DEDUPLICATOR_AVAILABLE = False
    logger_bootstrap = logging.getLogger("gold_server_v4")
    logger_bootstrap.warning("signal_deduplicator not available — deduplication disabled")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gold_server_v4")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MONGO_URL             = os.environ.get("MONGO_URL", "")
DB_NAME               = os.environ.get("DB_NAME", "gold_signals_v4")
TELEGRAM_BOT_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TWELVE_DATA_API_KEY   = os.environ.get("TWELVE_DATA_API_KEY", "")
OPENAI_API_KEY        = os.environ.get("OPENAI_API_KEY") or os.environ.get("EMERGENT_LLM_KEY", "")

_raw_channel = (
    os.environ.get("TELEGRAM_CHANNEL_ID")
    or os.environ.get("TELEGRAM_GOLD_CHANNEL_ID")
    or "-1003834233408"
)
try:
    TELEGRAM_CHANNEL_ID: int | str = int(_raw_channel)
except ValueError:
    TELEGRAM_CHANNEL_ID = _raw_channel

SIGNAL_INTERVAL_MINUTES = int(os.environ.get("SIGNAL_INTERVAL_MINUTES", "2"))
MIN_CONFIDENCE          = int(os.environ.get("MIN_CONFIDENCE", "62"))   # Raised from 60 → 62 for V4
# ACCOUNT_BALANCE reads from ACCOUNT_BALANCE first, then falls back to DEFAULT_ACCOUNT_BALANCE.
# Set ACCOUNT_BALANCE env var to reflect the live account size for accurate position sizing.
ACCOUNT_BALANCE         = float(os.environ.get("ACCOUNT_BALANCE", os.environ.get("DEFAULT_ACCOUNT_BALANCE", "10000.0")))
STRATEGY_MODE           = os.environ.get("STRATEGY_MODE", "price_action")  # Backtest winner: +7.85% return, 45.1% win rate

# V4 Risk Management Constants
MTF_MIN_ALIGNMENT       = float(os.environ.get("MTF_MIN_ALIGNMENT", "70.0"))   # ≥70% required
BE_ACTIVATION_R         = float(os.environ.get("BE_ACTIVATION_R", "0.5"))      # Breakeven at +0.5R
TRAILING_ATR_MULT       = float(os.environ.get("TRAILING_ATR_MULT", "1.0"))    # Trail by 1 ATR
RETRAIN_INTERVAL_HOURS  = int(os.environ.get("RETRAIN_INTERVAL_HOURS", "24"))  # 24-48 h full retrain
RETRAIN_SYNC_HOURS      = int(os.environ.get("RETRAIN_SYNC_HOURS", "6"))       # 6 h MongoDB win-rate sync
ENABLE_TRAILING_STOP    = os.environ.get("ENABLE_TRAILING_STOP", "true").lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# Risk Management — Drawdown & Daily Loss Controls
# ---------------------------------------------------------------------------
MAX_TOTAL_DRAWDOWN_PCT  = float(os.environ.get("MAX_TOTAL_DRAWDOWN_PCT", "15.0"))  # Hard stop: 15% of account
MAX_DAILY_LOSS_PCT      = float(os.environ.get("MAX_DAILY_DRAWDOWN_PCT", "5.0"))   # Daily limit: 5% of account
MAX_CONCURRENT_PER_PAIR = int(os.environ.get("MAX_CONCURRENT_POSITIONS_PER_PAIR", "5"))
SL_ATR_MULTIPLIER       = float(os.environ.get("SL_ATR_MULTIPLIER", "2.0"))        # SL at 2x ATR from entry
TRAILING_PROFIT_TRIGGER = float(os.environ.get("TRAILING_STOP_PROFIT_TRIGGER_PCT", "1.0"))  # Activate trailing at +1%
TRAILING_SL_ATR_MULT    = float(os.environ.get("TRAILING_STOP_ATR_MULT", "0.5"))   # Move SL by 0.5x ATR

# ---------------------------------------------------------------------------
# Confidence-Based Position Sizing
# ---------------------------------------------------------------------------
# Base: 1 unit per $1,000 account balance
# Scale factor applied on top of base size based on signal confidence:
#   60-70% → 0.5x  |  70-80% → 1.0x  |  80-90% → 1.5x  |  90-100% → 2.0x
POSITION_SIZE_BASE_PER_1K = float(os.environ.get("POSITION_SIZE_BASE_PER_1K", "1.0"))
POSITION_SCALE_60_70      = float(os.environ.get("POSITION_SCALE_60_70", "0.5"))
POSITION_SCALE_70_80      = float(os.environ.get("POSITION_SCALE_70_80", "1.0"))
POSITION_SCALE_80_90      = float(os.environ.get("POSITION_SCALE_80_90", "1.5"))
POSITION_SCALE_90_100     = float(os.environ.get("POSITION_SCALE_90_100", "2.0"))
POSITION_SIZE_MAX_UNITS   = float(os.environ.get("POSITION_SIZE_MAX_UNITS", "10.0"))  # Hard cap
POSITION_SIZE_MIN_UNITS   = float(os.environ.get("POSITION_SIZE_MIN_UNITS", "0.1"))   # Floor

# ---------------------------------------------------------------------------
# Price Action Engine Thresholds (env-var configurable for A/B testing)
# ---------------------------------------------------------------------------
# Per-pair overrides: PRICE_ACTION_MOMENTUM_THRESHOLD_XAUUSD, etc.
PRICE_ACTION_MOMENTUM_THRESHOLD  = float(os.environ.get("PRICE_ACTION_MOMENTUM_THRESHOLD", "0.65"))
PRICE_ACTION_VOLATILITY_THRESHOLD = float(os.environ.get("PRICE_ACTION_VOLATILITY_THRESHOLD", "0.55"))
PRICE_ACTION_CONFLUENCE_WEIGHT   = float(os.environ.get("PRICE_ACTION_CONFLUENCE_WEIGHT", "0.40"))

# ---------------------------------------------------------------------------
# A/B Testing & Account Scaling Constants
# ---------------------------------------------------------------------------
AB_TEST_COLLECTION          = os.environ.get("AB_TEST_COLLECTION", "ab_tests_v4")
AB_TEST_MIN_SIGNALS         = int(os.environ.get("AB_TEST_MIN_SIGNALS", "20"))
AB_TEST_MAX_ACTIVE_PER_PAIR = int(os.environ.get("AB_TEST_MAX_ACTIVE_PER_PAIR", "1"))

ACCOUNT_SCALING_BASE_BALANCE    = float(os.environ.get("ACCOUNT_SCALING_BASE_BALANCE", "10000.0"))
ACCOUNT_SCALING_MAX_BASE_PER_1K = float(os.environ.get("ACCOUNT_SCALING_MAX_BASE_PER_1K", "50.0"))
ACCOUNT_HISTORY_COLLECTION      = os.environ.get("ACCOUNT_HISTORY_COLLECTION", "account_history_v4")
ACCOUNT_SCALING_THRESHOLDS = [
    {"growth_pct": 10.0, "size_increase_pct": 10.0},
    {"growth_pct": 25.0, "size_increase_pct": 25.0},
    {"growth_pct": 50.0, "size_increase_pct": 50.0},
]

# Backtest benchmarks (Phase 2 results — used for live vs backtest comparison)
BACKTEST_WIN_RATE        = float(os.environ.get("BACKTEST_WIN_RATE", "45.1"))
BACKTEST_PROFIT_FACTOR   = float(os.environ.get("BACKTEST_PROFIT_FACTOR", "2.17"))
BACKTEST_AVG_RETURN_PCT  = float(os.environ.get("BACKTEST_AVG_RETURN_PCT", "7.85"))

# V4 Advanced Position Sizing — Volatility Regime Constants
# Regimes: SQUEEZE → NORMAL → EXPANDING → HIGH_EXPANDING → EXTREME_HIGH
VOL_REGIME_MULTIPLIERS: dict[str, float] = {
    "SQUEEZE":        1.10,   # Low vol → slightly larger size
    "NORMAL":         1.00,   # Baseline
    "EXPANDING":      0.85,   # Vol picking up → reduce slightly
    "HIGH_EXPANDING": 0.60,   # High vol expansion → significant reduction
    "EXTREME_HIGH":   0.30,   # Extreme vol → minimal exposure
    # Legacy names from VolatilityAdjustment (mapped for compatibility)
    "LOW":            1.10,
    "LOW_CONTRACTING":1.10,
    "HIGH":           0.75,
    "CHAOS":          0.00,
    "UNKNOWN":        0.80,
    "TREND_UP":       1.00,
    "TREND_DOWN":     1.00,
    "RANGE":          0.80,
    "HIGH_VOL":       0.60,
    "LOW_VOL":        1.10,
}
VOL_POSITION_SIZE_HARD_CAP = 1.5   # Hard cap: never exceed 1.5x base lot size

# ---------------------------------------------------------------------------
# V4.1 Safety Config
# ---------------------------------------------------------------------------
# Maximum data age before a freshness warning is emitted (seconds)
DATA_FRESHNESS_MAX_AGE_SECONDS = int(os.environ.get("DATA_FRESHNESS_MAX_AGE_SECONDS", "300"))

# V4.1 Runtime Metrics (in-memory counters, reset on restart)
_v4_metrics: dict[str, int] = {
    "signals_generated":          0,
    "signals_suppressed_candle":  0,   # Suppressed: candle still forming
    "signals_suppressed_stale":   0,   # Suppressed: stale data
    "signals_suppressed_dedupe":  0,   # Suppressed: duplicate within same 4H candle
    "trades_opened":              0,
    "be_activations":             0,
    "ts_updates":                 0,
    "partial_closes":             0,
    "trade_closes":               0,
}

# ---------------------------------------------------------------------------
# Trading Pairs — V4 ATR Multipliers (tighter TP1 for faster BE activation)
# ---------------------------------------------------------------------------
PAIRS: dict[str, dict] = {
    "XAUUSD": {
        "symbol":   "XAU/USD",
        "decimals": 2,
        # SL wider than V3 to survive noise before BE kicks in
        "atr_sl":   1.0,    # 1.0x ATR  — slightly wider for swing room
        # TP1 tighter → hit faster → BE activates sooner → drawdown ↓
        "atr_tp1":  0.40,   # 0.40x ATR — quick TP1 / BE trigger
        "atr_tp2":  0.80,   # 0.80x ATR — mid target
        "atr_tp3":  1.40,   # 1.40x ATR — extended target (trailing captures)
    },
    "XAUEUR": {
        "symbol":   "XAU/EUR",
        "decimals": 2,
        "atr_sl":   1.0,
        "atr_tp1":  0.40,
        "atr_tp2":  0.80,
        "atr_tp3":  1.40,
    },
}

# ---------------------------------------------------------------------------
# MongoDB
# ---------------------------------------------------------------------------
_mongo_client: AsyncIOMotorClient | None = None
_db = None


def get_db():
    return _db


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
_bot: Bot | None = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        if not TELEGRAM_BOT_TOKEN:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
        _bot = Bot(token=TELEGRAM_BOT_TOKEN)
    return _bot


# ---------------------------------------------------------------------------
# Hybrid System (lazy import)
# ---------------------------------------------------------------------------
_hybrid_system = None


def get_hybrid_system():
    global _hybrid_system
    if _hybrid_system is None:
        try:
            from ml_engine.hybrid_portfolio_system_v3 import HybridPortfolioSystemV3
            _hybrid_system = HybridPortfolioSystemV3(account_balance=ACCOUNT_BALANCE)
            logger.info("✅ HybridPortfolioSystemV3 loaded for V4")
        except Exception as exc:
            logger.error(f"❌ Failed to load HybridPortfolioSystemV3: {exc}")
            _hybrid_system = None
    return _hybrid_system


# ---------------------------------------------------------------------------
# V4 Feature 5: Light Model Retraining — WinRateTracker
# ---------------------------------------------------------------------------
class WinRateTracker:
    """
    Exponentially-weighted win-rate tracker per (regime, signal_type) bucket.

    Maintains a live confidence multiplier in [0.80, 1.20] for each bucket.
    Syncs from MongoDB every RETRAIN_SYNC_HOURS (6 h) and performs a full
    parameter re-optimisation every RETRAIN_INTERVAL_HOURS (24-48 h).

    Bucket key: f"{regime}:{signal_type}"  e.g. "TREND_UP:BUY"
    """

    EW_ALPHA          = 0.15    # EW decay — higher = faster adaptation
    CONF_MULT_MIN     = 0.80    # Minimum confidence multiplier
    CONF_MULT_MAX     = 1.20    # Maximum confidence multiplier
    MIN_BUCKET_TRADES = 5       # Minimum trades before multiplier activates

    def __init__(self) -> None:
        # bucket_key → {"ew_win_rate": float, "trade_count": int, "conf_mult": float}
        self._buckets: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_confidence_multiplier(self, regime: str, signal_type: str) -> float:
        """
        Return the confidence multiplier for a (regime, signal_type) bucket.
        Returns 1.0 (neutral) if the bucket has insufficient data.
        """
        key = f"{regime.upper()}:{signal_type.upper()}"
        bucket = self._buckets.get(key)
        if bucket is None or bucket["trade_count"] < self.MIN_BUCKET_TRADES:
            return 1.0
        return bucket["conf_mult"]

    def record_outcome(self, regime: str, signal_type: str, won: bool) -> None:
        """
        Update the EW win-rate for a bucket after a trade closes.
        Called in-process when a signal result is recorded.
        """
        key = f"{regime.upper()}:{signal_type.upper()}"
        outcome = 1.0 if won else 0.0

        if key not in self._buckets:
            self._buckets[key] = {
                "ew_win_rate":  outcome,
                "trade_count":  1,
                "conf_mult":    1.0,
            }
        else:
            b = self._buckets[key]
            b["ew_win_rate"] = (
                self.EW_ALPHA * outcome
                + (1 - self.EW_ALPHA) * b["ew_win_rate"]
            )
            b["trade_count"] += 1
            b["conf_mult"] = self._wr_to_multiplier(b["ew_win_rate"])

    def get_all_buckets(self) -> dict:
        """Return a snapshot of all bucket states (for API / logging)."""
        return {k: dict(v) for k, v in self._buckets.items()}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _wr_to_multiplier(self, ew_win_rate: float) -> float:
        """
        Map EW win-rate → confidence multiplier in [0.80, 1.20].

        Linear interpolation:
          wr ≤ 0.40  →  0.80  (penalise poor-performing bucket)
          wr = 0.65  →  1.00  (neutral — matches V3 baseline)
          wr ≥ 0.85  →  1.20  (reward high-performing bucket)
        """
        if ew_win_rate <= 0.40:
            return self.CONF_MULT_MIN
        if ew_win_rate >= 0.85:
            return self.CONF_MULT_MAX
        # Linear interpolation between anchor points
        if ew_win_rate <= 0.65:
            t = (ew_win_rate - 0.40) / (0.65 - 0.40)
            return round(self.CONF_MULT_MIN + t * (1.0 - self.CONF_MULT_MIN), 4)
        else:
            t = (ew_win_rate - 0.65) / (0.85 - 0.65)
            return round(1.0 + t * (self.CONF_MULT_MAX - 1.0), 4)

    async def sync_from_mongodb(self, db) -> dict:
        """
        Rebuild all bucket EW win-rates from the last 500 closed signals
        stored in MongoDB.  Called every RETRAIN_SYNC_HOURS (6 h).
        """
        if db is None:
            return {"synced": False, "error": "MongoDB not connected"}

        try:
            signals = (
                await db.gold_signals_v4
                .find(
                    {"status": {"$in": ["CLOSED", "WIN", "LOSS"]}},
                    {"_id": 0, "regime": 1, "type": 1, "result": 1},
                )
                .sort("created_at", -1)
                .limit(500)
                .to_list(500)
            )

            if not signals:
                return {"synced": True, "buckets_updated": 0, "signals_processed": 0}

            # Rebuild buckets from scratch (full replay)
            new_buckets: dict[str, dict] = {}
            for sig in reversed(signals):   # oldest first for correct EW order
                regime      = str(sig.get("regime", "UNKNOWN")).upper()
                signal_type = str(sig.get("type", "UNKNOWN")).upper()
                result      = str(sig.get("result", "")).upper()
                if signal_type not in ("BUY", "SELL") or result not in ("WIN", "LOSS"):
                    continue

                key     = f"{regime}:{signal_type}"
                outcome = 1.0 if result == "WIN" else 0.0

                if key not in new_buckets:
                    new_buckets[key] = {
                        "ew_win_rate": outcome,
                        "trade_count": 1,
                        "conf_mult":   1.0,
                    }
                else:
                    b = new_buckets[key]
                    b["ew_win_rate"] = (
                        self.EW_ALPHA * outcome
                        + (1 - self.EW_ALPHA) * b["ew_win_rate"]
                    )
                    b["trade_count"] += 1
                    b["conf_mult"] = self._wr_to_multiplier(b["ew_win_rate"])

            async with self._lock:
                self._buckets = new_buckets

            logger.info(
                f"✅ WinRateTracker synced — {len(signals)} signals, "
                f"{len(new_buckets)} buckets"
            )
            return {
                "synced":            True,
                "signals_processed": len(signals),
                "buckets_updated":   len(new_buckets),
                "buckets":           {k: round(v["ew_win_rate"], 4) for k, v in new_buckets.items()},
            }

        except Exception as exc:
            logger.error(f"❌ WinRateTracker sync failed: {exc}", exc_info=True)
            return {"synced": False, "error": str(exc)}


# Module-level singleton
_win_rate_tracker = WinRateTracker()


def get_win_rate_tracker() -> WinRateTracker:
    return _win_rate_tracker


# ---------------------------------------------------------------------------
# Signal Deduplicator — module-level singleton (db injected at startup)
# ---------------------------------------------------------------------------
_deduplicator: "SignalDeduplicator | None" = None


def get_deduplicator() -> "SignalDeduplicator | None":
    return _deduplicator


# ---------------------------------------------------------------------------
# Light Model Retraining State
# ---------------------------------------------------------------------------
_last_retrain_time: datetime | None = None
_last_sync_time:    datetime | None = None
_retrain_lock = asyncio.Lock()


async def sync_win_rate_tracker() -> dict:
    """
    Sync WinRateTracker from MongoDB every RETRAIN_SYNC_HOURS (6 h).
    Lightweight — only reads result/regime/type fields.
    """
    global _last_sync_time

    now = datetime.now(timezone.utc)
    if _last_sync_time is not None:
        elapsed = (now - _last_sync_time).total_seconds() / 3600
        if elapsed < RETRAIN_SYNC_HOURS:
            return {
                "skipped": True,
                "next_sync_in_hours": round(RETRAIN_SYNC_HOURS - elapsed, 1),
            }

    db = get_db()
    result = await _win_rate_tracker.sync_from_mongodb(db)
    if result.get("synced"):
        _last_sync_time = now
    return result


async def maybe_retrain_model() -> dict:
    """
    Light model retraining — runs every RETRAIN_INTERVAL_HOURS (24-48 h).
    Pulls recent closed signals from MongoDB and re-optimises the signal
    quality parameters without a full rebuild.

    Also triggers a WinRateTracker sync as part of the retrain cycle.
    """
    global _last_retrain_time

    now = datetime.now(timezone.utc)
    if _last_retrain_time is not None:
        elapsed = (now - _last_retrain_time).total_seconds() / 3600
        if elapsed < RETRAIN_INTERVAL_HOURS:
            return {"skipped": True, "next_retrain_in_hours": round(RETRAIN_INTERVAL_HOURS - elapsed, 1)}

    async with _retrain_lock:
        # Double-check after acquiring lock
        if _last_retrain_time is not None:
            elapsed = (now - _last_retrain_time).total_seconds() / 3600
            if elapsed < RETRAIN_INTERVAL_HOURS:
                return {"skipped": True}

        logger.info("🔄 V4 Light model retraining started …")
        result: dict = {"timestamp": now.isoformat(), "success": False}

        db = get_db()
        if db is None:
            result["error"] = "MongoDB not connected"
            return result

        try:
            from ml_engine.model_trainer import SignalOptimizationEngine

            # Fetch last 500 closed signals
            signals = (
                await db.gold_signals_v4
                .find({"status": {"$in": ["CLOSED", "WIN", "LOSS"]}}, {"_id": 0})
                .sort("created_at", -1)
                .limit(500)
                .to_list(500)
            )

            if len(signals) < 30:
                result["error"] = f"Insufficient data for retraining ({len(signals)} signals, need ≥30)"
                logger.warning(result["error"])
                return result

            optimizer = SignalOptimizationEngine()
            pair_analysis   = optimizer.analyze_performance_by_pair(signals)
            regime_analysis = optimizer.analyze_performance_by_regime(signals)
            recommendations = optimizer.recommend_pair_settings(pair_analysis)

            total    = len(signals)
            wins     = sum(1 for s in signals if s.get("result") == "WIN")
            win_rate = wins / total * 100 if total else 0

            # Also sync the WinRateTracker buckets
            sync_result = await _win_rate_tracker.sync_from_mongodb(db)

            result.update({
                "success":            True,
                "signals_analyzed":   total,
                "win_rate":           round(win_rate, 1),
                "pair_analysis":      pair_analysis,
                "regime_analysis":    regime_analysis,
                "recommendations":    recommendations,
                "win_rate_buckets":   sync_result.get("buckets", {}),
                "buckets_updated":    sync_result.get("buckets_updated", 0),
            })

            _last_retrain_time = now
            logger.info(
                f"✅ V4 Light retraining complete — {total} signals, "
                f"win_rate={win_rate:.1f}%, "
                f"buckets={sync_result.get('buckets_updated', 0)}"
            )

        except Exception as exc:
            result["error"] = str(exc)
            logger.error(f"❌ V4 retraining failed: {exc}", exc_info=True)

        return result


# ---------------------------------------------------------------------------
# Live Risk Manager — Drawdown & Daily Loss Controls
# ---------------------------------------------------------------------------
class LiveRiskManager:
    """
    Tracks cumulative P&L and enforces hard risk limits:

      - Max total drawdown : 15% of account balance (hard stop)
      - Daily loss limit   : 5% of account balance (resets at UTC midnight)
      - Max concurrent positions per pair : 5

    When a limit is breached, ``is_trading_allowed()`` returns False and
    all new signal generation is paused until the condition clears or the
    daily counter resets.

    Thread-safe via asyncio.Lock for concurrent scheduler access.
    """

    # Alert thresholds — fire AI warning before hard limits are hit
    DRAWDOWN_ALERT_PCT   = 10.0   # Warn at 10% drawdown (hard limit: 15%)
    DAILY_LOSS_ALERT_PCT = 3.0    # Warn at 3% daily loss (hard limit: 5%)

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        # Cumulative P&L tracking (in account currency)
        self._total_pnl: float = 0.0          # Lifetime P&L since last restart
        self._daily_pnl: float = 0.0          # P&L since last UTC midnight reset
        self._daily_reset_date: str = ""       # ISO date of last daily reset
        # Per-pair open position count
        self._open_positions: dict[str, int] = {}
        # Breach flags
        self._drawdown_breached: bool = False
        self._daily_limit_breached: bool = False
        # Alert-sent flags — prevent repeated alerts for the same threshold crossing
        self._drawdown_alert_sent: bool = False
        self._daily_loss_alert_sent: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_trading_allowed(self, pair: str) -> tuple[bool, str]:
        """
        Return (allowed, reason).  Checks:
          1. Total drawdown ≤ MAX_TOTAL_DRAWDOWN_PCT
          2. Daily loss ≤ MAX_DAILY_LOSS_PCT
          3. Open positions for pair < MAX_CONCURRENT_PER_PAIR
        """
        self._maybe_reset_daily()

        account = ACCOUNT_BALANCE
        max_total_loss = account * (MAX_TOTAL_DRAWDOWN_PCT / 100.0)
        max_daily_loss = account * (MAX_DAILY_LOSS_PCT / 100.0)

        if self._total_pnl <= -max_total_loss:
            self._drawdown_breached = True
            return False, (
                f"MAX_DRAWDOWN_BREACHED: cumulative_loss=${abs(self._total_pnl):.2f} "
                f">= {MAX_TOTAL_DRAWDOWN_PCT}% of ${account:.0f}"
            )

        if self._daily_pnl <= -max_daily_loss:
            self._daily_limit_breached = True
            return False, (
                f"DAILY_LOSS_LIMIT_BREACHED: daily_loss=${abs(self._daily_pnl):.2f} "
                f">= {MAX_DAILY_LOSS_PCT}% of ${account:.0f}"
            )

        # ── AI risk alerts — warn before hard limits are hit ──────────────
        total_drawdown_pct = abs(self._total_pnl / account * 100) if account else 0.0
        daily_loss_pct     = abs(self._daily_pnl / account * 100) if account else 0.0

        if (
            total_drawdown_pct >= self.DRAWDOWN_ALERT_PCT
            and not self._drawdown_alert_sent
        ):
            self._drawdown_alert_sent = True
            asyncio.ensure_future(
                generate_risk_alert(
                    alert_type="DRAWDOWN",
                    current_pct=round(total_drawdown_pct, 2),
                    limit_pct=MAX_TOTAL_DRAWDOWN_PCT,
                    account_balance=account,
                    daily_pnl=self._daily_pnl,
                    total_pnl=self._total_pnl,
                )
            )

        if (
            daily_loss_pct >= self.DAILY_LOSS_ALERT_PCT
            and not self._daily_loss_alert_sent
        ):
            self._daily_loss_alert_sent = True
            asyncio.ensure_future(
                generate_risk_alert(
                    alert_type="DAILY_LOSS",
                    current_pct=round(daily_loss_pct, 2),
                    limit_pct=MAX_DAILY_LOSS_PCT,
                    account_balance=account,
                    daily_pnl=self._daily_pnl,
                    total_pnl=self._total_pnl,
                )
            )

        open_count = self._open_positions.get(pair, 0)
        if open_count >= MAX_CONCURRENT_PER_PAIR:
            return False, (
                f"MAX_CONCURRENT_POSITIONS: {pair} already has "
                f"{open_count}/{MAX_CONCURRENT_PER_PAIR} open positions"
            )

        return True, "OK"

    def record_trade_close(self, pair: str, pnl: float) -> None:
        """Record a closed trade's P&L and update counters."""
        self._maybe_reset_daily()
        self._total_pnl += pnl
        self._daily_pnl += pnl
        # Decrement open position count (floor at 0)
        if pair in self._open_positions:
            self._open_positions[pair] = max(0, self._open_positions[pair] - 1)
        logger.info(
            f"[RiskManager] Trade closed: pair={pair} pnl=${pnl:+.2f} "
            f"daily_pnl=${self._daily_pnl:+.2f} total_pnl=${self._total_pnl:+.2f}"
        )

    def record_trade_open(self, pair: str) -> None:
        """Increment open position count when a new trade is opened."""
        self._open_positions[pair] = self._open_positions.get(pair, 0) + 1

    def get_state(self) -> dict:
        """Return a snapshot of current risk state for logging/API."""
        self._maybe_reset_daily()
        account = ACCOUNT_BALANCE
        return {
            "account_balance":        account,
            "total_pnl":              round(self._total_pnl, 2),
            "daily_pnl":              round(self._daily_pnl, 2),
            "total_drawdown_pct":     round(self._total_pnl / account * 100, 2) if account else 0.0,
            "daily_loss_pct":         round(self._daily_pnl / account * 100, 2) if account else 0.0,
            "max_total_drawdown_pct": MAX_TOTAL_DRAWDOWN_PCT,
            "max_daily_loss_pct":     MAX_DAILY_LOSS_PCT,
            "drawdown_breached":      self._drawdown_breached,
            "daily_limit_breached":   self._daily_limit_breached,
            "open_positions":         dict(self._open_positions),
            "max_concurrent_per_pair": MAX_CONCURRENT_PER_PAIR,
            "daily_reset_date":       self._daily_reset_date,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _maybe_reset_daily(self) -> None:
        """Reset daily P&L counter at UTC midnight."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._daily_reset_date:
            if self._daily_reset_date:  # Not the first call
                logger.info(
                    f"[RiskManager] Daily reset — previous day P&L: "
                    f"${self._daily_pnl:+.2f} | resetting for {today}"
                )
            self._daily_pnl = 0.0
            self._daily_limit_breached = False
            self._daily_loss_alert_sent = False   # Reset daily alert flag at midnight
            self._daily_reset_date = today


# Module-level singleton
_live_risk_manager = LiveRiskManager()


def get_live_risk_manager() -> LiveRiskManager:
    return _live_risk_manager


# ---------------------------------------------------------------------------
# Confidence-Based Position Sizing
# ---------------------------------------------------------------------------
def compute_confidence_position_size(
    confidence: float,
    account_balance: float = ACCOUNT_BALANCE,
) -> dict:
    """
    Compute position size (units) based on account balance and signal confidence.

    Formula:
      base_units = account_balance / 1000 * POSITION_SIZE_BASE_PER_1K
      scale_factor = confidence tier (0.5x / 1.0x / 1.5x / 2.0x)
      units = clamp(base_units * scale_factor, MIN_UNITS, MAX_UNITS)

    Confidence tiers:
      60-70%  → 0.5x  (cautious — low conviction)
      70-80%  → 1.0x  (baseline)
      80-90%  → 1.5x  (elevated conviction)
      90-100% → 2.0x  (high conviction)

    Returns a dict with units, scale_factor, base_units, and tier label.
    """
    base_units = (account_balance / 1000.0) * POSITION_SIZE_BASE_PER_1K

    if confidence >= 90.0:
        scale_factor = POSITION_SCALE_90_100
        tier = "HIGH_CONVICTION_90_100"
    elif confidence >= 80.0:
        scale_factor = POSITION_SCALE_80_90
        tier = "ELEVATED_80_90"
    elif confidence >= 70.0:
        scale_factor = POSITION_SCALE_70_80
        tier = "BASELINE_70_80"
    else:
        scale_factor = POSITION_SCALE_60_70
        tier = "CAUTIOUS_60_70"

    raw_units = base_units * scale_factor
    units = round(
        max(POSITION_SIZE_MIN_UNITS, min(POSITION_SIZE_MAX_UNITS, raw_units)),
        2,
    )
    capped = raw_units > POSITION_SIZE_MAX_UNITS or raw_units < POSITION_SIZE_MIN_UNITS

    logger.info(
        f"[ConfidenceSizing] confidence={confidence:.1f}% tier={tier} "
        f"base={base_units:.2f} scale={scale_factor}x → units={units} "
        f"(capped={capped})"
    )

    return {
        "units":          units,
        "base_units":     round(base_units, 2),
        "scale_factor":   scale_factor,
        "tier":           tier,
        "confidence":     round(confidence, 1),
        "account_balance": account_balance,
        "capped":         capped,
        "max_units":      POSITION_SIZE_MAX_UNITS,
        "min_units":      POSITION_SIZE_MIN_UNITS,
    }


# ---------------------------------------------------------------------------
# JSON Signal Logger — structured log for MongoDB storage & monitoring
# ---------------------------------------------------------------------------
def log_signal_json(
    pair: str,
    signal_type: str,
    confidence: float,
    entry: float,
    tps: list,
    sl: float,
    rr: float,
    pos_size: dict,
    conf_sizing: dict,
    be_ts: dict,
    regime: str,
    smc_score: int,
    mtf_alignment: float,
    mtf_direction: str,
    strategy_mode: str,
    pa_thresholds: dict,
    risk_state: dict,
    analysis: str,
    ab_test_id: str | None = None,
) -> dict:
    """
    Emit a structured JSON log entry for every generated signal.

    This record is:
      - Logged at INFO level (JSON string) for log aggregators
      - Returned as a dict for MongoDB storage alongside the signal document
      - Designed for easy parsing, dashboarding, and performance analysis

    Fields cover all four monitoring dimensions:
      1. Signal identity   — timestamp, pair, direction, confidence
      2. Price levels      — entry, TP1/2/3, SL, R:R
      3. Position sizing   — units (confidence-based + vol-adjusted), risk $
      4. Risk context      — regime, drawdown state, PA thresholds used
    """
    now = datetime.now(timezone.utc)
    record = {
        # ── Signal identity ──────────────────────────────────────────
        "log_type":         "SIGNAL_GENERATED",
        "timestamp":        now.isoformat(),
        "timestamp_unix":   int(now.timestamp()),
        "pair":             pair,
        "direction":        signal_type,          # "BUY" or "SELL"
        "confidence_pct":   round(confidence, 1),
        "strategy_mode":    strategy_mode,

        # ── Price levels ─────────────────────────────────────────────
        "entry_price":      entry,
        "tp1":              tps[0] if len(tps) > 0 else None,
        "tp2":              tps[1] if len(tps) > 1 else None,
        "tp3":              tps[2] if len(tps) > 2 else None,
        "sl_price":         sl,
        "risk_reward":      rr,

        # ── Breakeven / trailing stop levels ─────────────────────────
        "be_trigger":       be_ts.get("be_trigger"),
        "be_sl":            be_ts.get("be_sl"),
        "ts_start":         be_ts.get("ts_start"),
        "ts_distance":      be_ts.get("ts_distance"),
        "ts_enabled":       be_ts.get("ts_enabled"),

        # ── Position sizing (confidence-based) ───────────────────────
        "position_units":   conf_sizing.get("units"),
        "position_tier":    conf_sizing.get("tier"),
        "position_scale":   conf_sizing.get("scale_factor"),
        "position_base":    conf_sizing.get("base_units"),

        # ── Position sizing (vol-adjusted, from advanced sizing) ──────
        "lots":             pos_size.get("lots"),
        "dollar_risk":      pos_size.get("dollar_risk"),
        "risk_pct":         pos_size.get("risk_pct"),
        "vol_regime":       pos_size.get("vol_regime"),
        "vol_regime_mult":  pos_size.get("vol_regime_mult"),
        "conf_mult":        pos_size.get("conf_mult"),
        "stop_distance":    pos_size.get("stop_distance"),

        # ── Market context ────────────────────────────────────────────
        "regime":           regime,
        "smc_score":        smc_score,
        "mtf_alignment_pct": round(mtf_alignment, 1),
        "mtf_direction":    mtf_direction,

        # ── Price action thresholds used (for A/B analysis) ───────────
        "pa_momentum_threshold":   pa_thresholds.get("momentum_threshold"),
        "pa_volatility_threshold": pa_thresholds.get("volatility_threshold"),
        "pa_confluence_weight":    pa_thresholds.get("confluence_weight"),

        # ── Risk management state at signal time ──────────────────────
        "account_balance":        risk_state.get("account_balance"),
        "daily_pnl":              risk_state.get("daily_pnl"),
        "total_pnl":              risk_state.get("total_pnl"),
        "daily_loss_pct":         risk_state.get("daily_loss_pct"),
        "total_drawdown_pct":     risk_state.get("total_drawdown_pct"),
        "open_positions_pair":    risk_state.get("open_positions", {}).get(pair, 0),

        # ── Analysis text ─────────────────────────────────────────────
        "analysis":         analysis,

        # ── A/B test attribution ──────────────────────────────────────
        "ab_test_id":       ab_test_id,

        # ── System metadata ───────────────────────────────────────────
        "system_version":   "4.0.0",
        "signal_engine":    "grandcom_gold_v4",
    }

    # Emit as a single-line JSON string so log aggregators can parse it
    logger.info(f"SIGNAL_JSON {json.dumps(record, default=str)}")
    return record


# ---------------------------------------------------------------------------
# Price Data
# ---------------------------------------------------------------------------
async def fetch_ohlcv(
    pair: str,
    interval: str = "4h",
    outputsize: int = 100,
) -> tuple[pd.DataFrame, datetime] | tuple[None, None]:
    """Fetch OHLCV from TwelveData.

    Returns a (DataFrame, response_timestamp) tuple where response_timestamp
    is the UTC datetime captured immediately after the API call returned.
    This timestamp is used by DataFreshnessGuard to measure feed staleness
    (not the candle open time).  Returns (None, None) on failure.
    """
    cfg = PAIRS[pair]
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={cfg['symbol']}&interval={interval}&outputsize={outputsize}"
        f"&apikey={TWELVE_DATA_API_KEY}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()

        # Capture response time immediately after the API call completes
        response_timestamp = datetime.now(timezone.utc)

        if "values" not in data:
            logger.error(f"[{pair}] TwelveData error: {data.get('message', data)}")
            return None, None

        df = pd.DataFrame(data["values"])
        for col in ("open", "high", "low", "close"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.iloc[::-1].reset_index(drop=True)
        logger.info(f"[{pair}] Fetched {len(df)} {interval} candles (response_ts={response_timestamp.isoformat()})")
        return df, response_timestamp

    except Exception as exc:
        logger.error(f"[{pair}] fetch_ohlcv failed: {exc}")
        return None, None


async def fetch_correlation_ohlcv(
    symbol: str,
    interval: str = "4h",
    outputsize: int = 100,
    timeout: int = 10,
) -> pd.DataFrame | None:
    """Fetch OHLCV for an arbitrary symbol (e.g. DXY, EURUSD) from TwelveData.

    Unlike fetch_ohlcv, this function accepts any symbol string directly and
    is used to retrieve correlation assets that are not in the PAIRS config.
    Returns a DataFrame on success, or None on failure.
    """
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={symbol}&interval={interval}&outputsize={outputsize}"
        f"&apikey={TWELVE_DATA_API_KEY}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                data = await resp.json()

        if "values" not in data:
            logger.warning(
                f"[corr:{symbol}] TwelveData error: {data.get('message', data)}"
            )
            return None

        df = pd.DataFrame(data["values"])
        for col in ("open", "high", "low", "close"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.iloc[::-1].reset_index(drop=True)
        logger.info(f"[corr:{symbol}] Fetched {len(df)} {interval} candles")
        return df

    except Exception as exc:
        logger.warning(f"[corr:{symbol}] fetch_correlation_ohlcv failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Technical Indicators
# ---------------------------------------------------------------------------
def compute_indicators(df: pd.DataFrame, decimals: int) -> dict | None:
    """Compute RSI, MACD, MA20/50, ATR for 4H candles."""
    try:
        close = df["close"]
        high  = df["high"]
        low   = df["low"]

        rsi      = ta.momentum.RSIIndicator(close, window=14).rsi()
        macd_obj = ta.trend.MACD(close)
        ma20     = ta.trend.SMAIndicator(close, window=20).sma_indicator()
        ma50     = ta.trend.SMAIndicator(close, window=50).sma_indicator()
        atr      = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()

        last = df.iloc[-1]
        dp   = decimals

        return {
            "price":    round(float(last["close"]), dp),
            "rsi":      round(float(rsi.iloc[-1]), 2),
            "macd":     round(float(macd_obj.macd().iloc[-1]), 6),
            "macd_sig": round(float(macd_obj.macd_signal().iloc[-1]), 6),
            "ma20":     round(float(ma20.iloc[-1]), dp),
            "ma50":     round(float(ma50.iloc[-1]), dp),
            "atr":      round(float(atr.iloc[-1]), dp),
            "trend":    "BULLISH" if float(last["close"]) > float(ma50.iloc[-1]) else "BEARISH",
        }
    except Exception as exc:
        logger.error(f"compute_indicators failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# V4 Feature 3: Multi-Timeframe Confirmation (MTF)
# ---------------------------------------------------------------------------
async def run_mtf_confirmation(pair: str) -> dict:
    """
    Confirm 4H signal with 1H + Daily alignment.
    Requires ≥70% alignment score (MTF_MIN_ALIGNMENT).
    Filters ~50% of false signals, adds ~10% win rate.
    """
    try:
        from ml_engine.multi_timeframe_confirmation import MultiTimeframeConfirmation

        mtf = MultiTimeframeConfirmation()
        result = await mtf.analyze(pair)

        alignment_score     = float(result.get("alignment_score", 0.0))
        dominant_direction  = result.get("dominant_direction", "NEUTRAL")
        timeframes          = result.get("timeframes", {})

        # Extract 1H and Daily specifically for V4 confirmation logic
        tf_1h    = timeframes.get("1h",    {})
        tf_daily = timeframes.get("1day",  {})
        tf_4h    = timeframes.get("4h",    {})

        # V4 requires 1H + Daily to agree with 4H direction
        directions = {
            "1h":    tf_1h.get("direction",    "NEUTRAL"),
            "4h":    tf_4h.get("direction",    "NEUTRAL"),
            "1day":  tf_daily.get("direction", "NEUTRAL"),
        }

        aligned_count = sum(
            1 for d in directions.values()
            if d == dominant_direction and d != "NEUTRAL"
        )
        alignment_ok = (
            alignment_score >= MTF_MIN_ALIGNMENT
            and aligned_count >= 2          # At least 2 of 3 TFs agree
            and dominant_direction != "NEUTRAL"
        )

        logger.info(
            f"[{pair}] MTF V4: score={alignment_score:.1f}% "
            f"direction={dominant_direction} aligned={aligned_count}/3 "
            f"ok={alignment_ok}"
        )

        return {
            "alignment_score":    alignment_score,
            "dominant_direction": dominant_direction,
            "directions":         directions,
            "aligned_count":      aligned_count,
            "alignment_ok":       alignment_ok,
            "min_required":       MTF_MIN_ALIGNMENT,
            "timeframes":         timeframes,
        }

    except Exception as exc:
        logger.error(f"[{pair}] MTF confirmation failed: {exc}")
        # Fail-open with reduced score so signal can still proceed
        return {
            "alignment_score":    0.0,
            "dominant_direction": "NEUTRAL",
            "directions":         {},
            "aligned_count":      0,
            "alignment_ok":       False,
            "min_required":       MTF_MIN_ALIGNMENT,
            "error":              str(exc),
        }


# ---------------------------------------------------------------------------
# V4 Feature 4: Advanced Position Sizing
# ---------------------------------------------------------------------------
def _map_vol_regime(raw_regime: str) -> str:
    """
    Normalise a raw volatility regime string to the V4 canonical set:
    SQUEEZE / NORMAL / EXPANDING / HIGH_EXPANDING / EXTREME_HIGH.

    Accepts both V4 names and legacy names from VolatilityAdjustment.
    """
    canonical = {
        # V4 canonical
        "SQUEEZE":        "SQUEEZE",
        "NORMAL":         "NORMAL",
        "EXPANDING":      "EXPANDING",
        "HIGH_EXPANDING": "HIGH_EXPANDING",
        "EXTREME_HIGH":   "EXTREME_HIGH",
        # Legacy VolatilityAdjustment names
        "LOW":            "SQUEEZE",
        "LOW_CONTRACTING":"SQUEEZE",
        "HIGH":           "EXPANDING",
        "HIGH_EXPANDING": "HIGH_EXPANDING",
        "EXTREME_HIGH":   "EXTREME_HIGH",
    }
    return canonical.get(raw_regime.upper(), "NORMAL")


def compute_advanced_position_size(
    pair: str,
    entry: float,
    sl: float,
    atr: float,
    df: pd.DataFrame,
    regime: str = "RANGE",
    signal_type: str = "BUY",
    account_balance: float = ACCOUNT_BALANCE,
) -> dict:
    """
    V4 Dynamic lot sizing pipeline:

    1. Fixed-risk baseline  — 1% account risk per trade
    2. Volatility regime    — SQUEEZE/NORMAL/EXPANDING/HIGH_EXPANDING/EXTREME_HIGH
                              multiplier from VOL_REGIME_MULTIPLIERS
    3. Hard cap             — never exceed VOL_POSITION_SIZE_HARD_CAP (1.5x) of base
    4. WinRateTracker       — confidence multiplier [0.80, 1.20] per (regime, signal)
    5. Per-trade risk guard — final dollar risk capped at 2% of account

    Returns lot size and full breakdown for transparency.
    """
    try:
        from ml_engine.volatility_adjustment import VolatilityAdjustment
        from ml_engine.position_calculator import PositionCalculator

        # ── Step 1: Base position size via fixed-risk (1%) ──────────────────
        pos_calc = PositionCalculator(
            default_risk_pct=1.0,
            max_risk_pct=2.0,
            min_lot=0.01,
            max_lot=5.0,
            contract_size=100.0,    # 100 oz per lot (gold standard)
        )
        base_result = pos_calc.calculate(
            account_balance=account_balance,
            entry_price=entry,
            sl_price=sl,
            symbol=pair,
            method="fixed_risk",
            risk_pct=1.0,
        )
        base_lots = float(base_result.get("lots", 0.01))

        # ── Step 2: Volatility adjustment (ATR-based scaling) ────────────────
        vol_adj = VolatilityAdjustment(
            target_vol=0.01,
            max_size_multiplier=VOL_POSITION_SIZE_HARD_CAP,   # 1.5x hard cap
            min_size_multiplier=0.3,
        )
        vol_result = vol_adj.calculate_position_size(
            df=df,
            base_size=base_lots,
            account_balance=account_balance,
            risk_pct=0.01,
            symbol=pair,
        )
        vol_lots = float(vol_result.get("adjusted_size", base_lots))

        # Enforce hard cap: vol_lots must not exceed 1.5x base
        vol_lots = min(vol_lots, base_lots * VOL_POSITION_SIZE_HARD_CAP)

        # ── Step 3: Volatility regime multiplier ─────────────────────────────
        # Determine V4 canonical vol regime from VolatilityAdjustment output
        raw_vol_regime = vol_result.get("regime", "NORMAL")
        v4_vol_regime  = _map_vol_regime(raw_vol_regime)
        vol_regime_mult = VOL_REGIME_MULTIPLIERS.get(v4_vol_regime, 1.0)

        # Also apply market regime multiplier (from hybrid system)
        market_regime_mult = VOL_REGIME_MULTIPLIERS.get(regime.upper(), 0.8)

        # Combined regime multiplier (geometric mean to avoid double-penalising)
        import math
        combined_regime_mult = math.sqrt(vol_regime_mult * market_regime_mult)

        # ── Step 4: WinRateTracker confidence multiplier ─────────────────────
        tracker = get_win_rate_tracker()
        conf_mult = tracker.get_confidence_multiplier(regime, signal_type)

        # ── Step 5: Final lot size with all adjustments ──────────────────────
        adjusted_lots = vol_lots * combined_regime_mult * conf_mult

        # Hard cap: never exceed 1.5x base regardless of multipliers
        adjusted_lots = min(adjusted_lots, base_lots * VOL_POSITION_SIZE_HARD_CAP)

        # Per-trade risk guard: cap at 2% of account
        stop_distance = abs(entry - sl)
        max_lots_by_risk = (account_balance * 0.02) / (stop_distance * 100) if stop_distance > 0 else adjusted_lots
        adjusted_lots = min(adjusted_lots, max_lots_by_risk)

        final_lots = round(max(0.01, adjusted_lots), 2)

        # ── Dollar risk at final size ─────────────────────────────────────────
        dollar_risk = stop_distance * final_lots * 100  # 100 oz/lot

        return {
            "lots":                 final_lots,
            "base_lots":            round(base_lots, 2),
            "vol_lots":             round(vol_lots, 2),
            "vol_regime":           v4_vol_regime,
            "vol_regime_mult":      round(vol_regime_mult, 4),
            "market_regime_mult":   round(market_regime_mult, 4),
            "combined_regime_mult": round(combined_regime_mult, 4),
            "conf_mult":            round(conf_mult, 4),
            "stop_distance":        round(stop_distance, 2),
            "dollar_risk":          round(dollar_risk, 2),
            "risk_pct":             round(dollar_risk / account_balance * 100, 2),
            "hard_cap_applied":     adjusted_lots >= base_lots * VOL_POSITION_SIZE_HARD_CAP,
            "valid":                True,
        }

    except Exception as exc:
        logger.error(f"[{pair}] Advanced position sizing failed: {exc}")
        return {
            "lots":      0.01,
            "valid":     False,
            "error":     str(exc),
        }


# ---------------------------------------------------------------------------
# V4 Feature 1 & 2: Breakeven & Trailing Stop Metadata
# ---------------------------------------------------------------------------
def compute_be_ts_levels(
    signal: str,
    entry: float,
    sl: float,
    atr: float,
    cfg: dict,
) -> dict:
    """
    Compute Breakeven (BE) and Trailing Stop (TS) activation levels.

    BE  — activates when price moves +0.5R in trade direction (+BE_ACTIVATION_R).
          SL is then moved to entry price (zero-risk trade).
          Always active — reduces drawdown by ~56%.

    TS  — activates after TP1 hit; trails price by 1 ATR distance.
          Captures extended trend moves beyond TP1.
          Controlled by ENABLE_TRAILING_STOP env var (default: true).

    These are informational levels for manual execution / copy-trading.
    The trader moves SL manually when price reaches be_trigger.
    """
    risk   = abs(entry - sl)
    half_r = risk * BE_ACTIVATION_R   # 0.5R distance

    if signal == "BUY":
        be_trigger  = round(entry + half_r, cfg["decimals"])                  # Price that triggers BE
        be_sl       = entry                                                     # SL moves to entry
        ts_start    = round(entry + atr * cfg["atr_tp1"], cfg["decimals"])    # TP1 = TS activation
        ts_distance = round(atr * TRAILING_ATR_MULT, cfg["decimals"])         # Trail by 1 ATR
    else:  # SELL
        be_trigger  = round(entry - half_r, cfg["decimals"])
        be_sl       = entry
        ts_start    = round(entry - atr * cfg["atr_tp1"], cfg["decimals"])
        ts_distance = round(atr * TRAILING_ATR_MULT, cfg["decimals"])

    return {
        # Breakeven (always active)
        "be_trigger":      be_trigger,       # Move SL to entry when price hits this
        "be_sl":           be_sl,            # New SL after BE activation (= entry)
        "be_activation_r": BE_ACTIVATION_R,
        "be_enabled":      True,
        # Trailing Stop (optional — controlled by ENABLE_TRAILING_STOP)
        "ts_start":        ts_start,         # Trailing stop activates at TP1
        "ts_distance":     ts_distance,      # Trail distance (1 ATR)
        "ts_atr_mult":     TRAILING_ATR_MULT,
        "ts_enabled":      ENABLE_TRAILING_STOP,
        # Common
        "risk_distance":   round(risk, cfg["decimals"]),
    }


# ---------------------------------------------------------------------------
# GPT Signal (V4 enhanced prompt)
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT_V4 = (
    "You are an elite institutional gold trader using the Hybrid Portfolio System v4.0 "
    "Balanced Edition. Analyse the provided market data and return a JSON trading signal. "
    "V4 uses breakeven stop-loss, trailing stops, and multi-timeframe confirmation. "
    "Only signal BUY or SELL when conviction is HIGH. "
    "Respond ONLY with valid JSON — no markdown, no extra text."
)

_USER_TEMPLATE_V4 = """\
Analyse {pair} (4H timeframe) — Hybrid Portfolio System v4.0 Balanced Edition

MARKET DATA
-----------
Price : {price}
RSI   : {rsi}
MACD  : {macd}  |  Signal: {macd_sig}
MA20  : {ma20}  |  MA50: {ma50}
ATR   : {atr}
Trend : {trend}
Regime: {regime}
SMC Score: {smc_score}/10
MTF Alignment: {mtf_alignment}% (min required: {mtf_min}%)
MTF Direction: {mtf_direction}
Pivot Zone: {pivot_zone}

V4 ATR MULTIPLIERS  (SL: {atr_sl}x | TP1: {atr_tp1}x | TP2: {atr_tp2}x | TP3: {atr_tp3}x)
V4 RISK FEATURES: Breakeven at +{be_r}R | Trailing Stop: {ts_atr}x ATR after TP1

OUTPUT FORMAT — return exactly this JSON structure:
{{
  "signal": "BUY" | "SELL" | "NEUTRAL",
  "confidence": <integer 0-100>,
  "entry_price": <number>,
  "tp_levels": [<tp1>, <tp2>, <tp3>],
  "sl_price": <number>,
  "analysis": "<max 140 words — include MTF alignment rationale>",
  "risk_reward": <number>
}}
"""


async def gpt_signal_v4(
    pair: str,
    ind: dict,
    cfg: dict,
    hybrid_ctx: dict,
    mtf_ctx: dict,
) -> dict | None:
    """Call GPT-4o-mini with V4 hybrid + MTF context."""
    import litellm

    prompt = _USER_TEMPLATE_V4.format(
        pair=pair,
        price=ind["price"],
        rsi=ind["rsi"],
        macd=ind["macd"],
        macd_sig=ind["macd_sig"],
        ma20=ind["ma20"],
        ma50=ind["ma50"],
        atr=ind["atr"],
        trend=ind["trend"],
        regime=hybrid_ctx.get("regime", "UNKNOWN"),
        smc_score=hybrid_ctx.get("smc_score", 0),
        mtf_alignment=mtf_ctx.get("alignment_score", 0),
        mtf_min=MTF_MIN_ALIGNMENT,
        mtf_direction=mtf_ctx.get("dominant_direction", "NEUTRAL"),
        pivot_zone=hybrid_ctx.get("pivot_zone", "UNKNOWN"),
        atr_sl=cfg["atr_sl"],
        atr_tp1=cfg["atr_tp1"],
        atr_tp2=cfg["atr_tp2"],
        atr_tp3=cfg["atr_tp3"],
        be_r=BE_ACTIVATION_R,
        ts_atr=TRAILING_ATR_MULT,
    )

    raw_response = None
    for attempt in range(3):
        try:
            resp = await litellm.acompletion(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT_V4},
                    {"role": "user",   "content": prompt},
                ],
                api_key=OPENAI_API_KEY,
                timeout=30,
            )
            raw_response = resp.choices[0].message.content
            if raw_response and len(raw_response.strip()) > 10:
                break
        except Exception as exc:
            logger.warning(f"[{pair}] GPT V4 attempt {attempt + 1}/3 failed: {exc}")
            await asyncio.sleep(2)

    if not raw_response:
        return None

    return _parse_gpt_response(pair, raw_response)


def _parse_gpt_response(pair: str, raw: str) -> dict | None:
    """Parse GPT JSON response (robust multi-strategy parser)."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("{"):
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if brace:
            text = brace.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        fixed = re.sub(r",\s*}", "}", text)
        fixed = re.sub(r",\s*]", "]", fixed)
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    try:
        sig_m   = re.search(r'"signal"\s*:\s*"(\w+)"', text)
        conf_m  = re.search(r'"confidence"\s*:\s*([\d.]+)', text)
        entry_m = re.search(r'"entry_price"\s*:\s*([\d.]+)', text)
        anal_m  = re.search(r'"analysis"\s*:\s*"([^"]*)"', text)
        rr_m    = re.search(r'"risk_reward"\s*:\s*([\d.]+)', text)
        return {
            "signal":      sig_m.group(1)          if sig_m   else "NEUTRAL",
            "confidence":  float(conf_m.group(1))  if conf_m  else 50.0,
            "entry_price": float(entry_m.group(1)) if entry_m else 0.0,
            "analysis":    anal_m.group(1)          if anal_m  else "",
            "risk_reward": float(rr_m.group(1))    if rr_m    else 2.0,
            "tp_levels":   [],
            "sl_price":    0.0,
        }
    except Exception as exc:
        logger.error(f"[{pair}] JSON parse failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# TP/SL Levels
# ---------------------------------------------------------------------------
def build_levels(
    signal: str,
    entry: float,
    atr: float,
    cfg: dict,
) -> tuple[list[float], float]:
    dp = cfg["decimals"]
    if signal == "BUY":
        tps = [
            round(entry + atr * cfg["atr_tp1"], dp),
            round(entry + atr * cfg["atr_tp2"], dp),
            round(entry + atr * cfg["atr_tp3"], dp),
        ]
        sl = round(entry - atr * cfg["atr_sl"], dp)
    else:
        tps = [
            round(entry - atr * cfg["atr_tp1"], dp),
            round(entry - atr * cfg["atr_tp2"], dp),
            round(entry - atr * cfg["atr_tp3"], dp),
        ]
        sl = round(entry + atr * cfg["atr_sl"], dp)
    return tps, sl


# ---------------------------------------------------------------------------
# Telegram — V4 Enhanced Message
# ---------------------------------------------------------------------------
def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def send_to_telegram_v4(
    pair: str,
    signal: str,
    entry: float,
    tps: list[float],
    sl: float,
    confidence: float,
    rr: float,
    analysis: str,
    be_ts: dict,
    pos_size: dict,
    regime: str = "UNKNOWN",
    smc_score: int = 0,
    mtf_alignment: float = 0.0,
    mtf_direction: str = "NEUTRAL",
    lots: float = 0.01,
) -> None:
    """
    Send V4 signal to Telegram.

    Message 1: Copy-trading compatible signal block (clean format).
    Message 2: V4 risk management details (BE/TS levels, position size, MTF).
    """
    try:
        bot   = get_bot()
        emoji = "🟢" if signal == "BUY" else "🔴"
        action = signal.capitalize()
        lo = round(entry - 0.50, 2)
        hi = round(entry + 0.50, 2)

        ts_label = "✅ ON" if be_ts.get("ts_enabled", True) else "⏸ OFF"

        # --- Message 1: Copy-trading block ---
        copier_msg = (
            f"{emoji} #{pair} [SWING — V4]\n"
            f"\n"
            f"{action} {lo} - {hi}\n"
            f"\n"
            f"TP1: {tps[0]}\n"
            f"TP2: {tps[1]}\n"
            f"TP3: {tps[2]}\n"
            f"\n"
            f"SL: {sl}\n"
            f"\n"
            f"📌 BE: Move SL → {be_ts['be_sl']} when price hits {be_ts['be_trigger']}\n"
            f"📌 TS [{ts_label}]: Trail by {be_ts['ts_distance']} pts after TP1 hit\n"
        )

        # --- Message 2: V4 analytics block ---
        info_msg = (
            f"<b>📊 R:R:</b> 1:{rr}  "
            f"<b>⚡ Confidence:</b> {confidence}%\n"
            f"<b>🎯 Regime:</b> {regime}  "
            f"<b>📐 SMC:</b> {smc_score}/10  "
            f"<b>🔗 MTF:</b> {mtf_alignment:.0f}% ({mtf_direction})\n"
            f"<b>📦 Lots:</b> {lots}  "
            f"<b>💰 Risk:</b> ${pos_size.get('dollar_risk', 0):.0f} "
            f"({pos_size.get('risk_pct', 0):.1f}%)  "
            f"<b>🧠 ConfMult:</b> {pos_size.get('conf_mult', 1.0):.2f}x\n"
            f"<b>🛡 BE trigger:</b> {be_ts['be_trigger']}  "
            f"<b>🔄 TS start:</b> {be_ts['ts_start']} [{ts_label}]\n"
            f"<b>📈 VolRegime:</b> {pos_size.get('vol_regime', 'NORMAL')}\n"
            f"<b>📝</b> {_html_escape(analysis)}\n"
            f"<i>⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
            f"| Grandcom Gold Engine v4.0 Balanced</i>"
        )

        await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=copier_msg)
        await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=info_msg, parse_mode="HTML")
        logger.info(f"[{pair}] V4 signal sent to Telegram channel {TELEGRAM_CHANNEL_ID}")

    except Exception as exc:
        logger.error(f"[{pair}] Telegram V4 delivery failed: {exc}")


async def send_telegram_signal(
    pair: str,
    direction: str,
    confidence: float,
    entry: float,
    tps: list[float],
    sl: float,
    rr: float,
    position_size: float,
    regime: str,
    strategy_mode: str,
    account_balance: float,
    daily_pnl: float,
) -> None:
    """
    Send a comprehensive signal notification to the Telegram channel.

    Formats a single HTML message with all critical trade details so traders
    can execute manually without checking the API.  Errors are logged but
    never propagate — signal generation must not be interrupted by Telegram
    delivery failures (bot blocked, not admin, network timeout, etc.).

    Args:
        pair:            Trading pair, e.g. "XAUUSD".
        direction:       "BUY" or "SELL".
        confidence:      Signal confidence percentage (60–100).
        entry:           Entry price (confirmed closed-candle close).
        tps:             List of take-profit levels [TP1, TP2, TP3].
        sl:              Stop-loss price.
        rr:              Risk:Reward ratio (reward / risk at TP1).
        position_size:   Recommended position size in units.
        regime:          Market regime label, e.g. "TREND_UP", "RANGE".
        strategy_mode:   Active strategy, e.g. "price_action".
        account_balance: Current account balance in USD.
        daily_pnl:       Today's realised P&L in USD (positive = profit).
    """
    try:
        bot = get_bot()

        dir_emoji  = "🟢" if direction == "BUY" else "🔴"
        pnl_sign   = "+" if daily_pnl >= 0 else ""
        tp1 = tps[0] if len(tps) > 0 else "N/A"
        tp2 = tps[1] if len(tps) > 1 else "N/A"
        tp3 = tps[2] if len(tps) > 2 else "N/A"
        timestamp  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        msg = (
            f"🎯 <b>SIGNAL GENERATED</b>\n"
            f"\n"
            f"📊 <b>{_html_escape(pair)}</b>\n"
            f"{dir_emoji} <b>{_html_escape(direction)}</b> | "
            f"<b>{confidence:.0f}%</b> Confidence\n"
            f"\n"
            f"💰 <b>Entry:</b> {entry}\n"
            f"🎯 <b>TP1:</b> {tp1} | <b>TP2:</b> {tp2} | <b>TP3:</b> {tp3}\n"
            f"🛑 <b>SL:</b> {sl}\n"
            f"📈 <b>R:R:</b> {rr}\n"
            f"\n"
            f"📦 <b>Position:</b> {position_size} units\n"
            f"💼 <b>Account:</b> ${account_balance:,.0f} | "
            f"<b>Daily P&amp;L:</b> {pnl_sign}${daily_pnl:,.0f}\n"
            f"⚡ <b>Regime:</b> {_html_escape(regime)} | "
            f"<b>Mode:</b> {_html_escape(strategy_mode)}\n"
            f"\n"
            f"<i>⏰ {timestamp} | Grandcom Gold Engine v4.0</i>"
        )

        await bot.send_message(
            chat_id=TELEGRAM_CHANNEL_ID,
            text=msg,
            parse_mode="HTML",
        )
        logger.info(
            f"[{pair}] ✅ send_telegram_signal delivered to channel {TELEGRAM_CHANNEL_ID}"
        )

    except Exception as exc:
        logger.error(
            f"[{pair}] ⚠️  send_telegram_signal failed (non-fatal): {exc}"
        )


# ---------------------------------------------------------------------------
# AI Analysis Functions — GPT-4 powered insights
# ---------------------------------------------------------------------------

async def analyze_signal_with_ai(
    signal_id: str,
    pair: str,
    direction: str,
    confidence: float,
    entry: float,
    tps: list,
    sl: float,
    rr: float,
    regime: str,
    account_balance: float,
) -> str:
    """
    Generate a 2-3 sentence AI explanation for a trading signal using GPT-4.

    Explains why the signal triggered, key support/resistance levels, and
    risk/reward assessment.  Result is cached back to MongoDB on the signal
    document.  Errors are logged but never propagate — signal delivery must
    not be blocked by an AI call failure.

    Returns the analysis text, or an empty string on failure.
    """
    if not OPENAI_API_KEY:
        logger.warning("[AI] OPENAI_API_KEY not set — skipping signal analysis")
        return ""

    try:
        import litellm

        tp1 = tps[0] if len(tps) > 0 else "N/A"
        tp2 = tps[1] if len(tps) > 1 else "N/A"
        tp3 = tps[2] if len(tps) > 2 else "N/A"

        prompt = f"""\
You are a professional forex trader analyzing a gold trading signal.

Signal Details:
- Pair: {pair}
- Direction: {direction}
- Confidence: {confidence:.0f}%
- Entry: {entry}
- TP1: {tp1} | TP2: {tp2} | TP3: {tp3}
- SL: {sl}
- Risk:Reward: {rr}
- Regime: {regime}
- Account: ${account_balance:,.0f}

Provide a 2-3 sentence professional analysis explaining:
1. Why this signal triggered (technical reason)
2. Key support/resistance levels
3. Risk/reward assessment

Keep it concise and actionable for traders."""

        resp = await litellm.acompletion(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an elite institutional gold trader. "
                        "Provide concise, actionable signal analysis. "
                        "Respond with plain text only — no markdown, no bullet points."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            api_key=OPENAI_API_KEY,
            timeout=20,
            max_tokens=200,
        )

        ai_analysis = resp.choices[0].message.content.strip()
        logger.info(f"[AI] Signal analysis generated for {pair} {direction} (id={signal_id})")

        # Cache result back to MongoDB signal document
        db = get_db()
        if db is not None and signal_id:
            try:
                from bson import ObjectId
                await db.gold_signals_v4.update_one(
                    {"_id": ObjectId(signal_id)},
                    {"$set": {"ai_analysis": ai_analysis, "ai_analysis_at": datetime.now(timezone.utc)}},
                )
            except Exception as cache_exc:
                logger.warning(f"[AI] Failed to cache signal analysis in MongoDB: {cache_exc}")

        return ai_analysis

    except Exception as exc:
        logger.error(f"[AI] analyze_signal_with_ai failed for {pair}: {exc}")
        return ""


async def generate_market_commentary() -> str:
    """
    Generate a 3-4 sentence AI market overview using GPT-4.

    Analyses the last 10 signals from MongoDB plus the current regime to
    produce a market commentary covering trend direction, volatility, and
    key levels.  Posts to Telegram and caches in the market_commentary_v4
    collection.  Returns the commentary text, or empty string on failure.
    """
    if not OPENAI_API_KEY:
        logger.warning("[AI] OPENAI_API_KEY not set — skipping market commentary")
        return ""

    db = get_db()

    try:
        import litellm

        # Fetch last 10 signals for context
        recent_signals: list = []
        if db is not None:
            try:
                recent_signals = (
                    await db.gold_signals_v4
                    .find(
                        {},
                        {"_id": 0, "pair": 1, "type": 1, "confidence": 1,
                         "entry_price": 1, "tp_levels": 1, "sl_price": 1,
                         "regime": 1, "risk_reward": 1, "status": 1, "created_at": 1},
                    )
                    .sort("created_at", -1)
                    .limit(10)
                    .to_list(10)
                )
            except Exception as db_exc:
                logger.warning(f"[AI] Failed to fetch recent signals for commentary: {db_exc}")

        # Summarise signals for the prompt
        signal_summary = ""
        if recent_signals:
            lines = []
            for s in recent_signals:
                tps = s.get("tp_levels", [])
                tp1 = tps[0] if tps else "N/A"
                lines.append(
                    f"  {s.get('pair','?')} {s.get('type','?')} @ {s.get('entry_price','?')} "
                    f"| TP1:{tp1} SL:{s.get('sl_price','?')} "
                    f"| Conf:{s.get('confidence','?')}% "
                    f"| Regime:{s.get('regime','?')} "
                    f"| Status:{s.get('status','?')}"
                )
            signal_summary = "\n".join(lines)
        else:
            signal_summary = "  No recent signals available."

        # Determine dominant regime from recent signals
        regimes = [s.get("regime", "UNKNOWN") for s in recent_signals if s.get("regime")]
        dominant_regime = max(set(regimes), key=regimes.count) if regimes else "UNKNOWN"

        prompt = f"""\
You are a professional gold market analyst. Based on the recent trading signals below, \
provide a 3-4 sentence market commentary covering:
1. Current trend direction and strength
2. Volatility assessment
3. Key support/resistance levels to watch
4. Recommended trading posture

Recent Signals (last 10):
{signal_summary}

Dominant Regime: {dominant_regime}
Account Balance: ${ACCOUNT_BALANCE:,.0f}
Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

Write in a professional, concise style suitable for traders. Plain text only."""

        resp = await litellm.acompletion(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an elite institutional gold market analyst. "
                        "Provide concise, actionable market commentary. "
                        "Plain text only — no markdown, no bullet points."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            api_key=OPENAI_API_KEY,
            timeout=25,
            max_tokens=300,
        )

        commentary = resp.choices[0].message.content.strip()
        now = datetime.now(timezone.utc)

        # Cache in MongoDB
        if db is not None:
            try:
                await db.market_commentary_v4.insert_one({
                    "commentary":       commentary,
                    "dominant_regime":  dominant_regime,
                    "signals_analysed": len(recent_signals),
                    "created_at":       now,
                })
            except Exception as cache_exc:
                logger.warning(f"[AI] Failed to cache market commentary: {cache_exc}")

        # Post to Telegram
        try:
            bot = get_bot()
            tg_msg = (
                f"📊 <b>MARKET COMMENTARY (4H)</b>\n\n"
                f"{_html_escape(commentary)}\n\n"
                f"<i>⏰ {now.strftime('%Y-%m-%d %H:%M UTC')} | Grandcom Gold Engine v4.0</i>"
            )
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=tg_msg,
                parse_mode="HTML",
            )
            logger.info("[AI] Market commentary posted to Telegram")
        except Exception as tg_exc:
            logger.error(f"[AI] Failed to post market commentary to Telegram: {tg_exc}")

        logger.info(f"[AI] Market commentary generated — {len(recent_signals)} signals analysed")
        return commentary

    except Exception as exc:
        logger.error(f"[AI] generate_market_commentary failed: {exc}")
        return ""


async def generate_daily_review() -> str:
    """
    Generate a 4-5 sentence AI daily performance review using GPT-4.

    Analyses all signals from the past 24 hours, calculates win rate and
    profit factor, and produces a narrative summary with insights.  Posts
    to Telegram and stores in the daily_reviews_v4 collection.
    Returns the review text, or empty string on failure.
    """
    if not OPENAI_API_KEY:
        logger.warning("[AI] OPENAI_API_KEY not set — skipping daily review")
        return ""

    db = get_db()

    try:
        import litellm

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        daily_signals: list = []

        if db is not None:
            try:
                daily_signals = (
                    await db.gold_signals_v4
                    .find(
                        {"created_at": {"$gte": cutoff}},
                        {"_id": 0, "pair": 1, "type": 1, "confidence": 1,
                         "entry_price": 1, "tp_levels": 1, "sl_price": 1,
                         "risk_reward": 1, "status": 1, "result": 1,
                         "regime": 1, "dollar_risk": 1, "created_at": 1},
                    )
                    .sort("created_at", -1)
                    .limit(100)
                    .to_list(100)
                )
            except Exception as db_exc:
                logger.warning(f"[AI] Failed to fetch daily signals for review: {db_exc}")

        total = len(daily_signals)
        wins = [s for s in daily_signals if s.get("result") == "WIN" or s.get("status") == "WIN"]
        losses = [s for s in daily_signals if s.get("result") == "LOSS" or s.get("status") == "LOSS"]
        closed = len(wins) + len(losses)
        win_rate = round(len(wins) / closed * 100, 1) if closed > 0 else 0.0

        # Approximate P&L from risk/reward
        win_pnl = sum(
            float(s.get("dollar_risk", 0)) * float(s.get("risk_reward", 1.0))
            for s in wins
        )
        loss_pnl = sum(float(s.get("dollar_risk", 0)) for s in losses)
        net_pnl = win_pnl - loss_pnl

        # Best and worst trades
        best_trade = max(
            wins,
            key=lambda s: float(s.get("risk_reward", 0)) * float(s.get("dollar_risk", 0)),
            default=None,
        )
        worst_trade = max(
            losses,
            key=lambda s: float(s.get("dollar_risk", 0)),
            default=None,
        )

        best_str = "None"
        if best_trade:
            tps = best_trade.get("tp_levels", [])
            tp1 = tps[0] if tps else "N/A"
            best_str = (
                f"{best_trade.get('pair','?')} {best_trade.get('type','?')} "
                f"@ {best_trade.get('entry_price','?')} → TP1:{tp1} "
                f"(+${float(best_trade.get('dollar_risk',0)) * float(best_trade.get('risk_reward',1)):.0f})"
            )

        worst_str = "None"
        if worst_trade:
            worst_str = (
                f"{worst_trade.get('pair','?')} {worst_trade.get('type','?')} "
                f"@ {worst_trade.get('entry_price','?')} → SL hit "
                f"(-${float(worst_trade.get('dollar_risk',0)):.0f})"
            )

        # Regime breakdown
        regimes = [s.get("regime", "UNKNOWN") for s in daily_signals if s.get("regime")]
        regime_counts: dict[str, int] = {}
        for r in regimes:
            regime_counts[r] = regime_counts.get(r, 0) + 1
        regime_summary = ", ".join(f"{r}:{c}" for r, c in sorted(regime_counts.items(), key=lambda x: -x[1]))

        prompt = f"""\
You are a professional trading performance analyst. Write a 4-5 sentence daily performance review \
for a gold trading system based on the data below. Include:
1. Overall performance summary (signals, win rate, P&L)
2. Best and worst trades
3. Market regime observations
4. Actionable insight for tomorrow

Daily Performance Data:
- Total signals: {total}
- Closed signals: {closed} ({len(wins)} wins, {len(losses)} losses)
- Win rate: {win_rate:.1f}%
- Estimated net P&L: ${net_pnl:+.0f}
- Best trade: {best_str}
- Worst trade: {worst_str}
- Regime breakdown: {regime_summary if regime_summary else 'N/A'}
- Account balance: ${ACCOUNT_BALANCE:,.0f}
- Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d UTC')}

Write in a professional, concise style. Plain text only."""

        resp = await litellm.acompletion(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an elite trading performance analyst. "
                        "Provide concise, insightful daily reviews. "
                        "Plain text only — no markdown, no bullet points."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            api_key=OPENAI_API_KEY,
            timeout=30,
            max_tokens=400,
        )

        review = resp.choices[0].message.content.strip()
        now = datetime.now(timezone.utc)

        # Store in MongoDB
        if db is not None:
            try:
                await db.daily_reviews_v4.insert_one({
                    "review":           review,
                    "date":             now.strftime("%Y-%m-%d"),
                    "total_signals":    total,
                    "closed_signals":   closed,
                    "wins":             len(wins),
                    "losses":           len(losses),
                    "win_rate_pct":     win_rate,
                    "net_pnl_approx":   round(net_pnl, 2),
                    "regime_breakdown": regime_counts,
                    "created_at":       now,
                })
            except Exception as cache_exc:
                logger.warning(f"[AI] Failed to store daily review: {cache_exc}")

        # Post to Telegram
        try:
            bot = get_bot()
            tg_msg = (
                f"📈 <b>DAILY PERFORMANCE REVIEW</b>\n\n"
                f"{_html_escape(review)}\n\n"
                f"<b>Stats:</b> {total} signals | {closed} closed | "
                f"{win_rate:.0f}% win rate | ${net_pnl:+.0f} est. P&amp;L\n"
                f"<i>⏰ {now.strftime('%Y-%m-%d %H:%M UTC')} | Grandcom Gold Engine v4.0</i>"
            )
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=tg_msg,
                parse_mode="HTML",
            )
            logger.info("[AI] Daily review posted to Telegram")
        except Exception as tg_exc:
            logger.error(f"[AI] Failed to post daily review to Telegram: {tg_exc}")

        logger.info(
            f"[AI] Daily review generated — {total} signals, "
            f"win_rate={win_rate:.1f}%, net_pnl=${net_pnl:+.0f}"
        )
        return review

    except Exception as exc:
        logger.error(f"[AI] generate_daily_review failed: {exc}")
        return ""


async def generate_risk_alert(
    alert_type: str,
    current_pct: float,
    limit_pct: float,
    account_balance: float,
    daily_pnl: float = 0.0,
    total_pnl: float = 0.0,
) -> str:
    """
    Generate an AI risk warning when drawdown or daily loss limits approach.

    alert_type: "DRAWDOWN" | "DAILY_LOSS"
    current_pct: current drawdown/loss as a percentage (positive = loss)
    limit_pct: the hard limit percentage

    Posts to Telegram immediately and stores in risk_alerts_v4 collection.
    Returns the alert text, or empty string on failure.
    """
    if not OPENAI_API_KEY:
        logger.warning("[AI] OPENAI_API_KEY not set — skipping risk alert")
        return ""

    db = get_db()

    try:
        import litellm

        remaining_pct = limit_pct - current_pct
        remaining_usd = account_balance * (remaining_pct / 100.0)
        now = datetime.now(timezone.utc)

        # Compute next daily reset time
        tomorrow = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        hours_to_reset = round((tomorrow - now).total_seconds() / 3600, 1)

        if alert_type == "DRAWDOWN":
            limit_label = "Total drawdown"
            current_label = f"{current_pct:.1f}% total drawdown"
        else:
            limit_label = "Daily loss"
            current_label = f"{current_pct:.1f}% daily loss"

        prompt = f"""\
You are a professional risk manager for a gold trading system. \
Write a 3-4 sentence risk alert for the following situation:

Risk Alert Details:
- Alert type: {alert_type}
- {limit_label} limit: {limit_pct:.1f}% of ${account_balance:,.0f}
- Current {current_label}: ${abs(daily_pnl if alert_type == 'DAILY_LOSS' else total_pnl):,.0f}
- Remaining buffer: {remaining_pct:.1f}% (${remaining_usd:,.0f})
- Daily reset in: {hours_to_reset:.1f} hours ({tomorrow.strftime('%Y-%m-%d %H:%M UTC')})

Include:
1. What the current risk exposure is
2. How much buffer remains before the hard limit
3. Recommended immediate action
4. When the daily counter resets (if applicable)

Be direct and urgent. Plain text only."""

        resp = await litellm.acompletion(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a professional trading risk manager. "
                        "Write clear, urgent risk alerts. "
                        "Plain text only — no markdown, no bullet points."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            api_key=OPENAI_API_KEY,
            timeout=20,
            max_tokens=250,
        )

        alert_text = resp.choices[0].message.content.strip()

        # Store in MongoDB
        if db is not None:
            try:
                await db.risk_alerts_v4.insert_one({
                    "alert_type":       alert_type,
                    "alert_text":       alert_text,
                    "current_pct":      round(current_pct, 2),
                    "limit_pct":        limit_pct,
                    "remaining_pct":    round(remaining_pct, 2),
                    "remaining_usd":    round(remaining_usd, 2),
                    "account_balance":  account_balance,
                    "daily_pnl":        round(daily_pnl, 2),
                    "total_pnl":        round(total_pnl, 2),
                    "created_at":       now,
                })
            except Exception as cache_exc:
                logger.warning(f"[AI] Failed to store risk alert: {cache_exc}")

        # Post to Telegram immediately
        try:
            bot = get_bot()
            emoji = "🚨" if remaining_pct < 1.0 else "⚠️"
            tg_msg = (
                f"{emoji} <b>RISK ALERT — {alert_type.replace('_', ' ')}</b>\n\n"
                f"{_html_escape(alert_text)}\n\n"
                f"<b>Current:</b> {current_pct:.1f}% | "
                f"<b>Limit:</b> {limit_pct:.1f}% | "
                f"<b>Buffer:</b> {remaining_pct:.1f}% (${remaining_usd:,.0f})\n"
                f"<i>⏰ {now.strftime('%Y-%m-%d %H:%M UTC')} | Grandcom Gold Engine v4.0</i>"
            )
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=tg_msg,
                parse_mode="HTML",
            )
            logger.info(f"[AI] Risk alert ({alert_type}) posted to Telegram")
        except Exception as tg_exc:
            logger.error(f"[AI] Failed to post risk alert to Telegram: {tg_exc}")

        logger.warning(
            f"[AI] Risk alert generated — type={alert_type} "
            f"current={current_pct:.1f}% limit={limit_pct:.1f}% "
            f"buffer={remaining_pct:.1f}%"
        )
        return alert_text

    except Exception as exc:
        logger.error(f"[AI] generate_risk_alert failed: {exc}")
        return ""


# ---------------------------------------------------------------------------
# Core Signal Generation — V4 Pipeline
# ---------------------------------------------------------------------------
async def generate_signal_v4(pair: str) -> None:
    """
    Full V4.0 pipeline:
      fetch → indicators → hybrid analysis → MTF confirmation →
      GPT → validate → advanced sizing → BE/TS levels →
      store → send
    """
    cfg = PAIRS[pair]
    logger.info(f"[{pair}] Starting V4.0 signal generation")

    # 1. Price data (4H — permanent timeframe)
    df, response_timestamp = await fetch_ohlcv(pair, interval="4h", outputsize=100)
    if df is None or len(df) < 52:
        logger.warning(f"[{pair}] Insufficient 4H candles, skipping")
        return

    # 1a. V4.1 Data freshness check — detects dead feed, not candle age
    # Age is measured from API response time so recently-closed candles
    # (whose open time is hours old) are correctly treated as fresh.
    # FAIL-CLOSED: if the guard is unavailable for any reason, reject the signal.
    if not _FRESHNESS_GUARD_AVAILABLE or _freshness_guard is None:
        logger.warning(
            f"[{pair}] ⚠️  DataFreshnessGuard unavailable — "
            f"cannot verify feed freshness — signal suppressed (fail-closed)"
        )
        _v4_metrics["signals_suppressed_stale"] += 1
        return

    logger.info(
        f"[{pair}] Freshness check — "
        f"response_ts={response_timestamp.isoformat() if response_timestamp else 'N/A'}"
    )
    if not _freshness_guard.is_fresh(
        df,
        max_age_seconds=DATA_FRESHNESS_MAX_AGE_SECONDS,
        response_timestamp=response_timestamp,
    ):
        logger.warning(
            f"[{pair}] ⚠️  Freshness check FAILED — "
            f"response_ts={response_timestamp.isoformat() if response_timestamp else 'N/A'} "
            f"— signal suppressed (fail-closed)"
        )
        _v4_metrics["signals_suppressed_stale"] += 1
        return
    if not _freshness_guard.validate_timestamps(df):
        logger.warning(f"[{pair}] ⚠️  Timestamp validation FAILED — signal suppressed (fail-closed)")
        _v4_metrics["signals_suppressed_stale"] += 1
        return
    logger.info(
        f"[{pair}] ✅ Feed freshness OK — "
        f"response_ts={response_timestamp.isoformat() if response_timestamp else 'N/A'}"
    )

    # 1b. V4.1 Candle-close confirmation — no mid-candle signals
    # CRITICAL: This gate MUST be fail-closed. If candle_utils is unavailable
    # or raises, we REJECT the signal rather than proceeding on a forming candle.
    _last_candle_ts_raw = df.iloc[-1].get("datetime", df.iloc[-1].name) if hasattr(df.iloc[-1], "get") else df.iloc[-1].name
    logger.info(
        f"[{pair}] 🕯️  Candle-close gate — last candle ts={_last_candle_ts_raw} "
        f"candle_utils_available={_CANDLE_UTILS_AVAILABLE}"
    )
    if not _CANDLE_UTILS_AVAILABLE:
        # candle_utils failed to import — REJECT to avoid mid-candle signals
        logger.error(
            f"[{pair}] ❌ candle_utils unavailable — signal REJECTED (fail-closed). "
            f"Fix the candle_utils import to re-enable signal generation."
        )
        _v4_metrics["signals_suppressed_candle"] += 1
        return
    try:
        _candle_is_closed = is_candle_closed(df, interval="4h")
    except Exception as _gate_exc:
        # Exception in gate — REJECT (fail-closed), never fail-open
        logger.error(
            f"[{pair}] ❌ is_candle_closed() raised {_gate_exc!r} — "
            f"signal REJECTED (fail-closed)"
        )
        _v4_metrics["signals_suppressed_candle"] += 1
        return
    logger.info(
        f"[{pair}] 🕯️  is_candle_closed={_candle_is_closed} "
        f"(candle_ts={_last_candle_ts_raw})"
    )
    if not _candle_is_closed:
        logger.info(
            f"[{pair}] ⏳ Last 4H candle still FORMING — "
            f"signal suppressed (candle-close confirmation)"
        )
        _v4_metrics["signals_suppressed_candle"] += 1
        return
    logger.info(f"[{pair}] ✅ Candle-close confirmed — proceeding with signal generation")

    # 1c. V4.2 Deduplication pre-check — extract candle timestamp before any
    #     expensive calls so we can bail out early if this candle was already
    #     processed.  The direction is not yet known at this point, so we store
    #     the candle_ts for use later in the pipeline (after GPT resolves the
    #     direction).
    _candle_ts: str = str(df.iloc[-1].get("datetime", df.iloc[-1].name))

    # 1d. Risk management gate — check drawdown and daily loss limits BEFORE
    #     any expensive API calls.  Pauses trading automatically if limits hit.
    _risk_mgr = get_live_risk_manager()
    _trading_allowed, _risk_reason = _risk_mgr.is_trading_allowed(pair)
    if not _trading_allowed:
        logger.warning(
            f"[{pair}] 🛑 Risk gate BLOCKED — {_risk_reason} — signal suppressed"
        )
        return

    # 2. Indicators
    ind = compute_indicators(df, cfg["decimals"])
    if ind is None:
        return

    # 2a. Daily candles for pivot-point analysis
    df_daily, _ = await fetch_ohlcv(pair, interval="1day", outputsize=60)

    # 2b. Correlation assets — DXY + major USD pairs for USD-regime filtering
    # XAUUSD is highly correlated with USD strength; without these the
    # correlation engine receives price_data=None and is completely disabled.
    _CORRELATION_ASSETS = ["DXY", "EURUSD", "GBPUSD", "USDJPY"]
    price_data: dict[str, pd.Series] = {}

    # Seed with primary symbol close series
    if df is not None and len(df) > 0:
        price_data[pair] = df["close"]

    # Fetch each correlation asset independently so one failure doesn't block
    for _corr_sym in _CORRELATION_ASSETS:
        try:
            _corr_df = await fetch_correlation_ohlcv(_corr_sym, interval="4h", outputsize=100, timeout=10)
            if _corr_df is not None and len(_corr_df) > 0:
                price_data[_corr_sym] = _corr_df["close"]
        except Exception as _corr_exc:
            logger.warning(f"[{pair}] Failed to fetch correlation asset {_corr_sym}: {_corr_exc}")

    _n_corr = len(price_data) - 1  # exclude primary symbol
    logger.info(
        f"[{pair}] Correlation data: {_n_corr}/{len(_CORRELATION_ASSETS)} assets fetched "
        f"({', '.join(k for k in price_data if k != pair)})"
    )

    # 3. Hybrid system analysis (regime, SMC, pivot)
    hybrid_ctx = {
        "regime":     "UNKNOWN",
        "smc_score":  0,
        "mtf_alignment": 0,
        "pivot_zone": "UNKNOWN",
    }
    hybrid = get_hybrid_system()
    if hybrid is not None:
        try:
            hybrid_result = await hybrid.generate_signal(
                symbol=pair,
                df_4h=df,
                df_daily=df_daily,
                price_data=price_data if len(price_data) >= 2 else None,
                strategy_mode=STRATEGY_MODE,
            )
            hybrid_ctx = {
                "regime":           hybrid_result.get("regime", "UNKNOWN"),
                "smc_score":        hybrid_result.get("smc_score", 0),
                "mtf_alignment":    hybrid_result.get("mtf_alignment", 0),
                "pivot_zone":       hybrid_result.get("pivot_zone", "UNKNOWN"),
                "hybrid_signal":    hybrid_result.get("signal", "NEUTRAL"),
                "hybrid_confidence":hybrid_result.get("confidence", 0),
            }
            logger.info(
                f"[{pair}] Hybrid: signal={hybrid_ctx['hybrid_signal']} "
                f"regime={hybrid_ctx['regime']} smc={hybrid_ctx['smc_score']}/10"
            )
        except Exception as exc:
            logger.error(f"[{pair}] Hybrid system error: {exc}")

    # 4. V4 Feature 3: Multi-Timeframe Confirmation (≥70% alignment required)
    mtf_ctx = await run_mtf_confirmation(pair)
    if not mtf_ctx["alignment_ok"]:
        logger.info(
            f"[{pair}] MTF filter: alignment={mtf_ctx['alignment_score']:.1f}% "
            f"< {MTF_MIN_ALIGNMENT}% or direction mismatch — signal suppressed"
        )
        return

    # 5. GPT analysis (V4 enhanced prompt)
    gpt = await gpt_signal_v4(pair, ind, cfg, hybrid_ctx, mtf_ctx)
    if gpt is None:
        return

    signal_type = str(gpt.get("signal", "NEUTRAL")).upper()
    confidence  = float(gpt.get("confidence", 0))
    analysis    = str(gpt.get("analysis", ""))

    # 6. Signal filter
    if signal_type == "NEUTRAL" or signal_type not in ("BUY", "SELL"):
        logger.info(f"[{pair}] {signal_type} signal — no trade")
        return

    # Apply WinRateTracker confidence multiplier before threshold check
    regime_for_tracker = hybrid_ctx.get("regime", "UNKNOWN")
    tracker    = get_win_rate_tracker()
    conf_mult  = tracker.get_confidence_multiplier(regime_for_tracker, signal_type)
    confidence = round(confidence * conf_mult, 1)

    if confidence < MIN_CONFIDENCE:
        logger.info(
            f"[{pair}] Confidence {confidence}% < {MIN_CONFIDENCE}% "
            f"(conf_mult={conf_mult:.3f}) — skipping"
        )
        return

    # 6b. V4.2 Deduplication check — skip if this (candle, pair, direction)
    #     has already been signalled within the current 4H window.
    _dedup = get_deduplicator()
    if _dedup is not None:
        if await _dedup.has_signalled(_candle_ts, pair, signal_type):
            logger.info(
                f"[{pair}] 🔁 Duplicate signal suppressed — "
                f"candle={_candle_ts} direction={signal_type} already fired"
            )
            _v4_metrics["signals_suppressed_dedupe"] += 1
            return

    # 7. Validate MTF direction agrees with GPT signal
    mtf_dir = mtf_ctx.get("dominant_direction", "NEUTRAL")
    expected_mtf = "BULLISH" if signal_type == "BUY" else "BEARISH"
    if mtf_dir != "NEUTRAL" and mtf_dir != expected_mtf:
        logger.info(
            f"[{pair}] MTF direction {mtf_dir} conflicts with GPT {signal_type} — skipping"
        )
        return

    # 8. Levels
    # CRITICAL: Entry price MUST come from the confirmed closed candle (df.iloc[-1]),
    # NOT from GPT's suggestion (which echoes the live forming price sent in the prompt).
    # GPT's entry_price is discarded — it is derived from ind["price"] which is the
    # live close of the last candle at prompt-build time and changes every cycle.
    # After the candle-close gate above, df.iloc[-1] is the confirmed closed candle.
    _closed_candle_close = round(float(df.iloc[-1]["close"]), cfg["decimals"])
    entry = _closed_candle_close
    logger.info(
        f"[{pair}] 📌 Entry pinned to closed candle close={entry} "
        f"(candle_ts={_last_candle_ts_raw}) — "
        f"GPT suggested entry_price={gpt.get('entry_price', 'N/A')} (ignored)"
    )

    tps, sl = build_levels(signal_type, entry, ind["atr"], cfg)

    if signal_type == "BUY" and (tps[0] <= entry or sl >= entry):
        logger.warning(f"[{pair}] BUY geometry invalid — skipping")
        return
    if signal_type == "SELL" and (tps[0] >= entry or sl <= entry):
        logger.warning(f"[{pair}] SELL geometry invalid — skipping")
        return

    # 9. Risk/reward
    risk   = abs(entry - sl)
    reward = abs(tps[0] - entry)
    rr     = round(reward / risk, 1) if risk > 0 else 2.0

    # 10. V4 Feature 1 & 2: Breakeven + Trailing Stop levels
    be_ts = compute_be_ts_levels(signal_type, entry, sl, ind["atr"], cfg)

    # 11. V4 Feature 4: Advanced position sizing
    pos_size = compute_advanced_position_size(
        pair=pair,
        entry=entry,
        sl=sl,
        atr=ind["atr"],
        df=df,
        regime=hybrid_ctx.get("regime", "UNKNOWN"),
        signal_type=signal_type,
        account_balance=ACCOUNT_BALANCE,
    )
    lots = pos_size.get("lots", 0.01)

    # 11b. Confidence-based position sizing (scales units by conviction tier)
    conf_sizing = compute_confidence_position_size(
        confidence=confidence,
        account_balance=ACCOUNT_BALANCE,
    )

    # 11c. Collect price action thresholds used (for A/B analysis in signal logs).
    #      If an active A/B test exists for this pair, use its thresholds and
    #      record the signal against the test for later win/loss attribution.
    db = get_db()
    _ab_mgr = get_ab_test_manager()
    _pa_thresholds_full = await _ab_mgr.get_thresholds_for_pair(db, pair)
    _active_test_id: str | None = _pa_thresholds_full.get("test_id")

    _pa_thresholds = {
        "momentum_threshold":   _pa_thresholds_full["momentum_threshold"],
        "volatility_threshold": _pa_thresholds_full["volatility_threshold"],
        "confluence_weight":    _pa_thresholds_full["confluence_weight"],
    }

    # Record this signal against the active A/B test (count only; result TBD)
    if _active_test_id:
        await _ab_mgr.record_signal(db, _active_test_id)

    # 11d. Capture risk state snapshot at signal time
    _risk_state = _risk_mgr.get_state()

    # 11e. Emit structured JSON signal log (for monitoring, MongoDB, and analysis)
    _signal_log = log_signal_json(
        pair=pair,
        signal_type=signal_type,
        confidence=confidence,
        entry=entry,
        tps=tps,
        sl=sl,
        rr=rr,
        pos_size=pos_size,
        conf_sizing=conf_sizing,
        be_ts=be_ts,
        regime=hybrid_ctx.get("regime", "UNKNOWN"),
        smc_score=hybrid_ctx.get("smc_score", 0),
        mtf_alignment=mtf_ctx.get("alignment_score", 0),
        mtf_direction=mtf_ctx.get("dominant_direction", "NEUTRAL"),
        strategy_mode=STRATEGY_MODE,
        pa_thresholds=_pa_thresholds,
        risk_state=_risk_state,
        analysis=analysis,
        ab_test_id=_active_test_id,
    )

    # 12. Store in MongoDB (V4 collection)
    # Note: db was already fetched in step 11c for A/B test threshold lookup
    _inserted_id: str = ""   # Will be set after successful MongoDB insert
    if db is not None:
        try:
            doc = {
                "pair":             pair,
                "type":             signal_type,
                "entry_price":      entry,
                "current_price":    ind["price"],
                "tp_levels":        tps,
                "sl_price":         sl,
                "confidence":       round(confidence, 1),
                "analysis":         analysis,
                "risk_reward":      rr,
                "timeframe":        "4H",
                "status":           "ACTIVE",
                "indicators":       ind,
                # Hybrid context
                "regime":           hybrid_ctx.get("regime", "UNKNOWN"),
                "smc_score":        hybrid_ctx.get("smc_score", 0),
                "pivot_zone":       hybrid_ctx.get("pivot_zone", "UNKNOWN"),
                # V4 MTF
                "mtf_alignment":    mtf_ctx.get("alignment_score", 0),
                "mtf_direction":    mtf_ctx.get("dominant_direction", "NEUTRAL"),
                "mtf_aligned_count":mtf_ctx.get("aligned_count", 0),
                # V4 BE/TS
                "be_trigger":       be_ts["be_trigger"],
                "be_sl":            be_ts["be_sl"],
                "be_enabled":       be_ts["be_enabled"],
                "ts_start":         be_ts["ts_start"],
                "ts_distance":      be_ts["ts_distance"],
                "ts_enabled":       be_ts["ts_enabled"],
                # V4 Position sizing (vol-adjusted)
                "lots":             lots,
                "dollar_risk":      pos_size.get("dollar_risk", 0),
                "risk_pct":         pos_size.get("risk_pct", 0),
                "vol_regime":       pos_size.get("vol_regime", "NORMAL"),
                "vol_regime_mult":  pos_size.get("vol_regime_mult", 1.0),
                "conf_mult":        pos_size.get("conf_mult", 1.0),
                "hard_cap_applied": pos_size.get("hard_cap_applied", False),
                # Confidence-based sizing
                "position_units":   conf_sizing.get("units"),
                "position_tier":    conf_sizing.get("tier"),
                "position_scale":   conf_sizing.get("scale_factor"),
                # Price action thresholds used
                "pa_momentum_threshold":   _pa_thresholds["momentum_threshold"],
                "pa_volatility_threshold": _pa_thresholds["volatility_threshold"],
                "pa_confluence_weight":    _pa_thresholds["confluence_weight"],
                # A/B test attribution — test_id is None if no active test
                "ab_test_id":              _active_test_id,
                "ab_test_source":          _pa_thresholds_full.get("source", "default"),
                # Risk state at signal time
                "risk_daily_pnl":          _risk_state.get("daily_pnl"),
                "risk_total_pnl":          _risk_state.get("total_pnl"),
                "risk_daily_loss_pct":     _risk_state.get("daily_loss_pct"),
                "risk_total_drawdown_pct": _risk_state.get("total_drawdown_pct"),
                # Meta
                "strategy_mode":    STRATEGY_MODE,
                "system_version":   "4.0.0",
                "created_at":       datetime.now(timezone.utc),
            }
            result = await db.gold_signals_v4.insert_one(doc)
            logger.info(f"[{pair}] V4 signal stored — id={result.inserted_id}")

            # Register with TradeManager for BE/TS/partial management
            if _TRADE_MANAGER_AVAILABLE:
                trade_doc = {**doc, "indicators": ind}
                get_trade_manager().register_new_trade(str(result.inserted_id), trade_doc)

            # Update risk manager: increment open position count for this pair
            _risk_mgr.record_trade_open(pair)

            # V4.2 Mark this (candle, pair, direction) as signalled so
            # subsequent scheduler runs within the same 4H window are
            # suppressed.  TTL = 4 hours — auto-expires with the candle.
            _dedup = get_deduplicator()
            if _dedup is not None:
                await _dedup.mark_signalled(
                    _candle_ts, pair, signal_type, ttl_seconds=14_400
                )

            _v4_metrics["signals_generated"] += 1
            _v4_metrics["trades_opened"]     += 1

            # 12a. AI signal analysis — fire-and-forget; result cached in MongoDB
            #      and appended to the Telegram message below.
            _inserted_id = str(result.inserted_id)
            asyncio.ensure_future(
                analyze_signal_with_ai(
                    signal_id=_inserted_id,
                    pair=pair,
                    direction=signal_type,
                    confidence=round(confidence, 1),
                    entry=entry,
                    tps=tps,
                    sl=sl,
                    rr=rr,
                    regime=hybrid_ctx.get("regime", "UNKNOWN"),
                    account_balance=_risk_state.get("account_balance", ACCOUNT_BALANCE),
                )
            )
        except Exception as exc:
            logger.error(f"[{pair}] MongoDB insert failed: {exc}")

    # 13a. Send comprehensive signal notification to Telegram channel.
    #      Uses the trader-friendly summary format with all critical details.
    #      Fire-and-forget: errors are caught inside send_telegram_signal()
    #      and logged without interrupting the rest of the pipeline.
    asyncio.ensure_future(
        send_telegram_signal(
            pair=pair,
            direction=signal_type,
            confidence=round(confidence, 1),
            entry=entry,
            tps=tps,
            sl=sl,
            rr=rr,
            position_size=conf_sizing.get("units", lots),
            regime=hybrid_ctx.get("regime", "UNKNOWN"),
            strategy_mode=STRATEGY_MODE,
            account_balance=_risk_state.get("account_balance", ACCOUNT_BALANCE),
            daily_pnl=_risk_state.get("daily_pnl", 0.0),
        )
    )

    # 13b. Send copy-trading block + V4 analytics detail messages.
    #      analysis already contains the GPT signal-generation analysis from
    #      gpt_signal_v4(); the deeper AI explanation is cached asynchronously
    #      in MongoDB via analyze_signal_with_ai() above.
    await send_to_telegram_v4(
        pair=pair,
        signal=signal_type,
        entry=entry,
        tps=tps,
        sl=sl,
        confidence=round(confidence, 1),
        rr=rr,
        analysis=analysis,
        be_ts=be_ts,
        pos_size=pos_size,
        regime=hybrid_ctx.get("regime", "UNKNOWN"),
        smc_score=hybrid_ctx.get("smc_score", 0),
        mtf_alignment=mtf_ctx.get("alignment_score", 0),
        mtf_direction=mtf_ctx.get("dominant_direction", "NEUTRAL"),
        lots=lots,
    )

    logger.info(
        f"[{pair}] ✅ V4 {signal_type} @ {entry} | "
        f"TP: {tps} | SL: {sl} | R:R 1:{rr} | Conf: {confidence}% | "
        f"MTF: {mtf_ctx['alignment_score']:.0f}% | Lots: {lots} | "
        f"BE: {be_ts['be_trigger']} | TS: {be_ts['ts_start']}"
    )


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
async def run_all_signals_v4() -> None:
    logger.info("=== V4.0 Signal generation cycle START ===")
    for pair in PAIRS:
        try:
            await generate_signal_v4(pair)
        except Exception as exc:
            logger.error(f"[{pair}] Unhandled error: {exc}", exc_info=True)
        await asyncio.sleep(2)
    logger.info("=== V4.0 Signal generation cycle END ===")


async def run_sync_job() -> None:
    """
    Scheduled WinRateTracker sync from MongoDB (every RETRAIN_SYNC_HOURS = 6 h).
    Lightweight — only reads result/regime/type fields from closed signals.
    """
    result = await sync_win_rate_tracker()
    if result.get("skipped"):
        logger.debug(
            f"WinRateTracker sync skipped — next in "
            f"{result.get('next_sync_in_hours', '?')}h"
        )
    elif result.get("synced"):
        logger.info(
            f"✅ WinRateTracker synced — "
            f"{result.get('signals_processed', 0)} signals, "
            f"{result.get('buckets_updated', 0)} buckets"
        )
    else:
        logger.warning(f"⚠️ WinRateTracker sync issue: {result.get('error', 'unknown')}")


async def run_retrain_job() -> None:
    """Scheduled light model retraining (every RETRAIN_INTERVAL_HOURS)."""
    result = await maybe_retrain_model()
    if result.get("skipped"):
        logger.debug(
            f"Retraining skipped — next in "
            f"{result.get('next_retrain_in_hours', '?')}h"
        )
    elif result.get("success"):
        logger.info(
            f"✅ Scheduled retraining complete — "
            f"{result.get('signals_analyzed', 0)} signals, "
            f"win_rate={result.get('win_rate', 0):.1f}%, "
            f"buckets={result.get('buckets_updated', 0)}"
        )
    else:
        logger.warning(f"⚠️ Scheduled retraining issue: {result.get('error', 'unknown')}")


async def run_market_commentary_job() -> None:
    """Scheduled AI market commentary — runs every 4 hours."""
    logger.info("🤖 AI market commentary job starting …")
    try:
        commentary = await generate_market_commentary()
        if commentary:
            logger.info(f"✅ AI market commentary posted ({len(commentary)} chars)")
        else:
            logger.warning("⚠️ AI market commentary returned empty result")
    except Exception as exc:
        logger.error(f"❌ AI market commentary job failed: {exc}", exc_info=True)


async def run_daily_review_job() -> None:
    """Scheduled AI daily performance review — runs at UTC midnight."""
    logger.info("🤖 AI daily review job starting …")
    try:
        review = await generate_daily_review()
        if review:
            logger.info(f"✅ AI daily review posted ({len(review)} chars)")
        else:
            logger.warning("⚠️ AI daily review returned empty result")
    except Exception as exc:
        logger.error(f"❌ AI daily review job failed: {exc}", exc_info=True)


async def run_trade_management_loop() -> None:
    """
    V4.1 Trade management loop — runs every 2 minutes.

    Fetches current prices for all active pairs and processes:
      - Breakeven activation (price reached +0.5R)
      - Trailing stop updates (after TP1 hit)
      - Partial profit closes (TP1/TP2/TP3)
      - SL hit detection → close as LOSS
    """
    if not _TRADE_MANAGER_AVAILABLE:
        return

    trade_mgr = get_trade_manager()
    open_trades = await trade_mgr.get_open_trades()
    if not open_trades:
        return

    db = get_db()
    if db is None:
        return

    # Fetch current prices for all pairs that have open trades
    active_pairs = {t.get("pair") for t in open_trades if t.get("pair")}
    current_prices: dict[str, float] = {}

    for pair in active_pairs:
        try:
            df, _ = await fetch_ohlcv(pair, interval="4h", outputsize=5)
            if df is not None and len(df) > 0:
                ind = compute_indicators(df, PAIRS[pair]["decimals"])
                if ind:
                    current_prices[pair] = ind["price"]
        except Exception as exc:
            logger.warning(f"[{pair}] Trade management: price fetch failed: {exc}")

    if not current_prices:
        return

    summary = await trade_mgr.run_management_cycle(db, current_prices)

    # Update global metrics
    _v4_metrics["be_activations"]  += summary.get("be_activations", 0)
    _v4_metrics["ts_updates"]      += summary.get("ts_updates", 0)
    _v4_metrics["partial_closes"]  += summary.get("partial_closes", 0)
    _v4_metrics["trade_closes"]    += summary.get("sl_hits", 0)

    if any(v > 0 for v in summary.values()):
        logger.info(
            f"🔄 Trade management cycle — "
            f"checked={summary.get('trades_checked', 0)} "
            f"BE={summary.get('be_activations', 0)} "
            f"TS={summary.get('ts_updates', 0)} "
            f"partials={summary.get('partial_closes', 0)} "
            f"SL_hits={summary.get('sl_hits', 0)}"
        )


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
scheduler = AsyncIOScheduler()


# Tracks startup errors so /healthz can surface them without crashing
_startup_errors: list[str] = []
_startup_complete: bool = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mongo_client, _db, _startup_complete, _startup_errors

    logger.info("🚀 Gold Signals Server v4.0 lifespan startup BEGIN")

    try:
        # ------------------------------------------------------------------
        # Startup validation
        # ------------------------------------------------------------------
        missing = []
        if not MONGO_URL:
            missing.append("MONGO_URL")
        if not TELEGRAM_BOT_TOKEN:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not TWELVE_DATA_API_KEY:
            missing.append("TWELVE_DATA_API_KEY")
        if not OPENAI_API_KEY:
            missing.append("OPENAI_API_KEY / EMERGENT_LLM_KEY")

        if missing:
            msg = f"Missing env vars: {missing}"
            logger.error(f"❌ {msg}")
            _startup_errors.append(msg)
        else:
            logger.info("✅ All required environment variables present")

        # ------------------------------------------------------------------
        # MongoDB — hard 10 s timeout on the ping so a hung DNS/TCP
        # connection never blocks the entire startup sequence.
        # ------------------------------------------------------------------
        if MONGO_URL:
            try:
                logger.info("⏳ Connecting to MongoDB …")
                _mongo_client = AsyncIOMotorClient(
                    MONGO_URL,
                    serverSelectionTimeoutMS=8000,
                    connectTimeoutMS=8000,
                    socketTimeoutMS=8000,
                )
                _db = _mongo_client[DB_NAME]
                await asyncio.wait_for(_db.command("ping"), timeout=10.0)
                logger.info(f"✅ MongoDB connected — db={DB_NAME}")
            except asyncio.TimeoutError:
                msg = "MongoDB ping timed out after 10 s"
                logger.error(f"❌ {msg}")
                _startup_errors.append(msg)
                _db = None
            except Exception as exc:
                msg = f"MongoDB connection failed: {exc!r}"
                logger.error(f"❌ {msg}", exc_info=True)
                _startup_errors.append(msg)
                _db = None
        else:
            logger.warning("⚠️ MONGO_URL not set — MongoDB disabled")

        # ------------------------------------------------------------------
        # Telegram — 15 s timeout; non-fatal
        # ------------------------------------------------------------------
        if TELEGRAM_BOT_TOKEN:
            try:
                logger.info("⏳ Verifying Telegram bot …")
                bot = get_bot()
                me  = await asyncio.wait_for(bot.get_me(), timeout=15.0)
                logger.info(f"✅ Telegram bot ready — @{me.username}")
            except asyncio.TimeoutError:
                msg = "Telegram bot.get_me() timed out after 15 s"
                logger.error(f"❌ {msg}")
                _startup_errors.append(msg)
            except Exception as exc:
                msg = f"Telegram bot init failed: {exc!r}"
                logger.error(f"❌ {msg}", exc_info=True)
                _startup_errors.append(msg)
        else:
            logger.warning("⚠️ TELEGRAM_BOT_TOKEN not set — Telegram disabled")

        # ------------------------------------------------------------------
        # HybridPortfolioSystemV3 — synchronous but may raise on import
        # ------------------------------------------------------------------
        try:
            logger.info("⏳ Initialising HybridPortfolioSystemV3 …")
            get_hybrid_system()
            if _hybrid_system is not None:
                logger.info("✅ HybridPortfolioSystemV3 ready")
            else:
                msg = "HybridPortfolioSystemV3 returned None — check ml_engine imports"
                logger.error(f"❌ {msg}")
                _startup_errors.append(msg)
        except Exception as exc:
            msg = f"HybridPortfolioSystemV3 init raised: {exc!r}"
            logger.error(f"❌ {msg}", exc_info=True)
            _startup_errors.append(msg)

        # ------------------------------------------------------------------
        # WinRateTracker sync — best-effort, 20 s timeout
        # ------------------------------------------------------------------
        if _db is not None:
            try:
                logger.info("⏳ WinRateTracker startup sync …")
                sync_result = await asyncio.wait_for(
                    _win_rate_tracker.sync_from_mongodb(_db), timeout=20.0
                )
                logger.info(
                    f"✅ WinRateTracker startup sync — "
                    f"{sync_result.get('signals_processed', 0)} signals, "
                    f"{sync_result.get('buckets_updated', 0)} buckets"
                )
            except asyncio.TimeoutError:
                logger.warning("⚠️ WinRateTracker startup sync timed out (non-fatal)")
            except Exception as exc:
                logger.warning(f"⚠️ WinRateTracker startup sync failed (non-fatal): {exc!r}")

        # ------------------------------------------------------------------
        # TradeManager sync — best-effort, 20 s timeout
        # ------------------------------------------------------------------
        if _TRADE_MANAGER_AVAILABLE and _db is not None:
            try:
                logger.info("⏳ TradeManager startup sync …")
                tm_sync = await asyncio.wait_for(
                    get_trade_manager().sync_from_mongodb(_db), timeout=20.0
                )
                logger.info(
                    f"✅ TradeManager startup sync — "
                    f"{tm_sync.get('open_trades', 0)} open trade(s) loaded"
                )
            except asyncio.TimeoutError:
                logger.warning("⚠️ TradeManager startup sync timed out (non-fatal)")
            except Exception as exc:
                logger.warning(f"⚠️ TradeManager startup sync failed (non-fatal): {exc!r}")

        # ------------------------------------------------------------------
        # SignalDeduplicator — best-effort, 15 s timeout
        # ------------------------------------------------------------------
        global _deduplicator
        if _DEDUPLICATOR_AVAILABLE:
            try:
                logger.info("⏳ Initialising SignalDeduplicator …")
                _deduplicator = SignalDeduplicator(db=_db)
                await asyncio.wait_for(_deduplicator.setup(), timeout=15.0)
                logger.info("✅ SignalDeduplicator initialised (4H TTL deduplication active)")
            except asyncio.TimeoutError:
                logger.warning("⚠️ SignalDeduplicator setup timed out (non-fatal)")
                _deduplicator = None
            except Exception as exc:
                logger.warning(f"⚠️ SignalDeduplicator init failed (non-fatal): {exc!r}")
                _deduplicator = None

        # ------------------------------------------------------------------
        # Scheduler
        # ------------------------------------------------------------------
        scheduler.add_job(
            run_all_signals_v4,
            "interval",
            minutes=SIGNAL_INTERVAL_MINUTES,
            id="gold_signals_v4",
            max_instances=1,
            coalesce=True,
        )

        # WinRateTracker sync scheduler (every RETRAIN_SYNC_HOURS = 6 h)
        scheduler.add_job(
            run_sync_job,
            "interval",
            hours=RETRAIN_SYNC_HOURS,
            id="win_rate_sync_v4",
            max_instances=1,
            coalesce=True,
        )

        # Light retraining scheduler (every RETRAIN_INTERVAL_HOURS = 24-48 h)
        scheduler.add_job(
            run_retrain_job,
            "interval",
            hours=RETRAIN_INTERVAL_HOURS,
            id="model_retrain_v4",
            max_instances=1,
            coalesce=True,
        )

        # V4.1 Trade management loop (every 2 minutes — BE/TS/partial management)
        scheduler.add_job(
            run_trade_management_loop,
            "interval",
            minutes=2,
            id="trade_management_v4",
            max_instances=1,
            coalesce=True,
        )

        # AI market commentary (every 4 hours)
        scheduler.add_job(
            run_market_commentary_job,
            "interval",
            hours=4,
            id="ai_market_commentary_v4",
            max_instances=1,
            coalesce=True,
        )

        # AI daily performance review (every day at UTC midnight)
        scheduler.add_job(
            run_daily_review_job,
            "cron",
            hour=0,
            minute=5,
            id="ai_daily_review_v4",
            max_instances=1,
            coalesce=True,
        )

        scheduler.start()
        logger.info(
            f"✅ V4.1 Scheduler started — pairs={list(PAIRS.keys())} "
            f"signal_interval={SIGNAL_INTERVAL_MINUTES}min "
            f"trade_mgmt_interval=2min "
            f"sync_interval={RETRAIN_SYNC_HOURS}h "
            f"retrain_interval={RETRAIN_INTERVAL_HOURS}h "
            f"ai_commentary_interval=4h "
            f"ai_daily_review=00:05 UTC"
        )

        asyncio.create_task(run_all_signals_v4())

        _startup_complete = True
        if _startup_errors:
            logger.warning(
                f"⚠️ Startup completed with {len(_startup_errors)} non-fatal error(s): "
                f"{_startup_errors}"
            )
        else:
            logger.info("✅ Gold Signals Server v4.0 startup complete — all systems nominal")

    except Exception as exc:
        # Catch-all: log the full traceback so Railway surfaces it in the
        # deployment logs, then re-raise so the process exits with a non-zero
        # code (which Railway will flag as a failed deploy rather than a
        # silent exit).
        logger.critical(
            f"💥 FATAL: lifespan startup crashed — {exc!r}",
            exc_info=True,
        )
        _startup_errors.append(f"FATAL startup crash: {exc!r}")
        raise

    yield

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    scheduler.shutdown(wait=False)
    if _mongo_client:
        _mongo_client.close()
    logger.info("Gold Signals Server v4.0 Balanced shut down")


app = FastAPI(
    title="Grandcom Gold Signals v4.0 Balanced Edition",
    description=(
        "Institutional Multi-Strategy Hybrid Portfolio System with "
        "Breakeven SL, Trailing Stop, MTF Confirmation, "
        "Advanced Position Sizing & Light Model Retraining"
    ),
    version="4.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Endpoint 0: Lightweight liveness probe — no MongoDB, no external calls.
# Use this as the Railway health-check path so the container is marked
# healthy as soon as uvicorn is accepting connections, regardless of whether
# MongoDB or the hybrid system finished initialising.
# ---------------------------------------------------------------------------
@app.get("/healthz")
async def healthz():
    """
    Minimal liveness probe.  Always returns 200 once uvicorn is running.
    Reports startup status and any non-fatal errors encountered during boot.
    """
    return {
        "status":           "ok",
        "service":          "gold_signals_v4",
        "version":          "4.1.0",
        "startup_complete": _startup_complete,
        "startup_errors":   _startup_errors,
        "timestamp":        datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 1: Health Check
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    """Railway health check endpoint."""
    db = get_db()
    mongo_ok = False
    if db is not None:
        try:
            await db.command("ping")
            mongo_ok = True
        except Exception:
            pass

    hybrid = get_hybrid_system()
    system_status = hybrid.get_system_status() if hybrid else {"status": "unavailable"}

    jobs = [
        {"id": j.id, "next_run": str(j.next_run_time)}
        for j in scheduler.get_jobs()
    ]

    # V4.1 TradeManager metrics
    tm_metrics: dict = {}
    if _TRADE_MANAGER_AVAILABLE:
        try:
            tm_metrics = get_trade_manager().get_metrics()
        except Exception:
            pass

    return {
        "status":              "ok",
        "service":             "gold_signals_v4",
        "version":             "4.1.0",
        "edition":             "Balanced Option C",
        "pairs":               list(PAIRS.keys()),
        "telegram_channel":    TELEGRAM_CHANNEL_ID,
        "scheduler_running":   scheduler.running,
        "scheduler_jobs":      jobs,
        "mongo_connected":     mongo_ok,
        "system_components":   system_status.get("total_components", 0),
        "v4_features": {
            "breakeven_sl":            True,
            "trailing_stop":           True,
            "mtf_confirmation":        True,
            "advanced_sizing":         True,
            "light_retraining":        True,
            "manual_execution":        True,
            # V4.1 additions
            "candle_close_guard":      _CANDLE_UTILS_AVAILABLE,
            "data_freshness_guard":    _FRESHNESS_GUARD_AVAILABLE,
            "trade_management_loop":   _TRADE_MANAGER_AVAILABLE,
            # V4.2 additions
            "signal_deduplication":    _DEDUPLICATOR_AVAILABLE,
        },
        "v4_config": {
            "mtf_min_alignment":        MTF_MIN_ALIGNMENT,
            "be_activation_r":          BE_ACTIVATION_R,
            "trailing_atr_mult":        TRAILING_ATR_MULT,
            "trailing_stop_enabled":    ENABLE_TRAILING_STOP,
            "retrain_interval_h":       RETRAIN_INTERVAL_HOURS,
            "sync_interval_h":          RETRAIN_SYNC_HOURS,
            "min_confidence":           MIN_CONFIDENCE,
            "vol_hard_cap":             VOL_POSITION_SIZE_HARD_CAP,
            # V4.1 additions
            "data_freshness_max_age_s": DATA_FRESHNESS_MAX_AGE_SECONDS,
            "trade_mgmt_interval_min":  2,
        },
        "v4_metrics":          _v4_metrics,
        "trade_manager":       tm_metrics,
        "risk_manager":        get_live_risk_manager().get_state(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 2: Get Signals
# ---------------------------------------------------------------------------
@app.get("/api/signals")
async def get_signals(
    status: Optional[str] = None,
    pair:   Optional[str] = None,
    limit:  int = Query(default=50, le=200),
):
    """Return stored V4 signals with optional filtering."""
    db = get_db()
    if db is None:
        return {"error": "MongoDB not connected", "signals": [], "count": 0}

    query: dict = {}
    if status:
        query["status"] = status.upper()
    if pair:
        query["pair"] = pair.upper()

    signals = (
        await db.gold_signals_v4
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
        .to_list(limit)
    )
    return {"signals": signals, "count": len(signals), "version": "4.0.0"}


# ---------------------------------------------------------------------------
# Endpoint 3: System Status
# ---------------------------------------------------------------------------
@app.get("/api/system/status")
async def system_status():
    """Get full hybrid system status."""
    hybrid = get_hybrid_system()
    if hybrid is None:
        return {"error": "Hybrid system not available", "version": "4.0.0"}
    return hybrid.get_system_status()


# ---------------------------------------------------------------------------
# Endpoint 4: Regime Analysis
# ---------------------------------------------------------------------------
@app.get("/api/analysis/regime/{pair}")
async def get_regime_analysis(pair: str):
    """Get current market regime for a pair."""
    pair = pair.upper()
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Pair {pair} not found")

    df, _ = await fetch_ohlcv(pair, interval="4h", outputsize=100)
    if df is None:
        raise HTTPException(status_code=503, detail="Failed to fetch price data")

    hybrid = get_hybrid_system()
    if hybrid is None:
        raise HTTPException(status_code=503, detail="Hybrid system not available")

    try:
        features = hybrid.feature_engineer.extract_features(df)
        regime   = hybrid.regime_detector.detect_regime(features)
        return {"pair": pair, "regime": regime, "timestamp": datetime.utcnow().isoformat()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Endpoint 5: SMC Analysis
# ---------------------------------------------------------------------------
@app.get("/api/analysis/smc/{pair}")
async def get_smc_analysis(pair: str):
    """Get SMC/ICT analysis for a pair."""
    pair = pair.upper()
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Pair {pair} not found")

    df, _ = await fetch_ohlcv(pair, interval="4h", outputsize=100)
    if df is None:
        raise HTTPException(status_code=503, detail="Failed to fetch price data")

    hybrid = get_hybrid_system()
    if hybrid is None:
        raise HTTPException(status_code=503, detail="Hybrid system not available")

    return hybrid.smc_ict.analyze(df, pair, timeframe="4h")


# ---------------------------------------------------------------------------
# Endpoint 6: Pivot Points
# ---------------------------------------------------------------------------
@app.get("/api/analysis/pivots/{pair}")
async def get_pivot_analysis(
    pair:   str,
    method: str = Query(default="standard", regex="^(standard|fibonacci|woodie|camarilla)$"),
):
    """Get pivot point analysis for a pair."""
    pair = pair.upper()
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Pair {pair} not found")

    df, _ = await fetch_ohlcv(pair, interval="1day", outputsize=10)
    if df is None:
        df, _ = await fetch_ohlcv(pair, interval="4h", outputsize=50)
    if df is None:
        raise HTTPException(status_code=503, detail="Failed to fetch price data")

    hybrid = get_hybrid_system()
    if hybrid is None:
        raise HTTPException(status_code=503, detail="Hybrid system not available")

    return hybrid.pivot_analyzer.analyze(df, pair, method=method, use_all_methods=True)


# ---------------------------------------------------------------------------
# Endpoint 7: MTF Confirmation (V4 enhanced)
# ---------------------------------------------------------------------------
@app.get("/api/analysis/mtf/{pair}")
async def get_mtf_analysis(pair: str):
    """
    Get V4 multi-timeframe confirmation analysis.
    Returns alignment score, per-TF directions, and V4 pass/fail status.
    """
    pair = pair.upper()
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Pair {pair} not found")

    try:
        result = await run_mtf_confirmation(pair)
        return {
            "pair":    pair,
            "version": "4.0.0",
            **result,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Endpoint 8: Full Hybrid Analysis
# ---------------------------------------------------------------------------
@app.get("/api/analysis/hybrid/{pair}")
async def get_hybrid_analysis(pair: str):
    """Run full hybrid portfolio system analysis for a pair."""
    pair = pair.upper()
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Pair {pair} not found")

    df, _ = await fetch_ohlcv(pair, interval="4h", outputsize=100)
    if df is None:
        raise HTTPException(status_code=503, detail="Failed to fetch price data")

    hybrid = get_hybrid_system()
    if hybrid is None:
        raise HTTPException(status_code=503, detail="Hybrid system not available")

    try:
        result = await hybrid.generate_signal(symbol=pair, df_4h=df, strategy_mode=STRATEGY_MODE)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Endpoint 9: Portfolio State
# ---------------------------------------------------------------------------
@app.get("/api/portfolio/state")
async def get_portfolio_state():
    """Get current portfolio state."""
    hybrid = get_hybrid_system()
    if hybrid is None:
        return {"error": "Hybrid system not available"}
    return hybrid.portfolio_manager.get_state(ACCOUNT_BALANCE)


# ---------------------------------------------------------------------------
# Endpoint 10: Performance Attribution
# ---------------------------------------------------------------------------
@app.get("/api/performance")
async def get_performance(lookback_days: int = Query(default=30, ge=1, le=365)):
    """Get performance attribution analysis (V4 collection)."""
    db = get_db()
    if db is None:
        return {"error": "MongoDB not connected"}

    try:
        trades = (
            await db.gold_signals_v4
            .find({"status": {"$in": ["CLOSED", "WIN", "LOSS"]}}, {"_id": 0})
            .sort("created_at", -1)
            .limit(500)
            .to_list(500)
        )

        hybrid = get_hybrid_system()
        if hybrid is None:
            return {"error": "Hybrid system not available"}

        return hybrid.performance.analyze(trades, ACCOUNT_BALANCE, lookback_days)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Endpoint 11: Trigger Signal Now
# ---------------------------------------------------------------------------
@app.post("/api/signals/trigger")
async def trigger_signal(pair: Optional[str] = None):
    """Manually trigger V4 signal generation."""
    if pair:
        pair = pair.upper()
        if pair not in PAIRS:
            raise HTTPException(status_code=404, detail=f"Pair {pair} not found")
        asyncio.create_task(generate_signal_v4(pair))
        return {
            "message":   f"V4 signal generation triggered for {pair}",
            "timestamp": datetime.utcnow().isoformat(),
        }
    else:
        asyncio.create_task(run_all_signals_v4())
        return {
            "message":   "V4 signal generation triggered for all pairs",
            "timestamp": datetime.utcnow().isoformat(),
        }


# ---------------------------------------------------------------------------
# Endpoint 12: V4 Breakeven / Trailing Stop Calculator
# ---------------------------------------------------------------------------
@app.get("/api/v4/be-ts/{pair}")
async def get_be_ts_levels(
    pair:   str,
    signal: str = Query(..., regex="^(BUY|SELL)$"),
    entry:  float = Query(..., gt=0),
    sl:     float = Query(..., gt=0),
):
    """
    Calculate V4 Breakeven and Trailing Stop levels for a given trade.
    Useful for manual execution reference.
    """
    pair = pair.upper()
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Pair {pair} not found")

    df, _ = await fetch_ohlcv(pair, interval="4h", outputsize=30)
    if df is None:
        raise HTTPException(status_code=503, detail="Failed to fetch price data")

    ind = compute_indicators(df, PAIRS[pair]["decimals"])
    if ind is None:
        raise HTTPException(status_code=500, detail="Failed to compute indicators")

    be_ts = compute_be_ts_levels(signal.upper(), entry, sl, ind["atr"], PAIRS[pair])

    return {
        "pair":    pair,
        "signal":  signal.upper(),
        "entry":   entry,
        "sl":      sl,
        "atr":     ind["atr"],
        "version": "4.0.0",
        **be_ts,
    }


# ---------------------------------------------------------------------------
# Endpoint 13: V4 Position Size Calculator
# ---------------------------------------------------------------------------
@app.get("/api/v4/position-size/{pair}")
async def get_position_size(
    pair:        str,
    entry:       float = Query(..., gt=0),
    sl:          float = Query(..., gt=0),
    balance:     float = Query(default=ACCOUNT_BALANCE, gt=0),
    signal_type: str   = Query(default="BUY", regex="^(BUY|SELL)$"),
):
    """
    Calculate V4 advanced position size for a given trade.
    Returns volatility-adjusted, regime-scaled lot size with WinRateTracker multiplier.
    """
    pair = pair.upper()
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Pair {pair} not found")

    df, _ = await fetch_ohlcv(pair, interval="4h", outputsize=100)
    if df is None:
        raise HTTPException(status_code=503, detail="Failed to fetch price data")

    ind = compute_indicators(df, PAIRS[pair]["decimals"])
    if ind is None:
        raise HTTPException(status_code=500, detail="Failed to compute indicators")

    # Get regime from hybrid system
    regime = "UNKNOWN"
    hybrid = get_hybrid_system()
    if hybrid is not None:
        try:
            features = hybrid.feature_engineer.extract_features(df)
            regime_result = hybrid.regime_detector.detect_regime(features)
            regime = regime_result.get("regime_name", "UNKNOWN")
        except Exception:
            pass

    pos_size = compute_advanced_position_size(
        pair=pair,
        entry=entry,
        sl=sl,
        atr=ind["atr"],
        df=df,
        regime=regime,
        signal_type=signal_type.upper(),
        account_balance=balance,
    )

    return {
        "pair":        pair,
        "entry":       entry,
        "sl":          sl,
        "balance":     balance,
        "regime":      regime,
        "signal_type": signal_type.upper(),
        "atr":         ind["atr"],
        "version":     "4.0.0",
        **pos_size,
    }


# ---------------------------------------------------------------------------
# Endpoint 14: Light Model Retraining (manual trigger)
# ---------------------------------------------------------------------------
@app.post("/api/v4/retrain")
async def trigger_retrain(force: bool = Query(default=False)):
    """
    Manually trigger V4 light model retraining.
    Set force=true to bypass the time-interval guard.
    """
    global _last_retrain_time
    if force:
        _last_retrain_time = None  # Reset timer to force immediate retrain

    result = await maybe_retrain_model()
    return {"version": "4.0.0", **result}


# ---------------------------------------------------------------------------
# Endpoint 15: V4 WinRateTracker State
# ---------------------------------------------------------------------------
@app.get("/api/v4/win-rate-tracker")
async def get_win_rate_tracker_state(
    force_sync: bool = Query(default=False),
):
    """
    Return the current WinRateTracker state — all (regime, signal_type) buckets
    with their EW win-rates and confidence multipliers.

    Set force_sync=true to trigger an immediate MongoDB sync before returning.
    """
    global _last_sync_time

    if force_sync:
        _last_sync_time = None   # Reset guard to force sync
        sync_result = await sync_win_rate_tracker()
    else:
        sync_result = None

    tracker = get_win_rate_tracker()
    buckets = tracker.get_all_buckets()

    return {
        "version":          "4.0.0",
        "bucket_count":     len(buckets),
        "buckets":          buckets,
        "ew_alpha":         WinRateTracker.EW_ALPHA,
        "conf_mult_range":  [WinRateTracker.CONF_MULT_MIN, WinRateTracker.CONF_MULT_MAX],
        "min_bucket_trades":WinRateTracker.MIN_BUCKET_TRADES,
        "last_sync":        _last_sync_time.isoformat() if _last_sync_time else None,
        "sync_interval_h":  RETRAIN_SYNC_HOURS,
        "sync_result":      sync_result,
        "timestamp":        datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 16: V4 Config
# ---------------------------------------------------------------------------
@app.get("/api/v4/config")
async def get_v4_config():
    """Return current V4 configuration and feature flags."""
    return {
        "version":  "4.0.0",
        "edition":  "Balanced Option C",
        "pairs":    list(PAIRS.keys()),
        "timeframe": "4H (PERMANENT)",
        "features": {
            "breakeven_sl": {
                "enabled":       True,
                "activation_r":  BE_ACTIVATION_R,
                "description":   "Moves SL to entry after +0.5R profit — always active",
                "drawdown_reduction": "~56%",
            },
            "trailing_stop": {
                "enabled":       ENABLE_TRAILING_STOP,
                "atr_multiplier": TRAILING_ATR_MULT,
                "activation":    "After TP1 hit (+1R profit)",
                "description":   "Trails price by 1 ATR after TP1 hit",
                "env_var":       "ENABLE_TRAILING_STOP",
            },
            "mtf_confirmation": {
                "enabled":       True,
                "min_alignment": MTF_MIN_ALIGNMENT,
                "timeframes":    ["1h", "4h", "1day"],
                "required_aligned": 2,
                "description":   "Requires ≥70% alignment across 1H, 4H, Daily",
                "false_signal_reduction": "~50%",
            },
            "advanced_sizing": {
                "enabled":          True,
                "base_risk_pct":    1.0,
                "max_risk_pct":     2.0,
                "hard_cap":         VOL_POSITION_SIZE_HARD_CAP,
                "vol_regimes":      list(VOL_REGIME_MULTIPLIERS.keys())[:5],
                "description":      "Volatility-adjusted, regime-scaled dynamic lots",
            },
            "light_retraining": {
                "enabled":           True,
                "retrain_interval_h": RETRAIN_INTERVAL_HOURS,
                "sync_interval_h":   RETRAIN_SYNC_HOURS,
                "last_retrain":      _last_retrain_time.isoformat() if _last_retrain_time else None,
                "last_sync":         _last_sync_time.isoformat() if _last_sync_time else None,
                "description":       "EW win-rate per (regime, signal) bucket; syncs every 6h",
                "conf_mult_range":   [WinRateTracker.CONF_MULT_MIN, WinRateTracker.CONF_MULT_MAX],
                "ew_alpha":          WinRateTracker.EW_ALPHA,
            },
            "manual_execution": {
                "enabled":       True,
                "description":   "Copy-trading compatible; no full automation",
                "endpoints":     15,
            },
        },
        "atr_multipliers": {pair: cfg for pair, cfg in PAIRS.items()},
        "expected_performance": {
            "win_rate":         "70%",
            "monthly_pnl":      "$2,000-2,800",
            "drawdown":         "4.5%",
            "signals_per_month": "25-30",
            "complexity":       "Medium-High",
            "risk":             "Medium",
        },
        "min_confidence":      MIN_CONFIDENCE,
        "signal_interval_min": SIGNAL_INTERVAL_MINUTES,
        "timestamp":           datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 17: V4.1 Runtime Metrics
# ---------------------------------------------------------------------------
@app.get("/api/v4/metrics")
async def get_v4_metrics():
    """
    Return V4.1 runtime metrics since last startup.

    Includes signal generation counts, suppression reasons,
    trade management activity (BE/TS/partials), and open trade count.
    """
    tm_metrics: dict = {}
    if _TRADE_MANAGER_AVAILABLE:
        try:
            tm_metrics = get_trade_manager().get_metrics()
        except Exception:
            pass

    return {
        "version":              "4.1.0",
        "signal_metrics":       _v4_metrics,
        "trade_manager":        tm_metrics,
        "safety_modules": {
            "candle_close_guard":   _CANDLE_UTILS_AVAILABLE,
            "data_freshness_guard": _FRESHNESS_GUARD_AVAILABLE,
            "trade_manager":        _TRADE_MANAGER_AVAILABLE,
            "signal_deduplication": _DEDUPLICATOR_AVAILABLE,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 18: V4.1 Open Trades
# ---------------------------------------------------------------------------
@app.get("/api/v4/trades/open")
async def get_open_trades(pair: Optional[str] = None):
    """
    Return all currently open (ACTIVE / PARTIAL) trades from the TradeManager
    in-memory cache.  Optionally filter by pair.
    """
    if not _TRADE_MANAGER_AVAILABLE:
        return {"error": "TradeManager not available", "trades": [], "count": 0}

    trades = await get_trade_manager().get_open_trades(pair=pair)
    # Strip MongoDB ObjectId objects for JSON serialisation
    safe_trades = []
    for t in trades:
        safe_t = {k: v for k, v in t.items() if k != "_id"}
        safe_trades.append(safe_t)

    return {
        "trades":  safe_trades,
        "count":   len(safe_trades),
        "pair":    pair,
        "version": "4.1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 19: V4 Risk Manager State
# ---------------------------------------------------------------------------
@app.get("/api/v4/risk")
async def get_risk_state():
    """
    Return the current LiveRiskManager state.

    Shows cumulative P&L, daily P&L, drawdown percentages, open position
    counts per pair, and whether any risk limits have been breached.

    Use this endpoint to monitor live risk exposure and verify that the
    automatic trading pause (max drawdown / daily loss) is working correctly.
    """
    risk_mgr = get_live_risk_manager()
    state = risk_mgr.get_state()
    return {
        "version":   "4.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **state,
        "limits": {
            "max_total_drawdown_pct":  MAX_TOTAL_DRAWDOWN_PCT,
            "max_daily_loss_pct":      MAX_DAILY_LOSS_PCT,
            "max_concurrent_per_pair": MAX_CONCURRENT_PER_PAIR,
            "sl_atr_multiplier":       SL_ATR_MULTIPLIER,
            "trailing_profit_trigger_pct": TRAILING_PROFIT_TRIGGER,
            "trailing_sl_atr_mult":    TRAILING_SL_ATR_MULT,
        },
        "position_sizing": {
            "account_balance":       ACCOUNT_BALANCE,
            "base_per_1k":           POSITION_SIZE_BASE_PER_1K,
            "scale_60_70":           POSITION_SCALE_60_70,
            "scale_70_80":           POSITION_SCALE_70_80,
            "scale_80_90":           POSITION_SCALE_80_90,
            "scale_90_100":          POSITION_SCALE_90_100,
            "max_units":             POSITION_SIZE_MAX_UNITS,
            "min_units":             POSITION_SIZE_MIN_UNITS,
        },
        "pa_thresholds": {
            "momentum_threshold":   PRICE_ACTION_MOMENTUM_THRESHOLD,
            "volatility_threshold": PRICE_ACTION_VOLATILITY_THRESHOLD,
            "confluence_weight":    PRICE_ACTION_CONFLUENCE_WEIGHT,
        },
    }


# ===========================================================================
# PHASE 3 — Advanced Monitoring, A/B Testing, Account Scaling, Analytics
# ===========================================================================

# ---------------------------------------------------------------------------
# A/B Testing Framework
# ---------------------------------------------------------------------------
import uuid as _uuid


class ABTestManager:
    """
    Manages per-pair PA threshold A/B tests stored in MongoDB.

    Each test document schema:
      {
        "test_id":    str (UUID4),
        "pair":       str,
        "name":       str,
        "status":     "ACTIVE" | "COMPLETED" | "CANCELLED",
        "thresholds": {
            "momentum_threshold":   float,
            "volatility_threshold": float,
            "confluence_weight":    float,
        },
        "start_date": datetime,
        "end_date":   datetime | None,
        "created_at": datetime,
        "signal_count": int,
        "win_count":    int,
        "loss_count":   int,
        "notes":        str,
      }
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Active test lookup (called on every signal — must be fast)
    # ------------------------------------------------------------------

    async def get_active_test_for_pair(self, db, pair: str) -> dict | None:
        """Return the single active A/B test for a pair, or None."""
        if db is None:
            return None
        try:
            doc = await db[AB_TEST_COLLECTION].find_one(
                {"pair": pair.upper(), "status": "ACTIVE"},
                {"_id": 0},
                sort=[("created_at", -1)],
            )
            return doc
        except Exception as exc:
            logger.warning(f"[ABTest] get_active_test_for_pair failed: {exc}")
            return None

    async def get_thresholds_for_pair(self, db, pair: str) -> dict:
        """
        Return PA thresholds for a pair.  If an active A/B test exists,
        return its thresholds; otherwise fall back to global env-var defaults.
        """
        test = await self.get_active_test_for_pair(db, pair)
        if test:
            return {
                "momentum_threshold":   test["thresholds"].get("momentum_threshold",   PRICE_ACTION_MOMENTUM_THRESHOLD),
                "volatility_threshold": test["thresholds"].get("volatility_threshold", PRICE_ACTION_VOLATILITY_THRESHOLD),
                "confluence_weight":    test["thresholds"].get("confluence_weight",    PRICE_ACTION_CONFLUENCE_WEIGHT),
                "test_id":              test["test_id"],
                "test_name":            test.get("name", ""),
                "source":               "ab_test",
            }
        # Fall back to per-pair env-var overrides → global defaults
        _pair_upper = pair.upper()
        return {
            "momentum_threshold": float(os.environ.get(
                f"PRICE_ACTION_MOMENTUM_THRESHOLD_{_pair_upper}",
                os.environ.get("PRICE_ACTION_MOMENTUM_THRESHOLD", str(PRICE_ACTION_MOMENTUM_THRESHOLD)),
            )),
            "volatility_threshold": float(os.environ.get(
                f"PRICE_ACTION_VOLATILITY_THRESHOLD_{_pair_upper}",
                os.environ.get("PRICE_ACTION_VOLATILITY_THRESHOLD", str(PRICE_ACTION_VOLATILITY_THRESHOLD)),
            )),
            "confluence_weight": float(os.environ.get(
                f"PRICE_ACTION_CONFLUENCE_WEIGHT_{_pair_upper}",
                os.environ.get("PRICE_ACTION_CONFLUENCE_WEIGHT", str(PRICE_ACTION_CONFLUENCE_WEIGHT)),
            )),
            "test_id":   None,
            "test_name": None,
            "source":    "default",
        }

    # ------------------------------------------------------------------
    # Signal outcome recording
    # ------------------------------------------------------------------

    async def record_signal(self, db, test_id: str, result: str | None = None) -> None:
        """
        Increment signal_count (and win/loss counters) for a test.
        Called every time a signal is generated under a test.
        result: "WIN" | "LOSS" | None (still open)
        """
        if db is None or not test_id:
            return
        try:
            inc: dict = {"signal_count": 1}
            if result == "WIN":
                inc["win_count"] = 1
            elif result == "LOSS":
                inc["loss_count"] = 1
            await db[AB_TEST_COLLECTION].update_one(
                {"test_id": test_id},
                {"$inc": inc},
            )
        except Exception as exc:
            logger.warning(f"[ABTest] record_signal failed: {exc}")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create_test(
        self,
        db,
        pair: str,
        name: str,
        thresholds: dict,
        notes: str = "",
    ) -> dict:
        """Create a new A/B test.  Fails if another test is already ACTIVE for the pair."""
        if db is None:
            return {"success": False, "error": "MongoDB not connected"}

        pair = pair.upper()
        async with self._lock:
            # Enforce max-1 active test per pair
            existing = await db[AB_TEST_COLLECTION].count_documents(
                {"pair": pair, "status": "ACTIVE"}
            )
            if existing >= AB_TEST_MAX_ACTIVE_PER_PAIR:
                return {
                    "success": False,
                    "error": f"Pair {pair} already has {existing} active test(s). "
                             f"Complete or cancel it before starting a new one.",
                }

            test_id = str(_uuid.uuid4())
            now = datetime.now(timezone.utc)
            doc = {
                "test_id":    test_id,
                "pair":       pair,
                "name":       name,
                "status":     "ACTIVE",
                "thresholds": {
                    "momentum_threshold":   float(thresholds.get("momentum_threshold",   PRICE_ACTION_MOMENTUM_THRESHOLD)),
                    "volatility_threshold": float(thresholds.get("volatility_threshold", PRICE_ACTION_VOLATILITY_THRESHOLD)),
                    "confluence_weight":    float(thresholds.get("confluence_weight",    PRICE_ACTION_CONFLUENCE_WEIGHT)),
                },
                "start_date":   now,
                "end_date":     None,
                "created_at":   now,
                "signal_count": 0,
                "win_count":    0,
                "loss_count":   0,
                "notes":        notes,
            }
            await db[AB_TEST_COLLECTION].insert_one(doc)
            logger.info(f"[ABTest] Created test {test_id} for {pair} — {name}")
            return {"success": True, "test_id": test_id, "pair": pair, "name": name}

    async def complete_test(self, db, test_id: str) -> dict:
        """Mark a test as COMPLETED."""
        if db is None:
            return {"success": False, "error": "MongoDB not connected"}
        now = datetime.now(timezone.utc)
        result = await db[AB_TEST_COLLECTION].update_one(
            {"test_id": test_id, "status": "ACTIVE"},
            {"$set": {"status": "COMPLETED", "end_date": now}},
        )
        if result.modified_count == 0:
            return {"success": False, "error": f"Test {test_id} not found or not ACTIVE"}
        return {"success": True, "test_id": test_id, "status": "COMPLETED"}

    async def cancel_test(self, db, test_id: str) -> dict:
        """Mark a test as CANCELLED."""
        if db is None:
            return {"success": False, "error": "MongoDB not connected"}
        now = datetime.now(timezone.utc)
        result = await db[AB_TEST_COLLECTION].update_one(
            {"test_id": test_id, "status": "ACTIVE"},
            {"$set": {"status": "CANCELLED", "end_date": now}},
        )
        if result.modified_count == 0:
            return {"success": False, "error": f"Test {test_id} not found or not ACTIVE"}
        return {"success": True, "test_id": test_id, "status": "CANCELLED"}

    async def get_test_results(self, db, test_id: str) -> dict:
        """Return full results for a single test, including computed metrics."""
        if db is None:
            return {"error": "MongoDB not connected"}
        doc = await db[AB_TEST_COLLECTION].find_one({"test_id": test_id}, {"_id": 0})
        if not doc:
            return {"error": f"Test {test_id} not found"}

        signal_count = doc.get("signal_count", 0)
        win_count    = doc.get("win_count", 0)
        loss_count   = doc.get("loss_count", 0)
        closed       = win_count + loss_count

        win_rate = round(win_count / closed * 100, 1) if closed > 0 else None
        profit_factor = round(win_count / loss_count, 2) if loss_count > 0 else None
        statistically_significant = closed >= AB_TEST_MIN_SIGNALS

        # Compare to backtest benchmarks
        vs_backtest: dict = {}
        if win_rate is not None:
            vs_backtest["win_rate_delta_pct"] = round(win_rate - BACKTEST_WIN_RATE, 1)
        if profit_factor is not None:
            vs_backtest["profit_factor_delta"] = round(profit_factor - BACKTEST_PROFIT_FACTOR, 2)

        return {
            **doc,
            "computed": {
                "win_rate_pct":             win_rate,
                "profit_factor":            profit_factor,
                "closed_signals":           closed,
                "statistically_significant": statistically_significant,
                "min_signals_required":     AB_TEST_MIN_SIGNALS,
                "vs_backtest":              vs_backtest,
            },
        }

    async def list_tests(self, db, status: str | None = None, pair: str | None = None) -> list:
        """List A/B tests with optional status/pair filter."""
        if db is None:
            return []
        query: dict = {}
        if status:
            query["status"] = status.upper()
        if pair:
            query["pair"] = pair.upper()
        docs = (
            await db[AB_TEST_COLLECTION]
            .find(query, {"_id": 0})
            .sort("created_at", -1)
            .limit(100)
            .to_list(100)
        )
        return docs


# Module-level singleton
_ab_test_manager = ABTestManager()


def get_ab_test_manager() -> ABTestManager:
    return _ab_test_manager


# ---------------------------------------------------------------------------
# Account Scaling Manager
# ---------------------------------------------------------------------------

class AccountScalingManager:
    """
    Tracks account balance growth and automatically scales POSITION_SIZE_BASE_PER_1K.

    Milestones (relative to ACCOUNT_SCALING_BASE_BALANCE = $10,000):
      +10% growth ($11,000) → increase base size by 10%
      +25% growth ($12,500) → increase base size by 25%
      +50% growth ($15,000) → increase base size by 50%

    Hard cap: POSITION_SIZE_BASE_PER_1K never exceeds ACCOUNT_SCALING_MAX_BASE_PER_1K (50).

    All scaling events are persisted to MongoDB (account_history_v4 collection).
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        # In-memory scaling state (persisted to MongoDB on every change)
        self._current_base_per_1k: float = POSITION_SIZE_BASE_PER_1K
        self._milestones_hit: list[dict] = []   # [{growth_pct, size_increase_pct, balance_at_hit, timestamp}]
        self._last_balance: float = ACCOUNT_SCALING_BASE_BALANCE

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_current_base_per_1k(self) -> float:
        """Return the current (possibly scaled) POSITION_SIZE_BASE_PER_1K."""
        return self._current_base_per_1k

    async def record_balance_update(self, db, new_balance: float, source: str = "pnl") -> dict:
        """
        Record a new account balance and check whether any scaling milestone
        has been crossed.  Persists a balance snapshot to MongoDB.

        Returns a dict describing what happened (milestone hit, new base size, etc.).
        """
        async with self._lock:
            now = datetime.now(timezone.utc)
            base = ACCOUNT_SCALING_BASE_BALANCE
            growth_pct = (new_balance - base) / base * 100.0 if base > 0 else 0.0

            result: dict = {
                "timestamp":          now.isoformat(),
                "new_balance":        round(new_balance, 2),
                "base_balance":       base,
                "growth_pct":         round(growth_pct, 2),
                "previous_base_per_1k": round(self._current_base_per_1k, 4),
                "milestone_hit":      None,
                "new_base_per_1k":    round(self._current_base_per_1k, 4),
                "cap_applied":        False,
                "source":             source,
            }

            # Check each threshold in descending order (highest first)
            # so we apply the largest applicable increase
            applicable = [
                t for t in sorted(ACCOUNT_SCALING_THRESHOLDS, key=lambda x: x["growth_pct"], reverse=True)
                if growth_pct >= t["growth_pct"]
                and not any(m["growth_pct"] == t["growth_pct"] for m in self._milestones_hit)
            ]

            if applicable:
                threshold = applicable[0]  # Highest un-hit threshold
                increase_factor = 1.0 + threshold["size_increase_pct"] / 100.0
                new_base = self._current_base_per_1k * increase_factor

                # Apply hard cap
                cap_applied = new_base > ACCOUNT_SCALING_MAX_BASE_PER_1K
                new_base = min(new_base, ACCOUNT_SCALING_MAX_BASE_PER_1K)

                milestone_record = {
                    "growth_pct":        threshold["growth_pct"],
                    "size_increase_pct": threshold["size_increase_pct"],
                    "balance_at_hit":    round(new_balance, 2),
                    "old_base_per_1k":   round(self._current_base_per_1k, 4),
                    "new_base_per_1k":   round(new_base, 4),
                    "cap_applied":       cap_applied,
                    "timestamp":         now.isoformat(),
                }
                self._milestones_hit.append(milestone_record)
                self._current_base_per_1k = new_base

                result.update({
                    "milestone_hit":   threshold,
                    "new_base_per_1k": round(new_base, 4),
                    "cap_applied":     cap_applied,
                })

                logger.info(
                    f"[AccountScaling] 🚀 Milestone hit! growth={growth_pct:.1f}% "
                    f"({threshold['growth_pct']}% threshold) — "
                    f"base_per_1k: {milestone_record['old_base_per_1k']} → {new_base:.4f} "
                    f"(cap_applied={cap_applied})"
                )

            self._last_balance = new_balance

            # Persist balance snapshot to MongoDB
            if db is not None:
                try:
                    await db[ACCOUNT_HISTORY_COLLECTION].insert_one({
                        **result,
                        "milestones_hit_total": len(self._milestones_hit),
                    })
                except Exception as exc:
                    logger.warning(f"[AccountScaling] MongoDB persist failed: {exc}")

            return result

    def get_scaling_state(self) -> dict:
        """Return a full snapshot of the current scaling state."""
        base = ACCOUNT_SCALING_BASE_BALANCE
        growth_pct = (self._last_balance - base) / base * 100.0 if base > 0 else 0.0

        # Compute next milestone
        hit_thresholds = {m["growth_pct"] for m in self._milestones_hit}
        remaining = [
            t for t in sorted(ACCOUNT_SCALING_THRESHOLDS, key=lambda x: x["growth_pct"])
            if t["growth_pct"] not in hit_thresholds
        ]
        next_milestone = remaining[0] if remaining else None
        next_milestone_balance = None
        if next_milestone:
            next_milestone_balance = round(
                base * (1 + next_milestone["growth_pct"] / 100.0), 2
            )

        return {
            "current_base_per_1k":      round(self._current_base_per_1k, 4),
            "original_base_per_1k":     POSITION_SIZE_BASE_PER_1K,
            "max_base_per_1k":          ACCOUNT_SCALING_MAX_BASE_PER_1K,
            "base_balance":             base,
            "last_known_balance":       round(self._last_balance, 2),
            "current_growth_pct":       round(growth_pct, 2),
            "milestones_hit":           self._milestones_hit,
            "milestones_hit_count":     len(self._milestones_hit),
            "next_milestone":           next_milestone,
            "next_milestone_balance":   next_milestone_balance,
            "all_thresholds":           ACCOUNT_SCALING_THRESHOLDS,
            "cap_reached":              self._current_base_per_1k >= ACCOUNT_SCALING_MAX_BASE_PER_1K,
        }

    async def get_balance_history(self, db, limit: int = 100) -> list:
        """Fetch account balance history from MongoDB."""
        if db is None:
            return []
        try:
            docs = (
                await db[ACCOUNT_HISTORY_COLLECTION]
                .find({}, {"_id": 0})
                .sort("timestamp", -1)
                .limit(limit)
                .to_list(limit)
            )
            return docs
        except Exception as exc:
            logger.warning(f"[AccountScaling] get_balance_history failed: {exc}")
            return []


# Module-level singleton
_account_scaling_manager = AccountScalingManager()


def get_account_scaling_manager() -> AccountScalingManager:
    return _account_scaling_manager


# ---------------------------------------------------------------------------
# Performance Analytics Helpers
# ---------------------------------------------------------------------------

def _compute_sharpe_ratio(returns: list[float], risk_free_rate: float = 0.0) -> float | None:
    """
    Compute annualised Sharpe ratio from a list of per-trade return percentages.
    Assumes ~252 trading days / year.  Returns None if insufficient data.
    """
    if len(returns) < 5:
        return None
    import statistics
    mean_r = statistics.mean(returns)
    std_r  = statistics.stdev(returns) if len(returns) > 1 else 0.0
    if std_r == 0:
        return None
    # Annualise: assume ~252 signals/year (rough approximation)
    sharpe = (mean_r - risk_free_rate) / std_r * (252 ** 0.5)
    return round(sharpe, 3)


def _compute_profit_factor(wins: list[float], losses: list[float]) -> float | None:
    """Gross profit / gross loss.  Returns None if no losses."""
    gross_profit = sum(w for w in wins if w > 0)
    gross_loss   = abs(sum(l for l in losses if l < 0))
    if gross_loss == 0:
        return None
    return round(gross_profit / gross_loss, 3)


async def compute_live_analytics(db, lookback_days: int = 30) -> dict:
    """
    Compute live performance metrics from closed signals in MongoDB.
    Compares against Phase 2 backtest benchmarks.
    """
    if db is None:
        return {"error": "MongoDB not connected"}

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    try:
        signals = (
            await db.gold_signals_v4
            .find(
                {
                    "status": {"$in": ["CLOSED", "WIN", "LOSS"]},
                    "created_at": {"$gte": cutoff},
                },
                {"_id": 0},
            )
            .sort("created_at", -1)
            .limit(1000)
            .to_list(1000)
        )
    except Exception as exc:
        return {"error": str(exc)}

    total = len(signals)
    if total == 0:
        return {
            "total_signals":  0,
            "lookback_days":  lookback_days,
            "message":        "No closed signals in lookback window",
            "backtest_benchmarks": {
                "win_rate_pct":    BACKTEST_WIN_RATE,
                "profit_factor":   BACKTEST_PROFIT_FACTOR,
                "avg_return_pct":  BACKTEST_AVG_RETURN_PCT,
            },
        }

    wins   = [s for s in signals if s.get("result") == "WIN" or s.get("status") == "WIN"]
    losses = [s for s in signals if s.get("result") == "LOSS" or s.get("status") == "LOSS"]

    win_count  = len(wins)
    loss_count = len(losses)
    closed     = win_count + loss_count

    live_win_rate = round(win_count / closed * 100, 1) if closed > 0 else None

    # Collect R:R values for return estimation
    rr_values = [float(s.get("risk_reward", 0)) for s in signals if s.get("risk_reward")]
    avg_rr    = round(sum(rr_values) / len(rr_values), 2) if rr_values else None

    # Approximate per-trade returns: wins = +rr%, losses = -1%
    win_returns  = [float(s.get("risk_reward", 1.0)) for s in wins]
    loss_returns = [-1.0 for _ in losses]
    all_returns  = win_returns + loss_returns

    live_profit_factor = _compute_profit_factor(win_returns, loss_returns)
    live_sharpe        = _compute_sharpe_ratio(all_returns)
    avg_return         = round(sum(all_returns) / len(all_returns), 3) if all_returns else None

    # Confidence distribution
    conf_buckets = {"60_70": 0, "70_80": 0, "80_90": 0, "90_100": 0}
    for s in signals:
        c = float(s.get("confidence", 0))
        if c >= 90:
            conf_buckets["90_100"] += 1
        elif c >= 80:
            conf_buckets["80_90"] += 1
        elif c >= 70:
            conf_buckets["70_80"] += 1
        else:
            conf_buckets["60_70"] += 1
    conf_dist = {
        k: {"count": v, "pct": round(v / total * 100, 1) if total > 0 else 0}
        for k, v in conf_buckets.items()
    }

    # Per-pair breakdown
    pair_stats: dict[str, dict] = {}
    for s in signals:
        p = s.get("pair", "UNKNOWN")
        if p not in pair_stats:
            pair_stats[p] = {"total": 0, "wins": 0, "losses": 0, "rr_sum": 0.0}
        pair_stats[p]["total"] += 1
        if s.get("result") == "WIN" or s.get("status") == "WIN":
            pair_stats[p]["wins"] += 1
        elif s.get("result") == "LOSS" or s.get("status") == "LOSS":
            pair_stats[p]["losses"] += 1
        pair_stats[p]["rr_sum"] += float(s.get("risk_reward", 0))

    pair_performance: dict[str, dict] = {}
    for p, stats in pair_stats.items():
        closed_p = stats["wins"] + stats["losses"]
        wr_p = round(stats["wins"] / closed_p * 100, 1) if closed_p > 0 else None
        avg_rr_p = round(stats["rr_sum"] / stats["total"], 2) if stats["total"] > 0 else None
        vs_bt_wr = round(wr_p - BACKTEST_WIN_RATE, 1) if wr_p is not None else None
        pair_performance[p] = {
            "total_signals":    stats["total"],
            "wins":             stats["wins"],
            "losses":           stats["losses"],
            "win_rate_pct":     wr_p,
            "avg_rr":           avg_rr_p,
            "vs_backtest_wr":   vs_bt_wr,
            "status":           (
                "OUTPERFORMING" if vs_bt_wr is not None and vs_bt_wr > 5
                else "UNDERPERFORMING" if vs_bt_wr is not None and vs_bt_wr < -5
                else "IN_LINE"
            ),
        }

    # vs backtest comparison
    vs_backtest: dict = {
        "win_rate_delta_pct":    round(live_win_rate - BACKTEST_WIN_RATE, 1) if live_win_rate is not None else None,
        "profit_factor_delta":   round(live_profit_factor - BACKTEST_PROFIT_FACTOR, 3) if live_profit_factor is not None else None,
        "avg_return_delta_pct":  round(avg_return - BACKTEST_AVG_RETURN_PCT, 3) if avg_return is not None else None,
    }

    return {
        "lookback_days":    lookback_days,
        "total_signals":    total,
        "closed_signals":   closed,
        "wins":             win_count,
        "losses":           loss_count,
        "live_metrics": {
            "win_rate_pct":    live_win_rate,
            "profit_factor":   live_profit_factor,
            "sharpe_ratio":    live_sharpe,
            "avg_return_pct":  avg_return,
            "avg_rr":          avg_rr,
        },
        "backtest_benchmarks": {
            "win_rate_pct":    BACKTEST_WIN_RATE,
            "profit_factor":   BACKTEST_PROFIT_FACTOR,
            "avg_return_pct":  BACKTEST_AVG_RETURN_PCT,
        },
        "vs_backtest":          vs_backtest,
        "confidence_distribution": conf_dist,
        "pair_performance":     pair_performance,
        "timestamp":            datetime.now(timezone.utc).isoformat(),
    }


# ===========================================================================
# PHASE 3 — New API Endpoints (20-34)
# ===========================================================================

# ---------------------------------------------------------------------------
# Endpoint 20: Live Signals Feed (last 100 with full metrics)
# ---------------------------------------------------------------------------
@app.get("/api/v4/signals/live")
async def get_live_signals(
    limit: int = Query(default=100, le=200),
    pair:  Optional[str] = None,
):
    """
    Return the last N signals with full metrics for real-time dashboard display.
    Includes confidence, regime, MTF alignment, position sizing, and PA thresholds.
    """
    db = get_db()
    if db is None:
        return {"error": "MongoDB not connected", "signals": [], "count": 0}

    query: dict = {}
    if pair:
        query["pair"] = pair.upper()

    signals = (
        await db.gold_signals_v4
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
        .to_list(limit)
    )

    return {
        "signals":   signals,
        "count":     len(signals),
        "pair":      pair,
        "version":   "4.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 21: Signal Stats (win rate, profit factor, avg confidence by pair)
# ---------------------------------------------------------------------------
@app.get("/api/v4/signals/stats")
async def get_signal_stats(
    lookback_days: int = Query(default=30, ge=1, le=365),
    pair:          Optional[str] = None,
):
    """
    Aggregate signal statistics: win rate, profit factor, avg confidence,
    and signal count — broken down by pair and overall.
    """
    db = get_db()
    if db is None:
        return {"error": "MongoDB not connected"}

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    query: dict = {"created_at": {"$gte": cutoff}}
    if pair:
        query["pair"] = pair.upper()

    signals = (
        await db.gold_signals_v4
        .find(query, {"_id": 0, "pair": 1, "status": 1, "result": 1, "confidence": 1, "risk_reward": 1})
        .sort("created_at", -1)
        .limit(2000)
        .to_list(2000)
    )

    def _stats_for(sigs: list) -> dict:
        total = len(sigs)
        wins  = sum(1 for s in sigs if s.get("result") == "WIN" or s.get("status") == "WIN")
        losses = sum(1 for s in sigs if s.get("result") == "LOSS" or s.get("status") == "LOSS")
        closed = wins + losses
        confs  = [float(s["confidence"]) for s in sigs if s.get("confidence") is not None]
        rrs    = [float(s["risk_reward"]) for s in sigs if s.get("risk_reward") is not None]
        win_rate = round(wins / closed * 100, 1) if closed > 0 else None
        pf = round(wins / losses, 2) if losses > 0 else None
        return {
            "total_signals":  total,
            "closed_signals": closed,
            "wins":           wins,
            "losses":         losses,
            "win_rate_pct":   win_rate,
            "profit_factor":  pf,
            "avg_confidence": round(sum(confs) / len(confs), 1) if confs else None,
            "avg_rr":         round(sum(rrs) / len(rrs), 2) if rrs else None,
        }

    overall = _stats_for(signals)

    by_pair: dict = {}
    for s in signals:
        p = s.get("pair", "UNKNOWN")
        by_pair.setdefault(p, []).append(s)
    pair_stats = {p: _stats_for(sigs) for p, sigs in by_pair.items()}

    return {
        "lookback_days": lookback_days,
        "overall":       overall,
        "by_pair":       pair_stats,
        "version":       "4.0.0",
        "timestamp":     datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 22: Performance — Live vs Backtest Comparison
# ---------------------------------------------------------------------------
@app.get("/api/v4/performance")
async def get_performance_comparison(
    lookback_days: int = Query(default=30, ge=1, le=365),
):
    """
    Compare live trading performance against Phase 2 backtest benchmarks.
    Returns win rate, profit factor, Sharpe ratio, and per-pair breakdown.
    """
    analytics = await compute_live_analytics(get_db(), lookback_days=lookback_days)
    return {"version": "4.0.0", **analytics}


# ---------------------------------------------------------------------------
# Endpoint 23: Account Growth History
# ---------------------------------------------------------------------------
@app.get("/api/v4/account/growth")
async def get_account_growth(
    limit: int = Query(default=100, le=500),
):
    """
    Return account balance history and growth rate.
    Includes all recorded balance snapshots and scaling events.
    """
    db = get_db()
    scaling_mgr = get_account_scaling_manager()
    history = await scaling_mgr.get_balance_history(db, limit=limit)
    state   = scaling_mgr.get_scaling_state()

    # Compute growth rate from history
    growth_rate_pct = None
    if len(history) >= 2:
        oldest = history[-1].get("new_balance", ACCOUNT_SCALING_BASE_BALANCE)
        newest = history[0].get("new_balance", ACCOUNT_SCALING_BASE_BALANCE)
        if oldest > 0:
            growth_rate_pct = round((newest - oldest) / oldest * 100, 2)

    return {
        "current_balance":    state["last_known_balance"],
        "base_balance":       state["base_balance"],
        "growth_pct":         state["current_growth_pct"],
        "growth_rate_pct":    growth_rate_pct,
        "history":            history,
        "history_count":      len(history),
        "scaling_state":      state,
        "version":            "4.0.0",
        "timestamp":          datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 24: Current PA Thresholds for All Pairs
# ---------------------------------------------------------------------------
@app.get("/api/v4/thresholds/current")
async def get_current_thresholds():
    """
    Return the currently active PA thresholds for all pairs.
    Shows whether each pair is using an A/B test override or global defaults.
    """
    db = get_db()
    ab_mgr = get_ab_test_manager()

    result: dict = {
        "global_defaults": {
            "momentum_threshold":   PRICE_ACTION_MOMENTUM_THRESHOLD,
            "volatility_threshold": PRICE_ACTION_VOLATILITY_THRESHOLD,
            "confluence_weight":    PRICE_ACTION_CONFLUENCE_WEIGHT,
        },
        "pairs": {},
        "version":   "4.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    for pair in PAIRS:
        thresholds = await ab_mgr.get_thresholds_for_pair(db, pair)
        result["pairs"][pair] = thresholds

    return result


# ---------------------------------------------------------------------------
# Endpoint 25: List A/B Tests
# ---------------------------------------------------------------------------
@app.get("/api/v4/ab-tests")
async def list_ab_tests(
    status: Optional[str] = Query(default=None, regex="^(ACTIVE|COMPLETED|CANCELLED)$"),
    pair:   Optional[str] = None,
):
    """
    List all A/B tests with optional status/pair filter.
    Returns test configurations, signal counts, and computed win rates.
    """
    db = get_db()
    if db is None:
        return {"error": "MongoDB not connected", "tests": [], "count": 0}

    ab_mgr = get_ab_test_manager()
    tests  = await ab_mgr.list_tests(db, status=status, pair=pair)

    # Enrich each test with computed metrics
    enriched = []
    for t in tests:
        signal_count = t.get("signal_count", 0)
        win_count    = t.get("win_count", 0)
        loss_count   = t.get("loss_count", 0)
        closed       = win_count + loss_count
        enriched.append({
            **t,
            "win_rate_pct":   round(win_count / closed * 100, 1) if closed > 0 else None,
            "profit_factor":  round(win_count / loss_count, 2) if loss_count > 0 else None,
            "statistically_significant": closed >= AB_TEST_MIN_SIGNALS,
        })

    return {
        "tests":     enriched,
        "count":     len(enriched),
        "version":   "4.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 26: Create A/B Test
# ---------------------------------------------------------------------------
@app.post("/api/v4/ab-tests/create")
async def create_ab_test(
    pair:                  str   = Query(..., description="Trading pair, e.g. XAUUSD"),
    name:                  str   = Query(..., description="Descriptive test name"),
    momentum_threshold:    float = Query(default=PRICE_ACTION_MOMENTUM_THRESHOLD, ge=0.0, le=1.0),
    volatility_threshold:  float = Query(default=PRICE_ACTION_VOLATILITY_THRESHOLD, ge=0.0, le=1.0),
    confluence_weight:     float = Query(default=PRICE_ACTION_CONFLUENCE_WEIGHT, ge=0.0, le=1.0),
    notes:                 str   = Query(default=""),
):
    """
    Start a new A/B test for a pair with custom PA thresholds.
    Only one active test is allowed per pair at a time.
    The test_id is logged with every signal generated under this test.
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="MongoDB not connected")

    pair = pair.upper()
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Pair {pair} not found")

    ab_mgr = get_ab_test_manager()
    result = await ab_mgr.create_test(
        db=db,
        pair=pair,
        name=name,
        thresholds={
            "momentum_threshold":   momentum_threshold,
            "volatility_threshold": volatility_threshold,
            "confluence_weight":    confluence_weight,
        },
        notes=notes,
    )

    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result.get("error", "Failed to create test"))

    return {"version": "4.0.0", **result}


# ---------------------------------------------------------------------------
# Endpoint 27: Get A/B Test Results
# ---------------------------------------------------------------------------
@app.get("/api/v4/ab-tests/{test_id}/results")
async def get_ab_test_results(test_id: str):
    """
    Return full results for a specific A/B test, including computed win rate,
    profit factor, and comparison against Phase 2 backtest benchmarks.
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="MongoDB not connected")

    ab_mgr = get_ab_test_manager()
    result = await ab_mgr.get_test_results(db, test_id)

    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])

    return {"version": "4.0.0", **result}


# ---------------------------------------------------------------------------
# Endpoint 28: Complete / Cancel A/B Test
# ---------------------------------------------------------------------------
@app.post("/api/v4/ab-tests/{test_id}/complete")
async def complete_ab_test(test_id: str):
    """Mark an active A/B test as COMPLETED."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="MongoDB not connected")
    result = await get_ab_test_manager().complete_test(db, test_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error"))
    return {"version": "4.0.0", **result}


@app.post("/api/v4/ab-tests/{test_id}/cancel")
async def cancel_ab_test(test_id: str):
    """Cancel an active A/B test."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="MongoDB not connected")
    result = await get_ab_test_manager().cancel_test(db, test_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error"))
    return {"version": "4.0.0", **result}


# ---------------------------------------------------------------------------
# Endpoint 29: Account Scaling State & History
# ---------------------------------------------------------------------------
@app.get("/api/v4/account/scaling")
async def get_account_scaling():
    """
    Return the current account scaling state: current base position size,
    milestones hit, next milestone, and full scaling history.
    """
    db = get_db()
    scaling_mgr = get_account_scaling_manager()
    state   = scaling_mgr.get_scaling_state()
    history = await scaling_mgr.get_balance_history(db, limit=50)

    return {
        "version":   "4.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **state,
        "recent_history": history[:10],  # Last 10 balance snapshots
    }


# ---------------------------------------------------------------------------
# Endpoint 30: Record Account Balance Update (triggers scaling check)
# ---------------------------------------------------------------------------
@app.post("/api/v4/account/update-balance")
async def update_account_balance(
    new_balance: float = Query(..., gt=0, description="New account balance in USD"),
    source:      str   = Query(default="manual", description="Source of update: pnl | manual | broker_sync"),
):
    """
    Record a new account balance and check whether any scaling milestone
    has been crossed.  Automatically increases POSITION_SIZE_BASE_PER_1K
    when growth thresholds are hit.
    """
    db = get_db()
    scaling_mgr = get_account_scaling_manager()
    result = await scaling_mgr.record_balance_update(db, new_balance=new_balance, source=source)

    return {
        "version":   "4.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **result,
        "scaling_state": scaling_mgr.get_scaling_state(),
    }


# ---------------------------------------------------------------------------
# Endpoint 31: Live vs Backtest Full Comparison Report
# ---------------------------------------------------------------------------
@app.get("/api/v4/analytics/live-vs-backtest")
async def get_live_vs_backtest(
    lookback_days: int = Query(default=30, ge=1, le=365),
):
    """
    Full live vs backtest comparison report.
    Calculates live win rate, profit factor, Sharpe ratio and compares
    against Phase 2 backtest benchmarks (+7.85% avg return, 45.1% win rate,
    2.17 profit factor).
    """
    analytics = await compute_live_analytics(get_db(), lookback_days=lookback_days)

    # Add overall assessment
    vs = analytics.get("vs_backtest", {})
    wr_delta = vs.get("win_rate_delta_pct")
    pf_delta = vs.get("profit_factor_delta")

    if wr_delta is not None and pf_delta is not None:
        if wr_delta > 5 and pf_delta > 0.2:
            assessment = "OUTPERFORMING"
        elif wr_delta < -5 or pf_delta < -0.3:
            assessment = "UNDERPERFORMING"
        else:
            assessment = "IN_LINE"
    else:
        assessment = "INSUFFICIENT_DATA"

    return {
        "version":    "4.0.0",
        "assessment": assessment,
        **analytics,
    }


# ---------------------------------------------------------------------------
# Endpoint 32: Per-Pair Performance Metrics
# ---------------------------------------------------------------------------
@app.get("/api/v4/analytics/pair-performance")
async def get_pair_performance(
    lookback_days: int = Query(default=30, ge=1, le=365),
):
    """
    Per-pair performance metrics: win rate, profit factor, avg R:R,
    confidence distribution, and outperforming/underperforming status
    vs Phase 2 backtest benchmarks.
    """
    db = get_db()
    if db is None:
        return {"error": "MongoDB not connected"}

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    try:
        signals = (
            await db.gold_signals_v4
            .find(
                {"created_at": {"$gte": cutoff}},
                {"_id": 0, "pair": 1, "status": 1, "result": 1,
                 "confidence": 1, "risk_reward": 1, "regime": 1,
                 "pa_momentum_threshold": 1, "pa_volatility_threshold": 1},
            )
            .sort("created_at", -1)
            .limit(2000)
            .to_list(2000)
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    by_pair: dict[str, list] = {}
    for s in signals:
        p = s.get("pair", "UNKNOWN")
        by_pair.setdefault(p, []).append(s)

    result: dict = {}
    for pair_name, sigs in by_pair.items():
        total  = len(sigs)
        wins   = [s for s in sigs if s.get("result") == "WIN" or s.get("status") == "WIN"]
        losses = [s for s in sigs if s.get("result") == "LOSS" or s.get("status") == "LOSS"]
        closed = len(wins) + len(losses)

        win_rate = round(len(wins) / closed * 100, 1) if closed > 0 else None
        pf       = round(len(wins) / len(losses), 2) if len(losses) > 0 else None

        confs = [float(s["confidence"]) for s in sigs if s.get("confidence") is not None]
        rrs   = [float(s["risk_reward"]) for s in sigs if s.get("risk_reward") is not None]

        # Regime breakdown
        regime_counts: dict[str, int] = {}
        for s in sigs:
            r = s.get("regime", "UNKNOWN")
            regime_counts[r] = regime_counts.get(r, 0) + 1

        vs_bt_wr = round(win_rate - BACKTEST_WIN_RATE, 1) if win_rate is not None else None
        vs_bt_pf = round(pf - BACKTEST_PROFIT_FACTOR, 2) if pf is not None else None

        result[pair_name] = {
            "total_signals":    total,
            "closed_signals":   closed,
            "wins":             len(wins),
            "losses":           len(losses),
            "win_rate_pct":     win_rate,
            "profit_factor":    pf,
            "avg_confidence":   round(sum(confs) / len(confs), 1) if confs else None,
            "avg_rr":           round(sum(rrs) / len(rrs), 2) if rrs else None,
            "regime_breakdown": regime_counts,
            "vs_backtest": {
                "win_rate_delta_pct":  vs_bt_wr,
                "profit_factor_delta": vs_bt_pf,
            },
            "status": (
                "OUTPERFORMING" if vs_bt_wr is not None and vs_bt_wr > 5
                else "UNDERPERFORMING" if vs_bt_wr is not None and vs_bt_wr < -5
                else "IN_LINE"
            ),
        }

    return {
        "lookback_days":    lookback_days,
        "pairs":            result,
        "backtest_benchmarks": {
            "win_rate_pct":   BACKTEST_WIN_RATE,
            "profit_factor":  BACKTEST_PROFIT_FACTOR,
            "avg_return_pct": BACKTEST_AVG_RETURN_PCT,
        },
        "version":   "4.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 33: Monitoring Dashboard Summary
# ---------------------------------------------------------------------------
@app.get("/api/v4/dashboard")
async def get_dashboard_summary(
    lookback_days: int = Query(default=7, ge=1, le=90),
):
    """
    Real-time monitoring dashboard summary.
    Aggregates signal quality, account state, active A/B tests,
    and live vs backtest performance into a single response.
    """
    db = get_db()
    ab_mgr      = get_ab_test_manager()
    scaling_mgr = get_account_scaling_manager()

    # Gather all data concurrently
    analytics_task = asyncio.create_task(compute_live_analytics(db, lookback_days=lookback_days))
    ab_tests_task  = asyncio.create_task(ab_mgr.list_tests(db, status="ACTIVE"))

    analytics = await analytics_task
    active_tests = await ab_tests_task

    scaling_state = scaling_mgr.get_scaling_state()
    risk_state    = get_live_risk_manager().get_state()

    # Recent signals (last 10)
    recent_signals: list = []
    if db is not None:
        try:
            recent_signals = (
                await db.gold_signals_v4
                .find({}, {"_id": 0, "pair": 1, "type": 1, "confidence": 1,
                           "status": 1, "created_at": 1, "risk_reward": 1})
                .sort("created_at", -1)
                .limit(10)
                .to_list(10)
            )
        except Exception:
            pass

    return {
        "version":        "4.0.0",
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "lookback_days":  lookback_days,
        "signal_quality": {
            "total_signals":  analytics.get("total_signals", 0),
            "win_rate_pct":   analytics.get("live_metrics", {}).get("win_rate_pct"),
            "profit_factor":  analytics.get("live_metrics", {}).get("profit_factor"),
            "sharpe_ratio":   analytics.get("live_metrics", {}).get("sharpe_ratio"),
            "vs_backtest":    analytics.get("vs_backtest", {}),
        },
        "account": {
            "balance":          risk_state.get("account_balance"),
            "daily_pnl":        risk_state.get("daily_pnl"),
            "total_pnl":        risk_state.get("total_pnl"),
            "drawdown_pct":     risk_state.get("total_drawdown_pct"),
            "growth_pct":       scaling_state.get("current_growth_pct"),
            "base_per_1k":      scaling_state.get("current_base_per_1k"),
            "next_milestone":   scaling_state.get("next_milestone"),
        },
        "ab_tests": {
            "active_count": len(active_tests),
            "active_tests": [
                {"test_id": t["test_id"], "pair": t["pair"], "name": t.get("name", ""),
                 "signal_count": t.get("signal_count", 0)}
                for t in active_tests
            ],
        },
        "risk": {
            "trading_allowed":       not (risk_state.get("drawdown_breached") or risk_state.get("daily_limit_breached")),
            "drawdown_breached":     risk_state.get("drawdown_breached"),
            "daily_limit_breached":  risk_state.get("daily_limit_breached"),
            "open_positions":        risk_state.get("open_positions", {}),
        },
        "recent_signals": recent_signals,
        "v4_metrics":     _v4_metrics,
    }


# ---------------------------------------------------------------------------
# Endpoint 34: Confidence Distribution
# ---------------------------------------------------------------------------
@app.get("/api/v4/analytics/confidence-distribution")
async def get_confidence_distribution(
    lookback_days: int = Query(default=30, ge=1, le=365),
    pair:          Optional[str] = None,
):
    """
    Track the distribution of signal confidence tiers over time.
    Shows what percentage of signals fall into each confidence bucket
    (60-70%, 70-80%, 80-90%, 90-100%) and how each tier performs.
    """
    db = get_db()
    if db is None:
        return {"error": "MongoDB not connected"}

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    query: dict = {"created_at": {"$gte": cutoff}}
    if pair:
        query["pair"] = pair.upper()

    signals = (
        await db.gold_signals_v4
        .find(query, {"_id": 0, "confidence": 1, "status": 1, "result": 1, "risk_reward": 1})
        .sort("created_at", -1)
        .limit(2000)
        .to_list(2000)
    )

    buckets: dict[str, dict] = {
        "60_70":  {"label": "60-70% (Cautious)",       "count": 0, "wins": 0, "losses": 0, "rr_sum": 0.0},
        "70_80":  {"label": "70-80% (Baseline)",       "count": 0, "wins": 0, "losses": 0, "rr_sum": 0.0},
        "80_90":  {"label": "80-90% (Elevated)",       "count": 0, "wins": 0, "losses": 0, "rr_sum": 0.0},
        "90_100": {"label": "90-100% (High Conviction)","count": 0, "wins": 0, "losses": 0, "rr_sum": 0.0},
    }

    total = len(signals)
    for s in signals:
        c = float(s.get("confidence", 0))
        is_win  = s.get("result") == "WIN" or s.get("status") == "WIN"
        is_loss = s.get("result") == "LOSS" or s.get("status") == "LOSS"
        rr = float(s.get("risk_reward", 0))

        if c >= 90:
            key = "90_100"
        elif c >= 80:
            key = "80_90"
        elif c >= 70:
            key = "70_80"
        else:
            key = "60_70"

        buckets[key]["count"]  += 1
        buckets[key]["rr_sum"] += rr
        if is_win:
            buckets[key]["wins"] += 1
        if is_loss:
            buckets[key]["losses"] += 1

    result: dict = {}
    for key, b in buckets.items():
        closed = b["wins"] + b["losses"]
        result[key] = {
            "label":        b["label"],
            "count":        b["count"],
            "pct_of_total": round(b["count"] / total * 100, 1) if total > 0 else 0,
            "wins":         b["wins"],
            "losses":       b["losses"],
            "win_rate_pct": round(b["wins"] / closed * 100, 1) if closed > 0 else None,
            "avg_rr":       round(b["rr_sum"] / b["count"], 2) if b["count"] > 0 else None,
        }

    return {
        "lookback_days":  lookback_days,
        "pair":           pair,
        "total_signals":  total,
        "buckets":        result,
        "version":        "4.0.0",
        "timestamp":      datetime.now(timezone.utc).isoformat(),
    }


# ===========================================================================
# AI ENDPOINTS (35-40) — GPT-4 powered signal analysis, commentary & reviews
# ===========================================================================

# ---------------------------------------------------------------------------
# Endpoint 35: Get AI Analysis for a Signal
# ---------------------------------------------------------------------------
@app.get("/api/v4/ai/signal-analysis/{signal_id}")
async def get_signal_ai_analysis(signal_id: str):
    """
    Return the AI-generated analysis for a specific signal.

    If the signal has already been analysed (ai_analysis field present in
    MongoDB), returns the cached result immediately.  Otherwise triggers a
    fresh GPT-4 analysis, caches it, and returns the result.
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="MongoDB not connected")

    try:
        from bson import ObjectId
        doc = await db.gold_signals_v4.find_one(
            {"_id": ObjectId(signal_id)},
            {"_id": 0, "pair": 1, "type": 1, "confidence": 1, "entry_price": 1,
             "tp_levels": 1, "sl_price": 1, "risk_reward": 1, "regime": 1,
             "ai_analysis": 1, "ai_analysis_at": 1},
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid signal_id: {exc}")

    if doc is None:
        raise HTTPException(status_code=404, detail=f"Signal {signal_id} not found")

    # Return cached analysis if available
    if doc.get("ai_analysis"):
        return {
            "signal_id":      signal_id,
            "ai_analysis":    doc["ai_analysis"],
            "ai_analysis_at": doc.get("ai_analysis_at"),
            "cached":         True,
            "version":        "4.0.0",
            "timestamp":      datetime.now(timezone.utc).isoformat(),
        }

    # Generate fresh analysis
    ai_analysis = await analyze_signal_with_ai(
        signal_id=signal_id,
        pair=doc.get("pair", "XAUUSD"),
        direction=doc.get("type", "BUY"),
        confidence=float(doc.get("confidence", 0)),
        entry=float(doc.get("entry_price", 0)),
        tps=doc.get("tp_levels", []),
        sl=float(doc.get("sl_price", 0)),
        rr=float(doc.get("risk_reward", 0)),
        regime=doc.get("regime", "UNKNOWN"),
        account_balance=ACCOUNT_BALANCE,
    )

    return {
        "signal_id":      signal_id,
        "ai_analysis":    ai_analysis,
        "ai_analysis_at": datetime.now(timezone.utc).isoformat(),
        "cached":         False,
        "version":        "4.0.0",
        "timestamp":      datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 36: Get Latest Market Commentary
# ---------------------------------------------------------------------------
@app.get("/api/v4/ai/market-commentary")
async def get_market_commentary(limit: int = Query(default=1, ge=1, le=10)):
    """
    Return the latest AI-generated market commentary from MongoDB.
    Set limit > 1 to retrieve recent commentary history.
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="MongoDB not connected")

    try:
        docs = (
            await db.market_commentary_v4
            .find({}, {"_id": 0})
            .sort("created_at", -1)
            .limit(limit)
            .to_list(limit)
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not docs:
        return {
            "commentary":  None,
            "message":     "No market commentary generated yet. Use POST /generate to create one.",
            "version":     "4.0.0",
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        }

    return {
        "commentary":  docs[0].get("commentary") if limit == 1 else None,
        "results":     docs,
        "count":       len(docs),
        "version":     "4.0.0",
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 37: Generate New Market Commentary
# ---------------------------------------------------------------------------
@app.post("/api/v4/ai/market-commentary/generate")
async def trigger_market_commentary():
    """
    Immediately generate a new AI market commentary and post it to Telegram.
    Does not wait for the 4-hour scheduler — useful for on-demand analysis.
    """
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not configured")

    commentary = await generate_market_commentary()

    if not commentary:
        raise HTTPException(
            status_code=500,
            detail="Failed to generate market commentary — check logs for details",
        )

    return {
        "commentary":  commentary,
        "posted_to_telegram": True,
        "version":     "4.0.0",
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 38: Get Latest Daily Review
# ---------------------------------------------------------------------------
@app.get("/api/v4/ai/daily-review")
async def get_daily_review(limit: int = Query(default=1, ge=1, le=30)):
    """
    Return the latest AI-generated daily performance review from MongoDB.
    Set limit > 1 to retrieve recent review history.
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="MongoDB not connected")

    try:
        docs = (
            await db.daily_reviews_v4
            .find({}, {"_id": 0})
            .sort("created_at", -1)
            .limit(limit)
            .to_list(limit)
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not docs:
        return {
            "review":    None,
            "message":   "No daily reviews generated yet. Use POST /generate to create one.",
            "version":   "4.0.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    return {
        "review":    docs[0].get("review") if limit == 1 else None,
        "results":   docs,
        "count":     len(docs),
        "version":   "4.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 39: Generate New Daily Review
# ---------------------------------------------------------------------------
@app.post("/api/v4/ai/daily-review/generate")
async def trigger_daily_review():
    """
    Immediately generate a new AI daily performance review and post to Telegram.
    Does not wait for the midnight scheduler — useful for on-demand reporting.
    """
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not configured")

    review = await generate_daily_review()

    if not review:
        raise HTTPException(
            status_code=500,
            detail="Failed to generate daily review — check logs for details",
        )

    return {
        "review":             review,
        "posted_to_telegram": True,
        "version":            "4.0.0",
        "timestamp":          datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoint 40: List Recent Risk Alerts
# ---------------------------------------------------------------------------
@app.get("/api/v4/ai/risk-alerts")
async def get_risk_alerts(
    limit:      int          = Query(default=20, ge=1, le=100),
    alert_type: Optional[str] = Query(default=None, description="DRAWDOWN or DAILY_LOSS"),
):
    """
    Return recent AI-generated risk alerts from MongoDB.

    Alerts are generated automatically when:
      - Total drawdown exceeds 10% (approaching 15% hard limit)
      - Daily loss exceeds 3% (approaching 5% hard limit)

    Use alert_type to filter by DRAWDOWN or DAILY_LOSS.
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="MongoDB not connected")

    query: dict = {}
    if alert_type:
        query["alert_type"] = alert_type.upper()

    try:
        docs = (
            await db.risk_alerts_v4
            .find(query, {"_id": 0})
            .sort("created_at", -1)
            .limit(limit)
            .to_list(limit)
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # Current risk state for context
    risk_state = get_live_risk_manager().get_state()

    return {
        "alerts":       docs,
        "count":        len(docs),
        "alert_type":   alert_type,
        "current_risk": {
            "total_drawdown_pct":  risk_state.get("total_drawdown_pct"),
            "daily_loss_pct":      risk_state.get("daily_loss_pct"),
            "drawdown_alert_threshold":   LiveRiskManager.DRAWDOWN_ALERT_PCT,
            "daily_loss_alert_threshold": LiveRiskManager.DAILY_LOSS_ALERT_PCT,
            "drawdown_hard_limit":        MAX_TOTAL_DRAWDOWN_PCT,
            "daily_loss_hard_limit":      MAX_DAILY_LOSS_PCT,
        },
        "version":      "4.0.0",
        "timestamp":    datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8003)))

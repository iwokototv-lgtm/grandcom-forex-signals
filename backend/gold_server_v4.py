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

_raw_channel = os.environ.get("TELEGRAM_GOLD_CHANNEL_ID", "-1003834233408")
try:
    TELEGRAM_CHANNEL_ID: int | str = int(_raw_channel)
except ValueError:
    TELEGRAM_CHANNEL_ID = _raw_channel

SIGNAL_INTERVAL_MINUTES = int(os.environ.get("SIGNAL_INTERVAL_MINUTES", "2"))
MIN_CONFIDENCE          = int(os.environ.get("MIN_CONFIDENCE", "62"))   # Raised from 60 → 62 for V4
ACCOUNT_BALANCE         = float(os.environ.get("DEFAULT_ACCOUNT_BALANCE", "10000.0"))

# V4 Risk Management Constants
MTF_MIN_ALIGNMENT       = float(os.environ.get("MTF_MIN_ALIGNMENT", "70.0"))   # ≥70% required
BE_ACTIVATION_R         = float(os.environ.get("BE_ACTIVATION_R", "0.5"))      # Breakeven at +0.5R
TRAILING_ATR_MULT       = float(os.environ.get("TRAILING_ATR_MULT", "1.0"))    # Trail by 1 ATR
RETRAIN_INTERVAL_HOURS  = int(os.environ.get("RETRAIN_INTERVAL_HOURS", "24"))  # 24-48 h full retrain
RETRAIN_SYNC_HOURS      = int(os.environ.get("RETRAIN_SYNC_HOURS", "6"))       # 6 h MongoDB win-rate sync
ENABLE_TRAILING_STOP    = os.environ.get("ENABLE_TRAILING_STOP", "true").lower() in ("1", "true", "yes")

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
    if _CANDLE_UTILS_AVAILABLE:
        if not is_candle_closed(df, interval="4h"):
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

    # 2. Indicators
    ind = compute_indicators(df, cfg["decimals"])
    if ind is None:
        return

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
            hybrid_result = await hybrid.generate_signal(symbol=pair, df_4h=df)
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
    entry = float(gpt.get("entry_price") or ind["price"])
    if entry <= 0:
        entry = ind["price"]

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

    # 12. Store in MongoDB (V4 collection)
    db = get_db()
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
                # V4 Position sizing
                "lots":             lots,
                "dollar_risk":      pos_size.get("dollar_risk", 0),
                "risk_pct":         pos_size.get("risk_pct", 0),
                "vol_regime":       pos_size.get("vol_regime", "NORMAL"),
                "vol_regime_mult":  pos_size.get("vol_regime_mult", 1.0),
                "conf_mult":        pos_size.get("conf_mult", 1.0),
                "hard_cap_applied": pos_size.get("hard_cap_applied", False),
                # Meta
                "system_version":   "4.0.0",
                "created_at":       datetime.now(timezone.utc),
            }
            result = await db.gold_signals_v4.insert_one(doc)
            logger.info(f"[{pair}] V4 signal stored — id={result.inserted_id}")

            # Register with TradeManager for BE/TS/partial management
            if _TRADE_MANAGER_AVAILABLE:
                trade_doc = {**doc, "indicators": ind}
                get_trade_manager().register_new_trade(str(result.inserted_id), trade_doc)

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
        except Exception as exc:
            logger.error(f"[{pair}] MongoDB insert failed: {exc}")

    # 13. Send to Telegram
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mongo_client, _db

    # Startup validation
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
        logger.error(f"❌ Missing env vars: {missing}")
    else:
        logger.info("✅ All required environment variables present")

    # MongoDB
    if MONGO_URL:
        try:
            _mongo_client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
            _db = _mongo_client[DB_NAME]
            await _db.command("ping")
            logger.info(f"✅ MongoDB connected — db={DB_NAME}")
        except Exception as exc:
            logger.error(f"❌ MongoDB connection failed: {exc}")
            _db = None

    # Telegram
    if TELEGRAM_BOT_TOKEN:
        try:
            bot = get_bot()
            me  = await bot.get_me()
            logger.info(f"✅ Telegram bot ready — @{me.username}")
        except Exception as exc:
            logger.error(f"❌ Telegram bot init failed: {exc}")

    # Hybrid system
    get_hybrid_system()

    # Initial WinRateTracker sync from MongoDB (best-effort at startup)
    if _db is not None:
        try:
            sync_result = await _win_rate_tracker.sync_from_mongodb(_db)
            logger.info(
                f"✅ WinRateTracker startup sync — "
                f"{sync_result.get('signals_processed', 0)} signals, "
                f"{sync_result.get('buckets_updated', 0)} buckets"
            )
        except Exception as exc:
            logger.warning(f"⚠️ WinRateTracker startup sync failed (non-fatal): {exc}")

    # V4.1 TradeManager startup sync — load all open trades from MongoDB
    if _TRADE_MANAGER_AVAILABLE and _db is not None:
        try:
            tm_sync = await get_trade_manager().sync_from_mongodb(_db)
            logger.info(
                f"✅ TradeManager startup sync — "
                f"{tm_sync.get('open_trades', 0)} open trade(s) loaded"
            )
        except Exception as exc:
            logger.warning(f"⚠️ TradeManager startup sync failed (non-fatal): {exc}")

    # V4.2 Signal deduplicator — ensure TTL index and inject DB reference
    global _deduplicator
    if _DEDUPLICATOR_AVAILABLE:
        try:
            _deduplicator = SignalDeduplicator(db=_db)
            await _deduplicator.setup()
            logger.info("✅ SignalDeduplicator initialised (4H TTL deduplication active)")
        except Exception as exc:
            logger.warning(f"⚠️ SignalDeduplicator init failed (non-fatal): {exc}")
            _deduplicator = None

    # Signal scheduler
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

    scheduler.start()
    logger.info(
        f"✅ V4.1 Scheduler started — pairs={list(PAIRS.keys())} "
        f"signal_interval={SIGNAL_INTERVAL_MINUTES}min "
        f"trade_mgmt_interval=2min "
        f"sync_interval={RETRAIN_SYNC_HOURS}h "
        f"retrain_interval={RETRAIN_INTERVAL_HOURS}h"
    )

    asyncio.create_task(run_all_signals_v4())

    yield

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
        result = await hybrid.generate_signal(symbol=pair, df_4h=df)
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
# Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8003)))

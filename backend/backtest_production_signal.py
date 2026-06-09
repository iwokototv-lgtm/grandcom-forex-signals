#!/usr/bin/env python3
"""
backtest_production_signal.py — Production Signal Backtest
===========================================================
Tests the REAL HybridPortfolioSystemV3 signal on historical TwelveData
candles.  Unlike backtest_twelvedata.py (which re-codes a 4-indicator
proxy), this script imports the production engine directly and calls
generate_signal() at every decision point — exactly as the live server
does — but with pre-fetched data so no live API calls occur during the
walk-forward loop.

Walk-forward design
-------------------
  • Fetch 1 000+ 4H candles for XAUUSD and XAUEUR from TwelveData.
  • Also fetch 1H, Daily, and Weekly candles for MTF confirmation.
  • Walk forward one 4H candle at a time (warmup = first 100 candles).
  • At each step, call HybridPortfolioSystemV3.generate_signal() with:
      - df_4h   : rolling 100-candle window ending at current candle
      - df_daily: rolling 100-candle window of daily data
      - MTF     : analyze_sync() with pre-fetched 1H / Daily / Weekly slices
  • Filter signals by confidence >= 62 %.
  • Simulate the full V4 trade lifecycle (BE, partial closes, trailing stop).

Splits
------
  • First 60 % of candles → in-sample
  • Last  40 % of candles → out-of-sample

Baseline
--------
  • Random strategy: 50/50 BUY/SELL at the same decision points, same
    TP/SL/costs/lifecycle.  Answers: "is there any edge over coin-flip?"

Output
------
  • Comprehensive report printed to stdout.
  • Full results (strategy + random + trade list) saved to MongoDB.

Usage
-----
    python backtest_production_signal.py

Environment variables
---------------------
    TWELVEDATA_API_KEY   — TwelveData API key (required for real data)
    MONGO_URL            — MongoDB connection string (optional)
    DB_NAME              — MongoDB database name (default: gold_signals_v4)
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Path setup — allow running from repo root or backend/
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ---------------------------------------------------------------------------
# Production engine imports
# ---------------------------------------------------------------------------
try:
    from ml_engine.hybrid_portfolio_system_v3 import HybridPortfolioSystemV3
    from ml_engine.multi_timeframe_confirmation import MultiTimeframeConfirmation
    _PRODUCTION_AVAILABLE = True
except ImportError as _imp_err:
    print(f"⚠  Could not import production engine: {_imp_err}", flush=True)
    print("   Ensure you are running from the backend/ directory or that", flush=True)
    print("   ml_engine/ is on PYTHONPATH.", flush=True)
    _PRODUCTION_AVAILABLE = False

# ---------------------------------------------------------------------------
# MongoDB
# ---------------------------------------------------------------------------
try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
    _PYMONGO_AVAILABLE = True
except ImportError:
    _PYMONGO_AVAILABLE = False

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.WARNING,          # Suppress verbose engine logs during backtest
    format="%(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("backtest_production")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAIRS: Dict[str, Dict] = {
    "XAUUSD": {
        "symbol":     "XAU/USD",
        "decimals":   2,
        "pip_value":  0.1,
        "atr_sl":     1.0,
        "atr_tp1":    0.40,
        "atr_tp2":    0.80,
        "atr_tp3":    1.40,
        "spread":     0.30,
        "commission": 0.10,
        "slippage":   0.10,
    },
    "XAUEUR": {
        "symbol":     "XAU/EUR",
        "decimals":   2,
        "pip_value":  0.1,
        "atr_sl":     1.0,
        "atr_tp1":    0.40,
        "atr_tp2":    0.80,
        "atr_tp3":    1.40,
        "spread":     0.35,
        "commission": 0.10,
        "slippage":   0.12,
    },
}

# Partial-profit schedule (mirrors trade_manager.py)
PARTIAL_SIZES: Dict[str, float] = {
    "TP1": 0.50,
    "TP2": 0.30,
    "TP3": 0.20,
}

# Backtest parameters
CONFIDENCE_THRESHOLD  = 62.0    # Minimum confidence % to take a trade
ATR_PERIOD            = 14      # Wilder ATR period
WARMUP_CANDLES        = 100     # Candles skipped at start for indicator warmup
ROLLING_WINDOW_4H     = 100     # Rolling 4H window fed to generate_signal()
ROLLING_WINDOW_DAILY  = 100     # Rolling daily window fed to generate_signal()
TIMEOUT_CANDLES       = 60      # Max candles before trade timeout
BE_ACTIVATION_R       = 0.5     # Breakeven trigger (multiples of ATR)
ACCOUNT_BALANCE       = 10_000.0
RISK_PER_TRADE_PCT    = 1.0     # % of account risked per trade
IN_SAMPLE_RATIO       = 0.60    # First 60 % = in-sample
FETCH_OUTPUTSIZE      = 1000    # Candles to fetch per timeframe
TWELVEDATA_BASE_URL   = "https://api.twelvedata.com/time_series"

# TwelveData interval strings
TF_4H     = "4h"
TF_1H     = "1h"
TF_DAILY  = "1day"
TF_WEEKLY = "1week"

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    """One simulated trade through the full V4 lifecycle."""
    pair:           str
    direction:      str           # "BUY" or "SELL"
    entry_price:    float
    entry_time:     datetime
    sl_price:       float
    tp1_price:      float
    tp2_price:      float
    tp3_price:      float
    atr_at_entry:   float
    cost_per_unit:  float
    candle_idx:     int           # Index in the 4H candle array
    confidence:     float         # Signal confidence (%)
    signal_source:  str           # "strategy" or "random"

    # Lifecycle state
    current_sl:     float = 0.0
    be_activated:   bool  = False
    tp1_hit:        bool  = False
    tp2_hit:        bool  = False
    tp3_hit:        bool  = False
    ts_active:      bool  = False

    # Outcome
    exit_price:     Optional[float]    = None
    exit_time:      Optional[datetime] = None
    result:         str = "ACTIVE"

    # P&L (price units, before position sizing)
    gross_pnl:      float = 0.0
    net_pnl:        float = 0.0
    partial_pnl:    float = 0.0
    remaining_pos:  float = 1.0
    max_adverse:    float = 0.0
    max_favourable: float = 0.0

    def __post_init__(self) -> None:
        self.current_sl = self.sl_price


@dataclass
class SplitStats:
    """Statistics for one data split (in-sample or out-of-sample)."""
    label:            str
    total_trades:     int   = 0
    wins:             int   = 0
    losses:           int   = 0
    timeouts:         int   = 0
    be_exits:         int   = 0
    win_rate:         float = 0.0
    gross_pnl:        float = 0.0
    net_pnl:          float = 0.0
    total_costs:      float = 0.0
    profit_factor:    float = 0.0
    sharpe_ratio:     float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_usd: float = 0.0
    final_balance:    float = 0.0
    return_pct:       float = 0.0
    be_activations:   int   = 0
    partial_closes:   int   = 0
    equity_curve:     List[float] = field(default_factory=list)
    monthly_pnl:      Dict[str, float] = field(default_factory=dict)
    trades:           List[Trade] = field(default_factory=list)


@dataclass
class PairResult:
    """Full backtest result for one pair."""
    pair:         str
    data_source:  str
    candles_4h:   int
    split_idx:    int           # Index where out-of-sample begins
    in_sample:    SplitStats = field(default_factory=lambda: SplitStats("in-sample"))
    out_sample:   SplitStats = field(default_factory=lambda: SplitStats("out-of-sample"))
    random_is:    SplitStats = field(default_factory=lambda: SplitStats("random-in-sample"))
    random_oos:   SplitStats = field(default_factory=lambda: SplitStats("random-out-of-sample"))
    atr_mean:     float = 0.0
    atr_min:      float = 0.0
    atr_max:      float = 0.0
    signals_raw:  int   = 0     # Signals before confidence filter
    signals_used: int   = 0     # Signals after confidence filter


# ---------------------------------------------------------------------------
# TwelveData fetch helpers
# ---------------------------------------------------------------------------

def _fetch_raw(
    symbol: str,
    interval: str,
    outputsize: int,
    api_key: str,
) -> Optional[List[Dict]]:
    """Fetch raw OHLCV values from TwelveData REST API (stdlib only)."""
    if not api_key:
        return None
    params = urllib.parse.urlencode({
        "symbol":     symbol,
        "interval":   interval,
        "outputsize": outputsize,
        "apikey":     api_key,
        "format":     "JSON",
    })
    url = f"{TWELVEDATA_BASE_URL}?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GoldBacktest/2.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if "values" not in data:
            msg = data.get("message", data.get("status", "unknown"))
            print(f"    ✗ TwelveData [{symbol}/{interval}]: {msg}", flush=True)
            return None
        return data["values"]
    except urllib.error.HTTPError as exc:
        print(f"    ✗ HTTP {exc.code} [{symbol}/{interval}]: {exc.reason}", flush=True)
        return None
    except urllib.error.URLError as exc:
        print(f"    ✗ Network error [{symbol}/{interval}]: {exc.reason}", flush=True)
        return None
    except Exception as exc:
        print(f"    ✗ Fetch error [{symbol}/{interval}]: {exc}", flush=True)
        return None


def _raw_to_df(raw_values: List[Dict]) -> pd.DataFrame:
    """
    Convert TwelveData raw value list to a chronological DataFrame.

    Columns: datetime (UTC), open, high, low, close, volume
    """
    rows = []
    for v in reversed(raw_values):          # API returns newest-first
        try:
            dt = datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
            rows.append({
                "datetime": dt,
                "open":     float(v["open"]),
                "high":     float(v["high"]),
                "low":      float(v["low"]),
                "close":    float(v["close"]),
                "volume":   float(v.get("volume", 0) or 0),
            })
        except (KeyError, ValueError):
            continue
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


def fetch_all_timeframes(
    symbol_td: str,
    api_key: str,
    outputsize: int = FETCH_OUTPUTSIZE,
    rate_limit_sleep: float = 1.2,
) -> Dict[str, Optional[pd.DataFrame]]:
    """
    Fetch 1H, 4H, Daily, and Weekly candles for one symbol.

    Returns a dict keyed by timeframe string.
    """
    result: Dict[str, Optional[pd.DataFrame]] = {}
    for tf in [TF_4H, TF_1H, TF_DAILY, TF_WEEKLY]:
        # Weekly needs fewer candles
        size = min(outputsize, 200) if tf == TF_WEEKLY else outputsize
        print(f"    → Fetching {size} {tf} candles for {symbol_td} …", flush=True)
        raw = _fetch_raw(symbol_td, tf, size, api_key)
        if raw:
            df = _raw_to_df(raw)
            result[tf] = df if len(df) > 0 else None
            print(f"      ✓ {len(df)} candles  "
                  f"({df['datetime'].iloc[0].date()} → {df['datetime'].iloc[-1].date()})",
                  flush=True)
        else:
            result[tf] = None
            print(f"      ✗ No data for {symbol_td}/{tf}", flush=True)
        time.sleep(rate_limit_sleep)
    return result


# ---------------------------------------------------------------------------
# ATR calculation (Wilder smoothing)
# ---------------------------------------------------------------------------

def _compute_atr_series(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """Return a Series of Wilder ATR values aligned to df's index."""
    high  = df["high"].values
    low   = df["low"].values
    close = df["close"].values
    n     = len(df)
    atr   = np.full(n, np.nan)

    trs = np.empty(n)
    trs[0] = high[0] - low[0]
    for i in range(1, n):
        trs[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i]  - close[i - 1]),
        )

    if n < period:
        return pd.Series(atr, index=df.index)

    atr[period - 1] = trs[:period].mean()
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + trs[i]) / period

    return pd.Series(atr, index=df.index)


# ---------------------------------------------------------------------------
# Trade lifecycle simulation (identical to backtest_twelvedata.py)
# ---------------------------------------------------------------------------

def _simulate_trade(
    trade: Trade,
    df_4h: pd.DataFrame,
    entry_idx: int,
    atr_series: pd.Series,
) -> Trade:
    """
    Simulate the full V4 trade lifecycle on subsequent 4H candles.

    Lifecycle:
      1. SL hit → LOSS_SL (or BE_EXIT if BE was active)
      2. BE activation at +0.5R
      3. TP1 partial close (50 %), activate BE + trailing stop
      4. Trailing stop update (1 ATR trail after TP1)
      5. TP2 partial close (30 %)
      6. TP3 full close (remaining 20 %)
      7. Timeout after TIMEOUT_CANDLES candles
    """
    atr  = trade.atr_at_entry
    cost = trade.cost_per_unit
    be_trigger = (
        trade.entry_price + BE_ACTIVATION_R * atr
        if trade.direction == "BUY"
        else trade.entry_price - BE_ACTIVATION_R * atr
    )

    n = len(df_4h)
    for i in range(entry_idx + 1, min(entry_idx + TIMEOUT_CANDLES + 1, n)):
        high  = float(df_4h["high"].iloc[i])
        low   = float(df_4h["low"].iloc[i])
        close = float(df_4h["close"].iloc[i])
        dt    = df_4h["datetime"].iloc[i]

        # MAE / MFE tracking
        if trade.direction == "BUY":
            adverse    = trade.entry_price - low
            favourable = high - trade.entry_price
        else:
            adverse    = high - trade.entry_price
            favourable = trade.entry_price - low
        trade.max_adverse    = max(trade.max_adverse,    adverse)
        trade.max_favourable = max(trade.max_favourable, favourable)

        # 1. SL hit
        sl_hit = (
            (trade.direction == "BUY"  and low  <= trade.current_sl) or
            (trade.direction == "SELL" and high >= trade.current_sl)
        )
        if sl_hit:
            exit_p = trade.current_sl
            pnl_pts = (
                (exit_p - trade.entry_price) if trade.direction == "BUY"
                else (trade.entry_price - exit_p)
            )
            trade.gross_pnl  = trade.partial_pnl + pnl_pts * trade.remaining_pos
            trade.net_pnl    = trade.gross_pnl - cost
            trade.exit_price = exit_p
            trade.exit_time  = dt
            trade.result     = "BE_EXIT" if trade.be_activated else "LOSS_SL"
            return trade

        # 2. BE activation
        if not trade.be_activated:
            be_reached = (
                (trade.direction == "BUY"  and high >= be_trigger) or
                (trade.direction == "SELL" and low  <= be_trigger)
            )
            if be_reached:
                trade.be_activated = True
                trade.current_sl   = trade.entry_price
                trade.ts_active    = True

        # 3. TP1 partial close (50 %)
        if not trade.tp1_hit:
            tp1_reached = (
                (trade.direction == "BUY"  and high >= trade.tp1_price) or
                (trade.direction == "SELL" and low  <= trade.tp1_price)
            )
            if tp1_reached:
                trade.tp1_hit = True
                pct = PARTIAL_SIZES["TP1"]
                pnl_pts = (
                    (trade.tp1_price - trade.entry_price) if trade.direction == "BUY"
                    else (trade.entry_price - trade.tp1_price)
                )
                trade.partial_pnl  += pnl_pts * pct
                trade.remaining_pos = round(trade.remaining_pos - pct, 4)
                if not trade.be_activated:
                    trade.be_activated = True
                    trade.current_sl   = trade.entry_price
                    trade.ts_active    = True

        # 4. Trailing stop update (1 ATR trail)
        if trade.ts_active and atr > 0:
            if trade.direction == "BUY":
                new_sl = round(close - atr, 2)
                if new_sl > trade.current_sl:
                    trade.current_sl = new_sl
            else:
                new_sl = round(close + atr, 2)
                if new_sl < trade.current_sl:
                    trade.current_sl = new_sl

        # 5. TP2 partial close (30 %)
        if trade.tp1_hit and not trade.tp2_hit:
            tp2_reached = (
                (trade.direction == "BUY"  and high >= trade.tp2_price) or
                (trade.direction == "SELL" and low  <= trade.tp2_price)
            )
            if tp2_reached:
                trade.tp2_hit = True
                pct = PARTIAL_SIZES["TP2"]
                pnl_pts = (
                    (trade.tp2_price - trade.entry_price) if trade.direction == "BUY"
                    else (trade.entry_price - trade.tp2_price)
                )
                trade.partial_pnl  += pnl_pts * pct
                trade.remaining_pos = round(trade.remaining_pos - pct, 4)

        # 6. TP3 full close (remaining 20 %)
        if trade.tp2_hit and not trade.tp3_hit:
            tp3_reached = (
                (trade.direction == "BUY"  and high >= trade.tp3_price) or
                (trade.direction == "SELL" and low  <= trade.tp3_price)
            )
            if tp3_reached:
                trade.tp3_hit = True
                pnl_pts = (
                    (trade.tp3_price - trade.entry_price) if trade.direction == "BUY"
                    else (trade.entry_price - trade.tp3_price)
                )
                trade.gross_pnl  = trade.partial_pnl + pnl_pts * trade.remaining_pos
                trade.net_pnl    = trade.gross_pnl - cost
                trade.exit_price = trade.tp3_price
                trade.exit_time  = dt
                trade.result     = "WIN_TP3"
                return trade

    # 7. Timeout
    last_i   = min(entry_idx + TIMEOUT_CANDLES, n - 1)
    exit_p   = float(df_4h["close"].iloc[last_i])
    exit_dt  = df_4h["datetime"].iloc[last_i]
    pnl_pts  = (
        (exit_p - trade.entry_price) if trade.direction == "BUY"
        else (trade.entry_price - exit_p)
    )
    trade.gross_pnl  = trade.partial_pnl + pnl_pts * trade.remaining_pos
    trade.net_pnl    = trade.gross_pnl - cost
    trade.exit_price = exit_p
    trade.exit_time  = exit_dt
    trade.result     = "TIMEOUT"
    return trade


# ---------------------------------------------------------------------------
# Statistics aggregation
# ---------------------------------------------------------------------------

def _compute_split_stats(
    trades: List[Trade],
    start_balance: float,
    label: str,
) -> SplitStats:
    """Compute SplitStats from a list of completed trades."""
    stats = SplitStats(label=label)
    stats.trades = trades

    if not trades:
        stats.final_balance = start_balance
        return stats

    balance      = start_balance
    peak_balance = start_balance
    equity_curve = [balance]

    for t in trades:
        sl_distance = abs(t.entry_price - t.sl_price)
        lot_size = (
            (balance * RISK_PER_TRADE_PCT / 100.0) / sl_distance
            if sl_distance > 0 else 0.01
        )
        trade_pnl = t.net_pnl * lot_size
        balance  += trade_pnl
        balance   = max(balance, 0.01)
        equity_curve.append(balance)

        if balance > peak_balance:
            peak_balance = balance
        dd_usd = peak_balance - balance
        dd_pct = (dd_usd / peak_balance * 100.0) if peak_balance > 0 else 0.0
        if dd_pct > stats.max_drawdown_pct:
            stats.max_drawdown_pct = dd_pct
            stats.max_drawdown_usd = dd_usd

        month_key = t.entry_time.strftime("%Y-%m")
        stats.monthly_pnl[month_key] = stats.monthly_pnl.get(month_key, 0.0) + trade_pnl

        if t.be_activated:
            stats.be_activations += 1
        if t.tp1_hit:
            stats.partial_closes += 1
        if t.tp2_hit:
            stats.partial_closes += 1
        if t.tp3_hit:
            stats.partial_closes += 1

    stats.equity_curve    = equity_curve
    stats.total_trades    = len(trades)
    stats.wins            = sum(1 for t in trades if t.result.startswith("WIN"))
    stats.losses          = sum(1 for t in trades if t.result == "LOSS_SL")
    stats.timeouts        = sum(1 for t in trades if t.result == "TIMEOUT")
    stats.be_exits        = sum(1 for t in trades if t.result == "BE_EXIT")
    stats.win_rate        = stats.wins / stats.total_trades * 100.0
    stats.gross_pnl       = sum(t.gross_pnl for t in trades)
    stats.net_pnl         = sum(t.net_pnl   for t in trades)
    stats.total_costs     = stats.gross_pnl - stats.net_pnl
    stats.final_balance   = balance
    stats.return_pct      = (balance - start_balance) / start_balance * 100.0

    gross_profit = sum(t.net_pnl for t in trades if t.net_pnl > 0)
    gross_loss   = abs(sum(t.net_pnl for t in trades if t.net_pnl < 0))
    stats.profit_factor  = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    if len(equity_curve) > 2:
        returns = [
            (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
            for i in range(1, len(equity_curve))
            if equity_curve[i - 1] > 0
        ]
        if returns:
            mean_r = sum(returns) / len(returns)
            var_r  = sum((r - mean_r) ** 2 for r in returns) / len(returns)
            std_r  = math.sqrt(var_r) if var_r > 0 else 0.0
            # Annualise: ~6 4H candles/day × 252 trading days ≈ 1 512 periods/year
            stats.sharpe_ratio = (mean_r / std_r * math.sqrt(1512)) if std_r > 0 else 0.0

    return stats


# ---------------------------------------------------------------------------
# MTF slice helper
# ---------------------------------------------------------------------------

def _slice_mtf_df(df: Optional[pd.DataFrame], current_dt: datetime, window: int = 100) -> Optional[pd.DataFrame]:
    """
    Return the last `window` rows of df whose datetime <= current_dt.
    Returns None if fewer than 30 rows are available (MTF needs 30+).
    """
    if df is None or df.empty:
        return None
    mask = df["datetime"] <= current_dt
    sub  = df[mask].tail(window)
    return sub if len(sub) >= 30 else None


# ---------------------------------------------------------------------------
# Core walk-forward engine
# ---------------------------------------------------------------------------

async def _run_walkforward(
    pair: str,
    cfg: Dict,
    tfs: Dict[str, Optional[pd.DataFrame]],
    system: "HybridPortfolioSystemV3",
    rng: random.Random,
) -> PairResult:
    """
    Walk forward one 4H candle at a time and call the production signal.

    Returns a PairResult with all trades and split statistics.
    """
    df_4h_full    = tfs.get(TF_4H)
    df_1h_full    = tfs.get(TF_1H)
    df_daily_full = tfs.get(TF_DAILY)
    df_weekly_full= tfs.get(TF_WEEKLY)

    if df_4h_full is None or len(df_4h_full) < WARMUP_CANDLES + TIMEOUT_CANDLES + 10:
        print(f"  ✗ Insufficient 4H data for {pair} — skipping.", flush=True)
        return PairResult(pair=pair, data_source="insufficient", candles_4h=0, split_idx=0)

    n_4h      = len(df_4h_full)
    split_idx = int(n_4h * IN_SAMPLE_RATIO)

    # Pre-compute ATR for the full 4H series
    atr_series = _compute_atr_series(df_4h_full, ATR_PERIOD)

    # ATR stats (skip NaN warmup)
    valid_atrs = atr_series.dropna().values
    atr_mean   = float(valid_atrs.mean()) if len(valid_atrs) else 0.0
    atr_min    = float(valid_atrs.min())  if len(valid_atrs) else 0.0
    atr_max    = float(valid_atrs.max())  if len(valid_atrs) else 0.0

    result = PairResult(
        pair=pair,
        data_source="real",
        candles_4h=n_4h,
        split_idx=split_idx,
        atr_mean=atr_mean,
        atr_min=atr_min,
        atr_max=atr_max,
    )

    strategy_trades_is:  List[Trade] = []
    strategy_trades_oos: List[Trade] = []
    random_trades_is:    List[Trade] = []
    random_trades_oos:   List[Trade] = []

    cost = cfg["spread"] + cfg["commission"] + cfg["slippage"]

    print(f"\n  Walking forward {n_4h - WARMUP_CANDLES} candles "
          f"(split at candle {split_idx}) …", flush=True)

    for idx in range(WARMUP_CANDLES, n_4h - TIMEOUT_CANDLES):
        current_dt = df_4h_full["datetime"].iloc[idx]
        atr        = float(atr_series.iloc[idx])
        if np.isnan(atr) or atr <= 0:
            continue

        # ── Build rolling windows ────────────────────────────────────────────
        df_4h_window    = df_4h_full.iloc[max(0, idx - ROLLING_WINDOW_4H + 1): idx + 1].copy()
        df_daily_window = _slice_mtf_df(df_daily_full, current_dt, ROLLING_WINDOW_DAILY)

        # MTF slices (no-lookahead: only data up to current_dt)
        mtf_dfs = {
            "1h":     _slice_mtf_df(df_1h_full,    current_dt, 100),
            "4h":     df_4h_window,
            "1day":   df_daily_window,
            "1week":  _slice_mtf_df(df_weekly_full, current_dt, 52),
        }

        # ── Call production signal ───────────────────────────────────────────
        try:
            # Patch MTF confirmation to use pre-fetched data (no live API calls)
            mtf_result = system.mtf_confirmation.analyze_sync(
                dfs={tf: df for tf, df in mtf_dfs.items() if df is not None},
                symbol=pair,
            )
            # Temporarily override the async analyze() to return the sync result
            # by monkey-patching for this one call
            _orig_analyze = system.mtf_confirmation.analyze

            async def _patched_analyze(symbol_arg: str) -> Dict:
                return mtf_result

            system.mtf_confirmation.analyze = _patched_analyze  # type: ignore[method-assign]

            signal_result = await asyncio.wait_for(
                system.generate_signal(
                    symbol=pair,
                    df_4h=df_4h_window,
                    df_daily=df_daily_window,
                ),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            signal_result = {"signal": "NEUTRAL", "confidence": 0.0, "meets_threshold": False}
        except Exception as exc:
            logger.debug(f"Signal error at idx={idx}: {exc}")
            signal_result = {"signal": "NEUTRAL", "confidence": 0.0, "meets_threshold": False}
        finally:
            # Restore original async analyze
            try:
                system.mtf_confirmation.analyze = _orig_analyze  # type: ignore[method-assign]
            except Exception:
                pass

        result.signals_raw += 1
        signal     = signal_result.get("signal", "NEUTRAL")
        confidence = float(signal_result.get("confidence", 0.0))

        # ── Confidence filter ────────────────────────────────────────────────
        if signal not in ("BUY", "SELL") or confidence < CONFIDENCE_THRESHOLD:
            continue

        result.signals_used += 1
        entry = float(df_4h_full["close"].iloc[idx])

        # ── Build TP / SL levels ─────────────────────────────────────────────
        if signal == "BUY":
            sl_price  = round(entry - atr * cfg["atr_sl"],  cfg["decimals"])
            tp1_price = round(entry + atr * cfg["atr_tp1"], cfg["decimals"])
            tp2_price = round(entry + atr * cfg["atr_tp2"], cfg["decimals"])
            tp3_price = round(entry + atr * cfg["atr_tp3"], cfg["decimals"])
        else:
            sl_price  = round(entry + atr * cfg["atr_sl"],  cfg["decimals"])
            tp1_price = round(entry - atr * cfg["atr_tp1"], cfg["decimals"])
            tp2_price = round(entry - atr * cfg["atr_tp2"], cfg["decimals"])
            tp3_price = round(entry - atr * cfg["atr_tp3"], cfg["decimals"])

        if abs(entry - sl_price) < cfg["pip_value"]:
            continue

        # ── Strategy trade ───────────────────────────────────────────────────
        strat_trade = Trade(
            pair=pair, direction=signal,
            entry_price=entry, entry_time=current_dt,
            sl_price=sl_price, tp1_price=tp1_price,
            tp2_price=tp2_price, tp3_price=tp3_price,
            atr_at_entry=atr, cost_per_unit=cost,
            candle_idx=idx, confidence=confidence,
            signal_source="strategy",
        )
        strat_trade = _simulate_trade(strat_trade, df_4h_full, idx, atr_series)

        # ── Random baseline trade (same entry, same TP/SL, random direction) ─
        rand_dir = "BUY" if rng.random() < 0.5 else "SELL"
        if rand_dir == "BUY":
            r_sl  = round(entry - atr * cfg["atr_sl"],  cfg["decimals"])
            r_tp1 = round(entry + atr * cfg["atr_tp1"], cfg["decimals"])
            r_tp2 = round(entry + atr * cfg["atr_tp2"], cfg["decimals"])
            r_tp3 = round(entry + atr * cfg["atr_tp3"], cfg["decimals"])
        else:
            r_sl  = round(entry + atr * cfg["atr_sl"],  cfg["decimals"])
            r_tp1 = round(entry - atr * cfg["atr_tp1"], cfg["decimals"])
            r_tp2 = round(entry - atr * cfg["atr_tp2"], cfg["decimals"])
            r_tp3 = round(entry - atr * cfg["atr_tp3"], cfg["decimals"])

        rand_trade = Trade(
            pair=pair, direction=rand_dir,
            entry_price=entry, entry_time=current_dt,
            sl_price=r_sl, tp1_price=r_tp1,
            tp2_price=r_tp2, tp3_price=r_tp3,
            atr_at_entry=atr, cost_per_unit=cost,
            candle_idx=idx, confidence=50.0,
            signal_source="random",
        )
        rand_trade = _simulate_trade(rand_trade, df_4h_full, idx, atr_series)

        # ── Assign to in-sample or out-of-sample ─────────────────────────────
        if idx < split_idx:
            strategy_trades_is.append(strat_trade)
            random_trades_is.append(rand_trade)
        else:
            strategy_trades_oos.append(strat_trade)
            random_trades_oos.append(rand_trade)

    # ── Compute statistics ───────────────────────────────────────────────────
    result.in_sample  = _compute_split_stats(strategy_trades_is,  ACCOUNT_BALANCE, "in-sample")
    result.out_sample = _compute_split_stats(strategy_trades_oos, ACCOUNT_BALANCE, "out-of-sample")
    result.random_is  = _compute_split_stats(random_trades_is,    ACCOUNT_BALANCE, "random-in-sample")
    result.random_oos = _compute_split_stats(random_trades_oos,   ACCOUNT_BALANCE, "random-out-of-sample")

    return result


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

_SEP  = "─" * 72
_SEP2 = "═" * 72


def _bar(value: float, max_val: float, width: int = 18, char: str = "█") -> str:
    if max_val <= 0:
        return ""
    filled = max(0, min(int(round(value / max_val * width)), width))
    return char * filled + "░" * (width - filled)


def _pf_str(pf: float) -> str:
    return "∞" if pf == float("inf") else f"{pf:.2f}"


def _print_split(stats: SplitStats, label: str) -> None:
    """Print one split's statistics block."""
    sign = "+" if stats.return_pct >= 0 else ""
    print(f"\n  ┌─ {label.upper()} ({'%d trades' % stats.total_trades})")
    print(f"  │  Win rate      : {stats.win_rate:>6.1f}%  "
          f"(W={stats.wins} L={stats.losses} BE={stats.be_exits} T={stats.timeouts})")
    print(f"  │  Return        : {sign}{stats.return_pct:>+7.2f}%  "
          f"(${stats.final_balance:,.2f} final)")
    print(f"  │  Profit factor : {_pf_str(stats.profit_factor):>6}  "
          f"Sharpe: {stats.sharpe_ratio:>6.2f}")
    print(f"  │  Max drawdown  : {stats.max_drawdown_pct:>6.2f}%  "
          f"(${stats.max_drawdown_usd:,.2f})")
    print(f"  └─ Net P&L (units): {stats.net_pnl:>+10.2f}  "
          f"Costs: {stats.total_costs:>+8.2f}")


def _print_comparison(strat: SplitStats, rand: SplitStats, label: str) -> None:
    """Print strategy vs random comparison for one split."""
    edge_wr  = strat.win_rate  - rand.win_rate
    edge_ret = strat.return_pct - rand.return_pct
    edge_pf  = (
        (strat.profit_factor - rand.profit_factor)
        if strat.profit_factor != float("inf") and rand.profit_factor != float("inf")
        else float("nan")
    )
    print(f"\n  ┌─ STRATEGY vs RANDOM  [{label}]")
    print(f"  │  {'Metric':<22} {'Strategy':>10}  {'Random':>10}  {'Edge':>10}")
    print(f"  │  {'─'*22} {'─'*10}  {'─'*10}  {'─'*10}")
    print(f"  │  {'Win rate':<22} {strat.win_rate:>9.1f}%  {rand.win_rate:>9.1f}%  "
          f"{edge_wr:>+9.1f}%")
    print(f"  │  {'Return':<22} {strat.return_pct:>+9.2f}%  {rand.return_pct:>+9.2f}%  "
          f"{edge_ret:>+9.2f}%")
    pf_s = _pf_str(strat.profit_factor)
    pf_r = _pf_str(rand.profit_factor)
    pf_e = f"{edge_pf:>+9.2f}" if not math.isnan(edge_pf) else "       N/A"
    print(f"  │  {'Profit factor':<22} {pf_s:>10}  {pf_r:>10}  {pf_e}")
    print(f"  │  {'Sharpe':<22} {strat.sharpe_ratio:>10.2f}  {rand.sharpe_ratio:>10.2f}  "
          f"{strat.sharpe_ratio - rand.sharpe_ratio:>+10.2f}")
    print(f"  │  {'Max drawdown':<22} {strat.max_drawdown_pct:>9.2f}%  "
          f"{rand.max_drawdown_pct:>9.2f}%  "
          f"{strat.max_drawdown_pct - rand.max_drawdown_pct:>+9.2f}%")
    has_edge = edge_ret > 0 and edge_wr > 0
    verdict  = "✅ EDGE DETECTED" if has_edge else "❌ NO CLEAR EDGE"
    print(f"  └─ Verdict: {verdict}")


def _print_overfit_check(is_stats: SplitStats, oos_stats: SplitStats) -> None:
    """Print in-sample vs out-of-sample overfit check."""
    ret_decay = is_stats.return_pct - oos_stats.return_pct
    wr_decay  = is_stats.win_rate   - oos_stats.win_rate
    print(f"\n  ┌─ OVERFIT CHECK  (in-sample → out-of-sample)")
    print(f"  │  {'Metric':<22} {'In-Sample':>10}  {'Out-Sample':>10}  {'Decay':>10}")
    print(f"  │  {'─'*22} {'─'*10}  {'─'*10}  {'─'*10}")
    print(f"  │  {'Return':<22} {is_stats.return_pct:>+9.2f}%  "
          f"{oos_stats.return_pct:>+9.2f}%  {-ret_decay:>+9.2f}%")
    print(f"  │  {'Win rate':<22} {is_stats.win_rate:>9.1f}%  "
          f"{oos_stats.win_rate:>9.1f}%  {-wr_decay:>+9.1f}%")
    print(f"  │  {'Sharpe':<22} {is_stats.sharpe_ratio:>10.2f}  "
          f"{oos_stats.sharpe_ratio:>10.2f}  "
          f"{oos_stats.sharpe_ratio - is_stats.sharpe_ratio:>+10.2f}")
    print(f"  │  {'Max drawdown':<22} {is_stats.max_drawdown_pct:>9.2f}%  "
          f"{oos_stats.max_drawdown_pct:>9.2f}%")
    overfit = ret_decay > 10 or wr_decay > 10
    verdict = "⚠️  POSSIBLE OVERFIT" if overfit else "✅ GENERALISES WELL"
    print(f"  └─ Verdict: {verdict}")


def print_pair_report(result: PairResult) -> None:
    """Print the full report for one pair."""
    print(f"\n{_SEP2}")
    print(f"  PRODUCTION SIGNAL BACKTEST — {result.pair}  [{result.data_source.upper()} DATA]")
    print(f"{_SEP2}")

    print(f"\n  DATA SUMMARY")
    print(f"  {'4H candles fetched':<30} {result.candles_4h:>8}")
    print(f"  {'In-sample candles':<30} {result.split_idx:>8}  "
          f"({result.split_idx / result.candles_4h * 100:.0f}%)")
    print(f"  {'Out-of-sample candles':<30} {result.candles_4h - result.split_idx:>8}  "
          f"({(result.candles_4h - result.split_idx) / result.candles_4h * 100:.0f}%)")
    print(f"  {'ATR mean / min / max':<30} "
          f"{result.atr_mean:.2f} / {result.atr_min:.2f} / {result.atr_max:.2f}")
    print(f"  {'Signals generated (raw)':<30} {result.signals_raw:>8}")
    print(f"  {'Signals after ≥{:.0f}% filter'.format(CONFIDENCE_THRESHOLD):<30} "
          f"{result.signals_used:>8}  "
          f"({result.signals_used / result.signals_raw * 100:.1f}% pass rate)"
          if result.signals_raw > 0 else
          f"  {'Signals after filter':<30} {'0':>8}")

    print(f"\n{_SEP}")
    print(f"  STRATEGY PERFORMANCE")
    print(f"{_SEP}")
    _print_split(result.in_sample,  "In-Sample  (60%)")
    _print_split(result.out_sample, "Out-of-Sample (40%)")

    print(f"\n{_SEP}")
    print(f"  RANDOM BASELINE PERFORMANCE")
    print(f"{_SEP}")
    _print_split(result.random_is,  "Random In-Sample")
    _print_split(result.random_oos, "Random Out-of-Sample")

    print(f"\n{_SEP}")
    print(f"  EDGE ANALYSIS")
    print(f"{_SEP}")
    _print_comparison(result.in_sample,  result.random_is,  "In-Sample")
    _print_comparison(result.out_sample, result.random_oos, "Out-of-Sample")

    print(f"\n{_SEP}")
    print(f"  OVERFIT ANALYSIS")
    print(f"{_SEP}")
    _print_overfit_check(result.in_sample, result.out_sample)

    # Last 10 strategy trades (out-of-sample)
    oos_trades = result.out_sample.trades
    if oos_trades:
        print(f"\n{_SEP}")
        print(f"  LAST 10 OUT-OF-SAMPLE TRADES")
        print(f"{_SEP}")
        print(f"  {'#':<4} {'Dir':<5} {'Conf':>5} {'Entry':>8} {'Exit':>8} "
              f"{'Result':<12} {'Net P&L':>9}  {'BE':>3} {'TP1':>3}")
        print(f"  {'-'*4} {'-'*5} {'-'*5} {'-'*8} {'-'*8} "
              f"{'-'*12} {'-'*9}  {'-'*3} {'-'*3}")
        for i, t in enumerate(oos_trades[-10:], 1):
            ep   = f"{t.exit_price:.2f}" if t.exit_price else "—"
            be   = "✓" if t.be_activated else " "
            tp1  = "✓" if t.tp1_hit else " "
            sign = "+" if t.net_pnl >= 0 else ""
            print(
                f"  {i:<4} {t.direction:<5} {t.confidence:>4.0f}% "
                f"{t.entry_price:>8.2f} {ep:>8} "
                f"{t.result:<12} {sign}{t.net_pnl:>+8.2f}  {be:>3} {tp1:>3}"
            )

    print(f"\n{_SEP2}\n")


def print_combined_summary(results: List[PairResult]) -> None:
    """Print a combined summary across all pairs."""
    if len(results) < 2:
        return

    print(f"\n{_SEP2}")
    print(f"  COMBINED SUMMARY — ALL PAIRS")
    print(f"{_SEP2}")

    for label, attr_is, attr_oos in [
        ("STRATEGY",       "in_sample",  "out_sample"),
        ("RANDOM BASELINE","random_is",  "random_oos"),
    ]:
        print(f"\n  {label}")
        print(f"  {'Pair':<10} {'IS Return':>10}  {'OOS Return':>10}  "
              f"{'IS WR':>7}  {'OOS WR':>7}  {'IS PF':>7}  {'OOS PF':>7}")
        print(f"  {'─'*10} {'─'*10}  {'─'*10}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*7}")
        for r in results:
            is_s  = getattr(r, attr_is)
            oos_s = getattr(r, attr_oos)
            print(
                f"  {r.pair:<10} "
                f"{is_s.return_pct:>+9.2f}%  {oos_s.return_pct:>+9.2f}%  "
                f"{is_s.win_rate:>6.1f}%  {oos_s.win_rate:>6.1f}%  "
                f"{_pf_str(is_s.profit_factor):>7}  {_pf_str(oos_s.profit_factor):>7}"
            )

    # Overall verdict
    strat_oos_returns = [r.out_sample.return_pct for r in results]
    rand_oos_returns  = [r.random_oos.return_pct  for r in results]
    avg_strat = sum(strat_oos_returns) / len(strat_oos_returns)
    avg_rand  = sum(rand_oos_returns)  / len(rand_oos_returns)
    print(f"\n  {'─'*72}")
    print(f"  Avg OOS return — Strategy: {avg_strat:>+7.2f}%  |  Random: {avg_rand:>+7.2f}%")
    if avg_strat > avg_rand + 2:
        verdict = "✅ PRODUCTION SIGNAL HAS EDGE OVER RANDOM ON OUT-OF-SAMPLE DATA"
    elif avg_strat > avg_rand:
        verdict = "⚠️  MARGINAL EDGE — NEEDS MORE DATA TO CONFIRM"
    else:
        verdict = "❌ NO EDGE — PRODUCTION SIGNAL DOES NOT BEAT RANDOM BASELINE"
    print(f"  VERDICT: {verdict}")
    print(f"{_SEP2}\n")


# ---------------------------------------------------------------------------
# MongoDB persistence
# ---------------------------------------------------------------------------

def _trade_to_dict(t: Trade) -> Dict:
    return {
        "pair":           t.pair,
        "direction":      t.direction,
        "signal_source":  t.signal_source,
        "confidence":     round(t.confidence, 2),
        "entry_price":    t.entry_price,
        "entry_time":     t.entry_time.isoformat(),
        "exit_price":     t.exit_price,
        "exit_time":      t.exit_time.isoformat() if t.exit_time else None,
        "sl_price":       t.sl_price,
        "tp1_price":      t.tp1_price,
        "tp2_price":      t.tp2_price,
        "tp3_price":      t.tp3_price,
        "atr_at_entry":   round(t.atr_at_entry, 5),
        "result":         t.result,
        "gross_pnl":      round(t.gross_pnl, 6),
        "net_pnl":        round(t.net_pnl, 6),
        "be_activated":   t.be_activated,
        "tp1_hit":        t.tp1_hit,
        "tp2_hit":        t.tp2_hit,
        "tp3_hit":        t.tp3_hit,
        "max_adverse":    round(t.max_adverse, 6),
        "max_favourable": round(t.max_favourable, 6),
    }


def _split_to_dict(s: SplitStats) -> Dict:
    pf = s.profit_factor if s.profit_factor != float("inf") else None
    return {
        "label":            s.label,
        "total_trades":     s.total_trades,
        "wins":             s.wins,
        "losses":           s.losses,
        "timeouts":         s.timeouts,
        "be_exits":         s.be_exits,
        "win_rate":         round(s.win_rate, 4),
        "gross_pnl":        round(s.gross_pnl, 4),
        "net_pnl":          round(s.net_pnl, 4),
        "total_costs":      round(s.total_costs, 4),
        "profit_factor":    round(pf, 4) if pf is not None else None,
        "sharpe_ratio":     round(s.sharpe_ratio, 4),
        "max_drawdown_pct": round(s.max_drawdown_pct, 4),
        "max_drawdown_usd": round(s.max_drawdown_usd, 4),
        "final_balance":    round(s.final_balance, 4),
        "return_pct":       round(s.return_pct, 4),
        "be_activations":   s.be_activations,
        "partial_closes":   s.partial_closes,
        "monthly_pnl":      {k: round(v, 4) for k, v in s.monthly_pnl.items()},
    }


def save_results_to_mongodb(results: List[PairResult]) -> Optional[str]:
    """Persist all backtest results to MongoDB."""
    if not _PYMONGO_AVAILABLE:
        print("  ⚠  pymongo not installed — skipping MongoDB save.", flush=True)
        return None

    mongo_url = os.environ.get("MONGO_URL", "").strip()
    if not mongo_url:
        print("  ⚠  MONGO_URL not set — skipping MongoDB save.", flush=True)
        return None

    try:
        client = MongoClient(mongo_url, serverSelectionTimeoutMS=5_000)
        db     = client[os.environ.get("DB_NAME", "gold_signals_v4")]
        col    = db["backtest_production_signal"]

        pairs_docs = []
        for r in results:
            all_strat_trades = r.in_sample.trades + r.out_sample.trades
            all_rand_trades  = r.random_is.trades  + r.random_oos.trades

            pairs_docs.append({
                "pair":         r.pair,
                "data_source":  r.data_source,
                "candles_4h":   r.candles_4h,
                "split_idx":    r.split_idx,
                "atr_mean":     round(r.atr_mean, 4),
                "atr_min":      round(r.atr_min, 4),
                "atr_max":      round(r.atr_max, 4),
                "signals_raw":  r.signals_raw,
                "signals_used": r.signals_used,
                "in_sample":    _split_to_dict(r.in_sample),
                "out_sample":   _split_to_dict(r.out_sample),
                "random_is":    _split_to_dict(r.random_is),
                "random_oos":   _split_to_dict(r.random_oos),
                "strategy_trades": [_trade_to_dict(t) for t in all_strat_trades],
                "random_trades":   [_trade_to_dict(t) for t in all_rand_trades],
            })

        document = {
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "script":      "backtest_production_signal.py",
            "version":     "1.0.0",
            "config": {
                "confidence_threshold": CONFIDENCE_THRESHOLD,
                "warmup_candles":       WARMUP_CANDLES,
                "rolling_window_4h":    ROLLING_WINDOW_4H,
                "timeout_candles":      TIMEOUT_CANDLES,
                "be_activation_r":      BE_ACTIVATION_R,
                "account_balance":      ACCOUNT_BALANCE,
                "risk_per_trade_pct":   RISK_PER_TRADE_PCT,
                "in_sample_ratio":      IN_SAMPLE_RATIO,
                "fetch_outputsize":     FETCH_OUTPUTSIZE,
            },
            "pairs": pairs_docs,
        }

        res    = col.insert_one(document)
        doc_id = str(res.inserted_id)
        client.close()
        print(f"  ✓ Results saved to MongoDB  (id={doc_id})", flush=True)
        return doc_id

    except Exception as exc:
        print(f"  ✗ MongoDB save failed: {exc}", flush=True)
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def _main_async() -> None:
    api_key = (
        os.environ.get("TWELVEDATA_API_KEY", "")
        or os.environ.get("TWELVE_DATA_API_KEY", "")
    ).strip()

    print(_SEP2)
    print("  PRODUCTION SIGNAL BACKTEST — HybridPortfolioSystemV3")
    print(f"  Pairs     : {', '.join(PAIRS)}")
    print(f"  Confidence: ≥{CONFIDENCE_THRESHOLD:.0f}%  |  Warmup: {WARMUP_CANDLES} candles")
    print(f"  Fetch size: {FETCH_OUTPUTSIZE} candles/TF  |  Timeout: {TIMEOUT_CANDLES} candles")
    print(f"  Split     : {IN_SAMPLE_RATIO*100:.0f}% in-sample / "
          f"{(1-IN_SAMPLE_RATIO)*100:.0f}% out-of-sample")
    print(f"  Account   : ${ACCOUNT_BALANCE:,.0f}  |  Risk/trade: {RISK_PER_TRADE_PCT}%")
    print(_SEP2)

    if not _PRODUCTION_AVAILABLE:
        print("\n❌ Production engine not available — cannot run backtest.", flush=True)
        sys.exit(1)

    if api_key:
        print(f"\n✓ TWELVEDATA_API_KEY detected — fetching real market data\n", flush=True)
    else:
        print(
            "\n⚠  TWELVEDATA_API_KEY not set.\n"
            "   Set TWELVEDATA_API_KEY to fetch real data.\n"
            "   Without it, no candles can be fetched and the backtest cannot run.\n",
            flush=True,
        )
        sys.exit(1)

    # Initialise production system (one shared instance across pairs)
    print("Initialising HybridPortfolioSystemV3 …", flush=True)
    system = HybridPortfolioSystemV3(account_balance=ACCOUNT_BALANCE)
    rng    = random.Random(42)   # Fixed seed for reproducible random baseline

    all_results: List[PairResult] = []

    for pair, cfg in PAIRS.items():
        print(f"\n{'─'*72}")
        print(f"  Processing {pair} ({cfg['symbol']}) …", flush=True)

        # Fetch all timeframes
        tfs = fetch_all_timeframes(
            symbol_td=cfg["symbol"],
            api_key=api_key,
            outputsize=FETCH_OUTPUTSIZE,
            rate_limit_sleep=1.2,
        )

        df_4h = tfs.get(TF_4H)
        if df_4h is None or len(df_4h) < WARMUP_CANDLES + TIMEOUT_CANDLES + 10:
            print(f"  ✗ Insufficient 4H data for {pair} — skipping.", flush=True)
            continue

        print(f"\n  Running walk-forward backtest …", flush=True)
        result = await _run_walkforward(pair, cfg, tfs, system, rng)
        all_results.append(result)

        print_pair_report(result)

    if not all_results:
        print("\n❌ No pairs completed successfully.", flush=True)
        sys.exit(1)

    print_combined_summary(all_results)

    print("Saving results to MongoDB …", flush=True)
    doc_id = save_results_to_mongodb(all_results)
    if doc_id:
        print(f"✓ MongoDB document ID: {doc_id}\n", flush=True)
    else:
        print("  (MongoDB save skipped or failed — results printed above)\n", flush=True)


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()

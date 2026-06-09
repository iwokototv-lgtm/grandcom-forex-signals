#!/usr/bin/env python3
"""
backtest_twelvedata.py — Real-Data Backtest for Gold Trading Strategy
======================================================================
Fetches real XAUUSD and XAUEUR 4H candles from TwelveData and runs the
full V4 trade-manager lifecycle simulation (BE activation, partial closes
at TP1, trailing stops) on actual historical prices.

Falls back to synthetic random-walk data if the API is unavailable or the
key is not set, so the script is always runnable in CI / offline environments.

Usage
-----
    python backtest_twelvedata.py

Environment variables
---------------------
    TWELVEDATA_API_KEY   — TwelveData API key (required for real data)

Output
------
    Comprehensive per-pair backtest report printed to stdout, including:
      • Candle fetch summary
      • ATR statistics
      • Trade-by-trade lifecycle (BE, partial closes, trailing stops)
      • Win rate, P&L, max drawdown, profit factor, Sharpe ratio
      • Monthly performance breakdown
"""

from __future__ import annotations

import json
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
from typing import Optional

try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
    _PYMONGO_AVAILABLE = True
except ImportError:
    _PYMONGO_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAIRS: dict[str, dict] = {
    "XAUUSD": {
        "symbol":    "XAU/USD",
        "decimals":  2,
        "pip_value": 0.1,       # 1 pip = $0.10 per oz; 1 lot = 100 oz → $10/pip
        # V4 ATR multipliers (mirrors gold_server_v4.py PAIRS config)
        "atr_sl":    1.0,
        "atr_tp1":   0.40,
        "atr_tp2":   0.80,
        "atr_tp3":   1.40,
        # Realistic transaction costs (spread + commission + slippage) in price units
        "spread":    0.30,      # ~$0.30 typical spread on gold
        "commission": 0.10,     # ~$0.10 round-turn commission per oz equivalent
        "slippage":  0.10,      # ~$0.10 average slippage on entry/exit
    },
    "XAUEUR": {
        "symbol":    "XAU/EUR",
        "decimals":  2,
        "pip_value": 0.1,
        "atr_sl":    1.0,
        "atr_tp1":   0.40,
        "atr_tp2":   0.80,
        "atr_tp3":   1.40,
        "spread":    0.35,      # Slightly wider spread on EUR-denominated gold
        "commission": 0.10,
        "slippage":  0.12,
    },
}

# Partial-profit schedule (mirrors trade_manager.py PARTIAL_SIZES)
PARTIAL_SIZES: dict[str, float] = {
    "TP1": 0.50,   # Close 50% at TP1
    "TP2": 0.30,   # Close 30% at TP2
    "TP3": 0.20,   # Close 20% at TP3
}

ATR_PERIOD          = 14        # Wilder smoothing period for ATR
MAX_CANDLES         = 500       # Maximum candles to fetch per pair
INTERVAL            = "4h"      # Candle interval
ACCOUNT_BALANCE     = 10_000.0  # Starting account balance (USD)
RISK_PER_TRADE_PCT  = 1.0       # Risk 1% of account per trade
MAX_TRADES_PER_DAY  = 3         # Daily trade cap (mirrors backtest_engine.py)
TIMEOUT_CANDLES     = 60        # Max candles before a trade is timed out
BE_ACTIVATION_R     = 0.5       # Breakeven activates at +0.5R (mirrors V4 config)

# Signal generation thresholds (mirrors backtest_engine.py _generate_signal)
RSI_OVERSOLD        = 35
RSI_OVERBOUGHT      = 65
MA_FAST_PERIOD      = 20
MA_SLOW_PERIOD      = 50

TWELVEDATA_BASE_URL = "https://api.twelvedata.com/time_series"

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Candle:
    """Single OHLCV candle."""
    dt:    datetime
    open:  float
    high:  float
    low:   float
    close: float
    atr:   float = 0.0          # Populated after ATR calculation


@dataclass
class Trade:
    """Represents one simulated trade through the full V4 lifecycle."""
    pair:          str
    direction:     str           # "BUY" or "SELL"
    entry_price:   float
    entry_time:    datetime
    sl_price:      float         # Initial stop-loss
    tp1_price:     float
    tp2_price:     float
    tp3_price:     float
    atr_at_entry:  float
    cost_per_unit: float         # Total transaction cost (spread + commission + slippage)

    # Lifecycle state
    current_sl:    float = 0.0   # Tracks SL as BE / TS moves it
    be_activated:  bool  = False
    tp1_hit:       bool  = False
    tp2_hit:       bool  = False
    tp3_hit:       bool  = False
    ts_active:     bool  = False

    # Outcome
    exit_price:    Optional[float]    = None
    exit_time:     Optional[datetime] = None
    result:        str = "ACTIVE"    # WIN_TP1/WIN_TP2/WIN_TP3/LOSS_SL/TIMEOUT/BE_EXIT

    # P&L tracking (in price units, before cost deduction)
    gross_pnl:     float = 0.0
    net_pnl:       float = 0.0   # gross_pnl minus transaction costs
    max_adverse:   float = 0.0   # Maximum adverse excursion (price units)
    max_favourable:float = 0.0   # Maximum favourable excursion (price units)

    # Partial-profit tracking
    partial_pnl:   float = 0.0   # Accumulated P&L from partial closes
    remaining_pos: float = 1.0   # Fraction of position still open

    def __post_init__(self) -> None:
        self.current_sl = self.sl_price


@dataclass
class BacktestStats:
    """Aggregated statistics for one pair's backtest run."""
    pair:                str
    data_source:         str    # "real" or "synthetic"
    candles_fetched:     int    = 0
    candles_used:        int    = 0
    atr_mean:            float  = 0.0
    atr_min:             float  = 0.0
    atr_max:             float  = 0.0

    total_trades:        int    = 0
    wins:                int    = 0
    losses:              int    = 0
    timeouts:            int    = 0
    be_exits:            int    = 0

    win_rate:            float  = 0.0
    gross_pnl:           float  = 0.0
    net_pnl:             float  = 0.0
    total_costs:         float  = 0.0
    profit_factor:       float  = 0.0
    sharpe_ratio:        float  = 0.0
    max_drawdown_pct:    float  = 0.0
    max_drawdown_usd:    float  = 0.0
    final_balance:       float  = 0.0
    return_pct:          float  = 0.0

    be_activations:      int    = 0
    ts_updates:          int    = 0
    partial_closes:      int    = 0

    trades:              list   = field(default_factory=list)
    monthly_pnl:         dict   = field(default_factory=dict)
    equity_curve:        list   = field(default_factory=list)


# ---------------------------------------------------------------------------
# TwelveData API fetch (synchronous, no external dependencies)
# ---------------------------------------------------------------------------

def fetch_candles_from_twelvedata(
    symbol: str,
    interval: str = "4h",
    outputsize: int = 500,
    api_key: str = "",
) -> Optional[list[dict]]:
    """
    Fetch OHLCV candles from TwelveData REST API using only stdlib urllib.

    Returns a list of raw value dicts (newest-first as returned by the API),
    or None on any error.
    """
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
        print(f"  → Fetching {outputsize} {interval} candles for {symbol} …", flush=True)
        req = urllib.request.Request(url, headers={"User-Agent": "GoldBacktest/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            import json
            data = json.loads(resp.read().decode("utf-8"))

        if "values" not in data:
            msg = data.get("message", data.get("status", "unknown error"))
            print(f"  ✗ TwelveData API error for {symbol}: {msg}", flush=True)
            return None

        values = data["values"]
        print(f"  ✓ Received {len(values)} candles for {symbol}", flush=True)
        return values

    except urllib.error.HTTPError as exc:
        print(f"  ✗ HTTP {exc.code} fetching {symbol}: {exc.reason}", flush=True)
        return None
    except urllib.error.URLError as exc:
        print(f"  ✗ Network error fetching {symbol}: {exc.reason}", flush=True)
        return None
    except Exception as exc:
        print(f"  ✗ Unexpected error fetching {symbol}: {exc}", flush=True)
        return None


def parse_candles(raw_values: list[dict]) -> list[Candle]:
    """
    Parse raw TwelveData value dicts into Candle objects.

    TwelveData returns candles newest-first; we reverse to chronological order.
    """
    candles: list[Candle] = []
    for v in reversed(raw_values):
        try:
            dt_str = v["datetime"]
            # TwelveData returns "YYYY-MM-DD HH:MM:SS" (UTC)
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
            candles.append(Candle(
                dt=dt,
                open=float(v["open"]),
                high=float(v["high"]),
                low=float(v["low"]),
                close=float(v["close"]),
            ))
        except (KeyError, ValueError):
            continue
    return candles


# ---------------------------------------------------------------------------
# Synthetic data fallback
# ---------------------------------------------------------------------------

def generate_synthetic_candles(
    pair: str,
    n: int = 500,
    seed: int = 42,
) -> list[Candle]:
    """
    Generate a realistic synthetic gold price series using a geometric
    random walk with mean-reversion and volatility clustering.

    Used as a fallback when the TwelveData API is unavailable.
    """
    rng = random.Random(seed)

    # Realistic starting prices
    base_price = 2_000.0 if pair == "XAUUSD" else 1_850.0
    candles: list[Candle] = []

    price    = base_price
    vol      = 8.0          # Initial 4H volatility (price units)
    dt       = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    interval = timedelta(hours=4)

    for _ in range(n):
        # Volatility clustering (GARCH-like)
        vol = max(3.0, min(25.0, vol * rng.uniform(0.92, 1.08) + rng.gauss(0, 0.5)))

        # Candle body
        body_size = abs(rng.gauss(0, vol * 0.6))
        direction = 1 if rng.random() > 0.48 else -1   # Slight upward drift
        open_p    = price
        close_p   = price + direction * body_size

        # Wicks
        upper_wick = abs(rng.gauss(0, vol * 0.4))
        lower_wick = abs(rng.gauss(0, vol * 0.4))
        high_p     = max(open_p, close_p) + upper_wick
        low_p      = min(open_p, close_p) - lower_wick

        candles.append(Candle(
            dt=dt, open=open_p, high=high_p, low=low_p, close=close_p
        ))

        price = close_p
        dt   += interval

    return candles


# ---------------------------------------------------------------------------
# ATR calculation (Wilder smoothing — matches ta library used in production)
# ---------------------------------------------------------------------------

def calculate_atr(candles: list[Candle], period: int = ATR_PERIOD) -> list[Candle]:
    """
    Calculate ATR with Wilder smoothing and attach it to each Candle.

    True Range = max(H-L, |H-prev_C|, |L-prev_C|)
    ATR[0..period-1] = simple mean of first `period` TRs
    ATR[i]           = (ATR[i-1] * (period-1) + TR[i]) / period  (Wilder)
    """
    n = len(candles)
    if n < period + 1:
        return candles

    # Compute True Range for each candle
    trs: list[float] = []
    for i, c in enumerate(candles):
        if i == 0:
            trs.append(c.high - c.low)
        else:
            prev_close = candles[i - 1].close
            tr = max(
                c.high - c.low,
                abs(c.high - prev_close),
                abs(c.low  - prev_close),
            )
            trs.append(tr)

    # Seed with simple mean of first `period` TRs
    atr = sum(trs[:period]) / period
    candles[period - 1].atr = atr

    # Wilder smoothing for the rest
    for i in range(period, n):
        atr = (atr * (period - 1) + trs[i]) / period
        candles[i].atr = atr

    return candles


# ---------------------------------------------------------------------------
# Technical indicators for signal generation
# ---------------------------------------------------------------------------

def _sma(values: list[float], period: int, idx: int) -> Optional[float]:
    """Simple moving average at index idx."""
    if idx < period - 1:
        return None
    return sum(values[idx - period + 1 : idx + 1]) / period


def _rsi(closes: list[float], period: int, idx: int) -> Optional[float]:
    """Wilder RSI at index idx."""
    if idx < period:
        return None
    gains, losses = [], []
    for i in range(idx - period + 1, idx + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _macd(closes: list[float], idx: int) -> tuple[float, float]:
    """
    MACD line and signal line at index idx.
    Uses EMA(12) - EMA(26) for MACD, EMA(9) of MACD for signal.
    Returns (macd_line, signal_line).
    """
    if idx < 33:   # Need at least 26 + 9 - 1 bars
        return 0.0, 0.0

    def ema(period: int, end: int) -> float:
        k = 2.0 / (period + 1)
        val = closes[end - period + 1]
        for j in range(end - period + 2, end + 1):
            val = closes[j] * k + val * (1 - k)
        return val

    macd_vals = [
        ema(12, i) - ema(26, i)
        for i in range(26, idx + 1)
    ]
    if len(macd_vals) < 9:
        return macd_vals[-1], macd_vals[-1]

    # EMA(9) of MACD values
    k = 2.0 / 10
    sig = macd_vals[-9]
    for v in macd_vals[-8:]:
        sig = v * k + sig * (1 - k)

    return macd_vals[-1], sig


def generate_signal(
    candles: list[Candle],
    idx: int,
) -> Optional[str]:
    """
    Generate BUY / SELL signal on the closed candle at `idx`.

    Logic mirrors backtest_engine.py _generate_signal:
      - RSI not at extremes (RSI_OVERSOLD < rsi < RSI_OVERBOUGHT)
      - Price above/below MA20
      - MACD crossover in the signal direction
    """
    if idx < MA_SLOW_PERIOD + 1:
        return None

    closes = [c.close for c in candles]

    rsi = _rsi(closes, 14, idx)
    if rsi is None:
        return None

    ma20 = _sma(closes, MA_FAST_PERIOD, idx)
    ma50 = _sma(closes, MA_SLOW_PERIOD, idx)
    if ma20 is None or ma50 is None:
        return None

    macd_now,  sig_now  = _macd(closes, idx)
    macd_prev, sig_prev = _macd(closes, idx - 1)

    price = closes[idx]

    # BUY conditions
    buy_score = sum([
        RSI_OVERSOLD < rsi < RSI_OVERBOUGHT,
        price > ma20,
        macd_now > sig_now,
        macd_prev <= sig_prev,   # MACD cross up
    ])

    # SELL conditions
    sell_score = sum([
        RSI_OVERSOLD < rsi < RSI_OVERBOUGHT,
        price < ma20,
        macd_now < sig_now,
        macd_prev >= sig_prev,   # MACD cross down
    ])

    if buy_score >= 3:
        return "BUY"
    if sell_score >= 3:
        return "SELL"
    return None


# ---------------------------------------------------------------------------
# Trade lifecycle simulation (V4 BE + partial closes + trailing stop)
# ---------------------------------------------------------------------------

def simulate_trade(
    trade: Trade,
    candles: list[Candle],
    entry_idx: int,
    pair_cfg: dict,
) -> Trade:
    """
    Simulate the full V4 trade lifecycle on subsequent candles.

    Lifecycle (mirrors trade_manager.py run_management_cycle):
      1. Check SL hit → LOSS
      2. Check BE activation at +0.5R
      3. Check TP1 → partial close 50%, activate BE + trailing stop
      4. Update trailing stop (1 ATR trail after TP1)
      5. Check TP2 → partial close 30%
      6. Check TP3 → close remaining 20%
      7. Timeout after TIMEOUT_CANDLES candles
    """
    atr          = trade.atr_at_entry
    be_trigger   = (
        trade.entry_price + BE_ACTIVATION_R * atr
        if trade.direction == "BUY"
        else trade.entry_price - BE_ACTIVATION_R * atr
    )
    cost         = trade.cost_per_unit

    for i in range(entry_idx + 1, min(entry_idx + TIMEOUT_CANDLES + 1, len(candles))):
        c = candles[i]
        high, low = c.high, c.low

        # ── Max adverse / favourable excursion tracking ──────────────────────
        if trade.direction == "BUY":
            adverse    = trade.entry_price - low
            favourable = high - trade.entry_price
        else:
            adverse    = high - trade.entry_price
            favourable = trade.entry_price - low

        trade.max_adverse    = max(trade.max_adverse,    adverse)
        trade.max_favourable = max(trade.max_favourable, favourable)

        # ── 1. SL hit check ──────────────────────────────────────────────────
        sl_hit = (
            (trade.direction == "BUY"  and low  <= trade.current_sl) or
            (trade.direction == "SELL" and high >= trade.current_sl)
        )
        if sl_hit:
            exit_p = trade.current_sl
            # P&L on remaining position
            pnl_pts = (
                (exit_p - trade.entry_price) if trade.direction == "BUY"
                else (trade.entry_price - exit_p)
            )
            trade.gross_pnl = trade.partial_pnl + pnl_pts * trade.remaining_pos
            trade.net_pnl   = trade.gross_pnl - cost
            trade.exit_price = exit_p
            trade.exit_time  = c.dt
            trade.result     = "BE_EXIT" if trade.be_activated else "LOSS_SL"
            return trade

        # ── 2. BE activation at +0.5R ────────────────────────────────────────
        if not trade.be_activated:
            be_reached = (
                (trade.direction == "BUY"  and high >= be_trigger) or
                (trade.direction == "SELL" and low  <= be_trigger)
            )
            if be_reached:
                trade.be_activated = True
                trade.current_sl   = trade.entry_price   # Move SL to entry
                # TS activates together with BE (V4 behaviour)
                trade.ts_active    = True

        # ── 3. TP1 partial close (50%) ───────────────────────────────────────
        if not trade.tp1_hit:
            tp1_reached = (
                (trade.direction == "BUY"  and high >= trade.tp1_price) or
                (trade.direction == "SELL" and low  <= trade.tp1_price)
            )
            if tp1_reached:
                trade.tp1_hit = True
                partial_pct   = PARTIAL_SIZES["TP1"]
                pnl_pts       = (
                    (trade.tp1_price - trade.entry_price) if trade.direction == "BUY"
                    else (trade.entry_price - trade.tp1_price)
                )
                trade.partial_pnl  += pnl_pts * partial_pct
                trade.remaining_pos = round(trade.remaining_pos - partial_pct, 4)
                # Activate BE + TS on TP1 hit (if not already active)
                if not trade.be_activated:
                    trade.be_activated = True
                    trade.current_sl   = trade.entry_price
                    trade.ts_active    = True

        # ── 4. Trailing stop update (1 ATR trail, after TP1 / BE active) ─────
        if trade.ts_active and atr > 0:
            if trade.direction == "BUY":
                new_sl = round(c.close - atr, 2)
                if new_sl > trade.current_sl:
                    trade.current_sl = new_sl
            else:
                new_sl = round(c.close + atr, 2)
                if new_sl < trade.current_sl:
                    trade.current_sl = new_sl

        # ── 5. TP2 partial close (30%) ───────────────────────────────────────
        if trade.tp1_hit and not trade.tp2_hit:
            tp2_reached = (
                (trade.direction == "BUY"  and high >= trade.tp2_price) or
                (trade.direction == "SELL" and low  <= trade.tp2_price)
            )
            if tp2_reached:
                trade.tp2_hit = True
                partial_pct   = PARTIAL_SIZES["TP2"]
                pnl_pts       = (
                    (trade.tp2_price - trade.entry_price) if trade.direction == "BUY"
                    else (trade.entry_price - trade.tp2_price)
                )
                trade.partial_pnl  += pnl_pts * partial_pct
                trade.remaining_pos = round(trade.remaining_pos - partial_pct, 4)

        # ── 6. TP3 full close (remaining 20%) ────────────────────────────────
        if trade.tp2_hit and not trade.tp3_hit:
            tp3_reached = (
                (trade.direction == "BUY"  and high >= trade.tp3_price) or
                (trade.direction == "SELL" and low  <= trade.tp3_price)
            )
            if tp3_reached:
                trade.tp3_hit = True
                pnl_pts       = (
                    (trade.tp3_price - trade.entry_price) if trade.direction == "BUY"
                    else (trade.entry_price - trade.tp3_price)
                )
                trade.gross_pnl  = trade.partial_pnl + pnl_pts * trade.remaining_pos
                trade.net_pnl    = trade.gross_pnl - cost
                trade.exit_price = trade.tp3_price
                trade.exit_time  = c.dt
                trade.result     = "WIN_TP3"
                return trade

    # ── 7. Timeout ───────────────────────────────────────────────────────────
    last_idx = min(entry_idx + TIMEOUT_CANDLES, len(candles) - 1)
    last_c   = candles[last_idx]
    exit_p   = last_c.close
    pnl_pts  = (
        (exit_p - trade.entry_price) if trade.direction == "BUY"
        else (trade.entry_price - exit_p)
    )
    trade.gross_pnl  = trade.partial_pnl + pnl_pts * trade.remaining_pos
    trade.net_pnl    = trade.gross_pnl - cost
    trade.exit_price = exit_p
    trade.exit_time  = last_c.dt
    trade.result     = "TIMEOUT"
    return trade


# ---------------------------------------------------------------------------
# Main backtest runner
# ---------------------------------------------------------------------------

def run_backtest(pair: str, candles: list[Candle], data_source: str) -> BacktestStats:
    """
    Run the full backtest simulation for one pair.

    Returns a BacktestStats object with all metrics populated.
    """
    cfg   = PAIRS[pair]
    stats = BacktestStats(
        pair=pair,
        data_source=data_source,
        candles_fetched=len(candles),
    )

    # ── ATR calculation ──────────────────────────────────────────────────────
    candles = calculate_atr(candles, ATR_PERIOD)

    # ATR statistics (skip warmup candles with atr == 0)
    valid_atrs = [c.atr for c in candles if c.atr > 0]
    if valid_atrs:
        stats.atr_mean = sum(valid_atrs) / len(valid_atrs)
        stats.atr_min  = min(valid_atrs)
        stats.atr_max  = max(valid_atrs)

    # ── Signal generation and trade simulation ───────────────────────────────
    balance      = ACCOUNT_BALANCE
    peak_balance = ACCOUNT_BALANCE
    equity_curve = [balance]
    trades: list[Trade] = []

    # Daily trade cap tracking
    current_day:       Optional[datetime] = None
    daily_trade_count: int = 0

    # Warmup: skip first max(MA_SLOW_PERIOD, ATR_PERIOD) + 5 candles
    warmup = max(MA_SLOW_PERIOD, ATR_PERIOD) + 5
    stats.candles_used = max(0, len(candles) - warmup - TIMEOUT_CANDLES)

    for idx in range(warmup, len(candles) - TIMEOUT_CANDLES):
        c   = candles[idx]
        atr = c.atr
        if atr <= 0:
            continue

        # Daily trade cap
        trade_day = c.dt.date()
        if trade_day != (current_day.date() if current_day else None):
            current_day       = c.dt
            daily_trade_count = 0

        if daily_trade_count >= MAX_TRADES_PER_DAY:
            continue

        # Signal on closed candle
        signal = generate_signal(candles, idx)
        if signal is None:
            continue

        # ── Build TP / SL levels from ATR multipliers ────────────────────────
        entry = c.close
        cost  = cfg["spread"] + cfg["commission"] + cfg["slippage"]

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

        # Sanity check: SL must be at least 1 pip away
        if abs(entry - sl_price) < cfg["pip_value"]:
            continue

        trade = Trade(
            pair=pair,
            direction=signal,
            entry_price=entry,
            entry_time=c.dt,
            sl_price=sl_price,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            tp3_price=tp3_price,
            atr_at_entry=atr,
            cost_per_unit=cost,
        )

        # Simulate the trade lifecycle
        trade = simulate_trade(trade, candles, idx, cfg)
        trades.append(trade)
        daily_trade_count += 1

        # ── Position sizing: risk 1% of current balance ──────────────────────
        sl_distance = abs(entry - sl_price)
        # lot_size in oz-equivalent; P&L = pnl_pts * lot_size
        # We size so that sl_distance * lot_size = RISK_PER_TRADE_PCT% of balance
        lot_size = (balance * RISK_PER_TRADE_PCT / 100.0) / sl_distance if sl_distance > 0 else 0.01

        # ── Update balance ───────────────────────────────────────────────────
        trade_pnl = trade.net_pnl * lot_size
        balance  += trade_pnl
        balance   = max(balance, 0.01)   # Floor at near-zero
        equity_curve.append(balance)

        # ── Drawdown tracking ────────────────────────────────────────────────
        if balance > peak_balance:
            peak_balance = balance
        dd_usd = peak_balance - balance
        dd_pct = (dd_usd / peak_balance * 100.0) if peak_balance > 0 else 0.0
        if dd_pct > stats.max_drawdown_pct:
            stats.max_drawdown_pct = dd_pct
            stats.max_drawdown_usd = dd_usd

        # ── Monthly P&L tracking ─────────────────────────────────────────────
        month_key = trade.entry_time.strftime("%Y-%m")
        stats.monthly_pnl[month_key] = stats.monthly_pnl.get(month_key, 0.0) + trade_pnl

        # ── Lifecycle counters ───────────────────────────────────────────────
        if trade.be_activated:
            stats.be_activations += 1
        if trade.tp1_hit:
            stats.partial_closes += 1
        if trade.tp2_hit:
            stats.partial_closes += 1
        if trade.tp3_hit:
            stats.partial_closes += 1

    # ── Aggregate statistics ─────────────────────────────────────────────────
    stats.trades       = trades
    stats.equity_curve = equity_curve

    stats.total_trades = len(trades)
    stats.wins         = sum(1 for t in trades if t.result.startswith("WIN"))
    stats.losses       = sum(1 for t in trades if t.result == "LOSS_SL")
    stats.timeouts     = sum(1 for t in trades if t.result == "TIMEOUT")
    stats.be_exits     = sum(1 for t in trades if t.result == "BE_EXIT")

    stats.win_rate = (
        stats.wins / stats.total_trades * 100.0
        if stats.total_trades > 0 else 0.0
    )

    stats.gross_pnl  = sum(t.gross_pnl for t in trades)
    stats.net_pnl    = sum(t.net_pnl   for t in trades)
    stats.total_costs = stats.gross_pnl - stats.net_pnl

    gross_profit = sum(t.net_pnl for t in trades if t.net_pnl > 0)
    gross_loss   = abs(sum(t.net_pnl for t in trades if t.net_pnl < 0))
    stats.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    stats.final_balance = balance
    stats.return_pct    = (balance - ACCOUNT_BALANCE) / ACCOUNT_BALANCE * 100.0

    # Sharpe ratio (annualised, using per-trade returns)
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
            # Annualise: ~6 4H candles/day × 252 trading days ≈ 1512 periods/year
            stats.sharpe_ratio = (mean_r / std_r * math.sqrt(1512)) if std_r > 0 else 0.0

    return stats


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def _bar(value: float, max_val: float, width: int = 20, char: str = "█") -> str:
    """Render a simple ASCII progress bar."""
    if max_val <= 0:
        return ""
    filled = int(round(value / max_val * width))
    filled = max(0, min(filled, width))
    return char * filled + "░" * (width - filled)


def print_report(stats: BacktestStats) -> None:
    """Print a comprehensive backtest report for one pair."""
    sep  = "─" * 68
    sep2 = "═" * 68

    print(f"\n{sep2}")
    print(f"  BACKTEST REPORT — {stats.pair}  [{stats.data_source.upper()} DATA]")
    print(f"{sep2}")

    # ── Data summary ─────────────────────────────────────────────────────────
    print(f"\n{'DATA SUMMARY':}")
    print(f"  {'Candles fetched':<30} {stats.candles_fetched:>8}")
    print(f"  {'Candles used (post-warmup)':<30} {stats.candles_used:>8}")
    print(f"  {'ATR mean (price units)':<30} {stats.atr_mean:>8.2f}")
    print(f"  {'ATR min':<30} {stats.atr_min:>8.2f}")
    print(f"  {'ATR max':<30} {stats.atr_max:>8.2f}")

    # ── Trade summary ─────────────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"TRADE SUMMARY")
    print(f"{sep}")
    print(f"  {'Total trades':<30} {stats.total_trades:>8}")
    print(f"  {'Wins (TP1/TP2/TP3)':<30} {stats.wins:>8}  {_bar(stats.wins, stats.total_trades or 1)}")
    print(f"  {'Losses (SL hit)':<30} {stats.losses:>8}  {_bar(stats.losses, stats.total_trades or 1)}")
    print(f"  {'BE exits (SL at entry)':<30} {stats.be_exits:>8}  {_bar(stats.be_exits, stats.total_trades or 1)}")
    print(f"  {'Timeouts':<30} {stats.timeouts:>8}  {_bar(stats.timeouts, stats.total_trades or 1)}")
    print(f"  {'Win rate':<30} {stats.win_rate:>7.1f}%")

    # ── Lifecycle counters ────────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"TRADE MANAGER LIFECYCLE")
    print(f"{sep}")
    print(f"  {'BE activations':<30} {stats.be_activations:>8}")
    print(f"  {'Partial closes (TP1+TP2+TP3)':<30} {stats.partial_closes:>8}")

    # ── P&L ──────────────────────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"P&L  (position-sized at {RISK_PER_TRADE_PCT:.0f}% risk, ${ACCOUNT_BALANCE:,.0f} start)")
    print(f"{sep}")
    print(f"  {'Gross P&L (price units)':<30} {stats.gross_pnl:>+10.2f}")
    print(f"  {'Transaction costs':<30} {stats.total_costs:>+10.2f}")
    print(f"  {'Net P&L (price units)':<30} {stats.net_pnl:>+10.2f}")
    print(f"  {'Starting balance':<30} ${ACCOUNT_BALANCE:>9,.2f}")
    print(f"  {'Final balance':<30} ${stats.final_balance:>9,.2f}")
    pnl_sign = "+" if stats.return_pct >= 0 else ""
    print(f"  {'Return':<30} {pnl_sign}{stats.return_pct:>7.2f}%")

    # ── Risk metrics ──────────────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"RISK METRICS")
    print(f"{sep}")
    print(f"  {'Max drawdown':<30} {stats.max_drawdown_pct:>7.2f}%  (${stats.max_drawdown_usd:,.2f})")
    pf_str = f"{stats.profit_factor:.2f}" if stats.profit_factor != float("inf") else "∞"
    print(f"  {'Profit factor':<30} {pf_str:>8}")
    print(f"  {'Sharpe ratio (annualised)':<30} {stats.sharpe_ratio:>8.2f}")

    # ── Monthly breakdown ─────────────────────────────────────────────────────
    if stats.monthly_pnl:
        print(f"\n{sep}")
        print(f"MONTHLY P&L  (USD, position-sized)")
        print(f"{sep}")
        max_abs = max(abs(v) for v in stats.monthly_pnl.values()) or 1.0
        for month in sorted(stats.monthly_pnl):
            pnl   = stats.monthly_pnl[month]
            sign  = "+" if pnl >= 0 else ""
            bar   = _bar(abs(pnl), max_abs, width=15, char="▓" if pnl >= 0 else "░")
            print(f"  {month}  {sign}{pnl:>+9.2f}  {bar}")

    # ── Recent trades sample ──────────────────────────────────────────────────
    if stats.trades:
        print(f"\n{sep}")
        print(f"LAST 10 TRADES")
        print(f"{sep}")
        print(f"  {'#':<4} {'Dir':<5} {'Entry':>8} {'Exit':>8} {'Result':<12} {'Net P&L':>9}  {'BE':>3} {'TP1':>3}")
        print(f"  {'-'*4} {'-'*5} {'-'*8} {'-'*8} {'-'*12} {'-'*9}  {'-'*3} {'-'*3}")
        for i, t in enumerate(stats.trades[-10:], 1):
            ep   = f"{t.exit_price:.2f}" if t.exit_price else "—"
            be   = "✓" if t.be_activated else " "
            tp1  = "✓" if t.tp1_hit else " "
            sign = "+" if t.net_pnl >= 0 else ""
            print(
                f"  {i:<4} {t.direction:<5} {t.entry_price:>8.2f} {ep:>8} "
                f"{t.result:<12} {sign}{t.net_pnl:>+8.2f}  {be:>3} {tp1:>3}"
            )

    print(f"\n{sep2}\n")


# ---------------------------------------------------------------------------
# MongoDB persistence
# ---------------------------------------------------------------------------

def save_to_mongodb(stats: BacktestStats) -> Optional[str]:
    """
    Persist a completed backtest result to the MongoDB ``backtest_results``
    collection.

    Returns the inserted document ID as a string on success, or None if the
    save was skipped / failed.  All errors are caught and logged so they never
    interrupt the backtest run itself.
    """
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
        col    = db["backtest_results"]

        # Determine date range from the trade list (fall back to "unknown")
        if stats.trades:
            start_dt = min(t.entry_time for t in stats.trades)
            end_dt   = max(
                t.exit_time if t.exit_time else t.entry_time
                for t in stats.trades
            )
            start_date    = start_dt.strftime("%Y-%m-%d")
            end_date      = end_dt.strftime("%Y-%m-%d")
            duration_years = round(
                (end_dt - start_dt).total_seconds() / (365.25 * 86_400), 2
            )
        else:
            start_date = end_date = "unknown"
            duration_years = 0.0

        # Serialise individual trades to plain dicts
        trades_list = []
        for t in stats.trades:
            trades_list.append({
                "pair":           t.pair,
                "direction":      t.direction,
                "entry_price":    t.entry_price,
                "entry_time":     t.entry_time.isoformat(),
                "exit_price":     t.exit_price,
                "exit_time":      t.exit_time.isoformat() if t.exit_time else None,
                "sl_price":       t.sl_price,
                "tp1_price":      t.tp1_price,
                "tp2_price":      t.tp2_price,
                "tp3_price":      t.tp3_price,
                "atr_at_entry":   t.atr_at_entry,
                "result":         t.result,
                "gross_pnl":      round(t.gross_pnl, 6),
                "net_pnl":        round(t.net_pnl, 6),
                "be_activated":   t.be_activated,
                "tp1_hit":        t.tp1_hit,
                "tp2_hit":        t.tp2_hit,
                "tp3_hit":        t.tp3_hit,
                "max_adverse":    round(t.max_adverse, 6),
                "max_favourable": round(t.max_favourable, 6),
            })

        profit_factor = (
            stats.profit_factor
            if stats.profit_factor != float("inf")
            else None          # JSON / BSON cannot store Infinity
        )

        document = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "pair":           stats.pair,
                "data_source":    stats.data_source,
                "start_date":     start_date,
                "end_date":       end_date,
                "duration_years": duration_years,
                "timeframe":      INTERVAL,
                "candles_fetched": stats.candles_fetched,
                "candles_used":   stats.candles_used,
            },
            "summary": {
                "total_trades":    stats.total_trades,
                "wins":            stats.wins,
                "losses":          stats.losses,
                "timeouts":        stats.timeouts,
                "be_exits":        stats.be_exits,
                "win_rate":        round(stats.win_rate, 4),
                "gross_pnl":       round(stats.gross_pnl, 4),
                "net_pnl":         round(stats.net_pnl, 4),
                "total_costs":     round(stats.total_costs, 4),
                "profit_factor":   round(profit_factor, 4) if profit_factor is not None else None,
                "sharpe_ratio":    round(stats.sharpe_ratio, 4),
                "max_drawdown_pct": round(stats.max_drawdown_pct, 4),
                "max_drawdown_usd": round(stats.max_drawdown_usd, 4),
                "final_equity":    round(stats.final_balance, 4),
                "roi_pct":         round(stats.return_pct, 4),
                "be_activations":  stats.be_activations,
                "partial_closes":  stats.partial_closes,
                "atr_mean":        round(stats.atr_mean, 4),
                "atr_min":         round(stats.atr_min, 4),
                "atr_max":         round(stats.atr_max, 4),
                "account_balance": ACCOUNT_BALANCE,
                "risk_per_trade_pct": RISK_PER_TRADE_PCT,
            },
            "monthly_pnl": {
                k: round(v, 4) for k, v in stats.monthly_pnl.items()
            },
            "trades": trades_list,
        }

        result = col.insert_one(document)
        client.close()

        doc_id = str(result.inserted_id)
        print(f"  ✓ Backtest result saved to MongoDB  (id={doc_id})", flush=True)
        return doc_id

    except PyMongoError as exc:
        print(f"  ✗ MongoDB save failed: {exc}", flush=True)
        return None
    except Exception as exc:
        print(f"  ✗ Unexpected error saving to MongoDB: {exc}", flush=True)
        return None


def save_backtest_to_mongodb(stats_list: list[BacktestStats]) -> Optional[str]:
    """
    Save backtest results to MongoDB.

    Args:
        stats_list: List of BacktestStats objects (one per pair)

    Returns:
        Inserted document ID string, or None if save failed
    """
    if not _PYMONGO_AVAILABLE:
        print("⚠️  PyMongo not available, skipping MongoDB save")
        return None

    mongo_url = os.environ.get("MONGO_URL")
    if not mongo_url:
        print("⚠️  MONGO_URL not set, skipping MongoDB save")
        return None

    try:
        client = MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
        db = client["gold_signals_test"]
        collection = db["backtest_results"]

        # Prepare document
        doc = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "pairs": [s.pair for s in stats_list],
                "data_source": stats_list[0].data_source if stats_list else "unknown",
                "num_pairs": len(stats_list),
            },
            "results": [
                {
                    "pair": s.pair,
                    "total_trades": s.total_trades,
                    "win_rate": s.win_rate,
                    "gross_pnl": s.gross_pnl,
                    "net_pnl": s.net_pnl,
                    "profit_factor": s.profit_factor,
                    "sharpe_ratio": s.sharpe_ratio,
                    "max_drawdown_pct": s.max_drawdown_pct,
                    "final_balance": s.final_balance,
                    "return_pct": s.return_pct,
                }
                for s in stats_list
            ]
        }

        result = collection.insert_one(doc)
        doc_id = str(result.inserted_id)
        print(f"✅ Backtest results saved to MongoDB: {doc_id}")
        return doc_id

    except PyMongoError as e:
        print(f"⚠️  MongoDB save failed: {e}")
        return None
    except Exception as e:
        print(f"⚠️  Unexpected error saving to MongoDB: {e}")
        return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    api_key = (
        os.environ.get("TWELVEDATA_API_KEY", "")
        or os.environ.get("TWELVE_DATA_API_KEY", "")
    ).strip()

    print("=" * 68)
    print("  Gold Trading Strategy — Real-Data Backtest (TwelveData)")
    print(f"  Pairs    : {', '.join(PAIRS)}")
    print(f"  Interval : {INTERVAL}  |  Max candles : {MAX_CANDLES}")
    print(f"  ATR period: {ATR_PERIOD}  |  Risk/trade: {RISK_PER_TRADE_PCT}%")
    print(f"  Account  : ${ACCOUNT_BALANCE:,.0f}")
    print("=" * 68)

    if api_key:
        print(f"\n✓ TWELVEDATA_API_KEY detected — fetching real market data\n")
    else:
        print(
            "\n⚠  TWELVEDATA_API_KEY not set — falling back to synthetic data.\n"
            "   Set the TWELVEDATA_API_KEY environment variable for real results.\n"
        )

    all_stats: list[BacktestStats] = []

    for pair, cfg in PAIRS.items():
        print(f"{'─'*68}")
        print(f"Processing {pair} ({cfg['symbol']}) …")

        candles: Optional[list[Candle]] = None
        data_source = "synthetic"

        if api_key:
            raw = fetch_candles_from_twelvedata(
                symbol=cfg["symbol"],
                interval=INTERVAL,
                outputsize=MAX_CANDLES,
                api_key=api_key,
            )
            if raw:
                candles     = parse_candles(raw)
                data_source = "real"
                print(f"  ✓ Parsed {len(candles)} candles  "
                      f"({candles[0].dt.date()} → {candles[-1].dt.date()})")
            else:
                print(f"  ⚠  API fetch failed — falling back to synthetic data")

        if candles is None:
            candles = generate_synthetic_candles(pair, n=MAX_CANDLES)
            print(f"  ✓ Generated {len(candles)} synthetic candles")

        if len(candles) < ATR_PERIOD + MA_SLOW_PERIOD + TIMEOUT_CANDLES + 10:
            print(f"  ✗ Insufficient candles ({len(candles)}) — skipping {pair}")
            continue

        # Small delay between API calls to respect rate limits
        if api_key and pair != list(PAIRS.keys())[-1]:
            time.sleep(1.5)

        stats = run_backtest(pair, candles, data_source)
        all_stats.append(stats)
        print_report(stats)
        save_to_mongodb(stats)

    # Save results to MongoDB
    mongo_id = save_backtest_to_mongodb(all_stats)
    if mongo_id:
        print(f"\n📊 Results stored in MongoDB with ID: {mongo_id}")

    # ── Combined summary ──────────────────────────────────────────────────────
    if len(all_stats) > 1:
        print("═" * 68)
        print("  COMBINED SUMMARY — ALL PAIRS")
        print("═" * 68)
        total_trades = sum(s.total_trades for s in all_stats)
        total_wins   = sum(s.wins         for s in all_stats)
        total_losses = sum(s.losses       for s in all_stats)
        total_be     = sum(s.be_exits     for s in all_stats)
        combined_wr  = total_wins / total_trades * 100.0 if total_trades > 0 else 0.0
        combined_net = sum(s.net_pnl for s in all_stats)
        combined_ret = sum(s.return_pct for s in all_stats) / len(all_stats)
        max_dd       = max(s.max_drawdown_pct for s in all_stats)

        print(f"  {'Total trades':<30} {total_trades:>8}")
        print(f"  {'Total wins':<30} {total_wins:>8}")
        print(f"  {'Total losses':<30} {total_losses:>8}")
        print(f"  {'BE exits':<30} {total_be:>8}")
        print(f"  {'Combined win rate':<30} {combined_wr:>7.1f}%")
        print(f"  {'Combined net P&L (units)':<30} {combined_net:>+10.2f}")
        print(f"  {'Avg return across pairs':<30} {combined_ret:>+7.2f}%")
        print(f"  {'Worst pair drawdown':<30} {max_dd:>7.2f}%")
        print()
        for s in all_stats:
            src_tag = f"[{s.data_source}]"
            print(
                f"  {s.pair:<8} {src_tag:<10}  "
                f"WR={s.win_rate:>5.1f}%  "
                f"PF={s.profit_factor:.2f}  "
                f"Ret={s.return_pct:>+6.2f}%  "
                f"DD={s.max_drawdown_pct:.2f}%"
            )
        print("═" * 68)
        print()


if __name__ == "__main__":
    main()

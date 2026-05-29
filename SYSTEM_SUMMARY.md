# System Summary — Institutional Multi-Strategy Hybrid Portfolio System v3.0

## Executive Summary

The Grandcom Gold Signals system has been upgraded to v3.0, implementing a complete institutional-grade multi-strategy hybrid portfolio system. All 6 core components are confirmed and integrated.

## Component Status: 6/6 Confirmed

### G1: Daily Pivot Points
- **File:** `backend/ml_engine/pivot_points_analyzer.py`
- **Methods:** Standard, Fibonacci, Woodie, Camarilla (4 methods)
- **Levels:** S3, S2, S1, PP, R1, R2, R3 (6 support/resistance levels)
- **Zones:** Deep Support, Support, Near Support, Near Resistance, Resistance, Deep Resistance (6 zones)
- **Output:** Zone classification, nearest levels, directional bias, R:R to nearest levels

### G2: Multi-Timeframe Confirmation
- **File:** `backend/ml_engine/multi_timeframe_confirmation.py`
- **Timeframes:** 1H (15%), 4H (35%), Daily (35%), Weekly (15%)
- **Score:** 0-100% weighted alignment score
- **Analysis:** EMA alignment, RSI, MACD, ADX per timeframe
- **Output:** Dominant direction, alignment score, trade recommendation

### G3: Regime Detection
- **File:** `backend/ml_engine/regime_detector.py`
- **Regimes:** TREND_UP, TREND_DOWN, RANGE, HIGH_VOL, LOW_VOL (5 regimes)
- **Method:** Rule-based + ML (Gradient Boosting) + Hysteresis smoothing
- **Features:** ADX, ATR ratio, RSI, BB position, MA slope, Z-score
- **Output:** Regime name, confidence, active strategies, risk multiplier

### SMC/Institutional Structure
- **File:** `backend/ml_engine/smc_ict_strategy.py`
- **Concepts:** Order Blocks, Breaker Blocks, Fair Value Gaps, Liquidity Sweeps
- **ICT:** OTE zones (61.8-78.6% Fib), Power of 3, Inducement levels
- **Structure:** BOS (Break of Structure), ChoCH (Change of Character)
- **Output:** SMC score (0-10), bias, signal quality

### Correlation/Exposure Engine
- **File:** `backend/ml_engine/correlation_engine.py`
- **Windows:** 20, 60, 120 bar rolling correlations
- **Beta:** OLS regression vs DXY benchmark
- **Clustering:** USD-positive, USD-negative, USD-neutral groups
- **Output:** Correlation matrices, beta, diversification score, exposure

### Multi-Timeframe Consensus (Strategy Router)
- **File:** `backend/ml_engine/strategy_router.py`
- **Routing:** Regime → Strategy mapping
- **Validation:** Pre-flight checks (calendar, portfolio capacity)
- **Composite:** Weighted signal from all components
- **Output:** Final signal, confidence, strategy selection

## Risk Management Stack

| Module | Purpose |
|--------|---------|
| `risk_parity.py` | Equal risk contribution allocation (ERC, Inverse Vol, Max Diversification) |
| `volatility_adjustment.py` | Dynamic sizing via volatility targeting (EWMA, ATR, Parkinson) |
| `drawdown_recovery.py` | Tiered recovery (25%/50%/75%/100%) with circuit breakers |
| `economic_calendar.py` | High-impact event blackout windows (±30 min) |
| `position_calculator.py` | Fixed Risk %, ATR, Kelly Criterion, Vol-Adjusted sizing |
| `portfolio_manager.py` | Open position tracking, correlation limits, daily P&L |

## Analytics Stack

| Module | Purpose |
|--------|---------|
| `performance_attribution.py` | P&L attribution by strategy, regime, symbol, timeframe, time-of-day |
| `trade_journal.py` | Full trade logging with pattern recognition and improvement insights |

## Signal Pipeline

```
Price Data (TwelveData)
    ↓
Feature Engineering
    ↓
G3: Regime Detection ──────────────────────────────────┐
    ↓                                                   │
SMC/ICT Analysis ─────────────────────────────────────┤
    ↓                                                   │
Mean Reversion Analysis ──────────────────────────────┤
    ↓                                                   │
G2: MTF Confirmation (async) ─────────────────────────┤
    ↓                                                   │
G1: Pivot Points ─────────────────────────────────────┤
    ↓                                                   │
Correlation Engine ───────────────────────────────────┤
    ↓                                                   │
Economic Calendar Check ──────────────────────────────┤
    ↓                                                   │
Portfolio State Check ────────────────────────────────┤
    ↓                                                   │
Strategy Router ←─────────────────────────────────────┘
    ↓
Volatility Adjustment × Drawdown Recovery
    ↓
Position Calculator
    ↓
GPT-4o-mini (with full context)
    ↓
Signal Validation
    ↓
MongoDB Storage + Telegram Delivery
```

## Performance Targets

- Signal generation: < 5 seconds per pair
- MTF analysis: < 30 seconds (async with timeout)
- Health check: < 100ms
- Uptime: 99.9% (Railway production)

# System Summary — Grandcom Gold Signals v3.0

## What Was Built

A complete institutional-grade trading signal system for XAUUSD and XAUEUR, deployed on Railway.

## System Statistics

| Metric | Value |
|--------|-------|
| Total ML Modules | 15 |
| API Endpoints | 11 |
| Market Regimes | 5 |
| Trading Strategies | 3 |
| Risk Components | 5 |
| Configuration Variables | 50+ |
| Supported Pairs | XAUUSD, XAUEUR |
| Primary Timeframe | 4H |
| MTF Timeframes | 1H, 4H, Daily, Weekly |

## Signal Generation Pipeline

Every `SIGNAL_INTERVAL_MINUTES` (default: 30), the system runs this pipeline for each pair:

```
1. CALENDAR CHECK
   └─ ForexFactory API → blackout window check
   └─ High impact: ±60 min | Medium impact: ±30 min

2. MULTI-TIMEFRAME ANALYSIS
   └─ 1H + 4H + Daily + Weekly EMA alignment
   └─ Minimum 3/4 timeframes must agree (configurable)

3. FEATURE EXTRACTION (30+ features)
   └─ Volatility: ATR ratio, BB width, realised vol
   └─ Trend: ADX, EMA slopes, structure bias
   └─ Momentum: RSI, MACD, Stochastic
   └─ Mean Reversion: Z-score, BB position, CCI

4. REGIME DETECTION
   └─ Rule-based classification → 5 regimes
   └─ Hysteresis smoothing (3 consecutive predictions)
   └─ Strategy gate per regime

5. STRATEGY ROUTING
   └─ TREND_UP/DOWN → SMC/ICT Strategy
   └─ RANGE/LOW_VOL → Mean Reversion Strategy
   └─ CHAOS → No trading

6. PORTFOLIO APPROVAL
   └─ Max 5 open positions
   └─ Correlation cap: 0.70
   └─ USD cluster exposure limit
   └─ Drawdown recovery check

7. POSITION SIZING
   └─ Base: risk-parity weight × account equity × 1% risk
   └─ × Volatility scale (0.5× to 1.5×)
   └─ × Drawdown scale (0% to 100%)
   └─ Hard limits: 0.01 to 10.0 lots

8. DELIVERY
   └─ MongoDB storage
   └─ Telegram: copier format + analysis card
   └─ Trade journal entry
   └─ Portfolio position registration
```

## Risk Management Layers

| Layer | Component | Trigger |
|-------|-----------|---------|
| 1 | Economic Calendar | High/medium impact news |
| 2 | Regime Gate | CHAOS regime → no trading |
| 3 | Confidence Filter | < 65% confidence → reject |
| 4 | MTF Confluence | < 3/4 timeframes aligned → reject |
| 5 | Correlation Cap | > 0.70 correlation → reject |
| 6 | Drawdown Recovery | > 5% DD → 50% size; > 10% → 25%; > 15% → pause |
| 7 | Daily Loss Limit | > 3% daily loss → pause |
| 8 | Weekly Loss Limit | > 6% weekly loss → pause |
| 9 | Consecutive Losses | ≥ 3 losses → 50% size |
| 10 | Volatility Adjustment | High vol → reduce size; Low vol → increase |

## SMC/ICT Strategy

Active in: TREND_UP, TREND_DOWN, HIGH_VOL regimes

Signal sources (weighted):
- BOS/ChoCH (weight: 2.0) — Break of Structure / Change of Character
- Liquidity Sweep (weight: 2.0) — Buy/sell-side liquidity sweeps
- Order Block proximity (weight: 1.5) — Bullish/bearish OB entries
- FVG fill (weight: 1.0) — Fair Value Gap fills
- Premium/Discount zone (weight: 1.0) — Fibonacci-based zones

Minimum score to generate signal: 3.0

## Mean Reversion Strategy

Active in: RANGE, LOW_VOL regimes

Signal sources (weighted):
- RSI extreme (weight: 2.0) — < 30 oversold, > 70 overbought
- Bollinger Band touch (weight: 2.0) — At upper/lower band
- Z-score extreme (weight: 1.5) — > ±2.0 standard deviations
- Stochastic cross (weight: 1.0) — Oversold/overbought crossover
- CCI extreme (weight: 1.0) — > ±100
- Keltner Channel (weight: 0.5) — Outside channel

Minimum score to generate signal: 4.0

## Correlation Engine

Three analysis layers:
1. **Rolling Correlation** — Pearson correlation over 30-period window
2. **Beta Exposure** — Pair beta relative to XAUUSD benchmark
3. **USD Clustering** — Groups USD-correlated pairs, caps total exposure

Static fallback table for 13 common pair combinations when live data unavailable.

## Performance Attribution

Tracks P&L across 6 dimensions:
- By strategy (SMC/ICT, Mean Reversion)
- By market regime
- By trading pair
- By timeframe
- By session (London, New York, Asia)
- By confidence bucket (< 65%, 65-75%, 75-85%, 85%+)

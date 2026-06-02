# Signal Quality V2 Guide
## Grandcom Gold Signals v3.0.2 — Phase 2 Enhancement

---

## Overview

Phase 2 introduces **13 Hybrid Enhancement Indicators** and a comprehensive **Signal Quality V2** engine that resolves all 12 critical signal quality issues identified in the system audit.

**Key improvements:**
- R:R improved from 1:1.3 → target **1:2.5**
- Dynamic confidence scoring (was static 75%)
- Regime-aware entry rules (RANGE: sell at resistance, not support)
- 10-pip entry bands (was 1-pip)
- SL anchored to swing high/low + ATR buffer
- ATR quantified with position sizing
- Session quality detection (London open 07:00 UTC)
- Signal expiry mechanism (SWING: 24h, SCALP: 4h)
- News filter (JOLTS, Beige Book, NFP, FOMC, CPI)
- Dynamic MTF confidence recalculation

---

## New Files

| File | Description |
|------|-------------|
| `backend/ml_engine/signal_quality_v2.py` | Core quality engine — 12 validation dimensions |
| `backend/ml_engine/hybrid_indicators.py` | 13 hybrid enhancement indicators |
| `backend/ml_engine/session_quality.py` | Session quality detection |
| `backend/ml_engine/volatility_metrics.py` | ATR calculation + position sizing |
| `backend/signal_quality_api_v2.py` | 17 REST API endpoints |

---

## 13 Hybrid Enhancement Indicators

### 1. SMC + Order Flow
Filters false SMC levels using order flow confirmation. A valid order block requires price to return to the zone with confirming order flow bias.

**Score:** 0–10 | **Weight in confluence:** Equal

### 2. RSI + MACD + Stochastic RSI (Triple Momentum)
All three momentum indicators must align for HIGH CONFIDENCE.
- RSI: overbought/oversold/directional bias
- MACD: histogram direction + signal line cross
- Stochastic RSI: extreme readings for entry timing

**Confluence levels:** STRONG_BUY, BUY, NEUTRAL, SELL, STRONG_SELL

### 3. VWAP + Price Action
VWAP as institutional session benchmark. Price above VWAP = institutional buying pressure. Used to confirm entry direction aligns with institutional flow.

### 4. Fibonacci + SMC Confluence
Stacked zones where Fibonacci retracement levels (23.6%, 38.2%, 50%, 61.8%, 78.6%) coincide with Order Blocks or FVGs within 10 pips. Stacked zones have significantly higher probability.

### 5. ATR + Bollinger Bands
- ATR: volatility sizing and SL placement
- BB Squeeze (BB width < 1 ATR): breakout imminent
- BB Expanding: high volatility — reduce size

### 6. Range + Breakout Filter
Regime clarity scoring. Confirms whether market is in RANGE, BREAKOUT, or TREND mode. Prevents counter-regime entries.

### 7. Swing + Scalp Entry Timing
M15 confirmation for H4/H1 bias. Entry window only opens when M15 aligns with the higher timeframe direction.

### 8. Trend + Mean Reversion
Selects primary strategy based on ADX:
- ADX > 25: Trend-following
- ADX < 20 + extreme Z-score: Mean reversion
- ADX 20–25: Breakout

### 9. MTF Pyramid Breakdown
Full H4 → H1 → M15 pyramid validation. All three levels must align for a valid pyramid (score 10/10).

### 10. Session-Based MTF Weighting
Adjusts MTF weights based on session liquidity:
- London/NY peak: M15 weight = 30%
- Asia session: M15 weight = 15% (noise reduction)
- Off-session: M15 weight = 10%

### 11. Fixed + Trailing Stop Hybrid
- Fixed SL until TP1 is reached
- Trail activates at TP1 with 1 ATR distance
- Locks profit while allowing trade to run

### 12. Volatility-Adjusted Position Sizing
1% account risk rule with ATR-based adjustment:
```
size = (balance × 0.01) / (sl_pips × $10/pip)
```
Uses the more conservative of SL-based and ATR-based sizing.

### 13. Dynamic Confluence Score
Aggregates all 12 indicators into a single score (0–100%).
- **> 75% = HIGH CONFIDENCE** ✓
- 55–75% = MEDIUM CONFIDENCE
- < 55% = LOW CONFIDENCE

---

## Quality Scoring Methodology

### Overall Score Components

| Component | Weight | Description |
|-----------|--------|-------------|
| MTF Alignment | 40% | H4/H1/M15 directional agreement |
| SMC Confluence | 20% | Order blocks, FVGs, liquidity |
| Momentum | 15% | RSI + MACD + Stoch RSI |
| Session Quality | 10% | London/NY/Asia/Off |
| News Filter | 10% | High-impact event proximity |
| Regime | 5% | ADX-based regime confidence |

### Recommendation Thresholds

| Score | Recommendation |
|-------|---------------|
| ≥ 75 | **APPROVE** |
| 55–74 | **ADJUST** |
| < 55 | **REJECT** |

### Hard Rejection Triggers (any one = REJECT)
- R:R below minimum (< 1:2 swing, < 1:1.5 scalp)
- Signal expired
- News blackout window active
- CHAOS regime (ADX > 40 + extreme RSI)
- Counter-regime entry (e.g., SELL in TREND_UP)

---

## Risk/Reward Calculation

### Minimum Requirements
- **Swing trades:** 1:2.0 minimum, 1:2.5 target
- **Scalp trades:** 1:1.5 minimum

### Formula
```
R:R = (entry - tp1) / (entry - sl)   [for SELL]
R:R = (tp1 - entry) / (entry - sl)   [for BUY]
```

### Example (SELL XAUUSD)
```
Entry:  2345.00
SL:     2358.00  → Risk = 130 pips
TP1:    2319.00  → Reward = 260 pips
R:R = 260/130 = 2.0:1  ✓ Meets minimum
```

---

## Session Quality

| Session | UTC Hours | Quality | MTF M15 Weight |
|---------|-----------|---------|----------------|
| London Peak | 07:00–09:00 | OPTIMAL | 30% |
| NY Peak | 13:00–15:00 | OPTIMAL | 30% |
| London/NY Overlap | 13:00–16:00 | OPTIMAL | 33% |
| London | 07:00–16:00 | GOOD | 27% |
| NY | 13:00–22:00 | GOOD | 27% |
| Asia | 00:00–08:00 | POOR | 15% |
| Off-session | 22:00–07:00 | AVOID | 10% |

**Recommendation:** Enter gold trades at London open (07:00 UTC) for best results.

---

## Signal Expiry

| Trade Type | Expiry |
|------------|--------|
| SWING | 24 hours from creation |
| SCALP | 4 hours from creation |

Expired signals in PENDING_REVIEW status are automatically rejected by the system.

---

## News Filter

High-impact events that trigger blackout windows (30 min before, 15 min after):

| Event | Impact |
|-------|--------|
| NFP (Non-Farm Payroll) | CRITICAL |
| FOMC Rate Decision | CRITICAL |
| CPI / Core CPI | HIGH |
| JOLTS Job Openings | HIGH |
| Beige Book | HIGH |
| GDP | HIGH |
| Unemployment Rate | HIGH |
| PPI | MEDIUM |
| Retail Sales | MEDIUM |
| ISM Manufacturing/Services | MEDIUM |

**Size reduction:** 50% within 60 minutes of events, 0% during blackout.

---

## API Endpoints

Base URL: `https://your-service.railway.app`

### Full Quality Assessment
```bash
curl -X GET "https://your-service.railway.app/api/signals/quality/{signal_id}" \
  -H "Authorization: Bearer {token}"
```

### Dynamic Confidence
```bash
curl -X GET "https://your-service.railway.app/api/signals/confidence/{signal_id}" \
  -H "Authorization: Bearer {token}"
```

### Regime Classification
```bash
curl -X GET "https://your-service.railway.app/api/signals/regime/{signal_id}" \
  -H "Authorization: Bearer {token}"
```

### Session Quality (no signal_id needed)
```bash
curl -X GET "https://your-service.railway.app/api/signals/session-quality" \
  -H "Authorization: Bearer {token}"
```

### News Impact
```bash
curl -X GET "https://your-service.railway.app/api/signals/news-impact?symbol=XAUUSD" \
  -H "Authorization: Bearer {token}"
```

### MTF Alignment
```bash
curl -X GET "https://your-service.railway.app/api/signals/mtf-alignment/{signal_id}" \
  -H "Authorization: Bearer {token}"
```

### Confluence Score (all 13 indicators)
```bash
curl -X GET "https://your-service.railway.app/api/signals/confluence/{signal_id}" \
  -H "Authorization: Bearer {token}"
```

### Risk/Reward Analysis
```bash
curl -X GET "https://your-service.railway.app/api/signals/risk-reward/{signal_id}" \
  -H "Authorization: Bearer {token}"
```

### Entry Band Validation
```bash
curl -X GET "https://your-service.railway.app/api/signals/entry-band/{signal_id}" \
  -H "Authorization: Bearer {token}"
```

### ATR Quantification
```bash
curl -X GET "https://your-service.railway.app/api/signals/atr/{signal_id}" \
  -H "Authorization: Bearer {token}"
```

### Signal Expiry
```bash
curl -X GET "https://your-service.railway.app/api/signals/expiry/{signal_id}" \
  -H "Authorization: Bearer {token}"
```

### All 13 Hybrid Indicator Scores
```bash
curl -X GET "https://your-service.railway.app/api/signals/hybrid-scores/{signal_id}" \
  -H "Authorization: Bearer {token}"
```

### Volatility-Adjusted Position Sizing
```bash
curl -X GET "https://your-service.railway.app/api/signals/volatility-sizing/{signal_id}?account_balance=10000&risk_pct=0.01" \
  -H "Authorization: Bearer {token}"
```

### Trailing Stop Recommendations
```bash
curl -X GET "https://your-service.railway.app/api/signals/trailing-stop/{signal_id}" \
  -H "Authorization: Bearer {token}"
```

### Economic Calendar
```bash
curl -X GET "https://your-service.railway.app/api/signals/economic-calendar?symbol=XAUUSD" \
  -H "Authorization: Bearer {token}"
```

### Recalculate Confidence (MTF Drop)
```bash
curl -X POST "https://your-service.railway.app/api/signals/recalculate-confidence" \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "signal_id": "abc123",
    "original_confidence": 78.5,
    "original_mtf": {"H4": "SELL", "H1": "SELL", "M15": "SELL"},
    "updated_mtf": {"H4": "SELL", "H1": "SELL", "M15": "BUY"}
  }'
```

### SL Anchor Validation
```bash
curl -X GET "https://your-service.railway.app/api/signals/sl-anchor/{signal_id}" \
  -H "Authorization: Bearer {token}"
```

---

## SignalManager Integration

The `SignalManager` class now includes four Phase 2 methods:

### Score Signal Quality
```python
result = await signal_manager.score_signal_quality(
    requesting_manager={"manager_id": "mgr1", "role": "MANAGER"},
    signal_id="abc123",
)
# Returns full quality assessment + persists to DB
```

### Check Signal Expiry
```python
result = await signal_manager.check_signal_expiry(
    requesting_manager={"manager_id": "mgr1", "role": "MANAGER"},
    signal_id="abc123",
)
# Auto-rejects expired PENDING_REVIEW signals
```

### Recalculate Confidence
```python
result = await signal_manager.recalculate_confidence(
    requesting_manager={"manager_id": "mgr1", "role": "MANAGER"},
    signal_id="abc123",
    updated_mtf={"H4": "SELL", "H1": "SELL", "M15": "BUY"},
)
# Updates dynamic_confidence in DB
```

### Check News Impact
```python
result = await signal_manager.check_news_impact(
    requesting_manager={"manager_id": "mgr1", "role": "MANAGER"},
    signal_id="abc123",
)
# Updates news_safe flag in DB
```

---

## Example Quality Response

```json
{
  "success": true,
  "data": {
    "signal_id": "abc123",
    "symbol": "XAUUSD",
    "signal_type": "SELL",
    "overall_score": 82.5,
    "recommendation": "APPROVE",
    "rejection_reasons": [],
    "adjustment_suggestions": [],
    "risk_reward": {
      "ratio": 2.3,
      "risk_pips": 130.0,
      "reward_pips": 299.0,
      "meets_minimum": true,
      "trade_type": "SWING",
      "recommendation": "GOOD R:R 2.3:1 — meets target of 2.5:1."
    },
    "regime": {
      "regime": "TREND_DOWN",
      "confidence": 0.87,
      "adx": 32.0,
      "trend_strength": "STRONG",
      "entry_rules": ["TREND_DOWN: Only SELL signals on rallies to resistance."],
      "blocked_entries": []
    },
    "confidence": {
      "total_score": 82.5,
      "label": "HIGH",
      "mtf_score": 95.0,
      "smc_score": 75.0,
      "momentum_score": 80.0,
      "session_score": 100.0,
      "news_score": 100.0,
      "regime_score": 87.0
    },
    "session": {
      "session": "LONDON",
      "quality": "OPTIMAL",
      "utc_hour": 8,
      "is_london_open": true,
      "recommendation": "✓ London peak session — optimal liquidity."
    },
    "expiry": {
      "expires_at": "2025-01-15T08:30:00+00:00",
      "hours_valid": 24,
      "is_expired": false,
      "minutes_remaining": 1430.0,
      "trade_type": "SWING"
    },
    "news_filter": {
      "safe_to_trade": true,
      "blocking_events": [],
      "recommendation": "✓ No high-impact news events blocking trade."
    }
  },
  "version": "2.0.0"
}
```

---

## ATR-Based SL Placement

### Formula
```
BUY:  SL = swing_low  - (ATR × 0.325)
SELL: SL = swing_high + (ATR × 0.325)
```

### Example (SELL XAUUSD, ATR = 12.5)
```
Swing High: 2355.00
ATR buffer: 12.5 × 0.325 = 4.06
Structural SL: 2355.00 + 4.06 = 2359.06
```

---

## Volatility-Adjusted Position Sizing

### Formula (1% Risk Rule)
```
risk_usd = account_balance × 0.01
sl_pips  = |entry - sl| / 0.10
size     = risk_usd / (sl_pips × $10/pip)
```

### Example
```
Account: $10,000
Risk:    $100 (1%)
SL:      130 pips
Size:    $100 / (130 × $10) = 0.077 lots → 0.07 lots
```

---

## Changelog

### v2.0.0 (Phase 2)
- Added `SignalQualityV2` with 12 quality dimensions
- Added 13 `HybridIndicators`
- Added `SessionQualityDetector`
- Added `VolatilityMetrics` with ATR calculation
- Added `signal_quality_api_v2.py` with 17 endpoints
- Updated `SignalManager` with quality scoring methods
- Updated `__init__.py` exports
- Added `pytz` to requirements

### v1.0.0 (Phase 1)
- Trade Geometry Rating System (4-component, 1–10 scale)
- Signal Manager approval workflow
- Economic Calendar integration

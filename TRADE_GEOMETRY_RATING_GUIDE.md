# Trade Geometry Rating Guide

**Gold Trading System v3.0.2 — Objective Signal Quality Scoring**

---

## Overview

The Trade Geometry Rating system provides an **objective, 4-component quality score** for every trading signal. Instead of relying solely on ML confidence scores, managers now have a transparent, rule-based rating that explains *why* a signal is good or bad in terms of its price structure.

Every signal receives:
- **4 component scores** (1–10 each)
- **1 overall geometry score** (1–10, weighted average)
- **Automatic recommendation**: `APPROVE`, `ADJUST`, or `REJECT`
- **Improvement hints** for sub-optimal components

---

## The 4 Components

### 1. Entry Price Rating (weight: 25%)

Measures how well-positioned the entry price is relative to key structure levels.

| Score | Meaning |
|---|---|
| 8.5–10 | Entry is very close to support/resistance — excellent timing |
| 7.0–8.4 | Entry is near structure — good timing |
| 5.0–6.9 | Entry is acceptable but not ideal |
| 1.0–4.9 | Entry is far from structure — chasing the move |

**Factors considered:**
- Position of entry within the SL–TP range (entry near SL = good, entry near TP = bad)
- Distance from recent swing high/low (if provided)
- ATR-relative SL distance (penalises entries with oversized SL)

---

### 2. Stop Loss Rating (weight: 25%)

Measures how tight and logical the stop-loss placement is.

| SL Distance | Score |
|---|---|
| < 0.5% of entry | 1–3 (too tight — noise risk) |
| 0.5%–1.5% | 7–10 (ideal range) |
| 1.5%–4.0% | 5–7 (acceptable but wide) |
| > 4.0% | 1–5 (very wide — poor R:R) |

**ATR adjustment:**
- SL at 0.5–1.5× ATR → +1.0 bonus
- SL < 0.3× ATR → −1.5 penalty (dangerously tight)
- SL > 3.0× ATR → −1.0 penalty (too wide)

---

### 3. Risk/Reward Rating (weight: 30%)

Measures the quality of the risk-to-reward ratio. This is the **highest-weighted component** because R:R is the single most important factor in long-term profitability.

| R:R Ratio | Score |
|---|---|
| ≥ 1:5 | 10 (exceptional) |
| 1:3 – 1:5 | 8–10 (excellent) |
| 1:2 – 1:3 | 6–8 (good) |
| 1:1.5 – 1:2 | 4–6 (acceptable) |
| 1:1 – 1:1.5 | 2–4 (marginal) |
| < 1:1 | 1 (unacceptable) |

When multiple TP levels are present, the **blended R:R** (average across all TPs) is also computed and the better of TP1 vs blended is used.

---

### 4. Take Profit Rating (weight: 20%)

Measures how realistic and well-structured the TP levels are.

| TP Count | Base Score |
|---|---|
| 1 TP | 6.0 |
| 2 TPs | 7.5 |
| 3 TPs | 9.0 |
| 4–5 TPs | 8.5 |

**Bonuses/penalties:**
- Evenly spaced TPs → +0.5
- Unevenly spaced TPs → −0.5
- TP1 aligns with recent swing high/low → +0.5
- TP1 extends > 5% beyond recent swing → −1.0 (unrealistic)

---

## Overall Score & Recommendations

```
Overall Score = (Entry × 0.25) + (SL × 0.25) + (R:R × 0.30) + (TP × 0.20)
```

| Score | Recommendation | Action |
|---|---|---|
| ≥ 7.0 | **APPROVE** | Signal is ready for live trading |
| 5.0–6.9 | **ADJUST** | Adjust entry/SL/TP before approving |
| < 5.0 | **REJECT** | Signal geometry is too poor to trade |

---

## API Reference

All endpoints require a valid manager JWT (`Authorization: Bearer <token>`).

### Rate a Single Signal

```
POST /api/manager/geometry/rate
```

**Request body:**
```json
{
  "signal_type": "BUY",
  "entry_price": 1900.0,
  "sl_price": 1882.0,
  "tp_levels": [1930.0, 1950.0, 1975.0],
  "pair": "XAUUSD",
  "market_context": {
    "recent_high": 1960.0,
    "recent_low": 1880.0,
    "atr": 18.0
  }
}
```

**Response:**
```json
{
  "success": true,
  "rating": {
    "overall_score": 8.15,
    "recommendation": "APPROVE",
    "components": {
      "entry": { "score": 8.5, "label": "EXCELLENT", "rationale": "..." },
      "sl":    { "score": 8.2, "label": "EXCELLENT", "sl_distance_pct": 0.947, "sl_distance_atr": 1.0 },
      "rr":    { "score": 8.0, "label": "GOOD",      "rr_tp1": 1.67, "rr_blended": 2.5 },
      "tp":    { "score": 9.0, "label": "EXCELLENT", "tp_count": 3 }
    },
    "weights": { "entry_price": 0.25, "stop_loss": 0.25, "risk_reward": 0.30, "take_profit": 0.20 },
    "improvement_hints": []
  }
}
```

---

### Rate Multiple Signals (Batch)

```
POST /api/manager/geometry/rate-batch
```

**Request body:**
```json
{
  "signals": [
    { "signal_type": "BUY", "entry_price": 1900.0, "sl_price": 1882.0, "tp_levels": [1930.0, 1950.0] },
    { "signal_type": "SELL", "entry_price": 1900.0, "sl_price": 1918.0, "tp_levels": [1870.0] }
  ],
  "market_context": { "atr": 18.0 }
}
```

**Response includes a summary:**
```json
{
  "success": true,
  "count": 2,
  "summary": { "approve": 1, "adjust": 1, "reject": 0, "avg_score": 7.1 },
  "ratings": [ ... ]
}
```

---

### Rate a Stored Signal by ID

```
GET /api/manager/geometry/signal/{signal_id}
```

Looks up the signal from MongoDB by its ObjectId and rates it automatically.

---

### Get Scoring Configuration

```
GET /api/manager/geometry/thresholds
```

Returns the current weights, thresholds, and scale labels. No special role required.

---

## Integration with Signal Approval Workflow

The geometry rating is designed to complement the signal approval workflow:

1. Manager calls `GET /api/manager/signals/pending` to see the review queue
2. For each signal, call `GET /api/manager/geometry/signal/{id}` to get the geometry rating
3. Use the recommendation as a guide:
   - `APPROVE` → call `POST /api/manager/signals/approve`
   - `ADJUST` → review improvement hints, call `POST /api/manager/signals/adjust`, then approve
   - `REJECT` → call `POST /api/manager/signals/reject` with the geometry issue as the reason

---

## Example: Full Review Workflow

```bash
# 1. Get pending signals
curl -H "Authorization: Bearer $TOKEN" \
  https://your-api.railway.app/api/manager/signals/pending

# 2. Rate a specific signal
curl -H "Authorization: Bearer $TOKEN" \
  https://your-api.railway.app/api/manager/geometry/signal/64abc123def456789012345

# 3a. If APPROVE recommendation — approve it
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"signal_id": "64abc123def456789012345", "notes": "Geometry score 8.2/10"}' \
  https://your-api.railway.app/api/manager/signals/approve

# 3b. If ADJUST recommendation — fix the SL and approve
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"signal_id": "64abc123def456789012345", "sl_price": 1885.0, "notes": "Tightened SL per geometry rating"}' \
  https://your-api.railway.app/api/manager/signals/adjust

# 3c. If REJECT recommendation — reject with geometry reason
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"signal_id": "64abc123def456789012345", "reason": "Geometry score 3.8/10 — R:R below 1:1"}' \
  https://your-api.railway.app/api/manager/signals/reject
```

---

## Score Interpretation Quick Reference

| Overall Score | Grade | Action |
|---|---|---|
| 9.0–10.0 | A+ | Excellent trade — approve immediately |
| 8.0–8.9  | A  | Very good trade — approve |
| 7.0–7.9  | B  | Good trade — approve |
| 6.0–6.9  | C+ | Acceptable — consider adjusting |
| 5.0–5.9  | C  | Marginal — adjust before approving |
| 4.0–4.9  | D  | Poor — likely reject |
| 1.0–3.9  | F  | Reject — geometry is fundamentally flawed |

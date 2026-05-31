# Trade Geometry Rating System

**Gold Trading System v3.0.2 — Objective Signal Quality Assessment**

---

## Overview

The Trade Geometry Rating System provides managers with an objective, quantifiable way to assess signal quality before making approval decisions. Every signal is automatically scored across four geometry components on a **1–10 scale**, producing an overall score and a clear recommendation: **APPROVE**, **ADJUST**, or **REJECT**.

This eliminates subjective approval decisions and creates a consistent, auditable quality standard across the entire management team.

---

## Architecture

```
Signal Generated (PENDING_REVIEW)
          │
          ▼
  GeometryRating Engine
  ┌─────────────────────────────────────────┐
  │  rate_entry_price()   → score 1–10      │
  │  rate_stop_loss()     → score 1–10      │
  │  rate_risk_reward()   → score 1–10      │
  │  rate_take_profits()  → score 1–10      │
  │                                         │
  │  calculate_overall_score() → avg 1–10   │
  │  get_recommendation()  → APPROVE/ADJUST/REJECT │
  └─────────────────────────────────────────┘
          │
          ▼
  geometry_rating field attached to signal response
          │
          ▼
  Manager reviews score + recommendation
          │
          ▼
  APPROVE / ADJUST / REJECT
```

**File locations:**
- Engine: `backend/ml_engine/geometry_rating.py`
- API integration: `backend/signal_management_api.py`
- Exported from: `backend/ml_engine/__init__.py`

---

## The Four Rating Components

### 1. Entry Price Rating (`rate_entry_price`)

Measures how well the entry price is placed relative to market structure.

| Score | Label      | Criteria |
|-------|------------|----------|
| 10    | EXCELLENT  | Entry is exactly at a key structural level (support for BUY, resistance for SELL) within ¼ ATR tolerance |
| 8     | GOOD       | Entry is within ½ ATR of the structural level |
| 6     | ACCEPTABLE | Entry is within 1 ATR of the structural level |
| 4     | POOR       | Entry is 1–2 ATR from the structural level |
| 2     | POOR       | Entry is more than 2 ATR from any structural level (chasing price) |
| 1     | CRITICAL   | Entry is on the wrong side of structure |

**When ATR/structure context is unavailable**, the engine falls back to a ratio-based score using the TP1 R:R implied by the entry placement:

| TP1 R:R implied by entry | Score |
|--------------------------|-------|
| ≥ 3.0:1                  | 9.0   |
| ≥ 2.0:1                  | 7.5   |
| ≥ 1.5:1                  | 6.0   |
| < 1.5:1                  | 3.0   |

---

### 2. Stop Loss Rating (`rate_stop_loss`)

Measures how well the stop loss is placed relative to structure and volatility.

| Score | Label      | Criteria |
|-------|------------|----------|
| 10    | EXCELLENT  | SL is just beyond a structural level AND within 1.0× ATR |
| 9     | EXCELLENT  | SL is within 1.0× ATR of entry |
| 8     | GOOD       | SL is within 1.5× ATR (ideal range) |
| 6–7   | ACCEPTABLE | SL is within 2.0× ATR |
| 4–5   | POOR       | SL is within 2.5–3.0× ATR (wide, compresses R:R) |
| 2–3   | POOR       | SL is > 3× ATR (dangerously wide) |
| 1     | CRITICAL   | SL is on the wrong side of entry (invalid geometry) |

**Structural alignment bonus (+1.0 point):**
- BUY: SL is below the support level ✓
- SELL: SL is above the resistance level ✓

**Directional validation (automatic CRITICAL):**
- BUY with SL ≥ entry → score = 1 (invalid)
- SELL with SL ≤ entry → score = 1 (invalid)

---

### 3. Risk/Reward Rating (`rate_risk_reward`)

Measures the quality of the R:R ratio, scored primarily on TP1.

| TP1 R:R | Score | Label      |
|---------|-------|------------|
| ≥ 4.0:1 | 10    | EXCELLENT  |
| ≥ 3.5:1 | 9     | EXCELLENT  |
| ≥ 3.0:1 | 8     | GOOD       |
| ≥ 2.5:1 | 7     | GOOD       |
| ≥ 2.0:1 | 6     | ACCEPTABLE |
| ≥ 1.75:1| 5     | ACCEPTABLE |
| ≥ 1.5:1 | 4     | POOR       |
| ≥ 1.25:1| 3     | POOR       |
| ≥ 1.0:1 | 2     | POOR       |
| < 1.0:1 | 1     | CRITICAL   |

**Multi-TP bonus (+0.5 points):** Applied when 3+ TP levels all achieve ≥ 2.0:1 R:R.

> **Minimum recommended R:R:** 2.0:1 on TP1 (score ≥ 6). Signals below this threshold should be adjusted before approval.

---

### 4. Take Profit Rating (`rate_take_profits`)

Measures how well TP levels align with structural targets and how logically they are spaced.

**Baseline score: 5.0**

| Bonus | Condition |
|-------|-----------|
| +2.0  | TP1 aligns with or exceeds the structural target (resistance for BUY, support for SELL) |
| +1.0  | TP1 is within 1 ATR of the structural target |
| +2.0  | TP levels are well-spaced (min gap ≥ 1× risk) |
| +1.0  | TP levels are adequately spaced (min gap ≥ 0.5× risk) |
| +1.0  | 3 or more TP levels defined |
| +0.5  | 2 TP levels defined |

**Automatic CRITICAL (score = 1):**
- Any TP is on the wrong side of entry (e.g. TP below entry for a BUY)

**Ordering validation:**
- BUY: TPs must be ascending (TP1 < TP2 < TP3)
- SELL: TPs must be descending (TP1 > TP2 > TP3)

---

## Overall Score Calculation

```
overall_score = (entry_score + sl_score + rr_score + tp_score) / 4
```

The overall score is the **unweighted average** of all four component scores, rounded to one decimal place.

---

## Recommendation Logic

| Condition | Recommendation |
|-----------|----------------|
| overall_score ≥ 7.0 AND no critical issues | **APPROVE** |
| overall_score ≥ 5.0 OR exactly 1 critical issue | **ADJUST** |
| overall_score < 5.0 OR 2+ critical issues | **REJECT** |

A **critical issue** is triggered when any single component scores below 3.0.

---

## Score Labels

| Score Range | Label      | Meaning |
|-------------|------------|---------|
| 9.0 – 10.0  | EXCELLENT  | Institutional-grade geometry |
| 7.0 – 8.9   | GOOD       | Solid setup, ready for approval |
| 5.0 – 6.9   | ACCEPTABLE | Marginal — consider adjustments |
| 3.0 – 4.9   | POOR       | Significant issues — adjust before approval |
| 1.0 – 2.9   | CRITICAL   | Fundamental geometry failure — reject |

---

## API Response Structure

The `geometry_rating` field is automatically attached to every signal returned by:

- `GET /api/manager/signals/pending` — each signal in the `signals` array
- `GET /api/manager/signals/{id}` — the `signal` object
- `GET /api/manager/signals/history/all` — each signal in the `history` array

### Example Response

```json
{
  "id": "64f1a2b3c4d5e6f7a8b9c0d1",
  "pair": "XAUUSD",
  "type": "BUY",
  "entry_price": 2345.50,
  "sl_price": 2330.00,
  "tp_levels": [2365.00, 2385.00, 2410.00],
  "confidence": 78.5,
  "status": "PENDING_REVIEW",
  "geometry_rating": {
    "signal_type": "BUY",
    "entry_price": 2345.50,
    "sl_price": 2330.00,
    "tp_levels": [2365.00, 2385.00, 2410.00],
    "ratings": {
      "entry": {
        "score": 8.0,
        "label": "GOOD",
        "explanation": "Entry is within ½ ATR of the support level at 2332.00 — good structural proximity.",
        "adjustments": [],
        "details": {
          "direction": "BUY",
          "entry_price": 2345.50,
          "structural_level": 2332.00,
          "distance_from_structure": 13.50,
          "atr": 12.50
        }
      },
      "stop_loss": {
        "score": 9.0,
        "label": "EXCELLENT",
        "explanation": "SL is 1.2× ATR from entry — within the ideal range. Good stop placement. SL is below support — correct.",
        "adjustments": [],
        "details": {
          "direction": "BUY",
          "entry_price": 2345.50,
          "sl_price": 2330.00,
          "risk_distance": 15.50,
          "atr": 12.50,
          "atr_multiple": 1.24,
          "structural_alignment": "SL is below support — correct"
        }
      },
      "risk_reward": {
        "score": 7.0,
        "label": "GOOD",
        "explanation": "Good R:R of 2.5:1 on TP1. Acceptable reward profile for live trading.",
        "adjustments": [],
        "details": {
          "direction": "BUY",
          "risk": 15.50,
          "rr_per_tp": [2.55, 3.84, 5.45],
          "tp1_rr": 2.55,
          "avg_rr": 3.95
        }
      },
      "take_profits": {
        "score": 9.0,
        "label": "EXCELLENT",
        "explanation": "3 TP levels defined. TP1 aligns with the resistance level — excellent structural targeting. TP levels are well-spaced relative to risk.",
        "adjustments": [],
        "details": {
          "direction": "BUY",
          "tp_levels": [2365.00, 2385.00, 2410.00],
          "structural_target": 2368.00,
          "tp1_structural_alignment": "TP1 at or beyond resistance — excellent",
          "tp_gaps": [20.0, 25.0],
          "tp_spacing": "well_spaced",
          "multiple_tp_bonus": true
        }
      }
    },
    "overall_score": 8.3,
    "overall_label": "GOOD",
    "recommendation": "APPROVE",
    "critical_issues": [],
    "summary": "Overall Geometry Score: 8.3/10 (GOOD) — Recommendation: APPROVE\n\n  Entry  : 8.0/10 (GOOD) — ...\n  SL     : 9.0/10 (EXCELLENT) — ...\n  R:R    : 7.0/10 (GOOD) — TP1 R:R 2.5:1\n  TPs    : 9.0/10 (EXCELLENT) — ...",
    "rated_at": "2024-01-15T10:30:00.000000"
  }
}
```

---

## Practical Examples

### Example 1 — High-Quality BUY Signal (Score: 8.5 → APPROVE)

**Signal:**
- Pair: XAUUSD BUY
- Entry: 2345.50 (at support zone 2344.00)
- SL: 2330.00 (below support, 1.2× ATR)
- TP1: 2365.00 (at resistance), TP2: 2385.00, TP3: 2410.00
- ATR: 12.50

**Ratings:**
| Component | Score | Reason |
|-----------|-------|--------|
| Entry     | 9.0   | Entry within ¼ ATR of support at 2344.00 |
| Stop Loss | 9.0   | SL below support, 1.2× ATR — ideal |
| R:R       | 7.0   | TP1 R:R = 2.5:1 — good |
| TPs       | 9.0   | TP1 at resistance, 3 well-spaced levels |
| **Overall** | **8.5** | **APPROVE** |

**Manager action:** Approve immediately. Geometry is institutional-grade.

---

### Example 2 — Marginal SELL Signal (Score: 5.5 → ADJUST)

**Signal:**
- Pair: XAUUSD SELL
- Entry: 2380.00 (2 ATR above resistance at 2355.00)
- SL: 2410.00 (3.0× ATR — very wide)
- TP1: 2360.00 (R:R = 0.67:1 — below 1:1)
- ATR: 10.00

**Ratings:**
| Component | Score | Reason |
|-----------|-------|--------|
| Entry     | 2.0   | Entry is 2.5 ATR above resistance — chasing price |
| Stop Loss | 4.0   | SL is 3.0× ATR — wide, compresses R:R |
| R:R       | 1.0   | TP1 R:R = 0.67:1 — reward less than risk |
| TPs       | 6.0   | TP1 near support, single TP level |
| **Overall** | **3.3** | **REJECT** |

**Manager action:** Reject. Entry is chasing price, R:R is below 1:1. Wait for price to pull back to the resistance zone before re-entering.

**Adjustment guidelines:**
1. Wait for price to pull back to resistance (2355.00)
2. Re-enter SELL at 2355.00–2358.00
3. Move SL to 2368.00 (just above resistance, ~1.3× ATR)
4. Set TP1 at 2335.00 (R:R = 2.0:1), TP2 at 2320.00, TP3 at 2300.00

---

### Example 3 — Acceptable BUY Signal (Score: 6.2 → ADJUST)

**Signal:**
- Pair: XAUUSD BUY
- Entry: 2350.00 (1.5 ATR above support at 2332.00)
- SL: 2325.00 (2.0× ATR — slightly wide)
- TP1: 2368.00 (R:R = 0.72:1 — too close)
- TP2: 2390.00 (R:R = 1.6:1)
- ATR: 12.50

**Ratings:**
| Component | Score | Reason |
|-----------|-------|--------|
| Entry     | 4.0   | Entry is 1.5 ATR from support — poor structural placement |
| Stop Loss | 6.0   | SL is 2.0× ATR — acceptable but slightly wide |
| R:R       | 4.0   | TP1 R:R = 0.72:1 — below 1:1 |
| TPs       | 7.0   | TP2 has good R:R, but TP1 is too close |
| **Overall** | **5.3** | **ADJUST** |

**Manager action:** Adjust before approving.

**Adjustment guidelines:**
1. Move TP1 from 2368.00 to 2382.00 (R:R = 1.28:1 → 2.56:1)
2. Consider tightening SL from 2325.00 to 2330.00 (below support at 2332.00)
3. After adjustment, re-rate: expected score ~7.5 → APPROVE

---

## Approval Decision Matrix

| Overall Score | Critical Issues | Recommendation | Manager Action |
|---------------|-----------------|----------------|----------------|
| 8.0 – 10.0    | None            | **APPROVE**    | Approve immediately |
| 7.0 – 7.9     | None            | **APPROVE**    | Approve with optional notes |
| 6.0 – 6.9     | None            | **ADJUST**     | Minor adjustments, then approve |
| 5.0 – 5.9     | 0–1             | **ADJUST**     | Significant adjustments required |
| 4.0 – 4.9     | 0–1             | **ADJUST**     | Major restructuring needed |
| 3.0 – 3.9     | 1+              | **REJECT**     | Reject — fundamental issues |
| 1.0 – 2.9     | 2+              | **REJECT**     | Reject immediately |

---

## Manager Adjustment Guidelines

### Entry Price Adjustments

**Problem:** Entry is too far from structure (score < 6)
- **BUY:** Wait for price to pull back to the support zone before approving. Do not approve entries that are chasing price more than 1 ATR above support.
- **SELL:** Wait for price to rally back to the resistance zone. Do not approve entries more than 1 ATR below resistance.

**Adjustment formula:**
```
Ideal BUY entry  = support_level + (0.1 × ATR)   [just above support]
Ideal SELL entry = resistance_level - (0.1 × ATR) [just below resistance]
```

---

### Stop Loss Adjustments

**Problem:** SL is too wide (score < 6, ATR multiple > 2.0)
- Tighten SL to just beyond the structural level:
  ```
  BUY SL  = support_level - (0.2 × ATR)    [just below support]
  SELL SL = resistance_level + (0.2 × ATR) [just above resistance]
  ```
- Maximum acceptable SL: 3.0× ATR from entry

**Problem:** SL is not beyond structure
- BUY: SL must be below the nearest support level
- SELL: SL must be above the nearest resistance level

---

### Risk/Reward Adjustments

**Problem:** TP1 R:R < 2.0:1 (score < 6)

Option A — Extend TP1:
```
Minimum TP1 (BUY)  = entry_price + (2.0 × risk)
Minimum TP1 (SELL) = entry_price - (2.0 × risk)
```

Option B — Tighten SL:
```
Maximum SL distance = (TP1 - entry) / 2.0   [for 2:1 R:R]
```

Option C — Both (preferred when entry is at structure):
- Tighten SL to structural level
- Extend TP1 to next structural resistance/support

---

### Take Profit Adjustments

**Problem:** TP1 does not align with structure (score < 7)
- Move TP1 to the nearest resistance (BUY) or support (SELL) level
- If no structural level is available, use: `entry + (2.5 × risk)` as a minimum

**Problem:** TPs are too closely spaced (min gap < 0.5× risk)
- Space TPs at least 1× risk apart:
  ```
  TP1 = entry + (2.0 × risk)
  TP2 = entry + (3.5 × risk)
  TP3 = entry + (5.0 × risk)
  ```

**Problem:** Only 1 TP level defined
- Add at least 2 more TP levels to improve the reward profile
- Use the 2R / 3.5R / 5R framework above

---

## Component Weighting Philosophy

All four components are weighted equally (25% each) in the overall score. This reflects the principle that a trade with excellent R:R but a poorly placed stop loss is just as dangerous as a trade with a well-placed stop but poor R:R. Every component must be sound for the trade to be approved.

| Component | Weight | Why It Matters |
|-----------|--------|----------------|
| Entry Price | 25% | Determines the quality of the trade's starting point |
| Stop Loss | 25% | Defines the maximum risk and structural protection |
| Risk/Reward | 25% | Determines the statistical edge of the trade |
| Take Profits | 25% | Determines how efficiently the reward is captured |

---

## Quick Reference Checklist

Before approving a signal, verify:

**Entry (target: ≥ 7/10)**
- [ ] Entry is at or near a key structural level
- [ ] Entry is not chasing price (within 1 ATR of structure)
- [ ] Entry is confirmed by price action (not a random level)

**Stop Loss (target: ≥ 7/10)**
- [ ] SL is on the correct side of entry (below for BUY, above for SELL)
- [ ] SL is beyond a structural level (below support for BUY, above resistance for SELL)
- [ ] SL distance is ≤ 2.0× ATR (ideally 1.0–1.5× ATR)

**Risk/Reward (target: ≥ 6/10)**
- [ ] TP1 R:R is ≥ 2.0:1 (minimum acceptable)
- [ ] TP1 R:R is ≥ 2.5:1 (preferred)
- [ ] Average R:R across all TPs is ≥ 2.5:1

**Take Profits (target: ≥ 7/10)**
- [ ] TP1 aligns with the nearest structural target
- [ ] TPs are on the correct side of entry
- [ ] TPs are in the correct order (ascending for BUY, descending for SELL)
- [ ] At least 2 TP levels defined
- [ ] TPs are spaced at least 1× risk apart

**Overall**
- [ ] Overall score ≥ 7.0
- [ ] No critical issues (no component below 3.0)
- [ ] Recommendation is APPROVE

---

## Implementation Guide

### Direct Usage in Python

```python
from ml_engine.geometry_rating import GeometryRating, geometry_rater

# Using the module-level singleton
result = geometry_rater.rate_signal(
    signal_type="BUY",
    entry_price=2345.50,
    sl_price=2330.00,
    tp_levels=[2365.00, 2385.00, 2410.00],
    # Optional context (improves accuracy)
    current_price=2346.00,
    atr=12.50,
    support_level=2332.00,
    resistance_level=2368.00,
)

print(result["overall_score"])      # 8.3
print(result["recommendation"])     # "APPROVE"
print(result["ratings"]["entry"]["score"])   # 8.0
print(result["ratings"]["stop_loss"]["score"])  # 9.0
print(result["ratings"]["risk_reward"]["score"]) # 7.0
print(result["ratings"]["take_profits"]["score"]) # 9.0
print(result["summary"])            # Full text summary
```

### Rating Individual Components

```python
from ml_engine.geometry_rating import GeometryRating

rater = GeometryRating()

# Rate just the R:R
rr = rater.rate_risk_reward(
    signal_type="BUY",
    entry_price=2345.50,
    sl_price=2330.00,
    tp_levels=[2365.00, 2385.00, 2410.00],
)
print(rr.score)        # 7.0
print(rr.label)        # "GOOD"
print(rr.explanation)  # "Good R:R of 2.5:1 on TP1..."
print(rr.adjustments)  # [] (no adjustments needed)

# Rate just the SL
sl = rater.rate_stop_loss(
    signal_type="BUY",
    entry_price=2345.50,
    sl_price=2330.00,
    atr=12.50,
    support_level=2332.00,
)
print(sl.score)   # 9.0
print(sl.label)   # "EXCELLENT"

# Get recommendation from a known score
rec = rater.get_recommendation(overall_score=7.5, critical_issues=[])
print(rec)  # "APPROVE"

rec = rater.get_recommendation(overall_score=4.5, critical_issues=["SL is critically poor"])
print(rec)  # "ADJUST"
```

### Accessing the API Response

```bash
# Get pending signals with geometry ratings
curl -X GET "https://your-api.railway.app/api/manager/signals/pending" \
  -H "Authorization: Bearer <manager_token>"

# Response includes geometry_rating on each signal:
# {
#   "success": true,
#   "signals": [
#     {
#       "id": "...",
#       "pair": "XAUUSD",
#       "type": "BUY",
#       "entry_price": 2345.50,
#       ...
#       "geometry_rating": {
#         "overall_score": 8.3,
#         "recommendation": "APPROVE",
#         "ratings": { ... }
#       }
#     }
#   ]
# }

# Get full signal details with geometry rating
curl -X GET "https://your-api.railway.app/api/manager/signals/<signal_id>" \
  -H "Authorization: Bearer <manager_token>"
```

---

## Performance Tracking

The geometry rating system enables systematic quality tracking over time. Use the approval history endpoint to analyse geometry scores:

```bash
# Get history with geometry ratings
curl -X GET "https://your-api.railway.app/api/manager/signals/history/all?hours=720" \
  -H "Authorization: Bearer <manager_token>"
```

**Metrics to track:**
- Average overall score per manager (identifies review quality)
- Average score per trading pair (identifies which pairs generate better geometry)
- Score distribution by component (identifies systematic weaknesses)
- Correlation between geometry score and trade outcome (validates the rating system)

**Target benchmarks:**
| Metric | Target |
|--------|--------|
| Average overall score of approved signals | ≥ 7.5 |
| % of approved signals with score ≥ 7.0 | ≥ 90% |
| Average TP1 R:R of approved signals | ≥ 2.5:1 |
| % of signals with critical SL issues | < 5% |

---

## Frequently Asked Questions

**Q: What if the signal doesn't have ATR or structural levels?**
A: The engine automatically falls back to ratio-based heuristics using the price levels alone. Scores are still meaningful but less precise. For best results, ensure signals include `atr`, `support_level`, and `resistance_level` fields.

**Q: Can I override the recommendation?**
A: Yes. The recommendation is advisory. Managers can approve, adjust, or reject any signal regardless of the geometry score. The score is a tool to inform decisions, not replace them.

**Q: What does a `null` geometry_rating mean?**
A: The signal is missing required fields (`type`, `entry_price`, `sl_price`, or `tp_levels`). Check the signal document for completeness.

**Q: Should I always reject signals with score < 5.0?**
A: The recommendation is REJECT for scores below 5.0, but context matters. A signal with a score of 4.8 due to a slightly wide SL may still be worth adjusting. Use the component breakdown and adjustment guidelines to make the final call.

**Q: How is the overall score calculated?**
A: It is the simple unweighted average of all four component scores: `(entry + sl + rr + tp) / 4`. All components are equally important.

---

*Gold Trading System v3.0.2 — Trade Geometry Rating System*
*`backend/ml_engine/geometry_rating.py` | `backend/signal_management_api.py`*

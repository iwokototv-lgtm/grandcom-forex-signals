# Trade Geometry Rating System

**Gold Trading System v3.0.2 — Objective Signal Quality Assessment**

---

## Overview

The Trade Geometry Rating system gives every signal an objective, quantifiable quality score before it reaches the manager approval queue. Instead of relying on intuition, managers now have a structured 1–10 score for each of the four critical geometry components, a weighted overall score, and a clear approval recommendation.

**Core principle:** Good trade geometry means the entry, stop loss, risk/reward ratio, and take-profit targets all align with market structure. A signal with excellent geometry has a high probability of reaching its targets before hitting its stop.

---

## Rating Components

Every signal is scored across four components. Each component is rated independently on a **1–10 scale**.

| Component | Weight | What It Measures |
|---|---|---|
| **Risk/Reward** | 30 % | Quality of the R:R ratio across all TP levels |
| **Stop Loss** | 30 % | Placement beyond the structural invalidation level |
| **Entry Price** | 25 % | Placement relative to key support/resistance |
| **Take Profits** | 15 % | Alignment with market structure targets |

**Overall Score** = weighted average of all four components.

---

## Decision Thresholds

| Overall Score | Recommendation | Action |
|---|---|---|
| **≥ 8.0** | ✅ APPROVE | Excellent geometry — send to trading immediately |
| **7.0 – 7.9** | ✅ APPROVE | Good geometry — meets minimum quality bar |
| **5.5 – 6.9** | ⚠️ ADJUST | Acceptable structure but needs improvement |
| **< 5.5** | ❌ REJECT | Poor geometry — do not trade |

The minimum approval threshold is **7.0/10**. Signals below this score should be adjusted or rejected.

---

## Component 1: Entry Price Rating (25% weight)

### What Is Being Measured

How precisely the entry price is placed relative to the nearest key structure level — support for BUY signals, resistance for SELL signals. The closer the entry is to the key level (without being on the wrong side of it), the higher the score.

### Scoring Table

Distance is measured as a multiple of the current ATR (Average True Range).

| Distance from Key Level | Score | Label |
|---|---|---|
| ≤ 0.10× ATR | 10 | EXCELLENT |
| ≤ 0.25× ATR | 9 | EXCELLENT |
| ≤ 0.40× ATR | 8 | GOOD |
| ≤ 0.55× ATR | 7 | GOOD |
| ≤ 0.70× ATR | 6 | FAIR |
| ≤ 0.85× ATR | 5 | FAIR |
| ≤ 1.00× ATR | 4 | POOR |
| ≤ 1.25× ATR | 3 | POOR |
| ≤ 1.50× ATR | 2 | VERY_POOR |
| > 1.50× ATR | 1 | VERY_POOR |

### Bonuses and Penalties

| Condition | Adjustment |
|---|---|
| Entry is on the wrong side of the key level | −1.0 |
| Entry is at a confluence zone (support AND resistance within 0.5× ATR) | +0.5 |
| No structure level provided | −0.5 (scored on SL distance proxy) |

### BUY Signal Entry Rules

- Entry should be **above** the support level
- Ideal: entry within 0.25× ATR above support
- Wrong side: entry below support (−1.0 penalty)

### SELL Signal Entry Rules

- Entry should be **below** the resistance level
- Ideal: entry within 0.25× ATR below resistance
- Wrong side: entry above resistance (−1.0 penalty)

### Adjustment Guidelines

| Score | Guideline |
|---|---|
| 1–3 | Entry is too far from the key level. Move entry to within 0.40× ATR of the structure level. |
| 4–5 | Entry placement is marginal. Aim for within 0.40× ATR of the key level. |
| 6–7 | Entry is acceptable. Minor improvement possible by moving closer to the key level. |
| 8–10 | Entry placement is excellent. No adjustment needed. |

---

## Component 2: Stop Loss Rating (30% weight)

### What Is Being Measured

How well the stop loss is placed beyond the structural invalidation point. The SL must be far enough beyond the key level to avoid being triggered by normal price noise, but close enough to keep risk tight.

### Scoring Table

Buffer is the distance between the SL and the key invalidation level, expressed as a fraction of ATR.

| Buffer Beyond Key Level | Score | Label |
|---|---|---|
| 5–20% of ATR | 10 | EXCELLENT |
| 20–35% of ATR | 9 | EXCELLENT |
| 35–50% of ATR | 8 | GOOD |
| 50–70% of ATR | 7 | GOOD |
| 70–90% of ATR | 6 | FAIR |
| 90–110% of ATR | 5 | FAIR |
| 110–140% of ATR | 4 | POOR |
| 140–180% of ATR | 3 | POOR |
| 180–220% of ATR | 2 | VERY_POOR |
| < 5% or > 220% of ATR | 1 | VERY_POOR |

### Penalties

| Condition | Adjustment |
|---|---|
| SL is on the wrong side of the key level (inside structure) | −2.0 |
| SL distance from entry is < 0.5× ATR (too tight) | −1.0 |
| SL distance from entry is > 3.0× ATR (too wide) | −0.5 |

### BUY Signal SL Rules

- SL should be **below** the support level
- Ideal buffer: 5–35% of ATR below support
- Wrong side: SL above support (−2.0 penalty — will be hit by normal retracements)

### SELL Signal SL Rules

- SL should be **above** the resistance level
- Ideal buffer: 5–35% of ATR above resistance
- Wrong side: SL below resistance (−2.0 penalty — will be hit by normal retracements)

### Adjustment Guidelines

| Score | Guideline |
|---|---|
| 1–2 | SL is either inside structure or far too wide. Reposition to 10–35% of ATR beyond the key level. |
| 3–4 | SL buffer is too wide. Tighten to 10–50% of ATR beyond the key level. |
| 5–6 | SL is acceptable but could be tighter. Target 10–35% of ATR buffer. |
| 7–8 | SL placement is good. Minor tightening may improve the score. |
| 9–10 | SL placement is excellent. No adjustment needed. |

---

## Component 3: Risk/Reward Rating (30% weight)

### What Is Being Measured

The quality of the risk/reward ratio across all take-profit levels. The primary score is based on TP1 (the first take-profit level). Additional TP levels contribute a weighted bonus.

### TP1 Scoring Table

| TP1 R:R Ratio | Score | Label |
|---|---|---|
| ≥ 4.0 | 10 | EXCELLENT |
| ≥ 3.5 | 9 | EXCELLENT |
| ≥ 3.0 | 8 | GOOD |
| ≥ 2.5 | 7 | GOOD |
| ≥ 2.0 | 6 | FAIR |
| ≥ 1.5 | 5 | FAIR |
| ≥ 1.2 | 4 | POOR |
| ≥ 1.0 | 3 | POOR |
| ≥ 0.7 | 2 | VERY_POOR |
| < 0.7 | 1 | VERY_POOR |

### Multi-TP Bonuses

| Condition | Bonus |
|---|---|
| Each additional TP level with R:R ≥ 2.0 | +0.3 (max +0.9) |
| Average R:R across all TPs ≥ 3.0 | +0.5 |

### Minimum Requirements

- **TP1 R:R ≥ 1.5** is the minimum acceptable ratio
- **TP1 R:R ≥ 2.0** is the recommended minimum for approval
- **TP1 R:R ≥ 3.0** is excellent

### Adjustment Guidelines

| Score | Guideline |
|---|---|
| 1–2 | R:R is below 1:1. This trade risks more than it gains. Move TP1 further or tighten the SL. |
| 3–4 | R:R is below 1.5:1. Extend TP1 to achieve at least 1.5:1. |
| 5–6 | R:R is acceptable. Consider extending TP1 to 2.0:1 if structure allows. |
| 7–8 | R:R is good. Adding TP2/TP3 at higher levels will improve the score further. |
| 9–10 | R:R profile is excellent. No adjustment needed. |

---

## Component 4: Take Profits Rating (15% weight)

### What Is Being Measured

How well each take-profit level aligns with a known market structure level (resistance for BUY, support for SELL). TPs placed at or near key structure levels are more likely to be reached and respected by the market.

### Per-TP Alignment Scoring

Alignment distance is measured as a fraction of ATR from the nearest structure level.

| Distance from Nearest Structure Level | Score | Label |
|---|---|---|
| ≤ 0.10× ATR | 10 | EXCELLENT |
| ≤ 0.20× ATR | 9 | EXCELLENT |
| ≤ 0.35× ATR | 8 | GOOD |
| ≤ 0.50× ATR | 7 | GOOD |
| ≤ 0.65× ATR | 6 | FAIR |
| ≤ 0.80× ATR | 5 | FAIR |
| ≤ 1.00× ATR | 4 | POOR |
| ≤ 1.30× ATR | 3 | POOR |
| ≤ 1.60× ATR | 2 | VERY_POOR |
| > 1.60× ATR | 1 | VERY_POOR |

### Weighting

When multiple TP levels are present, they are weighted as follows:

| TP Level | Weight |
|---|---|
| TP1 | 2.0× |
| TP2 | 1.5× |
| TP3+ | 1.0× |

### Bonuses and Penalties

| Condition | Adjustment |
|---|---|
| All TPs are on the correct side of entry | +0.5 |
| Any TP is on the wrong side of entry | −1.0 per TP |

### Adjustment Guidelines

| Score | Guideline |
|---|---|
| 1–3 | TPs have no structural basis. Identify key resistance (BUY) or support (SELL) levels and align TPs to them. |
| 4–5 | TPs are loosely aligned. Move each TP to within 0.50× ATR of a key structure level. |
| 6–7 | TP alignment is acceptable. Fine-tune to within 0.35× ATR of structure for a higher score. |
| 8–10 | TP alignment is excellent. No adjustment needed. |

---

## Practical Examples

### Example 1: Excellent BUY Signal (Score: 8.6/10 → APPROVE)

**Signal Parameters**
- Pair: XAUUSD
- Direction: BUY
- Entry: 2,345.00
- Stop Loss: 2,328.00
- TP1: 2,367.00 | TP2: 2,389.00 | TP3: 2,415.00
- ATR: 18.50
- Support: 2,332.00 | Resistance: 2,368.00

**Geometry Analysis**

| Component | Calculation | Score |
|---|---|---|
| Entry | Distance from support: \|2345 − 2332\| = 13.00 = 0.70× ATR | 6.0 |
| Stop Loss | Buffer below support: \|2328 − 2332\| = 4.00 = 0.22× ATR | 9.0 |
| Risk/Reward | TP1 R:R = (2367−2345)/(2345−2328) = 22/17 = 1.29 → score 4.0; TP2 R:R = 2.59; TP3 R:R = 4.12; avg = 2.67 → bonus +0.5+0.6 | 5.1 |
| Take Profits | TP1 distance from resistance (2368): 1.00 = 0.05× ATR → 10.0; TP2/TP3 scored on extra levels | 8.5 |

> **Note:** This example illustrates the scoring mechanics. Real scores depend on exact ATR and structure level values.

**Overall Score:** (6.0×0.25) + (9.0×0.30) + (5.1×0.30) + (8.5×0.15) = 1.50 + 2.70 + 1.53 + 1.28 = **7.01 → APPROVE**

**Manager Notes:** Entry is slightly far from support (0.70× ATR). Consider moving entry to 2,336–2,338 to improve the entry score to 8+. SL placement is excellent. R:R could be improved by tightening the SL slightly.

---

### Example 2: Marginal SELL Signal (Score: 6.2/10 → ADJUST)

**Signal Parameters**
- Pair: XAUUSD
- Direction: SELL
- Entry: 2,398.00
- Stop Loss: 2,418.00
- TP1: 2,378.00 | TP2: 2,355.00
- ATR: 22.00
- Support: 2,360.00 | Resistance: 2,405.00

**Geometry Analysis**

| Component | Calculation | Score |
|---|---|---|
| Entry | Distance from resistance: \|2398 − 2405\| = 7.00 = 0.32× ATR | 8.0 |
| Stop Loss | Buffer above resistance: \|2418 − 2405\| = 13.00 = 0.59× ATR | 7.0 |
| Risk/Reward | TP1 R:R = (2398−2378)/(2418−2398) = 20/20 = 1.00 → score 3.0; TP2 R:R = 1.95 → bonus +0.3 | 3.3 |
| Take Profits | TP1 distance from support (2360): 18.00 = 0.82× ATR → 5.0; TP2 distance: 5.00 = 0.23× ATR → 9.0 | 6.7 |

**Overall Score:** (8.0×0.25) + (7.0×0.30) + (3.3×0.30) + (6.7×0.15) = 2.00 + 2.10 + 0.99 + 1.01 = **6.10 → ADJUST**

**Adjustment Required:** The R:R ratio is the weakest component (3.3/10). TP1 at 1:1 is too close to entry. Move TP1 to at least 2,368 (R:R ≥ 1.5) or tighten the SL to 2,410 to improve the ratio. After adjustment, re-rate before approving.

---

### Example 3: Poor BUY Signal (Score: 3.8/10 → REJECT)

**Signal Parameters**
- Pair: XAUUSD
- Direction: BUY
- Entry: 2,310.00
- Stop Loss: 2,340.00 ← SL is ABOVE entry (wrong side)
- TP1: 2,325.00 ← TP1 is below entry (wrong side)
- ATR: 15.00
- Support: 2,318.00 | Resistance: 2,335.00

**Geometry Analysis**

| Component | Issue | Score |
|---|---|---|
| Entry | Entry (2310) is below support (2318) — wrong side penalty −1.0 | 2.0 |
| Stop Loss | SL (2340) is above entry for a BUY — structurally invalid, wrong side penalty −2.0 | 1.0 |
| Risk/Reward | TP1 is below entry for a BUY — negative R:R | 1.0 |
| Take Profits | TP1 on wrong side of entry — direction penalty −1.0 | 1.0 |

**Overall Score:** (2.0×0.25) + (1.0×0.30) + (1.0×0.30) + (1.0×0.15) = 0.50 + 0.30 + 0.30 + 0.15 = **1.25 → REJECT**

**Manager Notes:** This signal has fundamental structural errors. The SL is above the entry for a BUY signal (it should be below), and TP1 is below the entry (it should be above). This signal must be completely rebuilt before it can be considered for approval.

---

## Approval Decision Matrix

Use this matrix to make consistent approval decisions:

| Overall Score | Entry Score | SL Score | R:R Score | TP Score | Decision |
|---|---|---|---|---|---|
| ≥ 8.0 | Any | Any | Any | Any | ✅ APPROVE immediately |
| 7.0–7.9 | ≥ 6.0 | ≥ 7.0 | ≥ 6.0 | ≥ 5.0 | ✅ APPROVE |
| 7.0–7.9 | < 6.0 | ≥ 7.0 | ≥ 6.0 | ≥ 5.0 | ✅ APPROVE with note |
| 6.5–6.9 | ≥ 7.0 | ≥ 7.0 | ≥ 6.0 | Any | ⚠️ ADJUST entry/TP |
| 6.0–6.9 | Any | < 6.0 | Any | Any | ⚠️ ADJUST SL first |
| 5.5–6.9 | Any | Any | < 5.0 | Any | ⚠️ ADJUST R:R first |
| < 5.5 | Any | Any | Any | Any | ❌ REJECT |
| Any | Any | < 4.0 | Any | Any | ❌ REJECT (SL invalid) |
| Any | Any | Any | < 3.0 | Any | ❌ REJECT (R:R too poor) |

### Priority Order for Adjustments

When a signal scores in the ADJUST range, fix components in this order:

1. **Stop Loss** (highest weight, most critical for risk control)
2. **Risk/Reward** (highest weight, defines trade viability)
3. **Entry Price** (second highest weight, affects execution quality)
4. **Take Profits** (lowest weight, but important for realistic targets)

---

## Manager Adjustment Guidelines

### When to Adjust vs. Reject

**Adjust when:**
- Overall score is 5.5–6.9 AND the weakness is in a single component
- The structural logic is sound but execution levels need fine-tuning
- Entry is slightly far from the key level but SL and R:R are good
- TP levels need to be moved to align with structure

**Reject when:**
- Overall score is below 5.5
- SL is on the wrong side of the key level (structural error)
- R:R is below 1.0 (trade risks more than it gains)
- Multiple components score below 4.0
- The signal direction contradicts the current market regime

### Common Adjustments

#### Improving Entry Score

```
BUY signal with entry too far from support:
  Current entry: 2,350.00 (support at 2,332.00, distance = 18.00 = 0.97× ATR)
  Adjusted entry: 2,336.00 (distance = 4.00 = 0.22× ATR → score 9.0)
  
SELL signal with entry too far from resistance:
  Current entry: 2,380.00 (resistance at 2,405.00, distance = 25.00 = 1.14× ATR)
  Adjusted entry: 2,400.00 (distance = 5.00 = 0.23× ATR → score 9.0)
```

#### Improving SL Score

```
BUY signal with SL inside support:
  Current SL: 2,335.00 (support at 2,332.00 — SL is ABOVE support, wrong side)
  Adjusted SL: 2,328.00 (4.00 below support = 0.22× ATR buffer → score 9.0)
  
SELL signal with SL too wide:
  Current SL: 2,450.00 (resistance at 2,405.00, buffer = 45.00 = 2.05× ATR → score 2.0)
  Adjusted SL: 2,412.00 (buffer = 7.00 = 0.32× ATR → score 9.0)
```

#### Improving R:R Score

```
Signal with poor R:R (TP1 too close):
  Current: Entry 2,345, SL 2,328, TP1 2,362 → R:R = 17/17 = 1.0 → score 3.0
  Option A — Move TP1 further: TP1 = 2,379 → R:R = 34/17 = 2.0 → score 6.0
  Option B — Tighten SL: SL = 2,336 → R:R = 17/9 = 1.89 → score 5.0
  Option C — Both: TP1 = 2,379, SL = 2,336 → R:R = 34/9 = 3.78 → score 9.0
```

#### Improving TP Alignment

```
BUY signal with TP1 not at resistance:
  Current TP1: 2,370.00 (nearest resistance at 2,385.00, distance = 15.00 = 0.81× ATR → score 5.0)
  Adjusted TP1: 2,383.00 (distance = 2.00 = 0.11× ATR → score 9.0)
  Note: Place TP just below resistance (not at it) to ensure fill probability.
```

---

## API Reference

### Endpoint: GET /api/manager/signals/{signal_id}

Returns full signal details including the geometry rating.

**Response includes `geometry_rating` block:**

```json
{
  "success": true,
  "signal": { ... },
  "adjustments": [ ... ],
  "review_log": [ ... ],
  "geometry_rating": {
    "signal_id": "abc123",
    "pair": "XAUUSD",
    "signal_type": "BUY",
    "entry_price": 2345.00,
    "sl_price": 2328.00,
    "tp_levels": [2367.00, 2389.00, 2415.00],
    "atr": 18.50,
    "rr_ratios": [1.29, 2.59, 4.12],
    "rated_at": "2024-01-15T10:30:00",
    "overall_score": 7.01,
    "recommendation": "APPROVE",
    "summary": "XAUUSD BUY — Overall 7.01/10 → ✅ APPROVE | Entry 6.0 | SL 9.0 | R:R 5.1 | TP 8.5 | Weakest: R:R (5.1)",
    "breakdown": {
      "entry": {
        "score": 6.0,
        "label": "FAIR",
        "explanation": "Entry 2345.00 is 13.00 (0.70× ATR) from the key BUY level at 2332.00.",
        "guidelines": [
          "Entry placement is acceptable but could be improved. Aim for within 0.40× ATR of the key level (2332.00)."
        ]
      },
      "stop_loss": {
        "score": 9.0,
        "label": "EXCELLENT",
        "explanation": "SL 2328.00 is 4.00 (0.22× ATR) beyond the key BUY invalidation level at 2332.00.",
        "guidelines": [
          "SL placement is well-positioned beyond the key invalidation level. No adjustment needed."
        ]
      },
      "risk_reward": {
        "score": 5.1,
        "label": "FAIR",
        "explanation": "TP1 R:R = 1.29 (score 4.0). All R:R ratios: [1.29, 2.59, 4.12]. Average R:R = 2.67. Multi-TP bonus: +1.1.",
        "guidelines": [
          "TP1 R:R of 1.29 is below the 1.5:1 minimum. Extend TP1 to achieve at least 1.5:1, ideally 2.0:1."
        ]
      },
      "take_profits": {
        "score": 8.5,
        "label": "GOOD",
        "explanation": "3 TP level(s) rated. TP1=2367.00 → 1.00 (0.05× ATR) from level 2368.00 → score 10.0 ...",
        "guidelines": [
          "TP levels are well-aligned with market structure. No adjustment needed."
        ]
      }
    }
  }
}
```

---

### Endpoint: POST /api/manager/signals/rate

Compute a geometry rating for any set of price levels without requiring a stored signal.

**Request Body:**

```json
{
  "signal_type": "BUY",
  "entry_price": 2345.00,
  "sl_price": 2328.00,
  "tp_levels": [2367.00, 2389.00, 2415.00],
  "atr": 18.50,
  "support": 2332.00,
  "resistance": 2368.00,
  "extra_levels": [2385.00, 2410.00],
  "pair": "XAUUSD",
  "signal_id": "optional-reference-id"
}
```

**Required fields:** `signal_type`, `entry_price`, `sl_price`, `tp_levels`, `atr`

**Optional fields:** `support`, `resistance`, `extra_levels`, `pair`, `signal_id`

**Response:**

```json
{
  "success": true,
  "geometry_rating": { ... }
}
```

**Error responses:**
- `422 Unprocessable Entity` — Invalid price structure (e.g. BUY with SL above entry)
- `500 Internal Server Error` — Unexpected calculation failure

---

## Quick Reference Checklist

Use this checklist when reviewing a signal:

### Before Approving

- [ ] Overall score ≥ 7.0
- [ ] SL score ≥ 6.0 (SL is beyond the key level, not inside it)
- [ ] R:R score ≥ 5.0 (TP1 R:R ≥ 1.5:1)
- [ ] Entry score ≥ 5.0 (entry is on the correct side of the key level)
- [ ] No component score below 4.0
- [ ] Recommendation is APPROVE

### Red Flags (Reject Immediately)

- [ ] SL is on the wrong side of the key level (inside structure)
- [ ] TP1 is on the wrong side of entry
- [ ] R:R < 1.0 (trade risks more than it gains)
- [ ] Overall score < 5.5
- [ ] Any component score = 1.0

### When Adjusting

- [ ] Fix the lowest-scoring component first
- [ ] Re-rate after each adjustment
- [ ] Ensure overall score reaches ≥ 7.0 before approving
- [ ] Document the adjustment reason in the notes field

---

## Score Interpretation Guide

| Score | Label | Meaning | Manager Action |
|---|---|---|---|
| 9–10 | EXCELLENT | Textbook geometry, near-perfect placement | Approve immediately |
| 7–8 | GOOD | Solid geometry, meets quality standards | Approve |
| 6–6.9 | FAIR | Acceptable but improvable | Consider adjusting |
| 5.5–5.9 | FAIR | Marginal, needs improvement | Adjust before approving |
| 4–5.4 | POOR | Significant geometry issues | Adjust or reject |
| 2–3.9 | POOR | Major structural problems | Reject unless easily fixable |
| 1–1.9 | VERY_POOR | Fundamental errors | Reject |

---

## Implementation Guide

### How Geometry Rating Is Computed

The rating system is implemented in `backend/ml_engine/geometry_rating.py` as the `GeometryRating` class with a module-level singleton `geometry_rater`.

**Key design decisions:**

1. **ATR-relative scoring** — All distances are expressed as multiples of the ATR, making the system instrument-agnostic and volatility-aware. A 10-point move in a low-volatility environment is very different from a 10-point move in a high-volatility environment.

2. **Weighted overall score** — R:R and SL are weighted at 30% each because they directly control risk. Entry is 25% because it affects execution quality. TPs are 15% because they define reward targets but are less critical than risk management.

3. **Graceful degradation** — If structure levels (support/resistance) are not provided, the system falls back to ATR-based scoring using the SL distance as a proxy. This ensures every signal gets a rating even when market structure data is unavailable.

4. **Directional validation** — The system validates that all price levels are structurally consistent (BUY: SL < entry < TP; SELL: SL > entry > TP) before computing any scores. Invalid structures receive heavy penalties.

### Integration Points

The geometry rating is automatically computed and attached to the signal detail response:

```python
# In signal_management_api.py — get_signal_details endpoint
signal_doc = result.get("signal", {})
geometry_rating = _compute_geometry_rating(signal_doc)
result["geometry_rating"] = geometry_rating
```

The `_compute_geometry_rating` helper extracts price fields from the MongoDB signal document and calls `geometry_rater.rate()`. It handles missing fields gracefully and logs warnings on failure.

### ATR Fallback

If the signal document does not contain an `atr` field, the system estimates ATR as:

```
estimated_atr = abs(entry_price - sl_price) × 1.5
```

This is a rough proxy. For more accurate ratings, ensure signals include the `atr` field when they are generated.

### Structure Level Fields

The system looks for structure levels in the following signal document fields (in order of preference):

- `support_level` or `support` → used as the support level
- `resistance_level` or `resistance` → used as the resistance level

If neither is present, the system scores entry and SL placement using ATR-based proxies with a −0.5 penalty.

### Tracking Geometry Metrics Over Time

To track geometry quality trends, query the signal history and aggregate the `geometry_rating.overall_score` field:

```javascript
// MongoDB aggregation example
db.signals.aggregate([
  { $match: { "review_status": "APPROVED" } },
  { $group: {
    _id: "$pair",
    avg_geometry_score: { $avg: "$geometry_score" },
    count: { $sum: 1 }
  }},
  { $sort: { avg_geometry_score: -1 } }
])
```

> **Note:** To persist geometry scores for historical tracking, store the `overall_score` back to the signal document when the rating is computed. This can be added as a background task in a future iteration.

---

## Frequently Asked Questions

**Q: Why is the minimum approval threshold 7.0 and not higher?**

A: A score of 7.0 represents "good" geometry — the signal has solid structural alignment even if it's not perfect. Setting the threshold too high (e.g. 8.5+) would reject too many viable signals. Setting it too low (e.g. 6.0) would allow marginal signals through. 7.0 is the empirically validated balance point.

**Q: Can a signal with a low entry score still be approved?**

A: Yes, if the overall score is ≥ 7.0. Entry placement is weighted at 25%, so a poor entry score (e.g. 4.0) can be offset by excellent SL and R:R scores. However, a manager note should document why the entry placement was accepted.

**Q: What if no ATR value is available?**

A: The system estimates ATR from the SL distance (ATR ≈ SL distance × 1.5). This is a rough proxy. For accurate ratings, ensure the signal generation pipeline stores the ATR value in the signal document.

**Q: Should I always follow the recommendation?**

A: The recommendation is a starting point, not a mandate. Managers should use their judgment alongside the geometry score. A signal with a 7.2 score during a high-impact news event may warrant rejection. A signal with a 6.8 score in a very clean structural setup may warrant approval after a minor adjustment.

**Q: How do I improve a signal's R:R without changing the entry?**

A: Two options: (1) Move TP1 further from entry to increase the reward, or (2) tighten the SL to reduce the risk. Option 2 is only viable if the tighter SL is still beyond the key invalidation level. Option 1 is preferred when structure supports a further target.

---

*Gold Trading System v3.0.2 — Trade Geometry Rating System*
*Last updated: 2024*

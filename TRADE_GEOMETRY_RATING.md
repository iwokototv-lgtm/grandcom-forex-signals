# Trade Geometry Rating System

**Gold Trading System v3.0.2 — Manager Reference Guide**

---

## Overview

The Trade Geometry Rating System provides managers with an objective, quantifiable score (1–10) for every signal component before the approval decision is made. Instead of relying on intuition, managers now have a structured breakdown of exactly *why* a signal is geometrically sound or flawed, and precisely *what* to adjust when it is not.

Every signal returned by the pending queue (`GET /api/manager/signals/pending`) and the signal detail endpoint (`GET /api/manager/signals/{id}`) includes a `geometry_rating` block automatically computed by `ml_engine/geometry_rating.py`.

---

## Rating Architecture

Four independent components are rated on a 1–10 scale. The **overall score** is their unweighted average.

| Component | What It Measures |
|---|---|
| **Entry Price** | How well the entry is placed relative to support/resistance |
| **Stop Loss** | How well the SL is placed relative to structural levels |
| **Risk/Reward** | Quality of the R:R ratio across all TP levels |
| **Take Profits** | How well TPs align with structural targets |

### Approval Decision Matrix

| Overall Score | Recommendation | Action |
|---|---|---|
| **≥ 7.0 / 10** | ✅ **APPROVE** | Signal geometry is structurally sound — approve for live trading |
| **5.0 – 6.9 / 10** | ⚠️ **ADJUST** | Geometry has fixable issues — adjust price levels, then approve |
| **< 5.0 / 10** | ❌ **REJECT** | Geometry is too poor to trade safely — reject and regenerate |

### Score Labels

| Score Range | Label | Meaning |
|---|---|---|
| 9.0 – 10.0 | **EXCELLENT** | Textbook placement, no adjustments needed |
| 7.0 – 8.9 | **GOOD** | Solid placement, minor improvements possible |
| 5.0 – 6.9 | **FAIR** | Acceptable but suboptimal — consider adjusting |
| 3.0 – 4.9 | **POOR** | Significant structural issues — adjustment required |
| 1.0 – 2.9 | **VERY_POOR** | Critical flaw — reject or completely restructure |

---

## Component 1: Entry Price Rating

### What Is Being Measured

The entry price rating evaluates how close the proposed entry is to a key structural level (support for BUY, resistance for SELL). Entries placed at or near structure have a natural price reaction in their favour; entries that chase price away from structure carry unnecessary risk.

### Scoring Logic (BUY)

The distance from entry to the nearest support level is measured in ATR multiples:

| Distance to Support | Score | Label | Meaning |
|---|---|---|---|
| ≤ 0.25 ATR above support | 9.5 | EXCELLENT | Entry is in the demand zone — optimal |
| 0.25 – 0.50 ATR above support | 8.0 | GOOD | Entry is close to structure — solid |
| 0.50 – 1.00 ATR above support | 6.0 | FAIR | Entry is acceptable but not ideal |
| 1.00 – 1.50 ATR above support | 4.0 | POOR | Entry is chasing price — risky |
| > 1.50 ATR above support | 2.0 | VERY_POOR | Entry is far from structure — do not trade |

**Bonus (+1.0):** Entry aligns with a recent swing low (OTE zone).

### Scoring Logic (SELL)

Mirror of BUY — distance from entry to nearest resistance:

| Distance to Resistance | Score | Label |
|---|---|---|
| ≤ 0.25 ATR below resistance | 9.5 | EXCELLENT |
| 0.25 – 0.50 ATR below resistance | 8.0 | GOOD |
| 0.50 – 1.00 ATR below resistance | 6.0 | FAIR |
| 1.00 – 1.50 ATR below resistance | 4.0 | POOR |
| > 1.50 ATR below resistance | 2.0 | VERY_POOR |

**Bonus (+1.0):** Entry aligns with a recent swing high (OTE zone).

### Manager Adjustment Guidelines

- **Score < 7.0 (BUY):** Move entry down toward the nearest support level. Target 0.2–0.4 ATR above support.
- **Score < 7.0 (SELL):** Move entry up toward the nearest resistance level. Target 0.2–0.4 ATR below resistance.
- **Score < 4.0:** Do not approve. Wait for price to retrace to structure before entering.

---

## Component 2: Stop Loss Rating

### What Is Being Measured

The stop loss rating evaluates whether the SL is placed at a structurally logical level with an appropriate buffer. A well-placed SL sits just beyond a key structural level — far enough to avoid stop hunts, close enough to maintain a good R:R ratio.

### Scoring Logic (BUY)

The SL must be below entry. The buffer between the SL and the nearest support is measured in ATR multiples:

| Buffer Below Support | Score | Label | Meaning |
|---|---|---|---|
| 0.05 – 0.10 ATR below support | 9.5 | EXCELLENT | Ideal structural protection |
| 0.10 – 0.30 ATR below support | 8.0 | GOOD | Good buffer, adequate protection |
| 0.30 – 0.60 ATR below support | 6.0 | FAIR | Acceptable but wider than ideal |
| < 0.05 ATR below support | 4.0 | POOR | Too tight — high stop-hunt risk |
| > 1.00 ATR below support | 3.0 | POOR | Too wide — poor R:R impact |
| SL ≥ entry | 1.0 | VERY_POOR | Invalid — structural error |

**Bonus (+0.5):** SL is below a recent swing low (structural protection).

### Scoring Logic (SELL)

Mirror of BUY — buffer above nearest resistance:

| Buffer Above Resistance | Score | Label |
|---|---|---|
| 0.05 – 0.10 ATR above resistance | 9.5 | EXCELLENT |
| 0.10 – 0.30 ATR above resistance | 8.0 | GOOD |
| 0.30 – 0.60 ATR above resistance | 6.0 | FAIR |
| < 0.05 ATR above resistance | 4.0 | POOR |
| > 1.00 ATR above resistance | 3.0 | POOR |
| SL ≤ entry | 1.0 | VERY_POOR |

**Bonus (+0.5):** SL is above a recent swing high (structural protection).

### Manager Adjustment Guidelines

- **Score < 7.0 (too tight):** Move SL to at least 0.15 ATR beyond the structural level.
- **Score < 7.0 (too wide):** Tighten SL to 0.20–0.25 ATR beyond the structural level.
- **Score = 1.0 (invalid):** The SL is on the wrong side of entry — this is a critical error. Reject immediately.

---

## Component 3: Risk/Reward Rating

### What Is Being Measured

The R:R rating evaluates the quality of the reward-to-risk ratio across all TP levels. TP1 sets the minimum acceptable R:R; TP2 and TP3 extend the profile. A progressive TP ladder (each TP further than the last) earns a bonus.

### Scoring Logic

Scoring is based primarily on TP1 R:R (the minimum achievable reward):

| TP1 R:R | Score | Label | Meaning |
|---|---|---|---|
| ≥ 3.0 : 1 | 10.0 | EXCELLENT | Exceptional — all TPs exceed 3:1 |
| 2.0 – 2.9 : 1 | 8.5 | GOOD | Strong — TP1 meets the 2:1 quality threshold |
| 1.5 – 1.9 : 1 | 6.5 – 7.0 | FAIR | Acceptable — TP1 meets the 1.5:1 minimum |
| 1.0 – 1.4 : 1 | 4.0 | POOR | Below minimum — TP1 must be extended |
| < 1.0 : 1 | 2.0 | VERY_POOR | Unacceptable — risk exceeds reward |

**Bonus (+0.5):** Three or more TP levels with strictly increasing R:R (progressive ladder).

**Score upgrade (6.5 → 7.0):** TP1 is at 1.5:1 but TP2 or TP3 reaches ≥ 2.0:1.

### Manager Adjustment Guidelines

- **Score < 7.0 (TP1 too close):** Move TP1 to at least 1.5× the risk distance from entry.
- **Score < 5.0 (SL too wide):** Consider tightening the SL to improve R:R without moving TPs.
- **Score < 3.0:** Completely restructure the trade. The current setup should not be traded.

### R:R Quick Reference (XAUUSD example)

Assume entry = 2345.00, SL = 2330.00 (risk = 15 pips):

| TP Level | Price | R:R | Score Contribution |
|---|---|---|---|
| TP1 | 2367.50 | 1.5 : 1 | Minimum acceptable |
| TP2 | 2375.00 | 2.0 : 1 | Good |
| TP3 | 2390.00 | 3.0 : 1 | Excellent |

---

## Component 4: Take Profit Rating

### What Is Being Measured

The TP rating evaluates how well each take profit level aligns with a structural target (resistance for BUY, support for SELL). TPs placed at or near structural levels are more likely to be reached before price reverses; TPs placed in open air are speculative.

### Scoring Logic

Each TP is checked for proximity to the nearest structural target (as a percentage of the TP price):

| Proximity to Structural Level | Classification |
|---|---|
| Within 5% | **Aligned** — full credit |
| Within 10% | **Near** — half credit |
| Within 20% | **Far** — no credit, adjustment suggested |
| > 20% | **Misaligned** — no credit, adjustment required |

The overall score is based on the alignment ratio (aligned + 0.5 × near) / total TPs:

| Alignment Ratio | Score | Label |
|---|---|---|
| ≥ 90% | 9.5 | EXCELLENT |
| 70 – 89% | 8.0 | GOOD |
| 50 – 69% | 6.0 | FAIR |
| 30 – 49% | 4.0 | POOR |
| < 30% | 2.0 | VERY_POOR |

**Bonus (+0.5):** TP1 is placed just *before* (not through) a major structural level — conservative placement that respects the level.

### Manager Adjustment Guidelines

- **Score < 7.0:** Move misaligned TPs to the nearest structural level (resistance for BUY, support for SELL).
- **TP placed through a major level:** Move TP to just before the level (within 1–2% below resistance for BUY, above support for SELL).
- **No structural targets available:** Use ATR-based targets (TP1 = 2 ATR, TP2 = 3.5 ATR, TP3 = 5 ATR from entry).

---

## API Response Structure

The `geometry_rating` block is embedded in every signal object returned by:
- `GET /api/manager/signals/pending` — in each item of the `signals` array
- `GET /api/manager/signals/{id}` — in the `signal` object

### Example Response

```json
{
  "geometry_rating": {
    "signal_type": "BUY",
    "overall_score": 7.88,
    "recommendation": "APPROVE",
    "summary": "Signal geometry score 7.88/10 meets the approval threshold (≥ 7.0). Geometry is structurally sound.",
    "components": {
      "entry": {
        "score": 8.0,
        "label": "GOOD",
        "explanation": "Entry at 2345.5 is 0.42 ATR above support (2340.2) — good structural placement.",
        "guidelines": []
      },
      "stop_loss": {
        "score": 9.5,
        "label": "EXCELLENT",
        "explanation": "SL at 2330.0 is 0.08 ATR below support (2340.2) — ideal structural protection.",
        "guidelines": []
      },
      "risk_reward": {
        "score": 8.5,
        "label": "GOOD",
        "explanation": "Strong R:R — TP1 at 2.17:1, best TP at 4.33:1. TP1 meets the 2:1 minimum for quality setups. Progressive TP ladder detected — bonus.",
        "guidelines": []
      },
      "take_profits": {
        "score": 5.5,
        "label": "FAIR",
        "explanation": "Partial TP alignment — 1/3 TPs aligned. TP1 (2378.0) is aligned with structural target (within 1.2%). TP2 (2395.0) is 8.3% from nearest structural target — consider adjusting. TP3 (2420.0) is 22.1% from nearest structural target — misaligned.",
        "guidelines": [
          "Adjust TP2 to 2368.0 to align with structural target.",
          "TP3 is misaligned. Move to 2368.0 (nearest structural level)."
        ]
      }
    },
    "adjustment_guidelines": [
      "[Take Profits] Adjust TP2 to 2368.0 to align with structural target.",
      "[Take Profits] TP3 is misaligned. Move to 2368.0 (nearest structural level)."
    ],
    "thresholds": {
      "approve": 7.0,
      "adjust": 5.0
    }
  }
}
```

---

## Real-World Scenarios

### Scenario 1: High-Quality BUY Signal (Score: 8.6 → APPROVE)

**Signal:** XAUUSD BUY
- Entry: 2345.50 (0.3 ATR above support at 2341.80)
- SL: 2330.00 (0.2 ATR below support)
- TP1: 2378.00 (2.17:1 R:R, near resistance at 2380.00)
- TP2: 2395.00 (3.3:1 R:R, near swing high at 2398.00)
- TP3: 2420.00 (4.97:1 R:R, extended target)
- ATR: 12.5

| Component | Score | Label | Key Factor |
|---|---|---|---|
| Entry | 8.5 | GOOD | 0.3 ATR above support — solid demand zone |
| Stop Loss | 9.5 | EXCELLENT | 0.2 ATR below support — ideal buffer |
| Risk/Reward | 9.0 | EXCELLENT | TP1 at 2.17:1, progressive ladder |
| Take Profits | 7.5 | GOOD | TP1 and TP2 near structural levels |
| **Overall** | **8.6** | **GOOD** | **→ APPROVE** |

**Manager Action:** Approve. All components are structurally sound. The only minor note is TP3 is in open air, but TP1 and TP2 provide excellent partial-close opportunities.

---

### Scenario 2: Adjustable SELL Signal (Score: 5.8 → ADJUST)

**Signal:** XAUUSD SELL
- Entry: 2398.00 (1.2 ATR below resistance at 2413.00)
- SL: 2415.00 (0.16 ATR above resistance)
- TP1: 2370.00 (1.47:1 R:R — just below 1.5:1 minimum)
- TP2: 2350.00 (2.82:1 R:R)
- ATR: 14.0

| Component | Score | Label | Key Factor |
|---|---|---|---|
| Entry | 4.0 | POOR | 1.2 ATR below resistance — chasing price |
| Stop Loss | 8.0 | GOOD | 0.16 ATR above resistance — good buffer |
| Risk/Reward | 6.0 | FAIR | TP1 at 1.47:1 — just below minimum |
| Take Profits | 5.5 | FAIR | TP1 near support, TP2 misaligned |
| **Overall** | **5.9** | **FAIR** | **→ ADJUST** |

**Manager Adjustments Required:**
1. **Entry:** Move entry up to 2408.00 (0.35 ATR below resistance at 2413.00).
2. **TP1:** After adjusting entry, recalculate — new TP1 should be at 2400.50 (1.5:1 R:R with tighter entry).
3. After adjustments, re-rate: expected score ≈ 7.5 → APPROVE.

---

### Scenario 3: Rejected BUY Signal (Score: 3.2 → REJECT)

**Signal:** XAUUSD BUY
- Entry: 2389.00 (2.8 ATR above support at 2354.00)
- SL: 2385.00 (only 0.32 ATR below entry — no structural basis)
- TP1: 2395.00 (0.43:1 R:R — risk exceeds reward)
- TP2: 2400.00 (0.88:1 R:R)
- ATR: 14.0

| Component | Score | Label | Key Factor |
|---|---|---|---|
| Entry | 2.0 | VERY_POOR | 2.8 ATR above support — severely chasing price |
| Stop Loss | 2.5 | VERY_POOR | SL has no structural basis, only 0.32 ATR from entry |
| Risk/Reward | 2.0 | VERY_POOR | TP1 at 0.43:1 — risk exceeds reward |
| Take Profits | 6.5 | FAIR | TPs near resistance but irrelevant given poor R:R |
| **Overall** | **3.3** | **POOR** | **→ REJECT** |

**Manager Action:** Reject. The entry is chasing price 2.8 ATR above support, the SL has no structural basis, and the R:R is inverted. This signal should not be traded. Wait for price to retrace to the 2354–2360 support zone before re-evaluating.

---

## Manager Quick Reference Checklist

Use this checklist when reviewing a signal with a geometry rating:

### Before Approving (Score ≥ 7.0)

- [ ] Entry score ≥ 7.0 — entry is at or near a structural level
- [ ] SL score ≥ 7.0 — SL is beyond a structural level with appropriate buffer
- [ ] R:R score ≥ 7.0 — TP1 achieves at least 1.5:1 R:R (2.0:1 preferred)
- [ ] TP score ≥ 7.0 — at least one TP is aligned with a structural target
- [ ] No component has a VERY_POOR label
- [ ] `adjustment_guidelines` list is empty or contains only minor suggestions

### Before Adjusting (Score 5.0 – 6.9)

- [ ] Identify which components are below 7.0
- [ ] Read the `explanation` field for each weak component
- [ ] Apply the specific adjustments listed in `guidelines`
- [ ] After adjusting, re-fetch the signal to confirm the new geometry rating
- [ ] Confirm the adjusted signal achieves ≥ 7.0 before approving

### Before Rejecting (Score < 5.0)

- [ ] Confirm at least two components are POOR or VERY_POOR
- [ ] Check if the signal can be salvaged with adjustments (score 4.5–4.9)
- [ ] If entry is chasing price by > 2 ATR, reject — do not adjust
- [ ] If R:R is inverted (< 1.0:1), reject — do not adjust
- [ ] Record the rejection reason referencing the geometry score

---

## Adjustment Guidelines Reference

### Entry Price Adjustments

| Issue | Adjustment |
|---|---|
| BUY entry too far above support | Move entry down to 0.2–0.4 ATR above support |
| SELL entry too far below resistance | Move entry up to 0.2–0.4 ATR below resistance |
| Entry chasing price (> 1.5 ATR from structure) | Reject — wait for retrace |
| Entry aligns with OTE zone | No adjustment needed — bonus applies |

### Stop Loss Adjustments

| Issue | Adjustment |
|---|---|
| SL too tight (< 0.05 ATR from structure) | Move SL to 0.15 ATR beyond structural level |
| SL too wide (> 1.0 ATR from structure) | Tighten SL to 0.20–0.25 ATR beyond structural level |
| SL has no structural basis | Move SL to just beyond nearest swing high/low |
| SL on wrong side of entry | Critical error — reject immediately |

### Risk/Reward Adjustments

| Issue | Adjustment |
|---|---|
| TP1 R:R < 1.5:1 | Move TP1 to 1.5× risk distance from entry |
| TP1 R:R < 1.0:1 | Tighten SL or move TP1 — do not approve as-is |
| No TP2/TP3 | Add TP2 at 2.5:1 and TP3 at 4.0:1 for full ladder |
| Non-progressive TPs | Reorder TPs so each is further than the last |

### Take Profit Adjustments

| Issue | Adjustment |
|---|---|
| TP placed through a major level | Move TP to 1–2% before the level |
| TP misaligned (> 20% from structure) | Move TP to nearest resistance (BUY) or support (SELL) |
| All TPs in open air | Use ATR-based targets: TP1=2 ATR, TP2=3.5 ATR, TP3=5 ATR |
| TP below entry for BUY | Critical error — move TP above entry |

---

## Implementation Guide

### How the Rating Is Computed

The rating engine (`backend/ml_engine/geometry_rating.py`) is called automatically by the Signal Management API for every signal fetch. No manual invocation is required.

**Data sources used (in priority order):**

1. `signal.entry_price`, `signal.sl_price`, `signal.tp_levels`, `signal.type` — always present
2. `signal.current_price` — falls back to `entry_price` if absent
3. `signal.atr` — stored by the TP/SL engine; falls back to 0.5% of entry price
4. `signal.nearest_support`, `signal.nearest_resistance` — stored by the TP/SL engine
5. `signal.market_structure.support`, `signal.market_structure.resistance` — from SMC analysis
6. `signal.swing_high`, `signal.swing_low` — from SMC analysis
7. If structural levels are absent, the engine estimates them from ATR multiples

### Improving Rating Accuracy

To get the most accurate geometry ratings, ensure signals are generated with the full TP/SL engine output stored on the signal document:

```python
# In your signal generation pipeline, store these fields:
signal_doc = {
    "entry_price": ...,
    "sl_price": ...,
    "tp_levels": [...],
    "type": "BUY",
    "atr": tp_sl_result["atr"],
    "atr_weighted": tp_sl_result["atr_weighted"],
    "nearest_support": tp_sl_result["market_structure"]["support"],
    "nearest_resistance": tp_sl_result["market_structure"]["resistance"],
    "swing_high": smc_result["swing_highs"][-1]["price"] if smc_result.get("swing_highs") else None,
    "swing_low": smc_result["swing_lows"][-1]["price"] if smc_result.get("swing_lows") else None,
    "market_structure": tp_sl_result["market_structure"],
}
```

### Direct Usage (Python)

```python
from ml_engine.geometry_rating import geometry_rater

rating = geometry_rater.rate_signal(
    signal_type="BUY",
    entry_price=2345.50,
    sl_price=2330.00,
    tp_levels=[2378.00, 2395.00, 2420.00],
    current_price=2346.00,
    atr=12.5,
    nearest_support=2341.80,
    nearest_resistance=2380.00,
    swing_high=2398.00,
    swing_low=2338.00,
)

print(f"Overall: {rating.overall_score:.2f}/10 → {rating.recommendation}")
print(f"Entry:   {rating.entry_rating.score:.1f} ({rating.entry_rating.label})")
print(f"SL:      {rating.sl_rating.score:.1f} ({rating.sl_rating.label})")
print(f"R:R:     {rating.rr_rating.score:.1f} ({rating.rr_rating.label})")
print(f"TPs:     {rating.tp_rating.score:.1f} ({rating.tp_rating.label})")

for guideline in rating.adjustment_guidelines:
    print(f"  → {guideline}")

# Serialise to dict for JSON response
rating_dict = rating.to_dict()
```

### Tracking Geometry Metrics Over Time

The `geometry_rating.overall_score` and `geometry_rating.recommendation` fields can be stored on the signal document at approval time to enable performance tracking:

```python
# When approving a signal, store the geometry score for analytics
await db.signals.update_one(
    {"_id": oid},
    {"$set": {
        "geometry_score_at_approval": signal["geometry_rating"]["overall_score"],
        "geometry_recommendation": signal["geometry_rating"]["recommendation"],
    }}
)
```

This enables queries like:
- Average geometry score of approved signals vs. their win rate
- Correlation between geometry score and P&L
- Manager approval patterns by geometry score band

---

## Frequently Asked Questions

**Q: Can I approve a signal with a score below 7.0?**
A: The system does not block approval — the geometry rating is advisory. However, approving signals below 7.0 without documented justification is not recommended. Signals below 5.0 should always be rejected or restructured.

**Q: The geometry rating shows INSUFFICIENT_DATA. What does this mean?**
A: The signal document is missing `entry_price`, `sl_price`, or `tp_levels`. This indicates a data quality issue in signal generation. Reject the signal and investigate the generation pipeline.

**Q: Why is my entry score low even though the signal looks good visually?**
A: The rating uses ATR-based distance thresholds. If the ATR stored on the signal is incorrect (e.g., defaulted to 0.5% of price), the distance calculations will be off. Ensure the TP/SL engine stores the correct ATR on the signal document.

**Q: Can the thresholds be changed?**
A: Yes. The constants `APPROVE_THRESHOLD` (default 7.0) and `ADJUST_THRESHOLD` (default 5.0) are defined at the top of `backend/ml_engine/geometry_rating.py` and can be adjusted to match your risk tolerance.

**Q: Does the geometry rating replace the confidence score?**
A: No. The confidence score reflects the ML model's prediction probability. The geometry rating evaluates structural placement quality. Both are complementary — a high-confidence signal with poor geometry should still be adjusted before approval.

---

*Gold Trading System v3.0.2 — Trade Geometry Rating System v1.0*

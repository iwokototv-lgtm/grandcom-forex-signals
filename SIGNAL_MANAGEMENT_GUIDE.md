# Signal Management Guide

**Gold Trading System v3.0.2 — Manager Review & Approval Workflow**

---

## Overview

The Signal Management feature gives system managers complete control over the signal lifecycle. Instead of auto-generated signals going directly to trading, every signal now enters a `PENDING_REVIEW` queue where managers can inspect, adjust, approve, or reject it before it goes live.

All decisions are logged in an immutable audit trail, and approval statistics are available per manager and per trading pair.

---

## Signal Lifecycle

```
Auto-generated signal
        │
        ▼
  PENDING_REVIEW  ──── adjust ────►  ADJUSTED
        │                                │
        ├── approve ──────────────────────┤
        │                                │
        ▼                                ▼
     ACTIVE                          ACTIVE
   (live trading)                 (live trading)
        │
        └── reject ──► REJECTED
                      (removed from queue)
```

| Status | Meaning |
|---|---|
| `PENDING_REVIEW` | Signal generated, awaiting manager decision |
| `ADJUSTED` | Price levels modified by a manager, awaiting final decision |
| `ACTIVE` | Approved and live in the trading queue |
| `REJECTED` | Rejected by a manager, not sent to trading |

---

## Authentication

All Signal Management endpoints require a valid manager JWT. Obtain one from the Manager Auth endpoint:

```bash
curl -X POST https://your-api.railway.app/api/manager/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "manager@example.com", "password": "your-password"}'
```

Response:
```json
{
  "success": true,
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "role": "MANAGER"
}
```

Use the token in all subsequent requests:
```bash
-H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

---

## API Endpoints

### 1. List Pending Signals

**`GET /api/manager/signals/pending`**

Returns all signals currently awaiting review, sorted newest-first.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | int | 50 | Maximum signals to return (1–200) |
| `pair` | string | — | Filter by trading pair (e.g. `XAUUSD`) |
| `min_confidence` | float | — | Minimum confidence threshold (0–100) |

**Example:**
```bash
curl -X GET "https://your-api.railway.app/api/manager/signals/pending?limit=20&pair=XAUUSD" \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "success": true,
  "total": 3,
  "signals": [
    {
      "id": "6849a1b2c3d4e5f6a7b8c9d0",
      "pair": "XAUUSD",
      "type": "BUY",
      "entry_price": 2345.50,
      "tp_levels": [2348.50, 2352.00, 2358.00],
      "sl_price": 2340.00,
      "confidence": 82.5,
      "status": "PENDING_REVIEW",
      "created_at": "2025-01-15T09:30:00.000000",
      "analysis": "[TRENDING_BULL | score=74] Strong bullish momentum..."
    }
  ]
}
```

---

### 2. Get Signal Details

**`GET /api/manager/signals/{signal_id}`**

Returns the full signal document including all adjustment history and the complete review log.

**Example:**
```bash
curl -X GET "https://your-api.railway.app/api/manager/signals/6849a1b2c3d4e5f6a7b8c9d0" \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "success": true,
  "signal": {
    "id": "6849a1b2c3d4e5f6a7b8c9d0",
    "pair": "XAUUSD",
    "type": "BUY",
    "entry_price": 2346.00,
    "tp_levels": [2349.00, 2353.00, 2359.00],
    "sl_price": 2340.00,
    "confidence": 82.5,
    "status": "ADJUSTED",
    "review_status": "ADJUSTED",
    "adjusted_by": "mgr-uuid-1234",
    "adjusted_at": "2025-01-15T09:35:00.000000"
  },
  "adjustments": [
    {
      "adjustment_id": "adj-uuid-5678",
      "adjusted_by": "mgr-uuid-1234",
      "adjusted_at": "2025-01-15T09:35:00.000000",
      "original_entry": 2345.50,
      "new_entry": 2346.00,
      "original_sl": 2340.00,
      "new_sl": 2340.00,
      "notes": "Adjusted entry to align with resistance level"
    }
  ],
  "review_log": [
    {
      "action": "signal:adjust",
      "manager_id": "mgr-uuid-1234",
      "timestamp": "2025-01-15T09:35:00.000000",
      "success": true
    }
  ]
}
```

---

### 3. Approve Signal

**`POST /api/manager/signals/approve`**

Approves a `PENDING_REVIEW` or `ADJUSTED` signal. The signal status changes to `ACTIVE` and it enters the live trading queue.

**Request body:**
```json
{
  "signal_id": "6849a1b2c3d4e5f6a7b8c9d0",
  "notes": "Strong confluence at key support. Good R:R ratio."
}
```

| Field | Required | Description |
|---|---|---|
| `signal_id` | ✅ | MongoDB ObjectId of the signal |
| `notes` | ❌ | Optional manager notes (max 1000 chars) |

**Example:**
```bash
curl -X POST "https://your-api.railway.app/api/manager/signals/approve" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "signal_id": "6849a1b2c3d4e5f6a7b8c9d0",
    "notes": "Strong confluence at key support. Good R:R ratio."
  }'
```

**Response:**
```json
{
  "success": true,
  "signal_id": "6849a1b2c3d4e5f6a7b8c9d0",
  "new_status": "ACTIVE",
  "approved_by": "mgr-uuid-1234",
  "approved_at": "2025-01-15T09:40:00.000000",
  "pair": "XAUUSD",
  "type": "BUY"
}
```

---

### 4. Reject Signal

**`POST /api/manager/signals/reject`**

Rejects a `PENDING_REVIEW` or `ADJUSTED` signal. A rejection reason is **mandatory** — this ensures every rejection is documented for quality-improvement purposes.

**Request body:**
```json
{
  "signal_id": "6849a1b2c3d4e5f6a7b8c9d0",
  "reason": "Weak confluence — RSI divergence not confirmed on 4H timeframe",
  "notes": "Wait for cleaner setup near 2330 support"
}
```

| Field | Required | Description |
|---|---|---|
| `signal_id` | ✅ | MongoDB ObjectId of the signal |
| `reason` | ✅ | Rejection reason (min 5 chars, max 500 chars) |
| `notes` | ❌ | Optional additional notes (max 1000 chars) |

**Example:**
```bash
curl -X POST "https://your-api.railway.app/api/manager/signals/reject" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "signal_id": "6849a1b2c3d4e5f6a7b8c9d0",
    "reason": "Weak confluence — RSI divergence not confirmed on 4H timeframe",
    "notes": "Wait for cleaner setup near 2330 support"
  }'
```

**Response:**
```json
{
  "success": true,
  "signal_id": "6849a1b2c3d4e5f6a7b8c9d0",
  "new_status": "REJECTED",
  "rejected_by": "mgr-uuid-1234",
  "rejected_at": "2025-01-15T09:42:00.000000",
  "rejection_reason": "Weak confluence — RSI divergence not confirmed on 4H timeframe",
  "pair": "XAUUSD",
  "type": "BUY"
}
```

---

### 5. Adjust Signal

**`POST /api/manager/signals/adjust`**

Modifies the entry price, TP levels, and/or SL price of a pending signal before approval. At least one price field must be provided.

The adjusted signal enters `ADJUSTED` status and must then be explicitly approved or rejected.

**Request body:**
```json
{
  "signal_id": "6849a1b2c3d4e5f6a7b8c9d0",
  "entry_price": 2346.00,
  "tp_levels": [2349.00, 2353.00, 2359.00],
  "sl_price": 2341.00,
  "notes": "Tightened entry to resistance breakout level; SL moved above swing low"
}
```

| Field | Required | Description |
|---|---|---|
| `signal_id` | ✅ | MongoDB ObjectId of the signal |
| `entry_price` | ❌ | New entry price (must be > 0) |
| `tp_levels` | ❌ | New TP levels list (1–5 values, all > 0) |
| `sl_price` | ❌ | New SL price (must be > 0) |
| `notes` | ❌ | Rationale for the adjustment (recommended) |

**Price validation rules:**
- **BUY**: `sl_price < entry_price < tp_levels[0] ≤ tp_levels[1] ≤ ...`
- **SELL**: `sl_price > entry_price > tp_levels[0] ≥ tp_levels[1] ≥ ...`

**Example:**
```bash
curl -X POST "https://your-api.railway.app/api/manager/signals/adjust" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "signal_id": "6849a1b2c3d4e5f6a7b8c9d0",
    "entry_price": 2346.00,
    "sl_price": 2341.00,
    "notes": "Tightened entry to resistance breakout level"
  }'
```

**Response:**
```json
{
  "success": true,
  "signal_id": "6849a1b2c3d4e5f6a7b8c9d0",
  "new_status": "ADJUSTED",
  "adjusted_by": "mgr-uuid-1234",
  "adjusted_at": "2025-01-15T09:35:00.000000",
  "pair": "XAUUSD",
  "type": "BUY",
  "entry_price": 2346.00,
  "tp_levels": [2348.50, 2352.00, 2358.00],
  "sl_price": 2341.00
}
```

---

### 6. Signal History

**`GET /api/manager/signals/history/all`**

Returns reviewed signals (approved, rejected, or adjusted) with a summary statistics block.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | int | 100 | Maximum records (1–500) |
| `hours` | int | 168 | Look-back window in hours (default 7 days) |
| `status` | string | — | Filter: `APPROVED`, `REJECTED`, or `ADJUSTED` |
| `pair` | string | — | Filter by trading pair |
| `manager_id` | string | — | Filter by manager who acted on the signal |

**Example:**
```bash
curl -X GET "https://your-api.railway.app/api/manager/signals/history/all?hours=72&status=APPROVED" \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "success": true,
  "total": 45,
  "history": [...],
  "stats": {
    "approved": 32,
    "rejected": 10,
    "adjusted": 3,
    "approval_rate": 76.2
  }
}
```

---

### 7. Approval Statistics

**`GET /api/manager/signals/stats/approval`**

Returns aggregated approval statistics broken down by manager and by trading pair.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `days` | int | 30 | Look-back window in days (1–365) |
| `manager_id` | string | — | Restrict stats to a single manager |

**Example:**
```bash
curl -X GET "https://your-api.railway.app/api/manager/signals/stats/approval?days=7" \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "success": true,
  "period_days": 7,
  "stats": {
    "total_pending": 5,
    "total_approved": 28,
    "total_rejected": 9,
    "total_adjusted": 4,
    "overall_approval_rate": 75.7
  },
  "per_manager": [
    {
      "manager_id": "mgr-uuid-1234",
      "approved": 18,
      "rejected": 6,
      "adjusted": 3,
      "total_actions": 27,
      "approval_rate": 75.0
    }
  ],
  "per_pair": [
    {
      "pair": "XAUUSD",
      "approved": 22,
      "rejected": 7,
      "approval_rate": 75.9
    }
  ]
}
```

---

## Common Workflows

### Workflow 1: Standard Approval

1. Check the pending queue: `GET /api/manager/signals/pending`
2. Review a specific signal: `GET /api/manager/signals/{id}`
3. Approve it: `POST /api/manager/signals/approve`

### Workflow 2: Adjust Then Approve

1. Check the pending queue: `GET /api/manager/signals/pending`
2. Review the signal: `GET /api/manager/signals/{id}`
3. Adjust the entry price to align with a key level: `POST /api/manager/signals/adjust`
4. Confirm the adjusted values look correct: `GET /api/manager/signals/{id}`
5. Approve the adjusted signal: `POST /api/manager/signals/approve`

### Workflow 3: Reject Low-Quality Signal

1. Check the pending queue: `GET /api/manager/signals/pending`
2. Review the signal: `GET /api/manager/signals/{id}`
3. Reject with a documented reason: `POST /api/manager/signals/reject`

### Workflow 4: Weekly Performance Review

1. Pull the last 7 days of history: `GET /api/manager/signals/history/all?hours=168`
2. Check approval stats: `GET /api/manager/signals/stats/approval?days=7`
3. Identify pairs with low approval rates and review the rejection reasons

---

## Best Practices

### Signal Review Checklist

Before approving a signal, verify:

- [ ] **Trend alignment** — Does the signal direction match the higher-timeframe trend?
- [ ] **Key levels** — Is the entry near a significant support/resistance level?
- [ ] **Risk/Reward** — Is the R:R ratio at least 1.5:1?
- [ ] **Confidence score** — Is the AI confidence above your team's threshold (e.g. 75%)?
- [ ] **Market conditions** — Are there any upcoming high-impact news events?
- [ ] **Spread** — Is the current spread acceptable for this pair?

### When to Adjust

Adjust a signal (rather than rejecting it outright) when:

- The entry price is slightly off a key level but the overall setup is valid
- The SL is too tight and needs to be moved below a swing low
- The TP levels need to align with known resistance zones
- The signal is good but the timing needs a small correction

### When to Reject

Reject a signal when:

- The signal direction contradicts the higher-timeframe trend
- The confidence score is below your team's minimum threshold
- High-impact news is imminent (within 30 minutes)
- The R:R ratio is below 1.5:1 even after adjustment
- The setup has already played out (entry price is far from current price)
- There are already too many open trades in the same direction

### Rejection Reason Quality

Write rejection reasons that help improve the AI model over time:

**Good:** `"Bearish signal generated during strong uptrend — 4H EMA200 acting as support, not resistance"`

**Poor:** `"Bad signal"`

---

## Permissions Matrix

| Action | ADMIN | MANAGER | VIEWER |
|---|---|---|---|
| List pending signals | ✅ | ✅ | ❌ |
| Get signal details | ✅ | ✅ | ❌ |
| Approve signal | ✅ | ✅ | ❌ |
| Reject signal | ✅ | ✅ | ❌ |
| Adjust signal | ✅ | ✅ | ❌ |
| View history | ✅ | ✅ | ❌ |
| View stats | ✅ | ✅ | ❌ |

> **Note:** VIEWER role does not have access to signal management endpoints. Only ADMIN and MANAGER roles can review signals.

---

## Error Reference

| HTTP Status | Meaning | Common Cause |
|---|---|---|
| 400 | Bad Request | Invalid signal ID, missing required field, price validation failure |
| 401 | Unauthorized | Missing or expired JWT token |
| 403 | Forbidden | Manager role does not have permission (VIEWER role) |
| 404 | Not Found | Signal ID does not exist in the database |
| 422 | Unprocessable Entity | Pydantic validation error (e.g. `reason` too short) |

### Common Error Messages

**`"Signal cannot be approved from status 'ACTIVE'"`**
The signal has already been approved. Check the signal status with `GET /api/signals/{id}`.

**`"Price validation failed: BUY: sl_price (2350.00) must be < entry_price (2345.00)"`**
The SL price is above the entry price for a BUY signal. Correct the price levels.

**`"A rejection reason is mandatory"`**
The `reason` field was empty or missing. Provide a meaningful rejection reason.

**`"At least one of entry_price, tp_levels, or sl_price must be provided"`**
The adjust request body contained no price fields. Include at least one price to change.

---

## Troubleshooting

### Signals Not Appearing in Pending Queue

1. Verify the signal generator is creating signals with `status: "PENDING_REVIEW"` (not `"ACTIVE"`)
2. Check the database directly: `db.signals.find({"status": "PENDING_REVIEW"})`
3. Confirm your JWT has not expired (tokens expire after 24 hours by default)

### Adjustment Rejected with Price Validation Error

1. Retrieve the current signal values: `GET /api/signals/{id}`
2. Note the signal `type` (BUY or SELL)
3. For BUY: ensure `sl_price < entry_price < tp_levels[0]`
4. For SELL: ensure `sl_price > entry_price > tp_levels[0]`

### Stats Showing Zero Records

1. Confirm the `days` parameter covers the period you expect
2. Check that signals have a `review_status` field set (only reviewed signals appear in stats)
3. Verify the `signal_review_log` collection is being written to (check server logs)

---

## Performance Tracking Metrics

Use the approval statistics endpoint to track these key metrics over time:

| Metric | Formula | Target |
|---|---|---|
| **Approval Rate** | `approved / (approved + rejected) × 100` | > 60% |
| **Adjustment Rate** | `adjusted / total_reviewed × 100` | < 20% |
| **Review Throughput** | `total_reviewed / days` | Depends on signal volume |
| **Per-Pair Quality** | Approval rate per pair | Identify underperforming pairs |
| **Manager Workload** | `total_actions` per manager | Ensure balanced distribution |

A consistently low approval rate (< 40%) suggests the signal generator needs retraining or its confidence thresholds need raising. A high adjustment rate (> 30%) suggests the AI entry prices are systematically off and the ML optimizer parameters should be reviewed.

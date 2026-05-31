# Signal Management — Feature Summary

**Gold Trading System v3.0.2**

---

## Overview

The Signal Management feature introduces a manager review and approval workflow between signal generation and live trading. Managers can inspect every auto-generated signal, adjust its price levels if needed, and make an explicit approve or reject decision before it enters the trading queue.

All decisions are immutably logged and aggregated into approval statistics that help track signal quality and manager performance over time.

---

## What Changed

| Before | After |
|---|---|
| Signals generated → immediately `ACTIVE` | Signals generated → `PENDING_REVIEW` queue |
| No manager oversight | Full manager review workflow |
| No adjustment capability | Entry, TP, and SL adjustable before approval |
| No rejection mechanism | Reject with mandatory documented reason |
| No approval audit trail | Immutable audit log for every decision |
| No approval statistics | Per-manager and per-pair approval stats |

---

## New Files

| File | Size | Purpose |
|---|---|---|
| `backend/signal_manager.py` | ~17.9 KB | Core `SignalManager` class — all business logic |
| `backend/signal_management_api.py` | ~9.6 KB | FastAPI router — 7 HTTP endpoints |
| `SIGNAL_MANAGEMENT_GUIDE.md` | ~10.2 KB | Complete user guide with curl examples |
| `SIGNAL_MANAGEMENT_SUMMARY.md` | ~6.2 KB | This file — feature overview and quick start |

---

## Modified Files

| File | Change |
|---|---|
| `backend/server.py` | Registers the `signal_management_router` at `/api/signals` |

---

## Quick Start

### Step 1 — Log in as a manager

```bash
curl -X POST https://your-api.railway.app/api/manager/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "manager@example.com", "password": "your-password"}'
```

Save the `access_token` from the response.

### Step 2 — Check the pending queue

```bash
curl https://your-api.railway.app/api/manager/signals/pending \
  -H "Authorization: Bearer <token>"
```

### Step 3 — Review a signal

```bash
curl https://your-api.railway.app/api/manager/signals/<signal_id> \
  -H "Authorization: Bearer <token>"
```

### Step 4 — Approve, reject, or adjust

**Approve:**
```bash
curl -X POST https://your-api.railway.app/api/manager/signals/approve \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"signal_id": "<id>", "notes": "Strong setup at key support"}'
```

**Reject:**
```bash
curl -X POST https://your-api.railway.app/api/manager/signals/reject \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"signal_id": "<id>", "reason": "Contradicts 4H trend direction"}'
```

**Adjust then approve:**
```bash
# Adjust entry price
curl -X POST https://your-api.railway.app/api/manager/signals/adjust \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"signal_id": "<id>", "entry_price": 2346.00, "notes": "Aligned to resistance"}'

# Then approve the adjusted signal
curl -X POST https://your-api.railway.app/api/manager/signals/approve \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"signal_id": "<id>"}'
```

---

## API Endpoint Summary

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/manager/signals/pending` | List signals awaiting review |
| `GET` | `/api/manager/signals/{id}` | Full signal details + adjustment history |
| `POST` | `/api/manager/signals/approve` | Approve a pending signal |
| `POST` | `/api/manager/signals/reject` | Reject with mandatory reason |
| `POST` | `/api/manager/signals/adjust` | Adjust entry / TP levels / SL price |
| `GET` | `/api/manager/signals/history/all` | Review history with summary stats |
| `GET` | `/api/manager/signals/stats/approval` | Per-manager and per-pair approval stats |

---

## Permissions Matrix

| Role | Can Review Signals | Can Approve | Can Reject | Can Adjust | Can View Stats |
|---|---|---|---|---|---|
| `ADMIN` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `MANAGER` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `VIEWER` | ❌ | ❌ | ❌ | ❌ | ❌ |

Authentication uses the same JWT scheme as the existing Manager API (`/api/manager/auth/login`). No new login endpoint is needed.

---

## Database Collections

Three MongoDB collections are used:

| Collection | Purpose |
|---|---|
| `signals` | Existing collection — `status` and `review_status` fields extended |
| `signal_review_log` | Immutable audit record for every approve/reject/adjust action |
| `signal_adjustments` | History of price-level changes with original and new values |

### New fields added to `signals` documents

| Field | Type | Set when |
|---|---|---|
| `review_status` | string | Any review action taken |
| `approved_by` | string | Signal approved |
| `approved_at` | datetime | Signal approved |
| `rejected_by` | string | Signal rejected |
| `rejected_at` | datetime | Signal rejected |
| `rejection_reason` | string | Signal rejected |
| `adjusted_by` | string | Signal adjusted |
| `adjusted_at` | datetime | Signal adjusted |
| `manager_notes` | string | Any review action with notes |

---

## Signal Status Lifecycle

```
PENDING_REVIEW
    ├── adjust  →  ADJUSTED  ──┐
    │                          ├── approve  →  ACTIVE (live trading)
    └── approve ───────────────┘
    └── reject  →  REJECTED
```

Signals in `ADJUSTED` status must still be explicitly approved or rejected — adjustment alone does not send a signal to trading.

---

## Integration Instructions

### Making signals enter the review queue

To route auto-generated signals through the review workflow, set their initial status to `PENDING_REVIEW` instead of `ACTIVE` in the signal generation code (`backend/server.py`, `generate_signal_for_pair` function):

```python
# Change this:
signal_dict["status"] = "ACTIVE"

# To this:
signal_dict["status"] = "PENDING_REVIEW"
```

This single change routes all auto-generated signals through the manager review queue. Manually created signals (via `/api/admin/signals/create`) can remain `ACTIVE` if desired, or also be routed through review by the same change.

### Enabling the review workflow for existing signals

Existing `ACTIVE` signals are not affected. Only signals created after the status change will enter the review queue.

---

## Architecture Notes

- `SignalManager` uses a lazy MongoDB connection (same pattern as `SystemManager`)
- The router reuses the existing JWT auth scheme from `manager_api.py` — no new auth infrastructure needed
- All price validation is performed in `signal_manager.py` before any database write
- The `signal_review_log` collection is append-only — records are never updated or deleted
- The `signal_adjustments` collection preserves original values before every price change
- The router is registered with a try/except block in `server.py` so a startup failure does not take down the entire API

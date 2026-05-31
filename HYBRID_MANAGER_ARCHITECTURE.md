# Hybrid Manager System — Architecture Reference
## Gold Trading System v3.0.2 — Technical Architecture Documentation

---

## Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [Component Breakdown](#2-component-breakdown)
3. [Data Flow Diagrams](#3-data-flow-diagrams)
4. [Database Schema](#4-database-schema)
5. [Integration Points](#5-integration-points)
6. [API Endpoint Map](#6-api-endpoint-map)
7. [Security Architecture](#7-security-architecture)
8. [Scalability Design](#8-scalability-design)
9. [Deployment Architecture](#9-deployment-architecture)
10. [Monitoring & Observability](#10-monitoring--observability)

---

## 1. System Architecture Overview

The Hybrid Manager System is a layered, async-first architecture built on FastAPI with MongoDB as the persistence layer. It integrates with the existing Gold Trading System v3.0.2 without replacing any existing functionality.

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                                │
│                                                                     │
│   Web Dashboard    Mobile App    API Clients    Telegram Bot        │
│        │               │              │              │              │
└────────┼───────────────┼──────────────┼──────────────┼─────────────┘
         │               │              │              │
         └───────────────┴──────────────┴──────────────┘
                                 │
                                 ▼ HTTPS / JWT Bearer
┌─────────────────────────────────────────────────────────────────────┐
│                         API GATEWAY LAYER                           │
│                                                                     │
│   FastAPI Application (server.py)                                   │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │  /api/hybrid/*          hybrid_manager_api.py (NEW)         │   │
│   │  /api/manager/*         manager_api.py (existing)           │   │
│   │  /api/manager/signals/* signal_management_api.py (existing) │   │
│   │  /api/*                 server.py (existing routes)         │   │
│   └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        BUSINESS LOGIC LAYER                         │
│                                                                     │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │  HybridManager   │  │   RiskEngine     │  │PerformanceTracker│  │
│  │                  │  │                  │  │                  │  │
│  │ • Role RBAC      │  │ • Position limits│  │ • Manager metrics│  │
│  │ • Approval flow  │  │ • Drawdown calc  │  │ • Signal quality │  │
│  │ • Signal scoring │  │ • Exposure check │  │ • Leaderboards   │  │
│  │ • Collaboration  │  │ • SL enforcement │  │ • Reports        │  │
│  │ • Alerts         │  │ • Circuit breaker│  │ • Trend analysis │  │
│  │ • Audit logging  │  │ • Risk scoring   │  │ • Sharpe/Sortino │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘  │
│                                                                     │
│  ┌──────────────────┐  ┌──────────────────┐                        │
│  │  SystemManager   │  │  SignalManager   │                        │
│  │  (existing)      │  │  (existing)      │                        │
│  └──────────────────┘  └──────────────────┘                        │
└─────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        PERSISTENCE LAYER                            │
│                                                                     │
│   MongoDB (motor async driver)                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │  hybrid_managers        hybrid_signals      hybrid_alerts   │   │
│   │  hybrid_audit_log       hybrid_risk_config  hybrid_comments │   │
│   │  hybrid_notes           hybrid_performance  signal_outcomes │   │
│   │  risk_events            risk_engine_config                  │   │
│   │                                                             │   │
│   │  (existing collections)                                     │   │
│   │  signals  system_managers  manager_audit_log  system_alerts │   │
│   └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Component Breakdown

### 2.1 HybridManager (`backend/ml_engine/hybrid_manager.py`)

The core orchestration class. Responsible for:

| Responsibility | Methods |
|---|---|
| Manager CRUD | `add_manager`, `remove_manager`, `suspend_manager`, `update_manager`, `list_managers`, `get_manager` |
| Signal workflow | `submit_signal_for_review`, `approve_signal`, `reject_signal`, `adjust_signal`, `escalate_signal` |
| Signal queries | `get_pending_signals`, `get_signal_detail` |
| Collaboration | `add_comment`, `add_note`, `get_team_activity` |
| Risk controls | `set_risk_limits`, `get_risk_config`, `trigger_circuit_breaker`, `reset_circuit_breaker` |
| Performance | `get_manager_performance`, `get_signal_performance_stats` |
| Alerts | `create_manual_alert`, `resolve_alert`, `list_alerts` |
| Dashboard | `get_dashboard` |
| Compliance | `get_audit_log`, `get_compliance_report` |

**Key design decisions:**
- All DB operations are async (motor)
- Lazy DB connection (initialised on first use)
- Every mutating operation writes to `hybrid_audit_log`
- Permission checks via `check_hybrid_permission()` before any operation
- Singleton instance `hybrid_manager` exported for use by the API

### 2.2 RiskEngine (`backend/ml_engine/risk_engine.py`)

Stateless risk validation engine. Responsible for:

| Responsibility | Methods |
|---|---|
| R:R validation | `validate_risk_reward` |
| Position sizing | `validate_position_size` |
| Drawdown calculation | `calculate_drawdown`, `check_drawdown_limits` |
| Correlation checks | `check_correlation_limits` |
| Exposure limits | `check_exposure_limits` |
| SL enforcement | `enforce_stop_loss` |
| Full signal validation | `validate_signal` |
| Real-time metrics | `get_real_time_metrics` |
| DB operations | `save_risk_event`, `get_risk_events`, `update_config` |

**Key design decisions:**
- Core validation methods are synchronous (no I/O) for speed
- DB operations are async (for event persistence)
- `RiskValidationResult` class provides structured output
- Configuration loaded from DB on startup, cached in memory
- Singleton instance `risk_engine` exported

### 2.3 PerformanceTracker (`backend/ml_engine/performance_tracker.py`)

Analytics and reporting engine. Responsible for:

| Responsibility | Methods |
|---|---|
| Trade metrics | `calculate_trade_metrics` |
| Manager metrics | `get_manager_metrics` |
| Signal quality | `get_signal_quality_stats` |
| Leaderboard | `get_leaderboard` |
| Weekly report | `generate_weekly_report` |
| Monthly report | `generate_monthly_report` |
| Trend analysis | `get_trend_analysis` |
| Outcome recording | `record_signal_outcome`, `get_outcome_stats` |

**Key design decisions:**
- Statistical functions (`_sharpe_ratio`, `_sortino_ratio`, etc.) are pure functions
- All DB queries use MongoDB aggregation pipelines for efficiency
- Reports are generated on-demand (not pre-computed)
- Singleton instance `performance_tracker` exported

### 2.4 HybridManagerAPI (`backend/hybrid_manager_api.py`)

FastAPI router with 35+ endpoints. Responsible for:
- JWT authentication and validation
- Request/response serialisation (Pydantic models)
- Permission enforcement (delegates to HybridManager)
- Error handling (HTTP 400, 401, 403, 404)
- Dependency injection (`get_current_hybrid_manager`)

---

## 3. Data Flow Diagrams

### 3.1 Signal Approval Flow

```
Client                  API Router              HybridManager           MongoDB
  │                         │                        │                     │
  │  POST /signals/approve  │                        │                     │
  │────────────────────────►│                        │                     │
  │                         │  get_current_manager() │                     │
  │                         │───────────────────────►│                     │
  │                         │                        │  find_one(managers) │
  │                         │                        │────────────────────►│
  │                         │                        │◄────────────────────│
  │                         │  check_permission()    │                     │
  │                         │  (signal:approve)      │                     │
  │                         │                        │                     │
  │                         │  approve_signal()      │                     │
  │                         │───────────────────────►│                     │
  │                         │                        │  find_one(signals)  │
  │                         │                        │────────────────────►│
  │                         │                        │◄────────────────────│
  │                         │                        │                     │
  │                         │                        │  Check: duplicate?  │
  │                         │                        │  Count approvals    │
  │                         │                        │  Check: fully appr? │
  │                         │                        │                     │
  │                         │                        │  update_one(signals)│
  │                         │                        │────────────────────►│
  │                         │                        │                     │
  │                         │                        │  update_one(managers│
  │                         │                        │  stats)             │
  │                         │                        │────────────────────►│
  │                         │                        │                     │
  │                         │                        │  insert(audit_log)  │
  │                         │                        │────────────────────►│
  │                         │                        │                     │
  │                         │                        │  [if fully approved]│
  │                         │                        │  insert(alert)      │
  │                         │                        │────────────────────►│
  │                         │◄───────────────────────│                     │
  │◄────────────────────────│                        │                     │
  │  {success, status, ...} │                        │                     │
```

### 3.2 Risk Validation Flow

```
Client                  API Router              RiskEngine
  │                         │                        │
  │  POST /risk/validate    │                        │
  │────────────────────────►│                        │
  │                         │  validate_signal()     │
  │                         │───────────────────────►│
  │                         │                        │  validate_risk_reward()
  │                         │                        │  ├── Direction check
  │                         │                        │  ├── SL/TP distance
  │                         │                        │  └── R:R ratio
  │                         │                        │
  │                         │                        │  validate_position_size()
  │                         │                        │  ├── Lot size limits
  │                         │                        │  ├── Dollar risk %
  │                         │                        │  └── Exposure %
  │                         │                        │
  │                         │                        │  check_correlation_limits()
  │                         │                        │  └── Category count
  │                         │                        │
  │                         │                        │  check_exposure_limits()
  │                         │                        │  ├── Per-pair limit
  │                         │                        │  ├── Per-category limit
  │                         │                        │  └── Total portfolio
  │                         │                        │
  │                         │                        │  enforce_stop_loss()
  │                         │                        │  └── ATR-based SL
  │                         │◄───────────────────────│
  │◄────────────────────────│  {approved, violations,│
  │                         │   warnings, risk_score}│
```

### 3.3 Authentication Flow

```
Client                  API Router              MongoDB
  │                         │                        │
  │  POST /auth/login       │                        │
  │  {email, password}      │                        │
  │────────────────────────►│                        │
  │                         │  find_one(hybrid_mgrs) │
  │                         │───────────────────────►│
  │                         │◄───────────────────────│
  │                         │                        │
  │                         │  bcrypt.verify()       │
  │                         │                        │
  │                         │  jwt.encode()          │
  │                         │  {sub, role, type,     │
  │                         │   exp, issued_at}      │
  │                         │                        │
  │                         │  update last_login     │
  │                         │───────────────────────►│
  │◄────────────────────────│                        │
  │  {access_token, role,   │                        │
  │   permissions, ...}     │                        │
```

---

## 4. Database Schema

### 4.1 `hybrid_managers` Collection

```json
{
  "_id": "ObjectId",
  "manager_id": "uuid-string",
  "email": "manager@company.com",
  "full_name": "John Smith",
  "role": "TRADING_MANAGER",
  "password_hash": "$2b$12$...",
  "department": "Trading Desk",
  "is_active": true,
  "is_suspended": false,
  "created_at": "ISODate",
  "created_by": "uuid-string",
  "last_login": "ISODate",
  "last_activity": "ISODate",
  "updated_at": "ISODate",
  "updated_by": "uuid-string",
  "deactivated_at": null,
  "deactivated_by": null,
  "suspended_at": null,
  "suspended_by": null,
  "suspension_reason": null,
  "metadata": {},
  "performance_stats": {
    "total_approvals": 45,
    "total_rejections": 12,
    "total_adjustments": 8,
    "approval_accuracy": 0.0,
    "avg_review_time_minutes": 0.0,
    "signals_reviewed": 57
  },
  "notification_prefs": {
    "email_alerts": true,
    "critical_only": false,
    "daily_digest": true
  }
}
```

**Indexes:**
- `manager_id` (unique)
- `email` (unique)
- `role`
- `is_active`
- `department`

### 4.2 `hybrid_signals` Collection

```json
{
  "_id": "ObjectId",
  "review_id": "uuid-string",
  "signal_id": "sig_001",
  "submitted_at": "ISODate",
  "submitted_by": "uuid-string",
  "status": "PENDING_REVIEW",
  "risk_tier": "MEDIUM",
  "quality_score": {
    "composite_score": 72.5,
    "grade": "B",
    "dimensions": {
      "technical_confidence": 78.5,
      "rr_quality": 65.0,
      "entry_precision": 80.0,
      "mtf_alignment": 70.0,
      "regime_fit": 95.0,
      "volatility_context": 90.0,
      "session_quality": 95.0,
      "historical_pattern": 65.0
    },
    "risk_tier": "MEDIUM",
    "recommendation": "APPROVE"
  },
  "required_approvals": 2,
  "risk_manager_required": false,
  "current_approval_count": 1,
  "is_approved": false,
  "is_rejected": false,
  "final_decision": null,
  "final_decision_at": null,
  "final_decision_by": null,
  "expires_at": "ISODate",
  "signal_data": {
    "pair": "XAUUSD",
    "signal_type": "BUY",
    "entry_price": 2650.50,
    "sl_price": 2640.00,
    "tp1": 2665.00,
    "tp2": 2680.00,
    "tp3": 2700.00,
    "lot_size": 0.10,
    "confidence": 78.5,
    "risk_reward": 2.4,
    "strategy": "SMC",
    "regime": "TREND_UP",
    "volatility": "NORMAL",
    "session": "LONDON"
  },
  "approvals": [
    {
      "approval_id": "uuid-string",
      "manager_id": "uuid-string",
      "role": "TRADING_MANAGER",
      "approved_at": "ISO-string",
      "notes": "Strong setup",
      "adjusted_params": {}
    }
  ],
  "rejections": [],
  "adjustments": [],
  "comments": [],
  "escalations": [],
  "outcome": null,
  "pnl": null,
  "pnl_pct": null,
  "r_multiple": null,
  "closed_at": null
}
```

**Indexes:**
- `signal_id`
- `status`
- `risk_tier`
- `submitted_at`
- `approvals.manager_id`
- `rejections.manager_id`
- `final_decision_at`

### 4.3 `hybrid_audit_log` Collection

```json
{
  "_id": "ObjectId",
  "audit_id": "uuid-string",
  "timestamp": "ISODate",
  "action": "signal:approve",
  "performed_by": "uuid-string",
  "role": "TRADING_MANAGER",
  "details": {
    "signal_id": "sig_001",
    "approval_count": 1,
    "required": 2,
    "fully_approved": false,
    "notes": "Strong setup"
  },
  "success": true,
  "error": null,
  "ip_address": "192.168.1.1",
  "system": "hybrid_manager",
  "version": "3.0.2"
}
```

**Indexes:**
- `timestamp` (descending)
- `performed_by`
- `action`
- `system`

### 4.4 `hybrid_risk_config` Collection

```json
{
  "_id": "ObjectId",
  "config_type": "global_limits",
  "limits": {
    "max_daily_drawdown_pct": 3.0,
    "max_weekly_drawdown_pct": 6.0,
    "max_monthly_drawdown_pct": 12.0,
    "max_position_size_lots": 1.0,
    "max_open_positions": 5,
    "max_exposure_per_pair_pct": 25.0,
    "max_total_exposure_pct": 80.0,
    "min_rr_ratio": 1.5,
    "max_lot_size": 2.0,
    "circuit_breaker_drawdown_pct": 5.0,
    "auto_halt_on_breach": true
  },
  "circuit_breaker_active": false,
  "trading_halted": false,
  "halt_reason": null,
  "halted_at": null,
  "halted_by": null,
  "reset_at": null,
  "reset_by": null,
  "reset_reason": null,
  "updated_at": "ISODate",
  "updated_by": "uuid-string"
}
```

### 4.5 `hybrid_alerts` Collection

```json
{
  "_id": "ObjectId",
  "alert_id": "uuid-string",
  "title": "High Impact News Approaching",
  "message": "US CPI data release in 30 minutes.",
  "severity": "WARNING",
  "category": "TRADING",
  "resolved": false,
  "created_at": "ISODate",
  "created_by": "uuid-string",
  "resolved_at": null,
  "resolved_by": null,
  "resolution_note": null,
  "metadata": {}
}
```

**Indexes:**
- `alert_id`
- `resolved`
- `severity`
- `category`
- `created_at`

### 4.6 `hybrid_comments` Collection

```json
{
  "_id": "ObjectId",
  "comment_id": "uuid-string",
  "signal_id": "sig_001",
  "manager_id": "uuid-string",
  "role": "ANALYST",
  "comment_text": "Strong SMC structure at this level.",
  "comment_type": "ANALYSIS",
  "mentions": ["uuid-string-2"],
  "is_private": false,
  "created_at": "ISODate",
  "edited": false,
  "reactions": {}
}
```

### 4.7 `hybrid_notes` Collection

```json
{
  "_id": "ObjectId",
  "note_id": "uuid-string",
  "manager_id": "uuid-string",
  "role": "TRADING_MANAGER",
  "title": "Gold Market Outlook — Week 48",
  "content": "DXY showing weakness...",
  "note_type": "MARKET_ANALYSIS",
  "signal_id": null,
  "tags": ["gold", "weekly-outlook"],
  "created_at": "ISODate",
  "updated_at": null,
  "is_pinned": false,
  "views": 0
}
```

### 4.8 `signal_outcomes` Collection

```json
{
  "_id": "ObjectId",
  "outcome_id": "uuid-string",
  "signal_id": "sig_001",
  "outcome": "WIN",
  "pnl": 1250.00,
  "pnl_pct": 2.5,
  "r_multiple": 2.4,
  "closed_at": "ISODate",
  "recorded_at": "ISODate",
  "metadata": {
    "pair": "XAUUSD",
    "strategy": "SMC"
  }
}
```

### 4.9 `risk_events` Collection

```json
{
  "_id": "ObjectId",
  "event_id": "uuid-string",
  "event_type": "drawdown_breach",
  "severity": "WARNING",
  "details": {
    "daily_pnl_pct": -2.8,
    "limit": 3.0,
    "utilisation": 93.3
  },
  "timestamp": "ISODate"
}
```

---

## 5. Integration Points

### 5.1 Integration with Existing System Manager

The Hybrid Manager System runs **alongside** the existing `SystemManager` (at `/api/manager/*`). They share:
- The same MongoDB instance and database
- The same JWT secret (but different token types: `"type": "hybrid_manager"` vs `"type": "manager"`)
- The same `signals` collection (for reading existing signals)

They do **not** share:
- Manager accounts (separate `hybrid_managers` vs `system_managers` collections)
- Audit logs (separate `hybrid_audit_log` vs `manager_audit_log` collections)
- Alert collections (separate `hybrid_alerts` vs `system_alerts` collections)

### 5.2 Integration with Signal Generator

The Hybrid Manager System can receive signals from the existing signal generator via the `POST /api/hybrid/signals/submit` endpoint. The signal generator should:

1. Generate a signal as normal
2. POST the signal to `/api/hybrid/signals/submit` with the signal data
3. Wait for the signal to be approved before sending to subscribers

Alternatively, the signal generator can continue to use the existing `PENDING_REVIEW` status flow via the existing `SignalManager`.

### 5.3 Integration with Notification Service

When a signal is fully approved, the Hybrid Manager creates an alert. The existing notification service can be extended to listen for `hybrid_alerts` documents with `category: "TRADING"` and `severity: "INFO"` to trigger Telegram/push notifications.

### 5.4 Integration with Backtest Engine

The `PerformanceTracker.record_signal_outcome()` method can be called by the `SignalOutcomeTracker` when a signal closes. This feeds real outcome data into the performance analytics system.

---

## 6. API Endpoint Map

### Authentication (3 endpoints)
| Method | Path | Description |
|---|---|---|
| POST | `/api/hybrid/auth/login` | Login and get JWT |
| GET | `/api/hybrid/auth/me` | Get current manager profile |
| POST | `/api/hybrid/auth/refresh` | Refresh JWT token |

### Manager Management (6 endpoints)
| Method | Path | Description |
|---|---|---|
| POST | `/api/hybrid/managers` | Create manager |
| GET | `/api/hybrid/managers` | List managers |
| GET | `/api/hybrid/managers/{id}` | Get manager |
| PUT | `/api/hybrid/managers/{id}` | Update manager |
| DELETE | `/api/hybrid/managers/{id}` | Deactivate manager |
| POST | `/api/hybrid/managers/{id}/suspend` | Suspend manager |

### Signal Management (9 endpoints)
| Method | Path | Description |
|---|---|---|
| POST | `/api/hybrid/signals/submit` | Submit for review |
| POST | `/api/hybrid/signals/approve` | Approve signal |
| POST | `/api/hybrid/signals/reject` | Reject signal |
| POST | `/api/hybrid/signals/adjust` | Adjust parameters |
| POST | `/api/hybrid/signals/escalate` | Escalate signal |
| GET | `/api/hybrid/signals/pending` | List pending signals |
| GET | `/api/hybrid/signals/{id}` | Get signal detail |
| POST | `/api/hybrid/signals/score` | Score signal quality |
| POST | `/api/hybrid/signals/validate-risk` | Validate risk |

### Risk Management (7 endpoints)
| Method | Path | Description |
|---|---|---|
| POST | `/api/hybrid/risk/limits` | Set risk limits |
| GET | `/api/hybrid/risk/config` | Get risk config |
| POST | `/api/hybrid/risk/circuit-breaker/trigger` | Trigger circuit breaker |
| POST | `/api/hybrid/risk/circuit-breaker/reset` | Reset circuit breaker |
| POST | `/api/hybrid/risk/validate` | Validate signal risk |
| GET | `/api/hybrid/risk/metrics` | Get risk metrics |
| POST | `/api/hybrid/risk/engine/config` | Update risk engine config |
| GET | `/api/hybrid/risk/events` | Get risk events |

### Performance Analytics (6 endpoints)
| Method | Path | Description |
|---|---|---|
| GET | `/api/hybrid/performance/managers` | Manager performance |
| GET | `/api/hybrid/performance/managers/{id}/detail` | Detailed manager metrics |
| GET | `/api/hybrid/performance/signals` | Signal statistics |
| GET | `/api/hybrid/performance/signals/quality` | Quality statistics |
| GET | `/api/hybrid/performance/leaderboard` | Manager leaderboard |
| GET | `/api/hybrid/performance/outcomes` | Outcome statistics |
| POST | `/api/hybrid/performance/outcomes/record` | Record outcome |

### Team Collaboration (3 endpoints)
| Method | Path | Description |
|---|---|---|
| POST | `/api/hybrid/collaboration/comments` | Add comment |
| POST | `/api/hybrid/collaboration/notes` | Add note |
| GET | `/api/hybrid/collaboration/activity` | Team activity feed |

### Alerts (3 endpoints)
| Method | Path | Description |
|---|---|---|
| POST | `/api/hybrid/alerts` | Create alert |
| POST | `/api/hybrid/alerts/{id}/resolve` | Resolve alert |
| GET | `/api/hybrid/alerts` | List alerts |

### Dashboard (2 endpoints)
| Method | Path | Description |
|---|---|---|
| GET | `/api/hybrid/dashboard` | Real-time dashboard |
| GET | `/api/hybrid/dashboard/system-status` | System health |

### Compliance & Audit (2 endpoints)
| Method | Path | Description |
|---|---|---|
| GET | `/api/hybrid/audit` | Audit trail |
| GET | `/api/hybrid/compliance/report` | Compliance report |

### Reports (3 endpoints)
| Method | Path | Description |
|---|---|---|
| GET | `/api/hybrid/reports/weekly` | Weekly report |
| GET | `/api/hybrid/reports/monthly` | Monthly report |
| GET | `/api/hybrid/reports/trends` | Trend analysis |

### Utility (2 endpoints)
| Method | Path | Description |
|---|---|---|
| GET | `/api/hybrid/roles` | List roles and permissions |
| GET | `/api/hybrid/health` | Health check (public) |

**Total: 52 endpoints**

---

## 7. Security Architecture

### Authentication

- **Algorithm:** HS256 JWT
- **Token type:** `"type": "hybrid_manager"` (distinct from existing manager tokens)
- **Expiry:** Configurable via `JWT_EXPIRATION_HOURS` env var (default: 24h)
- **Payload:** `{sub: manager_id, role, type, exp, issued_at}`

### Authorisation

- **Model:** Role-Based Access Control (RBAC)
- **Enforcement:** `check_hybrid_permission()` called at the start of every operation
- **Granularity:** Per-action permissions (e.g., `signal:approve`, `risk:set_limits`)
- **Failure mode:** `PermissionError` → HTTP 403

### Password Security

- **Hashing:** bcrypt with auto-generated salt
- **Library:** passlib with bcrypt scheme
- **Storage:** Only hash stored, never plaintext

### Suspension vs Deactivation

- **Suspended:** Account exists, login blocked, audit trail preserved
- **Deactivated:** Account soft-deleted, login blocked, data preserved

### Audit Trail Integrity

- Audit records are insert-only (no update/delete operations)
- Each record includes timestamp, actor, role, and action details
- Failed operations are also logged (with `success: false`)

---

## 8. Scalability Design

### Horizontal Scaling

The API layer is stateless — all state is in MongoDB. Multiple API instances can run behind a load balancer without coordination.

**Considerations:**
- JWT validation is stateless (no session store needed)
- MongoDB connections use connection pooling (motor)
- No in-memory caches that need synchronisation

### Database Scaling

**Recommended indexes for production:**

```javascript
// hybrid_managers
db.hybrid_managers.createIndex({ "manager_id": 1 }, { unique: true })
db.hybrid_managers.createIndex({ "email": 1 }, { unique: true })
db.hybrid_managers.createIndex({ "role": 1, "is_active": 1 })

// hybrid_signals
db.hybrid_signals.createIndex({ "signal_id": 1 })
db.hybrid_signals.createIndex({ "status": 1, "submitted_at": -1 })
db.hybrid_signals.createIndex({ "risk_tier": 1, "status": 1 })
db.hybrid_signals.createIndex({ "approvals.manager_id": 1 })
db.hybrid_signals.createIndex({ "final_decision_at": -1 })

// hybrid_audit_log
db.hybrid_audit_log.createIndex({ "timestamp": -1 })
db.hybrid_audit_log.createIndex({ "performed_by": 1, "timestamp": -1 })
db.hybrid_audit_log.createIndex({ "action": 1, "timestamp": -1 })

// hybrid_alerts
db.hybrid_alerts.createIndex({ "resolved": 1, "severity": 1 })
db.hybrid_alerts.createIndex({ "created_at": -1 })

// hybrid_comments
db.hybrid_comments.createIndex({ "signal_id": 1, "created_at": -1 })
db.hybrid_comments.createIndex({ "manager_id": 1, "created_at": -1 })
```

### Performance Optimisations

1. **Lazy DB connections** — Connection pool initialised on first request
2. **Projection queries** — `password_hash` excluded from all manager queries
3. **Aggregation pipelines** — Used for leaderboard and report generation
4. **Limit enforcement** — All list endpoints enforce maximum limits
5. **Async throughout** — All DB operations use `await` with motor

---

## 9. Deployment Architecture

### Railway Deployment

The Hybrid Manager API is registered in `server.py` alongside existing routers:

```python
# In server.py (to be added)
try:
    from hybrid_manager_api import router as hybrid_router
    app.include_router(hybrid_router)
    logger.info("✅ Hybrid Manager API registered at /api/hybrid")
except Exception as _hm_err:
    logger.warning(f"⚠️ Hybrid Manager API not loaded: {_hm_err}")
```

### Environment Variables

| Variable | Description | Default |
|---|---|---|
| `MONGO_URL` | MongoDB connection string | `mongodb://localhost:27017` |
| `DB_NAME` | Database name | `gold_signals_v3` |
| `JWT_SECRET` | JWT signing secret | `your-secret-key` |
| `JWT_ALGORITHM` | JWT algorithm | `HS256` |
| `JWT_EXPIRATION_HOURS` | Token expiry | `24` |
| `RISK_FREE_RATE` | Annual risk-free rate for Sharpe | `0.05` |

### File Structure

```
backend/
├── hybrid_manager_api.py          ← FastAPI router (NEW)
├── ml_engine/
│   ├── hybrid_manager.py          ← Core HybridManager class (NEW)
│   ├── risk_engine.py             ← Enterprise RiskEngine (NEW)
│   └── performance_tracker.py    ← PerformanceTracker (NEW)
├── manager_api.py                 ← Existing manager API
├── signal_management_api.py       ← Existing signal management API
├── ml_engine/
│   └── system_manager.py          ← Existing SystemManager
└── server.py                      ← Main FastAPI app
```

---

## 10. Monitoring & Observability

### Health Check

```bash
GET /api/hybrid/health
```

Returns system version, status, and timestamp. No authentication required. Suitable for load balancer health checks.

### System Status

```bash
GET /api/hybrid/dashboard/system-status
```

Returns CPU, memory, disk usage, and MongoDB connectivity. Requires authentication.

### Key Metrics to Monitor

| Metric | Alert Threshold | Description |
|---|---|---|
| Pending signals | > 20 | Signals awaiting review |
| Critical alerts | > 0 | Unresolved critical alerts |
| Circuit breaker | Active | Trading halted |
| Daily drawdown | > 80% of limit | Approaching daily limit |
| API error rate | > 5% | HTTP 4xx/5xx responses |
| DB response time | > 500ms | MongoDB slow queries |

### Logging

All operations log at appropriate levels:
- `INFO` — Normal operations (approvals, rejections, logins)
- `WARNING` — Approaching limits, escalations, suspensions
- `CRITICAL` — Circuit breaker triggers, security events

Log format follows the existing `structured_logger.py` conventions.

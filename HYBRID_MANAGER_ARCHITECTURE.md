# Hybrid Manager System — Architecture Reference
## Gold Trading System v3.0.2 | Enterprise Architecture Documentation

---

## Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [Component Descriptions](#2-component-descriptions)
3. [Data Flow Diagrams](#3-data-flow-diagrams)
4. [Database Schema](#4-database-schema)
5. [Integration Points](#5-integration-points)
6. [Security Model](#6-security-model)
7. [Scalability Considerations](#7-scalability-considerations)
8. [Deployment Architecture](#8-deployment-architecture)
9. [Monitoring & Observability](#9-monitoring--observability)
10. [Disaster Recovery](#10-disaster-recovery)

---

## 1. System Architecture Overview

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        GOLD TRADING SYSTEM v3.0.2                       │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                     FastAPI Application Layer                     │   │
│  │                                                                   │   │
│  │  ┌─────────────┐  ┌──────────────────┐  ┌────────────────────┐  │   │
│  │  │  server.py  │  │  manager_api.py  │  │ hybrid_manager_    │  │   │
│  │  │  (main app) │  │  /api/manager    │  │ api.py /api/hybrid │  │   │
│  │  └─────────────┘  └──────────────────┘  └────────────────────┘  │   │
│  │         │                  │                       │              │   │
│  └─────────┼──────────────────┼───────────────────────┼─────────────┘   │
│            │                  │                       │                  │
│  ┌─────────▼──────────────────▼───────────────────────▼─────────────┐   │
│  │                      Business Logic Layer                         │   │
│  │                                                                   │   │
│  │  ┌──────────────────────────────────────────────────────────┐    │   │
│  │  │              HybridManagerSystem (Facade)                 │    │   │
│  │  │                                                           │    │   │
│  │  │  ┌──────────────────┐  ┌──────────────────────────────┐  │    │   │
│  │  │  │ MultiTierApproval│  │   RiskManagementEngine       │  │    │   │
│  │  │  │ Workflow         │  │   - 7 automated checks       │  │    │   │
│  │  │  │ - 6 stages       │  │   - Runtime config           │  │    │   │
│  │  │  │ - Role gates     │  │   - Portfolio heat           │  │    │   │
│  │  │  └──────────────────┘  └──────────────────────────────┘  │    │   │
│  │  │                                                           │    │   │
│  │  │  ┌──────────────────┐  ┌──────────────────────────────┐  │    │   │
│  │  │  │ PerformanceAna-  │  │   ComplianceAuditLog         │  │    │   │
│  │  │  │ lytics           │  │   - Immutable records        │  │    │   │
│  │  │  │ - Manager KPIs   │  │   - Compliance reports       │  │    │   │
│  │  │  │ - P&L attribution│  │   - Decision tracking        │  │    │   │
│  │  │  └──────────────────┘  └──────────────────────────────┘  │    │   │
│  │  │                                                           │    │   │
│  │  │  ┌──────────────────┐  ┌──────────────────────────────┐  │    │   │
│  │  │  │ TeamCollaboration│  │   AlertingSystem             │  │    │   │
│  │  │  │ Engine           │  │   - Real-time alerts         │  │    │   │
│  │  │  │ - Comments/Notes │  │   - Auto-resolution          │  │    │   │
│  │  │  │ - Activity feed  │  │   - Severity levels          │  │    │   │
│  │  │  └──────────────────┘  └──────────────────────────────┘  │    │   │
│  │  └──────────────────────────────────────────────────────────┘    │   │
│  │                                                                   │   │
│  │  ┌──────────────────────────────────────────────────────────┐    │   │
│  │  │              Legacy ML Engine Components                  │    │   │
│  │  │  SystemManager │ RiskManager │ PerformanceAttribution    │    │   │
│  │  │  SignalFilter  │ RegimeDetector │ CorrelationEngine      │    │   │
│  │  └──────────────────────────────────────────────────────────┘    │   │
│  └───────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ┌───────────────────────────────────────────────────────────────────┐   │
│  │                        Data Layer (MongoDB)                        │   │
│  │                                                                   │   │
│  │  hybrid_signals  │  hybrid_audit_log  │  hybrid_alerts           │   │
│  │  hybrid_managers │  hybrid_comments   │  hybrid_notes            │   │
│  │  hybrid_risk_config                                               │   │
│  └───────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### Technology Stack

| Layer | Technology | Version |
|---|---|---|
| Runtime | Python | 3.11+ |
| Web Framework | FastAPI | 0.115+ |
| ASGI Server | Uvicorn | 0.30+ |
| Database | MongoDB | 6.0+ |
| DB Driver | Motor (async) | 3.4+ |
| Auth | PyJWT | 2.12+ |
| Password Hashing | Passlib + bcrypt | 1.7+ |
| Data Validation | Pydantic | 2.8+ |
| Deployment | Railway | Latest |

---

## 2. Component Descriptions

### HybridManagerSystem (Facade)

The top-level facade that wires together all subsystems. Provides a single initialization point and exposes the dashboard and health check methods.

**Responsibilities:**
- Lazy initialization of all subsystems
- MongoDB connection management
- Index creation on startup
- Dashboard aggregation
- Health check coordination

**Key Methods:**
- `initialize()` — Boot all subsystems, create indexes, load risk config
- `get_dashboard()` — Aggregate real-time dashboard data
- `get_system_health()` — DB ping + signal/alert counts

### MultiTierApprovalWorkflow

Manages the 6-stage signal lifecycle with role-based gates at each transition.

**Responsibilities:**
- Signal submission and lifecycle management
- Stage transition validation (correct role, correct current state)
- Automatic expiry of stale signals
- Stage history tracking
- Integration with RiskManagementEngine at Stage 4

**State Machine:**
```
PENDING → RECOMMENDED → APPROVED → ACTIVE → EXECUTED → CLOSED
   ↓           ↓            ↓          ↓
REJECTED    REJECTED    REJECTED   REJECTED
                                      ↓
                                   EXPIRED
```

**Key Design Decisions:**
- Each transition is atomic (MongoDB update_one)
- Stage history is append-only (MongoDB $push)
- Expiry is handled by a background task, not a timer
- Risk validation is delegated to RiskManagementEngine

### RiskManagementEngine

Institutional-grade risk controls with 7 automated checks.

**Responsibilities:**
- Running the 7-check validation suite
- Loading/saving risk configuration from MongoDB
- Providing the risk dashboard
- Categorizing symbols by asset class

**Check Pipeline:**
```
Signal Input
    │
    ├─ 1. R:R Ratio Check (Gold: 1.8, Others: 1.5)
    ├─ 2. Position Size Check (max 2% per trade)
    ├─ 3. Daily Drawdown Check (max 3%)
    ├─ 4. Asset Class Exposure Check
    ├─ 5. Correlation Check (max 3 correlated)
    ├─ 6. Portfolio Heat Check (max 8% total)
    └─ 7. Position Limits Check (max 10 total, 2 per pair)
         │
         ▼
    Risk Score (0-100) + Approved/Rejected
```

**Configuration Storage:**
Risk config is stored in `hybrid_risk_config` collection with `active: true` flag. Changes via the API update both the in-memory config and the database record.

### PerformanceAnalytics

MongoDB aggregation pipeline-based analytics engine.

**Responsibilities:**
- Manager performance KPIs
- Signal quality metrics and outcome correlation
- P&L attribution across multiple dimensions
- Approval funnel analysis

**Aggregation Strategy:**
All analytics use MongoDB's `$facet` aggregation stage to compute multiple metrics in a single database round-trip. This minimizes latency for dashboard queries.

### ComplianceAuditLog

Immutable, append-only audit trail for all system decisions.

**Responsibilities:**
- Recording every action with full context
- Compliance report generation
- Decision trail retrieval per signal

**Immutability Guarantee:**
Records are never updated or deleted. The `immutable: true` field is a marker for external audit tools. MongoDB collection-level write concerns ensure durability.

### TeamCollaborationEngine

Lightweight collaboration layer for signal discussion and team notes.

**Responsibilities:**
- Threaded comments on signals
- Standalone team notes
- Team activity feed (combines comments + audit log)

### AlertingSystem

Real-time alerting with severity levels and auto-resolution.

**Responsibilities:**
- Creating alerts (manual and system-generated)
- Alert resolution and acknowledgment
- Alert summary aggregation
- Auto-resolution of time-bounded alerts

---

## 3. Data Flow Diagrams

### Signal Submission Flow

```
Client Request
    │
    ▼
hybrid_manager_api.py
    │ POST /api/hybrid/signals/submit
    │ Validates JWT → get_current_hybrid_manager()
    │ Validates request body (Pydantic)
    ▼
MultiTierApprovalWorkflow.submit_signal()
    │ Generates signal_id (UUID)
    │ Sets status = PENDING
    │ Sets expires_at = now + 4h
    ▼
MongoDB: hybrid_signals.insert_one()
    │
    ▼
ComplianceAuditLog.record()
    │ action = "signal:submit"
    ▼
MongoDB: hybrid_audit_log.insert_one()
    │
    ▼
Response: { success, signal_id, signal }
```

### Risk Validation Flow

```
Client Request
    │
    ▼
hybrid_manager_api.py
    │ POST /api/hybrid/workflow/validate-risk
    │ Validates JWT → RISK_MANAGER role required
    ▼
MultiTierApprovalWorkflow.risk_manager_validate()
    │ Fetches signal from MongoDB
    │ Checks status == APPROVED
    ▼
RiskManagementEngine.validate_signal()
    │
    ├─ _check_rr_ratio()          → MongoDB: none (in-memory)
    ├─ _check_position_size()     → MongoDB: none (in-memory)
    ├─ _check_drawdown()          → MongoDB: hybrid_signals aggregate
    ├─ _check_exposure()          → MongoDB: hybrid_signals count
    ├─ _check_correlation()       → MongoDB: hybrid_signals count
    ├─ _check_portfolio_heat()    → MongoDB: hybrid_signals aggregate
    └─ _check_position_limits()   → MongoDB: hybrid_signals count
    │
    ▼
Risk Score Calculation
    │
    ├─ If approved (or override):
    │   MongoDB: hybrid_signals.update_one(status=ACTIVE)
    │   ComplianceAuditLog.record(signal:validate_risk)
    │   Response: { success, ACTIVE, risk_result }
    │
    └─ If rejected (no override):
        MongoDB: hybrid_signals.update_one(status=REJECTED)
        ComplianceAuditLog.record(signal:reject)
        Response: { success=false, risk_result, REJECTED }
```

### Dashboard Data Flow

```
Client Request
    │
    ▼
hybrid_manager_api.py
    │ GET /api/hybrid/dashboard
    │ Validates JWT
    ▼
HybridManagerSystem.get_dashboard()
    │
    ├─ MongoDB: hybrid_signals aggregate (status counts)
    ├─ RiskManagementEngine.get_risk_dashboard()
    │   ├─ MongoDB: hybrid_signals aggregate (drawdown)
    │   ├─ MongoDB: hybrid_signals count (active positions)
    │   ├─ MongoDB: hybrid_signals aggregate (portfolio heat)
    │   └─ MongoDB: hybrid_signals aggregate (weekly P&L)
    ├─ AlertingSystem.get_alert_summary()
    │   └─ MongoDB: hybrid_alerts aggregate
    └─ MongoDB: hybrid_audit_log count (recent activity)
    │
    ▼
Response: Aggregated dashboard object
```

---

## 4. Database Schema

### Collection: hybrid_signals

Primary collection for the signal lifecycle.

```javascript
{
  // Identity
  signal_id:        String (UUID, unique index),
  status:           String (PENDING|RECOMMENDED|APPROVED|ACTIVE|EXECUTED|CLOSED|REJECTED|EXPIRED),
  
  // Signal data
  symbol:           String (e.g. "XAUUSD"),
  symbol_category:  String (gold|forex|crypto|jpy|usd),
  direction:        String (BUY|SELL),
  entry_price:      Number,
  stop_loss:        Number,
  take_profit:      Number,
  tp1:              Number,
  tp2:              Number,
  tp3:              Number,
  risk_pct:         Number,
  confidence:       Number (0-100),
  strategy:         String,
  timeframe:        String,
  regime:           String,
  source:           String,
  raw_signal:       Object (original ML engine output),
  
  // Submission
  submitted_by:     String (manager_id),
  submitted_at:     Date,
  expires_at:       Date,
  
  // Stage 2: Analyst
  analyst_id:       String,
  quality_score:    Number (0-100),
  analyst_review:   Object { notes, quality_score, reviewed_at, reviewer },
  analyst_adjustments: Object,
  recommended_at:   Date,
  
  // Stage 3: Trading Manager
  trading_manager_id:  String,
  trading_approval:    Object { rationale, priority, approved_at, approver },
  trading_adjustments: Object,
  approved_at:         Date,
  priority:            String (LOW|NORMAL|HIGH|URGENT),
  
  // Stage 4: Risk Manager
  risk_manager_id:  String,
  risk_validation:  Object { approved, risk_score, checks[], override_reason, validated_at },
  activated_at:     Date,
  
  // Stage 5: Operator
  operator_id:      String,
  execution_details: Object { actual_entry, lot_size, broker_ref, slippage, executed_at },
  executed_at:      Date,
  
  // Stage 6: Closed
  close_price:      Number,
  pnl_usd:          Number,
  outcome:          String (WIN|LOSS|BREAKEVEN),
  close_reason:     String (TP_HIT|SL_HIT|MANUAL|PARTIAL|TRAILING_STOP),
  closed_at:        Date,
  closed_by:        String,
  
  // Rejection
  rejection_reason: String,
  rejected_by:      String,
  rejected_at:      Date,
  
  // Workflow tracking
  stage_history:    Array[{ stage, actor, role, timestamp, ...stage_specific_fields }],
  comments:         Array (populated on read, not stored here),
  tags:             Array[String],
  
  // Adjustments
  last_adjusted_at: Date,
  last_adjusted_by: String,
}
```

**Indexes:**
- `signal_id` (unique)
- `(status, submitted_at)` (compound, descending)
- `(symbol, status)` (compound)
- `analyst_id`
- `trading_manager_id`
- `risk_manager_id`
- `submitted_at` (descending)
- `closed_at` (descending)

### Collection: hybrid_audit_log

Immutable audit trail. Never updated, only inserted.

```javascript
{
  audit_id:     String (UUID, unique index),
  timestamp:    Date,
  action:       String (e.g. "signal:approve"),
  performed_by: String (manager_id or "SYSTEM"),
  role:         String,
  details:      Object (action-specific data),
  success:      Boolean,
  error:        String (null on success),
  signal_id:    String (null for non-signal actions),
  ip_address:   String,
  rationale:    String,
  immutable:    Boolean (always true),
}
```

**Indexes:**
- `audit_id` (unique)
- `timestamp` (descending)
- `(performed_by, timestamp)` (compound, descending)
- `signal_id`
- `action`

### Collection: hybrid_alerts

System and manual alerts.

```javascript
{
  alert_id:         String (UUID, unique index),
  title:            String,
  message:          String,
  severity:         String (INFO|WARNING|CRITICAL),
  category:         String (RISK|PERFORMANCE|COMPLIANCE|TRADING|SYSTEM|GENERAL),
  signal_id:        String (optional),
  created_by:       String (manager_id or "SYSTEM"),
  created_at:       Date,
  resolved:         Boolean,
  resolved_by:      String,
  resolved_at:      Date,
  resolution_note:  String,
  auto_resolve_at:  Date (optional),
  metadata:         Object,
  acknowledged_by:  Array[{ manager_id, acknowledged_at }],
}
```

**Indexes:**
- `alert_id` (unique)
- `(resolved, created_at)` (compound, descending)
- `(severity, resolved)` (compound)

### Collection: hybrid_managers

Hybrid manager accounts (separate from legacy system_managers).

```javascript
{
  manager_id:    String (UUID),
  email:         String (unique),
  full_name:     String,
  role:          String (HybridManagerRole),
  department:    String,
  password_hash: String (bcrypt, excluded from reads),
  is_active:     Boolean,
  created_at:    Date,
  created_by:    String,
  last_login:    Date,
  updated_at:    Date,
  updated_by:    String,
  deactivated_at: Date,
  deactivated_by: String,
  metadata:      Object,
}
```

### Collection: hybrid_comments

Signal discussion threads.

```javascript
{
  comment_id:  String (UUID),
  signal_id:   String,
  author_id:   String,
  author_name: String,
  author_role: String,
  text:        String,
  type:        String (GENERAL|ANALYSIS|RISK|DECISION),
  mentions:    Array[String] (manager_ids),
  is_private:  Boolean,
  created_at:  Date,
  edited:      Boolean,
}
```

**Index:** `(signal_id, created_at)` (compound, ascending)

### Collection: hybrid_notes

Standalone team notes.

```javascript
{
  note_id:     String (UUID),
  signal_id:   String (optional),
  author_id:   String,
  author_name: String,
  author_role: String,
  title:       String,
  content:     String,
  type:        String (GENERAL|MARKET|RISK|STRATEGY),
  tags:        Array[String],
  created_at:  Date,
  updated_at:  Date,
}
```

**Index:** `(author_id, created_at)` (compound, descending)

### Collection: hybrid_risk_config

Runtime risk configuration (single active document).

```javascript
{
  active:                    Boolean (true for the active config),
  max_positions_total:       Number,
  max_positions_per_pair:    Number,
  max_position_size_pct:     Number,
  daily_drawdown_limit_pct:  Number,
  weekly_drawdown_limit_pct: Number,
  monthly_drawdown_cap_pct:  Number,
  min_rr_ratio:              Number,
  min_rr_gold:               Number,
  max_risk_per_trade_pct:    Number,
  max_gold_exposure_pct:     Number,
  max_forex_exposure_pct:    Number,
  max_crypto_exposure_pct:   Number,
  max_portfolio_heat_pct:    Number,
  max_correlated_positions:  Number,
  updated_at:                Date,
  updated_by:                String,
}
```

---

## 5. Integration Points

### Integration with Legacy System Manager

The Hybrid Manager API accepts JWT tokens from both the legacy `/api/manager/auth/login` and the new `/api/hybrid/auth/login`. The `get_current_hybrid_manager()` dependency checks `hybrid_managers` first, then falls back to `system_managers`.

**Role Mapping:**
| Legacy Role | Hybrid Role |
|---|---|
| ADMIN | SUPER_ADMIN |
| MANAGER | TRADING_MANAGER |
| VIEWER | VIEWER |

### Integration with ML Signal Engine

The ML engine submits signals to the hybrid workflow via `MultiTierApprovalWorkflow.submit_signal()`. The `raw_signal` field preserves the complete ML engine output for reference.

**Submission Pattern:**
```python
from ml_engine.hybrid_manager_system import hybrid_manager_system

# In the signal generation loop:
await hybrid_manager_system.workflow.submit_signal(
    signal_data={
        "symbol":      "XAUUSD",
        "direction":   "BUY",
        "entry_price": 2650.00,
        "stop_loss":   2640.00,
        "take_profit": 2680.00,
        "confidence":  78.5,
        "strategy":    "SMC_ICT",
        "timeframe":   "4H",
        "regime":      "TREND_UP",
        "source":      "ML_ENGINE",
        # ... full ML output
    },
    submitted_by="SYSTEM",
)
```

### Integration with Notification Service

The AlertingSystem creates alerts in MongoDB. To push these to Telegram or push notifications, integrate with the existing `notification_service.py`:

```python
# In AlertingSystem.create_alert() or create_system_alert():
from notification_service import get_push_service
push_service = get_push_service()
if push_service and severity == "CRITICAL":
    await push_service.send_alert(title, message)
```

### Integration with server.py

The hybrid manager router is registered in `server.py` alongside the existing routers:

```python
# In server.py (already added):
try:
    from hybrid_manager_api import router as hybrid_router
    app.include_router(hybrid_router)
    logger.info("✅ Hybrid Manager API registered at /api/hybrid")
except Exception as _hybrid_err:
    logger.warning(f"⚠️ Hybrid Manager API not loaded: {_hybrid_err}")
```

---

## 6. Security Model

### Authentication

**JWT Token Structure:**
```json
{
  "sub":       "manager_uuid",
  "role":      "TRADING_MANAGER",
  "type":      "hybrid_manager",
  "exp":       1705312800,
  "issued_at": "2024-01-15T10:00:00"
}
```

**Token Validation:**
1. Decode JWT with `JWT_SECRET` and `JWT_ALGORITHM`
2. Verify `type` is `hybrid_manager` or `manager`
3. Look up manager in `hybrid_managers` (then `system_managers`)
4. Verify `is_active: true`

**Token Expiry:** Configurable via `JWT_EXPIRATION_HOURS` env var (default: 24h)

### Authorization

Every endpoint calls `check_hybrid_permission(manager, action)` which:
1. Extracts the role from the manager document
2. Looks up the role in `HYBRID_ROLE_PERMISSIONS`
3. Raises `PermissionError` if the action is not in the role's permission set
4. The API layer catches `PermissionError` and returns HTTP 403

### Password Security

- Passwords are hashed with bcrypt (cost factor 12)
- Password hashes are never returned in API responses (`{"password_hash": 0}` projection)
- No password reset endpoint (must be done by SUPER_ADMIN via direct DB update)

### Audit Trail Security

- Audit records are append-only (no update or delete operations)
- The `immutable: true` field marks records for external audit tools
- All actions are recorded regardless of success/failure
- IP addresses are captured when available (passed from request context)

### Environment Variables

| Variable | Description | Default |
|---|---|---|
| `JWT_SECRET` | JWT signing secret | `your-secret-key` (CHANGE IN PRODUCTION) |
| `JWT_ALGORITHM` | JWT algorithm | `HS256` |
| `JWT_EXPIRATION_HOURS` | Token expiry | `24` |
| `MONGO_URL` | MongoDB connection string | `mongodb://localhost:27017` |
| `DB_NAME` | Database name | `gold_signals_v3` |
| `DEFAULT_ACCOUNT_BALANCE` | Account balance for drawdown % | `100000` |

**Critical:** Change `JWT_SECRET` to a cryptographically random value in production:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## 7. Scalability Considerations

### Current Architecture (Single Instance)

The current architecture is designed for a single Railway service instance. All state is in MongoDB, so horizontal scaling is possible with minor changes.

### MongoDB Optimization

**Indexes:** All critical query patterns have compound indexes. The `_create_indexes()` method runs on startup and is idempotent.

**Aggregation Performance:**
- Analytics queries use `$facet` to minimize round-trips
- Large date ranges (365 days) may be slow with many signals
- Consider adding a `$limit` stage before `$facet` for very large datasets

**Connection Pooling:**
Motor (async MongoDB driver) handles connection pooling automatically. The default pool size is 100 connections, which is sufficient for a single Railway instance.

### Horizontal Scaling

To scale to multiple instances:

1. **Shared MongoDB**: All instances connect to the same MongoDB cluster (already the case)
2. **JWT Statelessness**: JWT tokens are stateless — any instance can validate them
3. **No In-Memory State**: The `RiskManagementEngine.config` is loaded from MongoDB on startup. For multi-instance deployments, use `await risk.load_config()` before each risk validation to ensure fresh config.

### Caching Strategy

For high-traffic deployments, consider caching:
- Risk configuration (changes rarely) — cache for 60 seconds
- Manager profiles (changes rarely) — cache for 5 minutes
- Dashboard data (changes frequently) — cache for 10 seconds

Redis caching can be added using the existing `REDIS_URL` environment variable.

### Database Scaling

For production with high signal volume:
- Use MongoDB Atlas M10+ cluster with replica set
- Enable MongoDB Atlas Search for full-text search on comments/notes
- Consider time-series collections for the audit log (MongoDB 5.0+)
- Archive closed signals older than 90 days to a separate collection

---

## 8. Deployment Architecture

### Railway Deployment

The Hybrid Manager System is deployed as part of the existing `serene-growth` Railway service. No additional services are required.

**Service Configuration:**
```
Service: serene-growth
Runtime: Python 3.11
Entry: uvicorn server:app
Port: $PORT (Railway-assigned)
```

**Required Environment Variables:**
```
MONGO_URL=mongodb+srv://...
DB_NAME=gold_signals_v3
JWT_SECRET=<cryptographically-random-32-byte-hex>
JWT_ALGORITHM=HS256
JWT_EXPIRATION_HOURS=24
DEFAULT_ACCOUNT_BALANCE=100000
```

### Startup Sequence

```
1. server.py loads
2. FastAPI app created
3. Existing routers registered (/api/manager, /api/manager/signals)
4. hybrid_manager_api.py router registered (/api/hybrid)
5. First request to /api/hybrid/* triggers _ensure_initialized()
6. HybridManagerSystem.initialize() called:
   a. MongoDB connection established
   b. ComplianceAuditLog initialized
   c. RiskManagementEngine initialized
   d. MultiTierApprovalWorkflow initialized
   e. PerformanceAnalytics initialized
   f. TeamCollaborationEngine initialized
   g. AlertingSystem initialized
   h. MongoDB indexes created (idempotent)
   i. Risk config loaded from DB
7. System ready
```

### Health Check Endpoint

```
GET /api/hybrid/dashboard/health
```

Returns:
```json
{
  "status": "HEALTHY",
  "db": "HEALTHY",
  "signal_count": 1247,
  "active_alerts": 2,
  "timestamp": "2024-01-15T10:30:00Z"
}
```

Use this endpoint for Railway health checks and uptime monitoring.

---

## 9. Monitoring & Observability

### Logging

All components use Python's standard `logging` module with the `logging.getLogger(__name__)` pattern. Log levels:

- `INFO`: Normal operations (signal submitted, approved, closed)
- `WARNING`: Alerts created, risk checks failed
- `ERROR`: Database errors, unexpected exceptions

### Key Log Messages

```
✅ HybridManagerSystem initialized
✅ HybridManagerSystem DB indexes created
✅ Signal submitted: {signal_id} ({symbol} {direction})
✅ Signal {signal_id} recommended by analyst {manager_id} (score: {score})
✅ Signal {signal_id} approved by trading manager {manager_id}
✅ Signal {signal_id} risk-validated and ACTIVE (score: {risk_score})
✅ Signal {signal_id} executed at {price}
✅ Signal {signal_id} closed: {outcome} ${pnl}
🚨 Alert [{severity}] {title}: {message}
```

### Metrics to Monitor

| Metric | Source | Alert Threshold |
|---|---|---|
| Signals in PENDING > 2h | hybrid_signals | > 5 signals |
| Daily drawdown | hybrid_signals aggregate | > 2.5% |
| Active alerts (CRITICAL) | hybrid_alerts | > 0 |
| Audit log write failures | Python logs | Any |
| API response time | Railway metrics | > 2s p95 |
| MongoDB connection errors | Python logs | Any |

### Dashboard Monitoring

The real-time dashboard at `GET /api/hybrid/dashboard` provides a comprehensive snapshot. For automated monitoring, poll `GET /api/hybrid/dashboard/realtime` every 30 seconds.

---

## 10. Disaster Recovery

### Data Backup

The `hybrid_*` collections should be included in the existing backup strategy. Critical collections by priority:

1. `hybrid_audit_log` — Compliance data, must never be lost
2. `hybrid_signals` — Signal history and P&L records
3. `hybrid_managers` — Manager accounts
4. `hybrid_risk_config` — Risk configuration

### Recovery Procedures

**Scenario 1: Risk config lost**
The `RiskManagementEngine` has hardcoded defaults in `DEFAULT_CONFIG`. If the `hybrid_risk_config` collection is empty, the system falls back to defaults automatically. Re-configure via `PUT /api/hybrid/risk/config`.

**Scenario 2: Manager accounts lost**
SUPER_ADMIN accounts must be recreated via direct MongoDB insert (since there's no unauthenticated account creation endpoint). Use the existing `system_managers` collection as a fallback — the hybrid API accepts both.

**Scenario 3: Signal data corrupted**
Signals in terminal states (CLOSED, REJECTED, EXPIRED) are historical records. Active signals (PENDING through EXECUTED) may need manual status correction via direct MongoDB update. Always audit any manual corrections.

**Scenario 4: Audit log corruption**
The audit log is append-only. If records are missing, they cannot be reconstructed. Ensure MongoDB Atlas continuous backup is enabled for the `hybrid_audit_log` collection.

### Rollback Strategy

The Hybrid Manager System is additive — it adds new collections and a new API router without modifying existing collections or endpoints. Rolling back means:

1. Remove the `hybrid_manager_api.py` router registration from `server.py`
2. The existing `/api/manager` and `/api/manager/signals` endpoints continue to work
3. The `hybrid_*` MongoDB collections remain but are no longer accessed

This makes rollback safe and non-destructive.

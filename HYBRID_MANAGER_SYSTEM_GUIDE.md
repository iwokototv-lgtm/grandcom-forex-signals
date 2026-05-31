# Hybrid Manager System — Complete Guide
## Gold Trading System v3.0.2 | Enterprise-Grade Multi-Tier Approval Workflow

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Role Definitions & Permissions Matrix](#2-role-definitions--permissions-matrix)
3. [Multi-Tier Approval Workflow](#3-multi-tier-approval-workflow)
4. [Risk Management Controls](#4-risk-management-controls)
5. [Performance Analytics & Reporting](#5-performance-analytics--reporting)
6. [Compliance & Audit Logging](#6-compliance--audit-logging)
7. [Team Collaboration Features](#7-team-collaboration-features)
8. [Alerting System](#8-alerting-system)
9. [API Reference — All 50+ Endpoints](#9-api-reference--all-50-endpoints)
10. [Common Workflows with curl Examples](#10-common-workflows-with-curl-examples)
11. [Best Practices](#11-best-practices)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. System Overview

The Hybrid Manager System is an enterprise-grade signal management platform built on top of the Gold Trading System v3.0.2. It replaces the basic 3-role approval system with a comprehensive 6-role, 6-stage workflow that enforces institutional-grade risk controls, compliance logging, and team collaboration.

### Key Capabilities

| Capability | Description |
|---|---|
| Multi-tier approval | 6-stage signal lifecycle (PENDING → RECOMMENDED → APPROVED → ACTIVE → EXECUTED → CLOSED) |
| Role-based access | 6 roles with granular permission enforcement |
| Risk management | Position limits, drawdown controls, correlation checks, portfolio heat |
| Performance analytics | Manager KPIs, signal quality, P&L attribution, win rate |
| Compliance | Immutable audit trail, compliance reports, decision tracking |
| Team collaboration | Comments, notes, @mentions, activity feed |
| Alerting | Real-time risk, performance, compliance, and system alerts |
| Dashboard | Real-time monitoring of the entire signal pipeline |

### Base URL

All hybrid manager endpoints are mounted at:

```
https://your-service.railway.app/api/hybrid
```

### Authentication

All endpoints require a Bearer JWT token obtained from `/api/hybrid/auth/login`.

```
Authorization: Bearer <your_jwt_token>
```

---

## 2. Role Definitions & Permissions Matrix

### Role Hierarchy

```
SUPER_ADMIN
    │
    ├── RISK_MANAGER
    │       └── Risk validation, limits, drawdown, compliance
    │
    ├── TRADING_MANAGER
    │       └── Signal approval, trading decisions, priority
    │
    ├── ANALYST
    │       └── Signal review, quality scoring, analysis
    │
    ├── OPERATOR
    │       └── Signal execution, trade monitoring, P&L
    │
    └── VIEWER
            └── Read-only dashboard access
```

### Role Descriptions

#### SUPER_ADMIN
Full system control. Can perform all operations including manager CRUD, risk overrides, compliance exports, and system configuration. Typically assigned to the system owner or CTO.

#### RISK_MANAGER
Responsible for the final risk gate before signals go live. Can set position limits, drawdown thresholds, and override risk checks with documented rationale. Has full compliance and audit access.

#### TRADING_MANAGER
Reviews analyst-recommended signals and makes trading decisions. Can approve, reject, or adjust signals. Has access to performance analytics and P&L reporting.

#### ANALYST
First human reviewer in the workflow. Reviews ML-generated signals, assigns quality scores (0-100), and provides analysis notes. Can make minor price adjustments.

#### OPERATOR
Confirms signal execution with actual entry prices and lot sizes. Records trade closures with P&L. Monitors active positions.

#### VIEWER
Read-only access to dashboards, signal history, and analytics. Cannot take any action on signals.

### Permissions Matrix

| Permission | SUPER_ADMIN | RISK_MANAGER | TRADING_MANAGER | ANALYST | OPERATOR | VIEWER |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| manager:add | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| manager:remove | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| manager:update | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| manager:list | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| signal:view | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| signal:recommend | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ |
| signal:approve | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| signal:validate_risk | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| signal:execute | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ |
| signal:close | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ |
| signal:reject | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| signal:adjust | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ |
| risk:view | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| risk:set_limits | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| risk:override | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| analytics:view | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| analytics:export | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| compliance:report | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| alert:create | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| alert:resolve | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| alert:configure | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| dashboard:realtime | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| system:deploy | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |

---

## 3. Multi-Tier Approval Workflow

### Signal Lifecycle Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SIGNAL LIFECYCLE                                  │
│                                                                      │
│  ML Engine / Manual                                                  │
│       │                                                              │
│       ▼                                                              │
│  ┌─────────┐    Analyst Reviews        ┌─────────────┐              │
│  │ PENDING │ ─────────────────────────▶│ RECOMMENDED │              │
│  └─────────┘    + Quality Score        └─────────────┘              │
│       │         (0-100)                       │                      │
│       │                                       │ Trading Manager      │
│       │                                       ▼ Approves            │
│       │                               ┌──────────┐                  │
│       │                               │ APPROVED │                  │
│       │                               └──────────┘                  │
│       │                                       │                      │
│       │                                       │ Risk Manager         │
│       │                                       ▼ Validates           │
│       │                               ┌────────┐                    │
│       │                               │ ACTIVE │ ──▶ Trading        │
│       │                               └────────┘                    │
│       │                                       │                      │
│       │                                       │ Operator             │
│       │                                       ▼ Executes            │
│       │                               ┌──────────┐                  │
│       │                               │ EXECUTED │                  │
│       │                               └──────────┘                  │
│       │                                       │                      │
│       │                                       │ System / Operator    │
│       │                                       ▼ Closes              │
│       │                               ┌────────┐                    │
│       │                               │ CLOSED │ + P&L              │
│       │                               └────────┘                    │
│       │                                                              │
│       └──────────────────────────────────────────────────────────▶  │
│                    REJECTED (any stage) / EXPIRED (timeout)          │
└─────────────────────────────────────────────────────────────────────┘
```

### Stage Details

#### Stage 1: PENDING
- **Trigger**: Signal generated by ML engine or submitted manually
- **Actor**: System / SUPER_ADMIN
- **Duration**: Up to 4 hours before auto-expiry
- **Data captured**: Symbol, direction, entry/SL/TP, confidence, strategy, timeframe

#### Stage 2: RECOMMENDED
- **Trigger**: Analyst reviews and recommends
- **Actor**: ANALYST (or higher)
- **Required**: Quality score (0-100), review notes (min 10 chars)
- **Optional**: Price adjustments (entry, SL, TP)
- **Data captured**: Quality score, analyst notes, adjustments

#### Stage 3: APPROVED
- **Trigger**: Trading Manager approves
- **Actor**: TRADING_MANAGER (or higher)
- **Required**: Trading rationale (min 10 chars), priority (LOW/NORMAL/HIGH/URGENT)
- **Optional**: Price adjustments
- **Data captured**: Rationale, priority, trading manager ID

#### Stage 4: ACTIVE
- **Trigger**: Risk Manager validates
- **Actor**: RISK_MANAGER (or higher)
- **Process**: Runs 7 automated risk checks (R:R, position size, drawdown, exposure, correlation, portfolio heat, position limits)
- **Override**: Risk Manager can override failed checks with documented reason
- **Data captured**: Risk score, check results, override reason

#### Stage 5: EXECUTED
- **Trigger**: Operator confirms execution
- **Actor**: OPERATOR (or higher)
- **Required**: Actual entry price, lot size
- **Optional**: Broker reference number
- **Data captured**: Actual entry, slippage, lot size, broker ref

#### Stage 6: CLOSED
- **Trigger**: Trade closes (TP hit, SL hit, manual close)
- **Actor**: OPERATOR (or higher)
- **Required**: Close price, P&L in USD, close reason
- **Data captured**: Close price, P&L, outcome (WIN/LOSS/BREAKEVEN)

### Rejection Rules

Any authorized role can reject a signal at any non-terminal stage. Rejection requires a mandatory reason (min 10 chars). Rejected signals are permanently closed and cannot be reactivated.

### Expiry Rules

Signals in PENDING or RECOMMENDED state that have not progressed within 4 hours are automatically expired by the background task. Run `/api/hybrid/workflow/expire-stale` to trigger manually.

---

## 4. Risk Management Controls

### Automated Risk Checks (Stage 4)

The Risk Management Engine runs 7 checks before activating a signal:

#### Check 1: R:R Ratio
- **Gold pairs (XAUUSD, XAUEUR)**: Minimum R:R of 1.8
- **All other pairs**: Minimum R:R of 1.5
- **Formula**: `R:R = |TP - Entry| / |Entry - SL|`

#### Check 2: Position Size
- Maximum risk per trade: 2% of account balance
- Configurable via `/api/hybrid/risk/config`

#### Check 3: Daily Drawdown
- Default limit: 3% daily drawdown
- Calculated from all closed trades in the last 24 hours
- Trading halted when limit is reached

#### Check 4: Asset Class Exposure
- Gold pairs: Max 30% exposure
- Forex pairs: Max 40% exposure
- Crypto: Max 15% exposure
- Configurable per asset class

#### Check 5: Correlation Check
- Gold pairs (XAUUSD, XAUEUR, XAUGBP) are treated as correlated
- Maximum 3 correlated positions simultaneously

#### Check 6: Portfolio Heat
- Total risk across all open trades: Max 8%
- Prevents over-leveraging the portfolio

#### Check 7: Position Limits
- Total positions: Max 10 simultaneously
- Per-pair positions: Max 2 per symbol

### Risk Configuration

All risk parameters are configurable at runtime via the API. Changes take effect immediately and are persisted to MongoDB.

```json
{
  "max_positions_total": 10,
  "max_positions_per_pair": 2,
  "daily_drawdown_limit_pct": 3.0,
  "weekly_drawdown_limit_pct": 6.0,
  "monthly_drawdown_cap_pct": 12.0,
  "min_rr_ratio": 1.5,
  "min_rr_gold": 1.8,
  "max_risk_per_trade_pct": 2.0,
  "max_gold_exposure_pct": 30.0,
  "max_forex_exposure_pct": 40.0,
  "max_portfolio_heat_pct": 8.0,
  "max_correlated_positions": 3
}
```

### Risk Override

Risk Managers can override failed risk checks by providing an `override_reason`. All overrides are logged in the immutable audit trail with the manager's identity and rationale.

---

## 5. Performance Analytics & Reporting

### Manager Performance Metrics

For each manager role, the system tracks:

| Metric | Description |
|---|---|
| Signals reviewed | Total signals processed |
| Approval rate | % of signals approved vs rejected |
| Average quality score | Mean quality score assigned (Analysts) |
| Win rate | % of closed trades that were profitable |
| Total P&L | Cumulative P&L from approved signals |
| Average risk score | Mean risk score from validations (Risk Managers) |

### Signal Quality Metrics

Quality scores (0-100) assigned by Analysts are tracked against outcomes:

- **Score 80-100**: High quality — expected win rate > 70%
- **Score 60-79**: Good quality — expected win rate > 55%
- **Score 40-59**: Moderate quality — expected win rate ~50%
- **Score 0-39**: Low quality — consider rejection

### P&L Attribution

P&L is attributed across multiple dimensions:
- By symbol (XAUUSD, XAUEUR, etc.)
- By strategy (SMC, Mean Reversion, Breakout, Hybrid)
- By timeframe (1H, 4H, 1D, 1W)
- By day (daily P&L chart)
- By manager (who approved the signal)

### Key Performance Indicators

| KPI | Formula |
|---|---|
| Win Rate | Wins / Total Closed × 100 |
| Profit Factor | (Wins × Avg Win) / (Losses × Avg Loss) |
| Expectancy | (Win Rate × Avg Win) + (Loss Rate × Avg Loss) |
| Approval Rate | Approved / Total Submitted × 100 |
| Quality-Outcome Correlation | Pearson correlation of quality score vs outcome |

---

## 6. Compliance & Audit Logging

### Audit Trail Design

Every action in the system is recorded in an immutable audit log (`hybrid_audit_log` collection). Records are append-only and include:

```json
{
  "audit_id": "uuid",
  "timestamp": "2024-01-15T10:30:00Z",
  "action": "signal:approve",
  "performed_by": "manager_uuid",
  "role": "TRADING_MANAGER",
  "details": { "signal_id": "...", "priority": "HIGH" },
  "success": true,
  "error": null,
  "signal_id": "signal_uuid",
  "ip_address": "192.168.1.1",
  "rationale": "Strong SMC setup with 4H confluence",
  "immutable": true
}
```

### Audited Actions

All of the following actions are automatically audited:
- `signal:submit`, `signal:recommend`, `signal:approve`, `signal:validate_risk`
- `signal:execute`, `signal:close`, `signal:reject`, `signal:adjust`
- `manager:add`, `manager:remove`, `manager:update`
- `alert:create`, `alert:resolve`
- `compliance:report`
- `collab:comment`
- `risk:set_limits`

### Compliance Reports

Generate compliance reports for any date range:

```bash
curl -X POST https://your-service.railway.app/api/hybrid/compliance/report \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2024-01-01T00:00:00Z",
    "end_date": "2024-01-31T23:59:59Z",
    "report_type": "full"
  }'
```

Report types:
- `full`: Complete breakdown by action, role, and manager
- `summary`: High-level statistics only
- `by_manager`: Actions grouped by manager
- `by_action`: Actions grouped by type

---

## 7. Team Collaboration Features

### Comments

Add threaded comments to any signal for team discussion:

```bash
curl -X POST https://your-service.railway.app/api/hybrid/collab/comments \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "signal_id": "signal_uuid",
    "text": "Strong SMC setup — order block at 2650 with 4H FVG confluence",
    "comment_type": "ANALYSIS",
    "mentions": ["manager_id_1"],
    "is_private": false
  }'
```

Comment types: `GENERAL`, `ANALYSIS`, `RISK`, `DECISION`

### Notes

Standalone notes for market observations, strategy notes, or team communications:

```bash
curl -X POST https://your-service.railway.app/api/hybrid/collab/notes \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "NFP Week Risk Reduction",
    "content": "Reducing position sizes by 50% during NFP week. High impact news expected Friday 13:30 UTC.",
    "note_type": "RISK",
    "tags": ["nfp", "risk-reduction", "news"]
  }'
```

### Team Activity Feed

Get a real-time feed of all team actions across signals:

```bash
curl https://your-service.railway.app/api/hybrid/collab/activity?hours=24 \
  -H "Authorization: Bearer $TOKEN"
```

---

## 8. Alerting System

### Alert Severity Levels

| Severity | Use Case | Example |
|---|---|---|
| INFO | Informational events | Signal submitted, manager logged in |
| WARNING | Attention required | Drawdown at 80% of limit, quality score below threshold |
| CRITICAL | Immediate action required | Drawdown limit breached, risk check failed |

### Alert Categories

| Category | Description |
|---|---|
| RISK | Drawdown breach, position limit, exposure limit |
| PERFORMANCE | Win rate drop, P&L threshold, quality score drop |
| COMPLIANCE | Unauthorized action attempt, audit anomaly |
| TRADING | Signal expiry, execution failure, slippage |
| SYSTEM | DB connectivity, API errors, service health |
| GENERAL | Manual alerts from managers |

### Creating Alerts

```bash
curl -X POST https://your-service.railway.app/api/hybrid/alerts \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Daily Drawdown Warning",
    "message": "Daily drawdown has reached 2.5% — approaching 3% limit",
    "severity": "WARNING",
    "category": "RISK",
    "auto_resolve_hours": 24
  }'
```

### Auto-Resolution

Alerts can be configured to auto-resolve after a specified number of hours. This is useful for time-bounded warnings (e.g., "high volatility expected during NFP — auto-resolves in 4 hours").

---

## 9. API Reference — All 50+ Endpoints

### Authentication Endpoints

| Method | Path | Description | Min Role |
|---|---|---|---|
| POST | `/api/hybrid/auth/login` | Login and get JWT | Public |
| GET | `/api/hybrid/auth/me` | Get current manager profile | Any |
| POST | `/api/hybrid/auth/refresh` | Refresh JWT token | Any |

### Manager Management Endpoints

| Method | Path | Description | Min Role |
|---|---|---|---|
| POST | `/api/hybrid/managers` | Add new manager | SUPER_ADMIN |
| GET | `/api/hybrid/managers` | List all managers | Any |
| GET | `/api/hybrid/managers/{id}` | Get manager by ID | Any |
| PUT | `/api/hybrid/managers/{id}` | Update manager | SUPER_ADMIN |
| DELETE | `/api/hybrid/managers/{id}` | Deactivate manager | SUPER_ADMIN |
| GET | `/api/hybrid/managers/{id}/performance` | Manager performance stats | TRADING_MANAGER+ |

### Signal Management Endpoints

| Method | Path | Description | Min Role |
|---|---|---|---|
| POST | `/api/hybrid/signals/submit` | Submit new signal | Any |
| GET | `/api/hybrid/signals/pending` | List pending signals | Any |
| GET | `/api/hybrid/signals` | List all signals with filters | Any |
| GET | `/api/hybrid/signals/{id}` | Get signal details | Any |
| GET | `/api/hybrid/signals/{id}/history` | Get workflow history | Any |
| PUT | `/api/hybrid/signals/{id}/adjust` | Adjust signal levels | ANALYST+ |

### Workflow Action Endpoints

| Method | Path | Description | Min Role |
|---|---|---|---|
| POST | `/api/hybrid/workflow/recommend` | Analyst recommends signal | ANALYST |
| POST | `/api/hybrid/workflow/approve` | Trading Manager approves | TRADING_MANAGER |
| POST | `/api/hybrid/workflow/validate-risk` | Risk Manager validates | RISK_MANAGER |
| POST | `/api/hybrid/workflow/execute` | Operator confirms execution | OPERATOR |
| POST | `/api/hybrid/workflow/close` | Close signal with P&L | OPERATOR |
| POST | `/api/hybrid/workflow/reject` | Reject signal | ANALYST+ |
| POST | `/api/hybrid/workflow/expire-stale` | Expire stale signals | RISK_MANAGER+ |
| GET | `/api/hybrid/workflow/stats` | Workflow statistics | Any |

### Risk Management Endpoints

| Method | Path | Description | Min Role |
|---|---|---|---|
| GET | `/api/hybrid/risk/dashboard` | Risk dashboard | Any |
| GET | `/api/hybrid/risk/config` | Get risk configuration | Any |
| PUT | `/api/hybrid/risk/config` | Update risk configuration | RISK_MANAGER |
| POST | `/api/hybrid/risk/validate-signal` | Validate signal risk | Any |
| GET | `/api/hybrid/risk/drawdown` | Current drawdown status | TRADING_MANAGER+ |
| GET | `/api/hybrid/risk/exposure` | Exposure by asset class | TRADING_MANAGER+ |
| GET | `/api/hybrid/risk/positions` | Active position summary | TRADING_MANAGER+ |

### Analytics Endpoints

| Method | Path | Description | Min Role |
|---|---|---|---|
| GET | `/api/hybrid/analytics/overview` | Performance overview | Any |
| GET | `/api/hybrid/analytics/signal-quality` | Signal quality metrics | Any |
| GET | `/api/hybrid/analytics/pnl` | P&L report | Any |
| GET | `/api/hybrid/analytics/approval-funnel` | Approval funnel analysis | Any |
| GET | `/api/hybrid/analytics/managers` | All managers performance | TRADING_MANAGER+ |
| GET | `/api/hybrid/analytics/export` | Export analytics data | RISK_MANAGER+ |

### Compliance Endpoints

| Method | Path | Description | Min Role |
|---|---|---|---|
| GET | `/api/hybrid/compliance/audit` | View audit trail | Any |
| POST | `/api/hybrid/compliance/report` | Generate compliance report | RISK_MANAGER+ |
| GET | `/api/hybrid/compliance/signal-decisions/{id}` | Signal decision trail | Any |
| GET | `/api/hybrid/compliance/summary` | Compliance summary | Any |

### Collaboration Endpoints

| Method | Path | Description | Min Role |
|---|---|---|---|
| POST | `/api/hybrid/collab/comments` | Add comment to signal | ANALYST+ |
| GET | `/api/hybrid/collab/comments/{signal_id}` | Get signal comments | Any |
| POST | `/api/hybrid/collab/notes` | Add team note | ANALYST+ |
| GET | `/api/hybrid/collab/notes` | Get team notes | Any |
| GET | `/api/hybrid/collab/activity` | Team activity feed | Any |

### Alert Endpoints

| Method | Path | Description | Min Role |
|---|---|---|---|
| POST | `/api/hybrid/alerts` | Create alert | TRADING_MANAGER+ |
| GET | `/api/hybrid/alerts` | List alerts | Any |
| GET | `/api/hybrid/alerts/summary` | Alert summary | Any |
| POST | `/api/hybrid/alerts/{id}/resolve` | Resolve alert | TRADING_MANAGER+ |
| POST | `/api/hybrid/alerts/{id}/acknowledge` | Acknowledge alert | Any |
| POST | `/api/hybrid/alerts/auto-resolve` | Auto-resolve expired | SUPER_ADMIN |

### Dashboard Endpoints

| Method | Path | Description | Min Role |
|---|---|---|---|
| GET | `/api/hybrid/dashboard` | Full monitoring dashboard | Any |
| GET | `/api/hybrid/dashboard/realtime` | Real-time pipeline status | TRADING_MANAGER+ |
| GET | `/api/hybrid/dashboard/health` | System health check | Any |
| GET | `/api/hybrid/dashboard/roles` | Role permissions reference | Any |

### System Endpoints

| Method | Path | Description | Min Role |
|---|---|---|---|
| GET | `/api/hybrid/system/status` | System status | Any |
| GET | `/api/hybrid/system/signal-stats` | Signal statistics | Any |

---

## 10. Common Workflows with curl Examples

### Workflow A: Complete Signal Lifecycle

**Step 1: Login as Analyst**
```bash
TOKEN=$(curl -s -X POST https://your-service.railway.app/api/hybrid/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "analyst@company.com", "password": "secure_password"}' \
  | jq -r '.access_token')
```

**Step 2: View pending signals**
```bash
curl https://your-service.railway.app/api/hybrid/signals/pending \
  -H "Authorization: Bearer $TOKEN"
```

**Step 3: Recommend a signal (Analyst)**
```bash
curl -X POST https://your-service.railway.app/api/hybrid/workflow/recommend \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "signal_id": "signal_uuid_here",
    "quality_score": 82,
    "review_notes": "Strong SMC setup with order block at 2650. 4H FVG confluence confirmed. Volume profile supports bullish bias. Risk/reward excellent at 2.3.",
    "adjustments": {
      "stop_loss": 2645.00
    }
  }'
```

**Step 4: Login as Trading Manager and approve**
```bash
TM_TOKEN=$(curl -s -X POST https://your-service.railway.app/api/hybrid/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "trading_manager@company.com", "password": "secure_password"}' \
  | jq -r '.access_token')

curl -X POST https://your-service.railway.app/api/hybrid/workflow/approve \
  -H "Authorization: Bearer $TM_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "signal_id": "signal_uuid_here",
    "rationale": "Confirmed 4H order block with strong institutional footprint. London session setup aligns with weekly bias. Approving with HIGH priority.",
    "priority": "HIGH"
  }'
```

**Step 5: Login as Risk Manager and validate**
```bash
RM_TOKEN=$(curl -s -X POST https://your-service.railway.app/api/hybrid/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "risk_manager@company.com", "password": "secure_password"}' \
  | jq -r '.access_token')

curl -X POST https://your-service.railway.app/api/hybrid/workflow/validate-risk \
  -H "Authorization: Bearer $RM_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "signal_id": "signal_uuid_here"
  }'
```

**Step 6: Login as Operator and execute**
```bash
OP_TOKEN=$(curl -s -X POST https://your-service.railway.app/api/hybrid/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "operator@company.com", "password": "secure_password"}' \
  | jq -r '.access_token')

curl -X POST https://your-service.railway.app/api/hybrid/workflow/execute \
  -H "Authorization: Bearer $OP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "signal_id": "signal_uuid_here",
    "actual_entry": 2651.50,
    "lot_size": 0.10,
    "broker_ref": "MT5-ORDER-12345",
    "notes": "Executed at market open. Slight positive slippage."
  }'
```

**Step 7: Close the signal with P&L**
```bash
curl -X POST https://your-service.railway.app/api/hybrid/workflow/close \
  -H "Authorization: Bearer $OP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "signal_id": "signal_uuid_here",
    "close_price": 2680.00,
    "pnl_usd": 285.00,
    "close_reason": "TP_HIT"
  }'
```

### Workflow B: Risk Override

When a signal fails risk checks but the Risk Manager wants to approve it anyway:

```bash
curl -X POST https://your-service.railway.app/api/hybrid/workflow/validate-risk \
  -H "Authorization: Bearer $RM_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "signal_id": "signal_uuid_here",
    "override_reason": "Portfolio heat check failed due to temporary spike. Underlying risk is acceptable. Correlation check override: XAUUSD position is hedging existing XAUEUR exposure. Approved with reduced lot size."
  }'
```

### Workflow C: Update Risk Limits

```bash
curl -X PUT https://your-service.railway.app/api/hybrid/risk/config \
  -H "Authorization: Bearer $RM_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "daily_drawdown_limit_pct": 2.5,
    "max_portfolio_heat_pct": 6.0,
    "max_positions_total": 8
  }'
```

### Workflow D: Generate Monthly Compliance Report

```bash
curl -X POST https://your-service.railway.app/api/hybrid/compliance/report \
  -H "Authorization: Bearer $RM_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2024-01-01T00:00:00Z",
    "end_date": "2024-01-31T23:59:59Z",
    "report_type": "full"
  }'
```

### Workflow E: Create a Risk Alert

```bash
curl -X POST https://your-service.railway.app/api/hybrid/alerts \
  -H "Authorization: Bearer $RM_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "NFP High Impact News",
    "message": "Non-Farm Payrolls release in 30 minutes. Suspending new signal approvals until 14:00 UTC.",
    "severity": "WARNING",
    "category": "RISK",
    "auto_resolve_hours": 2
  }'
```

---

## 11. Best Practices

### For Analysts

1. **Quality Score Calibration**: Use the full 0-100 range. Reserve 90+ for exceptional setups with multiple confluences. Scores below 50 should typically be rejected rather than recommended.

2. **Review Notes**: Always include the specific technical reasons for your recommendation. Reference the timeframe, key levels, and confluence factors. Minimum 10 characters is enforced but aim for 100+.

3. **Price Adjustments**: Only adjust prices when you have a specific technical reason. Document the reason in your review notes.

4. **Expiry Awareness**: Signals expire after 4 hours in PENDING state. Prioritize your review queue to avoid unnecessary expiries.

### For Trading Managers

1. **Priority Assignment**: Use URGENT only for time-sensitive setups (e.g., news-driven moves). HIGH for strong setups during active sessions. NORMAL for standard setups.

2. **Rationale Quality**: Your approval rationale is part of the compliance record. Be specific about why you're approving — reference the analyst's quality score, market context, and your trading thesis.

3. **Rejection Discipline**: Don't hesitate to reject signals that don't meet your standards. A 60% approval rate with high win rate is better than 90% approval with poor outcomes.

### For Risk Managers

1. **Override Documentation**: Risk overrides are the most scrutinized entries in the audit log. Always provide detailed, specific rationale. Vague overrides will be flagged in compliance reviews.

2. **Limit Calibration**: Review risk limits monthly against actual performance. Tighten limits during high-volatility periods (NFP week, FOMC, etc.).

3. **Drawdown Response**: When daily drawdown reaches 2%, proactively reduce position size limits. Don't wait for the 3% hard stop.

4. **Correlation Monitoring**: Check the exposure dashboard before validating gold signals. Multiple correlated positions amplify drawdown risk.

### For Operators

1. **Slippage Recording**: Always record the actual entry price, not the signal's entry price. Slippage data is used to improve signal quality scoring.

2. **Broker Reference**: Always include the broker order reference. This is essential for reconciliation and dispute resolution.

3. **Close Reason Accuracy**: Use the correct close reason (TP_HIT, SL_HIT, MANUAL, PARTIAL, TRAILING_STOP). This data drives performance attribution.

### For All Roles

1. **Comment Regularly**: Use the comment system to share observations. A well-documented signal thread is invaluable for post-trade analysis.

2. **Alert Acknowledgment**: Acknowledge alerts promptly even if you're not resolving them. This signals to the team that the alert has been seen.

3. **Dashboard Monitoring**: Check the real-time dashboard at the start of each trading session to understand the current pipeline state.

---

## 12. Troubleshooting

### Authentication Issues

**Problem**: `401 Token has expired`
**Solution**: Call `/api/hybrid/auth/refresh` to get a new token, or re-login via `/api/hybrid/auth/login`.

**Problem**: `401 Token is not a hybrid manager token`
**Solution**: Ensure you're using a token from `/api/hybrid/auth/login`, not from the legacy `/api/manager/auth/login`. Both token types are accepted by the hybrid API.

**Problem**: `401 Manager account not found or inactive`
**Solution**: The manager account may have been deactivated. Contact a SUPER_ADMIN to reactivate.

### Permission Issues

**Problem**: `403 Role 'ANALYST' does not have permission for action 'signal:approve'`
**Solution**: The action requires a higher role. Check the permissions matrix in Section 2. Contact a SUPER_ADMIN to update your role if needed.

### Workflow Issues

**Problem**: `400 Signal is RECOMMENDED, expected PENDING`
**Solution**: The signal has already been recommended. Check the signal's current status via `GET /api/hybrid/signals/{id}` and proceed to the next stage.

**Problem**: `400 Signal rejected due to risk validation failure`
**Solution**: The risk engine rejected the signal. The response includes a `risk_result` object with detailed check results. Either fix the signal parameters or use `override_reason` if you're a Risk Manager.

**Problem**: Signal stuck in PENDING for hours
**Solution**: Run `POST /api/hybrid/workflow/expire-stale` to expire stale signals, or check if analysts are logged in and reviewing their queue.

### Risk Configuration Issues

**Problem**: All signals failing the drawdown check
**Solution**: Check the current drawdown via `GET /api/hybrid/risk/drawdown`. If the daily limit has been reached, trading is suspended until the next day. Risk Managers can temporarily increase the limit via `PUT /api/hybrid/risk/config`.

**Problem**: Portfolio heat check always failing
**Solution**: Check active positions via `GET /api/hybrid/risk/positions`. Close some positions or increase `max_portfolio_heat_pct` in the risk config.

### Database Issues

**Problem**: Slow API responses
**Solution**: The system creates MongoDB indexes on initialization. If indexes are missing, restart the service to trigger re-initialization. Check `GET /api/hybrid/dashboard/health` for DB status.

**Problem**: `500 Internal Server Error` on signal submission
**Solution**: Check the system logs. Common causes: MongoDB connection timeout, invalid price levels (entry/SL/TP must be > 0), or missing required fields.

### Performance Issues

**Problem**: Analytics endpoints timing out
**Solution**: Reduce the `days` parameter. Large date ranges with many signals can be slow. Consider adding a dedicated analytics MongoDB instance for production.

**Problem**: Dashboard loading slowly
**Solution**: The dashboard aggregates multiple data sources. Use `/api/hybrid/dashboard/realtime` for lightweight pipeline status instead of the full dashboard.

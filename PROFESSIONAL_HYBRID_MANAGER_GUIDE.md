# Professional Hybrid Manager System — Complete Guide
## Gold Trading System v3.0.2 — Enterprise-Grade Multi-Tier Approval Workflow

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Role Definitions & Permissions](#2-role-definitions--permissions)
3. [Multi-Level Approval Workflow](#3-multi-level-approval-workflow)
4. [Risk Management Controls](#4-risk-management-controls)
5. [Signal Quality Scoring](#5-signal-quality-scoring)
6. [Team Collaboration Features](#6-team-collaboration-features)
7. [Performance Analytics](#7-performance-analytics)
8. [Real-Time Monitoring Dashboard](#8-real-time-monitoring-dashboard)
9. [Compliance & Audit Logging](#9-compliance--audit-logging)
10. [API Reference with Examples](#10-api-reference-with-examples)
11. [Best Practices](#11-best-practices)
12. [Compliance Requirements](#12-compliance-requirements)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. System Overview

The Professional Hybrid Manager System is an enterprise-grade, multi-tier approval workflow built on top of the Gold Trading System v3.0.2. It provides institutional-quality controls for signal management, risk oversight, team collaboration, and compliance reporting.

### Key Capabilities

| Capability | Description |
|---|---|
| **Multi-Tier Approvals** | 1–3 approvals required based on signal risk tier |
| **6 Role Hierarchy** | SUPER_ADMIN → RISK_MANAGER → TRADING_MANAGER → ANALYST → OPERATOR → VIEWER |
| **Risk Engine** | Position limits, drawdown controls, exposure limits, circuit breakers |
| **Signal Scoring** | 8-dimension quality scoring with A+–F grading |
| **Team Collaboration** | Comments, notes, mentions, activity feed |
| **Performance Analytics** | Sharpe ratio, profit factor, leaderboards, trend analysis |
| **Compliance Logging** | Immutable audit trail for every action |
| **Real-Time Dashboard** | Live signal counts, alert summary, risk status |

### Architecture Summary

```
┌─────────────────────────────────────────────────────────────┐
│                  Hybrid Manager API (/api/hybrid)            │
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │  Auth    │  │ Signals  │  │  Risk    │  │ Reports  │   │
│  │ /auth/*  │  │/signals/*│  │ /risk/*  │  │/reports/*│   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ Managers │  │  Collab  │  │  Alerts  │  │Dashboard │   │
│  │/managers/│  │/collab/* │  │ /alerts/ │  │/dashboard│   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
└─────────────────────────────────────────────────────────────┘
         │                │                │
         ▼                ▼                ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│HybridManager │  │  RiskEngine  │  │Performance   │
│   (Core)     │  │  (Controls)  │  │  Tracker     │
└──────────────┘  └──────────────┘  └──────────────┘
         │                │                │
         └────────────────┴────────────────┘
                          │
                          ▼
                   ┌──────────────┐
                   │   MongoDB    │
                   │  (Persistent)│
                   └──────────────┘
```

---

## 2. Role Definitions & Permissions

### Role Hierarchy

```
SUPER_ADMIN
    │
    ├── RISK_MANAGER
    │       │
    │       └── TRADING_MANAGER
    │               │
    │               ├── ANALYST
    │               ├── OPERATOR
    │               └── VIEWER
```

### Role Descriptions

#### SUPER_ADMIN
The highest authority in the system. Has unrestricted access to all operations including manager CRUD, risk overrides, system configuration, and compliance exports.

**Use for:** System administrators, CTO, Head of Trading

**Key exclusive permissions:**
- `manager:add`, `manager:remove`, `manager:promote`, `manager:demote`, `manager:suspend`
- `risk:override`, `risk:drawdown_override`
- `system:config`, `system:restart`, `system:deploy`, `system:backup`
- `signal:override`

---

#### RISK_MANAGER
Full risk management authority. Can set limits, trigger circuit breakers, and has mandatory approval authority for HIGH/CRITICAL signals.

**Use for:** Chief Risk Officer, Senior Risk Analyst

**Key permissions:**
- All signal operations (approve, reject, adjust, escalate)
- Full risk controls (`risk:set_limits`, `risk:circuit_breaker`, `risk:drawdown_override`)
- Compliance export and audit access
- Alert escalation

**Special rule:** For HIGH and CRITICAL risk tier signals, at least one RISK_MANAGER approval is **mandatory** regardless of other approvals.

---

#### TRADING_MANAGER
Operational trading authority. Can approve/reject/adjust signals and manage day-to-day trading operations.

**Use for:** Head of Trading, Senior Trader, Trading Desk Manager

**Key permissions:**
- Signal approve, reject, adjust, comment
- Limited risk controls (`risk:exposure_adjust`)
- Alert create and resolve
- Team collaboration

---

#### ANALYST
Read and annotate access. Can view all signals and add analysis comments but cannot approve or reject.

**Use for:** Market Analyst, Research Analyst, Junior Trader

**Key permissions:**
- Signal view and list
- Add comments and notes
- View risk config and performance
- No approval authority

---

#### OPERATOR
Operational support role. Can reject signals and create alerts but cannot approve.

**Use for:** Operations Team, Support Staff, Monitoring Personnel

**Key permissions:**
- Signal view, list, reject (not approve)
- Create alerts
- Add comments
- View dashboard

---

#### VIEWER
Read-only access to all non-sensitive data.

**Use for:** Stakeholders, Auditors, Compliance Officers (read-only), Executives

**Key permissions:**
- View signals, risk config, performance, alerts
- View audit log
- View dashboard
- No write operations

---

### Permission Matrix

| Permission | SUPER_ADMIN | RISK_MANAGER | TRADING_MANAGER | ANALYST | OPERATOR | VIEWER |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| manager:add | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| manager:remove | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| manager:suspend | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| signal:approve | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| signal:reject | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ |
| signal:adjust | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| signal:comment | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| signal:view | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| risk:set_limits | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| risk:circuit_breaker | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| risk:view | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| performance:view | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| compliance:export | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| audit:view | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| dashboard:view | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## 3. Multi-Level Approval Workflow

### Signal Risk Tiers

Every signal submitted for review is automatically classified into one of four risk tiers based on a composite scoring algorithm:

| Tier | Required Approvals | RISK_MANAGER Mandatory | Description |
|---|:---:|:---:|---|
| **LOW** | 1 | No | High confidence, good R:R, small position |
| **MEDIUM** | 2 | No | Moderate confidence or elevated position size |
| **HIGH** | 3 | **Yes** | Low confidence, poor R:R, or high volatility |
| **CRITICAL** | 3 | **Yes** | Multiple risk factors present simultaneously |

### Risk Tier Classification Factors

The tier is determined by a composite score across four dimensions:

1. **Confidence Score** (0–3 points)
   - ≥85%: 0 points (low risk)
   - 70–84%: 1 point
   - 55–69%: 2 points
   - <55%: 3 points (high risk)

2. **Risk/Reward Ratio** (0–3 points)
   - ≥3.0: 0 points
   - 2.0–2.9: 1 point
   - 1.5–1.9: 2 points
   - <1.5: 3 points

3. **Position Size** (0–3 points)
   - ≤0.05 lots: 0 points
   - 0.06–0.10 lots: 1 point
   - 0.11–0.50 lots: 2 points
   - >0.50 lots: 3 points

4. **Market Volatility** (0–3 points)
   - LOW: 0 points
   - NORMAL: 1 point
   - HIGH: 2 points
   - EXTREME: 3 points

**Total Score → Tier:**
- 0–2: LOW
- 3–5: MEDIUM
- 6–8: HIGH
- 9–12: CRITICAL

### Approval Workflow Diagram

```
Signal Generated
      │
      ▼
┌─────────────────┐
│  Score Signal   │ ← 8-dimension quality scoring
│  Classify Tier  │ ← LOW / MEDIUM / HIGH / CRITICAL
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ PENDING_REVIEW  │ ← Signal enters approval queue
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
APPROVE    REJECT ──────────────────────────────────┐
    │                                               │
    ▼                                               ▼
Count approvals                              REJECTED (final)
    │                                        Audit logged
    ├── Not enough? → Stay PENDING_REVIEW
    │
    ├── Enough approvals?
    │   ├── LOW tier: 1 approval → APPROVED
    │   ├── MEDIUM tier: 2 approvals → APPROVED
    │   └── HIGH/CRITICAL: 3 approvals + RISK_MANAGER → APPROVED
    │
    └── APPROVED (final)
        Signal activated
        Audit logged
        Alert sent
```

### Escalation Flow

```
PENDING_REVIEW
      │
      │ (Manager escalates)
      ▼
  ESCALATED ──────────────────────────────────────────┐
      │                                               │
      │ (Higher authority reviews)                    │
      ▼                                               ▼
PENDING_REVIEW                                  REJECTED
(back to queue with                          (with escalation
 escalation note)                              context)
```

---

## 4. Risk Management Controls

### Position Limits

| Parameter | Default | Description |
|---|---|---|
| `max_position_size_lots` | 1.0 | Maximum lot size per position |
| `max_lot_size` | 2.0 | Hard cap on lot size |
| `max_open_positions` | 5 | Maximum concurrent open positions |
| `min_rr_ratio` | 1.5 | Minimum risk/reward ratio (non-Gold) |
| `min_rr_ratio_gold` | 1.8 | Minimum risk/reward ratio (Gold) |

### Drawdown Limits

| Parameter | Default | Description |
|---|---|---|
| `max_daily_drawdown_pct` | 3.0% | Maximum daily loss |
| `max_weekly_drawdown_pct` | 6.0% | Maximum weekly loss |
| `max_monthly_drawdown_pct` | 12.0% | Maximum monthly loss |
| `circuit_breaker_drawdown_pct` | 5.0% | Triggers automatic halt |

### Exposure Limits

| Parameter | Default | Description |
|---|---|---|
| `max_exposure_per_pair_pct` | 25.0% | Max exposure per trading pair |
| `max_total_exposure_pct` | 80.0% | Max total portfolio exposure |
| `max_gold_exposure_pct` | 30.0% | Max Gold (XAU) exposure |
| `max_usd_exposure_pct` | 40.0% | Max USD pairs exposure |
| `max_crypto_exposure_pct` | 15.0% | Max Crypto exposure |

### Circuit Breaker

The circuit breaker is a hard stop mechanism that immediately halts all trading when triggered. It can be activated:

1. **Automatically** — when `circuit_breaker_drawdown_pct` is breached
2. **Manually** — by SUPER_ADMIN or RISK_MANAGER via the API

When the circuit breaker is active:
- All new signal approvals are blocked
- A CRITICAL alert is generated
- All managers are notified
- The halt reason is logged in the audit trail

To reset the circuit breaker, a SUPER_ADMIN or RISK_MANAGER must explicitly call the reset endpoint with a documented reason.

### Stop-Loss Enforcement

The risk engine enforces stop-loss placement rules:

**Gold (XAUUSD/XAUEUR):**
- Minimum SL distance: 3.0 price units
- Maximum SL distance: 100.0 price units
- If SL too tight: auto-calculated from ATR (1.5× ATR)
- If SL too wide: capped at maximum distance

**Forex:**
- Minimum SL distance: 0.0005 price units
- Maximum SL distance: 0.05 price units

**JPY pairs:**
- Minimum SL distance: 0.05 price units
- Maximum SL distance: 5.0 price units

---

## 5. Signal Quality Scoring

### Scoring Dimensions

Each signal is scored across 8 dimensions (0–100 each):

| Dimension | Weight | Description |
|---|:---:|---|
| Technical Confidence | 25% | ML model confidence score |
| R:R Quality | 20% | Risk/reward ratio quality |
| Entry Precision | 10% | Entry vs current price proximity |
| MTF Alignment | 15% | Multi-timeframe confluence |
| Regime Fit | 10% | Strategy-regime alignment |
| Volatility Context | 10% | ATR-based volatility assessment |
| Session Quality | 5% | Trading session timing |
| Historical Pattern | 5% | Similar historical signal performance |

### Grade Scale

| Grade | Score Range | Recommendation |
|---|---|---|
| **A+** | 85–100 | APPROVE |
| **A** | 75–84 | APPROVE |
| **B** | 65–74 | APPROVE |
| **C** | 55–64 | REVIEW |
| **D** | 45–54 | REVIEW |
| **F** | 0–44 | REJECT |

### Regime-Strategy Fit Matrix

| Strategy | Regime | Fit Score |
|---|---|---|
| SMC | TREND_UP | 95 |
| SMC | TREND_DOWN | 95 |
| MEAN_REVERSION | RANGE | 90 |
| BREAKOUT | TREND_UP | 85 |
| BREAKOUT | TREND_DOWN | 85 |
| SMC | RANGE | 60 |
| MEAN_REVERSION | TREND_UP | 50 |
| MEAN_REVERSION | TREND_DOWN | 50 |

---

## 6. Team Collaboration Features

### Comments

Comments can be added to any signal by managers with `collab:comment` permission. Comment types:

| Type | Description |
|---|---|
| `GENERAL` | General observation |
| `ANALYSIS` | Technical or fundamental analysis |
| `RISK_NOTE` | Risk-related observation |
| `DECISION` | Decision rationale |
| `QUESTION` | Question for the team |

Comments support:
- **Mentions** — tag specific managers by ID
- **Private comments** — visible only to managers with approval authority
- **Reactions** — emoji reactions (stored in reactions dict)

### Notes

Standalone notes are not tied to a specific signal and serve as a team knowledge base. Note types:

| Type | Description |
|---|---|
| `GENERAL` | General team note |
| `MARKET_ANALYSIS` | Market analysis and outlook |
| `RISK_OBSERVATION` | Risk management observations |
| `STRATEGY` | Strategy notes and updates |
| `COMPLIANCE` | Compliance-related notes |

Notes support:
- **Tags** — for categorisation and search
- **Pinning** — pin important notes to the top
- **Signal linking** — optionally link to a specific signal

### Team Activity Feed

The `/api/hybrid/collaboration/activity` endpoint provides a unified activity feed showing:
- Recent comments across all signals
- Recent notes from all managers
- Recent approval/rejection decisions

---

## 7. Performance Analytics

### Manager Metrics

For each manager, the system tracks:

| Metric | Description |
|---|---|
| Total Approvals | Count of signals approved in period |
| Total Rejections | Count of signals rejected in period |
| Total Adjustments | Count of signal adjustments made |
| Approval Rate | Approvals / (Approvals + Rejections) × 100 |
| Avg Quality Score | Average quality score of approved signals |
| Avg Review Time | Average minutes from submission to decision |
| Comments Added | Collaboration activity count |

### Signal Performance Metrics

When signal outcomes are recorded, the system calculates:

| Metric | Description |
|---|---|
| Win Rate | Winning trades / Total trades |
| Profit Factor | Gross profit / Gross loss |
| Expectancy | Expected P&L per trade |
| Sharpe Ratio | Annualised risk-adjusted return |
| Sortino Ratio | Downside-adjusted Sharpe ratio |
| Max Drawdown | Peak-to-trough equity decline |
| Calmar Ratio | Annualised return / Max drawdown |
| Recovery Factor | Total return / Max drawdown |
| Current Streak | Current win/loss streak |
| Max Streak | Maximum historical streak |

### Leaderboard Metrics

The leaderboard can be sorted by:
- `total_decisions` — Most active reviewer
- `approval_rate` — Highest approval rate
- `quality_score` — Highest average quality of approved signals
- `activity_score` — Composite activity score (decisions × 2 + comments × 0.5)

---

## 8. Real-Time Monitoring Dashboard

The dashboard (`GET /api/hybrid/dashboard`) provides a real-time snapshot:

```json
{
  "system_status": {
    "circuit_breaker_active": false,
    "trading_halted": false,
    "active_managers": 3
  },
  "signals": {
    "pending": 5,
    "escalated": 1,
    "approved_today": 12,
    "rejected_today": 3,
    "pending_by_tier": {
      "LOW": 2,
      "MEDIUM": 2,
      "HIGH": 1,
      "CRITICAL": 0
    }
  },
  "alerts": {
    "critical": 0,
    "warning": 2,
    "info": 5,
    "total_active": 7
  },
  "weekly_performance": {
    "approved": 45,
    "rejected": 12,
    "total": 57,
    "approval_rate": 78.95
  },
  "recent_decisions": [...]
}
```

---

## 9. Compliance & Audit Logging

### Audit Trail

Every action in the system generates an immutable audit record containing:

| Field | Description |
|---|---|
| `audit_id` | Unique UUID for the audit record |
| `timestamp` | UTC timestamp of the action |
| `action` | Action key (e.g., `signal:approve`) |
| `performed_by` | Manager ID who performed the action |
| `role` | Manager's role at time of action |
| `details` | Action-specific details |
| `success` | Whether the action succeeded |
| `error` | Error message if action failed |
| `ip_address` | IP address (when available) |
| `system` | Always "hybrid_manager" |
| `version` | System version "3.0.2" |

### Audit Actions Logged

All of the following actions are logged:
- `manager:add`, `manager:remove`, `manager:update`, `manager:suspend`
- `signal:approve`, `signal:reject`, `signal:adjust`, `signal:escalate`
- `risk:set_limits`, `risk:circuit_breaker`, `risk:circuit_breaker_reset`
- `alert:create`, `alert:resolve`
- `collab:comment`, `collab:note`
- `compliance:view`, `audit:view`

### Compliance Report

The compliance report (`GET /api/hybrid/compliance/report`) provides:
- Signal decision summary (total, approved, rejected, approval rate)
- Audit action breakdown by type
- Manager activity summary
- Risk events (circuit breaker triggers)

---

## 10. API Reference with Examples

### Authentication

All endpoints (except `/api/hybrid/health`) require a Bearer JWT token.

#### Login

```bash
curl -X POST https://your-domain.com/api/hybrid/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@company.com",
    "password": "your-secure-password"
  }'
```

**Response:**
```json
{
  "success": true,
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "manager_id": "uuid-here",
  "role": "SUPER_ADMIN",
  "full_name": "John Smith",
  "permissions": ["signal:approve", "risk:set_limits", ...],
  "expires_in": "24h"
}
```

#### Using the Token

```bash
export TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

curl -H "Authorization: Bearer $TOKEN" \
  https://your-domain.com/api/hybrid/dashboard
```

---

### Manager Management

#### Create a Manager

```bash
curl -X POST https://your-domain.com/api/hybrid/managers \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "trader@company.com",
    "full_name": "Jane Doe",
    "role": "TRADING_MANAGER",
    "password": "SecurePass123!",
    "department": "Trading Desk"
  }'
```

#### List Managers

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "https://your-domain.com/api/hybrid/managers?role_filter=TRADING_MANAGER"
```

#### Suspend a Manager

```bash
curl -X POST https://your-domain.com/api/hybrid/managers/{manager_id}/suspend \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason": "Violation of trading policy — under investigation"}'
```

---

### Signal Management

#### Submit Signal for Review

```bash
curl -X POST https://your-domain.com/api/hybrid/signals/submit \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "signal_id": "sig_001",
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
    }
  }'
```

**Response:**
```json
{
  "success": true,
  "review_id": "uuid-here",
  "signal_id": "sig_001",
  "risk_tier": "MEDIUM",
  "quality_score": 72.5,
  "grade": "B",
  "required_approvals": 2,
  "risk_manager_required": false,
  "recommendation": "APPROVE"
}
```

#### Approve a Signal

```bash
curl -X POST https://your-domain.com/api/hybrid/signals/approve \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "signal_id": "sig_001",
    "notes": "Strong SMC setup with good confluence. Approved."
  }'
```

#### Reject a Signal

```bash
curl -X POST https://your-domain.com/api/hybrid/signals/reject \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "signal_id": "sig_002",
    "reason": "R:R ratio below minimum threshold. Entry too close to resistance level.",
    "category": "QUALITY"
  }'
```

#### Adjust Signal Parameters

```bash
curl -X POST https://your-domain.com/api/hybrid/signals/adjust \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "signal_id": "sig_003",
    "adjustments": {
      "entry_price": 2648.00,
      "sl_price": 2638.00,
      "tp1": 2663.00
    },
    "reason": "Adjusted entry to better confluence zone. Improved R:R from 1.8 to 2.1."
  }'
```

#### Get Pending Signals

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "https://your-domain.com/api/hybrid/signals/pending?risk_tier=HIGH&limit=20"
```

---

### Risk Management

#### Set Risk Limits

```bash
curl -X POST https://your-domain.com/api/hybrid/risk/limits \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "limits": {
      "max_daily_drawdown_pct": 2.5,
      "max_position_size_lots": 0.5,
      "min_rr_ratio": 2.0,
      "circuit_breaker_drawdown_pct": 4.0
    }
  }'
```

#### Trigger Circuit Breaker

```bash
curl -X POST https://your-domain.com/api/hybrid/risk/circuit-breaker/trigger \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "reason": "Unusual market volatility detected. Halting trading pending review.",
    "halt_trading": true
  }'
```

#### Reset Circuit Breaker

```bash
curl -X POST https://your-domain.com/api/hybrid/risk/circuit-breaker/reset \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "reason": "Market conditions normalised. Risk review completed. Resuming trading."
  }'
```

#### Validate Signal Risk

```bash
curl -X POST https://your-domain.com/api/hybrid/risk/validate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "signal": {
      "symbol": "XAUUSD",
      "signal_type": "BUY",
      "entry_price": 2650.50,
      "sl_price": 2640.00,
      "tp1": 2665.00,
      "lot_size": 0.10
    },
    "account_balance": 50000.0,
    "open_positions": []
  }'
```

---

### Performance Analytics

#### Get Manager Leaderboard

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "https://your-domain.com/api/hybrid/performance/leaderboard?days=30&metric=total_decisions&limit=10"
```

#### Get Weekly Report

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "https://your-domain.com/api/hybrid/reports/weekly?week_offset=0"
```

#### Get Monthly Report

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "https://your-domain.com/api/hybrid/reports/monthly?month_offset=0"
```

#### Get Trend Analysis

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "https://your-domain.com/api/hybrid/reports/trends?days=90&granularity=weekly"
```

---

### Team Collaboration

#### Add a Comment

```bash
curl -X POST https://your-domain.com/api/hybrid/collaboration/comments \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "signal_id": "sig_001",
    "comment_text": "Strong SMC structure. Order block at 2648 is well-defined. Recommend approval.",
    "comment_type": "ANALYSIS",
    "mentions": ["manager_id_1", "manager_id_2"]
  }'
```

#### Add a Team Note

```bash
curl -X POST https://your-domain.com/api/hybrid/collaboration/notes \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Gold Market Outlook — Week 48",
    "content": "DXY showing weakness. Gold likely to test 2700 resistance this week. Bias: BULLISH. Recommend increasing approval threshold for BUY signals.",
    "note_type": "MARKET_ANALYSIS",
    "tags": ["gold", "weekly-outlook", "bullish"]
  }'
```

---

### Alerts

#### Create an Alert

```bash
curl -X POST https://your-domain.com/api/hybrid/alerts \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "High Impact News Approaching",
    "message": "US CPI data release in 30 minutes. Consider pausing signal approvals.",
    "severity": "WARNING",
    "category": "TRADING"
  }'
```

#### Resolve an Alert

```bash
curl -X POST https://your-domain.com/api/hybrid/alerts/{alert_id}/resolve \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"resolution_note": "CPI data released. Market impact minimal. Normal operations resumed."}'
```

---

### Compliance

#### Get Audit Log

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "https://your-domain.com/api/hybrid/audit?since_hours=168&action=signal:approve&limit=100"
```

#### Get Compliance Report

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "https://your-domain.com/api/hybrid/compliance/report?days=30"
```

---

## 11. Best Practices

### Signal Review Best Practices

1. **Always review quality score first** — Signals with grade F should almost always be rejected. Signals with grade A+ should be fast-tracked.

2. **Check risk tier before approving** — HIGH and CRITICAL signals require RISK_MANAGER sign-off. Do not approve these without risk review.

3. **Document your decisions** — Always add a comment explaining your approval or rejection rationale. This creates a learning record for the team.

4. **Use adjustments sparingly** — Only adjust signal parameters when you have strong conviction the adjustment improves the trade. Document the reason clearly.

5. **Escalate when uncertain** — If you are unsure about a signal, escalate it rather than approving or rejecting. The escalation system exists for this purpose.

6. **Review pending signals regularly** — Signals expire after 24 hours. Check the pending queue at least every 4 hours during trading sessions.

### Risk Management Best Practices

1. **Monitor the circuit breaker utilisation** — When the circuit breaker is at 80%+ utilisation, proactively reduce position sizes and tighten approval criteria.

2. **Review risk limits monthly** — Market conditions change. Review and adjust risk limits at the start of each month.

3. **Never override risk limits without documentation** — Any risk override must be documented with a clear reason in the audit trail.

4. **Treat CRITICAL alerts immediately** — CRITICAL alerts require immediate attention. Do not leave them unresolved for more than 30 minutes.

5. **Test circuit breaker quarterly** — Conduct a quarterly drill to ensure the circuit breaker mechanism works correctly.

### Team Collaboration Best Practices

1. **Use mentions for urgent items** — When a signal needs immediate attention from a specific manager, use the mentions feature.

2. **Write weekly market notes** — The TRADING_MANAGER or ANALYST should post a weekly market outlook note every Monday morning.

3. **Review team activity daily** — Check the activity feed at the start of each trading day to stay informed of overnight decisions.

4. **Use private comments for sensitive information** — If a comment contains sensitive risk information, mark it as private.

---

## 12. Compliance Requirements

### Mandatory Audit Trail

The system maintains an immutable audit trail for all operations. This audit trail:
- Cannot be deleted or modified
- Is stored in MongoDB with timestamps
- Includes the manager ID, role, and IP address for every action
- Is retained indefinitely (no automatic purging)

### Approval Documentation

For regulatory compliance, every signal approval must include:
- The approving manager's ID and role
- The timestamp of approval
- Any notes or adjustments made
- The quality score at time of approval

### Risk Limit Changes

Any change to risk limits must be:
- Made by SUPER_ADMIN or RISK_MANAGER only
- Documented with a reason (via the audit trail)
- Reviewed and confirmed within 24 hours

### Circuit Breaker Events

Every circuit breaker trigger must be:
- Documented with a specific reason
- Reviewed by SUPER_ADMIN within 1 hour
- Reset only after explicit written approval
- Reported in the monthly compliance report

### Data Retention

| Data Type | Retention Period |
|---|---|
| Audit logs | Indefinite |
| Signal review records | 7 years |
| Performance reports | 5 years |
| Alert records | 2 years |
| Collaboration notes | 3 years |

---

## 13. Troubleshooting

### Common Issues

#### "Token is not a hybrid manager token"
**Cause:** You are using a token from the old manager system (`/api/manager/auth/login`) instead of the hybrid system.
**Fix:** Login via `/api/hybrid/auth/login` to get a hybrid manager token.

#### "Role 'X' does not have permission for action 'Y'"
**Cause:** Your role does not have the required permission for this operation.
**Fix:** Check the permission matrix in Section 2. Contact a SUPER_ADMIN to upgrade your role if needed.

#### "Signal not found or not pending review"
**Cause:** The signal has already been approved, rejected, or expired.
**Fix:** Check the signal status via `GET /api/hybrid/signals/{signal_id}`.

#### "Rejection reason must be at least 10 characters"
**Cause:** The rejection reason is too short.
**Fix:** Provide a meaningful rejection reason of at least 10 characters.

#### "Circuit breaker triggered: drawdown X% >= Y%"
**Cause:** The drawdown has exceeded the circuit breaker threshold.
**Fix:** A SUPER_ADMIN or RISK_MANAGER must review the situation and reset the circuit breaker via `POST /api/hybrid/risk/circuit-breaker/reset`.

#### "Too many correlated positions in GOLD"
**Cause:** The maximum number of correlated positions (default: 3) has been reached.
**Fix:** Close some existing Gold positions before opening new ones, or increase the `max_correlated_positions` limit.

### Performance Issues

If the API is responding slowly:
1. Check MongoDB connection via `GET /api/hybrid/dashboard/system-status`
2. Check CPU and memory usage in the system status response
3. Review recent audit logs for unusual activity patterns
4. Consider reducing the `limit` parameter on list endpoints

### Getting Help

For system issues, create a CRITICAL alert via the API and contact the system administrator. Include:
- The endpoint that failed
- The request body (redact sensitive data)
- The error response
- The timestamp of the failure

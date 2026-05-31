# 👔 System Manager Guide — Grandcom Gold Signals v3.0.2

Complete reference for designated system managers: roles, permissions,
API usage, common workflows, best practices, and troubleshooting.

---

## Table of Contents

1. [Manager Roles](#1-manager-roles)
2. [Getting Started](#2-getting-started)
3. [API Reference with curl Examples](#3-api-reference-with-curl-examples)
4. [Common Tasks & Workflows](#4-common-tasks--workflows)
5. [Best Practices](#5-best-practices)
6. [Troubleshooting](#6-troubleshooting)
7. [Security Information](#7-security-information)

---

## 1. Manager Roles

The system uses three roles with a strict permission hierarchy.

### ADMIN
Full control over the entire system.

| Capability | Allowed |
|---|---|
| Add / remove / update managers | ✅ |
| View manager list & profiles | ✅ |
| System status & monitoring | ✅ |
| View trading signals | ✅ |
| View system logs | ✅ |
| Create & resolve alerts | ✅ |
| Trigger backups | ✅ |
| View backup history | ✅ |
| Request service restart | ✅ |
| Record deployments / rollbacks | ✅ |
| View audit trail | ✅ |
| View dashboard | ✅ |

### MANAGER
Operational control — can run the system day-to-day but cannot manage
other manager accounts.

| Capability | Allowed |
|---|---|
| Add / remove / update managers | ❌ |
| View manager list & profiles | ✅ |
| System status & monitoring | ✅ |
| View trading signals | ✅ |
| View system logs | ✅ |
| Create & resolve alerts | ✅ |
| Trigger backups | ✅ |
| View backup history | ✅ |
| Request service restart | ✅ |
| Record deployments / rollbacks | ✅ |
| View audit trail | ✅ |
| View dashboard | ✅ |

### VIEWER
Read-only access — suitable for stakeholders who need visibility
without the ability to change anything.

| Capability | Allowed |
|---|---|
| Add / remove / update managers | ❌ |
| View manager list & profiles | ✅ |
| System status & monitoring | ✅ |
| View trading signals | ✅ |
| View system logs | ✅ |
| Create & resolve alerts | ❌ |
| Trigger backups | ❌ |
| View backup history | ✅ |
| Request service restart | ❌ |
| Record deployments / rollbacks | ❌ |
| View audit trail | ✅ |
| View dashboard | ✅ |

---

## 2. Getting Started

### Step 1 — Create the first ADMIN manager

Run this once from the Railway shell or locally (requires `MONGO_URL` and
`DB_NAME` environment variables):

```bash
cd backend
python - <<'EOF'
import asyncio, os, uuid
from datetime import datetime
from passlib.context import CryptContext
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

async def bootstrap():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    manager_id = str(uuid.uuid4())
    doc = {
        "manager_id":    manager_id,
        "email":         "admin@grandcom.com",
        "full_name":     "System Administrator",
        "role":          "ADMIN",
        "password_hash": pwd.hash("ChangeMe@2024!"),
        "is_active":     True,
        "created_at":    datetime.utcnow(),
        "created_by":    "bootstrap",
        "last_login":    None,
        "metadata":      {},
    }
    await db.system_managers.insert_one(doc)
    print(f"✅ ADMIN created — manager_id: {manager_id}")
    print("   Email:    admin@grandcom.com")
    print("   Password: ChangeMe@2024!  ← change this immediately!")

asyncio.run(bootstrap())
EOF
```

### Step 2 — Log in and get your token

```bash
BASE="https://your-railway-app.up.railway.app"

curl -s -X POST "$BASE/api/manager/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@grandcom.com","password":"ChangeMe@2024!"}' \
  | python3 -m json.tool
```

Copy the `access_token` value. All subsequent requests need:

```
Authorization: Bearer <access_token>
```

### Step 3 — Verify your profile

```bash
TOKEN="<paste_token_here>"

curl -s "$BASE/api/manager/auth/me" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -m json.tool
```

### Step 4 — View the dashboard

```bash
curl -s "$BASE/api/manager/dashboard" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -m json.tool
```

---

## 3. API Reference with curl Examples

All endpoints are prefixed with `/api/manager`.
Replace `$BASE` and `$TOKEN` with your values throughout.

---

### Authentication

#### POST /api/manager/auth/login
```bash
curl -s -X POST "$BASE/api/manager/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "manager@grandcom.com",
    "password": "SecurePass@123"
  }'
```

#### GET /api/manager/auth/me
```bash
curl -s "$BASE/api/manager/auth/me" \
  -H "Authorization: Bearer $TOKEN"
```

---

### Manager Management (ADMIN only for write operations)

#### POST /api/manager/managers — Add a manager
```bash
curl -s -X POST "$BASE/api/manager/managers" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "email":     "ops@grandcom.com",
    "full_name": "Operations Lead",
    "role":      "MANAGER",
    "password":  "Ops@SecurePass1!"
  }'
```

#### GET /api/manager/managers — List all managers
```bash
curl -s "$BASE/api/manager/managers" \
  -H "Authorization: Bearer $TOKEN"

# Include deactivated accounts
curl -s "$BASE/api/manager/managers?include_inactive=true" \
  -H "Authorization: Bearer $TOKEN"
```

#### GET /api/manager/managers/{id} — Get a single manager
```bash
curl -s "$BASE/api/manager/managers/MANAGER_ID_HERE" \
  -H "Authorization: Bearer $TOKEN"
```

#### PUT /api/manager/managers/{id} — Update a manager
```bash
# Promote to ADMIN
curl -s -X PUT "$BASE/api/manager/managers/MANAGER_ID_HERE" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role": "ADMIN"}'

# Deactivate
curl -s -X PUT "$BASE/api/manager/managers/MANAGER_ID_HERE" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"is_active": false}'
```

#### DELETE /api/manager/managers/{id} — Deactivate a manager
```bash
curl -s -X DELETE "$BASE/api/manager/managers/MANAGER_ID_HERE" \
  -H "Authorization: Bearer $TOKEN"
```

---

### System Monitoring

#### GET /api/manager/system/status
```bash
curl -s "$BASE/api/manager/system/status" \
  -H "Authorization: Bearer $TOKEN"
```

Sample response:
```json
{
  "success": true,
  "status": {
    "overall": "HEALTHY",
    "timestamp": "2024-01-15T10:30:00",
    "version": "3.0.2",
    "database": "HEALTHY",
    "cpu_percent": 12.4,
    "memory_percent": 45.2,
    "disk_percent": 23.1,
    "signals_last_1h": 8,
    "active_alerts": 0
  }
}
```

#### GET /api/manager/system/signals
```bash
# Last 50 signals from the past 24 hours
curl -s "$BASE/api/manager/system/signals" \
  -H "Authorization: Bearer $TOKEN"

# Last 100 signals from the past 48 hours
curl -s "$BASE/api/manager/system/signals?limit=100&hours=48" \
  -H "Authorization: Bearer $TOKEN"
```

#### GET /api/manager/system/logs
```bash
curl -s "$BASE/api/manager/system/logs?limit=50" \
  -H "Authorization: Bearer $TOKEN"
```

---

### Alert Management

#### POST /api/manager/alerts — Create an alert
```bash
curl -s -X POST "$BASE/api/manager/alerts" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title":    "High CPU Usage Detected",
    "message":  "CPU has been above 80% for 10 minutes",
    "severity": "WARNING",
    "category": "SYSTEM"
  }'
```

Severity options: `INFO` | `WARNING` | `CRITICAL`  
Category options: `GENERAL` | `TRADING` | `SYSTEM` | `SECURITY`

#### GET /api/manager/alerts — List alerts
```bash
# Active alerts only (default)
curl -s "$BASE/api/manager/alerts" \
  -H "Authorization: Bearer $TOKEN"

# All alerts including resolved
curl -s "$BASE/api/manager/alerts?include_resolved=true" \
  -H "Authorization: Bearer $TOKEN"

# Critical alerts only
curl -s "$BASE/api/manager/alerts?severity=CRITICAL" \
  -H "Authorization: Bearer $TOKEN"
```

#### POST /api/manager/alerts/{id}/resolve — Resolve an alert
```bash
curl -s -X POST "$BASE/api/manager/alerts/ALERT_ID_HERE/resolve" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"resolution_note": "Restarted the signal generator service. CPU normalised."}'
```

---

### Backup Management

#### POST /api/manager/backups/trigger — Trigger a backup
```bash
# Full database backup
curl -s -X POST "$BASE/api/manager/backups/trigger" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"backup_type": "full"}'

# Signals only
curl -s -X POST "$BASE/api/manager/backups/trigger" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"backup_type": "signals"}'
```

Backup types: `full` | `signals` | `models`

#### GET /api/manager/backups/history — View backup history
```bash
curl -s "$BASE/api/manager/backups/history?limit=10" \
  -H "Authorization: Bearer $TOKEN"
```

---

### System Control

#### POST /api/manager/system/restart — Request a service restart
```bash
curl -s -X POST "$BASE/api/manager/system/restart" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "service_name": "api",
    "reason": "Memory leak detected — scheduled maintenance restart"
  }'
```

Services: `api` | `signal_generator` | `outcome_tracker` | `all`

> **Note:** This endpoint logs the request and creates a CRITICAL alert.
> The actual restart must be applied via the Railway dashboard or CLI:
> ```bash
> railway service restart
> ```

#### POST /api/manager/system/deploy — Record a deployment
```bash
# New deployment
curl -s -X POST "$BASE/api/manager/system/deploy" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "version":     "3.0.3",
    "description": "Added new SMC strategy improvements",
    "rollback":    false
  }'

# Rollback
curl -s -X POST "$BASE/api/manager/system/deploy" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "version":     "3.0.2",
    "description": "Rolling back due to signal quality regression",
    "rollback":    true
  }'
```

---

### Audit Trail

#### GET /api/manager/audit — View audit log
```bash
# Last 100 entries (default 7 days)
curl -s "$BASE/api/manager/audit" \
  -H "Authorization: Bearer $TOKEN"

# Filter by manager
curl -s "$BASE/api/manager/audit?manager_id=MANAGER_ID_HERE" \
  -H "Authorization: Bearer $TOKEN"

# Filter by action
curl -s "$BASE/api/manager/audit?action=system%3Arestart" \
  -H "Authorization: Bearer $TOKEN"

# Last 30 days
curl -s "$BASE/api/manager/audit?since_hours=720" \
  -H "Authorization: Bearer $TOKEN"
```

---

### Dashboard

#### GET /api/manager/dashboard
```bash
curl -s "$BASE/api/manager/dashboard" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -m json.tool
```

---

### Roles & Permissions (no auth required)

#### GET /api/manager/roles
```bash
curl -s "$BASE/api/manager/roles" | python3 -m json.tool
```

---

## 4. Common Tasks & Workflows

### Onboarding a new operations manager

```bash
# 1. Create the account (ADMIN token required)
curl -s -X POST "$BASE/api/manager/managers" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "email":     "newops@grandcom.com",
    "full_name": "New Ops Manager",
    "role":      "MANAGER",
    "password":  "TempPass@2024!"
  }'

# 2. Verify the account was created
curl -s "$BASE/api/manager/managers" \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# 3. Share credentials securely with the new manager
# 4. Ask them to log in and change their password immediately
```

### Daily health check routine

```bash
# 1. Check overall system status
curl -s "$BASE/api/manager/system/status" \
  -H "Authorization: Bearer $TOKEN"

# 2. Check for open alerts
curl -s "$BASE/api/manager/alerts" \
  -H "Authorization: Bearer $TOKEN"

# 3. Review recent signals
curl -s "$BASE/api/manager/system/signals?hours=24" \
  -H "Authorization: Bearer $TOKEN"

# 4. Check dashboard summary
curl -s "$BASE/api/manager/dashboard" \
  -H "Authorization: Bearer $TOKEN"
```

### Responding to a CRITICAL alert

```bash
# 1. List critical alerts
curl -s "$BASE/api/manager/alerts?severity=CRITICAL" \
  -H "Authorization: Bearer $TOKEN"

# 2. Investigate — check system status
curl -s "$BASE/api/manager/system/status" \
  -H "Authorization: Bearer $TOKEN"

# 3. Check recent logs
curl -s "$BASE/api/manager/system/logs?limit=50" \
  -H "Authorization: Bearer $TOKEN"

# 4. Take action (e.g. trigger backup before restart)
curl -s -X POST "$BASE/api/manager/backups/trigger" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"backup_type": "full"}'

# 5. Request restart if needed
curl -s -X POST "$BASE/api/manager/system/restart" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"service_name": "api", "reason": "Responding to CRITICAL alert #ALERT_ID"}'

# 6. Resolve the alert once fixed
curl -s -X POST "$BASE/api/manager/alerts/ALERT_ID/resolve" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"resolution_note": "Restarted API service. Issue resolved."}'
```

### Pre-deployment checklist

```bash
# 1. Trigger a full backup
curl -s -X POST "$BASE/api/manager/backups/trigger" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"backup_type": "full"}'

# 2. Confirm backup completed
curl -s "$BASE/api/manager/backups/history?limit=1" \
  -H "Authorization: Bearer $TOKEN"

# 3. Record the deployment
curl -s -X POST "$BASE/api/manager/system/deploy" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "version":     "3.0.3",
    "description": "Describe what changed",
    "rollback":    false
  }'

# 4. Push to Railway
git push railway main
# or: railway up

# 5. Verify system health after deploy
curl -s "$BASE/api/manager/system/status" \
  -H "Authorization: Bearer $TOKEN"
```

### Offboarding a manager

```bash
# Soft-delete (deactivate) — preserves audit history
curl -s -X DELETE "$BASE/api/manager/managers/MANAGER_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Verify deactivation
curl -s "$BASE/api/manager/managers/MANAGER_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

---

## 5. Best Practices

### Credential management
- **Change the bootstrap password immediately** after first login.
- Use strong passwords: minimum 12 characters, mixed case, numbers, symbols.
- Never share tokens. Each manager must have their own account.
- Tokens expire after 24 hours — re-login to refresh.
- Store tokens in environment variables, never in code or logs.

### Role assignment
- Grant the **minimum role** needed for the job.
- Use **VIEWER** for stakeholders who only need dashboards and reports.
- Use **MANAGER** for operations staff who run day-to-day tasks.
- Reserve **ADMIN** for senior engineers who manage the team.
- Review role assignments quarterly.

### Monitoring cadence
- Check the dashboard at the start of every shift.
- Review open alerts before taking any system action.
- Always trigger a **full backup** before deployments or restarts.
- Check backup history to confirm backups completed successfully.

### Audit trail
- The audit log is immutable — every action is recorded automatically.
- Review the audit trail after any incident.
- Use `manager_id` and `action` filters to narrow investigations.
- Export audit logs monthly for compliance records.

### Alerts
- Resolve alerts promptly with a descriptive `resolution_note`.
- Do not leave CRITICAL alerts open for more than 30 minutes.
- Create INFO alerts for planned maintenance windows so the team is aware.

---

## 6. Troubleshooting

### "Invalid credentials" on login
- Verify the email address is correct (case-sensitive).
- Confirm the account is active: ask an ADMIN to check with `GET /api/manager/managers`.
- If the password was recently changed, use the new password.
- If locked out, an ADMIN can reset the password by updating the `password_hash` field directly in MongoDB.

### "Token has expired"
- Tokens are valid for 24 hours. Simply log in again to get a new token.
- If tokens expire too quickly, ask an ADMIN to increase `JWT_EXPIRATION_HOURS` in Railway environment variables.

### "Role 'VIEWER' does not have permission for action 'alert:create'"
- You are using an endpoint that requires a higher role.
- Contact an ADMIN to upgrade your role if the access is legitimate.
- Check `GET /api/manager/roles` to see the full permission matrix.

### Dashboard shows "DEGRADED" health
1. Check `GET /api/manager/system/status` for the specific failing component.
2. If `database` is `UNHEALTHY`: verify `MONGO_URL` in Railway environment variables.
3. If `cpu_percent > 85`: check for runaway processes; consider a service restart.
4. If `memory_percent > 85`: trigger a restart of the API service.
5. If `disk_percent > 90`: clean up old backup files from the `backups/` directory.

### Backup shows "FAILED" status
1. Check `GET /api/manager/backups/history` for the error message in the `result` field.
2. Common causes: insufficient disk space, MongoDB connection timeout, missing `backups/` directory.
3. Ensure the `backups/` directory exists and is writable.
4. Retry with `POST /api/manager/backups/trigger`.

### Service restart not taking effect
- The restart endpoint **logs the request** but does not directly restart the process on Railway.
- Apply the actual restart via:
  ```bash
  railway service restart
  ```
  or through the Railway dashboard → your service → **Restart**.

### "Manager not found or inactive" after login
- The account may have been deactivated. Contact an ADMIN.
- An ADMIN can reactivate with:
  ```bash
  curl -s -X PUT "$BASE/api/manager/managers/MANAGER_ID" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"is_active": true}'
  ```

---

## 7. Security Information

### Authentication
- All manager endpoints (except `GET /api/manager/roles`) require a valid JWT.
- Tokens are signed with `JWT_SECRET` using HS256. Keep this secret secure.
- Tokens carry the manager's role — changing a role takes effect on the **next login**.
- There is no token revocation mechanism; deactivating the account prevents new logins but does not invalidate existing tokens. For immediate lockout, change `JWT_SECRET` in Railway (this invalidates **all** tokens system-wide).

### Audit trail
- Every API call that mutates state writes an immutable record to `manager_audit_log`.
- Records include: timestamp, action, manager ID, role, details, success/failure.
- The audit log cannot be deleted through the API — only a direct MongoDB operation can remove records.

### Permission enforcement
- Permissions are checked server-side on every request.
- The role embedded in the JWT is **not trusted** — the server re-reads the role from MongoDB on every request.
- A deactivated manager's token will be rejected even if it has not expired.

### Network security
- All traffic should go through HTTPS (Railway provides TLS by default).
- The manager API is mounted at `/api/manager` — consider restricting this path to known IP ranges using Railway's network policies or a reverse proxy if needed.

### Sensitive data
- Passwords are hashed with bcrypt (cost factor 12) and never stored in plain text.
- `password_hash` is excluded from all API responses.
- Tokens should be treated as secrets — do not log them or include them in error reports.

### Incident response
1. Identify the affected manager account from the audit log.
2. Deactivate the account immediately: `DELETE /api/manager/managers/{id}`.
3. If the `JWT_SECRET` may be compromised, rotate it in Railway environment variables.
4. Review the audit log for all actions taken by the compromised account.
5. Create a CRITICAL alert documenting the incident.
6. Notify stakeholders and follow your organisation's incident response procedure.

---

*Grandcom Gold Signals System — Manager Guide v3.0.2*  
*For technical support, contact the engineering team.*

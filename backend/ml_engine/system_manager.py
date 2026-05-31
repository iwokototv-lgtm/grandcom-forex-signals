"""
System Manager — Role-Based Access Control & Full System Control
Grandcom Gold Signals System v3.0.2

Provides:
  - ManagerRole enum  (ADMIN, MANAGER, VIEWER)
  - SystemManager class with complete CRUD, monitoring, alerting,
    backup control, audit trail, dashboard capabilities, and
    signal management integration
  - Permission matrix enforced on every operation
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME   = os.environ.get("DB_NAME",   "gold_signals_v3")


# ─────────────────────────────────────────────────────────────
# ROLE DEFINITIONS
# ─────────────────────────────────────────────────────────────

class ManagerRole(str, Enum):
    ADMIN   = "ADMIN"    # Full control — all operations
    MANAGER = "MANAGER"  # Operational control — no manager CRUD
    VIEWER  = "VIEWER"   # Read-only — monitoring & dashboard only


# Permission matrix: role → set of allowed action keys
ROLE_PERMISSIONS: Dict[ManagerRole, set] = {
    ManagerRole.ADMIN: {
        # Manager CRUD
        "manager:add", "manager:remove", "manager:update", "manager:list", "manager:get",
        # System monitoring
        "system:status", "system:signals", "system:logs",
        # Alerts
        "alert:create", "alert:resolve", "alert:list",
        # Backups
        "backup:trigger", "backup:history",
        # System control
        "system:restart", "system:deploy",
        # Audit
        "audit:view",
        # Dashboard
        "dashboard:view",
        # Signal management (approve / reject / adjust)
        "signal:approve", "signal:reject", "signal:adjust",
    },
    ManagerRole.MANAGER: {
        # No manager CRUD (cannot add/remove/update other managers)
        "manager:list", "manager:get",
        # System monitoring
        "system:status", "system:signals", "system:logs",
        # Alerts
        "alert:create", "alert:resolve", "alert:list",
        # Backups
        "backup:trigger", "backup:history",
        # System control
        "system:restart", "system:deploy",
        # Audit
        "audit:view",
        # Dashboard
        "dashboard:view",
        # Signal management (approve / reject / adjust)
        "signal:approve", "signal:reject", "signal:adjust",
    },
    ManagerRole.VIEWER: {
        # Read-only
        "manager:list", "manager:get",
        "system:status", "system:signals", "system:logs",
        "alert:list",
        "backup:history",
        "audit:view",
        "dashboard:view",
        # Viewers can only read signals, not mutate them
    },
}


# ─────────────────────────────────────────────────────────────
# HELPER — permission check
# ─────────────────────────────────────────────────────────────

def check_permission(manager: Dict[str, Any], action: str) -> None:
    """
    Raise PermissionError if *manager* does not hold *action*.
    manager dict must contain a 'role' key (ManagerRole value).
    """
    role_str = manager.get("role", "")
    try:
        role = ManagerRole(role_str)
    except ValueError:
        raise PermissionError(f"Unknown manager role: '{role_str}'")

    if action not in ROLE_PERMISSIONS.get(role, set()):
        raise PermissionError(
            f"Role '{role}' does not have permission for action '{action}'"
        )


# ─────────────────────────────────────────────────────────────
# SYSTEM MANAGER
# ─────────────────────────────────────────────────────────────

class SystemManager:
    """
    Central controller for the Grandcom Gold Signals system.

    All mutating operations require a *requesting_manager* dict that
    carries at minimum ``{"manager_id": str, "role": ManagerRole}``.
    Every operation is recorded in the audit log collection.
    """

    def __init__(self) -> None:
        self._client: Optional[AsyncIOMotorClient] = None
        self._db = None

    # ── DB connection (lazy) ──────────────────────────────────

    def _get_db(self):
        if self._client is None:
            self._client = AsyncIOMotorClient(
                MONGO_URL,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
            )
            self._db = self._client[DB_NAME]
        return self._db

    # ── Audit logging ─────────────────────────────────────────

    async def _audit(
        self,
        action: str,
        performed_by: str,
        role: str,
        details: Dict[str, Any],
        success: bool = True,
        error: Optional[str] = None,
    ) -> None:
        """Persist an immutable audit record."""
        try:
            db = self._get_db()
            entry = {
                "audit_id":    str(uuid.uuid4()),
                "timestamp":   datetime.utcnow(),
                "action":      action,
                "performed_by": performed_by,
                "role":        role,
                "details":     details,
                "success":     success,
                "error":       error,
            }
            await db.manager_audit_log.insert_one(entry)
        except Exception as exc:
            logger.error(f"Audit log write failed: {exc}")

    # ═══════════════════════════════════════════════════════════
    # MANAGER MANAGEMENT
    # ═══════════════════════════════════════════════════════════

    async def add_manager(
        self,
        requesting_manager: Dict[str, Any],
        email: str,
        full_name: str,
        role: ManagerRole,
        password_hash: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a new system manager account.
        Requires: ADMIN role (manager:add).
        """
        check_permission(requesting_manager, "manager:add")
        db = self._get_db()

        # Duplicate check
        existing = await db.system_managers.find_one({"email": email})
        if existing:
            await self._audit(
                "manager:add", requesting_manager["manager_id"],
                requesting_manager["role"],
                {"email": email, "role": role},
                success=False, error="Email already registered",
            )
            return {"success": False, "error": "A manager with that email already exists"}

        manager_id = str(uuid.uuid4())
        doc = {
            "manager_id":    manager_id,
            "email":         email,
            "full_name":     full_name,
            "role":          role.value,
            "password_hash": password_hash,
            "is_active":     True,
            "created_at":    datetime.utcnow(),
            "created_by":    requesting_manager["manager_id"],
            "last_login":    None,
            "metadata":      metadata or {},
        }
        await db.system_managers.insert_one(doc)

        await self._audit(
            "manager:add", requesting_manager["manager_id"],
            requesting_manager["role"],
            {"new_manager_id": manager_id, "email": email, "role": role.value},
        )
        logger.info(f"✅ Manager added: {email} ({role.value}) by {requesting_manager['manager_id']}")
        return {
            "success":    True,
            "manager_id": manager_id,
            "email":      email,
            "role":       role.value,
            "created_at": doc["created_at"].isoformat(),
        }

    async def remove_manager(
        self,
        requesting_manager: Dict[str, Any],
        target_manager_id: str,
    ) -> Dict[str, Any]:
        """
        Deactivate (soft-delete) a manager account.
        Requires: ADMIN role (manager:remove).
        Cannot remove yourself.
        """
        check_permission(requesting_manager, "manager:remove")

        if requesting_manager["manager_id"] == target_manager_id:
            return {"success": False, "error": "Cannot remove your own account"}

        db = self._get_db()
        result = await db.system_managers.update_one(
            {"manager_id": target_manager_id},
            {"$set": {"is_active": False, "deactivated_at": datetime.utcnow(),
                      "deactivated_by": requesting_manager["manager_id"]}},
        )
        if result.modified_count == 0:
            return {"success": False, "error": "Manager not found"}

        await self._audit(
            "manager:remove", requesting_manager["manager_id"],
            requesting_manager["role"],
            {"target_manager_id": target_manager_id},
        )
        logger.info(f"✅ Manager deactivated: {target_manager_id}")
        return {"success": True, "message": "Manager deactivated successfully"}

    async def update_manager(
        self,
        requesting_manager: Dict[str, Any],
        target_manager_id: str,
        updates: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Update manager fields (role, full_name, is_active, metadata).
        Requires: ADMIN role (manager:update).
        """
        check_permission(requesting_manager, "manager:update")

        allowed_fields = {"role", "full_name", "is_active", "metadata"}
        sanitised = {k: v for k, v in updates.items() if k in allowed_fields}
        if not sanitised:
            return {"success": False, "error": "No valid fields to update"}

        # Validate role value if being changed
        if "role" in sanitised:
            try:
                sanitised["role"] = ManagerRole(sanitised["role"]).value
            except ValueError:
                return {"success": False, "error": f"Invalid role: {sanitised['role']}"}

        sanitised["updated_at"] = datetime.utcnow()
        sanitised["updated_by"] = requesting_manager["manager_id"]

        db = self._get_db()
        result = await db.system_managers.update_one(
            {"manager_id": target_manager_id},
            {"$set": sanitised},
        )
        if result.modified_count == 0:
            return {"success": False, "error": "Manager not found or no changes made"}

        await self._audit(
            "manager:update", requesting_manager["manager_id"],
            requesting_manager["role"],
            {"target_manager_id": target_manager_id, "fields_updated": list(sanitised.keys())},
        )
        return {"success": True, "message": "Manager updated", "updated_fields": list(sanitised.keys())}

    async def list_managers(
        self,
        requesting_manager: Dict[str, Any],
        include_inactive: bool = False,
    ) -> Dict[str, Any]:
        """
        List all manager accounts.
        Requires: manager:list (all roles).
        """
        check_permission(requesting_manager, "manager:list")
        db = self._get_db()

        query: Dict[str, Any] = {} if include_inactive else {"is_active": True}
        managers = await db.system_managers.find(query, {"password_hash": 0}).to_list(500)

        formatted = []
        for m in managers:
            m.pop("_id", None)
            if m.get("created_at"):
                m["created_at"] = m["created_at"].isoformat()
            if m.get("last_login"):
                m["last_login"] = m["last_login"].isoformat()
            if m.get("deactivated_at"):
                m["deactivated_at"] = m["deactivated_at"].isoformat()
            formatted.append(m)

        return {"success": True, "managers": formatted, "count": len(formatted)}

    async def get_manager(
        self,
        requesting_manager: Dict[str, Any],
        target_manager_id: str,
    ) -> Dict[str, Any]:
        """
        Retrieve a single manager by ID.
        Requires: manager:get (all roles).
        """
        check_permission(requesting_manager, "manager:get")
        db = self._get_db()

        m = await db.system_managers.find_one(
            {"manager_id": target_manager_id}, {"password_hash": 0}
        )
        if not m:
            return {"success": False, "error": "Manager not found"}

        m.pop("_id", None)
        for ts_field in ("created_at", "last_login", "deactivated_at", "updated_at"):
            if m.get(ts_field):
                m[ts_field] = m[ts_field].isoformat()

        return {"success": True, "manager": m}

    # ═══════════════════════════════════════════════════════════
    # SYSTEM MONITORING
    # ═══════════════════════════════════════════════════════════

    async def get_system_status(
        self, requesting_manager: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Full system health snapshot.
        Requires: system:status (all roles).
        """
        check_permission(requesting_manager, "system:status")

        try:
            import psutil
            cpu_pct    = psutil.cpu_percent(interval=0.5)
            mem        = psutil.virtual_memory()
            disk       = psutil.disk_usage("/")
            mem_pct    = mem.percent
            disk_pct   = disk.percent
        except Exception:
            cpu_pct = mem_pct = disk_pct = -1.0

        db = self._get_db()

        # MongoDB ping
        try:
            await asyncio.wait_for(db.command("ping"), timeout=3)
            db_status = "HEALTHY"
        except Exception as exc:
            db_status = f"UNHEALTHY: {exc}"

        # Recent signal count (last 1 h)
        cutoff = datetime.utcnow() - timedelta(hours=1)
        try:
            recent_signals = await db.signals.count_documents(
                {"created_at": {"$gte": cutoff}}
            )
        except Exception:
            recent_signals = -1

        # Active alerts
        try:
            active_alerts = await db.system_alerts.count_documents({"resolved": False})
        except Exception:
            active_alerts = -1

        overall = "HEALTHY"
        if db_status != "HEALTHY" or cpu_pct > 85 or mem_pct > 85 or disk_pct > 90:
            overall = "DEGRADED"

        status = {
            "overall":        overall,
            "timestamp":      datetime.utcnow().isoformat(),
            "version":        "3.0.2",
            "database":       db_status,
            "cpu_percent":    round(cpu_pct, 1),
            "memory_percent": round(mem_pct, 1),
            "disk_percent":   round(disk_pct, 1),
            "signals_last_1h": recent_signals,
            "active_alerts":  active_alerts,
        }

        await self._audit(
            "system:status", requesting_manager["manager_id"],
            requesting_manager["role"], {},
        )
        return {"success": True, "status": status}

    async def get_recent_signals(
        self,
        requesting_manager: Dict[str, Any],
        limit: int = 50,
        hours: int = 24,
    ) -> Dict[str, Any]:
        """
        Retrieve recent trading signals.
        Requires: system:signals (all roles).
        """
        check_permission(requesting_manager, "system:signals")
        db = self._get_db()

        cutoff = datetime.utcnow() - timedelta(hours=hours)
        signals = await (
            db.signals
            .find({"created_at": {"$gte": cutoff}})
            .sort("created_at", -1)
            .limit(max(1, min(limit, 200)))
            .to_list(None)
        )

        formatted = []
        for s in signals:
            s["id"] = str(s.pop("_id"))
            if s.get("created_at"):
                s["created_at"] = s["created_at"].isoformat()
            if s.get("closed_at"):
                s["closed_at"] = s["closed_at"].isoformat()
            formatted.append(s)

        return {
            "success": True,
            "signals": formatted,
            "count":   len(formatted),
            "hours":   hours,
        }

    async def get_system_logs(
        self,
        requesting_manager: Dict[str, Any],
        limit: int = 100,
        level: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Retrieve recent audit / system log entries.
        Requires: system:logs (all roles).
        """
        check_permission(requesting_manager, "system:logs")
        db = self._get_db()

        query: Dict[str, Any] = {}
        if level:
            query["level"] = level.upper()

        logs = await (
            db.manager_audit_log
            .find(query)
            .sort("timestamp", -1)
            .limit(max(1, min(limit, 500)))
            .to_list(None)
        )

        formatted = []
        for entry in logs:
            entry.pop("_id", None)
            if entry.get("timestamp"):
                entry["timestamp"] = entry["timestamp"].isoformat()
            formatted.append(entry)

        return {"success": True, "logs": formatted, "count": len(formatted)}

    # ═══════════════════════════════════════════════════════════
    # ALERT MANAGEMENT
    # ═══════════════════════════════════════════════════════════

    async def create_alert(
        self,
        requesting_manager: Dict[str, Any],
        title: str,
        message: str,
        severity: str = "INFO",
        category: str = "GENERAL",
    ) -> Dict[str, Any]:
        """
        Create a system alert.
        Requires: alert:create (ADMIN, MANAGER).
        severity: INFO | WARNING | CRITICAL
        category: GENERAL | TRADING | SYSTEM | SECURITY
        """
        check_permission(requesting_manager, "alert:create")

        valid_severities = {"INFO", "WARNING", "CRITICAL"}
        valid_categories = {"GENERAL", "TRADING", "SYSTEM", "SECURITY"}
        severity = severity.upper()
        category = category.upper()

        if severity not in valid_severities:
            return {"success": False, "error": f"Invalid severity. Choose from: {valid_severities}"}
        if category not in valid_categories:
            return {"success": False, "error": f"Invalid category. Choose from: {valid_categories}"}

        db = self._get_db()
        alert_id = str(uuid.uuid4())
        doc = {
            "alert_id":   alert_id,
            "title":      title,
            "message":    message,
            "severity":   severity,
            "category":   category,
            "resolved":   False,
            "created_at": datetime.utcnow(),
            "created_by": requesting_manager["manager_id"],
            "resolved_at":  None,
            "resolved_by":  None,
            "resolution_note": None,
        }
        await db.system_alerts.insert_one(doc)

        await self._audit(
            "alert:create", requesting_manager["manager_id"],
            requesting_manager["role"],
            {"alert_id": alert_id, "severity": severity, "title": title},
        )
        logger.info(f"🚨 Alert created [{severity}]: {title}")
        return {
            "success":    True,
            "alert_id":   alert_id,
            "severity":   severity,
            "created_at": doc["created_at"].isoformat(),
        }

    async def resolve_alert(
        self,
        requesting_manager: Dict[str, Any],
        alert_id: str,
        resolution_note: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Mark an alert as resolved.
        Requires: alert:resolve (ADMIN, MANAGER).
        """
        check_permission(requesting_manager, "alert:resolve")
        db = self._get_db()

        result = await db.system_alerts.update_one(
            {"alert_id": alert_id, "resolved": False},
            {"$set": {
                "resolved":        True,
                "resolved_at":     datetime.utcnow(),
                "resolved_by":     requesting_manager["manager_id"],
                "resolution_note": resolution_note or "",
            }},
        )
        if result.modified_count == 0:
            return {"success": False, "error": "Alert not found or already resolved"}

        await self._audit(
            "alert:resolve", requesting_manager["manager_id"],
            requesting_manager["role"],
            {"alert_id": alert_id, "resolution_note": resolution_note},
        )
        return {"success": True, "message": "Alert resolved"}

    async def list_alerts(
        self,
        requesting_manager: Dict[str, Any],
        include_resolved: bool = False,
        severity: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """
        List system alerts.
        Requires: alert:list (all roles).
        """
        check_permission(requesting_manager, "alert:list")
        db = self._get_db()

        query: Dict[str, Any] = {}
        if not include_resolved:
            query["resolved"] = False
        if severity:
            query["severity"] = severity.upper()

        alerts = await (
            db.system_alerts
            .find(query)
            .sort("created_at", -1)
            .limit(max(1, min(limit, 200)))
            .to_list(None)
        )

        formatted = []
        for a in alerts:
            a.pop("_id", None)
            for ts in ("created_at", "resolved_at"):
                if a.get(ts):
                    a[ts] = a[ts].isoformat()
            formatted.append(a)

        return {"success": True, "alerts": formatted, "count": len(formatted)}

    # ═══════════════════════════════════════════════════════════
    # BACKUP MANAGEMENT
    # ═══════════════════════════════════════════════════════════

    async def trigger_backup(
        self,
        requesting_manager: Dict[str, Any],
        backup_type: str = "full",
    ) -> Dict[str, Any]:
        """
        Trigger an on-demand backup.
        Requires: backup:trigger (ADMIN, MANAGER).
        backup_type: full | signals | models
        """
        check_permission(requesting_manager, "backup:trigger")

        valid_types = {"full", "signals", "models"}
        backup_type = backup_type.lower()
        if backup_type not in valid_types:
            return {"success": False, "error": f"Invalid backup_type. Choose from: {valid_types}"}

        db = self._get_db()
        backup_id = str(uuid.uuid4())
        record = {
            "backup_id":    backup_id,
            "backup_type":  backup_type,
            "status":       "INITIATED",
            "triggered_by": requesting_manager["manager_id"],
            "triggered_at": datetime.utcnow(),
            "completed_at": None,
            "result":       None,
        }
        await db.backup_history.insert_one(record)

        # Run the actual backup asynchronously
        asyncio.create_task(
            self._run_backup(backup_id, backup_type, requesting_manager)
        )

        await self._audit(
            "backup:trigger", requesting_manager["manager_id"],
            requesting_manager["role"],
            {"backup_id": backup_id, "backup_type": backup_type},
        )
        return {
            "success":      True,
            "backup_id":    backup_id,
            "backup_type":  backup_type,
            "status":       "INITIATED",
            "triggered_at": record["triggered_at"].isoformat(),
            "message":      "Backup initiated. Check backup history for status.",
        }

    async def _run_backup(
        self,
        backup_id: str,
        backup_type: str,
        requesting_manager: Dict[str, Any],
    ) -> None:
        """Internal: execute backup and update history record."""
        db = self._get_db()
        try:
            from ml_engine.backup_manager import BackupManager
            bm = BackupManager()

            if backup_type == "signals":
                result = await bm.backup_signals(days=7)
            elif backup_type == "models":
                result = await bm.backup_models({})
            else:  # full
                result = await bm.backup_database()

            await db.backup_history.update_one(
                {"backup_id": backup_id},
                {"$set": {
                    "status":       "COMPLETED" if result.get("success") else "FAILED",
                    "completed_at": datetime.utcnow(),
                    "result":       result,
                }},
            )
            logger.info(f"✅ Backup {backup_id} ({backup_type}) completed")
        except Exception as exc:
            logger.error(f"❌ Backup {backup_id} failed: {exc}")
            await db.backup_history.update_one(
                {"backup_id": backup_id},
                {"$set": {
                    "status":       "FAILED",
                    "completed_at": datetime.utcnow(),
                    "result":       {"success": False, "error": str(exc)},
                }},
            )

    async def get_backup_history(
        self,
        requesting_manager: Dict[str, Any],
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        Retrieve backup history.
        Requires: backup:history (all roles).
        """
        check_permission(requesting_manager, "backup:history")
        db = self._get_db()

        records = await (
            db.backup_history
            .find({})
            .sort("triggered_at", -1)
            .limit(max(1, min(limit, 100)))
            .to_list(None)
        )

        formatted = []
        for r in records:
            r.pop("_id", None)
            for ts in ("triggered_at", "completed_at"):
                if r.get(ts):
                    r[ts] = r[ts].isoformat()
            formatted.append(r)

        return {"success": True, "backups": formatted, "count": len(formatted)}

    # ═══════════════════════════════════════════════════════════
    # SYSTEM CONTROL
    # ═══════════════════════════════════════════════════════════

    async def restart_service(
        self,
        requesting_manager: Dict[str, Any],
        service_name: str = "api",
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Record a service restart request and log it.
        Requires: system:restart (ADMIN, MANAGER).

        On Railway, a restart is triggered by the platform; this method
        records the intent and creates a CRITICAL alert so operators are
        notified. Actual process restart must be triggered via Railway CLI
        or the Railway dashboard.
        """
        check_permission(requesting_manager, "system:restart")

        valid_services = {"api", "signal_generator", "outcome_tracker", "all"}
        if service_name not in valid_services:
            return {
                "success": False,
                "error":   f"Unknown service '{service_name}'. Valid: {valid_services}",
            }

        db = self._get_db()
        restart_id = str(uuid.uuid4())
        record = {
            "restart_id":    restart_id,
            "service":       service_name,
            "reason":        reason or "Manual restart requested",
            "requested_by":  requesting_manager["manager_id"],
            "requested_at":  datetime.utcnow(),
            "status":        "REQUESTED",
        }
        await db.system_control_log.insert_one(record)

        # Create a CRITICAL alert so all managers are aware
        await self.create_alert(
            requesting_manager,
            title=f"Service Restart Requested: {service_name}",
            message=(
                f"Manager {requesting_manager['manager_id']} requested restart of "
                f"'{service_name}'. Reason: {reason or 'Not specified'}"
            ),
            severity="CRITICAL",
            category="SYSTEM",
        )

        await self._audit(
            "system:restart", requesting_manager["manager_id"],
            requesting_manager["role"],
            {"restart_id": restart_id, "service": service_name, "reason": reason},
        )
        logger.warning(
            f"⚠️ Restart requested for '{service_name}' by {requesting_manager['manager_id']}"
        )
        return {
            "success":      True,
            "restart_id":   restart_id,
            "service":      service_name,
            "status":       "REQUESTED",
            "message":      (
                "Restart request logged. Trigger the actual restart via Railway dashboard "
                "or CLI: `railway service restart`"
            ),
        }

    async def deploy_update(
        self,
        requesting_manager: Dict[str, Any],
        version: str,
        description: Optional[str] = None,
        rollback: bool = False,
    ) -> Dict[str, Any]:
        """
        Record a deployment / rollback event.
        Requires: system:deploy (ADMIN, MANAGER).
        """
        check_permission(requesting_manager, "system:deploy")

        db = self._get_db()
        deploy_id = str(uuid.uuid4())
        action    = "ROLLBACK" if rollback else "DEPLOY"
        record = {
            "deploy_id":    deploy_id,
            "action":       action,
            "version":      version,
            "description":  description or "",
            "requested_by": requesting_manager["manager_id"],
            "requested_at": datetime.utcnow(),
            "status":       "REQUESTED",
        }
        await db.system_control_log.insert_one(record)

        await self.create_alert(
            requesting_manager,
            title=f"{action}: v{version}",
            message=(
                f"Manager {requesting_manager['manager_id']} initiated {action.lower()} "
                f"to version {version}. {description or ''}"
            ),
            severity="WARNING",
            category="SYSTEM",
        )

        await self._audit(
            "system:deploy", requesting_manager["manager_id"],
            requesting_manager["role"],
            {"deploy_id": deploy_id, "action": action, "version": version},
        )
        return {
            "success":     True,
            "deploy_id":   deploy_id,
            "action":      action,
            "version":     version,
            "status":      "REQUESTED",
            "message":     (
                f"{action} to v{version} logged. Push to Railway via git or "
                "Railway CLI to apply."
            ),
        }

    # ═══════════════════════════════════════════════════════════
    # AUDIT LOG
    # ═══════════════════════════════════════════════════════════

    async def get_audit_log(
        self,
        requesting_manager: Dict[str, Any],
        limit: int = 100,
        manager_id_filter: Optional[str] = None,
        action_filter: Optional[str] = None,
        since_hours: int = 168,  # 7 days default
    ) -> Dict[str, Any]:
        """
        Retrieve the audit trail.
        Requires: audit:view (all roles).
        """
        check_permission(requesting_manager, "audit:view")
        db = self._get_db()

        cutoff = datetime.utcnow() - timedelta(hours=since_hours)
        query: Dict[str, Any] = {"timestamp": {"$gte": cutoff}}
        if manager_id_filter:
            query["performed_by"] = manager_id_filter
        if action_filter:
            query["action"] = action_filter

        entries = await (
            db.manager_audit_log
            .find(query)
            .sort("timestamp", -1)
            .limit(max(1, min(limit, 1000)))
            .to_list(None)
        )

        formatted = []
        for e in entries:
            e.pop("_id", None)
            if e.get("timestamp"):
                e["timestamp"] = e["timestamp"].isoformat()
            formatted.append(e)

        return {
            "success":     True,
            "audit_log":   formatted,
            "count":       len(formatted),
            "since_hours": since_hours,
        }

    # ═══════════════════════════════════════════════════════════
    # DASHBOARD
    # ═══════════════════════════════════════════════════════════

    async def get_dashboard(
        self, requesting_manager: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Full system overview dashboard.
        Requires: dashboard:view (all roles).
        """
        check_permission(requesting_manager, "dashboard:view")
        db = self._get_db()

        # Gather all data concurrently
        now = datetime.utcnow()

        async def _count(collection: str, query: Dict) -> int:
            try:
                return await db[collection].count_documents(query)
            except Exception:
                return -1

        (
            total_managers,
            active_managers,
            total_signals_24h,
            active_signals,
            open_alerts,
            critical_alerts,
            recent_backups,
        ) = await asyncio.gather(
            _count("system_managers", {}),
            _count("system_managers", {"is_active": True}),
            _count("signals", {"created_at": {"$gte": now - timedelta(hours=24)}}),
            _count("signals", {"status": "ACTIVE"}),
            _count("system_alerts", {"resolved": False}),
            _count("system_alerts", {"resolved": False, "severity": "CRITICAL"}),
            _count("backup_history", {"triggered_at": {"$gte": now - timedelta(days=7)}}),
        )

        # Last 5 audit entries
        recent_audit = await (
            db.manager_audit_log
            .find({})
            .sort("timestamp", -1)
            .limit(5)
            .to_list(None)
        )
        for e in recent_audit:
            e.pop("_id", None)
            if e.get("timestamp"):
                e["timestamp"] = e["timestamp"].isoformat()

        # System health
        try:
            import psutil
            cpu_pct  = psutil.cpu_percent(interval=0.3)
            mem_pct  = psutil.virtual_memory().percent
            disk_pct = psutil.disk_usage("/").percent
        except Exception:
            cpu_pct = mem_pct = disk_pct = -1.0

        dashboard = {
            "generated_at": now.isoformat(),
            "system_version": "3.0.2",
            "managers": {
                "total":  total_managers,
                "active": active_managers,
            },
            "trading": {
                "signals_last_24h": total_signals_24h,
                "active_signals":   active_signals,
            },
            "alerts": {
                "open":     open_alerts,
                "critical": critical_alerts,
            },
            "backups": {
                "last_7_days": recent_backups,
            },
            "infrastructure": {
                "cpu_percent":    round(cpu_pct, 1),
                "memory_percent": round(mem_pct, 1),
                "disk_percent":   round(disk_pct, 1),
                "health": (
                    "HEALTHY"
                    if cpu_pct < 85 and mem_pct < 85 and disk_pct < 90
                    else "DEGRADED"
                ),
            },
            "recent_activity": recent_audit,
        }

        await self._audit(
            "dashboard:view", requesting_manager["manager_id"],
            requesting_manager["role"], {},
        )
        return {"success": True, "dashboard": dashboard}

    # ═══════════════════════════════════════════════════════════
    # AUTHENTICATION HELPER
    # ═══════════════════════════════════════════════════════════

    async def authenticate_manager(
        self, email: str, password_hash: str
    ) -> Optional[Dict[str, Any]]:
        """
        Look up a manager by email and verify the pre-hashed password.
        Returns the manager dict (without password_hash) on success, else None.
        The caller is responsible for hashing the raw password before calling this.
        """
        db = self._get_db()
        m = await db.system_managers.find_one(
            {"email": email, "is_active": True}
        )
        if not m:
            return None
        if m.get("password_hash") != password_hash:
            return None

        # Update last_login
        await db.system_managers.update_one(
            {"manager_id": m["manager_id"]},
            {"$set": {"last_login": datetime.utcnow()}},
        )
        m.pop("_id", None)
        m.pop("password_hash", None)
        return m

    # ═══════════════════════════════════════════════════════════
    # SIGNAL MANAGEMENT INTEGRATION
    # ═══════════════════════════════════════════════════════════

    async def get_pending_signals(
        self,
        requesting_manager: Dict[str, Any],
        pair: Optional[str] = None,
        limit: int = 50,
        min_confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Proxy to SignalManager.get_pending_signals.
        Requires: system:signals (all roles).
        """
        try:
            from ml_engine.signal_manager import signal_manager as _sm
            return await _sm.get_pending_signals(
                requesting_manager,
                pair=pair,
                limit=limit,
                min_confidence=min_confidence,
            )
        except Exception as exc:
            logger.error(f"get_pending_signals error: {exc}")
            return {"success": False, "error": str(exc)}

    async def get_signal_details(
        self,
        requesting_manager: Dict[str, Any],
        signal_id: str,
    ) -> Dict[str, Any]:
        """
        Proxy to SignalManager.get_signal_details.
        Requires: system:signals (all roles).
        """
        try:
            from ml_engine.signal_manager import signal_manager as _sm
            return await _sm.get_signal_details(requesting_manager, signal_id)
        except Exception as exc:
            logger.error(f"get_signal_details error: {exc}")
            return {"success": False, "error": str(exc)}

    async def approve_signal(
        self,
        requesting_manager: Dict[str, Any],
        signal_id: str,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Proxy to SignalManager.approve_signal.
        Requires: signal:approve (ADMIN, MANAGER).
        """
        try:
            from ml_engine.signal_manager import signal_manager as _sm
            result = await _sm.approve_signal(requesting_manager, signal_id, notes)
            if result.get("success"):
                await self._audit(
                    "signal:approve",
                    requesting_manager["manager_id"],
                    requesting_manager["role"],
                    {"signal_id": signal_id, "notes": notes},
                )
            return result
        except Exception as exc:
            logger.error(f"approve_signal error: {exc}")
            return {"success": False, "error": str(exc)}

    async def reject_signal(
        self,
        requesting_manager: Dict[str, Any],
        signal_id: str,
        reason: str,
    ) -> Dict[str, Any]:
        """
        Proxy to SignalManager.reject_signal.
        Requires: signal:reject (ADMIN, MANAGER).
        """
        try:
            from ml_engine.signal_manager import signal_manager as _sm
            result = await _sm.reject_signal(requesting_manager, signal_id, reason)
            if result.get("success"):
                await self._audit(
                    "signal:reject",
                    requesting_manager["manager_id"],
                    requesting_manager["role"],
                    {"signal_id": signal_id, "reason": reason},
                )
            return result
        except Exception as exc:
            logger.error(f"reject_signal error: {exc}")
            return {"success": False, "error": str(exc)}

    async def adjust_signal(
        self,
        requesting_manager: Dict[str, Any],
        signal_id: str,
        entry_price: Optional[float] = None,
        tp_levels: Optional[List[float]] = None,
        sl_price: Optional[float] = None,
        adjustment_notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Proxy to SignalManager.adjust_signal.
        Requires: signal:adjust (ADMIN, MANAGER).
        """
        try:
            from ml_engine.signal_manager import signal_manager as _sm
            result = await _sm.adjust_signal(
                requesting_manager,
                signal_id=signal_id,
                entry_price=entry_price,
                tp_levels=tp_levels,
                sl_price=sl_price,
                adjustment_notes=adjustment_notes,
            )
            if result.get("success"):
                await self._audit(
                    "signal:adjust",
                    requesting_manager["manager_id"],
                    requesting_manager["role"],
                    {
                        "signal_id":        signal_id,
                        "entry_price":      entry_price,
                        "tp_levels":        tp_levels,
                        "sl_price":         sl_price,
                        "adjustment_notes": adjustment_notes,
                    },
                )
            return result
        except Exception as exc:
            logger.error(f"adjust_signal error: {exc}")
            return {"success": False, "error": str(exc)}

    async def get_signal_history(
        self,
        requesting_manager: Dict[str, Any],
        status: Optional[str] = None,
        pair: Optional[str] = None,
        reviewed_by: Optional[str] = None,
        hours: int = 168,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        Proxy to SignalManager.get_signal_history.
        Requires: system:signals (all roles).
        """
        try:
            from ml_engine.signal_manager import signal_manager as _sm
            return await _sm.get_signal_history(
                requesting_manager,
                status=status,
                pair=pair,
                reviewed_by=reviewed_by,
                hours=hours,
                limit=limit,
            )
        except Exception as exc:
            logger.error(f"get_signal_history error: {exc}")
            return {"success": False, "error": str(exc)}

    async def get_approval_stats(
        self,
        requesting_manager: Dict[str, Any],
        hours: int = 168,
        manager_id_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Proxy to SignalManager.get_approval_stats.
        Requires: system:signals (all roles).
        """
        try:
            from ml_engine.signal_manager import signal_manager as _sm
            return await _sm.get_approval_stats(
                requesting_manager,
                hours=hours,
                manager_id_filter=manager_id_filter,
            )
        except Exception as exc:
            logger.error(f"get_approval_stats error: {exc}")
            return {"success": False, "error": str(exc)}


# ─────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────

system_manager = SystemManager()

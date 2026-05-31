"""
Manager API — FastAPI router for System Manager operations
Grandcom Gold Signals System v3.0.2

Mounts at: /api/manager
All endpoints require a valid JWT that resolves to a system_manager document.
Permission enforcement is delegated to SystemManager (raises PermissionError
which is caught here and returned as HTTP 403).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorClient
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field

from ml_engine.system_manager import ManagerRole, SystemManager, system_manager

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────

MONGO_URL      = os.environ.get("MONGO_URL",      "mongodb://localhost:27017")
DB_NAME        = os.environ.get("DB_NAME",        "gold_signals_v3")
JWT_SECRET     = os.environ.get("JWT_SECRET",     "your-secret-key")
JWT_ALGORITHM  = os.environ.get("JWT_ALGORITHM",  "HS256")
JWT_EXPIRY_HRS = int(os.environ.get("JWT_EXPIRATION_HOURS", "24"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security    = HTTPBearer()

router = APIRouter(prefix="/api/manager", tags=["System Manager"])

# ─────────────────────────────────────────────────────────────
# DB helper
# ─────────────────────────────────────────────────────────────

_client: Optional[AsyncIOMotorClient] = None


def _get_db():
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(
            MONGO_URL,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=10000,
        )
    return _client[DB_NAME]


# ─────────────────────────────────────────────────────────────
# JWT helpers
# ─────────────────────────────────────────────────────────────

def _create_manager_token(manager_id: str, role: str) -> str:
    payload = {
        "sub":        manager_id,
        "role":       role,
        "type":       "manager",
        "exp":        datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HRS),
        "issued_at":  datetime.utcnow().isoformat(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


async def get_current_manager(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Dict[str, Any]:
    """
    Decode the Bearer JWT and return the system_manager document.
    Raises HTTP 401 on any auth failure.
    """
    try:
        token   = credentials.credentials
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

        if payload.get("type") != "manager":
            raise HTTPException(status_code=401, detail="Token is not a manager token")

        manager_id = payload.get("sub")
        if not manager_id:
            raise HTTPException(status_code=401, detail="Invalid token payload")

        db = _get_db()
        manager = await db.system_managers.find_one(
            {"manager_id": manager_id, "is_active": True},
            {"password_hash": 0},
        )
        if not manager:
            raise HTTPException(status_code=401, detail="Manager account not found or inactive")

        manager.pop("_id", None)
        return manager

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def _handle_permission_error(exc: PermissionError) -> None:
    raise HTTPException(status_code=403, detail=str(exc))


# ─────────────────────────────────────────────────────────────
# Pydantic request models
# ─────────────────────────────────────────────────────────────

class ManagerLoginRequest(BaseModel):
    email:    EmailStr
    password: str


class AddManagerRequest(BaseModel):
    email:     EmailStr
    full_name: str
    role:      ManagerRole
    password:  str
    metadata:  Optional[Dict[str, Any]] = None


class UpdateManagerRequest(BaseModel):
    role:       Optional[ManagerRole]  = None
    full_name:  Optional[str]          = None
    is_active:  Optional[bool]         = None
    metadata:   Optional[Dict[str, Any]] = None


class CreateAlertRequest(BaseModel):
    title:    str
    message:  str
    severity: str = Field(default="INFO",    description="INFO | WARNING | CRITICAL")
    category: str = Field(default="GENERAL", description="GENERAL | TRADING | SYSTEM | SECURITY")


class ResolveAlertRequest(BaseModel):
    resolution_note: Optional[str] = None


class TriggerBackupRequest(BaseModel):
    backup_type: str = Field(default="full", description="full | signals | models")


class RestartServiceRequest(BaseModel):
    service_name: str  = Field(default="api", description="api | signal_generator | outcome_tracker | all")
    reason:       Optional[str] = None


class DeployUpdateRequest(BaseModel):
    version:     str
    description: Optional[str] = None
    rollback:    bool = False


# ─────────────────────────────────────────────────────────────
# AUTH ENDPOINTS
# ─────────────────────────────────────────────────────────────

@router.post("/auth/login", summary="Manager login — returns JWT")
async def manager_login(body: ManagerLoginRequest):
    """
    Authenticate a system manager and return a signed JWT.
    The token must be passed as ``Authorization: Bearer <token>`` on all
    subsequent requests.
    """
    db = _get_db()
    manager = await db.system_managers.find_one(
        {"email": body.email, "is_active": True}
    )
    if not manager:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not pwd_context.verify(body.password, manager.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Update last_login
    await db.system_managers.update_one(
        {"manager_id": manager["manager_id"]},
        {"$set": {"last_login": datetime.utcnow()}},
    )

    token = _create_manager_token(manager["manager_id"], manager["role"])
    return {
        "success":      True,
        "access_token": token,
        "token_type":   "bearer",
        "manager_id":   manager["manager_id"],
        "role":         manager["role"],
        "full_name":    manager.get("full_name"),
        "expires_in":   f"{JWT_EXPIRY_HRS}h",
    }


@router.get("/auth/me", summary="Get current manager profile")
async def get_my_profile(current_manager: Dict = Depends(get_current_manager)):
    """Return the authenticated manager's own profile."""
    profile = dict(current_manager)
    for ts in ("created_at", "last_login", "updated_at"):
        if profile.get(ts) and hasattr(profile[ts], "isoformat"):
            profile[ts] = profile[ts].isoformat()
    return {"success": True, "manager": profile}


# ─────────────────────────────────────────────────────────────
# MANAGER MANAGEMENT ENDPOINTS
# ─────────────────────────────────────────────────────────────

@router.post("/managers", summary="Add a new system manager [ADMIN]")
async def add_manager(
    body:            AddManagerRequest,
    current_manager: Dict = Depends(get_current_manager),
):
    """Create a new manager account. Requires ADMIN role."""
    try:
        hashed = pwd_context.hash(body.password)
        result = await system_manager.add_manager(
            requesting_manager=current_manager,
            email=body.email,
            full_name=body.full_name,
            role=body.role,
            password_hash=hashed,
            metadata=body.metadata,
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.delete("/managers/{manager_id}", summary="Deactivate a manager [ADMIN]")
async def remove_manager(
    manager_id:      str,
    current_manager: Dict = Depends(get_current_manager),
):
    """Soft-delete (deactivate) a manager account. Requires ADMIN role."""
    try:
        result = await system_manager.remove_manager(current_manager, manager_id)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.put("/managers/{manager_id}", summary="Update a manager [ADMIN]")
async def update_manager(
    manager_id:      str,
    body:            UpdateManagerRequest,
    current_manager: Dict = Depends(get_current_manager),
):
    """Update manager role, name, or active status. Requires ADMIN role."""
    try:
        updates = body.model_dump(exclude_none=True)
        if "role" in updates:
            updates["role"] = updates["role"].value
        result = await system_manager.update_manager(current_manager, manager_id, updates)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get("/managers", summary="List all managers")
async def list_managers(
    include_inactive: bool = Query(default=False, description="Include deactivated managers"),
    current_manager:  Dict = Depends(get_current_manager),
):
    """List system managers. All roles can call this endpoint."""
    try:
        return await system_manager.list_managers(current_manager, include_inactive)
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get("/managers/{manager_id}", summary="Get a single manager")
async def get_manager(
    manager_id:      str,
    current_manager: Dict = Depends(get_current_manager),
):
    """Retrieve a manager by ID. All roles can call this endpoint."""
    try:
        result = await system_manager.get_manager(current_manager, manager_id)
        if not result["success"]:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


# ─────────────────────────────────────────────────────────────
# SYSTEM MONITORING ENDPOINTS
# ─────────────────────────────────────────────────────────────

@router.get("/system/status", summary="System health status")
async def get_system_status(current_manager: Dict = Depends(get_current_manager)):
    """Full system health snapshot (CPU, memory, disk, DB, alerts). All roles."""
    try:
        return await system_manager.get_system_status(current_manager)
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get("/system/signals", summary="Recent trading signals")
async def get_recent_signals(
    limit:           int  = Query(default=50,  ge=1, le=200),
    hours:           int  = Query(default=24,  ge=1, le=168),
    current_manager: Dict = Depends(get_current_manager),
):
    """Retrieve recent trading signals. All roles."""
    try:
        return await system_manager.get_recent_signals(current_manager, limit, hours)
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get("/system/logs", summary="System & audit logs")
async def get_system_logs(
    limit:           int           = Query(default=100, ge=1, le=500),
    level:           Optional[str] = Query(default=None, description="Filter by log level"),
    current_manager: Dict          = Depends(get_current_manager),
):
    """Retrieve recent system log entries. All roles."""
    try:
        return await system_manager.get_system_logs(current_manager, limit, level)
    except PermissionError as exc:
        _handle_permission_error(exc)


# ─────────────────────────────────────────────────────────────
# ALERT MANAGEMENT ENDPOINTS
# ─────────────────────────────────────────────────────────────

@router.post("/alerts", summary="Create a system alert [ADMIN, MANAGER]")
async def create_alert(
    body:            CreateAlertRequest,
    current_manager: Dict = Depends(get_current_manager),
):
    """Create a new system alert. Requires ADMIN or MANAGER role."""
    try:
        result = await system_manager.create_alert(
            current_manager,
            title=body.title,
            message=body.message,
            severity=body.severity,
            category=body.category,
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.post("/alerts/{alert_id}/resolve", summary="Resolve an alert [ADMIN, MANAGER]")
async def resolve_alert(
    alert_id:        str,
    body:            ResolveAlertRequest,
    current_manager: Dict = Depends(get_current_manager),
):
    """Mark an alert as resolved. Requires ADMIN or MANAGER role."""
    try:
        result = await system_manager.resolve_alert(
            current_manager, alert_id, body.resolution_note
        )
        if not result["success"]:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get("/alerts", summary="List system alerts")
async def list_alerts(
    include_resolved: bool          = Query(default=False),
    severity:         Optional[str] = Query(default=None, description="INFO | WARNING | CRITICAL"),
    limit:            int           = Query(default=50, ge=1, le=200),
    current_manager:  Dict          = Depends(get_current_manager),
):
    """List system alerts. All roles."""
    try:
        return await system_manager.list_alerts(
            current_manager, include_resolved, severity, limit
        )
    except PermissionError as exc:
        _handle_permission_error(exc)


# ─────────────────────────────────────────────────────────────
# BACKUP MANAGEMENT ENDPOINTS
# ─────────────────────────────────────────────────────────────

@router.post("/backups/trigger", summary="Trigger an on-demand backup [ADMIN, MANAGER]")
async def trigger_backup(
    body:            TriggerBackupRequest,
    current_manager: Dict = Depends(get_current_manager),
):
    """
    Initiate an on-demand backup (full | signals | models).
    Requires ADMIN or MANAGER role.
    """
    try:
        result = await system_manager.trigger_backup(current_manager, body.backup_type)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get("/backups/history", summary="Backup history")
async def get_backup_history(
    limit:           int  = Query(default=20, ge=1, le=100),
    current_manager: Dict = Depends(get_current_manager),
):
    """Retrieve backup history. All roles."""
    try:
        return await system_manager.get_backup_history(current_manager, limit)
    except PermissionError as exc:
        _handle_permission_error(exc)


# ─────────────────────────────────────────────────────────────
# SYSTEM CONTROL ENDPOINTS
# ─────────────────────────────────────────────────────────────

@router.post("/system/restart", summary="Request service restart [ADMIN, MANAGER]")
async def restart_service(
    body:            RestartServiceRequest,
    current_manager: Dict = Depends(get_current_manager),
):
    """
    Log a service restart request and create a CRITICAL alert.
    Requires ADMIN or MANAGER role.
    Actual restart must be applied via Railway dashboard or CLI.
    """
    try:
        result = await system_manager.restart_service(
            current_manager, body.service_name, body.reason
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.post("/system/deploy", summary="Record a deployment or rollback [ADMIN, MANAGER]")
async def deploy_update(
    body:            DeployUpdateRequest,
    current_manager: Dict = Depends(get_current_manager),
):
    """
    Record a deployment or rollback event and notify via alert.
    Requires ADMIN or MANAGER role.
    """
    try:
        result = await system_manager.deploy_update(
            current_manager,
            version=body.version,
            description=body.description,
            rollback=body.rollback,
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


# ─────────────────────────────────────────────────────────────
# AUDIT LOG ENDPOINT
# ─────────────────────────────────────────────────────────────

@router.get("/audit", summary="View audit trail")
async def get_audit_log(
    limit:             int           = Query(default=100, ge=1, le=1000),
    manager_id_filter: Optional[str] = Query(default=None, alias="manager_id"),
    action_filter:     Optional[str] = Query(default=None, alias="action"),
    since_hours:       int           = Query(default=168, ge=1, le=8760, description="Hours to look back (default 7 days)"),
    current_manager:   Dict          = Depends(get_current_manager),
):
    """
    Retrieve the full audit trail with optional filters.
    All roles can view the audit log.
    """
    try:
        return await system_manager.get_audit_log(
            current_manager,
            limit=limit,
            manager_id_filter=manager_id_filter,
            action_filter=action_filter,
            since_hours=since_hours,
        )
    except PermissionError as exc:
        _handle_permission_error(exc)


# ─────────────────────────────────────────────────────────────
# DASHBOARD ENDPOINT
# ─────────────────────────────────────────────────────────────

@router.get("/dashboard", summary="Full system overview dashboard")
async def get_dashboard(current_manager: Dict = Depends(get_current_manager)):
    """
    Aggregated dashboard: managers, trading stats, alerts, backups,
    infrastructure health, and recent activity. All roles.
    """
    try:
        return await system_manager.get_dashboard(current_manager)
    except PermissionError as exc:
        _handle_permission_error(exc)


# ─────────────────────────────────────────────────────────────
# ROLES / PERMISSIONS INFO (public — no auth required)
# ─────────────────────────────────────────────────────────────

@router.get("/roles", summary="List available roles and their permissions", include_in_schema=True)
async def list_roles():
    """Return the role → permissions matrix. No authentication required."""
    from ml_engine.system_manager import ROLE_PERMISSIONS
    return {
        "success": True,
        "roles": {
            role.value: sorted(perms)
            for role, perms in ROLE_PERMISSIONS.items()
        },
    }

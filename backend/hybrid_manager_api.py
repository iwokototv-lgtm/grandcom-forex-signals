"""
Hybrid Manager API — Enterprise-Grade Multi-Tier Approval Workflow
Gold Trading System v3.0.2

Mounts at: /api/hybrid
All endpoints require a valid JWT that resolves to a hybrid_manager document.

Endpoint groups (50+ endpoints):
  /api/hybrid/auth          — Login, profile, token refresh
  /api/hybrid/managers      — Manager CRUD (SUPER_ADMIN only)
  /api/hybrid/signals       — Signal management & workflow
  /api/hybrid/workflow      — Approval workflow actions
  /api/hybrid/risk          — Risk management & limits
  /api/hybrid/analytics     — Performance analytics & reporting
  /api/hybrid/compliance    — Audit log & compliance reports
  /api/hybrid/collab        — Team collaboration (comments, notes)
  /api/hybrid/alerts        — Alert management
  /api/hybrid/dashboard     — Real-time monitoring dashboard
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorClient
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field, field_validator

from ml_engine.hybrid_manager_system import (
    HybridManagerRole,
    HybridManagerSystem,
    SignalStatus,
    AlertSeverity,
    AlertCategory,
    check_hybrid_permission,
    hybrid_manager_system,
    HYBRID_ROLE_PERMISSIONS,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

MONGO_URL      = os.environ.get("MONGO_URL",      "mongodb://localhost:27017")
DB_NAME        = os.environ.get("DB_NAME",        "gold_signals_v3")
JWT_SECRET     = os.environ.get("JWT_SECRET",     "your-secret-key")
JWT_ALGORITHM  = os.environ.get("JWT_ALGORITHM",  "HS256")
JWT_EXPIRY_HRS = int(os.environ.get("JWT_EXPIRATION_HOURS", "24"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security    = HTTPBearer()

router = APIRouter(prefix="/api/hybrid", tags=["Hybrid Manager System"])

# ─────────────────────────────────────────────────────────────────────────────
# DB helper
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# JWT helpers
# ─────────────────────────────────────────────────────────────────────────────

def _create_hybrid_token(manager_id: str, role: str) -> str:
    payload = {
        "sub":       manager_id,
        "role":      role,
        "type":      "hybrid_manager",
        "exp":       datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HRS),
        "issued_at": datetime.utcnow().isoformat(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


async def get_current_hybrid_manager(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Dict[str, Any]:
    """Decode Bearer JWT and return the hybrid_manager document."""
    try:
        token   = credentials.credentials
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

        if payload.get("type") not in ("hybrid_manager", "manager"):
            raise HTTPException(status_code=401, detail="Token is not a hybrid manager token")

        manager_id = payload.get("sub")
        if not manager_id:
            raise HTTPException(status_code=401, detail="Invalid token payload")

        db = _get_db()
        # Check hybrid_managers first, fall back to system_managers
        manager = await db.hybrid_managers.find_one(
            {"manager_id": manager_id, "is_active": True},
            {"password_hash": 0},
        )
        if not manager:
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


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic request models
# ─────────────────────────────────────────────────────────────────────────────

class HybridLoginRequest(BaseModel):
    email:    EmailStr
    password: str


class AddHybridManagerRequest(BaseModel):
    email:     EmailStr
    full_name: str
    role:      HybridManagerRole
    password:  str
    department: Optional[str] = None
    metadata:  Optional[Dict[str, Any]] = None


class UpdateHybridManagerRequest(BaseModel):
    role:       Optional[HybridManagerRole] = None
    full_name:  Optional[str]               = None
    is_active:  Optional[bool]              = None
    department: Optional[str]               = None
    metadata:   Optional[Dict[str, Any]]    = None


class SubmitSignalRequest(BaseModel):
    symbol:      str = Field(..., description="Trading pair e.g. XAUUSD")
    direction:   str = Field(..., description="BUY or SELL")
    entry_price: float = Field(..., gt=0)
    stop_loss:   float = Field(..., gt=0)
    take_profit: float = Field(..., gt=0)
    tp1:         Optional[float] = None
    tp2:         Optional[float] = None
    tp3:         Optional[float] = None
    risk_pct:    float = Field(default=1.0, ge=0.1, le=5.0)
    confidence:  float = Field(default=0.0, ge=0, le=100)
    strategy:    str   = Field(default="HYBRID")
    timeframe:   str   = Field(default="1H")
    regime:      Optional[str] = None
    source:      str   = Field(default="MANUAL")
    tags:        Optional[List[str]] = None

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v: str) -> str:
        v = v.upper()
        if v not in ("BUY", "SELL"):
            raise ValueError("direction must be BUY or SELL")
        return v


class AnalystRecommendRequest(BaseModel):
    signal_id:    str
    quality_score: float = Field(..., ge=0, le=100,
                                  description="Signal quality score 0-100")
    review_notes: str    = Field(..., min_length=10,
                                  description="Analyst review notes (min 10 chars)")
    adjustments:  Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional price adjustments: entry_price, stop_loss, take_profit"
    )


class TradingApprovalRequest(BaseModel):
    signal_id:   str
    rationale:   str  = Field(..., min_length=10,
                               description="Trading rationale (min 10 chars)")
    priority:    str  = Field(default="NORMAL",
                               description="LOW | NORMAL | HIGH | URGENT")
    adjustments: Optional[Dict[str, Any]] = None

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        v = v.upper()
        if v not in ("LOW", "NORMAL", "HIGH", "URGENT"):
            raise ValueError("priority must be LOW, NORMAL, HIGH, or URGENT")
        return v


class RiskValidationRequest(BaseModel):
    signal_id:       str
    override_reason: Optional[str] = Field(
        default=None,
        description="Provide to override a failed risk check (RISK_MANAGER only)"
    )


class ExecutionRequest(BaseModel):
    signal_id:     str
    actual_entry:  float = Field(..., gt=0, description="Actual execution price")
    lot_size:      float = Field(..., gt=0, description="Lot size executed")
    broker_ref:    Optional[str] = Field(default=None, description="Broker order reference")
    notes:         Optional[str] = None


class CloseSignalRequest(BaseModel):
    signal_id:    str
    close_price:  float = Field(..., gt=0)
    pnl_usd:      float = Field(..., description="P&L in USD (negative for loss)")
    close_reason: str   = Field(
        default="TP_HIT",
        description="TP_HIT | SL_HIT | MANUAL | PARTIAL | TRAILING_STOP"
    )


class RejectSignalRequest(BaseModel):
    signal_id: str
    reason:    str = Field(..., min_length=10,
                            description="Rejection reason (min 10 chars)")


class AdjustSignalRequest(BaseModel):
    signal_id:   str
    entry_price: Optional[float] = None
    stop_loss:   Optional[float] = None
    take_profit: Optional[float] = None
    tp1:         Optional[float] = None
    tp2:         Optional[float] = None
    tp3:         Optional[float] = None
    risk_pct:    Optional[float] = None
    reason:      str = Field(..., min_length=5)


class UpdateRiskConfigRequest(BaseModel):
    max_positions_total:       Optional[int]   = None
    max_positions_per_pair:    Optional[int]   = None
    max_position_size_pct:     Optional[float] = None
    daily_drawdown_limit_pct:  Optional[float] = None
    weekly_drawdown_limit_pct: Optional[float] = None
    monthly_drawdown_cap_pct:  Optional[float] = None
    min_rr_ratio:              Optional[float] = None
    min_rr_gold:               Optional[float] = None
    max_risk_per_trade_pct:    Optional[float] = None
    max_gold_exposure_pct:     Optional[float] = None
    max_forex_exposure_pct:    Optional[float] = None
    max_portfolio_heat_pct:    Optional[float] = None
    max_correlated_positions:  Optional[int]   = None


class AddCommentRequest(BaseModel):
    signal_id:    str
    text:         str  = Field(..., min_length=1)
    comment_type: str  = Field(default="GENERAL",
                                description="GENERAL | ANALYSIS | RISK | DECISION")
    mentions:     Optional[List[str]] = None
    is_private:   bool = False


class AddNoteRequest(BaseModel):
    title:     str = Field(..., min_length=3)
    content:   str = Field(..., min_length=1)
    note_type: str = Field(default="GENERAL",
                            description="GENERAL | MARKET | RISK | STRATEGY")
    signal_id: Optional[str]       = None
    tags:      Optional[List[str]] = None


class CreateAlertRequest(BaseModel):
    title:              str
    message:            str
    severity:           str = Field(default="INFO",
                                     description="INFO | WARNING | CRITICAL")
    category:           str = Field(default="GENERAL",
                                     description="RISK | PERFORMANCE | COMPLIANCE | TRADING | SYSTEM | GENERAL")
    signal_id:          Optional[str] = None
    auto_resolve_hours: Optional[int] = Field(default=None, ge=1, le=168)
    metadata:           Optional[Dict[str, Any]] = None


class ResolveAlertRequest(BaseModel):
    resolution_note: Optional[str] = None


class ComplianceReportRequest(BaseModel):
    start_date:  datetime
    end_date:    datetime
    report_type: str = Field(default="full",
                              description="full | summary | by_manager | by_action")


# ─────────────────────────────────────────────────────────────────────────────
# STARTUP — initialize the hybrid manager system
# ─────────────────────────────────────────────────────────────────────────────

_initialized = False


async def _ensure_initialized() -> None:
    global _initialized
    if not _initialized:
        await hybrid_manager_system.initialize()
        _initialized = True


# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/auth/login", summary="Hybrid manager login — returns JWT")
async def hybrid_login(body: HybridLoginRequest):
    """
    Authenticate a hybrid manager and return a signed JWT.
    Checks hybrid_managers collection first, then falls back to system_managers.
    """
    await _ensure_initialized()
    db = _get_db()

    manager = await db.hybrid_managers.find_one(
        {"email": body.email, "is_active": True}
    )
    if not manager:
        manager = await db.system_managers.find_one(
            {"email": body.email, "is_active": True}
        )
    if not manager:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not pwd_context.verify(body.password, manager.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    collection = "hybrid_managers" if await db.hybrid_managers.find_one(
        {"email": body.email}
    ) else "system_managers"

    await db[collection].update_one(
        {"manager_id": manager["manager_id"]},
        {"$set": {"last_login": datetime.utcnow()}},
    )

    token = _create_hybrid_token(manager["manager_id"], manager["role"])
    return {
        "success":      True,
        "access_token": token,
        "token_type":   "bearer",
        "manager_id":   manager["manager_id"],
        "role":         manager["role"],
        "full_name":    manager.get("full_name"),
        "expires_in":   f"{JWT_EXPIRY_HRS}h",
        "permissions":  list(HYBRID_ROLE_PERMISSIONS.get(
            HybridManagerRole(manager["role"]), set()
        )),
    }


@router.get("/auth/me", summary="Get current hybrid manager profile")
async def get_my_hybrid_profile(
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Return the authenticated manager's profile with permissions."""
    await _ensure_initialized()
    profile = dict(current_manager)
    for ts in ("created_at", "last_login", "updated_at"):
        if profile.get(ts) and hasattr(profile[ts], "isoformat"):
            profile[ts] = profile[ts].isoformat()

    role = profile.get("role", "")
    try:
        permissions = list(HYBRID_ROLE_PERMISSIONS.get(HybridManagerRole(role), set()))
    except ValueError:
        permissions = []

    return {
        "success":     True,
        "manager":     profile,
        "permissions": permissions,
        "role_description": _role_description(role),
    }


@router.post("/auth/refresh", summary="Refresh JWT token")
async def refresh_token(
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Issue a fresh JWT for the authenticated manager."""
    await _ensure_initialized()
    token = _create_hybrid_token(
        current_manager["manager_id"], current_manager["role"]
    )
    return {
        "success":      True,
        "access_token": token,
        "token_type":   "bearer",
        "expires_in":   f"{JWT_EXPIRY_HRS}h",
    }


# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
# MANAGER MANAGEMENT ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/managers", summary="Add a new hybrid manager [SUPER_ADMIN]")
async def add_hybrid_manager(
    body:            AddHybridManagerRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Create a new hybrid manager account. Requires SUPER_ADMIN role."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "manager:add")
    except PermissionError as exc:
        _handle_permission_error(exc)

    db = _get_db()
    existing = await db.hybrid_managers.find_one({"email": body.email})
    if existing:
        raise HTTPException(status_code=400, detail="A manager with that email already exists")

    import uuid
    manager_id = str(uuid.uuid4())
    doc = {
        "manager_id":  manager_id,
        "email":       body.email,
        "full_name":   body.full_name,
        "role":        body.role.value,
        "department":  body.department,
        "password_hash": pwd_context.hash(body.password),
        "is_active":   True,
        "created_at":  datetime.utcnow(),
        "created_by":  current_manager["manager_id"],
        "last_login":  None,
        "metadata":    body.metadata or {},
    }
    await db.hybrid_managers.insert_one(doc)

    await hybrid_manager_system.audit.record(
        action="manager:add",
        performed_by=current_manager["manager_id"],
        role=current_manager["role"],
        details={"new_manager_id": manager_id, "email": body.email,
                 "role": body.role.value},
    )

    return {
        "success":    True,
        "manager_id": manager_id,
        "email":      body.email,
        "role":       body.role.value,
        "created_at": doc["created_at"].isoformat(),
    }


@router.get("/managers", summary="List all hybrid managers")
async def list_hybrid_managers(
    include_inactive: bool          = Query(default=False),
    role_filter:      Optional[str] = Query(default=None, alias="role"),
    current_manager:  Dict          = Depends(get_current_hybrid_manager),
):
    """List hybrid managers. All roles can call this endpoint."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "manager:list")
    except PermissionError as exc:
        _handle_permission_error(exc)

    db    = _get_db()
    query: Dict[str, Any] = {} if include_inactive else {"is_active": True}
    if role_filter:
        query["role"] = role_filter.upper()

    managers = await db.hybrid_managers.find(
        query, {"password_hash": 0}
    ).to_list(500)

    formatted = []
    for m in managers:
        m.pop("_id", None)
        for ts in ("created_at", "last_login", "updated_at"):
            if m.get(ts) and hasattr(m[ts], "isoformat"):
                m[ts] = m[ts].isoformat()
        formatted.append(m)

    return {"success": True, "managers": formatted, "count": len(formatted)}


@router.get("/managers/{manager_id}", summary="Get a single hybrid manager")
async def get_hybrid_manager(
    manager_id:      str,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Retrieve a manager by ID."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "manager:get")
    except PermissionError as exc:
        _handle_permission_error(exc)

    db = _get_db()
    m  = await db.hybrid_managers.find_one(
        {"manager_id": manager_id}, {"password_hash": 0}
    )
    if not m:
        raise HTTPException(status_code=404, detail="Manager not found")

    m.pop("_id", None)
    for ts in ("created_at", "last_login", "updated_at"):
        if m.get(ts) and hasattr(m[ts], "isoformat"):
            m[ts] = m[ts].isoformat()

    return {"success": True, "manager": m}


@router.put("/managers/{manager_id}", summary="Update a hybrid manager [SUPER_ADMIN]")
async def update_hybrid_manager(
    manager_id:      str,
    body:            UpdateHybridManagerRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Update manager role, name, or active status. Requires SUPER_ADMIN."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "manager:update")
    except PermissionError as exc:
        _handle_permission_error(exc)

    db      = _get_db()
    updates = body.model_dump(exclude_none=True)
    if "role" in updates:
        updates["role"] = updates["role"].value
    updates["updated_at"] = datetime.utcnow()
    updates["updated_by"] = current_manager["manager_id"]

    result = await db.hybrid_managers.update_one(
        {"manager_id": manager_id}, {"$set": updates}
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Manager not found or no changes made")

    await hybrid_manager_system.audit.record(
        action="manager:update",
        performed_by=current_manager["manager_id"],
        role=current_manager["role"],
        details={"target_manager_id": manager_id,
                 "fields_updated": list(updates.keys())},
    )

    return {"success": True, "message": "Manager updated",
            "updated_fields": list(updates.keys())}


@router.delete("/managers/{manager_id}", summary="Deactivate a hybrid manager [SUPER_ADMIN]")
async def deactivate_hybrid_manager(
    manager_id:      str,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Soft-delete (deactivate) a manager account. Requires SUPER_ADMIN."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "manager:remove")
    except PermissionError as exc:
        _handle_permission_error(exc)

    if current_manager["manager_id"] == manager_id:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")

    db = _get_db()
    result = await db.hybrid_managers.update_one(
        {"manager_id": manager_id},
        {"$set": {"is_active": False, "deactivated_at": datetime.utcnow(),
                  "deactivated_by": current_manager["manager_id"]}},
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Manager not found")

    await hybrid_manager_system.audit.record(
        action="manager:remove",
        performed_by=current_manager["manager_id"],
        role=current_manager["role"],
        details={"target_manager_id": manager_id},
    )

    return {"success": True, "message": "Manager deactivated"}


@router.get("/managers/{manager_id}/performance",
            summary="Get performance stats for a specific manager")
async def get_manager_performance_by_id(
    manager_id:      str,
    days:            int  = Query(default=30, ge=1, le=365),
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get performance metrics for a specific manager."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "analytics:manager_stats")
    except PermissionError as exc:
        _handle_permission_error(exc)

    return await hybrid_manager_system.analytics.get_manager_performance(
        manager_id=manager_id, days=days
    )


# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL MANAGEMENT ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/signals/submit", summary="Submit a new signal into the workflow")
async def submit_signal(
    body:            SubmitSignalRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """
    Submit a new trading signal into the multi-tier approval workflow.
    Signal enters PENDING state awaiting analyst review.
    """
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "signal:view")
    except PermissionError as exc:
        _handle_permission_error(exc)

    signal_data = body.model_dump()
    result = await hybrid_manager_system.workflow.submit_signal(
        signal_data=signal_data,
        submitted_by=current_manager["manager_id"],
    )
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@router.get("/signals/pending", summary="List signals awaiting action")
async def get_pending_signals(
    status:          Optional[str] = Query(default=None,
                                            description="Filter by status: PENDING, RECOMMENDED, APPROVED, ACTIVE"),
    symbol:          Optional[str] = Query(default=None),
    limit:           int           = Query(default=50, ge=1, le=200),
    skip:            int           = Query(default=0, ge=0),
    current_manager: Dict          = Depends(get_current_hybrid_manager),
):
    """
    List signals awaiting action. Default status is role-based:
    - ANALYST → PENDING
    - TRADING_MANAGER → RECOMMENDED
    - RISK_MANAGER → APPROVED
    - OPERATOR → ACTIVE
    """
    await _ensure_initialized()
    return await hybrid_manager_system.workflow.get_pending_signals(
        requesting_manager=current_manager,
        status_filter=status,
        symbol=symbol,
        limit=limit,
        skip=skip,
    )


@router.get("/signals/{signal_id}", summary="Get full signal details")
async def get_signal_detail(
    signal_id:       str,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get complete signal details including workflow history and comments."""
    await _ensure_initialized()
    result = await hybrid_manager_system.workflow.get_signal_detail(
        signal_id=signal_id,
        requesting_manager=current_manager,
    )
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result.get("error"))
    return result


@router.get("/signals", summary="List all signals with filters")
async def list_all_signals(
    status:          Optional[str] = Query(default=None),
    symbol:          Optional[str] = Query(default=None),
    direction:       Optional[str] = Query(default=None, description="BUY or SELL"),
    strategy:        Optional[str] = Query(default=None),
    outcome:         Optional[str] = Query(default=None, description="WIN | LOSS | BREAKEVEN"),
    days:            int           = Query(default=7, ge=1, le=365),
    limit:           int           = Query(default=50, ge=1, le=200),
    skip:            int           = Query(default=0, ge=0),
    current_manager: Dict          = Depends(get_current_hybrid_manager),
):
    """List all signals with advanced filtering."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "signal:history")
    except PermissionError as exc:
        _handle_permission_error(exc)

    db     = _get_db()
    cutoff = datetime.utcnow() - timedelta(days=days)
    query: Dict[str, Any] = {"submitted_at": {"$gte": cutoff}}

    if status:
        query["status"] = status.upper()
    if symbol:
        query["symbol"] = symbol.upper()
    if direction:
        query["direction"] = direction.upper()
    if strategy:
        query["strategy"] = strategy.upper()
    if outcome:
        query["outcome"] = outcome.upper()

    total  = await db.hybrid_signals.count_documents(query)
    cursor = (
        db.hybrid_signals
        .find(query, {"raw_signal": 0})
        .sort("submitted_at", -1)
        .skip(skip)
        .limit(limit)
    )
    signals = await cursor.to_list(limit)

    formatted = []
    for s in signals:
        s.pop("_id", None)
        for ts in ("submitted_at", "recommended_at", "approved_at",
                   "activated_at", "executed_at", "closed_at",
                   "rejected_at", "expires_at"):
            if s.get(ts) and hasattr(s[ts], "isoformat"):
                s[ts] = s[ts].isoformat()
        formatted.append(s)

    return {
        "success": True,
        "signals": formatted,
        "total":   total,
        "count":   len(formatted),
        "skip":    skip,
        "limit":   limit,
    }


@router.get("/signals/{signal_id}/history", summary="Get signal workflow history")
async def get_signal_workflow_history(
    signal_id:       str,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get the complete stage-by-stage workflow history for a signal."""
    await _ensure_initialized()
    result = await hybrid_manager_system.workflow.get_signal_detail(
        signal_id=signal_id,
        requesting_manager=current_manager,
    )
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result.get("error"))

    signal = result["signal"]
    return {
        "success":       True,
        "signal_id":     signal_id,
        "current_status": signal.get("status"),
        "stage_history": signal.get("stage_history", []),
        "submitted_at":  signal.get("submitted_at"),
        "submitted_by":  signal.get("submitted_by"),
    }


@router.put("/signals/{signal_id}/adjust", summary="Adjust signal price levels")
async def adjust_signal(
    signal_id:       str,
    body:            AdjustSignalRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Adjust signal price levels (entry, SL, TP). Requires signal:adjust permission."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "signal:adjust")
    except PermissionError as exc:
        _handle_permission_error(exc)

    db     = _get_db()
    signal = await db.hybrid_signals.find_one({"signal_id": signal_id})
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    terminal = ["CLOSED", "REJECTED", "EXPIRED", "EXECUTED"]
    if signal["status"] in terminal:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot adjust signal in {signal['status']} state"
        )

    updates: Dict[str, Any] = {}
    for field in ("entry_price", "stop_loss", "take_profit", "tp1", "tp2", "tp3", "risk_pct"):
        val = getattr(body, field, None)
        if val is not None:
            updates[field] = val

    if not updates:
        raise HTTPException(status_code=400, detail="No adjustment fields provided")

    updates["last_adjusted_at"] = datetime.utcnow()
    updates["last_adjusted_by"] = current_manager["manager_id"]

    stage_entry = {
        "stage":     "ADJUSTED",
        "actor":     current_manager["manager_id"],
        "role":      current_manager["role"],
        "timestamp": datetime.utcnow().isoformat(),
        "reason":    body.reason,
        "fields":    list(updates.keys()),
    }

    await db.hybrid_signals.update_one(
        {"signal_id": signal_id},
        {"$set": updates, "$push": {"stage_history": stage_entry}},
    )

    await hybrid_manager_system.audit.record(
        action="signal:adjust",
        performed_by=current_manager["manager_id"],
        role=current_manager["role"],
        details={"signal_id": signal_id, "adjustments": updates, "reason": body.reason},
        signal_id=signal_id,
        rationale=body.reason,
    )

    return {
        "success":    True,
        "signal_id":  signal_id,
        "adjusted_fields": list(updates.keys()),
        "reason":     body.reason,
    }


# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
# APPROVAL WORKFLOW ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/workflow/recommend", summary="Analyst recommends a signal [ANALYST+]")
async def analyst_recommend(
    body:            AnalystRecommendRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """
    Stage 2: Analyst reviews and recommends a PENDING signal.
    Assigns a quality score (0-100) and review notes.
    Requires ANALYST role or higher.
    """
    await _ensure_initialized()
    result = await hybrid_manager_system.workflow.analyst_recommend(
        signal_id=body.signal_id,
        requesting_manager=current_manager,
        quality_score=body.quality_score,
        review_notes=body.review_notes,
        adjustments=body.adjustments,
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=400 if "not found" not in result.get("error", "").lower() else 404,
            detail=result.get("error"),
        )
    return result


@router.post("/workflow/approve", summary="Trading Manager approves a signal [TRADING_MANAGER+]")
async def trading_manager_approve(
    body:            TradingApprovalRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """
    Stage 3: Trading Manager approves a RECOMMENDED signal.
    Requires TRADING_MANAGER role or higher.
    """
    await _ensure_initialized()
    result = await hybrid_manager_system.workflow.trading_manager_approve(
        signal_id=body.signal_id,
        requesting_manager=current_manager,
        rationale=body.rationale,
        priority=body.priority,
        adjustments=body.adjustments,
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=400 if "not found" not in result.get("error", "").lower() else 404,
            detail=result.get("error"),
        )
    return result


@router.post("/workflow/validate-risk",
             summary="Risk Manager validates a signal [RISK_MANAGER+]")
async def risk_manager_validate(
    body:            RiskValidationRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """
    Stage 4: Risk Manager validates an APPROVED signal.
    Runs full risk engine checks. Signal becomes ACTIVE if passed.
    Provide override_reason to bypass failed checks (RISK_MANAGER only).
    """
    await _ensure_initialized()
    result = await hybrid_manager_system.workflow.risk_manager_validate(
        signal_id=body.signal_id,
        requesting_manager=current_manager,
        override_reason=body.override_reason,
    )
    if not result.get("success") and "risk_result" not in result:
        raise HTTPException(
            status_code=400 if "not found" not in result.get("error", "").lower() else 404,
            detail=result.get("error"),
        )
    return result


@router.post("/workflow/execute", summary="Operator confirms signal execution [OPERATOR+]")
async def operator_execute(
    body:            ExecutionRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """
    Stage 5: Operator confirms execution of an ACTIVE signal.
    Records actual entry price, lot size, and broker reference.
    Requires OPERATOR role or higher.
    """
    await _ensure_initialized()
    execution_details = body.model_dump(exclude={"signal_id"})
    result = await hybrid_manager_system.workflow.operator_execute(
        signal_id=body.signal_id,
        requesting_manager=current_manager,
        execution_details=execution_details,
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=400 if "not found" not in result.get("error", "").lower() else 404,
            detail=result.get("error"),
        )
    return result


@router.post("/workflow/close", summary="Close a signal and record P&L [OPERATOR+]")
async def close_signal(
    body:            CloseSignalRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """
    Stage 6: Close an EXECUTED signal and record P&L.
    Requires OPERATOR role or higher.
    """
    await _ensure_initialized()
    result = await hybrid_manager_system.workflow.close_signal(
        signal_id=body.signal_id,
        requesting_manager=current_manager,
        close_price=body.close_price,
        pnl_usd=body.pnl_usd,
        close_reason=body.close_reason,
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=400 if "not found" not in result.get("error", "").lower() else 404,
            detail=result.get("error"),
        )
    return result


@router.post("/workflow/reject", summary="Reject a signal at any stage")
async def reject_signal(
    body:            RejectSignalRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """
    Reject a signal at any workflow stage.
    Requires signal:reject permission (ANALYST, TRADING_MANAGER, RISK_MANAGER, SUPER_ADMIN).
    """
    await _ensure_initialized()
    result = await hybrid_manager_system.workflow.reject_signal(
        signal_id=body.signal_id,
        requesting_manager=current_manager,
        reason=body.reason,
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=400 if "not found" not in result.get("error", "").lower() else 404,
            detail=result.get("error"),
        )
    return result


@router.post("/workflow/expire-stale",
             summary="Expire stale signals [SUPER_ADMIN, RISK_MANAGER]")
async def expire_stale_signals(
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Expire signals that have passed their expiry time without progressing."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "risk:override")
    except PermissionError as exc:
        _handle_permission_error(exc)

    result = await hybrid_manager_system.workflow.expire_stale_signals()
    return {"success": True, **result}


@router.get("/workflow/stats", summary="Approval workflow statistics")
async def get_workflow_stats(
    days:            int  = Query(default=30, ge=1, le=365),
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get approval funnel statistics for the workflow."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "signal:stats")
    except PermissionError as exc:
        _handle_permission_error(exc)

    return await hybrid_manager_system.analytics.get_approval_rate_analysis(days=days)


# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
# RISK MANAGEMENT ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/risk/dashboard", summary="Risk management dashboard")
async def get_risk_dashboard(
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Real-time risk dashboard: drawdown, positions, portfolio heat, weekly P&L."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "risk:view")
    except PermissionError as exc:
        _handle_permission_error(exc)

    return await hybrid_manager_system.risk.get_risk_dashboard()


@router.get("/risk/config", summary="Get current risk configuration")
async def get_risk_config(
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get the current risk management configuration."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "risk:view")
    except PermissionError as exc:
        _handle_permission_error(exc)

    return {
        "success": True,
        "config":  hybrid_manager_system.risk.config,
    }


@router.put("/risk/config", summary="Update risk configuration [RISK_MANAGER, SUPER_ADMIN]")
async def update_risk_config(
    body:            UpdateRiskConfigRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Update risk management limits and thresholds. Requires RISK_MANAGER or SUPER_ADMIN."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "risk:set_limits")
    except PermissionError as exc:
        _handle_permission_error(exc)

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No configuration fields provided")

    result = await hybrid_manager_system.risk.save_config(
        updates=updates,
        updated_by=current_manager["manager_id"],
    )
    return result


@router.post("/risk/validate-signal", summary="Run risk validation on a signal")
async def validate_signal_risk(
    signal_id:       str  = Body(..., embed=True),
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Run the full risk validation suite on a signal without changing its status."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "risk:view")
    except PermissionError as exc:
        _handle_permission_error(exc)

    db     = _get_db()
    signal = await db.hybrid_signals.find_one({"signal_id": signal_id})
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    signal.pop("_id", None)

    result = await hybrid_manager_system.risk.validate_signal(signal, current_manager)
    return {"success": True, "signal_id": signal_id, "risk_validation": result}


@router.get("/risk/drawdown", summary="Current drawdown status")
async def get_drawdown_status(
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get current daily, weekly, and monthly drawdown status."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "risk:drawdown_check")
    except PermissionError as exc:
        _handle_permission_error(exc)

    db = _get_db()

    async def _pnl_since(hours: int) -> float:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        pipeline = [
            {"$match": {"status": "CLOSED", "closed_at": {"$gte": cutoff}}},
            {"$group": {"_id": None, "pnl": {"$sum": "$pnl_usd"}}},
        ]
        result = await db.hybrid_signals.aggregate(pipeline).to_list(1)
        return result[0]["pnl"] if result else 0.0

    daily_pnl   = await _pnl_since(24)
    weekly_pnl  = await _pnl_since(168)
    monthly_pnl = await _pnl_since(720)

    account_balance = float(os.environ.get("DEFAULT_ACCOUNT_BALANCE", "100000"))
    config          = hybrid_manager_system.risk.config

    def _dd_pct(pnl: float) -> float:
        return round(abs(min(pnl, 0)) / account_balance * 100, 3)

    return {
        "success": True,
        "account_balance": account_balance,
        "drawdown": {
            "daily": {
                "pnl":     round(daily_pnl, 2),
                "pct":     _dd_pct(daily_pnl),
                "limit":   config["daily_drawdown_limit_pct"],
                "status":  "OK" if _dd_pct(daily_pnl) < config["daily_drawdown_limit_pct"]
                           else "LIMIT_REACHED",
            },
            "weekly": {
                "pnl":     round(weekly_pnl, 2),
                "pct":     _dd_pct(weekly_pnl),
                "limit":   config["weekly_drawdown_limit_pct"],
                "status":  "OK" if _dd_pct(weekly_pnl) < config["weekly_drawdown_limit_pct"]
                           else "LIMIT_REACHED",
            },
            "monthly": {
                "pnl":     round(monthly_pnl, 2),
                "pct":     _dd_pct(monthly_pnl),
                "limit":   config["monthly_drawdown_cap_pct"],
                "status":  "OK" if _dd_pct(monthly_pnl) < config["monthly_drawdown_cap_pct"]
                           else "LIMIT_REACHED",
            },
        },
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/risk/exposure", summary="Current exposure by asset class")
async def get_exposure_summary(
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get current exposure breakdown by asset class and symbol."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "risk:exposure_check")
    except PermissionError as exc:
        _handle_permission_error(exc)

    db = _get_db()
    pipeline = [
        {"$match": {"status": {"$in": ["ACTIVE", "EXECUTED"]}}},
        {"$group": {
            "_id":      "$symbol",
            "count":    {"$sum": 1},
            "category": {"$first": "$symbol_category"},
            "total_risk": {"$sum": "$risk_pct"},
        }},
        {"$sort": {"total_risk": -1}},
    ]
    by_symbol = await db.hybrid_signals.aggregate(pipeline).to_list(50)

    by_category: Dict[str, Any] = {}
    for item in by_symbol:
        cat = item.get("category", "unknown")
        if cat not in by_category:
            by_category[cat] = {"count": 0, "total_risk": 0.0, "symbols": []}
        by_category[cat]["count"]      += item["count"]
        by_category[cat]["total_risk"] += item["total_risk"]
        by_category[cat]["symbols"].append(item["_id"])

    return {
        "success":     True,
        "by_symbol":   by_symbol,
        "by_category": by_category,
        "limits":      {
            "gold":   hybrid_manager_system.risk.config["max_gold_exposure_pct"],
            "forex":  hybrid_manager_system.risk.config["max_forex_exposure_pct"],
            "crypto": hybrid_manager_system.risk.config["max_crypto_exposure_pct"],
        },
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/risk/positions", summary="Active position summary")
async def get_position_summary(
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get summary of all active and executed positions."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "risk:position_check")
    except PermissionError as exc:
        _handle_permission_error(exc)

    db = _get_db()
    positions = await db.hybrid_signals.find(
        {"status": {"$in": ["ACTIVE", "EXECUTED"]}},
        {"raw_signal": 0},
    ).sort("activated_at", -1).to_list(200)

    formatted = []
    for p in positions:
        p.pop("_id", None)
        for ts in ("submitted_at", "activated_at", "executed_at"):
            if p.get(ts) and hasattr(p[ts], "isoformat"):
                p[ts] = p[ts].isoformat()
        formatted.append(p)

    total_risk = sum(p.get("risk_pct", 0) for p in formatted)

    return {
        "success":       True,
        "positions":     formatted,
        "count":         len(formatted),
        "total_risk_pct": round(total_risk, 2),
        "heat_limit":    hybrid_manager_system.risk.config["max_portfolio_heat_pct"],
        "heat_status":   "OK" if total_risk <= hybrid_manager_system.risk.config["max_portfolio_heat_pct"]
                         else "OVER_LIMIT",
    }


# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
# PERFORMANCE ANALYTICS ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/analytics/overview", summary="Performance analytics overview")
async def get_analytics_overview(
    days:            int  = Query(default=30, ge=1, le=365),
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Comprehensive performance analytics overview."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "analytics:view")
    except PermissionError as exc:
        _handle_permission_error(exc)

    return await hybrid_manager_system.analytics.get_manager_performance(days=days)


@router.get("/analytics/signal-quality", summary="Signal quality metrics")
async def get_signal_quality(
    days:            int  = Query(default=30, ge=1, le=365),
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Analyze signal quality scores and their correlation with outcomes."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "analytics:signal_quality")
    except PermissionError as exc:
        _handle_permission_error(exc)

    return await hybrid_manager_system.analytics.get_signal_quality_metrics(days=days)


@router.get("/analytics/pnl", summary="P&L report with attribution")
async def get_pnl_report(
    days:            int  = Query(default=30, ge=1, le=365),
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Comprehensive P&L report with attribution by symbol, strategy, and day."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "analytics:pnl")
    except PermissionError as exc:
        _handle_permission_error(exc)

    return await hybrid_manager_system.analytics.get_pnl_report(days=days)


@router.get("/analytics/approval-funnel", summary="Approval funnel analysis")
async def get_approval_funnel(
    days:            int  = Query(default=30, ge=1, le=365),
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Analyze approval rates at each workflow stage."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "analytics:view")
    except PermissionError as exc:
        _handle_permission_error(exc)

    return await hybrid_manager_system.analytics.get_approval_rate_analysis(days=days)


@router.get("/analytics/managers", summary="All managers performance stats")
async def get_all_managers_performance(
    days:            int  = Query(default=30, ge=1, le=365),
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get performance metrics for all managers."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "analytics:manager_stats")
    except PermissionError as exc:
        _handle_permission_error(exc)

    return await hybrid_manager_system.analytics.get_manager_performance(days=days)


@router.get("/analytics/export", summary="Export analytics data [SUPER_ADMIN, RISK_MANAGER]")
async def export_analytics(
    days:            int  = Query(default=30, ge=1, le=365),
    format_type:     str  = Query(default="json", description="json (csv coming soon)"),
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Export analytics data for external reporting."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "analytics:export")
    except PermissionError as exc:
        _handle_permission_error(exc)

    pnl     = await hybrid_manager_system.analytics.get_pnl_report(days=days)
    quality = await hybrid_manager_system.analytics.get_signal_quality_metrics(days=days)
    funnel  = await hybrid_manager_system.analytics.get_approval_rate_analysis(days=days)

    return {
        "success":      True,
        "export_date":  datetime.utcnow().isoformat(),
        "period_days":  days,
        "pnl_report":   pnl,
        "quality":      quality,
        "funnel":       funnel,
        "exported_by":  current_manager["manager_id"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
# COMPLIANCE & AUDIT ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/compliance/audit", summary="View audit trail")
async def get_audit_log(
    limit:       int           = Query(default=100, ge=1, le=1000),
    manager_id:  Optional[str] = Query(default=None),
    action:      Optional[str] = Query(default=None),
    signal_id:   Optional[str] = Query(default=None),
    since_hours: int           = Query(default=168, ge=1, le=8760,
                                        description="Hours to look back (default 7 days)"),
    category:    Optional[str] = Query(default=None,
                                        description="Filter by action category prefix"),
    current_manager: Dict      = Depends(get_current_hybrid_manager),
):
    """Retrieve the full audit trail with optional filters."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "audit:view")
    except PermissionError as exc:
        _handle_permission_error(exc)

    return await hybrid_manager_system.audit.get_log(
        limit=limit,
        manager_id=manager_id,
        action_filter=action,
        signal_id=signal_id,
        since_hours=since_hours,
        category=category,
    )


@router.post("/compliance/report", summary="Generate compliance report [RISK_MANAGER, SUPER_ADMIN]")
async def generate_compliance_report(
    body:            ComplianceReportRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Generate a compliance report for a specified date range."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "compliance:report")
    except PermissionError as exc:
        _handle_permission_error(exc)

    if body.end_date <= body.start_date:
        raise HTTPException(status_code=400, detail="end_date must be after start_date")

    result = await hybrid_manager_system.audit.generate_compliance_report(
        start_date=body.start_date,
        end_date=body.end_date,
        report_type=body.report_type,
    )

    await hybrid_manager_system.audit.record(
        action="compliance:report",
        performed_by=current_manager["manager_id"],
        role=current_manager["role"],
        details={"start": body.start_date.isoformat(),
                 "end":   body.end_date.isoformat(),
                 "type":  body.report_type},
    )

    return result


@router.get("/compliance/signal-decisions/{signal_id}",
            summary="Get all decisions for a signal")
async def get_signal_decisions(
    signal_id:       str,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get the complete decision trail for a specific signal."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "compliance:view")
    except PermissionError as exc:
        _handle_permission_error(exc)

    audit_result = await hybrid_manager_system.audit.get_log(
        signal_id=signal_id,
        limit=200,
        since_hours=8760,
    )

    signal_result = await hybrid_manager_system.workflow.get_signal_detail(
        signal_id=signal_id,
        requesting_manager=current_manager,
    )

    return {
        "success":       True,
        "signal_id":     signal_id,
        "signal_status": signal_result.get("signal", {}).get("status") if signal_result.get("success") else None,
        "stage_history": signal_result.get("signal", {}).get("stage_history", []) if signal_result.get("success") else [],
        "audit_trail":   audit_result.get("records", []),
        "decision_count": audit_result.get("count", 0),
    }


@router.get("/compliance/summary", summary="Compliance summary dashboard")
async def get_compliance_summary(
    days:            int  = Query(default=30, ge=1, le=365),
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get a compliance summary for the specified period."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "compliance:view")
    except PermissionError as exc:
        _handle_permission_error(exc)

    end_date   = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    report = await hybrid_manager_system.audit.generate_compliance_report(
        start_date=start_date,
        end_date=end_date,
        report_type="summary",
    )
    return report


# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
# TEAM COLLABORATION ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/collab/comments", summary="Add a comment to a signal")
async def add_comment(
    body:            AddCommentRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Add a comment to a signal's discussion thread."""
    await _ensure_initialized()
    result = await hybrid_manager_system.collab.add_comment(
        signal_id=body.signal_id,
        requesting_manager=current_manager,
        comment_text=body.text,
        comment_type=body.comment_type,
        mentions=body.mentions,
        is_private=body.is_private,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@router.get("/collab/comments/{signal_id}", summary="Get comments for a signal")
async def get_signal_comments(
    signal_id:       str,
    include_private: bool = Query(default=False),
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get all comments for a signal thread."""
    await _ensure_initialized()
    return await hybrid_manager_system.collab.get_comments(
        signal_id=signal_id,
        requesting_manager=current_manager,
        include_private=include_private,
    )


@router.post("/collab/notes", summary="Add a team note")
async def add_note(
    body:            AddNoteRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Add a standalone note (market observation, strategy note, etc.)."""
    await _ensure_initialized()
    result = await hybrid_manager_system.collab.add_note(
        requesting_manager=current_manager,
        title=body.title,
        content=body.content,
        note_type=body.note_type,
        signal_id=body.signal_id,
        tags=body.tags,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@router.get("/collab/notes", summary="Get team notes")
async def get_notes(
    signal_id:       Optional[str] = Query(default=None),
    note_type:       Optional[str] = Query(default=None),
    limit:           int           = Query(default=50, ge=1, le=200),
    current_manager: Dict          = Depends(get_current_hybrid_manager),
):
    """Get team notes with optional filters."""
    await _ensure_initialized()
    return await hybrid_manager_system.collab.get_notes(
        requesting_manager=current_manager,
        signal_id=signal_id,
        note_type=note_type,
        limit=limit,
    )


@router.get("/collab/activity", summary="Team activity feed")
async def get_team_activity(
    hours:           int  = Query(default=24, ge=1, le=168),
    limit:           int  = Query(default=100, ge=1, le=500),
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get recent team activity across all signals."""
    await _ensure_initialized()
    return await hybrid_manager_system.collab.get_team_activity(
        requesting_manager=current_manager,
        hours=hours,
        limit=limit,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
# ALERT MANAGEMENT ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/alerts", summary="Create a system alert")
async def create_alert(
    body:            CreateAlertRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Create a new system alert. Requires alert:create permission."""
    await _ensure_initialized()
    result = await hybrid_manager_system.alerts.create_alert(
        requesting_manager=current_manager,
        title=body.title,
        message=body.message,
        severity=body.severity,
        category=body.category,
        signal_id=body.signal_id,
        auto_resolve_hours=body.auto_resolve_hours,
        metadata=body.metadata,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@router.get("/alerts", summary="List system alerts")
async def list_alerts(
    include_resolved: bool          = Query(default=False),
    severity:         Optional[str] = Query(default=None,
                                             description="INFO | WARNING | CRITICAL"),
    category:         Optional[str] = Query(default=None),
    limit:            int           = Query(default=50, ge=1, le=200),
    skip:             int           = Query(default=0, ge=0),
    current_manager:  Dict          = Depends(get_current_hybrid_manager),
):
    """List system alerts with optional filters."""
    await _ensure_initialized()
    return await hybrid_manager_system.alerts.list_alerts(
        requesting_manager=current_manager,
        include_resolved=include_resolved,
        severity=severity,
        category=category,
        limit=limit,
        skip=skip,
    )


@router.get("/alerts/summary", summary="Alert summary by severity and category")
async def get_alert_summary(
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get a summary of active alerts grouped by severity and category."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "alert:view")
    except PermissionError as exc:
        _handle_permission_error(exc)

    return await hybrid_manager_system.alerts.get_alert_summary()


@router.post("/alerts/{alert_id}/resolve", summary="Resolve an alert")
async def resolve_alert(
    alert_id:        str,
    body:            ResolveAlertRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Mark an alert as resolved. Requires alert:resolve permission."""
    await _ensure_initialized()
    result = await hybrid_manager_system.alerts.resolve_alert(
        alert_id=alert_id,
        requesting_manager=current_manager,
        resolution_note=body.resolution_note,
    )
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error"))
    return result


@router.post("/alerts/{alert_id}/acknowledge", summary="Acknowledge an alert")
async def acknowledge_alert(
    alert_id:        str,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Acknowledge an alert (mark as seen without resolving)."""
    await _ensure_initialized()
    result = await hybrid_manager_system.alerts.acknowledge_alert(
        alert_id=alert_id,
        requesting_manager=current_manager,
    )
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error"))
    return result


@router.post("/alerts/auto-resolve", summary="Auto-resolve expired alerts [SUPER_ADMIN]")
async def auto_resolve_alerts(
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Trigger auto-resolution of alerts past their auto_resolve_at time."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "alert:configure")
    except PermissionError as exc:
        _handle_permission_error(exc)

    result = await hybrid_manager_system.alerts.auto_resolve_expired()
    return {"success": True, **result}


# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard", summary="Real-time monitoring dashboard")
async def get_dashboard(
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """
    Real-time monitoring dashboard combining:
    - Signal pipeline status
    - Risk metrics
    - Alert summary
    - Recent activity
    """
    await _ensure_initialized()
    return await hybrid_manager_system.get_dashboard(current_manager)


@router.get("/dashboard/realtime", summary="Real-time signal pipeline status")
async def get_realtime_pipeline(
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get real-time signal pipeline counts by stage."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "dashboard:realtime")
    except PermissionError as exc:
        _handle_permission_error(exc)

    db = _get_db()
    pipeline = [
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    status_counts_raw = await db.hybrid_signals.aggregate(pipeline).to_list(20)
    status_counts     = {item["_id"]: item["count"] for item in status_counts_raw}

    # Recent submissions (last 1h)
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    recent_count = await db.hybrid_signals.count_documents(
        {"submitted_at": {"$gte": one_hour_ago}}
    )

    return {
        "success":     True,
        "timestamp":   datetime.utcnow().isoformat(),
        "pipeline": {
            "pending":      status_counts.get("PENDING", 0),
            "recommended":  status_counts.get("RECOMMENDED", 0),
            "approved":     status_counts.get("APPROVED", 0),
            "active":       status_counts.get("ACTIVE", 0),
            "executed":     status_counts.get("EXECUTED", 0),
            "closed":       status_counts.get("CLOSED", 0),
            "rejected":     status_counts.get("REJECTED", 0),
            "expired":      status_counts.get("EXPIRED", 0),
        },
        "recent_submissions_1h": recent_count,
        "total_signals": sum(status_counts.values()),
    }


@router.get("/dashboard/health", summary="Hybrid manager system health check")
async def get_system_health(
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Health check for the hybrid manager subsystem."""
    await _ensure_initialized()
    return await hybrid_manager_system.get_system_health()


@router.get("/dashboard/roles", summary="Role permissions reference")
async def get_roles_reference(
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get the complete role permissions matrix."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "dashboard:view")
    except PermissionError as exc:
        _handle_permission_error(exc)

    return {
        "success": True,
        "roles": {
            role.value: {
                "description": _role_description(role.value),
                "permissions": sorted(list(perms)),
            }
            for role, perms in HYBRID_ROLE_PERMISSIONS.items()
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/system/status", summary="System status overview")
async def get_system_status(
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get system status including DB health and signal counts."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "system:status")
    except PermissionError as exc:
        _handle_permission_error(exc)

    return await hybrid_manager_system.get_system_health()


@router.get("/system/signal-stats", summary="Signal statistics summary")
async def get_signal_stats(
    days:            int  = Query(default=7, ge=1, le=365),
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get signal statistics for the specified period."""
    await _ensure_initialized()
    try:
        check_hybrid_permission(current_manager, "signal:stats")
    except PermissionError as exc:
        _handle_permission_error(exc)

    db     = _get_db()
    cutoff = datetime.utcnow() - timedelta(days=days)

    pipeline = [
        {"$match": {"submitted_at": {"$gte": cutoff}}},
        {"$facet": {
            "by_status": [
                {"$group": {"_id": "$status", "count": {"$sum": 1}}},
            ],
            "by_symbol": [
                {"$group": {"_id": "$symbol", "count": {"$sum": 1},
                            "wins": {"$sum": {"$cond": [
                                {"$eq": ["$outcome", "WIN"]}, 1, 0]}}}},
                {"$sort": {"count": -1}},
            ],
            "by_direction": [
                {"$group": {"_id": "$direction", "count": {"$sum": 1}}},
            ],
            "by_strategy": [
                {"$group": {"_id": "$strategy", "count": {"$sum": 1}}},
            ],
        }},
    ]

    result = await db.hybrid_signals.aggregate(pipeline).to_list(1)
    data   = result[0] if result else {}

    return {
        "success":      True,
        "period_days":  days,
        "by_status":    {item["_id"]: item["count"]
                         for item in data.get("by_status", [])},
        "by_symbol":    data.get("by_symbol", []),
        "by_direction": {item["_id"]: item["count"]
                         for item in data.get("by_direction", [])},
        "by_strategy":  {item["_id"]: item["count"]
                         for item in data.get("by_strategy", [])},
        "generated_at": datetime.utcnow().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _role_description(role: str) -> str:
    descriptions = {
        "SUPER_ADMIN":     "Full system control — all operations including manager CRUD",
        "RISK_MANAGER":    "Risk validation, position limits, drawdown controls, compliance",
        "TRADING_MANAGER": "Signal approval, trading decisions, priority management",
        "ANALYST":         "Signal review, quality scoring, market analysis",
        "OPERATOR":        "Signal execution, trade monitoring, P&L recording",
        "VIEWER":          "Read-only access — monitoring and dashboard only",
        # Legacy roles
        "ADMIN":           "Legacy admin role — equivalent to SUPER_ADMIN",
        "MANAGER":         "Legacy manager role — equivalent to TRADING_MANAGER",
        "VIEWER":          "Read-only access",
    }
    return descriptions.get(role, f"Role: {role}")

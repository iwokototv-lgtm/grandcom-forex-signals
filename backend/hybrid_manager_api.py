"""
Professional Hybrid Manager API
Enterprise-Grade Multi-Tier Approval Workflow — FastAPI Router
Gold Trading System v3.0.2

Mounts at: /api/hybrid
All endpoints require a valid JWT that resolves to a hybrid_manager document.

Endpoint Groups:
  AUTH          — Login, profile, token refresh
  MANAGERS      — CRUD, suspend, promote
  SIGNALS       — Approve, reject, adjust, escalate, comment
  RISK          — Limits, circuit breaker, exposure, metrics
  PERFORMANCE   — Manager stats, signal stats, leaderboard
  COLLABORATION — Comments, notes, team activity
  ALERTS        — Create, resolve, list
  DASHBOARD     — Real-time monitoring
  COMPLIANCE    — Audit log, compliance reports
  REPORTS       — Weekly/monthly reports, trend analysis
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
from pydantic import BaseModel, EmailStr, Field, field_validator

from ml_engine.hybrid_manager import (
    HybridManager,
    HybridManagerRole,
    hybrid_manager,
    score_signal_quality,
    classify_signal_risk_tier,
    HYBRID_ROLE_PERMISSIONS,
)
from ml_engine.risk_engine import risk_engine, RiskEngine
from ml_engine.performance_tracker import performance_tracker, PerformanceTracker

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

router = APIRouter(prefix="/api/hybrid", tags=["Hybrid Manager"])

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
    """
    Decode the Bearer JWT and return the hybrid_manager document.
    Raises HTTP 401 on any auth failure.
    """
    try:
        token   = credentials.credentials
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

        if payload.get("type") != "hybrid_manager":
            raise HTTPException(status_code=401, detail="Token is not a hybrid manager token")

        manager_id = payload.get("sub")
        if not manager_id:
            raise HTTPException(status_code=401, detail="Invalid token payload")

        db = _get_db()
        manager = await db.hybrid_managers.find_one(
            {"manager_id": manager_id, "is_active": True, "is_suspended": {"$ne": True}},
            {"password_hash": 0},
        )
        if not manager:
            raise HTTPException(status_code=401, detail="Manager account not found, inactive, or suspended")

        manager.pop("_id", None)
        for ts in ("created_at", "last_login", "last_activity"):
            if manager.get(ts) and hasattr(manager[ts], "isoformat"):
                manager[ts] = manager[ts].isoformat()

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

class HybridLoginRequest(BaseModel):
    email:    EmailStr
    password: str


class AddHybridManagerRequest(BaseModel):
    email:      EmailStr
    full_name:  str
    role:       HybridManagerRole
    password:   str
    department: Optional[str] = None
    metadata:   Optional[Dict[str, Any]] = None


class UpdateHybridManagerRequest(BaseModel):
    role:               Optional[HybridManagerRole] = None
    full_name:          Optional[str]               = None
    is_active:          Optional[bool]              = None
    department:         Optional[str]               = None
    metadata:           Optional[Dict[str, Any]]    = None
    notification_prefs: Optional[Dict[str, Any]]    = None


class SuspendManagerRequest(BaseModel):
    reason: str = Field(min_length=10, description="Reason for suspension (min 10 chars)")


class ApproveSignalRequest(BaseModel):
    signal_id:        str
    notes:            Optional[str]            = None
    adjusted_params:  Optional[Dict[str, Any]] = None


class RejectSignalRequest(BaseModel):
    signal_id: str
    reason:    str = Field(min_length=10, description="Rejection reason (min 10 chars)")
    category:  str = Field(default="QUALITY", description="QUALITY | RISK | COMPLIANCE | DUPLICATE | OTHER")

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        allowed = {"QUALITY", "RISK", "COMPLIANCE", "DUPLICATE", "OTHER"}
        if v.upper() not in allowed:
            raise ValueError(f"category must be one of {allowed}")
        return v.upper()


class AdjustSignalRequest(BaseModel):
    signal_id:   str
    adjustments: Dict[str, Any] = Field(description="Fields to adjust: entry_price, tp1, tp2, tp3, sl_price, lot_size")
    reason:      str = Field(min_length=10, description="Reason for adjustment (min 10 chars)")


class EscalateSignalRequest(BaseModel):
    signal_id:         str
    escalation_reason: str = Field(min_length=10)
    escalate_to_role:  str = Field(default="RISK_MANAGER")


class SubmitSignalReviewRequest(BaseModel):
    signal_id:   str
    signal_data: Dict[str, Any]
    submitted_by: Optional[str] = "SYSTEM"


class AddCommentRequest(BaseModel):
    signal_id:    str
    comment_text: str = Field(min_length=3)
    comment_type: str = Field(default="GENERAL", description="GENERAL | ANALYSIS | RISK_NOTE | DECISION | QUESTION")
    mentions:     Optional[List[str]] = None
    is_private:   bool = False


class AddNoteRequest(BaseModel):
    title:     str = Field(min_length=3)
    content:   str = Field(min_length=10)
    note_type: str = Field(default="GENERAL", description="GENERAL | MARKET_ANALYSIS | RISK_OBSERVATION | STRATEGY | COMPLIANCE")
    signal_id: Optional[str] = None
    tags:      Optional[List[str]] = None


class SetRiskLimitsRequest(BaseModel):
    limits: Dict[str, Any] = Field(description="Risk limit key-value pairs")


class CircuitBreakerRequest(BaseModel):
    reason:        str  = Field(min_length=10)
    halt_trading:  bool = True


class ResetCircuitBreakerRequest(BaseModel):
    reason: str = Field(min_length=10)


class CreateAlertRequest(BaseModel):
    title:    str
    message:  str
    severity: str = Field(default="INFO",    description="INFO | WARNING | CRITICAL")
    category: str = Field(default="GENERAL", description="GENERAL | TRADING | RISK | SYSTEM | SECURITY | COMPLIANCE")


class ResolveAlertRequest(BaseModel):
    resolution_note: Optional[str] = None


class ValidateSignalRiskRequest(BaseModel):
    signal:          Dict[str, Any]
    account_balance: float = Field(default=10000.0, gt=0)
    open_positions:  Optional[List[Dict[str, Any]]] = None


class RecordOutcomeRequest(BaseModel):
    signal_id:  str
    outcome:    str = Field(description="WIN | LOSS | BREAKEVEN")
    pnl:        float
    pnl_pct:    float
    r_multiple: float
    metadata:   Optional[Dict[str, Any]] = None

    @field_validator("outcome")
    @classmethod
    def validate_outcome(cls, v: str) -> str:
        allowed = {"WIN", "LOSS", "BREAKEVEN"}
        if v.upper() not in allowed:
            raise ValueError(f"outcome must be one of {allowed}")
        return v.upper()


class UpdateRiskConfigRequest(BaseModel):
    config: Dict[str, Any] = Field(description="Risk engine configuration key-value pairs")


# ─────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ═══════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────

@router.post("/auth/login", summary="Hybrid Manager login — returns JWT")
async def hybrid_login(body: HybridLoginRequest):
    """
    Authenticate a hybrid manager and return a signed JWT.
    The token must be passed as ``Authorization: Bearer <token>`` on all
    subsequent requests.
    """
    db = _get_db()
    manager = await db.hybrid_managers.find_one(
        {"email": body.email, "is_active": True, "is_suspended": {"$ne": True}}
    )
    if not manager:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not pwd_context.verify(body.password, manager.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    await db.hybrid_managers.update_one(
        {"manager_id": manager["manager_id"]},
        {"$set": {"last_login": datetime.utcnow(), "last_activity": datetime.utcnow()}},
    )

    token = _create_hybrid_token(manager["manager_id"], manager["role"])
    permissions = list(HYBRID_ROLE_PERMISSIONS.get(HybridManagerRole(manager["role"]), set()))

    return {
        "success":      True,
        "access_token": token,
        "token_type":   "bearer",
        "manager_id":   manager["manager_id"],
        "role":         manager["role"],
        "full_name":    manager.get("full_name"),
        "department":   manager.get("department"),
        "permissions":  permissions,
        "expires_in":   f"{JWT_EXPIRY_HRS}h",
    }


@router.get("/auth/me", summary="Get current hybrid manager profile")
async def get_my_profile(current_manager: Dict = Depends(get_current_hybrid_manager)):
    """Return the authenticated manager's own profile with permissions."""
    profile = dict(current_manager)
    role = profile.get("role", "")
    try:
        permissions = list(HYBRID_ROLE_PERMISSIONS.get(HybridManagerRole(role), set()))
    except ValueError:
        permissions = []
    profile["permissions"] = permissions
    return {"success": True, "manager": profile}


@router.post("/auth/refresh", summary="Refresh JWT token")
async def refresh_token(current_manager: Dict = Depends(get_current_hybrid_manager)):
    """Issue a new JWT token for the authenticated manager."""
    token = _create_hybrid_token(current_manager["manager_id"], current_manager["role"])
    return {
        "success":      True,
        "access_token": token,
        "token_type":   "bearer",
        "expires_in":   f"{JWT_EXPIRY_HRS}h",
    }


# ─────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════
# MANAGER MANAGEMENT ENDPOINTS
# ═══════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────

@router.post("/managers", summary="Add a new hybrid manager [SUPER_ADMIN]")
async def add_hybrid_manager(
    body:            AddHybridManagerRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Create a new hybrid manager account. Requires SUPER_ADMIN role."""
    try:
        hashed = pwd_context.hash(body.password)
        result = await hybrid_manager.add_manager(
            requesting_manager=current_manager,
            email=body.email,
            full_name=body.full_name,
            role=body.role,
            password_hash=hashed,
            department=body.department,
            metadata=body.metadata,
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.delete("/managers/{manager_id}", summary="Deactivate a hybrid manager [SUPER_ADMIN]")
async def remove_hybrid_manager(
    manager_id:      str,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Soft-delete (deactivate) a hybrid manager account. Requires SUPER_ADMIN role."""
    try:
        result = await hybrid_manager.remove_manager(current_manager, manager_id)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.post("/managers/{manager_id}/suspend", summary="Suspend a hybrid manager [SUPER_ADMIN]")
async def suspend_hybrid_manager(
    manager_id:      str,
    body:            SuspendManagerRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Temporarily suspend a manager account. Requires SUPER_ADMIN role."""
    try:
        result = await hybrid_manager.suspend_manager(current_manager, manager_id, body.reason)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.put("/managers/{manager_id}", summary="Update a hybrid manager [SUPER_ADMIN]")
async def update_hybrid_manager(
    manager_id:      str,
    body:            UpdateHybridManagerRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Update manager role, name, department, or active status. Requires SUPER_ADMIN role."""
    try:
        updates = body.model_dump(exclude_none=True)
        if "role" in updates:
            updates["role"] = updates["role"].value
        result = await hybrid_manager.update_manager(current_manager, manager_id, updates)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get("/managers", summary="List all hybrid managers")
async def list_hybrid_managers(
    include_inactive:  bool          = Query(default=False),
    role_filter:       Optional[str] = Query(default=None, description="Filter by role"),
    department_filter: Optional[str] = Query(default=None, description="Filter by department"),
    current_manager:   Dict          = Depends(get_current_hybrid_manager),
):
    """List hybrid managers with optional filters. All roles can call this."""
    try:
        return await hybrid_manager.list_managers(
            current_manager, include_inactive, role_filter, department_filter
        )
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get("/managers/{manager_id}", summary="Get a single hybrid manager")
async def get_hybrid_manager(
    manager_id:      str,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Retrieve a hybrid manager by ID. All roles can call this."""
    try:
        result = await hybrid_manager.get_manager(current_manager, manager_id)
        if not result["success"]:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


# ─────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════
# SIGNAL MANAGEMENT ENDPOINTS
# ═══════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────

@router.post("/signals/submit", summary="Submit a signal for hybrid review [SYSTEM/ADMIN]")
async def submit_signal_for_review(
    body:            SubmitSignalReviewRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """
    Submit a signal into the multi-tier approval workflow.
    Scores the signal, classifies risk tier, and sets required approvals.
    """
    try:
        result = await hybrid_manager.submit_signal_for_review(
            signal_id=body.signal_id,
            signal_data=body.signal_data,
            submitted_by=body.submitted_by or current_manager["manager_id"],
        )
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.post("/signals/approve", summary="Approve a signal [SUPER_ADMIN, RISK_MANAGER, TRADING_MANAGER]")
async def approve_signal(
    body:            ApproveSignalRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """
    Approve a signal in the multi-tier workflow.
    Signal activates when required approval count is reached.
    """
    try:
        result = await hybrid_manager.approve_signal(
            requesting_manager=current_manager,
            signal_id=body.signal_id,
            notes=body.notes,
            adjusted_params=body.adjusted_params,
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.post("/signals/reject", summary="Reject a signal [SUPER_ADMIN, RISK_MANAGER, TRADING_MANAGER, OPERATOR]")
async def reject_signal(
    body:            RejectSignalRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """
    Reject a signal — immediately removes it from the approval queue.
    Rejection reason is mandatory (min 10 characters).
    """
    try:
        result = await hybrid_manager.reject_signal(
            requesting_manager=current_manager,
            signal_id=body.signal_id,
            reason=body.reason,
            category=body.category,
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.post("/signals/adjust", summary="Adjust signal parameters [SUPER_ADMIN, RISK_MANAGER, TRADING_MANAGER]")
async def adjust_signal(
    body:            AdjustSignalRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """
    Adjust signal parameters (entry, TP, SL, lot size) before approval.
    Adjustment reason is mandatory.
    """
    try:
        result = await hybrid_manager.adjust_signal(
            requesting_manager=current_manager,
            signal_id=body.signal_id,
            adjustments=body.adjustments,
            reason=body.reason,
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.post("/signals/escalate", summary="Escalate a signal to higher authority [SUPER_ADMIN, RISK_MANAGER, TRADING_MANAGER]")
async def escalate_signal(
    body:            EscalateSignalRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Escalate a signal to a higher authority for review."""
    try:
        result = await hybrid_manager.escalate_signal(
            requesting_manager=current_manager,
            signal_id=body.signal_id,
            escalation_reason=body.escalation_reason,
            escalate_to_role=body.escalate_to_role,
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get("/signals/pending", summary="List signals pending review")
async def get_pending_signals(
    limit:             int           = Query(default=50, ge=1, le=200),
    risk_tier:         Optional[str] = Query(default=None, description="LOW | MEDIUM | HIGH | CRITICAL"),
    pair:              Optional[str] = Query(default=None, description="e.g. XAUUSD"),
    min_quality_score: Optional[float] = Query(default=None, ge=0, le=100),
    current_manager:   Dict          = Depends(get_current_hybrid_manager),
):
    """Return signals awaiting review in the hybrid workflow. All roles."""
    try:
        return await hybrid_manager.get_pending_signals(
            current_manager, limit, risk_tier, pair, min_quality_score
        )
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get("/signals/{signal_id}", summary="Get full signal detail")
async def get_signal_detail(
    signal_id:       str,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get full signal detail including approval history, comments, adjustments. All roles."""
    try:
        result = await hybrid_manager.get_signal_detail(current_manager, signal_id)
        if not result["success"]:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.post("/signals/score", summary="Score a signal's quality [all roles]")
async def score_signal(
    signal_data:     Dict[str, Any],
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """
    Score a signal's quality without submitting it for review.
    Returns composite score, grade, dimension breakdown, and recommendation.
    """
    quality = score_signal_quality(signal_data)
    return {
        "success": True,
        "quality": quality,
    }


@router.post("/signals/validate-risk", summary="Validate signal against risk limits [all roles]")
async def validate_signal_risk(
    body:            ValidateSignalRiskRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """
    Run comprehensive risk validation against a signal.
    Returns approval status, violations, warnings, and risk score.
    """
    result = risk_engine.validate_signal(
        signal=body.signal,
        account_balance=body.account_balance,
        open_positions=body.open_positions or [],
    )
    return {"success": True, "validation": result}


# ─────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════
# RISK MANAGEMENT ENDPOINTS
# ═══════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────

@router.post("/risk/limits", summary="Set risk management limits [SUPER_ADMIN, RISK_MANAGER]")
async def set_risk_limits(
    body:            SetRiskLimitsRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Set or update risk management limits. Requires SUPER_ADMIN or RISK_MANAGER role."""
    try:
        result = await hybrid_manager.set_risk_limits(current_manager, body.limits)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get("/risk/config", summary="Get current risk configuration [all roles]")
async def get_risk_config(current_manager: Dict = Depends(get_current_hybrid_manager)):
    """Get current risk management configuration. All roles."""
    try:
        return await hybrid_manager.get_risk_config(current_manager)
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.post("/risk/circuit-breaker/trigger", summary="Trigger circuit breaker [SUPER_ADMIN, RISK_MANAGER]")
async def trigger_circuit_breaker(
    body:            CircuitBreakerRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Manually trigger the circuit breaker to halt trading. Requires SUPER_ADMIN or RISK_MANAGER."""
    try:
        result = await hybrid_manager.trigger_circuit_breaker(
            current_manager, body.reason, body.halt_trading
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.post("/risk/circuit-breaker/reset", summary="Reset circuit breaker [SUPER_ADMIN, RISK_MANAGER]")
async def reset_circuit_breaker(
    body:            ResetCircuitBreakerRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Reset the circuit breaker and resume trading. Requires SUPER_ADMIN or RISK_MANAGER."""
    try:
        result = await hybrid_manager.reset_circuit_breaker(current_manager, body.reason)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.post("/risk/validate", summary="Validate a signal against risk engine [all roles]")
async def validate_risk(
    body:            ValidateSignalRiskRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Run full risk engine validation against a signal."""
    result = risk_engine.validate_signal(
        signal=body.signal,
        account_balance=body.account_balance,
        open_positions=body.open_positions or [],
    )
    return {"success": True, "risk_validation": result}


@router.get("/risk/metrics", summary="Get real-time risk metrics [all roles]")
async def get_risk_metrics(
    account_balance:     float = Query(default=10000.0, gt=0),
    equity_peak:         float = Query(default=10000.0, gt=0),
    daily_pnl:           float = Query(default=0.0),
    weekly_pnl:          float = Query(default=0.0),
    monthly_pnl:         float = Query(default=0.0),
    consecutive_losses:  int   = Query(default=0, ge=0),
    current_manager:     Dict  = Depends(get_current_hybrid_manager),
):
    """Get real-time risk metrics snapshot. All roles."""
    metrics = risk_engine.get_real_time_metrics(
        account_balance=account_balance,
        equity_peak=equity_peak,
        daily_pnl=daily_pnl,
        weekly_pnl=weekly_pnl,
        monthly_pnl=monthly_pnl,
        open_positions=[],
        consecutive_losses=consecutive_losses,
    )
    return {"success": True, "metrics": metrics}


@router.post("/risk/engine/config", summary="Update risk engine configuration [SUPER_ADMIN, RISK_MANAGER]")
async def update_risk_engine_config(
    body:            UpdateRiskConfigRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Update risk engine configuration parameters."""
    try:
        from ml_engine.hybrid_manager import check_hybrid_permission
        check_hybrid_permission(current_manager, "risk:set_limits")
        result = await risk_engine.update_config(body.config, current_manager["manager_id"])
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get("/risk/events", summary="Get recent risk events [all roles]")
async def get_risk_events(
    hours:    int           = Query(default=24, ge=1, le=720),
    severity: Optional[str] = Query(default=None, description="INFO | WARNING | CRITICAL"),
    limit:    int           = Query(default=100, ge=1, le=500),
    current_manager: Dict   = Depends(get_current_hybrid_manager),
):
    """Get recent risk events from the database. All roles."""
    events = await risk_engine.get_risk_events(hours=hours, severity_filter=severity, limit=limit)
    return {"success": True, "events": events, "count": len(events)}


# ─────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════
# PERFORMANCE ANALYTICS ENDPOINTS
# ═══════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────

@router.get("/performance/managers", summary="Get manager performance metrics [all roles]")
async def get_manager_performance(
    days:              int           = Query(default=30, ge=1, le=365),
    target_manager_id: Optional[str] = Query(default=None, description="Specific manager ID"),
    current_manager:   Dict          = Depends(get_current_hybrid_manager),
):
    """Get detailed performance metrics for managers. All roles."""
    try:
        return await hybrid_manager.get_manager_performance(
            current_manager, target_manager_id, days
        )
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get("/performance/managers/{manager_id}/detail", summary="Get detailed manager metrics [all roles]")
async def get_manager_detail_metrics(
    manager_id:      str,
    days:            int  = Query(default=30, ge=1, le=365),
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get detailed performance metrics for a specific manager. All roles."""
    result = await performance_tracker.get_manager_metrics(manager_id, days)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "Manager not found"))
    return result


@router.get("/performance/signals", summary="Get signal performance statistics [all roles]")
async def get_signal_performance(
    days:            int  = Query(default=30, ge=1, le=365),
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get aggregate signal approval/rejection statistics. All roles."""
    try:
        return await hybrid_manager.get_signal_performance_stats(current_manager, days)
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get("/performance/signals/quality", summary="Get signal quality statistics [all roles]")
async def get_signal_quality_stats(
    days:             int           = Query(default=30, ge=1, le=365),
    pair_filter:      Optional[str] = Query(default=None),
    strategy_filter:  Optional[str] = Query(default=None),
    current_manager:  Dict          = Depends(get_current_hybrid_manager),
):
    """Get aggregate signal quality statistics. All roles."""
    return await performance_tracker.get_signal_quality_stats(days, pair_filter, strategy_filter)


@router.get("/performance/leaderboard", summary="Get manager leaderboard [all roles]")
async def get_leaderboard(
    days:            int  = Query(default=30, ge=1, le=365),
    metric:          str  = Query(default="total_decisions", description="total_decisions | approval_rate | quality_score | activity_score"),
    limit:           int  = Query(default=10, ge=1, le=50),
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get manager leaderboard ranked by specified metric. All roles."""
    return await performance_tracker.get_leaderboard(days, metric, limit)


@router.get("/performance/outcomes", summary="Get signal outcome statistics [all roles]")
async def get_outcome_stats(
    days:            int           = Query(default=30, ge=1, le=365),
    pair_filter:     Optional[str] = Query(default=None),
    current_manager: Dict          = Depends(get_current_hybrid_manager),
):
    """Get aggregate outcome statistics for completed signals. All roles."""
    return await performance_tracker.get_outcome_stats(days, pair_filter)


@router.post("/performance/outcomes/record", summary="Record signal outcome [SUPER_ADMIN, RISK_MANAGER, TRADING_MANAGER]")
async def record_signal_outcome(
    body:            RecordOutcomeRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Record the outcome of a completed signal for performance tracking."""
    try:
        from ml_engine.hybrid_manager import check_hybrid_permission
        check_hybrid_permission(current_manager, "signal:approve")
        result = await performance_tracker.record_signal_outcome(
            signal_id=body.signal_id,
            outcome=body.outcome,
            pnl=body.pnl,
            pnl_pct=body.pnl_pct,
            r_multiple=body.r_multiple,
            metadata=body.metadata,
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


# ─────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════
# TEAM COLLABORATION ENDPOINTS
# ═══════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────

@router.post("/collaboration/comments", summary="Add a comment to a signal [all active roles]")
async def add_comment(
    body:            AddCommentRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Add a collaboration comment/note to a signal."""
    try:
        result = await hybrid_manager.add_comment(
            requesting_manager=current_manager,
            signal_id=body.signal_id,
            comment_text=body.comment_text,
            comment_type=body.comment_type,
            mentions=body.mentions,
            is_private=body.is_private,
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.post("/collaboration/notes", summary="Add a standalone team note [all active roles]")
async def add_note(
    body:            AddNoteRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Add a standalone team note (not tied to a specific signal)."""
    try:
        result = await hybrid_manager.add_note(
            requesting_manager=current_manager,
            title=body.title,
            content=body.content,
            note_type=body.note_type,
            signal_id=body.signal_id,
            tags=body.tags,
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get("/collaboration/activity", summary="Get recent team activity [all roles]")
async def get_team_activity(
    hours:           int  = Query(default=24, ge=1, le=168),
    limit:           int  = Query(default=100, ge=1, le=500),
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Get recent team collaboration activity (comments, notes, decisions). All roles."""
    try:
        return await hybrid_manager.get_team_activity(current_manager, hours, limit)
    except PermissionError as exc:
        _handle_permission_error(exc)


# ─────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════
# ALERTS ENDPOINTS
# ═══════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────

@router.post("/alerts", summary="Create a system alert [SUPER_ADMIN, RISK_MANAGER, TRADING_MANAGER, OPERATOR]")
async def create_alert(
    body:            CreateAlertRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Create a new system alert."""
    try:
        result = await hybrid_manager.create_manual_alert(
            requesting_manager=current_manager,
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


@router.post("/alerts/{alert_id}/resolve", summary="Resolve an alert [SUPER_ADMIN, RISK_MANAGER, TRADING_MANAGER]")
async def resolve_alert(
    alert_id:        str,
    body:            ResolveAlertRequest,
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Mark an alert as resolved."""
    try:
        result = await hybrid_manager.resolve_alert(
            current_manager, alert_id, body.resolution_note
        )
        if not result["success"]:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get("/alerts", summary="List system alerts [all roles]")
async def list_alerts(
    include_resolved: bool          = Query(default=False),
    severity:         Optional[str] = Query(default=None, description="INFO | WARNING | CRITICAL"),
    category:         Optional[str] = Query(default=None, description="GENERAL | TRADING | RISK | SYSTEM | SECURITY | COMPLIANCE"),
    limit:            int           = Query(default=50, ge=1, le=200),
    current_manager:  Dict          = Depends(get_current_hybrid_manager),
):
    """List hybrid system alerts. All roles."""
    try:
        return await hybrid_manager.list_alerts(
            current_manager, include_resolved, severity, category, limit
        )
    except PermissionError as exc:
        _handle_permission_error(exc)


# ─────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════
# DASHBOARD ENDPOINTS
# ═══════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────

@router.get("/dashboard", summary="Real-time monitoring dashboard [all roles]")
async def get_dashboard(current_manager: Dict = Depends(get_current_hybrid_manager)):
    """
    Get the real-time monitoring dashboard data.
    Includes signal counts, alert summary, risk status, and recent decisions.
    All roles.
    """
    try:
        return await hybrid_manager.get_dashboard(current_manager)
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get("/dashboard/system-status", summary="System health status [all roles]")
async def get_system_status(current_manager: Dict = Depends(get_current_hybrid_manager)):
    """Get system health status including DB, CPU, memory, and active alerts."""
    try:
        import psutil
        cpu_pct  = psutil.cpu_percent(interval=0.5)
        mem      = psutil.virtual_memory()
        disk     = psutil.disk_usage("/")
        mem_pct  = mem.percent
        disk_pct = disk.percent
    except Exception:
        cpu_pct = mem_pct = disk_pct = -1.0

    db = _get_db()
    try:
        import asyncio
        await asyncio.wait_for(db.command("ping"), timeout=3)
        db_status = "HEALTHY"
    except Exception as exc:
        db_status = f"UNHEALTHY: {exc}"

    overall = "HEALTHY"
    if db_status != "HEALTHY" or cpu_pct > 85 or mem_pct > 85 or disk_pct > 90:
        overall = "DEGRADED"

    return {
        "success": True,
        "status": {
            "overall":        overall,
            "timestamp":      datetime.utcnow().isoformat(),
            "version":        "3.0.2",
            "database":       db_status,
            "cpu_percent":    round(cpu_pct, 1),
            "memory_percent": round(mem_pct, 1),
            "disk_percent":   round(disk_pct, 1),
        },
    }


# ─────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════
# COMPLIANCE & AUDIT ENDPOINTS
# ═══════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────

@router.get("/audit", summary="View compliance audit trail [all roles]")
async def get_audit_log(
    limit:             int           = Query(default=100, ge=1, le=1000),
    manager_id_filter: Optional[str] = Query(default=None, alias="manager_id"),
    action_filter:     Optional[str] = Query(default=None, alias="action"),
    since_hours:       int           = Query(default=168, ge=1, le=8760, description="Hours to look back (default 7 days)"),
    current_manager:   Dict          = Depends(get_current_hybrid_manager),
):
    """Retrieve the full compliance audit trail with optional filters. All roles."""
    try:
        return await hybrid_manager.get_audit_log(
            current_manager, limit, manager_id_filter, action_filter, since_hours
        )
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get("/compliance/report", summary="Generate compliance report [all roles]")
async def get_compliance_report(
    days:            int  = Query(default=30, ge=1, le=365),
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Generate a compliance report for the specified period. All roles."""
    try:
        return await hybrid_manager.get_compliance_report(current_manager, days)
    except PermissionError as exc:
        _handle_permission_error(exc)


# ─────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════
# REPORTS ENDPOINTS
# ═══════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────

@router.get("/reports/weekly", summary="Generate weekly performance report [all roles]")
async def get_weekly_report(
    week_offset:     int  = Query(default=0, ge=0, le=52, description="0=current week, 1=last week"),
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Generate a comprehensive weekly performance report. All roles."""
    return await performance_tracker.generate_weekly_report(week_offset)


@router.get("/reports/monthly", summary="Generate monthly performance report [all roles]")
async def get_monthly_report(
    month_offset:    int  = Query(default=0, ge=0, le=12, description="0=current month, 1=last month"),
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Generate a comprehensive monthly performance report. All roles."""
    return await performance_tracker.generate_monthly_report(month_offset)


@router.get("/reports/trends", summary="Get performance trend analysis [all roles]")
async def get_trend_analysis(
    days:            int  = Query(default=90, ge=7, le=365),
    granularity:     str  = Query(default="daily", description="daily | weekly | monthly"),
    current_manager: Dict = Depends(get_current_hybrid_manager),
):
    """Analyse performance trends over time. All roles."""
    return await performance_tracker.get_trend_analysis(days, granularity)


# ─────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════
# UTILITY ENDPOINTS
# ═══════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────

@router.get("/roles", summary="List all roles and their permissions [all roles]")
async def list_roles(current_manager: Dict = Depends(get_current_hybrid_manager)):
    """List all hybrid manager roles and their associated permissions."""
    roles_info = {}
    for role in HybridManagerRole:
        permissions = list(HYBRID_ROLE_PERMISSIONS.get(role, set()))
        permissions.sort()
        roles_info[role.value] = {
            "role":        role.value,
            "permissions": permissions,
            "count":       len(permissions),
        }
    return {
        "success": True,
        "roles":   roles_info,
        "total_roles": len(roles_info),
    }


@router.get("/health", summary="Hybrid Manager API health check [public]")
async def health_check():
    """Public health check endpoint — no authentication required."""
    return {
        "success":   True,
        "service":   "Hybrid Manager API",
        "version":   "3.0.2",
        "status":    "HEALTHY",
        "timestamp": datetime.utcnow().isoformat(),
    }

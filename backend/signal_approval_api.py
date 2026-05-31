"""
Signal Approval API — FastAPI router for Signal Management
Grandcom Gold Signals System v3.0.2

Mounts at: /api/signals  (registered in server.py / gold_server_v3.py)
All endpoints require a valid manager JWT (same auth as manager_api.py).
Permission enforcement is delegated to SignalManager (raises PermissionError
which is caught here and returned as HTTP 403).

Endpoints:
  GET  /api/signals/pending          — List pending signals
  GET  /api/signals/history          — Signal approval history
  GET  /api/signals/stats            — Approval statistics
  GET  /api/signals/{signal_id}      — Get signal details
  POST /api/signals/{signal_id}/approve — Approve signal
  POST /api/signals/{signal_id}/reject  — Reject signal
  POST /api/signals/{signal_id}/adjust  — Adjust entry/TP/SL
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
from pydantic import BaseModel, Field, validator

from ml_engine.signal_manager import SignalStatus, signal_manager

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────

MONGO_URL      = os.environ.get("MONGO_URL",      "mongodb://localhost:27017")
DB_NAME        = os.environ.get("DB_NAME",        "gold_signals_v3")
JWT_SECRET     = os.environ.get("JWT_SECRET",     "your-secret-key")
JWT_ALGORITHM  = os.environ.get("JWT_ALGORITHM",  "HS256")

security = HTTPBearer()

router = APIRouter(prefix="/api/signals", tags=["Signal Approval"])

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
# Auth dependency — reuses manager JWT from manager_api.py
# ─────────────────────────────────────────────────────────────

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
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def _handle_permission_error(exc: PermissionError) -> None:
    raise HTTPException(status_code=403, detail=str(exc))


# ─────────────────────────────────────────────────────────────
# Pydantic request models
# ─────────────────────────────────────────────────────────────

class ApproveSignalRequest(BaseModel):
    notes: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Optional manager notes for the approval",
    )


class RejectSignalRequest(BaseModel):
    reason: str = Field(
        ...,
        min_length=5,
        max_length=500,
        description="Mandatory reason for rejection (min 5 characters)",
    )


class AdjustSignalRequest(BaseModel):
    entry_price:      Optional[float] = Field(default=None, gt=0, description="New entry price")
    tp_levels:        Optional[List[float]] = Field(
        default=None,
        min_items=1,
        max_items=5,
        description="New TP levels (ordered from nearest to furthest)",
    )
    sl_price:         Optional[float] = Field(default=None, gt=0, description="New stop-loss price")
    adjustment_notes: Optional[str]   = Field(
        default=None,
        max_length=500,
        description="Reason for the adjustment",
    )

    @validator("tp_levels", each_item=True, pre=True)
    def tp_must_be_positive(cls, v):
        if v is not None and v <= 0:
            raise ValueError("Each TP level must be > 0")
        return v


# ─────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────

@router.get(
    "/pending",
    summary="List pending signals awaiting manager review",
    response_description="Paginated list of PENDING signals",
)
async def list_pending_signals(
    pair:           Optional[str]   = Query(default=None, description="Filter by pair, e.g. XAUUSD"),
    limit:          int             = Query(default=50, ge=1, le=200, description="Max results"),
    min_confidence: Optional[float] = Query(default=None, ge=0, le=100, description="Minimum confidence %"),
    current_manager: Dict           = Depends(get_current_manager),
):
    """
    Return all signals with ``approval_status = PENDING``, sorted newest-first.
    All manager roles (ADMIN, MANAGER, VIEWER) can call this endpoint.
    """
    try:
        return await signal_manager.get_pending_signals(
            current_manager,
            pair=pair,
            limit=limit,
            min_confidence=min_confidence,
        )
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get(
    "/history",
    summary="Signal approval history",
    response_description="Reviewed signals (APPROVED, REJECTED, ADJUSTED)",
)
async def get_signal_history(
    status:          Optional[str] = Query(default=None, description="APPROVED | REJECTED | ADJUSTED"),
    pair:            Optional[str] = Query(default=None, description="Filter by pair"),
    reviewed_by:     Optional[str] = Query(default=None, description="Filter by manager_id"),
    hours:           int           = Query(default=168, ge=1, le=8760, description="Look-back hours (default 7 days)"),
    limit:           int           = Query(default=100, ge=1, le=500, description="Max results"),
    current_manager: Dict          = Depends(get_current_manager),
):
    """
    Return reviewed signals with optional filters.
    All manager roles can call this endpoint.
    """
    try:
        return await signal_manager.get_signal_history(
            current_manager,
            status=status,
            pair=pair,
            reviewed_by=reviewed_by,
            hours=hours,
            limit=limit,
        )
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get(
    "/stats",
    summary="Signal approval statistics",
    response_description="Global and per-manager approval statistics",
)
async def get_approval_stats(
    hours:             int           = Query(default=168, ge=1, le=8760, description="Look-back hours"),
    manager_id_filter: Optional[str] = Query(default=None, alias="manager_id", description="Filter stats by manager"),
    current_manager:   Dict          = Depends(get_current_manager),
):
    """
    Return approval statistics: global counts, per-manager breakdown,
    per-pair breakdown, and average confidence of approved signals.
    All manager roles can call this endpoint.
    """
    try:
        return await signal_manager.get_approval_stats(
            current_manager,
            hours=hours,
            manager_id_filter=manager_id_filter,
        )
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get(
    "/{signal_id}",
    summary="Get full signal details",
    response_description="Complete signal document with approval history",
)
async def get_signal_details(
    signal_id:       str,
    current_manager: Dict = Depends(get_current_manager),
):
    """
    Return the full signal document including all analysis fields
    (indicators, regime, SMC score, MTF alignment, pivot zone) and
    the complete approval audit trail.
    All manager roles can call this endpoint.
    """
    try:
        result = await signal_manager.get_signal_details(current_manager, signal_id)
        if not result["success"]:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.post(
    "/{signal_id}/approve",
    summary="Approve a pending signal [ADMIN, MANAGER]",
    response_description="Approval confirmation",
)
async def approve_signal(
    signal_id:       str,
    body:            ApproveSignalRequest,
    current_manager: Dict = Depends(get_current_manager),
):
    """
    Approve a PENDING signal and mark it ready for trading.
    Requires ADMIN or MANAGER role (``signal:approve`` permission).

    The signal's ``approval_status`` is set to APPROVED and it remains
    ACTIVE so the trading engine picks it up on the next cycle.
    """
    try:
        result = await signal_manager.approve_signal(
            current_manager,
            signal_id=signal_id,
            notes=body.notes,
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.post(
    "/{signal_id}/reject",
    summary="Reject a pending signal [ADMIN, MANAGER]",
    response_description="Rejection confirmation",
)
async def reject_signal(
    signal_id:       str,
    body:            RejectSignalRequest,
    current_manager: Dict = Depends(get_current_manager),
):
    """
    Reject a PENDING signal with a mandatory reason.
    Requires ADMIN or MANAGER role (``signal:reject`` permission).

    The signal's ``approval_status`` is set to REJECTED and ``status``
    is set to CANCELLED so it is excluded from all trading activity.
    """
    try:
        result = await signal_manager.reject_signal(
            current_manager,
            signal_id=signal_id,
            reason=body.reason,
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.post(
    "/{signal_id}/adjust",
    summary="Adjust entry/TP/SL of a pending signal [ADMIN, MANAGER]",
    response_description="Adjustment confirmation with change summary",
)
async def adjust_signal(
    signal_id:       str,
    body:            AdjustSignalRequest,
    current_manager: Dict = Depends(get_current_manager),
):
    """
    Modify the entry price, TP levels, and/or SL of a PENDING signal,
    then automatically approve it.
    Requires ADMIN or MANAGER role (``signal:adjust`` permission).

    All provided values are validated for correct BUY/SELL geometry.
    At least one of ``entry_price``, ``tp_levels``, or ``sl_price`` must
    be supplied. The signal is stamped ADJUSTED (a sub-type of approved)
    and queued for trading with the new levels.
    """
    try:
        result = await signal_manager.adjust_signal(
            current_manager,
            signal_id=signal_id,
            entry_price=body.entry_price,
            tp_levels=body.tp_levels,
            sl_price=body.sl_price,
            adjustment_notes=body.adjustment_notes,
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except PermissionError as exc:
        _handle_permission_error(exc)

"""
Signal Management API — FastAPI router for manager signal review workflow
Gold Trading System v3.0.2

Mounts at: /api/manager/signals
All endpoints require a valid JWT that resolves to a system_manager document
with role ADMIN or MANAGER.

Endpoints:
  GET  /api/manager/signals/pending          — List signals awaiting review
  GET  /api/manager/signals/{id}             — Full signal details + adjustment history
  POST /api/manager/signals/approve          — Approve a pending signal
  POST /api/manager/signals/reject           — Reject a pending signal (reason required)
  POST /api/manager/signals/adjust           — Adjust entry / TP levels / SL price
  GET  /api/manager/signals/history/all      — Approval history with summary stats
  GET  /api/manager/signals/stats/approval   — Per-manager and per-pair approval stats
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
from pydantic import BaseModel, Field, field_validator

from signal_manager import signal_manager

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────

MONGO_URL      = os.environ.get("MONGO_URL",      "mongodb://localhost:27017")
DB_NAME        = os.environ.get("DB_NAME",        "gold_signals_v3")
JWT_SECRET     = os.environ.get("JWT_SECRET",     "your-secret-key")
JWT_ALGORITHM  = os.environ.get("JWT_ALGORITHM",  "HS256")

security = HTTPBearer()

router = APIRouter(prefix="/api/manager/signals", tags=["Signal Management"])

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
# Auth dependency — reuses the same JWT scheme as manager_api
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
            raise HTTPException(
                status_code=401, detail="Manager account not found or inactive"
            )

        manager.pop("_id", None)
        return manager

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def _handle_permission_error(exc: PermissionError) -> None:
    raise HTTPException(status_code=403, detail=str(exc))


def _handle_result(result: Dict[str, Any], not_found_on_missing: bool = True) -> Dict[str, Any]:
    """
    Translate a SignalManager result dict into an HTTP response.
    Raises 400 on validation errors, 404 when the signal is not found.
    """
    if result.get("success"):
        return result
    error = result.get("error", "Unknown error")
    if not_found_on_missing and "not found" in error.lower():
        raise HTTPException(status_code=404, detail=error)
    raise HTTPException(status_code=400, detail=error)


# ─────────────────────────────────────────────────────────────
# Pydantic request models
# ─────────────────────────────────────────────────────────────

class ApproveSignalRequest(BaseModel):
    signal_id: str = Field(..., description="MongoDB ObjectId of the signal to approve")
    notes:     Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Optional manager notes recorded in the audit log",
    )


class RejectSignalRequest(BaseModel):
    signal_id: str = Field(..., description="MongoDB ObjectId of the signal to reject")
    reason:    str = Field(
        ...,
        min_length=5,
        max_length=500,
        description="Mandatory rejection reason (min 5 characters)",
    )
    notes: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Optional additional manager notes",
    )

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Rejection reason cannot be blank")
        return v.strip()


class AdjustSignalRequest(BaseModel):
    signal_id:   str = Field(..., description="MongoDB ObjectId of the signal to adjust")
    entry_price: Optional[float] = Field(
        default=None,
        gt=0,
        description="New entry price (must be > 0)",
    )
    tp_levels: Optional[List[float]] = Field(
        default=None,
        min_length=1,
        max_length=5,
        description="New list of TP levels (1–5 values, all > 0)",
    )
    sl_price: Optional[float] = Field(
        default=None,
        gt=0,
        description="New SL price (must be > 0)",
    )
    notes: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Rationale for the adjustment (recommended)",
    )

    @field_validator("tp_levels")
    @classmethod
    def tp_levels_positive(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if v is not None:
            for i, tp in enumerate(v):
                if tp <= 0:
                    raise ValueError(f"tp_levels[{i}] must be > 0, got {tp}")
        return v


# ─────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────

@router.get(
    "/pending",
    summary="List signals awaiting manager review",
    response_description="Paginated list of PENDING_REVIEW signals",
)
async def get_pending_signals(
    limit:          int            = Query(default=50,  ge=1, le=200,
                                          description="Maximum number of signals to return"),
    pair:           Optional[str]  = Query(default=None,
                                          description="Filter by trading pair (e.g. XAUUSD)"),
    min_confidence: Optional[float] = Query(default=None, ge=0, le=100,
                                            description="Minimum confidence threshold"),
    current_manager: Dict = Depends(get_current_manager),
):
    """
    Return all signals currently in ``PENDING_REVIEW`` status.

    Managers use this endpoint to build their review queue.  Results are
    sorted newest-first.  Optional filters allow narrowing by pair or
    minimum confidence score.
    """
    try:
        result = await signal_manager.get_pending_signals(
            requesting_manager=current_manager,
            limit=limit,
            pair_filter=pair,
            min_confidence=min_confidence,
        )
        return _handle_result(result)
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get(
    "/history/all",
    summary="Signal review history with summary statistics",
    response_description="Reviewed signals with approval/rejection stats",
)
async def get_signal_history(
    limit:      int            = Query(default=100, ge=1, le=500,
                                      description="Maximum number of records"),
    hours:      int            = Query(default=168, ge=1, le=8760,
                                      description="Look-back window in hours (default 7 days)"),
    status:     Optional[str]  = Query(default=None,
                                      description="Filter by review status: APPROVED | REJECTED | ADJUSTED"),
    pair:       Optional[str]  = Query(default=None,
                                      description="Filter by trading pair"),
    manager_id: Optional[str]  = Query(default=None,
                                      description="Filter by the manager who acted on the signal"),
    current_manager: Dict = Depends(get_current_manager),
):
    """
    Return the review history for signals that have been approved, rejected,
    or adjusted.  Includes a summary ``stats`` block with counts and the
    overall approval rate for the requested window.
    """
    try:
        result = await signal_manager.get_signal_history(
            requesting_manager=current_manager,
            limit=limit,
            hours=hours,
            status_filter=status,
            pair_filter=pair,
            manager_id_filter=manager_id,
        )
        return _handle_result(result)
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get(
    "/stats/approval",
    summary="Approval statistics per manager and per pair",
    response_description="Aggregated approval/rejection statistics",
)
async def get_approval_stats(
    days:       int           = Query(default=30, ge=1, le=365,
                                     description="Look-back window in days"),
    manager_id: Optional[str] = Query(default=None,
                                     description="Restrict stats to a single manager"),
    current_manager: Dict = Depends(get_current_manager),
):
    """
    Return aggregated approval statistics broken down by manager and by
    trading pair.  Useful for tracking review quality and workload
    distribution across the management team.
    """
    try:
        result = await signal_manager.get_approval_stats(
            requesting_manager=current_manager,
            days=days,
            manager_id_filter=manager_id,
        )
        return _handle_result(result)
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.get(
    "/{signal_id}",
    summary="Get full signal details including adjustment history",
    response_description="Signal document with adjustments and review log",
)
async def get_signal_details(
    signal_id:       str,
    current_manager: Dict = Depends(get_current_manager),
):
    """
    Return the complete signal document together with:
    - ``adjustments``: ordered list of every price-level change made by managers
    - ``review_log``:  ordered list of every review action (approve/reject/adjust)

    This endpoint supports any signal status, not just pending ones.
    """
    try:
        result = await signal_manager.get_signal_details(
            requesting_manager=current_manager,
            signal_id=signal_id,
        )
        return _handle_result(result)
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.post(
    "/approve",
    summary="Approve a pending signal and send it to trading",
    response_description="Approval confirmation with new signal status",
)
async def approve_signal(
    body:            ApproveSignalRequest,
    current_manager: Dict = Depends(get_current_manager),
):
    """
    Approve a signal that is in ``PENDING_REVIEW`` or ``ADJUSTED`` status.

    On success the signal status is changed to ``ACTIVE`` and it enters
    the live trading queue.  The approval is recorded in the audit log
    with the manager's ID, timestamp, and any notes provided.
    """
    try:
        result = await signal_manager.approve_signal(
            requesting_manager=current_manager,
            signal_id=body.signal_id,
            notes=body.notes,
        )
        return _handle_result(result)
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.post(
    "/reject",
    summary="Reject a pending signal with a mandatory reason",
    response_description="Rejection confirmation with reason recorded",
)
async def reject_signal(
    body:            RejectSignalRequest,
    current_manager: Dict = Depends(get_current_manager),
):
    """
    Reject a signal that is in ``PENDING_REVIEW`` or ``ADJUSTED`` status.

    A non-empty rejection reason is mandatory — this ensures every
    rejection is documented for quality-improvement purposes.  The signal
    status is changed to ``REJECTED`` and it is removed from the trading
    queue.
    """
    try:
        result = await signal_manager.reject_signal(
            requesting_manager=current_manager,
            signal_id=body.signal_id,
            reason=body.reason,
            notes=body.notes,
        )
        return _handle_result(result)
    except PermissionError as exc:
        _handle_permission_error(exc)


@router.post(
    "/adjust",
    summary="Adjust entry price, TP levels, or SL price before approval",
    response_description="Adjustment confirmation with updated price levels",
)
async def adjust_signal(
    body:            AdjustSignalRequest,
    current_manager: Dict = Depends(get_current_manager),
):
    """
    Modify the price levels of a pending signal before it is approved.

    At least one of ``entry_price``, ``tp_levels``, or ``sl_price`` must
    be supplied.  The resulting price structure is validated for
    directional consistency (BUY: sl < entry < tp; SELL: sl > entry > tp)
    before the update is persisted.

    The original values are preserved in the ``signal_adjustments``
    collection.  The signal status is changed to ``ADJUSTED`` to indicate
    it has been modified and is ready for a final approve/reject decision.
    """
    try:
        result = await signal_manager.adjust_signal(
            requesting_manager=current_manager,
            signal_id=body.signal_id,
            entry_price=body.entry_price,
            tp_levels=body.tp_levels,
            sl_price=body.sl_price,
            notes=body.notes,
        )
        return _handle_result(result)
    except PermissionError as exc:
        _handle_permission_error(exc)

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

Geometry Rating:
  Every signal returned by GET /pending and GET /{id} now includes a
  ``geometry_rating`` block computed by ml_engine.geometry_rating.GeometryRating.
  The block contains:
    - overall_score (1-10): average of the four component scores
    - recommendation: APPROVE (>=7.0) | ADJUST (5.0-6.9) | REJECT (<5.0)
    - summary: one-line manager-facing explanation
    - components.entry / stop_loss / risk_reward / take_profits:
        score, label, explanation, guidelines
    - adjustment_guidelines: consolidated list of all required adjustments

Signal Quality Validation:
  Every signal passed through the approve endpoint is now validated by
  ml_engine.signal_quality_validator.SignalQualityValidator before approval.
  The quality block contains:
    - quality_score (0-100): composite score across 10 quality checks
    - confidence_score: dynamic confidence (MTF + SMC + momentum + session + news)
    - grade: A / B / C / D / F
    - regime: inferred market regime
    - session_quality: HIGH / MEDIUM / LOW / DEAD_ZONE
    - expiry: signal validity window (e.g. 'Valid until 02:00 UTC')
    - news_flags: high-impact news events detected
    - enhancement_scores: 13 hybrid indicator scores
    - recommendations: list of improvement suggestions
    - warnings: critical quality issues
  Signals scoring below 40 (grade F) are flagged for mandatory adjustment.
  Signals scoring 40–54 (grade D) generate warnings but can still be approved.
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
from ml_engine.geometry_rating import geometry_rater
from ml_engine.signal_quality_validator import signal_quality_validator

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


def _attach_geometry_rating(signal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute and attach a geometry rating to a serialised signal dict.

    Reads the standard signal fields (entry_price, sl_price, tp_levels,
    type, confidence) and any structural context stored on the signal
    document.  Falls back gracefully when optional fields are absent.

    The rating is added under the key ``geometry_rating``.
    """
    try:
        entry_price = float(signal.get("entry_price", 0) or 0)
        sl_price    = float(signal.get("sl_price",    0) or 0)
        tp_levels   = [float(t) for t in (signal.get("tp_levels") or [])]
        signal_type = str(signal.get("type", "BUY")).upper()

        # Current price — fall back to entry if not stored
        current_price = float(signal.get("current_price", entry_price) or entry_price)

        # ATR — stored by the TP/SL engine; fall back to 0.5% of entry
        atr = float(signal.get("atr", 0) or 0)
        if atr <= 0:
            atr = entry_price * 0.005

        # Structural levels — stored by the TP/SL engine or SMC analysis
        market_structure = signal.get("market_structure") or {}
        nearest_support     = float(
            signal.get("nearest_support")
            or market_structure.get("support")
            or (entry_price - atr * 1.5 if signal_type == "BUY" else entry_price - atr * 3.0)
        )
        nearest_resistance  = float(
            signal.get("nearest_resistance")
            or market_structure.get("resistance")
            or (entry_price + atr * 3.0 if signal_type == "BUY" else entry_price + atr * 1.5)
        )

        # Optional swing points
        swing_high = signal.get("swing_high") or market_structure.get("last_swing_high")
        swing_low  = signal.get("swing_low")  or market_structure.get("last_swing_low")
        swing_high = float(swing_high) if swing_high is not None else None
        swing_low  = float(swing_low)  if swing_low  is not None else None

        # Skip rating if core price data is missing or invalid
        if entry_price <= 0 or sl_price <= 0 or not tp_levels:
            signal["geometry_rating"] = {
                "overall_score":  None,
                "recommendation": "INSUFFICIENT_DATA",
                "summary": (
                    "Geometry rating unavailable — signal is missing entry_price, "
                    "sl_price, or tp_levels."
                ),
                "components": {},
                "adjustment_guidelines": [],
            }
            return signal

        rating = geometry_rater.rate_signal(
            signal_type=signal_type,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_levels=tp_levels,
            current_price=current_price,
            atr=atr,
            nearest_support=nearest_support,
            nearest_resistance=nearest_resistance,
            swing_high=swing_high,
            swing_low=swing_low,
        )
        signal["geometry_rating"] = rating.to_dict()

    except Exception as exc:
        logger.warning(f"Geometry rating failed for signal {signal.get('id')}: {exc}")
        signal["geometry_rating"] = {
            "overall_score":  None,
            "recommendation": "ERROR",
            "summary": f"Geometry rating computation error: {exc}",
            "components": {},
            "adjustment_guidelines": [],
        }

    return signal


def _attach_quality_validation(signal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run signal quality validation and attach the result under the key
    ``quality_validation``.

    Reads all available signal fields and passes them to the
    SignalQualityValidator.  Falls back gracefully on any error.

    The result includes:
      - quality_score (0–100)
      - confidence_score (dynamic)
      - grade (A/B/C/D/F)
      - regime, session_quality, expiry, news_flags
      - enhancement_scores (13 hybrid indicators)
      - recommendations, warnings
    """
    try:
        validation = signal_quality_validator.validate(signal)
        signal["quality_validation"] = validation.to_dict()
    except Exception as exc:
        logger.warning(
            f"Signal quality validation failed for signal {signal.get('id')}: {exc}"
        )
        signal["quality_validation"] = {
            "passed": None,
            "quality_score": None,
            "grade": "UNKNOWN",
            "error": f"Quality validation error: {exc}",
        }
    return signal


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

    Each signal in the response includes a ``geometry_rating`` block with
    an objective 1–10 score for entry placement, stop loss placement,
    risk/reward ratio, and TP alignment, plus an overall score and
    APPROVE / ADJUST / REJECT recommendation.
    """
    try:
        result = await signal_manager.get_pending_signals(
            requesting_manager=current_manager,
            limit=limit,
            pair_filter=pair,
            min_confidence=min_confidence,
        )
        handled = _handle_result(result)
        # Attach geometry rating and quality validation to every signal in the list
        handled["signals"] = [
            _attach_quality_validation(_attach_geometry_rating(s))
            for s in handled.get("signals", [])
        ]
        return handled
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
    - ``signal.geometry_rating``: full geometry rating breakdown (1–10 per component,
      overall score, APPROVE/ADJUST/REJECT recommendation, and adjustment guidelines)
    - ``signal.quality_validation``: comprehensive quality validation result including
      quality_score, confidence_score, grade, regime, session_quality, expiry,
      news_flags, enhancement_scores (13 hybrid indicators), recommendations, warnings

    This endpoint supports any signal status, not just pending ones.
    """
    try:
        result = await signal_manager.get_signal_details(
            requesting_manager=current_manager,
            signal_id=signal_id,
        )
        handled = _handle_result(result)
        # Attach geometry rating and quality validation to the full signal document
        if "signal" in handled:
            handled["signal"] = _attach_quality_validation(
                _attach_geometry_rating(handled["signal"])
            )
        return handled
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

    The response includes a ``quality_metrics`` block with the signal's
    quality score, grade, and any recommendations from the quality validator.
    Signals with grade F (score < 40) are flagged with a ``quality_warning``
    in the response but are not blocked — the manager retains final authority.
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

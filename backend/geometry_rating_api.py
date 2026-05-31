"""
Trade Geometry Rating API — FastAPI router for signal geometry scoring
Gold Trading System v3.0.2

Mounts at: /api/manager/geometry
All endpoints require a valid manager JWT (ADMIN or MANAGER role).

Endpoints:
  POST /api/manager/geometry/rate          — Rate a single signal's geometry
  POST /api/manager/geometry/rate-batch    — Rate multiple signals at once
  GET  /api/manager/geometry/signal/{id}   — Rate a stored signal by its DB ID
  GET  /api/manager/geometry/thresholds    — Return scoring thresholds & weights
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import jwt
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, field_validator

from ml_engine.trade_geometry_rater import (
    APPROVE_THRESHOLD,
    ADJUST_THRESHOLD,
    WEIGHT_ENTRY,
    WEIGHT_SL,
    WEIGHT_RR,
    WEIGHT_TP,
    trade_geometry_rater,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────

MONGO_URL     = os.environ.get("MONGO_URL",     "mongodb://localhost:27017")
DB_NAME       = os.environ.get("DB_NAME",       "gold_signals_v3")
JWT_SECRET    = os.environ.get("JWT_SECRET",    "your-secret-key")
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")

security = HTTPBearer()
router   = APIRouter(prefix="/api/manager/geometry", tags=["Trade Geometry Rating"])

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
# Auth dependency
# ─────────────────────────────────────────────────────────────

async def get_current_manager(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Dict[str, Any]:
    """Decode the Bearer JWT and return the system_manager document."""
    try:
        token   = credentials.credentials
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

        if payload.get("type") != "manager":
            raise HTTPException(status_code=401, detail="Token is not a manager token")

        manager_id = payload.get("sub")
        if not manager_id:
            raise HTTPException(status_code=401, detail="Invalid token payload")

        db      = _get_db()
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
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def _require_review_role(manager: Dict[str, Any]) -> None:
    """Raise HTTP 403 if the manager does not have ADMIN or MANAGER role."""
    if manager.get("role") not in ("ADMIN", "MANAGER"):
        raise HTTPException(
            status_code=403,
            detail="Geometry rating requires ADMIN or MANAGER role",
        )


# ─────────────────────────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────────────────────────

class MarketContextModel(BaseModel):
    recent_high: Optional[float] = Field(default=None, gt=0, description="Recent swing high")
    recent_low:  Optional[float] = Field(default=None, gt=0, description="Recent swing low")
    atr:         Optional[float] = Field(default=None, gt=0, description="Average True Range")


class RateSignalRequest(BaseModel):
    signal_type: str   = Field(..., description="BUY or SELL")
    entry_price: float = Field(..., gt=0, description="Entry price")
    sl_price:    float = Field(..., gt=0, description="Stop-loss price")
    tp_levels:   List[float] = Field(
        ..., min_length=1, max_length=5,
        description="Take-profit levels (1–5 values, all > 0)",
    )
    pair:        Optional[str]  = Field(default=None, description="Trading pair (e.g. XAUUSD)")
    market_context: Optional[MarketContextModel] = None

    @field_validator("signal_type")
    @classmethod
    def validate_signal_type(cls, v: str) -> str:
        v = v.upper()
        if v not in ("BUY", "SELL"):
            raise ValueError("signal_type must be BUY or SELL")
        return v

    @field_validator("tp_levels")
    @classmethod
    def validate_tp_levels(cls, v: List[float]) -> List[float]:
        for i, tp in enumerate(v):
            if tp <= 0:
                raise ValueError(f"tp_levels[{i}] must be > 0")
        return v


class BatchRateRequest(BaseModel):
    signals: List[RateSignalRequest] = Field(
        ..., min_length=1, max_length=50,
        description="List of signals to rate (max 50)",
    )
    market_context: Optional[MarketContextModel] = None


# ─────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────

@router.post(
    "/rate",
    summary="Rate a single signal's trade geometry",
    response_description="4-component geometry rating with overall score and recommendation",
)
async def rate_signal_geometry(
    body:            RateSignalRequest,
    current_manager: Dict = Depends(get_current_manager),
):
    """
    Compute a comprehensive geometry rating for a trading signal.

    Returns four component scores (1–10 each):
    - **Entry Price Rating** — how well-positioned the entry is
    - **Stop Loss Rating**   — how tight and logical the SL placement is
    - **Risk/Reward Rating** — quality of the R:R ratio
    - **Take Profit Rating** — how realistic and well-spaced the TP levels are

    An **Overall Geometry Score** (1–10) is computed as a weighted average.
    The automatic **recommendation** is:
    - ``APPROVE``  — score ≥ 7.0
    - ``ADJUST``   — score ≥ 5.0
    - ``REJECT``   — score < 5.0
    """
    _require_review_role(current_manager)

    signal_dict = {
        "type":        body.signal_type,
        "entry_price": body.entry_price,
        "sl_price":    body.sl_price,
        "tp_levels":   body.tp_levels,
        "pair":        body.pair or "UNKNOWN",
    }
    ctx = body.market_context.model_dump() if body.market_context else {}

    rating = trade_geometry_rater.rate(signal_dict, ctx)
    return {"success": True, "rating": rating}


@router.post(
    "/rate-batch",
    summary="Rate multiple signals at once (max 50)",
    response_description="List of geometry ratings in the same order as the input",
)
async def rate_signals_batch(
    body:            BatchRateRequest,
    current_manager: Dict = Depends(get_current_manager),
):
    """
    Rate up to 50 signals in a single request.

    A shared ``market_context`` (recent_high, recent_low, ATR) can be
    supplied at the top level and will be applied to all signals.
    Individual signals may also carry their own ``market_context`` which
    takes precedence over the shared one.
    """
    _require_review_role(current_manager)

    shared_ctx = body.market_context.model_dump() if body.market_context else {}
    results    = []

    for req in body.signals:
        signal_dict = {
            "type":        req.signal_type,
            "entry_price": req.entry_price,
            "sl_price":    req.sl_price,
            "tp_levels":   req.tp_levels,
            "pair":        req.pair or "UNKNOWN",
        }
        # Per-signal context overrides shared context
        ctx = {**shared_ctx}
        if req.market_context:
            ctx.update({k: v for k, v in req.market_context.model_dump().items() if v is not None})

        results.append(trade_geometry_rater.rate(signal_dict, ctx))

    approve_count = sum(1 for r in results if r.get("recommendation") == "APPROVE")
    adjust_count  = sum(1 for r in results if r.get("recommendation") == "ADJUST")
    reject_count  = sum(1 for r in results if r.get("recommendation") == "REJECT")
    avg_score     = round(
        sum(r.get("overall_score", 0) for r in results) / len(results), 2
    ) if results else 0.0

    return {
        "success": True,
        "count":   len(results),
        "summary": {
            "approve": approve_count,
            "adjust":  adjust_count,
            "reject":  reject_count,
            "avg_score": avg_score,
        },
        "ratings": results,
    }


@router.get(
    "/signal/{signal_id}",
    summary="Rate a stored signal by its MongoDB ObjectId",
    response_description="Geometry rating for the stored signal",
)
async def rate_stored_signal(
    signal_id:       str,
    current_manager: Dict = Depends(get_current_manager),
):
    """
    Look up a signal from the database by its ObjectId and compute its
    geometry rating.  Useful for reviewing signals that are already in
    the ``PENDING_REVIEW`` queue.
    """
    _require_review_role(current_manager)

    if not ObjectId.is_valid(signal_id):
        raise HTTPException(status_code=400, detail=f"Invalid signal ID: '{signal_id}'")

    db     = _get_db()
    signal = await db.signals.find_one({"_id": ObjectId(signal_id)})
    if not signal:
        raise HTTPException(status_code=404, detail=f"Signal '{signal_id}' not found")

    # Normalise field names (DB uses 'type', legacy may use 'signal')
    signal_dict = {
        "type":        signal.get("type", signal.get("signal", "BUY")),
        "entry_price": signal.get("entry_price", 0),
        "sl_price":    signal.get("sl_price", 0),
        "tp_levels":   signal.get("tp_levels", []),
        "pair":        signal.get("pair", "UNKNOWN"),
    }

    rating = trade_geometry_rater.rate(signal_dict)
    return {
        "success":   True,
        "signal_id": signal_id,
        "pair":      signal.get("pair"),
        "type":      signal_dict["type"],
        "rating":    rating,
    }


@router.get(
    "/thresholds",
    summary="Return scoring thresholds and component weights",
    response_description="Geometry rating configuration",
)
async def get_geometry_thresholds(
    current_manager: Dict = Depends(get_current_manager),
):
    """
    Return the current scoring configuration: component weights,
    recommendation thresholds, and scale description.
    No role restriction — all authenticated managers may call this.
    """
    return {
        "success": True,
        "scale": {
            "min": 1.0,
            "max": 10.0,
            "labels": {
                "8.5–10.0": "EXCELLENT",
                "7.0–8.4":  "GOOD",
                "5.0–6.9":  "FAIR",
                "1.0–4.9":  "POOR",
            },
        },
        "weights": {
            "entry_price": WEIGHT_ENTRY,
            "stop_loss":   WEIGHT_SL,
            "risk_reward": WEIGHT_RR,
            "take_profit": WEIGHT_TP,
        },
        "recommendations": {
            "APPROVE": f"overall_score >= {APPROVE_THRESHOLD}",
            "ADJUST":  f"overall_score >= {ADJUST_THRESHOLD}",
            "REJECT":  f"overall_score < {ADJUST_THRESHOLD}",
        },
    }

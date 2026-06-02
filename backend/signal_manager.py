"""
Signal Manager — Manager Review & Approval Workflow
Gold Trading System v3.0.2

Provides the SignalManager class which gives system managers full control
over the signal lifecycle:
  - Review pending signals before they go live
  - Approve high-quality signals (sends to trading)
  - Reject low-quality signals with a mandatory reason
  - Adjust entry price, TP levels, and SL price before approval
  - Full audit trail of every decision
  - Approval statistics per manager

Collection layout (MongoDB):
  signals                — existing signal documents (status field extended)
  signal_review_log      — immutable audit record for every review action
  signal_adjustments     — history of price-level adjustments
  signal_quality_metrics — quality validation snapshots per signal

Signal status lifecycle:
  PENDING_REVIEW  → APPROVED  (sent to trading, status becomes ACTIVE)
  PENDING_REVIEW  → REJECTED  (removed from trading queue)
  PENDING_REVIEW  → ADJUSTED  → APPROVED / REJECTED

Quality Validation (v3.0.2):
  Every approve_signal() call now runs SignalQualityValidator against the
  signal document.  The quality result is:
    - Stored in signal_quality_metrics collection
    - Embedded in the signal document under quality_metrics
    - Included in the approval audit log
    - Returned in the approve_signal() response
  Signals with grade F (score < 40) generate a quality_warning in the
  response but are not blocked — managers retain final authority.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

# Lazy import to avoid circular dependencies at module load time
_signal_quality_validator = None


def _get_quality_validator():
    """Lazy-load the signal quality validator singleton."""
    global _signal_quality_validator
    if _signal_quality_validator is None:
        try:
            from ml_engine.signal_quality_validator import signal_quality_validator
            _signal_quality_validator = signal_quality_validator
        except Exception as exc:
            logger.warning(f"Could not load SignalQualityValidator: {exc}")
    return _signal_quality_validator

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME   = os.environ.get("DB_NAME",   "gold_signals_v3")

# Roles that are allowed to perform signal review actions
SIGNAL_REVIEW_ROLES = {"ADMIN", "MANAGER"}

# Maximum number of TP levels a signal may carry
MAX_TP_LEVELS = 5

# Minimum price value accepted for any price field
MIN_PRICE = 0.0001


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _oid(value: str) -> ObjectId:
    """Convert a string to ObjectId, raising ValueError on bad input."""
    if not ObjectId.is_valid(value):
        raise ValueError(f"Invalid signal ID: '{value}'")
    return ObjectId(value)


def _serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a MongoDB document to a JSON-serialisable dict.
    Replaces ObjectId with str and datetime with ISO-8601 string.
    """
    out: Dict[str, Any] = {}
    for k, v in doc.items():
        if k == "_id":
            out["id"] = str(v)
        elif isinstance(v, ObjectId):
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, list):
            out[k] = [_serialize(i) if isinstance(i, dict) else i for i in v]
        elif isinstance(v, dict):
            out[k] = _serialize(v)
        else:
            out[k] = v
    return out


def _check_review_permission(manager: Dict[str, Any]) -> None:
    """Raise PermissionError if the manager cannot perform signal reviews."""
    role = manager.get("role", "")
    if role not in SIGNAL_REVIEW_ROLES:
        raise PermissionError(
            f"Role '{role}' does not have permission to review signals. "
            f"Required: {SIGNAL_REVIEW_ROLES}"
        )


def _validate_price_levels(
    signal_type: str,
    entry_price: float,
    tp_levels: List[float],
    sl_price: float,
) -> Tuple[bool, str]:
    """
    Validate that entry / TP / SL form a structurally sound trade.

    BUY  : sl < entry < tp1 ≤ tp2 ≤ tp3 …
    SELL : sl > entry > tp1 ≥ tp2 ≥ tp3 …

    Returns (True, "") on success or (False, reason) on failure.
    """
    if entry_price <= MIN_PRICE:
        return False, f"entry_price must be > {MIN_PRICE}"
    if sl_price <= MIN_PRICE:
        return False, f"sl_price must be > {MIN_PRICE}"
    if not tp_levels:
        return False, "tp_levels must contain at least one value"
    if len(tp_levels) > MAX_TP_LEVELS:
        return False, f"tp_levels may contain at most {MAX_TP_LEVELS} values"
    for i, tp in enumerate(tp_levels):
        if tp <= MIN_PRICE:
            return False, f"tp_levels[{i}] must be > {MIN_PRICE}"

    direction = signal_type.upper()
    if direction == "BUY":
        if not (sl_price < entry_price):
            return False, f"BUY: sl_price ({sl_price}) must be < entry_price ({entry_price})"
        if not (entry_price < tp_levels[0]):
            return False, (
                f"BUY: entry_price ({entry_price}) must be < tp_levels[0] ({tp_levels[0]})"
            )
        for i in range(1, len(tp_levels)):
            if tp_levels[i] < tp_levels[i - 1]:
                return False, (
                    f"BUY: tp_levels must be non-decreasing "
                    f"(tp_levels[{i - 1}]={tp_levels[i - 1]} > tp_levels[{i}]={tp_levels[i]})"
                )
    elif direction == "SELL":
        if not (sl_price > entry_price):
            return False, f"SELL: sl_price ({sl_price}) must be > entry_price ({entry_price})"
        if not (entry_price > tp_levels[0]):
            return False, (
                f"SELL: entry_price ({entry_price}) must be > tp_levels[0] ({tp_levels[0]})"
            )
        for i in range(1, len(tp_levels)):
            if tp_levels[i] > tp_levels[i - 1]:
                return False, (
                    f"SELL: tp_levels must be non-increasing "
                    f"(tp_levels[{i - 1}]={tp_levels[i - 1]} < tp_levels[{i}]={tp_levels[i]})"
                )
    else:
        return False, f"signal_type must be BUY or SELL, got '{signal_type}'"

    return True, ""


# ─────────────────────────────────────────────────────────────
# SIGNAL MANAGER
# ─────────────────────────────────────────────────────────────

class SignalManager:
    """
    Central controller for the signal review and approval workflow.

    All mutating operations require a *requesting_manager* dict that
    carries at minimum ``{"manager_id": str, "role": str}``.
    Every action is recorded in the signal_review_log collection.
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
        manager_id: str,
        role: str,
        signal_id: str,
        details: Dict[str, Any],
        success: bool = True,
        error: Optional[str] = None,
    ) -> None:
        """Persist an immutable review audit record."""
        try:
            db = self._get_db()
            entry = {
                "log_id":      str(uuid.uuid4()),
                "timestamp":   datetime.utcnow(),
                "action":      action,
                "manager_id":  manager_id,
                "role":        role,
                "signal_id":   signal_id,
                "details":     details,
                "success":     success,
                "error":       error,
            }
            await db.signal_review_log.insert_one(entry)
        except Exception as exc:
            logger.error(f"Signal review audit log write failed: {exc}")

    # ═══════════════════════════════════════════════════════════
    # GET PENDING SIGNALS
    # ═══════════════════════════════════════════════════════════

    async def get_pending_signals(
        self,
        requesting_manager: Dict[str, Any],
        limit: int = 50,
        pair_filter: Optional[str] = None,
        min_confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Return signals that are awaiting manager review.

        Signals enter the pending queue when they are generated with
        status ``PENDING_REVIEW``.  Managers with ADMIN or MANAGER role
        may call this endpoint.

        Args:
            requesting_manager: Authenticated manager dict.
            limit:              Maximum number of signals to return (1–200).
            pair_filter:        Optional trading pair filter (e.g. "XAUUSD").
            min_confidence:     Optional minimum confidence threshold (0–100).

        Returns:
            {"success": True, "signals": [...], "total": int}
        """
        _check_review_permission(requesting_manager)
        db = self._get_db()

        query: Dict[str, Any] = {"status": "PENDING_REVIEW"}
        if pair_filter:
            query["pair"] = pair_filter.upper()
        if min_confidence is not None:
            query["confidence"] = {"$gte": min_confidence}

        limit = max(1, min(limit, 200))
        cursor = db.signals.find(query).sort("created_at", -1).limit(limit)
        raw = await cursor.to_list(length=limit)
        total = await db.signals.count_documents(query)

        signals = [_serialize(s) for s in raw]

        logger.info(
            f"📋 Manager {requesting_manager['manager_id']} listed "
            f"{len(signals)} pending signals"
        )
        return {"success": True, "signals": signals, "total": total}

    # ═══════════════════════════════════════════════════════════
    # GET SIGNAL DETAILS
    # ═══════════════════════════════════════════════════════════

    async def get_signal_details(
        self,
        requesting_manager: Dict[str, Any],
        signal_id: str,
    ) -> Dict[str, Any]:
        """
        Return full details for a single signal, including any adjustment
        history and the review log entries for that signal.

        Args:
            requesting_manager: Authenticated manager dict.
            signal_id:          MongoDB ObjectId string of the signal.

        Returns:
            {"success": True, "signal": {...}, "adjustments": [...], "review_log": [...]}
        """
        _check_review_permission(requesting_manager)
        db = self._get_db()

        try:
            oid = _oid(signal_id)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        signal = await db.signals.find_one({"_id": oid})
        if not signal:
            return {"success": False, "error": f"Signal '{signal_id}' not found"}

        # Fetch adjustment history
        adj_cursor = db.signal_adjustments.find(
            {"signal_id": signal_id}
        ).sort("adjusted_at", 1)
        adjustments = [_serialize(a) async for a in adj_cursor]

        # Fetch review log entries for this signal
        log_cursor = db.signal_review_log.find(
            {"signal_id": signal_id}
        ).sort("timestamp", 1)
        review_log = [_serialize(e) async for e in log_cursor]

        return {
            "success":    True,
            "signal":     _serialize(signal),
            "adjustments": adjustments,
            "review_log": review_log,
        }

    # ═══════════════════════════════════════════════════════════
    # APPROVE SIGNAL
    # ═══════════════════════════════════════════════════════════

    async def approve_signal(
        self,
        requesting_manager: Dict[str, Any],
        signal_id: str,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Approve a pending signal and promote it to ACTIVE status so it
        enters the live trading queue.

        Only signals with status ``PENDING_REVIEW`` or ``ADJUSTED`` may
        be approved.

        Args:
            requesting_manager: Authenticated manager dict.
            signal_id:          MongoDB ObjectId string of the signal.
            notes:              Optional manager notes recorded in the audit log.

        Returns:
            {"success": True, "signal_id": str, "new_status": "ACTIVE", ...}
        """
        _check_review_permission(requesting_manager)
        db = self._get_db()

        try:
            oid = _oid(signal_id)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        signal = await db.signals.find_one({"_id": oid})
        if not signal:
            return {"success": False, "error": f"Signal '{signal_id}' not found"}

        current_status = signal.get("status", "")
        if current_status not in ("PENDING_REVIEW", "ADJUSTED"):
            return {
                "success": False,
                "error": (
                    f"Signal cannot be approved from status '{current_status}'. "
                    f"Expected PENDING_REVIEW or ADJUSTED."
                ),
            }

        # ── Run signal quality validation ─────────────────────
        quality_metrics: Dict[str, Any] = {}
        quality_warning: Optional[str]  = None
        try:
            validator = _get_quality_validator()
            if validator is not None:
                serialised_signal = _serialize(signal)
                validation = validator.validate(serialised_signal)
                quality_metrics = validation.to_dict()

                # Persist quality metrics to dedicated collection
                await db.signal_quality_metrics.insert_one({
                    "signal_id":      signal_id,
                    "recorded_at":    datetime.utcnow(),
                    "action":         "approve",
                    "manager_id":     requesting_manager["manager_id"],
                    **quality_metrics,
                })

                # Flag low-quality signals (grade F) with a warning
                if validation.grade == "F":
                    quality_warning = (
                        f"⚠️  Signal quality grade F "
                        f"(score={validation.quality_score:.1f}/100). "
                        f"Recommendations: {'; '.join(validation.recommendations[:3])}"
                    )
                    logger.warning(
                        f"Low-quality signal {signal_id} approved by "
                        f"{requesting_manager['manager_id']}: "
                        f"score={validation.quality_score:.1f} grade=F"
                    )
        except Exception as exc:
            logger.warning(f"Quality validation during approval failed: {exc}")

        now = datetime.utcnow()
        update: Dict[str, Any] = {
            "status":          "ACTIVE",
            "approved_by":     requesting_manager["manager_id"],
            "approved_at":     now,
            "manager_notes":   notes or "",
            "review_status":   "APPROVED",
        }
        # Embed quality metrics snapshot in the signal document
        if quality_metrics:
            update["quality_metrics"] = {
                "quality_score":    quality_metrics.get("quality_score"),
                "confidence_score": quality_metrics.get("confidence_score"),
                "grade":            quality_metrics.get("grade"),
                "regime":           quality_metrics.get("regime"),
                "session_quality":  quality_metrics.get("session_quality"),
                "expiry":           quality_metrics.get("expiry"),
                "news_flags":       quality_metrics.get("news_flags", []),
                "recommendations":  quality_metrics.get("recommendations", []),
                "recorded_at":      now.isoformat(),
            }

        await db.signals.update_one({"_id": oid}, {"$set": update})

        await self._audit(
            action="signal:approve",
            manager_id=requesting_manager["manager_id"],
            role=requesting_manager["role"],
            signal_id=signal_id,
            details={
                "pair":             signal.get("pair"),
                "type":             signal.get("type"),
                "entry_price":      signal.get("entry_price"),
                "confidence":       signal.get("confidence"),
                "previous_status":  current_status,
                "notes":            notes,
                "quality_score":    quality_metrics.get("quality_score"),
                "quality_grade":    quality_metrics.get("grade"),
                "quality_warning":  quality_warning,
            },
        )

        logger.info(
            f"✅ Signal {signal_id} APPROVED by manager "
            f"{requesting_manager['manager_id']} "
            f"({signal.get('pair')} {signal.get('type')}) "
            f"quality={quality_metrics.get('grade', 'N/A')}"
        )

        response: Dict[str, Any] = {
            "success":    True,
            "signal_id":  signal_id,
            "new_status": "ACTIVE",
            "approved_by": requesting_manager["manager_id"],
            "approved_at": now.isoformat(),
            "pair":        signal.get("pair"),
            "type":        signal.get("type"),
        }
        if quality_metrics:
            response["quality_metrics"] = {
                "quality_score":    quality_metrics.get("quality_score"),
                "confidence_score": quality_metrics.get("confidence_score"),
                "grade":            quality_metrics.get("grade"),
                "regime":           quality_metrics.get("regime"),
                "session_quality":  quality_metrics.get("session_quality"),
                "expiry":           quality_metrics.get("expiry"),
                "news_flags":       quality_metrics.get("news_flags", []),
                "recommendations":  quality_metrics.get("recommendations", []),
            }
        if quality_warning:
            response["quality_warning"] = quality_warning

        return response

    # ═══════════════════════════════════════════════════════════
    # REJECT SIGNAL
    # ═══════════════════════════════════════════════════════════

    async def reject_signal(
        self,
        requesting_manager: Dict[str, Any],
        signal_id: str,
        reason: str,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Reject a pending signal with a mandatory reason.  The signal is
        marked ``REJECTED`` and will not enter the trading queue.

        Args:
            requesting_manager: Authenticated manager dict.
            signal_id:          MongoDB ObjectId string of the signal.
            reason:             Mandatory rejection reason (non-empty string).
            notes:              Optional additional manager notes.

        Returns:
            {"success": True, "signal_id": str, "new_status": "REJECTED", ...}
        """
        _check_review_permission(requesting_manager)

        if not reason or not reason.strip():
            return {"success": False, "error": "A rejection reason is mandatory"}

        db = self._get_db()

        try:
            oid = _oid(signal_id)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        signal = await db.signals.find_one({"_id": oid})
        if not signal:
            return {"success": False, "error": f"Signal '{signal_id}' not found"}

        current_status = signal.get("status", "")
        if current_status not in ("PENDING_REVIEW", "ADJUSTED"):
            return {
                "success": False,
                "error": (
                    f"Signal cannot be rejected from status '{current_status}'. "
                    f"Expected PENDING_REVIEW or ADJUSTED."
                ),
            }

        now = datetime.utcnow()
        update = {
            "status":           "REJECTED",
            "rejected_by":      requesting_manager["manager_id"],
            "rejected_at":      now,
            "rejection_reason": reason.strip(),
            "manager_notes":    notes or "",
            "review_status":    "REJECTED",
        }
        await db.signals.update_one({"_id": oid}, {"$set": update})

        await self._audit(
            action="signal:reject",
            manager_id=requesting_manager["manager_id"],
            role=requesting_manager["role"],
            signal_id=signal_id,
            details={
                "pair":            signal.get("pair"),
                "type":            signal.get("type"),
                "entry_price":     signal.get("entry_price"),
                "confidence":      signal.get("confidence"),
                "previous_status": current_status,
                "reason":          reason.strip(),
                "notes":           notes,
            },
        )

        logger.info(
            f"❌ Signal {signal_id} REJECTED by manager "
            f"{requesting_manager['manager_id']} — {reason.strip()}"
        )
        return {
            "success":         True,
            "signal_id":       signal_id,
            "new_status":      "REJECTED",
            "rejected_by":     requesting_manager["manager_id"],
            "rejected_at":     now.isoformat(),
            "rejection_reason": reason.strip(),
            "pair":            signal.get("pair"),
            "type":            signal.get("type"),
        }

    # ═══════════════════════════════════════════════════════════
    # ADJUST SIGNAL
    # ═══════════════════════════════════════════════════════════

    async def adjust_signal(
        self,
        requesting_manager: Dict[str, Any],
        signal_id: str,
        entry_price: Optional[float] = None,
        tp_levels: Optional[List[float]] = None,
        sl_price: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Adjust the price levels of a pending signal before approval.

        At least one of entry_price, tp_levels, or sl_price must be
        provided.  The resulting price structure is validated for
        directional consistency before the update is persisted.

        The signal status is changed to ``ADJUSTED`` to indicate it has
        been modified by a manager.  The original values are preserved in
        the signal_adjustments collection for full auditability.

        Args:
            requesting_manager: Authenticated manager dict.
            signal_id:          MongoDB ObjectId string of the signal.
            entry_price:        New entry price (optional).
            tp_levels:          New list of TP levels (optional).
            sl_price:           New SL price (optional).
            notes:              Mandatory adjustment rationale.

        Returns:
            {"success": True, "signal_id": str, "new_status": "ADJUSTED", ...}
        """
        _check_review_permission(requesting_manager)

        if entry_price is None and tp_levels is None and sl_price is None:
            return {
                "success": False,
                "error": "At least one of entry_price, tp_levels, or sl_price must be provided",
            }

        db = self._get_db()

        try:
            oid = _oid(signal_id)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        signal = await db.signals.find_one({"_id": oid})
        if not signal:
            return {"success": False, "error": f"Signal '{signal_id}' not found"}

        current_status = signal.get("status", "")
        if current_status not in ("PENDING_REVIEW", "ADJUSTED"):
            return {
                "success": False,
                "error": (
                    f"Signal cannot be adjusted from status '{current_status}'. "
                    f"Expected PENDING_REVIEW or ADJUSTED."
                ),
            }

        # Resolve final values (use existing if not overridden)
        new_entry = entry_price if entry_price is not None else signal["entry_price"]
        new_tps   = tp_levels   if tp_levels   is not None else signal["tp_levels"]
        new_sl    = sl_price    if sl_price    is not None else signal["sl_price"]
        sig_type  = signal.get("type", "BUY")

        # Validate the resulting price structure
        valid, validation_error = _validate_price_levels(sig_type, new_entry, new_tps, new_sl)
        if not valid:
            await self._audit(
                action="signal:adjust",
                manager_id=requesting_manager["manager_id"],
                role=requesting_manager["role"],
                signal_id=signal_id,
                details={"attempted_entry": entry_price, "attempted_tps": tp_levels,
                         "attempted_sl": sl_price},
                success=False,
                error=validation_error,
            )
            return {"success": False, "error": f"Price validation failed: {validation_error}"}

        now = datetime.utcnow()

        # Persist the original values before overwriting
        adjustment_record = {
            "adjustment_id":    str(uuid.uuid4()),
            "signal_id":        signal_id,
            "adjusted_by":      requesting_manager["manager_id"],
            "adjusted_at":      now,
            "original_entry":   signal["entry_price"],
            "original_tps":     signal["tp_levels"],
            "original_sl":      signal["sl_price"],
            "new_entry":        new_entry,
            "new_tps":          new_tps,
            "new_sl":           new_sl,
            "notes":            notes or "",
        }
        await db.signal_adjustments.insert_one(adjustment_record)

        # Apply the adjustments
        update: Dict[str, Any] = {
            "entry_price":    new_entry,
            "tp_levels":      new_tps,
            "sl_price":       new_sl,
            "status":         "ADJUSTED",
            "adjusted_by":    requesting_manager["manager_id"],
            "adjusted_at":    now,
            "manager_notes":  notes or "",
            "review_status":  "ADJUSTED",
        }
        await db.signals.update_one({"_id": oid}, {"$set": update})

        await self._audit(
            action="signal:adjust",
            manager_id=requesting_manager["manager_id"],
            role=requesting_manager["role"],
            signal_id=signal_id,
            details={
                "pair":           signal.get("pair"),
                "type":           sig_type,
                "original_entry": signal["entry_price"],
                "new_entry":      new_entry,
                "original_tps":   signal["tp_levels"],
                "new_tps":        new_tps,
                "original_sl":    signal["sl_price"],
                "new_sl":         new_sl,
                "notes":          notes,
            },
        )

        logger.info(
            f"✏️  Signal {signal_id} ADJUSTED by manager "
            f"{requesting_manager['manager_id']} "
            f"({signal.get('pair')} {sig_type}): "
            f"entry {signal['entry_price']} → {new_entry}, "
            f"sl {signal['sl_price']} → {new_sl}"
        )
        return {
            "success":    True,
            "signal_id":  signal_id,
            "new_status": "ADJUSTED",
            "adjusted_by": requesting_manager["manager_id"],
            "adjusted_at": now.isoformat(),
            "pair":        signal.get("pair"),
            "type":        sig_type,
            "entry_price": new_entry,
            "tp_levels":   new_tps,
            "sl_price":    new_sl,
        }

    # ═══════════════════════════════════════════════════════════
    # GET SIGNAL HISTORY
    # ═══════════════════════════════════════════════════════════

    async def get_signal_history(
        self,
        requesting_manager: Dict[str, Any],
        limit: int = 100,
        hours: int = 168,
        status_filter: Optional[str] = None,
        pair_filter: Optional[str] = None,
        manager_id_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Return the review history for signals that have been acted upon
        (approved, rejected, or adjusted).

        Args:
            requesting_manager: Authenticated manager dict.
            limit:              Maximum number of records (1–500).
            hours:              Look-back window in hours (default 7 days).
            status_filter:      Filter by review status (APPROVED/REJECTED/ADJUSTED).
            pair_filter:        Filter by trading pair.
            manager_id_filter:  Filter by the manager who acted on the signal.

        Returns:
            {"success": True, "history": [...], "total": int, "stats": {...}}
        """
        _check_review_permission(requesting_manager)
        db = self._get_db()

        since = datetime.utcnow() - timedelta(hours=max(1, min(hours, 8760)))
        query: Dict[str, Any] = {
            "review_status": {"$exists": True},
            "created_at":    {"$gte": since},
        }
        if status_filter:
            query["review_status"] = status_filter.upper()
        if pair_filter:
            query["pair"] = pair_filter.upper()
        if manager_id_filter:
            query["$or"] = [
                {"approved_by": manager_id_filter},
                {"rejected_by": manager_id_filter},
                {"adjusted_by": manager_id_filter},
            ]

        limit = max(1, min(limit, 500))
        cursor = db.signals.find(query).sort("created_at", -1).limit(limit)
        raw    = await cursor.to_list(length=limit)
        total  = await db.signals.count_documents(query)

        history = [_serialize(s) for s in raw]

        # Quick summary stats
        approved = sum(1 for s in history if s.get("review_status") == "APPROVED")
        rejected = sum(1 for s in history if s.get("review_status") == "REJECTED")
        adjusted = sum(1 for s in history if s.get("review_status") == "ADJUSTED")

        return {
            "success": True,
            "history": history,
            "total":   total,
            "stats": {
                "approved": approved,
                "rejected": rejected,
                "adjusted": adjusted,
                "approval_rate": (
                    round(approved / (approved + rejected) * 100, 1)
                    if (approved + rejected) > 0 else 0.0
                ),
            },
        }

    # ═══════════════════════════════════════════════════════════
    # GET APPROVAL STATS
    # ═══════════════════════════════════════════════════════════

    async def get_approval_stats(
        self,
        requesting_manager: Dict[str, Any],
        days: int = 30,
        manager_id_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Return approval statistics aggregated per manager.

        Includes total approved, rejected, adjusted counts, approval rate,
        and a breakdown by trading pair.

        Args:
            requesting_manager: Authenticated manager dict.
            days:               Look-back window in days (1–365).
            manager_id_filter:  Restrict stats to a single manager.

        Returns:
            {"success": True, "stats": {...}, "per_manager": [...], "per_pair": [...]}
        """
        _check_review_permission(requesting_manager)
        db = self._get_db()

        days  = max(1, min(days, 365))
        since = datetime.utcnow() - timedelta(days=days)

        base_query: Dict[str, Any] = {
            "review_status": {"$exists": True},
            "created_at":    {"$gte": since},
        }

        # ── Overall totals ────────────────────────────────────
        total_approved = await db.signals.count_documents(
            {**base_query, "review_status": "APPROVED"}
        )
        total_rejected = await db.signals.count_documents(
            {**base_query, "review_status": "REJECTED"}
        )
        total_adjusted = await db.signals.count_documents(
            {**base_query, "review_status": "ADJUSTED"}
        )
        total_pending  = await db.signals.count_documents({"status": "PENDING_REVIEW"})

        reviewed = total_approved + total_rejected
        overall_approval_rate = (
            round(total_approved / reviewed * 100, 1) if reviewed > 0 else 0.0
        )

        # ── Per-manager breakdown (from audit log) ────────────
        pipeline_manager: List[Dict] = [
            {"$match": {
                "action":    {"$in": ["signal:approve", "signal:reject", "signal:adjust"]},
                "timestamp": {"$gte": since},
                **({"manager_id": manager_id_filter} if manager_id_filter else {}),
            }},
            {"$group": {
                "_id":      "$manager_id",
                "approved": {"$sum": {"$cond": [{"$eq": ["$action", "signal:approve"]}, 1, 0]}},
                "rejected": {"$sum": {"$cond": [{"$eq": ["$action", "signal:reject"]}, 1, 0]}},
                "adjusted": {"$sum": {"$cond": [{"$eq": ["$action", "signal:adjust"]}, 1, 0]}},
                "total":    {"$sum": 1},
            }},
            {"$sort": {"total": -1}},
        ]
        per_manager_raw = await db.signal_review_log.aggregate(pipeline_manager).to_list(50)
        per_manager = []
        for row in per_manager_raw:
            rev = row["approved"] + row["rejected"]
            per_manager.append({
                "manager_id":    row["_id"],
                "approved":      row["approved"],
                "rejected":      row["rejected"],
                "adjusted":      row["adjusted"],
                "total_actions": row["total"],
                "approval_rate": round(row["approved"] / rev * 100, 1) if rev > 0 else 0.0,
            })

        # ── Per-pair breakdown ────────────────────────────────
        pipeline_pair: List[Dict] = [
            {"$match": {**base_query, "review_status": {"$in": ["APPROVED", "REJECTED"]}}},
            {"$group": {
                "_id":      "$pair",
                "approved": {"$sum": {"$cond": [{"$eq": ["$review_status", "APPROVED"]}, 1, 0]}},
                "rejected": {"$sum": {"$cond": [{"$eq": ["$review_status", "REJECTED"]}, 1, 0]}},
            }},
            {"$sort": {"approved": -1}},
        ]
        per_pair_raw = await db.signals.aggregate(pipeline_pair).to_list(50)
        per_pair = []
        for row in per_pair_raw:
            rev = row["approved"] + row["rejected"]
            per_pair.append({
                "pair":          row["_id"],
                "approved":      row["approved"],
                "rejected":      row["rejected"],
                "approval_rate": round(row["approved"] / rev * 100, 1) if rev > 0 else 0.0,
            })

        return {
            "success": True,
            "period_days": days,
            "stats": {
                "total_pending":       total_pending,
                "total_approved":      total_approved,
                "total_rejected":      total_rejected,
                "total_adjusted":      total_adjusted,
                "overall_approval_rate": overall_approval_rate,
            },
            "per_manager": per_manager,
            "per_pair":    per_pair,
        }


# ─────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────

signal_manager = SignalManager()

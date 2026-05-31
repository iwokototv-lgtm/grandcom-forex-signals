"""
Signal Manager — Manager Approval Workflow
Grandcom Gold Signals System v3.0.2

Provides:
  - SignalStatus enum  (PENDING, APPROVED, REJECTED, ADJUSTED)
  - SignalManager class with full approval workflow:
      • get_pending_signals   — list signals awaiting review
      • get_signal_details    — full analysis breakdown for one signal
      • approve_signal        — send approved signal to trading
      • reject_signal         — reject with mandatory reason
      • adjust_signal         — modify entry, TP levels, SL before approval
      • get_signal_history    — paginated approval history
      • get_approval_stats    — per-manager and global statistics
  - Immutable audit trail for every signal action
  - Permission enforcement: ADMIN/MANAGER can approve; VIEWER read-only
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorClient

from ml_engine.system_manager import ManagerRole, check_permission

logger = logging.getLogger(__name__)

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME   = os.environ.get("DB_NAME",   "gold_signals_v3")


# ─────────────────────────────────────────────────────────────
# SIGNAL STATUS ENUM
# ─────────────────────────────────────────────────────────────

class SignalStatus(str, Enum):
    PENDING  = "PENDING"   # Auto-generated, awaiting manager review
    APPROVED = "APPROVED"  # Approved as-is, sent to trading
    REJECTED = "REJECTED"  # Rejected — will not be traded
    ADJUSTED = "ADJUSTED"  # Entry/TP/SL modified, then approved


# ─────────────────────────────────────────────────────────────
# PERMISSION KEYS (aligned with system_manager permission matrix)
# ─────────────────────────────────────────────────────────────

# These are checked via check_permission() from system_manager.
# The permission matrix in system_manager.py grants:
#   ADMIN   → signal:approve, signal:reject, signal:adjust, system:signals
#   MANAGER → signal:approve, signal:reject, signal:adjust, system:signals
#   VIEWER  → system:signals (read-only — no mutate permissions)

_SIGNAL_VIEW_PERM    = "system:signals"   # All roles
_SIGNAL_APPROVE_PERM = "signal:approve"   # ADMIN + MANAGER
_SIGNAL_REJECT_PERM  = "signal:reject"    # ADMIN + MANAGER
_SIGNAL_ADJUST_PERM  = "signal:adjust"    # ADMIN + MANAGER


# ─────────────────────────────────────────────────────────────
# VALIDATION HELPERS
# ─────────────────────────────────────────────────────────────

def _validate_price_levels(
    signal_type: str,
    entry: float,
    tp_levels: List[float],
    sl: float,
) -> Optional[str]:
    """
    Validate that entry/TP/SL geometry is correct for the signal direction.
    Returns an error string on failure, None on success.
    """
    if entry <= 0:
        return f"entry must be > 0, got {entry}"
    if sl <= 0:
        return f"sl must be > 0, got {sl}"
    if not tp_levels:
        return "tp_levels must contain at least one level"
    for i, tp in enumerate(tp_levels):
        if tp <= 0:
            return f"tp_levels[{i}] must be > 0, got {tp}"

    direction = signal_type.upper()
    if direction == "BUY":
        if sl >= entry:
            return f"BUY: sl ({sl}) must be < entry ({entry})"
        for i, tp in enumerate(tp_levels):
            if tp <= entry:
                return f"BUY: tp_levels[{i}] ({tp}) must be > entry ({entry})"
    elif direction == "SELL":
        if sl <= entry:
            return f"SELL: sl ({sl}) must be > entry ({entry})"
        for i, tp in enumerate(tp_levels):
            if tp >= entry:
                return f"SELL: tp_levels[{i}] ({tp}) must be < entry ({entry})"
    else:
        return f"Unknown signal type '{signal_type}' — expected BUY or SELL"

    return None


def _calculate_rr(entry: float, sl: float, tp: float) -> float:
    """Calculate risk/reward ratio from raw price levels."""
    risk   = abs(entry - sl)
    reward = abs(tp - entry)
    return round(reward / risk, 2) if risk > 0 else 0.0


# ─────────────────────────────────────────────────────────────
# SIGNAL MANAGER
# ─────────────────────────────────────────────────────────────

class SignalManager:
    """
    Approval workflow controller for auto-generated trading signals.

    All mutating operations require a *requesting_manager* dict that
    carries at minimum ``{"manager_id": str, "role": ManagerRole}``.
    Every action is recorded in the ``signal_approval_audit`` collection.
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
        signal_id: str,
        details: Dict[str, Any],
        success: bool = True,
        error: Optional[str] = None,
    ) -> None:
        """Persist an immutable signal-approval audit record."""
        try:
            db = self._get_db()
            entry = {
                "audit_id":     str(uuid.uuid4()),
                "timestamp":    datetime.utcnow(),
                "action":       action,
                "performed_by": performed_by,
                "role":         role,
                "signal_id":    signal_id,
                "details":      details,
                "success":      success,
                "error":        error,
            }
            await db.signal_approval_audit.insert_one(entry)
        except Exception as exc:
            logger.error(f"Signal audit log write failed: {exc}")

    # ── Serialisation helper ──────────────────────────────────

    @staticmethod
    def _fmt_signal(doc: Dict[str, Any]) -> Dict[str, Any]:
        """Normalise a signal document for API responses."""
        doc.pop("_id", None)
        for ts_field in ("created_at", "approved_at", "rejected_at",
                         "adjusted_at", "reviewed_at"):
            if doc.get(ts_field) and hasattr(doc[ts_field], "isoformat"):
                doc[ts_field] = doc[ts_field].isoformat()
        return doc

    # ═══════════════════════════════════════════════════════════
    # 1. GET PENDING SIGNALS
    # ═══════════════════════════════════════════════════════════

    async def get_pending_signals(
        self,
        requesting_manager: Dict[str, Any],
        pair: Optional[str] = None,
        limit: int = 50,
        min_confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Return signals with status PENDING, sorted newest-first.

        Requires: system:signals (all roles).

        Args:
            pair:           Filter by trading pair (e.g. "XAUUSD").
            limit:          Maximum results (1–200).
            min_confidence: Only return signals at or above this confidence %.
        """
        check_permission(requesting_manager, _SIGNAL_VIEW_PERM)
        db = self._get_db()

        query: Dict[str, Any] = {"approval_status": SignalStatus.PENDING}
        if pair:
            query["pair"] = pair.upper()
        if min_confidence is not None:
            query["confidence"] = {"$gte": min_confidence}

        raw = await (
            db.gold_signals
            .find(query)
            .sort("created_at", -1)
            .limit(max(1, min(limit, 200)))
            .to_list(None)
        )

        signals = [self._fmt_signal(s) for s in raw]

        await self._audit(
            "signal:list_pending",
            requesting_manager["manager_id"],
            requesting_manager["role"],
            signal_id="",
            details={"count": len(signals), "pair": pair, "min_confidence": min_confidence},
        )

        return {
            "success": True,
            "signals": signals,
            "count":   len(signals),
            "status":  SignalStatus.PENDING,
        }

    # ═══════════════════════════════════════════════════════════
    # 2. GET SIGNAL DETAILS
    # ═══════════════════════════════════════════════════════════

    async def get_signal_details(
        self,
        requesting_manager: Dict[str, Any],
        signal_id: str,
    ) -> Dict[str, Any]:
        """
        Return the full document for a single signal, including all
        analysis fields (indicators, regime, SMC score, MTF alignment,
        pivot zone) and the complete approval history.

        Requires: system:signals (all roles).
        """
        check_permission(requesting_manager, _SIGNAL_VIEW_PERM)
        db = self._get_db()

        # Try lookup by our signal_id field first, then MongoDB _id string
        from bson import ObjectId
        doc = await db.gold_signals.find_one({"signal_id": signal_id})
        if not doc:
            try:
                doc = await db.gold_signals.find_one({"_id": ObjectId(signal_id)})
            except Exception:
                pass

        if not doc:
            return {"success": False, "error": f"Signal '{signal_id}' not found"}

        # Fetch approval audit trail for this signal
        audit_entries = await (
            db.signal_approval_audit
            .find({"signal_id": signal_id})
            .sort("timestamp", 1)
            .to_list(None)
        )
        for e in audit_entries:
            e.pop("_id", None)
            if e.get("timestamp") and hasattr(e["timestamp"], "isoformat"):
                e["timestamp"] = e["timestamp"].isoformat()

        signal = self._fmt_signal(doc)
        signal["approval_history"] = audit_entries

        await self._audit(
            "signal:view_details",
            requesting_manager["manager_id"],
            requesting_manager["role"],
            signal_id=signal_id,
            details={"pair": signal.get("pair"), "status": signal.get("approval_status")},
        )

        return {"success": True, "signal": signal}

    # ═══════════════════════════════════════════════════════════
    # 3. APPROVE SIGNAL
    # ═══════════════════════════════════════════════════════════

    async def approve_signal(
        self,
        requesting_manager: Dict[str, Any],
        signal_id: str,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Approve a PENDING signal and mark it ready for trading.

        Requires: signal:approve (ADMIN, MANAGER).

        The signal's ``approval_status`` is set to APPROVED and
        ``status`` remains ACTIVE so the trading engine picks it up.
        """
        check_permission(requesting_manager, _SIGNAL_APPROVE_PERM)
        db = self._get_db()

        from bson import ObjectId
        doc = await db.gold_signals.find_one({"signal_id": signal_id})
        if not doc:
            try:
                doc = await db.gold_signals.find_one({"_id": ObjectId(signal_id)})
                if doc:
                    signal_id = str(doc["_id"])
            except Exception:
                pass

        if not doc:
            await self._audit(
                "signal:approve", requesting_manager["manager_id"],
                requesting_manager["role"], signal_id,
                {}, success=False, error="Signal not found",
            )
            return {"success": False, "error": f"Signal '{signal_id}' not found"}

        current_status = doc.get("approval_status", SignalStatus.PENDING)
        if current_status != SignalStatus.PENDING:
            return {
                "success": False,
                "error":   f"Signal is already '{current_status}' — only PENDING signals can be approved",
            }

        now = datetime.utcnow()
        update = {
            "approval_status":  SignalStatus.APPROVED,
            "approved_by":      requesting_manager["manager_id"],
            "approved_at":      now,
            "approval_notes":   notes or "",
            "reviewed_at":      now,
            "reviewed_by":      requesting_manager["manager_id"],
        }
        await db.gold_signals.update_one(
            {"_id": doc["_id"]},
            {"$set": update},
        )

        await self._audit(
            "signal:approve",
            requesting_manager["manager_id"],
            requesting_manager["role"],
            signal_id=signal_id,
            details={
                "pair":       doc.get("pair"),
                "type":       doc.get("type"),
                "entry":      doc.get("entry_price"),
                "confidence": doc.get("confidence"),
                "notes":      notes,
            },
        )

        logger.info(
            f"✅ Signal APPROVED: {signal_id} ({doc.get('pair')} {doc.get('type')}) "
            f"by {requesting_manager['manager_id']}"
        )
        return {
            "success":         True,
            "signal_id":       signal_id,
            "approval_status": SignalStatus.APPROVED,
            "approved_by":     requesting_manager["manager_id"],
            "approved_at":     now.isoformat(),
            "message":         "Signal approved and queued for trading",
        }

    # ═══════════════════════════════════════════════════════════
    # 4. REJECT SIGNAL
    # ═══════════════════════════════════════════════════════════

    async def reject_signal(
        self,
        requesting_manager: Dict[str, Any],
        signal_id: str,
        reason: str,
    ) -> Dict[str, Any]:
        """
        Reject a PENDING signal with a mandatory reason.

        Requires: signal:reject (ADMIN, MANAGER).

        The signal's ``approval_status`` is set to REJECTED and
        ``status`` is set to CANCELLED so it is excluded from trading.
        """
        check_permission(requesting_manager, _SIGNAL_REJECT_PERM)

        if not reason or not reason.strip():
            return {"success": False, "error": "A rejection reason is required"}

        db = self._get_db()

        from bson import ObjectId
        doc = await db.gold_signals.find_one({"signal_id": signal_id})
        if not doc:
            try:
                doc = await db.gold_signals.find_one({"_id": ObjectId(signal_id)})
                if doc:
                    signal_id = str(doc["_id"])
            except Exception:
                pass

        if not doc:
            await self._audit(
                "signal:reject", requesting_manager["manager_id"],
                requesting_manager["role"], signal_id,
                {}, success=False, error="Signal not found",
            )
            return {"success": False, "error": f"Signal '{signal_id}' not found"}

        current_status = doc.get("approval_status", SignalStatus.PENDING)
        if current_status != SignalStatus.PENDING:
            return {
                "success": False,
                "error":   f"Signal is already '{current_status}' — only PENDING signals can be rejected",
            }

        now = datetime.utcnow()
        update = {
            "approval_status":  SignalStatus.REJECTED,
            "status":           "CANCELLED",
            "rejected_by":      requesting_manager["manager_id"],
            "rejected_at":      now,
            "rejection_reason": reason.strip(),
            "reviewed_at":      now,
            "reviewed_by":      requesting_manager["manager_id"],
        }
        await db.gold_signals.update_one(
            {"_id": doc["_id"]},
            {"$set": update},
        )

        await self._audit(
            "signal:reject",
            requesting_manager["manager_id"],
            requesting_manager["role"],
            signal_id=signal_id,
            details={
                "pair":             doc.get("pair"),
                "type":             doc.get("type"),
                "entry":            doc.get("entry_price"),
                "confidence":       doc.get("confidence"),
                "rejection_reason": reason.strip(),
            },
        )

        logger.info(
            f"❌ Signal REJECTED: {signal_id} ({doc.get('pair')} {doc.get('type')}) "
            f"by {requesting_manager['manager_id']} — reason: {reason.strip()}"
        )
        return {
            "success":          True,
            "signal_id":        signal_id,
            "approval_status":  SignalStatus.REJECTED,
            "rejected_by":      requesting_manager["manager_id"],
            "rejected_at":      now.isoformat(),
            "rejection_reason": reason.strip(),
            "message":          "Signal rejected and removed from trading queue",
        }

    # ═══════════════════════════════════════════════════════════
    # 5. ADJUST SIGNAL
    # ═══════════════════════════════════════════════════════════

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
        Adjust entry price, TP levels, and/or SL of a PENDING signal,
        then automatically approve it.

        Requires: signal:adjust (ADMIN, MANAGER).

        At least one of entry_price, tp_levels, or sl_price must be provided.
        All provided values are validated for correct BUY/SELL geometry before
        the update is persisted.
        """
        check_permission(requesting_manager, _SIGNAL_ADJUST_PERM)

        if entry_price is None and tp_levels is None and sl_price is None:
            return {
                "success": False,
                "error":   "At least one of entry_price, tp_levels, or sl_price must be provided",
            }

        db = self._get_db()

        from bson import ObjectId
        doc = await db.gold_signals.find_one({"signal_id": signal_id})
        if not doc:
            try:
                doc = await db.gold_signals.find_one({"_id": ObjectId(signal_id)})
                if doc:
                    signal_id = str(doc["_id"])
            except Exception:
                pass

        if not doc:
            await self._audit(
                "signal:adjust", requesting_manager["manager_id"],
                requesting_manager["role"], signal_id,
                {}, success=False, error="Signal not found",
            )
            return {"success": False, "error": f"Signal '{signal_id}' not found"}

        current_status = doc.get("approval_status", SignalStatus.PENDING)
        if current_status != SignalStatus.PENDING:
            return {
                "success": False,
                "error":   f"Signal is already '{current_status}' — only PENDING signals can be adjusted",
            }

        signal_type = doc.get("type", "BUY")

        # Merge with existing values for validation
        new_entry  = entry_price if entry_price is not None else doc.get("entry_price", 0.0)
        new_tps    = tp_levels   if tp_levels   is not None else doc.get("tp_levels", [])
        new_sl     = sl_price    if sl_price    is not None else doc.get("sl_price", 0.0)

        # Validate geometry
        validation_error = _validate_price_levels(signal_type, new_entry, new_tps, new_sl)
        if validation_error:
            await self._audit(
                "signal:adjust", requesting_manager["manager_id"],
                requesting_manager["role"], signal_id,
                {"validation_error": validation_error},
                success=False, error=validation_error,
            )
            return {"success": False, "error": f"Price level validation failed: {validation_error}"}

        # Recalculate R:R with first TP
        new_rr = _calculate_rr(new_entry, new_sl, new_tps[0]) if new_tps else 0.0

        # Snapshot original values for audit
        original_values = {
            "entry_price": doc.get("entry_price"),
            "tp_levels":   doc.get("tp_levels"),
            "sl_price":    doc.get("sl_price"),
            "risk_reward": doc.get("risk_reward"),
        }

        now = datetime.utcnow()
        update: Dict[str, Any] = {
            "approval_status":   SignalStatus.ADJUSTED,
            "adjusted_by":       requesting_manager["manager_id"],
            "adjusted_at":       now,
            "adjustment_notes":  adjustment_notes or "",
            "original_values":   original_values,
            # Approved immediately after adjustment
            "approved_by":       requesting_manager["manager_id"],
            "approved_at":       now,
            "reviewed_at":       now,
            "reviewed_by":       requesting_manager["manager_id"],
            # Updated levels
            "entry_price":       new_entry,
            "tp_levels":         new_tps,
            "sl_price":          new_sl,
            "risk_reward":       new_rr,
        }
        await db.gold_signals.update_one(
            {"_id": doc["_id"]},
            {"$set": update},
        )

        changes: Dict[str, Any] = {}
        if entry_price is not None:
            changes["entry_price"] = {"from": original_values["entry_price"], "to": new_entry}
        if tp_levels is not None:
            changes["tp_levels"] = {"from": original_values["tp_levels"], "to": new_tps}
        if sl_price is not None:
            changes["sl_price"] = {"from": original_values["sl_price"], "to": new_sl}
        changes["risk_reward"] = {"from": original_values["risk_reward"], "to": new_rr}

        await self._audit(
            "signal:adjust",
            requesting_manager["manager_id"],
            requesting_manager["role"],
            signal_id=signal_id,
            details={
                "pair":             doc.get("pair"),
                "type":             signal_type,
                "changes":          changes,
                "adjustment_notes": adjustment_notes,
            },
        )

        logger.info(
            f"🔧 Signal ADJUSTED: {signal_id} ({doc.get('pair')} {signal_type}) "
            f"by {requesting_manager['manager_id']} — changes: {list(changes.keys())}"
        )
        return {
            "success":         True,
            "signal_id":       signal_id,
            "approval_status": SignalStatus.ADJUSTED,
            "adjusted_by":     requesting_manager["manager_id"],
            "adjusted_at":     now.isoformat(),
            "changes":         changes,
            "new_risk_reward": new_rr,
            "adjustment_notes": adjustment_notes or "",
            "message":         "Signal adjusted and approved for trading",
        }

    # ═══════════════════════════════════════════════════════════
    # 6. SIGNAL HISTORY
    # ═══════════════════════════════════════════════════════════

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
        Return reviewed signals (APPROVED, REJECTED, ADJUSTED) with filters.

        Requires: system:signals (all roles).

        Args:
            status:      Filter by approval status (APPROVED/REJECTED/ADJUSTED).
            pair:        Filter by trading pair.
            reviewed_by: Filter by manager_id who reviewed the signal.
            hours:       Look-back window in hours (default 7 days).
            limit:       Maximum results (1–500).
        """
        check_permission(requesting_manager, _SIGNAL_VIEW_PERM)
        db = self._get_db()

        cutoff = datetime.utcnow() - timedelta(hours=max(1, hours))
        query: Dict[str, Any] = {
            "created_at":      {"$gte": cutoff},
            "approval_status": {"$in": [
                SignalStatus.APPROVED,
                SignalStatus.REJECTED,
                SignalStatus.ADJUSTED,
            ]},
        }

        if status:
            try:
                query["approval_status"] = SignalStatus(status.upper())
            except ValueError:
                return {
                    "success": False,
                    "error":   f"Invalid status '{status}'. Valid: APPROVED, REJECTED, ADJUSTED",
                }

        if pair:
            query["pair"] = pair.upper()
        if reviewed_by:
            query["reviewed_by"] = reviewed_by

        raw = await (
            db.gold_signals
            .find(query)
            .sort("reviewed_at", -1)
            .limit(max(1, min(limit, 500)))
            .to_list(None)
        )

        signals = [self._fmt_signal(s) for s in raw]

        return {
            "success": True,
            "signals": signals,
            "count":   len(signals),
            "hours":   hours,
            "filters": {
                "status":      status,
                "pair":        pair,
                "reviewed_by": reviewed_by,
            },
        }

    # ═══════════════════════════════════════════════════════════
    # 7. APPROVAL STATISTICS
    # ═══════════════════════════════════════════════════════════

    async def get_approval_stats(
        self,
        requesting_manager: Dict[str, Any],
        hours: int = 168,
        manager_id_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Return approval statistics — global and per-manager breakdowns.

        Requires: system:signals (all roles).

        Args:
            hours:             Look-back window in hours (default 7 days).
            manager_id_filter: Restrict stats to a specific manager.
        """
        check_permission(requesting_manager, _SIGNAL_VIEW_PERM)
        db = self._get_db()

        cutoff = datetime.utcnow() - timedelta(hours=max(1, hours))

        # ── Global counts ─────────────────────────────────────
        async def _count(extra_query: Dict) -> int:
            try:
                q = {"created_at": {"$gte": cutoff}, **extra_query}
                return await db.gold_signals.count_documents(q)
            except Exception:
                return -1

        total_pending  = await _count({"approval_status": SignalStatus.PENDING})
        total_approved = await _count({"approval_status": SignalStatus.APPROVED})
        total_rejected = await _count({"approval_status": SignalStatus.REJECTED})
        total_adjusted = await _count({"approval_status": SignalStatus.ADJUSTED})
        total_reviewed = total_approved + total_rejected + total_adjusted

        approval_rate = (
            round((total_approved + total_adjusted) / total_reviewed * 100, 1)
            if total_reviewed > 0 else 0.0
        )
        rejection_rate = (
            round(total_rejected / total_reviewed * 100, 1)
            if total_reviewed > 0 else 0.0
        )

        # ── Per-manager breakdown ─────────────────────────────
        pipeline: List[Dict] = [
            {"$match": {
                "created_at":      {"$gte": cutoff},
                "approval_status": {"$in": [
                    SignalStatus.APPROVED,
                    SignalStatus.REJECTED,
                    SignalStatus.ADJUSTED,
                ]},
            }},
            {"$group": {
                "_id":      "$reviewed_by",
                "approved": {"$sum": {"$cond": [{"$eq": ["$approval_status", SignalStatus.APPROVED]}, 1, 0]}},
                "rejected": {"$sum": {"$cond": [{"$eq": ["$approval_status", SignalStatus.REJECTED]}, 1, 0]}},
                "adjusted": {"$sum": {"$cond": [{"$eq": ["$approval_status", SignalStatus.ADJUSTED]}, 1, 0]}},
                "total":    {"$sum": 1},
            }},
            {"$sort": {"total": -1}},
        ]
        if manager_id_filter:
            pipeline[0]["$match"]["reviewed_by"] = manager_id_filter

        try:
            per_manager_raw = await db.gold_signals.aggregate(pipeline).to_list(None)
        except Exception:
            per_manager_raw = []

        per_manager = []
        for row in per_manager_raw:
            mgr_total = row["total"]
            per_manager.append({
                "manager_id":    row["_id"],
                "approved":      row["approved"],
                "rejected":      row["rejected"],
                "adjusted":      row["adjusted"],
                "total_reviewed": mgr_total,
                "approval_rate": round((row["approved"] + row["adjusted"]) / mgr_total * 100, 1)
                                 if mgr_total > 0 else 0.0,
            })

        # ── Pair breakdown ────────────────────────────────────
        pair_pipeline: List[Dict] = [
            {"$match": {
                "created_at":      {"$gte": cutoff},
                "approval_status": {"$in": [
                    SignalStatus.APPROVED,
                    SignalStatus.REJECTED,
                    SignalStatus.ADJUSTED,
                ]},
            }},
            {"$group": {
                "_id":      "$pair",
                "approved": {"$sum": {"$cond": [{"$eq": ["$approval_status", SignalStatus.APPROVED]}, 1, 0]}},
                "rejected": {"$sum": {"$cond": [{"$eq": ["$approval_status", SignalStatus.REJECTED]}, 1, 0]}},
                "adjusted": {"$sum": {"$cond": [{"$eq": ["$approval_status", SignalStatus.ADJUSTED]}, 1, 0]}},
                "total":    {"$sum": 1},
            }},
            {"$sort": {"total": -1}},
        ]
        try:
            pair_stats_raw = await db.gold_signals.aggregate(pair_pipeline).to_list(None)
        except Exception:
            pair_stats_raw = []

        pair_stats = [
            {
                "pair":     row["_id"],
                "approved": row["approved"],
                "rejected": row["rejected"],
                "adjusted": row["adjusted"],
                "total":    row["total"],
            }
            for row in pair_stats_raw
        ]

        # ── Average confidence of approved signals ────────────
        try:
            conf_pipeline = [
                {"$match": {
                    "created_at":      {"$gte": cutoff},
                    "approval_status": {"$in": [SignalStatus.APPROVED, SignalStatus.ADJUSTED]},
                }},
                {"$group": {"_id": None, "avg_confidence": {"$avg": "$confidence"}}},
            ]
            conf_result = await db.gold_signals.aggregate(conf_pipeline).to_list(1)
            avg_approved_confidence = round(conf_result[0]["avg_confidence"], 1) if conf_result else 0.0
        except Exception:
            avg_approved_confidence = 0.0

        return {
            "success": True,
            "period_hours": hours,
            "global": {
                "total_pending":           total_pending,
                "total_approved":          total_approved,
                "total_rejected":          total_rejected,
                "total_adjusted":          total_adjusted,
                "total_reviewed":          total_reviewed,
                "approval_rate_pct":       approval_rate,
                "rejection_rate_pct":      rejection_rate,
                "avg_approved_confidence": avg_approved_confidence,
            },
            "per_manager": per_manager,
            "per_pair":    pair_stats,
            "generated_at": datetime.utcnow().isoformat(),
        }

    # ═══════════════════════════════════════════════════════════
    # UTILITY: mark new signals as PENDING
    # ═══════════════════════════════════════════════════════════

    async def mark_signal_pending(self, signal_mongo_id: str) -> None:
        """
        Called by the signal generation pipeline to stamp a newly
        inserted signal with ``approval_status = PENDING``.

        This is a fire-and-forget helper — errors are logged but not raised.
        """
        try:
            from bson import ObjectId
            db = self._get_db()
            await db.gold_signals.update_one(
                {"_id": ObjectId(signal_mongo_id)},
                {"$set": {"approval_status": SignalStatus.PENDING}},
            )
        except Exception as exc:
            logger.error(f"mark_signal_pending failed for {signal_mongo_id}: {exc}")


# ─────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────

signal_manager = SignalManager()

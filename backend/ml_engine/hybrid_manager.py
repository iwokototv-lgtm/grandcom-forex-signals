"""
Professional Hybrid Manager System
Enterprise-Grade Multi-Tier Approval Workflow with Advanced Risk Management
Gold Trading System v3.0.2

Provides:
  - HybridManagerRole enum (6 roles: SUPER_ADMIN, RISK_MANAGER, TRADING_MANAGER,
    ANALYST, OPERATOR, VIEWER)
  - HybridManager class with:
      * Multi-level approval workflow (2-3 approvals required)
      * Advanced risk management controls
      * Position & exposure limits
      * Drawdown monitoring & circuit breakers
      * Performance tracking & analytics
      * Team collaboration (comments, notes, mentions)
      * Automated alerts & notifications
      * Compliance & audit logging
  - Signal quality scoring engine
  - Manager performance metrics & leaderboards
  - Real-time monitoring dashboard
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME   = os.environ.get("DB_NAME",   "gold_signals_v3")

# ─────────────────────────────────────────────────────────────
# ROLE DEFINITIONS
# ─────────────────────────────────────────────────────────────

class HybridManagerRole(str, Enum):
    SUPER_ADMIN      = "SUPER_ADMIN"      # Full system control — all operations
    RISK_MANAGER     = "RISK_MANAGER"     # Risk controls, limits, circuit breakers
    TRADING_MANAGER  = "TRADING_MANAGER"  # Signal approval, trading operations
    ANALYST          = "ANALYST"          # Read + annotate, no approval authority
    OPERATOR         = "OPERATOR"         # Operational tasks, limited write access
    VIEWER           = "VIEWER"           # Read-only — monitoring & dashboard only


# ─────────────────────────────────────────────────────────────
# APPROVAL WORKFLOW CONFIGURATION
# ─────────────────────────────────────────────────────────────

# Minimum approvals required per signal risk tier
APPROVAL_REQUIREMENTS: Dict[str, int] = {
    "LOW":      1,   # Low-risk signals: 1 approval
    "MEDIUM":   2,   # Medium-risk signals: 2 approvals
    "HIGH":     3,   # High-risk signals: 3 approvals
    "CRITICAL": 3,   # Critical signals: 3 approvals + RISK_MANAGER mandatory
}

# Roles that can approve signals
APPROVAL_ROLES = {
    HybridManagerRole.SUPER_ADMIN,
    HybridManagerRole.RISK_MANAGER,
    HybridManagerRole.TRADING_MANAGER,
}

# Roles that can reject signals
REJECTION_ROLES = {
    HybridManagerRole.SUPER_ADMIN,
    HybridManagerRole.RISK_MANAGER,
    HybridManagerRole.TRADING_MANAGER,
    HybridManagerRole.OPERATOR,
}

# ─────────────────────────────────────────────────────────────
# PERMISSION MATRIX
# ─────────────────────────────────────────────────────────────

HYBRID_ROLE_PERMISSIONS: Dict[HybridManagerRole, set] = {
    HybridManagerRole.SUPER_ADMIN: {
        # Manager CRUD
        "manager:add", "manager:remove", "manager:update", "manager:list", "manager:get",
        "manager:promote", "manager:demote", "manager:suspend",
        # Signal operations
        "signal:approve", "signal:reject", "signal:adjust", "signal:comment",
        "signal:view", "signal:list", "signal:override", "signal:escalate",
        # Risk controls
        "risk:set_limits", "risk:override", "risk:view", "risk:circuit_breaker",
        "risk:drawdown_override", "risk:exposure_adjust",
        # Performance
        "performance:view", "performance:export", "performance:compare",
        # Compliance
        "compliance:view", "compliance:export", "compliance:audit",
        # Alerts
        "alert:create", "alert:resolve", "alert:list", "alert:escalate",
        # System
        "system:status", "system:signals", "system:logs", "system:config",
        "system:restart", "system:deploy", "system:backup",
        # Dashboard
        "dashboard:view", "dashboard:realtime", "dashboard:export",
        # Collaboration
        "collab:comment", "collab:note", "collab:mention", "collab:view",
        # Audit
        "audit:view", "audit:export",
    },
    HybridManagerRole.RISK_MANAGER: {
        # Manager (read only)
        "manager:list", "manager:get",
        # Signal operations
        "signal:approve", "signal:reject", "signal:adjust", "signal:comment",
        "signal:view", "signal:list", "signal:escalate",
        # Risk controls (full)
        "risk:set_limits", "risk:override", "risk:view", "risk:circuit_breaker",
        "risk:drawdown_override", "risk:exposure_adjust",
        # Performance
        "performance:view", "performance:export",
        # Compliance
        "compliance:view", "compliance:export", "compliance:audit",
        # Alerts
        "alert:create", "alert:resolve", "alert:list", "alert:escalate",
        # System
        "system:status", "system:signals", "system:logs",
        # Dashboard
        "dashboard:view", "dashboard:realtime",
        # Collaboration
        "collab:comment", "collab:note", "collab:mention", "collab:view",
        # Audit
        "audit:view", "audit:export",
    },
    HybridManagerRole.TRADING_MANAGER: {
        # Manager (read only)
        "manager:list", "manager:get",
        # Signal operations
        "signal:approve", "signal:reject", "signal:adjust", "signal:comment",
        "signal:view", "signal:list",
        # Risk controls (view + limited)
        "risk:view", "risk:exposure_adjust",
        # Performance
        "performance:view",
        # Compliance
        "compliance:view",
        # Alerts
        "alert:create", "alert:resolve", "alert:list",
        # System
        "system:status", "system:signals", "system:logs",
        # Dashboard
        "dashboard:view", "dashboard:realtime",
        # Collaboration
        "collab:comment", "collab:note", "collab:mention", "collab:view",
        # Audit
        "audit:view",
    },
    HybridManagerRole.ANALYST: {
        # Manager (read only)
        "manager:list", "manager:get",
        # Signal operations (read + comment only)
        "signal:view", "signal:list", "signal:comment",
        # Risk controls (view only)
        "risk:view",
        # Performance
        "performance:view",
        # Compliance
        "compliance:view",
        # Alerts
        "alert:list",
        # System
        "system:status", "system:signals", "system:logs",
        # Dashboard
        "dashboard:view",
        # Collaboration
        "collab:comment", "collab:note", "collab:view",
        # Audit
        "audit:view",
    },
    HybridManagerRole.OPERATOR: {
        # Manager (read only)
        "manager:list", "manager:get",
        # Signal operations (limited)
        "signal:view", "signal:list", "signal:reject", "signal:comment",
        # Risk controls (view only)
        "risk:view",
        # Performance
        "performance:view",
        # Alerts
        "alert:create", "alert:list",
        # System
        "system:status", "system:signals", "system:logs",
        # Dashboard
        "dashboard:view",
        # Collaboration
        "collab:comment", "collab:view",
        # Audit
        "audit:view",
    },
    HybridManagerRole.VIEWER: {
        # Read-only access
        "manager:list", "manager:get",
        "signal:view", "signal:list",
        "risk:view",
        "performance:view",
        "compliance:view",
        "alert:list",
        "system:status", "system:signals", "system:logs",
        "dashboard:view",
        "collab:view",
        "audit:view",
    },
}


# ─────────────────────────────────────────────────────────────
# SIGNAL RISK TIER CLASSIFICATION
# ─────────────────────────────────────────────────────────────

def classify_signal_risk_tier(signal: Dict[str, Any]) -> str:
    """
    Classify a signal into a risk tier based on multiple factors.

    Factors:
      - Confidence score
      - Risk/reward ratio
      - Position size
      - Market volatility indicator
      - Time of day (session)

    Returns: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
    """
    confidence  = float(signal.get("confidence", 0))
    rr_ratio    = float(signal.get("risk_reward", signal.get("rr", 0)))
    lot_size    = float(signal.get("lot_size", signal.get("position_size", 0.01)))
    volatility  = signal.get("volatility", "NORMAL")  # LOW | NORMAL | HIGH | EXTREME

    score = 0

    # Confidence scoring (lower confidence = higher risk)
    if confidence >= 85:
        score += 0
    elif confidence >= 70:
        score += 1
    elif confidence >= 55:
        score += 2
    else:
        score += 3

    # R:R scoring (lower R:R = higher risk)
    if rr_ratio >= 3.0:
        score += 0
    elif rr_ratio >= 2.0:
        score += 1
    elif rr_ratio >= 1.5:
        score += 2
    else:
        score += 3

    # Position size scoring
    if lot_size <= 0.05:
        score += 0
    elif lot_size <= 0.10:
        score += 1
    elif lot_size <= 0.50:
        score += 2
    else:
        score += 3

    # Volatility scoring
    vol_scores = {"LOW": 0, "NORMAL": 1, "HIGH": 2, "EXTREME": 3}
    score += vol_scores.get(str(volatility).upper(), 1)

    # Map total score to tier
    if score <= 2:
        return "LOW"
    elif score <= 5:
        return "MEDIUM"
    elif score <= 8:
        return "HIGH"
    else:
        return "CRITICAL"


# ─────────────────────────────────────────────────────────────
# SIGNAL QUALITY SCORER
# ─────────────────────────────────────────────────────────────

def score_signal_quality(signal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute a comprehensive quality score for a trading signal.

    Dimensions scored (0-100 each):
      1. Technical Confidence  — ML model confidence
      2. Risk/Reward Quality   — R:R ratio quality
      3. Entry Precision       — Entry vs current price proximity
      4. Multi-Timeframe Align — MTF confluence score
      5. Market Regime Fit     — Strategy-regime alignment
      6. Volatility Context    — ATR-based volatility assessment
      7. Session Quality       — Trading session timing
      8. Historical Pattern    — Similar historical signal performance

    Returns composite score (0-100) with dimension breakdown.
    """
    scores: Dict[str, float] = {}

    # 1. Technical Confidence (0-100)
    confidence = float(signal.get("confidence", 0))
    scores["technical_confidence"] = min(100.0, confidence)

    # 2. Risk/Reward Quality (0-100)
    rr = float(signal.get("risk_reward", signal.get("rr", 0)))
    if rr >= 4.0:
        scores["rr_quality"] = 100.0
    elif rr >= 3.0:
        scores["rr_quality"] = 85.0
    elif rr >= 2.5:
        scores["rr_quality"] = 75.0
    elif rr >= 2.0:
        scores["rr_quality"] = 65.0
    elif rr >= 1.5:
        scores["rr_quality"] = 50.0
    elif rr >= 1.0:
        scores["rr_quality"] = 30.0
    else:
        scores["rr_quality"] = 0.0

    # 3. Entry Precision (0-100) — how close entry is to current price
    entry         = float(signal.get("entry_price", signal.get("entry", 0)))
    current_price = float(signal.get("current_price", entry))
    if entry > 0 and current_price > 0:
        slippage_pct = abs(entry - current_price) / current_price * 100
        if slippage_pct <= 0.01:
            scores["entry_precision"] = 100.0
        elif slippage_pct <= 0.05:
            scores["entry_precision"] = 80.0
        elif slippage_pct <= 0.10:
            scores["entry_precision"] = 60.0
        elif slippage_pct <= 0.20:
            scores["entry_precision"] = 40.0
        else:
            scores["entry_precision"] = 20.0
    else:
        scores["entry_precision"] = 50.0  # neutral if no price data

    # 4. Multi-Timeframe Alignment (0-100)
    mtf_score = float(signal.get("mtf_confluence", signal.get("mtf_score", 50)))
    scores["mtf_alignment"] = min(100.0, max(0.0, mtf_score))

    # 5. Market Regime Fit (0-100)
    strategy = str(signal.get("strategy", "")).upper()
    regime   = str(signal.get("regime",   "")).upper()
    regime_fit_map = {
        ("SMC",           "TREND_UP"):    95,
        ("SMC",           "TREND_DOWN"):  95,
        ("MEAN_REVERSION","RANGE"):       90,
        ("BREAKOUT",      "TREND_UP"):    85,
        ("BREAKOUT",      "TREND_DOWN"):  85,
        ("SMC",           "RANGE"):       60,
        ("MEAN_REVERSION","TREND_UP"):    50,
        ("MEAN_REVERSION","TREND_DOWN"):  50,
    }
    scores["regime_fit"] = float(regime_fit_map.get((strategy, regime), 65))

    # 6. Volatility Context (0-100)
    volatility = str(signal.get("volatility", "NORMAL")).upper()
    vol_scores_map = {"LOW": 70, "NORMAL": 90, "HIGH": 60, "EXTREME": 20}
    scores["volatility_context"] = float(vol_scores_map.get(volatility, 70))

    # 7. Session Quality (0-100)
    session = str(signal.get("session", "")).upper()
    session_scores = {
        "LONDON":    95,
        "NEW_YORK":  90,
        "OVERLAP":   100,
        "ASIAN":     60,
        "OFF_HOURS": 30,
    }
    scores["session_quality"] = float(session_scores.get(session, 70))

    # 8. Historical Pattern Score (0-100) — from signal metadata if available
    hist_score = float(signal.get("historical_win_rate", 0.65)) * 100
    scores["historical_pattern"] = min(100.0, max(0.0, hist_score))

    # Weighted composite score
    weights = {
        "technical_confidence": 0.25,
        "rr_quality":           0.20,
        "entry_precision":      0.10,
        "mtf_alignment":        0.15,
        "regime_fit":           0.10,
        "volatility_context":   0.10,
        "session_quality":      0.05,
        "historical_pattern":   0.05,
    }

    composite = sum(scores[k] * weights[k] for k in scores)

    # Grade assignment
    if composite >= 85:
        grade = "A+"
    elif composite >= 75:
        grade = "A"
    elif composite >= 65:
        grade = "B"
    elif composite >= 55:
        grade = "C"
    elif composite >= 45:
        grade = "D"
    else:
        grade = "F"

    return {
        "composite_score": round(composite, 2),
        "grade":           grade,
        "dimensions":      {k: round(v, 2) for k, v in scores.items()},
        "risk_tier":       classify_signal_risk_tier(signal),
        "recommendation":  "APPROVE" if composite >= 65 else ("REVIEW" if composite >= 45 else "REJECT"),
    }


# ─────────────────────────────────────────────────────────────
# PERMISSION HELPER
# ─────────────────────────────────────────────────────────────

def check_hybrid_permission(manager: Dict[str, Any], action: str) -> None:
    """
    Raise PermissionError if *manager* does not hold *action*.
    manager dict must contain a 'role' key (HybridManagerRole value).
    """
    role_str = manager.get("role", "")
    try:
        role = HybridManagerRole(role_str)
    except ValueError:
        raise PermissionError(f"Unknown hybrid manager role: '{role_str}'")

    if action not in HYBRID_ROLE_PERMISSIONS.get(role, set()):
        raise PermissionError(
            f"Role '{role}' does not have permission for action '{action}'"
        )


# ─────────────────────────────────────────────────────────────
# HYBRID MANAGER — CORE CLASS
# ─────────────────────────────────────────────────────────────

class HybridManager:
    """
    Enterprise-grade Hybrid Manager System.

    Central controller for the Gold Trading System's professional
    multi-tier approval workflow, risk management, and team collaboration.

    All mutating operations require a *requesting_manager* dict that
    carries at minimum ``{"manager_id": str, "role": HybridManagerRole}``.
    Every operation is recorded in the hybrid_audit_log collection.

    Collections used:
      hybrid_managers          — manager accounts with extended profiles
      hybrid_signals           — signals with multi-tier approval state
      hybrid_approval_log      — immutable approval/rejection records
      hybrid_risk_config       — risk limits and circuit breaker state
      hybrid_alerts            — automated and manual alerts
      hybrid_comments          — team collaboration comments/notes
      hybrid_performance       — manager performance metrics
      hybrid_audit_log         — full compliance audit trail
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
        ip_address: Optional[str] = None,
    ) -> None:
        """Persist an immutable compliance audit record."""
        try:
            db = self._get_db()
            entry = {
                "audit_id":     str(uuid.uuid4()),
                "timestamp":    datetime.utcnow(),
                "action":       action,
                "performed_by": performed_by,
                "role":         role,
                "details":      details,
                "success":      success,
                "error":        error,
                "ip_address":   ip_address,
                "system":       "hybrid_manager",
                "version":      "3.0.2",
            }
            await db.hybrid_audit_log.insert_one(entry)
        except Exception as exc:
            logger.error(f"Hybrid audit log write failed: {exc}")

    # ── Alert generation ──────────────────────────────────────

    async def _create_alert(
        self,
        title: str,
        message: str,
        severity: str,
        category: str,
        created_by: str = "SYSTEM",
        metadata: Optional[Dict] = None,
    ) -> str:
        """Internal alert creation — returns alert_id."""
        try:
            db = self._get_db()
            alert_id = str(uuid.uuid4())
            await db.hybrid_alerts.insert_one({
                "alert_id":   alert_id,
                "title":      title,
                "message":    message,
                "severity":   severity,
                "category":   category,
                "resolved":   False,
                "created_at": datetime.utcnow(),
                "created_by": created_by,
                "metadata":   metadata or {},
            })
            logger.info(f"🚨 Hybrid Alert [{severity}]: {title}")
            return alert_id
        except Exception as exc:
            logger.error(f"Alert creation failed: {exc}")
            return ""

    # ═══════════════════════════════════════════════════════════
    # MANAGER MANAGEMENT
    # ═══════════════════════════════════════════════════════════

    async def add_manager(
        self,
        requesting_manager: Dict[str, Any],
        email: str,
        full_name: str,
        role: HybridManagerRole,
        password_hash: str,
        department: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a new hybrid manager account.
        Requires: SUPER_ADMIN role (manager:add).
        """
        check_hybrid_permission(requesting_manager, "manager:add")
        db = self._get_db()

        existing = await db.hybrid_managers.find_one({"email": email})
        if existing:
            await self._audit(
                "manager:add", requesting_manager["manager_id"],
                requesting_manager["role"],
                {"email": email, "role": role.value},
                success=False, error="Email already registered",
            )
            return {"success": False, "error": "A manager with that email already exists"}

        manager_id = str(uuid.uuid4())
        doc = {
            "manager_id":       manager_id,
            "email":            email,
            "full_name":        full_name,
            "role":             role.value,
            "password_hash":    password_hash,
            "department":       department or "General",
            "is_active":        True,
            "is_suspended":     False,
            "created_at":       datetime.utcnow(),
            "created_by":       requesting_manager["manager_id"],
            "last_login":       None,
            "last_activity":    None,
            "metadata":         metadata or {},
            "performance_stats": {
                "total_approvals":  0,
                "total_rejections": 0,
                "total_adjustments": 0,
                "approval_accuracy": 0.0,
                "avg_review_time_minutes": 0.0,
                "signals_reviewed": 0,
            },
            "notification_prefs": {
                "email_alerts":    True,
                "critical_only":   False,
                "daily_digest":    True,
            },
        }
        await db.hybrid_managers.insert_one(doc)

        await self._audit(
            "manager:add", requesting_manager["manager_id"],
            requesting_manager["role"],
            {"new_manager_id": manager_id, "email": email, "role": role.value},
        )
        logger.info(f"✅ Hybrid Manager added: {email} ({role.value})")
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
        Deactivate (soft-delete) a hybrid manager account.
        Requires: SUPER_ADMIN role (manager:remove).
        Cannot remove yourself.
        """
        check_hybrid_permission(requesting_manager, "manager:remove")

        if requesting_manager["manager_id"] == target_manager_id:
            return {"success": False, "error": "Cannot remove your own account"}

        db = self._get_db()
        result = await db.hybrid_managers.update_one(
            {"manager_id": target_manager_id},
            {"$set": {
                "is_active":       False,
                "deactivated_at":  datetime.utcnow(),
                "deactivated_by":  requesting_manager["manager_id"],
            }},
        )
        if result.modified_count == 0:
            return {"success": False, "error": "Manager not found"}

        await self._audit(
            "manager:remove", requesting_manager["manager_id"],
            requesting_manager["role"],
            {"target_manager_id": target_manager_id},
        )
        return {"success": True, "message": "Manager deactivated successfully"}

    async def suspend_manager(
        self,
        requesting_manager: Dict[str, Any],
        target_manager_id: str,
        reason: str,
    ) -> Dict[str, Any]:
        """
        Suspend a manager account (temporary block without deactivation).
        Requires: SUPER_ADMIN role (manager:suspend).
        """
        check_hybrid_permission(requesting_manager, "manager:suspend")

        if requesting_manager["manager_id"] == target_manager_id:
            return {"success": False, "error": "Cannot suspend your own account"}

        db = self._get_db()
        result = await db.hybrid_managers.update_one(
            {"manager_id": target_manager_id, "is_active": True},
            {"$set": {
                "is_suspended":    True,
                "suspended_at":    datetime.utcnow(),
                "suspended_by":    requesting_manager["manager_id"],
                "suspension_reason": reason,
            }},
        )
        if result.modified_count == 0:
            return {"success": False, "error": "Manager not found or already inactive"}

        await self._audit(
            "manager:suspend", requesting_manager["manager_id"],
            requesting_manager["role"],
            {"target_manager_id": target_manager_id, "reason": reason},
        )
        await self._create_alert(
            title=f"Manager Suspended: {target_manager_id}",
            message=f"Manager account suspended. Reason: {reason}",
            severity="WARNING",
            category="SECURITY",
            created_by=requesting_manager["manager_id"],
        )
        return {"success": True, "message": "Manager suspended"}

    async def update_manager(
        self,
        requesting_manager: Dict[str, Any],
        target_manager_id: str,
        updates: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Update manager fields.
        Requires: SUPER_ADMIN role (manager:update).
        """
        check_hybrid_permission(requesting_manager, "manager:update")

        allowed_fields = {"role", "full_name", "is_active", "department",
                          "metadata", "notification_prefs", "is_suspended"}
        sanitised = {k: v for k, v in updates.items() if k in allowed_fields}
        if not sanitised:
            return {"success": False, "error": "No valid fields to update"}

        if "role" in sanitised:
            try:
                sanitised["role"] = HybridManagerRole(sanitised["role"]).value
            except ValueError:
                return {"success": False, "error": f"Invalid role: {sanitised['role']}"}

        sanitised["updated_at"] = datetime.utcnow()
        sanitised["updated_by"] = requesting_manager["manager_id"]

        db = self._get_db()
        result = await db.hybrid_managers.update_one(
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
        role_filter: Optional[str] = None,
        department_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        List all hybrid manager accounts with optional filters.
        Requires: manager:list (all roles).
        """
        check_hybrid_permission(requesting_manager, "manager:list")
        db = self._get_db()

        query: Dict[str, Any] = {} if include_inactive else {"is_active": True}
        if role_filter:
            query["role"] = role_filter.upper()
        if department_filter:
            query["department"] = department_filter

        managers = await db.hybrid_managers.find(
            query, {"password_hash": 0}
        ).sort("created_at", -1).to_list(500)

        formatted = []
        for m in managers:
            m.pop("_id", None)
            for ts in ("created_at", "last_login", "last_activity",
                       "deactivated_at", "suspended_at", "updated_at"):
                if m.get(ts) and hasattr(m[ts], "isoformat"):
                    m[ts] = m[ts].isoformat()
            formatted.append(m)

        return {
            "success":  True,
            "managers": formatted,
            "count":    len(formatted),
            "filters":  {"include_inactive": include_inactive, "role": role_filter, "department": department_filter},
        }

    async def get_manager(
        self,
        requesting_manager: Dict[str, Any],
        target_manager_id: str,
    ) -> Dict[str, Any]:
        """
        Retrieve a single hybrid manager by ID.
        Requires: manager:get (all roles).
        """
        check_hybrid_permission(requesting_manager, "manager:get")
        db = self._get_db()

        m = await db.hybrid_managers.find_one(
            {"manager_id": target_manager_id}, {"password_hash": 0}
        )
        if not m:
            return {"success": False, "error": "Manager not found"}

        m.pop("_id", None)
        for ts in ("created_at", "last_login", "last_activity",
                   "deactivated_at", "suspended_at", "updated_at"):
            if m.get(ts) and hasattr(m[ts], "isoformat"):
                m[ts] = m[ts].isoformat()

        return {"success": True, "manager": m}

    # ═══════════════════════════════════════════════════════════
    # MULTI-TIER SIGNAL APPROVAL WORKFLOW
    # ═══════════════════════════════════════════════════════════

    async def submit_signal_for_review(
        self,
        signal_id: str,
        signal_data: Dict[str, Any],
        submitted_by: str = "SYSTEM",
    ) -> Dict[str, Any]:
        """
        Submit a signal into the hybrid approval workflow.
        Scores the signal, classifies risk tier, and sets required approvals.
        """
        db = self._get_db()

        # Score the signal
        quality = score_signal_quality(signal_data)
        risk_tier = quality["risk_tier"]
        required_approvals = APPROVAL_REQUIREMENTS.get(risk_tier, 2)

        # Check if RISK_MANAGER approval is mandatory
        risk_manager_required = risk_tier in ("HIGH", "CRITICAL")

        review_doc = {
            "review_id":              str(uuid.uuid4()),
            "signal_id":              signal_id,
            "submitted_at":           datetime.utcnow(),
            "submitted_by":           submitted_by,
            "status":                 "PENDING_REVIEW",
            "risk_tier":              risk_tier,
            "quality_score":          quality,
            "required_approvals":     required_approvals,
            "risk_manager_required":  risk_manager_required,
            "approvals":              [],
            "rejections":             [],
            "adjustments":            [],
            "comments":               [],
            "escalations":            [],
            "current_approval_count": 0,
            "is_approved":            False,
            "is_rejected":            False,
            "final_decision":         None,
            "final_decision_at":      None,
            "final_decision_by":      None,
            "signal_data":            signal_data,
            "expires_at":             datetime.utcnow() + timedelta(hours=24),
        }

        await db.hybrid_signals.insert_one(review_doc)

        # Auto-alert for HIGH/CRITICAL signals
        if risk_tier in ("HIGH", "CRITICAL"):
            await self._create_alert(
                title=f"⚠️ {risk_tier} Risk Signal Requires Review",
                message=(
                    f"Signal {signal_id} classified as {risk_tier} risk. "
                    f"Requires {required_approvals} approvals"
                    + (" including RISK_MANAGER." if risk_manager_required else ".")
                ),
                severity="WARNING" if risk_tier == "HIGH" else "CRITICAL",
                category="TRADING",
                metadata={"signal_id": signal_id, "risk_tier": risk_tier},
            )

        logger.info(
            f"📋 Signal {signal_id} submitted for review — "
            f"tier={risk_tier}, required_approvals={required_approvals}"
        )
        return {
            "success":            True,
            "review_id":          review_doc["review_id"],
            "signal_id":          signal_id,
            "risk_tier":          risk_tier,
            "quality_score":      quality["composite_score"],
            "grade":              quality["grade"],
            "required_approvals": required_approvals,
            "risk_manager_required": risk_manager_required,
            "recommendation":     quality["recommendation"],
        }

    async def approve_signal(
        self,
        requesting_manager: Dict[str, Any],
        signal_id: str,
        notes: Optional[str] = None,
        adjusted_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Approve a signal in the multi-tier workflow.
        Each approval is recorded; signal activates when required count is reached.
        Requires: signal:approve (SUPER_ADMIN, RISK_MANAGER, TRADING_MANAGER).
        """
        check_hybrid_permission(requesting_manager, "signal:approve")
        db = self._get_db()

        review = await db.hybrid_signals.find_one(
            {"signal_id": signal_id, "status": "PENDING_REVIEW"}
        )
        if not review:
            return {"success": False, "error": "Signal not found or not pending review"}

        manager_id = requesting_manager["manager_id"]
        role       = requesting_manager["role"]

        # Check for duplicate approval from same manager
        existing_approvals = [a["manager_id"] for a in review.get("approvals", [])]
        if manager_id in existing_approvals:
            return {"success": False, "error": "You have already approved this signal"}

        # Record this approval
        approval_record = {
            "approval_id":  str(uuid.uuid4()),
            "manager_id":   manager_id,
            "role":         role,
            "approved_at":  datetime.utcnow().isoformat(),
            "notes":        notes or "",
            "adjusted_params": adjusted_params or {},
        }

        new_approval_count = review.get("current_approval_count", 0) + 1
        required           = review.get("required_approvals", 2)
        risk_mgr_required  = review.get("risk_manager_required", False)

        # Check if RISK_MANAGER has approved (for HIGH/CRITICAL signals)
        all_approvals = review.get("approvals", []) + [approval_record]
        risk_mgr_approved = any(
            a["role"] == HybridManagerRole.RISK_MANAGER.value
            for a in all_approvals
        )

        # Determine if signal is fully approved
        fully_approved = (
            new_approval_count >= required
            and (not risk_mgr_required or risk_mgr_approved)
        )

        update_fields: Dict[str, Any] = {
            "current_approval_count": new_approval_count,
        }
        update_push = {"approvals": approval_record}

        if fully_approved:
            update_fields.update({
                "status":           "APPROVED",
                "is_approved":      True,
                "final_decision":   "APPROVED",
                "final_decision_at": datetime.utcnow(),
                "final_decision_by": manager_id,
            })
            # Apply any adjusted params to signal data
            if adjusted_params:
                for k, v in adjusted_params.items():
                    update_fields[f"signal_data.{k}"] = v

        await db.hybrid_signals.update_one(
            {"signal_id": signal_id},
            {"$set": update_fields, "$push": update_push},
        )

        # Update manager performance stats
        await self._update_manager_stats(manager_id, "approval")

        await self._audit(
            "signal:approve", manager_id, role,
            {
                "signal_id":          signal_id,
                "approval_count":     new_approval_count,
                "required":           required,
                "fully_approved":     fully_approved,
                "notes":              notes,
                "adjusted_params":    adjusted_params,
            },
        )

        if fully_approved:
            logger.info(f"✅ Signal {signal_id} FULLY APPROVED ({new_approval_count}/{required})")
            await self._create_alert(
                title=f"✅ Signal Approved: {signal_id}",
                message=f"Signal received all {required} required approvals and is now ACTIVE.",
                severity="INFO",
                category="TRADING",
                metadata={"signal_id": signal_id},
            )

        return {
            "success":            True,
            "signal_id":          signal_id,
            "approval_count":     new_approval_count,
            "required_approvals": required,
            "fully_approved":     fully_approved,
            "status":             "APPROVED" if fully_approved else "PENDING_REVIEW",
            "message":            (
                f"Signal fully approved and activated."
                if fully_approved
                else f"Approval recorded ({new_approval_count}/{required}). Awaiting more approvals."
            ),
        }

    async def reject_signal(
        self,
        requesting_manager: Dict[str, Any],
        signal_id: str,
        reason: str,
        category: str = "QUALITY",
    ) -> Dict[str, Any]:
        """
        Reject a signal — immediately removes it from the approval queue.
        Requires: signal:reject (SUPER_ADMIN, RISK_MANAGER, TRADING_MANAGER, OPERATOR).
        Reason is mandatory.
        """
        check_hybrid_permission(requesting_manager, "signal:reject")

        if not reason or len(reason.strip()) < 10:
            return {"success": False, "error": "Rejection reason must be at least 10 characters"}

        db = self._get_db()
        review = await db.hybrid_signals.find_one(
            {"signal_id": signal_id, "status": "PENDING_REVIEW"}
        )
        if not review:
            return {"success": False, "error": "Signal not found or not pending review"}

        manager_id = requesting_manager["manager_id"]
        role       = requesting_manager["role"]

        rejection_record = {
            "rejection_id": str(uuid.uuid4()),
            "manager_id":   manager_id,
            "role":         role,
            "rejected_at":  datetime.utcnow().isoformat(),
            "reason":       reason,
            "category":     category,
        }

        await db.hybrid_signals.update_one(
            {"signal_id": signal_id},
            {
                "$set": {
                    "status":           "REJECTED",
                    "is_rejected":      True,
                    "final_decision":   "REJECTED",
                    "final_decision_at": datetime.utcnow(),
                    "final_decision_by": manager_id,
                },
                "$push": {"rejections": rejection_record},
            },
        )

        await self._update_manager_stats(manager_id, "rejection")

        await self._audit(
            "signal:reject", manager_id, role,
            {"signal_id": signal_id, "reason": reason, "category": category},
        )

        logger.info(f"❌ Signal {signal_id} REJECTED by {manager_id}: {reason}")
        return {
            "success":   True,
            "signal_id": signal_id,
            "status":    "REJECTED",
            "reason":    reason,
            "rejected_by": manager_id,
        }

    async def adjust_signal(
        self,
        requesting_manager: Dict[str, Any],
        signal_id: str,
        adjustments: Dict[str, Any],
        reason: str,
    ) -> Dict[str, Any]:
        """
        Adjust signal parameters (entry, TP, SL, lot size) before approval.
        Requires: signal:adjust (SUPER_ADMIN, RISK_MANAGER, TRADING_MANAGER).
        """
        check_hybrid_permission(requesting_manager, "signal:adjust")

        if not adjustments:
            return {"success": False, "error": "No adjustments provided"}

        allowed_adjustments = {
            "entry_price", "entry", "tp1", "tp2", "tp3",
            "sl_price", "sl", "lot_size", "position_size",
            "take_profit_levels", "stop_loss",
        }
        invalid = set(adjustments.keys()) - allowed_adjustments
        if invalid:
            return {"success": False, "error": f"Invalid adjustment fields: {invalid}"}

        db = self._get_db()
        review = await db.hybrid_signals.find_one(
            {"signal_id": signal_id, "status": "PENDING_REVIEW"}
        )
        if not review:
            return {"success": False, "error": "Signal not found or not pending review"}

        manager_id = requesting_manager["manager_id"]
        role       = requesting_manager["role"]

        adjustment_record = {
            "adjustment_id": str(uuid.uuid4()),
            "manager_id":    manager_id,
            "role":          role,
            "adjusted_at":   datetime.utcnow().isoformat(),
            "adjustments":   adjustments,
            "reason":        reason,
            "previous_values": {
                k: review.get("signal_data", {}).get(k)
                for k in adjustments
            },
        }

        # Apply adjustments to signal_data
        signal_data_updates = {f"signal_data.{k}": v for k, v in adjustments.items()}
        signal_data_updates["status"] = "PENDING_REVIEW"  # Keep in review

        await db.hybrid_signals.update_one(
            {"signal_id": signal_id},
            {
                "$set":  signal_data_updates,
                "$push": {"adjustments": adjustment_record},
            },
        )

        await self._update_manager_stats(manager_id, "adjustment")

        await self._audit(
            "signal:adjust", manager_id, role,
            {"signal_id": signal_id, "adjustments": adjustments, "reason": reason},
        )

        return {
            "success":    True,
            "signal_id":  signal_id,
            "adjustments": adjustments,
            "reason":     reason,
            "adjusted_by": manager_id,
        }

    async def escalate_signal(
        self,
        requesting_manager: Dict[str, Any],
        signal_id: str,
        escalation_reason: str,
        escalate_to_role: str = "RISK_MANAGER",
    ) -> Dict[str, Any]:
        """
        Escalate a signal to a higher authority for review.
        Requires: signal:escalate (SUPER_ADMIN, RISK_MANAGER, TRADING_MANAGER).
        """
        check_hybrid_permission(requesting_manager, "signal:escalate")

        db = self._get_db()
        review = await db.hybrid_signals.find_one({"signal_id": signal_id})
        if not review:
            return {"success": False, "error": "Signal not found"}

        manager_id = requesting_manager["manager_id"]
        role       = requesting_manager["role"]

        escalation_record = {
            "escalation_id":  str(uuid.uuid4()),
            "escalated_by":   manager_id,
            "escalated_from": role,
            "escalated_to":   escalate_to_role,
            "reason":         escalation_reason,
            "escalated_at":   datetime.utcnow().isoformat(),
        }

        await db.hybrid_signals.update_one(
            {"signal_id": signal_id},
            {
                "$set":  {"status": "ESCALATED", "escalated_to": escalate_to_role},
                "$push": {"escalations": escalation_record},
            },
        )

        await self._create_alert(
            title=f"🔺 Signal Escalated: {signal_id}",
            message=f"Signal escalated to {escalate_to_role}. Reason: {escalation_reason}",
            severity="WARNING",
            category="TRADING",
            created_by=manager_id,
            metadata={"signal_id": signal_id, "escalated_to": escalate_to_role},
        )

        await self._audit(
            "signal:escalate", manager_id, role,
            {"signal_id": signal_id, "reason": escalation_reason, "escalated_to": escalate_to_role},
        )

        return {
            "success":      True,
            "signal_id":    signal_id,
            "escalated_to": escalate_to_role,
            "reason":       escalation_reason,
        }

    async def get_pending_signals(
        self,
        requesting_manager: Dict[str, Any],
        limit: int = 50,
        risk_tier_filter: Optional[str] = None,
        pair_filter: Optional[str] = None,
        min_quality_score: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Return signals awaiting review in the hybrid workflow.
        Requires: signal:list (all roles).
        """
        check_hybrid_permission(requesting_manager, "signal:list")
        db = self._get_db()

        query: Dict[str, Any] = {"status": {"$in": ["PENDING_REVIEW", "ESCALATED"]}}
        if risk_tier_filter:
            query["risk_tier"] = risk_tier_filter.upper()
        if pair_filter:
            query["signal_data.pair"] = pair_filter.upper()
        if min_quality_score is not None:
            query["quality_score.composite_score"] = {"$gte": min_quality_score}

        limit = max(1, min(limit, 200))
        signals = await (
            db.hybrid_signals
            .find(query)
            .sort("submitted_at", -1)
            .limit(limit)
            .to_list(length=limit)
        )

        formatted = []
        for s in signals:
            s.pop("_id", None)
            for ts in ("submitted_at", "final_decision_at", "expires_at"):
                if s.get(ts) and hasattr(s[ts], "isoformat"):
                    s[ts] = s[ts].isoformat()
            formatted.append(s)

        return {
            "success": True,
            "signals": formatted,
            "count":   len(formatted),
            "filters": {
                "risk_tier":        risk_tier_filter,
                "pair":             pair_filter,
                "min_quality_score": min_quality_score,
            },
        }

    async def get_signal_detail(
        self,
        requesting_manager: Dict[str, Any],
        signal_id: str,
    ) -> Dict[str, Any]:
        """
        Get full signal detail including approval history, comments, adjustments.
        Requires: signal:view (all roles).
        """
        check_hybrid_permission(requesting_manager, "signal:view")
        db = self._get_db()

        review = await db.hybrid_signals.find_one({"signal_id": signal_id})
        if not review:
            return {"success": False, "error": "Signal not found"}

        review.pop("_id", None)
        for ts in ("submitted_at", "final_decision_at", "expires_at"):
            if review.get(ts) and hasattr(review[ts], "isoformat"):
                review[ts] = review[ts].isoformat()

        return {"success": True, "signal": review}

    # ═══════════════════════════════════════════════════════════
    # TEAM COLLABORATION
    # ═══════════════════════════════════════════════════════════

    async def add_comment(
        self,
        requesting_manager: Dict[str, Any],
        signal_id: str,
        comment_text: str,
        comment_type: str = "GENERAL",
        mentions: Optional[List[str]] = None,
        is_private: bool = False,
    ) -> Dict[str, Any]:
        """
        Add a collaboration comment/note to a signal.
        Requires: collab:comment (SUPER_ADMIN, RISK_MANAGER, TRADING_MANAGER, ANALYST, OPERATOR).
        comment_type: GENERAL | ANALYSIS | RISK_NOTE | DECISION | QUESTION
        """
        check_hybrid_permission(requesting_manager, "collab:comment")

        if not comment_text or len(comment_text.strip()) < 3:
            return {"success": False, "error": "Comment must be at least 3 characters"}

        db = self._get_db()
        review = await db.hybrid_signals.find_one({"signal_id": signal_id})
        if not review:
            return {"success": False, "error": "Signal not found"}

        manager_id = requesting_manager["manager_id"]
        role       = requesting_manager["role"]

        comment_record = {
            "comment_id":   str(uuid.uuid4()),
            "manager_id":   manager_id,
            "role":         role,
            "comment_text": comment_text.strip(),
            "comment_type": comment_type.upper(),
            "mentions":     mentions or [],
            "is_private":   is_private,
            "created_at":   datetime.utcnow().isoformat(),
            "edited":       False,
            "reactions":    {},
        }

        await db.hybrid_signals.update_one(
            {"signal_id": signal_id},
            {"$push": {"comments": comment_record}},
        )

        # Also store in dedicated comments collection for cross-signal queries
        await db.hybrid_comments.insert_one({
            **comment_record,
            "signal_id": signal_id,
            "created_at": datetime.utcnow(),
        })

        await self._audit(
            "collab:comment", manager_id, role,
            {"signal_id": signal_id, "comment_type": comment_type, "mentions": mentions},
        )

        return {
            "success":    True,
            "comment_id": comment_record["comment_id"],
            "signal_id":  signal_id,
            "created_at": comment_record["created_at"],
        }

    async def add_note(
        self,
        requesting_manager: Dict[str, Any],
        title: str,
        content: str,
        note_type: str = "GENERAL",
        signal_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Add a standalone team note (not tied to a specific signal).
        Requires: collab:note (SUPER_ADMIN, RISK_MANAGER, TRADING_MANAGER, ANALYST).
        note_type: GENERAL | MARKET_ANALYSIS | RISK_OBSERVATION | STRATEGY | COMPLIANCE
        """
        check_hybrid_permission(requesting_manager, "collab:note")

        if not content or len(content.strip()) < 10:
            return {"success": False, "error": "Note content must be at least 10 characters"}

        db = self._get_db()
        manager_id = requesting_manager["manager_id"]
        role       = requesting_manager["role"]

        note_id = str(uuid.uuid4())
        note_doc = {
            "note_id":    note_id,
            "manager_id": manager_id,
            "role":       role,
            "title":      title.strip(),
            "content":    content.strip(),
            "note_type":  note_type.upper(),
            "signal_id":  signal_id,
            "tags":       tags or [],
            "created_at": datetime.utcnow(),
            "updated_at": None,
            "is_pinned":  False,
            "views":      0,
        }

        await db.hybrid_notes.insert_one(note_doc)

        await self._audit(
            "collab:note", manager_id, role,
            {"note_id": note_id, "note_type": note_type, "signal_id": signal_id},
        )

        return {
            "success":    True,
            "note_id":    note_id,
            "created_at": note_doc["created_at"].isoformat(),
        }

    async def get_team_activity(
        self,
        requesting_manager: Dict[str, Any],
        hours: int = 24,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        Get recent team collaboration activity (comments, notes, decisions).
        Requires: collab:view (all roles).
        """
        check_hybrid_permission(requesting_manager, "collab:view")
        db = self._get_db()

        cutoff = datetime.utcnow() - timedelta(hours=hours)

        # Recent comments
        comments = await (
            db.hybrid_comments
            .find({"created_at": {"$gte": cutoff}})
            .sort("created_at", -1)
            .limit(limit)
            .to_list(length=limit)
        )

        # Recent notes
        notes = await (
            db.hybrid_notes
            .find({"created_at": {"$gte": cutoff}})
            .sort("created_at", -1)
            .limit(50)
            .to_list(length=50)
        )

        # Recent decisions
        decisions = await (
            db.hybrid_signals
            .find({
                "final_decision_at": {"$gte": cutoff},
                "final_decision":    {"$ne": None},
            })
            .sort("final_decision_at", -1)
            .limit(50)
            .to_list(length=50)
        )

        def _fmt(docs):
            result = []
            for d in docs:
                d.pop("_id", None)
                for ts in ("created_at", "updated_at", "final_decision_at"):
                    if d.get(ts) and hasattr(d[ts], "isoformat"):
                        d[ts] = d[ts].isoformat()
                result.append(d)
            return result

        return {
            "success":   True,
            "hours":     hours,
            "comments":  _fmt(comments),
            "notes":     _fmt(notes),
            "decisions": _fmt(decisions),
            "summary": {
                "total_comments":  len(comments),
                "total_notes":     len(notes),
                "total_decisions": len(decisions),
            },
        }

    # ═══════════════════════════════════════════════════════════
    # RISK MANAGEMENT CONTROLS
    # ═══════════════════════════════════════════════════════════

    async def set_risk_limits(
        self,
        requesting_manager: Dict[str, Any],
        limits: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Set or update risk management limits.
        Requires: risk:set_limits (SUPER_ADMIN, RISK_MANAGER).
        """
        check_hybrid_permission(requesting_manager, "risk:set_limits")

        allowed_limit_keys = {
            "max_daily_drawdown_pct",
            "max_weekly_drawdown_pct",
            "max_monthly_drawdown_pct",
            "max_position_size_lots",
            "max_open_positions",
            "max_exposure_per_pair_pct",
            "max_total_exposure_pct",
            "min_rr_ratio",
            "max_lot_size",
            "circuit_breaker_drawdown_pct",
            "auto_halt_on_breach",
        }

        invalid = set(limits.keys()) - allowed_limit_keys
        if invalid:
            return {"success": False, "error": f"Invalid limit keys: {invalid}"}

        db = self._get_db()
        manager_id = requesting_manager["manager_id"]
        role       = requesting_manager["role"]

        await db.hybrid_risk_config.update_one(
            {"config_type": "global_limits"},
            {
                "$set": {
                    **{f"limits.{k}": v for k, v in limits.items()},
                    "updated_at": datetime.utcnow(),
                    "updated_by": manager_id,
                }
            },
            upsert=True,
        )

        await self._audit(
            "risk:set_limits", manager_id, role,
            {"limits_updated": limits},
        )

        await self._create_alert(
            title="⚙️ Risk Limits Updated",
            message=f"Risk limits updated by {manager_id}: {list(limits.keys())}",
            severity="INFO",
            category="RISK",
            created_by=manager_id,
        )

        return {
            "success":        True,
            "limits_updated": limits,
            "updated_by":     manager_id,
            "updated_at":     datetime.utcnow().isoformat(),
        }

    async def get_risk_config(
        self,
        requesting_manager: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Get current risk management configuration.
        Requires: risk:view (all roles).
        """
        check_hybrid_permission(requesting_manager, "risk:view")
        db = self._get_db()

        config = await db.hybrid_risk_config.find_one({"config_type": "global_limits"})
        if not config:
            # Return defaults
            config = {
                "config_type": "global_limits",
                "limits": {
                    "max_daily_drawdown_pct":      3.0,
                    "max_weekly_drawdown_pct":     6.0,
                    "max_monthly_drawdown_pct":    12.0,
                    "max_position_size_lots":      1.0,
                    "max_open_positions":          5,
                    "max_exposure_per_pair_pct":   25.0,
                    "max_total_exposure_pct":      80.0,
                    "min_rr_ratio":                1.5,
                    "max_lot_size":                2.0,
                    "circuit_breaker_drawdown_pct": 5.0,
                    "auto_halt_on_breach":         True,
                },
                "circuit_breaker_active": False,
                "trading_halted":         False,
                "halt_reason":            None,
            }

        config.pop("_id", None)
        if config.get("updated_at") and hasattr(config["updated_at"], "isoformat"):
            config["updated_at"] = config["updated_at"].isoformat()

        return {"success": True, "risk_config": config}

    async def trigger_circuit_breaker(
        self,
        requesting_manager: Dict[str, Any],
        reason: str,
        halt_trading: bool = True,
    ) -> Dict[str, Any]:
        """
        Manually trigger the circuit breaker to halt trading.
        Requires: risk:circuit_breaker (SUPER_ADMIN, RISK_MANAGER).
        """
        check_hybrid_permission(requesting_manager, "risk:circuit_breaker")

        db = self._get_db()
        manager_id = requesting_manager["manager_id"]
        role       = requesting_manager["role"]

        await db.hybrid_risk_config.update_one(
            {"config_type": "global_limits"},
            {
                "$set": {
                    "circuit_breaker_active": True,
                    "trading_halted":         halt_trading,
                    "halt_reason":            reason,
                    "halted_at":              datetime.utcnow(),
                    "halted_by":              manager_id,
                }
            },
            upsert=True,
        )

        await self._create_alert(
            title="🚨 CIRCUIT BREAKER TRIGGERED",
            message=f"Trading circuit breaker activated by {manager_id}. Reason: {reason}",
            severity="CRITICAL",
            category="RISK",
            created_by=manager_id,
        )

        await self._audit(
            "risk:circuit_breaker", manager_id, role,
            {"reason": reason, "halt_trading": halt_trading},
        )

        logger.critical(f"🚨 CIRCUIT BREAKER TRIGGERED by {manager_id}: {reason}")
        return {
            "success":       True,
            "circuit_breaker_active": True,
            "trading_halted": halt_trading,
            "reason":        reason,
            "triggered_by":  manager_id,
            "triggered_at":  datetime.utcnow().isoformat(),
        }

    async def reset_circuit_breaker(
        self,
        requesting_manager: Dict[str, Any],
        reason: str,
    ) -> Dict[str, Any]:
        """
        Reset the circuit breaker and resume trading.
        Requires: risk:circuit_breaker (SUPER_ADMIN, RISK_MANAGER).
        """
        check_hybrid_permission(requesting_manager, "risk:circuit_breaker")

        db = self._get_db()
        manager_id = requesting_manager["manager_id"]
        role       = requesting_manager["role"]

        await db.hybrid_risk_config.update_one(
            {"config_type": "global_limits"},
            {
                "$set": {
                    "circuit_breaker_active": False,
                    "trading_halted":         False,
                    "halt_reason":            None,
                    "reset_at":               datetime.utcnow(),
                    "reset_by":               manager_id,
                    "reset_reason":           reason,
                }
            },
            upsert=True,
        )

        await self._create_alert(
            title="✅ Circuit Breaker Reset",
            message=f"Trading circuit breaker reset by {manager_id}. Reason: {reason}",
            severity="INFO",
            category="RISK",
            created_by=manager_id,
        )

        await self._audit(
            "risk:circuit_breaker_reset", manager_id, role,
            {"reason": reason},
        )

        return {
            "success":       True,
            "circuit_breaker_active": False,
            "trading_halted": False,
            "reset_by":      manager_id,
            "reset_at":      datetime.utcnow().isoformat(),
        }

    # ═══════════════════════════════════════════════════════════
    # PERFORMANCE TRACKING
    # ═══════════════════════════════════════════════════════════

    async def _update_manager_stats(
        self,
        manager_id: str,
        action_type: str,
    ) -> None:
        """Update manager performance statistics after an action."""
        try:
            db = self._get_db()
            field_map = {
                "approval":   "performance_stats.total_approvals",
                "rejection":  "performance_stats.total_rejections",
                "adjustment": "performance_stats.total_adjustments",
            }
            field = field_map.get(action_type)
            if field:
                await db.hybrid_managers.update_one(
                    {"manager_id": manager_id},
                    {
                        "$inc": {field: 1, "performance_stats.signals_reviewed": 1},
                        "$set": {"last_activity": datetime.utcnow()},
                    },
                )
        except Exception as exc:
            logger.error(f"Manager stats update failed: {exc}")

    async def get_manager_performance(
        self,
        requesting_manager: Dict[str, Any],
        target_manager_id: Optional[str] = None,
        days: int = 30,
    ) -> Dict[str, Any]:
        """
        Get detailed performance metrics for a manager or all managers.
        Requires: performance:view (all roles).
        """
        check_hybrid_permission(requesting_manager, "performance:view")
        db = self._get_db()

        cutoff = datetime.utcnow() - timedelta(days=days)

        if target_manager_id:
            managers = await db.hybrid_managers.find(
                {"manager_id": target_manager_id}, {"password_hash": 0}
            ).to_list(1)
        else:
            managers = await db.hybrid_managers.find(
                {"is_active": True}, {"password_hash": 0}
            ).to_list(500)

        results = []
        for mgr in managers:
            mgr_id = mgr["manager_id"]

            # Count decisions in period
            approvals = await db.hybrid_signals.count_documents({
                "approvals.manager_id": mgr_id,
                "approvals.approved_at": {"$gte": cutoff.isoformat()},
            })
            rejections = await db.hybrid_signals.count_documents({
                "rejections.manager_id": mgr_id,
                "rejections.rejected_at": {"$gte": cutoff.isoformat()},
            })
            adjustments = await db.hybrid_signals.count_documents({
                "adjustments.manager_id": mgr_id,
                "adjustments.adjusted_at": {"$gte": cutoff.isoformat()},
            })

            total_decisions = approvals + rejections
            approval_rate = (approvals / total_decisions * 100) if total_decisions > 0 else 0

            results.append({
                "manager_id":    mgr_id,
                "full_name":     mgr.get("full_name", ""),
                "role":          mgr.get("role", ""),
                "department":    mgr.get("department", ""),
                "period_days":   days,
                "approvals":     approvals,
                "rejections":    rejections,
                "adjustments":   adjustments,
                "total_decisions": total_decisions,
                "approval_rate": round(approval_rate, 2),
                "lifetime_stats": mgr.get("performance_stats", {}),
                "last_activity": mgr.get("last_activity", {}).isoformat()
                    if mgr.get("last_activity") and hasattr(mgr.get("last_activity"), "isoformat")
                    else None,
            })

        # Sort by total decisions descending (leaderboard)
        results.sort(key=lambda x: x["total_decisions"], reverse=True)

        return {
            "success":     True,
            "period_days": days,
            "managers":    results,
            "count":       len(results),
        }

    async def get_signal_performance_stats(
        self,
        requesting_manager: Dict[str, Any],
        days: int = 30,
    ) -> Dict[str, Any]:
        """
        Get aggregate signal approval/rejection statistics.
        Requires: performance:view (all roles).
        """
        check_hybrid_permission(requesting_manager, "performance:view")
        db = self._get_db()

        cutoff = datetime.utcnow() - timedelta(days=days)

        total     = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": cutoff}})
        approved  = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": cutoff}, "status": "APPROVED"})
        rejected  = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": cutoff}, "status": "REJECTED"})
        pending   = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": cutoff}, "status": "PENDING_REVIEW"})
        escalated = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": cutoff}, "status": "ESCALATED"})

        # By risk tier
        tier_stats = {}
        for tier in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            tier_total    = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": cutoff}, "risk_tier": tier})
            tier_approved = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": cutoff}, "risk_tier": tier, "status": "APPROVED"})
            tier_stats[tier] = {
                "total":         tier_total,
                "approved":      tier_approved,
                "approval_rate": round(tier_approved / tier_total * 100, 2) if tier_total > 0 else 0,
            }

        approval_rate = round(approved / total * 100, 2) if total > 0 else 0

        return {
            "success":      True,
            "period_days":  days,
            "total":        total,
            "approved":     approved,
            "rejected":     rejected,
            "pending":      pending,
            "escalated":    escalated,
            "approval_rate": approval_rate,
            "by_risk_tier": tier_stats,
        }

    # ═══════════════════════════════════════════════════════════
    # ALERTS MANAGEMENT
    # ═══════════════════════════════════════════════════════════

    async def create_manual_alert(
        self,
        requesting_manager: Dict[str, Any],
        title: str,
        message: str,
        severity: str = "INFO",
        category: str = "GENERAL",
    ) -> Dict[str, Any]:
        """
        Create a manual system alert.
        Requires: alert:create (SUPER_ADMIN, RISK_MANAGER, TRADING_MANAGER, OPERATOR).
        """
        check_hybrid_permission(requesting_manager, "alert:create")

        valid_severities = {"INFO", "WARNING", "CRITICAL"}
        valid_categories = {"GENERAL", "TRADING", "RISK", "SYSTEM", "SECURITY", "COMPLIANCE"}
        severity = severity.upper()
        category = category.upper()

        if severity not in valid_severities:
            return {"success": False, "error": f"Invalid severity. Choose: {valid_severities}"}
        if category not in valid_categories:
            return {"success": False, "error": f"Invalid category. Choose: {valid_categories}"}

        manager_id = requesting_manager["manager_id"]
        alert_id = await self._create_alert(
            title=title,
            message=message,
            severity=severity,
            category=category,
            created_by=manager_id,
        )

        await self._audit(
            "alert:create", manager_id, requesting_manager["role"],
            {"alert_id": alert_id, "severity": severity, "title": title},
        )

        return {
            "success":    True,
            "alert_id":   alert_id,
            "severity":   severity,
            "created_at": datetime.utcnow().isoformat(),
        }

    async def resolve_alert(
        self,
        requesting_manager: Dict[str, Any],
        alert_id: str,
        resolution_note: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Mark an alert as resolved.
        Requires: alert:resolve (SUPER_ADMIN, RISK_MANAGER, TRADING_MANAGER).
        """
        check_hybrid_permission(requesting_manager, "alert:resolve")
        db = self._get_db()

        manager_id = requesting_manager["manager_id"]
        result = await db.hybrid_alerts.update_one(
            {"alert_id": alert_id, "resolved": False},
            {"$set": {
                "resolved":        True,
                "resolved_at":     datetime.utcnow(),
                "resolved_by":     manager_id,
                "resolution_note": resolution_note or "",
            }},
        )
        if result.modified_count == 0:
            return {"success": False, "error": "Alert not found or already resolved"}

        await self._audit(
            "alert:resolve", manager_id, requesting_manager["role"],
            {"alert_id": alert_id, "resolution_note": resolution_note},
        )
        return {"success": True, "message": "Alert resolved", "alert_id": alert_id}

    async def list_alerts(
        self,
        requesting_manager: Dict[str, Any],
        include_resolved: bool = False,
        severity: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """
        List hybrid system alerts.
        Requires: alert:list (all roles).
        """
        check_hybrid_permission(requesting_manager, "alert:list")
        db = self._get_db()

        query: Dict[str, Any] = {}
        if not include_resolved:
            query["resolved"] = False
        if severity:
            query["severity"] = severity.upper()
        if category:
            query["category"] = category.upper()

        alerts = await (
            db.hybrid_alerts
            .find(query)
            .sort("created_at", -1)
            .limit(max(1, min(limit, 200)))
            .to_list(None)
        )

        formatted = []
        for a in alerts:
            a.pop("_id", None)
            for ts in ("created_at", "resolved_at"):
                if a.get(ts) and hasattr(a[ts], "isoformat"):
                    a[ts] = a[ts].isoformat()
            formatted.append(a)

        return {
            "success": True,
            "alerts":  formatted,
            "count":   len(formatted),
        }

    # ═══════════════════════════════════════════════════════════
    # REAL-TIME DASHBOARD
    # ═══════════════════════════════════════════════════════════

    async def get_dashboard(
        self,
        requesting_manager: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Get the real-time monitoring dashboard data.
        Requires: dashboard:view (all roles).
        """
        check_hybrid_permission(requesting_manager, "dashboard:view")
        db = self._get_db()

        now    = datetime.utcnow()
        today  = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week   = now - timedelta(days=7)
        month  = now - timedelta(days=30)

        # Signal counts
        pending_count   = await db.hybrid_signals.count_documents({"status": "PENDING_REVIEW"})
        escalated_count = await db.hybrid_signals.count_documents({"status": "ESCALATED"})
        approved_today  = await db.hybrid_signals.count_documents({"status": "APPROVED", "final_decision_at": {"$gte": today}})
        rejected_today  = await db.hybrid_signals.count_documents({"status": "REJECTED", "final_decision_at": {"$gte": today}})

        # Active alerts
        critical_alerts = await db.hybrid_alerts.count_documents({"resolved": False, "severity": "CRITICAL"})
        warning_alerts  = await db.hybrid_alerts.count_documents({"resolved": False, "severity": "WARNING"})
        info_alerts     = await db.hybrid_alerts.count_documents({"resolved": False, "severity": "INFO"})

        # Risk config
        risk_config = await db.hybrid_risk_config.find_one({"config_type": "global_limits"})
        circuit_breaker_active = risk_config.get("circuit_breaker_active", False) if risk_config else False
        trading_halted         = risk_config.get("trading_halted", False) if risk_config else False

        # Active managers (last 1 hour)
        active_managers = await db.hybrid_managers.count_documents({
            "is_active":     True,
            "last_activity": {"$gte": now - timedelta(hours=1)},
        })

        # Weekly signal stats
        weekly_approved = await db.hybrid_signals.count_documents({"status": "APPROVED", "submitted_at": {"$gte": week}})
        weekly_rejected = await db.hybrid_signals.count_documents({"status": "REJECTED", "submitted_at": {"$gte": week}})
        weekly_total    = weekly_approved + weekly_rejected
        weekly_approval_rate = round(weekly_approved / weekly_total * 100, 2) if weekly_total > 0 else 0

        # Risk tier breakdown (pending)
        tier_breakdown = {}
        for tier in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            tier_breakdown[tier] = await db.hybrid_signals.count_documents({
                "status":    "PENDING_REVIEW",
                "risk_tier": tier,
            })

        # Recent activity (last 5 decisions)
        recent_decisions = await (
            db.hybrid_signals
            .find({"final_decision": {"$ne": None}})
            .sort("final_decision_at", -1)
            .limit(5)
            .to_list(5)
        )
        recent_formatted = []
        for d in recent_decisions:
            d.pop("_id", None)
            if d.get("final_decision_at") and hasattr(d["final_decision_at"], "isoformat"):
                d["final_decision_at"] = d["final_decision_at"].isoformat()
            recent_formatted.append({
                "signal_id":      d.get("signal_id"),
                "decision":       d.get("final_decision"),
                "risk_tier":      d.get("risk_tier"),
                "quality_score":  d.get("quality_score", {}).get("composite_score"),
                "decided_at":     d.get("final_decision_at"),
                "decided_by":     d.get("final_decision_by"),
            })

        return {
            "success":   True,
            "timestamp": now.isoformat(),
            "system_status": {
                "circuit_breaker_active": circuit_breaker_active,
                "trading_halted":         trading_halted,
                "active_managers":        active_managers,
            },
            "signals": {
                "pending":        pending_count,
                "escalated":      escalated_count,
                "approved_today": approved_today,
                "rejected_today": rejected_today,
                "pending_by_tier": tier_breakdown,
            },
            "alerts": {
                "critical": critical_alerts,
                "warning":  warning_alerts,
                "info":     info_alerts,
                "total_active": critical_alerts + warning_alerts + info_alerts,
            },
            "weekly_performance": {
                "approved":      weekly_approved,
                "rejected":      weekly_rejected,
                "total":         weekly_total,
                "approval_rate": weekly_approval_rate,
            },
            "recent_decisions": recent_formatted,
        }

    # ═══════════════════════════════════════════════════════════
    # COMPLIANCE & AUDIT
    # ═══════════════════════════════════════════════════════════

    async def get_audit_log(
        self,
        requesting_manager: Dict[str, Any],
        limit: int = 100,
        manager_id_filter: Optional[str] = None,
        action_filter: Optional[str] = None,
        since_hours: int = 168,
    ) -> Dict[str, Any]:
        """
        Retrieve the full compliance audit trail.
        Requires: audit:view (all roles).
        """
        check_hybrid_permission(requesting_manager, "audit:view")
        db = self._get_db()

        cutoff = datetime.utcnow() - timedelta(hours=since_hours)
        query: Dict[str, Any] = {"timestamp": {"$gte": cutoff}}

        if manager_id_filter:
            query["performed_by"] = manager_id_filter
        if action_filter:
            query["action"] = {"$regex": action_filter, "$options": "i"}

        entries = await (
            db.hybrid_audit_log
            .find(query)
            .sort("timestamp", -1)
            .limit(max(1, min(limit, 1000)))
            .to_list(None)
        )

        formatted = []
        for e in entries:
            e.pop("_id", None)
            if e.get("timestamp") and hasattr(e["timestamp"], "isoformat"):
                e["timestamp"] = e["timestamp"].isoformat()
            formatted.append(e)

        return {
            "success":    True,
            "audit_log":  formatted,
            "count":      len(formatted),
            "since_hours": since_hours,
        }

    async def get_compliance_report(
        self,
        requesting_manager: Dict[str, Any],
        days: int = 30,
    ) -> Dict[str, Any]:
        """
        Generate a compliance report for the specified period.
        Requires: compliance:view (SUPER_ADMIN, RISK_MANAGER, TRADING_MANAGER, ANALYST, VIEWER).
        """
        check_hybrid_permission(requesting_manager, "compliance:view")
        db = self._get_db()

        cutoff = datetime.utcnow() - timedelta(days=days)

        # Audit action counts
        pipeline = [
            {"$match": {"timestamp": {"$gte": cutoff}}},
            {"$group": {"_id": "$action", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
        action_counts_raw = await db.hybrid_audit_log.aggregate(pipeline).to_list(None)
        action_counts = {item["_id"]: item["count"] for item in action_counts_raw}

        # Manager activity
        manager_pipeline = [
            {"$match": {"timestamp": {"$gte": cutoff}}},
            {"$group": {"_id": "$performed_by", "actions": {"$sum": 1}, "role": {"$first": "$role"}}},
            {"$sort": {"actions": -1}},
        ]
        manager_activity = await db.hybrid_audit_log.aggregate(manager_pipeline).to_list(None)

        # Signal decisions
        total_signals   = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": cutoff}})
        approved_signals = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": cutoff}, "status": "APPROVED"})
        rejected_signals = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": cutoff}, "status": "REJECTED"})

        # Risk events
        circuit_breaker_events = await db.hybrid_audit_log.count_documents({
            "timestamp": {"$gte": cutoff},
            "action":    "risk:circuit_breaker",
        })

        return {
            "success":          True,
            "period_days":      days,
            "generated_at":     datetime.utcnow().isoformat(),
            "generated_by":     requesting_manager["manager_id"],
            "signal_decisions": {
                "total":         total_signals,
                "approved":      approved_signals,
                "rejected":      rejected_signals,
                "approval_rate": round(approved_signals / total_signals * 100, 2) if total_signals > 0 else 0,
            },
            "audit_summary": {
                "total_actions":        sum(action_counts.values()),
                "action_breakdown":     action_counts,
                "active_managers":      len(manager_activity),
                "manager_activity":     manager_activity,
            },
            "risk_events": {
                "circuit_breaker_triggers": circuit_breaker_events,
            },
        }


# ─────────────────────────────────────────────────────────────
# SINGLETON INSTANCE
# ─────────────────────────────────────────────────────────────

hybrid_manager = HybridManager()

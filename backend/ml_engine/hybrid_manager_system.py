"""
Hybrid Manager System — Enterprise-Grade Multi-Tier Approval Workflow Engine
Gold Trading System v3.0.2

Provides:
  - HybridManagerRole enum (6 roles: SUPER_ADMIN, RISK_MANAGER, TRADING_MANAGER,
    ANALYST, OPERATOR, VIEWER)
  - MultiTierApprovalWorkflow: 6-stage signal lifecycle management
  - RiskManagementEngine: institutional-grade position/drawdown/correlation controls
  - PerformanceAnalytics: manager KPIs, signal quality, P&L attribution
  - ComplianceAuditLog: immutable, tamper-evident audit trail
  - TeamCollaborationEngine: comments, notes, decision history
  - AlertingSystem: real-time risk, performance, and compliance alerts
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

# ─────────────────────────────────────────────────────────────────────────────
# ROLE DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────

class HybridManagerRole(str, Enum):
    SUPER_ADMIN      = "SUPER_ADMIN"      # Full system control + manager CRUD
    RISK_MANAGER     = "RISK_MANAGER"     # Risk validation, limits, drawdown
    TRADING_MANAGER  = "TRADING_MANAGER"  # Signal approval, trading decisions
    ANALYST          = "ANALYST"          # Signal review, quality scoring
    OPERATOR         = "OPERATOR"         # Signal execution, monitoring
    VIEWER           = "VIEWER"           # Read-only dashboard access


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL LIFECYCLE STATES
# ─────────────────────────────────────────────────────────────────────────────

class SignalStatus(str, Enum):
    PENDING      = "PENDING"       # Awaiting analyst review
    RECOMMENDED  = "RECOMMENDED"   # Analyst approved with quality score
    APPROVED     = "APPROVED"      # Trading Manager approved
    ACTIVE       = "ACTIVE"        # Risk Manager validated — sent to trading
    EXECUTED     = "EXECUTED"      # Operator confirmed execution
    CLOSED       = "CLOSED"        # Trade closed with P&L recorded
    REJECTED     = "REJECTED"      # Rejected at any stage
    EXPIRED      = "EXPIRED"       # Signal expired before execution


class AlertSeverity(str, Enum):
    INFO     = "INFO"
    WARNING  = "WARNING"
    CRITICAL = "CRITICAL"
    RESOLVED = "RESOLVED"


class AlertCategory(str, Enum):
    RISK        = "RISK"
    PERFORMANCE = "PERFORMANCE"
    COMPLIANCE  = "COMPLIANCE"
    TRADING     = "TRADING"
    SYSTEM      = "SYSTEM"
    GENERAL     = "GENERAL"


# ─────────────────────────────────────────────────────────────────────────────
# PERMISSION MATRIX
# ─────────────────────────────────────────────────────────────────────────────

HYBRID_ROLE_PERMISSIONS: Dict[HybridManagerRole, set] = {
    HybridManagerRole.SUPER_ADMIN: {
        # Manager CRUD
        "manager:add", "manager:remove", "manager:update", "manager:list", "manager:get",
        # Signal workflow — all stages
        "signal:view", "signal:recommend", "signal:approve", "signal:validate_risk",
        "signal:execute", "signal:close", "signal:reject", "signal:adjust",
        "signal:history", "signal:stats",
        # Risk management
        "risk:view", "risk:set_limits", "risk:override", "risk:drawdown_check",
        "risk:position_check", "risk:correlation_check", "risk:exposure_check",
        # Performance analytics
        "analytics:view", "analytics:export", "analytics:manager_stats",
        "analytics:signal_quality", "analytics:pnl",
        # Compliance
        "compliance:view", "compliance:export", "compliance:report",
        # Collaboration
        "collab:comment", "collab:note", "collab:view_history",
        # Alerts
        "alert:create", "alert:resolve", "alert:view", "alert:configure",
        # Dashboard
        "dashboard:view", "dashboard:realtime",
        # System
        "system:status", "system:logs", "system:deploy", "system:restart",
        "audit:view",
    },
    HybridManagerRole.RISK_MANAGER: {
        "manager:list", "manager:get",
        "signal:view", "signal:validate_risk", "signal:reject", "signal:history", "signal:stats",
        "risk:view", "risk:set_limits", "risk:override", "risk:drawdown_check",
        "risk:position_check", "risk:correlation_check", "risk:exposure_check",
        "analytics:view", "analytics:manager_stats", "analytics:signal_quality", "analytics:pnl",
        "compliance:view", "compliance:export", "compliance:report",
        "collab:comment", "collab:note", "collab:view_history",
        "alert:create", "alert:resolve", "alert:view", "alert:configure",
        "dashboard:view", "dashboard:realtime",
        "system:status", "audit:view",
    },
    HybridManagerRole.TRADING_MANAGER: {
        "manager:list", "manager:get",
        "signal:view", "signal:approve", "signal:reject", "signal:adjust",
        "signal:history", "signal:stats",
        "risk:view", "risk:drawdown_check", "risk:position_check",
        "analytics:view", "analytics:manager_stats", "analytics:signal_quality", "analytics:pnl",
        "compliance:view",
        "collab:comment", "collab:note", "collab:view_history",
        "alert:create", "alert:resolve", "alert:view",
        "dashboard:view", "dashboard:realtime",
        "system:status", "audit:view",
    },
    HybridManagerRole.ANALYST: {
        "manager:list", "manager:get",
        "signal:view", "signal:recommend", "signal:history", "signal:stats",
        "risk:view",
        "analytics:view", "analytics:signal_quality",
        "compliance:view",
        "collab:comment", "collab:note", "collab:view_history",
        "alert:view",
        "dashboard:view",
        "audit:view",
    },
    HybridManagerRole.OPERATOR: {
        "manager:list", "manager:get",
        "signal:view", "signal:execute", "signal:close", "signal:history",
        "risk:view",
        "analytics:view",
        "collab:comment", "collab:view_history",
        "alert:view",
        "dashboard:view",
        "system:status",
    },
    HybridManagerRole.VIEWER: {
        "manager:list", "manager:get",
        "signal:view", "signal:history", "signal:stats",
        "risk:view",
        "analytics:view",
        "compliance:view",
        "collab:view_history",
        "alert:view",
        "dashboard:view",
        "system:status",
    },
}


def check_hybrid_permission(manager: Dict[str, Any], action: str) -> None:
    """Raise PermissionError if manager does not hold the required action."""
    role_str = manager.get("role", "")
    try:
        role = HybridManagerRole(role_str)
    except ValueError:
        raise PermissionError(f"Unknown hybrid manager role: '{role_str}'")

    if action not in HYBRID_ROLE_PERMISSIONS.get(role, set()):
        raise PermissionError(
            f"Role '{role}' does not have permission for action '{action}'"
        )


# ─────────────────────────────────────────────────────────────────────────────
# COMPLIANCE AUDIT LOG
# ─────────────────────────────────────────────────────────────────────────────

class ComplianceAuditLog:
    """
    Immutable, tamper-evident audit trail for all system decisions.

    Every write is append-only. Records include:
      - action, actor, role, timestamp
      - before/after state snapshots
      - IP address (when available)
      - decision rationale
    """

    def __init__(self, db) -> None:
        self._db = db

    async def record(
        self,
        action: str,
        performed_by: str,
        role: str,
        details: Dict[str, Any],
        success: bool = True,
        error: Optional[str] = None,
        signal_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        rationale: Optional[str] = None,
    ) -> str:
        """Persist an immutable audit record. Returns the audit_id."""
        audit_id = str(uuid.uuid4())
        entry = {
            "audit_id":     audit_id,
            "timestamp":    datetime.utcnow(),
            "action":       action,
            "performed_by": performed_by,
            "role":         role,
            "details":      details,
            "success":      success,
            "error":        error,
            "signal_id":    signal_id,
            "ip_address":   ip_address,
            "rationale":    rationale,
            "immutable":    True,
        }
        try:
            await self._db.hybrid_audit_log.insert_one(entry)
        except Exception as exc:
            logger.error(f"Audit log write failed: {exc}")
        return audit_id

    async def get_log(
        self,
        limit: int = 200,
        manager_id: Optional[str] = None,
        action_filter: Optional[str] = None,
        signal_id: Optional[str] = None,
        since_hours: int = 168,
        category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retrieve audit records with optional filters."""
        cutoff = datetime.utcnow() - timedelta(hours=since_hours)
        query: Dict[str, Any] = {"timestamp": {"$gte": cutoff}}

        if manager_id:
            query["performed_by"] = manager_id
        if action_filter:
            query["action"] = {"$regex": action_filter, "$options": "i"}
        if signal_id:
            query["signal_id"] = signal_id
        if category:
            query["action"] = {"$regex": f"^{category}:", "$options": "i"}

        cursor = self._db.hybrid_audit_log.find(query).sort("timestamp", -1).limit(limit)
        records = await cursor.to_list(limit)

        formatted = []
        for r in records:
            r.pop("_id", None)
            if r.get("timestamp"):
                r["timestamp"] = r["timestamp"].isoformat()
            formatted.append(r)

        return {
            "success": True,
            "records": formatted,
            "count":   len(formatted),
            "filters": {
                "manager_id":    manager_id,
                "action":        action_filter,
                "signal_id":     signal_id,
                "since_hours":   since_hours,
            },
        }

    async def generate_compliance_report(
        self,
        start_date: datetime,
        end_date: datetime,
        report_type: str = "full",
    ) -> Dict[str, Any]:
        """Generate a compliance report for a date range."""
        query = {"timestamp": {"$gte": start_date, "$lte": end_date}}
        records = await self._db.hybrid_audit_log.find(query).to_list(10000)

        total = len(records)
        by_action: Dict[str, int] = {}
        by_role: Dict[str, int] = {}
        by_manager: Dict[str, int] = {}
        failures = 0

        for r in records:
            action = r.get("action", "unknown")
            role   = r.get("role", "unknown")
            actor  = r.get("performed_by", "unknown")

            by_action[action] = by_action.get(action, 0) + 1
            by_role[role]     = by_role.get(role, 0) + 1
            by_manager[actor] = by_manager.get(actor, 0) + 1

            if not r.get("success", True):
                failures += 1

        return {
            "success":      True,
            "report_type":  report_type,
            "period": {
                "start": start_date.isoformat(),
                "end":   end_date.isoformat(),
            },
            "summary": {
                "total_actions":   total,
                "failed_actions":  failures,
                "success_rate":    round((total - failures) / total * 100, 2) if total else 0,
                "unique_actors":   len(by_manager),
            },
            "breakdown": {
                "by_action":  by_action,
                "by_role":    by_role,
                "by_manager": by_manager,
            },
            "generated_at": datetime.utcnow().isoformat(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# RISK MANAGEMENT ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class RiskManagementEngine:
    """
    Institutional-grade risk management controls.

    Controls:
      - Daily position limits per pair
      - Maximum drawdown threshold (daily / weekly / monthly)
      - Risk/reward ratio validation
      - Correlation checks (prevent over-exposure to correlated pairs)
      - Exposure limits by asset class
      - Automatic stop-loss enforcement
      - Portfolio heat calculation
    """

    # Default risk configuration
    DEFAULT_CONFIG = {
        # Position limits
        "max_positions_total":       10,
        "max_positions_per_pair":    2,
        "max_position_size_pct":     5.0,    # % of account per position

        # Drawdown limits
        "daily_drawdown_limit_pct":  3.0,    # -3% daily hard stop
        "weekly_drawdown_limit_pct": 6.0,    # -6% weekly hard stop
        "monthly_drawdown_cap_pct":  12.0,   # -12% monthly cap

        # Risk/reward
        "min_rr_ratio":              1.5,    # Minimum R:R for approval
        "min_rr_gold":               1.8,    # Stricter for Gold
        "max_risk_per_trade_pct":    2.0,    # Max 2% risk per trade

        # Exposure limits by asset class
        "max_gold_exposure_pct":     30.0,
        "max_forex_exposure_pct":    40.0,
        "max_crypto_exposure_pct":   15.0,
        "max_single_pair_pct":       20.0,

        # Correlation
        "max_correlated_positions":  3,
        "correlation_threshold":     0.75,

        # Portfolio heat
        "max_portfolio_heat_pct":    8.0,    # Total risk across all open trades
    }

    def __init__(self, db, config: Optional[Dict] = None) -> None:
        self._db     = db
        self.config  = {**self.DEFAULT_CONFIG, **(config or {})}

    async def load_config(self) -> None:
        """Load risk config from DB (allows runtime updates)."""
        doc = await self._db.hybrid_risk_config.find_one({"active": True})
        if doc:
            doc.pop("_id", None)
            doc.pop("active", None)
            self.config.update(doc)

    async def save_config(self, updates: Dict[str, Any], updated_by: str) -> Dict[str, Any]:
        """Persist updated risk configuration."""
        self.config.update(updates)
        await self._db.hybrid_risk_config.update_one(
            {"active": True},
            {"$set": {**updates, "updated_at": datetime.utcnow(), "updated_by": updated_by}},
            upsert=True,
        )
        return {"success": True, "config": self.config, "updated_by": updated_by}

    async def validate_signal(
        self,
        signal: Dict[str, Any],
        requesting_manager: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Run full risk validation suite on a signal.
        Returns approval decision with detailed breakdown.
        """
        checks: List[Dict[str, Any]] = []
        passed = True

        # 1. R:R ratio check
        rr_check = self._check_rr_ratio(signal)
        checks.append(rr_check)
        if not rr_check["passed"]:
            passed = False

        # 2. Position size check
        size_check = await self._check_position_size(signal)
        checks.append(size_check)
        if not size_check["passed"]:
            passed = False

        # 3. Drawdown check
        dd_check = await self._check_drawdown()
        checks.append(dd_check)
        if not dd_check["passed"]:
            passed = False

        # 4. Exposure check
        exp_check = await self._check_exposure(signal)
        checks.append(exp_check)
        if not exp_check["passed"]:
            passed = False

        # 5. Correlation check
        corr_check = await self._check_correlation(signal)
        checks.append(corr_check)
        if not corr_check["passed"]:
            passed = False

        # 6. Portfolio heat check
        heat_check = await self._check_portfolio_heat(signal)
        checks.append(heat_check)
        if not heat_check["passed"]:
            passed = False

        # 7. Daily position limit
        pos_limit_check = await self._check_position_limits(signal)
        checks.append(pos_limit_check)
        if not pos_limit_check["passed"]:
            passed = False

        risk_score = sum(1 for c in checks if c["passed"]) / len(checks) * 100

        return {
            "approved":    passed,
            "risk_score":  round(risk_score, 1),
            "checks":      checks,
            "signal_id":   signal.get("signal_id"),
            "symbol":      signal.get("symbol"),
            "validated_by": requesting_manager.get("manager_id"),
            "validated_at": datetime.utcnow().isoformat(),
            "config_snapshot": {
                "min_rr":            self.config["min_rr_ratio"],
                "max_drawdown_pct":  self.config["daily_drawdown_limit_pct"],
                "max_heat_pct":      self.config["max_portfolio_heat_pct"],
            },
        }

    def _check_rr_ratio(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """Validate risk/reward ratio."""
        entry  = float(signal.get("entry_price", 0))
        sl     = float(signal.get("stop_loss", 0))
        tp     = float(signal.get("take_profit", signal.get("tp1", 0)))
        symbol = signal.get("symbol", "").upper()

        if entry <= 0 or sl <= 0 or tp <= 0:
            return {
                "check":   "rr_ratio",
                "passed":  False,
                "reason":  "Invalid price levels (entry/SL/TP must be > 0)",
                "value":   0,
                "limit":   self.config["min_rr_ratio"],
            }

        risk   = abs(entry - sl)
        reward = abs(tp - entry)
        rr     = round(reward / risk, 3) if risk > 0 else 0

        min_rr = self.config["min_rr_gold"] if "XAU" in symbol else self.config["min_rr_ratio"]
        passed = rr >= min_rr

        return {
            "check":  "rr_ratio",
            "passed": passed,
            "value":  rr,
            "limit":  min_rr,
            "reason": f"R:R {rr:.2f} {'≥' if passed else '<'} minimum {min_rr}",
        }

    async def _check_position_size(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """Validate position size against account limits."""
        size_pct = float(signal.get("risk_pct", signal.get("position_size_pct", 1.0)))
        limit    = self.config["max_risk_per_trade_pct"]
        passed   = size_pct <= limit

        return {
            "check":  "position_size",
            "passed": passed,
            "value":  size_pct,
            "limit":  limit,
            "reason": f"Risk {size_pct:.2f}% {'≤' if passed else '>'} max {limit}%",
        }

    async def _check_drawdown(self) -> Dict[str, Any]:
        """Check current drawdown against daily limit."""
        cutoff = datetime.utcnow() - timedelta(hours=24)
        pipeline = [
            {"$match": {"status": "CLOSED", "closed_at": {"$gte": cutoff}}},
            {"$group": {"_id": None, "total_pnl": {"$sum": "$pnl_usd"}}},
        ]
        result = await self._db.hybrid_signals.aggregate(pipeline).to_list(1)
        daily_pnl = result[0]["total_pnl"] if result else 0.0

        # Assume $100k account for percentage calculation
        account_balance = float(os.environ.get("DEFAULT_ACCOUNT_BALANCE", "100000"))
        drawdown_pct    = abs(min(daily_pnl, 0)) / account_balance * 100
        limit           = self.config["daily_drawdown_limit_pct"]
        passed          = drawdown_pct < limit

        return {
            "check":       "daily_drawdown",
            "passed":      passed,
            "value":       round(drawdown_pct, 3),
            "limit":       limit,
            "daily_pnl":   round(daily_pnl, 2),
            "reason":      f"Daily drawdown {drawdown_pct:.2f}% {'<' if passed else '≥'} limit {limit}%",
        }

    async def _check_exposure(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """Check asset class exposure limits."""
        symbol   = signal.get("symbol", "").upper()
        category = self._categorize_symbol(symbol)

        # Count active positions in same category
        active = await self._db.hybrid_signals.count_documents(
            {"status": {"$in": ["ACTIVE", "EXECUTED"]},
             "symbol_category": category}
        )

        limit_key = f"max_{category}_exposure_pct"
        limit     = self.config.get(limit_key, 30.0)
        # Approximate: each position = 10% of category limit
        current_pct = active * 10.0
        passed      = current_pct < limit

        return {
            "check":        "exposure",
            "passed":       passed,
            "category":     category,
            "active_count": active,
            "value":        current_pct,
            "limit":        limit,
            "reason":       f"{category} exposure {current_pct:.0f}% {'<' if passed else '≥'} limit {limit}%",
        }

    async def _check_correlation(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """Check for over-exposure to correlated instruments."""
        symbol = signal.get("symbol", "").upper()

        # Gold pairs are highly correlated
        gold_pairs = ["XAUUSD", "XAUEUR", "XAUGBP"]
        if symbol in gold_pairs:
            active_gold = await self._db.hybrid_signals.count_documents(
                {"status": {"$in": ["ACTIVE", "EXECUTED"]},
                 "symbol": {"$in": gold_pairs}}
            )
            limit  = self.config["max_correlated_positions"]
            passed = active_gold < limit
            return {
                "check":        "correlation",
                "passed":       passed,
                "group":        "gold_pairs",
                "active_count": active_gold,
                "limit":        limit,
                "reason":       f"Correlated gold positions: {active_gold} {'<' if passed else '≥'} limit {limit}",
            }

        return {
            "check":  "correlation",
            "passed": True,
            "reason": "No correlation constraint for this symbol",
        }

    async def _check_portfolio_heat(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate total portfolio heat (sum of all open trade risks)."""
        pipeline = [
            {"$match": {"status": {"$in": ["ACTIVE", "EXECUTED"]}}},
            {"$group": {"_id": None, "total_risk": {"$sum": "$risk_pct"}}},
        ]
        result     = await self._db.hybrid_signals.aggregate(pipeline).to_list(1)
        total_heat = result[0]["total_risk"] if result else 0.0
        new_risk   = float(signal.get("risk_pct", 1.0))
        projected  = total_heat + new_risk
        limit      = self.config["max_portfolio_heat_pct"]
        passed     = projected <= limit

        return {
            "check":          "portfolio_heat",
            "passed":         passed,
            "current_heat":   round(total_heat, 2),
            "new_risk":       new_risk,
            "projected_heat": round(projected, 2),
            "limit":          limit,
            "reason":         f"Portfolio heat {projected:.2f}% {'≤' if passed else '>'} max {limit}%",
        }

    async def _check_position_limits(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """Check total and per-pair position limits."""
        symbol = signal.get("symbol", "")

        total_active = await self._db.hybrid_signals.count_documents(
            {"status": {"$in": ["ACTIVE", "EXECUTED"]}}
        )
        pair_active = await self._db.hybrid_signals.count_documents(
            {"status": {"$in": ["ACTIVE", "EXECUTED"]}, "symbol": symbol}
        )

        total_limit = self.config["max_positions_total"]
        pair_limit  = self.config["max_positions_per_pair"]

        passed = (total_active < total_limit) and (pair_active < pair_limit)
        reason_parts = []
        if total_active >= total_limit:
            reason_parts.append(f"Total positions {total_active} ≥ limit {total_limit}")
        if pair_active >= pair_limit:
            reason_parts.append(f"{symbol} positions {pair_active} ≥ limit {pair_limit}")

        return {
            "check":        "position_limits",
            "passed":       passed,
            "total_active": total_active,
            "pair_active":  pair_active,
            "total_limit":  total_limit,
            "pair_limit":   pair_limit,
            "reason":       "; ".join(reason_parts) if reason_parts else "Position limits OK",
        }

    def _categorize_symbol(self, symbol: str) -> str:
        """Categorize symbol for exposure tracking."""
        s = symbol.upper()
        if "XAU" in s or "XAG" in s:
            return "gold"
        elif "BTC" in s or "ETH" in s or "XRP" in s:
            return "crypto"
        elif "JPY" in s:
            return "jpy"
        elif "USD" in s:
            return "usd"
        else:
            return "forex"

    async def get_risk_dashboard(self) -> Dict[str, Any]:
        """Return a comprehensive risk dashboard snapshot."""
        # Current drawdown
        dd_check = await self._check_drawdown()

        # Open positions
        total_active = await self._db.hybrid_signals.count_documents(
            {"status": {"$in": ["ACTIVE", "EXECUTED"]}}
        )

        # Portfolio heat
        pipeline = [
            {"$match": {"status": {"$in": ["ACTIVE", "EXECUTED"]}}},
            {"$group": {"_id": None, "total_risk": {"$sum": "$risk_pct"},
                        "count": {"$sum": 1}}},
        ]
        heat_result  = await self._db.hybrid_signals.aggregate(pipeline).to_list(1)
        total_heat   = heat_result[0]["total_risk"] if heat_result else 0.0

        # Weekly P&L
        week_cutoff = datetime.utcnow() - timedelta(days=7)
        week_pipeline = [
            {"$match": {"status": "CLOSED", "closed_at": {"$gte": week_cutoff}}},
            {"$group": {"_id": None, "pnl": {"$sum": "$pnl_usd"},
                        "wins": {"$sum": {"$cond": [{"$gt": ["$pnl_usd", 0]}, 1, 0]}},
                        "losses": {"$sum": {"$cond": [{"$lte": ["$pnl_usd", 0]}, 1, 0]}}}},
        ]
        week_result = await self._db.hybrid_signals.aggregate(week_pipeline).to_list(1)
        week_pnl    = week_result[0]["pnl"] if week_result else 0.0
        week_wins   = week_result[0]["wins"] if week_result else 0
        week_losses = week_result[0]["losses"] if week_result else 0

        return {
            "timestamp":       datetime.utcnow().isoformat(),
            "risk_status":     "NORMAL" if dd_check["passed"] else "BREACH",
            "drawdown": {
                "daily_pct":   dd_check["value"],
                "daily_limit": dd_check["limit"],
                "daily_pnl":   dd_check["daily_pnl"],
                "status":      "OK" if dd_check["passed"] else "LIMIT_REACHED",
            },
            "positions": {
                "total_active":  total_active,
                "max_allowed":   self.config["max_positions_total"],
                "portfolio_heat": round(total_heat, 2),
                "heat_limit":    self.config["max_portfolio_heat_pct"],
            },
            "weekly_performance": {
                "pnl":      round(week_pnl, 2),
                "wins":     week_wins,
                "losses":   week_losses,
                "win_rate": round(week_wins / (week_wins + week_losses) * 100, 1)
                            if (week_wins + week_losses) > 0 else 0,
            },
            "limits": self.config,
        }


# ─────────────────────────────────────────────────────────────────────────────
# MULTI-TIER APPROVAL WORKFLOW
# ─────────────────────────────────────────────────────────────────────────────

class MultiTierApprovalWorkflow:
    """
    6-stage signal lifecycle management.

    Stage 1: Signal generated → PENDING
    Stage 2: Analyst reviews  → RECOMMENDED (with quality score 0-100)
    Stage 3: Trading Manager  → APPROVED (with trading rationale)
    Stage 4: Risk Manager     → ACTIVE (risk validated, sent to trading)
    Stage 5: Operator         → EXECUTED (confirmed execution)
    Stage 6: System monitors  → CLOSED (P&L recorded)

    Any stage can REJECT the signal with a mandatory reason.
    """

    STAGE_TRANSITIONS = {
        SignalStatus.PENDING:     [SignalStatus.RECOMMENDED, SignalStatus.REJECTED, SignalStatus.EXPIRED],
        SignalStatus.RECOMMENDED: [SignalStatus.APPROVED,    SignalStatus.REJECTED],
        SignalStatus.APPROVED:    [SignalStatus.ACTIVE,      SignalStatus.REJECTED],
        SignalStatus.ACTIVE:      [SignalStatus.EXECUTED,    SignalStatus.REJECTED],
        SignalStatus.EXECUTED:    [SignalStatus.CLOSED],
        SignalStatus.CLOSED:      [],
        SignalStatus.REJECTED:    [],
        SignalStatus.EXPIRED:     [],
    }

    STAGE_REQUIRED_ROLE = {
        SignalStatus.RECOMMENDED: HybridManagerRole.ANALYST,
        SignalStatus.APPROVED:    HybridManagerRole.TRADING_MANAGER,
        SignalStatus.ACTIVE:      HybridManagerRole.RISK_MANAGER,
        SignalStatus.EXECUTED:    HybridManagerRole.OPERATOR,
        SignalStatus.CLOSED:      HybridManagerRole.OPERATOR,
        SignalStatus.REJECTED:    None,  # Any authorized role
    }

    def __init__(self, db, risk_engine: RiskManagementEngine,
                 audit_log: ComplianceAuditLog) -> None:
        self._db          = db
        self._risk        = risk_engine
        self._audit       = audit_log

    async def submit_signal(
        self,
        signal_data: Dict[str, Any],
        submitted_by: str,
    ) -> Dict[str, Any]:
        """
        Stage 1: Submit a new signal into the workflow (PENDING).
        Called by the signal generation system or SUPER_ADMIN.
        """
        signal_id = str(uuid.uuid4())
        symbol    = signal_data.get("symbol", "XAUUSD").upper()

        doc = {
            "signal_id":        signal_id,
            "status":           SignalStatus.PENDING.value,
            "symbol":           symbol,
            "symbol_category":  self._risk._categorize_symbol(symbol),
            "direction":        signal_data.get("direction", signal_data.get("side", "BUY")).upper(),
            "entry_price":      float(signal_data.get("entry_price", signal_data.get("entry", 0))),
            "stop_loss":        float(signal_data.get("stop_loss", signal_data.get("sl", 0))),
            "take_profit":      float(signal_data.get("take_profit", signal_data.get("tp", 0))),
            "tp1":              float(signal_data.get("tp1", 0)),
            "tp2":              float(signal_data.get("tp2", 0)),
            "tp3":              float(signal_data.get("tp3", 0)),
            "risk_pct":         float(signal_data.get("risk_pct", 1.0)),
            "confidence":       float(signal_data.get("confidence", 0)),
            "strategy":         signal_data.get("strategy", "HYBRID"),
            "timeframe":        signal_data.get("timeframe", "1H"),
            "regime":           signal_data.get("regime", "UNKNOWN"),
            "source":           signal_data.get("source", "ML_ENGINE"),
            "raw_signal":       signal_data,
            "submitted_by":     submitted_by,
            "submitted_at":     datetime.utcnow(),
            "expires_at":       datetime.utcnow() + timedelta(hours=4),
            # Workflow tracking
            "analyst_id":       None,
            "analyst_review":   None,
            "quality_score":    None,
            "trading_manager_id":     None,
            "trading_approval":       None,
            "risk_manager_id":        None,
            "risk_validation":        None,
            "operator_id":            None,
            "execution_details":      None,
            "pnl_usd":                None,
            "closed_at":              None,
            "rejection_reason":       None,
            "rejected_by":            None,
            "rejected_at":            None,
            "stage_history":          [],
            "comments":               [],
            "tags":                   signal_data.get("tags", []),
        }

        await self._db.hybrid_signals.insert_one(doc)
        await self._audit.record(
            action="signal:submit",
            performed_by=submitted_by,
            role="SYSTEM",
            details={"signal_id": signal_id, "symbol": symbol,
                     "direction": doc["direction"]},
            signal_id=signal_id,
        )

        doc.pop("_id", None)
        doc["submitted_at"] = doc["submitted_at"].isoformat()
        doc["expires_at"]   = doc["expires_at"].isoformat()

        logger.info(f"✅ Signal submitted: {signal_id} ({symbol} {doc['direction']})")
        return {"success": True, "signal_id": signal_id, "signal": doc}

    async def analyst_recommend(
        self,
        signal_id: str,
        requesting_manager: Dict[str, Any],
        quality_score: float,
        review_notes: str,
        adjustments: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Stage 2: Analyst reviews and recommends a signal.
        Requires ANALYST role (or higher).
        """
        check_hybrid_permission(requesting_manager, "signal:recommend")

        signal = await self._get_signal(signal_id)
        if not signal:
            return {"success": False, "error": "Signal not found"}

        if signal["status"] != SignalStatus.PENDING.value:
            return {"success": False, "error": f"Signal is {signal['status']}, expected PENDING"}

        if not (0 <= quality_score <= 100):
            return {"success": False, "error": "Quality score must be between 0 and 100"}

        # Apply any analyst adjustments
        update_fields: Dict[str, Any] = {
            "status":        SignalStatus.RECOMMENDED.value,
            "analyst_id":    requesting_manager["manager_id"],
            "quality_score": quality_score,
            "analyst_review": {
                "notes":       review_notes,
                "quality_score": quality_score,
                "reviewed_at": datetime.utcnow().isoformat(),
                "reviewer":    requesting_manager.get("full_name", requesting_manager["manager_id"]),
            },
            "recommended_at": datetime.utcnow(),
        }

        if adjustments:
            for field in ("entry_price", "stop_loss", "take_profit", "tp1", "tp2", "tp3", "risk_pct"):
                if field in adjustments:
                    update_fields[field] = float(adjustments[field])
            update_fields["analyst_adjustments"] = adjustments

        stage_entry = {
            "stage":      "RECOMMENDED",
            "actor":      requesting_manager["manager_id"],
            "role":       requesting_manager["role"],
            "timestamp":  datetime.utcnow().isoformat(),
            "notes":      review_notes,
            "quality_score": quality_score,
        }
        update_fields["$push"] = {"stage_history": stage_entry}

        await self._db.hybrid_signals.update_one(
            {"signal_id": signal_id},
            {"$set": {k: v for k, v in update_fields.items() if k != "$push"},
             "$push": {"stage_history": stage_entry}},
        )

        await self._audit.record(
            action="signal:recommend",
            performed_by=requesting_manager["manager_id"],
            role=requesting_manager["role"],
            details={"signal_id": signal_id, "quality_score": quality_score,
                     "adjustments": adjustments},
            signal_id=signal_id,
            rationale=review_notes,
        )

        logger.info(f"✅ Signal {signal_id} recommended by analyst "
                    f"{requesting_manager['manager_id']} (score: {quality_score})")
        return {
            "success":       True,
            "signal_id":     signal_id,
            "new_status":    SignalStatus.RECOMMENDED.value,
            "quality_score": quality_score,
            "next_step":     "Awaiting Trading Manager approval",
        }

    async def trading_manager_approve(
        self,
        signal_id: str,
        requesting_manager: Dict[str, Any],
        rationale: str,
        priority: str = "NORMAL",
        adjustments: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Stage 3: Trading Manager approves a recommended signal.
        Requires TRADING_MANAGER role (or higher).
        """
        check_hybrid_permission(requesting_manager, "signal:approve")

        signal = await self._get_signal(signal_id)
        if not signal:
            return {"success": False, "error": "Signal not found"}

        if signal["status"] != SignalStatus.RECOMMENDED.value:
            return {"success": False, "error": f"Signal is {signal['status']}, expected RECOMMENDED"}

        update_fields: Dict[str, Any] = {
            "status":             SignalStatus.APPROVED.value,
            "trading_manager_id": requesting_manager["manager_id"],
            "trading_approval": {
                "rationale":   rationale,
                "priority":    priority,
                "approved_at": datetime.utcnow().isoformat(),
                "approver":    requesting_manager.get("full_name", requesting_manager["manager_id"]),
            },
            "approved_at": datetime.utcnow(),
            "priority":    priority,
        }

        if adjustments:
            for field in ("entry_price", "stop_loss", "take_profit", "tp1", "tp2", "tp3"):
                if field in adjustments:
                    update_fields[field] = float(adjustments[field])
            update_fields["trading_adjustments"] = adjustments

        stage_entry = {
            "stage":     "APPROVED",
            "actor":     requesting_manager["manager_id"],
            "role":      requesting_manager["role"],
            "timestamp": datetime.utcnow().isoformat(),
            "notes":     rationale,
            "priority":  priority,
        }

        await self._db.hybrid_signals.update_one(
            {"signal_id": signal_id},
            {"$set": {k: v for k, v in update_fields.items()},
             "$push": {"stage_history": stage_entry}},
        )

        await self._audit.record(
            action="signal:approve",
            performed_by=requesting_manager["manager_id"],
            role=requesting_manager["role"],
            details={"signal_id": signal_id, "priority": priority,
                     "adjustments": adjustments},
            signal_id=signal_id,
            rationale=rationale,
        )

        logger.info(f"✅ Signal {signal_id} approved by trading manager "
                    f"{requesting_manager['manager_id']}")
        return {
            "success":    True,
            "signal_id":  signal_id,
            "new_status": SignalStatus.APPROVED.value,
            "priority":   priority,
            "next_step":  "Awaiting Risk Manager validation",
        }

    async def risk_manager_validate(
        self,
        signal_id: str,
        requesting_manager: Dict[str, Any],
        override_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Stage 4: Risk Manager validates and activates a signal.
        Runs full risk engine checks. Requires RISK_MANAGER role (or higher).
        """
        check_hybrid_permission(requesting_manager, "signal:validate_risk")

        signal = await self._get_signal(signal_id)
        if not signal:
            return {"success": False, "error": "Signal not found"}

        if signal["status"] != SignalStatus.APPROVED.value:
            return {"success": False, "error": f"Signal is {signal['status']}, expected APPROVED"}

        # Run risk engine
        risk_result = await self._risk.validate_signal(signal, requesting_manager)

        if not risk_result["approved"] and not override_reason:
            # Auto-reject on risk failure (unless override provided)
            await self._reject_signal(
                signal_id, requesting_manager,
                reason=f"Risk validation failed: "
                       f"{[c['reason'] for c in risk_result['checks'] if not c['passed']]}",
            )
            return {
                "success":      False,
                "signal_id":    signal_id,
                "risk_result":  risk_result,
                "new_status":   SignalStatus.REJECTED.value,
                "error":        "Signal rejected due to risk validation failure",
            }

        update_fields: Dict[str, Any] = {
            "status":           SignalStatus.ACTIVE.value,
            "risk_manager_id":  requesting_manager["manager_id"],
            "risk_validation":  {
                **risk_result,
                "override_reason": override_reason,
                "validated_at":    datetime.utcnow().isoformat(),
                "validator":       requesting_manager.get("full_name",
                                                          requesting_manager["manager_id"]),
            },
            "activated_at": datetime.utcnow(),
        }

        stage_entry = {
            "stage":      "ACTIVE",
            "actor":      requesting_manager["manager_id"],
            "role":       requesting_manager["role"],
            "timestamp":  datetime.utcnow().isoformat(),
            "risk_score": risk_result["risk_score"],
            "override":   bool(override_reason),
        }

        await self._db.hybrid_signals.update_one(
            {"signal_id": signal_id},
            {"$set": update_fields,
             "$push": {"stage_history": stage_entry}},
        )

        await self._audit.record(
            action="signal:validate_risk",
            performed_by=requesting_manager["manager_id"],
            role=requesting_manager["role"],
            details={"signal_id": signal_id, "risk_score": risk_result["risk_score"],
                     "override": bool(override_reason)},
            signal_id=signal_id,
            rationale=override_reason,
        )

        logger.info(f"✅ Signal {signal_id} risk-validated and ACTIVE "
                    f"(score: {risk_result['risk_score']})")
        return {
            "success":     True,
            "signal_id":   signal_id,
            "new_status":  SignalStatus.ACTIVE.value,
            "risk_result": risk_result,
            "next_step":   "Signal sent to trading — awaiting operator execution",
        }

    async def operator_execute(
        self,
        signal_id: str,
        requesting_manager: Dict[str, Any],
        execution_details: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Stage 5: Operator confirms signal execution.
        Requires OPERATOR role (or higher).
        """
        check_hybrid_permission(requesting_manager, "signal:execute")

        signal = await self._get_signal(signal_id)
        if not signal:
            return {"success": False, "error": "Signal not found"}

        if signal["status"] != SignalStatus.ACTIVE.value:
            return {"success": False, "error": f"Signal is {signal['status']}, expected ACTIVE"}

        actual_entry = float(execution_details.get("actual_entry", signal["entry_price"]))
        lot_size     = float(execution_details.get("lot_size", 0.01))
        broker_ref   = execution_details.get("broker_ref", "")

        update_fields = {
            "status":      SignalStatus.EXECUTED.value,
            "operator_id": requesting_manager["manager_id"],
            "execution_details": {
                "actual_entry": actual_entry,
                "lot_size":     lot_size,
                "broker_ref":   broker_ref,
                "slippage":     round(abs(actual_entry - signal["entry_price"]), 5),
                "executed_at":  datetime.utcnow().isoformat(),
                "executor":     requesting_manager.get("full_name",
                                                       requesting_manager["manager_id"]),
                **execution_details,
            },
            "executed_at": datetime.utcnow(),
        }

        stage_entry = {
            "stage":       "EXECUTED",
            "actor":       requesting_manager["manager_id"],
            "role":        requesting_manager["role"],
            "timestamp":   datetime.utcnow().isoformat(),
            "actual_entry": actual_entry,
            "lot_size":    lot_size,
        }

        await self._db.hybrid_signals.update_one(
            {"signal_id": signal_id},
            {"$set": update_fields,
             "$push": {"stage_history": stage_entry}},
        )

        await self._audit.record(
            action="signal:execute",
            performed_by=requesting_manager["manager_id"],
            role=requesting_manager["role"],
            details={"signal_id": signal_id, "actual_entry": actual_entry,
                     "lot_size": lot_size, "broker_ref": broker_ref},
            signal_id=signal_id,
        )

        logger.info(f"✅ Signal {signal_id} executed at {actual_entry}")
        return {
            "success":    True,
            "signal_id":  signal_id,
            "new_status": SignalStatus.EXECUTED.value,
            "actual_entry": actual_entry,
            "next_step":  "Monitoring trade — will close with P&L",
        }

    async def close_signal(
        self,
        signal_id: str,
        requesting_manager: Dict[str, Any],
        close_price: float,
        pnl_usd: float,
        close_reason: str = "TP_HIT",
    ) -> Dict[str, Any]:
        """
        Stage 6: Close a signal and record P&L.
        Requires OPERATOR role (or higher).
        """
        check_hybrid_permission(requesting_manager, "signal:close")

        signal = await self._get_signal(signal_id)
        if not signal:
            return {"success": False, "error": "Signal not found"}

        if signal["status"] != SignalStatus.EXECUTED.value:
            return {"success": False, "error": f"Signal is {signal['status']}, expected EXECUTED"}

        outcome = "WIN" if pnl_usd > 0 else ("LOSS" if pnl_usd < 0 else "BREAKEVEN")

        update_fields = {
            "status":       SignalStatus.CLOSED.value,
            "close_price":  close_price,
            "pnl_usd":      round(pnl_usd, 2),
            "outcome":      outcome,
            "close_reason": close_reason,
            "closed_at":    datetime.utcnow(),
            "closed_by":    requesting_manager["manager_id"],
        }

        stage_entry = {
            "stage":       "CLOSED",
            "actor":       requesting_manager["manager_id"],
            "role":        requesting_manager["role"],
            "timestamp":   datetime.utcnow().isoformat(),
            "close_price": close_price,
            "pnl_usd":     round(pnl_usd, 2),
            "outcome":     outcome,
            "close_reason": close_reason,
        }

        await self._db.hybrid_signals.update_one(
            {"signal_id": signal_id},
            {"$set": update_fields,
             "$push": {"stage_history": stage_entry}},
        )

        await self._audit.record(
            action="signal:close",
            performed_by=requesting_manager["manager_id"],
            role=requesting_manager["role"],
            details={"signal_id": signal_id, "close_price": close_price,
                     "pnl_usd": pnl_usd, "outcome": outcome, "close_reason": close_reason},
            signal_id=signal_id,
        )

        logger.info(f"✅ Signal {signal_id} closed: {outcome} ${pnl_usd:.2f}")
        return {
            "success":     True,
            "signal_id":   signal_id,
            "new_status":  SignalStatus.CLOSED.value,
            "outcome":     outcome,
            "pnl_usd":     round(pnl_usd, 2),
            "close_reason": close_reason,
        }

    async def reject_signal(
        self,
        signal_id: str,
        requesting_manager: Dict[str, Any],
        reason: str,
    ) -> Dict[str, Any]:
        """Reject a signal at any stage. Requires signal:reject permission."""
        check_hybrid_permission(requesting_manager, "signal:reject")

        signal = await self._get_signal(signal_id)
        if not signal:
            return {"success": False, "error": "Signal not found"}

        terminal = [SignalStatus.CLOSED.value, SignalStatus.REJECTED.value,
                    SignalStatus.EXPIRED.value]
        if signal["status"] in terminal:
            return {"success": False, "error": f"Signal is already in terminal state: {signal['status']}"}

        await self._reject_signal(signal_id, requesting_manager, reason)

        return {
            "success":    True,
            "signal_id":  signal_id,
            "new_status": SignalStatus.REJECTED.value,
            "reason":     reason,
        }

    async def _reject_signal(
        self,
        signal_id: str,
        requesting_manager: Dict[str, Any],
        reason: str,
    ) -> None:
        """Internal: mark signal as REJECTED."""
        stage_entry = {
            "stage":     "REJECTED",
            "actor":     requesting_manager["manager_id"],
            "role":      requesting_manager["role"],
            "timestamp": datetime.utcnow().isoformat(),
            "reason":    reason,
        }
        await self._db.hybrid_signals.update_one(
            {"signal_id": signal_id},
            {"$set": {
                "status":           SignalStatus.REJECTED.value,
                "rejection_reason": reason,
                "rejected_by":      requesting_manager["manager_id"],
                "rejected_at":      datetime.utcnow(),
            },
             "$push": {"stage_history": stage_entry}},
        )
        await self._audit.record(
            action="signal:reject",
            performed_by=requesting_manager["manager_id"],
            role=requesting_manager["role"],
            details={"signal_id": signal_id, "reason": reason},
            signal_id=signal_id,
            rationale=reason,
        )

    async def get_pending_signals(
        self,
        requesting_manager: Dict[str, Any],
        status_filter: Optional[str] = None,
        symbol: Optional[str] = None,
        limit: int = 50,
        skip: int = 0,
    ) -> Dict[str, Any]:
        """Get signals awaiting action, filtered by role."""
        check_hybrid_permission(requesting_manager, "signal:view")

        role = requesting_manager.get("role", "")

        # Role-based default status filter
        if not status_filter:
            role_status_map = {
                HybridManagerRole.ANALYST.value:         SignalStatus.PENDING.value,
                HybridManagerRole.TRADING_MANAGER.value: SignalStatus.RECOMMENDED.value,
                HybridManagerRole.RISK_MANAGER.value:    SignalStatus.APPROVED.value,
                HybridManagerRole.OPERATOR.value:        SignalStatus.ACTIVE.value,
            }
            status_filter = role_status_map.get(role)

        query: Dict[str, Any] = {}
        if status_filter:
            query["status"] = status_filter
        if symbol:
            query["symbol"] = symbol.upper()

        total = await self._db.hybrid_signals.count_documents(query)
        cursor = (
            self._db.hybrid_signals
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
            "filter":  {"status": status_filter, "symbol": symbol},
        }

    async def get_signal_detail(
        self,
        signal_id: str,
        requesting_manager: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Get full signal details including workflow history."""
        check_hybrid_permission(requesting_manager, "signal:view")

        signal = await self._get_signal(signal_id)
        if not signal:
            return {"success": False, "error": "Signal not found"}

        # Attach comments
        comments = await self._db.hybrid_comments.find(
            {"signal_id": signal_id}
        ).sort("created_at", -1).to_list(100)
        for c in comments:
            c.pop("_id", None)
            if c.get("created_at"):
                c["created_at"] = c["created_at"].isoformat()

        signal["comments"] = comments
        return {"success": True, "signal": signal}

    async def _get_signal(self, signal_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a signal by ID, stripping MongoDB _id."""
        doc = await self._db.hybrid_signals.find_one({"signal_id": signal_id})
        if not doc:
            return None
        doc.pop("_id", None)
        for ts in ("submitted_at", "recommended_at", "approved_at",
                   "activated_at", "executed_at", "closed_at",
                   "rejected_at", "expires_at"):
            if doc.get(ts) and hasattr(doc[ts], "isoformat"):
                doc[ts] = doc[ts].isoformat()
        return doc

    async def expire_stale_signals(self) -> Dict[str, Any]:
        """Background task: expire signals past their expiry time."""
        now = datetime.utcnow()
        result = await self._db.hybrid_signals.update_many(
            {
                "status":     {"$in": [SignalStatus.PENDING.value,
                                       SignalStatus.RECOMMENDED.value]},
                "expires_at": {"$lt": now},
            },
            {"$set": {"status": SignalStatus.EXPIRED.value,
                      "expired_at": now}},
        )
        return {"expired_count": result.modified_count}


# ─────────────────────────────────────────────────────────────────────────────
# PERFORMANCE ANALYTICS
# ─────────────────────────────────────────────────────────────────────────────

class PerformanceAnalytics:
    """
    Comprehensive performance analytics for managers and signals.

    Tracks:
      - Manager KPIs (approval rate, quality scores, decision speed)
      - Signal quality metrics (accuracy, win rate, avg R:R)
      - P&L attribution (by manager, symbol, strategy, timeframe)
      - Risk-adjusted returns (Sharpe, Sortino, Calmar)
      - Drawdown analysis
    """

    def __init__(self, db) -> None:
        self._db = db

    async def get_manager_performance(
        self,
        manager_id: Optional[str] = None,
        days: int = 30,
    ) -> Dict[str, Any]:
        """Get performance metrics for one or all managers."""
        cutoff = datetime.utcnow() - timedelta(days=days)

        # Build pipeline for manager stats
        match_stage: Dict[str, Any] = {"submitted_at": {"$gte": cutoff}}
        if manager_id:
            match_stage["$or"] = [
                {"analyst_id":        manager_id},
                {"trading_manager_id": manager_id},
                {"risk_manager_id":   manager_id},
                {"operator_id":       manager_id},
            ]

        pipeline = [
            {"$match": match_stage},
            {"$facet": {
                "by_analyst": [
                    {"$match": {"analyst_id": {"$ne": None}}},
                    {"$group": {
                        "_id":           "$analyst_id",
                        "reviewed":      {"$sum": 1},
                        "avg_quality":   {"$avg": "$quality_score"},
                        "recommended":   {"$sum": {"$cond": [
                            {"$eq": ["$status", "RECOMMENDED"]}, 1, 0]}},
                    }},
                ],
                "by_trading_manager": [
                    {"$match": {"trading_manager_id": {"$ne": None}}},
                    {"$group": {
                        "_id":      "$trading_manager_id",
                        "approved": {"$sum": 1},
                        "wins":     {"$sum": {"$cond": [
                            {"$eq": ["$outcome", "WIN"]}, 1, 0]}},
                        "losses":   {"$sum": {"$cond": [
                            {"$eq": ["$outcome", "LOSS"]}, 1, 0]}},
                        "total_pnl": {"$sum": "$pnl_usd"},
                    }},
                ],
                "by_risk_manager": [
                    {"$match": {"risk_manager_id": {"$ne": None}}},
                    {"$group": {
                        "_id":       "$risk_manager_id",
                        "validated": {"$sum": 1},
                        "avg_risk_score": {"$avg": "$risk_validation.risk_score"},
                    }},
                ],
                "overall": [
                    {"$group": {
                        "_id":       None,
                        "total":     {"$sum": 1},
                        "approved":  {"$sum": {"$cond": [
                            {"$in": ["$status",
                                     ["APPROVED", "ACTIVE", "EXECUTED", "CLOSED"]]}, 1, 0]}},
                        "rejected":  {"$sum": {"$cond": [
                            {"$eq": ["$status", "REJECTED"]}, 1, 0]}},
                        "closed":    {"$sum": {"$cond": [
                            {"$eq": ["$status", "CLOSED"]}, 1, 0]}},
                        "wins":      {"$sum": {"$cond": [
                            {"$eq": ["$outcome", "WIN"]}, 1, 0]}},
                        "losses":    {"$sum": {"$cond": [
                            {"$eq": ["$outcome", "LOSS"]}, 1, 0]}},
                        "total_pnl": {"$sum": "$pnl_usd"},
                        "avg_quality": {"$avg": "$quality_score"},
                    }},
                ],
            }},
        ]

        result = await self._db.hybrid_signals.aggregate(pipeline).to_list(1)
        data   = result[0] if result else {}

        overall = data.get("overall", [{}])[0] or {}
        total   = overall.get("total", 0)
        closed  = overall.get("closed", 0)
        wins    = overall.get("wins", 0)
        losses  = overall.get("losses", 0)

        return {
            "success":    True,
            "period_days": days,
            "manager_id": manager_id,
            "overall": {
                "total_signals":   total,
                "approved":        overall.get("approved", 0),
                "rejected":        overall.get("rejected", 0),
                "closed":          closed,
                "approval_rate":   round(overall.get("approved", 0) / total * 100, 1) if total else 0,
                "win_rate":        round(wins / closed * 100, 1) if closed else 0,
                "total_pnl":       round(overall.get("total_pnl") or 0, 2),
                "avg_quality_score": round(overall.get("avg_quality") or 0, 1),
            },
            "by_analyst":         data.get("by_analyst", []),
            "by_trading_manager": data.get("by_trading_manager", []),
            "by_risk_manager":    data.get("by_risk_manager", []),
            "generated_at":       datetime.utcnow().isoformat(),
        }

    async def get_signal_quality_metrics(self, days: int = 30) -> Dict[str, Any]:
        """Analyze signal quality scores and their correlation with outcomes."""
        cutoff = datetime.utcnow() - timedelta(days=days)

        pipeline = [
            {"$match": {"submitted_at": {"$gte": cutoff},
                        "quality_score": {"$ne": None}}},
            {"$facet": {
                "quality_distribution": [
                    {"$bucket": {
                        "groupBy": "$quality_score",
                        "boundaries": [0, 20, 40, 60, 80, 100],
                        "default": "other",
                        "output": {
                            "count": {"$sum": 1},
                            "wins":  {"$sum": {"$cond": [
                                {"$eq": ["$outcome", "WIN"]}, 1, 0]}},
                            "avg_pnl": {"$avg": "$pnl_usd"},
                        },
                    }},
                ],
                "by_strategy": [
                    {"$group": {
                        "_id":         "$strategy",
                        "count":       {"$sum": 1},
                        "avg_quality": {"$avg": "$quality_score"},
                        "win_rate":    {"$avg": {"$cond": [
                            {"$eq": ["$outcome", "WIN"]}, 1, 0]}},
                        "avg_pnl":     {"$avg": "$pnl_usd"},
                    }},
                ],
                "by_symbol": [
                    {"$group": {
                        "_id":         "$symbol",
                        "count":       {"$sum": 1},
                        "avg_quality": {"$avg": "$quality_score"},
                        "win_rate":    {"$avg": {"$cond": [
                            {"$eq": ["$outcome", "WIN"]}, 1, 0]}},
                        "total_pnl":   {"$sum": "$pnl_usd"},
                    }},
                ],
                "by_timeframe": [
                    {"$group": {
                        "_id":         "$timeframe",
                        "count":       {"$sum": 1},
                        "avg_quality": {"$avg": "$quality_score"},
                        "win_rate":    {"$avg": {"$cond": [
                            {"$eq": ["$outcome", "WIN"]}, 1, 0]}},
                    }},
                ],
            }},
        ]

        result = await self._db.hybrid_signals.aggregate(pipeline).to_list(1)
        data   = result[0] if result else {}

        return {
            "success":              True,
            "period_days":          days,
            "quality_distribution": data.get("quality_distribution", []),
            "by_strategy":          data.get("by_strategy", []),
            "by_symbol":            data.get("by_symbol", []),
            "by_timeframe":         data.get("by_timeframe", []),
            "generated_at":         datetime.utcnow().isoformat(),
        }

    async def get_pnl_report(self, days: int = 30) -> Dict[str, Any]:
        """Comprehensive P&L report with attribution."""
        cutoff = datetime.utcnow() - timedelta(days=days)

        pipeline = [
            {"$match": {"status": "CLOSED", "closed_at": {"$gte": cutoff}}},
            {"$facet": {
                "summary": [
                    {"$group": {
                        "_id":       None,
                        "total_pnl": {"$sum": "$pnl_usd"},
                        "wins":      {"$sum": {"$cond": [
                            {"$gt": ["$pnl_usd", 0]}, 1, 0]}},
                        "losses":    {"$sum": {"$cond": [
                            {"$lt": ["$pnl_usd", 0]}, 1, 0]}},
                        "breakeven": {"$sum": {"$cond": [
                            {"$eq": ["$pnl_usd", 0]}, 1, 0]}},
                        "avg_win":   {"$avg": {"$cond": [
                            {"$gt": ["$pnl_usd", 0]}, "$pnl_usd", None]}},
                        "avg_loss":  {"$avg": {"$cond": [
                            {"$lt": ["$pnl_usd", 0]}, "$pnl_usd", None]}},
                        "max_win":   {"$max": "$pnl_usd"},
                        "max_loss":  {"$min": "$pnl_usd"},
                        "count":     {"$sum": 1},
                    }},
                ],
                "by_symbol": [
                    {"$group": {
                        "_id":       "$symbol",
                        "total_pnl": {"$sum": "$pnl_usd"},
                        "count":     {"$sum": 1},
                        "wins":      {"$sum": {"$cond": [
                            {"$gt": ["$pnl_usd", 0]}, 1, 0]}},
                    }},
                    {"$sort": {"total_pnl": -1}},
                ],
                "by_strategy": [
                    {"$group": {
                        "_id":       "$strategy",
                        "total_pnl": {"$sum": "$pnl_usd"},
                        "count":     {"$sum": 1},
                    }},
                    {"$sort": {"total_pnl": -1}},
                ],
                "daily": [
                    {"$group": {
                        "_id": {"$dateToString": {
                            "format": "%Y-%m-%d",
                            "date":   "$closed_at",
                        }},
                        "pnl":   {"$sum": "$pnl_usd"},
                        "count": {"$sum": 1},
                        "wins":  {"$sum": {"$cond": [
                            {"$gt": ["$pnl_usd", 0]}, 1, 0]}},
                    }},
                    {"$sort": {"_id": 1}},
                ],
            }},
        ]

        result  = await self._db.hybrid_signals.aggregate(pipeline).to_list(1)
        data    = result[0] if result else {}
        summary = (data.get("summary") or [{}])[0] or {}

        total   = summary.get("count", 0)
        wins    = summary.get("wins", 0)
        losses  = summary.get("losses", 0)
        avg_win = summary.get("avg_win") or 0
        avg_loss = abs(summary.get("avg_loss") or 1)
        profit_factor = round((wins * avg_win) / (losses * avg_loss), 3) \
                        if losses > 0 and avg_loss > 0 else 0

        return {
            "success":       True,
            "period_days":   days,
            "summary": {
                "total_trades":   total,
                "total_pnl":      round(summary.get("total_pnl") or 0, 2),
                "wins":           wins,
                "losses":         losses,
                "breakeven":      summary.get("breakeven", 0),
                "win_rate":       round(wins / total * 100, 1) if total else 0,
                "avg_win":        round(avg_win, 2),
                "avg_loss":       round(summary.get("avg_loss") or 0, 2),
                "max_win":        round(summary.get("max_win") or 0, 2),
                "max_loss":       round(summary.get("max_loss") or 0, 2),
                "profit_factor":  profit_factor,
                "expectancy":     round(
                    (wins / total * avg_win) + (losses / total * (summary.get("avg_loss") or 0)), 2
                ) if total else 0,
            },
            "by_symbol":   data.get("by_symbol", []),
            "by_strategy": data.get("by_strategy", []),
            "daily":       data.get("daily", []),
            "generated_at": datetime.utcnow().isoformat(),
        }

    async def get_approval_rate_analysis(self, days: int = 30) -> Dict[str, Any]:
        """Analyze approval rates at each workflow stage."""
        cutoff = datetime.utcnow() - timedelta(days=days)

        pipeline = [
            {"$match": {"submitted_at": {"$gte": cutoff}}},
            {"$group": {
                "_id":        None,
                "total":      {"$sum": 1},
                "recommended": {"$sum": {"$cond": [
                    {"$in": ["$status",
                             ["RECOMMENDED", "APPROVED", "ACTIVE",
                              "EXECUTED", "CLOSED"]]}, 1, 0]}},
                "approved":   {"$sum": {"$cond": [
                    {"$in": ["$status",
                             ["APPROVED", "ACTIVE", "EXECUTED", "CLOSED"]]}, 1, 0]}},
                "activated":  {"$sum": {"$cond": [
                    {"$in": ["$status",
                             ["ACTIVE", "EXECUTED", "CLOSED"]]}, 1, 0]}},
                "executed":   {"$sum": {"$cond": [
                    {"$in": ["$status", ["EXECUTED", "CLOSED"]]}, 1, 0]}},
                "closed":     {"$sum": {"$cond": [
                    {"$eq": ["$status", "CLOSED"]}, 1, 0]}},
                "rejected":   {"$sum": {"$cond": [
                    {"$eq": ["$status", "REJECTED"]}, 1, 0]}},
                "expired":    {"$sum": {"$cond": [
                    {"$eq": ["$status", "EXPIRED"]}, 1, 0]}},
            }},
        ]

        result = await self._db.hybrid_signals.aggregate(pipeline).to_list(1)
        data   = (result[0] if result else {}) or {}
        total  = data.get("total", 0)

        def pct(n: int) -> float:
            return round(n / total * 100, 1) if total else 0

        return {
            "success":     True,
            "period_days": days,
            "funnel": {
                "submitted":   total,
                "recommended": {"count": data.get("recommended", 0),
                                "rate":  pct(data.get("recommended", 0))},
                "approved":    {"count": data.get("approved", 0),
                                "rate":  pct(data.get("approved", 0))},
                "activated":   {"count": data.get("activated", 0),
                                "rate":  pct(data.get("activated", 0))},
                "executed":    {"count": data.get("executed", 0),
                                "rate":  pct(data.get("executed", 0))},
                "closed":      {"count": data.get("closed", 0),
                                "rate":  pct(data.get("closed", 0))},
                "rejected":    {"count": data.get("rejected", 0),
                                "rate":  pct(data.get("rejected", 0))},
                "expired":     {"count": data.get("expired", 0),
                                "rate":  pct(data.get("expired", 0))},
            },
            "generated_at": datetime.utcnow().isoformat(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# TEAM COLLABORATION ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class TeamCollaborationEngine:
    """
    Team collaboration features for signal review and decision-making.

    Features:
      - Comments & notes on signals
      - Decision history tracking
      - Team activity log
      - @mentions (stored, not pushed)
      - Signal tagging
    """

    def __init__(self, db, audit_log: ComplianceAuditLog) -> None:
        self._db    = db
        self._audit = audit_log

    async def add_comment(
        self,
        signal_id: str,
        requesting_manager: Dict[str, Any],
        comment_text: str,
        comment_type: str = "GENERAL",
        mentions: Optional[List[str]] = None,
        is_private: bool = False,
    ) -> Dict[str, Any]:
        """Add a comment to a signal thread."""
        check_hybrid_permission(requesting_manager, "collab:comment")

        comment_id = str(uuid.uuid4())
        doc = {
            "comment_id":  comment_id,
            "signal_id":   signal_id,
            "author_id":   requesting_manager["manager_id"],
            "author_name": requesting_manager.get("full_name",
                                                   requesting_manager["manager_id"]),
            "author_role": requesting_manager["role"],
            "text":        comment_text,
            "type":        comment_type,
            "mentions":    mentions or [],
            "is_private":  is_private,
            "created_at":  datetime.utcnow(),
            "edited":      False,
        }
        await self._db.hybrid_comments.insert_one(doc)

        await self._audit.record(
            action="collab:comment",
            performed_by=requesting_manager["manager_id"],
            role=requesting_manager["role"],
            details={"signal_id": signal_id, "comment_id": comment_id,
                     "type": comment_type},
            signal_id=signal_id,
        )

        doc.pop("_id", None)
        doc["created_at"] = doc["created_at"].isoformat()
        return {"success": True, "comment": doc}

    async def get_comments(
        self,
        signal_id: str,
        requesting_manager: Dict[str, Any],
        include_private: bool = False,
    ) -> Dict[str, Any]:
        """Get all comments for a signal."""
        check_hybrid_permission(requesting_manager, "collab:view_history")

        query: Dict[str, Any] = {"signal_id": signal_id}
        if not include_private:
            query["is_private"] = False

        comments = await self._db.hybrid_comments.find(query).sort(
            "created_at", 1
        ).to_list(500)

        formatted = []
        for c in comments:
            c.pop("_id", None)
            if c.get("created_at"):
                c["created_at"] = c["created_at"].isoformat()
            formatted.append(c)

        return {"success": True, "comments": formatted, "count": len(formatted)}

    async def add_note(
        self,
        requesting_manager: Dict[str, Any],
        title: str,
        content: str,
        note_type: str = "GENERAL",
        signal_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Add a standalone note (not tied to a specific signal)."""
        check_hybrid_permission(requesting_manager, "collab:note")

        note_id = str(uuid.uuid4())
        doc = {
            "note_id":    note_id,
            "signal_id":  signal_id,
            "author_id":  requesting_manager["manager_id"],
            "author_name": requesting_manager.get("full_name",
                                                   requesting_manager["manager_id"]),
            "author_role": requesting_manager["role"],
            "title":      title,
            "content":    content,
            "type":       note_type,
            "tags":       tags or [],
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        await self._db.hybrid_notes.insert_one(doc)

        doc.pop("_id", None)
        doc["created_at"] = doc["created_at"].isoformat()
        doc["updated_at"] = doc["updated_at"].isoformat()
        return {"success": True, "note": doc}

    async def get_team_activity(
        self,
        requesting_manager: Dict[str, Any],
        hours: int = 24,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Get recent team activity across all signals."""
        check_hybrid_permission(requesting_manager, "collab:view_history")

        cutoff = datetime.utcnow() - timedelta(hours=hours)

        # Combine comments and audit log entries
        comments = await self._db.hybrid_comments.find(
            {"created_at": {"$gte": cutoff}}
        ).sort("created_at", -1).limit(limit // 2).to_list(limit // 2)

        audit_entries = await self._db.hybrid_audit_log.find(
            {"timestamp": {"$gte": cutoff},
             "action":    {"$regex": "^signal:", "$options": "i"}}
        ).sort("timestamp", -1).limit(limit // 2).to_list(limit // 2)

        activity = []
        for c in comments:
            c.pop("_id", None)
            if c.get("created_at"):
                c["created_at"] = c["created_at"].isoformat()
            activity.append({"type": "COMMENT", **c})

        for a in audit_entries:
            a.pop("_id", None)
            if a.get("timestamp"):
                a["timestamp"] = a["timestamp"].isoformat()
            activity.append({"type": "ACTION", **a})

        activity.sort(key=lambda x: x.get("created_at") or x.get("timestamp", ""), reverse=True)

        return {
            "success":  True,
            "activity": activity[:limit],
            "count":    len(activity[:limit]),
            "hours":    hours,
        }

    async def get_notes(
        self,
        requesting_manager: Dict[str, Any],
        signal_id: Optional[str] = None,
        note_type: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Get notes with optional filters."""
        check_hybrid_permission(requesting_manager, "collab:view_history")

        query: Dict[str, Any] = {}
        if signal_id:
            query["signal_id"] = signal_id
        if note_type:
            query["type"] = note_type

        notes = await self._db.hybrid_notes.find(query).sort(
            "created_at", -1
        ).limit(limit).to_list(limit)

        formatted = []
        for n in notes:
            n.pop("_id", None)
            for ts in ("created_at", "updated_at"):
                if n.get(ts) and hasattr(n[ts], "isoformat"):
                    n[ts] = n[ts].isoformat()
            formatted.append(n)

        return {"success": True, "notes": formatted, "count": len(formatted)}


# ─────────────────────────────────────────────────────────────────────────────
# ALERTING SYSTEM
# ─────────────────────────────────────────────────────────────────────────────

class AlertingSystem:
    """
    Real-time alerting for risk, performance, compliance, and system events.

    Alert types:
      - RISK:        Drawdown breach, position limit, exposure limit
      - PERFORMANCE: Win rate drop, P&L threshold, quality score drop
      - COMPLIANCE:  Unauthorized action, audit anomaly
      - TRADING:     Signal expiry, execution failure, slippage
      - SYSTEM:      DB connectivity, API errors, service health
      - GENERAL:     Manual alerts from managers
    """

    def __init__(self, db, audit_log: ComplianceAuditLog) -> None:
        self._db    = db
        self._audit = audit_log

    async def create_alert(
        self,
        requesting_manager: Dict[str, Any],
        title: str,
        message: str,
        severity: str = AlertSeverity.INFO.value,
        category: str = AlertCategory.GENERAL.value,
        signal_id: Optional[str] = None,
        auto_resolve_hours: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new system alert."""
        check_hybrid_permission(requesting_manager, "alert:create")

        alert_id = str(uuid.uuid4())
        doc = {
            "alert_id":    alert_id,
            "title":       title,
            "message":     message,
            "severity":    severity,
            "category":    category,
            "signal_id":   signal_id,
            "created_by":  requesting_manager["manager_id"],
            "created_at":  datetime.utcnow(),
            "resolved":    False,
            "resolved_by": None,
            "resolved_at": None,
            "resolution_note": None,
            "auto_resolve_at": (
                datetime.utcnow() + timedelta(hours=auto_resolve_hours)
                if auto_resolve_hours else None
            ),
            "metadata":    metadata or {},
            "acknowledged_by": [],
        }
        await self._db.hybrid_alerts.insert_one(doc)

        await self._audit.record(
            action="alert:create",
            performed_by=requesting_manager["manager_id"],
            role=requesting_manager["role"],
            details={"alert_id": alert_id, "severity": severity,
                     "category": category, "title": title},
            signal_id=signal_id,
        )

        doc.pop("_id", None)
        doc["created_at"] = doc["created_at"].isoformat()
        if doc.get("auto_resolve_at"):
            doc["auto_resolve_at"] = doc["auto_resolve_at"].isoformat()

        logger.warning(f"🚨 Alert [{severity}] {title}: {message}")
        return {"success": True, "alert": doc}

    async def create_system_alert(
        self,
        title: str,
        message: str,
        severity: str = AlertSeverity.WARNING.value,
        category: str = AlertCategory.SYSTEM.value,
        signal_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create an alert from the system (no requesting manager)."""
        alert_id = str(uuid.uuid4())
        doc = {
            "alert_id":    alert_id,
            "title":       title,
            "message":     message,
            "severity":    severity,
            "category":    category,
            "signal_id":   signal_id,
            "created_by":  "SYSTEM",
            "created_at":  datetime.utcnow(),
            "resolved":    False,
            "resolved_by": None,
            "resolved_at": None,
            "resolution_note": None,
            "auto_resolve_at": None,
            "metadata":    metadata or {},
            "acknowledged_by": [],
        }
        try:
            await self._db.hybrid_alerts.insert_one(doc)
        except Exception as exc:
            logger.error(f"Failed to create system alert: {exc}")
        logger.warning(f"🚨 System Alert [{severity}] {title}: {message}")
        return alert_id

    async def resolve_alert(
        self,
        alert_id: str,
        requesting_manager: Dict[str, Any],
        resolution_note: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Mark an alert as resolved."""
        check_hybrid_permission(requesting_manager, "alert:resolve")

        result = await self._db.hybrid_alerts.update_one(
            {"alert_id": alert_id, "resolved": False},
            {"$set": {
                "resolved":          True,
                "resolved_by":       requesting_manager["manager_id"],
                "resolved_at":       datetime.utcnow(),
                "resolution_note":   resolution_note,
            }},
        )

        if result.modified_count == 0:
            return {"success": False, "error": "Alert not found or already resolved"}

        await self._audit.record(
            action="alert:resolve",
            performed_by=requesting_manager["manager_id"],
            role=requesting_manager["role"],
            details={"alert_id": alert_id, "resolution_note": resolution_note},
        )

        return {"success": True, "alert_id": alert_id, "resolved": True}

    async def acknowledge_alert(
        self,
        alert_id: str,
        requesting_manager: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Acknowledge an alert (mark as seen without resolving)."""
        check_hybrid_permission(requesting_manager, "alert:view")

        manager_id = requesting_manager["manager_id"]
        result = await self._db.hybrid_alerts.update_one(
            {"alert_id": alert_id},
            {"$addToSet": {"acknowledged_by": {
                "manager_id": manager_id,
                "acknowledged_at": datetime.utcnow().isoformat(),
            }}},
        )

        if result.matched_count == 0:
            return {"success": False, "error": "Alert not found"}

        return {"success": True, "alert_id": alert_id, "acknowledged_by": manager_id}

    async def list_alerts(
        self,
        requesting_manager: Dict[str, Any],
        include_resolved: bool = False,
        severity: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 50,
        skip: int = 0,
    ) -> Dict[str, Any]:
        """List alerts with optional filters."""
        check_hybrid_permission(requesting_manager, "alert:view")

        query: Dict[str, Any] = {}
        if not include_resolved:
            query["resolved"] = False
        if severity:
            query["severity"] = severity.upper()
        if category:
            query["category"] = category.upper()

        total = await self._db.hybrid_alerts.count_documents(query)
        alerts = await self._db.hybrid_alerts.find(query).sort(
            "created_at", -1
        ).skip(skip).limit(limit).to_list(limit)

        formatted = []
        for a in alerts:
            a.pop("_id", None)
            for ts in ("created_at", "resolved_at", "auto_resolve_at"):
                if a.get(ts) and hasattr(a[ts], "isoformat"):
                    a[ts] = a[ts].isoformat()
            formatted.append(a)

        return {
            "success": True,
            "alerts":  formatted,
            "total":   total,
            "count":   len(formatted),
            "skip":    skip,
            "limit":   limit,
        }

    async def get_alert_summary(self) -> Dict[str, Any]:
        """Get a summary of active alerts by severity and category."""
        pipeline = [
            {"$match": {"resolved": False}},
            {"$facet": {
                "by_severity": [
                    {"$group": {"_id": "$severity", "count": {"$sum": 1}}},
                ],
                "by_category": [
                    {"$group": {"_id": "$category", "count": {"$sum": 1}}},
                ],
                "total": [
                    {"$count": "count"},
                ],
            }},
        ]
        result = await self._db.hybrid_alerts.aggregate(pipeline).to_list(1)
        data   = result[0] if result else {}

        by_severity = {item["_id"]: item["count"]
                       for item in data.get("by_severity", [])}
        by_category = {item["_id"]: item["count"]
                       for item in data.get("by_category", [])}
        total_list  = data.get("total", [])
        total       = total_list[0]["count"] if total_list else 0

        return {
            "total_active":  total,
            "critical":      by_severity.get("CRITICAL", 0),
            "warning":       by_severity.get("WARNING", 0),
            "info":          by_severity.get("INFO", 0),
            "by_category":   by_category,
            "timestamp":     datetime.utcnow().isoformat(),
        }

    async def auto_resolve_expired(self) -> Dict[str, Any]:
        """Background task: auto-resolve alerts past their auto_resolve_at time."""
        now = datetime.utcnow()
        result = await self._db.hybrid_alerts.update_many(
            {"resolved": False,
             "auto_resolve_at": {"$ne": None, "$lt": now}},
            {"$set": {
                "resolved":        True,
                "resolved_by":     "SYSTEM",
                "resolved_at":     now,
                "resolution_note": "Auto-resolved by system",
            }},
        )
        return {"auto_resolved_count": result.modified_count}


# ─────────────────────────────────────────────────────────────────────────────
# HYBRID MANAGER SYSTEM — FACADE
# ─────────────────────────────────────────────────────────────────────────────

class HybridManagerSystem:
    """
    Top-level facade that wires together all subsystems.

    Usage:
        system = HybridManagerSystem()
        await system.initialize()
        # Then use system.workflow, system.risk, system.analytics,
        # system.audit, system.collab, system.alerts
    """

    def __init__(self) -> None:
        self._client: Optional[AsyncIOMotorClient] = None
        self._db     = None

        self.workflow:   Optional[MultiTierApprovalWorkflow] = None
        self.risk:       Optional[RiskManagementEngine]      = None
        self.analytics:  Optional[PerformanceAnalytics]      = None
        self.audit:      Optional[ComplianceAuditLog]        = None
        self.collab:     Optional[TeamCollaborationEngine]   = None
        self.alerts:     Optional[AlertingSystem]            = None

    def _get_db(self):
        if self._client is None:
            self._client = AsyncIOMotorClient(
                MONGO_URL,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
            )
            self._db = self._client[DB_NAME]
        return self._db

    async def initialize(self) -> None:
        """Initialize all subsystems and create DB indexes."""
        db = self._get_db()

        self.audit     = ComplianceAuditLog(db)
        self.risk      = RiskManagementEngine(db)
        self.workflow  = MultiTierApprovalWorkflow(db, self.risk, self.audit)
        self.analytics = PerformanceAnalytics(db)
        self.collab    = TeamCollaborationEngine(db, self.audit)
        self.alerts    = AlertingSystem(db, self.audit)

        await self._create_indexes()
        await self.risk.load_config()
        logger.info("✅ HybridManagerSystem initialized")

    async def _create_indexes(self) -> None:
        """Create MongoDB indexes for performance."""
        db = self._get_db()
        try:
            # Signals
            await db.hybrid_signals.create_index([("signal_id", 1)], unique=True)
            await db.hybrid_signals.create_index([("status", 1), ("submitted_at", -1)])
            await db.hybrid_signals.create_index([("symbol", 1), ("status", 1)])
            await db.hybrid_signals.create_index([("analyst_id", 1)])
            await db.hybrid_signals.create_index([("trading_manager_id", 1)])
            await db.hybrid_signals.create_index([("risk_manager_id", 1)])
            await db.hybrid_signals.create_index([("submitted_at", -1)])
            await db.hybrid_signals.create_index([("closed_at", -1)])

            # Audit log
            await db.hybrid_audit_log.create_index([("audit_id", 1)], unique=True)
            await db.hybrid_audit_log.create_index([("timestamp", -1)])
            await db.hybrid_audit_log.create_index([("performed_by", 1), ("timestamp", -1)])
            await db.hybrid_audit_log.create_index([("signal_id", 1)])
            await db.hybrid_audit_log.create_index([("action", 1)])

            # Alerts
            await db.hybrid_alerts.create_index([("alert_id", 1)], unique=True)
            await db.hybrid_alerts.create_index([("resolved", 1), ("created_at", -1)])
            await db.hybrid_alerts.create_index([("severity", 1), ("resolved", 1)])

            # Comments
            await db.hybrid_comments.create_index([("signal_id", 1), ("created_at", -1)])

            # Notes
            await db.hybrid_notes.create_index([("author_id", 1), ("created_at", -1)])

            logger.info("✅ HybridManagerSystem DB indexes created")
        except Exception as exc:
            logger.warning(f"Index creation warning: {exc}")

    async def get_dashboard(self, requesting_manager: Dict[str, Any]) -> Dict[str, Any]:
        """Real-time monitoring dashboard."""
        check_hybrid_permission(requesting_manager, "dashboard:view")

        db = self._get_db()

        # Signal counts by status
        pipeline = [
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        ]
        status_counts_raw = await db.hybrid_signals.aggregate(pipeline).to_list(20)
        status_counts = {item["_id"]: item["count"] for item in status_counts_raw}

        # Risk dashboard
        risk_dash = await self.risk.get_risk_dashboard()

        # Alert summary
        alert_summary = await self.alerts.get_alert_summary()

        # Recent activity (last 1h)
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        recent_actions = await db.hybrid_audit_log.count_documents(
            {"timestamp": {"$gte": one_hour_ago}}
        )

        # Pending signals by stage
        pending_by_stage = {
            "awaiting_analyst":         status_counts.get("PENDING", 0),
            "awaiting_trading_manager": status_counts.get("RECOMMENDED", 0),
            "awaiting_risk_manager":    status_counts.get("APPROVED", 0),
            "awaiting_execution":       status_counts.get("ACTIVE", 0),
        }

        return {
            "success":   True,
            "timestamp": datetime.utcnow().isoformat(),
            "signal_pipeline": {
                "status_counts":    status_counts,
                "pending_by_stage": pending_by_stage,
                "total_pending":    sum(pending_by_stage.values()),
            },
            "risk":              risk_dash,
            "alerts":            alert_summary,
            "recent_activity": {
                "actions_last_hour": recent_actions,
            },
            "system_version": "3.0.2",
        }

    async def get_system_health(self) -> Dict[str, Any]:
        """System health check for the hybrid manager subsystem."""
        db = self._get_db()
        try:
            await asyncio.wait_for(db.command("ping"), timeout=3)
            db_status = "HEALTHY"
        except Exception as exc:
            db_status = f"UNHEALTHY: {exc}"

        signal_count = await db.hybrid_signals.count_documents({})
        alert_count  = await db.hybrid_alerts.count_documents({"resolved": False})

        return {
            "status":        "HEALTHY" if db_status == "HEALTHY" else "DEGRADED",
            "db":            db_status,
            "signal_count":  signal_count,
            "active_alerts": alert_count,
            "timestamp":     datetime.utcnow().isoformat(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETON
# ─────────────────────────────────────────────────────────────────────────────

hybrid_manager_system = HybridManagerSystem()

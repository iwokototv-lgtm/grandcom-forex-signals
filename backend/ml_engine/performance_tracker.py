"""
Professional Performance Tracker
Enterprise-Grade Manager & Signal Performance Analytics
Gold Trading System v3.0.2

Provides:
  - PerformanceTracker class with:
      * Manager performance metrics & leaderboards
      * Signal quality scoring & tracking
      * Win rate tracking & trend analysis
      * Profit factor calculation
      * Sharpe ratio & Sortino ratio
      * Monthly/weekly performance reports
      * Drawdown analysis
      * Streak tracking (win/loss streaks)
      * Benchmark comparison
  - Leaderboard generation
  - Trend analysis
  - Export-ready report generation
"""

from __future__ import annotations

import logging
import os
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME   = os.environ.get("DB_NAME",   "gold_signals_v3")

# Risk-free rate for Sharpe ratio (annualised, e.g. 5% = 0.05)
RISK_FREE_RATE = float(os.environ.get("RISK_FREE_RATE", "0.05"))


# ─────────────────────────────────────────────────────────────
# STATISTICAL HELPERS
# ─────────────────────────────────────────────────────────────

def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safe division returning default when denominator is zero."""
    return numerator / denominator if denominator != 0 else default


def _sharpe_ratio(
    returns: List[float],
    risk_free_rate: float = RISK_FREE_RATE,
    periods_per_year: int = 252,
) -> float:
    """
    Calculate annualised Sharpe ratio.

    Args:
        returns: List of period returns (as decimals, e.g. 0.01 = 1%)
        risk_free_rate: Annual risk-free rate
        periods_per_year: Trading periods per year (252 for daily)

    Returns:
        Annualised Sharpe ratio
    """
    if len(returns) < 2:
        return 0.0
    arr = np.array(returns, dtype=float)
    excess_returns = arr - (risk_free_rate / periods_per_year)
    std = np.std(excess_returns, ddof=1)
    if std == 0:
        return 0.0
    return float(np.mean(excess_returns) / std * np.sqrt(periods_per_year))


def _sortino_ratio(
    returns: List[float],
    risk_free_rate: float = RISK_FREE_RATE,
    periods_per_year: int = 252,
) -> float:
    """
    Calculate annualised Sortino ratio (uses downside deviation).

    Args:
        returns: List of period returns
        risk_free_rate: Annual risk-free rate
        periods_per_year: Trading periods per year

    Returns:
        Annualised Sortino ratio
    """
    if len(returns) < 2:
        return 0.0
    arr = np.array(returns, dtype=float)
    target = risk_free_rate / periods_per_year
    downside = arr[arr < target] - target
    downside_std = np.sqrt(np.mean(downside ** 2)) if len(downside) > 0 else 0.0
    if downside_std == 0:
        return 0.0
    excess_return = np.mean(arr) - target
    return float(excess_return / downside_std * np.sqrt(periods_per_year))


def _max_drawdown(equity_curve: List[float]) -> Tuple[float, int, int]:
    """
    Calculate maximum drawdown from an equity curve.

    Returns:
        (max_drawdown_pct, peak_index, trough_index)
    """
    if len(equity_curve) < 2:
        return 0.0, 0, 0

    arr = np.array(equity_curve, dtype=float)
    peak_idx   = 0
    trough_idx = 0
    max_dd     = 0.0
    running_peak = arr[0]
    running_peak_idx = 0

    for i in range(1, len(arr)):
        if arr[i] > running_peak:
            running_peak     = arr[i]
            running_peak_idx = i
        dd = (running_peak - arr[i]) / running_peak * 100 if running_peak > 0 else 0.0
        if dd > max_dd:
            max_dd     = dd
            peak_idx   = running_peak_idx
            trough_idx = i

    return round(max_dd, 4), peak_idx, trough_idx


def _profit_factor(pnls: List[float]) -> float:
    """Calculate profit factor (gross profit / gross loss)."""
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss   = abs(sum(p for p in pnls if p < 0))
    return _safe_div(gross_profit, gross_loss, default=999.0)


def _expectancy(pnls: List[float]) -> float:
    """Calculate mathematical expectancy per trade."""
    if not pnls:
        return 0.0
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate  = len(wins) / len(pnls)
    avg_win   = np.mean(wins) if wins else 0.0
    avg_loss  = abs(np.mean(losses)) if losses else 0.0
    return float(win_rate * avg_win - (1 - win_rate) * avg_loss)


def _win_streak(results: List[bool]) -> Tuple[int, int]:
    """
    Calculate current and maximum win/loss streaks.

    Args:
        results: List of booleans (True=win, False=loss)

    Returns:
        (current_streak, max_streak) — positive=win streak, negative=loss streak
    """
    if not results:
        return 0, 0

    max_win_streak  = 0
    max_loss_streak = 0
    current_streak  = 0
    current_type    = None

    for result in results:
        if result:
            if current_type == "win":
                current_streak += 1
            else:
                current_streak = 1
                current_type   = "win"
            max_win_streak = max(max_win_streak, current_streak)
        else:
            if current_type == "loss":
                current_streak += 1
            else:
                current_streak = 1
                current_type   = "loss"
            max_loss_streak = max(max_loss_streak, current_streak)

    final_streak = current_streak if current_type == "win" else -current_streak
    return final_streak, max(max_win_streak, max_loss_streak)


# ─────────────────────────────────────────────────────────────
# PERFORMANCE TRACKER
# ─────────────────────────────────────────────────────────────

class PerformanceTracker:
    """
    Enterprise-grade Performance Tracking System.

    Tracks and analyses performance across:
      - Individual managers (approvals, accuracy, speed)
      - Signal quality (win rates, profit factors, R-multiples)
      - Time periods (daily, weekly, monthly, quarterly)
      - Strategies and market regimes
      - Leaderboards and rankings

    All data is persisted to MongoDB for historical analysis.
    """

    def __init__(self) -> None:
        self._client: Optional[AsyncIOMotorClient] = None
        self._db = None

    def _get_db(self):
        if self._client is None:
            self._client = AsyncIOMotorClient(
                MONGO_URL,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
            )
            self._db = self._client[DB_NAME]
        return self._db

    # ─────────────────────────────────────────────────────────
    # CORE METRICS CALCULATION
    # ─────────────────────────────────────────────────────────

    def calculate_trade_metrics(
        self,
        trades: List[Dict[str, Any]],
        account_balance: float = 10000.0,
        label: str = "portfolio",
    ) -> Dict[str, Any]:
        """
        Calculate comprehensive trading performance metrics.

        Args:
            trades: List of trade dicts with at minimum:
                    {pnl, result (WIN/LOSS), entry_price, exit_price,
                     risk_reward, strategy, symbol, created_at}
            account_balance: Current account balance for % calculations
            label: Label for this metrics set

        Returns:
            Comprehensive metrics dictionary
        """
        if not trades:
            return {
                "label":        label,
                "total_trades": 0,
                "message":      "No trades to analyse",
            }

        pnls     = [float(t.get("pnl", 0)) for t in trades]
        results  = [str(t.get("result", "")).upper() == "WIN" for t in trades]
        r_mults  = [float(t.get("risk_reward", t.get("r_multiple", 0))) for t in trades]

        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        total_pnl   = sum(pnls)
        win_rate    = _safe_div(len(wins), len(pnls))
        avg_win     = float(np.mean(wins)) if wins else 0.0
        avg_loss    = abs(float(np.mean(losses))) if losses else 0.0
        pf          = _profit_factor(pnls)
        exp         = _expectancy(pnls)
        avg_r       = float(np.mean(r_mults)) if r_mults else 0.0

        # Equity curve
        equity_curve = [account_balance]
        for pnl in pnls:
            equity_curve.append(equity_curve[-1] + pnl)

        max_dd, peak_idx, trough_idx = _max_drawdown(equity_curve)

        # Returns for Sharpe/Sortino
        returns = [
            (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
            for i in range(1, len(equity_curve))
            if equity_curve[i - 1] > 0
        ]
        sharpe  = _sharpe_ratio(returns)
        sortino = _sortino_ratio(returns)

        # Streaks
        current_streak, max_streak = _win_streak(results)

        # Consecutive losses
        max_consec_losses = 0
        current_losses    = 0
        for r in results:
            if not r:
                current_losses += 1
                max_consec_losses = max(max_consec_losses, current_losses)
            else:
                current_losses = 0

        # Recovery factor
        total_return_pct = _safe_div(total_pnl, account_balance) * 100
        recovery_factor  = _safe_div(total_return_pct, max_dd, default=999.0)

        # Calmar ratio (annualised return / max drawdown)
        n_trades = len(trades)
        annual_factor = 252 / max(n_trades, 1)
        annualised_return = total_return_pct * annual_factor
        calmar_ratio = _safe_div(annualised_return, max_dd, default=999.0)

        return {
            "label":              label,
            "total_trades":       len(trades),
            "winning_trades":     len(wins),
            "losing_trades":      len(losses),
            "win_rate":           round(win_rate, 4),
            "win_rate_pct":       round(win_rate * 100, 2),
            "total_pnl":          round(total_pnl, 2),
            "total_return_pct":   round(total_return_pct, 4),
            "avg_win":            round(avg_win, 2),
            "avg_loss":           round(avg_loss, 2),
            "largest_win":        round(max(wins), 2) if wins else 0.0,
            "largest_loss":       round(min(losses), 2) if losses else 0.0,
            "profit_factor":      round(pf, 4) if pf != 999.0 else 999.0,
            "expectancy":         round(exp, 4),
            "avg_r_multiple":     round(avg_r, 4),
            "sharpe_ratio":       round(sharpe, 4),
            "sortino_ratio":      round(sortino, 4),
            "max_drawdown_pct":   round(max_dd, 4),
            "recovery_factor":    round(recovery_factor, 4) if recovery_factor != 999.0 else 999.0,
            "calmar_ratio":       round(calmar_ratio, 4) if calmar_ratio != 999.0 else 999.0,
            "current_streak":     current_streak,
            "max_streak":         max_streak,
            "max_consecutive_losses": max_consec_losses,
            "equity_curve":       [round(e, 2) for e in equity_curve],
        }

    # ─────────────────────────────────────────────────────────
    # MANAGER PERFORMANCE METRICS
    # ─────────────────────────────────────────────────────────

    async def get_manager_metrics(
        self,
        manager_id: str,
        days: int = 30,
    ) -> Dict[str, Any]:
        """
        Get detailed performance metrics for a specific manager.

        Tracks:
          - Approval/rejection counts and rates
          - Decision accuracy (based on signal outcomes)
          - Average review time
          - Signal quality of approved signals
          - Comparison to team average
        """
        db = self._get_db()
        cutoff = datetime.utcnow() - timedelta(days=days)

        # Manager profile
        manager = await db.hybrid_managers.find_one(
            {"manager_id": manager_id}, {"password_hash": 0}
        )
        if not manager:
            return {"success": False, "error": "Manager not found"}

        # Decisions in period
        approved_signals = await (
            db.hybrid_signals
            .find({
                "approvals.manager_id": manager_id,
                "submitted_at": {"$gte": cutoff},
            })
            .to_list(None)
        )

        rejected_signals = await (
            db.hybrid_signals
            .find({
                "rejections.manager_id": manager_id,
                "submitted_at": {"$gte": cutoff},
            })
            .to_list(None)
        )

        adjusted_signals = await (
            db.hybrid_signals
            .find({
                "adjustments.manager_id": manager_id,
                "submitted_at": {"$gte": cutoff},
            })
            .to_list(None)
        )

        n_approved   = len(approved_signals)
        n_rejected   = len(rejected_signals)
        n_adjusted   = len(adjusted_signals)
        n_total      = n_approved + n_rejected
        approval_rate = _safe_div(n_approved, n_total) * 100

        # Quality scores of approved signals
        quality_scores = []
        for sig in approved_signals:
            qs = sig.get("quality_score", {})
            if isinstance(qs, dict) and "composite_score" in qs:
                quality_scores.append(float(qs["composite_score"]))

        avg_quality = float(np.mean(quality_scores)) if quality_scores else 0.0

        # Risk tier distribution of approved signals
        tier_dist: Dict[str, int] = defaultdict(int)
        for sig in approved_signals:
            tier_dist[sig.get("risk_tier", "UNKNOWN")] += 1

        # Review time analysis (time from submission to first approval)
        review_times = []
        for sig in approved_signals:
            submitted = sig.get("submitted_at")
            for appr in sig.get("approvals", []):
                if appr.get("manager_id") == manager_id:
                    approved_at_str = appr.get("approved_at")
                    if submitted and approved_at_str:
                        try:
                            if hasattr(submitted, "timestamp"):
                                sub_ts = submitted.timestamp()
                            else:
                                sub_ts = datetime.fromisoformat(str(submitted)).timestamp()
                            appr_ts = datetime.fromisoformat(approved_at_str).timestamp()
                            review_times.append((appr_ts - sub_ts) / 60)  # minutes
                        except Exception:
                            pass

        avg_review_time = float(np.mean(review_times)) if review_times else 0.0

        # Comments/collaboration activity
        n_comments = await db.hybrid_comments.count_documents({
            "manager_id": manager_id,
            "created_at": {"$gte": cutoff},
        })

        # Audit actions
        n_audit_actions = await db.hybrid_audit_log.count_documents({
            "performed_by": manager_id,
            "timestamp":    {"$gte": cutoff},
        })

        # Format manager profile
        manager.pop("_id", None)
        for ts in ("created_at", "last_login", "last_activity"):
            if manager.get(ts) and hasattr(manager[ts], "isoformat"):
                manager[ts] = manager[ts].isoformat()

        return {
            "success":      True,
            "manager_id":   manager_id,
            "manager":      manager,
            "period_days":  days,
            "decisions": {
                "approvals":     n_approved,
                "rejections":    n_rejected,
                "adjustments":   n_adjusted,
                "total":         n_total,
                "approval_rate": round(approval_rate, 2),
            },
            "quality": {
                "avg_approved_signal_quality": round(avg_quality, 2),
                "quality_scores_count":        len(quality_scores),
                "tier_distribution":           dict(tier_dist),
            },
            "efficiency": {
                "avg_review_time_minutes": round(avg_review_time, 2),
                "comments_added":          n_comments,
                "total_audit_actions":     n_audit_actions,
            },
        }

    # ─────────────────────────────────────────────────────────
    # SIGNAL QUALITY TRACKING
    # ─────────────────────────────────────────────────────────

    async def get_signal_quality_stats(
        self,
        days: int = 30,
        pair_filter: Optional[str] = None,
        strategy_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get aggregate signal quality statistics.

        Analyses:
          - Quality score distribution
          - Grade breakdown (A+, A, B, C, D, F)
          - Risk tier distribution
          - Approval rates by quality grade
          - Quality trends over time
        """
        db = self._get_db()
        cutoff = datetime.utcnow() - timedelta(days=days)

        query: Dict[str, Any] = {"submitted_at": {"$gte": cutoff}}
        if pair_filter:
            query["signal_data.pair"] = pair_filter.upper()
        if strategy_filter:
            query["signal_data.strategy"] = strategy_filter.upper()

        signals = await db.hybrid_signals.find(query).to_list(None)

        if not signals:
            return {
                "success":      True,
                "period_days":  days,
                "total_signals": 0,
                "message":      "No signals in period",
            }

        # Quality score distribution
        quality_scores = []
        grade_counts: Dict[str, int] = defaultdict(int)
        tier_counts:  Dict[str, int] = defaultdict(int)
        grade_approval: Dict[str, Dict[str, int]] = defaultdict(lambda: {"approved": 0, "rejected": 0, "pending": 0})

        for sig in signals:
            qs = sig.get("quality_score", {})
            if isinstance(qs, dict):
                score = qs.get("composite_score", 0)
                grade = qs.get("grade", "?")
                quality_scores.append(float(score))
                grade_counts[grade] += 1

                status = sig.get("status", "PENDING_REVIEW")
                if status == "APPROVED":
                    grade_approval[grade]["approved"] += 1
                elif status == "REJECTED":
                    grade_approval[grade]["rejected"] += 1
                else:
                    grade_approval[grade]["pending"] += 1

            tier = sig.get("risk_tier", "UNKNOWN")
            tier_counts[tier] += 1

        # Approval rates by grade
        grade_approval_rates = {}
        for grade, counts in grade_approval.items():
            total = counts["approved"] + counts["rejected"]
            grade_approval_rates[grade] = {
                **counts,
                "approval_rate": round(_safe_div(counts["approved"], total) * 100, 2),
            }

        # Score statistics
        score_arr = np.array(quality_scores) if quality_scores else np.array([0.0])

        # Weekly trend (last 4 weeks)
        weekly_trend = []
        for week_offset in range(4):
            week_start = cutoff + timedelta(weeks=week_offset)
            week_end   = week_start + timedelta(weeks=1)
            week_sigs  = [
                s for s in signals
                if week_start <= (s.get("submitted_at") or datetime.min) < week_end
            ]
            week_scores = [
                float(s.get("quality_score", {}).get("composite_score", 0))
                for s in week_sigs
                if isinstance(s.get("quality_score"), dict)
            ]
            weekly_trend.append({
                "week_start":  week_start.isoformat(),
                "week_end":    week_end.isoformat(),
                "count":       len(week_sigs),
                "avg_score":   round(float(np.mean(week_scores)), 2) if week_scores else 0.0,
            })

        return {
            "success":       True,
            "period_days":   days,
            "total_signals": len(signals),
            "score_stats": {
                "mean":   round(float(np.mean(score_arr)), 2),
                "median": round(float(np.median(score_arr)), 2),
                "std":    round(float(np.std(score_arr)), 2),
                "min":    round(float(np.min(score_arr)), 2),
                "max":    round(float(np.max(score_arr)), 2),
                "p25":    round(float(np.percentile(score_arr, 25)), 2),
                "p75":    round(float(np.percentile(score_arr, 75)), 2),
            },
            "grade_distribution":    dict(grade_counts),
            "tier_distribution":     dict(tier_counts),
            "approval_by_grade":     grade_approval_rates,
            "weekly_trend":          weekly_trend,
        }

    # ─────────────────────────────────────────────────────────
    # LEADERBOARD
    # ─────────────────────────────────────────────────────────

    async def get_leaderboard(
        self,
        days: int = 30,
        metric: str = "total_decisions",
        limit: int = 10,
    ) -> Dict[str, Any]:
        """
        Generate a manager leaderboard ranked by the specified metric.

        Available metrics:
          - total_decisions: Total approvals + rejections
          - approval_rate: Percentage of signals approved
          - avg_review_time: Average time to review (lower = better)
          - quality_score: Average quality of approved signals
          - activity_score: Composite activity score
        """
        db = self._get_db()
        cutoff = datetime.utcnow() - timedelta(days=days)

        managers = await db.hybrid_managers.find(
            {"is_active": True}, {"password_hash": 0}
        ).to_list(500)

        leaderboard = []
        for mgr in managers:
            mgr_id = mgr["manager_id"]

            n_approved = await db.hybrid_signals.count_documents({
                "approvals.manager_id": mgr_id,
                "submitted_at": {"$gte": cutoff},
            })
            n_rejected = await db.hybrid_signals.count_documents({
                "rejections.manager_id": mgr_id,
                "submitted_at": {"$gte": cutoff},
            })
            n_total = n_approved + n_rejected

            # Quality scores
            approved_sigs = await (
                db.hybrid_signals
                .find({
                    "approvals.manager_id": mgr_id,
                    "submitted_at": {"$gte": cutoff},
                })
                .to_list(None)
            )
            quality_scores = [
                float(s.get("quality_score", {}).get("composite_score", 0))
                for s in approved_sigs
                if isinstance(s.get("quality_score"), dict)
            ]
            avg_quality = float(np.mean(quality_scores)) if quality_scores else 0.0

            # Activity score (composite)
            n_comments = await db.hybrid_comments.count_documents({
                "manager_id": mgr_id,
                "created_at": {"$gte": cutoff},
            })
            activity_score = n_total * 2 + n_comments * 0.5

            entry = {
                "rank":           0,  # filled after sort
                "manager_id":     mgr_id,
                "full_name":      mgr.get("full_name", ""),
                "role":           mgr.get("role", ""),
                "department":     mgr.get("department", ""),
                "approvals":      n_approved,
                "rejections":     n_rejected,
                "total_decisions": n_total,
                "approval_rate":  round(_safe_div(n_approved, n_total) * 100, 2),
                "avg_quality_score": round(avg_quality, 2),
                "comments_added": n_comments,
                "activity_score": round(activity_score, 2),
            }
            leaderboard.append(entry)

        # Sort by metric
        sort_key_map = {
            "total_decisions": lambda x: x["total_decisions"],
            "approval_rate":   lambda x: x["approval_rate"],
            "quality_score":   lambda x: x["avg_quality_score"],
            "activity_score":  lambda x: x["activity_score"],
        }
        sort_fn = sort_key_map.get(metric, sort_key_map["total_decisions"])
        leaderboard.sort(key=sort_fn, reverse=True)

        # Assign ranks
        for i, entry in enumerate(leaderboard[:limit]):
            entry["rank"] = i + 1

        return {
            "success":     True,
            "period_days": days,
            "metric":      metric,
            "leaderboard": leaderboard[:limit],
            "total_managers": len(leaderboard),
        }

    # ─────────────────────────────────────────────────────────
    # PERIOD REPORTS
    # ─────────────────────────────────────────────────────────

    async def generate_weekly_report(
        self,
        week_offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Generate a comprehensive weekly performance report.

        Args:
            week_offset: 0 = current week, 1 = last week, etc.

        Returns:
            Full weekly report with signal stats, manager activity,
            risk events, and trend analysis.
        """
        db = self._get_db()

        now        = datetime.utcnow()
        week_start = now - timedelta(days=now.weekday() + week_offset * 7)
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        week_end   = week_start + timedelta(days=7)

        # Signal statistics
        total_signals    = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": week_start, "$lt": week_end}})
        approved_signals = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": week_start, "$lt": week_end}, "status": "APPROVED"})
        rejected_signals = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": week_start, "$lt": week_end}, "status": "REJECTED"})
        escalated_signals = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": week_start, "$lt": week_end}, "status": "ESCALATED"})

        # Risk tier breakdown
        tier_breakdown = {}
        for tier in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            tier_breakdown[tier] = await db.hybrid_signals.count_documents({
                "submitted_at": {"$gte": week_start, "$lt": week_end},
                "risk_tier":    tier,
            })

        # Manager activity
        manager_pipeline = [
            {"$match": {"timestamp": {"$gte": week_start, "$lt": week_end}}},
            {"$group": {
                "_id":     "$performed_by",
                "actions": {"$sum": 1},
                "role":    {"$first": "$role"},
            }},
            {"$sort": {"actions": -1}},
            {"$limit": 10},
        ]
        top_managers = await db.hybrid_audit_log.aggregate(manager_pipeline).to_list(None)

        # Alert summary
        critical_alerts = await db.hybrid_alerts.count_documents({"created_at": {"$gte": week_start, "$lt": week_end}, "severity": "CRITICAL"})
        warning_alerts  = await db.hybrid_alerts.count_documents({"created_at": {"$gte": week_start, "$lt": week_end}, "severity": "WARNING"})

        # Risk events
        risk_events = await db.hybrid_audit_log.count_documents({
            "timestamp": {"$gte": week_start, "$lt": week_end},
            "action":    {"$regex": "^risk:", "$options": "i"},
        })

        # Daily breakdown
        daily_breakdown = []
        for day_offset in range(7):
            day_start = week_start + timedelta(days=day_offset)
            day_end   = day_start + timedelta(days=1)
            day_total    = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": day_start, "$lt": day_end}})
            day_approved = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": day_start, "$lt": day_end}, "status": "APPROVED"})
            daily_breakdown.append({
                "date":          day_start.strftime("%Y-%m-%d"),
                "day_name":      day_start.strftime("%A"),
                "total_signals": day_total,
                "approved":      day_approved,
                "approval_rate": round(_safe_div(day_approved, day_total) * 100, 2),
            })

        approval_rate = round(_safe_div(approved_signals, total_signals) * 100, 2)

        return {
            "success":      True,
            "report_type":  "weekly",
            "week_start":   week_start.isoformat(),
            "week_end":     week_end.isoformat(),
            "generated_at": now.isoformat(),
            "signals": {
                "total":         total_signals,
                "approved":      approved_signals,
                "rejected":      rejected_signals,
                "escalated":     escalated_signals,
                "approval_rate": approval_rate,
                "by_risk_tier":  tier_breakdown,
            },
            "alerts": {
                "critical": critical_alerts,
                "warning":  warning_alerts,
                "total":    critical_alerts + warning_alerts,
            },
            "risk_events":    risk_events,
            "top_managers":   top_managers,
            "daily_breakdown": daily_breakdown,
        }

    async def generate_monthly_report(
        self,
        month_offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Generate a comprehensive monthly performance report.

        Args:
            month_offset: 0 = current month, 1 = last month, etc.

        Returns:
            Full monthly report with trends, comparisons, and analytics.
        """
        db = self._get_db()

        now = datetime.utcnow()
        # Calculate month boundaries
        month_year  = now.year
        month_month = now.month - month_offset
        while month_month <= 0:
            month_month += 12
            month_year  -= 1

        month_start = datetime(month_year, month_month, 1)
        if month_month == 12:
            month_end = datetime(month_year + 1, 1, 1)
        else:
            month_end = datetime(month_year, month_month + 1, 1)

        # Signal statistics
        total_signals    = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": month_start, "$lt": month_end}})
        approved_signals = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": month_start, "$lt": month_end}, "status": "APPROVED"})
        rejected_signals = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": month_start, "$lt": month_end}, "status": "REJECTED"})

        # Quality score stats
        quality_pipeline = [
            {"$match": {"submitted_at": {"$gte": month_start, "$lt": month_end}}},
            {"$group": {
                "_id":       None,
                "avg_score": {"$avg": "$quality_score.composite_score"},
                "max_score": {"$max": "$quality_score.composite_score"},
                "min_score": {"$min": "$quality_score.composite_score"},
            }},
        ]
        quality_agg = await db.hybrid_signals.aggregate(quality_pipeline).to_list(1)
        quality_stats = quality_agg[0] if quality_agg else {}
        quality_stats.pop("_id", None)

        # Manager leaderboard for month
        manager_pipeline = [
            {"$match": {"timestamp": {"$gte": month_start, "$lt": month_end}}},
            {"$group": {
                "_id":     "$performed_by",
                "actions": {"$sum": 1},
                "role":    {"$first": "$role"},
            }},
            {"$sort": {"actions": -1}},
            {"$limit": 5},
        ]
        top_managers = await db.hybrid_audit_log.aggregate(manager_pipeline).to_list(None)

        # Risk tier distribution
        tier_breakdown = {}
        for tier in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            tier_breakdown[tier] = await db.hybrid_signals.count_documents({
                "submitted_at": {"$gte": month_start, "$lt": month_end},
                "risk_tier":    tier,
            })

        # Weekly breakdown within month
        weekly_breakdown = []
        current_week = month_start
        while current_week < month_end:
            next_week = min(current_week + timedelta(days=7), month_end)
            week_total    = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": current_week, "$lt": next_week}})
            week_approved = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": current_week, "$lt": next_week}, "status": "APPROVED"})
            weekly_breakdown.append({
                "week_start":    current_week.strftime("%Y-%m-%d"),
                "week_end":      next_week.strftime("%Y-%m-%d"),
                "total_signals": week_total,
                "approved":      week_approved,
                "approval_rate": round(_safe_div(week_approved, week_total) * 100, 2),
            })
            current_week = next_week

        # Compliance summary
        total_audit_actions = await db.hybrid_audit_log.count_documents({
            "timestamp": {"$gte": month_start, "$lt": month_end},
        })
        circuit_breaker_events = await db.hybrid_audit_log.count_documents({
            "timestamp": {"$gte": month_start, "$lt": month_end},
            "action":    "risk:circuit_breaker",
        })

        approval_rate = round(_safe_div(approved_signals, total_signals) * 100, 2)

        return {
            "success":      True,
            "report_type":  "monthly",
            "month":        month_start.strftime("%B %Y"),
            "month_start":  month_start.isoformat(),
            "month_end":    month_end.isoformat(),
            "generated_at": now.isoformat(),
            "signals": {
                "total":         total_signals,
                "approved":      approved_signals,
                "rejected":      rejected_signals,
                "approval_rate": approval_rate,
                "by_risk_tier":  tier_breakdown,
            },
            "quality_stats":    {k: round(float(v), 2) if v else 0.0 for k, v in quality_stats.items()},
            "top_managers":     top_managers,
            "weekly_breakdown": weekly_breakdown,
            "compliance": {
                "total_audit_actions":      total_audit_actions,
                "circuit_breaker_events":   circuit_breaker_events,
            },
        }

    # ─────────────────────────────────────────────────────────
    # TREND ANALYSIS
    # ─────────────────────────────────────────────────────────

    async def get_trend_analysis(
        self,
        days: int = 90,
        granularity: str = "daily",
    ) -> Dict[str, Any]:
        """
        Analyse performance trends over time.

        Args:
            days: Lookback period in days
            granularity: "daily" | "weekly" | "monthly"

        Returns:
            Trend data with approval rates, quality scores, and risk metrics.
        """
        db = self._get_db()
        cutoff = datetime.utcnow() - timedelta(days=days)

        # Determine bucket size
        if granularity == "weekly":
            bucket_days = 7
        elif granularity == "monthly":
            bucket_days = 30
        else:
            bucket_days = 1

        n_buckets = max(1, days // bucket_days)
        trend_data = []

        for i in range(n_buckets):
            bucket_start = cutoff + timedelta(days=i * bucket_days)
            bucket_end   = bucket_start + timedelta(days=bucket_days)

            total    = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": bucket_start, "$lt": bucket_end}})
            approved = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": bucket_start, "$lt": bucket_end}, "status": "APPROVED"})
            rejected = await db.hybrid_signals.count_documents({"submitted_at": {"$gte": bucket_start, "$lt": bucket_end}, "status": "REJECTED"})

            # Average quality score
            quality_pipeline = [
                {"$match": {"submitted_at": {"$gte": bucket_start, "$lt": bucket_end}}},
                {"$group": {"_id": None, "avg_score": {"$avg": "$quality_score.composite_score"}}},
            ]
            quality_agg = await db.hybrid_signals.aggregate(quality_pipeline).to_list(1)
            avg_quality = float(quality_agg[0]["avg_score"]) if quality_agg and quality_agg[0].get("avg_score") else 0.0

            trend_data.append({
                "period_start":  bucket_start.isoformat(),
                "period_end":    bucket_end.isoformat(),
                "total_signals": total,
                "approved":      approved,
                "rejected":      rejected,
                "approval_rate": round(_safe_div(approved, total) * 100, 2),
                "avg_quality":   round(avg_quality, 2),
            })

        # Calculate trend direction (linear regression on approval rate)
        approval_rates = [d["approval_rate"] for d in trend_data]
        if len(approval_rates) >= 3:
            x = np.arange(len(approval_rates))
            slope = float(np.polyfit(x, approval_rates, 1)[0])
            trend_direction = "IMPROVING" if slope > 0.5 else ("DECLINING" if slope < -0.5 else "STABLE")
        else:
            slope           = 0.0
            trend_direction = "INSUFFICIENT_DATA"

        return {
            "success":         True,
            "period_days":     days,
            "granularity":     granularity,
            "trend_direction": trend_direction,
            "trend_slope":     round(slope, 4),
            "data_points":     trend_data,
            "summary": {
                "avg_approval_rate": round(float(np.mean(approval_rates)), 2) if approval_rates else 0.0,
                "max_approval_rate": round(max(approval_rates), 2) if approval_rates else 0.0,
                "min_approval_rate": round(min(approval_rates), 2) if approval_rates else 0.0,
            },
        }

    # ─────────────────────────────────────────────────────────
    # RECORD SIGNAL OUTCOME
    # ─────────────────────────────────────────────────────────

    async def record_signal_outcome(
        self,
        signal_id: str,
        outcome: str,
        pnl: float,
        pnl_pct: float,
        r_multiple: float,
        closed_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Record the outcome of a completed signal for performance tracking.

        Args:
            signal_id: Signal identifier
            outcome: "WIN" | "LOSS" | "BREAKEVEN"
            pnl: Profit/loss in USD
            pnl_pct: Profit/loss as percentage
            r_multiple: R-multiple achieved
            closed_at: When the trade closed
            metadata: Additional trade metadata

        Returns:
            Success status
        """
        try:
            db = self._get_db()

            outcome_doc = {
                "outcome_id": str(uuid.uuid4()),
                "signal_id":  signal_id,
                "outcome":    outcome.upper(),
                "pnl":        round(pnl, 2),
                "pnl_pct":    round(pnl_pct, 4),
                "r_multiple": round(r_multiple, 4),
                "closed_at":  closed_at or datetime.utcnow(),
                "recorded_at": datetime.utcnow(),
                "metadata":   metadata or {},
            }

            await db.signal_outcomes.insert_one(outcome_doc)

            # Update the signal document with outcome
            await db.hybrid_signals.update_one(
                {"signal_id": signal_id},
                {"$set": {
                    "outcome":    outcome.upper(),
                    "pnl":        round(pnl, 2),
                    "pnl_pct":    round(pnl_pct, 4),
                    "r_multiple": round(r_multiple, 4),
                    "closed_at":  closed_at or datetime.utcnow(),
                }},
            )

            logger.info(f"📊 Signal outcome recorded: {signal_id} → {outcome} (PnL: ${pnl:.2f})")
            return {"success": True, "outcome_id": outcome_doc["outcome_id"]}

        except Exception as exc:
            logger.error(f"Signal outcome recording failed: {exc}")
            return {"success": False, "error": str(exc)}

    async def get_outcome_stats(
        self,
        days: int = 30,
        pair_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get aggregate outcome statistics for completed signals.

        Returns win rate, profit factor, Sharpe ratio, and more.
        """
        db = self._get_db()
        cutoff = datetime.utcnow() - timedelta(days=days)

        query: Dict[str, Any] = {"recorded_at": {"$gte": cutoff}}
        if pair_filter:
            query["metadata.pair"] = pair_filter.upper()

        outcomes = await db.signal_outcomes.find(query).to_list(None)

        if not outcomes:
            return {
                "success":      True,
                "period_days":  days,
                "total_trades": 0,
                "message":      "No completed trades in period",
            }

        pnls       = [float(o.get("pnl", 0)) for o in outcomes]
        r_mults    = [float(o.get("r_multiple", 0)) for o in outcomes]
        win_flags  = [o.get("outcome", "") == "WIN" for o in outcomes]

        metrics = self.calculate_trade_metrics(
            [{"pnl": p, "result": "WIN" if w else "LOSS", "risk_reward": r}
             for p, w, r in zip(pnls, win_flags, r_mults)],
            label="signal_outcomes",
        )

        return {
            "success":     True,
            "period_days": days,
            **metrics,
        }


# ─────────────────────────────────────────────────────────────
# SINGLETON INSTANCE
# ─────────────────────────────────────────────────────────────

performance_tracker = PerformanceTracker()

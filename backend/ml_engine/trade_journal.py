"""
Trade Journal
Comprehensive trade logging, analysis, and pattern recognition
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class TradeJournal:
    """
    Institutional-Grade Trade Journal.

    Records every trade with full context:
    - Entry/exit prices, P&L, R-multiple
    - Strategy, regime, timeframe context
    - SMC score, MTF alignment, pivot zone
    - Market conditions at entry
    - Post-trade analysis

    Provides:
    - Pattern recognition (what setups work best)
    - Strategy performance breakdown
    - Regime-specific win rates
    - Optimal entry time analysis
    - Continuous improvement insights
    """

    def __init__(self, max_entries: int = 1000):
        self.max_entries = max_entries
        self.version = "3.0.0"
        self._entries: List[Dict] = []

    # ------------------------------------------------------------------
    # Entry Recording
    # ------------------------------------------------------------------

    def record_trade(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        exit_price: Optional[float] = None,
        sl_price: Optional[float] = None,
        tp_levels: Optional[List[float]] = None,
        lot_size: float = 0.01,
        strategy: str = "UNKNOWN",
        regime: str = "UNKNOWN",
        timeframe: str = "4h",
        smc_score: int = 0,
        mtf_alignment: float = 0.0,
        pivot_zone: str = "UNKNOWN",
        confidence: float = 0.0,
        notes: str = "",
        metadata: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Record a new trade entry.

        Returns:
            Trade record with generated ID
        """
        trade_id = f"TJ_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{symbol}"

        # Calculate P&L if exit is known
        pnl = None
        r_multiple = None
        outcome = "OPEN"

        if exit_price is not None:
            pnl = self._calculate_pnl(direction, entry_price, exit_price, lot_size)
            if sl_price:
                risk = abs(entry_price - sl_price)
                reward = abs(exit_price - entry_price)
                r_multiple = round(reward / risk, 2) if risk > 0 else 0.0
                r_multiple = r_multiple if pnl > 0 else -r_multiple
            outcome = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")

        entry = {
            "id": trade_id,
            "symbol": symbol,
            "direction": direction.upper(),
            "entry_price": round(entry_price, 5),
            "exit_price": round(exit_price, 5) if exit_price else None,
            "sl_price": round(sl_price, 5) if sl_price else None,
            "tp_levels": [round(tp, 5) for tp in (tp_levels or [])],
            "lot_size": lot_size,
            "pnl": round(pnl, 2) if pnl is not None else None,
            "r_multiple": r_multiple,
            "outcome": outcome,
            "strategy": strategy,
            "regime": regime,
            "timeframe": timeframe,
            "smc_score": smc_score,
            "mtf_alignment": round(mtf_alignment, 2),
            "pivot_zone": pivot_zone,
            "confidence": round(confidence, 2),
            "notes": notes,
            "metadata": metadata or {},
            "created_at": datetime.utcnow().isoformat(),
            "closed_at": datetime.utcnow().isoformat() if exit_price else None,
            "version": self.version,
        }

        self._entries.append(entry)

        # Enforce max entries
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries:]

        logger.info(
            f"TradeJournal: recorded {trade_id} "
            f"{direction} {symbol} @ {entry_price} "
            f"outcome={outcome} pnl={pnl}"
        )
        return entry

    def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str = "MANUAL",
    ) -> Optional[Dict]:
        """Close an open trade and calculate final P&L."""
        for entry in self._entries:
            if entry["id"] == trade_id and entry["outcome"] == "OPEN":
                entry["exit_price"] = round(exit_price, 5)
                entry["closed_at"] = datetime.utcnow().isoformat()
                entry["exit_reason"] = exit_reason

                pnl = self._calculate_pnl(
                    entry["direction"],
                    entry["entry_price"],
                    exit_price,
                    entry["lot_size"],
                )
                entry["pnl"] = round(pnl, 2)
                entry["outcome"] = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")

                if entry.get("sl_price"):
                    risk = abs(entry["entry_price"] - entry["sl_price"])
                    reward = abs(exit_price - entry["entry_price"])
                    r = round(reward / risk, 2) if risk > 0 else 0.0
                    entry["r_multiple"] = r if pnl > 0 else -r

                return entry
        return None

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def analyze(
        self,
        lookback: int = 100,
        filter_strategy: Optional[str] = None,
        filter_symbol: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Analyze trade journal for patterns and insights.

        Args:
            lookback: Number of recent trades to analyze
            filter_strategy: Filter by strategy name
            filter_symbol: Filter by symbol

        Returns:
            Comprehensive journal analysis
        """
        try:
            entries = self._entries[-lookback:]

            # Apply filters
            if filter_strategy:
                entries = [e for e in entries if e.get("strategy") == filter_strategy]
            if filter_symbol:
                entries = [e for e in entries if e.get("symbol") == filter_symbol]

            closed = [e for e in entries if e["outcome"] != "OPEN"]

            if not closed:
                return {
                    "valid": True,
                    "message": "No closed trades to analyze",
                    "total_entries": len(entries),
                }

            result: Dict[str, Any] = {
                "valid": True,
                "total_entries": len(entries),
                "closed_trades": len(closed),
                "open_trades": len(entries) - len(closed),
                "timestamp": datetime.utcnow().isoformat(),
                "version": self.version,
            }

            # Core metrics
            result["metrics"] = self._core_metrics(closed)

            # Pattern analysis
            result["patterns"] = self._pattern_analysis(closed)

            # Best setups
            result["best_setups"] = self._find_best_setups(closed)

            # Improvement areas
            result["improvement_areas"] = self._find_improvement_areas(closed)

            # Recent trades summary
            result["recent_trades"] = [
                {
                    "id": e["id"],
                    "symbol": e["symbol"],
                    "direction": e["direction"],
                    "outcome": e["outcome"],
                    "pnl": e.get("pnl"),
                    "r_multiple": e.get("r_multiple"),
                    "strategy": e.get("strategy"),
                    "created_at": e.get("created_at"),
                }
                for e in closed[-10:]
            ]

            return result

        except Exception as exc:
            logger.error(f"Trade journal analysis error: {exc}", exc_info=True)
            return {"valid": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Core Metrics
    # ------------------------------------------------------------------

    def _core_metrics(self, trades: List[Dict]) -> Dict[str, Any]:
        """Calculate core trading metrics."""
        pnls = [float(t.get("pnl", 0)) for t in trades if t.get("pnl") is not None]
        r_multiples = [float(t.get("r_multiple", 0)) for t in trades if t.get("r_multiple") is not None]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        return {
            "total_pnl": round(sum(pnls), 2),
            "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0,
            "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if losses and sum(losses) != 0 else 999.0,
            "avg_win": round(np.mean(wins), 2) if wins else 0,
            "avg_loss": round(abs(np.mean(losses)), 2) if losses else 0,
            "avg_r_multiple": round(np.mean(r_multiples), 4) if r_multiples else 0,
            "expectancy": round(
                (len(wins) / len(pnls) * np.mean(wins) if wins else 0) -
                (len(losses) / len(pnls) * abs(np.mean(losses)) if losses else 0),
                4
            ) if pnls else 0,
            "win_loss_ratio": round(np.mean(wins) / abs(np.mean(losses)), 4) if wins and losses else 0,
        }

    # ------------------------------------------------------------------
    # Pattern Analysis
    # ------------------------------------------------------------------

    def _pattern_analysis(self, trades: List[Dict]) -> Dict[str, Any]:
        """Identify performance patterns."""
        patterns: Dict[str, Any] = {}

        # By SMC score
        smc_groups: Dict[str, List] = defaultdict(list)
        for t in trades:
            score = t.get("smc_score", 0)
            bucket = f"smc_{(score // 2) * 2}_{(score // 2) * 2 + 2}"
            smc_groups[bucket].append(float(t.get("pnl", 0)))

        patterns["by_smc_score"] = {
            k: {
                "trades": len(v),
                "win_rate": round(sum(1 for p in v if p > 0) / len(v), 4),
                "avg_pnl": round(np.mean(v), 4),
            }
            for k, v in smc_groups.items()
        }

        # By MTF alignment
        mtf_groups: Dict[str, List] = defaultdict(list)
        for t in trades:
            alignment = t.get("mtf_alignment", 0)
            if alignment >= 80:
                bucket = "high_80+"
            elif alignment >= 60:
                bucket = "medium_60-80"
            else:
                bucket = "low_<60"
            mtf_groups[bucket].append(float(t.get("pnl", 0)))

        patterns["by_mtf_alignment"] = {
            k: {
                "trades": len(v),
                "win_rate": round(sum(1 for p in v if p > 0) / len(v), 4),
                "avg_pnl": round(np.mean(v), 4),
            }
            for k, v in mtf_groups.items()
        }

        # By confidence
        conf_groups: Dict[str, List] = defaultdict(list)
        for t in trades:
            conf = t.get("confidence", 0)
            if conf >= 80:
                bucket = "high_80+"
            elif conf >= 60:
                bucket = "medium_60-80"
            else:
                bucket = "low_<60"
            conf_groups[bucket].append(float(t.get("pnl", 0)))

        patterns["by_confidence"] = {
            k: {
                "trades": len(v),
                "win_rate": round(sum(1 for p in v if p > 0) / len(v), 4),
                "avg_pnl": round(np.mean(v), 4),
            }
            for k, v in conf_groups.items()
        }

        return patterns

    def _find_best_setups(self, trades: List[Dict]) -> List[Dict]:
        """Identify the highest-performing setup combinations."""
        setup_groups: Dict[str, List] = defaultdict(list)

        for t in trades:
            key = f"{t.get('strategy', 'UNK')}_{t.get('regime', 'UNK')}_{t.get('pivot_zone', 'UNK')}"
            setup_groups[key].append(float(t.get("pnl", 0)))

        setups = []
        for key, pnls in setup_groups.items():
            if len(pnls) >= 3:
                wins = [p for p in pnls if p > 0]
                setups.append({
                    "setup": key,
                    "trades": len(pnls),
                    "win_rate": round(len(wins) / len(pnls), 4),
                    "avg_pnl": round(np.mean(pnls), 4),
                    "total_pnl": round(sum(pnls), 4),
                })

        return sorted(setups, key=lambda x: x["total_pnl"], reverse=True)[:5]

    def _find_improvement_areas(self, trades: List[Dict]) -> List[str]:
        """Identify areas needing improvement."""
        areas = []
        metrics = self._core_metrics(trades)

        if metrics["win_rate"] < 0.45:
            areas.append("Win rate below 45% — review entry criteria")
        if metrics["profit_factor"] < 1.2:
            areas.append("Profit factor below 1.2 — improve R:R ratio")
        if metrics["avg_r_multiple"] < 1.0:
            areas.append("Average R-multiple below 1.0 — let winners run longer")

        # Check if low-confidence trades are dragging performance
        low_conf = [t for t in trades if t.get("confidence", 100) < 60]
        if low_conf:
            low_pnls = [float(t.get("pnl", 0)) for t in low_conf]
            if sum(low_pnls) < 0:
                areas.append("Low-confidence trades (<60%) are net negative — raise confidence threshold")

        return areas

    # ------------------------------------------------------------------
    # P&L Calculation
    # ------------------------------------------------------------------

    def _calculate_pnl(
        self,
        direction: str,
        entry: float,
        exit_price: float,
        lot_size: float,
    ) -> float:
        """Calculate P&L for a trade (simplified for gold)."""
        # Gold: 1 lot = 100 oz, price in USD/oz
        # P&L = (exit - entry) * lot_size * 100
        multiplier = 100  # oz per lot
        if direction.upper() == "BUY":
            return (exit_price - entry) * lot_size * multiplier
        else:
            return (entry - exit_price) * lot_size * multiplier

    # ------------------------------------------------------------------
    # Data Access
    # ------------------------------------------------------------------

    def get_entries(
        self,
        limit: int = 50,
        status: Optional[str] = None,
    ) -> List[Dict]:
        """Get journal entries."""
        entries = self._entries[-limit:]
        if status:
            entries = [e for e in entries if e.get("outcome") == status.upper()]
        return entries

    def get_open_trades(self) -> List[Dict]:
        """Get all open trades."""
        return [e for e in self._entries if e.get("outcome") == "OPEN"]

    def clear(self) -> None:
        """Clear all journal entries."""
        self._entries = []
        logger.info("TradeJournal: cleared all entries")


# Global instance
trade_journal = TradeJournal()

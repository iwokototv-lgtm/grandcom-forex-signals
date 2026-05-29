"""
Performance Attribution Engine
Track and attribute trading performance across strategies, regimes, and timeframes
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class PerformanceAttribution:
    """
    Performance Attribution and Analytics Engine.

    Tracks and attributes P&L across:
    - Trading strategies (SMC, Mean Reversion, Breakout)
    - Market regimes (Trend Up/Down, Range, High/Low Vol)
    - Timeframes (1H, 4H, Daily, Weekly)
    - Symbols (XAUUSD, XAUEUR)
    - Time of day / day of week

    Metrics:
    - Win rate, profit factor, expectancy
    - Sharpe ratio, Sortino ratio, Calmar ratio
    - Maximum drawdown, recovery factor
    - Average win/loss, R-multiple distribution
    """

    def __init__(self, lookback_days: int = 30):
        self.lookback_days = lookback_days
        self.version = "3.0.0"

    # ------------------------------------------------------------------
    # Main Attribution
    # ------------------------------------------------------------------

    def analyze(
        self,
        trades: List[Dict],
        account_balance: float = 10000.0,
        lookback_days: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Full performance attribution analysis.

        Args:
            trades: List of trade dicts with pnl, strategy, regime, symbol, etc.
            account_balance: Current account balance
            lookback_days: Override default lookback

        Returns:
            Comprehensive performance attribution
        """
        try:
            lb = lookback_days or self.lookback_days
            cutoff = datetime.now(timezone.utc) - timedelta(days=lb)

            # Filter to lookback period
            recent_trades = [
                t for t in trades
                if self._parse_dt(t.get("closed_at", t.get("created_at", ""))) >= cutoff
            ]

            if not recent_trades:
                return {
                    "valid": True,
                    "message": "No trades in lookback period",
                    "lookback_days": lb,
                    "total_trades": 0,
                }

            result: Dict[str, Any] = {
                "valid": True,
                "lookback_days": lb,
                "total_trades": len(recent_trades),
                "timestamp": datetime.utcnow().isoformat(),
                "version": self.version,
            }

            # Overall metrics
            result["overall"] = self._overall_metrics(recent_trades, account_balance)

            # By strategy
            result["by_strategy"] = self._attribute_by_dimension(recent_trades, "strategy")

            # By regime
            result["by_regime"] = self._attribute_by_dimension(recent_trades, "regime")

            # By symbol
            result["by_symbol"] = self._attribute_by_dimension(recent_trades, "symbol")

            # By timeframe
            result["by_timeframe"] = self._attribute_by_dimension(recent_trades, "timeframe")

            # Time-based analysis
            result["by_hour"] = self._time_analysis(recent_trades, "hour")
            result["by_day"] = self._time_analysis(recent_trades, "weekday")

            # Equity curve
            result["equity_curve"] = self._build_equity_curve(recent_trades, account_balance)

            # Risk metrics
            result["risk_metrics"] = self._risk_metrics(recent_trades, account_balance)

            # Best/worst trades
            result["extremes"] = self._find_extremes(recent_trades)

            logger.info(
                f"PerformanceAttribution: {len(recent_trades)} trades "
                f"WR={result['overall']['win_rate']:.2%} "
                f"PF={result['overall']['profit_factor']:.2f}"
            )
            return result

        except Exception as exc:
            logger.error(f"Performance attribution error: {exc}", exc_info=True)
            return {"valid": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Overall Metrics
    # ------------------------------------------------------------------

    def _overall_metrics(
        self, trades: List[Dict], account_balance: float
    ) -> Dict[str, Any]:
        """Calculate overall performance metrics."""
        pnls = [float(t.get("pnl", 0)) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        total_pnl = sum(pnls)
        win_rate = len(wins) / len(pnls) if pnls else 0
        avg_win = np.mean(wins) if wins else 0
        avg_loss = abs(np.mean(losses)) if losses else 0
        profit_factor = sum(wins) / abs(sum(losses)) if losses else float("inf")
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

        # R-multiples
        r_multiples = [float(t.get("r_multiple", t.get("risk_reward", 0))) for t in trades]
        avg_r = np.mean(r_multiples) if r_multiples else 0

        return {
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl / account_balance * 100, 4) if account_balance > 0 else 0,
            "win_rate": round(win_rate, 4),
            "total_trades": len(trades),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else 999.0,
            "expectancy": round(expectancy, 4),
            "avg_r_multiple": round(avg_r, 4),
            "largest_win": round(max(wins), 2) if wins else 0,
            "largest_loss": round(min(losses), 2) if losses else 0,
        }

    # ------------------------------------------------------------------
    # Dimensional Attribution
    # ------------------------------------------------------------------

    def _attribute_by_dimension(
        self, trades: List[Dict], dimension: str
    ) -> Dict[str, Dict]:
        """Attribute performance by a given dimension (strategy, regime, etc.)."""
        groups: Dict[str, List] = defaultdict(list)

        for trade in trades:
            key = str(trade.get(dimension, "UNKNOWN"))
            groups[key].append(trade)

        result = {}
        for key, group_trades in groups.items():
            pnls = [float(t.get("pnl", 0)) for t in group_trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]

            result[key] = {
                "trades": len(group_trades),
                "total_pnl": round(sum(pnls), 2),
                "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0,
                "avg_pnl": round(np.mean(pnls), 4) if pnls else 0,
                "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if losses and sum(losses) != 0 else 999.0,
            }

        return result

    # ------------------------------------------------------------------
    # Time Analysis
    # ------------------------------------------------------------------

    def _time_analysis(
        self, trades: List[Dict], granularity: str
    ) -> Dict[str, Dict]:
        """Analyze performance by time of day or day of week."""
        groups: Dict[str, List] = defaultdict(list)

        for trade in trades:
            dt = self._parse_dt(trade.get("created_at", ""))
            if granularity == "hour":
                key = str(dt.hour)
            elif granularity == "weekday":
                key = dt.strftime("%A")
            else:
                key = "UNKNOWN"
            groups[key].append(trade)

        result = {}
        for key, group_trades in groups.items():
            pnls = [float(t.get("pnl", 0)) for t in group_trades]
            wins = [p for p in pnls if p > 0]
            result[key] = {
                "trades": len(group_trades),
                "total_pnl": round(sum(pnls), 2),
                "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0,
            }

        return result

    # ------------------------------------------------------------------
    # Equity Curve
    # ------------------------------------------------------------------

    def _build_equity_curve(
        self, trades: List[Dict], starting_balance: float
    ) -> List[Dict]:
        """Build equity curve from trade history."""
        sorted_trades = sorted(
            trades,
            key=lambda t: self._parse_dt(t.get("closed_at", t.get("created_at", ""))),
        )

        curve = []
        balance = starting_balance
        for trade in sorted_trades:
            pnl = float(trade.get("pnl", 0))
            balance += pnl
            curve.append({
                "timestamp": trade.get("closed_at", trade.get("created_at", "")),
                "balance": round(balance, 2),
                "pnl": round(pnl, 2),
                "symbol": trade.get("symbol", ""),
            })

        return curve

    # ------------------------------------------------------------------
    # Risk Metrics
    # ------------------------------------------------------------------

    def _risk_metrics(
        self, trades: List[Dict], account_balance: float
    ) -> Dict[str, Any]:
        """Calculate risk-adjusted performance metrics."""
        pnls = [float(t.get("pnl", 0)) for t in trades]
        if not pnls:
            return {}

        pnl_series = pd.Series(pnls)
        returns = pnl_series / account_balance

        # Sharpe (annualized, assuming daily trades)
        sharpe = float(returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0

        # Sortino (downside deviation)
        downside = returns[returns < 0]
        sortino = float(returns.mean() / downside.std() * np.sqrt(252)) if len(downside) > 0 and downside.std() > 0 else 0

        # Max drawdown
        cumulative = (1 + returns).cumprod()
        rolling_max = cumulative.cummax()
        drawdown = (cumulative - rolling_max) / rolling_max
        max_dd = float(drawdown.min()) * 100

        # Calmar
        annual_return = float(returns.mean() * 252)
        calmar = annual_return / abs(max_dd / 100) if max_dd != 0 else 0

        return {
            "sharpe_ratio": round(sharpe, 4),
            "sortino_ratio": round(sortino, 4),
            "calmar_ratio": round(calmar, 4),
            "max_drawdown_pct": round(max_dd, 4),
            "annualized_return_pct": round(annual_return * 100, 4),
            "volatility_pct": round(float(returns.std() * np.sqrt(252)) * 100, 4),
        }

    # ------------------------------------------------------------------
    # Extremes
    # ------------------------------------------------------------------

    def _find_extremes(self, trades: List[Dict]) -> Dict[str, Any]:
        """Find best and worst trades."""
        if not trades:
            return {}

        sorted_by_pnl = sorted(trades, key=lambda t: float(t.get("pnl", 0)))
        return {
            "best_trade": {
                "pnl": float(sorted_by_pnl[-1].get("pnl", 0)),
                "symbol": sorted_by_pnl[-1].get("symbol", ""),
                "strategy": sorted_by_pnl[-1].get("strategy", ""),
            },
            "worst_trade": {
                "pnl": float(sorted_by_pnl[0].get("pnl", 0)),
                "symbol": sorted_by_pnl[0].get("symbol", ""),
                "strategy": sorted_by_pnl[0].get("strategy", ""),
            },
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_dt(self, dt_str: str) -> datetime:
        """Parse datetime string to datetime object."""
        try:
            if not dt_str:
                return datetime.now(timezone.utc)
            dt = datetime.fromisoformat(str(dt_str).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return datetime.now(timezone.utc)


# Global instance
performance_attribution = PerformanceAttribution()

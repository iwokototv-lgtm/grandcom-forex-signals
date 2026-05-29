"""
Portfolio Manager
Centralized portfolio state management and risk oversight
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


class PortfolioManager:
    """
    Centralized Portfolio Management System.

    Manages:
    - Open positions tracking
    - Portfolio-level risk metrics
    - Exposure limits enforcement
    - Correlation-adjusted sizing
    - Daily/weekly P&L tracking
    - Position lifecycle management
    """

    def __init__(
        self,
        max_open_positions: int = 5,
        max_correlated_positions: int = 2,
        max_total_risk_pct: float = 10.0,
        max_single_risk_pct: float = 2.0,
        correlation_threshold: float = 0.7,
    ):
        self.max_open_positions = max_open_positions
        self.max_correlated_positions = max_correlated_positions
        self.max_total_risk_pct = max_total_risk_pct
        self.max_single_risk_pct = max_single_risk_pct
        self.correlation_threshold = correlation_threshold
        self.version = "3.0.0"

        # State
        self._positions: Dict[str, Dict] = {}
        self._closed_positions: List[Dict] = []
        self._daily_pnl: float = 0.0
        self._total_pnl: float = 0.0

    # ------------------------------------------------------------------
    # Position Management
    # ------------------------------------------------------------------

    def open_position(
        self,
        trade_id: str,
        symbol: str,
        direction: str,
        entry_price: float,
        lot_size: float,
        sl_price: float,
        tp_levels: List[float],
        strategy: str = "UNKNOWN",
        dollar_risk: float = 0.0,
        metadata: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Open a new position."""
        position = {
            "id": trade_id,
            "symbol": symbol,
            "direction": direction.upper(),
            "entry_price": round(entry_price, 5),
            "current_price": round(entry_price, 5),
            "lot_size": lot_size,
            "sl_price": round(sl_price, 5),
            "tp_levels": [round(tp, 5) for tp in tp_levels],
            "strategy": strategy,
            "dollar_risk": round(dollar_risk, 2),
            "unrealized_pnl": 0.0,
            "status": "OPEN",
            "opened_at": datetime.utcnow().isoformat(),
            "metadata": metadata or {},
        }

        self._positions[trade_id] = position
        logger.info(f"PortfolioManager: opened {trade_id} {direction} {symbol} @ {entry_price}")
        return position

    def close_position(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str = "MANUAL",
    ) -> Optional[Dict]:
        """Close an open position."""
        position = self._positions.pop(trade_id, None)
        if not position:
            return None

        # Calculate P&L
        direction = position["direction"]
        entry = position["entry_price"]
        lots = position["lot_size"]
        contract_size = 100  # oz per lot for gold

        if direction == "BUY":
            pnl = (exit_price - entry) * lots * contract_size
        else:
            pnl = (entry - exit_price) * lots * contract_size

        position["exit_price"] = round(exit_price, 5)
        position["pnl"] = round(pnl, 2)
        position["exit_reason"] = exit_reason
        position["closed_at"] = datetime.utcnow().isoformat()
        position["status"] = "CLOSED"

        self._closed_positions.append(position)
        self._daily_pnl += pnl
        self._total_pnl += pnl

        # Keep last 500 closed
        if len(self._closed_positions) > 500:
            self._closed_positions = self._closed_positions[-500:]

        logger.info(
            f"PortfolioManager: closed {trade_id} @ {exit_price} "
            f"pnl=${pnl:.2f} reason={exit_reason}"
        )
        return position

    def update_prices(self, price_updates: Dict[str, float]) -> None:
        """Update current prices for all open positions."""
        for trade_id, position in self._positions.items():
            symbol = position["symbol"]
            if symbol in price_updates:
                current_price = price_updates[symbol]
                position["current_price"] = round(current_price, 5)

                # Update unrealized P&L
                direction = position["direction"]
                entry = position["entry_price"]
                lots = position["lot_size"]
                contract_size = 100

                if direction == "BUY":
                    pnl = (current_price - entry) * lots * contract_size
                else:
                    pnl = (entry - current_price) * lots * contract_size

                position["unrealized_pnl"] = round(pnl, 2)

    # ------------------------------------------------------------------
    # Risk Checks
    # ------------------------------------------------------------------

    def can_open_position(
        self,
        symbol: str,
        dollar_risk: float,
        account_balance: float,
        correlated_symbols: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Check if a new position can be opened given current portfolio state.

        Returns:
            Dict with approved bool and reason
        """
        # Max positions check
        if len(self._positions) >= self.max_open_positions:
            return {
                "approved": False,
                "reason": f"MAX_POSITIONS_REACHED ({len(self._positions)}/{self.max_open_positions})",
            }

        # Total risk check
        total_risk = sum(p.get("dollar_risk", 0) for p in self._positions.values())
        new_total_risk = total_risk + dollar_risk
        new_total_risk_pct = (new_total_risk / account_balance) * 100 if account_balance > 0 else 0

        if new_total_risk_pct > self.max_total_risk_pct:
            return {
                "approved": False,
                "reason": f"TOTAL_RISK_EXCEEDED ({new_total_risk_pct:.2f}% > {self.max_total_risk_pct}%)",
            }

        # Single trade risk check
        single_risk_pct = (dollar_risk / account_balance) * 100 if account_balance > 0 else 0
        if single_risk_pct > self.max_single_risk_pct:
            return {
                "approved": False,
                "reason": f"SINGLE_RISK_EXCEEDED ({single_risk_pct:.2f}% > {self.max_single_risk_pct}%)",
            }

        # Correlated positions check
        if correlated_symbols:
            correlated_open = sum(
                1 for p in self._positions.values()
                if p["symbol"] in correlated_symbols
            )
            if correlated_open >= self.max_correlated_positions:
                return {
                    "approved": False,
                    "reason": f"CORRELATED_POSITIONS_LIMIT ({correlated_open}/{self.max_correlated_positions})",
                }

        return {
            "approved": True,
            "reason": "APPROVED",
            "current_positions": len(self._positions),
            "current_risk_pct": round((total_risk / account_balance) * 100, 4) if account_balance > 0 else 0,
            "new_total_risk_pct": round(new_total_risk_pct, 4),
        }

    # ------------------------------------------------------------------
    # Portfolio State
    # ------------------------------------------------------------------

    def get_state(self, account_balance: float = 10000.0) -> Dict[str, Any]:
        """Get current portfolio state."""
        open_positions = list(self._positions.values())
        total_unrealized = sum(p.get("unrealized_pnl", 0) for p in open_positions)
        total_risk = sum(p.get("dollar_risk", 0) for p in open_positions)

        return {
            "open_positions": len(open_positions),
            "positions": open_positions,
            "total_unrealized_pnl": round(total_unrealized, 2),
            "total_risk": round(total_risk, 2),
            "total_risk_pct": round((total_risk / account_balance) * 100, 4) if account_balance > 0 else 0,
            "daily_pnl": round(self._daily_pnl, 2),
            "total_pnl": round(self._total_pnl, 2),
            "symbols_open": list({p["symbol"] for p in open_positions}),
            "timestamp": datetime.utcnow().isoformat(),
            "version": self.version,
        }

    def get_open_positions(self) -> List[Dict]:
        """Get all open positions."""
        return list(self._positions.values())

    def get_closed_positions(self, limit: int = 50) -> List[Dict]:
        """Get recent closed positions."""
        return self._closed_positions[-limit:]

    def reset_daily_pnl(self) -> None:
        """Reset daily P&L counter."""
        self._daily_pnl = 0.0
        logger.info("PortfolioManager: daily P&L reset")

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def get_analytics(self) -> Dict[str, Any]:
        """Get portfolio analytics."""
        closed = self._closed_positions
        if not closed:
            return {"message": "No closed positions"}

        pnls = [p.get("pnl", 0) for p in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        return {
            "total_trades": len(closed),
            "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0,
            "total_pnl": round(sum(pnls), 2),
            "avg_win": round(np.mean(wins), 2) if wins else 0,
            "avg_loss": round(abs(np.mean(losses)), 2) if losses else 0,
            "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if losses and sum(losses) != 0 else 999.0,
            "by_symbol": self._group_by(closed, "symbol"),
            "by_strategy": self._group_by(closed, "strategy"),
        }

    def _group_by(self, positions: List[Dict], key: str) -> Dict[str, Any]:
        """Group positions by a key and compute metrics."""
        groups: Dict[str, List] = {}
        for p in positions:
            k = str(p.get(key, "UNKNOWN"))
            groups.setdefault(k, []).append(float(p.get("pnl", 0)))

        return {
            k: {
                "trades": len(v),
                "total_pnl": round(sum(v), 2),
                "win_rate": round(sum(1 for p in v if p > 0) / len(v), 4),
            }
            for k, v in groups.items()
        }


# Global instance
portfolio_manager = PortfolioManager()

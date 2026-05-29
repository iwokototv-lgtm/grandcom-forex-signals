"""
Portfolio Manager — v3.0
Multi-strategy, multi-pair portfolio orchestration.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .correlation_engine import CorrelationEngine
from .correlation_engine import correlation_engine as correlation_engine_singleton
from .risk_parity_allocator import RiskParityAllocator, risk_parity_allocator
from .drawdown_recovery import DrawdownRecoveryManager, drawdown_recovery_manager
from .volatility_adjuster import VolatilityAdjuster, volatility_adjuster

logger = logging.getLogger(__name__)


class PortfolioManager:
    """
    Manages the overall portfolio state across all strategies and pairs.

    Responsibilities:
    - Track open positions
    - Enforce portfolio-level risk limits
    - Coordinate between correlation, risk-parity, and drawdown managers
    - Approve or reject new signals based on portfolio state
    - Provide portfolio-level reporting
    """

    def __init__(
        self,
        max_open_positions: int = 5,
        max_portfolio_risk_pct: float = 0.10,
        max_single_pair_exposure: float = 0.40,
        correlation_engine: CorrelationEngine | None = None,
        risk_parity: RiskParityAllocator | None = None,
        dd_manager: DrawdownRecoveryManager | None = None,
        vol_adjuster: VolatilityAdjuster | None = None,
    ) -> None:
        self.max_open_positions = max_open_positions
        self.max_portfolio_risk_pct = max_portfolio_risk_pct
        self.max_single_pair_exposure = max_single_pair_exposure

        self.corr_engine = correlation_engine or correlation_engine_singleton
        self.risk_parity = risk_parity or risk_parity_allocator
        self.dd_manager = dd_manager or drawdown_recovery_manager
        self.vol_adjuster = vol_adjuster or volatility_adjuster

        self._open_positions: list[dict] = []
        self._account_equity: float = 100_000.0

    # ------------------------------------------------------------------
    # Portfolio State
    # ------------------------------------------------------------------

    def set_equity(self, equity: float) -> None:
        """Update current account equity."""
        self._account_equity = equity

    def add_position(self, position: dict) -> None:
        """Register a new open position."""
        self._open_positions.append(position)
        logger.info(
            f"[Portfolio] Position added: {position.get('symbol')} "
            f"{position.get('direction')} lots={position.get('lots')}"
        )

    def remove_position(self, trade_id: str) -> bool:
        """Remove a closed position by trade_id."""
        before = len(self._open_positions)
        self._open_positions = [
            p for p in self._open_positions if p.get("trade_id") != trade_id
        ]
        removed = len(self._open_positions) < before
        if removed:
            logger.info(f"[Portfolio] Position removed: {trade_id}")
        return removed

    def get_open_positions(self) -> list[dict]:
        return list(self._open_positions)

    # ------------------------------------------------------------------
    # Signal Approval
    # ------------------------------------------------------------------

    def approve_signal(
        self,
        symbol: str,
        direction: str,
        confidence: float,
        strategy: str,
        lots: float = 0.01,
    ) -> dict[str, Any]:
        """
        Evaluate whether a new signal should be approved for execution.

        Checks:
        1. Drawdown recovery — can we trade at all?
        2. Max open positions
        3. Single-pair exposure limit
        4. Correlation with existing positions
        5. Portfolio risk budget

        Returns:
            dict with approved (bool), reason, and adjusted_lots.
        """
        # 1. Drawdown check
        dd_status = self.dd_manager.get_status()
        if not dd_status["can_trade"]:
            return self._reject(
                f"Drawdown block: {dd_status.get('pause_reason', 'unknown')}"
            )

        # 2. Max positions
        if len(self._open_positions) >= self.max_open_positions:
            return self._reject(
                f"Max open positions reached ({self.max_open_positions})"
            )

        # 3. Single-pair exposure
        pair_count = sum(
            1 for p in self._open_positions
            if p.get("symbol") == symbol or p.get("pair") == symbol
        )
        pair_exposure = pair_count / max(len(self._open_positions), 1)
        if pair_exposure >= self.max_single_pair_exposure and len(self._open_positions) > 0:
            return self._reject(
                f"Single-pair exposure limit: {symbol} already at {pair_exposure:.0%}"
            )

        # 4. Correlation check
        corr_check = self.corr_engine.is_correlated_with_open(
            symbol, self._open_positions, direction
        )
        if not corr_check["allowed"]:
            return self._reject(
                f"Correlation violation: {symbol} correlated with "
                f"{corr_check['correlated_pairs']} "
                f"(max={corr_check['max_correlation']:.2f})"
            )

        # 5. USD cluster check
        usd_check = self.corr_engine.usd_cluster_exposure(self._open_positions)
        if usd_check["usd_cluster_full"] and symbol in self.corr_engine.USD_CLUSTER:
            return self._reject("USD cluster exposure limit reached")
        if usd_check["gold_cluster_full"] and symbol in self.corr_engine.GOLD_CLUSTER:
            return self._reject("Gold cluster exposure limit reached")

        # 6. Apply drawdown position scale
        dd_scale = dd_status.get("position_scale", 1.0)
        adjusted_lots = round(lots * dd_scale, 2)
        adjusted_lots = max(0.01, adjusted_lots)

        return {
            "approved": True,
            "reason": "All portfolio checks passed",
            "adjusted_lots": adjusted_lots,
            "dd_scale": dd_scale,
            "dd_regime": dd_status.get("drawdown_regime", "NORMAL"),
            "correlation_check": corr_check,
            "usd_exposure": usd_check,
            "open_positions": len(self._open_positions),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Portfolio Report
    # ------------------------------------------------------------------

    def portfolio_report(self) -> dict[str, Any]:
        """Return a comprehensive portfolio status report."""
        dd_status = self.dd_manager.get_status()
        usd_exposure = self.corr_engine.usd_cluster_exposure(self._open_positions)

        symbols = list({p.get("symbol", p.get("pair", "")) for p in self._open_positions})
        corr_matrix = self.corr_engine.portfolio_correlation_matrix(symbols) if symbols else {}

        total_lots = sum(p.get("lots", 0.0) for p in self._open_positions)
        total_risk_usd = sum(p.get("risk_usd", 0.0) for p in self._open_positions)
        portfolio_risk_pct = total_risk_usd / self._account_equity if self._account_equity > 0 else 0.0

        return {
            "account_equity": round(self._account_equity, 2),
            "open_positions": len(self._open_positions),
            "max_open_positions": self.max_open_positions,
            "total_lots": round(total_lots, 2),
            "total_risk_usd": round(total_risk_usd, 2),
            "portfolio_risk_pct": round(portfolio_risk_pct, 4),
            "max_portfolio_risk_pct": self.max_portfolio_risk_pct,
            "drawdown": dd_status,
            "usd_exposure": usd_exposure,
            "correlation_matrix": corr_matrix,
            "positions": self._open_positions,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _reject(reason: str) -> dict[str, Any]:
        return {
            "approved": False,
            "reason": reason,
            "adjusted_lots": 0.0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# Module-level singleton
portfolio_manager = PortfolioManager()

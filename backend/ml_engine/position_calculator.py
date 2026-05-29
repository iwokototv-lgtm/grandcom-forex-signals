"""
Position Calculator — v3.0
Unified position sizing combining risk parity, volatility adjustment,
and drawdown recovery into a single sizing decision.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import pandas as pd

from .risk_parity_allocator import RiskParityAllocator
from .volatility_adjuster import VolatilityAdjuster
from .drawdown_recovery import DrawdownRecoveryManager

logger = logging.getLogger(__name__)


class PositionCalculator:
    """
    Unified position sizing engine.

    Sizing pipeline:
    1. Base size from risk-parity weight and account equity
    2. Volatility adjustment (scale up/down based on current vol)
    3. Drawdown recovery scaling (reduce during drawdown)
    4. Hard limits (min/max lot sizes)
    5. Final validation

    All three components are injected for testability.
    """

    def __init__(
        self,
        risk_parity: RiskParityAllocator | None = None,
        vol_adjuster: VolatilityAdjuster | None = None,
        dd_manager: DrawdownRecoveryManager | None = None,
        min_lot: float = 0.01,
        max_lot: float = 10.0,
    ) -> None:
        self.risk_parity = risk_parity or RiskParityAllocator()
        self.vol_adjuster = vol_adjuster or VolatilityAdjuster()
        self.dd_manager = dd_manager or DrawdownRecoveryManager()
        self.min_lot = min_lot
        self.max_lot = max_lot

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate(
        self,
        symbol: str,
        signal: str,
        entry_price: float,
        sl_price: float,
        account_equity: float,
        df: pd.DataFrame,
        risk_weight: float = 1.0,
        base_risk_pct: float = 0.01,
    ) -> dict[str, Any]:
        """
        Calculate final position size for a trade.

        Args:
            symbol:         Trading pair (e.g. "XAUUSD").
            signal:         "BUY" or "SELL".
            entry_price:    Proposed entry price.
            sl_price:       Stop-loss price.
            account_equity: Current account equity in USD.
            df:             OHLCV DataFrame for volatility calculation.
            risk_weight:    Risk-parity weight (0-1, default 1.0 = full weight).
            base_risk_pct:  Base risk per trade as fraction of equity.

        Returns:
            dict with lots, risk_usd, risk_pct, sl_distance, breakdown, and metadata.
        """
        try:
            # 1. Validate inputs
            sl_distance = abs(entry_price - sl_price)
            if sl_distance <= 0 or entry_price <= 0 or account_equity <= 0:
                return self._error_result("Invalid price/SL/equity inputs")

            # 2. Base risk capital
            risk_capital = account_equity * base_risk_pct * risk_weight

            # 3. Base lots from risk capital
            # For gold: lots = risk_capital / (sl_distance * contract_size)
            # Gold contract size ≈ 100 oz, but we use pip-based sizing
            base_lots = risk_capital / (sl_distance * 100)
            base_lots = round(base_lots, 2)

            # 4. Volatility adjustment
            vol_adj = self.vol_adjuster.compute_adjustment(df, symbol)
            vol_scale = vol_adj.get("scale_factor", 1.0)
            vol_lots = round(base_lots * vol_scale, 2)

            # 5. Drawdown recovery adjustment
            dd_status = self.dd_manager.get_status()
            if not dd_status["can_trade"]:
                return {
                    "lots": 0.0,
                    "risk_usd": 0.0,
                    "risk_pct": 0.0,
                    "sl_distance": round(sl_distance, 4),
                    "can_trade": False,
                    "pause_reason": dd_status.get("pause_reason"),
                    "breakdown": {
                        "base_lots": base_lots,
                        "vol_scale": vol_scale,
                        "dd_scale": 0.0,
                        "final_lots": 0.0,
                    },
                    "timestamp": datetime.utcnow().isoformat(),
                }

            dd_scale = dd_status.get("position_scale", 1.0)
            final_lots = round(vol_lots * dd_scale, 2)

            # 6. Apply hard limits
            final_lots = max(self.min_lot, min(self.max_lot, final_lots))

            # 7. Compute actual risk
            actual_risk_usd = final_lots * sl_distance * 100
            actual_risk_pct = actual_risk_usd / account_equity if account_equity > 0 else 0.0

            return {
                "lots": final_lots,
                "risk_usd": round(actual_risk_usd, 2),
                "risk_pct": round(actual_risk_pct, 4),
                "sl_distance": round(sl_distance, 4),
                "can_trade": True,
                "pause_reason": None,
                "breakdown": {
                    "base_lots": base_lots,
                    "vol_scale": round(vol_scale, 3),
                    "vol_regime": vol_adj.get("vol_regime", "NORMAL"),
                    "dd_scale": round(dd_scale, 3),
                    "dd_regime": dd_status.get("drawdown_regime", "NORMAL"),
                    "final_lots": final_lots,
                },
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as exc:
            logger.error(f"[PositionCalc] Error for {symbol}: {exc}")
            return self._error_result(str(exc))

    def quick_size(
        self,
        account_equity: float,
        risk_pct: float,
        sl_distance: float,
    ) -> float:
        """
        Simple position size calculation without adjustments.
        Returns lots.
        """
        if sl_distance <= 0 or account_equity <= 0:
            return self.min_lot
        risk_capital = account_equity * risk_pct
        lots = risk_capital / (sl_distance * 100)
        return max(self.min_lot, min(self.max_lot, round(lots, 2)))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _error_result(self, reason: str) -> dict[str, Any]:
        return {
            "lots": self.min_lot,
            "risk_usd": 0.0,
            "risk_pct": 0.0,
            "sl_distance": 0.0,
            "can_trade": False,
            "pause_reason": reason,
            "breakdown": {},
            "error": reason,
            "timestamp": datetime.utcnow().isoformat(),
        }


# Module-level singleton (uses default sub-components)
position_calculator = PositionCalculator()

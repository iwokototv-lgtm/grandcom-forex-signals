"""
Position Calculator
Comprehensive position sizing with multiple methods
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, Optional, List
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class PositionCalculator:
    """
    Institutional Position Sizing Calculator.

    Methods:
    1. Fixed Risk % — Risk a fixed % of account per trade
    2. ATR-based — Size based on ATR stop distance
    3. Kelly Criterion — Optimal sizing based on win rate and R:R
    4. Volatility-Adjusted — Scale by realized volatility
    5. Risk Parity — Equal risk contribution

    Constraints:
    - Maximum position size (lots)
    - Minimum position size (lots)
    - Maximum account risk per trade
    - Maximum total open risk
    """

    def __init__(
        self,
        default_risk_pct: float = 2.0,
        max_risk_pct: float = 5.0,
        min_lot: float = 0.01,
        max_lot: float = 10.0,
        pip_value_per_lot: float = 1.0,  # USD per pip per lot (gold: $1 per pip per lot)
        contract_size: float = 100.0,    # oz per lot for gold
    ):
        self.default_risk_pct = default_risk_pct
        self.max_risk_pct = max_risk_pct
        self.min_lot = min_lot
        self.max_lot = max_lot
        self.pip_value_per_lot = pip_value_per_lot
        self.contract_size = contract_size
        self.version = "3.0.0"

    # ------------------------------------------------------------------
    # Main Calculation
    # ------------------------------------------------------------------

    def calculate(
        self,
        account_balance: float,
        entry_price: float,
        sl_price: float,
        symbol: str = "XAUUSD",
        method: str = "fixed_risk",
        risk_pct: Optional[float] = None,
        win_rate: Optional[float] = None,
        avg_rr: Optional[float] = None,
        volatility_multiplier: float = 1.0,
        risk_parity_weight: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Calculate position size.

        Args:
            account_balance: Account balance in USD
            entry_price: Trade entry price
            sl_price: Stop loss price
            symbol: Trading symbol
            method: Sizing method
            risk_pct: Risk percentage (overrides default)
            win_rate: Historical win rate (for Kelly)
            avg_rr: Average risk/reward ratio (for Kelly)
            volatility_multiplier: Vol adjustment factor (from VolatilityAdjustment)
            risk_parity_weight: Weight from risk parity allocation

        Returns:
            Position size recommendation with full breakdown
        """
        try:
            risk_pct = min(risk_pct or self.default_risk_pct, self.max_risk_pct)
            stop_distance = abs(entry_price - sl_price)

            if stop_distance <= 0:
                return {"error": "Invalid stop distance", "valid": False}

            # Calculate using selected method
            if method == "fixed_risk":
                lots = self._fixed_risk(account_balance, risk_pct, stop_distance, entry_price)
            elif method == "atr_based":
                lots = self._atr_based(account_balance, risk_pct, stop_distance, entry_price)
            elif method == "kelly":
                lots = self._kelly_criterion(
                    account_balance, risk_pct, stop_distance, entry_price,
                    win_rate or 0.5, avg_rr or 2.0
                )
            elif method == "volatility_adjusted":
                base = self._fixed_risk(account_balance, risk_pct, stop_distance, entry_price)
                lots = base * volatility_multiplier
            elif method == "risk_parity":
                base = self._fixed_risk(account_balance, risk_pct, stop_distance, entry_price)
                lots = base * risk_parity_weight
            else:
                lots = self._fixed_risk(account_balance, risk_pct, stop_distance, entry_price)

            # Apply all adjustments
            lots = lots * volatility_multiplier * risk_parity_weight

            # Enforce constraints
            lots = max(self.min_lot, min(self.max_lot, round(lots, 2)))

            # Calculate risk metrics
            dollar_risk = stop_distance * lots * self.contract_size
            risk_pct_actual = (dollar_risk / account_balance) * 100 if account_balance > 0 else 0

            result = {
                "valid": True,
                "symbol": symbol,
                "method": method,
                "lots": lots,
                "entry_price": round(entry_price, 5),
                "sl_price": round(sl_price, 5),
                "stop_distance": round(stop_distance, 5),
                "stop_distance_pct": round(stop_distance / entry_price * 100, 4),
                "dollar_risk": round(dollar_risk, 2),
                "risk_pct_actual": round(risk_pct_actual, 4),
                "risk_pct_target": risk_pct,
                "account_balance": round(account_balance, 2),
                "adjustments": {
                    "volatility_multiplier": round(volatility_multiplier, 4),
                    "risk_parity_weight": round(risk_parity_weight, 4),
                },
                "constraints": {
                    "min_lot": self.min_lot,
                    "max_lot": self.max_lot,
                    "max_risk_pct": self.max_risk_pct,
                },
                "timestamp": datetime.utcnow().isoformat(),
                "version": self.version,
            }

            logger.info(
                f"PositionCalc [{symbol}/{method}]: lots={lots} "
                f"risk=${dollar_risk:.2f} ({risk_pct_actual:.2f}%)"
            )
            return result

        except Exception as exc:
            logger.error(f"Position calculation error: {exc}", exc_info=True)
            return {"valid": False, "error": str(exc), "lots": self.min_lot}

    # ------------------------------------------------------------------
    # Sizing Methods
    # ------------------------------------------------------------------

    def _fixed_risk(
        self,
        balance: float,
        risk_pct: float,
        stop_distance: float,
        entry_price: float,
    ) -> float:
        """
        Fixed Risk % sizing.
        lots = (balance * risk_pct%) / (stop_distance * contract_size)
        """
        dollar_risk = balance * (risk_pct / 100)
        lots = dollar_risk / (stop_distance * self.contract_size)
        return lots

    def _atr_based(
        self,
        balance: float,
        risk_pct: float,
        stop_distance: float,
        entry_price: float,
    ) -> float:
        """ATR-based sizing (same as fixed risk when stop is ATR-derived)."""
        return self._fixed_risk(balance, risk_pct, stop_distance, entry_price)

    def _kelly_criterion(
        self,
        balance: float,
        risk_pct: float,
        stop_distance: float,
        entry_price: float,
        win_rate: float,
        avg_rr: float,
    ) -> float:
        """
        Kelly Criterion sizing.
        f* = (p * b - q) / b
        where p = win rate, q = 1-p, b = avg R:R
        Uses half-Kelly for safety.
        """
        p = max(0.1, min(0.9, win_rate))
        q = 1 - p
        b = max(0.5, avg_rr)

        kelly_f = (p * b - q) / b
        half_kelly = max(0, kelly_f * 0.5)  # Half-Kelly for safety

        # Cap at max risk
        kelly_risk_pct = min(half_kelly * 100, risk_pct)
        return self._fixed_risk(balance, kelly_risk_pct, stop_distance, entry_price)

    # ------------------------------------------------------------------
    # Multi-Trade Risk Check
    # ------------------------------------------------------------------

    def check_portfolio_risk(
        self,
        open_positions: List[Dict],
        new_trade: Dict,
        account_balance: float,
        max_total_risk_pct: float = 10.0,
    ) -> Dict[str, Any]:
        """
        Check if adding a new trade would exceed total portfolio risk.

        Args:
            open_positions: List of open position dicts with dollar_risk
            new_trade: New trade dict with dollar_risk
            account_balance: Account balance
            max_total_risk_pct: Maximum total portfolio risk %

        Returns:
            Risk check result
        """
        existing_risk = sum(float(p.get("dollar_risk", 0)) for p in open_positions)
        new_risk = float(new_trade.get("dollar_risk", 0))
        total_risk = existing_risk + new_risk
        total_risk_pct = (total_risk / account_balance) * 100 if account_balance > 0 else 0

        approved = total_risk_pct <= max_total_risk_pct

        return {
            "approved": approved,
            "existing_risk": round(existing_risk, 2),
            "new_trade_risk": round(new_risk, 2),
            "total_risk": round(total_risk, 2),
            "total_risk_pct": round(total_risk_pct, 4),
            "max_allowed_pct": max_total_risk_pct,
            "reason": "APPROVED" if approved else f"EXCEEDS_MAX_RISK ({total_risk_pct:.2f}% > {max_total_risk_pct}%)",
        }

    # ------------------------------------------------------------------
    # TP Levels
    # ------------------------------------------------------------------

    def calculate_tp_levels(
        self,
        entry_price: float,
        sl_price: float,
        direction: str,
        rr_ratios: Optional[List[float]] = None,
    ) -> Dict[str, Any]:
        """
        Calculate take profit levels based on R:R ratios.

        Default: TP1=2R, TP2=3.5R, TP3=5R
        """
        rr_ratios = rr_ratios or [2.0, 3.5, 5.0]
        risk = abs(entry_price - sl_price)

        tps = []
        for rr in rr_ratios:
            if direction.upper() == "BUY":
                tp = entry_price + risk * rr
            else:
                tp = entry_price - risk * rr
            tps.append(round(tp, 5))

        return {
            "tp_levels": tps,
            "rr_ratios": rr_ratios,
            "risk_distance": round(risk, 5),
            "direction": direction.upper(),
        }


# Global instance
position_calculator = PositionCalculator()

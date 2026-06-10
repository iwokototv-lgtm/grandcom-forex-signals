"""
Position Calculator
Fixed 1% risk per trade with ATR-based SL and TP levels.

Design:
  - Fixed 1% risk per trade (no volatility multipliers, no drawdown adjustments)
  - SL = Entry ± (1.5 × ATR)
  - TP1 = Entry ± (0.5 × ATR)  — quick profit
  - TP2 = Entry ± (1.0 × ATR)  — medium target
  - TP3 = Entry ± (1.5 × ATR)  — extended target (1:1 R:R)

Rationale: Complexity in position sizing was adding noise, not edge.
A simple fixed-risk approach is more robust and easier to diagnose.
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, Optional, List
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Default risk per trade — fixed, no multipliers
DEFAULT_RISK_PCT = 1.0

# ATR multipliers for SL and TP levels
ATR_SL_MULT = 1.5
ATR_TP1_MULT = 0.5
ATR_TP2_MULT = 1.0
ATR_TP3_MULT = 1.5


class PositionCalculator:
    """
    Fixed-Risk Position Sizing Calculator v3.1

    Core method: fixed_risk_atr
      - Risk exactly 1% of account balance per trade
      - SL distance = 1.5 × ATR(14)
      - TP1 = 0.5 × ATR (quick profit, 1:3 risk)
      - TP2 = 1.0 × ATR (medium target, 1:1.5 risk)
      - TP3 = 1.5 × ATR (extended target, 1:1 risk)

    Legacy methods (fixed_risk, atr_based, kelly, volatility_adjusted,
    risk_parity) are kept for backward compatibility but all internally
    use the same fixed 1% risk calculation — multipliers are ignored.
    """

    def __init__(
        self,
        default_risk_pct: float = DEFAULT_RISK_PCT,
        max_risk_pct: float = 2.0,          # Hard cap — never risk more than 2%
        min_lot: float = 0.01,
        max_lot: float = 10.0,
        pip_value_per_lot: float = 1.0,
        contract_size: float = 100.0,       # oz per lot for gold
    ):
        self.default_risk_pct = default_risk_pct
        self.max_risk_pct = max_risk_pct
        self.min_lot = min_lot
        self.max_lot = max_lot
        self.pip_value_per_lot = pip_value_per_lot
        self.contract_size = contract_size
        self.version = "3.1.0"

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
        volatility_multiplier: float = 1.0,   # Accepted but ignored — fixed risk
        risk_parity_weight: float = 1.0,       # Accepted but ignored — fixed risk
    ) -> Dict[str, Any]:
        """
        Calculate position size using fixed 1% risk.

        All methods now resolve to fixed_risk with 1% risk.
        Volatility and risk-parity multipliers are accepted for backward
        compatibility but are NOT applied — they were identified as a root
        cause of over-sizing and inconsistent risk.

        Args:
            account_balance: Account balance in USD
            entry_price: Trade entry price
            sl_price: Stop loss price
            symbol: Trading symbol
            method: Sizing method (all resolve to fixed_risk internally)
            risk_pct: Risk % — capped at max_risk_pct (default 1%)
            win_rate: Unused (kept for API compatibility)
            avg_rr: Unused (kept for API compatibility)
            volatility_multiplier: Accepted but ignored
            risk_parity_weight: Accepted but ignored

        Returns:
            Position size recommendation with full breakdown
        """
        try:
            # Always use fixed 1% risk — ignore multipliers
            effective_risk_pct = min(
                risk_pct if risk_pct is not None else self.default_risk_pct,
                self.max_risk_pct,
            )
            stop_distance = abs(entry_price - sl_price)

            if stop_distance <= 0:
                return {"error": "Invalid stop distance", "valid": False}

            # Fixed risk calculation — no multipliers applied
            lots = self._fixed_risk(account_balance, effective_risk_pct, stop_distance, entry_price)

            # Enforce constraints
            lots = max(self.min_lot, min(self.max_lot, round(lots, 2)))

            # Calculate risk metrics
            dollar_risk = stop_distance * lots * self.contract_size
            risk_pct_actual = (dollar_risk / account_balance) * 100 if account_balance > 0 else 0

            result = {
                "valid": True,
                "symbol": symbol,
                "method": "fixed_risk",          # Always fixed_risk
                "lots": lots,
                "entry_price": round(entry_price, 5),
                "sl_price": round(sl_price, 5),
                "stop_distance": round(stop_distance, 5),
                "stop_distance_pct": round(stop_distance / entry_price * 100, 4),
                "dollar_risk": round(dollar_risk, 2),
                "risk_pct_actual": round(risk_pct_actual, 4),
                "risk_pct_target": effective_risk_pct,
                "account_balance": round(account_balance, 2),
                "sizing_note": "Fixed 1% risk — no volatility or drawdown multipliers",
                "constraints": {
                    "min_lot": self.min_lot,
                    "max_lot": self.max_lot,
                    "max_risk_pct": self.max_risk_pct,
                },
                "timestamp": datetime.utcnow().isoformat(),
                "version": self.version,
            }

            logger.info(
                f"PositionCalc [{symbol}]: lots={lots} "
                f"risk=${dollar_risk:.2f} ({risk_pct_actual:.2f}%) fixed_1pct"
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
        atr: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Calculate take profit levels.

        When atr is provided (preferred), uses ATR-based multiples:
          TP1 = 0.5 × ATR  (quick profit)
          TP2 = 1.0 × ATR  (medium target)
          TP3 = 1.5 × ATR  (extended target, 1:1 R:R with 1.5×ATR SL)

        When atr is not provided, falls back to R:R ratios from sl_price.
        Default R:R fallback: [0.33, 0.67, 1.0] (matching ATR-based targets).
        """
        risk = abs(entry_price - sl_price)

        if atr is not None and atr > 0:
            # ATR-based TP levels (preferred)
            atr_mults = [ATR_TP1_MULT, ATR_TP2_MULT, ATR_TP3_MULT]
            tps = []
            for mult in atr_mults:
                if direction.upper() == "BUY":
                    tp = entry_price + atr * mult
                else:
                    tp = entry_price - atr * mult
                tps.append(round(tp, 5))
            used_rr = [round(atr * m / risk, 2) if risk > 0 else 0 for m in atr_mults]
            return {
                "tp_levels": tps,
                "rr_ratios": used_rr,
                "atr_multiples": atr_mults,
                "risk_distance": round(risk, 5),
                "atr": round(atr, 5),
                "direction": direction.upper(),
                "method": "atr_based",
            }

        # R:R fallback
        rr_ratios = rr_ratios or [0.33, 0.67, 1.0]
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
            "method": "rr_ratio",
        }


# Global instance
position_calculator = PositionCalculator()

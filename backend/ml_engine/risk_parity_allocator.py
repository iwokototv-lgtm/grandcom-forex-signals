"""
Risk Parity Allocator — v3.0
Equal risk contribution allocation across strategies and pairs.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class RiskParityAllocator:
    """
    Risk Parity allocation: each strategy/pair contributes equally to
    total portfolio volatility.

    Method:
    1. Estimate volatility (std of returns) for each instrument.
    2. Compute inverse-volatility weights.
    3. Scale weights to sum to 1.0.
    4. Apply regime and drawdown multipliers.
    5. Return per-instrument risk budget (fraction of total risk capital).
    """

    def __init__(
        self,
        lookback: int = 20,
        target_vol: float = 0.10,
        rebalance_threshold: float = 0.05,
    ) -> None:
        self.lookback = lookback
        self.target_vol = target_vol          # Annualised target portfolio vol
        self.rebalance_threshold = rebalance_threshold
        self._last_weights: dict[str, float] = {}
        self._last_rebalance: datetime | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def allocate(
        self,
        instruments: list[str],
        price_histories: dict[str, pd.Series],
        regime_multipliers: dict[str, float] | None = None,
        drawdown_multipliers: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """
        Compute risk-parity weights for a set of instruments.

        Args:
            instruments:          List of instrument identifiers.
            price_histories:      {symbol: pd.Series of close prices}.
            regime_multipliers:   Optional per-instrument regime scaling.
            drawdown_multipliers: Optional per-instrument drawdown scaling.

        Returns:
            dict with weights, risk_budgets, volatilities, and metadata.
        """
        try:
            if not instruments:
                return self._empty_result()

            vols = self._estimate_volatilities(instruments, price_histories)
            raw_weights = self._inverse_vol_weights(instruments, vols)

            # Apply regime multipliers
            if regime_multipliers:
                for sym in instruments:
                    mult = regime_multipliers.get(sym, 1.0)
                    raw_weights[sym] = raw_weights.get(sym, 0.0) * mult

            # Apply drawdown multipliers
            if drawdown_multipliers:
                for sym in instruments:
                    mult = drawdown_multipliers.get(sym, 1.0)
                    raw_weights[sym] = raw_weights.get(sym, 0.0) * mult

            # Re-normalise
            total = sum(raw_weights.values())
            if total > 0:
                weights = {k: v / total for k, v in raw_weights.items()}
            else:
                n = len(instruments)
                weights = {sym: 1.0 / n for sym in instruments}

            # Risk budgets (fraction of target vol)
            risk_budgets = {
                sym: round(w * self.target_vol, 6) for sym, w in weights.items()
            }

            # Check if rebalance is needed
            needs_rebalance = self._needs_rebalance(weights)
            if needs_rebalance:
                self._last_weights = weights.copy()
                self._last_rebalance = datetime.utcnow()

            return {
                "weights": {k: round(v, 4) for k, v in weights.items()},
                "risk_budgets": risk_budgets,
                "volatilities": {k: round(v, 6) for k, v in vols.items()},
                "target_vol": self.target_vol,
                "needs_rebalance": needs_rebalance,
                "last_rebalance": (
                    self._last_rebalance.isoformat()
                    if self._last_rebalance
                    else None
                ),
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as exc:
            logger.error(f"[RiskParity] Allocation error: {exc}")
            n = max(len(instruments), 1)
            equal = 1.0 / n
            return {
                "weights": {sym: equal for sym in instruments},
                "risk_budgets": {sym: equal * self.target_vol for sym in instruments},
                "volatilities": {},
                "error": str(exc),
                "timestamp": datetime.utcnow().isoformat(),
            }

    def position_size_from_weight(
        self,
        symbol: str,
        weight: float,
        account_equity: float,
        price: float,
        atr: float,
        sl_atr_mult: float = 1.5,
    ) -> dict[str, Any]:
        """
        Convert a risk-parity weight into a concrete position size.

        Uses: size = (equity × weight × target_vol) / (atr × sl_mult × price)
        """
        try:
            risk_capital = account_equity * weight * self.target_vol
            sl_distance = atr * sl_atr_mult
            if sl_distance <= 0 or price <= 0:
                return {"lots": 0.0, "risk_usd": 0.0, "error": "Invalid price/ATR"}

            lots = risk_capital / (sl_distance * price)
            lots = max(0.01, round(lots, 2))

            return {
                "symbol": symbol,
                "lots": lots,
                "risk_usd": round(risk_capital, 2),
                "weight": round(weight, 4),
                "sl_distance": round(sl_distance, 4),
            }
        except Exception as exc:
            logger.error(f"[RiskParity] Position size error: {exc}")
            return {"lots": 0.01, "risk_usd": 0.0, "error": str(exc)}

    # ------------------------------------------------------------------
    # Internal Methods
    # ------------------------------------------------------------------

    def _estimate_volatilities(
        self,
        instruments: list[str],
        price_histories: dict[str, pd.Series],
    ) -> dict[str, float]:
        """Estimate annualised return volatility for each instrument."""
        vols: dict[str, float] = {}
        for sym in instruments:
            if sym in price_histories and len(price_histories[sym]) >= 5:
                prices = price_histories[sym].tail(self.lookback + 1)
                returns = prices.pct_change().dropna()
                if len(returns) >= 3:
                    # Annualise: multiply daily vol by sqrt(252)
                    # For 4H data: multiply by sqrt(252 * 6) ≈ sqrt(1512)
                    daily_vol = float(returns.std())
                    ann_vol = daily_vol * np.sqrt(252)
                    vols[sym] = max(ann_vol, 1e-6)
                else:
                    vols[sym] = self.target_vol  # Default
            else:
                vols[sym] = self.target_vol  # Default when no history

        return vols

    def _inverse_vol_weights(
        self,
        instruments: list[str],
        vols: dict[str, float],
    ) -> dict[str, float]:
        """Compute inverse-volatility weights."""
        inv_vols = {sym: 1.0 / max(vols.get(sym, self.target_vol), 1e-6) for sym in instruments}
        total = sum(inv_vols.values())
        if total == 0:
            n = len(instruments)
            return {sym: 1.0 / n for sym in instruments}
        return {sym: v / total for sym, v in inv_vols.items()}

    def _needs_rebalance(self, new_weights: dict[str, float]) -> bool:
        """Check if weights have drifted beyond rebalance threshold."""
        if not self._last_weights:
            return True
        for sym, w in new_weights.items():
            old_w = self._last_weights.get(sym, 0.0)
            if abs(w - old_w) > self.rebalance_threshold:
                return True
        return False

    @staticmethod
    def _empty_result() -> dict[str, Any]:
        return {
            "weights": {},
            "risk_budgets": {},
            "volatilities": {},
            "target_vol": 0.0,
            "needs_rebalance": False,
            "timestamp": datetime.utcnow().isoformat(),
        }


# Module-level singleton
risk_parity_allocator = RiskParityAllocator()

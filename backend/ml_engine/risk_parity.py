"""
Risk Parity Allocation Engine
Equal risk contribution across portfolio assets
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import logging
from scipy.optimize import minimize

logger = logging.getLogger(__name__)


class RiskParityAllocator:
    """
    Risk Parity Portfolio Allocation.

    Allocates capital such that each asset contributes equally to
    total portfolio risk (volatility). This is the foundation of
    institutional risk management.

    Methods:
    - Equal Risk Contribution (ERC) — primary
    - Inverse Volatility — simplified approximation
    - Maximum Diversification — correlation-adjusted
    """

    def __init__(
        self,
        lookback: int = 60,
        rebalance_threshold: float = 0.05,
        min_weight: float = 0.05,
        max_weight: float = 0.60,
    ):
        self.lookback = lookback
        self.rebalance_threshold = rebalance_threshold
        self.min_weight = min_weight
        self.max_weight = max_weight
        self.version = "3.0.0"
        self._last_weights: Optional[Dict[str, float]] = None

    # ------------------------------------------------------------------
    # Main Allocation
    # ------------------------------------------------------------------

    def allocate(
        self,
        returns: pd.DataFrame,
        method: str = "erc",
        current_weights: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        Calculate risk parity weights.

        Args:
            returns: DataFrame of asset returns (columns = assets)
            method: 'erc', 'inverse_vol', or 'max_diversification'
            current_weights: Current portfolio weights for rebalancing check

        Returns:
            Allocation dict with weights, risk contributions, and metrics
        """
        try:
            if returns.empty or len(returns) < 10:
                return {"error": "Insufficient return data", "valid": False}

            # Use lookback window
            returns_window = returns.tail(self.lookback).dropna(how="all")
            assets = list(returns_window.columns)
            n = len(assets)

            if n == 0:
                return {"error": "No valid assets", "valid": False}

            # Covariance matrix
            cov_matrix = returns_window.cov().values * 252  # Annualized
            vols = np.sqrt(np.diag(cov_matrix))

            # Calculate weights
            if method == "erc":
                weights = self._erc_weights(cov_matrix, n)
            elif method == "inverse_vol":
                weights = self._inverse_vol_weights(vols)
            elif method == "max_diversification":
                weights = self._max_diversification_weights(cov_matrix, vols, n)
            else:
                weights = self._erc_weights(cov_matrix, n)

            # Apply constraints
            weights = self._apply_constraints(weights)

            # Build result
            weight_dict = {assets[i]: round(float(weights[i]), 4) for i in range(n)}
            risk_contributions = self._risk_contributions(weights, cov_matrix)
            rc_dict = {assets[i]: round(float(risk_contributions[i]), 4) for i in range(n)}

            # Portfolio metrics
            port_vol = float(np.sqrt(weights @ cov_matrix @ weights))
            port_return = float(returns_window.mean().values @ weights) * 252

            # Rebalancing check
            needs_rebalance = False
            if current_weights:
                current_arr = np.array([current_weights.get(a, 0) for a in assets])
                drift = np.abs(weights - current_arr).max()
                needs_rebalance = drift > self.rebalance_threshold

            self._last_weights = weight_dict

            result = {
                "valid": True,
                "method": method,
                "weights": weight_dict,
                "risk_contributions": rc_dict,
                "portfolio_metrics": {
                    "annualized_volatility": round(port_vol, 4),
                    "annualized_return": round(port_return, 4),
                    "sharpe_ratio": round(port_return / port_vol, 4) if port_vol > 0 else 0.0,
                    "diversification_ratio": self._diversification_ratio(weights, cov_matrix, vols),
                },
                "rebalancing": {
                    "needs_rebalance": needs_rebalance,
                    "threshold": self.rebalance_threshold,
                },
                "assets": assets,
                "n_assets": n,
                "lookback": self.lookback,
                "timestamp": datetime.utcnow().isoformat(),
                "version": self.version,
            }

            logger.info(
                f"RiskParity [{method}]: vol={port_vol:.4f} "
                f"weights={weight_dict}"
            )
            return result

        except Exception as exc:
            logger.error(f"Risk parity allocation error: {exc}", exc_info=True)
            return {"error": str(exc), "valid": False}

    # ------------------------------------------------------------------
    # ERC (Equal Risk Contribution)
    # ------------------------------------------------------------------

    def _erc_weights(self, cov_matrix: np.ndarray, n: int) -> np.ndarray:
        """
        Equal Risk Contribution optimization.
        Minimizes sum of squared differences in risk contributions.
        """
        def objective(w):
            w = np.array(w)
            port_var = w @ cov_matrix @ w
            marginal_risk = cov_matrix @ w
            risk_contrib = w * marginal_risk / port_var if port_var > 0 else w
            target = 1.0 / n
            return float(np.sum((risk_contrib - target) ** 2))

        # Initial guess: equal weights
        w0 = np.ones(n) / n
        bounds = [(self.min_weight, self.max_weight)] * n
        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]

        result = minimize(
            objective,
            w0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-9},
        )

        if result.success:
            return np.array(result.x)
        else:
            logger.warning("ERC optimization failed, falling back to inverse vol")
            vols = np.sqrt(np.diag(cov_matrix))
            return self._inverse_vol_weights(vols)

    # ------------------------------------------------------------------
    # Inverse Volatility
    # ------------------------------------------------------------------

    def _inverse_vol_weights(self, vols: np.ndarray) -> np.ndarray:
        """Inverse volatility weighting — simple approximation of risk parity."""
        inv_vols = 1.0 / np.where(vols > 0, vols, 1e-8)
        return inv_vols / inv_vols.sum()

    # ------------------------------------------------------------------
    # Maximum Diversification
    # ------------------------------------------------------------------

    def _max_diversification_weights(
        self, cov_matrix: np.ndarray, vols: np.ndarray, n: int
    ) -> np.ndarray:
        """
        Maximum Diversification Portfolio.
        Maximizes the diversification ratio: weighted avg vol / portfolio vol.
        """
        def neg_diversification_ratio(w):
            w = np.array(w)
            port_vol = np.sqrt(w @ cov_matrix @ w)
            weighted_vol = w @ vols
            return -weighted_vol / port_vol if port_vol > 0 else 0.0

        w0 = np.ones(n) / n
        bounds = [(self.min_weight, self.max_weight)] * n
        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]

        result = minimize(
            neg_diversification_ratio,
            w0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000},
        )

        return np.array(result.x) if result.success else self._inverse_vol_weights(vols)

    # ------------------------------------------------------------------
    # Risk Contributions
    # ------------------------------------------------------------------

    def _risk_contributions(
        self, weights: np.ndarray, cov_matrix: np.ndarray
    ) -> np.ndarray:
        """Calculate marginal risk contributions for each asset."""
        port_var = weights @ cov_matrix @ weights
        if port_var <= 0:
            return weights
        marginal = cov_matrix @ weights
        return weights * marginal / port_var

    def _diversification_ratio(
        self,
        weights: np.ndarray,
        cov_matrix: np.ndarray,
        vols: np.ndarray,
    ) -> float:
        """Diversification ratio: weighted avg vol / portfolio vol."""
        port_vol = float(np.sqrt(weights @ cov_matrix @ weights))
        weighted_vol = float(weights @ vols)
        return round(weighted_vol / port_vol, 4) if port_vol > 0 else 1.0

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    def _apply_constraints(self, weights: np.ndarray) -> np.ndarray:
        """Apply min/max weight constraints and renormalize."""
        weights = np.clip(weights, self.min_weight, self.max_weight)
        total = weights.sum()
        return weights / total if total > 0 else weights

    # ------------------------------------------------------------------
    # Single Asset Sizing
    # ------------------------------------------------------------------

    def get_position_weight(
        self,
        symbol: str,
        returns: pd.DataFrame,
        method: str = "erc",
    ) -> float:
        """Get the risk parity weight for a single symbol."""
        allocation = self.allocate(returns, method=method)
        if not allocation.get("valid"):
            return 1.0 / max(len(returns.columns), 1)
        return allocation["weights"].get(symbol, 0.0)


# Global instance
risk_parity_allocator = RiskParityAllocator()

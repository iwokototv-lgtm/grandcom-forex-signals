"""
Volatility Adjustment Engine
Dynamic position sizing based on realized and implied volatility
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, Optional, List
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class VolatilityAdjustment:
    """
    Dynamic Position Sizing via Volatility Targeting.

    Adjusts position size so that each trade contributes a consistent
    amount of volatility to the portfolio, regardless of market conditions.

    Methods:
    - ATR-based sizing (primary)
    - Realized volatility targeting
    - GARCH-style volatility forecasting
    - Volatility regime scaling
    """

    def __init__(
        self,
        target_vol: float = 0.01,       # 1% daily volatility target
        vol_lookback: int = 20,
        atr_lookback: int = 14,
        vol_floor: float = 0.002,       # Minimum volatility (prevent over-sizing)
        vol_ceiling: float = 0.05,      # Maximum volatility (prevent under-sizing)
        max_size_multiplier: float = 2.0,
        min_size_multiplier: float = 0.25,
    ):
        self.target_vol = target_vol
        self.vol_lookback = vol_lookback
        self.atr_lookback = atr_lookback
        self.vol_floor = vol_floor
        self.vol_ceiling = vol_ceiling
        self.max_size_multiplier = max_size_multiplier
        self.min_size_multiplier = min_size_multiplier
        self.version = "3.0.0"

    # ------------------------------------------------------------------
    # Main Sizing
    # ------------------------------------------------------------------

    def calculate_position_size(
        self,
        df: pd.DataFrame,
        base_size: float,
        account_balance: float,
        risk_pct: float = 0.02,
        symbol: str = "XAUUSD",
    ) -> Dict[str, Any]:
        """
        Calculate volatility-adjusted position size.

        Args:
            df: OHLCV DataFrame
            base_size: Base position size (lots)
            account_balance: Account balance in USD
            risk_pct: Maximum risk per trade as fraction of balance
            symbol: Trading symbol

        Returns:
            Adjusted position size and volatility metrics
        """
        try:
            if len(df) < max(self.vol_lookback, self.atr_lookback) + 5:
                return {
                    "adjusted_size": base_size,
                    "multiplier": 1.0,
                    "error": "Insufficient data",
                    "valid": False,
                }

            df = df.copy()

            # Compute volatility metrics
            vol_metrics = self._compute_volatility(df)
            atr_metrics = self._compute_atr(df)
            regime = self._detect_vol_regime(vol_metrics)

            # Volatility-based multiplier
            realized_vol = vol_metrics["realized_vol_daily"]
            realized_vol = max(self.vol_floor, min(self.vol_ceiling, realized_vol))

            vol_multiplier = self.target_vol / realized_vol if realized_vol > 0 else 1.0
            vol_multiplier = max(self.min_size_multiplier, min(self.max_size_multiplier, vol_multiplier))

            # ATR-based dollar risk sizing
            atr = atr_metrics["atr"]
            current_price = float(df["close"].iloc[-1])
            max_dollar_risk = account_balance * risk_pct

            # ATR sizing: risk = ATR * lot_size * pip_value
            # For gold: 1 lot = 100 oz, pip = $0.01
            pip_value = 0.01  # per pip per lot for XAUUSD
            atr_size = max_dollar_risk / (atr * 100 * pip_value) if atr > 0 else base_size

            # Combine: use minimum of vol-adjusted and ATR-sized
            adjusted_size = min(base_size * vol_multiplier, atr_size)
            adjusted_size = max(0.01, round(adjusted_size, 2))  # Min 0.01 lots

            # Regime adjustment
            regime_multiplier = self._regime_multiplier(regime)
            final_size = round(adjusted_size * regime_multiplier, 2)
            final_size = max(0.01, final_size)

            result = {
                "valid": True,
                "symbol": symbol,
                "base_size": base_size,
                "adjusted_size": final_size,
                "vol_multiplier": round(vol_multiplier, 4),
                "regime_multiplier": round(regime_multiplier, 4),
                "total_multiplier": round(final_size / base_size if base_size > 0 else 1.0, 4),
                "volatility": vol_metrics,
                "atr": atr_metrics,
                "regime": regime,
                "risk_metrics": {
                    "max_dollar_risk": round(max_dollar_risk, 2),
                    "estimated_risk": round(atr * final_size * 100 * pip_value, 2),
                    "risk_pct": round(risk_pct * 100, 2),
                },
                "timestamp": datetime.utcnow().isoformat(),
                "version": self.version,
            }

            logger.info(
                f"VolAdj [{symbol}]: base={base_size} → final={final_size} "
                f"vol_mult={vol_multiplier:.3f} regime={regime}"
            )
            return result

        except Exception as exc:
            logger.error(f"Volatility adjustment error: {exc}", exc_info=True)
            return {"adjusted_size": base_size, "multiplier": 1.0, "error": str(exc), "valid": False}

    # ------------------------------------------------------------------
    # Volatility Computation
    # ------------------------------------------------------------------

    def _compute_volatility(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Compute realized volatility metrics."""
        close = df["close"]
        returns = close.pct_change().dropna()

        # Realized volatility (daily)
        realized_vol = float(returns.tail(self.vol_lookback).std())

        # Annualized
        annualized_vol = realized_vol * np.sqrt(252)

        # Short-term vs long-term vol
        short_vol = float(returns.tail(5).std())
        long_vol = float(returns.tail(self.vol_lookback).std())
        vol_ratio = short_vol / long_vol if long_vol > 0 else 1.0

        # Parkinson volatility (uses high/low)
        if "high" in df.columns and "low" in df.columns:
            hl_ratio = np.log(df["high"] / df["low"])
            parkinson_vol = float(np.sqrt(hl_ratio.tail(self.vol_lookback).pow(2).mean() / (4 * np.log(2))))
        else:
            parkinson_vol = realized_vol

        return {
            "realized_vol_daily": round(realized_vol, 6),
            "annualized_vol": round(annualized_vol, 4),
            "short_vol_5d": round(short_vol, 6),
            "long_vol": round(long_vol, 6),
            "vol_ratio": round(vol_ratio, 4),
            "parkinson_vol": round(parkinson_vol, 6),
            "target_vol": self.target_vol,
        }

    def _compute_atr(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Compute ATR and related metrics."""
        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)

        atr = float(tr.rolling(self.atr_lookback).mean().iloc[-1])
        atr_pct = atr / float(close.iloc[-1]) * 100 if float(close.iloc[-1]) > 0 else 0

        # ATR trend
        atr_series = tr.rolling(self.atr_lookback).mean()
        atr_slope = float(atr_series.diff().tail(5).mean())

        return {
            "atr": round(atr, 5),
            "atr_pct": round(atr_pct, 4),
            "atr_slope": round(atr_slope, 6),
            "atr_expanding": atr_slope > 0,
            "lookback": self.atr_lookback,
        }

    # ------------------------------------------------------------------
    # Volatility Regime
    # ------------------------------------------------------------------

    def _detect_vol_regime(self, vol_metrics: Dict[str, Any]) -> str:
        """Classify current volatility regime."""
        vol_ratio = vol_metrics.get("vol_ratio", 1.0)
        realized_vol = vol_metrics.get("realized_vol_daily", 0.01)

        if realized_vol > self.vol_ceiling:
            return "EXTREME_HIGH"
        elif vol_ratio > 1.5:
            return "HIGH_EXPANDING"
        elif vol_ratio > 1.2:
            return "HIGH"
        elif vol_ratio < 0.7:
            return "LOW_CONTRACTING"
        elif vol_ratio < 0.85:
            return "LOW"
        return "NORMAL"

    def _regime_multiplier(self, regime: str) -> float:
        """Get position size multiplier for volatility regime."""
        multipliers = {
            "EXTREME_HIGH": 0.25,
            "HIGH_EXPANDING": 0.5,
            "HIGH": 0.75,
            "NORMAL": 1.0,
            "LOW": 1.1,
            "LOW_CONTRACTING": 1.2,
        }
        return multipliers.get(regime, 1.0)

    # ------------------------------------------------------------------
    # Volatility Forecast
    # ------------------------------------------------------------------

    def forecast_volatility(
        self, df: pd.DataFrame, horizon: int = 5
    ) -> Dict[str, Any]:
        """
        Simple EWMA volatility forecast.
        Approximates GARCH(1,1) behavior.
        """
        returns = df["close"].pct_change().dropna()
        if len(returns) < 20:
            return {"forecast": self.target_vol, "method": "fallback"}

        # EWMA with lambda = 0.94 (RiskMetrics standard)
        lam = 0.94
        ewma_var = returns.ewm(com=(1 - lam) / lam).var()
        current_var = float(ewma_var.iloc[-1])

        # Mean reversion: long-run variance
        long_run_var = float(returns.var())

        # Forecast: variance mean-reverts to long-run level
        alpha = 0.1  # Mean reversion speed
        forecasts = []
        var_t = current_var
        for _ in range(horizon):
            var_t = alpha * long_run_var + (1 - alpha) * var_t
            forecasts.append(round(float(np.sqrt(var_t)), 6))

        return {
            "current_vol": round(float(np.sqrt(current_var)), 6),
            "forecasts": forecasts,
            "horizon": horizon,
            "long_run_vol": round(float(np.sqrt(long_run_var)), 6),
            "method": "EWMA",
        }


# Global instance
volatility_adjustment = VolatilityAdjustment()

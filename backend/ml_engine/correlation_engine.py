"""
Correlation / Exposure Engine
Rolling correlation windows, Beta exposure, USD clustering analysis
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import logging
from scipy import stats

logger = logging.getLogger(__name__)


class CorrelationEngine:
    """
    Correlation and Exposure Analysis Engine.

    Components:
    1. Rolling Correlation: Multi-window correlation matrices (20, 60, 120 bars)
    2. Beta Exposure: Asset beta relative to benchmark (DXY, SPX)
    3. USD Clustering: Group assets by USD sensitivity
    4. Portfolio Exposure: Net directional exposure across correlated assets
    5. Diversification Score: How diversified the current portfolio is
    """

    def __init__(
        self,
        windows: List[int] = None,
        beta_window: int = 60,
        usd_threshold: float = 0.7,
    ):
        self.windows = windows or [20, 60, 120]
        self.beta_window = beta_window
        self.usd_threshold = usd_threshold
        self.version = "3.0.0"

    # ------------------------------------------------------------------
    # Main Analysis
    # ------------------------------------------------------------------

    def analyze(
        self,
        price_data: Dict[str, pd.Series],
        benchmark: str = "DXY",
        symbol: str = "XAUUSD",
    ) -> Dict[str, Any]:
        """
        Full correlation and exposure analysis.

        Args:
            price_data: Dict of {symbol: price_series}
            benchmark: Benchmark symbol for beta calculation
            symbol: Primary symbol being analyzed

        Returns:
            Comprehensive correlation analysis
        """
        try:
            if len(price_data) < 2:
                return {"error": "Need at least 2 price series", "valid": False}

            result: Dict[str, Any] = {
                "symbol": symbol,
                "benchmark": benchmark,
                "timestamp": datetime.utcnow().isoformat(),
                "valid": True,
                "version": self.version,
            }

            # Build returns DataFrame
            returns_df = self._build_returns_df(price_data)

            if returns_df.empty or len(returns_df) < self.windows[0]:
                return {"error": "Insufficient return data", "valid": False}

            # 1. Rolling correlations
            result["correlations"] = self._rolling_correlations(returns_df, symbol)

            # 2. Beta exposure
            if benchmark in returns_df.columns and symbol in returns_df.columns:
                result["beta"] = self._calculate_beta(returns_df, symbol, benchmark)
            else:
                result["beta"] = {"error": "Benchmark not available"}

            # 3. USD clustering
            result["usd_clustering"] = self._usd_clustering(returns_df)

            # 4. Portfolio exposure
            result["exposure"] = self._portfolio_exposure(returns_df, symbol)

            # 5. Diversification score
            result["diversification"] = self._diversification_score(returns_df)

            # 6. Risk-adjusted correlation
            result["risk_adjusted"] = self._risk_adjusted_analysis(returns_df, symbol)

            # 7. Correlation regime
            result["correlation_regime"] = self._detect_correlation_regime(result["correlations"])

            logger.info(
                f"Correlation [{symbol}]: beta={result['beta'].get('beta', 'N/A')} "
                f"regime={result['correlation_regime']}"
            )
            return result

        except Exception as exc:
            logger.error(f"Correlation analysis error: {exc}", exc_info=True)
            return {"error": str(exc), "valid": False}

    # ------------------------------------------------------------------
    # Returns Computation
    # ------------------------------------------------------------------

    def _build_returns_df(self, price_data: Dict[str, pd.Series]) -> pd.DataFrame:
        """Build aligned returns DataFrame from price series."""
        returns = {}
        for symbol, prices in price_data.items():
            if isinstance(prices, pd.Series) and len(prices) > 1:
                returns[symbol] = prices.pct_change().dropna()

        if not returns:
            return pd.DataFrame()

        df = pd.DataFrame(returns)
        df = df.dropna(how="all")
        return df

    # ------------------------------------------------------------------
    # Rolling Correlations
    # ------------------------------------------------------------------

    def _rolling_correlations(
        self, returns_df: pd.DataFrame, primary_symbol: str
    ) -> Dict[str, Any]:
        """Calculate rolling correlation matrices for multiple windows."""
        result: Dict[str, Any] = {}

        symbols = list(returns_df.columns)

        for window in self.windows:
            if len(returns_df) < window:
                continue

            window_key = f"window_{window}"
            result[window_key] = {}

            # Full correlation matrix for this window
            corr_matrix = returns_df.tail(window).corr()

            # Store as nested dict
            result[window_key]["matrix"] = {
                sym: {
                    other: round(float(corr_matrix.loc[sym, other]), 4)
                    for other in symbols
                    if other in corr_matrix.columns
                }
                for sym in symbols
                if sym in corr_matrix.index
            }

            # Primary symbol correlations
            if primary_symbol in corr_matrix.index:
                primary_corrs = corr_matrix.loc[primary_symbol].drop(primary_symbol, errors="ignore")
                result[window_key]["primary_correlations"] = {
                    sym: round(float(val), 4)
                    for sym, val in primary_corrs.items()
                }

                # Highly correlated assets
                result[window_key]["high_correlation"] = [
                    sym for sym, val in primary_corrs.items()
                    if abs(val) >= self.usd_threshold
                ]

                # Average correlation
                result[window_key]["avg_correlation"] = round(
                    float(primary_corrs.abs().mean()), 4
                )

        # Rolling correlation time series (primary vs each other)
        if primary_symbol in returns_df.columns:
            rolling_corrs: Dict[str, List] = {}
            for sym in symbols:
                if sym != primary_symbol and sym in returns_df.columns:
                    roll = returns_df[primary_symbol].rolling(self.windows[0]).corr(returns_df[sym])
                    rolling_corrs[sym] = {
                        "current": round(float(roll.iloc[-1]), 4) if not np.isnan(roll.iloc[-1]) else 0.0,
                        "mean": round(float(roll.mean()), 4),
                        "std": round(float(roll.std()), 4),
                    }
            result["rolling_series"] = rolling_corrs

        return result

    # ------------------------------------------------------------------
    # Beta Exposure
    # ------------------------------------------------------------------

    def _calculate_beta(
        self,
        returns_df: pd.DataFrame,
        symbol: str,
        benchmark: str,
    ) -> Dict[str, Any]:
        """
        Calculate beta of symbol relative to benchmark.
        Beta > 1: More volatile than benchmark
        Beta < 0: Inverse relationship (gold vs DXY)
        """
        try:
            window_data = returns_df[[symbol, benchmark]].dropna().tail(self.beta_window)

            if len(window_data) < 10:
                return {"error": "Insufficient data for beta"}

            sym_returns = window_data[symbol].values
            bench_returns = window_data[benchmark].values

            # OLS regression: sym = alpha + beta * bench
            slope, intercept, r_value, p_value, std_err = stats.linregress(
                bench_returns, sym_returns
            )

            beta = float(slope)
            alpha = float(intercept)
            r_squared = float(r_value ** 2)

            # Rolling beta (last 3 windows)
            rolling_betas = []
            step = max(len(window_data) // 3, 10)
            for i in range(0, len(window_data) - step, step):
                chunk = window_data.iloc[i : i + step]
                if len(chunk) >= 5:
                    s, _, _, _, _ = stats.linregress(chunk[benchmark].values, chunk[symbol].values)
                    rolling_betas.append(round(float(s), 4))

            return {
                "beta": round(beta, 4),
                "alpha": round(alpha, 6),
                "r_squared": round(r_squared, 4),
                "p_value": round(float(p_value), 4),
                "std_err": round(float(std_err), 6),
                "rolling_betas": rolling_betas,
                "beta_regime": self._classify_beta(beta),
                "window": self.beta_window,
                "benchmark": benchmark,
            }

        except Exception as exc:
            logger.error(f"Beta calculation error: {exc}")
            return {"error": str(exc)}

    def _classify_beta(self, beta: float) -> str:
        """Classify beta into regime."""
        if beta < -0.5:
            return "STRONG_INVERSE"
        elif beta < 0:
            return "WEAK_INVERSE"
        elif beta < 0.5:
            return "LOW_BETA"
        elif beta < 1.0:
            return "MODERATE_BETA"
        elif beta < 1.5:
            return "HIGH_BETA"
        return "VERY_HIGH_BETA"

    # ------------------------------------------------------------------
    # USD Clustering
    # ------------------------------------------------------------------

    def _usd_clustering(self, returns_df: pd.DataFrame) -> Dict[str, Any]:
        """
        Cluster assets by USD sensitivity.
        USD-positive: Moves with USD (DXY)
        USD-negative: Moves against USD (Gold, EUR, GBP)
        USD-neutral: Low correlation with USD
        """
        if "DXY" not in returns_df.columns:
            return {"error": "DXY not available for clustering"}

        window = min(self.windows[1], len(returns_df))
        corr_with_dxy = returns_df.tail(window).corr()["DXY"].drop("DXY", errors="ignore")

        usd_positive = []
        usd_negative = []
        usd_neutral = []

        for sym, corr in corr_with_dxy.items():
            if np.isnan(corr):
                usd_neutral.append(sym)
            elif corr >= self.usd_threshold:
                usd_positive.append({"symbol": sym, "correlation": round(float(corr), 4)})
            elif corr <= -self.usd_threshold:
                usd_negative.append({"symbol": sym, "correlation": round(float(corr), 4)})
            else:
                usd_neutral.append({"symbol": sym, "correlation": round(float(corr), 4)})

        return {
            "usd_positive": usd_positive,
            "usd_negative": usd_negative,
            "usd_neutral": usd_neutral,
            "threshold": self.usd_threshold,
            "window": window,
        }

    # ------------------------------------------------------------------
    # Portfolio Exposure
    # ------------------------------------------------------------------

    def _portfolio_exposure(
        self, returns_df: pd.DataFrame, primary_symbol: str
    ) -> Dict[str, Any]:
        """
        Calculate net directional exposure across correlated assets.
        Identifies concentration risk and hedging opportunities.
        """
        window = min(self.windows[0], len(returns_df))
        recent = returns_df.tail(window)

        # Cumulative returns
        cum_returns = (1 + recent).prod() - 1

        # Volatility
        vols = recent.std() * np.sqrt(252)

        # Sharpe-like ratio
        sharpe = recent.mean() / recent.std().replace(0, np.nan) * np.sqrt(252)

        exposure = {}
        for sym in returns_df.columns:
            exposure[sym] = {
                "cumulative_return": round(float(cum_returns.get(sym, 0)), 4),
                "annualized_vol": round(float(vols.get(sym, 0)), 4),
                "sharpe_ratio": round(float(sharpe.get(sym, 0)), 4) if not np.isnan(sharpe.get(sym, 0)) else 0.0,
            }

        # Net exposure for primary symbol
        primary_corrs = returns_df.tail(window).corr().get(primary_symbol, pd.Series())
        net_exposure = float(primary_corrs.drop(primary_symbol, errors="ignore").sum())

        return {
            "per_asset": exposure,
            "net_exposure": round(net_exposure, 4),
            "concentration_risk": abs(net_exposure) > 2.0,
            "window": window,
        }

    # ------------------------------------------------------------------
    # Diversification Score
    # ------------------------------------------------------------------

    def _diversification_score(self, returns_df: pd.DataFrame) -> Dict[str, Any]:
        """
        Calculate portfolio diversification score (0-100).
        Higher = more diversified (lower average correlation).
        """
        window = min(self.windows[1], len(returns_df))
        corr_matrix = returns_df.tail(window).corr()

        # Average off-diagonal correlation
        n = len(corr_matrix)
        if n < 2:
            return {"score": 50, "interpretation": "INSUFFICIENT_DATA"}

        off_diag = corr_matrix.values[np.triu_indices(n, k=1)]
        avg_corr = float(np.mean(np.abs(off_diag)))

        # Score: 0 = perfectly correlated, 100 = perfectly uncorrelated
        score = round((1 - avg_corr) * 100, 1)

        if score >= 70:
            interpretation = "WELL_DIVERSIFIED"
        elif score >= 50:
            interpretation = "MODERATELY_DIVERSIFIED"
        elif score >= 30:
            interpretation = "POORLY_DIVERSIFIED"
        else:
            interpretation = "HIGHLY_CONCENTRATED"

        return {
            "score": score,
            "avg_correlation": round(avg_corr, 4),
            "interpretation": interpretation,
            "n_assets": n,
        }

    # ------------------------------------------------------------------
    # Risk-Adjusted Analysis
    # ------------------------------------------------------------------

    def _risk_adjusted_analysis(
        self, returns_df: pd.DataFrame, primary_symbol: str
    ) -> Dict[str, Any]:
        """Risk-adjusted correlation metrics."""
        if primary_symbol not in returns_df.columns:
            return {}

        window = min(self.windows[1], len(returns_df))
        primary = returns_df[primary_symbol].tail(window)

        vol = float(primary.std()) * np.sqrt(252)
        skew = float(primary.skew())
        kurt = float(primary.kurtosis())
        var_95 = float(np.percentile(primary.dropna(), 5))
        cvar_95 = float(primary[primary <= var_95].mean()) if len(primary[primary <= var_95]) > 0 else var_95

        return {
            "annualized_volatility": round(vol, 4),
            "skewness": round(skew, 4),
            "kurtosis": round(kurt, 4),
            "var_95": round(var_95, 6),
            "cvar_95": round(cvar_95, 6),
            "tail_risk": abs(cvar_95) > abs(var_95) * 1.5,
        }

    # ------------------------------------------------------------------
    # Correlation Regime
    # ------------------------------------------------------------------

    def _detect_correlation_regime(self, correlations: Dict[str, Any]) -> str:
        """Detect current correlation regime."""
        try:
            short_key = f"window_{self.windows[0]}"
            long_key = f"window_{self.windows[-1]}"

            short_avg = correlations.get(short_key, {}).get("avg_correlation", 0.5)
            long_avg = correlations.get(long_key, {}).get("avg_correlation", 0.5)

            if short_avg > 0.7:
                return "HIGH_CORRELATION"
            elif short_avg < 0.3:
                return "LOW_CORRELATION"
            elif short_avg > long_avg + 0.2:
                return "RISING_CORRELATION"
            elif short_avg < long_avg - 0.2:
                return "FALLING_CORRELATION"
            return "STABLE_CORRELATION"
        except Exception:
            return "UNKNOWN"


# Global instance
correlation_engine = CorrelationEngine()

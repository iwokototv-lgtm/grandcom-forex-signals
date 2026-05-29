"""
Advanced Correlation Engine — v3.0
Rolling correlation, Beta exposure, and USD clustering analysis.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class CorrelationEngine:
    """
    Three-layer correlation analysis:

    1. Rolling Correlation  — pairwise Pearson correlation over a sliding window.
    2. Beta Exposure        — pair beta relative to a benchmark (XAUUSD).
    3. USD Clustering       — groups USD-correlated pairs to cap total USD exposure.

    Used by the portfolio manager to prevent over-correlated position stacking.
    """

    # Approximate static correlation matrix (updated dynamically when data available)
    STATIC_CORRELATIONS: dict[tuple[str, str], float] = {
        ("XAUUSD", "XAUEUR"): 0.95,
        ("XAUUSD", "EURUSD"): 0.45,
        ("XAUUSD", "GBPUSD"): 0.40,
        ("XAUUSD", "USDJPY"): -0.35,
        ("XAUUSD", "USDCHF"): -0.40,
        ("XAUUSD", "USDCAD"): -0.30,
        ("EURUSD", "GBPUSD"): 0.85,
        ("EURUSD", "AUDUSD"): 0.70,
        ("EURUSD", "USDCHF"): -0.95,
        ("GBPUSD", "AUDUSD"): 0.65,
        ("USDJPY", "EURJPY"): 0.75,
        ("USDJPY", "GBPJPY"): 0.80,
        ("EURJPY", "GBPJPY"): 0.90,
    }

    USD_CLUSTER: list[str] = [
        "EURUSD", "GBPUSD", "AUDUSD", "USDCHF", "USDCAD", "USDJPY"
    ]
    GOLD_CLUSTER: list[str] = ["XAUUSD", "XAUEUR"]

    def __init__(
        self,
        window: int = 30,
        beta_lookback: int = 60,
        correlation_cap: float = 0.70,
        usd_cluster_threshold: float = 0.65,
    ) -> None:
        self.window = window
        self.beta_lookback = beta_lookback
        self.correlation_cap = correlation_cap
        self.usd_cluster_threshold = usd_cluster_threshold

        # Price history store: {symbol: pd.Series of close prices}
        self._price_history: dict[str, pd.Series] = {}
        # Computed rolling correlations cache
        self._correlation_cache: dict[tuple[str, str], float] = {}
        self._cache_timestamp: datetime | None = None

    # ------------------------------------------------------------------
    # Price History Management
    # ------------------------------------------------------------------

    def update_prices(self, symbol: str, prices: pd.Series) -> None:
        """Update stored price history for a symbol."""
        self._price_history[symbol] = prices.tail(max(self.window, self.beta_lookback) + 10)
        self._invalidate_cache()

    def update_price_point(self, symbol: str, price: float) -> None:
        """Append a single price point to history."""
        if symbol not in self._price_history:
            self._price_history[symbol] = pd.Series(dtype=float)
        series = self._price_history[symbol]
        new_entry = pd.Series([price])
        self._price_history[symbol] = pd.concat(
            [series, new_entry], ignore_index=True
        ).tail(self.beta_lookback + 10)
        self._invalidate_cache()

    def _invalidate_cache(self) -> None:
        self._correlation_cache = {}
        self._cache_timestamp = None

    # ------------------------------------------------------------------
    # Rolling Correlation
    # ------------------------------------------------------------------

    def rolling_correlation(self, sym_a: str, sym_b: str) -> float:
        """
        Compute rolling Pearson correlation between two symbols.
        Falls back to static table if insufficient price history.
        """
        cache_key = tuple(sorted([sym_a, sym_b]))
        if cache_key in self._correlation_cache:
            return self._correlation_cache[cache_key]

        try:
            if sym_a in self._price_history and sym_b in self._price_history:
                s_a = self._price_history[sym_a]
                s_b = self._price_history[sym_b]

                # Align by index length
                min_len = min(len(s_a), len(s_b), self.window)
                if min_len >= 10:
                    r_a = s_a.tail(min_len).pct_change().dropna()
                    r_b = s_b.tail(min_len).pct_change().dropna()
                    min_len2 = min(len(r_a), len(r_b))
                    if min_len2 >= 5:
                        corr = float(
                            np.corrcoef(
                                r_a.tail(min_len2).values,
                                r_b.tail(min_len2).values,
                            )[0, 1]
                        )
                        if not np.isnan(corr):
                            self._correlation_cache[cache_key] = corr
                            return corr
        except Exception as exc:
            logger.debug(f"Rolling correlation error ({sym_a}/{sym_b}): {exc}")

        # Fallback to static table
        static = self.STATIC_CORRELATIONS.get(
            cache_key,  # type: ignore[arg-type]
            self.STATIC_CORRELATIONS.get((sym_b, sym_a), 0.0),
        )
        self._correlation_cache[cache_key] = static
        return static

    # ------------------------------------------------------------------
    # Beta Exposure
    # ------------------------------------------------------------------

    def beta(self, symbol: str, benchmark: str = "XAUUSD") -> float:
        """
        Compute beta of `symbol` relative to `benchmark`.
        Beta > 1 → amplified moves; Beta < 0 → inverse.
        """
        if symbol == benchmark:
            return 1.0

        try:
            if symbol in self._price_history and benchmark in self._price_history:
                s = self._price_history[symbol].tail(self.beta_lookback)
                b = self._price_history[benchmark].tail(self.beta_lookback)
                min_len = min(len(s), len(b))
                if min_len >= 20:
                    r_s = s.tail(min_len).pct_change().dropna()
                    r_b = b.tail(min_len).pct_change().dropna()
                    min_len2 = min(len(r_s), len(r_b))
                    if min_len2 >= 10:
                        r_s_arr = r_s.tail(min_len2).values
                        r_b_arr = r_b.tail(min_len2).values
                        cov = np.cov(r_s_arr, r_b_arr)[0, 1]
                        var_b = np.var(r_b_arr)
                        if var_b > 0:
                            return float(cov / var_b)
        except Exception as exc:
            logger.debug(f"Beta calculation error ({symbol}/{benchmark}): {exc}")

        # Fallback: use correlation as proxy
        corr = self.rolling_correlation(symbol, benchmark)
        return corr  # Approximate beta ≈ correlation when std ratios unknown

    # ------------------------------------------------------------------
    # USD Clustering
    # ------------------------------------------------------------------

    def usd_cluster_exposure(self, open_positions: list[dict]) -> dict[str, Any]:
        """
        Analyse USD cluster exposure across open positions.

        Returns:
            dict with total_usd_exposure, gold_exposure, cluster_count,
            and whether adding another USD-correlated position is allowed.
        """
        usd_count = 0
        gold_count = 0
        usd_symbols: list[str] = []
        gold_symbols: list[str] = []

        for pos in open_positions:
            sym = pos.get("symbol", pos.get("pair", ""))
            if sym in self.USD_CLUSTER:
                usd_count += 1
                usd_symbols.append(sym)
            if sym in self.GOLD_CLUSTER:
                gold_count += 1
                gold_symbols.append(sym)

        total = len(open_positions) or 1
        usd_exposure = usd_count / total
        gold_exposure = gold_count / total

        return {
            "usd_count": usd_count,
            "gold_count": gold_count,
            "usd_exposure_pct": round(usd_exposure, 3),
            "gold_exposure_pct": round(gold_exposure, 3),
            "usd_symbols": usd_symbols,
            "gold_symbols": gold_symbols,
            "usd_cluster_full": usd_exposure >= self.usd_cluster_threshold,
            "gold_cluster_full": gold_exposure >= 0.50,
        }

    # ------------------------------------------------------------------
    # Position Correlation Check
    # ------------------------------------------------------------------

    def is_correlated_with_open(
        self,
        new_symbol: str,
        open_positions: list[dict],
        new_direction: str = "BUY",
    ) -> dict[str, Any]:
        """
        Check if a new signal is too correlated with existing open positions.

        Returns:
            dict with allowed (bool), max_correlation, correlated_pairs.
        """
        max_corr = 0.0
        correlated: list[str] = []

        for pos in open_positions:
            sym = pos.get("symbol", pos.get("pair", ""))
            if sym == new_symbol:
                continue

            corr = abs(self.rolling_correlation(new_symbol, sym))

            # Inverse correlation: same direction on inversely correlated pairs
            # is effectively doubling exposure
            raw_corr = self.rolling_correlation(new_symbol, sym)
            pos_dir = pos.get("direction", pos.get("type", "BUY"))
            if raw_corr < 0 and pos_dir != new_direction:
                # Opposite directions on inverse pairs = same net exposure
                corr = abs(raw_corr)

            if corr > max_corr:
                max_corr = corr
            if corr >= self.correlation_cap:
                correlated.append(sym)

        allowed = len(correlated) == 0

        return {
            "allowed": allowed,
            "max_correlation": round(max_corr, 3),
            "correlated_pairs": correlated,
            "correlation_cap": self.correlation_cap,
        }

    # ------------------------------------------------------------------
    # Full Portfolio Correlation Matrix
    # ------------------------------------------------------------------

    def portfolio_correlation_matrix(
        self, symbols: list[str]
    ) -> dict[str, dict[str, float]]:
        """Return pairwise correlation matrix for a list of symbols."""
        matrix: dict[str, dict[str, float]] = {}
        for sym_a in symbols:
            matrix[sym_a] = {}
            for sym_b in symbols:
                if sym_a == sym_b:
                    matrix[sym_a][sym_b] = 1.0
                else:
                    matrix[sym_a][sym_b] = round(
                        self.rolling_correlation(sym_a, sym_b), 3
                    )
        return matrix

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def get_summary(self, symbols: list[str] | None = None) -> dict[str, Any]:
        """Return a summary of correlation engine state."""
        syms = symbols or list(self._price_history.keys())
        return {
            "tracked_symbols": list(self._price_history.keys()),
            "window": self.window,
            "beta_lookback": self.beta_lookback,
            "correlation_cap": self.correlation_cap,
            "usd_cluster_threshold": self.usd_cluster_threshold,
            "cache_size": len(self._correlation_cache),
            "timestamp": datetime.utcnow().isoformat(),
        }


# Module-level singleton
correlation_engine = CorrelationEngine()

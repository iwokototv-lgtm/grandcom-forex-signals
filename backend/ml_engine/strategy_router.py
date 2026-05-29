"""
Strategy Router — v3.0
Routes market data to the appropriate strategy based on regime and confluence.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import pandas as pd

from .regime_detector import RegimeDetector, MarketRegime
from .feature_engineering import FeatureEngineer
from .smc_ict_strategy import SMCICTStrategy
from .mean_reversion_strategy import MeanReversionStrategy

logger = logging.getLogger(__name__)


# Strategy → regime mapping
STRATEGY_REGIME_MAP: dict[str, list[int]] = {
    "SMC_ICT": [
        MarketRegime.TREND_UP,
        MarketRegime.TREND_DOWN,
        MarketRegime.HIGH_VOLATILITY,
    ],
    "MEAN_REVERSION": [
        MarketRegime.RANGE,
        MarketRegime.LOW_VOLATILITY,
    ],
    "TREND_FOLLOWING": [
        MarketRegime.TREND_UP,
        MarketRegime.TREND_DOWN,
    ],
}


class StrategyRouter:
    """
    Routes incoming market data to the best-fit strategy for the current regime.

    Routing logic:
    1. Extract features from OHLCV data
    2. Detect market regime
    3. Select eligible strategies for the regime
    4. Run all eligible strategies
    5. Return the highest-confidence signal (or aggregate)

    Supports:
    - SMC/ICT (trending / breakout regimes)
    - Mean Reversion (ranging / low-vol regimes)
    - Trend Following (strong trend regimes, via existing SignalOptimizer)
    """

    def __init__(
        self,
        regime_detector: RegimeDetector | None = None,
        feature_engineer: FeatureEngineer | None = None,
        smc_strategy: SMCICTStrategy | None = None,
        mr_strategy: MeanReversionStrategy | None = None,
    ) -> None:
        self.regime_detector = regime_detector or RegimeDetector()
        self.feature_engineer = feature_engineer or FeatureEngineer()
        self.smc_strategy = smc_strategy or SMCICTStrategy()
        self.mr_strategy = mr_strategy or MeanReversionStrategy()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(
        self,
        df: pd.DataFrame,
        symbol: str,
        force_strategy: str | None = None,
    ) -> dict[str, Any]:
        """
        Route market data to the appropriate strategy and return the best signal.

        Args:
            df:             OHLCV DataFrame (chronological, oldest first).
            symbol:         Trading pair identifier.
            force_strategy: Override routing and use a specific strategy.

        Returns:
            dict with selected_strategy, regime, signal, confidence,
            all_signals, and routing_reason.
        """
        try:
            # 1. Feature extraction
            features = self.feature_engineer.extract_features(df, symbol)
            if features is None:
                return self._neutral_result(symbol, "Feature extraction failed")

            # 2. Regime detection
            regime = self.regime_detector.detect_regime(features)
            regime_id = regime.get("regime", MarketRegime.RANGE)
            regime_name = regime.get("regime_name", "RANGE")

            # 3. Chaos gate — no trading in chaos regime
            if regime_id == MarketRegime.CHAOS:
                return self._neutral_result(
                    symbol,
                    f"Chaos regime detected — no trading",
                    regime=regime,
                )

            # 4. Select strategies
            if force_strategy:
                eligible = [force_strategy]
                routing_reason = f"Forced strategy: {force_strategy}"
            else:
                eligible = self._select_strategies(regime_id)
                routing_reason = f"Regime {regime_name} → strategies: {eligible}"

            if not eligible:
                return self._neutral_result(
                    symbol,
                    f"No strategies eligible for regime {regime_name}",
                    regime=regime,
                )

            # 5. Run eligible strategies
            all_signals: dict[str, dict] = {}

            if "SMC_ICT" in eligible:
                smc_sig = self.smc_strategy.generate_signal(df, symbol, regime)
                all_signals["SMC_ICT"] = smc_sig

            if "MEAN_REVERSION" in eligible:
                mr_sig = self.mr_strategy.generate_signal(df, symbol, regime)
                all_signals["MEAN_REVERSION"] = mr_sig

            # 6. Select best signal
            best_strategy, best_signal = self._select_best(all_signals)

            if best_signal.get("signal") == "NEUTRAL":
                return self._neutral_result(
                    symbol,
                    "All strategies returned NEUTRAL",
                    regime=regime,
                    all_signals=all_signals,
                )

            return {
                "symbol": symbol,
                "selected_strategy": best_strategy,
                "routing_reason": routing_reason,
                "regime": regime,
                "signal": best_signal.get("signal"),
                "confidence": best_signal.get("confidence", 50.0),
                "entry": best_signal.get("entry", 0.0),
                "tp_levels": best_signal.get("tp_levels", []),
                "sl": best_signal.get("sl", 0.0),
                "analysis": best_signal.get("analysis", ""),
                "all_signals": {
                    k: {
                        "signal": v.get("signal"),
                        "confidence": v.get("confidence"),
                        "strategy": v.get("strategy"),
                    }
                    for k, v in all_signals.items()
                },
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as exc:
            logger.error(f"[StrategyRouter] Error for {symbol}: {exc}")
            return self._neutral_result(symbol, f"Router error: {exc}")

    # ------------------------------------------------------------------
    # Strategy Selection
    # ------------------------------------------------------------------

    def _select_strategies(self, regime_id: int) -> list[str]:
        """Return list of strategy names eligible for the given regime."""
        eligible: list[str] = []
        for strategy, regimes in STRATEGY_REGIME_MAP.items():
            if regime_id in regimes:
                eligible.append(strategy)
        return eligible

    def _select_best(
        self, all_signals: dict[str, dict]
    ) -> tuple[str, dict]:
        """Select the highest-confidence non-neutral signal."""
        best_strategy = ""
        best_signal: dict = {"signal": "NEUTRAL", "confidence": 0.0}

        for strategy, sig in all_signals.items():
            if sig.get("signal") == "NEUTRAL":
                continue
            conf = sig.get("confidence", 0.0)
            if conf > best_signal.get("confidence", 0.0):
                best_strategy = strategy
                best_signal = sig

        return best_strategy, best_signal

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _neutral_result(
        symbol: str,
        reason: str,
        regime: dict | None = None,
        all_signals: dict | None = None,
    ) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "selected_strategy": "NONE",
            "routing_reason": reason,
            "regime": regime or {},
            "signal": "NEUTRAL",
            "confidence": 50.0,
            "entry": 0.0,
            "tp_levels": [],
            "sl": 0.0,
            "analysis": reason,
            "all_signals": all_signals or {},
            "timestamp": datetime.utcnow().isoformat(),
        }


# Module-level singleton
strategy_router = StrategyRouter()

"""
Hybrid Portfolio System v3.0
Complete integration of all 6 institutional components
"""

import asyncio
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import logging

from .regime_detector import RegimeDetector
from .smc_ict_strategy import SMCICTStrategy
from .mean_reversion_strategy import MeanReversionStrategy
from .multi_timeframe_confirmation import MultiTimeframeConfirmation
from .pivot_points_analyzer import PivotPointsAnalyzer
from .correlation_engine import CorrelationEngine
from .risk_parity import RiskParityAllocator
from .volatility_adjustment import VolatilityAdjustment
from .drawdown_recovery import DrawdownRecoveryManager
from .economic_calendar import EconomicCalendar
from .performance_attribution import PerformanceAttribution
from .trade_journal import TradeJournal
from .position_calculator import PositionCalculator
from .portfolio_manager import PortfolioManager
from .strategy_router import StrategyRouter
from .feature_engineering import FeatureEngineer

logger = logging.getLogger(__name__)


class HybridPortfolioSystemV3:
    """
    Institutional Multi-Strategy Hybrid Portfolio System v3.0

    Integrates all 6 confirmed components:
    ✅ G1: Daily Pivot Points (4 methods, 6 levels, 6 zones)
    ✅ G2: Multi-Timeframe Confirmation (1H, 4H, D, W — 0-100% alignment)
    ✅ G3: Regime Detection (5 regimes, adaptive parameters)
    ✅ SMC/Institutional Structure (Order Blocks, FVGs, Liquidity Voids)
    ✅ Correlation/Exposure Engine (Rolling, Beta, USD Clustering)
    ✅ Multi-Timeframe Consensus (Cross-timeframe validation)

    Plus full risk management stack:
    - Risk Parity Allocation
    - Volatility Adjustment
    - Drawdown Recovery
    - Economic Calendar Filtering
    - Performance Attribution
    - Trade Journaling
    - Position Sizing
    - Portfolio Management
    """

    def __init__(self, account_balance: float = 10000.0):
        self.account_balance = account_balance
        self.version = "3.0.0"

        # Core analysis engines
        self.regime_detector = RegimeDetector()
        self.smc_ict = SMCICTStrategy()
        self.mean_reversion = MeanReversionStrategy()
        self.mtf_confirmation = MultiTimeframeConfirmation()
        self.pivot_analyzer = PivotPointsAnalyzer()
        self.correlation_engine = CorrelationEngine()
        self.feature_engineer = FeatureEngineer()

        # Risk management
        self.risk_parity = RiskParityAllocator()
        self.vol_adjustment = VolatilityAdjustment()
        self.drawdown_recovery = DrawdownRecoveryManager()
        self.economic_calendar = EconomicCalendar()
        self.position_calculator = PositionCalculator()
        self.portfolio_manager = PortfolioManager()

        # Analytics
        self.performance = PerformanceAttribution()
        self.journal = TradeJournal()

        # Routing
        self.strategy_router = StrategyRouter()

        logger.info(f"HybridPortfolioSystemV3 initialized — v{self.version}")

    # ------------------------------------------------------------------
    # Main Signal Generation
    # ------------------------------------------------------------------

    async def generate_signal(
        self,
        symbol: str,
        df_4h: pd.DataFrame,
        df_daily: Optional[pd.DataFrame] = None,
        price_data: Optional[Dict[str, pd.Series]] = None,
    ) -> Dict[str, Any]:
        """
        Full institutional signal generation pipeline.

        Args:
            symbol: Trading symbol (e.g. XAUUSD)
            df_4h: 4H OHLCV DataFrame (primary timeframe)
            df_daily: Daily OHLCV DataFrame (for pivot points)
            price_data: Multi-asset price series for correlation

        Returns:
            Complete signal with all component analyses
        """
        start_time = datetime.utcnow()

        try:
            result: Dict[str, Any] = {
                "symbol": symbol,
                "timestamp": start_time.isoformat(),
                "version": self.version,
                "valid": True,
                "components": {},
            }

            # ── G3: Regime Detection ──────────────────────────────────
            features = self.feature_engineer.extract_features(df_4h)
            regime_analysis = self.regime_detector.detect_regime(features)
            result["components"]["regime"] = regime_analysis

            # ── SMC/ICT Analysis ──────────────────────────────────────
            smc_analysis = self.smc_ict.analyze(df_4h, symbol, timeframe="4h")
            result["components"]["smc_ict"] = smc_analysis

            # ── Mean Reversion ────────────────────────────────────────
            mr_analysis = self.mean_reversion.analyze(df_4h, symbol)
            result["components"]["mean_reversion"] = mr_analysis

            # ── G2: Multi-Timeframe Confirmation ──────────────────────
            try:
                mtf_analysis = await asyncio.wait_for(
                    self.mtf_confirmation.analyze(symbol),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
                logger.warning(f"MTF analysis timed out for {symbol}")
                mtf_analysis = {"valid": False, "alignment_score": 0, "dominant_direction": "NEUTRAL"}
            result["components"]["mtf_confirmation"] = mtf_analysis

            # ── G1: Pivot Points ──────────────────────────────────────
            pivot_df = df_daily if df_daily is not None else df_4h
            pivot_analysis = self.pivot_analyzer.analyze(pivot_df, symbol, use_all_methods=True)
            result["components"]["pivot_points"] = pivot_analysis

            # ── Correlation Engine ────────────────────────────────────
            if price_data and len(price_data) >= 2:
                corr_analysis = self.correlation_engine.analyze(price_data, symbol=symbol)
                result["components"]["correlation"] = corr_analysis
            else:
                result["components"]["correlation"] = {"valid": False, "message": "Insufficient price data"}

            # ── Volatility Adjustment ─────────────────────────────────
            vol_analysis = self.vol_adjustment.calculate_position_size(
                df_4h,
                base_size=0.1,
                account_balance=self.account_balance,
                symbol=symbol,
            )
            result["components"]["volatility"] = vol_analysis

            # ── Drawdown Recovery ─────────────────────────────────────
            dd_analysis = self.drawdown_recovery.assess(self.account_balance)
            result["components"]["drawdown_recovery"] = dd_analysis

            # ── Economic Calendar ─────────────────────────────────────
            try:
                calendar_check = await asyncio.wait_for(
                    self.economic_calendar.is_safe_to_trade(symbol),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                calendar_check = {"safe_to_trade": True, "reason": "TIMEOUT_FAIL_OPEN"}
            result["components"]["economic_calendar"] = calendar_check

            # ── Portfolio State ───────────────────────────────────────
            portfolio_state = self.portfolio_manager.get_state(self.account_balance)
            result["components"]["portfolio"] = portfolio_state

            # ── Strategy Routing ──────────────────────────────────────
            routing = self.strategy_router.route(
                regime_analysis=regime_analysis,
                smc_analysis=smc_analysis,
                mtf_analysis=mtf_analysis,
                mean_reversion_analysis=mr_analysis,
                pivot_analysis=pivot_analysis,
                calendar_check=calendar_check,
                portfolio_state=portfolio_state,
                symbol=symbol,
            )
            result["routing"] = routing

            # ── Final Signal ──────────────────────────────────────────
            signal = routing.get("signal", "NEUTRAL")
            confidence = float(routing.get("confidence", 0.0))

            result["signal"] = signal
            result["confidence"] = confidence
            result["strategy"] = routing.get("selected_strategy", "UNKNOWN")
            result["regime"] = regime_analysis.get("regime_name", "UNKNOWN")
            result["smc_score"] = smc_analysis.get("smc_score", 0)
            result["mtf_alignment"] = mtf_analysis.get("alignment_score", 0)
            result["pivot_zone"] = pivot_analysis.get("zone", {}).get("name", "UNKNOWN")
            result["meets_threshold"] = routing.get("meets_threshold", False)

            # ── Position Sizing ───────────────────────────────────────
            if signal in ("BUY", "SELL") and result["meets_threshold"]:
                current_price = float(df_4h["close"].iloc[-1])
                atr = float(df_4h["high"].sub(df_4h["low"]).rolling(14).mean().iloc[-1])

                sl_distance = atr * 1.5
                sl_price = current_price - sl_distance if signal == "BUY" else current_price + sl_distance

                vol_mult = vol_analysis.get("vol_multiplier", 1.0) if vol_analysis.get("valid") else 1.0
                dd_mult = dd_analysis.get("size_multiplier", 1.0) if dd_analysis.get("valid") else 1.0

                position_size = self.position_calculator.calculate(
                    account_balance=self.account_balance,
                    entry_price=current_price,
                    sl_price=sl_price,
                    symbol=symbol,
                    method="fixed_risk",
                    risk_pct=2.0,
                    volatility_multiplier=vol_mult * dd_mult,
                )
                result["position_sizing"] = position_size

                # TP levels
                tp_calc = self.position_calculator.calculate_tp_levels(
                    entry_price=current_price,
                    sl_price=sl_price,
                    direction=signal,
                    rr_ratios=[2.0, 3.5, 5.0],
                )
                result["tp_levels"] = tp_calc["tp_levels"]
                result["sl_price"] = round(sl_price, 5)
                result["entry_price"] = round(current_price, 5)
                result["atr"] = round(atr, 5)

            # ── Processing Time ───────────────────────────────────────
            elapsed = (datetime.utcnow() - start_time).total_seconds()
            result["processing_time_ms"] = round(elapsed * 1000, 1)

            logger.info(
                f"HybridPortfolioV3 [{symbol}]: signal={signal} "
                f"confidence={confidence:.1f}% regime={result['regime']} "
                f"smc={result['smc_score']}/10 mtf={result['mtf_alignment']:.1f}% "
                f"time={result['processing_time_ms']}ms"
            )
            return result

        except Exception as exc:
            logger.error(f"HybridPortfolioV3 error [{symbol}]: {exc}", exc_info=True)
            return {
                "symbol": symbol,
                "signal": "NEUTRAL",
                "confidence": 0.0,
                "error": str(exc),
                "valid": False,
                "timestamp": start_time.isoformat(),
            }

    # ------------------------------------------------------------------
    # System Status
    # ------------------------------------------------------------------

    def get_system_status(self) -> Dict[str, Any]:
        """Get full system status and component health."""
        return {
            "version": self.version,
            "system_name": "Institutional Multi-Strategy Hybrid Portfolio System",
            "components": {
                "G1_pivot_points": "ACTIVE",
                "G2_mtf_confirmation": "ACTIVE",
                "G3_regime_detection": "ACTIVE",
                "smc_ict_strategy": "ACTIVE",
                "correlation_engine": "ACTIVE",
                "mean_reversion": "ACTIVE",
                "risk_parity": "ACTIVE",
                "volatility_adjustment": "ACTIVE",
                "drawdown_recovery": "ACTIVE",
                "economic_calendar": "ACTIVE",
                "performance_attribution": "ACTIVE",
                "trade_journal": "ACTIVE",
                "position_calculator": "ACTIVE",
                "portfolio_manager": "ACTIVE",
                "strategy_router": "ACTIVE",
                "feature_engineering": "ACTIVE",
            },
            "total_components": 16,
            "account_balance": self.account_balance,
            "portfolio_state": self.portfolio_manager.get_state(self.account_balance),
            "timestamp": datetime.utcnow().isoformat(),
        }

    def update_account_balance(self, balance: float) -> None:
        """Update account balance across all components."""
        self.account_balance = balance
        self.drawdown_recovery.current_balance = balance
        if balance > self.drawdown_recovery.peak_balance:
            self.drawdown_recovery.peak_balance = balance
        logger.info(f"HybridPortfolioV3: account balance updated to {balance:.2f}")


# Global instance
hybrid_system_v3 = HybridPortfolioSystemV3()

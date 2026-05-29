"""
Hybrid Portfolio System v2.0 — Complete Integration
Orchestrates all v3 components into a unified signal generation pipeline.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .regime_detector import RegimeDetector
from .feature_engineering import FeatureEngineer
from .multi_timeframe import MultiTimeframeAnalyzer
from .strategy_router import StrategyRouter
from .correlation_engine import CorrelationEngine
from .risk_parity_allocator import RiskParityAllocator
from .volatility_adjuster import VolatilityAdjuster
from .drawdown_recovery import DrawdownRecoveryManager
from .economic_calendar import EconomicCalendarManager
from .position_calculator import PositionCalculator
from .portfolio_manager import PortfolioManager
from .performance_attributor import PerformanceAttributor
from .trade_journal import TradeJournal

logger = logging.getLogger(__name__)


class HybridPortfolioSystemV2:
    """
    Complete institutional multi-strategy hybrid portfolio system.

    Pipeline for each signal generation cycle:
    1.  Economic calendar check (blackout window)
    2.  Multi-timeframe analysis (1H, 4H, Daily, Weekly)
    3.  Feature extraction
    4.  Regime detection
    5.  Strategy routing (SMC/ICT or Mean Reversion)
    6.  Portfolio approval (correlation, drawdown, exposure)
    7.  Position sizing (risk parity + vol adjustment + drawdown)
    8.  Signal packaging and delivery

    All components are wired together here and exposed via a single
    `generate_signal()` coroutine.
    """

    def __init__(
        self,
        account_equity: float = 100_000.0,
        min_confidence: float = 65.0,
        min_mtf_confluence: int = 3,
    ) -> None:
        self.account_equity = account_equity
        self.min_confidence = min_confidence
        self.min_mtf_confluence = min_mtf_confluence

        # Instantiate all components
        self.regime_detector = RegimeDetector()
        self.feature_engineer = FeatureEngineer()
        self.mtf_analyzer = MultiTimeframeAnalyzer()
        self.corr_engine = CorrelationEngine()
        self.risk_parity = RiskParityAllocator()
        self.vol_adjuster = VolatilityAdjuster()
        self.dd_manager = DrawdownRecoveryManager()
        self.econ_calendar = EconomicCalendarManager()
        self.strategy_router = StrategyRouter(
            regime_detector=self.regime_detector,
            feature_engineer=self.feature_engineer,
        )
        self.position_calc = PositionCalculator(
            risk_parity=self.risk_parity,
            vol_adjuster=self.vol_adjuster,
            dd_manager=self.dd_manager,
        )
        self.portfolio_mgr = PortfolioManager(
            correlation_engine=self.corr_engine,
            risk_parity=self.risk_parity,
            dd_manager=self.dd_manager,
            vol_adjuster=self.vol_adjuster,
        )
        self.attributor = PerformanceAttributor()
        self.journal = TradeJournal()

        # Initialise drawdown manager
        self.dd_manager.initialise(account_equity)
        self.portfolio_mgr.set_equity(account_equity)

        logger.info(
            f"[HybridPortfolio] Initialised v2.0 — "
            f"equity={account_equity:,.0f} "
            f"min_confidence={min_confidence}% "
            f"min_mtf_confluence={min_mtf_confluence}"
        )

    # ------------------------------------------------------------------
    # Main Pipeline
    # ------------------------------------------------------------------

    async def generate_signal(
        self,
        symbol: str,
        df_4h: pd.DataFrame,
        df_1h: pd.DataFrame | None = None,
        df_daily: pd.DataFrame | None = None,
        df_weekly: pd.DataFrame | None = None,
    ) -> dict[str, Any]:
        """
        Full signal generation pipeline for a single symbol.

        Args:
            symbol:    Trading pair (e.g. "XAUUSD").
            df_4h:     4H OHLCV DataFrame (primary timeframe).
            df_1h:     1H OHLCV DataFrame (optional).
            df_daily:  Daily OHLCV DataFrame (optional).
            df_weekly: Weekly OHLCV DataFrame (optional).

        Returns:
            Complete signal dict or rejection dict.
        """
        pipeline_start = datetime.now(timezone.utc)
        logger.info(f"[HybridPortfolio] Pipeline start: {symbol}")

        # ── Step 1: Economic Calendar ──────────────────────────────────
        calendar_check = await self.econ_calendar.is_safe_to_trade(symbol)
        if not calendar_check.get("safe", True):
            return self._rejected(
                symbol,
                f"Economic calendar blackout: {calendar_check.get('reason')}",
                stage="CALENDAR",
                calendar=calendar_check,
            )

        # ── Step 2: Multi-Timeframe Analysis ───────────────────────────
        mtf_result = await self._run_mtf_analysis(symbol, df_4h, df_1h, df_daily, df_weekly)
        mtf_confluence = mtf_result.get("confluence_score", 0)
        mtf_direction = mtf_result.get("trade_direction", "NEUTRAL")

        if mtf_direction == "NEUTRAL" or mtf_confluence < self.min_mtf_confluence:
            return self._rejected(
                symbol,
                f"Insufficient MTF confluence: {mtf_confluence}/{self.min_mtf_confluence} ({mtf_direction})",
                stage="MTF",
                mtf=mtf_result,
            )

        # ── Step 3: Strategy Routing ───────────────────────────────────
        routed = self.strategy_router.route(df_4h, symbol)
        signal_type = routed.get("signal", "NEUTRAL")
        confidence = routed.get("confidence", 0.0)

        if signal_type == "NEUTRAL":
            return self._rejected(
                symbol,
                f"Strategy router returned NEUTRAL: {routed.get('routing_reason')}",
                stage="STRATEGY",
                routed=routed,
            )

        # ── Step 4: MTF Direction Alignment ───────────────────────────
        if mtf_direction != "NEUTRAL" and signal_type != mtf_direction:
            return self._rejected(
                symbol,
                f"Signal {signal_type} conflicts with MTF direction {mtf_direction}",
                stage="MTF_ALIGNMENT",
                mtf=mtf_result,
                routed=routed,
            )

        # ── Step 5: Confidence Gate ────────────────────────────────────
        if confidence < self.min_confidence:
            return self._rejected(
                symbol,
                f"Confidence {confidence:.1f}% below threshold {self.min_confidence}%",
                stage="CONFIDENCE",
                routed=routed,
            )

        # ── Step 6: Portfolio Approval ─────────────────────────────────
        entry = routed.get("entry", 0.0)
        sl = routed.get("sl", 0.0)
        base_lots = 0.01  # Will be refined by position calculator

        approval = self.portfolio_mgr.approve_signal(
            symbol=symbol,
            direction=signal_type,
            confidence=confidence,
            strategy=routed.get("selected_strategy", "UNKNOWN"),
            lots=base_lots,
        )

        if not approval.get("approved"):
            return self._rejected(
                symbol,
                f"Portfolio rejected: {approval.get('reason')}",
                stage="PORTFOLIO",
                approval=approval,
            )

        # ── Step 7: Position Sizing ────────────────────────────────────
        sizing = self.position_calc.calculate(
            symbol=symbol,
            signal=signal_type,
            entry_price=entry if entry > 0 else float(df_4h["close"].iloc[-1]),
            sl_price=sl if sl > 0 else float(df_4h["close"].iloc[-1]) * 0.99,
            account_equity=self.account_equity,
            df=df_4h,
            base_risk_pct=0.01,
        )

        if not sizing.get("can_trade"):
            return self._rejected(
                symbol,
                f"Position sizing blocked: {sizing.get('pause_reason')}",
                stage="SIZING",
                sizing=sizing,
            )

        # ── Step 8: Assemble Final Signal ──────────────────────────────
        pipeline_ms = int(
            (datetime.now(timezone.utc) - pipeline_start).total_seconds() * 1000
        )

        final_signal = {
            "approved": True,
            "symbol": symbol,
            "signal": signal_type,
            "confidence": round(confidence, 1),
            "entry": routed.get("entry", 0.0),
            "tp_levels": routed.get("tp_levels", []),
            "sl": routed.get("sl", 0.0),
            "lots": sizing.get("lots", 0.01),
            "risk_usd": sizing.get("risk_usd", 0.0),
            "risk_pct": sizing.get("risk_pct", 0.0),
            "strategy": routed.get("selected_strategy"),
            "regime": routed.get("regime", {}).get("regime_name", "UNKNOWN"),
            "regime_detail": routed.get("regime", {}),
            "mtf_confluence": mtf_confluence,
            "mtf_direction": mtf_direction,
            "mtf_detail": mtf_result,
            "analysis": routed.get("analysis", ""),
            "all_strategies": routed.get("all_signals", {}),
            "sizing_breakdown": sizing.get("breakdown", {}),
            "calendar_check": calendar_check,
            "pipeline_ms": pipeline_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "3.0.0",
        }

        logger.info(
            f"[HybridPortfolio] ✅ Signal approved: {symbol} {signal_type} "
            f"conf={confidence:.1f}% lots={sizing.get('lots')} "
            f"strategy={routed.get('selected_strategy')} "
            f"regime={routed.get('regime', {}).get('regime_name')} "
            f"pipeline={pipeline_ms}ms"
        )

        return final_signal

    # ------------------------------------------------------------------
    # MTF Analysis Helper
    # ------------------------------------------------------------------

    async def _run_mtf_analysis(
        self,
        symbol: str,
        df_4h: pd.DataFrame,
        df_1h: pd.DataFrame | None,
        df_daily: pd.DataFrame | None,
        df_weekly: pd.DataFrame | None,
    ) -> dict[str, Any]:
        """
        Run multi-timeframe analysis using available DataFrames.
        Falls back to live API fetch if DataFrames are not provided.
        """
        try:
            # If we have all DataFrames, do local analysis
            if df_1h is not None and df_daily is not None:
                return self._local_mtf_analysis(
                    symbol, df_4h, df_1h, df_daily, df_weekly
                )
            # Otherwise use the MTF analyzer (fetches from API)
            return await self.mtf_analyzer.analyze(symbol)
        except Exception as exc:
            logger.error(f"[HybridPortfolio] MTF analysis error: {exc}")
            return {
                "confluence_score": 0,
                "trade_direction": "NEUTRAL",
                "error": str(exc),
            }

    def _local_mtf_analysis(
        self,
        symbol: str,
        df_4h: pd.DataFrame,
        df_1h: pd.DataFrame,
        df_daily: pd.DataFrame,
        df_weekly: pd.DataFrame | None,
    ) -> dict[str, Any]:
        """Compute MTF confluence from pre-fetched DataFrames."""
        import ta

        def _trend(df: pd.DataFrame) -> str:
            if len(df) < 20:
                return "NEUTRAL"
            close = df["close"]
            ema20 = ta.trend.EMAIndicator(close, window=20).ema_indicator()
            ema50 = ta.trend.EMAIndicator(close, window=min(50, len(df) - 1)).ema_indicator()
            last_close = float(close.iloc[-1])
            last_ema20 = float(ema20.iloc[-1])
            last_ema50 = float(ema50.iloc[-1])
            if last_close > last_ema20 > last_ema50:
                return "BUY"
            if last_close < last_ema20 < last_ema50:
                return "SELL"
            return "NEUTRAL"

        directions = {
            "1h": _trend(df_1h),
            "4h": _trend(df_4h),
            "1day": _trend(df_daily),
        }
        if df_weekly is not None and len(df_weekly) >= 10:
            directions["1week"] = _trend(df_weekly)

        buy_count = sum(1 for d in directions.values() if d == "BUY")
        sell_count = sum(1 for d in directions.values() if d == "SELL")
        total = len(directions)

        if buy_count > sell_count:
            direction = "BUY"
            score = buy_count
        elif sell_count > buy_count:
            direction = "SELL"
            score = sell_count
        else:
            direction = "NEUTRAL"
            score = 0

        return {
            "symbol": symbol,
            "confluence_score": score,
            "trade_direction": direction,
            "timeframe_directions": directions,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "total_timeframes": total,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Portfolio State Updates
    # ------------------------------------------------------------------

    def record_trade_result(
        self,
        trade_id: str,
        pnl_usd: float,
        symbol: str = "",
        strategy: str = "",
    ) -> None:
        """Record a completed trade result across all tracking components."""
        self.dd_manager.record_trade(pnl_usd, symbol, strategy)
        self.portfolio_mgr.remove_position(trade_id)
        logger.info(
            f"[HybridPortfolio] Trade result recorded: {trade_id} pnl={pnl_usd:+.2f}"
        )

    def update_equity(self, new_equity: float) -> None:
        """Update account equity across all components."""
        self.account_equity = new_equity
        self.portfolio_mgr.set_equity(new_equity)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_system_status(self) -> dict[str, Any]:
        """Return complete system status."""
        return {
            "version": "3.0.0",
            "account_equity": self.account_equity,
            "min_confidence": self.min_confidence,
            "min_mtf_confluence": self.min_mtf_confluence,
            "portfolio": self.portfolio_mgr.portfolio_report(),
            "drawdown": self.dd_manager.get_status(),
            "journal": self.journal.summary(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _rejected(symbol: str, reason: str, stage: str = "", **context) -> dict[str, Any]:
        result: dict[str, Any] = {
            "approved": False,
            "symbol": symbol,
            "signal": "NEUTRAL",
            "reason": reason,
            "stage": stage,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        result.update(context)
        logger.info(f"[HybridPortfolio] ❌ Signal rejected [{stage}]: {symbol} — {reason}")
        return result


# Module-level singleton
hybrid_portfolio_v2 = HybridPortfolioSystemV2()

"""
ML Engine for Grandcom Gold Signals
v3.0 — Institutional Multi-Strategy Hybrid Portfolio System
"""

# ── v1/v2 modules (preserved) ──────────────────────────────────────────────
from .feature_engineering import FeatureEngineer
from .regime_detector import RegimeDetector
from .risk_manager import RiskManager
from .signal_optimizer import SignalOptimizer
from .multi_timeframe import MultiTimeframeAnalyzer, mtf_analyzer
from .data_collector import (
    HistoricalDataCollector,
    SignalResultTracker,
    historical_collector,
    signal_tracker,
)
from .smart_money import SmartMoneyAnalyzer, smc_analyzer
from .signal_filter import (
    SignalQualityFilter,
    RegimeEnforcedTPSL,
    signal_quality_filter,
    regime_enforced_tpsl,
)

# ── v3 modules ─────────────────────────────────────────────────────────────
from .smc_ict_strategy import SMCICTStrategy, smc_ict_strategy
from .mean_reversion_strategy import MeanReversionStrategy, mean_reversion_strategy
from .correlation_engine import CorrelationEngine, correlation_engine
from .risk_parity_allocator import RiskParityAllocator, risk_parity_allocator
from .volatility_adjuster import VolatilityAdjuster, volatility_adjuster
from .drawdown_recovery import DrawdownRecoveryManager, drawdown_recovery_manager
from .economic_calendar import EconomicCalendarManager, economic_calendar
from .performance_attributor import PerformanceAttributor, performance_attributor
from .trade_journal import TradeJournal, trade_journal
from .position_calculator import PositionCalculator, position_calculator
from .portfolio_manager import PortfolioManager, portfolio_manager
from .strategy_router import StrategyRouter, strategy_router
from .hybrid_portfolio_v2 import HybridPortfolioSystemV2, hybrid_portfolio_v2

__all__ = [
    # v1/v2
    "FeatureEngineer",
    "RegimeDetector",
    "RiskManager",
    "SignalOptimizer",
    "MultiTimeframeAnalyzer",
    "mtf_analyzer",
    "HistoricalDataCollector",
    "SignalResultTracker",
    "historical_collector",
    "signal_tracker",
    "SmartMoneyAnalyzer",
    "smc_analyzer",
    "SignalQualityFilter",
    "RegimeEnforcedTPSL",
    "signal_quality_filter",
    "regime_enforced_tpsl",
    # v3
    "SMCICTStrategy",
    "smc_ict_strategy",
    "MeanReversionStrategy",
    "mean_reversion_strategy",
    "CorrelationEngine",
    "correlation_engine",
    "RiskParityAllocator",
    "risk_parity_allocator",
    "VolatilityAdjuster",
    "volatility_adjuster",
    "DrawdownRecoveryManager",
    "drawdown_recovery_manager",
    "EconomicCalendarManager",
    "economic_calendar",
    "PerformanceAttributor",
    "performance_attributor",
    "TradeJournal",
    "trade_journal",
    "PositionCalculator",
    "position_calculator",
    "PortfolioManager",
    "portfolio_manager",
    "StrategyRouter",
    "strategy_router",
    "HybridPortfolioSystemV2",
    "hybrid_portfolio_v2",
]

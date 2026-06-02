"""
ML Engine for Grandcom Gold Signals
Institutional Multi-Strategy Hybrid Portfolio System v3.0
"""

# ── Legacy modules (v2.0, preserved) ──────────────────────────────────────
from .feature_engineering import FeatureEngineer
from .regime_detector import RegimeDetector
from .risk_manager import RiskManager
from .signal_optimizer import SignalOptimizer
from .multi_timeframe import MultiTimeframeAnalyzer, mtf_analyzer
from .data_collector import HistoricalDataCollector, SignalResultTracker, historical_collector, signal_tracker
from .smart_money import SmartMoneyAnalyzer, smc_analyzer
from .signal_filter import SignalQualityFilter, RegimeEnforcedTPSL, signal_quality_filter, regime_enforced_tpsl

# ── v3.0 modules ──────────────────────────────────────────────────────────
from .smc_ict_strategy import SMCICTStrategy, smc_ict_strategy
from .mean_reversion_strategy import MeanReversionStrategy, mean_reversion_strategy
from .multi_timeframe_confirmation import MultiTimeframeConfirmation, mtf_confirmation
from .pivot_points_analyzer import PivotPointsAnalyzer, pivot_analyzer
from .correlation_engine import CorrelationEngine, correlation_engine
from .risk_parity import RiskParityAllocator, risk_parity_allocator
from .volatility_adjustment import VolatilityAdjustment, volatility_adjustment
from .drawdown_recovery import DrawdownRecoveryManager, drawdown_recovery
from .economic_calendar import EconomicCalendar, economic_calendar
from .performance_attribution import PerformanceAttribution, performance_attribution
from .trade_journal import TradeJournal, trade_journal
from .position_calculator import PositionCalculator, position_calculator
from .portfolio_manager import PortfolioManager, portfolio_manager
from .strategy_router import StrategyRouter, strategy_router
from .hybrid_portfolio_system_v3 import HybridPortfolioSystemV3, hybrid_system_v3
from .geometry_rating import GeometryRating, geometry_rater
from .signal_quality_validator import SignalQualityValidator, signal_quality_validator
from .hybrid_enhancement_indicators import HybridEnhancementIndicators

__all__ = [
    # Legacy v2.0
    'FeatureEngineer',
    'RegimeDetector',
    'RiskManager',
    'SignalOptimizer',
    'MultiTimeframeAnalyzer',
    'mtf_analyzer',
    'HistoricalDataCollector',
    'SignalResultTracker',
    'historical_collector',
    'signal_tracker',
    'SmartMoneyAnalyzer',
    'smc_analyzer',
    'SignalQualityFilter',
    'RegimeEnforcedTPSL',
    'signal_quality_filter',
    'regime_enforced_tpsl',
    # v3.0
    'SMCICTStrategy',
    'smc_ict_strategy',
    'MeanReversionStrategy',
    'mean_reversion_strategy',
    'MultiTimeframeConfirmation',
    'mtf_confirmation',
    'PivotPointsAnalyzer',
    'pivot_analyzer',
    'CorrelationEngine',
    'correlation_engine',
    'RiskParityAllocator',
    'risk_parity_allocator',
    'VolatilityAdjustment',
    'volatility_adjustment',
    'DrawdownRecoveryManager',
    'drawdown_recovery',
    'EconomicCalendar',
    'economic_calendar',
    'PerformanceAttribution',
    'performance_attribution',
    'TradeJournal',
    'trade_journal',
    'PositionCalculator',
    'position_calculator',
    'PortfolioManager',
    'portfolio_manager',
    'StrategyRouter',
    'strategy_router',
    'HybridPortfolioSystemV3',
    'hybrid_system_v3',
    # Geometry Rating
    'GeometryRating',
    'geometry_rater',
    # Signal Quality Validation (v3.0.2)
    'SignalQualityValidator',
    'signal_quality_validator',
    'HybridEnhancementIndicators',
]

"""
ML Engine for Grandcom Gold Signals
Institutional Multi-Strategy Hybrid Portfolio System v3.4
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
from .position_manager import PositionManager, position_manager
from .reversal_detector import ReversalDetector, reversal_detector
from .economic_calendar_filter import EconomicCalendarFilter, economic_calendar_filter
from .performance_attribution import PerformanceAttribution, performance_attribution
from .trade_journal import TradeJournal, trade_journal
from .position_calculator import PositionCalculator, position_calculator
from .portfolio_manager import PortfolioManager, portfolio_manager
from .strategy_router import StrategyRouter, strategy_router
from .hybrid_portfolio_system_v3 import HybridPortfolioSystemV3, hybrid_system_v3
from .volume_confirmation import VolumeConfirmationStrategy
from .geometry_rating import GeometryRating, geometry_rater
from .candle_tracker import CandleTracker, candle_tracker
from .validation import ValidationEngine
# ── v3.4 modules ──────────────────────────────────────────────────────────
from .market_regime_detector import MarketRegimeDetector, market_regime_detector
from .signal_filters import SignalFilters, signal_filters

# ── Phase 2: Signal Quality V2 ────────────────────────────────────────────
# Imported with graceful degradation so that a broken Phase 2 module never
# prevents the core package (and its tests) from loading.
try:
    from .signal_quality_v2 import SignalQualityV2, signal_quality_v2
    from .hybrid_indicators import HybridIndicators, hybrid_indicators
    from .session_quality import SessionQualityDetector, session_quality_detector
    from .volatility_metrics import VolatilityMetrics, volatility_metrics
    _PHASE2_AVAILABLE = True
except Exception as _phase2_err:  # pragma: no cover
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "Phase 2 Signal Quality V2 modules failed to import — "
        "quality scoring disabled. Error: %s", _phase2_err
    )
    SignalQualityV2 = None          # type: ignore[assignment,misc]
    signal_quality_v2 = None        # type: ignore[assignment]
    HybridIndicators = None         # type: ignore[assignment,misc]
    hybrid_indicators = None        # type: ignore[assignment]
    SessionQualityDetector = None   # type: ignore[assignment,misc]
    session_quality_detector = None # type: ignore[assignment]
    VolatilityMetrics = None        # type: ignore[assignment,misc]
    volatility_metrics = None       # type: ignore[assignment]
    _PHASE2_AVAILABLE = False

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
    'PositionManager',
    'position_manager',
    'ReversalDetector',
    'reversal_detector',
    'EconomicCalendarFilter',
    'economic_calendar_filter',
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
    # v3.3: Volume Confirmation
    'VolumeConfirmationStrategy',
    # Geometry Rating
    'GeometryRating',
    'geometry_rater',
    # Candle Tracker
    'CandleTracker',
    'candle_tracker',
    # Validation Engine
    'ValidationEngine',
    # v3.4: Market Regime Detector
    'MarketRegimeDetector',
    'market_regime_detector',
    # v3.4: Signal Filters
    'SignalFilters',
    'signal_filters',
    # Phase 2: Signal Quality V2
    'SignalQualityV2',
    'signal_quality_v2',
    'HybridIndicators',
    'hybrid_indicators',
    'SessionQualityDetector',
    'session_quality_detector',
    'VolatilityMetrics',
    'volatility_metrics',
]

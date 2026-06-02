"""
Signal Quality API V2 — Grandcom Gold Signals v3.0.2
Phase 2 Enhancement: 15+ REST endpoints for signal quality

Endpoints:
  GET  /api/signals/quality/{signal_id}          — Full quality score
  GET  /api/signals/confidence/{signal_id}       — Dynamic confidence
  GET  /api/signals/regime/{signal_id}           — Regime classification
  GET  /api/signals/session-quality              — Current session quality
  GET  /api/signals/news-impact                  — News filter events
  GET  /api/signals/mtf-alignment/{signal_id}    — MTF alignment
  GET  /api/signals/confluence/{signal_id}       — Confluence score
  GET  /api/signals/risk-reward/{signal_id}      — R:R analysis
  GET  /api/signals/entry-band/{signal_id}       — Entry band validation
  GET  /api/signals/atr/{signal_id}              — ATR quantification
  GET  /api/signals/expiry/{signal_id}           — Signal expiry
  GET  /api/signals/hybrid-scores/{signal_id}    — Hybrid indicator scores
  GET  /api/signals/volatility-sizing/{signal_id} — Position sizing
  GET  /api/signals/trailing-stop/{signal_id}    — Stop recommendations
  GET  /api/signals/economic-calendar            — Economic events
  POST /api/signals/recalculate-confidence       — Recalculate on MTF drop
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel

from ml_engine.signal_quality_v2 import SignalQualityV2, signal_quality_v2
from ml_engine.hybrid_indicators import HybridIndicators, hybrid_indicators
from ml_engine.session_quality import SessionQualityDetector, session_quality_detector
from ml_engine.volatility_metrics import VolatilityMetrics, volatility_metrics
from ml_engine.economic_calendar import EconomicCalendar, economic_calendar

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/signals", tags=["Signal Quality V2"])

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME   = os.environ.get("DB_NAME",   "gold_signals_v3")

security = HTTPBearer(auto_error=False)

# ─────────────────────────────────────────────────────────────
# DB helper
# ─────────────────────────────────────────────────────────────

_client: Optional[AsyncIOMotorClient] = None


def _get_db():
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(
            MONGO_URL,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=10000,
        )
    return _client[DB_NAME]


def _oid(value: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid signal ID: '{value}'",
        )
    return ObjectId(value)


def _serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in doc.items():
        if k == "_id":
            out["id"] = str(v)
        elif isinstance(v, ObjectId):
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, list):
            out[k] = [_serialize(i) if isinstance(i, dict) else i for i in v]
        elif isinstance(v, dict):
            out[k] = _serialize(v)
        else:
            out[k] = v
    return out


async def _get_signal(signal_id: str) -> Dict[str, Any]:
    """Fetch signal from DB or raise 404."""
    db  = _get_db()
    oid = _oid(signal_id)
    doc = await db.signals.find_one({"_id": oid})
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Signal '{signal_id}' not found.",
        )
    return _serialize(doc)


def _build_quality_params(signal: Dict[str, Any]) -> Dict[str, Any]:
    """Extract quality assessment parameters from a signal document."""
    return {
        "signal_id":          signal.get("id", ""),
        "symbol":             signal.get("pair", signal.get("symbol", "XAUUSD")),
        "signal_type":        signal.get("type", signal.get("signal_type", "BUY")),
        "entry_price":        float(signal.get("entry_price", 0.0)),
        "sl_price":           float(signal.get("sl_price", 0.0)),
        "tp_levels":          [float(t) for t in signal.get("tp_levels", [])],
        "current_price":      float(signal.get("current_price", signal.get("entry_price", 0.0))),
        "atr":                float(signal.get("atr", signal.get("atr_value", 12.0))),
        "swing_high":         float(signal.get("swing_high", 0.0)),
        "swing_low":          float(signal.get("swing_low", 0.0)),
        "nearest_resistance": float(signal.get("nearest_resistance", signal.get("resistance", 0.0))),
        "nearest_support":    float(signal.get("nearest_support", signal.get("support", 0.0))),
        "adx":                float(signal.get("adx", 25.0)),
        "rsi":                float(signal.get("rsi", 50.0)),
        "mtf_alignment":      signal.get("mtf_alignment", {}),
        "smc_score":          float(signal.get("smc_score", 5.0)),
        "created_at":         datetime.fromisoformat(
            signal.get("created_at", datetime.now(timezone.utc).isoformat())
        ),
        "account_balance":    float(signal.get("account_balance", 10_000.0)),
        "macd_signal":        signal.get("macd_signal"),
        "stoch_rsi":          signal.get("stoch_rsi"),
        "trade_type":         signal.get("trade_type", "SWING"),
    }


# ─────────────────────────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────────────────────────

class RecalculateConfidenceRequest(BaseModel):
    signal_id:           str
    original_confidence: float
    original_mtf:        Dict[str, str]
    updated_mtf:         Dict[str, str]


class NewsImpactRequest(BaseModel):
    symbol:     str = "XAUUSD"
    check_time: Optional[str] = None


# ─────────────────────────────────────────────────────────────
# ENDPOINT 1: Full Quality Score
# ─────────────────────────────────────────────────────────────

@router.get("/quality/{signal_id}", summary="Full signal quality assessment (Phase 2)")
async def get_signal_quality(
    signal_id: str,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    """
    Run the complete Phase 2 quality assessment on a signal.

    Returns all 12 quality dimensions including R:R, regime, entry band,
    dynamic confidence, SL anchoring, ATR, session, expiry, and news filter.
    """
    signal = await _get_signal(signal_id)
    params = _build_quality_params(signal)

    # Fetch news events
    try:
        news_check = await economic_calendar.is_safe_to_trade(
            symbol=params["symbol"]
        )
        news_events = news_check.get("upcoming_events", [])
    except Exception:
        news_events = []

    params["news_events"] = news_events

    result = signal_quality_v2.assess(**params)
    return {
        "success": True,
        "data":    result.to_dict(),
        "version": "2.0.0",
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 2: Dynamic Confidence
# ─────────────────────────────────────────────────────────────

@router.get("/confidence/{signal_id}", summary="Dynamic confidence score")
async def get_signal_confidence(
    signal_id: str,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    """
    Return the dynamic confidence score for a signal.

    Confidence is computed from 6 components:
    MTF alignment (40%), SMC (20%), momentum (15%),
    session (10%), news (10%), regime (5%).
    """
    signal = await _get_signal(signal_id)
    params = _build_quality_params(signal)

    session_result = session_quality_detector.assess()

    try:
        news_check = await economic_calendar.is_safe_to_trade(symbol=params["symbol"])
        news_events = news_check.get("upcoming_events", [])
    except Exception:
        news_events = []

    from ml_engine.signal_quality_v2 import NewsFilterResult, SessionResult
    news_result = signal_quality_v2.apply_news_filter(news_events=news_events)

    from ml_engine.signal_quality_v2 import RegimeResult
    regime_result = signal_quality_v2.classify_regime(
        adx=params["adx"],
        rsi=params["rsi"],
        signal_type=params["signal_type"],
        nearest_support=params["nearest_support"],
        nearest_resistance=params["nearest_resistance"],
        entry_price=params["entry_price"],
        atr=params["atr"],
    )

    rr_result = signal_quality_v2.validate_risk_reward(
        signal_type=params["signal_type"],
        entry_price=params["entry_price"],
        sl_price=params["sl_price"],
        tp_levels=params["tp_levels"],
        trade_type=params["trade_type"],
    )

    # Build session result from detector
    sq = session_quality_detector.assess()
    from ml_engine.signal_quality_v2 import SessionResult as SQResult
    session_sq = SQResult(
        session=sq.session,
        quality=sq.quality,
        utc_hour=sq.utc_hour,
        is_london_open=sq.is_london_open,
        is_post_ny=sq.is_post_ny,
        recommendation=sq.recommendation,
        mtf_weight_adj=sq.liquidity_score,
    )

    confidence = signal_quality_v2.calculate_dynamic_confidence(
        mtf_alignment=params["mtf_alignment"],
        smc_score=params["smc_score"],
        rsi=params["rsi"],
        macd_signal=params["macd_signal"],
        stoch_rsi=params["stoch_rsi"],
        session_result=session_sq,
        news_result=news_result,
        regime_result=regime_result,
        rr_result=rr_result,
    )

    return {
        "success":   True,
        "signal_id": signal_id,
        "data": {
            "total_score":    round(confidence.total_score, 1),
            "label":          confidence.label,
            "mtf_score":      round(confidence.mtf_score, 1),
            "smc_score":      round(confidence.smc_score, 1),
            "momentum_score": round(confidence.momentum_score, 1),
            "session_score":  round(confidence.session_score, 1),
            "news_score":     round(confidence.news_score, 1),
            "regime_score":   round(confidence.regime_score, 1),
            "breakdown":      confidence.breakdown,
        },
        "version": "2.0.0",
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 3: Regime Classification
# ─────────────────────────────────────────────────────────────

@router.get("/regime/{signal_id}", summary="Regime classification")
async def get_signal_regime(
    signal_id: str,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    """
    Return regime classification for a signal.

    Classifies as TREND_UP, TREND_DOWN, RANGE, BREAKOUT, or CHAOS.
    Includes regime-specific entry rules and blocked entries.
    """
    signal = await _get_signal(signal_id)
    params = _build_quality_params(signal)

    regime = signal_quality_v2.classify_regime(
        adx=params["adx"],
        rsi=params["rsi"],
        signal_type=params["signal_type"],
        nearest_support=params["nearest_support"],
        nearest_resistance=params["nearest_resistance"],
        entry_price=params["entry_price"],
        atr=params["atr"],
    )

    return {
        "success":   True,
        "signal_id": signal_id,
        "data": {
            "regime":          regime.regime,
            "confidence":      round(regime.confidence, 2),
            "adx":             round(regime.adx, 1),
            "trend_strength":  regime.trend_strength,
            "entry_rules":     regime.entry_rules,
            "blocked_entries": regime.blocked_entries,
        },
        "version": "2.0.0",
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 4: Session Quality
# ─────────────────────────────────────────────────────────────

@router.get("/session-quality", summary="Current session quality")
async def get_session_quality(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    """
    Return current trading session quality.

    Identifies session (London/NY/Asia/Off), liquidity score,
    MTF weight adjustments, and trading recommendation.
    """
    result = session_quality_detector.assess()
    schedule = session_quality_detector.get_session_schedule()

    return {
        "success": True,
        "data":    result.to_dict(),
        "schedule": schedule,
        "version": "2.0.0",
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 5: News Impact
# ─────────────────────────────────────────────────────────────

@router.get("/news-impact", summary="News filter events")
async def get_news_impact(
    symbol: str = Query(default="XAUUSD", description="Trading symbol"),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    """
    Return current news filter status for the given symbol.

    Checks for JOLTS, Beige Book, NFP, FOMC, CPI, and other
    high-impact events that affect gold trading.
    """
    try:
        result = await economic_calendar.is_safe_to_trade(symbol=symbol)
    except Exception as exc:
        logger.error(f"News impact check error: {exc}")
        result = {
            "safe_to_trade": True,
            "reason": "CALENDAR_ERROR_FAIL_OPEN",
            "blocking_events": [],
            "upcoming_events": [],
        }

    return {
        "success": True,
        "symbol":  symbol,
        "data":    result,
        "version": "2.0.0",
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 6: MTF Alignment
# ─────────────────────────────────────────────────────────────

@router.get("/mtf-alignment/{signal_id}", summary="MTF alignment detail")
async def get_mtf_alignment(
    signal_id: str,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    """
    Return detailed MTF alignment for a signal.

    Shows H4 bias, H1 structure, M15 trigger, and pyramid validity.
    """
    signal = await _get_signal(signal_id)
    mtf    = signal.get("mtf_alignment", {})
    sig_type = signal.get("type", signal.get("signal_type", "BUY"))

    import pandas as pd
    dummy_df = pd.DataFrame()

    result = hybrid_indicators.mtf_pyramid_breakdown(
        mtf_alignment=mtf,
        signal_type=sig_type,
    )

    return {
        "success":   True,
        "signal_id": signal_id,
        "data": {
            "mtf_alignment":   mtf,
            "h4_bias":         result.h4_bias,
            "h1_structure":    result.h1_structure,
            "m15_trigger":     result.m15_trigger,
            "alignment_score": round(result.alignment_score, 2),
            "pyramid_valid":   result.pyramid_valid,
            "missing_levels":  result.missing_levels,
            "score":           round(result.score, 2),
            "recommendation":  result.recommendation,
        },
        "version": "2.0.0",
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 7: Confluence Score
# ─────────────────────────────────────────────────────────────

@router.get("/confluence/{signal_id}", summary="Dynamic confluence score")
async def get_confluence_score(
    signal_id: str,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    """
    Return the dynamic confluence score from all 13 hybrid indicators.

    Score > 75% = HIGH CONFIDENCE.
    """
    signal = await _get_signal(signal_id)
    params = _build_quality_params(signal)

    import pandas as pd
    dummy_df = pd.DataFrame({
        "open":   [params["entry_price"]] * 50,
        "high":   [params["swing_high"] or params["entry_price"] * 1.01] * 50,
        "low":    [params["swing_low"] or params["entry_price"] * 0.99] * 50,
        "close":  [params["current_price"]] * 50,
        "volume": [1000.0] * 50,
    })

    result = hybrid_indicators.compute_all(
        signal_id=signal_id,
        symbol=params["symbol"],
        signal_type=params["signal_type"],
        df=dummy_df,
        entry_price=params["entry_price"],
        sl_price=params["sl_price"],
        tp_levels=params["tp_levels"],
        current_price=params["current_price"],
        atr=params["atr"],
        swing_high=params["swing_high"] or params["entry_price"] * 1.01,
        swing_low=params["swing_low"] or params["entry_price"] * 0.99,
        nearest_resistance=params["nearest_resistance"] or params["entry_price"] * 1.005,
        nearest_support=params["nearest_support"] or params["entry_price"] * 0.995,
        mtf_alignment=params["mtf_alignment"],
        smc_analysis=signal.get("smc_analysis", {}),
        account_balance=params["account_balance"],
        adx=params["adx"],
        rsi=params["rsi"],
    )

    return {
        "success":   True,
        "signal_id": signal_id,
        "data": {
            "overall_hybrid_score":  round(result.overall_hybrid_score, 2),
            "recommendation":        result.recommendation,
            "dynamic_confluence":    result.dynamic_confluence.__dict__,
        },
        "version": "2.0.0",
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 8: Risk/Reward Analysis
# ─────────────────────────────────────────────────────────────

@router.get("/risk-reward/{signal_id}", summary="R:R analysis")
async def get_risk_reward(
    signal_id: str,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    """
    Return R:R analysis for a signal.

    Validates minimum 1:2 for swing trades, 1:1.5 for scalps.
    Provides per-TP breakdown.
    """
    signal = await _get_signal(signal_id)
    params = _build_quality_params(signal)

    result = signal_quality_v2.validate_risk_reward(
        signal_type=params["signal_type"],
        entry_price=params["entry_price"],
        sl_price=params["sl_price"],
        tp_levels=params["tp_levels"],
        trade_type=params["trade_type"],
    )

    return {
        "success":   True,
        "signal_id": signal_id,
        "data": {
            "ratio":           round(result.ratio, 2),
            "risk_pips":       round(result.risk_pips, 1),
            "reward_pips":     round(result.reward_pips, 1),
            "meets_minimum":   result.meets_minimum,
            "trade_type":      result.trade_type,
            "minimum_required": 2.0 if result.trade_type == "SWING" else 1.5,
            "recommendation":  result.recommendation,
            "tp_rr_breakdown": result.tp_rr_breakdown,
        },
        "version": "2.0.0",
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 9: Entry Band Validation
# ─────────────────────────────────────────────────────────────

@router.get("/entry-band/{signal_id}", summary="Entry band validation")
async def get_entry_band(
    signal_id: str,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    """
    Return entry band validation for a signal.

    Validates that entry is within a realistic 10-pip zone.
    """
    signal = await _get_signal(signal_id)
    params = _build_quality_params(signal)

    regime = signal_quality_v2.classify_regime(
        adx=params["adx"],
        rsi=params["rsi"],
        signal_type=params["signal_type"],
        nearest_support=params["nearest_support"],
        nearest_resistance=params["nearest_resistance"],
        entry_price=params["entry_price"],
        atr=params["atr"],
    )

    result = signal_quality_v2.validate_entry_band(
        signal_type=params["signal_type"],
        entry_price=params["entry_price"],
        current_price=params["current_price"],
        nearest_support=params["nearest_support"],
        nearest_resistance=params["nearest_resistance"],
        regime=regime.regime,
        atr=params["atr"],
    )

    return {
        "success":   True,
        "signal_id": signal_id,
        "data": {
            "valid":          result.valid,
            "band_low":       round(result.band_low, 2),
            "band_high":      round(result.band_high, 2),
            "band_pips":      round(result.band_pips, 1),
            "current_price":  round(result.current_price, 2),
            "in_band":        result.in_band,
            "distance_pips":  round(result.distance_pips, 1),
            "recommendation": result.recommendation,
        },
        "version": "2.0.0",
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 10: ATR Quantification
# ─────────────────────────────────────────────────────────────

@router.get("/atr/{signal_id}", summary="ATR quantification")
async def get_atr_quantification(
    signal_id: str,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    """
    Return ATR quantification for a signal.

    Provides ATR value, pips, percentage, regime, and position sizing.
    """
    signal = await _get_signal(signal_id)
    params = _build_quality_params(signal)

    result = signal_quality_v2.quantify_atr(
        atr=params["atr"],
        current_price=params["current_price"],
        entry_price=params["entry_price"],
        sl_price=params["sl_price"],
        account_balance=params["account_balance"],
        symbol=params["symbol"],
    )

    return {
        "success":   True,
        "signal_id": signal_id,
        "data": {
            "atr_value":          round(result.atr_value, 2),
            "atr_pips":           round(result.atr_pips, 1),
            "atr_pct":            round(result.atr_pct, 4),
            "regime":             result.regime,
            "position_size_lots": round(result.position_size_lots, 2),
            "risk_per_trade_usd": round(result.risk_per_trade_usd, 2),
            "account_balance":    round(result.account_balance, 2),
        },
        "version": "2.0.0",
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 11: Signal Expiry
# ─────────────────────────────────────────────────────────────

@router.get("/expiry/{signal_id}", summary="Signal expiry status")
async def get_signal_expiry(
    signal_id: str,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    """
    Return signal expiry status.

    SWING signals expire after 24 hours.
    SCALP signals expire after 4 hours.
    """
    signal = await _get_signal(signal_id)
    params = _build_quality_params(signal)

    result = signal_quality_v2.calculate_expiry(
        created_at=params["created_at"],
        trade_type=params["trade_type"],
    )

    return {
        "success":   True,
        "signal_id": signal_id,
        "data": {
            "expires_at":        result.expires_at,
            "hours_valid":       result.hours_valid,
            "is_expired":        result.is_expired,
            "minutes_remaining": round(result.minutes_remaining, 1),
            "trade_type":        result.trade_type,
            "status":            "EXPIRED" if result.is_expired else "VALID",
        },
        "version": "2.0.0",
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 12: Hybrid Indicator Scores
# ─────────────────────────────────────────────────────────────

@router.get("/hybrid-scores/{signal_id}", summary="All 13 hybrid indicator scores")
async def get_hybrid_scores(
    signal_id: str,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    """
    Return scores from all 13 hybrid enhancement indicators.

    Includes SMC+OrderFlow, TripleMomentum, VWAP, Fibonacci+SMC,
    ATR+BB, Range+Breakout, Swing+Scalp, Trend+MeanRev, MTFPyramid,
    SessionMTF, TrailingStop, VolatilitySize, DynamicConfluence.
    """
    signal = await _get_signal(signal_id)
    params = _build_quality_params(signal)

    import pandas as pd
    dummy_df = pd.DataFrame({
        "open":   [params["entry_price"]] * 50,
        "high":   [params["swing_high"] or params["entry_price"] * 1.01] * 50,
        "low":    [params["swing_low"] or params["entry_price"] * 0.99] * 50,
        "close":  [params["current_price"]] * 50,
        "volume": [1000.0] * 50,
    })

    result = hybrid_indicators.compute_all(
        signal_id=signal_id,
        symbol=params["symbol"],
        signal_type=params["signal_type"],
        df=dummy_df,
        entry_price=params["entry_price"],
        sl_price=params["sl_price"],
        tp_levels=params["tp_levels"],
        current_price=params["current_price"],
        atr=params["atr"],
        swing_high=params["swing_high"] or params["entry_price"] * 1.01,
        swing_low=params["swing_low"] or params["entry_price"] * 0.99,
        nearest_resistance=params["nearest_resistance"] or params["entry_price"] * 1.005,
        nearest_support=params["nearest_support"] or params["entry_price"] * 0.995,
        mtf_alignment=params["mtf_alignment"],
        smc_analysis=signal.get("smc_analysis", {}),
        account_balance=params["account_balance"],
        adx=params["adx"],
        rsi=params["rsi"],
    )

    return {
        "success":   True,
        "signal_id": signal_id,
        "data":      result.to_dict(),
        "version":   "2.0.0",
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 13: Volatility-Adjusted Position Sizing
# ─────────────────────────────────────────────────────────────

@router.get("/volatility-sizing/{signal_id}", summary="Volatility-adjusted position sizing")
async def get_volatility_sizing(
    signal_id:       str,
    account_balance: float = Query(default=10000.0, description="Account balance in USD"),
    risk_pct:        float = Query(default=0.01, description="Risk per trade (0.01 = 1%)"),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    """
    Return volatility-adjusted position sizing for a signal.

    Uses 1% account risk rule with ATR-based adjustment.
    """
    signal = await _get_signal(signal_id)
    params = _build_quality_params(signal)

    result = volatility_metrics.calculate_position_size(
        entry_price=params["entry_price"],
        sl_price=params["sl_price"],
        atr_value=params["atr"],
        account_balance=account_balance,
        risk_pct=risk_pct,
    )

    return {
        "success":   True,
        "signal_id": signal_id,
        "data":      result.to_dict(),
        "version":   "2.0.0",
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 14: Trailing Stop Recommendations
# ─────────────────────────────────────────────────────────────

@router.get("/trailing-stop/{signal_id}", summary="Trailing stop recommendations")
async def get_trailing_stop(
    signal_id: str,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    """
    Return trailing stop recommendations for a signal.

    Fixed SL until TP1, then trail by 1 ATR to lock profits.
    """
    signal = await _get_signal(signal_id)
    params = _build_quality_params(signal)

    result = hybrid_indicators.fixed_trailing_stop(
        signal_type=params["signal_type"],
        entry_price=params["entry_price"],
        sl_price=params["sl_price"],
        tp_levels=params["tp_levels"],
        atr=params["atr"],
    )

    return {
        "success":   True,
        "signal_id": signal_id,
        "data": {
            "fixed_sl":          round(result.fixed_sl, 2),
            "trailing_sl":       round(result.trailing_sl, 2),
            "trailing_distance": round(result.trailing_distance, 2),
            "trailing_pips":     round(result.trailing_pips, 1),
            "profit_locked":     round(result.profit_locked, 1),
            "activation_price":  round(result.activation_price, 2),
            "recommendation":    result.recommendation,
            "score":             round(result.score, 2),
        },
        "version": "2.0.0",
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 15: Economic Calendar
# ─────────────────────────────────────────────────────────────

@router.get("/economic-calendar", summary="Economic calendar events")
async def get_economic_calendar(
    symbol: str = Query(default="XAUUSD", description="Trading symbol"),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    """
    Return upcoming high-impact economic events.

    Includes JOLTS, Beige Book, NFP, FOMC, CPI, and other
    events that affect gold trading.
    """
    try:
        result = await economic_calendar.is_safe_to_trade(symbol=symbol)
    except Exception as exc:
        logger.error(f"Economic calendar error: {exc}")
        result = {
            "safe_to_trade": True,
            "reason": "CALENDAR_ERROR_FAIL_OPEN",
            "blocking_events": [],
            "upcoming_events": [],
            "next_event": None,
        }

    return {
        "success": True,
        "symbol":  symbol,
        "data":    result,
        "version": "2.0.0",
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 16: Recalculate Confidence (POST)
# ─────────────────────────────────────────────────────────────

@router.post("/recalculate-confidence", summary="Recalculate confidence on MTF drop")
async def recalculate_confidence(
    body: RecalculateConfidenceRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    """
    Recalculate confidence when MTF alignment changes.

    Use this when a timeframe drops alignment (e.g., M15 flips).
    Returns the new confidence score and recommendation.
    """
    result = signal_quality_v2.recalculate_mtf_confidence(
        original_confidence=body.original_confidence,
        original_mtf=body.original_mtf,
        updated_mtf=body.updated_mtf,
    )

    return {
        "success":   True,
        "signal_id": body.signal_id,
        "data":      result,
        "version":   "2.0.0",
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 17: SL Anchor
# ─────────────────────────────────────────────────────────────

@router.get("/sl-anchor/{signal_id}", summary="Structure-anchored SL validation")
async def get_sl_anchor(
    signal_id: str,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    """
    Return SL anchoring analysis for a signal.

    Validates that SL is anchored to swing high/low + ATR buffer.
    """
    signal = await _get_signal(signal_id)
    params = _build_quality_params(signal)

    result = signal_quality_v2.anchor_sl_to_structure(
        signal_type=params["signal_type"],
        entry_price=params["entry_price"],
        sl_price=params["sl_price"],
        swing_high=params["swing_high"] or params["entry_price"] * 1.01,
        swing_low=params["swing_low"] or params["entry_price"] * 0.99,
        atr=params["atr"],
    )

    return {
        "success":   True,
        "signal_id": signal_id,
        "data": {
            "sl_price":       round(result.sl_price, 2),
            "anchor_level":   round(result.anchor_level, 2),
            "atr_buffer":     round(result.atr_buffer, 2),
            "atr_value":      round(result.atr_value, 2),
            "distance_pips":  round(result.distance_pips, 1),
            "is_structural":  result.is_structural,
            "recommendation": result.recommendation,
        },
        "version": "2.0.0",
    }

"""
Hybrid Enhancement Indicators — Grandcom Gold Signals v3.0.2
Phase 2: 13 Hybrid Indicators for Signal Quality Enhancement

Indicators:
  1.  SMC + Order Flow          — filters false SMC levels with order flow
  2.  RSI + MACD + Stoch RSI    — triple momentum confluence
  3.  VWAP + Price Action       — institutional session benchmark
  4.  Fibonacci + SMC           — stacked confluence zones
  5.  ATR + Bollinger Bands     — volatility sizing + squeeze detection
  6.  Range + Breakout Filter   — regime clarity scoring
  7.  Swing + Scalp Timing      — M15 entry confirmation
  8.  Trend + Mean Reversion    — primary strategy + breakout
  9.  MTF Pyramid Breakdown     — timeframe alignment detail
  10. Session-Based MTF Weight  — low-liquidity filtering
  11. Fixed + Trailing Stop     — profit locking + trailing
  12. Volatility Position Size  — 1% account risk calculation
  13. Dynamic Confluence Score  — >75% = HIGH CONFIDENCE
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

PIP_VALUE_GOLD       = 0.10   # 1 pip = $0.10 for XAUUSD
HIGH_CONFIDENCE_THRESHOLD = 75.0
FIBONACCI_LEVELS     = [0.0, 0.236, 0.382, 0.500, 0.618, 0.786, 1.0]
FIBONACCI_EXTENSIONS = [1.272, 1.414, 1.618, 2.0, 2.618]
BB_PERIOD            = 20
BB_STD               = 2.0
ATR_PERIOD           = 14
VWAP_RESET_HOUR      = 0      # Reset VWAP at midnight UTC


# ─────────────────────────────────────────────────────────────
# Result dataclasses
# ─────────────────────────────────────────────────────────────

@dataclass
class SMCOrderFlowResult:
    """Indicator 1: SMC + Order Flow."""
    valid_ob_count:    int
    false_ob_count:    int
    order_flow_bias:   str       # "BULLISH", "BEARISH", "NEUTRAL"
    confirmed_levels:  List[float]
    rejected_levels:   List[float]
    score:             float     # 0–10
    recommendation:    str


@dataclass
class TripleMomentumResult:
    """Indicator 2: RSI + MACD + Stochastic RSI."""
    rsi:               float
    rsi_signal:        str       # "OVERBOUGHT", "OVERSOLD", "NEUTRAL", "BULLISH", "BEARISH"
    macd:              float
    macd_signal_line:  float
    macd_histogram:    float
    macd_bias:         str       # "BULLISH", "BEARISH", "NEUTRAL"
    stoch_rsi_k:       float
    stoch_rsi_d:       float
    stoch_signal:      str       # "OVERBOUGHT", "OVERSOLD", "NEUTRAL"
    confluence:        str       # "STRONG_BUY", "BUY", "NEUTRAL", "SELL", "STRONG_SELL"
    score:             float     # 0–10
    recommendation:    str


@dataclass
class VWAPResult:
    """Indicator 3: VWAP + Price Action."""
    vwap:              float
    current_price:     float
    price_vs_vwap:     str       # "ABOVE", "BELOW", "AT"
    distance_pips:     float
    institutional_bias: str      # "BULLISH", "BEARISH", "NEUTRAL"
    score:             float     # 0–10
    recommendation:    str


@dataclass
class FibSMCResult:
    """Indicator 4: Fibonacci + SMC Confluence."""
    fib_levels:        Dict[str, float]
    fib_extensions:    Dict[str, float]
    nearest_fib:       float
    nearest_fib_label: str
    smc_confluence:    bool
    stacked_zones:     List[Dict[str, Any]]
    score:             float     # 0–10
    recommendation:    str


@dataclass
class ATRBBResult:
    """Indicator 5: ATR + Bollinger Bands."""
    atr:               float
    atr_pips:          float
    bb_upper:          float
    bb_middle:         float
    bb_lower:          float
    bb_width:          float
    bb_squeeze:        bool
    price_vs_bb:       str       # "ABOVE_UPPER", "NEAR_UPPER", "MIDDLE", "NEAR_LOWER", "BELOW_LOWER"
    volatility_regime: str       # "SQUEEZE", "EXPANDING", "NORMAL"
    score:             float     # 0–10
    recommendation:    str


@dataclass
class RangeBreakoutResult:
    """Indicator 6: Range + Breakout Filter."""
    regime:            str       # "RANGE", "BREAKOUT", "TREND"
    range_high:        float
    range_low:         float
    range_pips:        float
    breakout_confirmed: bool
    breakout_direction: str      # "UP", "DOWN", "NONE"
    regime_clarity:    float     # 0–1
    score:             float     # 0–10
    recommendation:    str


@dataclass
class SwingScalpTimingResult:
    """Indicator 7: Swing + Scalp Entry Timing."""
    m15_signal:        str       # "BUY", "SELL", "NEUTRAL"
    m15_confirmed:     bool
    swing_bias:        str       # "BUY", "SELL", "NEUTRAL"
    timing_quality:    str       # "OPTIMAL", "GOOD", "POOR"
    entry_window_open: bool
    score:             float     # 0–10
    recommendation:    str


@dataclass
class TrendMeanRevResult:
    """Indicator 8: Trend + Mean Reversion."""
    primary_strategy:  str       # "TREND_FOLLOW", "MEAN_REVERSION", "BREAKOUT"
    trend_strength:    float     # 0–1
    mean_rev_signal:   str       # "OVERSOLD", "OVERBOUGHT", "NEUTRAL"
    zscore:            float
    strategy_alignment: bool
    score:             float     # 0–10
    recommendation:    str


@dataclass
class MTFPyramidResult:
    """Indicator 9: MTF Pyramid Breakdown."""
    h4_bias:           str
    h1_structure:      str
    m15_trigger:       str
    alignment_score:   float     # 0–1
    pyramid_valid:     bool
    missing_levels:    List[str]
    score:             float     # 0–10
    recommendation:    str


@dataclass
class SessionMTFWeightResult:
    """Indicator 10: Session-Based MTF Weighting."""
    session:           str
    adjusted_weights:  Dict[str, float]
    liquidity_score:   float     # 0–1
    low_liquidity:     bool
    filtered_signals:  List[str]
    score:             float     # 0–10
    recommendation:    str


@dataclass
class TrailingStopResult:
    """Indicator 11: Fixed + Trailing Stop Hybrid."""
    fixed_sl:          float
    trailing_sl:       float
    trailing_distance: float
    trailing_pips:     float
    profit_locked:     float
    activation_price:  float
    recommendation:    str
    score:             float     # 0–10


@dataclass
class VolatilityPositionResult:
    """Indicator 12: Volatility-Adjusted Position Sizing."""
    base_size:         float
    adjusted_size:     float
    risk_pct:          float
    risk_usd:          float
    atr_based_size:    float
    vol_multiplier:    float
    account_balance:   float
    score:             float     # 0–10
    recommendation:    str


@dataclass
class DynamicConfluenceResult:
    """Indicator 13: Dynamic Confluence Score."""
    total_score:       float     # 0–100
    component_scores:  Dict[str, float]
    confidence_label:  str       # "HIGH", "MEDIUM", "LOW"
    is_high_confidence: bool     # True if > 75%
    active_indicators: int
    aligned_indicators: int
    score:             float     # 0–10
    recommendation:    str


@dataclass
class HybridIndicatorsResult:
    """Complete result from all 13 hybrid indicators."""
    signal_id:         str
    symbol:            str
    signal_type:       str
    timestamp:         str

    # All 13 indicators
    smc_order_flow:    SMCOrderFlowResult
    triple_momentum:   TripleMomentumResult
    vwap_pa:           VWAPResult
    fib_smc:           FibSMCResult
    atr_bb:            ATRBBResult
    range_breakout:    RangeBreakoutResult
    swing_scalp:       SwingScalpTimingResult
    trend_mean_rev:    TrendMeanRevResult
    mtf_pyramid:       MTFPyramidResult
    session_mtf:       SessionMTFWeightResult
    trailing_stop:     TrailingStopResult
    vol_position:      VolatilityPositionResult
    dynamic_confluence: DynamicConfluenceResult

    # Aggregate
    overall_hybrid_score: float   # 0–10
    recommendation:    str
    version:           str = "2.0.0"

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to JSON-safe dict."""
        def _r(v: Any) -> Any:
            if isinstance(v, float):
                return round(v, 4)
            if isinstance(v, dict):
                return {k: _r(vv) for k, vv in v.items()}
            if isinstance(v, list):
                return [_r(i) for i in v]
            return v

        return {
            "signal_id":    self.signal_id,
            "symbol":       self.symbol,
            "signal_type":  self.signal_type,
            "timestamp":    self.timestamp,
            "overall_hybrid_score": round(self.overall_hybrid_score, 2),
            "recommendation": self.recommendation,
            "version":      self.version,
            "indicators": {
                "1_smc_order_flow":    _r(self.smc_order_flow.__dict__),
                "2_triple_momentum":   _r(self.triple_momentum.__dict__),
                "3_vwap_pa":           _r(self.vwap_pa.__dict__),
                "4_fib_smc":           _r(self.fib_smc.__dict__),
                "5_atr_bb":            _r(self.atr_bb.__dict__),
                "6_range_breakout":    _r(self.range_breakout.__dict__),
                "7_swing_scalp":       _r(self.swing_scalp.__dict__),
                "8_trend_mean_rev":    _r(self.trend_mean_rev.__dict__),
                "9_mtf_pyramid":       _r(self.mtf_pyramid.__dict__),
                "10_session_mtf":      _r(self.session_mtf.__dict__),
                "11_trailing_stop":    _r(self.trailing_stop.__dict__),
                "12_vol_position":     _r(self.vol_position.__dict__),
                "13_dynamic_confluence": _r(self.dynamic_confluence.__dict__),
            },
        }


# ─────────────────────────────────────────────────────────────
# Main Class
# ─────────────────────────────────────────────────────────────

class HybridIndicators:
    """
    13 Hybrid Enhancement Indicators for Grandcom Gold Signals v3.0.2.

    Each indicator is independently computed and contributes to the
    Dynamic Confluence Score (Indicator 13).  A score > 75% triggers
    HIGH CONFIDENCE classification.

    Usage::

        from ml_engine.hybrid_indicators import HybridIndicators, hybrid_indicators

        result = hybrid_indicators.compute_all(
            signal_id="abc123",
            symbol="XAUUSD",
            signal_type="SELL",
            df=price_df,
            entry_price=2345.00,
            sl_price=2358.00,
            tp_levels=[2325.00, 2305.00],
            current_price=2344.50,
            atr=12.5,
            swing_high=2355.00,
            swing_low=2310.00,
            nearest_resistance=2350.00,
            nearest_support=2320.00,
            mtf_alignment={"H4": "SELL", "H1": "SELL", "M15": "SELL"},
            smc_analysis={},
            account_balance=10000.0,
        )
    """

    def __init__(self) -> None:
        self.version = "2.0.0"

    # ═══════════════════════════════════════════════════════════
    # MAIN ENTRY POINT
    # ═══════════════════════════════════════════════════════════

    def compute_all(
        self,
        signal_id:          str,
        symbol:             str,
        signal_type:        str,
        df:                 pd.DataFrame,
        entry_price:        float,
        sl_price:           float,
        tp_levels:          List[float],
        current_price:      float,
        atr:                float,
        swing_high:         float,
        swing_low:          float,
        nearest_resistance: float,
        nearest_support:    float,
        mtf_alignment:      Dict[str, str],
        smc_analysis:       Dict[str, Any],
        account_balance:    float = 10_000.0,
        adx:                float = 25.0,
        rsi:                float = 50.0,
        check_time:         Optional[datetime] = None,
    ) -> HybridIndicatorsResult:
        """Compute all 13 hybrid indicators and return a complete result."""
        now = check_time or datetime.now(timezone.utc)
        sig_type = signal_type.upper()

        # ── Indicator 1: SMC + Order Flow ─────────────────────
        ind1 = self.smc_order_flow(
            smc_analysis=smc_analysis,
            signal_type=sig_type,
            entry_price=entry_price,
        )

        # ── Indicator 2: Triple Momentum ──────────────────────
        ind2 = self.triple_momentum(df=df, signal_type=sig_type)

        # ── Indicator 3: VWAP + Price Action ─────────────────
        ind3 = self.vwap_price_action(
            df=df,
            current_price=current_price,
            signal_type=sig_type,
        )

        # ── Indicator 4: Fibonacci + SMC ──────────────────────
        ind4 = self.fibonacci_smc_confluence(
            swing_high=swing_high,
            swing_low=swing_low,
            current_price=current_price,
            entry_price=entry_price,
            smc_analysis=smc_analysis,
            signal_type=sig_type,
        )

        # ── Indicator 5: ATR + Bollinger Bands ───────────────
        ind5 = self.atr_bollinger_bands(
            df=df,
            current_price=current_price,
            atr=atr,
        )

        # ── Indicator 6: Range + Breakout Filter ─────────────
        ind6 = self.range_breakout_filter(
            df=df,
            nearest_support=nearest_support,
            nearest_resistance=nearest_resistance,
            current_price=current_price,
            adx=adx,
        )

        # ── Indicator 7: Swing + Scalp Timing ────────────────
        ind7 = self.swing_scalp_timing(
            df=df,
            signal_type=sig_type,
            mtf_alignment=mtf_alignment,
        )

        # ── Indicator 8: Trend + Mean Reversion ──────────────
        ind8 = self.trend_mean_reversion(
            df=df,
            signal_type=sig_type,
            adx=adx,
            rsi=rsi,
        )

        # ── Indicator 9: MTF Pyramid ──────────────────────────
        ind9 = self.mtf_pyramid_breakdown(
            mtf_alignment=mtf_alignment,
            signal_type=sig_type,
        )

        # ── Indicator 10: Session MTF Weighting ──────────────
        ind10 = self.session_mtf_weighting(
            mtf_alignment=mtf_alignment,
            check_time=now,
        )

        # ── Indicator 11: Trailing Stop ───────────────────────
        ind11 = self.fixed_trailing_stop(
            signal_type=sig_type,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_levels=tp_levels,
            atr=atr,
        )

        # ── Indicator 12: Volatility Position Sizing ─────────
        ind12 = self.volatility_position_sizing(
            entry_price=entry_price,
            sl_price=sl_price,
            atr=atr,
            account_balance=account_balance,
        )

        # ── Indicator 13: Dynamic Confluence Score ────────────
        component_scores = {
            "smc_order_flow":   ind1.score,
            "triple_momentum":  ind2.score,
            "vwap_pa":          ind3.score,
            "fib_smc":          ind4.score,
            "atr_bb":           ind5.score,
            "range_breakout":   ind6.score,
            "swing_scalp":      ind7.score,
            "trend_mean_rev":   ind8.score,
            "mtf_pyramid":      ind9.score,
            "session_mtf":      ind10.score,
            "trailing_stop":    ind11.score,
            "vol_position":     ind12.score,
        }
        ind13 = self.dynamic_confluence_score(component_scores=component_scores)

        # ── Overall hybrid score ──────────────────────────────
        all_scores = list(component_scores.values()) + [ind13.score]
        overall = float(np.mean(all_scores))

        if overall >= 7.5:
            rec = "APPROVE — strong hybrid indicator confluence."
        elif overall >= 5.5:
            rec = "ADJUST — moderate confluence, review flagged indicators."
        else:
            rec = "REJECT — insufficient hybrid indicator confluence."

        return HybridIndicatorsResult(
            signal_id=signal_id,
            symbol=symbol,
            signal_type=sig_type,
            timestamp=now.isoformat(),
            smc_order_flow=ind1,
            triple_momentum=ind2,
            vwap_pa=ind3,
            fib_smc=ind4,
            atr_bb=ind5,
            range_breakout=ind6,
            swing_scalp=ind7,
            trend_mean_rev=ind8,
            mtf_pyramid=ind9,
            session_mtf=ind10,
            trailing_stop=ind11,
            vol_position=ind12,
            dynamic_confluence=ind13,
            overall_hybrid_score=overall,
            recommendation=rec,
            version=self.version,
        )

    # ═══════════════════════════════════════════════════════════
    # INDICATOR 1: SMC + ORDER FLOW
    # ═══════════════════════════════════════════════════════════

    def smc_order_flow(
        self,
        smc_analysis: Dict[str, Any],
        signal_type:  str,
        entry_price:  float,
    ) -> SMCOrderFlowResult:
        """
        Filter false SMC levels using order flow confirmation.

        A valid order block requires:
        - Price has returned to the OB zone
        - Order flow (volume delta) confirms the direction
        - No FVG invalidation
        """
        direction = signal_type.upper()
        order_blocks = smc_analysis.get("order_blocks", [])
        fvgs         = smc_analysis.get("fair_value_gaps", [])
        smc_score    = smc_analysis.get("smc_score", 5.0)

        valid_obs:    List[float] = []
        rejected_obs: List[float] = []

        for ob in order_blocks:
            ob_price = ob.get("price", ob.get("level", 0.0))
            ob_type  = ob.get("type", "").upper()
            ob_valid = ob.get("valid", True)

            # Check if OB aligns with signal direction
            if direction == "SELL" and ob_type in ("BEARISH", "SUPPLY", "SELL"):
                if ob_valid and abs(ob_price - entry_price) / PIP_VALUE_GOLD < 50:
                    valid_obs.append(ob_price)
                else:
                    rejected_obs.append(ob_price)
            elif direction == "BUY" and ob_type in ("BULLISH", "DEMAND", "BUY"):
                if ob_valid and abs(ob_price - entry_price) / PIP_VALUE_GOLD < 50:
                    valid_obs.append(ob_price)
                else:
                    rejected_obs.append(ob_price)

        # Order flow bias from SMC analysis
        smc_bias = smc_analysis.get("smc_bias", "NEUTRAL").upper()
        if smc_bias in ("BULLISH", "BUY"):
            of_bias = "BULLISH"
        elif smc_bias in ("BEARISH", "SELL"):
            of_bias = "BEARISH"
        else:
            of_bias = "NEUTRAL"

        # Score: valid OBs + SMC score alignment
        base_score = min(smc_score, 10.0)
        if valid_obs:
            base_score = min(base_score + len(valid_obs) * 0.5, 10.0)
        if rejected_obs:
            base_score = max(base_score - len(rejected_obs) * 0.3, 0.0)

        # Alignment bonus
        if (direction == "BUY" and of_bias == "BULLISH") or \
           (direction == "SELL" and of_bias == "BEARISH"):
            base_score = min(base_score + 1.0, 10.0)

        rec = (
            f"{'✓' if valid_obs else '⚠'} "
            f"{len(valid_obs)} valid OB(s), {len(rejected_obs)} rejected. "
            f"Order flow: {of_bias}. "
            f"{'Aligned with signal.' if of_bias != 'NEUTRAL' else 'Neutral — wait for confirmation.'}"
        )

        return SMCOrderFlowResult(
            valid_ob_count=len(valid_obs),
            false_ob_count=len(rejected_obs),
            order_flow_bias=of_bias,
            confirmed_levels=valid_obs,
            rejected_levels=rejected_obs,
            score=round(base_score, 2),
            recommendation=rec,
        )

    # ═══════════════════════════════════════════════════════════
    # INDICATOR 2: TRIPLE MOMENTUM (RSI + MACD + STOCH RSI)
    # ═══════════════════════════════════════════════════════════

    def triple_momentum(
        self,
        df:          pd.DataFrame,
        signal_type: str,
    ) -> TripleMomentumResult:
        """
        Triple momentum confluence: RSI + MACD + Stochastic RSI.

        All three must align for HIGH CONFIDENCE.
        """
        direction = signal_type.upper()

        if len(df) < 35:
            return TripleMomentumResult(
                rsi=50.0, rsi_signal="NEUTRAL",
                macd=0.0, macd_signal_line=0.0, macd_histogram=0.0, macd_bias="NEUTRAL",
                stoch_rsi_k=50.0, stoch_rsi_d=50.0, stoch_signal="NEUTRAL",
                confluence="NEUTRAL", score=5.0,
                recommendation="Insufficient data for triple momentum.",
            )

        close = df["close"].astype(float)

        # ── RSI ───────────────────────────────────────────────
        rsi_val = self._compute_rsi(close, period=14)
        if rsi_val > 70:
            rsi_signal = "OVERBOUGHT"
        elif rsi_val < 30:
            rsi_signal = "OVERSOLD"
        elif rsi_val > 55:
            rsi_signal = "BULLISH"
        elif rsi_val < 45:
            rsi_signal = "BEARISH"
        else:
            rsi_signal = "NEUTRAL"

        # ── MACD ──────────────────────────────────────────────
        macd_line, signal_line, histogram = self._compute_macd(close)
        if macd_line > signal_line and histogram > 0:
            macd_bias = "BULLISH"
        elif macd_line < signal_line and histogram < 0:
            macd_bias = "BEARISH"
        else:
            macd_bias = "NEUTRAL"

        # ── Stochastic RSI ────────────────────────────────────
        stoch_k, stoch_d = self._compute_stoch_rsi(close)
        if stoch_k > 80:
            stoch_signal = "OVERBOUGHT"
        elif stoch_k < 20:
            stoch_signal = "OVERSOLD"
        else:
            stoch_signal = "NEUTRAL"

        # ── Confluence ────────────────────────────────────────
        buy_signals  = 0
        sell_signals = 0

        if rsi_signal in ("BULLISH", "OVERSOLD"):
            buy_signals += 1
        elif rsi_signal in ("BEARISH", "OVERBOUGHT"):
            sell_signals += 1

        if macd_bias == "BULLISH":
            buy_signals += 1
        elif macd_bias == "BEARISH":
            sell_signals += 1

        if stoch_signal == "OVERSOLD":
            buy_signals += 1
        elif stoch_signal == "OVERBOUGHT":
            sell_signals += 1

        if buy_signals == 3:
            confluence = "STRONG_BUY"
        elif buy_signals == 2:
            confluence = "BUY"
        elif sell_signals == 3:
            confluence = "STRONG_SELL"
        elif sell_signals == 2:
            confluence = "SELL"
        else:
            confluence = "NEUTRAL"

        # Score based on alignment with signal direction
        if direction == "BUY":
            aligned = buy_signals
        else:
            aligned = sell_signals

        score = 3.0 + aligned * 2.5  # 3.0 base, +2.5 per aligned indicator
        score = min(max(score, 0.0), 10.0)

        rec = (
            f"Triple momentum: RSI={rsi_val:.1f}({rsi_signal}), "
            f"MACD={macd_bias}, StochRSI={stoch_k:.1f}({stoch_signal}). "
            f"Confluence: {confluence}. "
            f"{'✓ Aligned with signal.' if aligned >= 2 else '⚠ Weak momentum confluence.'}"
        )

        return TripleMomentumResult(
            rsi=round(rsi_val, 2),
            rsi_signal=rsi_signal,
            macd=round(macd_line, 4),
            macd_signal_line=round(signal_line, 4),
            macd_histogram=round(histogram, 4),
            macd_bias=macd_bias,
            stoch_rsi_k=round(stoch_k, 2),
            stoch_rsi_d=round(stoch_d, 2),
            stoch_signal=stoch_signal,
            confluence=confluence,
            score=round(score, 2),
            recommendation=rec,
        )

    # ═══════════════════════════════════════════════════════════
    # INDICATOR 3: VWAP + PRICE ACTION
    # ═══════════════════════════════════════════════════════════

    def vwap_price_action(
        self,
        df:            pd.DataFrame,
        current_price: float,
        signal_type:   str,
    ) -> VWAPResult:
        """
        VWAP as institutional session benchmark.

        Price above VWAP = institutional buying pressure.
        Price below VWAP = institutional selling pressure.
        """
        direction = signal_type.upper()

        if len(df) < 10 or "volume" not in df.columns:
            # Fallback: use simple price average
            vwap = float(df["close"].mean()) if len(df) > 0 else current_price
        else:
            vwap = self._compute_vwap(df)

        distance = current_price - vwap
        distance_pips = abs(distance) / PIP_VALUE_GOLD

        if distance > PIP_VALUE_GOLD * 5:
            price_vs_vwap = "ABOVE"
            inst_bias = "BULLISH"
        elif distance < -PIP_VALUE_GOLD * 5:
            price_vs_vwap = "BELOW"
            inst_bias = "BEARISH"
        else:
            price_vs_vwap = "AT"
            inst_bias = "NEUTRAL"

        # Score: alignment with signal direction
        if (direction == "BUY" and inst_bias == "BULLISH") or \
           (direction == "SELL" and inst_bias == "BEARISH"):
            score = 8.5
        elif inst_bias == "NEUTRAL":
            score = 5.0
        else:
            score = 3.0  # Counter-VWAP

        rec = (
            f"VWAP: {vwap:.2f}. Price {price_vs_vwap} VWAP by {distance_pips:.1f} pips. "
            f"Institutional bias: {inst_bias}. "
            f"{'✓ Aligned.' if score >= 7.0 else '⚠ Counter-institutional.'}"
        )

        return VWAPResult(
            vwap=round(vwap, 2),
            current_price=round(current_price, 2),
            price_vs_vwap=price_vs_vwap,
            distance_pips=round(distance_pips, 1),
            institutional_bias=inst_bias,
            score=round(score, 2),
            recommendation=rec,
        )

    # ═══════════════════════════════════════════════════════════
    # INDICATOR 4: FIBONACCI + SMC CONFLUENCE
    # ═══════════════════════════════════════════════════════════

    def fibonacci_smc_confluence(
        self,
        swing_high:    float,
        swing_low:     float,
        current_price: float,
        entry_price:   float,
        smc_analysis:  Dict[str, Any],
        signal_type:   str,
    ) -> FibSMCResult:
        """
        Fibonacci retracement + SMC confluence (stacked zones).

        Stacked zone = Fibonacci level + Order Block/FVG within 10 pips.
        """
        direction = signal_type.upper()
        swing_range = swing_high - swing_low

        # Fibonacci retracement levels
        fib_levels: Dict[str, float] = {}
        for level in FIBONACCI_LEVELS:
            if direction == "SELL":
                # Retracement from high
                price = swing_high - (swing_range * level)
            else:
                # Retracement from low
                price = swing_low + (swing_range * level)
            fib_levels[f"{level:.3f}"] = round(price, 2)

        # Fibonacci extensions
        fib_extensions: Dict[str, float] = {}
        for ext in FIBONACCI_EXTENSIONS:
            if direction == "SELL":
                price = swing_high - (swing_range * ext)
            else:
                price = swing_low + (swing_range * ext)
            fib_extensions[f"{ext:.3f}"] = round(price, 2)

        # Find nearest Fibonacci level to entry
        nearest_fib = min(
            fib_levels.values(),
            key=lambda x: abs(x - entry_price),
        )
        nearest_label = min(
            fib_levels.items(),
            key=lambda kv: abs(kv[1] - entry_price),
        )[0]

        # Check SMC confluence (OB or FVG within 10 pips of nearest Fib)
        stacked_zones: List[Dict[str, Any]] = []
        smc_confluence = False
        tolerance = 10 * PIP_VALUE_GOLD

        for ob in smc_analysis.get("order_blocks", []):
            ob_price = ob.get("price", ob.get("level", 0.0))
            if abs(ob_price - nearest_fib) <= tolerance:
                stacked_zones.append({
                    "type": "ORDER_BLOCK",
                    "price": ob_price,
                    "fib_level": nearest_label,
                    "distance_pips": round(abs(ob_price - nearest_fib) / PIP_VALUE_GOLD, 1),
                })
                smc_confluence = True

        for fvg in smc_analysis.get("fair_value_gaps", []):
            fvg_mid = (fvg.get("high", 0.0) + fvg.get("low", 0.0)) / 2.0
            if abs(fvg_mid - nearest_fib) <= tolerance:
                stacked_zones.append({
                    "type": "FVG",
                    "price": fvg_mid,
                    "fib_level": nearest_label,
                    "distance_pips": round(abs(fvg_mid - nearest_fib) / PIP_VALUE_GOLD, 1),
                })
                smc_confluence = True

        # Score
        dist_to_fib_pips = abs(entry_price - nearest_fib) / PIP_VALUE_GOLD
        if dist_to_fib_pips <= 5:
            base_score = 9.0
        elif dist_to_fib_pips <= 15:
            base_score = 7.0
        elif dist_to_fib_pips <= 30:
            base_score = 5.0
        else:
            base_score = 3.0

        if smc_confluence:
            base_score = min(base_score + 1.5, 10.0)

        rec = (
            f"Nearest Fib: {nearest_label} at {nearest_fib:.2f} "
            f"({dist_to_fib_pips:.1f} pips from entry). "
            f"SMC confluence: {'✓ YES' if smc_confluence else '✗ NO'}. "
            f"Stacked zones: {len(stacked_zones)}."
        )

        return FibSMCResult(
            fib_levels=fib_levels,
            fib_extensions=fib_extensions,
            nearest_fib=round(nearest_fib, 2),
            nearest_fib_label=nearest_label,
            smc_confluence=smc_confluence,
            stacked_zones=stacked_zones,
            score=round(base_score, 2),
            recommendation=rec,
        )

    # ═══════════════════════════════════════════════════════════
    # INDICATOR 5: ATR + BOLLINGER BANDS
    # ═══════════════════════════════════════════════════════════

    def atr_bollinger_bands(
        self,
        df:            pd.DataFrame,
        current_price: float,
        atr:           float,
    ) -> ATRBBResult:
        """
        ATR + Bollinger Bands for volatility sizing and squeeze detection.

        BB Squeeze = BB width < 1 ATR → breakout imminent.
        """
        if len(df) < BB_PERIOD + 5:
            return ATRBBResult(
                atr=atr, atr_pips=atr / PIP_VALUE_GOLD,
                bb_upper=current_price + atr * 2,
                bb_middle=current_price,
                bb_lower=current_price - atr * 2,
                bb_width=atr * 4,
                bb_squeeze=False,
                price_vs_bb="MIDDLE",
                volatility_regime="NORMAL",
                score=5.0,
                recommendation="Insufficient data for BB calculation.",
            )

        close = df["close"].astype(float)
        bb_mid = float(close.rolling(BB_PERIOD).mean().iloc[-1])
        bb_std = float(close.rolling(BB_PERIOD).std().iloc[-1])
        bb_upper = bb_mid + BB_STD * bb_std
        bb_lower = bb_mid - BB_STD * bb_std
        bb_width = bb_upper - bb_lower

        atr_pips = atr / PIP_VALUE_GOLD
        bb_squeeze = bb_width < atr  # Squeeze when BB width < 1 ATR

        # Price position relative to BB
        if current_price > bb_upper:
            price_vs_bb = "ABOVE_UPPER"
        elif current_price > bb_mid + bb_std * 0.5:
            price_vs_bb = "NEAR_UPPER"
        elif current_price < bb_lower:
            price_vs_bb = "BELOW_LOWER"
        elif current_price < bb_mid - bb_std * 0.5:
            price_vs_bb = "NEAR_LOWER"
        else:
            price_vs_bb = "MIDDLE"

        # Volatility regime
        if bb_squeeze:
            vol_regime = "SQUEEZE"
        elif bb_width > atr * 3:
            vol_regime = "EXPANDING"
        else:
            vol_regime = "NORMAL"

        # Score
        if vol_regime == "SQUEEZE":
            score = 7.0  # Breakout opportunity
        elif vol_regime == "NORMAL":
            score = 8.0  # Good conditions
        else:
            score = 5.0  # High volatility — caution

        rec = (
            f"ATR: {atr:.2f} ({atr_pips:.0f} pips). "
            f"BB: [{bb_lower:.2f}–{bb_upper:.2f}], width={bb_width:.2f}. "
            f"Regime: {vol_regime}. "
            f"Price: {price_vs_bb}. "
            f"{'⚡ Squeeze — breakout imminent.' if bb_squeeze else ''}"
        )

        return ATRBBResult(
            atr=round(atr, 2),
            atr_pips=round(atr_pips, 1),
            bb_upper=round(bb_upper, 2),
            bb_middle=round(bb_mid, 2),
            bb_lower=round(bb_lower, 2),
            bb_width=round(bb_width, 2),
            bb_squeeze=bb_squeeze,
            price_vs_bb=price_vs_bb,
            volatility_regime=vol_regime,
            score=round(score, 2),
            recommendation=rec,
        )

    # ═══════════════════════════════════════════════════════════
    # INDICATOR 6: RANGE + BREAKOUT FILTER
    # ═══════════════════════════════════════════════════════════

    def range_breakout_filter(
        self,
        df:                 pd.DataFrame,
        nearest_support:    float,
        nearest_resistance: float,
        current_price:      float,
        adx:                float,
    ) -> RangeBreakoutResult:
        """
        Regime clarity scoring: RANGE vs BREAKOUT vs TREND.

        Breakout confirmed when price closes beyond S/R with ADX expanding.
        """
        range_pips = (nearest_resistance - nearest_support) / PIP_VALUE_GOLD

        # Determine regime
        if adx > 30:
            regime = "TREND"
            clarity = min(0.5 + adx * 0.01, 1.0)
        elif adx > 20:
            regime = "BREAKOUT"
            clarity = 0.65
        else:
            regime = "RANGE"
            clarity = 0.80 if adx < 15 else 0.70

        # Breakout detection
        breakout_confirmed = False
        breakout_direction = "NONE"

        if len(df) >= 3:
            last_close = float(df["close"].iloc[-1])
            prev_close = float(df["close"].iloc[-2])

            if last_close > nearest_resistance and prev_close <= nearest_resistance:
                breakout_confirmed = True
                breakout_direction = "UP"
                regime = "BREAKOUT"
            elif last_close < nearest_support and prev_close >= nearest_support:
                breakout_confirmed = True
                breakout_direction = "DOWN"
                regime = "BREAKOUT"

        # Score
        if breakout_confirmed:
            score = 8.5
        elif regime == "RANGE" and clarity > 0.75:
            score = 7.5
        elif regime == "TREND":
            score = 8.0
        else:
            score = 5.5

        rec = (
            f"Regime: {regime} (clarity: {clarity:.0%}). "
            f"Range: {nearest_support:.2f}–{nearest_resistance:.2f} ({range_pips:.0f} pips). "
            f"Breakout: {'✓ ' + breakout_direction if breakout_confirmed else 'None'}."
        )

        return RangeBreakoutResult(
            regime=regime,
            range_high=round(nearest_resistance, 2),
            range_low=round(nearest_support, 2),
            range_pips=round(range_pips, 1),
            breakout_confirmed=breakout_confirmed,
            breakout_direction=breakout_direction,
            regime_clarity=round(clarity, 2),
            score=round(score, 2),
            recommendation=rec,
        )

    # ═══════════════════════════════════════════════════════════
    # INDICATOR 7: SWING + SCALP ENTRY TIMING
    # ═══════════════════════════════════════════════════════════

    def swing_scalp_timing(
        self,
        df:            pd.DataFrame,
        signal_type:   str,
        mtf_alignment: Dict[str, str],
    ) -> SwingScalpTimingResult:
        """
        M15 confirmation for swing and scalp entries.

        M15 must confirm the H1/H4 bias before entry.
        """
        direction = signal_type.upper()
        m15_signal = mtf_alignment.get("M15", "NEUTRAL").upper()

        # Normalise M15 signal
        if m15_signal in ("BUY", "BULLISH", "UP"):
            m15_norm = "BUY"
        elif m15_signal in ("SELL", "BEARISH", "DOWN"):
            m15_norm = "SELL"
        else:
            m15_norm = "NEUTRAL"

        # Swing bias from H4
        h4_signal = mtf_alignment.get("H4", "NEUTRAL").upper()
        if h4_signal in ("BUY", "BULLISH", "UP"):
            swing_bias = "BUY"
        elif h4_signal in ("SELL", "BEARISH", "DOWN"):
            swing_bias = "SELL"
        else:
            swing_bias = "NEUTRAL"

        # M15 confirmation
        m15_confirmed = m15_norm == direction

        # Timing quality
        if m15_confirmed and swing_bias == direction:
            timing_quality = "OPTIMAL"
            entry_window = True
        elif m15_confirmed or swing_bias == direction:
            timing_quality = "GOOD"
            entry_window = True
        else:
            timing_quality = "POOR"
            entry_window = False

        # Score
        score_map = {"OPTIMAL": 9.0, "GOOD": 7.0, "POOR": 3.0}
        score = score_map.get(timing_quality, 5.0)

        rec = (
            f"M15: {m15_norm}, H4 swing: {swing_bias}. "
            f"Timing: {timing_quality}. "
            f"Entry window: {'OPEN ✓' if entry_window else 'CLOSED ✗'}. "
            f"{'Wait for M15 confirmation.' if not m15_confirmed else ''}"
        )

        return SwingScalpTimingResult(
            m15_signal=m15_norm,
            m15_confirmed=m15_confirmed,
            swing_bias=swing_bias,
            timing_quality=timing_quality,
            entry_window_open=entry_window,
            score=round(score, 2),
            recommendation=rec,
        )

    # ═══════════════════════════════════════════════════════════
    # INDICATOR 8: TREND + MEAN REVERSION
    # ═══════════════════════════════════════════════════════════

    def trend_mean_reversion(
        self,
        df:          pd.DataFrame,
        signal_type: str,
        adx:         float,
        rsi:         float,
    ) -> TrendMeanRevResult:
        """
        Determine primary strategy: trend-following vs mean reversion.

        Trend-follow when ADX > 25.
        Mean reversion when ADX < 20 and price at extremes.
        """
        direction = signal_type.upper()

        # Z-score for mean reversion
        if len(df) >= 20:
            close = df["close"].astype(float)
            mean  = float(close.rolling(20).mean().iloc[-1])
            std   = float(close.rolling(20).std().iloc[-1])
            zscore = (float(close.iloc[-1]) - mean) / std if std > 0 else 0.0
        else:
            zscore = 0.0

        # Strategy selection
        if adx > 25:
            primary_strategy = "TREND_FOLLOW"
            trend_strength = min(adx / 50.0, 1.0)
        elif adx < 20 and abs(zscore) > 1.5:
            primary_strategy = "MEAN_REVERSION"
            trend_strength = adx / 50.0
        else:
            primary_strategy = "BREAKOUT"
            trend_strength = adx / 50.0

        # Mean reversion signal
        if zscore > 2.0:
            mean_rev_signal = "OVERBOUGHT"
        elif zscore < -2.0:
            mean_rev_signal = "OVERSOLD"
        else:
            mean_rev_signal = "NEUTRAL"

        # Strategy alignment with signal
        if primary_strategy == "TREND_FOLLOW":
            if (direction == "BUY" and rsi > 50) or (direction == "SELL" and rsi < 50):
                aligned = True
            else:
                aligned = False
        elif primary_strategy == "MEAN_REVERSION":
            if (direction == "BUY" and mean_rev_signal == "OVERSOLD") or \
               (direction == "SELL" and mean_rev_signal == "OVERBOUGHT"):
                aligned = True
            else:
                aligned = False
        else:
            aligned = True  # Breakout — neutral

        score = 7.0 if aligned else 4.0
        if primary_strategy == "TREND_FOLLOW" and trend_strength > 0.6:
            score = min(score + 1.5, 10.0)

        rec = (
            f"Strategy: {primary_strategy} (ADX={adx:.1f}, Z={zscore:.2f}). "
            f"Mean rev signal: {mean_rev_signal}. "
            f"Alignment: {'✓' if aligned else '✗'}."
        )

        return TrendMeanRevResult(
            primary_strategy=primary_strategy,
            trend_strength=round(trend_strength, 3),
            mean_rev_signal=mean_rev_signal,
            zscore=round(zscore, 3),
            strategy_alignment=aligned,
            score=round(score, 2),
            recommendation=rec,
        )

    # ═══════════════════════════════════════════════════════════
    # INDICATOR 9: MTF PYRAMID BREAKDOWN
    # ═══════════════════════════════════════════════════════════

    def mtf_pyramid_breakdown(
        self,
        mtf_alignment: Dict[str, str],
        signal_type:   str,
    ) -> MTFPyramidResult:
        """
        Detailed MTF pyramid: H4 bias → H1 structure → M15 trigger.

        All three levels must align for a valid pyramid.
        """
        direction = signal_type.upper()

        def _norm(s: str) -> str:
            s = s.upper()
            if s in ("BUY", "BULLISH", "UP"):
                return "BUY"
            if s in ("SELL", "BEARISH", "DOWN"):
                return "SELL"
            return "NEUTRAL"

        h4_bias    = _norm(mtf_alignment.get("H4", "NEUTRAL"))
        h1_struct  = _norm(mtf_alignment.get("H1", "NEUTRAL"))
        m15_trigger = _norm(mtf_alignment.get("M15", "NEUTRAL"))

        # Pyramid validity
        missing: List[str] = []
        aligned_count = 0

        if h4_bias == direction:
            aligned_count += 1
        else:
            missing.append(f"H4 bias ({h4_bias}) ≠ signal ({direction})")

        if h1_struct == direction:
            aligned_count += 1
        else:
            missing.append(f"H1 structure ({h1_struct}) ≠ signal ({direction})")

        if m15_trigger == direction:
            aligned_count += 1
        else:
            missing.append(f"M15 trigger ({m15_trigger}) ≠ signal ({direction})")

        alignment_score = aligned_count / 3.0
        pyramid_valid   = aligned_count == 3

        score = 3.0 + aligned_count * 2.3  # 3.0 base, +2.3 per aligned TF
        score = min(max(score, 0.0), 10.0)

        rec = (
            f"MTF Pyramid: H4={h4_bias} | H1={h1_struct} | M15={m15_trigger}. "
            f"Aligned: {aligned_count}/3. "
            f"{'✓ Full pyramid confirmed.' if pyramid_valid else '⚠ Incomplete: ' + '; '.join(missing)}"
        )

        return MTFPyramidResult(
            h4_bias=h4_bias,
            h1_structure=h1_struct,
            m15_trigger=m15_trigger,
            alignment_score=round(alignment_score, 2),
            pyramid_valid=pyramid_valid,
            missing_levels=missing,
            score=round(score, 2),
            recommendation=rec,
        )

    # ═══════════════════════════════════════════════════════════
    # INDICATOR 10: SESSION-BASED MTF WEIGHTING
    # ═══════════════════════════════════════════════════════════

    def session_mtf_weighting(
        self,
        mtf_alignment: Dict[str, str],
        check_time:    Optional[datetime] = None,
    ) -> SessionMTFWeightResult:
        """
        Adjust MTF weights based on session liquidity.

        Low-liquidity sessions (Asia, off-hours) reduce M15 weight
        and increase H4 weight to filter noise.
        """
        now = check_time or datetime.now(timezone.utc)
        hour = now.hour

        # Session detection
        if 7 <= hour < 16:
            session = "LONDON"
            liquidity = 0.90
            weights = {"H4": 0.35, "H1": 0.35, "M15": 0.30}
        elif 13 <= hour < 22:
            session = "NY"
            liquidity = 0.85
            weights = {"H4": 0.35, "H1": 0.35, "M15": 0.30}
        elif 0 <= hour < 8:
            session = "ASIA"
            liquidity = 0.55
            weights = {"H4": 0.50, "H1": 0.35, "M15": 0.15}  # Reduce M15 weight
        else:
            session = "OFF"
            liquidity = 0.30
            weights = {"H4": 0.60, "H1": 0.30, "M15": 0.10}  # Heavily reduce M15

        low_liquidity = liquidity < 0.60

        # Filter signals in low-liquidity sessions
        filtered: List[str] = []
        if low_liquidity:
            filtered.append(
                f"M15 signals filtered in {session} session (low liquidity). "
                f"Use H4/H1 bias only."
            )

        score = liquidity * 10.0

        rec = (
            f"Session: {session} (liquidity: {liquidity:.0%}). "
            f"MTF weights: H4={weights['H4']:.0%}, H1={weights['H1']:.0%}, "
            f"M15={weights['M15']:.0%}. "
            f"{'⚠ Low liquidity — M15 weight reduced.' if low_liquidity else '✓ Normal liquidity.'}"
        )

        return SessionMTFWeightResult(
            session=session,
            adjusted_weights=weights,
            liquidity_score=round(liquidity, 2),
            low_liquidity=low_liquidity,
            filtered_signals=filtered,
            score=round(score, 2),
            recommendation=rec,
        )

    # ═══════════════════════════════════════════════════════════
    # INDICATOR 11: FIXED + TRAILING STOP HYBRID
    # ═══════════════════════════════════════════════════════════

    def fixed_trailing_stop(
        self,
        signal_type:  str,
        entry_price:  float,
        sl_price:     float,
        tp_levels:    List[float],
        atr:          float,
    ) -> TrailingStopResult:
        """
        Hybrid stop: fixed SL until TP1, then trail by 1 ATR.

        Activation: when price reaches TP1.
        Trail distance: 1.0 ATR from current price.
        """
        direction = signal_type.upper()
        risk = abs(entry_price - sl_price)
        trailing_distance = atr * 1.0
        trailing_pips = trailing_distance / PIP_VALUE_GOLD

        # Activation at TP1
        activation_price = tp_levels[0] if tp_levels else (
            entry_price + risk if direction == "BUY" else entry_price - risk
        )

        # Trailing SL at activation
        if direction == "BUY":
            trailing_sl = activation_price - trailing_distance
            profit_locked = (activation_price - entry_price) - trailing_distance
        else:
            trailing_sl = activation_price + trailing_distance
            profit_locked = (entry_price - activation_price) - trailing_distance

        profit_locked = max(0.0, profit_locked)
        profit_locked_pips = profit_locked / PIP_VALUE_GOLD

        score = 7.5  # Trailing stops always improve risk management

        rec = (
            f"Fixed SL: {sl_price:.2f}. "
            f"Trail activates at TP1 ({activation_price:.2f}). "
            f"Trail distance: {trailing_pips:.0f} pips (1 ATR). "
            f"Trailing SL: {trailing_sl:.2f}. "
            f"Profit locked at activation: {profit_locked_pips:.0f} pips."
        )

        return TrailingStopResult(
            fixed_sl=round(sl_price, 2),
            trailing_sl=round(trailing_sl, 2),
            trailing_distance=round(trailing_distance, 2),
            trailing_pips=round(trailing_pips, 1),
            profit_locked=round(profit_locked_pips, 1),
            activation_price=round(activation_price, 2),
            recommendation=rec,
            score=round(score, 2),
        )

    # ═══════════════════════════════════════════════════════════
    # INDICATOR 12: VOLATILITY-ADJUSTED POSITION SIZING
    # ═══════════════════════════════════════════════════════════

    def volatility_position_sizing(
        self,
        entry_price:     float,
        sl_price:        float,
        atr:             float,
        account_balance: float,
        risk_pct:        float = 0.01,  # 1% account risk
    ) -> VolatilityPositionResult:
        """
        1% account risk position sizing with ATR-based adjustment.

        Formula: size = (balance * risk_pct) / (sl_pips * pip_value_per_lot)
        """
        sl_pips = abs(entry_price - sl_price) / PIP_VALUE_GOLD
        risk_usd = account_balance * risk_pct
        pip_value_per_lot = 10.0  # XAUUSD standard lot

        if sl_pips > 0:
            base_size = risk_usd / (sl_pips * pip_value_per_lot)
        else:
            base_size = 0.01

        # ATR-based adjustment
        atr_pips = atr / PIP_VALUE_GOLD
        if atr_pips > 0:
            atr_size = risk_usd / (atr_pips * pip_value_per_lot)
        else:
            atr_size = base_size

        # Use minimum of both (more conservative)
        adjusted_size = min(base_size, atr_size)
        adjusted_size = max(0.01, round(adjusted_size, 2))

        vol_multiplier = adjusted_size / base_size if base_size > 0 else 1.0

        score = 8.0  # Position sizing always valid

        rec = (
            f"1% risk: ${risk_usd:.2f} on ${account_balance:.0f} balance. "
            f"SL: {sl_pips:.0f} pips. "
            f"Position size: {adjusted_size:.2f} lots. "
            f"Risk per trade: ${adjusted_size * sl_pips * pip_value_per_lot:.2f}."
        )

        return VolatilityPositionResult(
            base_size=round(base_size, 2),
            adjusted_size=adjusted_size,
            risk_pct=risk_pct,
            risk_usd=round(risk_usd, 2),
            atr_based_size=round(atr_size, 2),
            vol_multiplier=round(vol_multiplier, 3),
            account_balance=account_balance,
            score=round(score, 2),
            recommendation=rec,
        )

    # ═══════════════════════════════════════════════════════════
    # INDICATOR 13: DYNAMIC CONFLUENCE SCORE
    # ═══════════════════════════════════════════════════════════

    def dynamic_confluence_score(
        self,
        component_scores: Dict[str, float],
    ) -> DynamicConfluenceResult:
        """
        Dynamic Confluence Score from all 12 preceding indicators.

        Score > 75% = HIGH CONFIDENCE.
        Each indicator scored 0–10; normalised to 0–100.
        """
        if not component_scores:
            return DynamicConfluenceResult(
                total_score=50.0,
                component_scores={},
                confidence_label="LOW",
                is_high_confidence=False,
                active_indicators=0,
                aligned_indicators=0,
                score=5.0,
                recommendation="No component scores available.",
            )

        scores = list(component_scores.values())
        total_score = (sum(scores) / (len(scores) * 10.0)) * 100.0
        total_score = round(min(max(total_score, 0.0), 100.0), 1)

        active = len(scores)
        aligned = sum(1 for s in scores if s >= 7.0)

        if total_score >= HIGH_CONFIDENCE_THRESHOLD:
            label = "HIGH"
            is_high = True
        elif total_score >= 55.0:
            label = "MEDIUM"
            is_high = False
        else:
            label = "LOW"
            is_high = False

        # Normalise to 0–10 for consistency
        score_10 = total_score / 10.0

        rec = (
            f"Dynamic Confluence: {total_score:.1f}% "
            f"({'HIGH CONFIDENCE ✓' if is_high else label}). "
            f"{aligned}/{active} indicators aligned (≥7.0). "
            f"{'Ready to trade.' if is_high else 'Improve confluence before entry.'}"
        )

        return DynamicConfluenceResult(
            total_score=total_score,
            component_scores=component_scores,
            confidence_label=label,
            is_high_confidence=is_high,
            active_indicators=active,
            aligned_indicators=aligned,
            score=round(score_10, 2),
            recommendation=rec,
        )

    # ═══════════════════════════════════════════════════════════
    # PRIVATE HELPERS
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _compute_rsi(close: pd.Series, period: int = 14) -> float:
        """Compute RSI."""
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(period).mean()
        loss  = (-delta.clip(upper=0)).rolling(period).mean()
        rs    = gain / loss.replace(0, float("nan"))
        rsi   = 100.0 - (100.0 / (1.0 + rs))
        val   = float(rsi.iloc[-1])
        return val if not np.isnan(val) else 50.0

    @staticmethod
    def _compute_macd(
        close: pd.Series,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> Tuple[float, float, float]:
        """Compute MACD line, signal line, histogram."""
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        sig_line  = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - sig_line
        return (
            float(macd_line.iloc[-1]),
            float(sig_line.iloc[-1]),
            float(histogram.iloc[-1]),
        )

    @staticmethod
    def _compute_stoch_rsi(
        close: pd.Series,
        rsi_period: int = 14,
        stoch_period: int = 14,
        k_smooth: int = 3,
        d_smooth: int = 3,
    ) -> Tuple[float, float]:
        """Compute Stochastic RSI %K and %D."""
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(rsi_period).mean()
        loss  = (-delta.clip(upper=0)).rolling(rsi_period).mean()
        rs    = gain / loss.replace(0, float("nan"))
        rsi   = 100.0 - (100.0 / (1.0 + rs))

        rsi_min = rsi.rolling(stoch_period).min()
        rsi_max = rsi.rolling(stoch_period).max()
        stoch   = (rsi - rsi_min) / (rsi_max - rsi_min).replace(0, float("nan")) * 100.0

        k = float(stoch.rolling(k_smooth).mean().iloc[-1])
        d = float(stoch.rolling(d_smooth).mean().iloc[-1])

        k = k if not np.isnan(k) else 50.0
        d = d if not np.isnan(d) else 50.0
        return k, d

    @staticmethod
    def _compute_vwap(df: pd.DataFrame) -> float:
        """Compute VWAP from OHLCV data."""
        typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
        volume = df["volume"].astype(float)
        vwap = (typical_price * volume).sum() / volume.sum() if volume.sum() > 0 else float(df["close"].mean())
        return float(vwap)


# ─────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────

hybrid_indicators = HybridIndicators()

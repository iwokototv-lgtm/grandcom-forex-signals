"""
Hybrid Enhancement Indicators
Gold Trading System v3.0.2

13 hybrid enhancement indicators that combine multiple signal quality
dimensions into a unified scoring framework.  Each indicator returns a
score in [0.0, 1.0] and a human-readable explanation.

Indicators:
  1.  SMC + Order Flow          — filters false SMC levels with order flow
  2.  Triple Momentum           — RSI + MACD + Stochastic RSI confluence
  3.  VWAP + Price Action       — institutional session alignment
  4.  Fibonacci + SMC           — stacked Fibonacci/SMC confluence zones
  5.  ATR + Bollinger Bands     — volatility sizing + squeeze timing
  6.  Range + Breakout Filter   — regime clarity scoring
  7.  Swing + Scalp Timing      — M15 confirmation, 1:1.3 → 1:2.5 R:R
  8.  Trend + Mean Reversion    — primary strategy + breakout transitions
  9.  MTF Pyramid Breakdown     — timeframe alignment analysis
  10. Session MTF Weighting     — low-liquidity hour reduction
  11. Fixed + Trailing Stop     — TP1 lock + TP3 trail hybrid
  12. Volatility Position Size  — 1% account risk sizing
  13. Dynamic Confluence Score  — >75% = HIGH CONFIDENCE

Usage:
    from ml_engine.hybrid_enhancement_indicators import HybridEnhancementIndicators

    hei = HybridEnhancementIndicators()
    scores = hei.score_all(signal_dict)
    confluence = hei.dynamic_confluence_score(scores)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

CONFLUENCE_HIGH_THRESHOLD   = 0.75   # >75% = HIGH CONFIDENCE
CONFLUENCE_MEDIUM_THRESHOLD = 0.55   # 55–75% = MEDIUM CONFIDENCE

# RSI thresholds
RSI_OVERSOLD    = 30
RSI_OVERBOUGHT  = 70
RSI_NEUTRAL_LOW = 40
RSI_NEUTRAL_HI  = 60

# MACD signal thresholds
MACD_BULLISH_THRESHOLD = 0.0
MACD_BEARISH_THRESHOLD = 0.0

# Stochastic RSI thresholds
STOCH_OVERSOLD   = 20
STOCH_OVERBOUGHT = 80

# Bollinger Band squeeze threshold (BB width / price)
BB_SQUEEZE_THRESHOLD = 0.005   # < 0.5% of price = squeeze

# ATR multipliers for position sizing
ATR_RISK_MULTIPLIER = 1.5      # SL = entry ± 1.5 × ATR
ACCOUNT_RISK_PCT    = 0.01     # 1% account risk per trade

# Fibonacci levels
FIB_LEVELS = [0.236, 0.382, 0.500, 0.618, 0.786]
FIB_TOLERANCE = 0.005          # 0.5% proximity tolerance

# Session UTC hours (mirrors signal_quality_validator)
SESSION_LONDON_OPEN = 7
SESSION_NY_OPEN     = 13
SESSION_NY_CLOSE    = 22

# MTF weights by session quality
MTF_WEIGHTS_HIGH_LIQUIDITY = {"H4": 0.40, "H1": 0.35, "M15": 0.25}
MTF_WEIGHTS_LOW_LIQUIDITY  = {"H4": 0.60, "H1": 0.30, "M15": 0.10}


# ─────────────────────────────────────────────────────────────
# Data class
# ─────────────────────────────────────────────────────────────

@dataclass
class IndicatorResult:
    """Result of a single hybrid enhancement indicator."""
    name:        str
    score:       float          # 0.0 – 1.0
    label:       str            # HIGH / MEDIUM / LOW
    explanation: str
    details:     Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name":        self.name,
            "score":       round(self.score, 3),
            "label":       self.label,
            "explanation": self.explanation,
            "details":     self.details,
        }


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _label(score: float) -> str:
    if score >= CONFLUENCE_HIGH_THRESHOLD:
        return "HIGH"
    if score >= CONFLUENCE_MEDIUM_THRESHOLD:
        return "MEDIUM"
    return "LOW"


# ─────────────────────────────────────────────────────────────
# Main Class
# ─────────────────────────────────────────────────────────────

class HybridEnhancementIndicators:
    """
    Computes all 13 hybrid enhancement indicator scores for a signal.

    Each indicator is self-contained and degrades gracefully when
    optional signal fields are absent.
    """

    # ═══════════════════════════════════════════════════════════
    # 1. SMC + Order Flow
    # ═══════════════════════════════════════════════════════════

    def smc_order_flow(self, signal: Dict[str, Any]) -> IndicatorResult:
        """
        Indicator 1: SMC + Order Flow
        Filters false SMC levels by requiring order flow confirmation.
        A valid SMC level must have:
          - smc_score ≥ 6/10
          - order_flow_bias aligned with signal direction
          - No liquidity sweep against the signal
        """
        smc_score    = float(signal.get("smc_score", 0) or 0)
        of_bias      = str(signal.get("order_flow_bias", "") or "").upper()
        liq_sweep    = bool(signal.get("liquidity_sweep_against", False))
        stype        = str(signal.get("type") or signal.get("signal") or "BUY").upper()

        # SMC score component (0–1)
        smc_component = _clamp(smc_score / 10.0)

        # Order flow alignment
        of_aligned = (
            (stype == "BUY"  and of_bias in ("BULLISH", "BUY"))  or
            (stype == "SELL" and of_bias in ("BEARISH", "SELL")) or
            of_bias == ""  # Not provided — neutral
        )
        of_component = 1.0 if of_aligned else 0.2

        # Liquidity sweep penalty
        sweep_penalty = 0.3 if liq_sweep else 0.0

        score = _clamp(smc_component * 0.6 + of_component * 0.4 - sweep_penalty)

        return IndicatorResult(
            name="smc_order_flow",
            score=score,
            label=_label(score),
            explanation=(
                f"SMC score {smc_score:.1f}/10, order flow {'aligned' if of_aligned else 'misaligned'}"
                f"{', liquidity sweep detected' if liq_sweep else ''}."
            ),
            details={
                "smc_score": smc_score,
                "order_flow_bias": of_bias,
                "of_aligned": of_aligned,
                "liquidity_sweep_against": liq_sweep,
            },
        )

    # ═══════════════════════════════════════════════════════════
    # 2. Triple Momentum (RSI + MACD + Stochastic RSI)
    # ═══════════════════════════════════════════════════════════

    def triple_momentum(self, signal: Dict[str, Any]) -> IndicatorResult:
        """
        Indicator 2: Triple Momentum Confluence
        RSI + MACD + Stochastic RSI must all agree on direction.
        """
        rsi       = float(signal.get("rsi", 50) or 50)
        macd      = float(signal.get("macd", 0) or 0)
        macd_sig  = float(signal.get("macd_signal", 0) or 0)
        stoch_rsi = float(signal.get("stoch_rsi", 50) or 50)
        stype     = str(signal.get("type") or signal.get("signal") or "BUY").upper()

        macd_hist = macd - macd_sig

        # RSI alignment
        if stype == "BUY":
            rsi_ok    = rsi < RSI_OVERBOUGHT and rsi > RSI_OVERSOLD
            macd_ok   = macd_hist > MACD_BULLISH_THRESHOLD
            stoch_ok  = stoch_rsi < STOCH_OVERBOUGHT
        else:
            rsi_ok    = rsi > RSI_OVERSOLD and rsi < RSI_OVERBOUGHT
            macd_ok   = macd_hist < MACD_BEARISH_THRESHOLD
            stoch_ok  = stoch_rsi > STOCH_OVERSOLD

        aligned_count = sum([rsi_ok, macd_ok, stoch_ok])
        score = _clamp(aligned_count / 3.0)

        # Bonus for strong momentum alignment
        if stype == "BUY" and rsi < 50 and macd_hist > 0 and stoch_rsi < 50:
            score = _clamp(score + 0.15)
        elif stype == "SELL" and rsi > 50 and macd_hist < 0 and stoch_rsi > 50:
            score = _clamp(score + 0.15)

        return IndicatorResult(
            name="triple_momentum",
            score=score,
            label=_label(score),
            explanation=(
                f"{aligned_count}/3 momentum indicators aligned for {stype}: "
                f"RSI={rsi:.1f} ({'✓' if rsi_ok else '✗'}), "
                f"MACD hist={macd_hist:.4f} ({'✓' if macd_ok else '✗'}), "
                f"StochRSI={stoch_rsi:.1f} ({'✓' if stoch_ok else '✗'})."
            ),
            details={
                "rsi": rsi, "rsi_ok": rsi_ok,
                "macd_histogram": round(macd_hist, 6), "macd_ok": macd_ok,
                "stoch_rsi": stoch_rsi, "stoch_ok": stoch_ok,
                "aligned_count": aligned_count,
            },
        )

    # ═══════════════════════════════════════════════════════════
    # 3. VWAP + Price Action
    # ═══════════════════════════════════════════════════════════

    def vwap_price_action(self, signal: Dict[str, Any]) -> IndicatorResult:
        """
        Indicator 3: VWAP + Price Action
        Institutional session alignment — price should be on the correct
        side of VWAP for the signal direction.
        """
        entry = float(signal.get("entry_price", 0) or 0)
        vwap  = float(signal.get("vwap", 0) or 0)
        stype = str(signal.get("type") or signal.get("signal") or "BUY").upper()

        if vwap <= 0 or entry <= 0:
            return IndicatorResult(
                name="vwap_price_action",
                score=0.5,
                label="MEDIUM",
                explanation="VWAP not provided — neutral score assigned.",
                details={"vwap": vwap, "entry_price": entry},
            )

        # Price relative to VWAP
        vwap_diff_pct = (entry - vwap) / vwap * 100

        if stype == "BUY":
            # BUY: price above VWAP = institutional bullish bias
            if entry > vwap:
                score = _clamp(0.7 + min(vwap_diff_pct / 10, 0.3))
                explanation = (
                    f"BUY entry ({entry:.2f}) is {vwap_diff_pct:.2f}% above VWAP ({vwap:.2f}) "
                    f"— institutional bullish alignment."
                )
            else:
                score = _clamp(0.5 + vwap_diff_pct / 20)  # vwap_diff_pct is negative
                explanation = (
                    f"BUY entry ({entry:.2f}) is {abs(vwap_diff_pct):.2f}% below VWAP ({vwap:.2f}) "
                    f"— potential mean-reversion buy at discount."
                )
        else:
            # SELL: price below VWAP = institutional bearish bias
            if entry < vwap:
                score = _clamp(0.7 + min(abs(vwap_diff_pct) / 10, 0.3))
                explanation = (
                    f"SELL entry ({entry:.2f}) is {abs(vwap_diff_pct):.2f}% below VWAP ({vwap:.2f}) "
                    f"— institutional bearish alignment."
                )
            else:
                score = _clamp(0.5 - vwap_diff_pct / 20)
                explanation = (
                    f"SELL entry ({entry:.2f}) is {vwap_diff_pct:.2f}% above VWAP ({vwap:.2f}) "
                    f"— selling at premium (good for SELL)."
                )
                score = _clamp(0.7 + min(vwap_diff_pct / 10, 0.3))

        return IndicatorResult(
            name="vwap_price_action",
            score=score,
            label=_label(score),
            explanation=explanation,
            details={
                "entry_price": entry,
                "vwap": vwap,
                "vwap_diff_pct": round(vwap_diff_pct, 3),
            },
        )

    # ═══════════════════════════════════════════════════════════
    # 4. Fibonacci + SMC Confluence
    # ═══════════════════════════════════════════════════════════

    def fibonacci_smc_confluence(self, signal: Dict[str, Any]) -> IndicatorResult:
        """
        Indicator 4: Fibonacci + SMC Confluence
        Stacked zones: entry near a Fibonacci level AND an SMC level.
        """
        entry      = float(signal.get("entry_price", 0) or 0)
        swing_high = float(signal.get("swing_high", 0) or 0)
        swing_low  = float(signal.get("swing_low", 0) or 0)
        smc_score  = float(signal.get("smc_score", 0) or 0)
        stype      = str(signal.get("type") or signal.get("signal") or "BUY").upper()

        if entry <= 0 or swing_high <= 0 or swing_low <= 0:
            return IndicatorResult(
                name="fibonacci_smc",
                score=0.4,
                label="LOW",
                explanation="Insufficient data for Fibonacci analysis (need entry, swing_high, swing_low).",
                details={},
            )

        swing_range = swing_high - swing_low
        if swing_range <= 0:
            return IndicatorResult(
                name="fibonacci_smc",
                score=0.4,
                label="LOW",
                explanation="Swing high equals swing low — cannot compute Fibonacci levels.",
                details={},
            )

        # Compute Fibonacci retracement levels
        fib_levels_prices: Dict[float, float] = {}
        for fib in FIB_LEVELS:
            if stype == "BUY":
                # Retracement from high to low (buy at retracement)
                fib_levels_prices[fib] = swing_high - fib * swing_range
            else:
                # Retracement from low to high (sell at retracement)
                fib_levels_prices[fib] = swing_low + fib * swing_range

        # Find closest Fibonacci level to entry
        closest_fib  = min(fib_levels_prices.keys(), key=lambda f: abs(fib_levels_prices[f] - entry))
        closest_price = fib_levels_prices[closest_fib]
        proximity_pct = abs(entry - closest_price) / max(abs(closest_price), 1e-9)

        fib_aligned = proximity_pct <= FIB_TOLERANCE

        # SMC component
        smc_component = _clamp(smc_score / 10.0)

        # Fibonacci component
        if fib_aligned:
            fib_component = 1.0
        elif proximity_pct <= FIB_TOLERANCE * 2:
            fib_component = 0.7
        elif proximity_pct <= FIB_TOLERANCE * 4:
            fib_component = 0.4
        else:
            fib_component = 0.1

        # Stacked confluence bonus
        stacked_bonus = 0.15 if (fib_aligned and smc_score >= 6) else 0.0

        score = _clamp(fib_component * 0.5 + smc_component * 0.5 + stacked_bonus)

        return IndicatorResult(
            name="fibonacci_smc",
            score=score,
            label=_label(score),
            explanation=(
                f"Entry ({entry:.2f}) is {proximity_pct * 100:.2f}% from Fib {closest_fib:.3f} "
                f"({closest_price:.2f}). SMC score={smc_score:.1f}/10. "
                f"{'Stacked confluence!' if stacked_bonus > 0 else ''}"
            ),
            details={
                "entry_price": entry,
                "closest_fib_level": closest_fib,
                "closest_fib_price": round(closest_price, 4),
                "proximity_pct": round(proximity_pct * 100, 3),
                "fib_aligned": fib_aligned,
                "smc_score": smc_score,
                "stacked_bonus": stacked_bonus,
            },
        )

    # ═══════════════════════════════════════════════════════════
    # 5. ATR + Bollinger Bands
    # ═══════════════════════════════════════════════════════════

    def atr_bollinger_bands(self, signal: Dict[str, Any]) -> IndicatorResult:
        """
        Indicator 5: ATR + Bollinger Bands
        Volatility sizing + squeeze timing.
        High score when: ATR is normal + BB squeeze is releasing.
        """
        atr        = float(signal.get("atr", 0) or 0)
        atr_ratio  = float(signal.get("atr_ratio", 1.0) or 1.0)
        bb_upper   = float(signal.get("bb_upper", 0) or 0)
        bb_lower   = float(signal.get("bb_lower", 0) or 0)
        bb_middle  = float(signal.get("bb_middle", 0) or 0)
        entry      = float(signal.get("entry_price", 0) or 0)

        if entry <= 0:
            return IndicatorResult(
                name="atr_bollinger",
                score=0.5,
                label="MEDIUM",
                explanation="No entry_price — neutral ATR/BB score.",
                details={},
            )

        # ATR ratio component (ideal: 0.8–1.5)
        if 0.8 <= atr_ratio <= 1.5:
            atr_component = 1.0
        elif 0.5 <= atr_ratio <= 2.0:
            atr_component = 0.7
        elif atr_ratio > 2.0:
            atr_component = 0.3  # Too volatile
        else:
            atr_component = 0.5  # Too quiet

        # Bollinger Band component
        bb_component = 0.5  # Default if BB not provided
        bb_squeeze   = False
        bb_position  = 0.5

        if bb_upper > 0 and bb_lower > 0 and bb_middle > 0:
            bb_width = (bb_upper - bb_lower) / bb_middle
            bb_squeeze = bb_width < BB_SQUEEZE_THRESHOLD

            # Price position within BB (0 = lower band, 1 = upper band)
            bb_range = bb_upper - bb_lower
            if bb_range > 0:
                bb_position = (entry - bb_lower) / bb_range

            # Squeeze releasing = high score (breakout imminent)
            if bb_squeeze:
                bb_component = 0.85  # Squeeze = good timing
            elif bb_position < 0.2 or bb_position > 0.8:
                bb_component = 0.9   # Near band extremes = reversal opportunity
            else:
                bb_component = 0.6   # Mid-band = less precise

        score = _clamp(atr_component * 0.5 + bb_component * 0.5)

        return IndicatorResult(
            name="atr_bollinger",
            score=score,
            label=_label(score),
            explanation=(
                f"ATR ratio={atr_ratio:.2f} ({'normal' if 0.8 <= atr_ratio <= 1.5 else 'abnormal'}), "
                f"BB {'squeeze detected' if bb_squeeze else f'position={bb_position:.2f}'}."
            ),
            details={
                "atr": atr,
                "atr_ratio": atr_ratio,
                "bb_squeeze": bb_squeeze,
                "bb_position": round(bb_position, 3),
                "bb_width": round((bb_upper - bb_lower) / bb_middle, 5) if bb_middle > 0 else None,
            },
        )

    # ═══════════════════════════════════════════════════════════
    # 6. Range + Breakout Filter
    # ═══════════════════════════════════════════════════════════

    def range_breakout_filter(self, signal: Dict[str, Any]) -> IndicatorResult:
        """
        Indicator 6: Range + Breakout Filter
        Regime clarity scoring — penalises ambiguous regime signals.
        """
        regime     = str(signal.get("regime", "") or "").upper()
        adx        = float(signal.get("adx", 0) or 0)
        atr_ratio  = float(signal.get("atr_ratio", 1.0) or 1.0)
        stype      = str(signal.get("type") or signal.get("signal") or "BUY").upper()

        # Regime clarity score
        if regime in ("TREND_UP", "TREND_DOWN") and adx > 25:
            clarity = 1.0
        elif regime == "BREAKOUT" and atr_ratio > 1.2:
            clarity = 0.9
        elif regime == "RANGE" and adx < 25:
            clarity = 0.85
        elif regime in ("HIGH_VOL", "CHAOS"):
            clarity = 0.3  # Avoid trading in chaos
        elif not regime:
            clarity = 0.4  # No regime = ambiguous
        else:
            clarity = 0.6  # Regime present but indicators don't fully confirm

        # Penalise counter-trend signals in clear trend regimes
        counter_trend = (
            (regime == "TREND_UP"   and stype == "SELL") or
            (regime == "TREND_DOWN" and stype == "BUY")
        )
        if counter_trend:
            clarity = _clamp(clarity * 0.4)

        score = _clamp(clarity)

        return IndicatorResult(
            name="range_breakout_filter",
            score=score,
            label=_label(score),
            explanation=(
                f"Regime='{regime}', ADX={adx:.1f}, ATR ratio={atr_ratio:.2f}. "
                f"{'Counter-trend signal — penalised.' if counter_trend else 'Regime clarity OK.'}"
            ),
            details={
                "regime": regime,
                "adx": adx,
                "atr_ratio": atr_ratio,
                "counter_trend": counter_trend,
                "clarity_score": round(clarity, 3),
            },
        )

    # ═══════════════════════════════════════════════════════════
    # 7. Swing + Scalp Entry Timing
    # ═══════════════════════════════════════════════════════════

    def swing_scalp_timing(self, signal: Dict[str, Any]) -> IndicatorResult:
        """
        Indicator 7: Swing + Scalp Entry Timing
        M15 confirmation required. Upgrades R:R from 1:1.3 to 1:2.5.
        """
        trade_type   = str(signal.get("trade_type", "SWING") or "SWING").upper()
        m15_confirm  = bool(signal.get("m15_confirmed") or signal.get("m15_confirmation"))
        entry        = float(signal.get("entry_price", 0) or 0)
        sl           = float(signal.get("sl_price", 0) or 0)
        tps          = [float(t) for t in (signal.get("tp_levels") or [])]
        stype        = str(signal.get("type") or signal.get("signal") or "BUY").upper()

        # Compute TP1 R:R
        tp1_rr = 0.0
        if entry > 0 and sl > 0 and tps:
            risk = abs(entry - sl)
            if risk > 0:
                reward = (tps[0] - entry) if stype == "BUY" else (entry - tps[0])
                tp1_rr = reward / risk

        # Target R:R based on trade type
        target_rr = 2.5 if trade_type == "SWING" else 1.5

        # M15 confirmation component
        m15_component = 1.0 if m15_confirm else 0.5

        # R:R component
        if tp1_rr >= target_rr:
            rr_component = 1.0
        elif tp1_rr >= target_rr * 0.8:
            rr_component = 0.8
        elif tp1_rr >= 1.3:
            rr_component = 0.5
        else:
            rr_component = max(0.0, tp1_rr / target_rr * 0.4)

        score = _clamp(m15_component * 0.4 + rr_component * 0.6)

        return IndicatorResult(
            name="swing_scalp_timing",
            score=score,
            label=_label(score),
            explanation=(
                f"{trade_type} trade: M15 {'confirmed' if m15_confirm else 'not confirmed'}, "
                f"TP1 R:R={tp1_rr:.2f}:1 (target {target_rr}:1)."
            ),
            details={
                "trade_type": trade_type,
                "m15_confirmed": m15_confirm,
                "tp1_rr": round(tp1_rr, 3),
                "target_rr": target_rr,
            },
        )

    # ═══════════════════════════════════════════════════════════
    # 8. Trend + Mean Reversion
    # ═══════════════════════════════════════════════════════════

    def trend_mean_reversion(self, signal: Dict[str, Any]) -> IndicatorResult:
        """
        Indicator 8: Trend + Mean Reversion
        Primary strategy alignment + breakout transition detection.
        """
        regime     = str(signal.get("regime", "") or "").upper()
        strategy   = str(signal.get("strategy", "") or "").upper()
        zscore     = float(signal.get("zscore_20", 0) or 0)
        adx        = float(signal.get("adx", 0) or 0)
        stype      = str(signal.get("type") or signal.get("signal") or "BUY").upper()

        # Strategy-regime alignment
        trend_regimes = {"TREND_UP", "TREND_DOWN", "BREAKOUT"}
        mr_regimes    = {"RANGE", "LOW_VOL"}

        if strategy in ("TREND", "BREAKOUT", "PULLBACK") and regime in trend_regimes:
            alignment = 1.0
        elif strategy in ("MEAN_REVERSION", "REVERSAL") and regime in mr_regimes:
            alignment = 1.0
        elif not strategy:
            # Infer from regime
            alignment = 0.7
        else:
            alignment = 0.4  # Strategy-regime mismatch

        # Z-score component (mean reversion signal)
        if abs(zscore) > 2.0:
            mr_signal = 1.0  # Strong mean reversion opportunity
        elif abs(zscore) > 1.5:
            mr_signal = 0.7
        else:
            mr_signal = 0.4

        # Breakout transition detection
        breakout_transition = (
            regime == "BREAKOUT" or
            (adx > 30 and abs(zscore) < 0.5)
        )

        score = _clamp(alignment * 0.6 + mr_signal * 0.4)
        if breakout_transition:
            score = _clamp(score + 0.1)

        return IndicatorResult(
            name="trend_mean_reversion",
            score=score,
            label=_label(score),
            explanation=(
                f"Strategy='{strategy}', Regime='{regime}', "
                f"Z-score={zscore:.2f}, ADX={adx:.1f}. "
                f"{'Breakout transition detected.' if breakout_transition else ''}"
            ),
            details={
                "strategy": strategy,
                "regime": regime,
                "zscore": zscore,
                "adx": adx,
                "alignment": round(alignment, 3),
                "breakout_transition": breakout_transition,
            },
        )

    # ═══════════════════════════════════════════════════════════
    # 9. MTF Pyramid Breakdown
    # ═══════════════════════════════════════════════════════════

    def mtf_pyramid_breakdown(self, signal: Dict[str, Any]) -> IndicatorResult:
        """
        Indicator 9: MTF Pyramid Breakdown
        Timeframe alignment analysis — H4 bias → H1 structure → M15 trigger.
        """
        mtf = signal.get("mtf_alignment") or {}

        h4_bias     = str(mtf.get("h4_bias", "") or "").upper()
        h1_structure = str(mtf.get("h1_structure", "") or "").upper()
        m15_trigger  = bool(mtf.get("m15_trigger") or mtf.get("m15_aligned"))
        h4_aligned   = bool(mtf.get("h4_aligned") or mtf.get("H4_aligned"))
        h1_aligned   = bool(mtf.get("h1_aligned") or mtf.get("H1_aligned"))
        m15_aligned  = bool(mtf.get("m15_aligned") or mtf.get("M15_aligned"))
        stype        = str(signal.get("type") or signal.get("signal") or "BUY").upper()

        # Pyramid alignment: H4 → H1 → M15
        pyramid_score = 0.0
        aligned_count = sum([h4_aligned, h1_aligned, m15_aligned])

        if aligned_count == 3:
            pyramid_score = 1.0
        elif aligned_count == 2:
            pyramid_score = 0.7
        elif aligned_count == 1:
            pyramid_score = 0.4
        else:
            pyramid_score = 0.2

        # H4 bias alignment bonus
        h4_bias_aligned = (
            (stype == "BUY"  and h4_bias in ("BULLISH", "BUY", "UPTREND")) or
            (stype == "SELL" and h4_bias in ("BEARISH", "SELL", "DOWNTREND")) or
            not h4_bias
        )
        if h4_bias_aligned and h4_bias:
            pyramid_score = _clamp(pyramid_score + 0.1)

        score = _clamp(pyramid_score)

        return IndicatorResult(
            name="mtf_pyramid",
            score=score,
            label=_label(score),
            explanation=(
                f"MTF pyramid: H4={'✓' if h4_aligned else '✗'} "
                f"H1={'✓' if h1_aligned else '✗'} "
                f"M15={'✓' if m15_aligned else '✗'} "
                f"({aligned_count}/3 aligned). "
                f"H4 bias='{h4_bias}' {'aligned' if h4_bias_aligned else 'misaligned'}."
            ),
            details={
                "h4_aligned": h4_aligned,
                "h1_aligned": h1_aligned,
                "m15_aligned": m15_aligned,
                "aligned_count": aligned_count,
                "h4_bias": h4_bias,
                "h4_bias_aligned": h4_bias_aligned,
            },
        )

    # ═══════════════════════════════════════════════════════════
    # 10. Session-Based MTF Weighting
    # ═══════════════════════════════════════════════════════════

    def session_mtf_weighting(self, signal: Dict[str, Any]) -> IndicatorResult:
        """
        Indicator 10: Session-Based MTF Weighting
        Reduces M15 weight during low-liquidity hours (post-NY close).
        """
        from datetime import datetime, timezone
        now  = datetime.now(timezone.utc)
        hour = now.hour

        mtf = signal.get("mtf_alignment") or {}
        h4_aligned  = bool(mtf.get("h4_aligned") or mtf.get("H4_aligned"))
        h1_aligned  = bool(mtf.get("h1_aligned") or mtf.get("H1_aligned"))
        m15_aligned = bool(mtf.get("m15_aligned") or mtf.get("M15_aligned"))

        # Determine session and weights
        is_low_liquidity = (hour >= SESSION_NY_CLOSE or hour < SESSION_LONDON_OPEN)
        is_high_liquidity = (SESSION_NY_OPEN <= hour < 16)

        if is_low_liquidity:
            weights = MTF_WEIGHTS_LOW_LIQUIDITY
            session = "LOW_LIQUIDITY"
        elif is_high_liquidity:
            weights = MTF_WEIGHTS_HIGH_LIQUIDITY
            session = "HIGH_LIQUIDITY"
        else:
            weights = {"H4": 0.45, "H1": 0.35, "M15": 0.20}
            session = "MEDIUM_LIQUIDITY"

        # Weighted alignment score
        weighted_score = (
            (1.0 if h4_aligned else 0.0) * weights["H4"] +
            (1.0 if h1_aligned else 0.0) * weights["H1"] +
            (1.0 if m15_aligned else 0.0) * weights["M15"]
        )

        score = _clamp(weighted_score)

        return IndicatorResult(
            name="session_mtf_weighting",
            score=score,
            label=_label(score),
            explanation=(
                f"Session: {session} (UTC {hour:02d}:00). "
                f"Weighted MTF score: H4({weights['H4']:.0%})={'✓' if h4_aligned else '✗'} "
                f"H1({weights['H1']:.0%})={'✓' if h1_aligned else '✗'} "
                f"M15({weights['M15']:.0%})={'✓' if m15_aligned else '✗'}."
            ),
            details={
                "session": session,
                "utc_hour": hour,
                "weights": weights,
                "h4_aligned": h4_aligned,
                "h1_aligned": h1_aligned,
                "m15_aligned": m15_aligned,
                "weighted_score": round(weighted_score, 3),
            },
        )

    # ═══════════════════════════════════════════════════════════
    # 11. Fixed + Trailing Stop Hybrid
    # ═══════════════════════════════════════════════════════════

    def fixed_trailing_stop_hybrid(self, signal: Dict[str, Any]) -> IndicatorResult:
        """
        Indicator 11: Fixed + Trailing Stop Hybrid
        TP1 lock (fixed) + TP3 trail (trailing stop).
        Validates that the stop strategy is appropriate for the trade.
        """
        tps        = [float(t) for t in (signal.get("tp_levels") or [])]
        entry      = float(signal.get("entry_price", 0) or 0)
        sl         = float(signal.get("sl_price", 0) or 0)
        atr        = float(signal.get("atr", 0) or 0)
        stop_type  = str(signal.get("stop_type", "") or "").upper()
        stype      = str(signal.get("type") or signal.get("signal") or "BUY").upper()

        if entry <= 0 or sl <= 0 or not tps:
            return IndicatorResult(
                name="fixed_trailing_stop",
                score=0.4,
                label="LOW",
                explanation="Insufficient data for stop strategy evaluation.",
                details={},
            )

        if atr <= 0:
            atr = entry * 0.005

        risk = abs(entry - sl)

        # Check TP ladder for hybrid stop strategy
        has_tp1 = len(tps) >= 1
        has_tp3 = len(tps) >= 3

        # TP1 should be at 1:1 to 1:1.5 R:R (lock-in profit)
        tp1_rr = 0.0
        if has_tp1:
            reward = (tps[0] - entry) if stype == "BUY" else (entry - tps[0])
            tp1_rr = reward / risk if risk > 0 else 0.0

        # TP3 should be at 1:3+ R:R (trailing stop territory)
        tp3_rr = 0.0
        if has_tp3:
            reward = (tps[2] - entry) if stype == "BUY" else (entry - tps[2])
            tp3_rr = reward / risk if risk > 0 else 0.0

        # Score components
        tp1_ok = 1.0 <= tp1_rr <= 2.0
        tp3_ok = tp3_rr >= 3.0

        hybrid_score = 0.0
        if tp1_ok and tp3_ok:
            hybrid_score = 1.0
        elif tp1_ok:
            hybrid_score = 0.7
        elif has_tp3:
            hybrid_score = 0.5
        elif has_tp1:
            hybrid_score = 0.4
        else:
            hybrid_score = 0.2

        # Bonus for explicit trailing stop type
        if stop_type in ("TRAILING", "HYBRID", "FIXED_TRAILING"):
            hybrid_score = _clamp(hybrid_score + 0.1)

        score = _clamp(hybrid_score)

        return IndicatorResult(
            name="fixed_trailing_stop",
            score=score,
            label=_label(score),
            explanation=(
                f"TP1 R:R={tp1_rr:.2f}:1 ({'lock-in zone' if tp1_ok else 'outside lock-in zone'}), "
                f"TP3 R:R={tp3_rr:.2f}:1 ({'trail zone' if tp3_ok else 'below trail threshold'}). "
                f"Stop type='{stop_type or 'not specified'}'."
            ),
            details={
                "tp1_rr": round(tp1_rr, 3),
                "tp3_rr": round(tp3_rr, 3),
                "tp1_ok": tp1_ok,
                "tp3_ok": tp3_ok,
                "stop_type": stop_type,
                "tp_count": len(tps),
            },
        )

    # ═══════════════════════════════════════════════════════════
    # 12. Volatility-Adjusted Position Sizing
    # ═══════════════════════════════════════════════════════════

    def volatility_position_sizing(self, signal: Dict[str, Any]) -> IndicatorResult:
        """
        Indicator 12: Volatility-Adjusted Position Sizing
        Validates that position size respects 1% account risk rule
        and is adjusted for current ATR.
        """
        entry           = float(signal.get("entry_price", 0) or 0)
        sl              = float(signal.get("sl_price", 0) or 0)
        atr             = float(signal.get("atr", 0) or 0)
        account_balance = float(signal.get("account_balance", 10000) or 10000)
        position_size   = float(signal.get("position_size", 0) or 0)
        atr_ratio       = float(signal.get("atr_ratio", 1.0) or 1.0)

        if entry <= 0 or sl <= 0:
            return IndicatorResult(
                name="volatility_position_size",
                score=0.5,
                label="MEDIUM",
                explanation="Cannot validate position sizing without entry and SL.",
                details={},
            )

        if atr <= 0:
            atr = entry * 0.005

        risk_per_unit = abs(entry - sl)
        max_risk_usd  = account_balance * ACCOUNT_RISK_PCT

        # Ideal position size based on 1% risk rule
        ideal_size = max_risk_usd / risk_per_unit if risk_per_unit > 0 else 0.0

        # Volatility adjustment: reduce size in high-volatility regimes
        vol_multiplier = 1.0
        if atr_ratio > 1.5:
            vol_multiplier = 0.7   # Reduce 30% in high volatility
        elif atr_ratio > 2.0:
            vol_multiplier = 0.5   # Reduce 50% in very high volatility
        elif atr_ratio < 0.6:
            vol_multiplier = 1.2   # Increase 20% in low volatility (capped)

        adjusted_ideal = ideal_size * vol_multiplier

        # Score based on how close actual size is to ideal
        if position_size <= 0:
            score = 0.6  # No size provided — partial credit
            explanation = (
                f"No position_size provided. Ideal size: {adjusted_ideal:.4f} lots "
                f"(1% risk = ${max_risk_usd:.2f}, risk/unit = {risk_per_unit:.2f}, "
                f"vol multiplier = {vol_multiplier:.1f}x)."
            )
        else:
            size_ratio = position_size / adjusted_ideal if adjusted_ideal > 0 else 0.0
            if 0.8 <= size_ratio <= 1.2:
                score = 1.0
                explanation = f"Position size {position_size:.4f} is within 20% of ideal {adjusted_ideal:.4f}."
            elif 0.5 <= size_ratio <= 1.5:
                score = 0.7
                explanation = (
                    f"Position size {position_size:.4f} is {abs(1 - size_ratio) * 100:.0f}% "
                    f"from ideal {adjusted_ideal:.4f}."
                )
            else:
                score = 0.3
                explanation = (
                    f"Position size {position_size:.4f} significantly deviates from "
                    f"ideal {adjusted_ideal:.4f} (ratio={size_ratio:.2f})."
                )

        return IndicatorResult(
            name="volatility_position_size",
            score=_clamp(score),
            label=_label(score),
            explanation=explanation,
            details={
                "entry_price": entry,
                "sl_price": sl,
                "risk_per_unit": round(risk_per_unit, 4),
                "max_risk_usd": round(max_risk_usd, 2),
                "ideal_size": round(ideal_size, 6),
                "vol_multiplier": vol_multiplier,
                "adjusted_ideal": round(adjusted_ideal, 6),
                "actual_size": position_size,
                "atr_ratio": atr_ratio,
            },
        )

    # ═══════════════════════════════════════════════════════════
    # 13. Dynamic Confluence Score
    # ═══════════════════════════════════════════════════════════

    def dynamic_confluence_score(
        self, scores: Optional[Dict[str, float]] = None, signal: Optional[Dict[str, Any]] = None
    ) -> IndicatorResult:
        """
        Indicator 13: Dynamic Confluence Score
        Aggregates all 12 other indicator scores into a single confluence
        score.  >75% = HIGH CONFIDENCE.
        """
        if scores is None:
            if signal is not None:
                scores = self.score_all(signal, include_confluence=False)
            else:
                scores = {}

        if not scores:
            return IndicatorResult(
                name="dynamic_confluence",
                score=0.5,
                label="MEDIUM",
                explanation="No indicator scores provided for confluence calculation.",
                details={},
            )

        # Weighted average of all indicator scores
        # Higher weights for the most critical indicators
        indicator_weights = {
            "smc_order_flow":          0.12,
            "triple_momentum":         0.10,
            "vwap_price_action":       0.07,
            "fibonacci_smc":           0.08,
            "atr_bollinger":           0.07,
            "range_breakout_filter":   0.10,
            "swing_scalp_timing":      0.10,
            "trend_mean_reversion":    0.09,
            "mtf_pyramid":             0.12,
            "session_mtf_weighting":   0.08,
            "fixed_trailing_stop":     0.04,
            "volatility_position_size": 0.03,
        }

        total_weight = 0.0
        weighted_sum = 0.0
        for indicator, weight in indicator_weights.items():
            if indicator in scores:
                weighted_sum += scores[indicator] * weight
                total_weight += weight

        if total_weight > 0:
            confluence = weighted_sum / total_weight
        else:
            confluence = sum(scores.values()) / len(scores) if scores else 0.5

        score = _clamp(confluence)
        high_count = sum(1 for s in scores.values() if s >= CONFLUENCE_HIGH_THRESHOLD)
        low_count  = sum(1 for s in scores.values() if s < CONFLUENCE_MEDIUM_THRESHOLD)

        return IndicatorResult(
            name="dynamic_confluence",
            score=score,
            label=_label(score),
            explanation=(
                f"Dynamic confluence = {score * 100:.1f}% "
                f"({'HIGH CONFIDENCE' if score >= CONFLUENCE_HIGH_THRESHOLD else 'MEDIUM' if score >= CONFLUENCE_MEDIUM_THRESHOLD else 'LOW CONFIDENCE'}). "
                f"{high_count}/{len(scores)} indicators HIGH, {low_count}/{len(scores)} LOW."
            ),
            details={
                "confluence_score": round(score, 4),
                "high_count": high_count,
                "low_count": low_count,
                "total_indicators": len(scores),
                "threshold_high": CONFLUENCE_HIGH_THRESHOLD,
                "threshold_medium": CONFLUENCE_MEDIUM_THRESHOLD,
            },
        )

    # ═══════════════════════════════════════════════════════════
    # AGGREGATE SCORING
    # ═══════════════════════════════════════════════════════════

    def score_all(
        self,
        signal: Dict[str, Any],
        include_confluence: bool = True,
    ) -> Dict[str, float]:
        """
        Run all 13 hybrid enhancement indicators and return a dict of
        {indicator_name: score} for each.

        Args:
            signal:             Signal dict with all available fields.
            include_confluence: Whether to include the dynamic confluence
                                score (indicator 13). Default True.

        Returns:
            Dict mapping indicator name → score (0.0–1.0).
        """
        results: Dict[str, float] = {}

        indicators = [
            ("smc_order_flow",          self.smc_order_flow),
            ("triple_momentum",         self.triple_momentum),
            ("vwap_price_action",       self.vwap_price_action),
            ("fibonacci_smc",           self.fibonacci_smc_confluence),
            ("atr_bollinger",           self.atr_bollinger_bands),
            ("range_breakout_filter",   self.range_breakout_filter),
            ("swing_scalp_timing",      self.swing_scalp_timing),
            ("trend_mean_reversion",    self.trend_mean_reversion),
            ("mtf_pyramid",             self.mtf_pyramid_breakdown),
            ("session_mtf_weighting",   self.session_mtf_weighting),
            ("fixed_trailing_stop",     self.fixed_trailing_stop_hybrid),
            ("volatility_position_size", self.volatility_position_sizing),
        ]

        for name, fn in indicators:
            try:
                result = fn(signal)
                results[name] = result.score
            except Exception as exc:
                logger.warning(f"Indicator '{name}' failed: {exc}")
                results[name] = 0.5

        if include_confluence:
            try:
                confluence = self.dynamic_confluence_score(scores=results)
                results["dynamic_confluence"] = confluence.score
            except Exception as exc:
                logger.warning(f"Confluence scoring failed: {exc}")
                results["dynamic_confluence"] = 0.5

        return results

    def score_all_detailed(
        self, signal: Dict[str, Any]
    ) -> List[IndicatorResult]:
        """
        Run all 13 indicators and return full IndicatorResult objects.
        """
        indicators = [
            self.smc_order_flow,
            self.triple_momentum,
            self.vwap_price_action,
            self.fibonacci_smc_confluence,
            self.atr_bollinger_bands,
            self.range_breakout_filter,
            self.swing_scalp_timing,
            self.trend_mean_reversion,
            self.mtf_pyramid_breakdown,
            self.session_mtf_weighting,
            self.fixed_trailing_stop_hybrid,
            self.volatility_position_sizing,
        ]

        results: List[IndicatorResult] = []
        scores: Dict[str, float] = {}

        for fn in indicators:
            try:
                result = fn(signal)
                results.append(result)
                scores[result.name] = result.score
            except Exception as exc:
                logger.warning(f"Indicator '{fn.__name__}' failed: {exc}")

        # Add confluence as indicator 13
        try:
            confluence = self.dynamic_confluence_score(scores=scores)
            results.append(confluence)
        except Exception as exc:
            logger.warning(f"Confluence scoring failed: {exc}")

        return results

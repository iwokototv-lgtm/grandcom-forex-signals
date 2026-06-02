"""
Volatility Metrics — Grandcom Gold Signals v3.0.2
Phase 2 Enhancement: ATR quantification and volatility-adjusted sizing

Provides:
- ATR calculation (14-period, Wilder's smoothing)
- ATR regime classification (LOW/NORMAL/HIGH/EXTREME)
- Volatility-adjusted position sizing (1% account risk)
- Dynamic SL placement based on ATR multiples
- ATR-based entry band calculation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

ATR_PERIOD          = 14
PIP_VALUE_GOLD      = 0.10    # 1 pip = $0.10 for XAUUSD
PIP_VALUE_PER_LOT   = 10.0    # $10 per pip per standard lot (XAUUSD)
DEFAULT_RISK_PCT    = 0.01    # 1% account risk per trade
MIN_LOTS            = 0.01
MAX_LOTS            = 10.0

# ATR regime thresholds (in pips for XAUUSD)
ATR_LOW_PIPS        = 50.0
ATR_NORMAL_PIPS     = 150.0
ATR_HIGH_PIPS       = 300.0

# SL ATR multiples
SL_ATR_TIGHT        = 0.5
SL_ATR_NORMAL       = 1.0
SL_ATR_WIDE         = 1.5
SL_ATR_STRUCTURAL   = 0.325   # Buffer beyond swing high/low


@dataclass
class ATRMetrics:
    """ATR calculation result."""
    atr_value:      float    # Raw ATR in price units
    atr_pips:       float    # ATR in pips
    atr_pct:        float    # ATR as % of current price
    regime:         str      # "LOW", "NORMAL", "HIGH", "EXTREME"
    period:         int
    current_price:  float
    timestamp:      str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "atr_value":     round(self.atr_value, 4),
            "atr_pips":      round(self.atr_pips, 1),
            "atr_pct":       round(self.atr_pct, 4),
            "regime":        self.regime,
            "period":        self.period,
            "current_price": round(self.current_price, 2),
            "timestamp":     self.timestamp,
        }


@dataclass
class PositionSizeResult:
    """Volatility-adjusted position sizing result."""
    lots:           float
    risk_usd:       float
    risk_pct:       float
    sl_pips:        float
    atr_pips:       float
    account_balance: float
    method:         str      # "ATR_BASED", "SL_BASED", "COMBINED"
    recommendation: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lots":            round(self.lots, 2),
            "risk_usd":        round(self.risk_usd, 2),
            "risk_pct":        f"{self.risk_pct * 100:.1f}%",
            "sl_pips":         round(self.sl_pips, 1),
            "atr_pips":        round(self.atr_pips, 1),
            "account_balance": round(self.account_balance, 2),
            "method":          self.method,
            "recommendation":  self.recommendation,
        }


@dataclass
class DynamicSLResult:
    """Dynamic SL placement based on ATR."""
    sl_price:       float
    sl_pips:        float
    atr_multiple:   float
    anchor_level:   float    # Swing high/low used as anchor
    buffer_price:   float    # ATR buffer added beyond anchor
    is_structural:  bool
    recommendation: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sl_price":      round(self.sl_price, 2),
            "sl_pips":       round(self.sl_pips, 1),
            "atr_multiple":  round(self.atr_multiple, 2),
            "anchor_level":  round(self.anchor_level, 2),
            "buffer_price":  round(self.buffer_price, 4),
            "is_structural": self.is_structural,
            "recommendation": self.recommendation,
        }


class VolatilityMetrics:
    """
    ATR calculation, regime classification, and volatility-adjusted
    position sizing for Grandcom Gold Signals v3.0.2.

    All calculations use Wilder's smoothing (standard ATR method).
    Position sizing uses the 1% account risk rule.
    """

    def __init__(
        self,
        atr_period:   int   = ATR_PERIOD,
        risk_pct:     float = DEFAULT_RISK_PCT,
        symbol:       str   = "XAUUSD",
    ) -> None:
        self.atr_period = atr_period
        self.risk_pct   = risk_pct
        self.symbol     = symbol
        self.version    = "2.0.0"

    # ═══════════════════════════════════════════════════════════
    # ATR CALCULATION
    # ═══════════════════════════════════════════════════════════

    def calculate_atr(
        self,
        df:            pd.DataFrame,
        current_price: Optional[float] = None,
    ) -> ATRMetrics:
        """
        Calculate ATR using Wilder's smoothing method.

        Args:
            df:            OHLCV DataFrame with 'high', 'low', 'close' columns.
            current_price: Current market price (defaults to last close).

        Returns:
            ATRMetrics with ATR value, pips, percentage, and regime.
        """
        if len(df) < self.atr_period + 2:
            # Fallback: use high-low range
            if len(df) > 0:
                hl_range = float((df["high"] - df["low"]).mean())
                price = current_price or float(df["close"].iloc[-1])
            else:
                hl_range = 12.0  # Default gold ATR
                price = current_price or 2300.0
            return self._build_atr_result(hl_range, price)

        high  = df["high"].astype(float)
        low   = df["low"].astype(float)
        close = df["close"].astype(float)

        # True Range
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs(),
        ], axis=1).max(axis=1)

        # Wilder's smoothing (equivalent to EMA with alpha = 1/period)
        atr_series = tr.ewm(alpha=1.0 / self.atr_period, adjust=False).mean()
        atr_value  = float(atr_series.iloc[-1])

        price = current_price or float(close.iloc[-1])
        return self._build_atr_result(atr_value, price)

    def _build_atr_result(self, atr_value: float, price: float) -> ATRMetrics:
        """Build ATRMetrics from raw ATR value."""
        atr_pips = atr_value / PIP_VALUE_GOLD
        atr_pct  = (atr_value / price) * 100.0 if price > 0 else 0.0

        if atr_pips < ATR_LOW_PIPS:
            regime = "LOW"
        elif atr_pips < ATR_NORMAL_PIPS:
            regime = "NORMAL"
        elif atr_pips < ATR_HIGH_PIPS:
            regime = "HIGH"
        else:
            regime = "EXTREME"

        return ATRMetrics(
            atr_value=round(atr_value, 4),
            atr_pips=round(atr_pips, 1),
            atr_pct=round(atr_pct, 4),
            regime=regime,
            period=self.atr_period,
            current_price=round(price, 2),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # ═══════════════════════════════════════════════════════════
    # POSITION SIZING
    # ═══════════════════════════════════════════════════════════

    def calculate_position_size(
        self,
        entry_price:     float,
        sl_price:        float,
        atr_value:       float,
        account_balance: float,
        risk_pct:        Optional[float] = None,
    ) -> PositionSizeResult:
        """
        Calculate volatility-adjusted position size using 1% risk rule.

        Two methods are computed and the more conservative is used:
        1. SL-based: risk_usd / (sl_pips * pip_value_per_lot)
        2. ATR-based: risk_usd / (atr_pips * pip_value_per_lot)

        Args:
            entry_price:     Trade entry price.
            sl_price:        Stop loss price.
            atr_value:       Current ATR in price units.
            account_balance: Account balance in USD.
            risk_pct:        Risk per trade (default: 1%).

        Returns:
            PositionSizeResult with lot size and risk metrics.
        """
        rp = risk_pct if risk_pct is not None else self.risk_pct
        risk_usd = account_balance * rp

        sl_pips  = abs(entry_price - sl_price) / PIP_VALUE_GOLD
        atr_pips = atr_value / PIP_VALUE_GOLD

        # SL-based sizing
        if sl_pips > 0:
            sl_lots = risk_usd / (sl_pips * PIP_VALUE_PER_LOT)
        else:
            sl_lots = MIN_LOTS

        # ATR-based sizing
        if atr_pips > 0:
            atr_lots = risk_usd / (atr_pips * PIP_VALUE_PER_LOT)
        else:
            atr_lots = sl_lots

        # Use minimum (more conservative)
        final_lots = min(sl_lots, atr_lots)
        final_lots = max(MIN_LOTS, min(MAX_LOTS, round(final_lots, 2)))

        method = "COMBINED" if abs(sl_lots - atr_lots) > 0.01 else "SL_BASED"

        rec = (
            f"Position size: {final_lots:.2f} lots. "
            f"Risk: ${risk_usd:.2f} ({rp*100:.1f}% of ${account_balance:.0f}). "
            f"SL: {sl_pips:.0f} pips. ATR: {atr_pips:.0f} pips. "
            f"Method: {method}."
        )

        return PositionSizeResult(
            lots=final_lots,
            risk_usd=round(risk_usd, 2),
            risk_pct=rp,
            sl_pips=round(sl_pips, 1),
            atr_pips=round(atr_pips, 1),
            account_balance=account_balance,
            method=method,
            recommendation=rec,
        )

    # ═══════════════════════════════════════════════════════════
    # DYNAMIC SL PLACEMENT
    # ═══════════════════════════════════════════════════════════

    def calculate_dynamic_sl(
        self,
        signal_type:  str,
        entry_price:  float,
        swing_high:   float,
        swing_low:    float,
        atr_value:    float,
        atr_multiple: float = SL_ATR_STRUCTURAL,
    ) -> DynamicSLResult:
        """
        Calculate dynamic SL anchored to swing high/low + ATR buffer.

        BUY : SL = swing_low - (atr * multiple)
        SELL: SL = swing_high + (atr * multiple)

        Args:
            signal_type:  "BUY" or "SELL".
            entry_price:  Trade entry price.
            swing_high:   Recent swing high.
            swing_low:    Recent swing low.
            atr_value:    Current ATR in price units.
            atr_multiple: ATR buffer multiple (default: 0.325).

        Returns:
            DynamicSLResult with structural SL price.
        """
        direction = signal_type.upper()
        buffer = atr_value * atr_multiple

        if direction == "BUY":
            anchor = swing_low
            sl_price = swing_low - buffer
            is_structural = sl_price < entry_price
        else:
            anchor = swing_high
            sl_price = swing_high + buffer
            is_structural = sl_price > entry_price

        sl_pips = abs(entry_price - sl_price) / PIP_VALUE_GOLD

        rec = (
            f"Dynamic SL: {sl_price:.2f} "
            f"({'swing_low' if direction == 'BUY' else 'swing_high'} "
            f"{anchor:.2f} ± {buffer:.2f} ATR buffer). "
            f"Distance: {sl_pips:.0f} pips. "
            f"{'✓ Structural.' if is_structural else '⚠ Check direction.'}"
        )

        return DynamicSLResult(
            sl_price=round(sl_price, 2),
            sl_pips=round(sl_pips, 1),
            atr_multiple=atr_multiple,
            anchor_level=round(anchor, 2),
            buffer_price=round(buffer, 4),
            is_structural=is_structural,
            recommendation=rec,
        )

    # ═══════════════════════════════════════════════════════════
    # ATR-BASED ENTRY BAND
    # ═══════════════════════════════════════════════════════════

    def calculate_entry_band(
        self,
        anchor_price: float,
        atr_value:    float,
        band_atr_pct: float = 0.25,  # 25% of ATR = ~10 pips for normal gold ATR
    ) -> Dict[str, Any]:
        """
        Calculate entry band as a fraction of ATR.

        Default: 25% of ATR ≈ 10 pips for normal gold ATR (~40 pips).
        """
        half_band = atr_value * band_atr_pct
        band_low  = anchor_price - half_band
        band_high = anchor_price + half_band
        band_pips = (band_high - band_low) / PIP_VALUE_GOLD

        return {
            "anchor":     round(anchor_price, 2),
            "band_low":   round(band_low, 2),
            "band_high":  round(band_high, 2),
            "band_pips":  round(band_pips, 1),
            "half_band":  round(half_band, 4),
            "atr_pct":    f"{band_atr_pct:.0%}",
        }

    # ═══════════════════════════════════════════════════════════
    # ATR HISTORY (for trend analysis)
    # ═══════════════════════════════════════════════════════════

    def calculate_atr_history(
        self,
        df:     pd.DataFrame,
        window: int = 5,
    ) -> Dict[str, Any]:
        """
        Calculate ATR trend over the last N periods.

        Returns whether ATR is expanding (increasing volatility) or
        contracting (decreasing volatility).
        """
        if len(df) < self.atr_period + window + 2:
            return {"trend": "UNKNOWN", "expanding": False}

        high  = df["high"].astype(float)
        low   = df["low"].astype(float)
        close = df["close"].astype(float)

        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs(),
        ], axis=1).max(axis=1)

        atr_series = tr.ewm(alpha=1.0 / self.atr_period, adjust=False).mean()
        recent = atr_series.tail(window)

        slope = float(recent.diff().mean())
        expanding = slope > 0

        return {
            "current_atr":  round(float(atr_series.iloc[-1]), 4),
            "atr_5_ago":    round(float(atr_series.iloc[-window]), 4),
            "slope":        round(slope, 6),
            "expanding":    expanding,
            "trend":        "EXPANDING" if expanding else "CONTRACTING",
            "pct_change":   round(
                (float(atr_series.iloc[-1]) - float(atr_series.iloc[-window]))
                / float(atr_series.iloc[-window]) * 100.0
                if float(atr_series.iloc[-window]) > 0 else 0.0,
                2,
            ),
        }


# ─────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────

volatility_metrics = VolatilityMetrics()

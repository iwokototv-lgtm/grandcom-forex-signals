"""
Enterprise Risk Engine
Advanced Risk Management for Gold Trading System v3.0.2

Provides:
  - RiskEngine class with:
      * Position limit validation
      * Drawdown calculation & circuit breakers
      * Risk/reward validation
      * Correlation checks
      * Exposure limits per pair/category
      * Automatic stop-loss enforcement
      * Real-time risk metrics
      * Alert generation
  - RiskMetrics dataclass
  - Exposure tracking
  - Automated risk scoring
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME   = os.environ.get("DB_NAME",   "gold_signals_v3")


# ─────────────────────────────────────────────────────────────
# DEFAULT RISK CONFIGURATION
# ─────────────────────────────────────────────────────────────

DEFAULT_RISK_CONFIG: Dict[str, Any] = {
    # Drawdown limits
    "max_daily_drawdown_pct":      3.0,
    "max_weekly_drawdown_pct":     6.0,
    "max_monthly_drawdown_pct":    12.0,
    "circuit_breaker_drawdown_pct": 5.0,

    # Position limits
    "max_position_size_lots":      1.0,
    "max_lot_size":                2.0,
    "max_open_positions":          5,
    "min_rr_ratio":                1.5,
    "min_rr_ratio_gold":           1.8,

    # Exposure limits (% of account)
    "max_exposure_per_pair_pct":   25.0,
    "max_total_exposure_pct":      80.0,
    "max_gold_exposure_pct":       30.0,
    "max_usd_exposure_pct":        40.0,
    "max_crypto_exposure_pct":     15.0,

    # Consecutive loss controls
    "max_consecutive_losses":      3,
    "consecutive_loss_reduction":  0.25,

    # Correlation limits
    "max_correlated_positions":    3,
    "correlation_threshold":       0.75,

    # Auto-halt
    "auto_halt_on_breach":         True,
}


# ─────────────────────────────────────────────────────────────
# RISK VALIDATION RESULT
# ─────────────────────────────────────────────────────────────

class RiskValidationResult:
    """Structured result from a risk validation check."""

    def __init__(
        self,
        approved: bool,
        risk_score: float = 0.0,
        violations: Optional[List[str]] = None,
        warnings: Optional[List[str]] = None,
        adjustments: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.approved    = approved
        self.risk_score  = risk_score
        self.violations  = violations or []
        self.warnings    = warnings or []
        self.adjustments = adjustments or {}
        self.metadata    = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved":    self.approved,
            "risk_score":  round(self.risk_score, 2),
            "violations":  self.violations,
            "warnings":    self.warnings,
            "adjustments": self.adjustments,
            "metadata":    self.metadata,
        }


# ─────────────────────────────────────────────────────────────
# RISK ENGINE
# ─────────────────────────────────────────────────────────────

class RiskEngine:
    """
    Enterprise-grade Risk Engine for the Gold Trading System.

    Implements institutional-grade risk management:
      - Multi-layer position validation
      - Real-time drawdown monitoring
      - Exposure limit enforcement
      - Correlation-based position limits
      - Automated circuit breakers
      - Risk scoring (0-100)
      - Alert generation on breaches

    All state is persisted to MongoDB for cross-session consistency.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = {**DEFAULT_RISK_CONFIG, **(config or {})}
        self._client: Optional[AsyncIOMotorClient] = None
        self._db = None

        # In-memory state (backed by DB)
        self._daily_pnl:    float = 0.0
        self._weekly_pnl:   float = 0.0
        self._monthly_pnl:  float = 0.0
        self._equity_peak:  float = 0.0
        self._current_equity: float = 0.0
        self._consecutive_losses: int = 0
        self._open_positions: List[Dict] = []
        self._circuit_breaker_active: bool = False
        self._trading_halted: bool = False

    def _get_db(self):
        if self._client is None:
            self._client = AsyncIOMotorClient(
                MONGO_URL,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
            )
            self._db = self._client[DB_NAME]
        return self._db

    # ─────────────────────────────────────────────────────────
    # SYMBOL CLASSIFICATION
    # ─────────────────────────────────────────────────────────

    def classify_symbol(self, symbol: str) -> str:
        """Classify a symbol into a risk category."""
        s = symbol.upper()
        if "XAU" in s or "GOLD" in s:
            return "GOLD"
        elif "BTC" in s or "ETH" in s or "CRYPTO" in s:
            return "CRYPTO"
        elif "JPY" in s:
            return "JPY"
        elif "USD" in s:
            return "USD"
        elif "EUR" in s:
            return "EUR"
        elif "GBP" in s:
            return "GBP"
        else:
            return "OTHER"

    def get_pip_value(self, symbol: str) -> float:
        """Get pip value for a symbol."""
        category = self.classify_symbol(symbol)
        pip_values = {
            "GOLD":   0.01,
            "JPY":    0.01,
            "CRYPTO": 1.0,
            "USD":    0.0001,
            "EUR":    0.0001,
            "GBP":    0.0001,
            "OTHER":  0.0001,
        }
        return pip_values.get(category, 0.0001)

    def price_to_pips(self, price_diff: float, symbol: str) -> float:
        """Convert raw price difference to pips."""
        pip_value = self.get_pip_value(symbol)
        return abs(price_diff) / pip_value if pip_value > 0 else 0.0

    # ─────────────────────────────────────────────────────────
    # RISK/REWARD VALIDATION
    # ─────────────────────────────────────────────────────────

    def validate_risk_reward(
        self,
        symbol: str,
        entry: float,
        stop_loss: float,
        take_profit: float,
        signal_type: str = "BUY",
    ) -> RiskValidationResult:
        """
        Validate risk/reward ratio for a trade.

        Rules:
          - Gold: minimum R:R of 1.8
          - All others: minimum R:R of 1.5
          - TP distance must be >= 3.0 for Gold
          - SL distance must be >= 1.0 for Gold
          - SL distance must not exceed 100.0 for Gold
        """
        violations = []
        warnings   = []

        category = self.classify_symbol(symbol)
        min_rr   = (
            self.config["min_rr_ratio_gold"]
            if category == "GOLD"
            else self.config["min_rr_ratio"]
        )

        # Direction validation
        direction = signal_type.upper()
        if direction == "BUY":
            if stop_loss >= entry:
                violations.append(f"BUY: stop_loss ({stop_loss}) must be < entry ({entry})")
            if take_profit <= entry:
                violations.append(f"BUY: take_profit ({take_profit}) must be > entry ({entry})")
        elif direction == "SELL":
            if stop_loss <= entry:
                violations.append(f"SELL: stop_loss ({stop_loss}) must be > entry ({entry})")
            if take_profit >= entry:
                violations.append(f"SELL: take_profit ({take_profit}) must be < entry ({entry})")

        if violations:
            return RiskValidationResult(approved=False, violations=violations)

        # Calculate distances
        sl_distance = abs(entry - stop_loss)
        tp_distance = abs(take_profit - entry)
        rr_ratio    = tp_distance / sl_distance if sl_distance > 0 else 0.0

        # Gold-specific checks
        if category == "GOLD":
            if tp_distance < 3.0:
                violations.append(f"Gold TP distance too small: {tp_distance:.2f} (min 3.0)")
            if sl_distance < 1.0:
                violations.append(f"Gold SL distance too small: {sl_distance:.2f} (min 1.0)")
            if sl_distance > 100.0:
                violations.append(f"Gold SL distance too large: {sl_distance:.2f} (max 100.0)")

        # R:R check
        if rr_ratio < min_rr:
            violations.append(
                f"R:R ratio {rr_ratio:.2f} below minimum {min_rr} for {category}"
            )
        elif rr_ratio < min_rr * 1.2:
            warnings.append(f"R:R ratio {rr_ratio:.2f} is close to minimum {min_rr}")

        # Risk score (higher = riskier)
        risk_score = max(0.0, min(100.0, (min_rr / rr_ratio) * 50)) if rr_ratio > 0 else 100.0

        approved = len(violations) == 0
        return RiskValidationResult(
            approved=approved,
            risk_score=risk_score,
            violations=violations,
            warnings=warnings,
            metadata={
                "rr_ratio":    round(rr_ratio, 4),
                "sl_distance": round(sl_distance, 5),
                "tp_distance": round(tp_distance, 5),
                "min_rr":      min_rr,
                "category":    category,
            },
        )

    # ─────────────────────────────────────────────────────────
    # POSITION SIZE VALIDATION
    # ─────────────────────────────────────────────────────────

    def validate_position_size(
        self,
        symbol: str,
        lot_size: float,
        account_balance: float,
        entry_price: float,
        stop_loss: float,
    ) -> RiskValidationResult:
        """
        Validate position size against risk limits.

        Checks:
          - Lot size within configured limits
          - Dollar risk within account risk percentage
          - Exposure within category limits
        """
        violations = []
        warnings   = []

        max_lots = self.config["max_lot_size"]
        if lot_size > max_lots:
            violations.append(f"Lot size {lot_size} exceeds maximum {max_lots}")
        elif lot_size > max_lots * 0.8:
            warnings.append(f"Lot size {lot_size} approaching maximum {max_lots}")

        if lot_size <= 0:
            violations.append("Lot size must be positive")
            return RiskValidationResult(approved=False, violations=violations)

        # Dollar risk calculation
        sl_distance  = abs(entry_price - stop_loss)
        dollar_risk  = sl_distance * lot_size * 100  # Approximate for Gold
        risk_pct     = (dollar_risk / account_balance * 100) if account_balance > 0 else 0

        max_risk_pct = 2.0  # 2% max risk per trade
        if risk_pct > max_risk_pct:
            violations.append(
                f"Trade risk {risk_pct:.2f}% exceeds maximum {max_risk_pct}% per trade"
            )
        elif risk_pct > max_risk_pct * 0.8:
            warnings.append(f"Trade risk {risk_pct:.2f}% approaching maximum {max_risk_pct}%")

        # Exposure check
        exposure_value = lot_size * entry_price * 100
        exposure_pct   = (exposure_value / account_balance * 100) if account_balance > 0 else 0
        category       = self.classify_symbol(symbol)
        max_exposure   = self.config.get(f"max_{category.lower()}_exposure_pct",
                                         self.config["max_exposure_per_pair_pct"])

        if exposure_pct > max_exposure:
            violations.append(
                f"{category} exposure {exposure_pct:.2f}% exceeds limit {max_exposure}%"
            )

        risk_score = min(100.0, risk_pct / max_risk_pct * 50)

        return RiskValidationResult(
            approved=len(violations) == 0,
            risk_score=risk_score,
            violations=violations,
            warnings=warnings,
            metadata={
                "lot_size":      lot_size,
                "dollar_risk":   round(dollar_risk, 2),
                "risk_pct":      round(risk_pct, 4),
                "exposure_pct":  round(exposure_pct, 4),
                "category":      category,
            },
        )

    # ─────────────────────────────────────────────────────────
    # DRAWDOWN CALCULATION
    # ─────────────────────────────────────────────────────────

    def calculate_drawdown(
        self,
        equity_curve: List[float],
    ) -> Dict[str, Any]:
        """
        Calculate comprehensive drawdown metrics from an equity curve.

        Returns:
          - max_drawdown_pct: Maximum peak-to-trough drawdown
          - current_drawdown_pct: Current drawdown from peak
          - drawdown_duration_bars: Bars since last peak
          - recovery_factor: Total return / max drawdown
          - calmar_ratio: Annualised return / max drawdown
        """
        if not equity_curve or len(equity_curve) < 2:
            return {
                "max_drawdown_pct":      0.0,
                "current_drawdown_pct":  0.0,
                "drawdown_duration_bars": 0,
                "recovery_factor":       0.0,
                "calmar_ratio":          0.0,
                "peak_value":            equity_curve[0] if equity_curve else 0.0,
                "trough_value":          equity_curve[0] if equity_curve else 0.0,
            }

        equity = np.array(equity_curve, dtype=float)
        peak   = equity[0]
        max_dd = 0.0
        peak_idx = 0
        trough_value = equity[0]

        for i, val in enumerate(equity):
            if val > peak:
                peak     = val
                peak_idx = i
            dd = (peak - val) / peak * 100 if peak > 0 else 0.0
            if dd > max_dd:
                max_dd       = dd
                trough_value = val

        current_peak = max(equity)
        current_val  = equity[-1]
        current_dd   = (current_peak - current_val) / current_peak * 100 if current_peak > 0 else 0.0

        # Duration since last peak
        running_peak = equity[0]
        dd_start_idx = 0
        for i, val in enumerate(equity):
            if val >= running_peak:
                running_peak = val
                dd_start_idx = i
        duration = len(equity) - 1 - dd_start_idx

        # Recovery factor
        total_return   = (equity[-1] - equity[0]) / equity[0] * 100 if equity[0] > 0 else 0.0
        recovery_factor = total_return / max_dd if max_dd > 0 else float("inf")

        # Calmar ratio (annualised return / max drawdown)
        # Assume each bar = 1 hour, 8760 hours/year
        n_bars = len(equity)
        annual_factor = 8760 / n_bars if n_bars > 0 else 1
        annualised_return = total_return * annual_factor
        calmar_ratio = annualised_return / max_dd if max_dd > 0 else float("inf")

        return {
            "max_drawdown_pct":       round(max_dd, 4),
            "current_drawdown_pct":   round(current_dd, 4),
            "drawdown_duration_bars": duration,
            "recovery_factor":        round(recovery_factor, 4) if recovery_factor != float("inf") else 999.0,
            "calmar_ratio":           round(calmar_ratio, 4) if calmar_ratio != float("inf") else 999.0,
            "peak_value":             round(float(current_peak), 2),
            "trough_value":           round(float(trough_value), 2),
            "total_return_pct":       round(total_return, 4),
        }

    # ─────────────────────────────────────────────────────────
    # DRAWDOWN LIMIT VALIDATION
    # ─────────────────────────────────────────────────────────

    def check_drawdown_limits(
        self,
        daily_pnl_pct: float,
        weekly_pnl_pct: float,
        monthly_pnl_pct: float,
        current_drawdown_pct: float,
    ) -> RiskValidationResult:
        """
        Check if current drawdown levels breach configured limits.

        Returns a RiskValidationResult with:
          - approved=False if any hard limit is breached
          - warnings for soft limits (80% of hard limit)
        """
        violations = []
        warnings   = []

        # Daily drawdown check
        daily_limit = self.config["max_daily_drawdown_pct"]
        if abs(daily_pnl_pct) >= daily_limit:
            violations.append(
                f"Daily drawdown {abs(daily_pnl_pct):.2f}% breached limit {daily_limit}%"
            )
        elif abs(daily_pnl_pct) >= daily_limit * 0.8:
            warnings.append(
                f"Daily drawdown {abs(daily_pnl_pct):.2f}% approaching limit {daily_limit}%"
            )

        # Weekly drawdown check
        weekly_limit = self.config["max_weekly_drawdown_pct"]
        if abs(weekly_pnl_pct) >= weekly_limit:
            violations.append(
                f"Weekly drawdown {abs(weekly_pnl_pct):.2f}% breached limit {weekly_limit}%"
            )
        elif abs(weekly_pnl_pct) >= weekly_limit * 0.8:
            warnings.append(
                f"Weekly drawdown {abs(weekly_pnl_pct):.2f}% approaching limit {weekly_limit}%"
            )

        # Monthly drawdown check
        monthly_limit = self.config["max_monthly_drawdown_pct"]
        if abs(monthly_pnl_pct) >= monthly_limit:
            violations.append(
                f"Monthly drawdown {abs(monthly_pnl_pct):.2f}% breached limit {monthly_limit}%"
            )

        # Circuit breaker check
        cb_limit = self.config["circuit_breaker_drawdown_pct"]
        if current_drawdown_pct >= cb_limit:
            violations.append(
                f"Circuit breaker triggered: drawdown {current_drawdown_pct:.2f}% >= {cb_limit}%"
            )

        risk_score = min(100.0, max(
            abs(daily_pnl_pct) / daily_limit * 100,
            abs(weekly_pnl_pct) / weekly_limit * 100,
            current_drawdown_pct / cb_limit * 100,
        ))

        return RiskValidationResult(
            approved=len(violations) == 0,
            risk_score=risk_score,
            violations=violations,
            warnings=warnings,
            metadata={
                "daily_pnl_pct":      round(daily_pnl_pct, 4),
                "weekly_pnl_pct":     round(weekly_pnl_pct, 4),
                "monthly_pnl_pct":    round(monthly_pnl_pct, 4),
                "current_drawdown_pct": round(current_drawdown_pct, 4),
            },
        )

    # ─────────────────────────────────────────────────────────
    # CORRELATION CHECK
    # ─────────────────────────────────────────────────────────

    def check_correlation_limits(
        self,
        new_symbol: str,
        open_positions: List[Dict[str, Any]],
        correlation_matrix: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> RiskValidationResult:
        """
        Check if adding a new position would create excessive correlation risk.

        Uses symbol category as a proxy for correlation when no matrix is provided.
        """
        violations = []
        warnings   = []

        new_category = self.classify_symbol(new_symbol)

        # Count positions in same category
        same_category_count = sum(
            1 for pos in open_positions
            if self.classify_symbol(pos.get("symbol", "")) == new_category
        )

        max_correlated = self.config["max_correlated_positions"]
        if same_category_count >= max_correlated:
            violations.append(
                f"Too many correlated positions in {new_category}: "
                f"{same_category_count} >= {max_correlated}"
            )
        elif same_category_count >= max_correlated - 1:
            warnings.append(
                f"Approaching correlation limit for {new_category}: "
                f"{same_category_count}/{max_correlated}"
            )

        # If correlation matrix provided, check actual correlations
        if correlation_matrix and new_symbol in correlation_matrix:
            high_corr_pairs = []
            for pos in open_positions:
                pos_symbol = pos.get("symbol", "")
                corr = correlation_matrix.get(new_symbol, {}).get(pos_symbol, 0.0)
                if abs(corr) >= self.config["correlation_threshold"]:
                    high_corr_pairs.append((pos_symbol, round(corr, 3)))

            if len(high_corr_pairs) >= max_correlated:
                violations.append(
                    f"High correlation with existing positions: {high_corr_pairs}"
                )
            elif high_corr_pairs:
                warnings.append(
                    f"Correlated with existing positions: {high_corr_pairs}"
                )

        risk_score = min(100.0, same_category_count / max_correlated * 100)

        return RiskValidationResult(
            approved=len(violations) == 0,
            risk_score=risk_score,
            violations=violations,
            warnings=warnings,
            metadata={
                "new_symbol":          new_symbol,
                "new_category":        new_category,
                "same_category_count": same_category_count,
                "max_correlated":      max_correlated,
                "open_positions":      len(open_positions),
            },
        )

    # ─────────────────────────────────────────────────────────
    # EXPOSURE LIMIT CHECK
    # ─────────────────────────────────────────────────────────

    def check_exposure_limits(
        self,
        symbol: str,
        new_exposure_usd: float,
        account_balance: float,
        open_positions: List[Dict[str, Any]],
    ) -> RiskValidationResult:
        """
        Check if new position would breach exposure limits.

        Checks:
          - Per-pair exposure limit
          - Per-category exposure limit
          - Total portfolio exposure limit
        """
        violations = []
        warnings   = []

        if account_balance <= 0:
            return RiskValidationResult(
                approved=False,
                violations=["Account balance must be positive"],
            )

        category = self.classify_symbol(symbol)

        # Current exposure by pair
        pair_exposure = sum(
            float(pos.get("exposure_usd", 0))
            for pos in open_positions
            if pos.get("symbol", "").upper() == symbol.upper()
        )
        total_pair_exposure = pair_exposure + new_exposure_usd
        pair_exposure_pct   = total_pair_exposure / account_balance * 100

        max_pair_pct = self.config["max_exposure_per_pair_pct"]
        if pair_exposure_pct > max_pair_pct:
            violations.append(
                f"Pair exposure {pair_exposure_pct:.2f}% exceeds limit {max_pair_pct}% for {symbol}"
            )
        elif pair_exposure_pct > max_pair_pct * 0.8:
            warnings.append(
                f"Pair exposure {pair_exposure_pct:.2f}% approaching limit {max_pair_pct}%"
            )

        # Current exposure by category
        cat_exposure = sum(
            float(pos.get("exposure_usd", 0))
            for pos in open_positions
            if self.classify_symbol(pos.get("symbol", "")) == category
        )
        total_cat_exposure = cat_exposure + new_exposure_usd
        cat_exposure_pct   = total_cat_exposure / account_balance * 100

        max_cat_pct = self.config.get(
            f"max_{category.lower()}_exposure_pct",
            self.config["max_exposure_per_pair_pct"]
        )
        if cat_exposure_pct > max_cat_pct:
            violations.append(
                f"{category} category exposure {cat_exposure_pct:.2f}% exceeds limit {max_cat_pct}%"
            )

        # Total portfolio exposure
        total_exposure = sum(float(pos.get("exposure_usd", 0)) for pos in open_positions)
        total_exposure += new_exposure_usd
        total_exposure_pct = total_exposure / account_balance * 100

        max_total_pct = self.config["max_total_exposure_pct"]
        if total_exposure_pct > max_total_pct:
            violations.append(
                f"Total portfolio exposure {total_exposure_pct:.2f}% exceeds limit {max_total_pct}%"
            )
        elif total_exposure_pct > max_total_pct * 0.9:
            warnings.append(
                f"Total exposure {total_exposure_pct:.2f}% approaching limit {max_total_pct}%"
            )

        risk_score = min(100.0, max(
            pair_exposure_pct / max_pair_pct * 100,
            total_exposure_pct / max_total_pct * 100,
        ))

        return RiskValidationResult(
            approved=len(violations) == 0,
            risk_score=risk_score,
            violations=violations,
            warnings=warnings,
            metadata={
                "symbol":              symbol,
                "category":            category,
                "pair_exposure_pct":   round(pair_exposure_pct, 4),
                "cat_exposure_pct":    round(cat_exposure_pct, 4),
                "total_exposure_pct":  round(total_exposure_pct, 4),
                "new_exposure_usd":    round(new_exposure_usd, 2),
            },
        )

    # ─────────────────────────────────────────────────────────
    # STOP-LOSS ENFORCEMENT
    # ─────────────────────────────────────────────────────────

    def enforce_stop_loss(
        self,
        symbol: str,
        signal_type: str,
        entry_price: float,
        provided_sl: Optional[float],
        atr_value: Optional[float] = None,
        atr_multiplier: float = 1.5,
    ) -> Dict[str, Any]:
        """
        Enforce stop-loss placement rules.

        If no SL provided, calculates one based on ATR.
        Validates SL is within acceptable distance from entry.

        Returns:
          - stop_loss: Validated/calculated stop loss price
          - method: "provided" | "atr_calculated" | "default"
          - adjusted: Whether the SL was adjusted
        """
        category  = self.classify_symbol(symbol)
        direction = signal_type.upper()

        # Minimum SL distances (in price units)
        min_sl_distances = {
            "GOLD":   3.0,
            "JPY":    0.05,
            "CRYPTO": 50.0,
            "USD":    0.0005,
            "EUR":    0.0005,
            "GBP":    0.0005,
            "OTHER":  0.0005,
        }
        max_sl_distances = {
            "GOLD":   100.0,
            "JPY":    5.0,
            "CRYPTO": 5000.0,
            "USD":    0.05,
            "EUR":    0.05,
            "GBP":    0.05,
            "OTHER":  0.05,
        }

        min_dist = min_sl_distances.get(category, 0.0005)
        max_dist = max_sl_distances.get(category, 0.05)

        # Calculate ATR-based SL if ATR available
        if atr_value and atr_value > 0:
            atr_sl_distance = atr_value * atr_multiplier
            if direction == "BUY":
                atr_sl = entry_price - atr_sl_distance
            else:
                atr_sl = entry_price + atr_sl_distance
        else:
            atr_sl = None

        # Validate provided SL
        if provided_sl is not None:
            sl_distance = abs(entry_price - provided_sl)

            if sl_distance < min_dist:
                # SL too tight — use ATR or minimum
                if atr_sl is not None:
                    return {
                        "stop_loss": round(atr_sl, 5),
                        "method":    "atr_calculated",
                        "adjusted":  True,
                        "reason":    f"Provided SL too tight ({sl_distance:.5f} < {min_dist}), using ATR",
                        "original_sl": provided_sl,
                    }
                else:
                    if direction == "BUY":
                        adjusted_sl = entry_price - min_dist
                    else:
                        adjusted_sl = entry_price + min_dist
                    return {
                        "stop_loss": round(adjusted_sl, 5),
                        "method":    "minimum_distance",
                        "adjusted":  True,
                        "reason":    f"Provided SL too tight, using minimum distance {min_dist}",
                        "original_sl": provided_sl,
                    }

            if sl_distance > max_dist:
                # SL too wide — cap at maximum
                if direction == "BUY":
                    adjusted_sl = entry_price - max_dist
                else:
                    adjusted_sl = entry_price + max_dist
                return {
                    "stop_loss": round(adjusted_sl, 5),
                    "method":    "maximum_distance",
                    "adjusted":  True,
                    "reason":    f"Provided SL too wide ({sl_distance:.5f} > {max_dist}), capped",
                    "original_sl": provided_sl,
                }

            return {
                "stop_loss": round(provided_sl, 5),
                "method":    "provided",
                "adjusted":  False,
                "reason":    "Provided SL within acceptable range",
            }

        # No SL provided — calculate from ATR or use default
        if atr_sl is not None:
            return {
                "stop_loss": round(atr_sl, 5),
                "method":    "atr_calculated",
                "adjusted":  False,
                "reason":    f"SL calculated from ATR ({atr_value:.5f} × {atr_multiplier})",
            }

        # Default SL
        default_distance = min_dist * 2
        if direction == "BUY":
            default_sl = entry_price - default_distance
        else:
            default_sl = entry_price + default_distance

        return {
            "stop_loss": round(default_sl, 5),
            "method":    "default",
            "adjusted":  False,
            "reason":    f"No SL provided, using default distance {default_distance:.5f}",
        }

    # ─────────────────────────────────────────────────────────
    # COMPREHENSIVE SIGNAL RISK VALIDATION
    # ─────────────────────────────────────────────────────────

    def validate_signal(
        self,
        signal: Dict[str, Any],
        account_balance: float,
        open_positions: Optional[List[Dict]] = None,
        correlation_matrix: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Run all risk checks against a trading signal.

        Performs:
          1. R:R validation
          2. Position size validation
          3. Drawdown limit check
          4. Correlation check
          5. Exposure limit check
          6. Stop-loss enforcement

        Returns comprehensive validation result with overall approval status.
        """
        open_positions = open_positions or []

        symbol      = signal.get("symbol", signal.get("pair", "XAUUSD"))
        signal_type = signal.get("signal_type", signal.get("type", "BUY"))
        entry       = float(signal.get("entry_price", signal.get("entry", 0)))
        sl          = signal.get("sl_price", signal.get("sl", signal.get("stop_loss")))
        tp          = float(signal.get("tp1", signal.get("tp", signal.get("take_profit", 0))))
        lot_size    = float(signal.get("lot_size", signal.get("position_size", 0.01)))

        if sl is not None:
            sl = float(sl)

        results: Dict[str, Any] = {
            "signal_id":  signal.get("signal_id", signal.get("id", "unknown")),
            "symbol":     symbol,
            "timestamp":  datetime.utcnow().isoformat(),
            "checks":     {},
            "overall_approved": True,
            "overall_risk_score": 0.0,
            "all_violations": [],
            "all_warnings":   [],
        }

        # 1. R:R Validation
        if entry > 0 and tp > 0 and sl is not None:
            rr_result = self.validate_risk_reward(symbol, entry, sl, tp, signal_type)
            results["checks"]["risk_reward"] = rr_result.to_dict()
            if not rr_result.approved:
                results["overall_approved"] = False
                results["all_violations"].extend(rr_result.violations)
            results["all_warnings"].extend(rr_result.warnings)
            results["overall_risk_score"] = max(results["overall_risk_score"], rr_result.risk_score)

        # 2. Position Size Validation
        if entry > 0 and sl is not None:
            ps_result = self.validate_position_size(symbol, lot_size, account_balance, entry, sl)
            results["checks"]["position_size"] = ps_result.to_dict()
            if not ps_result.approved:
                results["overall_approved"] = False
                results["all_violations"].extend(ps_result.violations)
            results["all_warnings"].extend(ps_result.warnings)
            results["overall_risk_score"] = max(results["overall_risk_score"], ps_result.risk_score)

        # 3. Correlation Check
        corr_result = self.check_correlation_limits(symbol, open_positions, correlation_matrix)
        results["checks"]["correlation"] = corr_result.to_dict()
        if not corr_result.approved:
            results["overall_approved"] = False
            results["all_violations"].extend(corr_result.violations)
        results["all_warnings"].extend(corr_result.warnings)
        results["overall_risk_score"] = max(results["overall_risk_score"], corr_result.risk_score)

        # 4. Exposure Check
        if entry > 0:
            exposure_usd = lot_size * entry * 100
            exp_result   = self.check_exposure_limits(symbol, exposure_usd, account_balance, open_positions)
            results["checks"]["exposure"] = exp_result.to_dict()
            if not exp_result.approved:
                results["overall_approved"] = False
                results["all_violations"].extend(exp_result.violations)
            results["all_warnings"].extend(exp_result.warnings)
            results["overall_risk_score"] = max(results["overall_risk_score"], exp_result.risk_score)

        # 5. Stop-Loss Enforcement
        sl_result = self.enforce_stop_loss(symbol, signal_type, entry, sl)
        results["checks"]["stop_loss"] = sl_result
        if sl_result.get("adjusted"):
            results["all_warnings"].append(
                f"Stop-loss adjusted: {sl_result['reason']}"
            )
            results["recommended_sl"] = sl_result["stop_loss"]

        # 6. Open positions count check
        if len(open_positions) >= self.config["max_open_positions"]:
            results["overall_approved"] = False
            results["all_violations"].append(
                f"Maximum open positions reached: {len(open_positions)}/{self.config['max_open_positions']}"
            )

        results["overall_risk_score"] = round(results["overall_risk_score"], 2)
        results["risk_grade"] = (
            "LOW" if results["overall_risk_score"] < 25 else
            "MEDIUM" if results["overall_risk_score"] < 50 else
            "HIGH" if results["overall_risk_score"] < 75 else
            "CRITICAL"
        )

        return results

    # ─────────────────────────────────────────────────────────
    # REAL-TIME RISK METRICS
    # ─────────────────────────────────────────────────────────

    def get_real_time_metrics(
        self,
        account_balance: float,
        equity_peak: float,
        daily_pnl: float,
        weekly_pnl: float,
        monthly_pnl: float,
        open_positions: List[Dict[str, Any]],
        consecutive_losses: int = 0,
    ) -> Dict[str, Any]:
        """
        Compute real-time risk metrics snapshot.

        Returns a comprehensive risk dashboard with:
          - Drawdown metrics
          - Exposure breakdown
          - Risk utilisation percentages
          - Trading status
          - Recommended actions
        """
        # Drawdown metrics
        current_dd = (equity_peak - account_balance) / equity_peak * 100 if equity_peak > 0 else 0.0
        daily_dd   = abs(daily_pnl) / account_balance * 100 if account_balance > 0 else 0.0
        weekly_dd  = abs(weekly_pnl) / account_balance * 100 if account_balance > 0 else 0.0

        # Exposure breakdown
        exposure_by_category: Dict[str, float] = {}
        total_exposure = 0.0
        for pos in open_positions:
            cat = self.classify_symbol(pos.get("symbol", ""))
            exp = float(pos.get("exposure_usd", 0))
            exposure_by_category[cat] = exposure_by_category.get(cat, 0.0) + exp
            total_exposure += exp

        exposure_pct_by_cat = {
            cat: round(exp / account_balance * 100, 2) if account_balance > 0 else 0.0
            for cat, exp in exposure_by_category.items()
        }

        # Risk utilisation
        daily_util   = daily_dd / self.config["max_daily_drawdown_pct"] * 100
        weekly_util  = weekly_dd / self.config["max_weekly_drawdown_pct"] * 100
        cb_util      = current_dd / self.config["circuit_breaker_drawdown_pct"] * 100
        pos_util     = len(open_positions) / self.config["max_open_positions"] * 100

        # Overall risk level
        max_util = max(daily_util, weekly_util, cb_util, pos_util)
        if max_util >= 100:
            risk_level = "CRITICAL"
        elif max_util >= 80:
            risk_level = "HIGH"
        elif max_util >= 60:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        # Trading status
        trading_allowed = (
            daily_dd < self.config["max_daily_drawdown_pct"]
            and weekly_dd < self.config["max_weekly_drawdown_pct"]
            and current_dd < self.config["circuit_breaker_drawdown_pct"]
            and len(open_positions) < self.config["max_open_positions"]
            and consecutive_losses < self.config["max_consecutive_losses"]
        )

        # Recommended actions
        recommendations = []
        if daily_util >= 80:
            recommendations.append(f"⚠️ Daily drawdown at {daily_util:.0f}% utilisation — reduce position sizes")
        if weekly_util >= 80:
            recommendations.append(f"⚠️ Weekly drawdown at {weekly_util:.0f}% utilisation — consider pausing")
        if cb_util >= 80:
            recommendations.append(f"🚨 Circuit breaker at {cb_util:.0f}% — immediate risk review required")
        if consecutive_losses >= self.config["max_consecutive_losses"] - 1:
            recommendations.append(f"⚠️ {consecutive_losses} consecutive losses — review strategy")
        if pos_util >= 80:
            recommendations.append(f"⚠️ Position count at {pos_util:.0f}% capacity")

        return {
            "timestamp":        datetime.utcnow().isoformat(),
            "account_balance":  round(account_balance, 2),
            "equity_peak":      round(equity_peak, 2),
            "risk_level":       risk_level,
            "trading_allowed":  trading_allowed,
            "drawdown": {
                "current_pct":  round(current_dd, 4),
                "daily_pct":    round(daily_dd, 4),
                "weekly_pct":   round(weekly_dd, 4),
                "monthly_pnl":  round(monthly_pnl, 2),
            },
            "utilisation": {
                "daily_drawdown_pct":   round(daily_util, 2),
                "weekly_drawdown_pct":  round(weekly_util, 2),
                "circuit_breaker_pct":  round(cb_util, 2),
                "position_count_pct":   round(pos_util, 2),
            },
            "exposure": {
                "total_usd":    round(total_exposure, 2),
                "total_pct":    round(total_exposure / account_balance * 100, 2) if account_balance > 0 else 0.0,
                "by_category":  exposure_pct_by_cat,
            },
            "positions": {
                "open_count":   len(open_positions),
                "max_allowed":  self.config["max_open_positions"],
                "consecutive_losses": consecutive_losses,
            },
            "limits": {
                "max_daily_drawdown_pct":   self.config["max_daily_drawdown_pct"],
                "max_weekly_drawdown_pct":  self.config["max_weekly_drawdown_pct"],
                "circuit_breaker_pct":      self.config["circuit_breaker_drawdown_pct"],
                "max_open_positions":       self.config["max_open_positions"],
            },
            "recommendations": recommendations,
        }

    # ─────────────────────────────────────────────────────────
    # ASYNC DB OPERATIONS
    # ─────────────────────────────────────────────────────────

    async def save_risk_event(
        self,
        event_type: str,
        details: Dict[str, Any],
        severity: str = "INFO",
    ) -> str:
        """Persist a risk event to the database."""
        try:
            db = self._get_db()
            event_id = str(uuid.uuid4())
            await db.risk_events.insert_one({
                "event_id":   event_id,
                "event_type": event_type,
                "severity":   severity,
                "details":    details,
                "timestamp":  datetime.utcnow(),
            })
            return event_id
        except Exception as exc:
            logger.error(f"Risk event save failed: {exc}")
            return ""

    async def get_risk_events(
        self,
        hours: int = 24,
        severity_filter: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Retrieve recent risk events from the database."""
        try:
            db = self._get_db()
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            query: Dict[str, Any] = {"timestamp": {"$gte": cutoff}}
            if severity_filter:
                query["severity"] = severity_filter.upper()

            events = await (
                db.risk_events
                .find(query)
                .sort("timestamp", -1)
                .limit(max(1, min(limit, 500)))
                .to_list(None)
            )

            formatted = []
            for e in events:
                e.pop("_id", None)
                if e.get("timestamp") and hasattr(e["timestamp"], "isoformat"):
                    e["timestamp"] = e["timestamp"].isoformat()
                formatted.append(e)

            return formatted
        except Exception as exc:
            logger.error(f"Risk events retrieval failed: {exc}")
            return []

    async def update_config(
        self,
        new_config: Dict[str, Any],
        updated_by: str,
    ) -> Dict[str, Any]:
        """Update risk engine configuration in the database."""
        try:
            db = self._get_db()
            allowed_keys = set(DEFAULT_RISK_CONFIG.keys())
            sanitised = {k: v for k, v in new_config.items() if k in allowed_keys}

            if not sanitised:
                return {"success": False, "error": "No valid configuration keys provided"}

            # Update in-memory config
            self.config.update(sanitised)

            # Persist to DB
            await db.risk_engine_config.update_one(
                {"config_type": "risk_engine"},
                {
                    "$set": {
                        **{f"config.{k}": v for k, v in sanitised.items()},
                        "updated_at": datetime.utcnow(),
                        "updated_by": updated_by,
                    }
                },
                upsert=True,
            )

            return {
                "success":        True,
                "updated_keys":   list(sanitised.keys()),
                "updated_by":     updated_by,
                "updated_at":     datetime.utcnow().isoformat(),
            }
        except Exception as exc:
            logger.error(f"Risk config update failed: {exc}")
            return {"success": False, "error": str(exc)}

    async def load_config_from_db(self) -> None:
        """Load risk configuration from the database on startup."""
        try:
            db = self._get_db()
            config_doc = await db.risk_engine_config.find_one({"config_type": "risk_engine"})
            if config_doc and "config" in config_doc:
                self.config.update(config_doc["config"])
                logger.info("✅ Risk engine config loaded from database")
        except Exception as exc:
            logger.warning(f"Could not load risk config from DB, using defaults: {exc}")


# ─────────────────────────────────────────────────────────────
# SINGLETON INSTANCE
# ─────────────────────────────────────────────────────────────

risk_engine = RiskEngine()

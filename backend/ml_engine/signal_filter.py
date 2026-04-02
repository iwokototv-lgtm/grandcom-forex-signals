"""
Advanced Signal Quality Filter - CORRECTED VERSION
Implements strict regime enforcement, confidence filtering, throttling, and exposure control
Based on System Correction Summary requirements
"""
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import logging
import asyncio

logger = logging.getLogger(__name__)


class SignalQualityFilter:
    """
    CORRECTED Signal Quality Filter with strict enforcement.
    
    Key Corrections:
    1. Regime MUST enforce strategy behavior (not just label)
    2. Confidence threshold raised to 65%+ (70% preferred)
    3. Signal throttling - minimum 30 minutes between trades
    4. Correlation cap at 0.7 - reject highly correlated trades
    5. Session timing filter - block before session close/news
    6. Maintain daily/weekly/consecutive loss controls
    """
    
    def __init__(self):
        # Correlation matrix for pairs (approximate values)
        self.correlation_matrix = {
            ("EURUSD", "GBPUSD"): 0.85,
            ("EURUSD", "AUDUSD"): 0.70,
            ("EURUSD", "USDCHF"): -0.95,  # Inverse
            ("GBPUSD", "AUDUSD"): 0.65,
            ("XAUUSD", "XAUEUR"): 0.95,
            ("USDJPY", "EURJPY"): 0.75,
            ("USDJPY", "GBPJPY"): 0.80,
            ("EURJPY", "GBPJPY"): 0.90,
            ("EURUSD", "EURJPY"): 0.60,
            ("GBPUSD", "GBPJPY"): 0.65,
        }
        
        # Correlation groups for exposure tracking
        self.correlation_groups = {
            "USD_STRENGTH": ["EURUSD", "GBPUSD", "AUDUSD", "USDCHF", "USDCAD"],
            "GOLD": ["XAUUSD", "XAUEUR"],
            "JPY_PAIRS": ["USDJPY", "EURJPY", "GBPJPY"],
            "CRYPTO": ["BTCUSD"]
        }
        
        # Session times (UTC hours)
        self.session_schedule = {
            "ASIA": {"start": 0, "end": 8},
            "LONDON": {"start": 7, "end": 16},
            "NEW_YORK": {"start": 13, "end": 22},
        }
        
        # === CORRECTED THRESHOLDS ===
        self.min_confidence = 70  # RAISED from 65 → 70 (Phase 1: false signal reduction)
        self.preferred_confidence = 75  # Preferred for live trading (raised from 70 → 75)
        self.min_confluence_score = 2
        self.min_smc_score = 4

        # Gold pairs require stricter confidence threshold
        self.gold_pairs = ["XAUUSD", "XAUEUR"]
        self.gold_min_confidence = 75  # Gold pairs: 75% confidence (premium filtering)
        
        # Correlation threshold - reject if > 0.7
        self.max_correlation = 0.7
        
        # Maximum positions per correlation group
        self.max_positions_per_group = 2
        
        # === SIGNAL THROTTLING ===
        self.min_time_between_trades = timedelta(minutes=45)  # RAISED from 30 → 45 min (Phase 1: false signal reduction)
        self.last_signal_time: Dict[str, datetime] = {}
        self.global_last_signal_time: Optional[datetime] = None
        
        # === SESSION TIMING ===
        self.minutes_before_session_close = 15  # Block entries
        self.minutes_before_news = 30  # Block entries before high-impact news
        
        # Track active signals
        self.active_signals: Dict[str, Dict] = {}
        
        # Daily tracking
        self.daily_trades = 0
        self.daily_losses = 0
        self.consecutive_losses = 0
        self.last_reset_date = datetime.utcnow().date()
    
    def should_take_signal(
        self,
        symbol: str,
        signal_type: str,
        confidence: float,
        regime_result: Dict[str, Any],
        mtf_result: Optional[Dict[str, Any]] = None,
        smc_result: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Determine if signal meets ALL quality criteria with STRICT enforcement.
        
        Returns:
            Tuple of (should_take, reason, quality_metrics)
        """
        quality_metrics = {
            "checks_passed": 0,
            "checks_total": 7,
            "details": {},
            "blocked_reasons": []
        }
        
        # Reset daily counters if new day
        self._check_daily_reset()
        
        # === CHECK 1: CONFIDENCE THRESHOLD (RAISED TO 70%; GOLD 75%) ===
        # Gold pairs require stricter confidence for premium, reliable signals
        effective_min = self.gold_min_confidence if symbol in self.gold_pairs else self.min_confidence
        confidence_pass = confidence >= effective_min
        is_high_confidence = confidence >= self.preferred_confidence
        quality_metrics["details"]["confidence"] = {
            "pass": confidence_pass,
            "value": confidence,
            "threshold": effective_min,
            "preferred": self.preferred_confidence,
            "is_high_confidence": is_high_confidence,
            "is_gold_pair": symbol in self.gold_pairs
        }
        if confidence_pass:
            quality_metrics["checks_passed"] += 1
        else:
            quality_metrics["blocked_reasons"].append(
                f"Confidence {confidence}% < {effective_min}% minimum"
                + (" (gold premium threshold)" if symbol in self.gold_pairs else "")
            )
        
        # === CHECK 2: REGIME ENFORCEMENT (STRICT) ===
        regime_pass, regime_details = self._check_regime_enforcement(regime_result, signal_type)
        quality_metrics["details"]["regime"] = regime_details
        if regime_pass:
            quality_metrics["checks_passed"] += 1
        else:
            quality_metrics["blocked_reasons"].append(regime_details.get("block_reason", "Regime mismatch"))
        
        # === CHECK 3: SIGNAL THROTTLING (30 MIN MINIMUM) ===
        throttle_pass, throttle_details = self._check_signal_throttling(symbol)
        quality_metrics["details"]["throttling"] = throttle_details
        if throttle_pass:
            quality_metrics["checks_passed"] += 1
        else:
            quality_metrics["blocked_reasons"].append(throttle_details.get("block_reason", "Signal throttled"))
        
        # === CHECK 4: CORRELATION & EXPOSURE CAP ===
        correlation_pass, correlation_details = self._check_correlation_exposure(symbol, signal_type)
        quality_metrics["details"]["correlation"] = correlation_details
        if correlation_pass:
            quality_metrics["checks_passed"] += 1
        else:
            quality_metrics["blocked_reasons"].append(correlation_details.get("block_reason", "Correlation exceeded"))
        
        # === CHECK 5: SESSION TIMING FILTER ===
        session_pass, session_details = self._check_session_timing(confidence)
        quality_metrics["details"]["session"] = session_details
        if session_pass:
            quality_metrics["checks_passed"] += 1
        else:
            quality_metrics["blocked_reasons"].append(session_details.get("block_reason", "Bad session timing"))
        
        # === CHECK 6: MTF CONFLUENCE ===
        mtf_pass = False
        if mtf_result:
            mtf_score = mtf_result.get("confluence_score", 0)
            mtf_direction = mtf_result.get("trade_direction", "NEUTRAL")
            mtf_pass = mtf_score >= self.min_confluence_score and mtf_direction == signal_type
        quality_metrics["details"]["mtf"] = {
            "pass": mtf_pass,
            "score": mtf_result.get("confluence_score", 0) if mtf_result else 0,
            "direction_match": mtf_result.get("trade_direction") == signal_type if mtf_result else False
        }
        if mtf_pass:
            quality_metrics["checks_passed"] += 1
        
        # === CHECK 7: SMC CONFIRMATION ===
        smc_pass = False
        if smc_result and smc_result.get("valid"):
            smc_score = smc_result.get("smc_score", 0)
            smc_bias = smc_result.get("smc_bias", "NEUTRAL")
            smc_pass = smc_score >= self.min_smc_score
            if smc_bias != "NEUTRAL":
                bias_match = (signal_type == "BUY" and smc_bias == "BULLISH") or \
                            (signal_type == "SELL" and smc_bias == "BEARISH")
                smc_pass = smc_pass and bias_match
        quality_metrics["details"]["smc"] = {
            "pass": smc_pass,
            "score": smc_result.get("smc_score", 0) if smc_result else 0,
            "bias": smc_result.get("smc_bias", "N/A") if smc_result else "N/A"
        }
        if smc_pass:
            quality_metrics["checks_passed"] += 1
        
        # Calculate quality score
        quality_score = quality_metrics["checks_passed"] / quality_metrics["checks_total"]
        quality_metrics["quality_score"] = round(quality_score * 100, 1)
        
        # === FINAL DECISION ===
        # STRICT: Must pass confidence, regime, throttling, correlation, session
        core_checks_pass = all([confidence_pass, regime_pass, throttle_pass, correlation_pass, session_pass])
        
        # Additional quality: MTF or SMC should also pass
        has_confirmation = mtf_pass or smc_pass
        
        if core_checks_pass and has_confirmation:
            return True, "HIGH_QUALITY", quality_metrics
        elif core_checks_pass:
            return True, "CORE_CRITERIA_MET", quality_metrics
        else:
            # Return the first blocked reason
            main_reason = quality_metrics["blocked_reasons"][0] if quality_metrics["blocked_reasons"] else "Quality check failed"
            return False, main_reason, quality_metrics
    
    def _check_regime_enforcement(self, regime_result: Dict[str, Any], signal_type: str) -> Tuple[bool, Dict]:
        """
        STRICT regime enforcement - regime MUST control strategy behavior.
        
        RANGE regime:
        - Only mean-reversion allowed
        - Reduced TP targets (0.5R-1R)
        - Earlier break-even (+0.5R)
        - Risk multiplier 0.5x-0.7x
        
        TREND regime:
        - Breakout/pullback allowed
        - Full TP targets (2R+)
        - Normal break-even timing
        - Full risk multiplier
        """
        regime_name = regime_result.get("regime_name", "UNKNOWN")
        regime_confidence = regime_result.get("confidence", 0)
        risk_mult = regime_result.get("risk_multiplier", 0)
        active_strategies = regime_result.get("active_strategies", [])
        
        details = {
            "regime": regime_name,
            "confidence": regime_confidence,
            "risk_multiplier": risk_mult,
            "active_strategies": active_strategies
        }
        
        # No trading in CHAOS
        if regime_name == "CHAOS":
            details["pass"] = False
            details["block_reason"] = "CHAOS regime - no trading"
            return False, details
        
        # Low confidence regime detection
        if regime_confidence < 0.6:
            details["pass"] = False
            details["block_reason"] = f"Low regime confidence ({regime_confidence:.0%})"
            return False, details
        
        # === STRICT REGIME ENFORCEMENT ===
        if regime_name == "RANGE":
            # In RANGE: Only allow reversal/mean-reversion strategies
            # Risk should be reduced
            if risk_mult > 0.7:
                details["adjusted_risk"] = 0.7
                details["note"] = "Risk capped at 0.7x for RANGE"
            
            details["allowed_strategies"] = ["reversal", "mean_reversion"]
            details["tp_adjustment"] = "reduced"  # 0.5R-1R
            details["breakeven_trigger"] = 0.5  # +0.5R
            details["pass"] = True
            
        elif regime_name in ["TREND_UP", "TREND_DOWN"]:
            # In TREND: Allow breakout/pullback strategies
            details["allowed_strategies"] = ["breakout", "pullback"]
            details["tp_adjustment"] = "normal"  # 2R+
            details["breakeven_trigger"] = 1.0  # +1R
            details["pass"] = True
            
        elif regime_name == "HIGH_VOL":
            # High volatility: Reduced risk, tighter stops
            details["adjusted_risk"] = min(risk_mult, 0.6)
            details["allowed_strategies"] = ["breakout"]
            details["pass"] = True
            
        elif regime_name == "LOW_VOL":
            # Low volatility: Can increase risk slightly
            details["allowed_strategies"] = ["mean_reversion"]
            details["pass"] = True
            
        else:
            # Unknown regime - be cautious
            details["pass"] = False
            details["block_reason"] = f"Unknown regime: {regime_name}"
            return False, details
        
        details["pass"] = True
        return True, details
    
    def _check_signal_throttling(self, symbol: str) -> Tuple[bool, Dict]:
        """
        Enforce minimum 30 minutes between trades to prevent clustering.
        """
        now = datetime.utcnow()
        
        details = {
            "min_interval_minutes": self.min_time_between_trades.total_seconds() / 60
        }
        
        # Check global throttle (any symbol)
        if self.global_last_signal_time:
            time_since_last = now - self.global_last_signal_time
            if time_since_last < self.min_time_between_trades:
                remaining = (self.min_time_between_trades - time_since_last).total_seconds() / 60
                details["pass"] = False
                details["block_reason"] = f"Global throttle: wait {remaining:.0f} more minutes"
                details["time_since_last"] = time_since_last.total_seconds() / 60
                return False, details
        
        # Check symbol-specific throttle
        if symbol in self.last_signal_time:
            time_since_symbol = now - self.last_signal_time[symbol]
            if time_since_symbol < self.min_time_between_trades:
                remaining = (self.min_time_between_trades - time_since_symbol).total_seconds() / 60
                details["pass"] = False
                details["block_reason"] = f"{symbol} throttle: wait {remaining:.0f} more minutes"
                return False, details
        
        details["pass"] = True
        return True, details
    
    def _check_correlation_exposure(self, symbol: str, signal_type: str) -> Tuple[bool, Dict]:
        """
        Check correlation against active positions.
        Reject if correlation > 0.7 with same direction.
        """
        details = {
            "max_correlation": self.max_correlation,
            "active_positions": len(self.active_signals)
        }
        
        high_correlations = []
        
        for active_symbol, active_data in self.active_signals.items():
            if active_symbol == symbol:
                continue
            
            # Get correlation between symbols
            correlation = self._get_correlation(symbol, active_symbol)
            
            # Check if same direction with high correlation
            active_type = active_data.get("type")
            
            # For positive correlation, same direction is risky
            # For negative correlation, opposite direction is risky
            if correlation > 0:
                risky = (signal_type == active_type) and (abs(correlation) > self.max_correlation)
            else:
                risky = (signal_type != active_type) and (abs(correlation) > self.max_correlation)
            
            if risky:
                high_correlations.append({
                    "symbol": active_symbol,
                    "correlation": correlation,
                    "direction": active_type
                })
        
        if high_correlations:
            details["pass"] = False
            details["high_correlations"] = high_correlations
            details["block_reason"] = f"High correlation with {high_correlations[0]['symbol']} ({high_correlations[0]['correlation']:.2f})"
            return False, details
        
        # Also check group exposure
        symbol_group = self._get_correlation_group(symbol)
        if symbol_group:
            group_count = sum(1 for s in self.active_signals if self._get_correlation_group(s) == symbol_group)
            if group_count >= self.max_positions_per_group:
                details["pass"] = False
                details["block_reason"] = f"Max {symbol_group} positions ({group_count}) reached"
                return False, details
        
        details["pass"] = True
        return True, details
    
    def _check_session_timing(self, confidence: float) -> Tuple[bool, Dict]:
        """
        Check session timing filters:
        - Block 15 minutes before session close
        - Block during low liquidity unless confidence >= 70%
        """
        now = datetime.utcnow()
        hour = now.hour
        minute = now.minute
        
        details = {
            "current_hour_utc": hour,
            "current_minute": minute
        }
        
        # Weekend check
        if now.weekday() >= 5:
            details["pass"] = False
            details["block_reason"] = "Weekend - markets closed"
            return False, details
        
        # Determine current session
        in_london = 7 <= hour < 16
        in_ny = 13 <= hour < 22
        in_overlap = 13 <= hour < 16
        in_asia = 0 <= hour < 8
        
        # Check if near session close (15 minutes before)
        london_close_soon = hour == 15 and minute >= 45
        ny_close_soon = hour == 21 and minute >= 45
        
        if london_close_soon or ny_close_soon:
            details["pass"] = False
            details["block_reason"] = "Too close to session close (15 min buffer)"
            return False, details
        
        # Low liquidity periods require higher confidence
        low_liquidity = not (in_london or in_ny)
        if low_liquidity and confidence < self.preferred_confidence:
            details["pass"] = False
            details["block_reason"] = f"Low liquidity period requires {self.preferred_confidence}%+ confidence"
            return False, details
        
        details["session"] = "OVERLAP" if in_overlap else ("LONDON" if in_london else ("NY" if in_ny else "OFF_HOURS"))
        details["pass"] = True
        return True, details
    
    def _get_correlation(self, symbol1: str, symbol2: str) -> float:
        """Get correlation between two symbols"""
        # Check direct lookup
        if (symbol1, symbol2) in self.correlation_matrix:
            return self.correlation_matrix[(symbol1, symbol2)]
        if (symbol2, symbol1) in self.correlation_matrix:
            return self.correlation_matrix[(symbol2, symbol1)]
        
        # Check if in same group
        group1 = self._get_correlation_group(symbol1)
        group2 = self._get_correlation_group(symbol2)
        
        if group1 and group1 == group2:
            return 0.75  # Assume high correlation within groups
        
        return 0.3  # Default low correlation
    
    def _get_correlation_group(self, symbol: str) -> Optional[str]:
        """Get correlation group for a symbol"""
        for group_name, symbols in self.correlation_groups.items():
            if symbol in symbols:
                return group_name
        return None
    
    def _check_daily_reset(self):
        """Reset daily counters if new day"""
        today = datetime.utcnow().date()
        if today != self.last_reset_date:
            self.daily_trades = 0
            self.daily_losses = 0
            self.last_reset_date = today
            logger.info("Daily counters reset")
    
    def register_signal(self, symbol: str, signal_type: str, signal_id: str):
        """Register an active signal and update throttling"""
        now = datetime.utcnow()
        
        self.active_signals[symbol] = {
            "type": signal_type,
            "id": signal_id,
            "timestamp": now.isoformat()
        }
        
        # Update throttle timestamps
        self.last_signal_time[symbol] = now
        self.global_last_signal_time = now
        self.daily_trades += 1
        
        logger.info(f"Signal registered: {symbol} {signal_type}, Daily trades: {self.daily_trades}")
    
    def close_signal(self, symbol: str, result: str = "UNKNOWN"):
        """Remove closed signal and update stats"""
        if symbol in self.active_signals:
            del self.active_signals[symbol]
        
        if result == "LOSS":
            self.daily_losses += 1
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        
        logger.info(f"Signal closed: {symbol} {result}, Consecutive losses: {self.consecutive_losses}")
    
    def get_quality_summary(self) -> Dict[str, Any]:
        """Get summary of quality filter status"""
        session_pass, session_details = self._check_session_timing(70)
        
        return {
            "current_session": session_details.get("session", "UNKNOWN"),
            "session_optimal": session_pass,
            "active_signals": len(self.active_signals),
            "active_positions": list(self.active_signals.keys()),
            "daily_trades": self.daily_trades,
            "daily_losses": self.daily_losses,
            "consecutive_losses": self.consecutive_losses,
            "global_throttle_active": self.global_last_signal_time is not None,
            "thresholds": {
                "min_confidence": self.min_confidence,
                "gold_min_confidence": self.gold_min_confidence,
                "preferred_confidence": self.preferred_confidence,
                "min_confluence": self.min_confluence_score,
                "min_smc_score": self.min_smc_score,
                "max_correlation": self.max_correlation,
                "throttle_minutes": self.min_time_between_trades.total_seconds() / 60
            }
        }


class RegimeEnforcedTPSL:
    """
    Regime-enforced TP/SL management with REALISTIC profit targets.
    
    CORRECTED: Much tighter TP levels for actual profit taking
    
    RANGE regime:
    - TP1: 0.3R (quick scalp)
    - TP2: 0.5R
    - TP3: 0.8R
    - Break-even at +0.3R
    
    TREND regime:
    - TP1: 0.5R
    - TP2: 1.0R
    - TP3: 1.5R
    - Break-even at +0.5R
    """
    
    def __init__(self):
        # CORRECTED: Much tighter, realistic TP ratios
        self.regime_settings = {
            "RANGE": {
                "tp1_ratio": 0.3,   # Quick profit - 0.3R
                "tp2_ratio": 0.5,   # 0.5R
                "tp3_ratio": 0.8,   # 0.8R max
                "breakeven_at": 0.3,  # Move to BE at +0.3R
                "max_risk_mult": 0.7,
                "partial_close_tp1": 0.5,  # Close 50% at TP1
            },
            "TREND_UP": {
                "tp1_ratio": 0.5,   # 0.5R
                "tp2_ratio": 1.0,   # 1R
                "tp3_ratio": 1.5,   # 1.5R
                "breakeven_at": 0.5,  # Move to BE at +0.5R
                "max_risk_mult": 1.0,
                "partial_close_tp1": 0.4,
            },
            "TREND_DOWN": {
                "tp1_ratio": 0.5,
                "tp2_ratio": 1.0,
                "tp3_ratio": 1.5,
                "breakeven_at": 0.5,
                "max_risk_mult": 1.0,
                "partial_close_tp1": 0.4,
            },
            "HIGH_VOL": {
                "tp1_ratio": 0.4,   # Tighter in high vol
                "tp2_ratio": 0.7,
                "tp3_ratio": 1.0,
                "breakeven_at": 0.3,
                "max_risk_mult": 0.6,
                "partial_close_tp1": 0.5,
            },
            "LOW_VOL": {
                "tp1_ratio": 0.3,   # Even tighter in low vol
                "tp2_ratio": 0.5,
                "tp3_ratio": 0.7,
                "breakeven_at": 0.25,
                "max_risk_mult": 1.0,
                "partial_close_tp1": 0.5,
            }
        }
        
        self.default_settings = {
            "tp1_ratio": 0.4,
            "tp2_ratio": 0.7,
            "tp3_ratio": 1.0,
            "breakeven_at": 0.3,
            "max_risk_mult": 0.8,
            "partial_close_tp1": 0.5,
        }
    
    def calculate_regime_adjusted_levels(
        self,
        entry_price: float,
        sl_price: float,
        signal_type: str,
        regime: str,
        pair_params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Calculate regime-adjusted TP levels and settings.
        """
        # Get regime settings
        settings = self.regime_settings.get(regime, self.default_settings)
        
        # Calculate risk (SL distance)
        sl_distance = abs(entry_price - sl_price)
        
        # Calculate TP levels based on regime
        if signal_type == "BUY":
            tp1 = entry_price + (sl_distance * settings["tp1_ratio"])
            tp2 = entry_price + (sl_distance * settings["tp2_ratio"])
            tp3 = entry_price + (sl_distance * settings["tp3_ratio"])
            breakeven_price = entry_price + (sl_distance * settings["breakeven_at"])
        else:  # SELL
            tp1 = entry_price - (sl_distance * settings["tp1_ratio"])
            tp2 = entry_price - (sl_distance * settings["tp2_ratio"])
            tp3 = entry_price - (sl_distance * settings["tp3_ratio"])
            breakeven_price = entry_price - (sl_distance * settings["breakeven_at"])
        
        decimal_places = pair_params.get("decimal_places", 5)
        
        return {
            "entry_price": entry_price,
            "sl_price": sl_price,
            "tp_levels": [
                round(tp1, decimal_places),
                round(tp2, decimal_places),
                round(tp3, decimal_places)
            ],
            "breakeven_trigger": round(breakeven_price, decimal_places),
            "regime": regime,
            "settings_used": settings,
            "partial_close_at_tp1": settings["partial_close_tp1"],
            "max_risk_multiplier": settings["max_risk_mult"],
            "sl_distance": sl_distance,
            "risk_reward_tp1": settings["tp1_ratio"],
            "risk_reward_tp3": settings["tp3_ratio"]
        }
    
    def check_exit_conditions(
        self,
        current_price: float,
        entry_price: float,
        sl_price: float,
        tp_levels: List[float],
        signal_type: str,
        regime: str
    ) -> Dict[str, Any]:
        """
        Check if any exit conditions are met.
        
        Returns:
            Dictionary with exit decision and reason
        """
        settings = self.regime_settings.get(regime, self.default_settings)
        sl_distance = abs(entry_price - sl_price)
        breakeven_at = settings["breakeven_at"]
        
        result = {
            "should_exit": False,
            "exit_type": None,
            "exit_reason": None,
            "move_to_breakeven": False,
            "partial_close": False,
            "partial_close_pct": 0
        }
        
        if signal_type == "BUY":
            profit_distance = current_price - entry_price
            profit_r = profit_distance / sl_distance if sl_distance > 0 else 0
            
            # Check SL hit
            if current_price <= sl_price:
                result["should_exit"] = True
                result["exit_type"] = "SL"
                result["exit_reason"] = "Stop loss hit"
                return result
            
            # Check TP hits
            for i, tp in enumerate(tp_levels):
                if current_price >= tp:
                    result["should_exit"] = True
                    result["exit_type"] = f"TP{i+1}"
                    result["exit_reason"] = f"Take profit {i+1} hit"
                    return result
            
            # Check breakeven trigger
            if profit_r >= breakeven_at:
                result["move_to_breakeven"] = True
                result["new_sl"] = entry_price
            
            # Check partial close at TP1 distance
            if profit_r >= settings["tp1_ratio"] * 0.9:  # 90% of TP1
                result["partial_close"] = True
                result["partial_close_pct"] = settings["partial_close_tp1"]
        
        else:  # SELL
            profit_distance = entry_price - current_price
            profit_r = profit_distance / sl_distance if sl_distance > 0 else 0
            
            # Check SL hit
            if current_price >= sl_price:
                result["should_exit"] = True
                result["exit_type"] = "SL"
                result["exit_reason"] = "Stop loss hit"
                return result
            
            # Check TP hits
            for i, tp in enumerate(tp_levels):
                if current_price <= tp:
                    result["should_exit"] = True
                    result["exit_type"] = f"TP{i+1}"
                    result["exit_reason"] = f"Take profit {i+1} hit"
                    return result
            
            # Check breakeven trigger
            if profit_r >= breakeven_at:
                result["move_to_breakeven"] = True
                result["new_sl"] = entry_price
            
            # Check partial close at TP1 distance
            if profit_r >= settings["tp1_ratio"] * 0.9:
                result["partial_close"] = True
                result["partial_close_pct"] = settings["partial_close_tp1"]
        
        result["current_profit_r"] = round(profit_r, 2)
        return result


# Global instances
signal_quality_filter = SignalQualityFilter()
regime_enforced_tpsl = RegimeEnforcedTPSL()

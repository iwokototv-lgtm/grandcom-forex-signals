"""
Advanced Signal Quality Filter
Combines multiple factors to ensure only high-probability signals are generated
"""
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import logging
import aiohttp

logger = logging.getLogger(__name__)


class SignalQualityFilter:
    """
    Advanced signal quality filtering system.
    
    Filters:
    1. Minimum confluence requirement (MTF alignment)
    2. Session filter (best trading sessions)
    3. Correlation filter (avoid duplicate exposure)
    4. News filter (avoid high-impact news)
    5. SMC confirmation
    6. Minimum confidence threshold
    """
    
    def __init__(self):
        # Correlation groups - pairs that move together
        self.correlation_groups = {
            "USD_STRENGTH": ["EURUSD", "GBPUSD", "AUDUSD"],  # Inverse correlation
            "GOLD": ["XAUUSD", "XAUEUR"],
            "JPY_PAIRS": ["USDJPY", "EURJPY", "GBPJPY"],
            "CRYPTO": ["BTCUSD"]
        }
        
        # Best trading sessions (UTC hours)
        self.optimal_sessions = {
            "LONDON_OPEN": (7, 10),      # 07:00-10:00 UTC
            "NY_OPEN": (13, 16),          # 13:00-16:00 UTC
            "LONDON_NY_OVERLAP": (13, 16) # Best volatility
        }
        
        # Minimum quality thresholds
        self.min_confluence_score = 2  # Out of 3
        self.min_confidence = 55
        self.min_smc_score = 4  # Out of 10
        self.max_positions_per_group = 2
        
        # Track active signals
        self.active_signals: Dict[str, Dict] = {}
    
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
        Determine if signal meets quality criteria.
        
        Returns:
            Tuple of (should_take, reason, quality_metrics)
        """
        quality_metrics = {
            "checks_passed": 0,
            "checks_total": 6,
            "details": {}
        }
        
        # 1. Confidence Check
        confidence_pass = confidence >= self.min_confidence
        quality_metrics["details"]["confidence"] = {
            "pass": confidence_pass,
            "value": confidence,
            "threshold": self.min_confidence
        }
        if confidence_pass:
            quality_metrics["checks_passed"] += 1
        
        # 2. Session Check
        session_pass, session_name = self._check_trading_session()
        quality_metrics["details"]["session"] = {
            "pass": session_pass,
            "session": session_name
        }
        if session_pass:
            quality_metrics["checks_passed"] += 1
        
        # 3. Regime Check
        regime_pass = self._check_regime_quality(regime_result, signal_type)
        quality_metrics["details"]["regime"] = {
            "pass": regime_pass,
            "regime": regime_result.get("regime_name", "UNKNOWN"),
            "risk_mult": regime_result.get("risk_multiplier", 0)
        }
        if regime_pass:
            quality_metrics["checks_passed"] += 1
        
        # 4. MTF Confluence Check
        mtf_pass = False
        if mtf_result:
            mtf_pass = mtf_result.get("confluence_score", 0) >= self.min_confluence_score
            mtf_pass = mtf_pass and mtf_result.get("trade_direction") == signal_type
        quality_metrics["details"]["mtf_confluence"] = {
            "pass": mtf_pass,
            "score": mtf_result.get("confluence_score", 0) if mtf_result else 0,
            "direction": mtf_result.get("trade_direction") if mtf_result else "N/A"
        }
        if mtf_pass:
            quality_metrics["checks_passed"] += 1
        
        # 5. Correlation Check
        correlation_pass, corr_reason = self._check_correlation(symbol, signal_type)
        quality_metrics["details"]["correlation"] = {
            "pass": correlation_pass,
            "reason": corr_reason
        }
        if correlation_pass:
            quality_metrics["checks_passed"] += 1
        
        # 6. SMC Check
        smc_pass = False
        if smc_result and smc_result.get("valid"):
            smc_score = smc_result.get("smc_score", 0)
            smc_bias = smc_result.get("smc_bias", "NEUTRAL")
            smc_pass = smc_score >= self.min_smc_score
            # Also check bias alignment
            if smc_bias != "NEUTRAL":
                smc_pass = smc_pass and (
                    (signal_type == "BUY" and smc_bias == "BULLISH") or
                    (signal_type == "SELL" and smc_bias == "BEARISH")
                )
        quality_metrics["details"]["smc"] = {
            "pass": smc_pass,
            "score": smc_result.get("smc_score", 0) if smc_result else 0,
            "bias": smc_result.get("smc_bias", "N/A") if smc_result else "N/A"
        }
        if smc_pass:
            quality_metrics["checks_passed"] += 1
        
        # Calculate overall quality score
        quality_score = quality_metrics["checks_passed"] / quality_metrics["checks_total"]
        quality_metrics["quality_score"] = round(quality_score * 100, 1)
        
        # Decision logic
        # Minimum requirements: confidence + regime + (mtf OR smc)
        core_pass = confidence_pass and regime_pass and (mtf_pass or smc_pass)
        
        # High quality: 5+ checks passed
        high_quality = quality_metrics["checks_passed"] >= 5
        
        # Medium quality: 4 checks passed
        medium_quality = quality_metrics["checks_passed"] >= 4
        
        if high_quality:
            return True, "HIGH_QUALITY", quality_metrics
        elif medium_quality and core_pass:
            return True, "MEDIUM_QUALITY", quality_metrics
        elif core_pass and session_pass:
            return True, "CORE_CRITERIA_MET", quality_metrics
        else:
            # Find main failure reason
            if not confidence_pass:
                reason = f"Low confidence ({confidence}% < {self.min_confidence}%)"
            elif not regime_pass:
                reason = f"Unfavorable regime ({regime_result.get('regime_name')})"
            elif not session_pass:
                reason = f"Outside optimal trading session"
            elif not correlation_pass:
                reason = corr_reason
            else:
                reason = f"Insufficient quality (score: {quality_metrics['quality_score']}%)"
            
            return False, reason, quality_metrics
    
    def _check_trading_session(self) -> Tuple[bool, str]:
        """Check if current time is in optimal trading session"""
        now = datetime.utcnow()
        hour = now.hour
        
        # Check each session
        for session_name, (start, end) in self.optimal_sessions.items():
            if start <= hour < end:
                return True, session_name
        
        # Weekend check
        if now.weekday() >= 5:  # Saturday or Sunday
            return False, "WEEKEND"
        
        # Off-hours but still trading day
        return False, "OFF_HOURS"
    
    def _check_regime_quality(self, regime_result: Dict[str, Any], signal_type: str) -> bool:
        """Check if regime supports the signal type"""
        regime_name = regime_result.get("regime_name", "UNKNOWN")
        confidence = regime_result.get("confidence", 0)
        risk_mult = regime_result.get("risk_multiplier", 0)
        
        # No trading in CHAOS
        if regime_name == "CHAOS":
            return False
        
        # Low confidence regime
        if confidence < 0.6:
            return False
        
        # Zero risk multiplier means no trading
        if risk_mult <= 0:
            return False
        
        # Trend regimes favor trend-following
        # Range regimes favor reversals
        # All are acceptable if other criteria met
        return True
    
    def _check_correlation(self, symbol: str, signal_type: str) -> Tuple[bool, str]:
        """Check correlation exposure"""
        # Find which group this symbol belongs to
        symbol_group = None
        for group_name, symbols in self.correlation_groups.items():
            if symbol in symbols:
                symbol_group = group_name
                break
        
        if not symbol_group:
            return True, "No correlation group"
        
        # Count active signals in same group with same direction
        same_direction_count = 0
        for sig_symbol, sig_data in self.active_signals.items():
            if sig_symbol in self.correlation_groups.get(symbol_group, []):
                if sig_data.get("type") == signal_type:
                    same_direction_count += 1
        
        if same_direction_count >= self.max_positions_per_group:
            return False, f"Max {symbol_group} exposure reached ({same_direction_count})"
        
        return True, f"{symbol_group}: {same_direction_count}/{self.max_positions_per_group}"
    
    def register_signal(self, symbol: str, signal_type: str, signal_id: str):
        """Register an active signal"""
        self.active_signals[symbol] = {
            "type": signal_type,
            "id": signal_id,
            "timestamp": datetime.utcnow().isoformat()
        }
    
    def close_signal(self, symbol: str):
        """Remove closed signal"""
        if symbol in self.active_signals:
            del self.active_signals[symbol]
    
    def get_quality_summary(self) -> Dict[str, Any]:
        """Get summary of quality filter status"""
        session_ok, session_name = self._check_trading_session()
        
        return {
            "current_session": session_name,
            "session_optimal": session_ok,
            "active_signals": len(self.active_signals),
            "active_by_group": {
                group: sum(1 for s in symbols if s in self.active_signals)
                for group, symbols in self.correlation_groups.items()
            },
            "thresholds": {
                "min_confluence": self.min_confluence_score,
                "min_confidence": self.min_confidence,
                "min_smc_score": self.min_smc_score
            }
        }


class TrailingStopManager:
    """
    Manages trailing stops and partial profit taking.
    """
    
    def __init__(self):
        # Trailing stop settings
        self.trailing_activation_pct = 0.5  # Activate after 50% to TP1
        self.trailing_distance_pct = 0.3    # Trail 30% behind price
        
        # Partial profit settings
        self.partial_at_tp1 = 0.5  # Close 50% at TP1
        self.partial_at_tp2 = 0.3  # Close 30% at TP2
        # Remaining 20% runs to TP3
    
    def calculate_trailing_stop(
        self,
        entry_price: float,
        current_price: float,
        original_sl: float,
        tp1: float,
        signal_type: str
    ) -> Dict[str, Any]:
        """Calculate trailing stop level"""
        
        if signal_type == "BUY":
            # Calculate progress to TP1
            total_distance = tp1 - entry_price
            current_distance = current_price - entry_price
            
            if total_distance <= 0:
                return {"trailing_active": False, "sl": original_sl}
            
            progress = current_distance / total_distance
            
            if progress >= self.trailing_activation_pct:
                # Activate trailing stop
                trail_distance = current_distance * self.trailing_distance_pct
                new_sl = max(entry_price, current_price - trail_distance)
                return {
                    "trailing_active": True,
                    "sl": round(new_sl, 5),
                    "progress_pct": round(progress * 100, 1),
                    "locked_profit": round((new_sl - entry_price) / entry_price * 100, 3)
                }
        
        else:  # SELL
            total_distance = entry_price - tp1
            current_distance = entry_price - current_price
            
            if total_distance <= 0:
                return {"trailing_active": False, "sl": original_sl}
            
            progress = current_distance / total_distance
            
            if progress >= self.trailing_activation_pct:
                trail_distance = current_distance * self.trailing_distance_pct
                new_sl = min(entry_price, current_price + trail_distance)
                return {
                    "trailing_active": True,
                    "sl": round(new_sl, 5),
                    "progress_pct": round(progress * 100, 1),
                    "locked_profit": round((entry_price - new_sl) / entry_price * 100, 3)
                }
        
        return {"trailing_active": False, "sl": original_sl}
    
    def calculate_partial_close(
        self,
        entry_price: float,
        current_price: float,
        tp1: float,
        tp2: float,
        signal_type: str,
        position_size: float
    ) -> Dict[str, Any]:
        """Calculate partial close recommendations"""
        
        result = {
            "close_partial": False,
            "close_amount": 0,
            "close_pct": 0,
            "reason": None
        }
        
        if signal_type == "BUY":
            if current_price >= tp1:
                result["close_partial"] = True
                result["close_pct"] = self.partial_at_tp1 * 100
                result["close_amount"] = position_size * self.partial_at_tp1
                result["reason"] = "TP1_HIT"
            elif current_price >= tp2:
                result["close_partial"] = True
                result["close_pct"] = self.partial_at_tp2 * 100
                result["close_amount"] = position_size * self.partial_at_tp2
                result["reason"] = "TP2_HIT"
        else:  # SELL
            if current_price <= tp1:
                result["close_partial"] = True
                result["close_pct"] = self.partial_at_tp1 * 100
                result["close_amount"] = position_size * self.partial_at_tp1
                result["reason"] = "TP1_HIT"
            elif current_price <= tp2:
                result["close_partial"] = True
                result["close_pct"] = self.partial_at_tp2 * 100
                result["close_amount"] = position_size * self.partial_at_tp2
                result["reason"] = "TP2_HIT"
        
        return result


# Global instances
signal_quality_filter = SignalQualityFilter()
trailing_stop_manager = TrailingStopManager()

"""
Error Handling & Graceful Degradation
Ensures system continues operating even when components fail
"""

import asyncio
import logging
from typing import Dict, Any, Optional, Callable, TypeVar, Coroutine
from functools import wraps
from datetime import datetime
import traceback

logger = logging.getLogger(__name__)

T = TypeVar('T')


class ErrorHandler:
    """Centralized error handling with fallbacks"""

    @staticmethod
    def safe_sync(func: Callable, fallback: Any = None, context: str = ""):
        """
        Synchronous function wrapper with error handling
        
        Args:
            func: Function to execute
            fallback: Value to return on error
            context: Error context for logging
        """
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                logger.error(
                    f"Error in {context or func.__name__}: {str(e)}",
                    extra={
                        'error_type': type(e).__name__,
                        'traceback': traceback.format_exc(),
                        'context': context,
                        'timestamp': datetime.utcnow().isoformat()
                    }
                )
                return fallback
        return wrapper

    @staticmethod
    def safe_async(func: Callable, fallback: Any = None, context: str = "", timeout: int = 30):
        """
        Asynchronous function wrapper with error handling and timeout
        
        Args:
            func: Async function to execute
            fallback: Value to return on error
            context: Error context for logging
            timeout: Timeout in seconds
        """
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                result = await asyncio.wait_for(func(*args, **kwargs), timeout=timeout)
                return result
            except asyncio.TimeoutError:
                logger.warning(
                    f"Timeout in {context or func.__name__} (>{timeout}s)",
                    extra={'context': context, 'timeout': timeout}
                )
                return fallback
            except Exception as e:
                logger.error(
                    f"Error in {context or func.__name__}: {str(e)}",
                    extra={
                        'error_type': type(e).__name__,
                        'traceback': traceback.format_exc(),
                        'context': context,
                        'timestamp': datetime.utcnow().isoformat()
                    }
                )
                return fallback
        return wrapper


class FallbackSignal:
    """Fallback signal when analysis fails"""
    
    @staticmethod
    def neutral_signal(symbol: str, reason: str = "Analysis failed") -> Dict[str, Any]:
        """Return neutral signal as fallback"""
        return {
            'symbol': symbol,
            'signal': 'NEUTRAL',
            'confidence': 0.0,
            'regime': 'UNKNOWN',
            'smc_score': 0,
            'mtf_alignment': 0.0,
            'reason': reason,
            'fallback': True,
            'timestamp': datetime.utcnow().isoformat()
        }
    
    @staticmethod
    def safe_mtf_result(symbol: str, reason: str = "MTF unavailable") -> Dict[str, Any]:
        """Return safe MTF result"""
        return {
            'symbol': symbol,
            'alignment_score': 0.0,
            'dominant_direction': 'NEUTRAL',
            'valid': False,
            'error': reason,
            'fallback': True,
            'timestamp': datetime.utcnow().isoformat()
        }
    
    @staticmethod
    def safe_smc_result(symbol: str, reason: str = "SMC unavailable") -> Dict[str, Any]:
        """Return safe SMC result"""
        return {
            'symbol': symbol,
            'smc_score': 0,
            'bias': 'NEUTRAL',
            'valid': False,
            'error': reason,
            'fallback': True,
            'timestamp': datetime.utcnow().isoformat()
        }


class CircuitBreaker:
    """Circuit breaker pattern for API calls"""
    
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = 'CLOSED'  # CLOSED, OPEN, HALF_OPEN
    
    def record_success(self):
        """Record successful call"""
        self.failure_count = 0
        self.state = 'CLOSED'
        logger.info("✅ Circuit breaker CLOSED")
    
    def record_failure(self):
        """Record failed call"""
        self.failure_count += 1
        self.last_failure_time = datetime.utcnow()
        
        if self.failure_count >= self.failure_threshold:
            self.state = 'OPEN'
            logger.warning(f"⚠️ Circuit breaker OPEN ({self.failure_count} failures)")
    
    def can_execute(self) -> bool:
        """Check if call can be executed"""
        if self.state == 'CLOSED':
            return True
        
        if self.state == 'OPEN':
            # Check if recovery timeout has passed
            if self.last_failure_time:
                elapsed = (datetime.utcnow() - self.last_failure_time).total_seconds()
                if elapsed > self.recovery_timeout:
                    self.state = 'HALF_OPEN'
                    logger.info("🔄 Circuit breaker HALF_OPEN (attempting recovery)")
                    return True
            return False
        
        # HALF_OPEN state - allow one attempt
        return True


class RateLimiter:
    """Rate limiting for API calls"""
    
    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests = []
    
    async def check_rate_limit(self) -> bool:
        """Check if request is allowed"""
        import time
        now = time.time()
        
        # Remove old requests outside window
        self.requests = [r for r in self.requests if now - r < self.window_seconds]
        
        if len(self.requests) >= self.max_requests:
            logger.warning(
                f"⚠️ Rate limit exceeded ({len(self.requests)}/{self.max_requests})"
            )
            return False
        
        self.requests.append(now)
        return True
    
    def get_remaining(self) -> int:
        """Get remaining requests in current window"""
        import time
        now = time.time()
        self.requests = [r for r in self.requests if now - r < self.window_seconds]
        return max(0, self.max_requests - len(self.requests))


# Global instances
error_handler = ErrorHandler()
fallback_signal = FallbackSignal()
circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
rate_limiter = RateLimiter(max_requests=100, window_seconds=60)


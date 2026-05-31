"""
Structured Logging System
Comprehensive logging for all operations with context
"""

import logging
import json
import traceback
from datetime import datetime
from typing import Dict, Any, Optional
import sys

# Configure structured logging
class StructuredFormatter(logging.Formatter):
    """Format logs as structured JSON"""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            'timestamp': datetime.utcnow().isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
        }
        
        # Add extra fields
        if hasattr(record, '__dict__'):
            for key, value in record.__dict__.items():
                if key not in ['name', 'msg', 'args', 'created', 'filename', 'funcName', 
                               'levelname', 'levelno', 'lineno', 'module', 'msecs', 'message',
                               'pathname', 'process', 'processName', 'relativeCreated', 'thread',
                               'threadName', 'exc_info', 'exc_text', 'stack_info']:
                    log_data[key] = value
        
        return json.dumps(log_data)


class StructuredLogger:
    """Structured logging for all operations"""
    
    @staticmethod
    def setup_logging(log_level: str = 'INFO'):
        """Setup structured logging"""
        logger = logging.getLogger()
        logger.setLevel(getattr(logging, log_level))
        
        # Console handler with structured format
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(StructuredFormatter())
        logger.addHandler(console_handler)
        
        # File handler
        file_handler = logging.FileHandler('logs/system.log')
        file_handler.setFormatter(StructuredFormatter())
        logger.addHandler(file_handler)
        
        return logger
    
    @staticmethod
    def log_signal(signal: Dict[str, Any]):
        """Log signal generation"""
        logger = logging.getLogger(__name__)
        logger.info(
            "Signal generated",
            extra={
                'event': 'signal_generated',
                'symbol': signal.get('symbol'),
                'signal_type': signal.get('signal'),
                'confidence': signal.get('confidence'),
                'mtf_alignment': signal.get('mtf_alignment', 0),
                'smc_score': signal.get('smc_score', 0),
                'regime': signal.get('regime'),
                'entry_price': signal.get('entry_price'),
                'sl_price': signal.get('sl_price'),
                'tp_levels': signal.get('tp_levels'),
                'version': '3.0.2'
            }
        )
    
    @staticmethod
    def log_error(error: Exception, context: Dict[str, Any]):
        """Log error with context"""
        logger = logging.getLogger(__name__)
        logger.error(
            f"Error: {str(error)}",
            extra={
                'event': 'error',
                'error_type': type(error).__name__,
                'error_message': str(error),
                'traceback': traceback.format_exc(),
                'context': context,
            }
        )
    
    @staticmethod
    def log_validation(validation_type: str, is_valid: bool, details: Dict[str, Any]):
        """Log validation result"""
        logger = logging.getLogger(__name__)
        level = 'info' if is_valid else 'warning'
        
        getattr(logger, level)(
            f"Validation: {validation_type}",
            extra={
                'event': 'validation',
                'validation_type': validation_type,
                'is_valid': is_valid,
                'details': details,
            }
        )
    
    @staticmethod
    def log_health_check(check_name: str, status: Dict[str, Any]):
        """Log health check result"""
        logger = logging.getLogger(__name__)
        is_healthy = status.get('healthy', False)
        level = 'info' if is_healthy else 'warning'
        
        getattr(logger, level)(
            f"Health check: {check_name}",
            extra={
                'event': 'health_check',
                'check_name': check_name,
                'healthy': is_healthy,
                'status': status,
            }
        )
    
    @staticmethod
    def log_backup(backup_type: str, result: Dict[str, Any]):
        """Log backup operation"""
        logger = logging.getLogger(__name__)
        is_success = result.get('success', False)
        level = 'info' if is_success else 'error'
        
        getattr(logger, level)(
            f"Backup: {backup_type}",
            extra={
                'event': 'backup',
                'backup_type': backup_type,
                'success': is_success,
                'result': result,
            }
        )
    
    @staticmethod
    def log_api_call(method: str, endpoint: str, status_code: int, duration_ms: float):
        """Log API call"""
        logger = logging.getLogger(__name__)
        is_success = 200 <= status_code < 300
        level = 'info' if is_success else 'warning'
        
        getattr(logger, level)(
            f"API call: {method} {endpoint}",
            extra={
                'event': 'api_call',
                'method': method,
                'endpoint': endpoint,
                'status_code': status_code,
                'duration_ms': duration_ms,
            }
        )
    
    @staticmethod
    def log_performance(operation: str, duration_ms: float, details: Dict[str, Any]):
        """Log performance metrics"""
        logger = logging.getLogger(__name__)
        is_slow = duration_ms > 1000  # Alert if > 1 second
        level = 'warning' if is_slow else 'info'
        
        getattr(logger, level)(
            f"Performance: {operation}",
            extra={
                'event': 'performance',
                'operation': operation,
                'duration_ms': duration_ms,
                'is_slow': is_slow,
                'details': details,
            }
        )
    
    @staticmethod
    def log_data_quality(symbol: str, metric: str, value: float, threshold: float):
        """Log data quality metrics"""
        logger = logging.getLogger(__name__)
        is_good = value >= threshold
        level = 'info' if is_good else 'warning'
        
        getattr(logger, level)(
            f"Data quality: {symbol} {metric}",
            extra={
                'event': 'data_quality',
                'symbol': symbol,
                'metric': metric,
                'value': value,
                'threshold': threshold,
                'is_good': is_good,
            }
        )


# Setup logging on import
StructuredLogger.setup_logging('INFO')


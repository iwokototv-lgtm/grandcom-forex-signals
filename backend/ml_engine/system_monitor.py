"""
System Monitoring & Health Checks
Real-time monitoring of system health with alerting
"""

import asyncio
import psutil
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import os

logger = logging.getLogger(__name__)


class SystemMonitor:
    """Monitor system health and performance"""
    
    def __init__(self):
        self.start_time = datetime.utcnow()
        self.error_count = 0
        self.signal_count = 0
        self.last_signal_time = None
        self.health_checks = {}
    
    async def full_health_check(self) -> Dict[str, Any]:
        """Run comprehensive health check"""
        checks = {
            'timestamp': datetime.utcnow().isoformat(),
            'uptime_seconds': (datetime.utcnow() - self.start_time).total_seconds(),
            'mongodb': await self._check_mongodb(),
            'api_health': await self._check_api(),
            'data_freshness': await self._check_data_freshness(),
            'error_rate': self._check_error_rate(),
            'memory': self._check_memory(),
            'disk': self._check_disk(),
            'cpu': self._check_cpu(),
            'signal_generation': self._check_signal_generation(),
        }
        
        # Overall status
        all_healthy = all(check.get('healthy', False) for check in checks.values() if isinstance(check, dict))
        checks['overall_status'] = 'HEALTHY' if all_healthy else 'DEGRADED'
        
        # Log critical issues
        for check_name, check_result in checks.items():
            if isinstance(check_result, dict) and not check_result.get('healthy', True):
                logger.warning(
                    f"⚠️ Health check failed: {check_name}",
                    extra={'check': check_name, 'reason': check_result.get('reason', 'Unknown')}
                )
        
        self.health_checks = checks
        return checks
    
    async def _check_mongodb(self) -> Dict[str, Any]:
        """Check MongoDB connection"""
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
            mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
            client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)
            
            # Try to ping
            await asyncio.wait_for(client.admin.command('ping'), timeout=5)
            
            logger.info("✅ MongoDB: HEALTHY")
            return {
                'healthy': True,
                'status': 'Connected',
                'timestamp': datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"❌ MongoDB: FAILED - {str(e)}")
            return {
                'healthy': False,
                'status': 'Disconnected',
                'reason': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }
    
    async def _check_api(self) -> Dict[str, Any]:
        """Check API health endpoint"""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get('http://localhost:8080/api/health', timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        logger.info("✅ API: HEALTHY")
                        return {
                            'healthy': True,
                            'status': 'Running',
                            'status_code': resp.status,
                            'timestamp': datetime.utcnow().isoformat()
                        }
                    else:
                        return {
                            'healthy': False,
                            'status': 'Unhealthy',
                            'status_code': resp.status,
                            'reason': f"HTTP {resp.status}",
                            'timestamp': datetime.utcnow().isoformat()
                        }
        except Exception as e:
            logger.error(f"❌ API: FAILED - {str(e)}")
            return {
                'healthy': False,
                'status': 'Unreachable',
                'reason': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }
    
    async def _check_data_freshness(self) -> Dict[str, Any]:
        """Check if data is being updated regularly"""
        try:
            if self.last_signal_time is None:
                return {
                    'healthy': False,
                    'status': 'No signals generated yet',
                    'reason': 'Waiting for first signal',
                    'timestamp': datetime.utcnow().isoformat()
                }
            
            time_since_signal = (datetime.utcnow() - self.last_signal_time).total_seconds()
            max_age_seconds = 1800  # 30 minutes
            
            if time_since_signal > max_age_seconds:
                logger.warning(f"⚠️ Data freshness: Last signal {time_since_signal}s ago")
                return {
                    'healthy': False,
                    'status': 'Stale',
                    'last_signal_age_seconds': time_since_signal,
                    'reason': f'No signal for {time_since_signal}s',
                    'timestamp': datetime.utcnow().isoformat()
                }
            
            logger.info(f"✅ Data freshness: OK ({time_since_signal}s)")
            return {
                'healthy': True,
                'status': 'Fresh',
                'last_signal_age_seconds': time_since_signal,
                'timestamp': datetime.utcnow().isoformat()
            }
        except Exception as e:
            return {
                'healthy': False,
                'status': 'Error',
                'reason': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }
    
    def _check_error_rate(self) -> Dict[str, Any]:
        """Check error rate"""
        try:
            if self.signal_count == 0:
                error_rate = 0.0
            else:
                error_rate = (self.error_count / self.signal_count) * 100
            
            healthy = error_rate < 5.0  # Alert if >5% errors
            
            status = "HEALTHY" if healthy else "DEGRADED"
            logger.info(f"✅ Error rate: {error_rate:.2f}% ({status})")
            
            return {
                'healthy': healthy,
                'error_count': self.error_count,
                'signal_count': self.signal_count,
                'error_rate_percent': round(error_rate, 2),
                'status': status,
                'timestamp': datetime.utcnow().isoformat()
            }
        except Exception as e:
            return {
                'healthy': False,
                'reason': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }
    
    def _check_memory(self) -> Dict[str, Any]:
        """Check memory usage"""
        try:
            memory = psutil.virtual_memory()
            memory_percent = memory.percent
            
            healthy = memory_percent < 80  # Alert if >80%
            
            logger.info(f"✅ Memory: {memory_percent:.1f}% ({'HEALTHY' if healthy else 'HIGH'})")
            
            return {
                'healthy': healthy,
                'used_gb': round(memory.used / (1024**3), 2),
                'total_gb': round(memory.total / (1024**3), 2),
                'percent': memory_percent,
                'status': 'HEALTHY' if healthy else 'HIGH',
                'timestamp': datetime.utcnow().isoformat()
            }
        except Exception as e:
            return {
                'healthy': False,
                'reason': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }
    
    def _check_disk(self) -> Dict[str, Any]:
        """Check disk space"""
        try:
            disk = psutil.disk_usage('/')
            disk_percent = disk.percent
            
            healthy = disk_percent < 85  # Alert if >85%
            
            logger.info(f"✅ Disk: {disk_percent:.1f}% ({'HEALTHY' if healthy else 'FULL'})")
            
            return {
                'healthy': healthy,
                'used_gb': round(disk.used / (1024**3), 2),
                'total_gb': round(disk.total / (1024**3), 2),
                'percent': disk_percent,
                'status': 'HEALTHY' if healthy else 'FULL',
                'timestamp': datetime.utcnow().isoformat()
            }
        except Exception as e:
            return {
                'healthy': False,
                'reason': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }
    
    def _check_cpu(self) -> Dict[str, Any]:
        """Check CPU usage"""
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            
            healthy = cpu_percent < 80  # Alert if >80%
            
            logger.info(f"✅ CPU: {cpu_percent:.1f}% ({'HEALTHY' if healthy else 'HIGH'})")
            
            return {
                'healthy': healthy,
                'percent': cpu_percent,
                'status': 'HEALTHY' if healthy else 'HIGH',
                'timestamp': datetime.utcnow().isoformat()
            }
        except Exception as e:
            return {
                'healthy': False,
                'reason': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }
    
    def _check_signal_generation(self) -> Dict[str, Any]:
        """Check signal generation status"""
        return {
            'total_signals': self.signal_count,
            'total_errors': self.error_count,
            'uptime_seconds': (datetime.utcnow() - self.start_time).total_seconds(),
            'last_signal_time': self.last_signal_time.isoformat() if self.last_signal_time else None,
            'timestamp': datetime.utcnow().isoformat()
        }
    
    def record_signal(self):
        """Record signal generation"""
        self.signal_count += 1
        self.last_signal_time = datetime.utcnow()
    
    def record_error(self):
        """Record error"""
        self.error_count += 1


# Global monitor instance
system_monitor = SystemMonitor()


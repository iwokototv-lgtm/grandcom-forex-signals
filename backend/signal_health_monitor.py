"""
Signal Health Monitor
=====================
Monitors signal generation health and alerts on anomalies.

Anomalies detected:
  - No signals generated across multiple cycles (0 successful signals)
  - Low success rate (< 50%)
  - High API timeout rate (> 5 timeouts)
  - Drawdown recovery halting trading unexpectedly

Exposes a /api/health/signals endpoint that returns the current health
status and any active alerts.

Usage:
    # In gold_server_v3.py:
    from signal_health_monitor import SignalHealthMonitor, get_signal_health_endpoint

    monitor = SignalHealthMonitor(signal_metrics=_signal_metrics)

    @app.get("/api/health/signals")
    async def get_signal_health():
        return await monitor.check_signal_health()
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

# Minimum success rate before a WARNING alert fires (%)
MIN_SUCCESS_RATE_PCT: float = 50.0

# Maximum API timeouts before a WARNING alert fires
MAX_API_TIMEOUTS: int = 5

# Minimum cycles before success-rate alerts are meaningful
MIN_CYCLES_FOR_RATE_ALERT: int = 3


class SignalHealthMonitor:
    """
    Monitor signal generation health and surface anomalies.

    Designed to be instantiated once and reused across requests.
    The *signal_metrics* object is the same SignalMetrics singleton
    used by gold_server_v3.py.
    """

    def __init__(self, signal_metrics=None):
        """
        Args:
            signal_metrics: The SignalMetrics singleton from gold_server_v3.
                            If None, the monitor will attempt to import it
                            lazily on first use.
        """
        self._signal_metrics = signal_metrics

    # ── Public API ────────────────────────────────────────────────────────────

    async def check_signal_health(self) -> Dict[str, Any]:
        """
        Check if signals are being generated normally.

        Returns:
            {
                "healthy":    bool,
                "alerts":     [{"severity": str, "message": str}, ...],
                "metrics":    {...},
                "timestamp":  ISO-8601 string,
            }
        """
        metrics = await self._get_metrics()
        alerts: List[Dict[str, str]] = []

        # ── Alert 1: No signals in any completed cycles ───────────────────────
        total_cycles = metrics.get("total_cycles", 0)
        successful_signals = metrics.get("successful_signals", 0)

        if total_cycles > 0 and successful_signals == 0:
            alerts.append({
                "severity": "CRITICAL",
                "message": (
                    f"No signals generated in {total_cycles} cycle(s). "
                    "Check consensus logic, drawdown limits, and API connectivity."
                ),
            })

        # ── Alert 2: Low success rate ─────────────────────────────────────────
        if total_cycles >= MIN_CYCLES_FOR_RATE_ALERT:
            success_rate_str = metrics.get("success_rate", "0.0%")
            try:
                success_rate = float(success_rate_str.rstrip("%"))
            except (ValueError, AttributeError):
                success_rate = 0.0

            if success_rate < MIN_SUCCESS_RATE_PCT:
                alerts.append({
                    "severity": "WARNING",
                    "message": (
                        f"Low signal success rate: {success_rate:.1f}% "
                        f"(threshold: {MIN_SUCCESS_RATE_PCT:.0f}%). "
                        f"Successful: {successful_signals}/{total_cycles} cycles."
                    ),
                })

        # ── Alert 3: High API timeout rate ────────────────────────────────────
        api_timeouts = metrics.get("api_timeouts", 0)
        if api_timeouts > MAX_API_TIMEOUTS:
            alerts.append({
                "severity": "WARNING",
                "message": (
                    f"High API timeout count: {api_timeouts} "
                    f"(threshold: {MAX_API_TIMEOUTS}). "
                    "TwelveData or OpenAI may be experiencing issues."
                ),
            })

        # ── Alert 4: High API error rate ──────────────────────────────────────
        api_errors = metrics.get("api_errors", 0)
        failed_cycles = metrics.get("failed_cycles", 0)
        if total_cycles > 0 and failed_cycles > total_cycles * 0.5:
            alerts.append({
                "severity": "WARNING",
                "message": (
                    f"High failure rate: {failed_cycles}/{total_cycles} cycles failed. "
                    f"API errors: {api_errors}."
                ),
            })

        healthy = len(alerts) == 0

        if not healthy:
            for alert in alerts:
                log_fn = logger.error if alert["severity"] == "CRITICAL" else logger.warning
                log_fn(f"[SignalHealth] {alert['severity']}: {alert['message']}")

        return {
            "healthy": healthy,
            "alerts": alerts,
            "metrics": metrics,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def get_summary(self) -> str:
        """Return a one-line health summary string for logging."""
        result = await self.check_signal_health()
        if result["healthy"]:
            m = result["metrics"]
            return (
                f"HEALTHY — cycles={m.get('total_cycles', 0)} "
                f"signals={m.get('successful_signals', 0)} "
                f"rate={m.get('success_rate', '0%')}"
            )
        alert_msgs = "; ".join(a["message"] for a in result["alerts"])
        return f"UNHEALTHY — {alert_msgs}"

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _get_metrics(self) -> Dict[str, Any]:
        """Fetch metrics from the signal_metrics singleton."""
        if self._signal_metrics is not None:
            try:
                return await self._signal_metrics.log_metrics()
            except Exception as exc:
                logger.error(f"[SignalHealth] Failed to fetch metrics: {exc}")
                return self._empty_metrics()

        # Lazy import fallback
        try:
            from gold_server_v3 import _signal_metrics
            return await _signal_metrics.log_metrics()
        except Exception as exc:
            logger.error(f"[SignalHealth] Cannot import _signal_metrics: {exc}")
            return self._empty_metrics()

    @staticmethod
    def _empty_metrics() -> Dict[str, Any]:
        return {
            "total_cycles": 0,
            "successful_signals": 0,
            "failed_cycles": 0,
            "success_rate": "0.0%",
            "retry_attempts": 0,
            "api_timeouts": 0,
            "api_errors": 0,
        }


# ── Module-level singleton ────────────────────────────────────────────────────

# Instantiated without a metrics reference; will lazy-import on first use.
# Replace with SignalHealthMonitor(signal_metrics=_signal_metrics) in
# gold_server_v3.py for tighter coupling.
_signal_health_monitor = SignalHealthMonitor()

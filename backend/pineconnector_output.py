"""Optional PineConnector output for legacy BUY/SELL signals.

Off unless PINECONNECTOR_ENABLED=true AND PINECONNECTOR_WEBHOOK_URL and
PINECONNECTOR_KEY are set. Only XAUUSD and XAUEUR are sent. Failures are logged and
never raised, so a PineConnector problem cannot block the Telegram post.

Message format (PineConnector alert):  KEY,buy|sell,PAIR,vol_lots=X
The Telegram copier and PineConnector must not both trade the same
signal; enable only one of them for XAUUSD.
"""

import logging
import os
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

PAIRS = ("XAUUSD", "XAUEUR")


def is_enabled() -> bool:
    return (
        os.environ.get("PINECONNECTOR_ENABLED", "false").strip().lower() == "true"
        and bool(os.environ.get("PINECONNECTOR_WEBHOOK_URL", "").strip())
        and bool(os.environ.get("PINECONNECTOR_KEY", "").strip())
    )


def build_alert(pair: str, signal: str, vol_lots: str) -> Optional[str]:
    """Return the alert text, or None when this signal is not sent."""
    if pair not in PAIRS or signal not in ("BUY", "SELL"):
        return None
    key = os.environ.get("PINECONNECTOR_KEY", "").strip()
    return f"{key},{signal.lower()},{pair},vol_lots={vol_lots}"


async def send_signal(pair: str, signal: str) -> bool:
    """POST one alert to PineConnector. Returns True if it was accepted."""
    if not is_enabled():
        return False
    vol_lots = os.environ.get("PINECONNECTOR_VOL_LOTS", "0.01").strip() or "0.01"
    alert = build_alert(pair, signal, vol_lots)
    if alert is None:
        return False
    url = os.environ["PINECONNECTOR_WEBHOOK_URL"].strip()
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, data=alert.encode("utf-8")) as resp:
                ok = 200 <= resp.status < 300
                if not ok:
                    logger.warning("PineConnector rejected alert: HTTP %s", resp.status)
                else:
                    logger.info("PineConnector alert sent: %s %s", signal, pair)
                return ok
    except Exception as exc:  # never block the signal flow
        logger.warning("PineConnector send failed: %s", type(exc).__name__)
        return False

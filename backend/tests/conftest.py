"""
Pytest configuration and shared fixtures.

Includes:
  - HTTP integration test fixtures (api_client, admin_token, authenticated_client)
  - Signal generation unit/integration test fixtures (mock_position_manager,
    mock_risk_manager, mock_reversal_detector, sample_ohlcv_df)
"""

import pytest
import asyncio
import requests
import os
import numpy as np
from unittest.mock import AsyncMock, MagicMock

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://gold-signal-debug.preview.emergentagent.com').rstrip('/')

# Test credentials
ADMIN_EMAIL = "admin@forexsignals.com"
ADMIN_PASSWORD = "Admin@2024!Forex"


@pytest.fixture
def api_client():
    """Shared requests session"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


@pytest.fixture
def admin_token(api_client):
    """Get admin authentication token"""
    response = api_client.post(f"{BASE_URL}/api/auth/login", json={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD
    })
    assert response.status_code == 200, f"Admin login failed: {response.text}"
    data = response.json()
    assert "access_token" in data
    return data["access_token"]


@pytest.fixture
def authenticated_client(api_client, admin_token):
    """Session with auth header"""
    api_client.headers.update({"Authorization": f"Bearer {admin_token}"})
    return api_client


# ─────────────────────────────────────────────────────────────────────────
# Signal Generation Test Fixtures
# ─────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    """Create a single event loop for the entire test session (async tests)."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_ohlcv_df():
    """Return a minimal 60-row OHLCV DataFrame suitable for indicator tests."""
    import pandas as pd
    n = 60
    close = np.linspace(1900.0, 1950.0, n)
    return pd.DataFrame({
        "open":   close - 1.0,
        "high":   close + 2.0,
        "low":    close - 2.0,
        "close":  close,
        "volume": np.ones(n) * 1000,
    })


@pytest.fixture
def mock_position_manager():
    """
    Mock PositionManager with sensible defaults.

    Defaults:
      - get_position_count → 0  (no open positions)
      - get_positions → []
      - close_all_positions → {"closed": 0, "total_pnl": 0.0}
    """
    manager = MagicMock()
    manager.get_position_count = AsyncMock(return_value=0)
    manager.get_positions = AsyncMock(return_value=[])
    manager.get_open_positions = AsyncMock(return_value=[])
    manager.get_positions_summary = AsyncMock(return_value={
        "total_open": 0,
        "exposure_pct": 0.0,
    })
    manager.close_all_positions = AsyncMock(
        return_value={"closed": 0, "total_pnl": 0.0, "success": True}
    )
    manager.add_position = AsyncMock(
        return_value={"allowed": True, "position_id": "mock-id", "reason": "OK"}
    )
    return manager


@pytest.fixture
def mock_risk_manager():
    """
    Mock RiskManager with sensible defaults.

    Defaults:
      - enforce_risk_limits → trading allowed
      - get_risk_status → GREEN, 0% drawdown
      - equity_peak = 10000.0 (not $1M — Bug #1 check)
    """
    manager = MagicMock()
    manager.enforce_risk_limits = AsyncMock(
        return_value={"trading_allowed": True, "reason": "OK"}
    )
    manager.check_daily_loss = AsyncMock(
        return_value={"halted": False, "daily_pnl": 0.0, "daily_loss_pct": 0.0}
    )
    manager.check_account_drawdown = AsyncMock(
        return_value={
            "halted": False,
            "drawdown_pct": 0.0,
            "equity_peak": 10_000.0,
            "current_equity": 10_000.0,
        }
    )
    manager.get_risk_status = MagicMock(return_value={
        "trading_halted": False,
        "halt_reason": "",
        "daily_pnl": 0.0,
        "daily_loss_pct": 0.0,
        "drawdown_pct": 0.0,
        "risk_level": "GREEN",
        "equity_peak": 10_000.0,
        "current_equity": 10_000.0,
    })
    manager.current_equity = 10_000.0
    manager.equity_peak = 10_000.0   # Must NOT be 1_000_000 (Bug #1)
    manager.set_account_balance = MagicMock()
    return manager


@pytest.fixture
def mock_reversal_detector():
    """
    Mock ReversalDetector that reports no reversal by default.

    Override detect_reversal in individual tests to simulate reversals.
    """
    detector = MagicMock()
    detector.detect_reversal = AsyncMock(return_value={
        "reversal_detected": False,
        "previous_regime": "NEUTRAL",
        "new_regime": "NEUTRAL",
        "reason": "",
    })
    return detector


@pytest.fixture
def mock_telegram_bot():
    """Mock Telegram Bot that captures send_message calls."""
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=None)
    bot.get_me = AsyncMock(return_value=MagicMock(username="test_bot"))
    return bot

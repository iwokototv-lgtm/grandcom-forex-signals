"""
Tests for Signal Management API — /api/manager/signals/*
Covers: pending signals, signal details, approve, reject, adjust,
        history, approval stats
"""
import pytest
import requests
import os

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://gold-signal-debug.preview.emergentagent.com",
).rstrip("/")

MANAGER_EMAIL    = os.environ.get("MANAGER_EMAIL",    "admin@forexsignals.com")
MANAGER_PASSWORD = os.environ.get("MANAGER_PASSWORD", "Admin@2024!Forex")


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def manager_token(api_client):
    resp = api_client.post(
        f"{BASE_URL}/api/manager/auth/login",
        json={"email": MANAGER_EMAIL, "password": MANAGER_PASSWORD},
    )
    if resp.status_code != 200:
        pytest.skip(f"Manager login failed: {resp.text}")
    return resp.json()["access_token"]


@pytest.fixture(scope="module")
def mgr_client(api_client, manager_token):
    api_client.headers.update({"Authorization": f"Bearer {manager_token}"})
    return api_client


# ─────────────────────────────────────────────────────────────
# PENDING SIGNALS
# ─────────────────────────────────────────────────────────────

class TestPendingSignals:
    """Test GET /api/manager/signals/pending."""

    def test_pending_requires_auth(self, api_client):
        resp = requests.get(f"{BASE_URL}/api/manager/signals/pending")
        assert resp.status_code in (401, 403)

    def test_pending_returns_list(self, mgr_client):
        resp = mgr_client.get(f"{BASE_URL}/api/manager/signals/pending")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "signals" in data
        assert "total"   in data
        assert isinstance(data["signals"], list)

    def test_pending_with_pair_filter(self, mgr_client):
        resp = mgr_client.get(
            f"{BASE_URL}/api/manager/signals/pending",
            params={"pair": "XAUUSD"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        # All returned signals should be XAUUSD
        for sig in data["signals"]:
            assert sig.get("pair") == "XAUUSD"

    def test_pending_with_confidence_filter(self, mgr_client):
        resp = mgr_client.get(
            f"{BASE_URL}/api/manager/signals/pending",
            params={"min_confidence": 70.0},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        for sig in data["signals"]:
            assert sig.get("confidence", 0) >= 70.0

    def test_pending_limit_respected(self, mgr_client):
        resp = mgr_client.get(
            f"{BASE_URL}/api/manager/signals/pending",
            params={"limit": 5},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["signals"]) <= 5


# ─────────────────────────────────────────────────────────────
# SIGNAL HISTORY
# ─────────────────────────────────────────────────────────────

class TestSignalHistory:
    """Test GET /api/manager/signals/history/all."""

    def test_history_requires_auth(self, api_client):
        resp = requests.get(f"{BASE_URL}/api/manager/signals/history/all")
        assert resp.status_code in (401, 403)

    def test_history_returns_data(self, mgr_client):
        resp = mgr_client.get(f"{BASE_URL}/api/manager/signals/history/all")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "history" in data
        assert "total"   in data
        assert "stats"   in data

    def test_history_stats_structure(self, mgr_client):
        resp = mgr_client.get(f"{BASE_URL}/api/manager/signals/history/all")
        data = resp.json()
        stats = data["stats"]
        assert "approved"      in stats
        assert "rejected"      in stats
        assert "adjusted"      in stats
        assert "approval_rate" in stats

    def test_history_status_filter(self, mgr_client):
        resp = mgr_client.get(
            f"{BASE_URL}/api/manager/signals/history/all",
            params={"status": "APPROVED"},
        )
        assert resp.status_code == 200
        data = resp.json()
        for sig in data["history"]:
            assert sig.get("review_status") == "APPROVED"

    def test_history_hours_filter(self, mgr_client):
        resp = mgr_client.get(
            f"{BASE_URL}/api/manager/signals/history/all",
            params={"hours": 48},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True


# ─────────────────────────────────────────────────────────────
# APPROVAL STATS
# ─────────────────────────────────────────────────────────────

class TestApprovalStats:
    """Test GET /api/manager/signals/stats/approval."""

    def test_stats_requires_auth(self, api_client):
        resp = requests.get(f"{BASE_URL}/api/manager/signals/stats/approval")
        assert resp.status_code in (401, 403)

    def test_stats_returns_data(self, mgr_client):
        resp = mgr_client.get(f"{BASE_URL}/api/manager/signals/stats/approval")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"]    is True
        assert "stats"            in data
        assert "per_manager"      in data
        assert "per_pair"         in data
        assert "period_days"      in data

    def test_stats_overall_structure(self, mgr_client):
        resp = mgr_client.get(f"{BASE_URL}/api/manager/signals/stats/approval")
        data = resp.json()
        stats = data["stats"]
        assert "total_pending"          in stats
        assert "total_approved"         in stats
        assert "total_rejected"         in stats
        assert "total_adjusted"         in stats
        assert "overall_approval_rate"  in stats

    def test_stats_days_parameter(self, mgr_client):
        resp = mgr_client.get(
            f"{BASE_URL}/api/manager/signals/stats/approval",
            params={"days": 7},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["period_days"] == 7


# ─────────────────────────────────────────────────────────────
# APPROVE / REJECT / ADJUST (validation tests — no real signal needed)
# ─────────────────────────────────────────────────────────────

class TestSignalActions:
    """Test approve / reject / adjust endpoints with invalid inputs."""

    def test_approve_requires_auth(self, api_client):
        resp = requests.post(
            f"{BASE_URL}/api/manager/signals/approve",
            json={"signal_id": "000000000000000000000001"},
        )
        assert resp.status_code in (401, 403)

    def test_reject_requires_auth(self, api_client):
        resp = requests.post(
            f"{BASE_URL}/api/manager/signals/reject",
            json={"signal_id": "000000000000000000000001", "reason": "test"},
        )
        assert resp.status_code in (401, 403)

    def test_adjust_requires_auth(self, api_client):
        resp = requests.post(
            f"{BASE_URL}/api/manager/signals/adjust",
            json={"signal_id": "000000000000000000000001", "entry_price": 1900.0},
        )
        assert resp.status_code in (401, 403)

    def test_approve_nonexistent_signal(self, mgr_client):
        resp = mgr_client.post(
            f"{BASE_URL}/api/manager/signals/approve",
            json={"signal_id": "000000000000000000000001"},
        )
        # Should be 404 (not found) or 400 (bad state)
        assert resp.status_code in (400, 404)

    def test_reject_requires_reason(self, mgr_client):
        # Pydantic should reject an empty reason
        resp = mgr_client.post(
            f"{BASE_URL}/api/manager/signals/reject",
            json={"signal_id": "000000000000000000000001", "reason": ""},
        )
        assert resp.status_code in (400, 422)

    def test_reject_reason_too_short(self, mgr_client):
        resp = mgr_client.post(
            f"{BASE_URL}/api/manager/signals/reject",
            json={"signal_id": "000000000000000000000001", "reason": "bad"},
        )
        assert resp.status_code in (400, 422)

    def test_adjust_requires_at_least_one_field(self, mgr_client):
        # No price fields provided — should fail validation
        resp = mgr_client.post(
            f"{BASE_URL}/api/manager/signals/adjust",
            json={"signal_id": "000000000000000000000001"},
        )
        # 400 from SignalManager or 404 from not found
        assert resp.status_code in (400, 404, 422)

    def test_adjust_invalid_tp_levels(self, mgr_client):
        resp = mgr_client.post(
            f"{BASE_URL}/api/manager/signals/adjust",
            json={
                "signal_id": "000000000000000000000001",
                "tp_levels": [-100.0, 0.0],
            },
        )
        assert resp.status_code in (400, 422)

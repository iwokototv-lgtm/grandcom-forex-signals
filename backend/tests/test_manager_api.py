"""
Tests for System Manager API — /api/manager/*
Covers: auth, CRUD, system status, alerts, backups, audit, dashboard
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
    """Obtain a manager JWT via /api/manager/auth/login."""
    resp = api_client.post(
        f"{BASE_URL}/api/manager/auth/login",
        json={"email": MANAGER_EMAIL, "password": MANAGER_PASSWORD},
    )
    if resp.status_code != 200:
        pytest.skip(f"Manager login failed ({resp.status_code}): {resp.text}")
    data = resp.json()
    assert "access_token" in data, "No access_token in manager login response"
    return data["access_token"]


@pytest.fixture(scope="module")
def mgr_client(api_client, manager_token):
    """Requests session pre-loaded with manager Bearer token."""
    api_client.headers.update({"Authorization": f"Bearer {manager_token}"})
    return api_client


# ─────────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────────

class TestManagerAuth:
    """Test /api/manager/auth/* endpoints."""

    def test_login_returns_token(self, api_client):
        resp = api_client.post(
            f"{BASE_URL}/api/manager/auth/login",
            json={"email": MANAGER_EMAIL, "password": MANAGER_PASSWORD},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert "role" in data
        assert "expires_in" in data

    def test_login_invalid_credentials(self, api_client):
        resp = api_client.post(
            f"{BASE_URL}/api/manager/auth/login",
            json={"email": "nobody@example.com", "password": "wrong"},
        )
        assert resp.status_code == 401

    def test_get_my_profile(self, mgr_client):
        resp = mgr_client.get(f"{BASE_URL}/api/manager/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "manager" in data
        assert "role" in data["manager"]

    def test_profile_requires_auth(self, api_client):
        # Remove auth header for this call
        resp = requests.get(f"{BASE_URL}/api/manager/auth/me")
        assert resp.status_code in (401, 403)


# ─────────────────────────────────────────────────────────────
# ROLES ENDPOINT (public)
# ─────────────────────────────────────────────────────────────

class TestRolesEndpoint:
    """Test /api/manager/roles — no auth required."""

    def test_roles_returns_permission_matrix(self, api_client):
        resp = api_client.get(f"{BASE_URL}/api/manager/roles")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "roles" in data
        roles = data["roles"]
        assert "ADMIN"   in roles
        assert "MANAGER" in roles
        assert "VIEWER"  in roles

    def test_admin_has_all_permissions(self, api_client):
        resp = api_client.get(f"{BASE_URL}/api/manager/roles")
        data = resp.json()
        admin_perms = data["roles"]["ADMIN"]
        assert "manager:add"    in admin_perms
        assert "manager:remove" in admin_perms
        assert "system:restart" in admin_perms
        assert "audit:view"     in admin_perms

    def test_viewer_is_read_only(self, api_client):
        resp = api_client.get(f"{BASE_URL}/api/manager/roles")
        data = resp.json()
        viewer_perms = data["roles"]["VIEWER"]
        assert "manager:add"    not in viewer_perms
        assert "manager:remove" not in viewer_perms
        assert "system:restart" not in viewer_perms
        assert "dashboard:view" in viewer_perms


# ─────────────────────────────────────────────────────────────
# MANAGER CRUD
# ─────────────────────────────────────────────────────────────

class TestManagerCRUD:
    """Test /api/manager/managers/* endpoints."""

    def test_list_managers_requires_auth(self, api_client):
        resp = requests.get(f"{BASE_URL}/api/manager/managers")
        assert resp.status_code in (401, 403)

    def test_list_managers_authenticated(self, mgr_client):
        resp = mgr_client.get(f"{BASE_URL}/api/manager/managers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "managers" in data
        assert isinstance(data["managers"], list)
        assert "count" in data

    def test_list_managers_includes_inactive(self, mgr_client):
        resp = mgr_client.get(
            f"{BASE_URL}/api/manager/managers",
            params={"include_inactive": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True


# ─────────────────────────────────────────────────────────────
# SYSTEM MONITORING
# ─────────────────────────────────────────────────────────────

class TestSystemMonitoring:
    """Test /api/manager/system/* endpoints."""

    def test_system_status_requires_auth(self, api_client):
        resp = requests.get(f"{BASE_URL}/api/manager/system/status")
        assert resp.status_code in (401, 403)

    def test_system_status_returns_health(self, mgr_client):
        resp = mgr_client.get(f"{BASE_URL}/api/manager/system/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "status" in data
        status = data["status"]
        assert "overall"         in status
        assert "database"        in status
        assert "cpu_percent"     in status
        assert "memory_percent"  in status
        assert "disk_percent"    in status
        assert "version"         in status

    def test_system_status_version_is_correct(self, mgr_client):
        resp = mgr_client.get(f"{BASE_URL}/api/manager/system/status")
        data = resp.json()
        assert data["status"]["version"] == "3.0.2"

    def test_recent_signals_endpoint(self, mgr_client):
        resp = mgr_client.get(
            f"{BASE_URL}/api/manager/system/signals",
            params={"limit": 10, "hours": 24},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "signals" in data
        assert "count"   in data

    def test_system_logs_endpoint(self, mgr_client):
        resp = mgr_client.get(
            f"{BASE_URL}/api/manager/system/logs",
            params={"limit": 20},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "logs"  in data
        assert "count" in data


# ─────────────────────────────────────────────────────────────
# ALERTS
# ─────────────────────────────────────────────────────────────

class TestAlertManagement:
    """Test /api/manager/alerts/* endpoints."""

    def test_list_alerts_requires_auth(self, api_client):
        resp = requests.get(f"{BASE_URL}/api/manager/alerts")
        assert resp.status_code in (401, 403)

    def test_list_alerts_authenticated(self, mgr_client):
        resp = mgr_client.get(f"{BASE_URL}/api/manager/alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "alerts" in data
        assert "count"  in data

    def test_create_alert(self, mgr_client):
        resp = mgr_client.post(
            f"{BASE_URL}/api/manager/alerts",
            json={
                "title":    "Test Alert from pytest",
                "message":  "This is an automated test alert",
                "severity": "INFO",
                "category": "GENERAL",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "alert_id"   in data
        assert "severity"   in data
        assert "created_at" in data

    def test_create_alert_invalid_severity(self, mgr_client):
        resp = mgr_client.post(
            f"{BASE_URL}/api/manager/alerts",
            json={
                "title":    "Bad Alert",
                "message":  "Test",
                "severity": "INVALID_LEVEL",
            },
        )
        # Should return 400 or 422
        assert resp.status_code in (400, 422)


# ─────────────────────────────────────────────────────────────
# BACKUPS
# ─────────────────────────────────────────────────────────────

class TestBackupManagement:
    """Test /api/manager/backups/* endpoints."""

    def test_backup_history_requires_auth(self, api_client):
        resp = requests.get(f"{BASE_URL}/api/manager/backups/history")
        assert resp.status_code in (401, 403)

    def test_backup_history_authenticated(self, mgr_client):
        resp = mgr_client.get(f"{BASE_URL}/api/manager/backups/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "backups" in data
        assert "count"   in data

    def test_trigger_backup(self, mgr_client):
        resp = mgr_client.post(
            f"{BASE_URL}/api/manager/backups/trigger",
            json={"backup_type": "signals"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "backup_id"   in data
        assert "backup_type" in data
        assert data["backup_type"] == "signals"

    def test_trigger_backup_invalid_type(self, mgr_client):
        resp = mgr_client.post(
            f"{BASE_URL}/api/manager/backups/trigger",
            json={"backup_type": "invalid_type"},
        )
        assert resp.status_code in (400, 422)


# ─────────────────────────────────────────────────────────────
# AUDIT LOG
# ─────────────────────────────────────────────────────────────

class TestAuditLog:
    """Test /api/manager/audit endpoint."""

    def test_audit_log_requires_auth(self, api_client):
        resp = requests.get(f"{BASE_URL}/api/manager/audit")
        assert resp.status_code in (401, 403)

    def test_audit_log_authenticated(self, mgr_client):
        resp = mgr_client.get(
            f"{BASE_URL}/api/manager/audit",
            params={"limit": 20, "since_hours": 24},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"]   is True
        assert "audit_log"       in data
        assert "count"           in data
        assert "since_hours"     in data

    def test_audit_log_is_list(self, mgr_client):
        resp = mgr_client.get(f"{BASE_URL}/api/manager/audit", params={"limit": 5})
        data = resp.json()
        assert isinstance(data["audit_log"], list)


# ─────────────────────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────────────────────

class TestDashboard:
    """Test /api/manager/dashboard endpoint."""

    def test_dashboard_requires_auth(self, api_client):
        resp = requests.get(f"{BASE_URL}/api/manager/dashboard")
        assert resp.status_code in (401, 403)

    def test_dashboard_returns_full_overview(self, mgr_client):
        resp = mgr_client.get(f"{BASE_URL}/api/manager/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "dashboard" in data
        dash = data["dashboard"]
        assert "managers"        in dash
        assert "trading"         in dash
        assert "alerts"          in dash
        assert "backups"         in dash
        assert "infrastructure"  in dash
        assert "recent_activity" in dash
        assert "system_version"  in dash

    def test_dashboard_version_is_correct(self, mgr_client):
        resp = mgr_client.get(f"{BASE_URL}/api/manager/dashboard")
        data = resp.json()
        assert data["dashboard"]["system_version"] == "3.0.2"

    def test_dashboard_infrastructure_has_metrics(self, mgr_client):
        resp = mgr_client.get(f"{BASE_URL}/api/manager/dashboard")
        data = resp.json()
        infra = data["dashboard"]["infrastructure"]
        assert "cpu_percent"    in infra
        assert "memory_percent" in infra
        assert "disk_percent"   in infra
        assert "health"         in infra

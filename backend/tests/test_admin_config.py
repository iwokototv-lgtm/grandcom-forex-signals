"""
Test Admin Configuration and Pair Re-enablement Verification

Verifies:
1. XAUUSD, XAUEUR, GBPJPY, AUDUSD pairs are enabled
2. System config endpoint returns correct active pairs list
3. Admin authentication and authorization
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://gold-signal-debug.preview.emergentagent.com').rstrip('/')

# Expected re-enabled pairs
RE_ENABLED_PAIRS = ["XAUUSD", "XAUEUR", "GBPJPY", "AUDUSD"]


class TestAdminSystemConfig:
    """Test admin system configuration endpoint"""
    
    def test_system_config_requires_auth(self, api_client):
        """System config endpoint should require authentication"""
        response = api_client.get(f"{BASE_URL}/api/admin/system-config")
        assert response.status_code in [401, 403], "Endpoint should require auth"
    
    def test_system_config_requires_admin_role(self, authenticated_client):
        """System config should be accessible to admin users"""
        response = authenticated_client.get(f"{BASE_URL}/api/admin/system-config")
        assert response.status_code == 200, f"Admin should access system-config: {response.text}"
        data = response.json()
        assert data.get("success") is True
    
    def test_active_pairs_list_structure(self, authenticated_client):
        """Verify active_pairs_list exists and has correct structure"""
        response = authenticated_client.get(f"{BASE_URL}/api/admin/system-config")
        assert response.status_code == 200
        data = response.json()
        
        # Check structure
        assert "config" in data
        assert "signal_generation" in data["config"]
        assert "active_pairs_list" in data["config"]["signal_generation"]
        assert "disabled_pairs" in data["config"]["signal_generation"]
        
        active_pairs = data["config"]["signal_generation"]["active_pairs_list"]
        assert isinstance(active_pairs, list)
        assert len(active_pairs) > 0, "Should have at least one active pair"
    
    def test_xauusd_is_enabled(self, authenticated_client):
        """Verify XAUUSD is in active pairs list"""
        response = authenticated_client.get(f"{BASE_URL}/api/admin/system-config")
        assert response.status_code == 200
        data = response.json()
        
        active_pairs = data["config"]["signal_generation"]["active_pairs_list"]
        disabled_pairs = data["config"]["signal_generation"]["disabled_pairs"]
        
        assert "XAUUSD" in active_pairs, f"XAUUSD should be in active pairs. Active: {active_pairs}, Disabled: {disabled_pairs}"
        assert "XAUUSD" not in disabled_pairs, "XAUUSD should NOT be in disabled pairs"
    
    def test_xaueur_is_enabled(self, authenticated_client):
        """Verify XAUEUR is in active pairs list"""
        response = authenticated_client.get(f"{BASE_URL}/api/admin/system-config")
        assert response.status_code == 200
        data = response.json()
        
        active_pairs = data["config"]["signal_generation"]["active_pairs_list"]
        disabled_pairs = data["config"]["signal_generation"]["disabled_pairs"]
        
        assert "XAUEUR" in active_pairs, f"XAUEUR should be in active pairs. Active: {active_pairs}, Disabled: {disabled_pairs}"
        assert "XAUEUR" not in disabled_pairs, "XAUEUR should NOT be in disabled pairs"
    
    def test_gbpjpy_is_enabled(self, authenticated_client):
        """Verify GBPJPY is in active pairs list"""
        response = authenticated_client.get(f"{BASE_URL}/api/admin/system-config")
        assert response.status_code == 200
        data = response.json()
        
        active_pairs = data["config"]["signal_generation"]["active_pairs_list"]
        disabled_pairs = data["config"]["signal_generation"]["disabled_pairs"]
        
        assert "GBPJPY" in active_pairs, f"GBPJPY should be in active pairs. Active: {active_pairs}, Disabled: {disabled_pairs}"
        assert "GBPJPY" not in disabled_pairs, "GBPJPY should NOT be in disabled pairs"
    
    def test_audusd_is_enabled(self, authenticated_client):
        """Verify AUDUSD is in active pairs list"""
        response = authenticated_client.get(f"{BASE_URL}/api/admin/system-config")
        assert response.status_code == 200
        data = response.json()
        
        active_pairs = data["config"]["signal_generation"]["active_pairs_list"]
        disabled_pairs = data["config"]["signal_generation"]["disabled_pairs"]
        
        assert "AUDUSD" in active_pairs, f"AUDUSD should be in active pairs. Active: {active_pairs}, Disabled: {disabled_pairs}"
        assert "AUDUSD" not in disabled_pairs, "AUDUSD should NOT be in disabled pairs"
    
    def test_all_requested_pairs_enabled(self, authenticated_client):
        """Verify all four requested pairs are enabled"""
        response = authenticated_client.get(f"{BASE_URL}/api/admin/system-config")
        assert response.status_code == 200
        data = response.json()
        
        active_pairs = data["config"]["signal_generation"]["active_pairs_list"]
        
        for pair in RE_ENABLED_PAIRS:
            assert pair in active_pairs, f"{pair} should be enabled but is not in active_pairs_list"
    
    def test_btcusd_is_disabled(self, authenticated_client):
        """Verify BTCUSD remains disabled (poor performance)"""
        response = authenticated_client.get(f"{BASE_URL}/api/admin/system-config")
        assert response.status_code == 200
        data = response.json()
        
        disabled_pairs = data["config"]["signal_generation"]["disabled_pairs"]
        
        assert "BTCUSD" in disabled_pairs, "BTCUSD should remain disabled due to poor performance"
    
    def test_tp_sl_config_structure(self, authenticated_client):
        """Verify TP/SL configuration is present"""
        response = authenticated_client.get(f"{BASE_URL}/api/admin/system-config")
        assert response.status_code == 200
        data = response.json()
        
        assert "tp_sl" in data["config"]
        tp_sl = data["config"]["tp_sl"]
        
        # Check key pairs have TP/SL config
        assert "forex" in tp_sl
        assert "xauusd" in tp_sl
        assert "xaueur" in tp_sl
        assert "audusd" in tp_sl
    
    def test_outcome_tracker_status(self, authenticated_client):
        """Verify outcome tracker status is included"""
        response = authenticated_client.get(f"{BASE_URL}/api/admin/system-config")
        assert response.status_code == 200
        data = response.json()
        
        assert "outcome_tracker" in data["config"]
        assert "status" in data["config"]["outcome_tracker"]


class TestSignalGenerationEndpoints:
    """Test signal generation related endpoints"""
    
    def test_generate_signal_endpoint_exists(self, authenticated_client):
        """Test that generate signal endpoint works for active pairs"""
        # Try to generate for an active pair
        response = authenticated_client.post(
            f"{BASE_URL}/api/signals/generate",
            json={"symbol": "XAUUSD"}
        )
        # It should either succeed (200) or return a valid error (not 404)
        assert response.status_code != 404, "Signal generate endpoint should exist"
    
    def test_signals_list_endpoint(self, authenticated_client):
        """Test signals list endpoint works"""
        response = authenticated_client.get(f"{BASE_URL}/api/signals")
        assert response.status_code == 200, f"Signals endpoint failed: {response.text}"
        data = response.json()
        assert isinstance(data, list), "Signals should return a list"


class TestHealthAndStatus:
    """Test health and status endpoints"""
    
    def test_health_check(self, api_client):
        """Verify health endpoint returns correct status"""
        response = api_client.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        
        assert data["status"] == "healthy"
        assert data["database"] == "healthy"
        assert "signal_tracker" in data
        assert data["version"] == "2.0.0"

"""
Test Signal endpoints
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://gold-signal-debug.preview.emergentagent.com').rstrip('/')


class TestSignalEndpoints:
    """Test signal-related endpoints"""
    
    def test_get_signals_requires_auth(self, api_client):
        """Test signals endpoint requires authentication"""
        response = api_client.get(f"{BASE_URL}/api/signals")
        assert response.status_code in [401, 403]
    
    def test_get_signals_authenticated(self, authenticated_client):
        """Test signals endpoint returns data when authenticated"""
        response = authenticated_client.get(f"{BASE_URL}/api/signals?limit=10")
        assert response.status_code == 200
        data = response.json()
        
        # Should return a list
        assert isinstance(data, list)
        
        # If there are signals, verify structure
        if len(data) > 0:
            signal = data[0]
            assert "id" in signal
            assert "pair" in signal
            assert "type" in signal
            assert "entry_price" in signal
            assert "tp_levels" in signal
            assert "sl_price" in signal
    
    def test_get_stats_requires_auth(self, api_client):
        """Test stats endpoint requires authentication"""
        response = api_client.get(f"{BASE_URL}/api/stats")
        assert response.status_code in [401, 403]
    
    def test_get_stats_authenticated(self, authenticated_client):
        """Test stats endpoint returns data when authenticated"""
        response = authenticated_client.get(f"{BASE_URL}/api/stats")
        assert response.status_code == 200
        data = response.json()
        
        assert "total_signals" in data
        assert "active_signals" in data
        assert "win_rate" in data
    
    def test_get_active_signals(self, authenticated_client):
        """Test active signals endpoint"""
        response = authenticated_client.get(f"{BASE_URL}/api/signals/active")
        assert response.status_code == 200
        data = response.json()
        
        assert data["success"] == True
        assert "count" in data
        assert "signals" in data

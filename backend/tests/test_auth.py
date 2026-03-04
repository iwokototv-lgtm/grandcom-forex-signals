"""
Test Authentication and User Role functionality
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://grandcom-trading.preview.emergentagent.com').rstrip('/')
ADMIN_EMAIL = "admin@forexsignals.com"
ADMIN_PASSWORD = "Admin@2024!Forex"


class TestAuthentication:
    """Test auth endpoints"""
    
    def test_health_endpoint(self, api_client):
        """Test health endpoint is accessible"""
        response = api_client.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["database"] == "healthy"
    
    def test_admin_login_returns_token_and_role(self, api_client):
        """Test admin login returns correct role"""
        response = api_client.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200
        data = response.json()
        
        # Verify token is returned
        assert "access_token" in data
        assert "token_type" in data
        assert data["token_type"] == "bearer"
        
        # Verify user data includes role
        assert "user" in data
        user = data["user"]
        assert user["email"] == ADMIN_EMAIL
        assert user["role"] == "admin", "Admin user should have role='admin'"
        assert "subscription_tier" in user
    
    def test_login_invalid_credentials(self, api_client):
        """Test login fails with invalid credentials"""
        response = api_client.post(f"{BASE_URL}/api/auth/login", json={
            "email": "invalid@test.com",
            "password": "wrongpassword"
        })
        assert response.status_code == 401
    
    def test_auth_me_returns_role(self, authenticated_client):
        """Test /api/auth/me returns role field"""
        response = authenticated_client.get(f"{BASE_URL}/api/auth/me")
        assert response.status_code == 200
        data = response.json()
        
        # Verify role is returned in the response
        assert "role" in data, "Role field should be present in /api/auth/me response"
        assert data["role"] == "admin"
        assert data["email"] == ADMIN_EMAIL
    
    def test_protected_endpoint_without_token(self, api_client):
        """Test protected endpoint fails without token"""
        response = api_client.get(f"{BASE_URL}/api/signals")
        assert response.status_code in [401, 403]

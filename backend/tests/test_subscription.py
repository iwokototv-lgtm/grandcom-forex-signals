"""
Test Subscription endpoints - packages, current subscription, checkout
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://grandcom-trading.preview.emergentagent.com').rstrip('/')


class TestSubscriptionEndpoints:
    """Test subscription backend endpoints"""
    
    def test_get_subscription_packages_no_auth(self, api_client):
        """Test packages endpoint works without auth"""
        response = api_client.get(f"{BASE_URL}/api/subscriptions/packages")
        assert response.status_code == 200
        data = response.json()
        
        assert data["success"] == True
        assert "packages" in data
        packages = data["packages"]
        
        # Verify all expected packages exist
        expected_packages = ["pro_monthly", "pro_yearly", "premium_monthly", "premium_yearly"]
        for pkg_id in expected_packages:
            assert pkg_id in packages, f"Package {pkg_id} not found"
            pkg = packages[pkg_id]
            assert "tier" in pkg
            assert "price" in pkg
            assert "features" in pkg
            assert pkg["price"] > 0
    
    def test_get_subscription_packages_structure(self, api_client):
        """Test package structure has all required fields"""
        response = api_client.get(f"{BASE_URL}/api/subscriptions/packages")
        data = response.json()
        
        for pkg_id, pkg in data["packages"].items():
            assert "tier" in pkg
            assert "name" in pkg
            assert "price" in pkg
            assert "currency" in pkg
            assert "duration_days" in pkg
            assert "features" in pkg
            assert isinstance(pkg["features"], list)
    
    def test_tier_features_included(self, api_client):
        """Test tier_features are returned with packages"""
        response = api_client.get(f"{BASE_URL}/api/subscriptions/packages")
        data = response.json()
        
        assert "tier_features" in data
        features = data["tier_features"]
        
        # Verify all tiers have features
        assert "free" in features
        assert "pro" in features
        assert "premium" in features
        
        # Verify free tier limitations
        free_features = features["free"]
        assert free_features["max_signals_per_day"] == 3
        assert free_features["push_notifications"] == False
    
    def test_get_current_subscription_requires_auth(self, api_client):
        """Test current subscription endpoint requires auth"""
        response = api_client.get(f"{BASE_URL}/api/subscriptions/current")
        assert response.status_code in [401, 403]
    
    def test_get_current_subscription_authenticated(self, authenticated_client):
        """Test current subscription returns proper data"""
        response = authenticated_client.get(f"{BASE_URL}/api/subscriptions/current")
        assert response.status_code == 200
        data = response.json()
        
        assert data["success"] == True
        assert "tier" in data
        assert "features" in data
    
    def test_create_checkout_session_requires_auth(self, api_client):
        """Test checkout session creation requires auth"""
        response = api_client.post(f"{BASE_URL}/api/subscriptions/create-checkout-session",
                                   json={"package_id": "pro_monthly"})
        assert response.status_code in [401, 403]
    
    def test_create_checkout_session_valid_package(self, authenticated_client):
        """Test checkout session creation with valid package"""
        response = authenticated_client.post(
            f"{BASE_URL}/api/subscriptions/create-checkout-session",
            json={"package_id": "pro_monthly"}
        )
        assert response.status_code == 200
        data = response.json()
        
        assert data["success"] == True
        assert "checkout_url" in data
        assert "session_id" in data
        assert data["checkout_url"].startswith("https://checkout.stripe.com")
    
    def test_create_checkout_session_invalid_package(self, authenticated_client):
        """Test checkout session with invalid package returns error"""
        response = authenticated_client.post(
            f"{BASE_URL}/api/subscriptions/create-checkout-session",
            json={"package_id": "invalid_package_xyz"}
        )
        data = response.json()
        
        # Should return success=False for invalid package
        assert data["success"] == False
        assert "error" in data

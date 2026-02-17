#!/usr/bin/env python3
"""
Grandcom Forex Signals Pro Backend API Tests
Tests all backend endpoints for authentication and functionality
"""

import requests
import json
import sys
from datetime import datetime

# Base URL from frontend environment - using external URL for testing
BASE_URL = "https://grandcom-alerts.preview.emergentagent.com/api"

class ForexSignalsAPITester:
    def __init__(self, base_url):
        self.base_url = base_url
        self.access_token = None
        self.test_results = []
        
    def log_test(self, test_name, success, message, details=None):
        """Log test results"""
        status = "✅ PASS" if success else "❌ FAIL"
        result = {
            "test": test_name,
            "status": status,
            "message": message,
            "details": details,
            "timestamp": datetime.now().isoformat()
        }
        self.test_results.append(result)
        print(f"{status}: {test_name} - {message}")
        if details:
            print(f"   Details: {details}")
        print()

    def test_health_check_unauthorized(self):
        """Test 1: Health Check without authentication (should fail with 403)"""
        try:
            response = requests.get(f"{self.base_url}/stats", timeout=10)
            
            if response.status_code == 403:
                self.log_test("Health Check (Unauthorized)", True, "Correctly returned 403 Forbidden")
            elif response.status_code == 401:
                self.log_test("Health Check (Unauthorized)", True, "Correctly returned 401 Unauthorized")
            else:
                self.log_test("Health Check (Unauthorized)", False, 
                            f"Expected 403/401, got {response.status_code}", 
                            response.text[:200])
                
        except requests.exceptions.RequestException as e:
            self.log_test("Health Check (Unauthorized)", False, f"Request failed: {str(e)}")

    def test_user_login(self):
        """Test 2: User Login with admin credentials"""
        try:
            login_data = {
                "email": "admin@forexsignals.com",
                "password": "Admin@2024!Forex"
            }
            
            response = requests.post(
                f"{self.base_url}/auth/login",
                json=login_data,
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if "access_token" in data:
                    self.access_token = data["access_token"]
                    user_info = data.get("user", {})
                    self.log_test("User Login", True, 
                                f"Successfully logged in as {user_info.get('email', 'Unknown')}")
                else:
                    self.log_test("User Login", False, "No access_token in response", data)
            else:
                self.log_test("User Login", False, 
                            f"Login failed with status {response.status_code}", 
                            response.text[:300])
                            
        except requests.exceptions.RequestException as e:
            self.log_test("User Login", False, f"Request failed: {str(e)}")

    def test_get_signals(self):
        """Test 3: Get Signals with authentication"""
        if not self.access_token:
            self.log_test("Get Signals", False, "No access token available - login failed")
            return
            
        try:
            headers = {"Authorization": f"Bearer {self.access_token}"}
            response = requests.get(f"{self.base_url}/signals", headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    signal_count = len(data)
                    self.log_test("Get Signals", True, 
                                f"Successfully retrieved {signal_count} signals")
                    
                    # Test signal structure if signals exist
                    if signal_count > 0:
                        sample_signal = data[0]
                        required_fields = ["id", "pair", "type", "entry_price", "tp_levels", "sl_price"]
                        missing_fields = [field for field in required_fields if field not in sample_signal]
                        
                        if not missing_fields:
                            self.log_test("Signal Structure Validation", True, 
                                        "All required fields present in signals")
                        else:
                            self.log_test("Signal Structure Validation", False, 
                                        f"Missing fields: {missing_fields}")
                else:
                    self.log_test("Get Signals", False, "Response is not a list", str(data)[:200])
            else:
                self.log_test("Get Signals", False, 
                            f"Request failed with status {response.status_code}", 
                            response.text[:300])
                            
        except requests.exceptions.RequestException as e:
            self.log_test("Get Signals", False, f"Request failed: {str(e)}")

    def test_get_stats_authenticated(self):
        """Test 4: Get Stats with authentication"""
        if not self.access_token:
            self.log_test("Get Stats (Authenticated)", False, "No access token available - login failed")
            return
            
        try:
            headers = {"Authorization": f"Bearer {self.access_token}"}
            response = requests.get(f"{self.base_url}/stats", headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                required_stats = ["total_signals", "active_signals", "win_rate"]
                
                if all(stat in data for stat in required_stats):
                    self.log_test("Get Stats (Authenticated)", True, 
                                f"Stats retrieved: {data.get('total_signals', 0)} total signals, " +
                                f"{data.get('win_rate', 0)}% win rate")
                else:
                    missing_stats = [stat for stat in required_stats if stat not in data]
                    self.log_test("Get Stats (Authenticated)", False, 
                                f"Missing stats: {missing_stats}", data)
            else:
                self.log_test("Get Stats (Authenticated)", False, 
                            f"Request failed with status {response.status_code}", 
                            response.text[:300])
                            
        except requests.exceptions.RequestException as e:
            self.log_test("Get Stats (Authenticated)", False, f"Request failed: {str(e)}")

    def test_user_profile(self):
        """Test 5: Get User Profile"""
        if not self.access_token:
            self.log_test("User Profile", False, "No access token available - login failed")
            return
            
        try:
            headers = {"Authorization": f"Bearer {self.access_token}"}
            response = requests.get(f"{self.base_url}/auth/me", headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                required_fields = ["id", "email", "subscription_tier"]
                
                if all(field in data for field in required_fields):
                    self.log_test("User Profile", True, 
                                f"Profile retrieved for {data.get('email')} " +
                                f"({data.get('subscription_tier')} tier)")
                else:
                    missing_fields = [field for field in required_fields if field not in data]
                    self.log_test("User Profile", False, 
                                f"Missing profile fields: {missing_fields}", data)
            else:
                self.log_test("User Profile", False, 
                            f"Request failed with status {response.status_code}", 
                            response.text[:300])
                            
        except requests.exceptions.RequestException as e:
            self.log_test("User Profile", False, f"Request failed: {str(e)}")

    def test_additional_endpoints(self):
        """Test additional endpoints for completeness"""
        if not self.access_token:
            return
            
        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        # Test signal generation trigger (admin only)
        try:
            response = requests.post(f"{self.base_url}/signals/generate", 
                                   headers=headers, timeout=10)
            if response.status_code in [200, 201]:
                self.log_test("Signal Generation Trigger", True, "Signal generation triggered successfully")
            else:
                self.log_test("Signal Generation Trigger", False, 
                            f"Failed with status {response.status_code}", response.text[:200])
        except requests.exceptions.RequestException as e:
            self.log_test("Signal Generation Trigger", False, f"Request failed: {str(e)}")

    def run_all_tests(self):
        """Run all tests in sequence"""
        print(f"🚀 Starting Grandcom Forex Signals Pro Backend API Tests")
        print(f"📍 Base URL: {self.base_url}")
        print(f"⏰ Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 80)
        print()
        
        # Run tests in order
        self.test_health_check_unauthorized()
        self.test_user_login()
        self.test_get_signals()
        self.test_get_stats_authenticated()
        self.test_user_profile()
        self.test_additional_endpoints()
        
        # Summary
        print("=" * 80)
        print("📊 TEST SUMMARY")
        print("=" * 80)
        
        passed = sum(1 for result in self.test_results if "✅ PASS" in result["status"])
        failed = sum(1 for result in self.test_results if "❌ FAIL" in result["status"])
        total = len(self.test_results)
        
        print(f"Total Tests: {total}")
        print(f"Passed: {passed}")
        print(f"Failed: {failed}")
        print(f"Success Rate: {(passed/total)*100:.1f}%" if total > 0 else "0%")
        print()
        
        if failed > 0:
            print("❌ FAILED TESTS:")
            for result in self.test_results:
                if "❌ FAIL" in result["status"]:
                    print(f"  - {result['test']}: {result['message']}")
            print()
        
        return passed, failed, total

def main():
    """Main test execution"""
    tester = ForexSignalsAPITester(BASE_URL)
    passed, failed, total = tester.run_all_tests()
    
    # Exit with appropriate code
    sys.exit(0 if failed == 0 else 1)

if __name__ == "__main__":
    main()
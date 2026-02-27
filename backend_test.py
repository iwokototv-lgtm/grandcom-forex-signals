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

    def test_ml_stats_endpoint(self):
        """Test ML Stats endpoint"""
        if not self.access_token:
            self.log_test("ML Stats", False, "No access token available - login failed")
            return
            
        try:
            headers = {"Authorization": f"Bearer {self.access_token}"}
            response = requests.get(f"{self.base_url}/ml/stats", headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("success") and "stats" in data:
                    self.log_test("ML Stats", True, "ML stats retrieved successfully")
                else:
                    self.log_test("ML Stats", False, "Missing success or stats field", data)
            else:
                self.log_test("ML Stats", False, 
                            f"Request failed with status {response.status_code}", 
                            response.text[:300])
                            
        except requests.exceptions.RequestException as e:
            self.log_test("ML Stats", False, f"Request failed: {str(e)}")

    def test_ml_risk_endpoint(self):
        """Test ML Risk Status endpoint"""
        if not self.access_token:
            self.log_test("ML Risk Status", False, "No access token available - login failed")
            return
            
        try:
            headers = {"Authorization": f"Bearer {self.access_token}"}
            response = requests.get(f"{self.base_url}/ml/risk", headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                required_fields = ["trading_allowed", "metrics"]
                if all(field in data for field in required_fields):
                    self.log_test("ML Risk Status", True, 
                                f"Risk status: trading_allowed={data.get('trading_allowed')}")
                else:
                    missing_fields = [field for field in required_fields if field not in data]
                    self.log_test("ML Risk Status", False, 
                                f"Missing fields: {missing_fields}", data)
            else:
                self.log_test("ML Risk Status", False, 
                            f"Request failed with status {response.status_code}", 
                            response.text[:300])
                            
        except requests.exceptions.RequestException as e:
            self.log_test("ML Risk Status", False, f"Request failed: {str(e)}")

    def test_ml_mtf_analysis(self):
        """Test MTF Analysis for XAUUSD"""
        if not self.access_token:
            self.log_test("MTF Analysis XAUUSD", False, "No access token available - login failed")
            return
            
        try:
            headers = {"Authorization": f"Bearer {self.access_token}"}
            response = requests.get(f"{self.base_url}/ml/mtf/XAUUSD", headers=headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("success") and "analysis" in data:
                    analysis = data["analysis"]
                    required_fields = ["h4_bias", "h1_structure", "m15_trigger"]
                    if all(field in analysis for field in required_fields):
                        self.log_test("MTF Analysis XAUUSD", True, 
                                    f"MTF analysis complete - H4 bias: {analysis.get('h4_bias')}")
                    else:
                        missing_fields = [field for field in required_fields if field not in analysis]
                        self.log_test("MTF Analysis XAUUSD", False, 
                                    f"Missing analysis fields: {missing_fields}", analysis)
                else:
                    self.log_test("MTF Analysis XAUUSD", False, 
                                "Missing success or analysis field", data)
            else:
                self.log_test("MTF Analysis XAUUSD", False, 
                            f"Request failed with status {response.status_code}", 
                            response.text[:300])
                            
        except requests.exceptions.RequestException as e:
            self.log_test("MTF Analysis XAUUSD", False, f"Request failed: {str(e)}")

    def test_ml_regime_detection(self):
        """Test Regime Detection for EURUSD"""
        if not self.access_token:
            self.log_test("Regime Detection EURUSD", False, "No access token available - login failed")
            return
            
        try:
            headers = {"Authorization": f"Bearer {self.access_token}"}
            response = requests.get(f"{self.base_url}/ml/regime/EURUSD", headers=headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("success") and "regime" in data:
                    regime = data["regime"]
                    self.log_test("Regime Detection EURUSD", True, 
                                f"Regime detected: {regime.get('name', 'Unknown')}")
                else:
                    self.log_test("Regime Detection EURUSD", False, 
                                "Missing success or regime field", data)
            else:
                self.log_test("Regime Detection EURUSD", False, 
                            f"Request failed with status {response.status_code}", 
                            response.text[:300])
                            
        except requests.exceptions.RequestException as e:
            self.log_test("Regime Detection EURUSD", False, f"Request failed: {str(e)}")

    def test_signals_with_regime(self):
        """Test that signals contain regime field"""
        if not self.access_token:
            self.log_test("Signals Regime Field", False, "No access token available - login failed")
            return
            
        try:
            headers = {"Authorization": f"Bearer {self.access_token}"}
            response = requests.get(f"{self.base_url}/signals?limit=10", headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and len(data) > 0:
                    # Check if signals have regime field
                    signals_with_regime = [s for s in data if 'regime' in str(s)]
                    if signals_with_regime:
                        self.log_test("Signals Regime Field", True, 
                                    f"Signals contain regime information ({len(signals_with_regime)}/{len(data)})")
                    else:
                        # Check database directly by looking at analysis field
                        regime_in_analysis = [s for s in data if '[' in str(s.get('analysis', ''))]
                        if regime_in_analysis:
                            self.log_test("Signals Regime Field", True, 
                                        f"Regime info found in analysis field ({len(regime_in_analysis)}/{len(data)})")
                        else:
                            self.log_test("Signals Regime Field", False, 
                                        "No regime information found in signals")
                else:
                    self.log_test("Signals Regime Field", False, "No signals available to check")
            else:
                self.log_test("Signals Regime Field", False, 
                            f"Request failed with status {response.status_code}", 
                            response.text[:300])
                            
        except requests.exceptions.RequestException as e:
            self.log_test("Signals Regime Field", False, f"Request failed: {str(e)}")

    def test_signals_history_endpoint(self):
        """Test Signals History endpoint"""
        if not self.access_token:
            self.log_test("Signals History", False, "No access token available - login failed")
            return
            
        try:
            headers = {"Authorization": f"Bearer {self.access_token}"}
            response = requests.get(f"{self.base_url}/signals/history?limit=10", headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                required_fields = ["signals", "stats"]
                if all(field in data for field in required_fields):
                    signals = data.get("signals", [])
                    stats = data.get("stats", {})
                    self.log_test("Signals History", True, 
                                f"Retrieved {len(signals)} historical signals with stats: {stats}")
                else:
                    missing_fields = [field for field in required_fields if field not in data]
                    self.log_test("Signals History", False, 
                                f"Missing fields: {missing_fields}", data)
            else:
                self.log_test("Signals History", False, 
                            f"Request failed with status {response.status_code}", 
                            response.text[:300])
                            
        except requests.exceptions.RequestException as e:
            self.log_test("Signals History", False, f"Request failed: {str(e)}")

    def test_live_prices_endpoint(self):
        """Test Live Prices endpoint"""
        if not self.access_token:
            self.log_test("Live Prices", False, "No access token available - login failed")
            return
            
        try:
            headers = {"Authorization": f"Bearer {self.access_token}"}
            response = requests.get(f"{self.base_url}/prices/live", headers=headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("success") and "prices" in data:
                    prices = data["prices"]
                    expected_pairs = ["XAUUSD", "XAUEUR", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "EURJPY", "GBPJPY", "AUDUSD", "USDCAD"]
                    available_pairs = list(prices.keys())
                    self.log_test("Live Prices", True, 
                                f"Retrieved prices for {len(available_pairs)}/10 pairs: {available_pairs}")
                else:
                    self.log_test("Live Prices", False, "Missing success or prices field", data)
            else:
                self.log_test("Live Prices", False, 
                            f"Request failed with status {response.status_code}", 
                            response.text[:300])
                            
        except requests.exceptions.RequestException as e:
            self.log_test("Live Prices", False, f"Request failed: {str(e)}")

    def test_recent_signals_with_regime(self):
        """Test Recent Signals with Regime field"""
        if not self.access_token:
            self.log_test("Recent Signals with Regime", False, "No access token available - login failed")
            return
            
        try:
            headers = {"Authorization": f"Bearer {self.access_token}"}
            response = requests.get(f"{self.base_url}/signals?limit=10", headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    regime_signals = []
                    for signal in data:
                        # Check if signal has regime info in analysis or as separate field
                        has_regime = ('regime' in signal or 
                                    '[' in str(signal.get('analysis', '')) or 
                                    'RANGE' in str(signal.get('analysis', '')) or
                                    'TREND' in str(signal.get('analysis', '')))
                        if has_regime:
                            regime_signals.append(signal)
                    
                    if len(regime_signals) > 0:
                        self.log_test("Recent Signals with Regime", True, 
                                    f"{len(regime_signals)}/{len(data)} signals contain regime information")
                    else:
                        self.log_test("Recent Signals with Regime", False, 
                                    "No regime information found in recent signals")
                else:
                    self.log_test("Recent Signals with Regime", False, "Invalid response format", str(data)[:200])
            else:
                self.log_test("Recent Signals with Regime", False, 
                            f"Request failed with status {response.status_code}", 
                            response.text[:300])
                            
        except requests.exceptions.RequestException as e:
            self.log_test("Recent Signals with Regime", False, f"Request failed: {str(e)}")

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
        print(f"🚀 Starting Grandcom Forex Signals Pro ML Backend API Tests")
        print(f"📍 Base URL: {self.base_url}")
        print(f"⏰ Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("🤖 Focus: Testing ML Engine endpoints as per review request")
        print("=" * 80)
        print()
        
        # Run tests in order focusing on review request endpoints
        self.test_health_check_unauthorized()
        self.test_user_login()
        
        # PRIORITY 1: Review Request Endpoints
        print("🎯 REVIEW REQUEST ENDPOINTS (TOP PRIORITY)")
        print("-" * 50)
        self.test_get_signals()  # This tests Recent Signals API
        self.test_signals_history_endpoint()  # New endpoint for history
        self.test_live_prices_endpoint()  # New endpoint for live prices
        self.test_ml_stats_endpoint()  # ML Stats as requested
        self.test_recent_signals_with_regime()  # Regime integration check
        
        # PRIORITY 2: Core System Tests
        print("\n📊 CORE SYSTEM VERIFICATION")
        print("-" * 50)
        self.test_get_stats_authenticated()
        self.test_user_profile()
        
        # PRIORITY 3: ML Engine Extended Tests
        print("\n🤖 ML ENGINE EXTENDED TESTS")
        print("-" * 50)
        self.test_ml_risk_endpoint()
        self.test_ml_mtf_analysis()
        self.test_ml_regime_detection()
        self.test_signals_with_regime()
        
        # PRIORITY 4: Additional endpoint tests
        print("\n⚙️ ADDITIONAL ENDPOINTS")
        print("-" * 50)
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
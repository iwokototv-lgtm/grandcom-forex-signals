#!/usr/bin/env python3
"""
Advanced ML Endpoints Testing for Grandcom Forex Signals Pro
Testing the new advanced ML endpoints as per review request
"""

import requests
import json
import sys
from typing import Dict, Any

# Configuration
BASE_URL = "https://gold-signal-debug.preview.emergentagent.com"
ADMIN_EMAIL = "admin@forexsignals.com"
ADMIN_PASSWORD = "Admin@2024!Forex"

def log_test(test_name: str, status: str, details: str = ""):
    """Log test results with consistent formatting"""
    status_emoji = "✅" if status == "PASS" else "❌"
    print(f"{status_emoji} {test_name}: {status}")
    if details:
        print(f"   Details: {details}")
    print()

def make_request(method: str, endpoint: str, headers: Dict = None, data: Dict = None) -> tuple:
    """Make HTTP request and return response data and status"""
    url = f"{BASE_URL}/api{endpoint}"
    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, timeout=30)
        elif method.upper() == "POST":
            response = requests.post(url, headers=headers, json=data, timeout=30)
        else:
            return None, f"Unsupported method: {method}"
        
        return response.json(), response.status_code
    except requests.exceptions.RequestException as e:
        return None, f"Request failed: {str(e)}"
    except json.JSONDecodeError as e:
        return None, f"JSON decode error: {str(e)}"

def test_admin_login():
    """Test 1: Admin Login Authentication"""
    print("🔐 TESTING: Admin Login Authentication")
    
    login_data = {
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD
    }
    
    response_data, status_code = make_request("POST", "/auth/login", data=login_data)
    
    if status_code == 200 and response_data:
        if "access_token" in response_data:
            token = response_data["access_token"]
            user = response_data.get("user", {})
            log_test("Admin Login", "PASS", 
                   f"Successfully authenticated. User: {user.get('email')}, Tier: {user.get('subscription_tier')}")
            return token
        else:
            log_test("Admin Login", "FAIL", "No access_token in response")
            return None
    else:
        log_test("Admin Login", "FAIL", f"Status: {status_code}, Response: {response_data}")
        return None

def test_smc_analysis(token: str):
    """Test 2: SMC Analysis for XAUUSD"""
    print("📊 TESTING: SMC Analysis for XAUUSD")
    
    headers = {"Authorization": f"Bearer {token}"}
    response_data, status_code = make_request("GET", "/ml/smc/XAUUSD", headers=headers)
    
    if status_code == 200 and response_data:
        if response_data.get("success") and "analysis" in response_data:
            analysis = response_data["analysis"]
            # Check for key SMC components
            expected_keys = ["order_blocks", "fair_value_gaps", "liquidity_sweep"]
            found_keys = [key for key in expected_keys if key in analysis]
            
            log_test("SMC Analysis", "PASS", 
                   f"SMC components found: {found_keys}. Analysis structure valid.")
            return True
        else:
            log_test("SMC Analysis", "FAIL", f"Missing analysis or success=False: {response_data}")
            return False
    else:
        log_test("SMC Analysis", "FAIL", f"Status: {status_code}, Response: {response_data}")
        return False

def test_quality_filter(token: str):
    """Test 3: Quality Filter Status"""
    print("🔍 TESTING: Quality Filter Status")
    
    headers = {"Authorization": f"Bearer {token}"}
    response_data, status_code = make_request("GET", "/ml/quality-filter", headers=headers)
    
    if status_code == 200 and response_data:
        if response_data.get("success") and "filter_status" in response_data:
            filter_status = response_data["filter_status"]
            log_test("Quality Filter", "PASS", 
                   f"Filter status retrieved successfully. Data: {json.dumps(filter_status, indent=2)}")
            return True
        else:
            log_test("Quality Filter", "FAIL", f"Missing filter_status or success=False: {response_data}")
            return False
    else:
        log_test("Quality Filter", "FAIL", f"Status: {status_code}, Response: {response_data}")
        return False

def test_full_analysis(token: str):
    """Test 4: Full Analysis for EURUSD"""
    print("🎯 TESTING: Full Analysis for EURUSD")
    
    headers = {"Authorization": f"Bearer {token}"}
    response_data, status_code = make_request("GET", "/ml/full-analysis/EURUSD", headers=headers)
    
    if status_code == 200 and response_data:
        if response_data.get("success") and "analysis" in response_data:
            analysis = response_data["analysis"]
            # Check for key components
            expected_components = ["regime", "mtf", "smc", "quality_assessment"]
            found_components = [comp for comp in expected_components if comp in analysis]
            
            log_test("Full Analysis", "PASS", 
                   f"Analysis components found: {found_components}. Symbol: {analysis.get('symbol')}")
            return True
        else:
            log_test("Full Analysis", "FAIL", f"Missing analysis or success=False: {response_data}")
            return False
    else:
        log_test("Full Analysis", "FAIL", f"Status: {status_code}, Response: {response_data}")
        return False

def test_live_prices(token: str):
    """Test 5: Live Prices for All Pairs"""
    print("💱 TESTING: Live Prices")
    
    headers = {"Authorization": f"Bearer {token}"}
    response_data, status_code = make_request("GET", "/prices/live", headers=headers)
    
    if status_code == 200 and response_data:
        if response_data.get("success") and "prices" in response_data:
            prices = response_data["prices"]
            expected_pairs = ["XAUUSD", "XAUEUR", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "EURJPY", "GBPJPY", "AUDUSD", "USDCAD"]
            available_pairs = [pair for pair in expected_pairs if pair in prices and "price" in prices[pair]]
            
            log_test("Live Prices", "PASS", 
                   f"Retrieved prices for {len(available_pairs)}/10 pairs. Available: {available_pairs}")
            return True
        else:
            log_test("Live Prices", "FAIL", f"Missing prices or success=False: {response_data}")
            return False
    else:
        log_test("Live Prices", "FAIL", f"Status: {status_code}, Response: {response_data}")
        return False

def main():
    """Run all advanced ML endpoint tests"""
    print("🚀 GRANDCOM FOREX SIGNALS PRO - ADVANCED ML ENDPOINTS TESTING")
    print("=" * 70)
    print(f"Backend URL: {BASE_URL}")
    print(f"Admin Email: {ADMIN_EMAIL}")
    print("=" * 70)
    print()
    
    # Test results tracking
    test_results = {
        "Admin Login": False,
        "SMC Analysis": False, 
        "Quality Filter": False,
        "Full Analysis": False,
        "Live Prices": False
    }
    
    # Test 1: Admin Login
    token = test_admin_login()
    if token:
        test_results["Admin Login"] = True
        
        # Test 2: SMC Analysis
        test_results["SMC Analysis"] = test_smc_analysis(token)
        
        # Test 3: Quality Filter
        test_results["Quality Filter"] = test_quality_filter(token)
        
        # Test 4: Full Analysis
        test_results["Full Analysis"] = test_full_analysis(token)
        
        # Test 5: Live Prices
        test_results["Live Prices"] = test_live_prices(token)
    else:
        print("❌ Cannot proceed with other tests due to authentication failure")
    
    # Final Summary
    print("=" * 70)
    print("📋 FINAL TEST SUMMARY")
    print("=" * 70)
    
    passed = sum(test_results.values())
    total = len(test_results)
    
    for test_name, result in test_results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} {test_name}")
    
    print()
    print(f"📊 OVERALL RESULT: {passed}/{total} tests passed ({passed/total*100:.1f}%)")
    
    if passed == total:
        print("🎉 ALL ADVANCED ML ENDPOINTS ARE WORKING CORRECTLY!")
        return 0
    else:
        print(f"⚠️  {total - passed} endpoints need attention")
        return 1

if __name__ == "__main__":
    sys.exit(main())
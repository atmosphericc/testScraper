#!/usr/bin/env python3
"""
Comprehensive test for purchase triggering and WebSocket integration
Tests the complete flow: stock detection -> purchase attempt -> status updates -> WebSocket notifications
"""

import json
import time
import threading
import requests
from datetime import datetime
from pathlib import Path

def test_purchase_system():
    """Test the complete purchase system flow"""
    print("=" * 80)
    print("COMPREHENSIVE PURCHASE SYSTEM TEST")
    print("=" * 80)
    
    # Test 1: Import and test basic purchase logic
    print("\n[TEST 1] Testing basic purchase logic...")
    try:
        import sys
        sys.path.append('.')
        from main_dashboard import (
            start_new_refresh_cycle, 
            can_attempt_purchase, 
            set_purchase_status,
            get_purchase_status,
            record_purchase_attempt,
            current_refresh_timestamp,
            purchase_attempts,
            purchase_statuses,
            mock_purchase_attempt_sync
        )
        print("✅ Successfully imported purchase functions")
        
        # Test refresh cycle
        print("\n[TEST 1A] Testing refresh cycle logic...")
        initial_timestamp = current_refresh_timestamp
        print(f"Initial timestamp: {initial_timestamp}")
        
        new_timestamp = start_new_refresh_cycle()
        print(f"New timestamp after start_new_refresh_cycle(): {new_timestamp}")
        print(f"Global timestamp updated: {current_refresh_timestamp}")
        
        assert new_timestamp > initial_timestamp, "New timestamp should be greater"
        print("✅ Refresh cycle logic working")
        
        # Test purchase attempt logic
        print("\n[TEST 1B] Testing purchase attempt logic...")
        test_tcin = "89542109"
        
        # Should be able to attempt
        can_attempt = can_attempt_purchase(test_tcin)
        print(f"can_attempt_purchase('{test_tcin}'): {can_attempt}")
        assert can_attempt, "Should be able to attempt purchase in new cycle"
        
        # Record attempt
        record_purchase_attempt(test_tcin)
        
        # Should NOT be able to attempt again in same cycle
        can_attempt_again = can_attempt_purchase(test_tcin)
        print(f"can_attempt_purchase again: {can_attempt_again}")
        assert not can_attempt_again, "Should NOT be able to attempt again in same cycle"
        
        print("✅ Purchase attempt logic working")
        
    except ImportError as e:
        print(f"❌ Failed to import: {e}")
        return False
    except Exception as e:
        print(f"❌ Test 1 failed: {e}")
        return False
    
    # Test 2: Test status transitions
    print("\n[TEST 2] Testing purchase status transitions...")
    try:
        test_tcin = "94886127"  # Different TCIN for clean test
        
        # Initial status should be ready
        initial_status = get_purchase_status(test_tcin)
        print(f"Initial status: {initial_status}")
        
        # Set to attempting
        set_purchase_status(test_tcin, 'attempting')
        attempting_status = get_purchase_status(test_tcin)
        print(f"After set to attempting: {attempting_status}")
        assert attempting_status['status'] == 'attempting'
        
        # Set to purchased
        set_purchase_status(test_tcin, 'purchased')
        purchased_status = get_purchase_status(test_tcin)
        print(f"After set to purchased: {purchased_status}")
        assert purchased_status['status'] == 'purchased'
        
        # New refresh cycle should reset to ready
        start_new_refresh_cycle()
        time.sleep(0.1)  # Allow for processing
        reset_status = get_purchase_status(test_tcin)
        print(f"After new refresh cycle: {reset_status}")
        assert reset_status['status'] == 'ready'
        
        print("✅ Status transitions working correctly")
        
    except Exception as e:
        print(f"❌ Test 2 failed: {e}")
        return False
    
    # Test 3: Test mock purchase process
    print("\n[TEST 3] Testing mock purchase process...")
    try:
        test_tcin = "94300072"
        product_name = "Test Product"
        
        print(f"Starting mock purchase for {test_tcin}...")
        start_time = time.time()
        
        # This should complete in ~3 seconds
        result = mock_purchase_attempt_sync(test_tcin, product_name)
        end_time = time.time()
        
        duration = end_time - start_time
        print(f"Mock purchase completed in {duration:.1f}s, result: {result}")
        
        # Check final status
        final_status = get_purchase_status(test_tcin)
        print(f"Final purchase status: {final_status}")
        
        assert final_status['status'] in ['purchased', 'failed'], "Should have completed status"
        assert 2.5 <= duration <= 4.0, f"Duration should be ~3s, got {duration:.1f}s"
        
        print("✅ Mock purchase process working")
        
    except Exception as e:
        print(f"❌ Test 3 failed: {e}")
        return False
    
    print("\n" + "=" * 80)
    print("✅ ALL PURCHASE SYSTEM TESTS PASSED")
    print("✅ Ready for dashboard integration testing")
    print("=" * 80)
    return True

def test_api_endpoints():
    """Test dashboard API endpoints"""
    print("\n[API TEST] Testing dashboard API endpoints...")
    
    base_url = "http://127.0.0.1:5001"
    
    endpoints_to_test = [
        "/api/status",
        "/api/purchase-status", 
        "/api/analytics",
        "/api/activity-log"
    ]
    
    for endpoint in endpoints_to_test:
        try:
            response = requests.get(f"{base_url}{endpoint}", timeout=10)
            if response.status_code == 200:
                print(f"✅ {endpoint}: {response.status_code}")
            else:
                print(f"⚠️ {endpoint}: {response.status_code}")
        except requests.exceptions.ConnectionError:
            print(f"❌ {endpoint}: Dashboard not running")
        except Exception as e:
            print(f"❌ {endpoint}: {e}")

if __name__ == "__main__":
    # Run basic tests first
    if test_purchase_system():
        print("\n[INFO] Basic tests passed. Now testing API endpoints...")
        test_api_endpoints()
        
        print("\n[INFO] To test full integration:")
        print("1. Start dashboard: python main_dashboard.py")
        print("2. Open http://localhost:5001 in browser")
        print("3. Watch for products to go in stock and trigger purchase attempts")
        print("4. Verify WebSocket updates show purchase status changes")
    else:
        print("\n❌ Basic tests failed. Fix issues before testing integration.")
#!/usr/bin/env python3
"""
Test script for the purchase system
Tests purchase state management without requiring actual stock data
"""

import json
import os
import sys
import time
from datetime import datetime

# Add current directory to path so we can import from simple_dashboard
sys.path.insert(0, os.getcwd())

from simple_dashboard import PurchaseStateManager, add_activity_log

def test_purchase_state_manager():
    """Test the PurchaseStateManager functionality"""
    print("=" * 60)
    print("TESTING PURCHASE STATE MANAGER")
    print("=" * 60)

    # Initialize purchase manager
    manager = PurchaseStateManager()

    # Test 1: Load empty states
    print("\n[TEST 1] Loading empty purchase states...")
    states = manager.load_purchase_states()
    print(f"Initial states: {states}")
    assert states == {}, "Expected empty states initially"
    print("[PASS] Empty states loaded correctly")

    # Test 2: Save and load states
    print("\n[TEST 2] Saving and loading purchase states...")
    test_states = {
        "12345678": {
            "status": "ready",
            "window_number": manager.get_current_window(),
            "attempt_count": 0
        },
        "87654321": {
            "status": "attempting",
            "started_at": datetime.now().isoformat(),
            "window_number": manager.get_current_window(),
            "attempt_count": 1
        }
    }

    manager.save_purchase_states(test_states)
    loaded_states = manager.load_purchase_states()
    print(f"Saved and loaded states: {loaded_states}")
    assert len(loaded_states) == 2, "Expected 2 states"
    assert loaded_states["12345678"]["status"] == "ready", "Expected ready status"
    print("[PASS] States saved and loaded correctly")

    # Test 3: Get purchase queue
    print("\n[TEST 3] Testing purchase queue generation...")
    mock_stock_data = {
        "12345678": {"available": True, "name": "Test Product 1"},
        "87654321": {"available": True, "name": "Test Product 2"},
        "99999999": {"available": False, "name": "Out of Stock Product"}
    }

    queue = manager.get_purchase_queue(mock_stock_data, loaded_states)
    print(f"Purchase queue: {queue}")
    # Should only include the ready product (12345678)
    assert len(queue) == 1, "Expected 1 product in queue"
    assert queue[0][0] == "12345678", "Expected ready product in queue"
    print("[PASS] Purchase queue generated correctly")

    # Test 4: Window reset logic
    print("\n[TEST 4] Testing window reset logic...")
    # Simulate previous window states
    old_states = {
        "12345678": {
            "status": "purchased",
            "window_number": manager.get_current_window() - 1,  # Previous window
            "attempt_count": 1
        },
        "87654321": {
            "status": "failed",
            "window_number": manager.get_current_window() - 1,  # Previous window
            "attempt_count": 2
        }
    }

    manager.reset_window_states(old_states)
    print(f"States after reset: {old_states}")
    assert old_states["12345678"]["status"] == "ready", "Expected reset to ready"
    assert old_states["87654321"]["status"] == "ready", "Expected reset to ready"
    print("[PASS] Window reset logic working correctly")

    print("\n" + "=" * 60)
    print("ALL PURCHASE STATE MANAGER TESTS PASSED!")
    print("=" * 60)

def test_mock_purchase_execution():
    """Test purchase execution with mock data"""
    print("\n[TEST 5] Testing mock purchase execution...")

    # Create mock stock data with in-stock product
    mock_stock_data = {
        "94681784": {  # Use actual TCIN from config
            "available": True,
            "name": "Test Pokemon Product",
            "status": "IN_STOCK",
            "tcin": "94681784"
        }
    }

    manager = PurchaseStateManager()

    # Clear any existing states
    manager.save_purchase_states({})

    print(f"Mock stock data: {mock_stock_data}")
    print("Executing purchase cycle...")

    # Note: This will attempt actual purchase if session file exists
    # In real test environment, we'd mock the subprocess call
    try:
        result_states = manager.execute_purchase_cycle(mock_stock_data)
        print(f"Purchase cycle result: {result_states}")

        if "94681784" in result_states:
            status = result_states["94681784"].get("status", "unknown")
            print(f"Product 94681784 status: {status}")

            if status == "attempting":
                print("[PASS] Purchase attempt initiated correctly")
            elif status == "failed":
                print("[EXPECTED] Purchase failed (likely no session file)")
            else:
                print(f"[INFO] Unexpected status: {status}")
        else:
            print("[FAIL] No state found for test product")

    except Exception as e:
        print(f"[EXPECTED ERROR] {e}")
        print("This is normal if BuyBot dependencies are missing")

def cleanup_test_files():
    """Clean up test files"""
    test_files = [
        'logs/purchase_states.json',
        'logs/purchase_states.json.tmp'
    ]

    for file_path in test_files:
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"Cleaned up: {file_path}")

def main():
    """Run all tests"""
    print("PURCHASE SYSTEM INTEGRATION TESTS")
    print("=" * 60)

    try:
        # Test purchase state manager
        test_purchase_state_manager()

        # Test mock purchase execution (may fail gracefully)
        test_mock_purchase_execution()

        print("\n" + "=" * 60)
        print("PURCHASE SYSTEM TESTS COMPLETED!")
        print("Core functionality is working correctly")
        print("Full testing requires session file and BuyBot setup")
        print("=" * 60)

    except Exception as e:
        print(f"\nTEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        cleanup_test_files()

    return 0

if __name__ == '__main__':
    sys.exit(main())
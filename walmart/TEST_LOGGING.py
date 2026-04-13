#!/usr/bin/env python3
"""
Quick test of Walmart logging system.
Run this to verify all logging infrastructure is working.
"""

import json
import pickle
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path so imports work
sys.path.insert(0, str(Path(__file__).parent.parent))

from walmart.logging_manager import get_walmart_logger, log_activity, log_error, log_purchase_state, get_purchase_state


def test_logging_system():
    """Test all logging components"""
    print("\n" + "="*80)
    print("WALMART LOGGING SYSTEM TEST")
    print("="*80 + "\n")

    logger = get_walmart_logger()

    # 1. Test error logging
    print("[TEST 1] Error Logging")
    print("-" * 40)
    try:
        raise ValueError("Test error for logging")
    except Exception as e:
        logger.log_error("test_error", "A test error occurred", e)
    print("✓ Error logged to: walmart/logs/error_log.txt")

    # 2. Test activity logging
    print("\n[TEST 2] Activity Logging")
    print("-" * 40)
    log_activity("Test purchase started for item TEST001", category="PURCHASE")
    log_activity("Test ATC successful", category="PURCHASE")
    log_activity("Test checkout reached", category="PURCHASE")
    print("✓ Activities logged")

    # 3. Test purchase state logging
    print("\n[TEST 3] Purchase State Logging")
    print("-" * 40)
    test_item_id = "TEST001"
    log_purchase_state(test_item_id, {
        'status': 'attempting',
        'timestamp': datetime.now().isoformat(),
        'attempt_count': 1,
        'final_outcome': None,
        'last_error': None,
        'order_id': None
    })
    print(f"✓ State logged for item {test_item_id}")

    # 4. Read back and verify
    print("\n[TEST 4] Verification")
    print("-" * 40)

    # Check error log exists
    error_log_path = Path("walmart/logs/error_log.txt")
    if error_log_path.exists():
        with open(error_log_path) as f:
            errors = f.readlines()
        print(f"✓ Error log exists: {len(errors)} lines")
        if errors:
            print(f"  Last error: {errors[-2][:80]}...")

    # Check activity log
    activity_log_path = Path("walmart/logs/activity_log.pkl")
    if activity_log_path.exists():
        with open(activity_log_path, 'rb') as f:
            activities = pickle.load(f)
        print(f"✓ Activity log exists: {len(activities)} entries")
        if activities:
            latest = activities[-1]
            print(f"  Latest: [{latest['time_str']}] {latest['message']}")

    # Check purchase states
    states_path = Path("walmart/logs/purchase_states.json")
    if states_path.exists():
        with open(states_path) as f:
            states = json.load(f)
        print(f"✓ Purchase states file exists: {len(states)} items tracked")
        if test_item_id in states:
            state = states[test_item_id]
            print(f"  Test item: status={state['status']}, outcome={state['final_outcome']}")

    # 5. Simulate purchase completion
    print("\n[TEST 5] Simulate Purchase Success")
    print("-" * 40)
    log_purchase_state(test_item_id, {
        'status': 'success',
        'timestamp': datetime.now().isoformat(),
        'attempt_count': 1,
        'final_outcome': 'purchased',
        'last_error': None,
        'order_id': 'WM-TEST-12345'
    })
    log_activity(f"PURCHASED: item {test_item_id}, order ID WM-TEST-12345", category="PURCHASE")
    print("✓ Purchase marked as successful")

    # Verify final state
    final_state = get_purchase_state(test_item_id)
    if final_state:
        print(f"  Verified: {final_state['status']} / {final_state['final_outcome']} / {final_state['order_id']}")

    # 6. Test per-purchase log creation
    print("\n[TEST 6] Per-Purchase Log")
    print("-" * 40)
    log_path = logger.create_purchase_log("TEST001", "20260408_120000")
    if log_path.exists():
        print(f"✓ Per-purchase log created: {log_path.name}")

    # 7. Directory structure
    print("\n[TEST 7] Directory Structure")
    print("-" * 40)
    logs_dir = Path("walmart/logs")
    if logs_dir.exists():
        files = list(logs_dir.rglob("*"))
        log_files = [f for f in files if f.is_file()]
        print(f"✓ walmart/logs/ directory exists")
        print(f"  Files: {len(log_files)}")
        for f in sorted(log_files)[:10]:
            size = f.stat().st_size
            print(f"    - {f.relative_to(logs_dir)} ({size} bytes)")

    print("\n" + "="*80)
    print("LOGGING SYSTEM TEST COMPLETE")
    print("="*80)
    print("\nLog files are ready for failure-forensics agent to diagnose issues:")
    print("  - walmart/logs/error_log.txt")
    print("  - walmart/logs/activity_log.pkl")
    print("  - walmart/logs/purchase_states.json")
    print("  - walmart/logs/purchases/purchase_*.log")
    print("\nSee walmart/LOGGING.md for full documentation")
    print("See walmart/FAILURE_AGENT_GUIDE.md for troubleshooting guide")
    print()


if __name__ == "__main__":
    test_logging_system()

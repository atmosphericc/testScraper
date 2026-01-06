#!/usr/bin/env python3
"""
Automated Test Suite for Purchase Flow
Tests critical race conditions, state management, and product switching bugs
"""

import json
import time
import threading
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

class PurchaseFlowTester:
    def __init__(self):
        self.test_results = []
        self.purchase_states_path = "logs/purchase_states.json"
        self.backup_path = "logs/purchase_states_backup.json"

    def backup_states(self):
        """Backup current purchase states"""
        if os.path.exists(self.purchase_states_path):
            with open(self.purchase_states_path, 'r') as f:
                states = json.load(f)
            with open(self.backup_path, 'w') as f:
                json.dump(states, f, indent=2)
            return states
        return {}

    def restore_states(self):
        """Restore backed up states"""
        if os.path.exists(self.backup_path):
            with open(self.backup_path, 'r') as f:
                states = json.load(f)
            with open(self.purchase_states_path, 'w') as f:
                json.dump(states, f, indent=2)
            os.remove(self.backup_path)

    def get_states(self):
        """Read current purchase states"""
        if os.path.exists(self.purchase_states_path):
            with open(self.purchase_states_path, 'r') as f:
                return json.load(f)
        return {}

    def set_state(self, tcin, state):
        """Set a specific product state"""
        states = self.get_states()
        states[tcin] = state
        with open(self.purchase_states_path, 'w') as f:
            json.dump(states, f, indent=2)

    def count_status(self, status):
        """Count products with specific status"""
        states = self.get_states()
        return sum(1 for s in states.values() if s.get('status') == status)

    def log_test(self, test_name, passed, details=""):
        """Log test result"""
        result = "✅ PASS" if passed else "❌ FAIL"
        message = f"{result}: {test_name}"
        if details:
            message += f" - {details}"
        print(message)
        self.test_results.append({
            'test': test_name,
            'passed': passed,
            'details': details
        })

    # ==================== TEST 1: Single Purchase Enforcement ====================
    def test_single_purchase_enforcement(self):
        """Test that only 1 product can be 'attempting' at a time via process_stock_data"""
        print("\n" + "="*60)
        print("TEST 1: Single Purchase Enforcement")
        print("="*60)

        # Setup: Create 3 products all in 'ready' state
        test_states = {
            '111111': {'status': 'ready', 'tcin': '111111', 'product_title': 'Product A'},
            '222222': {'status': 'ready', 'tcin': '222222', 'product_title': 'Product B'},
            '333333': {'status': 'ready', 'tcin': '333333', 'product_title': 'Product C'}
        }

        with open(self.purchase_states_path, 'w') as f:
            json.dump(test_states, f, indent=2)

        # Create mock stock data with all 3 in stock
        mock_stock = {
            '111111': {'title': 'Product A', 'in_stock': True, 'tcin': '111111'},
            '222222': {'title': 'Product B', 'in_stock': True, 'tcin': '222222'},
            '333333': {'title': 'Product C', 'in_stock': True, 'tcin': '333333'}
        }

        # Force mock mode
        os.environ['FORCE_MOCK_MODE'] = 'true'
        from src.purchasing import BulletproofPurchaseManager
        manager = BulletproofPurchaseManager()

        # Process stock data - should only start 1 purchase due to concurrency limit
        results = manager.process_stock_data(mock_stock)

        # Check results
        states = self.get_states()
        attempting_count = sum(1 for s in states.values()
                              if s.get('status') in ['attempting', 'queued'])

        # PASS if only 1 product is attempting
        passed = attempting_count == 1 and len(results) == 1
        self.log_test(
            "Single Purchase Enforcement",
            passed,
            f"Attempting: {attempting_count}, Purchases started: {len(results)}"
        )

        return passed

    # ==================== TEST 2: Product Priority System ====================
    def test_product_priority(self):
        """Test that products are selected in priority order"""
        print("\n" + "="*60)
        print("TEST 2: Product Priority System")
        print("="*60)

        # Setup: Load product config to get priority order
        with open('config/product_config.json', 'r') as f:
            config = json.load(f)

        # Get first 3 TCINs in priority order (config['products'] is an array)
        products_list = config['products']
        tcins = [p['tcin'] for p in products_list[:3]]

        if len(tcins) < 3:
            self.log_test("Product Priority System", False, "Need at least 3 products in config")
            return False

        # Create mock stock data with all 3 in stock
        mock_stock = {}
        for i, tcin in enumerate(tcins):
            mock_stock[tcin] = {
                'title': products_list[i]['name'],
                'in_stock': True,
                'tcin': tcin
            }

        # Reset ALL states to clean slate (remove any leftover from previous tests)
        with open(self.purchase_states_path, 'w') as f:
            json.dump({}, f, indent=2)

        # Set only our test products to ready
        for tcin in tcins:
            self.set_state(tcin, {'status': 'ready', 'tcin': tcin})

        # Process stock data
        from src.purchasing import BulletproofPurchaseManager
        manager = BulletproofPurchaseManager()
        manager.process_stock_data(mock_stock)

        # Check which product started
        states = self.get_states()
        started_tcin = None
        for tcin, state in states.items():
            if state.get('status') in ['attempting', 'queued']:
                started_tcin = tcin
                break

        # PASS if highest priority product (first in list) was selected
        passed = started_tcin == tcins[0]
        self.log_test(
            "Product Priority System",
            passed,
            f"Expected: {tcins[0]}, Got: {started_tcin}"
        )

        return passed

    # ==================== TEST 3: TEST_MODE Reset Behavior ====================
    def test_testmode_reset_no_switch(self):
        """Test that TEST_MODE reset doesn't cause product switching"""
        print("\n" + "="*60)
        print("TEST 3: TEST_MODE Reset Behavior")
        print("="*60)

        # Setup: Product A completed, Product B ready
        test_states = {
            '111111': {
                'status': 'purchased',
                'tcin': '111111',
                'product_title': 'Product A',
                'completed_at': time.time()
            },
            '222222': {
                'status': 'ready',
                'tcin': '222222',
                'product_title': 'Product B'
            }
        }

        with open(self.purchase_states_path, 'w') as f:
            json.dump(test_states, f, indent=2)

        # Simulate TEST_MODE reset
        from src.purchasing import BulletproofPurchaseManager
        # Force mock mode by setting environment variable
        os.environ['FORCE_MOCK_MODE'] = 'true'
        manager = BulletproofPurchaseManager()

        # In TEST_MODE, this should reset completed purchases
        reset_count = manager.reset_completed_purchases_to_ready()

        # Now process stock with Product B in stock
        mock_stock = {
            '222222': {'title': 'Product B', 'in_stock': True, 'tcin': '222222'}
        }
        manager.process_stock_data(mock_stock)

        # Check results
        states = self.get_states()

        # Product A should be reset to ready
        product_a_ready = states.get('111111', {}).get('status') == 'ready'

        # Product B should be attempting (since it's in stock)
        product_b_attempting = states.get('222222', {}).get('status') in ['attempting', 'queued']

        # PASS if reset worked and correct product selected
        passed = product_a_ready and product_b_attempting
        self.log_test(
            "TEST_MODE Reset Behavior",
            passed,
            f"A reset: {product_a_ready}, B attempting: {product_b_attempting}"
        )

        return passed

    # ==================== TEST 4: State Persistence ====================
    def test_state_persistence(self):
        """Test that states persist correctly to file"""
        print("\n" + "="*60)
        print("TEST 4: State Persistence")
        print("="*60)

        # Setup: Create test state
        test_state = {
            '999999': {
                'status': 'attempting',
                'tcin': '999999',
                'product_title': 'Test Product',
                'started_at': time.time()
            }
        }

        with open(self.purchase_states_path, 'w') as f:
            json.dump(test_state, f, indent=2)

        # Read back
        states = self.get_states()

        # Verify
        persisted_correctly = (
            states.get('999999', {}).get('status') == 'attempting' and
            states.get('999999', {}).get('tcin') == '999999'
        )

        self.log_test(
            "State Persistence",
            persisted_correctly,
            f"State saved and loaded correctly: {persisted_correctly}"
        )

        return persisted_correctly

    # ==================== TEST 5: Concurrent In-Stock Handling ====================
    def test_concurrent_instock_handling(self):
        """Test handling multiple products coming in stock simultaneously"""
        print("\n" + "="*60)
        print("TEST 5: Concurrent In-Stock Handling")
        print("="*60)

        # Setup: 5 products all ready
        test_states = {}
        for i in range(5):
            tcin = f'{i:06d}'
            test_states[tcin] = {
                'status': 'ready',
                'tcin': tcin,
                'product_title': f'Product {i}'
            }

        with open(self.purchase_states_path, 'w') as f:
            json.dump(test_states, f, indent=2)

        # All come in stock at once
        mock_stock = {}
        for tcin in test_states:
            mock_stock[tcin] = {
                'title': test_states[tcin]['product_title'],
                'in_stock': True,
                'tcin': tcin
            }

        # Process
        from src.purchasing import BulletproofPurchaseManager
        # Force mock mode by setting environment variable
        os.environ['FORCE_MOCK_MODE'] = 'true'
        manager = BulletproofPurchaseManager()
        manager.process_stock_data(mock_stock)

        # Check only 1 started
        attempting_count = self.count_status('attempting') + self.count_status('queued')

        passed = attempting_count == 1
        self.log_test(
            "Concurrent In-Stock Handling",
            passed,
            f"Only 1 purchase started (attempting count: {attempting_count})"
        )

        return passed

    def run_all_tests(self):
        """Run all tests and report results"""
        print("\n" + "="*70)
        print("PURCHASE FLOW TEST SUITE")
        print("="*70)

        # Backup current states
        print("\n[SETUP] Backing up current purchase states...")
        original_states = self.backup_states()

        try:
            # Run tests
            tests = [
                self.test_single_purchase_enforcement,
                self.test_product_priority,
                self.test_testmode_reset_no_switch,
                self.test_state_persistence,
                self.test_concurrent_instock_handling
            ]

            for test in tests:
                try:
                    test()
                    time.sleep(0.5)  # Brief pause between tests
                except Exception as e:
                    self.log_test(test.__name__, False, f"Exception: {str(e)}")
                    import traceback
                    traceback.print_exc()

            # Report
            print("\n" + "="*70)
            print("TEST RESULTS SUMMARY")
            print("="*70)

            total = len(self.test_results)
            passed = sum(1 for r in self.test_results if r['passed'])
            failed = total - passed

            print(f"\nTotal Tests: {total}")
            print(f"✅ Passed: {passed}")
            print(f"❌ Failed: {failed}")
            print(f"Success Rate: {(passed/total*100):.1f}%")

            if failed > 0:
                print("\n" + "="*70)
                print("FAILED TESTS:")
                print("="*70)
                for result in self.test_results:
                    if not result['passed']:
                        print(f"❌ {result['test']}: {result['details']}")

            print("\n" + "="*70)

        finally:
            # Restore original states
            print("\n[CLEANUP] Restoring original purchase states...")
            self.restore_states()
            print("[CLEANUP] Complete!")

if __name__ == '__main__':
    tester = PurchaseFlowTester()
    tester.run_all_tests()

#!/usr/bin/env python3
"""
Clean Stock Monitor - Simple bulk API monitoring
Checks stock status every 15-25 seconds with clean data output
"""

import json
import time
import random
import requests
import html
from datetime import datetime
from pathlib import Path
import logging

class StockMonitor:
    def __init__(self):
        self.api_endpoint = 'https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1'
        self.api_keys = [
            "ff457966e64d5e877fdbad070f276d18ecec4a01",
            "9f36aeafbe60771e321a7cc95a78140772ab3e96"
        ]
        self.last_check_time = None

        # TEST MODE: Comprehensive testing infrastructure
        self.test_mode = False
        self.test_scenario = None
        self.test_cycle_count = 0
        self.test_start_time = None
        self.test_scenarios = {
            'alternating': self._test_alternating_stock,
            'rapid_changes': self._test_rapid_changes,
            'purchase_timing': self._test_purchase_timing,
            'edge_cases': self._test_edge_cases,
            'sync_stress': self._test_sync_stress_test
        }
        self.test_data_override = {}

    def get_config(self):
        """Load product configuration"""
        config_paths = [
            "config/product_config.json",
            "dashboard/../config/product_config.json",
            "../config/product_config.json"
        ]

        for path in config_paths:
            if Path(path).exists():
                with open(path, 'r') as f:
                    return json.load(f)
        return {"products": []}

    def get_headers(self):
        """Simple headers for API calls"""
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0'
        ]

        return {
            'accept': 'application/json',
            'user-agent': random.choice(user_agents),
            'accept-encoding': 'gzip, deflate, br',
            'accept-language': 'en-US,en;q=0.9',
            'connection': 'keep-alive'
        }

    def check_stock(self):
        """
        Check stock for all configured products
        Returns: {tcin: {title, in_stock, last_checked, status_detail}}
        """
        # TEST MODE: Override with test scenarios
        if self.test_mode and self.test_scenario:
            print(f"[TEST_MODE] Running scenario: {self.test_scenario} (cycle {self.test_cycle_count})")
            return self._run_test_scenario()

        config = self.get_config()
        enabled_products = [p for p in config.get('products', []) if p.get('enabled', True)]

        if not enabled_products:
            print("[STOCK] No enabled products to monitor")
            return {}

        tcins = [p['tcin'] for p in enabled_products]
        print(f"[STOCK] Checking {len(tcins)} products: {tcins}")

        # Prepare API call
        api_key = random.choice(self.api_keys)
        params = {
            'key': api_key,
            'tcins': ','.join(tcins),
            'store_id': '865',
            'pricing_store_id': '865',
            'has_pricing_context': 'true',
            'has_promotions': 'true',
            'is_bot': 'false'
        }

        headers = self.get_headers()

        try:
            start_time = time.time()
            response = requests.get(
                self.api_endpoint,
                params=params,
                headers=headers,
                timeout=8  # Reduced from 15 to 8 seconds for threading compatibility
            )
            response_time = (time.time() - start_time) * 1000

            if response.status_code == 200:
                data = response.json()
                result = self._process_response(data, response_time)
                self.last_check_time = datetime.now()
                print(f"[STOCK] Success: {len(result)} products processed in {response_time:.0f}ms")
                return result
            else:
                print(f"[STOCK] API Error: HTTP {response.status_code}")
                return self._get_error_result(tcins, f"HTTP {response.status_code}")

        except requests.exceptions.Timeout:
            print(f"[STOCK] Timeout after 8 seconds - API call took too long")
            return self._get_error_result(tcins, "API timeout")
        except requests.exceptions.ConnectionError:
            print(f"[STOCK] Connection error - unable to reach API")
            return self._get_error_result(tcins, "Connection error")
        except Exception as e:
            print(f"[STOCK] Exception: {e}")
            return self._get_error_result(tcins, str(e))

    def _process_response(self, data, response_time):
        """Process API response into clean format"""
        if not data or 'data' not in data:
            return {}

        if 'product_summaries' not in data['data']:
            return {}

        result = {}
        product_summaries = data['data']['product_summaries']

        for product_summary in product_summaries:
            try:
                tcin = product_summary.get('tcin')
                if not tcin:
                    continue

                item = product_summary.get('item', {})
                fulfillment = product_summary.get('fulfillment', {})

                # Extract product title
                product_desc = item.get('product_description', {})
                raw_title = product_desc.get('title', f'Product {tcin}')
                clean_title = html.unescape(raw_title)

                # Check stock status
                shipping = fulfillment.get('shipping_options', {})
                availability_status = shipping.get('availability_status', 'UNKNOWN')
                relationship_code = item.get('relationship_type_code', 'UNKNOWN')
                is_target_direct = relationship_code == 'SA'

                # Determine if in stock
                is_preorder = 'PRE_ORDER' in availability_status
                if is_preorder:
                    base_available = availability_status == 'PRE_ORDER_SELLABLE'
                else:
                    base_available = availability_status == 'IN_STOCK'

                in_stock = base_available and is_target_direct

                # Status detail
                if not is_target_direct:
                    status_detail = 'MARKETPLACE_SELLER'
                elif not base_available:
                    status_detail = 'OUT_OF_STOCK'
                else:
                    status_detail = 'IN_STOCK'

                result[tcin] = {
                    'title': clean_title,
                    'in_stock': in_stock,
                    'last_checked': datetime.now().isoformat(),
                    'status_detail': status_detail,
                    'availability_status': availability_status,
                    'is_preorder': is_preorder,
                    'is_target_direct': is_target_direct,
                    'response_time_ms': response_time
                }

            except Exception as e:
                print(f"[STOCK] Error processing product {tcin}: {e}")
                continue

        return result

    def _get_error_result(self, tcins, error_msg):
        """Return error result for all TCINs"""
        result = {}
        for tcin in tcins:
            result[tcin] = {
                'title': f'Product {tcin}',
                'in_stock': False,
                'last_checked': datetime.now().isoformat(),
                'status_detail': 'ERROR',
                'error': error_msg
            }
        return result

    # ========== COMPREHENSIVE TEST MODE INFRASTRUCTURE ==========

    def enable_test_mode(self, scenario='alternating'):
        """Enable test mode with specified scenario"""
        self.test_mode = True
        self.test_scenario = scenario
        self.test_cycle_count = 0
        self.test_start_time = time.time()
        print(f"[TEST_MODE] Enabled with scenario: {scenario}")
        print(f"[TEST_MODE] Available scenarios: {list(self.test_scenarios.keys())}")

    def disable_test_mode(self):
        """Disable test mode and return to normal API calls"""
        self.test_mode = False
        self.test_scenario = None
        self.test_cycle_count = 0
        self.test_data_override = {}
        print("[TEST_MODE] Disabled - returning to normal API calls")

    def set_test_data_override(self, tcin_stock_data):
        """Manually override stock data for specific TCINs in test mode"""
        self.test_data_override.update(tcin_stock_data)
        print(f"[TEST_MODE] Manual override set for TCINs: {list(tcin_stock_data.keys())}")

    def _run_test_scenario(self):
        """Execute the current test scenario"""
        if self.test_scenario not in self.test_scenarios:
            print(f"[TEST_MODE] Unknown scenario: {self.test_scenario}")
            return {}

        self.test_cycle_count += 1
        scenario_func = self.test_scenarios[self.test_scenario]

        try:
            result = scenario_func()
            print(f"[TEST_MODE] Scenario '{self.test_scenario}' cycle {self.test_cycle_count} completed")
            print(f"[TEST_MODE] Generated data for {len(result)} products")

            # Log detailed test results
            for tcin, data in result.items():
                status = "IN STOCK" if data['in_stock'] else "OUT OF STOCK"
                print(f"[TEST_MODE]   {tcin}: {data['title']} - {status} ({data['status_detail']})")

            return result

        except Exception as e:
            print(f"[TEST_MODE] Error in scenario '{self.test_scenario}': {e}")
            return self._get_base_test_data()

    def _get_base_test_data(self):
        """Get base test data for configured products"""
        config = self.get_config()
        enabled_products = [p for p in config.get('products', []) if p.get('enabled', True)]

        result = {}
        for product in enabled_products:
            tcin = product['tcin']

            # Check for manual overrides first
            if tcin in self.test_data_override:
                override_data = self.test_data_override[tcin]
                result[tcin] = {
                    'title': product.get('name', f'Test Product {tcin}'),
                    'in_stock': override_data.get('in_stock', False),
                    'last_checked': datetime.now().isoformat(),
                    'status_detail': override_data.get('status_detail', 'OUT_OF_STOCK'),
                    'test_mode': True,
                    'test_cycle': self.test_cycle_count,
                    'response_time_ms': random.randint(50, 200)
                }
            else:
                # Default to out of stock for base data
                result[tcin] = {
                    'title': product.get('name', f'Test Product {tcin}'),
                    'in_stock': False,
                    'last_checked': datetime.now().isoformat(),
                    'status_detail': 'OUT_OF_STOCK',
                    'test_mode': True,
                    'test_cycle': self.test_cycle_count,
                    'response_time_ms': random.randint(50, 200)
                }

        return result

    def _test_alternating_stock(self):
        """Test scenario: Alternating stock status every cycle"""
        result = self._get_base_test_data()

        # Alternate every cycle: odd cycles = out of stock, even cycles = in stock
        is_in_stock = (self.test_cycle_count % 2 == 0)

        print(f"[TEST_ALTERNATING] Cycle {self.test_cycle_count}: Setting all products to {'IN STOCK' if is_in_stock else 'OUT OF STOCK'}")

        for tcin in result:
            # Skip manual overrides
            if tcin not in self.test_data_override:
                result[tcin]['in_stock'] = is_in_stock
                result[tcin]['status_detail'] = 'IN_STOCK' if is_in_stock else 'OUT_OF_STOCK'

        return result

    def _test_rapid_changes(self):
        """Test scenario: Rapid stock status changes to test race conditions"""
        result = self._get_base_test_data()

        # Change stock status every 3 cycles for stress testing
        cycle_mod = self.test_cycle_count % 3

        for i, tcin in enumerate(result):
            if tcin not in self.test_data_override:
                # Different patterns for different products to test UI updates
                if i % 2 == 0:
                    # Product index even: changes every 3 cycles
                    is_in_stock = (cycle_mod == 0)
                else:
                    # Product index odd: changes every 2 cycles
                    is_in_stock = (self.test_cycle_count % 2 == 1)

                result[tcin]['in_stock'] = is_in_stock
                result[tcin]['status_detail'] = 'IN_STOCK' if is_in_stock else 'OUT_OF_STOCK'

        print(f"[TEST_RAPID] Cycle {self.test_cycle_count}: Mixed stock statuses for stress testing")
        return result

    def _test_purchase_timing(self):
        """Test scenario: Focused on purchase attempt timing validation"""
        result = self._get_base_test_data()

        # Cycle through products being in stock to test purchase attempt timing
        product_tcins = list(result.keys())
        if product_tcins:
            # Only one product in stock per cycle to test individual purchase timing
            active_index = self.test_cycle_count % len(product_tcins)
            active_tcin = product_tcins[active_index]

            for tcin in result:
                if tcin not in self.test_data_override:
                    is_active = (tcin == active_tcin)
                    result[tcin]['in_stock'] = is_active
                    result[tcin]['status_detail'] = 'IN_STOCK' if is_active else 'OUT_OF_STOCK'

            print(f"[TEST_PURCHASE_TIMING] Cycle {self.test_cycle_count}: Only {active_tcin} in stock")

        return result

    def _test_edge_cases(self):
        """Test scenario: Edge cases and error conditions"""
        result = self._get_base_test_data()

        # Cycle through different edge case scenarios
        edge_case = self.test_cycle_count % 4

        if edge_case == 0:
            # All products out of stock
            for tcin in result:
                if tcin not in self.test_data_override:
                    result[tcin]['in_stock'] = False
                    result[tcin]['status_detail'] = 'OUT_OF_STOCK'
            print("[TEST_EDGE] All products out of stock")

        elif edge_case == 1:
            # All products in stock
            for tcin in result:
                if tcin not in self.test_data_override:
                    result[tcin]['in_stock'] = True
                    result[tcin]['status_detail'] = 'IN_STOCK'
            print("[TEST_EDGE] All products in stock")

        elif edge_case == 2:
            # Mixed marketplace sellers
            for i, tcin in enumerate(result):
                if tcin not in self.test_data_override:
                    if i % 2 == 0:
                        result[tcin]['in_stock'] = False
                        result[tcin]['status_detail'] = 'MARKETPLACE_SELLER'
                    else:
                        result[tcin]['in_stock'] = True
                        result[tcin]['status_detail'] = 'IN_STOCK'
            print("[TEST_EDGE] Mixed marketplace sellers")

        else:
            # API errors simulation
            for tcin in result:
                if tcin not in self.test_data_override:
                    result[tcin]['in_stock'] = False
                    result[tcin]['status_detail'] = 'ERROR'
                    result[tcin]['error'] = 'Simulated API timeout'
            print("[TEST_EDGE] Simulated API errors")

        return result

    def _test_sync_stress_test(self):
        """Test scenario: Maximum stress test for UI synchronization"""
        result = self._get_base_test_data()

        # Aggressive pattern to stress test UI updates
        for i, tcin in enumerate(result):
            if tcin not in self.test_data_override:
                # Each product follows a different rapid pattern
                pattern = (self.test_cycle_count + i) % 4
                patterns = [
                    False,  # Out of stock
                    True,   # In stock
                    True,   # In stock
                    False   # Out of stock
                ]

                is_in_stock = patterns[pattern]
                result[tcin]['in_stock'] = is_in_stock
                result[tcin]['status_detail'] = 'IN_STOCK' if is_in_stock else 'OUT_OF_STOCK'

        print(f"[TEST_SYNC_STRESS] Cycle {self.test_cycle_count}: Aggressive pattern changes")
        return result

    def get_test_status(self):
        """Get current test mode status and statistics"""
        if not self.test_mode:
            return {'test_mode': False}

        elapsed_time = time.time() - self.test_start_time if self.test_start_time else 0

        return {
            'test_mode': True,
            'scenario': self.test_scenario,
            'cycle_count': self.test_cycle_count,
            'elapsed_time': elapsed_time,
            'available_scenarios': list(self.test_scenarios.keys()),
            'manual_overrides': list(self.test_data_override.keys())
        }

def main():
    """Test the stock monitor"""
    monitor = StockMonitor()

    print("Testing Stock Monitor...")
    result = monitor.check_stock()

    print(f"\nResults for {len(result)} products:")
    for tcin, data in result.items():
        status = "IN STOCK" if data['in_stock'] else "OUT OF STOCK"
        print(f"  {tcin}: {data['title'][:50]}... - {status}")

if __name__ == '__main__':
    main()
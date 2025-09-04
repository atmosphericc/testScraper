#!/usr/bin/env python3
"""
Test script to analyze pre-order TCIN API responses
Tests both out-of-stock and in-stock pre-orders to identify patterns
"""

import requests
import json
import time
import random
from datetime import datetime

class PreorderTester:
    def __init__(self):
        self.base_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
        self.api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
        self.store_id = "865"
        
    def generate_visitor_id(self):
        """Generate realistic visitor ID"""
        timestamp = int(time.time() * 1000)
        random_suffix = ''.join(random.choices('0123456789ABCDEF', k=16))
        return f"{timestamp:016X}{random_suffix}"
        
    def get_headers(self):
        """Generate realistic headers"""
        return {
            'accept': 'application/json',
            'accept-encoding': 'gzip, deflate, br, zstd',
            'accept-language': 'en-US,en;q=0.9',
            'cache-control': 'no-cache',
            'origin': 'https://www.target.com',
            'referer': 'https://www.target.com/',
            'sec-ch-ua': '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        }

    def test_tcin(self, tcin: str, description: str):
        """Test a single TCIN and analyze response"""
        print(f"\n{'='*60}")
        print(f"Testing {description}: {tcin}")
        print(f"{'='*60}")
        
        params = {
            'key': self.api_key,
            'tcin': tcin,
            'store_id': self.store_id,
            'pricing_store_id': self.store_id,
            'has_pricing_store_id': 'true',
            'has_financing_options': 'true',
            'visitor_id': self.generate_visitor_id(),
            'has_size_context': 'true',
        }
        
        try:
            response = requests.get(
                self.base_url,
                params=params,
                headers=self.get_headers(),
                timeout=15
            )
            
            if response.status_code == 200:
                data = response.json()
                self.analyze_response(tcin, description, data)
                return data
            else:
                print(f"❌ HTTP {response.status_code}")
                print(f"Response: {response.text[:500]}")
                return None
                
        except Exception as e:
            print(f"❌ Error: {e}")
            return None
    
    def analyze_response(self, tcin: str, description: str, data: dict):
        """Analyze the API response for pre-order patterns"""
        try:
            product = data['data']['product']
            item = product['item']
            
            # Basic info
            name = item.get('product_description', {}).get('title', 'Unknown')
            price = product.get('price', {}).get('current_retail', 0)
            
            print(f"📦 Product: {name[:80]}")
            print(f"💰 Price: ${price}")
            
            # Fulfillment analysis
            fulfillment = item.get('fulfillment', {})
            print(f"\n🚚 FULFILLMENT INFO:")
            for key, value in fulfillment.items():
                if key in ['is_marketplace', 'purchase_limit', 'shipping_options', 'store_options']:
                    print(f"  {key}: {value}")
            
            # Eligibility rules - KEY FOR STOCK DETECTION
            eligibility = item.get('eligibility_rules', {})
            print(f"\n✅ ELIGIBILITY RULES:")
            for rule_name, rule_data in eligibility.items():
                if isinstance(rule_data, dict) and 'is_active' in rule_data:
                    status = "✅ ACTIVE" if rule_data['is_active'] else "❌ INACTIVE"
                    print(f"  {rule_name}: {status}")
                    if rule_data.get('start_time') or rule_data.get('end_time'):
                        print(f"    Start: {rule_data.get('start_time')}")
                        print(f"    End: {rule_data.get('end_time')}")
            
            # Check for pre-order specific fields
            print(f"\n📅 PRE-ORDER INDICATORS:")
            
            # Look for shipping/delivery dates
            shipping_details = fulfillment.get('shipping_options', {})
            if shipping_details:
                print(f"  Shipping options: {shipping_details}")
                
            # Check product lifecycle
            product_classification = item.get('product_classification', {})
            if product_classification:
                print(f"  Classification: {product_classification}")
                
            # Look for availability messages
            enrichment = item.get('enrichment', {})
            if enrichment:
                buy_url = enrichment.get('buy_url', '')
                if 'preorder' in buy_url.lower():
                    print(f"  ⭐ PRE-ORDER detected in buy_url: {buy_url}")
                    
            # Check for any date fields that might indicate pre-order
            def find_date_fields(obj, path=""):
                """Recursively find date-related fields"""
                date_fields = []
                if isinstance(obj, dict):
                    for key, value in obj.items():
                        current_path = f"{path}.{key}" if path else key
                        if any(date_word in key.lower() for date_word in ['date', 'time', 'release', 'available', 'ship']):
                            date_fields.append((current_path, value))
                        if isinstance(value, (dict, list)):
                            date_fields.extend(find_date_fields(value, current_path))
                elif isinstance(obj, list):
                    for i, item in enumerate(obj):
                        current_path = f"{path}[{i}]" if path else f"[{i}]"
                        date_fields.extend(find_date_fields(item, current_path))
                return date_fields
            
            date_fields = find_date_fields(item)
            if date_fields:
                print(f"  📅 Date-related fields found:")
                for field_path, value in date_fields[:10]:  # Limit to first 10
                    print(f"    {field_path}: {value}")
            
            # Current stock logic result
            purchase_limit = fulfillment.get('purchase_limit', 0)
            ship_to_guest_active = eligibility.get('ship_to_guest', {}).get('is_active', False)
            inventory_excluded = eligibility.get('inventory_notification_to_guest_excluded', {}).get('is_active', False)
            hold_active = eligibility.get('hold', {}).get('is_active', False)
            
            # Apply current logic
            if inventory_excluded:
                current_result = "❌ OUT OF STOCK (inventory_notification_excluded)"
            elif ship_to_guest_active and purchase_limit >= 1:
                if hold_active:
                    current_result = "❌ RESTRICTED (hold_active)"
                else:
                    current_result = "✅ AVAILABLE (ship_to_guest_active)"
            else:
                current_result = "❌ OUT OF STOCK (no_positive_signals)"
            
            print(f"\n🤖 CURRENT API LOGIC RESULT: {current_result}")
            
            # Save detailed response for analysis
            filename = f"preorder_response_{tcin}_{description.replace(' ', '_')}.json"
            with open(filename, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"💾 Full response saved to: {filename}")
            
        except Exception as e:
            print(f"❌ Analysis error: {e}")

def main():
    """Test both pre-order TCINs"""
    tester = PreorderTester()
    
    print(f"🧪 PRE-ORDER API ANALYSIS")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Test cases
    test_cases = [
        ("94681776", "Out of Stock Pre-order"),
        ("94723520", "In Stock Pre-order")
    ]
    
    results = {}
    
    for tcin, description in test_cases:
        result = tester.test_tcin(tcin, description)
        results[tcin] = result
        
        # Small delay between requests
        time.sleep(2)
    
    print(f"\n{'='*60}")
    print("🎯 ANALYSIS COMPLETE")
    print(f"{'='*60}")
    print("Check the generated JSON files for detailed response analysis.")
    print("Look for patterns that differentiate pre-orders from regular stock.")
    
    return results

if __name__ == "__main__":
    main()
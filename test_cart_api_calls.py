#!/usr/bin/env python3
"""
Test different API approaches to find real pre-order inventory status
Maybe we need to simulate add-to-cart calls or use a different endpoint
"""

import requests
import json
import time
import random

class CartAPITester:
    def __init__(self):
        self.api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
        self.store_id = "865"
        
    def generate_visitor_id(self):
        timestamp = int(time.time() * 1000)
        random_suffix = ''.join(random.choices('0123456789ABCDEF', k=16))
        return f"{timestamp:016X}{random_suffix}"
        
    def get_headers(self):
        return {
            'accept': 'application/json',
            'accept-encoding': 'gzip, deflate, br, zstd',
            'accept-language': 'en-US,en;q=0.9',
            'cache-control': 'no-cache',
            'origin': 'https://www.target.com',
            'referer': 'https://www.target.com/',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        }

    def test_different_pdp_params(self, tcin: str, description: str):
        """Try different PDP API parameters that might show inventory status"""
        print(f"\\n🧪 TESTING DIFFERENT PDP PARAMS FOR {description} (TCIN {tcin})")
        print("-" * 60)
        
        base_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
        
        # Try different parameter combinations that might reveal inventory
        param_sets = [
            {
                'name': 'With inventory context',
                'params': {
                    'key': self.api_key,
                    'tcin': tcin,
                    'store_id': self.store_id,
                    'pricing_store_id': self.store_id,
                    'visitor_id': self.generate_visitor_id(),
                    'has_inventory_context': 'true',
                    'include_inventory': 'true',
                }
            },
            {
                'name': 'With cart context',
                'params': {
                    'key': self.api_key,
                    'tcin': tcin,
                    'store_id': self.store_id,
                    'pricing_store_id': self.store_id,
                    'visitor_id': self.generate_visitor_id(),
                    'has_cart_context': 'true',
                    'include_cart_details': 'true',
                }
            },
            {
                'name': 'With purchasability filter disabled',
                'params': {
                    'key': self.api_key,
                    'tcin': tcin,
                    'store_id': self.store_id,
                    'pricing_store_id': self.store_id,
                    'visitor_id': self.generate_visitor_id(),
                    'default_purchasability_filter': 'false',
                    'include_unavailable': 'true',
                }
            }
        ]
        
        for param_set in param_sets:
            print(f"\\n  Testing: {param_set['name']}")
            
            try:
                response = requests.get(
                    base_url,
                    params=param_set['params'],
                    headers=self.get_headers(),
                    timeout=15
                )
                
                print(f"    Status: {response.status_code}")
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Look for new fields we haven't seen before
                    item = data['data']['product']['item']
                    product = data['data']['product']
                    
                    # Quick check for any inventory-related fields
                    inventory_indicators = []
                    
                    def find_inventory_fields(obj, path=""):
                        found = []
                        if isinstance(obj, dict):
                            for key, value in obj.items():
                                current_path = f"{path}.{key}" if path else key
                                if any(term in key.lower() for term in ['inventory', 'stock', 'available', 'purchasable']):
                                    found.append((current_path, value))
                                if isinstance(value, dict):
                                    found.extend(find_inventory_fields(value, current_path))
                        return found
                    
                    inventory_indicators = find_inventory_fields(data)
                    
                    if inventory_indicators:
                        print(f"    ✅ Found inventory fields:")
                        for field, value in inventory_indicators:
                            print(f"      {field}: {value}")
                    else:
                        print(f"    ❌ No new inventory fields found")
                        
                else:
                    print(f"    ❌ Failed: {response.text[:200]}")
                    
            except Exception as e:
                print(f"    ❌ Error: {e}")
            
            time.sleep(0.5)

    def try_cart_related_apis(self, tcin: str, description: str):
        """Try cart-related APIs that might show real availability"""
        print(f"\\n🛒 TESTING CART-RELATED APIs FOR {description} (TCIN {tcin})")
        print("-" * 60)
        
        # Try different cart/inventory endpoints
        cart_urls = [
            f"https://redsky.target.com/redsky_aggregations/v1/web/cart_item_eligibility?key={self.api_key}&tcin={tcin}&store_id={self.store_id}",
            f"https://redsky.target.com/redsky_aggregations/v1/web/inventory_check?key={self.api_key}&tcin={tcin}&store_id={self.store_id}",
            f"https://redsky.target.com/redsky_aggregations/v1/web/add_to_cart_eligibility?key={self.api_key}&tcin={tcin}&store_id={self.store_id}",
        ]
        
        for i, url in enumerate(cart_urls, 1):
            endpoint_name = url.split('/')[-1].split('?')[0]
            print(f"\\n  {i}. Testing {endpoint_name}...")
            
            try:
                response = requests.get(url, headers=self.get_headers(), timeout=10)
                print(f"     Status: {response.status_code}")
                
                if response.status_code == 200:
                    data = response.json()
                    print(f"     ✅ Success! Got data: {list(data.keys())}")
                    
                    # Save for analysis
                    filename = f"cart_api_{endpoint_name}_{tcin}.json"
                    with open(filename, 'w') as f:
                        json.dump(data, f, indent=2)
                    print(f"     💾 Saved to: {filename}")
                    
                elif response.status_code == 404:
                    print(f"     ❌ Endpoint doesn't exist")
                else:
                    print(f"     ❌ Failed: {response.text[:100]}")
                    
            except Exception as e:
                print(f"     ❌ Error: {e}")

    def test_website_scraping_approach(self, tcin: str, description: str):
        """Test direct website scraping to see button state"""
        print(f"\\n🌐 TESTING WEBSITE APPROACH FOR {description} (TCIN {tcin})")
        print("-" * 60)
        
        product_url = f"https://www.target.com/p/-/A-{tcin}"
        
        try:
            # Just check if page loads and what HTTP status we get
            response = requests.get(product_url, headers=self.get_headers(), timeout=10)
            print(f"  Product page status: {response.status_code}")
            
            if response.status_code == 200:
                html = response.text
                
                # Look for button-related text that might indicate status
                button_indicators = [
                    'add to cart',
                    'preorder', 
                    'pre-order',
                    'out of stock',
                    'unavailable',
                    'coming soon',
                    'notify me',
                    'sold out'
                ]
                
                found_indicators = []
                for indicator in button_indicators:
                    if indicator.lower() in html.lower():
                        found_indicators.append(indicator)
                
                print(f"  Button-related text found: {found_indicators}")
                
                # Look for specific button states in HTML
                if 'disabled' in html.lower():
                    print(f"  ⚠️  Contains 'disabled' - might indicate greyed out button")
                    
                if 'data-test=' in html:
                    print(f"  ℹ️  Contains test attributes - might have button state info")
                    
            else:
                print(f"  ❌ Page failed to load")
                
        except Exception as e:
            print(f"  ❌ Error: {e}")

def main():
    """Test different approaches to find inventory status"""
    tester = CartAPITester()
    
    # Focus on the two key cases
    test_cases = [
        ('94681776', 'GREYED OUT pre-order'),
        ('94734932', 'CLICKABLE pre-order')
    ]
    
    print("🔍 TESTING DIFFERENT API APPROACHES")
    print("Looking for real pre-order inventory status")
    print("="*70)
    
    for tcin, description in test_cases:
        # Test different PDP parameters
        tester.test_different_pdp_params(tcin, description)
        
        # Test cart-related APIs
        tester.try_cart_related_apis(tcin, description)
        
        # Test website approach
        tester.test_website_scraping_approach(tcin, description)
        
        time.sleep(1)
    
    print(f"\\n🎯 CONCLUSION:")
    print("If none of these approaches reveal inventory status,")
    print("we may need to:")
    print("1. Accept that API doesn't show real-time pre-order inventory")
    print("2. Use website scraping for accurate status")  
    print("3. Make actual add-to-cart attempts to test availability")
    print("4. Use simple purchase_limit > 0 logic and handle failures gracefully")

if __name__ == "__main__":
    main()
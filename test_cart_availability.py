#!/usr/bin/env python3
"""
Test actual cart availability for pre-orders
This will reveal which can actually be added to cart vs just having product info
"""

import requests
import json
import time
import random

class CartAvailabilityTester:
    def __init__(self):
        # Target's cart-related APIs
        self.cart_api = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_fulfillment_v1"
        self.availability_api = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
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
            'sec-ch-ua': '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        }

    def test_fulfillment_api(self, tcin: str, description: str):
        """Test the fulfillment API which might show cart availability"""
        print(f"\\n{'='*60}")
        print(f"Testing FULFILLMENT API for {description}: {tcin}")
        print(f"{'='*60}")
        
        params = {
            'key': self.api_key,
            'tcin': tcin,
            'store_id': self.store_id,
            'visitor_id': self.generate_visitor_id(),
            'channel': 'WEB',
            'page': f'/p/-/A-{tcin}',
            'has_store_id': 'true',
            'pricing_store_id': self.store_id,
        }
        
        try:
            response = requests.get(
                self.cart_api,
                params=params,
                headers=self.get_headers(),
                timeout=15
            )
            
            if response.status_code == 200:
                data = response.json()
                print(f"✅ SUCCESS - Fulfillment API returned data")
                
                # Save response for analysis
                filename = f"fulfillment_response_{tcin}_{description.replace(' ', '_')}.json"
                with open(filename, 'w') as f:
                    json.dump(data, f, indent=2)
                print(f"💾 Response saved to: {filename}")
                
                # Analyze fulfillment data
                self.analyze_fulfillment_response(data, tcin, description)
                return data
                
            else:
                print(f"❌ HTTP {response.status_code}")
                print(f"Response: {response.text[:300]}")
                return None
                
        except Exception as e:
            print(f"❌ Error: {e}")
            return None
    
    def analyze_fulfillment_response(self, data: dict, tcin: str, description: str):
        """Analyze fulfillment response for cart availability indicators"""
        try:
            print(f"\\n🔍 FULFILLMENT ANALYSIS:")
            
            # Look for fulfillment options
            if 'data' in data and 'product' in data['data']:
                product = data['data']['product']
                
                # Check for fulfillment options
                fulfillment_options = product.get('fulfillment', {})
                print(f"  Fulfillment options: {list(fulfillment_options.keys())}")
                
                # Look for shipping/pickup availability
                if 'shipping_options' in fulfillment_options:
                    shipping = fulfillment_options['shipping_options']
                    print(f"  Shipping options: {shipping}")
                
                if 'store_options' in fulfillment_options:
                    store = fulfillment_options['store_options']
                    print(f"  Store options: {store}")
                
                # Look for any availability flags
                available_to_purchase_network = fulfillment_options.get('available_to_purchase_network', {})
                if available_to_purchase_network:
                    print(f"  Available to purchase network: {available_to_purchase_network}")
                
                # Check for cart-related fields
                for key, value in fulfillment_options.items():
                    if any(word in key.lower() for word in ['cart', 'purchase', 'available', 'add']):
                        print(f"  🎯 {key}: {value}")
            
        except Exception as e:
            print(f"❌ Analysis error: {e}")

    def test_enhanced_pdp_api(self, tcin: str, description: str):
        """Test PDP API with more cart-focused parameters"""
        print(f"\\n{'='*60}")
        print(f"Testing ENHANCED PDP API for {description}: {tcin}")
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
            'channel': 'WEB',
            'page': f'/p/-/A-{tcin}',
            # Cart-focused parameters
            'has_fulfillment_context': 'true',
            'has_promotion_context': 'true',
            'has_size_context': 'true',
        }
        
        try:
            response = requests.get(
                self.availability_api,
                params=params,
                headers=self.get_headers(),
                timeout=15
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # Focus on fields that might indicate cart availability
                product = data['data']['product']
                item = product['item']
                
                print(f"🔍 CART AVAILABILITY INDICATORS:")
                
                # Check for any cart/purchase related fields we might have missed
                def find_cart_fields(obj, path=""):
                    cart_fields = []
                    if isinstance(obj, dict):
                        for key, value in obj.items():
                            current_path = f"{path}.{key}" if path else key
                            if any(word in key.lower() for word in ['cart', 'purchase', 'available', 'add', 'buy', 'order']):
                                cart_fields.append((current_path, value))
                            if isinstance(value, (dict, list)) and len(str(value)) < 1000:  # Don't recurse into huge objects
                                cart_fields.extend(find_cart_fields(value, current_path))
                    elif isinstance(obj, list):
                        for i, item_val in enumerate(obj[:3]):  # Only check first few items
                            current_path = f"{path}[{i}]" if path else f"[{i}]"
                            cart_fields.extend(find_cart_fields(item_val, current_path))
                    return cart_fields
                
                cart_fields = find_cart_fields(item)
                if cart_fields:
                    print("  Cart-related fields found:")
                    for field_path, value in cart_fields:
                        print(f"    {field_path}: {value}")
                else:
                    print("  ❌ No obvious cart-related fields found")
                
                return data
                
            else:
                print(f"❌ HTTP {response.status_code}")
                return None
                
        except Exception as e:
            print(f"❌ Error: {e}")
            return None

def main():
    tester = CartAvailabilityTester()
    
    test_cases = [
        ("94681776", "Not Available for Purchase"),
        ("94723520", "Available for Purchase")
    ]
    
    print("🧪 CART AVAILABILITY TESTING")
    print("Testing both fulfillment and enhanced PDP APIs")
    
    for tcin, description in test_cases:
        # Test fulfillment API
        tester.test_fulfillment_api(tcin, description)
        time.sleep(1)
        
        # Test enhanced PDP API  
        tester.test_enhanced_pdp_api(tcin, description)
        time.sleep(2)

if __name__ == "__main__":
    main()
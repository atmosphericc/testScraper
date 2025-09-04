#!/usr/bin/env python3
"""
Test the enhanced parser against mixed product types:
- Regular in-stock products
- Regular out-of-stock products  
- Pre-orders
"""

import requests
import json
import time
from enhanced_preorder_parser import EnhancedPreorderParser

class MixedProductTester:
    def __init__(self):
        self.base_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
        self.api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
        self.store_id = "865"
        self.parser = EnhancedPreorderParser()
        
    def generate_visitor_id(self):
        import random
        timestamp = int(time.time() * 1000)
        random_suffix = ''.join(random.choices('0123456789ABCDEF', k=16))
        return f"{timestamp:016X}{random_suffix}"
        
    def get_headers(self):
        return {
            'accept': 'application/json',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
            'referer': 'https://www.target.com/',
        }

    def test_tcin(self, tcin: str, expected_type: str):
        """Test a TCIN and show parsing results"""
        params = {
            'key': self.api_key,
            'tcin': tcin,
            'store_id': self.store_id,
            'pricing_store_id': self.store_id,
            'visitor_id': self.generate_visitor_id(),
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
                result = self.parser.parse_availability(tcin, data)
                
                print(f"TCIN {tcin} ({expected_type}):")
                print(f"  Product: {result['name']}")
                print(f"  Available: {'✅ YES' if result['available'] else '❌ NO'}")
                print(f"  Detected Type: {result['product_type']}")
                print(f"  Reason: {result['reason']}")
                print(f"  Confidence: {result['confidence']}")
                
                if result['product_type'] == 'pre-order':
                    print(f"  Release Date: {result.get('release_date', 'N/A')}")
                elif result['product_type'] == 'regular':
                    print(f"  Seller: {result.get('seller_type', 'N/A')}")
                
                print()
                return result
                
            else:
                print(f"❌ TCIN {tcin}: HTTP {response.status_code}")
                return None
                
        except Exception as e:
            print(f"❌ TCIN {tcin}: Error - {e}")
            return None

def main():
    tester = MixedProductTester()
    
    # Test cases - you can add known regular TCINs here
    test_cases = [
        # Your pre-orders
        ("94681776", "Pre-order (out of stock?)"),
        ("94723520", "Pre-order (in stock?)"),
        
        # Add some regular products if you have TCINs
        # ("12345678", "Regular product"),
    ]
    
    print("🧪 MIXED PRODUCT TYPE TESTING")
    print("="*50)
    
    for tcin, expected in test_cases:
        tester.test_tcin(tcin, expected)
        time.sleep(1)  # Be nice to API

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
Test TCIN 94734932 with the final working preorder logic
Using free_shipping.enabled field to determine availability
"""

import requests
import json
import time
import random
from final_working_preorder_parser import parse_availability_final

class PreorderTester:
    def __init__(self):
        self.base_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
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

    def test_tcin(self, tcin: str):
        """Test TCIN with final working preorder logic"""
        print(f"🧪 TESTING TCIN {tcin} WITH FINAL PREORDER LOGIC")
        print("="*60)
        
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
                
                # Save response for analysis
                filename = f"test_response_{tcin}.json"
                with open(filename, 'w') as f:
                    json.dump(data, f, indent=2)
                print(f"💾 Response saved to: {filename}")
                
                # Parse with final working logic
                result = parse_availability_final(tcin, data)
                
                # Display comprehensive results
                print(f"\n📦 PRODUCT ANALYSIS:")
                print(f"  TCIN: {result['tcin']}")
                print(f"  Name: {result['name']}")
                print(f"  Price: ${result.get('price', 0)}")
                print(f"  Product Type: {result.get('product_type', 'unknown')}")
                
                print(f"\n🎯 AVAILABILITY DECISION:")
                available_status = "✅ AVAILABLE FOR PREORDER" if result['available'] else "❌ NOT AVAILABLE FOR PREORDER"
                print(f"  Status: {available_status}")
                print(f"  Reason: {result.get('reason', 'unknown')}")
                print(f"  Confidence: {result.get('confidence', 'unknown')}")
                
                if result.get('product_type') == 'preorder':
                    print(f"\n📅 PREORDER DETAILS:")
                    print(f"  Street Date: {result.get('street_date', 'N/A')}")
                    print(f"  Release Date: {result.get('release_date', 'N/A')}")
                    print(f"  Days Until Release: {result.get('days_until_release', 'N/A')}")
                    print(f"  Purchase Limit: {result.get('purchase_limit', 'N/A')}")
                    
                    print(f"\n🔍 KEY AVAILABILITY INDICATORS:")
                    print(f"  Free Shipping Enabled: {result.get('free_shipping_enabled', 'N/A')}")
                    print(f"  Has Purchase Limit: {result.get('purchase_limit', 0) > 0}")
                    
                    # Show the logic applied
                    free_shipping = result.get('free_shipping_enabled', False)
                    purchase_limit = result.get('purchase_limit', 0)
                    print(f"\n🧮 LOGIC APPLIED:")
                    print(f"  purchase_limit > 0: {purchase_limit > 0} (purchase_limit = {purchase_limit})")
                    print(f"  free_shipping.enabled: {free_shipping}")
                    print(f"  Final calculation: {purchase_limit > 0} AND NOT {free_shipping} = {result['available']}")
                
                elif result.get('product_type') == 'regular':
                    print(f"\n🏪 REGULAR PRODUCT DETAILS:")
                    print(f"  Seller Type: {result.get('seller_type', 'N/A')}")
                    print(f"  Purchase Limit: {result.get('purchase_limit', 'N/A')}")
                
                # Quick raw data check
                print(f"\n🔍 RAW API DATA CHECK:")
                item = data['data']['product']['item']
                product = data['data']['product']
                
                has_eligibility = 'eligibility_rules' in item
                street_date = item.get('mmbv_content', {}).get('street_date')
                free_shipping = product.get('free_shipping', {})
                
                print(f"  Has eligibility_rules: {has_eligibility}")
                print(f"  Has street_date: {street_date is not None}")
                if street_date:
                    print(f"  Street date: {street_date}")
                print(f"  Free shipping data: {free_shipping}")
                
                return result
                
            else:
                print(f"❌ HTTP {response.status_code}")
                print(f"Response: {response.text[:500]}")
                return None
                
        except Exception as e:
            print(f"❌ Error: {e}")
            return None

def main():
    tester = PreorderTester()
    result = tester.test_tcin("94734932")
    
    if result:
        print(f"\n🎯 FINAL ANSWER:")
        if result['available']:
            print(f"✅ TCIN 94734932 IS AVAILABLE FOR PREORDER")
            print(f"   Your bot can add this to cart and attempt purchase")
        else:
            print(f"❌ TCIN 94734932 IS NOT AVAILABLE FOR PREORDER") 
            print(f"   Your bot should not attempt to purchase this")
        
        print(f"\n📊 TECHNICAL DETAILS:")
        print(f"   Product Type: {result.get('product_type', 'unknown')}")
        print(f"   Logic Used: {result.get('reason', 'unknown')}")
        if result.get('product_type') == 'preorder':
            print(f"   Key Factor: free_shipping.enabled = {result.get('free_shipping_enabled', 'unknown')}")

if __name__ == "__main__":
    main()
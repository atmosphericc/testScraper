#!/usr/bin/env python3
"""
Test the new TCIN 94827553 with our final pre-order parser
"""

import requests
import json
import time
import random
from final_preorder_parser import parse_availability_with_preorders

class NewTCINTester:
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
        """Test a TCIN with our enhanced parser"""
        print(f"🧪 TESTING TCIN {tcin}")
        print("="*50)
        
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
                
                # Save response
                filename = f"tcin_response_{tcin}.json"
                with open(filename, 'w') as f:
                    json.dump(data, f, indent=2)
                print(f"💾 Response saved to: {filename}")
                
                # Parse with our enhanced parser
                result = parse_availability_with_preorders(tcin, data)
                
                # Display results
                print(f"\n📦 PRODUCT INFO:")
                print(f"  TCIN: {result['tcin']}")
                print(f"  Name: {result['name']}")
                print(f"  Price: ${result.get('price', 0)}")
                
                print(f"\n🎯 AVAILABILITY ANALYSIS:")
                print(f"  Available: {'✅ YES' if result['available'] else '❌ NO'}")
                print(f"  Product Type: {result.get('product_type', 'unknown')}")
                print(f"  Reason: {result.get('reason', 'unknown')}")
                print(f"  Confidence: {result.get('confidence', 'unknown')}")
                
                if result.get('product_type') == 'preorder':
                    print(f"\n📅 PRE-ORDER INFO:")
                    print(f"  Street Date: {result.get('street_date', 'N/A')}")
                    print(f"  Release Date: {result.get('release_date', 'N/A')}")
                    print(f"  Days Until Release: {result.get('days_until_release', 'N/A')}")
                elif result.get('product_type') == 'regular':
                    print(f"\n🏪 REGULAR PRODUCT INFO:")
                    print(f"  Seller Type: {result.get('seller_type', 'N/A')}")
                    print(f"  Purchase Limit: {result.get('purchase_limit', 'N/A')}")
                
                # Quick analysis of raw data
                print(f"\n🔍 RAW DATA ANALYSIS:")
                item = data['data']['product']['item']
                print(f"  Has eligibility_rules: {'eligibility_rules' in item}")
                print(f"  Has street_date: {'mmbv_content' in item and 'street_date' in item.get('mmbv_content', {})}")
                
                if 'mmbv_content' in item and 'street_date' in item['mmbv_content']:
                    street_date = item['mmbv_content']['street_date']
                    print(f"  Street date: {street_date}")
                
                fulfillment = item.get('fulfillment', {})
                print(f"  Purchase limit: {fulfillment.get('purchase_limit', 0)}")
                
                return result
                
            else:
                print(f"❌ HTTP {response.status_code}")
                print(f"Response: {response.text[:500]}")
                return None
                
        except Exception as e:
            print(f"❌ Error: {e}")
            return None

def main():
    tester = NewTCINTester()
    result = tester.test_tcin("94827553")
    
    if result:
        print(f"\n🎯 SUMMARY:")
        print(f"TCIN 94827553 is {'AVAILABLE' if result['available'] else 'NOT AVAILABLE'} for purchase")
        print(f"Detected as: {result.get('product_type', 'unknown')} product")

if __name__ == "__main__":
    main()
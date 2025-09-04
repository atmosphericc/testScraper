#!/usr/bin/env python3
"""
Test different API parameters and endpoints to find preorder availability data
Maybe we need different parameters to get eligibility_rules for preorders
"""

import requests
import json
import time
import random

class APIParameterTester:
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
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
            'referer': 'https://www.target.com/',
        }

    def test_pdp_with_different_params(self, tcin: str, description: str):
        """Test PDP API with different parameter combinations"""
        base_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
        
        # Different parameter sets to try
        param_sets = [
            {
                'name': 'Basic Parameters',
                'params': {
                    'key': self.api_key,
                    'tcin': tcin,
                    'store_id': self.store_id,
                }
            },
            {
                'name': 'With Store Context',
                'params': {
                    'key': self.api_key,
                    'tcin': tcin,
                    'store_id': self.store_id,
                    'pricing_store_id': self.store_id,
                    'has_store_id': 'true',
                }
            },
            {
                'name': 'Full Context Parameters', 
                'params': {
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
                }
            },
            {
                'name': 'With Fulfillment Context',
                'params': {
                    'key': self.api_key,
                    'tcin': tcin,
                    'store_id': self.store_id,
                    'pricing_store_id': self.store_id,
                    'visitor_id': self.generate_visitor_id(),
                    'has_fulfillment_context': 'true',
                    'has_promotion_context': 'true',
                    'has_size_context': 'true',
                    'channel': 'WEB',
                }
            }
        ]
        
        print(f"\\n🧪 TESTING DIFFERENT PARAMETERS FOR {description} (TCIN {tcin})")
        print("="*70)
        
        for param_set in param_sets:
            print(f"\\nTesting: {param_set['name']}")
            try:
                response = requests.get(
                    base_url,
                    params=param_set['params'],
                    headers=self.get_headers(),
                    timeout=15
                )
                
                if response.status_code == 200:
                    data = response.json()
                    item = data['data']['product']['item']
                    
                    print(f"  ✅ Success!")
                    print(f"  Has eligibility_rules: {'eligibility_rules' in item}")
                    
                    if 'eligibility_rules' in item:
                        eligibility = item['eligibility_rules']
                        print(f"  🎯 ELIGIBILITY RULES FOUND!")
                        for rule_name, rule_data in eligibility.items():
                            if isinstance(rule_data, dict) and 'is_active' in rule_data:
                                status = "✅ ACTIVE" if rule_data['is_active'] else "❌ INACTIVE"
                                print(f"    {rule_name}: {status}")
                    
                    # Check for any new fields
                    all_fields = set(item.keys())
                    print(f"  Fields: {len(all_fields)} total")
                    
                else:
                    print(f"  ❌ HTTP {response.status_code}")
                    
            except Exception as e:
                print(f"  ❌ Error: {e}")
            
            time.sleep(0.5)  # Be nice to API

    def test_search_api(self, tcin: str, description: str):
        """Test search API which might have different availability data"""
        search_url = "https://redsky.target.com/redsky_aggregations/v1/web/plp_search_v1"
        
        print(f"\\n🔍 TESTING SEARCH API FOR {description} (TCIN {tcin})")
        print("="*70)
        
        params = {
            'key': self.api_key,
            'category': '5xtg6',
            'channel': 'WEB',
            'count': '24',
            'default_purchasability_filter': 'false',
            'include_sponsored': 'true',
            'keyword': f'tcin:{tcin}',
            'offset': '0',
            'page': f'/s/tcin:{tcin}',
            'platform': 'desktop',
            'pricing_store_id': self.store_id,
            'store_ids': self.store_id,
            'visitor_id': self.generate_visitor_id()
        }
        
        try:
            response = requests.get(
                search_url,
                params=params,
                headers=self.get_headers(),
                timeout=15
            )
            
            if response.status_code == 200:
                data = response.json()
                search_response = data.get('data', {}).get('search', {})
                products = search_response.get('products', [])
                
                if products:
                    product = products[0]
                    item = product.get('item', {})
                    
                    print(f"  ✅ Found in search!")
                    print(f"  Has eligibility_rules: {'eligibility_rules' in item}")
                    
                    if 'eligibility_rules' in item:
                        eligibility = item['eligibility_rules']
                        print(f"  🎯 SEARCH API HAS ELIGIBILITY RULES!")
                        for rule_name, rule_data in eligibility.items():
                            if isinstance(rule_data, dict) and 'is_active' in rule_data:
                                status = "✅ ACTIVE" if rule_data['is_active'] else "❌ INACTIVE"
                                print(f"    {rule_name}: {status}")
                    
                    # Check fulfillment from search
                    fulfillment = item.get('fulfillment', {})
                    print(f"  Purchase limit: {fulfillment.get('purchase_limit', 0)}")
                    is_marketplace = fulfillment.get('is_marketplace', False)
                    print(f"  Is marketplace: {is_marketplace}")
                    
                    # Save search response
                    filename = f"search_response_{tcin}.json"
                    with open(filename, 'w') as f:
                        json.dump(data, f, indent=2)
                    print(f"  💾 Search response saved to: {filename}")
                    
                else:
                    print(f"  ❌ No products found in search")
            else:
                print(f"  ❌ HTTP {response.status_code}")
                
        except Exception as e:
            print(f"  ❌ Error: {e}")

def main():
    tester = APIParameterTester()
    
    # Test all three TCINs with different parameters
    test_cases = [
        ('94681776', 'NOT AVAILABLE preorder'),
        ('94723520', 'AVAILABLE preorder'),  
        ('94827553', 'AVAILABLE preorder (27 days out)')
    ]
    
    for tcin, description in test_cases:
        # Test PDP API with different parameters
        tester.test_pdp_with_different_params(tcin, description)
        
        # Test search API
        tester.test_search_api(tcin, description)
        
        time.sleep(1)

if __name__ == "__main__":
    main()
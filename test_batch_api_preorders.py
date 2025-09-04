#!/usr/bin/env python3
"""
Test the batch API that the dashboard uses - it might have different preorder data
This is the same API your batch_stock_checker.py uses
"""

import requests
import json
import time
import random

class BatchAPIPreorderTester:
    def __init__(self):
        self.base_url = "https://redsky.target.com/redsky_aggregations/v1/web/plp_search_v1"
        self.api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
        self.store_id = "865"
        
    def generate_visitor_id(self):
        timestamp = int(time.time() * 1000)
        random_suffix = ''.join(random.choices('0123456789ABCDEF', k=16))
        return f"{timestamp:016X}{random_suffix}"
        
    def get_headers(self):
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
        ]
        
        user_agent = random.choice(user_agents)
        version = user_agent.split('Chrome/')[1].split('.')[0]
        
        return {
            'accept': 'application/json',
            'accept-encoding': 'gzip, deflate, br, zstd',
            'accept-language': 'en-US,en;q=0.9',
            'cache-control': 'no-cache',
            'origin': 'https://www.target.com',
            'referer': 'https://www.target.com/',
            'sec-ch-ua': f'"Chromium";v="{version}", "Google Chrome";v="{version}", "Not?A_Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
            'user-agent': user_agent,
        }

    def test_single_tcin_batch(self, tcin: str, description: str):
        """Test single TCIN using batch/search API"""
        print(f"\\n🧪 TESTING BATCH API FOR {description} (TCIN {tcin})")
        print("="*70)
        
        # Use same parameters as your batch_stock_checker.py
        params = {
            'key': self.api_key,
            'category': '5xtg6',  # General category
            'channel': 'WEB',
            'count': '24',
            'default_purchasability_filter': 'false',  # Important: don't filter out unavailable items
            'include_sponsored': 'true',
            'keyword': f'tcin:{tcin}',  # Search for this specific TCIN
            'offset': '0',
            'page': f'/s/tcin:{tcin}',
            'platform': 'desktop',
            'pricing_store_id': self.store_id,
            'store_ids': self.store_id,
            'useragent': 'Mozilla/5.0',
            'visitor_id': self.generate_visitor_id()
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
                filename = f"batch_api_response_{tcin}_{description.replace(' ', '_')}.json"
                with open(filename, 'w') as f:
                    json.dump(data, f, indent=2)
                print(f"💾 Batch API response saved to: {filename}")
                
                # Analyze the response
                self.analyze_batch_response(tcin, data, description)
                return data
                
            else:
                print(f"❌ Batch API failed: HTTP {response.status_code}")
                print(f"Response: {response.text[:500]}")
                return None
                
        except Exception as e:
            print(f"❌ Batch API error: {e}")
            return None
    
    def analyze_batch_response(self, tcin: str, data: dict, description: str):
        """Analyze batch API response for availability data"""
        try:
            search_response = data.get('data', {}).get('search', {})
            products = search_response.get('products', [])
            
            print(f"🔍 BATCH API ANALYSIS:")
            print(f"  Found {len(products)} products")
            
            if not products:
                print(f"  ❌ TCIN not found in batch search")
                return
            
            # Should be exactly 1 product for our TCIN search
            product = products[0]
            found_tcin = product.get('tcin')
            
            if found_tcin != tcin:
                print(f"  ⚠️  Found different TCIN: {found_tcin}")
            
            # Analyze item data from batch API
            item = product.get('item', {})
            print(f"\\n📦 BATCH API PRODUCT DATA:")
            print(f"  TCIN: {found_tcin}")
            
            # Check if batch API has eligibility_rules
            has_eligibility = 'eligibility_rules' in item
            print(f"  Has eligibility_rules: {has_eligibility}")
            
            if has_eligibility:
                print(f"  🎯 BATCH API HAS ELIGIBILITY RULES!")
                eligibility = item['eligibility_rules']
                for rule_name, rule_data in eligibility.items():
                    if isinstance(rule_data, dict) and 'is_active' in rule_data:
                        status = "✅ ACTIVE" if rule_data['is_active'] else "❌ INACTIVE"
                        print(f"    {rule_name}: {status}")
            
            # Check fulfillment from batch API
            fulfillment = item.get('fulfillment', {})
            print(f"\\n🚚 BATCH API FULFILLMENT:")
            for key, value in fulfillment.items():
                print(f"    {key}: {value}")
            
            # Check for batch-specific availability fields
            availability_fields = []
            for key, value in item.items():
                if any(word in key.lower() for word in ['available', 'purchas', 'eligible', 'active']):
                    availability_fields.append((key, value))
            
            if availability_fields:
                print(f"\\n🎯 AVAILABILITY-RELATED FIELDS:")
                for field, value in availability_fields:
                    print(f"    {field}: {value}")
            
            # Compare with individual PDP data if we have it
            print(f"\\n📊 FIELD COMPARISON:")
            individual_fields = set()
            batch_fields = set(item.keys())
            
            # Try to load individual API response for comparison
            individual_files = [
                f'preorder_response_{tcin}_Out_of_Stock_Pre-order.json',
                f'preorder_response_{tcin}_In_Stock_Pre-order.json', 
                f'tcin_response_{tcin}.json'
            ]
            
            individual_data = None
            for filename in individual_files:
                try:
                    with open(filename) as f:
                        individual_response = json.load(f)
                        individual_data = individual_response['data']['product']['item']
                        individual_fields = set(individual_data.keys())
                        break
                except:
                    continue
            
            if individual_data:
                print(f"    Individual API fields: {len(individual_fields)}")
                print(f"    Batch API fields: {len(batch_fields)}")
                
                only_in_batch = batch_fields - individual_fields
                only_in_individual = individual_fields - batch_fields
                
                if only_in_batch:
                    print(f"    🎯 ONLY IN BATCH API: {sorted(only_in_batch)}")
                    for field in only_in_batch:
                        print(f"      {field}: {item[field]}")
                
                if only_in_individual:
                    print(f"    🎯 ONLY IN INDIVIDUAL API: {sorted(only_in_individual)}")
            
        except Exception as e:
            print(f"❌ Analysis error: {e}")

    def test_multiple_tcins_batch(self, tcins: list):
        """Test multiple TCINs at once using batch API (like your dashboard does)"""
        print(f"\\n🧪 TESTING MULTIPLE TCINs IN SINGLE BATCH REQUEST")
        print("="*70)
        
        # Create OR query for multiple TCINs (same as batch_stock_checker.py)
        tcin_query = " OR ".join([f"tcin:{tcin}" for tcin in tcins])
        
        params = {
            'key': self.api_key,
            'category': '5xtg6',
            'channel': 'WEB',
            'count': '24',
            'default_purchasability_filter': 'false',
            'include_sponsored': 'true',
            'keyword': tcin_query,  # Search for multiple TCINs
            'offset': '0',
            'page': f'/s/{tcin_query}',
            'platform': 'desktop',
            'pricing_store_id': self.store_id,
            'store_ids': self.store_id,
            'visitor_id': self.generate_visitor_id()
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
                
                filename = f"batch_multi_tcin_response.json"
                with open(filename, 'w') as f:
                    json.dump(data, f, indent=2)
                print(f"💾 Multi-TCIN batch response saved to: {filename}")
                
                # Analyze results
                search_response = data.get('data', {}).get('search', {})
                products = search_response.get('products', [])
                
                print(f"\\n📊 MULTI-TCIN RESULTS:")
                print(f"  Requested TCINs: {tcins}")
                print(f"  Found products: {len(products)}")
                
                for product in products:
                    tcin = product.get('tcin')
                    item = product.get('item', {})
                    fulfillment = item.get('fulfillment', {})
                    purchase_limit = fulfillment.get('purchase_limit', 0)
                    has_eligibility = 'eligibility_rules' in item
                    
                    print(f"\\n  TCIN {tcin}:")
                    print(f"    Purchase limit: {purchase_limit}")
                    print(f"    Has eligibility_rules: {has_eligibility}")
                    
                    if has_eligibility:
                        eligibility = item['eligibility_rules']
                        active_rules = []
                        for rule_name, rule_data in eligibility.items():
                            if isinstance(rule_data, dict) and rule_data.get('is_active'):
                                active_rules.append(rule_name)
                        print(f"    Active eligibility rules: {active_rules}")
                
                return data
                
            else:
                print(f"❌ Multi-TCIN batch failed: HTTP {response.status_code}")
                return None
                
        except Exception as e:
            print(f"❌ Multi-TCIN batch error: {e}")
            return None

def main():
    tester = BatchAPIPreorderTester()
    
    # Test individual TCINs with batch API
    test_cases = [
        ('94681776', 'NOT AVAILABLE preorder'),
        ('94723520', 'AVAILABLE preorder'),
        ('94827553', 'AVAILABLE preorder (user confirmed)')
    ]
    
    print("🔍 TESTING BATCH API FOR PREORDER AVAILABILITY")
    print("This is the same API your dashboard uses")
    
    for tcin, description in test_cases:
        tester.test_single_tcin_batch(tcin, description)
        time.sleep(2)  # Be nice to API
    
    # Also test all together (like dashboard does)
    all_tcins = [case[0] for case in test_cases]
    tester.test_multiple_tcins_batch(all_tcins)
    
    print(f"\\n🎯 BATCH API RESEARCH COMPLETE")
    print("Check if batch API provides different/better availability data!")

if __name__ == "__main__":
    main()
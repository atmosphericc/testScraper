#!/usr/bin/env python3
"""
Test APIs using the member_id extracted from authenticated session
"""
import sys
import asyncio
import aiohttp
import json
sys.path.insert(0, 'src')
from stock_checker import StockChecker

async def test_apis_with_member_id():
    """Test APIs using member_id from authenticated session"""
    
    # Extracted from authenticated session
    member_id = "20023424802"
    visitor_id = "0198E42C9A2102019DDFC73B9431C118"
    
    print("TESTING APIs WITH AUTHENTICATED MEMBER_ID")
    print("="*60)
    print(f"Using member_id: {member_id}")
    print(f"Using visitor_id: {visitor_id}")
    print("="*60)
    
    checker = StockChecker(use_website_checking=False)
    
    async with aiohttp.ClientSession() as session:
        
        # Test 1: Cart API with member_id
        print("\n1. TESTING CART API WITH MEMBER_ID")
        print("-"*50)
        
        cart_url = "https://carts.target.com/web_checkouts/v1/cart"
        cart_params = {
            'cart_type': 'REGULAR',
            'field_groups': 'ADDRESSES,CART_ITEMS,SUMMARY',
            'key': checker.api_key,
            'client_feature': 'add_to_cart',
            'member_id': member_id,
            'visitor_id': visitor_id
        }
        
        headers = checker.get_headers()
        
        try:
            async with session.get(cart_url, params=cart_params, headers=headers, timeout=10) as response:
                print(f"   Status: {response.status}")
                
                if response.status == 200:
                    data = await response.json()
                    
                    with open('cart_with_member_id.json', 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    
                    print("   SUCCESS! Cart API with member_id worked!")
                    print(f"   Response keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
                    
                    # Analyze cart contents
                    if isinstance(data, dict):
                        cart_items = data.get('cart_items', [])
                        print(f"   Cart items: {len(cart_items)}")
                        
                        summary = data.get('summary', {})
                        if summary:
                            print(f"   Summary keys: {list(summary.keys())}")
                else:
                    error_text = await response.text()
                    print(f"   Error {response.status}: {error_text[:200]}")
                    
        except Exception as e:
            print(f"   Exception: {e}")
        
        # Test 2: Try redsky APIs with member_id
        print(f"\n2. TESTING REDSKY APIs WITH MEMBER_ID")
        print("-"*50)
        
        test_products = [
            ('89542109', 'KNOWN IN STOCK'),
            ('94724987', 'KNOWN OUT OF STOCK')
        ]
        
        for tcin, status in test_products:
            print(f"\n   Testing {tcin} ({status})")
            
            # Try the fulfillment API with member_id
            fulfillment_url = "https://redsky.target.com/redsky_aggregations/v1/web/product_fulfillment_and_variation_hierarchy_v1"
            fulfillment_params = {
                'key': checker.api_key,
                'tcin': tcin,
                'store_id': checker.store_id,
                'pricing_store_id': checker.store_id,
                'scheduled_delivery_store_id': checker.store_id,
                'latitude': '42.056656',
                'longitude': '-87.968300',
                'state': 'IL',
                'zip': '60056',
                'paid_membership': 'false',
                'base_membership': 'false',
                'card_membership': 'false',
                'is_bot': 'false',
                'member_id': member_id,
                'visitor_id': visitor_id,
                'channel': 'WEB',
                'page': f'/p/A-{tcin}'
            }
            
            headers = checker.get_headers()
            
            try:
                async with session.get(fulfillment_url, params=fulfillment_params, headers=headers, timeout=10) as response:
                    print(f"     Fulfillment API: {response.status}")
                    
                    if response.status == 200:
                        data = await response.json()
                        
                        with open(f'fulfillment_with_member_{tcin}.json', 'w', encoding='utf-8') as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)
                        
                        print(f"     SUCCESS! Fulfillment API worked for {tcin}")
                        
                        # Quick analysis for inventory data
                        if isinstance(data, dict):
                            print(f"     Keys: {list(data.keys())}")
                            
                            response_str = json.dumps(data).lower()
                            inventory_keywords = ['available', 'stock', 'inventory', 'purchasable', 'fulfillment', 'add_to_cart', 'eligible']
                            found = [kw for kw in inventory_keywords if kw in response_str]
                            if found:
                                print(f"     Inventory keywords: {found}")
                    else:
                        error_text = await response.text()
                        print(f"     Error: {error_text[:100]}")
                        
            except Exception as e:
                print(f"     Exception: {e}")
        
        # Test 3: Try the regular pdp_client_v1 API with member_id
        print(f"\n3. TESTING PDP_CLIENT_V1 API WITH MEMBER_ID")
        print("-"*50)
        
        for tcin, status in test_products:
            print(f"\n   Testing {tcin} ({status})")
            
            pdp_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
            pdp_params = {
                'key': checker.api_key,
                'tcin': tcin,
                'store_id': checker.store_id,
                'pricing_store_id': checker.store_id,
                'has_pricing_store_id': 'true',
                'has_financing_options': 'true',
                'visitor_id': visitor_id,
                'member_id': member_id,
                'has_size_context': 'true',
                'channel': 'WEB',
                'page': f'/p/A-{tcin}'
            }
            
            headers = checker.get_headers()
            
            try:
                async with session.get(pdp_url, params=pdp_params, headers=headers, timeout=10) as response:
                    print(f"     PDP Client API: {response.status}")
                    
                    if response.status == 200:
                        data = await response.json()
                        
                        with open(f'pdp_client_with_member_{tcin}.json', 'w', encoding='utf-8') as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)
                        
                        print(f"     SUCCESS! PDP Client API worked for {tcin}")
                        print(f"     This could be better than our current API!")
                        
                        # Quick comparison with current API response
                        if isinstance(data, dict) and 'data' in data:
                            product_data = data['data'].get('product', {})
                            item_data = product_data.get('item', {})
                            
                            if item_data:
                                fulfillment = item_data.get('fulfillment', {})
                                eligibility = item_data.get('eligibility_rules', {})
                                
                                print(f"     Fulfillment keys: {list(fulfillment.keys())}")
                                print(f"     Eligibility rules: {len(eligibility)} rules")
                                
                                if eligibility:
                                    for rule, details in eligibility.items():
                                        print(f"       {rule}: {details}")
                    else:
                        print(f"     Still {response.status}")
                        
            except Exception as e:
                print(f"     Exception: {e}")

async def main():
    """Run all member_id tests"""
    await test_apis_with_member_id()
    
    print(f"\n{'='*60}")
    print("MEMBER_ID API TESTING COMPLETE")
    print("="*60)
    print("Check for any new JSON files with inventory data")

if __name__ == "__main__":
    asyncio.run(main())
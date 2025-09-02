#!/usr/bin/env python3
"""
Test discovered APIs with proper authentication headers from working stock checker
"""
import sys
import asyncio
import aiohttp
import json
import random
import time
sys.path.insert(0, 'src')
from stock_checker import StockChecker

async def test_with_proper_auth():
    """Test discovered APIs with proper authentication and headers"""
    
    # Test products with known stock status
    products = [
        ('89542109', 'KNOWN IN STOCK'),
        ('94724987', 'KNOWN OUT OF STOCK'), 
        ('94681785', 'KNOWN OUT OF STOCK')
    ]
    
    print("TESTING DISCOVERED APIs WITH PROPER AUTHENTICATION")
    print("="*70)
    
    # Use the same setup as working stock checker
    checker = StockChecker(use_website_checking=False)
    
    async with aiohttp.ClientSession() as session:
        
        # Test 1: Cart API with proper headers
        print("\n1. TESTING CART API WITH PROPER AUTH")
        print("-"*50)
        
        cart_url = "https://carts.target.com/web_checkouts/v1/cart"
        cart_params = {
            'cart_type': 'REGULAR',
            'field_groups': 'ADDRESSES,CART_ITEMS,SUMMARY',
            'key': checker.api_key,
            'client_feature': 'add_to_cart'
        }
        
        headers = checker.get_headers()
        
        try:
            async with session.get(cart_url, params=cart_params, headers=headers, timeout=10) as response:
                print(f"   Status: {response.status}")
                
                if response.status == 200:
                    data = await response.json()
                    
                    with open('authenticated_cart_response.json', 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    
                    print("   Success! Cart API response saved")
                    print(f"   Response keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
                    
                    # Look for inventory-related info
                    if isinstance(data, dict):
                        cart_items = data.get('cart_items', [])
                        print(f"   Cart items: {len(cart_items)}")
                        
                else:
                    error_text = await response.text()
                    print(f"   Error {response.status}: {error_text[:200]}")
                    
        except Exception as e:
            print(f"   Exception: {e}")
        
        # Test 2: Product fulfillment API with proper headers  
        print(f"\n2. TESTING PRODUCT FULFILLMENT APIs WITH PROPER AUTH")
        print("-"*60)
        
        for tcin, status in products:
            print(f"\n   Testing {tcin} ({status})")
            
            # Try the fulfillment API that returned 404 before
            fulfillment_url = "https://redsky.target.com/redsky_aggregations/v1/web/product_fulfillment_and_variation_hierarchy_v1"
            fulfillment_params = {
                'key': checker.api_key,
                'tcin': tcin,
                'store_id': checker.store_id,
                'pricing_store_id': checker.store_id,
                'scheduled_delivery_store_id': checker.store_id,
                'latitude': '42.080',
                'longitude': '-87.730',
                'state': 'IL',
                'zip': '60091',
                'paid_membership': 'false',
                'base_membership': 'false', 
                'card_membership': 'false',
                'is_bot': 'false',
                'visitor_id': checker.generate_visitor_id(),
                'channel': 'WEB',
                'page': f'/p/A-{tcin}'
            }
            
            headers = checker.get_headers()
            
            try:
                async with session.get(fulfillment_url, params=fulfillment_params, headers=headers, timeout=10) as response:
                    print(f"     Fulfillment API Status: {response.status}")
                    
                    if response.status == 200:
                        data = await response.json()
                        
                        with open(f'authenticated_fulfillment_{tcin}.json', 'w', encoding='utf-8') as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)
                        
                        print(f"     SUCCESS! Fulfillment response saved for {tcin}")
                        
                        # Quick analysis
                        if isinstance(data, dict):
                            print(f"     Response keys: {list(data.keys())}")
                            
                            # Look for inventory indicators
                            response_str = json.dumps(data).lower()
                            inventory_keywords = ['available', 'stock', 'inventory', 'purchasable', 'fulfillment', 'sellable']
                            found = [kw for kw in inventory_keywords if kw in response_str]
                            if found:
                                print(f"     Inventory keywords: {found}")
                    
                    elif response.status == 404:
                        print(f"     Still 404 - endpoint may not exist")
                    else:
                        error_text = await response.text()
                        print(f"     Error: {error_text[:100]}")
                        
            except Exception as e:
                print(f"     Exception: {e}")
        
        # Test 3: Try other redsky endpoints with proper auth
        print(f"\n3. TESTING OTHER REDSKY ENDPOINTS")
        print("-"*40)
        
        other_endpoints = [
            ('nearby_stores_v1', {
                'limit': '5',
                'within': '100',
                'place': '60091'
            }),
            ('store_location_v1', {
                'store_id': checker.store_id
            })
        ]
        
        for endpoint, extra_params in other_endpoints:
            print(f"\n   Testing {endpoint}")
            
            url = f"https://redsky.target.com/redsky_aggregations/v1/web/{endpoint}"
            params = {
                'key': checker.api_key,
                'visitor_id': checker.generate_visitor_id(),
                'channel': 'WEB',
                **extra_params
            }
            
            headers = checker.get_headers()
            
            try:
                async with session.get(url, params=params, headers=headers, timeout=10) as response:
                    print(f"     {endpoint} Status: {response.status}")
                    
                    if response.status == 200:
                        data = await response.json()
                        
                        with open(f'authenticated_{endpoint}_response.json', 'w', encoding='utf-8') as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)
                        
                        print(f"     SUCCESS! {endpoint} response saved")
                        if isinstance(data, dict):
                            print(f"     Keys: {list(data.keys())}")
                    else:
                        print(f"     Still getting {response.status}")
                        
            except Exception as e:
                print(f"     Exception: {e}")

async def main():
    """Run authenticated API tests"""
    await test_with_proper_auth()
    
    print(f"\n{'='*70}")
    print("AUTHENTICATED API TESTING COMPLETE")
    print("="*70)
    print("Check generated JSON files for any new inventory data")
    print("Look for differences between in-stock vs out-of-stock responses")

if __name__ == "__main__":
    asyncio.run(main())
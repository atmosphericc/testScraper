#!/usr/bin/env python3
"""
Test the discovered real-time inventory APIs from network analysis
"""
import sys
import asyncio
import aiohttp
import json
sys.path.insert(0, 'src')

async def test_fulfillment_api():
    """Test the product fulfillment API that might have real-time data"""
    
    # Test products with known stock status
    products = [
        ('89542109', 'KNOWN IN STOCK'),
        ('94724987', 'KNOWN OUT OF STOCK'),
        ('94681785', 'KNOWN OUT OF STOCK')
    ]
    
    print("TESTING DISCOVERED FULFILLMENT API")
    print("="*60)
    print("API: product_fulfillment_and_variation_hierarchy_v1")
    print("="*60)
    
    async with aiohttp.ClientSession() as session:
        for tcin, status in products:
            print(f"\n{tcin} - {status}")
            print("-"*50)
            
            # Use exact parameters from network capture
            url = "https://redsky.target.com/redsky_aggregations/v1/web/product_fulfillment_and_variation_hierarchy_v1"
            params = {
                'key': '9f36aeafbe60771e321a7cc95a78140772ab3e96',
                'latitude': '42.080',
                'longitude': '-87.730', 
                'scheduled_delivery_store_id': '927',
                'state': 'IL',
                'zip': '60091',
                'paid_membership': 'false',
                'base_membership': 'false',
                'card_membership': 'false',
                'is_bot': 'false',
                'tcin': tcin,
                'visitor_id': '019901555A8202018122202A54DD0CD7',
                'channel': 'WEB'
            }
            
            headers = {
                'accept': 'application/json',
                'accept-language': 'en-US,en;q=0.9',
                'origin': 'https://www.target.com',
                'referer': f'https://www.target.com/p/-/A-{tcin}',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
            }
            
            try:
                async with session.get(url, params=params, headers=headers, timeout=10) as response:
                    print(f"   Status: {response.status}")
                    
                    if response.status == 200:
                        data = await response.json()
                        
                        # Save response for analysis
                        with open(f'fulfillment_response_{tcin}.json', 'w', encoding='utf-8') as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)
                        
                        print(f"   Response saved: fulfillment_response_{tcin}.json")
                        
                        # Quick analysis
                        if 'data' in data:
                            product_data = data.get('data', {})
                            print(f"   Data keys: {list(product_data.keys())}")
                            
                            # Look for fulfillment/availability info
                            fulfillment_info = product_data.get('product', {}).get('fulfillment', {})
                            if fulfillment_info:
                                print(f"   Fulfillment info found!")
                                print(f"   Fulfillment keys: {list(fulfillment_info.keys())}")
                            
                            # Look for inventory-related fields
                            inventory_keywords = ['available', 'stock', 'inventory', 'purchasable', 'sellable']
                            response_str = json.dumps(data).lower()
                            found_keywords = [kw for kw in inventory_keywords if kw in response_str]
                            if found_keywords:
                                print(f"   Inventory keywords found: {found_keywords}")
                        
                    elif response.status == 404:
                        print(f"   404 - Endpoint not found or no data for {tcin}")
                    else:
                        print(f"   Error: {response.status}")
                        error_text = await response.text()
                        print(f"   Error details: {error_text[:200]}")
                        
            except Exception as e:
                print(f"   Exception: {e}")

async def test_cart_api():
    """Test the cart API that might show real-time availability"""
    
    print(f"\n{'='*60}")
    print("TESTING CART API")
    print("="*60)
    
    url = "https://carts.target.com/web_checkouts/v1/cart"
    params = {
        'cart_type': 'REGULAR',
        'field_groups': 'ADDRESSES,CART_ITEMS,SUMMARY',
        'key': '9f36aeafbe60771e321a7cc95a78140772ab3e96',
        'client_feature': 'add_to_cart'
    }
    
    headers = {
        'accept': 'application/json',
        'accept-language': 'en-US,en;q=0.9',
        'origin': 'https://www.target.com',
        'referer': 'https://www.target.com/',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params, headers=headers, timeout=10) as response:
                print(f"Status: {response.status}")
                
                if response.status == 200:
                    data = await response.json()
                    
                    with open('cart_api_response.json', 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    
                    print("Cart API response saved: cart_api_response.json")
                    print(f"Response keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
                else:
                    error_text = await response.text()
                    print(f"Error: {error_text[:200]}")
                    
        except Exception as e:
            print(f"Exception: {e}")

async def main():
    """Run all API tests"""
    await test_fulfillment_api()
    await test_cart_api()
    
    print(f"\n{'='*60}")
    print("ANALYSIS COMPLETE")
    print("="*60)
    print("Check the generated JSON files for detailed API responses")
    print("Look for inventory/availability indicators in the responses")

if __name__ == "__main__":
    asyncio.run(main())
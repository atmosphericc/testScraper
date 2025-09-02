#!/usr/bin/env python3
"""
Test the APIs that actually worked from network analysis
"""
import sys
import asyncio
import aiohttp
import json

async def test_sapphire_api():
    """Test the Sapphire API that returned 200"""
    
    products = [
        ('89542109', 'KNOWN IN STOCK'),
        ('94724987', 'KNOWN OUT OF STOCK'),
        ('94681785', 'KNOWN OUT OF STOCK')
    ]
    
    print("TESTING WORKING SAPPHIRE API")
    print("="*60)
    
    async with aiohttp.ClientSession() as session:
        for tcin, status in products:
            print(f"\n{tcin} - {status}")
            print("-"*50)
            
            # Test the Sapphire API that worked
            url = f"https://sapphire-api.target.com/sapphire/runtime/api/v1/raw/www.target.com/p/-/A-{tcin}"
            params = {
                'channel': 'web',
                'context': 'geo,60091|42.080|-87.730|IL|US',
                'service': 'redoak,digital-web',
                'source': 'top-of-funnel',
                'state': 'IL',
                'tm': 'false',
                'visitor_id': '019901555A8202018122202A54DD0CD7',
                'zip': '60091'
            }
            
            headers = {
                'accept': 'application/json',
                'accept-language': 'en-US,en;q=0.9',
                'origin': 'https://www.target.com',
                'referer': f'https://www.target.com/p/-/A-{tcin}',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
            }
            
            try:
                async with session.get(url, params=params, headers=headers, timeout=15) as response:
                    print(f"   Status: {response.status}")
                    
                    if response.status == 200:
                        data = await response.json()
                        
                        # Save full response
                        with open(f'sapphire_response_{tcin}.json', 'w', encoding='utf-8') as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)
                        
                        print(f"   Response saved: sapphire_response_{tcin}.json")
                        
                        # Quick analysis for stock indicators
                        response_str = json.dumps(data).lower()
                        stock_keywords = [
                            'available', 'stock', 'inventory', 'purchasable', 'sellable',
                            'out_of_stock', 'in_stock', 'quantity', 'fulfillment', 'add_to_cart'
                        ]
                        
                        found_keywords = [kw for kw in stock_keywords if kw in response_str]
                        if found_keywords:
                            print(f"   Stock-related keywords: {found_keywords}")
                        
                        # Look at top-level structure
                        if isinstance(data, dict):
                            print(f"   Top-level keys: {list(data.keys())}")
                            
                            # Check for product data
                            if 'product' in data:
                                product_data = data['product']
                                print(f"   Product keys: {list(product_data.keys()) if isinstance(product_data, dict) else 'Not a dict'}")
                        
                    else:
                        error_text = await response.text()
                        print(f"   Error: {error_text[:200]}")
                        
            except Exception as e:
                print(f"   Exception: {e}")

async def test_location_fulfillment_api():
    """Test the location fulfillment API that worked"""
    
    print(f"\n{'='*60}")
    print("TESTING LOCATION FULFILLMENT API")
    print("="*60)
    
    url = "https://api.target.com/location_fulfillment_aggregations/v1/preferred_stores"
    params = {
        'key': '9f36aeafbe60771e321a7cc95a78140772ab3e96',
        'zipcode': '60091'
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
                    
                    with open('location_fulfillment_response.json', 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    
                    print("Response saved: location_fulfillment_response.json")
                    print(f"Response keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
                    
                    # This might contain store-specific fulfillment info
                    if isinstance(data, dict) and 'stores' in data:
                        stores = data['stores']
                        print(f"   Found {len(stores)} stores")
                        if stores:
                            print(f"   First store keys: {list(stores[0].keys())}")
                else:
                    error_text = await response.text()
                    print(f"Error: {error_text[:200]}")
                    
        except Exception as e:
            print(f"Exception: {e}")

async def main():
    """Test the working APIs"""
    await test_sapphire_api()
    await test_location_fulfillment_api()
    
    print(f"\n{'='*60}")
    print("WORKING API ANALYSIS COMPLETE")
    print("="*60)
    print("Check the JSON files for potential inventory indicators")

if __name__ == "__main__":
    asyncio.run(main())
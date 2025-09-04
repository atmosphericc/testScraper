#!/usr/bin/env python3
"""
Debug API calls to see what's causing errors
"""
import asyncio
import aiohttp
import json
import random
from datetime import datetime

async def test_api():
    config_path = 'config/product_config.json'
    
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
    base_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
    
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        for product in config['products'][:2]:  # Test first 2 products only
            if product.get('enabled', True):
                tcin = product['tcin']
                print(f"\n🔍 Testing TCIN: {tcin}")
                
                try:
                    params = {
                        'key': api_key,
                        'tcin': tcin,
                        'store_id': '865',
                        'pricing_store_id': '865',
                        'has_pricing_store_id': 'true',
                        'visitor_id': ''.join(random.choices('0123456789ABCDEF', k=32))
                    }
                    
                    headers = {
                        'accept': 'application/json',
                        'origin': 'https://www.target.com',
                        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    }
                    
                    async with session.get(base_url, params=params, headers=headers) as response:
                        print(f"Status: {response.status}")
                        if response.status == 200:
                            data = await response.json()
                            product_data = data.get('data', {}).get('product', {})
                            print(f"✅ SUCCESS: Got product data")
                            print(f"Product keys: {list(product_data.keys())[:5]}")  # Show first 5 keys
                        else:
                            text = await response.text()
                            print(f"❌ ERROR: {response.status}")
                            print(f"Response: {text[:200]}")
                            
                except Exception as e:
                    print(f"❌ EXCEPTION: {e}")

if __name__ == "__main__":
    asyncio.run(test_api())
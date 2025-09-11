#!/usr/bin/env python3
"""
Detailed API test to examine the full response structure and fix the data processing
"""

import requests
import json
import time
from pathlib import Path

def detailed_api_test():
    """Test the API and show the full response structure"""
    
    endpoint = 'https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1'
    api_key = "ff457966e64d5e877fdbad070f276d18ecec4a01"
    
    headers = {
        'accept': 'application/json',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'accept-encoding': 'gzip, deflate, br',
        'accept-language': 'en-US,en;q=0.9',
        'cache-control': 'no-cache',
        'referer': 'https://www.target.com/',
    }
    
    # Load your actual product config
    try:
        config_paths = [
            "config/product_config.json",
            "../config/product_config.json"
        ]
        
        config = None
        for path in config_paths:
            if Path(path).exists():
                with open(path, 'r') as f:
                    config = json.load(f)
                break
        
        if not config:
            print("Could not find product config file")
            return
            
        products = config.get('products', [])
        enabled_products = [p for p in products if p.get('enabled', True)]
        
        if not enabled_products:
            print("No enabled products found in config")
            return
            
        # Test with first 3 products
        test_products = enabled_products[:3]
        test_tcins = [p['tcin'] for p in test_products]
        
        print(f"Testing with TCINs: {test_tcins}")
        print(f"Expected product names: {[p.get('name', 'Unknown') for p in test_products]}")
        
        params = {
            'key': api_key,
            'tcins': ','.join(test_tcins),
            'store_id': '1859',
            'pricing_store_id': '1859',
            'has_pricing_context': 'true',
            'has_promotions': 'true',
            'is_bot': 'false',
        }
        
        print(f"\nAPI Call:")
        print(f"URL: {endpoint}")
        print(f"Params: {params}")
        
        response = requests.get(endpoint, params=params, headers=headers, timeout=15)
        print(f"\nResponse Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            # Save the full response for analysis
            with open('api_response_debug.json', 'w') as f:
                json.dump(data, f, indent=2)
            print("Full response saved to api_response_debug.json")
            
            print(f"\nTop-level response keys: {list(data.keys())}")
            
            if 'data' in data:
                print(f"Data section keys: {list(data['data'].keys())}")
                
                if 'product_summaries' in data['data']:
                    summaries = data['data']['product_summaries']
                    print(f"Found {len(summaries)} product summaries")
                    
                    for i, product in enumerate(summaries):
                        print(f"\n--- Product {i+1} ---")
                        print(f"Top-level keys: {list(product.keys())}")
                        
                        # Check various name fields
                        tcin = product.get('tcin', 'Unknown')
                        name_fields = [
                            product.get('name'),
                            product.get('title'),
                            product.get('product_description', {}).get('title'),
                            product.get('item', {}).get('product_description', {}).get('title'),
                        ]
                        
                        print(f"TCIN: {tcin}")
                        print(f"Name field: {product.get('name')}")
                        print(f"Title field: {product.get('title')}")
                        
                        # Check if there's an 'item' section
                        if 'item' in product:
                            item = product['item']
                            print(f"Item section keys: {list(item.keys())}")
                            
                            if 'product_description' in item:
                                desc = item['product_description']
                                print(f"Product description keys: {list(desc.keys())}")
                                print(f"Product description title: {desc.get('title')}")
                        
                        # Check availability
                        available = product.get('available', False)
                        print(f"Available: {available}")
                        
                        # Check fulfillment section
                        if 'fulfillment' in product:
                            fulfillment = product['fulfillment']
                            if 'shipping_options' in fulfillment:
                                shipping = fulfillment['shipping_options']
                                availability_status = shipping.get('availability_status')
                                print(f"Availability status: {availability_status}")
                        
                        # Show a few more relevant fields
                        relevant_fields = ['status', 'quantity', 'is_preorder', 'street_date']
                        for field in relevant_fields:
                            if field in product:
                                print(f"{field}: {product[field]}")
                
        else:
            print(f"Error response: {response.text}")
            
    except Exception as e:
        print(f"Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    detailed_api_test()
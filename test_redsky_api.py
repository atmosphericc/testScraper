#!/usr/bin/env python3
"""
Test script to diagnose Target RedSky API issues
"""

import requests
import json
import time
import random

def test_redsky_api():
    """Test the Target RedSky API with multiple TCINs"""
    
    # API endpoint from your code
    endpoint = 'https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1'
    
    # Example TCINs - you should replace these with your actual TCINs
    test_tcins = ['15059321', '88282464', '87862765']  # Examples from various product types
    
    # API key from your code
    api_key = "ff457966e64d5e877fdbad070f276d18ecec4a01"
    
    # Basic headers
    headers = {
        'accept': 'application/json',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'accept-encoding': 'gzip, deflate, br',
        'accept-language': 'en-US,en;q=0.9',
        'cache-control': 'no-cache',
        'referer': 'https://www.target.com/',
    }
    
    # Test 1: Single TCIN
    print("="*60)
    print("TEST 1: Single TCIN")
    print("="*60)
    
    params = {
        'key': api_key,
        'tcins': test_tcins[0],  # Single TCIN
        'store_id': '1859',
        'pricing_store_id': '1859',
        'has_pricing_context': 'true',
        'has_promotions': 'true',
        'is_bot': 'false',
    }
    
    try:
        response = requests.get(endpoint, params=params, headers=headers, timeout=15)
        print(f"Status Code: {response.status_code}")
        print(f"Response Headers: {dict(response.headers)}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"Response JSON structure: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
            if 'data' in data:
                print(f"Data section keys: {list(data['data'].keys())}")
                if 'product_summaries' in data['data']:
                    print(f"Product summaries count: {len(data['data']['product_summaries'])}")
                    if data['data']['product_summaries']:
                        print(f"First product summary keys: {list(data['data']['product_summaries'][0].keys())}")
        else:
            print(f"Error response: {response.text[:500]}")
            
    except Exception as e:
        print(f"Single TCIN test failed: {e}")
    
    print("\n" + "="*60)
    print("TEST 2: Multiple TCINs (Batch)")
    print("="*60)
    
    # Test 2: Multiple TCINs
    params = {
        'key': api_key,
        'tcins': ','.join(test_tcins),  # Multiple TCINs comma-separated
        'store_id': '1859',
        'pricing_store_id': '1859',
        'has_pricing_context': 'true',
        'has_promotions': 'true',
        'is_bot': 'false',
    }
    
    try:
        response = requests.get(endpoint, params=params, headers=headers, timeout=15)
        print(f"Status Code: {response.status_code}")
        print(f"Response Headers: {dict(response.headers)}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"Response JSON structure: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
            if 'data' in data:
                print(f"Data section keys: {list(data['data'].keys())}")
                if 'product_summaries' in data['data']:
                    print(f"Product summaries count: {len(data['data']['product_summaries'])}")
                    for i, product in enumerate(data['data']['product_summaries']):
                        tcin = product.get('tcin', 'unknown')
                        name = product.get('name', 'unknown')
                        available = product.get('available', False)
                        print(f"  Product {i+1}: TCIN={tcin}, Name={name[:50]}..., Available={available}")
        else:
            print(f"Error response: {response.text[:500]}")
            
    except Exception as e:
        print(f"Batch TCIN test failed: {e}")
    
    print("\n" + "="*60)
    print("TEST 3: Different API Key")
    print("="*60)
    
    # Test 3: Try with different API key
    alternative_keys = [
        "9f36aeafbe60771e321a7cc95a78140772ab3e96",
        "7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b"
    ]
    
    for alt_key in alternative_keys:
        params = {
            'key': alt_key,
            'tcins': test_tcins[0],
            'store_id': '1859',
            'pricing_store_id': '1859',
            'has_pricing_context': 'true',
            'has_promotions': 'true',
            'is_bot': 'false',
        }
        
        try:
            response = requests.get(endpoint, params=params, headers=headers, timeout=15)
            print(f"API Key {alt_key[:8]}...: Status {response.status_code}")
            if response.status_code != 200:
                print(f"  Error: {response.text[:200]}")
        except Exception as e:
            print(f"API Key {alt_key[:8]}... failed: {e}")
    
    print("\n" + "="*60)
    print("TEST 4: Check with your actual product TCINs")
    print("="*60)
    
    # Let's check your actual product config
    try:
        import json
        from pathlib import Path
        
        config_paths = [
            "config/product_config.json",
            "../config/product_config.json"
        ]
        
        for path in config_paths:
            if Path(path).exists():
                with open(path, 'r') as f:
                    config = json.load(f)
                    products = config.get('products', [])
                    if products:
                        print(f"Found config with {len(products)} products")
                        # Test first few enabled products
                        enabled_products = [p for p in products if p.get('enabled', True)][:3]
                        if enabled_products:
                            test_tcins = [p['tcin'] for p in enabled_products]
                            print(f"Testing with your TCINs: {test_tcins}")
                            
                            params = {
                                'key': api_key,
                                'tcins': ','.join(test_tcins),
                                'store_id': '1859',
                                'pricing_store_id': '1859',
                                'has_pricing_context': 'true',
                                'has_promotions': 'true',
                                'is_bot': 'false',
                            }
                            
                            response = requests.get(endpoint, params=params, headers=headers, timeout=15)
                            print(f"Your TCINs - Status Code: {response.status_code}")
                            
                            if response.status_code == 200:
                                data = response.json()
                                if 'data' in data and 'product_summaries' in data['data']:
                                    print(f"Success! Got {len(data['data']['product_summaries'])} products")
                                    for product in data['data']['product_summaries']:
                                        tcin = product.get('tcin')
                                        name = product.get('name', 'Unknown')
                                        available = product.get('available', False)
                                        print(f"  {tcin}: {name[:40]}... Available: {available}")
                                else:
                                    print("Invalid response structure")
                            else:
                                print(f"Error: {response.text[:300]}")
                        break
                        
    except Exception as e:
        print(f"Config test failed: {e}")

if __name__ == "__main__":
    test_redsky_api()
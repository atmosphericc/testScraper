#!/usr/bin/env python3
"""
Simple stock test to check API directly
"""

import json
import time
import random
import requests
from datetime import datetime
from pathlib import Path

def get_config():
    """Load product configuration"""
    possible_paths = [
        "config/product_config.json",
        Path(__file__).parent / "config" / "product_config.json"
    ]
    
    for path in possible_paths:
        if Path(path).exists():
            with open(path, 'r') as f:
                return json.load(f)
    return {"products": []}

def test_api_call():
    """Test the batch API call directly"""
    print("Testing batch API call...")
    
    config = get_config()
    enabled_products = [p for p in config.get('products', []) if p.get('enabled', True)]
    print(f"Found {len(enabled_products)} enabled products")
    
    if not enabled_products:
        print("No enabled products found!")
        return {}
    
    # Extract TCINs
    tcins = [p['tcin'] for p in enabled_products[:5]]  # Test with first 5 only
    print(f"Testing with TCINs: {tcins}")
    
    # API parameters
    api_key = "ff457966e64d5e877fdbad070f276d18ecec4a01"
    
    params = {
        'key': api_key,
        'tcins': ','.join(tcins),
        'store_id': '1859',
        'pricing_store_id': '1859',
        'is_bot': 'false',
        '_': str(int(time.time() * 1000)),
    }
    
    headers = {
        'accept': 'application/json',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'accept-encoding': 'gzip, deflate, br',
        'accept-language': 'en-US,en;q=0.9',
    }
    
    url = 'https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1'
    
    try:
        print(f"Making API call to {url}")
        response = requests.get(url, params=params, headers=headers, timeout=25)
        
        print(f"Response status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"Response keys: {list(data.keys())}")
            
            if 'data' in data and 'product_summaries' in data['data']:
                product_summaries = data['data']['product_summaries']
                print(f"Found {len(product_summaries)} product summaries")
                
                results = {}
                for product_summary in product_summaries:
                    tcin = product_summary.get('tcin')
                    item = product_summary.get('item', {})
                    fulfillment = product_summary.get('fulfillment', {})
                    shipping = fulfillment.get('shipping_options', {})
                    
                    availability_status = shipping.get('availability_status', 'UNKNOWN')
                    
                    # Simple status logic
                    if availability_status == 'IN_STOCK':
                        status = 'IN_STOCK'
                        available = True
                    elif availability_status == 'PRE_ORDER_SELLABLE':
                        status = 'IN_STOCK'  # Preorder available
                        available = True
                    else:
                        status = 'OUT_OF_STOCK'
                        available = False
                    
                    product_desc = item.get('product_description', {})
                    name = product_desc.get('title', 'Unknown Product')
                    
                    results[tcin] = {
                        'tcin': tcin,
                        'name': name,
                        'status': status,
                        'available': available,
                        'availability_status': availability_status,
                        'last_checked': datetime.now().isoformat()
                    }
                    
                    print(f"  {tcin}: {name[:30]}... -> status='{status}', available={available}, raw='{availability_status}'")
                
                return results
            else:
                print("Invalid response structure")
                print(f"Response: {response.text[:500]}")
                return {}
        else:
            print(f"API call failed: {response.status_code}")
            print(f"Response: {response.text}")
            return {}
            
    except Exception as e:
        print(f"Exception during API call: {e}")
        import traceback
        traceback.print_exc()
        return {}

if __name__ == '__main__':
    result = test_api_call()
    print(f"\nTest completed. {len(result)} products processed.")
    
    # Check for any "error" statuses
    error_count = sum(1 for r in result.values() if r.get('status') == 'error' or r.get('status') == 'ERROR')
    if error_count > 0:
        print(f"WARNING: {error_count} products have error status!")
    else:
        print("No error statuses found in API response.")
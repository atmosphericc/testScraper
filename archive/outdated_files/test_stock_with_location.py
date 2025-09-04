#!/usr/bin/env python3
"""
Test Target stock API with various location and fulfillment parameters
Based on 2021 gist parameters to get actual stock availability data
"""

import requests
import json

def test_stock_with_location_params(tcin="89542109"):
    """Test with location and fulfillment parameters from the 2021 gist"""
    
    # Parameters from 2021 gist that worked for stock data
    base_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
    
    params = {
        'key': 'ff457966e64d5e877fdbad070f276d18ecec4a01',
        'tcin': tcin,
        'store_id': '1859',  # Store from gist example
        'store_positions_store_id': '1859',
        'has_store_positions_store_id': 'true',
        'zip': '33809',  # Lakeland FL zip from gist
        'state': 'FL',
        'latitude': '28.0395',  # Lakeland FL coordinates
        'longitude': '-81.9498',
        'pricing_store_id': '1859',
        'has_pricing_store_id': 'true',
        'is_bot': 'false',
        'pricing_context': 'in_store'
    }
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Referer': f'https://www.target.com/p/-/A-{tcin}',
    }
    
    try:
        print("🏪 TESTING STOCK API WITH LOCATION PARAMETERS")
        print(f"TCIN: {tcin}")
        print(f"Store ID: {params['store_id']}")
        print(f"Location: {params['zip']}, {params['state']}")
        print("="*80)
        
        response = requests.get(base_url, params=params, headers=headers, timeout=15)
        
        print(f"Status Code: {response.status_code}")
        print(f"URL: {response.url}")
        print(f"Response Length: {len(response.text)} characters")
        
        if response.status_code == 200:
            data = response.json()
            print("✅ SUCCESS - Searching for stock data...")
            
            # Look specifically for fulfillment data
            fulfillment_data = find_fulfillment_data(data)
            if fulfillment_data:
                print("🎯 FOUND FULFILLMENT DATA:")
                print(json.dumps(fulfillment_data, indent=2))
            else:
                print("❌ No fulfillment data found")
                print("\nFirst 1000 characters of response:")
                print(json.dumps(data, indent=2)[:1000] + "...")
                
        else:
            print(f"❌ FAILED: HTTP {response.status_code}")
            print(f"Response: {response.text}")
            
    except Exception as e:
        print(f"❌ Exception: {e}")

def test_old_fulfillment_endpoint(tcin="89542109"):
    """Try the original 2021 fulfillment endpoint with exact parameters"""
    
    # Exact URL structure from 2021 gist
    base_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_fulfillment_v1"
    
    params = {
        'key': 'ff457966e64d5e877fdbad070f276d18ecec4a01',
        'tcin': tcin,
        'store_id': '1859',
        'store_positions_store_id': '1859', 
        'has_store_positions_store_id': 'true',
        'zip': '33809',
        'state': 'FL',
        'latitude': '28.0395',
        'longitude': '-81.9498',
        'pricing_store_id': '1859',
        'has_pricing_store_id': 'true',
        'is_bot': 'false'
    }
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Referer': f'https://www.target.com/p/-/A-{tcin}',
    }
    
    try:
        print("\n📦 TESTING ORIGINAL 2021 FULFILLMENT ENDPOINT")
        print(f"TCIN: {tcin}")
        print("="*80)
        
        response = requests.get(base_url, params=params, headers=headers, timeout=15)
        
        print(f"Status Code: {response.status_code}")
        print(f"Response Length: {len(response.text)} characters")
        
        if response.status_code == 200:
            data = response.json()
            print("✅ SUCCESS - Original endpoint still works!")
            print(json.dumps(data, indent=2))
        else:
            print(f"❌ FAILED: HTTP {response.status_code}")
            print(f"Response: {response.text}")
            
    except Exception as e:
        print(f"❌ Exception: {e}")

def find_fulfillment_data(data):
    """Recursively search for fulfillment-related data in response"""
    if isinstance(data, dict):
        if 'fulfillment' in data:
            return data['fulfillment']
        for key, value in data.items():
            if key.lower() in ['fulfillment', 'availability', 'stock', 'inventory']:
                return {key: value}
            result = find_fulfillment_data(value)
            if result:
                return result
    elif isinstance(data, list):
        for item in data:
            result = find_fulfillment_data(item)
            if result:
                return result
    return None

if __name__ == '__main__':
    print("🎯 TESTING TARGET STOCK API WITH FULFILLMENT PARAMETERS")
    print("="*80)
    
    # Test current endpoint with location parameters
    test_stock_with_location_params("89542109")
    
    # Test original 2021 endpoint
    test_old_fulfillment_endpoint("89542109")
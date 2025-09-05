#!/usr/bin/env python3
"""Test individual product pricing endpoint"""

import json
import requests
import random
from pathlib import Path

def get_massive_user_agent_rotation():
    """Get random user agent"""
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    ]
    return random.choice(user_agents)

def test_individual_pricing():
    """Test individual product pricing endpoint"""
    
    # Test with first TCIN
    tcin = "94886127"
    
    # Try the pdp_client_v1 endpoint mentioned in GitHub code
    pdp_endpoint = 'https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1'
    
    api_key = "ff457966e64d5e877fdbad070f276d18ecec4a01"
    params = {
        'key': api_key,
        'tcin': tcin,
        'store_id': '1859',
        'pricing_store_id': '1859',
        'has_pricing_store_id': 'true',
        'has_pricing_context': 'true',
        'pricing_context': 'digital',
        'has_promotions': 'true',
        'is_bot': 'false',
        'zip': '33809',
        'state': 'FL',
        'latitude': '28.0395',
        'longitude': '-81.9498'
    }
    
    headers = {
        'accept': 'application/json',
        'user-agent': get_massive_user_agent_rotation(),
        'accept-encoding': 'gzip, deflate, br',
        'accept-language': 'en-US,en;q=0.9',
        'cache-control': 'no-cache',
        'referer': 'https://www.target.com/',
    }
    
    print(f"Testing individual pricing endpoint for TCIN: {tcin}")
    print("Endpoint: " + pdp_endpoint)
    print("=" * 60)
    
    try:
        response = requests.get(pdp_endpoint, params=params, headers=headers, timeout=15)
        
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print("Response structure:")
            print(f"Top-level keys: {list(data.keys())}")
            
            # Look for price data anywhere in response
            def find_price_data(obj, path=""):
                price_fields = []
                if isinstance(obj, dict):
                    for key, value in obj.items():
                        current_path = f"{path}.{key}" if path else key
                        if any(keyword in key.lower() for keyword in ['price', 'cost', 'retail']):
                            price_fields.append((current_path, value))
                        if isinstance(value, (dict, list)):
                            price_fields.extend(find_price_data(value, current_path))
                elif isinstance(obj, list):
                    for i, item in enumerate(obj):
                        price_fields.extend(find_price_data(item, f"{path}[{i}]"))
                return price_fields
            
            price_data = find_price_data(data)
            if price_data:
                print("\nFound price-related fields:")
                for path, value in price_data:
                    print(f"  {path}: {value}")
            else:
                print("\nNo price-related fields found in response")
                
            # Save full response for inspection
            with open('pdp_response.json', 'w') as f:
                json.dump(data, f, indent=2)
            print("\nFull response saved to pdp_response.json")
                
        else:
            print(f"Request failed with status {response.status_code}")
            print(f"Response: {response.text[:500]}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_individual_pricing()
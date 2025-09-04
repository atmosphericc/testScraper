#!/usr/bin/env python3
"""
Test the current Target API endpoint: pdp_client_v1
Based on your observation that this is what shows in network tab now
"""

import requests
import json

def test_current_fulfillment_api(tcin="89542109"):
    """Test the current pdp_client_v1 endpoint"""
    
    # Current API endpoint based on your network observation
    url = f"https://redsky.target.com/redsky_aggregations/v1/redsky/pdp_client_v1?key=ff457966e64d5e877fdbad070f276d18ecec4a01&tcin={tcin}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Referer': f'https://www.target.com/p/-/A-{tcin}',
        'Upgrade-Insecure-Requests': '1'
    }
    
    try:
        print(f"Testing current API: pdp_client_v1")
        print(f"TCIN: {tcin}")
        print(f"URL: {url}")
        print("="*80)
        
        response = requests.get(url, headers=headers, timeout=10)
        
        print(f"Status Code: {response.status_code}")
        print(f"Response Length: {len(response.text)} characters")
        
        if response.status_code == 200:
            data = response.json()
            print("✅ SUCCESS - Raw response:")
            print(json.dumps(data, indent=2))
            
            # Look for fulfillment/stock data
            print("\n" + "="*60)
            print("LOOKING FOR STOCK/FULFILLMENT DATA:")
            print("="*60)
            
            def search_for_stock_data(obj, path=""):
                """Recursively search for stock-related keys"""
                if isinstance(obj, dict):
                    for key, value in obj.items():
                        current_path = f"{path}.{key}" if path else key
                        if any(term in key.lower() for term in ['stock', 'fulfillment', 'available', 'inventory', 'quantity', 'shipping']):
                            print(f"📦 FOUND: {current_path} = {value}")
                        search_for_stock_data(value, current_path)
                elif isinstance(obj, list):
                    for i, item in enumerate(obj):
                        search_for_stock_data(item, f"{path}[{i}]")
            
            search_for_stock_data(data)
            
        else:
            print(f"❌ FAILED: HTTP {response.status_code}")
            print(f"Response: {response.text[:500]}")
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Request failed: {e}")
    except json.JSONDecodeError as e:
        print(f"❌ JSON decode failed: {e}")
        print(f"Raw response: {response.text[:1000]}")

if __name__ == '__main__':
    test_current_fulfillment_api("89542109")
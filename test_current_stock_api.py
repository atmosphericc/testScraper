#!/usr/bin/env python3
"""
Test the current Target stock API endpoints found from GitHub repo
Testing both online and store-specific stock availability
"""

import requests
import json

def test_online_stock_api(tcin="89542109"):
    """Test the current online stock availability endpoint"""
    
    # Current online stock endpoint
    url = f"https://redsky.target.com/redsky_aggregations/v1/web_platform/product_fulfillment_v1?key=ff457966e64d5e877fdbad070f276d18ecec4a01&tcin={tcin}&pricing_context=digital"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Referer': f'https://www.target.com/p/-/A-{tcin}',
    }
    
    try:
        print(f"🌐 TESTING ONLINE STOCK API")
        print(f"TCIN: {tcin}")
        print(f"URL: {url}")
        print("="*80)
        
        response = requests.get(url, headers=headers, timeout=15)
        
        print(f"Status Code: {response.status_code}")
        print(f"Response Length: {len(response.text)} characters")
        
        if response.status_code == 200:
            data = response.json()
            print("✅ SUCCESS - Raw response:")
            print(json.dumps(data, indent=2))
            return data
        else:
            print(f"❌ FAILED: HTTP {response.status_code}")
            print(f"Response: {response.text}")
            return None
            
    except Exception as e:
        print(f"❌ Exception: {e}")
        return None

def test_store_stock_api(tcin="89542109", store_id="1859"):
    """Test the store-specific stock availability endpoint"""
    
    # Store-specific stock endpoint  
    url = f"https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1?key=ff457966e64d5e877fdbad070f276d18ecec4a01&tcin={tcin}&pricing_context=in_store&store_id={store_id}&pricing_store_id={store_id}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Referer': f'https://www.target.com/p/-/A-{tcin}',
    }
    
    try:
        print(f"\n🏪 TESTING STORE-SPECIFIC STOCK API")
        print(f"TCIN: {tcin}")
        print(f"Store ID: {store_id}")
        print(f"URL: {url}")
        print("="*80)
        
        response = requests.get(url, headers=headers, timeout=15)
        
        print(f"Status Code: {response.status_code}")
        print(f"Response Length: {len(response.text)} characters")
        
        if response.status_code == 200:
            data = response.json()
            print("✅ SUCCESS - Raw response:")
            print(json.dumps(data, indent=2))
            return data
        else:
            print(f"❌ FAILED: HTTP {response.status_code}")
            print(f"Response: {response.text}")
            return None
            
    except Exception as e:
        print(f"❌ Exception: {e}")
        return None

def extract_stock_info(data, api_type):
    """Extract stock availability from API response"""
    if not data:
        return
        
    print(f"\n📦 STOCK INFORMATION EXTRACTION ({api_type}):")
    print("="*60)
    
    def search_stock_keys(obj, path=""):
        """Recursively find stock-related information"""
        stock_terms = ['stock', 'available', 'inventory', 'quantity', 'fulfillment', 'shipping', 'pickup', 'out_of_stock']
        
        if isinstance(obj, dict):
            for key, value in obj.items():
                current_path = f"{path}.{key}" if path else key
                if any(term in key.lower() for term in stock_terms):
                    print(f"🎯 {current_path}: {value}")
                search_stock_keys(value, current_path)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                search_stock_keys(item, f"{path}[{i}]")
    
    search_stock_keys(data)

if __name__ == '__main__':
    print("🎯 TESTING CURRENT TARGET STOCK API ENDPOINTS")
    print("="*80)
    
    # Test online stock availability
    online_data = test_online_stock_api("89542109")
    extract_stock_info(online_data, "ONLINE")
    
    # Test store-specific stock availability
    store_data = test_store_stock_api("89542109", "1859")  # Using store from gist example
    extract_stock_info(store_data, "STORE")
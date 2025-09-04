#!/usr/bin/env python3
"""
Test various Target API endpoint variations to find current stock availability endpoint
"""

import requests
import json

def test_endpoint_variations(tcin="89542109"):
    """Test different endpoint variations that might contain stock data"""
    
    base_urls = [
        # Current variations
        "https://redsky.target.com/redsky_aggregations/v1/web/pdp_fulfillment_v2",
        "https://redsky.target.com/redsky_aggregations/v2/web/pdp_fulfillment_v1", 
        "https://redsky.target.com/redsky_aggregations/v1/web/product_fulfillment_v1",
        "https://redsky.target.com/redsky_aggregations/v1/web/fulfillment_v1",
        "https://redsky.target.com/redsky_aggregations/v1/web/inventory_v1",
        "https://redsky.target.com/redsky_aggregations/v1/web/availability_v1",
        "https://redsky.target.com/redsky_aggregations/v1/web/stock_v1",
        
        # Mobile variations
        "https://redsky.target.com/redsky_aggregations/v1/mobile/pdp_fulfillment_v1",
        "https://redsky.target.com/redsky_aggregations/v1/mobile/product_fulfillment_v1",
        
        # Platform variations  
        "https://redsky.target.com/redsky_aggregations/v1/app/pdp_fulfillment_v1",
        "https://redsky.target.com/redsky_aggregations/v1/native/pdp_fulfillment_v1",
    ]
    
    # Parameters that worked in 2021
    params = {
        'key': 'ff457966e64d5e877fdbad070f276d18ecec4a01',
        'tcin': tcin,
        'store_id': '1859',
        'pricing_store_id': '1859',
        'zip': '33809',
        'state': 'FL',
        'latitude': '28.0395',
        'longitude': '-81.9498',
        'is_bot': 'false'
    }
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': f'https://www.target.com/p/-/A-{tcin}',
    }
    
    print("🔍 TESTING ENDPOINT VARIATIONS FOR STOCK DATA")
    print(f"TCIN: {tcin}")
    print("="*80)
    
    successful_endpoints = []
    
    for url in base_urls:
        try:
            print(f"\n🧪 Testing: {url.split('/')[-1]}")
            response = requests.get(url, params=params, headers=headers, timeout=10)
            
            status = response.status_code
            length = len(response.text)
            
            if status == 200:
                print(f"✅ SUCCESS: {status} ({length} chars)")
                data = response.json()
                
                # Look for stock-related data
                stock_data = find_stock_indicators(data)
                if stock_data:
                    print(f"🎯 STOCK DATA FOUND!")
                    successful_endpoints.append((url, data))
                    print(json.dumps(stock_data, indent=2)[:500] + "...")
                else:
                    print("📦 Response received but no stock data found")
                    
            elif status == 410:
                print(f"❌ GONE: {status} (endpoint deprecated)")
            elif status == 400:
                print(f"❌ BAD REQUEST: {status} (invalid parameters)")
            elif status == 404:
                print(f"❌ NOT FOUND: {status}")
            else:
                print(f"❌ ERROR: {status}")
                
        except requests.exceptions.Timeout:
            print("⏰ TIMEOUT")
        except requests.exceptions.RequestException as e:
            print(f"❌ REQUEST ERROR: {e}")
        except json.JSONDecodeError:
            print("❌ INVALID JSON")
        except Exception as e:
            print(f"❌ EXCEPTION: {e}")
    
    print(f"\n🏆 SUMMARY: Found {len(successful_endpoints)} working endpoints")
    return successful_endpoints

def find_stock_indicators(data):
    """Look for stock-related indicators in the API response"""
    stock_keywords = [
        'availability_status', 'available_to_promise_quantity', 'out_of_stock',
        'in_stock', 'inventory', 'fulfillment', 'shipping_options', 'store_options',
        'location_available_to_promise_quantity', 'is_out_of_stock_in_all_store_locations'
    ]
    
    def search_recursive(obj, path=""):
        results = {}
        if isinstance(obj, dict):
            for key, value in obj.items():
                current_path = f"{path}.{key}" if path else key
                
                # Check if key contains stock keywords
                if any(keyword in key.lower() for keyword in stock_keywords):
                    results[current_path] = value
                
                # Recursively search nested objects
                nested_results = search_recursive(value, current_path)
                results.update(nested_results)
                
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                nested_results = search_recursive(item, f"{path}[{i}]")
                results.update(nested_results)
        
        return results
    
    return search_recursive(data)

if __name__ == '__main__':
    successful = test_endpoint_variations("89542109")
    
    if successful:
        print("\n🎯 DETAILED ANALYSIS OF SUCCESSFUL ENDPOINTS:")
        print("="*80)
        for url, data in successful:
            print(f"\n📡 ENDPOINT: {url}")
            stock_data = find_stock_indicators(data) 
            if stock_data:
                print("Stock-related data found:")
                for key, value in stock_data.items():
                    print(f"  {key}: {value}")
            print("-" * 60)
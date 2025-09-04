#!/usr/bin/env python3
"""
Test the exact endpoints and parameter formats from the GitHub documentation
"""

import requests
import json
import time

def test_documented_formats():
    """Test endpoints using the exact formats from documentation"""
    
    api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
    store_id = "865"
    
    headers = {
        'accept': 'application/json',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'referer': 'https://www.target.com/',
    }
    
    test_tcins = [
        ('94681776', 'GREYED OUT'),
        ('94734932', 'CLICKABLE')
    ]
    
    print("🔍 TESTING DOCUMENTED API FORMATS")
    print("Using exact formats from GitHub documentation")
    print("=" * 60)
    
    # Test format 1: Multiple TCINs with fulfillment
    print(f"\n📡 TESTING: Multiple TCINs with fulfillment context")
    print("-" * 50)
    
    url1 = "https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1"
    
    for tcin, description in test_tcins:
        print(f"\n  📦 {tcin} ({description}):")
        
        # Try different parameter combinations for fulfillment endpoint
        param_variations = [
            {
                'key': api_key,
                'tcins': tcin,  # Single TCIN
                'store_id': store_id,
                'pricing_store_id': store_id,
                'has_pricing_context': 'true',
                'has_promotions': 'true',
                'is_bot': 'false',
            },
            {
                'key': api_key,
                'tcin': tcin,  # Try 'tcin' instead of 'tcins'
                'store_id': store_id,
                'fulfillment_context': 'true',
                'inventory_context': 'true',
            },
            {
                'key': api_key,
                'tcins': f'["{tcin}"]',  # JSON array format
                'store_id': store_id,
                'channel': 'WEB',
                'visitor_id': f"{int(time.time()*1000)}0000000000",
            }
        ]
        
        for i, params in enumerate(param_variations, 1):
            try:
                response = requests.get(url1, params=params, headers=headers, timeout=10)
                print(f"    Variation {i}: {response.status_code}")
                
                if response.status_code == 200:
                    data = response.json()
                    print(f"    ✅ SUCCESS! Keys: {list(data.keys())}")
                    
                    # Save successful response
                    filename = f"success_fulfillment_{tcin}_var{i}.json"
                    with open(filename, 'w') as f:
                        json.dump(data, f, indent=2)
                    print(f"    💾 Saved to: {filename}")
                    
                    # Look for availability fields
                    availability_fields = find_availability_in_response(data)
                    if availability_fields:
                        print(f"    🎯 Found availability fields:")
                        for field, value in availability_fields[:5]:
                            print(f"      {field}: {value}")
                    
                elif response.status_code in [400, 404]:
                    error_text = response.text[:150]
                    if "tcins" in error_text.lower():
                        print(f"    ❌ TCINS parameter issue: {error_text}")
                    else:
                        print(f"    ❌ Error: {error_text}")
                else:
                    print(f"    ❌ Status {response.status_code}: {response.text[:100]}")
                    
            except Exception as e:
                print(f"    ❌ Exception: {str(e)[:100]}")
            
            time.sleep(0.3)

def find_availability_in_response(data):
    """Look for availability fields in API response"""
    found = []
    
    def search_recursive(obj, path=""):
        nonlocal found
        
        if isinstance(obj, dict):
            for key, value in obj.items():
                current_path = f"{path}.{key}" if path else key
                
                # Look for availability terms
                availability_terms = [
                    'availability', 'available', 'stock', 'inventory', 
                    'fulfillment', 'promise', 'reason', 'out_of_stock'
                ]
                
                if any(term in key.lower() for term in availability_terms):
                    found.append((current_path, value))
                
                # Check values for status indicators
                elif isinstance(value, str):
                    status_terms = ['out_of_stock', 'in_stock', 'unavailable', 'available']
                    if any(term in value.lower() for term in status_terms):
                        found.append((f"{current_path}(value)", value))
                
                # Recurse
                if isinstance(value, (dict, list)):
                    search_recursive(value, current_path)
        
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                current_path = f"{path}[{i}]" if path else f"[{i}]"
                search_recursive(item, current_path)
    
    search_recursive(data)
    return found

def test_alternative_approach():
    """Test using location-based availability checking"""
    
    print(f"\n🌍 TESTING: Location-based availability checking")
    print("-" * 50)
    
    api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
    
    headers = {
        'accept': 'application/json',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'referer': 'https://www.target.com/',
    }
    
    # Try the stores endpoint to get availability by location
    stores_url = "https://redsky.target.com/redsky_aggregations/v1/web/store_fulfillment_availability_v2"
    
    test_tcins = ['94681776', '94734932']
    
    for tcin in test_tcins:
        print(f"\n  📦 Testing {tcin}:")
        
        params = {
            'key': api_key,
            'tcin': tcin,
            'zip': '10001',  # NYC
            'radius': '50',
            'fulfillment_types': 'store_pickup',
        }
        
        try:
            response = requests.get(stores_url, params=params, headers=headers, timeout=10)
            print(f"    Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"    ✅ Success! Response structure: {list(data.keys())}")
                
                filename = f"store_availability_{tcin}.json"
                with open(filename, 'w') as f:
                    json.dump(data, f, indent=2)
                print(f"    💾 Saved to: {filename}")
                
                # Look for availability info
                availability_fields = find_availability_in_response(data)
                if availability_fields:
                    print(f"    🎯 Found availability info:")
                    for field, value in availability_fields[:3]:
                        print(f"      {field}: {value}")
            
            else:
                print(f"    ❌ Failed: {response.text[:100]}")
                
        except Exception as e:
            print(f"    ❌ Error: {e}")
        
        time.sleep(0.5)

if __name__ == "__main__":
    test_documented_formats()
    test_alternative_approach()
    
    print(f"\n🎯 FINAL ANALYSIS:")
    print("If no endpoints return availability status fields, then:")
    print("1. The current API version doesn't expose preorder inventory status")
    print("2. We may need to use website scraping as fallback")
    print("3. Or accept that preorder availability can't be determined via API")
    print("4. The dashboard could use purchase_limit > 0 with error handling")
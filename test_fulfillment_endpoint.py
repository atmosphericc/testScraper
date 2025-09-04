#!/usr/bin/env python3
"""
Test the fulfillment-specific endpoint that should contain availability status fields
Based on API research, try product_summary_with_fulfillment_v1 endpoint
"""

import requests
import json
import time

def test_fulfillment_endpoint():
    """Test the fulfillment-specific endpoint for availability status fields"""
    
    api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
    store_id = "865"
    
    headers = {
        'accept': 'application/json',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'referer': 'https://www.target.com/',
    }
    
    # Test different fulfillment-focused endpoints
    endpoints = [
        "https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1",
        "https://redsky.target.com/v2/pdp/tcin",  # Alternative format
        "https://redsky.target.com/redsky_aggregations/v1/web/fulfillment_v1",
        "https://redsky.target.com/redsky_aggregations/v1/web/inventory_v1"
    ]
    
    test_tcins = [
        ('94681776', 'GREYED OUT (expected unavailable)'),
        ('94734932', 'CLICKABLE (expected available)')
    ]
    
    print("🔍 TESTING FULFILLMENT-SPECIFIC ENDPOINTS")
    print("Looking for availability_status and inventory fields")
    print("=" * 70)
    
    for endpoint in endpoints:
        endpoint_name = endpoint.split('/')[-1]
        print(f"\n📡 TESTING ENDPOINT: {endpoint_name}")
        print("-" * 50)
        
        for tcin, description in test_tcins:
            print(f"\n  📦 {tcin} ({description}):")
            
            # Try different parameter formats
            param_sets = [
                {
                    'key': api_key,
                    'tcin': tcin,
                    'store_id': store_id,
                    'pricing_store_id': store_id,
                },
                {
                    'key': api_key,
                    'tcin': tcin,
                    'store_id': store_id,
                },
                # Try with just TCIN for v2 endpoint
                {}  # Will be replaced with just TCIN in URL
            ]
            
            for i, params in enumerate(param_sets):
                if i == 2 and 'v2/pdp/tcin' in endpoint:
                    # Special handling for v2 endpoint
                    test_url = f"{endpoint}/{tcin}?key={api_key}&store_id={store_id}"
                    params = None
                else:
                    test_url = endpoint
                
                try:
                    if params:
                        response = requests.get(test_url, params=params, headers=headers, timeout=10)
                    else:
                        response = requests.get(test_url, headers=headers, timeout=10)
                    
                    print(f"    Param set {i+1}: {response.status_code}")
                    
                    if response.status_code == 200:
                        data = response.json()
                        
                        # Search for availability fields
                        def find_availability_fields(obj, path=""):
                            found = []
                            if isinstance(obj, dict):
                                for key, value in obj.items():
                                    current_path = f"{path}.{key}" if path else key
                                    
                                    # Look for availability-related fields
                                    availability_terms = [
                                        'availability_status', 'available_to_promise', 'reason_code',
                                        'out_of_stock', 'in_stock', 'inventory', 'fulfillment'
                                    ]
                                    
                                    if any(term in key.lower() for term in availability_terms):
                                        found.append((current_path, value))
                                    
                                    # Check string values too
                                    elif isinstance(value, str) and any(term in value.upper() for term in ['OUT_OF_STOCK', 'IN_STOCK', 'UNAVAILABLE']):
                                        found.append((f"{current_path}(value)", value))
                                    
                                    # Recurse
                                    if isinstance(value, (dict, list)):
                                        found.extend(find_availability_fields(value, current_path))
                            
                            elif isinstance(obj, list):
                                for idx, item in enumerate(obj):
                                    current_path = f"{path}[{idx}]" if path else f"[{idx}]"
                                    found.extend(find_availability_fields(item, current_path))
                            
                            return found
                        
                        availability_fields = find_availability_fields(data)
                        
                        if availability_fields:
                            print(f"    ✅ Found {len(availability_fields)} availability fields:")
                            for field_path, value in availability_fields[:10]:  # Show first 10
                                print(f"      {field_path}: {value}")
                            
                            # Save successful response for analysis
                            filename = f"fulfillment_response_{tcin}_{endpoint_name}_params{i+1}.json"
                            with open(filename, 'w') as f:
                                json.dump(data, f, indent=2)
                            print(f"    💾 Saved to: {filename}")
                            
                        else:
                            print(f"    ❌ No availability fields found")
                    
                    elif response.status_code == 400:
                        error_text = response.text[:200]
                        if "No query found" in error_text:
                            print(f"    ❌ Endpoint doesn't exist")
                        else:
                            print(f"    ❌ Bad request: {error_text}")
                    
                    else:
                        print(f"    ❌ Failed: {response.status_code}")
                
                except Exception as e:
                    print(f"    ❌ Error: {str(e)[:100]}")
                
                time.sleep(0.3)  # Be nice to API

def test_store_availability_endpoint():
    """Test store-specific availability endpoint"""
    
    print(f"\n🏪 TESTING STORE AVAILABILITY ENDPOINT")
    print("=" * 50)
    
    api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
    store_id = "865"
    
    headers = {
        'accept': 'application/json',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'referer': 'https://www.target.com/',
    }
    
    # Try store availability endpoint
    base_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_fulfillment_v1"
    
    test_tcins = [
        ('94681776', 'GREYED OUT'),
        ('94734932', 'CLICKABLE')
    ]
    
    for tcin, description in test_tcins:
        print(f"\n📦 {tcin} ({description}):")
        
        params = {
            'key': api_key,
            'tcin': tcin,
            'store_id': store_id,
            'store_positions_enabled': 'true',
            'fulfillment_test_and_learn': 'false',
            'zip': '10001',  # NYC zip for testing
            'state': 'NY',
            'latitude': '40.7128',
            'longitude': '-74.0060',
        }
        
        try:
            response = requests.get(base_url, params=params, headers=headers, timeout=10)
            print(f"  Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"  ✅ Success! Response keys: {list(data.keys())}")
                
                # Save for analysis
                filename = f"store_availability_{tcin}_{description.lower().replace(' ', '_')}.json"
                with open(filename, 'w') as f:
                    json.dump(data, f, indent=2)
                print(f"  💾 Saved to: {filename}")
                
            else:
                print(f"  ❌ Failed: {response.text[:200]}")
                
        except Exception as e:
            print(f"  ❌ Error: {e}")
        
        time.sleep(0.5)

if __name__ == "__main__":
    test_fulfillment_endpoint()
    test_store_availability_endpoint()
    
    print(f"\n🎯 SUMMARY:")
    print("If any endpoint returns availability_status fields, we can use those")
    print("to create an accurate preorder inventory parser for the dashboard.")
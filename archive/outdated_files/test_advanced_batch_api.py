#!/usr/bin/env python3
"""
Advanced batch API testing with more variations including POST requests
Based on user feedback that batch calls should work with comma-separated SKUs
"""

import requests
import json
from urllib.parse import urlencode

def test_advanced_batch_methods():
    """Test more advanced batch methods including POST and different formats"""
    
    tcins = ["89542109", "94724987", "94681785", "94681770", "94336414"]
    
    # Common parameters
    base_params = {
        'key': 'ff457966e64d5e877fdbad070f276d18ecec4a01',
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
        'Referer': 'https://www.target.com/',
    }
    
    print("🚀 ADVANCED BATCH API TESTING")
    print("="*80)
    print(f"Testing TCINs: {', '.join(tcins)}")
    print("="*80)
    
    # Test Case 1: GET with tcins parameter (comma-separated)
    test_get_batch_variations(tcins, base_params, headers)
    
    # Test Case 2: POST requests with JSON payloads
    test_post_batch_variations(tcins, base_params, headers)
    
    # Test Case 3: Different endpoint variations
    test_endpoint_variations(tcins, base_params, headers)
    
    # Test Case 4: Query string manipulation
    test_query_string_variations(tcins, base_params, headers)

def test_get_batch_variations(tcins, base_params, headers):
    """Test GET request variations for batch calls"""
    
    print("\n📡 TESTING GET REQUEST BATCH VARIATIONS")
    print("-" * 60)
    
    base_url = "https://redsky.target.com/redsky_aggregations/v1/web/product_fulfillment_v1"
    
    variations = [
        # Variation 1: tcins parameter
        {
            "name": "tcins (comma-separated)",
            "params": {**base_params, 'tcins': ','.join(tcins)}
        },
        
        # Variation 2: tcin_list parameter  
        {
            "name": "tcin_list (comma-separated)",
            "params": {**base_params, 'tcin_list': ','.join(tcins)}
        },
        
        # Variation 3: product_ids parameter
        {
            "name": "product_ids (comma-separated)", 
            "params": {**base_params, 'product_ids': ','.join(tcins)}
        },
        
        # Variation 4: Multiple tcin parameters
        {
            "name": "Multiple tcin parameters",
            "params": base_params,
            "custom_tcins": tcins  # Handle separately
        },
        
        # Variation 5: Space-separated
        {
            "name": "tcins (space-separated)",
            "params": {**base_params, 'tcins': ' '.join(tcins)}
        },
        
        # Variation 6: Semicolon-separated
        {
            "name": "tcins (semicolon-separated)",
            "params": {**base_params, 'tcins': ';'.join(tcins)}
        },
        
        # Variation 7: URL encoded comma
        {
            "name": "tcins (URL encoded comma)",
            "params": {**base_params, 'tcins': '%2C'.join(tcins)}
        }
    ]
    
    for variation in variations:
        test_single_variation("GET", base_url, variation, headers)

def test_post_batch_variations(tcins, base_params, headers):
    """Test POST request variations for batch calls"""
    
    print("\n📤 TESTING POST REQUEST BATCH VARIATIONS")
    print("-" * 60)
    
    base_url = "https://redsky.target.com/redsky_aggregations/v1/web/product_fulfillment_v1"
    
    # POST with JSON payload
    json_payload = {
        **base_params,
        'tcins': tcins  # Array format
    }
    
    # POST with form data
    form_data = {
        **base_params,
        'tcins': ','.join(tcins)
    }
    
    variations = [
        {
            "name": "POST JSON (tcins array)",
            "method": "POST",
            "headers": {**headers, 'Content-Type': 'application/json'},
            "data": json.dumps(json_payload)
        },
        
        {
            "name": "POST Form (tcins comma-separated)",  
            "method": "POST",
            "headers": {**headers, 'Content-Type': 'application/x-www-form-urlencoded'},
            "data": urlencode(form_data)
        },
        
        {
            "name": "POST JSON (tcin_list)",
            "method": "POST", 
            "headers": {**headers, 'Content-Type': 'application/json'},
            "data": json.dumps({**base_params, 'tcin_list': tcins})
        }
    ]
    
    for variation in variations:
        test_post_variation(base_url, variation)

def test_endpoint_variations(tcins, base_params, headers):
    """Test different endpoint paths that might support batch"""
    
    print("\n🔍 TESTING DIFFERENT ENDPOINT VARIATIONS")  
    print("-" * 60)
    
    endpoints = [
        "https://redsky.target.com/redsky_aggregations/v1/web/products_fulfillment_v1",  # plural
        "https://redsky.target.com/redsky_aggregations/v1/web/bulk_product_fulfillment_v1",
        "https://redsky.target.com/redsky_aggregations/v1/web/multi_fulfillment_v1",
        "https://redsky.target.com/redsky_aggregations/v1/web/fulfillment_batch_v1",
        "https://redsky.target.com/redsky_aggregations/v1/web/product_batch_fulfillment_v1",
        "https://redsky.target.com/redsky_aggregations/v2/web/product_fulfillment_v1",  # v2
        "https://redsky.target.com/redsky_aggregations/v1/mobile/product_fulfillment_v1",  # mobile
    ]
    
    params_with_tcins = {**base_params, 'tcins': ','.join(tcins)}
    
    for endpoint in endpoints:
        endpoint_name = endpoint.split('/')[-1]
        
        try:
            response = requests.get(endpoint, params=params_with_tcins, headers=headers, timeout=10)
            
            print(f"   🧪 {endpoint_name}:")
            print(f"      Status: {response.status_code} ({len(response.text)} chars)")
            
            if response.status_code == 200:
                print("      ✅ SUCCESS!")
                data = response.json()
                product_count = count_products_in_response(data)
                
                if product_count > 1:
                    print(f"      🎯 BATCH SUCCESS! Found {product_count} products")
                    show_batch_results(data, tcins)
                    return True
                elif product_count == 1:
                    print(f"      📦 Single product returned")
                else:
                    print(f"      ❓ No products found in response")
                    
            elif response.status_code in [400, 404, 410]:
                print(f"      ❌ {response.status_code}")
            else:
                print(f"      ❓ Unexpected: {response.status_code}")
                
        except Exception as e:
            print(f"      ❌ Error: {e}")
    
    return False

def test_query_string_variations(tcins, base_params, headers):
    """Test manual query string construction variations"""
    
    print("\n🔧 TESTING QUERY STRING VARIATIONS")
    print("-" * 60)
    
    base_url = "https://redsky.target.com/redsky_aggregations/v1/web/product_fulfillment_v1"
    
    # Manual query string constructions
    query_variations = [
        # Multiple tcin parameters in URL
        f"?key={base_params['key']}&" + "&".join([f"tcin={tcin}" for tcin in tcins]) + f"&store_id={base_params['store_id']}&pricing_store_id={base_params['pricing_store_id']}&zip={base_params['zip']}&state={base_params['state']}&latitude={base_params['latitude']}&longitude={base_params['longitude']}&is_bot={base_params['is_bot']}",
        
        # tcins[] array notation
        f"?key={base_params['key']}&" + "&".join([f"tcins[]={tcin}" for tcin in tcins]) + f"&store_id={base_params['store_id']}&pricing_store_id={base_params['pricing_store_id']}&zip={base_params['zip']}&state={base_params['state']}&latitude={base_params['latitude']}&longitude={base_params['longitude']}&is_bot={base_params['is_bot']}",
        
        # tcin[0], tcin[1], etc.
        f"?key={base_params['key']}&" + "&".join([f"tcin[{i}]={tcin}" for i, tcin in enumerate(tcins)]) + f"&store_id={base_params['store_id']}&pricing_store_id={base_params['pricing_store_id']}&zip={base_params['zip']}&state={base_params['state']}&latitude={base_params['latitude']}&longitude={base_params['longitude']}&is_bot={base_params['is_bot']}"
    ]
    
    variation_names = [
        "Multiple tcin parameters",
        "tcins[] array notation", 
        "tcin[index] notation"
    ]
    
    for i, query_string in enumerate(query_variations):
        full_url = base_url + query_string
        
        try:
            print(f"   🧪 {variation_names[i]}:")
            response = requests.get(full_url, headers=headers, timeout=10)
            
            print(f"      Status: {response.status_code} ({len(response.text)} chars)")
            
            if response.status_code == 200:
                print("      ✅ SUCCESS!")
                data = response.json()
                product_count = count_products_in_response(data)
                
                if product_count > 1:
                    print(f"      🎯 BATCH SUCCESS! Found {product_count} products")
                    show_batch_results(data, tcins)
                    return True
                else:
                    print(f"      📦 Single product: {product_count}")
            else:
                print(f"      ❌ {response.status_code}")
                
        except Exception as e:
            print(f"      ❌ Error: {e}")

def test_single_variation(method, url, variation, headers):
    """Test a single GET variation"""
    
    try:
        print(f"   🧪 {variation['name']}:")
        
        if 'custom_tcins' in variation:
            # Handle multiple tcin parameters specially
            params = variation['params'].copy()
            tcins = variation['custom_tcins']
            
            # Create query string with multiple tcin params
            query_parts = []
            for key, value in params.items():
                query_parts.append(f"{key}={value}")
            for tcin in tcins:
                query_parts.append(f"tcin={tcin}")
            
            full_url = url + "?" + "&".join(query_parts)
            response = requests.get(full_url, headers=headers, timeout=10)
        else:
            response = requests.get(url, params=variation['params'], headers=headers, timeout=10)
        
        print(f"      Status: {response.status_code} ({len(response.text)} chars)")
        
        if response.status_code == 200:
            print("      ✅ SUCCESS!")
            data = response.json()
            product_count = count_products_in_response(data)
            
            if product_count > 1:
                print(f"      🎯 BATCH SUCCESS! Found {product_count} products")
                show_batch_results(data, ['89542109', '94724987', '94681785', '94681770', '94336414'])
                return True
            else:
                print(f"      📦 Products found: {product_count}")
        else:
            print(f"      ❌ {response.status_code}")
            
    except Exception as e:
        print(f"      ❌ Error: {e}")
    
    return False

def test_post_variation(url, variation):
    """Test a POST variation"""
    
    try:
        print(f"   🧪 {variation['name']}:")
        
        if variation['method'] == 'POST':
            response = requests.post(
                url, 
                headers=variation['headers'],
                data=variation['data'],
                timeout=10
            )
        
        print(f"      Status: {response.status_code} ({len(response.text)} chars)")
        
        if response.status_code == 200:
            print("      ✅ SUCCESS!")
            data = response.json()
            product_count = count_products_in_response(data)
            
            if product_count > 1:
                print(f"      🎯 BATCH SUCCESS! Found {product_count} products")
                show_batch_results(data, ['89542109', '94724987', '94681785', '94681770', '94336414'])
                return True
            else:
                print(f"      📦 Products found: {product_count}")
        else:
            print(f"      ❌ {response.status_code}")
            
    except Exception as e:
        print(f"      ❌ Error: {e}")
    
    return False

def count_products_in_response(data):
    """Count products in API response - handle various response formats"""
    
    try:
        if isinstance(data, dict):
            # Standard single product format
            if 'data' in data and 'product' in data['data']:
                return 1
            
            # Batch format with products array
            if 'data' in data and 'products' in data['data']:
                return len(data['data']['products'])
            
            # Direct products array
            if 'products' in data:
                return len(data['products'])
            
            # Multiple data entries
            if 'data' in data and isinstance(data['data'], list):
                return len(data['data'])
                
        return 0
    except:
        return 0

def show_batch_results(data, tcins):
    """Show results from successful batch call"""
    
    print("      📊 Batch Stock Results:")
    
    try:
        # Try different response formats
        products = None
        
        if 'data' in data and 'products' in data['data']:
            products = data['data']['products']
        elif 'products' in data:
            products = data['products']
        elif 'data' in data and isinstance(data['data'], list):
            products = data['data']
        
        if products:
            for product in products[:5]:  # Show first 5
                tcin = product.get('tcin', 'Unknown')
                fulfillment = product.get('fulfillment', {})
                shipping = fulfillment.get('shipping_options', {})
                
                qty = int(shipping.get('available_to_promise_quantity', 0))
                status = shipping.get('availability_status', 'UNKNOWN')
                
                status_icon = "✅" if status == 'IN_STOCK' else "❌"
                print(f"         {status_icon} {tcin}: {qty} units ({status})")
        else:
            print("         ❓ Could not parse product data")
            
    except Exception as e:
        print(f"         ❌ Parse error: {e}")

if __name__ == '__main__':
    test_advanced_batch_methods()
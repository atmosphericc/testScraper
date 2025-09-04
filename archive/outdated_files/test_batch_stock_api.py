#!/usr/bin/env python3
"""
Test batch/bulk stock checking for multiple TCINs in single API call
Based on 2021 gist mention of 'tcins' field for multiple products
"""

import requests
import json

def test_batch_tcins_single_call():
    """Test various ways to get multiple TCINs in one API call"""
    
    tcins = ["89542109", "94724987", "94681785", "94681770", "94336414"]
    
    # Test different parameter formats for batch requests
    test_cases = [
        # Case 1: tcins parameter with comma-separated values
        {
            "name": "Comma-separated tcins",
            "params": {
                'key': 'ff457966e64d5e877fdbad070f276d18ecec4a01',
                'tcins': ','.join(tcins),  # "89542109,94724987,94681785,94681770,94336414"
                'store_id': '1859',
                'pricing_store_id': '1859',
                'zip': '33809',
                'state': 'FL',
                'latitude': '28.0395',
                'longitude': '-81.9498',
                'is_bot': 'false'
            }
        },
        
        # Case 2: tcins as JSON array string
        {
            "name": "JSON array tcins",
            "params": {
                'key': 'ff457966e64d5e877fdbad070f276d18ecec4a01',
                'tcins': json.dumps(tcins),  # ["89542109","94724987",...]
                'store_id': '1859',
                'pricing_store_id': '1859',
                'zip': '33809',
                'state': 'FL',
                'latitude': '28.0395',
                'longitude': '-81.9498',
                'is_bot': 'false'
            }
        },
        
        # Case 3: Multiple tcin parameters
        {
            "name": "Multiple tcin params",
            "params": {
                'key': 'ff457966e64d5e877fdbad070f276d18ecec4a01',
                'tcin': tcins,  # Will create multiple tcin= params
                'store_id': '1859',
                'pricing_store_id': '1859',
                'zip': '33809',
                'state': 'FL',
                'latitude': '28.0395',
                'longitude': '-81.9498',
                'is_bot': 'false'
            }
        },
        
        # Case 4: Pipe-separated tcins
        {
            "name": "Pipe-separated tcins",
            "params": {
                'key': 'ff457966e64d5e877fdbad070f276d18ecec4a01',
                'tcins': '|'.join(tcins),  # "89542109|94724987|..."
                'store_id': '1859',
                'pricing_store_id': '1859',
                'zip': '33809',
                'state': 'FL',
                'latitude': '28.0395',
                'longitude': '-81.9498',
                'is_bot': 'false'
            }
        }
    ]
    
    endpoints_to_test = [
        "https://redsky.target.com/redsky_aggregations/v1/web/product_fulfillment_v1",
        "https://redsky.target.com/redsky_aggregations/v1/web/bulk_fulfillment_v1",
        "https://redsky.target.com/redsky_aggregations/v1/web/batch_fulfillment_v1",
        "https://redsky.target.com/redsky_aggregations/v1/web/multi_product_fulfillment_v1"
    ]
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://www.target.com/',
    }
    
    print("🔍 TESTING BATCH STOCK API - MULTIPLE TCINS IN ONE CALL")
    print("="*80)
    print(f"Target TCINs: {', '.join(tcins)}")
    print("="*80)
    
    successful_calls = []
    
    for endpoint in endpoints_to_test:
        endpoint_name = endpoint.split('/')[-1]
        print(f"\n🧪 TESTING ENDPOINT: {endpoint_name}")
        print("-" * 60)
        
        for case in test_cases:
            try:
                print(f"\n   📋 {case['name']}:")
                
                response = requests.get(endpoint, params=case['params'], headers=headers, timeout=15)
                
                print(f"      Status: {response.status_code}")
                print(f"      Length: {len(response.text)} chars")
                
                if response.status_code == 200:
                    print("      ✅ SUCCESS!")
                    data = response.json()
                    
                    # Count how many products returned
                    product_count = count_products_in_response(data)
                    print(f"      📦 Products found: {product_count}")
                    
                    if product_count > 1:
                        print("      🎯 BATCH SUCCESS - Multiple products!")
                        successful_calls.append({
                            'endpoint': endpoint,
                            'case': case['name'],
                            'data': data,
                            'product_count': product_count
                        })
                        
                        # Show quick summary
                        show_batch_summary(data, tcins)
                        
                elif response.status_code == 400:
                    print("      ❌ Bad Request")
                elif response.status_code == 404:
                    print("      ❌ Not Found")  
                elif response.status_code == 410:
                    print("      ❌ Gone (deprecated)")
                else:
                    print(f"      ❌ Error {response.status_code}")
                    
            except Exception as e:
                print(f"      ❌ Exception: {e}")
    
    print(f"\n🏆 BATCH API RESULTS:")
    print("="*60)
    
    if successful_calls:
        print(f"✅ Found {len(successful_calls)} working batch methods!")
        for call in successful_calls:
            print(f"   📡 {call['endpoint'].split('/')[-1]} - {call['case']} ({call['product_count']} products)")
        
        # Show detailed results for best method
        best_call = max(successful_calls, key=lambda x: x['product_count'])
        print(f"\n🎯 BEST BATCH METHOD: {best_call['case']}")
        print(f"   Endpoint: {best_call['endpoint']}")
        print(f"   Products: {best_call['product_count']}")
        
        show_detailed_batch_results(best_call['data'], tcins)
        
    else:
        print("❌ No working batch methods found")
        print("💡 Individual API calls may be required for each TCIN")
        
        # Fallback: Show individual call timing
        print(f"\n⏱️  FALLBACK: Individual call timing test")
        test_individual_call_speed(tcins)

def count_products_in_response(data):
    """Count how many products are in the API response"""
    try:
        if isinstance(data, dict):
            if 'data' in data:
                if 'products' in data['data']:
                    return len(data['data']['products'])
                elif 'product' in data['data']:
                    return 1
        return 0
    except:
        return 0

def show_batch_summary(data, tcins):
    """Show quick summary of batch results"""
    print("      📊 Quick Stock Summary:")
    
    try:
        # Look for multiple products in response
        if 'data' in data and 'products' in data['data']:
            products = data['data']['products']
            for product in products:
                tcin = product.get('tcin', 'Unknown')
                fulfillment = product.get('fulfillment', {})
                shipping = fulfillment.get('shipping_options', {})
                qty = shipping.get('available_to_promise_quantity', 0)
                status = shipping.get('availability_status', 'UNKNOWN')
                print(f"         {tcin}: {status} ({int(qty)} units)")
    except Exception as e:
        print(f"         Error parsing: {e}")

def show_detailed_batch_results(data, tcins):
    """Show detailed results from successful batch call"""
    print("\n📋 DETAILED BATCH STOCK RESULTS:")
    print("-" * 50)
    
    try:
        # Parse and display each product's stock info
        if 'data' in data and 'products' in data['data']:
            products = data['data']['products']
            
            for product in products:
                tcin = product.get('tcin', 'Unknown')
                fulfillment = product.get('fulfillment', {})
                shipping = fulfillment.get('shipping_options', {})
                
                qty = int(shipping.get('available_to_promise_quantity', 0))
                status = shipping.get('availability_status', 'UNKNOWN')
                
                status_icon = "✅" if status == 'IN_STOCK' else "❌"
                print(f"   {status_icon} TCIN {tcin}: {status} - {qty} units available")
                
    except Exception as e:
        print(f"   Error parsing results: {e}")

def test_individual_call_speed(tcins):
    """Test speed of individual calls vs batch"""
    import time
    
    print("   Making 5 individual calls...")
    start_time = time.time()
    
    for tcin in tcins:
        try:
            url = "https://redsky.target.com/redsky_aggregations/v1/web/product_fulfillment_v1"
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
            
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                fulfillment = data.get('data', {}).get('product', {}).get('fulfillment', {})
                shipping = fulfillment.get('shipping_options', {})
                qty = int(shipping.get('available_to_promise_quantity', 0))
                status = shipping.get('availability_status', 'UNKNOWN')
                
                status_icon = "✅" if status == 'IN_STOCK' else "❌"
                print(f"      {status_icon} {tcin}: {qty} units")
                
        except Exception as e:
            print(f"      ❌ {tcin}: Error - {e}")
    
    total_time = time.time() - start_time
    print(f"   ⏱️  Total time: {total_time:.2f} seconds ({total_time/len(tcins):.2f}s per TCIN)")

if __name__ == '__main__':
    test_batch_tcins_single_call()
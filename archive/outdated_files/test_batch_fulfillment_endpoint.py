#!/usr/bin/env python3
"""
Test the correct batch endpoint found in search results:
product_summary_with_fulfillment_v1 with comma-separated tcins
"""

import requests
import json

def test_correct_batch_endpoint():
    """Test the product_summary_with_fulfillment_v1 endpoint with batch tcins"""
    
    tcins = ["89542109", "94724987", "94681785", "94681770", "94336414"]
    
    # The correct batch endpoint from search results
    url = "https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1"
    
    params = {
        'key': 'ff457966e64d5e877fdbad070f276d18ecec4a01',
        'tcins': ','.join(tcins),  # Comma-separated as shown in search results
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
    
    print("🎯 TESTING CORRECT BATCH ENDPOINT FROM SEARCH RESULTS")
    print("="*80)
    print(f"Endpoint: product_summary_with_fulfillment_v1")
    print(f"TCINs: {', '.join(tcins)}")
    print(f"Format: tcins={','.join(tcins)}")
    print("="*80)
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=20)
        
        print(f"Status Code: {response.status_code}")
        print(f"Response Length: {len(response.text)} characters")
        print(f"Full URL: {response.url}")
        
        if response.status_code == 200:
            print("✅ SUCCESS! Batch API call worked!")
            
            data = response.json()
            
            # Show raw response first
            print("\n📄 RAW RESPONSE:")
            print("="*60)
            print(json.dumps(data, indent=2))
            print("="*60)
            
            # Parse and show batch results
            parse_batch_response(data, tcins)
            
        else:
            print(f"❌ FAILED: HTTP {response.status_code}")
            print(f"Response: {response.text}")
            
    except Exception as e:
        print(f"❌ Error: {e}")

def parse_batch_response(data, tcins):
    """Parse the batch response and show stock information for each TCIN"""
    
    print("\n📊 BATCH STOCK RESULTS")
    print("="*60)
    
    try:
        # The response might have different structures for batch data
        products_data = None
        
        if 'data' in data:
            if 'products' in data['data']:
                products_data = data['data']['products']
            elif isinstance(data['data'], list):
                products_data = data['data']
            elif 'product' in data['data']:
                # Single product in batch format
                products_data = [data['data']['product']]
        
        if products_data:
            print(f"📦 Found {len(products_data)} products in batch response")
            print()
            
            # Summary table header
            print(f"{'TCIN':<12} {'Status':<15} {'Online Qty':<10} {'Store':<8} {'Product Name'}")
            print("-" * 80)
            
            for product in products_data:
                parse_single_product(product)
                
        else:
            print("❌ Could not find products data in response")
            print("Response structure:")
            show_response_structure(data)
            
    except Exception as e:
        print(f"❌ Error parsing batch response: {e}")

def parse_single_product(product):
    """Parse stock data for a single product in the batch response"""
    
    try:
        tcin = product.get('tcin', 'Unknown')
        
        # Get product name
        product_name = "Unknown Product"
        if 'item' in product and 'product_description' in product['item']:
            product_name = product['item']['product_description'].get('title', 'Unknown Product')
        
        # Get fulfillment/stock data
        fulfillment = product.get('fulfillment', {})
        
        if fulfillment:
            # Online availability
            shipping = fulfillment.get('shipping_options', {})
            online_status = shipping.get('availability_status', 'UNKNOWN')
            online_qty = int(shipping.get('available_to_promise_quantity', 0))
            
            # Store availability
            is_oos_all_stores = fulfillment.get('is_out_of_stock_in_all_store_locations', True)
            
            # Format for table
            status_icon = "✅" if online_status == 'IN_STOCK' else "❌"
            store_icon = "✅" if not is_oos_all_stores else "❌"
            
            print(f"{tcin:<12} {status_icon}{online_status:<14} {online_qty:<10} {store_icon:<7} {product_name[:30]}")
            
        else:
            print(f"{tcin:<12} {'❓NO_DATA':<15} {'0':<10} {'❓':<8} {product_name[:30]}")
            
    except Exception as e:
        print(f"{'ERROR':<12} {str(e)[:15]:<15} {'0':<10} {'❌':<8} Parse Error")

def show_response_structure(data, prefix="", max_depth=3, current_depth=0):
    """Show the structure of the response to help understand the format"""
    
    if current_depth >= max_depth:
        return
        
    if isinstance(data, dict):
        for key, value in list(data.items())[:10]:  # Limit to first 10 keys
            if isinstance(value, (dict, list)):
                print(f"{prefix}{key}: {type(value).__name__}")
                if len(str(value)) < 100:  # Show small objects
                    print(f"{prefix}  └─ {value}")
                else:
                    show_response_structure(value, prefix + "  ", max_depth, current_depth + 1)
            else:
                print(f"{prefix}{key}: {value}")
    elif isinstance(data, list):
        print(f"{prefix}List with {len(data)} items")
        if data and current_depth < max_depth:
            print(f"{prefix}First item:")
            show_response_structure(data[0], prefix + "  ", max_depth, current_depth + 1)

if __name__ == '__main__':
    test_correct_batch_endpoint()
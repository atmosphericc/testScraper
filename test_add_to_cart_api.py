#!/usr/bin/env python3
"""
Test if there's an add-to-cart or inventory API that would show actual purchasability
This might be the real way to determine if a preorder can be purchased
"""

import requests
import json
import time

def test_inventory_apis(tcin: str, description: str):
    """Test different inventory/cart-related APIs"""
    api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
    store_id = "865"
    
    headers = {
        'accept': 'application/json',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        'referer': 'https://www.target.com/',
    }
    
    print(f"\\n🧪 TESTING INVENTORY APIs FOR {description} (TCIN {tcin})")
    print("="*70)
    
    # Try inventory/fulfillment specific endpoint
    inventory_urls = [
        # Fulfillment API
        f"https://redsky.target.com/redsky_aggregations/v1/web/pdp_fulfillment_v1?key={api_key}&tcin={tcin}&store_id={store_id}&visitor_id=123456789",
        
        # Alternative PLP search that might show different data
        f"https://redsky.target.com/redsky_aggregations/v1/web/plp_search_v2?key={api_key}&tcin={tcin}&store_id={store_id}",
        
        # Try the batch/bulk endpoint for this single TCIN
        f"https://redsky.target.com/redsky_aggregations/v1/web/bulk_item_info_v1?key={api_key}&tcins={tcin}&store_id={store_id}",
    ]
    
    for i, url in enumerate(inventory_urls, 1):
        print(f"\\n{i}. Testing inventory API endpoint...")
        try:
            response = requests.get(url, headers=headers, timeout=10)
            print(f"   Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"   ✅ Success - got data")
                
                # Save response
                filename = f"inventory_api_{i}_{tcin}.json"
                with open(filename, 'w') as f:
                    json.dump(data, f, indent=2)
                print(f"   💾 Saved to: {filename}")
                
                # Quick analysis
                if 'data' in data:
                    print(f"   📊 Data structure: {list(data['data'].keys())}")
            else:
                print(f"   ❌ Failed: {response.text[:200]}")
                
        except Exception as e:
            print(f"   ❌ Error: {e}")
        
        time.sleep(0.5)

    # Also try a simpler approach - look for add to cart URL patterns
    print(f"\\n🔍 Checking product URL for add-to-cart behavior...")
    product_url = f"https://www.target.com/p/-/A-{tcin}"
    try:
        response = requests.head(product_url, headers=headers, timeout=10, allow_redirects=True)
        print(f"   Product page status: {response.status_code}")
        print(f"   Final URL: {response.url}")
        
        if response.status_code == 200:
            print(f"   ✅ Product page accessible")
        else:
            print(f"   ❌ Product page issue")
            
    except Exception as e:
        print(f"   ❌ Error checking product page: {e}")

def main():
    """Test all three TCINs with inventory APIs"""
    test_cases = [
        ('94681776', 'NOT AVAILABLE preorder'),
        ('94723520', 'AVAILABLE preorder'),  
        ('94827553', 'AVAILABLE preorder (user confirmed)')
    ]
    
    print("🔍 TESTING INVENTORY/CART APIs")
    print("Looking for APIs that show actual purchasability vs just product info")
    
    for tcin, description in test_cases:
        test_inventory_apis(tcin, description)
        time.sleep(1)
        
    print(f"\\n🎯 SUMMARY:")
    print("If any of these APIs return different data structure or availability flags,")
    print("that could be the key to determining actual preorder purchasability!")

if __name__ == "__main__":
    main()
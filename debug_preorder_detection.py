#!/usr/bin/env python3
"""
Debug preorder detection logic
"""

import json

def debug_preorder_detection():
    """Debug why preorder detection isn't working"""
    
    # Load the batch response to see the structure
    try:
        with open('success_fulfillment_94681776_var1.json', 'r') as f:
            data = json.load(f)
            
        print("🔍 DEBUGGING PREORDER DETECTION")
        print("=" * 50)
        
        if 'data' in data and 'product_summaries' in data['data']:
            for summary in data['data']['product_summaries']:
                tcin = summary.get('tcin')
                if tcin:
                    print(f"\n📦 TCIN {tcin}:")
                    
                    # Check item structure
                    item = summary.get('item', {})
                    print(f"  Item keys: {list(item.keys())}")
                    
                    # Check for eligibility_rules
                    has_eligibility = 'eligibility_rules' in item
                    print(f"  Has eligibility_rules: {has_eligibility}")
                    
                    # Check for street_date
                    mmbv_content = item.get('mmbv_content', {})
                    street_date = mmbv_content.get('street_date')
                    print(f"  Street date: {street_date}")
                    print(f"  MMBV content keys: {list(mmbv_content.keys())}")
                    
                    # Check fulfillment
                    fulfillment = summary.get('fulfillment', {})
                    shipping = fulfillment.get('shipping_options', {})
                    availability_status = shipping.get('availability_status')
                    print(f"  Availability status: {availability_status}")
                    
                    # Current logic result
                    is_preorder = not has_eligibility and street_date is not None
                    print(f"  Current logic says preorder: {is_preorder}")
                    
                    # Check if we should use different logic
                    if availability_status and 'PRE_ORDER' in availability_status:
                        print(f"  ✅ API says this IS a preorder (status contains PRE_ORDER)")
                    else:
                        print(f"  ❌ API says this is NOT a preorder")
                    
    except FileNotFoundError:
        print("❌ No test file found, let's make a fresh API call")
        
        import requests
        
        api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
        fulfillment_url = "https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1"
        
        headers = {
            'accept': 'application/json',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'referer': 'https://www.target.com/',
        }
        
        params = {
            'key': api_key,
            'tcins': '94681776',
            'store_id': '865',
            'pricing_store_id': '865',
            'has_pricing_context': 'true',
            'has_promotions': 'true',
            'is_bot': 'false',
        }
        
        try:
            response = requests.get(fulfillment_url, params=params, headers=headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                
                with open('debug_preorder_response.json', 'w') as f:
                    json.dump(data, f, indent=2)
                    
                print("✅ Fresh API call successful, saved to debug_preorder_response.json")
                
                # Now analyze
                if 'data' in data and 'product_summaries' in data['data']:
                    summary = data['data']['product_summaries'][0]
                    item = summary.get('item', {})
                    fulfillment = summary.get('fulfillment', {})
                    shipping = fulfillment.get('shipping_options', {})
                    
                    print(f"\n🔍 ANALYSIS:")
                    print(f"  Item has eligibility_rules: {'eligibility_rules' in item}")
                    print(f"  Item has street_date: {item.get('mmbv_content', {}).get('street_date') is not None}")
                    print(f"  Availability status: {shipping.get('availability_status')}")
                    
                    # The correct logic should be based on availability status
                    availability_status = shipping.get('availability_status', '')
                    is_preorder_by_status = 'PRE_ORDER' in availability_status
                    
                    print(f"\n💡 CORRECT LOGIC:")
                    print(f"  Use availability status contains 'PRE_ORDER': {is_preorder_by_status}")
                    
            else:
                print(f"❌ API call failed: {response.status_code}")
                
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    debug_preorder_detection()
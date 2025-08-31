#!/usr/bin/env python3
"""
Show raw API responses for all 5 products from config
"""
import requests
import json
from pprint import pprint

def get_api_response(tcin: str):
    """Get and display API response for a product"""
    
    url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
    params = {
        'key': '9f36aeafbe60771e321a7cc95a78140772ab3e96',
        'tcin': tcin,
        'store_id': '865',
        'pricing_store_id': '865',
        'has_pricing_store_id': 'true',
        'has_financing_options': 'true',
        'visitor_id': 'test123456789',
        'has_size_context': 'true'
    }
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
        'Referer': 'https://www.target.com/'
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=15)
        
        print(f"\n{'='*80}")
        print(f"📦 TCIN {tcin} - API RESPONSE")
        print(f"{'='*80}")
        print(f"HTTP Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            product = data['data']['product']
            item = product['item']
            
            # Basic info
            name = item.get('product_description', {}).get('title', 'Unknown')
            price = product.get('price', {}).get('current_retail', 0)
            print(f"Name: {name}")
            print(f"Price: ${price}")
            
            # Fulfillment data
            fulfillment = item.get('fulfillment', {})
            print(f"\nFULFILLMENT:")
            print(f"  is_marketplace: {fulfillment.get('is_marketplace', 'NOT SET')}")
            print(f"  purchase_limit: {fulfillment.get('purchase_limit', 'NOT SET')}")
            print(f"  All keys: {list(fulfillment.keys())}")
            
            # Eligibility rules
            eligibility = item.get('eligibility_rules', {})
            print(f"\nELIGIBILITY RULES:")
            if eligibility:
                print(f"  Has eligibility_rules: YES")
                print(f"  Keys: {list(eligibility.keys())}")
                
                # Ship to guest specifically
                ship_to_guest = eligibility.get('ship_to_guest', {})
                if ship_to_guest:
                    print(f"  ship_to_guest.is_active: {ship_to_guest.get('is_active', 'NOT SET')}")
                    print(f"  ship_to_guest full: {ship_to_guest}")
                else:
                    print(f"  ship_to_guest: NOT PRESENT")
            else:
                print(f"  Has eligibility_rules: NO")
            
            # Current algorithm result
            is_marketplace = fulfillment.get('is_marketplace', False)
            purchase_limit = fulfillment.get('purchase_limit', 0)
            has_eligibility = bool(eligibility)
            ship_to_guest_active = eligibility.get('ship_to_guest', {}).get('is_active', False)
            
            print(f"\nALGORITHM ANALYSIS:")
            print(f"  is_marketplace: {is_marketplace}")
            print(f"  purchase_limit: {purchase_limit}")
            print(f"  has_eligibility_rules: {has_eligibility}")
            print(f"  ship_to_guest.is_active: {ship_to_guest_active}")
            
            # New algorithm
            if is_marketplace:
                available = purchase_limit > 0
                logic = f"MARKETPLACE: purchase_limit > 0 = {available}"
            else:
                available = has_eligibility and ship_to_guest_active and purchase_limit >= 1
                logic = f"TARGET: has_eligibility AND ship_to_guest_active AND purchase_limit >= 1 = {available}"
            
            result = "🟢 IN STOCK" if available else "🔴 OUT OF STOCK"
            print(f"  Logic: {logic}")
            print(f"  API Result: {result}")
            
        else:
            print(f"❌ Error: {response.text[:500]}")
            
    except Exception as e:
        print(f"❌ Exception: {e}")

def main():
    """Test all 5 products from config"""
    print("🔍 RAW API RESPONSES FOR ALL 5 PRODUCTS")
    print("This shows exactly what the Target API returns for each product")
    
    tcins = ['94724987', '94681785', '94681770', '94336414', '89542109']
    
    for tcin in tcins:
        get_api_response(tcin)
    
    print(f"\n{'='*80}")
    print("📊 SUMMARY")
    print("Based on your website verification:")
    print("  89542109: Should be IN STOCK")
    print("  94336414: Should be IN STOCK") 
    print("  Others: Should be OUT OF STOCK")

if __name__ == "__main__":
    main()
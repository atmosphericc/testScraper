#!/usr/bin/env python3
"""
Deep dive into API response to find the TRUE availability indicators
"""
import requests
import json
from pprint import pprint

def deep_analyze(tcin: str, expected_status: str):
    """Deep analysis of a product's API response"""
    
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
        
        if response.status_code != 200:
            print(f"❌ TCIN {tcin}: HTTP {response.status_code}")
            return
            
        data = response.json()
        product = data['data']['product']
        item = product['item']
        
        print(f"\n{'='*80}")
        print(f"📦 TCIN {tcin} - EXPECTED: {expected_status}")
        print(f"{'='*80}")
        
        # Basic info
        name = item.get('product_description', {}).get('title', 'Unknown')[:60]
        print(f"Name: {name}")
        
        # Look for ALL possible availability/stock indicators
        print(f"\n🔍 SEARCHING ALL AVAILABILITY INDICATORS:")
        
        # Check root level product fields
        print(f"\n📋 PRODUCT LEVEL:")
        for key in product.keys():
            if any(keyword in key.lower() for keyword in ['avail', 'stock', 'inventory', 'fulfil']):
                print(f"  {key}: {product[key]}")
        
        # Check item level fields  
        print(f"\n📦 ITEM LEVEL:")
        for key in item.keys():
            if any(keyword in key.lower() for keyword in ['avail', 'stock', 'inventory', 'fulfil', 'ship', 'store']):
                value = item[key]
                if isinstance(value, dict) and len(value) < 10:  # Don't print huge objects
                    print(f"  {key}: {value}")
                elif not isinstance(value, dict):
                    print(f"  {key}: {value}")
                else:
                    print(f"  {key}: <dict with {len(value)} keys>")
        
        # Deep dive into fulfillment
        fulfillment = item.get('fulfillment', {})
        print(f"\n📋 FULFILLMENT DEEP DIVE:")
        for key, value in fulfillment.items():
            print(f"  {key}: {value}")
        
        # Deep dive into eligibility_rules
        eligibility = item.get('eligibility_rules', {})
        print(f"\n✅ ELIGIBILITY RULES DEEP DIVE:")
        if eligibility:
            for key, value in eligibility.items():
                print(f"  {key}: {value}")
        else:
            print("  NO ELIGIBILITY RULES")
        
        # Look for nested availability fields
        print(f"\n🔎 NESTED AVAILABILITY SEARCH:")
        
        def find_availability_fields(obj, path=""):
            """Recursively find fields containing availability keywords"""
            if isinstance(obj, dict):
                for key, value in obj.items():
                    current_path = f"{path}.{key}" if path else key
                    if any(keyword in key.lower() for keyword in ['avail', 'stock', 'inventory', 'active', 'enable']):
                        print(f"  {current_path}: {value}")
                    if isinstance(value, dict) and len(value) < 20:  # Avoid infinite recursion
                        find_availability_fields(value, current_path)
            elif isinstance(obj, list) and len(obj) < 10:
                for i, item_obj in enumerate(obj):
                    find_availability_fields(item_obj, f"{path}[{i}]")
        
        find_availability_fields(item)
        
        # Look at price fields too - sometimes unavailable items have different pricing
        price_info = product.get('price', {})
        print(f"\n💰 PRICE INFO:")
        for key, value in price_info.items():
            print(f"  {key}: {value}")
        
        # Save full response for manual inspection
        with open(f"full_analysis_{tcin}_{expected_status.lower()}.json", "w") as f:
            json.dump(data, f, indent=2)
        print(f"\n💾 Saved full response to full_analysis_{tcin}_{expected_status.lower()}.json")
        
    except Exception as e:
        print(f"❌ Error analyzing {tcin}: {e}")

def main():
    """Analyze products with known true status"""
    
    print("🔍 DEEP API ANALYSIS - Finding True Availability Indicators")
    print("Based on website verification:")
    print("- 94724987: API says IN STOCK, website says OUT OF STOCK")  
    print("- 89542109: API says OUT OF STOCK, website says IN STOCK")
    
    # Analyze the discrepancy cases
    deep_analyze('94724987', 'OUT_OF_STOCK')  # API wrong - says IN but is OUT
    deep_analyze('89542109', 'IN_STOCK')      # API wrong - says OUT but is IN
    
    # Also analyze one that matches for comparison
    deep_analyze('94681770', 'OUT_OF_STOCK')  # Should match API

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
Verify specific products based on user feedback
"""
import requests
import json

def check_eligibility_detailed(tcin: str):
    """Check detailed eligibility for a specific product"""
    
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
        
        name = item.get('product_description', {}).get('title', 'Unknown')[:50]
        price = product.get('price', {}).get('current_retail', 0)
        
        # Check fulfillment
        fulfillment = item.get('fulfillment', {})
        purchase_limit = fulfillment.get('purchase_limit', 0)
        
        # Check eligibility rules
        eligibility = item.get('eligibility_rules', {})
        has_eligibility = bool(eligibility)
        
        print(f"\n📦 {tcin} - {name}")
        print(f"💰 Price: ${price}")
        print(f"📋 purchase_limit: {purchase_limit}")
        print(f"📄 has_eligibility_rules: {has_eligibility}")
        
        if has_eligibility:
            print(f"✅ eligibility_rules keys: {list(eligibility.keys())}")
            
            ship_to_guest = eligibility.get('ship_to_guest', {})
            if ship_to_guest:
                is_active = ship_to_guest.get('is_active', False)
                print(f"🚚 ship_to_guest.is_active: {is_active}")
            else:
                print(f"🚚 ship_to_guest: NOT PRESENT")
        else:
            print(f"❌ NO eligibility_rules section")
        
        # Apply fixed algorithm
        has_eligibility_rules = bool(eligibility)
        ship_to_guest_active = eligibility.get('ship_to_guest', {}).get('is_active', False)
        available = has_eligibility_rules and ship_to_guest_active and purchase_limit >= 1
        
        result = "🟢 IN STOCK" if available else "🔴 OUT OF STOCK"
        print(f"🤖 Algorithm Result: {result}")
        
        return available
        
    except Exception as e:
        print(f"❌ Error checking {tcin}: {e}")
        return None

def main():
    """Check the specific products user mentioned"""
    
    print("🔍 DETAILED VERIFICATION")
    print("User said: All products are out of stock besides 89542109")
    print("=" * 70)
    
    # Check all 5 products
    tcins = ['94724987', '94681785', '94681770', '94336414', '89542109']
    
    results = {}
    for tcin in tcins:
        result = check_eligibility_detailed(tcin)
        results[tcin] = result
    
    print(f"\n{'='*70}")
    print("📊 SUMMARY")
    print("=" * 70)
    
    print("According to user: Only 89542109 should be IN STOCK")
    print("According to algorithm:")
    
    in_stock_count = 0
    for tcin, available in results.items():
        if available is not None:
            status = "IN STOCK" if available else "OUT STOCK"
            print(f"  {tcin}: {status}")
            if available:
                in_stock_count += 1
    
    print(f"\nDiscrepancy check:")
    if results.get('89542109') and in_stock_count == 1:
        print("✅ Perfect match - only 89542109 shows as in stock")
    else:
        print(f"⚠️  Issue: {in_stock_count} products show as in stock, but user says only 89542109 should be")
        print("This suggests there might be additional factors to consider in the algorithm")

if __name__ == "__main__":
    main()
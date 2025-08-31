#!/usr/bin/env python3
"""
Deep analysis of Target API responses to fix stock detection algorithm
"""
import requests
import json
import sys
from pathlib import Path

def analyze_product_response(tcin: str):
    """Get and analyze detailed API response for a product"""
    
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
            return None
            
        data = response.json()
        product = data['data']['product']
        item = product['item']
        
        print(f"\n{'='*80}")
        print(f"📦 TCIN {tcin} - DETAILED ANALYSIS")
        print(f"{'='*80}")
        
        # Basic info
        name = item.get('product_description', {}).get('title', 'Unknown')
        price = product.get('price', {}).get('current_retail', 0)
        print(f"Name: {name}")
        print(f"Price: ${price}")
        
        # Fulfillment analysis
        fulfillment = item.get('fulfillment', {})
        print(f"\n📋 FULFILLMENT DATA:")
        print(f"  is_marketplace: {fulfillment.get('is_marketplace', 'NOT SET')}")
        print(f"  purchase_limit: {fulfillment.get('purchase_limit', 'NOT SET')}")
        print(f"  is_po_box_prohibited: {fulfillment.get('is_po_box_prohibited', 'NOT SET')}")
        
        # Store fulfillment
        store_fulfillment = fulfillment.get('store_fulfillment', {})
        print(f"\n🏪 STORE FULFILLMENT:")
        print(f"  store_options: {store_fulfillment.get('store_options', 'NOT SET')}")
        print(f"  availability_status: {store_fulfillment.get('availability_status', 'NOT SET')}")
        print(f"  pickup_display_text: {store_fulfillment.get('pickup_display_text', 'NOT SET')}")
        
        # Shipping fulfillment
        shipping_fulfillment = fulfillment.get('shipping_fulfillment', {})
        print(f"\n🚚 SHIPPING FULFILLMENT:")
        print(f"  availability_status: {shipping_fulfillment.get('availability_status', 'NOT SET')}")
        print(f"  is_available: {shipping_fulfillment.get('is_available', 'NOT SET')}")
        
        # Eligibility rules
        eligibility = item.get('eligibility_rules', {})
        print(f"\n✅ ELIGIBILITY RULES:")
        
        # Ship to guest
        ship_to_guest = eligibility.get('ship_to_guest', {})
        print(f"  ship_to_guest:")
        print(f"    is_active: {ship_to_guest.get('is_active', 'NOT SET')}")
        print(f"    eligibility: {ship_to_guest.get('eligibility', 'NOT SET')}")
        
        # Hold for pickup
        hold_for_pickup = eligibility.get('hold_for_pickup', {})
        print(f"  hold_for_pickup:")
        print(f"    is_active: {hold_for_pickup.get('is_active', 'NOT SET')}")
        print(f"    eligibility: {hold_for_pickup.get('eligibility', 'NOT SET')}")
        
        # Buy online pickup in store
        bopis = eligibility.get('buy_online_pickup_in_store', {})
        print(f"  buy_online_pickup_in_store:")
        print(f"    is_active: {bopis.get('is_active', 'NOT SET')}")
        print(f"    eligibility: {bopis.get('eligibility', 'NOT SET')}")
        
        # Inventory
        inventory = item.get('inventory', {})
        print(f"\n📊 INVENTORY:")
        print(f"  street_date: {inventory.get('street_date', 'NOT SET')}")
        print(f"  availability: {inventory.get('availability', 'NOT SET')}")
        print(f"  inventory_status: {inventory.get('inventory_status', 'NOT SET')}")
        
        # Current algorithm result
        is_marketplace = fulfillment.get('is_marketplace', False)
        purchase_limit = fulfillment.get('purchase_limit', 0)
        ship_to_guest_active = ship_to_guest.get('is_active', False)
        
        print(f"\n🤖 CURRENT ALGORITHM:")
        print(f"  is_marketplace: {is_marketplace}")
        print(f"  purchase_limit: {purchase_limit}")
        print(f"  ship_to_guest.is_active: {ship_to_guest_active}")
        
        if is_marketplace:
            current_logic = purchase_limit > 0
            print(f"  MARKETPLACE LOGIC: purchase_limit > 0 = {current_logic}")
        else:
            current_logic = ship_to_guest_active and purchase_limit >= 1
            print(f"  TARGET LOGIC: ship_to_guest.is_active AND purchase_limit >= 1 = {current_logic}")
        
        result = "🟢 IN STOCK" if current_logic else "🔴 OUT OF STOCK"
        print(f"  CURRENT RESULT: {result}")
        
        # Suggested improved logic
        print(f"\n💡 IMPROVED LOGIC ANALYSIS:")
        
        # Check shipping availability
        shipping_available = shipping_fulfillment.get('is_available', False)
        shipping_status = shipping_fulfillment.get('availability_status', '')
        
        # Check store availability  
        store_status = store_fulfillment.get('availability_status', '')
        
        print(f"  shipping_fulfillment.is_available: {shipping_available}")
        print(f"  shipping_fulfillment.availability_status: '{shipping_status}'")
        print(f"  store_fulfillment.availability_status: '{store_status}'")
        
        # New suggested logic
        if is_marketplace:
            new_logic = purchase_limit > 0 and shipping_available
            print(f"  NEW MARKETPLACE LOGIC: purchase_limit > 0 AND shipping_available = {new_logic}")
        else:
            new_logic = (ship_to_guest_active and purchase_limit >= 1 and 
                        (shipping_available or shipping_status in ['AVAILABLE', 'LIMITED']))
            print(f"  NEW TARGET LOGIC: comprehensive availability check = {new_logic}")
        
        new_result = "🟢 IN STOCK" if new_logic else "🔴 OUT OF STOCK" 
        print(f"  NEW SUGGESTED RESULT: {new_result}")
        
        return {
            'tcin': tcin,
            'name': name,
            'current_result': current_logic,
            'suggested_result': new_logic,
            'shipping_available': shipping_available,
            'shipping_status': shipping_status,
            'store_status': store_status
        }
        
    except Exception as e:
        print(f"❌ Error analyzing {tcin}: {e}")
        return None

def main():
    """Analyze all products from config"""
    tcins = ['94724987', '94681785', '94681770', '94336414', '89542109']
    
    print("🔍 DEEP API RESPONSE ANALYSIS")
    print("Analyzing all products to fix stock detection algorithm...\n")
    
    results = []
    for tcin in tcins:
        result = analyze_product_response(tcin)
        if result:
            results.append(result)
    
    # Summary
    print(f"\n{'='*80}")
    print("📊 SUMMARY COMPARISON")
    print(f"{'='*80}")
    print(f"{'TCIN':<12} {'Current':<10} {'Suggested':<12} {'Shipping':<12} {'Status'}")
    print("-" * 80)
    
    for r in results:
        current = "IN STOCK" if r['current_result'] else "OUT STOCK"  
        suggested = "IN STOCK" if r['suggested_result'] else "OUT STOCK"
        shipping = "YES" if r['shipping_available'] else "NO"
        
        print(f"{r['tcin']:<12} {current:<10} {suggested:<12} {shipping:<12} {r['shipping_status']}")

if __name__ == "__main__":
    main()
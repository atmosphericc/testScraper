#!/usr/bin/env python3
"""
Extract full stock response from the working product_fulfillment_v1 endpoint
Focus on getting exact availability numbers like Discord stock bots show
"""

import requests
import json

def get_full_stock_response(tcin="89542109"):
    """Get complete stock response with all availability data"""
    
    # Working endpoint found
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
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': f'https://www.target.com/p/-/A-{tcin}',
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            
            print("🎯 COMPLETE STOCK RESPONSE FOR TARGET DISCORD-STYLE BOT")
            print("="*80)
            print(json.dumps(data, indent=2))
            print("="*80)
            
            # Extract key stock metrics like Discord bots show
            extract_discord_style_metrics(data, tcin)
            
            return data
        else:
            print(f"❌ Failed: {response.status_code}")
            return None
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return None

def extract_discord_style_metrics(data, tcin):
    """Extract stock metrics in the format Discord stock bots typically show"""
    
    print("\n📊 DISCORD-STYLE STOCK METRICS")
    print("="*60)
    
    try:
        product = data.get('data', {}).get('product', {})
        fulfillment = product.get('fulfillment', {})
        
        # Product basics
        product_name = product.get('item', {}).get('product_description', {}).get('title', 'Unknown Product')
        price = product.get('price', {}).get('formatted_current_price', 'N/A')
        
        print(f"🏷️  Product: {product_name}")
        print(f"💰 Price: {price}")
        print(f"🏷️  TCIN: {tcin}")
        print()
        
        # Online shipping availability  
        shipping = fulfillment.get('shipping_options', {})
        online_status = shipping.get('availability_status', 'UNKNOWN')
        online_quantity = shipping.get('available_to_promise_quantity', 0)
        
        if online_status == 'IN_STOCK':
            print(f"🌐 ONLINE SHIPPING: ✅ IN STOCK")
            print(f"📦 Available Quantity: {int(online_quantity)} units")
        else:
            print(f"🌐 ONLINE SHIPPING: ❌ {online_status}")
            print(f"📦 Available Quantity: 0 units")
        
        # Store availability
        is_oos_all_stores = fulfillment.get('is_out_of_stock_in_all_store_locations', True)
        store_options = fulfillment.get('store_options', [])
        
        print()
        print(f"🏪 STORE AVAILABILITY:")
        
        if is_oos_all_stores:
            print("   ❌ Out of stock in ALL store locations")
        else:
            print("   ✅ Available in some stores")
            
        # Show individual store data
        for store in store_options:
            store_name = store.get('store', {}).get('location_name', 'Unknown Store')
            store_qty = store.get('location_available_to_promise_quantity', 0)
            pickup_status = store.get('order_pickup', {}).get('availability_status', 'UNKNOWN')
            
            print(f"   📍 {store_name}: {int(store_qty)} units (Pickup: {pickup_status})")
        
        # Summary like Discord bots
        print()
        print("📋 SUMMARY:")
        total_available = int(online_quantity)
        if total_available > 0:
            print(f"   🎯 TOTAL AVAILABLE: {total_available} units online")
            print(f"   🚚 Shipping: Available")
            print(f"   🏪 Store Pickup: {'Not Available' if is_oos_all_stores else 'Check Stores'}")
        else:
            print(f"   ❌ CURRENTLY SOLD OUT")
            
        # Return structured data for bot usage
        return {
            'tcin': tcin,
            'product_name': product_name,
            'price': price,
            'online_available': online_status == 'IN_STOCK',
            'online_quantity': int(online_quantity),
            'store_available': not is_oos_all_stores,
            'total_quantity': int(online_quantity),
            'status': 'IN_STOCK' if online_quantity > 0 else 'OUT_OF_STOCK'
        }
        
    except Exception as e:
        print(f"❌ Error extracting metrics: {e}")
        return None

def test_multiple_tcins():
    """Test multiple TCINs to see quantity variations"""
    
    tcins = [
        "89542109",  # Your Quaquaval deck
        "94724987",  # Blooming Waters Premium Collection
        "94681785",  # White Flare Booster Bundle  
        "94681770",  # Black Bolt Booster Bundle
        "94336414"   # Prismatic Evolutions Surprise Box
    ]
    
    print("\n🔍 TESTING MULTIPLE TCINS FOR QUANTITY PATTERNS")
    print("="*80)
    
    results = []
    for tcin in tcins:
        print(f"\n🧪 Testing TCIN: {tcin}")
        print("-" * 40)
        
        data = get_full_stock_response(tcin)
        if data:
            metrics = extract_discord_style_metrics(data, tcin)
            if metrics:
                results.append(metrics)
        
        print("\n" + "="*60)
    
    # Summary table
    print("\n📊 STOCK SUMMARY TABLE (Discord Bot Style)")
    print("="*80)
    print(f"{'TCIN':<12} {'Status':<12} {'Online Qty':<10} {'Store':<10} {'Product'}")
    print("-" * 80)
    
    for result in results:
        status_icon = "✅" if result['online_available'] else "❌"
        store_icon = "✅" if result['store_available'] else "❌"
        print(f"{result['tcin']:<12} {status_icon:<12} {result['online_quantity']:<10} {store_icon:<10} {result['product_name'][:40]}")

if __name__ == '__main__':
    # Get full response for main TCIN
    print("🎯 TARGET STOCK API - FULL RESPONSE ANALYSIS")
    print("="*80)
    
    get_full_stock_response("89542109")
    
    # Test multiple products for comparison
    test_multiple_tcins()
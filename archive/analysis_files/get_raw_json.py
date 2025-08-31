#!/usr/bin/env python3
"""
Get raw JSON response to understand the actual API structure
"""
import requests
import json

def get_raw_response(tcin: str):
    """Get raw API response"""
    
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
        
        # Save to file for inspection
        with open(f"raw_response_{tcin}.json", "w") as f:
            json.dump(data, f, indent=2)
        
        print(f"✅ Saved raw response for {tcin} to raw_response_{tcin}.json")
        
        # Show structure
        product = data['data']['product']
        item = product['item']
        
        print(f"\n📋 ACTUAL JSON STRUCTURE FOR {tcin}:")
        print("=" * 60)
        
        # Show what's actually in fulfillment
        fulfillment = item.get('fulfillment', {})
        print("fulfillment keys:", list(fulfillment.keys()))
        
        # Show what's actually in eligibility_rules
        eligibility = item.get('eligibility_rules', {})
        print("eligibility_rules keys:", list(eligibility.keys()))
        
        # Show what's in ship_to_guest specifically
        ship_to_guest = eligibility.get('ship_to_guest', {})
        print("ship_to_guest keys:", list(ship_to_guest.keys()))
        print("ship_to_guest content:", ship_to_guest)
        
        return data
        
    except Exception as e:
        print(f"❌ Error getting raw response for {tcin}: {e}")
        return None

def main():
    # Get raw responses for a couple products to understand structure
    tcins = ['94724987', '89542109']  # One that shows IN STOCK, one OUT OF STOCK
    
    for tcin in tcins:
        print(f"\nGetting raw JSON for {tcin}...")
        get_raw_response(tcin)

if __name__ == "__main__":
    main()
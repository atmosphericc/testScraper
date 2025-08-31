#!/usr/bin/env python3
"""
Try different Target API endpoints to find inventory/availability data
"""
import requests
import json

def try_different_endpoints(tcin: str):
    """Try various Target API endpoints for inventory data"""
    
    print(f"\n{'='*60}")
    print(f"🔍 Testing different endpoints for TCIN {tcin}")
    print(f"{'='*60}")
    
    # Standard headers
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
        'Referer': 'https://www.target.com/'
    }
    
    api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
    store_id = "865"
    
    # 1. Try location-specific inventory endpoint
    print(f"1. 📍 Location Inventory API:")
    try:
        url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_location_v1"
        params = {
            'key': api_key,
            'tcin': tcin,
            'store_id': store_id,
            'pricing_store_id': store_id,
            'visitor_id': 'test123'
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        print(f"   Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"   Keys: {list(data.keys())}")
            
            # Save response
            with open(f"location_api_{tcin}.json", "w") as f:
                json.dump(data, f, indent=2)
            print(f"   📁 Saved to location_api_{tcin}.json")
        else:
            print(f"   Response: {response.text[:200]}")
            
    except Exception as e:
        print(f"   Error: {e}")
    
    # 2. Try store fulfillment endpoint  
    print(f"\n2. 🏪 Store Fulfillment API:")
    try:
        url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_fulfillment_v1"
        params = {
            'key': api_key,
            'tcin': tcin,
            'store_id': store_id,
            'visitor_id': 'test123'
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        print(f"   Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"   Keys: {list(data.keys())}")
            
            with open(f"fulfillment_api_{tcin}.json", "w") as f:
                json.dump(data, f, indent=2)
            print(f"   📁 Saved to fulfillment_api_{tcin}.json")
        else:
            print(f"   Response: {response.text[:200]}")
            
    except Exception as e:
        print(f"   Error: {e}")
        
    # 3. Try adding more specific parameters to main API
    print(f"\n3. 🎯 Enhanced Main API:")
    try:
        url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
        params = {
            'key': api_key,
            'tcin': tcin,
            'store_id': store_id,
            'pricing_store_id': store_id,
            'has_pricing_store_id': 'true',
            'has_financing_options': 'true',
            'visitor_id': 'test123',
            'has_size_context': 'true',
            'has_subscription_context': 'true',
            'has_pickup_context': 'true',
            'has_delivery_context': 'true'
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        print(f"   Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            # Look for additional fields with enhanced params
            item = data['data']['product']['item']
            
            print(f"   Checking for new fields...")
            for key in item.keys():
                if 'delivery' in key.lower() or 'pickup' in key.lower() or 'subscription' in key.lower():
                    print(f"   New field: {key}: {item[key]}")
            
            # Check if eligibility rules have more detail
            eligibility = item.get('eligibility_rules', {})
            if eligibility:
                print(f"   Eligibility with enhanced params:")
                for key, value in eligibility.items():
                    print(f"     {key}: {value}")
        else:
            print(f"   Response: {response.text[:200]}")
            
    except Exception as e:
        print(f"   Error: {e}")
        
    # 4. Try the add to cart endpoint to see what happens
    print(f"\n4. 🛒 Add to Cart Test (simulation):")
    print("   (This would test actual availability but we won't actually add to cart)")
    print("   Real availability might only be detectable via cart operations")

def main():
    """Test different endpoints on known products"""
    
    print("🔍 TRYING ALTERNATIVE TARGET API ENDPOINTS")
    print("Looking for inventory-specific endpoints that might give true availability")
    
    # Test on the discrepancy cases
    try_different_endpoints('89542109')  # Should be IN STOCK but API says OUT
    try_different_endpoints('94724987')  # Should be OUT but API says IN

if __name__ == "__main__":
    main()
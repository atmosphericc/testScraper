#!/usr/bin/env python3
"""
Test the new API endpoints discovered from the curl requests
Using the correct store IDs and parameters
"""
import requests
import json

def test_enhanced_api(tcin: str):
    """Test with the exact parameters from the curl request"""
    
    print(f"\n🔍 Testing enhanced API for {tcin}")
    print("=" * 60)
    
    # Enhanced headers from the curl request
    headers = {
        'accept': 'application/json',
        'accept-language': 'en-US,en;q=0.9',
        'origin': 'https://www.target.com',
        'referer': f'https://www.target.com/p/-/A-{tcin}',
        'sec-ch-ua': '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"macOS"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-site',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
    }
    
    api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
    
    # Test multiple store IDs from your curl requests
    store_tests = [
        {'id': '3229', 'name': 'Primary Store (from curl)'},
        {'id': '2776', 'name': 'Secondary Store (from curl)'},
        {'id': '865', 'name': 'Our Current Store'}
    ]
    
    for store_info in store_tests:
        store_id = store_info['id']
        store_name = store_info['name']
        
        print(f"\n📍 Testing Store ID {store_id} - {store_name}")
        
        # Main pdp_client_v1 endpoint
        url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
        params = {
            'key': api_key,
            'tcin': tcin,
            'store_id': store_id,
            'pricing_store_id': store_id,
            'has_pricing_store_id': 'true',
            'has_financing_options': 'true',
            'visitor_id': '0196A7360EB402019454A2498267180C',  # From curl
            'channel': 'WEB',
            'page': f'/p/A-{tcin}',
            'skip_personalized': 'true',  # From curl
            'include_obsolete': 'true',   # From curl
        }
        
        try:
            response = requests.get(url, params=params, headers=headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                
                # Parse the response
                product = data['data']['product']
                item = product['item']
                
                name = item.get('product_description', {}).get('title', 'Unknown')
                price = product.get('price', {}).get('current_retail', 0)
                
                # Check fulfillment and eligibility
                fulfillment = item.get('fulfillment', {})
                eligibility = item.get('eligibility_rules', {})
                
                is_marketplace = fulfillment.get('is_marketplace', False)
                purchase_limit = fulfillment.get('purchase_limit', 0)
                has_eligibility = bool(eligibility)
                ship_to_guest_active = eligibility.get('ship_to_guest', {}).get('is_active', False)
                
                # Apply our algorithm
                if is_marketplace:
                    available = purchase_limit > 0
                else:
                    available = has_eligibility and ship_to_guest_active and purchase_limit >= 1
                
                status = "🟢 IN STOCK" if available else "🔴 OUT OF STOCK"
                
                print(f"  Status: {status}")
                print(f"  Name: {name[:50]}")
                print(f"  Price: ${price}")
                print(f"  Has eligibility_rules: {has_eligibility}")
                print(f"  Ship to guest active: {ship_to_guest_active}")
                print(f"  Purchase limit: {purchase_limit}")
                
                if has_eligibility:
                    print(f"  Eligibility keys: {list(eligibility.keys())}")
                
            else:
                print(f"  ❌ HTTP {response.status_code}: {response.text[:200]}")
                
        except Exception as e:
            print(f"  ❌ Error: {e}")

def main():
    """Test all 5 products with enhanced API parameters"""
    
    print("🚀 TESTING ENHANCED API WITH CURL PARAMETERS")
    print("Using the exact store IDs and parameters from your curl request")
    
    tcins = ['89542109', '94724987', '94681785', '94681770', '94336414']
    
    for tcin in tcins:
        test_enhanced_api(tcin)
    
    print(f"\n{'='*80}")
    print("📊 SUMMARY")
    print("This should show which store ID gives the most accurate results")
    print("Expected: Only 89542109 should show as IN STOCK")

if __name__ == "__main__":
    main()
import requests
import json

def test_tcin_with_working_api(tcin):
    # Using the working API format from TEST_for_grabbing_stock_info.py
    url = f"https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1?key=9f36aeafbe60771e321a7cc95a78140772ab3e96&tcin={tcin}&is_bot=false&store_id=865&pricing_store_id=865&has_pricing_store_id=true&has_financing_options=true&include_obsolete=true&visitor_id=0198538661860201B9F1AD74ED8A1AE4&skip_personalized=true&skip_variation_hierarchy=true&channel=WEB&page=%2Fp%2FA-{tcin}"
    
    headers = {
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9",
        "origin": "https://www.target.com",
        "referer": f"https://www.target.com/p/A-{tcin}",
        "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"\nTCIN {tcin}:")
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            product = data['data']['product']
            item = product['item']
            
            name = item['product_description']['title']
            price = product['price']['current_retail']
            
            fulfillment = item['fulfillment']
            is_marketplace = fulfillment.get('is_marketplace', False)
            purchase_limit = fulfillment.get('purchase_limit', 0)
            
            eligibility = item.get('eligibility_rules', {})
            ship_to_guest = eligibility.get('ship_to_guest', {}).get('is_active', False)
            
            print(f"Name: {name[:50]}")
            print(f"Price: ${price}")
            print(f"Marketplace: {is_marketplace}")
            print(f"Purchase Limit: {purchase_limit}")
            print(f"Ship to Guest: {ship_to_guest}")
            
            # Test stock logic from the working file
            if is_marketplace:
                available = purchase_limit > 0
                logic = f"Marketplace: purchase_limit > 0 = {available}"
            else:
                available = ship_to_guest and len(eligibility) > 0
                logic = f"Target: ship_to_guest AND has_eligibility = {available}"
            
            print(f"Stock Logic: {logic}")
            print(f"Should be: {'IN STOCK' if available else 'OUT OF STOCK'}")
            
        else:
            print(f"Error: {response.status_code}")
            print(f"Response: {response.text[:200]}")
            
    except Exception as e:
        print(f"Exception: {e}")

# Test your configured TCINs plus some known working ones
tcins = ["89542109", "1001304528", "94300069"]  # Mix of yours + known working

for tcin in tcins:
    test_tcin_with_working_api(tcin)
    print("-" * 50)
import requests
import json
import time
from typing import List, Dict, Any

def get_target_product_info(tcin: str) -> Dict[str, Any]:
    """
    Fetch product information from Target's API for a single TCIN
    """
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
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Error fetching TCIN {tcin}: {e}")
        return None

def process_product_data(tcin: str, api_response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process raw API response into clean product data
    """
    try:
        product_data = api_response['data']['product']
        name = product_data['item']['product_description']['title']
        current_price = product_data['price']['current_retail']

        # Check fulfillment and eligibility
        fulfillment = product_data['item']['fulfillment']
        eligibility_rules = product_data['item'].get('eligibility_rules', {})
        is_marketplace = fulfillment.get('is_marketplace', False)
        purchase_limit = fulfillment.get('purchase_limit', 0)

        # Determine seller type and availability
        if is_marketplace:
            # Third-party marketplace seller
            seller_type = "third-party"
            available = purchase_limit > 0
            want_to_buy = False  # You don't want third-party
        elif len(eligibility_rules) > 0:
            # Target direct with eligibility rules
            seller_type = "target"
            ship_to_guest = eligibility_rules.get('ship_to_guest', {}).get('is_active', False)
            available = ship_to_guest
            want_to_buy = available  # Only if available
        else:
            # Likely preorder or special Target item
            seller_type = "preorder"
            available = purchase_limit > 0
            want_to_buy = available  # You want preorders

        return {
            "tcin": tcin,
            "name": name,
            "current_price": current_price,
            "available": available,
            "seller_type": seller_type,
            "want_to_buy": want_to_buy,
            "purchase_limit": purchase_limit,
            "link": f"https://www.target.com/p/A-{tcin}",
            "status": "success"
        }
    
    except (KeyError, TypeError) as e:
        print(f"Error processing data for TCIN {tcin}: {e}")
        return {
            "tcin": tcin,
            "name": "Error - Unable to fetch product data",
            "current_price": 0.0,
            "available": False,
            "seller_type": "unknown",
            "want_to_buy": False,
            "purchase_limit": 0,
            "link": f"https://www.target.com/p/A-{tcin}",
            "status": "error"
        }

def get_multiple_target_products(tcin_list: List[str], delay: float = 1.0) -> List[Dict[str, Any]]:
    """
    Fetch product information for multiple TCINs
    
    Args:
        tcin_list: List of TCIN strings to fetch
        delay: Delay between requests in seconds to be respectful to the API
    
    Returns:
        List of product data dictionaries
    """
    products = []
    
    for i, tcin in enumerate(tcin_list):
        print(f"Processing TCIN {i+1}/{len(tcin_list)}: {tcin}")
        
        # Fetch raw data from API
        api_response = get_target_product_info(tcin)
        
        if api_response:
            # Process the data
            product_info = process_product_data(tcin, api_response)
        else:
            # Create error entry if API call failed
            product_info = {
                "tcin": tcin,
                "name": "Error - Failed to fetch data",
                "current_price": 0.0,
                "available": False,
                "seller_type": "unknown",
                "want_to_buy": False,
                "purchase_limit": 0,
                "link": f"https://www.target.com/p/A-{tcin}",
                "status": "error"
            }
        
        products.append(product_info)
        
        # Add delay between requests to be respectful
        if i < len(tcin_list) - 1:  # Don't delay after the last request
            time.sleep(delay)
    
    return products

# Example usage
if __name__ == "__main__":
    # List of TCINs to fetch
    tcin_list = [
        "1001304528",
        "94300069", 
        "93859727",
        "94694203"
    ]
    
    print(f"Fetching data for {len(tcin_list)} products...")
    products = get_multiple_target_products(tcin_list, delay=1.5)  # 1.5 second delay between requests
    
    # Print results
    print("\n" + "="*50)
    print("RESULTS:")
    print("="*50)
    
    for product in products:
        print(json.dumps(product, indent=2))
        print("-" * 30)
    
    # Save to file
    with open('target_products.json', 'w') as f:
        json.dump(products, f, indent=2)
    
    print(f"\nData saved to target_products.json")
    print(f"Successfully processed: {sum(1 for p in products if p['status'] == 'success')}/{len(products)} products")
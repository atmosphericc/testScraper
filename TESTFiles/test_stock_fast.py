import requests
import json
import time
import random
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

def get_target_product_info(tcin: str) -> Dict[str, Any]:
    """
    Fetch product information from Target's API for a single TCIN
    """
    url = f"https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1?key=9f36aeafbe60771e321a7cc95a78140772ab3e96&tcin={tcin}&is_bot=false&store_id=865&pricing_store_id=865&has_pricing_store_id=true&has_financing_options=true&include_obsolete=true&visitor_id=0198538661860201B9F1AD74ED8A1AE4&skip_personalized=true&skip_variation_hierarchy=true&channel=WEB&page=%2Fp%2FA-{tcin}"
    
    # Rotate user agents to avoid detection
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    ]
    
    headers = {
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9",
        "origin": "https://www.target.com",
        "referer": f"https://www.target.com/p/A-{tcin}",
        "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="121", "Google Chrome";v="121"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": random.choice(user_agents)
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
        
        # Clean HTML entities
        import html
        name = html.unescape(name)
        
        # Price extraction
        price_data = product_data.get('price', {})
        current_price = price_data.get('current_retail', 0.0)
        if current_price is None:
            current_price = price_data.get('formatted_current_price', '$0.00')
            if isinstance(current_price, str):
                import re
                price_match = re.search(r'[\d.]+', current_price)
                current_price = float(price_match.group()) if price_match else 0.0

        # Check fulfillment and eligibility
        fulfillment = product_data['item']['fulfillment']
        eligibility_rules = product_data['item'].get('eligibility_rules', {})
        is_marketplace = fulfillment.get('is_marketplace', False)
        purchase_limit = fulfillment.get('purchase_limit', 0)
        ship_to_guest = eligibility_rules.get('ship_to_guest', {}).get('is_active', False)

        # Determine availability using your bulletproof algorithm
        if is_marketplace:
            seller_type = "third-party"
            available = purchase_limit > 0
            want_to_buy = False
        else:
            seller_type = "target"
            
            if not ship_to_guest:
                # Special case for high-value pre-orders
                if current_price >= 400 and purchase_limit == 1 and len(eligibility_rules) == 0:
                    available = True
                else:
                    available = False
            elif purchase_limit <= 1:
                available = False
            elif ship_to_guest and purchase_limit >= 2:
                available = True
            else:
                available = False
            
            want_to_buy = available

        stock_status = "In Stock" if available else "Out of Stock"
        
        return {
            "tcin": tcin,
            "name": name[:60] + "..." if len(name) > 60 else name,
            "price": f"${current_price:.2f}",
            "available": available,
            "stock": stock_status,
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
            "name": "Error - Unable to process",
            "price": "$0.00",
            "available": False,
            "stock": "Error",
            "seller_type": "unknown",
            "want_to_buy": False,
            "purchase_limit": 0,
            "link": f"https://www.target.com/p/A-{tcin}",
            "status": "error"
        }

def fetch_product_with_jitter(tcin: str) -> Dict[str, Any]:
    """
    Fetch a single product with random jitter to avoid patterns
    """
    # Add small random delay to avoid all requests hitting at exact same time
    time.sleep(random.uniform(0, 0.3))
    
    api_response = get_target_product_info(tcin)
    if api_response:
        return process_product_data(tcin, api_response)
    else:
        return {
            "tcin": tcin,
            "name": "Error - Failed to fetch",
            "price": "$0.00",
            "available": False,
            "stock": "API Error",
            "seller_type": "unknown",
            "want_to_buy": False,
            "purchase_limit": 0,
            "link": f"https://www.target.com/p/A-{tcin}",
            "status": "api_error"
        }

def get_multiple_products_fast(tcin_list: List[str], max_workers: int = 3) -> List[Dict[str, Any]]:
    """
    Fetch multiple products concurrently
    
    Args:
        tcin_list: List of TCINs to fetch
        max_workers: Number of simultaneous requests (3 is safe, 5 is usually ok)
    
    Returns:
        List of product data dictionaries
    """
    products = []
    total = len(tcin_list)
    completed = 0
    
    print(f"Fetching {total} products with {max_workers} concurrent requests...")
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_tcin = {
            executor.submit(fetch_product_with_jitter, tcin): tcin 
            for tcin in tcin_list
        }
        
        # Collect results as they complete
        for future in as_completed(future_to_tcin):
            completed += 1
            result = future.result()
            products.append(result)
            
            # Progress indicator
            status_emoji = "✅" if result['available'] else "❌"
            print(f"[{completed}/{total}] {status_emoji} {result['tcin']}: {result['name'][:30]}... - {result['stock']}")
    
    elapsed = time.time() - start_time
    print(f"\nCompleted in {elapsed:.2f} seconds ({elapsed/total:.2f}s per product)")
    
    return products

def print_summary(products: List[Dict[str, Any]]):
    """
    Print a nice summary of the results
    """
    print("\n" + "="*60)
    print("STOCK CHECK SUMMARY")
    print("="*60)
    
    # Group by status
    in_stock = [p for p in products if p['available']]
    out_of_stock = [p for p in products if not p['available'] and p['status'] == 'success']
    errors = [p for p in products if p['status'] != 'success']
    want_to_buy = [p for p in products if p['want_to_buy']]
    
    # Print in stock items
    if in_stock:
        print("\n✅ IN STOCK:")
        for p in in_stock:
            buy_flag = " 🎯 [WANT TO BUY]" if p['want_to_buy'] else ""
            print(f"  - {p['tcin']}: {p['name'][:40]}... {p['price']}{buy_flag}")
            print(f"    {p['link']}")
    
    # Print out of stock items
    if out_of_stock:
        print("\n❌ OUT OF STOCK:")
        for p in out_of_stock:
            print(f"  - {p['tcin']}: {p['name'][:40]}...")
    
    # Print errors
    if errors:
        print("\n⚠️ ERRORS:")
        for p in errors:
            print(f"  - {p['tcin']}: Failed to fetch")
    
    # Summary stats
    print("\n📊 STATISTICS:")
    print(f"  Total products: {len(products)}")
    print(f"  In stock: {len(in_stock)}")
    print(f"  Out of stock: {len(out_of_stock)}")
    print(f"  Want to buy: {len(want_to_buy)}")
    print(f"  Errors: {len(errors)}")

# Example usage
if __name__ == "__main__":
    # Your product list - can also load from product_config.json
    tcin_list = [
        "94724987",  # From your config
        "94681785",
        "94681770",
        "94336414",
        "1001304528",
        "94300069",
        "93859727",
        "94694203"
    ]
    
    # Run with different speeds
    print("Choose speed setting:")
    print("1. Safe (3 concurrent) - Recommended")
    print("2. Fast (5 concurrent)")
    print("3. Aggressive (8 concurrent) - Risk of rate limiting")
    
    choice = input("Enter choice (1-3): ").strip() or "1"
    
    max_workers = {"1": 3, "2": 5, "3": 8}.get(choice, 3)
    
    # Fetch products
    products = get_multiple_products_fast(tcin_list, max_workers=max_workers)
    
    # Print summary
    print_summary(products)
    
    # Save to file
    with open('stock_check_results.json', 'w') as f:
        json.dump(products, f, indent=2)
    
    print(f"\nDetailed results saved to stock_check_results.json")
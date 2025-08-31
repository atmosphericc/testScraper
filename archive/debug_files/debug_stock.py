import aiohttp
import asyncio
import random
import json

async def test_stock_api(tcin):
    base_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
    api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
    store_id = "865"
    
    chrome_version = random.randint(120, 125)
    headers = {
        'accept': 'application/json',
        'accept-language': 'en-US,en;q=0.9',
        'origin': 'https://www.target.com',
        'user-agent': f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_version}.0.0.0 Safari/537.36',
        'sec-ch-ua': f'"Google Chrome";v="{chrome_version}", "Chromium";v="{chrome_version}", "Not?A_Brand";v="24"',
        'sec-fetch-site': 'same-site',
        'sec-fetch-mode': 'cors'
    }
    
    params = {
        'key': api_key,
        'tcin': tcin,
        'store_id': store_id,
        'pricing_store_id': store_id,
        'has_pricing_store_id': 'true',
        'has_financing_options': 'true',
        'visitor_id': ''.join(random.choices('0123456789ABCDEF', k=32)),
        'has_size_context': 'true'
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.get(base_url, params=params, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                
                # Extract the key fields we're using for stock detection
                product = data['data']['product']
                item = product['item']
                
                fulfillment = item.get('fulfillment', {})
                is_marketplace = fulfillment.get('is_marketplace', False)
                purchase_limit = fulfillment.get('purchase_limit', 0)
                
                eligibility = item.get('eligibility_rules', {})
                ship_to_guest = eligibility.get('ship_to_guest', {}).get('is_active', False)
                
                name = item.get('product_description', {}).get('title', 'Unknown')
                price = product.get('price', {}).get('current_retail', 0)
                
                print(f"\n=== TCIN {tcin} ({name[:30]}) ===")
                print(f"Price: ${price}")
                print(f"is_marketplace: {is_marketplace}")
                print(f"purchase_limit: {purchase_limit}")
                print(f"ship_to_guest: {ship_to_guest}")
                
                # Current logic evaluation
                if is_marketplace:
                    current_available = purchase_limit > 0
                    current_logic = f"Marketplace: purchase_limit > 0 = {current_available}"
                else:
                    current_available = ship_to_guest and purchase_limit >= 2
                    current_logic = f"Target: ship_to_guest AND purchase_limit >= 2 = {current_available}"
                
                print(f"Current logic result: {current_logic}")
                print(f"Would be marked as: {'IN STOCK' if current_available else 'OUT OF STOCK'}")
                
                # Also check additional fulfillment data
                shipping_eligibility = fulfillment.get('shipping_eligibility', {})
                print(f"Shipping eligibility: {shipping_eligibility}")
                
                store_eligibility = fulfillment.get('store_eligibility', {})
                print(f"Store eligibility: {store_eligibility}")
                
            else:
                print(f"Error: HTTP {response.status}")
                text_response = await response.text()
                print(f"Response text: {text_response[:200]}")

async def main():
    # Test the TCINs that are showing incorrectly as in stock
    # Plus add a known working TCIN for comparison
    test_tcins = [
        "13491099",  # Known working TCIN for testing
        "94724987",  # Test Product 1 - should be OUT OF STOCK
        "94681770",  # Test Product 3 - should be OUT OF STOCK  
        "89542109"   # Test Product 5 - should be IN STOCK
    ]
    
    for tcin in test_tcins:
        await test_stock_api(tcin)
        await asyncio.sleep(1)  # Rate limiting

if __name__ == "__main__":
    asyncio.run(main())
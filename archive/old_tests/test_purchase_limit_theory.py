#!/usr/bin/env python3
"""
Test the purchase_limit theory for stock detection
Theory: purchase_limit > 1 might indicate in-stock items
"""
import sys
import asyncio
import aiohttp
sys.path.insert(0, 'src')
from stock_checker import StockChecker

async def test_purchase_limit_theory():
    """Test if purchase_limit correlates with actual stock status"""
    
    # Test products with known stock status
    test_products = [
        ('89542109', 'KNOWN IN STOCK via website'),
        ('94724987', 'KNOWN OUT OF STOCK via website (had false positive)'),
        ('94681785', 'KNOWN OUT OF STOCK via website'),
        ('94681770', 'KNOWN OUT OF STOCK via website'),
        ('94336414', 'KNOWN OUT OF STOCK via website')
    ]
    
    print("TESTING PURCHASE_LIMIT THEORY FOR STOCK DETECTION")
    print("=" * 60)
    print("Theory: purchase_limit might indicate stock availability")
    print("Looking for patterns between purchase_limit and actual stock status")
    print("=" * 60)
    
    checker = StockChecker(use_website_checking=False)
    
    async with aiohttp.ClientSession() as session:
        for tcin, known_status in test_products:
            print(f"\n{tcin} - {known_status}")
            print("-" * 50)
            
            # Use the exact parameters from your curl example
            params = {
                'key': '9f36aeafbe60771e321a7cc95a78140772ab3e96',
                'tcin': tcin,
                'store_id': '2776',  # Using store_id from your example
                'pricing_store_id': '2776',
                'scheduled_delivery_store_id': '2776'  # New parameter from your curl
            }
            
            headers = {
                'accept': '*/*',
                'accept-language': 'en-US,en;q=0.9',
                'origin': 'https://www.target.com',
                'referer': f'https://www.target.com/p/-/A-{tcin}',
                'sec-ch-ua': '"Not;A=Brand";v="99", "Google Chrome";v="139", "Chromium";v="139"',
                'sec-ch-ua-mobile': '?0', 
                'sec-ch-ua-platform': '"Windows"',
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'same-site',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36'
            }
            
            try:
                async with session.get(
                    'https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1',
                    params=params,
                    headers=headers,
                    timeout=10
                ) as response:
                    
                    if response.status == 200:
                        data = await response.json()
                        item = data['data']['product']['item']
                        
                        # Extract all relevant fields for analysis
                        fulfillment = item.get('fulfillment', {})
                        purchase_limit = fulfillment.get('purchase_limit', 0)
                        
                        eligibility = item.get('eligibility_rules', {})
                        ship_to_guest = eligibility.get('ship_to_guest', {}).get('is_active', False) if eligibility else False
                        
                        # Check if there are NEW fields with this API call
                        print(f"   Purchase Limit: {purchase_limit}")
                        print(f"   Eligibility Rules: {len(eligibility)} rules")
                        print(f"   Ship to Guest: {ship_to_guest}")
                        
                        if eligibility:
                            print(f"   Eligibility Details:")
                            for rule, details in eligibility.items():
                                print(f"     {rule}: {details}")
                        
                        # Check for any new fields this API call might provide
                        fulfillment_keys = list(fulfillment.keys())
                        print(f"   All Fulfillment Keys: {fulfillment_keys}")
                        
                        # Analyze pattern
                        if purchase_limit >= 2:
                            print(f"   THEORY: purchase_limit={purchase_limit} >= 2 suggests IN STOCK")
                        else:
                            print(f"   THEORY: purchase_limit={purchase_limit} < 2 suggests OUT OF STOCK")
                            
                    else:
                        print(f"   API Error: {response.status}")
                        
            except Exception as e:
                print(f"   Exception: {e}")
    
    print(f"\n{'='*60}")
    print("PURCHASE_LIMIT THEORY ANALYSIS")
    print("=" * 60)
    print("If theory holds:")
    print("89542109 (known IN STOCK) should have purchase_limit >= 2")
    print("Others (known OUT OF STOCK) should have purchase_limit < 2 or other indicators")

if __name__ == "__main__":
    asyncio.run(test_purchase_limit_theory())
#!/usr/bin/env python3
"""
Deep analysis of API response structure for 89542109
to find alternative availability indicators
"""
import sys
import asyncio
import aiohttp
import json
sys.path.insert(0, 'src')
from stock_checker import StockChecker

async def deep_api_analysis():
    """Get full raw API response for analysis"""
    
    # Target the problematic product
    tcin = "89542109"
    
    checker = StockChecker(use_website_checking=False)
    
    # Monkey patch to capture raw response
    async def capture_raw_response(session, tcin_param):
        params = {
            'key': checker.api_key,
            'tcin': tcin_param,
            'store_id': checker.store_id,
            'pricing_store_id': checker.store_id,
            'has_pricing_store_id': 'true',
            'has_financing_options': 'true',
            'visitor_id': checker.generate_visitor_id(),
            'has_size_context': 'true'
        }
        
        headers = checker.get_headers()
        
        async with session.get(
            checker.base_url,
            params=params,
            headers=headers,
            timeout=10
        ) as response:
            if response.status == 200:
                data = await response.json()
                
                # Save raw response for manual inspection
                with open(f'raw_response_{tcin_param}.json', 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                
                print(f"Raw API response saved to: raw_response_{tcin_param}.json")
                
                # Extract ALL fields for analysis
                product = data['data']['product']
                item = product['item']
                
                print(f"\nFULL STRUCTURE ANALYSIS FOR {tcin_param}:")
                print("=" * 50)
                
                # Show top-level item keys
                print("ITEM KEYS:", list(item.keys()))
                
                # Analyze fulfillment in detail
                fulfillment = item.get('fulfillment', {})
                print(f"\nFULFILLMENT STRUCTURE:")
                for key, value in fulfillment.items():
                    print(f"  {key}: {value}")
                
                # Analyze eligibility rules in detail
                eligibility = item.get('eligibility_rules', {})
                print(f"\nELIGIBILITY RULES:")
                if eligibility:
                    for key, value in eligibility.items():
                        print(f"  {key}: {value}")
                else:
                    print("  (empty)")
                
                # Look for other potential availability indicators
                print(f"\nOTHER POTENTIAL INDICATORS:")
                
                # Check product availability fields
                if 'availability' in item:
                    print(f"  availability: {item['availability']}")
                
                # Check inventory fields
                if 'inventory' in item:
                    print(f"  inventory: {item['inventory']}")
                    
                # Check product state
                if 'product_state' in item:
                    print(f"  product_state: {item['product_state']}")
                
                # Check enrichment
                enrichment = item.get('enrichment', {})
                if enrichment:
                    print(f"  enrichment keys: {list(enrichment.keys())}")
                    
                    # Check buy_url which might indicate purchaseability
                    if 'buy_url' in enrichment:
                        print(f"  buy_url present: {bool(enrichment['buy_url'])}")
                
                # Check compliance for any purchase restrictions
                compliance = item.get('compliance', {})
                if compliance:
                    print(f"  compliance: {compliance}")
                    
                return data
            else:
                print(f"API request failed: {response.status}")
                return None
    
    async with aiohttp.ClientSession() as session:
        await capture_raw_response(session, tcin)

if __name__ == "__main__":
    asyncio.run(deep_api_analysis())
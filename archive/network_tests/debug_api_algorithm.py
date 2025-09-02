#!/usr/bin/env python3
"""
Deep dive into API stock detection algorithm
Analyze raw API responses to understand false positive
"""
import sys
import asyncio
import aiohttp
import json
sys.path.insert(0, 'src')
from stock_checker import StockChecker

async def debug_api_algorithm():
    products = [
        ('94724987', 'FALSE POSITIVE - API says available, website says OOS'),
        ('89542109', 'TRUE POSITIVE - Both say available'),
        ('94681785', 'TRUE NEGATIVE - Both say OOS'),
        ('94681770', 'Verify OOS'),
        ('94336414', 'Verify OOS')
    ]
    
    print('DEEP API ALGORITHM ANALYSIS')
    print('=' * 50)
    print('Goal: Identify why 94724987 shows false positive')
    print('=' * 50)
    
    # Create checker without website verification
    checker = StockChecker(use_website_checking=False)
    
    # Patch the parse function to show detailed analysis
    original_parse = checker.parse_availability
    
    def debug_parse(tcin, data):
        print(f'\nRAW API ANALYSIS FOR {tcin}:')
        try:
            product = data['data']['product']
            item = product['item']
            
            # Extract all relevant fields for analysis
            name = item.get('product_description', {}).get('title', 'Unknown')
            price = product.get('price', {}).get('current_retail', 0)
            
            fulfillment = item.get('fulfillment', {})
            is_marketplace = fulfillment.get('is_marketplace', False)
            purchase_limit = fulfillment.get('purchase_limit', 0)
            
            eligibility = item.get('eligibility_rules', {})
            ship_to_guest = eligibility.get('ship_to_guest', {}).get('is_active', False) if eligibility else False
            has_eligibility_rules = bool(eligibility)
            
            # Show detailed breakdown
            print(f'  Product: {name[:40]}...')
            print(f'  Price: ${price}')
            print(f'  Marketplace: {is_marketplace}')
            print(f'  Purchase Limit: {purchase_limit}')
            print(f'  Has Eligibility Rules: {has_eligibility_rules}')
            print(f'  Ship to Guest Active: {ship_to_guest}')
            
            # Show current algorithm decision
            if is_marketplace:
                available = purchase_limit > 0
                seller_type = "third-party"
                print(f'  Algorithm: Marketplace -> Available = {available} (purchase_limit > 0)')
            else:
                available = has_eligibility_rules and ship_to_guest and purchase_limit >= 1
                seller_type = "target"
                print(f'  Algorithm: Target Direct -> Available = {available}')
                print(f'      (has_eligibility_rules={has_eligibility_rules} AND ship_to_guest={ship_to_guest} AND purchase_limit>={purchase_limit})')
            
            # Special pre-order check
            if not ship_to_guest and purchase_limit == 1 and price >= 400:
                available = True
                seller_type = "target-preorder"
                print(f'  Special Case: Pre-order detected -> Available = True')
            
            print(f'  FINAL DECISION: {"AVAILABLE" if available else "NOT AVAILABLE"} ({seller_type})')
            
            # Show raw eligibility structure if it exists
            if eligibility:
                print(f'  Eligibility Rules Structure:')
                for key, value in eligibility.items():
                    print(f'      {key}: {value}')
            
        except Exception as e:
            print(f'  ❌ Parse Error: {e}')
        
        # Call original parse function
        return original_parse(tcin, data)
    
    checker.parse_availability = debug_parse
    
    async with aiohttp.ClientSession() as session:
        for tcin, description in products:
            print(f'\n{"="*60}')
            print(f'Testing {tcin}: {description}')
            print("="*60)
            
            result = await checker.check_stock(session, tcin)
            
            status = 'AVAILABLE' if result.get('available') else 'NOT AVAILABLE'
            print(f'\nFINAL API RESULT: {status}')

if __name__ == "__main__":
    asyncio.run(debug_api_algorithm())
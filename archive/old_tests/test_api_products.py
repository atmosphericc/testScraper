#!/usr/bin/env python3
"""
Test API calls for all 5 configured products
"""
import sys
import asyncio
import aiohttp
sys.path.insert(0, 'src')
from stock_checker import StockChecker

async def test_products():
    products = [
        ('94724987', 'Test Product 1'),
        ('94681785', 'Test Product 2'), 
        ('94681770', 'Test Product 3'),
        ('94336414', 'Test Product 4'),
        ('89542109', 'Test Product 5')
    ]
    
    print('Testing API calls for all 5 configured products...')
    print('=' * 60)
    
    # Test without website checking first (API only)
    checker = StockChecker(use_website_checking=False)
    
    async with aiohttp.ClientSession() as session:
        for tcin, name in products:
            print(f'\nTesting {tcin} ({name})')
            result = await checker.check_stock(session, tcin)
            
            status = 'Available' if result.get('available') else 'Not Available'
            print(f'  Status: {status}')
            print(f'  Name: {result.get("name", "N/A")}')
            print(f'  Price: ${result.get("price", 0)}')
            print(f'  Method: {result.get("status", "N/A")}')
            if result.get('error'):
                print(f'  Error: {result["error"]}')

if __name__ == "__main__":
    asyncio.run(test_products())
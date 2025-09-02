#!/usr/bin/env python3
"""
Test website-based checking for a few products
"""
import sys
import asyncio
sys.path.insert(0, 'src')
from authenticated_stock_checker import AuthenticatedStockChecker

async def test_website_checking():
    # Test just a couple products with website checking
    test_products = [
        ('89542109', 'Test Product 5 - Should be available'),
        ('94724987', 'Test Product 1 - Check website vs API')
    ]
    
    print('Testing website-based stock checking...')
    print('=' * 50)
    
    checker = AuthenticatedStockChecker()
    
    for tcin, description in test_products:
        print(f'\nTesting {tcin} ({description})')
        try:
            result = await checker.check_authenticated_stock(tcin)
            
            status = 'Available' if result.get('available') else 'Not Available'
            print(f'  Status: {status}')
            print(f'  Name: {result.get("name", "N/A")}')
            print(f'  Price: ${result.get("price", 0)}')
            print(f'  Method: {result.get("method", "N/A")}')
            print(f'  Details: {result.get("availability_text", "N/A")}')
            if result.get('error'):
                print(f'  Error: {result["error"]}')
        except Exception as e:
            print(f'  Error: {e}')

if __name__ == "__main__":
    asyncio.run(test_website_checking())
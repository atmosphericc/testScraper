#!/usr/bin/env python3
"""
Test hybrid stock checking system (website + API fallback)
"""
import sys
import asyncio
import aiohttp
sys.path.insert(0, 'src')
from stock_checker import StockChecker

async def test_hybrid_system():
    # Test with website checking enabled (hybrid mode)
    test_products = [
        ('89542109', 'Test Product 5'),
        ('94724987', 'Test Product 1'),
        ('94681770', 'Test Product 3')
    ]
    
    print('Testing HYBRID stock checking system (Website + API fallback)...')
    print('=' * 65)
    
    checker = StockChecker(use_website_checking=True)  # Enable website checking
    
    async with aiohttp.ClientSession() as session:
        for tcin, name in test_products:
            print(f'\nTesting {tcin} ({name})')
            result = await checker.check_stock(session, tcin)
            
            status = 'Available' if result.get('available') else 'Not Available'
            print(f'  Status: {status}')
            print(f'  Name: {result.get("name", "N/A")}')
            print(f'  Price: ${result.get("price", 0)}')
            print(f'  Method: {result.get("method", result.get("status", "N/A"))}')
            if result.get('availability_text'):
                print(f'  Details: {result["availability_text"]}')
            if result.get('error'):
                print(f'  Error: {result["error"]}')

if __name__ == "__main__":
    asyncio.run(test_hybrid_system())
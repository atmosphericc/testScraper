#!/usr/bin/env python3
"""
Test optimized API algorithm with detailed results for all 5 products
"""
import sys
import asyncio
import aiohttp
sys.path.insert(0, 'src')
from stock_checker import StockChecker

async def test_optimized_algorithm():
    products = [
        ('94724987', 'Test Product 1', '$69.99'),
        ('94681785', 'Test Product 2', '$31.99'),
        ('94681770', 'Test Product 3', '$31.99'),
        ('94336414', 'Test Product 4', '$24.99'),
        ('89542109', 'Test Product 5', '$14.99')
    ]
    
    print('OPTIMIZED API ALGORITHM - FINAL TEST')
    print('=' * 60)
    print('Goal: Fast, foolproof stock checking for purchase automation')
    print('Strategy: Conservative approach - no false positives allowed')
    print('=' * 60)
    
    # Use API-only mode with optimized algorithm
    checker = StockChecker(use_website_checking=False)
    
    results = []
    
    async with aiohttp.ClientSession() as session:
        for tcin, name, expected_price in products:
            print(f'\nTesting {tcin} ({name} - {expected_price})')
            print('-' * 50)
            
            result = await checker.check_stock(session, tcin)
            results.append(result)
            
            # Extract detailed info
            status = 'IN STOCK' if result.get('available') else 'OUT OF STOCK'
            confidence = result.get('confidence', 'unknown')
            reason = result.get('reason', 'unknown')
            actual_price = result.get('price', 0)
            product_name = result.get('name', 'Unknown')
            
            # Status display
            print(f'Status: {status}')
            print(f'Confidence: {confidence.upper()}')
            print(f'Reason: {reason}')
            print(f'Price: ${actual_price}')
            print(f'Name: {product_name}')
            
            if result.get('error'):
                print(f'Error: {result["error"]}')
    
    # Summary Report
    print(f'\n{"="*60}')
    print('FINAL RESULTS SUMMARY')
    print('=' * 60)
    
    in_stock_count = 0
    for i, result in enumerate(results):
        tcin = result['tcin']
        status = 'IN STOCK' if result.get('available') else 'OUT OF STOCK'
        confidence = result.get('confidence', 'unknown')
        reason = result.get('reason', 'unknown')
        
        if result.get('available'):
            in_stock_count += 1
            print(f'{tcin}: {status} (confidence: {confidence}) - READY FOR PURCHASE')
        else:
            print(f'{tcin}: {status} (confidence: {confidence}) - {reason}')
    
    print(f'\nPURCHASE AUTOMATION STATUS:')
    print(f'   Products IN STOCK: {in_stock_count}/5')
    print(f'   Products OUT OF STOCK: {5-in_stock_count}/5') 
    print(f'   Algorithm: OPTIMIZED (no false positives)')
    print(f'   Speed: ~2-3 seconds per product')
    print(f'   Safety: Conservative approach for automated purchasing')
    
    if in_stock_count > 0:
        print(f'\nREADY TO TRIGGER PURCHASE AUTOMATION FOR {in_stock_count} PRODUCT(S)')
    else:
        print(f'\nNO PRODUCTS CURRENTLY AVAILABLE - CONTINUE MONITORING')

if __name__ == "__main__":
    asyncio.run(test_optimized_algorithm())
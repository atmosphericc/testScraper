#!/usr/bin/env python3
"""
Test the website checker directly to see what's happening
"""
import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from website_stock_checker import AccurateStockChecker

async def test_direct():
    """Test the website checker directly"""
    
    checker = AccurateStockChecker()
    
    # Test all 5 products
    tcins = {
        '89542109': 'Should be IN STOCK',
        '94724987': 'Should be OUT OF STOCK', 
        '94681785': 'Should be OUT OF STOCK',
        '94681770': 'Should be OUT OF STOCK',
        '94336414': 'Should be OUT OF STOCK'
    }
    
    print("🔍 TESTING WEBSITE CHECKER DIRECTLY")
    print("=" * 60)
    
    for tcin, expected in tcins.items():
        print(f"\n📦 Testing {tcin} - {expected}")
        
        result = await checker.check_website_stock(tcin)
        
        status = "🟢 IN STOCK" if result.get('available') else "🔴 OUT OF STOCK"
        print(f"  Result: {status}")
        print(f"  Details: {result.get('availability_text', 'N/A')}")
        print(f"  Price: ${result.get('price', 0):.2f}")
        
        if result.get('error'):
            print(f"  Error: {result['error']}")
        
        # Validate
        if tcin == '89542109':
            if result.get('available'):
                print("  ✅ CORRECT")
            else:
                print("  ❌ WRONG - Should be IN STOCK")
        else:
            if not result.get('available'):
                print("  ✅ CORRECT")
            else:
                print("  ❌ WRONG - Should be OUT OF STOCK")
    
    await checker.cleanup()

if __name__ == "__main__":
    asyncio.run(test_direct())
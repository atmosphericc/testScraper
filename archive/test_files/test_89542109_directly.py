#!/usr/bin/env python3
"""
Test 89542109 directly to see why it's not being detected as in stock
"""
import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from website_stock_checker import AccurateStockChecker

async def test_89542109():
    """Test just the product that should be in stock"""
    
    print("🔍 TESTING 89542109 DIRECTLY")
    print("This should show as IN STOCK")
    print("=" * 50)
    
    checker = AccurateStockChecker()
    
    result = await checker.check_website_stock('89542109')
    
    print(f"Result: {result}")
    
    status = "🟢 IN STOCK" if result.get('available') else "🔴 OUT OF STOCK"
    print(f"\nStatus: {status}")
    print(f"Details: {result.get('availability_text', 'N/A')}")
    print(f"Method: {result.get('method', 'unknown')}")
    print(f"Price: ${result.get('price', 0):.2f}")
    
    if result.get('available'):
        print("\n✅ SUCCESS - Correctly detected as IN STOCK")
    else:
        print("\n❌ FAILED - Should be IN STOCK but detected as OUT")
        print("This means the detection logic needs further improvement")
    
    await checker.cleanup()

if __name__ == "__main__":
    asyncio.run(test_89542109())
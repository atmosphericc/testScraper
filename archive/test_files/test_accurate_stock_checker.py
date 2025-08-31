#!/usr/bin/env python3
"""
Test the updated stock checker with accurate website-based detection
"""
import asyncio
import aiohttp
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from stock_checker import StockChecker

async def test_accurate_checking():
    """Test accurate stock checking vs old API method"""
    
    tcins = ['94724987', '94681785', '94681770', '94336414', '89542109']
    
    print("🔍 TESTING ACCURATE STOCK CHECKING")
    print("=" * 70)
    
    # Test with website checking enabled (accurate)
    print("\n🌐 WEBSITE-BASED CHECKING (ACCURATE):")
    website_checker = StockChecker(use_website_checking=True)
    
    async with aiohttp.ClientSession() as session:
        for i, tcin in enumerate(tcins, 1):
            print(f"\n[{i}/5] Testing {tcin} with website checking...")
            result = await website_checker.check_stock(session, tcin)
            
            status = "🟢 IN STOCK" if result.get('available') else "🔴 OUT OF STOCK"
            method = result.get('method', 'api')
            
            print(f"  Result: {status} ({method})")
            print(f"  Name: {result.get('name', 'Unknown')}")
            print(f"  Price: ${result.get('price', 0):.2f}")
            print(f"  Details: {result.get('availability_text', 'N/A')}")
            
            if result.get('error'):
                print(f"  Error: {result['error']}")
    
    # Cleanup browser resources
    if website_checker.website_checker:
        await website_checker.website_checker.cleanup()
    
    print(f"\n{'='*70}")
    print("📊 COMPARISON WITH PREVIOUS API RESULTS:")
    print("Previous API (incorrect) showed:")
    print("  94724987: IN STOCK (wrong)")
    print("  94681785: IN STOCK (wrong)")  
    print("  89542109: OUT OF STOCK (wrong)")
    print("  Others: OUT OF STOCK (correct)")
    print("\nWebsite checking should show:")
    print("  89542109: IN STOCK ✓")
    print("  94336414: IN STOCK ✓")  
    print("  Others: OUT OF STOCK ✓")

if __name__ == "__main__":
    asyncio.run(test_accurate_checking())
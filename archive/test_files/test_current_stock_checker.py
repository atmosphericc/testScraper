#!/usr/bin/env python3
"""
Test the current stock checker to verify it's working correctly
"""
import asyncio
import aiohttp
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from stock_checker import StockChecker

async def test_stock_checker():
    """Test the current stock checker with all configurations"""
    
    # All 5 products from config
    tcins = ['94724987', '94681785', '94681770', '94336414', '89542109']
    
    print("🔍 TESTING CURRENT STOCK CHECKER")
    print("=" * 80)
    print("Expected results based on your verification:")
    print("  89542109: Should show IN STOCK ✅")
    print("  All others: Should show OUT OF STOCK ❌")
    print("=" * 80)
    
    # Test 1: Website-based checking (should be accurate)
    print("\n🌐 TEST 1: Website-Based Stock Checking (Accurate)")
    print("-" * 60)
    
    website_checker = StockChecker(use_website_checking=True)
    
    async with aiohttp.ClientSession() as session:
        for i, tcin in enumerate(tcins, 1):
            print(f"\n[{i}/5] Testing {tcin}...")
            
            try:
                result = await website_checker.check_stock(session, tcin)
                
                # Display results
                status = "🟢 IN STOCK" if result.get('available') else "🔴 OUT OF STOCK"
                method = result.get('method', 'unknown')
                
                print(f"  Status: {status}")
                print(f"  Method: {method}")
                print(f"  Name: {result.get('name', 'Unknown')[:50]}")
                print(f"  Price: ${result.get('price', 0):.2f}")
                
                if result.get('availability_text'):
                    print(f"  Details: {result['availability_text']}")
                
                if result.get('error'):
                    print(f"  Error: {result['error']}")
                
                # Validation
                if tcin == '89542109':
                    if result.get('available'):
                        print("  ✅ CORRECT - Should be IN STOCK")
                    else:
                        print("  ❌ WRONG - Should be IN STOCK but shows OUT")
                else:
                    if not result.get('available'):
                        print("  ✅ CORRECT - Should be OUT OF STOCK")
                    else:
                        print("  ❌ WRONG - Should be OUT OF STOCK but shows IN")
                
            except Exception as e:
                print(f"  ❌ ERROR: {e}")
    
    # Cleanup website checker
    if website_checker.website_checker:
        await website_checker.website_checker.cleanup()
    
    # Test 2: API-based checking (for comparison)
    print(f"\n{'='*80}")
    print("🔥 TEST 2: API-Based Stock Checking (Fast but Less Accurate)")
    print("-" * 60)
    
    api_checker = StockChecker(use_website_checking=False)
    
    async with aiohttp.ClientSession() as session:
        for i, tcin in enumerate(tcins, 1):
            print(f"\n[{i}/5] Testing {tcin} with API...")
            
            try:
                result = await api_checker.check_stock(session, tcin)
                
                status = "🟢 IN STOCK" if result.get('available') else "🔴 OUT OF STOCK"
                method = result.get('method', 'api')
                
                print(f"  Status: {status}")
                print(f"  Method: {method}")
                print(f"  Name: {result.get('name', 'Unknown')[:50]}")
                print(f"  Price: ${result.get('price', 0):.2f}")
                
                # Show why API made this decision
                if result.get('seller_type'):
                    print(f"  Seller: {result['seller_type']}")
                if result.get('purchase_limit'):
                    print(f"  Purchase limit: {result['purchase_limit']}")
                
            except Exception as e:
                print(f"  ❌ ERROR: {e}")
    
    print(f"\n{'='*80}")
    print("📊 SUMMARY")
    print("=" * 80)
    print("The tests above show:")
    print("1. Website checking accuracy vs speed")
    print("2. API checking speed vs accuracy")
    print("3. Which method correctly identifies 89542109 as IN STOCK")

if __name__ == "__main__":
    asyncio.run(test_stock_checker())
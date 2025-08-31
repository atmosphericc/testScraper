#!/usr/bin/env python3
"""
Test just 89542109 with detailed logging to see button state
"""
import asyncio
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

# Set up logging to see debug messages
logging.basicConfig(level=logging.DEBUG)

from authenticated_stock_checker import AuthenticatedStockChecker

async def test_single_89542109():
    """Test just 89542109 with debug logging"""
    
    checker = AuthenticatedStockChecker()
    
    print("🔍 TESTING SINGLE PRODUCT 89542109 WITH DEBUG LOGGING")
    print("=" * 60)
    
    result = await checker.check_authenticated_stock('89542109')
    
    print(f"\nResult: {result}")
    
    is_available = result.get('available', False)
    status = "🟢 IN STOCK" if is_available else "🔴 OUT OF STOCK"
    
    print(f"\nFinal Status: {status}")
    print(f"Details: {result.get('availability_text', 'N/A')}")
    print(f"Expected: 🟢 IN STOCK")
    
    if is_available:
        print("✅ CORRECT - This product should be available")
    else:
        print("❌ WRONG - This product should be available but shows as out of stock")

if __name__ == "__main__":
    asyncio.run(test_single_89542109())
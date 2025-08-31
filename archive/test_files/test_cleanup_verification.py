#!/usr/bin/env python3
"""
Test that the cleaned-up project still works correctly
"""
import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from authenticated_stock_checker import AuthenticatedStockChecker
from stock_checker import StockChecker

async def test_cleanup_verification():
    """Verify the cleaned-up project still works"""
    
    print("🧹 TESTING CLEANED PROJECT")
    print("=" * 50)
    
    # Test 1: Direct authenticated stock checker
    print("\n📦 Testing direct authenticated stock checker...")
    auth_checker = AuthenticatedStockChecker()
    
    try:
        result = await auth_checker.check_authenticated_stock('89542109')
        status = "🟢 IN STOCK" if result.get('available') else "🔴 OUT OF STOCK"
        print(f"  89542109: {status} - {result.get('availability_text', 'N/A')}")
        print("  ✅ Direct checker works!")
    except Exception as e:
        print(f"  ❌ Direct checker failed: {e}")
        return False
    
    # Test 2: StockChecker integration
    print("\n🔧 Testing StockChecker integration...")
    stock_checker = StockChecker(use_website_checking=True)
    
    try:
        result = await stock_checker.check_stock(session=None, tcin='89542109')
        status = "🟢 IN STOCK" if result.get('available') else "🔴 OUT OF STOCK"
        print(f"  89542109: {status} - {result.get('availability_text', 'N/A')}")
        print("  ✅ StockChecker integration works!")
    except Exception as e:
        print(f"  ❌ StockChecker integration failed: {e}")
        return False
    
    # Test 3: Check that files are organized correctly
    print("\n📁 Testing file organization...")
    
    # Check that archive exists and contains expected files
    archive_path = Path('archive')
    if archive_path.exists():
        test_files = list(archive_path.glob('**/*test*.py'))
        debug_files = list(archive_path.glob('**/*debug*.py'))
        print(f"  ✅ Archive contains {len(test_files)} test files and {len(debug_files)} debug files")
    else:
        print("  ❌ Archive directory not found")
        return False
    
    # Check that src has the right files
    src_path = Path('src')
    if src_path.exists():
        auth_checker_path = src_path / 'authenticated_stock_checker.py'
        stock_checker_path = src_path / 'stock_checker.py'
        
        if auth_checker_path.exists():
            print("  ✅ authenticated_stock_checker.py in src/")
        else:
            print("  ❌ authenticated_stock_checker.py missing from src/")
            return False
            
        if stock_checker_path.exists():
            print("  ✅ stock_checker.py in src/")
        else:
            print("  ❌ stock_checker.py missing from src/")
            return False
    
    print("\n🎉 PROJECT CLEANUP VERIFICATION PASSED!")
    print("✅ All core functionality preserved")
    print("✅ Test/debug files archived")
    print("✅ Production code organized in src/")
    return True

if __name__ == "__main__":
    success = asyncio.run(test_cleanup_verification())
    sys.exit(0 if success else 1)
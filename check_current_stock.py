#!/usr/bin/env python3
"""
Check current stock status for all 8 products using the fixed API
"""

import sys
import os
import json

# Add the current directory to Python path
sys.path.insert(0, os.getcwd())

def check_all_products():
    """Check stock status for all 8 products"""
    try:
        from main_dashboard import UltimateStealthBatchChecker
        
        print("="*80)
        print("CURRENT STOCK STATUS FOR ALL 8 PRODUCTS")
        print("="*80)
        
        # Initialize the checker
        checker = UltimateStealthBatchChecker()
        
        # Make the API call
        print("[API] Making batch API call to Target RedSky...")
        result = checker.make_ultimate_stealth_batch_call()
        
        if result:
            print(f"[SUCCESS] Retrieved data for {len(result)} products\n")
            
            # Show detailed results
            for i, (tcin, product) in enumerate(result.items(), 1):
                print(f"PRODUCT {i}: {tcin}")
                print(f"  Name: {product.get('name', 'Unknown')}")
                print(f"  Available: {'✅ IN STOCK' if product.get('available') else '❌ OUT OF STOCK'}")
                print(f"  Status: {product.get('status', 'Unknown')}")
                print(f"  Is Preorder: {'Yes' if product.get('is_preorder') else 'No'}")
                if product.get('is_preorder'):
                    print(f"  Preorder Status: {product.get('preorder_status', 'Unknown')}")
                print(f"  Target Direct: {'Yes' if product.get('is_target_direct') else 'No (Marketplace)'}")
                print(f"  URL: {product.get('url', 'N/A')}")
                print()
            
            # Summary
            in_stock_count = sum(1 for p in result.values() if p.get('available'))
            out_of_stock_count = len(result) - in_stock_count
            
            print("="*80)
            print("SUMMARY")
            print("="*80)
            print(f"✅ IN STOCK: {in_stock_count} products")
            print(f"❌ OUT OF STOCK: {out_of_stock_count} products")
            print(f"📦 TOTAL MONITORED: {len(result)} products")
            
            if in_stock_count > 0:
                print(f"\n🎉 {in_stock_count} PRODUCT(S) CURRENTLY AVAILABLE FOR PURCHASE!")
                in_stock_products = [p for p in result.values() if p.get('available')]
                for product in in_stock_products:
                    print(f"   • {product.get('name', 'Unknown')} (TCIN: {product.get('tcin')})")
            else:
                print("\n😔 No products currently in stock")
            
            return result
        else:
            print("[ERROR] No data returned from API")
            return {}
            
    except Exception as e:
        print(f"[ERROR] Failed to check stock: {e}")
        import traceback
        traceback.print_exc()
        return {}

if __name__ == "__main__":
    check_all_products()
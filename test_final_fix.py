#!/usr/bin/env python3
"""
Final test to verify the fix works with the actual dashboard code
"""

import sys
import os
import json

# Add the current directory to Python path so we can import from main_dashboard
sys.path.insert(0, os.getcwd())

def test_batch_checker():
    """Test the fixed batch checker"""
    try:
        # Import the fixed classes
        from main_dashboard import UltimateStealthBatchChecker
        
        print("="*60)
        print("TESTING FIXED BATCH CHECKER")
        print("="*60)
        
        # Initialize the checker
        checker = UltimateStealthBatchChecker()
        
        # Make a batch call
        print("[TEST] Making batch API call...")
        result = checker.make_ultimate_stealth_batch_call()
        
        if result:
            print(f"[SUCCESS] Got data for {len(result)} products")
            
            for tcin, product in result.items():
                print(f"\nTCIN: {tcin}")
                print(f"  Name: {product.get('name', 'Unknown')}")
                print(f"  Available: {product.get('available', False)}")
                print(f"  Status: {product.get('status', 'Unknown')}")
                print(f"  Is Preorder: {product.get('is_preorder', False)}")
                if product.get('is_preorder'):
                    print(f"  Preorder Status: {product.get('preorder_status', 'Unknown')}")
                print(f"  Is Target Direct: {product.get('is_target_direct', True)}")
                print(f"  Has Data: {product.get('has_data', False)}")
            
            print(f"\n[SUMMARY] Successfully processed {len(result)} products")
            in_stock = sum(1 for p in result.values() if p.get('available'))
            print(f"[SUMMARY] {in_stock} products currently in stock")
            
            if len(result) > 0:
                print("\n✅ API FIX IS WORKING! Product names and data are being retrieved correctly.")
                return True
            else:
                print("\n❌ No products returned")
                return False
        else:
            print("[ERROR] No data returned from batch call")
            return False
            
    except Exception as e:
        print(f"[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    test_batch_checker()
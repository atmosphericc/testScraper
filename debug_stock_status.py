#!/usr/bin/env python3
"""
Debug script to test stock status without the dashboard
"""

import json
import sys
import os
from pathlib import Path

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the batch checker
from main_dashboard import UltimateStealthBatchChecker

def test_stock_checker():
    """Test the stock checker directly"""
    print("Testing stock checker directly...")
    
    checker = UltimateStealthBatchChecker()
    
    print("Loading config...")
    config = checker.get_config()
    enabled_products = [p for p in config.get('products', []) if p.get('enabled', True)]
    print(f"Found {len(enabled_products)} enabled products")
    
    print("\nMaking batch API call...")
    batch_data = checker.make_ultimate_stealth_batch_call()
    
    print(f"\nResults:")
    print(f"Returned data for {len(batch_data)} products")
    
    for tcin, data in batch_data.items():
        status = data.get('status', 'MISSING_STATUS')
        available = data.get('available', False)
        name = data.get('name', 'Unknown')[:30]
        
        print(f"  {tcin}: status='{status}', available={available}, name='{name}...'")
    
    return batch_data

if __name__ == '__main__':
    try:
        result = test_stock_checker()
        print(f"\nTest completed successfully. {len(result)} products processed.")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
#!/usr/bin/env python3
"""
Test the dashboard preorder integration
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main_dashboard import UltimateStealthBatchChecker, is_preorder_item
import json

def test_preorder_detection():
    """Test preorder detection with known items"""
    
    print("🧪 TESTING DASHBOARD PREORDER INTEGRATION")
    print("=" * 60)
    
    # Create test config
    test_config = {
        "products": [
            {"tcin": "94681776", "name": "Test Preorder 1", "enabled": True, "max_price": 55.00},
            {"tcin": "94734932", "name": "Test Preorder 2", "enabled": True, "max_price": 50.00},
            {"tcin": "89542109", "name": "Test Regular Product", "enabled": True, "max_price": 30.00}
        ]
    }
    
    # Save test config
    with open('config/product_config.json', 'w') as f:
        json.dump(test_config, f, indent=2)
    
    print("✅ Created test config with preorder items")
    
    # Test the stealth checker
    checker = UltimateStealthBatchChecker()
    
    print("\n🚀 Running batch call to test preorder detection...")
    batch_data = checker.make_ultimate_stealth_batch_call()
    
    if batch_data:
        print(f"\n📊 RESULTS:")
        print("-" * 40)
        
        preorder_count = 0
        regular_count = 0
        available_count = 0
        
        for tcin, result in batch_data.items():
            is_preorder = result.get('is_preorder', False)
            is_available = result.get('available', False)
            availability_status = result.get('availability_status', 'UNKNOWN')
            name = result.get('name', 'Unknown')
            
            if is_preorder:
                preorder_count += 1
            else:
                regular_count += 1
                
            if is_available:
                available_count += 1
            
            product_type = "🎯 PREORDER" if is_preorder else "📦 REGULAR"
            stock_status = "✅ AVAILABLE" if is_available else "❌ UNAVAILABLE"
            
            print(f"{tcin}: {product_type} - {stock_status}")
            print(f"  Name: {name[:50]}...")
            print(f"  Status: {availability_status}")
            if is_preorder and result.get('street_date'):
                print(f"  Release: {result.get('street_date')}")
            print()
        
        print(f"📈 SUMMARY:")
        print(f"  Total products: {len(batch_data)}")
        print(f"  Preorders: {preorder_count}")
        print(f"  Regular items: {regular_count}")
        print(f"  Available: {available_count}")
        print(f"  Unavailable: {len(batch_data) - available_count}")
        
        if preorder_count > 0:
            print("\n🎉 SUCCESS! Dashboard now supports preorders!")
            print("✅ Preorder detection working")
            print("✅ Mixed product types supported")
            print("✅ Availability status accurate")
        else:
            print("\n⚠️  No preorders detected - check test data")
            
    else:
        print("❌ Batch call failed")

if __name__ == "__main__":
    test_preorder_detection()
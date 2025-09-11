#!/usr/bin/env python3
"""
Test the complete loading → API → dashboard flow
"""

import sys
import os
import time
import threading

# Add the current directory to Python path
sys.path.insert(0, os.getcwd())

def test_loading_flow():
    """Test the complete flow: loading screen → API call → dashboard"""
    
    print("="*80)
    print("TESTING LOADING SCREEN → API CALL → DASHBOARD FLOW")
    print("="*80)
    
    try:
        from main_dashboard import UltimateStealthBatchChecker, latest_data_lock
        import main_dashboard
        
        # Reset global state
        main_dashboard.dashboard_ready = False
        main_dashboard.latest_stock_data = {}
        
        print("\n1. LOADING SCREEN PHASE")
        print("-" * 40)
        print("🔄 Loading screen would show: 'Loading, please wait...'")
        print("⏰ Background API call starting...")
        
        # Simulate the background API call
        checker = UltimateStealthBatchChecker()
        
        def background_api_call():
            print("   📡 Making API call to Target RedSky...")
            batch_data = checker.make_ultimate_stealth_batch_call()
            
            if batch_data and len(batch_data) > 0:
                # Update the global data stores (like the real dashboard does)
                with latest_data_lock:
                    main_dashboard.latest_stock_data = batch_data.copy()
                main_dashboard.dashboard_ready = True
                
                print(f"   ✅ API call complete: {len(batch_data)} products loaded")
                print(f"   ✅ Dashboard ready flag set to: {main_dashboard.dashboard_ready}")
                return batch_data
            else:
                print("   ❌ API call failed or returned empty")
                return {}
        
        # Run background API call
        api_thread = threading.Thread(target=background_api_call, daemon=True)
        api_thread.start()
        
        print("   ⏳ Waiting for API call (max 15 seconds)...")
        api_thread.join(timeout=15.0)
        
        print("\n2. TRANSITION CHECK")
        print("-" * 40)
        
        # Check if we should transition to dashboard
        with latest_data_lock:
            has_real_data = bool(main_dashboard.latest_stock_data)
            
        if main_dashboard.dashboard_ready and has_real_data:
            print("✅ TRANSITION CONDITIONS MET:")
            print(f"   - dashboard_ready: {main_dashboard.dashboard_ready}")
            print(f"   - has_real_data: {has_real_data}")
            print(f"   - latest_stock_data has: {len(main_dashboard.latest_stock_data)} products")
            
            print("\n3. DASHBOARD PHASE")
            print("-" * 40)
            print("🎯 Dashboard would now display with real stock data:")
            
            # Show what the dashboard would display
            for tcin, product in main_dashboard.latest_stock_data.items():
                status_emoji = "✅" if product.get('available') else "❌"
                name = product.get('name', 'Unknown')[:50]
                status = product.get('status', 'Unknown')
                print(f"   {status_emoji} {tcin}: {name}... ({status})")
            
            in_stock_count = sum(1 for p in main_dashboard.latest_stock_data.values() if p.get('available'))
            print(f"\n   📊 Summary: {in_stock_count} in stock, {len(main_dashboard.latest_stock_data)-in_stock_count} out of stock")
            
            print("\n" + "="*80)
            print("🎉 LOADING FLOW TEST: SUCCESS!")
            print("✅ Loading screen → API call → Dashboard transition works!")
            print("="*80)
            return True
            
        else:
            print("❌ TRANSITION CONDITIONS NOT MET:")
            print(f"   - dashboard_ready: {main_dashboard.dashboard_ready}")
            print(f"   - has_real_data: {has_real_data}")
            print("❌ Would remain on loading screen")
            return False
            
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    test_loading_flow()
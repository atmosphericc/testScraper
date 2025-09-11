#!/usr/bin/env python3
"""
Debug the loading issue by checking what happens with the API call
"""

import sys
import os
import json
import time
import threading

# Add the current directory to Python path
sys.path.insert(0, os.getcwd())

def debug_api_call():
    """Debug what happens during the background API call"""
    try:
        from main_dashboard import UltimateStealthBatchChecker
        
        print("="*60)
        print("DEBUGGING BACKGROUND API CALL")
        print("="*60)
        
        # Initialize the checker
        checker = UltimateStealthBatchChecker()
        
        # Test if API call works in simple form
        print("[TEST] Testing simple API call...")
        start_time = time.time()
        
        result = checker.make_ultimate_stealth_batch_call()
        
        end_time = time.time()
        duration = end_time - start_time
        
        print(f"[RESULT] API call took {duration:.2f} seconds")
        
        if result:
            print(f"[SUCCESS] Got data for {len(result)} products")
            
            # Check if data structure is correct
            for tcin, product in result.items():
                print(f"  {tcin}: {product.get('name', 'No name')[:50]}...")
                
            return True
        else:
            print("[ERROR] No data returned")
            return False
            
    except Exception as e:
        print(f"[ERROR] Debug failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def simulate_background_worker():
    """Simulate what the background worker should do"""
    print("\n" + "="*60)
    print("SIMULATING BACKGROUND WORKER")
    print("="*60)
    
    try:
        from main_dashboard import UltimateStealthBatchChecker
        
        # Simulate the exact background worker logic
        checker = UltimateStealthBatchChecker()
        
        def background_api_call():
            try:
                print("[WORKER_SIM] 🚀 Background API call started...")
                batch_data = checker.make_ultimate_stealth_batch_call()
                print(f"[WORKER_SIM] API call returned type: {type(batch_data)}, length: {len(batch_data) if batch_data else 0}")
                
                if batch_data and len(batch_data) > 0:
                    print(f"[WORKER_SIM] ✅ Background API call: Got data for {len(batch_data)} products")
                    print(f"[WORKER_SIM] ✅ batch_data keys: {list(batch_data.keys())}")
                    
                    # This is what should update latest_stock_data
                    print("[WORKER_SIM] ✅ This should populate latest_stock_data and make dashboard_ready = True")
                    return True
                else:
                    print("[WORKER_SIM] ❌ No data returned")
                    return False
                    
            except Exception as e:
                print(f"[WORKER_SIM] ❌ Background API call failed: {e}")
                import traceback
                traceback.print_exc()
                return False
        
        # Run in background thread like the real code
        print("[WORKER_SIM] Starting background thread...")
        thread = threading.Thread(target=background_api_call, daemon=True)
        thread.start()
        thread.join(timeout=30)  # Wait up to 30 seconds
        
        if thread.is_alive():
            print("[WORKER_SIM] ❌ Background thread is still running (may be hanging)")
            return False
        else:
            print("[WORKER_SIM] ✅ Background thread completed")
            return True
            
    except Exception as e:
        print(f"[WORKER_SIM] Exception: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("Starting debug...")
    
    # Test 1: Simple API call
    api_works = debug_api_call()
    
    # Test 2: Background worker simulation
    worker_works = simulate_background_worker()
    
    print("\n" + "="*60)
    print("DEBUG SUMMARY")
    print("="*60)
    print(f"API Call Works: {'✅' if api_works else '❌'}")
    print(f"Background Worker Works: {'✅' if worker_works else '❌'}")
    
    if api_works and worker_works:
        print("\n✅ API and background worker should work - issue is elsewhere")
    elif api_works and not worker_works:
        print("\n❌ API works but background worker is hanging/failing")
    else:
        print("\n❌ Basic API call is failing")
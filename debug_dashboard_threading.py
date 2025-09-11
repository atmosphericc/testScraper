#!/usr/bin/env python3
"""
Debug why API call hangs in dashboard but works standalone
"""

import sys
import os
import time
import threading
import flask
from flask_socketio import SocketIO

# Add the current directory to Python path
sys.path.insert(0, os.getcwd())

def test_1_standalone():
    """Test 1: Standalone API call (we know this works)"""
    print("="*60)
    print("TEST 1: STANDALONE API CALL")
    print("="*60)
    
    try:
        from main_dashboard import UltimateStealthBatchChecker
        checker = UltimateStealthBatchChecker()
        
        start_time = time.time()
        result = checker.make_ultimate_stealth_batch_call()
        end_time = time.time()
        
        print(f"✅ Standalone: {len(result) if result else 0} products in {end_time-start_time:.1f}s")
        return True
    except Exception as e:
        print(f"❌ Standalone failed: {e}")
        return False

def test_2_threading_only():
    """Test 2: Same API call but in a thread (like dashboard does)"""
    print("="*60)
    print("TEST 2: API CALL IN THREAD")
    print("="*60)
    
    try:
        from main_dashboard import UltimateStealthBatchChecker
        checker = UltimateStealthBatchChecker()
        
        result_holder = {}
        
        def threaded_api_call():
            try:
                print("[THREAD] Starting API call...")
                start_time = time.time()
                result = checker.make_ultimate_stealth_batch_call()
                end_time = time.time()
                result_holder['result'] = result
                result_holder['time'] = end_time - start_time
                print(f"[THREAD] ✅ Completed: {len(result) if result else 0} products in {end_time-start_time:.1f}s")
            except Exception as e:
                print(f"[THREAD] ❌ Failed: {e}")
                result_holder['error'] = str(e)
        
        thread = threading.Thread(target=threaded_api_call, daemon=True)
        thread.start()
        
        print("Waiting for thread to complete (max 20s)...")
        thread.join(timeout=20.0)
        
        if thread.is_alive():
            print("❌ Thread is still running - HANGING!")
            return False
        elif 'result' in result_holder:
            print(f"✅ Threaded: {len(result_holder['result'])} products in {result_holder['time']:.1f}s")
            return True
        else:
            print(f"❌ Threaded failed: {result_holder.get('error', 'Unknown error')}")
            return False
            
    except Exception as e:
        print(f"❌ Threading test failed: {e}")
        return False

def test_3_flask_context():
    """Test 3: API call in Flask app context (like real dashboard)"""
    print("="*60)
    print("TEST 3: API CALL IN FLASK CONTEXT")
    print("="*60)
    
    try:
        from main_dashboard import UltimateStealthBatchChecker
        
        # Create minimal Flask app like dashboard
        app = flask.Flask(__name__)
        app.config['SECRET_KEY'] = 'test'
        socketio = SocketIO(app, cors_allowed_origins="*")
        
        result_holder = {}
        
        def flask_api_call():
            try:
                with app.app_context():
                    print("[FLASK] Starting API call in Flask context...")
                    checker = UltimateStealthBatchChecker()
                    start_time = time.time()
                    result = checker.make_ultimate_stealth_batch_call()
                    end_time = time.time()
                    result_holder['result'] = result
                    result_holder['time'] = end_time - start_time
                    print(f"[FLASK] ✅ Completed: {len(result) if result else 0} products in {end_time-start_time:.1f}s")
            except Exception as e:
                print(f"[FLASK] ❌ Failed: {e}")
                result_holder['error'] = str(e)
        
        thread = threading.Thread(target=flask_api_call, daemon=True)
        thread.start()
        
        print("Waiting for Flask thread to complete (max 20s)...")
        thread.join(timeout=20.0)
        
        if thread.is_alive():
            print("❌ Flask thread is still running - HANGING!")
            return False
        elif 'result' in result_holder:
            print(f"✅ Flask context: {len(result_holder['result'])} products in {result_holder['time']:.1f}s")
            return True
        else:
            print(f"❌ Flask context failed: {result_holder.get('error', 'Unknown error')}")
            return False
            
    except Exception as e:
        print(f"❌ Flask context test failed: {e}")
        return False

def test_4_exact_dashboard_setup():
    """Test 4: Replicate exact dashboard worker setup"""
    print("="*60)
    print("TEST 4: EXACT DASHBOARD WORKER SETUP")
    print("="*60)
    
    try:
        # Import everything like the real dashboard
        from main_dashboard import UltimateStealthBatchChecker, latest_data_lock
        import main_dashboard
        
        # Reset state like dashboard does
        main_dashboard.dashboard_ready = False
        main_dashboard.latest_stock_data = {}
        
        checker = UltimateStealthBatchChecker()
        result_holder = {}
        
        def background_api_call():
            """Exact copy of dashboard background_api_call function"""
            global dashboard_ready, current_stock_data, latest_stock_data
            try:
                print("[STOCK_WORKER] 🚀 Background API call started...")
                batch_data = checker.make_ultimate_stealth_batch_call()
                print(f"[STOCK_WORKER] API call returned type: {type(batch_data)}, length: {len(batch_data) if batch_data else 0}")
                
                if batch_data and len(batch_data) > 0:
                    # Update both global data stores
                    main_dashboard.current_stock_data = batch_data
                    with latest_data_lock:
                        main_dashboard.latest_stock_data = batch_data.copy()
                    
                    # Set dashboard ready AFTER data is set
                    main_dashboard.dashboard_ready = True
                    
                    print(f"[STOCK_WORKER] ✅ Background API call: Got data for {len(batch_data)} products")
                    print(f"[STOCK_WORKER] ✅ Dashboard is now ready with real data!")
                    result_holder['success'] = True
                    result_holder['count'] = len(batch_data)
                else:
                    print("[STOCK_WORKER] ⚠️ API call returned empty data")
                    main_dashboard.dashboard_ready = True
                    print("[STOCK_WORKER] ✅ Dashboard ready (with empty data)")
                    result_holder['success'] = False
                    
            except Exception as e:
                print(f"[STOCK_WORKER] ❌ Background API call failed: {str(e)}")
                main_dashboard.dashboard_ready = True
                print("[STOCK_WORKER] ✅ Dashboard ready (after error)")
                result_holder['error'] = str(e)
                
            print("[STOCK_WORKER] 🏁 Background API call thread completed")
        
        # Start background API call exactly like dashboard
        api_thread = threading.Thread(target=background_api_call, daemon=True)
        api_thread.start()
        print("[STOCK_WORKER] 🚀 Background API thread started")
        
        # Wait for API call to complete (with timeout) exactly like dashboard
        print("[STOCK_WORKER] ⏰ Waiting up to 15 seconds for API call to complete...")
        api_thread.join(timeout=15.0)
        
        if api_thread.is_alive():
            print("[STOCK_WORKER] ⚠️ API call taking longer than 15 seconds - enabling dashboard anyway")
            main_dashboard.dashboard_ready = True
            print("❌ EXACT DASHBOARD SETUP: HANGING!")
            return False
        else:
            print("[STOCK_WORKER] ✅ API call completed within 15 seconds!")
            if result_holder.get('success'):
                print(f"✅ Exact dashboard setup: {result_holder['count']} products")
                return True
            else:
                print(f"❌ Exact dashboard setup failed: {result_holder.get('error', 'No data')}")
                return False
        
    except Exception as e:
        print(f"❌ Exact dashboard setup test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all tests to identify where the hanging occurs"""
    print("DEBUGGING: API HANGING IN DASHBOARD")
    print("Finding where standalone (works) differs from dashboard (hangs)")
    
    tests = [
        ("Standalone API", test_1_standalone),
        ("Threaded API", test_2_threading_only), 
        ("Flask Context API", test_3_flask_context),
        ("Exact Dashboard Setup", test_4_exact_dashboard_setup)
    ]
    
    results = {}
    
    for test_name, test_func in tests:
        print(f"\n{'='*80}")
        print(f"RUNNING: {test_name}")
        print(f"{'='*80}")
        
        try:
            success = test_func()
            results[test_name] = "✅ PASS" if success else "❌ FAIL (HANGING)"
        except Exception as e:
            results[test_name] = f"❌ ERROR: {e}"
            
        time.sleep(1)  # Small delay between tests
    
    print(f"\n{'='*80}")
    print("DEBUGGING RESULTS")
    print(f"{'='*80}")
    
    for test_name, result in results.items():
        print(f"{test_name}: {result}")
    
    # Identify the issue
    if results["Standalone API"].startswith("✅") and results["Threaded API"].startswith("❌"):
        print("\n🔍 ISSUE IDENTIFIED: Threading problem")
    elif results["Threaded API"].startswith("✅") and results["Flask Context API"].startswith("❌"):
        print("\n🔍 ISSUE IDENTIFIED: Flask context problem")
    elif results["Flask Context API"].startswith("✅") and results["Exact Dashboard Setup"].startswith("❌"):
        print("\n🔍 ISSUE IDENTIFIED: Dashboard-specific problem")
    else:
        print("\n🔍 Need more investigation...")

if __name__ == "__main__":
    main()
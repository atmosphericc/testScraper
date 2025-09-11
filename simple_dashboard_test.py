#!/usr/bin/env python3
"""
Create a simplified dashboard startup that works
"""

import sys
import os
import time
import threading
from flask import Flask, render_template, redirect
from flask_socketio import SocketIO

# Add the current directory to Python path
sys.path.insert(0, os.getcwd())

def create_simple_dashboard():
    """Create a working dashboard with just the essentials"""
    
    from main_dashboard import UltimateStealthBatchChecker
    
    # Global state
    dashboard_ready = False
    latest_stock_data = {}
    data_lock = threading.Lock()
    
    # Initialize Flask app
    app = Flask(__name__, template_folder='dashboard/templates', static_folder='dashboard/static')
    app.config['SECRET_KEY'] = 'ultra_stealth_dashboard_2024'
    socketio = SocketIO(app, cors_allowed_origins="*")
    
    @app.route('/')
    def index():
        """Main dashboard route"""
        nonlocal dashboard_ready, latest_stock_data
        
        # Check if we should show dashboard or loading screen
        with data_lock:
            has_real_data = bool(latest_stock_data)
        
        if not dashboard_ready or not has_real_data:
            print(f"[ROUTE] Loading screen: dashboard_ready={dashboard_ready}, has_data={has_real_data}")
            return render_template('loading.html', 
                message="🔥 Loading stock data... Please wait...", 
                current_step=2)
        
        print(f"[ROUTE] Dashboard ready: {len(latest_stock_data)} products")
        
        # Show dashboard with real data
        try:
            import json
            with open('config/product_config.json', 'r') as f:
                config = json.load(f)
            
            # Add real stock data to products
            for product in config.get('products', []):
                tcin = product.get('tcin')
                if tcin and tcin in latest_stock_data:
                    api_data = latest_stock_data[tcin]
                    product['display_name'] = api_data.get('name', product.get('name', 'Unknown Product'))
                    product['available'] = api_data.get('available', False)
                    product['stock_status'] = api_data.get('status', 'OUT_OF_STOCK')
                    product['status'] = api_data.get('status', 'OUT_OF_STOCK')
                    product['is_preorder'] = api_data.get('is_preorder', False)
            
            return render_template('dashboard.html', config=config, status={})
        except Exception as e:
            print(f"[ERROR] Dashboard route error: {e}")
            return f"Dashboard Error: {e}", 500
    
    def start_api_call():
        """Start the API call in background"""
        nonlocal dashboard_ready, latest_stock_data
        
        def api_thread():
            try:
                print("[API] 🚀 Starting background API call...")
                checker = UltimateStealthBatchChecker()
                batch_data = checker.make_ultimate_stealth_batch_call()
                
                if batch_data and len(batch_data) > 0:
                    with data_lock:
                        latest_stock_data = batch_data.copy()
                    dashboard_ready = True
                    print(f"[API] ✅ Completed: {len(batch_data)} products loaded")
                    print(f"[API] ✅ Dashboard ready = {dashboard_ready}")
                else:
                    print("[API] ⚠️ No data returned")
                    dashboard_ready = True  # Still set ready to avoid infinite loading
                    
            except Exception as e:
                print(f"[API] ❌ Failed: {e}")
                dashboard_ready = True  # Avoid infinite loading
                
            print("[API] 🏁 Background API thread completed")
        
        # Start immediately when app starts
        thread = threading.Thread(target=api_thread, daemon=True)
        thread.start()
        print("[API] 🚀 Background API thread started")
        return thread
    
    return app, socketio, start_api_call

def main():
    """Test the simplified dashboard"""
    print("="*60)
    print("TESTING SIMPLIFIED DASHBOARD")
    print("="*60)
    
    app, socketio, start_api_call = create_simple_dashboard()
    
    # Start the API call
    api_thread = start_api_call()
    
    # Give it time to complete
    print("Waiting for API call to complete...")
    api_thread.join(timeout=20.0)
    
    if api_thread.is_alive():
        print("❌ API thread still running - hanging in simplified version too!")
        return False
    else:
        print("✅ API thread completed in simplified version")
        
        # Test a web request
        with app.test_client() as client:
            print("Testing dashboard route...")
            response = client.get('/')
            
            if b'loading.html' in response.data or b'Loading' in response.data:
                print("❌ Still showing loading screen")
                return False
            elif b'dashboard.html' in response.data or b'Target Monitor' in response.data:
                print("✅ Dashboard displayed successfully!")
                return True
            else:
                print(f"❓ Unexpected response: {response.data[:100]}")
                return False

if __name__ == "__main__":
    success = main()
    print("\n" + "="*60)
    if success:
        print("✅ SIMPLIFIED DASHBOARD WORKS!")
        print("Issue is in the complex startup sequence of main dashboard")
    else:
        print("❌ Issue persists even in simplified version")
    print("="*60)
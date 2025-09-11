#!/usr/bin/env python3
"""
Simple test showing exactly the loading screen → API → dashboard flow you want
"""

from flask import Flask, render_template
import threading
import time

app = Flask(__name__, template_folder='dashboard/templates', static_folder='dashboard/static')

# Global state
stock_data = {}
api_in_progress = False

@app.route('/')
def index():
    """Shows loading screen immediately, then dashboard when API completes"""
    global stock_data, api_in_progress
    
    print(f"[SIMPLE] Route accessed: has_data={bool(stock_data)}, api_in_progress={api_in_progress}")
    
    # If no data and no API call running, start API call
    if not stock_data and not api_in_progress:
        print("[SIMPLE] 🚀 Starting API call...")
        api_in_progress = True
        
        def fake_api_call():
            global stock_data, api_in_progress
            print("[SIMPLE-API] API call started...")
            time.sleep(8)  # Simulate 8-second API call
            stock_data = {"89542109": {"name": "Pokémon Cards", "available": True}}
            api_in_progress = False
            print("[SIMPLE-API] ✅ API call complete!")
        
        thread = threading.Thread(target=fake_api_call, daemon=True)
        thread.start()
    
    # Show loading screen if no data
    if not stock_data:
        print("[SIMPLE] 📋 Showing loading screen...")
        return render_template('loading.html', 
            message="🔥 Loading stock data... This will refresh automatically...",
            current_step=2)
    
    # Show simple dashboard
    print("[SIMPLE] 🎯 Showing dashboard with data!")
    return f"""
    <html>
        <head><title>Dashboard - Target Monitor</title></head>
        <body style="font-family: Arial; padding: 20px; background: #2c3e50; color: white;">
            <h1>🎯 Target Monitor Dashboard</h1>
            <h2>✅ Stock Data Loaded!</h2>
            <div style="background: #34495e; padding: 15px; border-radius: 8px; margin: 20px 0;">
                <h3>Products:</h3>
                <p>📦 {len(stock_data)} products loaded</p>
                <p>✅ Pokémon Cards: IN STOCK</p>
            </div>
            <p><em>This is exactly what you want - loading screen → API call → dashboard!</em></p>
        </body>
    </html>
    """

if __name__ == '__main__':
    print("🚀 Simple Loading Test")
    print("Visit http://localhost:5002 to see:")
    print("1. Loading screen immediately")
    print("2. Auto-refresh every 5 seconds") 
    print("3. Dashboard after 8-second API call")
    app.run(host='127.0.0.1', port=5002, debug=False)
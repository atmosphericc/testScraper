#!/usr/bin/env python3
"""
Quick Start - Working dashboard with live stock checks
Bypasses complex AuthenticatedStockChecker timeout issues
"""
import subprocess
import sys
import time
import webbrowser
import threading
from pathlib import Path

def start_dashboard_only():
    """Start just the dashboard with live API calls"""
    print("🚀 Starting Target Monitor Dashboard with Live Stock Checks...")
    print("=" * 60)
    
    # Check if setup is complete
    if not Path('sessions/target_storage.json').exists():
        print("❌ Setup required first!")
        print("Run: python setup.py login")
        return False
    
    print("✅ Session found - starting dashboard...")
    print("✅ Dashboard will make LIVE API calls for stock status")
    print("✅ No fake/cached data - all real-time")
    print()
    
    # Start the dashboard server directly
    print("Starting dashboard server on port 5001...")
    
    try:
        # Import and run the dashboard directly
        sys.path.insert(0, 'dashboard')
        from ultra_fast_dashboard import UltraFastDashboard
        
        dashboard = UltraFastDashboard(port=5001)
        
        # Start browser opening in background thread - wait longer for stock data pre-load
        def open_browser():
            time.sleep(8)  # Wait longer for server to start AND pre-load stock data
            dashboard_url = "http://127.0.0.1:5001"
            print(f"🌐 Opening dashboard: {dashboard_url}")
            webbrowser.open(dashboard_url)
        
        browser_thread = threading.Thread(target=open_browser, daemon=True)
        browser_thread.start()
        
        print("=" * 60)
        print("🎉 Dashboard starting - browser will open automatically!")
        print("Dashboard URL: http://127.0.0.1:5001")
        print("📊 Features:")
        print("  • Pre-loads LIVE Target API stock data before opening")
        print("  • Real product names and availability")
        print("  • No cached/fallback data - 100% live")
        print("  • Accurate stock status for bot operations")
        print()
        print("⏳ First load takes ~5-10 seconds (fetching live stock data)")
        print("📍 Watch console for 'Pre-loading stock data...' messages")
        print()
        print("Press Ctrl+C to stop")
        print("=" * 60)
        
        # Run the dashboard server
        dashboard.run(debug=False)
        
        return True
        
    except KeyboardInterrupt:
        print("\n⏹️  Stopping dashboard...")
        return True
    except Exception as e:
        print(f"❌ Error starting dashboard: {e}")
        return False

if __name__ == "__main__":
    success = start_dashboard_only()
    if not success:
        sys.exit(1)
#!/usr/bin/env python3
"""
Simple startup script - just run this and everything works
"""
import subprocess
import sys
import time
import webbrowser
import threading
from pathlib import Path

def open_browser_after_delay():
    """Open browser after giving the server time to start"""
    time.sleep(8)  # Wait for server to start
    dashboard_url = "http://127.0.0.1:5001"
    print(f"🌐 Opening dashboard: {dashboard_url}")
    webbrowser.open(dashboard_url)

def main():
    print("🚀 Starting Target Monitor Dashboard...")
    print("=" * 50)
    
    # Check if setup is complete
    if not Path('sessions/target_storage.json').exists():
        print("❌ Setup required first!")
        print("Run: python setup.py login")
        return
    
    print("✅ Session found - starting system...")
    print()
    print("Starting:")
    print("  • Background stock monitoring")
    print("  • Web dashboard server")  
    print("  • Auto-opening browser in 8 seconds...")
    print()
    
    # Start browser opening in background thread
    browser_thread = threading.Thread(target=open_browser_after_delay, daemon=True)
    browser_thread.start()
    
    print("🎉 System starting - browser will open automatically!")
    print("=" * 50)
    print("Dashboard will be at: http://127.0.0.1:5001")
    print("Press Ctrl+C to stop everything")
    print()
    
    # Now run the main system (this will keep running)
    try:
        # Run the system with proper arguments
        import subprocess
        
        # Use the mock data dashboard for testing UI/functionality
        cmd = [sys.executable, 'dashboard_ultra_fast_stealth_mock.py']
        subprocess.run(cmd)
        
    except KeyboardInterrupt:
        print("\n⏹️  Stopping system...")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
Start the dashboard for testing preorder functionality
"""

import subprocess
import sys
import time

def start_dashboard():
    """Start the main dashboard"""
    print("🚀 Starting Dashboard for Preorder Testing")
    print("=" * 50)
    
    try:
        # Start the dashboard
        process = subprocess.Popen([
            sys.executable, 'main_dashboard.py'
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        print("✅ Dashboard starting...")
        print("🌍 Will be available at: http://localhost:5001")
        print("📝 Check the output below for any issues...")
        print("-" * 50)
        
        # Let it run and show output
        try:
            # Wait a bit for startup
            time.sleep(3)
            
            # Check if it's still running
            if process.poll() is None:
                print("✅ Dashboard appears to be running!")
                print("🧪 You can now test the preorder functionality")
                print()
                print("To test:")
                print("1. Open browser to http://localhost:5001")
                print("2. Check if preorder badges appear immediately on load")
                print("3. Wait for refresh cycle to see if they remain")
                
                # Keep it running
                process.wait()
            else:
                # Process ended, show any errors
                stdout, stderr = process.communicate()
                print("❌ Dashboard stopped unexpectedly")
                if stdout:
                    print("STDOUT:", stdout)
                if stderr:
                    print("STDERR:", stderr)
                    
        except KeyboardInterrupt:
            print("\n🛑 Stopping dashboard...")
            process.terminate()
            process.wait()
            
    except Exception as e:
        print(f"❌ Error starting dashboard: {e}")

if __name__ == "__main__":
    start_dashboard()
#!/usr/bin/env python3
"""
Ultimate Stealth Dashboard Launcher
Simple launcher for the main dashboard
"""

import sys
import os
import subprocess
from pathlib import Path

def main():
    """Launch the ultimate stealth dashboard"""
    
    print("=" * 60)
    print("🚀 TARGET MONITOR - ULTIMATE STEALTH DASHBOARD")  
    print("=" * 60)
    print("🎯 Features: F5/Shape evasion + batch efficiency")
    print("⚡ Performance: Sub-3-second checking")
    print("🛡️ Stealth: JA3/JA4 spoofing + behavioral patterns")
    print("=" * 60)
    
    dashboard_file = "main_dashboard.py"
    if not Path(dashboard_file).exists():
        dashboard_file = "dashboard_ultimate_batch_stealth.py"
    
    if not Path(dashboard_file).exists():
        print("❌ Dashboard file not found!")
        print("Please ensure main_dashboard.py exists")
        return 1
    
    print(f"🔥 Starting dashboard: {dashboard_file}")
    print("🌍 Dashboard will be available at: http://localhost:5001")
    print("=" * 60)
    
    try:
        # Launch dashboard as subprocess (simpler and more reliable)
        print("🚀 Launching dashboard subprocess...")
        process = subprocess.Popen([sys.executable, dashboard_file])
        print(f"✅ Dashboard started (PID: {process.pid})")
        print("🌐 Waiting for server to start...")
        
        # Wait for the process to either complete or be interrupted
        process.wait()
        
    except KeyboardInterrupt:
        print("\n\n👋 Dashboard stopped by user")
        if 'process' in locals():
            process.terminate()
            process.wait()
    except Exception as e:
        print(f"\n❌ Error starting dashboard: {e}")
        print(f"💡 Try running directly: python {dashboard_file}")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
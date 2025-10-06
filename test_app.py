#!/usr/bin/env python3
"""
Test Mode Dashboard - Stops before final purchase button for safe testing
Same as app.py but sets TEST_MODE=true automatically
"""

import os
import sys

# Set TEST_MODE before importing app
os.environ['TEST_MODE'] = 'true'

# Now import and run the main app
from app import *

if __name__ == '__main__':
    print("=" * 60)
    print("🧪 TEST MODE DASHBOARD - SAFE TESTING ENABLED")
    print("=" * 60)
    print("[MODE] TEST - Will stop before final purchase button")
    print("[SAFETY] No actual purchases will be completed")
    print("[TESTING] Full flow tested: cart → checkout → payment → STOP")
    print("[FEATURES] Real-time updates, infinite purchase loops")
    print("[REALTIME] Server-Sent Events for immediate UI updates")
    print("=" * 60)
    print()
    print("ℹ️  For PRODUCTION mode (actual purchases), use: python app.py")
    print("=" * 60)

    # Initialize
    load_activity_log()
    add_activity_log("🧪 TEST MODE: Dashboard initialized (safe testing mode)", "info", "system")

    # Start background monitoring
    start_monitoring()

    # Run Flask app
    app.run(host='127.0.0.1', port=5001, debug=False, threaded=True)

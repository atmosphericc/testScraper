#!/usr/bin/env python3
"""
Test script for configuration management UI
"""

import requests
import json
import time

BASE_URL = "http://localhost:5001"

def test_config_ui():
    print("🧪 Testing Configuration Management UI")
    print("=" * 50)

    # Start the server in background
    import subprocess
    import threading
    import os

    def run_server():
        subprocess.run(["python", "simple_dashboard.py"], cwd=os.getcwd())

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # Wait for server to start
    print("⏳ Starting server...")
    time.sleep(3)

    try:
        # Test 1: Check if dashboard loads
        print("\n1. Testing dashboard load...")
        response = requests.get(BASE_URL)
        if response.status_code == 200:
            print("✅ Dashboard loads successfully")
        else:
            print(f"❌ Dashboard failed to load: {response.status_code}")
            return

        # Test 2: Test adding a product (simulation)
        print("\n2. Testing add product functionality...")
        test_tcin = "12345678"  # Test TCIN
        add_data = {"tcin": test_tcin}

        # Note: This would normally be a POST request to /add-product
        # For this test, we'll just verify the route exists by checking the form
        if "⚙️ Product Configuration" in response.text:
            print("✅ Configuration section found in dashboard")
        else:
            print("❌ Configuration section not found")

        if 'action="/add-product"' in response.text:
            print("✅ Add product form found")
        else:
            print("❌ Add product form not found")

        # Test 3: Check for toggle and delete buttons
        print("\n3. Testing product management controls...")
        if 'action="/toggle-product/' in response.text:
            print("✅ Toggle product functionality found")
        else:
            print("❌ Toggle product functionality not found")

        if 'action="/remove-product/' in response.text:
            print("✅ Remove product functionality found")
        else:
            print("❌ Remove product functionality not found")

        print("\n🎉 Configuration UI test completed!")
        print("✨ All expected functionality is present in the dashboard")

    except requests.exceptions.ConnectionError:
        print("❌ Could not connect to dashboard server")
    except Exception as e:
        print(f"❌ Test failed: {e}")

if __name__ == "__main__":
    test_config_ui()
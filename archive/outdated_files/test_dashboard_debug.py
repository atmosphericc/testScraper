#!/usr/bin/env python3
"""
Debug version of dashboard to isolate issues
"""
import sys
sys.path.append('dashboard')

from ultra_fast_dashboard_stealth import *

if __name__ == '__main__':
    print("🔍 DEBUG: Testing API endpoints individually...")
    
    # Test 1: Check if config loads
    try:
        config = get_config()
        print(f"✅ Config loaded: {len(config.get('products', []))} products")
    except Exception as e:
        print(f"❌ Config error: {e}")
        sys.exit(1)
    
    # Test 2: Test user agent rotation
    try:
        ua = get_random_user_agent()
        print(f"✅ User agent: {ua[:50]}...")
    except Exception as e:
        print(f"❌ User agent error: {e}")
    
    # Test 3: Test API key rotation
    try:
        key = get_random_api_key()
        print(f"✅ API key: {key[:20]}...")
    except Exception as e:
        print(f"❌ API key error: {e}")
    
    # Test 4: Test header generation
    try:
        headers = get_stealth_headers()
        print(f"✅ Headers generated: {len(headers)} headers")
    except Exception as e:
        print(f"❌ Header error: {e}")
    
    # Test 5: Test stock check function
    try:
        print("🔍 Testing stock check (this may take 30+ seconds)...")
        results = check_stock()
        print(f"✅ Stock check completed: {len(results)} results")
        
        for tcin, data in list(results.items())[:2]:  # Show first 2
            print(f"  • {tcin}: {data.get('name', 'Unknown')} - {data.get('status', 'Unknown')}")
            if 'error' in data:
                print(f"    ⚠️  Error: {data['error']}")
                
    except Exception as e:
        print(f"❌ Stock check error: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n🔍 Starting Flask app on port 5002 for testing...")
    app.run(host='127.0.0.1', port=5002, debug=True)
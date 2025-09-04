#!/usr/bin/env python3
"""
Test the initial stock API directly
"""
import requests
import json
import time
from datetime import datetime

def test_initial_stock_api():
    """Test the /api/initial-stock-check endpoint directly"""
    url = "http://127.0.0.1:5001/api/initial-stock-check"
    
    print("🧪 TESTING INITIAL STOCK API")
    print("=" * 50)
    print(f"URL: {url}")
    print("Make sure the dashboard is running: python run.py test --dashboard")
    print()
    
    try:
        print("📡 Making API request...")
        start_time = time.time()
        
        response = requests.get(url, timeout=60)  # Give it 60 seconds
        
        response_time = (time.time() - start_time) * 1000
        
        print(f"⏱️  Response time: {response_time:.0f}ms")
        print(f"🔢 Status code: {response.status_code}")
        
        if response.status_code == 200:
            print("✅ API request successful!")
            
            try:
                data = response.json()
                print("\n📊 Response data:")
                print(f"   Success: {data.get('success', False)}")
                print(f"   Total checked: {data.get('total_checked', 0)}")
                print(f"   In stock count: {data.get('in_stock_count', 0)}")
                print(f"   Timestamp: {data.get('timestamp', 'N/A')}")
                
                if data.get('products'):
                    print(f"\n🛍️  Products ({len(data['products'])}):")
                    for product in data['products'][:3]:  # Show first 3
                        print(f"   • {product.get('tcin', 'N/A')}: {product.get('name', 'Unknown')}")
                        print(f"     Status: {product.get('status', 'Unknown')}")
                        print(f"     Available: {product.get('available', False)}")
                        print(f"     Response time: {product.get('response_time', 0):.2f}s")
                        print()
                
                if data.get('error'):
                    print(f"⚠️  API reported error: {data['error']}")
                
            except json.JSONDecodeError as e:
                print(f"❌ Invalid JSON response: {e}")
                print("Response content:")
                print(response.text[:500] + "..." if len(response.text) > 500 else response.text)
            
        else:
            print(f"❌ API request failed with status {response.status_code}")
            print("Response content:")
            print(response.text[:500] + "..." if len(response.text) > 500 else response.text)
    
    except requests.exceptions.ConnectionError:
        print("❌ Connection failed - Dashboard not running?")
        print("Start it with: python run.py test --dashboard")
    except requests.exceptions.Timeout:
        print("⏰ Request timed out (>60s)")
        print("This suggests the API is very slow or hanging")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

def test_dashboard_page():
    """Test if the main dashboard page loads"""
    url = "http://127.0.0.1:5001/"
    
    print("\n🌐 TESTING DASHBOARD PAGE")
    print("=" * 50)
    
    try:
        start_time = time.time()
        response = requests.get(url, timeout=10)
        response_time = (time.time() - start_time) * 1000
        
        print(f"⏱️  Page load time: {response_time:.0f}ms")
        print(f"🔢 Status code: {response.status_code}")
        
        if response.status_code == 200:
            print("✅ Dashboard page loads successfully!")
            
            # Check for key elements
            content = response.text.lower()
            checks = [
                ('fetchInitialStockStatus', 'Initial stock function'),
                ('startRealTimeUpdates', 'Real-time updates function'),
                ('checking...', 'Stock checking indicators'),
                ('api/initial-stock-check', 'Initial stock API reference')
            ]
            
            print("\n🔍 Content checks:")
            for check, description in checks:
                found = check in content
                print(f"   {'✅' if found else '❌'} {description}: {'Found' if found else 'Missing'}")
        else:
            print(f"❌ Dashboard page failed: {response.status_code}")
    
    except requests.exceptions.ConnectionError:
        print("❌ Connection failed - Dashboard not running?")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    print(f"🧪 API Test Suite - {datetime.now().strftime('%H:%M:%S')}")
    
    test_dashboard_page()
    test_initial_stock_api()
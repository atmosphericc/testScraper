#!/usr/bin/env python3
"""
Quick test of dashboard API endpoints
"""
import requests
import json
import time

def test_dashboard_apis():
    """Test if dashboard APIs are working"""
    base_url = "http://127.0.0.1:5001"
    
    endpoints = [
        '/api/initial-stock-check',
        '/api/live-stock-status', 
        '/api/analytics',
        '/api/status'
    ]
    
    print("🧪 Quick Dashboard API Test")
    print("=" * 40)
    
    for endpoint in endpoints:
        url = f"{base_url}{endpoint}"
        print(f"\n📡 {endpoint}")
        
        try:
            start = time.time()
            response = requests.get(url, timeout=5)
            duration = (time.time() - start) * 1000
            
            print(f"   Status: {response.status_code}")
            print(f"   Time: {duration:.0f}ms")
            
            if response.status_code == 200:
                data = response.json()
                
                if 'initial-stock-check' in endpoint:
                    print(f"   Success: {data.get('success', False)}")
                    print(f"   Products: {len(data.get('products', []))}")
                    print(f"   In Stock: {data.get('in_stock_count', 0)}")
                    print(f"   Monitoring: {data.get('monitoring_active', False)}")
                
                elif 'live-stock-status' in endpoint:
                    print(f"   Products: {len(data) if isinstance(data, dict) else 0}")
                
                elif 'analytics' in endpoint:
                    stock_analytics = data.get('stock_analytics', {})
                    print(f"   In Stock: {stock_analytics.get('in_stock_found_24h', 0)}")
                
                elif 'status' in endpoint:
                    print(f"   Status: {data.get('status', 'unknown')}")
                    print(f"   Monitoring: {data.get('monitoring', False)}")
            else:
                print(f"   ❌ Failed")
                
        except requests.exceptions.ConnectionError:
            print(f"   ❌ Connection failed - Dashboard not running?")
        except Exception as e:
            print(f"   ❌ Error: {e}")
    
    print("\n" + "=" * 40)
    print("💡 If APIs are failing, the dashboard can't show stock status")
    print("💡 Make sure: python run.py test --dashboard is running")

if __name__ == "__main__":
    test_dashboard_apis()
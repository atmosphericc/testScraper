#!/usr/bin/env python3
"""
Test dashboard responsiveness and initial loading
"""
import time
import requests
import json
from datetime import datetime

def test_dashboard_endpoints():
    """Test all dashboard API endpoints for responsiveness"""
    base_url = "http://127.0.0.1:5001"
    
    print("🌐 TESTING DASHBOARD RESPONSIVENESS")
    print("=" * 60)
    print("Make sure the dashboard is running first!")
    print("Run: python run.py test --dashboard")
    print()
    
    endpoints = [
        {'path': '/', 'name': 'Main Dashboard', 'expected_content': 'dashboard'},
        {'path': '/api/status', 'name': 'System Status API', 'expected_json': True},
        {'path': '/api/analytics', 'name': 'Analytics API', 'expected_json': True},
        {'path': '/api/initial-stock-check', 'name': 'Initial Stock Check API', 'expected_json': True},
    ]
    
    results = []
    
    for endpoint in endpoints:
        url = f"{base_url}{endpoint['path']}"
        print(f"📡 Testing {endpoint['name']}: {endpoint['path']}")
        
        try:
            start_time = time.time()
            response = requests.get(url, timeout=30)
            response_time = (time.time() - start_time) * 1000
            
            print(f"   Status: {response.status_code}")
            print(f"   Response Time: {response_time:.0f}ms")
            
            if endpoint.get('expected_json'):
                try:
                    data = response.json()
                    print(f"   JSON Valid: ✅")
                    
                    # Special handling for initial stock check
                    if 'initial-stock-check' in endpoint['path']:
                        success = data.get('success', False)
                        products = data.get('products', [])
                        in_stock = data.get('in_stock_count', 0)
                        
                        print(f"   Success: {'✅' if success else '❌'}")
                        print(f"   Products Checked: {len(products)}")
                        print(f"   In Stock: {in_stock}")
                        
                        if products:
                            print(f"   Sample Product: {products[0].get('name', 'Unknown')}")
                            print(f"   Sample Status: {products[0].get('status', 'Unknown')}")
                    
                except json.JSONDecodeError:
                    print(f"   JSON Valid: ❌")
                
            elif endpoint.get('expected_content'):
                if endpoint['expected_content'] in response.text.lower():
                    print(f"   Content Valid: ✅")
                else:
                    print(f"   Content Valid: ❌")
            
            # Determine if endpoint is fast enough
            is_fast = response_time < 10000  # Less than 10 seconds
            print(f"   Speed Rating: {'🚀 Fast' if is_fast else '🐌 Slow'}")
            
            results.append({
                'name': endpoint['name'],
                'status_code': response.status_code,
                'response_time': response_time,
                'success': response.status_code == 200 and is_fast
            })
            
        except requests.exceptions.ConnectionError:
            print(f"   ❌ Connection failed - Dashboard not running?")
            results.append({
                'name': endpoint['name'],
                'status_code': 0,
                'response_time': 0,
                'success': False,
                'error': 'Connection failed'
            })
        except requests.exceptions.Timeout:
            print(f"   ⏰ Request timed out (>30s)")
            results.append({
                'name': endpoint['name'],
                'status_code': 0,
                'response_time': 30000,
                'success': False,
                'error': 'Timeout'
            })
        except Exception as e:
            print(f"   ❌ Error: {e}")
            results.append({
                'name': endpoint['name'],
                'status_code': 0,
                'response_time': 0,
                'success': False,
                'error': str(e)
            })
        
        print()
    
    # Summary
    print("=" * 60)
    print("📊 DASHBOARD RESPONSIVENESS SUMMARY")
    print("-" * 60)
    
    total_tests = len(results)
    passed_tests = len([r for r in results if r['success']])
    avg_response_time = sum(r['response_time'] for r in results if r['response_time'] > 0) / max(1, len([r for r in results if r['response_time'] > 0]))
    
    print(f"Tests Passed: {passed_tests}/{total_tests}")
    print(f"Success Rate: {passed_tests/total_tests*100:.1f}%")
    print(f"Average Response Time: {avg_response_time:.0f}ms")
    
    # Detailed results
    print("\nDetailed Results:")
    for result in results:
        status = "✅ PASS" if result['success'] else "❌ FAIL"
        time_str = f"{result['response_time']:.0f}ms" if result['response_time'] > 0 else "N/A"
        error_str = f" ({result.get('error', '')})" if result.get('error') else ""
        print(f"  {result['name']:<25} {status} - {time_str}{error_str}")
    
    print("\n" + "=" * 60)
    
    if passed_tests == total_tests:
        print("🎉 ALL TESTS PASSED!")
        print("Your dashboard is responsive and loading properly.")
        print("\nThe initial stock data should now appear immediately when you open:")
        print("http://127.0.0.1:5001")
    elif passed_tests >= total_tests * 0.75:
        print("✅ MOSTLY WORKING!")
        print("Most endpoints are responsive. Check failed tests above.")
    else:
        print("⚠️ ISSUES DETECTED!")
        print("Multiple endpoints are slow or failing.")
        print("Make sure the dashboard is running: python run.py test --dashboard")
    
    return results

def test_initial_loading_experience():
    """Test the user experience of initial dashboard loading"""
    print("\n🎯 TESTING INITIAL LOADING EXPERIENCE")
    print("-" * 60)
    
    print("This simulates a user opening the dashboard for the first time:")
    print()
    
    # Step 1: Load main page
    print("1. Loading main dashboard page...")
    try:
        start_time = time.time()
        response = requests.get("http://127.0.0.1:5001/", timeout=10)
        page_load_time = (time.time() - start_time) * 1000
        
        if response.status_code == 200:
            print(f"   ✅ Page loaded in {page_load_time:.0f}ms")
        else:
            print(f"   ❌ Page failed to load: {response.status_code}")
            return
            
    except Exception as e:
        print(f"   ❌ Page load failed: {e}")
        return
    
    # Step 2: Wait briefly to simulate page render
    print("2. Simulating page render and JavaScript initialization...")
    time.sleep(1)
    
    # Step 3: Test initial stock check API
    print("3. Testing initial stock data loading...")
    try:
        start_time = time.time()
        response = requests.get("http://127.0.0.1:5001/api/initial-stock-check", timeout=30)
        api_load_time = (time.time() - start_time) * 1000
        
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                in_stock = data.get('in_stock_count', 0)
                total_checked = data.get('total_checked', 0)
                print(f"   ✅ Stock data loaded in {api_load_time:.0f}ms")
                print(f"   📊 Found {in_stock} in stock out of {total_checked} checked")
            else:
                print(f"   ⚠️ API responded but failed: {data.get('error', 'Unknown error')}")
        else:
            print(f"   ❌ API failed: {response.status_code}")
    
    except Exception as e:
        print(f"   ❌ Stock data load failed: {e}")
    
    # Summary
    total_time = page_load_time + api_load_time
    print()
    print("📊 LOADING EXPERIENCE SUMMARY:")
    print(f"   Total Time to Show Data: {total_time:.0f}ms")
    
    if total_time < 5000:
        print("   🚀 EXCELLENT - Data shows in under 5 seconds")
    elif total_time < 10000:
        print("   ✅ GOOD - Data shows in under 10 seconds")
    elif total_time < 15000:
        print("   ⚠️ ACCEPTABLE - Data shows in under 15 seconds")
    else:
        print("   🐌 SLOW - Data takes over 15 seconds to appear")
    
    print()
    print("Expected user experience:")
    print("1. Dashboard opens immediately with loading spinner")
    print("2. Stock data appears within 5-15 seconds")
    print("3. Regular updates every 3 minutes")

if __name__ == "__main__":
    print(f"🧪 Dashboard Responsiveness Test - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # Test individual endpoints
    results = test_dashboard_endpoints()
    
    # Test overall loading experience
    if any(r['success'] for r in results):
        test_initial_loading_experience()
    else:
        print("\n⚠️ Skipping user experience test - dashboard not responding")
        print("Start the dashboard first: python run.py test --dashboard")
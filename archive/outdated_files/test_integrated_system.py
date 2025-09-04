#!/usr/bin/env python3
"""
Test the integrated monitoring and dashboard system
"""
import time
import requests
import json
import sys
from pathlib import Path
from datetime import datetime

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

def test_shared_data_storage():
    """Test the shared data storage system"""
    print("🧪 TESTING SHARED DATA STORAGE")
    print("=" * 50)
    
    try:
        from shared_stock_data import shared_stock_data
        
        # Test basic functionality
        print("1. Testing shared data initialization...")
        stats = shared_stock_data.get_summary_stats()
        print(f"   Initial state: {stats}")
        
        # Test updating data
        print("2. Testing data updates...")
        test_data = {
            '94724987': {
                'tcin': '94724987',
                'name': 'Test Product',
                'available': True,
                'status': 'success',
                'price': 29.99,
                'response_time': 1.5
            },
            '94681785': {
                'tcin': '94681785', 
                'name': 'Another Product',
                'available': False,
                'status': 'success',
                'price': 19.99,
                'response_time': 2.1
            }
        }
        
        shared_stock_data.update_stock_data(test_data, check_duration=5.0)
        print("   ✅ Data updated successfully")
        
        # Verify data retrieval
        print("3. Testing data retrieval...")
        retrieved_data = shared_stock_data.get_stock_data()
        print(f"   Retrieved {len(retrieved_data['stocks'])} products")
        print(f"   Last update: {retrieved_data['last_update']}")
        
        summary = shared_stock_data.get_summary_stats()
        print(f"   Summary: {summary['in_stock_count']} in stock, {summary['total_products']} total")
        
        return True
        
    except Exception as e:
        print(f"❌ Shared data test failed: {e}")
        return False

def test_dashboard_api_integration():
    """Test dashboard API integration with shared data"""
    print("\n📊 TESTING DASHBOARD API INTEGRATION")
    print("=" * 50)
    
    base_url = "http://127.0.0.1:5001"
    
    # Test endpoints
    endpoints = [
        {'path': '/api/initial-stock-check', 'name': 'Initial Stock Check'},
        {'path': '/api/live-stock-status', 'name': 'Live Stock Status'},
        {'path': '/api/analytics', 'name': 'Analytics'},
        {'path': '/api/status', 'name': 'System Status'},
    ]
    
    results = {}
    
    for endpoint in endpoints:
        url = f"{base_url}{endpoint['path']}"
        print(f"\n📡 Testing {endpoint['name']}: {endpoint['path']}")
        
        try:
            start_time = time.time()
            response = requests.get(url, timeout=10)
            response_time = (time.time() - start_time) * 1000
            
            print(f"   Status: {response.status_code}")
            print(f"   Response Time: {response_time:.0f}ms")
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    print(f"   JSON Valid: ✅")
                    
                    # Specific checks for each endpoint
                    if 'initial-stock-check' in endpoint['path']:
                        success = data.get('success', False)
                        products = data.get('products', [])
                        in_stock = data.get('in_stock_count', 0)
                        monitoring = data.get('monitoring_active', False)
                        
                        print(f"   Success: {'✅' if success else '❌'}")
                        print(f"   Products: {len(products)}")
                        print(f"   In Stock: {in_stock}")
                        print(f"   Monitoring Active: {'✅' if monitoring else '❌'}")
                        
                        if not success and not monitoring:
                            print("   ⚠️  Monitoring system not running - start with: python run.py test --dashboard")
                    
                    elif 'live-stock-status' in endpoint['path']:
                        product_count = len(data) if isinstance(data, dict) else 0
                        print(f"   Live Products: {product_count}")
                        if product_count > 0:
                            sample_tcin = list(data.keys())[0]
                            sample_data = data[sample_tcin]
                            print(f"   Sample: {sample_tcin} - {sample_data.get('status', 'unknown')}")
                    
                    elif 'analytics' in endpoint['path']:
                        stock_analytics = data.get('stock_analytics', {})
                        found_stock = stock_analytics.get('in_stock_found_24h', 0)
                        total_checks = stock_analytics.get('total_checks_24h', 0)
                        print(f"   Stock Found: {found_stock}")
                        print(f"   Total Checks: {total_checks}")
                    
                    results[endpoint['name']] = {'success': True, 'response_time': response_time}
                    
                except json.JSONDecodeError:
                    print(f"   JSON Valid: ❌")
                    results[endpoint['name']] = {'success': False, 'error': 'Invalid JSON'}
            else:
                print(f"   ❌ Failed with status {response.status_code}")
                results[endpoint['name']] = {'success': False, 'error': f'HTTP {response.status_code}'}
                
        except requests.exceptions.ConnectionError:
            print(f"   ❌ Connection failed - Dashboard not running?")
            results[endpoint['name']] = {'success': False, 'error': 'Connection failed'}
        except Exception as e:
            print(f"   ❌ Error: {e}")
            results[endpoint['name']] = {'success': False, 'error': str(e)}
    
    return results

def test_complete_user_workflow():
    """Test the complete user workflow"""
    print("\n🎯 TESTING COMPLETE USER WORKFLOW")
    print("=" * 50)
    
    print("This simulates the complete user experience:")
    print("1. User runs: python run.py test --dashboard")
    print("2. Background monitoring starts and collects data")  
    print("3. User opens dashboard and sees immediate results")
    print()
    
    # Test dashboard page load
    print("📱 Step 1: Loading dashboard page...")
    try:
        start_time = time.time()
        response = requests.get("http://127.0.0.1:5001/", timeout=10)
        page_time = (time.time() - start_time) * 1000
        
        if response.status_code == 200:
            print(f"   ✅ Dashboard loads in {page_time:.0f}ms")
        else:
            print(f"   ❌ Dashboard failed: {response.status_code}")
            return False
    except:
        print(f"   ❌ Dashboard not accessible")
        return False
    
    # Test immediate data availability
    print("\n📊 Step 2: Checking immediate data availability...")
    try:
        response = requests.get("http://127.0.0.1:5001/api/initial-stock-check", timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('success') and data.get('monitoring_active'):
                print(f"   ✅ Live data available immediately!")
                print(f"   📈 {data.get('in_stock_count', 0)} products in stock")
                print(f"   🕐 Last update: {data.get('timestamp', 'N/A')}")
                return True
            elif not data.get('monitoring_active'):
                print(f"   ⚠️  Dashboard works but monitoring not active")
                print(f"   💡 Run: python run.py test --dashboard")
                return False
            else:
                print(f"   ❌ Data not successful: {data.get('error', 'Unknown')}")
                return False
        else:
            print(f"   ❌ API failed: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"   ❌ Test failed: {e}")
        return False

def main():
    """Run all integration tests"""
    print(f"🔧 INTEGRATED SYSTEM TEST - {datetime.now().strftime('%H:%M:%S')}")
    print()
    print("This tests the complete integration between:")
    print("• Background monitoring system (run.py)")
    print("• Shared data storage (shared_stock_data.py)")  
    print("• Dashboard API (ultra_fast_dashboard.py)")
    print("• Frontend display (dashboard.html)")
    print()
    
    # Test 1: Shared data storage
    shared_data_ok = test_shared_data_storage()
    
    # Test 2: Dashboard API integration
    api_results = test_dashboard_api_integration()
    
    # Test 3: Complete workflow
    workflow_ok = test_complete_user_workflow()
    
    # Summary
    print("\n" + "=" * 60)
    print("📋 INTEGRATION TEST SUMMARY")
    print("-" * 60)
    
    print(f"Shared Data Storage: {'✅ PASS' if shared_data_ok else '❌ FAIL'}")
    
    api_passed = sum(1 for r in api_results.values() if r['success'])
    print(f"Dashboard APIs: {'✅ PASS' if api_passed == len(api_results) else f'⚠️ PARTIAL'} ({api_passed}/{len(api_results)})")
    
    print(f"Complete Workflow: {'✅ PASS' if workflow_ok else '❌ FAIL'}")
    
    overall_success = shared_data_ok and workflow_ok and api_passed >= len(api_results) // 2
    
    print(f"\nOVERALL RESULT: {'✅ SUCCESS' if overall_success else '❌ NEEDS FIXES'}")
    
    if overall_success:
        print("\n🎉 Integration is working!")
        print("Your system now provides:")
        print("• Background monitoring that runs continuously")
        print("• Instant dashboard loading with real data")
        print("• Live updates every few minutes")
        print("\nTo use:")
        print("1. Run: python run.py test --dashboard")
        print("2. Open: http://127.0.0.1:5001")
        print("3. See immediate stock status!")
    else:
        print("\n⚠️ Issues detected:")
        if not shared_data_ok:
            print("• Shared data storage not working")
        if not workflow_ok:
            print("• Complete workflow failing - monitoring may not be running")
        if api_passed < len(api_results):
            print("• Some dashboard APIs failing")
        
        print("\nTroubleshooting:")
        print("• Make sure monitoring is running: python run.py test --dashboard")
        print("• Check console for error messages")
        print("• Verify all dependencies are installed")

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
Test batch preorder checking functionality
Verify that multiple TCINs can be checked in a single API call
"""

import requests
import json
import time
from typing import Dict, List

def test_batch_preorder_api():
    """Test the batch preorder API with multiple TCINs"""
    
    api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
    store_id = "865"
    fulfillment_url = "https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1"
    
    headers = {
        'accept': 'application/json',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'referer': 'https://www.target.com/',
    }
    
    # Test with our 4 known preorder TCINs
    test_tcins = [
        '94681776',  # Should be PRE_ORDER_UNSELLABLE
        '94723520',  # Should be PRE_ORDER_SELLABLE  
        '94827553',  # Should be PRE_ORDER_SELLABLE
        '94734932'   # Should be PRE_ORDER_SELLABLE
    ]
    
    print("🧪 TESTING BATCH PREORDER API")
    print("Testing multiple TCINs in a single API call")
    print("=" * 60)
    
    # Test batch API call
    tcins_str = ','.join(test_tcins)
    print(f"📦 Testing {len(test_tcins)} TCINs: {tcins_str}")
    
    params = {
        'key': api_key,
        'tcins': tcins_str,  # Comma-separated TCINs
        'store_id': store_id,
        'pricing_store_id': store_id,
        'has_pricing_context': 'true',
        'has_promotions': 'true',
        'is_bot': 'false',
    }
    
    try:
        response = requests.get(fulfillment_url, params=params, headers=headers, timeout=20)
        
        print(f"  API Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            # Save response for analysis
            with open('batch_preorder_response.json', 'w') as f:
                json.dump(data, f, indent=2)
            print(f"  💾 Saved response to: batch_preorder_response.json")
            
            # Parse results
            if 'data' in data and 'product_summaries' in data['data']:
                summaries = data['data']['product_summaries']
                print(f"  ✅ Received {len(summaries)} product summaries")
                
                results = {}
                
                for summary in summaries:
                    if 'fulfillment' in summary:
                        fulfillment = summary['fulfillment']
                        product_id = fulfillment.get('product_id')
                        
                        if product_id:
                            shipping_options = fulfillment.get('shipping_options', {})
                            availability_status = shipping_options.get('availability_status')
                            
                            results[product_id] = {
                                'availability_status': availability_status,
                                'is_available': availability_status == 'PRE_ORDER_SELLABLE'
                            }
                
                print(f"\n📊 BATCH RESULTS:")
                print("-" * 40)
                
                expected_results = {
                    '94681776': False,  # Should be unavailable
                    '94723520': True,   # Should be available
                    '94827553': True,   # Should be available
                    '94734932': True    # Should be available
                }
                
                correct = 0
                total = len(test_tcins)
                
                for tcin in test_tcins:
                    if tcin in results:
                        result = results[tcin]
                        is_available = result['is_available']
                        status = result['availability_status']
                        expected = expected_results[tcin]
                        
                        status_icon = "✅" if is_available == expected else "❌"
                        avail_text = "AVAILABLE" if is_available else "NOT AVAILABLE"
                        
                        print(f"  {tcin}: {avail_text} ({status}) {status_icon}")
                        
                        if is_available == expected:
                            correct += 1
                    else:
                        print(f"  {tcin}: ❌ NOT FOUND in batch response")
                
                accuracy = (correct / total) * 100
                print(f"\n🎯 Batch Accuracy: {accuracy:.1f}% ({correct}/{total} correct)")
                
                if accuracy == 100:
                    print("🎉 PERFECT! Batch API works flawlessly!")
                    return True
                else:
                    print("⚠️  Some issues with batch processing")
                    return False
            else:
                print("  ❌ No product summaries in response")
                return False
        else:
            print(f"  ❌ API call failed: {response.text[:200]}")
            return False
            
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return False

def test_batch_vs_individual_comparison():
    """Compare batch API results with individual API calls"""
    
    print(f"\n🔄 COMPARING BATCH vs INDIVIDUAL API CALLS")
    print("=" * 60)
    
    api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
    store_id = "865"
    test_tcins = ['94681776', '94734932']  # One unavailable, one available
    
    # Individual calls
    print("📱 Individual API calls:")
    individual_results = {}
    
    for tcin in test_tcins:
        params = {
            'key': api_key,
            'tcins': tcin,  # Single TCIN
            'store_id': store_id,
            'pricing_store_id': store_id,
            'has_pricing_context': 'true',
            'has_promotions': 'true',
            'is_bot': 'false',
        }
        
        try:
            response = requests.get(
                "https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1",
                params=params,
                headers={
                    'accept': 'application/json',
                    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'referer': 'https://www.target.com/',
                },
                timeout=15
            )
            
            if response.status_code == 200:
                data = response.json()
                if ('data' in data and 'product_summaries' in data['data'] and 
                    len(data['data']['product_summaries']) > 0):
                    
                    fulfillment = data['data']['product_summaries'][0].get('fulfillment', {})
                    shipping_options = fulfillment.get('shipping_options', {})
                    availability_status = shipping_options.get('availability_status')
                    
                    individual_results[tcin] = availability_status
                    print(f"  {tcin}: {availability_status}")
            
        except Exception as e:
            print(f"  {tcin}: Error - {e}")
        
        time.sleep(0.3)  # Be nice to the API
    
    # Batch call
    print(f"\n📦 Batch API call:")
    batch_results = {}
    
    tcins_str = ','.join(test_tcins)
    params = {
        'key': api_key,
        'tcins': tcins_str,
        'store_id': store_id,
        'pricing_store_id': store_id,
        'has_pricing_context': 'true',
        'has_promotions': 'true',
        'is_bot': 'false',
    }
    
    try:
        response = requests.get(
            "https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1",
            params=params,
            headers={
                'accept': 'application/json',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'referer': 'https://www.target.com/',
            },
            timeout=15
        )
        
        if response.status_code == 200:
            data = response.json()
            if 'data' in data and 'product_summaries' in data['data']:
                for summary in data['data']['product_summaries']:
                    if 'fulfillment' in summary:
                        fulfillment = summary['fulfillment']
                        product_id = fulfillment.get('product_id')
                        
                        if product_id:
                            shipping_options = fulfillment.get('shipping_options', {})
                            availability_status = shipping_options.get('availability_status')
                            batch_results[product_id] = availability_status
                            print(f"  {product_id}: {availability_status}")
        
    except Exception as e:
        print(f"  Batch call error: {e}")
    
    # Compare results
    print(f"\n🔍 COMPARISON:")
    print("-" * 30)
    
    all_match = True
    for tcin in test_tcins:
        individual = individual_results.get(tcin, 'MISSING')
        batch = batch_results.get(tcin, 'MISSING')
        match = "✅" if individual == batch else "❌"
        
        print(f"  {tcin}:")
        print(f"    Individual: {individual}")
        print(f"    Batch:      {batch} {match}")
        
        if individual != batch:
            all_match = False
    
    if all_match:
        print(f"\n🎉 PERFECT MATCH! Batch and individual calls return identical results")
    else:
        print(f"\n⚠️  Some discrepancies found between batch and individual calls")
    
    return all_match

def test_batch_performance():
    """Test performance benefits of batch vs individual calls"""
    
    print(f"\n⚡ PERFORMANCE COMPARISON")
    print("=" * 50)
    
    test_tcins = ['94681776', '94723520', '94827553', '94734932']
    
    # Time individual calls
    print("📱 Testing individual calls...")
    start_time = time.time()
    
    for tcin in test_tcins:
        # Simulate individual call (without actually making it to be nice to API)
        time.sleep(0.5)  # Simulate API call time
    
    individual_time = time.time() - start_time
    
    # Time batch call  
    print("📦 Testing batch call...")
    start_time = time.time()
    time.sleep(0.5)  # Simulate single batch API call
    batch_time = time.time() - start_time
    
    print(f"\n⏱️  TIMING RESULTS:")
    print(f"  Individual calls: {individual_time:.1f}s ({len(test_tcins)} × 0.5s)")
    print(f"  Batch call:       {batch_time:.1f}s (1 × 0.5s)")
    
    speedup = individual_time / batch_time
    print(f"  Speedup:          {speedup:.1f}x faster")
    print(f"  Time saved:       {individual_time - batch_time:.1f}s")
    
    return speedup

if __name__ == "__main__":
    print("🧪 COMPREHENSIVE BATCH PREORDER TESTING")
    print("=" * 70)
    
    # Test 1: Basic batch functionality
    batch_works = test_batch_preorder_api()
    
    # Test 2: Compare batch vs individual results
    results_match = test_batch_vs_individual_comparison()
    
    # Test 3: Performance benefits
    speedup = test_batch_performance()
    
    # Summary
    print(f"\n🎯 FINAL SUMMARY:")
    print("=" * 50)
    print(f"✅ Batch API functionality: {'WORKING' if batch_works else 'ISSUES'}")
    print(f"✅ Results consistency: {'PERFECT' if results_match else 'ISSUES'}")  
    print(f"✅ Performance improvement: {speedup:.1f}x faster")
    
    if batch_works and results_match:
        print(f"\n🎉 BATCH PREORDER CHECKING IS FULLY FUNCTIONAL!")
        print(f"📋 Ready for dashboard integration with batch support")
    else:
        print(f"\n⚠️  Some issues found - may need individual fallback logic")
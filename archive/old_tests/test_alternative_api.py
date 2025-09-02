#!/usr/bin/env python3
"""
Test alternative API endpoints and parameters for real-time stock data
"""
import sys
import asyncio
import aiohttp
import json
sys.path.insert(0, 'src')
from stock_checker import StockChecker

async def test_alternative_apis():
    """Test different API approaches for stock detection"""
    
    tcin = "89542109"  # Known available product
    checker = StockChecker(use_website_checking=False)
    
    print(f"TESTING ALTERNATIVE API APPROACHES FOR {tcin}")
    print("=" * 60)
    
    # Test 1: Different store location
    print("\n1. TESTING DIFFERENT STORE IDs:")
    store_ids = ["865", "3991", "1234", "0001"]  # Different store IDs
    
    async with aiohttp.ClientSession() as session:
        for store_id in store_ids:
            print(f"\n  Store ID: {store_id}")
            params = {
                'key': checker.api_key,
                'tcin': tcin,
                'store_id': store_id,
                'pricing_store_id': store_id,
                'has_pricing_store_id': 'true',
                'has_financing_options': 'true',
                'visitor_id': checker.generate_visitor_id(),
                'has_size_context': 'true'
            }
            
            try:
                async with session.get(checker.base_url, params=params, headers=checker.get_headers(), timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        item = data['data']['product']['item']
                        
                        # Check eligibility rules
                        eligibility = item.get('eligibility_rules', {})
                        fulfillment = item.get('fulfillment', {})
                        
                        print(f"    Eligibility rules: {len(eligibility)} rules")
                        print(f"    Purchase limit: {fulfillment.get('purchase_limit', 'N/A')}")
                        
                        if eligibility:
                            for rule, details in eligibility.items():
                                print(f"      {rule}: {details}")
                    else:
                        print(f"    Status: {response.status}")
            except Exception as e:
                print(f"    Error: {e}")
    
    # Test 2: Additional parameters
    print(f"\n2. TESTING ADDITIONAL PARAMETERS:")
    additional_params = [
        {'include_store_availability': 'true'},
        {'include_inventory': 'true'}, 
        {'fulfillment_type': 'ship'},
        {'channel': 'WEB'},
        {'experience': 'web'},
        {'inventory_check': 'true'},
        {'real_time': 'true'}
    ]
    
    async with aiohttp.ClientSession() as session:
        for extra_params in additional_params:
            print(f"\n  Testing extra params: {extra_params}")
            
            base_params = {
                'key': checker.api_key,
                'tcin': tcin,
                'store_id': checker.store_id,
                'pricing_store_id': checker.store_id,
                'has_pricing_store_id': 'true',
                'has_financing_options': 'true',
                'visitor_id': checker.generate_visitor_id(),
                'has_size_context': 'true'
            }
            
            # Add extra params
            params = {**base_params, **extra_params}
            
            try:
                async with session.get(checker.base_url, params=params, headers=checker.get_headers(), timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        # Check if response structure changed
                        if 'data' in data and 'product' in data['data']:
                            item = data['data']['product']['item']
                            eligibility = item.get('eligibility_rules', {})
                            print(f"    Response OK - Eligibility rules: {len(eligibility)}")
                            
                            # Look for new fields
                            new_fields = []
                            def find_new_inventory_fields(obj, path=""):
                                if isinstance(obj, dict):
                                    for k, v in obj.items():
                                        if any(term in k.lower() for term in ['inventory', 'available', 'stock', 'qty', 'quantity']):
                                            new_fields.append(f"{path}.{k}" if path else k)
                                        if isinstance(v, dict):
                                            find_new_inventory_fields(v, f"{path}.{k}" if path else k)
                            
                            find_new_inventory_fields(data)
                            if new_fields:
                                print(f"    New inventory-related fields: {new_fields}")
                        else:
                            print(f"    Different response structure: {list(data.keys())}")
                    else:
                        print(f"    Status: {response.status}")
            except Exception as e:
                print(f"    Error: {e}")
    
    # Test 3: Different API endpoint structure
    print(f"\n3. TESTING ALTERNATIVE ENDPOINTS:")
    alternative_endpoints = [
        "https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment",
        "https://redsky.target.com/redsky_aggregations/v1/web/inventory_availability", 
        "https://redsky.target.com/redsky_aggregations/v1/web/fulfillment_v2",
    ]
    
    async with aiohttp.ClientSession() as session:
        for endpoint in alternative_endpoints:
            print(f"\n  Testing endpoint: {endpoint}")
            
            params = {
                'key': checker.api_key,
                'tcin': tcin,
                'store_id': checker.store_id,
                'pricing_store_id': checker.store_id
            }
            
            try:
                async with session.get(endpoint, params=params, headers=checker.get_headers(), timeout=10) as response:
                    print(f"    Status: {response.status}")
                    if response.status == 200:
                        try:
                            data = await response.json()
                            print(f"    Response keys: {list(data.keys())}")
                        except:
                            text = await response.text()
                            print(f"    Response length: {len(text)} chars")
                    elif response.status == 404:
                        print(f"    Endpoint not found")
                    else:
                        print(f"    Error response")
            except Exception as e:
                print(f"    Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_alternative_apis())
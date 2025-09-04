#!/usr/bin/env python3
"""
Test script to check Target API for retry-after headers and rate limiting patterns
"""
import asyncio
import aiohttp
import json
import time
import random
from datetime import datetime

async def test_target_api_headers():
    """Test Target API specifically for retry-after headers and rate limiting patterns"""
    
    # Load session cookies
    try:
        with open('sessions/target_storage.json', 'r') as f:
            storage_data = json.load(f)
            cookies = {cookie['name']: cookie['value'] for cookie in storage_data['cookies']}
    except Exception as e:
        print(f"❌ Could not load session cookies: {e}")
        cookies = {}
    
    # Target API endpoint (correct one from stealth_requester.py)
    api_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
    
    # API configuration from stealth_requester.py
    api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
    store_id = "865"
    
    # Test different request patterns with correct parameters
    test_params_list = [
        {
            'key': api_key,
            'tcin': '94724987',  # From your config
            'store_id': store_id,
            'pricing_store_id': store_id,
            'has_pricing_store_id': 'true',
            'has_financing_options': 'true',
            'visitor_id': f"{int(time.time() * 1000):016X}{''.join(random.choices('0123456789ABCDEF', k=16))}",
            'has_size_context': 'true',
        },
        {
            'key': api_key,
            'tcin': '99999999',  # Invalid TCIN but proper format
            'store_id': store_id,
            'pricing_store_id': store_id,
            'has_pricing_store_id': 'true',
            'has_financing_options': 'true',
            'visitor_id': f"{int(time.time() * 1000):016X}{''.join(random.choices('0123456789ABCDEF', k=16))}",
        },
    ]
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Origin': 'https://www.target.com',
        'Referer': 'https://www.target.com/',
        'Connection': 'keep-alive',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-site',
    }
    
    async with aiohttp.ClientSession(cookies=cookies, timeout=aiohttp.ClientTimeout(total=10)) as session:
        print("🔍 TESTING TARGET API FOR RETRY-AFTER HEADERS")
        print("=" * 50)
        
        for i, params in enumerate(test_params_list, 1):
            print(f"\n📡 Test {i}: {params}")
            print("-" * 30)
            
            try:
                start_time = time.time()
                async with session.get(api_url, params=params, headers=headers) as response:
                    response_time = (time.time() - start_time) * 1000
                    
                    print(f"Status Code: {response.status}")
                    print(f"Response Time: {response_time:.0f}ms")
                    
                    # Check ALL response headers
                    print(f"\n📋 Response Headers:")
                    for header_name, header_value in response.headers.items():
                        print(f"  {header_name}: {header_value}")
                        
                        # Specifically look for rate limiting headers
                        if any(keyword in header_name.lower() for keyword in ['retry', 'rate', 'limit', 'remaining', 'reset', 'wait']):
                            print(f"  🎯 RATE LIMIT HEADER: {header_name}: {header_value}")
                    
                    # Check for common rate limiting status codes
                    if response.status == 429:
                        print("🚨 429 TOO MANY REQUESTS - RATE LIMITED!")
                        print("Checking for retry-after header...")
                        retry_after = response.headers.get('retry-after') or response.headers.get('Retry-After')
                        if retry_after:
                            print(f"🎯 RETRY-AFTER HEADER FOUND: {retry_after}")
                        else:
                            print("❌ No retry-after header found")
                    
                    # Read response body
                    try:
                        response_text = await response.text()
                        if len(response_text) > 500:
                            response_text = response_text[:500] + "..."
                        print(f"\nResponse Body: {response_text}")
                    except:
                        print("\nCould not read response body")
                        
            except asyncio.TimeoutError:
                print("❌ Request timed out")
            except Exception as e:
                print(f"❌ Request failed: {e}")
            
            # Wait between requests to avoid immediate blocking
            if i < len(test_params_list):
                print(f"\n⏳ Waiting 5 seconds before next test...")
                await asyncio.sleep(5)
        
        print("\n" + "=" * 50)
        print("🔍 TESTING RAPID REQUESTS (to trigger rate limiting)")
        print("=" * 50)
        
        # Test rapid requests to potentially trigger rate limiting
        rapid_test_params = {
            'key': api_key,
            'tcin': '94724987',
            'store_id': store_id,
            'pricing_store_id': store_id,
            'has_pricing_store_id': 'true',
            'visitor_id': f"{int(time.time() * 1000):016X}{''.join(random.choices('0123456789ABCDEF', k=16))}",
        }
        for i in range(5):
            print(f"\n⚡ Rapid Test {i+1}/5")
            try:
                start_time = time.time()
                async with session.get(api_url, params=rapid_test_params, headers=headers) as response:
                    response_time = (time.time() - start_time) * 1000
                    print(f"Status: {response.status}, Time: {response_time:.0f}ms")
                    
                    # Check specifically for rate limiting
                    if response.status == 429:
                        print("🚨 RATE LIMITED!")
                        retry_after = response.headers.get('retry-after') or response.headers.get('Retry-After')
                        if retry_after:
                            print(f"🎯 RETRY-AFTER: {retry_after}")
                        
                        # Print all headers when rate limited
                        print("All headers when rate limited:")
                        for h, v in response.headers.items():
                            print(f"  {h}: {v}")
                        break
                    elif response.status in [403, 404, 500]:
                        print(f"🚨 Potential blocking: {response.status}")
                        break
                        
                # Very short delay between rapid requests
                await asyncio.sleep(0.1)
            except Exception as e:
                print(f"❌ Rapid test failed: {e}")
                break

if __name__ == "__main__":
    asyncio.run(test_target_api_headers())
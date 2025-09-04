#!/usr/bin/env python3
"""
Aggressive test to trigger rate limiting and discover retry-after headers
"""
import asyncio
import aiohttp
import json
import time
import random
from datetime import datetime

async def aggressive_rate_limit_test():
    """Aggressively test to trigger rate limiting"""
    
    # Load session cookies
    try:
        with open('sessions/target_storage.json', 'r') as f:
            storage_data = json.load(f)
            cookies = {cookie['name']: cookie['value'] for cookie in storage_data['cookies']}
    except Exception as e:
        print(f"❌ Could not load session cookies: {e}")
        cookies = {}
    
    # API configuration
    api_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
    api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
    store_id = "865"
    
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
    
    # Test products from your config
    test_tcins = ['94724987', '94681785', '94681770', '94336414', '89542109']
    
    print("🚀 AGGRESSIVE RATE LIMITING TEST")
    print("=" * 50)
    print("This test will make many rapid requests to trigger rate limiting")
    print("We'll look for 429 responses and retry-after headers")
    print()
    
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(cookies=cookies, timeout=timeout) as session:
        
        # Phase 1: Rapid-fire requests
        print("📡 Phase 1: Rapid-fire requests (no delay)")
        print("-" * 40)
        
        for i in range(20):  # 20 rapid requests
            tcin = random.choice(test_tcins)
            params = {
                'key': api_key,
                'tcin': tcin,
                'store_id': store_id,
                'pricing_store_id': store_id,
                'has_pricing_store_id': 'true',
                'visitor_id': f"{int(time.time() * 1000):016X}{''.join(random.choices('0123456789ABCDEF', k=16))}",
            }
            
            try:
                start_time = time.time()
                async with session.get(api_url, params=params, headers=headers) as response:
                    response_time = (time.time() - start_time) * 1000
                    
                    print(f"Request {i+1:2d}: Status {response.status}, Time {response_time:.0f}ms")
                    
                    # Check for rate limiting
                    if response.status == 429:
                        print("🚨 RATE LIMITED! Analyzing headers...")
                        
                        # Check for retry-after header
                        retry_after = response.headers.get('retry-after') or response.headers.get('Retry-After')
                        if retry_after:
                            print(f"🎯 RETRY-AFTER FOUND: {retry_after}")
                        
                        # Print all headers
                        print("All headers:")
                        for h, v in response.headers.items():
                            if any(keyword in h.lower() for keyword in ['retry', 'rate', 'limit', 'wait', 'reset']):
                                print(f"  🎯 {h}: {v}")
                            else:
                                print(f"     {h}: {v}")
                        
                        # Read response body
                        try:
                            body = await response.text()
                            print(f"Response body: {body}")
                        except:
                            pass
                        
                        print("⏸️  Pausing for 60 seconds after rate limiting...")
                        await asyncio.sleep(60)
                        break
                        
                    elif response.status in [403, 404, 500]:
                        print(f"🚨 Potential issue: {response.status}")
                        if response.status == 403:
                            print("  This could be IP blocking rather than rate limiting")
                            break
                            
            except Exception as e:
                print(f"Request {i+1:2d}: ERROR - {e}")
                
            # Very short delay
            await asyncio.sleep(0.05)  # 50ms delay
        
        print("\n" + "=" * 50)
        print("📡 Phase 2: Burst pattern (10 requests every 5 seconds)")
        print("-" * 40)
        
        for burst in range(5):  # 5 bursts
            print(f"\n🔥 Burst {burst+1}/5")
            
            for i in range(10):  # 10 requests per burst
                tcin = random.choice(test_tcins)
                params = {
                    'key': api_key,
                    'tcin': tcin,
                    'store_id': store_id,
                    'pricing_store_id': store_id,
                    'has_pricing_store_id': 'true',
                    'visitor_id': f"{int(time.time() * 1000):016X}{''.join(random.choices('0123456789ABCDEF', k=16))}",
                }
                
                try:
                    start_time = time.time()
                    async with session.get(api_url, params=params, headers=headers) as response:
                        response_time = (time.time() - start_time) * 1000
                        
                        status_emoji = "✅" if response.status == 200 else "❌"
                        print(f"  {status_emoji} {i+1:2d}: {response.status} ({response_time:.0f}ms)")
                        
                        if response.status == 429:
                            print("🚨 RATE LIMITED IN BURST!")
                            retry_after = response.headers.get('retry-after') or response.headers.get('Retry-After')
                            if retry_after:
                                print(f"🎯 RETRY-AFTER: {retry_after}")
                            return
                            
                        elif response.status in [403, 500]:
                            print(f"🚨 Error {response.status} - may be blocked")
                            return
                            
                except Exception as e:
                    print(f"  ❌ {i+1:2d}: ERROR - {e}")
                
                # No delay between burst requests
            
            # Wait between bursts
            if burst < 4:  # Don't wait after last burst
                print(f"  ⏳ Waiting 5 seconds before next burst...")
                await asyncio.sleep(5)
        
        print("\n" + "=" * 50)
        print("📊 SUMMARY")
        print("-" * 50)
        print("If no rate limiting was triggered:")
        print("• Target's API may have higher rate limits")
        print("• Rate limiting may be based on other factors (IP reputation, etc.)")
        print("• Current session may have special privileges")
        print("• Rate limiting may apply per IP across all endpoints")

if __name__ == "__main__":
    asyncio.run(aggressive_rate_limit_test())
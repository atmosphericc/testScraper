#!/usr/bin/env python3
"""
Analyze Target API rate limits and response patterns
"""
import requests
import time
import random
from datetime import datetime

def test_rate_limits():
    """Test different request rates to find optimal timing"""
    api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
    base_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
    test_tcin = "94724987"  # Test product
    
    test_intervals = [1, 2, 5, 10]  # Seconds between requests
    
    for interval in test_intervals:
        print(f"\n🧪 Testing {interval}s intervals ({1/interval:.1f} req/sec)...")
        
        success_count = 0
        error_count = 0
        response_times = []
        
        for i in range(5):  # Test 5 requests at this rate
            start_time = time.time()
            
            params = {
                'key': api_key,
                'tcin': test_tcin,
                'store_id': '865',
                'pricing_store_id': '865',
                'has_pricing_store_id': 'true',
                'visitor_id': ''.join(random.choices('0123456789ABCDEF', k=32)),
                'isBot': 'false'
            }
            
            headers = {
                'accept': 'application/json',
                'origin': 'https://www.target.com',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            try:
                response = requests.get(base_url, params=params, headers=headers, timeout=10)
                response_time = time.time() - start_time
                response_times.append(response_time)
                
                if response.status_code == 200:
                    success_count += 1
                    print(f"  ✅ Request {i+1}: {response.status_code} ({response_time:.2f}s)")
                    
                    # Check for any rate limiting headers
                    rate_headers = {}
                    for header in ['retry-after', 'x-ratelimit-limit', 'x-ratelimit-remaining', 'x-ratelimit-reset']:
                        if header in response.headers:
                            rate_headers[header] = response.headers[header]
                    
                    if rate_headers:
                        print(f"    📊 Rate limit headers: {rate_headers}")
                else:
                    error_count += 1
                    print(f"  ❌ Request {i+1}: {response.status_code} ({response_time:.2f}s)")
                    if response.status_code == 429:
                        print(f"    ⚠️  RATE LIMITED!")
                        if 'retry-after' in response.headers:
                            print(f"    🕒 Retry-After: {response.headers['retry-after']}s")
                            
            except Exception as e:
                error_count += 1
                print(f"  ❌ Request {i+1}: Exception - {e}")
            
            # Wait for next request (except on last iteration)
            if i < 4:
                time.sleep(interval)
        
        # Summary
        avg_response_time = sum(response_times) / len(response_times) if response_times else 0
        success_rate = (success_count / 5) * 100
        
        print(f"  📊 Summary: {success_rate:.0f}% success rate, {avg_response_time:.2f}s avg response time")
        
        if error_count > 0:
            print(f"  ⚠️  {error_count} errors detected - may be too aggressive")
        else:
            print(f"  ✅ No errors - rate appears safe")

if __name__ == "__main__":
    print("🎯 Target API Rate Limit Analysis")
    print("=" * 50)
    test_rate_limits()
    
    print(f"\n🎯 Recommendations:")
    print(f"  • Current config: 0.1 req/sec (10s intervals) - Very Conservative")
    print(f"  • Based on testing above, you can likely:")
    print(f"    - Use 2-5 second intervals for better performance")  
    print(f"    - Monitor for any 429 responses")
    print(f"    - Keep randomized delays for stealth")
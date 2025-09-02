#!/usr/bin/env python3
"""
Simple API Block Check
"""
import asyncio
import aiohttp
import time
import json
from pathlib import Path

async def quick_api_test():
    print("CHECKING TARGET API STATUS")
    print("=" * 40)
    
    # Test basic API endpoint
    url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
    params = {
        "key": "9f36aeafbe60771e321a7cc95a78140772ab3e96",
        "tcin": "89542109",
        "store_id": "865"
    }
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Origin': 'https://www.target.com',
    }
    
    try:
        print("Testing Target API...")
        start_time = time.time()
        
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params, headers=headers) as response:
                response_time = (time.time() - start_time) * 1000
                status = response.status
                
                print(f"Status Code: {status}")
                print(f"Response Time: {response_time:.0f}ms")
                
                if status == 200:
                    print("SUCCESS: API is working")
                    try:
                        data = await response.json()
                        if 'data' in data:
                            print("GOOD: Valid API response received")
                            return False  # Not blocked
                        else:
                            print("WARNING: Unusual API response structure")
                            return True
                    except:
                        print("WARNING: Could not parse JSON response")
                        return True
                        
                elif status == 403:
                    print("BLOCKED: 403 Forbidden - likely IP blocked")
                    return True
                elif status == 429:
                    print("BLOCKED: 429 Too Many Requests - rate limited")
                    return True
                else:
                    print(f"ERROR: Unexpected status {status}")
                    text = await response.text()
                    print(f"Response: {text[:200]}...")
                    return True
                    
    except asyncio.TimeoutError:
        print("TIMEOUT: Request timed out - possible blocking")
        return True
    except Exception as e:
        print(f"ERROR: {str(e)}")
        return True

async def check_session():
    print("\nCHECKING SESSION STATUS")
    print("=" * 40)
    
    session_file = Path('sessions/target_storage.json')
    if session_file.exists():
        print("Session file: EXISTS")
        try:
            with open(session_file, 'r') as f:
                session_data = json.load(f)
                cookies = session_data.get('cookies', [])
                print(f"Stored cookies: {len(cookies)}")
                
                target_cookies = [c for c in cookies if 'target.com' in c.get('domain', '')]
                print(f"Target cookies: {len(target_cookies)}")
                
                if len(target_cookies) > 5:
                    print("Session appears VALID")
                else:
                    print("Session appears INVALID or expired")
                    
        except Exception as e:
            print(f"Error reading session: {e}")
    else:
        print("Session file: NOT FOUND")
        print("Need to run: python setup.py login")

async def main():
    is_blocked = await quick_api_test()
    await check_session()
    
    print("\nSUMMARY")
    print("=" * 40)
    
    if is_blocked:
        print("STATUS: LIKELY BLOCKED")
        print("SOLUTIONS:")
        print("1. Wait 1-2 hours and try again")
        print("2. Use VPN or different IP address")
        print("3. Try mobile hotspot")
        print("4. Use proxy rotation")
    else:
        print("STATUS: API ACCESS OK")
        print("You can proceed with the monitor")
    
    return is_blocked

if __name__ == "__main__":
    blocked = asyncio.run(main())
    exit(1 if blocked else 0)
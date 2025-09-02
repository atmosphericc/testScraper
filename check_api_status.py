#!/usr/bin/env python3
"""
Quick API Block Detection Script
Tests Target's API endpoints to detect blocking
"""
import asyncio
import aiohttp
import time
import random
import json
from pathlib import Path

async def test_api_endpoints():
    """Test various Target API endpoints for blocking"""
    
    print("🔍 CHECKING TARGET API STATUS")
    print("=" * 50)
    
    # Test endpoints with different approaches
    endpoints = [
        {
            "name": "Main PDP API",
            "url": "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1",
            "params": {
                "key": "9f36aeafbe60771e321a7cc95a78140772ab3e96",
                "tcin": "89542109",  # Known product
                "store_id": "865"
            }
        },
        {
            "name": "Store Locator API", 
            "url": "https://redsky.target.com/redsky_aggregations/v1/web/store_location_v1",
            "params": {
                "key": "9f36aeafbe60771e321a7cc95a78140772ab3e96",
                "store_id": "865"
            }
        },
        {
            "name": "Target Main Site",
            "url": "https://www.target.com",
            "params": {}
        }
    ]
    
    # Different user agents to test
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ]
    
    results = []
    
    for i, endpoint in enumerate(endpoints):
        print(f"\n📡 Testing: {endpoint['name']}")
        print("-" * 30)
        
        for j, ua in enumerate(user_agents):
            headers = {
                'User-Agent': ua,
                'Accept': 'application/json' if 'redsky' in endpoint['url'] else 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            }
            
            if 'redsky' in endpoint['url']:
                headers.update({
                    'Origin': 'https://www.target.com',
                    'Sec-Fetch-Site': 'same-site',
                    'Sec-Fetch-Mode': 'cors',
                    'Sec-Fetch-Dest': 'empty',
                })
            
            try:
                timeout = aiohttp.ClientTimeout(total=10)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    start_time = time.time()
                    
                    async with session.get(
                        endpoint['url'],
                        params=endpoint['params'],
                        headers=headers
                    ) as response:
                        
                        response_time = (time.time() - start_time) * 1000
                        status = response.status
                        
                        # Try to get response content
                        try:
                            if status == 200:
                                content = await response.text()
                                content_preview = content[:200] + "..." if len(content) > 200 else content
                            else:
                                content = await response.text()
                                content_preview = content[:100] + "..." if len(content) > 100 else content
                        except:
                            content_preview = "[Unable to read response]"
                        
                        # Analyze result
                        blocked_indicators = [
                            'blocked', 'forbidden', 'access denied', 'rate limit',
                            'too many requests', 'captcha', 'bot', 'automated'
                        ]
                        
                        is_blocked = any(indicator in content_preview.lower() for indicator in blocked_indicators)
                        
                        # Status analysis
                        if status == 200:
                            status_text = "✅ SUCCESS"
                        elif status == 403:
                            status_text = "🚫 FORBIDDEN (likely blocked)"
                            is_blocked = True
                        elif status == 429:
                            status_text = "⏰ RATE LIMITED (definitely blocked)"
                            is_blocked = True
                        elif status == 404:
                            status_text = "❓ NOT FOUND"
                        elif status >= 500:
                            status_text = "🔧 SERVER ERROR"
                        else:
                            status_text = f"❓ STATUS {status}"
                        
                        print(f"   UA {j+1}: {status_text} ({response_time:.0f}ms)")
                        
                        if is_blocked:
                            print(f"        🚨 BLOCKING DETECTED: {content_preview[:50]}")
                        elif status == 200 and 'redsky' in endpoint['url']:
                            print(f"        📊 API Response OK")
                        
                        results.append({
                            'endpoint': endpoint['name'],
                            'user_agent': j+1,
                            'status': status,
                            'response_time': response_time,
                            'blocked': is_blocked,
                            'content_preview': content_preview[:100]
                        })
                        
                        # Small delay between requests
                        await asyncio.sleep(0.5)
                        
            except asyncio.TimeoutError:
                print(f"   UA {j+1}: ⏱️ TIMEOUT (possible blocking)")
                results.append({
                    'endpoint': endpoint['name'],
                    'user_agent': j+1,
                    'status': 'timeout',
                    'blocked': True,
                    'error': 'timeout'
                })
            except Exception as e:
                print(f"   UA {j+1}: ❌ ERROR - {str(e)}")
                results.append({
                    'endpoint': endpoint['name'],
                    'user_agent': j+1,
                    'status': 'error',
                    'blocked': True,
                    'error': str(e)
                })
            
        # Longer delay between endpoints
        await asyncio.sleep(2)
    
    # Analysis
    print(f"\n📊 ANALYSIS")
    print("=" * 50)
    
    blocked_count = sum(1 for r in results if r.get('blocked', False))
    total_tests = len(results)
    success_count = sum(1 for r in results if r.get('status') == 200)
    
    print(f"Total tests: {total_tests}")
    print(f"Successful: {success_count}")
    print(f"Blocked/Failed: {blocked_count}")
    print(f"Block rate: {blocked_count/total_tests*100:.1f}%")
    
    if blocked_count > total_tests * 0.5:
        print("\n🚨 LIKELY BLOCKED:")
        print("   - High failure rate detected")
        print("   - Try using proxy or wait before retrying")
        print("   - Check if VPN is needed")
    elif blocked_count > 0:
        print("\n⚠️ PARTIAL BLOCKING:")
        print("   - Some requests failing")
        print("   - May need to adjust user agents or timing")
    else:
        print("\n✅ API ACCESS LOOKS GOOD:")
        print("   - No blocking detected")
        print("   - All endpoints responding normally")
    
    # Check if session exists and test it
    session_file = Path('sessions/target_storage.json')
    if session_file.exists():
        print(f"\n🔐 SESSION STATUS:")
        print(f"   Session file exists: ✅")
        try:
            with open(session_file, 'r') as f:
                session_data = json.load(f)
                cookies = session_data.get('cookies', [])
                print(f"   Stored cookies: {len(cookies)}")
                
                # Check for key Target cookies
                target_cookies = [c for c in cookies if 'target.com' in c.get('domain', '')]
                print(f"   Target cookies: {len(target_cookies)}")
                
                if target_cookies:
                    print("   ✅ Session appears valid")
                else:
                    print("   ❌ No Target cookies found - session may be invalid")
                    
        except Exception as e:
            print(f"   ❌ Error reading session: {e}")
    else:
        print(f"\n🔐 SESSION STATUS:")
        print(f"   No session file found")
        print(f"   Run: python setup.py login")
    
    # Recommendations
    print(f"\n💡 RECOMMENDATIONS:")
    if blocked_count > total_tests * 0.7:
        print("   1. Wait 1-2 hours before retrying")
        print("   2. Consider using a VPN or proxy")
        print("   3. Try from a different IP address")
        print("   4. Use residential proxy if available")
    elif blocked_count > 0:
        print("   1. Add delays between requests")
        print("   2. Rotate user agents more frequently")
        print("   3. Consider using proxy rotation")
    else:
        print("   1. API access is working normally")
        print("   2. You can proceed with monitoring")
    
    return blocked_count / total_tests

if __name__ == "__main__":
    print("Target API Block Detection")
    print("Checking multiple endpoints with different user agents...")
    
    block_rate = asyncio.run(test_api_endpoints())
    
    if block_rate > 0.5:
        print(f"\n🚨 HIGH BLOCK RATE: {block_rate*100:.1f}%")
        exit(1)
    else:
        print(f"\n✅ LOW BLOCK RATE: {block_rate*100:.1f}%")
        exit(0)
#!/usr/bin/env python3
"""
Free Proxy Fetcher - USE AT YOUR OWN RISK
WARNING: Free proxies are unreliable and potentially unsafe
"""
import requests
import json
import asyncio
import aiohttp
from pathlib import Path

def get_free_proxies():
    """
    Get free proxies from public APIs
    WARNING: These are public, untrusted, and often malicious
    """
    print("WARNING: Free proxies are unreliable and potentially unsafe!")
    print("Consider using a VPN service instead.")
    print("Continue? (y/N): ", end="")
    
    response = input().strip().lower()
    if response != 'y':
        print("Aborted. Recommend using VPN or mobile hotspot instead.")
        return []
    
    proxies = []
    
    # Try ProxyList API (one of the more reliable free sources)
    try:
        print("Fetching from ProxyList API...")
        url = "https://www.proxy-list.download/api/v1/get?type=http&anon=elite&country=US"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            proxy_list = response.text.strip().split('\n')
            for proxy in proxy_list[:5]:  # Only take first 5
                if ':' in proxy:
                    host, port = proxy.split(':')
                    proxies.append({
                        "host": host,
                        "port": int(port),
                        "username": "",
                        "password": "",
                        "protocol": "http",
                        "enabled": True,
                        "notes": "FREE PROXY - USE AT YOUR OWN RISK"
                    })
                    
    except Exception as e:
        print(f"Failed to get proxies: {e}")
    
    return proxies

async def test_proxy(proxy):
    """Test if a proxy actually works"""
    proxy_url = f"http://{proxy['host']}:{proxy['port']}"
    
    try:
        connector = aiohttp.ProxyConnector.from_url(proxy_url)
        timeout = aiohttp.ClientTimeout(total=10)
        
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            # Test with a simple IP check
            async with session.get('http://httpbin.org/ip') as response:
                if response.status == 200:
                    data = await response.json()
                    print(f"✓ {proxy['host']}:{proxy['port']} - Working (IP: {data.get('origin', 'unknown')})")
                    return True
                else:
                    print(f"✗ {proxy['host']}:{proxy['port']} - Failed ({response.status})")
                    return False
                    
    except Exception as e:
        print(f"✗ {proxy['host']}:{proxy['port']} - Error: {str(e)[:50]}...")
        return False

async def main():
    print("FREE PROXY SETUP")
    print("=" * 50)
    print("⚠️  WARNING: This uses free, public proxies that:")
    print("   - May log or steal your data")
    print("   - Are often unreliable/slow")
    print("   - May be blocked by Target")
    print("   - Could compromise your security")
    print()
    print("RECOMMENDED ALTERNATIVES:")
    print("   - Use VPN service (ProtonVPN free tier)")
    print("   - Continue with mobile hotspot")
    print("   - Wait for home IP block to clear")
    print()
    
    # Get free proxies
    proxies = get_free_proxies()
    
    if not proxies:
        print("No proxies obtained. Exiting.")
        return
    
    print(f"\nTesting {len(proxies)} free proxies...")
    print("(This may take 30-60 seconds)")
    
    # Test proxies
    working_proxies = []
    for proxy in proxies:
        is_working = await test_proxy(proxy)
        if is_working:
            working_proxies.append(proxy)
    
    if not working_proxies:
        print("\n❌ No working proxies found.")
        print("Recommendation: Use VPN or mobile hotspot instead.")
        return
    
    print(f"\n✅ Found {len(working_proxies)} working proxies")
    
    # Update config file
    config_path = Path('config/product_config.json')
    if config_path.exists():
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        # Replace proxies section
        config['proxies'] = working_proxies
        
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        
        print(f"✅ Updated config with {len(working_proxies)} working proxies")
        print("\nNOTE: These proxies may:")
        print("- Stop working at any time")  
        print("- Be slow or unreliable")
        print("- Log your requests")
        print("\nFor production use, consider paid proxy services.")
        
    else:
        print("❌ Config file not found")

if __name__ == "__main__":
    print("FREE PROXY FETCHER")
    print("USE AT YOUR OWN RISK")
    print()
    asyncio.run(main())
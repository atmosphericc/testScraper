#!/usr/bin/env python3
"""
Simple Free Proxy Setup
WARNING: Use at your own risk
"""
import requests
import json
import asyncio
import aiohttp
from pathlib import Path

def get_free_proxies():
    """Get free proxies from public APIs"""
    print("WARNING: Free proxies are potentially unsafe and unreliable!")
    print("They may log your data or be controlled by malicious actors.")
    print("Continue anyway? (type 'yes' to continue): ", end="")
    
    response = input().strip()
    if response.lower() != 'yes':
        print("Aborted.")
        return []
    
    proxies = []
    
    try:
        print("Fetching free proxies...")
        
        # Try free proxy API
        url = "https://api.proxyscrape.com/v2/?request=get&protocol=http&timeout=10000&country=US&format=textplain&limit=10"
        response = requests.get(url, timeout=15)
        
        if response.status_code == 200:
            proxy_list = response.text.strip().split('\n')
            for proxy in proxy_list:
                if ':' in proxy and len(proxy.split(':')) == 2:
                    host, port = proxy.split(':')
                    try:
                        port = int(port)
                        proxies.append({
                            "host": host.strip(),
                            "port": port,
                            "username": "",
                            "password": "",
                            "protocol": "http",
                            "enabled": True,
                            "notes": "FREE PROXY - UNRELIABLE"
                        })
                    except ValueError:
                        continue
                        
        print(f"Found {len(proxies)} potential proxies")
                        
    except Exception as e:
        print(f"Failed to fetch proxies: {e}")
    
    # Add some known working proxy services (often work temporarily)
    backup_proxies = [
        {"host": "8.210.83.33", "port": 80, "username": "", "password": "", "protocol": "http", "enabled": True, "notes": "FREE PROXY"},
        {"host": "43.134.68.153", "port": 3128, "username": "", "password": "", "protocol": "http", "enabled": True, "notes": "FREE PROXY"},
        {"host": "185.162.231.106", "port": 80, "username": "", "password": "", "protocol": "http", "enabled": True, "notes": "FREE PROXY"},
    ]
    
    proxies.extend(backup_proxies)
    return proxies[:10]  # Limit to 10 proxies

async def test_proxy(proxy):
    """Test if proxy works"""
    proxy_url = f"http://{proxy['host']}:{proxy['port']}"
    
    try:
        connector = aiohttp.ProxyConnector.from_url(proxy_url)
        timeout = aiohttp.ClientTimeout(total=8)
        
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get('http://httpbin.org/ip') as response:
                if response.status == 200:
                    data = await response.json()
                    print(f"WORKING: {proxy['host']}:{proxy['port']} (IP: {data.get('origin', 'unknown')})")
                    return True
                else:
                    print(f"FAILED: {proxy['host']}:{proxy['port']} - Status {response.status}")
                    return False
                    
    except Exception as e:
        print(f"FAILED: {proxy['host']}:{proxy['port']} - {str(e)[:30]}...")
        return False

async def main():
    print("FREE PROXY SETUP")
    print("=" * 40)
    print("WARNING: Free proxies may be unsafe or unreliable")
    print("Better alternatives: VPN service or mobile hotspot")
    print()
    
    proxies = get_free_proxies()
    
    if not proxies:
        print("No proxies to test")
        return
    
    print(f"Testing {len(proxies)} proxies...")
    
    # Test all proxies
    working_proxies = []
    for proxy in proxies:
        is_working = await test_proxy(proxy)
        if is_working:
            working_proxies.append(proxy)
    
    print(f"\nResult: {len(working_proxies)} working proxies found")
    
    if not working_proxies:
        print("No working proxies found.")
        print("Recommendation: Use VPN or mobile hotspot instead")
        return
    
    # Update config
    config_path = Path('config/product_config.json')
    if config_path.exists():
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        config['proxies'] = working_proxies
        
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        
        print(f"Updated config with {len(working_proxies)} proxies")
        print("\nNow you can run: python run.py --dashboard")
        print("NOTE: Free proxies often stop working quickly")
        
    else:
        print("Config file not found")

if __name__ == "__main__":
    asyncio.run(main())
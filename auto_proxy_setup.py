#!/usr/bin/env python3
"""
Automatic Free Proxy Setup - No confirmation needed
"""
import requests
import json
import asyncio
import aiohttp
from pathlib import Path

def get_free_proxies():
    """Get free proxies automatically"""
    print("Getting free proxies...")
    
    proxies = []
    
    # List of free proxy IPs (these change frequently)
    free_proxy_list = [
        "8.210.83.33:80",
        "43.134.68.153:3128", 
        "185.162.231.106:80",
        "103.152.112.162:80",
        "194.195.213.197:1080",
        "103.148.72.192:80",
        "194.233.69.90:443",
        "185.180.199.75:8080",
        "91.107.6.115:53281",
        "188.132.222.7:8080"
    ]
    
    # Try fetching more from API
    try:
        url = "https://api.proxyscrape.com/v2/?request=get&protocol=http&timeout=5000&country=US&format=textplain&limit=5"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            api_proxies = response.text.strip().split('\n')
            free_proxy_list.extend(api_proxies[:5])
            
    except:
        pass  # Use hardcoded list if API fails
    
    # Convert to config format
    for proxy_str in free_proxy_list:
        if ':' in proxy_str:
            try:
                host, port = proxy_str.split(':')
                proxies.append({
                    "host": host.strip(),
                    "port": int(port.strip()),
                    "username": "",
                    "password": "",
                    "protocol": "http",
                    "enabled": True,
                    "notes": "FREE PROXY - MAY BE UNRELIABLE"
                })
            except:
                continue
    
    return proxies[:8]  # Limit to 8 proxies

async def test_proxy_quick(proxy):
    """Quick proxy test"""
    try:
        proxy_url = f"http://{proxy['host']}:{proxy['port']}"
        connector = aiohttp.ProxyConnector.from_url(proxy_url)
        timeout = aiohttp.ClientTimeout(total=5)
        
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get('http://httpbin.org/ip') as response:
                if response.status == 200:
                    return True
    except:
        pass
    
    return False

async def setup_proxies():
    print("AUTOMATIC FREE PROXY SETUP")
    print("=" * 40)
    
    proxies = get_free_proxies()
    print(f"Testing {len(proxies)} free proxies...")
    
    # Test proxies quickly
    working_proxies = []
    for i, proxy in enumerate(proxies):
        print(f"Testing proxy {i+1}/{len(proxies)}: {proxy['host']}:{proxy['port']}")
        
        is_working = await test_proxy_quick(proxy)
        if is_working:
            print(f"  WORKING")
            working_proxies.append(proxy)
        else:
            print(f"  FAILED")
    
    print(f"\nFound {len(working_proxies)} working proxies")
    
    if working_proxies:
        # Update config file
        config_path = Path('config/product_config.json')
        if config_path.exists():
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            config['proxies'] = working_proxies
            
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)
            
            print(f"SUCCESS: Updated config with {len(working_proxies)} working proxies")
            print("\nNext steps:")
            print("1. Run: python simple_api_check.py")
            print("2. If working: python run.py --dashboard")
            
        else:
            print("ERROR: Config file not found")
            
    else:
        print("No working proxies found")
        print("Recommendation: Use VPN or mobile hotspot instead")

if __name__ == "__main__":
    asyncio.run(setup_proxies())
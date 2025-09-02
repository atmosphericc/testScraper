#!/usr/bin/env python3
"""
Advanced Free Proxy Finder
Scrapes multiple sources and finds actually working proxies
"""
import requests
import asyncio
import aiohttp
import json
import time
from pathlib import Path
import random

class ProxyFinder:
    def __init__(self):
        self.working_proxies = []
        self.sources = [
            "https://api.proxyscrape.com/v2/?request=get&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all&format=textplain",
            "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
            "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
            "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
            "https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt"
        ]
    
    def fetch_proxies_from_sources(self):
        """Fetch proxies from multiple sources"""
        all_proxies = set()
        
        for source in self.sources:
            try:
                print(f"Fetching from: {source.split('/')[-2] if 'github' in source else 'API'}")
                response = requests.get(source, timeout=10)
                
                if response.status_code == 200:
                    proxies = response.text.strip().split('\n')
                    for proxy in proxies:
                        if ':' in proxy and len(proxy.split(':')) == 2:
                            try:
                                host, port = proxy.strip().split(':')
                                port = int(port)
                                if 1 <= port <= 65535:
                                    all_proxies.add(f"{host}:{port}")
                            except:
                                continue
                                
                    print(f"  Found {len(proxies)} proxies")
                else:
                    print(f"  Failed: {response.status_code}")
                    
            except Exception as e:
                print(f"  Error: {str(e)[:50]}")
                
            time.sleep(1)  # Be nice to servers
        
        print(f"\nTotal unique proxies collected: {len(all_proxies)}")
        return list(all_proxies)[:50]  # Limit to 50 for testing
    
    async def test_proxy_advanced(self, proxy_str):
        """Advanced proxy testing with multiple checks"""
        try:
            host, port = proxy_str.split(':')
            port = int(port)
            proxy_url = f"http://{host}:{port}"
            
            connector = aiohttp.ProxyConnector.from_url(proxy_url)
            timeout = aiohttp.ClientTimeout(total=8)
            
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                # Test 1: Basic IP check
                async with session.get('http://httpbin.org/ip') as response:
                    if response.status != 200:
                        return False
                    
                    data = await response.json()
                    proxy_ip = data.get('origin', '').split(',')[0].strip()
                    
                    # Test 2: Speed test
                    start = time.time()
                    async with session.get('http://httpbin.org/get') as speed_test:
                        if speed_test.status != 200:
                            return False
                    speed = time.time() - start
                    
                    # Test 3: Target-like request
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                        'Accept': 'application/json'
                    }
                    async with session.get('https://httpbin.org/headers', headers=headers) as target_test:
                        if target_test.status != 200:
                            return False
                    
                    # Proxy is working if we get here
                    return {
                        'host': host,
                        'port': port,
                        'ip': proxy_ip,
                        'speed': round(speed, 2),
                        'working': True
                    }
                    
        except Exception as e:
            return False
    
    async def find_working_proxies(self):
        """Find actually working proxies"""
        print("\nADVANCED PROXY FINDER")
        print("=" * 50)
        print("Searching multiple sources for working proxies...")
        
        # Fetch from all sources
        proxy_list = self.fetch_proxies_from_sources()
        
        if not proxy_list:
            print("No proxies found from sources")
            return []
        
        print(f"\nTesting {len(proxy_list)} proxies (this may take 5-10 minutes)...")
        print("Looking for fast, reliable proxies...")
        
        # Test proxies in batches to avoid overwhelming
        batch_size = 10
        working_proxies = []
        
        for i in range(0, len(proxy_list), batch_size):
            batch = proxy_list[i:i+batch_size]
            print(f"\nTesting batch {i//batch_size + 1} ({len(batch)} proxies)...")
            
            # Test batch concurrently
            tasks = [self.test_proxy_advanced(proxy) for proxy in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for proxy, result in zip(batch, results):
                if result and isinstance(result, dict) and result.get('working'):
                    print(f"  WORKING: {proxy} (IP: {result['ip']}, Speed: {result['speed']}s)")
                    working_proxies.append({
                        "host": result['host'],
                        "port": result['port'],
                        "username": "",
                        "password": "",
                        "protocol": "http",
                        "enabled": True,
                        "notes": f"Free proxy - Speed: {result['speed']}s, IP: {result['ip']}"
                    })
                else:
                    print(f"  FAILED: {proxy}")
            
            # Don't overwhelm servers
            await asyncio.sleep(2)
        
        return working_proxies
    
    async def setup_config(self, working_proxies):
        """Update config with working proxies"""
        if not working_proxies:
            print("\nNo working proxies found")
            return False
        
        config_path = Path('config/product_config.json')
        if not config_path.exists():
            print("Config file not found")
            return False
        
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        # Keep only the best proxies (fastest)
        working_proxies.sort(key=lambda x: float(x['notes'].split('Speed: ')[1].split('s')[0]))
        best_proxies = working_proxies[:5]  # Top 5 fastest
        
        config['proxies'] = best_proxies
        
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        
        print(f"\n✅ SUCCESS: Added {len(best_proxies)} working proxies to config")
        print("\nProxies added:")
        for i, proxy in enumerate(best_proxies, 1):
            print(f"  {i}. {proxy['host']}:{proxy['port']} - {proxy['notes']}")
        
        print("\nNext steps:")
        print("1. python simple_api_check.py  # Test if API blocking is bypassed")
        print("2. python run.py --dashboard    # Run with proxy rotation")
        
        return True

async def main():
    finder = ProxyFinder()
    
    print("FREE PROXY FINDER - ADVANCED VERSION")
    print("Searching GitHub repositories and APIs for fresh proxies...")
    print("This will take 5-10 minutes but finds actually working proxies.")
    print()
    
    working_proxies = await finder.find_working_proxies()
    
    if working_proxies:
        print(f"\n🎉 FOUND {len(working_proxies)} WORKING PROXIES!")
        await finder.setup_config(working_proxies)
    else:
        print("\n😞 No working proxies found")
        print("Even with advanced search, free proxies are unreliable")
        print("Recommendation: Use VPN service or mobile hotspot")

if __name__ == "__main__":
    asyncio.run(main())
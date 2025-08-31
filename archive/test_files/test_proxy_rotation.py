"""
Test proxy rotation functionality
Run this to verify your proxy setup is working
"""
import asyncio
import sys
import json
from pathlib import Path
sys.path.append('src')

from stock_checker import StockChecker

async def test_proxy_rotation():
    print("Testing Proxy Rotation System")
    print("=" * 50)
    
    # Test with example proxies (these won't work - just for testing structure)
    test_proxies = [
        {
            "host": "proxy1.example.com",
            "port": 8080,
            "username": "test_user",
            "password": "test_pass",
            "protocol": "http",
            "enabled": True
        },
        {
            "host": "proxy2.example.com", 
            "port": 8081,
            "username": "test_user2",
            "password": "test_pass2",
            "protocol": "http",
            "enabled": True
        }
    ]
    
    # Initialize stock checker with test proxies
    checker = StockChecker(proxies=test_proxies)
    
    print(f"StockChecker initialized with {len(test_proxies)} proxies")
    
    # Test proxy selection
    for i in range(5):
        proxy_info = checker.get_best_proxy()
        if proxy_info:
            proxy_dict, proxy_index = proxy_info
            print(f"Round {i+1}: Selected proxy {proxy_index} - {proxy_dict['host']}:{proxy_dict['port']}")
            
            # Simulate some results
            if i % 2 == 0:
                checker.report_proxy_result(proxy_index, success=True)
                print(f"   SUCCESS: Simulated success")
            else:
                checker.report_proxy_result(proxy_index, success=False)
                print(f"   FAILURE: Simulated failure")
        else:
            print(f"Round {i+1}: No proxy available")
    
    # Show proxy stats
    print("\nProxy Statistics:")
    for i, proxy in enumerate(test_proxies):
        if i in checker.proxy_stats:
            stats = checker.proxy_stats[i]
            success_rate = stats['success'] / (stats['success'] + stats['failure']) if (stats['success'] + stats['failure']) > 0 else 0
            print(f"   Proxy {i} ({proxy['host']}): Success rate: {success_rate:.1%}, Blocked until: {stats['blocked_until']}")
    
    print("\nTo use real proxies:")
    print("1. Edit config/product_config.json")
    print("2. Replace example proxies with real residential proxies")  
    print("3. Set 'enabled': true for each proxy you want to use")
    print("4. Proxies support HTTP, HTTPS, and SOCKS5 protocols")
    
    print("\nRecommended proxy services:")
    print("- Residential proxies (recommended)")
    print("- Datacenter proxies (cheaper but more detectable)")
    print("- Avoid free proxies (unreliable)")

def test_config_loading():
    """Test loading proxies from config"""
    print("\nTesting Config Loading...")
    
    try:
        config_path = Path('config/product_config.json')
        if config_path.exists():
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            if 'proxies' in config:
                enabled_proxies = [p for p in config['proxies'] if p.get('enabled', False)]
                print(f"Found {len(config['proxies'])} total proxies in config")
                print(f"{len(enabled_proxies)} proxies are enabled")
                
                for i, proxy in enumerate(enabled_proxies):
                    print(f"   Proxy {i+1}: {proxy['host']}:{proxy['port']} ({proxy.get('protocol', 'http')})")
            else:
                print("No 'proxies' section found in config")
        else:
            print("Config file not found")
            
    except Exception as e:
        print(f"Error loading config: {e}")

if __name__ == "__main__":
    asyncio.run(test_proxy_rotation())
    test_config_loading()
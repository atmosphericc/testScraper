#!/usr/bin/env python3
"""
ADVANCED STOCK MONITOR - Production-Ready Anti-Detection System
Integrates all advanced evasion techniques for undetectable stock monitoring

Usage:
    python advanced_stock_monitor.py --tcin 89542109
    python advanced_stock_monitor.py --tcin 89542109 --proxy-config proxies.json
    python advanced_stock_monitor.py --batch tcins.txt
"""

import asyncio
import sys
import os
import json
import argparse
import time
from typing import Dict, List, Optional

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from ultra_stealth_bypass import UltraStealthBypass
from advanced_evasion_engine import AdvancedEvasionEngine
from residential_proxy_network import ResidentialProxyNetwork

class AdvancedStockMonitor:
    """Production-ready advanced stock monitor"""
    
    def __init__(self, use_proxies: bool = False, proxy_config_file: str = None):
        self.ultra_stealth = UltraStealthBypass()
        self.advanced_evasion = AdvancedEvasionEngine()
        self.proxy_network = ResidentialProxyNetwork()
        self.use_proxies = use_proxies
        
        # Load proxy configuration if provided
        if proxy_config_file and os.path.exists(proxy_config_file):
            self.load_proxy_config(proxy_config_file)
    
    def load_proxy_config(self, config_file: str):
        """Load proxy configuration from JSON file"""
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
            
            for proxy_config in config.get('proxies', []):
                if proxy_config.get('enabled', True):
                    self.proxy_network.add_proxy_config(proxy_config)
            
            print(f"Loaded {len(config.get('proxies', []))} proxies from {config_file}")
            
        except Exception as e:
            print(f"Error loading proxy config: {e}")
    
    async def check_single_tcin(self, tcin: str, method: str = 'ultra_stealth') -> Dict:
        """Check single TCIN with specified method"""
        
        print(f"\nChecking TCIN: {tcin}")
        print(f"Method: {method}")
        print("-" * 40)
        
        start_time = time.time()
        
        try:
            if method == 'ultra_stealth':
                # Get proxy if available
                proxy_config = None
                if self.use_proxies:
                    best_proxy = await self.proxy_network.get_best_proxy()
                    if best_proxy:
                        proxy_config = {'url': best_proxy.url}
                        print(f"Using proxy: {best_proxy.id}")
                
                result = await self.ultra_stealth.check_stock_ultra_stealth(
                    tcin, 
                    proxy_config=proxy_config, 
                    warm_proxy=bool(proxy_config)
                )
                
            elif method == 'advanced_evasion':
                result = await self.advanced_evasion.check_stock_advanced(tcin, warm_session=True)
            
            else:
                raise ValueError(f"Unknown method: {method}")
            
            end_time = time.time()
            
            # Print results
            print(f"Status: {result['status']}")
            print(f"Available: {'YES' if result.get('available') else 'NO'}")
            print(f"Response Time: {end_time - start_time:.2f}s")
            
            if 'name' in result:
                print(f"Product: {result['name']}")
            
            if 'price' in result:
                print(f"Price: ${result['price']}")
            
            if 'error' in result:
                print(f"Error: {result['error']}")
                if result.get('http_code'):
                    print(f"HTTP Code: {result['http_code']}")
            
            # Advanced metadata
            if 'stealth_metadata' in result:
                meta = result['stealth_metadata']
                print(f"Anti-bot params: {meta['anti_bot_params_used']}")
                print(f"Success rate: {meta['success_rate']:.1%}")
            
            if 'confidence' in result:
                print(f"Confidence: {result['confidence']}")
            
            return result
            
        except Exception as e:
            print(f"Error checking {tcin}: {e}")
            return {
                'tcin': tcin,
                'available': False,
                'status': 'error',
                'error': str(e)
            }
    
    async def check_batch_tcins(self, tcins: List[str], method: str = 'ultra_stealth') -> List[Dict]:
        """Check multiple TCINs in batch"""
        
        print(f"\nBatch checking {len(tcins)} TCINs")
        print(f"Method: {method}")
        print("=" * 50)
        
        results = []
        
        for i, tcin in enumerate(tcins, 1):
            print(f"\n[{i}/{len(tcins)}] Processing {tcin}")
            
            result = await self.check_single_tcin(tcin, method)
            results.append(result)
            
            # Delay between requests for safety
            if i < len(tcins):  # Don't delay after the last item
                delay = 5.0  # 5 second delay between requests
                print(f"Waiting {delay}s before next request...")
                await asyncio.sleep(delay)
        
        # Summary
        print("\n" + "=" * 50)
        print("BATCH RESULTS SUMMARY")
        print("=" * 50)
        
        available_count = sum(1 for r in results if r.get('available'))
        
        print(f"Total checked: {len(results)}")
        print(f"Available: {available_count}")
        print(f"Unavailable: {len(results) - available_count}")
        
        print(f"\n{'TCIN':<12} {'Status':<15} {'Available':<10} {'Product'}")
        print("-" * 70)
        
        for result in results:
            status = result['status']
            available = 'YES' if result.get('available') else 'NO'
            name = result.get('name', 'Unknown')[:30]
            
            print(f"{result['tcin']:<12} {status:<15} {available:<10} {name}")
        
        return results
    
    def print_system_info(self):
        """Print system information and capabilities"""
        
        print("ADVANCED STOCK MONITOR")
        print("=" * 50)
        
        # Check capabilities
        capabilities = []
        
        try:
            from curl_cffi import requests
            capabilities.append("✓ curl_cffi (Advanced TLS spoofing)")
        except ImportError:
            capabilities.append("✗ curl_cffi (install: pip install curl-cffi)")
        
        try:
            import aiohttp
            capabilities.append("✓ aiohttp (Async HTTP)")
        except ImportError:
            capabilities.append("✗ aiohttp")
        
        print("System Capabilities:")
        for cap in capabilities:
            print(f"  {cap}")
        
        # Proxy info
        if self.use_proxies:
            stats = self.proxy_network.get_network_stats()
            print(f"\nProxy Network:")
            print(f"  Total proxies: {stats['network_overview']['total_proxies']}")
            print(f"  Available: {stats['network_overview']['available_proxies']}")
        else:
            print(f"\nProxy Network: Disabled (direct connection)")
        
        print(f"\nAdvanced Features:")
        print(f"  ✓ JA3/JA4 TLS fingerprint spoofing")
        print(f"  ✓ Browser fingerprint randomization")
        print(f"  ✓ Request pattern obfuscation")
        print(f"  ✓ Anti-bot parameter injection (isBot=false)")
        print(f"  ✓ Intelligent adaptive timing")
        print(f"  ✓ Session warming and rotation")

def main():
    """Main CLI interface"""
    
    parser = argparse.ArgumentParser(description='Advanced Stock Monitor - Anti-Detection System')
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--tcin', help='Single TCIN to check')
    group.add_argument('--batch', help='File containing TCINs (one per line)')
    
    parser.add_argument('--method', choices=['ultra_stealth', 'advanced_evasion'], 
                       default='ultra_stealth', help='Detection method to use')
    parser.add_argument('--proxy-config', help='JSON file with proxy configuration')
    parser.add_argument('--info', action='store_true', help='Show system information')
    
    args = parser.parse_args()
    
    # Initialize monitor
    use_proxies = bool(args.proxy_config)
    monitor = AdvancedStockMonitor(use_proxies=use_proxies, proxy_config_file=args.proxy_config)
    
    if args.info:
        monitor.print_system_info()
        return
    
    async def run_monitor():
        if args.tcin:
            # Single TCIN check
            await monitor.check_single_tcin(args.tcin, args.method)
            
        elif args.batch:
            # Batch check
            if not os.path.exists(args.batch):
                print(f"Error: File not found: {args.batch}")
                return
            
            with open(args.batch, 'r') as f:
                tcins = [line.strip() for line in f if line.strip()]
            
            if not tcins:
                print(f"Error: No TCINs found in {args.batch}")
                return
            
            await monitor.check_batch_tcins(tcins, args.method)
    
    try:
        asyncio.run(run_monitor())
    except KeyboardInterrupt:
        print("\nMonitoring interrupted by user")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
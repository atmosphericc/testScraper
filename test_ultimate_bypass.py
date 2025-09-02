#!/usr/bin/env python3
"""
ULTIMATE BYPASS TEST - Integration of All Advanced Evasion Techniques
This script demonstrates the complete advanced anti-detection system

Run with: python test_ultimate_bypass.py
"""

import asyncio
import sys
import os
import time
import json
from typing import Dict, List

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from ultra_stealth_bypass import UltraStealthBypass
from advanced_evasion_engine import AdvancedEvasionEngine
from residential_proxy_network import ResidentialProxyNetwork

class UltimateBypassTester:
    """Test suite for ultimate bypass capabilities"""
    
    def __init__(self):
        self.ultra_stealth = UltraStealthBypass()
        self.advanced_evasion = AdvancedEvasionEngine()
        self.proxy_network = ResidentialProxyNetwork()
        
        # Test TCINs - replace with current valid ones
        self.test_tcins = [
            "89542109",  # Your selected TCIN
            "94681785",  # Alternative test TCIN
            "94724987"   # Another test TCIN
        ]
    
    async def run_comprehensive_test(self):
        """Run comprehensive bypass test suite"""
        
        print("ULTIMATE BYPASS SYSTEM TEST")
        print("=" * 50)
        
        # Test 1: Ultra Stealth Bypass (no proxy)
        print("\nTEST 1: Ultra Stealth Bypass (Direct)")
        await self._test_ultra_stealth_direct()
        
        # Test 2: Advanced Evasion Engine
        print("\nTEST 2: Advanced Evasion Engine")
        await self._test_advanced_evasion()
        
        # Test 3: With Residential Proxies (if available)
        print("\nTEST 3: Residential Proxy Network")
        await self._test_with_proxies()
        
        # Test 4: Comparison Test
        print("\nTEST 4: Performance Comparison")
        await self._run_comparison_test()
        
        print("\n" + "=" * 50)
        print("ULTIMATE BYPASS TEST COMPLETE")
    
    async def _test_ultra_stealth_direct(self):
        """Test ultra stealth without proxies"""
        
        tcin = self.test_tcins[0]
        
        print(f"Testing TCIN: {tcin}")
        print(f"Profile: {self.ultra_stealth.current_profile.browser} {self.ultra_stealth.current_profile.version}")
        print(f"TLS Profile: Advanced TLS spoofing enabled")
        
        start_time = time.time()
        result = await self.ultra_stealth.check_stock_ultra_stealth(tcin, warm_proxy=False)
        end_time = time.time()
        
        print(f"\nRESULTS:")
        print(f"Status: {result['status']}")
        print(f"Available: {result.get('available', 'N/A')}")
        print(f"Response Time: {end_time - start_time:.2f}s")
        
        if 'error' in result:
            print(f"Error: {result['error']}")
            if result.get('http_code'):
                print(f"HTTP Code: {result['http_code']}")
        
        if 'stealth_metadata' in result:
            meta = result['stealth_metadata']
            print(f"\nSTEALTH METADATA:")
            print(f"Anti-bot params used: {meta['anti_bot_params_used']}")
            print(f"Success rate: {meta['success_rate']:.1%}")
        
        return result
    
    async def _test_advanced_evasion(self):
        """Test advanced evasion engine"""
        
        tcin = self.test_tcins[1] if len(self.test_tcins) > 1 else self.test_tcins[0]
        
        print(f"Testing TCIN: {tcin}")
        print(f"Fingerprint: {self.advanced_evasion.fingerprint.browser_type}")
        print(f"Behavioral Pattern: {self.advanced_evasion.behavioral_pattern}")
        
        start_time = time.time()
        result = await self.advanced_evasion.check_stock_advanced(tcin, warm_session=True)
        end_time = time.time()
        
        print(f"\nRESULTS:")
        print(f"Status: {result['status']}")
        print(f"Available: {result.get('available', 'N/A')}")
        print(f"Response Time: {end_time - start_time:.2f}s")
        
        if 'error' in result:
            print(f"Error: {result['error']}")
        
        # Show advanced metadata
        if 'fingerprint_used' in result:
            print(f"Browser Used: {result['fingerprint_used']}")
        
        if 'behavioral_pattern' in result:
            print(f"Behavioral Pattern: {result['behavioral_pattern']}")
        
        return result
    
    async def _test_with_proxies(self):
        """Test with residential proxy network"""
        
        # Add example proxies (replace with real ones)
        print("Note: Add real residential proxies for full testing")
        
        # Example proxy config (won't work without real proxies)
        example_proxies = [
            {
                'host': '192.168.1.100',  # Replace with real proxy
                'port': 8080,
                'username': 'your_username',
                'password': 'your_password',
                'provider': 'YourProvider',
                'country': 'US',
                'state': 'CA',
                'city': 'Los Angeles'
            }
        ]
        
        # Add proxies if available
        for proxy_config in example_proxies:
            self.proxy_network.add_proxy_config(proxy_config)
        
        # Show network stats
        stats = self.proxy_network.get_network_stats()
        print(f"Proxy Network: {stats['network_overview']['total_proxies']} proxies")
        
        if stats['network_overview']['total_proxies'] > 0:
            # Try to get best proxy
            best_proxy = await self.proxy_network.get_best_proxy()
            if best_proxy:
                print(f"Best Proxy: {best_proxy.id} ({best_proxy.city}, {best_proxy.country})")
                
                # Test with proxy
                tcin = self.test_tcins[2] if len(self.test_tcins) > 2 else self.test_tcins[0]
                
                proxy_config = {'url': best_proxy.url}
                
                result = await self.ultra_stealth.check_stock_ultra_stealth(
                    tcin, 
                    proxy_config=proxy_config, 
                    warm_proxy=True
                )
                
                print(f"Proxy Test Result: {result['status']}")
                return result
        
        print("Warning: No working proxies available for testing")
        return None
    
    async def _run_comparison_test(self):
        """Run comparison between different methods"""
        
        tcin = self.test_tcins[0]
        methods = []
        
        print(f"Comparing methods for TCIN: {tcin}")
        
        # Method 1: Ultra Stealth
        print("\nMethod 1: Ultra Stealth")
        start_time = time.time()
        result1 = await self.ultra_stealth.check_stock_ultra_stealth(tcin, warm_proxy=False)
        time1 = time.time() - start_time
        
        methods.append({
            'name': 'Ultra Stealth',
            'time': time1,
            'status': result1['status'],
            'available': result1.get('available', False),
            'error': result1.get('error', None)
        })
        
        # Method 2: Advanced Evasion
        print("\nMethod 2: Advanced Evasion")
        start_time = time.time()
        result2 = await self.advanced_evasion.check_stock_advanced(tcin, warm_session=False)
        time2 = time.time() - start_time
        
        methods.append({
            'name': 'Advanced Evasion',
            'time': time2,
            'status': result2['status'],
            'available': result2.get('available', False),
            'error': result2.get('error', None)
        })
        
        # Show comparison table
        print(f"\nCOMPARISON RESULTS:")
        print(f"{'Method':<20} {'Time':<10} {'Status':<15} {'Available':<10} {'Error'}")
        print("-" * 70)
        
        for method in methods:
            error_short = (method['error'][:30] + '...') if method['error'] and len(method['error']) > 30 else (method['error'] or 'None')
            print(f"{method['name']:<20} {method['time']:<10.2f} {method['status']:<15} {str(method['available']):<10} {error_short}")
        
        return methods

async def main():
    """Main test execution"""
    
    print("ULTIMATE BYPASS SYSTEM")
    print("Advanced Anti-Detection Test Suite")
    print("=" * 50)
    
    # Check dependencies
    missing_deps = []
    
    try:
        from curl_cffi import requests
        print("[OK] curl_cffi available")
    except ImportError:
        missing_deps.append("curl-cffi")
        print("[MISSING] curl_cffi - install with: pip install curl-cffi")
    
    try:
        import aiohttp
        print("[OK] aiohttp available")
    except ImportError:
        missing_deps.append("aiohttp")
    
    if missing_deps:
        print(f"\nMissing dependencies: {', '.join(missing_deps)}")
        print("Install missing packages and run again for full testing")
    
    # Initialize and run tests
    tester = UltimateBypassTester()
    await tester.run_comprehensive_test()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
    except Exception as e:
        print(f"\nTest failed with error: {e}")
        import traceback
        traceback.print_exc()
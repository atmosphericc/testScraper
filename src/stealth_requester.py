"""
Advanced stealth HTTP client using curl_cffi for perfect browser impersonation
Install: pip install curl-cffi

This library uses actual browser TLS stacks, making requests indistinguishable from real browsers
"""
try:
    from curl_cffi import requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    import requests
    CURL_CFFI_AVAILABLE = False
    print("⚠️ curl_cffi not available. Install with: pip install curl-cffi")

import random
import time
import logging
from typing import Dict, List, Optional
import json
import asyncio

class StealthRequester:
    """Ultra-stealth HTTP client using curl_cffi for perfect browser impersonation"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
        self.store_id = "865"
        
        # Browser impersonation profiles (curl_cffi)
        self.browser_profiles = [
            "chrome110",
            "chrome116", 
            "chrome119",
            "chrome120",
            "chrome124",
            "edge99",
            "edge101",
            "safari15_3",
            "safari15_5"
        ] if CURL_CFFI_AVAILABLE else ["chrome"]
        
    def generate_visitor_id(self):
        """Generate realistic visitor ID"""
        timestamp = int(time.time() * 1000)
        random_suffix = ''.join(random.choices('0123456789ABCDEF', k=16))
        return f"{timestamp:016X}{random_suffix}"
    
    def get_realistic_headers(self):
        """Generate ultra-realistic headers that match real browsers"""
        
        # Vary headers based on "browser type"
        browser_type = random.choice(['chrome', 'firefox', 'safari', 'edge'])
        
        base_headers = {
            'accept': 'application/json',
            'accept-language': random.choice([
                'en-US,en;q=0.9',
                'en-US,en;q=0.8,es;q=0.6',
                'en-US,en;q=0.9,fr;q=0.8,de;q=0.7'
            ]),
            'cache-control': random.choice(['no-cache', 'max-age=0']),
            'dnt': random.choice(['1', '0']),
            'origin': 'https://www.target.com',
            'referer': random.choice([
                'https://www.target.com/',
                'https://www.target.com/c/electronics',
                'https://www.target.com/c/video-games'
            ]),
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
        }
        
        if browser_type == 'chrome':
            base_headers.update({
                'sec-ch-ua': f'"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': random.choice(['"Windows"', '"macOS"', '"Linux"']),
                'upgrade-insecure-requests': '1',
            })
        elif browser_type == 'firefox':
            # Firefox has different header patterns
            base_headers.update({
                'te': 'trailers',
                'upgrade-insecure-requests': '1',
            })
        
        # Sometimes add optional headers that real browsers include
        if random.random() < 0.3:
            base_headers['x-requested-with'] = 'XMLHttpRequest'
        
        if random.random() < 0.2:
            base_headers['pragma'] = 'no-cache'
            
        return base_headers
    
    def check_stock_stealth(self, tcin: str) -> Dict:
        """Check stock using curl_cffi for maximum stealth"""
        
        url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
        
        params = {
            'key': self.api_key,
            'tcin': tcin,
            'store_id': self.store_id,
            'pricing_store_id': self.store_id,
            'has_pricing_store_id': 'true',
            'has_financing_options': 'true',
            'visitor_id': self.generate_visitor_id(),
            'has_size_context': 'true',
            # Add some realistic optional parameters
            'skip_personalized': random.choice(['true', 'false']),
            'include_sponsored': random.choice(['true', 'false']),
        }
        
        headers = self.get_realistic_headers()
        
        try:
            if CURL_CFFI_AVAILABLE:
                # Use curl_cffi for perfect browser impersonation
                browser_profile = random.choice(self.browser_profiles)
                
                response = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=15,
                    impersonate=browser_profile,  # 🔥 Perfect browser impersonation
                    http2=True,  # Use HTTP/2 like real browsers
                )
            else:
                # Fallback to regular requests
                import requests as fallback_requests
                response = fallback_requests.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=15
                )
            
            if response.status_code == 200:
                data = response.json()
                return self.parse_availability(tcin, data)
            
            elif response.status_code == 429:
                self.logger.warning(f"Rate limited for {tcin}")
                return {
                    'tcin': tcin,
                    'available': False,
                    'status': 'rate_limited',
                    'error': 'Rate limited'
                }
            
            else:
                self.logger.warning(f"HTTP {response.status_code} for {tcin}")
                return {
                    'tcin': tcin,
                    'available': False,
                    'status': 'error',
                    'error': f'HTTP {response.status_code}'
                }
                
        except Exception as e:
            self.logger.error(f"Stealth request failed for {tcin}: {e}")
            return {
                'tcin': tcin,
                'available': False,
                'status': 'exception',
                'error': str(e)
            }
    
    def parse_availability(self, tcin: str, data: Dict) -> Dict:
        """Parse Target API response"""
        try:
            product = data['data']['product']
            item = product['item']
            
            name = item.get('product_description', {}).get('title', 'Unknown')
            price = product.get('price', {}).get('current_retail', 0)
            
            fulfillment = item.get('fulfillment', {})
            is_marketplace = fulfillment.get('is_marketplace', False)
            purchase_limit = fulfillment.get('purchase_limit', 0)
            
            eligibility = item.get('eligibility_rules', {})
            ship_to_guest = eligibility.get('ship_to_guest', {}).get('is_active', False)
            
            # Stock detection
            if is_marketplace:
                available = purchase_limit > 0
                seller_type = "third-party"
            else:
                available = ship_to_guest and purchase_limit >= 1
                seller_type = "target"
            
            return {
                'tcin': tcin,
                'name': name[:50],
                'price': price,
                'available': available,
                'seller_type': seller_type,
                'purchase_limit': purchase_limit,
                'status': 'success'
            }
            
        except Exception as e:
            self.logger.error(f"Parse error for {tcin}: {e}")
            return {
                'tcin': tcin,
                'available': False,
                'status': 'parse_error',
                'error': str(e)
            }


class ConcurrentStealthChecker:
    """Run multiple stealth checkers concurrently for maximum speed"""
    
    def __init__(self, max_concurrent=3):
        self.max_concurrent = max_concurrent
        self.stealth_requester = StealthRequester()
        self.logger = logging.getLogger(__name__)
    
    async def check_multiple_products_fast(self, tcins: List[str]) -> List[Dict]:
        """Check multiple products concurrently using stealth requests"""
        
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def check_with_semaphore(tcin: str) -> Dict:
            async with semaphore:
                # Add small random delay to spread requests
                await asyncio.sleep(random.uniform(0.1, 0.5))
                
                # Run the sync stealth check in thread pool
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(
                    None, 
                    self.stealth_requester.check_stock_stealth, 
                    tcin
                )
        
        # Create tasks for all products
        tasks = [check_with_semaphore(tcin) for tcin in tcins]
        
        # Wait for all to complete
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Handle any exceptions
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self.logger.error(f"Concurrent check failed for {tcins[i]}: {result}")
                final_results.append({
                    'tcin': tcins[i],
                    'available': False,
                    'status': 'exception',
                    'error': str(result)
                })
            else:
                final_results.append(result)
        
        return final_results


# Example usage
async def demo_stealth_checking():
    """Demo of ultra-fast stealth checking"""
    checker = ConcurrentStealthChecker(max_concurrent=3)
    
    tcins = ["94724987", "94681770", "89542109"]
    
    print("🚀 Running concurrent stealth checks...")
    start_time = time.time()
    
    results = await checker.check_multiple_products_fast(tcins)
    
    end_time = time.time()
    print(f"⚡ Checked {len(tcins)} products in {end_time - start_time:.2f} seconds")
    
    for result in results:
        status = "🟢 IN STOCK" if result.get('available') else "🔴 OUT OF STOCK"
        print(f"{result['tcin']}: {status} - {result.get('status', 'unknown')}")

if __name__ == "__main__":
    asyncio.run(demo_stealth_checking())
try:
    from curl_cffi import requests as cffi_requests
    CURL_CFFI_AVAILABLE = True
    print("curl_cffi loaded - Perfect browser impersonation enabled!")
except ImportError:
    import aiohttp
    CURL_CFFI_AVAILABLE = False
    print("curl_cffi not available - using fallback")

import asyncio
import random
import logging
import time
import requests
from typing import Dict, Optional
from datetime import datetime, timedelta

# Import the production authenticated stock checker
from authenticated_stock_checker import AuthenticatedStockChecker

class StockChecker:
    def __init__(self, proxies=None, use_website_checking=True):
        self.logger = logging.getLogger(__name__)
        self.base_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
        self.api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
        self.store_id = "865"
        self.consecutive_errors = 0
        self.last_error_time = None
        
        # Proxy rotation setup
        self.proxies = proxies or []
        self.proxy_index = 0
        self.proxy_stats = {}
        
        # Website checking for accurate results
        self.use_website_checking = use_website_checking
        self.website_checker = AuthenticatedStockChecker() if use_website_checking else None
        
        # Dashboard analytics integration
        self.dashboard_url = 'http://localhost:5000'
        self.record_proxy_analytics = None  # Will be set by monitor
        
        # Initialize proxy stats
        for i, proxy in enumerate(self.proxies):
            self.proxy_stats[i] = {
                'success': 0,
                'failure': 0,
                'blocked_until': 0,
                'last_used': 0
            }
        
    def generate_visitor_id(self):
        """Generate realistic visitor ID matching Target's format"""
        timestamp = int(time.time() * 1000)
        random_suffix = ''.join(random.choices('0123456789ABCDEF', k=16))
        return f"{timestamp:016X}{random_suffix}"
    
    def get_best_proxy(self):
        """Get the best available proxy based on success rate and cooldown"""
        if not self.proxies:
            return None
            
        current_time = time.time()
        available_proxies = []
        
        for i, proxy in enumerate(self.proxies):
            stats = self.proxy_stats[i]
            
            # Skip blocked proxies
            if stats['blocked_until'] > current_time:
                continue
                
            # Calculate success rate
            total_attempts = stats['success'] + stats['failure']
            if total_attempts == 0:
                success_rate = 1.0
            else:
                success_rate = stats['success'] / total_attempts
                
            # Prefer proxies not used recently
            time_since_use = current_time - stats['last_used']
            
            available_proxies.append({
                'index': i,
                'proxy': proxy,
                'success_rate': success_rate,
                'time_since_use': time_since_use,
                'score': success_rate + (time_since_use / 3600)  # Bonus for recent non-use
            })
        
        if not available_proxies:
            self.logger.warning("No available proxies! All may be blocked.")
            return None
            
        # Get best proxy
        best = max(available_proxies, key=lambda x: x['score'])
        
        # Update usage time
        self.proxy_stats[best['index']]['last_used'] = current_time
        
        return best['proxy'], best['index']
    
    def report_proxy_result(self, proxy_index: int, success: bool, blocked: bool = False):
        """Report the result of using a proxy"""
        if proxy_index not in self.proxy_stats:
            return
            
        stats = self.proxy_stats[proxy_index]
        
        if success:
            stats['success'] += 1
            stats['blocked_until'] = 0  # Reset block
        else:
            stats['failure'] += 1
            
        if blocked:
            # Block proxy for 30 minutes
            stats['blocked_until'] = time.time() + (30 * 60)
            proxy = self.proxies[proxy_index]
            self.logger.warning(f"Proxy {proxy.get('host', 'unknown')} blocked for 30 minutes")
        
    def get_headers(self):
        """Generate randomized headers"""
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:131.0) Gecko/20100101 Firefox/131.0'
        ]
        
        user_agent = random.choice(user_agents)
        
        # Extract version from user agent for consistent headers
        if 'Chrome/' in user_agent:
            version = user_agent.split('Chrome/')[1].split('.')[0]
            sec_ua = f'"Chromium";v="{version}", "Google Chrome";v="{version}", "Not?A_Brand";v="24"'
        else:  # Firefox
            sec_ua = '"Not?A_Brand";v="8", "Chromium";v="108"'
        
        return {
            'accept': 'application/json',
            'accept-encoding': 'gzip, deflate, br, zstd',
            'accept-language': 'en-US,en;q=0.9',
            'cache-control': 'no-cache',
            'dnt': '1',
            'origin': 'https://www.target.com',
            'pragma': 'no-cache',
            'referer': 'https://www.target.com/',
            'sec-ch-ua': sec_ua,
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
            'user-agent': user_agent,
            'x-requested-with': 'XMLHttpRequest'
        }
    
    async def should_backoff(self) -> bool:
        """Check if we should back off due to consecutive errors"""
        if self.consecutive_errors == 0:
            return False
            
        if self.last_error_time is None:
            return False
            
        # Exponential backoff: 2^errors seconds, max 300 seconds (5 minutes)
        backoff_seconds = min(2 ** self.consecutive_errors, 300)
        time_since_error = (datetime.now() - self.last_error_time).total_seconds()
        
        if time_since_error < backoff_seconds:
            self.logger.info(f"Backing off for {backoff_seconds - time_since_error:.0f} more seconds (error count: {self.consecutive_errors})")
            return True
            
        return False

    async def check_stock(self, session, tcin: str) -> Dict:
        """Check stock with ACCURATE website verification"""
        # Check if we should back off
        if await self.should_backoff():
            return {
                'tcin': tcin,
                'available': False,
                'status': 'backoff',
                'error': 'Backing off due to consecutive errors'
            }
        
        # Use accurate website checking if enabled
        if self.use_website_checking and self.website_checker:
            try:
                self.logger.debug(f"Using website checking for {tcin}")
                result = await self.website_checker.check_authenticated_stock(tcin)
                
                # Reset consecutive errors on success
                self.consecutive_errors = 0
                return result
                
            except Exception as e:
                self.logger.warning(f"Website checking failed for {tcin}: {e}, falling back to API")
                # Fall through to API checking
        
        # Fallback to API checking (less accurate but faster)  
        if CURL_CFFI_AVAILABLE:
            # 🔥 PERFECT BROWSER IMPERSONATION
            return await self._check_stock_stealth(tcin)
        else:
            # Fallback to regular aiohttp
            return await self._check_stock_fallback(session, tcin)
    
    async def _check_stock_stealth(self, tcin: str) -> Dict:
        """Ultra-stealth stock check using curl_cffi with proxy rotation"""
        params = {
            'key': self.api_key,
            'tcin': tcin,
            'store_id': self.store_id,
            'pricing_store_id': self.store_id,
            'has_pricing_store_id': 'true',
            'has_financing_options': 'true',
            'visitor_id': self.generate_visitor_id(),
            'has_size_context': 'true',
            # Add realistic optional params
            'skip_personalized': random.choice(['true', 'false']),
            'include_sponsored': random.choice(['true', 'false']),
        }
        
        headers = self.get_headers()
        
        # Perfect browser profiles that match real Chrome/Firefox TLS
        browser_profiles = [
            "chrome110", "chrome116", "chrome119", "chrome120", "chrome124",
            "edge99", "edge101", "safari15_3", "safari15_5"
        ]
        
        # Get best proxy
        proxy_info = self.get_best_proxy()
        proxy_dict = None
        proxy_index = None
        
        if proxy_info:
            proxy_dict, proxy_index = proxy_info
            self.logger.debug(f"Using proxy {proxy_index}: {proxy_dict.get('host', 'unknown')}")
        
        # Realistic human-like delay (but faster for personal use)
        base_delay = random.uniform(2, 5)  # 2-5 seconds for speed
        jitter = random.uniform(-0.5, 1) 
        await asyncio.sleep(max(0.5, base_delay + jitter))
        
        try:
            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            
            def make_stealth_request():
                browser_profile = random.choice(browser_profiles)
                
                # Prepare proxy for curl_cffi
                proxies_dict = None
                if proxy_dict:
                    if proxy_dict.get('username') and proxy_dict.get('password'):
                        proxy_url = f"{proxy_dict.get('protocol', 'http')}://{proxy_dict['username']}:{proxy_dict['password']}@{proxy_dict['host']}:{proxy_dict['port']}"
                    else:
                        proxy_url = f"{proxy_dict.get('protocol', 'http')}://{proxy_dict['host']}:{proxy_dict['port']}"
                    
                    proxies_dict = {
                        'http': proxy_url,
                        'https': proxy_url
                    }
                
                response = cffi_requests.get(
                    self.base_url,
                    params=params,
                    headers=headers,
                    timeout=15,
                    impersonate=browser_profile,  # Perfect impersonation
                    http2=True,                   # HTTP/2 like real browsers
                    allow_redirects=True,
                    proxies=proxies_dict          # Proxy rotation
                )
                
                return response
            
            # Execute in thread pool
            response = await loop.run_in_executor(None, make_stealth_request)
            
            if response.status_code == 200:
                data = response.json()
                result = self.parse_availability(tcin, data)
                
                # Success! Reset error counter and report proxy success
                self.consecutive_errors = 0
                self.last_error_time = None
                if proxy_index is not None:
                    self.report_proxy_result(proxy_index, success=True)
                
                # Record analytics
                if self.record_proxy_analytics:
                    proxy_host = proxy_dict.get('host', 'direct') if proxy_dict else 'direct'
                    self.record_proxy_analytics(proxy_host, True, 200)
                
                self.logger.debug(f"Stealth check successful for {tcin}")
                return result
                
            elif response.status_code == 429:
                self.consecutive_errors += 1
                self.last_error_time = datetime.now()
                if proxy_index is not None:
                    self.report_proxy_result(proxy_index, success=False, blocked=True)
                
                # Record analytics
                if self.record_proxy_analytics:
                    proxy_host = proxy_dict.get('host', 'direct') if proxy_dict else 'direct'
                    self.record_proxy_analytics(proxy_host, False, 0, "Rate limited (429)")
                
                self.logger.warning(f"Rate limited! Consecutive errors: {self.consecutive_errors}")
                return {
                    'tcin': tcin,
                    'available': False,
                    'status': 'rate_limited',
                    'error': 'Rate limited by Target'
                }
            
            elif response.status_code == 404:
                # Don't count 404s as errors but report neutral proxy result
                if proxy_index is not None:
                    self.report_proxy_result(proxy_index, success=True)  # 404 is "successful" request
                
                # Record analytics
                if self.record_proxy_analytics:
                    proxy_host = proxy_dict.get('host', 'direct') if proxy_dict else 'direct'
                    self.record_proxy_analytics(proxy_host, True, 150, "Product not found (404)")
                
                return {
                    'tcin': tcin,
                    'available': False,
                    'status': 'not_found',
                    'error': 'Product not found'
                }
            
            else:
                self.consecutive_errors += 1
                self.last_error_time = datetime.now()
                if proxy_index is not None:
                    self.report_proxy_result(proxy_index, success=False)
                
                # Record analytics
                if self.record_proxy_analytics:
                    proxy_host = proxy_dict.get('host', 'direct') if proxy_dict else 'direct'
                    self.record_proxy_analytics(proxy_host, False, 0, f"HTTP {response.status_code}")
                
                self.logger.warning(f"HTTP {response.status_code} error. Consecutive errors: {self.consecutive_errors}")
                return {
                    'tcin': tcin,
                    'available': False,
                    'status': 'error',
                    'error': f'HTTP {response.status_code}'
                }
                
        except Exception as e:
            self.consecutive_errors += 1
            self.last_error_time = datetime.now()
            
            # Record analytics
            if self.record_proxy_analytics:
                proxy_host = proxy_dict.get('host', 'direct') if proxy_dict else 'direct'
                self.record_proxy_analytics(proxy_host, False, 0, str(e))
            
            self.logger.error(f"Stealth request failed for {tcin}: {e}")
            return {
                'tcin': tcin,
                'available': False,
                'status': 'exception',
                'error': str(e)
            }
    
    async def _check_stock_fallback(self, session, tcin: str) -> Dict:
        """Fallback method using aiohttp"""
        params = {
            'key': self.api_key,
            'tcin': tcin,
            'store_id': self.store_id,
            'pricing_store_id': self.store_id,
            'has_pricing_store_id': 'true',
            'has_financing_options': 'true',
            'visitor_id': self.generate_visitor_id(),
            'has_size_context': 'true'
        }
        
        # Human-like delay
        base_delay = random.uniform(3, 8)
        jitter = random.uniform(-1, 2)
        await asyncio.sleep(max(1, base_delay + jitter))
        
        try:
            async with session.get(
                self.base_url,
                params=params,
                headers=self.get_headers(),
                timeout=10
            ) as response:
                
                if response.status == 200:
                    data = await response.json()
                    self.consecutive_errors = 0
                    self.last_error_time = None
                    return self.parse_availability(tcin, data)
                
                elif response.status == 429:
                    self.consecutive_errors += 1
                    self.last_error_time = datetime.now()
                    return {
                        'tcin': tcin,
                        'available': False,
                        'status': 'rate_limited',
                        'error': 'Rate limited by Target'
                    }
                
                elif response.status == 404:
                    return {
                        'tcin': tcin,
                        'available': False,
                        'status': 'not_found',
                        'error': 'Product not found'
                    }
                
                else:
                    self.consecutive_errors += 1
                    self.last_error_time = datetime.now()
                    return {
                        'tcin': tcin,
                        'available': False,
                        'status': 'error',
                        'error': f'HTTP {response.status}'
                    }
                
        except asyncio.TimeoutError:
            self.logger.error(f"Timeout checking {tcin}")
            return {
                'tcin': tcin,
                'available': False,
                'status': 'timeout'
            }
        except Exception as e:
            self.logger.error(f"Error checking {tcin}: {e}")
            return {
                'tcin': tcin,
                'available': False,
                'status': 'error',
                'error': str(e)
            }
    
    def parse_availability(self, tcin: str, data: Dict) -> Dict:
        """Parse Target API response for availability"""
        try:
            product = data['data']['product']
            item = product['item']
            
            # Extract key fields
            name = item.get('product_description', {}).get('title', 'Unknown')
            price = product.get('price', {}).get('current_retail', 0)
            
            # Fulfillment info
            fulfillment = item.get('fulfillment', {})
            is_marketplace = fulfillment.get('is_marketplace', False)
            purchase_limit = fulfillment.get('purchase_limit', 0)
            
            # Eligibility rules
            eligibility = item.get('eligibility_rules', {})
            ship_to_guest = eligibility.get('ship_to_guest', {}).get('is_active', False)
            
            # FIXED STOCK DETECTION LOGIC - based on actual API analysis
            # Key insight: OUT OF STOCK products have NO eligibility_rules at all
            # IN STOCK products have eligibility_rules.ship_to_guest.is_active: true
            
            if is_marketplace:
                # Third-party seller
                available = purchase_limit > 0
                seller_type = "third-party"
            else:
                # Target direct - must have eligibility_rules AND ship_to_guest active  
                has_eligibility_rules = bool(item.get('eligibility_rules'))
                available = has_eligibility_rules and ship_to_guest and purchase_limit >= 1
                seller_type = "target"
            
            # Special handling for pre-orders
            if not ship_to_guest and purchase_limit == 1 and price >= 400:
                # Likely a pre-order bundle
                available = True
                seller_type = "target-preorder"
            
            return {
                'tcin': tcin,
                'name': name[:50],  # Truncate long names
                'price': price,
                'available': available,
                'seller_type': seller_type,
                'purchase_limit': purchase_limit,
                'ship_to_guest': ship_to_guest,
                'status': 'success'
            }
            
        except Exception as e:
            self.logger.error(f"Error parsing response for {tcin}: {e}")
            return {
                'tcin': tcin,
                'available': False,
                'status': 'parse_error',
                'error': str(e)
            }
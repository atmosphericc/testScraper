import aiohttp
import asyncio
import random
import logging
from typing import List, Dict, Optional
import time
from dataclasses import dataclass

@dataclass
class ProxyConfig:
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    protocol: str = "http"  # http, https, socks5
    
    @property
    def url(self):
        if self.username and self.password:
            return f"{self.protocol}://{self.username}:{self.password}@{self.host}:{self.port}"
        return f"{self.protocol}://{self.host}:{self.port}"

class ProxyManager:
    """Commercial-grade proxy rotation for Target monitoring"""
    
    def __init__(self):
        self.proxies: List[ProxyConfig] = []
        self.proxy_stats = {}  # Track success/failure rates
        self.current_proxy_index = 0
        self.logger = logging.getLogger(__name__)
        self.blocked_proxies = set()
        
    def add_proxy(self, host: str, port: int, username: str = None, password: str = None, protocol: str = "http"):
        """Add a proxy to the rotation pool"""
        proxy = ProxyConfig(host, port, username, password, protocol)
        self.proxies.append(proxy)
        self.proxy_stats[len(self.proxies) - 1] = {
            'success': 0,
            'failure': 0,
            'last_used': 0,
            'blocked_until': 0
        }
        self.logger.info(f"Added proxy: {host}:{port}")
    
    def load_proxy_list(self, proxy_list: List[Dict]):
        """Load proxies from config
        Expected format: [{"host": "1.2.3.4", "port": 8080, "username": "user", "password": "pass"}]
        """
        for proxy_config in proxy_list:
            self.add_proxy(
                host=proxy_config['host'],
                port=proxy_config['port'],
                username=proxy_config.get('username'),
                password=proxy_config.get('password'),
                protocol=proxy_config.get('protocol', 'http')
            )
    
    def get_best_proxy(self) -> Optional[ProxyConfig]:
        """Get the best available proxy based on success rate and cooldown"""
        if not self.proxies:
            return None
            
        available_proxies = []
        current_time = time.time()
        
        for i, proxy in enumerate(self.proxies):
            stats = self.proxy_stats[i]
            
            # Skip blocked proxies
            if stats['blocked_until'] > current_time:
                continue
                
            # Calculate success rate (default to 1.0 if no attempts)
            total_attempts = stats['success'] + stats['failure']
            if total_attempts == 0:
                success_rate = 1.0
            else:
                success_rate = stats['success'] / total_attempts
                
            # Prefer proxies that haven't been used recently
            time_since_use = current_time - stats['last_used']
            
            available_proxies.append({
                'index': i,
                'proxy': proxy,
                'success_rate': success_rate,
                'time_since_use': time_since_use,
                'score': success_rate + (time_since_use / 3600)  # Bonus for not recently used
            })
        
        if not available_proxies:
            self.logger.warning("No available proxies! All may be blocked.")
            return None
            
        # Sort by score (success rate + time bonus)
        available_proxies.sort(key=lambda x: x['score'], reverse=True)
        best = available_proxies[0]
        
        # Update usage time
        self.proxy_stats[best['index']]['last_used'] = current_time
        
        self.logger.debug(f"Selected proxy {best['index']}: {best['proxy'].host}:{best['proxy'].port} "
                         f"(success rate: {best['success_rate']:.2f})")
        
        return best['proxy']
    
    def report_proxy_result(self, proxy: ProxyConfig, success: bool, blocked: bool = False):
        """Report the result of using a proxy"""
        # Find the proxy index
        proxy_index = None
        for i, p in enumerate(self.proxies):
            if p.host == proxy.host and p.port == proxy.port:
                proxy_index = i
                break
                
        if proxy_index is None:
            return
            
        stats = self.proxy_stats[proxy_index]
        
        if success:
            stats['success'] += 1
            # Reset block if successful
            stats['blocked_until'] = 0
        else:
            stats['failure'] += 1
            
        if blocked:
            # Block proxy for 30 minutes
            stats['blocked_until'] = time.time() + (30 * 60)
            self.logger.warning(f"Proxy {proxy.host}:{proxy.port} blocked for 30 minutes")
    
    def get_proxy_stats(self) -> Dict:
        """Get statistics for all proxies"""
        stats = {}
        for i, proxy in enumerate(self.proxies):
            proxy_stats = self.proxy_stats[i]
            total = proxy_stats['success'] + proxy_stats['failure']
            success_rate = proxy_stats['success'] / total if total > 0 else 0
            
            stats[f"{proxy.host}:{proxy.port}"] = {
                'success_rate': success_rate,
                'total_requests': total,
                'blocked_until': proxy_stats['blocked_until'],
                'available': proxy_stats['blocked_until'] < time.time()
            }
            
        return stats


class HighSpeedStockChecker:
    """Commercial-grade stock checker with proxy rotation for maximum speed"""
    
    def __init__(self, proxy_manager: ProxyManager):
        self.proxy_manager = proxy_manager
        self.logger = logging.getLogger(__name__)
        self.base_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
        self.api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
        self.store_id = "865"
        
    def generate_visitor_id(self):
        """Generate realistic visitor ID"""
        timestamp = int(time.time() * 1000)
        random_suffix = ''.join(random.choices('0123456789ABCDEF', k=16))
        return f"{timestamp:016X}{random_suffix}"
    
    def get_headers(self):
        """Generate randomized headers"""
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:131.0) Gecko/20100101 Firefox/131.0'
        ]
        
        user_agent = random.choice(user_agents)
        
        return {
            'accept': 'application/json',
            'accept-encoding': 'gzip, deflate, br, zstd',
            'accept-language': random.choice(['en-US,en;q=0.9', 'en-US,en;q=0.8', 'en-US,en;q=0.7']),
            'cache-control': 'no-cache',
            'dnt': random.choice(['1', '0']),
            'origin': 'https://www.target.com',
            'pragma': 'no-cache',
            'referer': 'https://www.target.com/',
            'sec-ch-ua': f'"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': random.choice(['"Windows"', '"macOS"']),
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
            'user-agent': user_agent,
            'x-requested-with': 'XMLHttpRequest'
        }
    
    async def check_stock_with_proxy(self, tcin: str, max_retries: int = 3) -> Dict:
        """Check stock using proxy rotation with retries"""
        
        for attempt in range(max_retries):
            proxy = self.proxy_manager.get_best_proxy()
            
            if not proxy:
                self.logger.error("No available proxies for stock check!")
                return {
                    'tcin': tcin,
                    'available': False,
                    'status': 'no_proxy',
                    'error': 'No available proxies'
                }
            
            try:
                # Create session with proxy
                connector = aiohttp.TCPConnector(limit=10)
                timeout = aiohttp.ClientTimeout(total=10)
                
                async with aiohttp.ClientSession(
                    connector=connector,
                    timeout=timeout
                ) as session:
                    
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
                    
                    # MINIMAL DELAY for commercial speed
                    await asyncio.sleep(random.uniform(0.5, 2.0))  # 0.5-2 seconds only
                    
                    async with session.get(
                        self.base_url,
                        params=params,
                        headers=self.get_headers(),
                        proxy=proxy.url if proxy else None
                    ) as response:
                        
                        if response.status == 200:
                            data = await response.json()
                            result = self.parse_availability(tcin, data)
                            
                            # Report success
                            self.proxy_manager.report_proxy_result(proxy, success=True)
                            return result
                            
                        elif response.status == 429:
                            # Rate limited - this proxy is likely blocked
                            self.logger.warning(f"Rate limited on proxy {proxy.host}:{proxy.port}")
                            self.proxy_manager.report_proxy_result(proxy, success=False, blocked=True)
                            continue  # Try next proxy
                            
                        else:
                            self.logger.warning(f"HTTP {response.status} on proxy {proxy.host}:{proxy.port}")
                            self.proxy_manager.report_proxy_result(proxy, success=False)
                            continue  # Try next proxy
                            
            except Exception as e:
                self.logger.error(f"Proxy {proxy.host}:{proxy.port} failed: {e}")
                self.proxy_manager.report_proxy_result(proxy, success=False)
                continue  # Try next proxy
        
        # All retries failed
        return {
            'tcin': tcin,
            'available': False,
            'status': 'all_proxies_failed',
            'error': f'All {max_retries} proxy attempts failed'
        }
    
    def parse_availability(self, tcin: str, data: Dict) -> Dict:
        """Parse Target API response for availability"""
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
            
            # Commercial stock logic - optimized for drops
            if is_marketplace:
                available = purchase_limit > 0
                seller_type = "third-party"
            else:
                available = ship_to_guest and purchase_limit >= 1  # Even 1 item = available
                seller_type = "target"
            
            return {
                'tcin': tcin,
                'name': name[:50],
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
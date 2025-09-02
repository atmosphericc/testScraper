"""
RESIDENTIAL PROXY NETWORK - Advanced IP Rotation & Management
Military-grade proxy rotation with health monitoring, warming, and intelligent selection

Features:
- Residential proxy health monitoring and rotation
- Automatic IP warming with realistic browsing patterns
- Geographic distribution and ASN diversity
- Real-time blocking detection and recovery
- Proxy provider integration (Bright Data, Smartproxy, etc.)
"""

import asyncio
import random
import time
import json
import logging
import hashlib
import aiohttp
import ssl
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import ipaddress
import socket

try:
    from curl_cffi import requests as cffi_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False

@dataclass
class ProxyMetrics:
    """Detailed proxy performance metrics"""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    rate_limited: int = 0
    blocked_count: int = 0
    avg_response_time: float = 0.0
    last_success: float = 0.0
    last_failure: float = 0.0
    consecutive_failures: int = 0
    uptime_percentage: float = 100.0
    geographic_location: str = "unknown"
    isp_name: str = "unknown"
    asn: str = "unknown"
    blocked_until: float = 0.0
    warmup_completed: bool = False
    last_warmup: float = 0.0

@dataclass
class ResidentialProxy:
    """Residential proxy configuration and metadata"""
    id: str
    host: str
    port: int
    username: str
    password: str
    protocol: str = "http"
    provider: str = "unknown"
    country: str = "US"
    state: str = "unknown"
    city: str = "unknown"
    isp: str = "residential"
    speed_tier: str = "standard"  # standard, premium, enterprise
    max_concurrent: int = 1
    rotation_interval: int = 600  # seconds
    enabled: bool = True
    
    @property
    def url(self) -> str:
        """Generate proxy URL"""
        return f"{self.protocol}://{self.username}:{self.password}@{self.host}:{self.port}"
    
    @property
    def endpoint(self) -> str:
        """Generate endpoint identifier"""
        return f"{self.host}:{self.port}"

class ProxyHealthChecker:
    """Advanced proxy health monitoring system"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.test_urls = [
            'https://httpbin.org/ip',
            'https://ipinfo.io/json',
            'https://api.ipify.org?format=json',
            'https://www.google.com/',
            'https://www.target.com/'
        ]
    
    async def check_proxy_health(self, proxy: ResidentialProxy) -> Dict[str, Any]:
        """Comprehensive proxy health check"""
        health_data = {
            'proxy_id': proxy.id,
            'timestamp': time.time(),
            'ip_accessible': False,
            'target_accessible': False,
            'response_time': 0.0,
            'detected_ip': None,
            'geographic_info': {},
            'errors': []
        }
        
        try:
            # Test 1: Basic IP accessibility
            ip_result = await self._test_ip_access(proxy)
            health_data.update(ip_result)
            
            # Test 2: Target.com accessibility
            target_result = await self._test_target_access(proxy)
            health_data['target_accessible'] = target_result['accessible']
            health_data['response_time'] = target_result.get('response_time', 0.0)
            
            # Test 3: Geographic verification
            if health_data['ip_accessible']:
                geo_info = await self._get_geographic_info(proxy)
                health_data['geographic_info'] = geo_info
            
            return health_data
            
        except Exception as e:
            health_data['errors'].append(str(e))
            self.logger.error(f"Health check failed for {proxy.id}: {e}")
            return health_data
    
    async def _test_ip_access(self, proxy: ResidentialProxy) -> Dict[str, Any]:
        """Test basic IP accessibility"""
        result = {
            'ip_accessible': False,
            'detected_ip': None,
            'ip_response_time': 0.0
        }
        
        try:
            if CURL_CFFI_AVAILABLE:
                start_time = time.time()
                
                def make_ip_request():
                    proxies = {'http': proxy.url, 'https': proxy.url}
                    response = cffi_requests.get(
                        'https://httpbin.org/ip',
                        proxies=proxies,
                        timeout=10,
                        impersonate='chrome131'
                    )
                    return response
                
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(None, make_ip_request)
                
                result['ip_response_time'] = time.time() - start_time
                
                if response.status_code == 200:
                    data = response.json()
                    result['ip_accessible'] = True
                    result['detected_ip'] = data.get('origin', '').split(',')[0].strip()
                
        except Exception as e:
            result['error'] = str(e)
            
        return result
    
    async def _test_target_access(self, proxy: ResidentialProxy) -> Dict[str, Any]:
        """Test Target.com accessibility"""
        result = {
            'accessible': False,
            'response_time': 0.0,
            'status_code': None
        }
        
        try:
            start_time = time.time()
            
            if CURL_CFFI_AVAILABLE:
                def make_target_request():
                    proxies = {'http': proxy.url, 'https': proxy.url}
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.5',
                        'Accept-Encoding': 'gzip, deflate, br',
                        'Cache-Control': 'no-cache',
                        'DNT': '1'
                    }
                    
                    response = cffi_requests.get(
                        'https://www.target.com/',
                        headers=headers,
                        proxies=proxies,
                        timeout=15,
                        impersonate='chrome131',
                        allow_redirects=True
                    )
                    return response
                
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(None, make_target_request)
                
                result['response_time'] = time.time() - start_time
                result['status_code'] = response.status_code
                result['accessible'] = response.status_code == 200
                
        except Exception as e:
            result['response_time'] = time.time() - start_time
            result['error'] = str(e)
            
        return result
    
    async def _get_geographic_info(self, proxy: ResidentialProxy) -> Dict[str, Any]:
        """Get geographic information for proxy IP"""
        geo_info = {}
        
        try:
            if CURL_CFFI_AVAILABLE:
                def get_geo_data():
                    proxies = {'http': proxy.url, 'https': proxy.url}
                    response = cffi_requests.get(
                        'https://ipinfo.io/json',
                        proxies=proxies,
                        timeout=10,
                        impersonate='chrome131'
                    )
                    return response.json() if response.status_code == 200 else {}
                
                loop = asyncio.get_event_loop()
                geo_info = await loop.run_in_executor(None, get_geo_data)
                
        except Exception as e:
            geo_info['error'] = str(e)
            
        return geo_info

class ProxyWarmer:
    """Advanced proxy warming system"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.warming_sequences = [
            # E-commerce browsing pattern
            [
                ('https://www.google.com/search?q=electronics+shopping', 3.2),
                ('https://www.target.com/', 2.8),
                ('https://www.target.com/c/electronics', 4.1),
                ('https://www.target.com/c/video-games', 3.5),
                ('https://www.target.com/s?searchTerm=gaming', 2.9)
            ],
            
            # Casual browsing pattern
            [
                ('https://www.bing.com/search?q=home+decor', 2.1),
                ('https://www.target.com/', 3.8),
                ('https://www.target.com/c/home', 4.2),
                ('https://www.target.com/c/deals', 3.1)
            ],
            
            # Product research pattern
            [
                ('https://www.google.com/search?q=target+deals+electronics', 2.5),
                ('https://www.target.com/', 1.9),
                ('https://www.target.com/c/electronics', 3.7),
                ('https://www.target.com/s?searchTerm=nintendo', 4.3),
                ('https://www.target.com/s?searchTerm=playstation', 3.1)
            ]
        ]
    
    async def warm_proxy(self, proxy: ResidentialProxy, intensive: bool = False) -> bool:
        """Warm proxy with realistic browsing behavior"""
        try:
            sequence = random.choice(self.warming_sequences)
            if intensive:
                # Use longer sequence for intensive warming
                sequence = sequence + [
                    ('https://www.target.com/c/toys', 3.2),
                    ('https://www.target.com/c/sports-outdoors', 2.9),
                    ('https://www.target.com/account/login', 1.8)
                ]
            
            self.logger.info(f"🔥 Starting proxy warmup: {proxy.id} ({len(sequence)} steps)")
            
            for i, (url, base_delay) in enumerate(sequence):
                success = await self._make_warming_request(proxy, url, i)
                if not success:
                    self.logger.warning(f"⚠️ Warmup step {i+1} failed for {proxy.id}")
                    return False
                
                # Human-like delay with variation
                delay = base_delay + random.uniform(-1.0, 2.0)
                delay = max(1.5, delay)  # Minimum delay
                await asyncio.sleep(delay)
            
            self.logger.info(f"✅ Proxy warmup completed: {proxy.id}")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Proxy warmup failed for {proxy.id}: {e}")
            return False
    
    async def _make_warming_request(self, proxy: ResidentialProxy, url: str, step: int) -> bool:
        """Make individual warming request"""
        try:
            if CURL_CFFI_AVAILABLE:
                def make_request():
                    proxies = {'http': proxy.url, 'https': proxy.url}
                    headers = self._get_warming_headers(step)
                    
                    response = cffi_requests.get(
                        url,
                        headers=headers,
                        proxies=proxies,
                        timeout=15,
                        impersonate='chrome131',
                        allow_redirects=True
                    )
                    return response.status_code in [200, 301, 302, 304]
                
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, make_request)
            
            return True  # Skip if no curl_cffi
            
        except Exception:
            return False
    
    def _get_warming_headers(self, step: int) -> Dict[str, str]:
        """Get headers for warming requests"""
        base_headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Accept-Language': 'en-US,en;q=0.9',
            'Cache-Control': 'max-age=0',
            'DNT': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'cross-site' if step == 0 else 'same-origin',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
        }
        
        # Add variation for different steps
        if step > 0:
            base_headers['Referer'] = 'https://www.google.com/' if step == 1 else 'https://www.target.com/'
        
        return base_headers

class ResidentialProxyNetwork:
    """Master residential proxy network manager"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.proxies: Dict[str, ResidentialProxy] = {}
        self.metrics: Dict[str, ProxyMetrics] = {}
        self.health_checker = ProxyHealthChecker()
        self.proxy_warmer = ProxyWarmer()
        self.thread_pool = ThreadPoolExecutor(max_workers=10)
        
        # Configuration
        self.min_success_rate = 0.7
        self.max_consecutive_failures = 3
        self.warmup_interval = 3600  # 1 hour
        self.health_check_interval = 300  # 5 minutes
        
        # State tracking
        self.last_health_check = 0
        self.active_proxy_id = None
        self.selection_algorithm = 'weighted_random'  # weighted_random, round_robin, fastest
        
        self.logger.info("🌐 Residential Proxy Network initialized")
    
    def add_proxy_config(self, config: Dict) -> bool:
        """Add proxy from configuration dict"""
        try:
            proxy = ResidentialProxy(
                id=config.get('id', f"{config['host']}:{config['port']}"),
                host=config['host'],
                port=config['port'],
                username=config['username'],
                password=config['password'],
                protocol=config.get('protocol', 'http'),
                provider=config.get('provider', 'unknown'),
                country=config.get('country', 'US'),
                state=config.get('state', 'unknown'),
                city=config.get('city', 'unknown'),
                isp=config.get('isp', 'residential'),
                speed_tier=config.get('speed_tier', 'standard')
            )
            
            return self.add_proxy(proxy)
            
        except Exception as e:
            self.logger.error(f"Failed to add proxy config: {e}")
            return False
    
    def add_proxy(self, proxy: ResidentialProxy) -> bool:
        """Add residential proxy to network"""
        try:
            self.proxies[proxy.id] = proxy
            self.metrics[proxy.id] = ProxyMetrics(
                geographic_location=f"{proxy.city}, {proxy.state}, {proxy.country}",
                isp_name=proxy.isp
            )
            
            self.logger.info(f"✅ Added proxy: {proxy.id} ({proxy.provider}) - {proxy.city}, {proxy.country}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to add proxy {proxy.id}: {e}")
            return False
    
    def remove_proxy(self, proxy_id: str) -> bool:
        """Remove proxy from network"""
        if proxy_id in self.proxies:
            del self.proxies[proxy_id]
            del self.metrics[proxy_id]
            self.logger.info(f"🗑️ Removed proxy: {proxy_id}")
            return True
        return False
    
    async def get_best_proxy(self, for_target_api: bool = True) -> Optional[ResidentialProxy]:
        """Get best available proxy using intelligent selection"""
        
        # Perform health check if needed
        if time.time() - self.last_health_check > self.health_check_interval:
            await self._periodic_health_check()
        
        available_proxies = self._get_available_proxies()
        if not available_proxies:
            self.logger.error("❌ No available proxies!")
            return None
        
        # Select based on algorithm
        if self.selection_algorithm == 'weighted_random':
            proxy = self._select_weighted_random(available_proxies)
        elif self.selection_algorithm == 'fastest':
            proxy = self._select_fastest(available_proxies)
        else:  # round_robin
            proxy = self._select_round_robin(available_proxies)
        
        # Warm proxy if needed
        if for_target_api:
            await self._ensure_proxy_warmed(proxy)
        
        self.active_proxy_id = proxy.id
        return proxy
    
    def _get_available_proxies(self) -> List[ResidentialProxy]:
        """Get list of available proxies"""
        available = []
        current_time = time.time()
        
        for proxy_id, proxy in self.proxies.items():
            if not proxy.enabled:
                continue
                
            metrics = self.metrics[proxy_id]
            
            # Skip blocked proxies
            if metrics.blocked_until > current_time:
                continue
            
            # Skip proxies with too many consecutive failures
            if metrics.consecutive_failures >= self.max_consecutive_failures:
                continue
            
            # Skip proxies with low success rate (if they have enough data)
            if metrics.total_requests > 10:
                success_rate = metrics.successful_requests / metrics.total_requests
                if success_rate < self.min_success_rate:
                    continue
            
            available.append(proxy)
        
        return available
    
    def _select_weighted_random(self, proxies: List[ResidentialProxy]) -> ResidentialProxy:
        """Select proxy using weighted random based on performance"""
        weights = []
        
        for proxy in proxies:
            metrics = self.metrics[proxy.id]
            
            # Base weight
            weight = 1.0
            
            # Success rate bonus
            if metrics.total_requests > 0:
                success_rate = metrics.successful_requests / metrics.total_requests
                weight *= (success_rate * 2)  # 0-2x multiplier
            
            # Speed bonus (faster = higher weight)
            if metrics.avg_response_time > 0:
                speed_factor = max(0.1, (5.0 - metrics.avg_response_time) / 5.0)
                weight *= speed_factor
            
            # Recency bonus (less recently used = higher weight)
            time_since_use = time.time() - metrics.last_success
            if time_since_use > 3600:  # 1 hour
                weight *= 1.5
            elif time_since_use > 1800:  # 30 minutes
                weight *= 1.2
            
            # Geographic diversity bonus
            if proxy.country != 'US':
                weight *= 1.1  # Slight bonus for non-US IPs
            
            weights.append(max(0.1, weight))
        
        # Weighted random selection
        total_weight = sum(weights)
        if total_weight == 0:
            return random.choice(proxies)
        
        r = random.uniform(0, total_weight)
        cumulative = 0
        
        for i, proxy in enumerate(proxies):
            cumulative += weights[i]
            if r <= cumulative:
                return proxy
        
        return proxies[-1]  # Fallback
    
    def _select_fastest(self, proxies: List[ResidentialProxy]) -> ResidentialProxy:
        """Select fastest proxy"""
        best_proxy = proxies[0]
        best_time = float('inf')
        
        for proxy in proxies:
            metrics = self.metrics[proxy.id]
            if metrics.avg_response_time > 0 and metrics.avg_response_time < best_time:
                best_time = metrics.avg_response_time
                best_proxy = proxy
        
        return best_proxy
    
    def _select_round_robin(self, proxies: List[ResidentialProxy]) -> ResidentialProxy:
        """Select proxy using round robin"""
        if not hasattr(self, '_round_robin_index'):
            self._round_robin_index = 0
        
        proxy = proxies[self._round_robin_index % len(proxies)]
        self._round_robin_index += 1
        
        return proxy
    
    async def _ensure_proxy_warmed(self, proxy: ResidentialProxy) -> bool:
        """Ensure proxy is properly warmed"""
        metrics = self.metrics[proxy.id]
        current_time = time.time()
        
        # Check if warmup is needed
        needs_warmup = (
            not metrics.warmup_completed or
            (current_time - metrics.last_warmup) > self.warmup_interval
        )
        
        if needs_warmup:
            self.logger.info(f"🔥 Warming proxy: {proxy.id}")
            success = await self.proxy_warmer.warm_proxy(proxy)
            
            metrics.warmup_completed = success
            metrics.last_warmup = current_time
            
            return success
        
        return True
    
    async def _periodic_health_check(self):
        """Perform periodic health checks on all proxies"""
        self.logger.debug("🔍 Running periodic health check...")
        
        # Check up to 5 proxies per cycle to avoid overload
        proxy_ids = list(self.proxies.keys())
        check_proxies = random.sample(proxy_ids, min(5, len(proxy_ids)))
        
        tasks = []
        for proxy_id in check_proxies:
            if proxy_id in self.proxies:
                proxy = self.proxies[proxy_id]
                task = self.health_checker.check_proxy_health(proxy)
                tasks.append(task)
        
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in results:
                if isinstance(result, dict):
                    await self._update_proxy_metrics(result)
        
        self.last_health_check = time.time()
    
    async def _update_proxy_metrics(self, health_data: Dict):
        """Update proxy metrics based on health check"""
        proxy_id = health_data['proxy_id']
        if proxy_id not in self.metrics:
            return
        
        metrics = self.metrics[proxy_id]
        
        # Update basic accessibility
        if health_data['ip_accessible'] and health_data['target_accessible']:
            metrics.successful_requests += 1
            metrics.last_success = health_data['timestamp']
            metrics.consecutive_failures = 0
            
            # Update response time
            if health_data['response_time'] > 0:
                if metrics.avg_response_time == 0:
                    metrics.avg_response_time = health_data['response_time']
                else:
                    # Exponential moving average
                    metrics.avg_response_time = (metrics.avg_response_time * 0.7) + (health_data['response_time'] * 0.3)
        else:
            metrics.failed_requests += 1
            metrics.last_failure = health_data['timestamp']
            metrics.consecutive_failures += 1
        
        metrics.total_requests += 1
        
        # Update geographic info
        if health_data.get('geographic_info'):
            geo = health_data['geographic_info']
            if 'city' in geo:
                metrics.geographic_location = f"{geo.get('city', '')}, {geo.get('region', '')}, {geo.get('country', '')}"
            if 'org' in geo:
                metrics.isp_name = geo['org']
        
        # Calculate uptime percentage
        if metrics.total_requests > 0:
            metrics.uptime_percentage = (metrics.successful_requests / metrics.total_requests) * 100
    
    def report_proxy_result(self, proxy_id: str, success: bool, response_time: float = 0,
                           error_type: str = None, response_code: int = None):
        """Report result of proxy usage"""
        if proxy_id not in self.metrics:
            return
        
        metrics = self.metrics[proxy_id]
        metrics.total_requests += 1
        
        if success:
            metrics.successful_requests += 1
            metrics.last_success = time.time()
            metrics.consecutive_failures = 0
            metrics.blocked_until = 0  # Clear any blocks
            
            # Update response time
            if response_time > 0:
                if metrics.avg_response_time == 0:
                    metrics.avg_response_time = response_time
                else:
                    metrics.avg_response_time = (metrics.avg_response_time * 0.8) + (response_time * 0.2)
        else:
            metrics.failed_requests += 1
            metrics.last_failure = time.time()
            metrics.consecutive_failures += 1
            
            # Handle blocking
            if error_type in ['rate_limit', 'blocked', '429', '403'] or response_code in [429, 403]:
                metrics.rate_limited += 1
                
                # Progressive blocking duration
                block_duration = min(7200, 600 * (2 ** metrics.consecutive_failures))  # Max 2 hours
                metrics.blocked_until = time.time() + block_duration
                
                self.logger.warning(f"🚫 Proxy {proxy_id} blocked for {block_duration}s ({error_type})")
        
        # Update uptime percentage
        metrics.uptime_percentage = (metrics.successful_requests / metrics.total_requests) * 100
    
    def get_network_stats(self) -> Dict:
        """Get comprehensive network statistics"""
        total_proxies = len(self.proxies)
        available_proxies = len(self._get_available_proxies())
        
        total_requests = sum(m.total_requests for m in self.metrics.values())
        total_successes = sum(m.successful_requests for m in self.metrics.values())
        
        stats = {
            'network_overview': {
                'total_proxies': total_proxies,
                'available_proxies': available_proxies,
                'blocked_proxies': sum(1 for m in self.metrics.values() if m.blocked_until > time.time()),
                'overall_success_rate': total_successes / max(1, total_requests),
                'active_proxy': self.active_proxy_id
            },
            
            'geographic_distribution': self._get_geographic_distribution(),
            
            'proxy_details': {
                proxy_id: {
                    'endpoint': proxy.endpoint,
                    'provider': proxy.provider,
                    'location': f"{proxy.city}, {proxy.country}",
                    'success_rate': metrics.successful_requests / max(1, metrics.total_requests),
                    'avg_response_time': metrics.avg_response_time,
                    'uptime_percentage': metrics.uptime_percentage,
                    'blocked': metrics.blocked_until > time.time(),
                    'consecutive_failures': metrics.consecutive_failures,
                    'warmup_completed': metrics.warmup_completed
                }
                for proxy_id, proxy in self.proxies.items()
                for metrics in [self.metrics[proxy_id]]
            }
        }
        
        return stats
    
    def _get_geographic_distribution(self) -> Dict[str, int]:
        """Get geographic distribution of proxies"""
        distribution = {}
        for proxy in self.proxies.values():
            country = proxy.country
            distribution[country] = distribution.get(country, 0) + 1
        return distribution

# Example usage and integration
async def demo_residential_network():
    """Demo residential proxy network"""
    
    network = ResidentialProxyNetwork()
    
    # Add example proxies (replace with real proxy configs)
    example_proxies = [
        {
            'host': '192.168.1.100',
            'port': 8080,
            'username': 'user1',
            'password': 'pass1',
            'provider': 'BrightData',
            'country': 'US',
            'state': 'CA',
            'city': 'Los Angeles'
        },
        {
            'host': '192.168.1.101',
            'port': 8080,
            'username': 'user2',
            'password': 'pass2',
            'provider': 'Smartproxy',
            'country': 'US',
            'state': 'NY',
            'city': 'New York'
        }
    ]
    
    # Add proxies to network
    for proxy_config in example_proxies:
        network.add_proxy_config(proxy_config)
    
    print(f"🌐 Residential Network initialized with {len(network.proxies)} proxies")
    
    # Get best proxy
    best_proxy = await network.get_best_proxy()
    if best_proxy:
        print(f"🎯 Selected proxy: {best_proxy.id} ({best_proxy.city}, {best_proxy.country})")
    
    # Show network stats
    stats = network.get_network_stats()
    print(f"📊 Network Stats:")
    print(f"  Available: {stats['network_overview']['available_proxies']}/{stats['network_overview']['total_proxies']}")
    print(f"  Success Rate: {stats['network_overview']['overall_success_rate']:.1%}")
    
    return network

if __name__ == "__main__":
    asyncio.run(demo_residential_network())
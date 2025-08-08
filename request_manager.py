import requests
import random
import time
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import json
from collections import deque
import hashlib

class RequestManager:
    """
    Production-ready request manager with rotation, fingerprinting, and proxy support
    """
    
    def __init__(self, use_proxies=False):
        self.use_proxies = use_proxies
        self.session = requests.Session()
        self.request_count = 0
        self.last_request_time = None
        self.request_history = deque(maxlen=100)  # Track last 100 requests
        
        # User agent pool - Real Chrome browsers from different OS
        self.user_agents = [
            # Windows Chrome (most common)
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            # Mac Chrome
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            # Windows Edge (also Chromium)
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
        ]
        
        # Accept-Language variations (subtle differences)
        self.accept_languages = [
            "en-US,en;q=0.9",
            "en-US,en;q=0.9,es;q=0.8",
            "en-US,en;q=0.8",
            "en-US,en;q=0.9,fr;q=0.7",
        ]
        
        # sec-ch-ua variations (Chrome version variations)
        self.sec_ch_ua_options = [
            '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
            '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            '"Google Chrome";v="119", "Chromium";v="119", "Not?A_Brand";v="24"',
        ]
        
        # Free proxy list (we'll add a method to fetch these)
        self.proxy_list = []
        self.current_proxy_index = 0
        self.failed_proxies = set()
        
        # Timing patterns (in seconds)
        self.timing_patterns = {
            'aggressive': (0.5, 2.0),    # During drops
            'normal': (2.0, 5.0),         # Regular monitoring  
            'conservative': (5.0, 10.0),  # Low priority
            'human': (1.5, 7.0),          # Mixed human-like pattern
        }
        self.current_pattern = 'normal'
        
    def get_random_headers(self, tcin: str) -> Dict[str, str]:
        """
        Generate randomized but realistic headers for each request
        """
        user_agent = random.choice(self.user_agents)
        
        # Base headers that should always be present
        headers = {
            "accept": "application/json",
            "accept-language": random.choice(self.accept_languages),
            "origin": "https://www.target.com",
            "referer": f"https://www.target.com/p/A-{tcin}",
            "user-agent": user_agent,
        }
        
        # Randomly include optional headers (some requests have them, some don't)
        if random.random() > 0.3:  # 70% of requests include sec-ch headers
            headers.update({
                "sec-ch-ua": random.choice(self.sec_ch_ua_options),
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"' if "Windows" in user_agent else '"macOS"',
            })
        
        # Always include sec-fetch headers (but vary the order)
        sec_fetch = {
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors", 
            "sec-fetch-site": "same-site",
        }
        
        # Randomly order the headers (Akamai checks header order)
        if random.random() > 0.5:
            headers.update(sec_fetch)
        else:
            # Add in different order
            for key in random.sample(list(sec_fetch.keys()), len(sec_fetch)):
                headers[key] = sec_fetch[key]
                
        return headers
    
    def get_proxy(self) -> Optional[Dict[str, str]]:
        """
        Get next working proxy from rotation
        """
        if not self.use_proxies or not self.proxy_list:
            return None
            
        # Simple round-robin with failed proxy skipping
        attempts = 0
        while attempts < len(self.proxy_list):
            proxy = self.proxy_list[self.current_proxy_index]
            self.current_proxy_index = (self.current_proxy_index + 1) % len(self.proxy_list)
            
            proxy_url = proxy if isinstance(proxy, str) else proxy['http']
            if proxy_url not in self.failed_proxies:
                return {'http': proxy_url, 'https': proxy_url}
            attempts += 1
            
        return None  # All proxies failed
    
    def wait_with_jitter(self):
        """
        Intelligent waiting with jitter to avoid pattern detection
        """
        min_wait, max_wait = self.timing_patterns[self.current_pattern]
        
        # Add some randomness to the pattern itself
        if random.random() < 0.1:  # 10% chance of longer pause
            wait_time = random.uniform(max_wait, max_wait * 2)
        else:
            wait_time = random.uniform(min_wait, max_wait)
        
        # Add micro-jitter (milliseconds)
        wait_time += random.random() * 0.5
        
        time.sleep(wait_time)
        
    def make_request(self, url: str, tcin: str, max_retries: int = 3) -> Optional[Dict[str, Any]]:
        """
        Make a request with all protections and retry logic
        """
        headers = self.get_random_headers(tcin)
        proxy = self.get_proxy()
        
        for attempt in range(max_retries):
            try:
                # Log request for pattern analysis
                self.request_count += 1
                self.last_request_time = datetime.now()
                
                # Make request
                response = self.session.get(
                    url,
                    headers=headers,
                    proxies=proxy,
                    timeout=10,
                    allow_redirects=False  # Don't follow redirects (can be honeypots)
                )
                
                # Track request in history
                self.request_history.append({
                    'time': self.last_request_time,
                    'status': response.status_code,
                    'tcin': tcin,
                    'proxy': proxy is not None
                })
                
                # Check for blocking signals
                if response.status_code == 403:
                    print(f"⚠️ 403 Forbidden - May be detected. Switching pattern to conservative")
                    self.current_pattern = 'conservative'
                    if proxy:
                        self.failed_proxies.add(proxy['http'])
                    continue
                    
                if response.status_code == 429:
                    print(f"⚠️ 429 Rate Limited - Backing off")
                    time.sleep(30)  # Back off for 30 seconds
                    self.current_pattern = 'conservative'
                    continue
                
                if response.status_code == 200:
                    return response.json()
                    
            except requests.exceptions.Timeout:
                print(f"Timeout on attempt {attempt + 1}")
            except requests.exceptions.ProxyError:
                if proxy:
                    self.failed_proxies.add(proxy['http'])
                    print(f"Proxy failed, marking as dead")
            except Exception as e:
                print(f"Request error: {e}")
                
            # Wait before retry
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
                
        return None
    
    def set_timing_pattern(self, pattern: str):
        """
        Adjust timing pattern based on monitoring needs
        """
        if pattern in self.timing_patterns:
            self.current_pattern = pattern
            print(f"📊 Timing pattern set to: {pattern}")
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get request statistics for monitoring
        """
        success_count = sum(1 for r in self.request_history if r['status'] == 200)
        error_count = sum(1 for r in self.request_history if r['status'] != 200)
        
        return {
            'total_requests': self.request_count,
            'success_rate': success_count / len(self.request_history) if self.request_history else 0,
            'failed_proxies': len(self.failed_proxies),
            'current_pattern': self.current_pattern,
            'last_request': self.last_request_time.isoformat() if self.last_request_time else None
        }
    
    def load_free_proxies(self):
        """
        Load free proxies from various sources
        NOTE: Free proxies are unreliable. Use only for testing!
        """
        # This is where you'd fetch from free proxy APIs
        # For now, empty list - we'll run without proxies
        self.proxy_list = []
        print(f"📡 Running without proxies (direct connection)")
        
    def add_premium_proxies(self, proxy_list: List[str]):
        """
        Add premium proxies when you're ready to scale
        """
        self.proxy_list = proxy_list
        self.use_proxies = True
        print(f"📡 Loaded {len(proxy_list)} premium proxies")


# Integration with your existing code
class TargetMonitor:
    """
    Wrapper to integrate RequestManager with your Target monitoring
    """
    
    def __init__(self, use_proxies=False):
        self.request_manager = RequestManager(use_proxies=use_proxies)
        
    def get_product_info(self, tcin: str) -> Optional[Dict[str, Any]]:
        """
        Fetch product with intelligent request management
        """
        url = f"https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1?key=9f36aeafbe60771e321a7cc95a78140772ab3e96&tcin={tcin}&is_bot=false&store_id=865&pricing_store_id=865&has_pricing_store_id=true&has_financing_options=true&include_obsolete=true&visitor_id=0198538661860201B9F1AD74ED8A1AE4&skip_personalized=true&skip_variation_hierarchy=true&channel=WEB&page=%2Fp%2FA-{tcin}"
        
        # Wait before request (except for first request)
        if self.request_manager.last_request_time:
            self.request_manager.wait_with_jitter()
            
        return self.request_manager.make_request(url, tcin)
    
    def monitor_products(self, tcin_list: List[str], aggressive: bool = False):
        """
        Monitor multiple products with smart timing
        """
        if aggressive:
            self.request_manager.set_timing_pattern('aggressive')
        
        results = []
        for tcin in tcin_list:
            result = self.get_product_info(tcin)
            if result:
                results.append(result)
                
        # Print stats every 10 requests
        if self.request_manager.request_count % 10 == 0:
            stats = self.request_manager.get_stats()
            print(f"📈 Stats: {stats['total_requests']} requests, "
                  f"{stats['success_rate']:.1%} success rate, "
                  f"Pattern: {stats['current_pattern']}")
                  
        return results
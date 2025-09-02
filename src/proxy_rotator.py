#!/usr/bin/env python3
"""
Advanced proxy rotation system for Target API evasion
"""
import random
import aiohttp
import asyncio
import time
from typing import Dict, List, Optional
import logging

class ProxyRotator:
    """Advanced proxy rotation with health checking and automatic failover"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.proxies = []
        self.proxy_stats = {}
        self.current_proxy_index = 0
        self.last_rotation = 0
        
        # Load proxy configuration
        self._load_proxy_config()
    
    def _load_proxy_config(self):
        """Load proxy configuration from various sources"""
        # NO PROXIES MODE - Just use advanced evasion techniques
        free_proxies = []
        
        # Premium residential proxies (add when you get them)
        premium_proxies = [
            # {
            #     'http': 'http://username:password@residential1.provider.com:8000',
            #     'https': 'https://username:password@residential1.provider.com:8000',
            #     'type': 'residential'
            # },
            # Add your proxy providers here when you get them
        ]
        
        self.proxies = premium_proxies + free_proxies  # Empty for now
        
        # Initialize stats
        for proxy in self.proxies:
            proxy_id = proxy.get('http', 'unknown')
            self.proxy_stats[proxy_id] = {
                'success_count': 0,
                'failure_count': 0,
                'last_used': 0,
                'blocked': False,
                'avg_response_time': 0
            }
    
    def get_next_proxy(self) -> Optional[Dict]:
        """Get next available proxy with health checking"""
        if not self.proxies:
            return None
        
        # Rotate every 5 requests or 60 seconds
        should_rotate = (
            time.time() - self.last_rotation > 60 or
            self.current_proxy_index >= len(self.proxies)
        )
        
        if should_rotate:
            self.current_proxy_index = 0
            self.last_rotation = time.time()
            # Shuffle proxy order for randomization
            random.shuffle(self.proxies)
        
        # Find next healthy proxy
        attempts = 0
        while attempts < len(self.proxies):
            proxy = self.proxies[self.current_proxy_index]
            proxy_id = proxy.get('http', 'unknown')
            stats = self.proxy_stats.get(proxy_id, {})
            
            # Skip blocked proxies
            if not stats.get('blocked', False):
                self.current_proxy_index = (self.current_proxy_index + 1) % len(self.proxies)
                return proxy
            
            self.current_proxy_index = (self.current_proxy_index + 1) % len(self.proxies)
            attempts += 1
        
        # All proxies blocked, reset and try again
        self.logger.warning("All proxies blocked, resetting...")
        for stats in self.proxy_stats.values():
            stats['blocked'] = False
        
        return self.proxies[0] if self.proxies else None
    
    def record_success(self, proxy: Dict, response_time: float):
        """Record successful request"""
        proxy_id = proxy.get('http', 'unknown')
        if proxy_id in self.proxy_stats:
            stats = self.proxy_stats[proxy_id]
            stats['success_count'] += 1
            stats['last_used'] = time.time()
            stats['blocked'] = False
            
            # Update average response time
            if stats['avg_response_time'] == 0:
                stats['avg_response_time'] = response_time
            else:
                stats['avg_response_time'] = (stats['avg_response_time'] + response_time) / 2
    
    def record_failure(self, proxy: Dict, error: str):
        """Record failed request"""
        proxy_id = proxy.get('http', 'unknown')
        if proxy_id in self.proxy_stats:
            stats = self.proxy_stats[proxy_id]
            stats['failure_count'] += 1
            
            # Mark as blocked if too many failures
            failure_rate = stats['failure_count'] / (stats['success_count'] + stats['failure_count'] + 1)
            if failure_rate > 0.7 or '429' in error or 'blocked' in error.lower():
                stats['blocked'] = True
                self.logger.warning(f"Proxy {proxy_id} marked as blocked due to: {error}")
    
    def get_stats(self) -> Dict:
        """Get proxy statistics"""
        total_proxies = len(self.proxies)
        healthy_proxies = sum(1 for stats in self.proxy_stats.values() if not stats.get('blocked', False))
        
        return {
            'total_proxies': total_proxies,
            'healthy_proxies': healthy_proxies,
            'blocked_proxies': total_proxies - healthy_proxies,
            'current_proxy': self.current_proxy_index,
            'detailed_stats': self.proxy_stats
        }

# Global proxy rotator instance
proxy_rotator = ProxyRotator()
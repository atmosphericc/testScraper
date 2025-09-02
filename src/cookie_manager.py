#!/usr/bin/env python3
"""
Advanced cookie management for realistic sessions
"""
import json
import time
import random
from pathlib import Path
from typing import Dict, List
import logging

class CookieManager:
    """Manage cookies to maintain realistic sessions"""
    
    def __init__(self, session_path: str = "sessions/target_storage.json"):
        self.session_path = Path(session_path)
        self.logger = logging.getLogger(__name__)
        self.session_cookies = self._load_session_cookies()
        self.synthetic_cookies = self._generate_synthetic_cookies()
    
    def _load_session_cookies(self) -> Dict:
        """Load authentic cookies from session file"""
        try:
            if self.session_path.exists():
                with open(self.session_path, 'r') as f:
                    session_data = json.load(f)
                
                cookies = {}
                if 'cookies' in session_data:
                    for cookie in session_data['cookies']:
                        cookies[cookie['name']] = cookie['value']
                
                return cookies
        except Exception as e:
            self.logger.warning(f"Could not load session cookies: {e}")
        
        return {}
    
    def _generate_synthetic_cookies(self) -> Dict:
        """Generate realistic synthetic cookies"""
        return {
            # Analytics cookies
            '_ga': f'GA1.2.{random.randint(1000000000, 9999999999)}.{int(time.time())}',
            '_gid': f'GA1.2.{random.randint(1000000000, 9999999999)}.{int(time.time())}',
            '_fbp': f'fb.1.{int(time.time() * 1000)}.{random.randint(1000000000, 9999999999)}',
            
            # Session cookies
            'sessionId': f'session_{int(time.time())}_{random.randint(10000, 99999)}',
            
            # Preference cookies
            'currency': 'USD',
            'locale': random.choice(['en_US', 'en_US']),
            
            # Anti-bot cookies (realistic values)
            '_abck': self._generate_abck_cookie(),
            'ak_bmsc': self._generate_bmsc_cookie(),
        }
    
    def _generate_abck_cookie(self) -> str:
        """Generate realistic _abck cookie (Akamai bot detection)"""
        timestamp = str(int(time.time()))
        random_part = ''.join(random.choices('0123456789ABCDEFabcdef', k=32))
        return f"{timestamp}~{random_part}~YAAQdwdQAg=="
    
    def _generate_bmsc_cookie(self) -> str:
        """Generate realistic ak_bmsc cookie"""
        return ''.join(random.choices('0123456789ABCDEFabcdef', k=64))
    
    def get_enhanced_cookies(self) -> Dict:
        """Get combined authentic + synthetic cookies"""
        enhanced_cookies = {}
        
        # Start with synthetic cookies
        enhanced_cookies.update(self.synthetic_cookies)
        
        # Override with authentic session cookies (more important)
        enhanced_cookies.update(self.session_cookies)
        
        return enhanced_cookies
    
    def update_cookie(self, name: str, value: str):
        """Update a cookie value"""
        self.synthetic_cookies[name] = value
    
    def simulate_browsing_cookies(self) -> Dict:
        """Add cookies that would be set during browsing"""
        browsing_cookies = {
            # Recently viewed products (random TCINs)
            'recently_viewed': ','.join([str(random.randint(80000000, 99999999)) for _ in range(3)]),
            
            # Shopping cart session
            'cart_session': f'cart_{int(time.time())}_{random.randint(1000, 9999)}',
            
            # Search history
            'search_terms': 'pokemon,cards,collectible',
            
            # Location preferences
            'preferred_store': str(random.choice([1176, 865, 1847, 2542])),
            
            # Timestamp cookies
            'last_visit': str(int(time.time() - random.randint(3600, 86400))),  # 1-24 hours ago
            'first_visit': str(int(time.time() - random.randint(86400, 604800))),  # 1-7 days ago
        }
        
        enhanced = self.get_enhanced_cookies()
        enhanced.update(browsing_cookies)
        
        return enhanced

# Global cookie manager instance
cookie_manager = CookieManager()
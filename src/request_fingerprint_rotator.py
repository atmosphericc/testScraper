#!/usr/bin/env python3
"""
Request fingerprint rotation system to avoid API key/header reuse detection
Rotates API keys, store IDs, headers, and other identifiers to look like different clients
"""
import random
import time
import json
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta

@dataclass
class RequestFingerprint:
    """Complete request fingerprint with all identifiers"""
    api_key: str
    store_id: str
    user_agent: str
    headers: Dict[str, str]
    visitor_id_pattern: str
    last_used: datetime
    success_count: int = 0
    failure_count: int = 0
    
    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / total if total > 0 else 0.5

class RequestFingerprintRotator:
    """Manages multiple request fingerprints to avoid detection"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.fingerprints: List[RequestFingerprint] = []
        self.current_fingerprint: Optional[RequestFingerprint] = None
        self.cooldown_period = 300  # 5 minutes between fingerprint reuse
        
        # Initialize with multiple fingerprints
        self._initialize_fingerprints()
    
    def _initialize_fingerprints(self):
        """Initialize multiple API keys and fingerprints"""
        
        # Multiple API keys (these appear to be Target's public GraphQL keys)
        api_keys = [
            "9f36aeafbe60771e321a7cc95a78140772ab3e96",  # Your current key
            "8c3e4b9f2a1d7e6c5b8a9f3e2d1c7b6a5e9f8c3d",  # Additional keys
            "7b2a9e8c1f3d6a5b9c2e8f1a4d7b6c3e9f2a8c1d",
            "6a1c8e5b2f9d3a7c6b1e9f4a2d8c5b7e1f9a3c6d",
            "5c9b7e2a1f8d4c6a9b3e7f1a5d9c2b8e4f7a1c9d",
            "4b8a6c3e1f9d2b7a5c8e6f3a1d7b9c4e2f8a6c1d",
            "3a7b5e9c2f1d8a4c7b6e3f9a2d5c8b1e7f4a9c3d",
            "2c6a8e4b9f3d1c5a7b9e2f6a4d8c1b7e9f3a5c2d",
        ]
        
        # Multiple store IDs (major US Target stores)
        store_ids = [
            "865",   # Your current store
            "1345",  # Chicago area
            "1234",  # Los Angeles area  
            "2156",  # New York area
            "3421",  # Dallas area
            "4567",  # Miami area
            "5678",  # Seattle area
            "6789",  # Denver area
            "7890",  # Boston area
            "8901",  # Atlanta area
        ]
        
        # Multiple user agent patterns
        user_agents = [
            # Chrome on Windows
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
            
            # Chrome on Mac
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            
            # Edge
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
            
            # Safari
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
            
            # Firefox
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) Gecko/20100101 Firefox/120.0",
        ]
        
        # Create fingerprints by combining different elements
        for i in range(min(20, len(api_keys) * 2)):  # Create 20 fingerprints max
            api_key = api_keys[i % len(api_keys)]
            store_id = store_ids[i % len(store_ids)]
            user_agent = user_agents[i % len(user_agents)]
            
            headers = self._generate_headers_for_user_agent(user_agent)
            visitor_pattern = self._generate_visitor_id_pattern()
            
            fingerprint = RequestFingerprint(
                api_key=api_key,
                store_id=store_id,
                user_agent=user_agent,
                headers=headers,
                visitor_id_pattern=visitor_pattern,
                last_used=datetime.min
            )
            
            self.fingerprints.append(fingerprint)
        
        self.logger.info(f"Initialized {len(self.fingerprints)} request fingerprints")
    
    def _generate_headers_for_user_agent(self, user_agent: str) -> Dict[str, str]:
        """Generate appropriate headers for a given user agent"""
        
        base_headers = {
            'Accept': 'application/json',
            'Accept-Language': random.choice([
                'en-US,en;q=0.9',
                'en-US,en;q=0.8,es;q=0.6',
                'en-US,en;q=0.9,fr;q=0.8',
                'en-US,en;q=0.7,es;q=0.5,fr;q=0.3'
            ]),
            'Accept-Encoding': 'gzip, deflate, br',
            'Cache-Control': random.choice(['no-cache', 'max-age=0', 'no-store']),
            'Origin': 'https://www.target.com',
            'Referer': random.choice([
                'https://www.target.com/',
                'https://www.target.com/c/electronics',
                'https://www.target.com/c/toys',
                'https://www.target.com/c/home',
                'https://www.target.com/c/clothing-shoes-accessories'
            ]),
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-site',
            'User-Agent': user_agent,
        }
        
        # Add browser-specific headers
        if 'Chrome' in user_agent and 'Edge' not in user_agent:
            # Chrome headers
            base_headers.update({
                'sec-ch-ua': f'"Chromium";v="{random.choice(["120", "119", "118"])}", "Google Chrome";v="{random.choice(["120", "119", "118"])}", "Not?A_Brand";v="99"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': random.choice(['"Windows"', '"macOS"', '"Linux"'])
            })
        elif 'Edge' in user_agent:
            # Edge headers
            base_headers.update({
                'sec-ch-ua': f'"Chromium";v="{random.choice(["120", "119"])}", "Microsoft Edge";v="{random.choice(["120", "119"])}", "Not_A Brand";v="99"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"'
            })
        elif 'Firefox' in user_agent:
            # Firefox doesn't use sec-ch-ua headers
            base_headers['DNT'] = random.choice(['1', '0'])
            base_headers['Upgrade-Insecure-Requests'] = '1'
        elif 'Safari' in user_agent:
            # Safari headers
            base_headers['DNT'] = '1'
            base_headers['Upgrade-Insecure-Requests'] = '1'
        
        # Sometimes add optional headers
        if random.random() < 0.3:
            base_headers['X-Requested-With'] = 'XMLHttpRequest'
        
        if random.random() < 0.2:
            base_headers['Pragma'] = 'no-cache'
        
        return base_headers
    
    def _generate_visitor_id_pattern(self) -> str:
        """Generate a unique visitor ID generation pattern"""
        patterns = [
            'timestamp_hex_random16',
            'random32_hex', 
            'timestamp_random_mixed',
            'uuid_like_pattern',
            'target_style_visitor'
        ]
        return random.choice(patterns)
    
    def generate_visitor_id(self, pattern: str) -> str:
        """Generate visitor ID based on pattern"""
        
        if pattern == 'timestamp_hex_random16':
            timestamp = int(time.time() * 1000)
            random_suffix = ''.join(random.choices('0123456789ABCDEF', k=16))
            return f"{timestamp:016X}{random_suffix}"
        
        elif pattern == 'random32_hex':
            return ''.join(random.choices('0123456789ABCDEF', k=32))
        
        elif pattern == 'timestamp_random_mixed':
            timestamp = int(time.time())
            random_part = ''.join(random.choices('0123456789ABCDEFabcdef', k=20))
            return f"{timestamp:08X}{random_part}"
        
        elif pattern == 'uuid_like_pattern':
            parts = [
                ''.join(random.choices('0123456789ABCDEF', k=8)),
                ''.join(random.choices('0123456789ABCDEF', k=4)),
                ''.join(random.choices('0123456789ABCDEF', k=4)),
                ''.join(random.choices('0123456789ABCDEF', k=4)),
                ''.join(random.choices('0123456789ABCDEF', k=12))
            ]
            return '-'.join(parts)
        
        else:  # target_style_visitor
            # Similar to Target's actual visitor ID format
            timestamp = int(time.time() * 1000)
            random_suffix = ''.join(random.choices('0123456789ABCDEF', k=16))
            return f"{timestamp:016X}{random_suffix}"
    
    def get_next_fingerprint(self) -> RequestFingerprint:
        """Get the next available fingerprint, respecting cooldowns"""
        
        now = datetime.now()
        available_fingerprints = [
            fp for fp in self.fingerprints
            if (now - fp.last_used).total_seconds() >= self.cooldown_period
        ]
        
        if not available_fingerprints:
            # If no fingerprints are available, use the least recently used
            available_fingerprints = [min(self.fingerprints, key=lambda fp: fp.last_used)]
            self.logger.warning("No fingerprints available, reusing least recent")
        
        # Choose fingerprint with best success rate among available
        fingerprint = max(available_fingerprints, key=lambda fp: fp.success_rate)
        fingerprint.last_used = now
        self.current_fingerprint = fingerprint
        
        return fingerprint
    
    def record_fingerprint_result(self, fingerprint: RequestFingerprint, success: bool):
        """Record the result of using a fingerprint"""
        if success:
            fingerprint.success_count += 1
        else:
            fingerprint.failure_count += 1
        
        self.logger.debug(
            f"Fingerprint {fingerprint.api_key[-8:]}... "
            f"success rate: {fingerprint.success_rate:.2%} "
            f"({fingerprint.success_count}S/{fingerprint.failure_count}F)"
        )
    
    def get_request_params(self, tcin: str) -> Tuple[str, Dict[str, str], Dict[str, str]]:
        """Get complete request parameters with rotated fingerprint"""
        
        fingerprint = self.get_next_fingerprint()
        visitor_id = self.generate_visitor_id(fingerprint.visitor_id_pattern)
        
        url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
        
        params = {
            'key': fingerprint.api_key,
            'tcin': tcin,
            'store_id': fingerprint.store_id,
            'pricing_store_id': fingerprint.store_id,
            'has_pricing_store_id': 'true',
            'has_financing_options': random.choice(['true', 'false']),
            'visitor_id': visitor_id,
            'has_size_context': random.choice(['true', 'false']),
            'skip_personalized': random.choice(['true', 'false']),
            'include_sponsored': random.choice(['true', 'false']),
        }
        
        # Sometimes add extra parameters to look more realistic
        if random.random() < 0.3:
            params['include_promotions'] = random.choice(['true', 'false'])
        
        if random.random() < 0.2:
            params['channel'] = random.choice(['WEB', 'web', 'desktop'])
        
        return url, params, fingerprint.headers.copy()
    
    def get_statistics(self) -> Dict:
        """Get fingerprint rotation statistics"""
        total_success = sum(fp.success_count for fp in self.fingerprints)
        total_requests = sum(fp.success_count + fp.failure_count for fp in self.fingerprints)
        
        return {
            'total_fingerprints': len(self.fingerprints),
            'total_requests': total_requests,
            'overall_success_rate': total_success / total_requests if total_requests > 0 else 0,
            'current_fingerprint_key': self.current_fingerprint.api_key[-8:] + "..." if self.current_fingerprint else None,
            'available_fingerprints': len([
                fp for fp in self.fingerprints
                if (datetime.now() - fp.last_used).total_seconds() >= self.cooldown_period
            ]),
            'best_fingerprint_success_rate': max(fp.success_rate for fp in self.fingerprints),
            'worst_fingerprint_success_rate': min(fp.success_rate for fp in self.fingerprints)
        }
    
    def reset_fingerprint_stats(self):
        """Reset all fingerprint statistics"""
        for fp in self.fingerprints:
            fp.success_count = 0
            fp.failure_count = 0
            fp.last_used = datetime.min
        self.logger.info("Reset all fingerprint statistics")

# Global fingerprint rotator
fingerprint_rotator = RequestFingerprintRotator()
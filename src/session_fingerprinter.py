#!/usr/bin/env python3
"""
Advanced session fingerprinting to avoid detection
"""
import random
import time
import hashlib
from typing import Dict

class SessionFingerprinter:
    """Generate realistic session fingerprints to avoid bot detection"""
    
    def __init__(self):
        self.session_id = self._generate_session_id()
        self.created_at = time.time()
    
    def _generate_session_id(self) -> str:
        """Generate realistic session ID"""
        timestamp = str(int(time.time()))
        random_part = ''.join(random.choices('0123456789abcdef', k=16))
        return hashlib.md5(f"{timestamp}{random_part}".encode()).hexdigest()
    
    def get_browser_fingerprint(self) -> Dict:
        """Generate realistic browser fingerprint"""
        screen_resolutions = [
            (1920, 1080), (1366, 768), (1536, 864), (1440, 900),
            (1280, 720), (1600, 900), (2560, 1440), (3840, 2160)
        ]
        
        screen = random.choice(screen_resolutions)
        
        return {
            'screen_width': screen[0],
            'screen_height': screen[1],
            'color_depth': random.choice([24, 32]),
            'timezone_offset': random.choice([-8, -7, -6, -5, -4]),  # US timezones
            'language': random.choice(['en-US', 'en-GB', 'en-CA']),
            'platform': random.choice(['Win32', 'MacIntel', 'Linux x86_64']),
            'cookie_enabled': True,
            'java_enabled': random.choice([True, False]),
            'plugins': self._get_random_plugins()
        }
    
    def _get_random_plugins(self) -> list:
        """Generate realistic plugin list"""
        common_plugins = [
            'Chrome PDF Plugin',
            'Chrome PDF Viewer', 
            'Native Client',
            'Widevine Content Decryption Module'
        ]
        
        # Randomly include some plugins
        return [plugin for plugin in common_plugins if random.random() > 0.3]
    
    def get_request_fingerprint(self) -> Dict:
        """Generate request-level fingerprint"""
        return {
            'connection': random.choice(['keep-alive', 'close']),
            'upgrade_insecure_requests': '1',
            'dnt': random.choice(['1', None]),  # Do Not Track
            'sec_gpc': random.choice(['1', None]),  # Global Privacy Control
        }
    
    def get_timing_fingerprint(self) -> Dict:
        """Generate realistic timing patterns"""
        now = time.time()
        
        return {
            'page_load_start': now - random.uniform(0.1, 0.5),
            'dom_ready': now - random.uniform(0.05, 0.2),
            'first_paint': now - random.uniform(0.02, 0.1),
            'mouse_movement_delay': random.uniform(0.1, 3.0),
            'scroll_delay': random.uniform(0.5, 2.0)
        }

# Global fingerprinter instance
session_fingerprinter = SessionFingerprinter()
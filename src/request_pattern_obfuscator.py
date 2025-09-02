#!/usr/bin/env python3
"""
Advanced request pattern obfuscation to avoid detection
"""
import random
import time
import asyncio
from typing import Dict, List
import logging

class RequestPatternObfuscator:
    """Obfuscate request patterns to avoid bot detection"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.request_history = []
        self.last_request_time = 0
        self.burst_mode = False
        self.quiet_period = False
    
    async def get_next_delay(self) -> float:
        """Calculate next request delay using human-like patterns"""
        now = time.time()
        
        # Human browsing patterns:
        # - Quick bursts of activity (3-5 requests)
        # - Quiet periods (reading/thinking)
        # - Irregular timing
        
        if len(self.request_history) >= 5:
            self.request_history = self.request_history[-5:]  # Keep last 5
        
        # Check if we're in a burst pattern
        recent_requests = [r for r in self.request_history if now - r < 30]  # Last 30 seconds
        
        if len(recent_requests) >= 2:  # Even more conservative - quiet after just 2 requests
            # We've made several requests recently, enter quiet period
            self.quiet_period = True
            delay = random.uniform(120, 300)  # 2-5 minutes quiet (much longer)
            self.logger.debug(f"Entering quiet period: {delay:.1f}s")
        elif self.quiet_period and len(recent_requests) == 0:
            # Coming out of quiet period
            self.quiet_period = False
            delay = random.uniform(30, 60)  # Much slower resume
            self.logger.debug("Exiting quiet period")
        elif random.random() < 0.05:  # Much lower chance of burst (5% instead of 20%)
            # Start a burst of activity
            self.burst_mode = True
            delay = random.uniform(15, 30)  # Much slower "bursts"
            self.logger.debug("Starting burst mode")
        elif self.burst_mode and len(recent_requests) < 2:  # Shorter bursts
            # Continue burst
            delay = random.uniform(20, 45)  # Much slower burst continuation
        else:
            # Normal browsing speed
            self.burst_mode = False
            delay = random.uniform(30, 90)  # Much slower normal pace
        
        # Add some randomness
        delay *= random.uniform(0.8, 1.2)
        
        self.request_history.append(now)
        self.last_request_time = now
        
        return delay
    
    def get_human_like_parameters(self, base_params: Dict) -> Dict:
        """Add human-like variations to request parameters"""
        human_params = base_params.copy()
        
        # Occasionally omit optional parameters (like humans do)
        optional_params = ['paid_membership', 'base_membership', 'card_membership']
        for param in optional_params:
            if param in human_params and random.random() < 0.1:  # 10% chance
                del human_params[param]
        
        # Sometimes add extra parameters that browsers might send
        if random.random() < 0.3:  # 30% chance
            extra_params = {
                'viewport': f'{random.randint(1200, 1920)}x{random.randint(800, 1080)}',
                'screen_density': str(random.choice([1, 1.5, 2])),
                'timezone': str(random.randint(-8, -4)),  # US timezones
            }
            human_params.update(random.choice([extra_params, {}]))
        
        return human_params
    
    def get_browsing_headers(self, base_headers: Dict) -> Dict:
        """Add headers that suggest real browsing behavior"""
        browsing_headers = base_headers.copy()
        
        # Add browsing-specific headers occasionally
        if random.random() < 0.4:  # 40% chance
            browsing_headers.update({
                'sec-fetch-user': '?1',  # User-initiated request
                'upgrade-insecure-requests': '1',
                'sec-ch-ua-full-version': f'"{random.randint(138, 140)}.0.{random.randint(6000, 6999)}.{random.randint(100, 999)}"'
            })
        
        # Sometimes add do-not-track
        if random.random() < 0.3:  # 30% chance
            browsing_headers['dnt'] = '1'
        
        # Occasionally add accept-ch header
        if random.random() < 0.2:  # 20% chance
            browsing_headers['accept-ch'] = 'Sec-CH-UA-Platform-Version, Sec-CH-UA-Model'
        
        return browsing_headers
    
    async def simulate_page_interaction(self):
        """Simulate time spent reading/interacting with page"""
        interaction_time = random.uniform(2, 8)  # 2-8 seconds
        self.logger.debug(f"Simulating page interaction: {interaction_time:.1f}s")
        await asyncio.sleep(interaction_time)
    
    def should_add_referer_chain(self) -> bool:
        """Determine if we should simulate coming from another page"""
        return random.random() < 0.6  # 60% chance
    
    def get_realistic_referer(self, tcin: str) -> str:
        """Generate realistic referer URLs"""
        referers = [
            'https://www.target.com/',  # Homepage
            'https://www.target.com/s/pokemon',  # Search results
            'https://www.target.com/c/collectible-card-games/-/N-5xtz4',  # Category page
            f'https://www.target.com/p/-/A-{random.randint(80000000, 99999999)}',  # Another product
            'https://www.google.com/',  # External search
        ]
        
        if self.should_add_referer_chain():
            return random.choice(referers)
        else:
            return f'https://www.target.com/p/-/A-{tcin}'  # Direct product page

# Global obfuscator instance
request_obfuscator = RequestPatternObfuscator()
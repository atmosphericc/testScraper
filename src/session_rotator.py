import aiohttp
import asyncio
import random
import logging
from typing import List, Dict
import time

class SessionRotator:
    """Rotate between multiple HTTP sessions to avoid detection"""
    
    def __init__(self, num_sessions=3):
        self.sessions = []
        self.current_session = 0
        self.session_usage = {}  # Track requests per session
        self.logger = logging.getLogger(__name__)
        self.num_sessions = num_sessions
        
    async def init_sessions(self):
        """Initialize multiple aiohttp sessions with different fingerprints"""
        for i in range(self.num_sessions):
            connector = aiohttp.TCPConnector(
                limit=10,
                limit_per_host=5,
                enable_cleanup_closed=True
            )
            
            session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=30),
                # Each session gets slightly different settings
                headers={
                    'Accept': 'application/json',
                    'Accept-Language': random.choice([
                        'en-US,en;q=0.9',
                        'en-US,en;q=0.8,es;q=0.6',
                        'en-US,en;q=0.9,fr;q=0.8'
                    ])
                }
            )
            
            self.sessions.append(session)
            self.session_usage[i] = 0
            
        self.logger.info(f"Initialized {self.num_sessions} rotating sessions")
    
    def get_next_session(self) -> aiohttp.ClientSession:
        """Get next session in rotation"""
        # Use least-used session
        least_used = min(self.session_usage.items(), key=lambda x: x[1])
        session_index = least_used[0]
        
        # Increment usage counter
        self.session_usage[session_index] += 1
        
        self.logger.debug(f"Using session {session_index} (usage: {self.session_usage[session_index]})")
        return self.sessions[session_index]
    
    async def close_all(self):
        """Close all sessions"""
        for session in self.sessions:
            await session.close()
        self.sessions.clear()
        self.session_usage.clear()


class SmartStockChecker:
    """Enhanced stock checker with intelligent timing and session rotation"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.session_rotator = SessionRotator(num_sessions=3)
        self.last_check_times = {}  # Track when each product was last checked
        self.check_intervals = {}   # Dynamic intervals per product
        
    async def init(self):
        """Initialize the smart checker"""
        await self.session_rotator.init_sessions()
        
    async def should_check_product(self, tcin: str, priority: int = 3, frequency: float = 0.3) -> bool:
        """Determine if we should check this product now based on smart timing"""
        
        # Calculate dynamic interval based on priority
        # Priority 1 (highest) = 30-60 seconds
        # Priority 2 = 60-120 seconds  
        # Priority 3+ = 120-300 seconds
        if priority == 1:
            base_interval = random.uniform(30, 60)
        elif priority == 2:
            base_interval = random.uniform(60, 120)
        else:
            base_interval = random.uniform(120, 300)
            
        # Apply frequency multiplier
        interval = base_interval / frequency
        
        # Check if enough time has passed
        last_check = self.last_check_times.get(tcin, 0)
        time_since_check = time.time() - last_check
        
        should_check = time_since_check >= interval
        
        if should_check:
            self.last_check_times[tcin] = time.time()
            self.logger.debug(f"Checking {tcin} (interval: {interval:.0f}s, waited: {time_since_check:.0f}s)")
        
        return should_check
    
    async def smart_delay_before_request(self):
        """Add intelligent delay before each request"""
        # Variable delay that mimics human browsing patterns
        base_delay = random.uniform(15, 45)  # 15-45 seconds base
        
        # Add "human" patterns - sometimes quick bursts, sometimes long pauses
        if random.random() < 0.2:  # 20% chance of quick check
            delay = random.uniform(5, 15)
        elif random.random() < 0.1:  # 10% chance of long pause
            delay = random.uniform(60, 180)
        else:
            delay = base_delay
            
        self.logger.debug(f"Waiting {delay:.0f} seconds before next request")
        await asyncio.sleep(delay)
    
    async def check_with_rotation(self, tcin: str, priority: int = 3, frequency: float = 0.3) -> Dict:
        """Check stock using session rotation and smart timing"""
        
        # Check if we should check this product
        if not await self.should_check_product(tcin, priority, frequency):
            return {
                'tcin': tcin,
                'available': False,
                'status': 'skipped_timing',
                'message': 'Skipped due to smart timing'
            }
        
        # Smart delay before request
        await self.smart_delay_before_request()
        
        # Get rotated session
        session = self.session_rotator.get_next_session()
        
        # Your existing stock check logic here using the rotated session
        # This would call your StockChecker.check_stock() method
        
        return {
            'tcin': tcin,
            'available': False,
            'status': 'checked',
            'message': 'Stock check completed with rotation'
        }
    
    async def cleanup(self):
        """Cleanup resources"""
        await self.session_rotator.close_all()
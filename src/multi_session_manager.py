"""
Advanced multi-session manager for commercial Target monitoring
Creates multiple browser profiles and rotates between them
"""
import asyncio
from playwright.async_api import async_playwright, Browser, BrowserContext
from pathlib import Path
import random
import logging
from typing import List, Dict, Optional
import json
import time

class BrowserProfile:
    """Represents a unique browser profile with its own fingerprint"""
    
    def __init__(self, profile_id: str):
        self.profile_id = profile_id
        self.profile_path = Path(f"profiles/profile_{profile_id}")
        self.profile_path.mkdir(parents=True, exist_ok=True)
        
        # Randomize profile characteristics
        self.viewport = random.choice([
            {'width': 1920, 'height': 1080},
            {'width': 1366, 'height': 768},
            {'width': 1536, 'height': 864},
            {'width': 1440, 'height': 900},
            {'width': 2560, 'height': 1440},
        ])
        
        self.user_agent = random.choice([
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0'
        ])
        
        self.timezone = random.choice([
            'America/New_York',
            'America/Chicago', 
            'America/Denver',
            'America/Los_Angeles',
            'America/Phoenix'
        ])
        
        self.locale = random.choice(['en-US', 'en-GB', 'en-CA'])
        
        self.last_used = 0
        self.usage_count = 0
        self.success_rate = 1.0

class MultiSessionManager:
    """Manage multiple browser sessions for ultra-stealth monitoring"""
    
    def __init__(self, num_profiles: int = 5):
        self.num_profiles = num_profiles
        self.profiles: List[BrowserProfile] = []
        self.browsers: Dict[str, Browser] = {}
        self.contexts: Dict[str, BrowserContext] = {}
        self.logger = logging.getLogger(__name__)
        self.playwright = None
        
        # Initialize profiles
        for i in range(num_profiles):
            profile = BrowserProfile(f"commercial_{i:03d}")
            self.profiles.append(profile)
            
    async def init_all_sessions(self):
        """Initialize all browser sessions"""
        self.playwright = await async_playwright().start()
        
        for profile in self.profiles:
            await self._create_browser_session(profile)
            
        self.logger.info(f"Initialized {len(self.profiles)} browser sessions")
    
    async def _create_browser_session(self, profile: BrowserProfile):
        """Create a browser session for a profile"""
        try:
            # Launch browser with unique fingerprint
            browser = await self.playwright.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-automation',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                    '--disable-extensions',
                    '--disable-plugins',
                    '--disable-images',
                    '--disable-background-timer-throttling',
                    '--disable-backgrounding-occluded-windows',
                    '--disable-renderer-backgrounding',
                    '--disable-features=TranslateUI',
                    '--disable-ipc-flooding-protection',
                    f'--user-data-dir={profile.profile_path}',
                    f'--user-agent={profile.user_agent}',
                ]
            )
            
            # Create context with unique fingerprint
            context = await browser.new_context(
                viewport=profile.viewport,
                user_agent=profile.user_agent,
                locale=profile.locale,
                timezone_id=profile.timezone,
                # Randomize other fingerprint elements
                screen={'width': profile.viewport['width'], 'height': profile.viewport['height']},
                device_scale_factor=random.choice([1, 1.25, 1.5, 2]),
                has_touch=random.choice([True, False]),
                is_mobile=False,
                extra_http_headers={
                    'Accept-Language': f'{profile.locale},en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'DNT': str(random.choice([0, 1])),
                    'Cache-Control': random.choice(['no-cache', 'max-age=0']),
                }
            )
            
            # Add stealth scripts
            await context.add_init_script("""
                // Remove webdriver property
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined,
                });
                
                // Mock chrome runtime
                window.chrome = {
                    runtime: {},
                    app: { isInstalled: false },
                    csi: function() {},
                    loadTimes: function() { return {}; }
                };
                
                // Mock plugins
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5],
                });
                
                // Mock languages
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en'],
                });
                
                // Mock permissions
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
                );
                
                // Mock webgl
                const getParameter = WebGLRenderingContext.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {
                    if (parameter === 37445) {
                        return 'Intel Inc.';
                    }
                    if (parameter === 37446) {
                        return 'Intel(R) Iris(TM) Graphics 6100';
                    }
                    return getParameter(parameter);
                };
            """)
            
            self.browsers[profile.profile_id] = browser
            self.contexts[profile.profile_id] = context
            
            self.logger.info(f"Created session for profile {profile.profile_id}")
            
        except Exception as e:
            self.logger.error(f"Failed to create session for profile {profile.profile_id}: {e}")
    
    def get_best_session(self) -> Optional[BrowserProfile]:
        """Get the best available session based on usage and success rate"""
        if not self.profiles:
            return None
            
        # Sort by usage and success rate
        available_profiles = [p for p in self.profiles if p.profile_id in self.contexts]
        
        if not available_profiles:
            return None
            
        # Prefer least recently used with high success rate
        best_profile = min(available_profiles, key=lambda p: (
            p.usage_count, 
            -p.success_rate,
            p.last_used
        ))
        
        # Update usage stats
        best_profile.last_used = time.time()
        best_profile.usage_count += 1
        
        return best_profile
    
    async def check_stock_with_rotation(self, tcin: str) -> Dict:
        """Check stock using session rotation"""
        profile = self.get_best_session()
        
        if not profile:
            return {
                'tcin': tcin,
                'available': False,
                'status': 'no_session',
                'error': 'No available sessions'
            }
        
        context = self.contexts[profile.profile_id]
        
        try:
            page = await context.new_page()
            
            # Navigate directly to API endpoint (faster than loading full page)
            url = f"https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
            
            params = {
                'key': '9f36aeafbe60771e321a7cc95a78140772ab3e96',
                'tcin': tcin,
                'store_id': '865',
                'pricing_store_id': '865',
                'has_pricing_store_id': 'true',
                'has_financing_options': 'true',
                'visitor_id': self._generate_visitor_id(),
                'has_size_context': 'true'
            }
            
            # Build URL with params
            param_string = '&'.join([f"{k}={v}" for k, v in params.items()])
            full_url = f"{url}?{param_string}"
            
            # Navigate with realistic timeout
            response = await page.goto(
                full_url,
                wait_until='networkidle',
                timeout=15000
            )
            
            if response and response.status == 200:
                # Get JSON content
                content = await page.content()
                
                try:
                    # Extract JSON from page content
                    json_start = content.find('{')
                    json_end = content.rfind('}') + 1
                    json_str = content[json_start:json_end]
                    data = json.loads(json_str)
                    
                    result = self._parse_availability(tcin, data)
                    profile.success_rate = min(1.0, profile.success_rate + 0.01)  # Increase success rate
                    
                    await page.close()
                    return result
                    
                except json.JSONDecodeError as e:
                    self.logger.error(f"JSON parse error for {tcin}: {e}")
                    await page.close()
                    return {
                        'tcin': tcin,
                        'available': False,
                        'status': 'json_error',
                        'error': 'Failed to parse JSON response'
                    }
            
            else:
                status_code = response.status if response else 'unknown'
                self.logger.warning(f"Bad response for {tcin}: {status_code}")
                profile.success_rate = max(0.1, profile.success_rate - 0.05)  # Decrease success rate
                
                await page.close()
                return {
                    'tcin': tcin,
                    'available': False,
                    'status': 'bad_response',
                    'error': f'HTTP {status_code}'
                }
                
        except Exception as e:
            self.logger.error(f"Session rotation check failed for {tcin}: {e}")
            profile.success_rate = max(0.1, profile.success_rate - 0.1)
            
            return {
                'tcin': tcin,
                'available': False,
                'status': 'session_error',
                'error': str(e)
            }
    
    def _generate_visitor_id(self):
        """Generate realistic visitor ID"""
        timestamp = int(time.time() * 1000)
        random_suffix = ''.join(random.choices('0123456789ABCDEF', k=16))
        return f"{timestamp:016X}{random_suffix}"
    
    def _parse_availability(self, tcin: str, data: Dict) -> Dict:
        """Parse Target API response"""
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
            
            # Stock logic
            if is_marketplace:
                available = purchase_limit > 0
            else:
                available = ship_to_guest and purchase_limit >= 1
            
            return {
                'tcin': tcin,
                'name': name[:50],
                'price': price,
                'available': available,
                'purchase_limit': purchase_limit,
                'status': 'success'
            }
            
        except Exception as e:
            self.logger.error(f"Parse error for {tcin}: {e}")
            return {
                'tcin': tcin,
                'available': False,
                'status': 'parse_error',
                'error': str(e)
            }
    
    async def cleanup(self):
        """Cleanup all browser sessions"""
        for browser in self.browsers.values():
            await browser.close()
            
        if self.playwright:
            await self.playwright.stop()
            
        self.browsers.clear()
        self.contexts.clear()
        self.logger.info("All browser sessions cleaned up")

    def get_session_stats(self) -> Dict:
        """Get statistics for all sessions"""
        stats = {}
        for profile in self.profiles:
            stats[profile.profile_id] = {
                'usage_count': profile.usage_count,
                'success_rate': profile.success_rate,
                'last_used': profile.last_used,
                'viewport': profile.viewport,
                'timezone': profile.timezone
            }
        return stats
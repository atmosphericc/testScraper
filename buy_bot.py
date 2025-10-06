import asyncio
from playwright.async_api import async_playwright
from datetime import datetime
from pathlib import Path
import logging
from typing import Dict, Optional, Callable, List
import os
import random
import json
from urllib.parse import urljoin, urlparse
import sys
import platform

class BuyBot:
    def __init__(self, session_path: str, status_callback: Optional[Callable] = None):
        # Validate environment first
        self._validate_environment()

        self.session_path = Path(session_path)
        self.logger = logging.getLogger(__name__)
        self.purchase_log = logging.getLogger('purchases')
        self.target_password = self._get_target_password()
        self.status_callback = status_callback
        print("INIT DEBUG: About to load fingerprint...")
        self.fingerprint_data = self._load_consistent_fingerprint()
        print(f"INIT DEBUG: Fingerprint loaded, user_agent: {self.fingerprint_data.get('user_agent', 'MISSING')[:50]}...")

        # PERSISTENT LOGIN: Ensure session file exists for cross-TCIN login persistence
        if not self.session_path.exists():
            self.logger.warning(f"Session file {session_path} not found - login will be required")
        else:
            self.logger.info(f"✅ Session file loaded: {session_path} - login should persist across TCINs")

        # 🚀 COMPETITION MODE: Enable ultra-fast competitive settings
        self._competitive_mode = True
        self.logger.info("🏁 COMPETITION MODE ACTIVATED - Maximum speed optimizations enabled")

        # Get screen dimensions from saved fingerprint or detect actual dimensions
        self.screen_width, self.screen_height = self._get_screen_dimensions()

        # 🚀 COMPETITION-GRADE SELECTORS - Ultra-fast performance optimization
        self.SELECTORS = {
            'add_to_cart': [
                # PREORDER PRIORITY - Many products are preorder now
                'button:has-text("Preorder")',
                'button:has-text("Pre-order")',
                'button:has-text("Pre order")',
                'button:has-text("Pre-Order")',
                'button[data-test*="preorder"]',

                # 2025 TARGET.COM PRIORITY SELECTORS - Most current patterns
                'button[data-test="addToCartButton"]',
                'button[data-test="chooseOptionsButton"]',
                'button[data-testid="addToCartButton"]',
                'button[data-test*="addToCart"]',
                'button[data-test*="add-to-cart"]',

                # Current Target button patterns (high priority)
                '[data-testid*="add-to-cart"]',
                '[data-testid*="addToCart"]',
                'button[data-testid="add-to-cart-button"]',
                'button[data-testid="pdp-add-to-cart"]',
                'button[data-testid="add-to-cart-cta"]',

                # Text-based selectors with exact Target.com text
                'button:has-text("Add to cart")',
                'button:has-text("Add to Cart")',
                'button:has-text("Add to bag")',
                'button:has-text("Add to Bag")',

                # Color-specific button targeting (red buttons are often the main CTA)
                'button[style*="background-color: rgb(204, 0, 0)"]',
                'button[class*="red"]',
                'button[class*="Red"]',

                # 2025 class patterns
                'button[class*="addToCart"]',
                'button[class*="add-to-cart"]',
                'button[class*="AddToCart"]',
                'button[class*="add-cart"]',
                '.add-to-cart-button',
                '.addToCartButton',

                # Enhanced ID patterns
                'button[id*="addToCart"]',
                'button[id*="add-to-cart"]',
                'button[id*="add-cart"]',
                'button[id*="preorder"]',

                # Accessibility selectors (Target uses these)
                'button[aria-label*="Add to cart"]',
                'button[aria-label*="Add to Cart"]',
                'button[aria-label*="Add to bag"]',
                'button[aria-label*="Preorder"]',
                '[role="button"][aria-label*="Add"]',

                # Generic button fallbacks
                'button:has-text("Add")',
                'text="Add to cart"',
                'text="Add to Cart"',
                'text="Preorder"',

                # NEW: Generic button fallbacks
                'button:text("Add")',  # Very generic fallback
                '[type="button"]:has-text("Add")'
            ],
            'checkout': [
                # MODERN CHECKOUT SELECTORS: Updated for current Target.com
                'button:has-text("Sign in to check out")',
                'button:has-text("Sign in to checkout")',  # No space variant
                'button:text("Sign in to check out")',

                # NEW: Data attribute patterns
                '[data-test*="checkoutButton"]',
                '[data-test*="checkout"]',
                '[data-test="checkout-button"]',
                '[data-testid*="checkout"]',
                '[data-testid="checkout-button"]',

                # ENHANCED: Text variations
                'button:has-text("Checkout")',
                'button:has-text("Check out")',
                'button:has-text("Continue to checkout")',
                'button:has-text("Proceed to checkout")',
                'button:has-text("Go to checkout")',

                # NEW: Class-based selectors
                'button[class*="checkout"]',
                '.checkout-button',
                '[class*="CheckoutButton"]',
                '[class*="checkout-btn"]',

                # ENHANCED: Semantic and role-based
                '[role="button"]:has-text("checkout")',
                '[role="button"]:has-text("Check out")',
                'button[type="submit"]:has-text("checkout")',

                # NEW: Generic fallbacks
                ':text("Sign in to check out")',
                '[role="button"]:text("Sign in to check out")',
                'text="Sign in to check out"',
                'text="Checkout"',

                # LEGACY: Maintained for compatibility
                'button[data-test="orderSummaryButton"]'
            ],
            'place_order': [
                # MODERN: Updated Target place order selectors
                'button[data-test="placeOrderButton"]',
                'button[data-test="place-order-button"]',
                'button[data-test*="place"]',
                '[data-testid*="place-order"]',
                '[data-testid="place-order-button"]',

                # ENHANCED: Text variations
                'button:has-text("Place order")',
                'button:has-text("Place Order")',  # Capitalized
                'button:has-text("Complete order")',
                'button:has-text("Complete Order")',
                'button:has-text("Submit order")',
                'button:has-text("Submit Order")',
                'button:has-text("Finish order")',

                # NEW: Class-based selectors
                'button[class*="place-order"]',
                'button[class*="placeOrder"]',
                '[class*="PlaceOrder"]',
                '.place-order-button',

                # ENHANCED: ID and semantic patterns
                'button[id*="place"]',
                'button[id*="order"]',
                'button[id*="placeOrder"]',
                '[role="button"]:has-text("Place")',
                'button[type="submit"]:has-text("order")',

                # NEW: Generic fallbacks
                'text="Place order"',
                'text="Place Order"',
                'text="Complete order"'
            ]
        }

    def _generate_fingerprint(self):
        """🎯 ADVANCED FINGERPRINT CHAOS - Ultimate F5 Evasion"""

        # 🔥 MASSIVE RESOLUTION POOL - 50+ realistic combinations
        screen_resolutions = [
            # Common desktop resolutions
            {'width': 1920, 'height': 1080}, {'width': 1366, 'height': 768}, {'width': 1440, 'height': 900},
            {'width': 1536, 'height': 864}, {'width': 1280, 'height': 720}, {'width': 1600, 'height': 900},
            {'width': 2560, 'height': 1440}, {'width': 1680, 'height': 1050}, {'width': 1280, 'height': 1024},
            {'width': 1024, 'height': 768}, {'width': 1152, 'height': 864}, {'width': 1280, 'height': 800},
            {'width': 1400, 'height': 1050}, {'width': 1440, 'height': 1024}, {'width': 1600, 'height': 1200},
            {'width': 1920, 'height': 1200}, {'width': 2048, 'height': 1152}, {'width': 2560, 'height': 1600},
            # Ultra-wide and 4K
            {'width': 3440, 'height': 1440}, {'width': 2560, 'height': 1080}, {'width': 3840, 'height': 2160},
            {'width': 3840, 'height': 1600}, {'width': 2880, 'height': 1800}, {'width': 3200, 'height': 1800},
            # MacBook specific
            {'width': 2880, 'height': 1920}, {'width': 2560, 'height': 1664}, {'width': 3024, 'height': 1964},
            # Surface/laptop ratios
            {'width': 2736, 'height': 1824}, {'width': 2256, 'height': 1504}, {'width': 1920, 'height': 1280}
        ]

        # 🌍 GLOBAL TIMEZONE CHAOS - Worldwide coverage
        timezones = [
            # US timezones
            'America/New_York', 'America/Chicago', 'America/Los_Angeles', 'America/Denver',
            'America/Phoenix', 'America/Detroit', 'America/Indianapolis', 'America/Anchorage',
            'America/Honolulu', 'America/Adak', 'America/Metlakatla', 'America/Yakutat',
            # Major world cities
            'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Europe/Rome', 'Europe/Madrid',
            'Asia/Tokyo', 'Asia/Seoul', 'Asia/Shanghai', 'Asia/Singapore', 'Asia/Mumbai',
            'Australia/Sydney', 'Australia/Melbourne', 'Pacific/Auckland'
        ]

        # 🗣️ LANGUAGE CHAOS - Realistic combinations
        languages = [
            'en-US,en;q=0.9', 'en-US,en;q=0.8', 'en-US,en', 'en-GB,en;q=0.9',
            'en-US,en;q=0.9,es;q=0.8', 'en-US,en;q=0.9,fr;q=0.8', 'en-US,en;q=0.9,de;q=0.8',
            'en-US,en;q=0.9,zh;q=0.8', 'en-US,en;q=0.9,ja;q=0.8', 'en-US,en;q=0.9,ko;q=0.8',
            'en-US,en;q=0.9,pt;q=0.8', 'en-US,en;q=0.9,ru;q=0.8', 'en-US,en;q=0.9,it;q=0.8'
        ]

        # 🤖 USER AGENT CHAOS - Massive variety with realistic versions
        chrome_versions = ['119.0.0.0', '120.0.0.0', '121.0.0.0', '118.0.0.0']
        webkit_versions = ['537.36', '537.35', '537.34']

        mac_versions = ['10_15_7', '10_14_6', '11_6_0', '12_0_0', '13_0_0']
        windows_versions = ['10.0; Win64; x64', '11.0; Win64; x64', '10.0; WOW64']

        user_agents = []
        # Generate dynamic user agents
        for chrome_ver in chrome_versions:
            for webkit_ver in webkit_versions:
                # Mac variants
                for mac_ver in mac_versions:
                    user_agents.append(f'Mozilla/5.0 (Macintosh; Intel Mac OS X {mac_ver}) AppleWebKit/{webkit_ver} (KHTML, like Gecko) Chrome/{chrome_ver} Safari/{webkit_ver}')
                # Windows variants
                for win_ver in windows_versions:
                    user_agents.append(f'Mozilla/5.0 (Windows NT {win_ver}) AppleWebKit/{webkit_ver} (KHTML, like Gecko) Chrome/{chrome_ver} Safari/{webkit_ver}')

        # 🎨 HARDWARE CHAOS
        platforms = ['MacIntel', 'Win32', 'Win64', 'Linux x86_64']
        color_depths = [24, 30, 32, 16]
        device_memory = [2, 4, 6, 8, 12, 16, 32]
        cpu_cores = [2, 4, 6, 8, 12, 16, 20, 24]

        # 🎯 GENERATE CHAOS FINGERPRINT
        fingerprint = {
            'resolution': random.choice(screen_resolutions),
            'timezone': random.choice(timezones),
            'language': random.choice(languages),
            'user_agent': random.choice(user_agents),
            'platform': random.choice(platforms),
            'screen_color_depth': random.choice(color_depths),
            'device_memory': random.choice(device_memory),
            'hardware_concurrency': random.choice(cpu_cores),
            'max_touch_points': random.choice([0, 1, 5, 10]),
            'pixel_ratio': random.choice([1, 1.25, 1.5, 2, 2.5, 3]),
            'session_chaos': random.randint(100000, 999999),
            'webgl_vendor': random.choice(['Google Inc.', 'NVIDIA Corporation', 'ATI Technologies Inc.', 'Intel Inc.']),
            'webgl_renderer': random.choice(['ANGLE', 'SwiftShader', 'Mesa DRI', 'Direct3D11']),
            'connection_type': random.choice(['ethernet', 'wifi', 'cellular', 'unknown'])
        }

        self.logger.info(f"🎯 Generated chaos fingerprint: {fingerprint['session_chaos']}")
        return fingerprint

    def _load_consistent_fingerprint(self):
        """Load consistent fingerprint from saved session for F5 bypass"""
        self.logger.info("FINGERPRINT DEBUG: Starting _load_consistent_fingerprint()")

        try:
            self.logger.info("FINGERPRINT DEBUG: Checking if target.json exists...")
            if os.path.exists('target.json'):
                self.logger.info("FINGERPRINT DEBUG: target.json found, opening file...")
                with open('target.json', 'r') as f:
                    session_data = json.load(f)
                    self.logger.info(f"FINGERPRINT DEBUG: Loaded session data with keys: {list(session_data.keys())}")
                    saved_fingerprint = session_data.get('fingerprint', {})
                    self.logger.info(f"FINGERPRINT DEBUG: Found fingerprint data: {bool(saved_fingerprint)}")

                if saved_fingerprint:
                    self.logger.info(f"FINGERPRINT DEBUG: Fingerprint keys: {list(saved_fingerprint.keys())}")
                    # Convert saved fingerprint to expected format
                    fingerprint = {
                        'user_agent': saved_fingerprint.get('userAgent', ''),
                        'language': f"{saved_fingerprint.get('language', 'en-US')},en;q=0.9",
                        'platform': saved_fingerprint.get('platform', 'Win32'),
                        'resolution': {
                            'width': saved_fingerprint.get('screenWidth', 1920),
                            'height': saved_fingerprint.get('screenHeight', 1080)
                        },
                        'timezone': saved_fingerprint.get('timezone', 'America/New_York'),
                        'screen_color_depth': saved_fingerprint.get('colorDepth', 24),
                        'device_memory': saved_fingerprint.get('deviceMemory', 8),
                        'hardware_concurrency': saved_fingerprint.get('hardwareConcurrency', 8),
                        'max_touch_points': 1 if saved_fingerprint.get('touchSupport', False) else 0,
                        'pixel_ratio': 1,
                        'webgl_vendor': saved_fingerprint.get('webglVendor', 'Google Inc.'),
                        'webgl_renderer': saved_fingerprint.get('webglRenderer', 'ANGLE'),
                        'connection_type': 'ethernet'
                    }

                    self.logger.info(f"SUCCESS: Loaded consistent fingerprint from session: {saved_fingerprint.get('userAgent', 'Unknown')[:50]}...")
                    self.logger.info(f"FINGERPRINT DEBUG: Returning consistent fingerprint with user_agent: {fingerprint['user_agent'][:50]}...")
                    return fingerprint
                else:
                    self.logger.warning("FINGERPRINT DEBUG: No fingerprint data in session file")
            else:
                self.logger.warning("FINGERPRINT DEBUG: target.json file not found")

        except Exception as e:
            self.logger.error(f"FINGERPRINT DEBUG: Exception occurred: {e}")
            import traceback
            self.logger.error(f"FINGERPRINT DEBUG: Traceback: {traceback.format_exc()}")

        # Fallback to generating random fingerprint
        self.logger.warning("FINGERPRINT DEBUG: Falling back to random fingerprint generation")
        return self._generate_fingerprint()

    def _get_screen_dimensions(self):
        """Get screen dimensions from saved fingerprint or detect actual dimensions"""

        # Priority 1: Use saved fingerprint dimensions for consistency
        if hasattr(self, 'fingerprint_data') and 'resolution' in self.fingerprint_data:
            width = self.fingerprint_data['resolution']['width']
            height = self.fingerprint_data['resolution']['height']
            self.logger.info(f"✅ Using saved fingerprint dimensions: {width}x{height}")
            return width, height

        # Method 1: Try system_profiler (macOS built-in)
        try:
            if platform.system() == "Darwin":  # macOS
                import subprocess
                result = subprocess.run(
                    ["system_profiler", "SPDisplaysDataType"],
                    capture_output=True, text=True, timeout=5
                )
                for line in result.stdout.split('\n'):
                    if 'Resolution:' in line:
                        import re
                        dimensions = re.findall(r'(\d+)\s*x\s*(\d+)', parts := line.split(':')[1].strip())
                        if dimensions:
                            width = int(dimensions[0][0])
                            height = int(dimensions[0][1])
                            # Account for macOS menu bar and dock
                            height = max(height - 80, 900)
                            self.logger.info(f"✅ Detected macOS screen via system_profiler: {width}x{height}")
                            return width, height
        except Exception as e:
            self.logger.warning(f"system_profiler failed: {e}")

        # Method 2: Try tkinter (Python built-in)
        try:
            import tkinter as tk
            root = tk.Tk()
            width = root.winfo_screenwidth()
            height = root.winfo_screenheight() - 80  # Account for menu bar
            root.destroy()
            self.logger.info(f"✅ Detected screen via tkinter: {width}x{height}")
            return width, height
        except Exception as e:
            self.logger.warning(f"tkinter detection failed: {e}")

        # Method 3: Use larger reasonable defaults for modern screens
        self.logger.warning("All screen detection failed - using large fallback")
        return 1920, 1000  # Larger fallback that works on most modern screens

    def _get_target_password(self) -> Optional[str]:
        """Get Target password for automated authentication"""
        # Static password for automation
        password = "Cars123!"
        if password:
            self.logger.info("Target password loaded for automated authentication")
            return password
        else:
            self.logger.warning("Password not available - automated authentication will be limited")
            return None

    def _validate_environment(self):
        """Validate required environment variables are set"""
        # No environment variables are required - password is hardcoded
        pass

        # Validate session file exists (only if session_path is already set)
        if hasattr(self, 'session_path') and not self.session_path.exists():
            raise FileNotFoundError(
                f"Target session file not found: {self.session_path}. "
                f"Please run save_login.py first to create a valid session."
            )

    def _update_status(self, status: str, details: dict = None):
        """Update status via callback to monitoring system"""
        if self.status_callback:
            self.status_callback(status, details or {})

    async def _setup_request_interception(self, page):
        """Stealth request interception - minimal blocking to avoid detection"""
        # Only block the most suspicious tracking (keep it minimal)
        blocked_domains = {
            'googletagmanager.com', 'google-analytics.com', 'doubleclick.net',
            'googlesyndication.com', 'hotjar.com', 'fullstory.com'
        }

        async def stealth_intercept(route, request):
            url = request.url
            domain = urlparse(url).netloc

            # Only block major trackers, allow everything else for realism
            if any(blocked in domain for blocked in blocked_domains):
                await route.abort()
            else:
                # Add realistic headers that real browsers send
                realistic_headers = {
                    **request.headers,
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Cache-Control': 'max-age=0',
                    'Connection': 'keep-alive',
                    'Sec-Fetch-Dest': 'document',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-Site': 'none',
                    'Sec-Fetch-User': '?1',
                    'Upgrade-Insecure-Requests': '1',
                }

                # Remove automation indicators from headers
                realistic_headers.pop('User-Agent-Mobile', None)
                realistic_headers.pop('Automation', None)

                await route.continue_(headers=realistic_headers)

        await page.route('**/*', stealth_intercept)

    async def ultra_delay(self, min_ms: int = 50, max_ms: int = 150):
        """🚀 COMPETITION-OPTIMIZED DELAYS - Speed vs stealth balance"""
        # COMPETITIVE MODE: Ultra-fast delays for speed while maintaining pattern destruction
        if hasattr(self, '_competitive_mode') and self._competitive_mode:
            # 70% faster delays for competitive edge
            min_ms = max(10, min_ms // 3)
            max_ms = max(20, max_ms // 3)

        # CHAOS PATTERNS: Advanced randomization to defeat ML detection
        delay_patterns = [
            # Micro-burst pattern (40% chance) - fastest
            lambda: random.randint(min_ms//2, min_ms) if random.random() < 0.4 else random.randint(min_ms, max_ms),
            # Gaussian distribution (30% chance) - realistic
            lambda: max(min_ms//3, int(random.gauss((min_ms + max_ms)/2, (max_ms - min_ms)/8))),
            # Exponential decay (20% chance) - natural pauses
            lambda: min(max_ms*2, int(random.expovariate(2.0/(min_ms + max_ms)) + min_ms)),
            # Pure random (10% chance) - unpredictable
            lambda: random.randint(min_ms, max_ms)
        ]

        # Weighted pattern selection for maximum chaos
        pattern = random.choices(delay_patterns, weights=[40, 30, 20, 10])[0]
        delay_ms = max(5, min(pattern(), max_ms*2))  # Clamp with competitive bounds

        # ANTI-DETECTION: Occasional micro-pauses to break timing patterns
        if random.random() < 0.05:  # 5% chance for micro-pause
            delay_ms += random.randint(50, 200)

        delay = delay_ms / 1000.0
        await asyncio.sleep(delay)

    async def ultra_click(self, page, element):
        """⚡ CHAOS CLICKING - Destroy click pattern detection"""
        box = await element.bounding_box()
        if box:
            # RANDOMIZED CLICK COORDINATES - Avoid center-click detection
            click_strategies = [
                # Random within element
                lambda: (
                    box['x'] + random.uniform(box['width'] * 0.1, box['width'] * 0.9),
                    box['y'] + random.uniform(box['height'] * 0.1, box['height'] * 0.9)
                ),
                # Weighted towards corners (realistic human behavior)
                lambda: (
                    box['x'] + random.choice([box['width'] * 0.2, box['width'] * 0.8]),
                    box['y'] + random.choice([box['height'] * 0.3, box['height'] * 0.7])
                ),
                # Completely random but valid
                lambda: (
                    box['x'] + random.random() * box['width'],
                    box['y'] + random.random() * box['height']
                )
            ]

            click_strategy = random.choice(click_strategies)
            x, y = click_strategy()

            # CHAOTIC MOUSE MOVEMENT - Variable patterns
            movement_type = random.choice(['direct', 'curved', 'multi_step'])

            if movement_type == 'direct':
                # Direct movement (competitive speed)
                await page.mouse.move(x, y, steps=random.randint(1, 3))
            elif movement_type == 'curved':
                # Curved movement (realistic)
                await self._curved_mouse_movement(page, x, y)
            else:
                # Multi-step movement (human-like)
                await self._multi_step_movement(page, x, y)

            # RANDOMIZED PRE-CLICK DELAY
            await self.ultra_delay(10, 80)

        # EXECUTE CLICK with randomized method
        click_methods = [
            lambda: element.click(),
            lambda: page.mouse.click(x, y),
            lambda: element.click(force=True)
        ]

        click_method = random.choice(click_methods)
        await click_method()

        # POST-CLICK CHAOS DELAY
        await self.ultra_delay(30, 120)

    async def _curved_mouse_movement(self, page, target_x, target_y):
        """Generate curved mouse movement for realism"""
        current_pos = await page.evaluate('() => ({x: window.mouseX || 0, y: window.mouseY || 0})')
        start_x = current_pos.get('x', 0)
        start_y = current_pos.get('y', 0)

        # Create bezier curve
        steps = random.randint(5, 12)
        for i in range(steps):
            t = i / steps
            # Add curve with random control points
            curve_x = start_x + (target_x - start_x) * t + random.randint(-20, 20) * (1 - abs(2*t - 1))
            curve_y = start_y + (target_y - start_y) * t + random.randint(-15, 15) * (1 - abs(2*t - 1))
            await page.mouse.move(curve_x, curve_y)
            await self.ultra_delay(5, 25)

    async def _multi_step_movement(self, page, target_x, target_y):
        """Multi-step mouse movement with pauses"""
        steps = random.randint(2, 5)
        for i in range(steps):
            progress = (i + 1) / steps
            intermediate_x = target_x * progress + random.randint(-10, 10)
            intermediate_y = target_y * progress + random.randint(-10, 10)
            await page.mouse.move(intermediate_x, intermediate_y, steps=random.randint(1, 3))
            if i < steps - 1:  # Don't delay on final move
                await self.ultra_delay(15, 45)

    async def human_click(self, page, element):
        """Alias for ultra_click for backward compatibility"""
        await self.ultra_click(page, element)

    async def realistic_typing(self, page, element, text: str):
        """Realistic typing with burst patterns"""
        await element.click()
        await self.ultra_delay(50, 100)

        # Clear existing content
        await page.keyboard.down('Control')
        await page.keyboard.press('a')
        await page.keyboard.up('Control')
        await page.keyboard.press('Delete')

        # Type with realistic patterns (bursts and pauses)
        for i, char in enumerate(text):
            await page.keyboard.type(char)

            # Burst typing with occasional pauses
            if i % random.randint(3, 7) == 0:
                await self.ultra_delay(100, 200)  # Thinking pause
            else:
                await self.ultra_delay(30, 80)   # Normal typing speed

    async def _ultra_dynamic_passkey_dismissal(self, page, tcin):
        """Ultra-dynamic passkey dismissal that adapts to any situation"""
        self.logger.info("🚀 Starting ultra-dynamic passkey dismissal")

        try:
            # Skip debug screenshots for speed optimization

            # Strategy 1: Robust passkey detection using multiple approaches
            passkey_containers = []

            # Method 1: Text-based search using VALID Playwright selectors
            text_variations = ['passkey', 'pass key', 'use a passkey', 'face id', 'fingerprint', 'biometric']
            for text in text_variations:
                passkey_containers.extend([
                    f'text="{text}"',
                    f'[aria-label*="{text}" i]',
                    f'[title*="{text}" i]'
                ])

            # Method 2: Common Target/web patterns
            passkey_containers.extend([
                '[data-test*="passkey"]',
                '[data-testid*="passkey"]',
                '.passkey',
                '#passkey',
                '[class*="passkey"]',
                '[id*="passkey"]',
                'button[aria-expanded]',
                '[role="button"][aria-expanded]'
            ])

            # Method 3: Use JavaScript to find any element containing passkey text
            js_found_elements = await page.evaluate("""
                () => {
                    const elements = [];
                    const walker = document.createTreeWalker(
                        document.body,
                        NodeFilter.SHOW_TEXT,
                        null,
                        false
                    );

                    let node;
                    while (node = walker.nextNode()) {
                        const text = node.textContent.toLowerCase();
                        if (text.includes('passkey') || text.includes('use a passkey')) {
                            const element = node.parentElement;
                            if (element) {
                                const rect = element.getBoundingClientRect();
                                elements.push({
                                    tagName: element.tagName,
                                    className: element.className,
                                    id: element.id,
                                    text: element.textContent?.trim(),
                                    rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height}
                                });
                            }
                        }
                    }
                    return elements;
                }
            """)

            self.logger.info(f"🔍 JavaScript found {len(js_found_elements)} elements with passkey text:")
            for element in js_found_elements:
                self.logger.info(f"  - {element['tagName']}.{element['className']}: '{element['text'][:50]}'")

            # Try to find the actual passkey element
            passkey_containers = list(set(passkey_containers))  # Remove duplicates

            passkey_element = None
            for container_selector in passkey_containers:
                try:
                    element = await page.wait_for_selector(container_selector, timeout=1000)
                    if element and await element.is_visible():
                        passkey_element = element
                        self.logger.info(f"✓ Found passkey container: {container_selector}")
                        break
                except:
                    continue

            if not passkey_element:
                self.logger.warning("❌ No passkey container found")
                return False

            # Strategy 2: Dynamic element discovery - find ALL clickable elements near passkey
            nearby_clickables = await page.evaluate("""
                (passkey_selector) => {
                    const passkey = document.querySelector(passkey_selector);
                    if (!passkey) return [];

                    const clickables = [];
                    const parent = passkey.closest('div, section, form, [role="dialog"]');
                    if (parent) {
                        // Find all buttons, links, and clickable elements in the parent
                        const buttons = parent.querySelectorAll('button, [role="button"], a, [onclick], [aria-expanded]');
                        buttons.forEach((btn, index) => {
                            const rect = btn.getBoundingClientRect();
                            clickables.push({
                                index: index,
                                tagName: btn.tagName,
                                className: btn.className,
                                id: btn.id,
                                ariaLabel: btn.getAttribute('aria-label'),
                                ariaExpanded: btn.getAttribute('aria-expanded'),
                                textContent: btn.textContent?.trim().substring(0, 50),
                                rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
                            });
                        });
                    }
                    return clickables;
                }
            """, passkey_containers[0])

            self.logger.info(f"🔍 Found {len(nearby_clickables)} clickable elements near passkey")

            # Strategy 3: Intelligent clicking - try elements that look like close/dismiss buttons
            dismissal_success = False
            close_keywords = ['close', 'collapse', 'dismiss', 'hide', 'cancel', '×', 'x']

            for clickable in nearby_clickables:
                # Score elements based on likelihood they're close buttons
                score = 0
                element_desc = f"{clickable.get('ariaLabel', '')} {clickable.get('textContent', '')} {clickable.get('className', '')}".lower()

                for keyword in close_keywords:
                    if keyword in element_desc:
                        score += 10

                # Higher score for buttons with aria-expanded
                if clickable.get('ariaExpanded') == 'true':
                    score += 15

                # Higher score for buttons near the passkey
                if clickable['rect']['width'] < 50 and clickable['rect']['height'] < 50:  # Small buttons often close buttons
                    score += 5

                if score > 5:  # Only try elements with decent score
                    try:
                        # Click by coordinates
                        x = clickable['rect']['x'] + clickable['rect']['width'] / 2
                        y = clickable['rect']['y'] + clickable['rect']['height'] / 2
                        await page.mouse.click(x, y)
                        await self.ultra_delay(100, 200)

                        self.logger.info(f"🎯 Clicked element (score: {score}): {element_desc[:30]}")

                        # Check if passkey is still visible
                        passkey_still_visible = await page.evaluate("""
                            (selector) => {
                                const element = document.querySelector(selector);
                                return element && element.offsetParent !== null;
                            }
                        """, passkey_containers[0])

                        if not passkey_still_visible:
                            dismissal_success = True
                            self.logger.info("✅ Passkey dismissed successfully!")
                            break

                    except Exception as e:
                        self.logger.info(f"❌ Click failed: {e}")
                        continue

            # Strategy 4: Fallback methods if intelligent clicking failed
            if not dismissal_success:
                self.logger.info("🔄 Trying fallback dismissal methods")

                fallback_methods = [
                    # Click passkey itself to toggle
                    lambda: self.human_click(page, passkey_element),
                    # Press ESC
                    lambda: page.keyboard.press('Escape'),
                    # Tab away and back
                    lambda: (page.keyboard.press('Tab'), page.keyboard.press('Shift+Tab')),
                    # Click outside
                    lambda: page.click('body', position={'x': 100, 'y': 100}),
                    # Double click passkey
                    lambda: passkey_element.dblclick(),
                    # Right click to potentially show context menu then ESC
                    lambda: (passkey_element.click(button='right'), page.keyboard.press('Escape'))
                ]

                for i, method in enumerate(fallback_methods):
                    try:
                        await method()
                        await self.ultra_delay(100, 200)
                        self.logger.info(f"🔄 Tried fallback method {i+1}")

                        # Check again
                        passkey_still_visible = await page.evaluate("""
                            (selector) => {
                                const element = document.querySelector(selector);
                                return element && element.offsetParent !== null;
                            }
                        """, passkey_containers[0])

                        if not passkey_still_visible:
                            dismissal_success = True
                            self.logger.info(f"✅ Fallback method {i+1} succeeded!")
                            break

                    except Exception as e:
                        self.logger.info(f"❌ Fallback method {i+1} failed: {e}")
                        continue

            # Skip final screenshot for speed

            if dismissal_success:
                self.logger.info("🎉 Ultra-dynamic passkey dismissal SUCCESSFUL")
            else:
                self.logger.warning("⚠️ Passkey dismissal attempts completed but may not have succeeded")

            return dismissal_success

        except Exception as e:
            self.logger.error(f"💥 Ultra-dynamic passkey dismissal failed: {e}")
            return False

    async def _revolutionary_session_manager(self):
        """🔄 REVOLUTIONARY SESSION MANAGEMENT - F5 Bypass Evolution"""
        import subprocess
        import time
        import json

        session_files = ['target.json', 'target_backup1.json', 'target_backup2.json']
        session_success = None

        for session_file in session_files:
            try:
                if not os.path.exists(session_file):
                    self.logger.debug(f"📂 Session file {session_file} not found")
                    continue

                self.logger.info(f"🔍 Evaluating session: {session_file}")

                with open(session_file, 'r') as f:
                    session_data = json.load(f)

                # COMPETITIVE SESSION VALIDATION - Check freshness
                session_created = session_data.get('created_at', 0)
                session_last_used = session_data.get('last_used', 0)
                current_time = time.time()

                # If no timestamps exist, treat as fresh session (newly created)
                if session_created == 0 or session_last_used == 0:
                    self.logger.info(f"📝 Session {session_file} has no timestamps - treating as fresh")
                    session_age = 0
                    last_use_age = 0
                else:
                    session_age = current_time - session_created
                    last_use_age = current_time - session_last_used

                # Dynamic session expiry based on usage patterns
                base_max_age = 14400  # 4 hours
                usage_bonus = min(1800, abs(session_created - session_last_used) / 10)  # Bonus for recent use
                effective_max_age = base_max_age + usage_bonus

                if session_age > effective_max_age and session_created != 0:
                    self.logger.warning(f"⏰ Session {session_file} expired (age: {session_age/3600:.1f}h)")

                    # PROACTIVE REFRESH: If primary session is expired, trigger refresh immediately
                    if session_file == 'target.json':
                        self.logger.info("🔄 Primary session expired - triggering proactive refresh")
                        try:
                            result = subprocess.run(['pgrep', '-f', 'save_login.py'],
                                                  capture_output=True, text=True, timeout=2)
                            if result.returncode != 0:
                                subprocess.Popen(['python', 'save_login.py', '--refresh'],
                                               stdout=subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL)
                                self.logger.info("🚀 Proactive session refresh launched")
                        except:
                            pass
                    continue

                # F5 COMPATIBILITY CHECK - Verify BIGip cookies
                cookies = session_data.get('cookies', [])
                bigip_cookies = [c for c in cookies if 'BIG' in c.get('name', '')]
                critical_cookies = [c for c in cookies if c.get('name') in ['store-hc', 'visitorId', 'UserLocation']]

                session_score = 0
                session_score += len(bigip_cookies) * 25  # F5 cookies are critical
                session_score += len(critical_cookies) * 15  # Target-specific cookies
                session_score += min(50, len(cookies) * 2)  # General cookie health

                # Recency bonus
                if last_use_age < 1800:  # Used within 30 minutes
                    session_score += 20
                elif last_use_age < 7200:  # Used within 2 hours
                    session_score += 10

                self.logger.info(f"🎯 Session {session_file} score: {session_score}% (BIGip: {len(bigip_cookies)}, Critical: {len(critical_cookies)})")

                if session_score >= 60:  # Minimum viable session
                    # Update last used timestamp for session rotation
                    session_data['last_used'] = current_time
                    session_data['usage_count'] = session_data.get('usage_count', 0) + 1

                    with open(session_file, 'w') as f:
                        json.dump(session_data, f, indent=2)

                    self.logger.info(f"✅ Selected session: {session_file} (score: {session_score}%)")
                    session_success = session_file
                    break
                else:
                    self.logger.warning(f"❌ Session {session_file} insufficient (score: {session_score}%)")
                    continue

            except Exception as e:
                self.logger.error(f"💥 Session evaluation failed for {session_file}: {e}")
                continue

        # EMERGENCY SESSION SYSTEM
        if not session_success:
            self.logger.warning("🚨 ALL SESSIONS FAILED - Activating emergency protocols")

            # Check if emergency session refresh is already running
            try:
                result = subprocess.run(['pgrep', '-f', 'save_login.py'],
                                      capture_output=True, text=True, timeout=2)
                if result.returncode == 0:
                    self.logger.info("🔄 Emergency session refresh already running")
                else:
                    # Launch emergency session refresh
                    subprocess.Popen(['python', 'save_login.py', '--emergency'],
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                    self.logger.info("🚀 Emergency session refresh launched")
            except:
                pass

            # Use most recent session as fallback
            fallback_files = [f for f in session_files if os.path.exists(f)]
            if fallback_files:
                # Sort by modification time
                fallback_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                session_success = fallback_files[0]
                self.logger.info(f"🆘 Using fallback session: {session_success}")
            else:
                # Create minimal session file
                minimal_session = {
                    'cookies': [],
                    'origins': [],
                    'created_at': time.time(),
                    'last_used': time.time(),
                    'emergency': True
                }
                session_success = 'target.json'
                with open(session_success, 'w') as f:
                    json.dump(minimal_session, f, indent=2)
                self.logger.warning("🔧 Created emergency minimal session")

        # COMPETITIVE SESSION ROTATION - Prevent F5 pattern detection
        if random.random() < 0.15:  # 15% chance to rotate session
            available_sessions = [f for f in session_files if os.path.exists(f) and f != session_success]
            if available_sessions:
                rotation_target = random.choice(available_sessions)
                self.logger.info(f"🎲 Session rotation triggered: {session_success} → {rotation_target}")
                session_success = rotation_target

        return session_success

    async def ultra_fast_login_check(self, page):
        """Improved login status check with multiple robust indicators"""
        try:
            # Check for multiple login indicators with longer timeout
            login_indicators = [
                '[data-test="@web/AccountLink"]',
                '[data-test="accountNav"]',
                'button[aria-label*="Account"]',
                'button[aria-label*="Hi,"]',
                'button:has-text("Hi,")',
                '[data-test="@web/ProfileIcon"]',
                'button[data-test="accountNav-signOut"]',
                '.account-menu',
                '[aria-label*="Account menu"]'
            ]

            for indicator in login_indicators:
                try:
                    await page.wait_for_selector(indicator, timeout=500)
                    self.logger.info(f"✓ Login confirmed via: {indicator}")
                    return True
                except:
                    continue

            # Check for sign-in prompts (indicates not logged in)
            signin_indicators = [
                'text="Sign in"',
                'text="Sign In"',
                'button:has-text("Sign in")',
                'button:has-text("Sign In")',
                '[data-test*="signin"]',
                '[data-test*="login"]',
                'h1:has-text("Sign in")',
                'input[type="email"]',
                'input[name="username"]'
            ]

            for indicator in signin_indicators:
                try:
                    await page.wait_for_selector(indicator, timeout=300)
                    self.logger.warning(f"⚠ Sign-in prompt detected: {indicator}")
                    return False
                except:
                    continue

            # Check URL for login page
            current_url = page.url
            if '/login' in current_url or '/signin' in current_url or '/account/signin' in current_url:
                self.logger.warning(f"⚠ On login page: {current_url}")
                return False

            # Default to logged in if no clear indicators
            self.logger.info("? No clear login indicators found - assuming logged in")
            return True
        except:
            return False

    async def _handle_post_password_popups(self, page, tcin):
        """Handle any popups that appear AFTER password entry - this is where bots get stuck!"""
        self.logger.info("🚨 CRITICAL: Checking for post-password popups (common stuck point)")

        try:
            # Wait a moment for any popups to appear
            await self.ultra_delay(200, 400)

            # Skip debug screenshot for speed

            # Strategy 1: Look for common popup indicators using VALID CSS selectors
            popup_indicators = [
                # Modal/dialog containers
                '[role="dialog"]',
                '.modal',
                '.popup',
                '.overlay',
                '[aria-modal="true"]',

                # Generic blocking elements
                '.blocking-overlay',
                '[style*="z-index: 999"]',
                '[style*="position: fixed"]'
            ]

            popup_found = False
            popup_type = None

            for indicator in popup_indicators:
                try:
                    popup_element = await page.wait_for_selector(indicator, timeout=1000)
                    if popup_element and await popup_element.is_visible():
                        popup_found = True
                        popup_type = indicator
                        self.logger.info(f"🎯 FOUND POST-PASSWORD POPUP: {indicator}")
                        break
                except:
                    continue

            # Strategy 2: JavaScript-based text detection for passkey popups
            if not popup_found:
                self.logger.info("🔍 No structural popup found, checking for passkey text...")
                try:
                    passkey_detected = await page.evaluate("""
                        () => {
                            const bodyText = document.body.innerText.toLowerCase();
                            const passkeyKeywords = ['passkey', 'touch id', 'face id', 'fingerprint', 'biometric', 'security key'];
                            return passkeyKeywords.some(keyword => bodyText.includes(keyword));
                        }
                    """)

                    if passkey_detected:
                        popup_found = True
                        popup_type = "passkey-text-based"
                        self.logger.info("🎯 FOUND PASSKEY POPUP via text detection!")
                except Exception as e:
                    self.logger.info(f"Text-based passkey detection failed: {e}")

            if popup_found:
                self.logger.info(f"🚨 Detected blocking popup after password: {popup_type}")

                # Strategy 3: Ultra-comprehensive close button detection
                success = await self._ultra_close_popup(page, popup_type, tcin)

                if success:
                    self.logger.info("✅ Successfully closed post-password popup!")
                else:
                    self.logger.error("❌ Failed to close post-password popup - this is why bot gets stuck!")

            else:
                self.logger.info("✅ No blocking popups detected after password entry")

        except Exception as e:
            self.logger.error(f"💥 Post-password popup handling failed: {e}")

    async def _ultra_close_popup(self, page, popup_type, tcin):
        """Ultra-comprehensive popup closing that tries every possible method"""
        self.logger.info("🎯 Starting ultra-comprehensive popup closing")

        try:
            # Method 1: Find and click close buttons - AGGRESSIVE PASSKEY TARGETING
            close_selectors = [
                # Target the specific close button seen in screenshots (top-right X)
                'button[aria-label="close"]',
                'button[title="close"]',
                '[data-testid="close-button"]',
                '[aria-label*="close"]',
                'button[class*="close"]',

                # Standard modal close patterns
                '.modal-close',
                '.close-button',
                '.close',

                # Generic patterns
                '[role="button"][aria-label*="close"]',
                'button[aria-label*="dismiss"]'
            ]

            # Try standard CSS selectors first
            for selector in close_selectors:
                try:
                    close_btn = await page.wait_for_selector(selector, timeout=1000)
                    if close_btn and await close_btn.is_visible():
                        await self.human_click(page, close_btn)
                        self.logger.info(f"✅ Clicked close button: {selector}")
                        await self.ultra_delay(150, 300)

                        # Verify popup is gone using generic detection
                        popup_still_there = await page.evaluate("""
                            () => {
                                // Check for modal dialogs
                                const modals = document.querySelectorAll('[role="dialog"], .modal, .popup, [aria-modal="true"]');
                                for (let modal of modals) {
                                    if (modal.offsetParent !== null) return true;
                                }

                                // Check for passkey text
                                const bodyText = document.body.innerText.toLowerCase();
                                const passkeyKeywords = ['passkey', 'touch id', 'face id'];
                                return passkeyKeywords.some(keyword => bodyText.includes(keyword));
                            }
                        """)

                        if not popup_still_there:
                            self.logger.info("✅ Popup verification: popup appears to be closed!")
                            return True
                        else:
                            self.logger.info("⚠️ Popup verification: popup still detected")

                except Exception as e:
                    self.logger.info(f"Close selector failed: {selector} - {e}")
                    continue

            # Method 2: Try Playwright text-based selectors (valid syntax)
            text_selectors = [
                # X symbols and close text
                'text="×"',
                'text="✕"',
                'text="Close"',
                'text="Cancel"',
                'text="Skip"',
                'text="Not now"',
                'text="Maybe later"',

                # Passkey-specific escape routes
                'text="Enter your password"',
                'text="Use password"',
                'text="Continue with password"'
            ]

            for selector in text_selectors:
                try:
                    close_btn = await page.wait_for_selector(selector, timeout=1000)
                    if close_btn and await close_btn.is_visible():
                        await self.human_click(page, close_btn)
                        self.logger.info(f"✅ Clicked text-based button: {selector}")
                        await self.ultra_delay(150, 300)

                        # Verify popup is gone using generic detection
                        popup_still_there = await page.evaluate("""
                            () => {
                                // Check for modal dialogs
                                const modals = document.querySelectorAll('[role="dialog"], .modal, .popup, [aria-modal="true"]');
                                for (let modal of modals) {
                                    if (modal.offsetParent !== null) return true;
                                }

                                // Check for passkey text
                                const bodyText = document.body.innerText.toLowerCase();
                                const passkeyKeywords = ['passkey', 'touch id', 'face id'];
                                return passkeyKeywords.some(keyword => bodyText.includes(keyword));
                            }
                        """)

                        if not popup_still_there:
                            self.logger.info("✅ Popup verification: text-based popup appears to be closed!")
                            return True
                        else:
                            self.logger.info("⚠️ Popup verification: popup still detected after text click")

                except Exception as e:
                    self.logger.info(f"Text selector failed: {selector} - {e}")
                    continue

            # Method 3: Click outside popup area
            try:
                await page.click('body', position={'x': 50, 'y': 50})
                await page.wait_for_timeout(1000)
                self.logger.info("Tried clicking outside popup")
            except:
                pass

            # Method 4: Escape key
            try:
                await page.keyboard.press('Escape')
                await page.wait_for_timeout(1000)
                self.logger.info("Tried ESC key")
            except:
                pass

            # Method 5: Find ANY clickable element and try them all
            all_clickables = await page.evaluate("""
                () => {
                    const clickables = document.querySelectorAll('button, [role="button"], a, [onclick]');
                    const result = [];
                    clickables.forEach((el, index) => {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            result.push({
                                index: index,
                                text: el.textContent?.trim().substring(0, 30),
                                className: el.className,
                                rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height}
                            });
                        }
                    });
                    return result;
                }
            """)

            self.logger.info(f"Found {len(all_clickables)} clickable elements, trying suspicious ones...")

            # Try elements that might be close buttons
            for clickable in all_clickables:
                text = clickable.get('text', '').lower()
                className = clickable.get('className', '').lower()

                if any(keyword in text or keyword in className for keyword in
                       ['close', 'skip', 'cancel', 'not now', 'maybe later', '×', 'dismiss']):
                    try:
                        x = clickable['rect']['x'] + clickable['rect']['width'] / 2
                        y = clickable['rect']['y'] + clickable['rect']['height'] / 2
                        await page.mouse.click(x, y)
                        await self.ultra_delay(100, 200)
                        self.logger.info(f"Tried clicking: {text}")

                        # Check if popup closed using generic detection
                        popup_still_there = await page.evaluate("""
                            () => {
                                // Check for modal dialogs
                                const modals = document.querySelectorAll('[role="dialog"], .modal, .popup, [aria-modal="true"]');
                                for (let modal of modals) {
                                    if (modal.offsetParent !== null) return true;
                                }

                                // Check for passkey text
                                const bodyText = document.body.innerText.toLowerCase();
                                const passkeyKeywords = ['passkey', 'touch id', 'face id'];
                                return passkeyKeywords.some(keyword => bodyText.includes(keyword));
                            }
                        """)

                        if not popup_still_there:
                            self.logger.info(f"✅ Successfully closed popup by clicking: {text}")
                            return True

                    except:
                        continue

            return False

        except Exception as e:
            self.logger.error(f"Ultra close popup failed: {e}")
            return False

    async def parallel_element_finder(self, page, selectors: List[str], timeout: int = 2000):
        """🚀 COMPETITION-GRADE PARALLEL FINDER - Ultra-fast element detection"""
        # SPEED OPTIMIZATION: Quick DOM check before expensive waits
        async def instant_check(selector):
            try:
                # 2s instant check - if element exists, return immediately
                element = await page.wait_for_selector(selector, timeout=2000)
                if element and await element.is_visible():
                    return element, selector, 0  # Priority 0 = instant
                return None
            except:
                return None

        async def fast_check(selector):
            try:
                # 3s fast check for recently loaded elements
                element = await page.wait_for_selector(selector, timeout=3000)
                if element and await element.is_visible():
                    return element, selector, 1  # Priority 1 = fast
                return None
            except:
                return None

        async def thorough_check(selector):
            try:
                # Full timeout for thorough check
                element = await page.wait_for_selector(selector, timeout=timeout)
                if element and await element.is_visible():
                    return element, selector, 2  # Priority 2 = thorough
                return None
            except:
                return None

        # COMPETITIVE STRATEGY: Staggered parallel execution
        # Phase 1: Instant checks (0.1s each) - covers 90% of cases
        instant_tasks = [asyncio.create_task(instant_check(sel)) for sel in selectors[:8]]

        try:
            # Try instant checks first
            for coro in asyncio.as_completed(instant_tasks, timeout=0.2):
                result = await coro
                if result:
                    # Cancel all remaining tasks
                    for task in instant_tasks:
                        if not task.done():
                            task.cancel()
                    return result[0], result[1]  # Return element, selector

            # Phase 2: Fast checks if instant failed
            fast_tasks = [asyncio.create_task(fast_check(sel)) for sel in selectors]

            for coro in asyncio.as_completed(fast_tasks, timeout=0.6):
                result = await coro
                if result:
                    # Cancel all remaining tasks
                    for task in fast_tasks:
                        if not task.done():
                            task.cancel()
                    return result[0], result[1]

            # Phase 3: Thorough check as final fallback
            thorough_tasks = [asyncio.create_task(thorough_check(sel)) for sel in selectors]

            for coro in asyncio.as_completed(thorough_tasks):
                result = await coro
                if result:
                    # Cancel all remaining tasks
                    for task in thorough_tasks:
                        if not task.done():
                            task.cancel()
                    return result[0], result[1]

            return None, None

        except Exception as e:
            # Emergency cleanup
            all_tasks = instant_tasks + (locals().get('fast_tasks', []) + locals().get('thorough_tasks', []))
            for task in all_tasks:
                if not task.done():
                    task.cancel()
            return None, None


    async def attempt_purchase(self, product: Dict) -> Dict:
        """
        Attempt to purchase a single product
        Returns: Dict with success status and details
        """
        tcin = product['tcin']

        self.purchase_log.info(f"Starting purchase: {tcin}")
        self._update_status('ATTEMPTING', {'tcin': tcin})

        browser = None
        try:
            async with async_playwright() as p:
                # Use actual detected screen dimensions
                screen_width = self.screen_width
                screen_height = self.screen_height

                # 🏆 ULTIMATE COMPETITIVE BROWSER - Complete Isolation Per Run
                import tempfile
                import uuid

                # Generate completely unique browser session
                session_id = str(uuid.uuid4())[:8]
                temp_dir = tempfile.mkdtemp(prefix=f'target_bot_{session_id}_')

                self.logger.info(f"🔥 COMPETITIVE MODE: Launching isolated browser session {session_id}")

                # STEALTH BROWSER - Keep F5 evasion but fix window sizing
                stealth_args = [
                    # CRITICAL F5 EVASION (keep these)
                    '--disable-blink-features=AutomationControlled',
                    '--exclude-switches=enable-automation',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-infobars',
                    '--no-first-run',
                    '--disable-default-apps',

                    # REMOVE ALL WINDOW/SIZING ARGS - these break macOS window sizing
                    # NO --kiosk, --start-fullscreen, --window-size, etc.
                ]

                # LAUNCH STEALTH BROWSER with minimal args
                browser = await p.chromium.launch(
                    headless=False,
                    args=stealth_args
                )

                # Store temp directory for cleanup
                self.temp_browser_dir = temp_dir

                self.logger.info(f"Launching browser with screen size: {screen_width}x{screen_height}")

                # 🔄 REVOLUTIONARY SESSION MANAGEMENT - F5 Bypass Evolution
                session_file = await self._revolutionary_session_manager()

                # 🧪 FORCE FRESH SESSION: Ensure we use the fresh login we just saved
                if not session_file or not os.path.exists(str(session_file)):
                    session_file = self.session_path
                    self.logger.info(f"🔧 Using fresh session file: {session_file}")
                else:
                    self.logger.info(f"✅ Using evaluated session file: {session_file}")

                # ULTIMATE STEALTH context - Let browser use natural viewport
                context = await browser.new_context(
                    storage_state=session_file,
                    # NO CUSTOM VIEWPORT - let browser use natural size
                    user_agent=self.fingerprint_data['user_agent'],
                    timezone_id=self.fingerprint_data.get('timezone', 'America/New_York'),
                    locale='en-US',
                    # Realistic browser behavior
                    ignore_https_errors=False,  # Real browsers don't ignore HTTPS errors
                    java_script_enabled=True,
                    bypass_csp=False,  # Real browsers respect CSP
                    # Perfect real browser headers
                    extra_http_headers={
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
                        'Accept-Language': self.fingerprint_data['language'],
                        'Accept-Encoding': 'gzip, deflate, br',
                        'Connection': 'keep-alive',
                        'Upgrade-Insecure-Requests': '1',
                        'Sec-Fetch-Dest': 'document',
                        'Sec-Fetch-Mode': 'navigate',
                        'Sec-Fetch-Site': 'none',
                        'Sec-Fetch-User': '?1',
                        'DNT': '1'  # Do Not Track like privacy-conscious users
                    },
                    # Realistic permissions
                    permissions=['geolocation'],
                    geolocation={'latitude': 40.7128, 'longitude': -74.0060},  # NYC
                    color_scheme='light',
                    reduced_motion='no-preference'
                )

                page = await context.new_page()
                page.set_default_timeout(15000)  # Increased timeout for preorder products

                # 🎯 NO VIEWPORT MANIPULATION - Let browser handle naturally
                self.logger.info("Using natural browser viewport - no forced sizing")

                # Setup request interception immediately
                await self._setup_request_interception(page)

                # ULTIMATE STEALTH MODE - Complete automation masking
                await page.add_init_script("""
                    // CRITICAL: Remove ALL webdriver traces
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined,
                        configurable: true
                    });

                    // Remove ALL automation indicators
                    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
                    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
                    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
                    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Object;
                    delete window.cdc_adoQpoasnfa76pfcZLmcfl_JSON;

                    // Override automation detection methods
                    const originalQuery = window.navigator.permissions.query;
                    window.navigator.permissions.query = (parameters) => (
                        parameters.name === 'notifications' ?
                            Promise.resolve({ state: Notification.permission }) :
                            originalQuery(parameters)
                    );

                    // Add realistic plugins
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => ({
                            length: 3,
                            0: { name: 'Chrome PDF Plugin', description: 'Portable Document Format' },
                            1: { name: 'Chrome PDF Viewer', description: 'PDF Viewer' },
                            2: { name: 'Native Client', description: 'Native Client' }
                        }),
                        configurable: true
                    });

                    // Fix languages
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['en-US', 'en'],
                        configurable: true
                    });

                    // Add realistic chrome object
                    window.chrome = {
                        runtime: {
                            onConnect: null,
                            onMessage: null
                        },
                        app: {
                            isInstalled: false
                        },
                        webstore: {
                            onInstallStageChanged: null,
                            onDownloadProgress: null
                        }
                    };

                    // Override console methods to hide automation
                    const originalLog = console.log;
                    console.log = function(...args) {
                        if (!args[0]?.includes('DevTools')) {
                            originalLog.apply(console, args);
                        }
                    };

                    // Add realistic timing
                    const originalPerformanceNow = performance.now;
                    let performanceOffset = Math.random() * 100;
                    performance.now = function() {
                        return originalPerformanceNow.call(performance) + performanceOffset;
                    };

                    // Hide automation traces in error stack
                    const originalError = Error;
                    window.Error = function(...args) {
                        const error = new originalError(...args);
                        error.stack = error.stack?.replace(/\\s+at.*playwright.*$/gim, '');
                        return error;
                    };
                    window.Error.prototype = originalError.prototype;
                """)

                # MINIMAL STEALTH WARMUP - Just enough to avoid detection
                self.logger.info("🎯 DIRECT PRODUCT ACCESS: Minimal stealth warmup")

                # 🔄 RANDOMIZED NAVIGATION PATTERNS - Destroy F5 pattern detection
                nav_patterns = [
                    # Speed pattern (competitive advantage)
                    {
                        'name': 'speed',
                        'wait_until': 'domcontentloaded',
                        'timeout': 5000,
                        'delay': (50, 150)
                    },
                    # Realistic pattern (human-like)
                    {
                        'name': 'realistic',
                        'wait_until': 'networkidle',
                        'timeout': 8000,
                        'delay': (200, 500)
                    },
                    # Varied pattern (unpredictable)
                    {
                        'name': 'varied',
                        'wait_until': 'load',
                        'timeout': 7000,
                        'delay': (100, 300)
                    }
                ]

                # Choose random navigation pattern
                pattern = random.choice(nav_patterns)
                self.logger.info(f"🎯 CHAOS NAVIGATION: Using {pattern['name']} pattern")

                await page.goto("https://www.target.com",
                               wait_until=pattern['wait_until'],
                               timeout=pattern['timeout'])
                await self.ultra_delay(*pattern['delay'])

                # ⚡ COMPETITION MODE: Skip auth verification, handle during checkout
                self.logger.info("🏁 SPEED MODE: Skipping auth verification - will handle on-the-fly")

                await self.ultra_delay(100, 200)  # SPEED: Minimal pause

                # Step 2: ULTRA-MINIMAL interaction for competition mode
                try:
                    # Minimal human-like behavior
                    await page.evaluate("window.scrollTo(0, 200)")
                    await self.ultra_delay(100, 200)  # SPEED: Minimal scroll pause
                    await page.mouse.move(400, 300, steps=5)
                    await self.ultra_delay(50, 100)   # SPEED: Minimal mouse pause
                except Exception as e:
                    self.logger.info(f"Minimal interaction failed: {e}")

                # Step 3: ULTRA-FAST direct navigation - COMPETITION SPEED
                self.logger.info("🏁 COMPETITION MODE: Direct product access for maximum speed")

                url = f"https://www.target.com/p/-/A-{tcin}"

                # FASTEST possible navigation
                await page.goto(url, wait_until='domcontentloaded', timeout=6000)
                await self.ultra_delay(100, 300)  # Minimal load time for competition

                # Validate we're on the correct product page
                try:
                    current_url = page.url
                    if tcin not in current_url:
                        self.logger.error(f"Wrong product page! Expected TCIN {tcin}, got URL: {current_url}")
                        raise Exception(f"Navigation failed - wrong product page")
                    else:
                        self.logger.info(f"✅ Successfully navigated to product {tcin}")
                except Exception as e:
                    self.logger.error(f"Product validation failed: {e}")
                    raise

                # CRITICAL: Validate session is still active before attempting purchase
                self.logger.info("🔐 Validating session before purchase attempt...")
                login_status = await self.ultra_fast_login_check(page)
                if not login_status:
                    self.logger.error("❌ Session expired on product page!")
                    session_screenshot = f"logs/session_check_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                    await page.screenshot(path=session_screenshot, full_page=True)
                    return {
                        'success': False,
                        'tcin': tcin,
                        'reason': 'session_expired',
                        'message': 'Session expired before purchase attempt',
                        'screenshot': session_screenshot
                    }
                else:
                    self.logger.info("✅ Session validated - proceeding with purchase")

                # Re-apply optimizations after page load
                await page.evaluate("""
                    document.body.style.zoom = '0.75';
                    // Speed up any remaining animations
                    const style = document.createElement('style');
                    style.textContent = `* { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }`;
                    document.head.appendChild(style);
                """)

                # Simple window status check
                window_info = await page.evaluate("""
                    () => ({
                        innerWidth: window.innerWidth,
                        innerHeight: window.innerHeight,
                        zoomLevel: document.body.style.zoom || 'not set'
                    })
                """)

                self.logger.info(f"Browser window - Size: {window_info['innerWidth']}x{window_info['innerHeight']}, Zoom: {window_info['zoomLevel']}")

                # Skip debug screenshots for speed

                # Check if product page loaded
                if "product not found" in page.url.lower():
                    self.purchase_log.error(f"Product {tcin} not found")
                    return {
                        'success': False,
                        'tcin': tcin,
                        'reason': 'product_not_found'
                    }

                # Enhanced shipping selection - try multiple strategies
                shipping_selected = False

                # Strategy 1: Look for explicit shipping buttons
                shipping_button_selectors = [
                    'button[data-test="fulfillment-cell-shipping"]',
                    'button:has-text("Shipping")',
                    '[data-test*="shipping"] button',
                    '.shipping-option button',
                    'button[data-test="shippingButton"]',
                    'button[aria-label*="shipping"]',
                    'button[aria-label*="Shipping"]'
                ]

                self.logger.info("Attempting shipping selection - Strategy 1: Direct button selectors")
                # Ultra-fast parallel shipping selection
                element, selector = await self.parallel_element_finder(page, shipping_button_selectors, 500)
                if element:
                    await self.ultra_click(page, element)
                    shipping_selected = True
                    pass  # Shipping selected

                # Fast fallback shipping selection
                if not shipping_selected:
                    shipping_text_selectors = ['text="Shipping"', 'text="Ship"', '[aria-label*="Shipping"]']
                    element, selector = await self.parallel_element_finder(page, shipping_text_selectors, 300)
                    if element:
                        await self.ultra_click(page, element)
                        shipping_selected = True
                        self.logger.info(f"✓ Fast shipping text selected: {selector}")

                # Final fast shipping input check
                if not shipping_selected:
                    shipping_inputs = ['input[type="radio"][value*="shipping"]', 'input[name*="shipping"]']
                    element, selector = await self.parallel_element_finder(page, shipping_inputs, 300)
                    if element:
                        await self.ultra_click(page, element)
                        shipping_selected = True
                        self.logger.info(f"✓ Fast shipping input selected: {selector}")

                if not shipping_selected:
                    self.logger.info("No shipping option found - continuing with default")

                # Skip lengthy shipping verification - proceed quickly
                if shipping_selected:
                    await self.ultra_delay(100, 200)  # Brief UI update delay

                # Handle potential authentication prompts
                try:
                    # Check for password prompt
                    password_selectors = [
                        'input[type="password"]',
                        'input[name="password"]',
                        '#password',
                        '[data-test="password-input"]'
                    ]

                    for pwd_selector in password_selectors:
                        try:
                            pwd_field = await page.wait_for_selector(pwd_selector, timeout=1000)
                            if pwd_field and await pwd_field.is_visible():
                                self.logger.warning("Password prompt detected - session may have expired")

                                # Take screenshot for manual intervention
                                auth_screenshot = f"logs/auth_prompt_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                                await page.screenshot(path=auth_screenshot)

                                pass  # Browser will be closed in finally block
                                return {
                                    'success': False,
                                    'tcin': tcin,
                                    'reason': 'authentication_required',
                                    'message': 'Password prompt detected - session expired',
                                    'screenshot': auth_screenshot
                                }
                        except:
                            continue

                    # Check for 2FA or verification prompts
                    verification_selectors = [
                        'input[name="verificationCode"]',
                        'input[type="tel"]',
                        '[data-test="verification-code"]',
                        'text="Enter verification code"'
                    ]

                    for verify_selector in verification_selectors:
                        try:
                            verify_field = await page.wait_for_selector(verify_selector, timeout=1000)
                            if verify_field and await verify_field.is_visible():
                                self.logger.warning("2FA/Verification prompt detected")

                                auth_screenshot = f"logs/2fa_prompt_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                                await page.screenshot(path=auth_screenshot)

                                pass  # Browser will be closed in finally block
                                return {
                                    'success': False,
                                    'tcin': tcin,
                                    'reason': 'verification_required',
                                    'message': '2FA/Verification code required',
                                    'screenshot': auth_screenshot
                                }
                        except:
                            continue

                except Exception as e:
                    self.logger.info(f"Authentication check failed: {e}")

                # ⚡ COMPETITION MODE: Skip browsing simulation, go direct to add-to-cart with retry
                max_add_to_cart_attempts = 3
                add_to_cart_succeeded = False

                for attempt in range(max_add_to_cart_attempts):
                    try:
                        self.logger.info(f"🏁 SPEED MODE: Direct add-to-cart targeting (attempt {attempt + 1}/{max_add_to_cart_attempts})")

                        if attempt > 0:
                            # Wait before retry and refresh page
                            await self.ultra_delay(1000, 2000)
                            await page.reload(wait_until='domcontentloaded')
                            await self.ultra_delay(500, 1000)

                        # ULTRA-FAST: Skip human simulation, find button immediately
                        add_button, selector = await self.parallel_element_finder(page, self.SELECTORS['add_to_cart'], 2000)

                        if not add_button:
                            self.logger.warning("No add to cart button found with parallel finder, analyzing page...")

                            # Enhanced debugging - get current page info
                            current_url = page.url
                            page_title = await page.title()
                            self.logger.info(f"🔍 Current page: {current_url}")
                            self.logger.info(f"🔍 Page title: {page_title}")

                            # Check if we're still logged in
                            login_status = await self.ultra_fast_login_check(page)
                            if not login_status:
                                self.logger.error("❌ Session expired - not logged in!")
                                debug_screenshot = f"logs/session_expired_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                                await page.screenshot(path=debug_screenshot, full_page=True)
                                return {
                                    'success': False,
                                    'tcin': tcin,
                                    'reason': 'session_expired',
                                    'message': 'Session expired during purchase attempt',
                                    'screenshot': debug_screenshot
                                }

                            # DEBUG: Take screenshot to see what page actually looks like
                            debug_screenshot = f"logs/debug_no_button_{tcin}_attempt{attempt+1}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                            await page.screenshot(path=debug_screenshot, full_page=True)
                            self.logger.info(f"🔍 Debug screenshot saved: {debug_screenshot}")

                            # Try to find ANY button on the page to understand the structure
                            all_buttons = await page.query_selector_all('button')
                            if all_buttons:
                                button_texts = []
                                for i, btn in enumerate(all_buttons[:10]):  # Check first 10 buttons
                                    try:
                                        text = await btn.text_content()
                                        if text and text.strip():
                                            button_texts.append(text.strip())
                                    except:
                                        continue
                                self.logger.info(f"🔍 Found buttons with text: {button_texts}")
                            else:
                                self.logger.warning("🔍 No buttons found on page at all!")

                            # Try text-based clicking as immediate fallback - include preorder options
                            fallback_texts = ['text="Add to cart"', 'text="Preorder"', 'text="Pre-order"']

                            for fallback_text in fallback_texts:
                                try:
                                    await page.click(fallback_text, timeout=1000)
                                    self.logger.info(f"✅ Button clicked via text locator: {fallback_text}")
                                    add_button = "text_locator_success"
                                    break
                                except Exception as e:
                                    self.logger.info(f"Text locator failed for {fallback_text}: {e}")
                                    continue

                            if not add_button:
                                # Check if we're still on the product page or if it changed
                                try:
                                    current_url = page.url
                                    page_content = await page.content()

                                    # If URL changed, it might mean add-to-cart already worked
                                    if "cart" in current_url or "checkout" in current_url:
                                        self.logger.info(f"Page redirected to {current_url} - add-to-cart may have succeeded")
                                        add_button = "redirect_success"
                                    elif "product not found" in page_content.lower() or "404" in page_content:
                                        raise Exception("Product not found or unavailable")
                                    else:
                                        self.logger.warning(f"No add-to-cart button found on {current_url}")
                                        raise Exception("No add to cart or preorder button found - product may require options selection")
                                except Exception as content_error:
                                    self.logger.warning(f"Could not check page content: {content_error}")
                                    # Don't fail completely - might be a temporary issue
                                    raise Exception(f"Add-to-cart button not found - page check failed: {content_error}")
                        else:
                            self.logger.info(f"✅ Found add to cart: {selector}")

                        # Ultra-fast clicking
                        if add_button and add_button not in ["text_locator_success", "redirect_success"]:
                            await self.ultra_click(page, add_button)
                            self.logger.info("✅ Add to cart clicked")
                            # CRITICAL: Wait for page transition after add-to-cart click
                            await self.ultra_delay(1000, 2000)  # Allow for any redirects/refreshes
                        elif add_button == "text_locator_success":
                            self.logger.info("✅ Add to cart already clicked via text locator")
                            # CRITICAL: Wait for page transition after add-to-cart click
                            await self.ultra_delay(1000, 2000)  # Allow for any redirects/refreshes
                        elif add_button == "redirect_success":
                            self.logger.info("✅ Add to cart succeeded - page already redirected")
                            # No need to wait since redirect already happened

                        # Ultra-fast popup dismissal
                        await self.ultra_delay(100, 200)
                        popup_selectors = ['button[data-test="modal-drawer-close-button"]', 'button:has-text("No thanks")', '[aria-label="close"]']
                        popup_element, popup_selector = await self.parallel_element_finder(page, popup_selectors, 300)
                        if popup_element:
                            await self.ultra_click(page, popup_element)
                            self.logger.info(f"Fast popup dismissed: {popup_selector}")

                        # CRITICAL VALIDATION: Verify add-to-cart actually succeeded
                        self.logger.info("🔍 Validating add-to-cart success...")
                        await self.ultra_delay(500, 1000)  # Allow time for cart to update

                        # Check for cart count indicator or success message
                        cart_success_indicators = [
                            '[data-test="@web/CartLink"] span',  # Cart count badge
                            '.CartIcon__badge',  # Alternative cart badge
                            'text="Added to cart"',  # Success message
                            '[aria-label*="cart"] [class*="badge"]',  # Generic cart badge
                            '[data-test="cart-count"]'  # Direct cart count
                        ]

                        cart_validated = False
                        for indicator in cart_success_indicators:
                            try:
                                element = await page.wait_for_selector(indicator, timeout=1000)
                                if element and await element.is_visible():
                                    text_content = await element.text_content()
                                    if text_content and text_content.strip() and text_content.strip() != '0':
                                        cart_validated = True
                                        self.logger.info(f"✅ Cart validated via {indicator}: '{text_content}'")
                                        break
                            except:
                                continue

                        # FAIL FAST: If add-to-cart validation failed, retry if attempts remain
                        if not cart_validated:
                            self.logger.error(f"❌ Add-to-cart validation failed on attempt {attempt + 1}")
                            if attempt == max_add_to_cart_attempts - 1:
                                # Final attempt failed - take screenshot and return error
                                screenshot_path = f"logs/add_to_cart_validation_failed_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                                await page.screenshot(path=screenshot_path, full_page=True)

                                self.purchase_log.error(f"Add-to-cart validation failed for {tcin} after {max_add_to_cart_attempts} attempts")
                                self._update_status('FAILED', {'tcin': tcin, 'reason': 'add_to_cart_validation_failed_all_attempts'})
                                return {
                                    'success': False,
                                    'tcin': tcin,
                                    'reason': 'add_to_cart_validation_failed_all_attempts',
                                    'error': f'Add-to-cart validation failed after {max_add_to_cart_attempts} attempts',
                                    'screenshot': screenshot_path
                                }
                            else:
                                # More attempts remain - continue to next attempt
                                self.logger.info(f"Retrying add-to-cart... {max_add_to_cart_attempts - attempt - 1} attempts remaining")
                                continue

                        # Validation succeeded!
                        self.logger.info("✅ Add-to-cart validated successfully")
                        add_to_cart_succeeded = True
                        break  # Exit retry loop on success

                    except Exception as e:
                        self.logger.error(f"Add-to-cart attempt {attempt + 1} failed: {e}")
                        if attempt == max_add_to_cart_attempts - 1:
                            # Final attempt failed - return error
                            self.purchase_log.error(f"Could not add to cart after {max_add_to_cart_attempts} attempts: {e}")
                            self._update_status('FAILED', {'tcin': tcin, 'reason': 'add_to_cart_failed_all_attempts'})
                            return {
                                'success': False,
                                'tcin': tcin,
                                'reason': 'add_to_cart_failed_all_attempts',
                                'error': f'Add-to-cart failed after {max_add_to_cart_attempts} attempts: {str(e)}'
                            }
                        else:
                            # More attempts remain - continue to next attempt
                            self.logger.info(f"Retrying after exception... {max_add_to_cart_attempts - attempt - 1} attempts remaining")
                            continue

                # Final check - if none of the attempts succeeded, return error
                if not add_to_cart_succeeded:
                    self.purchase_log.error(f"Add-to-cart completely failed for {tcin} after all attempts")
                    self._update_status('FAILED', {'tcin': tcin, 'reason': 'add_to_cart_completely_failed'})
                    return {
                        'success': False,
                        'tcin': tcin,
                        'reason': 'add_to_cart_completely_failed',
                        'error': f'Add-to-cart failed completely after {max_add_to_cart_attempts} attempts'
                    }

                # ⚡ COMPETITION MODE: Cart already validated, proceed to checkout
                self.logger.info("🏁 COMPETITION MODE: Add-to-cart validated, proceeding to checkout")

                # Navigate to checkout with improved error handling
                try:
                    self.logger.info("🛒 Navigating to cart...")
                    await page.goto("https://www.target.com/cart", wait_until='domcontentloaded', timeout=10000)
                    await self.ultra_delay(500, 1000)  # Allow cart page to fully load
                    self.logger.info("✓ Cart page loaded successfully")
                except Exception as cart_error:
                    self.logger.warning(f"Cart navigation failed, retrying: {cart_error}")
                    # Retry cart navigation once
                    try:
                        await page.reload(wait_until='domcontentloaded')
                        await self.ultra_delay(1000, 2000)
                        await page.goto("https://www.target.com/cart", wait_until='networkidle', timeout=15000)
                        await self.ultra_delay(1000, 2000)
                        self.logger.info("✓ Cart page loaded on retry")
                    except Exception as retry_error:
                        self.logger.error(f"Cart navigation failed completely: {retry_error}")
                        raise Exception(f"Failed to navigate to cart: {cart_error}")

                # Verify we're actually on the cart page
                current_url = page.url
                if 'cart' not in current_url.lower():
                    self.logger.warning(f"Not on cart page - current URL: {current_url}")
                    # Try direct cart URL one more time
                    await page.goto("https://www.target.com/cart", wait_until='networkidle', timeout=15000)
                    await self.ultra_delay(1000, 2000)

                # DOUBLE-CHECK: Verify cart is not empty on cart page
                self.logger.info("🔍 Double-checking cart is not empty...")
                empty_cart_indicators = [
                    'text="Your cart is empty"',
                    '.emptyCart',
                    '[data-test="emptyCart"]',
                    'text="Continue shopping"'
                ]

                cart_is_empty = False
                for empty_indicator in empty_cart_indicators:
                    try:
                        element = await page.wait_for_selector(empty_indicator, timeout=1000)
                        if element and await element.is_visible():
                            cart_is_empty = True
                            self.logger.error(f"❌ Cart is empty detected via: {empty_indicator}")
                            break
                    except:
                        continue

                if cart_is_empty:
                    screenshot_path = f"logs/cart_empty_error_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                    await page.screenshot(path=screenshot_path, full_page=True)

                    self.purchase_log.error(f"Cart is empty on cart page for {tcin}")
                    self._update_status('FAILED', {'tcin': tcin, 'reason': 'cart_empty'})
                    return {
                        'success': False,
                        'tcin': tcin,
                        'reason': 'cart_empty_on_checkout',
                        'error': 'Cart is empty when attempting checkout',
                        'screenshot': screenshot_path
                    }

                self.logger.info("✅ Cart verification passed - proceeding to checkout")

                # ⚡ COMPETITION MODE: Ultra-fast checkout button click
                try:
                    checkout_element, checkout_selector = await self.parallel_element_finder(page, self.SELECTORS['checkout'], 800)

                    if checkout_element:
                        await self.ultra_click(page, checkout_element)
                        await self.ultra_delay(100, 200)  # SPEED: Minimal checkout delay
                    else:
                        raise Exception("No checkout button found")

                    # Navigate through checkout steps
                    already_logged_in = await self.ultra_fast_login_check(page)
                    authentication_needed = not already_logged_in

                    # ⚡ COMPETITION MODE: Ultra-fast authentication handling
                    if not already_logged_in:
                        try:
                            await self.ultra_delay(100, 200)  # SPEED: Minimal modal wait

                            # Check for various authentication prompts that might appear
                            auth_prompt_selectors = [
                                'text="Sign in to your account"',
                                '[aria-label="Sign in to your account"]',
                                'h1:has-text("Sign in")',
                                'h2:has-text("Sign in")',
                                '[data-test*="signin"]',
                                '[data-test*="login"]',
                                'text="Please sign in"',
                                'text="Authentication required"'
                            ]

                            auth_prompt_found = None
                            for prompt_selector in auth_prompt_selectors:
                                try:
                                    prompt_element = await page.wait_for_selector(prompt_selector, timeout=1000)
                                    if prompt_element and await prompt_element.is_visible():
                                        self.logger.info(f"Authentication prompt detected: {prompt_selector}")
                                        auth_prompt_found = prompt_selector
                                        authentication_needed = True
                                        break
                                except:
                                    continue

                            if authentication_needed:
                                self.logger.info("Starting dynamic authentication flow")

                                # Step 1: Try to close the modal/prompt first (maybe session is still valid)
                                auth_closed = False
                                close_attempts = [
                                '[aria-label="close"]',
                                'button[aria-label="close"]',
                                'button:has-text("×")',
                                '[data-test="modal-close"]',
                                '.modal-close',
                                'button[class*="close"]'
                                ]

                                for close_selector in close_attempts:
                                    try:
                                        close_btn = await page.wait_for_selector(close_selector, timeout=1000)
                                        if close_btn and await close_btn.is_visible():
                                            await self.human_click(page, close_btn)
                                            await self.ultra_delay(200, 400)

                                        # Check if authentication prompt is gone
                                        prompt_still_visible = False
                                        for check_selector in auth_prompt_selectors[:3]:
                                            try:
                                                check_element = await page.wait_for_selector(check_selector, timeout=500)
                                                if check_element and await check_element.is_visible():
                                                    prompt_still_visible = True
                                                    break
                                            except:
                                                pass

                                        if not prompt_still_visible:
                                            self.logger.info(f"Authentication prompt closed successfully: {close_selector}")
                                            auth_closed = True
                                            authentication_needed = False
                                            break
                                        else:
                                            self.logger.info(f"Close attempted but prompt still visible: {close_selector}")
                                    except:
                                        continue

                                # Step 2: If auth prompt still there, handle authentication options dynamically
                                if authentication_needed and not auth_closed:
                                    self.logger.info("Authentication required - handling available options")

                                    # Check what authentication options are available
                                    passkey_available = False
                                    password_available = False
                                    code_available = False

                                    try:
                                        # Check if passkey option exists
                                        passkey_element = await page.wait_for_selector('text="Use a passkey"', timeout=1000)
                                        if passkey_element and await passkey_element.is_visible():
                                            passkey_available = True
                                            self.logger.info("Passkey option detected")
                                    except:
                                        pass

                                    try:
                                        # Check if password option exists
                                        password_element = await page.wait_for_selector('text="Enter your password"', timeout=1000)
                                        if password_element and await password_element.is_visible():
                                            password_available = True
                                            self.logger.info("Password option detected")
                                    except:
                                        pass

                                    try:
                                        # Check if code option exists
                                        code_element = await page.wait_for_selector('text="Get a code"', timeout=1000)
                                        if code_element and await code_element.is_visible():
                                            code_available = True
                                            self.logger.info("Code option detected")
                                    except:
                                        pass

                                    # Ultra-dynamic passkey dismissal system
                                    if passkey_available:
                                        await self._ultra_dynamic_passkey_dismissal(page, tcin)
                                    else:
                                        self.logger.info("No passkey option detected - skipping dismissal")

                                    # Handle "Keep me signed in" option if present
                                    keep_signed_in_checked = False
                                    try:
                                        keep_signed_in_selectors = [
                                            'input[type="checkbox"]:near(text="Keep me signed in")',
                                            'label:has-text("Keep me signed in")',
                                            '[data-test*="keep-signed"]',
                                            'input[name="keep_me_signed_in"]'
                                        ]

                                        for keep_selector in keep_signed_in_selectors:
                                            try:
                                                keep_element = await page.wait_for_selector(keep_selector, timeout=1000)
                                                if keep_element and await keep_element.is_visible():
                                                    await self.human_click(page, keep_element)
                                                    self.logger.info(f"Clicked keep me signed in: {keep_selector}")
                                                    break
                                            except:
                                                continue
                                    except Exception as e:
                                        self.logger.info(f"Keep signed in check failed: {e}")

                                    # Handle password option only if available
                                    try:
                                        password_option_selectors = [
                                            'button:has-text("Enter your password")',
                                            '[data-test*="password-option"]',
                                            '.password-option',
                                            'text="Enter your password"'
                                        ]

                                        password_option_clicked = False
                                        for pwd_option_selector in password_option_selectors:
                                            try:
                                                pwd_option_element = await page.wait_for_selector(pwd_option_selector, timeout=2000)
                                                if pwd_option_element and await pwd_option_element.is_visible():
                                                    await self.human_click(page, pwd_option_element)
                                                    self.logger.info(f"Clicked password option: {pwd_option_selector}")
                                                    password_option_clicked = True
                                                    await self.ultra_delay(500, 1000)  # Wait for password form
                                                    break
                                            except:
                                                continue

                                        if not password_option_clicked:
                                            self.logger.warning("Could not find password option - session may need manual intervention")

                                            # Take screenshot for debugging
                                            auth_screenshot = f"logs/signin_modal_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                                            await page.screenshot(path=auth_screenshot)

                                            pass  # Browser will be closed in finally block
                                            return {
                                                'success': False,
                                                'tcin': tcin,
                                                'reason': 'signin_modal_password_option_not_found',
                                                'message': 'Sign-in modal requires authentication but password option not found',
                                                'screenshot': auth_screenshot
                                            }
                                        else:
                                            self.logger.info("Authentication flow initiated - looking for password form")

                                            # Step 4: Wait for and fill password form
                                            try:
                                                await self.ultra_delay(200, 400)  # Wait for password form to appear

                                                # Look for password input field
                                                password_input_selectors = [
                                                    'input[type="password"]',
                                                    'input[name="password"]',
                                                    '#password',
                                                    '[data-test*="password-input"]',
                                                    '[autocomplete="current-password"]'
                                                ]

                                                password_field = None
                                                for pwd_input_selector in password_input_selectors:
                                                    try:
                                                        password_field = await page.wait_for_selector(pwd_input_selector, timeout=3000)
                                                        if password_field and await password_field.is_visible():
                                                            self.logger.info(f"Found password field: {pwd_input_selector}")
                                                            break
                                                    except:
                                                        continue

                                                if not password_field:
                                                    self.logger.warning("Password field not found after authentication flow")

                                                    # Take screenshot for debugging
                                                    auth_screenshot = f"logs/password_field_missing_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                                                    await page.screenshot(path=auth_screenshot)

                                                    pass  # Browser will be closed in finally block
                                                    return {
                                                        'success': False,
                                                        'tcin': tcin,
                                                        'reason': 'password_field_not_found',
                                                        'message': 'Password authentication initiated but password field not found',
                                                        'screenshot': auth_screenshot
                                                    }
                                                else:
                                                    self.logger.info("Password field found - proceeding with automated authentication")

                                                    if not self.target_password:
                                                        self.logger.error("Password field found but TARGET_PASSWORD environment variable not set")

                                                        # Take screenshot for debugging
                                                        auth_screenshot = f"logs/password_missing_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                                                        await page.screenshot(path=auth_screenshot)

                                                        pass  # Browser will be closed in finally block
                                                        return {
                                                            'success': False,
                                                            'tcin': tcin,
                                                            'reason': 'password_env_var_missing',
                                                            'message': 'Set TARGET_PASSWORD environment variable for automated authentication',
                                                            'screenshot': auth_screenshot
                                                        }

                                                    # Automated password entry
                                                    try:
                                                        # Click on the password field to focus it
                                                        await self.human_click(page, password_field)
                                                        await page.wait_for_timeout(500)

                                                        # Clear any existing content using keyboard
                                                        await page.keyboard.down('Control')
                                                        await page.keyboard.press('a')  # Select all
                                                        await page.keyboard.up('Control')
                                                        await page.keyboard.press('Delete')  # Delete selected
                                                        await page.wait_for_timeout(300)

                                                        # Type password with human-like delays
                                                        await page.keyboard.type(self.target_password, delay=random.randint(80, 150))
                                                        self.logger.info("Password entered successfully")

                                                        await self.ultra_delay(100, 200)  # Brief pause after typing

                                                        # Step 5: Submit the login form
                                                        sign_in_clicked = False
                                                        sign_in_selectors = [
                                                            'button[type="submit"]',
                                                            'button:has-text("Sign in")',
                                                            'button:has-text("Sign In")',
                                                            'button:has-text("Log in")',
                                                            'button:has-text("Continue")',
                                                            '[data-test*="signin-button"]',
                                                            '[data-test*="login-button"]',
                                                            'input[type="submit"]'
                                                        ]

                                                        for signin_selector in sign_in_selectors:
                                                            try:
                                                                signin_btn = await page.wait_for_selector(signin_selector, timeout=2000)
                                                                if signin_btn and await signin_btn.is_visible() and await signin_btn.is_enabled():
                                                                    await self.human_click(page, signin_btn)
                                                                    self.logger.info(f"Clicked sign-in button: {signin_selector}")
                                                                    sign_in_clicked = True
                                                                    break
                                                            except:
                                                                continue

                                                        if not sign_in_clicked:
                                                            # Try pressing Enter as fallback
                                                            try:
                                                                await password_field.press('Enter')
                                                                self.logger.info("Submitted login form using Enter key")
                                                                sign_in_clicked = True
                                                            except:
                                                                pass

                                                        if not sign_in_clicked:
                                                            self.logger.warning("Could not find sign-in button or submit form")

                                                            auth_screenshot = f"logs/signin_button_missing_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                                                            await page.screenshot(path=auth_screenshot)

                                                            pass  # Browser will be closed in finally block
                                                            return {
                                                                'success': False,
                                                                'tcin': tcin,
                                                                'reason': 'signin_button_not_found',
                                                                'message': 'Password entered but could not submit login form',
                                                                'screenshot': auth_screenshot
                                                            }

                                                        # Wait for login to process
                                                        await page.wait_for_timeout(3000)
                                                        self.logger.info("Login form submitted - waiting for authentication")

                                                        # CRITICAL: Check for post-password popups (this is where bot gets stuck!)
                                                        await self._handle_post_password_popups(page, tcin)

                                                    except Exception as e:
                                                        self.logger.error(f"Failed to enter password: {e}")

                                                        auth_screenshot = f"logs/password_entry_failed_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                                                        await page.screenshot(path=auth_screenshot)

                                                        pass  # Browser will be closed in finally block
                                                        return {
                                                            'success': False,
                                                            'tcin': tcin,
                                                            'reason': 'password_entry_failed',
                                                            'message': 'Failed to enter password automatically',
                                                            'screenshot': auth_screenshot
                                                        }

                                            except Exception as e:
                                                self.logger.error(f"Password form handling failed: {e}")

                                                auth_screenshot = f"logs/password_form_error_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                                                await page.screenshot(path=auth_screenshot)

                                                pass  # Browser will be closed in finally block
                                                return {
                                                    'success': False,
                                                    'tcin': tcin,
                                                    'reason': 'password_form_error',
                                                    'message': 'Error occurred while trying to access password form',
                                                    'screenshot': auth_screenshot
                                                }

                                    except Exception as e:
                                        self.logger.error(f"Password option selection failed: {e}")

                                        auth_screenshot = f"logs/signin_error_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                                        await page.screenshot(path=auth_screenshot)

                                        pass  # Browser will be closed in finally block
                                        return {
                                            'success': False,
                                            'tcin': tcin,
                                            'reason': 'signin_authentication_failed',
                                            'message': 'Failed to initiate password authentication',
                                            'screenshot': auth_screenshot
                                        }

                                # Enhanced authentication success verification
                                if authentication_needed:
                                    try:
                                        # Wait for authentication to complete
                                        await page.wait_for_timeout(5000)

                                        # Check if authentication prompt disappeared
                                        auth_still_blocking = False
                                        for check_selector in auth_prompt_selectors[:3]:
                                            try:
                                                check_element = await page.wait_for_selector(check_selector, timeout=1000)
                                                if check_element and await check_element.is_visible():
                                                    auth_still_blocking = True
                                                    break
                                            except:
                                                pass

                                        # Additional verification: Look for successful login indicators
                                        login_success_indicators = [
                                            '[data-test="@web/AccountLink"]',  # Account link in header
                                            'text="Account"',
                                            'text="Hi,"',  # Greeting with name
                                            '[aria-label*="Account"]',
                                            '.account-link',
                                            'button[aria-label*="account"]'
                                        ]

                                        login_verified = False
                                        for success_indicator in login_success_indicators:
                                            try:
                                                success_element = await page.wait_for_selector(success_indicator, timeout=2000)
                                                if success_element and await success_element.is_visible():
                                                    login_verified = True
                                                    self.logger.info(f"Login success verified via: {success_indicator}")
                                                    break
                                            except:
                                                continue

                                        if auth_still_blocking:
                                            # Check if it's a 2FA prompt or other additional step
                                            try:
                                                twofa_indicators = [
                                                    'text="verification code"',
                                                    'text="2-step verification"',
                                                    'text="Enter the code"',
                                                    'input[name*="code"]',
                                                    'input[type="tel"]'
                                                ]

                                                twofa_detected = False
                                                for twofa_selector in twofa_indicators:
                                                    try:
                                                        twofa_element = await page.wait_for_selector(twofa_selector, timeout=1000)
                                                        if twofa_element and await twofa_element.is_visible():
                                                            twofa_detected = True
                                                            self.logger.warning("2FA verification code required")
                                                            break
                                                    except:
                                                        continue

                                                if twofa_detected:
                                                    auth_screenshot = f"logs/2fa_required_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                                                    await page.screenshot(path=auth_screenshot)

                                                    pass  # Browser will be closed in finally block
                                                    return {
                                                        'success': False,
                                                        'tcin': tcin,
                                                        'reason': '2fa_verification_required',
                                                        'message': '2FA verification code required - cannot automate',
                                                        'screenshot': auth_screenshot
                                                    }
                                            except:
                                                pass

                                            self.logger.warning("Authentication failed or still required")
                                            auth_screenshot = f"logs/auth_failed_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                                            await page.screenshot(path=auth_screenshot)

                                            pass  # Browser will be closed in finally block
                                            return {
                                                'success': False,
                                                'tcin': tcin,
                                                'reason': 'authentication_failed',
                                                'message': 'Authentication was not successful',
                                                'screenshot': auth_screenshot
                                            }
                                        elif login_verified:
                                            self.logger.info("✓ Authentication successful - login verified")
                                        else:
                                            self.logger.info("Authentication prompt disappeared - assuming success")

                                    except Exception as e:
                                        self.logger.info(f"Authentication handling failed: {e}")
                                        # Continue anyway - maybe checkout will work
                                else:
                                    self.logger.info("🎉 Skipping authentication - user already logged in via saved session")
                        except Exception as e:
                            self.logger.error(f"Authentication block failed: {e}")
                            # Continue anyway - maybe checkout will work
                    # Logged in - proceed to checkout

                    # Checkout process
                    try:
                        # Step 1: Handle shipping/delivery options
                        # Look for shipping options
                        shipping_options = await page.query_selector_all('[data-test*="delivery"], [data-test*="shipping"]')
                        if shipping_options:
                            # Select first available shipping option
                            await shipping_options[0].click()
                            self.logger.info("Selected shipping option")
                            await self.ultra_delay(200, 400)

                        # Step 2: Handle payment method
                        # Look for continue/next buttons to proceed
                        continue_selectors = [
                            'button[data-test="continue-button"]',
                            'button[data-test="save-and-continue-button"]',
                            'button:has-text("Continue")',
                            'button:has-text("Save and continue")'
                        ]

                        for selector in continue_selectors:
                            try:
                                continue_btn = await page.wait_for_selector(selector, timeout=3000)
                                await continue_btn.click()
                                self.logger.info(f"Clicked continue button: {selector}")
                                await page.wait_for_timeout(3000)
                                break
                            except:
                                continue

                        # Step 3: Look for final place order button
                        place_order_found = False
                        place_order_selectors = [
                            'button[data-test="placeOrderButton"]',
                            'button[data-test="place-order-button"]',
                            'button:has-text("Place order")',
                            'button:has-text("Complete order")',
                            '[data-test*="place"][data-test*="order"]'
                        ]

                        for selector in place_order_selectors:
                            try:
                                place_order_btn = await page.wait_for_selector(selector, timeout=5000)
                                if place_order_btn:
                                    place_order_found = True
                                    self.logger.info(f"Found place order button: {selector}")

                                    # Take screenshot at final step for visual verification
                                    screenshot_path = f"logs/final_step_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                                    await page.screenshot(path=screenshot_path)
                                    self.logger.info(f"📸 Final checkout screenshot: {screenshot_path}")

                                    # 🧪 TEST MODE CHECK - Stop before final purchase to avoid cancellations
                                    test_mode = os.environ.get('TEST_MODE', 'false').lower() == 'true'
                                    if test_mode:
                                        self.purchase_log.warning(f"🧪 TEST MODE: Stopping before final purchase for {tcin}")
                                        return {
                                            'success': True,
                                            'tcin': tcin,
                                            'test_mode': True,
                                            'message': f'✅ TEST COMPLETE: Ready to purchase {tcin} - Place order button found',
                                            'screenshot': screenshot_path,
                                            'ready_to_purchase': True,
                                            'next_step': 'Set TEST_MODE=false to complete actual purchase'
                                        }

                                    # SAFETY CHECK - Never click place order unless explicitly authorized
                                    if os.environ.get('FINAL_PURCHASE_AUTHORIZED') == 'YES_COMPLETE_PURCHASE':
                                        self.purchase_log.warning(f"FINAL PURCHASE AUTHORIZED - PLACING ORDER for {tcin}")
                                        await place_order_btn.click()

                                        # Wait for order confirmation
                                        await page.wait_for_timeout(10000)

                                        # Look for order number
                                        try:
                                            order_selectors = [
                                                '[data-test="order-number"]',
                                                '[data-test*="order"][data-test*="number"]',
                                                '.order-number',
                                                'h1:has-text("Order")'
                                            ]

                                            order_number = None
                                            for order_selector in order_selectors:
                                                try:
                                                    order_element = await page.wait_for_selector(order_selector, timeout=5000)
                                                    order_number = await order_element.text_content()
                                                    if order_number:
                                                        break
                                                except:
                                                    continue

                                            if order_number:
                                                self.purchase_log.warning(f"ORDER COMPLETED: {order_number} for {tcin}")

                                                pass  # Browser will be closed in finally block
                                                return {
                                                    'success': True,
                                                    'tcin': tcin,
                                                    'order_number': order_number.strip(),
                                                    'completed': True
                                                }
                                            else:
                                                self.purchase_log.error("Order placed but could not find order number")

                                        except Exception as e:
                                            self.purchase_log.error(f"Error finding order confirmation: {e}")

                                    else:
                                        self.purchase_log.warning(f"🎯 READY TO PURCHASE {tcin} - STOPPED at final step (no authorization)")
                                        self.purchase_log.warning(f"✅ All checkout steps completed successfully!")
                                        self._update_status('READY_TO_PURCHASE', {'tcin': tcin})
                                        pass  # Browser will be closed in finally block
                                        return {
                                            'success': True,
                                            'tcin': tcin,
                                            'ready_to_purchase': True,
                                            'message': f'Successfully reached final checkout step for {tcin}',
                                            'screenshot': screenshot_path
                                        }
                                    break
                            except:
                                continue

                        if not place_order_found:
                            self.purchase_log.error("Could not find place order button")

                            # Take screenshot for debugging
                            screenshot_path = f"logs/checkout_stuck_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                            await page.screenshot(path=screenshot_path)

                            pass  # Browser will be closed in finally block
                            return {
                                'success': False,
                                'tcin': tcin,
                                'reason': 'place_order_button_not_found',
                                'screenshot': screenshot_path
                            }

                    except Exception as e:
                        self.purchase_log.error(f"Checkout flow failed: {e}")

                        # Take screenshot for debugging
                        screenshot_path = f"logs/checkout_error_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                        await page.screenshot(path=screenshot_path)

                        return {
                            'success': False,
                            'tcin': tcin,
                            'reason': 'checkout_flow_failed',
                            'error': str(e),
                            'screenshot': screenshot_path
                        }

                except Exception as e:
                    self.purchase_log.error(f"Checkout flow setup failed: {e}")

                    # Take screenshot for debugging
                    screenshot_path = f"logs/checkout_setup_error_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                    await page.screenshot(path=screenshot_path)

                    return {
                        'success': False,
                        'tcin': tcin,
                        'reason': 'checkout_setup_failed',
                        'error': str(e),
                        'screenshot': screenshot_path
                    }

        except Exception as e:
            self.purchase_log.error(f"Purchase attempt failed: {e}")
            return {
                'success': False,
                'tcin': tcin,
                'reason': 'exception',
                'error': str(e)
            }
        finally:
            # CRITICAL: Always close browser to prevent resource leaks
            if browser:
                try:
                    await browser.close()
                except:
                    pass  # Ignore errors during cleanup

def status_callback(status: str, details: dict):
    """Status callback for monitoring system integration"""
    print(f"STATUS UPDATE: {status} - {details}")
    # In real implementation, this would send status to monitoring dashboard
    # Example: send_to_dashboard(status, details)

async def purchase_product(tcin: str, session_path: str = 'target.json',
                          status_callback_func: Optional[Callable] = None):
    """
    Direct function for dashboard to call for purchasing a product

    Args:
        tcin: Target product ID
        session_path: Path to Target session file
        status_callback_func: Optional callback for status updates

    Returns:
        Dict with purchase result
    """
    try:
        product = {
            'tcin': tcin
        }

        bot = BuyBot(session_path, status_callback=status_callback_func)
        return await bot.attempt_purchase(product)

    except Exception as e:
        # Ensure status callback gets called even on unexpected errors
        if status_callback_func:
            try:
                status_callback_func('FAILED', {'tcin': tcin, 'reason': 'process_crash', 'error': str(e)})
            except:
                pass

        return {
            'success': False,
            'tcin': tcin,
            'reason': 'process_crash',
            'error': str(e)
        }

async def main():
    """Ultra-fast BuyBot with monitoring integration"""
    try:
        # Clean logging for production use
        logging.basicConfig(
            level=logging.WARNING,  # Minimal logging for clean output
            format='%(asctime)s - %(levelname)s - %(message)s'
        )

        # Product configuration from environment (for dashboard integration)
        test_tcin = os.environ.get('TARGET_TCIN', '75567580')

        # 🧪 TEST MODE: Stop before final purchase to avoid cancellations
        test_mode = os.environ.get('TEST_MODE', 'false').lower() == 'true'
        if test_mode:
            print("🧪 TEST MODE ENABLED - Will stop before final purchase confirmation")

        # Initialize ultra-fast bot with configurable session path
        session_path = os.environ.get('TARGET_SESSION_PATH', 'target.json')

        print(f"ULTRA-FAST BOT: Starting purchase for TCIN {test_tcin}")

        # Record start time for performance measurement
        import time
        start_time = time.time()

        # Use the robust purchase function
        result = await purchase_product(
            test_tcin,
            session_path,
            status_callback
        )

        end_time = time.time()
        duration = end_time - start_time

        print(f"⚡ Execution time: {duration:.2f} seconds")
        print(f"📊 Final result: {result}")

        # Performance expectations:
        # - Add to cart: 1-3 seconds
        # - Complete checkout: 3-8 seconds total
        # - Much faster than original 15-30+ seconds

    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        print("Bot process crashed - check environment variables and session file")

if __name__ == "__main__":
    print("TARGET.COM ULTRA-FAST CHECKOUT BOT")
    print("Optimized for competitive purchasing")
    print("Anti-detection with consistent fingerprints")
    print("Dashboard integration ready\n")

    asyncio.run(main())
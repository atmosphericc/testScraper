#!/usr/bin/env python3
"""
Persistent Session Manager - Maintains long-lived browser context for Target.com
Handles session lifecycle, validation, and recovery with minimal overhead
"""

import json
import time
import asyncio
import logging
import threading
import random
import os
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from patchright.async_api import async_playwright, Browser, BrowserContext, Page
# Patchright replaces playwright-stealth - it patches at source level automatically!


class SessionManager:
    """Manages persistent browser session for Target.com automation"""

    def __init__(self, session_path: str = "target.json"):
        self.session_path = Path(session_path)
        self.logger = logging.getLogger(__name__)

        # CRITICAL: User data directory for persistent context (cookies persist to disk here!)
        self.user_data_dir = Path("./playwright-profile")
        self.user_data_dir.mkdir(exist_ok=True)

        # Playwright instances
        self.playwright = None
        self.browser: Optional[Browser] = None  # Will be None with persistent context
        self.context: Optional[BrowserContext] = None
        self._context_lock = threading.Lock()

        # Session state
        self.session_active = False
        self.last_validation = None
        self.session_created_at = None
        self.validation_failures = 0

        # BUGFIX: Purchase lock to prevent validation during active purchases
        self.purchase_in_progress = False
        self._purchase_lock = threading.Lock()

        # Configuration
        self.max_validation_failures = 3
        self.validation_timeout = 30000  # 30 seconds (increased for slow-loading pages)

        # Context lifecycle management
        self._initialization_attempts = 0
        self._max_init_attempts = 3
        self._context_recreation_count = 0
        self._last_context_recreation = None

        # Event loop for thread-safe async operations
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

        # SessionStorage for restoration (Target.com checkout auth)
        self._session_storage: Optional[str] = None

        # CDP (Chrome DevTools Protocol) for cookie interception
        self._cdp_session = None
        self._cdp_cookie_interception_active = False

        # Cookie watchdog for continuous monitoring
        self._cookie_watchdog_task = None
        self._cookie_watchdog_running = False

        # Load fingerprint data for consistent sessions
        self.fingerprint_data = self._load_fingerprint_data()

    def _load_fingerprint_data(self) -> Dict[str, Any]:
        """Load consistent fingerprint data from session file"""
        try:
            if self.session_path.exists():
                with open(self.session_path, 'r') as f:
                    session_data = json.load(f)
                    fingerprint = session_data.get('fingerprint', {})
                    if fingerprint:
                        self.logger.info(f" Loaded fingerprint from {self.session_path}")
                        return fingerprint
        except Exception as e:
            self.logger.warning(f"Could not load fingerprint: {e}")

        # F5 BYPASS: Generate random but realistic fingerprint
        # This will be saved and reused for consistency

        # Pool of realistic Chrome user-agents (latest versions)
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        ]

        # Pool of realistic viewport sizes (common desktop resolutions)
        viewports = [
            {'width': 1920, 'height': 1080},  # Full HD
            {'width': 1536, 'height': 864},   # Laptop HD+
            {'width': 1440, 'height': 900},   # MacBook Pro 14"
            {'width': 1680, 'height': 1050},  # 20" monitor
            {'width': 2560, 'height': 1440},  # 2K/QHD
        ]

        # Randomly select from pools
        selected_ua = random.choice(user_agents)
        selected_viewport = random.choice(viewports)

        # F5 BYPASS: Match timezone to user-agent OS (timezone correlation)
        # F5 checks for inconsistencies between OS and timezone
        if 'Windows' in selected_ua:
            # Windows users spread across US timezones
            selected_timezone = random.choice([
                'America/New_York',      # EST
                'America/Chicago',       # CST
                'America/Denver',        # MST
                'America/Los_Angeles'    # PST
            ])
        elif 'Macintosh' in selected_ua:
            # Mac users typically in tech hubs (PST/EST)
            selected_timezone = random.choice([
                'America/Los_Angeles',   # PST (Silicon Valley, Seattle)
                'America/New_York'       # EST (NYC, Boston)
            ])
        else:
            # Fallback (shouldn't happen with our user-agents)
            selected_timezone = 'America/New_York'

        fingerprint = {
            'user_agent': selected_ua,
            'viewport': selected_viewport,
            'timezone': selected_timezone,
            'locale': 'en-US'
        }

        self.logger.info(f"🎲 Generated new fingerprint: {selected_viewport['width']}x{selected_viewport['height']}, Chrome {selected_ua.split('Chrome/')[1].split(' ')[0]}")

        return fingerprint

    async def initialize(self) -> bool:
        """Initialize persistent browser session with robust error handling"""
        self._initialization_attempts += 1

        try:
            self.logger.info(f"[INIT] Initializing persistent session (attempt {self._initialization_attempts}/{self._max_init_attempts})...")

            # Log session file status for debugging
            if self.session_path.exists():
                import os
                file_size = os.path.getsize(self.session_path)
                file_mtime = datetime.fromtimestamp(os.path.getmtime(self.session_path))
                self.logger.info(f"[INIT] 📁 Found session file: {self.session_path} ({file_size} bytes, last modified: {file_mtime.strftime('%Y-%m-%d %H:%M:%S')})")

                # Read saved_at timestamp from file
                try:
                    with open(self.session_path, 'r') as f:
                        session_data = json.load(f)
                        saved_at = session_data.get('saved_at', 'unknown')
                        self.logger.info(f"[INIT] 📅 Session was saved at: {saved_at}")
                except:
                    pass
            else:
                self.logger.info(f"[INIT] ℹ️  No session file found at {self.session_path} - will create new session")

            # Store the event loop for thread-safe operations
            self._event_loop = asyncio.get_running_loop()
            self.logger.info(f"[OK] Event loop stored for thread-safe operations")

            # Clean up any existing resources first
            await self._safe_cleanup()

            # CRITICAL FIX: Use launch_persistent_context instead of browser.launch()
            # This is THE KEY to making cookies persist across restarts!
            # Persistent context writes cookies to disk automatically
            self.playwright = await async_playwright().start()

            # Persistent context options - COMPLETE FIX for automation banner + sandbox warning
            # Patchright patches Playwright library code (not browser binary):
            # - Avoids Runtime.enable CDP command
            # - Disables Console API
            # - Modifies default args (including --disable-blink-features=AutomationControlled)
            # - Removes --enable-automation, --disable-popup-blocking, etc.
            # DO NOT manually add --disable-blink-features=AutomationControlled - Patchright handles it!
            persistent_options = {
                'headless': False,       # CRITICAL: Never use headless for stealth
                'no_viewport': True,     # Use native resolution (more natural)
                'ignore_default_args': [
                    '--enable-automation',      # Causes "controlled by automated test software" banner
                    '--no-sandbox',             # Causes "unsupported command-line flag" warning
                    '--disable-setuid-sandbox', # Related sandbox flag
                ],
                'args': [
                    '--test-type',              # Suppress Chrome's "unsupported command-line flag" warnings
                    '--start-maximized',        # Start with maximized window for visibility
                    # REMOVED: --restore-last-session (causes duplicate tabs, doesn't work reliably)
                    # See: https://github.com/microsoft/playwright/issues/10347
                    # Real fix: Convert ALL cookies (including auth) to persistent below
                ],
                'chromium_sandbox': True,  # Enable sandbox (prevents --no-sandbox warning)
            }

            # Launch with system Chrome via channel="chrome" (PATCHRIGHT RECOMMENDATION)
            # Patchright docs: "We recommend using Google Chrome instead of Chromium"
            # Real users don't browse with Chromium - using Chrome helps avoid detection
            self.logger.info(f"[INIT] Launching PATCHRIGHT persistent context with user_data_dir: {self.user_data_dir}")
            print(f"[SESSION_INIT] 🔧 Using PATCHRIGHT persistent context: {self.user_data_dir}")
            print(f"[SESSION_INIT] ⚡ Cookies will persist to disk automatically!")
            print(f"[SESSION_INIT] 🔑 Auth cookies will be converted to persistent on save!")
            print(f"[SESSION_INIT] 🥷 Patchright with system Chrome - NO automation banner!")

            self.context = await self.playwright.chromium.launch_persistent_context(
                str(self.user_data_dir),
                channel="chrome",  # Use system Chrome (patchright recommendation)
                **persistent_options
            )
            self.logger.info("[OK] Patchright persistent context launched successfully (using bundled Chromium)")
            print("[SESSION_INIT] ✅ Patchright context created - STEALTH MODE ACTIVE!")
            print("[SESSION_INIT] 🌐 BROWSER WINDOW OPENED - Check for maximized Chrome window!")

            # Listen for new pages and close them immediately to prevent flash
            async def close_popup_pages(new_page):
                """Close popup pages immediately to minimize visual flash"""
                try:
                    # Wait a tiny bit to let the page event fully register
                    await asyncio.sleep(0.05)
                    # Only close if we now have multiple pages (keep the first one)
                    if len(self.context.pages) > 1:
                        self.logger.debug(f"[POPUP_BLOCK] Closing popup tab to prevent flash")
                        await new_page.close()
                except Exception as e:
                    self.logger.debug(f"Error closing popup: {e}")

            self.context.on("page", close_popup_pages)
            print("[SESSION_INIT] 🚫 Popup closer activated - will minimize tab flash")

            # No Browser object with persistent context - it's built-in
            self.browser = None

            # Note: Persistent context automatically loads cookies from user_data_dir
            # No need to manually create context or load cookies!
            self.logger.info(f"[OK] Persistent context will auto-load cookies from: {self.user_data_dir / 'Default' / 'Cookies'}")
            print(f"[SESSION_INIT] 📁 Cookies stored in: {self.user_data_dir / 'Default' / 'Cookies'}")

            # Small wait for browser window to fully initialize
            await asyncio.sleep(1)

            # Create page and navigate to Target.com with timeout
            print("[SESSION_INIT] ⚡ Creating page and navigating to target.com...")
            if self.context:
                pages = self.context.pages
                if not pages:
                    # Create initial page
                    page = await self.context.new_page()
                    print("[SESSION_INIT] ⚡ Page created")
                else:
                    page = pages[0]
                    print(f"[SESSION_INIT] ⚡ Using existing page ({len(pages)} pages)")

                # Window maximization is now handled by --start-maximized flag + no_viewport=True
                # No need for manual CDP window bounds setting
                print("[SESSION_INIT] ⚡ Window maximized via --start-maximized flag")

                # Skip WebAuthn setup - can cause UI blocking issues
                # Target.com works fine without it, will fall back to password auth if needed
                print("[SESSION_INIT] ⚡ Skipping WebAuthn (prevents UI blocking)")

                # Navigate to Target.com with 30s timeout
                # BUGFIX 2.0: Use 'commit' for initial session setup (fastest wait strategy)
                # 'commit' = navigation committed (page starts loading)
                # Faster than 'domcontentloaded' and much faster than 'networkidle'
                try:
                    print("[SESSION_INIT] ⚡ Navigating to target.com (wait for commit)...")
                    await page.goto("https://www.target.com", wait_until='commit', timeout=30000)
                    print("[SESSION_INIT] ✅ Navigation successful!")
                    print(f"[SESSION_INIT] ✅ Browser ready at: {page.url}")

                    # CRITICAL FIX: Restore localStorage after navigation
                    # (Must happen after page loads so localStorage is available)
                    if hasattr(self, '_saved_local_storage') and self._saved_local_storage:
                        try:
                            print("[SESSION_INIT] 🔄 Restoring localStorage...")
                            for origin_data in self._saved_local_storage:
                                origin_url = origin_data.get('origin')
                                local_storage_items = origin_data.get('localStorage', [])

                                if origin_url and 'target.com' in origin_url and local_storage_items:
                                    # Inject localStorage items
                                    for item in local_storage_items:
                                        key = item.get('name')
                                        value = item.get('value')
                                        if key and value:
                                            await page.evaluate(
                                                f"() => {{ localStorage.setItem({json.dumps(key)}, {json.dumps(value)}); }}"
                                            )

                                    self.logger.info(f"[LOCALSTORAGE] ✅ Restored {len(local_storage_items)} localStorage items")
                                    print(f"[SESSION_INIT] ✅ localStorage restored ({len(local_storage_items)} items)")
                        except Exception as ls_error:
                            self.logger.warning(f"[LOCALSTORAGE] Failed to restore (non-fatal): {ls_error}")

                    # Extra wait to ensure rendering completes
                    await asyncio.sleep(2)
                    print("[SESSION_INIT] ✅ Page fully rendered")
                except Exception as nav_error:
                    print(f"[SESSION_INIT] ⚠️ Navigation timed out or failed (non-fatal): {nav_error}")
                    print(f"[SESSION_INIT] Current URL: {page.url}")
                    print("[SESSION_INIT] ⚡ Continuing anyway - browser still functional")
                    # Continue even if navigation fails - browser is still usable

            print("[SESSION_INIT] ⚡ Browser ready at target.com!")
            self.logger.info("[SPEED] Fast initialization complete")

            # LAYER 1: Setup CDP cookie interception (intercepts cookies in real-time)
            print("\n[SESSION_INIT] 🔧 Setting up CDP cookie interception...")
            cdp_setup_success = await self._setup_cdp_cookie_interception()
            if cdp_setup_success:
                print("[SESSION_INIT] ✅ CDP cookie interception ready!")
            else:
                print("[SESSION_INIT] ⚠️ CDP setup failed (non-fatal) - continuing without real-time interception")

            # LAYER 2: Start cookie watchdog (monitors and fixes cookies every 60s)
            print("[SESSION_INIT] 🔧 Starting cookie watchdog...")
            self._start_cookie_watchdog()
            print("[SESSION_INIT] ✅ Cookie watchdog active!")

            # Mark session as active immediately
            self.session_active = True
            self.session_created_at = datetime.now()
            self._initialization_attempts = 0
            self.logger.info("[OK] ⚡ Ultra-fast session initialized with 3-layer cookie protection")
            return True

        except Exception as e:
            self.logger.error(f"[ERROR] Failed to initialize session: {e}")
            await self._safe_cleanup()

            # Retry if we haven't exhausted attempts
            if self._initialization_attempts < self._max_init_attempts:
                self.logger.info("[RETRY] Retrying session initialization...")
                await asyncio.sleep(5)  # Wait longer between full retries
                return await self.initialize()
            else:
                self.logger.error("[CRITICAL] Exhausted all initialization attempts")
                return False

    def submit_async_task(self, coro):
        """
        Thread-safe method to submit async tasks to the main event loop.
        Returns a concurrent.futures.Future that can be waited on from any thread.

        This enables purchase and keepalive threads to safely use Playwright objects
        created in the main event loop, avoiding event loop conflicts.
        """
        if not self._event_loop:
            raise RuntimeError("Event loop not initialized - call initialize() first")

        return asyncio.run_coroutine_threadsafe(coro, self._event_loop)

    async def _create_context_with_retry(self) -> bool:
        """Create context with retry logic"""
        max_attempts = 3

        for attempt in range(max_attempts):
            try:
                await self._create_context()
                self.logger.info(f"[OK] Context created successfully on attempt {attempt + 1}")
                return True
            except Exception as e:
                self.logger.error(f"Context creation attempt {attempt + 1} failed: {e}")
                if attempt < max_attempts - 1:
                    await asyncio.sleep(2)

        return False

    async def _safe_cleanup(self):
        """Safe cleanup that doesn't throw exceptions"""
        try:
            # Stop cookie watchdog
            if self._cookie_watchdog_running:
                self.logger.info("[CLEANUP] Stopping cookie watchdog...")
                self._stop_cookie_watchdog()

            # Close CDP session
            if self._cdp_session:
                try:
                    await self._cdp_session.detach()
                    self._cdp_session = None
                    self.logger.info("[CLEANUP] CDP session closed")
                except:
                    pass

            # Close context (persistent or regular)
            if self.context:
                try:
                    await self.context.close()
                    self.logger.info("[CLEANUP] Context closed")
                except Exception as e:
                    self.logger.warning(f"[CLEANUP] Error closing context: {e}")
                self.context = None

            # Close browser (only exists in non-persistent mode)
            if self.browser:
                try:
                    await self.browser.close()
                    self.logger.info("[CLEANUP] Browser closed")
                except Exception as e:
                    self.logger.warning(f"[CLEANUP] Error closing browser: {e}")
                self.browser = None
            else:
                self.logger.info("[CLEANUP] No browser object (persistent context mode)")

            # Stop playwright
            if self.playwright:
                try:
                    await self.playwright.stop()
                    self.logger.info("[CLEANUP] Playwright stopped")
                except Exception as e:
                    self.logger.warning(f"[CLEANUP] Error stopping playwright: {e}")
                self.playwright = None

        except Exception as e:
            self.logger.warning(f"Cleanup warning (non-fatal): {e}")

        self.session_active = False
        self._cdp_cookie_interception_active = False

    async def _create_context(self):
        """Create or recreate browser context with session state"""
        try:
            # PERSISTENT CONTEXT: With launch_persistent_context(), we don't need this method
            # The context is created in initialize() and persists automatically
            if self.browser is None and self.context is not None:
                self.logger.info("[CONTEXT] Using persistent context - no recreation needed")
                return

            with self._context_lock:
                # Track context recreation
                self._context_recreation_count += 1
                self._last_context_recreation = datetime.now()

                # Close existing context if it exists
                if self.context:
                    try:
                        await self.context.close()
                    except Exception as e:
                        self.logger.warning(f"Error closing old context (non-fatal): {e}")
                    self.context = None

                if not self.browser:
                    # With persistent context, we don't have a browser object
                    self.logger.warning("[CONTEXT] No browser object (persistent context mode) - cannot create new context")
                    return

                # Debug fingerprint data
                self.logger.debug(f"Fingerprint data keys: {list(self.fingerprint_data.keys())}")

                # Create new context with session state
                # F5 BYPASS: Use randomized user-agent for anti-fingerprinting
                # Use no_viewport=True to allow --start-maximized to work properly
                context_options = {
                    'user_agent': self.fingerprint_data.get('user_agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'),
                    'no_viewport': True,  # Allow window to use full screen with --start-maximized
                    'timezone_id': self.fingerprint_data.get('timezone', 'America/New_York'),
                    'locale': self.fingerprint_data.get('locale', 'en-US'),
                    'is_mobile': False,
                    'has_touch': False
                }
                self.logger.info("🎯 Browser window: maximized (no fixed viewport)")

                # CRITICAL FIX: Load cookies and localStorage manually (don't use storage_state parameter)
                # Playwright bug: storage_state parameter doesn't reliably load cookies
                # Workaround from GitHub Issue #2529: Use add_cookies() after context creation
                session_data_to_restore = None
                if self.session_path.exists():
                    try:
                        # Validate and load session file
                        with open(self.session_path, 'r') as f:
                            session_data_to_restore = json.load(f)

                        # COOKIE VERIFICATION: Check for critical auth cookies in file
                        cookies = session_data_to_restore.get('cookies', [])
                        target_cookies = [c for c in cookies if 'target.com' in c.get('domain', '')]
                        critical_cookies = ['login-session', 'accessToken', 'idToken', 'refreshToken']

                        self.logger.info(f"[COOKIE_DEBUG] [LOAD] Loading {len(cookies)} total cookies from file, {len(target_cookies)} for Target.com")

                        found_cookies = []
                        for cookie in target_cookies:
                            if cookie['name'] in critical_cookies:
                                found_cookies.append(cookie['name'])
                                # Log expiration info with full details
                                expires = cookie.get('expires', -1)
                                if expires == -1:
                                    expires_str = "SESSION (will be lost on restart!)"
                                    self.logger.warning(f"[COOKIE_DEBUG] [LOAD] ⚠️  '{cookie['name']}' is a SESSION cookie - this is the problem!")
                                else:
                                    import time
                                    time_left = expires - time.time()
                                    days_left = time_left / (24 * 60 * 60)
                                    expires_str = f"{days_left:.1f} days"
                                    if days_left < 0:
                                        self.logger.warning(f"[COOKIE_DEBUG] [LOAD] ⚠️  '{cookie['name']}' EXPIRED {abs(days_left):.1f} days ago!")
                                    else:
                                        self.logger.info(f"[COOKIE_DEBUG] [LOAD] ✓ '{cookie['name']}' expires in {days_left:.1f} days")

                                self.logger.info(f"[COOKIE_DEBUG] [LOAD] '{cookie['name']}':")
                                self.logger.info(f"[COOKIE_DEBUG] [LOAD]    - domain: {cookie.get('domain')}")
                                self.logger.info(f"[COOKIE_DEBUG] [LOAD]    - expires: {expires_str}")
                                self.logger.info(f"[COOKIE_DEBUG] [LOAD]    - httpOnly: {cookie.get('httpOnly', False)}")
                                self.logger.info(f"[COOKIE_DEBUG] [LOAD]    - secure: {cookie.get('secure', False)}")
                                self.logger.info(f"[COOKIE_DEBUG] [LOAD]    - sameSite: {cookie.get('sameSite', 'None')}")

                        missing_cookies = set(critical_cookies) - set(found_cookies)
                        if missing_cookies:
                            self.logger.warning(f"[COOKIE_DEBUG] [LOAD] ⚠️  Missing auth cookies in file: {missing_cookies}")
                            self.logger.warning(f"[COOKIE_DEBUG] [LOAD] This means you'll need to log in again!")
                        else:
                            self.logger.info(f"[COOKIE_DEBUG] [LOAD] ✅ All critical auth cookies present in file: {found_cookies}")

                        # Store sessionStorage for restoration after context creation
                        self._session_storage = session_data_to_restore.get('sessionStorage', None)
                        if self._session_storage:
                            self.logger.info(f"[OK] Found sessionStorage in session file ({len(self._session_storage)} bytes)")
                            print(f"[SESSION] Will restore sessionStorage from file")

                        # Store localStorage for restoration after page navigation
                        self._saved_local_storage = session_data_to_restore.get('origins', [])
                        if self._saved_local_storage:
                            total_items = sum(len(o.get('localStorage', [])) for o in self._saved_local_storage)
                            self.logger.info(f"[OK] Found localStorage in session file ({total_items} items across {len(self._saved_local_storage)} origins)")
                            print(f"[SESSION] Will restore localStorage from file ({total_items} items)")

                        self.logger.info(f"[OK] Session file loaded from {self.session_path}")
                    except Exception as e:
                        self.logger.warning(f"Invalid session file, starting fresh: {e}")
                        session_data_to_restore = None

                print(f"[DEBUG_FLASH] [INIT] Creating new browser context WITHOUT storage_state (recreation #{self._context_recreation_count})")
                # CRITICAL: Create context WITHOUT storage_state parameter
                # We'll inject cookies manually after creation (fixes Playwright bug)
                self.context = await self.browser.new_context(**context_options)
                print(f"[DEBUG_FLASH] [OK] Browser context created - now injecting cookies manually...")

                # CRITICAL FIX: Manually inject cookies using add_cookies() after context creation
                # This is the workaround for Playwright bug where storage_state doesn't work
                if session_data_to_restore:
                    try:
                        cookies = session_data_to_restore.get('cookies', [])
                        if cookies:
                            self.logger.info(f"[COOKIE_INJECT] Manually injecting {len(cookies)} cookies...")
                            await self.context.add_cookies(cookies)
                            self.logger.info(f"[COOKIE_INJECT] ✅ Successfully injected {len(cookies)} cookies!")
                            print(f"[SESSION] ✅ Cookies injected manually (workaround for Playwright bug)")

                            # ENHANCED: Verify injection worked - check Target.com domain specifically
                            loaded_cookies_all = await self.context.cookies()
                            loaded_cookies_target = await self.context.cookies("https://www.target.com")

                            self.logger.info(f"[COOKIE_VERIFY] Context now has {len(loaded_cookies_all)} total cookies")
                            self.logger.info(f"[COOKIE_VERIFY] Target.com domain has {len(loaded_cookies_target)} cookies")

                            # Verify critical cookies are present for Target.com specifically
                            critical_cookies = ['login-session', 'accessToken', 'idToken', 'refreshToken']
                            found_in_context = []
                            found_details = {}

                            for loaded_cookie in loaded_cookies_target:
                                if loaded_cookie['name'] in critical_cookies:
                                    found_in_context.append(loaded_cookie['name'])
                                    # Log details for verification
                                    expires = loaded_cookie.get('expires', -1)
                                    if expires == -1:
                                        expires_str = "session"
                                    else:
                                        import time
                                        days_left = (expires - time.time()) / (24 * 60 * 60)
                                        expires_str = f"{days_left:.1f} days"
                                    found_details[loaded_cookie['name']] = {
                                        'domain': loaded_cookie.get('domain'),
                                        'expires': expires_str,
                                        'sameSite': loaded_cookie.get('sameSite', 'None')
                                    }

                            if found_in_context:
                                self.logger.info(f"[COOKIE_VERIFY] ✅ Critical auth cookies loaded for Target.com: {found_in_context}")
                                for cookie_name, details in found_details.items():
                                    self.logger.info(f"[COOKIE_VERIFY]    - {cookie_name}: domain={details['domain']}, expires={details['expires']}, sameSite={details['sameSite']}")
                            else:
                                self.logger.warning(f"[COOKIE_VERIFY] ⚠️  No critical auth cookies found for Target.com after injection!")
                                self.logger.warning(f"[COOKIE_VERIFY] This may cause login persistence issues")

                        # Restore localStorage separately (if needed in the future)
                        origins = session_data_to_restore.get('origins', [])
                        if origins:
                            self.logger.debug(f"[LOCALSTORAGE] Found {len(origins)} origins with localStorage")
                            # Note: localStorage will be restored after page navigation

                    except Exception as e:
                        self.logger.error(f"[ERROR] Failed to inject cookies: {e}")
                        import traceback
                        traceback.print_exc()

                print(f"[DEBUG_FLASH] [OK] Context setup complete - checking for initial pages...")

                # Check if context auto-created any pages
                initial_pages = self.context.pages
                print(f"[DEBUG_FLASH] Context has {len(initial_pages)} pages after creation")

                # WebAuthn setup will be done after main page creation to avoid tab flash

                # NOTE: Stealth mode is handled automatically by patchright!
                # Patchright patches playwright at source level to:
                # - Hide navigator.webdriver
                # - Remove --enable-automation flag
                # - Patch CDP leaks
                # - Fix all common automation detection fingerprints
                # No manual JavaScript injection needed!

                self.logger.info(f"[OK] Browser context created successfully (recreation #{self._context_recreation_count})")

        except Exception as e:
            self.logger.error(f"[ERROR] Failed to create context: {e}")
            raise

    async def _restore_session_storage(self):
        """
        Restore sessionStorage for Target.com checkout
        This is CRITICAL for maintaining auth tokens during checkout flow
        """
        try:
            # Restore sessionStorage if available (Target.com checkout auth)
            if self._session_storage:
                self.logger.info("[SESSION] Restoring sessionStorage for target.com checkout")
                print(f"[SESSION] Adding sessionStorage restore script")
                try:
                    # Embed sessionStorage JSON directly in script using string interpolation
                    # This is necessary because add_init_script() doesn't accept a second parameter
                    import json
                    session_storage_escaped = json.dumps(self._session_storage)

                    # IMPROVED: Runs on EVERY page load before any other scripts (via add_init_script)
                    # This ensures sessionStorage is always available, even after navigation
                    await self.context.add_init_script(f"""
                        (function() {{
                            // Only restore on target.com to avoid contaminating other sites
                            if (window.location.hostname.includes('target.com')) {{
                                try {{
                                    const sessionStorageJSON = {session_storage_escaped};
                                    const sessionData = JSON.parse(sessionStorageJSON);
                                    let restoredCount = 0;
                                    for (const [key, value] of Object.entries(sessionData)) {{
                                        window.sessionStorage.setItem(key, value);
                                        restoredCount++;
                                    }}
                                    console.log(`[SessionManager] ✓ Restored ${{restoredCount}} sessionStorage items for target.com`);
                                    console.log('[SessionManager] SessionStorage keys:', Object.keys(sessionStorage));
                                }} catch (e) {{
                                    console.error('[SessionManager] ❌ Failed to restore sessionStorage:', e);
                                }}
                            }} else {{
                                console.log('[SessionManager] Skipping sessionStorage restore (not on target.com)');
                            }}
                        }})();
                    """)
                    self.logger.info("[OK] ✓ SessionStorage restore script added (runs on every page load)")
                except Exception as e:
                    self.logger.warning(f"Could not add sessionStorage restore script (non-fatal): {e}")

            self.logger.debug("🥷 SessionStorage restoration configured for Target.com checkout")

        except Exception as e:
            self.logger.warning(f"Failed to restore sessionStorage: {e}")

    async def _setup_cdp_cookie_interception(self):
        """
        LAYER 1: CDP Real-Time Cookie Interception
        Intercepts cookies as Target.com sets them and converts session cookies to persistent
        """
        try:
            if not self.context:
                self.logger.error("[CDP_COOKIE] Cannot setup CDP - no context available")
                return False

            # Get the first page (or create one if none exist)
            pages = self.context.pages
            if not pages:
                self.logger.warning("[CDP_COOKIE] No pages available, creating one for CDP session")
                page = await self.context.new_page()
            else:
                page = pages[0]

            # Create CDP session (only works on Chromium)
            self.logger.info("[CDP_COOKIE] Creating CDP session for cookie interception...")
            self._cdp_session = await self.context.new_cdp_session(page)

            # Enable Network domain to receive cookie events
            await self._cdp_session.send('Network.enable')
            self.logger.info("[CDP_COOKIE] ✅ Network domain enabled")

            # Listen for response received events (when Set-Cookie headers arrive)
            self._cdp_session.on('Network.responseReceived', lambda params: asyncio.create_task(self._handle_response_received(params)))

            self._cdp_cookie_interception_active = True
            self.logger.info("[CDP_COOKIE] ✅ Real-time cookie interception ACTIVE")
            print("[CDP_COOKIE] ✅ Cookie interception system activated!")

            return True

        except Exception as e:
            self.logger.error(f"[CDP_COOKIE] Failed to setup cookie interception: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def _handle_response_received(self, params):
        """Handle Network.responseReceived events to intercept Set-Cookie headers"""
        try:
            response = params.get('response', {})
            url = response.get('url', '')
            headers = response.get('headers', {})

            # Only process Target.com responses
            if 'target.com' not in url:
                return

            # Check for Set-Cookie header (case-insensitive)
            set_cookie_header = None
            for header_name, header_value in headers.items():
                if header_name.lower() == 'set-cookie':
                    set_cookie_header = header_value
                    break

            if not set_cookie_header:
                return

            # Parse cookies from Set-Cookie header
            # Format: "name=value; expires=...; domain=...; path=...; HttpOnly; Secure; SameSite=..."
            cookies_to_fix = []

            # Set-Cookie can be a string or list
            cookie_strings = [set_cookie_header] if isinstance(set_cookie_header, str) else set_cookie_header

            for cookie_str in cookie_strings:
                # Parse cookie name and check if it's a critical auth cookie
                cookie_parts = cookie_str.split(';')
                if not cookie_parts:
                    continue

                name_value = cookie_parts[0].strip()
                if '=' not in name_value:
                    continue

                cookie_name = name_value.split('=')[0].strip()

                # Check if this is a critical auth cookie
                critical_cookies = ['accessToken', 'idToken', 'refreshToken', 'login-session']
                if cookie_name not in critical_cookies:
                    continue

                # Check if cookie has expires attribute
                has_expires = any('expires' in part.lower() or 'max-age' in part.lower() for part in cookie_parts[1:])

                if not has_expires:
                    # This is a SESSION cookie - we need to fix it!
                    self.logger.warning(f"[CDP_COOKIE] ⚠️  Intercepted SESSION cookie: '{cookie_name}' from {url}")
                    self.logger.warning(f"[CDP_COOKIE] ⚠️  This cookie would be lost on browser close!")

                    # Parse full cookie details
                    cookie_data = self._parse_set_cookie_header(cookie_str, url)
                    if cookie_data:
                        cookies_to_fix.append(cookie_data)

            # Fix intercepted session cookies by re-setting them with expires
            if cookies_to_fix:
                await self._inject_persistent_cookies_via_cdp(cookies_to_fix)

        except Exception as e:
            self.logger.error(f"[CDP_COOKIE] Error handling response: {e}")

    def _parse_set_cookie_header(self, cookie_str: str, url: str) -> Optional[Dict[str, Any]]:
        """Parse Set-Cookie header string into cookie dict for CDP Network.setCookie"""
        try:
            parts = [p.strip() for p in cookie_str.split(';')]

            # First part is name=value
            name_value = parts[0]
            if '=' not in name_value:
                return None

            name, value = name_value.split('=', 1)

            # Parse domain from URL if not in cookie
            from urllib.parse import urlparse
            parsed_url = urlparse(url)
            domain = parsed_url.hostname

            # Parse other attributes
            cookie_data = {
                'name': name.strip(),
                'value': value.strip(),
                'domain': domain,
                'path': '/',
                'httpOnly': False,
                'secure': False,
                'sameSite': 'Lax'
            }

            for part in parts[1:]:
                part_lower = part.lower()
                if 'domain' in part_lower and '=' in part:
                    cookie_data['domain'] = part.split('=')[1].strip()
                elif 'path' in part_lower and '=' in part:
                    cookie_data['path'] = part.split('=')[1].strip()
                elif 'httponly' in part_lower:
                    cookie_data['httpOnly'] = True
                elif 'secure' in part_lower:
                    cookie_data['secure'] = True
                elif 'samesite' in part_lower and '=' in part:
                    cookie_data['sameSite'] = part.split('=')[1].strip().capitalize()

            return cookie_data

        except Exception as e:
            self.logger.error(f"[CDP_COOKIE] Error parsing cookie: {e}")
            return None

    async def _inject_persistent_cookies_via_cdp(self, cookies: list):
        """Inject cookies with persistent expires using CDP Network.setCookie"""
        try:
            if not self._cdp_session:
                self.logger.error("[CDP_COOKIE] No CDP session available for cookie injection")
                return

            import time
            # Set expiration to 30 days from now
            future_timestamp = time.time() + (30 * 24 * 60 * 60)

            for cookie in cookies:
                # Add expires timestamp
                cookie['expires'] = future_timestamp

                # Use CDP to set the cookie with persistent expires
                self.logger.info(f"[CDP_COOKIE] 🔧 FIXING cookie '{cookie['name']}' - converting to PERSISTENT (30 days)")

                await self._cdp_session.send('Network.setCookie', cookie)

                self.logger.info(f"[CDP_COOKIE] ✅ Cookie '{cookie['name']}' now PERSISTENT - will survive browser restart!")
                print(f"[CDP_COOKIE] ✅ Fixed '{cookie['name']}' - now persistent for 30 days")

        except Exception as e:
            self.logger.error(f"[CDP_COOKIE] Error injecting cookies: {e}")
            import traceback
            traceback.print_exc()

    def _start_cookie_watchdog(self):
        """
        LAYER 2: Cookie Watchdog
        Starts a background task that monitors cookies every 60 seconds and re-injects persistent versions
        """
        try:
            if self._cookie_watchdog_running:
                self.logger.warning("[WATCHDOG] Cookie watchdog already running")
                return

            self._cookie_watchdog_running = True

            # Start the watchdog as an async task
            if self._event_loop:
                self._cookie_watchdog_task = asyncio.run_coroutine_threadsafe(
                    self._cookie_watchdog_loop(),
                    self._event_loop
                )
                self.logger.info("[WATCHDOG] ✅ Cookie watchdog started successfully")
            else:
                self.logger.error("[WATCHDOG] Cannot start watchdog - no event loop available")

        except Exception as e:
            self.logger.error(f"[WATCHDOG] Failed to start cookie watchdog: {e}")

    async def _cookie_watchdog_loop(self):
        """Background loop that monitors and fixes cookies every 60 seconds"""
        self.logger.info("[WATCHDOG] Cookie watchdog loop started")

        while self._cookie_watchdog_running and self.session_active:
            try:
                await asyncio.sleep(60)  # Check every 60 seconds

                if not self._cookie_watchdog_running or not self.session_active:
                    break

                self.logger.info("[WATCHDOG] 🔍 Checking cookies...")

                # Check if running in TEST_MODE to suppress noisy warnings
                import os
                test_mode = os.environ.get('TEST_MODE', 'false').lower() == 'true'

                # Get current cookies from context
                if not self.context:
                    self.logger.warning("[WATCHDOG] No context available, skipping check")
                    continue

                target_cookies = await self.context.cookies("https://www.target.com")
                self.logger.info(f"[WATCHDOG] Found {len(target_cookies)} Target.com cookies")

                # Check critical auth cookies
                critical_cookies = ['accessToken', 'idToken', 'refreshToken', 'login-session']
                found_cookies = {}
                missing_cookies = []
                session_cookies = []

                for cookie_name in critical_cookies:
                    found = False
                    for cookie in target_cookies:
                        if cookie['name'] == cookie_name:
                            found = True
                            found_cookies[cookie_name] = cookie

                            # Check if it's a session cookie
                            expires = cookie.get('expires', -1)
                            if expires == -1:
                                session_cookies.append(cookie_name)
                                if test_mode:
                                    self.logger.debug(f"[WATCHDOG] '{cookie_name}' is SESSION cookie (suppressed in TEST_MODE)")
                                else:
                                    self.logger.warning(f"[WATCHDOG] ⚠️  '{cookie_name}' is SESSION cookie - needs fixing!")
                            else:
                                import time
                                days_left = (expires - time.time()) / (24 * 60 * 60)
                                if days_left < 0:
                                    self.logger.warning(f"[WATCHDOG] ⚠️  '{cookie_name}' EXPIRED {abs(days_left):.1f} days ago!")
                                    missing_cookies.append(cookie_name)
                                else:
                                    self.logger.info(f"[WATCHDOG] ✓ '{cookie_name}' OK ({days_left:.1f} days left)")
                            break

                    if not found:
                        missing_cookies.append(cookie_name)
                        if test_mode:
                            self.logger.debug(f"[WATCHDOG] '{cookie_name}' MISSING (suppressed in TEST_MODE)")
                        else:
                            self.logger.warning(f"[WATCHDOG] ⚠️  '{cookie_name}' MISSING!")

                # Fix session cookies by re-injecting with persistent expires
                if session_cookies:
                    self.logger.warning(f"[WATCHDOG] Found {len(session_cookies)} session cookies to fix: {session_cookies}")
                    await self._fix_session_cookies(found_cookies, session_cookies)

                # If critical cookies are missing, try to restore from file
                if missing_cookies:
                    if test_mode:
                        self.logger.debug(f"[WATCHDOG] Missing critical cookies (suppressed in TEST_MODE): {missing_cookies}")
                    else:
                        self.logger.warning(f"[WATCHDOG] Missing critical cookies: {missing_cookies}")
                    await self._restore_cookies_from_file(missing_cookies)

                if not session_cookies and not missing_cookies:
                    self.logger.info("[WATCHDOG] ✅ All cookies healthy!")

            except Exception as e:
                self.logger.error(f"[WATCHDOG] Error in watchdog loop: {e}")
                import traceback
                traceback.print_exc()

        self.logger.info("[WATCHDOG] Cookie watchdog loop ended")

    async def _fix_session_cookies(self, found_cookies: dict, session_cookie_names: list):
        """Fix session cookies by re-injecting with persistent expires"""
        try:
            import time
            future_timestamp = time.time() + (30 * 24 * 60 * 60)

            for cookie_name in session_cookie_names:
                if cookie_name not in found_cookies:
                    continue

                cookie = found_cookies[cookie_name].copy()
                cookie['expires'] = future_timestamp

                # If we have CDP session, use it (more reliable)
                if self._cdp_session:
                    self.logger.info(f"[WATCHDOG] 🔧 Fixing '{cookie_name}' via CDP (converting to persistent)")
                    await self._cdp_session.send('Network.setCookie', cookie)
                    self.logger.info(f"[WATCHDOG] ✅ '{cookie_name}' fixed via CDP")
                else:
                    # Fallback: use context.add_cookies
                    self.logger.info(f"[WATCHDOG] 🔧 Fixing '{cookie_name}' via context.add_cookies")
                    await self.context.add_cookies([cookie])
                    self.logger.info(f"[WATCHDOG] ✅ '{cookie_name}' fixed via context")

                print(f"[WATCHDOG] ✅ Fixed '{cookie_name}' - now persistent for 30 days")

        except Exception as e:
            self.logger.error(f"[WATCHDOG] Error fixing session cookies: {e}")

    async def _restore_cookies_from_file(self, missing_cookie_names: list):
        """Restore missing cookies from saved session file"""
        try:
            if not self.session_path.exists():
                self.logger.warning("[WATCHDOG] No session file to restore cookies from")
                return

            # Load cookies from file
            with open(self.session_path, 'r') as f:
                import json
                session_data = json.load(f)
                saved_cookies = session_data.get('cookies', [])

            # Find and restore missing cookies
            cookies_to_restore = []
            for cookie in saved_cookies:
                if cookie['name'] in missing_cookie_names and 'target.com' in cookie.get('domain', ''):
                    cookies_to_restore.append(cookie)

            if cookies_to_restore:
                self.logger.info(f"[WATCHDOG] 🔄 Restoring {len(cookies_to_restore)} cookies from file")

                # Use CDP if available, otherwise fallback to context
                for cookie in cookies_to_restore:
                    # Ensure cookie is persistent
                    import time
                    if cookie.get('expires', -1) == -1:
                        cookie['expires'] = time.time() + (30 * 24 * 60 * 60)

                    if self._cdp_session:
                        await self._cdp_session.send('Network.setCookie', cookie)
                    else:
                        await self.context.add_cookies([cookie])

                    self.logger.info(f"[WATCHDOG] ✅ Restored '{cookie['name']}' from file")
                    print(f"[WATCHDOG] ✅ Restored '{cookie['name']}' from saved session")
            else:
                # Check if running in TEST_MODE to suppress noisy warnings
                import os
                test_mode = os.environ.get('TEST_MODE', 'false').lower() == 'true'
                if test_mode:
                    self.logger.debug(f"[WATCHDOG] Could not find cookies to restore in file (suppressed in TEST_MODE): {missing_cookie_names}")
                else:
                    self.logger.warning(f"[WATCHDOG] Could not find cookies to restore in file: {missing_cookie_names}")

        except Exception as e:
            self.logger.error(f"[WATCHDOG] Error restoring cookies from file: {e}")

    def _stop_cookie_watchdog(self):
        """Stop the cookie watchdog"""
        self._cookie_watchdog_running = False
        if self._cookie_watchdog_task:
            try:
                self._cookie_watchdog_task.cancel()
            except:
                pass
        self.logger.info("[WATCHDOG] Cookie watchdog stopped")

    async def human_click(self, page: Page, selector: str, timeout: int = 5000) -> bool:
        """
        F5 BYPASS: Click element with human-like Bézier curve mouse movement

        Args:
            page: Playwright page object
            selector: Element selector to click
            timeout: Timeout in milliseconds

        Returns:
            True if click successful, False otherwise
        """
        try:
            # Wait for element
            element = await page.wait_for_selector(selector, timeout=timeout)
            if not element:
                return False

            # Get element position
            box = await element.bounding_box()
            if not box:
                # Element not visible, use direct click as fallback
                await element.click()
                return True

            # Calculate click point (random position within element for realism)
            target_x = box['x'] + (box['width'] * random.uniform(0.3, 0.7))
            target_y = box['y'] + (box['height'] * random.uniform(0.3, 0.7))

            # Generate Bézier curve from current position to target
            # Use quadratic Bézier with one control point for natural curve
            current_pos = await page.evaluate("() => ({ x: window.lastMouseX || 0, y: window.lastMouseY || 0 })")
            start_x = current_pos['x'] if current_pos['x'] > 0 else random.randint(100, 500)
            start_y = current_pos['y'] if current_pos['y'] > 0 else random.randint(100, 500)

            # Control point creates the curve (offset from midpoint)
            mid_x = (start_x + target_x) / 2
            mid_y = (start_y + target_y) / 2
            control_x = mid_x + random.uniform(-100, 100)
            control_y = mid_y + random.uniform(-100, 100)

            # Generate points along Bézier curve
            steps = random.randint(10, 20)  # Random steps for varied movement
            for i in range(steps + 1):
                t = i / steps
                # Quadratic Bézier formula: B(t) = (1-t)²P0 + 2(1-t)tP1 + t²P2
                x = (1-t)**2 * start_x + 2*(1-t)*t * control_x + t**2 * target_x
                y = (1-t)**2 * start_y + 2*(1-t)*t * control_y + t**2 * target_y

                # Move mouse with slight random delay between steps
                await page.mouse.move(x, y)
                if i < steps:  # Don't delay after last step
                    await asyncio.sleep(random.uniform(0.001, 0.005))

            # Store position for next movement
            await page.evaluate(f"() => {{ window.lastMouseX = {target_x}; window.lastMouseY = {target_y}; }}")

            # Human-like pause before click
            await asyncio.sleep(random.uniform(0.05, 0.15))

            # Click at final position
            await page.mouse.click(target_x, target_y)

            # Human-like pause after click
            await asyncio.sleep(random.uniform(0.1, 0.3))

            return True

        except Exception as e:
            self.logger.warning(f"Human click failed, using fallback: {e}")
            # Fallback to direct click if Bézier movement fails
            try:
                element = await page.wait_for_selector(selector, timeout=timeout)
                if element:
                    await element.click()
                    return True
            except:
                pass
            return False

    async def simulate_human_reading(self, page: Page, duration_seconds: float = None) -> bool:
        """
        F5 BYPASS: Simulate human-like page reading behavior

        Performs random mouse movements and scrolling to defeat F5's behavioral analysis

        Args:
            page: Playwright page object
            duration_seconds: Total duration (random 1-3s if not specified)

        Returns:
            True if successful
        """
        try:
            if duration_seconds is None:
                duration_seconds = random.uniform(1.0, 3.0)

            # Get page dimensions
            dimensions = await page.evaluate("""
                () => ({
                    width: window.innerWidth,
                    height: window.innerHeight,
                    scrollHeight: document.documentElement.scrollHeight
                })
            """)

            width = dimensions['width']
            height = dimensions['height']
            scroll_height = dimensions['scrollHeight']

            # Number of random mouse movements (3-7)
            num_movements = random.randint(3, 7)
            time_per_movement = duration_seconds / num_movements

            for i in range(num_movements):
                # Random position on visible viewport
                target_x = random.randint(int(width * 0.2), int(width * 0.8))
                target_y = random.randint(int(height * 0.2), int(height * 0.8))

                # Get current mouse position
                current_pos = await page.evaluate("() => ({ x: window.lastMouseX || window.innerWidth/2, y: window.lastMouseY || window.innerHeight/2 })")
                start_x = current_pos['x']
                start_y = current_pos['y']

                # Bézier curve movement
                mid_x = (start_x + target_x) / 2
                mid_y = (start_y + target_y) / 2
                control_x = mid_x + random.uniform(-50, 50)
                control_y = mid_y + random.uniform(-50, 50)

                steps = random.randint(8, 15)
                for j in range(steps + 1):
                    t = j / steps
                    x = (1-t)**2 * start_x + 2*(1-t)*t * control_x + t**2 * target_x
                    y = (1-t)**2 * start_y + 2*(1-t)*t * control_y + t**2 * target_y
                    await page.mouse.move(x, y)
                    if j < steps:
                        await asyncio.sleep(0.002)

                # Store position
                await page.evaluate(f"() => {{ window.lastMouseX = {target_x}; window.lastMouseY = {target_y}; }}")

                # Random pause to simulate reading
                await asyncio.sleep(random.uniform(time_per_movement * 0.5, time_per_movement * 1.5))

                # Occasionally scroll (30% chance per movement)
                if random.random() < 0.3 and scroll_height > height:
                    # Smooth scroll with easing
                    current_scroll = await page.evaluate("() => window.scrollY")
                    max_scroll = scroll_height - height

                    # Scroll down or up randomly
                    if current_scroll < max_scroll * 0.8:
                        # Scroll down
                        target_scroll = min(current_scroll + random.randint(100, 300), max_scroll)
                    else:
                        # Scroll up if near bottom
                        target_scroll = max(current_scroll - random.randint(100, 200), 0)

                    # Smooth scroll with multiple steps
                    scroll_steps = random.randint(10, 20)
                    for k in range(scroll_steps + 1):
                        # Ease-out curve for natural scroll feel
                        progress = k / scroll_steps
                        eased = 1 - (1 - progress) ** 3
                        scroll_pos = current_scroll + (target_scroll - current_scroll) * eased
                        await page.evaluate(f"() => window.scrollTo(0, {scroll_pos})")
                        await asyncio.sleep(0.01)

            return True

        except Exception as e:
            self.logger.warning(f"Simulate human reading failed (non-fatal): {e}")
            return False

    async def get_page(self) -> Optional[Page]:
        """Get a page from the persistent context - bulletproof with auto-recovery"""
        max_attempts = 3
        print(f"[DEBUG_FLASH] [DEBUG] get_page() called - checking for pages...")

        for attempt in range(max_attempts):
            try:
                with self._context_lock:
                    # BULLETPROOF CHECK 1: Ensure context exists and is valid
                    # Note: self.browser is None with persistent context (this is normal)
                    if not self.context:
                        print(f"[DEBUG_FLASH] [WARNING] Context invalid on attempt {attempt + 1}, recreating context...")
                        self.logger.warning(f"Context invalid on attempt {attempt + 1}, recreating...")
                        await self._recreate_context_if_needed()

                    # BULLETPROOF CHECK 2: Test context health
                    if self.context and not await self._test_context_health():
                        print(f"[DEBUG_FLASH] [WARNING] Context unhealthy on attempt {attempt + 1}, recreating context...")
                        self.logger.warning(f"Context unhealthy on attempt {attempt + 1}, recreating...")
                        await self._recreate_context_if_needed()

                    # BULLETPROOF CHECK 3: Get or create page
                    if self.context:
                        pages = self.context.pages
                        print(f"[DEBUG_FLASH] Context has {len(pages)} pages available")

                        # Page cleanup disabled - popup handler should prevent accumulation
                        # (Re-enable if pages still accumulate over time)
                        # if len(pages) > 2:
                        #     for old_page in pages[1:]:
                        #         try:
                        #             await old_page.close()
                        #         except Exception as e:
                        #             self.logger.debug(f"Error closing stale page: {e}")
                        #     print(f"[DEBUG_FLASH] Closed {len(pages) - 1} stale pages")

                        if pages:
                            page = pages[0]
                            print(f"[DEBUG_FLASH] [OK] Using existing page[0] - NO NEW TAB")
                        else:
                            print(f"[DEBUG_FLASH] [WARNING] NO PAGES EXIST - CREATING NEW PAGE - THIS WILL FLASH!")
                            page = await self.context.new_page()
                            print(f"[DEBUG_FLASH] [OK] New page created in get_page()")

                        # Stealth already applied via context.add_init_script - no need to reapply

                        # BULLETPROOF CHECK 4: Test page validity
                        if page and await self._test_page_health(page):
                            self.logger.debug(f"Successfully got healthy page on attempt {attempt + 1}")
                            print(f"[DEBUG_FLASH] [OK] Returning healthy page from get_page()")
                            return page
                        else:
                            print(f"[DEBUG_FLASH] [ERROR] Page unhealthy on attempt {attempt + 1}")
                            self.logger.warning(f"Page unhealthy on attempt {attempt + 1}")

            except Exception as e:
                self.logger.error(f"Get page attempt {attempt + 1} failed: {e}")

            # Wait briefly before retry
            if attempt < max_attempts - 1:
                await asyncio.sleep(1)

        # BULLETPROOF FALLBACK: If all attempts failed, return None but log clearly
        self.logger.error("[CRITICAL] Failed to get healthy page after all attempts - session system needs restart")
        return None

    async def _recreate_context_if_needed(self):
        """Recreate context if it's broken - internal helper"""
        try:
            print(f"[DEBUG_FLASH] [RETRY] _recreate_context_if_needed() called")

            # With persistent context, we can't recreate it (it's tied to playwright launch)
            # The context should persist automatically
            if self.browser is None and self.context:
                print(f"[DEBUG_FLASH] [INFO] Using persistent context - cannot recreate dynamically")
                self.logger.info("Persistent context mode - context recreation not supported")
                return

            if self.browser:
                await self._create_context()
                print(f"[DEBUG_FLASH] [OK] Context recreated in _recreate_context_if_needed()")
            else:
                print(f"[DEBUG_FLASH] [ERROR] Cannot recreate context - no browser or context available")
                self.logger.error("Cannot recreate context - no browser or context available")
        except Exception as e:
            print(f"[DEBUG_FLASH] [ERROR] Failed to recreate context: {e}")
            self.logger.error(f"Failed to recreate context: {e}")

    async def _test_context_health(self) -> bool:
        """Test if context is healthy - internal helper"""
        try:
            if not self.context:
                return False
            # Try a simple operation that would fail if context is broken
            await self.context.cookies()
            return True
        except Exception:
            return False

    async def _test_page_health(self, page: Page) -> bool:
        """Test if page is healthy - internal helper"""
        try:
            if not page:
                return False
            # Try a simple operation that would fail if page is broken
            # Much more lenient - just check if page object is valid
            # Don't fail if evaluate times out - page might just be slow
            try:
                await page.evaluate("() => true", timeout=2000)
                return True
            except:
                # If evaluate fails, try a simpler check - does the page have a URL?
                if page.url:
                    return True  # Page has a URL, it's probably healthy enough
                return False
        except Exception:
            return False

    async def _dismiss_popups(self, page) -> bool:
        """Dismiss any popup overlays (passkey prompts, save password, notifications, etc.)"""
        try:
            # Common popup dismissal selectors for Target.com
            popup_selectors = [
                # Passkey/security prompts
                'button:has-text("Cancel")',
                'button:has-text("Skip")',
                'button:has-text("Skip for now")',
                'button:has-text("Not now")',
                'button:has-text("Maybe later")',
                'button:has-text("No thanks")',

                # Generic close buttons
                '[aria-label*="Close"]',
                '[aria-label*="Dismiss"]',
                '[data-test*="close"]',
                'button[class*="close"]',

                # Browser save password prompts (these are in browser chrome, not page)
                # Can't dismiss these via Playwright, but they don't block
            ]

            dismissed_count = 0
            for selector in popup_selectors:
                try:
                    # Quick check if element exists and is visible
                    element = await page.query_selector(selector)
                    if element and await element.is_visible():
                        self.logger.info(f"Found popup: {selector}, dismissing...")

                        # Human-like delay before clicking
                        await asyncio.sleep(random.uniform(0.3, 0.8))
                        await element.click()
                        dismissed_count += 1

                        # Wait for popup to disappear
                        await asyncio.sleep(random.uniform(0.5, 1.0))

                except Exception:
                    continue  # Element not found or not clickable

            if dismissed_count > 0:
                self.logger.info(f"[OK] Dismissed {dismissed_count} popup(s)")

            return True

        except Exception as e:
            self.logger.warning(f"Popup dismissal error (non-fatal): {e}")
            return False

    async def _trigger_token_refresh(self) -> bool:
        """Trigger Target's auto-refresh flow using refreshToken - preserves session cookies"""
        try:
            self.logger.info("[RETRY] Triggering token refresh flow (preserves context)...")

            # Get current page without recreating context
            page = await self.get_page()
            if not page:
                self.logger.error("Cannot trigger token refresh - no page available")
                return False

            # Navigate to account page - Target's backend will see refreshToken and auto-refresh
            self.logger.info("Navigating to account page to trigger auto-refresh...")
            try:
                await page.goto("https://www.target.com/account",
                              wait_until='commit',
                              timeout=30000)
            except Exception as goto_error:
                # Fallback: Try with load if commit fails
                self.logger.warning(f"Token refresh navigation with 'commit' failed, trying 'load': {goto_error}")
                await page.goto("https://www.target.com/account",
                              wait_until='load',
                              timeout=30000)

            # Wait for page to load and tokens to refresh
            await asyncio.sleep(3)

            # Check if we're redirected to login (means refreshToken expired)
            current_url = page.url
            if 'login' in current_url.lower() or 'signin' in current_url.lower():
                self.logger.error("[ERROR] Token refresh failed - redirected to login (refreshToken expired)")
                return False

            # Check for account indicators (successful refresh)
            account_indicators = [
                '[data-test="@web/AccountLink"]',
                '[data-test="accountNav"]',
                'text="Account"',
                'text="Orders"',
                'text="Profile"'
            ]

            for indicator in account_indicators:
                try:
                    element = await page.wait_for_selector(indicator, timeout=3000)
                    if element:
                        self.logger.info("[OK] Token refresh successful - account page loaded")
                        # Save refreshed session state
                        await self.save_session_state()
                        self.last_validation = datetime.now()
                        self.validation_failures = 0
                        return True
                except:
                    continue

            self.logger.warning("[WARNING] Token refresh ambiguous - no clear account indicators")
            return False

        except Exception as e:
            self.logger.error(f"[ERROR] Token refresh failed: {e}")
            return False

    async def refresh_session(self) -> bool:
        """Refresh session - tries navigation refresh first, context recreation as last resort"""
        try:
            self.logger.info("[RETRY] Refreshing session...")

            # STRATEGY 1: Try navigation-based refresh (preserves session cookies)
            self.logger.info("Attempting navigation-based refresh (preserves session cookies)...")
            if await self._trigger_token_refresh():
                self.logger.info("[OK] Session refreshed via navigation (context preserved)")
                return True

            # STRATEGY 2: Context recreation (destroys session cookies - last resort)
            self.logger.warning("Navigation refresh failed, attempting context recreation (will lose session cookies)...")

            # Try context recreation with retry
            if not await self._create_context_with_retry():
                self.logger.error("[ERROR] Failed to recreate context during refresh")
                return False

            # After context recreation, we MUST trigger token refresh
            self.logger.info("Context recreated, triggering token refresh to restore session...")
            if await self._trigger_token_refresh():
                self.logger.info("[OK] Session restored after context recreation")
                return True

            self.logger.error("[ERROR] Session refresh failed after all attempts")
            return False

        except Exception as e:
            self.logger.error(f"[ERROR] Session refresh failed: {e}")
            return False

    async def save_session_state(self) -> bool:
        """Save current session state to file with session cookie persistence fix"""
        try:
            if not self.context:
                self.logger.warning("Cannot save session - no context available")
                return False

            # DEBUGGING: Get all cookies from context to see what we have
            all_cookies = await self.context.cookies("https://www.target.com")
            self.logger.info(f"[COOKIE_DEBUG] [SAVE] Found {len(all_cookies)} cookies for Target.com BEFORE saving")

            # Log critical auth cookies with full details
            critical_cookies = ['accessToken', 'idToken', 'refreshToken', 'login-session']
            for cookie in all_cookies:
                if cookie['name'] in critical_cookies:
                    expires = cookie.get('expires', -1)
                    if expires == -1:
                        expires_str = "SESSION (expires when browser closes)"
                    else:
                        import time
                        days_left = (expires - time.time()) / (24 * 60 * 60)
                        expires_str = f"{days_left:.1f} days"

                    self.logger.info(f"[COOKIE_DEBUG] [SAVE] '{cookie['name']}':")
                    self.logger.info(f"[COOKIE_DEBUG] [SAVE]    - domain: {cookie.get('domain')}")
                    self.logger.info(f"[COOKIE_DEBUG] [SAVE]    - expires: {expires_str}")
                    self.logger.info(f"[COOKIE_DEBUG] [SAVE]    - httpOnly: {cookie.get('httpOnly', False)}")
                    self.logger.info(f"[COOKIE_DEBUG] [SAVE]    - secure: {cookie.get('secure', False)}")
                    self.logger.info(f"[COOKIE_DEBUG] [SAVE]    - sameSite: {cookie.get('sameSite', 'None')}")
                    self.logger.info(f"[COOKIE_DEBUG] [SAVE]    - value length: {len(cookie.get('value', ''))}")

            # Save storage state (includes cookies + localStorage)
            storage_state = await self.context.storage_state()

            # CRITICAL FIX: Convert session cookies (expires: -1) to have explicit expiration
            # This works around Playwright bug where session cookies don't persist between runs
            # https://github.com/microsoft/playwright/issues/36139
            if isinstance(storage_state, dict) and 'cookies' in storage_state:
                import time
                # Set expiration to 30 days from now (increased from 7 days for better persistence)
                future_timestamp = time.time() + (30 * 24 * 60 * 60)

                session_cookies_converted = 0
                skipped_auth_cookies = 0

                for cookie in storage_state['cookies']:
                    # Ensure sameSite attribute is set for better compatibility
                    if 'sameSite' not in cookie:
                        cookie['sameSite'] = 'Lax'  # More permissive for automation
                    elif cookie.get('sameSite') == 'Strict':
                        cookie['sameSite'] = 'Lax'  # Downgrade Strict to Lax for automation

                    if cookie.get('expires', -1) == -1:
                        # Session cookie detected
                        cookie_name = cookie.get('name', '')

                        # 🔑 CRITICAL FIX: Convert ALL session cookies to persistent, INCLUDING auth cookies!
                        # This is the KEY to making login persist across restarts.
                        # Previous logic skipped auth cookies, which is why login didn't persist.
                        critical_auth_cookies = ['accessToken', 'idToken', 'refreshToken', 'login-session']

                        # Convert session cookie to persistent (30 days)
                        cookie['expires'] = future_timestamp
                        session_cookies_converted += 1

                        # Enhanced logging for auth cookies
                        if cookie_name in critical_auth_cookies:
                            self.logger.info(f"[COOKIE_FIX] ✅ Converting AUTH cookie '{cookie_name}' to persistent (30 days)")
                            print(f"[COOKIE_SAVE] ✅ Auth cookie '{cookie_name}' → PERSISTENT (30 days)")
                        elif cookie_name in ['GuestLocation', 'visitorId']:
                            self.logger.info(f"[COOKIE_FIX] ✅ Converting cookie '{cookie_name}' to persistent (30 days)")

                if session_cookies_converted > 0:
                    self.logger.info(f"[COOKIE_FIX] ✓ Converted {session_cookies_converted} session cookies to persistent")
                    print(f"[COOKIE_SAVE] ✅ Converted {session_cookies_converted} session cookies to PERSISTENT")

            # Add fingerprint data
            if isinstance(storage_state, dict):
                storage_state['fingerprint'] = self.fingerprint_data
                storage_state['saved_at'] = datetime.now().isoformat()

            # CRITICAL: Also save sessionStorage (Target.com uses this for checkout auth)
            try:
                pages = self.context.pages
                if pages and len(pages) > 0:
                    page = pages[0]
                    current_url = page.url
                    # Only capture from target.com to avoid unnecessary data
                    if 'target.com' in current_url:
                        session_storage_json = await page.evaluate("() => JSON.stringify(sessionStorage)")
                        storage_state['sessionStorage'] = session_storage_json
                        self.logger.info("[OK] Saved sessionStorage from target.com")
                        print(f"[SESSION] Saved sessionStorage ({len(session_storage_json)} bytes)")
                    else:
                        self.logger.debug(f"[DEBUG] Not on target.com ({current_url}), skipping sessionStorage")
            except Exception as e:
                # Non-fatal - still save cookies/localStorage even if sessionStorage fails
                self.logger.debug(f"Could not save sessionStorage (non-fatal): {e}")

            # Write atomically using temp file
            temp_path = f"{self.session_path}.tmp"
            with open(temp_path, 'w') as f:
                json.dump(storage_state, f, indent=2)

            # Rename to final path (atomic on most systems)
            import os
            os.replace(temp_path, self.session_path)

            # DEBUGGING: Verify file was written
            file_size = os.path.getsize(self.session_path)
            self.logger.info(f"[COOKIE_DEBUG] [SAVE] ✅ Session file written: {self.session_path} ({file_size} bytes)")

            # DEBUGGING: Read back and verify cookies were saved
            with open(self.session_path, 'r') as f:
                saved_data = json.load(f)
                saved_cookies = saved_data.get('cookies', [])
                target_cookies = [c for c in saved_cookies if 'target.com' in c.get('domain', '')]
                self.logger.info(f"[COOKIE_DEBUG] [SAVE] ✅ File contains {len(saved_cookies)} total cookies, {len(target_cookies)} for Target.com")

                # Log critical cookies in saved file with enhanced console output
                auth_cookies_saved = []
                for cookie in target_cookies:
                    if cookie['name'] in critical_cookies:
                        expires = cookie.get('expires', -1)
                        if expires == -1:
                            expires_str = "SESSION ⚠️"
                            self.logger.warning(f"[COOKIE_DEBUG] [SAVE] ⚠️  '{cookie['name']}' still SESSION in file - conversion may have failed!")
                            print(f"[COOKIE_SAVE] ⚠️  WARNING: '{cookie['name']}' is still SESSION cookie in file!")
                        else:
                            import time
                            days_left = (expires - time.time()) / (24 * 60 * 60)
                            expires_str = f"{days_left:.1f} days"
                            auth_cookies_saved.append(cookie['name'])
                        self.logger.info(f"[COOKIE_DEBUG] [SAVE] ✅ Saved '{cookie['name']}' to file (expires: {expires_str})")

                if auth_cookies_saved:
                    print(f"[COOKIE_SAVE] ✅ Auth cookies saved as PERSISTENT: {', '.join(auth_cookies_saved)}")

            # Verify persistent context SQLite database also has cookies
            cookies_db_path = self.user_data_dir / 'Default' / 'Cookies'
            if cookies_db_path.exists():
                db_size = os.path.getsize(cookies_db_path)
                self.logger.info(f"[COOKIE_DEBUG] [SAVE] ✅ Persistent context Cookies DB: {cookies_db_path} ({db_size} bytes)")
                print(f"[COOKIE_SAVE] ✅ Cookies also saved to browser profile: {db_size} bytes")
            else:
                self.logger.warning(f"[COOKIE_DEBUG] [SAVE] ⚠️  Cookies DB not found at {cookies_db_path}")

            self.logger.info(f"[OK] Session state saved to {self.session_path}")
            print(f"[SESSION_SAVE] ✅ Session saved successfully to {self.session_path}")
            return True

        except Exception as e:
            self.logger.error(f"[ERROR] Failed to save session state: {e}")
            return False

    async def is_healthy(self) -> bool:
        """Check if session is healthy and ready for use"""
        if not self.session_active or not self.context:
            return False

        if self.validation_failures >= self.max_validation_failures:
            return False

        # Check if validation is too old (over 10 minutes)
        if self.last_validation:
            age = datetime.now() - self.last_validation
            if age > timedelta(minutes=10):
                return False

        return True

    async def cleanup(self):
        """Clean up resources - public interface"""
        self.logger.info("🧹 Starting session cleanup...")
        await self._safe_cleanup()
        self.logger.info("[OK] Session cleanup completed")

    def set_purchase_in_progress(self, in_progress: bool):
        """Set purchase lock to prevent session validation during purchases"""
        with self._purchase_lock:
            self.purchase_in_progress = in_progress
            if in_progress:
                self.logger.info("[PURCHASE_LOCK] 🔒 Purchase started - validation paused")
                print("[SESSION] 🔒 Purchase in progress - validation paused")
            else:
                self.logger.info("[PURCHASE_LOCK] 🔓 Purchase complete - validation resumed")
                print("[SESSION] 🔓 Purchase complete - validation resumed")

    def is_purchase_in_progress(self) -> bool:
        """Check if a purchase is currently in progress"""
        with self._purchase_lock:
            return self.purchase_in_progress

    def get_session_stats(self) -> Dict[str, Any]:
        """Get session statistics for monitoring"""
        return {
            'active': self.session_active,
            'created_at': self.session_created_at.isoformat() if self.session_created_at else None,
            'last_validation': self.last_validation.isoformat() if self.last_validation else None,
            'validation_failures': self.validation_failures,
            'initialization_attempts': self._initialization_attempts,
            'context_recreation_count': self._context_recreation_count,
            'last_context_recreation': self._last_context_recreation.isoformat() if self._last_context_recreation else None,
            'browser_connected': self.browser is not None,
            'context_connected': self.context is not None,
            'persistent_context_mode': self.browser is None and self.context is not None
        }
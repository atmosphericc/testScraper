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

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

# Import login helper
from ..utils.target_login import ensure_logged_in_with_session_save


class SessionManager:
    """Manages persistent browser session for Target.com automation"""

    def __init__(self, session_path: str = "target.json"):
        self.session_path = Path(session_path)
        self.logger = logging.getLogger(__name__)

        # Playwright instances
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self._context_lock = threading.Lock()

        # Session state
        self.session_active = False
        self.last_validation = None
        self.session_created_at = None
        self.validation_failures = 0

        # Configuration
        self.max_validation_failures = 3
        self.validation_timeout = 10000  # 10 seconds

        # Context lifecycle management
        self._initialization_attempts = 0
        self._max_init_attempts = 3
        self._context_recreation_count = 0
        self._last_context_recreation = None

        # Event loop for thread-safe async operations
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

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

        # Default fingerprint
        return {
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'timezone': 'America/New_York',
            'locale': 'en-US'
        }

    async def initialize(self) -> bool:
        """Initialize persistent browser session with robust error handling"""
        self._initialization_attempts += 1

        try:
            self.logger.info(f"[INIT] Initializing persistent session (attempt {self._initialization_attempts}/{self._max_init_attempts})...")

            # Store the event loop for thread-safe operations
            self._event_loop = asyncio.get_running_loop()
            self.logger.info(f"[OK] Event loop stored for thread-safe operations")

            # Clean up any existing resources first
            await self._safe_cleanup()

            # IMPROVED LIFECYCLE: Use proper async context management
            self.playwright = await async_playwright().start()

            # Configure browser launch options with anti-detection
            launch_options = {
                'headless': False,  # Keep visible for debugging
                'args': [
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-extensions',
                    '--disable-web-security',  # Help with CORS issues
                    '--disable-features=VizDisplayCompositor',  # Improve stability
                    # Anti-detection: Remove automation signals
                    '--exclude-switches=enable-automation',
                    '--disable-blink-features=AutomationControlled',
                    # Realistic browser behavior
                    '--disable-infobars',
                    '--start-maximized',
                    '--disable-notifications',
                    # WebGL/Canvas fingerprinting
                    '--use-gl=swiftshader',
                    '--use-angle=swiftshader',
                    # Additional stealth
                    '--disable-background-timer-throttling',
                    '--disable-backgrounding-occluded-windows',
                    '--disable-renderer-backgrounding'
                ]
            }

            self.browser = await self.playwright.chromium.launch(**launch_options)
            self.logger.info("[OK] Browser launched successfully")

            # Note: Window size will be set when context/page is created

            # Create persistent context with error handling
            if not await self._create_context_with_retry():
                raise Exception("Failed to create context after retries")

            # IMMEDIATE NAVIGATION: Navigate to Target.com right after context creation
            # Do this BEFORE validation to ensure page is ready
            print("[SESSION_INIT] [NAVIGATE] Attempting to navigate to Target.com...")
            self.logger.info("[NAVIGATE] Navigating to Target.com homepage...")
            try:
                # Get page directly from context (bypass health checks during initialization)
                if self.context:
                    print("[SESSION_INIT] Context exists, getting page...")
                    pages = self.context.pages
                    if pages:
                        page = pages[0]
                        print(f"[SESSION_INIT] Using existing page (total pages: {len(pages)})")
                        print(f"[DEBUG_FLASH] 📄 Using existing page from context - NO NEW TAB")
                    else:
                        print("[SESSION_INIT] No pages exist, creating new page...")
                        print(f"[DEBUG_FLASH] [WARNING] CREATING NEW PAGE during session init - THIS WILL CREATE A TAB")
                        page = await self.context.new_page()
                        print("[SESSION_INIT] New page created")
                        print(f"[DEBUG_FLASH] [OK] New page created during init")

                    if page:
                        # Set up WebAuthn (window maximizes automatically with no_viewport=True + --start-maximized)
                        try:
                            client = await self.context.new_cdp_session(page)
                            await client.send('WebAuthn.enable')
                            await client.send('WebAuthn.addVirtualAuthenticator', {
                                'options': {
                                    'protocol': 'ctap2',
                                    'transport': 'internal',
                                    'hasResidentKey': True,
                                    'hasUserVerification': True,
                                    'isUserVerified': True,
                                    'automaticPresenceSimulation': True
                                }
                            })
                            print("[SESSION_INIT] [OK] WebAuthn virtual authenticator enabled (no passkey prompts)")
                        except Exception as e:
                            print(f"[SESSION_INIT] [WARNING] Could not set up WebAuthn: {e}")

                        print(f"[SESSION_INIT] Page object valid, navigating to https://www.target.com...")
                        await page.goto("https://www.target.com", wait_until='domcontentloaded', timeout=15000)
                        print("[SESSION_INIT] Navigation completed, waiting 3s for page to stabilize...")
                        # Give page time to fully load and stabilize
                        await asyncio.sleep(3)
                        current_url = page.url
                        print(f"[SESSION_INIT] [OK] Successfully navigated to Target.com (current URL: {current_url})")
                        self.logger.info(f"[OK] Successfully navigated to Target.com (URL: {current_url})")
                    else:
                        print("[SESSION_INIT] [ERROR] Could not create page for initial navigation")
                        self.logger.warning("[WARNING] Could not create page for initial navigation")
                else:
                    print("[SESSION_INIT] [ERROR] Context not available for initial navigation")
                    self.logger.warning("[WARNING] Context not available for initial navigation")
            except Exception as nav_error:
                print(f"[SESSION_INIT] [ERROR] Navigation exception: {nav_error}")
                import traceback
                traceback.print_exc()
                self.logger.warning(f"[WARNING] Initial navigation failed (will retry in validation): {nav_error}")

            # ALWAYS validate login state - never skip this
            print("[SESSION_INIT] Running mandatory login validation...")
            validation_success = False

            validation_attempts = 2
            for attempt in range(validation_attempts):
                try:
                    if await self._validate_session():
                        validation_success = True
                        break
                    else:
                        self.logger.warning(f"Session validation failed on attempt {attempt + 1}")
                        if attempt < validation_attempts - 1:
                            await asyncio.sleep(2)  # Wait before retry
                except Exception as e:
                    self.logger.warning(f"Session validation error on attempt {attempt + 1}: {e}")
                    if attempt < validation_attempts - 1:
                        await asyncio.sleep(2)

            if validation_success:
                self.session_active = True
                self.session_created_at = datetime.now()
                self._initialization_attempts = 0  # Reset on success
                self.logger.info("[OK] Persistent session initialized successfully")
                return True
            else:
                self.logger.error("[ERROR] Failed to validate session after all attempts")

                # Try one more time with fresh context if we haven't exhausted attempts
                if self._initialization_attempts < self._max_init_attempts:
                    self.logger.info("[RETRY] Retrying initialization with fresh context...")
                    await asyncio.sleep(3)
                    return await self.initialize()
                else:
                    return False

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
            if self.context:
                await self.context.close()
                self.context = None

            if self.browser:
                await self.browser.close()
                self.browser = None

            if self.playwright:
                await self.playwright.stop()
                self.playwright = None

        except Exception as e:
            self.logger.warning(f"Cleanup warning (non-fatal): {e}")

        self.session_active = False

    async def _create_context(self):
        """Create or recreate browser context with session state"""
        try:
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
                    raise Exception("Browser not available for context creation")

                # Debug fingerprint data
                self.logger.debug(f"Fingerprint data keys: {list(self.fingerprint_data.keys())}")

                # Create new context with session state
                # No viewport to allow --start-maximized to work properly (critical for Mac!)
                context_options = {
                    'user_agent': self.fingerprint_data.get('user_agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'),
                    'timezone_id': self.fingerprint_data.get('timezone', 'America/New_York'),
                    'locale': self.fingerprint_data.get('locale', 'en-US'),
                    'no_viewport': True,  # CRITICAL: Allow browser to use actual window size
                    'is_mobile': False,
                    'has_touch': False
                }

                # Add storage state if session file exists and is valid
                if self.session_path.exists():
                    try:
                        # Validate session file before using
                        with open(self.session_path, 'r') as f:
                            session_data = json.load(f)
                        context_options['storage_state'] = str(self.session_path)
                        self.logger.info(f"[OK] Loading session state from {self.session_path}")
                    except Exception as e:
                        self.logger.warning(f"Invalid session file, starting fresh: {e}")

                print(f"[DEBUG_FLASH] [INIT] Creating new browser context (recreation #{self._context_recreation_count})")
                self.context = await self.browser.new_context(**context_options)
                print(f"[DEBUG_FLASH] [OK] Browser context created - checking for initial pages...")

                # Check if context auto-created any pages
                initial_pages = self.context.pages
                print(f"[DEBUG_FLASH] Context has {len(initial_pages)} pages after creation")

                # WebAuthn setup will be done after main page creation to avoid tab flash

                # Set up stealth mode
                await self._setup_stealth_mode()

                self.logger.info(f"[OK] Browser context created successfully (recreation #{self._context_recreation_count})")

        except Exception as e:
            self.logger.error(f"[ERROR] Failed to create context: {e}")
            raise

    async def _setup_stealth_mode(self):
        """Configure context for stealth automation - 2025 enhanced edition"""
        try:
            # Add comprehensive stealth script to all pages
            await self.context.add_init_script("""
                // ===== CRITICAL: Remove ALL webdriver/automation traces =====

                // 1. Navigator.webdriver (most common check)
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined,
                    configurable: true
                });

                // 2. Remove ALL CDP/Playwright detection variables
                delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
                delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
                delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
                delete window.cdc_adoQpoasnfa76pfcZLmcfl_Object;

                // Playwright-specific variables
                delete window.__playwright;
                delete window.__pw_manual;
                delete window.__PW_inspect;

                // 3. Chrome runtime properties (fix automation detection)
                if (window.chrome) {
                    window.chrome.runtime = {
                        connect: function() {},
                        sendMessage: function() {},
                        onMessage: {
                            addListener: function() {},
                            removeListener: function() {},
                            hasListener: function() { return false; }
                        }
                    };
                }

                // 4. Fix plugins length (headless = 0, real browser = 1+)
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [
                        {
                            0: {type: "application/x-google-chrome-pdf"},
                            description: "Portable Document Format",
                            filename: "internal-pdf-viewer",
                            length: 1,
                            name: "Chrome PDF Plugin"
                        }
                    ]
                });

                // 5. Languages (must be array, not just string)
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en']
                });

                // 6. Platform consistency
                Object.defineProperty(navigator, 'platform', {
                    get: () => 'Win32'
                });

                // 7. Hardware concurrency (realistic value)
                Object.defineProperty(navigator, 'hardwareConcurrency', {
                    get: () => 8
                });

                // 8. Override permissions query
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );

                // 9. WebGL vendor/renderer (avoid "Google SwiftShader" detection)
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {
                    if (parameter === 37445) return 'Intel Inc.';  // UNMASKED_VENDOR_WEBGL
                    if (parameter === 37446) return 'Intel Iris OpenGL Engine';  // UNMASKED_RENDERER_WEBGL
                    return getParameter.apply(this, arguments);
                };

                // 10. Connection type (realistic)
                Object.defineProperty(navigator, 'connection', {
                    get: () => ({
                        effectiveType: '4g',
                        rtt: 50,
                        downlink: 10,
                        saveData: false
                    })
                });

                // 11. Battery API (realistic)
                Object.defineProperty(navigator, 'getBattery', {
                    value: () => Promise.resolve({
                        charging: true,
                        chargingTime: 0,
                        dischargingTime: Infinity,
                        level: 1
                    })
                });
            """)

            self.logger.debug("🥷 Enhanced stealth mode configured (2025 edition)")

        except Exception as e:
            self.logger.warning(f"Failed to setup stealth mode: {e}")

    async def get_page(self) -> Optional[Page]:
        """Get a page from the persistent context - bulletproof with auto-recovery"""
        max_attempts = 3
        print(f"[DEBUG_FLASH] [DEBUG] get_page() called - checking for pages...")

        for attempt in range(max_attempts):
            try:
                with self._context_lock:
                    # BULLETPROOF CHECK 1: Ensure context exists and is valid
                    if not self.context or not self.browser:
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
                        if pages:
                            page = pages[0]
                            print(f"[DEBUG_FLASH] [OK] Using existing page[0] - NO NEW TAB")
                        else:
                            print(f"[DEBUG_FLASH] [WARNING] NO PAGES EXIST - CREATING NEW PAGE - THIS WILL FLASH!")
                            page = await self.context.new_page()
                            print(f"[DEBUG_FLASH] [OK] New page created in get_page()")

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
            print(f"[DEBUG_FLASH] [RETRY] _recreate_context_if_needed() called - WILL CREATE NEW CONTEXT")
            if self.browser:
                await self._create_context()
                print(f"[DEBUG_FLASH] [OK] Context recreated in _recreate_context_if_needed()")
            else:
                print(f"[DEBUG_FLASH] [ERROR] Cannot recreate context - browser is None")
                self.logger.error("Cannot recreate context - browser is None")
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

    async def _validate_session(self, attempt_recovery: bool = True) -> bool:
        """Validate that the session is still active and logged in - STRICT validation with auto-recovery"""
        try:
            self.logger.info("[DEBUG] [VALIDATION START] Beginning session validation...")

            page = await self.get_page()
            if not page:
                self.logger.error("[ERROR] [VALIDATION] No page available for validation")
                return False

            # Navigate to Target homepage to test session
            self.logger.info("[NAV] [VALIDATION] Navigating to Target.com for login validation...")
            await page.goto("https://www.target.com",
                          wait_until='domcontentloaded',
                          timeout=self.validation_timeout)

            current_url = page.url
            self.logger.info(f"📍 [VALIDATION] Current URL: {current_url}")

            # Wait a moment for login state to load
            self.logger.info("⏳ [VALIDATION] Waiting 2s for page to stabilize...")
            await asyncio.sleep(2)

            # STRICT CHECK: Look for positive login indicators
            login_indicators = [
                '[data-test="@web/AccountLink"]',
                '[data-test="accountNav"]',
                'button[aria-label*="Account"]',
                'button[aria-label*="Hi,"]',
                'button:has-text("Hi,")',
                '[data-test="@web/ProfileIcon"]',
                'button[data-test="accountNav-signOut"]'
            ]

            self.logger.info(f"🔎 [VALIDATION] Checking {len(login_indicators)} login indicators...")

            login_found = False
            for i, indicator in enumerate(login_indicators, 1):
                try:
                    self.logger.info(f"🔎 [VALIDATION] Checking indicator {i}/{len(login_indicators)}: {indicator}")
                    element = await page.wait_for_selector(indicator, timeout=3000)
                    if element and await element.is_visible():
                        self.logger.info(f"[OK] [VALIDATION] Login validated via: {indicator}")
                        self.last_validation = datetime.now()
                        self.validation_failures = 0
                        login_found = True
                        # Save session after successful validation
                        await self.save_session_state()
                        return True
                    else:
                        self.logger.info(f"[WARNING] [VALIDATION] Indicator {i} found but not visible: {indicator}")
                except Exception as e:
                    self.logger.info(f"[ERROR] [VALIDATION] Indicator {i} not found: {indicator} ({type(e).__name__})")
                    continue

            if not login_found:
                self.logger.error("[ERROR] [VALIDATION] No login indicators found - NOT LOGGED IN")

                # Check for sign-in prompts to confirm
                signin_indicators = [
                    'text="Sign in"',
                    'button:has-text("Sign in")',
                    'input[type="email"]',
                    '[data-test*="signin"]'
                ]

                signin_found = False
                for indicator in signin_indicators:
                    try:
                        element = await page.wait_for_selector(indicator, timeout=1000)
                        if element:
                            self.logger.error(f"[ERROR] Found sign-in prompt: {indicator} - SESSION EXPIRED")
                            signin_found = True
                            break
                    except:
                        continue

                # SESSION RECOVERY: Try multiple recovery strategies
                if attempt_recovery:
                    self.logger.warning("[RETRY] Attempting session recovery...")

                    # STRATEGY 1: Try token refresh (uses refreshToken)
                    self.logger.info("Strategy 1: Attempting token refresh...")
                    if await self._trigger_token_refresh():
                        self.logger.info("[OK] Session recovered via token refresh!")
                        # Re-validate after recovery (but don't attempt recovery again)
                        return await self._validate_session(attempt_recovery=False)

                    # STRATEGY 2: Try auto-login using our robust login flow
                    self.logger.info("Strategy 2: Attempting auto-login with full login flow...")
                    try:
                        # Pass existing page to avoid tab flash
                        if await ensure_logged_in_with_session_save(self.context, existing_page=page):
                            self.logger.info("[OK] Session recovered via auto-login!")
                            # Re-validate after login
                            return await self._validate_session(attempt_recovery=False)
                        else:
                            self.logger.warning("[WARNING] Auto-login failed")
                    except Exception as login_error:
                        self.logger.error(f"[ERROR] Auto-login error: {login_error}")

                    self.logger.error("[ERROR] All session recovery strategies failed")

                self.validation_failures += 1
                return False

        except Exception as e:
            self.logger.error(f"[ERROR] Session validation failed: {e}")
            self.validation_failures += 1
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

    async def _auto_login(self, email: str, password: str) -> bool:
        """Automatically log in to Target.com with F5-safe human-like behavior"""
        try:
            self.logger.info("[AUTH] Starting auto-login flow...")

            page = await self.get_page()
            if not page:
                self.logger.error("Cannot auto-login - no page available")
                return False

            # Navigate to login page with human-like delay
            self.logger.info("Navigating to Target.com sign-in page...")
            await page.goto("https://www.target.com/login",
                          wait_until='domcontentloaded',
                          timeout=15000)

            # Random delay (F5-safe: look like human reading page)
            await asyncio.sleep(random.uniform(1.5, 3.0))

            # Dismiss any initial popups
            await self._dismiss_popups(page)

            # Find email field - multiple possible selectors
            email_selectors = [
                'input[type="email"]',
                'input[name="username"]',
                'input[id*="username"]',
                'input[id*="email"]',
                '#username'
            ]

            email_field = None
            for selector in email_selectors:
                try:
                    email_field = await page.wait_for_selector(selector, timeout=3000)
                    if email_field:
                        self.logger.info(f"Found email field: {selector}")
                        break
                except:
                    continue

            if not email_field:
                self.logger.error("[ERROR] Could not find email input field")
                return False

            # F5-SAFE: Type email slowly like a human
            await asyncio.sleep(random.uniform(0.5, 1.0))
            await email_field.click()
            await asyncio.sleep(random.uniform(0.2, 0.4))

            for char in email:
                await email_field.type(char)
                await asyncio.sleep(random.uniform(0.05, 0.15))  # Human typing speed

            self.logger.info("[OK] Email entered")

            # Random pause (human behavior)
            await asyncio.sleep(random.uniform(0.5, 1.0))

            # CRITICAL: Target.com uses two-step login - click Continue button first!
            self.logger.info("Looking for 'Continue' button...")
            continue_selectors = [
                'button:has-text("Continue")',
                'button:has-text("Next")',
                'button[type="submit"]',
                '#login'
            ]

            continue_button = None
            for selector in continue_selectors:
                try:
                    continue_button = await page.wait_for_selector(selector, timeout=3000)
                    if continue_button and await continue_button.is_visible():
                        self.logger.info(f"Found continue button: {selector}")
                        break
                except:
                    continue

            if not continue_button:
                self.logger.error("[ERROR] Could not find continue button")
                return False

            # Click continue to proceed to password screen
            self.logger.info("Clicking continue to proceed to password...")
            await asyncio.sleep(random.uniform(0.3, 0.7))
            await continue_button.click()
            await asyncio.sleep(2)  # Wait for response

            # CRITICAL: Dismiss passkey modal that appears after clicking continue
            self.logger.info("Checking for passkey modal...")

            # Strategy 1: Press Escape key to dismiss modal
            self.logger.info("Attempting to dismiss modal with Escape key...")
            await page.keyboard.press('Escape')
            await asyncio.sleep(1)

            # Strategy 2: Look for and click close/cancel buttons
            passkey_close_selectors = [
                'button:has-text("Close")',
                'button:has-text("Cancel")',
                'button:has-text("Skip")',
                'button:has-text("Not now")',
                'button[aria-label="Close"]',
                '[aria-label*="close"]',
                'button[class*="close"]',
                '.modal button[aria-label="Close"]'
            ]

            for selector in passkey_close_selectors:
                try:
                    close_button = await page.query_selector(selector)
                    if close_button and await close_button.is_visible():
                        self.logger.info(f"Found close button: {selector}, dismissing...")
                        await asyncio.sleep(random.uniform(0.3, 0.7))
                        await close_button.click()
                        self.logger.info("[OK] Passkey modal dismissed!")
                        await asyncio.sleep(1)
                        break
                except:
                    continue

            self.logger.info("Modal dismissed (or none present)")

            # Now find password field
            self.logger.info("Looking for password field...")
            await asyncio.sleep(random.uniform(0.5, 1.0))

            password_selectors = [
                'input[type="password"]',
                'input[name="password"]',
                'input[id*="password"]',
                '#password'
            ]

            password_field = None
            for selector in password_selectors:
                try:
                    password_field = await page.wait_for_selector(selector, timeout=5000)
                    if password_field:
                        self.logger.info(f"Found password field: {selector}")
                        break
                except:
                    continue

            if not password_field:
                self.logger.error("[ERROR] Could not find password input field")
                return False

            # F5-SAFE: Type password slowly
            await asyncio.sleep(random.uniform(0.3, 0.7))
            await password_field.click()
            await asyncio.sleep(random.uniform(0.2, 0.4))

            for char in password:
                await password_field.type(char)
                await asyncio.sleep(random.uniform(0.05, 0.15))

            self.logger.info("[OK] Password entered")

            # Random pause before submit (human behavior)
            await asyncio.sleep(random.uniform(0.8, 1.5))

            # Find and click sign-in button
            signin_button_selectors = [
                'button[type="submit"]',
                'button:has-text("Sign in")',
                'button:has-text("Log in")',
                '#login',
                '[data-test*="sign"]'
            ]

            signin_button = None
            for selector in signin_button_selectors:
                try:
                    signin_button = await page.wait_for_selector(selector, timeout=3000)
                    if signin_button and await signin_button.is_visible():
                        self.logger.info(f"Found sign-in button: {selector}")
                        break
                except:
                    continue

            if not signin_button:
                self.logger.error("[ERROR] Could not find sign-in button")
                return False

            # F5-SAFE: Human-like delay before clicking submit
            await asyncio.sleep(random.uniform(0.3, 0.7))
            await signin_button.click()
            self.logger.info("[RETRY] Sign-in submitted, waiting for response...")

            # Wait for navigation or error
            await asyncio.sleep(random.uniform(2.0, 4.0))

            # CRITICAL: Dismiss passkey/security popups after login
            self.logger.info("Checking for post-login popups...")
            await self._dismiss_popups(page)
            await asyncio.sleep(random.uniform(1.0, 2.0))
            await self._dismiss_popups(page)  # Second pass to catch delayed popups

            # Check if login was successful
            current_url = page.url

            # Check for login error messages
            error_selectors = [
                'text="incorrect"',
                'text="error"',
                'text="invalid"',
                '[class*="error"]',
                '[role="alert"]'
            ]

            login_error = False
            for selector in error_selectors:
                try:
                    error_elem = await page.query_selector(selector)
                    if error_elem:
                        error_text = await error_elem.inner_text()
                        if error_text and len(error_text) > 0:
                            self.logger.error(f"[ERROR] Login error: {error_text}")
                            login_error = True
                            break
                except:
                    continue

            if login_error:
                return False

            # Check for successful login indicators
            if 'login' not in current_url.lower() and 'signin' not in current_url.lower():
                self.logger.info("[OK] Login successful - redirected away from login page")

                # Save session immediately
                await self.save_session_state()

                # Validate we're actually logged in
                return await self._validate_session(attempt_recovery=False)
            else:
                self.logger.warning(f"[WARNING] Still on login page: {current_url}")
                return False

        except Exception as e:
            self.logger.error(f"[ERROR] Auto-login failed: {e}")
            import traceback
            traceback.print_exc()
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
            await page.goto("https://www.target.com/account",
                          wait_until='domcontentloaded',
                          timeout=15000)

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

            # Final validation attempt
            validation_attempts = 2
            for attempt in range(validation_attempts):
                try:
                    if await self._validate_session():
                        self.logger.info("[OK] Session refreshed successfully")
                        return True
                    else:
                        self.logger.warning(f"Session refresh validation failed on attempt {attempt + 1}")
                        if attempt < validation_attempts - 1:
                            await asyncio.sleep(2)
                except Exception as e:
                    self.logger.warning(f"Session refresh validation error on attempt {attempt + 1}: {e}")
                    if attempt < validation_attempts - 1:
                        await asyncio.sleep(2)

            self.logger.error("[ERROR] Session refresh failed validation after all attempts")
            return False

        except Exception as e:
            self.logger.error(f"[ERROR] Session refresh failed: {e}")
            return False

    async def save_session_state(self) -> bool:
        """Save current session state to file"""
        try:
            if not self.context:
                self.logger.warning("Cannot save session - no context available")
                return False

            # Save storage state (includes cookies)
            storage_state = await self.context.storage_state()

            # Add fingerprint data
            if isinstance(storage_state, dict):
                storage_state['fingerprint'] = self.fingerprint_data
                storage_state['saved_at'] = datetime.now().isoformat()

            # Write atomically using temp file
            temp_path = f"{self.session_path}.tmp"
            with open(temp_path, 'w') as f:
                json.dump(storage_state, f, indent=2)

            # Rename to final path (atomic on most systems)
            import os
            os.replace(temp_path, self.session_path)

            self.logger.info(f"[OK] Session state saved to {self.session_path}")
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
            'context_connected': self.context is not None
        }
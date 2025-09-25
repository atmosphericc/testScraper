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
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from playwright.async_api import async_playwright, Browser, BrowserContext, Page


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
        """Initialize persistent browser session"""
        try:
            self.logger.info("🚀 Initializing persistent session...")

            # Launch Playwright and browser
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=False,  # Keep visible for debugging
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-extensions'
                ]
            )

            # Create persistent context
            await self._create_context()

            # Validate session immediately
            if await self._validate_session():
                self.session_active = True
                self.session_created_at = datetime.now()
                self.logger.info(" Persistent session initialized successfully")
                return True
            else:
                self.logger.error(" Failed to validate initial session")
                return False

        except Exception as e:
            self.logger.error(f" Failed to initialize session: {e}")
            await self.cleanup()
            return False

    async def _create_context(self):
        """Create or recreate browser context with session state"""
        try:
            with self._context_lock:
                # Close existing context if it exists
                if self.context:
                    await self.context.close()

                # Debug fingerprint data
                self.logger.info(f"Fingerprint data keys: {list(self.fingerprint_data.keys())}")

                # Create new context with session state
                context_options = {
                    'user_agent': self.fingerprint_data.get('user_agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'),
                    'timezone_id': self.fingerprint_data.get('timezone', 'America/New_York'),
                    'locale': self.fingerprint_data.get('locale', 'en-US'),
                    'viewport': {'width': 1920, 'height': 1080},
                    'device_scale_factor': 1,
                    'is_mobile': False,
                    'has_touch': False
                }

                # Add storage state if session file exists
                if self.session_path.exists():
                    context_options['storage_state'] = str(self.session_path)
                    self.logger.info(f"Loading session state from {self.session_path}")

                self.context = await self.browser.new_context(**context_options)

                # Set up stealth mode
                await self._setup_stealth_mode()

                self.logger.info("Browser context created successfully")

        except Exception as e:
            self.logger.error(f" Failed to create context: {e}")
            raise

    async def _setup_stealth_mode(self):
        """Configure context for stealth automation"""
        try:
            # Add stealth script to all pages
            await self.context.add_init_script("""
                // Remove webdriver traces
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined,
                    configurable: true
                });

                // Remove automation indicators
                delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
                delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
                delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;

                // Override permissions query
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );
            """)

            self.logger.debug("🥷 Stealth mode configured")

        except Exception as e:
            self.logger.warning(f"Failed to setup stealth mode: {e}")

    async def get_page(self) -> Optional[Page]:
        """Get a page from the persistent context"""
        try:
            with self._context_lock:
                if not self.context:
                    raise Exception("Context not initialized")

                # Get existing page or create new one
                pages = self.context.pages
                if pages:
                    return pages[0]
                else:
                    return await self.context.new_page()

        except Exception as e:
            self.logger.error(f" Failed to get page: {e}")
            return None

    async def _validate_session(self) -> bool:
        """Validate that the session is still active and logged in"""
        try:
            page = await self.get_page()
            if not page:
                return False

            # Navigate to Target homepage to test session
            await page.goto("https://www.target.com",
                          wait_until='domcontentloaded',
                          timeout=self.validation_timeout)

            # Check for login indicators
            login_indicators = [
                '[data-test="@web/AccountLink"]',
                '[data-test="accountNav"]',
                'button[aria-label*="Account"]',
                'button[aria-label*="Hi,"]',
                'button:has-text("Hi,")',
                '[data-test="@web/ProfileIcon"]',
                'button[data-test="accountNav-signOut"]'
            ]

            for indicator in login_indicators:
                try:
                    await page.wait_for_selector(indicator, timeout=2000)
                    self.logger.debug(f" Session validated via: {indicator}")
                    self.last_validation = datetime.now()
                    self.validation_failures = 0
                    return True
                except:
                    continue

            # Check for sign-in prompts (indicates not logged in)
            signin_indicators = [
                'text="Sign in"',
                'button:has-text("Sign in")',
                'input[type="email"]',
                '[data-test*="signin"]'
            ]

            for indicator in signin_indicators:
                try:
                    await page.wait_for_selector(indicator, timeout=1000)
                    self.logger.warning(f" Session invalid - found: {indicator}")
                    return False
                except:
                    continue

            # Default to valid if no clear indicators
            self.logger.info(" Session validation passed (no negative indicators)")
            self.last_validation = datetime.now()
            self.validation_failures = 0
            return True

        except Exception as e:
            self.logger.warning(f" Session validation failed: {e}")
            self.validation_failures += 1
            return False

    async def refresh_session(self) -> bool:
        """Refresh session by recreating context"""
        try:
            self.logger.info("Refreshing session...")

            await self._create_context()

            if await self._validate_session():
                self.logger.info(" Session refreshed successfully")
                return True
            else:
                self.logger.error(" Session refresh failed validation")
                return False

        except Exception as e:
            self.logger.error(f" Session refresh failed: {e}")
            return False

    async def save_session_state(self) -> bool:
        """Save current session state to file"""
        try:
            with self._context_lock:
                if not self.context:
                    return False

                # Save storage state
                storage_state = await self.context.storage_state()

                # Add fingerprint data
                if isinstance(storage_state, dict):
                    storage_state['fingerprint'] = self.fingerprint_data
                    storage_state['saved_at'] = datetime.now().isoformat()

                with open(self.session_path, 'w') as f:
                    json.dump(storage_state, f, indent=2)

                self.logger.info(f"Session state saved to {self.session_path}")
                return True

        except Exception as e:
            self.logger.error(f" Failed to save session state: {e}")
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
        """Clean up resources"""
        try:
            self.session_active = False

            if self.context:
                await self.context.close()
                self.context = None

            if self.browser:
                await self.browser.close()
                self.browser = None

            if self.playwright:
                await self.playwright.stop()
                self.playwright = None

            self.logger.info("Session cleanup completed")

        except Exception as e:
            self.logger.error(f" Cleanup failed: {e}")

    def get_session_stats(self) -> Dict[str, Any]:
        """Get session statistics for monitoring"""
        return {
            'active': self.session_active,
            'created_at': self.session_created_at.isoformat() if self.session_created_at else None,
            'last_validation': self.last_validation.isoformat() if self.last_validation else None,
            'validation_failures': self.validation_failures,
            'healthy': asyncio.create_task(self.is_healthy()) if asyncio.get_event_loop().is_running() else False
        }
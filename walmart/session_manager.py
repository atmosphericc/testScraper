"""
Walmart session manager — persistent Patchright browser session.

Handles:
  - Browser startup with stealth config (Patchright + real Chrome channel)
  - Login flow
  - Cookie persistence to disk
  - Session validation
  - Pre-drop session warming (builds Akamai behavioral profile + fresh _px3 cookie)
"""

import asyncio
import json
import logging
import random
import threading
import time
from pathlib import Path
from typing import Optional, Callable

from .config import (
    HEADLESS,
    PROFILE_DIR,
    COOKIES_FILE,
    WALMART_LOGIN_URL,
    WALMART_ACCOUNT_URL,
    PX3_MAX_AGE_SECONDS,
    SESSION_VALIDATE_INTERVAL,
    SESSION_MAX_IDLE,
)

logger = logging.getLogger(__name__)

# Injected before every page load to hide automation signals from PerimeterX / HUMAN Security
_STEALTH_SCRIPT = """
(function() {
    // Remove navigator.webdriver
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined, configurable: true });

    // Real Chrome always has plugins; automation contexts often have 0
    Object.defineProperty(navigator, 'plugins', {
        get: () => [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
            { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }
        ],
        configurable: true
    });

    // Spoof mimeTypes
    Object.defineProperty(navigator, 'mimeTypes', {
        get: () => [
            { type: 'application/pdf', description: 'Portable Document Format', suffixes: 'pdf' },
            { type: 'application/x-google-chrome-pdf', description: 'Portable Document Format', suffixes: 'pdf' },
            { type: 'application/x-nacl', description: 'Native Client Executable', suffixes: '' },
            { type: 'application/x-pnacl', description: 'Portable Native Client Executable', suffixes: '' }
        ],
        configurable: true
    });

    // Languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
        configurable: true
    });

    // window.chrome must exist in real Chrome
    if (!window.chrome) {
        window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
    }
})();
"""

# Module-level lock shared between session_manager (writer) and stock_monitor (reader)
# to prevent cookie file corruption during concurrent access.
_cookie_file_lock = threading.Lock()


class WalmartSessionManager:
    """
    Manages a single persistent Patchright browser session for Walmart checkout.

    Typical lifecycle:
        session = WalmartSessionManager()
        await session.start()
        await session.login("email", "password")   # only needed once
        await session.warm_session(["15042474261"])
        page = session.get_page()
        # ... purchase executor uses page ...
        await session.stop()
    """

    def __init__(self, status_callback: Optional[Callable[[str], None]] = None):
        self._status_cb = status_callback or (lambda msg: None)
        self._zd_browser = None   # zendriver — launches real Chrome
        self._pw_browser = None   # patchright — connects via CDP
        self._playwright = None
        self._context = None
        self._page = None
        self._cookies_path = Path(COOKIES_FILE)
        self._profile_dir = Path(PROFILE_DIR)
        self._last_validation: float = 0.0
        self._last_activity: float = time.monotonic()
        self._px3_timestamp: float = 0.0   # when we last saw a fresh _px3 cookie

        # Shared live cookie store — harvester writes, proxy workers read
        self._live_cookies: dict = {}
        self._live_cookies_lock = threading.Lock()
        self._live_cookies_timestamp: float = 0.0

        # Harvester background task
        self._harvester_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Startup / shutdown
    # ------------------------------------------------------------------

    async def start(self):
        """Launch Chrome via zendriver, then connect Patchright over CDP."""
        try:
            import zendriver as uc
        except ImportError:
            raise RuntimeError("zendriver is not installed. Run: pip install zendriver")
        try:
            from patchright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "patchright is not installed. Run: pip install patchright && "
                "python -m patchright install chromium"
            )

        self._profile_dir.mkdir(parents=True, exist_ok=True)
        Path("walmart/logs").mkdir(parents=True, exist_ok=True)

        self._status_cb("[SESSION] Starting Chrome via zendriver...")

        try:
            zd_config = uc.Config(
                user_data_dir=str(self._profile_dir),
                headless=False,
                browser_args=[
                    "--window-size=1920,1080",
                    "--disable-blink-features=AutomationControlled",
                ],
                browser_connection_timeout=1.0,
                browser_connection_max_tries=30,
            )
            self._zd_browser = await uc.start(zd_config)
            port = self._zd_browser.config.port

            self._status_cb(f"[SESSION] Chrome started (port {port}), connecting Patchright...")

            # Give Chrome's network stack time to fully initialize
            await asyncio.sleep(3)

            self._playwright = await async_playwright().start()
            self._pw_browser = await self._playwright.chromium.connect_over_cdp(
                f"http://localhost:{port}"
            )

            # Grab the context Chrome already opened for the profile
            contexts = self._pw_browser.contexts
            if contexts:
                self._context = contexts[0]
            else:
                self._context = await self._pw_browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    locale="en-US",
                    timezone_id="America/New_York",
                )

            await self._context.add_init_script(_STEALTH_SCRIPT)

            pages = self._context.pages
            self._page = pages[0] if pages else await self._context.new_page()

            # Load saved cookies from walmart_relogin.py into the session
            await self._load_cookies()

        except Exception as e:
            for attr in ('_page', '_context'):
                setattr(self, attr, None)
            if self._pw_browser:
                try:
                    await self._pw_browser.close()
                except Exception:
                    pass
                self._pw_browser = None
            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
            if self._zd_browser:
                try:
                    await self._zd_browser.stop()
                except Exception:
                    pass
                self._zd_browser = None
            raise RuntimeError(f"[SESSION] Failed to start browser: {e}") from e

        self._status_cb("[SESSION] Browser ready")
        logger.info("[SESSION] zendriver+Patchright CDP browser started")

    async def stop(self):
        """Save cookies and close the browser."""
        try:
            await self.save_cookies()
        except Exception:
            pass
        self._page = None
        self._context = None
        try:
            if self._pw_browser:
                await self._pw_browser.close()
        except Exception:
            pass
        self._pw_browser = None
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._playwright = None
        try:
            if self._zd_browser:
                await self._zd_browser.stop()
        except Exception:
            pass
        self._zd_browser = None
        logger.info("[SESSION] Browser stopped")

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    async def login(self, email: str, password: str) -> bool:
        """
        Log in to Walmart. Saves cookies on success.
        Returns True on success, False on failure.
        """
        if not self._page:
            raise RuntimeError("Session not started — call start() first")

        self._status_cb("[SESSION] Logging in to Walmart...")
        try:
            await self._page.goto(WALMART_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            await self._handle_blocked_page()
            await asyncio.sleep(2)

            # Fill email — try selectors individually (patchright doesn't support
            # comma-separated CSS selectors in wait_for_selector)
            email_input = await self._find_input(
                ['input[name="email"]', 'input[type="email"]', '#email'], timeout=10000
            )
            if not email_input:
                self._status_cb("[SESSION] Could not find email field")
                return False
            await email_input.fill(email)
            await asyncio.sleep(0.5)

            # Walmart desktop login shows both email + password simultaneously.
            # Some mobile/variant pages show a "Continue" step — try it but don't fail.
            try:
                continue_btn = self._page.locator(
                    'button:has-text("Continue")'
                ).first
                await continue_btn.click(timeout=5000)
                await asyncio.sleep(1.5)
            except Exception:
                pass  # Single-step form — no Continue button, that's normal

            # Fill password — try selectors individually
            password_input = await self._find_input(
                ['input[name="password"]', 'input[type="password"]', '#password'],
                timeout=10000,
            )
            if not password_input:
                self._status_cb("[SESSION] Could not find password field")
                return False
            await password_input.fill(password)
            await asyncio.sleep(0.5)

            # Submit
            sign_in_btn = self._page.locator(
                'button:has-text("Sign In"), button:has-text("Log in"), button[type="submit"]'
            ).first
            await sign_in_btn.click()
            await asyncio.sleep(3)

            # Verify login success — should no longer be on the login page.
            # Check the URL path only (not query string) to avoid false negatives
            # on redirect URLs like /account?signin=complete.
            from urllib.parse import urlparse
            current_url = self._page.url
            parsed_path = urlparse(current_url).path.lower()
            if "login" in parsed_path or "signin" in parsed_path:
                self._status_cb("[SESSION] Login failed — still on login page")
                logger.warning("[SESSION] Login may have failed — URL: %s", current_url)
                return False

            await self.save_cookies()
            self._last_validation = time.monotonic()
            self._last_activity = time.monotonic()
            self._status_cb("[SESSION] Login successful")
            logger.info("[SESSION] Login successful")
            return True

        except Exception as e:
            self._status_cb(f"[SESSION] Login error: {e}")
            logger.exception("[SESSION] Login error")
            return False

    # ------------------------------------------------------------------
    # Session validation
    # ------------------------------------------------------------------

    async def validate_session(self) -> bool:
        """
        Check if we're still logged in. Navigates to account page.
        Returns True if session is valid.
        """
        if not self._page:
            return False

        now = time.monotonic()

        # Don't re-validate if recently done
        if now - self._last_validation < SESSION_VALIDATE_INTERVAL:
            return True

        # Force re-login if too idle
        if now - self._last_activity > SESSION_MAX_IDLE:
            self._status_cb("[SESSION] Session expired due to inactivity")
            return False

        try:
            await self._page.goto(WALMART_ACCOUNT_URL, wait_until="domcontentloaded", timeout=15000)
            await self._handle_blocked_page()
            await asyncio.sleep(1)
            from urllib.parse import urlparse
            url = self._page.url
            parsed_path = urlparse(url).path.lower()
            if "login" in parsed_path or "signin" in parsed_path:
                self._status_cb("[SESSION] Session invalid — redirected to login")
                return False

            # URL check is sufficient — Walmart's account page always contains
            # "Sign in" text in the nav even for logged-in users, so a body text
            # check produces false positives and triggers re-login every startup.

            self._last_validation = now
            self._last_activity = now
            return True
        except Exception as e:
            logger.warning("[SESSION] Validation error: %s", e)
            return False

    # ------------------------------------------------------------------
    # Session warming
    # ------------------------------------------------------------------

    async def warm_session(self, item_ids: list[str]):
        """
        Visit product pages before a drop to build a warm Akamai behavioral
        profile and obtain a fresh _px3 PerimeterX clearance cookie.

        Should be called 1-2 minutes before a known drop time.
        """
        if not self._page:
            return

        self._status_cb("[SESSION] Warming session...")
        warm_ids = item_ids[:3]  # visit up to 3 product pages

        for item_id in warm_ids:
            try:
                url = f"https://www.walmart.com/ip/x/{item_id}"
                await self._page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await self._handle_blocked_page()
                await asyncio.sleep(2.5)  # dwell time — mimics human browsing

                # Capture the _px3 cookie timestamp
                cookies = await self._context.cookies()
                for c in cookies:
                    if c["name"] == "_px3":
                        self._px3_timestamp = time.monotonic()
                        logger.info("[SESSION] Fresh _px3 cookie obtained")
                        break

            except Exception as e:
                logger.warning("[SESSION] Warm page error for %s: %s", item_id, e)

        self._last_activity = time.monotonic()

        if self._px3_timestamp == 0.0:
            logger.warning(
                "[SESSION] _px3 cookie not found after warming — anti-bot protection may trigger"
            )

        self._status_cb("[SESSION] Session warm — ready for checkout")

    def needs_rewarm(self) -> bool:
        """True if the _px3 cookie is stale and we should warm before checkout."""
        return (time.monotonic() - self._px3_timestamp) > PX3_MAX_AGE_SECONDS

    # ------------------------------------------------------------------
    # Cookie harvester — keeps live cookies fresh for proxy workers
    # ------------------------------------------------------------------

    async def harvest_now(self):
        """Immediately snapshot current browser cookies into the live store."""
        if not self._context:
            return
        try:
            cookies = await self._context.cookies()
            cookie_dict = {c["name"]: c["value"] for c in cookies if "name" in c}
            with self._live_cookies_lock:
                self._live_cookies = cookie_dict
                self._live_cookies_timestamp = time.monotonic()
            if "_px3" in cookie_dict:
                self._px3_timestamp = time.monotonic()
            logger.info("[HARVESTER] Initial cookie snapshot: %d cookies", len(cookie_dict))
        except Exception as e:
            logger.warning("[HARVESTER] harvest_now failed: %s", e)

    def start_harvester(self, loop: asyncio.AbstractEventLoop):
        """Start the background cookie harvester on the given event loop."""
        self._harvester_task = asyncio.run_coroutine_threadsafe(
            self._harvester_loop(), loop
        )

    def stop_harvester(self):
        if self._harvester_task:
            self._harvester_task.cancel()
            self._harvester_task = None

    async def _harvester_loop(self):
        """
        Every 30s: navigate a Walmart page silently, extract all cookies,
        and publish them to _live_cookies for proxy workers to consume.
        _px3 expires in ~60s so 30s refresh keeps workers always valid.
        """
        HARVEST_INTERVAL = 30.0
        HARVEST_URL = "https://www.walmart.com/cp/movies-tv-shows/4096640"  # quiet category page

        while True:
            try:
                await asyncio.sleep(HARVEST_INTERVAL)
                if not self._context:
                    continue

                # Navigate a low-traffic Walmart page to trigger fresh _px3
                if self._page:
                    try:
                        await self._page.goto(
                            HARVEST_URL,
                            wait_until="domcontentloaded",
                            timeout=15000,
                        )
                        await self._handle_blocked_page()
                        await asyncio.sleep(1.5)
                    except Exception as e:
                        logger.debug("[HARVESTER] Navigation error: %s", e)

                # Extract all cookies from the browser context
                cookies = await self._context.cookies()
                cookie_dict = {c["name"]: c["value"] for c in cookies if "name" in c}

                with self._live_cookies_lock:
                    self._live_cookies = cookie_dict
                    self._live_cookies_timestamp = time.monotonic()

                # Track _px3 freshness
                if "_px3" in cookie_dict:
                    self._px3_timestamp = time.monotonic()
                    logger.debug("[HARVESTER] Fresh cookies harvested (%d total)", len(cookie_dict))
                else:
                    logger.warning("[HARVESTER] _px3 not found in harvested cookies")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("[HARVESTER] Unexpected error: %s", e)

    def get_monitoring_cookies(self) -> dict:
        """
        Return the latest harvested cookies for use by proxy workers.
        Falls back to loading from disk if harvester hasn't run yet.
        """
        with self._live_cookies_lock:
            if self._live_cookies:
                return dict(self._live_cookies)

        # Harvester hasn't run yet — load from disk as fallback
        path = Path(COOKIES_FILE)
        if not path.exists():
            return {}
        try:
            with _cookie_file_lock:
                with open(path, "r") as f:
                    cookie_list = json.load(f)
            if isinstance(cookie_list, list):
                return {c["name"]: c["value"] for c in cookie_list if "name" in c}
            if isinstance(cookie_list, dict):
                return cookie_list
        except Exception as e:
            logger.warning("[SESSION] Could not load fallback cookies: %s", e)
        return {}

    # ------------------------------------------------------------------
    # Cookie persistence
    # ------------------------------------------------------------------

    async def save_cookies(self):
        """Persist browser cookies to disk (thread-safe via module-level lock)."""
        if not self._context:
            return
        try:
            cookies = await self._context.cookies()
            self._cookies_path.parent.mkdir(parents=True, exist_ok=True)
            with _cookie_file_lock:
                with open(self._cookies_path, "w") as f:
                    json.dump(cookies, f, indent=2)
            logger.debug("[SESSION] Cookies saved (%d)", len(cookies))
        except Exception as e:
            logger.warning("[SESSION] Failed to save cookies: %s", e)

    async def _load_cookies(self):
        """Restore cookies from disk into the browser context (thread-safe read)."""
        try:
            with _cookie_file_lock:
                with open(self._cookies_path, "r") as f:
                    cookies = json.load(f)
            if cookies and self._context:
                if not isinstance(cookies, list):
                    logger.warning(
                        "[SESSION] Cookie file has unexpected format (not a list) — skipping add_cookies()"
                    )
                else:
                    valid_cookies = []
                    for c in cookies:
                        if isinstance(c, dict) and "name" in c and "value" in c:
                            valid_cookies.append(c)
                        else:
                            logger.warning(
                                "[SESSION] Skipping malformed cookie entry (missing name/value): %s", c
                            )
                    await self._context.add_cookies(valid_cookies)
                    logger.info("[SESSION] Restored %d cookies from disk", len(valid_cookies))
        except Exception as e:
            logger.warning("[SESSION] Failed to load cookies: %s", e)

    # ------------------------------------------------------------------
    # Page access
    # ------------------------------------------------------------------

    def get_page(self):
        """Return the active Patchright page object for use by purchase executor."""
        return self._page

    def get_context(self):
        """Return the active browser context."""
        return self._context

    def is_ready(self) -> bool:
        return self._page is not None and self._context is not None

    # ------------------------------------------------------------------
    # Blocked page / press-and-hold challenge handler
    # ------------------------------------------------------------------

    async def _handle_blocked_page(self, max_attempts: int = 8) -> bool:
        """
        Detect Walmart's /blocked PerimeterX press-and-hold challenge and solve it
        via mouse simulation. Handles multiple rounds (Walmart often requires 2-3
        completions and may reverse the bar if released early).
        Returns True once cleared, False if all attempts exhausted.
        """
        if not self._page or "/blocked" not in self._page.url:
            return True  # not on a blocked page

        logger.warning("[SESSION] /blocked page detected — attempting press-and-hold solve")
        self._status_cb("[SESSION] Bot challenge detected — solving press-and-hold...")

        # PerimeterX press-and-hold selectors (most specific first)
        _HOLD_SELECTORS = [
            "#px-captcha",
            "div[id*='px-captcha']",
            "div[class*='px-captcha']",
            "div[class*='hold']",
            "div[class*='press']",
            "div[class*='challenge']",
        ]

        for attempt in range(1, max_attempts + 1):
            if "/blocked" not in self._page.url:
                break

            logger.info("[SESSION] Challenge attempt %d/%d", attempt, max_attempts)

            # Fast element scan — 500ms per selector instead of 3s
            target = None
            for sel in _HOLD_SELECTORS:
                try:
                    el = await self._page.wait_for_selector(sel, timeout=500)
                    if el:
                        target = el
                        break
                except Exception:
                    continue

            if target is None:
                # Element not ready yet — short wait and retry
                await asyncio.sleep(0.5)
                continue

            try:
                # Scroll element into view so coordinates are in the visible viewport
                await target.scroll_into_view_if_needed()
                await asyncio.sleep(0.2)

                box = await target.bounding_box()
                if not box:
                    await asyncio.sleep(0.5)
                    continue

                # Aim slightly off-center — dead-center clicks can look robotic
                cx = box["x"] + box["width"] * random.uniform(0.45, 0.55)
                cy = box["y"] + box["height"] * random.uniform(0.45, 0.55)

                # Approach from a nearby point to mimic human cursor travel
                await self._page.mouse.move(
                    cx + random.uniform(-40, 40),
                    cy + random.uniform(-20, 20),
                    steps=12,
                )
                await asyncio.sleep(random.uniform(0.1, 0.25))
                await self._page.mouse.move(cx, cy, steps=6)
                await asyncio.sleep(0.15)
                await self._page.mouse.down()

                # Hold dynamically — make tiny micro-movements while holding
                # so the mouse looks like a real human hand (slight tremor)
                hold_start = time.monotonic()
                while time.monotonic() - hold_start < 20.0:
                    if "/blocked" not in self._page.url:
                        break
                    # Small jitter every ~400ms
                    await asyncio.sleep(random.uniform(0.35, 0.45))
                    if "/blocked" not in self._page.url:
                        break
                    jx = cx + random.uniform(-2, 2)
                    jy = cy + random.uniform(-2, 2)
                    await self._page.mouse.move(jx, jy, steps=2)

                await self._page.mouse.up()

                # Short settle — then loop immediately to catch "Try again" rounds
                await asyncio.sleep(0.8)

            except Exception as e:
                logger.warning("[SESSION] Mouse interaction error: %s", e)
                try:
                    await self._page.mouse.up()
                except Exception:
                    pass
                await asyncio.sleep(0.5)

        cleared = "/blocked" not in self._page.url
        if cleared:
            logger.info("[SESSION] /blocked challenge cleared")
            self._status_cb("[SESSION] Challenge solved — continuing")
        else:
            logger.error("[SESSION] Could not clear /blocked challenge after %d attempts", max_attempts)
            self._status_cb("[SESSION] Challenge unsolved — may need manual intervention in browser")
        return cleared

    async def _find_input(self, selectors: list[str], timeout: int = 10000):
        """Try each selector individually and return the first matching input element.
        Patchright does not support comma-separated selectors in wait_for_selector."""
        if not selectors:
            return None
        per = max(500, timeout // len(selectors))
        for selector in selectors:
            try:
                el = await self._page.wait_for_selector(selector, timeout=per)
                if el:
                    return el
            except Exception:
                continue
        return None

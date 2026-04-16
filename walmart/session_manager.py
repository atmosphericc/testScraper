"""
Walmart session manager — persistent zendriver browser session.

Handles:
  - Browser startup with stealth config (zendriver + real Chrome)
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

import re as _re

from .config import (
    HEADLESS,
    PROFILE_DIR,
    COOKIES_FILE,
    WALMART_LOGIN_URL,
    WALMART_ACCOUNT_URL,
    PX3_MAX_AGE_SECONDS,
    SESSION_VALIDATE_INTERVAL,
    SESSION_MAX_IDLE,
    set_graphql_hash_atf,
    set_graphql_hash_btf,
)
from .logging_manager import get_walmart_logger, log_error, log_activity

logger = logging.getLogger(__name__)
walmart_logger = get_walmart_logger()

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
    Manages a single persistent zendriver browser session for Walmart checkout.

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
        self._browser = None
        self._page = None          # Tab 1: harvester — roams product pages
        self._checkout_page = None # Tab 2: checkout — stays on homepage, clean for purchases
        self._cookies_path = Path(COOKIES_FILE)
        self._profile_dir = Path(PROFILE_DIR)
        self._last_validation: float = 0.0
        self._last_activity: float = time.monotonic()
        self._px3_timestamp: float = 0.0   # when we last saw a fresh _px3 cookie

        # Shared live cookie store — harvester writes, proxy workers read
        self._live_cookies: dict = {}
        self._live_cookies_lock = threading.Lock()
        self._live_cookies_timestamp: float = 0.0

        # Harvester background task + event loop reference
        self._harvester_task: Optional[asyncio.Task] = None
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

        # Stock intercept callback — set by WalmartStockMonitor to receive
        # parsed GraphQL responses captured from the browser's real page loads
        self._stock_intercept_cb: Optional[Callable[[dict], None]] = None
        # Tracks pending GraphQL request IDs → item_id so we can match responses
        self._pending_graphql: dict = {}  # requestId → item_id
        self._pending_lock = threading.Lock()
        # Signaled when a GraphQL response body has been captured, so the
        # harvester loop knows it can safely navigate to the next product.
        self._graphql_captured: Optional[asyncio.Event] = None

        # GraphQL hash refresh flag — set by stock_monitor when HTTP 400 detected
        # Signals harvester to visit a product page and re-discover GRAPHQL_HASH via CDP
        self._graphql_refresh_needed: bool = False

    def set_stock_intercept_callback(self, cb: Callable[[dict], None]):
        """
        Register a callback invoked with raw GraphQL JSON whenever the browser
        loads an ItemByIdBtf or ItemByIdAtf response. The stock monitor uses
        this to extract stock status without needing proxy workers.
        """
        self._stock_intercept_cb = cb

    # ------------------------------------------------------------------
    # Startup / shutdown
    # ------------------------------------------------------------------

    async def start(self):
        """Launch real Chrome via zendriver."""
        try:
            import zendriver as uc
        except ImportError:
            raise RuntimeError(
                "zendriver is not installed. Run: pip install zendriver"
            )

        self._profile_dir.mkdir(parents=True, exist_ok=True)
        Path("walmart/logs").mkdir(parents=True, exist_ok=True)

        self._status_cb("[SESSION] Starting browser...")

        try:
            config = uc.Config(
                user_data_dir=str(self._profile_dir),
                headless=False,
                browser_args=[
                    "--window-size=1920,1080",
                ],
                browser_connection_timeout=1.0,
                browser_connection_max_tries=30,
            )
            self._browser = await uc.start(config)

            # Navigate to Walmart — browser.get() is reliable and returns the tab
            self._page = await self._browser.get("https://www.walmart.com")
            await self._page.activate()

            # Inject stealth script via CDP so it runs on every subsequent document
            from zendriver import cdp
            await self._page.send(
                cdp.page.add_script_to_evaluate_on_new_document(source=_STEALTH_SCRIPT)
            )

            await self._load_cookies()

            # Enable network event monitoring so we can sniff GraphQL hashes
            await self._page.send(cdp.network.enable())
            self._page.add_handler(
                cdp.network.RequestWillBeSent,
                self._on_network_request,
            )
            self._page.add_handler(
                cdp.network.LoadingFinished,
                self._on_loading_finished,
            )

            # Solve /blocked if the initial load triggered PerimeterX
            await self._handle_blocked_page()

            # Tab 2 is opened later via open_checkout_tab(), after warm_session() has
            # run on Tab 1 and established clean cookies. Opening it now would mean
            # Tab 2 starts with cold cookies and is likely to hit /blocked.

        except Exception as e:
            self._page = None
            self._checkout_page = None
            if self._browser:
                try:
                    await self._browser.stop()
                except Exception:
                    pass
                self._browser = None
            raise RuntimeError(f"[SESSION] Failed to start browser: {e}") from e

        self._status_cb("[SESSION] Browser ready")
        logger.debug("[SESSION] Browser started")

    async def open_checkout_tab(self, warmup_url: str = "https://www.walmart.com"):
        """
        Open Tab 2 (checkout tab) after Tab 1 has warmed the session.
        Called by the manager after warm_session() so Tab 2 inherits clean cookies
        and is far less likely to hit the /blocked challenge.

        Tab 2 becomes and stays the foreground/visible tab — this prevents Chrome
        from throttling its JavaScript execution (background tabs get heavily
        throttled, which slows React hydration and makes ATC clicks flaky).

        Args:
            warmup_url: Product page URL to pre-load on Tab 2. Tab 2 stays on
                       that product page to keep React/CSS/JS warm. Saves 8-13s
                       cold start on the next navigation.
        """
        if not self._browser:
            return
        from zendriver import cdp
        self._status_cb("[SESSION] Opening checkout tab...")

        # Open Tab 2 on the warmup URL (product page, not homepage)
        self._checkout_page = await self._browser.get(warmup_url, new_tab=True)
        await self._checkout_page.send(
            cdp.page.add_script_to_evaluate_on_new_document(source=_STEALTH_SCRIPT)
        )
        await asyncio.sleep(0.5)
        await self._handle_blocked_page_on(self._checkout_page)

        # Wire CDP network handlers on Tab 2 so GraphQL hashes are
        # auto-discovered when Tab 2 navigates to product pages during purchase.
        # This eliminates the need for Tab 1 to visit PDPs (sensitive routes).
        try:
            await self._checkout_page.send(cdp.network.enable())
            self._checkout_page.add_handler(
                cdp.network.RequestWillBeSent,
                self._on_network_request,
            )
            logger.debug("[SESSION] CDP network handlers wired on Tab 2")
        except Exception as e:
            logger.warning("[SESSION] Failed to wire network handlers on Tab 2: %s", e)

        # Bring Tab 2 to the foreground and keep it there. This is the tab the
        # user sees and the tab that will handle the purchase, so it must not
        # be backgrounded (Chrome throttles JS in background tabs).
        try:
            await self._checkout_page.activate()
        except Exception as e:
            logger.debug("[SESSION] Tab 2 activate failed: %s", e)

        self._status_cb("[SESSION] Checkout tab ready — pre-loaded on product page (foreground)")
        logger.debug("[SESSION] Checkout tab opened on: %s", warmup_url)

    async def stop(self):
        """Save cookies and close the browser."""
        try:
            await self.save_cookies()
        except Exception:
            pass
        self._page = None
        self._checkout_page = None
        try:
            if self._browser:
                await self._browser.stop()
        except Exception:
            pass
        self._browser = None
        logger.debug("[SESSION] Browser stopped")

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
            await self._page.get(WALMART_LOGIN_URL)
            await self._handle_blocked_page()
            await asyncio.sleep(2)

            # Fill email — try selectors individually
            email_input = await self._find_input(
                ['input[name="email"]', 'input[type="email"]', '#email'], timeout=10000
            )
            if not email_input:
                self._status_cb("[SESSION] Could not find email field")
                return False
            await email_input.set_value(email)
            await asyncio.sleep(0.5)

            # Walmart desktop login shows both email + password simultaneously.
            # Some mobile/variant pages show a "Continue" step — try it but don't fail.
            try:
                els = await self._page.xpath('//button[contains(., "Continue")]')
                if els:
                    await els[0].click()
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
            await password_input.set_value(password)
            await asyncio.sleep(0.5)

            # Submit
            sign_in = None
            for xpath in ['//button[contains(., "Sign In")]', '//button[contains(., "Log in")]', '//button[@type="submit"]']:
                try:
                    els = await self._page.xpath(xpath)
                    if els:
                        sign_in = els[0]
                        break
                except Exception:
                    continue
            if sign_in:
                await sign_in.click()
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
            logger.warning("[SESSION] Login successful")
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
            from zendriver import cdp
            raw = await self._page.send(cdp.network.get_all_cookies())
            cookie_names = {c.name for c in raw}
            if "auth" not in cookie_names:
                self._status_cb("[SESSION] No auth cookie — session invalid. Run walmart_relogin.py.")
                logger.warning("[SESSION] No auth cookie found — not logged in")
                return False

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
        Build a warm PerimeterX behavioral profile using low-risk pages only.

        Product pages (/ip/...) are "sensitive routes" in PerimeterX — they always
        trigger a live server-side risk evaluation regardless of _px3 cookie state,
        meaning navigating to them repeatedly will keep triggering /blocked.

        Instead we warm on homepage → category → search, which builds _pxvid
        reputation and generates valid behavioral signals without the heightened
        scrutiny applied to PDPs.
        """
        if not self._page:
            return

        self._status_cb("[SESSION] Warming session...")
        from zendriver import cdp

        warm_pages = [
            "https://www.walmart.com",
            "https://www.walmart.com/browse/toys/trading-card-games/4171_4191_8134350",
            "https://www.walmart.com/search?q=pokemon+trading+cards",
        ]

        for i, url in enumerate(warm_pages, 1):
            try:
                self._status_cb(f"[SESSION] Warming {i}/{len(warm_pages)}...")
                await self._page.send(cdp.page.navigate(url))
                await asyncio.sleep(random.uniform(5.0, 8.0))
                await self._handle_blocked_page()
                # After each page, check if _px3 appeared — stop early if we have it
                raw = await self._page.send(cdp.network.get_all_cookies())
                cookie_dict = {c.name: c.value for c in raw}
                if "_abck" not in cookie_dict:
                    logger.warning("[SESSION] No _abck cookie — Akamai challenge may have failed")
                if "bm_sz" not in cookie_dict:
                    logger.warning("[SESSION] No bm_sz — sensor.js may use default seed 8888888")
                if "ak_bmsc" not in cookie_dict:
                    logger.warning("[SESSION] No ak_bmsc — Akamai device cache not populated")
                if "_px3" in cookie_dict:
                    with self._live_cookies_lock:
                        self._live_cookies = cookie_dict
                        self._live_cookies_timestamp = time.monotonic()
                    self._px3_timestamp = time.monotonic()
                    logger.warning("[SESSION] _px3 obtained after page %d/%d", i, len(warm_pages))
                    break
            except Exception as e:
                logger.warning("[SESSION] Warm page error (%s): %s", url, e)

        # If still no _px3, poll for up to 20s — it sometimes arrives a few seconds late
        if self._px3_timestamp == 0.0:
            self._status_cb("[SESSION] Waiting for _px3 cookie...")
            for _ in range(20):
                await asyncio.sleep(1.0)
                try:
                    raw = await self._page.send(cdp.network.get_all_cookies())
                    cookie_dict = {c.name: c.value for c in raw}
                    if "_px3" in cookie_dict:
                        with self._live_cookies_lock:
                            self._live_cookies = cookie_dict
                            self._live_cookies_timestamp = time.monotonic()
                        self._px3_timestamp = time.monotonic()
                        logger.warning("[SESSION] _px3 obtained after wait")
                        break
                except Exception:
                    pass

        self._last_activity = time.monotonic()

        if self._px3_timestamp == 0.0:
            logger.warning("[SESSION] _px3 not obtained — proxy workers may get 429s")
            self._status_cb("[SESSION] WARNING: no _px3 cookie — monitoring may fail")
        else:
            self._status_cb("[SESSION] Session warm — ready for checkout")

    def needs_rewarm(self) -> bool:
        """True if the _px3 cookie is stale and we should warm before checkout."""
        return (time.monotonic() - self._px3_timestamp) > PX3_MAX_AGE_SECONDS

    async def rewarm_tab1(self):
        """Quick re-warm of Tab 1 after a purchase flow.

        Navigates Tab 1 to walmart.com, solves any /blocked challenge,
        and refreshes cookies so fetch()-based stock checks work again.
        Much lighter than warm_session() — takes ~3-5s instead of 15-24s.
        """
        if not self._page:
            return
        try:
            from zendriver import cdp
            logger.info("[SESSION] Re-warming Tab 1 after purchase...")
            await self._page.send(cdp.page.navigate("https://www.walmart.com"))
            await asyncio.sleep(random.uniform(2.0, 3.0))
            await self._handle_blocked_page()

            raw = await self._page.send(cdp.network.get_all_cookies())
            cookie_dict = {c.name: c.value for c in raw}
            with self._live_cookies_lock:
                self._live_cookies = cookie_dict
                self._live_cookies_timestamp = time.monotonic()
            if "_px3" in cookie_dict:
                self._px3_timestamp = time.monotonic()
                logger.info("[SESSION] Tab 1 re-warmed — _px3 present, %d cookies", len(cookie_dict))
            else:
                logger.warning("[SESSION] Tab 1 re-warmed but no _px3 — stock checks may still get blocked")
        except Exception as e:
            logger.warning("[SESSION] Tab 1 re-warm failed: %s", e)

    # ------------------------------------------------------------------
    # Cookie harvester — keeps live cookies fresh for proxy workers
    # ------------------------------------------------------------------

    async def harvest_now(self):
        """Immediately snapshot current browser cookies into the live store."""
        if not self._browser:
            return
        try:
            from zendriver import cdp
            # get_all_cookies() returns ALL cookies regardless of current URL,
            # unlike get_cookies() which filters by the current page's URL/domain.
            raw = await self._page.send(cdp.network.get_all_cookies())
            cookie_dict = {c.name: c.value for c in raw}
            with self._live_cookies_lock:
                self._live_cookies = cookie_dict
                self._live_cookies_timestamp = time.monotonic()
            if "_px3" in cookie_dict:
                self._px3_timestamp = time.monotonic()
                logger.debug("[HARVESTER] Snapshot: %d cookies (has _px3)", len(cookie_dict))
            else:
                logger.warning("[HARVESTER] Snapshot: %d cookies — no _px3", len(cookie_dict))
        except Exception as e:
            logger.warning("[HARVESTER] harvest_now failed: %s", e)

    def start_harvester(self, loop: asyncio.AbstractEventLoop):
        """Start the background cookie harvester on the given event loop."""
        self._event_loop = loop
        self._harvester_task = asyncio.run_coroutine_threadsafe(
            self._harvester_loop(), loop
        )

    def stop_harvester(self):
        if self._harvester_task:
            self._harvester_task.cancel()
            self._harvester_task = None

    async def _harvester_loop(self):
        """
        One-time startup warmup on low-risk pages, then idles.
        Re-warms only when _px3 is stale — always using low-risk pages, never PDPs.

        Product pages (/ip/...) are PerimeterX sensitive routes that trigger a live
        server-side risk call on every hit regardless of cookie state, causing
        repeated /blocked challenges. Homepage/category/search are safe alternatives.
        """
        from .config import get_enabled_products

        NAV_WAIT = 4.0
        INTER_PRODUCT_DELAY = 2.0

        # Low-risk pages for re-warming — never product pages (/ip/...).
        # PDPs are PerimeterX "sensitive routes": they always trigger a live
        # server-side risk call regardless of _px3 state, causing /blocked loops.
        REWARM_PAGES = [
            "https://www.walmart.com",
            "https://www.walmart.com/browse/toys/trading-card-games/4171_4191_8134350",
            "https://www.walmart.com/search?q=pokemon+trading+cards",
        ]

        async def _do_warmup():
            # Do NOT call self._page.activate() here — CDP page.navigate() works
            # in background tabs and we must keep Tab 2 (checkout) as the
            # foreground tab so Chrome doesn't throttle its JavaScript execution.

            for url in REWARM_PAGES:
                if not self._browser:
                    return
                try:
                    from zendriver import cdp as _cdp
                    await self._page.send(_cdp.page.navigate(url))
                    await asyncio.sleep(NAV_WAIT)
                    await self._handle_blocked_page()
                except Exception as e:
                    logger.debug("[HARVESTER] Navigation error (%s): %s", url, e)

                try:
                    from zendriver import cdp as _cdp2
                    raw = await self._page.send(_cdp2.network.get_all_cookies())
                    cookie_dict = {c.name: c.value for c in raw}
                    with self._live_cookies_lock:
                        self._live_cookies = cookie_dict
                        self._live_cookies_timestamp = time.monotonic()
                    if "_px3" in cookie_dict:
                        self._px3_timestamp = time.monotonic()
                        logger.debug("[HARVESTER] _px3 refreshed — %d cookies in jar", len(cookie_dict))
                        break  # _px3 obtained — no need to visit more pages
                except Exception as e:
                    logger.debug("[HARVESTER] Cookie snapshot error: %s", e)

                await asyncio.sleep(INTER_PRODUCT_DELAY)

            # Park Tab 1 on the homepage when done, but do NOT re-activate Tab 2
            # here — Tab 2 should already be the foreground tab (we never stole
            # focus from it). If something did steal focus, re-activate explicitly.
            try:
                from zendriver import cdp as _cdp3
                await self._page.send(_cdp3.page.navigate("https://www.walmart.com"))
                await asyncio.sleep(2.0)
                if self._checkout_page:
                    try:
                        await self._checkout_page.activate()
                    except Exception:
                        pass
            except Exception:
                pass

        try:
            # Initial warmup on startup (low-risk pages only)
            await _do_warmup()
            logger.info("[HARVESTER] Startup warmup complete — Tab 1 ready for fetch() stock checks")

            # Cookie keep-alive loop — curl_cffi workers handle stock checking.
            # Re-warm _px3 every 20s via low-risk pages. Workers use these
            # cookies + direct IP (no proxy) to hit PDP pages at 3/sec/product.
            COOKIE_REFRESH_INTERVAL = 45.0

            while True:
                if not self._page:
                    await asyncio.sleep(2.0)
                    continue

                # Check if stock monitor signaled GraphQL hash refresh needed (HTTP 400 detected)
                # Tab 2 now has CDP network handlers — the hash will be auto-discovered
                # on Tab 2's next product page navigation (during purchase). We do NOT
                # navigate Tab 1 to a PDP here — PDPs are PerimeterX sensitive routes
                # that trigger /blocked challenges and pollute Tab 1's behavioral profile.
                if self._graphql_refresh_needed:
                    logger.warning("[HARVESTER] GraphQL hash refresh needed — Tab 2 will re-discover on next purchase navigation")
                    self._status_cb("[HARVESTER] GraphQL hash stale — will refresh on next product page visit")
                    self._graphql_refresh_needed = False

                if self.needs_rewarm():
                    logger.debug("[HARVESTER] _px3 stale — re-warming")
                    await _do_warmup()

                # Snapshot cookies for curl_cffi workers
                try:
                    from zendriver import cdp as _cdp_ck
                    raw = await self._page.send(_cdp_ck.network.get_all_cookies())
                    cookie_dict = {c.name: c.value for c in raw}
                    with self._live_cookies_lock:
                        self._live_cookies = cookie_dict
                        self._live_cookies_timestamp = time.monotonic()
                    if "_px3" in cookie_dict:
                        self._px3_timestamp = time.monotonic()
                        logger.info("[HARVESTER] Cookies refreshed — %d cookies, _px3 present", len(cookie_dict))
                    else:
                        logger.warning("[HARVESTER] Cookie snapshot has NO _px3 — workers may get blocked")
                except Exception as e:
                    logger.warning("[HARVESTER] Cookie snapshot error: %s", e)

                await asyncio.sleep(COOKIE_REFRESH_INTERVAL)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("[HARVESTER] Unexpected error: %s", e)

    async def _check_product_on_tab(self, tab, product: dict):
        """Navigate a tab to a product page and read stock data from __NEXT_DATA__."""
        item_id = product["item_id"]
        name = product.get("name", item_id)
        pdp_url = f"https://www.walmart.com/ip/{item_id}"

        try:
            from zendriver import cdp as _cdp_nav
            await tab.send(_cdp_nav.page.navigate(pdp_url))
            await asyncio.sleep(1.5)

            # Check for /blocked
            tab_url = tab.url or ""
            if "/blocked" in tab_url:
                logger.warning("[HARVESTER] /blocked on PDP for %s — solving", item_id)
                await self._handle_blocked_page_on(tab)
                await asyncio.sleep(1.0)
                # Re-navigate after solving
                await tab.send(_cdp_nav.page.navigate(pdp_url))
                await asyncio.sleep(2.0)

            # Read __NEXT_DATA__ from the DOM
            js = """
            (() => {
                const el = document.getElementById('__NEXT_DATA__');
                if (!el) return null;
                try { return JSON.parse(el.textContent); }
                catch(e) { return null; }
            })()
            """
            data = await tab.evaluate(js, await_promise=False)
            if not data:
                logger.debug("[HARVESTER] No __NEXT_DATA__ for %s", item_id)
                return

            # Extract product info and dispatch to stock monitor
            product_data = (
                data.get("props", {})
                    .get("pageProps", {})
                    .get("initialData", {})
                    .get("data", {})
                    .get("product")
            )
            if product_data and self._stock_intercept_cb:
                # Ensure usItemId is present (stock monitor needs it for matching)
                if "usItemId" not in product_data:
                    product_data["usItemId"] = item_id
                # Wrap in the format on_browser_graphql expects:
                # {item_id, data} where data has data.product
                self._stock_intercept_cb({
                    "item_id": item_id,
                    "data": {"data": {"product": product_data}},
                })
                name_str = product_data.get("name", item_id)[:45]
                avail = product_data.get("availabilityStatus", "?")
                price_info = product_data.get("priceInfo", {}).get("currentPrice", {}).get("price")
                price_str = f"${price_info:.2f}" if price_info else "no_price"
                logger.info("[HARVESTER] %s | %s | %s | %s", item_id, name_str, avail, price_str)
            else:
                logger.info("[HARVESTER] No product data for %s", item_id)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[HARVESTER] Tab check error for %s: %s", item_id, e)


    def get_monitoring_cookies(self) -> dict:
        """
        Return the latest harvested cookies for use by proxy workers.
        Falls back to loading from disk if harvester hasn't run yet.
        """
        with self._live_cookies_lock:
            if self._live_cookies:
                return dict(self._live_cookies)

        # Harvester hasn't run yet — load from disk as fallback, skipping expired cookies
        path = Path(COOKIES_FILE)
        if not path.exists():
            return {}
        try:
            with _cookie_file_lock:
                with open(path, "r") as f:
                    cookie_list = json.load(f)
            now = time.monotonic()
            import time as _time
            now_ts = _time.time()
            if isinstance(cookie_list, list):
                return {
                    c["name"]: c["value"]
                    for c in cookie_list
                    if "name" in c and c.get("expires", -1) > now_ts
                }
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
        if not self._browser or not self._page:
            return
        try:
            from zendriver import cdp
            all_cookies = await self._page.send(cdp.network.get_cookies())
            cookies = [
                {
                    "name": c.name,
                    "value": c.value,
                    "domain": c.domain,
                    "path": c.path,
                    "secure": c.secure,
                    "httpOnly": c.http_only,
                    "sameSite": str(c.same_site) if c.same_site else "None",
                    "expires": float(c.expires) if c.expires else -1,
                }
                for c in all_cookies
            ]
            self._cookies_path.parent.mkdir(parents=True, exist_ok=True)
            with _cookie_file_lock:
                with open(self._cookies_path, "w") as f:
                    json.dump(cookies, f, indent=2)
            logger.debug("[SESSION] Cookies saved (%d)", len(cookies))
        except Exception as e:
            logger.warning("[SESSION] Failed to save cookies: %s", e)

    async def _load_cookies(self):
        """Restore cookies from disk into the browser via CDP (thread-safe read)."""
        try:
            with _cookie_file_lock:
                with open(self._cookies_path, "r") as f:
                    cookies = json.load(f)
            if cookies and self._page:
                if not isinstance(cookies, list):
                    logger.warning(
                        "[SESSION] Cookie file has unexpected format (not a list) — skipping load"
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
                    if valid_cookies:
                        from zendriver import cdp
                        await self._page.send(
                            cdp.network.set_cookies(
                                cookies=[
                                    cdp.network.CookieParam(
                                        name=c["name"],
                                        value=c["value"],
                                        domain=c.get("domain"),
                                        path=c.get("path", "/"),
                                        secure=c.get("secure", False),
                                        http_only=c.get("httpOnly", False),
                                    )
                                    for c in valid_cookies
                                ]
                            )
                        )
                        logger.debug("[SESSION] Restored %d cookies from disk", len(valid_cookies))
        except Exception as e:
            logger.warning("[SESSION] Failed to load cookies: %s", e)

    # ------------------------------------------------------------------
    # Page access
    # ------------------------------------------------------------------

    def get_page(self):
        """Return Tab 1 (harvester tab) — used by the cookie harvester."""
        return self._page

    def get_checkout_page(self):
        """Return Tab 2 (checkout tab) — dedicated clean tab for purchase executor."""
        return self._checkout_page or self._page  # fall back to Tab 1 if Tab 2 not ready

    def get_context(self):
        """Return the active browser (zendriver Browser)."""
        return self._browser

    def is_ready(self) -> bool:
        return self._page is not None and self._browser is not None

    # ------------------------------------------------------------------
    # Blocked page / press-and-hold challenge handler
    # ------------------------------------------------------------------

    async def _handle_blocked_page(self, max_attempts: int = 10) -> bool:
        """Solve /blocked challenge on the harvester tab (Tab 1)."""
        return await self._handle_blocked_page_on(self._page, max_attempts)

    async def _handle_blocked_page_on(self, page, max_attempts: int = 10) -> bool:
        """
        Detect Walmart's /blocked PerimeterX press-and-hold challenge on the given
        tab and solve it.
        """
        if not page or "/blocked" not in (page.url or ""):
            return True  # not on a blocked page

        # 6d: detect checkbox variant — solve differently than press-and-hold
        if "g=a" in (page.url or ""):
            logger.warning("[SESSION] PerimeterX checkbox variant detected (/blocked?g=a) — attempting checkbox solve")
            return await self._solve_checkbox_challenge(page)

        logger.warning("[SESSION] /blocked page detected — attempting press-and-hold solve")
        self._status_cb("[SESSION] Bot challenge detected — solving press-and-hold...")

        from zendriver import cdp

        for attempt in range(1, max_attempts + 1):
            if "/blocked" not in (page.url or ""):
                break

            logger.debug("[SESSION] Challenge attempt %d/%d", attempt, max_attempts)

            # Fast-poll for the captcha element — check both selectors every 300ms
            # instead of waiting 5s per selector sequentially (was 10s worst case).
            target = None
            find_deadline = time.monotonic() + 8.0
            while time.monotonic() < find_deadline:
                try:
                    target = await page.query_selector("#px-captcha") \
                          or await page.query_selector("div[id*='px-captcha']")
                    if target:
                        break
                except Exception:
                    pass
                # Check if we left /blocked while waiting for element
                if "/blocked" not in (page.url or ""):
                    break
                await asyncio.sleep(0.3)

            if target is None:
                continue

            try:
                await target.scroll_into_view()

                box = await target.apply(
                    "(e) => { const r = e.getBoundingClientRect(); "
                    "return {x: r.left, y: r.top, width: r.width, height: r.height}; }"
                )
                if not box:
                    await asyncio.sleep(0.5)
                    continue

                cx = box["x"] + box["width"] * random.uniform(0.45, 0.55)
                cy = box["y"] + box["height"] * random.uniform(0.45, 0.55)

                await page.mouse_move(
                    cx + random.uniform(-30, 30),
                    cy + random.uniform(-15, 15),
                    steps=8,
                )
                await asyncio.sleep(random.uniform(0.05, 0.12))
                await page.mouse_move(cx, cy, steps=4)
                await asyncio.sleep(0.08)

                # 6c: pointerDown before mousePressed
                await page.send(cdp.input_.dispatch_mouse_event(
                    type_="pointerDown",
                    x=cx, y=cy,
                    button=cdp.input_.MouseButton.LEFT,
                    buttons=1, click_count=1,
                ))

                await page.send(cdp.input_.dispatch_mouse_event(
                    type_="mousePressed",
                    x=cx, y=cy,
                    button=cdp.input_.MouseButton.LEFT,
                    buttons=1, click_count=1,
                ))

                # 6b: cumulative random walk jitter
                drift_x = 0.0
                drift_y = 0.0

                hold_start = time.monotonic()
                while time.monotonic() - hold_start < 25.0:
                    elapsed = time.monotonic() - hold_start
                    if elapsed >= 6.0 and "/blocked" not in (page.url or ""):
                        break
                    # 6a: randomized hold loop sleep
                    await asyncio.sleep(random.uniform(0.08, 0.25))
                    elapsed = time.monotonic() - hold_start
                    if elapsed >= 6.0 and "/blocked" not in (page.url or ""):
                        break
                    # 6b: non-uniform random walk
                    drift_x += random.uniform(-3, 5)
                    drift_y += random.uniform(-3, 5)
                    drift_x = max(-20, min(20, drift_x))
                    drift_y = max(-20, min(20, drift_y))
                    jx = cx + drift_x
                    jy = cy + drift_y
                    await page.send(cdp.input_.dispatch_mouse_event(
                        type_="mouseMoved",
                        x=jx, y=jy,
                        button=cdp.input_.MouseButton.LEFT,
                        buttons=1,
                    ))

                await page.send(cdp.input_.dispatch_mouse_event(
                    type_="mouseReleased",
                    x=cx, y=cy,
                    button=cdp.input_.MouseButton.LEFT,
                    buttons=0, click_count=1,
                ))

                # 6c: pointerUp after mouseReleased
                await page.send(cdp.input_.dispatch_mouse_event(
                    type_="pointerUp",
                    x=cx, y=cy,
                    button=cdp.input_.MouseButton.LEFT,
                    buttons=0, click_count=1,
                ))

                await asyncio.sleep(0.5)

            except Exception as e:
                logger.warning("[SESSION] Mouse interaction error: %s", e)
                try:
                    await page.send(cdp.input_.dispatch_mouse_event(
                        type_="mouseReleased", x=0, y=0,
                        button=cdp.input_.MouseButton.LEFT,
                        buttons=0, click_count=1,
                    ))
                except Exception:
                    pass
                await asyncio.sleep(0.5)

        # 6e: secondary success signal — check _px3 cookie in addition to URL
        url_cleared = "/blocked" not in (page.url or "")
        px3_present = False
        try:
            raw = await page.send(cdp.network.get_all_cookies())
            px3_present = any(c.name == "_px3" for c in raw)
        except Exception:
            pass

        cleared = url_cleared or px3_present
        if cleared:
            logger.debug("[SESSION] /blocked challenge cleared")
            self._status_cb("[SESSION] Challenge solved — continuing")
        else:
            logger.error("[SESSION] Could not clear /blocked challenge after %d attempts", max_attempts)
            self._status_cb("[SESSION] Challenge unsolved — may need manual intervention in browser")
        return cleared

    async def _solve_checkbox_challenge(self, page) -> bool:
        """
        Solve PerimeterX checkbox variant challenge (/blocked?g=a).
        Finds the checkbox, clicks it, and waits for redirect back to /checkout or success signal.

        Returns True if challenge solved, False if unsolvable (purchase should abort gracefully).
        """
        logger.info("[SESSION] Starting checkbox challenge solver")
        self._status_cb("[SESSION] Solving checkbox challenge...")

        deadline = time.monotonic() + 20.0  # Increased from 15s to allow for slower redirects
        for attempt in range(1, 8):  # Increased from 5 to 8 attempts
            if time.monotonic() > deadline:
                logger.error("[SESSION] Checkbox solve timeout after %d attempts", attempt - 1)
                self._status_cb("[SESSION] Checkbox challenge timed out — may need manual intervention")
                return False

            # Look for checkbox input or button (multiple selectors for variant rendering)
            checkbox_selectors = [
                'input[type="checkbox"]',
                'input[role="checkbox"]',
                'button[data-testid*="checkbox"]',
                'button[aria-label*="checkbox" i]',
                '[role="checkbox"]',
                'label:has(input[type="checkbox"])',
            ]

            checkbox = None
            for selector in checkbox_selectors:
                try:
                    el = await page.query_selector(selector)
                    if el:
                        checkbox = el
                        logger.debug("[SESSION] Found checkbox with selector: %s", selector)
                        break
                except Exception:
                    continue

            if not checkbox:
                logger.debug("[SESSION] Checkbox not found on attempt %d, waiting...", attempt)
                await asyncio.sleep(1.0)
                continue

            try:
                # Scroll into view and ensure visibility
                await checkbox.scroll_into_view()
                await asyncio.sleep(0.2)

                # Check if element is visible/enabled before clicking
                is_visible = await checkbox.is_visible()
                if not is_visible:
                    logger.warning("[SESSION] Checkbox found but not visible on attempt %d", attempt)
                    await asyncio.sleep(0.5)
                    continue

                # Click the checkbox
                await checkbox.click()
                logger.debug("[SESSION] Checkbox clicked on attempt %d", attempt)
                self._status_cb("[SESSION] Checkbox clicked — waiting for redirect...")

                # Wait for redirect away from /blocked OR for _px3 cookie (success signal)
                # PerimeterX may redirect immediately or after a short delay
                redirect_deadline = time.monotonic() + 10.0  # Increased from 8s
                redirect_detected = False
                px3_obtained = False

                while time.monotonic() < redirect_deadline:
                    # Check URL redirect
                    current_url = page.url or ""
                    if "/blocked" not in current_url:
                        redirect_detected = True
                        logger.info("[SESSION] Checkbox challenge cleared — redirected away from /blocked to: %s", current_url)
                        break

                    # Also check for _px3 cookie (alternative success signal)
                    try:
                        from zendriver import cdp
                        cookies = await page.send(cdp.network.get_all_cookies())
                        if any(c.name == "_px3" for c in cookies):
                            px3_obtained = True
                            logger.info("[SESSION] Checkbox challenge cleared — _px3 cookie obtained")
                            break
                    except Exception:
                        pass

                    await asyncio.sleep(0.3)

                if redirect_detected or px3_obtained:
                    self._status_cb("[SESSION] Checkbox challenge solved")
                    return True

                logger.warning("[SESSION] No redirect or _px3 after checkbox click on attempt %d", attempt)
                # Continue to next attempt instead of immediate fail

            except Exception as e:
                logger.warning("[SESSION] Checkbox click error on attempt %d: %s", attempt, e)
                await asyncio.sleep(0.5)

        logger.error("[SESSION] Could not solve checkbox challenge after max attempts")
        self._status_cb("[SESSION] Checkbox challenge unsolved — purchase will abort")
        return False

    def _on_network_request(self, event):
        """
        CDP RequestWillBeSent handler — sniffs ItemByIdAtf/Btf URLs to:
        1. Auto-discover ATF and BTF GraphQL hashes
        2. Track request IDs so we can capture response bodies for stock data
        """
        try:
            url = event.request.url
            if "ItemByIdAtf" in url:
                m = _re.search(r"/ItemByIdAtf/([a-f0-9]{64})/", url)
                if m:
                    set_graphql_hash_atf(m.group(1))
            if "ItemByIdBtf" in url:
                m = _re.search(r"/ItemByIdBtf/([a-f0-9]{64})/", url)
                if m:
                    set_graphql_hash_btf(m.group(1))
            if "ItemByIdBtf" in url or "ItemByIdAtf" in url:
                m = _re.search(r"/ip/(\d+)", url)
                if m and self._stock_intercept_cb:
                    item_id = m.group(1)
                    with self._pending_lock:
                        self._pending_graphql[event.request_id] = item_id
        except Exception:
            pass

    def _on_loading_finished(self, event):
        """
        CDP LoadingFinished handler — fires after response body is fully buffered.
        At this point get_response_body is guaranteed to succeed (unlike ResponseReceived
        which fires before the body is ready).
        """
        try:
            with self._pending_lock:
                item_id = self._pending_graphql.pop(event.request_id, None)
            if item_id and self._stock_intercept_cb and self._page and self._event_loop:
                asyncio.run_coroutine_threadsafe(
                    self._fetch_graphql_body(event.request_id, item_id),
                    self._event_loop,
                )
        except Exception:
            pass

    async def _fetch_graphql_body(self, request_id, item_id: str):
        """Fetch the response body for a captured GraphQL request and call the stock callback."""
        try:
            from zendriver import cdp
            result = await self._page.send(
                cdp.network.get_response_body(request_id=request_id)
            )
            if result and result.body:
                import json as _json
                try:
                    data = _json.loads(result.body)
                    if self._stock_intercept_cb:
                        logger.info("[HARVESTER] CDP intercepted GraphQL for item %s", item_id)
                        self._stock_intercept_cb({"item_id": item_id, "data": data})
                except Exception as e:
                    logger.warning("[HARVESTER] Failed to parse GraphQL body for %s: %s", item_id, e)
            else:
                logger.warning("[HARVESTER] Empty GraphQL body for item %s", item_id)
        except Exception as e:
            logger.warning("[HARVESTER] get_response_body failed for %s: %s", item_id, e)
        finally:
            # Signal harvester loop that it's safe to navigate to the next product
            if self._graphql_captured:
                self._graphql_captured.set()

    async def _find_input(self, selectors: list[str], timeout: int = 10000):
        """Try each selector individually and return the first matching input element."""
        if not selectors:
            return None
        per = max(0.5, (timeout / len(selectors)) / 1000)
        for selector in selectors:
            try:
                el = await self._page.wait_for(selector=selector, timeout=per)
                if el:
                    return el
            except Exception:
                continue
        return None

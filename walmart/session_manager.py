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
import os
import platform
import random
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Callable

import re as _re

from .config import (
    HEADLESS,
    BROWSER_CHANNEL,
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

# Map Python platform to the Chrome getPlatformInfo OS string
def _chrome_os() -> str:
    p = sys.platform
    if p == "darwin":
        return "mac"
    if p.startswith("win"):
        return "win"
    return "linux"

_CHROME_OS = _chrome_os()

# Injected before every page load to hide automation signals from PerimeterX / HUMAN Security
_STEALTH_SCRIPT = f"""
(function() {{
    // Remove navigator.webdriver
    Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined, configurable: true }});

    // Real Chrome plugins are PluginArray, not a plain array. PerimeterX/HUMAN
    // probe namedItem(), refresh(), and per-plugin mimeTypes — undefined methods
    // or missing sub-objects are fingerprints. Build a fake PluginArray and
    // matching MimeTypeArray.
    const _fakeMimeTypes = [
        {{ type: 'application/pdf', description: 'Portable Document Format', suffixes: 'pdf' }},
        {{ type: 'application/x-google-chrome-pdf', description: 'Portable Document Format', suffixes: 'pdf' }},
        {{ type: 'application/x-nacl', description: 'Native Client Executable', suffixes: '' }},
        {{ type: 'application/x-pnacl', description: 'Portable Native Client Executable', suffixes: '' }}
    ];

    function _makePlugin(name, filename, description, mimeTypeList) {{
        const plugin = {{ name, filename, description, length: mimeTypeList.length }};
        mimeTypeList.forEach((mt, i) => {{
            const mimeWithPlugin = Object.assign({{}}, mt, {{ enabledPlugin: plugin }});
            plugin[i] = mimeWithPlugin;
            plugin[mt.type] = mimeWithPlugin;
        }});
        plugin.item = function(i) {{ return this[i] || null; }};
        plugin.namedItem = function(n) {{ return this[n] || null; }};
        return plugin;
    }}

    const _pluginList = [
        _makePlugin('PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format', _fakeMimeTypes.slice(0, 2)),
        _makePlugin('Chrome PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format', _fakeMimeTypes.slice(0, 2)),
        _makePlugin('Chromium PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format', _fakeMimeTypes.slice(0, 2)),
        _makePlugin('Microsoft Edge PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format', _fakeMimeTypes.slice(0, 2)),
        _makePlugin('WebKit built-in PDF', 'internal-pdf-viewer', 'Portable Document Format', _fakeMimeTypes.slice(0, 2))
    ];

    const _pluginArray = Object.create(Object.getPrototypeOf(navigator.plugins) || Object.prototype);
    _pluginList.forEach((p, i) => {{ _pluginArray[i] = p; _pluginArray[p.name] = p; }});
    Object.defineProperty(_pluginArray, 'length', {{ value: _pluginList.length, enumerable: false }});
    _pluginArray.item = function(i) {{ return this[i] || null; }};
    _pluginArray.namedItem = function(n) {{ return this[n] || null; }};
    _pluginArray.refresh = function() {{}};

    Object.defineProperty(navigator, 'plugins', {{
        get: () => _pluginArray,
        configurable: true
    }});

    // MimeTypeArray — also probed by HUMAN. Each entry's enabledPlugin must point
    // back to one of the entries in navigator.plugins.
    const _mimeArray = Object.create(Object.getPrototypeOf(navigator.mimeTypes) || Object.prototype);
    _fakeMimeTypes.forEach((mt, i) => {{
        const entry = Object.assign({{}}, mt, {{ enabledPlugin: _pluginList[0] }});
        _mimeArray[i] = entry;
        _mimeArray[mt.type] = entry;
    }});
    Object.defineProperty(_mimeArray, 'length', {{ value: _fakeMimeTypes.length, enumerable: false }});
    _mimeArray.item = function(i) {{ return this[i] || null; }};
    _mimeArray.namedItem = function(n) {{ return this[n] || null; }};

    Object.defineProperty(navigator, 'mimeTypes', {{
        get: () => _mimeArray,
        configurable: true
    }});

    // Languages
    Object.defineProperty(navigator, 'languages', {{
        get: () => ['en-US', 'en'],
        configurable: true
    }});

    // chrome.runtime.connect — real Chrome returns a Port object (even for invalid IDs).
    // Throwing immediately is a fingerprint that PerimeterX explicitly probes for.
    function _makeFakePort() {{
        return {{
            name: '',
            sender: undefined,
            disconnect: function() {{}},
            postMessage: function() {{}},
            onMessage: {{ addListener: function() {{}}, removeListener: function() {{}}, hasListener: function() {{ return false; }} }},
            onDisconnect: {{ addListener: function() {{}}, removeListener: function() {{}}, hasListener: function() {{ return false; }} }}
        }};
    }}

    // Capture session-start timing so loadTimes / csi can derive realistic offsets
    // instead of returning constant deltas. PerimeterX correlates these across
    // multiple calls — fixed values are statistically impossible.
    const _sessionStart = performance.now();

    Object.defineProperty(window, 'chrome', {{
        get: () => ({{
            runtime: {{
                connect: function() {{ return _makeFakePort(); }},
                sendMessage: function() {{ return Promise.reject(); }},
                getPlatformInfo: function() {{
                    return Promise.resolve({{ os: '{_CHROME_OS}', arch: 'x86-64' }});
                }},
                onMessage: {{ addListener: function() {{}}, removeListener: function() {{}}, hasListener: function() {{ return false; }} }},
                onConnect: {{ addListener: function() {{}}, removeListener: function() {{}}, hasListener: function() {{ return false; }} }}
            }},
            app: {{
                isInstalled: false,
                InstallState: {{ DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }},
                RunningState: {{ CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' }},
                getDetails: function() {{ return null; }},
                getIsInstalled: function() {{ return false; }}
            }},
            loadTimes: function() {{
                // Derive offsets from real performance.timing when available.
                // Falls back to bounded random variance per call so multiple
                // invocations during one page never return identical deltas.
                const t = (performance && performance.timing) ? performance.timing : null;
                const now = performance.now();
                if (t && t.navigationStart && t.requestStart) {{
                    return {{
                        requestStart: (t.requestStart - t.navigationStart) / 1000,
                        startLoadTime: (t.fetchStart - t.navigationStart) / 1000,
                        commitLoadTime: (t.responseStart - t.navigationStart) / 1000,
                        finishDocumentLoadTime: (t.domContentLoadedEventEnd - t.navigationStart) / 1000,
                        finishLoadTime: (t.loadEventEnd - t.navigationStart) / 1000 || 0,
                        firstPaintTime: (t.domLoading - t.navigationStart) / 1000,
                        firstPaintAfterLoadTime: 0,
                        navigationType: 'Other',
                        wasFetchedViaSpdy: true,
                        wasNpnNegotiated: true,
                        npnNegotiatedProtocol: 'h2',
                        wasAlternateProtocolAvailable: false,
                        connectionInfo: 'h2'
                    }};
                }}
                // Fallback with per-call variance
                const rs = now / 1000 - (1.5 + Math.random() * 1.5);
                return {{
                    requestStart: rs,
                    startLoadTime: rs,
                    commitLoadTime: rs + 0.05 + Math.random() * 0.1,
                    finishDocumentLoadTime: rs + 0.5 + Math.random() * 0.5,
                    finishLoadTime: rs + 1.0 + Math.random() * 1.0,
                    firstPaintTime: rs + 0.4 + Math.random() * 0.3,
                    firstPaintAfterLoadTime: 0,
                    navigationType: 'Other',
                    wasFetchedViaSpdy: true,
                    wasNpnNegotiated: true,
                    npnNegotiatedProtocol: 'h2',
                    wasAlternateProtocolAvailable: false,
                    connectionInfo: 'h2'
                }};
            }},
            csi: function() {{
                // pageLoadTime varies per navigation in real Chrome — fixed 1500 is
                // statistically impossible. Derive from performance.timing when possible.
                const t = (performance && performance.timing) ? performance.timing : null;
                const now = performance.now();
                let pageLoadTime;
                if (t && t.navigationStart && t.domContentLoadedEventEnd) {{
                    pageLoadTime = Math.max(0, t.domContentLoadedEventEnd - t.navigationStart);
                }} else {{
                    pageLoadTime = Math.floor(800 + Math.random() * 2200);
                }}
                return {{
                    pageT: now - _sessionStart,
                    onloadT: t && t.loadEventEnd ? (t.loadEventEnd - t.navigationStart) : Math.floor(500 + Math.random() * 1500),
                    startE: t && t.navigationStart ? t.navigationStart : Date.now() - Math.floor(2000 + Math.random() * 3000),
                    tran: 15,
                    pageLoadTime: pageLoadTime
                }};
            }}
        }}),
        configurable: true
    }});

    // Hardware fingerprint properties — real Chrome reports these
    Object.defineProperty(navigator, 'hardwareConcurrency', {{
        get: () => 4,  // Typical quad-core processor
        configurable: true
    }});

    Object.defineProperty(navigator, 'deviceMemory', {{
        get: () => 8,  // Typical 8GB RAM in modern machines
        configurable: true
    }});

    // navigator.connection — PerimeterX/HUMAN checks for undefined as bot signal,
    // AND probes for value variance across calls (real Chrome updates these on
    // network changes). Fixed downlink/rtt across a 30-min session is a fingerprint.
    // Generate plausible values on each access — bounded so the network looks
    // stable broadband, not a swinging mobile connection.
    function _stableConnection() {{
        // Quantize to discrete-looking values (Chrome rounds these for privacy)
        const downlink = Math.round((9 + Math.random() * 2) * 100) / 100;  // 9.00-11.00
        const rtt = Math.round(35 + Math.random() * 30);  // 35-65ms, integer
        return {{
            effectiveType: '4g',
            downlink: downlink,
            rtt: rtt,
            saveData: false,
            onchange: null,
            addEventListener: function() {{}},
            removeEventListener: function() {{}},
            dispatchEvent: function() {{ return true; }}
        }};
    }}
    Object.defineProperty(navigator, 'connection', {{
        get: () => _stableConnection(),
        configurable: true
    }});

    // navigator.getBattery() — must exist and return a Promise in real Chrome
    if (!navigator.getBattery) {{
        navigator.getBattery = function() {{
            return Promise.resolve({{
                charging: true,
                chargingTime: 0,
                dischargingTime: Infinity,
                level: 1.0,
                addEventListener: function() {{}},
                removeEventListener: function() {{}}
            }});
        }};
    }}

    // screen color depth — real Chrome on modern hardware = 24
    Object.defineProperty(screen, 'colorDepth', {{ get: () => 24, configurable: true }});
    Object.defineProperty(screen, 'pixelDepth', {{ get: () => 24, configurable: true }});

    // outerWidth/outerHeight — headless Chrome defaults to 0, which PerimeterX detects
    if (window.outerWidth === 0) {{
        Object.defineProperty(window, 'outerWidth', {{ get: () => 1920, configurable: true }});
    }}
    if (window.outerHeight === 0) {{
        Object.defineProperty(window, 'outerHeight', {{ get: () => 1040, configurable: true }});
    }}

    // performance.memory — present in real Chrome, absent in some automation contexts
    if (!performance.memory) {{
        Object.defineProperty(performance, 'memory', {{
            get: () => ({{
                jsHeapSizeLimit: 4294705152,
                totalJSHeapSize: 20000000 + Math.floor(Math.random() * 5000000),
                usedJSHeapSize: 10000000 + Math.floor(Math.random() * 3000000)
            }}),
            configurable: true
        }});
    }}
}})();
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
        # Cached outcome of the last validation — both successes and failures
        # are cached for SESSION_VALIDATE_INTERVAL so that a missing-auth-cookie
        # state isn't masked by a stale "recently validated" timestamp.
        self._last_validation_result: bool = False
        self._last_activity: float = time.monotonic()
        self._px3_timestamp: float = 0.0   # when we last saw a fresh _px3 cookie

        # Shared live cookie store — harvester writes, proxy workers read
        self._live_cookies: dict = {}
        self._live_cookies_lock = threading.Lock()
        self._live_cookies_timestamp: float = 0.0

        # Pre-checkout cookie snapshot — captured before Tab 2 enters cart/checkout
        # so we can restore Tab 1's trusted _px3 after Tab 2 inevitably rotates it.
        # Cookies are shared per browser context, so Tab 2's checkout navigations
        # contaminate Tab 1's _px3, causing the stock monitor to BLOCK on resume.
        self._monitor_cookie_snapshot: list = []  # raw cdp.network.Cookie objects

        # Harvester background task + event loop reference
        self._harvester_task: Optional[asyncio.Task] = None
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        # Pause flag — set by purchase_manager during checkout so the harvester
        # stops roaming Tab 1 (which would generate concurrent Walmart traffic
        # while Tab 2 is in the most-scrutinized checkout window).
        self._harvester_paused: bool = False

        # Stock intercept callback — set by WalmartStockMonitor to receive
        # parsed GraphQL responses captured from the browser's real page loads
        self._stock_intercept_cb: Optional[Callable[[dict], None]] = None
        # Tracks pending GraphQL request IDs → item_id so we can match responses.
        # Separate dicts per tab so the body fetch uses the correct CDP session
        # (asking Tab 1 for a request_id that was made on Tab 2 returns no body).
        self._pending_graphql: dict = {}        # Tab 1's pending requests
        self._pending_graphql_tab2: dict = {}   # Tab 2's pending requests
        self._pending_lock = threading.Lock()
        # Bounded staleness — drop entries older than this so a never-completed
        # request can't leak into the dict forever (e.g. cancelled navigation).
        self._pending_max_age_seconds: float = 30.0
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
                headless=HEADLESS,
                browser_channel=BROWSER_CHANNEL,
                browser_args=[
                    "--window-size=1920,1080",
                    # Force Accept-Language to match navigator.languages declared
                    # in the stealth script (['en-US', 'en']). Without --lang,
                    # Chromium derives Accept-Language from the OS locale, which
                    # can mismatch the spoofed navigator.languages and trip
                    # Akamai's fingerprint-consistency check.
                    "--lang=en-US",
                    # Belt-and-suspenders against any AutomationControlled
                    # blink feature being enabled by upstream defaults.
                    "--disable-blink-features=AutomationControlled",
                ],
                browser_connection_timeout=1.0,
                browser_connection_max_tries=30,
            )
            self._browser = await uc.start(config)

            # Extended pre-legitimacy warmup: Build comprehensive browser history BEFORE walmart
            # Akamai's behavioral analysis looks for: navigation history, idle patterns, referrer chains
            self._status_cb("[SESSION] Building browser legitimacy profile (pre-warmup)...")
            try:
                # Randomized site pool — same approach as warm_session() to eliminate
                # the fixed Google→Amazon→Reddit→YouTube→eBay behavioral signature
                _pre_warm_pool = [
                    "https://www.google.com",
                    "https://www.amazon.com",
                    "https://www.reddit.com",
                    "https://www.youtube.com",
                    "https://www.ebay.com",
                    "https://news.ycombinator.com",
                    "https://www.bestbuy.com",
                    "https://www.target.com",
                ]
                _num_sites = random.randint(3, 5)
                _selected = random.sample(_pre_warm_pool, k=_num_sites)
                random.shuffle(_selected)

                warmup = None
                for site in _selected:
                    self._status_cb(f"[SESSION] → {site.split('/')[2]} (legitimacy profile)")
                    if warmup is None:
                        warmup = await self._browser.get(site)
                    else:
                        warmup = await warmup.get(site)
                    await asyncio.sleep(random.uniform(1.2, 2.5))

                # Final idle: simulate real user behavior (thinking, reading)
                self._status_cb("[SESSION] → Idle period (realistic user pause)")
                await asyncio.sleep(random.uniform(2.0, 3.0))

                logger.debug("[SESSION] Pre-legitimacy warmup complete: %d sites", _num_sites)
            except Exception as e:
                logger.debug("[SESSION] Pre-warmup partial failure (non-critical): %s", e)

            # NOW navigate to Walmart — browser has comprehensive history, not cold profile
            self._status_cb("[SESSION] Navigating to Walmart (with legitimacy profile established)")
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

            # CRITICAL: Do NOT check for /blocked immediately.
            # When you manually browse, you don't get /blocked because:
            # 1. Akamai sees persistent cookies from previous sessions
            # 2. Browser has natural idle + scroll patterns
            # 3. Device fingerprint is trusted after initial navigation
            #
            # Instead of solving /blocked here (which signals bot evasion),
            # we let warm_session() happen naturally later, which refreshes
            # the device trust score through normal browsing before sensitive operations.
            # Do NOT call _handle_blocked_page() on first load — it signals automation.

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
        #
        # Both RequestWillBeSent AND LoadingFinished are required — without
        # LoadingFinished, requests captured on Tab 2 would queue in
        # `_pending_graphql` forever (memory leak) and their response bodies
        # would never be fetched (lost stock data). The handler routes through
        # `_on_network_request_tab2` / `_on_loading_finished_tab2` so the
        # body-fetch coroutine knows to use Tab 2's CDP session, not Tab 1's.
        try:
            await self._checkout_page.send(cdp.network.enable())
            self._checkout_page.add_handler(
                cdp.network.RequestWillBeSent,
                self._on_network_request_tab2,
            )
            self._checkout_page.add_handler(
                cdp.network.LoadingFinished,
                self._on_loading_finished_tab2,
            )
            logger.debug("[SESSION] CDP network handlers wired on Tab 2 (req + body)")
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
        """Save cookies and close the browser.

        Also clears restart-tainted state so a subsequent start() comes up
        clean: cancels the harvester task (so it doesn't keep polling on the
        dead browser), drops the live cookie cache + _px3 timestamp (so
        needs_rewarm correctly returns True after restart), empties
        _pending_graphql / _pending_graphql_tab2 (so stale request IDs from
        the dead browser can't collide with new IDs on the next session),
        and drops the monitor cookie snapshot (it references cookies that
        belonged to a now-dead browser context).
        """
        try:
            await self.save_cookies()
        except Exception:
            pass
        # Cancel harvester first so it doesn't keep trying to read self._page
        # after we null it out below. stop_harvester is idempotent so it's
        # safe to call even if start_harvester was never invoked.
        try:
            self.stop_harvester()
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
        # Drop restart-tainted state — see method docstring for rationale.
        with self._pending_lock:
            self._pending_graphql.clear()
            self._pending_graphql_tab2.clear()
        with self._live_cookies_lock:
            self._live_cookies = {}
            self._live_cookies_timestamp = 0.0
        self._px3_timestamp = 0.0
        self._monitor_cookie_snapshot = []
        # Reset validation cache so the post-restart validate_session does a
        # real check instead of returning a stale cached True.
        self._last_validation = 0.0
        self._last_validation_result = False
        logger.debug("[SESSION] Browser stopped — all session state cleared")

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    async def _cdp_click_element(self, el, label: str = "") -> bool:
        """
        Click an element via CDP mouse trajectory — pointer events, not synchronous
        DOM .click(). Used for credential-stuffing-scrutinized buttons (Sign In,
        Continue) where PerimeterX's authentication sensor is most aggressive.

        Returns True if the click was driven via CDP. Falls back to el.click()
        and returns False on rect lookup or CDP failure.
        """
        try:
            from zendriver.cdp import input_ as cdp_input
            rect = await el.apply("""(e) => {
                e.scrollIntoView({ behavior: 'instant', block: 'center' });
                const r = e.getBoundingClientRect();
                return { x: r.x, y: r.y, w: r.width, h: r.height };
            }""")
            if not rect or not rect.get('w', 0) > 0:
                await el.click()
                return False

            x = rect['x'] + rect['w'] / 2 + random.uniform(-3, 3)
            y = rect['y'] + rect['h'] / 2 + random.uniform(-2, 2)

            # Quadratic-Bezier-ish trajectory: 3-6 intermediate points from
            # a random start position toward the target. Curve via a midpoint
            # offset so the path isn't a straight line (which `_abck`'s
            # velocity/curvature analysis flags).
            start_x = random.uniform(200, 1700)
            start_y = random.uniform(150, 900)
            cp_x = (start_x + x) / 2 + random.uniform(-40, 40)
            cp_y = (start_y + y) / 2 + random.uniform(-30, 30)
            steps = random.randint(3, 6)
            for i in range(1, steps + 1):
                t = i / (steps + 1)
                bx = (1 - t) ** 2 * start_x + 2 * (1 - t) * t * cp_x + t ** 2 * x
                by = (1 - t) ** 2 * start_y + 2 * (1 - t) * t * cp_y + t ** 2 * y
                await self._page.send(cdp_input.dispatch_mouse_event(
                    type_="mouseMoved", x=int(bx), y=int(by), pointer_type="mouse"
                ))
                await asyncio.sleep(random.uniform(0.015, 0.045))

            await self._page.send(cdp_input.dispatch_mouse_event(
                type_="mouseMoved", x=x, y=y, pointer_type="mouse"
            ))
            await asyncio.sleep(random.uniform(0.04, 0.10))
            await self._page.send(cdp_input.dispatch_mouse_event(
                type_="mousePressed", x=x, y=y,
                button=cdp_input.MouseButton.LEFT, buttons=1,
                click_count=1, pointer_type="mouse"
            ))
            await asyncio.sleep(random.uniform(0.05, 0.12))
            await self._page.send(cdp_input.dispatch_mouse_event(
                type_="mouseReleased", x=x, y=y,
                button=cdp_input.MouseButton.LEFT, buttons=0,
                click_count=1, pointer_type="mouse"
            ))
            return True
        except Exception as e:
            logger.debug("[SESSION] CDP click on %s failed (%s) — fallback to element.click()", label, e)
            try:
                await el.click()
            except Exception:
                pass
            return False

    async def login(self, email: str, password: str) -> bool:
        """
        Log in to Walmart. Saves cookies on success.
        Returns True on success, False on failure.
        """
        if not self._page:
            raise RuntimeError("Session not started — call start() first")

        self._status_cb("[SESSION] Logging in to Walmart...")
        from zendriver.cdp import input_ as cdp_input
        try:
            await self._page.get(WALMART_LOGIN_URL)
            await self._handle_blocked_page()
            await asyncio.sleep(random.uniform(1.5, 2.5))

            # Fill email — try selectors individually
            email_input = await self._find_input(
                ['input[name="email"]', 'input[type="email"]', '#email'], timeout=10000
            )
            if not email_input:
                self._status_cb("[SESSION] Could not find email field")
                return False
            # Type email char-by-char via CDP to avoid detection
            await email_input.click()
            await asyncio.sleep(random.uniform(0.1, 0.3))
            await email_input.apply("(e) => { e.value = ''; e.focus(); }")
            await asyncio.sleep(random.uniform(0.05, 0.1))
            for char in email:
                await self._page.send(cdp_input.dispatch_key_event(
                    type_="keyDown", text=char, key=char,
                ))
                await asyncio.sleep(random.uniform(0.05, 0.15))
                await self._page.send(cdp_input.dispatch_key_event(
                    type_="keyUp", key=char,
                ))
                await asyncio.sleep(random.uniform(0.08, 0.15))
            await asyncio.sleep(random.uniform(0.3, 0.7))

            # Walmart desktop login shows both email + password simultaneously.
            # Some mobile/variant pages show a "Continue" step — try it but don't fail.
            try:
                els = await self._page.xpath('//button[contains(., "Continue")]')
                if els:
                    await self._cdp_click_element(els[0], "login Continue")
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
            # Type password char-by-char via CDP to avoid detection
            await password_input.click()
            await asyncio.sleep(random.uniform(0.1, 0.3))
            await password_input.apply("(e) => { e.value = ''; e.focus(); }")
            await asyncio.sleep(random.uniform(0.05, 0.1))
            for char in password:
                await self._page.send(cdp_input.dispatch_key_event(
                    type_="keyDown", text=char, key=char,
                ))
                await asyncio.sleep(random.uniform(0.05, 0.15))
                await self._page.send(cdp_input.dispatch_key_event(
                    type_="keyUp", key=char,
                ))
                await asyncio.sleep(random.uniform(0.08, 0.15))
            await asyncio.sleep(random.uniform(0.3, 0.7))

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
                # Credential submission — highest-scrutiny single click in the
                # session. PerimeterX has a dedicated credential-stuffing module
                # that analyzes the click event chain. CDP mouse trajectory is
                # required here.
                await self._cdp_click_element(sign_in, "Sign In")
            await asyncio.sleep(random.uniform(2.5, 3.5))

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

        Caches the LAST validation OUTCOME (not just timestamp) for
        SESSION_VALIDATE_INTERVAL — without this, the previous version
        cached only successes: if validation failed at T0, the cached
        check at T0+1s would short-circuit to True (because the cache
        check only looked at the timestamp), masking a logged-out session.
        """
        if not self._page:
            return False

        now = time.monotonic()

        # Don't re-validate if recently done — return the cached outcome
        if now - self._last_validation < SESSION_VALIDATE_INTERVAL:
            return getattr(self, "_last_validation_result", True)

        # Force re-login if too idle
        if now - self._last_activity > SESSION_MAX_IDLE:
            self._status_cb("[SESSION] Session expired due to inactivity")
            self._last_validation = now
            self._last_validation_result = False
            return False

        try:
            from zendriver import cdp
            raw = await self._page.send(cdp.network.get_all_cookies())
            cookie_names = {c.name for c in raw}
            if "auth" not in cookie_names:
                self._status_cb("[SESSION] No auth cookie — session invalid. Run walmart_relogin.py.")
                logger.warning("[SESSION] No auth cookie found — not logged in")
                self._last_validation = now
                self._last_validation_result = False
                return False

            self._last_validation = now
            self._last_validation_result = True
            self._last_activity = now
            return True
        except Exception as e:
            logger.warning("[SESSION] Validation error: %s", e)
            # Don't cache an exception result — retry on next call
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
        from zendriver.cdp import input_ as cdp_input

        # Pool of external sites to visit for warmup
        # Randomize selection and order to avoid machine-like patterns
        all_warm_sites = [
            "https://www.google.com",
            "https://www.amazon.com",
            "https://www.reddit.com",
            "https://www.youtube.com",
            "https://www.ebay.com",
            "https://www.wikipedia.org",
            "https://www.twitter.com",
            "https://www.instagram.com",
        ]
        # Select 3-5 random sites and shuffle order
        num_sites = random.randint(3, 5)
        selected_sites = random.sample(all_warm_sites, k=num_sites)
        random.shuffle(selected_sites)
        # Always include Walmart home at the start for _px3 generation
        warm_pages = ["https://www.walmart.com"] + selected_sites

        for i, url in enumerate(warm_pages, 1):
            try:
                self._status_cb(f"[SESSION] Warming {i}/{len(warm_pages)}...")
                await self._page.send(cdp.page.navigate(url))
                # Log-normal dwell — humans don't dwell in a flat [5,8]s band on
                # warmup sites. Most pages get a quick scan (2-5s), some get
                # longer reads (occasional 10-15s outliers). Uniform distribution
                # over many cycles is itself a machine-pattern signal.
                # mu=1.4 σ=0.6 → median ~4s, p90 ~9s; clamped [2, 15].
                _dwell = max(2.0, min(15.0, random.lognormvariate(1.4, 0.6)))
                await asyncio.sleep(_dwell)
                await self._handle_blocked_page()

                # Simulate realistic user interaction during warmup
                # (scroll 2-4 times, move mouse 3-6 times, idle for reading)
                for _ in range(random.randint(2, 4)):
                    # Scroll up or down via JS (zendriver lacks dispatch_wheel_event)
                    delta_y = random.uniform(-300, 300)
                    try:
                        await self._page.evaluate(f"window.scrollBy(0, {delta_y})")
                    except Exception:
                        pass
                    await asyncio.sleep(random.uniform(0.5, 1.5))

                # Move mouse around page (simulate reading/browsing)
                for _ in range(random.randint(3, 6)):
                    x = random.uniform(100, 1200)
                    y = random.uniform(100, 700)
                    await self._page.send(cdp_input.dispatch_mouse_event(
                        type_="mouseMoved", x=x, y=y, pointer_type="mouse"
                    ))
                    await asyncio.sleep(random.uniform(0.3, 1.0))

                # Random idle (user reading content)
                await asyncio.sleep(random.uniform(1.0, 3.0))

                # After each page, check if _px3 appeared — stop early if we have it
                raw = await self._page.send(cdp.network.get_all_cookies())
                cookie_dict = {c.name: c.value for c in raw}

                # Validate bm_sz cookie (Akamai sensor seed) — if missing, reload page
                if "bm_sz" not in cookie_dict:
                    logger.warning("[SESSION] No bm_sz cookie — sensor.js using default seed 8888888 (bot signal)")
                    if i == 1:  # First page (Walmart home) — reload to force cookie generation
                        logger.info("[SESSION] Reloading Walmart home page to generate bm_sz cookie")
                        await self._page.send(cdp.page.reload())
                        await asyncio.sleep(2.0)
                        raw = await self._page.send(cdp.network.get_all_cookies())
                        cookie_dict = {c.name: c.value for c in raw}
                        if "bm_sz" in cookie_dict:
                            logger.info("[SESSION] ✓ bm_sz generated after reload")
                        else:
                            logger.warning("[SESSION] bm_sz still missing after reload — Akamai may reject sensor data")

                if "_abck" not in cookie_dict:
                    logger.warning("[SESSION] No _abck cookie — Akamai device fingerprint not generated, challenge may have failed")
                    if i == 1:  # First page — try reload to generate _abck
                        logger.info("[SESSION] Reloading Walmart home page to generate _abck cookie")
                        await self._page.send(cdp.page.reload())
                        await asyncio.sleep(2.0)
                        raw = await self._page.send(cdp.network.get_all_cookies())
                        cookie_dict = {c.name: c.value for c in raw}
                        if "_abck" in cookie_dict:
                            logger.info("[SESSION] ✓ _abck generated after reload")
                        else:
                            logger.warning("[SESSION] _abck still missing — device fingerprint may be untrusted")
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

    async def return_tab1_to_walmart(self):
        """Navigate Tab 1 back to Walmart.com after warm_session leaves it on an external site.

        The stock monitor's fetch(/ip/{item_id}) uses relative URLs that depend on the page
        origin being walmart.com. warm_session() finishes on the last visited external site
        (Google, Amazon, etc), causing stock fetches to 404. This method ensures Tab 1 is
        on Walmart before the monitor starts.
        """
        if not self._page:
            return
        try:
            from zendriver import cdp
            logger.info("[SESSION] Returning Tab 1 to walmart.com for stock monitoring...")
            await self._page.send(cdp.page.navigate("https://www.walmart.com"))
            await asyncio.sleep(1.0)
            logger.info("[SESSION] Tab 1 now on walmart.com — stock monitor ready")
        except Exception as e:
            logger.error("[SESSION] Failed to return Tab 1 to Walmart: %s", e)
            raise

    async def rewarm_tab1(self):
        """Snapshot current Tab 1 cookies without navigation — avoids triggering /blocked.

        After a purchase, the cookies may be slightly stale from Tab 2's checkout flow.
        Instead of navigating and risking detection, we just refresh the cookie cache
        from Tab 1's current state. A real user wouldn't actively re-browse after
        completing a purchase — they'd just leave the tab idle or close it.
        """
        if not self._page:
            return
        try:
            from zendriver import cdp
            logger.debug("[SESSION] Snapshotting Tab 1 cookies (no navigation)...")
            raw = await self._page.send(cdp.network.get_all_cookies())
            cookie_dict = {c.name: c.value for c in raw}
            with self._live_cookies_lock:
                self._live_cookies = cookie_dict
                self._live_cookies_timestamp = time.monotonic()
            if "_px3" in cookie_dict:
                self._px3_timestamp = time.monotonic()
                logger.info("[SESSION] Tab 1 cookies snapshotted — _px3 present, %d cookies", len(cookie_dict))
            else:
                logger.warning("[SESSION] Tab 1 snapshot found no _px3 — stock checks may be at risk")
        except Exception as e:
            logger.warning("[SESSION] Tab 1 snapshot failed: %s (continuing anyway)", e)

    # ------------------------------------------------------------------
    # Pre-checkout cookie isolation — protects monitor _px3 from Tab 2 rotation
    # ------------------------------------------------------------------

    async def snapshot_monitor_cookies(self):
        """Snapshot the entire browser-context cookie jar before Tab 2 enters checkout.

        Cookies are shared per browser context, so Tab 2's cart/checkout/cart-clear
        navigations rotate _px3 with PerimeterX-flagged values. Capturing the trusted
        cookie set here lets us restore it after checkout so the stock monitor's next
        fetch on Tab 1 sends the un-poisoned _px3.
        """
        if not self._page:
            return
        try:
            from zendriver import cdp
            raw = await self._page.send(cdp.network.get_all_cookies())
            self._monitor_cookie_snapshot = list(raw)
            has_px3 = any(c.name == "_px3" for c in raw)
            logger.info(
                "[SESSION] Monitor cookie snapshot taken — %d cookies (has _px3: %s)",
                len(raw), has_px3,
            )
        except Exception as e:
            logger.warning("[SESSION] snapshot_monitor_cookies failed: %s", e)
            self._monitor_cookie_snapshot = []

    # Cookie names that PerimeterX / Akamai rotate during checkout navigation.
    # These are the only cookies we delete + restore — cart/session cookies that
    # Tab 2 legitimately updated are left alone so the user stays logged in.
    _PX_AKAMAI_COOKIE_NAMES = (
        "_px3", "_px", "_pxhd", "_pxvid", "_pxff_cc", "_pxde",
        "_abck", "bm_sz", "bm_sv",
    )

    async def restore_monitor_cookies(self):
        """Restore the pre-checkout cookie jar after Tab 2 finishes its purchase flow.

        Replaces _px3 (and any other PerimeterX/Akamai-rotated cookies) with the
        trusted values captured in snapshot_monitor_cookies(). Without this, the
        stock monitor's next fetch on Tab 1 sends Tab 2's poisoned _px3 and gets
        BLOCKED, tripping the circuit breaker.
        """
        if not self._page or not self._monitor_cookie_snapshot:
            logger.warning(
                "[SESSION] restore_monitor_cookies: skipping (page=%s, snapshot_len=%d)",
                bool(self._page), len(self._monitor_cookie_snapshot),
            )
            return
        try:
            from zendriver import cdp

            # Delete the contaminated PX/Akamai cookies first so the restore
            # doesn't collide with existing entries of the same name+domain+path.
            for cname in self._PX_AKAMAI_COOKIE_NAMES:
                try:
                    await self._page.send(cdp.network.delete_cookies(
                        name=cname,
                        domain=".walmart.com",
                    ))
                except Exception as e:
                    logger.debug("[SESSION] delete_cookies(%s) failed: %s", cname, e)

            # Re-inject the snapshot. CookieParam.expires expects TimeSinceEpoch
            # (a float subclass with .to_json()), but Cookie.expires is a plain
            # float — must wrap it explicitly or set_cookies fails silently.
            params = []
            skipped = []
            for c in self._monitor_cookie_snapshot:
                if c.name not in self._PX_AKAMAI_COOKIE_NAMES:
                    continue
                try:
                    expires_val = None
                    if c.expires is not None:
                        # Wrap the raw float into TimeSinceEpoch so to_json() works.
                        expires_val = cdp.network.TimeSinceEpoch(c.expires)
                    params.append(cdp.network.CookieParam(
                        name=c.name,
                        value=c.value,
                        domain=c.domain,
                        path=c.path or "/",
                        secure=c.secure,
                        http_only=c.http_only,
                        expires=expires_val,
                        same_site=c.same_site,
                    ))
                except Exception as e:
                    skipped.append((c.name, str(e)))

            if skipped:
                logger.warning("[SESSION] restore_monitor_cookies: skipped %d cookies: %s", len(skipped), skipped)

            if params:
                await self._page.send(cdp.network.set_cookies(cookies=params))
                # Refresh the live cookie cache so harvester reads see the restore
                with self._live_cookies_lock:
                    self._live_cookies = {p.name: p.value for p in params}
                    # Merge non-PX cookies from current state to keep cache complete
                    try:
                        current = await self._page.send(cdp.network.get_all_cookies())
                        for c in current:
                            self._live_cookies.setdefault(c.name, c.value)
                    except Exception:
                        pass
                    self._live_cookies_timestamp = time.monotonic()
                self._px3_timestamp = time.monotonic()
                restored_names = sorted({p.name for p in params})
                logger.info(
                    "[SESSION] Monitor cookies restored — %d PX/Akamai cookies re-injected: %s",
                    len(params), restored_names,
                )
            else:
                logger.warning("[SESSION] restore_monitor_cookies: snapshot had no PX/Akamai cookies to restore")
        except Exception as e:
            logger.exception("[SESSION] restore_monitor_cookies failed: %s", e)

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

    def pause_harvester(self):
        """Pause harvester roaming during checkout — call before Tab 2 enters cart/checkout."""
        self._harvester_paused = True

    def resume_harvester(self):
        """Resume harvester roaming after checkout completes."""
        self._harvester_paused = False

    @property
    def harvester_paused(self) -> bool:
        return self._harvester_paused

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
        # Override WALMART_REWARM_PAGES env var (comma-separated URLs) to use different browse pages.
        _default_rewarm = (
            "https://www.walmart.com"
            "|https://www.walmart.com/browse/electronics"
            "|https://www.walmart.com/browse/toys-games"
        )
        _rewarm_env = os.environ.get("WALMART_REWARM_PAGES", _default_rewarm)
        ALL_REWARM_PAGES = [u.strip() for u in _rewarm_env.split("|") if u.strip()]

        async def _do_warmup():
            # Randomize selection (2-3 pages) and order to break behavioral patterns
            warmup_count = random.randint(2, 3)
            REWARM_PAGES = random.sample(ALL_REWARM_PAGES, min(warmup_count, len(ALL_REWARM_PAGES)))
            random.shuffle(REWARM_PAGES)

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

                # While the purchase_manager is in checkout, pause all harvester
                # activity. Roaming Tab 1 during the most-scrutinized window adds
                # concurrent Walmart traffic that contributes to PerimeterX scoring
                # for the shared browser context. We do NOT cancel pending hash
                # refreshes; we just defer them until checkout finishes.
                if self._harvester_paused:
                    await asyncio.sleep(1.0)
                    continue

                # Check if stock monitor signaled GraphQL hash refresh needed (HTTP 400 detected)
                if self._graphql_refresh_needed:
                    logger.warning("[HARVESTER] GraphQL hash stale (HTTP 400 detected) — refreshing hash via product page visit")
                    self._status_cb("[HARVESTER] Refreshing GraphQL hash...")
                    self._graphql_refresh_needed = False

                    # Visit a product page to trigger GraphQL fetch and re-discover hash via CDP intercept
                    try:
                        products = get_enabled_products()
                        if products:
                            product = products[0]  # Use first product as test
                            item_id = product.get("item_id")
                            product_url = f"https://www.walmart.com/ip/{item_id}"
                            logger.info("[HARVESTER] Navigating to product %s to refresh GraphQL hash", item_id)
                            from zendriver import cdp as _cdp_hash
                            await self._page.send(_cdp_hash.page.navigate(product_url))
                            await asyncio.sleep(5.0)  # Wait for GraphQL response to be intercepted
                            logger.info("[HARVESTER] GraphQL hash refresh attempt complete — new hash should be set")
                        else:
                            logger.warning("[HARVESTER] No products in config — cannot refresh hash")
                    except Exception as e:
                        logger.error("[HARVESTER] GraphQL hash refresh failed: %s", e)

                    # Park back on homepage
                    try:
                        from zendriver import cdp as _cdp_home
                        await self._page.send(_cdp_home.page.navigate("https://www.walmart.com"))
                        await asyncio.sleep(2.0)
                    except Exception:
                        pass

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

                # Fat-tail idle distribution — real browsers don't refresh on
                # a uniform [35,55]s schedule. Most cycles are short (40-60s),
                # but occasional long idles (90-180s) match human browsing.
                # 35s base floor + Pareto-shaped extension keeps the mean near
                # the previous ~45s while breaking the uniform-distribution signal.
                _base = 35.0 + random.expovariate(1 / 12.0)  # exp mean ~12s, range mostly 35-90s
                if random.random() < 0.10:  # 10% of cycles get an extra long-tail
                    _base += random.uniform(40.0, 120.0)
                await asyncio.sleep(min(_base, 240.0))

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
            now_ts = time.time()
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
        """Persist browser cookies to disk (thread-safe via module-level lock).

        Uses `get_all_cookies()` instead of `get_cookies()` — the latter is
        URL-filtered to the current page's origin, so if Tab 1 happened to be
        on an external warmup site (Google/Amazon) at save time, the saved
        file would contain THOSE cookies, not Walmart's. `get_all_cookies()`
        ignores the current URL and returns every cookie in the browser jar.

        Write goes through a tempfile + os.replace to be crash-safe — same
        atomic-write pattern PM3 applied to walmart_config.json. A SIGKILL
        mid-write can no longer leave a half-written cookie file that
        _load_cookies would JSON-decode-fail on the next start.
        """
        if not self._browser or not self._page:
            return
        try:
            from zendriver import cdp
            all_cookies = await self._page.send(cdp.network.get_all_cookies())
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
            tmp_path = self._cookies_path.with_name(
                f".{self._cookies_path.name}.tmp.{os.getpid()}"
            )
            try:
                with _cookie_file_lock:
                    with open(tmp_path, "w") as f:
                        json.dump(cookies, f, indent=2)
                        f.flush()
                        try:
                            os.fsync(f.fileno())
                        except OSError:
                            pass
                    os.replace(tmp_path, self._cookies_path)
            finally:
                # Best-effort cleanup if os.replace didn't happen (exception path)
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
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
                await asyncio.sleep(random.uniform(0.06, 0.15))

                await page.send(cdp.input_.dispatch_mouse_event(
                    type_="mousePressed",
                    x=cx, y=cy,
                    button=cdp.input_.MouseButton.LEFT,
                    buttons=1, click_count=1,
                ))

                # 6b: aggressive hold loop — continuous micro-movements to signal human interaction
                drift_x = 0.0
                drift_y = 0.0

                hold_start = time.monotonic()
                while time.monotonic() - hold_start < 25.0:
                    elapsed = time.monotonic() - hold_start
                    if elapsed >= 6.0 and "/blocked" not in (page.url or ""):
                        break
                    # Micro-movements every 20-80ms with continuous drift
                    await asyncio.sleep(random.uniform(0.02, 0.08))
                    elapsed = time.monotonic() - hold_start
                    if elapsed >= 6.0 and "/blocked" not in (page.url or ""):
                        break
                    # Cumulative drift with bias toward edges (more natural motion)
                    drift_x += random.uniform(-5, 8)
                    drift_y += random.uniform(-5, 8)
                    drift_x = max(-25, min(25, drift_x))
                    drift_y = max(-25, min(25, drift_y))
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

                await asyncio.sleep(random.uniform(0.3, 0.8))

            except Exception as e:
                logger.warning("[SESSION] Mouse interaction error: %s", e)
                await asyncio.sleep(random.uniform(0.3, 0.8))

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
            logger.info("[SESSION] /blocked challenge cleared — url_cleared=%s, px3_present=%s", url_cleared, px3_present)
            self._status_cb("[SESSION] Challenge solved — continuing")
        else:
            current_url = page.url or "unknown"
            logger.error("[SESSION] Challenge unsolved after %d attempts — URL: %s, _px3: %s", max_attempts, current_url, px3_present)
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
        """RequestWillBeSent on Tab 1 — see _enqueue_pending_graphql."""
        self._enqueue_pending_graphql(event, self._pending_graphql)

    def _on_network_request_tab2(self, event):
        """RequestWillBeSent on Tab 2 — same as Tab 1 but routes the body
        fetch through the Tab 2 CDP session."""
        self._enqueue_pending_graphql(event, self._pending_graphql_tab2)

    def _enqueue_pending_graphql(self, event, pending: dict):
        """
        Common logic for both tabs' RequestWillBeSent handler:
        1. Auto-discover ATF and BTF GraphQL hashes from the URL
        2. Track request IDs (with enqueue time for staleness pruning)
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
                    now = time.monotonic()
                    with self._pending_lock:
                        pending[event.request_id] = (item_id, now)
                        # Opportunistic cleanup — prune entries older than
                        # _pending_max_age_seconds across BOTH dicts so a
                        # never-completing request can't leak forever.
                        cutoff = now - self._pending_max_age_seconds
                        for d in (self._pending_graphql, self._pending_graphql_tab2):
                            stale = [rid for rid, (_iid, ts) in d.items() if ts < cutoff]
                            for rid in stale:
                                d.pop(rid, None)
        except Exception:
            pass

    def _on_loading_finished(self, event):
        """LoadingFinished on Tab 1."""
        self._schedule_body_fetch(event, self._pending_graphql, self._page)

    def _on_loading_finished_tab2(self, event):
        """LoadingFinished on Tab 2."""
        self._schedule_body_fetch(event, self._pending_graphql_tab2, self._checkout_page)

    def _schedule_body_fetch(self, event, pending: dict, page):
        """Pop the matching request ID and schedule the body fetch on the right tab."""
        try:
            with self._pending_lock:
                entry = pending.pop(event.request_id, None)
            if not entry:
                return
            item_id, _ts = entry
            if item_id and self._stock_intercept_cb and page and self._event_loop:
                asyncio.run_coroutine_threadsafe(
                    self._fetch_graphql_body(event.request_id, item_id, page),
                    self._event_loop,
                )
        except Exception:
            pass

    async def _fetch_graphql_body(self, request_id, item_id: str, page=None):
        """Fetch the response body for a captured GraphQL request and call the stock callback.

        `page` parameter routes the body fetch to the tab that captured the
        request. Defaults to Tab 1 for backward compatibility with any direct
        callers.
        """
        if page is None:
            page = self._page
        if page is None:
            return
        try:
            from zendriver import cdp
            result = await page.send(
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

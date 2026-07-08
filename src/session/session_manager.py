#!/usr/bin/env python3
"""
Persistent Session Manager - Maintains long-lived browser context for Target.com
Uses nodriver (undetected Chrome) for stealth automation.
"""

import json
import time
import base64
import asyncio
import logging
import threading
import random
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

import zendriver as uc

# Module-level Chrome PID — set at browser launch so it survives even if self.browser is cleared.
# Used by close_browser_sync() to kill the entire Chrome process tree on Windows.
_chrome_pid: Optional[int] = None


class SessionManager:
    """Manages persistent browser session for Target.com automation using nodriver"""

    def __init__(self, session_path: str = "target.json",
                 user_data_dir: "str | Path | None" = None,
                 proxy_url: "str | None" = None,
                 account_id: "str | None" = None,
                 timezone: "str | None" = None,
                 apply_fingerprint: bool = False):
        """
        session_path: where to read/write the cookie+fingerprint JSON.
        user_data_dir: Chrome profile directory. Default ./nodriver-profile
            (preserves single-worker behavior). Phase 6 (worker pool) passes
            per-worker dirs like ./nodriver-profile-1, ./nodriver-profile-2.
        proxy_url: optional Chrome --proxy-server value (e.g. 127.0.0.1:23001
            fronting this account's Bright Data ISP IP, or a plain host:port).
            None = exit the home IP (legacy single-account behavior). Multi-
            account drops set this per worker so N accounts don't share one IP.
        """
        self.session_path = Path(session_path)
        self.proxy_url = (str(proxy_url).strip() or None) if proxy_url else None
        # Per-account device fingerprint (multi-account). When apply_fingerprint
        # is set, initialize() re-applies the SAME deterministic CDP identity the
        # harvester logged in under (build_identity is keyed on account_id+tz), so
        # the purchase browser's fingerprint matches the harvested session's.
        self.account_id = account_id
        self.account_timezone = (str(timezone).strip() or None) if timezone else None
        self.apply_fingerprint = bool(apply_fingerprint) and bool(account_id)
        # Global kill-switch. The deterministic account_identity build can drift from
        # the real installed Chrome (e.g. it spoofs Chrome 130 on a Chrome 149 host, or
        # macOS-on-Windows) — an incoherent UA/JA3 that Target's Shape flags on BOTH the
        # login and purchase surfaces (confirmed 2026-06-24: blocked credential logins,
        # and untested on ATC). TARGET_APPLY_FINGERPRINT=0 falls back to the real browser
        # identity everywhere until account_identity is pinned to the real Chrome major +
        # host OS and the canvas/navigator tampering is dropped.
        if os.environ.get('TARGET_APPLY_FINGERPRINT', '1').lower() in ('0', 'false', 'no'):
            self.apply_fingerprint = False
        self.logger = logging.getLogger(__name__)

        # CRITICAL: User data directory - nodriver persists profile here
        self.user_data_dir = Path(user_data_dir) if user_data_dir else Path("./nodriver-profile")
        self.user_data_dir.mkdir(exist_ok=True)

        # nodriver instances
        self.browser: Optional[uc.Browser] = None
        self._active_tab = None   # Main tab reference
        self._chrome_pid: Optional[int] = None  # this instance's Chrome PID (per-worker)
        # asyncio.Lock, NOT threading.Lock (2026-07-03 drop root cause): get_page
        # holds this across awaits on the worker's event loop. A threading.Lock
        # here deadlocks the WHOLE loop — coroutine A parks on an await inside
        # the lock, coroutine B's synchronous `with lock:` then blocks the loop
        # thread, so A can never resume to release it. With the Session Sentinel
        # (5-min ensure_logged_in) + keepalive + purchase all calling get_page on
        # the same loop, this froze all 3 workers before the 07-03 drop.
        self._context_lock = asyncio.Lock()

        # Session state
        self.session_active = False
        self.last_validation = None
        self.session_created_at = None
        self.validation_failures = 0

        # Purchase lock to prevent validation during active purchases
        self.purchase_in_progress = False
        self._purchase_lock = threading.Lock()
        # Drop-guard (2026-07-05): the PurchaseExecutor's _page_lock, wired in
        # by Worker.build_components. Self-heal paths acquire it before
        # restarting the browser so they can never kill Chrome under an
        # in-flight ATC/checkout. None in single-piece setups (tests, tools).
        self._drop_guard_lock = None
        # Serialize refresh_session: the manager retry-path, watchdog
        # escalation, and sentinel can all request a restart around the same
        # failure — two concurrent initialize() calls on one profile dir spawn
        # two Chromes and both fail to connect.
        self._refresh_lock = asyncio.Lock()
        self._watchdog_task_obj = None   # the watchdog's own asyncio.Task (self-cancel guard)
        self._escalation_task = None     # detached recovery task (single-flight)

        # Access-token keep-fresh (2026-07-07 post-mortem): carts.target.com
        # WRITES (ATC / checkout / cart-clear) need a live MEMBER accessToken
        # JWT. The 07-07 restocks were lost to 2h of ATC 401 _ERR_AUTH_DENIED
        # on accounts whose /account page still looked logged in — nothing in
        # the stack refreshed the token (bot fetches bypass the site JS that
        # owns refresh-on-401). These track token repairs and rate-gate the
        # DESTRUCTIVE credential relogin (full_signout wipes the jar; when the
        # follow-up login is Shape-blocked the account is left signed out —
        # 'business' burned in exactly that loop every 5-min tick on 07-07).
        self._last_token_repair_ts = 0.0
        self._relogin_attempt_times: list = []
        self._relogin_capped_alerted = False

        # Configuration
        self.max_validation_failures = 3
        self.validation_timeout = 30000  # 30 seconds

        # Context lifecycle management (kept for stats compatibility)
        self._initialization_attempts = 0
        self._max_init_attempts = 3
        self._context_recreation_count = 0
        self._last_context_recreation = None

        # Event loop for thread-safe async operations
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

        # SessionStorage for restoration
        self._session_storage: Optional[str] = None

        # CDP cookie interception
        self._cdp_cookie_interception_active = False

        # Cookie watchdog
        self._cookie_watchdog_task = None
        self._cookie_watchdog_running = False

        # Fingerprint data for consistent sessions
        self.fingerprint_data = self._load_fingerprint_data()

    @property
    def context(self):
        """Backward compatibility: returns browser when session active, None otherwise"""
        return self.browser if self.session_active and self.browser else None

    def _load_fingerprint_data(self) -> Dict[str, Any]:
        """Load consistent fingerprint data from session file"""
        try:
            if self.session_path.exists():
                with open(self.session_path, 'r', encoding='utf-8') as f:
                    session_data = json.load(f)
                    fingerprint = session_data.get('fingerprint', {})
                    if fingerprint:
                        self.logger.info(f" Loaded fingerprint from {self.session_path}")
                        return fingerprint
        except Exception as e:
            self.logger.warning(f"Could not load fingerprint: {e}")

        # Generate random but realistic fingerprint
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        ]
        viewports = [
            {'width': 1920, 'height': 1080},
            {'width': 1536, 'height': 864},
            {'width': 1440, 'height': 900},
            {'width': 1680, 'height': 1050},
            {'width': 2560, 'height': 1440},
        ]

        selected_ua = random.choice(user_agents)
        selected_viewport = random.choice(viewports)

        if 'Windows' in selected_ua:
            selected_timezone = random.choice([
                'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles'
            ])
        elif 'Macintosh' in selected_ua:
            selected_timezone = random.choice(['America/Los_Angeles', 'America/New_York'])
        else:
            selected_timezone = 'America/New_York'

        fingerprint = {
            'user_agent': selected_ua,
            'viewport': selected_viewport,
            'timezone': selected_timezone,
            'locale': 'en-US'
        }

        self.logger.info(f"Generated new fingerprint: {selected_viewport['width']}x{selected_viewport['height']}")
        return fingerprint

    async def initialize(self) -> bool:
        """Initialize persistent browser session with nodriver (undetected Chrome)"""
        self._initialization_attempts += 1

        try:
            self.logger.info(f"[INIT] Initializing session (attempt {self._initialization_attempts}/{self._max_init_attempts})...")

            if self.session_path.exists():
                file_size = os.path.getsize(self.session_path)
                file_mtime = datetime.fromtimestamp(os.path.getmtime(self.session_path))
                self.logger.info(f"[INIT] Found session file: {self.session_path} ({file_size} bytes, modified: {file_mtime.strftime('%Y-%m-%d %H:%M:%S')})")

                try:
                    with open(self.session_path, 'r', encoding='utf-8') as f:
                        session_data = json.load(f)
                        saved_at = session_data.get('saved_at', 'unknown')
                        self.logger.info(f"[INIT] Session was saved at: {saved_at}")
                        self._session_storage = session_data.get('sessionStorage', None)
                        self._saved_local_storage = session_data.get('origins', [])
                except Exception:
                    pass
            else:
                self.logger.info(f"[INIT] No session file found at {self.session_path}")

            # Store event loop for thread-safe operations
            self._event_loop = asyncio.get_running_loop()
            self.logger.info("[OK] Event loop stored")

            # Clean up any existing resources
            await self._safe_cleanup()

            # Launch nodriver browser (undetected Chrome)
            self.logger.info(f"[INIT] Launching nodriver with profile: {self.user_data_dir}")
            print(f"[SESSION_INIT] Using nodriver persistent profile: {self.user_data_dir}")
            print(f"[SESSION_INIT] Cookies persist via Chrome profile automatically!")
            print(f"[SESSION_INIT] UNDETECTED CHROME MODE - no automation detection!")

            import platform as _platform
            _browser_args = ['--window-size=1920,1080']
            # Anti-idle flags (2026-07-06): on two consecutive overnight runs
            # every purchase Chrome's CDP went dead around midnight while the
            # browsers sat idle/backgrounded (07-03 23:50, 07-06 00:21) —
            # consistent with Chrome/Windows suspending backgrounded renderers
            # and timers. These flags keep background tabs/timers alive. They
            # are Chrome-internal scheduling switches with no JS-visible
            # fingerprint surface. Kill-switch: TARGET_ANTI_IDLE_FLAGS=0.
            if os.environ.get('TARGET_ANTI_IDLE_FLAGS', '1').lower() not in ('0', 'false', 'no'):
                _browser_args += [
                    '--disable-background-timer-throttling',
                    '--disable-backgrounding-occluded-windows',
                    '--disable-renderer-backgrounding',
                    '--disable-features=HighEfficiencyModeAvailable,BatterySaverModeAvailable',
                ]
                print("[SESSION_INIT] Anti-idle flags ON (background throttling disabled)")
            # Per-account exit IP: route this browser through its forwarder/proxy
            # so N racing accounts don't all correlate on one home IP. Chrome
            # takes a bare host:port here (auth is handled by the local forwarder).
            if self.proxy_url:
                _browser_args.append(f'--proxy-server={self.proxy_url}')
                print(f"[SESSION_INIT] Exit proxy: --proxy-server={self.proxy_url}")
            _config = uc.Config(
                user_data_dir=str(self.user_data_dir.resolve()),
                headless=False,
                browser_args=_browser_args,
                # sandbox=True causes "Failed to connect to browser" on macOS
                sandbox=_platform.system() != "Darwin",
                # Increase connection retry window — macOS Chrome startup takes ~3-4s
                # but the default timeout (0.25s × 10 tries = 2.5s) expires too fast.
                browser_connection_timeout=1.0,
                browser_connection_max_tries=30,
            )
            self.browser = await uc.start(_config)

            self.logger.info("[OK] nodriver browser launched successfully")
            print("[SESSION_INIT] Browser launched - STEALTH MODE ACTIVE!")

            # Store Chrome PID globally so shutdown can kill the whole process tree
            global _chrome_pid
            _chrome_pid = getattr(self.browser, '_process_pid', None)
            # Per-instance copy too — the module global is last-writer-wins
            # across N workers, so _safe_cleanup must use its OWN pid.
            self._chrome_pid = _chrome_pid
            if _chrome_pid:
                print(f"[SESSION_INIT] Chrome PID stored: {_chrome_pid}")

            # Get or create main tab
            if self.browser.tabs:
                self._active_tab = self.browser.tabs[0]
                print(f"[SESSION_INIT] Using existing tab ({len(self.browser.tabs)} tabs open)")
            else:
                self._active_tab = await self.browser.get("about:blank")
                print("[SESSION_INIT] Created initial tab")

            # Multi-account: re-apply the per-account CDP fingerprint BEFORE any
            # Target navigation and BEFORE the live-UA read below — so the purchase
            # browser presents the SAME identity (UA/platform/tz/viewport/canvas)
            # the harvester logged this account in under. build_identity is
            # deterministic on (account_id, timezone), so it reproduces exactly
            # what harvest_accounts applied. Gated to file-driven accounts only;
            # legacy single-account skips this and keeps its established profile.
            if self.apply_fingerprint and self.account_id:
                try:
                    from .account_identity import build_identity, apply_identity
                    _identity = build_identity(self.account_id, timezone=self.account_timezone)
                    _applied = await apply_identity(self._active_tab, _identity)
                    print(f"[SESSION_INIT] Per-account identity applied for "
                          f"{self.account_id}: {_applied} (tz={_identity.get('timezone')})")
                except Exception as _id_err:
                    self.logger.warning(f"[FINGERPRINT] apply_identity failed (non-fatal): {_id_err}")
                    print(f"[SESSION_INIT] [WARN] apply_identity failed: {_id_err}")

            # Patch 3 (2026-04-25): Read the live UA from the running Chrome instance
            # instead of using the static pool in _load_fingerprint_data(). This ensures
            # fingerprint_data['user_agent'] always matches the actual browser JA3/TLS
            # fingerprint — a mismatch between the UA we log/send and the real browser UA
            # is a Shape Security detection vector.
            try:
                live_ua = await self._active_tab.evaluate("navigator.userAgent")
                if live_ua and isinstance(live_ua, str) and "Mozilla" in live_ua:
                    self.fingerprint_data['user_agent'] = live_ua
                    self.logger.info(f"[FINGERPRINT] Live UA read from browser: {live_ua[:80]}")
                    print(f"[SESSION_INIT] UA synced from live Chrome: {live_ua[:80]}")
                else:
                    self.logger.warning("[FINGERPRINT] Could not read live UA — keeping generated value")
            except Exception as _ua_err:
                self.logger.warning(f"[FINGERPRINT] Live UA read failed (non-fatal): {_ua_err}")

            # Enable Network CDP domain. tab.send() expects a CDP command
            # object, not a string — passing the string raised
            # "'str' object is not an iterator" every startup and Network
            # was effectively never enabled. Use the proper command (same
            # pattern walmart/session_manager.py uses).
            try:
                await self._active_tab.send(uc.cdp.network.enable())
            except Exception as e:
                self.logger.warning(f"[INIT] Network.enable warning: {e}")

            # Inject saved cookies BEFORE navigating so the first request is authenticated
            if self.session_path.exists():
                try:
                    with open(self.session_path, 'r', encoding='utf-8') as f:
                        session_data = json.load(f)
                    cookies = session_data.get('cookies', [])
                    if cookies:
                        injected = 0
                        for cookie in cookies:
                            try:
                                same_site = None
                                ss = cookie.get('sameSite')
                                if ss in ('Strict', 'Lax', 'None'):
                                    same_site = uc.cdp.network.CookieSameSite.from_json(ss)

                                expires = None
                                exp = cookie.get('expires', -1)
                                if exp and exp > 0:
                                    expires = uc.cdp.network.TimeSinceEpoch(exp)

                                await self._active_tab.send(uc.cdp.network.set_cookie(
                                    name=cookie['name'],
                                    value=cookie['value'],
                                    domain=cookie.get('domain') or None,
                                    path=cookie.get('path', '/'),
                                    secure=cookie.get('secure', False),
                                    http_only=cookie.get('httpOnly', False),
                                    same_site=same_site,
                                    expires=expires,
                                ))
                                injected += 1
                            except Exception:
                                pass
                        print(f"[SESSION_INIT] Injected {injected}/{len(cookies)} cookies from {self.session_path}")
                    else:
                        print(f"[SESSION_INIT] Session file has no cookies")
                except Exception as e:
                    self.logger.warning(f"[INIT] Could not inject cookies: {e}")

            # Navigate to Target.com
            print("[SESSION_INIT] Navigating to target.com...")
            try:
                self._active_tab = await self.browser.get("https://www.target.com")
                print(f"[SESSION_INIT] Navigation successful! URL: {self._active_tab.url}")

                # Restore localStorage if available
                if hasattr(self, '_saved_local_storage') and self._saved_local_storage:
                    try:
                        print("[SESSION_INIT] Restoring localStorage...")
                        for origin_data in self._saved_local_storage:
                            origin_url = origin_data.get('origin')
                            local_storage_items = origin_data.get('localStorage', [])
                            if origin_url and 'target.com' in origin_url and local_storage_items:
                                for item in local_storage_items:
                                    key = item.get('name')
                                    value = item.get('value')
                                    if key and value:
                                        try:
                                            await self._active_tab.evaluate(
                                                f"() => {{ localStorage.setItem({json.dumps(key)}, {json.dumps(value)}); }}"
                                            )
                                        except Exception:
                                            pass
                                self.logger.info(f"[LOCALSTORAGE] Restored {len(local_storage_items)} localStorage items")
                    except Exception as ls_error:
                        self.logger.warning(f"[LOCALSTORAGE] Failed to restore (non-fatal): {ls_error}")

                await asyncio.sleep(0.5)
                print("[SESSION_INIT] Page rendering complete")
            except Exception as nav_error:
                print(f"[SESSION_INIT] Navigation warning (non-fatal): {nav_error}")
                print("[SESSION_INIT] Continuing anyway...")

            print("[SESSION_INIT] Browser ready at target.com!")

            # LAYER 1: Setup CDP cookie interception
            print("\n[SESSION_INIT] Setting up CDP cookie interception...")
            cdp_setup_success = await self._setup_cdp_cookie_interception()
            if cdp_setup_success:
                print("[SESSION_INIT] CDP cookie interception ready!")
            else:
                print("[SESSION_INIT] CDP setup failed (non-fatal) - continuing")

            # LAYER 2: Start cookie watchdog
            print("[SESSION_INIT] Starting cookie watchdog...")
            self._start_cookie_watchdog()
            print("[SESSION_INIT] Cookie watchdog active!")

            self.session_active = True
            self.session_created_at = datetime.now()
            self._initialization_attempts = 0
            self.logger.info("[OK] Session initialized with nodriver (undetected Chrome)")
            return True

        except Exception as e:
            self.logger.error(f"[ERROR] Failed to initialize session: {e}")
            await self._safe_cleanup()

            if self._initialization_attempts < self._max_init_attempts:
                self.logger.info("[RETRY] Retrying session initialization...")
                await asyncio.sleep(2)
                return await self.initialize()
            else:
                self.logger.error("[CRITICAL] Exhausted all initialization attempts")
                return False

    def submit_async_task(self, coro):
        """Thread-safe method to submit async tasks to the main event loop."""
        if not self._event_loop:
            raise RuntimeError("Event loop not initialized - call initialize() first")
        return asyncio.run_coroutine_threadsafe(coro, self._event_loop)

    async def _safe_cleanup(self):
        """Safe cleanup that doesn't throw exceptions"""
        # Capture the Chrome PID before dropping references — if stop() can't
        # do its job (wedged CDP socket), we hard-kill the process tree so the
        # profile dir is actually released for the relaunch. A half-dead Chrome
        # left holding --user-data-dir makes every subsequent initialize() fail
        # with "Failed to connect to browser" (2026-07-03 02:50 relaunch).
        _pid = None
        try:
            _pid = getattr(self.browser, '_process_pid', None) or getattr(self, '_chrome_pid', None)
        except Exception:
            pass
        try:
            if self._cookie_watchdog_running:
                self.logger.info("[CLEANUP] Stopping cookie watchdog...")
                self._stop_cookie_watchdog()

            if self.browser:
                try:
                    # Bounded: browser.stop() awaits the CDP connection close,
                    # which never resolves on a dead websocket.
                    await asyncio.wait_for(self.browser.stop(), timeout=8.0)
                    self.logger.info("[CLEANUP] Browser stopped")
                except Exception as e:
                    self.logger.warning(f"[CLEANUP] Error stopping browser: {e}")
                self.browser = None
                self._active_tab = None

        except Exception as e:
            self.logger.warning(f"Cleanup warning (non-fatal): {e}")

        # Belt-and-braces: make sure the process tree is really gone (idempotent
        # — taskkill on an already-dead PID just returns nonzero).
        if _pid:
            try:
                import subprocess as _sp
                if sys.platform == "win32":
                    _sp.run(["taskkill", "/F", "/T", "/PID", str(_pid)],
                            capture_output=True, timeout=5)
                else:
                    import signal as _sig
                    os.kill(_pid, _sig.SIGKILL)
                self.logger.info(f"[CLEANUP] Chrome process tree {_pid} hard-killed (profile released)")
            except Exception as e:
                self.logger.warning(f"[CLEANUP] hard-kill pid={_pid} failed: {e}")
        self._chrome_pid = None

        self.session_active = False
        self._cdp_cookie_interception_active = False

    async def _restore_session_storage(self):
        """Restore sessionStorage for Target.com checkout via CDP init script"""
        try:
            if self._session_storage and self._active_tab:
                self.logger.info("[SESSION] Restoring sessionStorage for target.com checkout")
                try:
                    session_storage_escaped = json.dumps(self._session_storage)
                    js_code = f"""
                        (function() {{
                            if (window.location.hostname.includes('target.com')) {{
                                try {{
                                    const sessionStorageJSON = {session_storage_escaped};
                                    const sessionData = JSON.parse(sessionStorageJSON);
                                    let restoredCount = 0;
                                    for (const [key, value] of Object.entries(sessionData)) {{
                                        window.sessionStorage.setItem(key, value);
                                        restoredCount++;
                                    }}
                                    console.log('[SessionManager] Restored ' + restoredCount + ' sessionStorage items');
                                }} catch (e) {{
                                    console.error('[SessionManager] Failed to restore sessionStorage:', e);
                                }}
                            }}
                        }})();
                    """
                    await self._active_tab.send("Page.addScriptToEvaluateOnNewDocument", source=js_code)
                    self.logger.info("[OK] SessionStorage restore script added (runs on every page load)")
                except Exception as e:
                    self.logger.warning(f"Could not add sessionStorage restore script (non-fatal): {e}")
        except Exception as e:
            self.logger.warning(f"Failed to restore sessionStorage: {e}")

    async def _setup_cdp_cookie_interception(self):
        """LAYER 1: CDP Real-Time Cookie Interception"""
        try:
            if not self._active_tab:
                self.logger.error("[CDP_COOKIE] Cannot setup CDP - no tab available")
                return False

            # Network domain already enabled in initialize()
            self.logger.info("[CDP_COOKIE] Setting up response handler...")

            try:
                from zendriver import cdp
                self._active_tab.add_handler(
                    cdp.network.ResponseReceived,
                    self._handle_response_received
                )
                self.logger.info("[CDP_COOKIE] ResponseReceived handler registered")
            except Exception as handler_err:
                self.logger.warning(f"[CDP_COOKIE] Handler registration failed (non-fatal): {handler_err}")

            self._cdp_cookie_interception_active = True
            self.logger.info("[CDP_COOKIE] Real-time cookie interception ACTIVE")
            print("[CDP_COOKIE] Cookie interception system activated!")
            return True

        except Exception as e:
            self.logger.error(f"[CDP_COOKIE] Failed to setup cookie interception: {e}")
            return False

    async def _handle_response_received(self, event):
        """Handle CDP ResponseReceived events to intercept Set-Cookie headers"""
        try:
            # Handle nodriver typed event object
            if hasattr(event, 'response'):
                url = str(event.response.url)
                headers_obj = event.response.headers
                headers = dict(headers_obj) if headers_obj else {}
            elif isinstance(event, dict):
                response = event.get('response', {})
                url = response.get('url', '')
                headers = response.get('headers', {})
            else:
                return

            if 'target.com' not in url:
                return

            set_cookie_header = None
            for header_name, header_value in headers.items():
                if header_name.lower() == 'set-cookie':
                    set_cookie_header = header_value
                    break

            if not set_cookie_header:
                return

            cookies_to_fix = []
            cookie_strings = [set_cookie_header] if isinstance(set_cookie_header, str) else set_cookie_header

            for cookie_str in cookie_strings:
                cookie_parts = cookie_str.split(';')
                if not cookie_parts:
                    continue

                name_value = cookie_parts[0].strip()
                if '=' not in name_value:
                    continue

                cookie_name = name_value.split('=')[0].strip()
                critical_cookies = ['accessToken', 'idToken', 'refreshToken', 'login-session']
                if cookie_name not in critical_cookies:
                    continue

                has_expires = any('expires' in part.lower() or 'max-age' in part.lower() for part in cookie_parts[1:])
                if not has_expires:
                    self.logger.warning(f"[CDP_COOKIE] Intercepted SESSION cookie: '{cookie_name}' from {url}")
                    cookie_data = self._parse_set_cookie_header(cookie_str, url)
                    if cookie_data:
                        cookies_to_fix.append(cookie_data)

            if cookies_to_fix:
                asyncio.create_task(self._inject_persistent_cookies_via_cdp(cookies_to_fix))

        except Exception as e:
            self.logger.error(f"[CDP_COOKIE] Error handling response: {e}")

    def _parse_set_cookie_header(self, cookie_str: str, url: str) -> Optional[Dict[str, Any]]:
        """Parse Set-Cookie header string into cookie dict for CDP Network.setCookie"""
        try:
            parts = [p.strip() for p in cookie_str.split(';')]
            name_value = parts[0]
            if '=' not in name_value:
                return None

            name, value = name_value.split('=', 1)
            from urllib.parse import urlparse
            parsed_url = urlparse(url)
            hostname = parsed_url.hostname or ''
            # Use root domain with leading dot so cookie works across all Target subdomains
            # (carts.target.com, checkout.target.com, etc.)
            if 'target.com' in hostname:
                domain = '.target.com'
            else:
                domain = hostname

            cookie_data = {
                'name': name.strip(),
                'value': value.strip(),
                'domain': domain,
                'path': '/',
                'httpOnly': False,
                'secure': True,   # Target auth cookies are always HTTPS
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
                elif 'secure' in part_lower and '=' not in part:
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
            if not self._active_tab:
                return

            future_timestamp = time.time() + (30 * 24 * 60 * 60)

            for cookie in cookies:
                cookie['expires'] = future_timestamp
                self.logger.info(f"[CDP_COOKIE] Fixing cookie '{cookie['name']}' - converting to PERSISTENT (30 days)")
                try:
                    ss = cookie.get('sameSite')
                    same_site = uc.cdp.network.CookieSameSite.from_json(ss) if ss in ('Strict', 'Lax', 'None') else None
                    exp = cookie.get('expires', -1)
                    expires = uc.cdp.network.TimeSinceEpoch(exp) if exp and exp > 0 else None
                    # Normalize domain to .target.com so cookie reaches all subdomains
                    raw_domain = cookie.get('domain') or ''
                    domain = '.target.com' if 'target.com' in raw_domain else (raw_domain or None)
                    # sameSite=None requires Secure=True or browser rejects it
                    is_secure = True if ss == 'None' else cookie.get('secure', True)
                    await self._active_tab.send(uc.cdp.network.set_cookie(
                        name=cookie['name'],
                        value=cookie['value'],
                        domain=domain,
                        path=cookie.get('path', '/'),
                        secure=is_secure,
                        http_only=cookie.get('httpOnly', False),
                        same_site=same_site,
                        expires=expires,
                    ))
                    self.logger.info(f"[CDP_COOKIE] Cookie '{cookie['name']}' now PERSISTENT!")
                    print(f"[CDP_COOKIE] Fixed '{cookie['name']}' - persistent for 30 days")
                except Exception as set_err:
                    self.logger.warning(f"[CDP_COOKIE] setCookie failed for {cookie['name']}: {set_err}")

        except Exception as e:
            self.logger.error(f"[CDP_COOKIE] Error injecting cookies: {e}")

    def _start_cookie_watchdog(self):
        """LAYER 2: Cookie Watchdog - monitors cookies every 60 seconds"""
        try:
            if self._cookie_watchdog_running:
                self.logger.warning("[WATCHDOG] Cookie watchdog already running")
                return

            self._cookie_watchdog_running = True

            if self._event_loop:
                self._cookie_watchdog_task = asyncio.run_coroutine_threadsafe(
                    self._cookie_watchdog_loop(),
                    self._event_loop
                )
                self.logger.info("[WATCHDOG] Cookie watchdog started successfully")
            else:
                self.logger.error("[WATCHDOG] Cannot start watchdog - no event loop available")

        except Exception as e:
            self.logger.error(f"[WATCHDOG] Failed to start cookie watchdog: {e}")

    async def _cookie_watchdog_loop(self):
        """Background loop that monitors and fixes cookies every 60 seconds.

        Each cycle's CDP work is bounded (45s) — on 2026-07-03 the unbounded
        `storage.get_cookies` parked forever on a dead websocket at 23:50:11
        ("Checking cookies..." with no verdict), silently killing the watchdog
        for the rest of the night. Now a wedged cycle times out, and 3
        consecutive failures escalate to refresh_session() (browser restart)
        so the worker self-heals instead of rotting until the drop."""
        self.logger.info("[WATCHDOG] Cookie watchdog loop started")
        consecutive_failures = 0
        # Identity marker so _stop_cookie_watchdog can recognize (and refuse
        # to cancel) a stop request issued from inside this very coroutine.
        try:
            self._watchdog_task_obj = asyncio.current_task()
        except Exception:
            self._watchdog_task_obj = None

        while self._cookie_watchdog_running and self.session_active:
            try:
                await asyncio.sleep(60)

                if not self._cookie_watchdog_running or not self.session_active:
                    break

                try:
                    ok = await asyncio.wait_for(self._cookie_watchdog_check_once(), timeout=45.0)
                except asyncio.TimeoutError:
                    ok = False
                    self.logger.error("[WATCHDOG] Cookie check TIMED OUT (>45s) — CDP websocket likely wedged")

                if ok:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    self.logger.warning(f"[WATCHDOG] Cookie check failed ({consecutive_failures} consecutive)")
                    if consecutive_failures >= 3:
                        self.logger.error("[WATCHDOG] 3 consecutive failures — escalating to session refresh (browser restart if needed)")
                        print(f"[WATCHDOG] {self.account_id or 'primary'}: 3 consecutive cookie-check failures — restarting session")
                        consecutive_failures = 0
                        # DETACHED task (2026-07-06 incident): running the
                        # refresh inline here dies to self-cancellation —
                        # _safe_cleanup stops the watchdog, i.e. THIS task.
                        # A detached task survives that and retries; the
                        # refresh takes the drop-guard internally so it can
                        # never restart Chrome under an in-flight purchase.
                        if not self.purchase_in_progress:
                            self._spawn_escalation_refresh("cookie-watchdog 3-strike")

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"[WATCHDOG] Error in watchdog loop: {e}")

        self.logger.info("[WATCHDOG] Cookie watchdog loop ended")

    def _spawn_escalation_refresh(self, why: str):
        """Fire-and-forget session recovery on this SM's own loop.

        Detached from the watchdog task on purpose: _safe_cleanup (inside
        refresh_session) stops the watchdog, and an inline escalation would be
        cancelling itself (2026-07-06: all 3 workers' recoveries died one
        await before the Chrome restart). Retries up to 3× with a 60s gap;
        single-flight — a second trigger while one is running is a no-op.
        refresh_session is internally serialized and drop-guarded."""
        t = getattr(self, '_escalation_task', None)
        if t is not None and not t.done():
            self.logger.info(f"[ESCALATE] refresh already in flight — skipping duplicate ({why})")
            return

        async def _escalate():
            for attempt in range(1, 4):
                ok = False
                try:
                    ok = await asyncio.wait_for(self.refresh_session(), timeout=300.0)
                except Exception as e:
                    self.logger.error(f"[ESCALATE] refresh attempt {attempt}/3 error: {type(e).__name__}: {e}")
                if ok:
                    self.logger.info(f"[ESCALATE] session recovered on attempt {attempt} ({why})")
                    print(f"[ESCALATE] {self.account_id or 'primary'}: session RECOVERED ({why}, attempt {attempt})")
                    return
                print(f"[ESCALATE] {self.account_id or 'primary'}: refresh attempt {attempt}/3 failed ({why})")
                await asyncio.sleep(60)
            self.logger.critical(f"[ESCALATE] session NOT recovered after 3 attempts ({why})")
            print(f"[ESCALATE] {self.account_id or 'primary'}: FAILED to recover session after 3 attempts ({why}) — sentinel will keep retrying")

        try:
            self._escalation_task = asyncio.get_running_loop().create_task(_escalate())
        except RuntimeError:
            self.logger.error("[ESCALATE] no running loop — cannot spawn escalation")

    async def _cookie_watchdog_check_once(self) -> bool:
        """One watchdog cycle: read cookies via CDP, fix session/missing ones.
        Returns True if the CDP read succeeded (session considered alive)."""
        self.logger.info("[WATCHDOG] Checking cookies...")
        test_mode = os.environ.get('TEST_MODE', 'false').lower() == 'true'

        if not self._active_tab:
            self.logger.warning("[WATCHDOG] No tab available, skipping check")
            return False

        # Get current cookies via CDP
        try:
            all_cookies = await self._active_tab.send(uc.cdp.storage.get_cookies())
            target_cookies = [
                {
                    'name': str(c.name),
                    'value': str(c.value),
                    'expires': float(c.expires) if c.expires is not None else -1,
                    'domain': str(getattr(c, 'domain', '')),
                    'path': str(getattr(c, 'path', '/')),
                    'httpOnly': bool(getattr(c, 'http_only', False)),
                    'secure': bool(getattr(c, 'secure', False)),
                }
                for c in all_cookies
                if 'target.com' in str(getattr(c, 'domain', ''))
            ]
        except Exception as get_err:
            self.logger.warning(f"[WATCHDOG] Could not get cookies: {get_err}")
            return False

        self.logger.info(f"[WATCHDOG] Found {len(target_cookies)} Target.com cookies")

        critical_cookies = ['accessToken', 'idToken', 'refreshToken', 'login-session']
        found_cookies = {}
        missing_cookies = []
        session_cookies = []

        for cookie_name in critical_cookies:
            found = False
            for cookie in target_cookies:
                c_name = cookie.get('name', '') if isinstance(cookie, dict) else str(getattr(cookie, 'name', ''))
                if c_name == cookie_name:
                    found = True
                    found_cookies[cookie_name] = cookie
                    expires = cookie.get('expires', -1) if isinstance(cookie, dict) else getattr(cookie, 'expires', -1)
                    if expires is None or expires == -1:
                        session_cookies.append(cookie_name)
                        if not test_mode:
                            self.logger.warning(f"[WATCHDOG] '{cookie_name}' is SESSION cookie - needs fixing!")
                    else:
                        days_left = (expires - time.time()) / (24 * 60 * 60)
                        if days_left < 0:
                            missing_cookies.append(cookie_name)
                            if not test_mode:
                                self.logger.warning(f"[WATCHDOG] '{cookie_name}' EXPIRED {abs(days_left):.1f} days ago!")
                        else:
                            self.logger.info(f"[WATCHDOG] '{cookie_name}' OK ({days_left:.1f} days left)")
                    break

            if not found:
                missing_cookies.append(cookie_name)
                if not test_mode:
                    self.logger.warning(f"[WATCHDOG] '{cookie_name}' MISSING!")

        if session_cookies:
            await self._fix_session_cookies(found_cookies, session_cookies)

        if missing_cookies:
            if not test_mode:
                self.logger.warning(f"[WATCHDOG] Missing critical cookies: {missing_cookies}")
            await self._restore_cookies_from_file(missing_cookies)

        if not session_cookies and not missing_cookies:
            self.logger.info("[WATCHDOG] All cookies healthy!")
        return True

    async def _fix_session_cookies(self, found_cookies: dict, session_cookie_names: list):
        """Fix session cookies by re-injecting with persistent expires"""
        try:
            future_timestamp = time.time() + (30 * 24 * 60 * 60)

            for cookie_name in session_cookie_names:
                if cookie_name not in found_cookies:
                    continue

                raw = found_cookies[cookie_name]
                if isinstance(raw, dict):
                    cookie = dict(raw)
                else:
                    cookie = {
                        'name': str(raw.name),
                        'value': str(raw.value),
                        'domain': str(getattr(raw, 'domain', '.target.com')),
                        'path': str(getattr(raw, 'path', '/')),
                        'httpOnly': bool(getattr(raw, 'http_only', False)),
                        'secure': bool(getattr(raw, 'secure', True)),
                        'sameSite': raw.same_site.to_json() if getattr(raw, 'same_site', None) else None,
                    }
                cookie['expires'] = future_timestamp

                self.logger.info(f"[WATCHDOG] Fixing '{cookie_name}' via CDP (converting to persistent)")
                try:
                    ss = cookie.get('sameSite')
                    same_site = uc.cdp.network.CookieSameSite.from_json(ss) if ss in ('Strict', 'Lax', 'None') else None
                    exp = cookie.get('expires', -1)
                    expires = uc.cdp.network.TimeSinceEpoch(exp) if exp and exp > 0 else None
                    # Normalize domain to .target.com so cookie works across all subdomains
                    raw_domain = cookie.get('domain') or ''
                    domain = '.target.com' if 'target.com' in raw_domain else (raw_domain or None)
                    # sameSite=None requires Secure=True or browser rejects it
                    is_secure = True if ss == 'None' else cookie.get('secure', True)
                    await self._active_tab.send(uc.cdp.network.set_cookie(
                        name=cookie['name'],
                        value=cookie['value'],
                        domain=domain,
                        path=cookie.get('path', '/'),
                        secure=is_secure,
                        http_only=cookie.get('httpOnly', False),
                        same_site=same_site,
                        expires=expires,
                    ))
                    self.logger.info(f"[WATCHDOG] '{cookie_name}' fixed via CDP")
                    print(f"[WATCHDOG] Fixed '{cookie_name}' - now persistent for 30 days")
                except Exception as set_err:
                    self.logger.warning(f"[WATCHDOG] setCookie failed for {cookie_name}: {set_err}")

        except Exception as e:
            self.logger.error(f"[WATCHDOG] Error fixing session cookies: {e}")

    async def _restore_cookies_from_file(self, missing_cookie_names: list):
        """Restore missing cookies from saved session file"""
        try:
            if not self.session_path.exists():
                return

            with open(self.session_path, 'r', encoding='utf-8') as f:
                session_data = json.load(f)
                saved_cookies = session_data.get('cookies', [])

            cookies_to_restore = [
                c for c in saved_cookies
                if c.get('name') in missing_cookie_names and 'target.com' in c.get('domain', '')
            ]

            if cookies_to_restore:
                self.logger.info(f"[WATCHDOG] Restoring {len(cookies_to_restore)} cookies from file")
                for cookie in cookies_to_restore:
                    if cookie.get('expires', -1) == -1:
                        cookie['expires'] = time.time() + (30 * 24 * 60 * 60)

                    try:
                        same_site = None
                        ss = cookie.get('sameSite')
                        if ss in ('Strict', 'Lax', 'None'):
                            same_site = uc.cdp.network.CookieSameSite.from_json(ss)
                        exp = cookie.get('expires', -1)
                        expires = uc.cdp.network.TimeSinceEpoch(exp) if exp and exp > 0 else None
                        await self._active_tab.send(uc.cdp.network.set_cookie(
                            name=cookie['name'],
                            value=cookie['value'],
                            domain=cookie.get('domain') or None,
                            path=cookie.get('path', '/'),
                            secure=cookie.get('secure', False),
                            http_only=cookie.get('httpOnly', False),
                            same_site=same_site,
                            expires=expires,
                        ))
                        self.logger.info(f"[WATCHDOG] Restored '{cookie['name']}' from file")
                        print(f"[WATCHDOG] Restored '{cookie['name']}' from saved session")
                    except Exception as set_err:
                        self.logger.warning(f"[WATCHDOG] Could not restore {cookie.get('name', '?')}: {set_err}")
            else:
                test_mode = os.environ.get('TEST_MODE', 'false').lower() == 'true'
                if not test_mode:
                    self.logger.warning(f"[WATCHDOG] Could not find cookies to restore in file: {missing_cookie_names}")

        except Exception as e:
            self.logger.error(f"[WATCHDOG] Error restoring cookies from file: {e}")

    def _stop_cookie_watchdog(self):
        """Stop the cookie watchdog.

        2026-07-06 incident guard: NEVER cancel the watchdog task from WITHIN
        the watchdog coroutine itself. The 3-strike escalation used to call
        refresh_session -> _safe_cleanup -> here, and the cancel() killed the
        very task performing the recovery — one await before the browser
        restart. All 3 workers' watchdogs died that way at 00:26 and the bot
        ran browserless-warmup for 19 hours. The loop exits on its own via
        the running flag, so skipping the cancel for self is always safe."""
        self._cookie_watchdog_running = False
        fut = self._cookie_watchdog_task
        if fut:
            _is_self = False
            try:
                _cur = asyncio.current_task()
                _is_self = _cur is not None and _cur is getattr(self, '_watchdog_task_obj', None)
            except Exception:
                _is_self = False
            if _is_self:
                self.logger.info("[WATCHDOG] stop requested from within watchdog — flag cleared, no self-cancel")
            else:
                try:
                    fut.cancel()
                except Exception:
                    pass
        self.logger.info("[WATCHDOG] Cookie watchdog stopped")

    async def human_click(self, tab, selector: str, timeout: int = 5000) -> bool:
        """Click element with human-like Bezier curve mouse movement via CDP Input events"""
        try:
            # Find element by CSS selector
            try:
                element = await tab.select(selector, timeout=timeout // 1000)
            except Exception:
                element = None

            if not element:
                return False

            # Get element bounds via JS
            try:
                bounds = await tab.evaluate(
                    "el => { const r = el.getBoundingClientRect(); return {x: r.x, y: r.y, width: r.width, height: r.height}; }",
                    element
                )
            except Exception:
                bounds = None

            if not bounds or not bounds.get('width'):
                # Not visible, use direct click
                await element.click()
                return True

            target_x = bounds['x'] + (bounds['width'] * random.uniform(0.3, 0.7))
            target_y = bounds['y'] + (bounds['height'] * random.uniform(0.3, 0.7))

            # Bezier curve from current mouse position to target
            try:
                current_pos = await tab.evaluate("() => ({ x: window.lastMouseX || 0, y: window.lastMouseY || 0 })")
                start_x = current_pos['x'] if current_pos['x'] > 0 else random.randint(100, 500)
                start_y = current_pos['y'] if current_pos['y'] > 0 else random.randint(100, 500)
            except Exception:
                start_x = random.randint(100, 500)
                start_y = random.randint(100, 500)

            mid_x = (start_x + target_x) / 2
            mid_y = (start_y + target_y) / 2
            control_x = mid_x + random.uniform(-100, 100)
            control_y = mid_y + random.uniform(-100, 100)

            steps = random.randint(10, 20)
            for i in range(steps + 1):
                t = i / steps
                x = (1-t)**2 * start_x + 2*(1-t)*t * control_x + t**2 * target_x
                y = (1-t)**2 * start_y + 2*(1-t)*t * control_y + t**2 * target_y
                await tab.send("Input.dispatchMouseEvent", type="mouseMoved", x=int(x), y=int(y), buttons=0)

            try:
                await tab.evaluate(f"() => {{ window.lastMouseX = {target_x}; window.lastMouseY = {target_y}; }}")
            except Exception:
                pass

            await asyncio.sleep(random.uniform(0.01, 0.03))

            # Click via CDP Input events
            await tab.send("Input.dispatchMouseEvent", type="mousePressed", x=int(target_x), y=int(target_y), button="left", buttons=1, clickCount=1)
            await tab.send("Input.dispatchMouseEvent", type="mouseReleased", x=int(target_x), y=int(target_y), button="left", buttons=0, clickCount=1)

            await asyncio.sleep(random.uniform(0.02, 0.05))
            return True

        except Exception as e:
            self.logger.warning(f"Human click failed, using fallback: {e}")
            try:
                element = await tab.select(selector, timeout=timeout // 1000)
                if element:
                    await element.click()
                    return True
            except Exception:
                pass
            return False

    async def simulate_human_reading(self, tab, duration_seconds: float = None) -> bool:
        """Simulate human-like page reading via CDP Input events (Bezier curves + scrolling)"""
        try:
            if duration_seconds is None:
                duration_seconds = random.uniform(1.0, 3.0)

            dimensions = await tab.evaluate("""
                () => ({
                    width: window.innerWidth,
                    height: window.innerHeight,
                    scrollHeight: document.documentElement.scrollHeight
                })
            """)

            width = dimensions['width']
            height = dimensions['height']
            scroll_height = dimensions['scrollHeight']

            num_movements = random.randint(3, 7)

            for i in range(num_movements):
                target_x = random.randint(int(width * 0.2), int(width * 0.8))
                target_y = random.randint(int(height * 0.2), int(height * 0.8))

                try:
                    current_pos = await tab.evaluate("() => ({ x: window.lastMouseX || window.innerWidth/2, y: window.lastMouseY || window.innerHeight/2 })")
                    start_x = current_pos['x']
                    start_y = current_pos['y']
                except Exception:
                    start_x = width / 2
                    start_y = height / 2

                mid_x = (start_x + target_x) / 2
                mid_y = (start_y + target_y) / 2
                control_x = mid_x + random.uniform(-50, 50)
                control_y = mid_y + random.uniform(-50, 50)

                steps = random.randint(8, 15)
                for j in range(steps + 1):
                    t = j / steps
                    x = (1-t)**2 * start_x + 2*(1-t)*t * control_x + t**2 * target_x
                    y = (1-t)**2 * start_y + 2*(1-t)*t * control_y + t**2 * target_y
                    await tab.send("Input.dispatchMouseEvent", type="mouseMoved", x=int(x), y=int(y), buttons=0)

                try:
                    await tab.evaluate(f"() => {{ window.lastMouseX = {target_x}; window.lastMouseY = {target_y}; }}")
                except Exception:
                    pass

                await asyncio.sleep(random.uniform(0.05, 0.15))

                if random.random() < 0.3 and scroll_height > height:
                    try:
                        current_scroll = await tab.evaluate("() => window.scrollY")
                        max_scroll = scroll_height - height

                        if current_scroll < max_scroll * 0.8:
                            target_scroll = min(current_scroll + random.randint(100, 300), max_scroll)
                        else:
                            target_scroll = max(current_scroll - random.randint(100, 200), 0)

                        scroll_steps = random.randint(10, 20)
                        for k in range(scroll_steps + 1):
                            progress = k / scroll_steps
                            eased = 1 - (1 - progress) ** 3
                            scroll_pos = current_scroll + (target_scroll - current_scroll) * eased
                            await tab.evaluate(f"() => window.scrollTo(0, {scroll_pos})")
                    except Exception:
                        pass

            return True

        except Exception as e:
            self.logger.warning(f"Simulate human reading failed (non-fatal): {e}")
            return False

    async def get_page(self):
        """Get the main tab - bulletproof with auto-recovery. Returns nodriver tab."""
        max_attempts = 3

        for attempt in range(max_attempts):
            try:
                async with self._context_lock:
                    if not self.browser or not self.session_active:
                        break

                    # Get active tab
                    if self.browser.tabs:
                        tab = self.browser.tabs[0]
                        self._active_tab = tab
                    else:
                        # Bounded: on a dead CDP websocket this call never
                        # returns — an unbounded await here held the lock
                        # forever and wedged every downstream get_page caller.
                        tab = await asyncio.wait_for(
                            self.browser.get("about:blank"), timeout=10.0)
                        self._active_tab = tab

                    # Test tab health
                    if tab and await self._test_tab_health(tab):
                        return tab

            except Exception as e:
                self.logger.error(f"Get page attempt {attempt + 1} failed: {e}")

            if attempt < max_attempts - 1:
                await asyncio.sleep(0.3)

        self.logger.error("[CRITICAL] Failed to get healthy tab after all attempts")
        return None

    async def _test_browser_health(self) -> bool:
        """Test if browser is healthy"""
        try:
            if not self.browser:
                return False
            _ = self.browser.tabs  # Just check it's accessible
            return True
        except Exception:
            return False

    async def _test_tab_health(self, tab) -> bool:
        """Test if tab is healthy"""
        try:
            if not tab:
                return False
            try:
                result = await asyncio.wait_for(tab.evaluate("true"), timeout=2.0)
                return True
            except asyncio.TimeoutError:
                # 2026-07-03: a dead CDP websocket times out here but tab.url
                # (a cached property) stays truthy — the old fallback declared
                # wedged tabs "healthy", so get_page handed dead tabs to the
                # purchase path all night. A timeout on `evaluate("true")` on
                # an idle tab IS the wedge signature: report unhealthy so
                # callers escalate to a browser restart instead of hanging.
                self.logger.warning("[TAB_HEALTH] evaluate('true') timed out >2s — tab/websocket wedged")
                return False
            except Exception:
                # Non-timeout CDP errors (e.g. transient nav race) — keep the
                # lenient fallback: a tab mid-navigation can reject evaluate
                # yet be perfectly alive.
                if tab.url:
                    return True
                return False
        except Exception:
            return False

    # Backward compatibility aliases
    async def _test_context_health(self) -> bool:
        return await self._test_browser_health()

    async def _test_page_health(self, page) -> bool:
        return await self._test_tab_health(page)

    async def _dismiss_popups(self, tab) -> bool:
        """Dismiss any popup overlays"""
        try:
            popup_texts = ["Cancel", "Skip", "Skip for now", "Not now", "Maybe later", "No thanks"]
            popup_selectors = [
                '[aria-label*="Close"]',
                '[aria-label*="Dismiss"]',
                '[data-test*="close"]',
                'button[class*="close"]',
            ]

            dismissed_count = 0

            for text in popup_texts:
                try:
                    element = await tab.find(text, best_match=True, timeout=0.5)
                    if element:
                        await asyncio.sleep(random.uniform(0.05, 0.1))
                        await element.click()
                        dismissed_count += 1
                        await asyncio.sleep(random.uniform(0.1, 0.2))
                except Exception:
                    continue

            for selector in popup_selectors:
                try:
                    element = await tab.select(selector, timeout=0.5)
                    if element:
                        visible = await tab.evaluate(
                            "el => !!(el && el.offsetParent !== null && el.getBoundingClientRect().width > 0)",
                            element
                        )
                        if visible:
                            await asyncio.sleep(random.uniform(0.05, 0.1))
                            await element.click()
                            dismissed_count += 1
                            await asyncio.sleep(random.uniform(0.1, 0.2))
                except Exception:
                    continue

            if dismissed_count > 0:
                self.logger.info(f"[OK] Dismissed {dismissed_count} popup(s)")

            return True

        except Exception as e:
            self.logger.warning(f"Popup dismissal error (non-fatal): {e}")
            return False

    # ------------------------------------------------------------------
    # Access-token keep-fresh (2026-07-07 drop post-mortem)
    #
    # carts.target.com writes authenticate via the accessToken cookie (a
    # JWT; ~4h TTL, `eid` claim present only on member tokens). Target's
    # SPA refreshes it via its own HTTP client when IT sees a 401 — but
    # every bot request is a raw page-context fetch that bypasses that
    # layer, so the app never learns the token died and never refreshes.
    # Meanwhile /account keeps rendering from login-session, so DOM checks
    # (the old sentinel) pass all night while every ATC 401s. These
    # helpers detect the real write-token state and re-mint it the two
    # ways the site itself does.
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_jwt_claims(token_value: str) -> Dict[str, Any]:
        """Best-effort decode of a JWT payload (no signature check). {} on junk."""
        try:
            parts = str(token_value).split('.')
            if len(parts) < 2:
                return {}
            pad = parts[1] + '=' * (-len(parts[1]) % 4)
            return json.loads(base64.urlsafe_b64decode(pad))
        except Exception:
            return {}

    async def get_access_token_status(self, tab=None) -> Dict[str, Any]:
        """Read the live accessToken cookie from the browser jar.
        Returns {present, member, eid, ttl_s, iat}. member=False covers
        missing, expired-claim-less AND guest-scope tokens (no `eid`) — a
        guest token renders the site header fine while every carts WRITE
        401s, which is exactly the blind spot that lost the 07-07 drops."""
        out = {'present': False, 'member': False, 'eid': None, 'ttl_s': -1.0, 'iat': None}
        try:
            tab = tab or await self.get_page()
            if not tab:
                return out
            cookies = await asyncio.wait_for(
                tab.send(uc.cdp.storage.get_cookies()), timeout=10.0)
            tok = None
            for c in cookies:
                if c.name == 'accessToken' and 'target.com' in (c.domain or ''):
                    tok = c.value
                    break
            if not tok:
                return out
            out['present'] = True
            claims = self._decode_jwt_claims(tok)
            out['eid'] = claims.get('eid')
            out['iat'] = claims.get('iat')
            exp = claims.get('exp')
            if isinstance(exp, (int, float)):
                out['ttl_s'] = float(exp) - time.time()
            out['member'] = bool(out['eid'])
            return out
        except Exception as e:
            self.logger.warning(f"[TOKEN] status read failed: {e}")
            return out

    async def refresh_access_token(self, tab=None, allow_nav: bool = True) -> bool:
        """Mint a fresh MEMBER accessToken using the flows the real site uses.

        Rung 1 — fire the SPA's own refresh endpoint from page context
        (authenticates via refreshToken/login-session cookies; ~0.5s, no
        nav). Fired as a CORS *simple request* (no custom headers → no
        preflight) so Set-Cookie applies even when the response body is
        CORS-opaque; success is verified by re-reading the cookie jar,
        never the response.
        Rung 2 (allow_nav) — delete the dead accessToken/idToken cookies
        and do a full page load: Target's edge mints a new token for any
        valid login-session that presents none. Endpoint-agnostic, so it
        keeps working if the gsp URL drifts. refreshToken/login-session
        are NEVER touched, so this can't log the account out.

        Returns True iff the jar ends up holding a freshly-minted member
        token. Logs which rung repaired (watch relogin/run logs the first
        night to see which rung the live site actually honors)."""
        try:
            tab = tab or await self.get_page()
            if not tab:
                return False

            def _is_fresh_member(st: Dict[str, Any]) -> bool:
                if not (st['member'] and st['ttl_s'] > 600):
                    return False
                # 'fresh mint' = iat within the last few minutes (a stale-but-
                # unexpired token that the server just 401'd must not pass).
                return st['iat'] is None or (time.time() - float(st['iat'])) < 300

            async def _fresh_member_token() -> bool:
                return _is_fresh_member(await self.get_access_token_status(tab))

            async def _poll_fresh_member(deadline_s: float) -> bool:
                """The SPA mints the accessToken via an async XHR after the
                page loads — proven by relogin's validate-first path minting a
                fresh token on a plain nav (2026-07-07). A fixed sleep raced
                it; poll the jar instead so we catch the mint the instant it
                lands (typically 0.5-3s) without over-waiting."""
                _end = time.time() + deadline_s
                while time.time() < _end:
                    if await _fresh_member_token():
                        return True
                    await asyncio.sleep(0.4)
                return False

            refresh_url = os.environ.get(
                'TARGET_TOKEN_REFRESH_URL',
                'https://gsp.target.com/gsp/token_refresh?client_id=ecom-web-1.0.0')
            try:
                await asyncio.wait_for(tab.evaluate(
                    f"""(async () => {{
                        try {{ await fetch({json.dumps(refresh_url)},
                            {{method:'POST', credentials:'include'}}); }} catch(_e) {{}}
                        return true;
                    }})()""", await_promise=True), timeout=8.0)
            except Exception as e:
                self.logger.warning(f"[TOKEN] refresh endpoint fetch errored: {e}")
            if await _poll_fresh_member(2.0):
                self._last_token_repair_ts = time.time()
                self.logger.info(f"[TOKEN] {self.account_id or 'session'}: fresh member token "
                                 f"minted via token_refresh endpoint")
                return True

            if not allow_nav:
                return False

            # Rung 2: forced edge re-mint. Deleting only the token cookies is
            # safe — login-session/refreshToken stay, and today's alternative
            # (full browser restart re-injecting the same dead token from
            # disk) provably never repaired anything on 07-07.
            minted = False
            try:
                # Delete ONLY the dead accessToken — NOT idToken. 2026-07-07
                # live test: deleting idToken too made the reload re-mint a
                # GUEST token (member=False) instead of refreshing the member
                # session. idToken/refreshToken/login-session are the member
                # identity the mint keys off; leave them intact.
                for _dom in ('.target.com', 'www.target.com', 'target.com'):
                    try:
                        await tab.send(uc.cdp.network.delete_cookies(name='accessToken', domain=_dom))
                    except Exception:
                        pass
                # Navigate to the AUTH-GATED /account page: the SPA cannot
                # render it without a member accessToken, so it's forced to
                # mint one (a plain homepage load may lazy-defer the mint).
                # This is the exact surface relogin's validate-first uses when
                # it re-harvests a fresh token from a live login-session.
                await asyncio.wait_for(tab.get('https://www.target.com/account'), timeout=25.0)
                minted = await _poll_fresh_member(8.0)
            except Exception as e:
                self.logger.warning(f"[TOKEN] delete+reload rung failed: {e}")
            if minted or await _fresh_member_token():
                self._last_token_repair_ts = time.time()
                self.logger.info(f"[TOKEN] {self.account_id or 'session'}: fresh member token "
                                 f"minted via cookie-delete + /account reload")
                try:
                    await self.save_session_state()
                except Exception:
                    pass
                return True
            st = await self.get_access_token_status(tab)
            self.logger.error(
                f"[TOKEN] {self.account_id or 'session'}: could NOT mint a member token "
                f"(present={st['present']} member={st['member']} ttl={st['ttl_s']:.0f}s) — "
                f"login-session likely dead (needs credential relogin)")
            return False
        except Exception as e:
            self.logger.error(f"[TOKEN] refresh_access_token error: {e}")
            return False

    async def ensure_fresh_access_token(self, tab=None, allow_nav: bool = True,
                                        min_ttl_s: "float | None" = None,
                                        force: bool = False) -> bool:
        """True iff the jar holds a member accessToken with ≥min_ttl_s left,
        minting a fresh one when it doesn't. force=True skips the health
        check and repairs unconditionally — for the ATC-401 path, where the
        server just PROVED the current token is dead regardless of its exp
        claim. Kill-switch TARGET_TOKEN_KEEPFRESH=0 → always False (callers
        fall through to their pre-2026-07-07 behavior)."""
        if os.environ.get('TARGET_TOKEN_KEEPFRESH', '1').lower() in ('0', 'false', 'no'):
            return False
        if min_ttl_s is None:
            min_ttl_s = float(os.environ.get('TARGET_TOKEN_MIN_TTL_S', '1800'))
        try:
            tab = tab or await self.get_page()
            if not tab:
                return False
            if not force:
                st = await self.get_access_token_status(tab)
                if st['member'] and st['ttl_s'] >= float(min_ttl_s):
                    return True
                self.logger.warning(
                    f"[TOKEN] {self.account_id or 'session'}: token unhealthy "
                    f"(present={st['present']} member={st['member']} ttl={st['ttl_s']:.0f}s "
                    f"< min {float(min_ttl_s):.0f}s) — repairing")
            return await self.refresh_access_token(tab, allow_nav=allow_nav)
        except Exception as e:
            self.logger.error(f"[TOKEN] ensure_fresh_access_token error: {e}")
            return False

    def _credential_relogin_allowed(self) -> bool:
        """Rate-gate the DESTRUCTIVE last-resort relogin. full_signout wipes
        the cookie jar; when the follow-up login is Shape-blocked ('password
        did NOT advance') the account is left signed OUT — on 07-07 'business'
        looped signout→blocked-login every 5-min sentinel tick from 08:27 on.
        Cap: TARGET_RELOGIN_MAX_PER_6H tries (default 2) with a
        TARGET_RELOGIN_COOLDOWN_S gap (default 1200s). When capped, alert
        once and leave the jar alone — the wrapper/nightly relogin (fresh
        focused browser, proven flow) is the real fixer."""
        now = time.time()
        self._relogin_attempt_times = [t for t in self._relogin_attempt_times
                                       if now - t < 6 * 3600.0]
        cooldown_s = float(os.environ.get('TARGET_RELOGIN_COOLDOWN_S', '1200'))
        max_per_window = int(os.environ.get('TARGET_RELOGIN_MAX_PER_6H', '2'))
        if self._relogin_attempt_times and (now - self._relogin_attempt_times[-1]) < cooldown_s:
            self.logger.warning(f"[RELOGIN] {self.account_id}: in {cooldown_s:.0f}s cooldown — "
                                f"skipping destructive relogin this tick")
            return False
        if len(self._relogin_attempt_times) >= max_per_window:
            if not self._relogin_capped_alerted:
                self._relogin_capped_alerted = True
                self._alert_critical(
                    f"{self.account_id}: credential relogin CAPPED "
                    f"({max_per_window} tries in 6h, all failed) — leaving cookies alone; "
                    f"account needs the wrapper/nightly relogin or a manual login")
            return False
        self._relogin_attempt_times.append(now)
        return True

    def _alert_critical(self, msg: str) -> None:
        """logger + logs/error_log.txt — the operator's morning-scan surface."""
        self.logger.error(f"[AUTH_CRITICAL] {msg}")
        try:
            os.makedirs('logs', exist_ok=True)
            with open('logs/error_log.txt', 'a', encoding='utf-8') as f:
                f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [AUTH_CRITICAL] {msg}\n")
        except Exception:
            pass

    async def _trigger_token_refresh(self) -> bool:
        """Trigger Target's auto-refresh flow by navigating to account page"""
        try:
            self.logger.info("[RETRY] Triggering token refresh flow...")

            tab = await self.get_page()
            if not tab:
                return False

            self.logger.info("Navigating to account page to trigger auto-refresh...")
            try:
                # Bounded: unbounded tab.get parks forever on a dead websocket
                # (2026-07-03 — every 5-min sentinel tick stacked another
                # parked coroutine instead of detecting the wedge).
                await asyncio.wait_for(tab.get("https://www.target.com/account"), timeout=25.0)
            except asyncio.TimeoutError:
                self.logger.error("[ERROR] Account nav timed out >25s — websocket wedged, refresh failed")
                return False
            except Exception as goto_error:
                self.logger.warning(f"Account navigation warning: {goto_error}")

            await asyncio.sleep(1)

            current_url = tab.url
            if 'login' in current_url.lower() or 'signin' in current_url.lower():
                self.logger.error("[ERROR] Token refresh failed - redirected to login")
                return False

            account_indicators = [
                '[data-test="@web/AccountLink"]',
                '[data-test="accountNav"]',
            ]
            text_indicators = ['Account', 'Orders', 'Profile']

            for selector in account_indicators:
                try:
                    element = await tab.select(selector, timeout=3)
                    if element:
                        self.logger.info("[OK] Token refresh successful - account page loaded")
                        await self.save_session_state()
                        self.last_validation = datetime.now()
                        self.validation_failures = 0
                        return True
                except Exception:
                    continue

            for text in text_indicators:
                try:
                    element = await tab.find(text, timeout=2)
                    if element:
                        self.logger.info("[OK] Token refresh successful")
                        await self.save_session_state()
                        self.last_validation = datetime.now()
                        self.validation_failures = 0
                        return True
                except Exception:
                    continue

            self.logger.warning("[WARNING] Token refresh ambiguous - no clear account indicators")
            return False

        except Exception as e:
            self.logger.error(f"[ERROR] Token refresh failed: {e}")
            return False

    async def refresh_session(self, guard: bool = True) -> bool:
        """Refresh session - navigation refresh first, browser restart as last resort.

        guard=True (default) acquires the drop-guard (the executor's page
        lock) before touching the browser, so a restart can never kill Chrome
        under an in-flight purchase. Pass guard=False ONLY when the caller
        already holds that lock (the sentinel's ensure_logged_in path) —
        asyncio.Lock is not reentrant and re-acquiring would deadlock."""
        _guard_lock = self._drop_guard_lock if guard else None
        if _guard_lock is not None:
            async with _guard_lock:
                async with self._refresh_lock:
                    return await self._refresh_session_impl()
        async with self._refresh_lock:
            return await self._refresh_session_impl()

    async def _refresh_session_impl(self) -> bool:
        try:
            self.logger.info("[RETRY] Refreshing session...")

            # STRATEGY 1: Navigation-based refresh (preserves browser)
            self.logger.info("Attempting navigation-based refresh...")
            if await self._trigger_token_refresh():
                self.logger.info("[OK] Session refreshed via navigation")
                return True

            # STRATEGY 2: Full browser restart (last resort)
            self.logger.warning("Navigation refresh failed, restarting browser...")
            await self._safe_cleanup()
            await asyncio.sleep(2)
            result = await self.initialize()

            if result:
                self.logger.info("[OK] Session restored after browser restart")
            else:
                self.logger.error("[ERROR] Session refresh failed after all attempts")
            return result

        except Exception as e:
            self.logger.error(f"[ERROR] Session refresh failed: {e}")
            return False

    def _load_account_credentials(self, cfg_path: "Path | None" = None) -> "tuple[str, str] | None":
        """Read this account's username/password from config/target_accounts.json
        (keyed by self.account_id). Returns None if no file / no match / placeholder.
        Only used as the last-resort recovery when a session is truly dead.
        cfg_path overrides the default location (tests)."""
        if not self.account_id:
            return None
        try:
            cfg = cfg_path or (Path(__file__).resolve().parents[2] / "config" / "target_accounts.json")
            if not cfg.exists():
                return None
            with open(cfg, "r", encoding="utf-8") as f:
                data = json.load(f)
            for acc in (data.get("accounts", []) if isinstance(data, dict) else []):
                if not isinstance(acc, dict):
                    continue
                if str(acc.get("account_id")) == str(self.account_id):
                    u, p = acc.get("username", ""), acc.get("password", "")
                    if u and p and p != "REPLACE_ME":
                        return u, p
                    return None
        except Exception as e:
            self.logger.warning(f"[RELOGIN] could not load credentials: {e}")
        return None

    async def _credential_relogin(self) -> bool:
        """Last-resort recovery: re-login this account with its stored credentials
        using the PROVEN relogin_one flow (full sign-out → username-first login with
        requestSubmit/verified-fill/KMSI). Only fires when refresh/restart could not
        restore the session. 2FA-off accounts log in headlessly; a genuine new-device
        challenge returns False (needs `relogin_one.py <id> --force` present once)."""
        creds = self._load_account_credentials()
        if not creds:
            self.logger.warning(f"[RELOGIN] no usable credentials for account_id={self.account_id} — cannot auto-relogin")
            return False
        username, password = creds
        try:
            tab = await self.get_page()
            if not tab:
                return False
            # Import the proven flow (root-level script; no import-time side effects).
            import sys as _sys
            _root = str(Path(__file__).resolve().parents[2])
            if _root not in _sys.path:
                _sys.path.insert(0, _root)
            import relogin_one as _relogin
            self.logger.info(f"[RELOGIN] credential re-login for {self.account_id} (proven flow)...")
            await _relogin.full_signout(tab)
            ok = await _relogin.login(tab, username, password)
            if ok:
                self.session_active = True
                self.last_validation = datetime.now()
                self.validation_failures = 0
                await self.save_session_state()
                self.logger.info(f"[RELOGIN] [OK] credential re-login succeeded for {self.account_id}")
                return True
            self.logger.error(f"[RELOGIN] credential re-login failed for {self.account_id} "
                              f"(challenge or bad creds — may need manual enrolment)")
            return False
        except Exception as e:
            self.logger.error(f"[RELOGIN] credential re-login error for {self.account_id}: {e}")
            return False

    async def validate_logged_in(self) -> bool:
        """READ-ONLY login check — navigate /account, True iff not redirected to
        login. No refresh, no restart, no save, no re-login. For honest status
        reporting (the authoritative signal; the homepage account-link is not)."""
        try:
            tab = await self.get_page()
            if not tab:
                return False
            try:
                await asyncio.wait_for(tab.get("https://www.target.com/account"), timeout=25.0)
            except Exception:
                pass
            await asyncio.sleep(2.5)
            url = (getattr(tab, 'url', '') or '').lower()
            if any(x in url for x in ('login', 'signin', 'sign-in', '/guest')):
                return False
            return '/account' in url
        except Exception:
            return False

    async def ensure_logged_in(self) -> bool:
        """Escalation ladder that GUARANTEES a valid logged-in session or reports
        failure. Used by the per-account Session Sentinel:
          0. member write-token check + repair (2026-07-07: THE check that
             matters — /account DOM kept passing all night while every ATC
             401'd on a dead token; this rung keeps the token hot 24/7 so a
             drop never starts with cold auth)
          1. navigation refresh (also validates we're really logged in)
          2. browser restart (reloads persisted cookies)
          3. credential re-login from the accounts file (true recovery;
             DESTRUCTIVE, rate-capped)
        Returns True iff the account ends up logged in with a live member token."""
        try:
            _keepfresh_on = os.environ.get('TARGET_TOKEN_KEEPFRESH', '1').lower() \
                not in ('0', 'false', 'no')
            if _keepfresh_on:
                if await self.ensure_fresh_access_token():
                    self.last_validation = datetime.now()
                    self.validation_failures = 0
                    # Keep the crash-recovery snapshot warm (the old ladder
                    # saved cookies every healthy tick via the /account nav).
                    try:
                        await self.save_session_state()
                    except Exception:
                        pass
                    return True
                # Transient CDP hiccups happen (check raced a warmup nav) —
                # one recheck before walking the heavy ladder.
                await asyncio.sleep(2)
                if await self.ensure_fresh_access_token():
                    self.logger.info(f"[SENTINEL] {self.account_id}: token healthy on recheck "
                                     f"(first check was transient)")
                    return True
                self.logger.warning(f"[SENTINEL] {self.account_id}: token repair failed — "
                                    f"escalating to navigation refresh")
                if await self._trigger_token_refresh():
                    # Nav says logged in — but a member token must ALSO mint,
                    # or ATC still 401s (the 07-07 blind spot).
                    if await self.ensure_fresh_access_token():
                        return True
            else:
                if await self._trigger_token_refresh():   # validates + refreshes
                    return True
                # A single /account redirect can be transient (the check raced a
                # concurrent warmup navigation). Re-check once before the heavy
                # restart — this is exactly what false-tripped 'primary' in testing.
                await asyncio.sleep(2)
                if await self._trigger_token_refresh():
                    self.logger.info(f"[SENTINEL] {self.account_id}: logged in on recheck (first check was transient)")
                    return True
            self.logger.warning(f"[SENTINEL] {self.account_id}: navigation refresh failed — escalating to restart")
            # guard=False: the sentinel wrapper already holds the executor's
            # page lock (drop-guard); re-acquiring it here would deadlock.
            if await self.refresh_session(guard=False):
                # refresh_session may have restarted; re-validate it's truly authed
                if await self._trigger_token_refresh():
                    if not _keepfresh_on:
                        return True
                    if await self.ensure_fresh_access_token():
                        return True
            self.logger.warning(f"[SENTINEL] {self.account_id}: restart did not restore auth — escalating to credential re-login")
            if not self._credential_relogin_allowed():
                return False
            ok = await self._credential_relogin()
            if ok:
                # Healthy again — reset the destructive-relogin budget.
                self._relogin_attempt_times.clear()
                self._relogin_capped_alerted = False
            return ok
        except Exception as e:
            self.logger.error(f"[SENTINEL] ensure_logged_in error for {self.account_id}: {e}")
            return False

    async def save_session_state(self) -> bool:
        """Save current session state to file"""
        try:
            if not self._active_tab:
                self.logger.warning("Cannot save session - no tab available")
                return False

            # Get all cookies via Storage.getCookies (non-deprecated CDP method)
            cookie_collection_failed = False
            try:
                cookies_raw = await asyncio.wait_for(
                    self._active_tab.send(uc.cdp.storage.get_cookies()), timeout=15.0)
                all_cookies = []
                for c in cookies_raw:
                    same_site = c.same_site.to_json() if c.same_site is not None else None
                    all_cookies.append({
                        'name': c.name,
                        'value': c.value,
                        'domain': c.domain,
                        'path': c.path,
                        'expires': float(c.expires) if c.expires is not None else -1,
                        'httpOnly': c.http_only,
                        'secure': c.secure,
                        'sameSite': same_site,
                        'session': c.session,
                    })
            except Exception as get_err:
                self.logger.warning(f"Could not get cookies via CDP: {get_err}")
                all_cookies = []
                cookie_collection_failed = True

            # SAFETY: refuse to clobber a healthy session file with an empty
            # cookie list. This bites at shutdown when the CDP WebSocket has
            # already closed — `cdp.storage.get_cookies()` raises, we fall
            # back to all_cookies=[], and writing that out replaces 84 valid
            # auth cookies with zero, locking the user out on next start.
            if cookie_collection_failed and not all_cookies:
                if self.session_path.exists():
                    try:
                        with open(self.session_path, 'r', encoding='utf-8') as f:
                            existing = json.load(f)
                        if existing.get('cookies'):
                            self.logger.warning(
                                f"Cookie collection failed AND on-disk session has "
                                f"{len(existing['cookies'])} cookies — refusing to overwrite. "
                                f"Preserving existing target.json."
                            )
                            print(f"[SESSION_SAVE] Preserving existing {self.session_path} "
                                  f"({len(existing['cookies'])} cookies) — CDP read failed")
                            return False
                    except Exception:
                        # Existing file unreadable — treat as no-existing and proceed
                        pass

            target_cookies = [c for c in all_cookies if 'target.com' in c.get('domain', '')]
            self.logger.info(f"[COOKIE_DEBUG] [SAVE] Found {len(all_cookies)} total cookies, {len(target_cookies)} for Target.com")

            # Fix critical auth cookies - convert session cookies to persistent
            critical_cookies = ['accessToken', 'idToken', 'refreshToken', 'login-session']
            future_timestamp = time.time() + (30 * 24 * 60 * 60)
            session_cookies_converted = 0

            for cookie in all_cookies:
                if cookie.get('sameSite') == 'Strict':
                    cookie['sameSite'] = 'Lax'
                if cookie.get('expires', -1) == -1:
                    cookie['expires'] = future_timestamp
                    session_cookies_converted += 1
                    if cookie.get('name') in critical_cookies:
                        self.logger.info(f"[COOKIE_FIX] Converting AUTH cookie '{cookie['name']}' to persistent (30 days)")
                        print(f"[COOKIE_SAVE] Auth cookie '{cookie['name']}' -> PERSISTENT (30 days)")

            if session_cookies_converted > 0:
                self.logger.info(f"[COOKIE_FIX] Converted {session_cookies_converted} session cookies to persistent")
                print(f"[COOKIE_SAVE] Converted {session_cookies_converted} session cookies to PERSISTENT")

            storage_state = {
                'cookies': all_cookies,
                'fingerprint': self.fingerprint_data,
                'saved_at': datetime.now().isoformat(),
            }

            # Try to save sessionStorage
            try:
                current_url = self._active_tab.url
                if 'target.com' in current_url:
                    session_storage_json = await self._active_tab.evaluate(
                        "() => JSON.stringify(Object.fromEntries(Object.entries(sessionStorage)))"
                    )
                    storage_state['sessionStorage'] = session_storage_json
                    self.logger.info("[OK] Saved sessionStorage from target.com")
                    print(f"[SESSION] Saved sessionStorage ({len(session_storage_json)} bytes)")
            except Exception as e:
                self.logger.debug(f"Could not save sessionStorage (non-fatal): {e}")

            # Write atomically
            temp_path = f"{self.session_path}.tmp"
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(storage_state, f, indent=2, ensure_ascii=False)

            os.replace(temp_path, self.session_path)

            file_size = os.path.getsize(self.session_path)
            self.logger.info(f"[COOKIE_DEBUG] [SAVE] Session file written: {self.session_path} ({file_size} bytes)")
            print(f"[SESSION_SAVE] Session saved successfully to {self.session_path}")
            return True

        except Exception as e:
            self.logger.error(f"[ERROR] Failed to save session state: {e}")
            return False

    async def is_healthy(self) -> bool:
        """Check if session is healthy and ready for use"""
        if not self.session_active or not self.browser:
            return False

        if self.validation_failures >= self.max_validation_failures:
            return False

        if self.last_validation:
            age = datetime.now() - self.last_validation
            if age > timedelta(minutes=10):
                return False

        # Check if the underlying WebSocket connection is still alive
        try:
            tab = getattr(self, '_active_tab', None)
            if tab:
                ws = getattr(tab, '_ws', None) or getattr(tab, 'websocket', None)
                if ws and getattr(ws, 'closed', False):
                    self.logger.warning("[HEALTH] WebSocket is closed - session unhealthy")
                    self.session_active = False
                    return False
        except Exception:
            pass

        return True

    async def cleanup(self):
        """Clean up resources - public interface"""
        self.logger.info("Starting session cleanup...")
        await self._safe_cleanup()
        self.logger.info("[OK] Session cleanup completed")

    def close_browser_sync(self):
        """Synchronously kill the entire Chrome process tree — safe to call from signal handlers.

        NOTE: browser.stop() is async and silently does nothing without await.
        On Windows we use 'taskkill /F /T /PID' which kills the main Chrome process
        AND all its children (renderers, GPU process, etc.) — this is the only reliable
        way to fully close Chrome on Windows.
        """
        import subprocess as _sp
        import sys as _sys

        global _chrome_pid

        # Collect PID before clearing references
        pid = getattr(self.browser, '_process_pid', None) if self.browser else None
        if not pid:
            pid = _chrome_pid  # fall back to module-level stored PID

        # Clear all refs immediately
        self.browser        = None
        self._active_tab    = None
        self.session_active = False
        _chrome_pid         = None   # clear so a second call is a no-op

        if not pid:
            print("[CLEANUP] No Chrome PID available — nothing to kill")
            return

        if _sys.platform == "win32":
            # taskkill /F /T kills the process AND every child (renderer, GPU, etc.)
            # proc.terminate() alone only kills the launcher; children keep running.
            try:
                result = _sp.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True, timeout=5
                )
                print(f"[CLEANUP] taskkill /F /T /PID {pid} → exit {result.returncode}")
            except Exception as e:
                print(f"[CLEANUP] taskkill failed: {e}")
        else:
            # Unix: kill the process group so all Chrome child processes die
            try:
                import os as _os, signal as _sig
                _os.kill(pid, _sig.SIGKILL)
                print(f"[CLEANUP] os.kill({pid}, SIGKILL) executed")
            except Exception as e:
                print(f"[CLEANUP] kill failed: {e}")

    def set_purchase_in_progress(self, in_progress: bool):
        """Set purchase lock to prevent session validation during purchases"""
        with self._purchase_lock:
            self.purchase_in_progress = in_progress
            if in_progress:
                self.logger.info("[PURCHASE_LOCK] Purchase started - validation paused")
                print("[SESSION] Purchase in progress - validation paused")
            else:
                self.logger.info("[PURCHASE_LOCK] Purchase complete - validation resumed")
                print("[SESSION] Purchase complete - validation resumed")

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
            'tab_connected': self._active_tab is not None,
            'persistent_context_mode': True,  # Always True for nodriver
        }

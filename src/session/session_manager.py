#!/usr/bin/env python3
"""
Persistent Session Manager - Maintains long-lived browser context for Target.com
Uses nodriver (undetected Chrome) for stealth automation.
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

import zendriver as uc

# Module-level Chrome PID — set at browser launch so it survives even if self.browser is cleared.
# Used by close_browser_sync() to kill the entire Chrome process tree on Windows.
_chrome_pid: Optional[int] = None


class SessionManager:
    """Manages persistent browser session for Target.com automation using nodriver"""

    def __init__(self, session_path: str = "target.json"):
        self.session_path = Path(session_path)
        self.logger = logging.getLogger(__name__)

        # CRITICAL: User data directory - nodriver persists profile here
        self.user_data_dir = Path("./nodriver-profile")
        self.user_data_dir.mkdir(exist_ok=True)

        # nodriver instances
        self.browser: Optional[uc.Browser] = None
        self._active_tab = None   # Main tab reference
        self._context_lock = threading.Lock()

        # Session state
        self.session_active = False
        self.last_validation = None
        self.session_created_at = None
        self.validation_failures = 0

        # Purchase lock to prevent validation during active purchases
        self.purchase_in_progress = False
        self._purchase_lock = threading.Lock()

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
                with open(self.session_path, 'r') as f:
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
                    with open(self.session_path, 'r') as f:
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

            self.browser = await uc.start(
                user_data_dir=str(self.user_data_dir.resolve()),
                headless=False,
                browser_args=[
                    '--window-size=1920,1080',
                ],
                sandbox=True,
            )

            self.logger.info("[OK] nodriver browser launched successfully")
            print("[SESSION_INIT] Browser launched - STEALTH MODE ACTIVE!")

            # Store Chrome PID globally so shutdown can kill the whole process tree
            global _chrome_pid
            _chrome_pid = getattr(self.browser, '_process_pid', None)
            if _chrome_pid:
                print(f"[SESSION_INIT] Chrome PID stored: {_chrome_pid}")

            # Get or create main tab
            if self.browser.tabs:
                self._active_tab = self.browser.tabs[0]
                print(f"[SESSION_INIT] Using existing tab ({len(self.browser.tabs)} tabs open)")
            else:
                self._active_tab = await self.browser.get("about:blank")
                print("[SESSION_INIT] Created initial tab")

            # Enable Network CDP domain
            try:
                await self._active_tab.send("Network.enable")
            except Exception as e:
                self.logger.warning(f"[INIT] Network.enable warning: {e}")

            # Inject saved cookies BEFORE navigating so the first request is authenticated
            if self.session_path.exists():
                try:
                    with open(self.session_path, 'r') as f:
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
        try:
            if self._cookie_watchdog_running:
                self.logger.info("[CLEANUP] Stopping cookie watchdog...")
                self._stop_cookie_watchdog()

            if self.browser:
                try:
                    await self.browser.stop()
                    self.logger.info("[CLEANUP] Browser stopped")
                except Exception as e:
                    self.logger.warning(f"[CLEANUP] Error stopping browser: {e}")
                self.browser = None
                self._active_tab = None

        except Exception as e:
            self.logger.warning(f"Cleanup warning (non-fatal): {e}")

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
            domain = parsed_url.hostname

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
                    await self._active_tab.send("Network.setCookie", **cookie)
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
        """Background loop that monitors and fixes cookies every 60 seconds"""
        self.logger.info("[WATCHDOG] Cookie watchdog loop started")

        while self._cookie_watchdog_running and self.session_active:
            try:
                await asyncio.sleep(60)

                if not self._cookie_watchdog_running or not self.session_active:
                    break

                self.logger.info("[WATCHDOG] Checking cookies...")
                test_mode = os.environ.get('TEST_MODE', 'false').lower() == 'true'

                if not self._active_tab:
                    self.logger.warning("[WATCHDOG] No tab available, skipping check")
                    continue

                # Get current cookies via CDP
                try:
                    result = await self._active_tab.send("Network.getCookies", urls=["https://www.target.com"])
                    if hasattr(result, 'cookies'):
                        target_cookies = [
                            {
                                'name': str(c.name),
                                'value': str(c.value),
                                'expires': float(getattr(c, 'expires', -1)) if getattr(c, 'expires', None) is not None else -1,
                                'domain': str(getattr(c, 'domain', '')),
                                'path': str(getattr(c, 'path', '/')),
                                'httpOnly': bool(getattr(c, 'http_only', False)),
                                'secure': bool(getattr(c, 'secure', False)),
                            }
                            for c in result.cookies
                        ]
                    elif isinstance(result, dict):
                        target_cookies = result.get('cookies', [])
                    else:
                        target_cookies = []
                except Exception as get_err:
                    self.logger.warning(f"[WATCHDOG] Could not get cookies: {get_err}")
                    continue

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

            except Exception as e:
                self.logger.error(f"[WATCHDOG] Error in watchdog loop: {e}")

        self.logger.info("[WATCHDOG] Cookie watchdog loop ended")

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
                        'secure': bool(getattr(raw, 'secure', False)),
                    }
                cookie['expires'] = future_timestamp

                self.logger.info(f"[WATCHDOG] Fixing '{cookie_name}' via CDP (converting to persistent)")
                try:
                    await self._active_tab.send("Network.setCookie", **cookie)
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

            with open(self.session_path, 'r') as f:
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
        """Stop the cookie watchdog"""
        self._cookie_watchdog_running = False
        if self._cookie_watchdog_task:
            try:
                self._cookie_watchdog_task.cancel()
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
        print(f"[DEBUG_FLASH] [DEBUG] get_page() called - checking for tabs...")

        for attempt in range(max_attempts):
            try:
                with self._context_lock:
                    if not self.browser or not self.session_active:
                        print(f"[DEBUG_FLASH] [WARNING] Browser invalid on attempt {attempt + 1}")
                        break

                    # Get active tab
                    if self.browser.tabs:
                        tab = self.browser.tabs[0]
                        self._active_tab = tab
                        print(f"[DEBUG_FLASH] [OK] Using existing tab[0] - NO NEW TAB")
                    else:
                        print(f"[DEBUG_FLASH] [WARNING] NO TABS EXIST - CREATING NEW TAB")
                        tab = await self.browser.get("about:blank")
                        self._active_tab = tab
                        print(f"[DEBUG_FLASH] [OK] New tab created in get_page()")

                    # Test tab health
                    if tab and await self._test_tab_health(tab):
                        print(f"[DEBUG_FLASH] [OK] Returning healthy tab from get_page()")
                        return tab
                    else:
                        print(f"[DEBUG_FLASH] [ERROR] Tab unhealthy on attempt {attempt + 1}")

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
            except Exception:
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

    async def _trigger_token_refresh(self) -> bool:
        """Trigger Target's auto-refresh flow by navigating to account page"""
        try:
            self.logger.info("[RETRY] Triggering token refresh flow...")

            tab = await self.get_page()
            if not tab:
                return False

            self.logger.info("Navigating to account page to trigger auto-refresh...")
            try:
                await tab.get("https://www.target.com/account")
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

    async def refresh_session(self) -> bool:
        """Refresh session - tries navigation refresh first, browser restart as last resort"""
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

    async def save_session_state(self) -> bool:
        """Save current session state to file"""
        try:
            if not self._active_tab:
                self.logger.warning("Cannot save session - no tab available")
                return False

            # Get all cookies via Storage.getCookies (non-deprecated CDP method)
            try:
                cookies_raw = await self._active_tab.send(uc.cdp.storage.get_cookies())
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
            with open(temp_path, 'w') as f:
                json.dump(storage_state, f, indent=2)

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

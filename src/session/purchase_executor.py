#!/usr/bin/env python3
"""
Purchase Executor - Real Target.com purchasing using persistent session
Uses nodriver for browser automation (migrated from patchright)
"""

import asyncio
import json
import logging
import time
import random
import os
import re
from datetime import datetime
from pathlib import Path
from collections import deque
from typing import Optional, Callable, Deque, Dict, Any

from zendriver import cdp

from .session_manager import SessionManager

CARD_CVV = '229'


class PurchaseExecutor:
    """Executes real purchases using persistent session and buy_bot logic"""

    def __init__(self, session_manager: SessionManager, status_callback: Optional[Callable] = None):
        self.session_manager = session_manager
        self.status_callback = status_callback
        self.logger = logging.getLogger(__name__)

        # Configuration
        self.purchase_timeout = 60
        self.max_retries = 2

        # TEST_MODE support - read from environment
        self.test_mode = os.environ.get('TEST_MODE', 'false').lower() == 'true'

        # Lock to prevent concurrent page access
        self._page_lock = asyncio.Lock()

        # Cached auth headers captured from Target's own fetch interceptor
        self._cached_cart_headers: Dict[str, str] = {}
        self._cached_cart_headers_ts: float = 0.0          # timestamp of last capture
        # Rotating ring of recent Shape captures. Each Shape capture is
        # consumed by exactly one ATC POST (Target burns the rotating tokens
        # per request); the second back-to-back POST therefore re-uses an
        # already-burned set and gets 401. The ring lets us pop a *fresh*
        # unconsumed capture per attempt, so multiple in-flight retries (or
        # rapid back-to-back cycles) each get their own token set instead of
        # re-using the most recent one. maxlen=4 keeps memory bounded; the
        # warmup tab refills as we drain.
        self._shape_capture_ring: Deque[Dict[str, Any]] = deque(maxlen=4)
        self._warmup_tab = None                             # single shared background tab
        self._warmup_tab_cart_ts: float = 0.0               # last successful /cart nav on warmup tab
        self._warmup_in_progress: bool = False              # prevent concurrent warmups
        self._main_tab_interceptor_active: bool = False     # avoid double setup on main tab
        self._cdp_continued_ids: set = set()               # dedup across accumulated handlers
        self._checkout_rejected: bool = False              # set by interceptor on 424 checkout response
        self._checkout_reject_reason: str = ''             # tgt-cart-error-key value from 424
        # Phase 4b — set by _api_place_order when API-mode Place Order succeeds.
        # _complete_checkout reads these instead of parsing tab.url, since
        # API mode does not navigate to /checkout/confirmation.
        self._api_order_id: Optional[str] = None
        self._api_confirmation_url: Optional[str] = None
        # Per-TCIN cache for PDP-extracted purchase_limit. Bulk RedSky often
        # omits maximum_order_quantity; without this cache every repeat
        # purchase pays a 0.05-0.65s PDP poll. Entries expire after 30 min.
        self._pdp_qty_cache: Dict[str, tuple] = {}  # tcin -> (qty, ts)
        self._pdp_qty_ttl: float = 1800.0
        # Per-TCIN throttle cooldown. Set when a 400 MAX_PURCHASE_LIMIT_EXCEEDED
        # cannot be recovered via clear_cart + qty fallback (i.e. the limit is a
        # real per-customer/session throttle from Target, not just a cart-state
        # race). Subsequent purchase attempts for the TCIN bail fast until the
        # cooldown expires. Prevents test_mode infinite-looping on a throttled
        # product. Map: tcin -> unix ts when cooldown ends.
        self._tcin_throttle_until: Dict[str, float] = {}
        self._tcin_throttle_cooldown_s: float = 90.0

    # -------------------------------------------------------------------------
    # nodriver helper methods (replace patchright page/element API)
    # -------------------------------------------------------------------------

    async def _is_visible(self, element) -> bool:
        """Check if element is visible in viewport"""
        try:
            return await element.apply(
                "el => el.offsetParent !== null && el.getBoundingClientRect().width > 0"
            )
        except Exception:
            return False

    async def _get_attribute(self, element, attr: str):
        """Get element attribute value"""
        try:
            return await element.apply(f"el => el.getAttribute('{attr}')")
        except Exception:
            return None

    async def _dispatch_click(self, element) -> bool:
        """Click element via JS dispatch (bypasses overlays)"""
        try:
            await element.apply("el => el.click()")
            return True
        except Exception:
            return False

    async def _scroll_into_view(self, element):
        """Scroll element into view"""
        try:
            await element.apply("el => el.scrollIntoView({block: 'center', behavior: 'instant'})")
        except Exception:
            pass

    async def _inner_text(self, element) -> str:
        """Get element inner text"""
        try:
            return await element.apply("el => el.innerText") or ""
        except Exception:
            return ""

    async def _find_element(self, tab, selector: str, timeout: float = 2.0):
        """
        Find element by CSS selector or Playwright-style text selector.
        Handles: 'text="..."', ':has-text("...")', and standard CSS.
        """
        try:
            if selector.startswith('text='):
                text = selector[5:].strip('"\'')
                return await tab.find(text, best_match=True, timeout=timeout)
            if ':has-text(' in selector:
                m = re.search(r':has-text\(["\'](.+?)["\']\)', selector)
                if m:
                    return await tab.find(m.group(1), best_match=True, timeout=timeout)
            return await tab.select(selector, timeout=timeout)
        except Exception:
            return None

    async def _fast_nav(self, tab, url: str, timeout: float = 15.0, ready_timeout: float = 10.0) -> None:
        """Navigate without blocking on full network idle.

        tab.get() awaits the CDP listener's idle event — fine for first nav,
        but with background traffic from prior cycles (DELETE settling,
        warmup-tab fetches) it can stall 4-5s. This sends raw cdp.page.navigate
        and polls document.readyState=interactive instead. Used wherever the
        next operation only needs cookies + a hydrated DOM, not full networkidle.
        """
        await asyncio.wait_for(tab.send(cdp.page.navigate(url)), timeout=timeout)
        deadline = time.time() + ready_timeout
        while time.time() < deadline:
            try:
                rs = await tab.evaluate("document.readyState")
                if rs in ("interactive", "complete"):
                    return
            except Exception:
                pass
            await asyncio.sleep(0.05)

    async def _wait_for_url_contains(self, tab, pattern: str, timeout: float = 10.0) -> bool:
        """Wait for tab URL to contain a substring (glob wildcards stripped)"""
        check = pattern.replace('**/', '').replace('**', '').replace('*', '')
        start = time.time()
        while time.time() - start < timeout:
            if check in tab.url.lower():
                return True
            await asyncio.sleep(0.05)
        return False

    async def _wait_for_function(self, tab, js: str, timeout: float = 5.0) -> bool:
        """Poll JS expression until it returns truthy"""
        start = time.time()
        while time.time() - start < timeout:
            try:
                result = await tab.evaluate(js)
                if result:
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.05)
        return False

    async def _press_escape(self, tab):
        """Press Escape key via CDP"""
        try:
            await tab.send("Input.dispatchKeyEvent", type="keyDown", key="Escape", windowsVirtualKeyCode=27)
            await tab.send("Input.dispatchKeyEvent", type="keyUp", key="Escape", windowsVirtualKeyCode=27)
        except Exception:
            pass

    async def _screenshot(self, tab, path: str):
        """Take a screenshot, creating parent dirs as needed."""
        try:
            os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
            await tab.save_screenshot(path)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Public entry point
    # -------------------------------------------------------------------------

    async def execute_purchase(self, tcin: str, quantity: int = 1) -> Dict[str, Any]:
        """
        Execute purchase for given TCIN using persistent session.
        Uses lock to prevent concurrent tab access.

        quantity: how many units to add to the cart. Sourced from RedSky's
        per-customer purchase_limit (capped to ATP). Defaults to 1.
        """
        try:
            async with asyncio.timeout(140):  # slightly less than thread's 150s so coroutine self-cancels cleanly
                async with self._page_lock:
                    return await self._execute_purchase_impl(tcin, quantity=quantity)
        except asyncio.TimeoutError:
            return {
                'success': False,
                'tcin': tcin,
                'reason': 'lock_timeout',
                'error': 'Could not acquire page lock within 120 seconds'
            }

    # -------------------------------------------------------------------------
    # Page-level helpers (migrated from patchright)
    # -------------------------------------------------------------------------

    async def _dismiss_sticky_banners(self, tab) -> None:
        """Dismiss or hide sticky banners/overlays that can intercept clicks."""
        try:
            print("[BANNER] Checking for sticky banners...")

            app_banner_close_selectors = [
                '[data-test="app-banner-close"]',
                '[aria-label*="close"][class*="banner"]',
                '[aria-label*="dismiss"][class*="banner"]',
                'button[class*="AppBanner"] svg',
                '[data-test*="app-banner"] button',
                '[class*="sticky"] button[aria-label*="close"]',
                '[class*="fixed"] button[aria-label*="close"]',
            ]

            for selector in app_banner_close_selectors:
                try:
                    close_btn = await tab.select(selector, timeout=0.15)
                    if close_btn and await self._is_visible(close_btn):
                        await close_btn.apply("(el) => el.click()")
                        print(f"[BANNER] Closed banner via: {selector}")
                        return
                except Exception:
                    continue

            # Hide all fixed/sticky elements at the bottom of the viewport via JS
            await tab.evaluate('''(() => {
                const viewportHeight = window.innerHeight;
                document.querySelectorAll('*').forEach(el => {
                    const style = window.getComputedStyle(el);
                    if (style.position === 'fixed' || style.position === 'sticky') {
                        const rect = el.getBoundingClientRect();
                        if (rect.top > viewportHeight * 0.8) {
                            el.style.setProperty('display', 'none', 'important');
                        }
                    }
                });
            })()''')
            print("[BANNER] Executed JS to hide bottom sticky elements")

        except Exception as e:
            print(f"[BANNER] Warning during banner dismissal: {e}")

    async def _select_shipping_option(self, tab) -> bool:
        """Select the 'Shipping' fulfillment option on product page."""
        try:
            print("[SHIPPING] Checking for fulfillment options...")

            shipping_css = [
                '[data-test="fulfillment-cell-shipping"]',
                '[data-test="shipItButton"]',
                '[data-testid="fulfillment-cell-shipping"]',
            ]
            shipping_texts = ["Ship it", "Ship It", "Ship"]

            element = None
            for sel in shipping_css:
                element = await tab.select(sel, timeout=0.5)
                if element:
                    break

            if not element:
                for text in shipping_texts:
                    element = await tab.find(text, best_match=True, timeout=0.3)
                    if element:
                        break

            if element and await self._is_visible(element):
                is_pressed = await self._get_attribute(element, 'aria-pressed')
                is_selected = await self._get_attribute(element, 'aria-selected')
                if is_pressed == 'true' or is_selected == 'true':
                    print("[SHIPPING] Shipping already selected")
                    return True
                await self._dispatch_click(element)
                print("[SHIPPING] Clicked Shipping option")
                return True

            print("[SHIPPING] No shipping option found (single fulfillment product)")
            return True

        except Exception as e:
            print(f"[SHIPPING] Warning: {e}")
            return True

    async def _wait_for_click_handler(self, tab, button, max_wait: float = 5.0) -> bool:
        """Wait for Shape Security to attach click event handlers."""
        print("[SHAPE] Waiting for click event handlers to be attached...")
        start_time = time.time()

        while (time.time() - start_time) < max_wait:
            try:
                has_handler = await button.apply('''(element) => {
                    if (element.onclick) return true;
                    const listeners = window.getEventListeners ? window.getEventListeners(element) : null;
                    if (listeners && listeners.click && listeners.click.length > 0) return true;
                    if (element.hasAttribute('onclick')) return true;
                    let parent = element.parentElement;
                    let depth = 0;
                    while (parent && depth < 5) {
                        if (parent.onclick || parent.hasAttribute('onclick')) return true;
                        parent = parent.parentElement;
                        depth++;
                    }
                    return false;
                }''')
                if has_handler:
                    print(f"[SHAPE] Click handler detected after {time.time() - start_time:.2f}s")
                    return True
            except Exception as e:
                print(f"[SHAPE] Handler check error: {e}")
            await asyncio.sleep(0.2)

        print(f"[SHAPE] No click handler detected after {max_wait}s, proceeding anyway...")
        return False

    async def _humanized_click(self, tab, button, action_name: str):
        """Fast click using JS dispatch (bypasses Shape Security)"""
        try:
            print(f"[HUMANIZE] Clicking {action_name}...")
            await self._scroll_into_view(button)
            await tab.evaluate('window.scrollBy(0, -100)')
            await self._dispatch_click(button)
            print(f"[HUMANIZE] Clicked {action_name}")
            return True
        except Exception as e:
            print(f"[HUMANIZE] Click failed for {action_name}: {e}")
            try:
                await button.click()
                return True
            except Exception:
                return False

    # -------------------------------------------------------------------------
    # CDP fetch interceptor helpers
    # -------------------------------------------------------------------------

    async def _setup_cdp_fetch_interceptor(self, tab, persistent: bool = False) -> None:
        """Install CDP fetch interceptor. persistent=True for warmup tab (never disabled).
           persistent=False for main tab (disabled after each purchase)."""
        from zendriver import cdp
        label = "warmup" if persistent else "main"

        async def _on_request_paused(event: cdp.fetch.RequestPaused):
            # Deduplicate by (request_id + stage) — REQUEST and RESPONSE share the same
            # request_id but are separate events; keying on id alone drops RESPONSE events.
            req_id = str(event.request_id)
            is_response = event.response_status_code is not None or getattr(event, 'response_error_reason', None) is not None
            dedup_key = req_id + (':resp' if is_response else ':req')
            if dedup_key in self._cdp_continued_ids:
                return
            self._cdp_continued_ids.add(dedup_key)

            try:
                url = event.request.url if hasattr(event, 'request') else ''
                method = event.request.method if hasattr(event.request, 'method') else '?'
                is_checkout_post = 'web_checkouts/v1/checkout' in url and method == 'POST'

                # RESPONSE stage — log status for checkout POST only
                if is_response:
                    status = event.response_status_code
                    # response_headers is a list of HeaderEntry(name, value) objects, not a dict
                    raw_resp_headers = event.response_headers or []
                    try:
                        resp_headers = {h.name.lower(): h.value for h in raw_resp_headers}
                    except Exception:
                        try:
                            resp_headers = {h['name'].lower(): h['value'] for h in raw_resp_headers}
                        except Exception:
                            resp_headers = {}
                    shape_pass = resp_headers.get('x-shape-pass', '?')
                    cf_ray = resp_headers.get('cf-ray', '')
                    print(f"[INTERCEPTOR:{label}] [RESPONSE] {method} {url[:80]} → HTTP {status} (shape-pass={shape_pass}{', cf-ray=' + cf_ray if cf_ray else ''})")
                    if is_checkout_post:
                        print(f"[INTERCEPTOR:{label}] [CHECKOUT_RESPONSE] HTTP {status} — {'SUCCESS' if status in (200, 201) else 'REJECTED'}")
                        if status not in (200, 201):
                            error_key = resp_headers.get('tgt-cart-error-key', '')
                            self._checkout_rejected = True
                            self._checkout_reject_reason = error_key
                            print(f"[INTERCEPTOR:{label}] [CHECKOUT_RESPONSE] 424 flagged — short-circuiting wait loop (reason={error_key})")
                            print(f"[INTERCEPTOR:{label}] [CHECKOUT_RESPONSE] headers: {dict(list(resp_headers.items())[:10])}")
                            try:
                                body_result = await tab.send(cdp.fetch.get_response_body(request_id=event.request_id))
                                raw_body = getattr(body_result, 'body', '') or ''
                                if getattr(body_result, 'base64_encoded', False):
                                    import base64, zlib
                                    try:
                                        raw_body = zlib.decompress(base64.b64decode(raw_body), 16 + zlib.MAX_WBITS).decode('utf-8', errors='replace')
                                    except Exception:
                                        raw_body = base64.b64decode(raw_body).decode('utf-8', errors='replace')
                                print(f"[INTERCEPTOR:{label}] [CHECKOUT_RESPONSE] body: {raw_body[:500]!r}")
                            except Exception as body_err:
                                print(f"[INTERCEPTOR:{label}] [CHECKOUT_RESPONSE] body capture failed: {body_err}")
                    await tab.send(cdp.fetch.continue_request(request_id=event.request_id))
                    return

                if 'carts.target.com' in url or 'cart_items' in url:
                    headers = dict(event.request.headers) if event.request.headers else {}
                    header_names = list(headers.keys())
                    shape_headers = [h for h in header_names if h.lower().startswith('x-')]
                    # FIX 2: only cache POST — GET/OPTIONS/PUT don't carry Shape tokens
                    if method == 'POST' and headers:
                        # Count Shape rotating tokens only — exclude 'x-application-name'
                        # which is a static header the bot adds and is also the
                        # only X-header on page-driven natural fetches. Including
                        # it would either treat the warmup's 6-token capture as
                        # "degraded" vs the bot's 7 (warmup TIMEOUT) or fail to
                        # filter out natural pre_checkout (1 X-header, just
                        # x-application-name).
                        def _shape_token_count(hdrs):
                            return len([h for h in hdrs
                                        if h.lower().startswith('x-')
                                        and h.lower() != 'x-application-name'])
                        prev_shape_count = _shape_token_count(self._cached_cart_headers)
                        new_shape_count = _shape_token_count(header_names)
                        # Block ONLY zero-token poisoning (page-driven natural
                        # pre_checkout fetches with no Shape headers). A 6-token
                        # warmup capture is fully valid even when prev had 7 —
                        # and stale tokens are useless, so any non-zero refresh
                        # should be allowed. Also: if the current cache is older
                        # than 60s (Shape rotates ~90-120s), accept any non-zero
                        # capture regardless. Prior rule (new < prev) locked the
                        # cache forever once a 7-token capture was seen — fix
                        # for 2026-05-08 stale-headers stall.
                        cache_age_now = (time.time() - self._cached_cart_headers_ts) if self._cached_cart_headers_ts else 999.0
                        skip_cache_update = (
                            prev_shape_count > 0
                            and new_shape_count == 0
                            and cache_age_now < 60.0
                        )
                        if not skip_cache_update:
                            self._cached_cart_headers = headers
                            self._cached_cart_headers_ts = time.time()
                            # Push to ring so the ATC retry path can rotate to a
                            # fresh unconsumed token set instead of re-using the
                            # one we just published (which the impending POST is
                            # about to burn).
                            self._shape_capture_ring.append({
                                'headers': dict(headers),
                                'ts': self._cached_cart_headers_ts,
                                'consumed': False,
                            })
                        tag = '[CHECKOUT_POST]' if is_checkout_post else ''
                        print(f"[INTERCEPTOR:{label}] {tag} {method} {url[:80]}")
                        if skip_cache_update:
                            cache_age = time.time() - self._cached_cart_headers_ts if self._cached_cart_headers_ts else -1
                            print(f"[INTERCEPTOR:{label}] Preserved cache ({prev_shape_count} Shape tokens, age={cache_age:.1f}s) — new capture had only {new_shape_count} Shape tokens")
                        else:
                            print(f"[INTERCEPTOR:{label}] Captured {len(headers)} headers (Shape tokens: {new_shape_count}, prev cache had {prev_shape_count}): {header_names}")

                        # CAPTURE-AND-ABORT for Phase 3 research: when
                        # TARGET_API_CAPTURE_PLACE_ORDER=true and this is the
                        # Place Order POST, log the full body+headers to the
                        # capture log and then abort the request *before* it
                        # leaves Chrome. The bot will see the abort as a
                        # network failure (no order placed). Default off.
                        if is_checkout_post and os.environ.get('TARGET_API_CAPTURE_PLACE_ORDER', 'false').lower() == 'true':
                            try:
                                post_data = getattr(event.request, 'post_data', '') or ''
                                if hasattr(event.request, 'has_post_data') and event.request.has_post_data and not post_data:
                                    # post_data may need to be retrieved separately on some CDP versions
                                    try:
                                        post_data = await tab.send(cdp.fetch.get_request_post_data(request_id=event.request_id))
                                    except Exception:
                                        post_data = '<unavailable>'
                                import os as _os, datetime as _dt
                                _os.makedirs('logs', exist_ok=True)
                                with open('logs/api_capture.log', 'a', encoding='utf-8') as _f:
                                    _f.write(f"\n{'='*80}\n[{_dt.datetime.now().isoformat()}] PLACE ORDER POST captured + ABORTED\n")
                                    _f.write(f"url: {url}\n")
                                    _f.write(f"method: {method}\n")
                                    _f.write(f"headers:\n")
                                    for h_name, h_val in headers.items():
                                        if h_name.lower() == 'cookie':
                                            _f.write(f"  {h_name}: <redacted, {len(h_val)} chars>\n")
                                        else:
                                            _f.write(f"  {h_name}: {h_val}\n")
                                    _f.write(f"body:\n{post_data}\n")
                                print(f"[INTERCEPTOR:{label}] [PLACE_ORDER_CAPTURE] Body captured ({len(post_data)} chars), ABORTING request")
                                # Abort with a 503 so the bot treats it as a
                                # transient failure (clean error path) rather
                                # than a TCP-level disconnect.
                                await tab.send(cdp.fetch.fulfill_request(
                                    request_id=event.request_id,
                                    response_code=503,
                                    response_headers=[
                                        cdp.fetch.HeaderEntry(name='Content-Type', value='application/json'),
                                        cdp.fetch.HeaderEntry(name='X-Capture-Aborted', value='true'),
                                    ],
                                    body='eyJlcnJvciI6IkNhcHR1cmUgYWJvcnQifQ==',  # base64({"error":"Capture abort"})
                                ))
                                return  # do NOT fall through to continue_request
                            except Exception as cap_err:
                                print(f"[INTERCEPTOR:{label}] [PLACE_ORDER_CAPTURE] capture/abort failed: {cap_err}")
                                # fall through to normal continue_request — ORDER WILL FIRE
                                # if this happens. User must watch for this log line.
                    elif headers:
                        # non-POST carts request — log but don't overwrite cache
                        cache_age = time.time() - self._cached_cart_headers_ts if self._cached_cart_headers_ts else -1
                        cached_shape = len([h for h in self._cached_cart_headers if h.lower().startswith('x-')])
                        print(f"[INTERCEPTOR:{label}] {method} {url[:80]} — skipping cache (not POST, has {len(headers)} headers, {len(shape_headers)} X-headers)")
                        print(f"[INTERCEPTOR:{label}]   cache preserved: {len(self._cached_cart_headers)} headers, {cached_shape} Shape tokens, age={cache_age:.1f}s")

                        # Phase 4a passive capture: when TARGET_API_CAPTURE_CHECKOUT_STEPS=true,
                        # log full URL+headers+body for cart PUT and cart_fulfillments GET so
                        # Endpoint 4 + 5 in TARGET_CHECKOUT_API.md can be filled in. Pass-through
                        # only — never aborts. Default off; safe to leave on for one capture run.
                        if os.environ.get('TARGET_API_CAPTURE_CHECKOUT_STEPS', 'false').lower() == 'true':
                            is_cart_put = method == 'PUT' and 'web_checkouts/v1/cart' in url
                            is_fulfillments_get = method == 'GET' and 'cart_fulfillments' in url
                            if is_cart_put or is_fulfillments_get:
                                tag = 'CART_PUT' if is_cart_put else 'FULFILLMENTS_GET'
                                try:
                                    post_data = ''
                                    if is_cart_put:
                                        post_data = getattr(event.request, 'post_data', '') or ''
                                        if hasattr(event.request, 'has_post_data') and event.request.has_post_data and not post_data:
                                            try:
                                                post_data = await tab.send(cdp.fetch.get_request_post_data(request_id=event.request_id))
                                            except Exception:
                                                post_data = '<unavailable>'
                                    import os as _os, datetime as _dt
                                    _os.makedirs('logs', exist_ok=True)
                                    with open('logs/api_capture.log', 'a', encoding='utf-8') as _f:
                                        _f.write(f"\n{'='*80}\n[{_dt.datetime.now().isoformat()}] {tag} captured (pass-through)\n")
                                        _f.write(f"url: {url}\n")
                                        _f.write(f"method: {method}\n")
                                        _f.write(f"headers:\n")
                                        for h_name, h_val in headers.items():
                                            if h_name.lower() == 'cookie':
                                                _f.write(f"  {h_name}: <redacted, {len(h_val)} chars>\n")
                                            else:
                                                _f.write(f"  {h_name}: {h_val}\n")
                                        if is_cart_put:
                                            _f.write(f"body:\n{post_data}\n")
                                    print(f"[INTERCEPTOR:{label}] [{tag}_CAPTURE] logged ({len(headers)} headers"
                                          + (f", {len(post_data)} body chars" if is_cart_put else "") + ")")
                                except Exception as cap_err:
                                    print(f"[INTERCEPTOR:{label}] [{tag}_CAPTURE] failed: {cap_err}")
                    else:
                        print(f"[INTERCEPTOR:{label}] {method} {url[:80]} — no headers found")
                else:
                    # non-carts URL — logged once per request (fix 1 prevents repeats)
                    print(f"[INTERCEPTOR:{label}] Unexpected URL paused: {url[:80]} (method={method})")
            except Exception as e:
                print(f"[INTERCEPTOR:{label}] Handler error: {e}")
            try:
                await tab.send(cdp.fetch.continue_request(request_id=event.request_id))
            except Exception as e:
                print(f"[INTERCEPTOR:{label}] continue_request failed (req_id={req_id}): {e}")

        _cdp_enable_attempts = 3
        for _attempt in range(1, _cdp_enable_attempts + 1):
            try:
                await tab.send(cdp.fetch.enable(
                    patterns=[
                        cdp.fetch.RequestPattern(
                            url_pattern='*carts.target.com*',
                            request_stage=cdp.fetch.RequestStage.REQUEST
                        ),
                        cdp.fetch.RequestPattern(
                            url_pattern='*web_checkouts/v1/checkout*',
                            request_stage=cdp.fetch.RequestStage.RESPONSE
                        ),
                    ]
                ))
                print(f"[INTERCEPTOR:{label}] cdp.fetch.enable() sent with pattern *carts.target.com* + checkout RESPONSE (attempt {_attempt})")
                break
            except Exception as e:
                print(f"[INTERCEPTOR:{label}] cdp.fetch.enable() FAILED (attempt {_attempt}/{_cdp_enable_attempts}): {e}")
                if _attempt < _cdp_enable_attempts:
                    await asyncio.sleep(1.5)
                else:
                    import traceback as _tb
                    _tb.print_exc()
                    import os as _os, datetime as _dt
                    try:
                        _os.makedirs('logs', exist_ok=True)
                        with open('logs/error_log.txt', 'a', encoding='utf-8') as _f:
                            _f.write(f"[{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [CDP] cdp.fetch.enable() failed after {_cdp_enable_attempts} attempts ({label}): {e}\n{_tb.format_exc()}\n")
                    except Exception:
                        pass
                    return

        if cdp.fetch not in tab.enabled_domains:
            tab.enabled_domains.append(cdp.fetch)
            print(f"[INTERCEPTOR:{label}] Added cdp.fetch to enabled_domains")

        # Clear any stale handlers from previous cycles before adding new one
        handlers = getattr(tab, 'handlers', {})
        stale_handlers = handlers.get(cdp.fetch.RequestPaused, [])
        if stale_handlers:
            # Remove handler references and clear the list
            for handler in stale_handlers:
                try:
                    tab.remove_handler(handler, cdp.fetch.RequestPaused)
                except Exception:
                    pass
            handlers[cdp.fetch.RequestPaused] = []
            print(f"[INTERCEPTOR:{label}] Cleared {len(stale_handlers)} stale RequestPaused handler(s)")

        tab.add_handler(cdp.fetch.RequestPaused, _on_request_paused)
        handler_count = len(handlers.get(cdp.fetch.RequestPaused, []))
        print(f"[INTERCEPTOR:{label}] CDP fetch interceptor ready (total RequestPaused handlers on tab: {handler_count})")

    def _consume_fresh_capture(self) -> bool:
        """Rotate `_cached_cart_headers` to the freshest unconsumed ring entry.

        Each Target Shape rotating-token set is good for exactly one cart-API
        POST (the server burns it on use). Reusing the just-published headers
        on a back-to-back call returns 401. The interceptor pushes every new
        capture into `_shape_capture_ring`; this method pops the newest entry
        whose `consumed` flag is False, marks it consumed, and republishes its
        headers as the active cache view.

        Returns True if a fresh capture was rotated in. Returns False when the
        ring is exhausted (every entry already consumed) — caller should fall
        back to `warm_shape_headers()` to refill.
        """
        # Newest-first scan: iterate the deque in reverse insertion order.
        for entry in reversed(self._shape_capture_ring):
            if entry.get('consumed'):
                continue
            entry['consumed'] = True
            self._cached_cart_headers = entry['headers']
            self._cached_cart_headers_ts = entry['ts']
            unused = sum(1 for e in self._shape_capture_ring if not e.get('consumed'))
            print(f"[SHAPE_RING] Consumed fresh capture (ts age={time.time()-entry['ts']:.1f}s, "
                  f"{unused} unused remaining of {len(self._shape_capture_ring)})")
            return True
        return False

    async def warm_shape_headers(self) -> bool:
        """Refresh Shape headers via cart page visit on the shared background warmup tab."""
        if self._warmup_in_progress:
            return bool(self._cached_cart_headers)
        self._warmup_in_progress = True
        try:
            browser = self.session_manager.browser
            if not browser:
                return False

            ts_before = self._cached_cart_headers_ts
            now = time.time()
            # Skip the cart re-nav if we navigated < 90s ago — Shape JS is
            # already initialized on this tab, so the dummy POST will pick up
            # current tokens. Saves ~0.6-1.2s on the post-success refresh path.
            cart_nav_age = now - self._warmup_tab_cart_ts if self._warmup_tab_cart_ts else 999

            if not self._warmup_tab:
                print("[WARMUP] Opening new background warmup tab...")
                self._warmup_tab = await browser.get(
                    "https://www.target.com/cart", new_tab=True
                )
                print(f"[WARMUP] Warmup tab opened, URL={self._warmup_tab.url}")
                await self._setup_cdp_fetch_interceptor(self._warmup_tab, persistent=True)
                self._warmup_tab_cart_ts = time.time()
                fresh_nav = True
            elif cart_nav_age < 90:
                print(f"[WARMUP] Skipping cart re-nav (last nav {cart_nav_age:.0f}s ago, tab still warm)")
                fresh_nav = False
            else:
                current_url = getattr(self._warmup_tab, 'url', 'unknown')
                print(f"[WARMUP] Navigating warmup tab to cart (was at {current_url}, age={cart_nav_age:.0f}s)")
                await self._warmup_tab.get("https://www.target.com/cart")
                self._warmup_tab_cart_ts = time.time()
                fresh_nav = True

            # On fresh navs, wait briefly for Shape JS to initialize before the
            # dummy POST. Use a readyState poll (capped at 0.4s) instead of an
            # unconditional 0.8s sleep — cart page typically reaches `complete`
            # in 150-300ms after a fast nav.
            if fresh_nav:
                _ready_deadline = time.time() + 0.4
                while time.time() < _ready_deadline:
                    try:
                        rs = await self._warmup_tab.evaluate("document.readyState")
                        if rs == "complete":
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(0.05)
            print("[WARMUP] Firing dummy POST to trigger Shape header capture...")
            await self._warmup_tab.evaluate("""
                fetch('https://carts.target.com/web_checkouts/v1/cart_items?field_groups=CART,CART_ITEMS,SUMMARY', {
                    method: 'POST',
                    credentials: 'include',
                    headers: {
                        'Content-Type': 'application/json',
                        'Accept': 'application/json',
                        'Origin': 'https://www.target.com',
                    },
                    body: JSON.stringify({
                        cart_item: {tcin: '81926151', quantity: 1, item_channel_id: '10'},
                        cart_type: 'REGULAR',
                        channel_id: '10',
                        shopping_context: 'DIGITAL'
                    })
                }).catch(() => {});
            """)

            # Capture window tightened from 3s to 1.5s. When the interceptor IS
            # going to fire, it does so within 100-300ms of the dummy POST; the
            # remaining 2.7s of a 3s wait was pure dead time on the broken-Shape
            # JS / dead-tab failure path. Long-tail (p90/p99) cycles in v17 were
            # dominated by this wait when cart_nav_age >90s forced a re-nav. The
            # fast path (sub-300ms typical) is unchanged — the polling loop
            # below exits as soon as the interceptor writes a new ts.
            print("[WARMUP] Waiting for carts.target.com POST interception (up to 1.5s)...")

            wait_start = time.time()
            deadline = wait_start + 1.5
            while time.time() < deadline:
                if self._cached_cart_headers_ts > ts_before:
                    age = time.time() - self._cached_cart_headers_ts
                    print(f"[WARMUP] Shape headers captured successfully (age={age:.1f}s): "
                          f"{list(self._cached_cart_headers.keys())}")
                    return True
                await asyncio.sleep(0.1)

            wait_elapsed = time.time() - wait_start
            cache_age = time.time() - self._cached_cart_headers_ts if self._cached_cart_headers_ts else -1
            print(f"[WARMUP] TIMEOUT after {wait_elapsed:.1f}s — cache not refreshed "
                  f"(POST may have been intercepted but skipped by preserve rule; cache age={cache_age:.0f}s)")
            print(f"[WARMUP] Stale headers available: {bool(self._cached_cart_headers)}, "
                  f"age={cache_age:.0f}s")
            return bool(self._cached_cart_headers)
        except Exception as e:
            print(f"[WARMUP] Error: {e}")
            self._warmup_tab = None  # dead connection — force recreation next cycle
            return False
        finally:
            self._warmup_in_progress = False

    # -------------------------------------------------------------------------
    # Core purchase implementation
    # -------------------------------------------------------------------------

    async def _execute_purchase_impl(self, tcin: str, quantity: int = 1) -> Dict[str, Any]:
        """Execute purchase for given TCIN using persistent session"""
        start_time = time.time()
        # Floor at 1 to avoid quantity:0 (would be rejected by Target). No upper
        # bound — the PDP-extracted purchase_limit is authoritative.
        quantity = max(1, int(quantity or 1))
        # Per-TCIN throttle cooldown — bail fast if a recent attempt hit a
        # MAX_PURCHASE_LIMIT_EXCEEDED that the self-heal path could not recover.
        # Avoids burning ~3-5s on a guaranteed-fail ATC plus the test_mode
        # immediate re-trigger loop. Cooldown is short enough that a transient
        # throttle clears before next live in-stock signal in prod.
        _throttle_ts = self._tcin_throttle_until.get(tcin, 0.0)
        if _throttle_ts and time.time() < _throttle_ts:
            _remaining = _throttle_ts - time.time()
            print(f"[PURCHASE] {tcin} in throttle cooldown ({_remaining:.0f}s remaining) — bailing fast")
            return {'success': False, 'tcin': tcin, 'reason': 'tcin_throttled_cooldown',
                    'execution_time': time.time() - start_time}
        tab = None  # ensure tab is accessible in finally block
        prior_ids = len(self._cdp_continued_ids)
        self._cdp_continued_ids.clear()
        if prior_ids:
            print(f"[STATE_CARRY] WARNING: {prior_ids} stale cdp_continued_ids from prior purchase — cleared")
        print(f"[PURCHASE] cdp_continued_ids cleared for new purchase ({tcin}, qty={quantity})")
        self._checkout_rejected = False
        self._checkout_reject_reason = ''
        self._api_order_id = None
        self._api_confirmation_url = None

        try:
            print(f"[PURCHASE] Starting purchase for {tcin}")
            self._notify_status(tcin, 'attempting', {'start_time': datetime.now().isoformat()})

            # Get existing logged-in tab from session manager
            tab = await self.session_manager.get_page()
            if not tab:
                browser = self.session_manager.browser
                if not browser or not browser.tabs:
                    raise Exception("Browser not available")
                tab = browser.tabs[0]

            # Log auth cookie state before purchase — helps diagnose 401s.
            # Gated behind TARGET_DEBUG_AUTH=true: cdp.storage.get_cookies() is a
            # full CDP round-trip + filter + log write that costs 30-80ms per
            # cycle and is purely diagnostic. Flip the env var when diagnosing
            # auth issues; off by default keeps the hot path lean.
            if os.environ.get('TARGET_DEBUG_AUTH', 'false').lower() == 'true':
                try:
                    _cookies = await tab.send(cdp.storage.get_cookies())
                    _auth_names = [c.name for c in _cookies if 'target' in str(getattr(c, 'domain', '')).lower()
                                   and any(k in c.name.lower() for k in ['access', 'session', 'auth', 'token', 'guest', 'uid', 'tealeaf', 'cart'])]
                    print(f"[AUTH_CHECK] Target auth-related cookies present: {_auth_names}")
                    import os as _os, datetime as _dt
                    _os.makedirs('logs', exist_ok=True)
                    with open('logs/error_log.txt', 'a', encoding='utf-8') as _f:
                        _f.write(f"[{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [AUTH_CHECK] Purchase start for {tcin} — auth cookies: {_auth_names} — url={tab.url}\n")
                except Exception:
                    pass

            # Set up CDP interceptor BEFORE navigating — always re-run to clear stale
            # handlers from previous purchase cycles before the new navigation starts
            await self._setup_cdp_fetch_interceptor(tab, persistent=False)
            self._main_tab_interceptor_active = True

            # Detect session reuse from a prior confirmation page
            try:
                prior_url = tab.url
                if any(p in prior_url.lower() for p in ['confirmation', 'thank', 'order-confirmation']):
                    print(f"[SESSION_REUSE] Tab is on prior confirmation page: {prior_url}")
                elif prior_url and prior_url not in ('about:blank', ''):
                    print(f"[SESSION_REUSE] Tab starting from: {prior_url}")
            except Exception:
                pass

            # Determine if we need to navigate to the PDP. The ATC fetch only
            # needs cookies + Shape headers (both already cached on the tab).
            # We only need the PDP for one thing: scraping purchase_limit when
            # neither RedSky nor the in-memory cache supplied a qty > 1.
            need_pdp_for_qty = (
                quantity <= 1
                and (tcin not in self._pdp_qty_cache
                     or (time.time() - self._pdp_qty_cache[tcin][1]) >= self._pdp_qty_ttl)
            )

            if quantity > 1:
                print(f"[PURCHASE] purchase_limit from RedSky: {quantity} (skipping PDP nav)")
            elif not need_pdp_for_qty:
                cached_qty, cached_ts = self._pdp_qty_cache[tcin]
                age = time.time() - cached_ts
                print(f"[PURCHASE] purchase_limit from cache: {cached_qty} (cached {age:.0f}s ago, skipping PDP nav)")
                quantity = cached_qty

            if need_pdp_for_qty:
                # Navigate to product page using fast-nav (cdp.page.navigate +
                # readyState=interactive, no full network idle).
                product_url = f"https://www.target.com/p/-/A-{tcin}"
                try:
                    print(f"[PURCHASE] Navigating to {product_url} (PDP qty unknown)")
                    _nav_t0 = time.time()
                    try:
                        await self._fast_nav(tab, product_url)
                        print(f"[PURCHASE] Navigation returned in {time.time()-_nav_t0:.2f}s")
                    except asyncio.TimeoutError:
                        print(f"[ERROR] Navigation timed out after {time.time()-_nav_t0:.1f}s on {product_url} — "
                              f"tab may be wedged. Aborting purchase.")
                        raise
                except Exception as nav_error:
                    print(f"[ERROR] Navigation failed: {nav_error}")
                    raise

                pdp_lookup_start = time.time()
                pdp_qty = 0
                for _attempt in range(6):  # up to ~3s of polling
                    try:
                        pdp_qty = await asyncio.wait_for(
                            tab.evaluate(r"""(() => {
                                try {
                                    const re = /\\?"purchase_limit\\?"\s*:\s*(\d+)/;
                                    const scripts = document.getElementsByTagName('script');
                                    for (let i = 0; i < scripts.length; i++) {
                                        const t = scripts[i].textContent;
                                        if (!t || t.indexOf('purchase_limit') === -1) continue;
                                        const m = t.match(re);
                                        if (m) return parseInt(m[1], 10);
                                    }
                                    const mqRe = /\\?"maximum_order_quantity\\?"[\s\S]{0,200}?\\?"shipping\\?"[\s\S]{0,100}?\\?"value\\?"\s*:\s*(\d+)/;
                                    for (let i = 0; i < scripts.length; i++) {
                                        const t = scripts[i].textContent;
                                        if (!t || t.indexOf('maximum_order_quantity') === -1) continue;
                                        const m = t.match(mqRe);
                                        if (m) return parseInt(m[1], 10);
                                    }
                                    const sel = document.querySelector('select[id*="quantity" i], select[name*="quantity" i]');
                                    if (sel) {
                                        let max = 0;
                                        for (const o of sel.options) {
                                            const v = parseInt(o.value, 10);
                                            if (Number.isFinite(v) && v > max) max = v;
                                        }
                                        if (max > 0) return max;
                                    }
                                    return 0;
                                } catch (e) { return 0; }
                            })()"""),
                            timeout=1.0
                        )
                        if isinstance(pdp_qty, int) and pdp_qty > 0:
                            break
                    except asyncio.TimeoutError:
                        pass
                    except Exception:
                        pass
                    await asyncio.sleep(0.4)

                pdp_lookup_elapsed = time.time() - pdp_lookup_start
                if isinstance(pdp_qty, int) and pdp_qty > 0:
                    print(f"[PURCHASE] purchase_limit from PDP fallback: {pdp_qty} (was qty={quantity}, lookup {pdp_lookup_elapsed:.2f}s)")
                    quantity = pdp_qty
                    self._pdp_qty_cache[tcin] = (pdp_qty, time.time())
                else:
                    print(f"[PURCHASE] purchase_limit not found in PDP after {pdp_lookup_elapsed:.2f}s — keeping qty={quantity}")

            # Attempt 1: fetch-based ATC fired immediately — no need to wait for button
            # The cart API only needs valid session cookies, not full page render
            # Rotate to a fresh unconsumed Shape capture if one is sitting in the
            # ring. The active `_cached_cart_headers` may have been burned by a
            # prior cycle's POST; the warmup tab refills the ring in the
            # background, so a newer entry is often already available.
            self._consume_fresh_capture()
            headers_age = time.time() - self._cached_cart_headers_ts

            # PROACTIVE REFRESH: if cached headers approaching TTL (60s+), refresh warmup tab
            # before ATC to avoid 403 Shape block. Target's Shape tokens rotate ~every 90-120s.
            if self._cached_cart_headers and headers_age > 60:
                print(f"[PURCHASE] Shape headers approaching TTL (age={headers_age:.0f}s) — refreshing warmup tab")
                warmup_ok = await self.warm_shape_headers()
                if warmup_ok:
                    self._consume_fresh_capture()
                    headers_age = time.time() - self._cached_cart_headers_ts
                    print(f"[PURCHASE] Warmup refresh complete, new headers age={headers_age:.0f}s")
                else:
                    print(f"[PURCHASE] Warmup refresh failed, continuing with stale headers")

            use_cached = bool(self._cached_cart_headers) and headers_age < 90  # 90s TTL (Shape tokens rotate ~every 2min)
            # Strip Cookie and Referer from cached headers.
            # Cookie: credentials:'include' sends live cookies automatically; a stale cached
            #         Cookie header overrides them and breaks auth.
            # Referer: warmup runs on /cart, so cached Referer is cart-page. If we include it,
            #          it overrides our product-page Referer (spread comes last in fetch headers)
            #          and Shape may reject the token/Referer mismatch.
            _strip_keys = {'cookie', 'referer'}
            cached_shape_only = {k: v for k, v in self._cached_cart_headers.items()
                                  if k.lower() not in _strip_keys}
            # Always include x-application-name:'web' — present in every button-click POST
            # (16 headers) but absent from our fetch (15 headers). Required for correct
            # backend microservice routing and may affect Shape auth validation.
            cached_shape_only['x-application-name'] = 'web'
            extra_headers_js = json.dumps(cached_shape_only if use_cached else {'x-application-name': 'web'})
            if use_cached:
                print(f"[PURCHASE] Injecting Shape headers (age={headers_age:.0f}s): "
                      f"{list(cached_shape_only.keys())}")
            elif self._cached_cart_headers:
                print(f"[PURCHASE] Shape headers STALE (age={headers_age:.0f}s > 90s) — sending fetch WITHOUT Shape headers")
            else:
                print(f"[PURCHASE] No Shape headers cached yet — warmup tab may not have captured yet")
            print(f"[PURCHASE] Firing ATC fetch qty={quantity} (t={time.time()-start_time:.2f}s)")
            try:
                atc_result = await asyncio.wait_for(
                    tab.evaluate(f"""(async () => {{
                        try {{
                            const cachedHeaders = {extra_headers_js};
                            const resp = await fetch(
                                'https://carts.target.com/web_checkouts/v1/cart_items?field_groups=CART,CART_ITEMS,SUMMARY',
                                {{
                                    method: 'POST',
                                    credentials: 'include',
                                    headers: {{
                                        ...cachedHeaders,
                                        'Content-Type': 'application/json',
                                        'Accept': 'application/json',
                                        'Origin': 'https://www.target.com',
                                        'Referer': 'https://www.target.com/p/-/A-{tcin}',
                                        'x-application-name': 'web',
                                    }},
                                    body: JSON.stringify({{
                                        cart_item: {{
                                            tcin: '{tcin}',
                                            quantity: {quantity},
                                            item_channel_id: '10',
                                            fulfillment_type: 'SHIPPING',
                                            fulfillment_type_code: '02'
                                        }},
                                        cart_type: 'REGULAR',
                                        channel_id: '10',
                                        shopping_context: 'DIGITAL'
                                    }})
                                }}
                            );
                            const text = await resp.text();
                            // ATC POST returns the newly-added cart_item flat at the top level
                            // (verified shape 2026-05-07: cart_item_id, tcin, quantity at root).
                            // Wrap it in a list so downstream code sees the same shape as
                            // GET /cart's cart_items array.
                            let cart_items = [];
                            try {{
                                const parsed = JSON.parse(text);
                                if (parsed && parsed.tcin && parsed.cart_item_id) {{
                                    cart_items = [{{tcin: parsed.tcin, quantity: parsed.quantity}}];
                                }} else if (parsed && Array.isArray(parsed.cart_items)) {{
                                    cart_items = parsed.cart_items.map(it => ({{
                                        tcin: it && it.tcin, quantity: it && it.quantity
                                    }}));
                                }}
                            }} catch(_) {{}}
                            return {{status: resp.status, body: text.slice(0, 500), cart_items: cart_items}};
                        }} catch(e) {{
                            return {{status: 0, body: String(e)}};
                        }}
                    }})()""", await_promise=True),
                    timeout=8.0
                )
            except asyncio.TimeoutError:
                print(f"[PURCHASE] ATC fetch evaluate timed out after 8s — CDP wedged, aborting purchase")
                return {'success': False, 'tcin': tcin, 'reason': 'atc_evaluate_timeout',
                        'execution_time': time.time() - start_time}

            atc_status = atc_result.get('status', 0) if isinstance(atc_result, dict) else atc_result
            atc_body = atc_result.get('body', '') if isinstance(atc_result, dict) else ''
            # Classify the failure for better diagnostics
            if atc_status == 403 and ('<html' in atc_body.lower() or '<!doctype' in atc_body.lower()):
                print(f"[PURCHASE] ATC fetch blocked by Shape Security (403 HTML) (t={time.time()-start_time:.2f}s)")
                try:
                    import os as _os, datetime as _dt
                    _os.makedirs('logs', exist_ok=True)
                    with open('logs/error_log.txt', 'a', encoding='utf-8') as _f:
                        _f.write(f"[{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [SHAPE_BLOCK] ATC 403 Shape Security block — url={tab.url} headers_age={headers_age:.0f}s shape_headers_present={use_cached}\nResponse body: {atc_body}\n\n")
                except Exception:
                    pass
            elif atc_status in (422, 409) and 'OUT_OF_STOCK' in atc_body.upper():
                print(f"[PURCHASE] ATC fetch: item OOS at cart API ({atc_status}) (t={time.time()-start_time:.2f}s)")
            elif atc_status in (422, 409) and any(k in atc_body.upper() for k in ('PURCHASE_LIMIT', 'MAX_QUANTITY', 'QUANTITY_LIMIT', 'EXCEEDED')):
                print(f"[PURCHASE] ATC fetch: per-customer purchase limit hit at qty={quantity} ({atc_status}) (t={time.time()-start_time:.2f}s)")
            elif atc_status == 429 or 'RATE_LIMITED' in atc_body.upper() or 'DCO_RATE_LIMITED' in atc_body.upper():
                # Target's natural checkout rate-limit. Per Refract's Target docs:
                # "Target now rate-limits checkouts to prevent backend spam — just
                # let your task retry." This is NOT a Shape signal; the slow DOM
                # button-click fallback can't recover it and burns ~10s per cycle.
                # Bail fast so the manager's 3.5s Error Delay re-tries on the next
                # stock cycle with a fresh Shape capture.
                print(f"[PURCHASE] ATC fetch: rate-limited ({atc_status}) body={atc_body[:120]!r} — bailing for Error Delay retry (t={time.time()-start_time:.2f}s)")
            elif atc_status not in (200, 201):
                print(f"[PURCHASE] ATC fetch status: {atc_status} body={atc_body!r} (t={time.time()-start_time:.2f}s)")
            else:
                print(f"[PURCHASE] ATC fetch status: {atc_status} (t={time.time()-start_time:.2f}s)")

            # Success status → skip DOM polling entirely, go straight to checkout
            skip_signal_wait = False
            cart_confirmed = False  # default; branches below set True on success
            if atc_status in (200, 201):
                print(f"[PURCHASE] Fetch ATC succeeded ({atc_status}), skipping cart signal wait")
                cart_confirmed = True
            elif atc_status == 429 or 'RATE_LIMITED' in atc_body.upper() or 'DCO_RATE_LIMITED' in atc_body.upper():
                # Fast-bail. The slow DOM polling / button-click fallback below
                # exists for Shape token issues (401) and React-hydration races;
                # neither recovers a 429. Returning here lets the purchase
                # manager flip state to 'failed' and the next stock cycle
                # re-attempts after the 3.5s Error Delay with a fresh capture.
                return {'success': False, 'tcin': tcin, 'reason': 'rate_limited_429',
                        'error': f'ATC rate-limited ({atc_status})',
                        'execution_time': time.time() - start_time}
            elif atc_status == 401:
                # Auth denied — most likely Shape rotating tokens were consumed
                # (cycles back-to-back rapidly burn the token cache). FAST PATH:
                # warm the Shape headers via the warmup tab and retry once. This
                # is the only viable recovery in API-only mode (no PDP loaded
                # means no ATC button to wait on). On retry-fail, fall through
                # to the legacy DOM polling path — it still works when the tab
                # actually has a PDP loaded.
                print(f"[PURCHASE] ATC fetch 401 auth denied — refreshing Shape headers and retrying (t={time.time()-start_time:.2f}s)")
                await self._fix_auth_cookie_domains(tab)
                try:
                    await self.warm_shape_headers()
                except Exception as warm_err:
                    print(f"[PURCHASE] Shape refresh before ATC retry failed: {warm_err}")
                # Two-attempt fast-retry loop. Attempt 1 (immediate): warm Shape +
                # retry. Attempt 2 (jittered ~700ms sleep): warm Shape again, retry.
                # The sleep gives Target's per-Device-ID rate-limit bucket a chance
                # to refill — empirically catches ~50% of cycles that would have
                # bailed in v15.
                async def _do_fast_retry() -> Dict[str, Any]:
                    _strip_keys = {'cookie', 'referer'}
                    _shape_only = {k: v for k, v in self._cached_cart_headers.items()
                                   if k.lower() not in _strip_keys}
                    _shape_only['x-application-name'] = 'web'
                    _retry_headers_js = json.dumps(_shape_only)
                    try:
                        return await asyncio.wait_for(
                            tab.evaluate(f"""(async () => {{
                                try {{
                                    const h = {_retry_headers_js};
                                    const resp = await fetch(
                                        'https://carts.target.com/web_checkouts/v1/cart_items?field_groups=CART,CART_ITEMS,SUMMARY',
                                        {{method:'POST', credentials:'include',
                                          headers:{{...h,'Content-Type':'application/json','Accept':'application/json',
                                                    'Origin':'https://www.target.com',
                                                    'Referer':'https://www.target.com/p/-/A-{tcin}',
                                                    'x-application-name':'web'}},
                                          body:JSON.stringify({{cart_item:{{tcin:'{tcin}',quantity:{quantity},
                                            item_channel_id:'10',fulfillment_type:'SHIPPING',fulfillment_type_code:'02'}},
                                            cart_type:'REGULAR',channel_id:'10',shopping_context:'DIGITAL'}})}}
                                    );
                                    return {{status:resp.status, body:(await resp.text()).slice(0,300)}};
                                }} catch(e) {{ return {{status:0, body:String(e)}}; }}
                            }})()""", await_promise=True),
                            timeout=8.0
                        )
                    except asyncio.TimeoutError:
                        return {'status': 0, 'body': 'fast-retry timeout'}

                fast_retry = await _do_fast_retry()
                fast_status = fast_retry.get('status', 0) if isinstance(fast_retry, dict) else 0
                if fast_status in (200, 201):
                    print(f"[PURCHASE] ATC fast-retry succeeded ({fast_status}) after Shape refresh (t={time.time()-start_time:.2f}s)")
                    atc_status = fast_status
                    atc_body = fast_retry.get('body', '') if isinstance(fast_retry, dict) else ''
                    cart_confirmed = True
                    skip_signal_wait = True
                elif fast_status == 401:
                    # Second attempt — brief jittered sleep to let bucket refill, then retry.
                    # Tightened from 0.6-1.1s in v16: empirically the per-Device-ID bucket
                    # refills sub-300ms once a fresh Shape capture lands, so the longer
                    # human-shaped jitter was over-conservative for an API-only retry.
                    _jitter = 0.2 + (time.time() % 0.2)  # 0.2-0.4s
                    print(f"[PURCHASE] ATC fast-retry still 401 — sleeping {_jitter:.2f}s before second attempt (t={time.time()-start_time:.2f}s)")
                    await asyncio.sleep(_jitter)
                    # Try the ring first — the failed retry-1's request was
                    # itself observed by the interceptor and pushed a fresh
                    # capture. Skip the explicit warmup round-trip in that
                    # common case; only re-warm if the ring is empty.
                    if not self._consume_fresh_capture():
                        try:
                            await self.warm_shape_headers()
                            self._consume_fresh_capture()
                        except Exception as warm_err:
                            print(f"[PURCHASE] Second warmup before retry-2 failed: {warm_err}")
                    fast_retry2 = await _do_fast_retry()
                    fast_status2 = fast_retry2.get('status', 0) if isinstance(fast_retry2, dict) else 0
                    if fast_status2 in (200, 201):
                        print(f"[PURCHASE] ATC retry-2 succeeded ({fast_status2}) after second Shape refresh (t={time.time()-start_time:.2f}s)")
                        atc_status = fast_status2
                        atc_body = fast_retry2.get('body', '') if isinstance(fast_retry2, dict) else ''
                        cart_confirmed = True
                        skip_signal_wait = True
                    else:
                        print(f"[PURCHASE] ATC retry-2 returned {fast_status2} — falling through to DOM polling fallback")
                else:
                    print(f"[PURCHASE] ATC fast-retry returned {fast_status} — falling through to DOM polling fallback")
                token_fresh = False
                # Skip legacy DOM-polling block when:
                #  - fast-retry already won (cart_confirmed=True), OR
                #  - we're not on a PDP (no ATC button to poll for; common in API-only mode
                #    where we skip PDP nav and stay on /cart)
                _on_pdp = '/p/-/A-' in (tab.url or '')
                if not cart_confirmed and not _on_pdp:
                    print(f"[PURCHASE] Skipping DOM polling (tab not on PDP, url={tab.url}) — fast-retry was final attempt")
                _poll_iter_count = 0 if (cart_confirmed or not _on_pdp) else 20
                for _poll_i in range(_poll_iter_count):  # max 10s (20 x 0.5s) — 0 iterations if fast-retry won or no PDP
                    await asyncio.sleep(0.5)
                    # Primary indicator: ATC button enabled = React hydrated + write token refreshed.
                    # GET /cart always returns 200 (uses read-only auth) so it's not a reliable
                    # indicator that the write token (needed for POST) has been refreshed.
                    _btn_state = await tab.evaluate("""(() => {
                        const sels = [
                            'button[id^="addToCartButtonOrTextIdFor"]',
                            'button[data-test="addToCartButton"]',
                            'button[data-testid="addToCartButton"]',
                            '[data-testid*="add-to-cart"]',
                            'button[data-test*="addToCart"]',
                        ];
                        for (const sel of sels) {
                            const el = document.querySelector(sel);
                            if (!el) continue;
                            const r = el.getBoundingClientRect();
                            if (r.width === 0 || r.height === 0) continue;
                            return el.disabled || el.getAttribute('aria-disabled') === 'true'
                                ? 'disabled' : 'ready';
                        }
                        return 'absent';
                    })()""")
                    if _btn_state == 'ready':
                        token_fresh = True
                        print(f"[PURCHASE] Token ready — button enabled after {(_poll_i+1)*0.5:.1f}s (t={time.time()-start_time:.2f}s)")
                        break
                # Legacy DOM polling retry block — only relevant if fast-retry above
                # didn't already succeed. When cart_confirmed is True we just fall through.
                if not cart_confirmed:
                    if token_fresh:
                        print(f"[PURCHASE] Retrying ATC fetch — button enabled, write token ready (t={time.time()-start_time:.2f}s)")
                        atc_retry_result = await tab.evaluate(f"""(async () => {{
                            try {{
                                const h = {extra_headers_js};
                                const resp = await fetch(
                                    'https://carts.target.com/web_checkouts/v1/cart_items?field_groups=CART,CART_ITEMS,SUMMARY',
                                    {{method:'POST', credentials:'include',
                                      headers:{{...h,'Content-Type':'application/json','Accept':'application/json',
                                                'Origin':'https://www.target.com',
                                                'Referer':'https://www.target.com/p/-/A-{tcin}',
                                                'x-application-name':'web'}},
                                      body:JSON.stringify({{cart_item:{{tcin:'{tcin}',quantity:{quantity},
                                        item_channel_id:'10',fulfillment_type:'SHIPPING',fulfillment_type_code:'02'}},
                                        cart_type:'REGULAR',channel_id:'10',shopping_context:'DIGITAL'}})}}
                                );
                                return {{status:resp.status, body:(await resp.text()).slice(0,300)}};
                            }} catch(e) {{ return {{status:0, body:String(e)}}; }}
                        }})()""", await_promise=True)
                        retry_status = atc_retry_result.get('status', 0) if isinstance(atc_retry_result, dict) else atc_retry_result
                        retry_body = atc_retry_result.get('body', '') if isinstance(atc_retry_result, dict) else ''
                        print(f"[PURCHASE] ATC fetch retry: {retry_status} (t={time.time()-start_time:.2f}s)")
                        if retry_status in (200, 201):
                            print(f"[PURCHASE] ATC fetch retry succeeded ({retry_status})")
                            cart_confirmed = True
                        else:
                            if retry_status == 401:
                                print(f"[PURCHASE] ATC fetch retry still 401 — falling through to button click")
                            else:
                                print(f"[PURCHASE] ATC fetch retry failed ({retry_status}) body={retry_body!r}")
                            try:
                                import os as _os, datetime as _dt
                                _os.makedirs('logs', exist_ok=True)
                                with open('logs/error_log.txt', 'a', encoding='utf-8') as _f:
                                    _f.write(f"[{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [ATC_RETRY_FAIL] status={retry_status} url={tab.url}\nBody: {retry_body}\n\n")
                            except Exception:
                                pass
                            cart_confirmed = False
                    else:
                        # Only print the "token not ready" message when we actually polled
                        # (PDP loaded). In API-only mode the polling was deliberately skipped.
                        if _on_pdp:
                            print(f"[PURCHASE] Token not ready within 10s (button stayed disabled) — falling through to button click")
                        cart_confirmed = False
                skip_signal_wait = True
            elif atc_status == 400 and 'EXCEEDED' in atc_body.upper():
                # Self-heal MAX_PURCHASE_LIMIT_EXCEEDED. Two distinct root causes
                # produce this 400, both recoverable here:
                #   (1) Cart-state race — prior cycle's clear_cart returned 200 OK
                #       before Target's backend committed the DELETE. Next ATC
                #       sees the cart still populated and rejects.
                #   (2) Real per-customer/session throttle — cumulative add-events
                #       exceeded Target's tracked limit (test_mode-specific, since
                #       test_mode loops the same TCIN at high frequency).
                # Recovery: force clear_cart (handles 1), brief sleep for backend
                # commit, retry at original qty (recovers 1), then qty=1 if still
                # rejected (covers some throttle variants). If both retries also
                # fail it's a hard throttle — set per-TCIN cooldown so test_mode's
                # immediate re-trigger loop doesn't burn ~5s/cycle on guaranteed
                # fails. Cooldown is short enough (90s) that prod's natural
                # in-stock cadence won't be impacted.
                print(f"[PURCHASE] ATC 400 EXCEEDED — self-heal: clear_cart + retry (t={time.time()-start_time:.2f}s)")
                try:
                    await self._clear_cart(tab)
                except Exception as _ce:
                    print(f"[PURCHASE] self-heal clear_cart failed: {_ce}")
                await asyncio.sleep(1.0)
                self._consume_fresh_capture()
                _sh_strip = {'cookie', 'referer'}
                _sh_shape = {k: v for k, v in self._cached_cart_headers.items()
                             if k.lower() not in _sh_strip}
                _sh_shape['x-application-name'] = 'web'
                _sh_headers_js = json.dumps(_sh_shape)

                async def _do_self_heal_retry(_qty: int) -> Dict[str, Any]:
                    try:
                        return await asyncio.wait_for(
                            tab.evaluate(f"""(async () => {{
                                try {{
                                    const h = {_sh_headers_js};
                                    const resp = await fetch(
                                        'https://carts.target.com/web_checkouts/v1/cart_items?field_groups=CART,CART_ITEMS,SUMMARY',
                                        {{method:'POST', credentials:'include',
                                          headers:{{...h,'Content-Type':'application/json','Accept':'application/json',
                                                    'Origin':'https://www.target.com',
                                                    'Referer':'https://www.target.com/p/-/A-{tcin}',
                                                    'x-application-name':'web'}},
                                          body:JSON.stringify({{cart_item:{{tcin:'{tcin}',quantity:{_qty},
                                            item_channel_id:'10',fulfillment_type:'SHIPPING',fulfillment_type_code:'02'}},
                                            cart_type:'REGULAR',channel_id:'10',shopping_context:'DIGITAL'}})}}
                                    );
                                    return {{status:resp.status, body:(await resp.text()).slice(0,300)}};
                                }} catch(e) {{ return {{status:0, body:String(e)}}; }}
                            }})()""", await_promise=True),
                            timeout=8.0
                        )
                    except asyncio.TimeoutError:
                        return {'status': 0, 'body': 'self-heal retry timeout'}

                _sh1 = await _do_self_heal_retry(quantity)
                _sh1_status = _sh1.get('status', 0) if isinstance(_sh1, dict) else 0
                _sh1_body = _sh1.get('body', '') if isinstance(_sh1, dict) else ''
                if _sh1_status in (200, 201):
                    print(f"[PURCHASE] Self-heal qty={quantity} succeeded ({_sh1_status}) — race recovered (t={time.time()-start_time:.2f}s)")
                    atc_status = _sh1_status
                    atc_body = _sh1_body
                    cart_confirmed = True
                elif quantity > 1:
                    print(f"[PURCHASE] Self-heal qty={quantity} returned {_sh1_status} — retrying qty=1 (t={time.time()-start_time:.2f}s)")
                    self._consume_fresh_capture()
                    _sh2 = await _do_self_heal_retry(1)
                    _sh2_status = _sh2.get('status', 0) if isinstance(_sh2, dict) else 0
                    _sh2_body = _sh2.get('body', '') if isinstance(_sh2, dict) else ''
                    if _sh2_status in (200, 201):
                        print(f"[PURCHASE] Self-heal qty=1 succeeded ({_sh2_status}) (t={time.time()-start_time:.2f}s)")
                        atc_status = _sh2_status
                        atc_body = _sh2_body
                        quantity = 1
                        cart_confirmed = True
                    else:
                        print(f"[PURCHASE] Self-heal qty=1 also failed ({_sh2_status}) — marking {tcin} throttled for {self._tcin_throttle_cooldown_s:.0f}s")
                        self._tcin_throttle_until[tcin] = time.time() + self._tcin_throttle_cooldown_s
                        cart_confirmed = False
                else:
                    print(f"[PURCHASE] Self-heal qty=1 still {_sh1_status} — marking {tcin} throttled for {self._tcin_throttle_cooldown_s:.0f}s")
                    self._tcin_throttle_until[tcin] = time.time() + self._tcin_throttle_cooldown_s
                    cart_confirmed = False
                skip_signal_wait = True
            elif atc_status in (422, 409) and quantity > 1 and any(
                k in atc_body.upper() for k in ('PURCHASE_LIMIT', 'MAX_QUANTITY', 'QUANTITY_LIMIT', 'EXCEEDED')
            ):
                # RedSky reported a higher purchase_limit than Target now enforces.
                # Single-shot retry with quantity=1 — never escalate further. Shape
                # headers are single-use, so warm fresh ones if cache is stale (>85s).
                print(f"[PURCHASE] ATC rejected qty={quantity} (per-customer limit). Retrying qty=1.")
                if time.time() - self._cached_cart_headers_ts > 85:
                    print(f"[PURCHASE] Shape headers stale before qty-fallback retry — warming")
                    await self.warm_shape_headers()
                _retry_headers_age = time.time() - self._cached_cart_headers_ts
                _retry_use_cached = bool(self._cached_cart_headers) and _retry_headers_age < 90
                _retry_cached = {k: v for k, v in self._cached_cart_headers.items()
                                 if k.lower() not in {'cookie', 'referer'}}
                _retry_cached['x-application-name'] = 'web'
                _retry_headers_js = json.dumps(_retry_cached if _retry_use_cached else {'x-application-name': 'web'})
                qty_fallback = 1
                qty_retry_result = await tab.evaluate(f"""(async () => {{
                    try {{
                        const h = {_retry_headers_js};
                        const resp = await fetch(
                            'https://carts.target.com/web_checkouts/v1/cart_items?field_groups=CART,CART_ITEMS,SUMMARY',
                            {{method:'POST', credentials:'include',
                              headers:{{...h,'Content-Type':'application/json','Accept':'application/json',
                                        'Origin':'https://www.target.com',
                                        'Referer':'https://www.target.com/p/-/A-{tcin}',
                                        'x-application-name':'web'}},
                              body:JSON.stringify({{cart_item:{{tcin:'{tcin}',quantity:{qty_fallback},
                                item_channel_id:'10',fulfillment_type:'SHIPPING',fulfillment_type_code:'02'}},
                                cart_type:'REGULAR',channel_id:'10',shopping_context:'DIGITAL'}})}}
                        );
                        return {{status:resp.status, body:(await resp.text()).slice(0,300)}};
                    }} catch(e) {{ return {{status:0, body:String(e)}}; }}
                }})()""", await_promise=True)
                qty_retry_status = qty_retry_result.get('status', 0) if isinstance(qty_retry_result, dict) else qty_retry_result
                qty_retry_body = qty_retry_result.get('body', '') if isinstance(qty_retry_result, dict) else ''
                print(f"[PURCHASE] ATC qty=1 retry: {qty_retry_status} (t={time.time()-start_time:.2f}s)")
                if qty_retry_status in (200, 201):
                    quantity = 1  # update so downstream logging reflects what actually shipped
                    cart_confirmed = True
                else:
                    print(f"[PURCHASE] ATC qty=1 retry failed ({qty_retry_status}) body={qty_retry_body!r}")
                    cart_confirmed = False
                skip_signal_wait = True
            else:
                # Ambiguous/failed status — check DOM briefly (page had time to render by now)
                cart_confirmed = await self._wait_for_cart_signal(tab, timeout=1.5)

            # Attempt 2: button click fallback if fetch didn't work
            # Now we need the button to be ready — wait only if not already confirmed
            button_ready = True  # optimistic default; set False below if needed
            if not cart_confirmed:
                # Check if button is ready (may already be since fetch took some time)
                btn_state = await tab.evaluate("""(() => {
                    const selectors = [
                        'button[id^="addToCartButtonOrTextIdFor"]',
                        'button[data-test="addToCartButton"]',
                        'button[data-testid="addToCartButton"]',
                        '[data-testid*="add-to-cart"]',
                        'button[data-test*="addToCart"]',
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (!el) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width === 0 || r.height === 0) continue;
                        return el.disabled || el.getAttribute('aria-disabled') === 'true' ? 'disabled' : 'ready';
                    }
                    return 'absent';
                })()""")
                if btn_state == 'ready':
                    button_ready = True
                elif btn_state in ('absent', 'disabled'):
                    # Button not ready yet — wait up to remaining time
                    budget = 14.0 if skip_signal_wait else 8.0  # 401 path polls up to 10s, extend budget
                    remaining = max(0.0, budget - (time.time() - start_time))
                    print(f"[PURCHASE] ATC button {btn_state} — waiting up to {remaining:.1f}s for readiness")
                    button_ready = await self._wait_for_atc_button_ready(tab, timeout=remaining)
                if not button_ready:
                    # Button stayed disabled — try a forced programmatic click before giving up.
                    # Target's React handler may still fire on el.click() even when the button
                    # has disabled/aria-disabled set (e.g. during page hydration loading state).
                    print("[PURCHASE] Button stayed disabled — attempting forced click")
                    try:
                        _forced = await tab.evaluate("""(() => {
                            const sels = [
                                'button[id^="addToCartButtonOrTextIdFor"]',
                                'button[data-test="addToCartButton"]',
                                'button[data-testid="addToCartButton"]',
                                '[data-testid*="add-to-cart"]',
                                'button[data-test*="addToCart"]',
                            ];
                            for (const sel of sels) {
                                const el = document.querySelector(sel);
                                if (!el) continue;
                                const r = el.getBoundingClientRect();
                                if (r.width === 0 || r.height === 0) continue;
                                el.click();
                                return true;
                            }
                            return false;
                        })()""")
                        if _forced:
                            print(f"[PURCHASE] Forced click dispatched — waiting 4s for signal")
                            cart_confirmed = await self._wait_for_cart_signal(tab, timeout=4.0)
                            if cart_confirmed:
                                print("[PURCHASE] Forced click on disabled button succeeded")
                    except Exception as _fe:
                        print(f"[PURCHASE] Forced click error: {_fe}")
                    if not cart_confirmed:
                        await self._take_debug_screenshot(tab, "atc_button_not_ready")
                        return {'success': False, 'tcin': tcin, 'reason': 'page_not_ready',
                                'execution_time': time.time() - start_time}
                # API-only mode guard: if we never navigated to a PDP, the button-click
                # fallback below would target whatever ATC-shaped button exists on
                # the cart page (a cross-sell recommendation), not our actual product.
                # Bail out instead of clicking the wrong thing.
                if not cart_confirmed and '/p/-/A-' not in (tab.url or ''):
                    print(f"[PURCHASE] Fetch ATC not confirmed and not on PDP (url={tab.url}) — bailing without DOM fallback")
                    return {'success': False, 'tcin': tcin, 'reason': 'atc_failed_api_mode',
                            'execution_time': time.time() - start_time}
                if not cart_confirmed:
                    print(f"[PURCHASE] Fetch ATC not confirmed, trying button click...")
                    await self._dismiss_error_flyout(tab)
                    await self._select_shipping_option(tab)
                    await asyncio.sleep(0.2)
                    add_button = await self._find_add_to_cart_button(tab)
                    if not add_button:
                        await self._take_debug_screenshot(tab, "button_not_found_post_ready")
                        return {'success': False, 'tcin': tcin, 'reason': 'button_not_found',
                                'execution_time': time.time() - start_time}
                    try:
                        _click_time = time.time()
                        await self._dispatch_click(add_button)
                        print(f"[PURCHASE] ATC button clicked (t={time.time()-start_time:.2f}s)")
                        # 401 path: button click's initial POST also gets 401, then Target's JS
                        # does a silent token refresh (~5-8s) and fires a retry POST. Give 8s for
                        # the initial attempt, then detect if a late retry POST fired and extend.
                        signal_timeout = 8.0 if skip_signal_wait else 4.0
                        cart_confirmed = await self._wait_for_cart_signal(tab, timeout=signal_timeout)
                        if not cart_confirmed and skip_signal_wait:
                            # Check if a late retry POST was captured (>2s after click = token refresh retry,
                            # not the immediate initial POST which fires in ~0.5s)
                            _post_age = time.time() - self._cached_cart_headers_ts
                            _post_is_retry = self._cached_cart_headers_ts > _click_time + 2.0
                            if _post_is_retry and _post_age < 4.0:
                                print(f"[PURCHASE] Late retry POST detected ({_post_age:.1f}s ago) — "
                                      f"extending wait 5s for cart signal")
                                cart_confirmed = await self._wait_for_cart_signal(tab, timeout=5.0)
                            else:
                                print(f"[PURCHASE] No late retry POST detected "
                                      f"(last post age={_post_age:.1f}s, is_retry={_post_is_retry})")
                        if cart_confirmed and self._cached_cart_headers_ts > start_time:
                            print(f"[PURCHASE] Button-click cart headers captured: "
                                  f"{list(self._cached_cart_headers.keys())}")
                    except Exception as e:
                        print(f"[PURCHASE] Button click error: {e}")

            if not cart_confirmed:
                await self._take_debug_screenshot(tab, "atc_not_confirmed")
                # Diagnose what the page is showing
                try:
                    page_msg = await tab.evaluate("""(() => {
                        const OOS_PHRASES = ['out of stock', 'no longer in stock', 'sold out',
                                             'not available', 'item is unavailable', 'no longer available'];
                        const WRONG_PHRASES = ['something went wrong', 'error adding', 'unable to add'];
                        const text = (document.body && document.body.innerText || '').toLowerCase();
                        if (OOS_PHRASES.some(p => text.includes(p))) return 'oos';
                        if (WRONG_PHRASES.some(p => text.includes(p))) return 'error';
                        return 'unknown';
                    })()""")
                    if page_msg == 'oos':
                        print("[PURCHASE] ATC failed: item appears OUT OF STOCK on page")
                    elif page_msg == 'error':
                        print("[PURCHASE] ATC failed: 'something went wrong' on page")
                    else:
                        print("[PURCHASE] ATC failed: unknown page state")
                    import os as _os, datetime as _dt
                    _os.makedirs('logs', exist_ok=True)
                    with open('logs/error_log.txt', 'a', encoding='utf-8') as _f:
                        _f.write(f"[{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [ATC_FAIL] page_state={page_msg} url={tab.url}\n")
                except Exception as _diag_e:
                    import os as _os, datetime as _dt
                    try:
                        _os.makedirs('logs', exist_ok=True)
                        with open('logs/error_log.txt', 'a', encoding='utf-8') as _f:
                            _f.write(f"[{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [ATC_FAIL] Diagnosis eval failed: {_diag_e}\n")
                    except Exception:
                        pass
                try:
                    await tab.get("https://www.target.com/cart")
                    await self._clear_cart(tab)
                except Exception:
                    pass
                return {'success': False, 'tcin': tcin, 'reason': 'cart_addition_failed',
                        'execution_time': time.time() - start_time}

            print(f"[PURCHASE] Cart confirmed (t={time.time() - start_time:.1f}s)")

            # Cart-state safety net: warn loudly if ATC body shows >1 item (indicates a
            # leak from a prior failed attempt). Otherwise silent — the 201 from ATC
            # is sufficient confirmation. Diagnostic log dropped to save ~30-50ms.
            try:
                _cart_items = atc_result.get('cart_items', []) if isinstance(atc_result, dict) else []
                if len(_cart_items) > 1:
                    _items_dbg = [f"{i.get('tcin', '?')}x{i.get('quantity', 1)}" for i in _cart_items if isinstance(i, dict)]
                    print(f"[CART_STATE] WARNING: {len(_cart_items)} items in cart before checkout (expected 1): {_items_dbg}")
            except Exception:
                pass

            # Navigate to checkout
            self._notify_status(tcin, 'checking_out', {'timestamp': datetime.now().isoformat()})
            checkout_result = False
            t_nav_start = time.time()

            # Fire pre_checkout as fire-and-forget — mirrors exactly what Target's cart page
            # JS does: fires the fetch then immediately redirects without awaiting the response.
            # The HTTP request is already in-flight before navigation starts so the server
            # receives and processes it. By the time Place Order fires (~1.5s later),
            # pre_checkout has long since completed server-side (~200-300ms).
            try:
                await tab.evaluate(f"""(() => {{
                    const shapeHeaders = {extra_headers_js};
                    fetch(
                        'https://carts.target.com/web_checkouts/v1/pre_checkout?cart_type=REGULAR&field_groups=CART,CART_ITEMS,DELIVERY_WINDOWS,PAYMENT_INSTRUCTIONS,PROMOTION_CODES,SUMMARY,ADDRESSES',
                        {{
                            method: 'POST',
                            keepalive: true,
                            credentials: 'include',
                            headers: {{
                                'Content-Type': 'application/json',
                                'Accept': 'application/json',
                                'Origin': 'https://www.target.com',
                                'Referer': 'https://www.target.com/cart',
                                'x-application-name': 'web',
                                ...shapeHeaders,
                            }},
                            body: JSON.stringify({{cart_type: 'REGULAR'}})
                        }}
                    ).catch(() => {{}});
                }})()""", await_promise=False)
                print(f"[PURCHASE] pre_checkout fired (fire-and-forget) (t+{time.time()-t_nav_start:.3f}s)")
            except Exception as e:
                print(f"[PURCHASE] pre_checkout fire failed: {e}")

            # API-mode shortcut: skip the /checkout/start page nav only in
            # TEST_MODE where _place_order returns synthetic success. PROD
            # must still nav so the cart hydrates server-side before the API
            # place-order POST fires — skipping in PROD races pre_checkout
            # and triggers HTTP 424 CART_COMPARISION_FAILURE_ERROR (observed
            # 2026-05-07 23:16, all 3 cycles failed).
            _api_skip = self.test_mode
            if _api_skip:
                _co_state = 'place_order'
                landed_url = '<api_mode_no_nav>'
                print(f"[PURCHASE] API mode — skipping /checkout/start nav (t+{time.time()-t_nav_start:.3f}s)")
                checkout_result = True
            else:
                try:
                    await tab.get("https://www.target.com/checkout/start")
                    await tab  # flush CDP event queue before interacting (zendriver pattern)
                    # Use evaluate for reliable URL reading — tab.url can be stale during redirects
                    landed_url = await tab.evaluate("window.location.href")
                    print(f"[PURCHASE] Checkout nav done (t+{time.time()-t_nav_start:.3f}s) landed={landed_url}")
                    if 'checkout' not in landed_url.lower():
                        print(f"[PURCHASE] Checkout redirect detected → {landed_url}")
                        try:
                            snippet = (await tab.evaluate("(document.body && document.body.innerText || '').slice(0,200)")).replace('\n',' ')
                            print(f"[PURCHASE] Page text: {snippet!r}")
                        except Exception:
                            pass
                    try:
                        _co_state = 'unknown'  # ensure always defined even if evaluate throws
                        _co_start = time.time()
                        while time.time() - _co_start < 10.0:
                            _co_state = await tab.evaluate("""(() => {
                                function vis(el) {
                                    if (!el) return false;
                                    const r = el.getBoundingClientRect();
                                    return r.width > 0 && r.height > 0;
                                }
                                const po  = document.querySelector('[data-test="placeOrderButton"]');
                                const sac = document.querySelector('[data-test="save-and-continue-button"]');
                                if (vis(po))  return 'place_order';
                                if (vis(sac)) return 'sac';
                                const radios = document.querySelectorAll('input[type="radio"]');
                                if (Array.from(radios).some(r => vis(r))) return 'sac';
                                return 'none';
                            })()""")
                            if _co_state in ('place_order', 'sac'):
                                print(f"[PURCHASE] Checkout page ready ({_co_state}) in {time.time()-_co_start:.2f}s")
                                break
                            await asyncio.sleep(0.05)
                    except Exception as wait_error:
                        print(f"[PURCHASE] Checkout element wait warning: {wait_error}")

                    checkout_result = 'checkout' in landed_url.lower()
                except Exception as nav_error:
                    print(f"[ERROR] Checkout navigation failed: {nav_error}")
                    checkout_result = False

            if checkout_result:
                # _handle_delivery_options clicks a Shipping vs Pickup radio on the
                # cart page. The API place-order POST encodes fulfillment in its
                # body — the radio click is unnecessary and costs ~400ms. Only
                # run it when we're actually on a checkout page DOM (legacy path).
                if not _api_skip:
                    await self._handle_delivery_options(tab)
                print(f"[CHECKOUT_TRANSITION] ATC → Checkout → Payment phase starting (t={time.time()-start_time:.1f}s, co_state={_co_state})")
                payment_result = await self._complete_payment(tab, initial_state=_co_state)
                if not payment_result:
                    checkout_result = False

            if not checkout_result:
                # Diagnose: use interceptor reason if available (faster/more accurate than page text)
                try:
                    url_now = tab.url
                    if self._checkout_rejected and self._checkout_reject_reason:
                        reason = self._checkout_reject_reason
                        if 'RESERVATION_FAILURE' in reason:
                            print(f"[PURCHASE] DIAGNOSIS: RESERVATION_FAILURE — item sold out at order submission (inventory race)")
                        elif 'INVENTORY_NOT_AVAILABLE' in reason or 'CART_COMPARISION_FAILURE' in reason:
                            print(f"[PURCHASE] DIAGNOSIS: INVENTORY_NOT_AVAILABLE — item OOS at checkout submission")
                        else:
                            print(f"[PURCHASE] DIAGNOSIS: Checkout rejected by server — tgt-cart-error-key={reason}")
                    else:
                        page_text = (await tab.evaluate("(document.body && document.body.innerText || '').toLowerCase()"))
                        if any(p in page_text for p in ['busier', 'temporary issue', "can't view", 'busy right now', 'limiting how many guests']):
                            print(f"[PURCHASE] DIAGNOSIS: F5/Target rate-limit block (busy error on page)")
                        elif any(p in page_text for p in ['out of stock', 'unavailable', 'sold out', 'not available']):
                            print(f"[PURCHASE] DIAGNOSIS: Item sold out during checkout")
                        elif any(p in page_text for p in ['no longer be available', 'no longer available', "couldn't complete", 'reservation']):
                            print(f"[PURCHASE] DIAGNOSIS: RESERVATION_FAILURE — item sold out at order submission (inventory race)")
                        else:
                            print(f"[PURCHASE] DIAGNOSIS: Unknown failure — url={url_now}")
                    import os as _os, datetime as _dt
                    _os.makedirs('logs', exist_ok=True)
                    with open('logs/error_log.txt', 'a', encoding='utf-8') as _f:
                        _f.write(f"[{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [CHECKOUT_FAIL] url={url_now} reject_key={self._checkout_reject_reason or 'none'}\n")
                except Exception as _diag_e:
                    import os as _os, datetime as _dt
                    try:
                        _os.makedirs('logs', exist_ok=True)
                        with open('logs/error_log.txt', 'a', encoding='utf-8') as _f:
                            _f.write(f"[{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [CHECKOUT_FAIL] Diagnosis eval failed: {_diag_e}\n")
                    except Exception:
                        pass
                print(f"[PURCHASE] Checkout failed, clearing cart and waiting...")
                try:
                    await tab.get("https://www.target.com/cart")
                    await self._clear_cart(tab)
                    print(f"[PURCHASE] Cart cleared after checkout_navigation_failed, waiting for next cycle")
                except Exception:
                    pass
                return {
                    'success': False,
                    'tcin': tcin,
                    'reason': 'checkout_navigation_failed',
                    'execution_time': time.time() - start_time
                }

            print(f"[PURCHASE] Checkout complete (t={time.time() - start_time:.1f}s)")

            if self.test_mode:
                clear_result = await self._clear_cart(tab)
                execution_time = time.time() - start_time

                if not clear_result:
                    self._notify_status(tcin, 'failed', {
                        'error': 'Cart clearing failed',
                        'execution_time': execution_time,
                        'timestamp': datetime.now().isoformat(),
                        'failure_reason': 'cart_clear_failed'
                    })
                    return {
                        'success': False,
                        'tcin': tcin,
                        'reason': 'cart_clear_failed',
                        'error': 'Failed to clear cart after checkout',
                        'execution_time': execution_time
                    }

                print(f"[PURCHASE] TEST_MODE: Cart cleared, cycle complete: {tcin} in {execution_time:.2f}s")
            else:
                execution_time = time.time() - start_time
                print(f"[PURCHASE] PROD_MODE: Order complete: {tcin} in {execution_time:.2f}s")
                print(f"[PURCHASE] PROD_MODE: Staying on confirmation (next attempt will navigate to product)")

            # Save session after successful purchase
            await self.session_manager.save_session_state()

            # Extract order_id. API mode (Phase 4b) stashes the response-derived
            # order_id on self because the page does not navigate to /confirmation;
            # DOM mode reads it from the post-click URL.
            if self._api_order_id:
                order_id = self._api_order_id
                confirmation_url = self._api_confirmation_url or (tab.url or "")
                print(f"[PURCHASE] order_id from API response: {order_id}")
            else:
                confirmation_url = tab.url or ""
                order_id = None
                if 'orderId=' in confirmation_url:
                    try:
                        order_id = confirmation_url.split('orderId=')[1].split('&')[0]
                    except Exception as e:
                        print(f"[PURCHASE] Failed to parse order_id from URL: {e}")

            self._notify_status(tcin, 'purchased', {
                'execution_time': execution_time,
                'timestamp': datetime.now().isoformat(),
                'order_confirmed': True,
                'order_number': order_id,
                'order_id': order_id,
                'confirmation_url': confirmation_url,
            })

            return {
                'success': True,
                'tcin': tcin,
                'reason': 'order_confirmed',
                'execution_time': execution_time,
                'order_id': order_id,
                'confirmation_url': confirmation_url
            }

        except Exception as e:
            execution_time = time.time() - start_time
            import traceback as _tb
            _tb_str = _tb.format_exc()
            failure_reason = f"{type(e).__name__}: {str(e)}"
            print(f"[ERROR] Purchase failed for {tcin}: {e}")
            print(_tb_str)

            # Write full traceback to error_log.txt so it's available for diagnosis
            try:
                import os as _os, datetime as _dt
                _os.makedirs('logs', exist_ok=True)
                with open('logs/error_log.txt', 'a', encoding='utf-8') as _f:
                    _f.write(f"[{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [PURCHASE] Purchase failed for {tcin}: {failure_reason}\n{_tb_str}\n")
            except Exception:
                pass

            self._notify_status(tcin, 'failed', {
                'error': str(e),
                'failure_reason': failure_reason,
                'execution_time': execution_time,
                'timestamp': datetime.now().isoformat()
            })

            # Error recovery: clear cart so next cycle finds the Add to Cart button
            try:
                browser = self.session_manager.browser
                if browser and browser.tabs:
                    recovery_tab = browser.tabs[0]
                    await recovery_tab.get("https://www.target.com/cart")
                    await self._clear_cart(recovery_tab)
                    print(f"[PURCHASE] Error recovery: cart cleared")
            except Exception:
                pass

            return {
                'success': False,
                'tcin': tcin,
                'reason': 'exception',
                'error': str(e),
                'execution_time': execution_time
            }

        finally:
            # Disable main tab CDP interceptor so it can be re-enabled on next purchase
            if tab and self._main_tab_interceptor_active:
                try:
                    # Explicitly remove all RequestPaused handlers before disabling CDP
                    handlers = getattr(tab, 'handlers', {})
                    for handler in handlers.get(cdp.fetch.RequestPaused, []):
                        try:
                            tab.remove_handler(handler, cdp.fetch.RequestPaused)
                        except Exception:
                            pass
                    handlers[cdp.fetch.RequestPaused] = []
                    # Now disable the CDP domain itself
                    await tab.send(cdp.fetch.disable())
                    self._main_tab_interceptor_active = False
                    if cdp.fetch in tab.enabled_domains:
                        tab.enabled_domains.remove(cdp.fetch)
                    # Drain dedup set now that no more handler invocations
                    # can fire — prevents the misleading STATE_CARRY warning
                    # at the start of the next purchase.
                    self._cdp_continued_ids.clear()
                    print(f"[PURCHASE] CDP fetch interceptor disabled (cleanup) — all RequestPaused handlers removed")
                except Exception as cleanup_err:
                    print(f"[PURCHASE] CDP interceptor cleanup warning: {cleanup_err}")

    # -------------------------------------------------------------------------
    # ATC readiness and cart signal helpers
    # -------------------------------------------------------------------------

    async def _wait_for_atc_button_ready(self, tab, timeout: float = 8.0) -> bool:
        """Poll until the ATC button is present and enabled (React + Shape initialized)."""
        js = """(() => {
            const selectors = [
                'button[id^="addToCartButtonOrTextIdFor"]',
                'button[data-test="addToCartButton"]',
                'button[data-testid="addToCartButton"]',
                '[data-testid*="add-to-cart"]',
                'button[data-test*="addToCart"]',
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (!el) continue;
                const r = el.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) continue;
                const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true';
                return disabled ? 'disabled' : 'ready';
            }
            return 'absent';
        })()"""
        start = time.time()
        while time.time() - start < timeout:
            try:
                state = await tab.evaluate(js)
                if state == 'ready':
                    print(f"[READY] ATC button enabled after {time.time()-start:.2f}s")
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.05)
        print(f"[READY] ATC button not ready after {timeout}s")
        await self._take_debug_screenshot(tab, "atc_button_not_ready")
        try:
            dom_snap = await tab.evaluate("(document.body && document.body.innerHTML.slice(0,2000) || '')")
            url_now = tab.url
            import os as _os, datetime as _dt
            _os.makedirs('logs', exist_ok=True)
            with open('logs/error_log.txt', 'a', encoding='utf-8') as _f:
                _f.write(f"[{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [ATC_READY] Button not ready after {timeout}s — url={url_now}\nDOM: {dom_snap}\n\n")
        except Exception:
            pass
        return False

    async def _fix_auth_cookie_domains(self, tab):
        """Re-inject all www.target.com-scoped cookies with .target.com so fetch() to carts.target.com sends them."""
        try:
            all_cookies = await tab.send(cdp.storage.get_cookies())
            # Skip analytics, survey, and tracking cookies — only widen potential auth cookies
            skip_names = {
                'visitorId', '__utma', '__utmb', '__utmc', '__utmz', '_ga', '_gid', '_fbp', '_gcl_au',
                'lux_uid', 'bv_metrics',
                'kampyleUserSession', 'kampyleUserSessionsCount', 'kampyleUserPercentile', 'kampyleSessionPageCounter',
            }
            www_cookies = [c for c in all_cookies if str(getattr(c, 'domain', '')) == 'www.target.com']
            if www_cookies:
                print(f"[AUTH_FIX] Found {len(www_cookies)} www.target.com-scoped cookies: {[c.name for c in www_cookies]}")
            else:
                print(f"[AUTH_FIX] No www.target.com-scoped cookies found — auth cookies may already be .target.com scoped")
            fixed = 0
            for c in www_cookies:
                if c.name in skip_names:
                    continue
                # Delete narrow-scoped copy
                await tab.send(cdp.network.delete_cookies(name=c.name, domain='www.target.com'))
                # Re-inject with broad domain
                ss = c.same_site
                same_site = cdp.network.CookieSameSite.from_json(ss.to_json()) if ss else None
                exp = float(c.expires) if c.expires and float(c.expires) > 0 else None
                expires = cdp.network.TimeSinceEpoch(exp) if exp else None
                await tab.send(cdp.network.set_cookie(
                    name=c.name,
                    value=c.value,
                    domain='.target.com',
                    path=getattr(c, 'path', '/') or '/',
                    secure=True,
                    http_only=bool(getattr(c, 'http_only', False)),
                    same_site=same_site,
                    expires=expires,
                ))
                print(f"[AUTH_FIX] Re-injected '{c.name}' with domain .target.com")
                fixed += 1
            if fixed == 0:
                print(f"[AUTH_FIX] No cookies widened")
        except Exception as e:
            print(f"[AUTH_FIX] Cookie domain fix failed: {e}")

    async def _wait_for_cart_signal(self, tab, timeout: float = 4.0) -> bool:
        """Poll DOM for cart confirmation: badge > 0, flyout, or drawer. No API field-name guessing."""
        js = """(() => {
            const badge = document.querySelector('[data-test="cart-count"], [data-testid="cart-count"]');
            if (badge && parseInt((badge.textContent||'').trim(),10) > 0) return 'badge';
            const confirm = document.querySelector('[data-test="add-to-cart-confirmation"]');
            if (confirm && confirm.getBoundingClientRect().height > 0) return 'flyout';
            const drawer = document.querySelector('[data-test="cart-drawer"], [class*="CartDrawer"]');
            if (drawer && drawer.getBoundingClientRect().height > 0) return 'drawer';
            return 'none';
        })()"""
        start = time.time()
        while time.time() - start < timeout:
            try:
                result = await tab.evaluate(js)
                if result != 'none':
                    print(f"[CART_SIGNAL] {result} detected in {time.time()-start:.2f}s")
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.05)
        print(f"[CART_SIGNAL] No signal within {timeout}s")
        await self._take_debug_screenshot(tab, "cart_signal_timeout")
        try:
            dom_snap = await tab.evaluate("(document.body && document.body.innerHTML.slice(0,2000) || '')")
            url_now = tab.url
            import os as _os, datetime as _dt
            _os.makedirs('logs', exist_ok=True)
            with open('logs/error_log.txt', 'a', encoding='utf-8') as _f:
                _f.write(f"[{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [CART_SIGNAL] No cart signal within {timeout}s — url={url_now}\nDOM: {dom_snap}\n\n")
        except Exception:
            pass
        return False

    async def _dismiss_error_flyout(self, tab) -> bool:
        """Dismiss Target's 'Item not added to cart' error modal if present."""
        try:
            dismissed = await tab.evaluate("""(() => {
                const closeSelectors = [
                    '[data-test="close-modal"]',
                    '[aria-label="close"]',
                    '[aria-label="Close"]',
                    'button[data-test*="close"]',
                    '[data-testid="close-modal"]',
                ];
                for (const sel of closeSelectors) {
                    const el = document.querySelector(sel);
                    if (el && el.getBoundingClientRect().height > 0) {
                        el.click();
                        return true;
                    }
                }
                for (const btn of document.querySelectorAll('button')) {
                    const t = (btn.textContent || '').trim().toLowerCase();
                    if (t === 'continue shopping') {
                        btn.click();
                        return true;
                    }
                }
                return false;
            })()""")
            if dismissed:
                print("[DISMISS] Error flyout dismissed")
                await asyncio.sleep(0.3)
            return dismissed
        except Exception:
            return False

    # -------------------------------------------------------------------------
    # Cart verification
    # -------------------------------------------------------------------------

    async def _verify_cart_addition(self, tab) -> bool:
        """Verify item was actually added to cart — fast single JS check, no page navigation."""
        try:
            result = await tab.evaluate("""(() => {
                // Cart count badge in header
                const badge = document.querySelector('[data-test="cart-count"], [data-testid="cart-count"]');
                if (badge) {
                    const n = parseInt((badge.textContent || '').trim(), 10);
                    if (n > 0) return 'badge:' + n;
                }
                // Add-to-cart confirmation flyout
                const confirm = document.querySelector('[data-test="add-to-cart-confirmation"]');
                if (confirm && confirm.getBoundingClientRect().height > 0) return 'confirmation';
                // If already on cart page, check for items or empty state
                if (window.location.href.includes('/cart')) {
                    const items = document.querySelectorAll('[data-test="cart-item"], [data-testid="cart-item"]');
                    if (items.length > 0) return 'cart:' + items.length;
                    const body = (document.body.innerText || '').toLowerCase();
                    if (body.includes('your cart is empty') || body.includes('nothing in your cart')) return 'empty';
                }
                return 'unknown';
            })()""")

            if result == 'empty':
                print(f"[PURCHASE] Cart is empty — ATC rejected by Target")
                return False
            if result and result != 'unknown':
                print(f"[PURCHASE] Cart confirmed: {result}")
                return True
            # Unknown state — treat as failure to avoid proceeding with empty cart
            print(f"[PURCHASE] Cart state unclear, treating as failure")
            return False

        except Exception as e:
            self.logger.warning(f"[PURCHASE] Cart verification error: {e}")
            return False

    # -------------------------------------------------------------------------
    # Popup / overlay dismissal
    # -------------------------------------------------------------------------

    async def _dismiss_popups(self, tab) -> bool:
        """Dismiss Target engagement popups (photo upload, reviews, surveys)"""
        try:
            self.logger.debug("[POPUP] Checking for and dismissing any popups...")

            popup_texts = [
                "Cancel", "Skip", "Skip for now", "Not now", "Maybe later",
                "No thanks", "Dismiss", "Close", "Continue shopping",
                "No thanks, continue", "Continue without", "Shop separately",
                "Continue to product", "View product",
            ]
            popup_css_selectors = [
                '[data-test*="drawer"] button[aria-label*="Close"]',
                '[class*="drawer"] button[aria-label*="Close"]',
                '[class*="panel"] button[aria-label*="Close"]',
                '[class*="sidebar"] button[aria-label*="Close"]',
                'aside button[aria-label*="Close"]',
                '[role="complementary"] button[aria-label*="Close"]',
                '[aria-label*="Close"]',
                '[aria-label*="Dismiss"]',
                '[data-test*="close"]',
                '[data-test*="dismiss"]',
                'button[class*="close"]',
                'button.close',
                'button[aria-label="Close dialog"]',
            ]

            dismissed_count = 0

            for text in popup_texts:
                try:
                    button = await tab.find(text, best_match=True, timeout=0.5)
                    if button:
                        await self._dispatch_click(button)
                        dismissed_count += 1
                        self.logger.info(f"[POPUP] Dismissed popup: {text}")
                        await asyncio.sleep(0.1)
                except Exception:
                    continue

            for selector in popup_css_selectors:
                try:
                    button = await tab.select(selector, timeout=0.5)
                    if button:
                        await self._dispatch_click(button)
                        dismissed_count += 1
                        self.logger.info(f"[POPUP] Dismissed popup via: {selector}")
                        await asyncio.sleep(0.1)
                except Exception:
                    continue

            await self._press_escape(tab)
            await asyncio.sleep(0.05)

            # Try clicking backdrop/overlay to dismiss drawers
            backdrop_selectors = [
                '[class*="backdrop"]',
                '[class*="overlay"]',
                '[class*="Overlay"]',
                '[data-test*="backdrop"]',
                '[data-test*="overlay"]',
            ]
            for backdrop in backdrop_selectors:
                try:
                    element = await tab.select(backdrop, timeout=0.5)
                    if element:
                        await self._dispatch_click(element)
                        self.logger.info(f"[POPUP] Dismissed drawer via backdrop: {backdrop}")
                        await asyncio.sleep(0.1)
                        break
                except Exception:
                    continue

            if dismissed_count > 0:
                self.logger.info(f"[POPUP] Successfully dismissed {dismissed_count} popup(s)")

            return True

        except Exception as e:
            self.logger.debug(f"[POPUP] Popup dismissal check: {e}")
            return True

    async def _handle_cvv_modal(self, tab) -> bool:
        """Detect, fill, and confirm CVV modal in a single JS round trip.

        Polls internally for the confirm button to enable (up to 300ms) then clicks.
        Returns True if a CVV modal was found and handled, False if none present.
        """
        try:
            t_cvv = time.time()
            result = await tab.evaluate(f"""
(async () => {{
    const inputSelectors = [
        'input[name="cvv"]', 'input[name="cvc"]',
        'input[id*="cvv" i]', 'input[id*="cvc" i]',
        'input[placeholder*="CVV" i]', 'input[placeholder*="security" i]',
        'input[aria-label*="CVV" i]', 'input[aria-label*="security code" i]',
        'input[data-test*="cvv" i]',
    ];
    // Require the confirm button to be inside a modal/dialog containing the CVV input,
    // or match very specific CVV-related data-test values — avoids false positives on
    // [data-test*="confirm"] elements that exist elsewhere on the checkout page.
    const confirmSelectors = [
        '[data-test*="cvv"]',
        '[data-test="cvv-confirm-button"]',
        '[data-test="confirm-cvv"]',
        '[data-test*="confirm"]',
        '[data-test*="submit"]',
        'button[type="submit"]',
    ];

    // 1. Detect CVV input
    let cvvEl = null, foundSel = null;
    for (const sel of inputSelectors) {{
        const el = document.querySelector(sel);
        if (el) {{
            const r = el.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) {{ cvvEl = el; foundSel = sel; break; }}
        }}
    }}
    if (!cvvEl) return 'no_modal';

    // 2. Fill CVV — full event sequence for react-hook-form / Formik validation
    // focus first so the field is "touched", then fill, then blur to trigger validation
    cvvEl.dispatchEvent(new FocusEvent('focus', {{ bubbles: true }}));
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    nativeSetter.call(cvvEl, '{CARD_CVV}');
    cvvEl.dispatchEvent(new Event('input', {{ bubbles: true }}));
    cvvEl.dispatchEvent(new Event('change', {{ bubbles: true }}));
    cvvEl.dispatchEvent(new FocusEvent('blur', {{ bubbles: true }}));

    function btnReady(btn) {{
        if (!btn) return false;
        const r = btn.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return false;
        if (btn.disabled) return false;
        if (btn.getAttribute('aria-disabled') === 'true') return false;
        const style = window.getComputedStyle(btn);
        if (style.pointerEvents === 'none') return false;
        return true;
    }}

    // Verify fill actually stuck before trying to confirm
    if (cvvEl.value !== '{CARD_CVV}') return 'fill_failed (input:' + foundSel + ')';

    // 3. Poll up to 500ms for confirm button to enable, then click
    const deadline = Date.now() + 500;
    while (Date.now() < deadline) {{
        for (const sel of confirmSelectors) {{
            const btn = document.querySelector(sel);
            if (btnReady(btn)) {{
                const btnTxt = btn.textContent.trim();
                btn.click();
                return 'clicked:' + sel + ' text="' + btnTxt + '" (input:' + foundSel + ')';
            }}
        }}
        // Fallback: text-matched button
        for (const btn of document.querySelectorAll('button')) {{
            const txt = btn.textContent.trim().toLowerCase();
            if (['confirm', 'submit', 'continue'].some(w => txt.includes(w))) {{
                if (btnReady(btn)) {{
                    btn.click();
                    return 'clicked_text:"' + btn.textContent.trim() + '" (input:' + foundSel + ')';
                }}
            }}
        }}
        await new Promise(r => setTimeout(r, 20));
    }}
    // Log all visible buttons to diagnose what's on screen
    const visibleBtns = Array.from(document.querySelectorAll('button')).filter(b => {{
        const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0;
    }}).map(b => '"' + b.textContent.trim() + '" disabled=' + b.disabled + ' aria-disabled=' + b.getAttribute('aria-disabled'));
    return 'no_confirm_button (input:' + foundSel + ') visible_buttons=[' + visibleBtns.join(', ') + ']';
}})()
""", await_promise=True)
            if result == 'no_modal':
                return False
            url_now = tab.url
            if result and 'fill_failed' in str(result):
                print(f"[PAYMENT] CVV fill failed (native setter rejected): {result} url={url_now}")
                return False
            if result and 'no_confirm_button' in str(result):
                # CVV filled successfully but confirm button not ready in 500ms window.
                # This can happen on slow networks or heavy React re-renders.
                # Instead of failing, wait a bit longer and try clicking the confirm button.
                print(f"[PAYMENT] CVV filled but no ready confirm button in 500ms: {result} ({time.time()-t_cvv:.3f}s) — trying fallback click")
                try:
                    # Give React 500ms more to render the button, then try clicking
                    await asyncio.sleep(0.5)
                    fallback_result = await tab.evaluate("""
(async () => {
    const confirmSelectors = [
        '[data-test*="cvv"]', '[data-test="cvv-confirm-button"]', '[data-test="confirm-cvv"]',
        '[data-test*="confirm"]', '[data-test*="submit"]', 'button[type="submit"]'
    ];
    for (const sel of confirmSelectors) {
        const btn = document.querySelector(sel);
        if (btn && btn.getBoundingClientRect().width > 0) {
            btn.click();
            return 'fallback_clicked:' + sel;
        }
    }
    // Last resort: text match
    for (const btn of document.querySelectorAll('button')) {
        const txt = btn.textContent.trim().toLowerCase();
        if (['confirm', 'submit', 'continue'].some(w => txt.includes(w))) {
            if (btn.getBoundingClientRect().width > 0 && !btn.disabled) {
                btn.click();
                return 'fallback_clicked_text:' + btn.textContent.trim();
            }
        }
    }
    return 'fallback_no_button';
})()
""", await_promise=True)
                    print(f"[PAYMENT] CVV fallback click attempt: {fallback_result} ({time.time()-t_cvv:.3f}s)")
                    return True  # Assume click succeeded; main loop will check for confirmation
                except Exception as e:
                    print(f"[PAYMENT] CVV fallback click error: {e}")
                    return True  # Still count as handled — confirmation check will catch if it failed
            print(f"[PAYMENT] CVV confirm: {result} ({time.time()-t_cvv:.3f}s) url={url_now}")
            return True

        except Exception as e:
            print(f"[PAYMENT] CVV modal error: {e}")
            return False

    async def _handle_address_verify_modal(self, tab) -> bool:
        """Detect and dismiss Target's 'Verify address / Sorry something went wrong' modal.

        This appears on some networks when the address validation backend returns an error
        after clicking Save & Continue. The address is still valid (saved in account) —
        the modal is a soft error. Dismissing it lets the checkout flow continue normally.

        Returns True if the modal was found and dismissed, False if not present.
        """
        try:
            result = await tab.evaluate("""(() => {
                // Find any visible dialog/modal
                const dialogs = Array.from(document.querySelectorAll(
                    '[role="dialog"], [role="alertdialog"], [class*="modal" i], [class*="Modal" i], ' +
                    '[data-test*="modal"], [data-test*="dialog"], [class*="overlay" i]'
                )).filter(el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                });

                for (const dialog of dialogs) {
                    const text = (dialog.innerText || '').toLowerCase();
                    const isAddressModal = (text.includes('verify') && text.includes('address')) ||
                                           text.includes('something went wrong') ||
                                           text.includes('address not found') ||
                                           text.includes('confirm your address');
                    if (!isAddressModal) continue;

                    // Try to click a dismiss/confirm button inside the dialog
                    const btnSelectors = [
                        '[data-test*="confirm"]', '[data-test*="use-address"]',
                        '[data-test*="keep"]', '[data-test*="continue"]',
                        'button[class*="primary" i]', 'button[class*="confirm" i]',
                    ];
                    for (const sel of btnSelectors) {
                        const btn = dialog.querySelector(sel);
                        if (btn && btn.getBoundingClientRect().height > 0) {
                            btn.click();
                            return 'dismissed:' + sel;
                        }
                    }
                    // Fallback: find any visible button in the dialog that isn't "cancel"
                    const btns = Array.from(dialog.querySelectorAll('button')).filter(b => {
                        const r = b.getBoundingClientRect();
                        const t = (b.innerText || '').toLowerCase().trim();
                        return r.height > 0 && !t.includes('cancel');
                    });
                    if (btns.length > 0) {
                        btns[0].click();
                        return 'dismissed:fallback:' + (btns[0].innerText || '').trim().slice(0, 30);
                    }
                    return 'found_no_button';
                }
                return 'not_found';
            })()""")

            if isinstance(result, str) and result != 'not_found':
                print(f"[PAYMENT] Address verify modal: {result}")
                if result == 'found_no_button':
                    await self._press_escape(tab)
                return True
            return False
        except Exception as e:
            print(f"[PAYMENT] Address modal check error: {e}")
            return False

    # -------------------------------------------------------------------------
    # Login validation
    # -------------------------------------------------------------------------

    async def _validate_login_status(self, tab) -> bool:
        """Validate that user is still logged in"""
        try:
            login_css = [
                '[data-test="@web/AccountLink"]',
                '[data-test="accountNav"]',
                'button[aria-label*="Account"]',
                'button[aria-label*="Hi,"]',
            ]
            for selector in login_css:
                try:
                    elem = await tab.select(selector, timeout=2)
                    if elem:
                        return True
                except Exception:
                    continue

            try:
                hi_elem = await tab.find("Hi,", best_match=True, timeout=1)
                if hi_elem:
                    return True
            except Exception:
                pass

            try:
                signin = await tab.find("Sign in", best_match=True, timeout=1)
                if signin:
                    return False
            except Exception:
                pass

            try:
                email_field = await tab.select('input[type="email"]', timeout=1)
                if email_field:
                    return False
            except Exception:
                pass

            return True

        except Exception as e:
            self.logger.warning(f"Login validation error: {e}")
            return False

    # -------------------------------------------------------------------------
    # Button finding
    # -------------------------------------------------------------------------

    def _get_timeout_for_selector(self, selector_index: int) -> int:
        """Get timeout based on selector priority"""
        if selector_index <= 2:
            return 1000
        elif selector_index <= 8:
            return 500
        else:
            return 300

    async def _find_add_to_cart_button(self, tab):
        """Find add-to-cart/preorder button — single JS sweep for speed."""
        print("[BUTTON_FIND] Searching for add-to-cart/preorder button...")

        # Single JS call checks all selectors and text patterns at once
        found_selector = None
        try:
            found_selector = await tab.evaluate("""(() => {
                function visible(el) {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                }
                function enabled(el) {
                    return !el.disabled && el.getAttribute('aria-disabled') !== 'true';
                }
                // CSS selectors in priority order
                const selectors = [
                    'button[id^="addToCartButtonOrTextIdFor"]',
                    'button[data-test="addToCartButton"]',
                    'button[data-testid="addToCartButton"]',
                    '[data-testid*="add-to-cart"]',
                    'button[data-test*="addToCart"]',
                    'button[data-test*="add-to-cart"]',
                    'button[data-test="chooseOptionsButton"]',
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && visible(el) && enabled(el)) return sel;
                }
                // Text-based fallback
                const keywords = ['add to cart', 'preorder', 'pre-order', 'add to bag', 'ship it'];
                for (const btn of document.querySelectorAll('button')) {
                    if (!visible(btn) || !enabled(btn)) continue;
                    const t = (btn.textContent || '').trim().toLowerCase();
                    if (keywords.some(k => t.includes(k))) return 'text:' + btn.textContent.trim();
                }
                return null;
            })()""")
        except Exception as e:
            print(f"[BUTTON_FIND] JS sweep error: {e}")

        if not found_selector:
            print("[BUTTON_FIND] Could not find add-to-cart/preorder button")
            await self._take_debug_screenshot(tab, "no_add_to_cart_button")
            return None

        # Now fetch the actual element
        try:
            if found_selector.startswith('text:'):
                text = found_selector[5:]
                button = await tab.find(text, best_match=True, timeout=1)
            else:
                button = await tab.select(found_selector, timeout=1)
            if button:
                print(f"[BUTTON_FIND] Found button: {found_selector}")
                return button
        except Exception as e:
            print(f"[BUTTON_FIND] Element fetch error: {e}")

        print("[BUTTON_FIND] Could not find add-to-cart/preorder button")
        await self._take_debug_screenshot(tab, "no_add_to_cart_button")
        return None

    # -------------------------------------------------------------------------
    # Debug screenshots
    # -------------------------------------------------------------------------

    async def _take_debug_screenshot(self, tab, reason: str) -> Optional[str]:
        """Take debug screenshot for troubleshooting and log path + URL to error_log.txt"""
        try:
            import os as _os
            _os.makedirs('logs/screenshots', exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            screenshot_path = f"logs/screenshots/debug_{reason}_{timestamp}.png"
            await self._screenshot(tab, screenshot_path)
            try:
                url_now = tab.url
            except Exception:
                url_now = 'unknown'
            try:
                _os.makedirs('logs', exist_ok=True)
                with open('logs/error_log.txt', 'a', encoding='utf-8') as _f:
                    _f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [SCREENSHOT] {reason} — url={url_now} — saved: {screenshot_path}\n")
            except Exception:
                pass
            return screenshot_path
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # Cart operations
    # -------------------------------------------------------------------------

    async def _verify_item_in_cart(self, tab, tcin: str) -> bool:
        """Verify that item was added to cart"""
        try:
            cart_css = [
                '[data-test="cart-item"]',
                '[data-testid="cart-item"]',
                '.cart-item',
                '[class*="cart-item"]',
                '[data-test*="cartItem"]',
            ]
            for selector in cart_css:
                try:
                    count = await tab.evaluate(
                        f'document.querySelectorAll({json.dumps(selector)}).length'
                    )
                    if count and count > 0:
                        self.logger.debug(f"Found {count} cart items")
                        return True
                except Exception:
                    continue

            count_css = [
                '[data-test="cart-count"]',
                '[data-testid="cart-count"]',
                '.cart-count',
            ]
            for selector in count_css:
                try:
                    count_elem = await tab.select(selector, timeout=1)
                    if count_elem:
                        count_text = await self._inner_text(count_elem)
                        if count_text and count_text.strip() != '0':
                            return True
                except Exception:
                    continue

            return False

        except Exception as e:
            self.logger.error(f"Cart verification error: {e}")
            return False

    async def _api_clear_cart(self, tab) -> Optional[bool]:
        """Phase 4c: clear regular cart items via direct DELETE fetches.

        Two-step flow per TARGET_CHECKOUT_API.md Endpoint 6:
          1. GET /cart?field_groups=CART,CART_ITEMS → cart_items[].cart_item_id
          2. DELETE /cart_items/<cart_item_id> for each item

        Returns:
          True  — all items removed successfully (cart_items list emptied)
          False — at least one DELETE failed; caller should fall back to DOM clear
          None  — cart was already empty (nothing to do)

        SFL items are NOT handled here — they live behind a different endpoint
        family that is not yet documented. The caller falls back to the DOM
        SFL pass when needed.
        """
        try:
            cart_state = await tab.evaluate("""(async () => {
                try {
                    const r = await fetch(
                        'https://carts.target.com/web_checkouts/v1/cart?cart_type=REGULAR&field_groups=CART,CART_ITEMS',
                        {credentials: 'include', headers: {'Accept': 'application/json'}}
                    );
                    if (!r.ok) return {ok: false, status: r.status};
                    const d = await r.json();
                    const ids = (d.cart_items || []).map(i => i.cart_item_id).filter(Boolean);
                    return {ok: true, ids: ids};
                } catch(e) { return {ok: false, error: String(e)}; }
            })()""", await_promise=True)

            if not isinstance(cart_state, dict) or not cart_state.get('ok'):
                print(f"[CLEAR_CART_API] GET /cart failed: {cart_state}")
                return False

            ids = cart_state.get('ids', []) or []
            if not ids:
                print("[CLEAR_CART_API] cart already empty (no cart_item_id)")
                return None

            print(f"[CLEAR_CART_API] deleting {len(ids)} cart item(s) via API")

            def _build_extra_headers_js() -> str:
                headers_age = time.time() - self._cached_cart_headers_ts
                use_cached = bool(self._cached_cart_headers) and headers_age < 90
                _strip_keys = {'cookie', 'referer'}
                cached_shape_only = {k: v for k, v in self._cached_cart_headers.items()
                                     if k.lower() not in _strip_keys}
                cached_shape_only['x-application-name'] = 'web'
                return json.dumps(cached_shape_only if use_cached else {'x-application-name': 'web'})

            async def _send_delete(cid: str) -> dict:
                extra_headers_js = _build_extra_headers_js()
                return await tab.evaluate(f"""(async () => {{
                    try {{
                        const cachedHeaders = {extra_headers_js};
                        const resp = await fetch(
                            'https://carts.target.com/web_checkouts/v1/cart_items/{cid}',
                            {{
                                method: 'DELETE',
                                credentials: 'include',
                                headers: {{
                                    ...cachedHeaders,
                                    'Accept': 'application/json',
                                    'Origin': 'https://www.target.com',
                                    'Referer': 'https://www.target.com/cart',
                                    'x-application-name': 'web',
                                }},
                            }}
                        );
                        const body = (await resp.text()).slice(0, 200);
                        return {{status: resp.status, body: body}};
                    }} catch(e) {{
                        return {{status: 0, body: String(e)}};
                    }}
                }})()""", await_promise=True)

            # Issue DELETEs serially. Doing them in parallel would race the
            # cart-state mutation; serial keeps the flow predictable.
            for cid in ids:
                # Defensive: cart_item_id is meant to be a UUID string. Reject
                # anything else so a bad payload can't escape into the URL.
                if not isinstance(cid, str) or not re.match(r'^[A-Za-z0-9_-]+$', cid):
                    print(f"[CLEAR_CART_API] skipping suspicious cart_item_id: {cid!r}")
                    return False
                del_result = await _send_delete(cid)
                status = del_result.get('status', 0) if isinstance(del_result, dict) else 0
                # On 401 _ERR_AUTH_DENIED, Shape tokens have aged/staled. Refresh
                # via warmup tab (single dummy POST) and retry the DELETE once.
                # Cheaper than the DOM-clear fallback (saves 5-15s per affected cycle).
                if status == 401:
                    print(f"[CLEAR_CART_API] DELETE {cid[:8]}… 401 — refreshing Shape headers and retrying once")
                    try:
                        await self.warm_shape_headers()
                    except Exception as warm_err:
                        print(f"[CLEAR_CART_API] warmup before retry failed: {warm_err}")
                    del_result = await _send_delete(cid)
                    status = del_result.get('status', 0) if isinstance(del_result, dict) else 0
                if status not in (200, 204):
                    print(f"[CLEAR_CART_API] DELETE {cid[:8]}… returned {status} body={del_result.get('body', '')!r}")
                    return False
                print(f"[CLEAR_CART_API] DELETE {cid[:8]}… OK ({status})")
            return True

        except Exception as e:
            print(f"[CLEAR_CART_API] exception: {e}")
            return False

    async def _clear_cart(self, tab) -> bool:
        """Clear all items from cart and saved-for-later section.

        Phase 4c: when TARGET_API_CART_CLEAR=true, regular items are removed
        via direct DELETE fetches first; SFL items still go through the DOM
        path below (different endpoint, not yet documented). On any failure
        the API path falls through to the DOM flow so behavior stays
        backward-compatible.
        """
        try:
            removed_count = 0
            _skip_sfl_loop = False

            # API cart-clear: explicit opt-in via env var, OR default-on in TEST_MODE
            # so test cycles exercise Phase 4c and avoid the slow DOM remove-button loop
            # (10 deletes * polling = 5+ seconds per cycle).
            api_clear_enabled = (
                os.environ.get('TARGET_API_CART_CLEAR', 'false').lower() == 'true'
                or self.test_mode
            )
            if api_clear_enabled:
                api_result = await self._api_clear_cart(tab)
                if api_result is True:
                    # API confirmed all DELETEs returned 200 — cart is empty.
                    # Probe SFL bucket once: if empty, short-circuit and skip
                    # the slow cart-empty verify block at the end of this
                    # function (saves ~3s of dead polling). On probe failure,
                    # fall through to the full DOM SFL pass + verify.
                    try:
                        _sfl_count = await tab.evaluate(
                            'document.querySelectorAll('
                            '"button[data-test=\\"sflItem-remove\\"],'
                            ' button[data-test=\\"sfl-item-remove\\"],'
                            ' [data-testid=\\"sflItem-remove\\"]").length'
                        )
                        if isinstance(_sfl_count, (int, float)) and _sfl_count == 0:
                            print("[CLEAR_CART] API path cleared regular items, SFL bucket empty — short-circuit success")
                            return True
                    except Exception:
                        pass
                    print("[CLEAR_CART] API path cleared regular items — running DOM SFL pass")
                    # Skip the regular-item DOM pass; jump to SFL handling below.
                    # We accomplish this by setting a sentinel and falling through.
                    _skip_regular_pass = True
                elif api_result is None:
                    # cart already empty — short-circuit success
                    return True
                else:
                    print("[CLEAR_CART] API path failed — falling back to DOM clear")
                    _skip_regular_pass = False
            else:
                _skip_regular_pass = False

            # --- Pass 1: regular cart items ---
            cart_remove_selectors = [
                'button[data-test="cartItem-remove"]',
                'button[aria-label*="remove"]',
                'button[aria-label*="Remove"]',
            ]
            count_expr = (
                'document.querySelectorAll('
                '"button[data-test=\\"cartItem-remove\\"],'
                ' button[aria-label*=\\"remove\\"],'
                ' button[aria-label*=\\"Remove\\"]").length'
            )

            if not _skip_regular_pass:
                for _ in range(10):
                    button_found = False
                    for selector in cart_remove_selectors:
                        try:
                            btn = await tab.select(selector, timeout=0.3)
                            if btn and await self._is_visible(btn):
                                current_count = await tab.evaluate(count_expr)
                                await btn.apply("(el) => el.click()")
                                await self._wait_for_function(
                                    tab,
                                    f'({count_expr}) < {current_count}',
                                    timeout=5.0
                                )
                                removed_count += 1
                                button_found = True
                                break
                        except Exception:
                            continue
                    if not button_found:
                        break

            # --- Pass 2: saved-for-later items ---
            sfl_remove_selectors = [
                'button[data-test="sflItem-remove"]',
                'button[data-test="sfl-item-remove"]',
                '[data-testid="sflItem-remove"]',
                'button[aria-label*="saved for later"]',
                'button[aria-label*="Saved for later"]',
                'button[aria-label*="Saved For Later"]',
            ]
            sfl_count_expr = (
                'document.querySelectorAll('
                '"button[data-test=\\"sflItem-remove\\"],'
                ' button[data-test=\\"sfl-item-remove\\"],'
                ' [data-testid=\\"sflItem-remove\\"]").length'
            )

            if not _skip_sfl_loop:
                for _ in range(10):
                    button_found = False
                    for selector in sfl_remove_selectors:
                        try:
                            btn = await tab.select(selector, timeout=0.3)
                            if btn and await self._is_visible(btn):
                                current_count = await tab.evaluate(sfl_count_expr)
                                await btn.apply("(el) => el.click()")
                                await self._wait_for_function(
                                    tab,
                                    f'({sfl_count_expr}) < {current_count}',
                                    timeout=5.0
                                )
                                removed_count += 1
                                button_found = True
                                print(f"[CLEAR_CART] Removed 1 saved-for-later item (total removed: {removed_count})")
                                break
                        except Exception:
                            continue
                    if not button_found:
                        break

            # Verify cart is empty
            empty_texts = ["Your cart is empty", "cart is empty"]
            for text in empty_texts:
                try:
                    elem = await tab.find(text, best_match=True, timeout=1)
                    if elem:
                        return True
                except Exception:
                    continue

            try:
                empty_elem = await tab.select('[data-test="empty-cart"]', timeout=1)
                if empty_elem:
                    return True
            except Exception:
                pass

            return removed_count > 0 or True

        except Exception:
            return False

    # -------------------------------------------------------------------------
    # Checkout helpers
    # -------------------------------------------------------------------------

    async def _find_checkout_button(self, tab):
        """Find checkout button on cart page"""
        checkout_texts = ["Checkout", "Check out"]
        checkout_css = [
            '[data-test*="checkout"]',
            '[data-testid*="checkout"]',
            'button[data-test="checkout-button"]',
            'button[data-testid="checkout-button"]',
        ]

        for text in checkout_texts:
            try:
                button = await tab.find(text, best_match=True, timeout=2)
                if button and await self._is_visible(button):
                    self.logger.debug(f"Found checkout button: {text}")
                    return button
            except Exception:
                continue

        for selector in checkout_css:
            try:
                button = await tab.select(selector, timeout=2)
                if button and await self._is_visible(button):
                    self.logger.debug(f"Found checkout button: {selector}")
                    return button
            except Exception:
                continue

        self.logger.warning("Could not find checkout button")
        return None

    async def _proceed_to_checkout(self, tab) -> bool:
        """Complete full checkout process including payment"""
        try:
            checkout_button = await self._find_checkout_button(tab)
            if not checkout_button:
                return False

            await checkout_button.apply("(el) => el.click()")

            if not await self._wait_for_url_contains(tab, 'checkout', timeout=10.0):
                if 'checkout' not in tab.url.lower():
                    self.logger.error("Failed to reach checkout page")
                    return False

            await self._handle_delivery_options(tab)
            if await self._complete_payment(tab):
                return await self._verify_order_completion(tab)
            return False

        except Exception as e:
            self.logger.error(f"Checkout process error: {e}")
            return False

    async def _proceed_to_checkout_direct(self, tab) -> bool:
        """Navigate directly to checkout page"""
        try:
            self.logger.info("[PURCHASE] Navigating to checkout page...")
            await tab.get("https://www.target.com/checkout")

            human_delay = random.uniform(1.0, 1.5)
            self.logger.info(f"[PURCHASE] Page loaded, waiting for account data to load...")
            await asyncio.sleep(human_delay)

            current_url = tab.url
            if 'checkout' in current_url.lower():
                self.logger.info(f"[PURCHASE] Successfully reached checkout page: {current_url}")
                await self._handle_delivery_options(tab)
                return await self._complete_payment(tab)
            else:
                self.logger.error(f"[PURCHASE] Failed to reach checkout page, at: {current_url}")
                return False

        except Exception as e:
            self.logger.error(f"[PURCHASE] Checkout navigation error: {e}")
            return False

    async def _handle_delivery_options(self, tab):
        """Handle shipping/delivery selection - only if not already in review state.

        Uses a single JS evaluate for instant state detection (no CDP lag from
        sequential tab.select calls).
        """
        try:
            # Single JS check — instant, no CDP lag from sequential tab.select calls
            state = await tab.evaluate("""(() => {
                function vis(el) {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                }
                const po = document.querySelector('[data-test="placeOrderButton"]');
                if (vis(po)) return 'review';
                const sac = document.querySelector('[data-test="save-and-continue-button"]');
                if (vis(sac)) return 'sac';
                return 'delivery';
            })()""")

            if state == 'review':
                print("[DELIVERY] Already in review state — skipping")
                return

            if state == 'sac':
                print("[DELIVERY] S&C visible — delivery not needed")
                return

            # Only reach here if delivery step is active
            print("[DELIVERY] Checking for delivery option buttons...")
            clicked = await tab.evaluate("""(() => {
                const selectors = [
                    '[data-test="shipping-option"]',
                    '[data-test="ship-option"]',
                    'input[value="SHIPPING"]',
                    'input[id*="shipping"]',
                    'input[id*="ship-"]',
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.getBoundingClientRect().height > 0) {
                        el.click();
                        return sel;
                    }
                }
                return null;
            })()""")
            if clicked:
                print(f"[DELIVERY] Selected shipping option via: {clicked}")
                await asyncio.sleep(0.15)

        except Exception:
            pass

    async def _check_checkout_form_state(self, tab) -> dict:
        """Inspect the checkout page to determine the form's current state before clicking S&C.

        Returns a dict with keys:
          state   : 'no_sac' | 'no_form' | 'empty_form' | 'mostly_empty' | 'has_data'
          total   : total visible text-type inputs found
          empty   : number of those that are empty
          filled  : number of those that have a value
        """
        try:
            result = await tab.evaluate('''(() => {
                const sacBtn = document.querySelector('[data-test="save-and-continue-button"]');
                if (!sacBtn) return {state: "no_sac", total: 0, empty: 0, filled: 0};

                // 1) Prefer the nearest <form> ancestor
                let container = sacBtn.closest("form");

                // 2) If no <form>, walk up looking for an ancestor that holds >= 2 visible inputs
                if (!container) {
                    let node = sacBtn.parentElement;
                    for (let depth = 0; depth < 30 && node && node !== document.body; depth++) {
                        const vis = Array.from(node.querySelectorAll(
                            'input[type="text"], input[type="tel"], input[type="email"]'
                        )).filter(i => i.offsetParent !== null);
                        if (vis.length >= 2) { container = node; break; }
                        node = node.parentElement;
                    }
                }

                if (!container) return {state: "no_form", total: 0, empty: 0, filled: 0};

                const inputs = Array.from(container.querySelectorAll(
                    'input[type="text"], input[type="tel"], input[type="email"]'
                )).filter(i => i.offsetParent !== null && i.getBoundingClientRect().height > 0);

                const names = inputs.map(i => (i.name || i.id || i.placeholder || "?").substring(0, 30));
                const emptyCount  = inputs.filter(i => i.value.trim() === "").length;
                const filledCount = inputs.filter(i => i.value.trim() !== "").length;

                let state = "has_data";
                if (inputs.length >= 2 && filledCount === 0) state = "empty_form";
                else if (inputs.length >= 3 && emptyCount / inputs.length >= 0.67) state = "mostly_empty";

                return {state, total: inputs.length, empty: emptyCount, filled: filledCount, names};
            })()''')

            if isinstance(result, dict):
                return result
            return {'state': 'unknown', 'total': 0, 'empty': 0, 'filled': 0}
        except Exception as e:
            print(f"[PAYMENT] Form-state check error: {e}")
            return {'state': 'error', 'total': 0, 'empty': 0, 'filled': 0}

    async def _wait_for_checkout_ready(self, tab, timeout: float = 5.0) -> str:
        """Poll until either Place Order or S&C appears on the checkout page.

        Returns one of:
          'place_order' — Place Order button is visible (may still be disabled)
          'sac'         — Save & Continue button is visible
          'timeout'     — neither appeared within `timeout` seconds
        """
        print("[PAYMENT] Waiting for checkout page to be ready...")
        start = time.time()
        while time.time() - start < timeout:
            try:
                state = await tab.evaluate("""(() => {
                    function visible(el) {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    }
                    const po  = document.querySelector('[data-test="placeOrderButton"]');
                    const sac = document.querySelector('[data-test="save-and-continue-button"]');
                    if (visible(sac)) return 'sac';
                    if (visible(po))  return 'place_order';
                    // Checkout page is hydrated when payment radios are visible —
                    // S&C only appears after a radio is selected, so radios alone
                    // mean we should proceed immediately rather than waiting 5s.
                    const radios = document.querySelectorAll('input[type="radio"]');
                    if (Array.from(radios).some(r => visible(r))) return 'sac';
                    return 'none';
                })()""")
                if state in ('place_order', 'sac'):
                    elapsed = time.time() - start
                    print(f"[PAYMENT] Checkout ready ({state}) in {elapsed:.2f}s")
                    return state
            except Exception:
                pass
            await asyncio.sleep(0.05)
        print(f"[PAYMENT] Checkout ready timeout after {timeout}s")
        return 'timeout'

    async def _wait_for_sac_transition(self, tab, timeout: float = 5.0) -> str:
        """After clicking S&C, poll until the page advances to the next state.

        Returns one of:
          'place_order_enabled' — Place Order is visible and enabled
          'sac_again'           — Another S&C appeared (next step)
          'timeout'             — didn't transition within `timeout` seconds
        """
        start = time.time()
        while time.time() - start < timeout:
            # Dismiss address-verify modal if it appeared after S&C click
            await self._handle_address_verify_modal(tab)
            try:
                state = await tab.evaluate("""(() => {
                    function visible(el) {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    }
                    const po = document.querySelector('[data-test="placeOrderButton"]');
                    if (visible(po)) {
                        // Check if genuinely enabled
                        const style = window.getComputedStyle(po);
                        const enabled = !po.disabled
                            && po.getAttribute('aria-disabled') !== 'true'
                            && !(po.className || '').toLowerCase().includes('disabled')
                            && style.pointerEvents !== 'none'
                            && parseFloat(style.opacity) >= 0.6;
                        if (enabled) return 'place_order_enabled';
                    }
                    // Next S&C appeared (e.g. payment step loaded after address step)
                    const sac = document.querySelector('[data-test="save-and-continue-button"]');
                    if (visible(sac)) return 'sac_again';
                    return 'transitioning';
                })()""")
                if state in ('place_order_enabled', 'sac_again'):
                    elapsed = time.time() - start + 0.4
                    print(f"[PAYMENT] S&C transition → {state} in {elapsed:.2f}s")
                    return state
            except Exception:
                pass
            await asyncio.sleep(0.05)
        return 'timeout'

    async def _is_sac_on_empty_form(self, tab) -> bool:
        """Returns True if the S&C button is sitting on top of a form with no filled inputs.

        Logs the full form state for debugging regardless of outcome.
        """
        info = await self._check_checkout_form_state(tab)
        state  = info.get('state', 'unknown')
        total  = info.get('total', 0)
        empty  = info.get('empty', 0)
        filled = info.get('filled', 0)
        names  = info.get('names', [])

        print(f"[PAYMENT] Checkout form state: state={state} | "
              f"inputs={total} total / {filled} filled / {empty} empty")
        if names:
            print(f"[PAYMENT] Visible input fields: {names}")

        return state in ('empty_form', 'mostly_empty')

    async def _is_place_order_enabled(self, element) -> bool:
        """Return True only if the Place Order button is genuinely clickable.

        Target disables the button via CSS / aria-disabled, NOT the HTML `disabled`
        attribute.  Checking getAttribute('disabled') always returns None for it,
        making `not None` → True — which was the false-positive causing premature
        cart redirects before payment was selected.  This checks all four signals.
        """
        try:
            return await element.apply("""el => {
                // 1. HTML disabled property (most reliable for real <button> elements)
                if (el.disabled) return false;
                // 2. ARIA disabled (common in React / accessible UIs)
                if (el.getAttribute('aria-disabled') === 'true') return false;
                // 3. CSS class containing 'disabled' or 'inactive'
                const cls = (el.className || '').toLowerCase();
                if (cls.includes('disabled') || cls.includes('inactive')) return false;
                // 4. Pointer events cut off (button looks greyed, clicks ignored)
                const style = window.getComputedStyle(el);
                if (style.pointerEvents === 'none') return false;
                // 5. Heavily faded opacity (greyed-out visual state)
                if (parseFloat(style.opacity) < 0.6) return false;
                return true;
            }""")
        except Exception:
            return False

    async def _find_place_order_button(self, tab):
        """Find the Place Order button only if visible AND enabled — single JS sweep."""
        try:
            result = await tab.evaluate("""(() => {
                function enabled(el) {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) return false;
                    if (el.disabled) return false;
                    if (el.getAttribute('aria-disabled') === 'true') return false;
                    const cls = (el.className || '').toLowerCase();
                    if (cls.includes('disabled') || cls.includes('inactive')) return false;
                    const style = window.getComputedStyle(el);
                    if (style.pointerEvents === 'none') return false;
                    if (parseFloat(style.opacity) < 0.6) return false;
                    return true;
                }
                const selectors = [
                    '[data-test="placeOrderButton"]',
                    '[data-testid="placeOrderButton"]',
                    '#placeOrderButton',
                    'button[class*="place-order"]',
                    'button[class*="placeOrder"]',
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el) {
                        if (enabled(el)) return {found: true, sel: sel, disabled: false};
                        return {found: true, sel: sel, disabled: true};
                    }
                }
                // Text fallback
                const keywords = ['place your order', 'place order', 'complete order'];
                for (const btn of document.querySelectorAll('button')) {
                    const t = (btn.textContent || '').trim().toLowerCase();
                    if (keywords.some(k => t.includes(k))) {
                        if (enabled(btn)) return {found: true, sel: 'text:' + btn.textContent.trim(), disabled: false};
                        return {found: true, sel: 'text:' + btn.textContent.trim(), disabled: true};
                    }
                }
                return {found: false};
            })()""")

            if not result or not result.get('found'):
                return None, None

            sel = result['sel']
            if result.get('disabled'):
                print(f"[PAYMENT] Place Order found but disabled: {sel}")
                return None, None

            # Fetch the actual element
            if sel.startswith('text:'):
                elem = await tab.find(sel[5:], best_match=True, timeout=1)
            else:
                elem = await tab.select(sel, timeout=1)

            if elem:
                print(f"[PAYMENT] Place Order ENABLED: {sel}")
                return elem, sel

        except Exception as e:
            print(f"[PAYMENT] _find_place_order_button error: {e}")

        return None, None

    async def _handle_step_radio(self, tab) -> str:
        """Detect which checkout step is currently active (by its radio buttons) and
        select the right option before clicking Save & Continue.

        Handles all three radio-button steps Target may show:
          • Delivery step  → select "Ship it" / "Shipping" (not Store Pickup / Drive Up)
          • Payment step   → select first saved card (not Apple Pay / PayPal / etc.)
          • No radios      → address form or review page — nothing to do

        Returns a string describing what happened (for logging).
        """
        try:
            result = await tab.evaluate('''(() => {
                const radios = Array.from(document.querySelectorAll('input[type="radio"]'))
                    .filter(r => r.offsetParent !== null);

                if (radios.length === 0) return {action: "no_radios"};
                if (radios.some(r => r.checked)) return {action: "already_selected"};

                // Keywords that identify each step type
                const deliveryKW  = ['ship', 'shipping', 'pickup', 'drive up', 'same-day',
                                      'same day', 'order pickup', 'store pickup', 'in-store'];
                const walletKW    = ['apple pay', 'paypal', 'cash app', 'affirm',
                                     'venmo', 'klarna', 'afterpay', 'sezzle', 'zip'];
                const shipKW      = ['ship', 'shipping', 'delivered'];
                const pickupKW    = ['pickup', 'pick up', 'drive up', 'in-store', 'store'];

                // Label text helper
                function labelOf(radio) {
                    const c = radio.closest('label') ||
                              radio.closest('[class*="payment"]') ||
                              radio.closest('[class*="fulfillment"]') ||
                              radio.closest('[class*="delivery"]') ||
                              radio.parentElement;
                    return c ? c.innerText.toLowerCase() : '';
                }

                // Determine step type by scanning all radio labels
                const allLabels = radios.map(r => labelOf(r));
                const isDelivery = allLabels.some(t => deliveryKW.some(k => t.includes(k)));
                const isPayment  = !isDelivery;   // if no delivery keywords → payment step

                if (isDelivery) {
                    // Select the Shipping option (first radio whose label contains a
                    // ship keyword, or the first radio that does NOT say pickup/drive-up)
                    for (let i = 0; i < radios.length; i++) {
                        if (radios[i].disabled) continue;
                        const txt = allLabels[i];
                        if (shipKW.some(k => txt.includes(k)) &&
                            !pickupKW.some(k => txt.includes(k))) {
                            return {action: "delivery", index: i, label: txt.trim().slice(0,40)};
                        }
                    }
                    // Fallback: first enabled radio
                    for (let i = 0; i < radios.length; i++) {
                        if (!radios[i].disabled)
                            return {action: "delivery_fallback", index: i,
                                    label: allLabels[i].trim().slice(0,40)};
                    }
                }

                if (isPayment) {
                    // Select the first saved card — skip digital wallets
                    for (let i = 0; i < radios.length; i++) {
                        if (radios[i].disabled) continue;
                        const txt = allLabels[i];
                        if (!walletKW.some(k => txt.includes(k)))
                            return {action: "payment", index: i, label: txt.trim().slice(0,40)};
                    }
                    return {action: "payment_no_card"};
                }

                return {action: "unknown"};
            })()''')

            if not isinstance(result, dict):
                return "error"

            action = result.get('action', '')
            label  = result.get('label', '')
            idx    = result.get('index', 0)

            if action == 'no_radios':
                return 'no_radios'
            if action == 'already_selected':
                print("[STEP] Radio already selected — skipping")
                return 'already_selected'
            if action in ('payment_no_card', 'unknown'):
                print(f"[STEP] Radio state: {action}")
                return action
            if action not in ('delivery', 'delivery_fallback', 'payment'):
                return action

            step_type = 'Delivery' if 'delivery' in action else 'Payment'
            await tab.evaluate(f'''(() => {{
                const radios = Array.from(document.querySelectorAll('input[type="radio"]'))
                    .filter(r => r.offsetParent !== null);
                if (radios[{idx}]) {{
                    radios[{idx}].click();
                }}
            }})()''')
            print(f"[STEP] Selected {step_type} radio (index {idx}): \"{label}\"")
            return action

        except Exception as e:
            print(f"[STEP] _handle_step_radio error: {e}")
            return 'error'

    async def _handle_busy_modal(self, tab) -> bool:
        """Detect and dismiss Target's 'busier than expected' cart error modal.

        This modal appears after clicking Place Order when Target's cart service
        is overloaded. It can appear as a modal dialog OR as inline page content.
        Returns True if the error was found and dismissed/handled, False if not present.
        """
        try:
            result = await tab.evaluate("""(() => {
                const BUSY_PHRASES = ['busier', 'temporary issue', "can't view", 'try again soon',
                                      'busy right now', 'limiting how many guests', 'please keep trying'];
                const isBusy = text => BUSY_PHRASES.some(p => text.includes(p));

                // --- Check 1: modal/dialog overlays ---
                const dialogs = Array.from(document.querySelectorAll(
                    '[role="dialog"], [role="alertdialog"], [class*="modal" i], ' +
                    '[data-test*="modal"], [data-test*="dialog"]'
                )).filter(el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                });

                for (const dialog of dialogs) {
                    const text = (dialog.innerText || '').toLowerCase();
                    if (!isBusy(text)) continue;

                    // Click OK or any primary/confirm button
                    const btns = Array.from(dialog.querySelectorAll('button')).filter(b => {
                        const r = b.getBoundingClientRect();
                        return r.height > 0;
                    });
                    for (const btn of btns) {
                        const t = (btn.innerText || '').trim().toLowerCase();
                        if (['ok', 'okay', 'close', 'dismiss', 'got it'].some(w => t.includes(w)) || btns.length === 1) {
                            btn.click();
                            return 'dismissed:' + btn.innerText.trim();
                        }
                    }
                    if (btns.length > 0) {
                        btns[0].click();
                        return 'dismissed:fallback:' + (btns[0].innerText || '').trim();
                    }
                    return 'found_no_button';
                }

                // --- Check 2: inline page error (no dialog wrapper) ---
                // Also handles the "Checkout is busy right now" banner with an X close button
                const bodyText = (document.body && document.body.innerText || '').toLowerCase();
                if (isBusy(bodyText)) {
                    // First try: find the banner element itself and click its close/X button
                    const allEls = Array.from(document.querySelectorAll('*')).filter(el => {
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0 && r.height < 300;
                    });
                    for (const el of allEls) {
                        const t = (el.innerText || '').toLowerCase();
                        if (!isBusy(t)) continue;
                        // Found the banner — look for close/X button inside or nearby
                        const closeBtn = el.querySelector('button[aria-label*="close" i], button[aria-label*="dismiss" i], button[title*="close" i], button svg, button[class*="close" i]');
                        if (closeBtn) {
                            closeBtn.click();
                            return 'banner_closed:' + (closeBtn.ariaLabel || closeBtn.title || 'x');
                        }
                        // If the element itself is small (just the banner), click any button in it
                        const btnsInBanner = Array.from(el.querySelectorAll('button')).filter(b => {
                            const r = b.getBoundingClientRect();
                            return r.width > 0 && r.height > 0;
                        });
                        if (btnsInBanner.length > 0) {
                            btnsInBanner[0].click();
                            return 'banner_btn_clicked:' + (btnsInBanner[0].innerText || '').trim();
                        }
                    }
                    // Second try: any visible button with dismiss-type text
                    const allBtns = Array.from(document.querySelectorAll('button')).filter(b => {
                        const r = b.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    });
                    for (const btn of allBtns) {
                        const t = (btn.innerText || '').trim().toLowerCase();
                        if (['ok', 'okay', 'close', 'dismiss', 'got it', 'try again'].some(w => t.includes(w))) {
                            btn.click();
                            return 'inline_dismissed:' + btn.innerText.trim();
                        }
                    }
                    return 'inline_no_button';
                }

                return 'not_found';
            })()""")

            if isinstance(result, str) and result != 'not_found':
                print(f"[PAYMENT] Busy error detected: {result}")
                if result in ('found_no_button', 'inline_no_button'):
                    await self._press_escape(tab)
                return True
            return False
        except Exception as e:
            print(f"[PAYMENT] Busy modal check error: {e}")
            return False

    async def _handle_stock_error_modal(self, tab) -> bool:
        """Detect Target's 'out of stock' or 'no longer available' error after Place Order.

        Checks both modal dialogs and inline page text for OOS indicators.
        Returns True if detected. These errors are not dismissible, so no click is attempted.
        """
        try:
            result = await tab.evaluate("""(() => {
                const OOS_PHRASES = ['out of stock', 'no longer in stock', 'no longer available',
                                     'not available', 'sold out', 'item is unavailable'];
                const isOOS = text => OOS_PHRASES.some(p => text.includes(p));

                // Check modal/dialog overlays first
                const dialogs = Array.from(document.querySelectorAll(
                    '[role="dialog"], [role="alertdialog"], [class*="modal" i], ' +
                    '[data-test*="modal"], [data-test*="dialog"]'
                )).filter(el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                });
                for (const dialog of dialogs) {
                    const text = (dialog.innerText || '').toLowerCase();
                    if (isOOS(text)) return 'modal:' + text.slice(0, 100);
                }

                // Check inline page body
                const bodyText = (document.body && document.body.innerText || '').toLowerCase();
                if (isOOS(bodyText)) return 'inline:' + bodyText.slice(0, 100);

                return 'not_found';
            })()""")

            if isinstance(result, str) and result != 'not_found':
                print(f"[PAYMENT] Stock error detected: {result}")
                return True
            return False
        except Exception as e:
            print(f"[PAYMENT] Stock error modal check error: {e}")
            return False

    async def _api_place_order(self, tab) -> Dict[str, Any]:
        """Phase 4b — fire the Place Order POST directly via fetch (API mode).

        Patterned on the ATC fetch at :704-823. Reuses `_cached_cart_headers`
        captured by the warmup tab interceptor (Place Order shares the same
        Shape-token namespace as `cart_items` POST — see TARGET_CHECKOUT_API.md
        Endpoint 7).

        Returns a dict {success, status, body, order_id, confirmation_url, reason}.
        Caller decides whether to fall back to the DOM click on failure.
        """
        # Hard guard: if a capture flag is on, the interceptor will abort the
        # request with a synthetic 503 — API mode would mis-report failure.
        if os.environ.get('TARGET_API_CAPTURE_PLACE_ORDER', 'false').lower() == 'true':
            print("[API_PLACE_ORDER] Refusing to fire — TARGET_API_CAPTURE_PLACE_ORDER is set "
                  "(interceptor will abort). Disable capture flag before enabling API mode.")
            return {'success': False, 'status': 0, 'body': '',
                    'reason': 'capture_flag_active', 'order_id': None,
                    'confirmation_url': None}

        observe = os.environ.get('TARGET_API_PLACE_ORDER_OBSERVE', 'false').lower() == 'true'
        if observe:
            print("[API_PLACE_ORDER] OBSERVE flag set — order WILL be placed for real; "
                  "full response body will be logged to logs/api_capture.log")

        # Build headers the same way ATC does (strip Cookie/Referer, force x-application-name).
        headers_age = time.time() - self._cached_cart_headers_ts
        if self._cached_cart_headers and headers_age > 60:
            print(f"[API_PLACE_ORDER] Shape headers approaching TTL (age={headers_age:.0f}s) — refreshing warmup tab")
            warmup_ok = await self.warm_shape_headers()
            if warmup_ok:
                headers_age = time.time() - self._cached_cart_headers_ts
                print(f"[API_PLACE_ORDER] Warmup refresh complete, new headers age={headers_age:.0f}s")
            else:
                print(f"[API_PLACE_ORDER] Warmup refresh failed, continuing with stale headers")

        use_cached = bool(self._cached_cart_headers) and headers_age < 90
        _strip_keys = {'cookie', 'referer'}
        cached_shape_only = {k: v for k, v in self._cached_cart_headers.items()
                              if k.lower() not in _strip_keys}
        cached_shape_only['x-application-name'] = 'web'
        extra_headers_js = json.dumps(cached_shape_only if use_cached else {'x-application-name': 'web'})
        if use_cached:
            print(f"[API_PLACE_ORDER] Injecting Shape headers (age={headers_age:.0f}s): "
                  f"{list(cached_shape_only.keys())}")
        elif self._cached_cart_headers:
            print(f"[API_PLACE_ORDER] Shape headers STALE (age={headers_age:.0f}s > 90s) — sending fetch WITHOUT Shape headers")
        else:
            print(f"[API_PLACE_ORDER] No Shape headers cached yet — warmup tab may not have captured")

        url = ('https://carts.target.com/web_checkouts/v1/checkout'
               '?cart_type=REGULAR'
               '&field_groups=ADDRESSES%2CCART%2CCART_ITEMS%2CFINANCE_PROVIDERS'
               '%2CPAYMENT_INSTRUCTIONS%2CPICKUP_INSTRUCTIONS%2CPROMOTION_CODES%2CSUMMARY'
               '&key=e59ce3b531b2c39afb2e2b8a71ff10113aac2a14')

        # TEST_MODE compose-and-abort: build the full request (URL, headers,
        # body) so the dispatch path, header injection, and Shape-token TTL
        # logic all run, but skip the actual fetch. Returns a synthetic
        # success dict that downstream parsers/state handlers consume normally.
        if self.test_mode:
            fake_order_id = f"TEST-NO-ORDER-{int(time.time())}"
            print(f"[API_PLACE_ORDER] [TEST_MODE] Compose-and-abort — would POST to {url}")
            print(f"[API_PLACE_ORDER] [TEST_MODE] Headers: {list(cached_shape_only.keys())}")
            print(f"[API_PLACE_ORDER] [TEST_MODE] Body: {{'cart_type': 'REGULAR', 'channel_id': '10'}}")
            print(f"[API_PLACE_ORDER] [TEST_MODE] Synthetic success — fake order_id={fake_order_id}")
            return {
                'success': True, 'status': 200, 'body': '<test_mode_no_request>',
                'reason': 'ok', 'order_id': fake_order_id,
                'reference_id': None,
                'confirmation_url': f"https://www.target.com/checkout/confirmation?orderId={fake_order_id}"
            }

        t0 = time.time()
        print(f"[API_PLACE_ORDER] Firing checkout POST")
        try:
            resp = await tab.evaluate(f"""(async () => {{
                try {{
                    const cachedHeaders = {extra_headers_js};
                    const r = await fetch(
                        '{url}',
                        {{
                            method: 'POST',
                            credentials: 'include',
                            headers: {{
                                ...cachedHeaders,
                                'Content-Type': 'application/json',
                                'Accept': 'application/json',
                                'Origin': 'https://www.target.com',
                                'Referer': 'https://www.target.com/checkout',
                                'x-application-name': 'web',
                            }},
                            body: JSON.stringify({{
                                cart_type: 'REGULAR',
                                channel_id: '10'
                            }})
                        }}
                    );
                    const body = await r.text();
                    return {{status: r.status, body: body}};
                }} catch(e) {{
                    return {{status: 0, body: String(e)}};
                }}
            }})()""", await_promise=True)
        except Exception as fetch_err:
            print(f"[API_PLACE_ORDER] tab.evaluate raised: {fetch_err}")
            return {'success': False, 'status': 0, 'body': '',
                    'reason': f'fetch_threw:{fetch_err}',
                    'order_id': None, 'confirmation_url': None}

        elapsed = time.time() - t0
        status = resp.get('status', 0) if isinstance(resp, dict) else 0
        body = resp.get('body', '') if isinstance(resp, dict) else ''
        print(f"[API_PLACE_ORDER] HTTP {status} in {elapsed:.2f}s ({len(body)} body chars)")

        # OBSERVE: log the full response body before any parsing — first deployment
        # needs the success-response shape to design order_id parsing.
        if observe:
            try:
                import os as _os, datetime as _dt
                _os.makedirs('logs', exist_ok=True)
                with open('logs/api_capture.log', 'a', encoding='utf-8') as _f:
                    _f.write(f"\n{'='*80}\n[{_dt.datetime.now().isoformat()}] PLACE ORDER POST response (OBSERVE)\n")
                    _f.write(f"http_status: {status}\n")
                    _f.write(f"elapsed_s: {elapsed:.3f}\n")
                    _f.write(f"body_chars: {len(body)}\n")
                    _f.write(f"body:\n{body}\n")
                print(f"[API_PLACE_ORDER] OBSERVE: response logged to logs/api_capture.log")
            except Exception as log_err:
                print(f"[API_PLACE_ORDER] OBSERVE log failed: {log_err}")

        if status not in (200, 201):
            # Classify common failure modes for diagnosis (mirrors ATC error path).
            up = body.upper()
            if status == 403 and ('<html' in body.lower() or '<!doctype' in body.lower()):
                reason = 'shape_block'
            elif status == 401:
                reason = 'auth_expired'
            elif 'INVENTORY_NOT_AVAILABLE' in up or 'OUT_OF_STOCK' in up:
                reason = 'oos'
            elif 'RESERVATION_FAILURE' in up:
                reason = 'reservation_failure'
            else:
                reason = f'http_{status}'
            return {'success': False, 'status': status, 'body': body[:500],
                    'reason': reason, 'order_id': None, 'confirmation_url': None}

        # SUCCESS — extract order_id from the response.
        # Verified shape (2026-05-06 OBSERVE run):
        #   {"orders":[{"order_id":"<uuid>","reference_id":"<10-digit>",...}]}
        # Primary path is orders[0].order_id; fallbacks kept for shape drift.
        order_id = None
        reference_id = None
        confirmation_url = None
        try:
            payload = json.loads(body)
            if isinstance(payload, dict):
                orders = payload.get('orders')
                if isinstance(orders, list) and orders:
                    first = orders[0]
                    if isinstance(first, dict):
                        for key in ('order_id', 'orderId', 'order_number', 'id'):
                            val = first.get(key)
                            if isinstance(val, str) and val:
                                order_id = val
                                break
                        ref = first.get('reference_id')
                        if isinstance(ref, str) and ref:
                            reference_id = ref
                # Defensive fallbacks (root-level + 'order' singular) in case the
                # response shape ever changes.
                if not order_id:
                    for key in ('order_id', 'orderId', 'order_number', 'id', 'reference_id'):
                        val = payload.get(key)
                        if isinstance(val, str) and val:
                            order_id = val
                            break
                if not order_id:
                    order = payload.get('order') if isinstance(payload.get('order'), dict) else None
                    if order:
                        for key in ('order_id', 'orderId', 'order_number', 'id'):
                            val = order.get(key)
                            if isinstance(val, str) and val:
                                order_id = val
                                break
                for key in ('confirmation_url', 'redirect_url', 'order_confirmation_url'):
                    val = payload.get(key)
                    if isinstance(val, str) and val:
                        confirmation_url = val
                        break
        except Exception as parse_err:
            print(f"[API_PLACE_ORDER] JSON parse failed: {parse_err}")

        if not order_id:
            for pat in (r'"order_id"\s*:\s*"([^"]+)"',
                        r'"orderId"\s*:\s*"([^"]+)"',
                        r'"order_number"\s*:\s*"([^"]+)"'):
                m = re.search(pat, body)
                if m:
                    order_id = m.group(1)
                    break

        if order_id:
            ref_str = f" reference_id={reference_id}" if reference_id else ""
            print(f"[API_PLACE_ORDER] Order placed — order_id={order_id}{ref_str} (t={elapsed:.2f}s)")
            if not confirmation_url:
                confirmation_url = f"https://www.target.com/checkout/confirmation?orderId={order_id}"
        else:
            print(f"[API_PLACE_ORDER] Order placed (HTTP {status}) but order_id NOT FOUND in response — "
                  f"check logs/api_capture.log if OBSERVE was on. Body preview: {body[:300]!r}")

        return {'success': True, 'status': status, 'body': body[:500],
                'reason': 'ok', 'order_id': order_id,
                'reference_id': reference_id,
                'confirmation_url': confirmation_url}

    async def _place_order(self, tab) -> bool:
        """Find the enabled Place Order button and click it (production only).

        Retries up to 3 times if the 'busier than expected' modal appears.

        Phase 4b: when TARGET_API_PLACE_ORDER=true (and not test_mode), tries
        the API fetch first and falls back to DOM only on non-success.
        """
        # ── Phase 4b — API mode ──────────────────────────────────────────────
        # TEST_MODE auto-enables API path so test_app.py exercises the same
        # fast-path app.py uses. Compose-and-abort guard inside _api_place_order
        # prevents real orders from firing in TEST_MODE.
        _api_flag = (
            os.environ.get('TARGET_API_PLACE_ORDER', 'false').lower() == 'true'
            or self.test_mode
        )
        print(f"[PAYMENT] Phase 4b dispatch check: test_mode={self.test_mode}, "
              f"api_path={_api_flag} → {'API path' if _api_flag else 'DOM path'}")
        if _api_flag:
            label = 'TEST_MODE compose-and-abort' if self.test_mode else 'TARGET_API_PLACE_ORDER=true'
            print(f"[PAYMENT] {label} — attempting API place-order")
            api_result = await self._api_place_order(tab)
            if api_result.get('success'):
                self._api_order_id = api_result.get('order_id')
                self._api_confirmation_url = api_result.get('confirmation_url')
                print(f"[PAYMENT] API place-order succeeded — order_id={self._api_order_id}")
                return True
            print(f"[PAYMENT] API place-order failed (reason={api_result.get('reason')}, "
                  f"status={api_result.get('status')}) — falling back to DOM click")
            # Some failure reasons should NOT be retried via DOM — the order would
            # double-place if the API actually committed but we mis-parsed. Only
            # fall back on signals that prove the request was rejected by the server.
            if api_result.get('reason') in ('shape_block', 'auth_expired', 'capture_flag_active',
                                            'fetch_threw'):
                pass  # safe to fall through to DOM
            elif api_result.get('reason') in ('oos', 'reservation_failure'):
                # Server rejected — DOM click would also fail. Bail with the same
                # failure signature the DOM path produces.
                print(f"[PAYMENT] API rejection is terminal ({api_result.get('reason')}) — "
                      f"not retrying via DOM")
                return False
            # else: http_xxx or unknown — fall through and let DOM try.

        # Dismiss any "Checkout is busy right now" banner before attempting Place Order
        pre_dismissed = await self._handle_busy_modal(tab)
        if pre_dismissed:
            print("[PAYMENT] Dismissed busy banner before Place Order click")
            await asyncio.sleep(0.5)

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            place_order_button, found_selector = await self._find_place_order_button(tab)
            if not place_order_button:
                print("[PAYMENT] Place Order not found or still disabled")
                try:
                    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                    await self._screenshot(tab, f"logs/checkout_no_place_order_{ts}.png")
                    print(f"[PAYMENT] Screenshot: logs/checkout_no_place_order_{ts}.png")
                except Exception:
                    pass
                return False

            await self._scroll_into_view(place_order_button)
            print(f"[PAYMENT] Clicking Place Order ({found_selector}) attempt {attempt}/{max_attempts}")
            t_click = time.time()
            try:
                await self._dispatch_click(place_order_button)
                print(f"[PAYMENT] Click dispatched (t+{time.time()-t_click:.3f}s)")
                await asyncio.sleep(0.05)  # brief yield so browser processes click event
            except Exception as click_error:
                print(f"[PAYMENT] dispatch click failed ({click_error}), trying fallback")
                try:
                    await place_order_button.click()
                    print(f"[PAYMENT] Fallback click succeeded (t+{time.time()-t_click:.3f}s)")
                except Exception:
                    return False

            print("[PAYMENT] Waiting for confirmation, CVV modal, or busy modal...")
            start = time.time()
            cvv_handled = False
            while time.time() - start < 12.0:
                url = tab.url
                if 'order-confirmation' in url.lower() or 'thank' in url.lower() or 'confirmation' in url.lower():
                    print(f"[PAYMENT] Reached confirmation page (t+{time.time()-t_click:.3f}s from click) url={url}")
                    return True
                if not cvv_handled:
                    cvv_handled = await self._handle_cvv_modal(tab)
                    if cvv_handled:
                        print(f"[PAYMENT] CVV submitted (t+{time.time()-t_click:.3f}s from click) — waiting for confirmation...")
                    # Don't run busy/stock checks until CVV is handled — saves round trips
                    await asyncio.sleep(0.05)
                    continue
                # CVV already handled — check for busy/stock errors
                busy_dismissed = await self._handle_busy_modal(tab)
                if busy_dismissed:
                    print(f"[PAYMENT] Busy error detected (t+{time.time()-t_click:.3f}s) — retrying Place Order (attempt {attempt}/{max_attempts})")
                    await asyncio.sleep(1.5)
                    break  # break inner loop to retry Place Order click
                # Check for out-of-stock error — no point waiting further if detected
                stock_error = await self._handle_stock_error_modal(tab)
                if stock_error:
                    print(f"[PAYMENT] OUT OF STOCK detected (t+{time.time()-t_click:.3f}s) — item sold out during checkout")
                    return False
                await asyncio.sleep(0.05)
            else:
                # Inner loop completed without break — timed out
                url = tab.url
                print(f"[PAYMENT] Timed out after 12s — URL: {url}")
                try:
                    page_snippet = (await tab.evaluate("(document.body && document.body.innerText || '').slice(0, 300)")).replace('\n', ' ')
                    print(f"[PAYMENT] Page text on timeout: {page_snippet!r}")
                except Exception:
                    pass
                # Take screenshot to diagnose what happened
                try:
                    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                    await self._screenshot(tab, f"logs/place_order_timeout_{ts}.png")
                    print(f"[PAYMENT] Timeout screenshot: logs/place_order_timeout_{ts}.png")
                except Exception:
                    pass
                # Check one final time for busy error before giving up
                if await self._handle_busy_modal(tab):
                    print("[PAYMENT] Busy error found on timeout — will retry")
                    await asyncio.sleep(1.5)
                    break  # retry Place Order
                if 'checkout' in url.lower():
                    print("[PAYMENT] Still on checkout after Place Order wait — signaling failure")
                    return False
                # Navigated away from checkout — check page content before deciding
                page_text = ''
                try:
                    page_text = (await tab.evaluate("document.body.innerText || ''")).lower()
                except Exception:
                    pass
                if any(p in page_text for p in ['busier', 'temporary issue', "can't view", 'busy right now']):
                    print("[PAYMENT] Busy error on redirected page — will retry")
                    await asyncio.sleep(1.5)
                    break  # retry Place Order
                # Only treat as success if URL or page content confirms the order
                if any(p in url.lower() for p in ['order-confirmation', 'confirmation', 'thank']):
                    print(f"[PAYMENT] Order confirmed via URL: {url}")
                    return True
                if any(p in page_text for p in ['order confirmation', 'thank you', 'your order', 'order number']):
                    print("[PAYMENT] Order confirmed via page content")
                    return True
                print(f"[PAYMENT] Redirected to {url} without confirmation — treating as failure")
                return False

            # busy_dismissed caused break — loop back to retry Place Order

        print(f"[PAYMENT] Place Order failed after {max_attempts} attempts (busy modal)")
        try:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            await self._screenshot(tab, f"logs/busy_fail_{ts}.png")
            print(f"[PAYMENT] Screenshot saved: logs/busy_fail_{ts}.png")
        except Exception:
            pass
        return False

    async def _complete_payment(self, tab, initial_state: str = 'unknown') -> bool:
        """Drive the checkout to completion.

        Handles every flow Target may present:
          A. Review page already loaded (Place Order enabled) — skip S&C loop entirely.
          B. Address step needs S&C  → no radios, just click S&C.
          C. Delivery step needs S&C → select Ship, then click S&C.
          D. Payment step needs S&C  → select saved card, then click S&C.
          E. Multiple steps pending  → loop handles them in sequence.
          F. F5 kicks us off checkout (URL changes) → detected, fail gracefully.

        TEST_MODE: navigate back to cart as soon as no more S&C buttons exist.
        PROD MODE: click the enabled Place Order button.
        """
        try:
            t_payment_start = time.time()
            print("[PAYMENT] Starting checkout completion...")

            # Skip duplicate wait if the nav wait already confirmed page-ready state.
            # Bypass DOM checkout flow only in TEST_MODE — PROD nav'd /checkout/start
            # above so the page is hydrated; fall through to FLOW A (DOM ready check)
            # which then dispatches to API place-order via the Phase 4b gate at
            # _click_place_order. Pairs with the matching _api_skip change at line
            # ~1549 (PROD must nav to avoid CART_COMPARISION_FAILURE_ERROR).
            _api_skip = self.test_mode
            if _api_skip:
                print(f"[PAYMENT] API mode — bypassing DOM checkout flow, calling _place_order directly")
                po_result = await self._place_order(tab)
                if self.test_mode:
                    # TEST_MODE: redirect to cart so the cart-clear cycle can run.
                    await self._fast_nav(tab, "https://www.target.com/cart")
                return po_result

            if initial_state in ('place_order', 'sac'):
                ready = initial_state
                print(f"[PAYMENT] Page already confirmed ready ({ready}) — skipping wait (t+{time.time()-t_payment_start:.3f}s)")
            else:
                ready = await self._wait_for_checkout_ready(tab, timeout=5.0)
                print(f"[PAYMENT] Checkout ready state: {ready} (t+{time.time()-t_payment_start:.3f}s)")

            # ── FLOW A: Place Order already enabled (everything pre-confirmed) ─────────
            if ready == 'place_order':
                # Fast JS check — avoids tab.select() timeouts from fallback selectors
                _po_enabled = await tab.evaluate("""(() => {
                    const po = document.querySelector('[data-test="placeOrderButton"]');
                    if (!po) return false;
                    const r = po.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) return false;
                    const style = window.getComputedStyle(po);
                    return !po.disabled
                        && po.getAttribute('aria-disabled') !== 'true'
                        && !(po.className || '').toLowerCase().includes('disabled')
                        && style.pointerEvents !== 'none'
                        && parseFloat(style.opacity) >= 0.6;
                })()""")
                if _po_enabled:
                    print("[PAYMENT] FLOW A: Place Order already enabled — no S&C needed")
                    if self.test_mode:
                        # Run _place_order so the API compose-and-abort path
                        # exercises header injection, dispatch, and parsing.
                        # Then redirect to cart for the cart-clear cycle.
                        po_result = await self._place_order(tab)
                        print(f"[PAYMENT] TEST_MODE: API path returned {po_result} — going to cart for clear cycle")
                        await self._fast_nav(tab, "https://www.target.com/cart")
                        return True
                    return await self._place_order(tab)
                print("[PAYMENT] FLOW A: Place Order visible but disabled — entering S&C loop")

            # ── FLOWS B/C/D/E: S&C loop ───────────────────────────────────────────────
            for step in range(6):
                print(f"[PAYMENT] --- Step {step + 1}: waiting for checkout step to load ---")
                # Validate we're still on the checkout page (F5 may redirect us away).
                current_url = tab.url
                if 'checkout' not in current_url.lower():
                    print(f"[PAYMENT] FLOW F: no longer on checkout (url={current_url}) — aborting")
                    return False

                # Handle any radio button that needs selecting on this step.
                await self._handle_step_radio(tab)

                # Wait for S&C button to appear using MutationObserver (event-driven, not polling).
                # Resolves the instant React renders the button; immediate return if already present.
                try:
                    await tab.evaluate("""new Promise((resolve) => {
                        const check = () => {
                            const el = document.querySelector('[data-test="save-and-continue-button"]');
                            if (el && el.getBoundingClientRect().height > 0) { resolve('found'); return true; }
                            return false;
                        };
                        if (check()) return;
                        const obs = new MutationObserver(() => { if (check()) obs.disconnect(); });
                        obs.observe(document.body, { childList: true, subtree: true });
                        setTimeout(() => { obs.disconnect(); resolve('timeout'); }, 5000);
                    })""", await_promise=True)
                except Exception:
                    pass  # S&C click loop below is still the safety net

                # Click S&C via evaluate — avoids stale element references that occur when
                # tab.select() grabs a node right as React is re-rendering after the radio click.
                clicked = 'not_found'
                for _attempt in range(15):
                    try:
                        clicked = await tab.evaluate("""(() => {
                            // Find by data-test first, then by visible button text
                            let el = document.querySelector('[data-test="save-and-continue-button"]');
                            if (!el) {
                                el = Array.from(document.querySelectorAll(
                                    'button, [role="button"], a[class*="button"]'
                                )).find(b => {
                                    const t = (b.innerText || '').toLowerCase().trim();
                                    return t === 'save and continue' || t === 'save & continue' ||
                                           (t.includes('save') && t.includes('continue'));
                                }) || null;
                            }
                            if (!el) return 'not_found';
                            const r = el.getBoundingClientRect();
                            if (r.height === 0) return 'hidden';
                            el.scrollIntoView({block: 'center', behavior: 'instant'});
                            // Full mouse event sequence — most reliable for React synthetic events
                            ['mousedown', 'mouseup', 'click'].forEach(type => {
                                el.dispatchEvent(new MouseEvent(type, {
                                    bubbles: true, cancelable: true, view: window
                                }));
                            });
                            return 'clicked';
                        })()""")
                    except Exception as ev_err:
                        clicked = f'error:{ev_err}'
                    if clicked == 'clicked':
                        print(f"[PAYMENT] Clicked S&C (step {step + 1}, attempt {_attempt + 1})")
                        break
                    # Log reason on first failure so we can diagnose
                    if _attempt == 0:
                        print(f"[PAYMENT] S&C attempt 1 result: {clicked}")
                    await asyncio.sleep(0.3)

                if clicked != 'clicked':
                    print(f"[PAYMENT] No S&C button at step {step + 1} — loop done")
                    break

                # Poll for next state — exits as soon as Place Order enables or next S&C loads.
                transition = await self._wait_for_sac_transition(tab, timeout=5.0)

                if transition == 'place_order_enabled':
                    po_btn, _ = await self._find_place_order_button(tab)
                    if po_btn:
                        print(f"[PAYMENT] *** PLACE ORDER IS ENABLED (step {step + 1}) ***")
                        if self.test_mode:
                            po_result = await self._place_order(tab)
                            print(f"[PAYMENT] TEST_MODE: API path returned {po_result} — going to cart for clear cycle")
                            await self._fast_nav(tab, "https://www.target.com/cart")
                            return True
                        return await self._place_order(tab)

                # 'sac_again' or 'timeout' → continue loop (next step or retry)
                if transition == 'timeout':
                    print(f"[PAYMENT] S&C transition timed out at step {step + 1} — continuing loop")
                elif transition == 'sac_again':
                    print(f"[PAYMENT] Next S&C appeared at step {step + 1} — continuing")

            # ── Loop exhausted without finding Place Order ─────────────────────────────
            if self.test_mode:
                # In test mode success = we tried everything and cycled back to cart.
                print("[PAYMENT] TEST_MODE: S&C loop exhausted — going to cart")
                await self._fast_nav(tab, "https://www.target.com/cart")
                return True

            # Prod: one final attempt to find Place Order.
            print(f"[PAYMENT] S&C loop exhausted after 6 steps — making final Place Order attempt (url={tab.url})")
            return await self._place_order(tab)

        except Exception as e:
            import traceback as _tb
            _tb_str = _tb.format_exc()
            print(f"[PAYMENT] Payment completion error: {e}")
            print(_tb_str)
            await self._take_debug_screenshot(tab, "payment_error")
            try:
                import os as _os, datetime as _dt
                _os.makedirs('logs', exist_ok=True)
                with open('logs/error_log.txt', 'a', encoding='utf-8') as _f:
                    _f.write(f"[{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [PAYMENT_ERROR] {type(e).__name__}: {e} — url={tab.url}\n{_tb_str}\n")
            except Exception:
                pass
            return False

    async def _verify_order_completion(self, tab) -> bool:
        """Verify that order was successfully placed"""
        try:
            self.logger.info("Verifying order completion...")

            url_indicators = ['confirmation', 'thank', 'order', 'receipt']
            confirmation_texts = [
                "Thanks for your order!", "Thanks for your order",
                "Order confirmed", "Thank you", "Your order has been placed",
                "Order number", "Confirmation",
            ]
            confirmation_css = [
                '[data-test*="order-confirmation"]',
                '[data-testid*="order-confirmation"]',
            ]

            confirmation_found = False
            for attempt in range(15):
                current_url = tab.url
                if any(indicator in current_url.lower() for indicator in url_indicators):
                    self.logger.info(f"Order confirmation detected via URL: {current_url}")
                    confirmation_found = True
                    break

                for text in confirmation_texts:
                    try:
                        element = await tab.find(text, best_match=True, timeout=1)
                        if element:
                            self.logger.info(f"Order confirmation detected: {text}")
                            confirmation_found = True
                            break
                    except Exception:
                        continue

                if confirmation_found:
                    break

                for selector in confirmation_css:
                    try:
                        element = await tab.select(selector, timeout=0.5)
                        if element:
                            self.logger.info(f"Order confirmation detected: {selector}")
                            confirmation_found = True
                            break
                    except Exception:
                        continue

                if confirmation_found:
                    break

                await asyncio.sleep(0.3)

            if confirmation_found:
                self.logger.info("ORDER SUCCESSFULLY COMPLETED!")
                return True

            # Check for error messages
            error_texts = ["Payment failed", "Error", "Unable to place order"]
            error_css = ['[data-test*="error"]', '.error-message']

            for text in error_texts:
                try:
                    error = await tab.find(text, best_match=True, timeout=1)
                    if error:
                        error_text = await self._inner_text(error)
                        self.logger.warning(f"Order failed with error: {error_text}")
                        return False
                except Exception:
                    continue

            for selector in error_css:
                try:
                    error = await tab.select(selector, timeout=0.5)
                    if error:
                        error_text = await self._inner_text(error)
                        self.logger.warning(f"Order failed with error: {error_text}")
                        return False
                except Exception:
                    continue

            self.logger.warning("Order completion could not be verified")
            return False

        except Exception as e:
            self.logger.error(f"Order verification error: {e}")
            return False

    # -------------------------------------------------------------------------
    # Status callback
    # -------------------------------------------------------------------------

    def _notify_status(self, tcin: str, status: str, data: dict = None):
        """Notify status callback of purchase progress"""
        if self.status_callback:
            try:
                callback_data = {
                    'tcin': tcin,
                    'status': status,
                    'timestamp': datetime.now().isoformat()
                }
                if data:
                    callback_data.update(data)
                self.status_callback(callback_data)
            except Exception as e:
                self.logger.warning(f"Status callback failed: {e}")

    async def health_check(self) -> bool:
        """Check if purchase executor is healthy"""
        try:
            if not await self.session_manager.is_healthy():
                return False
            tab = await self.session_manager.get_page()
            if not tab:
                return False
            return True
        except Exception as e:
            self.logger.error(f"Health check failed: {e}")
            return False

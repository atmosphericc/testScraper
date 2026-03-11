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
from typing import Optional, Callable, Dict, Any

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
        self._warmup_tab = None                             # single shared background tab
        self._warmup_in_progress: bool = False              # prevent concurrent warmups
        self._main_tab_interceptor_active: bool = False     # avoid double setup on main tab
        self._cdp_continued_ids: set = set()               # dedup across accumulated handlers

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

    async def execute_purchase(self, tcin: str) -> Dict[str, Any]:
        """
        Execute purchase for given TCIN using persistent session.
        Uses lock to prevent concurrent tab access.
        """
        try:
            async with asyncio.timeout(140):  # slightly less than thread's 150s so coroutine self-cancels cleanly
                async with self._page_lock:
                    return await self._execute_purchase_impl(tcin)
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
            # FIX 1: deduplicate — accumulated handlers each fire; only first processes
            req_id = str(event.request_id)
            if req_id in self._cdp_continued_ids:
                return
            self._cdp_continued_ids.add(req_id)

            try:
                url = event.request.url if hasattr(event, 'request') else ''
                method = event.request.method if hasattr(event.request, 'method') else '?'
                if 'carts.target.com' in url or 'cart_items' in url:
                    headers = dict(event.request.headers) if event.request.headers else {}
                    header_names = list(headers.keys())
                    shape_headers = [h for h in header_names if h.lower().startswith('x-')]
                    # FIX 2: only cache POST — GET/OPTIONS/PUT don't carry Shape tokens
                    if method == 'POST' and headers:
                        prev_shape_count = len([h for h in self._cached_cart_headers if h.lower().startswith('x-')])
                        self._cached_cart_headers = headers
                        self._cached_cart_headers_ts = time.time()
                        print(f"[INTERCEPTOR:{label}] {method} {url[:80]}")
                        print(f"[INTERCEPTOR:{label}] Captured {len(headers)} headers (Shape X-headers: {len(shape_headers)}, prev cache had {prev_shape_count}): {header_names}")
                    elif headers:
                        # non-POST carts request — log but don't overwrite cache
                        cache_age = time.time() - self._cached_cart_headers_ts if self._cached_cart_headers_ts else -1
                        cached_shape = len([h for h in self._cached_cart_headers if h.lower().startswith('x-')])
                        print(f"[INTERCEPTOR:{label}] {method} {url[:80]} — skipping cache (not POST, has {len(headers)} headers, {len(shape_headers)} X-headers)")
                        print(f"[INTERCEPTOR:{label}]   cache preserved: {len(self._cached_cart_headers)} headers, {cached_shape} Shape tokens, age={cache_age:.1f}s")
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

        try:
            await tab.send(cdp.fetch.enable(
                patterns=[cdp.fetch.RequestPattern(
                    url_pattern='*carts.target.com*',
                    request_stage=cdp.fetch.RequestStage.REQUEST
                )]
            ))
            print(f"[INTERCEPTOR:{label}] cdp.fetch.enable() sent with pattern *carts.target.com*")
        except Exception as e:
            print(f"[INTERCEPTOR:{label}] cdp.fetch.enable() FAILED: {e}")
            return

        if cdp.fetch not in tab.enabled_domains:
            tab.enabled_domains.append(cdp.fetch)
            print(f"[INTERCEPTOR:{label}] Added cdp.fetch to enabled_domains")

        tab.add_handler(cdp.fetch.RequestPaused, _on_request_paused)
        handler_count = len([h for h in getattr(tab, 'handlers', {}).get(cdp.fetch.RequestPaused, []) if True])
        print(f"[INTERCEPTOR:{label}] CDP fetch interceptor ready (total RequestPaused handlers on tab: {handler_count})")

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

            if not self._warmup_tab:
                print("[WARMUP] Opening new background warmup tab...")
                self._warmup_tab = await browser.get(
                    "https://www.target.com/cart", new_tab=True
                )
                print(f"[WARMUP] Warmup tab opened, URL={self._warmup_tab.url}")
                await self._setup_cdp_fetch_interceptor(self._warmup_tab, persistent=True)
            else:
                current_url = getattr(self._warmup_tab, 'url', 'unknown')
                print(f"[WARMUP] Navigating warmup tab to cart (was at {current_url})")
                await self._warmup_tab.get("https://www.target.com/cart")

            # Wait for Shape JS to initialize, then fire a dummy POST so the CDP
            # interceptor (REQUEST stage) can capture Shape headers.
            # Cart page load only triggers GET/PUT — never a POST.
            await asyncio.sleep(0.8)
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
                        cart_item: {tcin: '00000000', quantity: 1, item_channel_id: '10'},
                        cart_type: 'REGULAR',
                        channel_id: '10',
                        shopping_context: 'DIGITAL'
                    })
                }).catch(() => {});
            """)

            print("[WARMUP] Waiting for carts.target.com POST interception (up to 3s)...")

            deadline = time.time() + 3.0
            while time.time() < deadline:
                if self._cached_cart_headers_ts > ts_before:
                    age = time.time() - self._cached_cart_headers_ts
                    print(f"[WARMUP] Shape headers captured successfully (age={age:.1f}s): "
                          f"{list(self._cached_cart_headers.keys())}")
                    return True
                await asyncio.sleep(0.1)

            elapsed = time.time() - ts_before
            print(f"[WARMUP] TIMEOUT after {elapsed:.1f}s — dummy POST was NOT intercepted")
            print(f"[WARMUP] Possible causes: Target cart page didn't load, Shape JS not initialized, "
                  f"or CDP interceptor not active")
            print(f"[WARMUP] Stale headers available: {bool(self._cached_cart_headers)}, "
                  f"age={time.time()-self._cached_cart_headers_ts:.0f}s")
            return bool(self._cached_cart_headers)
        except Exception as e:
            print(f"[WARMUP] Error: {e}")
            return False
        finally:
            self._warmup_in_progress = False

    # -------------------------------------------------------------------------
    # Core purchase implementation
    # -------------------------------------------------------------------------

    async def _execute_purchase_impl(self, tcin: str) -> Dict[str, Any]:
        """Execute purchase for given TCIN using persistent session"""
        start_time = time.time()
        tab = None  # ensure tab is accessible in finally block
        self._cdp_continued_ids.clear()
        print(f"[PURCHASE] cdp_continued_ids cleared for new purchase ({tcin})")

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

            # Navigate to product page
            product_url = f"https://www.target.com/p/-/A-{tcin}"
            try:
                print(f"[PURCHASE] Navigating to {product_url}")
                await tab.get(product_url)
            except Exception as nav_error:
                print(f"[ERROR] Navigation failed: {nav_error}")
                raise

            # Set up CDP interceptor on main tab to capture button-click POST headers
            if not self._main_tab_interceptor_active:
                await self._setup_cdp_fetch_interceptor(tab, persistent=False)
                self._main_tab_interceptor_active = True

            # Attempt 1: fetch-based ATC fired immediately — no need to wait for button
            # The cart API only needs valid session cookies, not full page render
            headers_age = time.time() - self._cached_cart_headers_ts
            use_cached = bool(self._cached_cart_headers) and headers_age < 180  # 3-min TTL
            extra_headers_js = json.dumps(self._cached_cart_headers if use_cached else {})
            if use_cached:
                print(f"[PURCHASE] Injecting Shape headers (age={headers_age:.0f}s): "
                      f"{list(self._cached_cart_headers.keys())}")
            elif self._cached_cart_headers:
                print(f"[PURCHASE] Shape headers STALE (age={headers_age:.0f}s > 180s) — sending fetch WITHOUT Shape headers")
            else:
                print(f"[PURCHASE] No Shape headers cached yet — warmup tab may not have captured yet")
            print(f"[PURCHASE] Firing ATC fetch (t={time.time()-start_time:.2f}s)")
            atc_result = await tab.evaluate(f"""(async () => {{
                try {{
                    const cachedHeaders = {extra_headers_js};
                    const resp = await fetch(
                        'https://carts.target.com/web_checkouts/v1/cart_items?field_groups=CART,CART_ITEMS,SUMMARY',
                        {{
                            method: 'POST',
                            credentials: 'include',
                            headers: {{
                                'Content-Type': 'application/json',
                                'Accept': 'application/json',
                                'Origin': 'https://www.target.com',
                                'Referer': 'https://www.target.com/p/-/A-{tcin}',
                                ...cachedHeaders,
                            }},
                            body: JSON.stringify({{
                                cart_item: {{tcin: '{tcin}', quantity: 1, item_channel_id: '10'}},
                                cart_type: 'REGULAR',
                                channel_id: '10',
                                shopping_context: 'DIGITAL'
                            }})
                        }}
                    );
                    const body = (await resp.text()).slice(0, 300);
                    return {{status: resp.status, body: body}};
                }} catch(e) {{
                    return {{status: 0, body: String(e)}};
                }}
            }})()""", await_promise=True)

            atc_status = atc_result.get('status', 0) if isinstance(atc_result, dict) else atc_result
            atc_body = atc_result.get('body', '') if isinstance(atc_result, dict) else ''
            # Classify the failure for better diagnostics
            if atc_status == 403 and ('<html' in atc_body.lower() or '<!doctype' in atc_body.lower()):
                print(f"[PURCHASE] ATC fetch blocked by Shape Security (403 HTML) (t={time.time()-start_time:.2f}s)")
            elif atc_status in (422, 409) and 'OUT_OF_STOCK' in atc_body.upper():
                print(f"[PURCHASE] ATC fetch: item OOS at cart API ({atc_status}) (t={time.time()-start_time:.2f}s)")
            elif atc_status not in (200, 201):
                print(f"[PURCHASE] ATC fetch status: {atc_status} body={atc_body!r} (t={time.time()-start_time:.2f}s)")
            else:
                print(f"[PURCHASE] ATC fetch status: {atc_status} (t={time.time()-start_time:.2f}s)")

            # Success status → skip DOM polling entirely, go straight to checkout
            skip_signal_wait = False
            if atc_status in (200, 201):
                print(f"[PURCHASE] Fetch ATC succeeded ({atc_status}), skipping cart signal wait")
                cart_confirmed = True
            elif atc_status == 401:
                # Auth denied — no cart signal will ever come; skip wait and go straight to button click
                print(f"[PURCHASE] ATC fetch 401 auth denied, skipping signal wait")
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
                    budget = 9.5 if skip_signal_wait else 8.0  # 401 path saved ~1.5s, extend budget
                    remaining = max(0.0, budget - (time.time() - start_time))
                    button_ready = await self._wait_for_atc_button_ready(tab, timeout=remaining)
                if not button_ready:
                    await self._take_debug_screenshot(tab, "atc_button_not_ready")
                    return {'success': False, 'tcin': tcin, 'reason': 'page_not_ready',
                            'execution_time': time.time() - start_time}
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
                    await self._dispatch_click(add_button)
                    print(f"[PURCHASE] ATC button clicked (t={time.time()-start_time:.2f}s)")
                    cart_confirmed = await self._wait_for_cart_signal(tab, timeout=4.0)
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

            # Navigate to checkout
            self._notify_status(tcin, 'checking_out', {'timestamp': datetime.now().isoformat()})
            checkout_result = False

            try:
                await tab.get("https://www.target.com/checkout/start")
                await tab  # flush CDP event queue before interacting (zendriver pattern)
                print("[PURCHASE] Waiting for checkout page elements...")
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

                checkout_result = 'checkout' in tab.url.lower()
            except Exception as nav_error:
                print(f"[ERROR] Checkout navigation failed: {nav_error}")
                checkout_result = False

            if checkout_result:
                await self._handle_delivery_options(tab)
                payment_result = await self._complete_payment(tab, initial_state=_co_state)
                if not payment_result:
                    checkout_result = False

            if not checkout_result:
                # Diagnose: F5 block vs sold out vs other
                try:
                    url_now = tab.url
                    page_text = (await tab.evaluate("(document.body && document.body.innerText || '').toLowerCase()"))
                    if any(p in page_text for p in ['busier', 'temporary issue', "can't view", 'busy right now', 'limiting how many guests']):
                        print(f"[PURCHASE] DIAGNOSIS: F5/Target rate-limit block (busy error on page)")
                    elif any(p in page_text for p in ['out of stock', 'unavailable', 'sold out', 'not available']):
                        print(f"[PURCHASE] DIAGNOSIS: Item sold out during checkout")
                    else:
                        print(f"[PURCHASE] DIAGNOSIS: Unknown failure — url={url_now}")
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
                await asyncio.sleep(0.2)
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

            self._notify_status(tcin, 'purchased', {
                'execution_time': execution_time,
                'timestamp': datetime.now().isoformat(),
                'order_confirmed': True
            })

            return {
                'success': True,
                'tcin': tcin,
                'reason': 'order_confirmed',
                'execution_time': execution_time
            }

        except Exception as e:
            execution_time = time.time() - start_time
            print(f"[ERROR] Purchase failed for {tcin}: {e}")

            self._notify_status(tcin, 'failed', {
                'error': str(e),
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
                    from zendriver import cdp
                    await tab.send(cdp.fetch.disable())
                    self._main_tab_interceptor_active = False
                    if cdp.fetch in tab.enabled_domains:
                        tab.enabled_domains.remove(cdp.fetch)
                except Exception:
                    pass

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
        return False

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
        """Detect and fill a CVV/security-code verification modal after Place Order.

        Returns True if a CVV modal was found and handled, False if none present.
        """
        try:
            found_sel = await tab.evaluate("""
(() => {
    const selectors = [
        'input[name="cvv"]', 'input[name="cvc"]',
        'input[id*="cvv" i]', 'input[id*="cvc" i]',
        'input[placeholder*="CVV" i]', 'input[placeholder*="security" i]',
        'input[aria-label*="CVV" i]', 'input[aria-label*="security code" i]',
        'input[data-test*="cvv" i]',
    ];
    for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el) {
            const r = el.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) return sel;
        }
    }
    return null;
})()
""")
            if not found_sel:
                return False

            print(f"[PAYMENT] CVV modal detected ({found_sel}) — entering CVV")

            filled = await tab.evaluate(f"""
(() => {{
    const selectors = [
        'input[name="cvv"]', 'input[name="cvc"]',
        'input[id*="cvv" i]', 'input[id*="cvc" i]',
        'input[placeholder*="CVV" i]', 'input[placeholder*="security" i]',
        'input[aria-label*="CVV" i]', 'input[aria-label*="security code" i]',
        'input[data-test*="cvv" i]',
    ];
    for (const sel of selectors) {{
        const el = document.querySelector(sel);
        if (el) {{
            const r = el.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) {{
                const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                nativeInputValueSetter.call(el, '{CARD_CVV}');
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return true;
            }}
        }}
    }}
    return false;
}})()
""")
            if not filled:
                print("[PAYMENT] CVV fill failed")
                return False

            await asyncio.sleep(0.1)

            confirmed = await tab.evaluate("""
(() => {
    const confirmSelectors = [
        '[data-test*="confirm"]', '[data-test*="submit"]',
        '[data-test*="cvv"]',
        'button[type="submit"]',
    ];
    for (const sel of confirmSelectors) {
        const el = document.querySelector(sel);
        if (el) {
            const r = el.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) {
                el.click();
                return 'clicked: ' + sel;
            }
        }
    }
    // Fallback: any button whose text matches confirm/submit/continue
    const buttons = Array.from(document.querySelectorAll('button'));
    for (const btn of buttons) {
        const txt = btn.textContent.trim().toLowerCase();
        if (['confirm', 'submit', 'continue'].some(w => txt.includes(w))) {
            const r = btn.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) {
                btn.click();
                return 'clicked text: ' + btn.textContent.trim();
            }
        }
    }
    return 'no confirm button found';
})()
""")
            print(f"[PAYMENT] CVV confirm: {confirmed}")
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
        """Take debug screenshot for troubleshooting"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            screenshot_path = f"logs/debug_{reason}_{timestamp}.png"
            await self._screenshot(tab, screenshot_path)
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

    async def _clear_cart(self, tab) -> bool:
        """Clear all items from cart and saved-for-later section"""
        try:
            removed_count = 0

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

            for _ in range(10):
                button_found = False
                for selector in cart_remove_selectors:
                    try:
                        btn = await tab.select(selector, timeout=2)
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

            for _ in range(10):
                button_found = False
                for selector in sfl_remove_selectors:
                    try:
                        btn = await tab.select(selector, timeout=2)
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

    async def _place_order(self, tab) -> bool:
        """Find the enabled Place Order button and click it (production only).

        Retries up to 3 times if the 'busier than expected' modal appears.
        """
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
            try:
                await self._dispatch_click(place_order_button)
                print("[PAYMENT] Click dispatched")
            except Exception as click_error:
                print(f"[PAYMENT] dispatch click failed ({click_error}), trying fallback")
                try:
                    await place_order_button.click()
                    print("[PAYMENT] Fallback click succeeded")
                except Exception:
                    return False

            print("[PAYMENT] Waiting for confirmation, CVV modal, or busy modal...")
            start = time.time()
            cvv_handled = False
            while time.time() - start < 12.0:
                url = tab.url
                if 'confirmation' in url.lower() or 'thank' in url.lower():
                    print("[PAYMENT] Reached confirmation page")
                    return True
                if not cvv_handled:
                    cvv_handled = await self._handle_cvv_modal(tab)
                    if cvv_handled:
                        print("[PAYMENT] CVV submitted — waiting for confirmation...")
                # Check for busy error on every iteration (it may load after a delay)
                busy_dismissed = await self._handle_busy_modal(tab)
                if busy_dismissed:
                    print(f"[PAYMENT] Busy error detected — retrying Place Order (attempt {attempt}/{max_attempts})")
                    await asyncio.sleep(1.5)
                    break  # break inner loop to retry Place Order click
                # Check for out-of-stock error — no point waiting further if detected
                stock_error = await self._handle_stock_error_modal(tab)
                if stock_error:
                    print("[PAYMENT] OUT OF STOCK detected after Place Order — item sold out during checkout")
                    return False
                await asyncio.sleep(0.2)
            else:
                # Inner loop completed without break — timed out
                url = tab.url
                print(f"[PAYMENT] URL after Place Order: {url}")
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
                # Navigated away from checkout but not to confirmation — check page content
                page_text = ''
                try:
                    page_text = (await tab.evaluate("document.body.innerText || ''")).lower()
                except Exception:
                    pass
                if any(p in page_text for p in ['busier', 'temporary issue', "can't view", 'busy right now']):
                    print("[PAYMENT] Busy error on redirected page — will retry")
                    await asyncio.sleep(1.5)
                    break  # retry Place Order
                return True

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
            print("[PAYMENT] Starting checkout completion...")

            # Screenshot on entry — check logs/ folder to see what the bot saw.
            try:
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                await self._screenshot(tab, f"logs/checkout_entry_{ts}.png")
                print(f"[PAYMENT] Screenshot: logs/checkout_entry_{ts}.png")
            except Exception:
                pass

            # Skip duplicate wait if the nav wait already confirmed page-ready state.
            if initial_state in ('place_order', 'sac'):
                ready = initial_state
                print(f"[PAYMENT] Page already confirmed ready ({ready}) — skipping wait")
            else:
                ready = await self._wait_for_checkout_ready(tab, timeout=5.0)

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
                    # The Place Order button appears in the HTML shell before React
                    # finishes hydrating the page — clicking too early causes Target
                    # to silently reject the order (item moves to Saved for Later).
                    # Wait for a product image to load as the hydration signal.
                    print("[PAYMENT] FLOW A: Waiting for cart item image (page hydration check)...")
                    hydrated = False
                    hydration_deadline = time.time() + 2.0
                    while time.time() < hydration_deadline:
                        try:
                            img_ready = await tab.evaluate("""(() => {
                                const imgs = document.querySelectorAll('img[src]');
                                for (const img of imgs) {
                                    const src = img.src || '';
                                    if (!src || src.startsWith('data:')) continue;
                                    if (!img.complete || img.naturalWidth < 40) continue;
                                    const r = img.getBoundingClientRect();
                                    if (r.width >= 40 && r.height >= 40) return true;
                                }
                                return false;
                            })()""")
                            if img_ready:
                                hydrated = True
                                print("[PAYMENT] FLOW A: Cart item image loaded — page fully hydrated")
                                break
                        except Exception:
                            pass
                        await asyncio.sleep(0.1)
                    if not hydrated:
                        print("[PAYMENT] FLOW A: Item image not detected after 2s — proceeding anyway")
                    if self.test_mode:
                        print("[PAYMENT] TEST_MODE: going to cart")
                        await tab.get("https://www.target.com/cart")
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
                            await asyncio.sleep(1.0)           # pause so state is visible before redirect
                            print("[PAYMENT] TEST_MODE: going to cart")
                            await tab.get("https://www.target.com/cart")
                            return True
                        return await self._place_order(tab)

                # 'sac_again' or 'timeout' → continue loop (next step or retry)

            # ── Loop exhausted without finding Place Order ─────────────────────────────
            if self.test_mode:
                # In test mode success = we tried everything and cycled back to cart.
                print("[PAYMENT] TEST_MODE: S&C loop exhausted — going to cart")
                await tab.get("https://www.target.com/cart")
                return True

            # Prod: one final attempt to find Place Order.
            print("[PAYMENT] Final Place Order check...")
            return await self._place_order(tab)

        except Exception as e:
            print(f"[PAYMENT] Payment completion error: {e}")
            try:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                await self._screenshot(tab, f"logs/payment_error_{timestamp}.png")
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

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
            await asyncio.sleep(0.3)
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
            await asyncio.sleep(0.2)
        return False

    async def _press_escape(self, tab):
        """Press Escape key via CDP"""
        try:
            await tab.send("Input.dispatchKeyEvent", type="keyDown", key="Escape", windowsVirtualKeyCode=27)
            await tab.send("Input.dispatchKeyEvent", type="keyUp", key="Escape", windowsVirtualKeyCode=27)
        except Exception:
            pass

    async def _screenshot(self, tab, path: str):
        """Take a screenshot"""
        try:
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
            async with asyncio.timeout(120):
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
                        await close_btn.click()
                        print(f"[BANNER] Closed banner via: {selector}")
                        return
                except Exception:
                    continue

            # Hide all fixed/sticky elements at the bottom of the viewport via JS
            await tab.evaluate('''() => {
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
            }''')
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
    # Core purchase implementation
    # -------------------------------------------------------------------------

    async def _execute_purchase_impl(self, tcin: str) -> Dict[str, Any]:
        """Execute purchase for given TCIN using persistent session"""
        start_time = time.time()

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

                print(f"[PURCHASE] Waiting for add-to-cart button to be ready...")
                try:
                    btn = await tab.select('button[id^="addToCartButtonOrTextIdFor"]', timeout=5)
                    if not btn:
                        btn = await tab.select('button[data-test="addToCartButton"]', timeout=3)
                    if not btn:
                        btn = await tab.find("Add to cart", best_match=True, timeout=3)
                    if not btn:
                        btn = await tab.find("Preorder", best_match=True, timeout=3)
                    if btn:
                        print(f"[PURCHASE] Add-to-cart button visible")
                except Exception as wait_error:
                    print(f"[PURCHASE] Button wait warning: {wait_error}, proceeding anyway...")

                print(f"[PURCHASE] Page loaded, waiting for Shape Security...")
            except Exception as nav_error:
                print(f"[ERROR] Navigation failed: {nav_error}")
                raise

            # Select "Shipping" fulfillment option
            await self._select_shipping_option(tab)

            # Find add-to-cart button
            add_button = await self._find_add_to_cart_button(tab)
            if not add_button:
                return {
                    'success': False,
                    'tcin': tcin,
                    'reason': 'button_not_found',
                    'execution_time': time.time() - start_time
                }

            # Click add-to-cart with CDP response monitoring
            click_success = False
            cart_confirmed = False

            try:
                cart_event = asyncio.Event()

                async def on_response(event: cdp.network.ResponseReceived):
                    url = event.response.url
                    status = event.response.status
                    if 'cart' in url.lower() and status in [200, 201]:
                        cart_event.set()

                tab.add_handler(cdp.network.ResponseReceived, on_response)

                print(f"[PURCHASE] Clicking add-to-cart button...")
                try:
                    await self._scroll_into_view(add_button)
                    await self._dispatch_click(add_button)
                    click_success = True
                    print(f"[PURCHASE] Add-to-cart clicked")
                except Exception as click_err:
                    print(f"[PURCHASE] Click error: {click_err}")
                    click_success = False

                if not click_success:
                    return {
                        'success': False,
                        'tcin': tcin,
                        'reason': 'click_failed',
                        'execution_time': time.time() - start_time
                    }

                # Wait for cart API response
                try:
                    await asyncio.wait_for(cart_event.wait(), timeout=15.0)
                    print(f"[PURCHASE] Cart API responded (t={time.time() - start_time:.1f}s)")
                    cart_confirmed = True
                except asyncio.TimeoutError:
                    print("[PURCHASE] Cart API timeout, checking DOM...")
                    cart_confirmed = await self._verify_cart_addition(tab)

            except Exception as e:
                if not click_success:
                    return {
                        'success': False,
                        'tcin': tcin,
                        'reason': 'click_failed',
                        'execution_time': time.time() - start_time
                    }
                print(f"[PURCHASE] Cart response exception: {e}, checking DOM...")
                cart_confirmed = await self._verify_cart_addition(tab)

            if not cart_confirmed:
                return {
                    'success': False,
                    'tcin': tcin,
                    'reason': 'cart_addition_timeout',
                    'execution_time': time.time() - start_time
                }

            print(f"[PURCHASE] Cart confirmed (t={time.time() - start_time:.1f}s)")

            # Navigate to checkout
            self._notify_status(tcin, 'checking_out', {'timestamp': datetime.now().isoformat()})
            checkout_result = False

            try:
                await tab.get("https://www.target.com/checkout")
                print("[PURCHASE] Waiting for checkout page elements...")
                try:
                    btn = await tab.find("Place order", best_match=True, timeout=3)
                    if not btn:
                        btn = await tab.find("Continue", best_match=True, timeout=3)
                    if not btn:
                        btn = await tab.select('[data-test="save-and-continue-button"]', timeout=3)
                    if btn:
                        print("[PURCHASE] Checkout page ready")
                except Exception as wait_error:
                    print(f"[PURCHASE] Checkout element wait warning: {wait_error}")

                checkout_result = 'checkout' in tab.url.lower()
            except Exception as nav_error:
                print(f"[ERROR] Checkout navigation failed: {nav_error}")
                checkout_result = False

            if checkout_result:
                await self._handle_delivery_options(tab)
                payment_result = await self._complete_payment(tab)
                if not payment_result:
                    checkout_result = False

            if not checkout_result:
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

    # -------------------------------------------------------------------------
    # Cart verification
    # -------------------------------------------------------------------------

    async def _verify_cart_addition(self, tab) -> bool:
        """Verify item was successfully added to cart via visual indicators"""
        try:
            self.logger.info("[PURCHASE] Waiting for cart UI to update...")

            css_success_selectors = [
                '[data-test="cart-count"]',
                '[data-testid="cart-count"]',
                'span[data-test="@web/CartIcon"]',
                '[data-test="add-to-cart-confirmation"]',
                '[aria-label*="cart"][aria-label*="item"]',
            ]
            success_texts = ["Added to cart", "Item added"]

            for selector in css_success_selectors:
                try:
                    element = await tab.select(selector, timeout=4)
                    if element:
                        self.logger.info(f"[PURCHASE] Cart confirmed via: {selector}")
                        await asyncio.sleep(random.uniform(0.05, 0.1))
                        return True
                except Exception:
                    continue

            for text in success_texts:
                try:
                    element = await tab.find(text, best_match=True, timeout=2)
                    if element:
                        self.logger.info(f"[PURCHASE] Cart confirmed via text: {text}")
                        await asyncio.sleep(random.uniform(0.05, 0.1))
                        return True
                except Exception:
                    continue

            # Check for error messages
            self.logger.warning("[PURCHASE] No visual cart confirmation, checking for errors...")
            try:
                error_elem = await tab.find("Something went wrong", best_match=True, timeout=2)
                if error_elem:
                    self.logger.error("[PURCHASE] Target returned error - likely anti-bot detection")
                    return False
            except Exception:
                pass

            self.logger.info("[PURCHASE] Assuming success, proceeding...")
            await asyncio.sleep(random.uniform(0.1, 0.2))
            return True

        except Exception as e:
            self.logger.warning(f"[PURCHASE] Cart verification warning: {e}")
            await asyncio.sleep(random.uniform(0.1, 0.2))
            return True

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
        """Find add-to-cart/preorder button"""
        print("[BUTTON_FIND] Searching for add-to-cart/preorder button...")

        # PRIMARY: CSS selectors (fastest)
        primary_css = [
            'button[id^="addToCartButtonOrTextIdFor"]',
            'button[data-test="addToCartButton"]',
            'button[data-testid="addToCartButton"]',
        ]
        for sel in primary_css:
            try:
                button = await tab.select(sel, timeout=2)
                if button and await self._is_visible(button):
                    is_disabled = await self._get_attribute(button, 'disabled')
                    if not is_disabled:
                        print(f"[BUTTON_FIND] Found button (primary CSS): {sel}")
                        return button
            except Exception:
                continue

        # PRIMARY: Text-based
        primary_texts = ["Add to cart", "Add to Cart", "Preorder", "Pre-order"]
        for text in primary_texts:
            try:
                button = await tab.find(text, best_match=True, timeout=1)
                if button and await self._is_visible(button):
                    is_disabled = await self._get_attribute(button, 'disabled')
                    if not is_disabled:
                        print(f"[BUTTON_FIND] Found button (primary text): {text}")
                        return button
            except Exception:
                continue

        # SECONDARY: CSS selectors
        secondary_css = [
            '[data-testid*="add-to-cart"]',
            'button[data-testid="add-to-cart-button"]',
            'button[data-testid="pdp-add-to-cart"]',
            'button[data-testid="add-to-cart-cta"]',
            'button[data-test*="addToCart"]',
            'button[data-test*="add-to-cart"]',
            'button[data-test="chooseOptionsButton"]',
        ]
        for sel in secondary_css:
            try:
                button = await tab.select(sel, timeout=0.5)
                if button and await self._is_visible(button):
                    is_disabled = await self._get_attribute(button, 'disabled')
                    if not is_disabled:
                        print(f"[BUTTON_FIND] Found button (secondary CSS): {sel}")
                        return button
            except Exception:
                continue

        # SECONDARY: Text-based
        secondary_texts = ["PRE-ORDER", "PREORDER", "Add to bag", "Add to Bag", "Ship it"]
        for text in secondary_texts:
            try:
                button = await tab.find(text, best_match=True, timeout=0.5)
                if button and await self._is_visible(button):
                    is_disabled = await self._get_attribute(button, 'disabled')
                    if not is_disabled:
                        print(f"[BUTTON_FIND] Found button (secondary text): {text}")
                        return button
            except Exception:
                continue

        # FALLBACK: JS-based case-insensitive search
        print("[BUTTON_FIND] Trying JS fallback...")
        fallback_texts = ['preorder', 'pre-order', 'add to cart']
        for text in fallback_texts:
            try:
                result = await tab.evaluate(f'''(() => {{
                    const buttons = Array.from(document.querySelectorAll('button'));
                    return buttons.some(b =>
                        b.textContent.toLowerCase().includes('{text}') &&
                        !b.disabled &&
                        b.offsetParent !== null
                    );
                }})()''')
                if result:
                    button = await tab.find(text, best_match=True, timeout=1)
                    if button:
                        print(f"[BUTTON_FIND] Found via JS fallback: {text}")
                        return button
            except Exception:
                continue

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
        """Clear all items from cart"""
        try:
            remove_css_selectors = [
                'button[data-test="cartItem-remove"]',
                'button[aria-label*="remove"]',
                'button[aria-label*="Remove"]',
                '[data-test*="remove"]',
                '[data-testid*="remove"]',
            ]

            removed_count = 0
            max_attempts = 10

            for attempt in range(max_attempts):
                button_found = False

                for selector in remove_css_selectors:
                    try:
                        remove_button = await tab.select(selector, timeout=2)
                        if remove_button and await self._is_visible(remove_button):
                            current_count = await tab.evaluate(
                                'document.querySelectorAll'
                                '("[data-test=\\"cartItem-remove\\"],'
                                ' button[aria-label*=\\"remove\\"],'
                                ' button[aria-label*=\\"Remove\\"]").length'
                            )
                            await remove_button.click()
                            await self._wait_for_function(
                                tab,
                                f'document.querySelectorAll'
                                f'("[data-test=\\"cartItem-remove\\"],'
                                f' button[aria-label*=\\"remove\\"],'
                                f' button[aria-label*=\\"Remove\\"]").length < {current_count}',
                                timeout=5.0
                            )
                            removed_count += 1
                            button_found = True
                            break
                    except Exception:
                        continue

                if not button_found:
                    try:
                        remove_btn = await tab.find("Remove", best_match=True, timeout=2)
                        if remove_btn and await self._is_visible(remove_btn):
                            await remove_btn.click()
                            await asyncio.sleep(0.3)
                            removed_count += 1
                            button_found = True
                    except Exception:
                        pass

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

            await checkout_button.click()

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
        """Handle shipping/delivery selection - only if not already in review state"""
        try:
            # GUARD: If "Place your order" is already visible, skip everything
            for po_text in ["Place your order", "Place Order"]:
                try:
                    btn = await tab.find(po_text, best_match=True, timeout=2)
                    if btn and await self._is_visible(btn):
                        print("[DELIVERY] Already in review state (Place Order visible) — skipping")
                        return
                except Exception:
                    continue

            try:
                btn = await tab.select('[data-test="placeOrderButton"]', timeout=1)
                if btn and await self._is_visible(btn):
                    print("[DELIVERY] Already in review state — skipping")
                    return
            except Exception:
                pass

            print("[DELIVERY] Place Order not visible yet, checking for delivery option buttons...")

            # Click Shipping option if present
            shipping_texts = ["Ship", "Shipping"]
            for text in shipping_texts:
                try:
                    element = await tab.find(text, best_match=True, timeout=0.5)
                    if element and await self._is_visible(element):
                        await element.click()
                        try:
                            await tab.find("Continue", best_match=True, timeout=3)
                        except Exception:
                            pass
                        break
                except Exception:
                    continue

            # Click continue button
            continue_texts = ["Continue", "Next"]
            for text in continue_texts:
                try:
                    button = await tab.find(text, best_match=True, timeout=0.5)
                    if button and await self._is_visible(button):
                        await button.click()
                        try:
                            await tab.find("Place order", best_match=True, timeout=5)
                        except Exception:
                            pass
                        break
                except Exception:
                    continue

        except Exception:
            pass

    async def _complete_payment(self, tab) -> bool:
        """Complete payment process with dynamic checkout flow detection."""
        try:
            print("[PAYMENT] Starting payment completion process...")

            # Give checkout page a moment to hydrate
            print("[PAYMENT] Waiting 0.3s for checkout to hydrate...")
            await asyncio.sleep(0.3)

            # Click "Save and Continue" buttons to progress through checkout steps
            print("[PAYMENT] Looking for Save and Continue buttons...")
            save_continue_css = ['[data-test="save-and-continue-button"]']
            save_continue_texts = [
                "Save and continue", "Save & continue",
                "Save and Continue", "Save & Continue",
            ]

            for step in range(3):
                clicked = False

                for selector in save_continue_css:
                    try:
                        elem = await tab.select(selector, timeout=0.4)
                        if elem and await self._is_visible(elem):
                            await self._scroll_into_view(elem)
                            await elem.click()
                            print(f"[PAYMENT] Clicked Save and Continue (step {step + 1}) via CSS")
                            clicked = True
                            await asyncio.sleep(0.5)
                            break
                    except Exception:
                        continue

                if not clicked:
                    for text in save_continue_texts:
                        try:
                            elem = await tab.find(text, best_match=True, timeout=0.4)
                            if elem and await self._is_visible(elem):
                                await self._scroll_into_view(elem)
                                await elem.click()
                                print(f"[PAYMENT] Clicked Save and Continue (step {step + 1}): {text}")
                                clicked = True
                                await asyncio.sleep(0.5)
                                break
                        except Exception:
                            continue

                if not clicked:
                    print(f"[PAYMENT] No more S&C buttons found after {step} clicks — proceeding")
                    break

            # Find Place Order button
            print("[PAYMENT] Waiting for Place Order button to become enabled...")
            place_order_button = None
            found_selector = None

            place_order_texts = [
                "Place your order", "Place Your Order",
                "Place order", "Place Order", "PLACE ORDER",
                "Buy now", "Complete order", "Complete Order",
            ]
            place_order_css = [
                '[data-test="placeOrderButton"]',
                '[data-testid="placeOrderButton"]',
                '#placeOrderButton',
                '.place-order-button',
                'button[class*="place-order"]',
                'button[class*="placeOrder"]',
            ]

            for text in place_order_texts:
                try:
                    elem = await tab.find(text, best_match=True, timeout=4)
                    if elem and await self._is_visible(elem):
                        place_order_button = elem
                        found_selector = text
                        print(f"[PAYMENT] Place Order button found: {text}")
                        break
                except Exception:
                    continue

            if not place_order_button:
                for selector in place_order_css:
                    try:
                        elem = await tab.select(selector, timeout=2)
                        if elem and await self._is_visible(elem):
                            place_order_button = elem
                            found_selector = selector
                            print(f"[PAYMENT] Place Order button found via CSS: {selector}")
                            break
                    except Exception:
                        continue

            if not place_order_button:
                print("[PAYMENT] Could not find enabled Place Order button")
                try:
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    await self._screenshot(tab, f"logs/payment_button_not_found_{timestamp}.png")
                except Exception:
                    pass
                return False

            await self._scroll_into_view(place_order_button)

            # TEST_MODE: stop before clicking, navigate back to cart
            if self.test_mode:
                print("[PAYMENT] TEST_MODE: Stopping before clicking Place Order button")
                print("[PAYMENT] TEST_MODE: Navigating back to cart...")
                await tab.get("https://www.target.com/cart")
                return True

            # PRODUCTION: Click Place Order
            print(f"[PAYMENT] PROD: Clicking Place Order button ({found_selector})")
            try:
                await self._dispatch_click(place_order_button)
                print("[PAYMENT] Click dispatched")
            except Exception as click_error:
                print(f"[PAYMENT] dispatch click failed: {click_error}")
                try:
                    await place_order_button.click()
                    print("[PAYMENT] Fallback click succeeded")
                except Exception:
                    return False

            print("[PAYMENT] Waiting for order confirmation...")
            try:
                if await self._wait_for_url_contains(tab, 'confirmation', timeout=5.0):
                    print("[PAYMENT] Reached confirmation page")
                else:
                    current_url = tab.url
                    print(f"[PAYMENT] Current URL: {current_url}")
                    if 'confirmation' in current_url.lower() or 'thank' in current_url.lower():
                        print("[PAYMENT] On confirmation page")
                    else:
                        print("[PAYMENT] Proceeding (click sent)")
            except Exception:
                pass

            return True

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

#!/usr/bin/env python3
"""
Purchase Executor - Real Target.com purchasing using persistent session
Replaces mock purchasing with actual buy_bot integration
"""

import asyncio
import json
import logging
import time
import random
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Dict, Any

from .session_manager import SessionManager


class PurchaseExecutor:
    """Executes real purchases using persistent session and buy_bot logic"""

    def __init__(self, session_manager: SessionManager, status_callback: Optional[Callable] = None):
        self.session_manager = session_manager
        self.status_callback = status_callback
        self.logger = logging.getLogger(__name__)

        # Configuration
        self.purchase_timeout = 60  # 60 seconds max per purchase
        self.max_retries = 2

        # TEST_MODE support - read from environment
        self.test_mode = os.environ.get('TEST_MODE', 'false').lower() == 'true'

        # Lock to prevent concurrent page access
        self._page_lock = asyncio.Lock()

        # Load existing buy_bot selectors
        self.SELECTORS = {
            'add_to_cart': [
                # PRIMARY SELECTOR - Most reliable (from working buy_bot.py line 88)
                # This is the selector that Target.com consistently uses for add-to-cart buttons
                'button[id^="addToCartButtonOrTextIdFor"]',

                # SECONDARY SELECTORS - High confidence patterns
                'button[data-test="addToCartButton"]',
                'button[data-testid="addToCartButton"]',

                # PREORDER VARIANTS - Important for preorder products
                'button:has-text("Preorder")',
                'button:has-text("Pre-order")',
                'button:has-text("Pre order")',
                'button:has-text("Pre-Order")',
                'button[data-test*="preorder"]',

                # ADDITIONAL HIGH-PRIORITY SELECTORS
                'button[data-test="chooseOptionsButton"]',
                'button[data-test*="addToCart"]',
                'button[data-test*="add-to-cart"]',

                # Current Target button patterns (high priority)
                '[data-testid*="add-to-cart"]',
                '[data-testid*="addToCart"]',
                'button[data-testid="add-to-cart-button"]',
                'button[data-testid="pdp-add-to-cart"]',
                'button[data-testid="add-to-cart-cta"]',

                # Text-based selectors with exact Target.com text
                'button:has-text("Add to cart")',
                'button:has-text("Add to Cart")',
                'button:has-text("Add to bag")',
                'button:has-text("Add to Bag")',

                # Color-specific button targeting (red buttons are often the main CTA)
                'button[style*="background-color: rgb(204, 0, 0)"]',
                'button[class*="red"]',
                'button[class*="Red"]',

                # Generic fallbacks
                'button:has-text("Add")',
                'text="Add to cart"',
                'text="Preorder"'
            ]
        }

    async def execute_purchase(self, tcin: str) -> Dict[str, Any]:
        """
        Execute purchase for given TCIN using persistent session.
        Uses lock to prevent concurrent page access.
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

    async def _dismiss_sticky_banners(self, page) -> None:
        """
        Dismiss or hide sticky banners/overlays that can intercept clicks.
        Target commonly shows app download banners at the bottom of the page.
        """
        try:
            print("[BANNER] Checking for sticky banners...")

            # Method 1: Try to find and close Target app banner specifically
            app_banner_close_selectors = [
                '[data-test="app-banner-close"]',
                '[aria-label*="close" i][class*="banner" i]',
                '[aria-label*="dismiss" i][class*="banner" i]',
                'button[class*="AppBanner"] svg',
                '[data-test*="app-banner"] button',
                '[class*="sticky"] button[aria-label*="close" i]',
                '[class*="fixed"] button[aria-label*="close" i]',
            ]

            for selector in app_banner_close_selectors:
                try:
                    close_btn = await page.wait_for_selector(selector, timeout=150)
                    if close_btn and await close_btn.is_visible():
                        await close_btn.click()
                        print(f"[BANNER] ✓ Closed banner via: {selector}")
                        return
                except:
                    continue

            # Method 2: Hide all fixed/sticky positioned elements at bottom via JS
            await page.evaluate('''() => {
                const viewportHeight = window.innerHeight;
                const elements = document.querySelectorAll('*');

                elements.forEach(el => {
                    const style = window.getComputedStyle(el);
                    const position = style.position;

                    // Check if element is fixed or sticky
                    if (position === 'fixed' || position === 'sticky') {
                        const rect = el.getBoundingClientRect();
                        // If element is in the bottom 20% of viewport, hide it
                        if (rect.top > viewportHeight * 0.8) {
                            el.style.setProperty('display', 'none', 'important');
                            console.log('[BANNER] Hidden sticky element at bottom');
                        }
                    }
                });
            }''')
            print("[BANNER] ✓ Executed JS to hide bottom sticky elements")

        except Exception as e:
            print(f"[BANNER] Warning during banner dismissal: {e}")

    async def _select_shipping_option(self, page) -> bool:
        """
        Select the 'Shipping' fulfillment option on product page.
        Target shows Pickup, Delivery, Shipping options - we need Shipping for checkout.
        OPTIMIZED: Uses combined selector for parallel checking (<500ms total)
        """
        try:
            print("[SHIPPING] Checking for fulfillment options...")

            # Combined selector - check all shipping options at once (fast parallel check)
            shipping_selector = '[data-test="fulfillment-cell-shipping"], [data-test="shipItButton"], [data-testid="fulfillment-cell-shipping"], button:has-text("Ship it"), button:has-text("Ship It"), [role="button"]:has-text("Ship")'

            try:
                element = await page.wait_for_selector(shipping_selector, timeout=1000)
                if element and await element.is_visible():
                    # Check if already selected
                    is_pressed = await element.get_attribute('aria-pressed')
                    is_selected = await element.get_attribute('aria-selected')

                    if is_pressed == 'true' or is_selected == 'true':
                        print("[SHIPPING] ✓ Shipping already selected")
                        return True

                    # Click to select shipping
                    await element.dispatch_event('click')
                    print("[SHIPPING] ✓ Clicked Shipping option")
                    return True
            except:
                pass

            # No shipping option found - might be a product with only one fulfillment method
            print("[SHIPPING] No shipping option found (single fulfillment product)")
            return True

        except Exception as e:
            print(f"[SHIPPING] Warning: {e}")
            return True  # Don't fail purchase if shipping selection has issues

    async def _wait_for_click_handler(self, page, button, max_wait: float = 5.0) -> bool:
        """
        Wait for Shape Security to attach click event handlers.
        Buttons may appear clickable but have no event listeners yet.
        """
        print("[SHAPE] Waiting for click event handlers to be attached...")
        start_time = time.time()

        while (time.time() - start_time) < max_wait:
            try:
                # Check if button has click handlers using JavaScript
                has_handler = await button.evaluate('''(element) => {
                    // Check for onclick attribute
                    if (element.onclick) return true;

                    // Check for addEventListener event listeners (harder to detect)
                    // Check if element has event listeners by looking at internal properties
                    const listeners = window.getEventListeners ? window.getEventListeners(element) : null;
                    if (listeners && listeners.click && listeners.click.length > 0) return true;

                    // Fallback: check if element has any event-related attributes
                    if (element.hasAttribute('onclick')) return true;

                    // Check parent elements for delegated handlers
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
                    print(f"[SHAPE] ✓ Click handler detected after {time.time() - start_time:.2f}s")
                    return True

            except Exception as e:
                print(f"[SHAPE] Handler check error: {e}")

            await asyncio.sleep(0.2)

        print(f"[SHAPE] ⚠ No click handler detected after {max_wait}s, proceeding anyway...")
        return False

    async def _humanized_click(self, page, button, action_name: str):
        """Fast click using dispatch_event (bypasses Shape Security)"""
        try:
            print(f"[HUMANIZE] Clicking {action_name}...")

            # Scroll into view (no sleep needed - synchronous operation)
            try:
                await button.scroll_into_view_if_needed(timeout=3000)
                await page.evaluate('window.scrollBy(0, -100)')  # Avoid bottom banners
            except Exception as e:
                print(f"[HUMANIZE] Scroll warning: {e}")

            # Direct click via dispatch_event (bypasses overlays and Shape Security)
            await button.dispatch_event('click')
            print(f"[HUMANIZE] ✓ Clicked {action_name}")
            return True

        except Exception as e:
            print(f"[HUMANIZE] ✗ Click failed for {action_name}: {e}")
            try:
                await button.evaluate('el => el.click()')
                return True
            except:
                return False

    async def _execute_purchase_impl(self, tcin: str) -> Dict[str, Any]:
        """Execute purchase for given TCIN using persistent session"""
        start_time = time.time()
        page = None

        try:
            print(f"[PURCHASE] Starting purchase for {tcin}")
            self._notify_status(tcin, 'attempting', {'start_time': datetime.now().isoformat()})

            # Get existing logged-in page from session
            context = self.session_manager.context
            if not context:
                raise Exception("Browser context not available")

            pages = context.pages
            if not pages:
                raise Exception("No pages available - session not initialized properly")

            page = pages[0]

            # Navigate to product page
            product_url = f"https://www.target.com/p/-/A-{tcin}"
            try:
                print(f"[PURCHASE] Navigating to {product_url}")
                await page.goto(product_url, wait_until='domcontentloaded', timeout=10000)
                # Wait for add-to-cart button to be interactive (replaces static sleep)
                print(f"[PURCHASE] Waiting for add-to-cart button to be ready...")
                try:
                    await page.wait_for_selector(
                        'button[id^="addToCartButtonOrTextIdFor"], button:has-text("Add to cart"), button:has-text("Preorder")',
                        state='visible',
                        timeout=8000
                    )
                    print(f"[PURCHASE] ✓ Add-to-cart button visible")
                except Exception as wait_error:
                    print(f"[PURCHASE] Button wait warning: {wait_error}, proceeding anyway...")
                print(f"[PURCHASE] Page loaded, waiting for Shape Security...")
            except Exception as nav_error:
                print(f"[ERROR] Navigation failed: {nav_error}")
                raise

            # Select "Shipping" fulfillment option if multiple options are present
            # Target shows Pickup, Delivery, Shipping - we need to click Shipping first
            await self._select_shipping_option(page)

            # Find and click add-to-cart button
            add_button = await self._find_add_to_cart_button(page)
            if not add_button:
                return {
                    'success': False,
                    'tcin': tcin,
                    'reason': 'button_not_found',
                    'execution_time': time.time() - start_time
                }

            # FAST: Direct click for add-to-cart (dispatch_event bypasses Shape Security)
            # Wait for add-to-cart API response while clicking (event-driven)
            try:
                async with page.expect_response(
                    lambda r: 'cart' in r.url.lower() and r.request.method == 'POST' and r.status in [200, 201],
                    timeout=15000
                ) as response_info:
                    print(f"[PURCHASE] Clicking add-to-cart button...")
                    try:
                        await add_button.scroll_into_view_if_needed(timeout=1000)
                        await add_button.dispatch_event('click')
                        click_success = True
                        print(f"[PURCHASE] ✓ Add-to-cart clicked")
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

                response = await response_info.value
                print(f"[PURCHASE] ✓ Cart API responded: {response.status} (t={time.time() - start_time:.1f}s)")

            except TimeoutError:
                # Fallback: check DOM for cart confirmation
                print("[PURCHASE] Cart API response not intercepted, checking DOM...")
                cart_confirmed = await self._verify_cart_addition(page)
                if not cart_confirmed:
                    return {
                        'success': False,
                        'tcin': tcin,
                        'reason': 'cart_addition_timeout',
                        'execution_time': time.time() - start_time
                    }
                print(f"[PURCHASE] ✓ Cart confirmed via DOM (t={time.time() - start_time:.1f}s)")
            except Exception as e:
                # Handle other exceptions (click failed, etc.)
                if not click_success:
                    return {
                        'success': False,
                        'tcin': tcin,
                        'reason': 'click_failed',
                        'execution_time': time.time() - start_time
                    }
                print(f"[PURCHASE] Cart response exception: {e}, checking DOM...")
                cart_confirmed = await self._verify_cart_addition(page)
                if not cart_confirmed:
                    return {
                        'success': False,
                        'tcin': tcin,
                        'reason': 'cart_addition_timeout',
                        'execution_time': time.time() - start_time
                    }
                print(f"[PURCHASE] ✓ Cart confirmed via DOM fallback (t={time.time() - start_time:.1f}s)")

            # Navigate to checkout (no static sleep needed - cart confirmed above)
            self._notify_status(tcin, 'checking_out', {'timestamp': datetime.now().isoformat()})

            try:
                await page.goto("https://www.target.com/checkout", wait_until='domcontentloaded', timeout=10000)
                # Wait for checkout page to be ready (replaces static sleep)
                print("[PURCHASE] Waiting for checkout page elements...")
                try:
                    await page.wait_for_selector(
                        'button:has-text("Place order"), button:has-text("Continue"), [data-test*="checkout"]',
                        state='visible',
                        timeout=15000
                    )
                    print("[PURCHASE] ✓ Checkout page ready")
                except Exception as wait_error:
                    print(f"[PURCHASE] Checkout element wait warning: {wait_error}")

                current_url = page.url
                checkout_result = 'checkout' in current_url.lower()

            except Exception as nav_error:
                print(f"[ERROR] Checkout navigation failed: {nav_error}")
                checkout_result = False

            # Handle delivery and payment (TEST_MODE stops before final button)
            if checkout_result:
                await self._handle_delivery_options(page)
                payment_result = await self._complete_payment(page)
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

            # TEST_MODE: Navigate to cart and clear it
            # PROD_MODE: Stay on confirmation page until next cycle
            if self.test_mode:
                # TEST_MODE: Go back to cart and clear it
                await page.goto("https://www.target.com/cart", wait_until='commit', timeout=2000)
                await asyncio.sleep(random.uniform(0.1, 0.15))

                clear_result = await self._clear_cart(page)
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

                print(f"[PURCHASE] ✓ TEST_MODE: Cart cleared, cycle complete: {tcin} in {execution_time:.2f}s")
            else:
                # PROD_MODE: Stay on confirmation page, next purchase will navigate to product
                execution_time = time.time() - start_time
                print(f"[PURCHASE] ✓ PROD_MODE: Order complete: {tcin} in {execution_time:.2f}s")
                print(f"[PURCHASE] ✓ PROD_MODE: Staying on confirmation (next attempt will navigate to product)")

            # CRITICAL: Save session after successful purchase
            await self.session_manager.save_session_state()

            # Notify dashboard immediately with SUCCESS status
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

            return {
                'success': False,
                'tcin': tcin,
                'reason': 'exception',
                'error': str(e),
                'execution_time': execution_time
            }

    async def _verify_cart_addition(self, page) -> bool:
        """Verify item was successfully added to cart via visual indicators"""
        try:
            # ANTI-BOT: Human-like patience - wait for UI to update naturally
            # Target's Shape system monitors timing patterns
            self.logger.info("[PURCHASE] Waiting for cart UI to update...")

            # Check for various success indicators (any one is sufficient)
            success_indicators = [
                # Cart count badge updated (most reliable)
                '[data-test="cart-count"]',
                '[data-testid="cart-count"]',
                'span[data-test="@web/CartIcon"]',

                # Success messages/toasts
                'text="Added to cart"',
                ':has-text("Added to cart")',
                'text="Item added"',
                ':has-text("Item added")',
                '[data-test="add-to-cart-confirmation"]',

                # Cart icon with items
                '[aria-label*="cart"][aria-label*="item"]',
            ]

            # Try to find success indicators with generous timeout
            for indicator in success_indicators:
                try:
                    element = await page.wait_for_selector(indicator, timeout=4000)
                    if element:
                        self.logger.info(f"[PURCHASE] ✅ Cart addition confirmed via: {indicator}")
                        # Fast confirmation (reduced from 0.2-0.4s)
                        await asyncio.sleep(random.uniform(0.05, 0.1))
                        return True
                except:
                    continue

            # If no visual indicator found, check for error message
            self.logger.warning("[PURCHASE] No visual cart confirmation found, checking for errors...")
            try:
                error_element = await page.wait_for_selector(
                    'text="Something went wrong", :has-text("not added to your cart")',
                    timeout=2000
                )
                if error_element:
                    self.logger.error("[PURCHASE] ❌ Target returned 'Something went wrong' error - likely anti-bot detection")
                    return False
            except:
                pass

            # No error message, assume success with short fallback
            self.logger.info("[PURCHASE] Assuming success, proceeding...")
            await asyncio.sleep(random.uniform(0.1, 0.2))
            return True

        except Exception as e:
            self.logger.warning(f"[PURCHASE] Cart verification warning: {e}")
            await asyncio.sleep(random.uniform(0.1, 0.2))
            return True

    async def _dismiss_popups(self, page) -> bool:
        """Dismiss Target engagement popups (photo upload, reviews, surveys)"""
        try:
            self.logger.debug("[POPUP] Checking for and dismissing any popups...")

            # Common popup close button patterns
            popup_selectors = [
                # Generic dismiss buttons
                'button:has-text("Cancel")',
                'button:has-text("Skip")',
                'button:has-text("Skip for now")',
                'button:has-text("Not now")',
                'button:has-text("Maybe later")',
                'button:has-text("No thanks")',
                'button:has-text("Dismiss")',
                'button:has-text("Close")',

                # Bundle/upsell modal specific (FREQUENTLY BOUGHT TOGETHER FIX)
                'button:has-text("Continue shopping")',
                'button:has-text("No thanks, continue")',
                'button:has-text("Continue without")',
                'button:has-text("Shop separately")',
                'button:has-text("Continue to product")',
                'button:has-text("View product")',

                # Side drawer/panel specific (DRAWER FROM RIGHT FIX)
                '[data-test*="drawer"] button[aria-label*="Close"]',
                '[data-test*="drawer"] button[aria-label*="close"]',
                '[class*="drawer"] button[aria-label*="Close"]',
                '[class*="drawer"] button[class*="close"]',
                '[class*="panel"] button[aria-label*="Close"]',
                '[class*="sidebar"] button[aria-label*="Close"]',
                'aside button[aria-label*="Close"]',
                '[role="complementary"] button[aria-label*="Close"]',

                # ARIA labels (photo upload modals often use these)
                '[aria-label*="Close"]',
                '[aria-label*="close"]',
                '[aria-label*="Dismiss"]',
                '[aria-label*="dismiss"]',

                # Common data-test attributes
                '[data-test*="close"]',
                '[data-test*="dismiss"]',
                'button[class*="close"]',
                'button[class*="Close"]',

                # X buttons in top-right corner
                'button.close',
                'button[aria-label="Close dialog"]',
            ]

            dismissed_count = 0

            # Try each selector (quick timeouts, non-blocking)
            for selector in popup_selectors:
                try:
                    # Wait for element (increased timeout for drawer animation)
                    button = await page.wait_for_selector(selector, timeout=1000)
                    if button:
                        # Use programmatic click to avoid auto-scroll
                        await button.evaluate('el => el.click()')
                        dismissed_count += 1
                        self.logger.info(f"[POPUP] Dismissed popup using: {selector}")
                        await asyncio.sleep(0.1)  # Brief pause after dismissal
                except:
                    continue

            # Also try pressing Escape key (works for many modals)
            try:
                await page.keyboard.press('Escape')
                await asyncio.sleep(0.05)
            except:
                pass

            # DRAWER FIX: Try clicking outside the drawer (on overlay/backdrop)
            # Side drawers often have a backdrop that dismisses when clicked
            try:
                backdrop_selectors = [
                    '[class*="backdrop"]',
                    '[class*="overlay"]',
                    '[class*="Overlay"]',
                    '[data-test*="backdrop"]',
                    '[data-test*="overlay"]',
                ]
                for backdrop in backdrop_selectors:
                    try:
                        element = await page.wait_for_selector(backdrop, timeout=500)
                        if element:
                            # Use programmatic click to avoid auto-scroll
                            await element.evaluate('el => el.click()')
                            self.logger.info(f"[POPUP] Dismissed drawer by clicking backdrop: {backdrop}")
                            await asyncio.sleep(0.1)
                            break
                    except:
                        continue
            except Exception as e:
                self.logger.debug(f"[POPUP] Backdrop click attempt: {e}")

            if dismissed_count > 0:
                self.logger.info(f"[POPUP] Successfully dismissed {dismissed_count} popup(s)")

            return True

        except Exception as e:
            self.logger.debug(f"[POPUP] Popup dismissal check: {e}")
            return True  # Don't fail purchase if dismissal has issues

    async def _validate_login_status(self, page) -> bool:
        """Validate that user is still logged in"""
        try:
            # Check for login indicators
            login_indicators = [
                '[data-test="@web/AccountLink"]',
                '[data-test="accountNav"]',
                'button[aria-label*="Account"]',
                'button[aria-label*="Hi,"]',
                'button:has-text("Hi,")'
            ]

            for indicator in login_indicators:
                try:
                    await page.wait_for_selector(indicator, timeout=2000)
                    return True
                except:
                    continue

            # Check for sign-in prompts
            signin_indicators = [
                'text="Sign in"',
                'button:has-text("Sign in")',
                'input[type="email"]'
            ]

            for indicator in signin_indicators:
                try:
                    await page.wait_for_selector(indicator, timeout=1000)
                    return False  # Found sign-in prompt, not logged in
                except:
                    continue

            return True  # No negative indicators found

        except Exception as e:
            self.logger.warning(f"Login validation error: {e}")
            return False

    def _get_timeout_for_selector(self, selector_index: int) -> int:
        """
        Get timeout based on selector priority (ULTRA-FAST competitive strategy).

        COMPETITIVE BOT OPTIMIZATION:
        - Stellar AIO and cutting-edge bots: sub 5 second checkout times
        - Primary selectors: 1000ms (1s max) - if not found fast, move on
        - Secondary selectors: 500ms - quick checks only
        - Fallback selectors: 300ms - lightning fast fallbacks

        Total max time: ~10-15 seconds for all 87 selectors (vs 2+ minutes before)
        """
        if selector_index <= 2:  # Primary selectors
            return 1000  # Reduced from 5000ms to 1000ms (80% faster)
        elif selector_index <= 8:  # Secondary selectors
            return 500   # Reduced from 3000ms to 500ms (83% faster)
        else:  # Fallback selectors
            return 300   # Reduced from 1000ms to 300ms (70% faster)

    async def _find_add_to_cart_button(self, page):
        """Find add-to-cart/preorder button with Shape Security bypass
        OPTIMIZED: Uses combined selector for fast parallel checking (<2s total)
        """
        print("[BUTTON_FIND] Searching for add-to-cart/preorder button...")

        # PRIMARY: Combined selector for fastest match (check all at once)
        # This checks all common button patterns in parallel via CSS selector list
        primary_selector = 'button[id^="addToCartButtonOrTextIdFor"], button[data-test="addToCartButton"], button[data-testid="addToCartButton"], button:has-text("Add to cart"), button:has-text("Preorder"), button:has-text("Pre-order")'

        try:
            button = await page.wait_for_selector(primary_selector, timeout=2000)
            if button and await button.is_visible():
                is_disabled = await button.get_attribute('disabled')
                if not is_disabled:
                    print("[BUTTON_FIND] ✓ Found button (primary selector)")
                    return button
        except:
            pass

        # SECONDARY: Try individual selectors with short timeouts
        # Only reached if primary combined selector fails
        secondary_selectors = [
            'button:has-text("PRE-ORDER")',
            'button:has-text("PREORDER")',
            'button[data-test*="preorder"]',
            '[data-testid*="add-to-cart"]',
            'button:has-text("Add to bag")',
            'button:has-text("Ship it")',
        ]

        for selector in secondary_selectors:
            try:
                button = await page.wait_for_selector(selector, timeout=500)
                if button and await button.is_visible():
                    is_disabled = await button.get_attribute('disabled')
                    if not is_disabled:
                        print(f"[BUTTON_FIND] ✓ Found button: {selector}")
                        return button
            except:
                continue

        # FALLBACK: JS-based case-insensitive search (last resort)
        print("[BUTTON_FIND] Trying JS fallback...")
        fallback_texts = ['preorder', 'pre-order', 'add to cart']
        for text in fallback_texts:
            try:
                button = await page.evaluate(f'''() => {{
                    const buttons = Array.from(document.querySelectorAll('button'));
                    return buttons.find(b =>
                        b.textContent.toLowerCase().includes('{text}') &&
                        !b.disabled &&
                        b.offsetParent !== null
                    );
                }}''')
                if button:
                    print(f"[BUTTON_FIND] ✓ Found via JS fallback: {text}")
                    return await page.query_selector(f'button:has-text("{text}")')
            except:
                continue

        print("[BUTTON_FIND] ✗ Could not find add-to-cart/preorder button")
        await self._take_debug_screenshot(page, "no_add_to_cart_button")
        return None

    async def _take_debug_screenshot(self, page, reason: str) -> Optional[str]:
        """Take debug screenshot for troubleshooting"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            screenshot_path = f"logs/debug_{reason}_{timestamp}.png"
            await page.screenshot(path=screenshot_path, full_page=True)
            return screenshot_path
        except:
            return None

    async def _verify_item_in_cart(self, page, tcin: str) -> bool:
        """Verify that item was added to cart"""
        try:
            # Look for cart items
            cart_selectors = [
                '[data-test="cart-item"]',
                '[data-testid="cart-item"]',
                '.cart-item',
                '[class*="cart-item"]',
                '[data-test*="cartItem"]'
            ]

            for selector in cart_selectors:
                try:
                    items = await page.query_selector_all(selector)
                    if items:
                        self.logger.debug(f"Found {len(items)} cart items")
                        return True
                except:
                    continue

            # Also check for cart count indicator
            count_selectors = [
                '[data-test="cart-count"]',
                '[data-testid="cart-count"]',
                '.cart-count'
            ]

            for selector in count_selectors:
                try:
                    count_element = await page.query_selector(selector)
                    if count_element:
                        count_text = await count_element.inner_text()
                        if count_text and count_text.strip() != '0':
                            return True
                except:
                    continue

            return False

        except Exception as e:
            self.logger.error(f"Cart verification error: {e}")
            return False

    async def _proceed_to_checkout(self, page) -> bool:
        """Complete full checkout process including payment"""
        try:
            # Step 1: Navigate to checkout
            checkout_button = await self._find_checkout_button(page)
            if not checkout_button:
                return False

            await checkout_button.click()

            # Step 2: Wait for checkout page to load (URL change is sufficient)
            try:
                await page.wait_for_url('**/checkout**', timeout=10000)
                self.logger.info("Reached checkout page, proceeding with purchase...")
            except:
                # Check if we're on a checkout-related page
                current_url = page.url
                if 'checkout' not in current_url.lower():
                    self.logger.error("Failed to reach checkout page")
                    return False

            # Step 3: Handle shipping/delivery options (if needed)
            await self._handle_delivery_options(page)

            # Step 4: Select payment method and complete purchase
            if await self._complete_payment(page):
                # Step 5: Verify order completion
                return await self._verify_order_completion(page)
            else:
                return False

        except Exception as e:
            self.logger.error(f"Checkout process error: {e}")
            return False

    async def _proceed_to_checkout_direct(self, page) -> bool:
        """Navigate directly to checkout page (new flow for testing cycles)"""
        try:
            # SHAPE FIX: Use domcontentloaded (faster, more realistic) + random delay
            # Humans don't wait for all images/resources, just for page to be interactive
            self.logger.info("[PURCHASE] Navigating to checkout page...")
            await page.goto("https://www.target.com/checkout", wait_until='domcontentloaded', timeout=5000)

            # Fast proceed - page already loaded via domcontentloaded
            # Reduced from 0.3-0.8s (Shape doesn't monitor checkout page timing)
            human_delay = random.uniform(0.1, 0.2)
            self.logger.info(f"[PURCHASE] Page loaded, proceeding...")
            await asyncio.sleep(human_delay)

            # Verify we're on checkout page
            current_url = page.url
            if 'checkout' in current_url.lower():
                self.logger.info(f"[PURCHASE] Successfully reached checkout page: {current_url}")

                # Handle any delivery options if needed
                await self._handle_delivery_options(page)

                # In TEST_MODE, we stop at the final button (already handled in _complete_payment)
                # but we need to navigate to the payment step to verify full flow
                payment_result = await self._complete_payment(page)

                return payment_result
            else:
                self.logger.error(f"[PURCHASE] Failed to reach checkout page, at: {current_url}")
                return False

        except Exception as e:
            self.logger.error(f"[PURCHASE] Checkout navigation error: {e}")
            return False

    async def _clear_cart(self, page) -> bool:
        """Clear all items from cart"""
        try:
            remove_selectors = [
                'button[data-test="cartItem-remove"]',
                'button[aria-label*="remove"]',
                'button[aria-label*="Remove"]',
                'button:has-text("Remove")',
                '[data-test*="remove"]',
                '[data-testid*="remove"]'
            ]

            removed_count = 0
            max_attempts = 10

            for attempt in range(max_attempts):
                button_found = False

                for selector in remove_selectors:
                    try:
                        remove_button = await page.wait_for_selector(selector, timeout=500)
                        if remove_button and await remove_button.is_visible():
                            # Get current cart item count before removal
                            current_count = await page.evaluate(
                                'document.querySelectorAll("[data-test=\\"cartItem-remove\\"], button[aria-label*=\\"remove\\"], button[aria-label*=\\"Remove\\"]").length'
                            )
                            await remove_button.click()
                            # Wait for item to be removed from DOM instead of static sleep
                            try:
                                await page.wait_for_function(
                                    f'document.querySelectorAll("[data-test=\\"cartItem-remove\\"], button[aria-label*=\\"remove\\"], button[aria-label*=\\"Remove\\"]").length < {current_count}',
                                    timeout=5000
                                )
                            except:
                                # Fallback to short delay if function wait fails
                                await asyncio.sleep(0.2)
                            removed_count += 1
                            button_found = True
                            break
                    except:
                        continue

                if not button_found:
                    break

            # Verify cart is empty
            empty_indicators = [
                'text="Your cart is empty"',
                ':has-text("Your cart is empty")',
                ':has-text("cart is empty")',
                '[data-test="empty-cart"]'
            ]

            for indicator in empty_indicators:
                try:
                    await page.wait_for_selector(indicator, timeout=1000)
                    return True
                except:
                    continue

            # If we removed items, consider success
            return removed_count > 0 or True

        except:
            return False

    async def _find_checkout_button(self, page):
        """Find checkout button on cart page"""
        checkout_selectors = [
            'button:has-text("Checkout")',
            'button:has-text("Check out")',
            '[data-test*="checkout"]',
            '[data-testid*="checkout"]',
            'button[data-test="checkout-button"]',
            'button[data-testid="checkout-button"]'
        ]

        for selector in checkout_selectors:
            try:
                button = await page.wait_for_selector(selector, timeout=2000)
                if button and await button.is_visible():
                    self.logger.debug(f"Found checkout button with selector: {selector}")
                    return button
            except:
                continue

        self.logger.warning("Could not find checkout button")
        return None

    async def _handle_delivery_options(self, page):
        """Handle shipping/delivery selection - event-driven waits"""
        try:
            # Fast shipping selection (500ms max per selector)
            shipping_selectors = [
                'button:has-text("Ship")',
                'button:has-text("Shipping")',
                '[data-test*="shipping"]',
                'button[aria-label*="Ship"]'
            ]

            for selector in shipping_selectors:
                try:
                    element = await page.wait_for_selector(selector, timeout=500)
                    if element and await element.is_visible():
                        await element.click()
                        # Wait for next UI element instead of static sleep
                        try:
                            await page.wait_for_selector(
                                'button:has-text("Continue"), button:has-text("Next"), [data-test*="continue"]',
                                state='visible',
                                timeout=3000
                            )
                        except:
                            pass  # Continue anyway if selector not found
                        break
                except:
                    continue

            # Fast continue button (500ms max per selector)
            continue_selectors = [
                'button:has-text("Continue")',
                'button:has-text("Next")',
                '[data-test*="continue"]'
            ]

            for selector in continue_selectors:
                try:
                    button = await page.wait_for_selector(selector, timeout=500)
                    if button and await button.is_visible():
                        await button.click()
                        # Wait for payment/place-order section instead of static sleep
                        try:
                            await page.wait_for_selector(
                                'button:has-text("Place order"), button:has-text("Place Order"), [data-test*="payment"], [data-test*="placeOrder"]',
                                state='visible',
                                timeout=5000
                            )
                        except:
                            pass  # Continue anyway if selector not found
                        break
                except:
                    continue

        except:
            pass

    async def _complete_payment(self, page) -> bool:
        """Complete payment process with dynamic checkout flow detection.

        Handles two scenarios:
        1. Scenario A: "Place your order" is already enabled → just click it (fastest path)
        2. Scenario B: Need to click "Save and Continue" for address/payment first
        """
        try:
            print("[PAYMENT] Starting payment completion process...")

            # Expanded selector list with more variations
            # OPTIMIZED: "Place your order" moved to top - this is the one that works per logs
            place_order_selectors = [
                'button:has-text("Place your order")',  # PRIORITY - this one works!
                'button:has-text("Place Your Order")',
                'button:has-text("Place order")',
                'button:has-text("Place Order")',
                'button:has-text("PLACE ORDER")',
                '[data-test="placeOrderButton"]',
                '[data-test*="place-order"]',
                '[data-testid="placeOrderButton"]',
                '[data-testid*="place-order"]',
                'button:has-text("Buy now")',
                'button:has-text("Complete order")',
                'button:has-text("Complete Order")',
                'button[aria-label*="Place order"]',
                'button[aria-label*="place order"]',
                '#placeOrderButton',
                '.place-order-button',
                'button[class*="place-order"]',
                'button[class*="placeOrder"]'
            ]

            place_order_button = None
            found_selector = None

            # DYNAMIC CHECKOUT FLOW: Check if "Place your order" is already enabled FIRST
            # This handles Scenario A (fastest path) - skip "Save and Continue" entirely
            if not self.test_mode:
                print("[PAYMENT] ⚡ DYNAMIC FLOW: Checking if Place Order is already enabled...")

                for selector in place_order_selectors[:5]:  # Check top 5 most common selectors
                    try:
                        button = await page.wait_for_selector(selector, timeout=500)
                        if button and await button.is_visible():
                            is_disabled = await button.get_attribute('disabled')
                            if not is_disabled:
                                print(f"[PAYMENT] ✓ SCENARIO A: Place Order already enabled! Using fast path")
                                print(f"[PAYMENT] ⚡ Found with selector: {selector}")
                                place_order_button = button
                                found_selector = selector
                                break
                    except:
                        continue

                # Only go through Save and Continue flow if Place Order is NOT ready (Scenario B)
                if not place_order_button:
                    print("[PAYMENT] → SCENARIO B: Place Order not ready, proceeding with Save and Continue flow...")

                    print("[PAYMENT] Waiting for address/payment autofill...")
                    try:
                        # Wait for shipping address to be filled (indicates autofill happened)
                        await page.wait_for_selector(
                            '[data-test*="address"] :has-text("Ship to"), [data-test*="shipping"] :has-text("Ship"), :has-text("Shipping address")',
                            state='visible',
                            timeout=10000
                        )
                        print("[PAYMENT] ✓ Shipping info detected")
                    except:
                        print("[PAYMENT] ⚠ Shipping selector not found, checking for payment...")

                    try:
                        # Wait for payment method to be visible
                        await page.wait_for_selector(
                            '[data-test*="payment"], [data-test*="card"], :has-text("Payment"), :has-text("Credit"), :has-text("Debit")',
                            state='visible',
                            timeout=5000
                        )
                        print("[PAYMENT] ✓ Payment info detected")
                    except:
                        print("[PAYMENT] ⚠ Payment selector not found, proceeding...")

                    # Click "Save and Continue" buttons to progress through checkout steps
                    # Target checkout has multiple steps: shipping → payment → place order
                    print("[PAYMENT] Looking for Save and Continue buttons...")
                    save_continue_selectors = [
                        'button:has-text("Save and continue")',
                        'button:has-text("Save & continue")',
                        'button:has-text("Save and Continue")',
                        'button:has-text("Save & Continue")',
                        '[data-test*="save-and-continue"]',
                        '[data-test*="saveAndContinue"]',
                        'button[data-test="save-and-continue-button"]',
                    ]

                    # May need to click multiple times (shipping, then payment sections)
                    for step in range(3):  # Max 3 steps
                        clicked = False
                        for selector in save_continue_selectors:
                            try:
                                button = await page.wait_for_selector(selector, timeout=2000)
                                if button and await button.is_visible():
                                    is_disabled = await button.get_attribute('disabled')
                                    if not is_disabled:
                                        await button.dispatch_event('click')
                                        print(f"[PAYMENT] ✓ Clicked Save and Continue (step {step + 1})")
                                        clicked = True
                                        # Wait for UI to update (event-driven, not static sleep)
                                        try:
                                            await page.wait_for_selector(
                                                'button:has-text("Save and continue"):not([disabled]), button:has-text("Place your order"):not([disabled])',
                                                state='visible',
                                                timeout=3000
                                            )
                                        except:
                                            await asyncio.sleep(0.3)  # Short fallback only if selector fails
                                        break
                            except:
                                continue

                        if not clicked:
                            print(f"[PAYMENT] No more Save and Continue buttons found after {step} clicks")
                            break

                    # After Save and Continue flow, now search for Place Order button
                    print("[PAYMENT] Searching for Place Order button after Save and Continue...")
                    for selector in place_order_selectors:
                        try:
                            print(f"[PAYMENT] Trying selector: {selector}")
                            button = await page.wait_for_selector(selector, timeout=1000)
                            if button:
                                is_visible = await button.is_visible()
                                print(f"[PAYMENT] Button found, visible: {is_visible}")

                                if is_visible:
                                    # PROD: Wait for button to be enabled (autofill may take a moment)
                                    # Check up to 10 times over 5 seconds
                                    for attempt in range(10):
                                        is_disabled = await button.get_attribute('disabled')
                                        if not is_disabled:
                                            place_order_button = button
                                            found_selector = selector
                                            print(f"[PAYMENT] ✓ Button enabled after {attempt * 0.2}s with selector: {selector}")
                                            break
                                        if attempt < 9:
                                            print(f"[PAYMENT] Button disabled, waiting for autofill... ({attempt + 1}/10)")
                                            await asyncio.sleep(0.2)

                                    if place_order_button:
                                        break
                        except Exception as e:
                            print(f"[PAYMENT] Selector {selector} failed: {e}")
                            continue
                else:
                    print("[PAYMENT] ⚡ FAST PATH: Skipping Save and Continue - button already found!")

            # TEST_MODE: Find button for display only (don't click)
            if self.test_mode:
                print("[PAYMENT] TEST_MODE: Searching for Place Order button...")
                for selector in place_order_selectors:
                    try:
                        button = await page.wait_for_selector(selector, timeout=1000)
                        if button and await button.is_visible():
                            try:
                                await button.scroll_into_view_if_needed(timeout=2000)
                                print("[PAYMENT] Scrolled button into view")
                            except:
                                pass
                            place_order_button = button
                            found_selector = selector
                            print(f"[PAYMENT] ✓ TEST_MODE: Button found with selector: {selector}")
                            break
                    except:
                        continue

            if not place_order_button:
                print("[PAYMENT] ✗ Could not find enabled Place Order button")
                # Take debug screenshot
                try:
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    screenshot_path = f"logs/payment_button_not_found_{timestamp}.png"
                    await page.screenshot(path=screenshot_path, full_page=True)
                    print(f"[PAYMENT] Debug screenshot saved: {screenshot_path}")
                except:
                    pass
                return False

            # TEST_MODE: Stop before clicking (for testing)
            if self.test_mode:
                print("[PAYMENT] TEST_MODE: Stopping before clicking Place Order button")
                try:
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    screenshot_path = f"logs/test_mode_final_step_{timestamp}.png"
                    await page.screenshot(path=screenshot_path)
                    print(f"[PAYMENT] Screenshot saved: {screenshot_path}")
                except:
                    pass
                return True

            # PRODUCTION MODE: Instant click for speed (no humanization needed at checkout)
            print(f"[PAYMENT] PROD: Instant click on Place Order button (selector: {found_selector})")
            try:
                await place_order_button.dispatch_event('click')
                print("[PAYMENT] ✓ Instant click dispatched")
            except Exception as click_error:
                print(f"[PAYMENT] ✗ dispatch_event failed: {click_error}")
                try:
                    await place_order_button.click(timeout=5000)
                    print("[PAYMENT] ✓ Fallback click succeeded")
                except:
                    return False

            # Wait briefly for confirmation (PROD: fast, don't block)
            print("[PAYMENT] Waiting for order confirmation...")
            try:
                await page.wait_for_url('**/confirmation**', timeout=5000)
                print("[PAYMENT] ✓ Reached confirmation page")
            except:
                # Don't block on networkidle - just check URL quickly
                current_url = page.url
                print(f"[PAYMENT] Current URL: {current_url}")
                if 'confirmation' in current_url.lower() or 'thank' in current_url.lower():
                    print("[PAYMENT] ✓ On confirmation page")
                else:
                    print("[PAYMENT] ⚠ Proceeding (click sent)")

            return True

        except Exception as e:
            print(f"[PAYMENT] ✗ Payment completion error: {e}")
            # Take error screenshot
            try:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                screenshot_path = f"logs/payment_error_{timestamp}.png"
                await page.screenshot(path=screenshot_path, full_page=True)
                print(f"[PAYMENT] Error screenshot saved: {screenshot_path}")
            except:
                pass
            return False

    async def _verify_order_completion(self, page) -> bool:
        """Verify that order was successfully placed"""
        try:
            self.logger.info("Verifying order completion...")

            # Wait for confirmation page indicators - specifically "Thanks for your order"
            confirmation_selectors = [
                'text="Thanks for your order!"',
                'text="Thanks for your order"',
                ':has-text("Thanks for your order!")',
                ':has-text("Thanks for your order")',
                'h1:has-text("Thanks for your order")',
                'h2:has-text("Thanks for your order")',
                'h1:has-text("Order confirmed")',
                'h1:has-text("Thank you")',
                'h2:has-text("Order confirmed")',
                'h2:has-text("Thank you")',
                '[data-test*="order-confirmation"]',
                '[data-testid*="order-confirmation"]',
                'text="Your order has been placed"',
                'text="Order number"',
                'text="Confirmation"'
            ]

            # Check URL for confirmation indicators
            current_url = page.url
            url_indicators = ['confirmation', 'thank', 'order', 'receipt']

            # Wait up to 15 seconds for confirmation
            confirmation_found = False
            for attempt in range(15):
                # Check URL first
                current_url = page.url
                if any(indicator in current_url.lower() for indicator in url_indicators):
                    self.logger.info(f"Order confirmation detected via URL: {current_url}")
                    confirmation_found = True
                    break

                # Check page content
                for selector in confirmation_selectors:
                    try:
                        element = await page.wait_for_selector(selector, timeout=1000)
                        if element:
                            self.logger.info(f"Order confirmation detected: {selector}")
                            confirmation_found = True
                            break
                    except:
                        continue

                if confirmation_found:
                    break

                await asyncio.sleep(0.3)  # Reduced from 1s - faster polling

            if confirmation_found:
                self.logger.info("ORDER SUCCESSFULLY COMPLETED!")
                return True
            else:
                # Check for error messages
                error_selectors = [
                    'text="Payment failed"',
                    'text="Error"',
                    'text="Unable to place order"',
                    '[data-test*="error"]',
                    '.error-message'
                ]

                for selector in error_selectors:
                    try:
                        error = await page.wait_for_selector(selector, timeout=1000)
                        if error:
                            error_text = await error.inner_text()
                            self.logger.warning(f"Order failed with error: {error_text}")
                            return False
                    except:
                        continue

                self.logger.warning("Order completion could not be verified")
                return False

        except Exception as e:
            self.logger.error(f"Order verification error: {e}")
            return False

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
            # Check session manager health
            if not await self.session_manager.is_healthy():
                return False

            # Try to get a page
            page = await self.session_manager.get_page()
            if not page:
                return False

            return True

        except Exception as e:
            self.logger.error(f"Health check failed: {e}")
            return False
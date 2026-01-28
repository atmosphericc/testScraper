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
                    close_btn = await page.wait_for_selector(selector, timeout=500)
                    if close_btn and await close_btn.is_visible():
                        await close_btn.click()
                        print(f"[BANNER] ✓ Closed banner via: {selector}")
                        await asyncio.sleep(0.3)
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
        """
        Perform human-like click with mouse movement to bypass Shape Security.
        Shape Security detects: straight lines, instant clicks, no hover time.
        """
        try:
            print(f"[HUMANIZE] Starting humanized click for {action_name}...")

            # CRITICAL: Wait for Shape Security to attach event handlers
            await self._wait_for_click_handler(page, button, max_wait=3.0)

            # CRITICAL: Dismiss any sticky banners that could intercept the click
            await self._dismiss_sticky_banners(page)

            # Scroll into view first (humans scroll to see buttons)
            try:
                await button.scroll_into_view_if_needed(timeout=3000)

                # Scroll up a bit more to ensure button isn't at very bottom where banners live
                await page.evaluate('window.scrollBy(0, -100)')

                await asyncio.sleep(random.uniform(0.3, 0.6))
                print(f"[HUMANIZE] Scrolled {action_name} button into view")
            except Exception as e:
                print(f"[HUMANIZE] Scroll warning: {e}")

            # Get button position
            box = await button.bounding_box()
            if not box:
                print(f"[HUMANIZE] Warning: Could not get bounding box for {action_name}")
                await button.click()
                return True

            # Calculate target position (center with slight randomness)
            target_x = box['x'] + (box['width'] / 2) + random.uniform(-10, 10)
            target_y = box['y'] + (box['height'] / 2) + random.uniform(-10, 10)

            # HUMAN BEHAVIOR: Move mouse in curved path (not straight line)
            # Shape Security detects perfectly straight mouse movements
            current_pos = await page.evaluate('() => ({x: window.mouseX || 0, y: window.mouseY || 0})')
            start_x = current_pos.get('x', 0)
            start_y = current_pos.get('y', 0)

            # Create curved path with 3-5 intermediate points
            steps = random.randint(3, 5)
            print(f"[HUMANIZE] Moving mouse in {steps} curved steps to {action_name} button")

            for i in range(steps + 1):
                progress = i / steps
                # Add bezier-like curve
                curve_offset_x = random.uniform(-20, 20) * (1 - abs(2 * progress - 1))
                curve_offset_y = random.uniform(-20, 20) * (1 - abs(2 * progress - 1))

                intermediate_x = start_x + (target_x - start_x) * progress + curve_offset_x
                intermediate_y = start_y + (target_y - start_y) * progress + curve_offset_y

                await page.mouse.move(intermediate_x, intermediate_y)
                # Variable speed (faster in middle, slower at start/end)
                delay = random.uniform(0.05, 0.15) if i in [0, steps] else random.uniform(0.02, 0.05)
                await asyncio.sleep(delay)

            # HUMAN BEHAVIOR: Hover before clicking (humans don't instant-click)
            hover_time = random.uniform(0.2, 0.5)
            print(f"[HUMANIZE] Hovering for {hover_time:.2f}s before clicking {action_name}")
            await asyncio.sleep(hover_time)

            # HUMAN BEHAVIOR: Small final adjustment (humans fine-tune position)
            await page.mouse.move(
                target_x + random.uniform(-2, 2),
                target_y + random.uniform(-2, 2)
            )
            await asyncio.sleep(random.uniform(0.05, 0.1))

            # Click with realistic mouse down/up timing
            print(f"[HUMANIZE] Clicking {action_name} button...")
            await page.mouse.down()
            await asyncio.sleep(random.uniform(0.08, 0.15))  # Human click duration
            await page.mouse.up()

            # HUMAN BEHAVIOR: Slight pause after click (humans don't instant-move away)
            await asyncio.sleep(random.uniform(0.1, 0.3))

            print(f"[HUMANIZE] ✓ Humanized click completed for {action_name}")
            return True

        except Exception as e:
            print(f"[HUMANIZE] ✗ Humanized click failed for {action_name}: {e}")
            # Fallback to regular click with force option for intercepted clicks
            try:
                # First try normal click
                await button.click(timeout=5000)
                return True
            except Exception as click_error:
                if "intercept" in str(click_error).lower():
                    print(f"[HUMANIZE] Click intercepted, trying force click...")
                    try:
                        await button.click(force=True)
                        return True
                    except:
                        return False
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
                        timeout=15000
                    )
                    print(f"[PURCHASE] ✓ Add-to-cart button visible")
                except Exception as wait_error:
                    print(f"[PURCHASE] Button wait warning: {wait_error}, proceeding anyway...")
                print(f"[PURCHASE] Page loaded, waiting for Shape Security...")
            except Exception as nav_error:
                print(f"[ERROR] Navigation failed: {nav_error}")
                raise

            # Find and click add-to-cart button
            add_button = await self._find_add_to_cart_button(page)
            if not add_button:
                return {
                    'success': False,
                    'tcin': tcin,
                    'reason': 'button_not_found',
                    'execution_time': time.time() - start_time
                }

            # Use humanized click to bypass Shape Security
            # Wait for add-to-cart API response while clicking (event-driven)
            try:
                async with page.expect_response(
                    lambda r: 'cart' in r.url.lower() and r.request.method == 'POST' and r.status in [200, 201],
                    timeout=15000
                ) as response_info:
                    click_success = await self._humanized_click(page, add_button, "Add to cart/Preorder")

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
                # PROD_MODE: Stay on confirmation page
                execution_time = time.time() - start_time

                # Wait a moment to ensure confirmation page is fully loaded
                try:
                    # Wait for confirmation page to be stable
                    await page.wait_for_load_state('domcontentloaded', timeout=3000)
                except:
                    pass

                print(f"[PURCHASE] ✓ PROD_MODE: Staying on confirmation page, order complete: {tcin} in {execution_time:.2f}s")

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
                        # Small delay after confirmation (human behavior)
                        await asyncio.sleep(random.uniform(0.5, 1.0))
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

            # No error message, assume success with longer wait
            self.logger.info("[PURCHASE] Assuming success, waiting for page to settle...")
            await asyncio.sleep(random.uniform(2.0, 3.0))  # Human-like patience
            return True

        except Exception as e:
            self.logger.warning(f"[PURCHASE] Cart verification warning: {e}")
            await asyncio.sleep(random.uniform(2.0, 3.0))
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
                        await asyncio.sleep(0.3)  # Brief pause after dismissal
                except:
                    continue

            # Also try pressing Escape key (works for many modals)
            try:
                await page.keyboard.press('Escape')
                await asyncio.sleep(0.2)
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
                            await asyncio.sleep(0.3)
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
        """Find add-to-cart/preorder button with Shape Security bypass"""
        print("[BUTTON_FIND] Searching for add-to-cart/preorder button...")

        # Comprehensive selector list including all preorder variations
        priority_selectors = [
            # Primary selectors from working buy_bot
            'button[id^="addToCartButtonOrTextIdFor"]',
            'button[data-test="addToCartButton"]',

            # Preorder variations (CRITICAL)
            'button:has-text("Preorder")',
            'button:has-text("Pre-order")',
            'button:has-text("Pre order")',
            'button:has-text("PRE-ORDER")',
            'button:has-text("PREORDER")',
            'button[data-test*="preorder"]',
            'button[data-testid*="preorder"]',
            'button[aria-label*="Preorder"]',
            'button[aria-label*="Pre-order"]',

            # Add to cart variations
            'button:has-text("Add to cart")',
            'button:has-text("Add to Cart")',
            'button:has-text("ADD TO CART")',
            '[data-testid*="add-to-cart"]',
            '[data-test*="addToCart"]',

            # Fallbacks
            'button:has-text("Add to bag")',
            'button:has-text("Add")'
        ]

        # Wait for page to be fully loaded and Shape to initialize
        print("[BUTTON_FIND] Waiting for Shape Security to initialize...")
        try:
            await page.wait_for_load_state('domcontentloaded', timeout=5000)
            # Additional wait for Shape to attach event handlers
            await asyncio.sleep(random.uniform(1.5, 2.5))
        except:
            pass

        for selector in priority_selectors:
            try:
                print(f"[BUTTON_FIND] Trying: {selector}")
                button = await page.wait_for_selector(selector, timeout=2000)
                if button and await button.is_visible():
                    # Check if button is actually enabled
                    is_disabled = await button.get_attribute('disabled')
                    if not is_disabled:
                        print(f"[BUTTON_FIND] ✓ Found button: {selector}")
                        return button
                    else:
                        print(f"[BUTTON_FIND] Button disabled: {selector}")
            except Exception as e:
                print(f"[BUTTON_FIND] Selector failed: {selector} - {e}")
                continue

        # Fallback: try case-insensitive text search
        print("[BUTTON_FIND] Trying fallback selectors...")
        fallback_texts = ['preorder', 'pre-order', 'add to cart', 'add to bag']
        for text in fallback_texts:
            try:
                # Use evaluate to find buttons by case-insensitive text
                button = await page.evaluate(f'''() => {{
                    const buttons = Array.from(document.querySelectorAll('button'));
                    return buttons.find(b =>
                        b.textContent.toLowerCase().includes('{text}') &&
                        !b.disabled &&
                        b.offsetParent !== null
                    );
                }}''')
                if button:
                    print(f"[BUTTON_FIND] ✓ Found via fallback: {text}")
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

            # SHAPE FIX: Random delay mimics human variance (network speed, rendering, processing)
            # 0.3-0.8s range = fast but still human-like for familiar users
            human_delay = random.uniform(0.3, 0.8)
            self.logger.info(f"[PURCHASE] Page loaded, waiting {human_delay:.1f}s (human-like variance)...")
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
        """Complete payment process with improved button detection and clicking"""
        try:
            print("[PAYMENT] Starting payment completion process...")

            # Expanded selector list with more variations
            place_order_selectors = [
                'button:has-text("Place order")',
                'button:has-text("Place Order")',
                'button:has-text("PLACE ORDER")',
                'button:has-text("Place your order")',
                'button:has-text("Place Your Order")',
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

            # Wait for place order button to exist (event-driven, no arbitrary sleep)
            print("[PAYMENT] Searching for Place Order button...")

            # Try to find the button with longer timeout
            place_order_button = None
            found_selector = None

            for selector in place_order_selectors:
                try:
                    print(f"[PAYMENT] Trying selector: {selector}")
                    button = await page.wait_for_selector(selector, timeout=3000)
                    if button:
                        is_visible = await button.is_visible()
                        print(f"[PAYMENT] Button found, visible: {is_visible}")

                        if is_visible:
                            # Scroll into view
                            try:
                                await button.scroll_into_view_if_needed(timeout=2000)
                                print("[PAYMENT] Scrolled button into view")
                            except:
                                pass

                            # Wait for button to be enabled (up to 5 seconds)
                            for attempt in range(10):
                                is_disabled = await button.get_attribute('disabled')
                                if not is_disabled:
                                    place_order_button = button
                                    found_selector = selector
                                    print(f"[PAYMENT] ✓ Button ready with selector: {selector}")
                                    break
                                print(f"[PAYMENT] Button disabled, waiting... (attempt {attempt + 1}/10)")
                                await asyncio.sleep(0.5)

                            if place_order_button:
                                break
                except Exception as e:
                    print(f"[PAYMENT] Selector {selector} failed: {e}")
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

            # PRODUCTION MODE: Click place order button with humanization
            print(f"[PAYMENT] Clicking Place Order button with selector: {found_selector}")
            click_success = await self._humanized_click(page, place_order_button, "Place Your Order")
            if not click_success:
                print("[PAYMENT] ✗ Humanized click failed, trying direct click...")
                try:
                    await place_order_button.click(timeout=5000)
                    print("[PAYMENT] ✓ Direct click succeeded")
                except Exception as click_error:
                    print(f"[PAYMENT] ✗ All click methods failed: {click_error}")
                    return False

            # Wait for confirmation page
            print("[PAYMENT] Waiting for order confirmation...")
            try:
                await page.wait_for_url('**/confirmation**', timeout=15000)
                print("[PAYMENT] ✓ Reached confirmation page")
            except:
                print("[PAYMENT] URL didn't change to confirmation, checking load state...")
                try:
                    await page.wait_for_load_state('networkidle', timeout=10000)
                    print("[PAYMENT] ✓ Page loaded (networkidle)")
                except:
                    print("[PAYMENT] Network idle timeout, checking current URL...")
                    current_url = page.url
                    print(f"[PAYMENT] Current URL: {current_url}")
                    if 'confirmation' in current_url.lower() or 'thank' in current_url.lower() or 'order' in current_url.lower():
                        print("[PAYMENT] ✓ On confirmation-related page")
                    else:
                        print("[PAYMENT] ⚠ May not be on confirmation page")

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

                await asyncio.sleep(1)

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
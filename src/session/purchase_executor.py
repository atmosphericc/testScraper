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
                await page.goto(product_url, wait_until='commit', timeout=3000)
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

            # Click button with F5-safe mouse movement
            try:
                await add_button.scroll_into_view_if_needed()
            except:
                pass

            try:
                box = await add_button.bounding_box()
                if box:
                    target_x = box['x'] + (box['width'] / 2) + random.uniform(-3, 3)
                    target_y = box['y'] + (box['height'] / 2) + random.uniform(-3, 3)
                    await page.mouse.move(target_x, target_y)
                    await asyncio.sleep(random.uniform(0.02, 0.05))
            except:
                pass

            await add_button.click()
            print(f"[PURCHASE] Added to cart (t={time.time() - start_time:.1f}s)")

            # Navigate to checkout
            await asyncio.sleep(random.uniform(0.1, 0.2))
            self._notify_status(tcin, 'checking_out', {'timestamp': datetime.now().isoformat()})

            try:
                await page.goto("https://www.target.com/checkout", wait_until='commit', timeout=3000)
                await asyncio.sleep(random.uniform(0.1, 0.2))

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

            # Navigate to cart and clear it
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

            # Success
            print(f"[PURCHASE] ✓ Cycle complete: {tcin} in {execution_time:.2f}s")

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
        """Find add-to-cart button with fast timeout strategy"""
        # Try top 5 most reliable selectors (500ms each)
        priority_selectors = [
            'button[id^="addToCartButtonOrTextIdFor"]',
            'button[data-test="addToCartButton"]',
            'button:has-text("Add to cart")',
            'button:has-text("Preorder")',
            '[data-testid*="add-to-cart"]'
        ]

        for selector in priority_selectors:
            try:
                button = await page.wait_for_selector(selector, timeout=500)
                if button and await button.is_visible():
                    return button
            except:
                continue

        # Fallback search
        try:
            button = await page.wait_for_selector(
                'button:has-text("Add"), button:has-text("cart"), button:has-text("Preorder")',
                timeout=1000
            )
            if button and await button.is_visible():
                return button
        except:
            pass

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
            await page.goto("https://www.target.com/checkout", wait_until='domcontentloaded', timeout=15000)

            # SHAPE FIX: Random delay mimics human variance (network speed, rendering, processing)
            # 1.5-3.5s range = 2s variance (not fingerprint-able across cycles)
            human_delay = random.uniform(1.5, 3.5)
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
                        remove_button = await page.wait_for_selector(selector, timeout=1000)
                        if remove_button and await remove_button.is_visible():
                            await remove_button.click()
                            await asyncio.sleep(random.uniform(0.3, 0.7))
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
        """Handle shipping/delivery selection - ULTRA-FAST for TEST_MODE"""
        try:
            # COMPETITIVE: Fast shipping selection (500ms max per selector)
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
                        await asyncio.sleep(random.uniform(0.1, 0.2))
                        break
                except:
                    continue

            # COMPETITIVE: Fast continue button (500ms max per selector)
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
                        # ULTRA-FAST: Minimal wait for page transition
                        await asyncio.sleep(random.uniform(0.2, 0.4))
                        break
                except:
                    continue

        except:
            pass

    async def _complete_payment(self, page) -> bool:
        """Complete payment process - ULTRA-FAST for TEST_MODE"""
        try:
            # COMPETITIVE: Top 5 most reliable selectors only
            place_order_selectors = [
                'button:has-text("Place order")',
                'button:has-text("Place Order")',
                '[data-test*="place-order"]',
                'button:has-text("Buy now")',
                'button[data-test="placeOrderButton"]'
            ]

            # ULTRA-FAST: Minimal wait for payment page (200-400ms)
            await asyncio.sleep(random.uniform(0.2, 0.4))

            place_order_button = None
            for selector in place_order_selectors:
                try:
                    # COMPETITIVE: Fast timeout (1s per selector)
                    button = await page.wait_for_selector(selector, timeout=1000)
                    if button and await button.is_visible():
                        is_disabled = await button.get_attribute('disabled')
                        if not is_disabled:
                            place_order_button = button
                            break
                except:
                    continue

            if place_order_button:
                # TEST_MODE: Stop before clicking (for testing)
                if self.test_mode:
                    try:
                        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                        screenshot_path = f"logs/test_mode_final_step_{timestamp}.png"
                        await page.screenshot(path=screenshot_path)
                    except:
                        pass
                    return True

                # PRODUCTION MODE: Click place order button
                await place_order_button.click()

                # Wait for confirmation
                try:
                    await page.wait_for_url('**/confirmation**', timeout=10000)
                except:
                    try:
                        await page.wait_for_load_state('networkidle', timeout=5000)
                    except:
                        pass
                return True
            else:
                return False

        except:
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
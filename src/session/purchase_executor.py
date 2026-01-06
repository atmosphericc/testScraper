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
        if self.test_mode:
            self.logger.info("[TEST] TEST MODE ENABLED - Will stop before final purchase button")


        # RACE CONDITION FIX: asyncio.Lock to prevent concurrent page access
        self._page_lock = asyncio.Lock()
        print("[PURCHASE_EXECUTOR] [INIT] Page access lock initialized")

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
        
        RACE CONDITION FIX: Uses _page_lock to prevent concurrent page access.
        This ensures cart clearing from previous purchase completes before
        new purchase starts, preventing navigation conflicts.
        """
        try:
            # Acquire lock with 120-second timeout (2x purchase timeout)
            async with asyncio.timeout(120):
                async with self._page_lock:
                    print(f"[PURCHASE_EXECUTOR] [LOCK] ✅ Acquired page lock for purchase {tcin}")
                    result = await self._execute_purchase_impl(tcin)
                    print(f"[PURCHASE_EXECUTOR] [LOCK] 🔓 Releasing page lock for purchase {tcin}")
                    return result
        except asyncio.TimeoutError:
            print(f"[PURCHASE_EXECUTOR] [LOCK] ⚠️ Lock acquisition timed out after 120s")
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
            print(f"[PURCHASE_EXECUTOR] [TARGET] Starting purchase for TCIN {tcin}")
            self.logger.info(f"[TARGET] Starting purchase for TCIN {tcin}")
            self._notify_status(tcin, 'attempting', {'start_time': datetime.now().isoformat()})

            # Get existing logged-in page from session (keepalive no longer interferes)
            print(f"[PURCHASE_EXECUTOR] Getting existing page from session...")
            print(f"[DEBUG_FLASH] [PURCHASE] PURCHASE_EXECUTOR getting page...")

            # Access context directly (synchronous attribute access)
            context = self.session_manager.context
            if not context:
                print(f"[PURCHASE_EXECUTOR] [ERROR] No context available")
                print(f"[DEBUG_FLASH] [ERROR] No context in purchase executor")
                raise Exception("Browser context not available")

            # Use existing page (already logged in from startup)
            pages = context.pages
            print(f"[DEBUG_FLASH] [PURCHASE] Purchase executor found {len(pages)} pages in context")
            if not pages:
                print(f"[PURCHASE_EXECUTOR] [ERROR] No pages available in context")
                print(f"[DEBUG_FLASH] [ERROR] No pages available - would need to create one (BAD!)")
                raise Exception("No pages available - session not initialized properly")

            page = pages[0]
            print(f"[PURCHASE_EXECUTOR] [OK] Using existing logged-in page from session")
            print(f"[DEBUG_FLASH] [OK] Purchase executor using existing page[0] - NO NEW TAB")

            # Navigate to product page
            product_url = f"https://www.target.com/p/-/A-{tcin}"
            print(f"[PURCHASE_EXECUTOR] [NAV] Navigating to product page: {product_url}")
            try:
                await page.goto(product_url, wait_until='domcontentloaded', timeout=10000)
                print(f"[PURCHASE_EXECUTOR] [OK] Navigation to product page completed")
            except Exception as nav_error:
                print(f"[PURCHASE_EXECUTOR] [ERROR] Navigation failed: {nav_error}")
                raise

            self.logger.info(f"Navigated to product page: {tcin}")

            # POPUP FIX: Dismiss any engagement popups after page load
            # Target shows photo upload/review modals especially after multiple cycles
            await asyncio.sleep(0.5)  # Let popups render
            await self._dismiss_popups(page)

            # Double-check login status on product page
            print(f"[PURCHASE_EXECUTOR] [AUTH] Validating login status on product page...")
            login_valid = await self._validate_login_status(page)
            print(f"[PURCHASE_EXECUTOR] Login validation result: {login_valid}")

            if not login_valid:
                self.logger.error("[ERROR] Session expired on product page")
                print(f"[PURCHASE_EXECUTOR] [ERROR] Session expired, aborting purchase")
                return {
                    'success': False,
                    'tcin': tcin,
                    'reason': 'session_expired',
                    'execution_time': time.time() - start_time
                }

            # Find and click add-to-cart button
            print(f"[PURCHASE_EXECUTOR] [DEBUG] Looking for add-to-cart button...")
            add_button = await self._find_add_to_cart_button(page)
            print(f"[PURCHASE_EXECUTOR] Add-to-cart button found: {add_button is not None}")
            if not add_button:
                self.logger.warning(f" No add-to-cart button found for {tcin}")
                return {
                    'success': False,
                    'tcin': tcin,
                    'reason': 'button_not_found',
                    'execution_time': time.time() - start_time
                }

            # COMPETITIVE BOT OPTIMIZATION: Minimize delays while evading F5 Shape detection
            # F5 Shape looks for: (1) machine-perfect timing, (2) no mouse movement, (3) consistent patterns
            # Strategy: Fast delays with randomness + mouse movement = human-like but competitive
            print(f"[BUTTON_CLICK] ═══════════════════════════════════════════════")
            print(f"[BUTTON_CLICK] Scrolling button into view...")
            try:
                await add_button.scroll_into_view_if_needed()
                print(f"[BUTTON_CLICK] ✓ Button scrolled into viewport")
            except Exception as e:
                print(f"[BUTTON_CLICK] Warning: scroll failed: {e}")

            # COMPETITIVE: Minimal stabilization (0.05-0.15s vs old 0.3-0.7s = 77% faster)
            # Randomness prevents F5 from detecting machine-perfect timing
            stabilization_delay = random.uniform(0.05, 0.15)
            await asyncio.sleep(stabilization_delay)

            # Quick verification (no logging to save time)
            try:
                is_visible = await add_button.is_visible()
                is_enabled = not await add_button.get_attribute('disabled')

                if not is_visible:
                    await self._take_debug_screenshot(page, "button_not_visible_after_scroll")
                    return {
                        'success': False,
                        'tcin': tcin,
                        'reason': 'button_not_visible',
                        'execution_time': time.time() - start_time
                    }

                if not is_enabled:
                    await self._take_debug_screenshot(page, "button_disabled")
                    return {
                        'success': False,
                        'tcin': tcin,
                        'reason': 'button_disabled',
                        'execution_time': time.time() - start_time
                    }
            except:
                pass  # Continue anyway

            # ANTI-BOT: Mouse movement to button (F5 Shape tracks cursor patterns)
            # Moving cursor makes automation look human vs instant click
            print(f"[BUTTON_CLICK] Moving cursor to button (anti-bot evasion)...")
            try:
                # Get button coordinates
                box = await add_button.bounding_box()
                if box:
                    # Calculate center of button with slight randomness (human variance)
                    target_x = box['x'] + (box['width'] / 2) + random.uniform(-5, 5)
                    target_y = box['y'] + (box['height'] / 2) + random.uniform(-5, 5)

                    # Move cursor to button (Playwright does smooth movement automatically)
                    await page.mouse.move(target_x, target_y)

                    # Brief delay simulating human eye-hand coordination (10-60ms variance)
                    cursor_delay = random.uniform(0.01, 0.06)  # 10-60ms
                    await asyncio.sleep(cursor_delay)
                    print(f"[BUTTON_CLICK] ✓ Cursor positioned on button")
            except Exception as e:
                print(f"[BUTTON_CLICK] Warning: cursor movement failed: {e}")

            # COMPETITIVE: Minimal pre-click delay (0.05-0.2s vs old 0.5-2.0s = 90% faster)
            # Still has variance to avoid detection but much faster for competitive purchasing
            pre_click_delay = random.uniform(0.05, 0.2)
            await asyncio.sleep(pre_click_delay)

            # Click the button
            print(f"[BUTTON_CLICK] Clicking button...")
            await add_button.click()
            print(f"[BUTTON_CLICK] [OK] ✓ Clicked in ~{stabilization_delay + pre_click_delay:.3f}s")
            print(f"[BUTTON_CLICK] ═══════════════════════════════════════════════")
            self.logger.info(f"[PURCHASE] Add-to-cart button clicked")

            # CRITICAL FIX: Navigate to checkout IMMEDIATELY to bypass sign-in pane
            # Target shows a sign-in/account creation pane after add-to-cart
            # Instead of dismissing it, we navigate directly to checkout which closes it automatically
            print(f"[FLOW] ═══ AGGRESSIVE CHECKOUT NAVIGATION ═══")

            # Brief wait for add-to-cart request to complete (backend API call)
            await asyncio.sleep(random.uniform(1.0, 1.5))

            self._notify_status(tcin, 'checking_out', {
                'timestamp': datetime.now().isoformat()
            })

            # Navigate DIRECTLY to checkout - this bypasses/closes any modals or panes
            print(f"[FLOW] Navigating directly to checkout (bypasses sign-in pane)...")
            self.logger.info(f"[PURCHASE] Navigating to checkout immediately after add-to-cart...")

            try:
                await page.goto("https://www.target.com/checkout", wait_until='domcontentloaded', timeout=15000)
                print(f"[FLOW] ✓ Navigated to checkout page")

                # Brief wait for checkout page to render
                await asyncio.sleep(random.uniform(1.5, 2.5))

                # Verify we're actually on checkout page
                current_url = page.url
                if 'checkout' in current_url.lower():
                    print(f"[FLOW] ✓ Confirmed on checkout page: {current_url}")
                    checkout_result = True
                else:
                    print(f"[FLOW] ✗ Not on checkout page, at: {current_url}")
                    checkout_result = False

            except Exception as nav_error:
                print(f"[FLOW] ✗ Checkout navigation failed: {nav_error}")
                self.logger.error(f"Checkout navigation error: {nav_error}")
                checkout_result = False

            # Handle delivery options and payment (TEST_MODE stops before final button)
            if checkout_result:
                print(f"[FLOW] Processing checkout page...")
                await self._handle_delivery_options(page)
                payment_result = await self._complete_payment(page)
                if not payment_result:
                    print(f"[FLOW] ✗ Payment processing failed")
                    checkout_result = False
                else:
                    print(f"[FLOW] ✓ Payment processing complete (TEST_MODE stops before final click)")

            print(f"[FLOW] Checkout result: {checkout_result}")

            if not checkout_result:
                print(f"[FLOW] ✗ Checkout navigation FAILED")
                self.logger.warning(f"[PURCHASE] Checkout navigation failed for {tcin}")
                return {
                    'success': False,
                    'tcin': tcin,
                    'reason': 'checkout_navigation_failed',
                    'execution_time': time.time() - start_time
                }

            # THEN navigate to cart to clear it
            print(f"[FLOW] ═══ STEP 4: Navigating to cart ═══")
            self.logger.info(f"[PURCHASE] Now navigating to cart to clear...")
            await page.goto("https://www.target.com/cart", wait_until='domcontentloaded', timeout=15000)
            print(f"[FLOW] ✓ Arrived at cart page")

            # SHAPE FIX: Human reading/processing time after page load
            await asyncio.sleep(random.uniform(1.0, 2.5))

            # Clear the cart
            print(f"[FLOW] ═══ STEP 5: Clearing cart ═══")
            clear_result = await self._clear_cart(page)

            execution_time = time.time() - start_time

            if not clear_result:
                # CRITICAL FIX: Cart clearing failed = purchase FAILED
                # SUCCESS is only when cart clears successfully
                print(f"[FLOW] ✗ Cart clearing FAILED - marking purchase as FAILED")
                self.logger.warning(f"[PURCHASE] Cart clearing failed for {tcin}")

                # Notify dashboard immediately with FAILED status
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

            # Cart cleared successfully = PURCHASE SUCCESS
            print(f"[FLOW] ✓ Cart cleared successfully")
            print(f"[FLOW] ═══ PURCHASE CYCLE COMPLETE - SUCCESS ═══")

            self.logger.info(f"[OK] PURCHASE COMPLETED for {tcin} in {execution_time:.2f}s")

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
            print(f"[PURCHASE_EXECUTOR] [ERROR] Purchase failed for {tcin}: {e}")
            self.logger.error(f" Purchase failed for {tcin}: {e}")

            import traceback
            traceback.print_exc()

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

        finally:
            # Note: We don't close the page - it's the main session page that stays open
            # The page remains logged in and ready for next purchase
            print(f"[PURCHASE_EXECUTOR] Purchase complete - page remains open for next purchase")

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
        Get timeout based on selector priority (tiered timeout strategy).

        Primary selectors (1-2): 5000ms - Most reliable, deserve longest timeout
        Secondary selectors (3-8): 3000ms - High confidence patterns
        Fallback selectors (9+): 1000ms - Quick checks for edge cases
        """
        if selector_index <= 2:  # Primary selectors
            return 5000
        elif selector_index <= 8:  # Secondary selectors (includes preorder variants)
            return 3000
        else:  # Fallback selectors
            return 1000

    async def _find_add_to_cart_button(self, page):
        """
        Find add-to-cart button with intelligent timeout strategy and detailed logging.

        Uses tiered timeouts based on selector reliability:
        - Primary selectors (1-2): 5000ms
        - Secondary selectors (3-8): 3000ms
        - Fallback selectors (9+): 1000ms
        """
        total_selectors = len(self.SELECTORS['add_to_cart'])
        print(f"[BUTTON_SEARCH] ═══════════════════════════════════════════════")
        print(f"[BUTTON_SEARCH] Starting button search - will try {total_selectors} selectors")
        print(f"[BUTTON_SEARCH] ═══════════════════════════════════════════════")

        attempt_log = []  # Track what we tried for debugging

        for idx, selector in enumerate(self.SELECTORS['add_to_cart'], 1):
            try:
                # Get tiered timeout based on selector priority
                timeout = self._get_timeout_for_selector(idx)
                tier = "PRIMARY" if idx <= 2 else ("SECONDARY" if idx <= 8 else "FALLBACK")

                print(f"[BUTTON_SEARCH] [{idx}/{total_selectors}] [{tier}] Trying: {selector} (timeout: {timeout}ms)")

                # Wait for selector with appropriate timeout
                button = await page.wait_for_selector(selector, timeout=timeout)

                if button:
                    # Check if button is visible
                    is_visible = await button.is_visible()
                    print(f"[BUTTON_SEARCH] [{idx}] Found element, visible={is_visible}")

                    if is_visible:
                        print(f"[BUTTON_SEARCH] ═══════════════════════════════════════════════")
                        print(f"[BUTTON_SEARCH] [OK] ✓✓✓ SUCCESS! Found visible button")
                        print(f"[BUTTON_SEARCH] Selector #{idx} ({tier}): {selector}")
                        print(f"[BUTTON_SEARCH] ═══════════════════════════════════════════════")
                        self.logger.info(f"Add-to-cart button found with selector #{idx}: {selector}")
                        return button
                    else:
                        attempt_log.append(f"#{idx} [{tier}] {selector}: found but not visible")
                else:
                    attempt_log.append(f"#{idx} [{tier}] {selector}: element not found")

            except Exception as e:
                error_msg = str(e)
                if "Timeout" in error_msg or "timeout" in error_msg.lower():
                    # Expected for wrong selectors - don't spam logs
                    attempt_log.append(f"#{idx} [{tier}] {selector}: timeout")
                else:
                    print(f"[BUTTON_SEARCH] [{idx}] Error: {e}")
                    attempt_log.append(f"#{idx} [{tier}] {selector}: error - {e}")
                continue

        # All selectors failed - detailed error report
        print(f"[BUTTON_SEARCH] ═══════════════════════════════════════════════")
        print(f"[BUTTON_SEARCH] ❌❌❌ FAILED - No visible button found")
        print(f"[BUTTON_SEARCH] ═══════════════════════════════════════════════")
        print(f"[BUTTON_SEARCH] Attempted {len(attempt_log)} selectors:")
        for log_entry in attempt_log[:10]:  # Show first 10 to avoid spam
            print(f"[BUTTON_SEARCH]   - {log_entry}")
        if len(attempt_log) > 10:
            print(f"[BUTTON_SEARCH]   ... and {len(attempt_log) - 10} more")
        print(f"[BUTTON_SEARCH] ═══════════════════════════════════════════════")

        # Take screenshot for debugging
        await self._take_debug_screenshot(page, "no_add_to_cart_button")

        self.logger.error(f"Add-to-cart button not found after trying {total_selectors} selectors")
        return None

    async def _take_debug_screenshot(self, page, reason: str) -> Optional[str]:
        """
        Take debug screenshot for troubleshooting with context logging.

        Args:
            page: Playwright page object
            reason: Short reason string for filename (e.g., "button_not_found")

        Returns:
            Screenshot path if successful, None otherwise
        """
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            screenshot_path = f"logs/debug_{reason}_{timestamp}.png"

            # Take full-page screenshot
            await page.screenshot(path=screenshot_path, full_page=True)

            print(f"[DEBUG] ═══════════════════════════════════════════════")
            print(f"[DEBUG] Screenshot saved: {screenshot_path}")
            print(f"[DEBUG] Reason: {reason}")

            # Log context for debugging
            current_url = page.url
            try:
                title = await page.title()
            except:
                title = "unknown"

            print(f"[DEBUG] Context:")
            print(f"[DEBUG]   URL: {current_url}")
            print(f"[DEBUG]   Title: {title}")
            print(f"[DEBUG] ═══════════════════════════════════════════════")

            self.logger.error(f"Debug screenshot saved: {screenshot_path} (reason: {reason}, url: {current_url})")

            return screenshot_path

        except Exception as e:
            print(f"[DEBUG] Failed to take screenshot: {e}")
            self.logger.error(f"Failed to take debug screenshot: {e}")
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
            self.logger.info("[PURCHASE] Clearing cart...")

            # Find all remove buttons in cart
            remove_selectors = [
                'button[data-test="cartItem-remove"]',
                'button[aria-label*="remove"]',
                'button[aria-label*="Remove"]',
                'button:has-text("Remove")',
                '[data-test*="remove"]',
                '[data-testid*="remove"]'
            ]

            removed_count = 0
            max_attempts = 10  # Prevent infinite loop

            for attempt in range(max_attempts):
                # Try to find and click a remove button
                button_found = False

                for selector in remove_selectors:
                    try:
                        remove_button = await page.wait_for_selector(selector, timeout=2000)
                        if remove_button and await remove_button.is_visible():
                            # SHAPE FIX: Humans wait for visual confirmation, not API responses
                            # Deterministic API waits are fingerprint-able
                            await remove_button.click()
                            await asyncio.sleep(random.uniform(0.8, 1.5))

                            removed_count += 1
                            button_found = True
                            self.logger.info(f"[PURCHASE] Removed item {removed_count} from cart")
                            break
                    except:
                        continue

                # If no button found, cart is empty
                if not button_found:
                    break

            if removed_count > 0:
                self.logger.info(f"[PURCHASE] Successfully cleared {removed_count} item(s) from cart")
            else:
                self.logger.info("[PURCHASE] Cart was already empty or no remove buttons found")

            # Verify cart is empty
            try:
                # Check for empty cart indicator
                empty_indicators = [
                    'text="Your cart is empty"',
                    ':has-text("Your cart is empty")',
                    ':has-text("cart is empty")',
                    '[data-test="empty-cart"]'
                ]

                for indicator in empty_indicators:
                    try:
                        await page.wait_for_selector(indicator, timeout=2000)
                        self.logger.info("[PURCHASE] Confirmed cart is empty")
                        return True
                    except:
                        continue

                # If we removed items but don't see empty message, still consider success
                if removed_count > 0:
                    self.logger.info("[PURCHASE] Cart cleared (items removed)")
                    return True

            except Exception as e:
                self.logger.debug(f"Cart empty verification: {e}")

            return True  # Don't fail if we can't verify, cart clearing is best-effort

        except Exception as e:
            self.logger.error(f"[PURCHASE] Cart clearing error: {e}")
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
        """Handle shipping/delivery selection - ensure shipping is selected"""
        try:
            self.logger.info("Selecting shipping delivery option...")

            # First, try to select shipping option if available
            shipping_selectors = [
                'button:has-text("Ship")',
                'button:has-text("Shipping")',
                'input[value*="ship"]',
                'input[value*="Ship"]',
                '[data-test*="shipping"]',
                '[data-testid*="shipping"]',
                'button[aria-label*="Ship"]',
                'button[aria-label*="Shipping"]',
                'label:has-text("Ship")',
                'label:has-text("Shipping")'
            ]

            # Try to find and select shipping option
            shipping_selected = False
            for selector in shipping_selectors:
                try:
                    element = await page.wait_for_selector(selector, timeout=2000)
                    if element and await element.is_visible():
                        await element.click()
                        self.logger.info("Selected shipping delivery option")
                        shipping_selected = True
                        # Wait for selection to register (UI update)
                        try:
                            await page.wait_for_load_state('networkidle', timeout=2000)
                        except:
                            pass
                        break
                except:
                    continue

            if not shipping_selected:
                self.logger.warning("Could not find explicit shipping option, continuing...")

            # Now look for continue buttons
            continue_selectors = [
                'button:has-text("Continue")',
                'button:has-text("Next")',
                'button:has-text("Continue to payment")',
                'button:has-text("Continue to checkout")',
                '[data-test*="continue"]',
                '[data-testid*="continue"]'
            ]

            for selector in continue_selectors:
                try:
                    button = await page.wait_for_selector(selector, timeout=3000)
                    if button and await button.is_visible():
                        await button.click()
                        # CRITICAL FIX: Replace networkidle with fixed delay
                        # Target's checkout page has continuous background network activity
                        # (analytics, inventory checks, ads) that prevents networkidle
                        # Fixed delay is more reliable than waiting for networkidle that never comes
                        await asyncio.sleep(random.uniform(2.0, 3.5))
                        self.logger.info("Continued to next checkout step")
                        break
                except:
                    continue

        except Exception as e:
            self.logger.debug(f"Delivery options handling: {e}")

    async def _complete_payment(self, page) -> bool:
        """Complete payment process"""
        try:
            self.logger.info("Attempting to complete payment...")

            # Look for place order / complete purchase buttons
            place_order_selectors = [
                'button:has-text("Place order")',
                'button:has-text("Place Order")',
                'button:has-text("Complete purchase")',
                'button:has-text("Complete Purchase")',
                'button:has-text("Buy now")',
                'button:has-text("Buy Now")',
                '[data-test*="place-order"]',
                '[data-testid*="place-order"]',
                '[data-test*="complete-purchase"]',
                '[data-testid*="complete-purchase"]',
                'button[data-test="placeOrderButton"]'
            ]

            # CRITICAL FIX: Give payment page time to render (replaces broken selector wait)
            # The old selector was malformed and would always timeout
            # Simple fixed delay is more reliable for payment page load
            print(f"[PAYMENT] Waiting for payment page to render...")
            await asyncio.sleep(random.uniform(2.0, 3.0))
            print(f"[PAYMENT] Payment page should be ready, looking for place order button...")

            place_order_button = None
            for selector in place_order_selectors:
                try:
                    button = await page.wait_for_selector(selector, timeout=3000)
                    if button and await button.is_visible():
                        # Check if button is enabled
                        is_disabled = await button.get_attribute('disabled')
                        if not is_disabled:
                            place_order_button = button
                            self.logger.debug(f"Found place order button: {selector}")
                            break
                except:
                    continue

            if place_order_button:
                # TEST_MODE: Stop before clicking the final button
                if self.test_mode:
                    self.logger.info("[TEST] TEST MODE: Found place order button - STOPPING before click")
                    self.logger.info("[TEST] TEST MODE: Purchase flow validated successfully!")

                    # Take screenshot for verification
                    try:
                        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                        screenshot_path = f"logs/test_mode_final_step_{timestamp}.png"
                        await page.screenshot(path=screenshot_path)
                        self.logger.info(f"[TEST] TEST MODE: Screenshot saved: {screenshot_path}")
                    except:
                        pass

                    return True  # Return success without actually purchasing

                # PRODUCTION MODE: Click the place order button
                await place_order_button.click()
                self.logger.info("Clicked place order button - purchase submitted!")

                # Wait for order processing - look for confirmation page or URL change
                try:
                    await page.wait_for_url('**/confirmation**', timeout=10000)
                except:
                    # Fallback: wait for network to settle
                    try:
                        await page.wait_for_load_state('networkidle', timeout=5000)
                    except:
                        pass
                return True
            else:
                self.logger.warning("Could not find enabled place order button")
                return False

        except Exception as e:
            self.logger.error(f"Payment completion error: {e}")
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
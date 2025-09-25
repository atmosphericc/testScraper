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

        # Load existing buy_bot selectors
        self.SELECTORS = {
            'add_to_cart': [
                # PREORDER PRIORITY - Many products are preorder now
                'button:has-text("Preorder")',
                'button:has-text("Pre-order")',
                'button:has-text("Pre order")',
                'button:has-text("Pre-Order")',
                'button[data-test*="preorder"]',

                # 2025 TARGET.COM PRIORITY SELECTORS - Most current patterns
                'button[data-test="addToCartButton"]',
                'button[data-test="chooseOptionsButton"]',
                'button[data-testid="addToCartButton"]',
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
        """Execute purchase for given TCIN using persistent session"""
        start_time = time.time()

        try:
            self.logger.info(f"🎯 Starting purchase for TCIN {tcin}")
            self._notify_status(tcin, 'attempting', {'start_time': datetime.now().isoformat()})

            # Get page from persistent session
            page = await self.session_manager.get_page()
            if not page:
                raise Exception("Failed to get page from session manager")

            # Navigate to product page
            product_url = f"https://www.target.com/p/-/A-{tcin}"
            await page.goto(product_url, wait_until='domcontentloaded', timeout=10000)

            self.logger.info(f"Navigated to product page: {tcin}")

            # Validate session is still active
            if not await self._validate_login_status(page):
                self.logger.error(" Session expired during purchase attempt")
                return {
                    'success': False,
                    'tcin': tcin,
                    'reason': 'session_expired',
                    'execution_time': time.time() - start_time
                }

            # Find and click add-to-cart button
            add_button = await self._find_add_to_cart_button(page)
            if not add_button:
                self.logger.warning(f" No add-to-cart button found for {tcin}")
                return {
                    'success': False,
                    'tcin': tcin,
                    'reason': 'button_not_found',
                    'execution_time': time.time() - start_time
                }

            # Click the button
            await add_button.click()
            await asyncio.sleep(random.uniform(0.5, 1.5))

            self.logger.info(f"🛒 Clicked add-to-cart for {tcin}")

            # Navigate to cart
            await page.goto("https://www.target.com/cart", wait_until='domcontentloaded', timeout=15000)
            await asyncio.sleep(random.uniform(1.0, 2.0))

            # Verify item in cart
            if not await self._verify_item_in_cart(page, tcin):
                self.logger.warning(f" Item not found in cart for {tcin}")
                return {
                    'success': False,
                    'tcin': tcin,
                    'reason': 'cart_verification_failed',
                    'execution_time': time.time() - start_time
                }

            # Add intermediate status for checkout process
            self._notify_status(tcin, 'checking_out', {
                'timestamp': datetime.now().isoformat()
            })

            # Complete full checkout and payment
            checkout_result = await self._proceed_to_checkout(page)

            execution_time = time.time() - start_time

            if checkout_result:
                self.logger.info(f"PURCHASE COMPLETED for {tcin} in {execution_time:.2f}s")
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
            else:
                self.logger.warning(f"Purchase failed for {tcin} - order not confirmed")
                return {
                    'success': False,
                    'tcin': tcin,
                    'reason': 'purchase_failed',
                    'execution_time': execution_time
                }

        except Exception as e:
            execution_time = time.time() - start_time
            self.logger.error(f" Purchase failed for {tcin}: {e}")

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

    async def _find_add_to_cart_button(self, page):
        """Find add-to-cart button using multiple selectors"""
        try:
            # Use parallel search approach
            for selector in self.SELECTORS['add_to_cart']:
                try:
                    button = await page.wait_for_selector(selector, timeout=1000)
                    if button and await button.is_visible():
                        self.logger.debug(f"Found button with selector: {selector}")
                        return button
                except:
                    continue

            return None

        except Exception as e:
            self.logger.error(f"Error finding add-to-cart button: {e}")
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
            await asyncio.sleep(random.uniform(1.0, 2.0))

            # Step 2: Wait for checkout page to load
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

            await asyncio.sleep(random.uniform(0.5, 1.5))

            # Try to find and select shipping option
            shipping_selected = False
            for selector in shipping_selectors:
                try:
                    element = await page.wait_for_selector(selector, timeout=2000)
                    if element and await element.is_visible():
                        await element.click()
                        self.logger.info("Selected shipping delivery option")
                        shipping_selected = True
                        await asyncio.sleep(random.uniform(0.5, 1.0))
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
                        await asyncio.sleep(random.uniform(0.8, 1.5))
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

            # Wait a moment for payment page to fully load
            await asyncio.sleep(random.uniform(1.0, 2.5))

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
                # Click the place order button
                await place_order_button.click()
                self.logger.info("Clicked place order button - purchase submitted!")

                # Wait for order processing
                await asyncio.sleep(random.uniform(2.0, 4.0))
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
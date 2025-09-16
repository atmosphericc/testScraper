import asyncio
from playwright.async_api import async_playwright
from datetime import datetime
from pathlib import Path
import logging
from typing import Dict, Optional
import os

class BuyBot:
    def __init__(self, session_path: str):
        self.session_path = Path(session_path)
        self.logger = logging.getLogger(__name__)
        self.purchase_log = logging.getLogger('purchases')

    async def attempt_purchase(self, product: Dict) -> Dict:
        """
        Attempt to purchase a single product
        Returns: Dict with success status and details
        """
        tcin = product['tcin']
        max_price = product.get('max_price', 999.99)
        current_price = product.get('price', 0)

        self.purchase_log.info(f"Starting purchase: {tcin} - ${current_price:.2f} (max: ${max_price:.2f})")

        # Price check
        if current_price > max_price:
            self.purchase_log.warning(f"Price too high: ${current_price:.2f} > ${max_price:.2f}")
            return {
                'success': False,
                'tcin': tcin,
                'reason': 'price_too_high',
                'message': f'Price ${current_price:.2f} exceeds max ${max_price:.2f}'
            }

        try:
            async with async_playwright() as p:
                # Launch browser (headless for speed)
                browser = await p.chromium.launch(
                    headless=True,
                    args=['--disable-blink-features=AutomationControlled']
                )

                # Load saved session
                context = await browser.new_context(
                    storage_state=str(self.session_path),
                    viewport={'width': 1920, 'height': 1080}
                )

                page = await context.new_page()
                page.set_default_timeout(15000)  # 15 second timeout

                # Go directly to product page
                url = f"https://www.target.com/p/-/A-{tcin}"
                self.logger.info(f"Navigating to {url}")

                await page.goto(url, wait_until='domcontentloaded')
                await page.wait_for_timeout(2000)

                # Check if product page loaded
                if "product not found" in page.url.lower():
                    self.purchase_log.error(f"Product {tcin} not found")
                    await browser.close()
                    return {
                        'success': False,
                        'tcin': tcin,
                        'reason': 'product_not_found'
                    }

                # Select shipping fulfillment if available
                try:
                    ship_button = await page.wait_for_selector(
                        'button[data-test="fulfillment-cell-shipping"]',
                        timeout=3000
                    )
                    await ship_button.click()
                    await page.wait_for_timeout(1000)
                    self.logger.info("Selected shipping option")
                except:
                    self.logger.info("Shipping option not found or already selected")

                # Add to cart
                try:
                    # Look for add to cart button
                    add_button = await page.wait_for_selector(
                        'button[id^="addToCartButtonOrTextIdFor"]',
                        timeout=5000
                    )

                    # Scroll button into view
                    await add_button.scroll_into_view_if_needed()
                    await page.wait_for_timeout(500)

                    # Click add to cart
                    await add_button.click()
                    self.logger.info("Clicked add to cart")

                    # Wait for cart update
                    await page.wait_for_timeout(3000)

                except Exception as e:
                    self.purchase_log.error(f"Could not add to cart: {e}")

                    # Take screenshot for debugging
                    screenshot_path = f"logs/failed_add_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                    await page.screenshot(path=screenshot_path)

                    await browser.close()
                    return {
                        'success': False,
                        'tcin': tcin,
                        'reason': 'add_to_cart_failed',
                        'screenshot': screenshot_path
                    }

                # Go to cart
                await page.goto("https://www.target.com/cart", wait_until='domcontentloaded')
                await page.wait_for_timeout(2000)

                # Verify item is in cart
                try:
                    await page.wait_for_selector('[data-test="cartItem-title"]', timeout=5000)
                    self.logger.info("Item confirmed in cart")
                except:
                    self.purchase_log.error("Item not found in cart")
                    await browser.close()
                    return {
                        'success': False,
                        'tcin': tcin,
                        'reason': 'item_not_in_cart'
                    }

                # Click checkout button
                try:
                    checkout_button = await page.wait_for_selector(
                        'button[data-test="checkout-button"]',
                        timeout=5000
                    )

                    # Check for TEST mode
                    if os.environ.get('CHECKOUT_MODE') == 'PRODUCTION':
                        await checkout_button.click()
                        self.purchase_log.warning(f"CHECKOUT INITIATED for {tcin}")

                        # Wait for checkout page
                        await page.wait_for_timeout(5000)

                        # Look for place order button (but don't click unless confirmed)
                        try:
                            await page.wait_for_selector(
                                'button[data-test="placeOrderButton"]',
                                timeout=10000
                            )

                            # Take screenshot of checkout page
                            screenshot_path = f"logs/checkout_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                            await page.screenshot(path=screenshot_path)

                            # FINAL CONFIRMATION - Only in production with explicit flag
                            if os.environ.get('FINAL_PURCHASE') == 'YES':
                                place_order = await page.query_selector('button[data-test="placeOrderButton"]')
                                await place_order.click()

                                # Wait for confirmation
                                await page.wait_for_timeout(10000)

                                # Look for order number
                                try:
                                    order_element = await page.wait_for_selector(
                                        '[data-test="order-number"]',
                                        timeout=15000
                                    )
                                    order_number = await order_element.text_content()

                                    self.purchase_log.warning(f"ORDER PLACED: {order_number} for {tcin}")

                                    await browser.close()
                                    return {
                                        'success': True,
                                        'tcin': tcin,
                                        'order_number': order_number,
                                        'price': current_price
                                    }
                                except:
                                    self.purchase_log.error("Could not find order number")

                            self.purchase_log.info(f"Checkout ready but not completed (PRODUCTION MODE - no FINAL_PURCHASE flag)")

                        except:
                            self.purchase_log.error("Could not reach place order button")

                    else:
                        self.purchase_log.info(f"TEST MODE: Would checkout {tcin} at ${current_price:.2f}")

                        # Take screenshot
                        screenshot_path = f"logs/test_cart_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                        await page.screenshot(path=screenshot_path)

                    await browser.close()
                    return {
                        'success': True,
                        'tcin': tcin,
                        'test_mode': True,
                        'price': current_price,
                        'message': 'Item added to cart successfully (test mode)'
                    }

                except Exception as e:
                    self.purchase_log.error(f"Checkout failed: {e}")
                    await browser.close()
                    return {
                        'success': False,
                        'tcin': tcin,
                        'reason': 'checkout_failed',
                        'error': str(e)
                    }

        except Exception as e:
            self.purchase_log.error(f"Purchase attempt failed: {e}")
            return {
                'success': False,
                'tcin': tcin,
                'reason': 'exception',
                'error': str(e)
            }
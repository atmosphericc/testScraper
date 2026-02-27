import asyncio
import zendriver as uc
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional


class BuyBot:
    def __init__(self, session_path: str):
        self.session_path = Path(session_path)
        self.logger = logging.getLogger(__name__)
        self.purchase_log = logging.getLogger('purchases')

    async def attempt_purchase(self, product: Dict) -> Dict:
        """
        Attempt to purchase a single product using nodriver.
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

        browser = None
        try:
            browser = await uc.start(
                user_data_dir="./nodriver-buybot-profile",
                headless=True,
                browser_args=['--window-size=1920,1080'],
            )

            tab = browser.tabs[0] if browser.tabs else await browser.get("about:blank")

            # Load saved session cookies
            if self.session_path.exists():
                try:
                    with open(self.session_path, 'r') as f:
                        session_data = json.load(f)
                    cookies = session_data.get('cookies', [])
                    if cookies:
                        await tab.send("Network.enable")
                        for cookie in cookies:
                            try:
                                await tab.send("Network.setCookie", **{
                                    k: v for k, v in cookie.items()
                                    if k in ('name', 'value', 'domain', 'path', 'expires',
                                             'httpOnly', 'secure', 'sameSite') and v is not None
                                })
                            except Exception:
                                pass
                        self.logger.info(f"Loaded {len(cookies)} session cookies")
                except Exception as e:
                    self.logger.warning(f"Could not load session cookies: {e}")

            # Go directly to product page
            url = f"https://www.target.com/p/-/A-{tcin}"
            self.logger.info(f"Navigating to {url}")

            tab = await browser.get(url)
            await asyncio.sleep(2)

            # Check if product page loaded
            if "product not found" in tab.url.lower():
                self.purchase_log.error(f"Product {tcin} not found")
                return {'success': False, 'tcin': tcin, 'reason': 'product_not_found'}

            # Select shipping fulfillment if available
            try:
                ship_button = await tab.select('button[data-test="fulfillment-cell-shipping"]', timeout=3)
                if ship_button:
                    await ship_button.click()
                    await asyncio.sleep(1)
                    self.logger.info("Selected shipping option")
            except Exception:
                self.logger.info("Shipping option not found or already selected")

            # Add to cart
            try:
                add_button = await tab.select('button[id^="addToCartButtonOrTextIdFor"]', timeout=5)
                if not add_button:
                    add_button = await tab.select('button[data-test="addToCartButton"]', timeout=2)

                if not add_button:
                    raise Exception("Add to cart button not found")

                # Scroll into view
                await tab.evaluate("el => el.scrollIntoView({block: 'center', behavior: 'instant'})", add_button)
                await asyncio.sleep(0.5)

                # Click add to cart
                await add_button.click()
                self.logger.info("Clicked add to cart")

                # Wait for cart update
                await asyncio.sleep(3)

            except Exception as e:
                self.purchase_log.error(f"Could not add to cart: {e}")

                # Take screenshot for debugging
                try:
                    screenshot_path = f"logs/failed_add_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                    await tab.save_screenshot(screenshot_path)
                except Exception:
                    pass

                return {
                    'success': False,
                    'tcin': tcin,
                    'reason': 'add_to_cart_failed',
                }

            # Go to cart
            tab = await browser.get("https://www.target.com/cart")
            await asyncio.sleep(2)

            # Verify item is in cart
            try:
                cart_item = await tab.select('[data-test="cartItem-title"]', timeout=5)
                if not cart_item:
                    raise Exception("Cart item not found")
                self.logger.info("Item confirmed in cart")
            except Exception:
                self.purchase_log.error("Item not found in cart")
                return {'success': False, 'tcin': tcin, 'reason': 'item_not_in_cart'}

            # Click checkout button
            try:
                checkout_button = await tab.select('button[data-test="checkout-button"]', timeout=5)
                if not checkout_button:
                    checkout_button = await tab.find("Checkout", best_match=True, timeout=3)

                if not checkout_button:
                    raise Exception("Checkout button not found")

                if os.environ.get('CHECKOUT_MODE') == 'PRODUCTION':
                    await checkout_button.click()
                    self.purchase_log.warning(f"CHECKOUT INITIATED for {tcin}")

                    await asyncio.sleep(5)

                    # Look for place order button
                    try:
                        place_order = await tab.select('button[data-test="placeOrderButton"]', timeout=10)
                        if not place_order:
                            place_order = await tab.find("Place order", best_match=True, timeout=5)

                        if place_order:
                            # Take screenshot of checkout page
                            try:
                                screenshot_path = f"logs/checkout_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                                await tab.save_screenshot(screenshot_path)
                            except Exception:
                                pass

                            # FINAL CONFIRMATION
                            if os.environ.get('FINAL_PURCHASE') == 'YES':
                                await place_order.click()
                                await asyncio.sleep(10)

                                # Look for order number
                                try:
                                    order_elem = await tab.select('[data-test="order-number"]', timeout=15)
                                    if order_elem:
                                        order_number = await tab.evaluate("el => el.textContent", order_elem)
                                        self.purchase_log.warning(f"ORDER PLACED: {order_number} for {tcin}")
                                        return {
                                            'success': True,
                                            'tcin': tcin,
                                            'order_number': order_number,
                                            'price': current_price
                                        }
                                except Exception:
                                    self.purchase_log.error("Could not find order number")

                            self.purchase_log.info(f"Checkout ready but not completed (no FINAL_PURCHASE flag)")

                    except Exception:
                        self.purchase_log.error("Could not reach place order button")

                else:
                    self.purchase_log.info(f"TEST MODE: Would checkout {tcin} at ${current_price:.2f}")

                    try:
                        screenshot_path = f"logs/test_cart_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                        await tab.save_screenshot(screenshot_path)
                    except Exception:
                        pass

                return {
                    'success': True,
                    'tcin': tcin,
                    'test_mode': True,
                    'price': current_price,
                    'message': 'Item added to cart successfully (test mode)'
                }

            except Exception as e:
                self.purchase_log.error(f"Checkout failed: {e}")
                return {'success': False, 'tcin': tcin, 'reason': 'checkout_failed', 'error': str(e)}

        except Exception as e:
            self.purchase_log.error(f"Purchase attempt failed: {e}")
            return {'success': False, 'tcin': tcin, 'reason': 'exception', 'error': str(e)}

        finally:
            if browser:
                try:
                    browser.stop()
                except Exception:
                    pass

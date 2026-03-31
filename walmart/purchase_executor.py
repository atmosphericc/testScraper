"""
Walmart purchase executor — ATC → checkout → place order.

Flow:
  1. Navigate to product page
  2. Check for / handle virtual queue (QueueHandler)
  3. Click Add to Cart
  4. Verify cart
  5. Click Checkout
  6. Confirm shipping (pre-saved address)
  7. Enter CVV if required
  8. Click Place Order (gated by CHECKOUT_MODE + FINAL_PURCHASE env vars)
  9. Capture order confirmation number

Safety:
  - CHECKOUT_MODE=TEST  → stops before Place Order (default)
  - CHECKOUT_MODE=PRODUCTION + FINAL_PURCHASE=YES → places the order
  - Screenshots saved to walmart/logs/ on every failure or key step
"""

import asyncio
import logging
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

from .config import (
    WALMART_CART_URL,
    WALMART_CHECKOUT_URL,
    LOGS_DIR,
)
from .queue_handler import QueueHandler

logger = logging.getLogger(__name__)

# Patchable timeout constant — bumped by timeout_bumper without touching call sites
NAVIGATE_TIMEOUT = 30000

# Multiple selector candidates for each step — Walmart's DOM varies by A/B test
ATC_SELECTORS = [
    'button[data-automation-id="add-to-cart-btn"]',
    'button[data-tl-id="ProductPrimaryCTA-cta_add_to_cart_button"]',
    'button:has-text("Add to cart")',
    'button:has-text("Add to Cart")',
]

CHECKOUT_SELECTORS = [
    'button[data-automation-id="checkout-btn"]',
    'a[data-automation-id="checkout-btn"]',
    'button:has-text("Checkout")',
    'a:has-text("Checkout")',
]

PLACE_ORDER_SELECTORS = [
    'button[data-automation-id="place-order-btn"]',
    'button:has-text("Place order")',
    'button:has-text("Place Order")',
    'button:has-text("Submit order")',
]

CVV_SELECTORS = [
    'input[name="cvv"]',
    'input[autocomplete="cc-csc"]',
    'input[placeholder*="CVV"]',
    'input[placeholder*="CVC"]',
    'input[aria-label*="CVV"]',
    'input[aria-label*="security code"]',
]

ORDER_CONFIRM_SELECTORS = [
    '[data-automation-id="order-confirmation-number"]',
    '[data-automation-id="confirmation-order-id"]',
    'h1:has-text("Your order is confirmed")',
    'h1:has-text("Thank you")',
    'span:has-text("Order #")',
]


class PurchaseResult:
    def __init__(self, success: bool, order_id: Optional[str] = None, error: Optional[str] = None):
        self.success = success
        self.order_id = order_id
        self.error = error

    def __repr__(self):
        if self.success:
            return f"PurchaseResult(SUCCESS, order_id={self.order_id})"
        return f"PurchaseResult(FAILED, error={self.error})"


class WalmartPurchaseExecutor:
    """
    Executes a single Walmart purchase attempt for a given item.

    Usage:
        executor = WalmartPurchaseExecutor(page, status_callback)
        result = await executor.purchase(item_id="15042474261", item_url="https://...")
    """

    def __init__(
        self,
        page,
        status_callback: Optional[Callable[[str], None]] = None,
    ):
        self._page = page
        self._status_cb = status_callback or (lambda msg: None)
        Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def purchase(self, item_id: str, item_url: str) -> PurchaseResult:
        """
        Attempt to purchase one unit of the given item.
        Returns PurchaseResult with success/failure and order ID if successful.
        """
        self._status_cb(f"[PURCHASE] Starting purchase attempt for {item_id}")
        logger.info("[PURCHASE] Starting: %s", item_id)

        try:
            # Step 1: Navigate to product page
            await self._navigate(item_url)

            # Step 2: Handle virtual queue if present
            queue = QueueHandler(self._page, self._status_cb)
            in_queue = await queue.detect()
            if in_queue:
                self._status_cb("[PURCHASE] Queue detected — waiting for pass-through")
                passed = await queue.wait_for_passthrough()
                if not passed:
                    return PurchaseResult(False, error="Queue timeout")
                # After pass-through, re-navigate to ensure we're on the product page
                await self._navigate(item_url)

            # Step 2b: Try FBT queue bypass first (known to bypass Walmart's virtual queue)
            fbt_ok = await self._try_fbt_add_to_cart(item_id)
            if fbt_ok:
                # FBT ATC succeeded — skip normal ATC, go straight to verify cart
                cart_ok = await self._verify_cart(item_id)
                if not cart_ok:
                    # FBT click happened but cart empty — fall through to normal ATC
                    self._status_cb("[PURCHASE] FBT ATC did not add to cart — trying normal ATC")
                else:
                    # FBT worked — skip to checkout
                    self._status_cb("[PURCHASE] FBT ATC confirmed in cart — proceeding to checkout")
                    # Jump to step 5 (checkout) — skip normal ATC
                    checkout_ok = await self._go_to_checkout()
                    if not checkout_ok:
                        return PurchaseResult(False, error="FBT path: Could not reach checkout")
                    await self._confirm_shipping()
                    await self._enter_cvv_if_needed()
                    checkout_mode = os.environ.get("CHECKOUT_MODE", "TEST")
                    final_purchase = os.environ.get("FINAL_PURCHASE", "NO")
                    if checkout_mode != "PRODUCTION":
                        await self._screenshot(f"test_mode_stop_fbt_{item_id}")
                        self._status_cb("[PURCHASE] TEST MODE — stopping before Place Order")
                        return PurchaseResult(True, order_id="TEST_MODE")
                    if final_purchase != "YES":
                        await self._screenshot(f"pre_place_order_fbt_{item_id}")
                        return PurchaseResult(True, order_id="DRY_RUN")
                    order_id = await self._place_order(item_id)
                    if order_id:
                        self._status_cb(f"[PURCHASE] ORDER PLACED via FBT! ID: {order_id}")
                        await self._clear_cart()
                        return PurchaseResult(True, order_id=order_id)
                    else:
                        await self._clear_cart()
                        return PurchaseResult(False, error="FBT path: No order ID after place order")

            # Step 3: Pre-check cart — skip ATC if item is already there
            already_in_cart = await self._is_item_already_in_cart(item_url)
            if already_in_cart:
                self._status_cb("[PURCHASE] Item already in cart — skipping ATC")
                logger.info("[PURCHASE] Skipping ATC: item already in cart for %s", item_id)
                cart_ok = await self._verify_cart(item_id)
            else:
                await self._clear_cart_if_needed()
                await self._navigate(item_url)  # cart clear navigates away — return to product page
                atc_ok = await self._add_to_cart(item_id)
                if not atc_ok:
                    return PurchaseResult(False, error="Add to cart failed")

                # Step 4: Verify cart
                cart_ok = await self._verify_cart(item_id)
            if not cart_ok:
                return PurchaseResult(False, error="Item not found in cart after ATC")

            # Step 5: Proceed to checkout
            checkout_ok = await self._go_to_checkout()
            if not checkout_ok:
                return PurchaseResult(False, error="Could not reach checkout page")

            # Step 6: Confirm shipping (pre-saved address — just continue)
            await self._confirm_shipping()

            # Step 7: Enter CVV if required
            await self._enter_cvv_if_needed()

            # TEST MODE — stop here (re-read env vars at purchase time so test/live toggle works)
            checkout_mode = os.environ.get("CHECKOUT_MODE", "TEST")
            final_purchase = os.environ.get("FINAL_PURCHASE", "NO")
            card_cvv = os.environ.get("WALMART_CVV", "")

            if checkout_mode != "PRODUCTION":
                await self._screenshot(f"test_mode_stop_{item_id}")
                self._status_cb("[PURCHASE] TEST MODE — stopping before Place Order")
                logger.info("[PURCHASE] TEST MODE — would have placed order for %s", item_id)
                return PurchaseResult(True, order_id="TEST_MODE")

            # Step 8: Place order
            if final_purchase != "YES":
                await self._screenshot(f"pre_place_order_{item_id}")
                self._status_cb("[PURCHASE] FINAL_PURCHASE not set — stopping before Place Order")
                return PurchaseResult(True, order_id="DRY_RUN")

            order_id = await self._place_order(item_id)
            if order_id:
                self._status_cb(f"[PURCHASE] ORDER PLACED! ID: {order_id}")
                logger.info("[PURCHASE] SUCCESS — order ID: %s", order_id)
                await self._clear_cart()
                return PurchaseResult(True, order_id=order_id)
            else:
                await self._clear_cart()
                return PurchaseResult(False, error="Place order click succeeded but no order ID found")

        except Exception as e:
            await self._screenshot(f"error_{item_id}")
            self._status_cb(f"[PURCHASE] Error: {e}")
            logger.exception("[PURCHASE] Unexpected error for %s", item_id)
            await self._clear_cart()
            return PurchaseResult(False, error=str(e))

    # ------------------------------------------------------------------
    # Step implementations
    # ------------------------------------------------------------------

    async def _navigate(self, url: str):
        self._status_cb(f"[PURCHASE] Navigating to {url}")
        await self._page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATE_TIMEOUT)
        await asyncio.sleep(1.5)

    async def _add_to_cart(self, item_id: str) -> bool:
        self._status_cb("[PURCHASE] Looking for Add to Cart button...")
        btn = await self._find_element(ATC_SELECTORS, timeout=10000)
        if not btn:
            await self._screenshot(f"no_atc_{item_id}")
            logger.warning("[PURCHASE] ATC button not found for %s", item_id)
            return False

        await btn.scroll_into_view_if_needed()
        await self._human_delay(200, 400)
        await btn.click()
        self._status_cb("[PURCHASE] Clicked Add to Cart")

        # Wait for cart confirmation (drawer/modal or URL change)
        await asyncio.sleep(2)
        # Some Walmart pages show a "View Cart" modal after ATC
        try:
            view_cart = await self._find_element([
                'button:has-text("View cart")',
                'a:has-text("View cart")',
                'button:has-text("Go to cart")',
                'a:has-text("Go to cart")',
            ], timeout=3000)
            if view_cart:
                await view_cart.click()
                await asyncio.sleep(1)
        except Exception:
            pass  # no modal — that's fine

        return True

    async def _verify_cart(self, item_id: str) -> bool:
        self._status_cb("[PURCHASE] Verifying cart...")
        await self._page.goto(WALMART_CART_URL, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(1.5)

        # Check for at least one cart item
        try:
            cart_items = await self._query_selector_all([
                '[data-automation-id="cart-item"]',
                '[data-testid="cart-item"]',
                '.cart-item',
            ])
            if cart_items:
                logger.info("[PURCHASE] Cart verified — %d item(s)", len(cart_items))
                return True
            else:
                logger.warning("[PURCHASE] Cart selector check failed — no cart-item elements found")
        except Exception:
            logger.warning("[PURCHASE] Cart selector check failed — query raised exception")

        # Fallback: check URL still on cart and no "empty cart" text
        if "cart" not in self._page.url:
            logger.warning("[PURCHASE] Cart URL check failed — current URL: %s", self._page.url)
            await self._screenshot(f"empty_cart_{item_id}")
            logger.warning("[PURCHASE] Cart appears empty after ATC")
            return False
        try:
            body = await self._page.locator("body").inner_text(timeout=3000)
            if "your cart is empty" not in body.lower():
                return True
        except Exception:
            pass

        await self._screenshot(f"empty_cart_{item_id}")
        logger.warning("[PURCHASE] Cart appears empty after ATC")
        return False

    async def _go_to_checkout(self) -> bool:
        self._status_cb("[PURCHASE] Clicking Checkout...")
        btn = await self._find_element(CHECKOUT_SELECTORS, timeout=8000)
        if not btn:
            await self._screenshot("no_checkout_btn")
            logger.warning("[PURCHASE] Checkout button not found")
            return False

        await btn.click()
        # Wait for checkout page to load
        try:
            await self._page.wait_for_url("**/checkout**", timeout=15000)
        except Exception:
            await asyncio.sleep(3)

        # Verify checkout page actually loaded — look for checkout-specific content
        checkout_loaded = False
        try:
            body = await self._page.locator("body").inner_text(timeout=3000)
            body_lower = body.lower()
            checkout_keywords = ("payment", "shipping", "order summary")
            if any(kw in body_lower for kw in checkout_keywords):
                checkout_loaded = True
        except Exception:
            pass
        if not checkout_loaded:
            # Also try a checkout-specific selector as a second signal
            for sel in (
                '[data-automation-id="checkout-page"]',
                '[data-page-type="checkout"]',
                'form[id*="checkout"]',
            ):
                try:
                    el = await self._page.query_selector(sel)
                    if el:
                        checkout_loaded = True
                        break
                except Exception:
                    pass
        if not checkout_loaded:
            logger.warning("[PURCHASE] Checkout page did not load correctly")
            await self._screenshot("checkout_load_failed")
            return False

        self._status_cb("[PURCHASE] On checkout page")
        return True

    async def _confirm_shipping(self):
        """
        Walmart checkout pre-fills the saved address. We just need to continue.
        Click any "Continue" or "Deliver to this address" button if present.
        """
        await asyncio.sleep(1.5)
        try:
            continue_btn = await self._find_element([
                'button:has-text("Continue")',
                'button:has-text("Deliver here")',
                'button:has-text("Use this address")',
            ], timeout=4000)
            if continue_btn:
                await continue_btn.click()
                await asyncio.sleep(1.5)
                self._status_cb("[PURCHASE] Shipping confirmed")
        except Exception:
            pass  # no continue button needed — already past shipping step

    async def _enter_cvv_if_needed(self):
        """Enter CVV if the payment page requires it."""
        card_cvv = os.environ.get("WALMART_CVV", "")  # re-read at call time
        if not card_cvv:
            return
        try:
            cvv_input = await self._find_element(CVV_SELECTORS, timeout=4000)
            if cvv_input:
                await cvv_input.fill(card_cvv)
                self._status_cb("[PURCHASE] CVV entered")
                await self._human_delay(300, 600)
                # Verify CVV was accepted by reading it back
                try:
                    filled_value = await cvv_input.input_value()
                    if filled_value != card_cvv:
                        logger.warning(
                            "[PURCHASE] CVV verification failed — filled '%s' but read back '%s'",
                            card_cvv,
                            filled_value,
                        )
                    else:
                        logger.info("[PURCHASE] CVV verified successfully")
                except Exception as e:
                    logger.warning("[PURCHASE] CVV read-back failed: %s", e)
        except Exception as e:
            logger.warning("[PURCHASE] CVV entry failed: %s", e)  # CVV not required or already filled

    async def _place_order(self, item_id: str) -> Optional[str]:
        self._status_cb("[PURCHASE] Clicking Place Order...")
        btn = await self._find_element(PLACE_ORDER_SELECTORS, timeout=10000)
        if not btn:
            await self._screenshot(f"no_place_order_{item_id}")
            logger.warning("[PURCHASE] Place Order button not found")
            return None

        await self._screenshot(f"before_place_order_{item_id}")
        await btn.click()
        self._status_cb("[PURCHASE] Place Order clicked — waiting for confirmation...")

        # Wait for order confirmation page
        await asyncio.sleep(0.05)  # CDP flush yield
        pre_click_url = self._page.url
        try:
            # Regex matches Walmart's known confirmation URL patterns
            await self._page.wait_for_url(
                re.compile(r".*(order-confirmation|order/confirm|thank-you|order-placed).*"),
                timeout=20000,
            )
        except Exception:
            # URL didn't match confirmation pattern — wait and check if page changed at all
            await asyncio.sleep(3)
            if self._page.url == pre_click_url:
                logger.warning("[PURCHASE] Page did not navigate after Place Order click")

        # Extract order ID
        order_id = await self._extract_order_id()
        await self._screenshot(f"order_confirmation_{item_id}")
        return order_id

    async def _extract_order_id(self) -> Optional[str]:
        for selector in ORDER_CONFIRM_SELECTORS:
            try:
                el = await self._page.query_selector(selector)
                if el:
                    text = await el.inner_text()
                    if text:
                        # Try to pull just the numeric order ID
                        numbers = re.findall(r'\d{6,}', text)
                        if numbers:
                            return numbers[0]
                        return text.strip()
            except Exception:
                continue

        # Check URL for order ID
        url = self._page.url
        if "order-confirmation" in url or "order" in url:
            numbers = re.findall(r'\d{7,}', url)
            if numbers:
                return numbers[0]
            return "CONFIRMED_NO_ID"  # URL looks like confirmation but no digits found

        return None

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    async def _is_item_already_in_cart(self, item_url: str = "") -> bool:
        """
        Navigate to cart and return True if at least one item is already there.
        Navigates back to item_url afterward so the caller stays on the product page.
        """
        try:
            await self._page.goto(WALMART_CART_URL, wait_until="domcontentloaded", timeout=NAVIGATE_TIMEOUT)
            await asyncio.sleep(1)
            cart_items = await self._query_selector_all([
                '[data-automation-id="cart-item"]',
                '[data-testid="cart-item"]',
                '.cart-item',
            ])
            found = bool(cart_items)
            if found:
                logger.info("[PURCHASE] Pre-check: %d item(s) already in cart", len(cart_items))
        except Exception as e:
            logger.warning("[PURCHASE] Cart pre-check failed: %s", e)
            found = False
        finally:
            # Always navigate back to the product page
            if item_url:
                try:
                    await self._page.goto(item_url, wait_until="domcontentloaded", timeout=NAVIGATE_TIMEOUT)
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.warning("[PURCHASE] Could not navigate back to item URL after cart check: %s", e)
        return found

    async def _clear_cart_if_needed(self):
        """
        If the cart already has items (from a previous attempt), remove them so
        we don't accidentally purchase more than one unit.
        Navigates to cart, checks item count, and clicks each remove button.
        """
        try:
            await self._page.goto(WALMART_CART_URL, wait_until="domcontentloaded", timeout=NAVIGATE_TIMEOUT)
            await asyncio.sleep(1)
            cart_items = await self._query_selector_all([
                '[data-automation-id="cart-item"]',
                '[data-testid="cart-item"]',
                '.cart-item',
            ])
            if not cart_items:
                return  # cart already empty — nothing to do
            logger.info("[PURCHASE] Cart has %d item(s) — clearing before ATC", len(cart_items))
            self._status_cb(f"[PURCHASE] Clearing {len(cart_items)} existing cart item(s)")
            remove_btns = await self._query_selector_all([
                'button[data-automation-id="remove-item"]',
                'button:has-text("Remove")',
                'button[aria-label*="Remove"]',
            ])
            for btn in remove_btns:
                try:
                    await btn.click()
                    await asyncio.sleep(0.8)
                except Exception:
                    pass
        except Exception as e:
            logger.warning("[PURCHASE] _clear_cart_if_needed failed: %s", e)

    async def _clear_cart(self):
        """
        Clear all items from the cart. Called after every purchase attempt
        (success or failure) to ensure a clean state for the next attempt.
        Silently ignores errors — best-effort cleanup only.
        """
        try:
            await self._page.goto(WALMART_CART_URL, wait_until="domcontentloaded", timeout=NAVIGATE_TIMEOUT)
            await asyncio.sleep(1)
            remove_btns = await self._query_selector_all([
                'button[data-automation-id="remove-item"]',
                'button:has-text("Remove")',
                'button[aria-label*="Remove"]',
            ])
            if not remove_btns:
                return
            logger.info("[PURCHASE] Post-attempt cleanup: removing %d cart item(s)", len(remove_btns))
            for btn in remove_btns:
                try:
                    await btn.click()
                    await asyncio.sleep(0.8)
                except Exception:
                    pass
        except Exception as e:
            logger.warning("[PURCHASE] _clear_cart cleanup failed: %s", e)

    async def _try_fbt_add_to_cart(self, item_id: str) -> bool:
        """
        Attempt to add item to cart via the 'Frequently Bought Together' section.
        This is a known queue bypass — the FBT ATC endpoint uses a different
        code path that bypasses Walmart's virtual queue validation.
        Returns True if ATC succeeded via this method.
        """
        self._status_cb("[PURCHASE] Trying Frequently Bought Together ATC bypass...")
        fbt_selectors = [
            f'[data-item-id="{item_id}"] button[data-automation-id="add-to-cart-btn"]',
            f'[data-item-id="{item_id}"] button:has-text("Add to cart")',
            '[data-testid="frequently-bought-together"] button[data-automation-id="add-to-cart-btn"]',
            '[data-testid="frequently-bought-together"] button:has-text("Add to cart")',
            '.frequently-bought-together button[data-automation-id="add-to-cart-btn"]',
            '.frequently-bought-together button:has-text("Add to cart")',
            '[class*="frequently-bought"] button:has-text("Add to cart")',
            '[class*="FBT"] button:has-text("Add to cart")',
        ]
        for selector in fbt_selectors:
            try:
                btn = await self._page.query_selector(selector)
                if btn and await btn.is_visible():
                    await btn.scroll_into_view_if_needed()
                    await asyncio.sleep(0.2)
                    await btn.click()
                    self._status_cb("[PURCHASE] FBT ATC clicked — queue bypass attempted")
                    logger.info("[PURCHASE] FBT ATC bypass clicked for %s", item_id)
                    await asyncio.sleep(2)
                    return True
            except Exception:
                continue
        logger.debug("[PURCHASE] No FBT ATC button found for %s", item_id)
        return False

    async def _human_delay(self, min_ms: int = 80, max_ms: int = 300):
        """Add a small randomized delay to simulate human interaction timing."""
        delay = random.randint(min_ms, max_ms) / 1000.0
        await asyncio.sleep(delay)

    async def _find_element(self, selectors: list[str], timeout: int = 5000):
        """Try each selector in order, return the first matching visible element.

        Notes:
        - Patchright does not support comma-separated selectors in wait_for_selector.
        - `state=` parameter is not supported in patchright's wait_for_selector;
          visibility is checked separately via is_visible().
        """
        if not selectors:
            return None
        per_selector_timeout = max(500, timeout // len(selectors))
        for selector in selectors:
            try:
                el = await self._page.wait_for_selector(selector, timeout=per_selector_timeout)
                if el and await el.is_visible():
                    return el
            except Exception:
                continue
        return None

    async def _query_selector_all(self, selectors: list[str]) -> list:
        """
        Query for all elements matching any selector in the list.
        Patchright does not support comma-separated selectors in query_selector_all,
        so we query each selector individually and deduplicate by DOM node identity.

        Deduplication uses evaluate() to get each element's outerHTML hash as a
        proxy for node identity — two Python ElementHandle objects wrapping the
        same DOM node will produce identical outerHTML strings at that moment.
        We use the first selector's results as primary, skipping elements already
        collected from prior selectors.
        """
        seen_keys = set()
        results = []
        # Use a counter to assign unique fallback keys to detached/keyless elements
        _fallback_counter = 0
        for selector in selectors:
            try:
                els = await self._page.query_selector_all(selector)
                for el in els:
                    try:
                        # Build a stable identity key from immutable DOM attributes.
                        # outerHTML slice is reliable for attached nodes; for detached
                        # nodes or evaluate failures we fall back to a unique counter
                        # so each element still gets added exactly once per selector.
                        node_key = await el.evaluate(
                            "el => (el.getAttribute('data-automation-id') || "
                            "el.getAttribute('data-testid') || "
                            "el.id || el.outerHTML.slice(0, 200))"
                        )
                        if not node_key:
                            raise ValueError("empty key")
                    except Exception:
                        # Detached or keyless element — give it a unique fallback key
                        _fallback_counter += 1
                        node_key = f"__fallback_{_fallback_counter}"
                    if node_key not in seen_keys:
                        seen_keys.add(node_key)
                        results.append(el)
            except Exception:
                continue
        return results

    async def _screenshot(self, label: str):
        """Save a screenshot to walmart/logs/ for debugging."""
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = f"{LOGS_DIR}/{label}_{ts}.png"
            await self._page.screenshot(path=path, full_page=False)
            logger.debug("[PURCHASE] Screenshot: %s", path)
        except Exception as e:
            logger.warning("[PURCHASE] Screenshot failed: %s", e)

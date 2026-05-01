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
    get_card_cvv,
)
from .queue_handler import QueueHandler

logger = logging.getLogger(__name__)

# Patchable timeout constant — bumped by timeout_bumper without touching call sites
NAVIGATE_TIMEOUT = 30000

# Multiple selector candidates for each step — Walmart's DOM varies by A/B test
ATC_SELECTORS = [
    'button[data-automation-id="atc"]',  # Most common: simple "atc" identifier
    'button[data-automation-id="add-to-cart-btn"]',
    'button[data-dca-event="addToCart"]',  # Fallback: DCA event marker
    'button[data-tl-id="ProductPrimaryCTA-cta_add_to_cart_button"]',
    'button[data-dca-name="ItemBuyBoxAddToCartButton"]',
    'button:has-text("Add to cart")',
    'button:has-text("Add to Cart")',
    'button:has-text("Pre-order")',
    'button:has-text("Pre-Order")',
    'button:has-text("Preorder")',
]

CHECKOUT_SELECTORS = [
    'button[data-automation-id="checkout-btn"]',
    'a[data-automation-id="checkout-btn"]',
    'button[data-automation-id="continue-to-checkout"]',
    'a[data-automation-id="continue-to-checkout"]',
    'button:has-text("Continue to checkout")',
    'a:has-text("Continue to checkout")',
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
    def __init__(self, success: bool, order_id: Optional[str] = None, confirmation_url: Optional[str] = None, error: Optional[str] = None):
        self.success = success
        self.order_id = order_id
        self.confirmation_url = confirmation_url
        self.error = error

    def __repr__(self):
        if self.success:
            return f"PurchaseResult(SUCCESS, order_id={self.order_id}, url={self.confirmation_url})"
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
        session=None,
    ):
        self._page = page
        self._status_cb = status_callback or (lambda msg: None)
        self._session = session  # WalmartSessionManager — for blocked page solving
        Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)
        # Track last mouse position for realistic click trajectories
        self._last_mouse_x: float = random.uniform(100, 1200)
        self._last_mouse_y: float = random.uniform(100, 700)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def purchase(self, item_id: str, item_url: str) -> PurchaseResult:
        """
        Attempt to purchase one unit of the given item.
        Returns PurchaseResult with success/failure and order ID if successful.
        """
        self._status_cb(f"[PURCHASE] Starting purchase attempt for {item_id}")
        logger.debug("[PURCHASE] Starting: %s", item_id)

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

            # Step 3: Click Add to Cart directly on the product page.
            atc_ok = await self._add_to_cart(item_id)
            if not atc_ok:
                return PurchaseResult(False, error="Add to cart failed")

            # Step 4: Try direct checkout from ATC flyout (mini-cart) if available
            # This is a valid user path and faster. Falls back to cart page if flyout has no checkout button.
            direct_checkout_ok = await self._try_direct_checkout_from_flyout(item_id)
            if direct_checkout_ok:
                logger.info("[PURCHASE] Using fast path: direct checkout from flyout")
                # Minimal think-time since user reviewed in the flyout
                await asyncio.sleep(random.uniform(0.5, 1.5))
            else:
                # Step 5 (fallback): Navigate to /cart, verify item present, click checkout — single visit.
                # Direct navigation to /checkout triggers Walmart's anti-bot ("technical issues").
                # Must click the checkout button from /cart like a real user.
                checkout_ok = await self._cart_and_checkout(item_id)
                if not checkout_ok:
                    return PurchaseResult(False, error="Cart verification or checkout navigation failed")

                # Real user behavior: Review cart before continuing (2-5s think time)
                self._status_cb("[PURCHASE] Reviewing cart (human think-time)...")
                await asyncio.sleep(random.uniform(2.0, 5.0))

            # Step 6: Confirm shipping (pre-saved address — just continue)
            await self._confirm_shipping()

            # Real user behavior: Review shipping before CVV (1-3s think time)
            self._status_cb("[PURCHASE] Reviewing shipping address (human think-time)...")
            await asyncio.sleep(random.uniform(1.0, 3.0))

            # Step 7: Enter CVV if required
            await self._enter_cvv_if_needed()

            # Real user behavior: Review order summary before placing (1-3s think time)
            self._status_cb("[PURCHASE] Reviewing order summary (human think-time)...")
            await asyncio.sleep(random.uniform(1.0, 3.0))

            # Step 7b: Select delivery day if modal appears
            await self._handle_delivery_day_modal(item_id)

            # Step 7c: Dismiss Walmart+ popup if present
            await self._dismiss_walmart_plus_popup(item_id)

            # TEST MODE — stop here (re-read env var at purchase time so toggle works from dashboard)
            checkout_mode = os.environ.get("CHECKOUT_MODE", "PRODUCTION")

            if checkout_mode != "PRODUCTION":
                await self._screenshot(f"test_mode_stop_{item_id}")
                self._status_cb("[PURCHASE] TEST MODE — stopping before Place Order, clearing cart")
                logger.debug("[PURCHASE] TEST MODE — would have placed order for %s", item_id)
                await self._clear_cart()
                return PurchaseResult(True, order_id="TEST_MODE")

            # Step 8: Place order

            order_id, confirmation_url = await self._place_order(item_id)
            if order_id:
                self._status_cb(f"[PURCHASE] ORDER PLACED! ID: {order_id}")
                logger.warning("[PURCHASE] SUCCESS — order ID: %s at %s", order_id, confirmation_url)
                await self._clear_cart()
                return PurchaseResult(True, order_id=order_id, confirmation_url=confirmation_url)
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
        # Activate the tab first — if it's backgrounded, Chrome throttles JS
        # and React hydration slows to a crawl. This is critical for Tab 2
        # (the checkout tab) which must remain the foreground visible tab.
        try:
            await self._page.activate()
        except Exception as e:
            logger.debug("[PURCHASE] Tab activate failed: %s", e)

        # Extract the item ID from the target URL so we can compare against
        # the tab's current location. Walmart uses /ip/{id} and /ip/x/{id}
        # interchangeably (the x is a placeholder slug).
        target_item_id = None
        try:
            m = re.search(r'/ip/(?:x/)?(\d+)', url)
            if m:
                target_item_id = m.group(1)
        except Exception:
            pass

        current_url = ""
        try:
            current_url = self._page.url or ""
        except Exception:
            pass

        already_on_page = False
        if target_item_id and f"/ip/" in current_url and target_item_id in current_url:
            # Tab 2 is already on this product page (pre-warmed or previous purchase).
            # Skip the navigation entirely — React is already hydrated and the ATC
            # button should be immediately clickable. This is the fast path.
            already_on_page = True
            self._status_cb(f"[PURCHASE] Already on product page — skipping navigation")
            logger.info("[PURCHASE] Tab already on %s — skipping navigation", target_item_id)
        else:
            self._status_cb(f"[PURCHASE] Navigating to {url}")
            try:
                await self._page.get(url)
            except Exception as e:
                logger.warning("[PURCHASE] Navigate encountered error: %s", e)
            await asyncio.sleep(random.uniform(0.5, 1.0))

        # Solve /blocked challenge if redirected
        blocked = await self._handle_blocked()
        if blocked:
            # Re-navigate after solving challenge
            self._status_cb(f"[PURCHASE] Re-navigating to {url} after challenge solve")
            try:
                await self._page.get(url)
            except Exception as e:
                logger.warning("[PURCHASE] Re-navigate encountered error: %s", e)
            await asyncio.sleep(random.uniform(0.8, 1.5))
            already_on_page = False  # we just reloaded — need to re-hydrate

        # Wait for React hydration. If we skipped navigation (Tab 2 was already
        # on the page), the button should be immediately available — use a
        # short timeout. Otherwise, give cold navigation up to 8s.
        ready_timeout = 2000 if already_on_page else 13000
        try:
            await self._wait_for_page_ready(timeout=ready_timeout)
        except Exception as e:
            logger.error("[PURCHASE] Page ready check failed: %s", e)
            # Still proceed — button might be visible even if check failed
            self._status_cb("[PURCHASE] Page ready check timed out — proceeding anyway")

    async def _add_to_cart(self, item_id: str) -> bool:
        self._status_cb("[PURCHASE] Looking for Add to Cart button...")

        # --- Locate the ATC button and get its bounding rect ---
        # We do NOT click here — we locate the button, scroll it into view,
        # then use CDP mouse events to click it (indistinguishable from a
        # real user click, unlike element.click() which PerimeterX detects).
        _FIND_ATC_JS = """
            (() => {
                const selectors = [
                    ['button[data-automation-id="atc"]', 'data-automation-id="atc"'],
                    ['button[data-automation-id="add-to-cart-btn"]', 'data-automation-id="add-to-cart-btn"'],
                    ['button[data-dca-event="addToCart"]', 'data-dca-event="addToCart"'],
                    ['button[data-tl-id="ProductPrimaryCTA-cta_add_to_cart_button"]', 'data-tl-id'],
                    ['button[data-dca-name="ItemBuyBoxAddToCartButton"]', 'data-dca-name'],
                ];
                for (const [sel, via] of selectors) {
                    const btn = document.querySelector(sel);
                    if (btn && !btn.disabled) {
                        btn.scrollIntoView({ behavior: 'instant', block: 'center' });
                        const rect = btn.getBoundingClientRect();
                        return { found: true, foundVia: via, x: rect.x, y: rect.y,
                                 w: rect.width, h: rect.height, disabled: false,
                                 text: btn.textContent.slice(0, 50) };
                    }
                    if (btn && btn.disabled) {
                        return { found: true, foundVia: via, disabled: true,
                                 text: btn.textContent.slice(0, 50) };
                    }
                }
                // Text-content fallback
                const buttons = Array.from(document.querySelectorAll('button'));
                const atcBtn = buttons.find(b => {
                    const t = b.textContent.toLowerCase().trim();
                    return (t.includes('add to cart') || t.includes('pre-order') ||
                            t.includes('preorder')) && !b.disabled;
                });
                if (atcBtn) {
                    atcBtn.scrollIntoView({ behavior: 'instant', block: 'center' });
                    const rect = atcBtn.getBoundingClientRect();
                    return { found: true, foundVia: 'text-content', x: rect.x, y: rect.y,
                             w: rect.width, h: rect.height, disabled: false,
                             text: atcBtn.textContent.slice(0, 50) };
                }
                return { found: false, buttonCount: buttons.length,
                         buttonTexts: buttons.slice(0, 5).map(b => b.textContent.slice(0, 30)) };
            })()
        """

        last_result = None
        for attempt in range(10):
            try:
                result = await self._page.evaluate(_FIND_ATC_JS)
                last_result = result

                if result.get('found') and not result.get('disabled'):
                    # Button located — click it via realistic mouse trajectory
                    x = result['x'] + result['w'] / 2 + random.uniform(-5, 5)
                    y = result['y'] + result['h'] / 2 + random.uniform(-3, 3)
                    clicked = await self._realistic_click(x, y, "Add to Cart")
                    if clicked:
                        self._status_cb(f"[PURCHASE] Clicked Add to Cart (attempt {attempt + 1})")
                        logger.info("[PURCHASE] ATC clicked via CDP mouse on attempt %d via %s at (%.0f, %.0f)",
                                    attempt + 1, result.get('foundVia'), x, y)
                        await asyncio.sleep(0.15)
                        break
                    else:
                        # CDP mouse failed — fall back to JS click
                        logger.warning("[PURCHASE] CDP mouse click failed — falling back to JS click")
                        await self._page.evaluate("""
                            (document.querySelector('button[data-automation-id="atc"]') ||
                             document.querySelector('button[data-dca-event="addToCart"]') ||
                             document.querySelector('button[data-automation-id="add-to-cart-btn"]'))?.click()
                        """)
                        self._status_cb(f"[PURCHASE] Clicked Add to Cart via JS fallback (attempt {attempt + 1})")
                        logger.info("[PURCHASE] ATC clicked via JS fallback on attempt %d", attempt + 1)
                        await asyncio.sleep(0.15)
                        break

                # Button not found or disabled — log and retry with jitter
                if not result.get('found'):
                    logger.debug("[PURCHASE] Attempt %d: button not found, %d buttons on page",
                                attempt + 1, result.get('buttonCount', 0))
                elif result.get('disabled'):
                    logger.debug("[PURCHASE] Attempt %d: button disabled: '%s'", attempt + 1, result.get('text'))
                await asyncio.sleep(random.uniform(0.3, 0.8))

            except Exception as e:
                logger.debug("[PURCHASE] Attempt %d: exception: %s", attempt + 1, str(e))
                await asyncio.sleep(random.uniform(0.3, 0.8))

        else:
            # All 10 attempts failed
            await self._screenshot(f"no_atc_{item_id}")
            if last_result and not last_result.get('found'):
                logger.error("[PURCHASE] ATC button not found after 10 attempts: %d buttons, texts: %s",
                            last_result.get('buttonCount'), last_result.get('buttonTexts'))
            else:
                logger.error("[PURCHASE] ATC button not clickable after 10 attempts: %s", last_result)
            self._status_cb("[PURCHASE] ATC button not found — unable to add to cart")
            return False

        # Quick ATC confirmation — one fast check, then move on.
        # The checkout page will catch a failed ATC; don't burn time here.
        await asyncio.sleep(0.3)
        try:
            confirmed = await self._page.evaluate("""
                (() => {
                    const btn = document.querySelector('button[data-automation-id="atc"]')
                             || document.querySelector('button[data-dca-event="addToCart"]');
                    if (btn) {
                        const t = btn.textContent.toLowerCase();
                        if (t.includes('added') || btn.disabled) return 'btn_changed';
                    }
                    if (document.querySelector('[data-automation-id="cart-flyout"]') ||
                        document.querySelector('[data-automation-id="atc-flyout"]'))
                        return 'flyout';
                    return null;
                })()
            """)
            if confirmed:
                self._status_cb(f"[PURCHASE] ATC confirmed — {confirmed}")
                logger.info("[PURCHASE] ATC confirmed via: %s", confirmed)
            else:
                logger.info("[PURCHASE] No immediate ATC confirmation — proceeding to checkout")
        except Exception:
            pass
            logger.info("[PURCHASE] No ATC confirmation — proceeding to cart verification")

        return True

    async def _try_direct_checkout_from_flyout(self, item_id: str) -> bool:
        """
        Try to checkout directly from the ATC flyout (mini-cart popup).
        If the flyout has a checkout button, click it and skip the /cart page entirely.
        Returns True if checkout was reached, False to fall back to cart page.
        """
        logger.info("[PURCHASE] Attempting fast path checkout from ATC flyout...")
        try:
            # Look for checkout button in the flyout
            flyout_checkout_selectors = [
                'button[data-automation-id="checkout-btn"]',  # In flyout context
                'button[data-automation-id="atc-flyout-checkout"]',
                'button:has-text("Proceed to checkout")',
                'button:has-text("Proceed to Checkout")',
                'button:has-text("Checkout")',
                'a:has-text("Proceed to checkout")',
                'a:has-text("Checkout")',
                'button[aria-label*="checkout" i]',
            ]

            # Quick check: is the flyout visible?
            flyout_visible = await self._page.evaluate("""
                () => !!(document.querySelector('[data-automation-id="cart-flyout"]') ||
                        document.querySelector('[data-automation-id="atc-flyout"]'))
            """)

            if not flyout_visible:
                logger.info("[PURCHASE] ATC flyout not visible — falling back to cart page")
                await self._screenshot(f"no_flyout_{item_id}")
                return False

            logger.info("[PURCHASE] ATC flyout detected")

            # Try to find and click checkout button in the flyout
            checkout_btn = await self._find_element(flyout_checkout_selectors, timeout=2000)
            if not checkout_btn:
                logger.info("[PURCHASE] No checkout button found in flyout — falling back to cart page")
                await self._screenshot(f"no_checkout_btn_in_flyout_{item_id}")
                return False

            logger.info("[PURCHASE] Found checkout button in ATC flyout — attempting direct checkout")
            self._status_cb("[PURCHASE] Checking out directly from cart flyout...")
            await checkout_btn.click()

            # Wait for /checkout URL
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline:
                url = self._page.url or ""
                if "/checkout" in url and "/cart" not in url:
                    logger.info("[PURCHASE] Direct checkout succeeded — reached: %s", url)
                    self._status_cb("[PURCHASE] Direct checkout from flyout successful")
                    return True
                if "/blocked" in url:
                    logger.warning("[PURCHASE] Blocked during direct checkout — falling back to cart")
                    return False
                await asyncio.sleep(0.2)

            logger.warning("[PURCHASE] Direct checkout timeout — falling back to cart page")
            return False

        except Exception as e:
            logger.warning("[PURCHASE] Direct checkout attempt failed: %s — falling back to cart", e)
            await self._screenshot(f"flyout_exception_{item_id}")
            return False

    async def _cart_and_checkout(self, item_id: str) -> bool:
        """Navigate to /cart, verify item is present, select Delivery, click Checkout — single visit."""
        self._status_cb("[PURCHASE] Navigating to cart...")

        # Check _px3 cookie age and refresh if approaching expiry
        if self._session and hasattr(self._session, 'needs_rewarm'):
            if self._session.needs_rewarm():
                self._status_cb("[PURCHASE] _px3 cookie approaching expiry — refreshing session...")
                logger.info("[PURCHASE] _px3 age >%ds, refreshing before checkout", 40)
                try:
                    await self._session.warm_session([])
                    logger.debug("[PURCHASE] _px3 refreshed before checkout")
                except Exception as e:
                    logger.warning("[PURCHASE] _px3 refresh failed: %s (continuing anyway)", e)

        # Navigate to /cart (skip if ATC flyout already landed us there)
        current_url = self._page.url or ""
        if "/cart" in current_url and "/blocked" not in current_url:
            logger.info("[PURCHASE] Already on cart page (URL: %s) — skipping navigation", current_url)
        else:
            logger.info("[PURCHASE] Navigating to cart from: %s → %s", current_url, WALMART_CART_URL)
            await self._page.get(WALMART_CART_URL)
            await asyncio.sleep(random.uniform(0.6, 1.0))
            current_url = self._page.url or ""
            logger.info("[PURCHASE] Arrived at: %s", current_url)

        # Handle /blocked on cart page
        current_url = self._page.url or ""
        if "/blocked" in current_url:
            logger.warning("[PURCHASE] Cart navigation hit /blocked — solving challenge")
            self._status_cb("[PURCHASE] Blocked on cart page — solving challenge...")
            solved = await self._handle_blocked()
            if solved:
                self._status_cb("[PURCHASE] Challenge solved — re-navigating to cart")
                await self._page.get(WALMART_CART_URL)
                await asyncio.sleep(random.uniform(1.5, 2.5))
            else:
                logger.error("[PURCHASE] Could not solve /blocked on cart — aborting")
                await self._screenshot(f"blocked_cart_{item_id}")
                return False

        # --- Verify cart has items (poll up to 4s) ---
        cart_items = []
        cart_selectors = [
            '[data-automation-id="cart-item"]',
            '[data-testid="cart-item"]',
            '[data-automation-id="cart-item-container"]',
            '[data-testid="cart-item-container"]',
            'button[data-automation-id="remove-item"]',
            'input[data-automation-id="item-qty"]',
            'button[aria-label*="Remove"]',
        ]
        cart_verified = False
        poll_deadline = time.monotonic() + 4.0
        while time.monotonic() < poll_deadline:
            try:
                cart_items = await self._query_selector_all(cart_selectors)
                if cart_items:
                    self._status_cb(f"[PURCHASE] Cart verified — {len(cart_items)} item(s)")
                    logger.info("[PURCHASE] Cart verified — %d item(s)", len(cart_items))
                    cart_verified = True
                    break
            except Exception:
                pass

            try:
                js_count = await self._page.evaluate("""
                    (() => {
                        try {
                            const nd = window.__NEXT_DATA__;
                            if (!nd) return -1;
                            const cart = nd?.props?.pageProps?.initialData?.data?.cart
                                      || nd?.props?.pageProps?.cart
                                      || nd?.props?.initialProps?.pageData?.cart;
                            if (!cart) return -1;
                            const items = cart.cartLines || cart.lineItems || cart.items || [];
                            return Array.isArray(items) ? items.length : -1;
                        } catch(e) { return -1; }
                    })()
                """)
                if isinstance(js_count, (int, float)) and js_count > 0:
                    self._status_cb(f"[PURCHASE] Cart verified via __NEXT_DATA__ — {int(js_count)} item(s)")
                    logger.info("[PURCHASE] Cart verified via __NEXT_DATA__ — %d item(s)", int(js_count))
                    cart_verified = True
                    break
            except Exception:
                pass

            await asyncio.sleep(0.5)

        if not cart_verified:
            logger.warning("[PURCHASE] Cart selector check failed after 4s poll — trying fallback")
            await self._screenshot(f"cart_verify_fallback_{item_id}")
            current_url = self._page.url or ""
            if "cart" not in current_url:
                logger.warning("[PURCHASE] Cart URL check failed — current URL: %s", current_url)
                self._status_cb("[PURCHASE] Cart appears empty after ATC")
                return False
            try:
                body = await self._page.evaluate("document.body.innerText")
                body_lower = body.lower() if body else ""
                if "your cart is empty" in body_lower:
                    logger.warning("[PURCHASE] Cart EMPTY (body text confirmed) — URL: %s", current_url)
                    await self._screenshot(f"empty_cart_{item_id}")
                    self._status_cb("[PURCHASE] Cart is confirmed empty after ATC — FAILED")
                    return False
                if body:
                    snippet = body[:400].replace("\n", " ")
                    logger.warning("[PURCHASE] Cart fallback: selectors didn't match. URL=%s snippet=%r", current_url, snippet)
                    cart_signals = ["checkout", "place order", "subtotal", "qty", "quantity", "item"]
                    has_cart_signal = any(sig in body_lower for sig in cart_signals)
                    if has_cart_signal:
                        self._status_cb("[PURCHASE] Cart verification: body signals present — proceeding")
                        logger.info("[PURCHASE] Cart body signals found (%s)", next(sig for sig in cart_signals if sig in body_lower))
                    else:
                        self._status_cb("[PURCHASE] Cart verification inconclusive — proceeding anyway")
                        logger.info("[PURCHASE] Cart body has no cart signals — proceeding anyway (selector mismatch)")
                else:
                    await self._screenshot(f"empty_cart_{item_id}")
                    self._status_cb("[PURCHASE] Cart appears empty after ATC")
                    return False
            except Exception as e:
                logger.warning("[PURCHASE] Cart body check exception: %s", e)
                await self._screenshot(f"empty_cart_{item_id}")
                self._status_cb("[PURCHASE] Cart appears empty after ATC")
                return False

        # --- Select Delivery before clicking checkout ---
        await self._select_delivery_on_cart()

        # --- Click checkout button via CDP mouse ---
        self._status_cb("[PURCHASE] Clicking Checkout...")
        btn = await self._find_element(CHECKOUT_SELECTORS, timeout=5000)
        if btn:
            try:
                rect = await btn.apply("""(e) => {
                    e.scrollIntoView({ behavior: 'instant', block: 'center' });
                    const r = e.getBoundingClientRect();
                    return { x: r.x, y: r.y, w: r.width, h: r.height };
                }""")
                if rect and rect.get('w', 0) > 0:
                    x = rect['x'] + rect['w'] / 2 + random.uniform(-3, 3)
                    y = rect['y'] + rect['h'] / 2 + random.uniform(-2, 2)
                    await self._realistic_click(x, y, "Checkout")
                    logger.info("[PURCHASE] Checkout button clicked via realistic mouse trajectory at (%.0f, %.0f)", x, y)
                else:
                    await btn.click()
                    logger.info("[PURCHASE] Checkout button clicked via element.click()")
            except Exception:
                await btn.click()
                logger.info("[PURCHASE] Checkout button clicked via element.click() (fallback)")
        else:
            await self._screenshot("no_checkout_btn")
            logger.warning("[PURCHASE] Checkout button not found on /cart")
            self._status_cb("[PURCHASE] Checkout button not found")
            return False

        # --- Wait for /checkout URL ---
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            url = self._page.url or ""
            if "/checkout" in url and "/cart" not in url:
                logger.info("[PURCHASE] URL reached checkout: %s", url)
                break
            if "/blocked" in url:
                self._status_cb("[PURCHASE] Blocked on checkout navigation — solving...")
                await self._handle_blocked()
                try:
                    await self._page.get(WALMART_CHECKOUT_URL)
                except Exception:
                    pass
            if "/account/login" in url or "/account/signin" in url or "sign-in" in url:
                logger.error("[PURCHASE] Redirected to login — not authenticated: %s", url)
                self._status_cb("[PURCHASE] Login required — set WALMART_EMAIL/WALMART_PASSWORD in .env")
                await self._screenshot("checkout_login_redirect")
                return False
            await asyncio.sleep(0.2)
        else:
            stuck_url = self._page.url or "unknown"
            logger.warning("[PURCHASE] Checkout URL not reached in 10s — still at: %s", stuck_url)
            await self._screenshot("checkout_url_timeout")
            self._status_cb(f"[PURCHASE] Failed to reach /checkout — still at: {stuck_url}")
            return False

        final_url = self._page.url or ""
        if "/checkout" not in final_url or "/cart" in final_url:
            logger.warning("[PURCHASE] Still not at /checkout — URL: %s", final_url)
            await self._screenshot("checkout_wrong_page")
            self._status_cb(f"[PURCHASE] Failed to reach /checkout — still at: {final_url}")
            return False

        # --- Wait for checkout content to load ---
        content_deadline = time.monotonic() + 8.0
        while time.monotonic() < content_deadline:
            try:
                status = await self._page.evaluate("""
                    (() => {
                        const body = (document.body.innerText || '').toLowerCase();
                        if (body.includes('technical issues') || body.includes('technical difficulties'))
                            return 'error_page';
                        if (body.includes('sign in') && body.includes('password'))
                            return 'sign_in';
                        if (body.includes('payment') || body.includes('shipping') ||
                            body.includes('delivery') || body.includes('order summary') ||
                            body.includes('place order') || body.includes('credit') ||
                            body.includes('fulfillment') || body.includes('checkout'))
                            return 'loaded';
                        if (document.querySelector('[data-automation-id="checkout-page"]') ||
                            document.querySelector('[data-page-type="checkout"]') ||
                            document.querySelector('form[id*="checkout"]'))
                            return 'loaded';
                        return null;
                    })()
                """)
                if status == 'loaded':
                    break
                if status == 'error_page':
                    logger.error("[PURCHASE] Checkout page shows 'technical issues'")
                    await self._screenshot("checkout_technical_issues")
                    self._status_cb("[PURCHASE] Checkout error — 'technical issues' (login may be required)")
                    return False
                if status == 'sign_in':
                    logger.error("[PURCHASE] Checkout redirected to sign-in — session expired")
                    await self._screenshot("checkout_sign_in_wall")
                    self._status_cb("[PURCHASE] Checkout blocked — sign-in required")
                    return False
            except Exception:
                pass
            await asyncio.sleep(0.3)
        else:
            logger.warning("[PURCHASE] Checkout page content not loaded in 8s — URL: %s", final_url)
            await self._screenshot("checkout_load_failed")
            return False

        self._status_cb(f"[PURCHASE] On checkout page — URL: {final_url}")
        return True

    async def _select_delivery_on_cart(self):
        """Select Delivery fulfillment on the /cart page before clicking checkout.

        Walmart's cart page shows Pickup/Delivery tiles. Pickup is often the default.
        We must click "Delivery" here — the checkout page inherits this choice.
        """
        try:
            result = await self._page.evaluate("""
                (() => {
                    // Look for Delivery tile/button on cart page
                    // The cart page uses fulfillment tiles with text "Delivery" and "Pickup"
                    const candidates = document.querySelectorAll(
                        'button, [role="tab"], [role="radio"], [role="option"], label, div[tabindex], a'
                    );
                    for (const el of candidates) {
                        const text = (el.textContent || '').trim();
                        // Match elements whose text is just "Delivery" or "Shipping"
                        // (not "Make this order a free delivery" or other promo text)
                        if (/^(Delivery|Ship(ping)?)$/i.test(text) ||
                            (text.toLowerCase().includes('delivery') && text.length < 40 &&
                             !text.toLowerCase().includes('free delivery'))) {
                            const style = window.getComputedStyle(el);
                            if (style.display === 'none' || style.visibility === 'hidden') continue;
                            // Check if already selected
                            const isSelected = el.getAttribute('aria-selected') === 'true' ||
                                               el.getAttribute('aria-pressed') === 'true' ||
                                               el.getAttribute('aria-checked') === 'true' ||
                                               el.classList.contains('selected') ||
                                               el.classList.contains('active');
                            if (isSelected) return { action: 'already_selected', text: text.slice(0, 30) };
                            const rect = el.getBoundingClientRect();
                            return { action: 'clicked', text: text.slice(0, 30), x: rect.left + rect.width/2, y: rect.top + rect.height/2 };
                        }
                    }
                    return { action: 'not_found' };
                })()
            """)
            if result:
                action = result.get('action')
                if action == 'clicked':
                    # Use CDP mouse click for authenticity
                    await self._realistic_click(result['x'], result['y'], "Delivery")
                    await asyncio.sleep(random.uniform(0.5, 1.0))
                    self._status_cb("[PURCHASE] Delivery selected on cart page")
                    logger.info("[PURCHASE] Cart: clicked Delivery tile (text='%s')", result.get('text'))
                elif action == 'already_selected':
                    logger.info("[PURCHASE] Cart: Delivery already selected (text='%s')", result.get('text'))
                else:
                    logger.info("[PURCHASE] Cart: no Delivery tile found — may already be delivery-only")
        except Exception as e:
            logger.warning("[PURCHASE] _select_delivery_on_cart error: %s", e)

    async def _select_delivery_option(self):
        """
        Ensure Delivery (not Pickup/Drive-up) is selected if a fulfillment choice is shown.

        Walmart's fulfillment step renders after React hydrates the checkout component —
        this can take 2-4s after the URL transitions to /checkout. Timeout is set to 5s
        to accommodate slow hydration. Always select Delivery; never allow Pickup through.
        Logs outcome in both the success and not-found cases so silence is never mistaken
        for success.
        """
        # Use a single JS evaluation to find and click the Delivery option.
        # Walmart's checkout fulfillment step shows tiles/radio buttons for Delivery vs Pickup.
        # We must select "Delivery" (not "Shipping" which is a sub-option of delivery,
        # and not "Pickup" which is store collection).
        try:
            result = await self._page.evaluate("""
                (() => {
                    // Strategy 1: data-automation-id selectors (most stable)
                    const autoSelectors = [
                        '[data-automation-id="fulfillment-option-DELIVERY"]',
                        '[data-automation-id="fulfillment-option-SHIPPING"]',
                        '[data-automation-id*="delivery"]',
                        '[data-automation-id*="shipping"]',
                    ];
                    for (const sel of autoSelectors) {
                        const els = document.querySelectorAll(sel);
                        for (const el of els) {
                            const text = (el.textContent || '').toLowerCase();
                            // Skip if this is clearly a Pickup option
                            if (text.includes('pickup') || text.includes('pick up')) continue;
                            const isSelected = el.getAttribute('aria-selected') === 'true' ||
                                               el.getAttribute('aria-pressed') === 'true' ||
                                               el.getAttribute('aria-checked') === 'true' ||
                                               el.classList.contains('selected');
                            if (isSelected) return { action: 'already_selected', via: sel };
                            const rect = el.getBoundingClientRect();
                            return { action: 'clicked', via: sel, x: rect.left + rect.width/2, y: rect.top + rect.height/2 };
                        }
                    }

                    // Strategy 2: text-based — find any clickable element with "delivery" text
                    const candidates = document.querySelectorAll('button, label, [role="radio"], [role="tab"], [role="option"], div[tabindex]');
                    for (const el of candidates) {
                        const text = (el.textContent || '').toLowerCase().trim();
                        if ((text.includes('delivery') || text === 'ship' || text === 'shipping') &&
                            !text.includes('pickup') && !text.includes('pick up')) {
                            const style = window.getComputedStyle(el);
                            if (style.display !== 'none' && style.visibility !== 'hidden') {
                                const isSelected = el.getAttribute('aria-selected') === 'true' ||
                                                   el.getAttribute('aria-pressed') === 'true' ||
                                                   el.getAttribute('aria-checked') === 'true';
                                if (isSelected) return { action: 'already_selected', via: 'text:' + text.slice(0, 30) };
                                const rect = el.getBoundingClientRect();
                                return { action: 'clicked', via: 'text:' + text.slice(0, 30), x: rect.left + rect.width/2, y: rect.top + rect.height/2 };
                            }
                        }
                    }

                    return { action: 'not_found' };
                })()
            """)

            if result:
                action = result.get('action')
                via = result.get('via', '?')
                if action == 'clicked':
                    # Use CDP mouse click for authenticity
                    await self._realistic_click(result['x'], result['y'], "Delivery")
                    await asyncio.sleep(random.uniform(0.3, 0.6))
                    self._status_cb("[PURCHASE] Delivery option selected")
                    logger.info("[PURCHASE] Delivery option clicked via: %s", via)
                    # After selecting delivery, a modal may appear asking for delivery day
                    await asyncio.sleep(random.uniform(0.5, 1.0))
                    # Try to dismiss delivery day modal if it popped up
                    await self._handle_delivery_day_modal("delivery_select")
                elif action == 'already_selected':
                    logger.info("[PURCHASE] Delivery already selected via: %s", via)
                else:
                    logger.info("[PURCHASE] No fulfillment selector found — assuming delivery is default or step not shown")
                    self._status_cb("[PURCHASE] No fulfillment choice shown — continuing")
        except Exception as e:
            logger.warning("[PURCHASE] _select_delivery_option raised: %s", e)
            self._status_cb("[PURCHASE] Delivery selection error — continuing")

    async def _confirm_shipping(self):
        """
        Walmart checkout has 2–3 steps depending on A/B variant: address → (payment) → review.
        Loop through all intermediate steps until Place Order is visible.
        Handles both 2-step and 3-step checkout flows (MAX_STEPS=6 covers both).
        Checks for and dismisses delivery day modals that appear between steps.
        """
        # Validate _px3 is fresh before entering checkout flow
        px3_ok = await self._check_px3_fresh()
        if not px3_ok:
            logger.warning("[PURCHASE] _px3 missing at start of shipping confirmation — continuing but may encounter stale cookie issues")

        # First ensure delivery (not pickup) is selected
        await self._select_delivery_option()

        # RIGHT AFTER delivery selection, a modal may appear asking for delivery day
        # Check and dismiss it BEFORE entering the Continue button loop
        logger.info("[PURCHASE] Checking for delivery day modal after fulfillment selection...")
        await asyncio.sleep(random.uniform(0.5, 1.5))  # Wait for modal to appear if it will
        modal_dismissed = await self._handle_delivery_day_modal("post_delivery_select")
        if modal_dismissed:
            logger.info("[PURCHASE] Modal appeared and was dismissed after delivery selection")
            await asyncio.sleep(random.uniform(1.0, 2.0))  # Extra pause after modal dismissal

        CONTINUE_SELECTORS = [
            'button:has-text("Continue")',
            'button:has-text("Deliver here")',
            'button:has-text("Use this address")',
            'button:has-text("Continue to payment")',
            'button:has-text("Review your order")',
            'button:has-text("Deliver to this address")',
        ]

        MAX_STEPS = 6
        modal_handle_count = 0
        MAX_MODAL_HANDLES = 3  # Prevent infinite modal loop

        for step_num in range(MAX_STEPS):
            await asyncio.sleep(random.uniform(0.3, 0.7))

            # Guard: check for PerimeterX /blocked challenge mid-checkout
            current_url = self._page.url or ""
            if "/blocked" in current_url:
                self._status_cb(f"[PURCHASE] /blocked challenge hit during checkout step {step_num + 1} — solving")
                logger.warning("[PURCHASE] /blocked detected during checkout step loop (step %d)", step_num + 1)
                solved = await self._handle_blocked()
                if not solved:
                    self._status_cb("[PURCHASE] Could not solve /blocked mid-checkout — aborting shipping confirmation")
                    await self._screenshot("blocked_mid_checkout")
                    return

            # Guard: check for sign-in wall (Walmart sometimes ejects to /checkout/#/sign-in)
            current_url = self._page.url or ""
            if "sign-in" in current_url or "login" in current_url:
                self._status_cb("[PURCHASE] Login wall detected mid-checkout — session cookie likely expired")
                logger.error("[PURCHASE] Login wall at checkout step %d — bailing out of shipping loop", step_num + 1)
                await self._screenshot("login_wall_mid_checkout")
                return

            # Guard: Dismiss any delivery day modals that appeared mid-checkout
            # Limit to MAX_MODAL_HANDLES attempts to prevent infinite loop if modal
            # keeps appearing or button doesn't actually close it
            if modal_handle_count < MAX_MODAL_HANDLES:
                modal_dismissed = await self._handle_delivery_day_modal(f"{step_num}")
                if modal_dismissed:
                    modal_handle_count += 1
                    logger.info("[PURCHASE] Delivery day modal handled (%d/%d)", modal_handle_count, MAX_MODAL_HANDLES)
                    # Modal handler now includes 2-4s pause + DOM stability wait.
                    # Do NOT add additional pause here — the handler covers it.
                    # Continue to look for Continue button since modal is gone

            # If Place Order button is now visible, we're on the review step — done
            place_order_visible = await self._find_element(PLACE_ORDER_SELECTORS, timeout=2000)
            if place_order_visible:
                self._status_cb(f"[PURCHASE] Reached review step after {step_num} Continue click(s)")
                return

            # Human think time: pause before looking for Continue button (1-2s)
            await asyncio.sleep(random.uniform(1.0, 2.0))

            # Click the next Continue/advance button
            continue_btn = await self._find_element(CONTINUE_SELECTORS, timeout=4000)
            if continue_btn:
                # Post-click pause: let page transition settle before next step
                await continue_btn.click()
                await asyncio.sleep(random.uniform(1.5, 2.5))
                self._status_cb(f"[PURCHASE] Continue clicked (step {step_num + 1})")
            else:
                # No Continue and no Place Order — checkout stalled
                self._status_cb("[PURCHASE] No Continue or Place Order button found — checkout may be stalled")
                await self._screenshot("checkout_stalled")
                break

        self._status_cb("[PURCHASE] Checkout step advancement complete")

    async def _enter_cvv_if_needed(self):
        """Enter CVV if the payment page requires it."""
        # Check for /blocked challenge before attempting CVV entry
        current_url = self._page.url or ""
        if "/blocked" in current_url:
            self._status_cb("[PURCHASE] Challenge detected during CVV entry — solving...")
            solved = await self._handle_blocked()
            if not solved:
                logger.error("[PURCHASE] Cannot solve /blocked during CVV entry — checkout failed")
                return False

        card_cvv = get_card_cvv()
        if not card_cvv:
            return
        try:
            cvv_input = await self._find_element(CVV_SELECTORS, timeout=4000)
            if cvv_input:
                # Click the input to focus it, then type character-by-character.
                # PerimeterX monitors keystroke timing on payment fields —
                # set_value() is a single atomic write with no key events,
                # which is trivially detectable as automation.
                try:
                    await cvv_input.click()
                    await asyncio.sleep(random.uniform(0.1, 0.3))
                except Exception:
                    pass
                # Clear existing value if any
                await cvv_input.apply("(e) => { e.value = ''; e.focus(); }")
                await asyncio.sleep(random.uniform(0.05, 0.15))
                # Type each digit with human-like inter-key delays
                from zendriver.cdp import input_ as cdp_input
                for char in card_cvv:
                    await self._page.send(cdp_input.dispatch_key_event(
                        type_="keyDown", text=char, key=char,
                    ))
                    # Patch 4 (2026-04-25): Hold duration raised to 50-150ms to match
                    # human dexterity. The prior 10-30ms window is below the minimum
                    # physically achievable keypress hold time and is a PerimeterX signal.
                    await asyncio.sleep(random.uniform(0.05, 0.15))
                    await self._page.send(cdp_input.dispatch_key_event(
                        type_="keyUp", key=char,
                    ))
                    await asyncio.sleep(random.uniform(0.08, 0.15))
                self._status_cb("[PURCHASE] CVV entered")
                await self._human_delay(200, 400)
                # Verify CVV was accepted by reading it back
                try:
                    filled_value = await cvv_input.apply("(e) => e.value")
                    if filled_value != card_cvv:
                        logger.warning(
                            "[PURCHASE] CVV verification failed — typed '%s' but read back '%s'",
                            card_cvv,
                            filled_value,
                        )
                        # Fall back to set_value if char-by-char typing didn't work
                        await cvv_input.set_value(card_cvv)
                        logger.info("[PURCHASE] CVV set via fallback set_value")
                    else:
                        logger.debug("[PURCHASE] CVV verified successfully")
                except Exception as e:
                    logger.warning("[PURCHASE] CVV read-back failed: %s", e)
        except Exception as e:
            logger.warning("[PURCHASE] CVV entry failed: %s", e)  # CVV not required or already filled

    async def _place_order(self, item_id: str) -> tuple[Optional[str], Optional[str]]:
        """Click Place Order and return (order_id, confirmation_url) tuple."""
        self._status_cb("[PURCHASE] Clicking Place Order...")
        btn = await self._find_element(PLACE_ORDER_SELECTORS, timeout=10000)
        if not btn:
            await self._screenshot(f"no_place_order_{item_id}")
            logger.warning("[PURCHASE] Place Order button not found")
            return None, None

        await self._screenshot(f"before_place_order_{item_id}")
        await btn.click()
        self._status_cb("[PURCHASE] Place Order clicked — waiting for confirmation...")

        # Wait for order confirmation page — polling loop (zendriver has no wait_for_url)
        await asyncio.sleep(random.uniform(0.03, 0.10))  # CDP flush yield
        pre_click_url = self._page.url

        # Regex matches Walmart's known confirmation URL patterns
        _confirm_pattern = re.compile(r".*(order-confirmation|order/confirm|thank-you|order-placed).*")
        deadline = time.monotonic() + 20.0
        cvv_modal_check_count = 0
        while time.monotonic() < deadline:
            current_url = self._page.url or ""

            # Check for CVV modal that might appear after Place Order click (race condition)
            if cvv_modal_check_count < 3:  # Only check first 3 iterations to avoid spam
                modal_dismissed = await self._handle_delivery_day_modal(f"place_order_cvv_{cvv_modal_check_count}")
                if modal_dismissed:
                    cvv_modal_check_count += 1
                    logger.info("[PURCHASE] Modal dismissed after Place Order click")
                    await asyncio.sleep(random.uniform(1.0, 2.0))  # Pause after modal dismissal

            # Check for /blocked challenge immediately after Place Order click
            if "/blocked" in current_url:
                self._status_cb("[PURCHASE] Challenge detected after Place Order click — solving...")
                solved = await self._handle_blocked()
                if not solved:
                    logger.error("[PURCHASE] Cannot solve /blocked after Place Order click")
                    return None, None
                # After solving, continue waiting for confirmation
            elif _confirm_pattern.match(current_url):
                break
            await asyncio.sleep(0.3)
        else:
            # URL didn't match confirmation pattern — wait and check if page changed at all
            await asyncio.sleep(3)
            if self._page.url == pre_click_url:
                logger.warning("[PURCHASE] Page did not navigate after Place Order click")

        # Extract order ID and capture confirmation URL
        order_id = await self._extract_order_id()
        confirmation_url = self._page.url or None
        await self._screenshot(f"order_confirmation_{item_id}")
        return order_id, confirmation_url

    async def _extract_order_id(self) -> Optional[str]:
        for selector in ORDER_CONFIRM_SELECTORS:
            try:
                el = await self._page.query_selector(selector)
                if el:
                    text = await el.apply("(e) => e.innerText")
                    if text:
                        # Try to pull just the numeric order ID
                        numbers = re.findall(r'\d{6,}', text)
                        if numbers:
                            return numbers[0]
                        return text.strip()
            except Exception:
                continue

        # Check URL for order ID
        url = self._page.url or ""
        if "order-confirmation" in url or "order" in url:
            numbers = re.findall(r'\d{7,}', url)
            if numbers:
                return numbers[0]
            # URL is confirmation page but no numeric ID found
            logger.warning("[PURCHASE] Order confirmation page detected but could not extract order ID from selectors or URL. "
                          f"URL: {url} — order tracking may be incomplete")
            return None

        logger.warning("[PURCHASE] Could not detect order confirmation page. "
                      f"Current URL: {url} — order ID extraction failed")
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
            await self._page.get(WALMART_CART_URL)
            await asyncio.sleep(random.uniform(0.8, 1.2))
            cart_items = await self._query_selector_all([
                '[data-automation-id="cart-item"]',           # last verified: 2026-04-10
                '[data-testid="cart-item"]',
                '[data-automation-id="cart-item-container"]',
                '[data-testid="cart-item-container"]',
                'button[data-automation-id="remove-item"]',   # proxy: only exists when item present
                'button[aria-label*="Remove"]',
                # NOTE: .cart-item removed — Walmart hashes class names on every deploy
            ])
            found = bool(cart_items)
            if found:
                logger.debug("[PURCHASE] Pre-check: %d item(s) already in cart", len(cart_items))
        except Exception as e:
            logger.warning("[PURCHASE] Cart pre-check failed: %s", e)
            found = False
        finally:
            # Always navigate back to the product page
            if item_url:
                try:
                    await self._page.get(item_url)
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
            await self._page.get(WALMART_CART_URL)
            await asyncio.sleep(random.uniform(0.8, 1.2))
            cart_items = await self._query_selector_all([
                '[data-automation-id="cart-item"]',           # last verified: 2026-04-10
                '[data-testid="cart-item"]',
                '[data-automation-id="cart-item-container"]',
                '[data-testid="cart-item-container"]',
                'button[data-automation-id="remove-item"]',   # proxy: only exists when item present
                'button[aria-label*="Remove"]',
                # NOTE: .cart-item removed — Walmart hashes class names on every deploy
            ])
            if not cart_items:
                return  # cart already empty — nothing to do
            logger.debug("[PURCHASE] Cart has %d item(s) — clearing before ATC", len(cart_items))
            self._status_cb(f"[PURCHASE] Clearing {len(cart_items)} existing cart item(s)")
            remove_btns = await self._query_selector_all([
                'button[data-automation-id="remove-item"]',
                'button[aria-label*="Remove"]',
            ])
            # Also try XPath for "Remove" text buttons
            try:
                xpath_btns = await self._page.xpath('//button[contains(., "Remove")]')
                remove_btns.extend(xpath_btns)
            except Exception:
                pass
            for btn in remove_btns:
                try:
                    await btn.click()
                    await asyncio.sleep(0.8)
                except Exception:
                    pass
        except Exception as e:
            logger.warning("[PURCHASE] _clear_cart_if_needed failed: %s", e)

    async def _handle_delivery_day_modal(self, item_id: str):
        """
        Select a delivery day if modal appears during checkout.
        Walmart may show a modal asking for delivery date/window after clicking Continue.
        Waits for modal to be visible first, selects a date option, then verifies modal closes.

        Critical: After modal close, waits 2-4s + DOM stabilization before returning.
        Akamai detects immediate continuation after modal as bot behavior. Humans read/pause.
        """
        try:
            # First check if modal is even visible
            modal_visible = await self._page.evaluate("""
                () => {
                    const dialog = document.querySelector('[role="dialog"]');
                    if (!dialog) return false;
                    const rect = dialog.getBoundingClientRect();
                    const style = window.getComputedStyle(dialog);
                    const visible = rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    return visible;
                }
            """)

            if not modal_visible:
                logger.debug("[PURCHASE] Modal check: No visible dialog found")
                return False

            logger.warning("[PURCHASE] ⚠️  DELIVERY MODAL DETECTED — initiating stealthy dismiss")

            logger.info("[PURCHASE] Delivery day modal detected — finding option button")

            # Priority: text-based delivery day options (Today, Tomorrow, etc.)
            day_selectors = [
                'button:has-text("Today")',
                'button:has-text("Tomorrow")',
                'button:has-text("Next Day")',
                'button:has-text("Standard")',
                'button[role="radio"][aria-label*="Today"]',
                'button[role="radio"][aria-label*="tomorrow"]',
                'button[data-automation-id*="delivery-day"]',
                'button[data-automation-id*="delivery-window"]',
                # Fallback: any clickable in dialog that's not a close button
                '[role="dialog"] button:not([aria-label*="close"])',
            ]

            day_btn = await self._find_element(day_selectors, timeout=2000)
            if not day_btn:
                logger.debug("[PURCHASE] No delivery option button found — modal may not be active")
                return False

            logger.info("[PURCHASE] Found delivery day button — clicking")
            # Get coordinates from the matched element (the one _find_element returned)
            location = await day_btn.apply("""(e) => {
                const rect = e.getBoundingClientRect();
                return { x: rect.left + rect.width/2, y: rect.top + rect.height/2 };
            }""")

            if location:
                # Click via CDP mouse events for authenticity
                clicked = await self._cdp_mouse_click(location['x'], location['y'])
                if clicked:
                    await asyncio.sleep(random.uniform(0.04, 0.12))
                else:
                    # Fallback to element click if CDP click fails
                    await day_btn.click()
            else:
                # Fallback to element click if coordinate extraction fails
                await day_btn.click()

            await asyncio.sleep(random.uniform(0.5, 1.0))

            # Verify modal has closed
            modal_still_visible = await self._page.evaluate("""
                () => {
                    const dialog = document.querySelector('[role="dialog"]');
                    if (!dialog) return false;
                    const rect = dialog.getBoundingClientRect();
                    const style = window.getComputedStyle(dialog);
                    return rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                }
            """)

            if not modal_still_visible:
                logger.warning("[PURCHASE] ✓ Modal dismissed successfully — entering stealth pause")
                self._status_cb("[PURCHASE] Selected delivery day")

                # CRITICAL: Wait after modal close before resuming checkout
                # Akamai's behavioral analysis flags immediate button clicks after modals close.
                # Human users pause to read the updated form. Vary pause based on modal complexity
                # (more options = longer to read).
                # Estimate complexity from modal state (typically 2-4 options)
                complexity = 2  # default: simple modal with 2 options
                pause_time = random.uniform(0.8 + complexity * 0.5, 2.0 + complexity * 1.0)
                logger.info("[PURCHASE] Modal pause: %.1fs (complexity=%d)", pause_time, complexity)
                await asyncio.sleep(pause_time)

                # After the stealth pause, inject a mouse movement toward the form
                # to simulate a user moving their cursor back to interact with the main checkout
                # Randomize coordinates to avoid fixed-pattern detection
                x = random.uniform(400, 700)
                y = random.uniform(300, 500)
                await self._page.mouse_move(x=x, y=y)
                await asyncio.sleep(random.uniform(0.2, 0.5))
                logger.info("[PURCHASE] ✓ Modal dismissed — resuming checkout flow")

                return True
            else:
                # Modal is still visible — the button we clicked didn't close it
                # This can happen if we clicked a button that isn't a valid selection
                logger.warning("[PURCHASE] Clicked button but modal still visible — may have clicked wrong button")
                self._status_cb("[PURCHASE] Selected delivery day")
                return False  # Return False to avoid infinite loop

        except Exception as e:
            logger.debug("[PURCHASE] Delivery day selection failed: %s (continuing)", e)
            return False

    async def _dismiss_walmart_plus_popup(self, item_id: str):
        """Dismiss Walmart+ upsell popup if present before Place Order."""
        popup_selectors = [
            'button[data-automation-id="walmart-plus-no-thanks"]',
            'button:has-text("No thanks")',
            'button:has-text("No, thanks")',
            'button:has-text("Skip")',
            'button[aria-label*="close" i]',
            '[data-automation-id="modal-close"]',
            'button[data-automation-id="offer-no-thanks"]',
        ]
        try:
            popup_btn = await self._find_element(popup_selectors, timeout=2000)
            if popup_btn:
                await popup_btn.click()
                await self._screenshot(f"walmart_plus_popup_dismissed_{item_id}")
                logger.info("[PURCHASE] Dismissed Walmart+ popup")
                self._status_cb("[PURCHASE] Dismissed Walmart+ popup")
            else:
                logger.debug("[PURCHASE] No Walmart+ popup detected — proceeding")
        except Exception as e:
            logger.debug("[PURCHASE] Walmart+ popup dismiss check failed: %s (continuing)", e)

    async def _dismiss_any_modal(self):
        """
        Aggressively dismiss any visible modal/dialog by searching for common close patterns.
        Used as a fallback when specific modal handling fails.
        Returns True if a modal was found and dismissed.
        """
        try:
            # Search for any visible modal with close/dismiss buttons
            result = await self._page.evaluate("""
                (() => {
                    // Look for any visible modal/dialog overlay
                    const modals = document.querySelectorAll('[role="dialog"], [role="alertdialog"], .modal, [class*="modal"], [class*="Modal"], .overlay, [class*="overlay"], [data-testid*="modal"]');

                    for (const modal of modals) {
                        const style = window.getComputedStyle(modal);
                        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;

                        // Found a visible modal — try to close it
                        const closeButtons = [
                            ...modal.querySelectorAll('button[aria-label*="close" i]'),
                            ...modal.querySelectorAll('button[aria-label*="dismiss" i]'),
                            ...modal.querySelectorAll('[data-automation-id*="close"]'),
                            ...modal.querySelectorAll('button:last-child'),  // Often the close/no button is rightmost
                        ];

                        for (const btn of closeButtons) {
                            if (btn.offsetParent !== null) {  // Is visible
                                btn.click();
                                return { found: true, method: 'close_button' };
                            }
                        }

                        // If no close button, try Escape key
                        const event = new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', keyCode: 27 });
                        document.dispatchEvent(event);
                        return { found: true, method: 'escape_key' };
                    }

                    return { found: false };
                })()
            """)

            if result and result.get('found'):
                logger.debug("[PURCHASE] Dismissed modal via %s", result.get('method'))
                await asyncio.sleep(random.uniform(0.3, 0.6))
                return True
        except Exception as e:
            logger.debug("[PURCHASE] Generic modal dismiss failed: %s", e)

        return False

    async def _clear_cart(self):
        """
        Clear all items from the cart. Called after every purchase attempt
        (success or failure) to ensure a clean state for the next attempt.
        After cleanup, re-warm Tab 1 to refresh cookies for the stock monitor.
        """
        try:
            await self._page.get(WALMART_CART_URL)
            await asyncio.sleep(1)
            remove_btns = await self._query_selector_all([
                'button[data-automation-id="remove-item"]',
                'button[aria-label*="Remove"]',
            ])
            # Also try XPath for "Remove" text buttons
            try:
                xpath_btns = await self._page.xpath('//button[contains(., "Remove")]')
                remove_btns.extend(xpath_btns)
            except Exception:
                pass
            if not remove_btns:
                logger.debug("[PURCHASE] Cart already empty — no cleanup needed")
            else:
                logger.debug("[PURCHASE] Post-attempt cleanup: removing %d cart item(s)", len(remove_btns))
                for btn in remove_btns:
                    try:
                        await btn.click()
                        await asyncio.sleep(random.uniform(0.6, 1.0))
                    except Exception:
                        pass
        except Exception as e:
            logger.warning("[PURCHASE] _clear_cart cleanup failed: %s", e)

        # Re-warm Tab 1 after purchase to refresh stock monitor cookies
        if self._session and hasattr(self._session, 'rewarm_tab1'):
            try:
                logger.info("[PURCHASE] Re-warming Tab 1 after purchase...")
                await self._session.rewarm_tab1()
            except Exception as e:
                logger.warning("[PURCHASE] Tab 1 rewarm failed: %s (stock monitor may be briefly blocked)", e)

    async def _handle_blocked(self) -> bool:
        """
        Detect and solve Walmart's /blocked PerimeterX challenge on the checkout tab.
        Delegates to the session manager's challenge solver if available, otherwise
        does a basic wait-and-check.
        Returns True if a /blocked page was detected and solved, False if not blocked.
        """
        current_url = self._page.url or ""
        if "/blocked" not in current_url:
            return False  # not blocked

        logger.warning("[PURCHASE] /blocked detected — URL: %s", current_url)
        self._status_cb("[PURCHASE] Bot challenge detected — attempting solve...")

        # Use session manager's challenge solver if available (it has press-and-hold logic)
        if self._session and hasattr(self._session, '_handle_blocked_page_on'):
            try:
                solved = await self._session._handle_blocked_page_on(self._page)
                if solved:
                    self._status_cb("[PURCHASE] Challenge solved via session manager")
                    logger.info("[PURCHASE] /blocked challenge solved")
                    return True
                else:
                    self._status_cb("[PURCHASE] Challenge solve FAILED")
                    logger.error("[PURCHASE] /blocked challenge could not be solved")
                    await self._screenshot("blocked_unsolved")
                    return True  # was blocked, but couldn't solve
            except Exception as e:
                logger.error("[PURCHASE] Challenge solver error: %s", e)

        # Fallback: wait and check if it auto-resolves (some challenges are time-based)
        logger.info("[PURCHASE] No session manager — waiting for auto-resolve")
        for attempt in range(3):
            await asyncio.sleep(5)
            if "/blocked" not in (self._page.url or ""):
                self._status_cb("[PURCHASE] Challenge auto-resolved")
                return True

        self._status_cb("[PURCHASE] Challenge could not be resolved")
        await self._screenshot("blocked_no_session")
        return True  # was blocked

    async def _human_delay(self, min_ms: int = 80, max_ms: int = 300):
        """Add a small randomized delay to simulate human interaction timing."""
        delay = random.randint(min_ms, max_ms) / 1000.0
        await asyncio.sleep(delay)

    async def _check_px3_fresh(self) -> bool:
        """
        Check if _px3 cookie is present. If missing, session may need rewarm.
        _px3 expires ~60s after generation; if checkout takes >50s, it may be stale.
        Returns True if _px3 is present, False if missing/stale.
        """
        if not self._session or not self._session._page:
            return True  # Can't check, assume ok
        try:
            from zendriver.cdp import network
            raw = await self._session._page.send(network.get_all_cookies())
            cookie_dict = {c.name: c.value for c in raw}
            if "_px3" not in cookie_dict:
                logger.warning("[PURCHASE] _px3 cookie missing — may need rewarm")
                return False
            return True
        except Exception as e:
            logger.warning("[PURCHASE] Failed to check _px3: %s", e)
            return True  # Can't check, assume ok

    async def _realistic_click(self, x: float, y: float, selector: str = None) -> bool:
        """
        Click with pre-movement trajectory to simulate human behavior.

        Humans always move the mouse from its previous position before clicking.
        Akamai detects instant teleport clicks as bot signals. This method emits
        a Bezier-like trajectory (3-7 intermediate points) before the final click.
        """
        try:
            from zendriver.cdp import input_ as cdp_input

            # Starting position (track between clicks)
            current_x = self._last_mouse_x
            current_y = self._last_mouse_y

            # Emit intermediate points along the path (Bezier-like)
            steps = random.randint(3, 7)
            for i in range(steps):
                # Linear interpolation from current to target
                t = i / steps
                move_x = int(current_x + (x - current_x) * t)
                move_y = int(current_y + (y - current_y) * t)

                # Emit mouse movement event
                await self._page.send(cdp_input.dispatch_mouse_event(
                    type_="mouseMoved", x=move_x, y=move_y, pointer_type="mouse"
                ))
                # 10-50ms between moves (human-like)
                await asyncio.sleep(random.uniform(0.01, 0.05))

            # Final move to exact target
            await self._page.send(cdp_input.dispatch_mouse_event(
                type_="mouseMoved", x=x, y=y, pointer_type="mouse"
            ))
            await asyncio.sleep(random.uniform(0.02, 0.08))

            # Now click (press + release)
            await self._page.send(cdp_input.dispatch_mouse_event(
                type_="mousePressed", x=x, y=y,
                button=cdp_input.MouseButton.LEFT, buttons=1,
                click_count=1, pointer_type="mouse"
            ))
            await asyncio.sleep(random.uniform(0.04, 0.12))

            await self._page.send(cdp_input.dispatch_mouse_event(
                type_="mouseReleased", x=x, y=y,
                button=cdp_input.MouseButton.LEFT, buttons=0,
                click_count=1, pointer_type="mouse"
            ))

            # Track position for next click
            self._last_mouse_x = x
            self._last_mouse_y = y

            if selector:
                logger.debug("[PURCHASE] Realistic click on %s at (%.0f, %.0f) with trajectory", selector, x, y)
            return True
        except Exception as e:
            logger.warning("[PURCHASE] Realistic click failed: %s", e)
            return False

    async def _cdp_mouse_click(self, x: float, y: float) -> bool:
        """Click at viewport coordinates using CDP Input.dispatchMouseEvent.

        Produces a mouseMoved → mousePressed → mouseReleased sequence that is
        indistinguishable from a real pointer click. PerimeterX behavioral
        analysis can detect element.click() (no coordinates, no pointer trail)
        but cannot distinguish a CDP-dispatched mouse event from a real one.
        """
        try:
            from zendriver.cdp import input_ as cdp_input
            # Move the mouse to the target — real users don't teleport
            await self._page.send(cdp_input.dispatch_mouse_event(
                type_="mouseMoved", x=x, y=y, pointer_type="mouse"
            ))
            await asyncio.sleep(random.uniform(0.02, 0.08))
            # Press
            await self._page.send(cdp_input.dispatch_mouse_event(
                type_="mousePressed", x=x, y=y,
                button=cdp_input.MouseButton.LEFT, buttons=1,
                click_count=1, pointer_type="mouse"
            ))
            await asyncio.sleep(random.uniform(0.04, 0.12))
            # Release
            await self._page.send(cdp_input.dispatch_mouse_event(
                type_="mouseReleased", x=x, y=y,
                button=cdp_input.MouseButton.LEFT, buttons=0,
                click_count=1, pointer_type="mouse"
            ))
            return True
        except Exception as e:
            logger.warning("[PURCHASE] CDP mouse click failed: %s", e)
            return False

    # Single JS snippet that checks __NEXT_DATA__ + all ATC selectors + text fallback
    # in one browser round-trip. Returns immediately when page is ready.
    _PAGE_READY_JS = """
        (() => {
            if (!window.__NEXT_DATA__) return { ready: false, reason: 'no_next_data' };

            const selectors = [
                ['button[data-automation-id="atc"]', 'data-automation-id="atc"'],
                ['button[data-automation-id="add-to-cart-btn"]', 'data-automation-id="add-to-cart-btn"'],
                ['button[data-dca-event="addToCart"]', 'data-dca-event="addToCart"'],
                ['button[data-tl-id="ProductPrimaryCTA-cta_add_to_cart_button"]', 'data-tl-id'],
                ['button[data-dca-name="ItemBuyBoxAddToCartButton"]', 'data-dca-name'],
            ];
            for (const [sel, via] of selectors) {
                const el = document.querySelector(sel);
                if (el) {
                    const s = window.getComputedStyle(el);
                    const visible = s.display !== 'none' && s.visibility !== 'hidden' && parseFloat(s.opacity) > 0;
                    const hasSize = !!(el.offsetWidth || el.offsetHeight);
                    if (visible) return { ready: true, via: sel, hasSize };
                    return { ready: false, reason: 'hidden', via: sel };
                }
            }
            // Text fallback — find button with "add" + "cart" text
            for (const btn of document.querySelectorAll('button')) {
                const t = btn.textContent.toLowerCase();
                if (t.includes('add') && t.includes('cart')) {
                    const s = window.getComputedStyle(btn);
                    if (s.display !== 'none' && s.visibility !== 'hidden') {
                        return { ready: true, via: 'text:' + btn.textContent.slice(0, 30), hasSize: !!(btn.offsetWidth || btn.offsetHeight) };
                    }
                }
            }
            return { ready: false, reason: 'no_button' };
        })()
    """

    async def _wait_for_page_ready(self, timeout: int = 5000):
        """Wait for Walmart product page React to hydrate and render ATC button.

        Uses a single JS evaluation per poll tick (one browser round-trip) instead
        of separate calls per selector. Typical exit: 0.5-2s on warm pages.
        """
        deadline = time.monotonic() + (timeout / 1000.0)
        started = time.monotonic()

        while time.monotonic() < deadline:
            try:
                result = await self._page.evaluate(self._PAGE_READY_JS)
                if result and result.get('ready'):
                    elapsed = time.monotonic() - started
                    logger.info("[PURCHASE] Page ready in %.1fs — ATC button found via: %s (hasSize=%s)",
                                elapsed, result.get('via'), result.get('hasSize'))
                    self._status_cb(f"[PURCHASE] Page ready — ATC button found ({elapsed:.1f}s)")
                    return
            except Exception as e:
                logger.debug("[PURCHASE] Page ready check error: %s", e)
            await asyncio.sleep(0.15)

        elapsed = time.monotonic() - started
        logger.warning("[PURCHASE] Page ready timeout after %.1fs", elapsed)
        self._status_cb(f"[PURCHASE] Page ready timeout after {elapsed:.1f}s — proceeding anyway")

    async def _wait_for_dom_stability(self, timeout: int = 4000):
        """
        Wait for DOM to stabilize after a modal dismissal or form change.
        Polls for absence of mutations for 800ms, indicating React has finished re-rendering.
        Returns True if stabilized within timeout, False if timeout hit (continues anyway).
        """
        started = time.monotonic()
        deadline = started + timeout / 1000.0
        stable_until = started

        while time.monotonic() < deadline:
            # Check if any mutations occurred in the last 50ms
            mutations_detected = await self._page.evaluate("""
                (() => {
                    let count = 0;
                    const observer = new MutationObserver(() => {
                        count++;
                    });
                    observer.observe(document.body, {
                        childList: true,
                        subtree: true,
                        attributes: true,
                        characterData: false
                    });
                    // Let it observe for 50ms
                    return new Promise(resolve => {
                        setTimeout(() => {
                            observer.disconnect();
                            resolve(count > 0);
                        }, 50);
                    });
                })()
            """)

            if mutations_detected:
                # Reset the stable timer
                stable_until = time.monotonic()
                await asyncio.sleep(0.1)
            else:
                # No mutations in the last 50ms
                if time.monotonic() - stable_until >= 0.8:
                    # DOM has been stable for 800ms
                    elapsed = time.monotonic() - started
                    logger.info("[PURCHASE] DOM stabilized after %.1fs", elapsed)
                    return True
                await asyncio.sleep(0.05)

        elapsed = time.monotonic() - started
        logger.debug("[PURCHASE] DOM stability timeout after %.1fs (continuing anyway)", elapsed)
        return False

    async def _find_element(self, selectors: list[str], timeout: int = 5000):
        """Try each selector in order, return the first matching visible element.

        Handles both plain CSS selectors and :has-text() patterns by converting
        the latter to XPath queries (zendriver does not support :has-text()).
        """
        if not selectors:
            return None
        per_selector_timeout = max(0.5, (timeout / len(selectors)) / 1000)  # seconds
        for selector in selectors:
            try:
                if ':has-text(' in selector:
                    # Convert to XPath with case-insensitive text matching
                    m = re.match(r'(\w+):has-text\("([^"]+)"\)', selector)
                    if m:
                        tag, text = m.group(1), m.group(2)
                        # Use case-insensitive XPath: translate() normalizes to lowercase for comparison
                        text_lower = text.lower()
                        xpath = f'//{tag}[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "{text_lower}")]'
                        deadline = time.monotonic() + per_selector_timeout
                        while time.monotonic() < deadline:
                            els = await self._page.xpath(xpath)
                            if els:
                                el = els[0]
                                vis = await el.apply("(e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length)")
                                if vis:
                                    return el
                            await asyncio.sleep(0.1)
                else:
                    el = await self._page.wait_for(selector=selector, timeout=per_selector_timeout)
                    if el:
                        vis = await el.apply("(e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length)")
                        if vis:
                            return el
            except Exception:
                continue
        return None

    async def _query_selector_all(self, selectors: list[str]) -> list:
        """
        Query for all elements matching any selector in the list.
        Queries each selector individually and deduplicates by DOM node identity.

        Deduplication uses apply() to get each element's outerHTML hash as a
        proxy for node identity — two Python element objects wrapping the
        same DOM node will produce identical outerHTML strings at that moment.
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
                        # nodes or apply failures we fall back to a unique counter
                        # so each element still gets added exactly once per selector.
                        node_key = await el.apply(
                            "(el) => (el.getAttribute('data-automation-id') || "
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
            await self._page.save_screenshot(filename=path)
            logger.debug("[PURCHASE] Screenshot: %s", path)
        except Exception as e:
            logger.warning("[PURCHASE] Screenshot failed: %s", e)

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
        session=None,
    ):
        self._page = page
        self._status_cb = status_callback or (lambda msg: None)
        self._session = session  # WalmartSessionManager — for blocked page solving
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
                        self._status_cb("[PURCHASE] TEST MODE — stopping before Place Order, clearing cart")
                        await self._clear_cart()
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

            # Step 3: Click Add to Cart directly — no pre-checking the cart.
            # Previous flow navigated to /cart twice before ATC (to check if
            # item was already there, and to clear stale items). This added
            # 4 extra navigations and 10-16s of latency before ATC even ran.
            # Now we just click ATC immediately. If the cart has stale items,
            # _verify_cart will catch it after the click.
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

            if checkout_mode != "PRODUCTION":
                await self._screenshot(f"test_mode_stop_{item_id}")
                self._status_cb("[PURCHASE] TEST MODE — stopping before Place Order, clearing cart")
                logger.debug("[PURCHASE] TEST MODE — would have placed order for %s", item_id)
                await self._clear_cart()
                return PurchaseResult(True, order_id="TEST_MODE")

            # Step 8: Place order
            if final_purchase != "YES":
                await self._screenshot(f"pre_place_order_{item_id}")
                self._status_cb("[PURCHASE] FINAL_PURCHASE not set — stopping before Place Order")
                return PurchaseResult(True, order_id="DRY_RUN")

            order_id = await self._place_order(item_id)
            if order_id:
                self._status_cb(f"[PURCHASE] ORDER PLACED! ID: {order_id}")
                logger.warning("[PURCHASE] SUCCESS — order ID: %s", order_id)
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
            await asyncio.sleep(random.uniform(1.2, 2.0))

        # Solve /blocked challenge if redirected
        blocked = await self._handle_blocked()
        if blocked:
            # Re-navigate after solving challenge
            self._status_cb(f"[PURCHASE] Re-navigating to {url} after challenge solve")
            try:
                await self._page.get(url)
            except Exception as e:
                logger.warning("[PURCHASE] Re-navigate encountered error: %s", e)
            await asyncio.sleep(random.uniform(1.8, 2.8))
            already_on_page = False  # we just reloaded — need to re-hydrate

        # Wait for React hydration. If we skipped navigation (Tab 2 was already
        # on the page), the button should be immediately available — use a
        # short timeout. Otherwise, give cold navigation up to 8s.
        ready_timeout = 2000 if already_on_page else 8000
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
                    # Button located — click it via CDP mouse events
                    x = result['x'] + result['w'] / 2 + random.uniform(-5, 5)
                    y = result['y'] + result['h'] / 2 + random.uniform(-3, 3)
                    clicked = await self._cdp_mouse_click(x, y)
                    if clicked:
                        self._status_cb(f"[PURCHASE] Clicked Add to Cart (attempt {attempt + 1})")
                        logger.info("[PURCHASE] ATC clicked via CDP mouse on attempt %d via %s at (%.0f, %.0f)",
                                    attempt + 1, result.get('foundVia'), x, y)
                        await asyncio.sleep(0.5)
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
                        await asyncio.sleep(0.5)
                        break

                # Button not found or disabled — log and retry
                if not result.get('found'):
                    logger.debug("[PURCHASE] Attempt %d: button not found, %d buttons on page",
                                attempt + 1, result.get('buttonCount', 0))
                elif result.get('disabled'):
                    logger.debug("[PURCHASE] Attempt %d: button disabled: '%s'", attempt + 1, result.get('text'))
                await asyncio.sleep(0.5)

            except Exception as e:
                logger.debug("[PURCHASE] Attempt %d: exception: %s", attempt + 1, str(e))
                await asyncio.sleep(0.5)

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

        # Wait for ATC confirmation — look for the flyout/modal/drawer or cart count change.
        # Walmart shows either a "View cart" modal, an "Added to cart" flyout, or
        # the ATC button text changes to "Added" / a checkmark.
        # JS click is instant, so 6s should be plenty for Walmart to respond.
        atc_confirmed = False
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            try:
                # Check for success indicators
                for sel in [
                    'button:has-text("View cart")',
                    'a:has-text("View cart")',
                    'button:has-text("Go to cart")',
                    'a:has-text("Go to cart")',
                    'button:has-text("Added to cart")',
                    'button:has-text("Added")',
                    '[data-automation-id="cart-flyout"]',
                    '[data-automation-id="atc-flyout"]',
                ]:
                    if ':has-text(' in sel:
                        m = re.match(r'(\w+):has-text\("([^"]+)"\)', sel)
                        if m:
                            tag, text = m.group(1), m.group(2)
                            xpath = f'//{tag}[contains(., "{text}")]'
                            els = await self._page.xpath(xpath)
                            if els:
                                self._status_cb(f"[PURCHASE] ATC confirmed — '{text}' visible")
                                logger.info("[PURCHASE] ATC flyout/modal detected: %s", text)
                                atc_confirmed = True
                                # Click "View cart" / "Go to cart" if it's a navigation link
                                if "cart" in text.lower():
                                    try:
                                        await els[0].click()
                                        self._status_cb("[PURCHASE] Clicked cart link from ATC flyout")
                                        await asyncio.sleep(1.5)
                                    except Exception:
                                        pass
                                break
                    else:
                        el = await self._page.query_selector(sel)
                        if el:
                            atc_confirmed = True
                            self._status_cb("[PURCHASE] ATC confirmed via flyout element")
                            break
                if atc_confirmed:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.4)

        if not atc_confirmed:
            # No flyout seen — not necessarily a failure, ATC may have worked silently
            self._status_cb("[PURCHASE] No ATC flyout detected — will verify cart directly")
            logger.info("[PURCHASE] No ATC confirmation flyout — proceeding to cart verification")
            await asyncio.sleep(1.0)

        return True

    async def _verify_cart(self, item_id: str) -> bool:
        self._status_cb("[PURCHASE] Verifying cart...")

        # If we're already on the cart page (e.g. from clicking "View cart" in ATC flyout),
        # skip the direct navigation which is more likely to trigger /blocked.
        current_url = self._page.url or ""
        if "/cart" in current_url and "/blocked" not in current_url:
            logger.info("[PURCHASE] Already on cart page (URL: %s) — skipping navigation", current_url)
        else:
            # Add a human-like delay before navigating to cart
            await self._human_delay(500, 1200)
            logger.info("[PURCHASE] Navigating to cart from: %s → %s", current_url, WALMART_CART_URL)
            await self._page.get(WALMART_CART_URL)
            await asyncio.sleep(2.0)
            current_url = self._page.url or ""
            logger.info("[PURCHASE] Arrived at: %s", current_url)

        # Handle /blocked on cart page
        current_url = self._page.url or ""
        if "/blocked" in current_url:
            logger.warning("[PURCHASE] Cart navigation hit /blocked — solving challenge")
            self._status_cb("[PURCHASE] Blocked on cart page — solving challenge...")
            solved = await self._handle_blocked()
            if solved:
                # Re-navigate to cart after solving
                self._status_cb("[PURCHASE] Challenge solved — re-navigating to cart")
                await self._page.get(WALMART_CART_URL)
                await asyncio.sleep(2.0)
            else:
                logger.error("[PURCHASE] Could not solve /blocked on cart — aborting")
                await self._screenshot(f"blocked_cart_{item_id}")
                return False

        # Poll for cart items — React hydration can take 3-8s after navigation.
        # Retry every 500ms for up to 8s before falling through to JS-state and body-text fallbacks.
        # Selector list uses stable data-* attributes only — Walmart hashes class names on every deploy.
        # last verified: 2026-04-10
        cart_items = []
        cart_selectors = [
            '[data-automation-id="cart-item"]',          # primary (confirmed 2026-04-07)
            '[data-testid="cart-item"]',                 # A/B variant
            '[data-automation-id="cart-item-container"]',# wrapper variant seen in some cohorts
            '[data-testid="cart-item-container"]',
            # Proxy selectors: if a remove button or quantity input is visible, items are present.
            # These key off stable aria/automation attributes that survive DOM restructuring.
            'button[data-automation-id="remove-item"]',  # remove btn only exists when item present
            'input[data-automation-id="item-qty"]',      # qty spinner only exists when item present
            'button[aria-label*="Remove"]',              # aria-label stable across deploys
            # NOTE: .cart-item removed — Walmart hashes class names on every deploy
        ]
        poll_deadline = time.monotonic() + 8.0
        while time.monotonic() < poll_deadline:
            try:
                cart_items = await self._query_selector_all(cart_selectors)
                if cart_items:
                    break
            except Exception:
                pass

            # Also try a JS-state probe on each poll tick — reads Walmart's React store
            # directly from window.__NEXT_DATA__ without relying on any CSS selectors.
            # This fires every iteration so we exit as soon as either signal resolves.
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
                    return True
            except Exception:
                pass

            await asyncio.sleep(0.5)

        if cart_items:
            self._status_cb(f"[PURCHASE] Cart verified — {len(cart_items)} item(s)")
            logger.info("[PURCHASE] Cart verified — %d item(s) — proceeding to checkout", len(cart_items))
            return True

        logger.warning("[PURCHASE] Cart selector check failed — no cart-item elements found after 8s poll — trying fallback")

        # Fallback: check URL still on cart and no "empty cart" text.
        # Screenshot first so we can see exactly what DOM Walmart rendered.
        await self._screenshot(f"cart_verify_fallback_{item_id}")
        current_url = self._page.url or ""
        if "cart" not in current_url:
            logger.warning("[PURCHASE] Cart URL check failed — current URL: %s", current_url)
            await self._screenshot(f"empty_cart_{item_id}")
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
                # Log a body snippet to help diagnose what Walmart is actually showing
                snippet = body[:400].replace("\n", " ")
                logger.warning(
                    "[PURCHASE] Cart fallback: selectors didn't match but body exists. URL=%s snippet=%r",
                    current_url,
                    snippet,
                )
                # Check for positive cart signals in body text as a secondary confidence boost
                cart_signals = ["checkout", "place order", "subtotal", "qty", "quantity", "item"]
                has_cart_signal = any(sig in body_lower for sig in cart_signals)
                if has_cart_signal:
                    self._status_cb("[PURCHASE] Cart verification: body signals present — proceeding to checkout")
                    logger.info("[PURCHASE] Cart body signals found (%s) — proceeding to checkout",
                                next(sig for sig in cart_signals if sig in body_lower))
                else:
                    self._status_cb("[PURCHASE] Cart verification inconclusive (no selectors, no signals, body present)")
                    logger.info("[PURCHASE] Cart body has no cart signals — proceeding to checkout anyway (selector mismatch)")
                # Better to proceed to checkout and hit a real failure than loop on unmatched selectors
                return True
        except Exception as e:
            logger.warning("[PURCHASE] Cart body check exception: %s", e)

        await self._screenshot(f"empty_cart_{item_id}")
        self._status_cb("[PURCHASE] Cart appears empty after ATC")
        return False

    async def _go_to_checkout(self) -> bool:
        # Check _px3 cookie age and refresh if approaching expiry (40s threshold, 20s safety before 60s TTL)
        if self._session and hasattr(self._session, 'needs_rewarm'):
            if self._session.needs_rewarm():
                self._status_cb("[PURCHASE] _px3 cookie approaching expiry — refreshing session...")
                logger.info("[PURCHASE] _px3 age >%ds, refreshing before checkout", 40)
                try:
                    await self._session.warm_session([])  # Refresh cookies without item browsing
                    logger.debug("[PURCHASE] _px3 refreshed before checkout")
                except Exception as e:
                    logger.warning("[PURCHASE] _px3 refresh failed: %s (continuing anyway)", e)

        self._status_cb("[PURCHASE] Clicking Checkout...")
        btn = await self._find_element(CHECKOUT_SELECTORS, timeout=8000)
        if not btn:
            await self._screenshot("no_checkout_btn")
            logger.warning("[PURCHASE] Checkout button not found")
            return False

        await btn.click()
        logger.info("[PURCHASE] Checkout button clicked — waiting for checkout page")

        # Wait for checkout URL — polling loop (zendriver has no wait_for_url).
        # Match /checkout specifically — NOT /cart?checkout=... or similar cart-page params
        # that contain the word "checkout" but are still on the cart page.
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            current_url = self._page.url or ""
            if "/checkout" in current_url and "/cart" not in current_url:
                logger.info("[PURCHASE] URL reached checkout: %s", current_url)
                break
            if "/blocked" in current_url:
                self._status_cb("[PURCHASE] Blocked on checkout navigation — solving...")
                await self._handle_blocked()
                break
            await asyncio.sleep(0.3)
        else:
            # URL did not reach /checkout within 15s — log current URL for diagnosis
            stuck_url = self._page.url or "unknown"
            logger.warning("[PURCHASE] Checkout URL not reached in 15s — still at: %s", stuck_url)
            await self._screenshot("checkout_url_timeout")
            await asyncio.sleep(random.uniform(2.5, 4.0))

        # Confirm we are actually on the /checkout path before declaring success.
        # This catches the case where the URL poll timed out or matched a false positive.
        final_url = self._page.url or ""
        if "/checkout" not in final_url or "/cart" in final_url:
            logger.warning("[PURCHASE] _go_to_checkout: still not at /checkout — URL: %s", final_url)
            await self._screenshot("checkout_wrong_page")
            self._status_cb(f"[PURCHASE] Failed to reach /checkout — still at: {final_url}")
            return False

        # Verify checkout page content loaded — look for checkout-specific content
        checkout_loaded = False
        try:
            body = await self._page.evaluate("document.body.innerText")
            body_lower = body.lower() if body else ""
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
            logger.warning("[PURCHASE] Checkout page did not load correctly — URL: %s", final_url)
            await self._screenshot("checkout_load_failed")
            return False

        self._status_cb(f"[PURCHASE] On checkout page — URL: {final_url}")
        return True

    async def _select_delivery_option(self):
        """
        Ensure Delivery (not Pickup/Drive-up) is selected if a fulfillment choice is shown.

        Walmart's fulfillment step renders after React hydrates the checkout component —
        this can take 2-4s after the URL transitions to /checkout. Timeout is set to 5s
        to accommodate slow hydration. Always select Delivery; never allow Pickup through.
        Logs outcome in both the success and not-found cases so silence is never mistaken
        for success.
        """
        # data-automation-id variants are more stable than text-based selectors.
        # The fulfillment tile for Ship/Delivery uses automation IDs containing "SHIPPING"
        # or "DELIVERY". Text-based selectors are kept as fallbacks for A/B variants.
        delivery_selectors = [
            '[data-automation-id="fulfillment-option-SHIPPING"]',
            '[data-automation-id="fulfillment-option-DELIVERY"]',
            '[data-automation-id*="shipping"][role="radio"]',
            '[data-automation-id*="delivery"][role="radio"]',
            'button:has-text("Delivery")',
            'button:has-text("Ship")',
            # Radio input variants (less common on current Walmart checkout SPA)
            'label:has-text("Delivery") input[type="radio"]',
            'label:has-text("Ship") input[type="radio"]',
        ]
        try:
            delivery_el = await self._find_element(delivery_selectors, timeout=5000)
            if delivery_el:
                await delivery_el.click()
                await self._human_delay(400, 800)
                self._status_cb("[PURCHASE] Delivery fulfillment option selected")
                logger.info("[PURCHASE] Delivery option clicked")
                # Confirm selection was accepted — element should now be selected/active
                try:
                    is_selected = await delivery_el.apply(
                        "(e) => e.getAttribute('aria-selected') === 'true' || "
                        "e.getAttribute('aria-pressed') === 'true' || "
                        "e.getAttribute('aria-checked') === 'true' || "
                        "e.checked === true"
                    )
                    if is_selected:
                        logger.info("[PURCHASE] Delivery option confirmed selected")
                    else:
                        logger.warning("[PURCHASE] Delivery option clicked but aria-selected not true — may not have registered")
                        await self._screenshot("delivery_selection_unconfirmed")
                except Exception as e:
                    logger.warning("[PURCHASE] Could not verify delivery selection state: %s", e)
            else:
                # No fulfillment choice shown — either already on delivery, item is ship-only,
                # or the step was already completed. Log so we know which case we're in.
                logger.info("[PURCHASE] No fulfillment selector found in 5s — assuming delivery already active or step not shown")
                self._status_cb("[PURCHASE] No fulfillment choice shown — continuing (delivery assumed active)")
        except Exception as e:
            logger.warning("[PURCHASE] _select_delivery_option raised: %s", e)
            self._status_cb("[PURCHASE] Delivery selection failed — see logs")

    async def _confirm_shipping(self):
        """
        Walmart checkout has 2–3 steps depending on A/B variant: address → (payment) → review.
        Loop through all intermediate steps until Place Order is visible.
        Handles both 2-step and 3-step checkout flows (MAX_STEPS=6 covers both).
        """
        # First ensure delivery (not pickup) is selected
        await self._select_delivery_option()

        CONTINUE_SELECTORS = [
            'button:has-text("Continue")',
            'button:has-text("Deliver here")',
            'button:has-text("Use this address")',
            'button:has-text("Continue to payment")',
            'button:has-text("Review your order")',
            'button:has-text("Deliver to this address")',
        ]

        MAX_STEPS = 6
        for step_num in range(MAX_STEPS):
            # Randomized step delay — human-range (1.0–2.5s); avoids bot-like cart-to-checkout speed
            await asyncio.sleep(random.uniform(1.0, 2.5))

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

            # If Place Order button is now visible, we're on the review step — done
            place_order_visible = await self._find_element(PLACE_ORDER_SELECTORS, timeout=2000)
            if place_order_visible:
                self._status_cb(f"[PURCHASE] Reached review step after {step_num} Continue click(s)")
                return

            # Click the next Continue/advance button
            continue_btn = await self._find_element(CONTINUE_SELECTORS, timeout=4000)
            if continue_btn:
                await continue_btn.click()
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
                    await asyncio.sleep(random.uniform(0.01, 0.03))
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

        # Wait for order confirmation page — polling loop (zendriver has no wait_for_url)
        await asyncio.sleep(0.05)  # CDP flush yield
        pre_click_url = self._page.url

        # Regex matches Walmart's known confirmation URL patterns
        _confirm_pattern = re.compile(r".*(order-confirmation|order/confirm|thank-you|order-placed).*")
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            current_url = self._page.url or ""
            # Check for /blocked challenge immediately after Place Order click
            if "/blocked" in current_url:
                self._status_cb("[PURCHASE] Challenge detected after Place Order click — solving...")
                solved = await self._handle_blocked()
                if not solved:
                    logger.error("[PURCHASE] Cannot solve /blocked after Place Order click")
                    return None
                # After solving, continue waiting for confirmation
            elif _confirm_pattern.match(current_url):
                break
            await asyncio.sleep(0.3)
        else:
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
            await asyncio.sleep(1)
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
            await asyncio.sleep(1)
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

    async def _clear_cart(self):
        """
        Clear all items from the cart. Called after every purchase attempt
        (success or failure) to ensure a clean state for the next attempt.
        Silently ignores errors — best-effort cleanup only.
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
                return
            logger.debug("[PURCHASE] Post-attempt cleanup: removing %d cart item(s)", len(remove_btns))
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
        fbt_css_selectors = [
            f'[data-item-id="{item_id}"] button[data-automation-id="atc"]',
            f'[data-item-id="{item_id}"] button[data-automation-id="add-to-cart-btn"]',
            '[data-testid="frequently-bought-together"] button[data-automation-id="atc"]',
            '[data-testid="frequently-bought-together"] button[data-automation-id="add-to-cart-btn"]',
        ]
        fbt_xpath_selectors = [
            f'//*[@data-item-id="{item_id}"]//button[contains(., "Add to cart")]',
            '//*[@data-testid="frequently-bought-together"]//button[contains(., "Add to cart")]',
            # NOTE: class-based XPaths removed — Walmart hashes class names on every deploy
        ]

        # Try CSS selectors first
        for selector in fbt_css_selectors:
            try:
                btn = await self._page.query_selector(selector)
                if btn:
                    is_vis = await btn.apply("(e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length)")
                    if is_vis:
                        await btn.scroll_into_view()
                        await asyncio.sleep(0.2)
                        await btn.click()
                        self._status_cb("[PURCHASE] FBT ATC clicked — queue bypass attempted")
                        logger.debug("[PURCHASE] FBT ATC bypass clicked for %s", item_id)
                        await asyncio.sleep(2)
                        return True
            except Exception:
                continue

        # Try XPath selectors
        for xpath in fbt_xpath_selectors:
            try:
                els = await self._page.xpath(xpath)
                if els:
                    btn = els[0]
                    is_vis = await btn.apply("(e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length)")
                    if is_vis:
                        await btn.scroll_into_view()
                        await asyncio.sleep(0.2)
                        await btn.click()
                        self._status_cb("[PURCHASE] FBT ATC clicked — queue bypass attempted")
                        logger.debug("[PURCHASE] FBT ATC bypass clicked for %s", item_id)
                        await asyncio.sleep(2)
                        return True
            except Exception:
                continue

        logger.debug("[PURCHASE] No FBT ATC button found for %s", item_id)
        return False

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

    async def _wait_for_page_ready(self, timeout: int = 5000):
        """Wait for Walmart product page React to hydrate and render ATC button.

        Checks for:
        1. window.__NEXT_DATA__ to exist (React page state initialized)
        2. At least one ATC selector to be visible in DOM
        3. Falls back to simple timeout if button never becomes visible

        Timeout in milliseconds.
        """
        deadline = time.monotonic() + (timeout / 1000.0)
        last_error = None
        started = time.monotonic()
        button_found_at = None
        next_data_found_at = None

        while time.monotonic() < deadline:
            try:
                # Check if React has initialized
                has_next_data = await self._page.evaluate("!!window.__NEXT_DATA__")
                if not has_next_data:
                    await asyncio.sleep(0.2)
                    continue

                if not next_data_found_at:
                    next_data_found_at = time.monotonic() - started
                    logger.debug("[PURCHASE] __NEXT_DATA__ found at %.1fs", next_data_found_at)

                # Check if any ATC selector is visible.
                # CSS-attribute selectors use query_selector; :has-text() selectors
                # are converted to XPath (patchright does not support :has-text() in
                # query_selector).  Both paths share the same visibility check so that
                # text-content selectors also contribute to the early-exit signal.
                # This matters because Walmart lazy-loads the buybox component island;
                # data-automation-id attributes are attached by React during the final
                # hydration commit, so text content may appear before the attributes do.

                # First, try all explicit selectors
                for sel in ATC_SELECTORS:
                    try:
                        el = None
                        if ':has-text(' in sel:
                            m = re.match(r'(\w+):has-text\("([^"]+)"\)', sel)
                            if m:
                                tag, text = m.group(1), m.group(2)
                                text_lower = text.lower()
                                xpath = (
                                    f'//{tag}[contains('
                                    f'translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ",'
                                    f' "abcdefghijklmnopqrstuvwxyz"), "{text_lower}")]'
                                )
                                els = await self._page.xpath(xpath)
                                if els:
                                    el = els[0]
                        else:
                            el = await self._page.query_selector(sel)

                        if el:
                            # Check visibility with a loose threshold:
                            # - Must not have display:none or visibility:hidden
                            # - Size check is secondary (button may be loading or have 0 dimensions)
                            vis = await el.apply("""(e) => {
                                const rect = e.getBoundingClientRect();
                                const style = window.getComputedStyle(e);
                                const display = style.display !== 'none';
                                const visibility = style.visibility !== 'hidden';
                                const opacity = parseFloat(style.opacity) > 0;
                                const hasSize = !!(e.offsetWidth || e.offsetHeight || rect.width || rect.height);
                                return {
                                    found: true,
                                    visible: display && visibility && opacity,
                                    hasSize: hasSize,
                                    offsetWidth: e.offsetWidth,
                                    offsetHeight: e.offsetHeight,
                                    rectWidth: rect.width,
                                    rectHeight: rect.height,
                                    display: style.display,
                                    visibility: style.visibility,
                                    opacity: style.opacity
                                };
                            }""")
                            # Button is ready if it's not hidden by CSS even if size is 0
                            if vis.get('visible'):
                                elapsed = time.monotonic() - started
                                logger.info("[PURCHASE] Page ready in %.1fs — ATC button found via: %s (hasSize=%s)", elapsed, sel, vis.get('hasSize'))
                                self._status_cb(f"[PURCHASE] Page ready — ATC button found ({elapsed:.1f}s)")
                                return
                            elif vis.get('found') and not button_found_at:
                                button_found_at = time.monotonic() - started
                                logger.debug("[PURCHASE] ATC button exists at %.1fs but display=hidden or opacity=0: %s", button_found_at, vis)
                    except Exception as e:
                        logger.debug("[PURCHASE] Error checking visibility for %s: %s", sel, str(e))
                        continue

                # Fallback: if explicit selectors didn't work, look for any button
                # with "add" and "cart" in text (catches variations like "Add to Cart", "Add to cart", etc)
                try:
                    fallback_buttons = await self._page.evaluate("""
                        Array.from(document.querySelectorAll('button')).filter(b => {
                            const text = b.textContent.toLowerCase();
                            const style = window.getComputedStyle(b);
                            return text.includes('add') && text.includes('cart') &&
                                   style.display !== 'none' && style.visibility !== 'hidden';
                        }).slice(0, 1).map(b => ({
                            text: b.textContent.slice(0, 30),
                            visible: !!(b.offsetWidth || b.offsetHeight)
                        }));
                    """)
                    if fallback_buttons:
                        btn = fallback_buttons[0]
                        elapsed = time.monotonic() - started
                        logger.info("[PURCHASE] Page ready in %.1fs — ATC button found via fallback search (text='%s')", elapsed, btn.get('text'))
                        self._status_cb(f"[PURCHASE] Page ready — ATC button found ({elapsed:.1f}s)")
                        return
                except Exception as e:
                    logger.debug("[PURCHASE] Fallback button search failed: %s", str(e))

                await asyncio.sleep(0.3)
            except Exception as e:
                last_error = e
                logger.debug("[PURCHASE] Exception in page ready check: %s", str(e))
                await asyncio.sleep(0.3)

        elapsed = time.monotonic() - started
        logger.warning("[PURCHASE] Page ready timeout: elapsed=%.1fs, next_data_at=%.1fs, button_at=%s",
                      elapsed, next_data_found_at or -1, button_found_at or 'never')
        self._status_cb(f"[PURCHASE] Page ready timeout after {elapsed:.1f}s — proceeding anyway")

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

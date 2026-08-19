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
import math
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
    FAST_DROP_MODE,
    PX3_MAX_AGE_SECONDS,
    get_card_cvv,
    get_final_purchase,
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
        # Set by _clear_cart() when post-purchase cart navigation hits /blocked.
        # Manager reads this to avoid immediate re-queue on a poisoned _px3 cookie.
        self._last_cart_clear_blocked: bool = False

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def purchase(self, item_id: str, item_url: str, quantity: int = 1) -> PurchaseResult:
        """
        Attempt to purchase the requested quantity of the given item.

        Quantity is bumped on the cart page via the qty input — ATC always
        adds qty=1, then if quantity > 1 we set the cart qty input via CDP
        keystrokes before clicking Checkout. The hard ceiling is enforced
        by the caller (manager); this method trusts the value passed in.
        """
        self._desired_quantity = max(1, int(quantity or 1))
        self._status_cb(f"[PURCHASE] Starting purchase attempt for {item_id}" +
                        (f" (qty={self._desired_quantity})" if self._desired_quantity > 1 else ""))
        logger.debug("[PURCHASE] Starting: %s (qty=%d)", item_id, self._desired_quantity)

        try:
            # Step 1: Navigate to product page
            await self._navigate(item_url)

            # Step 2: Handle virtual queue if present (2026 ticket-API model)
            # Pass self._session through. In the current single-Chrome path
            # this is a WalmartSessionManager (no in_queue attr — flag ops
            # are no-ops). In a future Phase 2 path it would be a
            # resilient-stack SessionEntry (in_queue flag activates and
            # protects the queue ticket from keepalive eviction).
            #
            # Eviction retry: when Walmart returns state=expired we
            # re-enter the queue up to WALMART_QUEUE_MAX_RETRIES times
            # with a _px3 refresh between attempts — the readiness doc
            # explicitly calls for "re-enter from fresh session" on
            # eviction. Conservative default (2) keeps total wall-clock
            # under ~3× detect_and_wait timeout so a single drop window
            # doesn't get burned chasing a hot queue.
            from .queue_handler import QueueState, AdmissionLikelihood
            queue = QueueHandler(self._page, self._status_cb, session=self._session)
            max_queue_retries = int(os.environ.get("WALMART_QUEUE_MAX_RETRIES", "2"))
            queue_attempts = 0

            while True:
                ticket = await queue.detect()
                if ticket is None:
                    # Not in a queue (or no longer queued) — fall through to ATC
                    break
                queue_attempts += 1
                attempt_label = f"{queue_attempts}/{max_queue_retries + 1}"
                self._status_cb(
                    f"[PURCHASE] In queue (attempt {attempt_label}) — awaiting ticket state..."
                )
                final_ticket = await queue.detect_and_wait()

                if final_ticket is None:
                    return PurchaseResult(False, error="Queue handler returned None")

                if final_ticket.state == QueueState.VALID:
                    self._status_cb(
                        f"[PURCHASE] Queue admitted (state=valid, attempt {attempt_label})"
                    )
                    # After admission, re-navigate to ensure we're on the product page.
                    # Walmart's queue JS sometimes navigates the tab automatically;
                    # this is a no-op in that case.
                    await self._navigate(item_url)
                    # After 10-30min in queue, _px3 is usually stale despite Walmart's
                    # own JS refresh attempts. Refresh before checkout or PerimeterX
                    # will block on /cart navigation.
                    if (self._session
                            and hasattr(self._session, 'needs_rewarm')
                            and self._session.needs_rewarm()):
                        self._status_cb("[PURCHASE] Queue exited — refreshing _px3 before checkout...")
                        try:
                            await self._session.warm_session([])
                        except Exception as _e:
                            logger.warning("[PURCHASE] Post-queue _px3 refresh failed: %s", _e)
                    break  # admitted — proceed to ATC

                if final_ticket.state == QueueState.EXPIRED:
                    if queue_attempts > max_queue_retries:
                        return PurchaseResult(
                            False,
                            error=(f"Queue evicted after {queue_attempts} attempt(s): "
                                   f"{final_ticket}"),
                        )
                    self._status_cb(
                        f"[PURCHASE] Queue evicted (attempt {attempt_label}) — "
                        f"refreshing _px3 + re-entering..."
                    )
                    # Refresh _px3 so the re-entry has a current cookie. PerimeterX
                    # may have soured on the prior _px3 during the long wait; a
                    # warm cycle gives the next entry a clean fingerprint.
                    if self._session and hasattr(self._session, 'warm_session'):
                        try:
                            await self._session.warm_session([])
                        except Exception as _e:
                            logger.warning("[PURCHASE] Pre-retry _px3 refresh failed: %s", _e)
                    # Navigate back to the PDP — Walmart will re-issue the queue
                    # interstitial if the drop is still active. The next loop
                    # iteration's detect() picks up the fresh ticket.
                    await self._navigate(item_url)
                    continue

                # Non-VALID, non-EXPIRED terminal: unlikely-streak bail or pending-timeout
                if final_ticket.likelihood == AdmissionLikelihood.UNLIKELY:
                    return PurchaseResult(
                        False, error="Queue admissionLikelihood=unlikely — bailing",
                    )
                return PurchaseResult(False, error=f"Queue timeout: {final_ticket}")

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

                # Real user behavior: Review cart before continuing
                self._status_cb("[PURCHASE] Reviewing cart (human think-time)...")
                await asyncio.sleep(random.uniform(0.3, 0.6) if FAST_DROP_MODE else random.uniform(2.0, 5.0))

            # Step 6: Confirm shipping (pre-saved address — just continue)
            await self._confirm_shipping()

            # Real user behavior: Review shipping before CVV
            self._status_cb("[PURCHASE] Reviewing shipping address (human think-time)...")
            await asyncio.sleep(random.uniform(0.2, 0.4) if FAST_DROP_MODE else random.uniform(1.0, 3.0))

            # Step 7: Enter CVV if required
            await self._enter_cvv_if_needed()

            # Real user behavior: Review order summary before placing
            self._status_cb("[PURCHASE] Reviewing order summary (human think-time)...")
            await asyncio.sleep(random.uniform(0.2, 0.4) if FAST_DROP_MODE else random.uniform(1.0, 3.0))

            # Step 7b: Select delivery day if modal appears
            await self._handle_delivery_day_modal(item_id)

            # Step 7c: Dismiss Walmart+ popup if present
            await self._dismiss_walmart_plus_popup(item_id)

            # TEST MODE — stop here (re-read env var at purchase time so toggle works from dashboard)
            checkout_mode = os.environ.get("CHECKOUT_MODE", "TEST")
            final_purchase = get_final_purchase()

            if checkout_mode != "PRODUCTION":
                await self._screenshot(f"test_mode_stop_{item_id}")
                self._status_cb("[PURCHASE] TEST MODE — stopping before Place Order, clearing cart")
                logger.debug("[PURCHASE] TEST MODE — would have placed order for %s", item_id)
                await self._clear_cart()
                return PurchaseResult(True, order_id="TEST_MODE")

            if final_purchase != "YES":
                await self._screenshot(f"dry_run_stop_{item_id}")
                self._status_cb("[PURCHASE] DRY RUN — CHECKOUT_MODE=PRODUCTION but FINAL_PURCHASE≠YES, clearing cart")
                logger.warning("[PURCHASE] DRY RUN — set FINAL_PURCHASE=YES to actually place orders")
                await self._clear_cart()
                return PurchaseResult(True, order_id="DRY_RUN")

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
            # Wrap navigation in wait_for — same pattern PM applied to _clear_cart.
            # A stalled `/blocked` redirect or hung page load could otherwise
            # freeze the purchase flow indefinitely.
            try:
                await asyncio.wait_for(self._page.get(url), timeout=20.0)
            except asyncio.TimeoutError:
                logger.warning("[PURCHASE] Navigation to %s timed out after 20s", url)
            except Exception as e:
                logger.warning("[PURCHASE] Navigate encountered error: %s", e)
            await asyncio.sleep(random.uniform(0.5, 1.0))

        # Solve /blocked challenge if redirected
        blocked = await self._handle_blocked()
        if blocked:
            # Re-navigate after solving challenge
            self._status_cb(f"[PURCHASE] Re-navigating to {url} after challenge solve")
            try:
                await asyncio.wait_for(self._page.get(url), timeout=20.0)
            except asyncio.TimeoutError:
                logger.warning("[PURCHASE] Re-navigation to %s timed out after 20s", url)
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
                        # _realistic_click failed (CDP write error). Fall back to a
                        # raw CDP press+release at the same coordinates — still produces
                        # pointer events that PerimeterX expects. Avoid JS .click(),
                        # which fires a click with no pointer trace and is one of the
                        # strongest bot signals on the ATC button specifically.
                        try:
                            from zendriver.cdp import input_ as cdp_input
                            await self._page.send(cdp_input.dispatch_mouse_event(
                                type_="mouseMoved", x=x, y=y, pointer_type="mouse"
                            ))
                            await asyncio.sleep(random.uniform(0.02, 0.06))
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
                            self._last_mouse_x = x
                            self._last_mouse_y = y
                            self._status_cb(f"[PURCHASE] Clicked Add to Cart via CDP fallback (attempt {attempt + 1})")
                            logger.info("[PURCHASE] ATC clicked via raw CDP fallback on attempt %d at (%.0f, %.0f)",
                                        attempt + 1, x, y)
                        except Exception as cdp_err:
                            # Last-ditch: JS click. Logged loudly because it's a detection risk.
                            logger.error("[PURCHASE] Raw CDP click also failed (%s) — last-resort JS click", cdp_err)
                            await self._page.evaluate("""
                                (document.querySelector('button[data-automation-id="atc"]') ||
                                 document.querySelector('button[data-dca-event="addToCart"]') ||
                                 document.querySelector('button[data-automation-id="add-to-cart-btn"]'))?.click()
                            """)
                            self._status_cb(f"[PURCHASE] Clicked Add to Cart via JS last-resort (attempt {attempt + 1})")
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
            # CDP trajectory click — the flyout fast-path bypasses /cart, but
            # the Checkout button click is still a scrutinized action.
            try:
                rect = await checkout_btn.apply("""(e) => {
                    e.scrollIntoView({ behavior: 'instant', block: 'center' });
                    const r = e.getBoundingClientRect();
                    return { x: r.x, y: r.y, w: r.width, h: r.height };
                }""")
                if rect and rect.get('w', 0) > 0:
                    x = rect['x'] + rect['w'] / 2 + random.uniform(-3, 3)
                    y = rect['y'] + rect['h'] / 2 + random.uniform(-2, 2)
                    clicked = await self._realistic_click(x, y, "Flyout Checkout")
                    if not clicked:
                        await checkout_btn.click()
                else:
                    await checkout_btn.click()
            except Exception as e:
                logger.debug("[PURCHASE] Flyout checkout rect lookup failed (%s) — fallback click", e)
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
                logger.info("[PURCHASE] _px3 age >%ds, refreshing before checkout", PX3_MAX_AGE_SECONDS)
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
            try:
                await asyncio.wait_for(self._page.get(WALMART_CART_URL), timeout=15.0)
            except asyncio.TimeoutError:
                logger.warning("[PURCHASE] Cart navigation timed out after 15s — aborting")
                await self._screenshot(f"cart_nav_timeout_{item_id}")
                return False
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
                try:
                    await asyncio.wait_for(self._page.get(WALMART_CART_URL), timeout=15.0)
                except asyncio.TimeoutError:
                    logger.warning("[PURCHASE] Post-challenge cart re-navigation timed out — aborting")
                    return False
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

        # --- Bump quantity if > 1 (qty input next to + / - stepper on cart row) ---
        desired_qty = getattr(self, "_desired_quantity", 1)
        if desired_qty and desired_qty > 1:
            try:
                actual = await self._set_cart_quantity(item_id, desired_qty)
                if actual is not None and actual < desired_qty:
                    self._status_cb(
                        f"[PURCHASE] Cart qty clamped to {actual} (requested {desired_qty})"
                    )
                    logger.info(
                        "[PURCHASE] Walmart enforced lower qty: requested=%d actual=%d",
                        desired_qty, actual,
                    )
            except Exception as e:
                logger.warning(
                    "[PURCHASE] Qty bump failed (proceeding with qty=1): %s", e
                )
                self._status_cb("[PURCHASE] Qty bump failed — proceeding with qty=1")

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
        bookslot_attempts = 0
        bookslot_max_attempts = 3
        bookslot_last_try = 0.0
        bookslot_retry_cooldown = 2.5
        while time.monotonic() < deadline:
            url = self._page.url or ""
            if "/checkout" in url and "/cart" not in url:
                logger.info("[PURCHASE] URL reached checkout: %s", url)
                break
            if "/blocked" in url:
                self._status_cb("[PURCHASE] Blocked on checkout navigation — solving...")
                await self._handle_blocked()
                try:
                    await asyncio.wait_for(
                        self._page.get(WALMART_CHECKOUT_URL), timeout=15.0
                    )
                except asyncio.TimeoutError:
                    logger.warning("[PURCHASE] Post-challenge checkout re-navigation timed out")
                except Exception:
                    pass
            # Walmart now serves a mandatory delivery-slot widget at
            # /cart?step=bookslot for every delivery-eligible item (not just
            # grocery — observed on controller + notebook). The widget blocks
            # /checkout until a time slot is reserved. Retry up to 3x — the
            # drawer takes a moment to fully render after the cart redirect,
            # and a single early miss should not fail the whole attempt.
            if "step=bookslot" in url and bookslot_attempts < bookslot_max_attempts:
                now = time.monotonic()
                if now - bookslot_last_try >= bookslot_retry_cooldown:
                    self._status_cb(
                        f"[PURCHASE] Delivery slot widget detected — reserving slot (attempt {bookslot_attempts + 1})..."
                    )
                    logger.info(
                        "[PURCHASE] step=bookslot detected at: %s (attempt %d/%d)",
                        url, bookslot_attempts + 1, bookslot_max_attempts,
                    )
                    bookslot_last_try = now
                    bookslot_attempts += 1
                    # Try hybrid first — one POST replaces the entire drawer.
                    bookslot_ok = False
                    from walmart.checkout_api import (
                        WalmartHybridCheckout, is_enabled as hybrid_enabled,
                    )
                    if hybrid_enabled() and bookslot_attempts == 1:
                        try:
                            api = WalmartHybridCheckout(self._page)
                            ctx = await api.read_cart_context()
                            if ctx:
                                logger.info(
                                    "[PURCHASE] Hybrid: attempting slot reservation via API"
                                )
                                rs = await api.reserve_cheapest_slot(ctx)
                                if rs and rs.get("data", {}).get("reserveSlot", {}).get("checkoutable"):
                                    logger.info(
                                        "[PURCHASE] Hybrid: slot reserved via API — skipping DOM drawer"
                                    )
                                    bookslot_ok = True
                                    # Server-side reservation done — navigate
                                    # forward to /checkout. URL still says
                                    # step=bookslot until React picks up.
                                    try:
                                        await self._page.get(WALMART_CHECKOUT_URL)
                                    except Exception as nav_err:
                                        logger.debug(
                                            "[PURCHASE] post-slot nav raised: %s", nav_err,
                                        )
                        except Exception as e:
                            logger.warning(
                                "[PURCHASE] Hybrid slot reserve raised: %s — falling to DOM",
                                e,
                            )
                    if not bookslot_ok:
                        bookslot_ok = await self._handle_bookslot_modal()
                    if bookslot_ok:
                        # Slot reservation takes a few seconds + a server
                        # roundtrip to /checkout. Extend the deadline to give
                        # it room.
                        deadline = max(deadline, time.monotonic() + 12.0)
                    elif bookslot_attempts >= bookslot_max_attempts:
                        logger.warning(
                            "[PURCHASE] Failed to reserve delivery slot after %d attempts — falling through",
                            bookslot_max_attempts,
                        )
                        await self._screenshot("bookslot_failed")
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

    async def _set_cart_quantity(self, item_id: str, desired: int) -> Optional[int]:
        """Bump the cart row quantity to ``desired``.

        Walmart's ATC button always adds qty=1; multi-unit purchases require
        adjusting the qty UI on the cart row. The qty UI varies by product
        variant — some show a typeable ``<input>`` (with stepper buttons
        flanking it), others render stepper-only with no input.

        Strategy:
          1. Locate typeable input via known selectors. If found, set value
             via CDP keystrokes (focus → Cmd/Ctrl-A → type → Tab). DOM
             ``e.value = N`` is a synchronous mutation with no input events —
             PerimeterX's cart-mutation sensor flags it (PM8 D8 lesson).
          2. If input path yields no commit (no input element, or input
             ignored the keystrokes), fall back to clicking the increment
             stepper (desired - committed) times. Each click via
             ``_realistic_click`` to preserve antibot hygiene.

        Returns the qty Walmart actually committed (may be < desired if
        Walmart's server clamps), or None if neither path succeeded.
        """
        from zendriver.cdp import input_ as cdp_input
        import sys as _sys

        # Hybrid path: one GraphQL POST replaces the entire input + stepper
        # loop. Saves ~5-12s of clicks per qty bump. The API call still
        # runs inside the browser tab's JS context so _px3 + JA3 are real.
        from walmart.checkout_api import (
            WalmartHybridCheckout, is_enabled as hybrid_enabled,
        )
        if hybrid_enabled() and desired > 1:
            api_committed = await self._set_cart_qty_via_api(item_id, desired)
            if api_committed is not None:
                # API succeeded — done. (Walmart-enforced clamp respected.)
                return api_committed
            # API failed or returned None — fall through to DOM path
            logger.info("[PURCHASE] Hybrid qty API failed — falling back to DOM path")

        committed = await self._set_cart_qty_via_input(item_id, desired, cdp_input, _sys)
        if committed is not None and committed >= desired:
            return committed

        # Input path didn't reach desired — try stepper fallback for any gap.
        current = committed or 1
        if current < desired:
            logger.info(
                "[PURCHASE] Qty input %s → stepper fallback for remaining %d clicks",
                "no-commit" if committed is None else f"committed {committed}",
                desired - current,
            )
            stepper_result = await self._set_cart_qty_via_stepper(item_id, current, desired)
            if stepper_result is not None:
                committed = stepper_result
        return committed

    async def _set_cart_qty_via_api(
        self, item_id: str, desired: int,
    ) -> Optional[int]:
        """Hybrid qty bump via updateItems GraphQL mutation.

        Returns the server-committed qty on success, None on any failure
        (caller falls back to DOM input/stepper). Side-effect: leaves
        the cart in the same observable state as a successful DOM bump.
        """
        from walmart.checkout_api import WalmartHybridCheckout
        api = WalmartHybridCheckout(self._page)
        ctx = await api.read_cart_context()
        if ctx is None:
            logger.info("[PURCHASE] Hybrid: cart context unavailable — skipping API qty")
            return None
        # Sanity: confirm the cart line is the item we're trying to buy
        first = ctx["lineItems"][0]
        if str(first.get("usItemId")) != str(item_id):
            logger.info(
                "[PURCHASE] Hybrid: cart line usItemId=%s != requested %s — skipping API qty",
                first.get("usItemId"), item_id,
            )
            return None
        logger.info(
            "[PURCHASE] Hybrid: bumping qty %d → %d via updateItems (cartId=%s)",
            first.get("quantity", 1), desired, ctx["cartId"],
        )
        body = await api.bump_quantity(ctx, desired)
        if not body:
            return None
        parsed = WalmartHybridCheckout.parse_update_items(body)
        if not parsed:
            logger.warning("[PURCHASE] Hybrid: updateItems response unparseable")
            return None
        if parsed["line_count"] == 0:
            logger.warning(
                "[PURCHASE] Hybrid: updateItems returned 0 line items — cart emptied"
            )
            return None
        committed = parsed["committed_qty"]
        logger.info(
            "[PURCHASE] Hybrid: qty committed=%d (target %d, checkoutable=%s)",
            committed, desired, parsed["checkoutable"],
        )
        return committed

    async def _set_cart_qty_via_input(
        self, item_id: str, desired: int, cdp_input, _sys
    ) -> Optional[int]:
        """Original input-based qty bump. See _set_cart_quantity docstring."""
        find_js = """
            (() => {
                const candidates = [
                    'input[data-automation-id="item-qty"]',
                    'input[data-automation-id="qty"]',
                    'input[aria-label*="quantity" i]',
                    'input[name*="quantity" i]',
                    'input[type="number"][min]',
                    'input[type="tel"][maxlength="3"]',
                ];
                for (const sel of candidates) {
                    const el = document.querySelector(sel);
                    if (!el) continue;
                    el.scrollIntoView({ behavior: 'instant', block: 'center' });
                    const rect = el.getBoundingClientRect();
                    if (rect.width <= 0 || rect.height <= 0) continue;
                    return {
                        found: true,
                        x: rect.x, y: rect.y, w: rect.width, h: rect.height,
                        currentValue: String(el.value || ''),
                        max: el.getAttribute('max') || '',
                        via: sel,
                    };
                }
                return { found: false };
            })()
        """
        # Poll for the qty input — /cart is CSR and the row may not be in
        # the DOM at navigation completion. Wait up to ~4s for it to appear.
        find_result = None
        find_deadline = time.monotonic() + 4.0
        while time.monotonic() < find_deadline:
            find_result = await self._page.evaluate(find_js)
            if find_result and find_result.get('found'):
                break
            await asyncio.sleep(0.15)

        if not find_result or not find_result.get('found'):
            logger.info("[PURCHASE] No qty input on cart after 4s wait — falling back to stepper (item_id=%s)", item_id)
            return None

        max_attr = find_result.get('max') or ''
        ceiling = desired
        try:
            if max_attr:
                ceiling = min(desired, int(max_attr))
        except ValueError:
            pass
        target = max(1, ceiling)
        target_str = str(target)
        logger.info(
            "[PURCHASE] Cart qty bump: %s → %s (via %s, max attr=%r)",
            find_result.get('currentValue'), target_str, find_result.get('via'), max_attr,
        )

        # Realistic click into the input (focus via mouse, not .focus()).
        x = find_result['x'] + find_result['w'] / 2 + random.uniform(-3, 3)
        y = find_result['y'] + find_result['h'] / 2 + random.uniform(-2, 2)
        await self._realistic_click(x, y, "Cart qty input")
        await asyncio.sleep(random.uniform(0.10, 0.22))

        # Select-all then type new value. Mac Cmd-A (modifier=4) / Win-Linux Ctrl-A (modifier=2).
        sel_modifier = 4 if _sys.platform == "darwin" else 2
        await self._page.send(cdp_input.dispatch_key_event(
            type_="keyDown", key="a", code="KeyA", text="a", modifiers=sel_modifier,
        ))
        await asyncio.sleep(random.uniform(0.04, 0.08))
        await self._page.send(cdp_input.dispatch_key_event(
            type_="keyUp", key="a", code="KeyA", modifiers=sel_modifier,
        ))
        await asyncio.sleep(random.uniform(0.04, 0.09))

        # Type the new quantity, digit-by-digit (most carts are 1-2 digits).
        for ch in target_str:
            await self._page.send(cdp_input.dispatch_key_event(
                type_="keyDown", text=ch, key=ch, code=f"Digit{ch}",
                windows_virtual_key_code=ord(ch),
            ))
            await asyncio.sleep(random.uniform(0.05, 0.13))
            await self._page.send(cdp_input.dispatch_key_event(
                type_="keyUp", key=ch, code=f"Digit{ch}",
                windows_virtual_key_code=ord(ch),
            ))
            await asyncio.sleep(random.uniform(0.07, 0.14))

        # Tab to commit (real users either Tab or click away). Tab fires
        # blur + change which is what Walmart's React handler listens for.
        await self._page.send(cdp_input.dispatch_key_event(
            type_="keyDown", key="Tab", code="Tab", windows_virtual_key_code=9,
        ))
        await asyncio.sleep(random.uniform(0.05, 0.10))
        await self._page.send(cdp_input.dispatch_key_event(
            type_="keyUp", key="Tab", code="Tab", windows_virtual_key_code=9,
        ))

        # Walmart's React handler debounces + roundtrips to update the cart line.
        # Poll briefly for the committed value (server may clamp downward).
        committed: Optional[int] = None
        deadline = time.monotonic() + 3.5
        while time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            try:
                value_now = await self._page.evaluate(f"""
                    (() => {{
                        const el = document.querySelector('{find_result.get('via').replace("'", "")}');
                        return el ? String(el.value || '') : null;
                    }})()
                """)
                if value_now is not None and value_now.isdigit():
                    iv = int(value_now)
                    if iv > 0:
                        committed = iv
                        if iv == target:
                            break
            except Exception:
                pass

        if committed is None:
            logger.warning("[PURCHASE] Cart qty input never reflected an updated value")
        else:
            logger.info("[PURCHASE] Cart qty committed: %d (desired %d)", committed, target)
        return committed

    async def _set_cart_qty_via_stepper(
        self, item_id: str, current: int, desired: int
    ) -> Optional[int]:
        """Click the qty increment stepper button until cart qty reaches ``desired``.

        Used when the typeable input path fails or doesn't exist (some
        Walmart product variants render only +/- steppers).

        Correctness model: Walmart's cart row debounces qty updates and
        roundtrips to the server (~200-600ms each). Naively counting clicks
        leads to under-counting — a click that fires while the server is
        still processing the previous one gets swallowed (button briefly
        disables → click no-ops → button re-enables). So we VERIFY each
        increment by reading the qty input value (or ``__NEXT_DATA__``
        cart line) after each click and only count clicks that landed.
        Up to MAX_RETRY_PER_STEP retries per missing increment.
        """
        clicks_needed = desired - current
        if clicks_needed <= 0:
            return current

        MAX_RETRY_PER_STEP = 2
        # Cart row updates roundtrip through GraphQL updateItems — observed
        # ~2-3s end-to-end. Walmart also debounces rapid clicks into one
        # batched mutation, so the visible qty may only commit several clicks
        # later. 3.5s gives the server room without making the loop drag.
        VERIFY_TIMEOUT_S = 3.5
        VERIFY_POLL_MS = 120

        async def _read_cart_qty() -> Optional[int]:
            """Return the current cart-row qty as the cart sees it.

            Returns:
              positive int — committed qty
              0            — cart is AUTHORITATIVELY empty (DOM empty-cart
                             marker). Not derived from __NEXT_DATA__ which
                             is empty during CSR hydration on /cart.
              None         — couldn't read truth source (assume unchanged
                             and keep polling)
            """
            try:
                v = await self._page.evaluate("""
                    (() => {
                        // Authoritative empty signal: DOM has an empty-cart
                        // marker. Check this FIRST — if Walmart actually
                        // emptied our line, the cart shows the empty state
                        // before anything else does.
                        const emptyMarkers = [
                            '[data-automation-id="empty-cart"]',
                            '[data-testid*="empty-cart" i]',
                            '[data-automation-id*="empty-cart" i]',
                        ];
                        for (const sel of emptyMarkers) {
                            if (document.querySelector(sel)) return 0;
                        }
                        // Text-content empty signal — only trust if there's
                        // also NO cart-row container present.
                        const cartRowSelectors = [
                            '[data-automation-id="cart-item"]',
                            '[data-testid*="cart-item" i]',
                            '[data-automation-id*="cart-line-item" i]',
                            '[data-item-id]',
                        ];
                        let hasRow = false;
                        for (const sel of cartRowSelectors) {
                            if (document.querySelector(sel)) { hasRow = true; break; }
                        }
                        const bodyTxt = (document.body?.innerText || '').toLowerCase();
                        if (!hasRow && /your\\s+cart\\s+is\\s+empty/.test(bodyTxt)) return 0;

                        // Primary: qty input value (reflects committed state)
                        const inputSelectors = [
                            'input[data-automation-id="item-qty"]',
                            'input[data-automation-id="qty"]',
                            'input[aria-label*="quantity" i]',
                            'input[name*="quantity" i]',
                        ];
                        for (const sel of inputSelectors) {
                            const el = document.querySelector(sel);
                            if (el && el.value && /^\\d+$/.test(el.value)) {
                                return parseInt(el.value, 10);
                            }
                        }
                        // Secondary: stepper container shows qty as text
                        const stepper = document.querySelector(
                            '[data-automation-id*="quantity-stepper"], [data-automation-id*="qty-stepper"]'
                        );
                        if (stepper) {
                            const txt = (stepper.textContent || '').trim();
                            const m = txt.match(/(\\d+)/);
                            if (m) return parseInt(m[1], 10);
                        }
                        // Tertiary: aria-label on stepper container
                        const sLabel = document.querySelector(
                            '[aria-label*="quantity" i][role="group"], [aria-label*="quantity selector" i]'
                        );
                        if (sLabel) {
                            const lbl = sLabel.getAttribute('aria-label') || '';
                            const m = lbl.match(/(\\d+)/);
                            if (m) return parseInt(m[1], 10);
                        }
                        // Quaternary: cart line in __NEXT_DATA__. NOTE: on a
                        // fresh /cart load, __NEXT_DATA__.cartLines is often
                        // [] for several seconds while CSR hydration fills
                        // it — so empty-array does NOT mean "cart empty".
                        // Only TRUST a positive qty from here.
                        try {
                            const nd = window.__NEXT_DATA__;
                            const cart = nd?.props?.pageProps?.initialData?.data?.cart
                                      || nd?.props?.pageProps?.cart;
                            const lines = cart?.cartLines || cart?.lineItems || cart?.items || [];
                            if (Array.isArray(lines) && lines.length > 0) {
                                const q = lines[0].quantity ?? lines[0].qty;
                                if (typeof q === 'number' && q > 0) return q;
                            }
                        } catch(_) {}
                        return null;
                    })()
                """)
                if isinstance(v, (int, float)) and v >= 0:
                    return int(v)
            except Exception:
                pass
            return None

        async def _wait_for_cart_row(timeout_s: float = 4.0) -> Optional[int]:
            """Block until either a positive cart qty is readable or the
            DOM authoritatively says the cart is empty.

            Walmart's /cart page hydrates async — the row + qty stepper +
            input may not be present at navigation completion. Polling
            _read_cart_qty immediately can produce a false None (which
            stepper code treats as 'unknown, keep going') OR a false 0
            (which the prior implementation returned on empty NEXT_DATA).
            This helper waits up to 4s for the row to materialize before
            committing to an action.
            """
            deadline = time.monotonic() + timeout_s
            last_seen: Optional[int] = None
            while time.monotonic() < deadline:
                qty = await _read_cart_qty()
                if qty == 0:
                    return 0
                if qty is not None and qty > 0:
                    return qty
                last_seen = qty
                await asyncio.sleep(0.15)
            return last_seen

        async def _verify_increment(prev_qty: int) -> Optional[int]:
            """Poll until cart qty changes (up to ``desired``) or timeout.

            Returns:
              new qty (>= 1) — qty observably changed
              0              — cart emptied while we polled (caller must abort)
              None           — never saw a change within VERIFY_TIMEOUT_S
            """
            deadline = time.monotonic() + VERIFY_TIMEOUT_S
            while time.monotonic() < deadline:
                qty = await _read_cart_qty()
                if qty == 0:
                    return 0
                if qty is not None and qty > prev_qty:
                    return qty
                await asyncio.sleep(VERIFY_POLL_MS / 1000.0)
            return None

        # Read starting qty (truth, not the `current` arg we were given).
        # Wait up to 4s for the cart row to actually hydrate — Walmart's
        # /cart is CSR; immediately after navigation the qty input doesn't
        # exist yet and __NEXT_DATA__.cartLines is [] (which previously was
        # misread as "cart emptied"). _wait_for_cart_row returns 0 only on
        # an authoritative DOM empty-cart signal.
        observed = await _wait_for_cart_row(timeout_s=4.0)
        if observed == 0:
            logger.warning("[PURCHASE] Stepper: cart authoritatively empty at entry — aborting qty bump")
            return 0
        if observed is not None and observed > 0:
            committed = observed
            logger.debug("[PURCHASE] Stepper: cart starts at qty=%d (caller said %d)",
                         committed, current)
        else:
            # Row never materialized but no empty signal either. Trust the
            # caller's `current` (which came from the upstream cart-verified
            # path that saw "1 item(s)"). Continuing to click is the right
            # default — worst case we fail the click-find next.
            committed = current
            logger.info(
                "[PURCHASE] Stepper: cart row not yet hydrated — proceeding with caller qty=%d",
                current,
            )

        total_clicks_fired = 0
        while committed < desired:
            # Locate increment button each iteration — React may re-render
            # the cart row between clicks, invalidating cached element refs.
            find_btn = await self._page.evaluate("""
                (() => {
                    const candidates = [
                        'button[data-automation-id="increment-button"]',
                        'button[data-automation-id="qty-stepper-increment"]',
                        'button[data-automation-id="cart-item-qty-increment"]',
                        'button[data-automation-id*="plus" i]',
                        'button[data-testid*="increment" i]',
                        'button[data-testid*="plus" i]',
                        'button[aria-label*="Increase" i]',
                        'button[aria-label*="increment" i]',
                        'button[aria-label*="add one" i]',
                        'button[aria-label*="Add one more" i]',
                        'button[aria-label="+"]',
                    ];
                    for (const sel of candidates) {
                        const el = document.querySelector(sel);
                        if (!el) continue;
                        el.scrollIntoView({ behavior: 'instant', block: 'center' });
                        const rect = el.getBoundingClientRect();
                        if (rect.width <= 0 || rect.height <= 0) continue;
                        return {
                            found: true,
                            x: rect.x, y: rect.y, w: rect.width, h: rect.height,
                            via: sel,
                            disabled: !!el.disabled,
                        };
                    }
                    // Fallback: look for any small square button whose text is "+"
                    // or whose aria-label CLEARLY indicates increment. Exclude
                    // Remove/Save/Delete/Heart/etc. — those are sometimes ≤60px
                    // square and would otherwise match `aria.includes('add')`
                    // (e.g. "add to list").
                    const buttons = Array.from(document.querySelectorAll('button:not([disabled])'));
                    const incExcludeRe = /(remove|delete|save\\s*for\\s*later|favorite|heart|wishlist|add\\s*to\\s*list|share|edit|close)/i;
                    for (const b of buttons) {
                        const r = b.getBoundingClientRect();
                        if (r.width <= 0 || r.height <= 0) continue;
                        if (r.width > 60 || r.height > 60) continue;
                        const txt = (b.textContent || '').trim();
                        const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                        if (incExcludeRe.test(txt) || incExcludeRe.test(aria)) continue;
                        // Allow only true "+" / "increase" / "increment" signals
                        const plusOk = (txt === '+' ||
                                        aria.includes('increase') ||
                                        aria.includes('increment') ||
                                        aria === '+' ||
                                        aria.includes('add one') ||
                                        aria.includes('add 1'));
                        if (!plusOk) continue;
                        return {
                            found: true, x: r.x, y: r.y, w: r.width, h: r.height,
                            via: 'fallback-plus', disabled: false,
                            aria: aria.slice(0, 60), text: txt.slice(0, 20),
                        };
                    }
                    // Diagnostic
                    const allBtns = document.querySelectorAll('button');
                    return {
                        found: false,
                        button_count: allBtns.length,
                        sample_arias: Array.from(allBtns).slice(0, 8).map(
                            b => (b.getAttribute('aria-label') || b.textContent || '?').slice(0, 50)
                        ),
                    };
                })()
            """)

            if not find_btn or not find_btn.get('found'):
                logger.warning(
                    "[PURCHASE] Qty stepper button vanished at qty=%d (target %d, button_count=%s, samples=%s)",
                    committed, desired,
                    (find_btn or {}).get('button_count'),
                    (find_btn or {}).get('sample_arias'),
                )
                break

            if find_btn.get('disabled'):
                logger.info(
                    "[PURCHASE] Qty stepper disabled at qty=%d — server-enforced cap (target %d)",
                    committed, desired,
                )
                break

            # Click + verify. Retry the click if the cart qty doesn't move
            # within VERIFY_TIMEOUT_S — Walmart's debounce may have swallowed
            # one of our clicks.
            x = find_btn['x'] + find_btn['w'] / 2 + random.uniform(-3, 3)
            y = find_btn['y'] + find_btn['h'] / 2 + random.uniform(-2, 2)

            prev_qty = committed
            new_qty: Optional[int] = None
            cart_emptied = False
            for retry in range(MAX_RETRY_PER_STEP):
                await self._realistic_click(
                    x, y,
                    f"Qty stepper {committed}→{committed + 1}" +
                    (f" (retry {retry})" if retry else ""),
                )
                total_clicks_fired += 1
                # Inter-click pause. Walmart debounces rapid clicks into a
                # single GraphQL mutation, so a moderate pause helps each
                # click register as a discrete intent.
                await asyncio.sleep(random.uniform(0.22, 0.42))
                new_qty = await _verify_increment(prev_qty)
                if new_qty == 0:
                    cart_emptied = True
                    break
                if new_qty is not None:
                    break
                logger.debug(
                    "[PURCHASE] Stepper click from qty=%d not reflected — retrying (%d/%d)",
                    prev_qty, retry + 1, MAX_RETRY_PER_STEP,
                )

            if cart_emptied:
                logger.warning(
                    "[PURCHASE] Stepper: cart emptied at qty=%d during click (server rejected line) — aborting",
                    committed,
                )
                committed = 0
                break

            if new_qty is None:
                logger.warning(
                    "[PURCHASE] Stepper stuck at qty=%d after %d retries (via=%s) — giving up. "
                    "Possible cause: variant has fixed Multipack Quantity, or "
                    "we clicked a Remove/Save button by mistake.",
                    committed, MAX_RETRY_PER_STEP, find_btn.get('via'),
                )
                break

            committed = new_qty
            if committed >= desired:
                break

        logger.info(
            "[PURCHASE] Cart qty via stepper: %d → %d (target %d, %d clicks fired)",
            current, committed, desired, total_clicks_fired,
        )
        return committed

    async def _select_delivery_on_cart(self):
        """Select Delivery fulfillment on the /cart page before clicking checkout.

        Walmart's cart page shows Pickup/Delivery tiles. Pickup is often the default.
        We must click "Delivery" here — the checkout page inherits this choice.
        """
        try:
            result = await self._page.evaluate("""
                (() => {
                    // Look for Delivery tile/button on cart page.
                    // The cart page uses fulfillment tiles rendered as buttons or
                    // ARIA radio/option controls — labels and generic divs/anchors
                    // are not actual fulfillment selectors. Narrowing the pool
                    // saves CDP work on ~100-300 irrelevant elements per cart.
                    const candidates = document.querySelectorAll(
                        'button, [role="tab"], [role="radio"], [role="option"]'
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
                    // Tighten the wildcards — `*="delivery"` previously matched
                    // delivery-tip and delivery-address containers as well as
                    // the actual fulfillment tile.
                    const autoSelectors = [
                        '[data-automation-id="fulfillment-option-DELIVERY"]',
                        '[data-automation-id="fulfillment-option-SHIPPING"]',
                        '[data-testid="fulfillment-option-DELIVERY"]',
                        '[data-testid="fulfillment-option-SHIPPING"]',
                        '[data-automation-id="cart-fulfillment-delivery"]',
                        '[data-automation-id="cart-fulfillment-shipping"]',
                    ];
                    for (const sel of autoSelectors) {
                        const els = document.querySelectorAll(sel);
                        for (const el of els) {
                            const text = (el.textContent || '').toLowerCase();
                            if (text.includes('pickup') || text.includes('pick up')) continue;
                            // Skip if this is a driver-tip / address container
                            if (text.includes('driver tip') || text.includes('tip ') ||
                                text.includes('address') || text.includes('change address')) continue;
                            const isSelected = el.getAttribute('aria-selected') === 'true' ||
                                               el.getAttribute('aria-pressed') === 'true' ||
                                               el.getAttribute('aria-checked') === 'true' ||
                                               el.classList.contains('selected');
                            if (isSelected) return { action: 'already_selected', via: sel };
                            const rect = el.getBoundingClientRect();
                            if (rect.width <= 0 || rect.height <= 0) continue;
                            return { action: 'clicked', via: sel,
                                     x: rect.left + rect.width/2, y: rect.top + rect.height/2 };
                        }
                    }

                    // Strategy 2: STRICT text match — only short tiles whose
                    // text is "Delivery" / "Ship" / "Shipping" exactly (or with
                    // a trailing "from store" / "to address" suffix). The old
                    // substring check matched "Driver tip (charged separately
                    // from delivery)" because that text contains 'delivery'.
                    const candidates = document.querySelectorAll(
                        'button, label, [role="radio"], [role="tab"], [role="option"]'
                    );
                    const exactRe = /^\\s*(delivery|ship|shipping|delivery from store|delivery to address|ship it)\\s*$/i;
                    const excludeRe = /(driver\\s*tip|change\\s*(address|location)|address|saved|leave at door)/i;
                    for (const el of candidates) {
                        const text = (el.textContent || '').trim();
                        if (!text || text.length > 40) continue;  // tile labels are short
                        if (excludeRe.test(text)) continue;
                        if (!exactRe.test(text)) continue;
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden') continue;
                        const rect = el.getBoundingClientRect();
                        if (rect.width <= 0 || rect.height <= 0) continue;
                        const isSelected = el.getAttribute('aria-selected') === 'true' ||
                                           el.getAttribute('aria-pressed') === 'true' ||
                                           el.getAttribute('aria-checked') === 'true';
                        if (isSelected) return { action: 'already_selected', via: 'text:' + text.slice(0, 30) };
                        return { action: 'clicked', via: 'text:' + text.slice(0, 30),
                                 x: rect.left + rect.width/2, y: rect.top + rect.height/2 };
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
                    # A modal may pop after selecting delivery — poll briefly instead
                    # of unconditionally sleeping. If [role="dialog"] hasn't rendered
                    # within ~350ms, it isn't going to appear here (will be caught by
                    # the step-loop handler if it shows up later).
                    modal_poll_deadline = time.monotonic() + 0.35
                    modal_appeared = False
                    while time.monotonic() < modal_poll_deadline:
                        try:
                            modal_appeared = await self._page.evaluate(
                                "!!document.querySelector('[role=\"dialog\"]')"
                            )
                        except Exception:
                            break
                        if modal_appeared:
                            break
                        await asyncio.sleep(0.05)
                    if modal_appeared:
                        await self._handle_delivery_day_modal("delivery_select")
                elif action == 'already_selected':
                    logger.info("[PURCHASE] Delivery already selected via: %s", via)
                else:
                    logger.info("[PURCHASE] No fulfillment selector found — assuming delivery is default or step not shown")
                    self._status_cb("[PURCHASE] No fulfillment choice shown — continuing")
        except Exception as e:
            logger.warning("[PURCHASE] _select_delivery_option raised: %s", e)
            self._status_cb("[PURCHASE] Delivery selection error — continuing")

    async def _decline_driver_tip(self):
        """
        Click the "No tip" / "$0" / "None" option in the driver-tip section.

        Walmart's checkout pre-selects a default tip ($2-$5) on delivery
        orders. If we don't explicitly opt out, the tip is charged. The
        tip widget renders as a row of radio buttons / pill buttons with
        amounts ($2, $3, $5, Other) plus a "No tip" / "$0" / "None" option.
        """
        try:
            result = await self._page.evaluate("""
                (() => {
                    // The tip widget is inside a container labeled "Driver tip"
                    // or has a data-automation-id mentioning "tip". Find that
                    // container first to scope the No-tip search.
                    const containers = [];
                    const labels = document.querySelectorAll(
                        'h1, h2, h3, h4, h5, h6, [role="heading"], legend, label, span, div'
                    );
                    for (const l of labels) {
                        const t = (l.textContent || '').trim().toLowerCase();
                        if (t === 'driver tip' || t.startsWith('driver tip') ||
                            t === 'add a tip' || t.startsWith('tip your driver') ||
                            t.startsWith('want to add a tip')) {
                            // Walk up to find a section container that holds
                            // the radio group below the label.
                            let node = l;
                            for (let depth = 0; node && depth < 6; depth++) {
                                if (node.querySelectorAll &&
                                    node.querySelectorAll('button, [role="radio"], input[type="radio"]').length > 2) {
                                    containers.push(node);
                                    break;
                                }
                                node = node.parentElement;
                            }
                        }
                    }
                    // Fallback: data-automation-id container
                    document.querySelectorAll(
                        '[data-automation-id*="tip" i], [data-testid*="tip" i]'
                    ).forEach(c => containers.push(c));

                    if (containers.length === 0) {
                        return { action: 'no_tip_section' };
                    }

                    const noTipRe = /^\\s*(no\\s*tip|none|\\$\\s*0(\\.0{1,2})?|0|no\\s*thanks)\\s*$/i;
                    for (const ctx of containers) {
                        const candidates = ctx.querySelectorAll(
                            'button, [role="radio"], input[type="radio"], label'
                        );
                        for (const el of candidates) {
                            // For inputs, read aria-label or sibling label
                            let text = (el.textContent || '').trim();
                            if (!text && el.tagName === 'INPUT') {
                                text = (el.getAttribute('aria-label') ||
                                        el.getAttribute('value') ||
                                        '').trim();
                            }
                            if (!text) continue;
                            if (!noTipRe.test(text)) continue;
                            const isSelected = el.getAttribute('aria-pressed') === 'true' ||
                                               el.getAttribute('aria-checked') === 'true' ||
                                               (el.tagName === 'INPUT' && el.checked);
                            if (isSelected) return { action: 'already_no_tip', text: text };
                            const r = el.getBoundingClientRect();
                            if (r.width <= 0 || r.height <= 0) continue;
                            el.scrollIntoView({ behavior: 'instant', block: 'center' });
                            const r2 = el.getBoundingClientRect();
                            return {
                                action: 'clicked',
                                x: r2.left + r2.width / 2,
                                y: r2.top + r2.height / 2,
                                text: text,
                            };
                        }
                    }
                    return { action: 'no_tip_button_in_section' };
                })()
            """)

            if not result:
                return
            action = result.get('action')
            if action == 'clicked':
                await self._realistic_click(
                    result['x'], result['y'], "No tip"
                )
                await asyncio.sleep(random.uniform(0.3, 0.7))
                logger.info("[PURCHASE] Driver tip declined — clicked %r", result.get('text'))
                self._status_cb("[PURCHASE] Driver tip declined ($0)")
            elif action == 'already_no_tip':
                logger.info("[PURCHASE] Driver tip already at $0 (%r)", result.get('text'))
            elif action == 'no_tip_section':
                logger.debug("[PURCHASE] No driver-tip section visible — skipping")
            elif action == 'no_tip_button_in_section':
                logger.info("[PURCHASE] Tip section visible but no $0/No-tip button found — leaving default")
        except Exception as e:
            logger.warning("[PURCHASE] _decline_driver_tip raised: %s — continuing", e)

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

        # Decline driver tip ($0 / No tip). Walmart's checkout pre-selects
        # a default tip ($2-$5) on delivery orders. Skip without an explicit
        # opt-out and we get charged. Click the "No tip" / "$0" / "None"
        # radio if present.
        await self._decline_driver_tip()

        # RIGHT AFTER delivery selection, a modal may appear asking for delivery day
        # Check and dismiss it BEFORE entering the Continue button loop
        logger.info("[PURCHASE] Checking for delivery day modal after fulfillment selection...")
        await asyncio.sleep(random.uniform(0.5, 1.5))  # Wait for modal to appear if it will
        modal_dismissed = await self._handle_delivery_day_modal("post_delivery_select")
        if modal_dismissed:
            logger.info("[PURCHASE] Modal appeared and was dismissed after delivery selection")
            await asyncio.sleep(random.uniform(1.0, 2.0))  # Extra pause after modal dismissal

        # Fast-path: 2-step checkout (pre-saved address + payment) may show Place Order immediately
        place_order_early = await self._find_element(PLACE_ORDER_SELECTORS, timeout=1000)
        if place_order_early:
            self._status_cb("[PURCHASE] Fast-path: Place Order already visible — skipping step loop")
            return

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
        # Skip the modal-handler CDP roundtrip after this many consecutive
        # iterations where no dialog was seen. The pre-saved-address flow
        # never triggers a modal mid-checkout, so we pay no recurring cost.
        no_modal_streak = 0
        NO_MODAL_SKIP_THRESHOLD = 2

        for step_num in range(MAX_STEPS):
            # Top-of-iteration pacing. The previous iteration's post-click sleep
            # (1.5-2.5s default, 0.3-0.5s in FAST_DROP_MODE) already gave the
            # page time to render — a second sleep here is partly redundant.
            # FAST_DROP_MODE skips this sleep entirely on iter 2+, saving
            # ~300-700ms per iteration on drops. Iter 0 still pauses to look
            # natural after the cart→checkout transition.
            if step_num == 0:
                await asyncio.sleep(random.uniform(0.3, 0.7))
            elif not FAST_DROP_MODE:
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

            # Guard: Dismiss any delivery day modals that appeared mid-checkout.
            # Cap by MAX_MODAL_HANDLES so a stuck modal can't loop forever, AND
            # short-circuit after NO_MODAL_SKIP_THRESHOLD consecutive iterations
            # with no modal present — the typical pre-saved-address flow never
            # raises a modal mid-checkout, so further CDP checks are wasted.
            if modal_handle_count < MAX_MODAL_HANDLES and no_modal_streak < NO_MODAL_SKIP_THRESHOLD:
                modal_dismissed = await self._handle_delivery_day_modal(f"{step_num}")
                if modal_dismissed:
                    modal_handle_count += 1
                    no_modal_streak = 0  # reset — a modal may reappear
                    logger.info("[PURCHASE] Delivery day modal handled (%d/%d)", modal_handle_count, MAX_MODAL_HANDLES)
                    # Modal handler now includes 2-4s pause + DOM stability wait.
                    # Do NOT add additional pause here — the handler covers it.
                    # Continue to look for Continue button since modal is gone
                else:
                    no_modal_streak += 1

            # If Place Order button is now visible, we're on the review step — done
            place_order_visible = await self._find_element(PLACE_ORDER_SELECTORS, timeout=2000)
            if place_order_visible:
                self._status_cb(f"[PURCHASE] Reached review step after {step_num} Continue click(s)")
                return

            # Human think time: pause before looking for Continue button
            await asyncio.sleep(random.uniform(0.3, 0.5) if FAST_DROP_MODE else random.uniform(1.0, 2.0))

            # Click the next Continue/advance button via CDP mouse trajectory.
            # Continue fires 2-4× across address/payment/review — most-executed click
            # in checkout, so raw element.click() here would be a strong bot signal.
            continue_btn = await self._find_element(CONTINUE_SELECTORS, timeout=4000)
            if continue_btn:
                try:
                    rect = await continue_btn.apply("""(e) => {
                        e.scrollIntoView({ behavior: 'instant', block: 'center' });
                        const r = e.getBoundingClientRect();
                        return { x: r.x, y: r.y, w: r.width, h: r.height };
                    }""")
                    if rect and rect.get('w', 0) > 0:
                        x = rect['x'] + rect['w'] / 2 + random.uniform(-3, 3)
                        y = rect['y'] + rect['h'] / 2 + random.uniform(-2, 2)
                        clicked = await self._realistic_click(x, y, f"Continue (step {step_num + 1})")
                        if not clicked:
                            await continue_btn.click()
                    else:
                        await continue_btn.click()
                except Exception as e:
                    logger.debug("[PURCHASE] Continue rect lookup failed: %s — falling back to element.click()", e)
                    await continue_btn.click()
                # Post-click pause: let page transition settle before next step
                await asyncio.sleep(random.uniform(0.3, 0.5) if FAST_DROP_MODE else random.uniform(1.5, 2.5))
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

        # PIE.js CVV submission shortcut.
        # When WALMART_CHECKOUT_API=1 AND WALMART_PIE_CVV=1, we encrypt the
        # CVV with Walmart's PIE public key (fetched from
        # securedataweb.walmart.com/pie/v1/wmcom_us_vtg_pie/getkey.js) and
        # POST the ciphertext directly to /api/checkout-customer/encrypted-pan.
        # This bypasses the DOM keystroke dance entirely — the most-
        # scrutinized form on Walmart's checkout for PerimeterX biometrics.
        #
        # Falls through to DOM entry on any failure (preserves regression).
        pie_enabled = (
            os.environ.get("WALMART_CHECKOUT_API", "").strip().lower() in ("1", "true", "yes", "on")
            and os.environ.get("WALMART_PIE_CVV", "").strip().lower() in ("1", "true", "yes", "on")
        )
        if pie_enabled:
            try:
                from walmart.checkout_api import WalmartHybridCheckout
                api = WalmartHybridCheckout(self._page)
                pie_result = await api.submit_cvv_via_pie(card_cvv)
                if pie_result and pie_result.get("status") == "ok":
                    self._status_cb("[PURCHASE] CVV submitted via PIE (no DOM keystrokes)")
                    logger.info(
                        "[PURCHASE] PIE CVV submit OK — skipping DOM entry. "
                        "token=%s", (pie_result.get("cardToken") or "?")[:24],
                    )
                    return
                logger.info(
                    "[PURCHASE] PIE submit returned no/non-ok result — "
                    "falling back to DOM CVV entry"
                )
            except Exception as e:
                logger.warning(
                    "[PURCHASE] PIE submit raised, falling back to DOM CVV: %s: %s",
                    type(e).__name__, e,
                )
            # Fall through to DOM keystroke path

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
                # Clear existing value via CDP keystrokes (focus + Ctrl/Cmd-A +
                # Delete). Setting `e.value = ''` is a synchronous DOM mutation
                # with no `input`/`beforeinput` events, which PerimeterX's payment
                # field sensor flags as automation. Real keyboard clears always
                # emit the full event chain.
                from zendriver.cdp import input_ as cdp_input
                try:
                    await cvv_input.apply("(e) => { e.focus(); }")
                    await asyncio.sleep(random.uniform(0.04, 0.10))
                    # Select-all via OS-native modifier. CDP key-event modifier bits:
                    # 1=Alt, 2=Ctrl, 4=Meta, 8=Shift. Mac uses Cmd-A (Meta),
                    # Windows/Linux uses Ctrl-A.
                    import sys as _sys
                    sel_modifier = 4 if _sys.platform == "darwin" else 2
                    await self._page.send(cdp_input.dispatch_key_event(
                        type_="keyDown", key="a", code="KeyA", text="a",
                        modifiers=sel_modifier,
                    ))
                    await asyncio.sleep(random.uniform(0.04, 0.08))
                    await self._page.send(cdp_input.dispatch_key_event(
                        type_="keyUp", key="a", code="KeyA",
                        modifiers=sel_modifier,
                    ))
                    await asyncio.sleep(random.uniform(0.04, 0.09))
                    await self._page.send(cdp_input.dispatch_key_event(
                        type_="keyDown", key="Delete", code="Delete",
                        windows_virtual_key_code=46,
                    ))
                    await asyncio.sleep(random.uniform(0.04, 0.08))
                    await self._page.send(cdp_input.dispatch_key_event(
                        type_="keyUp", key="Delete", code="Delete",
                        windows_virtual_key_code=46,
                    ))
                except Exception as clear_err:
                    # Belt-and-suspenders: if CDP clear fails, fall back to the
                    # old DOM-property clear. Worth the detection risk vs. typing
                    # over an existing value.
                    logger.debug("[PURCHASE] CDP CVV clear failed (%s) — DOM fallback", clear_err)
                    await cvv_input.apply("(e) => { e.value = ''; e.focus(); }")
                await asyncio.sleep(random.uniform(0.05, 0.15))
                # Type each digit with human-like inter-key delays
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
        # Hybrid path: one CreateContract POST replaces the click + URL-wait
        # + order-ID extraction. Saves ~5-10s and gives us the order ID
        # straight from the response body (no DOM scraping).
        from walmart.checkout_api import (
            WalmartHybridCheckout, is_enabled as hybrid_enabled,
        )
        if hybrid_enabled():
            api_result = await self._place_order_via_api(item_id)
            if api_result is not None:
                return api_result
            logger.info("[PURCHASE] Hybrid place-order API failed — falling back to DOM click")

        self._status_cb("[PURCHASE] Clicking Place Order...")
        btn = await self._find_element(PLACE_ORDER_SELECTORS, timeout=10000)
        if not btn:
            await self._screenshot(f"no_place_order_{item_id}")
            logger.warning("[PURCHASE] Place Order button not found")
            return None, None

        await self._screenshot(f"before_place_order_{item_id}")
        # Click via CDP mouse trajectory for scrutinized checkout button
        try:
            rect = await btn.apply("""(e) => {
                e.scrollIntoView({ behavior: 'instant', block: 'center' });
                const r = e.getBoundingClientRect();
                return { x: r.left, y: r.top, w: r.width, h: r.height };
            }""")
            if rect:
                x = rect['x'] + rect['w'] / 2 + random.uniform(-3, 3)
                y = rect['y'] + rect['h'] / 2 + random.uniform(-2, 2)
                await self._realistic_click(x, y, "Place Order")
            else:
                await btn.click()
        except Exception as e:
            logger.warning("[PURCHASE] Failed to get button bounds: %s — falling back to JS click", e)
            await btn.click()
        self._status_cb("[PURCHASE] Place Order clicked — waiting for confirmation...")

        # Wait for order confirmation page — polling loop (zendriver has no wait_for_url)
        await asyncio.sleep(random.uniform(0.03, 0.10))  # CDP flush yield
        pre_click_url = self._page.url

        # Regex matches Walmart's known confirmation URL patterns.
        # Live URL as of 2026: walmart.com/checkout/thankyou?version=v3/
        # NOTE: "thankyou" has no hyphen — "thank-you" (hyphenated) never matched live orders.
        _confirm_pattern = re.compile(r".*(order-confirmation|order/confirm|thank-you|thankyou|checkout/thankyou|order-placed).*")
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

    async def _place_order_via_api(
        self, item_id: str,
    ) -> Optional[tuple[Optional[str], Optional[str]]]:
        """Place order via CreateContract GraphQL POST.

        Returns ``(order_id, confirmation_url)`` on success, or None to
        signal the caller to fall back to the DOM click path.

        Success criteria: HTTP 200 + `data.createPurchaseContract.id`
        present (the `pcid` matching /thankyou?pcid=...). We don't
        navigate to /thankyou — the contract is created server-side.
        """
        from walmart.checkout_api import WalmartHybridCheckout
        api = WalmartHybridCheckout(self._page)
        ctx = await api.read_cart_context()
        if ctx is None:
            logger.info("[PURCHASE] Hybrid place-order: cart context unavailable")
            return None
        self._status_cb("[PURCHASE] Hybrid: placing order via CreateContract...")
        logger.info(
            "[PURCHASE] Hybrid place-order: cartId=%s, %d line(s)",
            ctx["cartId"], len(ctx["lineItems"]),
        )
        t0 = time.monotonic()
        body = await api.place_order(ctx)
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        if not body:
            logger.warning(
                "[PURCHASE] Hybrid place-order: CreateContract returned None after %.0fms",
                elapsed_ms,
            )
            return None
        parsed = WalmartHybridCheckout.parse_create_contract(body)
        if not parsed or not parsed.get("pcid"):
            logger.warning(
                "[PURCHASE] Hybrid place-order: response missing pcid — payload may be stale"
            )
            return None
        pcid = parsed["pcid"]
        amount = parsed.get("amount_paid")
        last4 = parsed.get("payment_last4")
        status = parsed.get("order_status")
        confirmation_url = f"https://www.walmart.com/thankyou?pcid={pcid}"
        self._status_cb(
            f"[PURCHASE] Hybrid ORDER PLACED — pcid={pcid} ${amount} card *{last4}"
        )
        logger.warning(
            "[PURCHASE] Hybrid SUCCESS — pcid=%s status=%s amount=$%s card=*%s ms=%.0f",
            pcid, status, amount, last4, elapsed_ms,
        )
        return pcid, confirmation_url

    async def _extract_order_id(self) -> Optional[str]:
        # Primary: parse pcid from URL — Walmart's confirmation URL is
        # /thankyou?pcid=<uuid> (the same id CreateContract returns).
        # This is the authoritative order ID; numeric "order numbers"
        # that some pages render are display-only.
        url = self._page.url or ""
        pcid_match = re.search(r"[?&]pcid=([0-9a-f-]{20,})", url, re.IGNORECASE)
        if pcid_match:
            return pcid_match.group(1)

        # Secondary: DOM selectors (e.g. "Order # 1234567890123")
        for selector in ORDER_CONFIRM_SELECTORS:
            try:
                el = await self._page.query_selector(selector)
                if el:
                    text = await el.apply("(e) => e.innerText")
                    if text:
                        numbers = re.findall(r'\d{6,}', text)
                        if numbers:
                            return numbers[0]
                        return text.strip()
            except Exception:
                continue

        # Tertiary: any 7+ digit number in the URL (legacy numeric order IDs)
        if "order-confirmation" in url or "thankyou" in url or "order" in url:
            numbers = re.findall(r'\d{7,}', url)
            if numbers:
                return numbers[0]
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
            try:
                await asyncio.wait_for(self._page.get(WALMART_CART_URL), timeout=15.0)
            except asyncio.TimeoutError:
                logger.warning("[PURCHASE] _is_item_already_in_cart: cart navigation timed out after 15s")
                return False
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
                    await asyncio.wait_for(self._page.get(item_url), timeout=15.0)
                    await asyncio.sleep(random.uniform(0.8, 1.4))
                except asyncio.TimeoutError:
                    logger.warning("[PURCHASE] Navigate-back to %s timed out after 15s", item_url)
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
            try:
                await asyncio.wait_for(self._page.get(WALMART_CART_URL), timeout=15.0)
            except asyncio.TimeoutError:
                logger.warning("[PURCHASE] _clear_cart_if_needed: cart navigation timed out after 15s — skipping pre-clear")
                return
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
                    await self._click_handle_via_cdp(btn, "cart Remove (pre-ATC)")
                    await asyncio.sleep(random.uniform(0.65, 1.05))
                except Exception:
                    pass
        except Exception as e:
            logger.warning("[PURCHASE] _clear_cart_if_needed failed: %s", e)

    async def _handle_bookslot_modal(self) -> bool:
        """
        Reserve a delivery slot at /cart?step=bookslot.

        Walmart added a mandatory delivery-slot widget between cart and
        /checkout for all delivery-eligible items (observed 2026-05-11). The
        widget is a side drawer (slide-in from the right), NOT a centered
        dialog. The drawer has:
          - A Pickup/Delivery toggle at the top
          - A day picker row (Today / Tue 5/12 / Wed 5/13 / ...)
          - Slot rows below, each a label with a radio bullet + text like
            "Express 30 min or less | $9.95 + $10.00 Express" + price
          - A "Continue" button at the bottom
        The header reads "Reserve a time".

        Empirically (capture 2026-05-11): the drawer is NOT a [role="dialog"]
        and the radios sit in the right half of the viewport. We locate by:
          1. Find all visible radios/labels in the right half of the viewport
             whose text matches a time-slot pattern (price + duration).
          2. Pick the cheapest viable one (avoid Walmart+ trial / decline).
          3. Click it via CDP trajectory.
          4. Find the Continue button in the same right half.
          5. Click via CDP trajectory.

        Returns True on success; caller's URL-wait loop will see /checkout next.
        """
        # Scroll the drawer body to surface all slot tiles. Cheap 2-hour
        # windows render below the fold (Express / 3-hour at top); without
        # scrolling, the candidate list only contains the expensive options.
        # Scrolling the WHOLE page works because the drawer is a normal
        # scrollable container — its overflow scrolls with window.scroll.
        try:
            await self._page.evaluate("""
                (() => {
                    // Find the drawer scroll container (right column) and
                    // scroll its overflow. Fallback: scroll the page body.
                    const vw = window.innerWidth;
                    const minX = vw * 0.55;
                    const scrollers = Array.from(
                        document.querySelectorAll('div, section, aside')
                    ).filter(el => {
                        const r = el.getBoundingClientRect();
                        if (r.right < minX) return false;
                        if (r.height < 200) return false;
                        const cs = getComputedStyle(el);
                        return (cs.overflowY === 'auto' || cs.overflowY === 'scroll') &&
                               el.scrollHeight > el.clientHeight + 50;
                    });
                    if (scrollers.length > 0) {
                        // Pick the largest scroller (the drawer body)
                        scrollers.sort((a, b) => b.scrollHeight - a.scrollHeight);
                        scrollers[0].scrollTo({ top: 9999, behavior: 'instant' });
                    } else {
                        window.scrollTo({ top: 9999, behavior: 'instant' });
                    }
                })()
            """)
            # Brief pause so any virtualized slot rows render
            await asyncio.sleep(random.uniform(0.4, 0.7))
        except Exception as e:
            logger.debug("[PURCHASE] bookslot: drawer scroll raised: %s — continuing", e)

        slot_result = await self._page.evaluate("""
            (() => {
                const vw = window.innerWidth;
                const vh = window.innerHeight;
                // The Reserve-a-time drawer takes the right ~33% of the
                // viewport on desktop. Anything entirely left of vw*0.55 is
                // page content, not the drawer.
                const minX = vw * 0.55;

                // Visibility: in-DOM, non-zero size, on the right side. We
                // INTENTIONALLY accept off-screen-below elements — Walmart
                // may render slot tiles below the fold; we'll scrollIntoView
                // before clicking.
                const isVisible = (el) => {
                    const r = el.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) return false;
                    if (r.bottom < 0) return false;          // entirely above
                    if (r.right < minX) return false;        // left of drawer
                    const cs = getComputedStyle(el);
                    return cs.display !== 'none' && cs.visibility !== 'hidden';
                };

                // A real slot tile MUST have a time-window descriptor — not
                // just a price. Demand either a duration ("30 min", "3 hr",
                // "today", etc.) or a clock range ("7pm-9pm").
                const slotTextRe = /(today|tomorrow|tonight|\\d+\\s*(?:min|mins|hr|hrs|hour|hours)\\b|\\d{1,2}\\s*(?:am|pm)\\s*[-–to]+\\s*\\d{1,2}\\s*(?:am|pm)|deliver\\s*by\\s*\\d|express\\s*\\d)/i;
                // Exclude: pickup, navigation chrome, the "Delivery from
                // store" tab button (it's a category toggle, NOT a slot),
                // promo banners, Walmart+ upsells, and any plain-number rows.
                const excludeRe  = /(pickup\\s*(at|from)|no thanks|decline|not now|skip|change\\s*address|view\\s*all|claim offer|walmart\\+|wplus|subtotal|estimated|taxes|order\\s*summary|order\\s*total|continue|reserve|confirm|delivery\\s*from\\s*store|^\\s*\\$?\\d+(\\.\\d+)?\\s*$)/i;

                // Build the candidate list: anything that looks clickable
                // and is visible in the drawer column. Prefer:
                //   1) input[type="radio"]:not(:disabled)
                //   2) [role="radio"]
                //   3) label containing a radio
                //   4) clickable cards: button | div[role="button"] | a — only
                //      when their text matches the slot pattern
                const all = [
                    ...document.querySelectorAll('input[type="radio"]:not(:disabled)'),
                    ...document.querySelectorAll('[role="radio"]:not([aria-disabled="true"])'),
                    ...document.querySelectorAll('label'),
                    ...document.querySelectorAll('button:not([disabled]), div[role="button"]:not([aria-disabled="true"])'),
                ];

                const slots = [];
                const seen = new Set();
                for (const el of all) {
                    if (seen.has(el)) continue;
                    if (!isVisible(el)) continue;
                    const rawText = (el.textContent || el.getAttribute('aria-label') || '').trim();
                    if (!rawText) continue;
                    const lc = rawText.toLowerCase();
                    if (excludeRe.test(lc)) continue;
                    if (!slotTextRe.test(lc)) continue;
                    // Skip oversize containers (the whole drawer matches too)
                    const r = el.getBoundingClientRect();
                    if (r.height > 220) continue;   // tile, not the whole panel
                    if (r.width > vw * 0.5) continue;
                    seen.add(el);

                    // Extract TRUE delivery cost. Walmart's tile text packs
                    // strikethrough/promo prices alongside the real total, e.g.
                    //   "Express 30 min or less$9.95was $9.95 is now $0 delivery + $10.00 Express$10.00"
                    //   true cost = $0 (base after promo) + $10 (Express add-on) = $10
                    // Strategy:
                    //   - if "is now $X" present, X is the base (post-promo)
                    //     else first "$Y" not preceded by "was" is the base
                    //   - sum every "+ $Z" addition
                    //   - tile total = base + Σ adds
                    // Ignore $ amounts immediately following "was" (strikethrough).
                    const stripped = rawText.replace(/\\s+/g, ' ').trim();
                    let base = NaN;
                    const isNow = stripped.match(/is\\s*now\\s*\\$\\s?(\\d+(?:\\.\\d+)?)/i);
                    if (isNow) {
                        base = parseFloat(isNow[1]);
                    } else {
                        // First $X that isn't preceded by "was"
                        const re = /(was\\s*)?\\$\\s?(\\d+(?:\\.\\d+)?)/gi;
                        let m;
                        while ((m = re.exec(stripped)) !== null) {
                            if (m[1]) continue;  // "was $..." — strikethrough
                            base = parseFloat(m[2]);
                            break;
                        }
                    }
                    let adds = 0;
                    const addRe = /\\+\\s*\\$\\s?(\\d+(?:\\.\\d+)?)/g;
                    let am;
                    while ((am = addRe.exec(stripped)) !== null) {
                        adds += parseFloat(am[1]);
                    }
                    let price = 9999;
                    if (!isNaN(base)) price = base + adds;
                    // Express tier is more expensive in practice and is a worse
                    // signal-to-cost ratio for a non-grocery item. Soft-penalize
                    // it so it only wins if no other slot was found.
                    const isExpress = /\\bexpress\\b/i.test(lc);
                    if (isExpress) price += 0.001;   // tiebreak deprioritization

                    slots.push({
                        x: r.x, y: r.y, w: r.width, h: r.height,
                        text: rawText.slice(0, 140),
                        price: price,
                        base: isNaN(base) ? null : base,
                        adds: adds,
                        isExpress: isExpress,
                        tag: el.tagName.toLowerCase(),
                        aid: el.getAttribute('data-automation-id') || '',
                        role: el.getAttribute('role') || '',
                    });
                }

                if (slots.length === 0) {
                    // Diagnostic dump
                    const allRadios = document.querySelectorAll('input[type="radio"], [role="radio"]');
                    return {
                        found: false,
                        radio_count: allRadios.length,
                        viewport: { w: vw, h: vh },
                        drawer_buttons: Array.from(
                            document.querySelectorAll('button')
                        ).filter(b => {
                            const r = b.getBoundingClientRect();
                            return r.x >= minX && r.width > 0 && r.height > 0;
                        }).slice(0, 8).map(b => (b.textContent || '').trim().slice(0, 60)),
                        sample_aria: Array.from(allRadios).slice(0, 5).map(
                            r => (r.getAttribute('aria-label') || r.textContent || '?').slice(0, 80)
                        ),
                    };
                }

                // Extract a sortable time-position from the slot text.
                // Earlier-in-the-day wins on price ties. Conventions:
                //   - "Express 30 min or less"     → 0 (right now)
                //   - "Today 3 hr or less"          → 100 (today, vague window)
                //   - "Today 6pm-8pm" / "6pm-8pm"   → minutes from midnight
                //   - "Tomorrow 9am-11am"           → 24*60 + slot minutes
                //   - "Tue 5/12 8am-10am"           → day-offset*24*60 + slot
                // Anything we can't parse falls back to its on-screen y-position
                // (Walmart already lists slots earliest-to-latest top-to-bottom).
                const parseTime = (text, yPos) => {
                    const lc = text.toLowerCase();
                    if (lc.includes('express') && /\\bmin\\b/.test(lc)) return 0;
                    const hourRe = /(\\d{1,2})(?::(\\d{2}))?\\s*(am|pm)/i;
                    const m = lc.match(hourRe);
                    let dayOffset = 0;
                    if (lc.includes('tomorrow')) dayOffset = 24 * 60;
                    else if (/(tue|wed|thu|fri|sat|sun|mon)/i.test(lc) && !lc.includes('today')) {
                        // Best-effort: future days come AFTER today/tomorrow
                        dayOffset = 48 * 60;
                    }
                    if (m) {
                        let h = parseInt(m[1], 10);
                        const mins = parseInt(m[2] || '0', 10);
                        const ampm = m[3].toLowerCase();
                        if (ampm === 'pm' && h !== 12) h += 12;
                        if (ampm === 'am' && h === 12) h = 0;
                        return dayOffset + h * 60 + mins;
                    }
                    // Vague window like "Today 3 hr or less"
                    if (lc.includes('today')) return dayOffset + 12 * 60;  // mid-day default
                    return dayOffset + 24 * 60 + yPos;  // unknown — push down
                };

                for (const s of slots) {
                    s.timeKey = parseTime(s.text, s.y);
                }

                // Earliest time at cheapest price: sort by (price ASC,
                // timeKey ASC, y ASC). When two slots share the cheapest
                // price, the one that comes first in time wins.
                slots.sort((a, b) =>
                    a.price - b.price ||
                    a.timeKey - b.timeKey ||
                    a.y - b.y
                );
                const pick = slots[0];
                return {
                    found: true,
                    x: pick.x, y: pick.y, w: pick.w, h: pick.h,
                    text: pick.text,
                    price: pick.price,
                    timeKey: pick.timeKey,
                    tag: pick.tag,
                    candidates: slots.length,
                    samples: slots.slice(0, 5).map(
                        s => `${s.tag}@$${s.price}/t=${s.timeKey}|${s.text.slice(0, 60)}`
                    ),
                };
            })()
        """)

        if not slot_result or not slot_result.get('found'):
            logger.warning(
                "[PURCHASE] bookslot: no slot found (radios=%s, drawer_buttons=%s, samples=%s)",
                slot_result.get('radio_count') if slot_result else '?',
                slot_result.get('drawer_buttons') if slot_result else '?',
                slot_result.get('sample_aria') if slot_result else '?',
            )
            await self._screenshot("bookslot_no_slot")
            return False

        sx = slot_result['x'] + slot_result['w'] / 2 + random.uniform(-3, 3)
        sy = slot_result['y'] + slot_result['h'] / 2 + random.uniform(-2, 2)
        logger.info(
            "[PURCHASE] bookslot: %d slot(s); picked $%s (t=%s) %r at (%.0f, %.0f); samples=%s",
            slot_result.get('candidates'), slot_result.get('price'),
            slot_result.get('timeKey'), slot_result.get('text'), sx, sy,
            slot_result.get('samples'),
        )
        await self._realistic_click(sx, sy, "Delivery slot")
        await asyncio.sleep(random.uniform(0.6, 1.2))

        # Step 2: Continue button in the drawer (right half of viewport).
        # The drawer's Continue button text is JUST "Continue" — the cart-page
        # button reads "Continue to checkout". The latter sits on the page
        # UNDER the drawer overlay and can match if we're not careful.
        # Poll up to 3s: right after slot click the drawer briefly replaces
        # Continue with a spinner; without the poll we get a false miss.
        slot_y = slot_result['y']
        cta_result = None
        cta_deadline = time.monotonic() + 3.0
        while time.monotonic() < cta_deadline:
            cta_result = await self._page.evaluate(f"""
            (() => {{
                const vw = window.innerWidth;
                const vh = window.innerHeight;
                const minX = vw * 0.60;        // tighter: deep inside drawer
                const slotY = {slot_y};
                // Drawer Continue is BELOW the slot list. Slot row at slotY,
                // so the button must be at least 50px below it.
                const minY = slotY + 50;

                const excludeRe = /(close|cancel|back|change|view\\s*all|claim offer|no thanks|to\\s*checkout|continue\\s*to\\s*cart)/i;

                const candidates = Array.from(
                    document.querySelectorAll('button:not([disabled]), [role="button"]:not([aria-disabled="true"])')
                ).filter(btn => {{
                    const r = btn.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) return false;
                    if (r.right < minX) return false;
                    if (r.top < minY) return false;
                    if (r.top > vh + 50) return false;
                    return true;
                }});

                let best = null;
                let bestScore = -1;
                for (const btn of candidates) {{
                    const txt = (btn.textContent || '').trim().toLowerCase();
                    if (!txt) continue;
                    if (excludeRe.test(txt)) continue;
                    // Exact "continue" wins; "reserve" / "confirm" / "save" /
                    // "apply" / "use this" are also valid drawer CTAs.
                    let score = 0;
                    if (txt === 'continue') score = 100;
                    else if (txt === 'reserve' || txt === 'confirm' || txt === 'save') score = 95;
                    else if (txt.startsWith('continue') && !txt.includes('checkout')) score = 70;
                    else if (txt.startsWith('reserve') || txt.startsWith('confirm') ||
                             txt.startsWith('save') || txt.startsWith('apply') ||
                             txt.startsWith('use this')) score = 65;
                    if (score === 0) continue;
                    const r = btn.getBoundingClientRect();
                    // Prefer larger + lower-on-screen (drawer CTA is at bottom).
                    score += Math.min(r.width / 10, 30);
                    score += (r.y / vh) * 20;
                    if (score > bestScore) {{
                        bestScore = score;
                        best = {{
                            x: r.x, y: r.y, w: r.width, h: r.height,
                            text: txt.slice(0, 60), score: score,
                        }};
                    }}
                }}
                return best ? {{ found: true, ...best }} : {{
                    found: false,
                    drawer_buttons_below_slot: candidates.slice(0, 6).map(b => {{
                        const r = b.getBoundingClientRect();
                        return `${{(b.textContent || '').trim().slice(0, 40)}}@(${{Math.round(r.x)}},${{Math.round(r.y)}})`;
                    }}),
                }};
            }})()
        """)
            if cta_result and cta_result.get('found'):
                break
            await asyncio.sleep(0.2)

        if not cta_result or not cta_result.get('found'):
            logger.warning(
                "[PURCHASE] bookslot: Continue button not found after slot click (buttons_below=%s)",
                (cta_result or {}).get('drawer_buttons_below_slot'),
            )
            await self._screenshot("bookslot_no_cta")
            return False

        cx = cta_result['x'] + cta_result['w'] / 2 + random.uniform(-3, 3)
        cy = cta_result['y'] + cta_result['h'] / 2 + random.uniform(-2, 2)
        logger.info(
            "[PURCHASE] bookslot: clicking Continue %r (score=%.1f) at (%.0f, %.0f)",
            cta_result.get('text'), cta_result.get('score', 0), cx, cy,
        )
        await self._realistic_click(cx, cy, "Slot Continue")
        await asyncio.sleep(random.uniform(0.8, 1.5))
        return True

    async def _handle_delivery_day_modal(self, item_id: str):
        """
        Confirm the delivery day modal that appears during checkout.

        Walmart pre-selects a default delivery day. The user does NOT need to
        change the selection — the only required action is clicking the primary
        confirm CTA ("Continue", "Confirm", "Save"). Selecting a date button
        unnecessarily adds a behavioral signal during the most-scrutinized step.

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

            logger.warning("[PURCHASE] ⚠️  DELIVERY MODAL DETECTED — confirming default selection")

            # Find the primary confirm CTA inside the dialog. Walmart pre-selects
            # a default day — clicking Continue/Confirm with the default is what
            # a human normally does (most users don't change the suggested date).
            #
            # Strategy: locate the dialog, then within the dialog find the
            # primary action button. We pick the *last* enabled non-close button
            # in the dialog footer, since Walmart's UI puts the primary CTA on
            # the right side of the footer (after any secondary "Cancel" button).
            cta_info = await self._page.evaluate("""
                () => {
                    const dialog = document.querySelector('[role="dialog"]');
                    if (!dialog) return null;

                    // Look for explicit primary CTAs by text first
                    const primaryTexts = ['continue', 'confirm', 'save', 'apply', 'done', 'use this', 'looks good'];
                    const buttons = Array.from(dialog.querySelectorAll('button'));
                    const visible = buttons.filter(b => {
                        if (b.disabled || b.getAttribute('aria-disabled') === 'true') return false;
                        const r = b.getBoundingClientRect();
                        const s = window.getComputedStyle(b);
                        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                    });

                    // First try: button whose text matches a known primary CTA word
                    for (const b of visible) {
                        const txt = (b.innerText || b.textContent || '').trim().toLowerCase();
                        const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                        if (primaryTexts.some(p => txt === p || txt.startsWith(p + ' ') || aria.includes(p))) {
                            // Skip close buttons
                            if (txt.includes('close') || aria.includes('close') || txt.includes('cancel')) continue;
                            const r = b.getBoundingClientRect();
                            return { x: r.left + r.width/2, y: r.top + r.height/2, label: txt || aria, source: 'text-match' };
                        }
                    }

                    // Fallback: the LAST visible non-close, non-cancel button (primary CTA is rightmost)
                    const candidates = visible.filter(b => {
                        const txt = (b.innerText || b.textContent || '').trim().toLowerCase();
                        const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                        if (txt.includes('close') || aria.includes('close')) return false;
                        if (txt.includes('cancel')) return false;
                        // Skip radio-style "day" options — they're typically inside a list/group
                        // and we explicitly do not want to change the default selection
                        if (b.getAttribute('role') === 'radio') return false;
                        return true;
                    });
                    if (candidates.length === 0) return null;
                    const b = candidates[candidates.length - 1];
                    const r = b.getBoundingClientRect();
                    const txt = (b.innerText || b.textContent || '').trim().toLowerCase();
                    const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                    return { x: r.left + r.width/2, y: r.top + r.height/2, label: txt || aria, source: 'last-non-close' };
                }
            """)

            if not cta_info:
                logger.debug("[PURCHASE] No primary CTA found inside dialog — modal may have closed itself")
                return False

            logger.info(
                "[PURCHASE] Delivery modal: clicking primary CTA '%s' via %s at (%.0f, %.0f)",
                cta_info.get('label', '?'), cta_info.get('source', '?'),
                cta_info.get('x', 0), cta_info.get('y', 0),
            )

            # Brief human read pause before clicking confirm (skim the modal contents).
            # A confirm-with-default-value action is fast — humans glance and click.
            await asyncio.sleep(random.uniform(0.3, 0.7))

            x = cta_info['x'] + random.uniform(-3, 3)
            y = cta_info['y'] + random.uniform(-2, 2)
            clicked = await self._realistic_click(x, y, "Delivery modal confirm")
            if not clicked:
                # Fallback to direct CDP click if realistic_click failed
                clicked = await self._cdp_mouse_click(x, y)

            await asyncio.sleep(random.uniform(0.2, 0.5))

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
                logger.info("[PURCHASE] ✓ Delivery modal confirmed — settle pause")
                self._status_cb("[PURCHASE] Confirmed delivery day")

                # Settle pause — short, since we just confirmed a default value
                # (no decision was actually made; humans don't dwell after confirm).
                await asyncio.sleep(random.uniform(0.4, 0.9))

                # Wait for DOM to stabilize after modal close before resuming
                await self._wait_for_dom_stability(timeout=1500)

                # Move mouse to a randomized location near the main checkout form
                # so the next click doesn't appear to teleport from the modal CTA.
                mx = random.uniform(400, 700)
                my = random.uniform(300, 500)
                await self._page.mouse_move(x=mx, y=my)
                await asyncio.sleep(random.uniform(0.15, 0.4))

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
        """Dismiss Walmart+ upsell popup if present before Place Order.

        The Walmart+ upsell is shown on the most-scrutinized checkout page.
        Dismissing it via raw element.click() leaves a deterministic DOM-event
        signature that PerimeterX uses to fire `/blocked?g=b` (press-and-hold).
        We mirror the Place Order pattern: getBoundingClientRect → CDP mouse
        trajectory → realistic click, with a human read/decide pause before
        and a settle pause after.
        """
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
            popup_btn = await self._find_element(popup_selectors, timeout=1200)
            if not popup_btn:
                logger.debug("[PURCHASE] No Walmart+ popup detected — proceeding")
                return

            # Human read/decide pause before dismissing. A "No thanks" upsell is
            # a reflexive dismiss for most users — sub-second is realistic.
            await asyncio.sleep(random.uniform(0.4, 0.9))

            clicked = False
            try:
                rect = await popup_btn.apply("""(e) => {
                    e.scrollIntoView({ behavior: 'instant', block: 'center' });
                    const r = e.getBoundingClientRect();
                    return { x: r.left, y: r.top, w: r.width, h: r.height };
                }""")
                if rect and rect.get('w', 0) > 0 and rect.get('h', 0) > 0:
                    x = rect['x'] + rect['w'] / 2 + random.uniform(-4, 4)
                    y = rect['y'] + rect['h'] / 2 + random.uniform(-3, 3)
                    clicked = await self._realistic_click(x, y, "Walmart+ No thanks")
            except Exception as e:
                logger.debug("[PURCHASE] Walmart+ popup bounds lookup failed: %s — falling back", e)

            if not clicked:
                # Fallback: native click only if CDP path failed
                try:
                    await popup_btn.click()
                except Exception as e:
                    logger.debug("[PURCHASE] Walmart+ fallback click failed: %s", e)
                    return

            # Settle pause — modal close animation + DOM reflow (humans don't
            # immediately Place Order in the same frame as dismissing a modal)
            await asyncio.sleep(random.uniform(0.4, 0.8))
            await self._screenshot(f"walmart_plus_popup_dismissed_{item_id}")
            logger.info("[PURCHASE] Dismissed Walmart+ popup")
            self._status_cb("[PURCHASE] Dismissed Walmart+ popup")
        except Exception as e:
            logger.debug("[PURCHASE] Walmart+ popup dismiss check failed: %s (continuing)", e)

    async def _dismiss_any_modal(self):
        """
        Aggressively dismiss any visible modal/dialog by searching for common close patterns.
        Used as a fallback when specific modal handling fails.
        Returns True if a modal was found and dismissed.

        Modal close clicks happen during the most-scrutinized window (often the
        Place Order page). We return the close button's coordinates from JS and
        drive the click via CDP mouse trajectory instead of a synchronous
        element.click() — PerimeterX scores DOM-only clicks during checkout as
        bot signals.
        """
        try:
            # Locate a visible modal and return its close-button coordinates.
            # No clicking inside the evaluate — Python drives the click via CDP.
            result = await self._page.evaluate("""
                (() => {
                    const modals = document.querySelectorAll('[role="dialog"], [role="alertdialog"], .modal, [class*="modal"], [class*="Modal"], .overlay, [class*="overlay"], [data-testid*="modal"]');

                    for (const modal of modals) {
                        const style = window.getComputedStyle(modal);
                        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;

                        const closeButtons = [
                            ...modal.querySelectorAll('button[aria-label*="close" i]'),
                            ...modal.querySelectorAll('button[aria-label*="dismiss" i]'),
                            ...modal.querySelectorAll('[data-automation-id*="close"]'),
                            ...modal.querySelectorAll('button:last-child'),
                        ];

                        for (const btn of closeButtons) {
                            if (btn.offsetParent !== null) {
                                const r = btn.getBoundingClientRect();
                                if (r.width > 0 && r.height > 0) {
                                    return { found: true, method: 'close_button',
                                             x: r.x, y: r.y, w: r.width, h: r.height };
                                }
                            }
                        }

                        // No usable close button — caller will fall back to Escape key
                        return { found: true, method: 'escape_key' };
                    }

                    return { found: false };
                })()
            """)

            if not result or not result.get('found'):
                return False

            method = result.get('method')
            if method == 'close_button':
                x = result['x'] + result['w'] / 2 + random.uniform(-3, 3)
                y = result['y'] + result['h'] / 2 + random.uniform(-2, 2)
                clicked = await self._realistic_click(x, y, "Modal close")
                if not clicked:
                    # CDP click failed — fall through to Escape key
                    method = 'escape_key'

            if method == 'escape_key':
                try:
                    from zendriver.cdp import input_ as cdp_input
                    await self._page.send(cdp_input.dispatch_key_event(
                        type_="keyDown", key="Escape", code="Escape", windows_virtual_key_code=27,
                    ))
                    await asyncio.sleep(random.uniform(0.04, 0.10))
                    await self._page.send(cdp_input.dispatch_key_event(
                        type_="keyUp", key="Escape", code="Escape", windows_virtual_key_code=27,
                    ))
                except Exception as e:
                    logger.debug("[PURCHASE] CDP Escape failed: %s", e)
                    return False

            logger.debug("[PURCHASE] Dismissed modal via %s", method)
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

        Returns True if the cart was successfully reached and cleared (or already
        empty). Returns False if the cart navigation landed on /blocked or
        otherwise failed — in that case the caller should treat the session as
        poisoned (no immediate re-queue).
        """
        cart_cleared = False
        landed_on_blocked = False
        try:
            try:
                await asyncio.wait_for(self._page.get(WALMART_CART_URL), timeout=15.0)
            except asyncio.TimeoutError:
                logger.warning("[PURCHASE] _clear_cart: cart navigation timed out after 15s — treating as poisoned")
                self._last_cart_clear_blocked = True
                return False
            await asyncio.sleep(random.uniform(0.8, 1.4))

            # If cart navigation landed us on /blocked, the cart is NOT empty —
            # we just can't see it. Do not log "cart already empty" here, since
            # that masks a poisoned session and lets the manager re-queue blindly.
            current_url = self._page.url or ""
            if "/blocked" in current_url:
                landed_on_blocked = True
                logger.warning("[PURCHASE] _clear_cart: navigation landed on /blocked — cart state unknown, NOT cleared")
                self._status_cb("[PURCHASE] Cart clear blocked — session poisoned, skipping rewarm")
            else:
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
                    logger.info("[PURCHASE] Cart already empty — no cleanup needed")
                    self._status_cb("[PURCHASE] Cart already empty")
                    cart_cleared = True
                else:
                    logger.info("[PURCHASE] Post-attempt cleanup: removing %d cart item(s)", len(remove_btns))
                    self._status_cb(f"[PURCHASE] Removing {len(remove_btns)} cart item(s)")
                    for btn in remove_btns:
                        try:
                            await self._click_handle_via_cdp(btn, "cart Remove (cleanup)")
                            await asyncio.sleep(random.uniform(0.6, 1.0))
                        except Exception:
                            pass
                    cart_cleared = True
        except Exception as e:
            logger.warning("[PURCHASE] _clear_cart cleanup failed: %s", e)

        # Stash the poisoned-session signal so the manager can read it and
        # avoid re-queuing on the same _px3 cookie that just got challenged.
        self._last_cart_clear_blocked = landed_on_blocked

        # Re-warm Tab 1 after purchase to refresh stock monitor cookies — but
        # NOT if the cart navigation just hit /blocked (rewarm would also be
        # blocked and would burn the proxy cooldown).
        if landed_on_blocked:
            logger.warning("[PURCHASE] Skipping Tab 1 rewarm — session is in /blocked state")
            return cart_cleared

        if self._session and hasattr(self._session, 'rewarm_tab1'):
            try:
                logger.info("[PURCHASE] Re-warming Tab 1 after purchase...")
                await self._session.rewarm_tab1()
            except Exception as e:
                logger.warning("[PURCHASE] Tab 1 rewarm failed: %s (stock monitor may be briefly blocked)", e)

        return cart_cleared

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
        # EVENT LOG: the PerimeterX /blocked wall is the dominant drop-night
        # failure (the "456 Access Denied at ATC" the research flagged). Record
        # every occurrence so the post-drop analysis quantifies how often we hit
        # it and on which session/URL.
        try:
            from walmart import queue_events
            queue_events.event("px_blocked", url=current_url[:120],
                               session_id=getattr(self._session, "id", None))
            queue_events.capture("error", url=current_url, body="px_blocked_redirect",
                                 kind="px_blocked")
        except Exception:
            pass

        # Use session manager's challenge solver if available (it has press-and-hold logic)
        if self._session and hasattr(self._session, '_handle_blocked_page_on'):
            try:
                solved = await self._session._handle_blocked_page_on(self._page)
                if solved:
                    self._status_cb("[PURCHASE] Challenge solved via session manager")
                    logger.info("[PURCHASE] /blocked challenge solved")
                    return True
                else:
                    self._status_cb("[PURCHASE] Challenge solve FAILED — aborting purchase")
                    logger.error("[PURCHASE] /blocked challenge could not be solved")
                    await self._screenshot("blocked_unsolved")
                    return False
            except Exception as e:
                logger.error("[PURCHASE] Challenge solver error: %s", e)
                return False

        # Fallback: wait and check if it auto-resolves (some challenges are time-based)
        logger.info("[PURCHASE] No session manager — waiting for auto-resolve")
        for attempt in range(3):
            await asyncio.sleep(5)
            if "/blocked" not in (self._page.url or ""):
                self._status_cb("[PURCHASE] Challenge auto-resolved")
                return True

        self._status_cb("[PURCHASE] Challenge could not be resolved — aborting purchase")
        await self._screenshot("blocked_no_session")
        return False

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

        Humans never move the mouse in a straight line at uniform speed. Akamai's
        `_abck` sensor runs velocity + curvature analysis on the mouseMoved event
        stream — straight-line linear interpolation between clicks is flagged.

        Trajectory: quadratic Bezier from the last cursor position to (x, y),
        with a random control point offset from the segment midpoint to bend
        the path. Inter-move delay is velocity-weighted via sin(π·t) so the
        cursor moves fast in the middle and decelerates near both endpoints
        (matches Fitts's law / human reach kinematics).
        """
        try:
            from zendriver.cdp import input_ as cdp_input

            current_x = self._last_mouse_x
            current_y = self._last_mouse_y

            # Control point: midpoint of the segment with a perpendicular-ish
            # random offset. Magnitude scales with distance so short hops don't
            # get exaggerated arcs; capped so long hops don't loop offscreen.
            dx = x - current_x
            dy = y - current_y
            dist = math.hypot(dx, dy)
            offset_mag = min(80.0, max(15.0, dist * 0.18))
            # Perpendicular unit vector (rotate the path direction by 90°)
            if dist > 1.0:
                px = -dy / dist
                py = dx / dist
            else:
                px, py = 0.0, 0.0
            sign = random.choice((-1.0, 1.0))
            jitter = random.uniform(-offset_mag * 0.4, offset_mag * 0.4)
            cp_x = (current_x + x) / 2 + sign * offset_mag * px + jitter
            cp_y = (current_y + y) / 2 + sign * offset_mag * py + random.uniform(-offset_mag * 0.4, offset_mag * 0.4)

            # More steps on long paths, fewer on short. 4-9 typical, clamps for safety.
            steps = max(4, min(9, int(dist / 80) + random.randint(3, 5)))

            for i in range(1, steps + 1):
                t = i / (steps + 1)
                bx = (1 - t) ** 2 * current_x + 2 * (1 - t) * t * cp_x + t ** 2 * x
                by = (1 - t) ** 2 * current_y + 2 * (1 - t) * t * cp_y + t ** 2 * y

                # Add small Gaussian jitter so the curve isn't a perfect Bezier
                bx += random.gauss(0, 0.6)
                by += random.gauss(0, 0.6)

                await self._page.send(cdp_input.dispatch_mouse_event(
                    type_="mouseMoved", x=int(bx), y=int(by), pointer_type="mouse"
                ))

                # Velocity-weighted: 12ms in the middle of the path,
                # ~55ms near the endpoints. sin(π·t) peaks at t=0.5.
                # Real humans accelerate-then-decelerate (ballistic phase + corrective phase).
                base = 0.055 - 0.043 * math.sin(math.pi * t)
                await asyncio.sleep(base + random.uniform(-0.005, 0.012))

            # Final settle move to exact target (with sub-pixel jitter)
            await self._page.send(cdp_input.dispatch_mouse_event(
                type_="mouseMoved", x=int(x), y=int(y), pointer_type="mouse"
            ))
            # Pre-click dwell — humans hesitate ~30-110ms after reaching a target
            await asyncio.sleep(random.uniform(0.03, 0.11))

            # Press + release
            await self._page.send(cdp_input.dispatch_mouse_event(
                type_="mousePressed", x=x, y=y,
                button=cdp_input.MouseButton.LEFT, buttons=1,
                click_count=1, pointer_type="mouse"
            ))
            # Press hold — humans hold a button for 60-130ms typically
            await asyncio.sleep(random.uniform(0.06, 0.13))

            await self._page.send(cdp_input.dispatch_mouse_event(
                type_="mouseReleased", x=x, y=y,
                button=cdp_input.MouseButton.LEFT, buttons=0,
                click_count=1, pointer_type="mouse"
            ))

            self._last_mouse_x = x
            self._last_mouse_y = y

            if selector:
                logger.debug("[PURCHASE] Realistic click on %s at (%.0f, %.0f) — %d-step Bezier, dist=%.0f",
                             selector, x, y, steps, dist)
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

    async def _click_handle_via_cdp(self, el, label: str = "") -> bool:
        """
        Resolve an element handle's bounding rect and click it via CDP mouse
        trajectory. Falls back to el.click() if the rect lookup fails or the
        CDP click errors. Used for lower-scrutiny clicks (cart Remove buttons,
        cleanup paths) where the full _realistic_click variance isn't critical
        but we still want pointer events rather than synchronous DOM clicks.
        """
        try:
            rect = await el.apply("""(e) => {
                e.scrollIntoView({ behavior: 'instant', block: 'center' });
                const r = e.getBoundingClientRect();
                return { x: r.x, y: r.y, w: r.width, h: r.height };
            }""")
            if not rect or not (rect.get('w', 0) > 0):
                await el.click()
                return False
            x = rect['x'] + rect['w'] / 2 + random.uniform(-3, 3)
            y = rect['y'] + rect['h'] / 2 + random.uniform(-2, 2)
            if await self._realistic_click(x, y, label):
                return True
            await el.click()
            return False
        except Exception as e:
            logger.debug("[PURCHASE] _click_handle_via_cdp(%s) fallback: %s", label, e)
            try:
                await el.click()
            except Exception:
                pass
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
        Returns True if stabilized within timeout, False if timeout hit.

        Implementation note: a single persistent MutationObserver is installed
        on first call and accumulates a counter on `window.__walmartMutCount`.
        Each poll just reads the counter delta — no observer create/destroy
        churn. HUMAN Security tracks observer creation rate as a bot signal;
        real React apps have a small number of long-lived observers, not
        rapid-fire creation. The observer is install-once-per-tab and is
        idempotent (a re-install no-ops if the flag is already set).
        """
        started = time.monotonic()
        deadline = started + timeout / 1000.0

        # Install the persistent observer (no-op after first call per tab)
        try:
            await self._page.evaluate("""
                (() => {
                    if (window.__walmartMutObserverInstalled) return;
                    window.__walmartMutObserverInstalled = true;
                    window.__walmartMutCount = 0;
                    const o = new MutationObserver((muts) => {
                        window.__walmartMutCount += muts.length;
                    });
                    o.observe(document.body, {
                        childList: true,
                        subtree: true,
                        attributes: true,
                        characterData: false
                    });
                    window.__walmartMutObserver = o;
                })()
            """)
        except Exception as e:
            # Fall back to a single short sleep — better than failing the
            # whole purchase if observer install errors.
            logger.debug("[PURCHASE] DOM observer install failed (%s) — short-sleep fallback", e)
            await asyncio.sleep(0.6)
            return False

        last_count = -1
        stable_since = None  # monotonic time when count last changed

        while time.monotonic() < deadline:
            try:
                count = await self._page.evaluate("window.__walmartMutCount || 0")
            except Exception:
                # Page navigated mid-poll, treat as not-yet-stable
                await asyncio.sleep(0.05)
                continue
            now = time.monotonic()
            if count != last_count:
                last_count = count
                stable_since = now
            elif stable_since is not None and (now - stable_since) >= 0.8:
                elapsed = now - started
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
        """Save a screenshot to walmart/logs/ for debugging.

        Wrapped in wait_for because CDP Page.captureScreenshot can hang
        indefinitely when the tab is mid-navigation (post-queue-admission was
        a real incident — 2.3 min hang in the executor's fallback path).
        """
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = f"{LOGS_DIR}/{label}_{ts}.png"
            await asyncio.wait_for(
                self._page.save_screenshot(filename=path),
                timeout=3.0,
            )
            logger.debug("[PURCHASE] Screenshot: %s", path)
        except asyncio.TimeoutError:
            logger.warning("[PURCHASE] Screenshot timed out (%s) — tab likely mid-nav", label)
        except Exception as e:
            logger.warning("[PURCHASE] Screenshot failed: %s", e)

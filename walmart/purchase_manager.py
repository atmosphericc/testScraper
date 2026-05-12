"""
Walmart purchase manager — orchestrates stock monitor → queue → purchase executor.

Responsibilities:
  - Start/stop the stock monitor
  - On in-stock signal: acquire a checkout proxy, warm the session, run the purchase executor
  - Circuit breaker: pause after N consecutive failures
  - Thread-safe state tracking
  - Status callback for dashboard/SSE integration
"""

import asyncio
import logging
import os
import random
import threading
import time
from typing import Callable, Optional

from .config import (
    CIRCUIT_BREAKER_FAILURES,
    CIRCUIT_BREAKER_PAUSE,
    get_enabled_products,
)

# Named timing constants (seconds) — avoids magic numbers in the purchase flow
_CHECKOUT_PROXY_TIMEOUT = 15.0    # how long to wait for a sticky checkout proxy
_POST_PURCHASE_COOLDOWN = 5       # sleep before transitioning state after purchase
_FAILURE_COOLDOWN = 30            # per-item cooldown after a failed purchase attempt
from .proxy_manager import ProxyManager
from .session_manager import WalmartSessionManager
from .stock_monitor import WalmartStockMonitor
from .purchase_executor import WalmartPurchaseExecutor
from .logging_manager import get_walmart_logger, log_activity, log_error

logger = logging.getLogger(__name__)
walmart_logger = get_walmart_logger()


class PurchaseState:
    IDLE = "IDLE"
    MONITORING = "MONITORING"
    IN_QUEUE = "IN_QUEUE"
    PURCHASING = "PURCHASING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"


class WalmartPurchaseManager:
    """
    Top-level orchestrator for the Walmart bot.

    Usage:
        def on_status(msg): print(msg)

        manager = WalmartPurchaseManager(status_callback=on_status)
        await manager.start(email="you@example.com", password="secret")
        # runs indefinitely until stopped
        await manager.stop()
    """

    def __init__(
        self,
        status_callback: Optional[Callable[[str], None]] = None,
    ):
        # Activity log must exist before _status_cb is wired — callbacks may fire during init
        self._activity_log: list[dict] = []
        self._activity_lock = threading.Lock()

        _raw_cb = status_callback or (lambda msg: None)

        def _wrapped_status(msg: str):
            """Log every status message into the activity log AND fire the external callback."""
            self._log_activity(msg)
            _raw_cb(msg)

        self._status_cb = _wrapped_status

        self._proxy_manager = ProxyManager()
        self._session = WalmartSessionManager(status_callback=self._status_cb)
        self._monitor = WalmartStockMonitor(
            proxy_manager=self._proxy_manager,
            session=self._session,
            on_in_stock=self._on_in_stock_signal,
            on_stock_change=self._on_stock_change,
            status_callback=self._status_cb,
        )
        # Feed browser-intercepted GraphQL responses into the monitor.
        # This is the primary stock check path — real Chrome TLS, no proxy blocks.
        self._session.set_stock_intercept_callback(self._monitor.on_browser_graphql)

        # Optional callback for dashboard: cb(item_id, in_stock, price)
        self._stock_update_callback: Optional[Callable] = None

        # State
        self._lock = threading.Lock()
        self._state: dict[str, str] = {}   # item_id → PurchaseState
        self._in_stock_ids: set[str] = set()  # items currently known in-stock
        self._consecutive_failures = 0
        self._circuit_open_until: float = 0.0
        self._cooldown_until: dict[str, float] = {}
        # Per-item last-known orderLimit (refreshed on every in-stock signal).
        # The test-mode re-queue path lacks the original signal payload, so we
        # cache it here and re-apply it on re-fire — otherwise the second
        # purchase in a test loop would silently drop qty back to 1.
        self._last_order_limit: dict[str, Optional[int]] = {}
        self._running = False
        self._login_ok = False   # set True after successful login in start()
        self._warmup_done = False  # set True after initial session warm + harvest

        # Running event loop — captured in start(), shared by browser + harvester + purchases
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # Startup / shutdown
    # ------------------------------------------------------------------

    async def start(self):
        """
        Start the Walmart bot:
          1. Launch browser, restore session cookies from walmart_relogin.py
          2. Warm session on configured products
          3. Start stock monitor
        """
        self._running = True
        self._status_cb("[MANAGER] Starting Walmart purchase manager...")

        # Capture the running event loop (manager_loop from blueprint.py).
        # The browser session, harvester, and purchase executor must all share
        # this same loop — Patchright objects are bound to the loop they were
        # created on, so scheduling purchases on a separate loop causes
        # cross-loop violations and silent browser failures.
        # Use get_running_loop() — get_event_loop() is deprecated since 3.10
        # and on 3.12+ returns a NEW loop when no loop is running, which
        # would silently misbehave if start() were ever called outside an
        # await context.
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — fallback for any caller that runs start()
            # outside an await context. Matches the prior behavior.
            self._loop = asyncio.get_event_loop()

        # Start browser session (includes a 3s network-stack warm-up internally)
        await self._session.start()

        # Validate session using saved cookies from walmart_relogin.py
        session_ok = await self._session.validate_session()
        if not session_ok:
            self._status_cb(
                "[MANAGER] NOT LOGGED IN — run walmart_relogin.py to create a session, then restart."
            )
            logger.warning("[MANAGER] No valid session — bot stopped. Run walmart_relogin.py.")
            self._running = False
            return
        self._login_ok = True

        # Warm session on products (Tab 1 roams, builds clean _px3 cookies)
        products = get_enabled_products()
        if products:
            item_ids = [p["item_id"] for p in products]
            await self._session.warm_session(item_ids)

        # Do an immediate cookie harvest so workers have valid cookies from the first check
        await self._session.harvest_now()

        # Return Tab 1 to Walmart.com so stock monitor fetches work correctly.
        # warm_session() leaves Tab 1 on the last external site visited (Google, Amazon, etc).
        # The stock monitor's fetch(/ip/{item_id}) is relative and needs walmart.com as origin.
        self._status_cb("[MANAGER] Returning Tab 1 to Walmart for stock monitoring")
        try:
            await self._session.return_tab1_to_walmart()
        except Exception as e:
            logger.warning("[MANAGER] Failed to return Tab 1 to Walmart: %s", e)

        # Open Tab 2 on the actual product page we're about to buy.
        # Pre-warming on product page allows us to jump straight to cart/checkout
        # on purchase signal, saving 8-13s vs. navigating from search page.
        # PerimeterX flags all navigation anyway, so warming on the actual PDP is optimal.
        products = get_enabled_products()
        if products:
            warmup_url = f"https://www.walmart.com/ip/{products[0]['item_id']}"
        else:
            warmup_url = "https://www.walmart.com/browse/electronics"
        self._status_cb(f"[MANAGER] Tab 2 pre-warming on product page: {warmup_url}")

        await self._session.open_checkout_tab(warmup_url=warmup_url)

        # Start cookie harvester — keeps _px3 fresh for proxy workers every 30s
        self._session.start_harvester(self._loop)

        # Warmup is complete — purchases are now allowed
        self._warmup_done = True

        # Start stock monitor
        self._monitor.start()
        self._status_cb("[MANAGER] Walmart bot running — monitoring stock")
        logger.debug("[MANAGER] Walmart bot started, monitoring %d product(s)", len(products))

    async def stop(self):
        """Gracefully stop the bot. Idempotent — safe to call multiple times.

        Called from the dashboard's atexit/SIGINT handlers, possibly more than
        once (e.g. blueprint.stop_manager + walmart_app graceful shutdown).
        Without the early-return, each call would re-fire monitor.stop()
        (which joins worker threads with a 10s timeout) and re-await
        session.stop() (which closes an already-dead browser).
        """
        if not self._running:
            return
        self._running = False
        self._monitor.stop()
        self._session.stop_harvester()
        await self._session.stop()
        self._status_cb("[MANAGER] Walmart bot stopped")
        logger.debug("[MANAGER] Bot stopped")

    def set_stock_update_callback(self, cb: Callable):
        """Register a callback fired on every stock state change: cb(item_id, in_stock, price)."""
        self._stock_update_callback = cb

    def _on_stock_change(self, item_id: str, in_stock: bool, price):
        # Keep the in-stock set in sync with the monitor — without the OOS
        # branch the set only grows, so a long-OOS item that was briefly
        # in-stock once would still be considered for the priority-override
        # path forever. (No call site ever removed entries before this fix.)
        with self._lock:
            if in_stock:
                self._in_stock_ids.add(item_id)
            else:
                self._in_stock_ids.discard(item_id)
        if self._stock_update_callback:
            self._stock_update_callback(item_id, in_stock, price)

    # ------------------------------------------------------------------
    # In-stock callback (called from monitor's thread)
    # ------------------------------------------------------------------

    def _on_in_stock_signal(
        self,
        item_id: str,
        offer_id: Optional[str],
        name: str,
        price: Optional[float],
        order_limit: Optional[int] = None,
    ):
        """
        Called by WalmartStockMonitor when a configured item is in stock.
        Schedules a purchase attempt on the dedicated event loop.
        """
        if not self._login_ok:
            logger.debug("[MANAGER] Skipping purchase — not logged in")
            return

        if not self._warmup_done:
            logger.debug("[MANAGER] Skipping purchase — warmup not complete yet")
            return

        # Track which items are currently in-stock for priority selection
        with self._lock:
            self._in_stock_ids.add(item_id)
            if order_limit is not None:
                self._last_order_limit[item_id] = order_limit

        with self._lock:
            current_state = self._state.get(item_id, PurchaseState.IDLE)
            if current_state in (PurchaseState.PURCHASING, PurchaseState.IN_QUEUE,
                                  PurchaseState.SUCCESS):
                logger.debug("[MANAGER] %s already in %s — skipping", item_id, current_state)
                return

            # Double-purchase cooldown after a failed attempt
            if time.monotonic() < self._cooldown_until.get(item_id, 0):
                logger.debug("[MANAGER] %s in cooldown — skipping signal", item_id)
                return

            # Circuit breaker check
            if time.monotonic() < self._circuit_open_until:
                remaining = int(self._circuit_open_until - time.monotonic())
                self._status_cb(f"[MANAGER] Circuit open — paused {remaining}s")
                return

            # Capture loop reference while holding the lock to avoid race between
            # check and use (self._loop could be set to None by stop() concurrently)
            loop = self._loop
            if loop is None or not loop.is_running():
                logger.warning("[MANAGER] Purchase loop not ready — skipping signal for %s", item_id)
                return

            self._state[item_id] = PurchaseState.PURCHASING

        self._log_activity(f"In-stock signal: {name} @ ${price}")
        logger.warning("[MANAGER] In-stock signal for %s — scheduling purchase", item_id)

        # Priority selection: if a higher-priority product is also in-stock and
        # not in a terminal/active state, buy that one instead.
        products = get_enabled_products()
        skip_states = (PurchaseState.PURCHASING, PurchaseState.SUCCESS, PurchaseState.IN_QUEUE)
        original_item_id = item_id  # remember the triggered ID so we can reset it on override
        with self._lock:
            current_states = dict(self._state)
            in_stock_ids = set(self._in_stock_ids)
        in_stock_ids.add(item_id)  # always include the triggered item

        eligible = [
            p for p in products
            if p["item_id"] in in_stock_ids
            and current_states.get(p["item_id"]) not in skip_states
        ]
        if eligible:
            best = min(eligible, key=lambda p: p.get("priority", 999))
            if best["item_id"] != item_id:
                logger.warning(
                    "[MANAGER] Priority override: purchasing %s (priority %s) instead of %s",
                    best["item_id"], best.get("priority", "?"), item_id,
                )
                self._log_activity(
                    f"Priority override: buying {best.get('name', best['item_id'])} "
                    f"(priority {best.get('priority', '?')}) over {name}"
                )
                item_id = best["item_id"]
                name = best.get("name", item_id)
                # Atomically: release the original item back to MONITORING (it
                # was marked PURCHASING above for the triggered ID — without
                # this it would be stuck PURCHASING forever, blocking all
                # future signals via the early-return at the top of this
                # method), and mark the override target as PURCHASING.
                with self._lock:
                    if self._state.get(original_item_id) == PurchaseState.PURCHASING:
                        self._state[original_item_id] = PurchaseState.MONITORING
                    self._state[item_id] = PurchaseState.PURCHASING

        item_url = f"https://www.walmart.com/ip/x/{item_id}"
        desired_qty: Optional[int] = None
        for p in products:
            if p["item_id"] == item_id:
                item_url = p.get("url", item_url)
                dq = p.get("desired_qty")
                if isinstance(dq, int) and dq > 0:
                    desired_qty = dq
                break

        # Hard cap — Walmart's UI dropdown maxes at 10 for most items.
        # Override via WALMART_MAX_QTY_CAP env (e.g. for soft-cap tests).
        try:
            hard_cap = int(os.environ.get("WALMART_MAX_QTY_CAP", "10"))
        except ValueError:
            hard_cap = 10
        hard_cap = max(1, min(hard_cap, 10))

        # Clamp: limit ≤ orderLimit ≤ hard_cap; respect desired_qty if set.
        candidates = [hard_cap]
        if isinstance(order_limit, int) and order_limit > 0:
            candidates.append(order_limit)
        if desired_qty is not None:
            candidates.append(desired_qty)
        quantity = max(1, min(candidates))

        if quantity > 1:
            logger.info(
                "[MANAGER] Quantity resolved for %s: qty=%d (orderLimit=%s, desired=%s, cap=%d)",
                item_id, quantity, order_limit, desired_qty, hard_cap,
            )

        # Schedule on the purchase event loop using the captured reference
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._run_purchase(item_id, item_url, name, quantity),
                loop,
            )
        except RuntimeError as e:
            logger.warning("[MANAGER] Could not schedule purchase — loop may have shut down: %s", e)
            with self._lock:
                self._state[item_id] = PurchaseState.MONITORING
            return

        def _on_done(f):
            try:
                f.result()
            except Exception as e:
                logger.exception("[MANAGER] Purchase task raised unhandled exception: %s", e)
                # _run_purchase has its own finally that resets state, but if it raises
                # before that finally runs (e.g. CancelledError), reset state here as fallback
                with self._lock:
                    if self._state.get(item_id) == PurchaseState.PURCHASING:
                        self._state[item_id] = PurchaseState.MONITORING

        future.add_done_callback(_on_done)

    # ------------------------------------------------------------------
    # Purchase flow
    # ------------------------------------------------------------------

    async def _run_purchase(self, item_id: str, item_url: str, name: str, quantity: int = 1):
        """Run a full purchase attempt, managing proxy + session warming."""
        checkout_proxy = None
        executor = None
        try:
            # Acquire a sticky checkout proxy
            checkout_proxy = self._proxy_manager.acquire_checkout_proxy(timeout=_CHECKOUT_PROXY_TIMEOUT)
            if checkout_proxy is None and self._proxy_manager.has_checkout_proxies():
                logger.warning("[MANAGER] No checkout proxy available after %ss timeout — proceeding without proxy", _CHECKOUT_PROXY_TIMEOUT)
                self._status_cb("[MANAGER] No checkout proxy available — trying without proxy")
            if checkout_proxy is None and not self._proxy_manager.has_checkout_proxies():
                logger.warning("[MANAGER] No checkout proxies configured — running all checkouts without proxy (higher block risk)")

            # Ensure browser is alive — restart it if it crashed
            if not self._session.is_ready():
                self._status_cb("[MANAGER] Browser not ready — attempting restart...")
                logger.warning("[MANAGER] Browser not ready, restarting session")
                try:
                    await self._session.stop()
                except Exception as e:
                    logger.warning("[MANAGER] Error stopping session during restart: %s", e)
                await self._session.start()
                # Re-auth via saved cookies (same path as initial startup)
                session_ok = await self._session.validate_session()
                if not session_ok:
                    self._login_ok = False
                    raise RuntimeError("Browser restarted but session validation failed — run walmart_relogin.py")
                # Re-wire the new page into the monitor
                self._monitor.set_page(self._session.get_checkout_page())
                # Restart the cookie harvester — session.stop() cancels the
                # prior task, so without this the post-restart browser has
                # no keep-alive refreshing _px3 and the next stock-check
                # batch would see stale cookies (most likely BLOCKED).
                if self._loop is not None:
                    try:
                        self._session.start_harvester(self._loop)
                        logger.info("[MANAGER] Cookie harvester restarted after browser restart")
                    except Exception as e:
                        logger.warning("[MANAGER] Failed to restart harvester after browser restart: %s", e)

            # Re-warm session if _px3 is stale
            if self._session.needs_rewarm():
                await self._session.warm_session([item_id])

            # Stock monitor health check — restart if dead
            if not self._is_monitor_healthy():
                logger.critical("[MANAGER] Stock monitor is dead — attempting restart")
                try:
                    self._monitor.stop()  # now blocks until thread exits (up to 10s)
                    self._monitor.start()
                    logger.warning("[MANAGER] Stock monitor restarted successfully")
                except Exception as _me:
                    logger.error("[MANAGER] Failed to restart stock monitor: %s", _me)

            # Snapshot the trusted monitor cookies BEFORE Tab 2 enters checkout.
            # Tab 2's cart/checkout navigations rotate _px3 with PerimeterX-flagged
            # values; restoring this snapshot after checkout prevents the stock
            # monitor from sending the poisoned _px3 on resume (which trips the
            # circuit breaker and stalls the bot for 120s).
            try:
                await self._session.snapshot_monitor_cookies()
            except Exception as e:
                logger.warning("[MANAGER] Pre-checkout cookie snapshot failed: %s", e)

            # Pause stock monitor during purchase — its rapid fetch() calls on Tab 1
            # contaminate the shared _px3 cookie with bot-like behavioral signals,
            # causing the checkout tab (Tab 2) to get /blocked on cart navigation.
            self._monitor.pause()
            self._status_cb("[MANAGER] Stock monitor paused for purchase")
            logger.info("[MANAGER] Stock monitor paused — protecting _px3 for checkout")

            # Also pause the cookie harvester — it roams Tab 1 across browse/category
            # pages on its keep-alive loop. Concurrent Tab 1 traffic during the
            # most-scrutinized checkout window adds aggregate behavioral signal
            # to the shared browser context and risks /blocked on Tab 2.
            try:
                self._session.pause_harvester()
                logger.info("[MANAGER] Cookie harvester paused — protecting checkout context")
            except Exception as e:
                logger.warning("[MANAGER] Harvester pause failed: %s", e)

            # Let the browser settle briefly after pausing fetch() spam, then
            # refresh cookies so Tab 2 starts with a clean _px3. The pause flag
            # stops new fetches; in-flight ones complete in 150-400ms typically,
            # so a randomized 400-800ms settle covers that with margin while
            # saving ~700-1100ms on the signal→purchase critical path.
            await asyncio.sleep(random.uniform(0.4, 0.8))
            try:
                await self._session.harvest_now()
            except Exception as e:
                logger.warning("[MANAGER] Pre-purchase cookie refresh failed: %s", e)

            # Run purchase on the dedicated checkout tab (Tab 2) — cookies from the
            # harvester tab (Tab 1) are shared automatically via the same browser context.
            page = self._session.get_checkout_page()
            if not page:
                raise RuntimeError("No browser page available")

            executor = WalmartPurchaseExecutor(page, self._status_cb, session=self._session)
            result = await executor.purchase(item_id=item_id, item_url=item_url, quantity=quantity)

            with self._lock:
                if result.success and result.order_id not in ("TEST_MODE", "DRY_RUN"):
                    self._state[item_id] = PurchaseState.SUCCESS
                    self._consecutive_failures = 0
                    self._circuit_open_until = 0.0
                    self._log_activity(f"PURCHASE SUCCESS: {name} — Order #{result.order_id}")
                    logger.warning("[MANAGER] Purchase success: %s — %s", name, result.order_id)
                elif result.success:
                    # Test/dry-run completed — reset to MONITORING immediately so
                    # the next in-stock cycle triggers another test run
                    self._state[item_id] = PurchaseState.MONITORING
                    self._consecutive_failures = 0
                    self._log_activity(f"TEST RUN complete: {name} — cart cleared, back to monitoring")
                    logger.debug("[MANAGER] Test run complete for %s — resuming monitoring", item_id)
                else:
                    self._state[item_id] = PurchaseState.FAILED
                    self._consecutive_failures += 1
                    self._log_activity(f"Purchase failed: {name} — {result.error}")
                    logger.warning("[MANAGER] Purchase failed: %s — %s", name, result.error)
                    self._check_circuit_breaker()

        except Exception as e:
            with self._lock:
                self._state[item_id] = PurchaseState.FAILED
                self._consecutive_failures += 1
                self._check_circuit_breaker()
            self._log_activity(f"Purchase error: {name} — {e}")
            logger.exception("[MANAGER] Purchase error for %s", item_id)

        finally:
            # Re-warm Tab 1 before resuming stock monitor — the purchase flow
            # navigates Tab 2 through cart/checkout which can contaminate the
            # shared PerimeterX session, causing Tab 1's fetch() stock checks
            # to hit /blocked on resume.
            try:
                await self._session.rewarm_tab1()
            except Exception as e:
                logger.warning("[MANAGER] Tab 1 re-warm failed: %s", e)

            # Restore the pre-checkout cookie snapshot so Tab 1's stock monitor
            # fetches go out with the trusted _px3 (not the rotated/flagged one
            # from Tab 2's checkout flow). This is what prevents the BLOCKED
            # cascade + circuit-breaker trip immediately after a successful run.
            try:
                await self._session.restore_monitor_cookies()
            except Exception as e:
                logger.warning("[MANAGER] Post-checkout cookie restore failed: %s", e)

            # Resume stock monitor regardless of purchase outcome
            if self._monitor.is_paused:
                self._monitor.resume()
                self._status_cb("[MANAGER] Stock monitor resumed")
                logger.info("[MANAGER] Stock monitor resumed after purchase attempt")

            # Resume the cookie harvester last — after monitor cookies are
            # restored and the stock monitor is back up. Pause may have been
            # skipped if the executor never reached the pause line, so we
            # resume unconditionally (no-op if already not paused).
            try:
                self._session.resume_harvester()
                logger.info("[MANAGER] Cookie harvester resumed")
            except Exception as e:
                logger.warning("[MANAGER] Harvester resume failed: %s", e)

            if checkout_proxy:
                self._proxy_manager.release_checkout_proxy(checkout_proxy)

            # Tab 2 stays on /cart after _clear_cart — no re-warm needed.
            # _navigate() will handle navigation to the correct product on next trigger.

            # Reset state to MONITORING after a delay so we can try again on next restock
            await asyncio.sleep(_POST_PURCHASE_COOLDOWN)
            with self._lock:
                current = self._state.get(item_id)
                if current == PurchaseState.FAILED:
                    self._cooldown_until[item_id] = time.monotonic() + _FAILURE_COOLDOWN
                self._state[item_id] = PurchaseState.MONITORING

            # If item is still in stock after purchase (test mode), re-queue so the
            # cycle repeats without waiting for a stock change event. SKIP re-queue
            # when the previous attempt's _clear_cart hit /blocked or the monitor
            # circuit breaker is open — re-queuing on a poisoned _px3 cookie cascades
            # into more BLOCKED responses and a longer Akamai cooldown.
            #
            # IMPORTANT: re-purchasing the same item within seconds is the strongest
            # behavioral bot signal in the entire flow. Real humans don't buy two of
            # the same item back-to-back. PerimeterX's score for a session that does
            # this accumulates fast and trips /blocked on the next cart navigation
            # (see live test 2026-05-03 — cycle 1 succeeded, cycle 2 hit /blocked at
            # cart with `g=b` press-and-hold). For test-mode regression loops we
            # intentionally need to repeat, so we add a randomized cooldown that lets
            # the PerimeterX score decay and _px3 rotate naturally via the harvester.
            #
            # Env var WALMART_TEST_LOOP_COOLDOWN overrides the default range:
            #   - unset / "default":  random 90-180s (recommended for endless test loops)
            #   - "off" / "0":        no cooldown (legacy behavior — likely to get blocked)
            #   - "<seconds>":        fixed delay in seconds
            session_poisoned = bool(getattr(executor, "_last_cart_clear_blocked", False))
            cb_open = time.monotonic() < getattr(self._monitor, "_monitor_circuit_open_until", 0.0)
            stock_states = self._monitor.get_stock_states()
            if stock_states.get(item_id, {}).get("in_stock", False):
                if session_poisoned or cb_open:
                    logger.warning(
                        "[MANAGER] Item %s still in stock but session is poisoned (cart_blocked=%s, cb_open=%s) — NOT re-queuing",
                        item_id, session_poisoned, cb_open,
                    )
                else:
                    cooldown = self._compute_test_loop_cooldown()
                    if cooldown > 0:
                        logger.info(
                            "[MANAGER] Item %s still in stock — sleeping %.0fs before re-queue (anti-detection cooldown)",
                            item_id, cooldown,
                        )
                        next_cycle_at = time.strftime("%H:%M:%S", time.localtime(time.time() + cooldown))
                        self._status_cb(
                            f"[MANAGER] Test loop cooldown — {int(cooldown)}s before next cycle "
                            f"(next at {next_cycle_at}, set WALMART_TEST_LOOP_COOLDOWN=off to disable)"
                        )

                        # Tick every 30s so the log doesn't go silent for 3 minutes
                        TICK_INTERVAL = 30.0
                        deadline = time.monotonic() + cooldown
                        while True:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                break
                            sleep_for = min(TICK_INTERVAL, remaining)
                            await asyncio.sleep(sleep_for)
                            remaining_after = max(0.0, deadline - time.monotonic())
                            if remaining_after > 0:
                                self._status_cb(
                                    f"[MANAGER] Cooldown: {int(remaining_after)}s remaining "
                                    f"(next cycle at {next_cycle_at})"
                                )

                        # Re-check in-stock state after cooldown — item may have gone OOS,
                        # session may have been poisoned by an unrelated event, etc.
                        stock_states = self._monitor.get_stock_states()
                        cb_open = time.monotonic() < getattr(self._monitor, "_monitor_circuit_open_until", 0.0)
                        if not stock_states.get(item_id, {}).get("in_stock", False):
                            logger.info("[MANAGER] Item %s no longer in stock after cooldown — skipping re-queue", item_id)
                            return
                        if cb_open:
                            logger.warning("[MANAGER] Circuit breaker tripped during cooldown — skipping re-queue")
                            return

                    logger.info("[MANAGER] Item %s still in stock after purchase — re-queuing", item_id)
                    self._on_in_stock_signal(
                        item_id=item_id,
                        offer_id=None,
                        name=stock_states.get(item_id, {}).get("name", item_id),
                        price=stock_states.get(item_id, {}).get("price"),
                        order_limit=self._last_order_limit.get(item_id),
                    )

    def _compute_test_loop_cooldown(self) -> float:
        """Return the inter-cycle cooldown for test-mode re-queues, in seconds.

        Default: random 90-180s. Configurable via WALMART_TEST_LOOP_COOLDOWN env var:
          - "off" / "0" / "none" → 0 (no cooldown, legacy behavior)
          - "<int>"              → fixed N seconds
          - anything else        → default range
        """
        raw = (os.environ.get("WALMART_TEST_LOOP_COOLDOWN") or "").strip().lower()
        if raw in ("off", "0", "none", "false", "no"):
            return 0.0
        if raw.isdigit():
            return float(raw)
        # Default: human-realistic gap between repeat purchases of the same item
        return random.uniform(90.0, 180.0)

    # ------------------------------------------------------------------
    # Stock monitor health check
    # ------------------------------------------------------------------

    def _is_monitor_healthy(self) -> bool:
        """Return True if the stock monitor's internal run flag is set.

        Uses the monitor's public is_healthy() rather than reaching into
        `_running_lock` + `_running` private attrs — those are an
        implementation detail and could be renamed.
        """
        return self._monitor.is_healthy()

    # ------------------------------------------------------------------
    # Circuit breaker
    # ------------------------------------------------------------------

    def _check_circuit_breaker(self):
        """Open the circuit breaker if too many consecutive failures."""
        if self._consecutive_failures >= CIRCUIT_BREAKER_FAILURES:
            self._circuit_open_until = time.monotonic() + CIRCUIT_BREAKER_PAUSE
            self._consecutive_failures = 0
            self._status_cb(
                f"[MANAGER] Circuit breaker open — pausing {CIRCUIT_BREAKER_PAUSE}s "
                f"after {CIRCUIT_BREAKER_FAILURES} failures"
            )
            logger.warning("[MANAGER] Circuit breaker opened for %ds", CIRCUIT_BREAKER_PAUSE)

    # ------------------------------------------------------------------
    # Activity log
    # ------------------------------------------------------------------

    def _log_activity(self, message: str):
        entry = {"time": time.strftime("%H:%M:%S"), "message": message}
        with self._activity_lock:
            self._activity_log.append(entry)
            if len(self._activity_log) > 200:
                self._activity_log = self._activity_log[-200:]
        # Also log to Walmart's centralized logger
        walmart_logger.log_activity(message, category="MANAGER")

    def get_activity_log(self) -> list[dict]:
        with self._activity_lock:
            return list(self._activity_log)

    # ------------------------------------------------------------------
    # Status snapshot (for dashboard)
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        with self._lock:
            states = dict(self._state)

        circuit_open = time.monotonic() < self._circuit_open_until
        products = get_enabled_products()

        stock_states = self._monitor.get_stock_states()

        product_status = []
        for p in products:
            iid = p["item_id"]
            ss = stock_states.get(iid, {})
            product_status.append({
                "item_id": iid,
                "name": p.get("name", "Unknown"),
                "priority": p.get("priority", 999),
                "state": states.get(iid, PurchaseState.MONITORING),
                "in_stock": ss.get("in_stock", False),
                "last_checked": ss.get("last_checked"),
            })

        return {
            "running": self._running,
            "circuit_open": circuit_open,
            "circuit_open_until": max(0, int(self._circuit_open_until - time.monotonic())),
            "consecutive_failures": self._consecutive_failures,
            "products": product_status,
        }


# ---------------------------------------------------------------------------
# Standalone runner (for testing outside the dashboard)
# ---------------------------------------------------------------------------

async def _main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    email = os.environ.get("WALMART_EMAIL", "")
    password = os.environ.get("WALMART_PASSWORD", "")

    if not email or not password:
        print("Set WALMART_EMAIL and WALMART_PASSWORD environment variables to run.")
        return

    def on_status(msg):
        print(msg)

    manager = WalmartPurchaseManager(status_callback=on_status)
    try:
        await manager.start()
        # Run indefinitely
        while True:
            await asyncio.sleep(60)
    except KeyboardInterrupt:
        pass
    finally:
        await manager.stop()


if __name__ == "__main__":
    asyncio.run(_main())

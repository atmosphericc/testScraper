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
import threading
import time
from typing import Callable, Optional

from .config import (
    CIRCUIT_BREAKER_FAILURES,
    CIRCUIT_BREAKER_PAUSE,
    get_enabled_products,
)
from .proxy_manager import ProxyManager
from .session_manager import WalmartSessionManager
from .stock_monitor import WalmartStockMonitor
from .purchase_executor import WalmartPurchaseExecutor

logger = logging.getLogger(__name__)


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

        # Open Tab 2 now that Tab 1 has warm cookies — much less likely to hit /blocked
        await self._session.open_checkout_tab()

        # Start cookie harvester — keeps _px3 fresh for proxy workers every 30s
        self._session.start_harvester(self._loop)

        # Warmup is complete — purchases are now allowed
        self._warmup_done = True

        # Start stock monitor
        self._monitor.start()
        self._status_cb("[MANAGER] Walmart bot running — monitoring stock")
        logger.debug("[MANAGER] Walmart bot started, monitoring %d product(s)", len(products))

    async def stop(self):
        """Gracefully stop the bot."""
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
        with self._lock:
            current_states = dict(self._state)
            in_stock_ids = set(self._in_stock_ids) if hasattr(self, '_in_stock_ids') else {item_id}
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
                # Mark the override target as PURCHASING
                with self._lock:
                    self._state[item_id] = PurchaseState.PURCHASING

        item_url = f"https://www.walmart.com/ip/x/{item_id}"
        for p in products:
            if p["item_id"] == item_id:
                item_url = p.get("url", item_url)
                break

        # Schedule on the purchase event loop using the captured reference
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._run_purchase(item_id, item_url, name),
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

    async def _run_purchase(self, item_id: str, item_url: str, name: str):
        """Run a full purchase attempt, managing proxy + session warming."""
        checkout_proxy = None
        try:
            # Acquire a sticky checkout proxy
            checkout_proxy = self._proxy_manager.acquire_checkout_proxy(timeout=15.0)
            if checkout_proxy is None and self._proxy_manager.has_checkout_proxies():
                logger.warning("[MANAGER] No checkout proxy available after %ss timeout — proceeding without proxy", 15.0)
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
                login_ok = await self._session.login(
                    os.environ.get("WALMART_EMAIL", ""),
                    os.environ.get("WALMART_PASSWORD", ""),
                )
                if not login_ok:
                    self._login_ok = False
                    raise RuntimeError("Browser restarted but re-login failed")
                # Re-wire the new page into the monitor
                self._monitor.set_page(self._session.get_checkout_page())

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

            # Pause stock monitor during purchase — its rapid fetch() calls on Tab 1
            # contaminate the shared _px3 cookie with bot-like behavioral signals,
            # causing the checkout tab (Tab 2) to get /blocked on cart navigation.
            self._monitor.pause()
            self._status_cb("[MANAGER] Stock monitor paused for purchase")
            logger.info("[MANAGER] Stock monitor paused — protecting _px3 for checkout")

            # Let the browser settle for a moment after pausing fetch() spam,
            # then refresh cookies so Tab 2 starts with a clean _px3
            await asyncio.sleep(1.5)
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
            result = await executor.purchase(item_id=item_id, item_url=item_url)

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
            # Resume stock monitor regardless of purchase outcome
            if self._monitor.is_paused:
                self._monitor.resume()
                self._status_cb("[MANAGER] Stock monitor resumed")
                logger.info("[MANAGER] Stock monitor resumed after purchase attempt")

            if checkout_proxy:
                self._proxy_manager.release_checkout_proxy(checkout_proxy)
            # Reset state to MONITORING after a delay so we can try again on next restock
            await asyncio.sleep(5)
            with self._lock:
                current = self._state.get(item_id)
                if current == PurchaseState.FAILED:
                    self._cooldown_until[item_id] = time.monotonic() + 30
                self._state[item_id] = PurchaseState.MONITORING

    # ------------------------------------------------------------------
    # Stock monitor health check
    # ------------------------------------------------------------------

    def _is_monitor_healthy(self) -> bool:
        """Return True if the stock monitor's internal run flag is set."""
        with self._monitor._running_lock:
            return self._monitor._running

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

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
    CARD_CVV,
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
        self._status_cb = status_callback or (lambda msg: None)

        self._proxy_manager = ProxyManager()
        self._session = WalmartSessionManager(status_callback=self._status_cb)
        self._monitor = WalmartStockMonitor(
            proxy_manager=self._proxy_manager,
            on_in_stock=self._on_in_stock_signal,
            status_callback=self._status_cb,
        )

        # State
        self._lock = threading.Lock()
        self._state: dict[str, str] = {}   # item_id → PurchaseState
        self._in_stock_ids: set[str] = set()  # items currently known in-stock
        self._consecutive_failures = 0
        self._circuit_open_until: float = 0.0
        self._cooldown_until: dict[str, float] = {}
        self._running = False
        self._login_ok = False   # set True after successful login in start()

        # Async event loop for purchase tasks (separate from monitor's loop)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None

        # Recent activity log for dashboard
        self._activity_log: list[dict] = []
        self._activity_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Startup / shutdown
    # ------------------------------------------------------------------

    async def start(self, email: str, password: str):
        """
        Start the Walmart bot:
          1. Launch browser, restore/login session
          2. Warm session on configured products
          3. Start stock monitor
        """
        self._running = True
        self._status_cb("[MANAGER] Starting Walmart purchase manager...")

        # Start dedicated event loop thread for purchase tasks.
        # Loop must be running before the stock monitor starts so _on_in_stock_signal
        # can safely call run_coroutine_threadsafe.
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_event_loop,
            daemon=True,
            name="WalmartPurchaseLoop",
        )
        self._loop_thread.start()
        # Give the loop a moment to enter run_forever() before we proceed
        await asyncio.sleep(0.1)

        # Start browser session
        await self._session.start()

        # Validate or perform login
        session_ok = await self._session.validate_session()
        if not session_ok:
            self._status_cb("[MANAGER] Logging in to Walmart...")
            login_ok = await self._session.login(email, password)
            if not login_ok:
                self._status_cb(
                    "[MANAGER] Login failed — stock monitor will still run but "
                    "purchases will not be attempted. Check WALMART_EMAIL / WALMART_PASSWORD."
                )
                logger.error("[MANAGER] Login failed — running in monitor-only mode")
                # Still start the monitor so we can see stock status in the dashboard,
                # but set a flag so _on_in_stock_signal skips purchase attempts.
                self._login_ok = False
                self._monitor.start()
                return
        self._login_ok = True

        # Warm session on products
        products = get_enabled_products()
        if products:
            item_ids = [p["item_id"] for p in products]
            await self._session.warm_session(item_ids)

        # Start stock monitor
        self._monitor.start()
        self._status_cb("[MANAGER] Walmart bot running — monitoring stock")
        logger.info("[MANAGER] Walmart bot started, monitoring %d product(s)", len(products))

    async def stop(self):
        """Gracefully stop the bot."""
        self._running = False
        self._monitor.stop()
        await self._session.stop()
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._status_cb("[MANAGER] Walmart bot stopped")
        logger.info("[MANAGER] Bot stopped")

    def _run_event_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

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
        logger.info("[MANAGER] In-stock signal for %s — scheduling purchase", item_id)

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
                logger.info(
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

            # Re-warm session if _px3 is stale
            if self._session.needs_rewarm():
                await self._session.warm_session([item_id])

            # Stock monitor health check — restart if dead
            if not self._is_monitor_healthy():
                logger.critical("[MANAGER] Stock monitor is dead — attempting restart")
                try:
                    self._monitor.stop()  # now blocks until thread exits (up to 10s)
                    self._monitor.start()
                    logger.info("[MANAGER] Stock monitor restarted successfully")
                except Exception as _me:
                    logger.error("[MANAGER] Failed to restart stock monitor: %s", _me)

            # Run purchase
            page = self._session.get_page()
            if not page:
                raise RuntimeError("No browser page available")

            executor = WalmartPurchaseExecutor(page, self._status_cb)
            result = await executor.purchase(item_id=item_id, item_url=item_url)

            with self._lock:
                if result.success:
                    self._state[item_id] = PurchaseState.SUCCESS
                    self._consecutive_failures = 0
                    self._circuit_open_until = 0.0
                    self._log_activity(f"PURCHASE SUCCESS: {name} — Order #{result.order_id}")
                    logger.info("[MANAGER] Purchase success: %s — %s", name, result.order_id)
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
            if checkout_proxy:
                self._proxy_manager.release_checkout_proxy(checkout_proxy)
            # Reset state to MONITORING after a delay so we can try again on next restock
            await asyncio.sleep(5)
            with self._lock:
                current = self._state.get(item_id)
                if current == PurchaseState.FAILED:
                    self._cooldown_until[item_id] = time.monotonic() + 30
                elif current == PurchaseState.SUCCESS:
                    self._cooldown_until[item_id] = time.monotonic() + 3600  # 1hr after success
                if current not in (PurchaseState.SUCCESS,):
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

        product_status = []
        for p in products:
            iid = p["item_id"]
            product_status.append({
                "item_id": iid,
                "name": p.get("name", "Unknown"),
                "priority": p.get("priority", 999),
                "state": states.get(iid, PurchaseState.MONITORING),
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
    import os
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
        await manager.start(email=email, password=password)
        # Run indefinitely
        while True:
            await asyncio.sleep(60)
    except KeyboardInterrupt:
        pass
    finally:
        await manager.stop()


if __name__ == "__main__":
    asyncio.run(_main())

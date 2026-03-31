"""
Walmart stock monitor — wraps stock_check.py with proxy rotation, cookie refresh,
jittered polling loop, and an on_in_stock callback.

Does NOT rewrite stock_check.py — imports and reuses its logic directly.
"""

import asyncio
import json
import logging
import random
import requests
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .config import (
    CHECK_INTERVAL_MIN,
    CHECK_INTERVAL_MAX,
    COOKIES_FILE,
    GRAPHQL_HASH,
    get_enabled_products,
)
from .proxy_manager import ProxyManager

# Reuse existing stock_check.py logic
from .stock_check import fetch_item, parse_item, HEADERS, build_body
from .session_manager import _cookie_file_lock

logger = logging.getLogger(__name__)

# How often to refresh cookies from disk (seconds)
COOKIE_REFRESH_INTERVAL = 300


def _format_proxy(proxy_str: Optional[str]) -> Optional[dict]:
    """
    Convert a proxy string like 'http://user:pass@host:port' or 'host:port'
    into a requests-compatible proxies dict.
    """
    if not proxy_str:
        return None
    if "://" not in proxy_str:
        proxy_str = f"http://{proxy_str}"
    return {"http": proxy_str, "https": proxy_str}


class WalmartStockMonitor:
    """
    Polls Walmart product availability via the GraphQL API.

    Usage:
        def on_stock(item_id, offer_id, name, price):
            print(f"IN STOCK: {name} @ ${price}")

        monitor = WalmartStockMonitor(on_in_stock=on_stock)
        monitor.start()   # launches background thread
        # ...
        monitor.stop()

    Or use directly in an asyncio context:
        await monitor.run_loop()
    """

    def __init__(
        self,
        proxy_manager: Optional[ProxyManager] = None,
        on_in_stock: Optional[Callable[[str, Optional[str], str, Optional[float]], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
    ):
        self._proxy_manager = proxy_manager
        self._on_in_stock = on_in_stock
        self._status_cb = status_callback or (lambda msg: None)
        self._running = False
        self._running_lock = threading.Lock()
        self._stop_event = threading.Event()   # threading.Event is safe across sync/async
        self._last_cookie_refresh: float = 0.0
        self._cookies: dict = self._load_cookies_from_disk()

        # Track which items are currently flagged as in-stock to avoid
        # repeated callbacks for the same restock event.
        self._in_stock_cache: dict[str, bool] = {}
        self._cache_lock = threading.Lock()

        # Track consecutive 400/404 errors to detect GraphQL hash expiry
        self._consecutive_hash_errors: int = 0
        self._hash_error_lock = threading.Lock()

        # Thread reference for clean join on stop()
        self._thread: Optional[threading.Thread] = None

        # Pause-until timestamp set when hash errors trigger a 300s backoff
        self._hash_error_pause_until: float = 0.0

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    def start(self):
        """Start the monitor loop in a background thread with its own event loop."""
        with self._running_lock:
            if self._running:
                logger.warning("[MONITOR] start() called while already running — ignoring")
                return
            self._running = True
            self._stop_event.clear()

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.run_loop())
            finally:
                loop.close()

        t = threading.Thread(target=_run, daemon=True, name="WalmartStockMonitor")
        self._thread = t
        t.start()
        logger.info("[MONITOR] Stock monitor started in background thread")

    def stop(self):
        with self._running_lock:
            self._running = False
        self._stop_event.set()
        # Capture thread reference before join — start() could overwrite self._thread
        # if called concurrently, so we join the specific thread we're stopping.
        thread_to_join = self._thread
        if thread_to_join and thread_to_join.is_alive():
            thread_to_join.join(timeout=10)  # wait up to 10s for clean exit
            if thread_to_join.is_alive():
                logger.warning("[MONITOR] Monitor thread did not exit within 10s")
        self._thread = None
        logger.info("[MONITOR] Stock monitor stopped")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run_loop(self):
        """Poll all enabled products in a jittered loop until stopped."""
        logger.info("[MONITOR] Starting polling loop")
        self._status_cb("[MONITOR] Stock monitor running")

        def _is_running() -> bool:
            with self._running_lock:
                return self._running

        while _is_running():
            products = get_enabled_products()
            if not products:
                self._status_cb("[MONITOR] No enabled products — waiting...")
                # Check stop event every second instead of sleeping the full 30s
                for _ in range(30):
                    if self._stop_event.is_set():
                        return
                    await asyncio.sleep(1)
                continue

            if time.monotonic() < self._hash_error_pause_until:
                remaining = int(self._hash_error_pause_until - time.monotonic())
                logger.info("[MONITOR] Hash error pause — %ds remaining", remaining)
                await asyncio.sleep(min(30, remaining))
                continue

            item_ids = [p["item_id"] for p in products]
            await self._check_batch(item_ids, products)

            if not _is_running():
                break

            interval = random.uniform(CHECK_INTERVAL_MIN, CHECK_INTERVAL_MAX)
            logger.debug("[MONITOR] Next check in %.1fs", interval)

            # Sleep in 0.5s increments so we can respond to stop quickly
            elapsed = 0.0
            while elapsed < interval:
                if self._stop_event.is_set():
                    logger.info("[MONITOR] Stop event received")
                    return
                await asyncio.sleep(0.5)
                elapsed += 0.5

        logger.info("[MONITOR] Polling loop exited")

    # ------------------------------------------------------------------
    # Batch check
    # ------------------------------------------------------------------

    async def _check_batch(self, item_ids: list[str], products: list[dict]):
        """Check availability of all item IDs concurrently."""
        # Refresh cookies from disk periodically
        now = time.monotonic()
        if now - self._last_cookie_refresh > COOKIE_REFRESH_INTERVAL:
            self._cookies = self._load_cookies_from_disk()
            self._last_cookie_refresh = now

        # Run all fetches concurrently via asyncio + executor
        loop = asyncio.get_running_loop()
        tasks = [
            loop.run_in_executor(None, self._fetch_and_parse, item_id)
            for item_id in item_ids
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for item_id, result in zip(item_ids, results):
            if isinstance(result, Exception):
                logger.warning("[MONITOR] Error checking %s: %s", item_id, result)
                continue
            if result is None:
                continue
            self._handle_result(result, products)

    def _fetch_and_parse(self, item_id: str) -> Optional[dict]:
        """Fetch + parse a single item. Runs in thread executor (synchronous)."""
        proxy_str = self._proxy_manager.get_monitor_proxy() if self._proxy_manager else None
        proxies = _format_proxy(proxy_str)

        try:
            with requests.Session() as session:
                if self._cookies:
                    session.cookies.update(self._cookies)
                if proxies:
                    session.proxies.update(proxies)

                raw = fetch_item(item_id, session)

                if "error" in raw:
                    # Track consecutive 400/404 errors as a GraphQL hash expiry signal
                    if raw.get("error") in (400, 404):
                        with self._hash_error_lock:
                            self._consecutive_hash_errors += 1
                            should_pause = self._consecutive_hash_errors >= 5
                            if should_pause:
                                self._consecutive_hash_errors = 0
                                self._hash_error_pause_until = time.monotonic() + 300
                        logger.warning(
                            "[MONITOR] GraphQL hash may be expired for %s (HTTP %s). "
                            "Update GRAPHQL_HASH in config.py.",
                            item_id, raw["error"]
                        )
                        if should_pause:
                            logger.critical("[MONITOR] GRAPHQL_HASH likely expired — pausing 300s")
                    if proxy_str and self._proxy_manager:
                        self._proxy_manager.record_monitor_error(proxy_str)
                    return None

                # Successful response — reset consecutive error counter
                with self._hash_error_lock:
                    self._consecutive_hash_errors = 0
                if proxy_str and self._proxy_manager:
                    self._proxy_manager.record_monitor_success(proxy_str)

                parsed = parse_item(raw)

                # Enrich with offerId if present in raw data (needed for queue drops)
                if parsed and "error" not in parsed:
                    offer_id = self._extract_offer_id(raw.get("data", {}), item_id)
                    parsed["offer_id"] = offer_id

                return parsed

        except Exception as e:
            if proxy_str and self._proxy_manager:
                self._proxy_manager.mark_failed(proxy_str)
            logger.warning("[MONITOR] Fetch error for %s: %s", item_id, e)
            return None

    def _handle_result(self, result: dict, products: list[dict]):
        """Process a parsed item result and fire on_in_stock if needed."""
        item_id = result.get("item_id")
        if not item_id:
            return

        # Find max_price from config
        max_price = None
        for p in products:
            if p["item_id"] == item_id:
                max_price = p.get("max_price")
                break

        is_in_stock = result.get("in_stock", False)
        price = result.get("price")
        name = result.get("name", "Unknown")
        walmart_direct = result.get("walmart_direct", False)
        offer_id = result.get("offer_id")

        # Only consider Walmart.com direct sales
        if not walmart_direct:
            logger.debug("[MONITOR] %s — skipping third-party seller", item_id)
            with self._cache_lock:
                self._in_stock_cache[item_id] = False
            return

        # Price guard
        if max_price and price and price > max_price:
            logger.info("[MONITOR] %s — price $%.2f exceeds max $%.2f", item_id, price, max_price)
            with self._cache_lock:
                self._in_stock_cache[item_id] = False
            return

        with self._cache_lock:
            was_in_stock = self._in_stock_cache.get(item_id, False)
            self._in_stock_cache[item_id] = is_in_stock

        # Fire callback outside lock to avoid deadlock
        if is_in_stock:
            price_str = f"${price:.2f}" if price is not None else "price unknown"
            self._status_cb(f"[MONITOR] IN STOCK: {name} @ {price_str}")
            logger.info("[MONITOR] IN STOCK: %s (%s) @ $%s", name, item_id, price)
            if not was_in_stock and self._on_in_stock:
                self._on_in_stock(item_id, offer_id, name, price)
        else:
            if was_in_stock:
                logger.info("[MONITOR] Out of stock: %s (%s)", name, item_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_cookies_from_disk(self) -> dict:
        """Load cookies from walmart-profile/cookies.json (thread-safe via shared lock)."""
        path = Path(COOKIES_FILE)
        if not path.exists():
            return {}
        try:
            with _cookie_file_lock:
                with open(path, "r") as f:
                    cookie_list = json.load(f)
            # Convert list-of-dicts to flat dict for requests.Session
            if isinstance(cookie_list, list):
                return {c["name"]: c["value"] for c in cookie_list if "name" in c}
            if isinstance(cookie_list, dict):
                return cookie_list
        except Exception as e:
            logger.warning("[MONITOR] Could not load cookies: %s", e)
        return {}

    def _extract_offer_id(self, data: dict, item_id: str) -> Optional[str]:
        """
        Extract the Walmart Offer ID (OID) from the GraphQL response.
        The OID is the seller-specific variant identifier used for queue drops.
        """
        try:
            # data = result["data"] from fetch_item; actual content is under data["data"]
            modules = data.get("data", {}).get("contentLayout", {}).get("modules", [])
            for module in modules:
                if module.get("type") != "SoftBundles":
                    continue
                products = module.get("configs", {}).get("products", [])
                for product in products:
                    if product.get("usItemId") == item_id:
                        return product.get("offerId")
        except Exception as e:
            logger.warning("[MONITOR] offer_id extraction failed for %s: %s", item_id, e)
        return None

    # ------------------------------------------------------------------
    # One-shot check (useful for testing)
    # ------------------------------------------------------------------

    def is_healthy(self) -> bool:
        """Return True if the monitor loop is running."""
        with self._running_lock:
            return self._running

    def check_now(self) -> list[dict]:
        """
        Synchronous one-shot check of all enabled products.
        Returns list of parsed results. Useful for testing.
        """
        products = get_enabled_products()
        results = []
        for p in products:
            result = self._fetch_and_parse(p["item_id"])
            if result:
                results.append(result)
        return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    print("=== Walmart Stock Monitor — one-shot check ===")
    monitor = WalmartStockMonitor()
    results = monitor.check_now()
    for r in results:
        if "error" in r:
            print(f"  [{r['item_id']}] ERROR: {r['error']}")
        else:
            stock = "IN STOCK" if r["in_stock"] else "OUT OF STOCK"
            direct = "WALMART DIRECT" if r.get("walmart_direct") else "3RD PARTY"
            print(f"  [{stock}] [{direct}] {r['name']}")
            print(f"    Price: ${r['price']} | OID: {r.get('offer_id')} | Limit: {r.get('order_limit')}")

"""
Walmart stock monitor — staggered proxy worker architecture.

50 proxy workers, each offset by WORKER_CYCLE / n seconds at startup.
Every worker independently checks all configured products every WORKER_CYCLE
seconds. Net result: one full sweep every WORKER_CYCLE/n seconds (~0.3s with
50 proxies), matching Target's monitoring rate.
"""

import json
import logging
import requests
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .config import (
    COOKIES_FILE,
    get_enabled_products,
)
from .proxy_manager import ProxyManager

from .stock_check import fetch_item, parse_item
from .session_manager import _cookie_file_lock

logger = logging.getLogger(__name__)

# Each worker repeats every WORKER_CYCLE seconds.
# With n workers staggered, effective sweep rate = n / WORKER_CYCLE checks/sec.
WORKER_CYCLE = 15.0

# How often each worker refreshes cookies from disk
COOKIE_REFRESH_INTERVAL = 300


def _format_proxy(proxy_str: Optional[str]) -> Optional[dict]:
    if not proxy_str:
        return None
    if "://" not in proxy_str:
        proxy_str = f"http://{proxy_str}"
    return {"http": proxy_str, "https": proxy_str}


def _load_cookies_from_disk() -> dict:
    """Load session cookies from walmart-profile/cookies.json."""
    path = Path(COOKIES_FILE)
    if not path.exists():
        return {}
    try:
        with _cookie_file_lock:
            with open(path, "r") as f:
                cookie_list = json.load(f)
        if isinstance(cookie_list, list):
            return {c["name"]: c["value"] for c in cookie_list if "name" in c}
        if isinstance(cookie_list, dict):
            return cookie_list
    except Exception as e:
        logger.warning("[MONITOR] Could not load cookies: %s", e)
    return {}


class WalmartStockMonitor:
    """
    Staggered proxy worker stock monitor.

    Spawns one thread per proxy. Workers are offset at startup so they spread
    evenly across WORKER_CYCLE seconds. Each worker checks all configured
    products on every cycle using its assigned proxy.

    Usage:
        monitor = WalmartStockMonitor(proxy_manager=pm, on_in_stock=cb)
        monitor.start()
        monitor.stop()
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
        self._stop_event = threading.Event()
        self._worker_threads: list[threading.Thread] = []

        # Shared in-stock cache — prevents duplicate callbacks for the same restock
        self._in_stock_cache: dict[str, bool] = {}
        self._cache_lock = threading.Lock()

        # Hash error tracking — pause all workers if GraphQL hash is expired
        self._consecutive_hash_errors: int = 0
        self._hash_error_lock = threading.Lock()
        self._hash_error_pause_until: float = 0.0

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    def start(self):
        with self._running_lock:
            if self._running:
                logger.warning("[MONITOR] start() called while already running — ignoring")
                return
            self._running = True
            self._stop_event.clear()

        proxies = self._proxy_manager._monitor_proxies if self._proxy_manager else []

        if not proxies:
            # No proxies — fall back to a single no-proxy worker
            logger.warning("[MONITOR] No monitor proxies — running single worker without proxy")
            proxies = [None]

        n = len(proxies)
        stagger = WORKER_CYCLE / n

        self._worker_threads = []
        for i, proxy in enumerate(proxies):
            t = threading.Thread(
                target=self._worker,
                args=(proxy, i * stagger),
                daemon=True,
                name=f"WalmartMonitor-{i+1}",
            )
            t.start()
            self._worker_threads.append(t)

        rate = n / WORKER_CYCLE
        logger.info("[MONITOR] %d workers started — %.1f sweeps/sec", n, rate)
        self._status_cb(f"[MONITOR] {n} proxy workers started — {rate:.1f} sweeps/sec")

    def stop(self):
        with self._running_lock:
            self._running = False
        self._stop_event.set()

        for t in self._worker_threads:
            if t.is_alive():
                t.join(timeout=10)
                if t.is_alive():
                    logger.warning("[MONITOR] Worker %s did not exit in 10s", t.name)

        self._worker_threads = []
        logger.info("[MONITOR] Stock monitor stopped")

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _worker(self, proxy: Optional[str], initial_delay: float):
        """Single proxy worker — waits for stagger offset then loops every WORKER_CYCLE."""
        # Stagger startup
        if initial_delay > 0:
            self._stop_event.wait(timeout=initial_delay)
            if self._stop_event.is_set():
                return

        cookies = _load_cookies_from_disk()
        last_cookie_refresh = time.monotonic()

        while not self._stop_event.is_set():
            cycle_start = time.monotonic()

            # Refresh cookies periodically
            if cycle_start - last_cookie_refresh > COOKIE_REFRESH_INTERVAL:
                cookies = _load_cookies_from_disk()
                last_cookie_refresh = cycle_start

            # Respect global hash-error pause
            if time.monotonic() < self._hash_error_pause_until:
                self._stop_event.wait(timeout=5)
                continue

            products = get_enabled_products()
            if products:
                for p in products:
                    if self._stop_event.is_set():
                        return
                    self._check_one(p["item_id"], proxy, cookies, products)

            # Sleep only the remainder of WORKER_CYCLE so checks don't drift
            elapsed = time.monotonic() - cycle_start
            remaining = max(0.0, WORKER_CYCLE - elapsed)
            self._stop_event.wait(timeout=remaining)

    # ------------------------------------------------------------------
    # Single item check
    # ------------------------------------------------------------------

    def _check_one(
        self,
        item_id: str,
        proxy: Optional[str],
        cookies: dict,
        products: list[dict],
    ):
        proxies_dict = _format_proxy(proxy)
        try:
            with requests.Session() as session:
                if cookies:
                    session.cookies.update(cookies)
                if proxies_dict:
                    session.proxies.update(proxies_dict)

                raw = fetch_item(item_id, session)

                if "error" in raw:
                    if raw.get("error") in (400, 404):
                        with self._hash_error_lock:
                            self._consecutive_hash_errors += 1
                            if self._consecutive_hash_errors >= 5:
                                self._consecutive_hash_errors = 0
                                self._hash_error_pause_until = time.monotonic() + 300
                                logger.critical(
                                    "[MONITOR] GRAPHQL_HASH likely expired — all workers pausing 300s. "
                                    "Update GRAPHQL_HASH in walmart/config.py."
                                )
                    if proxy and self._proxy_manager:
                        self._proxy_manager.record_monitor_error(proxy)
                    return

                with self._hash_error_lock:
                    self._consecutive_hash_errors = 0
                if proxy and self._proxy_manager:
                    self._proxy_manager.record_monitor_success(proxy)

                parsed = parse_item(raw)
                if parsed and "error" not in parsed:
                    offer_id = self._extract_offer_id(raw.get("data", {}), item_id)
                    parsed["offer_id"] = offer_id
                    self._handle_result(parsed, products)

        except Exception as e:
            if proxy and self._proxy_manager:
                self._proxy_manager.mark_failed(proxy)
            logger.warning("[MONITOR] Fetch error for %s: %s", item_id, e)

    # ------------------------------------------------------------------
    # Result handler
    # ------------------------------------------------------------------

    def _handle_result(self, result: dict, products: list[dict]):
        item_id = result.get("item_id")
        if not item_id:
            return

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

        if not walmart_direct:
            logger.debug("[MONITOR] %s — skipping third-party seller", item_id)
            with self._cache_lock:
                self._in_stock_cache[item_id] = False
            return

        if max_price and price and price > max_price:
            logger.info("[MONITOR] %s — price $%.2f exceeds max $%.2f", item_id, price, max_price)
            with self._cache_lock:
                self._in_stock_cache[item_id] = False
            return

        with self._cache_lock:
            was_in_stock = self._in_stock_cache.get(item_id, False)
            self._in_stock_cache[item_id] = is_in_stock

        if is_in_stock:
            price_str = f"${price:.2f}" if price is not None else "price unknown"
            self._status_cb(f"[MONITOR] IN STOCK: {name} @ {price_str}")
            logger.info("[MONITOR] IN STOCK: %s (%s) @ %s", name, item_id, price_str)
            if not was_in_stock and self._on_in_stock:
                self._on_in_stock(item_id, offer_id, name, price)
        else:
            if was_in_stock:
                logger.info("[MONITOR] Out of stock: %s (%s)", name, item_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_offer_id(self, data: dict, item_id: str) -> Optional[str]:
        try:
            modules = data.get("data", {}).get("contentLayout", {}).get("modules", [])
            for module in modules:
                if module.get("type") != "SoftBundles":
                    continue
                for product in module.get("configs", {}).get("products", []):
                    if product.get("usItemId") == item_id:
                        return product.get("offerId")
        except Exception as e:
            logger.warning("[MONITOR] offer_id extraction failed for %s: %s", item_id, e)
        return None

    def is_healthy(self) -> bool:
        with self._running_lock:
            return self._running

    def check_now(self) -> list[dict]:
        """One-shot synchronous check of all enabled products. For testing."""
        cookies = _load_cookies_from_disk()
        products = get_enabled_products()
        results = []
        for p in products:
            proxy = self._proxy_manager.get_monitor_proxy() if self._proxy_manager else None
            proxies_dict = _format_proxy(proxy)
            try:
                with requests.Session() as session:
                    if cookies:
                        session.cookies.update(cookies)
                    if proxies_dict:
                        session.proxies.update(proxies_dict)
                    raw = fetch_item(p["item_id"], session)
                    parsed = parse_item(raw)
                    if parsed:
                        results.append(parsed)
            except Exception as e:
                logger.warning("[MONITOR] check_now error for %s: %s", p["item_id"], e)
        return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    from .config import get_enabled_products as _gep
    products = _gep()
    print("=" * 60)
    print("  WALMART STOCK MONITOR — one-shot check")
    print(f"  Checking {len(products)} configured product(s)")
    print("=" * 60)
    if not products:
        print("  No products configured in walmart/walmart_config.json")
        print("  Add products via the dashboard or edit the JSON directly.")
    else:
        for p in products:
            print(f"  {p['item_id']} — {p['name']}")
    print()

    monitor = WalmartStockMonitor()
    results = monitor.check_now()

    print("Results:")
    for r in results:
        if "error" in r:
            print(f"  [{r['item_id']}] ERROR: {r['error']}")
        else:
            stock  = "IN STOCK ✓" if r["in_stock"] else "OUT OF STOCK"
            direct = "WALMART DIRECT" if r.get("walmart_direct") else "3RD PARTY — SKIPPED"
            print(f"  [{stock}] [{direct}] {r['name']}")
            print(f"    Item ID   : {r['item_id']}")
            print(f"    Price     : ${r.get('price')}")
            print(f"    Status    : {r.get('availability')}")
            print(f"    showAtc   : {r.get('showAtc', 'n/a')}")
            print(f"    Seller    : {r.get('seller_name')} ({r.get('seller_id', '')[:8]}...)")
            print(f"    Offer ID  : {r.get('offer_id')}")
            print(f"    Order lim : {r.get('order_limit')}")
            print()

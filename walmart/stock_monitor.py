"""
Walmart stock monitor — staggered proxy worker architecture with live cookie harvesting.

The browser session (WalmartSessionManager) runs a background harvester that
refreshes Walmart cookies (_px3, bm_sv, auth) every 30s by navigating a real
page. Proxy workers pull from this live cookie store so every request carries
valid PerimeterX tokens.

Architecture:
  - 50 proxy workers, each staggered by WORKER_CYCLE / 50 seconds at startup
  - Each worker checks all configured products every WORKER_CYCLE seconds
  - Net result: each product checked ~3.3 times/second (50/15)
  - Browser harvester refreshes cookies every 30s — well within _px3's ~60s TTL
"""

import json
import logging
import requests
import threading
import time
from typing import Callable, Optional

from .config import (
    GRAPHQL_HASH,
    WALMART_SELLER_ID,
    get_enabled_products,
)
from .proxy_manager import ProxyManager

logger = logging.getLogger(__name__)

# Each worker repeats every WORKER_CYCLE seconds.
# With 50 workers staggered: 50 / 15 = 3.3 checks/sec per product.
WORKER_CYCLE = 15.0

HEADERS = {
    "accept": "application/json",
    "accept-language": "en-US",
    "content-type": "application/json",
    "x-o-bu": "WALMART-US",
    "x-o-mart": "B2C",
    "x-o-platform": "rweb",
    "x-o-segment": "oaoh",
    "x-apollo-operation-name": "ItemByIdBtf",
    "x-o-gql-query": "query ItemByIdBtf",
    "wm_mp": "true",
    "calltype": "CLIENT",
    "origin": "https://www.walmart.com",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


def _format_proxy(proxy_str: Optional[str]) -> Optional[dict]:
    if not proxy_str:
        return None
    if "://" not in proxy_str:
        proxy_str = f"http://{proxy_str}"
    return {"http": proxy_str, "https": proxy_str}


def _build_body(item_id: str) -> dict:
    return {
        "variables": {
            "isMobile": False,
            "layout": ["itemPageThreeGridDesktop2"],
            "channel": "WWW",
            "version": "v1",
            "postProcessingVersion": 1,
            "p13nCls": {
                "pageId": item_id,
                "skipPtcFetch": True,
                "p13NCallType": "BTF",
            },
            "fetchP13N": True,
            "fMrkDscrp": False,
            "pageType": "ItemPageGlobalDesktop",
            "fIdml": False,
            "fRev": False,
            "iId": item_id,
            "bbe": True,
            "fSId": True,
            "eSb": True,
            "enableDetailedBeacon": False,
            "enableMultiSave": False,
            "enableClickTrackingURL": False,
            "eCc": True,
            "fIdmlOrMrkDscrp": False,
            "tenant": "WM_GLASS",
            "epsv": True,
            "enableRxDrugScheduleModal": False,
            "enablePromotionMessages": False,
            "enableSignInToSeePrice": False,
            "enableOptimisticWeightUpdate": False,
        }
    }


class WalmartStockMonitor:
    """
    Staggered proxy worker stock monitor with live cookie harvesting.

    Spawns one thread per proxy, staggered across WORKER_CYCLE seconds.
    Each worker fetches fresh cookies from the session's live cookie store
    (populated every 30s by the browser harvester) before each cycle.

    Usage:
        monitor = WalmartStockMonitor(
            proxy_manager=pm,
            session=session_manager,
            on_in_stock=cb,
        )
        monitor.start()
        monitor.stop()
    """

    def __init__(
        self,
        proxy_manager: Optional[ProxyManager] = None,
        session=None,  # WalmartSessionManager — provides get_monitoring_cookies()
        on_in_stock: Optional[Callable[[str, Optional[str], str, Optional[float]], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        # legacy param kept for compatibility
        page=None,
    ):
        self._proxy_manager = proxy_manager
        self._session = session
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
        logger.info("[MONITOR] %d workers started — %.1f checks/sec per product", n, rate)
        self._status_cb(f"[MONITOR] {n} proxy workers — {rate:.1f} checks/sec per product")

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

    def set_page(self, page):
        """No-op — kept for call-site compatibility."""
        pass

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _worker(self, proxy: Optional[str], initial_delay: float):
        """Single proxy worker — waits for stagger offset then loops every WORKER_CYCLE."""
        if initial_delay > 0:
            self._stop_event.wait(timeout=initial_delay)
            if self._stop_event.is_set():
                return

        while not self._stop_event.is_set():
            cycle_start = time.monotonic()

            # Respect global hash-error pause
            if time.monotonic() < self._hash_error_pause_until:
                self._stop_event.wait(timeout=5)
                continue

            # Pull fresh cookies from the browser harvester
            cookies = self._session.get_monitoring_cookies() if self._session else {}

            products = get_enabled_products()
            if products:
                for p in products:
                    if self._stop_event.is_set():
                        return
                    self._check_one(p["item_id"], proxy, cookies, products)

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
        url = (
            f"https://www.walmart.com/orchestra/pdp/graphql/ItemByIdBtf"
            f"/{GRAPHQL_HASH}/ip/{item_id}"
        )
        headers = {**HEADERS, "x-o-item-id": item_id}

        try:
            with requests.Session() as session:
                if cookies:
                    session.cookies.update(cookies)
                if proxies_dict:
                    session.proxies.update(proxies_dict)

                resp = session.post(url, headers=headers, json=_build_body(item_id), timeout=10)

                if resp.status_code != 200:
                    if resp.status_code in (400, 403, 404):
                        with self._hash_error_lock:
                            self._consecutive_hash_errors += 1
                            if self._consecutive_hash_errors >= 5:
                                self._consecutive_hash_errors = 0
                                self._hash_error_pause_until = time.monotonic() + 300
                                logger.critical(
                                    "[MONITOR] GRAPHQL_HASH likely expired — pausing 300s. "
                                    "Update GRAPHQL_HASH in walmart/config.py."
                                )
                    if proxy and self._proxy_manager:
                        self._proxy_manager.record_monitor_error(proxy)
                    return

                with self._hash_error_lock:
                    self._consecutive_hash_errors = 0
                if proxy and self._proxy_manager:
                    self._proxy_manager.record_monitor_success(proxy)

                data = resp.json()
                parsed = self._parse(item_id, data)
                if parsed:
                    self._handle_result(parsed, products)

        except Exception as e:
            if proxy and self._proxy_manager:
                self._proxy_manager.mark_failed(proxy)
            logger.warning("[MONITOR] Fetch error for %s: %s", item_id, e)

    # ------------------------------------------------------------------
    # Parse GraphQL response
    # ------------------------------------------------------------------

    def _parse(self, item_id: str, data: dict) -> Optional[dict]:
        # Check top-level data.product first (some response shapes put it here)
        top = data.get("data", {})
        direct = top.get("product")
        if isinstance(direct, dict):
            result = self._extract_product(item_id, direct)
            if result:
                return result

        # Scan all contentLayout modules — Walmart A/B tests put availability data
        # in different module types (ItemTiles, SoftBundles, ItemPageAtf, etc.)
        # so we check every module rather than hard-coding a single type.
        modules = top.get("contentLayout", {}).get("modules", [])
        for module in modules:
            configs = module.get("configs", {})

            # configs.products — list form (SoftBundles, ItemTiles, …)
            for product in configs.get("products", []):
                result = self._extract_product(item_id, product)
                if result:
                    return result

            # configs.product — singular form used by some module types
            product = configs.get("product")
            if isinstance(product, dict):
                result = self._extract_product(item_id, product)
                if result:
                    return result

        return None

    def _extract_product(self, item_id: str, product: dict) -> Optional[dict]:
        """Extract availability info from a product dict if it matches item_id."""
        if product.get("usItemId") != item_id:
            return None
        seller_id = product.get("sellerId", "")
        seller_name = product.get("sellerName", "")
        is_direct = (
            seller_id.upper() == WALMART_SELLER_ID
            or seller_name.lower() == "walmart.com"
        )
        price = product.get("priceInfo", {}).get("currentPrice", {}).get("price")
        availability = product.get("availabilityStatus", "UNKNOWN")
        show_atc = product.get("showAtc", False)
        offer_id = product.get("offerId")
        return {
            "item_id": item_id,
            "name": product.get("name", "Unknown"),
            "price": price,
            "availability": availability,
            "show_atc": show_atc,
            "in_stock": availability in ("IN_STOCK", "PRE_ORDER_SELLABLE") and show_atc,
            "walmart_direct": is_direct,
            "seller_id": seller_id,
            "seller_name": seller_name,
            "order_limit": product.get("orderLimit"),
            "offer_id": offer_id,
        }

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

    def is_healthy(self) -> bool:
        with self._running_lock:
            return self._running

    def check_now(self) -> list[dict]:
        """One-shot synchronous check of all enabled products. For testing."""
        cookies = self._session.get_monitoring_cookies() if self._session else {}
        products = get_enabled_products()
        results = []
        for p in products:
            proxy = self._proxy_manager.get_monitor_proxy() if self._proxy_manager else None
            proxies_dict = _format_proxy(proxy)
            url = (
                f"https://www.walmart.com/orchestra/pdp/graphql/ItemByIdBtf"
                f"/{GRAPHQL_HASH}/ip/{p['item_id']}"
            )
            try:
                with requests.Session() as session:
                    if cookies:
                        session.cookies.update(cookies)
                    if proxies_dict:
                        session.proxies.update(proxies_dict)
                    resp = session.post(
                        url,
                        headers={**HEADERS, "x-o-item-id": p["item_id"]},
                        json=_build_body(p["item_id"]),
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        parsed = self._parse(p["item_id"], resp.json())
                        if parsed:
                            results.append(parsed)
            except Exception as e:
                logger.warning("[MONITOR] check_now error for %s: %s", p["item_id"], e)
        return results

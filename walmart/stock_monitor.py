"""
Walmart stock monitor — browser fetch architecture.

Uses fetch() inside the real Chrome browser tab to check all product PDPs
in parallel. Three dispatcher threads fire overlapping batches via
run_coroutine_threadsafe, achieving ~6-8 checks/sec/product with 0% blocks.

No proxies or external HTTP clients needed — the browser has valid PerimeterX
cookies and TLS fingerprint.
"""

import asyncio
import json
import logging
import random
import threading
import time
from typing import Callable, Optional

from .config import (
    WALMART_SELLER_ID,
    get_enabled_products,
    get_config,
    save_config,
)
from .logging_manager import get_walmart_logger, log_error, log_activity

logger = logging.getLogger(__name__)
walmart_logger = get_walmart_logger()

# Browser fetch loop fires every CHECK_INTERVAL seconds.
# Each fire fetches ALL products in parallel via Promise.allSettled inside the browser.
# NOTE: Randomized per-dispatcher to break determinism and avoid Akamai detection.
# We use exponential distribution to achieve maximum safe speed with natural variance.
NUM_DISPATCHERS = 3  # 3 dispatchers × 1 check/sec each = 3 checks/sec total (breaks machine-pattern signal)
CHECK_INTERVAL_AVG = 1.0  # Each dispatcher: 1 check/second (human-like frequency per dispatcher)


class WalmartStockMonitor:
    """
    Browser-fetch stock monitor — maximum speed with Akamai evasion.

    Runs fetch() inside the real Chrome tab (Tab 1) to check all products
    in parallel. Ten dispatcher threads fire with randomized exponential-distribution
    intervals to achieve ~10 checks/sec/product while evading Akamai detection.

    Each dispatcher individually looks human (1 check/sec), but combined they
    achieve high speed without deterministic patterns that trigger bot detection.

    Usage:
        monitor = WalmartStockMonitor(
            session=session_manager,
            on_in_stock=cb,
        )
        monitor.start()
        monitor.stop()
    """

    def __init__(
        self,
        proxy_manager=None,  # accepted for call-site compatibility, unused
        session=None,  # WalmartSessionManager — provides browser page + event loop
        on_in_stock: Optional[Callable[[str, Optional[str], str, Optional[float]], None]] = None,
        on_stock_change: Optional[Callable] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        page=None,  # accepted for call-site compatibility, unused
    ):
        self._session = session
        self._on_in_stock = on_in_stock
        self._on_stock_change = on_stock_change
        self._status_cb = status_callback or (lambda msg: None)

        self._running = False
        self._running_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()  # when set, dispatchers pause
        self._worker_threads: list[threading.Thread] = []

        # Shared in-stock cache — prevents duplicate callbacks for the same restock
        self._in_stock_cache: dict[str, bool] = {}
        self._last_checked: dict[str, float] = {}   # item_id → epoch timestamp
        self._cache_lock = threading.Lock()

        # Check counters for dashboard visibility
        self._total_checks: int = 0
        self._total_errors: int = 0
        self._checks_lock = threading.Lock()

        # Rate limit detection (monitor for 429 errors indicating we're too fast)
        # _rate_limit_lock guards both _rate_limit_hits and _rate_limit_backoff —
        # NUM_DISPATCHERS threads concurrently read/write these.
        self._rate_limit_hits: int = 0
        self._rate_limit_backoff: float = 0.0
        self._rate_limit_lock = threading.Lock()

        # Circuit breaker — pause monitor after repeated BLOCKED responses to
        # avoid accumulating Akamai detection signals during a hard block period.
        # _cb_lock guards _consecutive_blocked + _monitor_circuit_open_until +
        # _graphql_refresh_signaled. Without it, NUM_DISPATCHERS dispatchers race
        # on the increment/threshold check, producing duplicate "circuit breaker
        # tripped" logs and (more importantly) duplicate _px3 diagnostic calls
        # that each spawn a CDP coroutine.
        self._consecutive_blocked: int = 0
        self._monitor_circuit_open_until: float = 0.0
        self._graphql_refresh_signaled: float = 0.0  # epoch — dedupes refresh signals
        self._cb_lock = threading.Lock()
        _MONITOR_CIRCUIT_BREAKER_THRESHOLD = 5   # consecutive BLOCKED → trip
        _MONITOR_CIRCUIT_BREAKER_PAUSE = 120     # seconds to pause before retry
        self._MONITOR_CB_THRESHOLD = _MONITOR_CIRCUIT_BREAKER_THRESHOLD
        self._MONITOR_CB_PAUSE = _MONITOR_CIRCUIT_BREAKER_PAUSE
        self._GRAPHQL_REFRESH_COOLDOWN = 30.0     # seconds — only refresh hash once per 30s

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

        self._worker_threads = []

        t = threading.Thread(
            target=self._browser_fetch_loop,
            daemon=True,
            name="WalmartMonitor-BrowserFetch",
        )
        t.start()
        self._worker_threads.append(t)

        logger.info("[MONITOR] Started browser-fetch monitor (%.0f checks/sec/product)",
                     1.0 / CHECK_INTERVAL_AVG)
        self._status_cb("[MONITOR] Stock monitoring active — browser fetch mode")

        hb = threading.Thread(target=self._heartbeat, daemon=True, name="WalmartMonitor-HB")
        hb.start()
        self._worker_threads.append(hb)

    def pause(self):
        """Pause stock monitoring — dispatchers will idle until resume() is called."""
        self._pause_event.set()
        logger.info("[MONITOR] Stock monitoring paused")

    def resume(self):
        """Resume stock monitoring after a pause."""
        self._pause_event.clear()
        logger.info("[MONITOR] Stock monitoring resumed")

    @property
    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    def stop(self):
        with self._running_lock:
            self._running = False
        self._pause_event.clear()  # unblock any paused dispatchers
        self._stop_event.set()

        for t in self._worker_threads:
            if t.is_alive():
                t.join(timeout=10)
                if t.is_alive():
                    logger.warning("[MONITOR] Worker %s did not exit in 10s", t.name)

        self._worker_threads = []
        logger.debug("[MONITOR] Stock monitor stopped")

    def set_page(self, page):
        """No-op — kept for call-site compatibility."""
        pass

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def _heartbeat(self):
        """
        Broadcasts per-product stock status to the dashboard.

        - Every 15s: summary line showing check rate and per-product status.
        - On change: immediately logs which products went IN STOCK or OUT OF STOCK.
        """
        last_cache: dict = {}
        last_summary_checks = 0
        last_summary_time = time.monotonic()
        SUMMARY_INTERVAL = 15.0

        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=2.0)
            if self._stop_event.is_set():
                return

            with self._cache_lock:
                current_cache = dict(self._in_stock_cache)
            with self._checks_lock:
                total_checks = self._total_checks
                total_errors = self._total_errors

            products = get_enabled_products()
            names = {p["item_id"]: p.get("name", p["item_id"]) for p in products}

            # Broadcast state *changes* only
            for iid, now_in_stock in current_cache.items():
                was_in_stock = last_cache.get(iid)
                if was_in_stock is not None and now_in_stock != was_in_stock:
                    label = "IN STOCK" if now_in_stock else "OUT OF STOCK"
                    self._status_cb(f"[MONITOR] {label}: {names.get(iid, iid)}")
            last_cache = dict(current_cache)

            # Periodic summary
            now = time.monotonic()
            new_checks = total_checks - last_summary_checks
            if now - last_summary_time >= SUMMARY_INTERVAL and (new_checks > 0 or total_errors > 0):
                rate = new_checks / SUMMARY_INTERVAL
                in_count = sum(1 for v in current_cache.values() if v)
                oos_count = sum(1 for v in current_cache.values() if not v)
                unchecked = sum(1 for p in products if p["item_id"] not in current_cache)
                err_note = f" | {total_errors} errs" if total_errors > 0 else ""
                unc_note = f" | {unchecked} unchecked" if unchecked else ""
                self._status_cb(
                    f"[MONITOR] {rate:.1f}/s | {in_count} in stock | {oos_count} out of stock{unc_note}{err_note}"
                )
                last_summary_checks = total_checks
                last_summary_time = now

    # ------------------------------------------------------------------
    # Browser fetch loop — runs fetch() inside the real browser tab
    # ------------------------------------------------------------------

    _FETCH_JS_TEMPLATE = """
    (async () => {{
        const ids = {item_ids_json};
        const results = await Promise.allSettled(
            ids.map(async (id) => {{
                const t0 = performance.now();
                try {{
                    const resp = await fetch('/ip/' + id, {{
                        credentials: 'include',
                        headers: {{
                            'Accept': 'text/html',
                            'Accept-Language': 'en-US,en;q=0.9',
                            'Sec-Fetch-Site': 'same-origin',
                            'Sec-Fetch-Mode': 'navigate',
                            'Sec-Fetch-Dest': 'document',
                            'Referer': 'https://www.walmart.com/',
                            'Cache-Control': 'max-age=0'
                        }}
                    }});
                    const ms = performance.now() - t0;
                    if (resp.redirected && resp.url.includes('/blocked')) {{
                        return {{ item_id: id, error: 'BLOCKED', ms }};
                    }}
                    if (!resp.ok) {{
                        return {{ item_id: id, error: 'HTTP_' + resp.status, ms }};
                    }}
                    const html = await resp.text();
                    const m = html.match(/<script id="__NEXT_DATA__"[^>]*>(.*?)<\\/script>/);
                    if (!m) {{
                        return {{ item_id: id, error: 'NO_NEXT_DATA', ms }};
                    }}
                    const data = JSON.parse(m[1]);
                    const product = data?.props?.pageProps?.initialData?.data?.product;
                    if (!product) {{
                        return {{ item_id: id, error: 'NO_PRODUCT', ms }};
                    }}
                    if (!product.usItemId) product.usItemId = id;
                    return {{ item_id: id, product, ms }};
                }} catch (e) {{
                    return {{ item_id: id, error: e.message?.substring(0, 60) || 'unknown', ms: performance.now() - t0 }};
                }}
            }})
        );
        return results.map(r => r.status === 'fulfilled' ? r.value : {{ item_id: '?', error: r.reason?.message || 'rejected' }});
    }})()
    """

    def _browser_fetch_loop(self):
        """
        Spawns 3 dispatcher threads, each firing a full batch every 1s but
        staggered by 333ms — so results arrive ~3 times per second.
        """
        while not self._stop_event.is_set():
            if self._session and self._session._page and self._session._event_loop:
                break
            self._stop_event.wait(timeout=0.5)

        if self._stop_event.is_set():
            return

        logger.info(f"[MONITOR] Browser fetch loop started — {NUM_DISPATCHERS} dispatchers, targeting ~{NUM_DISPATCHERS * (1.0/CHECK_INTERVAL_AVG):.0f} checks/sec")

        dispatchers = []
        # Fully randomized per-dispatcher initial offset over [0, 1.0)s.
        # Prior version used `i * 0.1 + random(0, 0.5)` — the `i * 0.1` base
        # creates a structured 100ms phase offset between dispatchers that
        # appears as a deterministic stagger pattern in Akamai's server-side
        # request timing analysis across many sessions. Pure random delay
        # eliminates the structure while preserving the desired throughput.
        for i in range(NUM_DISPATCHERS):
            initial_delay = random.uniform(0.0, 1.0)
            t = threading.Thread(
                target=self._fetch_dispatcher,
                args=(initial_delay,),
                daemon=True,
                name=f"WalmartFetch-{i}",
            )
            t.start()
            dispatchers.append(t)

        self._stop_event.wait()
        for t in dispatchers:
            t.join(timeout=5)

    def _fetch_dispatcher(self, initial_delay: float):
        """
        Single dispatcher — fires stock checks with randomized intervals.

        Uses exponential distribution to achieve ~4 checks/sec average
        while breaking deterministic timing patterns (which Akamai detects).
        Inter-arrival times: mostly 150-400ms, occasionally 80ms or 500ms.
        """
        if initial_delay > 0:
            self._stop_event.wait(timeout=initial_delay)
            if self._stop_event.is_set():
                return

        while not self._stop_event.is_set():
            # Respect pause — idle until resumed or stopped
            while self._pause_event.is_set() and not self._stop_event.is_set():
                self._stop_event.wait(timeout=0.5)
            if self._stop_event.is_set():
                return

            cycle_start = time.monotonic()

            products = get_enabled_products()
            if not products:
                self._stop_event.wait(timeout=1.0)
                continue

            item_ids = [p["item_id"] for p in products]
            names = {p["item_id"]: (p.get("name") or p["item_id"])[:30] for p in products}

            try:
                results = self._run_browser_fetch(item_ids)
                if results:
                    in_stock_names = []
                    oos_names = []
                    errors = []
                    max_ms = 0

                    for r in results:
                        item_id = r.get("item_id", "?")
                        ms = r.get("ms", 0)
                        max_ms = max(max_ms, ms)
                        display = names.get(item_id, item_id)

                        if r.get("error"):
                            with self._checks_lock:
                                self._total_errors += 1
                            error_msg = r['error']
                            errors.append(f"{item_id}:{error_msg}")

                            # Count consecutive BLOCKED responses — trip monitor circuit breaker.
                            # Only log + emit status the FIRST time we trip per cooldown window,
                            # otherwise a single batch of N BLOCKED results spams the log N times.
                            if error_msg == "BLOCKED":
                                # All circuit-breaker bookkeeping under one lock — NUM_DISPATCHERS
                                # threads concurrently process BLOCKED results; without the lock
                                # the increment, the "== 1" diagnostic gate, and the "trip once"
                                # gate all race.
                                with self._cb_lock:
                                    self._consecutive_blocked += 1
                                    current_blocked = self._consecutive_blocked
                                    already_open = time.monotonic() < self._monitor_circuit_open_until
                                    is_first_blocked = current_blocked == 1
                                    should_trip = (
                                        current_blocked >= self._MONITOR_CB_THRESHOLD
                                        and not already_open
                                    )
                                    if should_trip:
                                        self._monitor_circuit_open_until = (
                                            time.monotonic() + self._MONITOR_CB_PAUSE
                                        )
                                # Diagnostic: on first BLOCKED, log the _px3 prefix
                                # to confirm whether the restore actually stuck or
                                # if Walmart re-rotated the cookie before the next
                                # fetch (server-side session invalidation). Run
                                # outside _cb_lock — _log_px3_state_on_block does
                                # a CDP round-trip and we do not want to hold the
                                # lock across that.
                                if is_first_blocked:
                                    self._log_px3_state_on_block()
                                if should_trip:
                                    self._status_cb(
                                        f"[MONITOR] Circuit breaker open — {current_blocked} consecutive BLOCKED "
                                        f"responses, pausing {self._MONITOR_CB_PAUSE}s"
                                    )
                                    logger.warning(
                                        "[MONITOR] Circuit breaker tripped after %d BLOCKED responses — pausing %ds",
                                        current_blocked, self._MONITOR_CB_PAUSE,
                                    )
                            else:
                                # Non-BLOCKED error resets the consecutive counter —
                                # still under the lock so it cannot interleave with
                                # the BLOCKED increment above.
                                with self._cb_lock:
                                    self._consecutive_blocked = 0

                            # Detect rate limiting (429 = too fast, backoff needed)
                            if error_msg == "HTTP_429":
                                with self._rate_limit_lock:
                                    self._rate_limit_hits += 1
                                    hits = self._rate_limit_hits
                                    backoff_already_set = (
                                        time.monotonic() < self._rate_limit_backoff
                                    )
                                    if hits >= 3 and not backoff_already_set:
                                        self._rate_limit_backoff = time.monotonic() + 30.0
                                        emit_warning = True
                                    else:
                                        emit_warning = False
                                if emit_warning:
                                    logger.warning(
                                        "[MONITOR] Rate limited (429) detected %dx — backing off for 30s",
                                        hits,
                                    )

                            # Detect GraphQL hash staleness (400 error pattern).
                            # Multiple products in the same batch can all 400 simultaneously —
                            # _trigger_graphql_hash_refresh dedupes so we only signal once
                            # per cooldown window.
                            if error_msg == "HTTP_400":
                                self._trigger_graphql_hash_refresh()
                            continue

                        product_data = r.get("product", {})
                        parsed = self._extract_product(item_id, product_data)
                        if parsed:
                            with self._checks_lock:
                                self._total_checks += 1
                            if parsed.get("in_stock") and parsed.get("walmart_direct"):
                                in_stock_names.append(display)
                            else:
                                oos_names.append(display)
                            self._handle_result(parsed, products)
                        else:
                            with self._checks_lock:
                                self._total_errors += 1
                            errors.append(f"{item_id}:PARSE_FAIL")

                    parts = []
                    if in_stock_names:
                        parts.append(f"IN STOCK: [{', '.join(in_stock_names)}]")
                    if oos_names:
                        parts.append(f"OUT OF STOCK: [{', '.join(oos_names)}]")
                    if errors:
                        parts.append(f"ERRORS: [{', '.join(errors)}]")
                    logger.info("[MONITOR] %s | %dms", " | ".join(parts), max_ms)

            except Exception as e:
                with self._checks_lock:
                    self._total_errors += 1
                logger.warning("[MONITOR] Browser fetch error: %s", e)

            # RANDOMIZED INTER-REQUEST DELAY (Akamai evasion)
            # Use exponential distribution to simulate natural human check frequency
            # Average: CHECK_INTERVAL_AVG (1.0s for 10 dispatchers), Range: 0.5-1.5s
            elapsed = time.monotonic() - cycle_start

            # Monitor circuit breaker — back off after repeated BLOCKED responses.
            # Snapshot the open-until value under the lock so the wait calculation
            # cannot race with another dispatcher trip-or-clearing it.
            with self._cb_lock:
                cb_open_until = self._monitor_circuit_open_until
            now = time.monotonic()
            if now < cb_open_until:
                remaining_cb = cb_open_until - now
                logger.warning("[MONITOR] Circuit breaker open — pausing %.0fs", remaining_cb)
                self._stop_event.wait(timeout=remaining_cb)
                # Reset the counter only once per cooldown window — without this
                # gate, all NUM_DISPATCHERS dispatchers wake from the wait and
                # each reset _consecutive_blocked, generating multiple "open"
                # log lines on the next trip. Stagger resumption with random
                # jitter so dispatchers don't all fire simultaneously after
                # cooldown (the synchronized burst would itself look like a bot
                # pattern to Akamai).
                with self._cb_lock:
                    if self._monitor_circuit_open_until == cb_open_until:
                        # We are the first to observe the cooldown ending.
                        self._consecutive_blocked = 0
                        self._monitor_circuit_open_until = 0.0
                self._stop_event.wait(timeout=random.uniform(0.0, 1.5))
                continue

            # Check if rate limited — back off if so
            with self._rate_limit_lock:
                rl_backoff = self._rate_limit_backoff
            now = time.monotonic()
            if now < rl_backoff:
                wait_time = max(0.0, rl_backoff - now)
                logger.warning(f"[MONITOR] Rate limit backoff: waiting {wait_time:.0f}s before resuming")
                self._stop_event.wait(timeout=wait_time)
                # Reset the hit counter after a backoff window so a subsequent
                # transient 429 doesn't immediately re-trip the 3-hit gate.
                with self._rate_limit_lock:
                    if self._rate_limit_backoff == rl_backoff:
                        self._rate_limit_hits = 0
                        self._rate_limit_backoff = 0.0
            else:
                # Normal operation: exponential variance
                interval = random.expovariate(1.0 / CHECK_INTERVAL_AVG)
                interval = max(0.50, min(1.50, interval))  # Clamp to 0.5-1.5s (500-1500ms)
                remaining = max(0.0, interval - elapsed)
                if remaining > 0:
                    self._stop_event.wait(timeout=remaining)

    def _run_browser_fetch(self, item_ids: list[str]) -> Optional[list[dict]]:
        """Schedule the fetch JS on the browser event loop and wait for result.

        Snapshots `_page` and `_event_loop` references once up front — the
        session manager clears `_page` on shutdown (session_manager.py:360,
        431), so without a snapshot, a dispatcher already past the None
        guard can hit `NoneType.evaluate(...)` inside the coroutine.
        """
        if not self._session:
            return None
        page = getattr(self._session, "_page", None)
        loop = getattr(self._session, "_event_loop", None)
        if page is None or loop is None or loop.is_closed():
            return None

        js = self._FETCH_JS_TEMPLATE.format(item_ids_json=json.dumps(item_ids))

        async def _do_fetch():
            return await page.evaluate(js, await_promise=True)

        try:
            future = asyncio.run_coroutine_threadsafe(_do_fetch(), loop)
        except RuntimeError as e:
            # Event loop closed between the is_closed() check and submission.
            logger.debug("[MONITOR] Browser fetch submit failed: %s", e)
            return None
        try:
            return future.result(timeout=15)
        except Exception as e:
            logger.warning("[MONITOR] Browser fetch future error: %s", e)
            return None

    # ------------------------------------------------------------------
    # Product data extraction
    # ------------------------------------------------------------------

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

    def _parse(self, item_id: str, data: dict) -> Optional[dict]:
        """Parse a GraphQL response (used by on_browser_graphql intercept)."""
        top = data.get("data", {})
        direct = top.get("product")
        if isinstance(direct, dict):
            result = self._extract_product(item_id, direct)
            if result:
                return result

        modules = top.get("contentLayout", {}).get("modules", [])
        for module in modules:
            configs = module.get("configs", {})
            for product in configs.get("products", []):
                result = self._extract_product(item_id, product)
                if result:
                    return result
            product = configs.get("product")
            if isinstance(product, dict):
                result = self._extract_product(item_id, product)
                if result:
                    return result

        return None

    # ------------------------------------------------------------------
    # Result handler
    # ------------------------------------------------------------------

    def _handle_result(self, result: dict, products: list[dict]):
        item_id = result.get("item_id")
        if not item_id:
            return

        max_price = None
        config_name = None
        for p in products:
            if p["item_id"] == item_id:
                max_price = p.get("max_price")
                config_name = p.get("name")
                break

        is_in_stock = result.get("in_stock", False)
        price = result.get("price")
        api_name = result.get("name", "Unknown")
        name = (config_name if config_name and config_name != "Unknown" else None) or api_name

        # Back-fill API name into config if config only has a placeholder
        if api_name and api_name != "Unknown" and (not config_name or config_name == "Unknown" or config_name == item_id):
            try:
                cfg = get_config()
                updated = False
                for p in cfg.get("products", []):
                    if p["item_id"] == item_id and p.get("name", "Unknown") in ("Unknown", item_id, ""):
                        p["name"] = api_name
                        updated = True
                        break
                if updated:
                    save_config(cfg)
                    logger.debug("[MONITOR] Updated product name in config: %s → %s", item_id, api_name)
            except Exception:
                pass

        walmart_direct = result.get("walmart_direct", False)
        offer_id = result.get("offer_id")

        if not walmart_direct:
            logger.debug("[MONITOR] %s — skipping third-party seller", item_id)
            with self._cache_lock:
                self._in_stock_cache[item_id] = False
            return

        if max_price and price and price > max_price:
            logger.debug("[MONITOR] %s — price $%.2f exceeds max $%.2f", item_id, price, max_price)
            with self._cache_lock:
                self._in_stock_cache[item_id] = False
            return

        # Compute the cache transition under a single critical section so the
        # "did I flip the state?" decision is atomic across dispatchers. Without
        # this, two dispatchers reporting the same restock both see was=False
        # before either writes True, and both fire the in-stock callback,
        # producing duplicate purchase attempts that the manager has to dedupe.
        with self._cache_lock:
            was_in_stock = self._in_stock_cache.get(item_id, False)
            self._in_stock_cache[item_id] = is_in_stock
            self._last_checked[item_id] = time.time()
            transitioned_to_in_stock = is_in_stock and not was_in_stock
            transitioned_to_oos = was_in_stock and not is_in_stock

        if is_in_stock:
            price_str = f"${price:.2f}" if price is not None else "price unknown"
            self._status_cb(f"[MONITOR] IN STOCK: {name} @ {price_str}")
            logger.warning("[MONITOR] IN STOCK: %s (%s) @ %s", name, item_id, price_str)
            if transitioned_to_in_stock:
                if self._on_in_stock:
                    self._on_in_stock(item_id, offer_id, name, price)
                if self._on_stock_change:
                    self._on_stock_change(item_id, True, price)
        else:
            if transitioned_to_oos:
                logger.debug("[MONITOR] Out of stock: %s (%s)", name, item_id)
                if self._on_stock_change:
                    self._on_stock_change(item_id, False, price)

    # ------------------------------------------------------------------
    # GraphQL hash refresh on staleness detection
    # ------------------------------------------------------------------

    def _log_px3_state_on_block(self):
        """Log _px3 prefix from the live cookie cache and from the actual
        browser cookie jar so we can tell whether the cookie restore stuck
        or whether Walmart re-rotated the cookie before this fetch.
        """
        if not self._session:
            return
        try:
            cached = "?"
            try:
                with self._session._live_cookies_lock:
                    cached_val = self._session._live_cookies.get("_px3", "")
                cached = (cached_val[:24] + "…") if cached_val else "<missing>"
            except Exception:
                pass

            # Snapshot the page+loop refs so a concurrent shutdown can't null
            # them between the check and the coroutine dispatch.
            page = getattr(self._session, "_page", None)
            loop = getattr(self._session, "_event_loop", None)
            jar_prefix = "?"
            if page is not None and loop is not None and not loop.is_closed():
                try:
                    from zendriver import cdp

                    async def _get_px3():
                        raw = await page.send(cdp.network.get_all_cookies())
                        for c in raw:
                            if c.name == "_px3":
                                return c.value
                        return None

                    fut = asyncio.run_coroutine_threadsafe(_get_px3(), loop)
                    val = fut.result(timeout=2)
                    jar_prefix = (val[:24] + "…") if val else "<missing>"
                except Exception as e:
                    jar_prefix = f"<err:{e}>"

            match = "match" if cached and jar_prefix and cached.split("…")[0] == jar_prefix.split("…")[0] else "DIVERGED"
            logger.warning(
                "[MONITOR] First BLOCKED — _px3 cache=%s jar=%s (%s)",
                cached, jar_prefix, match,
            )
        except Exception as e:
            logger.debug("[MONITOR] _log_px3_state_on_block failed: %s", e)

    def _trigger_graphql_hash_refresh(self):
        """
        Called when HTTP 400 is detected during stock checks.
        Signals the session manager to refresh the GraphQL hash by visiting
        a fresh product page, which will auto-discover the current hash via CDP.

        Deduplicated under _cb_lock — a batch of N products that all 400
        simultaneously will signal exactly once per cooldown window, instead
        of N times. Without dedupe a single failed hash produces N ERROR
        log lines plus N pointless flag-sets in 100ms.
        """
        if not self._session:
            logger.warning("[MONITOR] Cannot refresh GraphQL hash — no session manager")
            return

        now = time.monotonic()
        with self._cb_lock:
            if now < self._graphql_refresh_signaled + self._GRAPHQL_REFRESH_COOLDOWN:
                return
            self._graphql_refresh_signaled = now

        try:
            if hasattr(self._session, '_graphql_refresh_needed'):
                self._session._graphql_refresh_needed = True
                logger.error(
                    "[MONITOR] HTTP 400 detected — possible GraphQL hash staleness, "
                    "signaled session manager to refresh"
                )
            else:
                logger.error(
                    "[MONITOR] Session manager does not support hash refresh — "
                    "operator should restart app"
                )
        except Exception as e:
            logger.warning("[MONITOR] Failed to signal hash refresh: %s", e)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def on_browser_graphql(self, payload: dict):
        """
        Called by the session manager whenever the browser intercepts a GraphQL
        ItemByIdBtf/Atf response. Feeds into the same parse + handle pipeline.
        """
        item_id = payload.get("item_id")
        data = payload.get("data")
        if not item_id or not data:
            return
        try:
            products = get_enabled_products()
            parsed = self._parse(item_id, data)
            if parsed:
                self._handle_result(parsed, products)
                with self._checks_lock:
                    self._total_checks += 1
                status = "IN STOCK" if parsed.get("in_stock") else "OUT OF STOCK"
                name = parsed.get("name", item_id)
                price = parsed.get("price")
                price_str = f" @ ${price:.2f}" if price is not None else ""
                logger.info("[MONITOR] %s — %s%s", name, status, price_str)
            else:
                logger.warning("[MONITOR] No product data parsed for item %s", item_id)
                with self._checks_lock:
                    self._total_errors += 1
        except Exception as e:
            logger.warning("[MONITOR] Browser GraphQL parse error for %s: %s", item_id, e)

    def get_stock_states(self) -> dict:
        """Return {item_id: {in_stock, last_checked}} snapshot for dashboard."""
        with self._cache_lock:
            return {
                iid: {
                    "in_stock": self._in_stock_cache.get(iid, False),
                    "last_checked": self._last_checked.get(iid),
                }
                for iid in self._in_stock_cache
            }

    def is_healthy(self) -> bool:
        with self._running_lock:
            return self._running

"""
Walmart proxy manager — two-pool design.

Monitor pool  (rotating):  used for lightweight GraphQL stock checks.
Checkout pool (sticky):    one proxy locked per checkout session until released.

Proxies are loaded from config/proxyIps.json (same file Target uses, read-only).
"""

import json
import time
import threading
import logging
from collections import deque, defaultdict
from pathlib import Path
from typing import Optional

from .config import (
    MONITOR_PROXY_POOL_SIZE,
    CHECKOUT_PROXY_POOL_SIZE,
    PROXY_COOLDOWN_SECONDS,
    PROXY_ERROR_RATE_THRESHOLD,
)

logger = logging.getLogger(__name__)

_PROXY_FILE_PATHS = [
    "config/proxyIps.json",
    "../config/proxyIps.json",
]


def _load_proxies() -> list[str]:
    """Load proxy list from config/proxyIps.json. Returns list of proxy URL strings."""
    for path in _PROXY_FILE_PATHS:
        p = Path(path)
        if p.exists():
            try:
                with open(p, "r") as f:
                    data = json.load(f)
                proxies = data.get("proxies", [])
                if proxies:
                    logger.info(f"[PROXY] Loaded {len(proxies)} proxies from {path}")
                    return proxies
            except Exception as e:
                logger.warning(f"[PROXY] Failed to load {path}: {e}")
    logger.warning("[PROXY] No proxy file found — running without proxies")
    return []


class _ProxyStats:
    """Tracks request/error counts for one proxy IP.

    All mutating methods are guarded by an internal lock — monitor workers
    and the dispatcher concurrently call record_success/record_error/bench,
    and `total += 1` is not atomic in CPython for instance attributes.
    """

    def __init__(self):
        self.total = 0
        self.errors = 0
        self.benched_until: float = 0.0
        self._lock = threading.Lock()

    def record_success(self):
        with self._lock:
            self.total += 1

    def record_error(self):
        with self._lock:
            self.total += 1
            self.errors += 1

    def is_benched(self) -> bool:
        # Plain read of a float — atomic in CPython, no lock needed.
        return time.monotonic() < self.benched_until

    def bench(self, duration: float):
        # Plain write of a float — atomic in CPython.
        self.benched_until = time.monotonic() + duration

    def error_rate(self) -> float:
        with self._lock:
            total = self.total
            errors = self.errors
        if total < 10:
            return 0.0
        return errors / total


class ProxyManager:
    """
    Manages a monitor pool and a checkout pool from the shared proxy list.

    Usage:
        pm = ProxyManager()
        proxy = pm.get_monitor_proxy()         # rotating, may return None
        proxy = pm.acquire_checkout_proxy()    # sticky, blocks until available
        pm.release_checkout_proxy(proxy)
        pm.mark_blocked(proxy, 429)
        pm.mark_failed(proxy)
    """

    def __init__(self):
        all_proxies = _load_proxies()
        total = len(all_proxies)

        # All proxies are available for monitoring (staggered workers each own one).
        # A separate sticky subset is reserved for checkout sessions.
        checkout_count = min(CHECKOUT_PROXY_POOL_SIZE, total)

        self._monitor_proxies: list[str] = all_proxies          # all 50
        self._checkout_proxies: list[str] = all_proxies[:checkout_count]  # first 12

        logger.info(
            f"[PROXY] Monitor pool: {len(self._monitor_proxies)} | "
            f"Checkout pool: {len(self._checkout_proxies)}"
        )

        # Monitor pool state
        self._monitor_queue: deque[str] = deque(self._monitor_proxies)
        self._monitor_lock = threading.Lock()

        # Checkout pool state — semaphore-guarded
        self._checkout_available: list[str] = list(self._checkout_proxies)
        self._checkout_lock = threading.Lock()
        self._checkout_semaphore = threading.Semaphore(len(self._checkout_proxies) or 1)

        # Per-proxy stats — protected by _stats_lock because defaultdict's
        # create-if-missing is not atomic across threads; two concurrent
        # callers can each create a fresh _ProxyStats and silently discard
        # one of them.
        self._stats: dict[str, _ProxyStats] = defaultdict(_ProxyStats)
        self._stats_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Monitor pool — rotating
    # ------------------------------------------------------------------

    def _get_stats(self, proxy: str) -> _ProxyStats:
        """Thread-safe accessor for the per-proxy stats entry."""
        with self._stats_lock:
            return self._stats[proxy]

    def get_monitor_proxy(self) -> Optional[str]:
        """
        Return the next available monitor proxy (round-robin).
        Skips benched proxies. Returns None if no proxies are configured
        or all are benched.
        """
        if not self._monitor_proxies:
            return None

        with self._monitor_lock:
            for _ in range(len(self._monitor_proxies)):
                proxy = self._monitor_queue[0]
                self._monitor_queue.rotate(-1)
                stats = self._get_stats(proxy)
                if stats.is_benched():
                    continue
                if stats.error_rate() > PROXY_ERROR_RATE_THRESHOLD:
                    stats.bench(PROXY_COOLDOWN_SECONDS)
                    logger.warning(f"[PROXY] Monitor proxy benched (high error rate): {proxy}")
                    continue
                return proxy
        return None

    def record_monitor_success(self, proxy: str):
        self._get_stats(proxy).record_success()

    def record_monitor_error(self, proxy: str):
        self._get_stats(proxy).record_error()

    # ------------------------------------------------------------------
    # Checkout pool — sticky
    # ------------------------------------------------------------------

    def acquire_checkout_proxy(self, timeout: float = 30.0) -> Optional[str]:
        """
        Acquire a sticky checkout proxy. Blocks up to `timeout` seconds.
        Returns None if no checkout proxies are configured.
        """
        if not self._checkout_proxies:
            return None

        acquired = self._checkout_semaphore.acquire(timeout=timeout)
        if not acquired:
            logger.warning("[PROXY] Timed out waiting for a checkout proxy")
            return None  # semaphore was NOT acquired — no release needed

        # Semaphore acquired; must release it if we don't return a proxy
        with self._checkout_lock:
            for proxy in list(self._checkout_available):
                if not self._get_stats(proxy).is_benched():
                    self._checkout_available.remove(proxy)
                    logger.info(f"[PROXY] Checkout proxy acquired: {proxy}")
                    return proxy  # caller must call release_checkout_proxy()

        # All available proxies are benched — release semaphore so it doesn't leak
        self._checkout_semaphore.release()
        logger.warning("[PROXY] All checkout proxies are benched")
        return None

    def release_checkout_proxy(self, proxy: str):
        """Return a checkout proxy to the available pool.

        Idempotent: a double-release (common bug in caller's `finally` blocks)
        is detected via the `proxy not in available` gate and the semaphore is
        only released exactly once per actual acquisition. Calling release
        on a proxy not in the checkout pool is also a no-op.
        """
        if proxy not in self._checkout_proxies:
            logger.debug(f"[PROXY] release ignored — proxy not in checkout pool: {proxy}")
            return
        with self._checkout_lock:
            if proxy in self._checkout_available:
                # Already released — double-release attempt. Don't release
                # the semaphore again or its count will drift upward.
                logger.warning(f"[PROXY] Double release ignored for {proxy}")
                return
            self._checkout_available.append(proxy)
            self._checkout_semaphore.release()
            logger.info(f"[PROXY] Checkout proxy released: {proxy}")

    # ------------------------------------------------------------------
    # Failure handling (shared for both pools)
    # ------------------------------------------------------------------

    def has_checkout_proxies(self) -> bool:
        """Returns True if any checkout proxies are configured."""
        return bool(self._checkout_proxies)

    def mark_blocked(self, proxy: str, status_code: int = 403):
        """Bench a proxy after a 403/429 response."""
        stats = self._get_stats(proxy)
        stats.record_error()
        stats.bench(PROXY_COOLDOWN_SECONDS)
        logger.warning(f"[PROXY] Benched {PROXY_COOLDOWN_SECONDS}s (HTTP {status_code}): {proxy}")

    def mark_failed(self, proxy: str):
        """Mark a proxy as dead (connection refused/timeout) — bench for 10 min."""
        self._get_stats(proxy).bench(PROXY_COOLDOWN_SECONDS * 2)
        logger.warning(f"[PROXY] Benched {PROXY_COOLDOWN_SECONDS * 2}s (dead): {proxy}")

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def stats_summary(self) -> dict:
        # Snapshot under the lock to avoid RuntimeError if a concurrent
        # _get_stats inserts a new key during iteration.
        with self._stats_lock:
            items = list(self._stats.items())
        summary = {}
        for proxy, stats in items:
            summary[proxy] = {
                "total": stats.total,
                "errors": stats.errors,
                "error_rate": round(stats.error_rate(), 3),
                "benched": stats.is_benched(),
            }
        return summary

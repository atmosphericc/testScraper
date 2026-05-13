"""
Pre-flight validator: probe each proxy once through the same curl_cffi+forwarder
path the workers use, return the subset that returned a clean 200 + valid stock
data. Eliminates the "discovery-phase 403" cost of the resilient stack so the
worker pool starts with verified-clean IPs only.

Usage:
    from src.monitoring.proxy_preflight import preflight_validate
    verified_urls = await preflight_validate(proxy_urls, tcin="50270379")
    # Pass `verified_urls` to ResilientStockChecker instead of raw list.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time

from curl_cffi import requests as cffi

from src.proxy.local_forwarder import ForwarderPool

logger = logging.getLogger(__name__)

REDSKY_MODERN = (
    "https://redsky.target.com/redsky_aggregations/v1/web/"
    "product_fulfillment_and_variation_hierarchy_v1"
)
DEFAULT_KEY = "9f36aeafbe60771e321a7cc95a78140772ab3e96"


def _pinned_ip(url: str) -> str:
    m = re.search(r"-ip-([\d\.]+):", url)
    return m.group(1) if m else ""


def _probe_one_sync(local_port: int, tcin: str, store_id: str) -> tuple[int, int, dict | None]:
    """Sync single probe via curl_cffi. Returns (status, latency_ms, parsed-or-None)."""
    params = {
        "key": DEFAULT_KEY,
        "tcin": tcin,
        "store_id": store_id,
        "pricing_store_id": store_id,
        "has_pricing_store_id": "true",
        "is_bot": "false",
        "channel": "WEB",
        "page": f"/p/A-{tcin}",
    }
    headers = {
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9",
        "origin": "https://www.target.com",
        "referer": f"https://www.target.com/p/A-{tcin}",
        "priority": "u=1, i",
        "sec-ch-ua": '"Chromium";v="131", "Google Chrome";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
    }
    proxy_url = f"http://127.0.0.1:{local_port}"
    t0 = time.time()
    try:
        r = cffi.get(
            REDSKY_MODERN, params=params, headers=headers,
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=15, impersonate="chrome131",
        )
        ms = int((time.time() - t0) * 1000)
        if r.status_code == 200:
            try:
                return 200, ms, r.json()
            except Exception:
                return 200, ms, None
        return r.status_code, ms, None
    except Exception:
        return 0, int((time.time() - t0) * 1000), None


async def preflight_validate(
    proxy_urls: list[str],
    tcin: str = "50270379",
    store_id: str = "3252",
    first_local_port: int = 23000,
    parallelism: int = 5,
) -> list[str]:
    """
    Probe every proxy in parallel batches; return the subset that passed.

    Pass = HTTP 200 + parseable response with a `data.product` field, indicating
    the proxy is currently in Shape's good graces for stock-check requests.

    Uses a separate port range (default 9000+) so it doesn't collide with the
    production forwarder pool that workers will use.
    """
    if not proxy_urls:
        return []

    pool = ForwarderPool()
    for i, url in enumerate(proxy_urls):
        pool.add_upstream(url, first_local_port + i)
    await pool.start_all()

    semaphore = asyncio.Semaphore(parallelism)
    results: list[tuple[str, int, int, bool]] = []

    async def probe(url: str, port: int):
        async with semaphore:
            status, ms, body = await asyncio.to_thread(
                _probe_one_sync, port, tcin, store_id
            )
            valid = (status == 200 and isinstance(body, dict)
                     and "data" in body and "product" in body.get("data", {}))
            results.append((url, status, ms, valid))
            ip = _pinned_ip(url)
            mark = "OK" if valid else ("!!" if status == 403 else "??")
            logger.info(f"[PREFLIGHT] {mark} {ip:<18} status={status} {ms}ms "
                        f"valid={valid}")

    try:
        await asyncio.gather(*[
            probe(url, first_local_port + i)
            for i, url in enumerate(proxy_urls)
        ])
    finally:
        await pool.stop_all()

    verified = [url for url, _, _, valid in results if valid]
    n_total = len(proxy_urls)
    n_pass = len(verified)
    n_403 = sum(1 for _, s, _, _ in results if s == 403)
    n_other = n_total - n_pass - n_403
    logger.info(
        f"[PREFLIGHT] {n_pass}/{n_total} verified clean  "
        f"(403={n_403}, other={n_other})"
    )
    return verified

"""
Standalone Walmart stock check test — PDP HTML scraping approach.

Fetches product detail pages and parses __NEXT_DATA__ for stock info.
No GraphQL hash needed — works reliably at 3+ checks/sec with proxies.

Usage:
    python walmart_stock_test.py
"""

import json
import re
import time
import threading
import random
from pathlib import Path
from curl_cffi import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WALMART_SELLER_ID = "F55CDC31AB754BB68FE0851F0F1F2C96"

ITEM_IDS = [
    "15042474261",
    "19012610850",
    "13816151308",
    "19402160990",
    "19380764160",
    "19283656289",
    "19009204107",
    "17823811037",
]

PROXIES_FILE = Path(__file__).parent / "config" / "proxyIps.json"


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

IMPERSONATE_CHOICES = ["chrome124", "chrome120", "chrome119", "chrome116"]


def load_proxies() -> list[str]:
    if not PROXIES_FILE.exists():
        return []
    data = json.loads(PROXIES_FILE.read_text())
    return data.get("proxies", [])


def check_pdp(item_id: str, proxy: str | None = None) -> dict:
    """Fetch a Walmart PDP and extract stock info from __NEXT_DATA__."""
    t0 = time.monotonic()
    try:
        px = {"http": proxy, "https": proxy} if proxy else None
        with requests.Session(impersonate=random.choice(IMPERSONATE_CHOICES)) as s:
            if px:
                s.proxies.update(px)
            resp = s.get(
                f"https://www.walmart.com/ip/{item_id}",
                timeout=10,
                headers={
                    "user-agent": random.choice(USER_AGENTS),
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "accept-language": random.choice(["en-US,en;q=0.9", "en-US,en;q=0.5", "en-US"]),
                },
            )
            ms = (time.monotonic() - t0) * 1000

            if "blocked" in str(resp.url):
                return {"item_id": item_id, "error": "BLOCKED", "ms": ms}
            if resp.status_code != 200:
                return {"item_id": item_id, "error": f"HTTP {resp.status_code}", "ms": ms}

            nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text)
            if not nd:
                return {"item_id": item_id, "error": "no __NEXT_DATA__", "ms": ms}

            data = json.loads(nd.group(1))
            product = data["props"]["pageProps"]["initialData"]["data"].get("product", {})

            seller_id = product.get("sellerId", "")
            seller_name = product.get("sellerName", "")
            is_walmart = (
                seller_id.upper() == WALMART_SELLER_ID
                or seller_name.lower() == "walmart.com"
            )
            avail = product.get("availabilityStatus", "UNKNOWN")
            show_atc = product.get("showAtc", False)
            price = product.get("priceInfo", {}).get("currentPrice", {}).get("price")

            return {
                "item_id": item_id,
                "name": product.get("name", "Unknown"),
                "price": price,
                "availability": avail,
                "in_stock": avail in ("IN_STOCK", "PRE_ORDER_SELLABLE") and show_atc,
                "walmart_direct": is_walmart,
                "seller": seller_name or seller_id[:16] or "unknown",
                "ms": ms,
            }
    except Exception as e:
        return {"item_id": item_id, "error": str(e)[:80], "ms": (time.monotonic() - t0) * 1000}


def print_result(r: dict, label: str = ""):
    prefix = f"[{label}] " if label else ""
    if r.get("error"):
        print(f"  {prefix}{r['item_id']}: {r['error']} ({r['ms']:.0f}ms)")
    else:
        stock = "IN STOCK" if r.get("in_stock") else "OUT OF STOCK"
        price = f"${r['price']:.2f}" if r.get("price") else "no price"
        seller = "Walmart" if r.get("walmart_direct") else r.get("seller", "?")
        name = r.get("name", r["item_id"])[:55]
        print(f"  {prefix}{name}: {stock} | {price} | {seller} ({r['ms']:.0f}ms)")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_sequential_no_proxy():
    print(f"\n{'=' * 70}")
    print("TEST 1: Sequential, no proxy (first 3 items)")
    print("=" * 70)
    for item_id in ITEM_IDS[:3]:
        r = check_pdp(item_id)
        print_result(r)


def test_parallel_proxies():
    proxies = load_proxies()
    print(f"\n{'=' * 70}")
    print(f"TEST 2: Parallel all {len(ITEM_IDS)} items, different proxy each")
    print("=" * 70)

    results = [None] * len(ITEM_IDS)

    def _check(i, iid, px):
        results[i] = check_pdp(iid, px)

    t0 = time.monotonic()
    threads = []
    for i, iid in enumerate(ITEM_IDS):
        px = proxies[i % len(proxies)] if proxies else None
        t = threading.Thread(target=_check, args=(i, iid, px))
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=15)

    wall = (time.monotonic() - t0) * 1000
    for r in results:
        if r:
            print_result(r)
        else:
            print("  TIMEOUT")
    print(f"  Wall time: {wall:.0f}ms")


def test_sustained_rate():
    proxies = load_proxies()
    if not proxies:
        print("\nTEST 3: SKIPPED — no proxies")
        return

    DURATION = 10.0
    RATE = 3.0
    INTERVAL = 1.0 / RATE

    print(f"\n{'=' * 70}")
    print(f"TEST 3: Sustained {RATE:.0f} checks/sec for {DURATION:.0f}s ({int(RATE * DURATION)} total)")
    print("=" * 70)

    results = []
    lock = threading.Lock()
    proxy_idx = [0]

    def _fire(iid, px):
        r = check_pdp(iid, px)
        with lock:
            results.append(r)

    t0 = time.monotonic()
    count = 0
    while time.monotonic() - t0 < DURATION:
        iid = ITEM_IDS[count % len(ITEM_IDS)]
        px = proxies[proxy_idx[0] % len(proxies)]
        proxy_idx[0] += 1
        threading.Thread(target=_fire, args=(iid, px), daemon=True).start()
        count += 1
        time.sleep(INTERVAL)

    # Wait for stragglers
    time.sleep(5.0)

    with lock:
        total = len(results)
        ok = sum(1 for r in results if not r.get("error"))
        blocked = sum(1 for r in results if r.get("error") == "BLOCKED")
        http_err = sum(1 for r in results if str(r.get("error", "")).startswith("HTTP"))
        other_err = total - ok - blocked - http_err
        avg_ms = sum(r.get("ms", 0) for r in results) / max(total, 1)
        in_stock = sum(1 for r in results if r.get("in_stock"))

    print(f"  Fired: {count} | Returned: {total}")
    print(f"  OK: {ok} ({ok / max(total, 1) * 100:.0f}%) | Blocked: {blocked} | HTTP err: {http_err} | Other: {other_err}")
    print(f"  In stock (Walmart-sold): {in_stock} | Avg latency: {avg_ms:.0f}ms")
    print(f"  Effective rate: {total / DURATION:.1f}/sec")


if __name__ == "__main__":
    print("Walmart Stock Check — PDP HTML Approach")
    print(f"Items: {len(ITEM_IDS)}")
    proxies = load_proxies()
    print(f"Proxies: {len(proxies)}")

    test_sequential_no_proxy()
    test_parallel_proxies()
    test_sustained_rate()

    print(f"\n{'=' * 70}")
    print("Done.")

"""
Validate the local forwarder end-to-end:
1. Start ONE forwarder against ONE BD upstream
2. Use curl_cffi through 127.0.0.1:<port> to hit lumtest.com (proves auth chain)
3. Then hit RedSky modern endpoint (proves Target reachable too)
"""

import asyncio
import json
import sys
import time
from pathlib import Path

from curl_cffi import requests as cffi
sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.proxy.local_forwarder import ForwarderPool


async def main():
    proxy_file = Path("config/proxyIps.json")
    data = json.loads(proxy_file.read_text(encoding="utf-8"))
    proxies = data.get("proxies", [])
    if not proxies:
        print("[ERR] no enabled proxies")
        return

    upstream_url = proxies[0]
    local_port = 8080

    pool = ForwarderPool()
    pool.add_upstream(upstream_url, local_port)
    await pool.start_all()
    print(f"[+] forwarder up on 127.0.0.1:{local_port}", flush=True)

    local_proxy = f"http://127.0.0.1:{local_port}"

    # ── Test 1: lumtest (proves auth chain works, confirms exit IP) ──
    print("\n[TEST 1] curl_cffi → local forwarder → BD → lumtest.com")
    t0 = time.time()
    try:
        r = await asyncio.to_thread(
            cffi.get,
            "https://lumtest.com/myip.json",
            proxies={"http": local_proxy, "https": local_proxy},
            timeout=15,
            impersonate="chrome131",
        )
        ms = int((time.time() - t0) * 1000)
        print(f"  status={r.status_code}  latency={ms}ms")
        print(f"  body: {r.text[:200]}")
        # Confirm the exit IP matches what we pinned
        try:
            body = r.json()
            actual_ip = body.get("ip")
            up = list(pool.upstreams.values())[0]
            if actual_ip == up.pinned_ip:
                print(f"  [PASS] exit IP {actual_ip} matches pinned {up.pinned_ip}")
            else:
                print(f"  [WARN] exit IP {actual_ip} != pinned {up.pinned_ip}")
        except Exception:
            pass
    except Exception as e:
        print(f"  [EXC] {type(e).__name__}: {e}")

    # ── Test 2: RedSky modern endpoint ──
    print("\n[TEST 2] curl_cffi → local forwarder → BD → redsky modern endpoint")
    t0 = time.time()
    try:
        r = await asyncio.to_thread(
            cffi.get,
            "https://redsky.target.com/redsky_aggregations/v1/web/product_fulfillment_and_variation_hierarchy_v1",
            params={
                "key": "9f36aeafbe60771e321a7cc95a78140772ab3e96",
                "tcin": "50270379",
                "store_id": "3252",
                "pricing_store_id": "3252",
                "has_pricing_store_id": "true",
                "is_bot": "false",
                "channel": "WEB",
                "page": "/p/A-50270379",
            },
            headers={
                "accept": "application/json",
                "accept-language": "en-US,en;q=0.9",
                "origin": "https://www.target.com",
                "referer": "https://www.target.com/p/A-50270379",
                "sec-ch-ua": '"Chromium";v="131", "Google Chrome";v="131", "Not_A Brand";v="24"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-site",
            },
            proxies={"http": local_proxy, "https": local_proxy},
            timeout=15,
            impersonate="chrome131",
        )
        ms = int((time.time() - t0) * 1000)
        print(f"  status={r.status_code}  latency={ms}ms")
        if r.status_code == 200:
            body = r.text
            print(f"  body_len={len(body)}")
            try:
                j = json.loads(body)
                prod = j.get("data", {}).get("product", {})
                f = prod.get("fulfillment", {})
                print(f"  sold_out={f.get('sold_out')}  "
                      f"oos_all={f.get('is_out_of_stock_in_all_store_locations')}  "
                      f"avail={f.get('shipping_options', {}).get('availability_status')}")
            except Exception as e:
                print(f"  parse: {e}")
        else:
            print(f"  body: {r.text[:300]}")
    except Exception as e:
        print(f"  [EXC] {type(e).__name__}: {e}")

    # ── Forwarder stats ──
    print("\n[STATS]")
    for up in pool.upstreams.values():
        s = up.stats
        print(f"  port={up.port} ip={up.pinned_ip} conns={s.connections_total} "
              f"in={s.bytes_in}B out={s.bytes_out}B errors={s.errors}")
        if s.last_error:
            print(f"    last_error: {s.last_error}")

    await pool.stop_all()


if __name__ == "__main__":
    asyncio.run(main())

"""
Test if the 'disabled' (raw-Python-burned) IPs return 200 when accessed via
curl_cffi+chrome131 through the local forwarder. If yes, we recover them.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

from curl_cffi import requests as cffi
sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.proxy.local_forwarder import ForwarderPool


async def test_one(pool, proxy_url, port):
    pool.add_upstream(proxy_url, port)


async def main():
    data = json.loads(Path("config/proxyIps.json").read_text(encoding="utf-8"))
    disabled = data.get("disabled_proxies", [])[:6]

    pool = ForwarderPool()
    for i, p in enumerate(disabled):
        pool.add_upstream(p, 8100 + i)
    await pool.start_all()

    print(f"[+] {len(disabled)} forwarders up on ports 8100-{8100+len(disabled)-1}")
    print(f"[+] testing disabled IPs through curl_cffi+chrome131")
    print()

    REDSKY = "https://redsky.target.com/redsky_aggregations/v1/web/product_fulfillment_and_variation_hierarchy_v1"
    params = {
        "key": "9f36aeafbe60771e321a7cc95a78140772ab3e96",
        "tcin": "50270379",
        "store_id": "3252",
        "pricing_store_id": "3252",
        "has_pricing_store_id": "true",
        "is_bot": "false",
        "channel": "WEB",
        "page": "/p/A-50270379",
    }
    headers = {
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
    }

    recovered = 0
    still_blocked = 0
    print(f"{'port':<6} {'pinned_ip':<18} {'status':<8} {'ms':<6} {'avail':<15}")
    print("-" * 65)
    for up in pool.upstreams.values():
        local_proxy = f"http://127.0.0.1:{up.port}"
        t0 = time.time()
        try:
            r = await asyncio.to_thread(
                cffi.get, REDSKY,
                params=params, headers=headers,
                proxies={"http": local_proxy, "https": local_proxy},
                timeout=15, impersonate="chrome131",
            )
            ms = int((time.time() - t0) * 1000)
            avail = "?"
            if r.status_code == 200:
                try:
                    j = r.json()
                    f = j.get("data", {}).get("product", {}).get("fulfillment", {})
                    avail = f.get("shipping_options", {}).get("availability_status") or "?"
                except Exception:
                    pass
                recovered += 1
            else:
                still_blocked += 1
            print(f"{up.port:<6} {up.pinned_ip:<18} {r.status_code:<8} {ms:<6} {avail:<15}")
        except Exception as e:
            print(f"{up.port:<6} {up.pinned_ip:<18} EXC: {type(e).__name__}: {e}")
            still_blocked += 1
        await asyncio.sleep(2)   # spacing

    print()
    print(f"[RESULT] recovered={recovered}/{len(disabled)}  still_blocked={still_blocked}/{len(disabled)}")

    await pool.stop_all()


if __name__ == "__main__":
    asyncio.run(main())

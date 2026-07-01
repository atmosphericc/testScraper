#!/usr/bin/env python3
"""Confirm each Target account exits its OWN Bright Data IP — through the SAME
local forwarder the purchase path uses, WITHOUT launching a browser (reliable,
fast, no profile locks).

Why this exists: verify_multi_account_live.py drives WorkerPool directly, which
hands Chrome the raw *authenticated* BD URL — Chrome can't navigate through an
authed proxy without the forwarder, so its egress-IP probe fails. This tool sets
up the real ForwarderPool (port 23000+idx, exactly like
BulletproofPurchaseManager._setup_purchase_forwarders) and GETs the IP echo
through each forwarder, so the reported exit IP is faithful to production.

Safe: hits only Bright Data's own IP echo (lumtest.com — NOT Target/Shape), no
add-to-cart, no login, no browser. Run:  python check_egress_ips.py
"""
from __future__ import annotations

import os
import sys
import re
import json
import asyncio
import threading
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from src.proxy.local_forwarder import ForwarderPool  # noqa: E402


def main() -> int:
    cfg = os.path.join("config", "target_accounts.json")
    if not os.path.exists(cfg):
        print(f"[FATAL] {cfg} not found.")
        return 2
    accts = json.load(open(cfg, encoding="utf-8"))["accounts"]

    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def _run():
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    threading.Thread(target=_run, daemon=True, name="EgressCheckLoop").start()
    ready.wait()

    fwd = ForwarderPool()
    ports = {}
    for i, a in enumerate(accts, start=1):
        pu = a.get("proxy_url")
        if not a.get("enabled", True) or not pu:
            print(f"  {a.get('account_id')}: HOME IP (no proxy_url / disabled)")
            continue
        m = re.search(r"-ip-([0-9.]+)[:@]", pu)
        port = 23000 + i
        fwd.add_upstream(pu, port)
        ports[a["account_id"]] = (port, m.group(1) if m else "?")

    if not ports:
        print("No enabled accounts have a BD proxy_url — nothing to check.")
        return 0

    try:
        asyncio.run_coroutine_threadsafe(fwd.start_all(), loop).result(timeout=20)
        print("forwarder pool LIVE for:", list(ports.keys()))
    except Exception as e:
        print(f"[FATAL] forwarder start_all FAILED: {e!r}")
        return 1

    def _check(port):
        p = f"http://127.0.0.1:{port}"
        op = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": p, "https": p})
        )
        return op.open("https://lumtest.com/myip.json", timeout=25).read().decode()

    print("\n=== exit IP through each account's production forwarder ===")
    got = {}
    for aid, (port, want) in ports.items():
        try:
            d = json.loads(_check(port))
            ip = d.get("ip")
            geo = d.get("geo") or {}
            flag = "OK" if ip == want else "MISMATCH!"
            print(
                f"  {aid:10s} expect {want:16s} -> got {ip:16s} [{flag}]  "
                f"({geo.get('city')}, {geo.get('region_name') or geo.get('region')})"
            )
            got[aid] = ip
        except Exception as e:
            print(f"  {aid:10s} expect {want:16s} -> FAILED: {type(e).__name__}: {e}")
            got[aid] = None

    vals = [v for v in got.values() if v]
    ok = len(set(vals)) == len(ports) and all(
        got.get(a) == w for a, (p, w) in ports.items()
    )
    print(
        "\nVERDICT: "
        + (
            "OK — all accounts exit DISTINCT + CORRECT BD IPs"
            if ok
            else "PROBLEM — not all distinct/correct (see above)"
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Backfill real product names into config/product_config.json from the running
bot's dashboard status API.

Why: the resilient stock checker captures each TCIN's real RedSky title, but the
auto-backfill (_backfill_config_names) only runs in the LEGACY StockMonitor, not
the resilient production path — so armed SKUs keep their "Product <tcin>"
placeholder names. This standalone tool (no bot internals, no hot-path code)
reads /api/status from the live dashboard and writes the real titles back into
the config for PLACEHOLDER entries only. It never overwrites a name you set, and
the write is atomic with a backup, so it is safe to run repeatedly (e.g. right
after launch for the live SKUs, then again after the drop once the pre-release
SKUs publish).

Usage:  python backfill_product_names.py            # auto-detect the dashboard port
        python backfill_product_names.py --port 5001
        python backfill_product_names.py --dry-run   # show what would change, write nothing
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.request

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "product_config.json")
CANDIDATE_PORTS = (5001, 5002, 5003)


def _fetch_status(port: int, timeout: float = 4.0):
    """GET http://127.0.0.1:<port>/api/status → {tcin: title}. None on any failure."""
    url = f"http://127.0.0.1:{port}/api/status"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None
    # /api/status shape tolerated: {"products":[{tcin,title}]} OR {tcin:{title}} OR [{tcin,title}]
    titles: dict[str, str] = {}

    def _add(tcin, title):
        t = str(tcin or "").strip()
        if t and isinstance(title, str) and title and not title.startswith("Product "):
            titles[t] = title

    # /api/status is {"stock_data": {tcin: {title,...}}, ...} — unwrap it first.
    if isinstance(data, dict) and isinstance(data.get("stock_data"), dict):
        for tcin, v in data["stock_data"].items():
            if isinstance(v, dict):
                _add(tcin, v.get("title") or v.get("name"))
    elif isinstance(data, dict) and isinstance(data.get("products"), list):
        for p in data["products"]:
            if isinstance(p, dict):
                _add(p.get("tcin"), p.get("title") or p.get("name"))
    elif isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                _add(k, v.get("title") or v.get("name"))
    elif isinstance(data, list):
        for p in data:
            if isinstance(p, dict):
                _add(p.get("tcin"), p.get("title") or p.get("name"))
    return titles


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=None, help="dashboard port (else auto-detect)")
    ap.add_argument("--dry-run", action="store_true", help="show changes, write nothing")
    args = ap.parse_args()

    ports = [args.port] if args.port else list(CANDIDATE_PORTS)
    titles = None
    used_port = None
    for p in ports:
        titles = _fetch_status(p)
        if titles is not None:
            used_port = p
            break
    if titles is None:
        print("[BACKFILL] dashboard not reachable on ports "
              f"{ports} — is the bot running? (launch it, then re-run this.)")
        return 2
    print(f"[BACKFILL] dashboard :{used_port} returned {len(titles)} real title(s)")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    changes = []
    for prod in cfg.get("products", []):
        tcin = str(prod.get("tcin") or "")
        cur = prod.get("name", "")
        # placeholder-only: never overwrite a name a human/you already set
        if cur and not cur.startswith("Product "):
            continue
        real = titles.get(tcin)
        if real:
            changes.append((tcin, cur, real))
            if not args.dry_run:
                prod["name"] = real

    if not changes:
        print("[BACKFILL] nothing to update — every placeholder either has no live title yet "
              "(pre-release SKUs publish at drop) or is already named.")
        return 0

    print(f"[BACKFILL] {'WOULD update' if args.dry_run else 'updating'} {len(changes)} name(s):")
    for tcin, old, new in changes:
        print(f"  {tcin}: {old!r} -> {new!r}")

    if args.dry_run:
        return 0

    bak = CONFIG_PATH + f".bak.{time.strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(CONFIG_PATH, bak)
    tmp = CONFIG_PATH + f".tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, CONFIG_PATH)
    print(f"[BACKFILL] wrote {CONFIG_PATH} (backup {os.path.basename(bak)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

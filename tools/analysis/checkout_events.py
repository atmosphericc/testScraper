#!/usr/bin/env python3
"""Claim C-2026-09-17-01 / -04 (docs/CLAIMS.md): every checkout (place-order)
POST in a run log with its time, status, error key and the hold / re-shoot /
order lines around it, in log order, so same-cart sequences can be read.

Usage:  python tools/analysis/checkout_events.py logs/runs/run_20260804_000646.log

Thread attribution is NOT automatic (July/Aug logs interleave up to three
account threads without a tag). Read the sequence: "Checkout complete (t=X s)"
ties an order back to its "Starting purchase" X seconds earlier; "In-place
re-shoot N/4" counters and DELETE cart_items/<id> lines identify a cart.
"""
from __future__ import annotations

import re
import sys

from shots import TS

KEY = re.compile(r"tgt-cart-error-key': '([A-Z_,]+)'")
API = re.compile(r"HTTP (\d+) in ([\d.]+)s \((\d+) body")
FL = re.compile(r"atc=(\d+) pre=(\d+) po=(\d+)")
MARK = ("In-place re-shoot", "HOLDING the won cart", "Order placed", "ORDER PLACED", "Checkout failed",
        "Checkout complete", "cooldown active", "[WON_CART_DIRECT]", "[FS_TICKET]", "CLEAR_CART_API] DELETE",
        "DELETE https://carts.target.com/web_checkouts/v1/cart_items/")


def main(path: str) -> None:
    ts = "??:??:??"
    last_key = ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            m = TS.match(line)
            if m:
                ts = f"{m.group(2)}:{m.group(3)}:{m.group(4)}.{m.group(5)}"
            k = KEY.search(line)
            if k:
                last_key = k.group(1)
            if "[API_PLACE_ORDER] HTTP" in line:
                a = API.search(line)
                st = a.group(1) if a else "?"
                print(f"{n:>7} {ts} API  HTTP {st} {a.group(2) if a else ''}s body={a.group(3) if a else ''} "
                      f"key={last_key if st != '200' else ''}")
                last_key = ""
            elif "[FAST_LANE] chain done" in line and "po=" in line:
                f = FL.search(line)
                if f and f.group(3) != "0":
                    print(f"{n:>7} {ts} FL   atc={f.group(1)} pre={f.group(2)} po={f.group(3)} "
                          f"{'key=' + last_key if f.group(3) != '200' else ''}")
                    last_key = ""
            elif any(mk in line for mk in MARK):
                print(f"{n:>7} {ts}      {line.strip()[:140]}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print("=====", p)
        main(p)

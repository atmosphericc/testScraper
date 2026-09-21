#!/usr/bin/env python3
"""The hot-SKU funnel, end to end, across every run log.

Every purchase on a hot item passes four independent gates, and the bot's
outcome is the product of all four. This prints the measured pass rate of each
so effort can be aimed at the one that actually binds:

  G1 edge limiter   cart_items POST -> empty-body 429 ERR_A2C_TCIN_RATE_LIMITED
                    means dropped before the cart service ever saw it.
  G2 ATC throttle   the cart service's own demand throttle (DCO_RATE_LIMITED /
                    FAST_SELLING) or 424 inventory -> admitted, but no cart.
                    Passing G1+G2 = an ATC 201 = a WON CART.
  G3 pre_checkout   the won cart must initialise a checkout. FAST_SELLING here
                    blocks the place-order entirely.
  G4 place-order    the checkout POST itself: FAST_SELLING again, or
                    RESERVATION_FAILURE (real inventory contention), or 200.

Usage:  python tools/analysis/funnel.py [--hot-only] [logs/runs/run_2026*.log ...]
        (no log arguments = every logs/runs/run_2026*.log)

Read-only. Attribution note: before 2026-08-25 the logs carry no per-shot
identity tag, so per-identity splits are only printed for tagged logs.
"""
from __future__ import annotations

import argparse
import glob
import re
from collections import Counter, defaultdict

from shots import parse_shots, TS

# The hyped families. Everything else counts as ordinary.
HOT_PREFIXES = ("10108920", "101242210", "101140749")
HOT_EXACT = {"1012055696", "1011209279", "1011960739", "95274164", "95274160",
             "1012422107", "1011407490"}
CHAIN = re.compile(r"\[FAST_LANE\] chain done in ([\d.]+)s — atc=(\d+) pre=(\d+) po=(\d+)")
ORDER = re.compile(r"(\*\*\* ORDER PLACED \*\*\*|\[API_PLACE_ORDER\] Order placed)")
API_PO = re.compile(r"\[API_PLACE_ORDER\] HTTP (\d+) in")
KEY = re.compile(r"tgt-cart-error-key': '([A-Z_,]+)'")


def is_hot(tcin: str) -> bool:
    t = str(tcin or "")
    return t in HOT_EXACT or any(t.startswith(p) for p in HOT_PREFIXES)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="*")
    ap.add_argument("--hot-only", action="store_true")
    a = ap.parse_args()
    paths = a.logs or sorted(glob.glob("logs/runs/run_2026*.log"))

    g12 = defaultdict(Counter)        # era -> outcome counts for every ATC shot
    chains = defaultdict(Counter)     # era -> in-chain pre/po outcomes
    orders = defaultdict(int)
    po_keys = defaultdict(Counter)
    for path in paths:
        era = "hot" if "hot" in path else None
        shots = parse_shots(path)
        for s in shots:
            if a.hot_only and not is_hot(s.tcin):
                continue
            cls = "hot" if is_hot(s.tcin) else "ordinary"
            g12[cls][s.cls] += 1
        # in-chain pre/po: only the fast-lane chain-done line carries all three
        last_tcin = None
        last_key = ""
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line[:4] == "2026":
                    continue          # skip the duplicated logger copy
                k = KEY.search(line)
                if k:
                    last_key = k.group(1)
                f = re.search(r"\[FAST_LANE\] Firing ATC.*?\(tcin=(\d+)", line)
                if f:
                    last_tcin = f.group(1)
                m = CHAIN.search(line)
                if m:
                    atc, pre, po = m.group(2), m.group(3), m.group(4)
                    if atc != "201":
                        continue      # no cart, G3/G4 never reached
                    if a.hot_only and not is_hot(last_tcin):
                        continue
                    cls = "hot" if is_hot(last_tcin) else "ordinary"
                    chains[cls]["carts"] += 1
                    if pre.startswith("2"):
                        chains[cls]["pre_2xx"] += 1
                        if po == "200":
                            chains[cls]["po_200"] += 1
                        elif po != "0":
                            chains[cls][f"po_{po}"] += 1
                            po_keys[cls][last_key or "-"] += 1
                    else:
                        chains[cls][f"pre_{pre}"] += 1
                if ORDER.search(line):
                    orders["hot" if is_hot(last_tcin) else "ordinary"] += 1

    print(f"== funnel over {len(paths)} run log(s)\n")
    for cls in ("ordinary", "hot"):
        sh = g12[cls]
        n = sum(sh.values())
        if not n:
            continue
        n401 = sh.get("401", 0)
        denom = n - n401
        edge = sh.get("edge429", 0)
        g1_pass = denom - edge
        carts = sh.get("201", 0)
        print(f"-- {cls.upper()} SKUs: {n} add-to-cart shots ({n401} were 401 auth-denied, excluded below)")
        print(f"   G1 edge limiter : {g1_pass}/{denom} admitted "
              f"({100.0 * g1_pass / denom if denom else 0:.1f}%)")
        print(f"   G2 cart service : {carts}/{g1_pass} of those became a CART "
              f"({100.0 * carts / g1_pass if g1_pass else 0:.1f}%)   [{dict(sh)}]")
        c = chains[cls]
        if c["carts"]:
            print(f"   G3 pre_checkout : {c['pre_2xx']}/{c['carts']} carts got a 2xx pre_checkout "
                  f"IN THE ORIGINAL CHAIN ({100.0 * c['pre_2xx'] / c['carts']:.1f}%)")
            po_fired = sum(v for k, v in c.items() if k.startswith("po_"))
            print(f"   G4 place-order  : {c['po_200']}/{po_fired} in-chain place-orders returned 200 "
                  f"({100.0 * c['po_200'] / po_fired if po_fired else 0:.0f}%)   "
                  f"[{{k: v for k, v in c.items() if k.startswith('po_')}}]".replace("{k: v for k, v in c.items() if k.startswith('po_')}",
                  str({k: v for k, v in c.items() if k.startswith('po_')})))
            print(f"      in-chain pre outcomes: "
                  f"{ {k: v for k, v in c.items() if k.startswith('pre_')} }")
            if po_keys[cls]:
                print(f"      in-chain place-order error keys: {dict(po_keys[cls])}")
        print(f"   ORDERS (any route): {orders[cls]}\n")


if __name__ == "__main__":
    main()

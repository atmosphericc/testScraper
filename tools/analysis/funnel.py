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

# The hyped families.
#
# 2026-09-22: this list is STILL hand-maintained -- there is no hot/hype field in
# config/product_config.json, so there is no contemporaneous source to derive it
# from. What changed is the DEFAULT. Until today an unclassified TCIN silently
# became "ordinary", so every SKU nobody had judged landed in the ordinary bucket
# and corrupted its rate: 2,184 Mega-Evolution chains fired on one night (08-27)
# sat in "ordinary" and drove the published ordinary edge-pass rate to
# 67/2,331 = 2.9%. Classified correctly it is 62/204 = 30.4% -- the quoted
# "hot takes only a 1.7x edge penalty" inverts into a >26x gap the other way.
#
# Now there are THREE classes and an unclassified TCIN is `unknown`, never
# silently ordinary. `unknown` must be REPORTED, not folded into either side.
HOT_PREFIXES = ("10108920", "101242210", "101140749")
HOT_EXACT = {"1012055696", "1011209279", "1011960739", "95274164", "95274160",
             "1012422107", "1011407490",
             # added 2026-09-22 -- hyped by the project's own 09-20 judgment and
             # previously mis-bucketed as ordinary (CURRENT_STATE.md).
             "1012644665", "1012644666", "1012644667", "95290385"}

# TCINs positively judged ORDINARY. Membership here is a claim with a source,
# not an absence of evidence -- that is the whole point of the three-way split.
ORDINARY_EXACT = {
    # "The one agreed-ordinary TCIN left is 1011483413" -- CURRENT_STATE.md.
    # Deliberately NOT qty-pinned and NOT parked (bat:1200-1207): it belongs to
    # the 1011483xxx family that produced every order this bot has ever placed.
    "1011483413",
}
CHAIN = re.compile(r"\[FAST_LANE\] chain done in ([\d.]+)s — atc=(\d+) pre=(\d+) po=(\d+)")
ORDER = re.compile(r"(\*\*\* ORDER PLACED \*\*\*|\[API_PLACE_ORDER\] Order placed)")
API_PO = re.compile(r"\[API_PLACE_ORDER\] HTTP (\d+) in")
KEY = re.compile(r"tgt-cart-error-key': '([A-Z_,]+)'")


def sku_class(tcin: str) -> str:
    """'hot' | 'ordinary' | 'unknown'. NEVER guess -- an unlisted TCIN is unknown.

    Silently defaulting the unlisted to 'ordinary' is the bug this replaces; it
    is undercoverage of the hot list turning into contamination of the ordinary
    bucket, which is invisible in the output and inverted a headline number."""
    t = str(tcin or "")
    if t in HOT_EXACT or any(t.startswith(p) for p in HOT_PREFIXES):
        return "hot"
    if t in ORDINARY_EXACT:
        return "ordinary"
    return "unknown"


def is_hot(tcin: str) -> bool:
    """Back-compat shim for limiter_key / readout_multi_sku / shot_index_yield.

    Note the asymmetry these callers inherit: `not is_hot(t)` means "hot or
    unknown", NOT "ordinary". Callers that need a real ordinary set must use
    sku_class() directly."""
    return sku_class(tcin) == "hot"


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
    unknown_tcins = set()             # 2026-09-22: so the list can be maintained
    for path in paths:
        era = "hot" if "hot" in path else None
        shots = parse_shots(path)
        for s in shots:
            if a.hot_only and not is_hot(s.tcin):
                continue
            cls = sku_class(s.tcin)
            if cls == "unknown":
                unknown_tcins.add(str(s.tcin))
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
                    cls = sku_class(last_tcin)
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
                    orders[sku_class(last_tcin)] += 1

    print(f"== funnel over {len(paths)} run log(s)\n")
    # 2026-09-22: iterate all THREE classes. Printing only hot+ordinary would
    # hide the unknown bucket entirely, which is the same blind spot in a new
    # place. The point of the split is that unclassified volume is VISIBLE.
    for cls in ("ordinary", "hot", "unknown"):
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
        if cls == "unknown":
            seen = sorted(unknown_tcins)
            print(f"   !! {n} shots on {len(seen)} UNCLASSIFIED TCIN(s). Every")
            print(f"      hot-vs-ordinary number above is INCOMPLETE until these are")
            print(f"      judged and added to HOT_EXACT or ORDINARY_EXACT in this file.")
            print(f"      Do not quote a hot-vs-ordinary rate while this is non-empty.")
            print(f"      {', '.join(seen)}\n")


if __name__ == "__main__":
    main()

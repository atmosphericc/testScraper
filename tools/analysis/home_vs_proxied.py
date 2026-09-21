#!/usr/bin/env python3
"""Is the home IP still productive, and is Bright Data dead?

Splits every add-to-cart shot by identity (primary = home IP from 2026-09-11 on,
business/alt-1 = Bright Data throughout), by SKU class, and by how long after the
stock edge it fired. The 0-5 s bucket is the one that produced 12 of 14 fast-lane
orders, so "does primary still convert in the first 5 seconds" is the decisive
number for whether the current setup can win at all.

Usage:  python tools/analysis/home_vs_proxied.py logs/runs/run_2026*.log
Read-only. Identity tags exist only from the 2026-08-25 logs on.
"""
from __future__ import annotations

import glob
import re
import sys
from collections import Counter, defaultdict

from shots import parse_shots, TS

HOT_PREFIXES = ("10108920", "101242210", "101140749")
HOT_EXACT = {"1012055696", "1011209279", "1011960739", "95274164", "95274160",
             "1012422107", "1011407490"}
EDGE = re.compile(r"^\[STOCK\] IN STOCK: (\d+)")
BUCKETS = (("0-5s", 0, 5), ("5-15s", 5, 15), ("15-45s", 15, 45), (">45s/none", 45, 1e12))


def is_hot(t) -> bool:
    t = str(t or "")
    return t in HOT_EXACT or any(t.startswith(p) for p in HOT_PREFIXES)


def edges_of(path):
    """tcin -> sorted list of [STOCK] IN STOCK edge times (same clock as shots)."""
    out = defaultdict(list)
    t = 0.0
    day0 = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = TS.match(line)
            if m:
                if day0 is None:
                    day0 = m.group(1)
                t = int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4)) + int(m.group(5)) / 1000
                if m.group(1) != day0:
                    t += 86400
            e = EDGE.match(line)
            if e:
                out[e.group(1)].append(t)
    return out


def main(paths) -> None:
    agg = defaultdict(Counter)          # (ident_class, sku_class, bucket) -> counts
    per_ident = defaultdict(Counter)
    for path in paths:
        shots = parse_shots(path)
        if not shots:
            continue
        ed = edges_of(path)
        for s in shots:
            if s.ident == "?":
                continue                # untagged era: identity unknowable
            # primary ran through a Bright Data forwarder until 2026-09-04
            home = (s.ident == "primary" and "202609" in path
                    and int(path.split("run_2026")[1][:4]) >= 911)
            ic = "home(primary)" if home else ("proxied(primary)" if s.ident == "primary" else "proxied(BD)")
            sc = "hot" if is_hot(s.tcin) else "ordinary"
            prior = [e for e in ed.get(s.tcin, []) if e <= s.t]
            age = (s.t - prior[-1]) if prior else 1e9      # no edge seen = treat as late
            b = next(n for n, lo, hi in BUCKETS if lo <= age < hi)
            for key in ((ic, sc, b), (ic, sc, "ALL")):
                agg[key]["n"] += 1
                if s.cls == "401":
                    agg[key]["401"] += 1
                elif s.cls == "201":
                    agg[key]["cart"] += 1
            per_ident[ic][s.cls] += 1

    print("carts / non-401 shots, by identity x SKU class x seconds after the stock edge\n")
    hdr = f"{'identity':<16}{'sku':<10}" + "".join(f"{b:>14}" for b, _, _ in BUCKETS) + f"{'ALL':>14}"
    print(hdr)
    print("-" * len(hdr))
    for ic in ("home(primary)", "proxied(primary)", "proxied(BD)"):
        for sc in ("ordinary", "hot"):
            row = f"{ic:<16}{sc:<10}"
            any_data = False
            for b, _, _ in list(BUCKETS) + [("ALL", 0, 0)]:
                c = agg[(ic, sc, b)]
                d = c["n"] - c["401"]
                if c["n"]:
                    any_data = True
                    row += f"{c['cart']}/{d}".rjust(14) if d else f"-/{c['n']}401".rjust(14)
                else:
                    row += "-".rjust(14)
            if any_data:
                print(row)
    print("\nper-identity totals (all tagged logs):")
    for ic, c in per_ident.items():
        n = sum(c.values())
        d = n - c.get("401", 0)
        print(f"  {ic:<18} shots={n:<6} non-401={d:<6} carts={c.get('201', 0)} "
              f"({100.0 * c.get('201', 0) / d if d else 0:.2f}%)  {dict(c)}")


if __name__ == "__main__":
    main(sys.argv[1:] or sorted(glob.glob("logs/runs/run_2026*.log")))

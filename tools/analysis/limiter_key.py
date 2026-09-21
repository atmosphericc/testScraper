#!/usr/bin/env python3
"""What is Target's per-TCIN edge limiter keyed on — the ACCOUNT, or something
shared across our whole fleet (IP / TCIN-global)?

This decides whether Refract's model ("10 tasks on 10 unique accounts, one task
per account per product, no proxies") can multiply our carts, or whether extra
accounts would just share one bucket and change nothing.

For every non-401 shot we count, in the preceding WINDOW seconds on the SAME
TCIN:
  own   = shots by the SAME identity
  other = shots by any OTHER identity
and report the pass rate as each climbs. A pass = any ATC outcome except the
empty-body edge 429 (401s excluded from every denominator: different gate).

  If only `own` suppresses the pass rate -> the bucket is PER-ACCOUNT, and more
  accounts multiply carts linearly. Refract's model transfers.
  If `other` suppresses it too -> the bucket is SHARED, and adding accounts on
  one connection buys much less than the vendor's task count implies.

Note the identities do NOT share an exit: in the September logs primary runs on
the home IP while alt-1/business run on Bright Data exits. So `other`
suppression here means the bucket is TCIN-global rather than per-IP.

Usage: python tools/analysis/limiter_key.py [--window 120] [logs ...]
Read-only.
"""
from __future__ import annotations

import argparse
import glob
from collections import Counter, defaultdict

from shots import parse_shots, is_pass
from funnel import is_hot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="*")
    ap.add_argument("--window", type=float, default=120.0)
    ap.add_argument("--all-skus", action="store_true")
    a = ap.parse_args()
    paths = a.logs or sorted(glob.glob("../../logs/runs/run_2026*.log"))

    own_b = defaultdict(Counter)
    oth_b = defaultdict(Counter)
    joint = defaultdict(Counter)

    def bucket(n):
        return "0" if n == 0 else ("1-2" if n <= 2 else ("3-4" if n <= 4 else ("5-8" if n <= 8 else "9+")))

    for p in paths:
        shots = [s for s in parse_shots(p) if s.ident != "?"]
        if not a.all_skus:
            shots = [s for s in shots if is_hot(s.tcin)]
        for i, s in enumerate(shots):
            if s.cls == "401":
                continue
            own = other = 0
            for j in range(i - 1, -1, -1):
                q = shots[j]
                if s.t - q.t > a.window:
                    break
                if q.tcin != s.tcin:
                    continue
                if q.ident == s.ident:
                    own += 1
                else:
                    other += 1
            ok = is_pass(s)
            for tbl, n in ((own_b, own), (oth_b, other)):
                tbl[bucket(n)]["n"] += 1
                tbl[bucket(n)]["pass"] += int(ok)
            joint[(bucket(own), bucket(other))]["n"] += 1
            joint[(bucket(own), bucket(other))]["pass"] += int(ok)

    order = ["0", "1-2", "3-4", "5-8", "9+"]
    label = "ALL SKUs" if a.all_skus else "HOT SKUs"
    print(f"== {label}: edge pass rate vs prior shots on the SAME TCIN in {a.window:.0f}s ({len(paths)} logs)")
    for name, tbl in (("SAME identity (own)", own_b), ("OTHER identities", oth_b)):
        print(f"\n-- prior shots by {name}")
        print(f"{'prior':>7} {'shots':>7} {'pass':>6}  pass%")
        for k in order:
            if k not in tbl:
                continue
            c = tbl[k]
            print(f"{k:>7} {c['n']:>7} {c['pass']:>6}  {100*c['pass']/c['n']:5.1f}%")

    print(f"\n-- joint (own x other), cells with >=10 shots")
    print(f"{'own':>5} {'other':>6} {'shots':>7} {'pass':>6}  pass%")
    for (o, t), c in sorted(joint.items()):
        if c["n"] >= 10:
            print(f"{o:>5} {t:>6} {c['n']:>7} {c['pass']:>6}  {100*c['pass']/c['n']:5.1f}%")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Claim C-2026-09-17-02 (docs/CLAIMS.md): where an identity's edge-limiter
passes come from — by the gap since ITS OWN previous shot on the same TCIN,
by the number of OUR shots (any identity) on that TCIN in the preceding 120 s,
and per identity totals.

Usage:  python tools/analysis/edge_pass_by_gap.py logs/runs/run_20260915_233355.log [more logs]
        [--ident primary] [--tcins 1010892069,1010892068,...]

Pass = any ATC outcome but an empty-body edge 429; 401s are excluded from every
denominator (they are a different gate). A DCO 429 counts as an edge pass.
"""
from __future__ import annotations

import argparse
import statistics
from collections import Counter, defaultdict

from shots import parse_shots, is_pass, hhmmss

GAP_BUCKETS = (("<10s", 0, 10), ("10-40s", 10, 40), ("40-80s", 40, 80), ("80-200s", 80, 200),
               (">200s/first", 200, float("inf")))  # the first shot ever on a TCIN lands here too


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--ident", default="primary")
    ap.add_argument("--tcins", default="", help="comma list; default = every TCIN in the log")
    a = ap.parse_args()
    want = set(t for t in a.tcins.split(",") if t.strip())
    for path in a.logs:
        shots = [s for s in parse_shots(path) if not want or s.tcin in want]
        print(f"===== {path}: {len(shots)} shots {dict(Counter(s.cls for s in shots))}")
        by_tcin = defaultdict(list)
        for s in shots:
            by_tcin[s.tcin].append(s)
        # per identity totals
        for idn in sorted(set(s.ident for s in shots)):
            v = [s for s in shots if s.ident == idn and s.cls != "401"]
            n401 = sum(1 for s in shots if s.ident == idn and s.cls == "401")
            print(f"  {idn:>9}: passes {sum(is_pass(s) for s in v)}/{len(v)} (non-401 shots), 401s={n401}")
        mine = [s for s in shots if s.ident == a.ident]
        # gap since own previous shot on the TCIN
        prev = {}
        gaps = defaultdict(Counter)
        for s in mine:
            g = s.t - prev.get(s.tcin, -1e9)
            prev[s.tcin] = s.t
            if s.cls == "401":
                continue
            for name, lo, hi in GAP_BUCKETS:
                if lo <= g < hi:
                    gaps[name]["pass" if is_pass(s) else "edge"] += 1
                    break
        print(f"  {a.ident}: passes by gap since OWN previous shot on the same TCIN")
        for name, _, _ in GAP_BUCKETS:
            c = gaps[name]
            if c:
                print(f"    {name:>8}: {c['pass']}/{c['pass'] + c['edge']}")
        # own-volume: shots by any identity on the TCIN in the prior 120 s
        priors = []
        dens = defaultdict(Counter)
        for s in mine:
            p = sum(1 for x in by_tcin[s.tcin] if s.t - 120 <= x.t < s.t)
            priors.append(p)
            if s.cls == "401":
                continue
            b = "0-4" if p <= 4 else ("5-8" if p <= 8 else "9+")
            dens[b]["pass" if is_pass(s) else "edge"] += 1
        if priors:
            print(f"  {a.ident}: median of OUR shots on the TCIN in the prior 120 s = {statistics.median(priors)}")
        for b in ("0-4", "5-8", "9+"):
            c = dens[b]
            if c:
                print(f"    prior {b:>3}: {c['pass']}/{c['pass'] + c['edge']}")
        print(f"  {a.ident}: every pass (time, tcin, class, gap since own prev, prior-120s any):")
        prev = {}
        for s in mine:
            g = s.t - prev.get(s.tcin, -1e9)
            prev[s.tcin] = s.t
            if is_pass(s):
                p = sum(1 for x in by_tcin[s.tcin] if s.t - 120 <= x.t < s.t)
                print(f"    {hhmmss(s.t)} {s.tcin} {s.cls:>7} gap={'idle' if g > 1e8 else f'{g:.0f}s'} prior={p} line={s.line}")


if __name__ == "__main__":
    main()

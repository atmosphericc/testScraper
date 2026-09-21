#!/usr/bin/env python3
"""Claim C-2026-09-17-03 (docs/CLAIMS.md): what the NEXT add-to-cart shot got
after an ATC-level DCO_RATE_LIMITED 429 ("high demand item"), by the gap, per
(identity, TCIN) sequence.

Usage:  python tools/analysis/dco_next_shot.py logs/runs/run_2026*.log

CAVEAT (and why this needs an independent re-derivation): logs before
2026-08-25 carry no identity tag, so a "next shot on the same TCIN" there may
belong to a DIFFERENT account thread that fired at the same flip. Pairs whose
gap is 0.0 s are the most suspect. Tagged logs (ident != '?') are clean.
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict

from shots import parse_shots, hhmmss

BUCKETS = (("<5s", 0, 5), ("5-15s", 5, 15), ("15-45s", 15, 45), ("45-90s", 45, 90), (">90s", 90, float("inf")))


def main(paths) -> None:
    tot = Counter()
    tot_tagged = Counter()
    rows = []
    for path in paths:
        seqs = defaultdict(list)
        for s in parse_shots(path):
            seqs[(s.ident, s.tcin)].append(s)
        for (idn, tcin), v in seqs.items():
            for a, b in zip(v, v[1:]):
                if a.cls != "dco429":
                    continue
                gap = b.t - a.t
                for name, lo, hi in BUCKETS:
                    if lo <= gap < hi:
                        tot[(name, b.cls)] += 1
                        if idn != "?":
                            tot_tagged[(name, b.cls)] += 1
                        break
                if b.cls in ("201", "dco429"):
                    rows.append((path[-19:-4], idn, tcin, hhmmss(a.t), round(gap, 1), b.cls, a.line, b.line))
    for label, t in (("ALL logs (July pairs are attribution-unknown)", tot), ("TAGGED logs only (ident known)", tot_tagged)):
        print(f"== next shot after a DCO 429, {label}")
        for name, _, _ in BUCKETS:
            d = {c: n for (bb, c), n in t.items() if bb == name}
            if d:
                n = sum(d.values())
                past = sum(v for c, v in d.items() if c not in ("edge429", "401"))
                print(f"   {name:>6}: n={n:>3} past-the-edge={past:>2} {dict(sorted(d.items()))}")
    print("== every DCO -> next-shot pair whose next shot was a 201 or another DCO:")
    print("   (drop, ident, tcin, t(DCO), gap_s, next, line(DCO), line(next))")
    for r in rows:
        print("  ", r)


if __name__ == "__main__":
    main(sys.argv[1:])

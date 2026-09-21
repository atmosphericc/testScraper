#!/usr/bin/env python3
"""Re-derivation of the 2026-09-09 census density claim with the shared parser:
for every fast-lane shot (all identities, all logs), count OUR shots (any
identity) on the same TCIN in the preceding 120 s, and report the edge-pass
rate and the 201 rate by that count (401s excluded from denominators).

Usage: python tools/analysis/census_density.py logs/runs/run_2026*.log
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict

from shots import parse_shots, is_pass


def main(paths) -> None:
    agg = defaultdict(Counter)
    per_log = {}
    for p in paths:
        shots = parse_shots(p)
        by_tcin = defaultdict(list)
        for s in shots:
            by_tcin[s.tcin].append(s.t)
        loc = defaultdict(Counter)
        for s in shots:
            if s.cls == "401":
                continue
            prior = sum(1 for t in by_tcin[s.tcin] if s.t - 120 <= t < s.t)
            b = "0" if prior == 0 else ("1-2" if prior <= 2 else ("3-4" if prior <= 4 else ("5-8" if prior <= 8 else "9+")))
            for d in (agg, loc):
                d[b]["n"] += 1
                d[b]["pass"] += is_pass(s)
                d[b]["201"] += (s.cls == "201")
        per_log[p[-19:-4]] = {b: f"{c['201']}/{c['pass']}/{c['n']}" for b, c in sorted(loc.items())}
    print("prior-120s own shots on the TCIN -> 201 / edge-pass / shots (all logs, 401s excluded)")
    for b in ("0", "1-2", "3-4", "5-8", "9+"):
        c = agg[b]
        if c["n"]:
            print(f"  {b:>4}: 201={c['201']:>3} ({100*c['201']/c['n']:.1f}%)  edge-pass={c['pass']:>4} ({100*c['pass']/c['n']:.1f}%)  n={c['n']}")
    print("per log (201/pass/n by bucket):")
    for k, v in per_log.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1:])

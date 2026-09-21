#!/usr/bin/env python3
"""Does firing MORE shots at one TCIN raise or lower TOTAL edge passes?

limiter_key.py showed the per-shot pass RATE collapses as shots pile onto a TCIN.
That alone does not decide the cadence question: Refract retries every 3500 ms and
says to "keep submitting and let it ride", betting that volume beats dilution.
The decision needs THROUGHPUT — passes per TCIN per bucket-window, as a function of
how many shots WE put in that window.

Method: slice every log into fixed WINDOW-second bins per TCIN, count our shots in
the bin (any identity, 401s excluded) and how many passed. Report mean passes per
bin at each volume level. If throughput keeps rising with volume, copy Refract's
cadence. If it peaks and falls, our wave-first instinct was right even though its
mechanism (a per-identity cooldown) was wrong.

Usage: python tools/analysis/throughput_vs_volume.py [--window 120] [logs ...]
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
    ap.add_argument("--hot-only", action="store_true")
    a = ap.parse_args()
    paths = a.logs or sorted(glob.glob("../../logs/runs/run_2026*.log"))

    bins = defaultdict(lambda: [0, 0])   # (path,tcin,binidx) -> [shots, passes]
    for p in paths:
        for s in parse_shots(p):
            if s.cls == "401":
                continue
            if a.hot_only and not is_hot(s.tcin):
                continue
            k = (p, s.tcin, int(s.t // a.window))
            bins[k][0] += 1
            bins[k][1] += int(is_pass(s))

    agg = defaultdict(lambda: [0, 0])    # volume bucket -> [n_bins, total_passes]
    def b(n):
        return "1" if n == 1 else ("2" if n == 2 else ("3-4" if n <= 4 else
               ("5-8" if n <= 8 else ("9-16" if n <= 16 else "17+"))))
    for (shots, passes) in bins.values():
        agg[b(shots)][0] += 1
        agg[b(shots)][1] += passes

    label = "HOT SKUs" if a.hot_only else "ALL SKUs"
    print(f"== {label}: TOTAL passes per {a.window:.0f}s window vs OUR shot volume in it ({len(paths)} logs)")
    print(f"{'shots/win':>10} {'windows':>8} {'passes':>7}  {'passes/win':>11}  {'pass rate':>9}")
    for k in ("1", "2", "3-4", "5-8", "9-16", "17+"):
        if k not in agg:
            continue
        nb, tp = agg[k]
        mid = {"1": 1, "2": 2, "3-4": 3.5, "5-8": 6.5, "9-16": 12.5, "17+": 22}[k]
        print(f"{k:>10} {nb:>8} {tp:>7}  {tp/nb:>11.3f}  {100*tp/(nb*mid):>8.1f}%")


if __name__ == "__main__":
    main()

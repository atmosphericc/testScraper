#!/usr/bin/env python3
"""Does firing MORE shots into one hot stock window help, or does our own
volume trip Target's per-TCIN edge limiter?

This is the measurement that decides whether to copy Refract's "keep submitting"
cadence (~3.5 s retry) or keep our wave-first policy (one cold re-entry every
55-70 s). The 2026-09-09 CENSUS said re-POSTing produced 0 orders from 13,244
attempts; wave-first was built on that. Refract's docs say the opposite. Only
our own logs can settle it for OUR stack.

For every hot-SKU shot we compute its INDEX within its identity's current
in-stock window (1 = the race-start shot, 2 = the next, ...) and report the
outcome mix per index. If our volume trips the limiter, the edge-429 share
climbs with the index and carts concentrate at index 1. If it does not, the
cart rate is flat and more tickets are strictly better.

A window closes for an (ident, tcin) when a `[RACE] <tcin>: racing` line resets
it, or after --gap seconds of silence on that pair (default 180 s, well past the
55-70 s wave-first re-entry so a cold re-entry stays INSIDE its window).

Usage: python tools/analysis/shot_index_yield.py [--gap 180] [logs/runs/run_2026*.log ...]
Read-only.
"""
from __future__ import annotations

import argparse
import glob
import re
from collections import Counter, defaultdict

from shots import TS, FIRE, DONE, IDENT
from funnel import is_hot

RACE = re.compile(r"\[RACE\] (\d+): racing \d+ accounts")
BURST = re.compile(r"\[DCO_BURST\] ATC-level FAST_SELLING on (\d+) .*?re-POST (\d+)/(\d+).*?ident=W\d/([\w-]+)")


def collect(path, gap_s):
    t, day0, last_tcin, last = 0.0, None, None, None
    idx = {}          # (ident,tcin) -> running index
    last_t = {}       # (ident,tcin) -> t of previous shot
    pending_burst = {}
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            m = TS.match(line)
            if m:
                if day0 is None:
                    day0 = m.group(1)
                t = int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4)) + int(m.group(5)) / 1000
                if m.group(1) != day0:
                    t += 86400
            r = RACE.search(line)
            if r:
                for k in list(idx):
                    if k[1] == r.group(1):
                        idx[k] = 0
            b = BURST.search(line)
            if b:
                pending_burst[b.group(4)] = (b.group(1), int(b.group(2)))
            f = FIRE.search(line)
            if f:
                last_tcin = f.group(1)
            d = DONE.search(line)
            if d:
                im = IDENT.search(line)
                idn = im.group(1) if im else "?"
                tcin = last_tcin or "?"
                key = (idn, tcin)
                if t - last_t.get(key, -1e9) > gap_s:
                    idx[key] = 0
                idx[key] = idx.get(key, 0) + 1
                last_t[key] = t
                pb = pending_burst.pop(idn, None)
                typ = f"burst{pb[1]}" if (pb and pb[0] == tcin) else ("race-start" if idx[key] == 1 else "re-entry")
                atc = d.group(2)
                last = {"tcin": tcin, "ident": idn, "atc": atc, "cls": atc,
                        "i": idx[key], "type": typ, "line": n}
                if atc == "429":
                    last["cls"] = "429?"
                out.append(last)
                continue
            if last is not None and last["cls"] == "429?" and "[PURCHASE] ATC fetch: rate-limited (429)" in line:
                last["cls"] = "dco429" if "DCO_RATE_LIMITED" in line else "edge429"
                last = None
    for s in out:
        if s["cls"] == "429?":
            s["cls"] = "edge429"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="*")
    ap.add_argument("--gap", type=float, default=180.0)
    ap.add_argument("--all-skus", action="store_true", help="include ordinary SKUs too")
    a = ap.parse_args()
    paths = a.logs or sorted(glob.glob("../../logs/runs/run_2026*.log"))

    by_i = defaultdict(Counter)
    by_type = defaultdict(Counter)
    for p in paths:
        for s in collect(p, a.gap):
            if not a.all_skus and not is_hot(s["tcin"]):
                continue
            bucket = str(s["i"]) if s["i"] <= 6 else "7+"
            by_i[bucket][s["cls"]] += 1
            by_i[bucket]["n"] += 1
            by_type[s["type"] if not s["type"].startswith("burst") else "burst"][s["cls"]] += 1
            by_type[s["type"] if not s["type"].startswith("burst") else "burst"]["n"] += 1

    label = "ALL SKUs" if a.all_skus else "HOT SKUs"
    print(f"== {label}: outcome by shot INDEX within a stock window ({len(paths)} logs, gap={a.gap:.0f}s)")
    print(f"{'idx':>4} {'shots':>7} {'cart201':>9} {'edge429':>9} {'dco429':>8} {'401':>7} {'other':>7}   cart%   edge%")
    order = [k for k in ("1", "2", "3", "4", "5", "6", "7+") if k in by_i]
    for k in order:
        c = by_i[k]
        n = c["n"]
        other = n - c["201"] - c["edge429"] - c["dco429"] - c["401"]
        print(f"{k:>4} {n:>7} {c['201']:>9} {c['edge429']:>9} {c['dco429']:>8} {c['401']:>7} {other:>7}"
              f"  {100*c['201']/n:5.2f}%  {100*c['edge429']/n:5.1f}%")

    print(f"\n== {label}: outcome by SHOT TYPE")
    print(f"{'type':>12} {'shots':>7} {'cart201':>9} {'edge429':>9} {'dco429':>8} {'401':>7}   cart%   edge%")
    for k in ("race-start", "re-entry", "burst"):
        if k not in by_type:
            continue
        c = by_type[k]
        n = c["n"]
        print(f"{k:>12} {n:>7} {c['201']:>9} {c['edge429']:>9} {c['dco429']:>8} {c['401']:>7}"
              f"  {100*c['201']/n:5.2f}%  {100*c['edge429']/n:5.1f}%")


if __name__ == "__main__":
    main()

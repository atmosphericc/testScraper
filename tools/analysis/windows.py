#!/usr/bin/env python3
"""Per stock-window picture of a run: for each TCIN, every in-stock window
(from the 30-s `[STOCK WATCH]` logger lines plus `[STOCK] IN STOCK:` edges) and
every fast-lane shot fired inside or near it, with its outcome, shot type and
how it was signed.

Usage:  python tools/analysis/windows.py logs/runs/run_<date>.log [--ident primary] [--min-shots 1]

Shot type: race-start = first shot by that identity since the last
`[RACE] <tcin>: racing` line; burst = a shot that follows a `[DCO_BURST] ...
re-POST n/m` line for that identity; re-entry = anything else (the wave-first
cold re-entry). Signing: banked = a `[HARVEST/<ident>] REPLAY on main shot`
line precedes the shot (with the set's age); page-signed = `bank EMPTY at shot
time`; '?' = neither line seen for this shot.

Read-only. Timestamps: only logger lines carry clocks; a print line takes the
nearest preceding one (so times can lag by seconds; the ORDER of lines is exact).
"""
from __future__ import annotations

import argparse
import re
from collections import defaultdict

from shots import TS, FIRE, DONE, IDENT, hhmmss

WATCH = re.compile(r"\[STOCK WATCH\] (\d+): in_stock=(True|False)")
EDGE = re.compile(r"^\[STOCK\] IN STOCK: (\d+)")
RACE = re.compile(r"\[RACE\] (\d+): racing \d+ accounts")
BURST = re.compile(r"\[DCO_BURST\] ATC-level FAST_SELLING on (\d+) .*?re-POST (\d+)/(\d+).*?ident=W\d/([\w-]+)")
REPLAY = re.compile(r"\[HARVEST/([\w-]+)\] REPLAY on main shot: banked set age=(\d+)s")
EMPTY = re.compile(r"\[HARVEST/([\w-]+)\] bank EMPTY at shot time")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--ident", default="primary")
    ap.add_argument("--min-shots", type=int, default=1)
    a = ap.parse_args()

    t = 0.0
    day0 = None
    last_tcin = None
    watch = defaultdict(list)          # tcin -> [(t, bool)]
    edges = defaultdict(list)          # tcin -> [t]
    shots = []                         # dict per shot
    since_race = defaultdict(int)      # (ident, tcin) -> shots since the last race line
    pending_burst = {}                 # ident -> (tcin, n)
    pending_sign = {}                  # ident -> 'banked(age)' | 'page-signed'
    last = None
    with open(a.log, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            m = TS.match(line)
            if m:
                if day0 is None:
                    day0 = m.group(1)
                t = int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4)) + int(m.group(5)) / 1000
                if m.group(1) != day0:
                    t += 86400
            w = WATCH.search(line)
            if w:
                watch[w.group(1)].append((t, w.group(2) == "True"))
            e = EDGE.match(line)
            if e:
                edges[e.group(1)].append(t)
            r = RACE.search(line)
            if r:
                for k in list(since_race):
                    if k[1] == r.group(1):
                        since_race[k] = 0
            b = BURST.search(line)
            if b:
                pending_burst[b.group(4)] = (b.group(1), int(b.group(2)))
            rp = REPLAY.search(line)
            if rp:
                pending_sign[rp.group(1)] = f"banked({rp.group(2)}s)"
            em = EMPTY.search(line)
            if em:
                pending_sign[em.group(1)] = "page-signed"
            f = FIRE.search(line)
            if f:
                last_tcin = f.group(1)
            d = DONE.search(line)
            if d:
                im = IDENT.search(line)
                idn = im.group(1) if im else "?"
                tcin = last_tcin or "?"
                since_race[(idn, tcin)] += 1
                typ = "race-start" if since_race[(idn, tcin)] == 1 else "re-entry"
                pb = pending_burst.pop(idn, None)
                if pb and pb[0] == tcin:
                    typ = f"burst{pb[1]}"
                atc = d.group(2)
                last = {"t": t, "ident": idn, "tcin": tcin, "atc": atc, "cls": atc, "type": typ,
                        "sign": pending_sign.pop(idn, "?"), "chain": float(d.group(1)), "line": n}
                if atc == "429":
                    last["cls"] = "429?"
                shots.append(last)
                continue
            if last is not None and last["cls"] == "429?" and "[PURCHASE] ATC fetch: rate-limited (429)" in line:
                last["cls"] = "dco429" if "DCO_RATE_LIMITED" in line else "edge429"
                last = None

    # windows from WATCH lines (30 s cadence): consecutive True readings
    print(f"== stock windows and {a.ident}'s shots ({a.log})")
    for tcin in sorted(watch):
        wins = []
        cur = None
        for tt, ok in watch[tcin]:
            if ok and cur is None:
                # a flip edge logged shortly before the first True watch line is the better start
                starts = [x for x in edges.get(tcin, []) if tt - 45 <= x <= tt]
                cur = [min(starts) if starts else tt, tt]
            elif ok:
                cur[1] = tt
            elif cur is not None:
                wins.append((cur[0], tt))
                cur = None
        if cur is not None:
            wins.append((cur[0], cur[1] + 30))
        mine = [s for s in shots if s["tcin"] == tcin and s["ident"] == a.ident]
        if len(mine) < a.min_shots and not wins:
            continue
        print(f"\n-- {tcin}: {len(wins)} window(s), {len(mine)} {a.ident} shot(s), "
              f"all identities {len([s for s in shots if s['tcin'] == tcin])}")
        for ws, we in wins:
            inw = [s for s in mine if ws - 5 <= s["t"] <= we + 5]
            print(f"   window {hhmmss(ws)} -> {hhmmss(we)} ({we - ws:.0f}s): {len(inw)} {a.ident} shot(s)")
            for s in inw:
                print(f"      {hhmmss(s['t'])} +{s['t'] - ws:5.0f}s {s['type']:<10} {s['cls']:<8} {s['sign']:<13} "
                      f"chain={s['chain']:.2f}s line={s['line']}")
        outside = [s for s in mine if not any(ws - 5 <= s["t"] <= we + 5 for ws, we in wins)]
        if outside:
            print(f"   outside any 30-s-watch window (flickers): {len(outside)} shot(s): "
                  + ", ".join(f"{hhmmss(s['t'])} {s['type']} {s['cls']}" for s in outside[:12]))
    # summary by type and signing
    mine = [s for s in shots if s["ident"] == a.ident]
    print(f"\n== {a.ident}: outcome by shot type / signing (401s shown, not excluded)")
    for key in ("type", "sign"):
        agg = defaultdict(lambda: defaultdict(int))
        for s in mine:
            k = s[key] if key == "type" else s[key].split("(")[0]
            agg[k][s["cls"]] += 1
        for k in sorted(agg):
            print(f"   {key}={k:<12} {dict(agg[k])}")


if __name__ == "__main__":
    main()

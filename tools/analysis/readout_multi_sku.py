#!/usr/bin/env python3
"""Pre-registered readout for D1 (multi-SKU dispatch), 2026-09-20.

Registered BEFORE the flag was armed so the verdict cannot be chosen after the
fact (docs/CLAIMS.md rule). Answers, from one run log:

  1. Did D1 fire at all?  [MULTI_SKU_DISPATCH] lines, and how many distinct
     TCINs were pursued concurrently.
  2. What did it buy?  Shots and edge passes on the SECOND-and-later TCIN of a
     concurrent pair - those shots did not exist before D1 (they were
     [MULTI_SKU_MISS]).
  3. Did it cost anything?  Carts/passes on the FIRST TCIN, versus nights where
     the fleet was undivided. A drop here is the risk the change carries.
  4. Safety: did any worker ever hold two TCINs, and did the stale sweep fire?

VERDICT RULES, fixed in advance:
  PASS  - concurrent TCINs pursued, >=1 edge pass on a second TCIN, and ZERO
          double-held workers.
  NEUTRAL - fired but no second-TCIN pass yet (needs more drop nights).
  FAIL  - any double-held worker, or first-TCIN pass rate falls below half the
          pre-D1 baseline on comparable windows.

Usage: python tools/analysis/readout_multi_sku.py logs/runs/run_<date>.log
Read-only.
"""
from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict

from shots import parse_shots, is_pass, TS
from funnel import is_hot

DISPATCH = re.compile(r"\[MULTI_SKU_DISPATCH\] (\d+): reserved \[([^\]]*)\]")
PURSUE = re.compile(r"\[MULTI_SKU_DISPATCH\] (\d+) in-stock — pursuing alongside '(\d+)'")
MISS = re.compile(r"\[MULTI_SKU_MISS\] (\d+) in-stock but SKIPPED")
STALE = re.compile(r"\[MULTI_SKU_DISPATCH\] released stale reservation (\S+) -> (\d+)")
NOFREE = re.compile(r"\[MULTI_SKU_DISPATCH\] (\d+): no unreserved worker")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    a = ap.parse_args()

    reserved = []          # (tcin, [labels])
    pursued = []           # (second_tcin, first_tcin)
    misses = Counter()
    stale = []
    nofree = Counter()
    held = {}              # label -> tcin  (running view, for the safety check)
    double_held = []

    with open(a.log, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = DISPATCH.search(line)
            if m:
                labels = [x.strip().strip("'\"") for x in m.group(2).split(",") if x.strip()]
                reserved.append((m.group(1), labels))
                for lbl in labels:
                    prev = held.get(lbl)
                    if prev is not None and prev != m.group(1):
                        double_held.append((lbl, prev, m.group(1)))
                    held[lbl] = m.group(1)
                continue
            m = PURSUE.search(line)
            if m:
                pursued.append((m.group(1), m.group(2)))
                continue
            m = MISS.search(line)
            if m:
                misses[m.group(1)] += 1
                continue
            m = STALE.search(line)
            if m:
                stale.append((m.group(1), m.group(2)))
                held.pop(m.group(1), None)
                continue
            m = NOFREE.search(line)
            if m:
                nofree[m.group(1)] += 1

    print(f"== D1 multi-SKU dispatch readout ({a.log})")
    print(f"\n1. DID IT FIRE?")
    print(f"   reservations taken       : {len(reserved)}")
    print(f"   concurrent pursuits      : {len(pursued)}")
    print(f"   distinct TCINs reserved  : {len({t for t, _ in reserved})}")
    print(f"   'no unreserved worker'   : {sum(nofree.values())} across {len(nofree)} TCIN(s)")
    print(f"   MULTI_SKU_MISS remaining : {sum(misses.values())} across {len(misses)} TCIN(s)")
    if pursued:
        print("   pairs (second <- first):")
        for sec, first in pursued[:15]:
            print(f"     {sec} alongside {first}{'  [HOT]' if is_hot(sec) else ''}")

    # 2/3. what the second TCINs actually produced
    second_tcins = {s for s, _ in pursued}
    first_tcins = {f for _, f in pursued}
    shots = parse_shots(a.log)
    agg = defaultdict(lambda: Counter())
    for s in shots:
        if s.cls == "401":
            continue
        role = ("second" if s.tcin in second_tcins else
                "first" if s.tcin in first_tcins else "uninvolved")
        agg[role]["n"] += 1
        agg[role]["pass"] += int(is_pass(s))
        agg[role]["cart"] += int(s.cls == "201")

    print(f"\n2/3. WHAT IT BOUGHT (401s excluded)")
    print(f"   {'role':>11} {'shots':>7} {'passes':>7} {'carts':>6}  pass%")
    for role in ("first", "second", "uninvolved"):
        c = agg.get(role)
        if not c or not c["n"]:
            continue
        print(f"   {role:>11} {c['n']:>7} {c['pass']:>7} {c['cart']:>6}  "
              f"{100*c['pass']/c['n']:5.1f}%")
    if not second_tcins:
        print("   (no second TCIN pursued in this run)")

    print(f"\n4. SAFETY")
    print(f"   workers double-held      : {len(double_held)}  <-- MUST BE 0")
    for lbl, a_t, b_t in double_held[:10]:
        print(f"     !! {lbl} held {a_t} and {b_t}")
    print(f"   stale reservations swept : {len(stale)}")

    verdict = ("FAIL — a worker was held by two TCINs" if double_held else
               "NEUTRAL — D1 never fired this run" if not pursued else
               "PASS — concurrent pursuit, no double-hold"
               if agg["second"]["pass"] else
               "NEUTRAL — fired, but no second-TCIN edge pass yet")
    print(f"\nVERDICT: {verdict}")


if __name__ == "__main__":
    main()

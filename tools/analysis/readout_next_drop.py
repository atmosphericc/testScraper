#!/usr/bin/env python3
"""Pre-registered readout for the first drop after 2026-09-17 (docs/CLAIMS.md rule 3).

Run it on the run log BEFORE writing any narrative:
    python tools/analysis/readout_next_drop.py logs/runs/run_<date>.log [--hot 1010892069,...]

It judges each change armed on 2026-09-17 against criteria fixed in advance:

  R1 business park   PASS if the [PARK] boot line names business AND business fired
                     0 shots on hot TCINs (sits out with account_parked_hot).
  R2 DCO burst       Reports every [DCO_BURST] and the burst re-POSTs' outcomes.
                     PASS (C-0917-03 live-confirmed) if >= 1 burst re-POST got past the
                     edge (dco429/201/other) within 15 s of the DCO; STRONG if any burst
                     re-POST is a 201. FAIL if >= 6 burst re-POSTs fired and 0 got past
                     the edge.
  R3 density         primary's hot-TCIN edge pass rate (strict: DCO/201 only; 431/503
                     are not passes) split by own prior shots in 120 s (0-2 vs >= 3)
                     and by shot type: race-start (attempt 1 of a race) vs re-entry
                     (the wave-first solo cold re-entry). Verified baselines (C-0917-08):
                     race-start 8/267, re-entry 1/273 across 09-11+09-16; primary strict
                     5/27 on 09-11 and 4/181 on 09-16. PASS (C-0917-02 live-confirmed)
                     if primary's strict rate with business parked is >= 3x 09-16's
                     (>= 6.7%) on >= 40 non-401 shots; FAIL if <= 2.5% on >= 100.
  R4 won-cart cadence  per won cart: [WON_CART_DIRECT] start, [FS_TICKET] tickets, gaps,
                     how many fired while the TCIN read live, and whether an FS 429 was
                     ever followed by a 200 on the same cart. PASS if >= 6 tickets fired
                     inside the live window of a won cart (09-16 fired 2).
  R5 orders          count of orders and their path.
  R6 dead-cart/timing (armed 2026-09-18): <= 2 tickets after the first po 424 / keyless
                     pre 400 on a cart (09-18: 32); >= 4 gate draws in a won cart's first
                     ~5 s (09-18: 1-2); a [FS_TICKET_BODY] line for every 424/400/RF ticket.

Everything is read-only over the log. Prints a table per rule and a final verdict block.
"""
from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict

from shots import parse_shots, is_pass, hhmmss, TS

DEFAULT_HOT = ("1010892078,1010892076,1010892069,1010892067,1010892068,1010892065,"
               "1012422107,1011407490,1010892075,1010892071,1012055696,1011960739,1011209279")


_DAY0 = [None]


def _ts(line, cur):
    """Same clock as shots.parse_shots: seconds since the log's first day 00:00,
    with the day rollover added, so race lines and shots compare correctly."""
    m = TS.match(line)
    if m:
        if _DAY0[0] is None:
            _DAY0[0] = m.group(1)
        t = int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4)) + int(m.group(5)) / 1000
        if m.group(1) != _DAY0[0]:
            t += 86400
        return t
    return cur


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--hot", default=DEFAULT_HOT)
    a = ap.parse_args()
    hot = set(t for t in a.hot.split(",") if t.strip())
    shots = parse_shots(a.log)
    verdict = {}

    # ---- R1 business park -------------------------------------------------
    park_line = ""
    race_starts = {}       # tcin -> [race start times]
    sits = Counter()
    burst_lines = []       # (t, ident, tcin, n, max)
    burst_caps = 0
    won_starts = []
    fs_tickets = []        # (t, ident, tcin, n, gap, live, status, key)
    orders = []
    t = 0.0
    with open(a.log, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            t = _ts(line, t)
            if "[PARK] TARGET_PARK_ACCOUNT_TCINS:" in line and not park_line:
                park_line = line.strip()
            m = re.search(r"\[REAL_PURCHASE_THREAD\] (W\d/[\w-]+) sits out (\d+): account_parked_hot", line)
            if m:
                sits[(m.group(1), m.group(2))] += 1
            m = re.search(r"\[DCO_BURST\] ATC-level FAST_SELLING on (\d+) .*?re-POST (\d+)/(\d+) .*?ident=([\w/-]+)", line)
            if m:
                burst_lines.append((t, m.group(4), m.group(1), int(m.group(2)), int(m.group(3)), n))
            if "[DCO_BURST] cap" in line:
                burst_caps += 1
            if "[WON_CART_DIRECT] start" in line:
                won_starts.append((t, line.strip()[:160]))
            m = re.search(r"\[RACE\] (\d+): racing \d+ accounts", line)
            if m:
                race_starts.setdefault(m.group(1), []).append(t)
            m = re.search(r"\[FS_TICKET\] ident=([\w/-]+) tcin=(\d+) .*?n=(\d+) cls=(\w+) gap_s=([\d.\-]+) .*?live=(\w+) .*?status=(\d+) key=([A-Z_\-]+)", line)
            if m:
                fs_tickets.append((t, m.group(1), m.group(2), int(m.group(3)), m.group(4), m.group(5), m.group(6), m.group(7), m.group(8)))
            if "Order placed" in line or "ORDER PLACED" in line:
                orders.append((hhmmss(t), line.strip()[:140]))

    print("== R1 business park")
    print("   boot line:", park_line or "MISSING")
    biz_hot = [s for s in shots if s.ident == "business" and s.tcin in hot]
    biz_sits = sum(v for (w, tc), v in sits.items() if w.endswith("business") and tc in hot)
    print(f"   business shots on hot TCINs: {len(biz_hot)}; sit-outs on hot TCINs: {biz_sits}")
    verdict["R1 park"] = "PASS" if ("business on" in park_line and len(biz_hot) == 0) else "FAIL"

    # ---- R2 DCO burst -----------------------------------------------------
    print("== R2 DCO burst")
    by_seq = defaultdict(list)
    for s in shots:
        by_seq[(s.ident, s.tcin)].append(s)
    past = 0
    got201 = 0
    fired = 0
    for (bt, idn, tcin, k, kmax, ln) in burst_lines:
        seq = by_seq[(idn.split("/")[-1], tcin)]
        nxt = [s for s in seq if s.t > bt and s.t - bt <= 15]
        nx = nxt[0] if nxt else None
        if nx:
            fired += 1
            if is_pass(nx):
                past += 1
            if nx.cls == "201":
                got201 += 1
        print(f"   {hhmmss(bt)} {idn:>12} {tcin} burst {k}/{kmax} -> next shot "
              f"{(nx.cls + ' +' + format(nx.t - bt, '.1f') + 's') if nx else 'none within 15 s'}")
    print(f"   burst lines={len(burst_lines)} caps={burst_caps} re-POSTs seen={fired} past-the-edge={past} 201={got201}")
    if not burst_lines:
        verdict["R2 burst"] = "NO DATA (no DCO 429 this run)"
    elif got201:
        verdict["R2 burst"] = "STRONG PASS (a burst re-POST carted)"
    elif past >= 1:
        verdict["R2 burst"] = "PASS (edge re-admitted a burst re-POST)"
    elif fired >= 6:
        verdict["R2 burst"] = "FAIL (>= 6 burst re-POSTs, none past the edge)"
    else:
        verdict["R2 burst"] = "INCONCLUSIVE (< 6 burst re-POSTs)"

    # ---- R3 density -------------------------------------------------------
    print("== R3 primary hot-TCIN edge passes")
    prim = [s for s in shots if s.ident == "primary" and s.tcin in hot]
    nz = [s for s in prim if s.cls != "401"]
    def strict(s):
        return s.cls in ("dco429", "201")
    tot_pass = sum(strict(s) for s in nz)
    kind = Counter()
    dens = Counter()
    seen_since_race = {}   # tcin -> primary shots since the last race start
    for s in prim:
        starts = [r for r in race_starts.get(s.tcin, []) if r <= s.t + 0.5]
        last_race = starts[-1] if starts else None
        key = (s.tcin, last_race)
        seen_since_race[key] = seen_since_race.get(key, 0) + 1
        if s.cls == "401":
            continue
        typ = "race-start" if seen_since_race[key] == 1 else "re-entry"
        own = sum(1 for x in prim if x.tcin == s.tcin and s.t - 120 <= x.t < s.t)
        kind[typ] += 1
        kind[typ + "_pass"] += strict(s)
        dens["0-2" if own <= 2 else ">=3"] += 1
        dens[("0-2" if own <= 2 else ">=3") + "_pass"] += strict(s)
    print(f"   overall (strict DCO/201): {tot_pass}/{len(nz)} non-401 shots ({(100.0 * tot_pass / len(nz)) if nz else 0:.1f}%)  401s={len(prim) - len(nz)}")
    print(f"   race-start: {kind['race-start_pass']}/{kind['race-start']}   re-entry: {kind['re-entry_pass']}/{kind['re-entry']}   (C-0917-08 baseline 8/267 vs 1/273)")
    print(f"   own prior 0-2: {dens['0-2_pass']}/{dens['0-2']}   own prior >=3: {dens['>=3_pass']}/{dens['>=3']}")
    rate = (tot_pass / len(nz)) if nz else 0.0
    if len(nz) >= 40 and rate >= 0.067:
        verdict["R3 density"] = "PASS (>= 3x the 09-16 rate)"
    elif len(nz) >= 100 and rate <= 0.025:
        verdict["R3 density"] = "FAIL (no better than 09-16)"
    else:
        verdict["R3 density"] = f"INCONCLUSIVE ({tot_pass}/{len(nz)})"

    # ---- R4 won-cart cadence ---------------------------------------------
    print("== R4 won-cart cadence")
    for wt, wl in won_starts:
        print("   ", hhmmss(wt), wl)
    per_cart = defaultdict(list)
    for tk in fs_tickets:
        per_cart[(tk[1], tk[2])].append(tk)
    live_max = 0
    fs_then_200 = False
    # tuple: (t, ident, tcin, n, cls, gap, live, status, key)
    for key, v in per_cart.items():
        v.sort()
        live_n = sum(1 for tk in v if tk[6] == "True")
        live_max = max(live_max, live_n)
        seen_fs = False
        for tk in v:
            if seen_fs and tk[7] == "200":
                fs_then_200 = True
            if "FAST_SELLING" in tk[8]:
                seen_fs = True
        gate_open = [tk[3] for tk in v if tk[8] in ("RESERVATION_FAILURE",) or tk[7] in ("200", "201")]
        print(f"   cart {key}: tickets={len(v)} live={live_n} gaps={[tk[5] for tk in v][:24]}")
        print(f"      statuses={[tk[7] for tk in v][:24]}")
        print(f"      keys={[tk[8][:4] for tk in v][:24]}  gate-open tickets (RF/2xx)={gate_open}")
    if not won_starts and not fs_tickets:
        verdict["R4 won-cart"] = "NO DATA (no cart won this run)"
    else:
        verdict["R4 won-cart"] = ("PASS" if live_max >= 6 else "FAIL") + f" (max {live_max} tickets inside a live window; FS->200 on same cart: {fs_then_200})"

    # ---- R6 dead-cart handling + checkout timing (rules armed 2026-09-18) ----
    # Pre-registered: (a) after the first po 424 / pre 400 on a cart, at most 2 further
    # tickets may fire on it (09-18 baseline: 32); (b) a won cart gets >= 4 gate draws in
    # its first 5 s (09-18 baseline: 1-2); (c) every RESERVATION_FAILURE / 424 / 400
    # ticket has a [FS_TICKET_BODY] line.
    print("== R6 dead-cart handling + checkout timing")
    emptied = presumed = held_presumed = bodies = 0
    with open(a.log, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "Target emptied the cart" in line:
                emptied += 1
            elif "presuming evicted" in line:
                presumed += 1
            elif "presuming the held" in line or "(second read) — marker" in line:
                held_presumed += 1
            elif line.startswith("[FS_TICKET_BODY]"):
                bodies += 1
    print(f"   eviction proven by read={emptied} presumed(unreadable twice)={presumed} "
          f"held marker dropped={held_presumed} FS_TICKET_BODY lines={bodies}")
    worst_after = 0
    min_early = None
    need_body = 0
    for key, v in per_cart.items():
        v.sort()
        first_dead = next((i for i, tk in enumerate(v)
                           if tk[7] == "424" or (tk[7] == "400" and tk[8] in ("-", ""))), None)
        after = (len(v) - 1 - first_dead) if first_dead is not None else 0
        worst_after = max(worst_after, after)
        need_body += sum(1 for tk in v if tk[7] in ("424", "400") or "RESERVATION" in tk[8])
        t0 = v[0][0]
        # ms_since_201 is not parsed here; approximate the first 5 s from the first ticket's clock
        early = sum(1 for tk in v if tk[0] - t0 <= 4.0) + 1      # +1 = the chain's own pre_checkout
        min_early = early if min_early is None else min(min_early, early)
        print(f"   cart {key}: tickets after the first 424/keyless-400 = {after}; gate draws in the first ~5 s = {early}")
    if per_cart:
        ok_a = worst_after <= 2
        ok_b = (min_early or 0) >= 4
        ok_c = bodies >= need_body
        verdict["R6 dead-cart"] = (f"{'PASS' if ok_a else 'FAIL'} (max {worst_after} tickets after a dead-cart signal) / "
                                   f"draws {'PASS' if ok_b else 'FAIL'} (min {min_early} in ~5 s) / "
                                   f"bodies {'PASS' if ok_c else 'FAIL'} ({bodies}/{need_body})")
    else:
        verdict["R6 dead-cart"] = "NO DATA (no cart won this run)"

    # ---- R5 orders --------------------------------------------------------
    print("== R5 orders")
    for o in orders:
        print("   ", *o)
    verdict["R5 orders"] = f"{len(orders)} order line(s)"

    print("\n== VERDICT")
    for k, v in verdict.items():
        print(f"   {k:<14} {v}")


if __name__ == "__main__":
    main()

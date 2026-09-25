#!/usr/bin/env python3
"""Pre-registered readout for the 2026-09-25 03:00 drop (docs/CLAIMS.md rule 3).

REGISTERED 2026-09-24 ~23:59 CDT, BEFORE the run. The criteria below are fixed in
advance so tomorrow's narrative cannot be fitted to tomorrow's data.

    python tools/analysis/readout_2026_09_25.py logs/runs/run_<boot>.log

It first runs the arming readout, tools/analysis/readout_arm_2026_09_23.py
(T0-T6: A1 WAVE_FIRST_EDGE=0, A2 WORKERS_PER_TCIN=3, A3 RETRY_STOP_WHEN_OOS=1,
G1 STUCK_RESET_LIVE_GUARD=1 -- armed 09-23, first live night tonight; its kill
rule for A1 is T4), then adds the drop-list rules below. Nothing else was armed.

DROP LIST (operator, 09-24 -- "make sure these are included, don't delete any"):
all seven were already present and enabled with qty 2; none had to be added.
ALSO ENABLED and kept: 1010892078, 1010892069, 1011960739 (10 enabled total).

TESTS
  D1 VISIBILITY   PASS  every "[GROUND-TRUTH] pool cache-bust ok ... (N TCINs)"
                        reads N == 10, and no [TCIN-VISIBILITY] INVISIBLE line
                        names a drop-list TCIN.
                  FAIL  any N != 10, or a drop-list TCIN named invisible.
                  (An UNKNOWN banner = ground-truth reads failing: reported, and
                  the verdict is INCONCLUSIVE rather than PASS.)
  D2 PRIORITY     Config list order is dispatch priority, but only inside ONE
                  stock update: bulletproof_purchase_manager.py sorts the event's
                  stock_data by list position (:4237) and walks it (:4278). A real
                  go-live is always a one-TCIN update (C-0924-02: 0 of 906 lines
                  ever named two), so NO DATA is the expected verdict.
                  An update = one "[STOCK] IN STOCK: a, b" line and the lines
                  up to the next one.
                  FAIL  inside one update naming 2+ TCINs, a [RACE] of a
                        lower-listed TCIN precedes a [RACE] of a higher-listed one,
                        or a higher-listed TCIN gets [MULTI_SKU_MISS] in favour of
                        a lower-listed TCIN that was raced IN THAT SAME update. (A
                        holder already racing from an earlier update is arrival
                        order, not an inversion.)
                  PASS  multi-TCIN updates occurred and none did that.
                  NO DATA  no update named 2+ TCINs (every go-live arrived as its
                        own event, so arrival order -- not list order -- decided).
  D3 SKIPPED-SKU COVERAGE (measured; nothing armed for it). docs/CLAIMS.md
                  C-0924-01, VERIFIED 09-24 for the pool-healthy regime: a TCIN
                  skipped with [MULTI_SKU_MISS] is NOT re-raced while it stays in
                  stock (23 historical skips, 0 same-window races). Expect LOST
                  unless it flickers or the blind-pool tab-fetch fallback runs.
                  A RACED episode under a healthy pool would contradict the claim.
                  For every MISS of TCIN B in favour of a DIFFERENT TCIN (a
                  '<B>#W<n>' holder is B's own racer, not contention), classify by
                  file line order: RACED (a "[RACE] B: racing" follows before B
                  next reads in_stock=False), LOST (B reads in_stock=False first),
                  OPEN (neither before end of log). Reported per drop-list TCIN.
  D4 SCOREBOARD   per enabled TCIN: in-stock transitions, races and their widths,
                  MISS count, shots ([EXPOSURE]) by kind, units_bought.

Read-only over the log. Stdlib only. Markers are print-only (no timestamp) and can
be glued to the next line, so they are searched anywhere in a line, never anchored.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

DROP_LIST = ['1010892076', '1012644667', '1010892067', '1012644665',
             '1012644666', '1010892065', '95120834']
# config/product_config.json enabled order AT REGISTRATION (list position =
# dispatch priority). If the config order changes before the run, update this
# line in the same commit and say so in docs/CLAIMS.md.
REGISTERED_ORDER = ['1010892076', '1010892067', '1010892065', '1010892078',
                    '1010892069', '1011960739', '95120834', '1012644667',
                    '1012644666', '1012644665']
N_ENABLED = len(REGISTERED_ORDER)

T = r'(\d{8,10})(?=\d{4}-\d\d-\d\d|\D|$)'   # a TCIN; tolerates a glued log date
R_GT = re.compile(r'\[GROUND-TRUTH\] pool cache-bust ok: in_stock=\[[^\]]*\] \((\d+) TCINs\)')
R_VIS_INV = re.compile(r'\[TCIN-VISIBILITY\] \d+ of \d+ configured TCIN\(s\) are INVISIBLE[^\[]*\[([^\]]*)')
R_VIS_UNK = re.compile(r'\[TCIN-VISIBILITY\] UNKNOWN')
R_INSTOCK = re.compile(r'\[STOCK\] IN STOCK: ')
R_LIST_ITEM = re.compile(T + r'(?:, )?')
R_WATCH = re.compile(r'\[STOCK WATCH\] ' + T + r': in_stock=(True|False)')
R_RACE = re.compile(r'\[RACE\] ' + T + r': racing (\d+) accounts')
R_UNITS = re.compile(r'\[RACE\] ' + T + r': \d+/\d+ accounts done, units_bought=(\d+),')
R_MISS = re.compile(r"\[MULTI_SKU_MISS\] " + T + r" in-stock but SKIPPED \S+ '([^']*)' holds the fleet")
R_EXPO = re.compile(r'\[EXPOSURE\] ident=(\S+) tcin=' + T + r' kind=(\S+)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('log')
    ap.add_argument('--skip-arm', action='store_true',
                    help='do not run readout_arm_2026_09_23.py first')
    a = ap.parse_args()

    if not a.skip_arm:
        arm = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'readout_arm_2026_09_23.py')
        sys.stdout.flush()
        subprocess.call([sys.executable, arm, a.log])
        print('')

    rank = {t: i for i, t in enumerate(REGISTERED_ORDER)}
    gt_counts = Counter()
    vis_invisible = []
    vis_unknown = 0
    transitions = Counter()
    races = defaultdict(list)          # tcin -> [N, ...]
    units = Counter()
    misses = Counter()
    shots = defaultdict(Counter)       # tcin -> kind -> n
    multi_updates = 0
    d2_fail = []
    cur_update = set()                 # TCINs named by the latest [STOCK] IN STOCK line
    raced_in_update = []               # TCINs raced since that line, in order
    pending = {}                       # B -> MISS lines in its open contention episode
    d3 = defaultdict(Counter)          # B -> RACED / LOST / OPEN episodes
    d3_lines = Counter()               # B -> contention MISS lines

    with open(a.log, 'r', encoding='utf-8', errors='replace') as fh:
        for line in fh:
            for m in R_GT.finditer(line):
                gt_counts[int(m.group(1))] += 1
            for m in R_VIS_INV.finditer(line):
                vis_invisible.extend(re.findall(r'\d{8,10}', m.group(1)))
            if R_VIS_UNK.search(line):
                vis_unknown += 1
            for m in R_INSTOCK.finditer(line):
                pos, ts = m.end(), []
                while True:
                    mm = R_LIST_ITEM.match(line, pos)
                    if not mm:
                        break
                    ts.append(mm.group(1))
                    pos = mm.end()
                for t in ts:
                    transitions[t] += 1
                cur_update, raced_in_update = set(ts), []
                if len(ts) >= 2:
                    multi_updates += 1
            for m in R_MISS.finditer(line):
                b, holder = m.group(1), m.group(2).split('#')[0]
                misses[b] += 1
                if holder == b:
                    continue            # its own racer key: a self-block, not contention
                if (len(cur_update) >= 2 and b in cur_update and holder in raced_in_update
                        and rank.get(b, 99) < rank.get(holder, 99)):
                    d2_fail.append((b, holder))
                pending[b] = pending.get(b, 0) + 1
                d3_lines[b] += 1
            for m in R_RACE.finditer(line):
                t = m.group(1)
                races[t].append(int(m.group(2)))
                if len(cur_update) >= 2 and t in cur_update:
                    for prior in raced_in_update:
                        if rank.get(t, 99) < rank.get(prior, 99):
                            d2_fail.append((t, prior))
                raced_in_update.append(t)
                if t in pending:
                    pending.pop(t)
                    d3[t]['RACED'] += 1
            for m in R_WATCH.finditer(line):
                t = m.group(1)
                if m.group(2) == 'False' and t in pending:
                    pending.pop(t)
                    d3[t]['LOST'] += 1
            for m in R_UNITS.finditer(line):
                units[m.group(1)] += int(m.group(2))
            for m in R_EXPO.finditer(line):
                shots[m.group(2)][m.group(3)] += 1
    for t in pending:
        d3[t]['OPEN'] += 1

    out = []
    w = out.append
    w('=' * 78)
    w('PRE-REGISTERED READOUT -- 2026-09-25 drop list   log: %s' % a.log)
    w('=' * 78)

    w('')
    w('D1  VISIBILITY (expect every ground-truth read = %d TCINs)' % N_ENABLED)
    w('    ground-truth reads by TCIN count: %s' % (dict(sorted(gt_counts.items())) or '(none)'))
    bad_vis = sorted(set(vis_invisible) & set(DROP_LIST))
    if vis_invisible:
        w('    INVISIBLE named: %s   (drop-list: %s)' % (sorted(set(vis_invisible)), bad_vis or 'none'))
    if vis_unknown:
        w('    [TCIN-VISIBILITY] UNKNOWN banners: %d' % vis_unknown)
    if not gt_counts:
        v1 = 'NO DATA -- no ground-truth read reached the log'
    elif bad_vis or any(n != N_ENABLED for n in gt_counts):
        v1 = 'FAIL -- %s' % ('drop-list TCIN invisible: %s' % bad_vis if bad_vis
                             else 'a read returned != %d TCINs' % N_ENABLED)
    elif vis_unknown:
        v1 = 'INCONCLUSIVE -- ground-truth reads failed at some point (UNKNOWN banner)'
    else:
        v1 = 'PASS'
    w('    VERDICT: %s' % v1)

    w('')
    w('D2  PRIORITY (list order inside one stock update)')
    w('    stock updates naming 2+ TCINs: %d' % multi_updates)
    for b, h in d2_fail[:10]:
        w('      higher-listed %s lost the fleet to lower-listed %s in one update' % (b, h))
    if d2_fail:
        v2 = 'FAIL -- %d inversion(s)' % len(d2_fail)
    elif multi_updates:
        v2 = 'PASS'
    else:
        v2 = 'NO DATA -- every go-live arrived as its own event (arrival order decided)'
    w('    VERDICT: %s' % v2)

    w('')
    w('D3  SKIPPED-SKU COVERAGE (MISS in favour of a different TCIN)')
    tot = Counter()
    for t in sorted(d3, key=lambda k: rank.get(k, 99)):
        c = d3[t]
        tot.update(c)
        w('      %-12s %s episodes: RACED=%d LOST=%d OPEN=%d   (MISS lines %d)' % (
            t, '(drop)' if t in DROP_LIST else '      ', c['RACED'], c['LOST'], c['OPEN'],
            d3_lines[t]))
    if not d3:
        w('    no contention MISS lines -- nothing to classify')
    else:
        w('    total episodes: RACED=%d LOST=%d OPEN=%d (measured; LOST = skipped and never '
          'raced before it read out of stock)' % (tot['RACED'], tot['LOST'], tot['OPEN']))

    w('')
    w('D4  SCOREBOARD (list order = dispatch priority)')
    w('      %-12s %-6s %5s %-14s %4s %6s %-28s %5s' % (
        'tcin', '', 'trans', 'races(N)', 'MISS', 'shots', 'kinds', 'units'))
    for t in REGISTERED_ORDER + sorted(set(transitions) - set(REGISTERED_ORDER)):
        k = shots.get(t, Counter())
        rw = Counter(races.get(t, []))
        w('      %-12s %-6s %5d %-14s %4d %6d %-28s %5d' % (
            t, 'drop' if t in DROP_LIST else ('' if t in rank else 'UNLIST'),
            transitions[t], ('%d %s' % (len(races.get(t, [])), dict(sorted(rw.items()))))
            if races.get(t) else '0', misses[t], sum(k.values()),
            ','.join('%s=%d' % kv for kv in sorted(k.items())) or '-', units[t]))
    w('')
    w('=' * 78)
    print('\n'.join(out))


if __name__ == '__main__':
    sys.exit(main())

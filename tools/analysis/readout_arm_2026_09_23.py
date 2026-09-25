#!/usr/bin/env python3
"""Pre-registered readout for the arming of 2026-09-23 (docs/CLAIMS.md rule 3).

REGISTERED 2026-09-23, BEFORE any run with this arming. The criteria below are
fixed in advance so the next restock's narrative cannot be fitted to its data.
Valid for ANY run log produced with this arming (the next restock date is not
known), so it is named for the arming date, not a drop date.

    python tools/analysis/readout_arm_2026_09_23.py logs/runs/run_<ts>.log

Why (docs/CLAIMS.md C-0923-01..04): on 09-23 five hot-SKU restock windows got
37 add-to-cart shots from 2 of 3 accounts, all 429. Each account fired at t~0,
slept 55-70 s (wave-first), fired again, and the window ended -- so nothing was
ever fired 3-55 s into a window, and 6-8 shots went out after the monitor had
already read the TCIN out of stock.

ARMED (run_bot_with_nightly_restart.bat):
  A1  TARGET_WAVE_FIRST_EDGE=0          edge-429 / DCO-429 re-shots at the
                                        2.0-3.0 s edge cadence, not a 55-70 s
                                        cold re-entry. 401s keep wave-first.
  A2  TARGET_MULTI_SKU_WORKERS_PER_TCIN=3  all three accounts race a lone TCIN.
  A3  TARGET_RETRY_STOP_WHEN_OOS=1      a race ends once the monitor has had no
                                        in-stock read of the TCIN for
                                        TARGET_RETRY_OOS_STOP_S (8) seconds.

TESTS
  T0 STOCK EVENTS   [STOCK] IN STOCK transitions per TCIN. Zero => every other
                    test is NO DATA by construction (0-for-ZERO, not 0-for-N).
  T1 A1 CADENCE     PASS  zero "[WAVE_FIRST] ATC-level edge|dco" lines AND the
                          median same-account gap after an edge/dco shot <= 5 s.
                    FAIL  any such wave-first line, or median gap > 15 s.
  T2 A2 WIDTH       PASS  every [RACE] N == 3 while no [MULTI_SKU_MISS] fired,
                          and alt-1 fired >= 1 shot.
                    FAIL  any N > 3, or a stuck-TCIN signature ("no unreserved
                          worker", or [PURCHASE_FORCE_COMPLETE] after 190-210 s).
  T3 A3 OOS STOP    reports every [RETRY_OOS] stop and its last_true_age, and
                    counts shots fired after a [STOCK WATCH] in_stock=False read
                    with no in-stock line for that TCIN in between (09-23: 8 by
                    this script's method).
                    PASS  <= one such shot per TCIN that had stock (~1 per window).
                    FAIL  more.
  T4 THE BET        (A1 + A2 are a bet that more tickets inside the window buy
                    more edge passes; the data could not settle it -- C-0923-02.)
                    edge pass = [EXPOSURE] kind in {pass, dco, other}
                    (i.e. not 'edge' and not 'auth401'), by win_age band.
                    KILL-A1 (no benefit): >= 150 re-shots (run_shots >= 2) at
                          win_age 2-120 s with ZERO edge passes -> revert
                          TARGET_WAVE_FIRST_EDGE=1.
                    HARM-A1: 401 share of main shots > 30% over >= 60 shots, or
                          a WRITE-AUTH DEAD that does not recover within 10 min
                          of a window -> revert TARGET_WAVE_FIRST_EDGE=1.
                    Baseline (09-23, wave-first, 2 accounts): 1 edge pass in 37
                          shots, 0.2 per window, 0 carts.
  T5 QTY            MAX_PURCHASE_LIMIT_EXCEEDED / self-heal lines, cart_qty.
                    Measured only (1 such rejection in the bot's history,
                    07-14 TCIN 95267143 at qty 2).
  T6 MONITOR        sweep loss and the ready-session floor.

Read-only over the log. Stdlib only.
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import Counter, defaultdict

TS = re.compile(r'^(\d{4}-\d\d-\d\d) (\d\d):(\d\d):(\d\d),(\d+)')
# Print lines carry no timestamp and can be glued to the next line without a
# newline, so markers are searched anywhere in a line, never anchored.
R_EXPO = re.compile(r'\[EXPOSURE\] ident=(\S+) tcin=(\d{8,10}) kind=(\S+) run_shots=(\d+) '
                    r'run_s=(\d+) win_age_s=(\S+)')
R_ATC_MAIN = re.compile(r'\[INTERCEPTOR:main\] \[ATC_RESP\] status=(\d+)')
R_INSTOCK = re.compile(r'\[STOCK\] IN STOCK: (\d{8,10})')
# [API_CYCLE] stamps its own clock; [STOCK] IN STOCK is print-only and would take
# the time of the nearest logger line (12.9 s early on 09-23 for 1010892069).
R_CYCLE = re.compile(r'\[(\d\d:\d\d:\d\d)\] \[API_CYCLE\] IN STOCK: \[([^\]]*)\]')
R_WATCH = re.compile(r'\[STOCK WATCH\] (\d{8,10}): in_stock=(True|False)')
R_RACE = re.compile(r'\[RACE\] (\d{8,10}): racing (\d+) accounts')
R_MISS = re.compile(r'\[MULTI_SKU_MISS\] (\d{8,10})')
R_WF = re.compile(r'\[WAVE_FIRST\] ATC-level (\w+) on (\d{8,10})')
R_CHAIN = re.compile(r'\[FAST_LANE\] chain done .*?ident=(\S+) atc_t0=(\d{13})')
R_FIRE = re.compile(r'\[FAST_LANE\] Firing ATC.*?tcin=(\d{8,10})')
R_OOS = re.compile(r'\[RETRY_OOS\] (\d{8,10}).*?last_true_age=([\d.]+)s')
R_STUCK = re.compile(r'no unreserved worker|\[PURCHASE_FORCE_COMPLETE\].*?after (1[89]\d|20\d|210)\.\d+s')
R_AUTHDEAD = re.compile(r'WRITE-AUTH DEAD')
R_PLIMIT = re.compile(r'MAX_PURCHASE_LIMIT_EXCEEDED|per-customer purchase limit hit at qty=\d+'
                      r'|ATC rejected qty=\d+ \(per-customer limit\)|Self-heal qty=1')
R_CARTQTY = re.compile(r'\[FAST_LANE\] chain done .*?\bcart_qty=(-|\d+?)(?=\d{4}-\d\d-\d\d|\s|\[|$)')
R_ORDER = re.compile(r'ORDER PLACED|order_placed|\[PURCHASE SUCCESS\]', re.I)
R_STATS = re.compile(r'\[STOCK STATS\] t=\S+ sweeps=(\d+) \S+ 200=(\d+) 403=(\d+) 429=(\d+) other=(\d+)')
R_SESS = re.compile(r'\bsessions=(\d+)r/')

BANDS = ((0, 2), (2, 10), (10, 30), (30, 60), (60, 120), (120, 10 ** 9))


def band_of(age):
    for lo, hi in BANDS:
        if lo <= age < hi:
            return '%d-%s' % (lo, hi if hi < 10 ** 9 else '+')
    return '?'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('log')
    a = ap.parse_args()

    expo = []                     # (ident, tcin, kind, run_shots, win_age or None)
    atc_main = Counter()
    instock = defaultdict(list)   # tcin -> [HH:MM:SS of each [STOCK] IN STOCK]
    races = []
    misses = Counter()
    wf = Counter()
    chains = []                   # (ident, tcin, t0_ms) in order
    last_fire_tcin = None
    oos_stops = []
    stuck = []
    authdead = 0
    plimit = []
    cart_qty = Counter()
    orders = 0
    stats = []
    ready = []
    # blind shots: after a [STOCK WATCH] False for T with no in-stock line since
    oos_seen = {}                 # tcin -> True once a False read lands, cleared by a True
    blind = []
    last_hms = None

    with open(a.log, 'r', encoding='utf-8', errors='replace') as fh:
        for line in fh:
            m = TS.match(line)
            if m:
                last_hms = '%s:%s:%s' % (m.group(2), m.group(3), m.group(4))
            for m in R_INSTOCK.finditer(line):
                oos_seen[m.group(1)] = False
            for m in R_CYCLE.finditer(line):
                for t in re.findall(r'\d{8,10}', m.group(2)):
                    instock[t].append(m.group(1))
                    oos_seen[t] = False
            for m in R_WATCH.finditer(line):
                oos_seen[m.group(1)] = (m.group(2) == 'False')
            for m in R_FIRE.finditer(line):
                last_fire_tcin = m.group(1)
            for m in R_CHAIN.finditer(line):
                chains.append((m.group(1), last_fire_tcin, int(m.group(2))))
                if last_fire_tcin and oos_seen.get(last_fire_tcin):
                    blind.append((last_hms, m.group(1), last_fire_tcin))
            for m in R_EXPO.finditer(line):
                wa = m.group(6)
                expo.append((m.group(1), m.group(2), m.group(3), int(m.group(4)),
                             float(wa) if wa not in ('-', '') else None))
            for m in R_ATC_MAIN.finditer(line):
                atc_main[m.group(1)] += 1
            for m in R_RACE.finditer(line):
                races.append((m.group(1), int(m.group(2))))
            for m in R_MISS.finditer(line):
                misses[m.group(1)] += 1
            for m in R_WF.finditer(line):
                wf[m.group(1)] += 1
            for m in R_OOS.finditer(line):
                oos_stops.append((last_hms, m.group(1), float(m.group(2))))
            if R_STUCK.search(line):
                stuck.append((last_hms, line.strip()[:160]))
            if R_AUTHDEAD.search(line):
                authdead += 1
            for m in R_PLIMIT.finditer(line):
                plimit.append((last_hms, m.group(0)))
            for m in R_CARTQTY.finditer(line):
                cart_qty[m.group(1)] += 1
            if R_ORDER.search(line):
                orders += 1
            m = R_STATS.search(line)
            if m:
                stats.append(tuple(int(x) for x in m.groups()))
                ms = R_SESS.search(line)
                if ms:
                    ready.append((last_hms, int(ms.group(1))))

    out = []
    w = out.append
    w('=' * 78)
    w('PRE-REGISTERED READOUT -- arming of 2026-09-23   log: %s' % a.log)
    w('=' * 78)

    # ---- T0 ---------------------------------------------------------------
    w('')
    w('T0  STOCK EVENTS (in-stock transitions, self-stamped [API_CYCLE] times)')
    if not instock:
        w('    ZERO transitions -> 0-for-ZERO. T1-T4 are NO DATA by construction.')
    for t in sorted(instock, key=lambda k: instock[k][0] or ''):
        v = instock[t]
        w('      %-12s transitions=%-3d first=%s last=%s' % (t, len(v), v[0], v[-1]))

    # ---- T1 ---------------------------------------------------------------
    w('')
    w('T1  A1 CADENCE (TARGET_WAVE_FIRST_EDGE=0)')
    w('    [WAVE_FIRST] ATC-level lines by kind: %s' % (dict(wf) or '(none)'))
    gaps = []
    prev = {}
    for ident, tcin, t0 in chains:
        k = (ident, tcin)
        if k in prev:
            g = (t0 - prev[k]) / 1000.0
            if 0 < g < 200:
                gaps.append(g)
        prev[k] = t0
    if gaps:
        med = statistics.median(gaps)
        w('    same-account gaps between consecutive shots on a TCIN: n=%d median=%.1fs '
          'p10=%.1fs p90=%.1fs' % (len(gaps), med, sorted(gaps)[len(gaps) // 10],
                                    sorted(gaps)[(9 * len(gaps)) // 10]))
    else:
        med = None
        w('    no consecutive same-account shots on a TCIN')
    bad_wf = wf.get('edge', 0) + wf.get('dco', 0)
    if not chains:
        v1 = 'NO DATA -- no shots'
    elif bad_wf or (med is not None and med > 15):
        v1 = 'FAIL -- %d edge/dco wave-first line(s); median gap %s' % (
            bad_wf, '%.1fs' % med if med is not None else '-')
    elif med is None or med <= 5:
        v1 = 'PASS'
    else:
        v1 = 'INCONCLUSIVE -- median gap %.1fs (5-15 s)' % med
    w('    VERDICT: %s' % v1)

    # ---- T2 ---------------------------------------------------------------
    w('')
    w('T2  A2 WIDTH (TARGET_MULTI_SKU_WORKERS_PER_TCIN=3)')
    dist = Counter(n for _, n in races)
    w('    races=%d N distribution=%s  [MULTI_SKU_MISS]=%d %s' % (
        len(races), dict(sorted(dist.items())), sum(misses.values()), dict(misses) or ''))
    by_ident = Counter(e[0] for e in expo)
    w('    shots by account ([EXPOSURE]): %s' % (dict(by_ident) or '(none)'))
    alt1 = sum(v for k, v in by_ident.items() if 'alt-1' in k)
    for s in stuck[:5]:
        w('    STUCK signature at %s: %s' % s)
    if not races:
        v2 = 'NO DATA -- no races'
    elif any(n > 3 for _, n in races) or stuck:
        v2 = 'FAIL -- N>3 race or stuck-TCIN signature'
    elif not misses and all(n == 3 for _, n in races) and alt1 >= 1:
        v2 = 'PASS'
    else:
        v2 = 'INCONCLUSIVE -- N<3 race(s) or alt-1 silent (check readiness / MISS lines)'
    w('    VERDICT: %s' % v2)

    # ---- T3 ---------------------------------------------------------------
    w('')
    w('T3  A3 OOS STOP (TARGET_RETRY_STOP_WHEN_OOS=1)')
    w('    [RETRY_OOS] stops: %d' % len(oos_stops))
    for s in oos_stops[:10]:
        w('      %s %s last_true_age=%.1fs' % s)
    w('    shots after a [STOCK WATCH] in_stock=False with no in-stock line since: %d '
      '(09-23 baseline 8)' % len(blind))
    for b in blind[:10]:
        w('      %s %s %s' % b)
    if not chains:
        v3 = 'NO DATA -- no shots'
    elif len(blind) <= max(1, len(instock)):
        v3 = 'PASS'
    else:
        v3 = 'FAIL -- %d blind shots across %d TCIN(s) with stock' % (len(blind), len(instock))
    w('    VERDICT: %s' % v3)

    # ---- T4 ---------------------------------------------------------------
    w('')
    w('T4  THE BET -- edge passes by window age ([EXPOSURE] kind; pass = not edge, not auth401)')
    cell = defaultdict(Counter)
    for ident, tcin, kind, rs, age in expo:
        b = band_of(age) if age is not None else 'no-age'
        cell[b]['n'] += 1
        if kind == 'auth401':
            cell[b]['401'] += 1
        elif kind != 'edge' and kind != '-':
            cell[b]['pass'] += 1
    for lo, hi in BANDS:
        b = band_of(lo)
        c = cell.get(b)
        if c:
            non401 = c['n'] - c['401']
            w('      %-8s n=%-4d 401=%-3d pass=%-3d pass/non401=%s' % (
                b, c['n'], c['401'], c['pass'],
                '%.1f%%' % (100.0 * c['pass'] / non401) if non401 else '-'))
    if cell.get('no-age'):
        w('      no-age   n=%d (win_age_s="-")' % cell['no-age']['n'])
    kinds = Counter(e[2] for e in expo)
    w('    [EXPOSURE] kinds: %s   main-tab ATC_RESP status: %s' % (dict(kinds), dict(atc_main)))
    n_expo = len(expo)
    n_main = sum(atc_main.values())
    if n_main != n_expo:
        w('    NOTE: %d main ATC_RESP vs %d [EXPOSURE] -- extra POSTs inside one attempt '
          '(legacy fallthrough / qty self-heal) or a glued line; reconcile before quoting'
          % (n_main, n_expo))
    reshots = [e for e in expo if e[3] >= 2 and e[4] is not None and 2 <= e[4] < 120]
    reshot_pass = sum(1 for e in reshots if e[2] not in ('edge', 'auth401', '-'))
    p401 = kinds.get('auth401', 0)
    w('    re-shots (run_shots>=2) at win_age 2-120 s: n=%d edge-passes=%d' % (len(reshots), reshot_pass))
    w('    401 share of main shots: %s   WRITE-AUTH DEAD lines: %d' % (
        '%d/%d' % (p401, n_expo) if n_expo else '-', authdead))
    carts = atc_main.get('201', 0)
    w('    carts (main-tab 201): %d   order markers: %d' % (carts, orders))
    if not expo:
        v4 = 'NO DATA -- no shots'
    elif len(reshots) >= 150 and reshot_pass == 0:
        v4 = 'KILL-A1 -- >=150 re-shots, zero edge passes: revert TARGET_WAVE_FIRST_EDGE=1'
    elif n_expo >= 60 and p401 / float(n_expo) > 0.30:
        v4 = 'HARM-A1 -- 401 share %.0f%% > 30%%: revert TARGET_WAVE_FIRST_EDGE=1 (check WRITE-AUTH DEAD)' % (
            100.0 * p401 / n_expo)
    else:
        v4 = 'NO KILL -- %d edge pass(es) in %d shots (09-23 baseline: 1 in 37); carts=%d' % (
            sum(c['pass'] for c in cell.values()), n_expo, carts)
    w('    VERDICT: %s' % v4)
    if authdead:
        w('    >> %d WRITE-AUTH DEAD line(s) (baseline 13-14 a night, all recovered, 09-22/09-23):'
          ' check any inside or just after a window recovered within 10 min (HARM-A1 by hand)'
          % authdead)

    # ---- T5 ---------------------------------------------------------------
    w('')
    w('T5  QTY (measured only)')
    w('    purchase-limit / self-heal lines: %d  (history: 1, 07-14)' % len(plimit))
    for p in plimit[:8]:
        w('      %s %s' % p)
    w('    cart_qty on chain lines: %s' % (dict(cart_qty) or '(none)'))

    # ---- T6 ---------------------------------------------------------------
    w('')
    w('T6  MONITOR')
    if stats:
        sw, ok, f403, f429, oth = stats[-1]
        w('    final [STOCK STATS]: sweeps=%d 200=%d 403=%d 429=%d other=%d  loss=%.3f%%' % (
            sw, ok, f403, f429, oth, 100.0 * (sw - ok) / sw if sw else 0.0))
    if ready:
        lo = min(ready, key=lambda r: r[1])
        w('    ready-session floor: %d at %s (over %d intervals)' % (lo[1], lo[0], len(ready)))
    w('')
    w('=' * 78)
    print('\n'.join(out))


if __name__ == '__main__':
    sys.exit(main())

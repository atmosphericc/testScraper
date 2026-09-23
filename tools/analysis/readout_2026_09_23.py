#!/usr/bin/env python3
"""Pre-registered readout for the 2026-09-23 03:00 drop (docs/CLAIMS.md rule 3).

REGISTERED 2026-09-22 ~22:20, BEFORE the run. Criteria are fixed here in advance
so tomorrow's narrative cannot be fitted to the data.

    python tools/analysis/readout_2026_09_23.py logs/runs/run_<ts>.log

Armed for this run: the SAME 10 enabled TCINs as 09-22 (all hot), and ONE change --
"qty": 2 on every product_config.json entry (operator decision, 09-22 evening). Six
of the ten were pinned to qty 1 since 2026-09-18 (TARGET_QTY_PER_TCIN=1); the four
Ascended Heroes TCINs had no pin (and every historical decision for all ten was
qty=2 "RedSky limit unreported", 462/462 lines). T1-T4 carry over from
readout_2026_09_22.py unchanged in intent -- 09-22 had zero stock events, so none
of them was exercised.

  T0 QTY               (the change armed tonight)
      PASS     every [QTY] line for an enabled TCIN reads "targeting qty=2" with the
               reason "per-TCIN pin qty=2 from product_config.json".
      FAIL     any enabled-TCIN [QTY] line targets a qty other than 2, or reaches 2
               by a reason other than the pin (the config did not load as written).
      NO DATA  no [QTY] line at all (nothing was dispatched -- see T5).
      Measured alongside (no PASS/FAIL): cart_qty= on [FAST_LANE] chain lines,
      per-customer PURCHASE_LIMIT rejections (0 in the bot's entire history; >= 1
      means a limit-1 SKU cost extra POSTs at qty 2 before the qty-1 self-heal),
      and RESERVATION_FAILURE mentions (the 09-18 pin rationale, n=2).

  T1 ATC_RESP label    main-vs-warmup honest shot count (TARGET_ATC_RESP_LABEL=1).
      PASS if every logger copy carries tab=.

  T2 RACE width        TARGET_MULTI_SKU_WORKERS_PER_TCIN=2, CAP_ALWAYS=1 =>
      _limit is 2 for every race (bulletproof_purchase_manager.py _limit expr).
      PASS  every [RACE] N is 1 or 2.   FAIL  any N >= 3 (the cap did not hold).
      Measured: [MULTI_SKU_MISS] per TCIN -- live hot TCINs that got ZERO workers
      because the fleet was already committed. With 3 accounts and 2-per-TCIN, a
      third simultaneously-live TCIN is structurally uncovered.

  T3 BLINDNESS in the drop window 02:45-04:15
      FAIL  any >= 3-read 429 ground-truth blackout overlapping the window.
      WARN  [STOCK STATS] ready sessions < 6 inside the window (below the floor
            that holds 3.0 sweeps/s at RESILIENT_PER_IP_MAX_RPS=0.5). 13 of the 18
            active exits share one /16 -- the subnet behind the 09-22 cascade.

  T4 OUTCOME           carts (main-tab 201s), order markers, ATC status mix.

  T5 STOCK EVENTS      first/last [STOCK WATCH] in_stock=True per TCIN.
      Zero events => the night is 0-for-ZERO, not 0-for-N; every other test is then
      NO DATA by construction, not a failure.

Read-only over the log. Stdlib only.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

ENABLED = ('1010892076', '1010892067', '1010892065', '1010892078', '1010892069',
           '1011960739', '95120834', '1012644667', '1012644666', '1012644665')

TS = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+')
R_ATC_PRINT = re.compile(r'^\[INTERCEPTOR:(\w+)\] \[ATC_RESP\] status=(\d+)')
R_ATC_LOG = re.compile(r'purchase_executor: \[ATC_RESP\] status=(\d+)')
R_TAB = re.compile(r'\btab=(\w+)\b')
R_RACE = re.compile(r'^\[RACE\] (\d{8,10}): racing (\d+) accounts(?:\s+\S+\s+(\[.*?\]))?')
R_MISS = re.compile(r'\[MULTI_SKU_MISS\] (\d{8,10}) in-stock but SKIPPED')
# [QTY] is print-only and has been seen glued to the next print (no newline), so
# never anchor at end of line; TCIN bounded to 8-10 digits (the 09-22 glue fix).
R_QTY = re.compile(r'\[QTY\] (\d{8,10}): targeting qty=(\d+)')
R_QTY_PIN = re.compile(r'per-TCIN pin qty=(\S+) from product_config\.json')
# cart_qty is '-' or a small int; a following print can glue a date straight on
# ("cart_qty=-2026-09-18"), so stop before a glued date. Anything else -> UNPARSED.
R_CARTQTY = re.compile(r'^\[FAST_LANE\] chain done .*?\bcart_qty=(-|\d+?)(?=\d{4}-\d\d-\d\d|\s|\[|$)')
R_PLIMIT = re.compile(r'per-customer purchase limit hit at qty=(\d+)|ATC rejected qty=(\d+) \(per-customer limit\)'
                      r'|Self-heal qty=1 (succeeded|also failed|still)')
R_RESV = re.compile(r'RESERVATION_FAILURE')
R_STATS = re.compile(r'\[STOCK STATS\] t=(\S+?)s sweeps=(\d+)')
R_STATS_429 = re.compile(r'\b429=(\d+)')
R_SESS = re.compile(r'\bsessions=(\d+)r/')
R_TARPIT = re.compile(r'cache-bust read FAILED.*?http=429')
R_ORDER = re.compile(r'ORDER PLACED|order_placed|\[PURCHASE SUCCESS\]', re.I)
R_INSTOCK = re.compile(r'\[STOCK WATCH\] (\d{8,10}): in_stock=True')

WINDOW = ('02:45:00', '04:15:00')
MIN_BLACKOUT_READS = 3
BLACKOUT_SPLIT_S = 120
READY_FLOOR = 6


def _ts(line):
    m = TS.match(line)
    return m.group(1) if m else None


def _hhmmss(stamp):
    return stamp.split(' ')[1] if stamp and ' ' in stamp else None


def _in_window(hhmmss, window):
    return bool(hhmmss) and window[0] <= hhmmss <= window[1]


def _group_blackouts(stamps):
    if not stamps:
        return []
    groups, cur = [], [stamps[0]]
    for prev, cur_ts in zip(stamps, stamps[1:]):
        try:
            gap = (datetime.strptime(cur_ts, '%Y-%m-%d %H:%M:%S')
                   - datetime.strptime(prev, '%Y-%m-%d %H:%M:%S')).total_seconds()
        except Exception:
            gap = 0
        if gap > BLACKOUT_SPLIT_S:
            groups.append(cur)
            cur = []
        cur.append(cur_ts)
    groups.append(cur)
    return groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('log')
    ap.add_argument('--window', nargs=2, default=list(WINDOW),
                    help='drop window HH:MM:SS HH:MM:SS for T3')
    a = ap.parse_args()
    win = tuple(a.window)

    qty = []                                   # (tcin, qty, pin or None)
    cart_qty = Counter()
    plimit = []                                # raw matched text
    resv = 0
    atc_print = Counter()
    atc_print_status = defaultdict(Counter)
    atc_log_total = 0
    atc_log_tabbed = Counter()
    atc_status_all = Counter()
    races = []
    misses = Counter()
    stats_win_ready = []                       # ready-session counts inside the window
    stats = []
    tarpit = []
    orders = 0
    instock = {}                               # tcin -> [first_ts, last_ts, n]
    last_ts = None

    with open(a.log, 'r', encoding='utf-8', errors='replace') as fh:
        for line in fh:
            line = line.rstrip('\n')
            ts = _ts(line)
            if ts:
                last_ts = ts
            for m in R_QTY.finditer(line):
                pin = R_QTY_PIN.search(line[m.end():m.end() + 160])
                qty.append((m.group(1), int(m.group(2)), pin.group(1) if pin else None))
            if line.startswith('[FAST_LANE] chain done') and 'cart_qty=' in line:
                m = R_CARTQTY.match(line)
                cart_qty[m.group(1) if m else 'UNPARSED'] += 1
            m = R_PLIMIT.search(line)
            if m:
                plimit.append(m.group(0))
            if R_RESV.search(line):
                resv += 1
            m = R_ATC_PRINT.match(line)
            if m:
                atc_print[m.group(1)] += 1
                atc_print_status[m.group(1)][m.group(2)] += 1
                atc_status_all[m.group(2)] += 1
            m = R_ATC_LOG.search(line)
            if m:
                atc_log_total += 1
                t = R_TAB.search(line)
                atc_log_tabbed[t.group(1) if t else '<no tab= field>'] += 1
            m = R_RACE.match(line)
            if m:
                races.append((m.group(1), int(m.group(2)), m.group(3) or '-'))
            for m in R_MISS.finditer(line):
                misses[m.group(1)] += 1
            if '[STOCK STATS]' in line:
                m = R_STATS.search(line)
                if m:
                    m429 = R_STATS_429.search(line)
                    stats.append((m.group(1), int(m.group(2)),
                                  int(m429.group(1)) if m429 else None))
                    ms = R_SESS.search(line)
                    if ms and _in_window(_hhmmss(ts), win):
                        stats_win_ready.append((_hhmmss(ts), int(ms.group(1))))
            if R_TARPIT.search(line):
                tarpit.append(ts or '?')
            if R_ORDER.search(line):
                orders += 1
            m = R_INSTOCK.search(line)
            if m:
                rec = instock.setdefault(m.group(1), [ts or last_ts, ts or last_ts, 0])
                rec[1] = ts or last_ts
                rec[2] += 1

    out = []
    w = out.append
    w('=' * 76)
    w('PRE-REGISTERED READOUT -- 2026-09-23 drop   log: %s' % a.log)
    w('=' * 76)

    # ---- T5 first: it decides whether the rest can have data -------------
    w('')
    w('T5  STOCK EVENTS ([STOCK WATCH] in_stock=True)')
    if not instock:
        w('    ZERO in_stock=True lines -> 0-for-ZERO. T0/T2/T4 are NO DATA by construction.')
    else:
        for t in sorted(instock, key=lambda k: instock[k][0] or ''):
            f, l, n = instock[t]
            w('      %-12s first=%s last=%s lines=%d%s'
              % (t, f, l, n, '' if t in ENABLED else '   (NOT an enabled TCIN)'))
        never = [t for t in ENABLED if t not in instock]
        w('    enabled TCINs with no stock line: %d/%d %s' % (len(never), len(ENABLED), never))

    # ---- T0 --------------------------------------------------------------
    w('')
    w('T0  QTY -- "qty": 2 on every entry (TARGET_QTY_PER_TCIN=1)')
    en = [q for q in qty if q[0] in ENABLED]
    other = [q for q in qty if q[0] not in ENABLED]
    w('    [QTY] lines: %d (enabled TCINs %d, other %d)' % (len(qty), len(en), len(other)))
    if not en:
        v0 = 'NO DATA -- no [QTY] line for an enabled TCIN (nothing dispatched)'
    else:
        bad = [q for q in en if q[1] != 2 or q[2] != '2']
        dist = Counter((q[0], q[1], q[2]) for q in en)
        for (t, n, pin), c in sorted(dist.items()):
            w('      %-12s qty=%d pin=%s  x%d' % (t, n, pin, c))
        v0 = ('PASS -- every enabled-TCIN [QTY] targets qty=2 via the per-TCIN pin' if not bad
              else 'FAIL -- %d of %d [QTY] lines are not qty=2-by-pin: %s'
              % (len(bad), len(en), sorted(set(bad))[:6]))
    w('    cart_qty on [FAST_LANE] chain lines: %s' % (dict(cart_qty) or '(none)'))
    w('    PURCHASE_LIMIT rejections / qty self-heals: %d  (history: 0)' % len(plimit))
    for p in plimit[:8]:
        w('      %s' % p)
    w('    RESERVATION_FAILURE mentions: %d' % resv)
    w('    VERDICT: %s' % v0)

    # ---- T1 --------------------------------------------------------------
    w('')
    w('T1  ATC_RESP LABEL (TARGET_ATC_RESP_LABEL=1)')
    w('    logger-copy [ATC_RESP] lines : %d' % atc_log_total)
    if not atc_log_total:
        v1 = 'NO DATA -- no logger-copy ATC_RESP lines at all'
    elif '<no tab= field>' not in atc_log_tabbed:
        v1 = 'PASS -- every logger copy carries tab='
    elif any(k != '<no tab= field>' for k in atc_log_tabbed):
        v1 = 'PARTIAL -- some logger copies carry tab=, some do not'
    else:
        v1 = 'FAIL -- logger copies exist but NONE carry tab='
    for k, v in sorted(atc_log_tabbed.items(), key=lambda kv: -kv[1]):
        w('      tab=%-18s %6d' % (k, v))
    for k, v in sorted(atc_print.items(), key=lambda kv: -kv[1]):
        w('      print %-12s %6d   status %s' % (k, v, dict(atc_print_status[k])))
    real = atc_log_tabbed.get('main', 0) or atc_print.get('main', 0)
    w('    >> REAL SHOTS (main tab) = %d' % real)
    w('    VERDICT: %s' % v1)

    # ---- T2 --------------------------------------------------------------
    w('')
    w('T2  RACE WIDTH (WORKERS_PER_TCIN=2, CAP_ALWAYS=1) + multi-SKU starvation')
    if not races:
        w('    no [RACE] lines. [RACE] is print-only: it reaches ONLY logs/runs/run_*.log.')
        v2 = 'NO DATA'
    else:
        dist = Counter(n for _, n, _ in races)
        w('    %d races. N distribution: %s' % (len(races), dict(sorted(dist.items()))))
        per = defaultdict(Counter)
        for t, n, _ in races:
            per[t][n] += 1
        for t in sorted(per):
            w('      %-12s %s' % (t, dict(sorted(per[t].items()))))
        seen = []
        for r in races:
            if r not in seen:
                seen.append(r)
        for t, n, accts in seen[:6]:
            w('      distinct race: %-12s N=%d %s' % (t, n, accts))
        over = [r for r in races if r[1] >= 3]
        v2 = ('PASS -- every race N<=2' if not over
              else 'FAIL -- %d race(s) with N>=3: the per-TCIN cap did not hold' % len(over))
    w('    [MULTI_SKU_MISS] (live TCIN, zero workers): %d total %s'
      % (sum(misses.values()), dict(misses) or ''))
    w('    VERDICT: %s' % v2)

    # ---- T3 --------------------------------------------------------------
    w('')
    w('T3  BLINDNESS (window %s-%s)' % win)
    w('    failed ground-truth 429 reads: %d' % len(tarpit))
    overlap = False
    for b in _group_blackouts(tarpit):
        if len(b) < MIN_BLACKOUT_READS:
            continue
        s, e = _hhmmss(b[0]), _hhmmss(b[-1])
        hit = bool(s and e and not (e < win[0] or s > win[1]))
        overlap = overlap or hit
        w('      blackout %s -> %s (%d reads)%s' % (s, e, len(b), '  <<< IN WINDOW' if hit else ''))
    if stats_win_ready:
        lo = min(stats_win_ready, key=lambda r: r[1])
        w('    ready sessions in window: min=%d at %s over %d [STOCK STATS] intervals'
          % (lo[1], lo[0], len(stats_win_ready)))
        low = lo[1] < READY_FLOOR
    else:
        w('    no [STOCK STATS] line inside the window (run did not cover it?)')
        low = False
    have429 = [s for s in stats if s[2] is not None]
    if have429:
        w('    [STOCK STATS] 429= final=%d over %d intervals' % (have429[-1][2], len(have429)))
    w('    VERDICT: %s%s' % ('FAIL -- blind inside the drop window' if overlap
                             else 'PASS -- no >=3-read 429 blackout in the window',
                             '; WARN ready<%d in window' % READY_FLOOR if low else ''))

    # ---- T4 --------------------------------------------------------------
    w('')
    w('T4  OUTCOME')
    w('    ATC status (print copies, all tabs): %s'
      % dict(sorted(atc_status_all.items(), key=lambda kv: -kv[1])))
    w('    main-tab ATC status: %s' % dict(atc_print_status.get('main', {})))
    w('    carts (main-tab 201): %d' % atc_print_status.get('main', Counter()).get('201', 0))
    w('    order markers: %d' % orders)
    w('')
    w('=' * 76)
    print('\n'.join(out))


if __name__ == '__main__':
    sys.exit(main())

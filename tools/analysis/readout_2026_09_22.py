#!/usr/bin/env python3
"""Pre-registered readout for the 2026-09-22 03:00 drop (docs/CLAIMS.md rule 3).

REGISTERED 2026-09-21 ~22:00, BEFORE the run. Criteria are fixed here in advance
so tomorrow's narrative cannot be fitted to the data.

    python tools/analysis/readout_2026_09_22.py logs/runs/run_<ts>.log

  T1 ATC_RESP label   (armed tonight, TARGET_ATC_RESP_LABEL=1)
      The interceptor label reached only the print copy, never package.log, so
      97.4 pct of [ATC_RESP] lines are the warmup heartbeat and every shot-volume
      number ever derived from them was inflated up to ~40x.
      PASS  if >= 1 logger-copy [ATC_RESP] line carries tab=.
      FAIL  if logger copies exist but none carry tab= (flag did not take).
      Output: the first honest main-vs-warmup shot count in the bot's history.

  T2 RACE width       (the single-worker cap question)
      [RACE] <tcin>: racing N accounts -- N is len(dispatch_workers), i.e. the
      ACTUAL dispatched count, so this is the direct observation the cap has
      never had. Baseline 09-18 (pre-cap): 36 races, every one N=3.
      Reports the distribution of N and the named accounts per race.
      No PASS/FAIL -- this is the measurement the decision was missing.

  T3 429 tarpit       (the 03:00 risk)
      On 09-21 a pool-wide 429 blackout ran 03:21:24-04:04:06 -- 75 consecutive
      failed ground-truth reads. THE DROP IS AT 03:00.
      FAIL  if any blackout of >= 3 consecutive failures overlaps 02:45-04:15.
      Also reports per-interval 429 deltas from the new [STOCK STATS] 429= field
      (551b1807; absent on runs before 2026-09-21 10:42).

  T4 outcome          carts (201), orders, and the ATC status distribution.

Read-only over the log. Stdlib only.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

TS = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+')
R_ATC_PRINT = re.compile(r'^\[INTERCEPTOR:(\w+)\] \[ATC_RESP\] status=(\d+)')
R_ATC_LOG = re.compile(r'purchase_executor: \[ATC_RESP\] status=(\d+)')
R_TAB = re.compile(r'\btab=(\w+)\b')
R_SELFTEST = re.compile(r'\bselftest=(\w+)\b')
R_RACE = re.compile(r'^\[RACE\] (\d+): racing (\d+) accounts(?:\s+\S+\s+(\[.*\]))?')
R_STATS = re.compile(r'\[STOCK STATS\] t=(\S+?)s sweeps=(\d+)')
R_STATS_429 = re.compile(r'\b429=(\d+)')
R_TARPIT = re.compile(r'cache-bust read FAILED.*?http=429')
R_ORDER = re.compile(r'ORDER PLACED|order_placed|\[PURCHASE SUCCESS\]', re.I)

# The drop window plus 15 min either side.
WINDOW = ('02:45:00', '04:15:00')
# A blackout shorter than this many consecutive failed reads is noise, not an outage.
MIN_BLACKOUT_READS = 3
# Gap that separates two distinct blackouts (ground-truth cycle is ~30 s).
BLACKOUT_SPLIT_S = 120


def _ts(line):
    m = TS.match(line)
    return m.group(1) if m else None


def _hhmmss(stamp):
    return stamp.split(' ')[1] if stamp and ' ' in stamp else None


def _group_blackouts(stamps):
    """Split an ordered list of failure timestamps into runs separated by a gap."""
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
                    help='drop window HH:MM:SS HH:MM:SS for the T3 overlap test')
    a = ap.parse_args()

    atc_print = Counter()                      # interceptor label -> n
    atc_print_status = defaultdict(Counter)
    atc_log_total = 0
    atc_log_tabbed = Counter()                 # tab= value -> n
    atc_log_selftest = Counter()
    atc_status_all = Counter()
    races = []                                 # (tcin, n, accounts)
    stats = []                                 # (t, sweeps, n429 or None)
    tarpit = []                                # timestamps of failed 429 reads
    orders = 0

    with open(a.log, 'r', encoding='utf-8', errors='replace') as fh:
        for line in fh:
            line = line.rstrip('\n')
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
                s = R_SELFTEST.search(line)
                if s:
                    atc_log_selftest[s.group(1)] += 1
            m = R_RACE.match(line)
            if m:
                races.append((m.group(1), int(m.group(2)), m.group(3) or '-'))
            if '[STOCK STATS]' in line:
                m = R_STATS.search(line)
                if m:
                    m429 = R_STATS_429.search(line)
                    stats.append((m.group(1), int(m.group(2)),
                                  int(m429.group(1)) if m429 else None))
            if R_TARPIT.search(line):
                tarpit.append(_ts(line) or '?')
            if R_ORDER.search(line):
                orders += 1

    out = []
    w = out.append
    w('=' * 74)
    w('PRE-REGISTERED READOUT -- 2026-09-22 drop   log: %s' % a.log)
    w('=' * 74)

    # ---- T1 --------------------------------------------------------------
    w('')
    w('T1  ATC_RESP LABEL (TARGET_ATC_RESP_LABEL=1)')
    w('    logger-copy [ATC_RESP] lines : %d' % atc_log_total)
    if not atc_log_total:
        verdict_t1 = 'NO DATA -- no logger-copy ATC_RESP lines at all'
    elif '<no tab= field>' not in atc_log_tabbed:
        verdict_t1 = 'PASS -- every logger copy carries tab='
    elif any(k != '<no tab= field>' for k in atc_log_tabbed):
        verdict_t1 = 'PARTIAL -- some logger copies carry tab=, some do not'
    else:
        verdict_t1 = 'FAIL -- logger copies exist but NONE carry tab= (flag not armed?)'
    for k, v in sorted(atc_log_tabbed.items(), key=lambda kv: -kv[1]):
        w('      tab=%-18s %6d' % (k, v))
    if atc_log_selftest:
        w('      selftest: %s' % dict(atc_log_selftest))
    w('    print-copy by interceptor label:')
    for k, v in sorted(atc_print.items(), key=lambda kv: -kv[1]):
        w('      %-18s %6d   status %s' % (k, v, dict(atc_print_status[k])))
    real = atc_log_tabbed.get('main', 0) or atc_print.get('main', 0)
    noise = sum(v for k, v in atc_print.items() if k != 'main')
    tot = real + noise
    if tot:
        w('    >> REAL SHOTS (main) = %d of %d ATC_RESP lines (%.1f pct);'
          ' the rest is harvester/warmup.' % (real, tot, 100.0 * real / tot))
    w('    VERDICT: %s' % verdict_t1)

    # ---- T2 --------------------------------------------------------------
    w('')
    w('T2  RACE WIDTH -- how many accounts actually fired per TCIN')
    if not races:
        w('    no [RACE] dispatch lines. NOTE: [RACE] is print-only, so it reaches')
        w('    ONLY the per-boot run_*.log tee, never package.log. Wrong file?')
    else:
        dist = Counter(n for _, n, _ in races)
        w('    %d races. distribution of N: %s' % (len(races), dict(sorted(dist.items()))))
        w('    baseline 09-18 (pre-cap): 36 races, all N=3')
        per = defaultdict(Counter)
        for tcin, n, _ in races:
            per[tcin][n] += 1
        for tcin in sorted(per):
            w('      %-12s %s' % (tcin, dict(sorted(per[tcin].items()))))
        w('    first 5 races, with the accounts named:')
        for tcin, n, accts in races[:5]:
            w('      %-12s N=%d  %s' % (tcin, n, accts))

    # ---- T3 --------------------------------------------------------------
    w('')
    w('T3  429 TARPIT WATCH (drop window %s-%s)' % (a.window[0], a.window[1]))
    w('    failed ground-truth 429 reads: %d' % len(tarpit))
    overlap = False
    for b in _group_blackouts(tarpit):
        if len(b) < MIN_BLACKOUT_READS:
            continue
        s, e = _hhmmss(b[0]), _hhmmss(b[-1])
        hit = bool(s and e and not (e < a.window[0] or s > a.window[1]))
        overlap = overlap or hit
        w('      blackout %s -> %s  (%d reads)%s'
          % (s, e, len(b), '   <<< OVERLAPS THE DROP WINDOW' if hit else ''))
    w('    VERDICT: %s' % ('FAIL -- blind inside the drop window' if overlap
                           else 'PASS -- no >=3-read blackout in the drop window'))
    have429 = [s for s in stats if s[2] is not None]
    if have429:
        peak = max(have429, key=lambda s: s[2])
        w('    [STOCK STATS] 429= present on %d/%d intervals; final=%d peak=%d at t=%ss'
          % (len(have429), len(stats), have429[-1][2], peak[2], peak[0]))
    elif stats:
        w('    [STOCK STATS] has no 429= field -- run predates 551b1807, or'
          ' RESILIENT_SPLIT_429=0.')

    # ---- T4 --------------------------------------------------------------
    w('')
    w('T4  OUTCOME')
    w('    ATC status distribution (print copies): %s'
      % dict(sorted(atc_status_all.items(), key=lambda kv: -kv[1])))
    w('    carts (ATC 201): %d' % atc_status_all.get('201', 0))
    w('    order markers  : %d' % orders)
    w('')
    w('=' * 74)
    print('\n'.join(out))


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Pre-registered readout for the arming of 2026-09-25 (docs/CLAIMS.md rule 3).

REGISTERED 2026-09-25, BEFORE any run with this arming. Valid for ANY run log
produced with it (the next drop date is not known), so it is named for the arming.

    python tools/analysis/readout_arm_2026_09_25.py logs/runs/run_<ts>.log

ARMED (run_bot_with_nightly_restart.bat), from the 09-25 post-run:
  K1  TARGET_WAVE_FIRST_EDGE=1   the 09-23 bet A1 KILLED by its own rule (C-0925-01):
                                 edge/DCO 429 re-shots take the 55-70 s wave-first
                                 cold re-entry again (0 of 663 fast re-shots passed).
  R1  TARGET_WONCART_RF_ENTRY=1  a place-order received as 429 RESERVATION_FAILURE
                                 enters the won-cart ticket loop (C-0925-02).
  L1  RESILIENT_206_LOG=1        log-only [STOCK][206] summaries (C-0925-03).
  L2  TARGET_TOKEN_MINT_LOG=1    log-only record of Target's answer to the member-
                                 token mint (C-0925-04).
  L3  RESILIENT_FLIP_LOG=1       log-only [STOCK][FLIP] line per out->in read: the
                                 read's epoch-ms stamp and RedSky's raw ATP / purchase-
                                 limit fields (added later on 09-25, after the first-gate
                                 investigation: every September admission was a first-
                                 volley shot, and the read that starts a volley had only
                                 a whole-second stamp).
  Still armed from 09-23: A2 WORKERS_PER_TCIN=3, A3 RETRY_STOP_WHEN_OOS=1 (8 s),
  G1 STUCK_RESET_LIVE_GUARD=1. readout_arm_2026_09_23.py T2/T3/T5/T6 still apply;
  its T1 and T4 judged A1 and no longer apply.

MEASUREMENT RULES learned on 09-25 (do not repeat these mistakes):
  - [EXPOSURE] win_age_s is stamped when an attempt ENDS (a won cart's reads 251 s).
    Fire time is atc_t0 (epoch ms) on the [FAST_LANE] chain line; use that.
  - run_shots is a per-(identity, TCIN) tracker count, not the within-race index.
  - [API_CYCLE] IN STOCK lines include level re-arm re-publications.

TESTS
  S0 FUNNEL (measured): in-stock updates per TCIN, races, main-tab add-to-cart
     responses by status+key, admissions past the edge (201 or a FAST_SELLING 429),
     carts, place-order attempts ([FS_TICKET] + in-chain), order markers.
  K1 CADENCE: median gap between one account's consecutive shots on one TCIN,
     from atc_t0, within 200 s.
     PASS  median >= 30 s and >=1 "[WAVE_FIRST] ATC-level edge|dco" line.
     FAIL  median < 10 s (the revert did not take).   NO DATA  <2 shots/account.
  R1 RF ENTRY: every in-chain place-order 429 RESERVATION_FAILURE on a 201 cart.
     RF_LOOP   "[WON_CART_DIRECT] start entry=first ident=<acct>" follows within 400 lines.
     RF_LEGACY the account's legacy checkout ([API_PLACE_ORDER]/[PAYMENT]) shows first.
     PASS  every RF cart is RF_LOOP and fired >= 3 "[WON_CART_DIRECT] ticket" lines.
     FAIL  any RF_LEGACY, or any AMBIGUOUS_COMMIT / DOUBLE-BUY GUARD line anywhere.
     NO DATA  no in-chain 429 RESERVATION_FAILURE.
     Kill: any duplicate order or AMBIGUOUS_COMMIT on an RF entry -> TARGET_WONCART_RF_ENTRY=0.
  L1 206: [STOCK][206] lines and their first three bodies (the shape nobody has seen).
     PASS  a monitor burst (>=30% of a 30 s interval failing) came with >=1 line.
     FAIL  a burst with no line: the burst was not 206 -- re-open C-0925-03.
     NO DATA  no burst.
  L2 MINT: every "mint rung1 token_refresh -> ..." by status, every "mint rung2 ...
     final_url=" by landing (login redirect / account / other) and token class, and
     "could NOT mint" per account. Measured only; it decides C-0925-04 (a) vs (b).
  L3 FLIP: every [STOCK][FLIP] line. For each new-window flip, the delay from read_ms
     to the first atc_t0 at or after it (any account, within 3 s; marked ambiguous when
     another TCIN flipped within 3 s), and the raw atp / max_order_qty / purchase_limit.
     PASS  every raced TCIN has >= 1 FLIP line (or was opened by the cache-bust VERIFY or
           GROUND-TRUTH path, whose own warning line carries a ms log stamp -- those paths
           set in_stock themselves, so the sweep never logs a FLIP for them).
     FAIL  a TCIN raced with none of those: the stamp is missing where it matters.
     NO DATA  no race.

Read-only over the log. Stdlib only.
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import Counter, defaultdict

T = r'(\d{8,10})(?=\d{4}-\d\d-\d\d|\D|$)'
R_INSTOCK = re.compile(r'\[STOCK\] IN STOCK: ' + T)
R_RACE = re.compile(r'\[RACE\] ' + T + r': racing (\d+) accounts')
R_ATC = re.compile(r'\[INTERCEPTOR:main\] \[ATC_RESP\] status=(\d+) method=POST tgt-cart-error-key=(\S*)')
R_CHAIN = re.compile(r'\[FAST_LANE\] chain done .*?atc=(\S+) pre=(\S+) po=(\S+) .*?ident=(\S+) atc_t0=(\d{13})')
R_FIRE = re.compile(r'\[FAST_LANE\] Firing ATC.*?tcin=' + T)
R_WF = re.compile(r'\[WAVE_FIRST\] ATC-level (\w+) on ' + T)
R_RF = re.compile(r'place-order rejected: HTTP 429 key=RESERVATION_FAILURE')
R_WCD_START = re.compile(r'\[WON_CART_DIRECT\] start entry=(\w+) ident=(\S+) tcin=' + T)
R_WCD_TICKET = re.compile(r'\[WON_CART_DIRECT\] ticket n=\d+ .*?ident=(\S+)')
R_LEGACY = re.compile(r'\[API_PLACE_ORDER\] Firing|\[PAYMENT\] ')
R_FS_TICKET = re.compile(r'\[FS_TICKET\] ident=(\S+) .*?status=(\d+) key=(\S+)')
R_ORDER = re.compile(r'ORDER PLACED|order_placed|\[PURCHASE SUCCESS\]', re.I)
R_DANGER = re.compile(r'AMBIGUOUS_COMMIT|DOUBLE-BUY GUARD')
R_206 = re.compile(r'\[STOCK\]\[206\] (.{0,400})')
R_STATS = re.compile(r'\[STOCK STATS\] t=\S+ sweeps=(\d+) \S+ 200=(\d+) 403=(\d+) 429=(\d+) other=(\d+)')
R_M1 = re.compile(r'\[TOKEN\] (\S+): mint rung1 token_refresh -> (status=\d+|error=\S+)')
R_M2 = re.compile(r'\[TOKEN\] (\S+): mint rung2 after /account reload final_url=(\S+) accessToken=(\w+)')
R_NOMINT = re.compile(r'\[TOKEN\] (\S+): could NOT mint a member token')
R_ALT_OPEN = re.compile(r'\[STOCK\] VERIFY CONFIRMED IN STOCK: (\d+)|'
                        r'\[GROUND-TRUTH\] FIRING PURCHASE \(cold/stale catch\): (\d+)')
R_FLIP = re.compile(r'\[STOCK\]\[FLIP\] tcin=(\d+) #(\d+) read_ms=(\d{13}) rt_ms=(\S+) '
                    r'last_oos_ms=(\d+) since_oos_ms=(\S+) new_window=([01]) via=\S+ \(\S+\) '
                    r'status=(\S+) atp=(.*?) max_order_qty=(.*?) purchase_limit=(.*?) services=')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('log')
    a = ap.parse_args()
    with open(a.log, 'r', encoding='utf-8', errors='replace') as fh:
        lines = fh.read().splitlines()

    instock, races, atc, wf = Counter(), [], Counter(), Counter()
    shots = defaultdict(list)              # (ident, tcin) -> [atc_t0 ms]
    last_fire, carts, po_in_chain, fs_tix, orders, danger = None, 0, 0, Counter(), 0, []
    rf_events = []                         # (line_no, ident)
    wcd_start = []                         # (line_no, entry, ident, tcin)
    wcd_ticket = Counter()
    legacy_at = defaultdict(list)          # ident-agnostic legacy markers by line
    s206, stats, m1, m2, nomint = [], [], Counter(), Counter(), Counter()
    flips = []                             # (tcin, n, read_ms, since_oos, new_window, atp, mq, pl)
    alt_open = Counter()                   # tcin -> VERIFY / GROUND-TRUTH openings
    pending_ident = None
    for i, ln in enumerate(lines):
        for m in R_INSTOCK.finditer(ln):
            instock[m.group(1)] += 1
        for m in R_RACE.finditer(ln):
            races.append((m.group(1), int(m.group(2))))
        for m in R_FIRE.finditer(ln):
            last_fire = m.group(1)
        for m in R_ATC.finditer(ln):
            atc[(m.group(1), m.group(2))] += 1
            if m.group(1) == '201':
                carts += 1
        for m in R_CHAIN.finditer(ln):
            ident, t0 = m.group(4), int(m.group(5))
            shots[(ident, last_fire)].append(t0)
            if m.group(3) not in ('0', '-'):
                po_in_chain += 1
            if m.group(1) in ('200', '201') and m.group(3) == '429':
                pending_ident = ident
        if R_RF.search(ln) and pending_ident:
            rf_events.append((i, pending_ident))
            pending_ident = None
        for m in R_WF.finditer(ln):
            wf[m.group(1)] += 1
        for m in R_WCD_START.finditer(ln):
            wcd_start.append((i, m.group(1), m.group(2), m.group(3)))
        for m in R_WCD_TICKET.finditer(ln):
            wcd_ticket[m.group(1)] += 1
        if R_LEGACY.search(ln):
            legacy_at['any'].append(i)
        for m in R_FS_TICKET.finditer(ln):
            fs_tix[(m.group(2), m.group(3))] += 1
        if R_ORDER.search(ln):
            orders += 1
        if R_DANGER.search(ln):
            danger.append((i + 1, ln.strip()[:140]))
        for m in R_206.finditer(ln):
            s206.append(m.group(1))
        m = R_STATS.search(ln)
        if m:
            stats.append(tuple(int(x) for x in m.groups()))
        for m in R_M1.finditer(ln):
            m1[(m.group(1), m.group(2))] += 1
        for m in R_M2.finditer(ln):
            url = m.group(2)
            where = 'login_redirect' if '/login' in url else ('account' if '/account' in url else 'other')
            m2[(m.group(1), where, m.group(3))] += 1
        for m in R_NOMINT.finditer(ln):
            nomint[m.group(1)] += 1
        for m in R_ALT_OPEN.finditer(ln):
            alt_open[m.group(1) or m.group(2)] += 1
        for m in R_FLIP.finditer(ln):
            flips.append((m.group(1), int(m.group(2)), int(m.group(3)), m.group(6), m.group(7) == '1',
                          m.group(9), m.group(10), m.group(11)))

    out = []
    w = out.append
    w('=' * 78)
    w('PRE-REGISTERED READOUT -- arming of 2026-09-25   log: %s' % a.log)
    w('=' * 78)

    w('')
    w('S0  FUNNEL (measured; in-stock updates include level re-arm re-publications)')
    w('    in-stock updates by TCIN: %s' % (dict(instock) or '(none)'))
    w('    races=%d  width=%s' % (len(races), dict(Counter(n for _, n in races))))
    adm = sum(v for (s, k), v in atc.items() if s == '201' or (s == '429' and 'FAST_SELLING' in k))
    w('    main-tab add-to-cart: %d  %s' % (sum(atc.values()), dict(atc)))
    w('    admitted past the edge (201 or FAST_SELLING 429): %d   carts (201): %d' % (adm, carts))
    w('    place-orders: in-chain %d + [FS_TICKET] %d %s   order markers: %d' % (
        po_in_chain, sum(fs_tix.values()), dict(fs_tix), orders))

    w('')
    w('K1  CADENCE (TARGET_WAVE_FIRST_EDGE=1; gaps from atc_t0)')
    gaps = []
    for k, ts in shots.items():
        ts = sorted(ts)
        gaps += [(b - a2) / 1000.0 for a2, b in zip(ts, ts[1:]) if 0 < b - a2 < 200000]
    w('    [WAVE_FIRST] ATC-level lines by kind: %s' % (dict(wf) or '(none)'))
    if not gaps:
        k1 = 'NO DATA -- fewer than 2 shots per account on any TCIN'
    else:
        med = statistics.median(gaps)
        w('    same-account gaps: n=%d median=%.1fs min=%.1fs max=%.1fs' % (len(gaps), med, min(gaps), max(gaps)))
        if med < 10:
            k1 = 'FAIL -- median gap %.1fs < 10 s: the revert did not take' % med
        elif med >= 30 and (wf.get('edge', 0) + wf.get('dco', 0)) >= 1:
            k1 = 'PASS'
        else:
            k1 = 'INCONCLUSIVE -- median %.1fs, edge/dco wave-first lines %d' % (
                med, wf.get('edge', 0) + wf.get('dco', 0))
    w('    VERDICT: %s' % k1)

    w('')
    w('R1  RF ENTRY (TARGET_WONCART_RF_ENTRY=1)')
    verdicts = []
    for i, ident in rf_events:
        nxt_start = next((s for s in wcd_start if s[0] > i and s[2] == ident and s[0] - i <= 400), None)
        nxt_legacy = next((j for j in legacy_at['any'] if j > i), None)
        if nxt_start and (nxt_legacy is None or nxt_start[0] < nxt_legacy):
            verdicts.append(('RF_LOOP', ident, i + 1))
        else:
            verdicts.append(('RF_LEGACY', ident, i + 1))
    for v in verdicts:
        w('      %s ident=%s at line %d  (loop tickets by that ident tonight: %d)' % (
            v[0], v[1], v[2], wcd_ticket.get(v[1], 0)))
    for d in danger[:5]:
        w('      DANGER line %d: %s' % d)
    if danger:
        r1 = 'FAIL -- AMBIGUOUS_COMMIT / DOUBLE-BUY GUARD present (check the order history now)'
    elif not verdicts:
        r1 = 'NO DATA -- no in-chain 429 RESERVATION_FAILURE'
    elif any(v[0] == 'RF_LEGACY' for v in verdicts):
        r1 = 'FAIL -- an RF cart took the legacy path'
    elif all(wcd_ticket.get(v[1], 0) >= 3 for v in verdicts):
        r1 = 'PASS'
    else:
        r1 = 'INCONCLUSIVE -- an RF cart entered the loop but fired < 3 tickets'
    w('    VERDICT: %s' % r1)

    w('')
    w('L1  206 LOG (RESILIENT_206_LOG=1)')
    bursts = 0
    for p, c in zip(stats, stats[1:]):            # (sweeps, 200, 403, 429, other), cumulative
        dsw = c[0] - p[0]
        dbad = (c[2] + c[3] + c[4]) - (p[2] + p[3] + p[4])
        if dsw > 0 and dbad / float(dsw) >= 0.30:
            bursts += 1
    w('    >=30%% failing 30 s intervals: %d   [STOCK][206] lines: %d' % (bursts, len(s206)))
    for s in s206[:3]:
        w('      %s' % s[:380])
    if not bursts:
        l1 = 'NO DATA -- no monitor burst'
    elif s206:
        l1 = 'PASS'
    else:
        l1 = 'FAIL -- bursts without a single 206 line: the burst was not 206; re-open C-0925-03'
    w('    VERDICT: %s' % l1)

    w('')
    w('L2  MINT LOG (TARGET_TOKEN_MINT_LOG=1) -- measured')
    w('    rung1 status by account: %s' % (dict(m1) or '(none)'))
    w('    rung2 landing by account: %s' % (dict(m2) or '(none)'))
    w('    could NOT mint by account: %s' % (dict(nomint) or '(none)'))

    w('')
    w('L3  FLIP LOG (RESILIENT_FLIP_LOG=1)')
    t0s = sorted(t0 for ts in shots.values() for t0 in ts)
    new = [f for f in flips if f[4]]
    w('    [STOCK][FLIP] lines: %d (new windows %d)' % (len(flips), len(new)))
    delays = []
    for f in new:
        nxt = next((t0 for t0 in t0s if t0 >= f[2]), None)
        d = (nxt - f[2]) if nxt is not None and nxt - f[2] <= 3000 else None
        amb = any(g[0] != f[0] and abs(g[2] - f[2]) <= 3000 for g in flips)
        if d is not None and not amb:
            delays.append(d)
        w('      tcin=%s #%d since_oos_ms=%s first_shot_after_ms=%s%s atp=%s max_order_qty=%s purchase_limit=%s' % (
            f[0], f[1], f[3], '-' if d is None else d, ' (ambiguous)' if amb else '', f[5], f[6], f[7]))
    if delays:
        w('    read -> first shot, unambiguous: n=%d median=%dms min=%dms max=%dms' % (
            len(delays), statistics.median(delays), min(delays), max(delays)))
    if alt_open:
        w('    opened by VERIFY / GROUND-TRUTH instead (ms log stamp, no FLIP): %s' % dict(alt_open))
    raced = sorted(set(t for t, _ in races))
    missing = [t for t in raced if t not in set(f[0] for f in flips) and t not in alt_open]
    if not raced:
        l3 = 'NO DATA -- no race'
    elif missing:
        l3 = 'FAIL -- raced TCIN(s) with no FLIP line: %s' % missing
    else:
        l3 = 'PASS'
    w('    VERDICT: %s' % l3)
    w('')
    w('=' * 78)
    print('\n'.join(out))


if __name__ == '__main__':
    sys.exit(main())

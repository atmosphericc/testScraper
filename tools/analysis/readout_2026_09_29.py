#!/usr/bin/env python3
"""Pre-registered readout for the 2026-09-29 drop (docs/CLAIMS.md rule 3).

REGISTERED 2026-09-28 ~23:30, BEFORE the run, from the /pre-drop.

    python tools/analysis/readout_2026_09_29.py logs/runs/run_<ts>.log

Runs readout_arm_2026_09_25.py (K1 cadence, R1 RF entry, L1 206, L2 mint, L3 flip;
all still armed, and never judged live: the 09-28 day run had no race) and then
adds one family of rules, from the 09-28 pre-drop finding:

  Target's member-token mint has failed for us since 09-25 ~06:00 (rung 1
  gsp token_refresh -> 404; rung 2 /account reload -> no accessToken). A hand
  login's member token is therefore the only one an account gets. When the
  keep-fresh repair fires (ttl < TARGET_TOKEN_MIN_TTL_S), both rungs fail, the
  sentinel escalates, and on 09-28 every scripted credential re-login (0/6) left
  the account a GUEST (idToken sut=G stamped within ~6 s of each attempt).
  An account whose write-auth is dead fires 401s into the window's first volley,
  the only volley that has passed the edge since September (C-0925-07).

TESTS
  W1 WRITE-AUTH AT THE WINDOW: every account's FIRST add-to-cart of each race
     (the [FAST_LANE] chain line, by ident, after the race's [RACE] dispatch).
     PASS  no first shot was a 401.
     FAIL  any first shot was a 401. Each is labelled by its key: ERR_UNAUTHORIZED =
           the account had no member token at the window (check W2 for when it died);
           keyless '-' = a Shape token burn (C-0925-06, 2 on 09-25); '?' = the response
           line was not adjacent to its chain line (positional join, unlabelled).
     NO DATA  no race.
  W2 SESSION SURVIVAL (measured): per account, boot time, the first
     "WRITE-AUTH DEAD", the first "could NOT mint", the first "logged_in=False",
     and every "[RELOGIN] credential re-login" with its outcome.
     Gate check: any "[RELOGIN] credential re-login" line while
     TARGET_RELOGIN_MAX_PER_6H=0 is armed is a FAIL (the gate did not hold).
  W3 DECOY WRITE MIX (measured): warmup [ATC_RESP] status + key by hour. The
     401 ERR_UNAUTHORIZED share is the fleet's dead-write-auth share.

ARMED 2026-09-28 pre-drop (run_bot_with_nightly_restart.bat, existing flags only):
  K2  TARGET_TOKEN_KEEPFRESH=0    no in-bot path deletes a live member token (the
                                  keep-fresh repair and the 401x3 heartbeat repair
                                  both go through ensure_fresh_access_token, which
                                  now returns False first).
      PASS  0 "[TOKEN] ... token unhealthy", 0 "mint rung1", 0 "mint rung2" lines.
      FAIL  any of them: the flag did not take.
  K2b TARGET_RELOGIN_MAX_PER_6H=0 no in-bot scripted sign-out + login. W2's gate check.
  K3  RE-MINT WATCH (measured): per account, the first "WRITE-AUTH DEAD" (expected at
      its token's 4 h expiry) and any "write-auth alive" after it. An alive line
      after a confirmed death = the legacy /account check (or something) re-minted:
      the first in-bot mint since 09-25 -- quote it. CAVEAT: a 424 "alive" does not
      prove MEMBER -- a guest token passes the decoy write too (09-28: primary and
      alt-1 read 424 for ~40 min after becoming guests at 19:45).
  Smoked 2026-09-28 on run_20260928_161223.log (old arming: K2 FAIL 662 lines, as it
  must; W1 NO DATA; W2 6/6 re-logins failed) and run_20260925_004139.log (W1 FAIL:
  primary's 2 keyless-401 first shots = C-0925-06; W2 5 attempts, 2 OK).

Read-only over the log. Stdlib only.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
TS = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)')
T = r'(\d{8,10})(?=\d{4}-\d\d-\d\d|\D|$)'
R_RACE = re.compile(r'\[RACE\] ' + T + r': racing (\d+) accounts')
R_CHAIN = re.compile(r'\[FAST_LANE\] chain done .*?atc=(\S+) pre=\S+ po=\S+ .*?ident=(\S+) atc_t0=(\d{13})')
R_DEAD = re.compile(r'\[WARMUP#\d+/(\S+?)\] WRITE-AUTH DEAD')
R_NOMINT = re.compile(r'\[TOKEN\] (\S+): could NOT mint a member token')
R_LOGGED_OUT = re.compile(r'\[SENTINEL\] (?:W\d+/)?(\S+?): logged_in=False')
R_RELOGIN = re.compile(r'\[RELOGIN\] credential re-login for (\S+)')
R_RELOGIN_FAIL = re.compile(r'\[RELOGIN\] credential re-login failed for (\S+)')
R_RELOGIN_OK = re.compile(r'\[RELOGIN\] \[OK\] credential re-login succeeded for (\S+)')
R_WARM = re.compile(r'\[ATC_RESP\] status=(\d+) method=POST tgt-cart-error-key=(\S*) .*?tab=warmup')
R_K2 = re.compile(r'\[TOKEN\] (\S+): (token unhealthy|mint rung1|mint rung2)')
R_ALIVE = re.compile(r'\[WARMUP#\d+/(\S+?)\] (?:dummy POST status \d+ — write-auth alive|transient 401 — re-probe)')
R_LEGACY_NAV = re.compile(r'\[OK\] Token refresh successful')
# The main-tab response print normally lands just before its own chain line; used
# only to label a first shot's 401 key (keyless = Shape token burn vs
# ERR_UNAUTHORIZED = no member token). Positional, so '?' when not adjacent.
R_MAIN_RESP = re.compile(r'\[INTERCEPTOR:main\] \[ATC_RESP\] status=(\d+) method=POST tgt-cart-error-key=(\S*)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('log')
    a = ap.parse_args()

    base = os.path.join(HERE, 'readout_arm_2026_09_25.py')
    p = subprocess.run([sys.executable, base, a.log], capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    print(p.stdout, end='')
    if p.returncode != 0:
        print('readout_arm_2026_09_25.py exited %d: %s' % (p.returncode, p.stderr[-400:]))

    with open(a.log, 'r', encoding='utf-8', errors='replace') as fh:
        lines = fh.read().splitlines()

    last_ts, boot_ts = None, None
    race_open = []                     # (line_no, tcin, width)
    firsts = []                        # (race_idx, ident, atc, t0)
    seen = set()                       # (race_idx, ident)
    first = defaultdict(dict)          # acct -> {kind: ts}
    relog = []                         # (ts, acct, 'attempt'|'failed'|'ok')
    warm = Counter()                   # (hour, status, key)
    k2 = Counter()                     # (acct, kind)
    dead_at = {}                       # acct -> (line, ts) of the first WRITE-AUTH DEAD
    alive_after = defaultdict(list)    # acct -> [ts] write-auth alive after dead_at
    legacy_nav = 0
    for i, ln in enumerate(lines):
        m = TS.match(ln)
        if m:
            last_ts = m.group(1)
            boot_ts = boot_ts or last_ts
        for m in R_RACE.finditer(ln):
            race_open.append((i, m.group(1), int(m.group(2))))
        for m in R_CHAIN.finditer(ln):
            if race_open:
                k = (len(race_open) - 1, m.group(2))
                if k not in seen:
                    seen.add(k)
                    key = '?'
                    for j in range(i - 1, max(-1, i - 9), -1):
                        mr = R_MAIN_RESP.search(lines[j])
                        if mr:
                            if mr.group(1) == m.group(1):
                                key = mr.group(2) or '-'
                            break
                    firsts.append((k[0], m.group(2), m.group(1), int(m.group(3)), key))
        for rx, kind in ((R_DEAD, 'write_auth_dead'), (R_NOMINT, 'could_not_mint'),
                         (R_LOGGED_OUT, 'logged_in_false')):
            for m in rx.finditer(ln):
                first[m.group(1)].setdefault(kind, last_ts)
        for m in R_RELOGIN.finditer(ln):
            relog.append((last_ts, m.group(1).rstrip('.'), 'attempt'))
        for m in R_RELOGIN_FAIL.finditer(ln):
            relog.append((last_ts, m.group(1), 'failed'))
        for m in R_RELOGIN_OK.finditer(ln):
            relog.append((last_ts, m.group(1), 'OK'))
        for m in R_WARM.finditer(ln):
            hour = (last_ts or '????-??-?? ??')[11:13]
            warm[(hour, m.group(1), m.group(2))] += 1
        for m in R_K2.finditer(ln):
            k2[(m.group(1), m.group(2))] += 1
        for m in R_DEAD.finditer(ln):
            dead_at.setdefault(m.group(1), (i, last_ts))
        for m in R_ALIVE.finditer(ln):
            if m.group(1) in dead_at and i > dead_at[m.group(1)][0]:
                alive_after[m.group(1)].append(last_ts)
        if R_LEGACY_NAV.search(ln):
            legacy_nav += 1

    out = []
    w = out.append
    w('')
    w('=' * 78)
    w('PRE-REGISTERED READOUT -- 2026-09-29 additions (W1-W3)   log: %s' % a.log)
    w('=' * 78)

    w('')
    w('W1  WRITE-AUTH AT THE WINDOW (first add-to-cart per account per race)')
    if not race_open:
        w1 = 'NO DATA -- no race'
    else:
        bad = [f for f in firsts if f[2] == '401']
        by = Counter((f[1], f[2]) for f in firsts)
        w('    races=%d  first shots=%d  by (ident, atc): %s' % (len(race_open), len(firsts), dict(by)))
        for ri, ident, atc, t0, key in bad:
            w('      401 first shot: race #%d tcin=%s ident=%s atc_t0=%d key=%s%s' % (
                ri + 1, race_open[ri][1], ident, t0, key,
                '  (no member token)' if key == 'ERR_UNAUTHORIZED' else
                ('  (keyless: Shape token burn, C-0925-06)' if key == '-' else '')))
        w1 = ('FAIL -- %d first shot(s) were 401: %s (ERR_UNAUTHORIZED=%d, keyless=%d, unlabelled=%d)' % (
            len(bad), sorted(set(f[1] for f in bad)), sum(1 for f in bad if f[4] == 'ERR_UNAUTHORIZED'),
            sum(1 for f in bad if f[4] == '-'), sum(1 for f in bad if f[4] == '?'))) if bad else 'PASS'
    w('    VERDICT: %s' % w1)

    w('')
    w('W2  SESSION SURVIVAL (measured; boot %s, last line %s)' % (boot_ts, last_ts))
    w('    (WRITE-AUTH DEAD is a 401x3 heartbeat verdict and fires on keyless Shape-burn 401s too;')
    w('     could-NOT-mint is the clean "member token gone" signal)')
    for acct in sorted(set(list(first.keys()) + [r[1] for r in relog])):
        f = first.get(acct, {})
        w('    %-9s first WRITE-AUTH DEAD=%s  first could-NOT-mint=%s  first logged_in=False=%s' % (
            acct, f.get('write_auth_dead', '-'), f.get('could_not_mint', '-'), f.get('logged_in_false', '-')))
    if not first:
        w('    no account ever lost write-auth or failed a mint')
    attempts = [r for r in relog if r[2] == 'attempt']
    w('    scripted credential re-logins: %d attempts, %d failed, %d OK (unresolved %d)' % (
        len(attempts), sum(1 for r in relog if r[2] == 'failed'), sum(1 for r in relog if r[2] == 'OK'),
        len(attempts) - sum(1 for r in relog if r[2] in ('failed', 'OK'))))
    for r in relog[:16]:
        w('      %s %s %s' % r)
    w('    (FAIL if any attempt appears while TARGET_RELOGIN_MAX_PER_6H=0 is armed)')

    w('')
    w('W3  DECOY WRITE MIX (warmup [ATC_RESP], by hour)')
    hours = sorted(set(h for h, _, _ in warm))
    for h in hours:
        row = {'%s %s' % (s, k): n for (hh, s, k), n in warm.items() if hh == h}
        tot = sum(row.values())
        dead = sum(n for (hh, s, k), n in warm.items() if hh == h and s == '401' and k == 'ERR_UNAUTHORIZED')
        w('    %sh  n=%d  401 ERR_UNAUTHORIZED=%d (%.0f%%)  %s' % (h, tot, dead, 100.0 * dead / tot if tot else 0, row))
    if not warm:
        w('    (no warmup [ATC_RESP] lines)')

    w('')
    w('K2  KEEP-FRESH OFF (TARGET_TOKEN_KEEPFRESH=0)')
    w('    token-repair lines by (account, kind): %s' % (dict(k2) or '(none)'))
    w('    legacy /account check successes ([OK] Token refresh successful): %d' % legacy_nav)
    w('    VERDICT: %s' % ('FAIL -- %d token-repair line(s): the flag did not take' % sum(k2.values())
                          if k2 else 'PASS'))

    w('')
    w('K3  RE-MINT WATCH (measured)')
    for acct in sorted(dead_at):
        al = alive_after.get(acct, [])
        w('    %-9s first WRITE-AUTH DEAD=%s  write-auth alive after it: %d%s' % (
            acct, dead_at[acct][1], len(al), (' (first %s, last %s)' % (al[0], al[-1])) if al else ''))
    if not dead_at:
        w('    no account logged WRITE-AUTH DEAD')
    w('    (alive lines after a 401x3 false alarm are expected; after the 4 h expiry they')
    w('     would be the first in-bot re-mint since 2026-09-25 -- check the times. A 424')
    w('     "alive" is NOT proof of a MEMBER token: guest tokens pass the decoy write too')
    w('     -- 09-28 primary/alt-1 read 424 until ~20:24 while already guests)')
    w('')
    w('=' * 78)
    print('\n'.join(out))


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Pre-registered readout for the 2026-09-30 03:00 restock (docs/CLAIMS.md rule 3).

REGISTERED 2026-09-30 ~00:30, BEFORE the run, from the /pre-drop.

    python tools/analysis/readout_2026_09_30.py logs/runs/run_<ts>.log [--jars]

Runs readout_2026_09_29.py (which runs readout_arm_2026_09_25.py: K1 cadence, R1 RF
entry, L1 206, L2 mint, L3 flip; then W1 first-shot 401s, W2 session survival, W3
decoy mix, K2 keep-fresh off, K3 re-mint watch) and adds:

  N1 NEW TCIN 95082118 (operator, added 2026-09-29 late; never in any config or log
     before). Report: INVISIBLE / NOW VISIBLE banners naming it, [STOCK] IN STOCK and
     [STOCK][FLIP] lines for it, [RACE] dispatches, first-shot outcomes.
     PASS  it was visible to RedSky (named by no INVISIBLE banner, or a NOW VISIBLE
           followed) -- a drop on it was detectable.
     FAIL  named by an INVISIBLE banner and never NOW VISIBLE: invisible all run.
     (A missing banner is the good case; the banner fires on first sighting.)

  M1 TOKEN CONTINUITY (C-0929-01, measured 2026-09-29, n=12 expiries / 0 lapses):
     with TARGET_TOKEN_KEEPFRESH=0 + TARGET_RELOGIN_MAX_PER_6H=0 a member token is
     re-minted inside the running bot at its 4 h expiry. Tonight's jars expire
     01:11:25 / 01:12:44 / 01:14:29 (09-30) and, if re-minted, again ~05:11-05:14.
     PASS  every hour of the run has 0% "401 ERR_UNAUTHORIZED" in the decoy mix AND
           no race first shot answered 401 ERR_UNAUTHORIZED.
     FAIL  any hour with >=5% ERR_UNAUTHORIZED decoys (n>=20), or any first shot
           401 ERR_UNAUTHORIZED. Then the re-mint did not hold -- quote the hour.
     NO DATA  no decoy lines.
     CAVEAT: the decoy mix only shows a MISSING token (ERR_UNAUTHORIZED). A GUEST
     token passes the decoy write with 424 (09-28), so this half cannot see a
     member -> guest downgrade; W2's logged_in=False lines and --jars can.
     --jars  also prints each saved jar's accessToken sut / iat / exp (read-only; no
             token value is printed) and adds to the verdict: FAIL if any jar is not
             sut=R. An iat later than the boot and not at a hand login = an in-bot
             mint (the direct evidence C-0929-01 rests on).
             Run it right after the bot stops (the bot saves the jars at shutdown).
             JAR VERDICT, per account: member (sut=R); VALID-AT-STOP (the saved
             token's exp is after the run's last log line -- a failed re-mint
             leaves the expired token in the jar); and GRID, judged only when the
             run was up before the base token expired: tonight's jars were issued
             2026-09-29 21:11:25 / 21:12:44 / 21:14:29, so if every expiry was
             re-minted in-bot within seconds the post-run iat = that base + k x 4 h
             + a small drift (09-29: +8..+22 s over five cycles); drift > 300 s =
             a lapse of minutes. A start after 01:14 makes the grid n/a (the
             wrapper-start pass mints off-grid). A hand login after 09-29 21:14
             resets the grid -- say so instead of reading it.
     NOTE (09-30 log-miner): every WRITE-AUTH DEAD burst on 09-29 was a KEYLESS 401
     that recovered by itself; an expired-but-present token likely answers keyless,
     not ERR_UNAUTHORIZED. So the decoy half above detects a DELETED token; the
     grid check detects a lapse.

  G1 GATE TABLE (measured, added 2026-09-30 ~01:30, still before the run): every race
     shot by account x first/later shot x credential (banked cookie / page-signed) x the
     gate it stopped at: shape_401 / limiter_429 / admitted (CART, FS429, 424, 400...).
     Baseline (five nights to 09-25, home line): first shots ~7-12% admitted, later shots
     0 of 1,300+, page-signed 0 of 1,000. Any admitted LATER shot is new -- quote it.
  M1 grid base: a forced hand login found in logs/relogin.log ([MANUAL] then [RESULT]
     LOGGED IN) after 09-29 21:15 and before the boot replaces the 09-29 base for that
     account; drift is measured to the nearest 4 h grid point (|drift| <= 300 s).

Read-only over the log (and, with --jars, over target*.json + logs/relogin.log). Stdlib only.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
NEW = '95082118'
TS = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)')
T = r'(\d{8,10})(?=\d{4}-\d\d-\d\d|\D|$)'
R_RACE = re.compile(r'\[RACE\] ' + T + r': racing (\d+) accounts')
R_CHAIN = re.compile(r'\[FAST_LANE\] chain done .*?atc=(\S+) pre=\S+ po=\S+ .*?ident=(\S+) atc_t0=(\d{13})')
R_INSTOCK = re.compile(r'\[STOCK\] IN STOCK: ' + T)
R_FLIP = re.compile(r'\[STOCK\]\[FLIP\] tcin=' + T + r' #')
R_INVIS = re.compile(r'\[TCIN-VISIBILITY\] \d+ of \d+ configured TCIN\(s\) are INVISIBLE')
R_NOWVIS = re.compile(r'\[TCIN-VISIBILITY\] NOW VISIBLE in RedSky')
R_WARM = re.compile(r'\[ATC_RESP\] status=(\d+) method=POST tgt-cart-error-key=(\S*) .*?tab=warmup')
R_MAIN_RESP = re.compile(r'\[INTERCEPTOR:main\] \[ATC_RESP\] status=(\d+) method=POST tgt-cart-error-key=(\S*)')
R_HDRS = re.compile(r'\[ATC_RESP_HDRS\] tab=(\w+) status=(\d+) n=\d+(.*)$')
JARS = (('primary', 'target.json'), ('business', 'target-2.json'), ('alt-1', 'target-3.json'))
R_REPLAY = re.compile(r'\[HARVEST/([\w-]+)\] REPLAY on main shot: banked set age=(\d+)s')
R_EMPTY = re.compile(r'\[HARVEST/([\w-]+)\] bank EMPTY at shot time')
R_MANUAL = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d) \[MANUAL\] ([\w-]+):')
R_LOGGED = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d) \[RESULT\] ([\w-]+): LOGGED IN')


def _gate(atc, key):
    """Which gate a main-tab add-to-cart stopped at (09-30 model: Shape -> hot-item
    limiter -> cart service). 'unknown' is counted, never folded into a bucket."""
    k = str(key or '').upper()
    if atc in ('200', '201'):
        return 'CART'
    if atc == '401':
        return 'shape_401'
    if atc == '429':
        if 'FAST_SELLING' in k:
            return 'admitted_FS429'
        if 'ERR_A2C_TCIN_RATE_LIMITED' in k or k in ('-', ''):
            return 'limiter_429'
        return 'unknown_429'
    if atc in ('424', '400', '409', '422'):
        return 'admitted_' + atc
    return 'unknown_' + str(atc)


def _hand_login_bases(after_ep, before_ep):
    """{acct: epoch} of each account's LAST forced hand login ([MANUAL] then [RESULT]
    LOGGED IN in logs/relogin.log) between after_ep and before_ep. Read-only."""
    out, manual = {}, set()
    try:
        with open(os.path.join(ROOT, 'logs', 'relogin.log'), encoding='utf-8', errors='replace') as fh:
            for ln in fh:
                m = R_MANUAL.match(ln)
                if m:
                    manual.add(m.group(2))
                    continue
                m = R_LOGGED.match(ln)
                if m and m.group(2) in manual:
                    ep = int(dt.datetime.strptime(m.group(1), '%Y-%m-%dT%H:%M:%S').timestamp())
                    if after_ep < ep < before_ep:
                        out[m.group(2)] = ep
                    manual.discard(m.group(2))
    except Exception:
        pass
    return out
# The 09-29 in-bot mints the 09-30 run starts from (jars saved 09-29 23:13), local time.
GRID_BASE = {acct: int(dt.datetime(2026, 9, 29, h, m, s).timestamp())
             for acct, (h, m, s) in (('primary', (21, 11, 25)), ('business', (21, 12, 44)),
                                     ('alt-1', (21, 14, 29)))}


def _jwt(tok):
    try:
        p = tok.split('.')[1]
        p += '=' * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p))
    except Exception:
        return {}


def _fmt(epoch):
    try:
        return dt.datetime.fromtimestamp(int(epoch)).strftime('%m-%d %H:%M:%S')
    except Exception:
        return '?'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('log')
    ap.add_argument('--jars', action='store_true')
    a = ap.parse_args()

    base = os.path.join(HERE, 'readout_2026_09_29.py')
    p = subprocess.run([sys.executable, base, a.log], capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    print(p.stdout, end='')
    if p.returncode != 0:
        print('readout_2026_09_29.py exited %d: %s' % (p.returncode, p.stderr[-400:]))

    with open(a.log, 'r', encoding='utf-8', errors='replace') as fh:
        lines = fh.read().splitlines()

    last_ts, boot_ts = None, None
    invis_named, nowvis_named = [], []
    instock, flips = [], []
    races = []                          # (line_no, tcin, width, ts)
    firsts = []                         # (tcin, ident, atc, key)
    seen = set()
    warm = Counter()                    # (hour, status, key)
    cred = {}                           # ident -> ('cookie'|'page', line_no)
    shot_n = Counter()                  # (race_idx, ident) -> shots so far
    allshots = []                       # (ident, first|later, cookie|page|none, gate)
    hdrs = []                           # (tab, status, ' | k=v | ...') from [ATC_RESP_HDRS]
    for i, ln in enumerate(lines):
        m = TS.match(ln)
        if m:
            last_ts = m.group(1)
            boot_ts = boot_ts or last_ts
        if R_INVIS.search(ln) and NEW in ln:
            invis_named.append(last_ts)
        if R_NOWVIS.search(ln) and NEW in ln:
            nowvis_named.append(last_ts)
        for m in R_INSTOCK.finditer(ln):
            if m.group(1) == NEW:
                instock.append(last_ts)
        for m in R_FLIP.finditer(ln):
            if m.group(1) == NEW:
                flips.append(last_ts)
        for m in R_RACE.finditer(ln):
            races.append((i, m.group(1), int(m.group(2)), last_ts))
        for m in R_REPLAY.finditer(ln):
            cred[m.group(1)] = ('cookie', i)
        for m in R_EMPTY.finditer(ln):
            cred[m.group(1)] = ('page', i)
        for m in R_CHAIN.finditer(ln):
            if not races:
                continue
            ridx = len(races) - 1
            k = (ridx, m.group(2))
            # G1: every shot, with its shot index in the race and its credential
            _c = cred.get(m.group(2))
            _src = _c[0] if _c and i - _c[1] <= 400 else 'none'
            _key = '?'
            for j in range(i - 1, max(-1, i - 12), -1):
                mr = R_MAIN_RESP.search(lines[j])
                if mr:
                    if mr.group(1) == m.group(1):
                        _key = mr.group(2) or '-'
                    break
            shot_n[k] += 1
            allshots.append((m.group(2), 'first' if shot_n[k] == 1 else 'later', _src,
                             _gate(m.group(1), _key), races[ridx][1]))
            if k in seen:
                continue
            seen.add(k)
            key = '?'
            for j in range(i - 1, max(-1, i - 9), -1):
                mr = R_MAIN_RESP.search(lines[j])
                if mr:
                    if mr.group(1) == m.group(1):
                        key = mr.group(2) or '-'
                    break
            firsts.append((races[ridx][1], m.group(2), m.group(1), key))
        m = R_HDRS.search(ln)
        if m and '[INTERCEPTOR:' in ln:
            hdrs.append((m.group(1), m.group(2), m.group(3)))
        m = R_WARM.search(ln)
        if m and last_ts:
            warm[(last_ts[11:13], m.group(1), m.group(2))] += 1

    out = []
    w = out.append
    w('')
    w('=' * 78)
    w('2026-09-30 ADDITIONS (pre-registered 2026-09-30 ~00:30)')
    w('=' * 78)

    w('N1  NEW TCIN %s' % NEW)
    w('    INVISIBLE banners naming it: %d%s' % (len(invis_named), (' (first %s)' % invis_named[0]) if invis_named else ''))
    w('    NOW VISIBLE naming it:       %d%s' % (len(nowvis_named), (' (first %s)' % nowvis_named[0]) if nowvis_named else ''))
    w('    [STOCK] IN STOCK reads: %d   [STOCK][FLIP]: %d%s' % (
        len(instock), len(flips), (' (first flip near %s)' % flips[0]) if flips else ''))
    nr = [r for r in races if r[1] == NEW]
    w('    races: %d  widths=%s' % (len(nr), [r[2] for r in nr]))
    nf = [f for f in firsts if f[0] == NEW]
    w('    race first shots: %s' % (Counter('%s %s' % (f[2], f[3]) for f in nf) or '(none)'))
    if invis_named and not nowvis_named:
        v1 = 'FAIL -- invisible to RedSky all run: a drop on it could not be detected'
    else:
        v1 = 'PASS -- visible (no INVISIBLE banner%s)' % (', or NOW VISIBLE followed' if invis_named else '')
    w('    VERDICT: %s' % v1)

    w('')
    w('M1  TOKEN CONTINUITY (re-mint at the 4 h expiry, C-0929-01)')
    hours = sorted(set(h for h, _, _ in warm))
    bad_hours = []
    for h in hours:
        tot = sum(n for (hh, s, k), n in warm.items() if hh == h)
        dead = sum(n for (hh, s, k), n in warm.items() if hh == h and s == '401' and k == 'ERR_UNAUTHORIZED')
        pct = 100.0 * dead / tot if tot else 0.0
        w('    %sh  decoys n=%d  401 ERR_UNAUTHORIZED=%d (%.1f%%)' % (h, tot, dead, pct))
        if tot >= 20 and pct >= 5.0:
            bad_hours.append(h)
    fs_unauth = [f for f in firsts if f[2] == '401' and f[3] == 'ERR_UNAUTHORIZED']
    w('    race first shots answered 401 ERR_UNAUTHORIZED: %d %s' % (
        len(fs_unauth), [(f[0], f[1]) for f in fs_unauth] if fs_unauth else ''))
    if not warm:
        v2 = 'NO DATA -- no decoy lines'
    elif bad_hours or fs_unauth:
        v2 = 'FAIL -- the re-mint did not hold (hours %s; first shots %d)' % (bad_hours, len(fs_unauth))
    else:
        v2 = 'PASS -- no hour with a dead-token decoy share >=5%, no ERR_UNAUTHORIZED first shot'
    w('    VERDICT: %s' % v2)

    w('')
    w('G1  GATE TABLE (measured; 09-30 model: Shape 401 -> hot-item limiter 429 -> cart service)')
    w('    baseline, home line, five nights to 09-25: first shot of a race ~7-12% admitted,')
    w('    every later shot 0 of 1,300+; page-signed 0 of 1,000. Any admitted LATER shot on the')
    w('    home line is the first ever -- quote it. "unknown_*" is counted, never re-bucketed.')
    w('    LAX-SKU CAVEAT: security is per SKU per night. On 09-17 two SKUs (1011483413,')
    w('    1011960739 -- the latter ENABLED tonight) admitted later shots freely; the 0-of-1,300')
    w('    baseline is the STRICT SKUs. Read later-shot admissions per TCIN, next to that TCIN')
    w('    first-shot rate: a lax SKU admits both; a trust breakthrough admits later shots on a')
    w('    SKU whose first shots are ~10%.')
    def _adm(c):
        return sum(v for g, v in c.items() if g == 'CART' or g.startswith('admitted_'))
    g1 = defaultdict(Counter)
    by_tcin = defaultdict(lambda: {'first': Counter(), 'later': Counter()})
    for ident, fl, src, gate, tcin in allshots:
        g1[(ident, fl, src)][gate] += 1
        by_tcin[tcin][fl][gate] += 1
    w('    by account x shot x credential:')
    for kk in sorted(g1):
        c = g1[kk]
        n = sum(c.values())
        w('      %-9s %-5s %-6s n=%4d  admitted=%d (%.1f%%)  %s' % (
            kk[0], kk[1], kk[2], n, _adm(c), 100.0 * _adm(c) / n if n else 0.0, dict(c)))
    w('    by TCIN (first shots | later shots):')
    for t in sorted(by_tcin):
        f, l = by_tcin[t]['first'], by_tcin[t]['later']
        nf, nl = sum(f.values()), sum(l.values())
        w('      %-10s first %d/%d admitted | later %d/%d admitted%s' % (
            t, _adm(f), nf, _adm(l), nl,
            ('   <-- later-shot admissions: lax SKU tonight, or a breakthrough (see caveat)'
             if _adm(l) else '')))
    if not allshots:
        w('    NO DATA -- no race shots')

    w('')
    w('H1  ADD-TO-CART RESPONSE HEADERS (TARGET_ATC_RESP_HDRS, armed 2026-09-30; measured)')
    w('    Questions, fixed before the run: (a) does the hot-item 429 carry Retry-After or')
    w('    x-ratelimit-* -- if yes, what values (a per-client penalty window would explain')
    w('    0/1,300 later shots); (b) which layer answers each status (header-name set per')
    w('    status: a 429 without envoy/upstream headers = rejected before Target services);')
    w('    (c) what a keyless 401 carries (Shape block vs token: www-authenticate? a body')
    w('    length? any x-* verdict header?). Values for the headers named below; names only')
    w('    for the rest. Each tab/status printed from its own lines.')
    if not hdrs:
        w('    NO DATA -- no [ATC_RESP_HDRS] lines (flag off, or no cart_items POST)')
    else:
        by = defaultdict(list)
        for tab_, st_, rest in hdrs:
            kv = {}
            for part in rest.split(' | '):
                if '=' in part:
                    k_, v_ = part.split('=', 1)
                    kv[k_.strip()] = v_.strip()
            by[(tab_, st_)].append(kv)
        KEYS = ('retry-after', 'x-ratelimit-limit', 'x-ratelimit-remaining', 'x-ratelimit-reset',
                'server', 'via', 'x-cache', 'content-length', 'content-type', 'cache-control',
                'www-authenticate', 'x-envoy-upstream-service-time', 'tgt-cart-error-key')
        for (tab_, st_), rows in sorted(by.items()):
            names = Counter(k_ for kv in rows for k_ in kv)
            w('    tab=%s status=%s n=%d' % (tab_, st_, len(rows)))
            w('      header names (count): %s' % dict(sorted(names.items())))
            for key in KEYS:
                vals = Counter(kv[key] for kv in rows if key in kv)
                if vals:
                    w('      %-30s %s' % (key, dict(vals.most_common(6))))
        ra = Counter(kv.get('retry-after', '<absent>') for (tab_, st_), rows in by.items()
                     if tab_ == 'main' and st_ == '429' for kv in rows)
        w('    ANSWER (a): main-tab 429 retry-after values: %s' % (dict(ra) or 'no main-tab 429'))

    if a.jars:
        # Read-only. Three checks per account, token values never printed:
        #   member      accessToken sut == 'R'
        #   valid@stop  exp > the run's last log line (a failed re-mint leaves the
        #               expired token in the jar; the bot saves the jar at shutdown)
        #   grid        only when the run was up before the base token expired: the
        #               post-run iat = base + k x 4 h + drift, drift <= 300 s
        def _ep(ts):
            try:
                return int(dt.datetime.strptime(ts, '%Y-%m-%d %H:%M:%S').timestamp())
            except Exception:
                return None
        boot_ep, end_ep = _ep(boot_ts or ''), _ep(last_ts or '')
        hand = _hand_login_bases(max(GRID_BASE.values()) + 60, boot_ep or 0)
        w('')
        w('    saved jars (accessToken; read-only, values never printed); run %s -> %s:' % (boot_ts, last_ts))
        fails = []
        for acct, fn in JARS:
            try:
                j = json.load(open(os.path.join(ROOT, fn), encoding='utf-8'))
                cookies = j.get('cookies', []) if isinstance(j, dict) else j
                tok = next((c.get('value', '') for c in cookies if c.get('name') == 'accessToken'), '')
                d = _jwt(tok) if tok else {}
            except Exception as e:
                w('      %-9s unreadable: %s' % (acct, type(e).__name__))
                fails.append('%s unreadable' % acct)
                continue
            if not d:
                w('      %-9s no accessToken' % acct)
                fails.append('%s no token' % acct)
                continue
            iat, exp = int(d.get('iat', 0) or 0), int(d.get('exp', 0) or 0)
            member = d.get('sut') == 'R'
            valid = bool(end_ep) and exp > end_ep
            base = GRID_BASE.get(acct)
            base_src = 'the 09-29 21:1x in-bot mint'
            if acct in hand:                      # a forced hand login resets the grid
                base, base_src = hand[acct], 'hand login %s' % _fmt(hand[acct])
            if base and boot_ep and boot_ep < base + 4 * 3600 - 60 and iat >= base - 120:
                k = int(round((iat - base) / (4 * 3600.0)))
                drift = (iat - base) - k * 4 * 3600   # signed, to the nearest grid point
                grid = '%d cycle(s) after %s, drift %+ds' % (k, base_src, drift)
                grid_bad = abs(drift) > 300
            else:
                grid = 'n/a (run started after the base token expired, or no base)'
                grid_bad = False
            w('      %-9s sut=%s iat=%s exp=%s  member=%s  valid-at-stop=%s  grid: %s' % (
                acct, d.get('sut'), _fmt(iat), _fmt(exp), 'Y' if member else 'N',
                'Y' if valid else 'N', grid))
            if not member:
                fails.append('%s not member' % acct)
            if not valid:
                fails.append('%s expired before the run stopped' % acct)
            if grid_bad:
                fails.append('%s off the 4 h grid' % acct)
        w('    JAR VERDICT: %s' % ('FAIL -- %s' % fails if fails else
                                  'PASS -- member, valid when the bot stopped, on the grid where applicable'))

    w('')
    w('=' * 78)
    print('\n'.join(out))
    return 0


if __name__ == '__main__':
    sys.exit(main())

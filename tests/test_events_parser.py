#!/usr/bin/env python3
"""Offline tests: tools/events/build.py + q.py -- the run-log event store (2026-09-30).

Every post-mortem used to re-parse 12-60 MB logs with fresh regexes; extraction slips
(print+logger double counts, glued lines, a phantom 'primary2' ident, truncated views)
produced wrong numbers more than once. These pins keep the one shared parser honest.
The synthetic lines copy the SHAPE of real run-log lines (no tokens, no cookie values,
no proxy credentials -- the only proxy line is a local 127.0.0.1 port).

Pins:
  - glued physical lines are split at every embedded logger timestamp; ident values stay
    bounded (a glued date or a glued print never extends them)
  - print + logger duplicates are counted once ([ATC_RESP] in both eras, [HARVEST] cred)
  - gate classification, including 'unknown' (no response, keyless or unrecognised 429)
  - shot_idx / is_first across interleaved accounts, two races, and D1 concurrent races
  - the response join: by timestamp, a response glued AFTER its own chain line, body-hint
    disambiguation, ambiguity tainting, resp_only rows, a timed-out (orphan) shot
  - [STOCK][FLIP] windows: new_window=1 opens a window until that TCIN's next one; the
    first race of a window is flip_opened; a run without flips gives NULL, never 0
  - the unparsed remainder, incl. a marker in the wrong (print vs logger) form
  - the SQLite build (incremental skip, re-parse on change or PARSER_VERSION, no duplicate
    rows) and q.py (named query, --run filter, -p parameter)
  v2 (2026-10-01):
  - INS-1: the 401 ladder's "ATC fast-retry / retry-2 succeeded (201)" lines are shots
    (src legacy_retry, gate cart), TCIN from the running "Starting purchase for" context,
    NULL + counted (PRETRY@tcin_null) when those executions span two TCINs
  - I-PO-2: place_orders -- POST->response FIFO, error key + Date header, path from the
    chain po= / [API_PLACE_ORDER] HTTP / [FS_TICKET] layer=po claim, ident/TCIN, the
    window (flip, else the read-based episode), cart link, order link; unpaired POSTs,
    HTTP-0 lines and unclassified POSTs counted in `unparsed`
  - FS-2: a 400's [PURCHASE] body "code" is the shot's err_key; MAX_PURCHASE_LIMIT_EXCEEDED
    is gate cart_limit (past the limiter), and every gate-enumerating query still reconciles
  - FS-4: ssx_sequence's per-TCIN switch reading and the pre-registered verdicts; po_by_age
    and late_carts run and bin
  v3 (2026-10-05):
  - INS-ATC-NET: [ATC_NET] lines -> atc_net (IPv4 / bracketed IPv6 ip:port, '-' -> NULL, a line
    glued to a logger stamp, a logger copy counted @wrong_form and never a second row, the
    format-error line counted); POST / OPTIONS joined one-to-one to a same-ident atc_t0 shot inside
    [-10, atc_rt + 10] ms -- ts / ts_rt / ts_multi, and the unjoinable rows (no_shot incl. a status
    mismatch, contested, no_send_ms) left NULL and counted (ATC_NET@post_unjoined)
  - gate_events: every BOOT_SKIP / DOUBLE-BUY GUARD (2xx = FX-1005-PO2XX, gateway, no response,
    incl. a reason that wraps the line) / won-cart late-add (foreign_entry = FOREIGN-KEEP) /
    foreign-bail line, glued lines, prior-logger time, a skipped account kept as printed
  - the atc_net (coverage + first-volley wire rank / reuse / edge IP) and gate_events queries
  v5 (2026-10-15):
  - INS-STORE-HARVEST-1015: every logger-form [HARVEST/x] bank line -> one `harvest` row (CAPTURED incl. the
    "(NO Shape tokens - not banked)" variant banked=0, pre-shot, and the REPLAY / EMPTY / STALE lines that feed
    shots.cred_source), counters and lengths only; the print twin is never read (a glued twin, a print-only line,
    20 pairs = 20 rows); the LAST bank= group wins over a body echo; an unreadable tail keeps its row (NULLs) and is
    counted in `unparsed`; every other table, `checks` and the unparsed remainders are identical with and without
    the harvest lines; PARSER_VERSION 5 re-parses and replaces the rows without duplicates
  - phase.sql: R-BANK met / not met / NO DATA, the >= 20 page-signed gate of C-1015-04, early-phase page-signed
    admissions, an overlapped window's first-shot lag, wire order vs atc_t0 order of a first volley
  v6 (2026-10-15):
  - INS-LAG-1015: every logger-form [PURCHASE_LOCK] started / complete line -> one `lock_events` row (kind + its own
    millisecond stamp, no ident); three same-ms starts, a line glued behind a print, two stamped lines on one physical
    line = every one a row, none twice; the print twin and a print-form marker are no rows (the marker counted
    @wrong_form), an unreadable [PURCHASE_LOCK] line is counted, never guessed; every other table and `checks` are
    identical with and without the lock lines; PARSER_VERSION 6 re-parses a v5 store and re-creates the table
  - dispatch_lag.sql: idle / busy_other / busy_same / no_shot / unknown classes, release + tick + start == the first
    shot by ANY race, the intervening-dispatch (redispatched) window, wire-order check, R-LAG build / skip / inconclusive
    / NO DATA verdicts (median, p90, the 20%-over-2 s line, the < 3 busy windows line); fix round 2: a redispatched
    (w9 / FAIR-FRESH) busy_other window is shown and counted (redispatched_not_counted) but is not in the R-LAG
    population on either basis (3 clean + 1 redispatched => n_busy 3), and disp_last is bounded by the window's first
    shot (per-window release / tick / start and the statement-4 sum on a multi-window run; a lock start after the first
    shot is not the dispatch)
  - reflip.sql: later shots within 2 s after a re-flip counted once, wire vs atc_t0 basis, the capped (flip-log cap)
    exclusion, unclassified gates shown not folded in, R-REFLIP met / not met (n, rate, vacuous 0 >= 0) / NO DATA;
    fix round 2: a later shot (is_first = 0, a racer already running) within 2 s after a new_window = 1 flip is NOT after
    a re-flip on either basis (the `new_window = 0` predicate is pinned for both the wire and the atc_t0 test)

  v7 (2026-10-16):
  - INS-LIMKEY-1016: every [LIMKEY_PROBE] result line -> one `limkey` row (event 'probe'; acct / tcin / status / err_key / hop /
    net_line / body_len / n / err), every event=<name> line its own event kind (skip, stop, error, cleanup, unknown names kept);
    the print and logger twins collapse to one row per identical text (print-only and logger-only lines keep a row, two real
    probes with the same text stay two); a glued print, an unreadable line counted and never guessed; the heartbeat
    "[WARMUP#N/acct] dummy POST status S" lines -> `warm_dummy` (the ADDED line is not read, a logger-form copy is counted
    @wrong_form); the probe's own warmup [ATC_RESP] (limkey_probe=1) is left out of `decoys` without disturbing the
    pairing of the others; every other table is identical with and without the new lines; the real limkey_result_line()
    output parses; PARSER_VERSION 7 re-parses a v6 store and re-creates both tables
  - limkey.sql (pre-registered rule P1): TESTBED EXISTS / DEMAND-SWITCHED / INCONCLUSIVE (n < 30, share < 50%, limited
    decoy by dummy status or by ERR_A2C key, empty control) / NO DATA; status 0 and -1 never in the rate; the control is the
    same accounts in the same local hours; the unclassified remainder, the event table and the parser accounting
No browser, no network, no bot. Run: python tests/test_events_parser.py
"""
from __future__ import annotations

import contextlib
import csv
import email.utils
import importlib.util
import io
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ev = _load('events_build', ROOT / 'tools' / 'events' / 'build.py')
evq = _load('events_q', ROOT / 'tools' / 'events' / 'q.py')

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail!r}"[:600])


# ---------------------------------------------------------------------------------
# Line builders (shapes copied from logs/runs/run_20260930_014647.log / _20260915_233355)
# ---------------------------------------------------------------------------------
OFF_MS = -5 * 3600 * 1000            # the logs' wall clock: UTC-5 (CDT) in September
T = 1790753483375                    # 2026-09-30 02:31:23.375 local (a real flip read_ms)
TC, TC2, TC3 = '1010892076', '1010892067', '95082118'
POOL = "[WORKER_POOL] sized from target_accounts.json: 3 account(s) -> ['primary', 'business', 'alt-1']"
A2C, FSK = 'ERR_A2C_TCIN_RATE_LIMITED', 'FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION'
DCO_BODY = ("'{\"message\":\"Rate Limited\",\"code\":\"DCO_RATE_LIMITED\",\"alerts\":[{\"message\":"
            "\"Request throttled due to high demand item\",\"co'")
W = {'primary': 'W1/primary', 'business': 'W2/business', 'alt-1': 'W3/alt-1'}


def stamp(ep_ms):
    t = ep_ms + OFF_MS
    return time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(t // 1000)) + ',%03d' % (t % 1000)


def L(ep_ms, msg, name='src.session.purchase_executor', level='INFO'):
    return f'{stamp(ep_ms)} {level} {name}: {msg}'


def race(tc, idents):
    return f"[RACE] {tc}: racing {len(idents)} accounts → [{', '.join(repr(W[i]) for i in idents)}]"


def done(tc, k, n, entries, units=0):
    bd = ', '.join(f"'{W[i]}': '{v}'" for i, v in entries)
    return f"[RACE] {tc}: {k}/{n} accounts done, units_bought={units}, breakdown={{{bd}}}"


def fire(tc):
    return f"[FAST_LANE] Firing ATC→pre_checkout→place-order chain (tcin={tc}, qty=2)"


def cred(ident, ep_ms, age=12):
    m = (f"[HARVEST/{ident}] REPLAY on main shot: banked set age={age}s tokens=6 a0=no src_tcin=21516452 "
         f"hdr_bytes=13757 (cookie=5152 shape=8078 a0=0) | bank=4/6 newest_age=39s harvested=50 replayed=4")
    return [m, L(ep_ms, m)]


def resp_msg(status, key, tab='main', label=True):
    m = f"[ATC_RESP] status={status} method=POST tgt-cart-error-key={key} x-request-id=- url=cart_items"
    return m + (f" envoy_ms=- tab={tab} selftest=off" if label else '')


def resp(ep_ms, status, key, tab='main', label=True):
    m = resp_msg(status, key, tab, label)
    return [f"[INTERCEPTOR:{tab}] {m}", L(ep_ms, m)]


def chain(ident, status, t0=None, rt=None):
    s = f"[FAST_LANE] chain done in 0.12s — atc={status} pre=0 po=0 skip=atc_{status} ident={ident}"
    return s + (f" atc_t0={t0} atc_rt={rt} cart_qty=-" if t0 is not None else '')


def pf429(ident, body="''"):
    return (f"[PURCHASE] ATC fetch: rate-limited (429) body={body} — bailing for Error Delay retry "
            f"(t=0.15s) ident={ident}")


def flip(tc, n, read_ms, nw):
    return L(read_ms + 18, f"[STOCK][FLIP] tcin={tc} #{n} read_ms={read_ms} rt_ms=804 last_oos_ms={read_ms - 21} "
                           f"since_oos_ms=21 new_window={nw} via=s15 (31.105.x.x) status=IN_STOCK atp=10.0",
             name='src.monitoring.stock_check_resilient')


def shot(ident, tc, t0, status=429, key=A2C, rt=120):
    """One labelled-era shot, in the order a thread writes it."""
    return ([fire(tc)] + cred(ident, t0 - 5) + resp(t0 + rt, status, key) +
            [chain(ident, status, t0, rt)])


def parse(lines, run_id='run_test'):
    return ev.parse_lines(run_id, lines)


def rows(res, table, **where):
    return [r for r in res[table] if all(r.get(k) == v for k, v in where.items())]


def check_of(res, name):
    return next((c['value'] for c in res['checks'] if c['name'] == name), None)


# ---------------------------------------------------------------------------------
def test_glued_lines():
    line = chain('primary', 429, T + 50, 140) + L(T + 191, resp_msg(429, A2C))
    segs = ev.split_segments(line)
    check('glued_split_into_print_then_logger', [s[0] for s in segs] == ['print', 'logger'], segs)
    check('glued_print_tail_intact', segs[0][1].endswith('cart_qty=-'), segs[0][1][-20:])
    check('glued_logger_message_starts_at_marker', segs[1][1].startswith('[ATC_RESP] status=429'), segs[1][1][:30])
    check('three_stamps_three_logger_segments',
          [s[0] for s in ev.split_segments('x' + L(T, 'a') + L(T + 1, 'b') + L(T + 2, 'c'))]
          == ['print', 'logger', 'logger', 'logger'])
    # the 09-15 L26069 shape: an old chain line (no atc_t0) glued to a logger line
    glued_old = ("[FAST_LANE] chain done in 0.86s — atc=429 pre=0 po=0 skip=atc_429 ident=primary"
                 + L(T + 900, resp_msg(429, A2C, label=False)))
    res = parse([POOL, race(TC, ['primary']), fire(TC), f"[INTERCEPTOR:main] {resp_msg(429, A2C, label=False)}",
                 glued_old])
    idents = [s['ident'] for s in res['shots']]
    check('glued_date_not_swallowed_into_ident', idents == ['primary'], idents)
    check('glued_logger_twin_not_a_second_response', check_of(res, 'main_resp') == '1', check_of(res, 'main_resp'))
    for raw, want in (('ident=business[PURCHASE] CDP fetch', 'business'),
                      ('ident=W3/alt-12026-09-30 02:31:23', 'alt-1'),
                      ('ident=primary2026-09-16', 'primary'),
                      ('ident=alt-1 atc_t0=1790753483426', 'alt-1'),
                      ('ident=W2/business', 'business')):
        m = ev.IDENT_RE.search(raw)
        check('ident_bounded ' + raw[:26], m is not None and m.group('ident') == want, m and m.group('ident'))
    # a print glued to another print: both events parsed, fields bounded
    two = ("[EXPOSURE] ident=W1/primary tcin=1010892076 kind=edge run_shots=1 run_s=0 win_age_s=0 "
           "chrome_age_s=2674 proxied=no resting=no" + chain('primary', 429, T + 50, 140))
    res = parse([POOL, race(TC, ['primary']), fire(TC)] + resp(T + 190, 429, A2C) + [two])
    check('print_print_glue_both_parsed', len(res['shots']) == 1 and res['shots'][0]['gate'] == 'wall1_limited'
          and rows(res, 'idents', ident='primary')[0]['exp_no'] == 1, res['shots'])


def test_duplicates_counted_once():
    # labelled era (09-22+): the logger copy carries tab= -> logger form, print copy ignored
    lines = [POOL, race(TC, ['primary'])] + shot('primary', TC, T + 50)
    lines += resp(T + 900, 424, 'ITEM_NOT_READY_FOR_LAUNCH,NOT_FOUND', tab='warmup')
    lines += resp(T + 1900, 401, '-', tab='warmup')
    res = parse(lines)
    check('labelled_form_is_logger', res['run']['atc_resp_form'] == 'logger', res['run'])
    check('labelled_one_shot_per_response', len(res['shots']) == 1, len(res['shots']))
    check('labelled_decoys_once_each', sorted(d['status'] for d in res['decoys']) == [401, 424], res['decoys'])
    check('decoy_logger_stamp_kept', res['decoys'][0]['ts'] == stamp(T + 900), res['decoys'][0]['ts'])
    check('cred_print_plus_logger_is_one',
          res['shots'][0]['cred_source'] == 'banked_replay' and check_of(res, 'cred_unclaimed') == '0',
          (res['shots'][0]['cred_source'], check_of(res, 'cred_unclaimed')))
    # pre-label era (09-15): the logger copy has no tab -> print form, logger copy ignored
    lines = [POOL, race(TC, ['primary']), fire(TC)] + resp(T, 429, A2C, label=False) + \
        [chain('primary', 429), pf429('primary')] + resp(T + 900, 424, 'ITEM_NOT_READY_FOR_LAUNCH,NOT_FOUND',
                                                          tab='warmup', label=False)
    res = parse(lines)
    check('unlabelled_form_is_print', res['run']['atc_resp_form'] == 'print', res['run'])
    check('unlabelled_counts', len(res['shots']) == 1 and len(res['decoys']) == 1, (res['shots'], res['decoys']))
    # [ATC_RESP_HDRS] print + logger: one header set on the one response
    hdr = ("[ATC_RESP_HDRS] tab=main status=401 n=3 | content-length=231 | vary=Origin,Origin | x-ssx-hop=1")
    lines = [POOL, race(TC, ['primary'])] + shot('primary', TC, T + 50, status=401, key='-') + \
        [f"[INTERCEPTOR:main] {hdr}", L(T + 171, hdr)]
    res = parse(lines)
    s0 = res['shots'][0]
    check('hdrs_once_and_joined', s0['has_ssx_hop'] == 1 and s0['content_length'] == 231
          and check_of(res, 'hdrs_main_unpaired') == '0', s0)


def test_gates():
    cases = [((201, '-'), 'cart'), ((200, None), 'cart'), ((401, '-'), 'wall2_denied'),
             ((401, 'ERR_UNAUTHORIZED'), 'wall2_denied'), ((424, 'X'), 'inventory'),
             ((429, A2C), 'wall1_limited'), ((429, FSK), 'admitted_fs'), ((429, 'DCO_RATE_LIMITED'), 'admitted_fs'),
             ((429, '-'), 'unknown'), ((429, None), 'unknown'), ((429, 'SOME_NEW_KEY'), 'unknown'),
             ((503, '-'), 'other'), ((431, '-'), 'other'), ((0, None), 'other'), ((None, None), 'unknown')]
    for (st, key), want in cases:
        check(f'gate {st} {key}', ev.gate_of(st, key) == want, ev.gate_of(st, key))
    # 10-23 FS-2: a 429 whose response line was seen and carried no key is its own class; without that evidence
    # (no line / ambiguous) it stays unknown; a seen key we do not recognise stays unknown too
    check('gate_keyless_seen_header', ev.gate_of(429, '-', 'header') == 'wall1_keyless' and ev.gate_of(429, '', 'header') == 'wall1_keyless',
          (ev.gate_of(429, '-', 'header'), ev.gate_of(429, '', 'header')))
    check('gate_keyless_needs_header', ev.gate_of(429, '-', 'ambiguous') == 'unknown' and ev.gate_of(429, None, None) == 'unknown'
          and ev.gate_of(429, 'SOME_NEW_KEY', 'header') == 'unknown' and ev.gate_of(429, 'ERR_A2C_TCIN_RATE_LIMITED', 'header') == 'wall1_limited')
    # a pre-08-27 429 (no [ATC_RESP] at all) is unknown, its body class kept
    res = parse([POOL, race(TC, ['primary']), fire(TC), chain('primary', 429), pf429('primary')])
    s0 = res['shots'][0]
    check('no_header_429_is_unknown_not_limited', s0['gate'] == 'unknown' and s0['body_hint'] == 'empty', s0)


def test_shot_index_interleaved():
    t = T + 1000
    lines = [POOL, race(TC, ['primary', 'business', 'alt-1']),
             fire(TC), fire(TC), fire(TC)]
    lines += cred('primary', t) + cred('business', t + 1) + cred('alt-1', t + 2)
    # responses land in one order, chain lines print in another (threads interleave)
    lines += resp(t + 120, 429, A2C) + resp(t + 125, 429, A2C) + resp(t + 131, 429, A2C)
    lines += [chain('alt-1', 429, t + 10, 121), chain('primary', 429, t, 120), chain('business', 429, t + 5, 120)]
    lines += shot('primary', TC, t + 60000) + shot('alt-1', TC, t + 61000)
    lines += [done(TC, 1, 3, [('business', 'rate_limited_429')]),
              done(TC, 2, 3, [('business', 'rate_limited_429'), ('primary', 'rate_limited_429')]),
              done(TC, 3, 3, [('business', 'rate_limited_429'), ('primary', 'rate_limited_429'),
                              ('alt-1', 'rate_limited_429')])]
    lines += [race(TC, ['primary', 'business', 'alt-1'])]
    lines += shot('business', TC, t + 300000) + shot('primary', TC, t + 300100)
    res = parse(lines)
    got = [(s['race_seq'], s['ident'], s['shot_idx'], s['is_first']) for s in res['shots']]
    want = [(1, 'alt-1', 1, 1), (1, 'primary', 1, 1), (1, 'business', 1, 1), (1, 'primary', 2, 0),
            (1, 'alt-1', 2, 0), (2, 'business', 1, 1), (2, 'primary', 1, 1)]
    check('shot_idx_per_race_per_ident', got == want, got)
    check('interleaved_all_joined_by_ts', {s['join_q'] for s in res['shots']} == {'ts'},
          [s['join_q'] for s in res['shots']])
    check('race_width_and_count', [r['width'] for r in res['races']] == [3, 3], res['races'])
    # D1: two races open at once; an account belongs to the race that listed it
    lines = [POOL, race(TC, ['primary', 'business']), race(TC2, ['alt-1'])]
    lines += shot('alt-1', TC2, T + 50) + shot('primary', TC, T + 60) + shot('business', TC, T + 70)
    lines += [done(TC, 1, 2, [('primary', 'rate_limited_429')]), race(TC3, ['primary'])]
    lines += shot('primary', TC3, T + 90000) + shot('business', TC, T + 91000)
    res = parse(lines)
    got = [(s['ident'], s['tcin'], s['race_seq'], s['shot_idx']) for s in res['shots']]
    want = [('alt-1', TC2, 2, 1), ('primary', TC, 1, 1), ('business', TC, 1, 1),
            ('primary', TC3, 3, 1), ('business', TC, 1, 2)]
    check('concurrent_races_by_account_membership', got == want, got)


def test_join_edge_cases():
    # (a) 09-25 L53930: the logger [ATC_RESP] glued AFTER its own chain print
    t = T + 5000
    lines = [POOL, race(TC, ['primary', 'business']), fire(TC)] + cred('primary', t)
    lines += [f"[INTERCEPTOR:main] {resp_msg(429, A2C)}", chain('primary', 429, t, 153) + L(t + 154, resp_msg(429, A2C))]
    lines += [fire(TC)] + cred('business', t + 3000)
    lines += [f"[INTERCEPTOR:main] {resp_msg(429, A2C)}", chain('business', 429, t + 3000, 179) + L(t + 3183, resp_msg(429, A2C))]
    res = parse(lines)
    got = [(s['ident'], s['join_q'], s['join_dt_ms'], s['src']) for s in res['shots']]
    check('response_after_own_chain_joined_by_ts', got == [('primary', 'ts', 1, 'chain'), ('business', 'ts', 4, 'chain')], got)
    # (b) print era, two same-status responses with different keys: the [PURCHASE] body decides
    base = [POOL, race(TC, ['primary', 'business']), fire(TC), fire(TC)]
    base += [f"[INTERCEPTOR:main] {resp_msg(429, FSK, label=False)}", f"[INTERCEPTOR:main] {resp_msg(429, A2C, label=False)}"]
    base += [chain('business', 429), chain('primary', 429)]
    res = parse(base + [pf429('business'), pf429('primary', DCO_BODY)])
    got = {s['ident']: (s['gate'], s['join_q']) for s in res['shots']}
    check('body_hint_resolves_mixed_keys', got == {'business': ('wall1_limited', 'seq_hint'),
                                                   'primary': ('admitted_fs', 'seq1')}, got)
    check('hint_vs_key_agree_check', check_of(res, 'body_hint_vs_key_429') == "{'agree': 2}",
          check_of(res, 'body_hint_vs_key_429'))
    # (c) the same without the [PURCHASE] lines: undecidable -> both unknown, none guessed
    res = parse(base)
    got = {s['ident']: (s['gate'], s['join_q'], s['err_key']) for s in res['shots']}
    check('ambiguous_keys_stay_unknown_for_both', got == {'business': ('unknown', 'ambiguous', None),
                                                          'primary': ('unknown', 'ambiguous', None)}, got)
    check('ambiguous_rows_not_duplicated', len(res['shots']) == 2, len(res['shots']))
    # (d) a main response no chain claims: its own row, ident NULL; the count reconciles
    lines = [POOL, race(TC, ['primary'])] + shot('primary', TC, T + 50) + resp(T + 9000, 429, A2C)
    res = parse(lines)
    srcs = sorted((s['src'], s['ident'] or '-') for s in res['shots'])
    check('unclaimed_response_is_resp_only_row', srcs == [('chain', 'primary'), ('resp_only', '-')], srcs)
    check('joined_plus_resp_only_equals_responses', len(res['shots']) == int(check_of(res, 'main_resp')),
          (len(res['shots']), check_of(res, 'main_resp')))
    # (e) 09-15 race 57: a shot whose evaluate timed out -- no response, no chain line
    lines = [POOL, race(TC, ['primary', 'alt-1'])] + shot('primary', TC, T + 50) + shot('alt-1', TC, T + 60)
    lines += [fire(TC)] + cred('alt-1', T + 70000)
    lines += ["[FAST_LANE] evaluate timed out after 12s — place-order state UNKNOWN, treating as no-response "
              "(non-retryable)", "[FAST_LANE] [DOUBLE-BUY GUARD] place-order got NO response (skip=evaluate_timeout)"]
    res = parse(lines)
    o = [s for s in res['shots'] if s['src'] == 'orphan']
    check('timed_out_shot_kept_as_unknown', len(o) == 1 and o[0]['gate'] == 'unknown' and o[0]['status'] is None, o)
    check('timed_out_shot_ident_from_unclaimed_cred', o and o[0]['ident'] == 'alt-1'
          and o[0]['ident_src'] == 'unclaimed_cred' and o[0]['shot_idx'] == 2, o)
    check('fire_lines_equal_shots', check_of(res, 'fire_lines') == str(len(res['shots'])),
          (check_of(res, 'fire_lines'), len(res['shots'])))


def test_flips_and_windows():
    lines = [POOL, flip(TC, 1, T, 1)]
    lines += ["[STOCK] IN STOCK: 1010892076", "[02:31:23] [API_CYCLE] IN STOCK: ['1010892076']"]
    lines += [race(TC, ['primary'])] + shot('primary', TC, T + 47)
    lines += [flip(TC2, 1, T + 30000, 1), race(TC2, ['business'])] + shot('business', TC2, T + 30040)
    lines += [flip(TC, 2, T + 60000, 0), race(TC, ['alt-1'])] + shot('alt-1', TC, T + 70000)
    lines += [flip(TC, 3, T + 200000, 1), race(TC, ['primary'])] + shot('primary', TC, T + 200050)
    res = parse(lines)
    wins = [(w['window_id'], w['tcin'], w['first_read_ms'], w['flips'], w['races'], w['shots']) for w in res['windows']]
    check('windows_ordered_by_first_read', wins == [(1, TC, T, 2, 2, 2), (2, TC2, T + 30000, 1, 1, 1),
                                                    (3, TC, T + 200000, 1, 1, 1)], wins)
    fo = [(r['race_seq'], r['window_id'], r['flip_opened'], r['lag_ms']) for r in res['races']]
    check('first_race_of_window_is_flip_opened', fo == [(1, 1, 1, 47), (2, 2, 1, 40), (3, 1, 0, 70000),
                                                        (4, 3, 1, 50)], fo)
    ages = [s['window_age_ms'] for s in res['shots']]
    check('window_age_is_t0_minus_first_read', ages == [47, 40, 70000, 50], ages)
    check('flip_new_window_zero_stays_in_window', [f['window_id'] for f in res['flips']] == [1, 2, 1, 3],
          [f['window_id'] for f in res['flips']])
    check('instock_read_counted_in_window', res['windows'][0]['reads'] == 1, res['windows'][0])
    check('utc_offset_measured_from_flip', res['run']['utc_offset_min'] == -300 and
          res['run']['tz_src'].startswith('measured'), res['run'])
    res = parse([POOL, race(TC, ['primary'])] + shot('primary', TC, T + 47))
    check('no_flip_log_means_null_not_zero', res['races'][0]['flip_opened'] is None
          and res['shots'][0]['flip_opened_race'] is None and res['run']['flip_log'] == 0, res['races'])


def test_unparsed_remainder():
    lines = [POOL,
             '    print(f"[RACE] {tcin}: {recorded}/{total} accounts done, units_bought={units}, "',
             "[FS_TICKET] ident=alt-1 tcin=1010892067 cart_id=694e52f1-8cc n=1 cls=sched",
             L(T, "[EXPOSURE] ident=W1/primary tcin=1010892076 kind=edge run_shots=1 run_s=0 win_age_s=0 "
                  "chrome_age_s=2674 proxied=no resting=no", name='src.x'),
             "[STOCK][FLIP] tcin=1010892076 #1 read_ms=1790753483375 printed instead of logged",
             L(T + 30000, "[STOCK STATS] t=30.0s sweeps=88 (2.93/s) 200=87 403=0 other=0 beh=0 outstanding=1 "
                          "sessions=18r/0c/0rc pool A=30 P=0 B=0", name='src.monitoring.stock_check_resilient')]
    res = parse(lines)
    u = {r['marker']: (r['n'], r['seen'], r['first_line']) for r in res['unparsed']}
    check('unparsed_race_code_echo', u.get('RACE') == (1, 1, 2), u.get('RACE'))
    check('unparsed_truncated_ticket', u.get('FS_TICKET') == (1, 1, 3), u.get('FS_TICKET'))
    check('print_marker_in_logger_line_counted', u.get('EXPOSURE@wrong_form') == (1, 1, 4), u)
    check('logger_marker_in_print_line_counted', u.get('FLIP@wrong_form') == (1, 1, 5), u)
    check('parsed_marker_has_zero_remainder', u.get('STATS') == (0, 1, None), u.get('STATS'))
    check('bad_lines_create_no_rows', not res['tickets'] and not res['races'] and not res['flips'],
          (res['tickets'], res['races']))
    st = res['monitor_stats'][0]
    check('stats_without_429_field', st['sweeps'] == 88 and st['s429'] is None and st['s200'] == 87, st)


def test_checkout_orders_proxied():
    lines = [POOL,
             "[FORWARDER] W2/business → 127.0.0.1:23002 (exit via account proxy)",
             "[FORWARDER] W3/alt-1 → 127.0.0.1:23003 (exit via account proxy)",
             race(TC2, ['primary', 'business', 'alt-1'])]
    lines += shot('alt-1', TC2, T + 50, status=201, key='-', rt=6378)
    lines += ["[FS_TICKET] ident=alt-1 tcin=1010892067 cart_id=694e52f1-8cc n=1 cls=sched gap_s=1.0 "
              "ms_since_201=3205 live=True win_age=10s layer=po mode=po_only status=429 "
              "key=FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION envoy_ms=7 js_ms=241",
              "[FS_TICKET] ident=alt-1 tcin=1010892067 cart_id=694e52f1-8cc n=2 cls=sched gap_s=1.0 "
              "ms_since_201=5919 live=True win_age=12s layer=po mode=po_only status=429 key=RESERVATION_FAILURE "
              "envoy_ms=216 js_ms=468",
              "[WON_CART_DIRECT] end reason=call_cap verdict=done tickets_call=39 tickets_cart=39 live=True oos=0 "
              "sched_used=6 verified=True cvv_put=ok fs_seen=0s_ago dl_left=158s held=yes ident=alt-1",
              "[FAST_LANE] *** ORDER PLACED *** HTTP 200 at t=3.13s — order_id=eea40c50-663e-11f1-ab69-e11734010606",
              "[FAST_LANE] *** ORDER PLACED *** HTTP 200 at t=3.13s — order_id=eea40c50-663e-11f1-ab69-e11734010606",
              "[REAL_PURCHASE_THREAD] [OK] Purchase execution completed (attempt 1): {'success': True, "
              "'tcin': '1010892067', 'reason': 'order_confirmed', 'order_id': 'eea40c50-663e-11f1-ab69-e11734010606'}",
              "[REAL_PURCHASE_THREAD] Marked thread as completing: 1010892067#W3",
              done(TC2, 1, 3, [('alt-1', 'purchased')], units=2),
              L(T + 99000, "[STOCK STATS] t=30.0s sweeps=88 (2.93/s) 200=87 403=0 429=1 other=0 beh=0 "
                           "outstanding=0 sessions=18r/0c/0rc", name='src.monitoring.stock_check_resilient')]
    res = parse(lines)
    tk = [(t['n'], t['status'], t['key'], t['live'], t['win_age_s'], t['ms_since_201']) for t in res['tickets']]
    check('tickets_parsed', tk == [(1, 429, FSK, 1, 10, 3205), (2, 429, 'RESERVATION_FAILURE', 1, 12, 5919)], tk)
    le = res['loop_ends'][0]
    check('loop_end_tcin_from_open_race', (le['ident'], le['tcin'], le['reason'], le['tickets_cart'], le['held'])
          == ('alt-1', TC2, 'call_cap', 39, 1), le)
    o = res['orders']
    check('order_counted_once_with_buyer', len(o) == 1 and o[0]['ident'] == 'alt-1' and o[0]['tcin'] == TC2
          and o[0]['ident_src'] == 'thread_mark' and check_of(res, 'order_duplicate_lines') == '1', o)
    px = {i['ident']: (i['proxied'], i['proxied_src']) for i in res['idents']}
    check('proxied_from_boot_forwarder', px == {'primary': (0, 'boot_forwarder'), 'business': (1, 'boot_forwarder'),
                                                'alt-1': (1, 'boot_forwarder')}, px)
    check('shot_carries_proxied', res['shots'][0]['proxied'] == 1 and res['shots'][0]['gate'] == 'cart',
          res['shots'][0])
    check('stats_with_429_field', res['monitor_stats'][0]['s429'] == 1, res['monitor_stats'])
    # [EXPOSURE] proxied= outranks the boot lines, and a disagreement is recorded
    lines2 = lines[:4] + ["[EXPOSURE] ident=W3/alt-1 tcin=1010892067 kind=edge run_shots=1 run_s=0 win_age_s=0 "
                          "chrome_age_s=2674 proxied=no resting=no"]
    res = parse(lines2)
    px = {i['ident']: (i['proxied'], i['proxied_src']) for i in res['idents']}
    check('exposure_outranks_boot', px['alt-1'] == (0, 'exposure')
          and 'alt-1' in (check_of(res, 'proxied_boot_vs_exposure_disagree') or ''), px)


def test_decoy_tokens():
    post = "[INTERCEPTOR:warmup]  POST https://carts.target.com/web_checkouts/v1/cart_items?field_groups=CART%2CCART_IT"
    lines = [POOL, post,
             "[INTERCEPTOR:warmup] Captured 16 headers (Shape tokens: 6, prev cache had 0): ['Accept', 'Content-Type']"]
    lines += resp(T, 424, 'ITEM_NOT_READY_FOR_LAUNCH,NOT_FOUND', tab='warmup')
    lines += [post, "[INTERCEPTOR:warmup] Preserved cache (6 Shape tokens, age=8.5s) — new capture had only 0 Shape tokens"]
    lines += resp(T + 5000, 401, '-', tab='warmup')
    res = parse(lines)
    got = [(d['status'], d['key'], d['shape_tokens'], d['pair_q']) for d in res['decoys']]
    check('decoy_tokens_paired', got == [(424, 'ITEM_NOT_READY_FOR_LAUNCH,NOT_FOUND', 6, 'adjacent'),
                                         (401, '-', 0, 'adjacent')], got)


def test_db_and_query_cli():
    lines = [POOL, flip(TC, 1, T, 1), race(TC, ['primary', 'alt-1'])]
    lines += shot('primary', TC, T + 47) + shot('alt-1', TC, T + 50, status=429, key=FSK)
    lines += shot('primary', TC, T + 60000) + shot('alt-1', TC, T + 61000, status=401, key='-')
    with tempfile.TemporaryDirectory() as tmp:
        logs = Path(tmp) / 'runs'
        logs.mkdir()
        log = logs / 'run_20990101_000000.log'
        log.write_bytes(('\r\n'.join(lines) + '\r\n').encode('utf-8'))
        db = Path(tmp) / 'ev.sqlite'

        def build(*extra):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ev.main(['--db', str(db), '--logs', str(logs), '--quiet'] + list(extra))
            return buf.getvalue()

        out1 = build()
        con = sqlite3.connect(str(db))
        n1 = con.execute('SELECT COUNT(*) FROM shots').fetchone()[0]
        check('db_first_build', '1 file(s) parsed' in out1 and n1 == 4, (out1, n1))
        out2 = build()
        check('db_unchanged_file_skipped', '0 file(s) parsed, 1 unchanged' in out2, out2)
        with open(log, 'ab') as fh:
            fh.write(b'[SESSION] one more line\r\n')
        out3 = build()
        n3 = con.execute('SELECT COUNT(*) FROM shots').fetchone()[0]
        nr = con.execute('SELECT COUNT(*) FROM runs').fetchone()[0]
        check('db_changed_file_reparsed_without_duplicates', '1 file(s) parsed' in out3 and n3 == 4 and nr == 1,
              (out3, n3, nr))
        con.execute('UPDATE ingested SET parser_version = parser_version - 1')
        con.commit()
        check('db_parser_version_change_reparses', '1 file(s) parsed' in build())
        lines_col = con.execute("SELECT lines FROM runs").fetchone()[0]
        check('db_line_count_matches_file', lines_col == len(lines) + 1, lines_col)
        con.close()

        def q(*args):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = evq.main(['--db', str(db)] + list(args))
            return rc, buf.getvalue()

        rc, out = q('walls', '--csv')
        body = [r.split(',') for r in out.strip().splitlines()[1:]]
        got = sorted((r[1], r[3], r[4], r[6]) for r in body)
        check('q_walls_named_query', rc == 0 and got == [('alt-1', 'first', '1', '1'), ('alt-1', 'later', '1', '1'),
                                                           ('primary', 'first', '1', '0'), ('primary', 'later', '1', '0')], out)
        rc, out = q('regime', '--csv', '--run', '2099')
        hdr, row = out.strip().splitlines()[:2]
        rec = dict(zip(hdr.split(','), row.split(',')))
        check('q_regime_row', rc == 0 and rec['f1'] == '1/2' and rec['eo'] == '0/1' and rec['eo_401'] == '1', rec)
        rc, out = q('SELECT COUNT(*) AS n FROM shots', '--run', 'no_such_run', '--csv')
        check('q_run_filter_applies_to_raw_sql', out.strip().splitlines()[-1] == '0', out)
        rc, out = q('arms', '-p', 'treated=alt-1', '--csv')
        check('q_arms_param_bound', 'treated:alt-1' in out and 'rest' in out, out)
        rc, out = q('SELECT gate FROM shots WHERE ident = :who', '-p', 'who=alt-1', '--json')
        check('q_json_and_param', rc == 0 and '"admitted_fs"' in out and '"wall2_denied"' in out, out)
        # 2026-09-30: per-TCIN regime check (C-0930-08 was a pooled-rate artifact). Two first
        # shots (ERR_A2C + FAST_SELLING) and two later shots (ERR_A2C + keyless 401) on one TCIN:
        # first 1/2 past the limiter, admitted 1/2, later 1/2 (a 401 is past), share 100%; the
        # leave-the-dominant-TCIN-out figure is empty (0/0) because only one TCIN exists.
        rc, out = q('regime_tcin', '--csv')
        rows = [r.split(',') for r in out.strip().splitlines()]
        per = [r for r in rows if len(r) > 1 and r[1] == TC]
        check('q_regime_tcin_per_tcin', rc == 0 and len(per) == 1
              and per[0][3:8] == ['1/2', '50.0', '1/2', '1/2', '100.0'], out)
        pooled = [r for r in rows if len(r) > 4 and r[3] == TC and r[1] == '1/2']
        check('q_regime_tcin_leave_one_out', rc == 0 and len(pooled) == 1 and pooled[0][5] == '0/0', out)
        rc, out = q('monitor_hours', '--csv')
        check('q_monitor_hours_runs_without_stats', rc == 0, out)


# ---------------------------------------------------------------------------------
# v2 (2026-10-01) builders -- shapes copied from run_20260804_000646 / run_20260930_233818
# ---------------------------------------------------------------------------------
PO_POST = ("[INTERCEPTOR:main] [CHECKOUT_POST] POST "
           "https://carts.target.com/web_checkouts/v1/checkout?cart_type=REGULAR&field_group")
ORDER_ID = '8cba94c1-8cc5-11f1-adbd-9de2787bfd93'
MAXQ_BODY = '\'{"message":"Items cannot be added to cart as max purchase limit exceeded","code":"MAX_PURCHASE_LIMIT_EXCEEDED"}\''


def start(tc):
    return f"[PURCHASE] Starting purchase for {tc}"


def pdone(tc):
    return ("[REAL_PURCHASE_THREAD] [OK] Purchase execution completed (attempt 1): {'success': False, "
            f"'tcin': '{tc}', 'reason': 'atc_failed_api_mode', 'execution_time': 7.33}}")


def retry201(kind):
    if kind == 'fast-retry':
        return "[PURCHASE] ATC fast-retry succeeded (201) after Shape refresh (t=3.92s)"
    return "[PURCHASE] ATC retry-2 succeeded (201) after second Shape refresh (t=7.27s)"


def pf401_old():
    return ("[PURCHASE] ATC fetch status: 401 body='{\\n  \"errorCode\": \"T83072242\",\\n  \"errorKey\": "
            "\"_ERR_AUTH_DENIED\"\\n}' (t=0.60s)")


def co_resp(st):
    return f"[INTERCEPTOR:main] [CHECKOUT_RESPONSE] HTTP {st} — {'SUCCESS' if st in (200, 201) else 'REJECTED'}"


def co_detail(key, date_ms):
    d = email.utils.formatdate(date_ms / 1000.0, usegmt=True)
    return [f"[INTERCEPTOR:main] [CHECKOUT_RESPONSE] 424 flagged — short-circuiting wait loop (reason={key})",
            f"[INTERCEPTOR:main] [CHECKOUT_RESPONSE] headers: {{'content-type': 'application/json', "
            f"'date': '{d}', 'tgt-cart-error-key': '{key}', 'x-envoy-upstream-service-time': '9'}}"]


def chain_po(ident, t0, rt, po, pre=201):
    return (f"[FAST_LANE] chain done in 2.94s — atc=201 pre={pre} po={po} skip=none ident={ident} "
            f"atc_t0={t0} atc_rt={rt} cart_qty=2")


def ticket(ident, tc, n, st, key, ms201, mode='po_only'):
    return (f"[FS_TICKET] ident={ident} tcin={tc} cart_id=694e52f1-8cc n={n} cls=sched gap_s=1.0 "
            f"ms_since_201={ms201} live=True win_age=10s layer=po mode={mode} status={st} key={key} "
            f"envoy_ms=7 js_ms=241")


def api_http(st):
    return f"[API_PLACE_ORDER] HTTP {st} in 0.20s (0 body chars)"


def sec(ep_ms):
    """An HTTP Date header carries whole seconds."""
    return ep_ms - ep_ms % 1000


def tick(ep_ms, what='[WATCHDOG] Checking cookies...'):
    return L(ep_ms, what, name='src.session.session_manager')


def watch(tc, ep_ms, v):
    return L(ep_ms, f"[STOCK WATCH] {tc}: in_stock={v} avail={'IN_STOCK' if v else 'OUT_OF_STOCK'} "
                    f"last_clean_read=0s ago", name='src.monitoring.stock_check_resilient')


def instock(tc, ep_ms):
    """A whole-second [STOCK] IN STOCK read (its [API_CYCLE] line carries the local second)."""
    return [f"[STOCK] IN STOCK: {tc}", f"[{stamp(ep_ms)[11:19]}] [API_CYCLE] IN STOCK: ['{tc}']"]


def test_v2_retry_shots():
    t = T + 1000
    lines = [POOL, tick(t), race(TC, ['primary', 'business']), start(TC), start(TC), pf401_old(),
             tick(t + 3000), retry201('fast-retry'), tick(t + 7000), retry201('retry-2')]
    res = parse(lines)
    rs = [s for s in res['shots'] if s['src'] == 'legacy_retry']
    got = [(s['variant'], s['status'], s['gate'], s['tcin'], s['tcin_src'], s['race_seq'], s['ident']) for s in rs]
    check('retry_201_lines_are_cart_shots', got == [('fast-retry', 201, 'cart', TC, 'purchase_ctx', 1, None),
                                                     ('retry-2', 201, 'cart', TC, 'purchase_ctx', 1, None)], got)
    check('retry_shots_keep_their_own_time', [s['ts_ms'] for s in rs] == [t + 3000, t + 7000], [s['ts_ms'] for s in rs])
    u = {r['marker']: (r['n'], r['seen']) for r in res['unparsed']}
    check('retry_all_tcins_resolved_counted', u.get('PRETRY@tcin_null') == (0, 2) and u.get('PRETRY') == (0, 2), u)
    # two TCINs running at once: the retry line cannot say which -> NULL, counted, no race
    lines = [POOL, tick(t), race(TC, ['primary']), race(TC2, ['business']), start(TC), start(TC2),
             retry201('retry-2'), pdone(TC2), retry201('retry-2')]
    res = parse(lines)
    rs = [(s['tcin'], s['race_seq']) for s in res['shots'] if s['src'] == 'legacy_retry']
    check('retry_ambiguous_context_is_null_not_guessed', rs == [(None, None), (TC, None)], rs)
    u = {r['marker']: (r['n'], r['seen']) for r in res['unparsed']}
    check('retry_ambiguous_counted_in_unparsed', u.get('PRETRY@tcin_null') == (1, 2), u)


def test_v2_atc400_cart_limit():
    for (st, key), want in (((400, 'MAX_PURCHASE_LIMIT_EXCEEDED'), 'cart_limit'), ((400, 'SOME_OTHER_CODE'), 'other'),
                            ((400, '-'), 'other'), ((400, None), 'other')):
        check(f'gate {st} {key}', ev.gate_of(st, key) == want, ev.gate_of(st, key))
    t = T + 2000
    hdr = "[ATC_RESP_HDRS] tab=main status=400 n=3 | content-length=110 | date=x | x-ssx-hop=1"
    lines = [POOL, race(TC2, ['business'])] + shot('business', TC2, t, status=400, key='-', rt=355) + \
        [L(t + 356, hdr), f"[PURCHASE] ATC fetch status: 400 body={MAXQ_BODY} (t=0.45s) ident=business"]
    # the pre-09 legacy print: no ident, no [ATC_RESP]
    lines += [race(TC, ['primary']), tick(t + 9000),
              f"[PURCHASE] ATC fetch status: 400 body={MAXQ_BODY} (t=3.19s)",
              "[PURCHASE] ATC fetch status: 400 body='{\"code\":\"SOME_OTHER_CODE\"}' (t=1.00s)",
              "[PURCHASE] ATC fetch status: 400 body='<html>gateway</html>' (t=1.10s)"]
    res = parse(lines)
    got = [(s['src'], s['ident'], s['gate'], s['err_key'], s['err_key_src']) for s in res['shots']]
    check('atc400_body_code_is_the_key', got == [
        ('chain', 'business', 'cart_limit', 'MAX_PURCHASE_LIMIT_EXCEEDED', 'body'),
        ('legacy', None, 'cart_limit', 'MAX_PURCHASE_LIMIT_EXCEEDED', 'body'),
        ('legacy', None, 'other', 'SOME_OTHER_CODE', 'body'),
        ('legacy', None, 'other', None, None)], got)
    check('cart_limit_is_past_the_limiter', res['shots'][0]['has_ssx_hop'] == 1, res['shots'][0])
    u = {r['marker']: (r['n'], r['seen']) for r in res['unparsed']}
    check('atc400_without_code_counted', u.get('ATC400@no_body_code') == (1, 4), u)
    # a 429's body code never becomes a key (pre-08-27 429s stay 'unknown', body_hint keeps the class)
    res = parse([POOL, race(TC, ['primary']), fire(TC), chain('primary', 429), pf429('primary', DCO_BODY)])
    s0 = res['shots'][0]
    check('body_code_only_for_400', s0['gate'] == 'unknown' and s0['err_key'] is None and s0['body_hint'] == 'dco', s0)


def _po_run_lines():
    """A flip-era night: an in-chain order, a legacy POST, won-cart tickets, overlapping in-chain
    POSTs, an unclaimed POST, an HTTP-0 legacy line and a POST that never got a response."""
    lines = [POOL, flip(TC, 1, T, 1), race(TC, ['primary', 'business', 'alt-1'])]
    # (1) primary carts in-chain and its own POST returns 200
    lines += [fire(TC)] + cred('primary', T + 45) + resp(T + 350, 201, '-')
    lines += [tick(T + 1500), PO_POST, tick(T + 2600), co_resp(200), chain_po('primary', T + 50, 300, 200),
              f"[FAST_LANE] *** ORDER PLACED *** HTTP 200 at t=3.08s — order_id={ORDER_ID}"]
    # (2) business carts in-chain, its POST gets FAST_SELLING, then the won-cart loop fires a ticket
    lines += [fire(TC)] + cred('business', T + 4000) + resp(T + 4300, 201, '-')
    lines += [tick(T + 5000), PO_POST, co_resp(429)] + co_detail(FSK, T + 6000) + \
        [tick(T + 6500), chain_po('business', T + 4000, 300, 429)]
    lines += [tick(T + 9000), PO_POST, co_resp(429)] + co_detail('RESERVATION_FAILURE', T + 9000) + \
        [ticket('business', TC, 1, 429, 'RESERVATION_FAILURE', 4700)]
    # (3) the legacy path: [API_PLACE_ORDER] Firing / HTTP + its mode=legacy ticket
    lines += ["[API_PLACE_ORDER] Firing checkout POST", tick(T + 40000), PO_POST, co_resp(429)] + \
        co_detail(FSK, T + 41000) + [tick(T + 45000), api_http(429),
                                     ticket('business', TC, 1, 429, FSK, '-', mode='legacy')]
    # (4) a POST nobody claims (the pre-09 DOM click) and an HTTP-0 legacy line (no POST printed)
    lines += ["[PAYMENT] Clicking Place Order ([data-test=\"placeOrderButton\"]) attempt 1/3", tick(T + 130000),
              PO_POST, co_resp(400)] + co_detail('', T + 130000) + [api_http(0)]
    # (5) a POST whose response never printed
    lines += [tick(T + 200000), PO_POST]
    return lines


def test_v2_place_orders():
    res = parse(_po_run_lines())
    po = res['place_orders']
    check('po_one_row_per_main_post', len(po) == 6, len(po))
    got = [(p['path'], p['status'], p['ident'], p['tcin'], p['err_key']) for p in po]
    check('po_paths_and_keys', got == [
        ('in_chain', 200, 'primary', TC, None), ('in_chain', 429, 'business', TC, FSK),
        ('ticket', 429, 'business', TC, 'RESERVATION_FAILURE'), ('legacy', 429, 'business', TC, FSK),
        ('unknown', 400, None, TC, '-'), ('unknown', None, None, TC, None)], got)
    p0 = po[0]
    check('po_inchain_200_window_and_201_delay',
          (p0['window_id'], p0['window_src'], p0['window_age_ms'], p0['ms_since_201'], p0['ms201_src'],
           p0['ts_src'], p0['ts_lo_ms'], p0['ts_hi_ms'], p0['order_id'], p0['cart_src'], p0['po_idx'])
          == (1, 'flip', 1500, 1150, 'chain_t0', 'logger_prior', T + 1500, T + 2600, ORDER_ID, 'chain', 1), p0)
    p1 = po[1]
    check('po_date_header_is_the_clock', (p1['ts_ms'], p1['ts_src'], p1['err_key_src'], p1['window_age_ms'],
                                          p1['ts_lo_ms'], p1['ts_hi_ms'])
          == (sec(T + 6000), 'resp_date', 'reason', sec(T + 6000) - T, T + 5000, T + 6500), p1)
    p2 = po[2]
    check('po_ticket_ident_and_ms201', (p2['ident_src'], p2['ms_since_201'], p2['ms201_src'], p2['cart_src'],
                                        p2['cart_line'], p2['po_idx']) ==
          ('ticket', 4700, 'fs_ticket', 'ident', p1['cart_line'], 2), p2)
    p3 = po[3]
    check('po_legacy_enriched_by_its_legacy_ticket', (p3['ticket_mode'], p3['ident_src'], p3['ts_ms']) ==
          ('legacy', 'ticket', sec(T + 41000)), p3)
    check('po_date_before_the_logger_bound_is_clamped', (po[4]['ts_ms'], po[4]['ts_src']) ==
          (T + 130000, 'resp_date'), po[4])
    check('po_unclaimed_tcin_from_open_race', (po[4]['tcin_src'], po[4]['err_key_src'], po[4]['window_age_ms'])
          == ('open_race', 'absent', 130000), po[4])
    check('po_unpaired_post_kept', po[5]['pair_q'] == 'unpaired' and po[5]['resp_line'] is None, po[5])
    u = {r['marker']: (r['n'], r['seen']) for r in res['unparsed']}
    check('po_remainders_counted', (u.get('CHECKOUT_POST@unpaired'), u.get('API_PO@http0'), u.get('PO@path_unknown'),
                                    u.get('PO@ident_null'), u.get('PO_CLAIM@unmatched'), u.get('PO@window_null'))
          == ((1, 6), (1, 2), (2, 6), (2, 6), (0, 5), (0, 6)), u)
    # two in-chain POSTs in flight at once: FIFO pairs them, each claim takes its own response
    lines = [POOL, flip(TC, 1, T, 1), race(TC, ['primary', 'business'])]
    lines += [fire(TC), fire(TC)] + resp(T + 300, 201, '-') + resp(T + 320, 201, '-')
    lines += [tick(T + 1500), PO_POST, PO_POST, co_resp(429)] + co_detail('RESERVATION_FAILURE', T + 2000) + \
        [chain_po('business', T + 20, 300, 429), co_resp(200), chain_po('primary', T + 10, 310, 200)]
    res = parse(lines)
    got = [(p['pair_q'], p['status'], p['ident'], p['claim_q']) for p in res['place_orders']]
    check('po_overlap_fifo_pairs', got == [('fifo_overlap', 429, 'business', 'single'),
                                           ('single', 200, 'primary', 'single')], got)


T0 = T - 375                                             # a whole second (the [API_CYCLE] clock)


def _read_ep_lines():
    lines = [POOL, watch(TC, T0 - 60000, False)] + instock(TC, T0)
    lines += [race(TC, ['primary'])] + shot('primary', TC, T0 + 400, status=429, key=A2C)
    lines += [tick(T0 + 3000), PO_POST, co_resp(429)] + co_detail(FSK, T0 + 3000) + [api_http(429)]
    lines += [watch(TC, T0 + 30000, True), tick(T0 + 50000), PO_POST, co_resp(429)] + \
        co_detail(FSK, T0 + 50000) + [api_http(429)]
    lines += [watch(TC, T0 + 70000, False)] + instock(TC, T0 + 100000)
    lines += [tick(T0 + 102000), PO_POST, co_resp(429)] + co_detail(FSK, T0 + 102000) + [api_http(429)]
    return lines


def test_v2_read_episodes():
    """No [STOCK][FLIP] lines: the window is the first in-stock read after the TCIN's last
    out-of-stock read ([STOCK WATCH] False), in log order; a True watch line keeps it open."""
    res = parse(_read_ep_lines())
    check('read_eps_utc_offset_measured', res['run']['tz_src'].startswith('measured'), res['run'])
    wins = [(w['window_id'], w['src'], w['first_read_ms'], w['reads'], w['end_ms']) for w in res['windows']]
    check('read_episodes_are_windows', wins == [(1, 'reads', T0, 2, T0 + 100000),
                                                (2, 'reads', T0 + 100000, 1, None)], wins)
    got = [(p['window_id'], p['window_src'], p['window_age_ms']) for p in res['place_orders']]
    check('po_window_from_read_episodes', got == [(1, 'reads', 3000), (1, 'reads', 50000), (2, 'reads', 2000)], got)
    s0 = res['shots'][0]
    check('shot_ep_window_kept_apart_from_flip_window', (s0['ep_window_id'], s0['ep_src'], s0['ep_age_ms'],
                                                         s0['window_id'], s0['window_age_ms'])
          == (1, 'reads', 400, None, None), s0)
    check('no_flip_runs_still_null_flip_fields', res['races'][0]['flip_opened'] is None and res['run']['flip_log'] == 0,
          res['races'])


def _ssx_lines(kinds, tc=TC, t=T):
    """One home-line account firing `kinds` in order: 'a' FAST_SELLING admit, 'c' a 201,
    'k' a keyless 401, 'w' an edge-429 (not past the limiter)."""
    lines = [POOL, race(tc, ['primary'])]
    for i, k in enumerate(kinds):
        st, key = {'a': (429, FSK), 'c': (201, '-'), 'k': (401, '-'), 'w': (429, A2C)}[k]
        lines += shot('primary', tc, t + i * 5000, status=st, key=key)
    return lines


def test_v2_queries():
    with tempfile.TemporaryDirectory() as tmp:
        logs = Path(tmp) / 'runs'
        logs.mkdir()
        runs = {
            'run_20990101_000000': _ssx_lines('awac' + 'k' * 20),            # switch: 2 admits, 20 keyless 401s
            'run_20990102_000000': _ssx_lines('aka' + 'k' * 5 + 'a' + 'k' * 13),   # re-admit: NOT REPLICATED
            'run_20990103_000000': _ssx_lines('ak' * 3),                      # n < 20: INCONCLUSIVE
            'run_20990104_000000': _po_run_lines(),
            'run_20990105_000000': _read_ep_lines(),
        }
        runs['run_20990101_000000'] += [race(TC2, ['primary'])] + shot('primary', TC2, T + 900000, status=400, key='-') + \
            [f"[PURCHASE] ATC fetch status: 400 body={MAXQ_BODY} (t=0.45s) ident=primary"]
        for name, lines in runs.items():
            (logs / (name + '.log')).write_bytes(('\r\n'.join(lines) + '\r\n').encode('utf-8'))
        db = Path(tmp) / 'ev.sqlite'
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ev.main(['--db', str(db), '--logs', str(logs), '--quiet'])
        check('v2_db_built', '5 file(s) parsed' in buf.getvalue(), buf.getvalue())

        def q(*args):
            b = io.StringIO()
            with contextlib.redirect_stdout(b):
                rc = evq.main(['--db', str(db), '--csv'] + list(args))
            return rc, b.getvalue()

        def tables(out):
            res_ = []
            for blk in out.strip().split('\n\n'):
                rows_ = list(csv.reader(io.StringIO(blk.strip())))
                res_.append([dict(zip(rows_[0], r)) for r in rows_[1:]])
            return res_

        rc, out = q('ssx_sequence')
        per, verdict = tables(out)
        r1 = next(r for r in per if r['run'] == 'run_20990101_000000' and r['tcin'] == TC)
        check('ssx_switch_row', rc == 0 and (r1['n'], r1['admits_before'], r1['k401_from'], r1['admits_after'],
                                             r1['run_after_last_admit'], r1['reading'], r1['rep'], r1['not_rep']) ==
              ('23', '3', '20', '0', '20', 'switch', '1', '0'), r1)
        r2 = next(r for r in per if r['run'] == 'run_20990102_000000')
        check('ssx_readmit_row', (r2['n'], r2['admits_before'], r2['admits_after'], r2['run_after_last_admit'],
                                  r2['reading']) == ('22', '1', '2', '13', 'admit after a post-admit 401'), r2)
        v = {r['run']: r['verdict'] for r in verdict}
        check('ssx_preregistered_verdicts', v == {'run_20990101_000000': 'REPLICATED',
                                                  'run_20990102_000000': 'NOT REPLICATED',
                                                  'run_20990103_000000': 'INCONCLUSIVE',
                                                  'run_20990104_000000': 'INCONCLUSIVE',
                                                  'run_20990105_000000': 'INCONCLUSIVE'}, v)
        rc, out = q('ssx_sequence', '--run', '20990101')
        per, _ = tables(out)
        check('ssx_cart_limit_is_an_admit', any(r['tcin'] == TC2 and r['admits_before'] == '1' for r in per), per)
        # walls / per_tcin / arms still reconcile with the new gate
        rc, out = q('walls', '--run', '20990101')
        w = tables(out)[0]
        ok = all(int(r['n']) == int(r['w1_limited']) + int(r['w1_pass']) + int(r['other']) + int(r['unknown'])
                 and int(r['w1_pass']) == sum(int(r[c]) for c in ('w2_denied', 'fs', 'inv', 'carts', 'climit'))
                 for r in w)
        check('walls_reconcile_with_cart_limit', rc == 0 and ok and sum(int(r['climit']) for r in w) == 1, w)
        rc, out = q('per_tcin', '--run', '20990101')
        pt = {r['tcin']: r for r in tables(out)[0]}
        check('per_tcin_counts_cart_limit_as_w1_pass', pt[TC2]['first_w1'] == '1/1' and pt[TC2]['climit'] == '1', pt)
        rc, out = q('regime', '--run', '20990101')
        rg = tables(out)[0][0]
        check('regime_cart_limit_admitted', rc == 0 and (rg['first'], rg['later']) == ('2/2', '2/3'), rg)
        rc, out = q('po_by_age', '--run', '20990104')
        per_run, pooled, reading = tables(out)
        bins = {r['bin']: r['ok_of_posts'] for r in per_run}
        check('po_by_age_bins', bins == {'1 <=5s': '1/2', '2 6-30s': '0/1', '3 31-120s': '0/1', '4 >120s': '0/2'},
              bins)
        check('po_by_age_pooled_bounds', {r['bin']: (r['est'], r['at_lo_bound'], r['at_hi_bound']) for r in pooled}
              .get('1 <=5s') == ('1/2', '1/2', '1/1'), pooled)
        check('po_by_age_reading_needs_10_early', reading[0]['reading'] == 'INCONCLUSIVE', reading)
        rc, out = q('late_carts', '--run', '20990104')
        carts, by_bin, rd = tables(out)
        c0 = {r['cart_line']: r for r in carts}
        check('late_carts_first_po', rc == 0 and len(carts) == 2 and
              sorted((r['ident'], r['po1_status'], r['n_po'], r['any_200']) for r in carts)
              == [('business', '429', '3', '0'), ('primary', '200', '1', '1')], c0)
        rc, out = q('SELECT COUNT(*) AS n FROM place_orders', '--run', 'no_such_run')
        check('q_run_filter_covers_place_orders', out.strip().splitlines()[-1] == '0', out)
        rc, out = q('windows')
        wr = tables(out)[0]
        check('windows_query_flip_only', rc == 0 and [r['run'] for r in wr] == ['run_20990104_000000'], wr)
        rc, out = q('SELECT src, COUNT(*) AS n FROM windows GROUP BY src ORDER BY src')
        check('windows_table_keeps_both_sources', [tuple(r.values()) for r in tables(out)[0]]
              == [('flip', '1'), ('reads', '2')], out)


SCR = 'src.monitoring.stock_check_resilient'


def _status_run_lines(base):
    tb, tc = '1010892074', '1010892076'
    return [
        L(base, f"[STOCK STATUS] tcin={tc} #1 old=(first) new=OUT_OF_STOCK|SA|svc1|- in_stock=0 services=1 "
                f"atp=0.0 reason=- via=s3", name=SCR),
        L(base + 1000, f"[STOCK STATUS] tcin={tb} #1 old=(first) new=OUT_OF_STOCK|SA|svc0|- in_stock=0 "
                       f"services=0 atp=- reason=- via=s4", name=SCR),
        L(base + 60000, f"[STOCK STATUS] tcin={tb} #2 old=OUT_OF_STOCK|SA|svc0|- new=PRE_ORDER_SELLABLE|SA|svc0|- "
                        f"in_stock=0 services=0 atp=5.0 reason=no_services via=s9", name=SCR),
        L(base + 60001, f"[STOCK] SELLABLE-PARSED-OOS tcin={tb} #1 reason=no_services avail=PRE_ORDER_SELLABLE "
                        f"loyalty=- rtc=SA services=0 via=s9", name=SCR, level='WARNING'),
        L(base + 61000, f"[STOCK STATUS] tcin={tb} #3 old=PRE_ORDER_SELLABLE|SA|svc0|- "
                        f"new=PRE_ORDER_SELLABLE|SA|svc1|PRE_ORDER_SELLABLE in_stock=1 services=2 atp=5.0 "
                        f"reason=- via=s2", name=SCR),
        L(base + 62000, f"[STOCK STATUS] tcin={tc}: 200 changes logged this run; further changes of this TCIN "
                        f"are not logged", name=SCR),
        L(base + 90000, "[STOCK][206-SHADOW] bodies=40 qualifying=39 tcin_reads=663 paired=120 agree=119 "
                        f"disagree=1 in_stock_206=['{tb}'] err_segs=store_positions:663,fulfillment:1 "
                        f"first_disagree=tcin={tb} 206=1 200=0 age_s=0.80 avail206=PRE_ORDER_SELLABLE "
                        f"avail200=OUT_OF_STOCK", name=SCR),
        L(base + 150000, "[STOCK][206-SHADOW] bodies=3 qualifying=0 tcin_reads=0 paired=0 agree=0 disagree=0 "
                         "in_stock_206=[] err_segs=?:3 first_disagree=-", name=SCR),
        L(base + 151000, "[STOCK][206-SHADOW] not counted: TypeError: boom", name=SCR),
    ]


def test_v3_status_and_shadow():
    lines = _status_run_lines(T) + [
        f"[STOCK STATUS] tcin=1010892074 #9 printed instead of logged",            # wrong form (print)
        L(T + 200000, "[STOCK STATUS] tcin=1010892074 garbled", name=SCR),          # logger, unparseable
    ]
    res = parse(lines)
    st = res['stock_status']
    check('v3_status_rows', [(r['tcin'], r['n'], r['avail'], r['in_stock']) for r in st] ==
          [('1010892076', 1, 'OUT_OF_STOCK', 0), ('1010892074', 1, 'OUT_OF_STOCK', 0),
           ('1010892074', 2, 'PRE_ORDER_SELLABLE', 0), ('1010892074', 3, 'PRE_ORDER_SELLABLE', 1)], st)
    check('v3_status_fields', st[2]['old_key'] == 'OUT_OF_STOCK|SA|svc0|-' and st[2]['reason'] == 'no_services'
          and st[2]['atp'] == 5.0 and st[1]['atp'] is None and st[0]['reason'] is None and st[3]['services'] == 2
          and st[0]['ts_ms'] == T and st[2]['via'] == 's9', st)
    so = res['sellable_oos']
    check('v3_sellable_oos_row', len(so) == 1 and (so[0]['tcin'], so[0]['reason'], so[0]['avail'], so[0]['loyalty'],
                                                   so[0]['services']) ==
          ('1010892074', 'no_services', 'PRE_ORDER_SELLABLE', None, 0), so)
    sh = res['shadow206']
    check('v3_shadow_rows', [(r['bodies'], r['qualifying'], r['paired'], r['agree'], r['disagree'], r['in_stock_206'])
                             for r in sh] == [(40, 39, 120, 119, 1, '1010892074'), (3, 0, 0, 0, 0, '')], sh)
    check('v3_shadow_first_disagree', sh[0]['first_disagree'].startswith('tcin=1010892074 206=1 200=0')
          and sh[1]['first_disagree'] is None and sh[0]['err_segs'] == 'store_positions:663,fulfillment:1', sh)
    u = {r['marker']: (r['n'], r['seen']) for r in res['unparsed']}
    check('v3_notes_and_errors_parsed_not_rows', check_of(res, 'status_note_lines') == '1'
          and check_of(res, 'shadow206_error_lines') == '1', res['checks'])
    check('v3_unparsed_remainder', u.get('STATUS') == (1, 6) and u.get('STATUS@wrong_form') == (1, 1)
          and u.get('SELLOOS') == (0, 1) and u.get('SHADOW206') == (0, 3), u)
    # q.py's --run views must cover every run-scoped table build.py writes (drift guard)
    check('v3_q_run_tables_cover_schema', set(evq.RUN_TABLES) - {'runs'} == set(ev.RUN_TABLES),
          sorted(set(ev.RUN_TABLES) ^ (set(evq.RUN_TABLES) - {'runs'})))
    with tempfile.TemporaryDirectory() as tmp:
        logs = Path(tmp) / 'runs'
        logs.mkdir()
        for name, ls in (('run_20990201_000000', _status_run_lines(T)),
                         ('run_20990202_000000', _status_run_lines(T + 86400000)[:2])):
            (logs / (name + '.log')).write_bytes(('\r\n'.join(ls) + '\r\n').encode('utf-8'))
        db = Path(tmp) / 'ev.sqlite'
        with contextlib.redirect_stdout(io.StringIO()):
            ev.main(['--db', str(db), '--logs', str(logs), '--quiet'])

        def q(*args):
            b = io.StringIO()
            with contextlib.redirect_stdout(b):
                rc = evq.main(['--db', str(db), '--csv'] + list(args))
            rows_ = list(csv.reader(io.StringIO(b.getvalue().strip())))
            return rc, [dict(zip(rows_[0], r)) for r in rows_[1:]] if rows_ else []

        rc, sc = q('status_changes', '--run', '20990201')
        by = {r['tcin']: r for r in sc}
        check('v3_status_changes_query', rc == 0 and set(by) == {'1010892074', '1010892076'}
              and (by['1010892074']['changes'], by['1010892074']['sellable_reads'], by['1010892074']['in_stock_reads'],
                   by['1010892074']['parsed_oos'], by['1010892074']['oos_reasons']) == ('3', '2', '1', '1', 'no_services')
              and by['1010892074']['last_status'] == 'PRE_ORDER_SELLABLE|SA|svc1|PRE_ORDER_SELLABLE', sc)
        rc, sc2 = q('status_changes', '--run', '20990202')
        check('v3_status_changes_run_filter', rc == 0 and {r['run'] for r in sc2} == {'run_20990202_000000'}
              and all(r['parsed_oos'] == '0' for r in sc2), sc2)
        rc, sh2 = q('shadow206', '--run', '20990201')
        check('v3_shadow206_query', rc == 0 and len(sh2) == 1 and (sh2[0]['bodies'], sh2[0]['paired'],
                                                                   sh2[0]['disagree'], sh2[0]['in_206']) ==
              ('43', '120', '1', '1010892074') and sh2[0]['first_disagree'].startswith('tcin=1010892074'), sh2)


# ---------------------------------------------------------------------------------
# v3 (2026-10-05) builders -- shapes copied from purchase_executor.py atc_net_line, worker_pool.py /
# app.py [BOOT_SKIP], the [DOUBLE-BUY GUARD] prints, the won-cart re-read and the foreign-cart bail
# (run_20260930_233818 L58040; run_20260720_231634 L17147 for the wrapped fetch_threw reason)
# ---------------------------------------------------------------------------------
NET_IP4, NET_IP6 = '151.101.2.187:443', '[2600:1408:c400::17d5:b24]:443'


def net(ident, m, st, send, conn=55, reused=1, ip=NET_IP4, ttfb=120, cms='-', ssl='-', rid='1234.5'):
    return (f"[ATC_NET] ident={ident} m={m} st={st} conn={conn} reused={reused} ip={ip} proto=h2 "
            f"send_ms={send} ttfb_ms={ttfb} connect_ms={cms} ssl_ms={ssl} rid={rid}")


def _net_run_lines():
    """A flip-opened race: primary / business / alt-1 fire at the flip and reach the wire in that
    order (send 107 / 140 / 405 ms after the flip); primary is admitted (FAST_SELLING), the other two
    edge-limited. Then the rows the join must NOT force: a POST with no shot near it, two POSTs inside
    one re-shot's window, a status mismatch, a GET, an all-'-' row, a POST without send_ms."""
    inst = "[ATC_NET] Network meta instrument installed on the main tab ident="
    lines = [POOL, inst + 'primary', inst + 'business', inst + 'alt-1',
             flip(TC, 1, T, 1), race(TC, ['primary', 'business', 'alt-1'])]
    lines += [fire(TC), fire(TC), fire(TC)] + cred('primary', T + 40) + cred('business', T + 41) + cred('alt-1', T + 42)
    lines += [net('primary', 'OPTIONS', 204, T + 67, reused=0, ttfb=31, cms=12, ssl=8, rid='1234.4'),
              net('primary', 'POST', 429, T + 107, rid='1234.5'),
              net('business', 'POST', 429, T + 140, conn=56, reused=0, ip=NET_IP6, cms=14, ssl=9, rid='1240.1')]
    lines += resp(T + 167, 429, FSK) + resp(T + 180, 429, A2C)
    # alt-1's [ATC_NET] print with its own [ATC_RESP] logger copy glued onto the line
    lines += [f"[INTERCEPTOR:main] {resp_msg(429, A2C)}",
              net('alt-1', 'POST', 429, T + 405, conn=57, ttfb='-', rid='1250.2') + L(T + 455, resp_msg(429, A2C))]
    lines += [chain('primary', 429, T + 47, 120), chain('business', 429, T + 50, 130), chain('alt-1', 429, T + 55, 400)]
    lines += [tick(T + 30000), net('primary', 'POST', 201, T + 30010, rid='1290.1')]      # a POST no shot claims
    lines += shot('primary', TC, T + 60000, status=429, key=A2C, rt=150)               # the re-shot
    lines += [net('primary', 'POST', 429, T + 60030, rid='1301.1'),                    # dt 30: wins
              net('primary', 'POST', 429, T + 60070, rid='1301.2'),                    # dt 70: lost the contest
              net('primary', 'POST', 201, T + 60050, rid='1301.3'),                    # status mismatch
              net('primary', 'GET', 200, T + 61000, rid='1302.1'),
              "[ATC_NET] ident=business m=? st=429 conn=- reused=- ip=-:- proto=- send_ms=- ttfb_ms=- "
              "connect_ms=- ssl_ms=- rid=1303.1",
              net('business', 'POST', 429, '-', conn=58, ttfb=88, rid='1303.2'),
              L(T + 61500, net('alt-1', 'POST', 429, T + 61400, rid='1304.1')),         # logger copy: wrong form
              "[ATC_NET] ident=? (format error)"]
    return lines


def test_v3_atc_net():
    res = parse(_net_run_lines())
    sh = [(s['ident'], s['shot_idx'], s['gate'], s['join_q']) for s in res['shots']]
    check('v3_net_lines_leave_shots_alone', sh == [('primary', 1, 'admitted_fs', 'ts'), ('business', 1, 'wall1_limited', 'ts'),
                                                   ('alt-1', 1, 'wall1_limited', 'ts'), ('primary', 2, 'wall1_limited', 'ts')]
          and check_of(res, 'main_resp') == '4', sh)
    an = res['atc_net']
    got = [(r['ident'], r['method'], r['join_q'], r['join_dt_ms'], r['shot_idx'], r['shot_gate']) for r in an]
    check('v3_net_rows_and_joins', got == [
        ('primary', 'OPTIONS', 'ts', 20, 1, 'admitted_fs'),
        ('primary', 'POST', 'ts', 60, 1, 'admitted_fs'),
        ('business', 'POST', 'ts', 90, 1, 'wall1_limited'),
        ('alt-1', 'POST', 'ts_rt', 350, 1, 'wall1_limited'),        # 300 < dt <= atc_rt (400) + 10
        ('primary', 'POST', 'no_shot', None, None, None),
        ('primary', 'POST', 'ts_multi', 30, 2, 'wall1_limited'),
        ('primary', 'POST', 'contested', None, None, None),
        ('primary', 'POST', 'no_shot', None, None, None),           # 201 vs the shot's 429: never forced
        ('primary', 'GET', None, None, None, None),
        ('business', '?', None, None, None, None),
        ('business', 'POST', 'no_send_ms', None, None, None)], got)
    b = an[2]
    check('v3_net_ipv6_and_fields', (b['ip'], b['port'], b['reused'], b['conn'], b['connect_ms'], b['ssl_ms'], b['proto'],
                                     b['rid'], b['send_ms'], b['status'], b['ttfb_ms'])
          == ('[2600:1408:c400::17d5:b24]', 443, 0, 56, 14, 9, 'h2', '1240.1', T + 140, 429, 120), b)
    a = an[3]
    check('v3_net_glued_logger_line', (a['rid'], a['ttfb_ms'], a['ip'], a['port']) == ('1250.2', None, '151.101.2.187', 443), a)
    d = an[9]
    check('v3_net_dash_fields_null', (d['status'], d['conn'], d['reused'], d['ip'], d['port'], d['proto'], d['send_ms'],
                                      d['connect_ms']) == (429, None, None, None, None, None, None, None), d)
    p = an[1]
    check('v3_net_copies_the_shot', (p['race_seq'], p['tcin'], p['is_first'], p['flip_opened_race'], p['shot_ts_ms'],
                                     p['shot_line']) == (1, TC, 1, 1, T + 47, res['shots'][0]['line']), p)
    u = {r['marker']: (r['n'], r['seen'], r['first_line']) for r in res['unparsed']}
    check('v3_net_unjoined_counted', u.get('ATC_NET@post_unjoined') == (4, 8, an[4]['line']), u.get('ATC_NET@post_unjoined'))
    check('v3_net_format_error_counted', u.get('ATC_NET', (None,))[:2] == (1, 15), u.get('ATC_NET'))
    check('v3_net_logger_copy_not_a_row', u.get('ATC_NET@wrong_form', (None,))[:2] == (1, 1) and len(an) == 11, u)
    check('v3_net_checks', check_of(res, 'atc_net_installed') == "{'primary': 1, 'business': 1, 'alt-1': 1}"
          and check_of(res, 'atc_net_t0_shots_without_post') == '0'
          and eval(check_of(res, 'atc_net_post_join')) == {'ts': 2, 'ts_rt': 1, 'ts_multi': 1, 'no_shot': 2,
                                                           'contested': 1, 'no_send_ms': 1}, res['checks'])
    # without atc_rt the window is [-10, 300]: a send 350 ms after atc_t0 is no longer joinable
    lines = [POOL, race(TC, ['alt-1']), fire(TC), net('alt-1', 'POST', 429, T + 350),
             "[FAST_LANE] chain done in 0.42s — atc=429 pre=0 po=0 skip=atc_429 ident=alt-1 "
             f"atc_t0={T} atc_rt=- cart_qty=-",
             net('alt-1', 'POST', 429, T - 11)]                                   # before its JS start: no
    res = parse(lines)
    check('v3_net_no_rt_window_300', [r['join_q'] for r in res['atc_net']] == ['no_shot', 'no_shot'], res['atc_net'])
    res = parse(lines[:3] + [net('alt-1', 'POST', 429, T + 299)] + lines[4:5])
    check('v3_net_no_rt_inside_300', [(r['join_q'], r['join_dt_ms']) for r in res['atc_net']] == [('ts', 299)],
          res['atc_net'])
    check('v3_net_absent_means_no_rows', parse([POOL, race(TC, ['primary'])] + shot('primary', TC, T))['atc_net'] == [])


def _gate_run_lines():
    pool2 = "[WORKER_POOL] sized from target_accounts.json: 2 account(s) -> ['business', 'alt-1']"
    return [
        flip(TC, 1, T - 1000, 1),                                                # a measured UTC offset
        tick(T),
        "[WORKER_POOL] [BOOT_SKIP] skipping primary this boot — its boot login probe failed as Worker 1 (boot login "
        "probe failed (no greeting), x2, 13 min ago). Hand-login it (relogin_one.py primary --manual --force) to restore.",
        pool2,
        "[WORKER_POOL] [BOOT_SKIP] every enabled account is on the skip list (['alt-1', 'business']) — IGNORING it "
        "and booting the full fleet",
        tick(T + 1000),
        "[SYSTEM] [BOOT_SKIP] business (Worker 1) recorded — the relaunch races WITHOUT it; hand-login it to restore"
        + tick(T + 1500),
        "[SYSTEM] [BOOT_SKIP] business failed too while a skip was in force — not an account problem; skip list "
        "CLEARED, the relaunch boots the full fleet",
        "[SYSTEM] [BOOT_SKIP] no skip recorded (none, fleet=1)",
        tick(T + 5000),
        "[FAST_LANE] [DOUBLE-BUY GUARD] place-order got HTTP 202 (skip=none) — an unexpected 2xx (FX-1005-PO2XX) — the "
        "order may exist; POST may have committed. Bailing terminal, NOT retrying.",
        "[PAYMENT] [DOUBLE-BUY GUARD] place-order got HTTP 204 (an unexpected 2xx, FX-1005-PO2XX — the order may exist) "
        "(reason=http_204) — POST may have committed; NOT clicking DOM Place Order. Bailing terminal.",
        "[FAST_LANE] [DOUBLE-BUY GUARD] place-order got HTTP 502 (skip=none) — a gateway error does not prove the order "
        "failed; POST may have committed. Bailing terminal, NOT retrying.",
        "[PAYMENT] [DOUBLE-BUY GUARD] place-order got NO response (reason=fetch_threw:Inspected target navigated or closed",
        "command:Runtime.evaluate",
        "[FAST_LANE] [DOUBLE-BUY GUARD] place-order got NO response (skip=evaluate_timeout) — POST may have committed. "
        "Bailing terminal, NOT retrying.",
        "[WON_CART_DIRECT] cart may hold a late add of ours (foreign_entry) — cart read shows only 1010892076 x2: "
        "po_only kept ident=alt-1",
        "[WON_CART_DIRECT] cart may hold a late add of ours (foreign_entry) — pre_po instead of po_only (cart read: "
        "2 line(s) 1010892076x2,95082118x1) ident=W3/alt-1",
        "[WON_CART_DIRECT] cart may hold a late add of ours (harvest_add) — pre_po instead of po_only (cart read: "
        "failed status=429 err=-) ident=business",
        # two prints glued: the bail's ident stops at the '[' of the next print, both are rows
        "[FAST_LANE] foreign/extra cart item present — clearing cart + re-racing a fresh ATC (NOT buying the whole "
        "cart) ident=business[WON_CART_DIRECT] cart may hold a late add of ours (orphan_atc) — cart read shows only "
        "1010892076 x1: po_only kept ident=business",
        "[FAST_LANE] cart clear after foreign_cart_item failed (HTTP 429 (cart_items))",
        L(T + 9000, "[FAST_LANE] [DOUBLE-BUY GUARD] place-order got HTTP 202 (skip=none) — an unexpected 2xx "
                    "(FX-1005-PO2XX)"),                                          # logger form: counted, no row
    ]


def test_v3_gate_events():
    res = parse(_gate_run_lines())
    g = [(r['kind'], r['ident_or_acct'], r['detail']) for r in res['gate_events']]
    check('v3_gate_rows', g == [
        ('boot_skip_skipping', 'primary', 'reason=boot login probe failed (no greeting) count=2 age_min=13'),
        ('boot_skip_ignored_all', None, "skip_list=['alt-1', 'business']"),
        ('boot_skip_recorded', 'business', None),
        ('boot_skip_cleared', 'business', None),
        ('boot_skip_none', None, 'act=none fleet=1'),
        ('po2xx_guard', None, 'path=fast_lane status=202 skip=none fx=1'),
        ('po2xx_guard', None, 'path=payment status=204 reason=http_204 fx=1'),
        ('po_gateway_guard', None, 'path=fast_lane status=502 skip=none fx=0'),
        ('po_noresp_guard', None, 'path=payment status=- reason=fetch_threw:Inspected target navigated or closed fx=0'),
        ('po_noresp_guard', None, 'path=fast_lane status=- skip=evaluate_timeout fx=0'),
        ('foreign_keep_po_only', 'alt-1', 'sus=foreign_entry tcin=1010892076 qty=2'),
        ('foreign_keep_pre_po', 'alt-1', 'sus=foreign_entry read=2 line(s) 1010892076x2,95082118x1'),
        ('late_add_pre_po', 'business', 'sus=harvest_add read=failed status=429 err=-'),
        ('foreign_bail', 'business', None),
        ('late_add_po_only', 'business', 'sus=orphan_atc tcin=1010892076 qty=1'),
        ('foreign_bail_clear_failed', None, 'err=HTTP 429 (cart_items)')], g)
    ts = [(r['ts_ms'], r['ts_src']) for r in res['gate_events']]
    check('v3_gate_prior_logger_time', ts[0] == (T, 'logger_prior') and ts[2] == (T + 1000, 'logger_prior')
          and ts[3] == (T + 1500, 'logger_prior') and ts[5] == (T + 5000, 'logger_prior'), ts)
    check('v3_gate_skipped_account_not_glue_repaired', check_of(res, 'ident_glue_repaired') is None,
          check_of(res, 'ident_glue_repaired'))
    u = {r['marker']: (r['n'], r['seen']) for r in res['unparsed']}
    check('v3_gate_remainder', (u.get('BOOT_SKIP'), u.get('PO_GUARD'), u.get('WCD_LATE'), u.get('FL_FOREIGN'),
                                u.get('GATE@wrong_form')) == ((0, 5), (0, 5), (0, 4), (0, 2), (1, 1)), u)
    # a changed format is counted, never a row
    res = parse([POOL, "[SYSTEM] [BOOT_SKIP] something new happened",
                 "[WON_CART_DIRECT] cart may hold a late add of ours (foreign_entry) — something else"])
    u = {r['marker']: (r['n'], r['seen']) for r in res['unparsed']}
    check('v3_gate_unknown_format_counted', not res['gate_events'] and u.get('BOOT_SKIP') == (1, 1)
          and u.get('WCD_LATE') == (1, 1), u)


def test_v3_net_gate_queries():
    with tempfile.TemporaryDirectory() as tmp:
        logs = Path(tmp) / 'runs'
        logs.mkdir()
        for name, ls in (('run_20990301_000000', _net_run_lines()), ('run_20990302_000000', _gate_run_lines()),
                         ('run_20990303_000000', _ssx_lines('wwa'))):
            (logs / (name + '.log')).write_bytes(('\r\n'.join(ls) + '\r\n').encode('utf-8'))
        db = Path(tmp) / 'ev.sqlite'
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ev.main(['--db', str(db), '--logs', str(logs)])
        out = buf.getvalue()
        check('v3_db_built_with_summary', '3 file(s) parsed' in out and 'atc_net=11 (POST joined 4/8)' in out
              and "'po2xx_guard': 2" in out, out)

        def q(*args):
            b = io.StringIO()
            with contextlib.redirect_stdout(b):
                rc = evq.main(['--db', str(db), '--csv'] + list(args))
            res_ = []
            for blk in b.getvalue().strip().split('\n\n'):
                rows_ = list(csv.reader(io.StringIO(blk.strip())))
                res_.append([dict(zip(rows_[0], r)) for r in rows_[1:]] if rows_ else [])
            return rc, res_

        rc, (cov, rank, reuse, ip) = q('atc_net')
        check('v3_q_atc_net_coverage_only_instrumented_runs', rc == 0 and [r['run'] for r in cov] == ['run_20990301_000000'],
              cov)
        c = cov[0] if cov else {}
        check('v3_q_atc_net_coverage', tuple(c.get(k) for k in (
            'net_lines', 'post_lines', 'options_lines', 'other_lines', 'shots', 't0_shots', 'post_joined', 'joined_pct',
            'post_unjoined', 't0_no_post', 'q_ts', 'q_ts_rt', 'q_multi', 'options_joined', 'fo_races_2shots',
            'fo_volleys')) == ('11', '8', '1', '2', '4', '4', '4', '50.0', '4', '0', '2', '1', '1', '1', '1', '1'), c)
        got = [(r['wire_rank'], r['n'], r['w1_pass'], r['limited'], r['avg_behind_ms']) for r in rank]
        check('v3_q_atc_net_wire_rank', got == [('1', '1', '1', '0', '0.0'), ('2', '1', '0', '1', '33.0'),
                                                ('3', '1', '0', '1', '298.0')], got)
        got = [(r['reused'], r['n'], r['w1_pass'], r['limited'], r['pass_pct'], r['rank1']) for r in reuse]
        check('v3_q_atc_net_reuse', got == [('0', '1', '0', '1', '0.0', '0'), ('1', '2', '1', '1', '50.0', '1')], got)
        got = [(r['edge_ip'], r['n'], r['idents'], r['w1_pass'], r['limited'], r['rank1']) for r in ip]
        check('v3_q_atc_net_edge_ip', got == [('151.101.2.187', '2', '2', '1', '1', '1'),
                                              ('[2600:1408:c400::17d5:b24]', '1', '1', '0', '1', '0')], got)
        rc, (ge,) = q('gate_events')
        by = {(r['run'], r['kind']): r for r in ge}
        check('v3_q_gate_events_counts', rc == 0 and {k[1]: r['n'] for k, r in by.items()} == {
            'boot_skip_skipping': '1', 'boot_skip_ignored_all': '1', 'boot_skip_recorded': '1', 'boot_skip_cleared': '1',
            'boot_skip_none': '1', 'po2xx_guard': '2', 'po_gateway_guard': '1', 'po_noresp_guard': '2',
            'foreign_keep_po_only': '1', 'foreign_keep_pre_po': '1', 'late_add_pre_po': '1', 'late_add_po_only': '1',
            'foreign_bail': '1', 'foreign_bail_clear_failed': '1'}
              and all(k[0] == 'run_20990302_000000' for k in by), ge)
        r0 = by.get(('run_20990302_000000', 'boot_skip_skipping'), {})
        check('v3_q_gate_events_first_ts_and_who', (r0.get('who'), r0.get('first_ts')) == ('primary', stamp(T)[:19])
              and by[('run_20990302_000000', 'po2xx_guard')]['first_detail'] == 'path=fast_lane status=202 skip=none fx=1',
              r0)
        rc, (ge2,) = q('gate_events', '--run', '20990301')
        check('v3_q_gate_events_run_filter', rc == 0 and ge2 == [], ge2)
        rc, (cov2, *_rest) = q('atc_net', '--run', '20990302')
        check('v3_q_atc_net_run_filter', rc == 0 and cov2 == [], cov2)


def test_v4_fx1006_and_diag_gate_events():
    """v4 (2026-10-06): FX-1006 dispatch markers + run diagnostics -> gate_events."""
    lines = [
        POOL,
        L(T, "[STOCK STATS] t=30.0s sweeps=90 (3.0/s) 200=89 403=0 429=0 other=1", name='src.monitoring.stock_check_resilient'),
        "[FX1006] multi-SKU dispatch flags: FAIR=1 MISS=1 KEEP=1 YIELD=1 LOCK=1",
        f"[MULTI_SKU_DISPATCH] {TC}: reserved ['W1/primary'] (alongside ['{TC2}'])",
        f"[MULTI_SKU_FAIR_SHARE] {TC}: 2 other fresh TCIN(s) ['{TC2}', '{TC3}'] -> taking 1 of 3 firing free worker(s) (cap 3; 0 would sit it out)",
        f"[MULTI_SKU_MISS] {TC3} in-stock but SKIPPED — '{TC}#W1' holds the fleet (3 ready worker(s)); distinct hot SKU not pursued this cycle.",
        f"[MULTI_SKU_MISS_REARM] {TC3}: hinted -- the level re-arm re-offers it while it reads in stock (window age 1.2s)",
        f"[MULTI_SKU_MISS_REARM] {TC2}: NOT hinted -- one of our accounts bought it this window (a repeat buy needs a real out-then-in flip)",
        f"[WAVE_YIELD] W2/business {TC}: ending this window mid-wait to free the account for skipped live TCIN {TC3} (keeping this attempt's result)",
        f"[FX1006_KEEP] {TC2}: kept 'failed' -- absent from this event, still in stock, failed this window; the level re-arm re-races it",
        f"[FX1006] {TC}: bought -- no FX-1006 path re-offers it in this stock window",
        "[STALL_WATCHDOG] armed -- every 30 s: the bus / dashboard / purchase-state locks must be free within 45 s",
        "[STALL_WATCHDOG] STALL: event_bus.lock not free for 45s",
        "[STALL_WATCHDOG] ===== every thread's stack (event_bus.lock not free for 45s) =====",
        "[STALL_WATCHDOG] --- thread MainThread (1234) ---",
        '[STALL_WATCHDOG]   File "app.py", line 10, in main',
        "[STALL_WATCHDOG] ===== end of stacks =====",
        "[HEALTH] rss=900MB os_threads=80 py_threads=40 handles=2000 chrome_procs=24 sys_cpu=12% sys_mem=40% disk_free=180GB active_purchases=0 reservations=0",
        L(T + 1000, "[STOCK][OTHER] 60s: http=206x12 http=0/TimeoutErrorx3 | by exit: 1.2.3.4x9",
          name='src.monitoring.stock_check_resilient'),
        L(T + 2000, "[MULTI_SESSION] pick_session steering away from souring /16s ['168.158'] (4 of 18 ready session(s) excluded; >= 2 4xx in 300s)",
          name='src.session.multi_session_pool'),
    ]
    res = parse(lines)
    g = [(r['kind'], r['ident_or_acct'], (r['detail'] or '')[:40]) for r in res['gate_events']]
    kinds = [k for k, _a, _d in g]
    check('v4_kinds', kinds == ['fx1006_flags', 'multi_sku_dispatch', 'fair_share', 'multi_sku_miss', 'miss_rearm',
                                'miss_rearm', 'wave_yield', 'keep_live_failed', 'fx1006_bought',
                                'stall_watchdog_armed', 'stall', 'health', 'monitor_other', 'subnet_steer'], kinds)
    d = {k: (a, det) for k, a, det in g}
    check('v4_tcin_prefix', d['multi_sku_dispatch'][1].startswith('tcin=%s ' % TC)
          and d['keep_live_failed'][1].startswith('tcin=%s ' % TC2), g)
    check('v4_flags_no_tcin', d['fx1006_flags'][1].startswith('multi-SKU dispatch flags'), d['fx1006_flags'])
    check('v4_yield_acct', d['wave_yield'][0] == 'W2/business', d['wave_yield'])
    check('v4_stall_detail', d['stall'][1].startswith('event_bus.lock not free'), d['stall'])
    nh = [r for r in res['gate_events'] if r['kind'] == 'miss_rearm' and 'NOT hinted' in (r['detail'] or '')]
    check('v4_not_hinted_kept', len(nh) == 1, nh)
    u = {r['marker']: (r['n'], r['seen']) for r in res['unparsed']}
    check('v4_remainder_zero', all(u.get(k, (0, 0))[0] == 0 for k in ('FX1006', 'STALL', 'HEALTH', 'OTHER', 'STEER')), u)
    check('v4_stack_lines_no_rows', sum(1 for r in res['gate_events'] if r['kind'].startswith('stall')) == 2,
          [r['kind'] for r in res['gate_events']])
    check('v4_parser_version', ev.PARSER_VERSION >= 4, ev.PARSER_VERSION)


# ---------------------------------------------------------------------------------
# v5 (2026-10-15, INS-STORE-HARVEST-1015): the harvest table + phase.sql
# ---------------------------------------------------------------------------------
CAP_URL = ("https://carts.target.com/web_checkouts/v1/cart_items?field_groups=CART%2CCART_ITEMS%2CSUMMARY"
           "&key=9f36aeafbe60771e321a7c")
CAP_BODY = ("body='{\"cart_item\":{\"item_channel_id\":\"10\",\"tcin\":\"21516452\",\"quantity\":1},"
            "\"cart_type\":\"REGULAR\",\"channel_id\":\"10\",\"shopping_context\":\"DIGITAL\"}'")


def hv_captured(ident, ep_ms, bank=1, age=0, tokens=6, nb=False, body=CAP_BODY, tail=True, harvested=1):
    """A [HARVEST/x] CAPTURED line pair (print twin + logger twin), shaped like run_20260925_004139 L603-604."""
    m = (f"[HARVEST/{ident}] CAPTURED{' (NO Shape tokens — not banked)' if nb else ''} tokens={tokens} a0=no "
         f"a_len=7807 a0_len=0 hdr_bytes=13782 cookie=5198 clicks_since_nav=1 hdrs=16 url={CAP_URL} {body}")
    if tail:
        m += f" | bank={bank}/6 newest_age={age}s harvested={harvested} replayed=0 expired=0 stale=0"
    return [m, L(ep_ms, m)]


def hv_preshot(ident, ep_ms, bank=5, age='24s', replay='on'):
    m = (f"[HARVEST/{ident}] pre-shot bank={bank}/6 newest_age={age} harvested=190 replayed=2 expired=183 "
         f"stale=0 replay={replay}")
    return [m, L(ep_ms, m)]


def hv_empty(ident, ep_ms):
    m = (f"[HARVEST/{ident}] bank EMPTY at shot time — shot goes page-signed "
         f"(bank=0/6 newest_age=- harvested=3 replayed=3 expired=0 stale=0)")
    return [m, L(ep_ms, m)]


def hv_stale(ident, ep_ms):
    m = (f"[HARVEST/{ident}] bank STALE at shot time (past the replay cap) — shot goes page-signed "
         f"(bank=2/6 newest_age=130s harvested=9 replayed=4 expired=3 stale=1)")
    return [m, L(ep_ms, m)]


def shot_with(ident, tc, t0, cred_lines, status=429, key=A2C, rt=120):
    """shot() with a caller-chosen [HARVEST] cred line pair (replay / empty / stale)."""
    return [fire(tc)] + cred_lines + resp(t0 + rt, status, key) + [chain(ident, status, t0, rt)]


def exposure(ident, proxied='no'):
    return (f"[EXPOSURE] ident=W1/{ident} tcin=1010892076 kind=edge run_shots=1 run_s=0 win_age_s=0 "
            f"chrome_age_s=2674 proxied={proxied} resting=no")


def test_v5_harvest_rows():
    base = [POOL, flip(TC, 1, T, 1)]                   # the flip fixes the UTC offset (ts_ms = the real epoch)
    # (1) a print + logger CAPTURED pair is ONE row; counters and lengths only
    res = parse(base + hv_captured('alt-1', T + 5000, bank=1, age=0))
    hv = res['harvest']
    check('v5_captured_pair_is_one_row', len(hv) == 1, hv)
    h = hv[0] if hv else {}
    check('v5_captured_fields', (h.get('acct'), h.get('event'), h.get('banked'), h.get('bank_n'), h.get('bank_max'),
                                 h.get('newest_age_s'), h.get('ts_ms'), h.get('ts_src')) ==
          ('alt-1', 'captured', 1, 1, 6, 0, T + 5000, 'logger'), h)
    check('v5_captured_lengths', (h.get('tokens'), h.get('a0'), h.get('a_len'), h.get('a0_len'), h.get('hdr_bytes'),
                                  h.get('cookie_bytes'), h.get('clicks_since_nav'), h.get('n_hdrs')) ==
          (6, 0, 7807, 0, 13782, 5198, 1, 16), h)
    check('v5_captured_counters', (h.get('c_harvested'), h.get('c_replayed'), h.get('c_expired'), h.get('c_stale'),
                                   h.get('set_age_s'), h.get('replay_on')) == (1, 0, 0, 0, None, None), h)
    strs = {k: v for r in hv for k, v in r.items() if isinstance(v, str)}
    check('v5_only_acct_event_and_ts_src_are_text', set(strs) == {'acct', 'event', 'ts_src'}
          and not any('carts.target' in v or 'cart_item' in v for v in strs.values()), strs)
    # (2) the "(NO Shape tokens — not banked)" variant: still a row, banked = 0
    res = parse(base + hv_captured('primary', T + 5000, bank=3, age=31, tokens=0, nb=True))
    h = (res['harvest'] or [{}])[0]
    check('v5_no_token_variant_banked_0', (len(res['harvest']), h.get('event'), h.get('banked'), h.get('tokens'),
                                           h.get('bank_n'), h.get('newest_age_s')) == (1, 'captured', 0, 0, 3, 31), h)
    # (3) pre-shot pair: one row, replay flag, an empty bank prints newest_age=-
    res = parse(base + hv_preshot('business', T + 6000) + hv_preshot('primary', T + 7000, bank=0, age='-', replay='OFF'))
    hv = res['harvest']
    check('v5_preshot_pairs_are_two_rows', [(r['acct'], r['event'], r['bank_n'], r['bank_max'], r['newest_age_s'],
                                             r['replay_on'], r['c_harvested'], r['c_stale']) for r in hv] ==
          [('business', 'pre_shot', 5, 6, 24, 1, 190, 0), ('primary', 'pre_shot', 0, 6, None, 0, 190, 0)], hv)
    # (4) REPLAY / EMPTY / STALE rows are the very lines that feed shots.cred_source
    lines = base + [race(TC, ['primary', 'business', 'alt-1'])]
    lines += shot_with('primary', TC, T + 47, cred('primary', T + 42, age=12))
    lines += shot_with('business', TC, T + 50, hv_empty('business', T + 44))
    lines += shot_with('alt-1', TC, T + 55, hv_stale('alt-1', T + 49))
    res = parse(lines)
    hv, sh = res['harvest'], res['shots']
    check('v5_cred_rows_agree_with_shots', [r['event'] for r in hv] == ['replay', 'empty', 'stale']
          and [(s['cred_source'], s['cred_detail']) for s in sh] ==
          [('banked_replay', 'replay'), ('page_signed', 'empty'), ('page_signed', 'stale')]
          and [r['acct'] for r in hv] == [s['ident'] for s in sh], (hv, sh))
    rp, em, st = hv
    check('v5_replay_row_fields', (rp['set_age_s'], rp['tokens'], rp['a0'], rp['hdr_bytes'], rp['cookie_bytes'],
                                   rp['a0_len'], rp['bank_n'], rp['bank_max'], rp['newest_age_s'], rp['c_harvested'],
                                   rp['c_replayed'], rp['c_expired'], rp['c_stale'], rp['ts_ms']) ==
          (12, 6, 0, 13757, 5152, 0, 4, 6, 39, 50, 4, None, None, T + 42), rp)
    check('v5_empty_and_stale_row_fields', (em['bank_n'], em['newest_age_s'], em['set_age_s'], em['c_replayed'],
                                            st['bank_n'], st['newest_age_s'], st['c_expired'], st['c_stale']) ==
          (0, None, None, 3, 2, 130, 3, 1), (em, st))
    check('v5_cred_unclaimed_unchanged', check_of(res, 'cred_unclaimed') == '0', check_of(res, 'cred_unclaimed'))


def test_v5_harvest_dedupe_and_remainder():
    base = [POOL, flip(TC, 1, T, 1)]
    # a print copy glued in front of its own logger twin (the 09-25 L33940 shape) is still ONE row
    pair = hv_captured('alt-1', T + 5000)
    res = parse(base + [pair[0] + pair[1]])
    check('v5_glued_print_plus_logger_is_one_row', len(res['harvest']) == 1 and res['harvest'][0]['bank_n'] == 1,
          res['harvest'])
    # a print-only line (no logger twin) is not read: the logger form is the one form, as for [HARVEST] REPLAY
    res = parse(base + [pair[0]])
    check('v5_print_only_line_is_not_a_row', res['harvest'] == [], res['harvest'])
    # 20 pairs on one account = 20 rows, in log order, each with its own stamp
    lines = base[:]
    for i in range(20):
        lines += hv_captured('alt-1', T + 5000 + i * 3000, bank=min(i + 1, 6))
    res = parse(lines)
    check('v5_twenty_pairs_twenty_rows', len(res['harvest']) == 20
          and [r['ts_ms'] for r in res['harvest']] == [T + 5000 + i * 3000 for i in range(20)]
          and res['harvest'][-1]['bank_n'] == 6, len(res['harvest']))
    # a url / body echo that itself looks like a bank counter never wins over the line's real tail
    body = "body='{\"note\":\"bank=9/9 newest_age=99s harvested=9 tokens=99 hdrs=77\"}'"
    res = parse(base + hv_captured('primary', T + 5000, bank=2, age=7, body=body))
    h = (res['harvest'] or [{}])[0]
    check('v5_last_bank_group_wins', (h.get('bank_n'), h.get('bank_max'), h.get('newest_age_s')) == (2, 6, 7), h)
    check('v5_counters_read_only_before_url', (h.get('tokens'), h.get('n_hdrs'), h.get('a_len')) == (6, 16, 7807), h)
    # a CAPTURED line whose bank counters cannot be read: the row is kept (NULLs) AND counted in unparsed
    res = parse(base + hv_captured('primary', T + 5000, tail=False))
    h = (res['harvest'] or [{}])[0]
    u = {r['marker']: (r['n'], r['seen'], r['first_line']) for r in res['unparsed']}
    check('v5_unreadable_tail_counted_not_guessed', len(res['harvest']) == 1 and h.get('bank_n') is None
          and h.get('event') == 'captured' and u.get('CRED', (0,))[0] == 1, (h, u.get('CRED')))
    # the family accounting: seen = parsed for well-formed CAPTURED / pre-shot lines
    res = parse(base + hv_captured('primary', T + 5000) + hv_preshot('primary', T + 6000) + cred('primary', T + 7000))
    u = {r['marker']: (r['n'], r['seen']) for r in res['unparsed']}
    check('v5_family_accounting_clean', u.get('CRED') == (0, 3), u.get('CRED'))


def test_v5_other_tables_unchanged():
    """The harvest lines add rows to `harvest` and to nothing else: the same log with and without them gives
    identical rows in every other table (the cred events still feed the shot join exactly as in v4)."""
    core = [POOL, flip(TC, 1, T, 1), race(TC, ['primary', 'business'])]
    core += shot('primary', TC, T + 47) + shot('business', TC, T + 50, status=429, key=FSK)
    core += shot('primary', TC, T + 60000, status=401, key='-')
    # appended after the core lines so every core line keeps its line number (rows are compared whole)
    extra = hv_captured('primary', T - 3000) + hv_captured('business', T - 2000) + hv_preshot('primary', T + 40) + \
        hv_preshot('business', T + 41, bank=0, age='-') + hv_captured('primary', T + 20000, bank=2, age=0)
    with_ = parse(core + extra)
    without = parse(core)
    same = all(with_[t] == without[t] for t in ('shots', 'races', 'flips', 'windows', 'tickets', 'loop_ends', 'decoys',
                                                 'monitor_stats', 'orders', 'idents', 'place_orders', 'atc_net',
                                                 'gate_events', 'stock_status'))
    bad = [t for t in ('shots', 'races', 'flips', 'windows', 'decoys', 'place_orders', 'gate_events', 'idents')
           if with_[t] != without[t]]
    check('v5_every_other_table_identical', same, bad)
    check('v5_checks_identical', with_['checks'] == without['checks'], (with_['checks'], without['checks']))
    ua = {r['marker']: (r['n'], r['first_line']) for r in with_['unparsed']}
    ub = {r['marker']: (r['n'], r['first_line']) for r in without['unparsed']}
    check('v5_unparsed_same_markers_and_remainders', ua == ub, (ua, ub))
    check('v5_no_extra_unparsed_row', len(with_['unparsed']) == len(without['unparsed']),
          (len(with_['unparsed']), len(without['unparsed'])))
    check('v5_harvest_counts', [r['event'] for r in with_['harvest']].count('captured') == 3
          and [r['event'] for r in with_['harvest']].count('pre_shot') == 2
          and [r['event'] for r in with_['harvest']].count('replay') == 3, [r['event'] for r in with_['harvest']])
    check('v5_parser_version', ev.PARSER_VERSION >= 5, ev.PARSER_VERSION)       # v6 (INS-LAG-1015) bumped it


def _rbank_lines(live, extra=None):
    """A flip-opened race, 3 home accounts, every first shot page-signed (bank EMPTY). `live` maps each account
    to when its CAPTURED sits relative to the flip (ms, negative = before) and whether it banked."""
    lines = [POOL, exposure('primary'), exposure('business'), exposure('alt-1')]
    for ident, (off_ms, nb) in live.items():
        lines += hv_captured(ident, T + off_ms, bank=0 if nb else 1, tokens=0 if nb else 6, nb=nb)
    if extra:
        lines += extra
    lines += [flip(TC, 1, T, 1), race(TC, ['primary', 'business', 'alt-1'])]
    lines += shot_with('primary', TC, T + 47, hv_empty('primary', T + 40), status=401, key='-')
    lines += shot_with('business', TC, T + 50, hv_empty('business', T + 41), status=429, key=A2C)
    lines += shot_with('alt-1', TC, T + 55, hv_empty('alt-1', T + 42), status=429, key=FSK)
    return lines


def _ps_shots_lines(n):
    """n page-signed past-limiter shots (keyless 401s) by one home account."""
    lines = [POOL, exposure('primary'), flip(TC, 1, T, 1), race(TC, ['primary'])]
    for i in range(n):
        lines += shot_with('primary', TC, T + 47 + i * 4000, hv_empty('primary', T + 40 + i * 4000), status=401, key='-')
    return lines


def _ps_mixed_lines(n_home, n_prox):
    """n_home page-signed past-limiter shots by a HOME account (exposure proxied=no) and n_prox by a PROXIED one
    (exposure proxied=yes) in the same run; the shots run one after another, 4 s apart."""
    lines = [POOL, exposure('primary', 'no'), exposure('business', 'yes'), flip(TC, 1, T, 1),
             race(TC, ['primary', 'business'])]
    for i in range(n_home + n_prox):
        ident = 'primary' if i < n_home else 'business'
        lines += shot_with(ident, TC, T + 47 + i * 4000, hv_empty(ident, T + 40 + i * 4000), status=401, key='-')
    return lines


def _overlap_lines():
    """TC opens at T and keeps reading; TC2 opens 5 s later while TC's window is still open (overlapped):
    TC's first shot lands 47 ms in, TC2's 1.5 s in."""
    return [POOL, exposure('primary'), exposure('business'),
            flip(TC, 1, T, 1), flip(TC2, 1, T + 5000, 1), flip(TC, 2, T + 20000, 0),
            race(TC, ['primary'])] + shot('primary', TC, T + 47) + \
        [race(TC2, ['business'])] + shot('business', TC2, T + 6500)


def _volley_lines():
    """One flip-opened volley: atc_t0 order primary / business / alt-1, WIRE order alt-1 / primary / business;
    alt-1 is the FAST_SELLING pass. The two orders disagree for all three accounts."""
    inst = "[ATC_NET] Network meta instrument installed on the main tab ident="
    lines = [POOL, inst + 'primary', inst + 'business', inst + 'alt-1', flip(TC, 1, T, 1),
             race(TC, ['primary', 'business', 'alt-1'])]
    lines += [fire(TC), fire(TC), fire(TC)] + cred('primary', T + 40) + cred('business', T + 41) + cred('alt-1', T + 42)
    lines += [net('alt-1', 'POST', 429, T + 100, rid='1.1'), net('primary', 'POST', 429, T + 120, rid='1.2'),
              net('business', 'POST', 429, T + 140, rid='1.3')]
    lines += resp(T + 165, 429, FSK) + resp(T + 167, 429, A2C) + resp(T + 180, 429, A2C)
    lines += [chain('primary', 429, T + 47, 120), chain('business', 429, T + 50, 130), chain('alt-1', 429, T + 55, 110)]
    return lines


def test_v5_phase_query():
    with tempfile.TemporaryDirectory() as tmp:
        logs = Path(tmp) / 'runs'
        logs.mkdir()
        runs = {
            'run_20990401_000000': _rbank_lines({'primary': (-60000, False), 'business': (-120000, False),
                                                 'alt-1': (-30000, False)},
                                                extra=[f for f in shot('primary', TC2, T - 50000)]),
            'run_20990402_000000': _rbank_lines({'primary': (-60000, False), 'business': (-120000, False),
                                                 'alt-1': (-60000, True)}),
            'run_20990408_000000': _rbank_lines({'primary': (-60000, False), 'business': (-400000, False),
                                                 'alt-1': (-60000, True)}),
            'run_20990403_000000': [POOL, flip(TC, 1, T, 1), race(TC, ['primary'])] +
            shot_with('primary', TC, T + 47, hv_empty('primary', T + 40)),      # shots, no [HARVEST] bank lines but EMPTY
            'run_20990404_000000': _ps_shots_lines(19),
            'run_20990405_000000': _ps_shots_lines(20),
            'run_20990406_000000': _overlap_lines(),
            'run_20990407_000000': _volley_lines(),
            # INS-STORE-HARVEST-1015 fix round 2: the C-1015-04 gate counts HOME shots only
            'run_20990409_000000': _ps_mixed_lines(0, 25),            # 25 page-signed past-limiter shots, all PROXIED
            'run_20990410_000000': _ps_mixed_lines(5, 20),            # 25 over all lines, 5 on the home line
            'run_20990411_000000': _ps_mixed_lines(20, 5),            # 20 home + 5 proxied
            # R-BANK 300 s window pinned from below (290 s: live) and from above (310 s: not live)
            'run_20990412_000000': _rbank_lines({'primary': (-290000, False), 'business': (-290000, False),
                                                 'alt-1': (-290000, False)}),
            'run_20990413_000000': _rbank_lines({'primary': (-310000, False), 'business': (-310000, False),
                                                 'alt-1': (-310000, False)}),
            # 25 page-signed past-limiter shots whose account has no exposure / boot-forwarder evidence (proxied NULL)
            'run_20990414_000000': [flip(TC, 1, T, 1), race(TC, ['primary'])] + [
                ln for i in range(25)
                for ln in shot_with('primary', TC, T + 47 + i * 4000, hv_empty('primary', T + 40 + i * 4000),
                                    status=401, key='-')],
        }
        for name, lines in runs.items():
            (logs / (name + '.log')).write_bytes(('\r\n'.join(lines) + '\r\n').encode('utf-8'))
        db = Path(tmp) / 'ev.sqlite'
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ev.main(['--db', str(db), '--logs', str(logs), '--quiet'])
        check('v5_phase_db_built', '14 file(s) parsed' in buf.getvalue(), buf.getvalue())
        con = sqlite3.connect(str(db))
        nh = con.execute('SELECT COUNT(*) FROM harvest').fetchone()[0]
        con.close()
        out2 = io.StringIO()
        with contextlib.redirect_stdout(out2):
            ev.main(['--db', str(db), '--logs', str(logs), '--quiet', '--only', 'run_20990401_000000'])
        con = sqlite3.connect(str(db))
        check('v5_harvest_rows_not_duplicated_by_reparse', '0 file(s) parsed, 1 unchanged' in out2.getvalue()
              and con.execute('SELECT COUNT(*) FROM harvest').fetchone()[0] == nh, (out2.getvalue(), nh))
        con.execute("UPDATE ingested SET parser_version = parser_version - 1 WHERE file = 'run_20990401_000000.log'")
        con.commit()
        con.close()
        with contextlib.redirect_stdout(io.StringIO()):
            ev.main(['--db', str(db), '--logs', str(logs), '--quiet'])
        con = sqlite3.connect(str(db))
        check('v5_version_reparse_replaces_harvest_rows', con.execute('SELECT COUNT(*) FROM harvest').fetchone()[0] == nh, nh)
        con.close()

        def q(*args):
            b = io.StringIO()
            with contextlib.redirect_stdout(b):
                rc = evq.main(['--db', str(db), '--csv'] + list(args))
            res_ = []
            for blk in b.getvalue().strip().split('\n\n'):
                rows_ = list(csv.reader(io.StringIO(blk.strip())))
                res_.append([dict(zip(rows_[0], r)) for r in rows_[1:]] if rows_ else [])
            return rc, res_

        rc, res = q('phase')
        check('v5_phase_runs_all_13_statements', rc == 0 and len(res) == 13, (rc, len(res)))
        check('v5_verdict_scope_pooled_over_many_runs', res[12][0]['scope'] == 'POOLED over 14 runs: pass --run for one night',
              res[12])
        rc, rs = q('phase', '--run', '20990401')
        s1, s2, s3, s4, s5, s6, s7, s8, s9, s10, s11, s12, s13 = rs
        # R-BANK met: 3 page-signed fresh-flip first shots, each with a banked CAPTURED within 300 s
        v = s13[0]
        check('v5_verdict_scope_one_run', v['scope'] == 'one run', v)
        check('v5_rbank_met_3_live', (v['n_first'], v['n_captured'], v['n_pagesigned_live'],
                                      v['n_pagesigned_live_any_capture']) == ('3', '3', '3', '3')
              and v['r_bank'].startswith('R-BANK MET') and v['ps_past_limiter'] == '2'
              and v['c_1015_04'].startswith('C-1015-04 stays NOT ESTABLISHED'), v)
        g = sorted((r['ident'], r['other_tcin_110s'], r['cred_source'], r['harvest_live'], r['n'], r['past']) for r in s10)
        check('v5_rbank_fresh_flip_split', g == [('alt-1', 'no', 'page_signed', 'yes', '1', '1'),
                                                  ('business', 'no', 'page_signed', 'yes', '1', '0'),
                                                  ('primary', 'yes', 'page_signed', 'yes', '1', '1')], g)
        a = sorted((r['acct'], r['captured'], r['pre_shot'], r['empty'], r['cred_rows'], r['shots_with_cred'], r['bank_null'])
                   for r in s1)
        check('v5_phase_harvest_coverage_row', a == [('alt-1', '1', '0', '1', '1', '1', '0'),
                                                      ('business', '1', '0', '1', '1', '1', '0'),
                                                      ('primary', '1', '0', '1', '2', '2', '0')], a)
        # R-BANK not met at 2 (the rule is >= 3): primary and business live, alt-1's capture banked nothing
        rc, rs = q('phase', '--run', '20990402')
        v = rs[12][0]
        check('v5_rbank_not_met_at_2_live_3_any', (v['n_first'], v['n_pagesigned_live'], v['n_pagesigned_live_any_capture'],
                                                   v['r_bank']) == ('3', '2', '3', 'R-BANK NOT MET (< 3)'), v)
        # ... and a capture 400 s before the shot is outside the 5-minute window: only primary is live
        rc, rs = q('phase', '--run', '20990408')
        v = rs[12][0]
        check('v5_rbank_capture_older_than_5min_not_live', (v['n_first'], v['n_pagesigned_live'],
                                                            v['n_pagesigned_live_any_capture'], v['r_bank']) ==
              ('3', '1', '2', 'R-BANK NOT MET (< 3)'), v)
        # shots but no [HARVEST] bank lines other than EMPTY: harvest rows exist (the EMPTY), no CAPTURED -> NOT MET
        rc, rs = q('phase', '--run', '20990403')
        v = rs[12][0]
        check('v5_rbank_no_captured_is_not_met', (v['n_first'], v['n_captured'], v['n_pagesigned_live'], v['r_bank']) ==
              ('1', '0', '0', 'R-BANK NOT MET (< 3)'), v)
        # no run at all -> NO DATA, never NOT MET
        rc, rs = q('phase', '--run', 'no_such_run')
        v = rs[12][0]
        check('v5_rbank_no_data_when_no_first_shots', v['r_bank'] == 'NO DATA (no fresh-flip first shots)', v)
        # a store whose runs carry no harvest rows: shots exist but harvest table empty -> NO DATA
        con = sqlite3.connect(str(db))
        con.execute("DELETE FROM harvest WHERE run_id = 'run_20990403_000000'")
        con.commit()
        con.close()
        rc, rs = q('phase', '--run', '20990403')
        v = rs[12][0]
        check('v5_rbank_no_data_when_no_harvest_rows', v['r_bank'].startswith('NO DATA (no harvest rows'), v)
        # C-1015-04 moves only with >= 20 page-signed past-limiter shots
        rc, rs = q('phase', '--run', '20990404')
        a19 = rs[12][0]
        rc, rs = q('phase', '--run', '20990405')
        a20 = rs[12][0]
        check('v5_c1015_04_gate_at_20', (a19['ps_past_limiter'], a19['c_1015_04'].startswith('C-1015-04 stays')) == ('19', True)
              and (a20['ps_past_limiter'], a20['c_1015_04'].startswith('C-1015-04 may move')) == ('20', True), (a19, a20))
        # fix round 2: the gate is the HOME population (the claim's own). 25 page-signed past-limiter shots on a PROXIED
        # account (exposure proxied=yes) leave it at 0 and C-1015-04 NOT ESTABLISHED; the all-lines count is shown only
        rc, rs = q('phase', '--run', '20990409')
        p25 = rs[12][0]
        check('v5_c1015_04_proxied_shots_do_not_count', (p25['ps_past_limiter'], p25['ps_past_limiter_all_lines']) == ('0', '25')
              and p25['c_1015_04'].startswith('C-1015-04 stays NOT ESTABLISHED'), p25)
        # 5 home + 20 proxied: 25 over all lines (>= 20) but 5 home -> still NOT ESTABLISHED
        rc, rs = q('phase', '--run', '20990410')
        mx = rs[12][0]
        check('v5_c1015_04_mixed_25_all_5_home_stays', (mx['ps_past_limiter'], mx['ps_past_limiter_all_lines']) == ('5', '25')
              and mx['c_1015_04'].startswith('C-1015-04 stays NOT ESTABLISHED'), mx)
        # 20 home + 5 proxied: the home count alone reaches 20
        rc, rs = q('phase', '--run', '20990411')
        mv = rs[12][0]
        check('v5_c1015_04_20_home_plus_proxied_may_move', (mv['ps_past_limiter'], mv['ps_past_limiter_all_lines']) == ('20', '25')
              and mv['c_1015_04'].startswith('C-1015-04 may move'), mv)
        # a NULL-proxied shot is in neither population of the gate: 25 shots, no line evidence -> 0 home
        rc, rs = q('phase', '--run', '20990414')
        nl = rs[12][0]
        check('v5_c1015_04_null_proxied_shots_do_not_count', (nl['ps_past_limiter'], nl['ps_past_limiter_all_lines']) == ('0', '25')
              and nl['c_1015_04'].startswith('C-1015-04 stays NOT ESTABLISHED'), nl)
        # R-BANK 300 s window pinned from below: a banked CAPTURED ~290 s before each shot IS live (a window narrowed
        # to 200 s drops all three), and from above: ~310 s before is NOT
        rc, rs = q('phase', '--run', '20990412')
        v290 = rs[12][0]
        check('v5_rbank_capture_290s_before_is_live', (v290['n_first'], v290['n_pagesigned_live'], v290['n_pagesigned_live_any_capture']) ==
              ('3', '3', '3') and v290['r_bank'].startswith('R-BANK MET'), v290)
        rc, rs = q('phase', '--run', '20990413')
        v310 = rs[12][0]
        check('v5_rbank_capture_310s_before_not_live', (v310['n_first'], v310['n_pagesigned_live'], v310['n_pagesigned_live_any_capture']) ==
              ('3', '0', '0') and v310['r_bank'] == 'R-BANK NOT MET (< 3)', v310)
        # statement 2: the two past-limiter shots of run 0401 sit in the first 3 s of the window, page-signed
        rc, rs = q('phase', '--run', '20990401')
        s2 = rs[1]
        check('v5_phase_stmt2_early_page_signed', [(r['phase'], r['cs'], r['past'], r['d401'], r['adm']) for r in s2] ==
              [('early<=3s', 'page_signed', '2', '1', '1')], s2)
        # statements 3-4: one overlapped window (TC2, lag 1500 ms, opened while TC was open), one plain (47 ms)
        rc, rs = q('phase', '--run', '20990406')
        s3, s4 = rs[2], rs[3]
        check('v5_phase_overlapped_window_row', [(r['tcin'], r['first_lag'], r['open_others'], r['which']) for r in s3] ==
              [(TC2, '1500', '1', TC)], s3)
        check('v5_phase_overlap_summary', [(r['windows'], r['overlapped'], r['ov_lag_gt_500ms'], r['plain'],
                                            r['plain_lag_le_100ms'], r['plain_lag_gt_100ms'], r['unclassified'])
                                           for r in s4] == [('2', '1', '1', '1', '1', '0', '0')], s4)
        # statements 7-9: wire order alt-1 < primary < business, atc_t0 order primary < business < alt-1
        rc, rs = q('phase', '--run', '20990407')
        s7, s8, s9 = rs[6], rs[7], rs[8]
        check('v5_phase_volley_wire_order', [(r['ident'], r['wire_rank'], r['t0_rank'], r['wire_behind_ms'], r['pass'],
                                              r['order_differs']) for r in s8] ==
              [('alt-1', '1', '3', '0', '1', '1'), ('primary', '2', '1', '20', '0', '1'),
               ('business', '3', '2', '40', '0', '1')], s8)
        check('v5_phase_volley_summary', (s7[0]['volleys'], s7[0]['joined_first_posts']) == ('1', '3')
              and [(r['volleys'], r['order_differs'], r['mixed'], r['mixed_wire_first_is_pass'],
                    r['mixed_t0_first_is_pass']) for r in s9] == [('1', '1', '1', '1', '0')], (s7, s9))
        # --run applies to the new table too
        rc, rs = q('SELECT COUNT(*) AS n FROM harvest', '--run', 'no_such_run')
        check('v5_q_run_filter_covers_harvest', rs[0][0]['n'] == '0', rs)
        check('v5_q_run_tables_has_harvest', 'harvest' in evq.RUN_TABLES and 'harvest' in ev.RUN_TABLES, evq.RUN_TABLES)


# ---------------------------------------------------------------------------------
# v6 (2026-10-15, INS-LAG-1015): lock_events + dispatch_lag.sql + reflip.sql
# ---------------------------------------------------------------------------------
LOCK_NAME = 'src.session.session_manager'
LOCK_ST = '[PURCHASE_LOCK] Purchase started - validation paused'
LOCK_CP = '[PURCHASE_LOCK] Purchase complete - validation resumed'


def lock(kind, ep_ms):
    """The logger line session_manager.set_purchase_in_progress() writes (its print twin is a different text)."""
    return L(ep_ms, LOCK_ST if kind == 'start' else LOCK_CP, name=LOCK_NAME)


def _unp(res):
    return {r['marker']: (r['seen'], r['parsed'], r['n']) for r in res['unparsed']}


def test_v6_lock_events():
    base = [POOL, flip(TC, 1, T, 1)]
    # (1) the 10-09 shapes: three starts in one ms on three physical lines, a start glued behind a [DISPATCH] print
    #     (run_20261009_000934 L17979), one behind another print (L18433), two stamped lines on ONE physical line
    glued_a = f"[DISPATCH] {TC} → W3/alt-1" + lock('start', T + 102)
    glued_b = "[REAL_PURCHASE_THREAD] [QTY] qty=2 (manager decision; see [QTY] reason line)" + lock('start', T + 103)
    two_on_one = lock('start', T + 104) + lock('start', T + 105)
    res = parse(base + [lock('start', T + 100), lock('start', T + 100), lock('start', T + 100), glued_a, glued_b, two_on_one])
    lk = res['lock_events']
    check('v6_seven_starts_seven_rows', len(lk) == 7 and {r['kind'] for r in lk} == {'start'}, lk)
    check('v6_ts_is_the_lines_own_stamp', [r['ts_ms'] for r in lk] == [T + 100] * 3 + [T + 102, T + 103, T + 104, T + 105]
          and {r['ts_src'] for r in lk} == {'logger'}, lk)
    check('v6_one_row_per_stamped_segment_in_log_order', [r['line'] for r in lk] == [3, 4, 5, 6, 7, 8, 8], [r['line'] for r in lk])
    check('v6_kind_and_ts_only_no_ident', set(lk[0]) == {'line', 'ts_ms', 'ts_src', 'kind'}, set(lk[0]))
    check('v6_family_accounting_clean', _unp(res).get('LOCK') == (7, 7, 0) and 'LOCK@wrong_form' not in _unp(res), _unp(res))
    # (2) completes and the start / complete mix keep log order
    res = parse(base + [lock('start', T + 100), lock('complete', T + 900), lock('complete', T + 901)])
    check('v6_complete_rows', [(r['kind'], r['ts_ms']) for r in res['lock_events']] ==
          [('start', T + 100), ('complete', T + 900), ('complete', T + 901)], res['lock_events'])
    # (3) the print twin is no row and no remainder; the marker in PRINT text is counted @wrong_form; an unreadable
    #     [PURCHASE_LOCK] line keeps no row and is counted (never guessed into start / complete)
    res = parse(base + ["[SESSION] Purchase in progress - validation paused", "[SESSION] Purchase complete - validation resumed"])
    check('v6_print_twin_is_not_a_row', res['lock_events'] == [] and 'LOCK' not in _unp(res) and 'LOCK@wrong_form' not in _unp(res),
          (res['lock_events'], _unp(res)))
    res = parse(base + [LOCK_ST])
    check('v6_print_form_counted_wrong_form', res['lock_events'] == [] and _unp(res).get('LOCK@wrong_form') == (1, 0, 1), _unp(res))
    res = parse(base + [L(T + 100, '[PURCHASE_LOCK] Purchase aborted - validation paused', name=LOCK_NAME)])
    u = {r['marker']: (r['n'], r['first_line']) for r in res['unparsed']}
    check('v6_unreadable_lock_line_counted_not_guessed', res['lock_events'] == [] and u.get('LOCK') == (1, 3), (res['lock_events'], u))


def test_v6_other_tables_unchanged():
    """The lock lines add rows to `lock_events` and to nothing else: the same log with and without them gives identical
    rows in every other table, identical `checks`, and the same unparsed remainders (plus the one accounting row for
    the new LOCK family -- the only change to a pre-existing table, and n = 0)."""
    core = [POOL, flip(TC, 1, T, 1), race(TC, ['primary', 'business'])]
    core += shot('primary', TC, T + 47) + shot('business', TC, T + 50, status=429, key=FSK)
    core += shot('primary', TC, T + 60000, status=401, key='-')
    extra = [lock('start', T + 10), lock('start', T + 11), "[SESSION] Purchase in progress - validation paused",
             lock('complete', T + 900), lock('complete', T + 901)]
    with_, without = parse(core + extra), parse(core)
    tabs = ('shots', 'races', 'flips', 'windows', 'tickets', 'loop_ends', 'decoys', 'monitor_stats', 'orders', 'idents',
            'place_orders', 'atc_net', 'gate_events', 'stock_status', 'sellable_oos', 'shadow206', 'harvest')
    bad = [t for t in tabs if with_[t] != without[t]]
    check('v6_every_other_table_identical', not bad, bad)
    check('v6_checks_identical', with_['checks'] == without['checks'], (with_['checks'], without['checks']))
    ua = {r['marker']: (r['n'], r['first_line']) for r in with_['unparsed'] if r['marker'] != 'LOCK'}
    ub = {r['marker']: (r['n'], r['first_line']) for r in without['unparsed']}
    check('v6_unparsed_same_markers_and_remainders', ua == ub, (ua, ub))
    check('v6_exactly_one_new_unparsed_row_n0', len(with_['unparsed']) == len(without['unparsed']) + 1
          and _unp(with_).get('LOCK') == (4, 4, 0), _unp(with_))
    check('v6_lock_counts', [r['kind'] for r in with_['lock_events']] == ['start', 'start', 'complete', 'complete']
          and without['lock_events'] == [], with_['lock_events'])
    check('v6_parser_version', ev.PARSER_VERSION >= 6, ev.PARSER_VERSION)


def test_v6_store_and_cli():
    with tempfile.TemporaryDirectory() as tmp:
        logs = Path(tmp) / 'runs'
        logs.mkdir()
        lines = [POOL, flip(TC, 1, T, 1), lock('start', T + 100), lock('start', T + 101), lock('complete', T + 900)]
        (logs / 'run_20990501_000000.log').write_bytes(('\r\n'.join(lines) + '\r\n').encode('utf-8'))
        db = Path(tmp) / 'ev.sqlite'
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ev.main(['--db', str(db), '--logs', str(logs)])
        check('v6_summary_line_names_lock_events', "lock_events={'complete': 1, 'start': 2}" in buf.getvalue(), buf.getvalue()[:400])
        check('v6_unparsed_remainder_printed_and_empty', 'unparsed remainder: none' in buf.getvalue()
              and 'unparsed remainder over the parsed files: none' in buf.getvalue(), buf.getvalue()[-300:])
        con = sqlite3.connect(str(db))
        cols = [r[1] for r in con.execute('PRAGMA table_info(lock_events)')]
        check('v6_table_columns', cols == ['run_id', 'line', 'ts_ms', 'ts_src', 'kind'], cols)
        check('v6_rows_stored', con.execute('SELECT kind, COUNT(*) FROM lock_events GROUP BY kind ORDER BY kind').fetchall()
              == [('complete', 1), ('start', 2)])
        check('v6_index_exists', con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'ix_lock_run'").fetchone()[0] == 1)
        con.close()
        out2 = io.StringIO()
        with contextlib.redirect_stdout(out2):
            ev.main(['--db', str(db), '--logs', str(logs), '--quiet'])
        con = sqlite3.connect(str(db))
        check('v6_unchanged_file_is_skipped_no_duplicates', '0 file(s) parsed, 1 unchanged' in out2.getvalue()
              and con.execute('SELECT COUNT(*) FROM lock_events').fetchone()[0] == 3, out2.getvalue())
        # a v5 store: no lock_events table, the file ingested at parser_version 5 -> the bump re-parses it and the table returns
        con.execute('DROP TABLE lock_events')
        con.execute('UPDATE ingested SET parser_version = 5')
        con.commit()
        con.close()
        out3 = io.StringIO()
        with contextlib.redirect_stdout(out3):
            ev.main(['--db', str(db), '--logs', str(logs), '--quiet'])
        con = sqlite3.connect(str(db))
        check('v6_v5_store_reparsed_and_table_recreated', '1 file(s) parsed' in out3.getvalue()
              and con.execute('SELECT COUNT(*) FROM lock_events').fetchone()[0] == 3
              and con.execute('SELECT parser_version FROM ingested').fetchone()[0] == ev.PARSER_VERSION, out3.getvalue())
        con.close()
        # a stale-version re-parse replaces the rows (no duplicates)
        con = sqlite3.connect(str(db))
        con.execute('UPDATE ingested SET parser_version = parser_version - 1')
        con.commit()
        con.close()
        with contextlib.redirect_stdout(io.StringIO()):
            ev.main(['--db', str(db), '--logs', str(logs), '--quiet'])
        con = sqlite3.connect(str(db))
        check('v6_version_reparse_replaces_rows', con.execute('SELECT COUNT(*) FROM lock_events').fetchone()[0] == 3)
        con.close()
        qb = io.StringIO()
        with contextlib.redirect_stdout(qb):
            evq.main(['--db', str(db), '--csv', 'SELECT COUNT(*) AS n FROM lock_events', '--run', 'no_such_run'])
        check('v6_q_run_filter_covers_lock_events', qb.getvalue().split() == ['n', '0'], qb.getvalue())
        check('v6_q_run_tables_has_lock_events', 'lock_events' in evq.RUN_TABLES and 'lock_events' in ev.RUN_TABLES, evq.RUN_TABLES)


def _q_for(db):
    def q(*args):
        b = io.StringIO()
        with contextlib.redirect_stdout(b):
            rc = evq.main(['--db', str(db), '--csv'] + list(args))
        res_ = []
        for blk in b.getvalue().strip().split('\n\n'):
            rows_ = list(csv.reader(io.StringIO(blk.strip())))
            res_.append([dict(zip(rows_[0], r)) for r in rows_[1:]] if rows_ else [])
        return rc, res_
    return q


def _build_store(tmp, runs):
    logs = Path(tmp) / 'runs'
    logs.mkdir()
    for name, lines in runs.items():
        (logs / (name + '.log')).write_bytes(('\r\n'.join(lines) + '\r\n').encode('utf-8'))
    db = Path(tmp) / 'ev.sqlite'
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ev.main(['--db', str(db), '--logs', str(logs), '--quiet'])
    return db, buf.getvalue()


def _win_lines(tc, fr, lag, locks, ident='primary', wire_gap=None, disp=None):
    """One flip window of `tc` read at `fr`: [PURCHASE_LOCK] lines at fr + offset (`locks`, (kind, ms)), the dispatch start at
    fr + lag - 30 (or `disp`), a single-account race whose first shot leaves at fr + lag (an [ATC_NET] POST wire_gap ms
    after it when given), then the race ends and its thread completes."""
    ev_ = sorted([(off, k) for k, off in locks] + [((lag - 30) if disp is None else disp, 'start')])
    out = [lock(k, fr + off) for off, k in ev_ if off < 0]
    out.append(flip(tc, 1, fr, 1))
    out += [lock(k, fr + off) for off, k in ev_ if off >= 0]
    out.append(race(tc, [ident]))
    if wire_gap is not None:
        out.append(net(ident, 'POST', 429, fr + lag + wire_gap))
    out += shot(ident, tc, fr + lag, rt=120)
    out += [done(tc, 1, 1, [(ident, 'rate_limited_429')]), lock('complete', fr + lag + 2000)]
    return out


def _busy_run(lags, wire_gap=None):
    """One busy_other window per lag: a thread live at the flip (start at -500, complete at +300)."""
    lines = [POOL]
    for i, lag in enumerate(lags):
        lines += _win_lines(str(1010892100 + i), T + i * 100000, lag, [('start', -500), ('complete', 300)], wire_gap=wire_gap)
    return lines


def _same_run():
    """TC opens at T (idle: its racer A starts at +12, never ends), re-opens at T + 20 s while A still shoots: A re-fires at
    +757 ms of the new window, the new window's own race B only at +7,242 ms (the 10-09 window 4 shape)."""
    return ([POOL, lock('start', T + 12), flip(TC, 1, T, 1), race(TC, ['primary'])] + shot('primary', TC, T + 45) +
            [flip(TC, 2, T + 20000, 1)] + shot('primary', TC, T + 20757) +
            [lock('start', T + 27212), race(TC, ['business'])] + shot('business', TC, T + 27242))


def _busy_redisp_run(lags, redisp_lag, wire_gap=None):
    """`_busy_run(lags)` plus one REDISPATCHED busy_other window (the 20990503 / 10-09 window 9 shape: the fleet is sent to
    another TCIN at +3,000 before this window's own dispatch at redisp_lag - 30)."""
    return _busy_run(lags, wire_gap=wire_gap) + _win_lines(
        '1010892199', T + len(lags) * 100000, redisp_lag,
        [('start', -500), ('complete', 100), ('start', 3000), ('complete', 3200)], wire_gap=wire_gap)


def _busy_nodisp_run(lags):
    """`_busy_run(lags)` plus one busy_other window with NO dispatch found (INS-LAG-1015 fix round 3): a race took the lock
    1,000 ms before the new_window = 1 flip (outside the [first_read - 200 ms, first shot] dispatch search) and its first shot
    fires 20 ms after the read, so the window is busy and its first race starts at/after the read, yet no lock start explains
    it -> intervening_starts NULL, release / tick / start NULL."""
    fr = T + len(lags) * 100000
    tc = '1010892150'
    return _busy_run(lags) + [lock('start', fr - 1000), race(tc, ['primary']), flip(tc, 1, fr, 1)] + \
        shot('primary', tc, fr + 20) + [done(tc, 1, 1, [('primary', 'rate_limited_429')]), lock('complete', fr + 2000)]


def test_v6_dispatch_lag_query():
    runs = {
        'run_20990501_000000': [POOL] + _win_lines(TC3, T, 45, [], disp=12),                       # idle
        'run_20990502_000000': [POOL] + _win_lines(TC, T, 1830, [('start', -500), ('complete', 300)], wire_gap=90),
        'run_20990503_000000': [POOL] + _win_lines(TC2, T, 6030, [('start', -500), ('complete', 100), ('start', 3000),
                                                                  ('complete', 3200)]),             # redispatched
        'run_20990504_000000': _same_run(),                                                         # busy_same
        'run_20990505_000000': [POOL, lock('start', T - 500), flip(TC, 1, T, 1)],                   # no_shot
        'run_20990506_000000': [POOL, flip(TC, 1, T, 1), race(TC, ['primary'])] + shot('primary', TC, T + 45),   # unknown
        'run_20990507_000000': [POOL] + _win_lines(TC, T, 1830, [('start', -500), ('complete', 300)], disp=1825,
                                                   wire_gap=-8),                                    # wire before dispatch
        # R-LAG verdict runs
        'run_20990510_000000': _busy_run([1600, 1700, 1800]),                    # median 1700 > 1500            => BUILD
        'run_20990511_000000': _busy_run([600, 600, 600, 600, 2500]),            # 1 of 5 = 20% over 2 s         => BUILD
        'run_20990512_000000': _busy_run([700, 800, 900, 1900]),                 # median 850, p90 1900          => SKIP
        'run_20990513_000000': _busy_run([1200, 1300, 1400, 1450]),              # median 1350                   => INCONCLUSIVE
        'run_20990514_000000': _busy_run([500, 600, 700, 800, 900, 2100]),       # median 750 but p90 2100, 16.7% => INCONCLUSIVE
        'run_20990515_000000': _busy_run([1500, 1500, 1500]),                    # exactly 1500 is NOT over      => INCONCLUSIVE
        'run_20990516_000000': _busy_run([1000, 1000, 2000]),                    # exactly 1000 / 2000 are inside => SKIP
        'run_20990517_000000': _busy_run([3000, 4000]),                          # 2 busy windows                => NO DATA
        'run_20990518_000000': _busy_run([1480, 1490, 1495]),                    # t0 median 1490 / wire 1520 differ
        'run_20990519_000000': _busy_run([1480, 1490, 1495], wire_gap=30),       #   (the wire basis flips the verdict)
        # R-LAG excludes the redispatched (w9 / FAIR-FRESH) window: 3 clean busy windows + 1 redispatched one at 6,030 ms
        'run_20990520_000000': _busy_redisp_run([600, 700, 800], 6030),          # n_busy 3 => SKIP (with it in: 25 % > 2 s => BUILD)
        'run_20990521_000000': _busy_redisp_run([600, 700, 800], 6030, wire_gap=30),   # the wire basis excludes it too
        'run_20990522_000000': _busy_redisp_run([600, 700], 6030),               # 2 clean + 1 redispatched => NO DATA (not n = 3)
        # a lock start AFTER the window's first shot (another account's purchase thread) must not become the dispatch
        'run_20990523_000000': [POOL] + _win_lines(TC, T, 1830, [('start', -500), ('complete', 300), ('start', 2330),
                                                                 ('complete', 2700)]),
        # a busy_other window with NO dispatch found (intervening_starts NULL) is out of the R-LAG population on both bases and is
        # counted: 3 clean busy windows + one whose lock start is outside the dispatch search (its 20 ms would drag the median)
        'run_20990524_000000': _busy_nodisp_run([600, 700, 800]),
    }
    with tempfile.TemporaryDirectory() as tmp:
        db, out = _build_store(tmp, runs)
        q = _q_for(db)
        check('v6_dl_db_built', '%d file(s) parsed' % len(runs) in out, out)

        def win(run, n=1):
            rc, rs = q('dispatch_lag', '--run', run)
            assert rc == 0, (run, rc)
            return rs[0][:n] if n else rs[0], rs

        # idle: the flip dispatches within a few ms, no release, no wait for a tick
        (w,), rs = win('20990501')
        check('v6_dl_idle_window', (w['busy_class'], w['busy'], w['release_ms'], w['tick_ms'], w['start_ms'], w['first_shot_any_ms'],
                                    w['lag_flip_race'], w['late']) == ('idle', '0', '0', '12', '33', '45', '45', '0'), w)
        # busy_other: release 300 + tick 1,500 + start 30 = the first shot at 1,830; ordering on the WIRE (+90 ms)
        (w,), rs = win('20990502')
        check('v6_dl_busy_other_split_adds_up', (w['busy_class'], w['busy'], w['release_ms'], w['tick_ms'], w['start_ms'], w['interv'],
                                                 w['first_shot_any_ms'], w['lag_flip_race'], w['first_is_flip'], w['late']) ==
              ('busy_other', '1', '300', '1500', '30', '0', '1830', '1830', '1', '1')
              and 300 + 1500 + 30 == 1830, w)
        check('v6_dl_wire_basis_and_order_ok', (w['first_wire_any_ms'], w['ord_ok']) == ('1920', '1'), w)
        # a dispatch to ANOTHER TCIN between the flip and the window's own dispatch is counted, not hidden
        (w,), rs = win('20990503')
        check('v6_dl_redispatched_window', (w['busy_class'], w['release_ms'], w['tick_ms'], w['start_ms'], w['interv'],
                                            w['first_shot_any_ms']) == ('busy_other', '3200', '2800', '30', '1', '6030'), w)
        rc, rs = q('dispatch_lag', '--run', '20990502,20990503')
        s4 = rs[3][0]
        check('v6_dl_split_leaves_out_the_redispatched_window', (s4['windows'], s4['release_ms'], s4['tick_ms'], s4['start_ms'],
                                                                  s4['total_ms'], s4['redispatched']) ==
              ('1', '300', '1500', '30', '1830', '1'), s4)
        # busy_same: the first shot comes from a racer already running (757 ms), the flip-opened race only at 7,242 ms
        ws, rs = win('20990504', 0)
        w = ws[1]
        check('v6_dl_busy_same_window', (ws[0]['busy_class'], w['busy_class'], w['busy'], w['first_shot_any_ms'], w['lag_flip_race'],
                                         w['first_ident'], w['first_is_flip'], w['release_ms'], w['tick_ms'], w['start_ms'],
                                         w['late']) ==
              ('idle', 'busy_same', '1', '757', '7242', 'primary', '0', '', '', '', '1'), (ws[0], w))
        s3 = {r['busy_class']: r for r in rs[2]}
        check('v6_dl_by_class_late_counts', (s3['busy_same']['windows'], s3['busy_same']['late'], s3['busy_same']['late_flip_race'],
                                             s3['idle']['late']) == ('1', '1', '1', '0'), s3)
        # no_shot / unknown are classes of their own, never idle
        (w,), rs = win('20990505')
        check('v6_dl_no_shot_class', (w['busy_class'], w['busy'], w['first_shot_any_ms']) == ('no_shot', '1', ''), w)
        (w,), rs = win('20990506')
        i = rs[1][0]
        check('v6_dl_unknown_when_no_lock_events', (w['busy_class'], w['busy'], w['release_ms'], i['unknown'], i['idle']) ==
              ('unknown', '', '', '1', '0'), (w, i))
        check('v6_dl_unknown_verdict_no_data', rs[4][0]['r_lag'].startswith('NO DATA (no lock_events'), rs[4])
        # wire order check: a dispatch logged AFTER the first request left on the wire is flagged
        (w,), rs = win('20990507')
        i = rs[1][0]
        check('v6_dl_ord_bad_flagged', (w['ord_ok'], w['start_ms'], i['ord_bad'], i['wire_before_t0']) == ('0', '5', '1', '0'), (w, i))
        check('v6_dl_instrument_lock_balance', (i['lock_starts'], i['lock_completes'], i['imbalance']) == ('2', '2', '0'), i)
        # R-LAG verdicts: the build line, the share line, the skip line, the gap between, the edges, too few busy windows
        want = {'20990510': ('3', '1700', '1800', 'R-LAG BUILD C2 (MISS-PHASE-TICK-1015)'),
                '20990511': ('5', '600', '2500', 'R-LAG BUILD C2 (MISS-PHASE-TICK-1015)'),
                '20990512': ('4', '850', '1900', 'R-LAG SKIP C2'),
                '20990513': ('4', '1350', '1450', 'R-LAG INCONCLUSIVE (between the build and skip lines)'),
                '20990514': ('6', '750', '2100', 'R-LAG INCONCLUSIVE (between the build and skip lines)'),
                '20990515': ('3', '1500', '1500', 'R-LAG INCONCLUSIVE (between the build and skip lines)'),
                '20990516': ('3', '1000', '2000', 'R-LAG SKIP C2'),
                '20990517': ('2', '3500', '4000', 'NO DATA (< 3 busy windows)')}
        got = {}
        for run in want:
            rc, rs = q('dispatch_lag', '--run', run)
            v = rs[4][0]
            got[run] = (v['n_busy'], '%d' % round(float(v['median_ms'])), v['p90_ms'], v['r_lag'])
        check('v6_dl_rlag_verdict_table', got == want, {k: (got[k], want[k]) for k in want if got[k] != want[k]})
        rc, rs = q('dispatch_lag', '--run', '20990511')
        v = rs[4][0]
        check('v6_dl_rlag_share_line_is_gt_2000', (v['gt_2s'], v['pct_gt_2s']) == ('1', '20.0'), v)
        # a REDISPATCHED busy_other window (intervening_starts > 0) is shown and counted, but is not in the R-LAG population:
        # 3 clean windows (600 / 700 / 800 ms) + the 6,030 ms redispatched one => n_busy 3, SKIP. Were it in, 1 of 4 > 2 s = 25 %
        # >= 20 % would read BUILD C2 off a window the tick does not explain.
        rc, rs = q('dispatch_lag', '--run', '20990520')
        v, i, bc = rs[4][0], rs[1][0], {r['busy_class']: r for r in rs[2]}
        red = [r for r in rs[0] if r['interv'] == '1']
        check('v6_dl_redispatched_window_is_in_the_rows_not_in_the_verdict',
              (i['busy_other'], len(rs[0]), len(red), red[0]['first_shot_any_ms'], bc['busy_other']['windows'], bc['busy_other']['max_ms'])
              == ('4', 4, 1, '6030', '4', '6030'), (i, red, bc))
        med = '%d' % round(float(v['median_ms'])) if v['median_ms'] else v['median_ms']       # '' when no row feeds it: a FAIL, not a crash
        check('v6_dl_rlag_excludes_redispatched_n_busy', (v['n_busy'], v['redispatched_not_counted'], v['busy_same_not_counted'],
                                                          v['gt_2s'], med, v['p90_ms']) == ('3', '1', '0', '0', '700', '800'), v)
        check('v6_dl_rlag_excludes_redispatched_verdict', v['r_lag'] == 'R-LAG SKIP C2' and v['r_lag_wire'] == 'R-LAG SKIP C2', v)
        rc, rs = q('dispatch_lag', '--run', '20990521')
        v = rs[4][0]
        check('v6_dl_rlag_wire_excludes_redispatched_too', (v['n_busy'], v['redispatched_not_counted'], v['r_lag'], v['r_lag_wire']) ==
              ('3', '1', 'R-LAG SKIP C2', 'R-LAG SKIP C2'), v)
        rc, rs = q('dispatch_lag', '--run', '20990522')
        v = rs[4][0]
        check('v6_dl_rlag_redispatched_does_not_make_up_n3', (v['n_busy'], v['redispatched_not_counted'], v['r_lag'], v['r_lag_wire']) ==
              ('2', '1', 'NO DATA (< 3 busy windows)', ''), v)
        # a run with no redispatched window counts 0 of them (and 0 no-dispatch windows)
        rc, rs = q('dispatch_lag', '--run', '20990510')
        check('v6_dl_no_redispatched_when_none', rs[4][0]['redispatched_not_counted'] == '0'
              and rs[4][0]['no_dispatch_not_counted'] == '0', rs[4][0])
        # a busy_other window with NO dispatch found (intervening_starts NULL, no release / tick / start) is shown in the rows and
        # counted in no_dispatch_not_counted, but is in neither basis of the R-LAG population: n_busy 3 (600 / 700 / 800), median 700,
        # SKIP on both. Were it in (the old COALESCE(intervening_starts, 0) = 0), n_busy 4 and the median 650 would read off a
        # window no dispatch explains.
        rc, rs = q('dispatch_lag', '--run', '20990524')
        v, i, bc = rs[4][0], rs[1][0], {r['busy_class']: r for r in rs[2]}
        nd = [r for r in rs[0] if r['busy_class'] == 'busy_other' and r['interv'] == '']
        check('v6_dl_nodispatch_window_is_in_the_rows_not_in_the_verdict',
              (i['busy_other'], len(rs[0]), len(nd), nd[0]['first_shot_any_ms'] if nd else None, nd[0]['release_ms'] if nd else None,
               nd[0]['tick_ms'] if nd else None, nd[0]['start_ms'] if nd else None, bc['busy_other']['windows'])
              == ('4', 4, 1, '20', '', '', '', '4'), (i, nd, bc))
        med = '%d' % round(float(v['median_ms'])) if v['median_ms'] else v['median_ms']
        check('v6_dl_rlag_excludes_nodispatch_n_busy', (v['n_busy'], v['no_dispatch_not_counted'], v['redispatched_not_counted'],
                                                        v['busy_same_not_counted'], v['gt_2s'], med, v['p90_ms']) ==
              ('3', '1', '0', '0', '0', '700', '800'), v)
        check('v6_dl_rlag_excludes_nodispatch_verdict_both_bases', v['r_lag'] == 'R-LAG SKIP C2' and v['r_lag_wire'] == 'R-LAG SKIP C2', v)
        s4 = rs[3][0]
        check('v6_dl_split_leaves_out_the_nodispatch_window', (s4['windows'], s4['total_ms'], s4['redispatched']) == ('3', '2100', '0'), s4)
        # the other not-counted classes leave the new counter at 0
        for run_, why in (('20990504', 'busy_same'), ('20990520', 'redispatched')):
            rc, rs = q('dispatch_lag', '--run', run_)
            check('v6_dl_nodispatch_counter_zero_for_' + why, rs[4][0]['no_dispatch_not_counted'] == '0', rs[4][0])
        # disp_last is bounded by the window's first shot: in a multi-window run the LATER windows' lock starts (and a lock start
        # after the first shot in the same window) must not become this window's dispatch. release + tick + start per window,
        # and the summed split of statement 4 (run 20990510 = lags 1600 / 1700 / 1800, release 300, start 30, tick = lag - 330)
        rc, rs = q('dispatch_lag', '--run', '20990510')
        split = [(r['busy_class'], r['release_ms'], r['tick_ms'], r['start_ms'], r['interv'], r['first_shot_any_ms'], r['ord_ok'])
                 for r in rs[0]]
        check('v6_dl_dispatch_bounded_by_first_shot_per_window', split ==
              [('busy_other', '300', '1270', '30', '0', '1600', '1'), ('busy_other', '300', '1370', '30', '0', '1700', '1'),
               ('busy_other', '300', '1470', '30', '0', '1800', '1')], split)
        s4 = rs[3][0]
        check('v6_dl_split_statement_multi_window', (s4['windows'], s4['release_ms'], s4['tick_ms'], s4['start_ms'], s4['total_ms'],
                                                     s4['redispatched']) == ('3', '900', '4110', '90', '5100', '0'), s4)
        rc, rs = q('dispatch_lag', '--run', '20990523')
        w = rs[0][0]
        check('v6_dl_lock_start_after_first_shot_is_not_the_dispatch',
              (w['busy_class'], w['release_ms'], w['tick_ms'], w['start_ms'], w['interv'], w['first_shot_any_ms'], w['ord_ok']) ==
              ('busy_other', '300', '1500', '30', '0', '1830', '1'), w)
        # the wire basis is a separate column: a night within one wire gap of a rule line says so
        rc, rs = q('dispatch_lag', '--run', '20990518')
        v = rs[4][0]
        check('v6_dl_no_wire_rows_both_bases_agree', v['r_lag'].startswith('R-LAG INCONCLUSIVE')
              and v['r_lag_wire'] == v['r_lag'], v)
        rc, rs = q('dispatch_lag', '--run', '20990519')
        v = rs[4][0]
        check('v6_dl_wire_basis_flips_verdict', v['r_lag'].startswith('R-LAG INCONCLUSIVE') and v['r_lag_wire'].startswith('R-LAG BUILD')
              and rs[0][0]['first_wire_any_ms'] == '1510', (v, rs[0][0]))
        # busy_same and idle windows are NOT in the R-LAG population
        rc, rs = q('dispatch_lag', '--run', '20990504')
        v = rs[4][0]
        check('v6_dl_population_excludes_busy_same', (v['n_busy'], v['busy_same_not_counted']) == ('0', '1')
              and v['r_lag'].startswith('NO DATA (< 3 busy'), v)
        rc, rs = q('dispatch_lag')
        check('v6_dl_pooled_scope', rs[4][0]['scope'] == 'POOLED over %d runs: pass --run for one night' % len(runs), rs[4][0])
        rc, rs = q('dispatch_lag', '--run', '20990501')
        check('v6_dl_one_run_scope', rs[4][0]['scope'] == 'one run', rs[4][0])
        # statement 0 is a TEMP view: the store itself gains nothing
        con = sqlite3.connect(str(db))
        check('v6_dl_leaves_no_view_in_the_store', con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'view'").fetchone()[0] == 0)
        con.close()


def _reflip_run(reflips, later, extra=None):
    """TC opens at T; a 3-account race fires a limited first volley; `reflips` = [offset ms] of new_window = 0 flips;
    `later` = [(ident, offset ms, status, key)] later shots (is_first = 0), fired in time order."""
    lines = [POOL, flip(TC, 1, T, 1)]
    lines += [flip(TC, 2 + i, T + off, 0) for i, off in enumerate(reflips)]
    lines += extra or []
    lines += [race(TC, ['primary', 'business', 'alt-1'])]
    lines += shot('primary', TC, T + 40) + shot('business', TC, T + 50) + shot('alt-1', TC, T + 60)
    for ident, off, st, key in sorted(later, key=lambda x: x[1]):
        lines += shot(ident, TC, T + off, status=st, key=key)
    return lines


def _many_reflips(n_re, re_pass, other_pass, n_other=None):
    """n_re re-flips 10 s apart; after each, a later shot 500 ms on (the first `re_pass` are 401s = past the limiter) and an
    'other' later shot 5 s on (the first `other_pass` of them 401s)."""
    n_other = n_re if n_other is None else n_other
    ids = ['primary', 'business', 'alt-1']
    re_off = [20000 + k * 10000 for k in range(n_re)]
    later = []
    for k in range(n_re):
        later.append((ids[k % 3], re_off[k] + 500, 401 if k < re_pass else 429, '-' if k < re_pass else A2C))
    for k in range(n_other):
        later.append((ids[(k + 1) % 3], 20000 + k * 10000 + 5000, 401 if k < other_pass else 429, '-' if k < other_pass else A2C))
    return _reflip_run(re_off, later)


def test_v6_reflip_query():
    cap = L(T + 20100, f"[STOCK][FLIP] tcin={TC}: 50 flips logged this run", name='src.monitoring.stock_check_resilient')
    runs = {
        # the detail run: re-flips at +20000, +20800, +30000
        'run_20990601_000000': _reflip_run([20000, 20800, 30000], [
            ('primary', 19990, 429, A2C),       # before the first re-flip                 -> other (limited)
            ('primary', 20500, 429, A2C),       # 500 ms after                              -> after (limited)
            ('business', 20900, 429, FSK),      # inside BOTH 2 s windows: counted ONCE     -> after (past: admitted)
            ('alt-1', 21990, 429, A2C),         # 1,990 ms after the first                  -> after (limited)
            ('alt-1', 22850, 429, A2C),         # outside both                              -> other (limited)
            ('primary', 30010, 401, '-'),       # after the third                           -> after (past)
            ('business', 30500, 503, '-'),      # an 'other' answer                         -> after (unclassified)
            ('business', 40000, 429, A2C),      #                                           -> other (limited)
            ('business', 50000, 401, '-')]),    #                                           -> other (past)
        # basis: t0 = +19,980 (before the re-flip) but the POST left at +20,060 (after it)
        'run_20990602_000000': _reflip_run([20000], [('primary', 19980, 429, A2C)],
                                           extra=[net('primary', 'POST', 429, T + 20060)]),
        # basis: t0 = +21,950 (inside) but the POST left at +22,030 (outside)
        'run_20990603_000000': _reflip_run([20000], [('business', 21950, 429, A2C)],
                                           extra=[net('business', 'POST', 429, T + 22030)]),
        # the flip-log cap: nothing after the last logged flip (+20,000) can be called 'other' beyond 2 s
        'run_20990604_000000': _reflip_run([20000], [('primary', 20500, 429, A2C), ('primary', 21999, 429, A2C),
                                                     ('business', 25000, 429, A2C), ('alt-1', 26000, 429, A2C)], extra=[cap]),
        'run_20990605_000000': _many_reflips(70, 6, 0),       # 6/70 = 8.6 % vs 0 %            => MET
        'run_20990606_000000': _many_reflips(70, 4, 3),       # 5.7 % vs 4.3 %                 => NOT MET (rate)
        'run_20990607_000000': _many_reflips(60, 0, 0),       # n = 60 but no pass at all      => NOT MET (rate): 0 >= 0 is vacuous
        'run_20990608_000000': _many_reflips(59, 6, 0),       # n = 59                         => NOT MET (n < 60)
        'run_20990609_000000': _many_reflips(60, 6, 0),       # n = 60 exactly                 => MET
        'run_20990610_000000': _reflip_run([20000], [('primary', 30000, 429, A2C)]),          # no later shot after the re-flip
        'run_20990611_000000': [POOL, race(TC, ['primary'])] + shot('primary', TC, T + 40) + shot('primary', TC, T + 5000),   # no flip log
        'run_20990612_000000': _many_reflips(61, 6, 0, n_other=0),     # nothing to compare with     => NO DATA
        # a later shot (is_first = 0, a racer already running) within 2 s after a new_window = 1 flip is NOT after a re-flip
        'run_20990613_000000': _same_run(),
        # ... and the same run with one real re-flip (new_window = 0) at +30 s and a later shot 400 ms after it: only that one counts
        'run_20990614_000000': _same_run() + [flip(TC, 3, T + 30000, 0)] + shot('primary', TC, T + 30400),
    }
    with tempfile.TemporaryDirectory() as tmp:
        db, out = _build_store(tmp, runs)
        q = _q_for(db)
        check('v6_rf_db_built', '%d file(s) parsed' % len(runs) in out, out)
        rc, rs = q('reflip', '--run', '20990601')
        r, v = rs[0][0], rs[1][0]
        check('v6_rf_detail_counts', (r['reflips'], r['after_reflip'], r['after_reflip_t0'], r['re_past'], r['re_limited'],
                                      r['re_unclass'], r['re_past_pct'], r['other'], r['ot_past'], r['ot_limited'], r['ot_unclass'],
                                      r['ot_past_pct'], r['capped'], r['first_shot_unknown']) ==
              ('3', '5', '5', '2', '2', '1', '50.0', '4', '1', '3', '0', '25.0', '0', '0'), r)
        check('v6_rf_detail_verdict_is_n_under_60', v['scope'] == 'one run' and v['later_after_reflip'] == '5'
              and v['reflip_pct'] == '50.0' and v['other_pct'] == '25.0' and v['r_reflip'].startswith('R-REFLIP NOT MET (n < 60)'), v)
        # wire vs atc_t0 basis: the same shot is in on one and out on the other
        rc, rs = q('reflip', '--run', '20990602')
        r = rs[0][0]
        check('v6_rf_wire_basis_in_t0_basis_out', (r['after_reflip'], r['after_reflip_t0'], r['other']) == ('1', '0', '0'), r)
        rc, rs = q('reflip', '--run', '20990603')
        r = rs[0][0]
        check('v6_rf_wire_basis_out_t0_basis_in', (r['after_reflip'], r['after_reflip_t0'], r['other']) == ('0', '1', '1'), r)
        # the cap: the +25 s / +26 s shots are neither after a re-flip nor 'other'
        rc, rs = q('reflip', '--run', '20990604')
        r = rs[0][0]
        check('v6_rf_capped_window_not_folded_into_other', (r['after_reflip'], r['other'], r['capped']) == ('2', '0', '2'), r)
        # a flip that OPENS a window (new_window = 1) is not a re-flip: the busy_same shot (primary's racer from the earlier window
        # re-firing 757 ms into window 2, is_first = 0) sits inside 2 s of a flip row of its own TCIN, on both bases
        rc, rs = q('reflip', '--run', '20990613')
        r = rs[0][0]
        check('v6_rf_new_window_flip_is_not_a_reflip', (r['reflips'], r['after_reflip'], r['after_reflip_t0'], r['other'],
                                                        r['ot_limited'], r['capped']) == ('0', '0', '0', '1', '1', '0'), r)
        rc, rs = q('reflip', '--run', '20990614')
        r = rs[0][0]
        check('v6_rf_only_the_real_reflip_counts', (r['reflips'], r['after_reflip'], r['after_reflip_t0'], r['other'], r['re_limited'],
                                                    r['ot_limited'], r['capped']) == ('1', '1', '1', '1', '1', '1', '0'), r)
        # verdicts
        want = {'20990605': ('70', '8.6', '0.0', 'R-REFLIP MET: design FLIP-WAKE (a design, not a build)'),
                '20990606': ('70', '5.7', '4.3', 'R-REFLIP NOT MET (rate): drop FLIP-WAKE'),
                '20990607': ('60', '0.0', '0.0', 'R-REFLIP NOT MET (rate): drop FLIP-WAKE'),
                '20990608': ('59', '10.2', '0.0', 'R-REFLIP NOT MET (n < 60)'),
                '20990609': ('60', '10.0', '0.0', 'R-REFLIP MET: design FLIP-WAKE (a design, not a build)'),
                '20990610': ('0', '', '0.0', 'NO DATA (no later shot within 2 s after a re-flip)'),
                '20990611': ('', '', '', 'NO DATA (no flip log in the selected runs)'),
                '20990612': ('61', '9.8', '', 'NO DATA (no classified other later shot to compare with)')}
        got = {}
        for run in want:
            rc, rs = q('reflip', '--run', run)
            v = rs[1][0]
            got[run] = (v['later_after_reflip'], v['reflip_pct'], v['other_pct'], v['r_reflip'])
        bad = {k: (got[k], want[k]) for k in want if not (got[k][:3] == want[k][:3] and got[k][3].startswith(want[k][3]))}
        check('v6_rf_verdict_table', not bad, bad)
        rc, rs = q('reflip')
        check('v6_rf_pooled_scope', rs[1][0]['scope'].startswith('POOLED over '), rs[1][0])
        # a run with no flip log has no statement-1 row (it is not folded into a zero)
        rc, rs = q('reflip', '--run', '20990611')
        check('v6_rf_no_flip_log_run_has_no_row', rs[0] == [], rs[0])
        con = sqlite3.connect(str(db))
        check('v6_rf_leaves_no_view_in_the_store', con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'view'").fetchone()[0] == 0)
        con.close()


# ---------------------------------------------------------------------------------
# v7 (2026-10-16, INS-LIMKEY-1016): limkey + warm_dummy tables, decoy exclusion, limkey.sql (rule P1)
# ---------------------------------------------------------------------------------
LK_NAME = 'src.session.purchase_executor'
LK_STOP_TAIL = (" -- a HOT-TCIN LINE WAS ADDED to this account's cart; probing is OFF for the rest of this run; "
                "audit the cart and delete the line before the next drop")


def lk_res(acct, tcin, status, key='ERR_A2C_TCIN_RATE_LIMITED', hop='-', line='home', bl=0, n=1, err=''):
    """limkey_result_line() (purchase_executor.py): acct= tcin= status= err_key= hop= line= body_len= n= [err=]."""
    m = (f"[LIMKEY_PROBE] acct={acct} tcin={tcin} status={status} err_key={key or '-'} hop={hop or '-'} "
         f"line={line} body_len={bl} n={n}")
    return m + (f" err={err}" if err else '')


def lk(ep_ms, msg):
    """_limkey_log(): print() first, then logger.info() -- the same text twice."""
    return [msg, L(ep_ms, msg, name=LK_NAME)]


def wdummy(idx, acct, status):
    return f"[WARMUP#{idx}/{acct}] dummy POST status {status} — write-auth alive"


def test_v7_limkey_rows():
    base = [POOL, flip(TC, 1, T, 1)]
    lines = base + lk(T + 1000, lk_res('primary', TC, 429, n=1))
    lines += lk(T + 61000, lk_res('Alt-1', TC2, 424, key='ITEM_NOT_READY_FOR_LAUNCH,NOT_FOUND', hop='1', line='egress', bl=95, n=2))
    lines += lk(T + 121000, lk_res('primary', TC, -1, key='?', err='TimeoutError', bl=-1, n=3))
    lines += lk(T + 181000, lk_res('business', TC3, 0, key='-', line='proxy', bl=-1, n=4))
    res = parse(lines)
    lkr = res['limkey']
    check('v7_four_probes_four_rows', len(lkr) == 4 and {r['event'] for r in lkr} == {'probe'}, lkr)
    check('v7_probe_fields', (lkr[0]['ident'], lkr[0]['tcin'], lkr[0]['n'], lkr[0]['status'], lkr[0]['err_key'], lkr[0]['hop'],
                              lkr[0]['net_line'], lkr[0]['body_len'], lkr[0]['err'], lkr[0]['ts_ms'], lkr[0]['ts_src']) ==
          ('primary', TC, 1, 429, 'ERR_A2C_TCIN_RATE_LIMITED', '-', 'home', 0, None, T + 1000, 'logger'), lkr[0])
    check('v7_key_with_comma_and_hop_and_egress_line', (lkr[1]['ident'], lkr[1]['err_key'], lkr[1]['hop'], lkr[1]['net_line'],
                                                         lkr[1]['body_len'], lkr[1]['status']) ==
          ('alt-1', 'ITEM_NOT_READY_FOR_LAUNCH,NOT_FOUND', '1', 'egress', 95, 424), lkr[1])
    check('v7_evaluate_error_row', (lkr[2]['status'], lkr[2]['err'], lkr[2]['err_key'], lkr[2]['body_len']) == (-1, 'TimeoutError', '?', -1), lkr[2])
    check('v7_status0_row_kept_as_printed', (lkr[3]['status'], lkr[3]['net_line'], lkr[3]['tcin']) == (0, 'proxy', TC3), lkr[3])
    check('v7_log_line_is_the_logger_copys', [r['line'] for r in lkr] == [4, 6, 8, 10], [r['line'] for r in lkr])
    check('v7_family_accounting_clean', _unp(res).get('LIMKEY') == (8, 8, 0) and not [m for m in _unp(res) if m.endswith('@wrong_form')],
          _unp(res))
    check('v7_twins_collapsed_counted', check_of(res, 'limkey_twins_collapsed') == '4', check_of(res, 'limkey_twins_collapsed'))
    check('v7_columns', set(lkr[0]) == {'line', 'ts_ms', 'ts_src', 'event', 'ident', 'tcin', 'n', 'status', 'err_key', 'hop',
                                         'net_line', 'body_len', 'reason', 'err', 'detail'}, set(lkr[0]))
    # an unreadable result line keeps no row and is counted, never guessed
    res = parse(base + lk(T + 1000, "[LIMKEY_PROBE] acct=primary tcin=oops status=429"))
    check('v7_unreadable_limkey_counted_not_guessed', res['limkey'] == [] and _unp(res).get('LIMKEY') == (2, 0, 2)
          and next(u['first_line'] for u in res['unparsed'] if u['marker'] == 'LIMKEY') == 3, (res['limkey'], _unp(res)))


def test_v7_limkey_events():
    base = [POOL, flip(TC, 1, T, 1)]
    stop = f"[LIMKEY_PROBE] event=STOP reason=probe_2xx acct=primary tcin={TC} status=201{LK_STOP_TAIL}"
    err = "[LIMKEY_PROBE] event=error ValueError: bad probe tcin 'x' status=9 (#1; the heartbeat is unaffected)"
    # the cleanup / reclassify lines are the bot lane's (LIMKEY-GUARD-1016, feat_limkey_guard_1016 purchase_executor.py f-strings, copied verbatim in shape)
    clean = f"[LIMKEY_PROBE] event=cleanup step=delete ok=True lines=1 proven_gone=True acct=primary tcin={TC}"
    cerr = (f"[LIMKEY_PROBE] event=cleanup step=error ValueError: bad reply status=502 tcin=1234567 reason=oops acct=primary tcin={TC} "
            f"(marker kept if it was written)")
    cmark = (f"[LIMKEY_PROBE] event=cleanup step=marker ok=False ALERT reason=probe_2xx late=False acct=primary tcin={TC} "
             f"-- state/limkey_markers.json could NOT be written; status=500 acct=other tcin=7654321")
    recl = (f"[LIMKEY_PROBE] event=reclassify acct=primary tcin={TC} page_status=0 seen_status=429 -- classified on the status the "
            f"interceptor captured")
    lines = base + lk(T + 1000, "[LIMKEY_PROBE] event=skip reason=in_stock_recent acct=primary")
    lines += lk(T + 2000, "[LIMKEY_PROBE] event=skip reason=in_stock_recent acct=primary")
    lines += lk(T + 3000, stop) + lk(T + 4000, err) + lk(T + 5000, clean) + lk(T + 6000, "[LIMKEY_PROBE] event=Future_Kind x=1")
    lines += lk(T + 7000, cerr) + lk(T + 8000, cmark) + lk(T + 9000, recl)
    res = parse(lines)
    lkr = res['limkey']
    check('v7_event_kinds_in_log_order', [r['event'] for r in lkr] == ['skip', 'skip', 'stop', 'error', 'cleanup', 'future_kind', 'cleanup', 'cleanup', 'reclassify'],
          [r['event'] for r in lkr])
    check('v7_skip_fields', (lkr[0]['reason'], lkr[0]['ident'], lkr[0]['tcin'], lkr[0]['status'], lkr[0]['n']) ==
          ('in_stock_recent', 'primary', None, None, None), lkr[0])
    check('v7_identical_skips_stay_two_rows', lkr[0]['ts_ms'] == T + 1000 and lkr[1]['ts_ms'] == T + 2000, (lkr[0], lkr[1]))
    check('v7_stop_fields_text_after_dashes_not_mined', (lkr[2]['reason'], lkr[2]['ident'], lkr[2]['tcin'], lkr[2]['status']) ==
          ('probe_2xx', 'primary', TC, 201) and lkr[2]['detail'].startswith('reason=probe_2xx'), lkr[2])
    check('v7_error_tail_is_free_text_not_mined', (lkr[3]['status'], lkr[3]['ident'], lkr[3]['tcin']) == (None, None, None)
          and lkr[3]['detail'].startswith('ValueError: bad probe tcin'), lkr[3])
    check('v7_cleanup_read_generically', (lkr[4]['reason'], lkr[4]['ident'], lkr[4]['tcin'], lkr[4]['status']) ==
          (None, 'primary', TC, None), lkr[4])
    # cleanup step=error: the exception text is free text -- its status= / tcin= / reason= are never mined, only the bot's trailing acct/tcin tag
    check('v7_cleanup_step_error_text_not_mined', (lkr[6]['reason'], lkr[6]['ident'], lkr[6]['tcin'], lkr[6]['status']) ==
          (None, 'primary', TC, None) and lkr[6]['detail'].startswith('step=error ValueError'), lkr[6])
    # a cleanup ALERT line: the text after ' -- ' is not mined; reason= before it is
    check('v7_cleanup_alert_tail_not_mined', (lkr[7]['reason'], lkr[7]['ident'], lkr[7]['tcin'], lkr[7]['status']) ==
          ('probe_2xx', 'primary', TC, None), lkr[7])
    # reclassify: page_status= / seen_status= are not the plain status= key
    check('v7_reclassify_fields', (lkr[8]['event'], lkr[8]['ident'], lkr[8]['tcin'], lkr[8]['status']) == ('reclassify', 'primary', TC, None), lkr[8])
    check('v7_unknown_event_name_kept', lkr[5]['event'] == 'future_kind', lkr[5])
    check('v7_events_family_clean', _unp(res).get('LIMKEY') == (18, 18, 0), _unp(res))


def test_v7_limkey_dedupe():
    base = [POOL, flip(TC, 1, T, 1)]
    # print twin first, then the logger twin: one row, the logger's own stamp
    res = parse(base + lk(T + 1000, lk_res('primary', TC, 429, n=7)))
    check('v7_twins_one_row_logger_stamp', len(res['limkey']) == 1 and res['limkey'][0]['ts_src'] == 'logger'
          and res['limkey'][0]['ts_ms'] == T + 1000, res['limkey'])
    # print-only (no logger copy): one row, the last logger stamp before it, ts_src logger_prior
    res = parse(base + [L(T + 500, '[STOCK] other line', name='x.y'), lk_res('primary', TC, 429, n=7)])
    r = res['limkey']
    check('v7_print_only_row_uses_prior_stamp', len(r) == 1 and r[0]['ts_src'] == 'logger_prior' and r[0]['ts_ms'] == T + 500, r)
    # logger-only
    res = parse(base + [L(T + 800, lk_res('primary', TC, 429, n=7), name=LK_NAME)])
    check('v7_logger_only_row', len(res['limkey']) == 1 and res['limkey'][0]['ts_src'] == 'logger', res['limkey'])
    # same n and same text from two real probes (two twin pairs) stay two rows; a different text with the same n is its own row
    lines = base + lk(T + 1000, lk_res('primary', TC, 429, n=9)) + lk(T + 2000, lk_res('primary', TC, 429, n=9))
    lines += lk(T + 3000, lk_res('business', TC, 429, n=9))
    res = parse(lines)
    check('v7_same_text_twice_two_rows_other_acct_own_row', [(r['ident'], r['ts_ms']) for r in res['limkey']] ==
          [('primary', T + 1000), ('primary', T + 2000), ('business', T + 3000)]
          and check_of(res, 'limkey_twins_collapsed') == '3', (res['limkey'], check_of(res, 'limkey_twins_collapsed')))
    # print text cut by a glued logger stamp (a print writes its text and its newline in two writes), and glued behind a print
    glued = lk_res('primary', TC, 429, n=5) + L(T + 400, '[STOCK] other', name='x.y')
    behind = '[PURCHASE] something printed' + lk_res('primary', TC2, 424, key='-', n=6)
    res = parse(base + [glued, L(T + 410, lk_res('primary', TC, 429, n=5), name=LK_NAME), behind,
                        L(T + 420, lk_res('primary', TC2, 424, key='-', n=6), name=LK_NAME)])
    check('v7_glued_lines_one_row_each', [(r['n'], r['tcin'], r['ts_src']) for r in res['limkey']] ==
          [(5, TC, 'logger'), (6, TC2, 'logger')] and _unp(res).get('LIMKEY') == (4, 4, 0), (res['limkey'], _unp(res)))
    # 40 probes with twins = 40 rows (the count a readout divides by)
    res = parse(base + sum([lk(T + 1000 * (i + 1), lk_res('primary', TC, 429, n=i + 1)) for i in range(40)], []))
    check('v7_forty_probes_forty_rows', len(res['limkey']) == 40 and check_of(res, 'limkey_twins_collapsed') == '40', len(res['limkey']))


def test_v7_warm_dummy():
    base = [POOL, flip(TC, 1, T, 1)]
    lines = base + [L(T + 100, '[STOCK] x', name='a.b'), wdummy(1, 'primary', 424), wdummy(0, 'Alt-1', 401), wdummy(2, 'business', 429),
                    f"[WARMUP#1/primary] dummy POST ADDED its line (status 201) — a won-cart po_only ticket re-checks the cart first (dummy_add)",
                    wdummy(1, 'primary', 201), "[WARMUP#1] Firing dummy POST to trigger Shape header capture..."]
    res = parse(lines)
    wd = res['warm_dummy']
    check('v7_warm_dummy_rows', [(r['ident'], r['idx'], r['status']) for r in wd] ==
          [('primary', 1, 424), ('alt-1', 0, 401), ('business', 2, 429), ('primary', 1, 201)], wd)
    check('v7_warm_dummy_ts_is_prior_stamp', {(r['ts_ms'], r['ts_src']) for r in wd} == {(T + 100, 'logger_prior')}, wd)
    check('v7_warm_dummy_family_clean_and_added_line_not_read', _unp(res).get('WDUMMY') == (4, 4, 0), _unp(res))
    res = parse(base + [L(T + 100, wdummy(1, 'primary', 424), name=LK_NAME)])
    check('v7_warm_dummy_logger_form_counted_wrong_form', res['warm_dummy'] == [] and _unp(res).get('WDUMMY@wrong_form') == (1, 0, 1), _unp(res))


def _warm_decoy(ep_ms, status, key, tag=False):
    post = "[INTERCEPTOR:warmup]  POST https://carts.target.com/web_checkouts/v1/cart_items?field_groups=CART%2CCART_IT"
    cap = "[INTERCEPTOR:warmup] Captured 16 headers (Shape tokens: 6, prev cache had 0): ['Accept', 'Content-Type']"
    m = resp_msg(status, key, 'warmup') + (' limkey_probe=1' if tag else '')
    return [post, cap, f"[INTERCEPTOR:warmup] {m}", L(ep_ms, m)]


def test_v7_decoy_exclusion():
    base = [POOL, flip(TC, 1, T, 1)]
    plain = _warm_decoy(T + 1000, 424, 'ITEM_NOT_READY_FOR_LAUNCH,NOT_FOUND') + _warm_decoy(T + 9000, 401, '-')
    with_probe = (_warm_decoy(T + 1000, 424, 'ITEM_NOT_READY_FOR_LAUNCH,NOT_FOUND') +
                  _warm_decoy(T + 5000, 429, 'ERR_A2C_TCIN_RATE_LIMITED', tag=True) + _warm_decoy(T + 9000, 401, '-'))
    a, b = parse(base + plain), parse(base + with_probe)
    sig = lambda r: [(d['status'], d['key'], d['shape_tokens'], d['pair_q'], d['selftest']) for d in r['decoys']]
    check('v7_probe_response_is_not_a_decoy_row', len(a['decoys']) == 2 and sig(a) == sig(b), (sig(a), sig(b)))
    check('v7_exclusion_counted_in_checks', check_of(b, 'limkey_probe_resps_not_decoys') == '1'
          and check_of(a, 'limkey_probe_resps_not_decoys') is None, check_of(b, 'limkey_probe_resps_not_decoys'))
    check('v7_untagged_429_decoy_stays', [d['status'] for d in parse(base + _warm_decoy(T + 5000, 429, 'ERR_A2C_TCIN_RATE_LIMITED'))['decoys']] == [429])
    # the HDRS twin carries limkey_probe=1 as one more '| name=value' pair: the HDRS parser is untouched
    hd = "[ATC_RESP_HDRS] tab=warmup status=401 n=3 | a=b | limkey_probe=1"
    res = parse(base + [L(T + 100, hd)])
    check('v7_hdrs_tag_does_not_break_hdrs_parse', _unp(res).get('HDRS') == (1, 1, 0), _unp(res))


def test_v7_other_tables_unchanged():
    """limkey / warm_dummy lines add rows to those two tables and to nothing else."""
    core = [POOL, flip(TC, 1, T, 1), race(TC, ['primary', 'business'])]
    core += shot('primary', TC, T + 47) + shot('business', TC, T + 50, status=429, key=FSK)
    core += shot('primary', TC, T + 60000, status=401, key='-')
    core += _warm_decoy(T + 70000, 424, 'ITEM_NOT_READY_FOR_LAUNCH,NOT_FOUND')
    extra = (lk(T + 1000, lk_res('primary', TC2, 429, n=1)) + lk(T + 61000, "[LIMKEY_PROBE] event=skip reason=gap acct=primary") +
             [wdummy(1, 'primary', 424), wdummy(2, 'business', 424)])
    with_, without = parse(core + extra), parse(core)
    tabs = ('shots', 'races', 'flips', 'windows', 'tickets', 'loop_ends', 'decoys', 'monitor_stats', 'orders', 'idents',
            'place_orders', 'atc_net', 'gate_events', 'stock_status', 'sellable_oos', 'shadow206', 'harvest', 'lock_events')
    bad = [t for t in tabs if with_[t] != without[t]]
    check('v7_every_other_table_identical', not bad, bad)
    ck = lambda r: [c for c in r['checks'] if not c['name'].startswith('limkey_')]
    check('v7_checks_identical_but_the_limkey_counter', ck(with_) == ck(without) and check_of(with_, 'limkey_twins_collapsed') == '2',
          (ck(with_), ck(without)))
    ua = {r['marker']: (r['n'], r['first_line']) for r in with_['unparsed'] if r['marker'] not in ('LIMKEY', 'WDUMMY')}
    ub = {r['marker']: (r['n'], r['first_line']) for r in without['unparsed']}
    check('v7_unparsed_same_markers_and_remainders', ua == ub, (ua, ub))
    check('v7_exactly_two_new_unparsed_rows_n0', len(with_['unparsed']) == len(without['unparsed']) + 2
          and _unp(with_).get('LIMKEY') == (4, 4, 0) and _unp(with_).get('WDUMMY') == (2, 2, 0), _unp(with_))
    check('v7_new_tables_empty_without_lines', without['limkey'] == [] and without['warm_dummy'] == [], (without['limkey'], without['warm_dummy']))
    check('v7_parser_version', ev.PARSER_VERSION >= 7, ev.PARSER_VERSION)


def test_v7_real_format_drift():
    """The lines above are built from copies of the format; build the same lines with the REAL limkey_result_line() and parse
    them, so a change to the bot's format cannot silently orphan this ingest. Skipped (printed, not counted) when the executor
    cannot be imported in this process."""
    try:
        os.environ.setdefault('TARGET_API_CAPTURE_CHECKOUT_STEPS', 'false')
        os.environ.setdefault('TARGET_API_CAPTURE_PLACE_ORDER', 'false')
        sys.path.insert(0, str(ROOT))
        import src.session.purchase_executor as pe
        fn = pe.limkey_result_line
    except Exception as e:                              # noqa: BLE001
        print(f"  skip v7_real_format_drift (executor not importable here: {type(e).__name__})")
        return
    msgs = [fn('primary', TC, {'status': 429, 'n': 0}, 'ERR_A2C_TCIN_RATE_LIMITED', '', 'home', 12),
            fn('alt-1', TC2, {'status': -1, 'n': -1}, '?', '', 'egress', 13, 'TimeoutError'),
            fn('business', TC3, {'status': 401, 'n': 88}, '', '1', 'proxy', 14)]
    res = parse([POOL, flip(TC, 1, T, 1)] + sum([lk(T + 1000 * (i + 1), m) for i, m in enumerate(msgs)], []))
    r = res['limkey']
    check('v7_real_limkey_result_line_parses', [(x['ident'], x['tcin'], x['status'], x['err_key'], x['hop'], x['net_line'], x['body_len'], x['n'], x['err'])
                                                for x in r] ==
          [('primary', TC, 429, 'ERR_A2C_TCIN_RATE_LIMITED', '-', 'home', 0, 12, None),
           ('alt-1', TC2, -1, '?', '-', 'egress', -1, 13, 'TimeoutError'),
           ('business', TC3, 401, '-', '1', 'proxy', 88, 14, None)] and _unp(res).get('LIMKEY') == (6, 6, 0), (r, _unp(res)))


def test_v7_store_and_cli():
    with tempfile.TemporaryDirectory() as tmp:
        logs = Path(tmp) / 'runs'
        logs.mkdir()
        lines = [POOL, flip(TC, 1, T, 1)] + lk(T + 1000, lk_res('primary', TC, 429, n=1)) + lk(T + 61000, lk_res('primary', TC, 424, key='-', n=2))
        lines += lk(T + 62000, "[LIMKEY_PROBE] event=skip reason=gap acct=primary") + [wdummy(1, 'primary', 424), wdummy(2, 'business', 424)]
        (logs / 'run_20990701_000000.log').write_bytes(('\r\n'.join(lines) + '\r\n').encode('utf-8'))
        db = Path(tmp) / 'ev.sqlite'
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ev.main(['--db', str(db), '--logs', str(logs)])
        check('v7_summary_names_limkey_and_warm_dummy', "limkey={'probe': 2, 'skip': 1}" in buf.getvalue() and 'warm_dummy=2' in buf.getvalue(), buf.getvalue()[:500])
        check('v7_unparsed_remainder_empty', 'unparsed remainder: none' in buf.getvalue(), buf.getvalue()[-300:])
        con = sqlite3.connect(str(db))
        cols = [r[1] for r in con.execute('PRAGMA table_info(limkey)')]
        check('v7_limkey_table_columns', cols == ['run_id', 'line', 'ts_ms', 'ts_src', 'event', 'ident', 'tcin', 'n', 'status', 'err_key', 'hop',
                                                  'net_line', 'body_len', 'reason', 'err', 'detail'], cols)
        check('v7_warm_dummy_table_columns', [r[1] for r in con.execute('PRAGMA table_info(warm_dummy)')] ==
              ['run_id', 'line', 'ts_ms', 'ts_src', 'ident', 'idx', 'status'])
        check('v7_rows_stored', con.execute('SELECT event, COUNT(*) FROM limkey GROUP BY event ORDER BY event').fetchall() == [('probe', 2), ('skip', 1)]
              and con.execute('SELECT COUNT(*) FROM warm_dummy').fetchone()[0] == 2)
        check('v7_indexes_exist', con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('ix_limkey_run', 'ix_wdummy_run')").fetchone()[0] == 2)
        con.close()
        out2 = io.StringIO()
        with contextlib.redirect_stdout(out2):
            ev.main(['--db', str(db), '--logs', str(logs), '--quiet'])
        con = sqlite3.connect(str(db))
        check('v7_unchanged_file_skipped_no_duplicates', '0 file(s) parsed, 1 unchanged' in out2.getvalue()
              and con.execute('SELECT COUNT(*) FROM limkey').fetchone()[0] == 3, out2.getvalue())
        # a v6 store: no limkey / warm_dummy tables, the file ingested at parser_version 6 -> the bump re-parses it and the tables return
        con.execute('DROP TABLE limkey')
        con.execute('DROP TABLE warm_dummy')
        con.execute('UPDATE ingested SET parser_version = 6')
        con.commit()
        con.close()
        out3 = io.StringIO()
        with contextlib.redirect_stdout(out3):
            ev.main(['--db', str(db), '--logs', str(logs), '--quiet'])
        con = sqlite3.connect(str(db))
        check('v7_v6_store_reparsed_and_tables_recreated', '1 file(s) parsed' in out3.getvalue()
              and con.execute('SELECT COUNT(*) FROM limkey').fetchone()[0] == 3
              and con.execute('SELECT COUNT(*) FROM warm_dummy').fetchone()[0] == 2
              and con.execute('SELECT parser_version FROM ingested').fetchone()[0] == ev.PARSER_VERSION, out3.getvalue())
        con.execute('UPDATE ingested SET parser_version = parser_version - 1')
        con.commit()
        con.close()
        with contextlib.redirect_stdout(io.StringIO()):
            ev.main(['--db', str(db), '--logs', str(logs), '--quiet'])
        con = sqlite3.connect(str(db))
        check('v7_version_reparse_replaces_rows', con.execute('SELECT COUNT(*) FROM limkey').fetchone()[0] == 3
              and con.execute('SELECT COUNT(*) FROM warm_dummy').fetchone()[0] == 2)
        con.close()
        for t in ('limkey', 'warm_dummy'):
            qb = io.StringIO()
            with contextlib.redirect_stdout(qb):
                evq.main(['--db', str(db), '--csv', 'SELECT COUNT(*) AS n FROM %s' % t, '--run', 'no_such_run'])
            check('v7_q_run_filter_covers_' + t, qb.getvalue().split() == ['n', '0'], qb.getvalue())
        check('v7_q_run_tables_has_new_tables', all(t in evq.RUN_TABLES and t in ev.RUN_TABLES for t in ('limkey', 'warm_dummy')), evq.RUN_TABLES)


def _lk_run(probes, dummies=(), decoys=(), events=(), t0=0):
    """A synthetic run: `probes` = (acct, tcin, status, key[, body_len]) in order, one per second from T + t0; `dummies` = (acct, status);
    `decoys` = (status, key) warmup-tab responses; `events` = raw [LIMKEY_PROBE] event texts."""
    lines = [POOL, flip(TC, 1, T, 1)]
    for i, pr in enumerate(probes):
        acct, tcin, status, key = pr[:4]
        bl = pr[4] if len(pr) > 4 else (-1 if status <= 0 else 0)        # optional 5th element: body_len
        lines += lk(T + t0 + 1000 * (i + 1), lk_res(acct, tcin, status, key=key, n=i + 1, bl=bl,
                                                    err='TimeoutError' if status < 0 else ''))
    at = T + t0 + 1000 * (len(probes) + 1)
    for j, (acct, status) in enumerate(dummies):
        lines += [L(at + j, '[STOCK] tick', name='a.b'), wdummy(1, acct, status)]
    for j, (status, key) in enumerate(decoys):
        lines += _warm_decoy(at + 100 + j, status, key)
    for j, txt in enumerate(events):
        lines += lk(at + 200 + j, txt)
    return lines


def _n(tcin, status, key, count):
    return [('primary', tcin, status, key)] * count


def test_v7_limkey_query():
    A2, K424 = 'ERR_A2C_TCIN_RATE_LIMITED', 'ITEM_NOT_READY_FOR_LAUNCH,NOT_FOUND'
    clean = [('primary', 424), ('primary', 424), ('business', 424)]
    far = T + 3 * 3600 * 1000                       # three hours later: a different local hour
    runs = {
        # TESTBED: 2 hot TCINs x 30 ERR_A2C, a non-empty clean control
        'run_20990801_000000': _lk_run(_n(TC, 429, A2, 30) + _n(TC2, 429, A2, 30), clean),
        # DEMAND-SWITCHED: 30 answered POSTs per TCIN, none limited
        'run_20990802_000000': _lk_run(_n(TC, 424, K424, 30) + _n(TC2, 401, '-', 30), clean),
        # one TCIN at 29 answered POSTs -> INCONCLUSIVE (n)
        'run_20990803_000000': _lk_run(_n(TC, 429, A2, 30) + _n(TC2, 429, A2, 29), clean),
        # 14 / 30 = 46.7% on one TCIN -> INCONCLUSIVE (share), not TESTBED and not DEMAND-SWITCHED
        'run_20990804_000000': _lk_run(_n(TC, 429, A2, 14) + _n(TC, 424, K424, 16) + _n(TC2, 429, A2, 30), clean),
        # the decoy was limited too (status 429 heartbeat dummy POST) -> not a per-TCIN policy
        'run_20990805_000000': _lk_run(_n(TC, 429, A2, 30) + _n(TC2, 429, A2, 30), clean + [('primary', 429)]),
        # ... or a warmup-tab decoy with the ERR_A2C key
        'run_20990806_000000': _lk_run(_n(TC, 429, A2, 30) + _n(TC2, 429, A2, 30), clean, decoys=[(429, A2)]),
        # an empty control cannot support TESTBED
        'run_20990807_000000': _lk_run(_n(TC, 429, A2, 30) + _n(TC2, 429, A2, 30)),
        # status 0 (preflight) and -1 are never in the rate: 29 answered + 100 preflight is still 29 POSTs
        'run_20990808_000000': _lk_run(_n(TC, 429, A2, 29) + _n(TC, 0, '-', 100) + _n(TC2, 429, A2, 30) + _n(TC2, -1, '?', 5), clean),
        # ... and 30 answered + 100 preflight is TESTBED on the 30, share 100%
        'run_20990809_000000': _lk_run(_n(TC, 429, A2, 30) + _n(TC, 0, '-', 100) + _n(TC2, 429, A2, 30), clean),
        # no probe line at all
        'run_20990810_000000': [POOL, flip(TC, 1, T, 1)] + [wdummy(1, 'primary', 424)],
        # a 429 dummy in ANOTHER hour, another account's 429 -> not the control of these hours
        'run_20990811_000000': (_lk_run(_n(TC, 429, A2, 30) + _n(TC2, 429, A2, 30), clean) +
                                [L(far, '[STOCK] later', name='a.b'), wdummy(1, 'primary', 429)]),
        'run_20990812_000000': _lk_run(_n(TC, 429, A2, 30) + _n(TC2, 429, A2, 30), clean + [('alt-1', 429)]),
        # 2xx / unkeyed 429 / unreadable + STOP + skip events: the remainder and the events
        'run_20990813_000000': _lk_run([('primary', TC, 201, '-'), ('primary', TC, 429, '?'), ('primary', TC, 429, '-'), ('primary', TC, -1, '?'),
                                        ('primary', TC, 500, '-'), ('primary', TC, 401, '-'), ('primary', TC, 429, 'SOME_OTHER_KEY'),
                                        ('primary', TC, 429, 'FAST_SELLING', 120), ('primary', TC, 429, A2, 80), ('primary', TC, 401, 'SOME_KEY'),
                                        ('primary', TC, 429, A2, 0), ('primary', TC, 424, K424), ('primary', TC, 0, '-'),
                                        ('primary', TC, 429, A2, -1), ('primary', TC, 429, A2 + ',OTHER', 0)], clean,
                                       events=[f"[LIMKEY_PROBE] event=STOP reason=probe_2xx acct=primary tcin={TC} status=201{LK_STOP_TAIL}",
                                               "[LIMKEY_PROBE] event=skip reason=gap acct=primary"]),
        # fix 1: every answer is a 429 whose key was not read ('?' on one TCIN, '-' on the other), >= 30 each: unknown, never DEMAND-SWITCHED
        'run_20990814_000000': _lk_run(_n(TC, 429, '?', 30) + _n(TC2, 429, '-', 30), clean),
        # ... one unkeyed 429 among otherwise clean answers blocks DEMAND-SWITCHED too
        'run_20990815_000000': _lk_run(_n(TC, 424, K424, 30) + _n(TC2, 424, K424, 29) + _n(TC2, 429, '-', 1), clean),
        # fix 2: TCIN A 15 ERR_A2C + 15 x 424 + 40 unreadable = 15/70 = 21% (passes 50% only if the unreadable are left out), TCIN B clean 30 ERR_A2C
        'run_20990816_000000': _lk_run(_n(TC, 429, A2, 15) + _n(TC, 424, K424, 15) + _n(TC, -1, '?', 40) + _n(TC2, 429, A2, 30), clean),
        # ... and unreadable rows alone block DEMAND-SWITCHED (30 clean answers per TCIN + 2 timeouts)
        'run_20990817_000000': _lk_run(_n(TC, 424, K424, 30) + _n(TC2, 424, K424, 30) + _n(TC2, -1, '?', 2), clean),
        # ... while a few unreadable rows do not stop a clear testbed (30 ERR_A2C + 2 timeouts = 93.8%)
        'run_20990818_000000': _lk_run(_n(TC, 429, A2, 30) + _n(TC, -1, '?', 2) + _n(TC2, 429, A2, 30), clean),
    }
    with tempfile.TemporaryDirectory() as tmp:
        db, out = _build_store(tmp, runs)
        q = _q_for(db)
        check('v7_q_db_built', '%d file(s) parsed' % len(runs) in out, out)

        def verdict(run):
            rc, rs = q('limkey', '--run', run)
            return rc, rs

        want = {'20990801': 'P1 TESTBED EXISTS', '20990802': 'P1 DEMAND-SWITCHED', '20990803': 'P1 INCONCLUSIVE', '20990804': 'P1 INCONCLUSIVE',
                '20990805': 'P1 INCONCLUSIVE', '20990806': 'P1 INCONCLUSIVE', '20990807': 'P1 INCONCLUSIVE', '20990808': 'P1 INCONCLUSIVE',
                '20990809': 'P1 TESTBED EXISTS', '20990810': 'NO DATA', '20990811': 'P1 TESTBED EXISTS', '20990812': 'P1 TESTBED EXISTS',
                '20990813': 'P1 INCONCLUSIVE', '20990814': 'P1 INCONCLUSIVE', '20990815': 'P1 INCONCLUSIVE',
                '20990816': 'P1 INCONCLUSIVE', '20990817': 'P1 INCONCLUSIVE', '20990818': 'P1 TESTBED EXISTS'}
        got, bad = {}, {}
        for run in want:
            rc, rs = verdict(run)
            v = rs[3][0] if rc == 0 and len(rs) > 3 else {}
            got[run] = v
            if v.get('verdict') != want[run]:
                bad[run] = (v.get('verdict'), want[run], v.get('why'))
        check('v7_verdict_table', not bad, bad)
        # fix 1: the why line names the unknown count; fix 2: the share the rule used is over answered + unreadable
        check('v7_unknown_answers_named_in_why', 'unknown answer(s)' in got['20990814']['why'] and '(unkeyed 429: 60, unreadable: 0)' in got['20990814']['why']
              and 'unkeyed 429: 1,' in got['20990815']['why'] and 'unreadable: 2)' in got['20990817']['why']
              and got['20990814']['unkeyed_429'] == '60', {k: got[k]['why'] for k in ('20990814', '20990815', '20990817')})
        check('v7_unreadable_in_share_denominator', got['20990816']['min_a2c_pct'] == '21.4' and 'lowest 21.4%' in got['20990816']['why']
              and got['20990816']['unreadable'] == '40' and got['20990818']['min_a2c_pct'] == '93.8', (got['20990816'], got['20990818']))
        rc, rs = q('limkey', '--run', '20990816')
        by16 = {r['tcin']: r for r in rs[0]}
        check('v7_unreadable_column_kept_posts_exclude_it', (by16[TC]['posts'], by16[TC]['unreadable'], by16[TC]['a2c_pct']) == ('30', '40', '21.4'), by16)
        v = got['20990801']
        check('v7_testbed_counts', (v['hot_tcins'], v['posts'], v['min_posts_per_tcin'], v['err_a2c'], v['min_a2c_pct'], v['dummy_posts'],
                                    v['dummy_429'], v['decoy_err_a2c']) == ('2', '60', '30', '60', '100.0', '2', '0', '0'), v)
        check('v7_why_texts', '< 30 answered POSTs (min 29)' in got['20990803']['why'] and 'lowest 46.7%' in got['20990804']['why']
              and 'decoy was limited' in got['20990805']['why'] and 'decoy was limited' in got['20990806']['why']
              and 'decoy control is empty' in got['20990807']['why'] and 'no [LIMKEY_PROBE]' in got['20990810']['why'], {k: got[k]['why'] for k in got})
        # status 0 / -1 are counted separately and never in the rate
        rc, rs = q('limkey', '--run', '20990808')
        by = {r['tcin']: r for r in rs[0]}
        check('v7_status0_and_unreadable_never_in_rate', (by[TC]['posts'], by[TC]['err_a2c'], by[TC]['a2c_pct'], by[TC]['preflight_0'], by[TC]['unreadable']) ==
              ('29', '29', '100.0', '100', '0') and (by[TC2]['posts'], by[TC2]['unreadable']) == ('30', '5'), by)
        # a dummy 429 from another hour / from an account that did not probe is not this hour's control
        check('v7_control_is_same_hours_same_accounts', got['20990811']['dummy_429'] == '0' and got['20990812']['dummy_429'] == '0'
              and got['20990812']['dummy_posts'] == '2', (got['20990811'], got['20990812']))
        # statement 2/3: per TCIN x answer, per hour
        rc, rs = q('limkey', '--run', '20990804')
        s2 = {(r['tcin'], r['ans']): r['n'] for r in rs[1]}
        check('v7_per_tcin_answer_counts', s2 == {(TC, 'ERR_A2C'): '14', (TC, '424'): '16', (TC2, 'ERR_A2C'): '30'}, s2)
        check('v7_per_hour_rows', len(rs[2]) == 2 and {r['posts'] for r in rs[2]} == {'30'} and rs[2][0]['dummy_posts'] == '2', rs[2])
        # the remainder: 2xx, unkeyed 429 (both '?' and '-'), unreadable, 5xx are listed; 401 / keyed non-A2C 429 are known non-landing
        rc, rs = q('limkey', '--run', '20990813')
        rem = {r['ans']: r['n'] for r in rs[5]}
        # fix 3: a keyed non-A2C 429 (FAST_SELLING / a DCO soft landing), an ERR_A2C with a body and a 401 with a key are possibly landed
        # and listed; an empty-body ERR_A2C, a keyless 401, 424 and status 0 are the only hidden answers
        rem = {(r['ans'], r['err_key'], r['why_listed']): r['n'] for r in rs[5]}
        check('v7_unclassified_remainder', rem == {
            ('201', '-', 'not a known non-landing answer'): '1', ('429:unkeyed', '?', '429 whose key is not ERR_A2C (possibly a soft landing)'): '1',
            ('429:unkeyed', '-', '429 whose key is not ERR_A2C (possibly a soft landing)'): '1', ('unreadable(-1)', '?', 'unreadable'): '1',
            ('500', '-', 'not a known non-landing answer'): '1', ('429:SOME_OTHER_KEY', 'SOME_OTHER_KEY', '429 whose key is not ERR_A2C (possibly a soft landing)'): '1',
            ('429:FAST_SELLING', 'FAST_SELLING', '429 whose key is not ERR_A2C (possibly a soft landing)'): '1',
            ('ERR_A2C', 'ERR_A2C_TCIN_RATE_LIMITED', 'ERR_A2C with a body (body_len > 0)'): '1',
            ('ERR_A2C', 'ERR_A2C_TCIN_RATE_LIMITED', 'ERR_A2C, body length unknown'): '1',
            ('ERR_A2C', A2 + ',OTHER', 'ERR_A2C inside a combined key (not the exact key)'): '1',
            ('401', 'SOME_KEY', '401 with a key (not keyless)'): '1'}, rem)
        check('v7_remainder_hides_only_proven_non_landings', not any(k[0] in ('424', '0', 'preflight(0)') for k in rem) and not any(k[:2] == ('401', '-') for k in rem)
              # the empty-body exact-key 429 (body_len 0) stays hidden; body_len -1 and a combined key stay listed (the bot's own limkey_proven_non_landing)
              and sum(int(n) for k, n in rem.items() if k[0] == 'ERR_A2C') == 3, rem)
        ev_ = {(r['event'], r['reason']): (r['n'], r['status_2xx']) for r in rs[4]}
        check('v7_events_statement_shows_stop_2xx', ev_ == {('stop', 'probe_2xx'): ('1', '201'), ('skip', 'gap'): ('1', '')}, ev_)
        check('v7_parser_accounting_statement_empty', rs[6] == [], rs[6])
        # no limkey rows -> statements still run, the other result sets are just empty
        rc, rs = q('limkey', '--run', '20990810')
        check('v7_no_data_run_runs_clean', rc == 0 and rs[0] == [] and rs[3][0]['verdict'] == 'NO DATA' and rs[3][0]['hot_tcins'] == '0', rs[3])
        # pooled over every run: still runs (the verdict then pools hot TCINs across the synthetic nights)
        rc, rs = q('limkey')
        check('v7_all_runs_pooled_runs', rc == 0 and rs[3][0]['verdict'] in ('P1 TESTBED EXISTS', 'P1 DEMAND-SWITCHED', 'P1 INCONCLUSIVE'), rs[3])
        con = sqlite3.connect(str(db))
        check('v7_query_leaves_no_view_in_the_store', con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'view'").fetchone()[0] == 0)
        con.close()
    lst = io.StringIO()
    with contextlib.redirect_stdout(lst):
        evq.main(['--list'])
    check('v7_limkey_query_listed', any(l.startswith('limkey ') and 'P1' in l for l in lst.getvalue().splitlines()), lst.getvalue()[:300])


if __name__ == "__main__":
    test_glued_lines()
    test_duplicates_counted_once()
    test_gates()
    test_shot_index_interleaved()
    test_join_edge_cases()
    test_flips_and_windows()
    test_unparsed_remainder()
    test_checkout_orders_proxied()
    test_decoy_tokens()
    test_db_and_query_cli()
    test_v2_retry_shots()
    test_v2_atc400_cart_limit()
    test_v2_place_orders()
    test_v2_read_episodes()
    test_v2_queries()
    test_v3_status_and_shadow()
    test_v3_atc_net()
    test_v3_gate_events()
    test_v3_net_gate_queries()
    test_v4_fx1006_and_diag_gate_events()
    test_v5_harvest_rows()
    test_v5_harvest_dedupe_and_remainder()
    test_v5_other_tables_unchanged()
    test_v5_phase_query()
    test_v6_lock_events()
    test_v6_other_tables_unchanged()
    test_v6_store_and_cli()
    test_v6_dispatch_lag_query()
    test_v6_reflip_query()
    test_v7_limkey_rows()
    test_v7_limkey_events()
    test_v7_limkey_dedupe()
    test_v7_warm_dummy()
    test_v7_decoy_exclusion()
    test_v7_other_tables_unchanged()
    test_v7_real_format_drift()
    test_v7_store_and_cli()
    test_v7_limkey_query()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(1 if FAIL else 0)

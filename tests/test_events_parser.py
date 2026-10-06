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
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(1 if FAIL else 0)

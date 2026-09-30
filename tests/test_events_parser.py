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

No browser, no network, no bot. Run: python tests/test_events_parser.py
"""
from __future__ import annotations

import contextlib
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
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(1 if FAIL else 0)

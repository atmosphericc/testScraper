#!/usr/bin/env python3
"""Real-click Shape harvest + banked replay (2026-09-03). No browser, no network.

Pins src/session/shape_harvest.py (pure helpers, run for real) and the
purchase_executor.py wiring (harvest-tab capture+block, main-tab header
override, boot self-test arming, danger path), plus the bat pins + CRLF rule.

Run: python tests/test_shape_harvest.py
"""
from __future__ import annotations

import asyncio
import math
import random
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.session import shape_harvest as h  # noqa: E402

EXE_PATH = ROOT / 'src' / 'session' / 'purchase_executor.py'
BAT_PATH = ROOT / 'run_bot_with_nightly_restart.bat'
EXE_SRC = EXE_PATH.read_text(encoding='utf-8', errors='replace')
BAT_RAW = BAT_PATH.read_bytes()
BAT_SRC = BAT_RAW.decode('utf-8', errors='replace')

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}")


HD = {'Cookie': 'c=1', 'User-Agent': 'UA', 'Content-Type': 'application/json',
      'X-GyJwza5Z-a': 'A', 'X-GyJwza5Z-b': 'B', 'X-GyJwza5Z-c': 'C',
      'X-GyJwza5Z-d': 'D', 'X-GyJwza5Z-f': 'F', 'X-GyJwza5Z-z': 'Z',
      'x-application-name': 'web', 'sec-ch-ua': '"Chromium";v="152"'}


# ---------------------------------------------------------------------------
# 1. Pure helpers
# ---------------------------------------------------------------------------
def test_prefix_and_tokens():
    check("prefix_detected", h.shape_prefix(HD) == 'x-gyjwza5z-')
    check("prefix_ignores_app_name_only", h.shape_prefix({'x-application-name': 'web'}) is None)
    check("prefix_from_a0_anchor", h.shape_prefix({'X-GyJwza5Z-a0': 'x'}) == 'x-gyjwza5z-')
    toks = h.shape_tokens(HD)
    check("tokens_six", len(toks) == 6 and 'x-application-name' not in toks)


def test_merge():
    bank = dict(HD)
    bank['X-GyJwza5Z-a0'] = 'A0'
    bank['X-GyJwza5Z-f'] = 'F-BANKED'
    merged = h.merge_replay_headers(HD, bank)
    names = [k for k, _ in merged]
    vals = dict(merged)
    check("merge_keeps_cookie_ua_ct", vals.get('Cookie') == 'c=1' and vals.get('User-Agent') == 'UA'
          and vals.get('Content-Type') == 'application/json' and vals.get('sec-ch-ua') == HD['sec-ch-ua'])
    check("merge_replaces_f_token", vals.get('X-GyJwza5Z-f') == 'F-BANKED')
    check("merge_adds_a0", vals.get('X-GyJwza5Z-a0') == 'A0')
    check("merge_no_duplicate_names", len(names) == len(set(n.lower() for n in names)))
    check("merge_position_preserved", names.index('X-GyJwza5Z-a') == list(HD.keys()).index('X-GyJwza5Z-a'))
    check("merge_total_count", len(merged) == len(HD) + 1)
    # request without tokens -> banked appended
    req = {'Cookie': 'c', 'Content-Type': 'application/json'}
    m2 = dict(h.merge_replay_headers(req, bank))
    check("merge_appends_when_request_has_no_tokens", m2.get('X-GyJwza5Z-b') == 'B' and m2.get('Cookie') == 'c')
    # bank without tokens -> unchanged
    m3 = h.merge_replay_headers(HD, {'x-application-name': 'web'})
    check("merge_unchanged_without_bank_tokens", m3 == list(HD.items()))
    # case-insensitive replacement
    lower = {k.lower(): v for k, v in HD.items()}
    m4 = dict(h.merge_replay_headers(lower, bank))
    check("merge_case_insensitive", m4.get('X-GyJwza5Z-f') == 'F-BANKED' and 'x-gyjwza5z-f' not in m4)


def test_bank():
    b = h.ShapeBank(size=2, ttl_s=100.0)
    check("bank_rejects_tokenless", b.push({'Cookie': 'x'}, now=1000.0) is False)
    check("bank_push_1", b.push(HD, {'tcin': '1'}, now=1000.0) is True and b.count(1000.0) == 1)
    b.push(HD, {'tcin': '2'}, now=1010.0)
    b.push(HD, {'tcin': '3'}, now=1020.0)
    check("bank_capacity_drops_oldest", b.count(1020.0) == 2 and b.expired == 1)
    e = b.pop_fresh(1021.0)
    check("bank_pop_is_lifo_freshest", e is not None and e['meta']['tcin'] == '3')
    check("bank_need_after_pop", b.need(1021.0) == 1)
    check("bank_ttl_prunes", b.count(1200.0) == 0 and b.pop_fresh(1200.0) is None)
    check("bank_summary_shape", 'bank=0/2' in b.summary(1200.0) and 'replayed=1' in b.summary(1200.0))
    a0 = dict(HD); a0['X-GyJwza5Z-a0'] = 'zz'
    b2 = h.ShapeBank(3, 300)
    b2.push(a0, now=5.0)
    check("bank_flags_a0", b2.pop_fresh(6.0)['a0'] is True)
    # 2026-09-13 prefer_no_a0: the freshest REPLAYABLE a0=no set beats a fresher a0=yes one
    b3 = h.ShapeBank(3, 300)
    b3.push(HD, {'tcin': 'clean-old'}, now=100.0)
    b3.push(a0, {'tcin': 'bloated-new'}, now=110.0)
    e = b3.pop_fresh(111.0, prefer_no_a0=True)
    check("bank_prefers_no_a0", e is not None and e['meta']['tcin'] == 'clean-old' and e['a0'] is False)
    check("bank_prefer_leaves_other", b3.count(111.0) == 1 and b3.replayed == 1)
    e2 = b3.pop_fresh(112.0, prefer_no_a0=True)
    check("bank_prefer_falls_back_to_a0", e2 is not None and e2['meta']['tcin'] == 'bloated-new')
    import os as _os
    _os.environ['TARGET_HARVEST_MAX_REPLAY_AGE_S'] = '50'
    try:
        b4 = h.ShapeBank(3, 300)
        b4.push(HD, {'tcin': 'clean-stale'}, now=0.0)
        b4.push(a0, {'tcin': 'bloated-fresh'}, now=90.0)
        e3 = b4.pop_fresh(100.0, prefer_no_a0=True)
        check("bank_prefer_skips_stale_clean", e3 is not None and e3['meta']['tcin'] == 'bloated-fresh')
        b5 = h.ShapeBank(3, 300)
        b5.push(HD, {'tcin': 'clean-old'}, now=100.0)
        b5.push(a0, {'tcin': 'bloated-new'}, now=110.0)
        check("bank_default_pop_still_lifo", b5.pop_fresh(111.0)['meta']['tcin'] == 'bloated-new')
    finally:
        _os.environ.pop('TARGET_HARVEST_MAX_REPLAY_AGE_S', None)


def test_bezier_and_click_point():
    import os
    rng = random.Random(7)
    # TARGET_HARVEST_CLICK_MOVES bounds the intermediate moves. 2026-09-09: the
    # default is back to the natural path (cap 9) — the earlier "2" rested on the
    # refuted socket-saturation diagnosis. Restore the env after (no cross-test leak).
    _prev = os.environ.pop('TARGET_HARVEST_CLICK_MOVES', None)
    try:
        pts = h.bezier_path(100, 100, 500, 400, random.Random(7))
        check("bezier_default_natural_path", 6 <= len(pts) <= 10)
        check("bezier_ends_on_target", pts[-1][0] == 500.0 and pts[-1][1] == 400.0)
        # curvature: max perpendicular deviation from the straight line > 2px
        dx, dy = 400.0, 300.0
        L = math.hypot(dx, dy)
        dev = max(abs((x - 100) * dy - (y - 100) * dx) / L for x, y, _ in pts[:-1])
        check("bezier_is_curved", dev > 2.0)
        check("bezier_dt_bounds", all(0.004 <= dt <= 0.13 for _, _, dt in pts))
        # knob raises the move count …
        os.environ['TARGET_HARVEST_CLICK_MOVES'] = '6'
        check("bezier_knob_raises_moves", len(h.bezier_path(100, 100, 500, 400, random.Random(7))) >= 5)
        # … lowers it to a single move + settle …
        os.environ['TARGET_HARVEST_CLICK_MOVES'] = '1'
        check("bezier_knob_min_one_move", len(h.bezier_path(100, 100, 500, 400, random.Random(7))) == 2)
        # … and a bad value falls back to the default.
        os.environ['TARGET_HARVEST_CLICK_MOVES'] = 'bogus'
        check("bezier_knob_bad_value_defaults", len(h.bezier_path(100, 100, 500, 400, random.Random(7))) == len(pts))
    finally:
        if _prev is None:
            os.environ.pop('TARGET_HARVEST_CLICK_MOVES', None)
        else:
            os.environ['TARGET_HARVEST_CLICK_MOVES'] = _prev
    p2 = h.bezier_path(100, 100, 500, 400, random.Random(8))
    check("bezier_paths_differ", [round(p[0], 1) for p in p2[:-1]] != [round(p[0], 1) for p in pts[:-1]])
    rect = {'x': 100, 'y': 200, 'w': 120, 'h': 40}
    for _ in range(50):
        x, y = h.click_point(rect, rng)
        assert 100 <= x <= 220 and 200 <= y <= 240
    check("click_point_inside_rect", True)


def test_config_and_js():
    env = {'TARGET_SHAPE_HARVEST': '1', 'TARGET_HARVEST_TCINS': ' 21516452, 50225561;abc,21516452',
           'TARGET_HARVEST_BANK': '99', 'TARGET_HARVEST_TTL_S': '5', 'TARGET_HARVEST_REPLAY': '0'}
    c = h.config(env)
    check("cfg_enabled", c['enabled'] is True)
    check("cfg_tcins_parsed_dedup", c['tcins'] == ['21516452', '50225561'])
    check("cfg_bank_clamped", c['bank'] == 10)
    check("cfg_ttl_clamped", c['ttl_s'] == 30.0)
    check("cfg_replay_off", c['replay'] is False)
    check("cfg_default_off", h.config({})['enabled'] is False and h.is_enabled({}) is False)
    # 2026-09-04 per-account skip
    se = {'TARGET_SHAPE_HARVEST': '1', 'TARGET_HARVEST_SKIP': ' Primary , alt-1 '}
    check("skip_parsed_lower", h.harvest_skip(se) == ['primary', 'alt-1'])
    check("cfg_has_skip", h.config(se)['skip'] == ['primary', 'alt-1'])
    check("enabled_for_skips_named", h.enabled_for('primary', se) is False and h.enabled_for('PRIMARY', se) is False)
    check("enabled_for_allows_others", h.enabled_for('business', se) is True)
    check("enabled_for_off_when_master_off", h.enabled_for('business', {}) is False)
    js = h.FIND_ATC_BUTTON_JS
    check("js_selectors", 'shippingButton' in js and 'addToCartButton' in js and 'shipItButton' in js)
    check("js_scrolls_into_view", 'scrollIntoView' in js)
    check("js_reports_oos_and_disabled", "oos:" in js and 'aria-disabled' in js)
    check("js_null_body_guarded", 'document.body && document.body.innerText' in js)
    check("js_reports_readystate", 'ready:' in js and 'document.readyState' in js)
    check("pdp_url", h.pdp_url('123') == 'https://www.target.com/p/-/A-123')
    # 2026-09-13 fresh-page knobs
    c0 = h.config({'TARGET_SHAPE_HARVEST': '1'})
    check("cfg_fresh_page_default_off", c0['fresh_page'] is False and c0['prefer_no_a0'] is False
          and c0['fresh_page_live'] is True and c0['fresh_page_min_gap_s'] == 15.0)
    c1 = h.config({'TARGET_SHAPE_HARVEST': '1', 'TARGET_HARVEST_FRESH_PAGE': '1', 'TARGET_HARVEST_FRESH_PAGE_LIVE': '0',
                   'TARGET_HARVEST_FRESH_PAGE_MIN_GAP_S': '9999', 'TARGET_HARVEST_PREFER_NO_A0': '1'})
    check("cfg_fresh_page_parsed", c1['fresh_page'] is True and c1['fresh_page_live'] is False
          and c1['fresh_page_min_gap_s'] == 600.0 and c1['prefer_no_a0'] is True)


def test_header_bytes():
    hb = h.header_bytes(HD)
    exp_total = sum(len(k) + len(v) + 4 for k, v in HD.items())
    check("hb_total", hb['total'] == exp_total and hb['n'] == len(HD))
    check("hb_cookie_and_shape", hb['cookie'] == 3 and hb['shape'] == 6 and hb['a'] == 1 and hb['a0'] == 0)
    a0 = dict(HD); a0['X-GyJwza5Z-a0'] = 'zzzz'
    check("hb_a0", h.header_bytes(a0)['a0'] == 4 and h.header_bytes(a0)['shape'] == 10)
    check("hb_pairs_input", h.header_bytes(list(a0.items())) == h.header_bytes(a0))
    check("hb_empty", h.header_bytes({})['total'] == 0 and h.header_bytes(None)['n'] == 0)


# ---------------------------------------------------------------------------
# 2. human_click dispatches trusted CDP input with human timing (fake tab)
# ---------------------------------------------------------------------------
class FakeTab:
    def __init__(self, fail=False):
        self.events = []
        self.fail = fail
        self.url = 'https://www.target.com/p/-/A-1'

    async def send(self, gen):
        if self.fail:
            raise RuntimeError('cdp down')
        cmd = next(gen)  # zendriver cdp functions are generators yielding the command dict
        self.events.append((time.time(), cmd))
        try:
            gen.send({})
        except StopIteration:
            pass
        return {}


def test_human_click_events():
    tab = FakeTab()

    async def run():
        return await h.human_click(tab, 300.0, 200.0, (50.0, 50.0))
    end = asyncio.run(run())
    kinds = [c['params']['type'] for _, c in tab.events]
    check("click_returns_target", end == (300.0, 200.0))
    # 2026-09-09: default cap = 2 intermediate + 1 settle = 3 mouseMoved, then
    # press/release. Still a real trusted move-then-click, just fewer round trips.
    check("click_moves_then_press_release", kinds.count('mouseMoved') >= 3
          and kinds[-2:] == ['mousePressed', 'mouseReleased'])
    check("click_method_is_input_domain", all(c['method'] == 'Input.dispatchMouseEvent' for _, c in tab.events))
    t_press = [t for t, c in tab.events if c['params']['type'] == 'mousePressed'][0]
    t_rel = [t for t, c in tab.events if c['params']['type'] == 'mouseReleased'][0]
    check("click_hold_60_130ms", 0.05 <= (t_rel - t_press) <= 0.20)
    last = [c for _, c in tab.events if c['params']['type'] == 'mouseMoved'][-1]['params']
    check("click_last_move_on_target", last['x'] == 300 and last['y'] == 200)
    # 2026-09-09: per-send timing + the hidden-tab abort (a painting tab acks a
    # move in ms; a hidden one only via Chromium's 5 s rAF fallback).
    stats = {}
    asyncio.run(h.human_click(FakeTab(), 300.0, 200.0, (50.0, 50.0), stats=stats, move_abort_ms=0))
    kinds2 = [k for k, _ in stats['sends']]
    check("click_stats_records_every_send", kinds2.count('move') >= 2 and kinds2[-2:] == ['press', 'release']
          and all(isinstance(ms, int) and ms >= 0 for _, ms in stats['sends']))
    summ = h.click_stats_summary(stats)
    check("click_stats_summary_shape", summ.startswith('sends=') and 'max_move_ms=' in summ and 'press_ms=' in summ)
    check("click_stats_summary_empty",
          h.click_stats_summary(None) == 'sends=0 moves=0 max_move_ms=- press_ms=- release_ms=-')

    class SlowTab(FakeTab):
        async def send(self, gen):
            await asyncio.sleep(0.03)
            return await FakeTab.send(self, gen)

    slow = SlowTab()
    stats2 = {}
    try:
        asyncio.run(h.human_click(slow, 300.0, 200.0, (50.0, 50.0), stats=stats2, move_abort_ms=10))
        aborted = False
    except h.HarvestTabNotPainting as e:
        aborted = 'took' in str(e) and 'not painting' in str(e)
    check("click_aborts_on_slow_move", aborted)
    check("click_abort_is_on_first_move", len(stats2['sends']) == 1 and stats2['sends'][0][0] == 'move'
          and not any(c['params']['type'] == 'mousePressed' for _, c in slow.events))
    stats3 = {}
    asyncio.run(h.human_click(SlowTab(), 300.0, 200.0, (50.0, 50.0), stats=stats3, move_abort_ms=0))
    check("click_abort_disabled_by_zero", [k for k, _ in stats3['sends']][-1] == 'release')
    check("abort_error_is_runtime_error", issubclass(h.HarvestTabNotPainting, RuntimeError))


def test_visibility_probe_and_verdict():
    js = h.VISIBILITY_PROBE_JS
    check("vis_js_reads_visibility_and_raf", 'document.visibilityState' in js and 'requestAnimationFrame' in js
          and 'setTimeout' in js and 'raf_ms' in js)
    check("vis_verdict_visible", h.visibility_verdict({'vis': 'visible', 'raf_ms': 16, 'focus': True})[0] == 'visible')
    check("vis_verdict_hidden", h.visibility_verdict({'vis': 'hidden', 'raf_ms': -1, 'focus': False})[0] == 'hidden')
    check("vis_verdict_stalled", h.visibility_verdict({'vis': 'visible', 'raf_ms': -1})[0] == 'stalled')
    check("vis_verdict_garbage", h.visibility_verdict(None)[0] == 'stalled'
          and h.visibility_verdict({'vis': 'visible', 'raf_ms': 'x'})[0] == 'stalled')
    check("vis_verdict_detail", 'vis=hidden' in h.visibility_verdict({'vis': 'hidden', 'raf_ms': -1})[1])
    c = h.config({'TARGET_SHAPE_HARVEST': '1'})
    check("cfg_vis_guard_default_on", c['vis_guard'] is True and c['move_abort_ms'] == 2500)
    c2 = h.config({'TARGET_HARVEST_VIS_GUARD': '0', 'TARGET_HARVEST_MOVE_ABORT_MS': '0'})
    check("cfg_vis_guard_off_and_abort_off", c2['vis_guard'] is False and c2['move_abort_ms'] == 0)
    check("cfg_move_abort_clamped", h.config({'TARGET_HARVEST_MOVE_ABORT_MS': '999999'})['move_abort_ms'] == 20000)


# ---------------------------------------------------------------------------
# 3. Executor behaviour on a stub instance (no browser)
# ---------------------------------------------------------------------------
def _stub_executor():
    import src.session.purchase_executor as pe
    ex = pe.PurchaseExecutor.__new__(pe.PurchaseExecutor)
    ex._harvest_cfg = h.config({'TARGET_SHAPE_HARVEST': '1', 'TARGET_HARVEST_TCINS': '21516452'})
    ex._shape_bank = h.ShapeBank(3, 300)
    ex._harvest_replay_on = True
    ex._harvest_selftest_armed = False
    ex._harvest_last_replay = None
    ex._harvest_capture_evt = asyncio.Event()
    ex._harvest_stats = {'captured': 0, 'no_tokens': 0}
    ex._harvest_tcin = '21516452'
    ex._harvest_first_capture_logged = False
    ex._harvest_landed_suspect = False
    ex._harvest_win = {'shots': 0, 'replayed': 0, 'a0': 0, 'stale': 0}
    ex._harvest_prev_live = False
    ex.logger = SimpleNamespace(info=lambda *a, **k: None)
    alerts = []
    ex.session_manager = SimpleNamespace(account_id='primary', _alert_critical=lambda m: alerts.append(m))
    ex._alerts = alerts
    return ex, pe


def test_executor_replay_lookup():
    ex, pe = _stub_executor()
    bank = dict(HD); bank['X-GyJwza5Z-f'] = 'F-BANKED'; bank['X-GyJwza5Z-a0'] = 'A0'
    ex._shape_bank.push(bank, {'tcin': '21516452'})
    out = ex._harvest_replay_headers_for('main', dict(HD))
    check("replay_main_returns_header_entries", isinstance(out, list) and len(out) == len(HD) + 1
          and all(hasattr(e, 'name') and hasattr(e, 'value') for e in out))
    vals = {e.name: e.value for e in out}
    check("replay_main_swaps_tokens_keeps_cookie", vals['X-GyJwza5Z-f'] == 'F-BANKED'
          and vals['X-GyJwza5Z-a0'] == 'A0' and vals['Cookie'] == 'c=1')
    check("replay_consumes_bank", ex._shape_bank.count() == 0)
    check("replay_main_empty_bank_none", ex._harvest_replay_headers_for('main', dict(HD)) is None)
    ex._shape_bank.push(bank, {'tcin': '21516452'})
    check("replay_warmup_unarmed_none_and_bank_kept",
          ex._harvest_replay_headers_for('warmup', dict(HD)) is None and ex._shape_bank.count() == 1)
    ex._harvest_selftest_armed = True
    check("replay_warmup_armed_returns", isinstance(ex._harvest_replay_headers_for('warmup', dict(HD)), list))
    check("replay_records_last", ex._harvest_last_replay and ex._harvest_last_replay['label'] == 'warmup'
          and ex._harvest_last_replay.get('bytes', 0) > 0)
    ex._harvest_selftest_armed = False
    ex._shape_bank.push(bank, {'tcin': '21516452'})
    check("replay_harvest_label_none", ex._harvest_replay_headers_for('harvest', dict(HD)) is None)
    ex._harvest_replay_on = False
    check("replay_off_none_and_bank_kept",
          ex._harvest_replay_headers_for('main', dict(HD)) is None and ex._shape_bank.count() == 1)
    # 2026-09-13 prefer_no_a0 wiring: the shot takes the clean set over the fresher bloated one
    ex5, _ = _stub_executor()
    ex5._harvest_cfg = h.config({'TARGET_SHAPE_HARVEST': '1', 'TARGET_HARVEST_TCINS': '21516452',
                                 'TARGET_HARVEST_PREFER_NO_A0': '1'})
    clean = dict(HD); clean['X-GyJwza5Z-f'] = 'F-CLEAN'
    ex5._shape_bank.push(clean, {'tcin': 'clean'}); ex5._shape_bank.push(bank, {'tcin': 'bloated'})
    out5 = {e.name: e.value for e in ex5._harvest_replay_headers_for('main', dict(HD))}
    check("replay_prefers_no_a0_set", out5.get('X-GyJwza5Z-f') == 'F-CLEAN' and 'X-GyJwza5Z-a0' not in out5)
    ex6, _ = _stub_executor()   # default (no preference) still takes the freshest = bloated
    ex6._shape_bank.push(clean, {'tcin': 'clean'}); ex6._shape_bank.push(bank, {'tcin': 'bloated'})
    out6 = {e.name: e.value for e in ex6._harvest_replay_headers_for('main', dict(HD))}
    check("replay_default_takes_freshest", out6.get('X-GyJwza5Z-a0') == 'A0')


class NavTab(FakeTab):
    def __init__(self, fail=False):
        super().__init__()
        self.gets = []
        self.fail_get = fail

    async def get(self, url):
        self.gets.append(url)
        if self.fail_get:
            raise RuntimeError('nav boom')
        return self


def _fresh_stub(env_extra=None):
    ex, pe = _stub_executor()
    env = {'TARGET_SHAPE_HARVEST': '1', 'TARGET_HARVEST_TCINS': '21516452', 'TARGET_HARVEST_FRESH_PAGE': '1'}
    env.update(env_extra or {})
    ex._harvest_cfg = h.config(env)
    ex._harvest_clicks_since_nav = 0
    ex._harvest_last_reload_ts = 0.0
    ex._harvest_fresh_stats = {'reloads': 0, 'gap_skips': 0, 'live_skips': 0, 'failed': 0}
    ex._harvest_tab_nav_ts = 0.0
    ex._harvest_last_xy = (1, 1)
    drops = []

    async def _drop(reason, close=True):
        drops.append(reason)
        ex._harvest_tab = None
    ex._harvest_drop_tab = _drop
    ex._drops = drops
    return ex


def test_executor_fresh_page():
    ex = _fresh_stub(); tab = NavTab(); ex._harvest_tab = tab
    check("fresh_no_click_no_reload", asyncio.run(ex._harvest_fresh_page(tab, False)) is True and tab.gets == [])
    ex._harvest_clicks_since_nav = 3; ex._harvest_last_xy = (5, 5)
    ok = asyncio.run(ex._harvest_fresh_page(tab, False))
    check("fresh_reloads_same_pdp", ok is True and tab.gets == [h.pdp_url('21516452')])
    check("fresh_resets_state", ex._harvest_clicks_since_nav == 0 and ex._harvest_last_xy is None
          and ex._harvest_fresh_stats['reloads'] == 1 and ex._harvest_tab_nav_ts > 0)
    ex._harvest_clicks_since_nav = 1
    check("fresh_min_gap_skips", asyncio.run(ex._harvest_fresh_page(tab, False)) is True and len(tab.gets) == 1
          and ex._harvest_fresh_stats['gap_skips'] == 1)
    ex._harvest_last_reload_ts = 0.0
    check("fresh_live_allowed_by_default", asyncio.run(ex._harvest_fresh_page(tab, True)) is True and len(tab.gets) == 2)
    ex2 = _fresh_stub({'TARGET_HARVEST_FRESH_PAGE_LIVE': '0'}); tab2 = NavTab(); ex2._harvest_tab = tab2
    ex2._harvest_clicks_since_nav = 2
    check("fresh_live_blocked_when_off", asyncio.run(ex2._harvest_fresh_page(tab2, True)) is True and tab2.gets == []
          and ex2._harvest_fresh_stats['live_skips'] == 1)
    check("fresh_idle_reloads_when_live_off", asyncio.run(ex2._harvest_fresh_page(tab2, False)) is True and len(tab2.gets) == 1)
    ex3 = _fresh_stub({'TARGET_HARVEST_FRESH_PAGE': '0'}); tab3 = NavTab(); ex3._harvest_tab = tab3
    ex3._harvest_clicks_since_nav = 9
    check("fresh_flag_off_noop", asyncio.run(ex3._harvest_fresh_page(tab3, False)) is True and tab3.gets == []
          and ex3._harvest_clicks_since_nav == 9)
    ex4 = _fresh_stub(); tab4 = NavTab(fail=True); ex4._harvest_tab = tab4
    ex4._harvest_clicks_since_nav = 1
    check("fresh_nav_failure_drops_tab", asyncio.run(ex4._harvest_fresh_page(tab4, False)) is False
          and ex4._drops == ['fresh page reload failed'] and ex4._harvest_fresh_stats['failed'] == 1)


def _event(headers, body='{"cart_item":{"tcin":"21516452","quantity":1}}'):
    from zendriver import cdp
    req = SimpleNamespace(headers=headers, post_data=body, has_post_data=True, method='POST',
                          url='https://carts.target.com/web_checkouts/v1/cart_items?x=1')
    return SimpleNamespace(request=req, request_id=cdp.fetch.RequestId('interception-1'))


def test_executor_capture_and_block():
    ex, pe = _stub_executor()
    tab = FakeTab()
    ev = _event(dict(HD))

    async def run():
        await ex._harvest_capture_and_block(tab, ev, ev.request.url)
    asyncio.run(run())
    cmds = [c for _, c in tab.events]
    check("capture_sends_fail_request", any(c['method'] == 'Fetch.failRequest' for c in cmds))
    fr = [c for c in cmds if c['method'] == 'Fetch.failRequest'][0]
    check("capture_blocked_by_client", fr['params']['errorReason'] == 'BlockedByClient'
          and fr['params']['requestId'] == 'interception-1')
    check("capture_never_continues", not any(c['method'] == 'Fetch.continueRequest' for c in cmds))
    check("capture_banks_set", ex._shape_bank.count() == 1 and ex._harvest_stats['captured'] == 1)
    check("capture_sets_event", ex._harvest_capture_evt.is_set())
    check("capture_meta_has_source", ex._shape_bank.pop_fresh()['meta']['tcin'] == '21516452')
    # tokenless page request: blocked but not banked
    ex2, _ = _stub_executor()
    tab2 = FakeTab()
    ev2 = _event({'Cookie': 'c', 'Content-Type': 'application/json'})
    asyncio.run(ex2._harvest_capture_and_block(tab2, ev2, ev2.request.url))
    check("capture_tokenless_blocked_not_banked", ex2._shape_bank.count() == 0
          and ex2._harvest_stats['no_tokens'] == 1
          and any(c['method'] == 'Fetch.failRequest' for _, c in tab2.events))
    # danger path: fail_request raises -> suspect flag + alert, nothing banked
    ex3, _ = _stub_executor()
    tab3 = FakeTab(fail=True)
    ev3 = _event(dict(HD))
    asyncio.run(ex3._harvest_capture_and_block(tab3, ev3, ev3.request.url))
    check("danger_flags_suspect_and_alerts", ex3._harvest_landed_suspect is True and len(ex3._alerts) == 1)
    check("danger_banks_nothing", ex3._shape_bank.count() == 0)


def _tid(s):
    from zendriver import cdp
    return cdp.target.TargetID(s)     # what zendriver's Tab.target.target_id really is (str subclass)


class VisTab:
    """Fake harvest tab: a scripted sequence of visibility-probe answers."""
    def __init__(self, seq):
        self.seq = list(seq)
        self.activated = 0
        self.target = SimpleNamespace(target_id=_tid('T1'), url='https://www.target.com/p/-/A-21516452')
        self.type_ = 'page'

    async def evaluate(self, js, await_promise=False):
        return self.seq.pop(0) if len(self.seq) > 1 else self.seq[0]

    async def activate(self):
        self.activated += 1


def test_executor_visibility_guard():
    ex, pe = _stub_executor()
    ex._harvest_vis_state = ''
    ex._harvest_vis_skips = 0
    ex._harvest_tab = None
    ex._harvest_last_xy = (1.0, 1.0)
    ex.session_manager.is_purchase_in_progress = lambda: False
    ex.session_manager._dead_session_parked_until = 0.0
    ex.session_manager.browser = None
    vis = {'vis': 'visible', 'raf_ms': 16, 'focus': True}
    hid = {'vis': 'hidden', 'raf_ms': -1, 'focus': False}
    t = VisTab([vis])
    check("guard_visible_ok", asyncio.run(ex._harvest_ensure_visible(t)) == 'ok' and t.activated == 0)
    t = VisTab([hid, vis])
    check("guard_hidden_activates_then_ok", asyncio.run(ex._harvest_ensure_visible(t)) == 'ok' and t.activated == 1)
    t = VisTab([hid, hid])
    check("guard_still_hidden_is_a_strike", asyncio.run(ex._harvest_ensure_visible(t)) == 'hidden' and t.activated == 1)
    ex.session_manager.is_purchase_in_progress = lambda: True
    t = VisTab([hid, vis])
    check("guard_activates_during_live_purchase", asyncio.run(ex._harvest_ensure_visible(t)) == 'ok' and t.activated == 1)
    ex.session_manager.is_purchase_in_progress = lambda: False
    ex.session_manager._dead_session_parked_until = time.time() + 60
    t = VisTab([hid])
    check("guard_skips_when_parked", asyncio.run(ex._harvest_ensure_visible(t)) == 'skip' and t.activated == 0)
    ex.session_manager._dead_session_parked_until = 0.0
    t = VisTab([{'vis': 'visible', 'raf_ms': -1}])
    check("guard_stalled_lets_the_click_decide", asyncio.run(ex._harvest_ensure_visible(t)) == 'ok')
    # drop closes the target through the browser-level connection …
    sent = []

    async def _send(cmd):
        sent.append(next(cmd))
        return {}
    ex.session_manager.browser = SimpleNamespace(connection=SimpleNamespace(send=_send))
    ex._harvest_tab = VisTab([vis])
    asyncio.run(ex._harvest_drop_tab("test"))
    check("drop_closes_target", ex._harvest_tab is None and len(sent) == 1
          and sent[0]['method'] == 'Target.closeTarget' and sent[0]['params']['targetId'] == 'T1')
    # … but not when the browser it belonged to is gone.
    ex._harvest_tab = VisTab([vis])
    sent.clear()
    asyncio.run(ex._harvest_drop_tab("browser gone", close=False))
    check("drop_without_close_when_browser_changed", ex._harvest_tab is None and not sent)
    # orphan sweep: closes PDP page targets, never the kept id, max 3
    orphans = [VisTab([vis]) for _ in range(5)]
    for i, o in enumerate(orphans):
        o.target = SimpleNamespace(target_id=_tid(f'O{i}'), url='https://www.target.com/p/-/A-21516452?x')
    other = VisTab([vis])
    other.target = SimpleNamespace(target_id=_tid('W'), url='https://www.target.com/cart')
    browser = SimpleNamespace(connection=SimpleNamespace(send=_send), targets=orphans + [other])
    sent.clear()
    n = asyncio.run(ex._harvest_close_orphans(browser, 'https://www.target.com/p/-/A-21516452', 'O0'))
    ids = [c['params']['targetId'] for c in sent]
    check("orphans_closed_bounded_and_keep_respected", n == 3 and 'O0' not in ids and 'W' not in ids and len(ids) == 3)


# ---------------------------------------------------------------------------
# 4. Executor wiring (source pins)
# ---------------------------------------------------------------------------
def test_executor_wiring():
    check("exe_imports_module", "from . import shape_harvest as _shape_harvest" in EXE_SRC)
    check("exe_init_state", "self._shape_bank = _shape_harvest.ShapeBank(" in EXE_SRC
          and "self._harvest_replay_on: bool = bool(self._harvest_cfg['enabled'] and self._harvest_cfg['replay'])" in EXE_SRC)
    check("exe_interceptor_label_param", "label: Optional[str] = None) -> None:" in EXE_SRC
          and 'label = label or ("warmup" if persistent else "main")' in EXE_SRC)
    i_branch = EXE_SRC.find("if _harvest_atc:")
    i_call = EXE_SRC.find("await self._harvest_capture_and_block(tab, event, url)", i_branch)
    i_ret = EXE_SRC.find("return", i_call)
    i_generic = EXE_SRC.find("if 'carts.target.com' in url or 'cart_items' in url:")
    check("exe_harvest_branch_before_generic_and_returns", 0 < i_branch < i_call < i_ret < i_generic)
    i_calc = EXE_SRC.find("_harvest_atc = (label == 'harvest' and not is_response")
    i_dedup = EXE_SRC.find("if dedup_key in self._cdp_continued_ids and not _harvest_atc:")
    check("exe_harvest_add_bypasses_dedup_shortcut", 0 < i_calc < i_dedup < i_branch)
    check("exe_harvest_matches_any_cart_items_post_put",
          "_hv_method in ('POST', 'PUT') and 'cart_items' in _hv_url" in EXE_SRC)
    check("exe_harvest_put_blocked_not_banked", "blocked a harvest-tab" in EXE_SRC
          and "if blocked and _method == 'POST':" in EXE_SRC)
    check("exe_replay_lookup_guarded_by_flag",
          "_override_headers = self._harvest_replay_headers_for(label, headers)" in EXE_SRC
          and "getattr(self, '_harvest_cfg', {}).get('enabled')" in EXE_SRC)
    i_over = EXE_SRC.find("request_id=event.request_id, headers=_override_headers))")
    i_fb = EXE_SRC.find("header-override continue FAILED", i_over)
    i_plain = EXE_SRC.find("await tab.send(cdp.fetch.continue_request(request_id=event.request_id))", i_fb)
    check("exe_override_continue_with_plain_fallback", 0 < i_over < i_fb < i_plain)
    check("exe_override_var_reset_per_event", "_override_headers = None  # 2026-09-03 harvest replay" in EXE_SRC)
    check("exe_start_hook_in_refill", "self._start_harvest()  # 2026-09-03" in EXE_SRC)
    check("exe_harvest_tab_label", "self._setup_cdp_fetch_interceptor(tab, persistent=True, label='harvest')" in EXE_SRC)
    check("exe_fail_request_blocked_by_client",
          "error_reason=cdp.network.ErrorReason.BLOCKED_BY_CLIENT" in EXE_SRC)
    check("exe_selftest_arms_warmup_only",
          "elif label == 'warmup' and self._harvest_selftest_armed:" in EXE_SRC
          and "self._harvest_selftest_armed = True" in EXE_SRC
          and "self._harvest_selftest_armed = False" in EXE_SRC)
    check("exe_selftest_disables_replay_on_3_rejections",
          "if replayed_n >= 3 and len(rejected) >= 3:" in EXE_SRC
          and "self._harvest_replay_on = False" in EXE_SRC)
    # 2026-09-16 HV-1: the live guard keeps its default (allow_live=False); only the
    # flag-gated buttonless-page path may pass allow_live (behaviour in test_hv1_*).
    check("exe_rotate_skips_live_purchase",
          "if self.session_manager.is_purchase_in_progress() and not allow_live:\n            return" in EXE_SRC
          and "async def _harvest_rotate(self, same: bool = False, allow_live: bool = False) -> None:" in EXE_SRC)
    check("exe_pre_shot_log_before_fast_lane",
          EXE_SRC.find('self._harvest_log(f"pre-shot') < EXE_SRC.find("_fl = await self._api_fast_lane(tab, tcin, quantity, extra_headers_js)"))
    check("exe_start_harvest_loud_without_tcins", "DISABLED: TARGET_HARVEST_TCINS is empty" in EXE_SRC)
    check("exe_real_atc_shape_logged_once", "REAL_ATC_SHAPE url=" in EXE_SRC)
    check("exe_harvest_readiness_poll",
          "info.get('found') and not info.get('disabled') and info.get('ready') == 'complete'" in EXE_SRC)
    check("exe_start_harvest_honors_skip",
          "if _acct in (cfg.get('skip') or []):" in EXE_SRC and "TARGET_HARVEST_SKIP" in EXE_SRC)
    check("exe_harvest_hydration_settle",
          "if time.time() - self._harvest_tab_nav_ts < 3.0:" in EXE_SRC)
    # 2026-09-09 hidden-tab root cause wiring
    check("exe_vis_guard_before_click", 0 < EXE_SRC.find("_vis = await self._harvest_ensure_visible(tab)")
          < EXE_SRC.find("_shape_harvest.human_click(tab, x, y, self._harvest_last_xy, stats=_stats"))
    check("exe_click_passes_abort_threshold", "move_abort_ms=self._harvest_cfg.get('move_abort_ms', 2500)" in EXE_SRC)
    check("exe_abort_marks_hidden", "isinstance(e, _shape_harvest.HarvestTabNotPainting)" in EXE_SRC
          and "self._harvest_vis_state = 'hidden'" in EXE_SRC)
    check("exe_activate_bounded", "await asyncio.wait_for(tab.activate(), timeout=3.0)" in EXE_SRC)
    check("exe_never_steals_from_a_parked_account",
          "if time.time() < parked_until:" in EXE_SRC and "return 'skip'" in EXE_SRC
          and "if live or time.time() < parked_until:" not in EXE_SRC)
    check("exe_drop_closes_target", "cdp.target.close_target(target_id=tid)" in EXE_SRC)
    for site in ("button lookup failed", "PDP nav failed", "consecutive click failures",
                 "after suspect-cart clear", "hidden after activate"):
        check(f"exe_drop_site_{site.split()[0]}_{site.split()[-1]}", f'self._harvest_drop_tab("{site}' in EXE_SRC)
    check("exe_browser_changed_drop_no_close",
          'await self._harvest_drop_tab("browser changed", close=False)' in EXE_SRC)
    check("exe_orphan_sweep_after_failed_open",
          'await self._harvest_close_orphans(browser, f"/A-{tcin}", None)' in EXE_SRC)
    check("exe_no_bare_handle_drops_left", EXE_SRC.count("self._harvest_tab = None") <= 4)
    check("exe_click_logs_timing", "| {_shape_harvest.click_stats_summary(_stats)}" in EXE_SRC)
    check("exe_socket_story_gone", "queue behind the warmup interceptor" not in EXE_SRC)
    # 2026-09-13 fresh-page harvest + size forensics
    check("exe_fresh_page_before_click", 0 < EXE_SRC.find("if not await self._harvest_fresh_page(tab, _live_now):")
          < EXE_SRC.find("_poll_deadline = time.time() + 9.0"))
    check("exe_fresh_page_counts_clicks", "self._harvest_clicks_since_nav += 1" in EXE_SRC
          and EXE_SRC.count("self._harvest_clicks_since_nav = 0") >= 3)
    check("exe_fresh_page_drop_site", 'self._harvest_drop_tab("fresh page reload failed")' in EXE_SRC)
    check("exe_fresh_page_bounded_nav",
          EXE_SRC.count("await asyncio.wait_for(tab.get(_shape_harvest.pdp_url(tcin)), timeout=25.0)") == 2)
    check("exe_capture_logs_bytes", "a0_len={_hb['a0']} hdr_bytes={_hb['total']} cookie={_hb['cookie']}" in EXE_SRC)
    check("exe_replay_logs_bytes",
          "hdr_bytes={_hb['total']} (cookie={_hb['cookie']} shape={_hb['shape']} a0={_hb['a0']})" in EXE_SRC)
    check("exe_pop_prefers_no_a0",
          "self._shape_bank.pop_fresh(prefer_no_a0=bool(self._harvest_cfg.get('prefer_no_a0')))" in EXE_SRC)
    check("exe_431_names_request_size", "if status == 431:" in EXE_SRC and "req_bytes={_rb.get('total', '?')}" in EXE_SRC
          and "self._last_req_hdr_bytes[label] = _sz" in EXE_SRC)


# ---------------------------------------------------------------------------
# 5. Bat pins + CRLF
# ---------------------------------------------------------------------------
def _bat_val(name):
    import re
    m = re.search(r'^set ' + re.escape(name) + r'=(.*)$', BAT_SRC, re.M)
    return (m.group(1).strip() if m else '')


def test_bat_pins():
    check("bat_harvest_on", _bat_val('TARGET_SHAPE_HARVEST') == '1')
    tc = _bat_val('TARGET_HARVEST_TCINS')
    check("bat_harvest_tcins_digits", bool(tc) and all(t.strip().isdigit() for t in tc.split(',')))
    check("bat_harvest_knobs", _bat_val('TARGET_HARVEST_BANK') == '3' and _bat_val('TARGET_HARVEST_TTL_S') == '300'
          and _bat_val('TARGET_HARVEST_REPLAY') == '1' and _bat_val('TARGET_HARVEST_SELFTEST') == '1')
    check("bat_crlf_only", BAT_RAW.count(b'\n') == BAT_RAW.count(b'\r\n') and BAT_RAW.count(b'\r\n') > 100)
    check("bat_changelog_dated", "2026-09-03" in BAT_SRC and "HARVEST" in BAT_SRC)
    check("bat_vis_guard_pinned", _bat_val('TARGET_HARVEST_VIS_GUARD') == '1'
          and _bat_val('TARGET_HARVEST_MOVE_ABORT_MS') == '2500')
    check("bat_hidden_tab_note", "FOREGROUND tab" in BAT_SRC and "2026-09-09" in BAT_SRC
          and "share the CDP socket" not in BAT_SRC)
    # 2026-09-13 fresh-page harvest armed
    check("bat_fresh_page_armed", _bat_val('TARGET_HARVEST_FRESH_PAGE') == '1'
          and _bat_val('TARGET_HARVEST_FRESH_PAGE_LIVE') == '1'
          and _bat_val('TARGET_HARVEST_FRESH_PAGE_MIN_GAP_S') == '15'
          and _bat_val('TARGET_HARVEST_PREFER_NO_A0') == '1')
    check("bat_fresh_page_note", "2026-09-13" in BAT_SRC and "first-click" in BAT_SRC)


def test_compiles():
    import py_compile
    ok = True
    for f in (EXE_PATH, ROOT / 'src' / 'session' / 'shape_harvest.py', ROOT / 'preflight_fp_drop.py'):
        try:
            py_compile.compile(str(f), doraise=True)
        except Exception as e:
            ok = False
            print('   compile error', f, e)
    check("compiles", ok)


# ---------------------------------------------------------------------------
# 6. 2026-09-16 hot-sku plan P8 (HV-1): miss probe + PX park, SKIP fix, live
#    renav (built, not armed), bad-load back-off, harvest_stuck, adaptive bank
#    gate, relaunch flush. No browser: stub tabs, node for the probe JS only.
# ---------------------------------------------------------------------------
import contextlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

from src.session import px_challenge as pxm  # noqa: E402

NODE = shutil.which('node')
_HV1_TMP = tempfile.mkdtemp(prefix='hv1_')
MGR_PATH = ROOT / 'src' / 'purchasing' / 'bulletproof_purchase_manager.py'
MGR_SRC = MGR_PATH.read_text(encoding='utf-8', errors='replace')
HV1_ENV = ('TARGET_HARVEST_SKIP_DISABLES_REPLAY', 'TARGET_HARVEST_MISS_PROBE', 'TARGET_HARVEST_MISS_SHOTS_MAX',
           'TARGET_HARVEST_PX_PARK_S', 'TARGET_HARVEST_MISS_RENAV_LIVE', 'TARGET_HARVEST_BADLOAD_BACKOFF_S',
           'TARGET_HARVEST_FLUSH_ON_RELAUNCH', 'TARGET_BANK_GATE_ADAPTIVE')
MISS = {'found': False, 'disabled': True, 'ready': 'complete', 'oos': False}
PX_INFO = {'url': 'https://www.target.com/p/-/A-111', 'title': 'Target', 'px_container': True,
           'px_iframe': False, 'press_hold': True, 'denied': False, 'ready': 'complete', 'vis': 'visible',
           'buttons': 1, 'buttons_vis': 1, 'fulfil': 0, 'text': 'Press & Hold to confirm you are a human',
           'hint': 'px_markers'}
OK_INFO = {'url': 'https://www.target.com/p/x/-/A-111', 'title': 'Card : Target', 'px_container': False,
           'px_iframe': False, 'press_hold': False, 'denied': False, 'ready': 'complete', 'vis': 'visible',
           'buttons': 40, 'buttons_vis': 12, 'fulfil': 0, 'text': 'Pokemon card', 'hint': 'no_fulfillment_block'}


def _captured(coro_or_fn):
    """Run a coroutine (or a plain callable) and return (result, printed text)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = asyncio.run(coro_or_fn) if asyncio.iscoroutine(coro_or_fn) else coro_or_fn()
    return res, buf.getvalue()


class ProbeTab(NavTab):
    """Harvest tab stub: answers the button lookup with MISS and the miss probe
    with a scripted snapshot; records every evaluate + nav."""
    def __init__(self, probe, probe_raises=None, fail_get=False):
        super().__init__(fail=fail_get)
        self.probe = probe
        self.probe_raises = probe_raises
        self.evals = []

    async def evaluate(self, js, await_promise=False):
        self.evals.append(js)
        if js == h.HARVEST_MISS_PROBE_JS:
            if self.probe_raises is not None:
                raise self.probe_raises
            return self.probe
        return dict(MISS)

    def probes(self):
        return sum(1 for j in self.evals if j == h.HARVEST_MISS_PROBE_JS)


def _hv1_stub(env_extra=None, live=False, tcins='111,222'):
    ex, pe = _stub_executor()
    env = {'TARGET_SHAPE_HARVEST': '1', 'TARGET_HARVEST_TCINS': tcins}
    env.update(env_extra or {})
    ex._harvest_cfg = h.config(env)
    ex._harvest_tcin = '111'
    ex._harvest_tcin_idx = 0
    ex._harvest_miss = 0
    ex._harvest_tab_nav_ts = time.time() - 100.0
    ex._harvest_last_reload_ts = 0.0
    ex._harvest_last_xy = None
    ex._harvest_clicks_since_nav = 0
    ex._harvest_disabled_reason = ''
    ex._harvest_tab = None           # tests attach their ProbeTab (the rotate navigates this handle)
    ex.logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
    ex.session_manager.is_purchase_in_progress = (lambda: live)
    shots = []

    async def _shot(tab, path):
        shots.append(path)
    ex._screenshot = _shot
    ex._shots = shots
    ex._harvest_px_error_log_path = os.path.join(_HV1_TMP, f"err_{id(ex)}.txt")
    return ex, pe


def _err_lines(ex):
    try:
        with open(ex._harvest_px_error_log_path, encoding='utf-8') as f:
            return [l for l in f.read().splitlines() if l.strip()]
    except FileNotFoundError:
        return []


def test_hv1_env_clean():
    # The executor paths below read the harvest config dict, the manager reads the
    # env: make sure no armed value leaks in from the shell running the suite.
    for k in HV1_ENV:
        os.environ.pop(k, None)
    check("hv1_env_clean", all(k not in os.environ for k in HV1_ENV))


def test_hv1_config():
    c = h.config({'TARGET_SHAPE_HARVEST': '1'})
    check("hv1_cfg_flags_default_off", c['skip_disables_replay'] is False and c['miss_probe'] is False
          and c['miss_renav_live'] is False and c['flush_on_relaunch'] is False)
    check("hv1_cfg_knob_defaults", c['miss_shots_max'] == 10 and c['px_park_s'] == 300.0
          and c['badload_backoff_s'] == 300.0)
    c1 = h.config({'TARGET_HARVEST_SKIP_DISABLES_REPLAY': ' 1 ', 'TARGET_HARVEST_MISS_PROBE': '1',
                   'TARGET_HARVEST_MISS_SHOTS_MAX': ' 3', 'TARGET_HARVEST_PX_PARK_S': '600 ',
                   'TARGET_HARVEST_MISS_RENAV_LIVE': '1', 'TARGET_HARVEST_BADLOAD_BACKOFF_S': '120',
                   'TARGET_HARVEST_FLUSH_ON_RELAUNCH': '1'})
    check("hv1_cfg_parsed_stripped", c1['skip_disables_replay'] and c1['miss_probe'] and c1['miss_renav_live']
          and c1['flush_on_relaunch'] and c1['miss_shots_max'] == 3 and c1['px_park_s'] == 600.0
          and c1['badload_backoff_s'] == 120.0)
    c2 = h.config({'TARGET_HARVEST_MISS_SHOTS_MAX': '999', 'TARGET_HARVEST_PX_PARK_S': '5',
                   'TARGET_HARVEST_BADLOAD_BACKOFF_S': 'x', 'TARGET_HARVEST_MISS_PROBE': '0',
                   'TARGET_HARVEST_FLUSH_ON_RELAUNCH': 'yes'})
    check("hv1_cfg_clamped_and_strict", c2['miss_shots_max'] == 100 and c2['px_park_s'] == 30.0
          and c2['badload_backoff_s'] == 300.0 and c2['miss_probe'] is False and c2['flush_on_relaunch'] is False)
    check("hv1_cfg_zero_shots_allowed", h.config({'TARGET_HARVEST_MISS_SHOTS_MAX': '0'})['miss_shots_max'] == 0)


def test_hv1_bank_clear():
    b = h.ShapeBank(3, 300)
    check("hv1_clear_empty", b.clear() == 0 and b.expired == 0)
    b.push(HD, now=time.time())
    b.push(HD, now=time.time())
    exp0 = b.expired
    n = b.clear()
    check("hv1_clear_counts_as_expired", n == 2 and b.count() == 0 and b.expired == exp0 + 2
          and b.harvested == 2 and b.replayed == 0 and b.stale == 0)
    check("hv1_clear_then_pop_none", b.pop_fresh() is None and b.need() == 3)


def _probe_node(scen):
    harness = r"""
const scen = __SCEN__;
const mkEl = (w, h) => ({ getBoundingClientRect: () => ({ width: w, height: h }) });
globalThis.location = { href: scen.href, pathname: scen.path };
globalThis.document = {
  title: scen.title || '', readyState: 'complete', visibilityState: scen.vis || 'visible',
  body: scen.body === null ? null : { innerText: scen.body },
  querySelector: (s) => (scen.present || []).some((p) => s.includes(p)) ? mkEl(1, 1) : null,
  querySelectorAll: (s) => {
    if (s === 'button') return (scen.buttons || []).map((wh) => mkEl(wh[0], wh[1]));
    if (s.includes('fulfillment')) return new Array(scen.fulfil || 0).fill(mkEl(1, 1));
    return [];
  },
};
const out = __JS__;
console.log(JSON.stringify(out));
"""
    src = harness.replace('__SCEN__', json.dumps(scen)).replace('__JS__', h.HARVEST_MISS_PROBE_JS)
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8', dir=_HV1_TMP) as f:
        f.write(src)
        path = f.name
    proc = subprocess.run([NODE, path], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise AssertionError(f"node failed rc={proc.returncode}: {proc.stderr[:400]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_hv1_probe_js():
    js = h.HARVEST_MISS_PROBE_JS
    # The PX half duplicates px_challenge.PX_MARKERS_JS verbatim (same selectors +
    # regexes), so is_px_challenge/describe classify the probe result.
    for frag in ('#px-captcha, [id^="px-captcha"], [class*="px-captcha"]',
                 'iframe[src*="px-cdn.net"], iframe[src*="px-cloud.net"], iframe[src*="/captcha/"]',
                 r'/press\s*(&|&amp;|and)\s*hold/i',
                 r'/access to this page has been denied|verify (that )?you are (a )?human|are you a human\??/i'):
        check(f"hv1_probe_dup_px_marker[{frag[:18]}]", frag in pxm.PX_MARKERS_JS and frag in js)
    for key in ('url:', 'title:', 'px_container:', 'px_iframe:', 'press_hold:', 'denied:', 'ready:',
                'vis:', 'buttons:', 'buttons_vis:', 'fulfil:', 'text:', 'hint:'):
        check(f"hv1_probe_key[{key[:-1]}]", key in js)
    check("hv1_probe_read_only", not any(t in js for t in ('.click(', 'dispatchEvent', 'fetch(', 'scrollIntoView',
                                                          'location.assign', 'location.href =')))
    check("hv1_node_available", bool(NODE))
    if not NODE:
        return
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8', dir=_HV1_TMP) as f:
        f.write(js)
    p = subprocess.run([NODE, '--check', f.name], capture_output=True, text=True, timeout=30)
    check("hv1_probe_js_parses", p.returncode == 0)
    pdp = {'href': 'https://www.target.com/p/x/-/A-111', 'path': '/p/x/-/A-111'}
    r = _probe_node(dict(pdp, body='Press & Hold to confirm you are a human (and not a bot).',
                         present=['px-captcha'], buttons=[[10, 10]], fulfil=0))
    check("hv1_node_px_page", r['px_container'] is True and r['press_hold'] is True and r['hint'] == 'px_markers'
          and pxm.is_px_challenge(r) is True and 'px_container' in pxm.describe(r))
    r = _probe_node(dict(pdp, body='Access to this page has been denied.'))
    check("hv1_node_denied_page_is_px", r['denied'] is True and pxm.is_px_challenge(r) is True)
    r = _probe_node(dict(pdp, body='Pokemon   Tin\n\nShipping  Arrives by Fri', buttons=[[10, 10], [0, 0], [5, 0]],
                         fulfil=2, title='Tin : Target'))
    check("hv1_node_buybox_without_button", r['hint'] == 'buybox_without_button' and r['buttons'] == 3
          and r['buttons_vis'] == 1 and r['fulfil'] == 2 and pxm.is_px_challenge(r) is False
          and r['text'] == 'Pokemon Tin Shipping Arrives by Fri' and r['title'] == 'Tin : Target'
          and r['vis'] == 'visible' and r['ready'] == 'complete' and r['url'] == pdp['href'])
    r = _probe_node(dict(pdp, body='Pokemon Tin', fulfil=0))
    check("hv1_node_no_fulfillment_block", r['hint'] == 'no_fulfillment_block')
    r = _probe_node(dict(pdp, body='   '))
    check("hv1_node_blank_body", r['hint'] == 'blank_body' and r['text'] == '')
    r = _probe_node(dict(pdp, body=None))
    check("hv1_node_null_body", r['hint'] == 'blank_body' and r['press_hold'] is False)
    r = _probe_node({'href': 'https://www.target.com/', 'path': '/', 'body': 'Target home', 'fulfil': 3})
    check("hv1_node_not_a_pdp", r['hint'] == 'not_a_pdp_url' and pxm.is_px_challenge(r) is False)
    r = _probe_node(dict(pdp, body="Something went wrong. We can't find that page.", fulfil=0))
    check("hv1_node_error_copy", r['hint'] == 'error_copy')
    r = _probe_node(dict(pdp, body='This item is sold out', fulfil=1))
    check("hv1_node_unavailable_copy", r['hint'] == 'unavailable_copy')
    r = _probe_node(dict(pdp, body='x' * 500, fulfil=1, vis='hidden'))
    check("hv1_node_text_capped_vis_reported", len(r['text']) == 160 and r['vis'] == 'hidden')
    r = _probe_node({'href': 'https://www.target.com/captcha?trackingId=1', 'path': '/captcha',
                     'body': 'Please verify'})
    check("hv1_node_captcha_url_is_px", pxm.is_px_challenge(r) is True)


def test_hv1_probe_fields():
    s = h.miss_probe_fields(OK_INFO)
    check("hv1_fields_shape", s.startswith('ready=complete vis=visible buttons=40 buttons_vis=12 fulfil=0 ')
          and "hint=no_fulfillment_block text='Pokemon card'" in s)
    check("hv1_fields_garbage", h.miss_probe_fields({'buttons': 'x', 'fulfil': None}).startswith(
        'ready=? vis=? buttons=? buttons_vis=? fulfil=? hint=- '))
    check("hv1_fields_non_dict", h.miss_probe_fields(None) == 'probe_result=NoneType')
    check("hv1_fields_text_capped", len(h.miss_probe_fields({'text': 'y' * 999})) < 260)


def test_hv1_skip_disables_replay():
    def _skip_stub(env_extra):
        ex, _ = _stub_executor()
        env = {'TARGET_SHAPE_HARVEST': '1', 'TARGET_HARVEST_TCINS': '21516452', 'TARGET_HARVEST_SKIP': 'primary'}
        env.update(env_extra)
        ex._harvest_cfg = h.config(env)
        ex._harvest_disabled_reason = ''
        ex._harvest_task = None
        ex._shape_bank.push(HD, now=time.time())
        return ex
    ex = _skip_stub({'TARGET_HARVEST_SKIP_DISABLES_REPLAY': '1'})
    _, out = _captured(ex._start_harvest)
    check("hv1_skip_turns_replay_off", ex._harvest_replay_on is False and ex.harvest_set_ready() is False
          and ex._harvest_task is None and 'banked replay OFF' in out)
    check("hv1_skip_replay_lookup_none", ex._harvest_replay_headers_for('main', dict(HD)) is None
          and ex._shape_bank.count() == 1)
    check("hv1_skip_reads_stuck", ex.harvest_stuck() is True and 'disabled' in ex.harvest_stuck_reason())
    ex2 = _skip_stub({})
    _, out2 = _captured(ex2._start_harvest)
    check("hv1_skip_flag_off_keeps_replay", ex2._harvest_replay_on is True and ex2.harvest_set_ready() is True
          and 'banked replay OFF' not in out2 and 'harvest SKIPPED' in out2)
    ex3 = _skip_stub({'TARGET_HARVEST_SKIP_DISABLES_REPLAY': '1', 'TARGET_HARVEST_SKIP': 'business'})
    _captured(ex3._start_harvest)      # not skipped; no running loop -> no task
    check("hv1_skip_flag_other_account_untouched", ex3._harvest_replay_on is True and ex3.harvest_set_ready() is True)


def test_hv1_miss_probe_and_park():
    ex, _ = _hv1_stub({'TARGET_HARVEST_MISS_PROBE': '1'})
    tab = ProbeTab(dict(PX_INFO))
    ex._harvest_tab = tab
    _, out = _captured(ex._harvest_handle_miss(tab, dict(MISS)))
    check("hv1_px_probe_parks", ex._harvest_hold_reason() == 'PX park' and ex.harvest_stuck() is True
          and 'MISS-PROBE px=yes tcin=111 hits=px_container,press_hold' in out
          and "hint=px_markers" in out and '[PX-CHALLENGE/harvest] primary:' in out and 'PARKED 300s' in out)
    check("hv1_px_no_nav_no_rotate", tab.gets == [] and tab.probes() == 1 and 'no rotate (PX park)' in out
          and ex._harvest_miss == 0)
    check("hv1_px_screenshot_taken", len(ex._shots) == 1 and ex._shots[0].endswith('_px.png')
          and 'harvest_miss_primary_111_' in ex._shots[0])
    check("hv1_px_error_log_one_line", len(_err_lines(ex)) == 1 and '[PX-CHALLENGE/harvest]' in _err_lines(ex)[0])
    check("hv1_px_nav_left_unprobed", getattr(ex, '_harvest_probe_nav_ts', -1.0) != ex._harvest_tab_nav_ts)
    ensured = []

    async def _ensure():
        ensured.append(1)
        return tab
    ex._ensure_harvest_tab = _ensure
    r, _ = _captured(ex._harvest_once())
    check("hv1_parked_harvest_once_returns_early", r is False and ensured == [] and tab.gets == [])
    _captured(ex._harvest_rotate(same=False))
    _captured(ex._harvest_rotate(same=True, allow_live=True))
    check("hv1_parked_rotate_noop", tab.gets == [])
    # Park expired + same PX page -> the nav is probed again and re-parked, but the
    # error_log line stays throttled to one per 10 min.
    ex._harvest_px_parked_until = time.time() - 1
    _, out = _captured(ex._harvest_handle_miss(tab, dict(MISS)))
    check("hv1_px_reprobe_after_park", tab.probes() == 2 and ex._harvest_hold_reason() == 'PX park'
          and '(#2)' in out and len(_err_lines(ex)) == 1)
    ex._harvest_px_parked_until = time.time() - 1
    ex._harvest_px_alert_ts = time.time() - 601
    _captured(ex._harvest_handle_miss(tab, dict(MISS)))
    check("hv1_px_error_log_after_10min", len(_err_lines(ex)) == 2)
    ex._harvest_px_parked_until = time.time() - 1
    check("hv1_park_expiry_clears_hold", ex._harvest_hold_reason() == '')
    # Non-PX page: one probe per navigation, then the normal 2nd-miss rotate.
    ex2, _ = _hv1_stub({'TARGET_HARVEST_MISS_PROBE': '1'})
    tab2 = ProbeTab(dict(OK_INFO))
    ex2._harvest_tab = tab2
    _, out1 = _captured(ex2._harvest_handle_miss(tab2, dict(MISS)))
    check("hv1_nonpx_no_park", ex2._harvest_hold_reason() == '' and 'MISS-PROBE px=no' in out1
          and 'buttons=40 buttons_vis=12 fulfil=0 hint=no_fulfillment_block' in out1
          and 'miss #1) — will retry' in out1 and ex2._harvest_probe_nav_ts == ex2._harvest_tab_nav_ts)
    check("hv1_nonpx_screenshot_plain", len(ex2._shots) == 1 and not ex2._shots[0].endswith('_px.png')
          and _err_lines(ex2) == [])
    _, out2 = _captured(ex2._harvest_handle_miss(tab2, dict(MISS)))
    check("hv1_one_probe_per_nav", tab2.probes() == 1 and len(ex2._shots) == 1)
    check("hv1_nonpx_second_miss_rotates", tab2.gets == [h.pdp_url('222')]
          and 'miss #2) — rotating to the next candidate' in out2 and ex2._harvest_miss == 0)
    # Screenshot cap across navigations + hidden tab + probe failure.
    ex3, _ = _hv1_stub({'TARGET_HARVEST_MISS_PROBE': '1', 'TARGET_HARVEST_MISS_SHOTS_MAX': '2'})
    tab3 = ProbeTab(dict(OK_INFO))
    ex3._harvest_tab = tab3
    outs = []
    for i in range(3):
        ex3._harvest_tab_nav_ts = 1000.0 + i
        ex3._harvest_miss = 0
        outs.append(_captured(ex3._harvest_handle_miss(tab3, dict(MISS)))[1])
    check("hv1_screenshot_cap", tab3.probes() == 3 and len(ex3._shots) == 2 and 'screenshot 2/2' in outs[1]
          and 'screenshot' not in outs[2])
    ex4, _ = _hv1_stub({'TARGET_HARVEST_MISS_PROBE': '1'})
    tab4 = ProbeTab(dict(OK_INFO, vis='hidden'))
    ex4._harvest_tab = tab4
    _, out4 = _captured(ex4._harvest_handle_miss(tab4, dict(MISS)))
    check("hv1_hidden_tab_no_screenshot", ex4._shots == [] and 'screenshot skipped (tab vis=hidden)' in out4
          and ex4._harvest_miss_shots == 0 if hasattr(ex4, '_harvest_miss_shots') else ex4._shots == [])
    ex5, _ = _hv1_stub({'TARGET_HARVEST_MISS_PROBE': '1'})
    tab5 = ProbeTab(None, probe_raises=asyncio.TimeoutError())
    ex5._harvest_tab = tab5
    _, out5 = _captured(ex5._harvest_handle_miss(tab5, dict(MISS)))
    check("hv1_probe_failure_marks_nav", 'MISS-PROBE failed' in out5 and ex5._harvest_hold_reason() == ''
          and ex5._harvest_probe_nav_ts == ex5._harvest_tab_nav_ts and ex5._shots == [])
    _captured(ex5._harvest_handle_miss(tab5, dict(MISS)))
    check("hv1_probe_failure_not_retried_same_nav", tab5.probes() == 1)
    ex6, _ = _hv1_stub({'TARGET_HARVEST_MISS_PROBE': '1'})
    tab6 = ProbeTab(['not', 'a', 'dict'])
    ex6._harvest_tab = tab6
    _, out6 = _captured(ex6._harvest_handle_miss(tab6, dict(MISS)))
    check("hv1_probe_garbage_result_safe", 'MISS-PROBE px=no' in out6 and ex6._harvest_hold_reason() == ''
          and ex6._shots == [])
    # Probe flag off: no evaluate at all, no screenshot (prior behaviour).
    ex7, _ = _hv1_stub({})
    tab7 = ProbeTab(dict(PX_INFO))
    ex7._harvest_tab = tab7
    _captured(ex7._harvest_handle_miss(tab7, dict(MISS)))
    check("hv1_probe_flag_off_no_evaluate", tab7.evals == [] and ex7._shots == []
          and ex7._harvest_hold_reason() == '')


class _Clock:
    def __init__(self):
        self.t = 1_800_000_000.0


def test_hv1_harvest_once_px_integration():
    """The real _harvest_once: 9 s readiness poll (fake clock) -> miss -> probe
    -> PX park; no click, no nav."""
    import src.session.purchase_executor as pe_mod
    ex, _ = _hv1_stub({'TARGET_HARVEST_MISS_PROBE': '1'})
    tab = ProbeTab(dict(PX_INFO))
    ex._harvest_tab = tab

    async def _ensure():
        return tab
    ex._ensure_harvest_tab = _ensure
    clicks = []

    async def _click(*a, **k):
        clicks.append(1)
        return (0, 0)
    c = _Clock()
    real_time, real_asyncio = pe_mod.time, pe_mod.asyncio

    class _T:
        def time(self):
            return c.t

        def __getattr__(self, n):
            return getattr(real_time, n)

    class _A:
        async def sleep(self, s, *a, **k):
            c.t += float(s)
            await real_asyncio.sleep(0)

        def __getattr__(self, n):
            return getattr(real_asyncio, n)
    ex._harvest_tab_nav_ts = c.t - 100.0
    saved_click = h.human_click
    pe_mod.time, pe_mod.asyncio = _T(), _A()
    h.human_click = _click
    try:
        r, out = _captured(ex._harvest_once())
    finally:
        pe_mod.time, pe_mod.asyncio = real_time, real_asyncio
        h.human_click = saved_click
    finds = sum(1 for j in tab.evals if j == h.FIND_ATC_BUTTON_JS)
    check("hv1_once_px_parks_without_click", r is False and clicks == [] and tab.gets == []
          and tab.probes() == 1 and finds >= 10 and '[PX-CHALLENGE/harvest]' in out
          and ex._harvest_px_parked_until > c.t)


def test_hv1_renav_live():
    # Flag off, live: the rotate stays a no-op and the line no longer claims it rotates.
    ex, _ = _hv1_stub({}, live=True)
    tab = ProbeTab(dict(OK_INFO))
    ex._harvest_tab = tab
    _captured(ex._harvest_handle_miss(tab, dict(MISS)))
    _, out = _captured(ex._harvest_handle_miss(tab, dict(MISS)))
    check("hv1_renav_off_no_live_rotate", tab.gets == [] and 'miss #2) — rotation deferred (purchase live)' in out
          and 'rotating to the next candidate' not in out and ex._harvest_miss == 0)
    # Flag off, idle: exactly the prior rotate + text.
    ex0, _ = _hv1_stub({}, live=False)
    tab0 = ProbeTab(dict(OK_INFO))
    ex0._harvest_tab = tab0
    _, o1 = _captured(ex0._harvest_handle_miss(tab0, dict(MISS)))
    _, o2 = _captured(ex0._harvest_handle_miss(tab0, dict(MISS)))
    check("hv1_idle_rotate_unchanged",
          'ATC button absent on 111 (ready=complete oos=False; miss #1) — will retry\n' in o1
          and 'ATC button absent on 111 (ready=complete oos=False; miss #2) — rotating to the next candidate\n' in o2
          and tab0.gets == [h.pdp_url('222')] and ex0._harvest_tcin == '222')
    # A found-but-disabled button keeps the prior 'DISABLED' wording.
    ex0d, _ = _hv1_stub({}, live=False)
    tab0d = ProbeTab(dict(OK_INFO))
    ex0d._harvest_tab = tab0d
    _, o1d = _captured(ex0d._harvest_handle_miss(tab0d, {'found': True, 'disabled': True, 'ready': 'complete',
                                                          'oos': True}))
    check("hv1_disabled_text_unchanged",
          'ATC button DISABLED on 111 (ready=complete oos=True; miss #1) — will retry\n' in o1d)
    check("hv1_idle_flag_off_no_backoff_state", ex0._harvest_hold_reason() == ''
          and not getattr(ex0, '_harvest_miss_times', None))
    # Flag on, live, last load 100 s ago: exactly one nav, to the NEXT candidate.
    ex1, _ = _hv1_stub({'TARGET_HARVEST_MISS_RENAV_LIVE': '1'}, live=True)
    tab1 = ProbeTab(dict(OK_INFO))
    ex1._harvest_tab = tab1
    _captured(ex1._harvest_handle_miss(tab1, dict(MISS)))
    t_before = time.time()
    _, out1 = _captured(ex1._harvest_handle_miss(tab1, dict(MISS)))
    check("hv1_renav_live_one_nav_next_tcin", tab1.gets == [h.pdp_url('222')] and ex1._harvest_tcin == '222'
          and 'rotating to the next candidate (live re-nav; last load' in out1
          and ex1._harvest_last_reload_ts >= t_before and ex1._harvest_clicks_since_nav == 0)
    # Flag on, live, recent load -> deferred.
    ex2, _ = _hv1_stub({'TARGET_HARVEST_MISS_RENAV_LIVE': '1'}, live=True)
    ex2._harvest_tab_nav_ts = time.time() - 5.0
    tab2 = ProbeTab(dict(OK_INFO))
    ex2._harvest_tab = tab2
    _captured(ex2._harvest_handle_miss(tab2, dict(MISS)))
    _, out2 = _captured(ex2._harvest_handle_miss(tab2, dict(MISS)))
    check("hv1_renav_live_min_gap", tab2.gets == [] and '< 15s)' in out2 and 'rotation deferred (purchase live;' in out2)
    # The gap honours a larger fresh-page min gap, and a recent fresh reload counts.
    ex3, _ = _hv1_stub({'TARGET_HARVEST_MISS_RENAV_LIVE': '1', 'TARGET_HARVEST_FRESH_PAGE_MIN_GAP_S': '40'}, live=True)
    ex3._harvest_tab_nav_ts = time.time() - 20.0
    tab3 = ProbeTab(dict(OK_INFO))
    ex3._harvest_tab = tab3
    _captured(ex3._harvest_handle_miss(tab3, dict(MISS)))
    _, out3 = _captured(ex3._harvest_handle_miss(tab3, dict(MISS)))
    check("hv1_renav_live_uses_larger_gap", tab3.gets == [] and '< 40s)' in out3)
    ex3b, _ = _hv1_stub({'TARGET_HARVEST_MISS_RENAV_LIVE': '1'}, live=True)
    ex3b._harvest_last_reload_ts = time.time() - 3.0
    tab3b = ProbeTab(dict(OK_INFO))
    ex3b._harvest_tab = tab3b
    _captured(ex3b._harvest_handle_miss(tab3b, dict(MISS)))
    _captured(ex3b._harvest_handle_miss(tab3b, dict(MISS)))
    check("hv1_renav_live_recent_reload_counts", tab3b.gets == [])
    # Won-cart loop (quiet) and held cart: never a live nav.
    ex4, _ = _hv1_stub({'TARGET_HARVEST_MISS_RENAV_LIVE': '1'}, live=True)
    ex4._woncart_active_until = time.time() + 100.0
    tab4 = ProbeTab(dict(OK_INFO))
    ex4._harvest_tab = tab4
    _captured(ex4._harvest_handle_miss(tab4, dict(MISS)))
    _, out4 = _captured(ex4._harvest_handle_miss(tab4, dict(MISS)))
    _captured(ex4._harvest_rotate(same=False, allow_live=True))
    check("hv1_renav_refused_during_woncart_loop", tab4.gets == [] and 'rotation deferred (won-cart loop)' in out4)
    ex5, _ = _hv1_stub({'TARGET_HARVEST_MISS_RENAV_LIVE': '1'}, live=True)
    ex5._held_cart = {'tcin': '999', 'created': time.time()}
    tab5 = ProbeTab(dict(OK_INFO))
    ex5._harvest_tab = tab5
    _captured(ex5._harvest_handle_miss(tab5, dict(MISS)))
    _, out5 = _captured(ex5._harvest_handle_miss(tab5, dict(MISS)))
    check("hv1_renav_refused_with_held_cart", tab5.gets == [] and 'held won cart' in out5)
    # The rotate primitive itself: allow_live bypasses only the live guard.
    ex6, _ = _hv1_stub({}, live=True)
    ex6._harvest_tab = ProbeTab(dict(OK_INFO))
    _captured(ex6._harvest_rotate(same=True))
    check("hv1_rotate_default_live_noop", ex6._harvest_tab.gets == [])
    _captured(ex6._harvest_rotate(same=True, allow_live=True))
    check("hv1_rotate_allow_live_navigates_same", ex6._harvest_tab.gets == [h.pdp_url('111')])


def test_hv1_backoff():
    ex, _ = _hv1_stub({'TARGET_HARVEST_MISS_RENAV_LIVE': '1'}, live=False)
    tab = ProbeTab(dict(OK_INFO))
    ex._harvest_tab = tab
    _captured(ex._harvest_handle_miss(tab, dict(MISS)))
    _captured(ex._harvest_handle_miss(tab, dict(MISS)))
    check("hv1_backoff_not_before_3", ex._harvest_hold_reason() == '' and tab.gets == [h.pdp_url('222')])
    _captured(ex._harvest_handle_miss(tab, dict(MISS)))
    _, out = _captured(ex._harvest_handle_miss(tab, dict(MISS)))
    check("hv1_backoff_after_3_in_600s", ex._harvest_hold_reason() == 'bad-load back-off'
          and ex._harvest_backoff_until > time.time() + 290 and 'rotation deferred (bad-load back-off)' in out
          and tab.gets == [h.pdp_url('222')] and ex.harvest_stuck() is True)
    ensured = []

    async def _ensure():
        ensured.append(1)
        return tab
    ex._ensure_harvest_tab = _ensure
    r, _ = _captured(ex._harvest_once())
    check("hv1_backoff_harvest_once_early", r is False and ensured == [])
    ex._harvest_backoff_until = time.time() - 1
    check("hv1_backoff_expires", ex._harvest_hold_reason() == '')
    # Window: misses > 600 s apart never add up.
    ex2, _ = _hv1_stub({'TARGET_HARVEST_MISS_RENAV_LIVE': '1', 'TARGET_HARVEST_BADLOAD_BACKOFF_S': '120'})
    base = time.time() - 5000
    r1 = [ex2._harvest_note_bad_load(base + d) for d in (0, 400, 1100)]
    check("hv1_backoff_window_600s", r1 == [False, False, False] and ex2._harvest_backoff_until == 0.0
          if hasattr(ex2, '_harvest_backoff_until') else r1 == [False, False, False])
    now = time.time()
    r2 = [ex2._harvest_note_bad_load(now - 20), ex2._harvest_note_bad_load(now - 10), ex2._harvest_note_bad_load(now)]
    check("hv1_backoff_knob", r2 == [False, False, True] and abs(ex2._harvest_backoff_until - (now + 120)) < 1)
    # Flag off: many misses, never a back-off.
    ex3, _ = _hv1_stub({}, live=False)
    tab3 = ProbeTab(dict(OK_INFO))
    ex3._harvest_tab = tab3
    for _ in range(6):
        _captured(ex3._harvest_handle_miss(tab3, dict(MISS)))
    check("hv1_backoff_flag_off", ex3._harvest_hold_reason() == '' and len(tab3.gets) == 3)


def test_hv1_harvest_stuck():
    import src.session.purchase_executor as pe_mod
    bare = pe_mod.PurchaseExecutor.__new__(pe_mod.PurchaseExecutor)
    check("hv1_stuck_bare_object_false", bare.harvest_stuck() is False and bare.harvest_stuck_reason() == ''
          and bare._harvest_hold_reason() == '')
    ex, _ = _hv1_stub({})
    check("hv1_stuck_fresh_false", ex.harvest_stuck() is False)
    tab = ProbeTab(dict(OK_INFO))
    ex._harvest_tab = tab
    _captured(ex._harvest_handle_miss(tab, dict(MISS)))
    check("hv1_stuck_one_miss_false", ex._harvest_miss_streak == 1 and ex.harvest_stuck() is False)
    _captured(ex._harvest_handle_miss(tab, dict(MISS)))
    _captured(ex._harvest_handle_miss(tab, dict(MISS)))
    check("hv1_stuck_streak_survives_rotate", ex._harvest_miss_streak == 3 and ex.harvest_stuck() is True
          and '3 buttonless loads' in ex.harvest_stuck_reason())
    ev = _event({'Cookie': 'c', 'Content-Type': 'application/json'})
    _captured(ex._harvest_capture_and_block(FakeTab(), ev, ev.request.url))
    check("hv1_stuck_tokenless_capture_keeps_streak", ex._harvest_miss_streak == 3)
    ev2 = _event(dict(HD))
    _captured(ex._harvest_capture_and_block(FakeTab(), ev2, ev2.request.url))
    check("hv1_stuck_capture_resets", ex._harvest_miss_streak == 0 and ex.harvest_stuck() is False)
    ex._harvest_px_parked_until = time.time() + 60
    check("hv1_stuck_parked", ex.harvest_stuck_reason() == 'PX park')
    ex._harvest_px_parked_until = 0.0
    ex._harvest_backoff_until = time.time() + 60
    check("hv1_stuck_backoff", ex.harvest_stuck_reason() == 'bad-load back-off')
    ex._harvest_backoff_until = 0.0
    ex._harvest_disabled_reason = 'no TARGET_HARVEST_TCINS'
    check("hv1_stuck_disabled", ex.harvest_stuck_reason() == 'disabled (no TARGET_HARVEST_TCINS)')
    ex._harvest_disabled_reason = ''
    ex._harvest_px_parked_until = 'garbage'
    check("hv1_stuck_never_raises", ex.harvest_stuck() is False)


def test_hv1_flush_on_relaunch():
    def _flush_stub(flag):
        ex, _ = _stub_executor()
        env = {'TARGET_SHAPE_HARVEST': '1', 'TARGET_HARVEST_TCINS': ''}
        if flag:
            env['TARGET_HARVEST_FLUSH_ON_RELAUNCH'] = '1'
        ex._harvest_cfg = h.config(env)
        ex._harvest_tab = None
        ex._harvest_vis_state = ''
        ex._harvest_tcin_idx = 0
        old, new = SimpleNamespace(name='old'), SimpleNamespace(name='new')
        ex._harvest_browser_ref = old
        ex.session_manager.browser = new
        ex._shape_bank.push(HD, {'tcin': 'a'}, now=time.time())
        ex._shape_bank.push(HD, {'tcin': 'b'}, now=time.time())
        return ex, old, new
    ex, old, new = _flush_stub(True)
    r, out = _captured(lambda: ex._harvest_replay_headers_for('main', dict(HD)))
    check("hv1_flush_at_replay", r is None and ex._shape_bank.count() == 0 and ex._shape_bank.expired == 2
          and 'bank FLUSHED on relaunch (replay): dropped 2 set(s)' in out and 'bank EMPTY at shot time' in out)
    ex.session_manager.browser = old
    ex._shape_bank.push(HD, now=time.time())
    r2, _ = _captured(lambda: ex._harvest_replay_headers_for('main', dict(HD)))
    check("hv1_same_browser_replays", isinstance(r2, list))
    exoff, _, _ = _flush_stub(False)
    r3, out3 = _captured(lambda: exoff._harvest_replay_headers_for('main', dict(HD)))
    check("hv1_flush_flag_off_replays_old_set", isinstance(r3, list) and 'FLUSHED' not in out3
          and exoff._shape_bank.count() == 1)
    exw, _, _ = _flush_stub(True)
    exw._harvest_selftest_armed = False
    _captured(lambda: exw._harvest_replay_headers_for('warmup', dict(HD)))
    check("hv1_flush_not_run_for_unarmed_labels", exw._shape_bank.count() == 2)
    # The harvest tab's browser-changed branch.
    ex2, old2, new2 = _flush_stub(True)
    r4, out4 = _captured(ex2._ensure_harvest_tab())
    check("hv1_flush_on_browser_changed", r4 is None and ex2._shape_bank.count() == 0
          and ex2._harvest_browser_ref is new2 and 'browser changed' in out4
          and 'bank FLUSHED on relaunch (browser changed)' in out4)
    ex3, _, new3 = _flush_stub(False)
    _captured(ex3._ensure_harvest_tab())
    check("hv1_browser_changed_flag_off_keeps_bank", ex3._shape_bank.count() == 2 and ex3._harvest_browser_ref is new3)
    ex4, _, new4 = _flush_stub(True)
    ex4._harvest_browser_ref = None
    _captured(ex4._ensure_harvest_tab())
    check("hv1_first_attach_no_flush", ex4._shape_bank.count() == 2 and ex4._harvest_browser_ref is new4)
    ex5, _, _ = _flush_stub(True)
    ex5.session_manager.browser = None          # teardown in progress = the minting Chrome is gone
    check("hv1_flush_when_browser_torn_down", ex5._harvest_flush_if_relaunched('replay') == 2)


def test_hv1_bank_gate_adaptive():
    from src.purchasing.bulletproof_purchase_manager import BulletproofPurchaseManager as BPM
    m = object.__new__(BPM)

    class Exe:
        def __init__(self, why=''):
            self.why = why

        def harvest_stuck_reason(self):
            if self.why == 'raise':
                raise RuntimeError('boom')
            return self.why
    os.environ.pop('TARGET_BANK_GATE_ADAPTIVE', None)
    try:
        check("hv1_gate_flag_off_never_skips", m._bank_gate_skip_reason(Exe('stuck'), 'W3/alt-1') == '')
        m._bank_gate_note('W3/alt-1', False, 8.0)
        m._bank_gate_note('W3/alt-1', False, 8.0)
        check("hv1_gate_flag_off_note_noop", getattr(m, '_bank_gate_timeouts', None) is None
              and m._bank_gate_skip_reason(Exe(), 'W3/alt-1') == '')
        os.environ['TARGET_BANK_GATE_ADAPTIVE'] = '1'
        check("hv1_gate_skips_when_harvest_stuck",
              m._bank_gate_skip_reason(Exe('PX park'), 'W3/alt-1') == 'harvest stuck: PX park')
        check("hv1_gate_waits_when_healthy", m._bank_gate_skip_reason(Exe(''), 'W3/alt-1') == '')
        m._bank_gate_note('W3/alt-1', False, 8.0)
        check("hv1_gate_one_timeout_still_waits", m._bank_gate_skip_reason(Exe(), 'W3/alt-1') == '')
        m._bank_gate_note('W3/alt-1', False, 0.0)          # a 0 s check is not a timeout
        check("hv1_gate_zero_wait_not_counted", m._bank_gate_timeouts.get('W3/alt-1') == 1)
        m._bank_gate_note('W3/alt-1', False, 8.0)
        check("hv1_gate_skips_after_2_timeouts",
              m._bank_gate_skip_reason(Exe(), 'W3/alt-1') == '2 bank-gate timeouts in a row')
        check("hv1_gate_counts_per_identity", m._bank_gate_skip_reason(Exe(), 'W2/business') == '')
        _, out = _captured(lambda: m._bank_gate_note('W3/alt-1', True, 0.0))
        check("hv1_gate_resets_on_ready", m._bank_gate_skip_reason(Exe(), 'W3/alt-1') == ''
              and 'gate wait re-armed ident=W3/alt-1' in out)
        m._bank_gate_note(None, False, 8.0)
        m._bank_gate_note(None, False, 8.0)
        check("hv1_gate_none_label_is_auto", m._bank_gate_skip_reason(Exe(), None) == '2 bank-gate timeouts in a row'
              and m._bank_gate_timeouts.get('auto') == 2)
        check("hv1_gate_executor_errors_safe", m._bank_gate_skip_reason(Exe('raise'), 'W1/primary') == ''
              and m._bank_gate_skip_reason(object(), 'W1/primary') == ''
              and m._bank_gate_skip_reason(None, 'W1/primary') == '')
        os.environ['TARGET_BANK_GATE_ADAPTIVE'] = '0'
        check("hv1_gate_kill_switch", m._bank_gate_skip_reason(Exe('PX park'), None) == '')
    finally:
        os.environ.pop('TARGET_BANK_GATE_ADAPTIVE', None)
    # Wiring (source order inside the gate block).
    i_gate = MGR_SRC.find("os.environ.get('TARGET_SHOT_BANK_GATE', '0') == '1' and _gk in _gate_kinds")
    i_bw = MGR_SRC.find("_bw = min(_bw, max(0.0, _retry_deadline - time.time()))", i_gate)
    i_skip = MGR_SRC.find("_bg_skip = self._bank_gate_skip_reason(target_purchase_executor, _race_wlbl)", i_bw)
    i_zero = MGR_SRC.find("if _bg_skip:\n                                _bw = 0.0", i_skip)
    i_wait = MGR_SRC.find("target_purchase_executor.harvest_wait_for_set(_bw)", i_zero)
    i_note = MGR_SRC.find("self._bank_gate_note(_race_wlbl, bool(_have), _bw)", i_wait)
    i_ready = MGR_SRC.find("[BANK_GATE] fresh banked set ready", i_note)
    i_skwf = MGR_SRC.find("elif _bg_skip and _wf_only:", i_ready)
    i_wf = MGR_SRC.find("elif _wf_only:", i_skwf)
    check("hv1_gate_wiring_order", 0 < i_gate < i_bw < i_skip < i_zero < i_wait < i_note < i_ready < i_skwf < i_wf)
    check("hv1_gate_note_skipped_on_wait_error", "if not _bg_errored:\n                                self._bank_gate_note(" in MGR_SRC
          and "_bg_errored = True" in MGR_SRC)
    check("hv1_gate_skip_lines", MGR_SRC.count("[BANK_GATE] skipped ({_bg_skip}) — no wait;") == 2)
    check("hv1_gate_prior_lines_kept", "[BANK_GATE] no fresh set within {_bw:.0f}s — cold re-entry fires page-signed" in MGR_SRC
          and "[BANK_GATE] no fresh set within {_bw:.0f}s after a 401 — wave-first " in MGR_SRC
          and "self._bank_gate_timeouts: Dict[str, int] = {}" in MGR_SRC)


def test_hv1_wiring():
    i_def = EXE_SRC.find("async def _harvest_once(self) -> bool:")
    i_quiet = EXE_SRC.find("if self._woncart_quiet():", i_def)
    i_hold = EXE_SRC.find("if self._harvest_hold_reason():", i_def)
    i_ens = EXE_SRC.find("tab = await self._ensure_harvest_tab()", i_def)
    i_miss = EXE_SRC.find("await self._harvest_handle_miss(tab, info)", i_def)
    i_vis = EXE_SRC.find("_vis = await self._harvest_ensure_visible(tab)", i_def)
    check("hv1_once_order", 0 < i_def < i_quiet < i_hold < i_ens < i_miss < i_vis)
    check("hv1_old_misleading_text_gone",
          "'rotating to the next candidate' if self._harvest_miss >= 2 else 'will retry'" not in EXE_SRC)
    i_rdef = EXE_SRC.find("async def _harvest_rotate(self, same: bool = False, allow_live: bool = False)")
    i_rq = EXE_SRC.find("if self._woncart_quiet():", i_rdef)
    i_rh = EXE_SRC.find("if self._harvest_hold_reason():", i_rdef)
    i_rl = EXE_SRC.find("if self.session_manager.is_purchase_in_progress() and not allow_live:", i_rdef)
    check("hv1_rotate_guard_order", 0 < i_rdef < i_rq < i_rh < i_rl)
    check("hv1_only_miss_path_passes_allow_live",
          EXE_SRC.count("allow_live=allow_live)") == 1 and EXE_SRC.count("allow_live=True") == 0)
    i_bc = EXE_SRC.find('self._harvest_log("browser changed — dropping harvest tab handle")')
    i_fl = EXE_SRC.find("self._harvest_flush_if_relaunched('browser changed')", i_bc)
    i_dr = EXE_SRC.find('await self._harvest_drop_tab("browser changed", close=False)', i_bc)
    check("hv1_flush_in_browser_changed_branch", 0 < i_bc < i_fl < i_dr)
    i_lbl = EXE_SRC.find("elif label == 'warmup' and self._harvest_selftest_armed:")
    i_rfl = EXE_SRC.find("self._harvest_flush_if_relaunched('replay')", i_lbl)
    i_pop = EXE_SRC.find("entry = self._shape_bank.pop_fresh(", i_lbl)
    check("hv1_flush_before_replay_pop", 0 < i_lbl < i_rfl < i_pop)
    i_sk = EXE_SRC.find("if _acct in (cfg.get('skip') or []):")
    i_sr = EXE_SRC.find("if cfg.get('skip_disables_replay') and self._harvest_replay_on:", i_sk)
    i_ret = EXE_SRC.find("return", i_sr)
    i_task = EXE_SRC.find("if self._harvest_task is not None and not self._harvest_task.done():", i_sk)
    check("hv1_skip_branch_turns_replay_off", 0 < i_sk < i_sr < i_ret < i_task)
    i_push = EXE_SRC.find("ok = self._shape_bank.push(headers, {'tcin': self._harvest_tcin")
    i_rst = EXE_SRC.find("self._harvest_miss_streak = 0", i_push)
    i_capt = EXE_SRC.find('self._harvest_log(f"CAPTURED', i_push)
    check("hv1_streak_reset_at_captured_push", 0 < i_push < i_rst < i_capt)
    check("hv1_probe_bounded", "tab.evaluate(_shape_harvest.HARVEST_MISS_PROBE_JS), timeout=3.0)" in EXE_SRC
          and "await asyncio.wait_for(self._screenshot(tab, path), timeout=5.0)" in EXE_SRC)
    check("hv1_px_alert_own_tag_not_auth_critical",
          "[PX-CHALLENGE/harvest]" in EXE_SRC and "_alert_critical" not in
          EXE_SRC[EXE_SRC.find("def _harvest_px_park"):EXE_SRC.find("def _harvest_note_bad_load")])


if __name__ == '__main__':
    for fn in (test_prefix_and_tokens, test_merge, test_bank, test_bezier_and_click_point, test_config_and_js,
               test_header_bytes,
               test_human_click_events, test_visibility_probe_and_verdict, test_executor_replay_lookup,
               test_executor_capture_and_block, test_executor_visibility_guard, test_executor_fresh_page,
               test_executor_wiring, test_bat_pins, test_compiles,
               # 2026-09-16 HV-1
               test_hv1_env_clean, test_hv1_config, test_hv1_bank_clear, test_hv1_probe_js,
               test_hv1_probe_fields, test_hv1_skip_disables_replay, test_hv1_miss_probe_and_park,
               test_hv1_harvest_once_px_integration, test_hv1_renav_live, test_hv1_backoff,
               test_hv1_harvest_stuck, test_hv1_flush_on_relaunch, test_hv1_bank_gate_adaptive,
               test_hv1_wiring):
        try:
            fn()
        except Exception as e:
            FAIL += 1
            import traceback
            print(f"[FAIL] {fn.__name__} raised {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(0 if FAIL == 0 else 1)

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


def test_bezier_and_click_point():
    rng = random.Random(7)
    pts = h.bezier_path(100, 100, 500, 400, rng)
    check("bezier_min_points", len(pts) >= 5)
    check("bezier_ends_on_target", pts[-1][0] == 500.0 and pts[-1][1] == 400.0)
    # curvature: max perpendicular deviation from the straight line > 2px
    dx, dy = 400.0, 300.0
    L = math.hypot(dx, dy)
    dev = max(abs((x - 100) * dy - (y - 100) * dx) / L for x, y, _ in pts[:-1])
    check("bezier_is_curved", dev > 2.0)
    check("bezier_dt_bounds", all(0.004 <= dt <= 0.13 for _, _, dt in pts))
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
    js = h.FIND_ATC_BUTTON_JS
    check("js_selectors", 'shippingButton' in js and 'addToCartButton' in js and 'shipItButton' in js)
    check("js_scrolls_into_view", 'scrollIntoView' in js)
    check("js_reports_oos_and_disabled", "oos:" in js and 'aria-disabled' in js)
    check("pdp_url", h.pdp_url('123') == 'https://www.target.com/p/-/A-123')


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
    check("click_moves_then_press_release", kinds.count('mouseMoved') >= 5
          and kinds[-2:] == ['mousePressed', 'mouseReleased'])
    check("click_method_is_input_domain", all(c['method'] == 'Input.dispatchMouseEvent' for _, c in tab.events))
    t_press = [t for t, c in tab.events if c['params']['type'] == 'mousePressed'][0]
    t_rel = [t for t, c in tab.events if c['params']['type'] == 'mouseReleased'][0]
    check("click_hold_60_130ms", 0.05 <= (t_rel - t_press) <= 0.20)
    last = [c for _, c in tab.events if c['params']['type'] == 'mouseMoved'][-1]['params']
    check("click_last_move_on_target", last['x'] == 300 and last['y'] == 200)


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
    check("replay_records_last", ex._harvest_last_replay and ex._harvest_last_replay['label'] == 'warmup')
    ex._harvest_selftest_armed = False
    ex._shape_bank.push(bank, {'tcin': '21516452'})
    check("replay_harvest_label_none", ex._harvest_replay_headers_for('harvest', dict(HD)) is None)
    ex._harvest_replay_on = False
    check("replay_off_none_and_bank_kept",
          ex._harvest_replay_headers_for('main', dict(HD)) is None and ex._shape_bank.count() == 1)


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


# ---------------------------------------------------------------------------
# 4. Executor wiring (source pins)
# ---------------------------------------------------------------------------
def test_executor_wiring():
    check("exe_imports_module", "from . import shape_harvest as _shape_harvest" in EXE_SRC)
    check("exe_init_state", "self._shape_bank = _shape_harvest.ShapeBank(" in EXE_SRC
          and "self._harvest_replay_on: bool = bool(self._harvest_cfg['enabled'] and self._harvest_cfg['replay'])" in EXE_SRC)
    check("exe_interceptor_label_param", "label: Optional[str] = None) -> None:" in EXE_SRC
          and 'label = label or ("warmup" if persistent else "main")' in EXE_SRC)
    i_branch = EXE_SRC.find("if (label == 'harvest' and not is_response and method == 'POST'")
    i_call = EXE_SRC.find("await self._harvest_capture_and_block(tab, event, url)", i_branch)
    i_ret = EXE_SRC.find("return", i_call)
    i_generic = EXE_SRC.find("if 'carts.target.com' in url or 'cart_items' in url:")
    check("exe_harvest_branch_before_generic_and_returns", 0 < i_branch < i_call < i_ret < i_generic)
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
    check("exe_rotate_skips_live_purchase",
          "if self.session_manager.is_purchase_in_progress():\n            return" in EXE_SRC)
    check("exe_pre_shot_log_before_fast_lane",
          EXE_SRC.find('self._harvest_log(f"pre-shot') < EXE_SRC.find("_fl = await self._api_fast_lane(tab, tcin, quantity, extra_headers_js)"))
    check("exe_start_harvest_loud_without_tcins", "DISABLED: TARGET_HARVEST_TCINS is empty" in EXE_SRC)
    check("exe_real_atc_shape_logged_once", "REAL_ATC_SHAPE url=" in EXE_SRC)


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


if __name__ == '__main__':
    for fn in (test_prefix_and_tokens, test_merge, test_bank, test_bezier_and_click_point, test_config_and_js,
               test_human_click_events, test_executor_replay_lookup, test_executor_capture_and_block,
               test_executor_wiring, test_bat_pins, test_compiles):
        try:
            fn()
        except Exception as e:
            FAIL += 1
            import traceback
            print(f"[FAIL] {fn.__name__} raised {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(0 if FAIL == 0 else 1)

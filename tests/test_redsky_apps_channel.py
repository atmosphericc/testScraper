#!/usr/bin/env python3
"""2026-09-09 detection + shot-path audit fixes. No browser, no network.

Pins: src/monitoring/redsky_channel.py (pure), the raw app-channel read in
tab_dispatcher, the trusted-reader await_promise fix + captcha park in
stock_monitor, the ShapeBank stale-discard/refill logic, the executor wiring
(dedup key per tab, empty-header guard, ATC byte-match URL, window census,
checkout body gate, shipping cell), and the bat pins (+ CRLF).

Run: python tests/test_redsky_apps_channel.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.monitoring import redsky_channel as rc   # noqa: E402
from src.session import shape_harvest as h         # noqa: E402

EXE_SRC = (ROOT / 'src' / 'session' / 'purchase_executor.py').read_text(encoding='utf-8', errors='replace')
DISP_SRC = (ROOT / 'src' / 'monitoring' / 'tab_dispatcher.py').read_text(encoding='utf-8', errors='replace')
MON_SRC = (ROOT / 'src' / 'monitoring' / 'stock_monitor.py').read_text(encoding='utf-8', errors='replace')
BAT_RAW = (ROOT / 'run_bot_with_nightly_restart.bat').read_bytes()
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


def _bat_val(name):
    import re
    m = re.search(r'^set ' + re.escape(name) + r'=(.*)$', BAT_SRC, re.M)
    return (m.group(1).strip() if m else None)


HD = {'Cookie': 'c=1', 'User-Agent': 'UA', 'Content-Type': 'application/json',
      'X-GyJwza5Z-a': 'A' * 120, 'X-GyJwza5Z-b': 'B', 'X-GyJwza5Z-c': 'C',
      'X-GyJwza5Z-d': 'D', 'X-GyJwza5Z-f': 'F', 'X-GyJwza5Z-z': 'Z'}


def test_channel_helper():
    check("mode_default_web", rc.mode({}) == 'web' and rc.is_raw({}) is False)
    check("mode_apps_raw", rc.mode({'RESILIENT_REDSKY_CHANNEL': 'apps_raw'}) == 'apps_raw')
    check("mode_apps_alias", rc.mode({'RESILIENT_REDSKY_CHANNEL': 'APPS '}) == 'apps_raw')
    check("mode_garbage_is_web", rc.mode({'RESILIENT_REDSKY_CHANNEL': 'bogus'}) == 'web')
    # browser fetches ALWAYS stay on the web aggregation (apps-in-browser is captcha'd / preflight-blocked)
    check("browser_url_is_always_web", rc.bulk_url({'RESILIENT_REDSKY_CHANNEL': 'apps_raw'}) == rc.WEB_URL
          and rc.extra_headers_js({'RESILIENT_REDSKY_CHANNEL': 'apps_raw'}) == "")
    hdr = rc.raw_headers()
    check("raw_headers_app_set", hdr['x-channel-id'] == 'APPS' and hdr['x-client-platform'] == 'iPhone'
          and hdr['user-agent'].startswith('Target/') and 'accept' in hdr)
    url = rc.raw_apps_url(['1', '22'], '865')
    check("raw_url_shape", url.startswith(rc.APPS_URL + '?') and 'tcins=1%2C22' in url
          and f'key={rc.APPS_KEY}' in url and 'store_id=865' in url and 'pricing_store_id=865' in url)
    check("raw_url_no_bust_by_default", '_=' not in url)
    check("raw_url_cache_bust", '&_=' in rc.raw_apps_url(['1'], '865', cache_bust=True))


def test_dispatcher_wiring():
    check("disp_raw_branch_in_sweep", "if _rc.is_raw():\n            return await self._fire_raw_on(s, chunk)" in DISP_SRC)
    check("disp_raw_branch_in_verify", "return await self._fire_raw_on(s, list(tcins), cache_bust=True)" in DISP_SRC)
    check("disp_raw_runs_in_thread", "asyncio.to_thread(self._raw_apps_get, url, _rc.raw_headers(), proxy," in DISP_SRC)
    check("disp_raw_uses_session_forwarder", 'else f"http://127.0.0.1:{s.local_port}")' in DISP_SRC
          and "getattr(self.session_pool, 'harvest_via_local_ip', False)" in DISP_SRC)
    check("disp_raw_direct_without_forwarder", "if proxy else {}" in DISP_SRC)
    check("disp_raw_reuses_interpreter", '{"__http_status": status, "__body": body, "__body_text": (text or "")[:800]}' in DISP_SRC)
    check("disp_raw_http_error_body_kept", "except urllib.error.HTTPError as e:" in DISP_SRC)
    check("disp_raw_bounded", "timeout=self.tab_eval_timeout_s + 2.0" in DISP_SRC)
    check("disp_raw_404_rate_park", "RESILIENT_RAW_404_PARK_S" in DISP_SRC and "s.rate_parked_until = time.time() + _park" in DISP_SRC
          and "s.recent_4xx.pop()" in DISP_SRC and 'elif status == 404 and \'"Not Found"\' in (text or \'\'):' in DISP_SRC
          and "_park = _base * min(8.0, 2.0 ** (s.rate_parks - 1))" in DISP_SRC and "s.rate_parks = 0" in DISP_SRC)
    check("disp_raw_404_needs_prior_200", "s.raw_ok_seen = True" in DISP_SRC
          and "and not getattr(s, 'raw_ok_seen', False):" in DISP_SRC and "[STOCK][RAW-404]" in DISP_SRC
          and "sessions rate-parked" in DISP_SRC)
    check("shipping_js_never_clicks_buttons", "/button$/.test(dt)" in h.SELECT_SHIPPING_JS and "dt.includes('addtocart')" in h.SELECT_SHIPPING_JS)
    check("disp_raw_cache_bust_off_by_default", "RESILIENT_RAW_CACHE_BUST" in DISP_SRC)
    POOL_SRC = (ROOT / 'src' / 'session' / 'multi_session_pool.py').read_text(encoding='utf-8', errors='replace')
    check("pool_rate_park_field", "rate_parked_until: float = 0.0" in POOL_SRC)
    check("pool_pick_honors_rate_park", "and s.rate_parked_until <= now]" in POOL_SRC)
    check("pool_usable_honors_rate_park", "and s.rate_parked_until <= t)" in POOL_SRC)


def test_trusted_reader_fixes():
    check("reader_awaits_promise", "return await tab.evaluate(js, await_promise=True)" in MON_SRC)
    check("reader_no_unawaited_evaluate", "return await tab.evaluate(js)\n" not in MON_SRC)
    check("reader_park_on_captcha", "self._trusted_reader_parked_until = _now + _park_s" in MON_SRC
          and "RESILIENT_TRUSTED_READER_PARK_S" in MON_SRC)
    check("reader_park_short_circuits", "if time.time() < _parked_until:" in MON_SRC)
    check("reader_url_from_channel_helper", "const url = new URL('{_bulk_url}');" in MON_SRC)


def test_bank_stale_and_refill():
    import os
    prev = os.environ.pop('TARGET_HARVEST_MAX_REPLAY_AGE_S', None)
    try:
        b = h.ShapeBank(size=1, ttl_s=300.0)
        b.push(HD, {'tcin': '1'}, now=0.0)
        check("bank_replay_cap_default_100", h.ShapeBank.max_replay_age() == 100.0)
        check("bank_fresh_pops", b.pop_fresh(now=50.0) is not None and b.count(50.0) == 0)
        b.push(HD, {'tcin': '2'}, now=100.0)
        check("bank_refill_not_wanted_young", b.refill_wanted(now=130.0) is False)
        check("bank_refill_wanted_past_half_cap", b.refill_wanted(now=160.0) is True)
        got = b.pop_fresh(now=250.0)          # 150 s old > 100 s cap
        check("bank_stale_discarded_not_returned", got is None and b.count(250.0) == 0 and b.stale == 1)
        check("bank_need_after_discard", b.need(250.0) == 1 and b.refill_wanted(250.0) is True)
        check("bank_summary_has_stale", 'stale=1' in b.summary(250.0))
        os.environ['TARGET_HARVEST_MAX_REPLAY_AGE_S'] = '0'
        b2 = h.ShapeBank(size=1, ttl_s=300.0)
        b2.push(HD, now=0.0)
        check("bank_cap_zero_disables", b2.pop_fresh(now=250.0) is not None)
    finally:
        if prev is None:
            os.environ.pop('TARGET_HARVEST_MAX_REPLAY_AGE_S', None)
        else:
            os.environ['TARGET_HARVEST_MAX_REPLAY_AGE_S'] = prev
    check("sensor_a_len", h.sensor_a_len(HD) == 120 and h.sensor_a_len({'Cookie': 'x'}) == 0)
    js = h.SELECT_SHIPPING_JS
    check("shipping_js_shape", 'fulfillment' in js and '.click()' in js and 'add to cart' in js
          and 'aria-pressed' in js)
    check("cfg_prefer_shipping_default_on", h.config({})['prefer_shipping'] is True
          and h.config({'TARGET_HARVEST_PREFER_SHIPPING': '0'})['prefer_shipping'] is False)


def test_executor_wiring():
    check("exe_dedup_key_per_tab", 'dedup_key = f"{label}:{req_id}" + (\':resp\' if is_response else \':req\')' in EXE_SRC)
    check("exe_override_needs_headers", "and headers   # 2026-09-09 audit #9" in EXE_SRC)
    check("exe_merge_guard_non_shape_subset", "if not _non_shape_req.issubset(_merged_names):" in EXE_SRC
          and "if len(merged) < len(req_headers or {}):" not in EXE_SRC)
    check("exe_bytematch_url", "if os.environ.get('TARGET_ATC_BYTEMATCH', '0') == '1':" in EXE_SRC
          and "'?field_groups=CART%2CCART_ITEMS%2CSUMMARY'" in EXE_SRC
          and "'&key=9f36aeafbe60771e321a7cc95a78140772ab3e96')" in EXE_SRC)
    # body byte-match: the page's shipping add carries NO fulfillment field and this key order
    check("exe_bytematch_body_helper_present", "def fast_lane_atc_body_literal(tcin, quantity, bytematch: bool) -> str:" in EXE_SRC)
    check("exe_waiting_room_full_text_flag", "out.atc.wr = /busier than we expected|sorry for the wait/i.test(t);" in EXE_SRC
          and "if atc_result.get('wr') or" in EXE_SRC)
    check("exe_waiting_room_detected", "[WAITING_ROOM] Target queue interstitial" in EXE_SRC
          and "'busier than we expected' in _ab" in EXE_SRC)
    check("exe_window_census", 'window census: shots=' in EXE_SRC and "self._harvest_win['shots'] += 1" in EXE_SRC
          and "self._harvest_win['replayed'] += 1" in EXE_SRC)
    check("exe_refill_gate_uses_stale", "want = need > 0 or self._shape_bank.refill_wanted()" in EXE_SRC
          and "if want and (time.time() - self._harvest_last_click_ts) >= min_gap:" in EXE_SRC)
    check("exe_bank_stale_vs_empty_logs", "bank STALE at shot time" in EXE_SRC and "bank EMPTY at shot time" in EXE_SRC)
    check("exe_checkout_body_gated", "if os.environ.get('TARGET_CHECKOUT_BODY_CAPTURE', '0') == '1':" in EXE_SRC)
    check("exe_shipping_cell_once_per_nav", "self._harvest_ship_nav_ts = self._harvest_tab_nav_ts" in EXE_SRC
          and "tab.evaluate(_shape_harvest.SELECT_SHIPPING_JS)" in EXE_SRC)
    check("exe_capture_logs_a_len", "a_len={_shape_harvest.sensor_a_len(headers)}" in EXE_SRC)
    check("exe_vis_guard_never_raises", "visibility guard errored" in EXE_SRC)
    check("exe_orphans_substring_match", "if url_marker not in str(getattr(t.target, 'url', '') or ''):" in EXE_SRC)


def test_fast_lane_body_js_really_parses():
    """The 2026-09-09 review caught doubled braces in the body literal: the emitted JS
    was `JSON.stringify({{...}})` = SyntaxError = every shot dead. Evaluate the REAL
    literal with node (present on this box) and compare the JSON it produces."""
    import json, shutil, subprocess
    from src.session.purchase_executor import fast_lane_atc_body_literal as lit
    for bm in (True, False):
        s = lit('1011960739', 2, bytematch=bm)
        check(f"body_literal_no_doubled_braces_bm{int(bm)}", '{{' not in s and '}}' not in s
              and s.count('{') == s.count('}') == 2)
    node = shutil.which('node')
    check("node_available_for_js_eval", node is not None)
    if node:
        for bm, expect in ((True, {"cart_item": {"item_channel_id": "10", "tcin": "1011960739", "quantity": 2},
                                   "cart_type": "REGULAR", "channel_id": "10", "shopping_context": "DIGITAL"}),
                           (False, {"cart_item": {"tcin": "1011960739", "quantity": 2, "item_channel_id": "10",
                                                  "fulfillment_type": "SHIPPING", "fulfillment_type_code": "02"},
                                    "cart_type": "REGULAR", "channel_id": "10", "shopping_context": "DIGITAL"})):
            s = lit('1011960739', 2, bytematch=bm)
            out = subprocess.run([node, '-e', 'process.stdout.write(JSON.stringify(' + s + '))'],
                                 capture_output=True, text=True, timeout=20)
            ok = out.returncode == 0
            got = json.loads(out.stdout) if ok else None
            check(f"body_literal_node_json_bm{int(bm)}", ok and got == expect)
            check(f"body_literal_key_order_bm{int(bm)}", ok and list(got.keys()) == list(expect.keys())
                  and list(got['cart_item'].keys()) == list(expect['cart_item'].keys()))
    # the fast lane must consume the helper, not an inline literal
    check("exe_fast_lane_uses_helper", "_atc_body_js = fast_lane_atc_body_literal(tcin, quantity, bytematch=True)" in EXE_SRC
          and "_atc_body_js = fast_lane_atc_body_literal(tcin, quantity, bytematch=False)" in EXE_SRC
          and "body: JSON.stringify({_atc_body_js})" in EXE_SRC)


def test_apps_response_parses_with_production_parser():
    """A real apps-channel response (2026-09-09, home IP) through StockMonitor._process_response."""
    import json
    from src.monitoring.stock_monitor import StockMonitor
    raw = json.loads((ROOT / 'tests' / 'fixtures' / 'redsky_apps_sample.json').read_text(encoding='utf-8'))
    parsed = StockMonitor()._process_response(raw, 100)
    check("apps_parsed_both_tcins", set(map(str, parsed.keys())) == {'21516452', '1011960739'})
    p = {str(k): v for k, v in parsed.items()}
    check("apps_in_stock_flag", p['21516452']['in_stock'] is True and p['1011960739']['in_stock'] is False)
    check("apps_status_fields", p['21516452'].get('availability_status') == 'IN_STOCK'
          and p['1011960739'].get('availability_status') == 'OUT_OF_STOCK')


def test_replay_merge_guard_allows_shorter_token_set():
    """Review #2: a 7-token page set replaced by a 6-token banked set is a valid merge."""
    ex, pe = _stub_executor_for_gate()
    ex._harvest_selftest_armed = False
    ex._harvest_last_replay = None
    ex._harvest_win = {'shots': 0, 'replayed': 0, 'a0': 0, 'stale': 0}
    ex.logger = __import__('types').SimpleNamespace(info=lambda *a, **k: None)
    ex.session_manager = __import__('types').SimpleNamespace(account_id='primary')
    req = dict(HD); req['X-GyJwza5Z-a0'] = 'PAGE-A0'          # 7 page tokens
    ex._shape_bank.push(HD, {'tcin': '1'})                    # 6 banked tokens
    out = ex._harvest_replay_headers_for('main', req)
    check("merge_guard_allows_6_for_7", isinstance(out, list) and len(out) == len(req) - 1)
    names = {e.name.lower() for e in out}
    check("merge_guard_keeps_non_shape", {'cookie', 'user-agent', 'content-type'} <= names and 'x-gyjwza5z-a0' not in names)
    check("merge_guard_counts_replay", ex._harvest_win['replayed'] == 1)


def test_bank_gate():
    MGR = (ROOT / 'src' / 'purchasing' / 'bulletproof_purchase_manager.py').read_text(encoding='utf-8', errors='replace')
    check("exe_harvest_set_ready_defined", "def harvest_set_ready(self) -> bool:" in EXE_SRC
          and "async def harvest_wait_for_set(self, max_wait_s: float) -> bool:" in EXE_SRC)
    check("mgr_bank_gate_flag_default_off", "os.environ.get('TARGET_SHOT_BANK_GATE', '0') == '1' and _gk in _gate_kinds" in MGR)
    check("mgr_bank_gate_only_after_401", "_gk == 'auth401'" in MGR and "TARGET_SHOT_BANK_WAIT_S" in MGR)
    check("mgr_bank_gate_pauses_instead_of_page_signed", "[BANK_GATE] no fresh set within" in MGR
          and "instead of a page-signed re-POST" in MGR)
    check("mgr_bank_gate_bounded_by_deadline", "_bw = min(_bw, max(0.0, _retry_deadline - time.time()))" in MGR)
    # wave-first-only policy (census 2026-09-09)
    check("mgr_wave_first_flag_default_off", "_wf_only = os.environ.get('TARGET_WAVE_FIRST_ONLY', '0') == '1'" in MGR)
    check("mgr_wave_first_covers_401_and_429", "if _wf_takes:" in MGR and "TARGET_WAVE_FIRST_EDGE" in MGR
          and "_wf_kinds = (('auth401', 'edge', 'dco')" in MGR)
    check("mgr_wave_first_leaves_room_for_leg", "_re_lo_needed = _re_lo + _bw_est + 20.0" in MGR
          and "if _remaining < _re_lo_needed:" in MGR)
    check("mgr_watchdog_tracks_budget", "_force_s = max(120.0, float(os.environ.get('TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S', '110')) + 90.0)" in MGR
          and "if elapsed_time > _force_s:" in MGR)
    check("mgr_cadence_print_silenced_under_wave_first", "and not _wf_takes:" in MGR)
    check("mgr_wave_first_cold_reentry", "TARGET_WAVE_REENTRY_MIN_S" in MGR and "_re_lo = max(15.0, _re_lo)" in MGR
          and "cold re-entry in" in MGR)
    check("mgr_wave_first_ends_window_when_no_room", "if _remaining < _re_lo_needed:" in MGR and "ending the window (re-arm opens a fresh one)" in MGR)
    check("mgr_wave_first_keeps_checkout_retries", "'checkout_busy_retryable'" in MGR)
    check("mgr_gate_kinds_widen_in_wave_first", "_gate_kinds = _wf_kinds if _wf_only else ('auth401',)" in MGR
          and "cold re-entry fires page-signed" in MGR)
    # executor stub: ready iff bank has a set inside the replay cap
    ex, pe = _stub_executor_for_gate()
    check("gate_not_ready_empty", ex.harvest_set_ready() is False)
    ex._shape_bank.push(HD, now=__import__('time').time())
    check("gate_ready_fresh", ex.harvest_set_ready() is True)
    check("gate_wait_returns_true_fast", __import__('asyncio').run(ex.harvest_wait_for_set(2.0)) is True)
    ex._shape_bank.push(HD, now=__import__('time').time() - 500)   # push a stale one on top
    ex._shape_bank._items.clear()
    ex._shape_bank.push(HD, now=__import__('time').time() - 150)   # 150 s old > 100 s cap
    check("gate_not_ready_stale", ex.harvest_set_ready() is False)
    t0 = __import__('time').time()
    check("gate_wait_times_out", __import__('asyncio').run(ex.harvest_wait_for_set(0.6)) is False
          and 0.5 <= __import__('time').time() - t0 <= 2.0)
    ex._harvest_replay_on = False
    ex._shape_bank.push(HD, now=__import__('time').time())
    check("gate_not_ready_when_replay_off", ex.harvest_set_ready() is False)


def _stub_executor_for_gate():
    import src.session.purchase_executor as pe
    ex = pe.PurchaseExecutor.__new__(pe.PurchaseExecutor)
    ex._harvest_cfg = h.config({'TARGET_SHAPE_HARVEST': '1', 'TARGET_HARVEST_TCINS': '21516452'})
    ex._shape_bank = h.ShapeBank(3, 300)
    ex._harvest_replay_on = True
    return ex, pe


def test_session_manager_wedge_fixes():
    SM = (ROOT / 'src' / 'session' / 'session_manager.py').read_text(encoding='utf-8', errors='replace')
    check("sm_occlusion_feature_merged", "_feat.append('CalculateNativeWinOcclusion')" in SM
          and "'--disable-features=' + ','.join(_feat)" in SM
          and "'--disable-features=HighEfficiencyModeAvailable,BatterySaverModeAvailable'," not in SM)
    check("sm_occlusion_fix_flag", "TARGET_ACCOUNT_OCCLUSION_FIX" in SM and "TARGET_ACCOUNT_DISABLE_SITE_ISOLATION" in SM)
    check("sm_wedge_probe_defined", "async def _wedge_http_probe(self) -> None:" in SM and "/json/version" in SM
          and "[WEDGE-PROBE]" in SM)
    check("sm_wedge_probe_called_on_both_timeouts", SM.count("await self._wedge_http_probe()") == 2)
    check("sm_wedge_probe_rate_limited", "if now - getattr(self, '_wedge_probe_at', 0.0) < 60.0:" in SM)


def test_bat_pins():
    check("bat_channel_apps_raw", _bat_val('RESILIENT_REDSKY_CHANNEL') == 'apps_raw')
    check("bat_fresh_profiles_off", _bat_val('RESILIENT_POOL_FRESH_PROFILES') == '0')
    check("bat_reader_cadence_3_5", _bat_val('RESILIENT_READ_CADENCE_MIN_S') == '3.0'
          and _bat_val('RESILIENT_READ_CADENCE_MAX_S') == '5.0')
    check("bat_trusted_reader_park", _bat_val('RESILIENT_TRUSTED_READER_PARK_S') == '600')
    check("bat_harvest_all_accounts", _bat_val('TARGET_HARVEST_SKIP') == '')
    check("bat_replay_age_pinned", _bat_val('TARGET_HARVEST_MAX_REPLAY_AGE_S') == '100')
    check("bat_prefer_shipping", _bat_val('TARGET_HARVEST_PREFER_SHIPPING') == '1')
    check("bat_bytematch", _bat_val('TARGET_ATC_BYTEMATCH') == '1')
    check("bat_per_ip_cap_half", _bat_val('RESILIENT_PER_IP_MAX_RPS') == '0.5')
    check("bat_raw_404_park", _bat_val('RESILIENT_RAW_404_PARK_S') == '900')
    check("bat_wave_first_edge_knob", _bat_val('TARGET_WAVE_FIRST_EDGE') == '1')
    check("bat_wave_first_armed", _bat_val('TARGET_WAVE_FIRST_ONLY') == '1' and _bat_val('TARGET_WAVE_REENTRY_MIN_S') == '55'
          and _bat_val('TARGET_WAVE_REENTRY_MAX_S') == '70' and _bat_val('TARGET_SHOT_BANK_GATE') == '1'
          and _bat_val('TARGET_SHOT_BANK_WAIT_S') == '8')
    check("bat_crlf_only", BAT_RAW.count(b'\n') == BAT_RAW.count(b'\r\n') and BAT_RAW.count(b'\r\n') > 100)
    check("bat_old_skip_gone", 'set TARGET_HARVEST_SKIP=primary,alt-1' not in BAT_SRC)


def test_compiles():
    import py_compile
    ok = True
    for f in ('src/monitoring/redsky_channel.py', 'src/monitoring/tab_dispatcher.py',
              'src/monitoring/stock_monitor.py', 'src/monitoring/stock_check_resilient.py',
              'src/session/purchase_executor.py', 'src/session/shape_harvest.py'):
        try:
            py_compile.compile(str(ROOT / f), doraise=True)
        except Exception as e:
            ok = False
            print('   compile error', f, e)
    check("compiles", ok)


if __name__ == '__main__':
    for fn in (test_channel_helper, test_dispatcher_wiring, test_trusted_reader_fixes,
               test_bank_stale_and_refill, test_executor_wiring, test_fast_lane_body_js_really_parses,
               test_apps_response_parses_with_production_parser, test_replay_merge_guard_allows_shorter_token_set,
               test_bank_gate, test_session_manager_wedge_fixes, test_bat_pins, test_compiles):
        try:
            fn()
        except Exception as e:
            FAIL += 1
            import traceback
            print(f"[FAIL] {fn.__name__} raised {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(0 if FAIL == 0 else 1)

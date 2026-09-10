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
    check("exe_merge_length_guard", "if len(merged) < len(req_headers or {}):" in EXE_SRC)
    check("exe_bytematch_url", "if os.environ.get('TARGET_ATC_BYTEMATCH', '0') == '1':" in EXE_SRC
          and "'?field_groups=CART%2CCART_ITEMS%2CSUMMARY'" in EXE_SRC
          and "'&key=9f36aeafbe60771e321a7cc95a78140772ab3e96')" in EXE_SRC)
    # body byte-match: the page's shipping add carries NO fulfillment field and this key order
    check("exe_bytematch_body_page_shape",
          "item_channel_id: '10', tcin: '" in EXE_SRC and "cart_type: 'REGULAR', channel_id: '10', " in EXE_SRC
          and "body: JSON.stringify({_atc_body_js})" in EXE_SRC)
    check("exe_bytematch_body_old_shape_kept", "fulfillment_type: 'SHIPPING', " in EXE_SRC
          and "fulfillment_type_code: '02'}}, cart_type: 'REGULAR'" in EXE_SRC)
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
    check("bat_raw_404_park", _bat_val('RESILIENT_RAW_404_PARK_S') == '120')
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
               test_bank_stale_and_refill, test_executor_wiring, test_bat_pins, test_compiles):
        try:
            fn()
        except Exception as e:
            FAIL += 1
            import traceback
            print(f"[FAIL] {fn.__name__} raised {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(0 if FAIL == 0 else 1)

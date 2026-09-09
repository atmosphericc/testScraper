#!/usr/bin/env python3
"""2026-09-07: HUMAN Security (PerimeterX) "Press & Hold" guard + BD sweep-pool
restore + trusted-browser tab-fetch AUTO policy. No browser, no network.

  1. px_challenge classifier truth table (DOM-marker snapshots + API bodies).
  2. tab_fetch_policy: 0 / 1 / auto semantics incl. boot grace + recovery.
  3. Wiring pins (source text): the sentinel ladder consults the guard BEFORE the
     restart rung and parks via the existing dead-session park; the executor labels
     px_block on ATC + place-order 403s ahead of the Shape-HTML branch; the
     trusted tab reader keeps the body head and logs [STOCK][PX-CAPTCHA]; the
     resilient checker records _last_200_at; app.py routes all three
     RESILIENT_FORCE_TAB_FETCH sites through _tab_fetch_wanted().
  4. Bat + config pins: LOCAL_IP=0, FORCE_TAB_FETCH=auto, BLIND_S=180, PX guard
     flags, CRLF-only (both bats); proxyIps = 16 sweep + 4 reserve, purchase exits
     held out, probe IPs present.

Run: python tests/test_px_challenge_guard.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.session import px_challenge as px          # noqa: E402
from src.monitoring import tab_fetch_policy as tfp   # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS += bool(cond)
    FAIL += (not cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


def _read(rel):
    return (ROOT / rel).read_text(encoding='utf-8', errors='replace')


# ── 1. classifier ──────────────────────────────────────────────────────────
def test_classifier():
    ok = {'url': 'https://www.target.com/account', 'title': 'Account', 'px_container': False,
          'px_iframe': False, 'press_hold': False, 'denied': False, 'ready': 'complete'}
    check("normal_account_page_not_challenge", not px.is_px_challenge(ok))
    check("container_is_challenge", px.is_px_challenge({**ok, 'px_container': True}))
    check("iframe_is_challenge", px.is_px_challenge({**ok, 'px_iframe': True}))
    check("press_hold_text_is_challenge", px.is_px_challenge({**ok, 'press_hold': True}))
    check("denied_text_is_challenge", px.is_px_challenge({**ok, 'denied': True}))
    check("redsky_captcha_url", px.is_px_challenge({**ok, 'url': 'https://redsky.target.com/captcha?trackingId=abc'}))
    check("blocked_url", px.is_px_challenge({**ok, 'url': 'https://www.target.com/blocked?url=%2Fp%2F'}))
    check("pdp_url_not_challenge", not px.is_px_challenge({**ok, 'url': 'https://www.target.com/p/pokemon/-/A-1011960739'}))
    check("non_mapping_false", not px.is_px_challenge(None) and not px.is_px_challenge("x")
          and not px.is_px_challenge([]) and not px.is_px_challenge(42))
    check("describe_names_hits", 'press_hold' in px.describe({**ok, 'press_hold': True}))
    check("describe_non_mapping", px.describe(None) == 'no-markers')

    check("api_redsky_captcha_403", px.body_looks_px_blocked(
        403, '{\n  "captchaRelativeURL":"/captcha?trackingId=1",\n  "captchaAbsoluteURL":"https://redsky.target.com/captcha?trackingId=1"}'))
    check("api_abr_json_403", px.body_looks_px_blocked(
        403, '{"appId":"PX1","jsClientSrc":"/x/init.js","firstPartyEnabled":true,"vid":"","uuid":"",'
             '"hostUrl":"/x/xhr","blockScript":"/x/captcha/captcha.js"}'))
    check("api_press_hold_html_403", px.body_looks_px_blocked(
        403, '<html><body><h1>Press &amp; Hold to confirm you are a human (and not a bot).</h1></body></html>'))
    check("shape_html_403_not_px", not px.body_looks_px_blocked(
        403, '<!DOCTYPE html><html><head><title>Access Denied</title></head><body>You don\'t have permission</body></html>'))
    check("carts_401_not_px", not px.body_looks_px_blocked(401, '{"code":"_ERR_AUTH_DENIED","message":"T83072242"}'))
    check("edge_429_empty_not_px", not px.body_looks_px_blocked(429, ''))
    check("dco_429_not_px", not px.body_looks_px_blocked(429, '{"code":"DCO_RATE_LIMITED"}'))
    check("none_body_not_px", not px.body_looks_px_blocked(403, None))
    check("bad_status_not_px", not px.body_looks_px_blocked('x', 'captcha'))

    saved = {k: os.environ.get(k) for k in (px.FLAG_ENV, px.PARK_ENV)}
    try:
        os.environ[px.FLAG_ENV] = '0';   check("guard_off_0", not px.guard_enabled())
        os.environ[px.FLAG_ENV] = 'false'; check("guard_off_false", not px.guard_enabled())
        os.environ[px.FLAG_ENV] = '1';   check("guard_on_1", px.guard_enabled())
        os.environ.pop(px.FLAG_ENV, None); check("guard_default_on", px.guard_enabled())
        os.environ[px.PARK_ENV] = '5';   check("park_floor_30", px.park_seconds() == 30.0)
        os.environ[px.PARK_ENV] = 'abc'; check("park_bad_value_default", px.park_seconds() == 300.0)
        os.environ.pop(px.PARK_ENV, None); check("park_default_300", px.park_seconds() == 300.0)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    js = px.PX_MARKERS_JS
    check("dom_probe_is_read_only", 'dispatch' not in js and 'click' not in js.lower()
          and 'mouse' not in js.lower() and 'querySelector' in js and 'innerText' in js)


# ── 2. tab-fetch policy ────────────────────────────────────────────────────
def test_tab_fetch_policy():
    f = tfp.tab_fetch_wanted
    check("mode_0_never", not f('0', 0, 1000, now=99999, blind_s=180))
    check("mode_false_never", not f('false', 0, 1000, now=99999, blind_s=180))
    check("mode_1_always", f('1', 99999, 99999, now=99999, blind_s=180))
    check("mode_true_always", f('true', 0, 0))
    check("auto_pool_not_started_false", not f('auto', 0, 0, now=5000))
    check("auto_boot_grace_false", not f('auto', 0, 1000, now=1100, blind_s=180))
    check("auto_blind_after_grace_true", f('auto', 0, 1000, now=1181, blind_s=180))
    check("auto_recent_200_false", not f('auto', 1500, 1000, now=1600, blind_s=180))
    check("auto_stale_200_true", f('auto', 1500, 1000, now=1681, blind_s=180))
    check("auto_garbage_inputs_false", not f('auto', 'x', 'y', now=1))
    check("none_mode_is_off", not f(None, 0, 1000, now=99999))

    class C:
        _last_200_at = 0.0
        _start_time = 1000.0

    saved = {k: os.environ.get(k) for k in (tfp.MODE_ENV, tfp.BLIND_ENV)}
    try:
        os.environ[tfp.MODE_ENV] = 'auto'
        os.environ[tfp.BLIND_ENV] = '180'
        check("decide_none_checker_false", not tfp.decide(None, now=5000))
        check("decide_blind_true", tfp.decide(C(), now=1200))
        c2 = C(); c2._last_200_at = 1190.0
        check("decide_fresh_200_false", not tfp.decide(c2, now=1200))
        os.environ[tfp.MODE_ENV] = '0';  check("decide_mode0_false", not tfp.decide(C(), now=99999))
        os.environ[tfp.MODE_ENV] = '1';  check("decide_mode1_true_even_without_checker", tfp.decide(None))
        os.environ.pop(tfp.MODE_ENV, None); check("decide_default_off", not tfp.decide(C(), now=99999))
        os.environ[tfp.BLIND_ENV] = '1';  check("blind_floor_10", tfp.blind_seconds() == 10.0)
        os.environ[tfp.BLIND_ENV] = 'x';  check("blind_bad_value_default", tfp.blind_seconds() == 180.0)
        os.environ.pop(tfp.BLIND_ENV, None); check("blind_default_180", tfp.blind_seconds() == 180.0)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── 3. wiring pins ─────────────────────────────────────────────────────────
def test_wiring():
    SM = _read('src/session/session_manager.py')
    i_guard = SM.find("if await self._px_challenge_parks_ladder():")
    i_restart = SM.find("navigation refresh failed — escalating to restart")
    check("sentinel_guard_before_restart_rung", 0 < i_guard < i_restart)
    g0 = SM.find("async def _px_challenge_parks_ladder(self) -> bool:")
    g1 = SM.find("async def _trigger_token_refresh(self) -> bool:", g0)
    body = SM[g0:g1] if 0 < g0 < g1 else ''
    check("guard_defined", bool(body))
    check("guard_parks_via_dead_session_park", "self._dead_session_parked_until = time.time() + park_s" in body)
    check("guard_logs_and_alerts", "[PX-CHALLENGE]" in body and "_alert_critical(" in body)
    check("guard_flag_gated", "guard_enabled()" in body)
    check("guard_bounded_probe", "asyncio.wait_for(tab.evaluate(PX_MARKERS_JS), timeout=4.0)" in body)
    check("guard_never_touches_widget", "dispatchMouseEvent" not in body and "click(" not in body)
    check("guard_alert_rate_limited", "_px_challenge_alert_at" in body and ">= 600.0" in body)

    EXE = _read('src/session/purchase_executor.py')
    check("atc_px_block_branch", "_px_blocked(atc_status, atc_body)" in EXE and "[PX_BLOCK]" in EXE)
    i_px = EXE.find("_px_blocked(atc_status, atc_body)")
    i_shape = EXE.find("ATC fetch blocked by Shape Security (403 HTML)")
    check("atc_px_checked_before_shape_html", 0 < i_px < i_shape)
    check("place_order_px_block_reason", "reason = 'px_block'" in EXE)
    check("executor_import_dual_form", EXE.count("from .px_challenge import body_looks_px_blocked") == 2)

    MON = _read('src/monitoring/stock_monitor.py')
    check("stock_tab_reader_keeps_body_head", "return {{error: resp.status, body: t}}" in MON)
    check("stock_px_captcha_log", "[STOCK][PX-CAPTCHA]" in MON and "_px_captcha_last_log" in MON)

    CHK = _read('src/monitoring/stock_check_resilient.py')
    check("checker_last_200_init", "self._last_200_at = 0.0" in CHK)
    check("checker_last_200_set_on_200", "self._last_200_at = time.time()" in CHK)
    check("checker_stats_expose_last_200", '"last_200_at": self._last_200_at' in CHK)

    APP = _read('app.py')
    check("app_helper_defined", "def _tab_fetch_wanted(self) -> bool:" in APP)
    check("app_no_raw_env_site_left", "os.environ.get('RESILIENT_FORCE_TAB_FETCH', '0') == '1'" not in APP)
    check("app_three_sites_routed", APP.count("self._tab_fetch_wanted()") >= 3)
    check("app_policy_import", "from src.monitoring.tab_fetch_policy import decide" in APP)
    check("app_logs_flip", "[STOCK][TAB-FETCH]" in APP)

    RL = _read('relogin_one.py')
    check("hand_login_prompt_mentions_press_hold", "Press & Hold" in RL)


# ── 4. bat + config pins ───────────────────────────────────────────────────
def test_bat_and_config():
    BAT_RAW = (ROOT / 'run_bot_with_nightly_restart.bat').read_bytes()
    BAT = BAT_RAW.decode('utf-8', errors='replace')

    def v(name):
        m = re.search(r'^set ' + re.escape(name) + r'=(.*)$', BAT, re.M)
        return m.group(1).strip() if m else ''

    check("bat_local_ip_off", v('RESILIENT_HARVEST_VIA_LOCAL_IP') == '0')
    check("bat_backoff_on", v('RESILIENT_CAPTCHA_BACKOFF') == '1')
    check("bat_tab_fetch_auto", v('RESILIENT_FORCE_TAB_FETCH') == 'auto')
    check("bat_blind_180", v('RESILIENT_TAB_FETCH_BLIND_S') == '180')
    check("bat_px_guard_on", v('TARGET_PX_CHALLENGE_GUARD') == '1')
    check("bat_px_park_300", v('TARGET_PX_CHALLENGE_PARK_S') == '300')
    check("bat_rate_3", v('TARGET_SWEEPS_PER_SEC') == '3.0')
    check("bat_harvest_still_on", v('TARGET_SHAPE_HARVEST') == '1')
    check("bat_crlf_only", BAT_RAW.count(b'\n') == BAT_RAW.count(b'\r\n') and BAT_RAW.count(b'\r\n') > 100)

    PROBE_RAW = (ROOT / 'probe_sweep_pool.bat').read_bytes()
    PROBE = PROBE_RAW.decode('utf-8', errors='replace')
    check("probe_bat_crlf_only", PROBE_RAW.count(b'\n') == PROBE_RAW.count(b'\r\n') and PROBE_RAW.count(b'\r\n') > 10)
    check("probe_bat_runs_readonly_probe", 'redsky_browser_probe_bd.py %%I' in PROBE
          and 'RESILIENT_HARVEST_VIA_LOCAL_IP=0' in PROBE)

    d = json.load(open(ROOT / 'config' / 'proxyIps.json', encoding='utf-8'))
    ips = [re.search(r'-ip-([0-9.]+):', u).group(1) for u in d['proxies']]
    check("sixteen_sweep_ips", len(ips) == 16)
    check("sweep_ips_unique", len(set(ips)) == 16)
    check("four_reserves", len(d.get('reserve_proxies', [])) == 4)
    check("burned_list_retired", 'burned_sweep_20260904' not in d)
    purchase_exits = {'168.158.160.228', '31.98.158.87', '168.158.32.64'}
    check("purchase_exits_held_out_of_sweep", not (purchase_exits & set(ips)))
    reserve_ips = {re.search(r'-ip-([0-9.]+):', u).group(1) for u in d['reserve_proxies']}
    check("reserve_disjoint_from_sweep", not (reserve_ips & set(ips)))
    check("probe_ips_in_pool", all(ip in ips for ip in ('31.105.228.245', '31.105.93.225',
                                                         '168.158.143.27', '72.56.171.184')))
    check("per_ip_rate_under_ceiling", 3.0 / len(ips) <= 1.0)


if __name__ == '__main__':
    for fn in (test_classifier, test_tab_fetch_policy, test_wiring, test_bat_and_config):
        try:
            fn()
        except Exception as e:
            FAIL += 1
            print(f"[FAIL] {fn.__name__} raised {type(e).__name__}: {e}")
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(0 if FAIL == 0 else 1)

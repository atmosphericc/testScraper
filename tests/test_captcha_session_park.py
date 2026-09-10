#!/usr/bin/env python3
"""2026-09-07 (23:05 probe verdict): per-SESSION captcha park + per-IP sweep-rate
cap + fresh pool profiles + probe `home`/`reserve` modes. No browser, no network.

Live fact pinned here: probe_sweep_pool.bat ran the SAME fresh profile on the same
machine through 4 sweep exits: 31.105.228.245 / 31.105.93.225 / 168.158.143.27 ->
403 captcha envelope, 72.56.171.184 -> 200 OK-DATA twice. The wall is IP-range
reputation (HUMAN/PerimeterX), not the device.

  1. captcha_park_seconds / effective_sweep_rate formulas.
  2. MultiSessionPool.pick_session skips captcha-parked sessions; usable/parked counts.
  3. Wiring pins: checker parks the session on a captcha 403 and only slows the
     whole sweep when nothing usable remains; 200 clears; sweep loop uses the
     effective rate; stats expose it; pool wipes profiles only under the flag and
     only inside profile_root; app.py tab-fetch budget env; probe script modes.
  4. Bat pins + probe bat options; CRLF-only.

Run: python tests/test_captcha_session_park.py
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.session.multi_session_pool import MultiSessionPool, SessionEntry   # noqa: E402
from src.monitoring.stock_check_resilient import (                          # noqa: E402
    captcha_park_seconds, effective_sweep_rate)

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS += bool(cond)
    FAIL += (not cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


def _read(rel):
    return (ROOT / rel).read_text(encoding='utf-8', errors='replace')


# ── 1. formulas ────────────────────────────────────────────────────────────
def test_formulas():
    check("park_hit1_base", captcha_park_seconds(1, 1800.0) == 1800.0)
    check("park_hit2_x2", captcha_park_seconds(2, 1800.0) == 3600.0)
    check("park_hit3_x4", captcha_park_seconds(3, 1800.0) == 7200.0)
    check("park_hit4_x8_cap", captcha_park_seconds(4, 1800.0) == 14400.0)
    check("park_hit9_still_cap", captcha_park_seconds(9, 1800.0) == 14400.0)
    check("park_floor_60", captcha_park_seconds(1, 0.0) == 60.0)
    check("park_hit0_treated_as_1", captcha_park_seconds(0, 1800.0) == 1800.0)

    check("rate_16_clean_is_target", effective_sweep_rate(3.0, 16, 1.0) == 3.0)
    check("rate_3_clean_is_target", effective_sweep_rate(3.0, 3, 1.0) == 3.0)
    check("rate_2_clean_capped_2", effective_sweep_rate(3.0, 2, 1.0) == 2.0)
    check("rate_1_clean_capped_1", effective_sweep_rate(3.0, 1, 1.0) == 1.0)
    check("rate_0_usable_keeps_schedule", effective_sweep_rate(3.0, 0, 1.0) == 3.0)
    check("rate_cap_off", effective_sweep_rate(3.0, 1, 0.0) == 3.0)
    check("rate_half_per_ip", effective_sweep_rate(3.0, 4, 0.5) == 2.0)
    check("rate_floor", effective_sweep_rate(0.0, 1, 1.0) == 0.01)


# ── 2. pool pick/usable ────────────────────────────────────────────────────
def _entry(i, ip, parked_until=0.0, state="ready"):
    s = SessionEntry(id=f"s{i}", proxy_url=f"http://u:p@h:1-ip-{ip}", proxy_ip=ip,
                     local_port=22000 + i, profile_dir=Path("state/session_profiles") / f"s{i}")
    s.state = state
    s.tab = object()
    s.cookies = {"visitorId": "X"}
    s.visitor_id = "X"
    s.captcha_parked_until = parked_until
    return s


def test_pool_pick():
    now = time.time()
    pool = MultiSessionPool.__new__(MultiSessionPool)
    pool.sessions = [
        _entry(1, "31.105.228.245", parked_until=now + 1800),
        _entry(2, "31.105.93.225", parked_until=now + 1800),
        _entry(3, "168.158.143.27", parked_until=now + 1800),
        _entry(4, "72.56.171.184"),
    ]
    picks = {pool.pick_session().id for _ in range(40)}
    check("pick_skips_parked_sessions", picks == {"s4"})
    check("usable_count_1", pool.usable_session_count() == 1)
    check("parked_count_3", pool.captcha_parked_count() == 3)
    pool.sessions[0].captcha_parked_until = now - 1     # park expired -> retest
    picks = {pool.pick_session().id for _ in range(80)}
    check("expired_park_returns_to_rotation", "s1" in picks and "s4" in picks)
    check("usable_count_2_after_expiry", pool.usable_session_count() == 2)
    for s in pool.sessions:
        s.captcha_parked_until = now + 600
    check("all_parked_pick_none", pool.pick_session() is None)
    check("all_parked_usable_0", pool.usable_session_count() == 0)
    pool.sessions = [_entry(9, "72.56.171.184", state="crashed")]
    check("crashed_not_usable", pool.usable_session_count() == 0 and pool.pick_session() is None)


# ── 3. wiring pins ─────────────────────────────────────────────────────────
def test_wiring():
    CHK = _read('src/monitoring/stock_check_resilient.py')
    check("checker_parks_on_captcha", "_usable, _total = self._park_captcha_session(result.session_id, result.pinned_ip)" in CHK)
    check("checker_global_backoff_only_when_blind", "if _usable == 0 else 1.0)" in CHK)
    check("checker_clears_on_200", "self._clear_captcha_session(result.session_id, result.pinned_ip)" in CHK)
    i_clear = CHK.find("self._clear_captcha_session(result.session_id, result.pinned_ip)")
    i_200 = CHK.find("if result.http_status == 200:", CHK.find("async def _dispatch_one"))
    i_403 = CHK.find("elif result.http_status in (401, 403):", i_200)
    check("clear_sits_in_200_branch", 0 < i_200 < i_clear < i_403)
    check("sweep_loop_uses_effective_rate", "1.0 / max(0.01, self._effective_sweeps_per_sec())" in CHK)
    check("rate_logged_on_change", "[STOCK][RATE]" in CHK and "_last_usable_logged" in CHK)
    check("park_logged", "[STOCK][CAPTCHA-PARK]" in CHK and "every session is walled" in CHK)
    check("env_knobs", "_env_float('RESILIENT_CAPTCHA_PARK_S', 1800.0, floor=60.0)" in CHK
          and "_env_float('RESILIENT_PER_IP_MAX_RPS', 1.0, floor=0.0)" in CHK)
    check("stats_expose", '"usable_sessions"' in CHK and '"captcha_parked"' in CHK)
    check("captcha_still_skips_proxystate_park",
          CHK.find("'captcha' in _err.lower()") < CHK.find(
              "else:\n                    self.proxy_state.record_status(result.pinned_ip, result.http_status)"))

    POOL = _read('src/session/multi_session_pool.py')
    check("entry_fields", "captcha_parked_until: float = 0.0" in POOL and "captcha_hits: int = 0" in POOL)
    check("pick_filters_parked", "and s.captcha_parked_until <= now" in POOL
          and "and s.rate_parked_until <= now]" in POOL)   # 2026-09-09: + app-channel rate park
    check("usable_helper", "def usable_session_count(self, now: Optional[float] = None) -> int:" in POOL)
    check("fresh_profiles_flag_gated", 'os.environ.get("RESILIENT_POOL_FRESH_PROFILES", "0") == "1"' in POOL)
    w0 = POOL.find("def _wipe_profile(self, s: SessionEntry) -> None:")
    w1 = POOL.find("async def _launch_persistent_one", w0)
    body = POOL[w0:w1] if 0 < w0 < w1 else ''
    check("wipe_guarded_to_profile_root", "if root not in pd.parents:" in body and "shutil.rmtree(pd, ignore_errors=True)" in body)
    check("wipe_resets_harvest", "s.cookies = {}" in body and 's.visitor_id = ""' in body)

    APP = _read('app.py')
    check("app_tab_fetch_budget_env", "RESILIENT_TAB_FETCH_TIMEOUT_S" in APP
          and "tab = future.result(timeout=max(1.0, _gp_budget))" in APP)

    PROBE = _read('redsky_browser_probe_bd.py')
    check("probe_home_mode", "HOME = WANT_IP.lower() in ('home', 'local')" in PROBE
          and "proxy_url=(None if HOME else f'127.0.0.1:{PORT}')" in PROBE)
    check("probe_searches_reserves", "cfg.get('reserve_proxies', [])" in PROBE and "sys.exit(5)" in PROBE)
    check("probe_forwarder_optional", "if fwd is not None:" in PROBE)


# ── 4. bat pins ────────────────────────────────────────────────────────────
def test_bat():
    BAT_RAW = (ROOT / 'run_bot_with_nightly_restart.bat').read_bytes()
    BAT = BAT_RAW.decode('utf-8', errors='replace')

    def v(name):
        m = re.search(r'^set ' + re.escape(name) + r'=(.*)$', BAT, re.M)
        return m.group(1).strip() if m else ''

    check("bat_park_1800", v('RESILIENT_CAPTCHA_PARK_S') == '1800')
    # 2026-09-09: the app channel's per-IP limiter (404 after ~166 reads at 2/s; clean at 0.5/s)
    check("bat_per_ip_cap_half", v('RESILIENT_PER_IP_MAX_RPS') == '0.5')
    # 2026-09-09 audit of run_20260907: fresh profiles did not help (all 16 walled
    # within 3.5 min of boot; the only recovery came with equally fresh profiles),
    # so the pin flipped to 0 (persistent profiles let HUMAN cookies age).
    check("bat_fresh_profiles_off", v('RESILIENT_POOL_FRESH_PROFILES') == '0')
    check("bat_tab_fetch_budget_8", v('RESILIENT_TAB_FETCH_TIMEOUT_S') == '8')
    check("bat_local_ip_still_off", v('RESILIENT_HARVEST_VIA_LOCAL_IP') == '0')
    check("bat_crlf_only", BAT_RAW.count(b'\n') == BAT_RAW.count(b'\r\n') and BAT_RAW.count(b'\r\n') > 100)

    PROBE_RAW = (ROOT / 'probe_sweep_pool.bat').read_bytes()
    PROBE = PROBE_RAW.decode('utf-8', errors='replace')
    check("probe_bat_crlf_only", PROBE_RAW.count(b'\n') == PROBE_RAW.count(b'\r\n') and PROBE_RAW.count(b'\r\n') > 10)
    check("probe_bat_reserve_option", 'if /i "%~1"=="reserve" set "IPS=31.105.63.137 168.158.111.240 168.158.219.76 168.158.74.45"' in PROBE)
    check("probe_bat_home_option", 'if /i "%~1"=="home" set "IPS=home"' in PROBE)
    check("probe_bat_single_ip_option", 'set "IPS=%~1"' in PROBE)


if __name__ == '__main__':
    for fn in (test_formulas, test_pool_pick, test_wiring, test_bat):
        try:
            fn()
        except Exception as e:
            FAIL += 1
            print(f"[FAIL] {fn.__name__} raised {type(e).__name__}: {e}")
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(0 if FAIL == 0 else 1)

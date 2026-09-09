#!/usr/bin/env python3
"""2026-09-04 RedSky captcha wall: captcha-aware sweep backoff + home-IP sweep mode.
No browser, no network.

Live facts pinned here: a real browser on the HOME IP read RedSky 200 while the
same browser on every Bright Data range got 403 + {"captchaRelativeURL": ...}
(F5/Shape ATA). The pool then fired 3/s into the wall (1,073 straight 403s).

  1. Backoff math replica: x1 until 5 walled reads, x2/x4/x8, cap x16; any 200 resets.
  2. Checker wiring: captcha 403 -> backoff, NOT ProxyState (no 2-strike 3h park);
     sweep period multiplies by the backoff; 200 resets + logs recovery.
  3. stock_monitor passes RESILIENT_HARVEST_VIA_LOCAL_IP into the checker.
  4. Bat pins: RESILIENT_CAPTCHA_BACKOFF=1, TARGET_SWEEPS_PER_SEC=3.0; CRLF-only.
     2026-09-07: RESILIENT_HARVEST_VIA_LOCAL_IP is pinned to 0 (home-IP sweep mode
     REVERTED — it read zero RedSky 200s in 3.9 days; the wall was HUMAN/PerimeterX
     device-keyed, not IP-keyed). The mode stays wired for emergencies (=1).
  5. proxyIps: 16 BD sweep IPs restored + 4 reserves; the 09-04 burned list retired.

Run: python tests/test_captcha_backoff_local_ip.py
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHK = (ROOT / 'src' / 'monitoring' / 'stock_check_resilient.py').read_text(encoding='utf-8', errors='replace')
MON = (ROOT / 'src' / 'monitoring' / 'stock_monitor.py').read_text(encoding='utf-8', errors='replace')
BAT_RAW = (ROOT / 'run_bot_with_nightly_restart.bat').read_bytes()
BAT = BAT_RAW.decode('utf-8', errors='replace')

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    PASS += bool(cond); FAIL += (not cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")

def mult_for(streak):  # replica of the checker's formula
    return float(min(16.0, 2.0 ** min(4, streak // 5)))

def test_backoff_math():
    check("no_backoff_under_5", all(mult_for(s) == 1.0 for s in range(0, 5)))
    check("x2_at_5", mult_for(5) == 2.0 and mult_for(9) == 2.0)
    check("x4_at_10", mult_for(10) == 4.0)
    check("x8_at_15", mult_for(15) == 8.0)
    check("x16_at_20_and_capped", mult_for(20) == 16.0 and mult_for(1000) == 16.0)
    # at 3/s a x16 backoff = one read every ~5.3s: still probing, never blind
    check("cap_keeps_probing", (1 / 3.0) * 16.0 < 6.0)

def test_checker_wiring():
    check("flag_default_on", "_os.environ.get('RESILIENT_CAPTCHA_BACKOFF', '1') != '0'" in CHK)
    # 2026-09-09: match the DISTINCTIVE RedSky/PX captcha envelope, not a bare
    # 'captcha' substring (which over-parked sweep IPs on any 403 mentioning it).
    check("captcha_detected_on_403_body", "captcharelativeurl" in CHK and "px-captcha" in CHK and "any(s in _err.lower()" in CHK)
    i_c = CHK.find("any(s in _err.lower()"); i_else = CHK.find("else:\n                    self.proxy_state.record_status(result.pinned_ip, result.http_status)", i_c)
    check("captcha_403_skips_proxystate_park", 0 < i_c < i_else)
    check("formula_pinned", "min(16.0, 2.0 ** min(4, self._captcha_streak // 5))" in CHK)
    check("sweep_period_uses_multiplier", "getattr(self, '_captcha_backoff_mult', 1.0)" in CHK)
    check("200_resets_and_logs", "[STOCK][CAPTCHA-BACKOFF] recovered" in CHK and "self._captcha_backoff_mult = 1.0" in CHK)
    check("wall_logged", "RedSky captcha wall" in CHK)

def test_monitor_local_ip_wiring():
    check("env_read", "os.environ.get('RESILIENT_HARVEST_VIA_LOCAL_IP', '0') == '1'" in MON)
    check("passed_to_checker", "harvest_via_local_ip=_local_ip," in MON)
    check("checker_accepts_kwarg", "harvest_via_local_ip: bool = False," in CHK)

def _bat_val(name):
    m = re.search(r'^set ' + re.escape(name) + r'=(.*)$', BAT, re.M)
    return m.group(1).strip() if m else ''

def test_bat_and_config():
    check("bat_local_ip_off_since_0907", _bat_val('RESILIENT_HARVEST_VIA_LOCAL_IP') == '0')
    check("bat_backoff_on", _bat_val('RESILIENT_CAPTCHA_BACKOFF') == '1')
    check("bat_rate_3", _bat_val('TARGET_SWEEPS_PER_SEC') == '3.0')
    check("bat_crlf_only", BAT_RAW.count(b'\n') == BAT_RAW.count(b'\r\n') and BAT_RAW.count(b'\r\n') > 100)
    d = json.load(open(ROOT / 'config' / 'proxyIps.json', encoding='utf-8'))
    check("sixteen_sweep_sessions", len(d['proxies']) == 16)
    check("reserves_kept", len(d.get('reserve_proxies', [])) == 4)
    check("burned_list_retired", 'burned_sweep_20260904' not in d)

if __name__ == '__main__':
    for fn in (test_backoff_math, test_checker_wiring, test_monitor_local_ip_wiring, test_bat_and_config):
        try: fn()
        except Exception as e:
            FAIL += 1; print(f"[FAIL] {fn.__name__} raised {type(e).__name__}: {e}")
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(0 if FAIL == 0 else 1)

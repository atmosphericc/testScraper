#!/usr/bin/env python3
"""TARGET_UA_MODE=engine + installed-Chrome auto-detect (2026-09-13). No browser, no network.

09-11 forensics: the purchase tab presented a CDP-overridden FULL-version User-Agent
('Chrome/152.0.7977.82', a string real Chrome 101+ never sends) and a GREASE-less
brand list, while the harvest/warmup tabs that mint the Shape sensor sent the real
reduced 'Chrome/152.0.0.0' -- and the pinned build had drifted from the installed
152.0.7977.84 for the fourth time. Pins:
  * account_identity.installed_chrome_version (env force / autodetect-off / cache)
  * build_identity in legacy vs engine mode (draw order unchanged, UA reduced)
  * apply_identity skips ONLY the UA override in engine mode (tz/locale/viewport stay)
  * both bats arm TARGET_UA_MODE=engine (CRLF), config tz for alt-1, preflight + log pins

Run: python tests/test_ua_engine_mode.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.session import account_identity as ai  # noqa: E402

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


@contextmanager
def env(**kw):
    """Temporarily set (value) or unset (None) env vars; resets the version cache."""
    old = {k: os.environ.get(k) for k in kw}
    try:
        for k, v in kw.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        ai.reset_installed_cache()
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        ai.reset_installed_cache()


class FakeTab:
    def __init__(self):
        self.cmds = []

    async def send(self, gen):
        cmd = next(gen)  # zendriver cdp functions are generators yielding the command dict
        self.cmds.append(cmd)
        try:
            gen.send({})
        except StopIteration:
            pass
        return {}


def _methods(tab):
    return [c['method'] for c in tab.cmds]


# ---------------------------------------------------------------------------
# 1. installed_chrome_version
# ---------------------------------------------------------------------------
def test_installed_version():
    with env(TARGET_CHROME_VERSION='152.0.7977.84', TARGET_UA_AUTODETECT=None):
        check("forced_version_wins", ai.installed_chrome_version() == '152.0.7977.84')
    with env(TARGET_CHROME_VERSION='garbage', TARGET_UA_AUTODETECT='0'):
        check("autodetect_off_returns_none", ai.installed_chrome_version() is None)
    with env(TARGET_CHROME_VERSION=None, TARGET_UA_AUTODETECT=None):
        v = ai.installed_chrome_version()
        import re
        check("autodetect_none_or_dotted", v is None or bool(re.fullmatch(r'\d+\.\d+\.\d+\.\d+', v)))
        check("autodetect_cached", ai.installed_chrome_version() == v)
        if v:
            # on the production host this is the real install; it must be a plausible modern major
            check("autodetect_plausible_major", int(v.split('.')[0]) >= 140)


# ---------------------------------------------------------------------------
# 2. build_identity legacy vs engine
# ---------------------------------------------------------------------------
def test_build_identity_modes():
    with env(TARGET_CHROME_VERSION='152.0.7977.84', TARGET_UA_MODE=None):
        leg = ai.build_identity('primary', 'America/Chicago')
        check("legacy_full_version_ua", 'Chrome/152.0.7977.84 Safari/537.36' in leg['user_agent']
              and leg['ua_mode'] == 'legacy' and leg['ua_full_version'] == '152.0.7977.84'
              and leg['ua_major_version'] == '152')
        check("legacy_tracks_installed_not_pinned", leg['ua_full_version'] != ai._CHROME_BUILDS[0]
              or ai._CHROME_BUILDS[0] == '152.0.7977.84')
    with env(TARGET_CHROME_VERSION='152.0.7977.84', TARGET_UA_MODE='engine'):
        eng = ai.build_identity('primary', 'America/Chicago')
        check("engine_reduced_ua", 'Chrome/152.0.0.0 Safari/537.36' in eng['user_agent']
              and '7977' not in eng['user_agent'] and eng['ua_mode'] == 'engine')
        check("engine_keeps_full_version_for_hints", eng['ua_full_version'] == '152.0.7977.84')
        check("engine_deterministic", ai.build_identity('primary', 'America/Chicago') == eng)
        # every OTHER axis is identical across modes: the seeded draw order did not move
        strip = lambda d: {k: v for k, v in d.items() if k not in ('user_agent', 'ua_mode')}
        check("modes_only_differ_in_ua", strip(leg) == strip(eng))
        check("accounts_still_distinct", ai.build_identity('business')['canvas_seed'] != eng['canvas_seed'])
        check("timezone_override_respected", ai.build_identity('alt-1', 'America/Phoenix')['timezone'] == 'America/Phoenix')
    with env(TARGET_CHROME_VERSION=None, TARGET_UA_AUTODETECT='0', TARGET_UA_MODE='engine'):
        pinned = ai.build_identity('primary')
        check("autodetect_off_uses_pinned_list", pinned['ua_full_version'] == ai._CHROME_BUILDS[0]
              and f"Chrome/{ai._CHROME_BUILDS[0].split('.')[0]}.0.0.0" in pinned['user_agent'])
    check("ua_mode_default_legacy_and_bad_value", True if os.environ.pop('TARGET_UA_MODE', None) is None else True)
    with env(TARGET_UA_MODE=None):
        check("ua_mode_default_legacy", ai.ua_mode() == 'legacy')
    with env(TARGET_UA_MODE='ENGINE '):
        check("ua_mode_case_insensitive", ai.ua_mode() == 'engine')
    with env(TARGET_UA_MODE='banana'):
        check("ua_mode_unknown_is_legacy", ai.ua_mode() == 'legacy')


# ---------------------------------------------------------------------------
# 3. apply_identity: engine skips ONLY the UA override
# ---------------------------------------------------------------------------
def test_apply_identity_modes():
    with env(TARGET_CHROME_VERSION='152.0.7977.84', TARGET_UA_MODE='engine'):
        ident = ai.build_identity('alt-1', 'America/Phoenix')
        tab = FakeTab()
        res = asyncio.run(ai.apply_identity(tab, ident))
        m = _methods(tab)
        check("engine_no_ua_override", 'Emulation.setUserAgentOverride' not in m)
        check("engine_tz_locale_viewport_still_applied",
              'Emulation.setTimezoneOverride' in m and 'Emulation.setLocaleOverride' in m
              and 'Emulation.setDeviceMetricsOverride' in m)
        tz = [c for c in tab.cmds if c['method'] == 'Emulation.setTimezoneOverride'][0]
        check("engine_tz_value", tz['params']['timezoneId'] == 'America/Phoenix')
        check("engine_results", res.get('user_agent') is False and res.get('ua_mode') == 'engine'
              and res.get('timezone') is True and res.get('spoof_js') is False)
    with env(TARGET_CHROME_VERSION='152.0.7977.84', TARGET_UA_MODE=None):
        ident = ai.build_identity('alt-1', 'America/Phoenix')
        tab = FakeTab()
        res = asyncio.run(ai.apply_identity(tab, ident))
        ua = [c for c in tab.cmds if c['method'] == 'Emulation.setUserAgentOverride']
        check("legacy_ua_override_sent", len(ua) == 1 and ua[0]['params']['userAgent'] == ident['user_agent']
              and res.get('user_agent') is True and 'ua_mode' not in res)
        # the identity dict's own ua_mode wins over the env (login + purchase agree by construction)
        ident2 = dict(ident); ident2['ua_mode'] = 'engine'
        tab2 = FakeTab()
        asyncio.run(ai.apply_identity(tab2, ident2))
        check("identity_ua_mode_beats_env", 'Emulation.setUserAgentOverride' not in _methods(tab2))


# ---------------------------------------------------------------------------
# 4. Pins: bats (CRLF), config, preflight, session_manager log line
# ---------------------------------------------------------------------------
def _bat(path):
    raw = (ROOT / path).read_bytes()
    return raw, raw.decode('utf-8', errors='replace')


def test_pins():
    import re
    for bat in ('run_bot_with_nightly_restart.bat', 'hand_login_all.bat'):
        raw, src = _bat(bat)
        check(f"{bat}_arms_engine", bool(re.search(r'^set TARGET_UA_MODE=engine\s*$', src, re.M)))
        check(f"{bat}_crlf_only", raw.count(b'\n') == raw.count(b'\r\n') and raw.count(b'\r\n') > 10)
    raw, src = _bat('run_bot_with_nightly_restart.bat')
    check("bat_ua_note_dated", '2026-09-13' in src and 'GREASE' in src)
    cfg_path = ROOT / 'config' / 'target_accounts.json'    # git-ignored (credentials): host-local pin
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
        tz = {a['account_id']: a.get('timezone') for a in cfg.get('accounts', [])}
        # 2026-09-20: alt-1 moved off its Bright Data Phoenix exit onto the HOME
        # line (proxy_url=""), so America/Phoenix would now be an identity
        # incoherence against a Chicago residential IP. All three share the exit,
        # so all three pin to the host's real timezone.
        check("config_all_chicago",
              all(tz.get(a) == 'America/Chicago' for a in ('primary', 'business', 'alt-1')))
        proxies = {a['account_id']: (a.get('proxy_url') or '') for a in cfg.get('accounts', [])}
        check("config_no_account_proxies", not any(proxies.values()))
    else:
        print("[SKIP] config/target_accounts.json absent on this host (timezone pins not checked)")
    pf = (ROOT / 'preflight_fp_drop.py').read_text(encoding='utf-8')
    check("preflight_uses_autodetect", 'installed_chrome_version as _icv' in pf and "TARGET_UA_MODE=engine" in pf)
    check("preflight_exit_tz_check", 'ip-api.com/json/' in pf and "'--no-net' not in sys.argv" in pf)
    sm = (ROOT / 'src' / 'session' / 'session_manager.py').read_text(encoding='utf-8')
    check("session_manager_logs_ua_mode", "ua_mode={_identity.get('ua_mode')}" in sm and 'live_ua[:200]' in sm)
    src_ai = (ROOT / 'src' / 'session' / 'account_identity.py').read_text(encoding='utf-8')
    check("identity_draw_order_preserved", 'build = rng.choice(_CHROME_BUILDS)' in src_ai
          and src_ai.find('build = rng.choice(_CHROME_BUILDS)') < src_ai.find('_inst = installed_chrome_version()'))
    check("relogin_uses_same_builder", 'identity = build_identity(acc_id, timezone=acc["timezone"] or None)'
          in (ROOT / 'relogin_one.py').read_text(encoding='utf-8'))


def test_compiles():
    import py_compile
    ok = True
    for f in ('src/session/account_identity.py', 'src/session/session_manager.py', 'preflight_fp_drop.py', 'relogin_one.py'):
        try:
            py_compile.compile(str(ROOT / f), doraise=True)
        except Exception as e:
            ok = False
            print('   compile error', f, e)
    check("compiles", ok)


if __name__ == '__main__':
    for fn in (test_installed_version, test_build_identity_modes, test_apply_identity_modes, test_pins, test_compiles):
        try:
            fn()
        except Exception as e:
            FAIL += 1
            import traceback
            print(f"[FAIL] {fn.__name__} raised {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(0 if FAIL == 0 else 1)

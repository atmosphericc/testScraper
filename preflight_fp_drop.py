#!/usr/bin/env python3
"""Pre-drop preflight for the fingerprint-chromium purchase setup.

READ-ONLY: no browser, no network, no login, and no cookie VALUES are printed.
Run before every drop (and before logging in):

    venv\\Scripts\\python.exe preflight_fp_drop.py

Verifies: code compiles + imports, config is drop-ready (enabled accounts each
have a distinct cvv / proxy / paths), the fingerprint-chromium binary is present,
the purchase/login decoupling behaves under the bat's flags, the bat flags are
correct, the launchers are in place, and the session files exist with auth cookies.
Also prints where everything gets logged.

Exit: 0 = all green, 2 = green with warnings (usually 'log in to refresh'), 1 = a
hard failure to fix before dropping.
"""
from __future__ import annotations
import json
import os
import py_compile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

OKS, FAILS, WARNS = [], [], []
def ok(m):   OKS.append(m);   print(f"  [PASS] {m}")
def fail(m): FAILS.append(m); print(f"  [FAIL] {m}")
def warn(m): WARNS.append(m); print(f"  [WARN] {m}")

print("=" * 76)
print("PRE-DROP PREFLIGHT  —  fingerprint-chromium purchase setup")
print("=" * 76)

# ---- 1. Code compiles -------------------------------------------------------
print("\n[1] Code compiles")
_files = [
    'src/session/fp_chromium.py', 'src/session/session_manager.py',
    'src/session/purchase_executor.py', 'src/purchasing/worker_pool.py',
    'src/purchasing/bulletproof_purchase_manager.py', 'relogin_one.py',
    'harvest_accounts.py', 'src/utils/save_login.py',
]
for f in _files:
    try:
        py_compile.compile(str(ROOT / f), doraise=True); ok(f"compiles: {f}")
    except Exception as e:
        fail(f"compile error in {f}: {e}")

# ---- 2. Imports -------------------------------------------------------------
print("\n[2] Imports resolve")
try:
    import src.session.fp_chromium as fp
    ok("import fp_chromium")
except Exception as e:
    fail(f"cannot import fp_chromium: {e}")
    print("\nABORT — core module will not import."); raise SystemExit(1)
# 2026-08-22: the login browsers + the business real-Chrome purchase arm apply the
# account_identity JS spoof, whose UA major MUST equal the real installed Chrome
# major (a mismatch is the exact Shape login-block vector from 06-24; it went
# unnoticed on 08-21 when Chrome auto-updated 150->151). Warn on drift so it is
# caught before the drop, not after.
try:
    import re as _re, glob as _glob
    from src.session.account_identity import _CHROME_BUILDS as _BUILDS
    _spoof_major = str(_BUILDS[0]).split('.')[0]
    _real_major = None
    _appdir = r'C:\Program Files\Google\Chrome\Application'
    _vers = [os.path.basename(p) for p in _glob.glob(_appdir + r'\*')
             if _re.fullmatch(r'\d+\.\d+\.\d+\.\d+', os.path.basename(p))]
    if _vers:
        _vers.sort(key=lambda v: [int(x) for x in v.split('.')])
        _real_major = _vers[-1].split('.')[0]
    if _real_major is None:
        warn(f"could not read installed Chrome version under {_appdir}; verify account_identity major {_spoof_major} matches it by hand")
    elif _real_major == _spoof_major:
        ok(f"account_identity UA major {_spoof_major} matches installed Chrome {_vers[-1]}")
    else:
        fail(f"account_identity UA major {_spoof_major} != installed Chrome major {_real_major} ({_vers[-1]}) "
             f"-- update src/session/account_identity.py _CHROME_BUILDS to {_vers[-1]} and re-run hand_login_all.bat")
except Exception as e:
    warn(f"Chrome-major coherence check skipped: {e}")
try:
    from src.purchasing.worker_pool import _build_worker_configs_from_accounts
    from harvest_accounts import load_accounts
    ok("import worker_pool + harvest_accounts loaders")
    _loaders = True
except Exception as e:
    warn(f"loader import failed (config cross-check skipped): {e}")
    _loaders = False

CFG = ROOT / 'config' / 'target_accounts.json'

# ---- 3. Config is drop-ready ------------------------------------------------
print("\n[3] Config: config/target_accounts.json")
enabled = []
try:
    raw = json.load(open(CFG, encoding='utf-8'))
    enabled = [a for a in raw.get('accounts', [])
               if isinstance(a, dict) and a.get('enabled', True) is not False]
    ok(f"{len(enabled)} enabled account(s)")
    for a in enabled:
        aid = a.get('account_id')
        cvv = str(a.get('cvv', '')).strip()
        if cvv.isdigit() and 3 <= len(cvv) <= 4:
            ok(f"{aid}: valid per-account cvv")
        else:
            fail(f"{aid}: MISSING/invalid cvv — checkout WILL fail for this account")
        if str(a.get('proxy_url', '')).strip():
            ok(f"{aid}: has a purchase proxy_url")
        else:
            warn(f"{aid}: empty proxy_url -> exits HOME IP (shared-IP 429 risk if others also empty)")
    if _loaders:
        farm = {a['account_id']: (a['session_path'], a['profile_dir']) for a in load_accounts(CFG)}
        fleet = {c.account_id: (c.session_path, c.profile_dir) for c in _build_worker_configs_from_accounts(CFG)}
        if farm == fleet:
            ok("login farm and purchase fleet derive IDENTICAL paths (no logged-out-racer risk)")
        else:
            fail(f"path divergence: farm={farm} fleet={fleet}")
except Exception as e:
    fail(f"config check failed: {e}")

# ---- 4. fingerprint-chromium binary ----------------------------------------
print("\n[4] fingerprint-chromium binary")
fp.reset_cache()
exe = fp.executable_path()
if exe and Path(exe).is_file():
    ok(f"binary present: {exe}")
else:
    fail("chrome.exe NOT found (set TARGET_FP_CHROMIUM_PATH, or place under ~/fp-chromium/win)")

# ---- 5. Purchase/login decoupling under the bat's env ----------------------
print("\n[5] Decoupling (simulating the bat: TARGET_FP_CHROMIUM=1, no LOGIN flag)")
for k in ('TARGET_FP_CHROMIUM', 'TARGET_FP_CHROMIUM_LOGIN', 'TARGET_APPLY_FINGERPRINT', 'RELOGIN_SKIP_PROXY'):
    os.environ.pop(k, None)
os.environ['TARGET_FP_CHROMIUM'] = '1'
os.environ['TARGET_APPLY_FINGERPRINT'] = '1'
os.environ['RELOGIN_SKIP_PROXY'] = '1'
fp.reset_cache()
# 2026-08-21: mirror the bat's A/B levers so the plan printed here is the plan the
# bot will run (a skipped identity = real Chrome + real profile = the control arm).
_bat_txt = ''
try:
    _bat_txt = (ROOT / 'run_bot_with_nightly_restart.bat').read_text(errors='ignore')
except Exception:
    pass
def _bat_val(name: str) -> str:
    import re as _re
    m = _re.search(r'^\s*set\s+' + _re.escape(name) + r'=(.*)$', _bat_txt, _re.M)
    return (m.group(1).strip() if m else '')
for k in ('TARGET_FP_CHROMIUM_SKIP', 'TARGET_FP_CHROMIUM_ACCOUNTS', 'TARGET_FP_SEED_SALT'):
    os.environ.pop(k, None)
    v = _bat_val(k)
    if v:
        os.environ[k] = v
_ids = [a.get('account_id') for a in enabled] or ['primary']
_fp_on = [aid for aid in _ids if fp.launch_overrides(aid, 'America/Chicago')[0]]
_fp_off = [aid for aid in _ids if aid not in _fp_on]
for aid in _ids:
    pexe, pargs = fp.launch_overrides(aid, 'America/Chicago')
    lexe, _ = fp.login_overrides(aid, 'America/Chicago')
    if pexe:
        seed = next((a.split('=', 1)[1] for a in pargs if a.startswith('--fingerprint=')), '?')
        ok(f"{aid}: PURCHASE launches fingerprint-chromium (seed={seed}, profile {fp.profile_dir('nodriver-profile', aid)!s}-style)")
    else:
        warn(f"{aid}: PURCHASE on REAL Chrome + real profile (A/B control via TARGET_FP_CHROMIUM_SKIP)")
    ok(f"{aid}: LOGIN stays on normal Chrome (fp-login OFF)") if (lexe is None and not fp.login_enabled()) else fail(f"{aid}: login would use fp-chromium -> Shape blocks it")
if not _fp_on:
    fail("TARGET_FP_CHROMIUM=1 but NO identity launches fp-chromium — check TARGET_FP_CHROMIUM_SKIP/_ACCOUNTS")
_salt = fp.seed_salt()
print(f"  [plan] fp-chromium ON for {_fp_on or '-'} | control (real Chrome) {_fp_off or '-'} | seed salt {_salt or '(none: static 08-11 seeds)'}")
if _salt:
    _unsalted = {aid: fp.fingerprint_seed(aid, '') for aid in _fp_on}
    if all(fp.fingerprint_seed(aid) != _unsalted[aid] for aid in _fp_on):
        ok(f"seed salt {_salt!r} rotates every fp identity's device")
    else:
        fail("seed salt set but seeds did not change — salt not reaching fingerprint_seed()")
for aid in _fp_off:
    _acc = next((a for a in enabled if a.get('account_id') == aid), {})
    _prof = str(_acc.get('profile_dir') or '')
    if _prof and not Path(_prof).is_dir() and not (ROOT / _prof).is_dir():
        warn(f"{aid}: control runs on real profile {_prof!r} which is missing — hand-login creates it")

# ---- 6. Bat flags -----------------------------------------------------------
print("\n[6] run_bot_with_nightly_restart.bat flags")
try:
    bat = (ROOT / 'run_bot_with_nightly_restart.bat').read_text(errors='ignore')
    ok("TARGET_FP_CHROMIUM=1 present (purchase fp ON)") if 'set TARGET_FP_CHROMIUM=1' in bat else fail("TARGET_FP_CHROMIUM=1 missing -> purchase would run normal Chrome")
    ok("RELOGIN_SKIP_PROXY=1 present (login on HOME IP)") if 'set RELOGIN_SKIP_PROXY=1' in bat else fail("RELOGIN_SKIP_PROXY=1 missing -> login via BD IP would Shape-block")
    ok("TARGET_FP_CHROMIUM_LOGIN absent (login stays normal)") if 'TARGET_FP_CHROMIUM_LOGIN' not in bat else warn("TARGET_FP_CHROMIUM_LOGIN is set -> login would use fp-chromium (risky)")
    # 2026-08-21 post-mortem flags (defaults in code match these; the bat pins them explicitly)
    for _flag, _want, _why in (
            ('TARGET_ATC_GATE_COUNT_EDGE_429', '0', 'breaker must NOT count empty-body edge 429s (08-21: armed on 53/62 lottery denials)'),
            ('TARGET_ATC_401_LADDER', '0', 'ATC-401 repair ladder must be OFF (0/85 mints, 0/103 in-ladder retries converted)'),
            ('TARGET_RETRY_FORCE_REWARM', '0', 'inter-retry re-warm must not force a /cart reload + cart PUT'),
            ('TARGET_ATC_GATE_BREAKER', '1', 'breaker stays ON (401/DCO-only counting)')):
        _v = _bat_val(_flag)
        if _v == _want:
            ok(f"{_flag}={_want} ({_why})")
        elif _v == '':
            warn(f"{_flag} not set in the bat -> code default applies ({_why})")
        else:
            warn(f"{_flag}={_v} (expected {_want}: {_why})")
except Exception as e:
    fail(f"could not read the bat: {e}")

# ---- 7. Launchers -----------------------------------------------------------
print("\n[7] Launchers")
ok("hand_login_all.bat present") if (ROOT / 'hand_login_all.bat').is_file() else fail("hand_login_all.bat missing")
ok("buggy relogin_fp.bat removed") if not (ROOT / 'relogin_fp.bat').exists() else warn("old relogin_fp.bat still here — do NOT use it (BD-IP login bug)")

# ---- 8. Session files (login refreshes these; values never printed) --------
print("\n[8] Session files (you refresh these when you log in)")
AUTH = ('accessToken', 'login-session', 'idToken', 'refreshToken')
for a in enabled:
    sp = ROOT / str(a.get('session_path') or 'target.json')
    aid = a.get('account_id')
    if not sp.exists():
        warn(f"{aid}: {sp.name} missing — created at login"); continue
    try:
        d = json.load(open(sp, encoding='utf-8'))
        cks = d.get('cookies', [])
        names = {c.get('name') for c in cks}
        if cks and any(n in names for n in AUTH):
            ok(f"{aid}: {sp.name} has {len(cks)} cookies incl. auth (saved {d.get('saved_at', '?')})")
        else:
            warn(f"{aid}: {sp.name} has no auth cookie — LOG IN to refresh")
    except Exception as e:
        warn(f"{aid}: {sp.name} unreadable ({e}) — LOG IN to refresh")

# ---- 9. Logging coverage (informational) -----------------------------------
print("\n[9] Logging coverage — where to look on drop night")
for m in [
    "logs/relogin.log            login: per-account IP/identity, 'LOGGED IN', cookies saved",
    "logs/runs/run_*.log         the drop: grep 'FP_CHROMIUM'=fp device active, 'Injected N/M cookies'=session loaded, ATC 2xx/401/429",
    "logs/purchases/purchase_*   per-SKU purchase detail (one file per attempt)",
    "  grep '[CVV][ERROR]'       fires if an account is missing its cvv",
    "  grep '[MULTI_SKU_MISS]'   fires if a 2nd hot SKU is skipped while one is active",
    "logs/bot_restart_wrapper.log  crash/restart wrapper + [DEADMAN] login-budget line",
]:
    print(f"  [log] {m}")

# ---- Summary ----------------------------------------------------------------
print("\n" + "=" * 76)
print(f"RESULT: {len(OKS)} pass · {len(WARNS)} warn · {len(FAILS)} fail")
if FAILS:
    print("STATUS: NOT READY — fix the [FAIL] item(s) above before dropping.")
    code = 1
elif WARNS:
    print("STATUS: READY (with warnings). The warnings are typically 'log in to refresh")
    print("        sessions' — run hand_login_all.bat, then re-run this preflight for all-green.")
    code = 2
else:
    print("STATUS: ALL GREEN — code, config, flags, binary, and decoupling verified.")
    code = 0
print("=" * 76)
raise SystemExit(code)

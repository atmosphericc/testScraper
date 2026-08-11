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
pexe, _ = fp.launch_overrides('primary', 'America/Chicago')
lexe, _ = fp.login_overrides('primary', 'America/Chicago')
ok("PURCHASE launches fingerprint-chromium (distinct device)") if pexe else fail("purchase did NOT enable fp-chromium")
ok("LOGIN stays on normal Chrome (fp-login OFF)") if (lexe is None and not fp.login_enabled()) else fail("login would use fp-chromium -> Shape blocks it")

# ---- 6. Bat flags -----------------------------------------------------------
print("\n[6] run_bot_with_nightly_restart.bat flags")
try:
    bat = (ROOT / 'run_bot_with_nightly_restart.bat').read_text(errors='ignore')
    ok("TARGET_FP_CHROMIUM=1 present (purchase fp ON)") if 'set TARGET_FP_CHROMIUM=1' in bat else fail("TARGET_FP_CHROMIUM=1 missing -> purchase would run normal Chrome")
    ok("RELOGIN_SKIP_PROXY=1 present (login on HOME IP)") if 'set RELOGIN_SKIP_PROXY=1' in bat else fail("RELOGIN_SKIP_PROXY=1 missing -> login via BD IP would Shape-block")
    ok("TARGET_FP_CHROMIUM_LOGIN absent (login stays normal)") if 'TARGET_FP_CHROMIUM_LOGIN' not in bat else warn("TARGET_FP_CHROMIUM_LOGIN is set -> login would use fp-chromium (risky)")
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

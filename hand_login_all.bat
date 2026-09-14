@echo off
REM ==========================================================================
REM  hand_login_all.bat - reliable hand-login for every account, the PROVEN
REM  way: HOME IP (RELOGIN_SKIP_PROXY=1) + real Chrome. This is what passes
REM  Target Shape at login. fingerprint-chromium is for the PURCHASE side only
REM  (set TARGET_FP_CHROMIUM=1 in run_bot_with_nightly_restart.bat, NOT here).
REM  Sign in BY HAND in each window and clear any one-time device code.
REM ==========================================================================
pushd "%~dp0"
set RELOGIN_SKIP_PROXY=1
REM 2026-09-13: same User-Agent mode as run_bot_with_nightly_restart.bat so each
REM jar is minted under the UA the purchase browser will present (engine = the
REM real Chrome's own UA + brands, no CDP override). Keep the two bats in sync.
set TARGET_UA_MODE=engine
echo.
echo === Hand-login ALL accounts (home IP, real Chrome) - sign in by hand ===
venv\Scripts\python.exe relogin_one.py all --manual
echo.
echo === Readiness check (want 3/3 MEMBER) ===
venv\Scripts\python.exe check_session_readiness.py
echo.
pause

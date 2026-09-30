@echo off
REM ==========================================================================
REM  hand_login_all_force.bat - FORCED hand-login of EVERY enabled account, one
REM  after another (2026-09-29). Same flags as hand_login_primary_force.bat.
REM  --force signs each account out first so you always get a real login prompt:
REM  plain hand_login_all.bat validates first and skips a guest jar that only
REM  LOOKS logged in (all three jars were guests on 09-28). HOME IP, real Chrome.
REM  For each account: sign in by hand in the window, clear any code or
REM  Press and Hold, wait for your name on /account, press ENTER here.
REM  Stop the bot wrapper BEFORE running this - it refuses while app.py runs.
REM ==========================================================================
pushd "%~dp0"
set RELOGIN_SKIP_PROXY=1
set TARGET_UA_MODE=engine
set TARGET_FP_CHROMIUM=0
REM relogin_one.py hard-exits after max(600, 120+90*N) s for the WHOLE run;
REM three hand logins in a row need more than 10 minutes. 30 min here.
set RELOGIN_DEADMAN_S=1800
echo.
echo === FORCED hand-login of ALL accounts (home IP, real Chrome) - sign in by hand in each window ===
venv\Scripts\python.exe relogin_one.py all --manual --force
echo.
echo === Readiness check (want 3/3 MEMBER, each with a token expiry time) ===
venv\Scripts\python.exe check_session_readiness.py
echo.
pause

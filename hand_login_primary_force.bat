@echo off
REM ==========================================================================
REM  hand_login_primary_force.bat - FORCED hand-login of the primary account.
REM  Same as hand_login_alt1_force.bat but for primary (2026-09-25: the boot
REM  login probe failed 4x and primary's saved token turned GUEST, sut=G).
REM  --force signs out first so you get a real login prompt; validate-first
REM  would otherwise call a guest jar already logged in. HOME IP, real Chrome.
REM  Stop the bot wrapper BEFORE running this - it refuses while app.py runs.
REM ==========================================================================
pushd "%~dp0"
set RELOGIN_SKIP_PROXY=1
set TARGET_UA_MODE=engine
set TARGET_FP_CHROMIUM=0
echo.
echo === primary FORCED hand-login (home IP, real Chrome) - sign in by hand when the window opens ===
venv\Scripts\python.exe relogin_one.py primary --manual --force
echo.
echo === Readiness check (want 3/3 MEMBER) ===
venv\Scripts\python.exe check_session_readiness.py
echo.
pause

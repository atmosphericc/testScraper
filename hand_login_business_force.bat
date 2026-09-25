@echo off
REM ==========================================================================
REM  hand_login_business_force.bat - FORCED hand-login of the business account.
REM  Same as hand_login_primary_force.bat but for business (2026-09-25: its
REM  member-token mint failed from 06:38 and two scripted relogins failed).
REM  --force signs out first so you get a real login prompt; validate-first
REM  would otherwise call a guest jar already logged in. HOME IP, real Chrome.
REM  Stop the bot wrapper BEFORE running this - it refuses while app.py runs.
REM ==========================================================================
pushd "%~dp0"
set RELOGIN_SKIP_PROXY=1
set TARGET_UA_MODE=engine
set TARGET_FP_CHROMIUM=0
echo.
echo === business FORCED hand-login (home IP, real Chrome) - sign in by hand when the window opens ===
venv\Scripts\python.exe relogin_one.py business --manual --force
echo.
echo === Readiness check (want 3/3 MEMBER) ===
venv\Scripts\python.exe check_session_readiness.py
echo.
pause

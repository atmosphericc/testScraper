@echo off
pushd "%~dp0"
REM 2026-09-04: alt-1 recovery. Its saved session can only mint a GUEST token, and every
REM previous attempt either skipped it ("already logged in") or went over the BD proxy IP
REM (Shape-blocked -> "Something went wrong"). This signs alt-1 OUT first and opens a real
REM login window on the HOME IP in real Chrome, exactly the path that worked for
REM primary/business at 23:44. Sign in by hand, clear any Press & Hold / code, press ENTER.
set RELOGIN_SKIP_PROXY=1
set TARGET_FP_CHROMIUM=0
echo.
echo === alt-1 FORCED hand-login (home IP, real Chrome) - sign in by hand when the window opens ===
venv\Scripts\python.exe relogin_one.py alt-1 --manual --force
echo.
echo === Readiness check (want alt-1 MEMBER) ===
venv\Scripts\python.exe check_session_readiness.py
echo.
pause

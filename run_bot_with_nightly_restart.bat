@echo off
REM ===========================================================================
REM  run_bot_with_nightly_restart.bat  --  Fix #7, post-5/21 incident plan
REM ---------------------------------------------------------------------------
REM  Overnight crash-resilience wrapper for the Target bot.
REM
REM  The bot is run unattended overnight. If app.py exits or crashes at 3am,
REM  this loop relaunches it immediately so the bot is not dead until morning
REM  (and does not miss a drop).
REM
REM  There is intentionally NO timed runtime cutoff: the operator stops the
REM  bot manually each morning, so a MAX_RUNTIME cutoff would never fire.
REM  This wrapper exists purely to survive crashes while unattended.
REM
REM  Usage:  double-click, or run from a cmd window in the repo root.
REM  Stop:   press Ctrl+C, then answer Y to "Terminate batch job".
REM ===========================================================================

setlocal EnableExtensions
cd /d "%~dp0"

REM Python venv path differs per machine: some boxes use .venv (dot-prefixed),
REM this desktop uses venv (no dot) and it has the full deps. Auto-detect
REM whichever actually exists on disk so the wrapper runs everywhere without a
REM per-machine edit. Pointing at the wrong/missing one was BOTH the 2026-05-22
REM failure (venv\ was a broken stub -> ModuleNotFoundError: zendriver) AND the
REM 2026-06-04 failure (.venv\ absent on this desktop -> guard hang on boot).
set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=%~dp0venv\Scripts\python.exe"
set "LOGDIR=%~dp0logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "RUNLOG=%LOGDIR%\bot_restart_wrapper.log"

REM Target production stack (app.py also defaults this, set explicitly anyway).
set USE_RESILIENT_STACK=1

REM Force REAL-PURCHASE mode. app.py places real orders unless TEST_MODE=true;
REM pinned to false here so a stray TEST_MODE=true left in the environment
REM cannot silently turn an unattended live run into a no-op (zero orders).
set "TEST_MODE=false"

if not exist "%PYTHON%" (
    echo [ERROR] venv python not found — checked .venv\Scripts and venv\Scripts
    echo Create the venv first, then re-run. Closing in ~30s.
    REM Bounded wait via ping (not pause/timeout): pause hangs forever on an
    REM unattended boot, and timeout returns instantly without a real console.
    ping -n 31 127.0.0.1 >nul
    exit /b 1
)

set /a ATTEMPT=0

:loop
set /a ATTEMPT+=1
echo.
echo === launch #%ATTEMPT%  --  %date% %time% ===
echo [%date% %time%] launch #%ATTEMPT% app.py >> "%RUNLOG%"

"%PYTHON%" app.py
set "EXITCODE=%ERRORLEVEL%"

echo [%date% %time%] app.py exited code=%EXITCODE% >> "%RUNLOG%"
echo.
echo app.py exited (code=%EXITCODE%). Restarting in 10s -- press Ctrl+C to stop.
REM `timeout` returns instantly when stdin isn't a true console (some launch
REM contexts) — caused a 0.27s/relaunch crash-loop on 2026-05-22 that burned
REM through 5 relaunches in <2s and clashed on Chrome's --user-data-dir lock
REM (STATUS_DLL_INIT_FAILED). `ping` is the reliable batch-sleep idiom:
REM 11 pings at 1s intervals = ~10s, no console dependency.
ping -n 11 127.0.0.1 >nul
goto loop

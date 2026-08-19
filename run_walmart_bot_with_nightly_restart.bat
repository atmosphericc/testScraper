@echo off
REM ===========================================================================
REM  run_walmart_bot_with_nightly_restart.bat
REM ---------------------------------------------------------------------------
REM  Overnight crash-resilience wrapper for the WALMART bot -- the Walmart analog
REM  of run_bot_with_nightly_restart.bat (Target). It supervises the Walmart
REM  dashboard `python -m walmart.walmart_app` (Flask, port 5001) so that if the
REM  app dies OR silently zombies overnight, this loop restarts it instead of
REM  leaving the bot dead until morning (and missing a drop).
REM
REM  There is intentionally NO timed runtime cutoff: the operator stops the bot
REM  manually each morning. This wrapper exists purely to survive failures while
REM  unattended.
REM
REM  ---- TWO DIFFERENCES vs the Target wrapper -- both load-bearing ----------
REM
REM  1) Walmart login is SINGLE-ACCOUNT and INTERACTIVE. There is no working
REM     non-interactive relogin: walmart_relogin.py waits at an input() prompt
REM     for a hand login (+ possible CAPTCHA/2FA), and walmart_session_bootstrap
REM     asks "Proceed? [y/N]". (A programmatic session_manager.login() exists but
REM     is never wired in.) So session prep is a guided ONE-TIME step ABOVE the
REM     loop -- never inside it. Re-running an interactive login in a crash loop
REM     would hang the box on the first relaunch.
REM
REM  2) walmart_app.py has NO "not-logged-in" exit code (Target app.py exits 87).
REM     When its manager/monitor thread fails to start, walmart_app LOGS the
REM     error and the Flask dashboard keeps serving on :5001 FOREVER with no
REM     monitoring and no purchasing -- a silent "dashboard-only zombie". A
REM     wrapper that only watches the process exit code can NEVER catch that,
REM     because the process never exits. So this wrapper runs the app in the
REM     background and polls the app's own /health endpoint via
REM     walmart_health_probe.py; a sustained running=false verdict means zombie,
REM     and the wrapper kills + relaunches. (Fix the gap at the source by adding
REM     an exit-87-style guard to walmart_app.py; until then this probe covers it.)
REM
REM  Usage:  double-click, or run from a cmd window in the repo root.
REM          Add the argument  fresh  to force a new hand login + reseed:
REM              run_walmart_bot_with_nightly_restart.bat fresh
REM  Stop:   press Ctrl+C, then answer Y to "Terminate batch job".
REM ===========================================================================

setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

REM Python venv path differs per machine: some boxes use .venv (dot-prefixed),
REM this desktop uses venv (no dot). Auto-detect whichever exists (same logic as
REM the Target wrapper -- pointing at a missing/broken venv caused two separate
REM Target boot failures).
set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=%~dp0venv\Scripts\python.exe"
set "LOGDIR=%~dp0logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "RUNLOG=%LOGDIR%\walmart_bot_restart_wrapper.log"

REM ---------------------------------------------------------------------------
REM  Walmart production stack env.
REM ---------------------------------------------------------------------------
REM  Resilient stock-check stack: N persistent Chromes pinned to BD ISP IPs
REM  firing bulk stock checks (Walmart analog of Target's USE_RESILIENT_STACK).
REM  Reads the `proxies` list in config\proxyIps.json (verified present: 16 IPs).
set WALMART_USE_RESILIENT=1

REM  LIVE checkout. walmart_app.py gates Place Order on CHECKOUT_MODE==PRODUCTION
REM  ("LIVE" is only the dashboard label; the executor's real branch is named
REM  PRODUCTION). Pin it here so a stray CHECKOUT_MODE=TEST in the environment
REM  cannot silently turn an unattended live run into a no-op.
set CHECKOUT_MODE=PRODUCTION

REM  Chrome count -- PINNED, not left to auto-size. Unset would launch ONE
REM  Chrome PER active proxy = 16 headful Chromes (walmart_stock_resilient.py:23
REM  warns this OOMs a normal box), AND only s1/s2 session profiles are actually
REM  seeded on disk today -- the other 14 would boot logged-OUT. So pin to the
REM  number of seeded profiles. Raise this only after seeding more profiles via
REM  the session-prep step (run with the  fresh  argument after adding proxies).
set WALMART_RESILIENT_NUM_CHROMES=2
set WALMART_RESILIENT_FIRST_PORT=25000

REM  Stock-check rate. The code has NO RPS-vs-N clamp -- it only warns -- and
REM  Walmart's PerimeterX ceiling is ~0.5 RPS PER IP (walmart_adapter.py:130).
REM  With NUM_CHROMES=2 that is 1.0 total. (Code default is 6.0, which at N=2
REM  would be 3.0/IP = 6x over the ceiling. Do NOT just take the default here.)
REM  Keep this <= 0.5 * WALMART_RESILIENT_NUM_CHROMES whenever you change N.
set WALMART_RESILIENT_RPS=1.0

REM  NOTE: WALMART_EMAIL / WALMART_PASSWORD / WALMART_CVV are read from .env by
REM  the app. Without WALMART_CVV set, CVV entry is skipped and checkout will
REM  fail at payment -- confirm it is populated before a real drop.

if not exist "%PYTHON%" (
    echo [ERROR] venv python not found -- checked .venv\Scripts and venv\Scripts
    echo Create the venv first, then re-run. Closing in ~30s.
    REM Bounded wait via ping (pause hangs forever unattended; timeout returns
    REM instantly with no real console). Same idiom the Target wrapper uses.
    ping -n 31 127.0.0.1 >nul
    exit /b 1
)

REM ===========================================================================
REM  ONE-TIME SESSION PREP  (interactive -- runs ONCE, ABOVE the loop)
REM ---------------------------------------------------------------------------
REM  Skipped automatically if a seeded session already exists AND you did not ask
REM  for a refresh. Force a fresh hand login + reseed (session expired, or first
REM  run on a new box) with:  run_walmart_bot_with_nightly_restart.bat fresh
REM ===========================================================================
set "WANT_FRESH="
if /I "%~1"=="fresh" set "WANT_FRESH=1"

set "SEEDED=%~dp0state\walmart_session_profiles\s1"
if defined WANT_FRESH goto :do_session_prep
if exist "%SEEDED%" (
    echo [INFO] Seeded Walmart session found -- skipping login. Run with argument  fresh  to reseed.
    echo [!date! !time!] session prep skipped (already seeded) >> "%RUNLOG%"
    goto :after_session_prep
)

:do_session_prep
echo.
echo ===========================================================================
echo   WALMART SESSION PREP  (one-time, interactive)
echo ===========================================================================
echo   A browser window will open. Log into walmart.com by hand, then press
echo   ENTER in this window. Then answer  y  at the bootstrap's Proceed? prompt.
echo   This seeds the session profiles the bot runs on.
echo ===========================================================================
echo [!date! !time!] session prep: walmart_relogin.py >> "%RUNLOG%"
"%PYTHON%" walmart_relogin.py
echo [!date! !time!] walmart_relogin.py done code=!ERRORLEVEL! >> "%RUNLOG%"

echo.
echo === seeding session profiles from that login (walmart_session_bootstrap) ===
echo   NOTE: it will list the sessions and ask  Proceed? [y/N]  -- answer  y.
echo [!date! !time!] session seed: walmart_session_bootstrap --first 2 --force >> "%RUNLOG%"
REM --first 2 seeds exactly s1/s2, matching WALMART_RESILIENT_NUM_CHROMES=2 above
REM (the bootstrap otherwise seeds one profile per proxy = 16). --force reseeds
REM even if s1/s2 already exist (the fresh login is newer than them). If you
REM raise NUM_CHROMES, raise --first here to match.
"%PYTHON%" -m walmart.walmart_session_bootstrap --first 2 --force
echo [!date! !time!] session seed done code=!ERRORLEVEL! >> "%RUNLOG%"

:after_session_prep

set /a ATTEMPT=0

:loop
set /a ATTEMPT+=1
echo.
echo === launch #!ATTEMPT!  --  !date! !time! ===
echo [!date! !time!] launch #!ATTEMPT! walmart_app >> "%RUNLOG%"

REM Launch the app in the BACKGROUND so this script can health-probe it while it
REM runs. `start /b` keeps it in this console (Ctrl+C still reaches it). The PID
REM is captured by tagging the window title and matching it back via tasklist,
REM so we can kill exactly this launch (not any other python.exe on the box).
set "APPTAG=WALMART_BOT_!ATTEMPT!_!RANDOM!"
start "!APPTAG!" /b "%PYTHON%" -m walmart.walmart_app

REM Give Flask + the manager/monitor time to boot before the first health check.
REM The resilient stack launches N Chromes + logs in + warms up, which routinely
REM takes 30-60s. Probing sooner would false-positive "zombie" during warmup.
echo    booting -- first health check in ~60s ...
ping -n 61 127.0.0.1 >nul

set /a ISTREAK=0

:supervise
REM Poll walmart_health_probe.py, which reads the boot verdict from the app's
REM OWN LOG (logs\walmart_app_<ts>.log), NOT from /health. This is deliberate:
REM /health's `running` flag reports true even in the dead-monitor zombie state
REM (manager._running is set at startup and never cleared when the separate
REM resilient-checker start fails), so it cannot detect the zombie. The log line
REM "[APP] Resilient stock checker started" is the authoritative green signal.
REM   Exit codes:
REM   0  = GREEN  (log confirms monitor started + process reachable) -> supervise
REM   10 = DEAD   (log shows checker/manager start failed, or NOT LOGGED IN)
REM              -> authoritative, restart immediately (the log does not lie)
REM   20 = INDETERMINATE (no verdict line yet; still booting OR crashed)
REM              -> require a streak before restarting so a slow boot is spared
"%PYTHON%" walmart_health_probe.py >nul 2>&1
set "HEALTH=!ERRORLEVEL!"

if "!HEALTH!"=="0" (
    set /a ISTREAK=0
    REM Green -- re-check in ~30s.
    ping -n 31 127.0.0.1 >nul
    goto supervise
)

if "!HEALTH!"=="10" (
    REM Terminal boot failure straight from the log -- no streak needed.
    echo [!date! !time!] health=DEAD -- boot logged monitor failure / not-logged-in, restarting >> "%RUNLOG%"
    echo app monitor failed to start (see logs\walmart_app_*.log) -- restarting.
    goto restart
)

REM HEALTH=20 (indeterminate): no green line and not reachable. Right after a
REM launch this is just a slow boot (N Chromes + login + warmup, up to ~minutes),
REM so require the verdict to persist across several checks ~20s apart before
REM concluding the process actually crashed. A real green boot clears ISTREAK.
set /a ISTREAK+=1
echo [!date! !time!] health=INDETERMINATE streak !ISTREAK!/6 (no monitor-started line yet) >> "%RUNLOG%"
if !ISTREAK! GEQ 6 (
    echo app never confirmed its monitor started -- treating as crashed, restarting.
    goto restart
)
ping -n 21 127.0.0.1 >nul
goto supervise

:restart
REM Kill exactly this launch's python.exe by the window title we tagged it with,
REM plus any Chrome children it spawned for the resilient stack, so the relaunch
REM starts clean and does not collide on Chrome's --user-data-dir locks.
echo [!date! !time!] killing launch #!ATTEMPT! (tag !APPTAG!) >> "%RUNLOG%"
taskkill /f /fi "WINDOWTITLE eq !APPTAG!" >nul 2>&1
REM Fallback: some Windows builds don't set the title on `start /b`. As a
REM last resort, kill python.exe running walmart_app (module name matches the
REM command line). Narrow filter avoids nuking an unrelated python.exe.
taskkill /f /im python.exe /fi "WINDOWTITLE eq !APPTAG!" >nul 2>&1

echo.
echo Restarting in 10s -- press Ctrl+C to stop.
REM `ping` is the reliable batch-sleep idiom: 11 pings at 1s = ~10s, no console
REM dependency (a real `timeout` isn't guaranteed and hangs when stdin isn't a
REM true console -- the crash-loop hazard the Target wrapper documents).
ping -n 11 127.0.0.1 >nul
goto loop

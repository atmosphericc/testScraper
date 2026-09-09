@echo off
REM ===========================================================================
REM  probe_sweep_pool.bat  --  READ-ONLY RedSky probe of the Bright Data sweep
REM  pool (2026-09-07). For each listed IP it launches ONE real Chrome on a
REM  scratch profile through the production CONNECT forwarder (the exact pool
REM  transport), parks on target.com, does two bulk RedSky reads and prints
REM  OK-DATA / CAPTCHA. No login, no cart, no purchase code, no captcha
REM  interaction. ~40 s per IP.
REM
REM  Run it BEFORE a drop (bot stopped). Want: every IP OK-DATA.
REM    CAPTCHA on ALL  -> the pool is still HUMAN/PerimeterX-walled: rest it
REM                       longer and set RESILIENT_FORCE_TAB_FETCH=1 in
REM                       run_bot_with_nightly_restart.bat so a trusted account
REM                       browser detects stock for that night.
REM    CAPTCHA on SOME -> move those entries from 'proxies' to 'reserve_proxies'
REM                       in config\proxyIps.json.
REM
REM  Usage:  probe_sweep_pool.bat          (4 representative IPs, one per block)
REM          probe_sweep_pool.bat all      (all 16 sweep IPs, ~10 min)
REM          probe_sweep_pool.bat reserve  (the 4 reserve IPs)
REM          probe_sweep_pool.bat home     (fresh profile on the HOME IP, no proxy)
REM          probe_sweep_pool.bat 1.2.3.4  (any single IP from proxies/reserves)
REM  2026-09-07 23:05 verdict of the first run: 31.105.228.245 / 31.105.93.225 /
REM  168.158.143.27 -> CAPTCHA, 72.56.171.184 -> OK-DATA twice (same fresh
REM  profile, same machine) => the wall is IP-RANGE reputation, not the device.
REM  The bot now parks a walled session on its own and caps the sweep at 1/s per
REM  usable IP, so a partly-walled pool still works; every OK-DATA IP counts.
REM ===========================================================================
pushd "%~dp0"
set RESILIENT_HARVEST_VIA_LOCAL_IP=0
set "IPS=31.105.228.245 31.105.93.225 168.158.143.27 72.56.171.184"
if /i "%~1"=="all" set "IPS=31.105.228.245 31.105.148.70 31.105.153.101 31.105.155.55 31.105.250.38 31.105.255.43 31.105.171.7 31.105.183.245 31.105.198.34 168.158.143.27 72.56.171.184 168.158.141.141 31.105.93.225 31.105.212.250 31.105.222.246 31.105.227.219"
if /i "%~1"=="reserve" set "IPS=31.105.63.137 168.158.111.240 168.158.219.76 168.158.74.45"
if /i "%~1"=="home" set "IPS=home"
if not "%~1"=="" if /i not "%~1"=="all" if /i not "%~1"=="reserve" if /i not "%~1"=="home" set "IPS=%~1"
set /a OK=0
set /a BAD=0
for %%I in (%IPS%) do (
    echo.
    echo === probing sweep exit %%I  --  %date% %time% ===
    venv\Scripts\python.exe redsky_browser_probe_bd.py %%I
    if errorlevel 1 (set /a BAD+=1) else (set /a OK+=1)
)
echo.
echo === sweep pool probe done: OK-DATA=%OK%  walled/other=%BAD%  (exit 0 = OK-DATA, 2 = CAPTCHA) ===
if %BAD% GTR 0 echo !! at least one exit is walled -- see the notes at the top of this file.
pause

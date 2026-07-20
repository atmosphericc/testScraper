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

setlocal EnableExtensions EnableDelayedExpansion
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

REM ---------------------------------------------------------------------------
REM  Fingerprint kill-switch (2026-06-24 incident).
REM  account_identity spoofs a STALE Chrome build (130 vs the real 149 on this
REM  box) and even macOS-on-Windows for some accounts. That incoherent identity
REM  is flagged by Target's Shape on BOTH surfaces: it BLOCKED credential logins
REM  (business + alt-1 died to "Something went wrong" / "password did NOT
REM  advance") and is unproven (= risky) on add-to-cart. Until account_identity
REM  is rebuilt coherent (pin to the real Chrome major + host OS, drop the
REM  canvas/navigator tampering), run with the REAL browser identity everywhere:
REM    - relogin: skip the spoof AND log in on the clean HOME IP (a BD IP + spoof
REM      is what Shape rejects on the login endpoint).
REM    - app.py purchase tabs: skip the spoof (TARGET_APPLY_FINGERPRINT=0).
REM  Purchase EXIT IPs are unaffected — app.py still reads each account's BD IP
REM  from config/target_accounts.json (proxy_url). Trade-off: with the spoof off
REM  all accounts share the real device fingerprint + log in from the home IP,
REM  so per-account isolation now rests on profile + session + purchase-IP +
REM  card/address. Rollback (re-enable spoof): delete these three SET lines.
REM  2026-07-12: account_identity rebuilt COHERENT (real Chrome 150 + Windows), so
REM  per-account fingerprints are now SAFE and ON — validated: alt-1/primary/business
REM  all logged in clean on the HOME IP with the coherent FP. Distinct coherent devices
REM  are the strongest un-linker for multi-accounting (F5 research: device canvas/WebGL
REM  fingerprint is the top cross-account linker, survives IP rotation + cache clear).
REM  LOGIN stays on the HOME IP (RELOGIN_SKIP_PROXY=1): BD-IP login is Shape-blocked
REM  ("password did NOT advance") regardless of fingerprint — re-confirmed 2026-07-12.
REM  Purchase still exits each account's own BD IP. Rollback to shared-real-identity:
REM  set RELOGIN_SKIP_FINGERPRINT=1 and TARGET_APPLY_FINGERPRINT=0.
set RELOGIN_SKIP_FINGERPRINT=0
set RELOGIN_SKIP_PROXY=1
set TARGET_APPLY_FINGERPRINT=1

REM Force REAL-PURCHASE mode. app.py places real orders unless TEST_MODE=true;
REM pinned to false here so a stray TEST_MODE=true left in the environment
REM cannot silently turn an unattended live run into a no-op (zero orders).
set "TEST_MODE=false"

REM ---------------------------------------------------------------------------
REM  Purchase retry tuning (2026-06-30 drop fix).
REM  Wall B that morning = an ATC-level 429 demand-throttle: a worker fired 24
REM  ATC shots (all instant 429) then QUIT at the default 24-attempt cap with
REM  ~38s of in-stock budget STILL LEFT (deadline_hit=False). Raise the cap so
REM  the 110s TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S window is the binding limit,
REM  not the attempt count. Pre-ATC 429s are provably pre-submit — cannot double-buy.
REM  Wall A (checkout "busy" / RESERVATION_FAILURE) is handled by the new
REM  checkout_busy_retryable path; kill-switch: set TARGET_RETRY_CHECKOUT_BUSY=0.
set TARGET_RETRY_WHILE_IN_STOCK_MAX=40

REM ---------------------------------------------------------------------------
REM  In-place checkout re-shoot (2026-07-17 drop fix). That drop went 0-for: the
REM  ATC succeeded 3x (HTTP 201, item in cart) but every checkout POST was 429
REM  FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION (10 that night vs 3 on the 07-14 win).
REM  On a checkout 429/424 the item is still in the cart and NO order committed, so
REM  the executor now re-fires the place-order POST IN PLACE N times (fresh Shape +
REM  ~3s spacing) before clearing the cart and re-racing the ATC wall, which wasted
REM  the rare 201. Double-buy-safe: re-shoots ONLY while status stays 429/424 and
REM  stops the instant an order_id lands. Validated: tests/test_checkout_inplace_reshoot.py
REM  (8/8, no browser). Kill-switch: set TARGET_CHECKOUT_INPLACE_RETRY_N=0.
set TARGET_CHECKOUT_INPLACE_RETRY_N=4

REM ---------------------------------------------------------------------------
REM  Token keep-fresh ON (2026-07-12, CORRECTED). The F5 research overturned the
REM  earlier "harvesting causes a Shape block" theory: our inline fetch() re-signs
REM  Shape per request, so we PASS Shape (424, never a 403 block) — the ATC 401 is
REM  the WRITE-AUTH/member-token layer, not Shape. Token freshness is THE #1 lever.
REM  Keep-fresh keeps a MEMBER token hot 24/7 (the sentinel's rung-0 member-token
REM  check every ~5 min), so the bot is drop-ready no matter WHEN it is started
REM  (fixes the "started hours before the drop -> 4h token expired -> 401" gap).
REM  It is member-aware: if it can only mint a GUEST token (degraded login-session)
REM  it escalates the ladder to a credential relogin. The 07-10 guest-churn was a
REM  concurrent live session (personal Chrome logged into an account) evicting the
REM  token faster than the relogin cap — OPERATIONAL fix: sign out / close any
REM  personal-browser Target tabs before the drop. Kill-switch: set to 0.
set TARGET_TOKEN_KEEPFRESH=1

if not exist "%PYTHON%" (
    echo [ERROR] venv python not found — checked .venv\Scripts and venv\Scripts
    echo Create the venv first, then re-run. Closing in ~30s.
    REM Bounded wait via ping (not pause/timeout): pause hangs forever on an
    REM unattended boot, and timeout returns instantly without a real console.
    ping -n 31 127.0.0.1 >nul
    exit /b 1
)

REM ---------------------------------------------------------------------------
REM  Personal-Chrome Target sign-out (2026-07-13). The operator's own Target
REM  login IS the bot's `primary` account, so a normal browsing session signed
REM  into target.com is a SECOND live session on that account — Target rotates
REM  the member token out from under the bot's session (the 07-10 guest-churn:
REM  every account holding a GUEST token at fire time). Confirmed 07-13: the
REM  personal Chrome held 48 target.com cookies incl. a live refreshToken.
REM  "Remember to sign out before the drop" failed three sessions running, so
REM  the bot heals it: close Chrome, delete ONLY target.com cookies, reopen
REM  Chrome with the tabs restored. Runs BEFORE the account logins so the bot's
REM  sessions end up the only live ones. Kill-switch: set CHROME_SIGNOUT_SKIP=1.
REM  Never fatal — a stale personal session degrades a drop, it can't break the bot.
REM ---------------------------------------------------------------------------
echo === signing personal Chrome out of Target  --  !date! !time! ===
"%PYTHON%" chrome_target_signout.py
echo [!date! !time!] chrome target sign-out done code=!ERRORLEVEL! >> "%RUNLOG%"

REM ---------------------------------------------------------------------------
REM  Multi-account session refresh — run ONCE, before the restart loop.
REM
REM  Brings every enabled account in config\target_accounts.json to a
REM  logged-in state and harvests its cookies (target.json / target-2.json /
REM  ...), which the WorkerPool then consumes (it auto-sizes from the same
REM  file). --auto does a SILENT REFRESH first (living sessions just re-harvest,
REM  no login, no Shape exposure) and only credential-logs the dead ones.
REM
REM  Deliberately OUTSIDE the :loop. app.py crash-restarts relaunch app.py
REM  only — re-running logins on every relaunch would be a login storm that
REM  trips Shape new-device challenges. Refresh once at wrapper start; the
REM  account session lasts days, far longer than one overnight run.
REM
REM  Skipped automatically when target_accounts.json is absent (single-account
REM  legacy setups keep using relogin.py / their existing target.json).
REM ---------------------------------------------------------------------------
if exist "%~dp0config\target_accounts.json" (
    echo === ensuring all account sessions are logged in  --  !date! !time! ===
    echo [!date! !time!] account login: relogin_one.py all >> "%RUNLOG%"
    REM relogin_one.py all = the PROVEN flow: per account, validate-first; only
    REM dead accounts get a full sign-out + re-login (username-first with the
    REM config email+password, KMSI, requestSubmit, 3x retry); each session saved
    REM to target.json / target-2.json / ... which the WorkerPool then consumes.
    REM NOTE: uses !delayed! expansion — the old %ERRORLEVEL% here expanded at
    REM PARSE time of this whole block (always 0), so failed logins were logged
    REM as code=0 and went unnoticed.
    "%PYTHON%" relogin_one.py all
    echo [!date! !time!] account login done code=!ERRORLEVEL! >> "%RUNLOG%"
) else (
    echo [INFO] config\target_accounts.json not found — single-account legacy mode ^(relogin.py^).
)

REM ---------------------------------------------------------------------------
REM  Drop-readiness echo (2026-07-14). After the sign-out + account logins above,
REM  print a per-account MEMBER-session verdict so a cold/guest account is VISIBLE
REM  at startup instead of discovered mid-drop. Reads the freshly-harvested
REM  target*.json jars — zero browser, ~2s, read-only. INFORMATIONAL ONLY: never
REM  blocks the launch (app.py + TARGET_TOKEN_KEEPFRESH keep healing 24/7). This
REM  is the whole pre-drop check baked in, so no separate command is needed.
REM  Kill-switch: set DROP_READINESS_SKIP=1.
REM ---------------------------------------------------------------------------
if not "%DROP_READINESS_SKIP%"=="1" (
    echo === drop-readiness check  --  !date! !time! ===
    "%PYTHON%" check_session_readiness.py
    echo [!date! !time!] drop-readiness check done code=!ERRORLEVEL! >> "%RUNLOG%"
)

set /a ATTEMPT=0
set /a RELOGIN_BURSTS=0

:loop
set /a ATTEMPT+=1
echo.
echo === launch #%ATTEMPT%  --  %date% %time% ===
echo [%date% %time%] launch #%ATTEMPT% app.py >> "%RUNLOG%"

"%PYTHON%" app.py
set "EXITCODE=%ERRORLEVEL%"

echo [%date% %time%] app.py exited code=%EXITCODE% >> "%RUNLOG%"
echo.

REM ---------------------------------------------------------------------------
REM  Exit code 87 = app.py booted but the Target session was NOT logged in
REM  (2026-07-03 fix: it used to idle forever as a dashboard-only zombie).
REM  Recover the PROVEN way: re-run relogin_one.py all (home IP, validate-first)
REM  and relaunch. Burst guard: after 3 consecutive 87-cycles, cool down 10
REM  minutes so a genuinely-broken account can't login-storm Shape all night.
REM ---------------------------------------------------------------------------
if "%EXITCODE%"=="87" (
    set /a RELOGIN_BURSTS+=1
    echo [!date! !time!] exit 87: not-logged-in — relogin cycle !RELOGIN_BURSTS!/3 >> "%RUNLOG%"
    if !RELOGIN_BURSTS! GEQ 4 (
        echo [!date! !time!] 3 relogin cycles failed — cooling down 10 min before next try >> "%RUNLOG%"
        echo Three relogin cycles failed. Cooling down 10 minutes...
        ping -n 601 127.0.0.1 >nul
        set /a RELOGIN_BURSTS=0
    )
    if exist "%~dp0config\target_accounts.json" (
        echo [!date! !time!] account relogin: relogin_one.py all >> "%RUNLOG%"
        "%PYTHON%" relogin_one.py all
        echo [!date! !time!] account relogin done code=!ERRORLEVEL! >> "%RUNLOG%"
    )
) else (
    set /a RELOGIN_BURSTS=0
)

echo app.py exited (code=%EXITCODE%). Restarting in 10s -- press Ctrl+C to stop.
REM `timeout` returns instantly when stdin isn't a true console (some launch
REM contexts) — caused a 0.27s/relaunch crash-loop on 2026-05-22 that burned
REM through 5 relaunches in <2s and clashed on Chrome's --user-data-dir lock
REM (STATUS_DLL_INIT_FAILED). `ping` is the reliable batch-sleep idiom:
REM 11 pings at 1s intervals = ~10s, no console dependency.
ping -n 11 127.0.0.1 >nul
goto loop

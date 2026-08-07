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
REM  CHECKOUT FAST LANE (2026-07-21 post-mortem, run_20260720_231634.log).
REM  THE measurement that explains 0-for-9: on wave 2 the ATC returned HTTP 201
REM  at t=0.92s, but the first place-order POST did not leave until t=3.4s -- and
REM  Target answered that clean first shot 429 RESERVATION_FAILURE. The 2.5s in
REM  between was tab.get("/checkout/start") (1.45s) plus the DOM poll for the
REM  Place Order button (0.87s). Pure dead time, and the inventory reservation
REM  died inside it. Every wave paid it: ATC->shot#1 measured 2.4 / 2.8 / 3.0 /
REM  3.3 / 3.6 / 3.9 / 4.1 / 5.0 / 10.1 seconds.
REM  The nav was never load-bearing -- it existed so the cart hydrates
REM  server-side first, and that hydration IS pre_checkout, which the old code
REM  fired FIRE-AND-FORGET just before navigating. (The 2026-05-07 attempt to
REM  drop the nav failed with 424 CART_COMPARISION_FAILURE_ERROR precisely
REM  because it raced that un-awaited pre_checkout.)
REM  The executor now runs ATC -> AWAIT pre_checkout -> place-order as ONE
REM  in-browser fetch chain, no navigation and no CDP round-trip between steps:
REM  ~0.85s from trigger to committed order instead of ~3.4s.
REM  Safety: a 2xx is treated as a committed order and never re-shot; a
REM  fired-but-no-response POST bails terminal (never retried); and the chain
REM  refuses to fire the place-order POST at all unless the ATC returned 201,
REM  pre_checkout returned 2xx, and every cart item is our TCIN. Skipped
REM  entirely while the CVV latch is on (that shot is a guaranteed 400).
REM  Validated: tests	est_fast_lane_checkout.py (37/37, incl. the chain's real
REM  JS run under node with a stubbed fetch). Requires TARGET_API_PLACE_ORDER,
REM  which app.py already defaults to true.
REM  Kill-switch (exact pre-07-21 nav behavior): set TARGET_FAST_LANE=0
set TARGET_FAST_LANE=1

REM  In-lane CVV (Endpoint 8, 2026-07-28). The 07-28 drop lost a won cart to
REM  400 MISSING_CREDIT_CARD_CVV on alt-1 (3rd night in a row for that account)
REM  while the DOM CVV modal's own network call was captured live on BOTH 07-24
REM  and 07-28 -- byte-identical: PUT /checkout_payments/v1/payment_instructions/
REM  {id} with {"card_details":{"cvv":..},"cart_id":..,"payment_type":"CARD",
REM  "wallet_mode":"NONE"} and no Shape headers. The fast lane now fires that
REM  PUT itself: latched accounts pre-PUT before the first shot (the lane is no
REM  longer disabled by the latch), unlatched accounts recover a po=400 with
REM  PUT + one re-shoot in the same chain. CVV comes from the per-account
REM  "cvv" field in config\target_accounts.json (all 3 verified present).
REM  Kill-switch (pre-07-28: latch disables the lane): set TARGET_FAST_LANE_CVV=0
set TARGET_FAST_LANE_CVV=1

REM  Do not re-shoot into Target's fast-selling limiter. The 07-17 fix above
REM  added the in-place re-shoot loop because FAST_SELLING killed that drop; the
REM  07-21 log then measured what re-shooting actually achieves. FAST_SELLING_
REM  ITEM_RATE_LIMIT_EXCEPTION answered 0 of ~20 re-shoots that night, and never
REM  once appeared on the FIRST checkout POST of a wave -- it only ever showed up
REM  from shot #2 onward. The loop was feeding the very limiter blocking it.
REM  Two minutes of quiet cleared it on its own (wave 6 got a clean first shot).
REM  So on FAST_SELLING the executor now backs off for this many seconds instead
REM  of re-shooting; every other 429/424 keeps the 07-17 behavior unchanged.
REM  Kill-switch (never back off, pre-07-21): set TARGET_FAST_SELLING_COOLDOWN_S=0
set TARGET_FAST_SELLING_COOLDOWN_S=45

REM  HOLD the won cart through that cooldown (2026-07-28). That drop won 4 carts
REM  and cleared every one after a FAST_SELLING/post-FS rejection, then re-raced
REM  the ATC wall 0-for-~250. The cart is strictly more valuable than a re-race:
REM  the executor now sits out the cooldown IN PLACE (cart intact, on /checkout)
REM  and lets the in-place re-shoot loop fire into the reopened window. A second
REM  FS rejection still stops it (one hold, bounded); capped by HOLD_MAX_S well
REM  under the manager's 140s coroutine guard.
REM  Validated: tests	est_checkout_inplace_reshoot.py + test_fast_lane_checkout.py.
REM  Kill-switch (07-21 clear+re-race): set TARGET_FAST_SELLING_HOLD_CART=0
set TARGET_FAST_SELLING_HOLD_CART=1
set TARGET_FAST_SELLING_HOLD_MAX_S=75

REM ---------------------------------------------------------------------------
REM  CDP-backpressure guard (2026-07-20). Overnight 07-19 the session sentinel
REM  destroyed and relaunched a HEALTHY Chrome 23 times (9 primary / 8 business /
REM  6 alt-1). Cause: the warmup tabs run persistent CDP Fetch interceptors, so a
REM  warmup /cart nav floods the single CDP websocket and the idle main-tab probe
REM  `evaluate("true")` queues past its 2s budget -- read as "websocket wedged".
REM  Each false wedge cost ~28s of BLOCKED PURCHASES (the sentinel holds the
REM  executor page lock for its whole ladder) plus the warm Shape header cache.
REM  get_page now re-probes ONCE with a longer budget on its final attempt: a
REM  busy socket answers, a dead one still fails both probes and escalates as
REM  before (~4s later). Validated: tests/test_wedge_recovery_smoke.py (17/17).
REM  Kill-switch (exact pre-07-20 behavior): set TARGET_TAB_HEALTH_SLOW_RETRY_S=0
set TARGET_TAB_HEALTH_SLOW_RETRY_S=6.0

REM  CDP paused-request LEAK guard (2026-07-23). The 07-21->22 run re-scoped the
REM  07-20 "false wedge" verdict: the wedges are REAL and on a rigid clock --
REM  every Chrome's CDP went dead ~70-75 min after ITS OWN launch (primary
REM  01:04 / 02:19 / 03:34 / 04:49 / 06:04 / 07:19, alt-1+business 5 min behind;
REM  22 sentinel destroys, each 3-6 min of blocked purchases on that account,
REM  and the 6s slow re-probe saved 0 of 80 because the socket stays dead for
REM  2.5+ min). Root-cause candidate shipped: the fetch interceptor's dedup
REM  early-return dropped re-paused events (redirect hops re-pause under the
REM  SAME request id; the two warmup tabs' interception-job ids also collide in
REM  the shared dedup set) WITHOUT Fetch.continueRequest -- each one a request
REM  paused forever, accumulating from launch until the browser wedges. The
REM  handler now continues dedup-hit events too (release-only, double-buy-safe)
REM  and logs "dedup hit #N" so tomorrow's log confirms or refutes the theory:
REM  counter climbing + wedges gone = confirmed; counter ~0 + wedges persist =
REM  look elsewhere. Validated: tests\test_cdp_dedup_leak_guard.py (7/7).
REM  Kill-switch (exact pre-07-23 drop-without-continue): set TARGET_CDP_DEDUP_CONTINUE=0
set TARGET_CDP_DEDUP_CONTINUE=1

REM ---------------------------------------------------------------------------
REM  CVV challenge handling (2026-07-21 drop fix). 07-20->21 went 0-for-9 with
REM  NINE clean ATC 201s -- the best add-to-cart night on record (07-14, the only
REM  win, got 2). Every place-order POST that reached Target came back 400
REM  tgt-cart-error-key=MISSING_CREDIT_CARD_CVV: Target now challenges the saved
REM  card, and our API body ({cart_type, channel_id}) carries no CVV, so the fast
REM  path is a GUARANTEED loss. Worse, 5-for-5 the DOM recovery that followed hit
REM  424 RESERVATION_FAILURE -- the ~1.0s the doomed API shot burned was enough to
REM  lose the reservation. The executor now latches the challenge (persisted to
REM  state\cvv_challenge_<account>.flag so it survives this restart wrapper) and
REM  goes DOM-first, where the CVV modal handler answers in ~0.08s.
REM  Kill-switch (always try API first, pre-07-21 behavior): TARGET_CVV_DOM_FIRST=0
REM  Force the latch on before a known drop:                 TARGET_CVV_REQUIRED=1
set TARGET_CVV_DOM_FIRST=1

REM  CVV latch AUTO-UNLATCH (2026-07-23). While latched the API place-order is
REM  skipped entirely, so the latch could never see Target DROP the challenge --
REM  it would keep the fast lane disabled on every future drop until someone
REM  remembered to set TARGET_CVV_REQUIRED=0. Definitive un-latch signal: an
REM  order CONFIRMS via the DOM path and no CVV modal appeared at any point in
REM  that purchase (if CVV were still required the order could not complete
REM  without the modal). Wrong-unlatch cost is bounded + self-healing: the next
REM  wave's API shot pays ~0.9s for a 400 and re-latches. Validated:
REM  tests\test_cvv_auto_unlatch.py. Kill-switch: set TARGET_CVV_AUTO_UNLATCH=0
set TARGET_CVV_AUTO_UNLATCH=1

REM  A RESERVATION_FAILURE renders as Target's generic "busy" copy, so the DOM
REM  loop used to claim it and re-click Place Order into a reservation the server
REM  had already torn down (15 wasted re-clicks x ~1.5s on 07-20->21). Now it
REM  bails immediately so the manager can re-race ATC inside the stock window.
REM  Kill-switch: set TARGET_RESERVATION_BAIL=0
set TARGET_RESERVATION_BAIL=1

REM  ROOT-CAUSE CANDIDATE for the CVV challenge (2026-07-21, HYPOTHESIS).
REM  Target's own help article says CVV re-entry is required when "a shipping
REM  address is updated during checkout". Loading /cart makes Target's page JS
REM  fire PUT /web_checkouts/v1/cart?...field_groups=ADDRESSES... against this
REM  account's cart -- and on 07-20->21 a background warmup /cart nav landed
REM  inside the checkout window, on the same session, immediately before every
REM  400 MISSING_CREDIT_CARD_CVV. A second concurrent cart write from a
REM  background tab is also not something a real shopper does. Warmup tabs now
REM  skip only the NAVIGATION while a purchase is live; the dummy POST still
REM  fires so the Shape ring keeps refilling (same path the routine <90s
REM  warm-tab skip already takes hourly), and force_fresh (ATC-401 recovery
REM  reload) still navigates. Confirm/refute via the cart-PUT body in
REM  logs\api_capture.log. Validated: tests\test_warmup_cart_nav_guard.py (5/5).
REM  Kill-switch: set TARGET_WARMUP_PAUSE_DURING_PURCHASE=0
set TARGET_WARMUP_PAUSE_DURING_PURCHASE=1

REM  Passive capture of Target's OWN CVV submit (PUT checkout_payments/v1/
REM  payment_instructions/<id>) -- the request the DOM path fires after the CVV
REM  modal is confirmed. Pass-through only, never aborts, writes to
REM  logs\api_capture.log. 2026-08-02: TURNED OFF -- the CVV PUT body was
REM  captured on 07-24, 07-28 and 5x on 07-31 (Endpoint 8 shipped AND
REM  live-validated: two alt-1 orders on 07-31 via in-lane pre-PUT cvv put=200),
REM  and the "only during a real checkout" premise proved wrong: warmup-tab
REM  /cart activity logged 10,840 CART_PUTs 24/7 (~11.5MB since 07-21).
REM  Re-enable (true) only to re-capture if Target changes the CVV endpoint.
set TARGET_API_CAPTURE_CHECKOUT_STEPS=false

REM ---------------------------------------------------------------------------
REM  2026-08-02 post-drop-audit fixes (07-31 = 7 orders/14 units; the audit of
REM  the losses + the 68h run shipped five flag-gated guards, all default-ON in
REM  code -- set here explicitly so this file stays the operator's changelog).
REM  1) Evicted-cart fast-bail: after 424 RESERVATION_FAILURE Target empties
REM     the cart server-side (items -> Saved for later); place-orders then 400
REM     with NO tgt-cart-error-key and the page says "no items in your cart" /
REM     "your cart is empty". Late 07-31 waves burned 15-25s each re-clicking
REM     that corpse. Now: terminal bail on the wire signature + a 1/s page-copy
REM     probe, so the manager re-races a FRESH ATC. Kill: TARGET_EMPTY_CART_BAIL=0
set TARGET_EMPTY_CART_BAIL=1
REM  2) Re-shoot re-nav: the 03:44 FS hold kept the won cart 45s, but the SPA
REM     bounced the tab to /cart mid-hold and the re-shoot loop aborted with
REM     ZERO post-hold shots. Now: ONE bounded re-nav to /checkout first.
REM     Kill: TARGET_RESHOOT_RENAV=0
set TARGET_RESHOOT_RENAV=1
REM  3) Level re-arm: resilient mode publishes stock events on OOS->IS flips
REM     ONLY, so a failed wave never re-raced while stock persisted (the two
REM     ~22-min street-date windows got 17-18 min of live stock with zero
REM     shots). Now: failed-but-still-stocked TCINs re-publish every 20s
REM     ('failed' states only -- purchased repeats still need a real flip).
REM     Kill: TARGET_LEVEL_REARM_S=0
set TARGET_LEVEL_REARM_S=20
REM  4) Dead-session park: with a dead login-session + relogin capped, the
REM     sentinel ladder restarted that account's Chrome every 5 min forever
REM     (576 restarts on 08-02) and hammered Target's login surface. Now: park
REM     the heavy rungs 30 min; the cheap token check keeps watching and any
REM     recovery (e.g. manual login) self-clears the park.
REM     Kill: TARGET_SENTINEL_DEAD_SESSION_BACKOFF_S=0
set TARGET_SENTINEL_DEAD_SESSION_BACKOFF_S=1800
REM  5) Cloak-verify backoff: post-drop the cloaking alarm fired every 30s for
REM     62.5h (~67k extra origin fetches) re-confirming the same all-OOS. Now:
REM     confirmed all-OOS widens the origin re-verify 60->300s; any hit or
REM     probe failure restores the sharp 30s. Kill: TARGET_CLOAK_VERIFY_BACKOFF=0
set TARGET_CLOAK_VERIFY_BACKOFF=1
REM ---------------------------------------------------------------------------
REM  2026-08-04 restock audit fixes (commit e718a71d). 4 orders/8 units, ALL on
REM  business; primary+alt-1 won ZERO carts on ~1,800 ATC attempts.
REM  6) Organic ATC fallback: 2,566 ATC 401s vs 22 201s while those accounts
REM     minted ~850 MEMBER tokens each and their warmup dummy POSTs (plain
REM     fetch, NO injected headers) passed ~92%%. Shape locks the REPLAYED-header
REM     dispatch per identity; page-signed fetches stay exempt. After every
REM     injected shot returns a RECEIVED 401 (provably no add), fire one ATC
REM     from the warmup tab unsigned by us. Kill: TARGET_ATC_NATIVE_FALLBACK=0
set TARGET_ATC_NATIVE_FALLBACK=1
REM  7) Non-destructive relogin: the 20:24 sentinel escalation signed primary
REM     OUT before the Shape-burned login failed, leaving a GUEST token for the
REM     next drop. Now the jar is snapshotted pre-signout and restored when the
REM     login fails. Kill: TARGET_RELOGIN_RESTORE_ON_FAIL=0
set TARGET_RELOGIN_RESTORE_ON_FAIL=1

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

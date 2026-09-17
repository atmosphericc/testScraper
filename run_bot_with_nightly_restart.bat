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
REM  Stop:   press Ctrl+C once. app.py shuts down cleanly (exit code 0) and the
REM          wrapper then STOPS instead of relaunching (2026-09-15 fix). If the
REM          batch's own "Terminate batch job (Y/N)?" prompt also appears, answer Y.
REM          Restart by re-running this script (a fresh top-of-file run).
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
REM  2026-09-04: sweep rate stays 3.0/s (operator requirement). The 403 tarpit was
REM  caused by TWO app.py instances running at once (orphan under system Python +
REM  the venv bot = ~6/s) plus restarts, NOT by 3/s itself; fixed by killing the
REM  orphan + swapping the burned sweep pool for the 4 clean reserves (proxyIps).
REM  History of the rest of this note:
REM  logins tripped a Target RedSky throttle: the 23:54 run read stock fine
REM  (~174 good reads) then went 100%% 403 within ~2.5 min, and every restart
REM  after started already-blocked. One bulk fetch covers all TCINs, so 1.5/s
REM  still checks every ~0.67s (wins land in 0.8-3.6s) at HALF the RedSky load,
REM  so the pool IPs are far less likely to re-tarpit. The burned IPs still need
REM  to REST first (stop the bot ~45 min before restarting). Revert: 3.0.
set TARGET_SWEEPS_PER_SEC=3.0
REM  2026-09-04 ~01:20 RedSky CAPTCHA WALL -- root cause + fix. A real Chrome on the
REM  HOME IP read RedSky 200 (probe) while the SAME browser on every Bright Data
REM  range got 403 + captchaRelativeURL (F5/Shape ATA): the BD ranges were flagged
REM  after two app.py instances ran at once. carts.target.com on BD stayed fine.
REM  So the stock sweep now exits the HOME IP (proxyIps 'proxies' = 2 entries =
REM  2 sweep Chromes, no --proxy-server) and purchases keep their own BD exits
REM  (primary back on 168.158.160.228 so the home IP is sweep-only). If the sweep
REM  ever sees the captcha 403 it backs off x2/x4/x8/x16 (cap ~5s) instead of
REM  hammering, and a single 200 restores full rate (no ProxyState 3h park).
REM  Revert: RESILIENT_HARVEST_VIA_LOCAL_IP=0 + restore 4 BD entries in proxyIps.
REM  2026-09-07 REVERTED: back on the Bright Data sweep pool -- the 16 pre-09-04
REM  IPs are restored in proxyIps after 3.9 days of rest (3.0/s across 16 =
REM  0.19/s per IP). The home-IP sweep (2 sessions) ran 09-04 01:47 -> 09-07
REM  22:30 and read ZERO RedSky 200s (11,050 straight failed ground-truth
REM  reads): the pool's cold profiles were captcha-walled on the home IP too,
REM  so the wall was never IP-only. The captcha is HUMAN Security's (PerimeterX)
REM  "Press & Hold" layer, which Target now runs on top of Shape -- all three
REM  account jars carry _px2/_px3/_pxhd/_pxvid/pxcts on .target.com. Verify the
REM  pool BEFORE a drop with probe_sweep_pool.bat (read-only, real Chrome + BD
REM  IP): want OK-DATA. Home-IP sweep again: =1 (+ 2 entries in proxyIps).
set RESILIENT_HARVEST_VIA_LOCAL_IP=0
set RESILIENT_CAPTCHA_BACKOFF=1
REM  2026-09-07 23:05 PROBE VERDICT (probe_sweep_pool.bat, same fresh profile, same
REM  machine): 31.105.228.245 / 31.105.93.225 / 168.158.143.27 -> CAPTCHA;
REM  72.56.171.184 -> OK-DATA twice. The wall is IP-RANGE reputation (HUMAN), not
REM  the device, and most of the pool is still walled. Three pool changes ship
REM  with that: (a) a captcha 403 now parks THAT SESSION for
REM  RESILIENT_CAPTCHA_PARK_S (x2 per repeat, cap x8) instead of slowing the whole
REM  sweep, so the clean IPs keep full cadence and walled ones retest on their
REM  own; (b) the sweep is capped at usable_sessions x RESILIENT_PER_IP_MAX_RPS,
REM  so a lone clean IP is read at 1/s (validated ceiling), never 3/s; (c)
REM  RESILIENT_POOL_FRESH_PROFILES=1 wipes the pool's scratch profiles at launch
REM  (the 09-04 profiles carry the HUMAN ids flagged that night). Every session
REM  walled -> the auto tab-fetch below takes over. grep "[STOCK][RATE]" and
REM  "[STOCK][CAPTCHA-PARK]" after boot. Revert: PARK_S=0 is NOT a kill (floor
REM  60s); pre-09-07 behaviour = RESILIENT_PER_IP_MAX_RPS=0 + FRESH_PROFILES=0.
set RESILIENT_CAPTCHA_PARK_S=1800
REM 2026-09-09 app-channel limiter (soaks through two walled exits): 0.5 reads/s
REM = 293/293 clean for 10 min; 2 reads/s = HTTP 404 "Not Found" on every read
REM after ~166 reads and still blocked >3 min later. Production is ~0.19/s per
REM exit at 3/s over 16; the cap keeps ANY exit under 0.5/s when few are usable
REM (sweep = min(target, usable x cap)). A 404 parks that exit only
REM (RESILIENT_RAW_404_PARK_S base, x2 per repeat, cap x8, reset on a 200). The
REM 404 ban measured on 168.158.143.27: still blocked 39 min after the burst at 1 read
REM per 5 min -> base 900 s (15/30/60/120 min ladder); production never nears the limit.
set RESILIENT_PER_IP_MAX_RPS=0.5
set RESILIENT_RAW_404_PARK_S=900
REM 2026-09-09 THE DETECTION FIX. RESILIENT_REDSKY_CHANNEL=apps_raw reads the
REM mobile-app RedSky aggregation (/v1/apps/tcin_product_list_v2) as RAW HTTP
REM through each sweep session's forwarder with the app header set, instead of
REM the in-page web fetch HUMAN captcha-walls on the Bright Data prefixes.
REM Probe 2026-09-09 19:3x through the walled exit 31.105.228.245: web = 403
REM captcha, apps_raw = HTTP 200 product data. Same parser shape. The pool
REM Chromes stay up (park/backoff logic is shared); grep "[STOCK STATS]" for
REM 200= climbing and "[STOCK][CAPTCHA-PARK]" staying quiet. Kill: =web.
set RESILIENT_REDSKY_CHANNEL=apps_raw
REM 2026-09-15: home-IP CANARY OFF. _canary_loop is raw urllib from the HOME IP
REM (primary's purchase exit) every 30 s with a mismatched TLS/UA -- the exact
REM bot-shaped hit HUMAN/Shape score -- and its 403-only self-retire never fires
REM now that Target answers HTTP 435 (run_20260914: ~1,700 rejections / 62 OKs,
REM zero signal; the cloak theory it guards was refuted 06-09). The app-channel
REM pool + ground-truth cache-bust (still ON) are the detectors. Re-enable: =1.
set STOCK_CANARY=0

REM 2026-09-09 audit of run_20260907: FRESH_PROFILES=1 did NOT help -- all 16 fresh
REM sessions were captcha-walled within 3.5 min of boot (~33 reads each), the pool
REM was blind 94% of 19 h, and the only recovery (4 sessions, 14:51-15:26, 98%
REM 200s) came with profiles just as fresh. Persistent profiles at least let the
REM HUMAN cookies age. Reads now go through the app channel anyway (below).
set RESILIENT_POOL_FRESH_PROFILES=0
REM  get_page budget for the trusted-browser tab-fetch (was a hard 3s: 30,012
REM  timeouts 09-04..09-07 on the loop shared with the sweep).
set RESILIENT_TAB_FETCH_TIMEOUT_S=8
REM  2026-09-04 ~01:40 RedSky captcha ROOT CAUSE isolated with 3 read-only probes:
REM  account profile on HOME IP -> 200; FRESH profile on home IP -> 403 captcha;
REM  fresh profile + injected trust cookies -> still 403. The device is F5-flagged so
REM  only a browser that EARNED a valid Akamai _abck (the logged-in purchase profiles)
REM  reads RedSky; cold pool profiles can't. And the trusted tab-fetch reader was
REM  SKIPPED whenever the resilient pool is active -> the bot was fully blind. This
REM  flag re-enables the tab-fetch (a live worker's account browser) IN resilient mode
REM  and polls every 4-8s. That is the detector tonight; the pool stays up (with the
REM  captcha backoff) and self-revives if the F5 flag decays. Revert: =0.
REM  2026-09-07: =auto -- the trusted-browser read is a FALLBACK only. It engages
REM  when the BD pool has produced no RedSky 200 for RESILIENT_TAB_FETCH_BLIND_S
REM  seconds (also covers a slow boot) and switches itself off on the pool's
REM  next 200. With a healthy pool it adds nothing but CDP contention on the
REM  primary account's tab (09-04..09-07: 30,012 x 3s get_page timeouts, 4,567
REM  empty reads). =1 forces it on (09-04 emergency behaviour), =0 never.
set RESILIENT_FORCE_TAB_FETCH=auto
set RESILIENT_TAB_FETCH_BLIND_S=180
REM 2026-09-09: a HUMAN captcha on the TRUSTED account browser parks that reader
REM (protects the purchase account). 0 = never park.
set RESILIENT_TRUSTED_READER_PARK_S=600
REM 2026-09-09: home-IP reader cadence. The trusted-browser reader (now the
REM PRIMARY detector since the BD sweep is HUMAN-walled) polled every 4-8s -
REM too slow: wins land 0.4-3.7s after a flip and hot SKUs sell <60s, so an 8s
REM poll misses the wave-first window (the only shot type that converts, 32.6%
REM vs 0.9% re-POSTs). One bulk fetch covers all ~19 TCINs, so ~1-2s catches the
REM flip in time and stays inside the vendor-safe 1000-4000ms monitor band.
REM Widen toward 4s if the home IP ever shows an interaction-burst flag.
REM 2026-09-09 CORRECTION: this reader had NEVER returned a verdict (its async
REM fetch was evaluated without await_promise -> "no result" on all 3,424 reads
REM 09-07/08 and 4,567 on 09-04..07). Now fixed. The home-IP account browser
REM tolerated ~200 reads/h (18 s cadence, 3,550 reads, no Press & Hold) on 09-07;
REM 1-2 s would be 10x that on the PRIMARY purchase account, so 3-5 s until the
REM home IP's HUMAN budget is measured. It only runs while the pool is blind.
set RESILIENT_READ_CADENCE_MIN_S=3.0
set RESILIENT_READ_CADENCE_MAX_S=5.0
REM  2026-09-07 HUMAN Security (PerimeterX) "Press & Hold" GUARD. Target runs
REM  HUMAN's sensor on the storefront in addition to Shape; when an account
REM  browser's trust score drops, the /account nav (sentinel rung 1) renders the
REM  Press & Hold interstitial. Before this guard the ladder read that page as a
REM  dead session and escalated: Chrome restart -> full sign-out -> scripted
REM  relogin (Shape-burned: "username did NOT advance" 0/12 on 09-06..09-07) ->
REM  jar left GUEST. That is how business + alt-1 died mid-run this week. Now
REM  the ladder takes a READ-ONLY DOM snapshot after a failed nav; a challenge
REM  page parks the heavy rungs for TARGET_PX_CHALLENGE_PARK_S, writes
REM  [PX-CHALLENGE] + [AUTH_CRITICAL] to logs\error_log.txt and leaves the
REM  widget on screen for a PERSON to press; the cheap token check clears the
REM  park on recovery. No automated solving. Kill-switch: TARGET_PX_CHALLENGE_GUARD=0
set TARGET_PX_CHALLENGE_GUARD=1
set TARGET_PX_CHALLENGE_PARK_S=300

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
REM  Purchase EXIT IPs are unaffected ? app.py still reads each account's BD IP
REM  from config/target_accounts.json (proxy_url). Trade-off: with the spoof off
REM  all accounts share the real device fingerprint + log in from the home IP,
REM  so per-account isolation now rests on profile + session + purchase-IP +
REM  card/address. Rollback (re-enable spoof): delete these three SET lines.
REM  2026-07-12: account_identity rebuilt COHERENT (real Chrome 150 + Windows), so
REM  per-account fingerprints are now SAFE and ON ? validated: alt-1/primary/business
REM  all logged in clean on the HOME IP with the coherent FP. Distinct coherent devices
REM  are the strongest un-linker for multi-accounting (F5 research: device canvas/WebGL
REM  fingerprint is the top cross-account linker, survives IP rotation + cache clear).
REM  LOGIN stays on the HOME IP (RELOGIN_SKIP_PROXY=1): BD-IP login is Shape-blocked
REM  ("password did NOT advance") regardless of fingerprint ? re-confirmed 2026-07-12.
REM  Purchase still exits each account's own BD IP. Rollback to shared-real-identity:
REM  set RELOGIN_SKIP_FINGERPRINT=1 and TARGET_APPLY_FINGERPRINT=0.
set RELOGIN_SKIP_FINGERPRINT=0
set RELOGIN_SKIP_PROXY=1
set TARGET_APPLY_FINGERPRINT=1
REM 2026-09-08: the JS fingerprint spoof (getImageData + navigator defineProperty
REM overrides injected via add_script_to_evaluate_on_new_document) is DEFAULT OFF
REM in code now - a detectable prototype-tamper tell (toString != native) that F5
REM Shape and HUMAN/PerimeterX both look for, and it never unlinked the accounts
REM (they collide on WebGL/canvas/audio regardless). The CDP UA/timezone/locale/
REM viewport overrides (which _px3's UA-signature and login rely on) stay ON via
REM TARGET_APPLY_FINGERPRINT=1 above. Restore the JS spoof (pre-09-08): =1.
set TARGET_SPOOF_JS=0
REM 2026-09-13 UA coherence (docs/FAILURES.md 2026-09-13): with APPLY_FINGERPRINT=1
REM  the purchase tab got a CDP User-Agent override carrying a FULL-version UA
REM  ("Chrome/152.0.7977.82" -- real Chrome 101+ only ever sends "152.0.0.0") and a
REM  brand list without the GREASE entry, while the harvest + warmup tabs (new tabs,
REM  never overridden) sent the real reduced UA + "Not?A_Brand";v="24" -- so the
REM  banked sensor was minted under one UA and replayed under another, and the
REM  pinned build had drifted again (.82 pinned, .84 installed). engine = no UA/brand
REM  override at all (tz/locale/viewport overrides stay); the identity's build now
REM  auto-tracks the installed Chrome (TARGET_UA_AUTODETECT=0 pins the list again).
REM  hand_login_all.bat sets the SAME mode so the jars mint under the same UA --
REM  keep the two bats in sync. Rollback: set TARGET_UA_MODE=legacy.
set TARGET_UA_MODE=engine
REM fp-chromium: engine-level distinct device per account on the PURCHASE side only (login stays real Chrome via RELOGIN_SKIP_PROXY). Master switch is set BELOW (09-03: OFF).
REM  2026-08-21 A/B (post-mortem in docs/FAILURES.md 08-21): every win in history
REM  (07-24/07-28/07-31/08-04) was on REAL Chrome + the real profile; every drop
REM  since fp-chromium shipped 08-11 went 0-for. Keep ONE identity on the proven
REM  real-Chrome config as the control while the other two stay on fp-chromium.
REM  business = control (the only 4-for-4 real-Chrome night, 08-04; it needs a
REM  hand-login anyway). Judge the night by per-identity ATC composition (grep
REM  "ident=" on the chain-done / ATC fetch lines), not by orders alone.
REM  Rollback to all-fp: set TARGET_FP_CHROMIUM_SKIP=   (empty). Full revert to the
REM  pre-08-11 winning config for ALL three: set TARGET_FP_CHROMIUM=0.
REM  2026-09-03 (pre-drop check for the 09-04 restock): A/B VERDICT after 3 nights
REM  (08-21/08-26/08-28) -- on the contested hot-SKU gate the fp-chromium pair
REM  passed Shape 0/318 shots while business (real Chrome) passed 9/146
REM  (Fisher p=2.5e-5; IP-range confounded, see FAILURES.md 08-28). Every order
REM  in history came from real Chrome; the fp engine is also ungoogled-Chromium
REM  148, four majors behind the real 152, and never reached even the demand
REM  throttle. So tonight ALL THREE run real Chrome + their real profiles (the
REM  07-24..08-04 winning config) and the IP axis is tested instead:
REM  primary = HOME IP (proxy_url emptied in config	arget_accounts.json; was
REM  168.158.160.228), business = 31.98.158.87, alt-1 = 168.158.32.64. Judge by
REM  per-identity P(401 | not edge-429) via grep "ident=" / [ATC_RESP].
REM  Rollback to the 08-21 A/B: set TARGET_FP_CHROMIUM=1 (SKIP line below still
REM  names business as the real-Chrome control) and restore primary's proxy_url.
set TARGET_FP_CHROMIUM=0
set TARGET_FP_CHROMIUM_SKIP=business
REM  Optional: rotate the fp-chromium device nightly by mixing a salt into the
REM  seed (e.g. set TARGET_FP_SEED_SALT=%DATE:~-4%%DATE:~4,2%%DATE:~7,2%). Left
REM  EMPTY on purpose so the A/B changes ONE variable (browser) per identity.
set TARGET_FP_SEED_SALT=

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
REM  not the attempt count. Pre-ATC 429s are provably pre-submit ? cannot double-buy.
REM  Wall A (checkout "busy" / RESERVATION_FAILURE) is handled by the new
REM  checkout_busy_retryable path; kill-switch: set TARGET_RETRY_CHECKOUT_BUSY=0.
set TARGET_RETRY_WHILE_IN_STOCK_MAX=40
REM  2026-08-26 adaptive edge-lottery cadence (08-26 post-mortem: 1011960739 restock
REM  02:51, ~60-75s window, 85 shots/0x2xx at ~4s cadence). Cross-log census verdict:
REM  lottery (empty-429) windows are 0-for-1,865 tickets ALL-TIME; every order ever came
REM  from a 401-dominated regular-SKU window, almost always shot #1 (bot already fires
REM  shot #1 in 0.8s). This knob packs ~50%% more tickets into an empty-429 window by
REM  tightening the inter-attempt sleep ONLY while the last attempt was an empty-body
REM  429 (gate_kind=edge); 401/DCO keep the proven 2.5/3.5s. Honest label: census EV of
REM  the extra tickets ~= 0 on hyped SKUs -- this is a doctrine bet (ticket count is the
REM  only lever there), not an evidenced converter. Research: vendor tested-safe band
REM  1000-4000ms, ~1s/IP ban floor (help.refractbot.com/modules/target); 2.0 stays >=2x
REM  the floor. Rollback (exact prior behaviour): set both EDGE knobs to 2.5 / 3.5.
set TARGET_ATC_EDGE429_RETRY_DELAY_MIN=2.0
set TARGET_ATC_EDGE429_RETRY_DELAY_MAX=3.0
REM  2026-08-26 Tgt-Cart-Error-Key capture on ATC responses (the 08-21 open item; CORS
REM  hides the header from page JS). Log-only [ATC_RESP] status= tgt-cart-error-key=
REM  line per ATC POST response via CDP response-stage interception -- next lottery
REM  window finally tells demand-throttle from identity-block. Continue-exactly-once
REM  per the 07-23 leak rule; capture never blocks or mutates the response.
REM  Kill-switch: set TARGET_ATC_RESPONSE_HEADER_CAPTURE=0
set TARGET_ATC_RESPONSE_HEADER_CAPTURE=1
REM ---------------------------------------------------------------------------
REM  ATC gate-wall circuit breaker (2026-08-09, run_20260806_234205.log audit).
REM  The 08-06->07 restock went 0-for-0: all 3 identities hit a Shape Device ID+
REM  wall at add-to-cart (~2,960 adds, 0 carts; 429 DCO_RATE_LIMITED hardening to
REM  401 _ERR_AUTH_DENIED as the night wore on). Device ID+ is hardware-anchored
REM  (docs/RETAILERS/target.md) so all 3 identities on this one machine share the
REM  score, and continuing to hammer ACCELERATES the block. This breaker counts
REM  CONSECUTIVE gate-denials per identity+TCIN and, past STREAK_LIMIT, arms the
REM  existing per-TCIN throttle so the retry loop stops storming the wall
REM  (~40 shots/window -> ~1 probe/COOLDOWN). FAIL-SAFE: any successful add resets
REM  it instantly, so a winning night never trips (07-31 7/7, 08-04 4/8 both had
REM  2xx). Costs nothing on a 0-for night. Validated: tests/test_atc_gate_breaker.py.
REM  Kill-switch: set TARGET_ATC_GATE_BREAKER=0. Tune: STREAK_LIMIT / COOLDOWN_S.
REM
REM  2026-08-18 LOOSENED (streak 8->20, cooldown 120->45s; user decision after
REM  the 3rd straight breaker-era hot-SKU 0-for). Rationale: every hot-SKU drop
REM  since the breaker shipped 08-09 (08-11/08-14/08-18) went 0-for, while every
REM  pre-breaker win did a full storm. The forensic reframe (see FAILURES.md
REM  08-18 + docs/RETAILERS/target.md "Hot-SKU ATC Wall"): the 85% dominant wall
REM  is a GLOBAL edge demand-lottery that hits humans too -- against it, SHOT
REM  COUNT is the only lever; the breaker was sacrificing tickets to protect the
REM  minority 15% hard-401 identity-hardening. Decisive datum: our ONLY 3
REM  non-first-shot wins (07-14/07-21/07-31) needed 21/21/24 consecutive ATC
REM  attempts before the 201 -- the streak-8 cutoff structurally PREVENTS that
REM  exact persistence pattern. And fp-chromium (08-11) split this desktop into
REM  3 distinct devices, so the "shared device score" the breaker guarded is
REM  largely obsolete. 20/45 still brakes a 2,960-add runaway (~3-4x more tickets,
REM  not unlimited). FAIL-SAFE unchanged: any 2xx add resets the streak instantly.
REM  Rollback to the protective config: STREAK_LIMIT=8, COOLDOWN_S=120.
REM
REM  2026-08-31 OFF (08-28 Phase-2 audit, adversarially verified). The breaker
REM  looped W05 into 45s-cooldown -> 20s-rearm -> throttle-bail -> 1 escaping
REM  401/identity/60s: cadence 48/min -> 3.2/min for 18 min of live stock,
REM  ~980 forgone shots across W05/W06/W07 (W07 opened pre-armed off W06's
REM  carried streak). Its premise is dead: 245 arms in 9 runs, 0 ever followed
REM  by a 2xx; the hot-SKU 401 re-forms in ~45-95s at ANY cadence (401 on shot
REM  #1 after 65 min idle) and resting never lowered it; the calm-TCIN
REM  heartbeat 401 rate was flat pre/post-arm. Nothing measurable is being
REM  protected; the only measured effect is the shot cut. Code default is now
REM  OFF too (purchase_executor.py). Re-enable for experiments: =1.
set TARGET_ATC_GATE_BREAKER=0
set TARGET_ATC_GATE_STREAK_LIMIT=20
set TARGET_ATC_GATE_COOLDOWN_S=45
REM  2026-08-21 post-mortem: the breaker armed at 03:14:39 on a streak that was
REM  53/62 EMPTY-body 429s (the edge demand lottery that hits humans too), cut
REM  the only 12.7-min window from ~27 shots/min to ~3, and every probe after
REM  03:19 was a 401 anyway. Winning nights were 63-76% hard-401 and converted
REM  INSIDE those walls (10/11 winners = shot #1 of a fresh race). So only
REM  carts-service denials (401 / DCO-body 429) count now; an empty-body 429 is
REM  neutral. The streak also decays after STREAK_TTL_S so a same-SKU restock
REM  hours later is not one-denial-then-armed. Rollback to 08-09..08-20 counting:
REM  set TARGET_ATC_GATE_COUNT_EDGE_429=1 ; no decay: TARGET_ATC_GATE_STREAK_TTL_S=0
set TARGET_ATC_GATE_COUNT_EDGE_429=0
set TARGET_ATC_GATE_STREAK_TTL_S=600

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
REM  2026-09-11 (09-11 drop 0-for): the only ATC 201 of the drop was held once,
REM  re-shot once, hit FAST_SELLING again and was CLEARED while the item stayed in
REM  stock 11+ min. Re-hold the WON cart up to N cycles inside a wall-clock budget
REM  (must stay under the manager 150 s future). Kill-switch (exact prior, one hold):
REM  set TARGET_FAST_SELLING_HOLD_CYCLES=1
REM  WON-CART RIDE (2026-09-11): with TARGET_WON_CART_RIDE=1 the manager extends its 150 s
REM  future wait (up to RIDE_MAX_S) ONLY while the executor is holding a live won cart, and
REM  the 200 s force-complete respects it, so the hold can ride ~6 x 45 s. "Keep submitting
REM  and let it ride." Kill-switch: set TARGET_WON_CART_RIDE=0 (holds then clamp to 80 s).
set TARGET_WON_CART_RIDE=1
set TARGET_WON_CART_RIDE_MAX_S=300
set TARGET_FAST_SELLING_HOLD_CYCLES=6
set TARGET_FAST_SELLING_HOLD_TOTAL_S=270

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

REM ---------------------------------------------------------------------------
REM  CDP WEDGE mitigations (2026-09-10). The run_20260910 soak (18 h) settled the
REM  open wedge question: the [WEDGE-PROBE] answered in 0-30 ms on 55/59 events
REM  (Chrome's HTTP thread ALIVE while CDP dispatch is DEAD = a browser-side CDP
REM  stall), and it hit ONLY the two PROXIED account Chromes (business/alt-1) on a
REM  ~49-84 min clock (median 69) -- the HOME-IP primary ran the whole 18 h on ONE
REM  launch, clean. The occlusion flag (TARGET_ACCOUNT_OCCLUSION_FIX) WAS applied
REM  and the wedge still fired => that theory is FALSIFIED; the wedge is
REM  proxy-path correlated. Root cause still open, but two flag-gated mitigations
REM  keep the 2 AM drop safe (a Chrome launched ~1 AM is ~60 min old at 2 AM =
REM  squarely in the wedge window):
REM   1) PROACTIVE pre-wedge relaunch: the sentinel relaunches a PROXIED Chrome
REM      once it crosses TARGET_CHROME_MAX_AGE_S (2100 s = 35 min, under
REM      the 49-min earliest onset). A scheduled ~5 s blip at a safe moment beats
REM      a random 70-135 s outage landing on the drop. Never touches the home-IP
REM      primary (proxy_url=None). Runs inside the sentinel drop-guard + mid-
REM      purchase skip, so it can't fire under a shot. Kill: TARGET_CHROME_MAX_AGE_S=0.
REM   2) FAST reactive restart: when the wedge-probe CONFIRMS a genuine CDP stall,
REM      the cookie watchdog restarts on strike 1 instead of waiting out the
REM      3-strike / ~2 extra 60 s cycles. The confirmed-wedge signal already
REM      required evaluate('true') to fail at 2 s AND on the slow re-probe, so it
REM      never fires on a healthy or backpressured tab. Kill: TARGET_WEDGE_FAST_RESTART=0.
REM  Validated offline: tests/test_wedge_recovery_smoke.py + session_sentinel_smoke.
REM  2100 s = 35 min: with the 300 s sentinel tick the relaunch lands by ~40 min,
REM  a ~9 min margin under the 49-min earliest observed wedge onset.
set TARGET_CHROME_MAX_AGE_S=2100
set TARGET_WEDGE_FAST_RESTART=1

REM ---------------------------------------------------------------------------
REM  Background-refill-on-warmup-miss (2026-09-10). run_20260910 left alt-1 with
REM  NO harvester and NO Shape-header refill for the ENTIRE 18 h run: its boot
REM  warmup returned False ("No headers available" -- its BD exit refused the
REM  first dummy POST), so _start_background_refill was never called, and the
REM  resilient path has no re-warm loop to rescue it. alt-1 would enter the drop
REM  with cold cart Shape headers (slow button-click ATC / miss). warm_shape_headers
REM  now starts the SELF-HEALING refill loop (which retries the warmup every
REM  60-90 s AND spawns the real-click harvester) even when the first warmup missed,
REM  as long as the session is alive. Kill: TARGET_REFILL_ON_WARM_MISS=0.
set TARGET_REFILL_ON_WARM_MISS=1

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
REM     2026-08-31: 20 -> 3. The 08-28 audit measured 46 in-window wave
REM     boundaries at median 11.6s lag (the 20s poll quantization), ~130-160
REM     forgone shots/night, and the breaker-era claim of 2,228s inter-wave
REM     dead air was a double-counting artifact (real: ~154s). At 3s the
REM     boundary drops to ~1.5-6.5s; the poll reads in-memory state so the
REM     cost is nil, and the publish->stock-aware-reset->dedup path is
REM     unchanged. Wave-first pauses now come from TARGET_401_PULSE, not from
REM     slow boundaries. Rollback: =20.
set TARGET_LEVEL_REARM_S=3
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
REM  6) Organic ATC fallback: DISABLED 2026-08-07 -- premise FALSIFIED on its first
REM     live test. The 08-04 theory was "Shape locks the REPLAYED-header dispatch
REM     per identity; page-signed fetches stay exempt." The 08-06->07 restock went
REM     0-for-3-accounts (0 carts on ~2,960 ATC), and this fallback fired 1,468x
REM     returning 401/429 EVERY time (zero 2xx) -- the organic page-signed add is
REM     NOT exempt. The warmup dummy POST's 424 only proves the token is valid: it
REM     uses a bogus tcin (81926151), so it never tests a real contested add. The
REM     real gate is per-identity anti-bot/demand on the hot SKU (429 early,
REM     hardening to 401 _ERR_AUTH_DENIED late), hitting injected AND organic adds
REM     alike; the fallback only added failed-add load on the contested SKU.
REM     Re-enable (=1) only if a future test shows organic adds actually pass.
set TARGET_ATC_NATIVE_FALLBACK=0
REM  8) 2026-08-21: ATC-401 repair ladder OFF. On a hot-SKU 401 the ladder fired
REM     2 more ATC writes + a token_refresh mint + an /account page load and
REM     blocked the identity 2.6-9.3s per 401 (08-21: 0/85 mints and 0/103
REM     in-ladder retries converted; the same token READ the cart fine after
REM     every 401). Bail straight to the Error-Delay re-shoot instead (cart-hold
REM     read still runs). Restore the ladder: set TARGET_ATC_401_LADDER=1
set TARGET_ATC_401_LADDER=0
REM     8b) 2026-08-22 safety net for 8): with the ladder off, a GENUINELY dead
REM     write token (07-07 mode) had no in-window repair. The warmup heartbeat's
REM     confirmed-dead repair (dummy POST 401 -> re-probe 401 -> token re-mint on
REM     the SEPARATE warmup tab) now runs during a window too, 300s-throttled and
REM     CONFIRMED-dead-only, so it is inert on a normal Shape-burn 401 (heartbeat
REM     424). Kill-switch: set TARGET_ATC_DEAD_TOKEN_MIDWINDOW_REPAIR=0
set TARGET_ATC_DEAD_TOKEN_MIDWINDOW_REPAIR=1
REM  9) 2026-08-21: the inter-retry re-warm no longer FORCES a /cart reload: with
REM     force_fresh=True it bypassed the purchase-time /cart guard and fired a
REM     /cart page load + a cart PUT (ADDRESSES = the CVV re-entry trigger) +
REM     a dummy POST between EVERY retry (03:13: 26 navs / 27 PUTs for 30 real
REM     shots). The guard now applies; the dummy POST still refills the Shape
REM     ring. Restore the forced reload: set TARGET_RETRY_FORCE_REWARM=1
set TARGET_RETRY_FORCE_REWARM=0
REM ---------------------------------------------------------------------------
REM  2026-08-31: 08-28 Fri 0-for Phase-2 fix stack (14 windows / 2,680 shots /
REM  0x2xx; multi-agent audit + adversarial verification; FAILURES.md 08-28).
REM  Verified night shape: 84%% empty-429 (global per-TCIN edge limiter) and
REM  100%% of limiter-passing shots died 401 (TCIN-scoped Shape score) -- the
REM  night was unwinnable at our pass rate, but the bot ALSO forfeited ~980
REM  shots to the breaker (now off above) and ~1.0-1.15s per 3.3s retry cycle
REM  to self-inflicted per-retry work. Winning-night forensics (07-31/08-04):
REM  wave-first shots (first shot per identity on a TCIN after a >=15s pause)
REM  converted 32.6%% vs 0.9%% for later re-POSTs -- every order ever was shot
REM  #1-3 of a fresh wave.
REM  10) Retry-path dummy-POST warm OFF: each retry awaited a warmup-tab dummy
REM      cart_items POST (+0.3-0.56s clean, +1.3-2.0s on its ~20%% 401s via the
REM      0.6s/probe re-probe, 17/17 FALSE "dead token" repairs at 4-13s each on
REM      08-28). The ring refills from every real shot's own capture (never <4
REM      unused); injected headers are re-signed in-page anyway (07-10). ~+40%%
REM      tickets/identity and ~25%% less cookie burn (Refract: more cookies =
REM      more flags). Forced re-warm + background refill + sentinel unchanged
REM      (07-07 dead-token net stays). Rollback: set TARGET_RETRY_WARM=1
set TARGET_RETRY_WARM=0
REM  11) Cart-hold read skipped on EMPTY-body 429s only: edge-dropped before
REM      the carts app, cannot silently land (1,526 GETs / 0 hits / 21 runs;
REM      0.19s median each). DCO-body 429s + 401s keep the hold read (silent
REM      lands documented there). Rollback: set TARGET_CART_HOLD_SKIP_EDGE=0
set TARGET_CART_HOLD_SKIP_EDGE=1
REM  12) Shot-#1 proactive Shape-TTL refresh OFF: all 4 firings on 08-28 taxed
REM      a wave's shot #1 (0.2-2.05s, one opened a lazy warmup tab mid-race) --
REM      the exact shots that win. Headers are inert (07-10); zero-header sends
REM      pass the same. Rollback: set TARGET_SHOT_TTL_REFRESH=1
set TARGET_SHOT_TTL_REFRESH=0
REM  13) ATC-level DCO/FAST_SELLING 429 now uses the edge cadence (it is the
REM      same demand-throttle class; auth provably passed). Checkout-level FS
REM      cooldown untouched. Rollback: set TARGET_ATC_DCO_AS_EDGE_CADENCE=0
set TARGET_ATC_DCO_AS_EDGE_CADENCE=1
REM  14) WAVE-FIRST 401 PULSE (the doctrine bet of this stack): after 3
REM      consecutive carts-401s an identity pauses 15-25s IN PLACE OF one
REM      2.5-3.5s sleep, so its next shot re-enters as wave-first (32.6%% vs
REM      0.9%% conversion on 07-31; the 401 is velocity-flat so hammering into
REM      it is ~1%% EV). Edge-429s neither count nor reset the streak -- an
REM      edge-lottery window never accumulates 3 and keeps FULL ticket cadence
REM      (ticket doctrine unchanged there). Rollback: set TARGET_401_PULSE=0
REM      2026-09-17: TARGET_401_PULSE is INERT under TARGET_WAVE_FIRST_ONLY=1
REM      (below): a 401 takes the wave-first cold re-entry first, which also
REM      resets the streak. Superseded by TARGET_IDENTITY_REST (per identity,
REM      across races; enforcement not built yet). Kept armed and pinned.
set TARGET_401_PULSE=1
set TARGET_401_PULSE_STREAK=3
set TARGET_401_PULSE_SLEEP_MIN=15
set TARGET_401_PULSE_SLEEP_MAX=25
REM 2026-09-09 CENSUS over every run log since June (21,684 ATC POSTs, 1,609
REM windows, 20 orders): 19/20 orders landed on attempt #1 at t+0 and the 20th was
REM a checkout re-shoot on a cart the first shots had already won; ZERO orders ever
REM came from a re-POSTed ATC (0/13,244 at attempt>=2). Cart rate by attempt:
REM 6.1%% -> 1.4%% -> 0.4%% -> 0.0%% (k=1,2,3-5,6-10). A fresh shot fired after >=5 prior
REM shots on the TCIN within 120 s went 0-for-438 vs 10%% cold. On hyped SKUs the
REM 429 share climbed 30%% -> 90%% by shot 3-5 (ERR_A2C_TCIN_RATE_LIMITED = our own
REM volume trips Target's per-TCIN limiter). Spam is a COST, not a lever. So:
REM WAVE_FIRST_ONLY: after an ATC-level 401/429 no re-POST -- a cold re-entry after
REM 55-70 s (or end the window; the level re-arm opens a fresh one), and
REM SHOT_BANK_GATE waits up to 8 s for a fresh real-click set before that cold
REM shot (a page-signed cold shot still fires -- that is what won 19/20).
REM Checkout-leg re-shoots (cart hold) are untouched. Kill: TARGET_WAVE_FIRST_ONLY=0.
set TARGET_WAVE_FIRST_ONLY=1
REM WAVE_FIRST_EDGE=1 applies the cold re-entry to the 429 lottery as well (the
REM census: 2,233/2,236 ATC 429s = ERR_A2C_TCIN_RATE_LIMITED, share climbing with our
REM own re-POSTs, 0 orders from 5,292 deep tickets). =0 restores the 2-3 s ticket
REM cadence for empty-429s only. With WAVE_FIRST_ONLY=1 the 401-PULSE above is
REM superseded (a 401 now always takes the longer cold re-entry).
set TARGET_WAVE_FIRST_EDGE=1
set TARGET_WAVE_REENTRY_MIN_S=55
set TARGET_WAVE_REENTRY_MAX_S=70
set TARGET_SHOT_BANK_GATE=1
set TARGET_SHOT_BANK_WAIT_S=8
REM  15) Real PDP referrer on the fast-lane ATC via fetch's `referrer` INIT
REM      option (the headers-object Referer is a forbidden name and never hit
REM      the wire -- shots actually carried the parked homepage//account URL; a
REM      real add carries the full PDP URL). Rollback: set TARGET_ATC_REFERRER_PDP=0
set TARGET_ATC_REFERRER_PDP=1
REM  16) 2026-09-03 REAL-CLICK SHAPE HARVEST + BANKED REPLAY -- the hot-SKU gap.
REM      Every hot-SKU window since 08-07 died 100%% 401 _ERR_AUTH_DENIED on the
REM      shots that passed the edge limiter. Vendor research (Refract/Stellar/
REM      Hidden AIO/Shikari docs, memory reference_target_winning_bot_architecture_2026):
REM      winners never sign a programmatic request -- a HARVESTER drives the REAL
REM      add-to-cart click on an in-stock product, captures the Shape header set
REM      that genuine interaction minted, banks ~3 per task (TTL 5-15 min) and
REM      replays one per shot. Our byte-audit: the main-tab ATC has carried the
REM      -a0 behavioral chunk 0/53,950 times; we never once fired a real page
REM      click. Now (src/session/shape_harvest.py): a harvest tab per identity
REM      parks on a cheap in-stock PDP (TCINS below), human-trajectory CDP-clicks
REM      the real button, the interceptor banks the page-signed X-headers and
REM      FAILS the request before it leaves Chrome (nothing lands, uuid unspent);
REM      the main-tab fast-lane ATC is paused at the request stage and
REM      Fetch.continueRequest swaps in the freshest banked set (after the in-page
REM      hook, so unlike fetch() header injection -- inert since 07-10 -- it hits
REM      the wire). Boot SELFTEST replays banked sets on the warmup dummy POST:
REM      424 = accepted, 3/3 x 401/403 = replay auto-OFF for the run (shots stay
REM      page-signed = the proven path). Bank empty mid-window = page-signed shot.
REM      grep "[HARVEST/" for CAPTURED / REAL_ATC_SHAPE / SELFTEST verdict / REPLAY
REM      on main. TCINS = staples (Tide Pods 42ct, Tide Free 42ct, Bounty 6 triple)
REM      -- swap for any cheap in-stock ship-eligible item; the loop rotates on a
REM      missing/disabled button and says so. Kill-switch: TARGET_SHAPE_HARVEST=0
REM      (exact prior behaviour); replay-only kill: TARGET_HARVEST_REPLAY=0.
set TARGET_SHAPE_HARVEST=1
set TARGET_HARVEST_TCINS=21516452,50225561,53274278
set TARGET_HARVEST_BANK=3
set TARGET_HARVEST_TTL_S=300
set TARGET_HARVEST_INTERVAL_S=40
set TARGET_HARVEST_REPLAY=1
set TARGET_HARVEST_SELFTEST=1
set TARGET_HARVEST_IN_WINDOW=1
REM  2026-09-09: harvest on ALL accounts. The 09-04 "primary fails under sweep
REM  load" premise was the hidden-tab bug (run_20260904_001027: warmup tab opened
REM  00:10:59, first click failure 00:11:05, every click after), fixed today by
REM  the visibility guard. Each account needs its own bank (sets are IP-bound and
REM  single-use). Re-skip an account: set TARGET_HARVEST_SKIP=primary
set TARGET_HARVEST_SKIP=
REM 2026-09-09: the REAL 09-07 harvester root cause (supersedes the 09-08 note).
REM The 09-07 click timeouts (4,613 real; the log carries every line twice) were
REM NOT socket contention -- zendriver gives every tab its own CDP socket. The
REM harvest tab was a BACKGROUND tab: every Target.createTarget in desktop Chrome
REM opens a new FOREGROUND tab, so each warmup-tab re-open after the ~75-min
REM sentinel Chrome restart pushed the harvest tab behind it (first click failure
REM 5-45 s later in 11/11 such lives; every capture came before). A hidden tab
REM paints no frames and Chromium releases queued mouseMoved input only via its
REM 5 s rAF fallback timer, so no click budget or move cap could ever win. Fix:
REM probe visibility and re-activate the harvest tab before each click (never
REM over a PARKED account -- a person may be solving), abort a click when one move
REM stalls (>2500 ms = the 5 s fallback fingerprint), close dropped tabs, log
REM per-move timing. grep "[HARVEST/" for "harvest tab is HIDDEN", "re-activated",
REM "click ABORTED", "max_move_ms=". The open budget below only matters past
REM zendriver's own 10 s wait; every 09-07 open failure sat inside a wedged-Chrome
REM phase. Kill: TARGET_HARVEST_VIS_GUARD=0 / TARGET_HARVEST_MOVE_ABORT_MS=0.
REM Never minimize the account Chrome windows while the harvester runs.
set TARGET_HARVEST_TAB_OPEN_TIMEOUT_S=45
set TARGET_HARVEST_CLICK_TIMEOUT_S=12
set TARGET_HARVEST_VIS_GUARD=1
set TARGET_HARVEST_MOVE_ABORT_MS=2500
REM 2026-09-09 audit fixes (docs/FAILURES.md 2026-09-09 detection+shot entry):
REM  - MAX_REPLAY_AGE pinned next to TTL: sets past it are now DISCARDED so the
REM    bank refills (a full-but-stale bank used to freeze the harvester ~2/3 of
REM    every 300 s cycle); the loop also re-clicks past half the cap.
REM  - PREFER_SHIPPING selects the Shipping fulfillment cell once per PDP nav
REM    (every 09-07 capture was a STORE_PICKUP add).
REM  - ATC_BYTEMATCH sends the page's own ATC URL (%%2C + key=) on the fast lane.
set TARGET_HARVEST_MAX_REPLAY_AGE_S=100
set TARGET_HARVEST_PREFER_SHIPPING=1
set TARGET_ATC_BYTEMATCH=1
REM 2026-09-13 (docs/FAILURES.md 2026-09-13, run_20260911 per-shot forensics):
REM  EVERY win in history rode a SMALL first-click Shape set (no -a0 chunk). The
REM  harvest tab clicked the same PDP document for ~15 min between reloads, so the
REM  sensor overflowed its 7,900-char -a into -a0 with ~13 min of synthetic
REM  click/move telemetry; those a0=yes sets went 0/14 on limiter-passing shots
REM  (12x401 on the two BD accounts, 431+503 on home-IP primary) and the night's
REM  ONLY 201 (05:19:54) rode a set captured 2 s after a PDP reload (a0=no). The
REM  -a0 bytes are also what pushed 380 cart_items POSTs over the edge's 431
REM  header cap. FRESH_PAGE reloads the PDP before every harvest click so each
REM  banked set is a first-click set; _LIVE=1 allows those reloads during a live
REM  window (wave re-entries need fresh sets too; =0 restores the no-nav-mid-
REM  purchase rule if [WEDGE-PROBE]/TAB_HEALTH lines cluster around reloads);
REM  MIN_GAP bounds the PDP load rate on the exit; PREFER_NO_A0 makes the shot
REM  take the freshest replayable a0=no set over a fresher bloated one. Every
REM  CAPTURED / REPLAY / 431 line now carries hdr_bytes= (cookie / shape / a0)
REM  so 09-16 tells us the edge cap. grep: "fresh page: reloading", "a0_len=",
REM  "req_bytes=". Kill: TARGET_HARVEST_FRESH_PAGE=0 (exact prior behaviour).
set TARGET_HARVEST_FRESH_PAGE=1
set TARGET_HARVEST_FRESH_PAGE_LIVE=1
set TARGET_HARVEST_FRESH_PAGE_MIN_GAP_S=15
set TARGET_HARVEST_PREFER_NO_A0=1
REM ===========================================================================
REM 2026-09-17 HOT-SKU FIX ARMING (the 09-16 0-for). Why and what, in plain
REM words: docs\HOT_SKU_FIX_2026_09_16.md. Plan with evidence:
REM logs\analysis_2026_09_16\wf2\plan_final.md. Every flag below defaults to the
REM prior behaviour in code, so each line's kill-switch is the value named in
REM its note (or deleting the line).
REM ---------------------------------------------------------------------------
REM AC-1 ambiguous-commit latch: a place-order POST that got NO answer (or a
REM manager execution_timeout) latches that identity off that TCIN for 30 min,
REM so no retry or level re-arm can place a second order on a possibly-placed
REM one. grep [AMBIGUOUS_COMMIT] (expect 0) and CHECK ORDER HISTORY if it shows.
REM Kill: TARGET_AMBIGUOUS_COMMIT_LATCH=0 (the won-cart loop below then refuses
REM to arm and stays inert).
set TARGET_AMBIGUOUS_COMMIT_LATCH=1
set TARGET_AMBIGUOUS_COMMIT_LATCH_S=1800
REM INF-2: a race state without started_at is stamped, never force-completed
REM with the Unix epoch as its age. Kill: TARGET_RACE_STATE_STARTED_AT_GUARD=0.
set TARGET_RACE_STATE_STARTED_AT_GUARD=1
REM WC-1 won-cart direct checkout loop: when an ATC 2xx cart hits
REM FAST_SELLING at checkout, fire checkout tickets straight away (first at
REM +5 s, two probe gaps 5 and 15 s once per cart, then one place-order-only
REM ticket every 45 s while RedSky reads in stock) instead of the ~26 s
REM nav/DOM detour and 45 s holds. 45 s place-order-only is the shape of the
REM only through-FAST_SELLING win ever (08-04). STOCK_PROBE/HYST = RedSky
REM freshness; MAX_TICKETS per cart; CALL_MAX caps one loop call (it holds
REM the fleet); YIELD_FLEET ends it early when another armed TCIN is live.
REM grep [WON_CART_DIRECT] and [FS_TICKET].
REM Kill: TARGET_WONCART_DIRECT=0 (exact legacy path). Probes off (pure 45 s):
REM TARGET_WONCART_SCHEDULE_S=0 -- an empty set X= UNSETS the variable, which
REM restores the 5,15 default.
set TARGET_STOCK_PROBE=1
set TARGET_STOCK_HYST_S=20
set TARGET_WONCART_DIRECT=1
set TARGET_WONCART_SCHEDULE_S=5,15
set TARGET_WONCART_STEADY_GAP_S=45
set TARGET_WONCART_OOS_TAIL_TICKETS=1
set TARGET_WONCART_MAX_TICKETS=14
set TARGET_WONCART_CALL_MAX_S=120
set TARGET_WONCART_HEADROOM_S=45
set TARGET_WONCART_YIELD_FLEET=1
REM WC-3 held-cart re-entry: a cart the loop still holds is re-entered with
REM no new ATC when its TCIN reads live again (up to 15 min, 14 tickets per
REM cart); a one-shot boot cart audit keeps or clears a leftover cart.
REM grep [HELD_CART] and [BOOT_CART_AUDIT]. Kill: TARGET_HELD_CART_REENTRY=0.
set TARGET_HELD_CART_REENTRY=1
set TARGET_HELD_CART_TTL_S=900
REM WC-2 hygiene on a held cart: no forced /cart re-warm before a legacy
REM re-shoot, no warmup /cart nav while a cart is held, no fleet cycle-warm
REM while stock is live, no duplicate pre_checkout, and a ride that ends as
REM won_cart_ride_timeout instead of a false purchase_impl_hang browser
REM restart. Kill: TARGET_RESHOOT_FORCE_REWARM=1, the other four =0.
set TARGET_RESHOOT_FORCE_REWARM=0
set TARGET_HOLD_QUIET_WARMUP=1
set TARGET_WARMUP_CYCLE_SKIP_ON_STOCK=1
set TARGET_FASTLANE_SKIP_DUP_PRE=1
set TARGET_WON_CART_RIDE_CLEAN_EXIT=1
REM FL-1: on a 12 s fast-lane evaluate timeout, one 2 s read aborts the page
REM chain unless the place-order already started, so a stuck ATC or
REM pre_checkout is retried instead of being treated as a possible
REM double-buy. Armed because the node abort tests pass (stage S5).
REM Kill: TARGET_FASTLANE_STAGE_TRACK=0.
set TARGET_FASTLANE_STAGE_TRACK=1
REM DX-1 log-only diagnostics for the next audit: [EXPOSURE] per shot,
REM [IDENT_CENSUS] per race, atc_t0= atc_rt= arrival stamps, envoy_ms=,
REM [FS_TICKET], cart_qty=, RedSky pickup fields ([STOCK WATCH] pickup= and
REM [STOCK PICKUP]). Kill: each =0.
set TARGET_EXPOSURE_LOG=1
set TARGET_FASTLANE_T_STAMPS=1
set TARGET_FS_TICKET_LOG=1
set TARGET_IDENT_CENSUS=1
set TARGET_FASTLANE_LOG_CART_QTY=1
set TARGET_REDSKY_STORE_OPTIONS_LOG=1
REM HV-1 harvest: a SKIPped account also turns replay off; a buttonless
REM harvest load is probed once per nav (MISS-PROBE, at most 10 screenshots a
REM run) and a PerimeterX page parks that harvester 300 s (no nav, no click);
REM the bank gate stops waiting while the harvest is stuck; a relaunch
REM flushes the bank. Live re-nav on a miss stays OFF until probe data exists.
REM grep MISS-PROBE, [PX-CHALLENGE/harvest], [BANK_GATE] skipped.
REM Kill: each =0.
set TARGET_HARVEST_SKIP_DISABLES_REPLAY=1
set TARGET_HARVEST_MISS_PROBE=1
set TARGET_HARVEST_MISS_SHOTS_MAX=10
set TARGET_HARVEST_PX_PARK_S=300
set TARGET_HARVEST_MISS_RENAV_LIVE=0
set TARGET_BANK_GATE_ADAPTIVE=1
set TARGET_HARVEST_FLUSH_ON_RELAUNCH=1
REM INF-1: print the sentinel ticks skipped while a purchase is in flight.
REM Kill: TARGET_SENTINEL_LOG_SKIPS=0.
set TARGET_SENTINEL_LOG_SKIPS=1
REM ---------------------------------------------------------------------------
REM U1 = (c), chosen 2026-09-17: PARK alt-1 on the hot TCINs. On 09-16 alt-1
REM (BD 168.158.x) drew 61/181 carts-401s and 0/120 limiter passes there, and
REM its shots add our own volume to the per-TCIN limiter that primary (the
REM only hot-SKU converter) must pass. alt-1 keeps racing every other SKU.
REM Boot line [PARK]; per race: sits out ... account_parked_hot.
REM Format acct:tcin,tcin;acct2:tcin -- keep the quotes, never use pipes. Add
REM each new hot TCIN you arm here. Kill: delete the line (nobody parked).
set "TARGET_PARK_ACCOUNT_TCINS=alt-1:1010892078,1010892076,1010892069,1010892067,1010892068,1010892065,1012422107,1011407490,1010892075,1010892071,1012055696,1011960739,1011209279"
REM business relaunches at 1800 s instead of 2100 s, so the two BD Chromes
REM stop relaunching in the same second. Kill: delete the line.
set TARGET_CHROME_MAX_AGE_OFFSETS=business:-300
REM Identity-rest enforcement (ID-1) stays off under (c).
set TARGET_IDENTITY_REST=0
REM ---------------------------------------------------------------------------
REM U1 = (a), alt-1 on the HOME IP: NOT READY. Its in-drop guard HS-1
REM (TARGET_HOME_SHARE_GUARD) and volume cap BG-1 (TARGET_BG_SLOW_ACCOUNTS,
REM TARGET_BG_SLOW_FACTOR) are NOT BUILT yet (stage S6 did not land), so those
REM lines would do nothing today. After that code lands, and only once you
REM have confirmed alt-1 and primary use a different address, card and phone:
REM  1. stop the bot;
REM  2. in config\target_accounts.json set alt-1 proxy_url to an empty string
REM     and timezone to America/Chicago (commit the config first);
REM  3. run hand_login_all.bat AFTER the edit (the tz change is a new device
REM     seed), then check_session_readiness.py (3/3 MEMBER) and
REM     preflight_fp_drop.py (its shared-IP WARN is expected);
REM  4. in this file delete the TARGET_PARK_ACCOUNT_TCINS and
REM     TARGET_CHROME_MAX_AGE_OFFSETS lines and add
REM       set TARGET_HOME_SHARE_GUARD=1
REM       set TARGET_BG_SLOW_ACCOUNTS=alt-1
REM       set TARGET_BG_SLOW_FACTOR=2
REM  5. launch once. Revert: restore proxy_url and timezone, hand-login again.
REM U1 = (b), keep alt-1 on BD and run the rest experiment, needs ID-1
REM enforcement (TARGET_IDENTITY_REST=1), also not built yet.
REM ---------------------------------------------------------------------------
REM U2 per-TCIN qty pin (NOT armed; hot SKUs stay qty 2): commit
REM config\product_config.json, add a qty of 1 to the hot entries, then add
REM set TARGET_QTY_PER_TCIN=1 here. A pin of 1 holds only while
REM TARGET_PDP_QTY_LOOKUP stays off (the default). Zero-code alternative:
REM set TARGET_QTY_OPTIMISTIC=0.
REM ===========================================================================
REM  7) Non-destructive relogin: the 20:24 sentinel escalation signed primary
REM     OUT before the Shape-burned login failed, leaving a GUEST token for the
REM     next drop. Now the jar is snapshotted pre-signout and restored when the
REM     login fails. Kill: TARGET_RELOGIN_RESTORE_ON_FAIL=0
set TARGET_RELOGIN_RESTORE_ON_FAIL=1

REM ---------------------------------------------------------------------------
REM  Token keep-fresh ON (2026-07-12, CORRECTED). The F5 research overturned the
REM  earlier "harvesting causes a Shape block" theory: our inline fetch() re-signs
REM  Shape per request, so we PASS Shape (424, never a 403 block) ? the ATC 401 is
REM  the WRITE-AUTH/member-token layer, not Shape. Token freshness is THE #1 lever.
REM  Keep-fresh keeps a MEMBER token hot 24/7 (the sentinel's rung-0 member-token
REM  check every ~5 min), so the bot is drop-ready no matter WHEN it is started
REM  (fixes the "started hours before the drop -> 4h token expired -> 401" gap).
REM  It is member-aware: if it can only mint a GUEST token (degraded login-session)
REM  it escalates the ladder to a credential relogin. The 07-10 guest-churn was a
REM  concurrent live session (personal Chrome logged into an account) evicting the
REM  token faster than the relogin cap ? OPERATIONAL fix: sign out / close any
REM  personal-browser Target tabs before the drop. Kill-switch: set to 0.
set TARGET_TOKEN_KEEPFRESH=1

if not exist "%PYTHON%" (
    echo [ERROR] venv python not found ? checked .venv\Scripts and venv\Scripts
    echo Create the venv first, then re-run. Closing in ~30s.
    REM Bounded wait via ping (not pause/timeout): pause hangs forever on an
    REM unattended boot, and timeout returns instantly without a real console.
    ping -n 31 127.0.0.1 >nul
    exit /b 1
)

REM ---------------------------------------------------------------------------
REM  Personal-Chrome Target sign-out (2026-07-13). The operator's own Target
REM  login IS the bot's `primary` account, so a normal browsing session signed
REM  into target.com is a SECOND live session on that account ? Target rotates
REM  the member token out from under the bot's session (the 07-10 guest-churn:
REM  every account holding a GUEST token at fire time). Confirmed 07-13: the
REM  personal Chrome held 48 target.com cookies incl. a live refreshToken.
REM  "Remember to sign out before the drop" failed three sessions running, so
REM  the bot heals it: close Chrome, delete ONLY target.com cookies, reopen
REM  Chrome with the tabs restored. Runs BEFORE the account logins so the bot's
REM  sessions end up the only live ones. Kill-switch: set CHROME_SIGNOUT_SKIP=1.
REM  Never fatal ? a stale personal session degrades a drop, it can't break the bot.
REM ---------------------------------------------------------------------------
REM ---------------------------------------------------------------------------
REM  2026-09-08 SINGLE-INSTANCE guard. Two app.py bots at once is the 09-04
REM  disaster: ~6 req/s from two instances poisoned the Bright Data pool's
REM  HUMAN/PerimeterX standing for days. check_single_instance.py is read-only
REM  (kills nothing) and exits 9 if another app.py is already running.
REM  Kill-switch: set SINGLE_INSTANCE_GUARD_SKIP=1
REM ---------------------------------------------------------------------------
REM reset errorlevel so a skipped guard can't inherit a stale 9 (2026-09-09)
ver >nul
if not "%SINGLE_INSTANCE_GUARD_SKIP%"=="1" "%PYTHON%" check_single_instance.py
if !ERRORLEVEL! EQU 9 (
    echo [!date! !time!] single-instance guard TRIPPED: app.py already running - not launching >> "%RUNLOG%"
    echo.
    echo   Another app.py bot is ALREADY running on this machine.
    echo   Running two at once poisons the proxy pool - see the 09-04 incident.
    echo   Close the other bot first, then re-run this script. Closing in ~30s.
    ping -n 31 127.0.0.1 >nul
    exit /b 9
)

echo === signing personal Chrome out of Target  --  !date! !time! ===
"%PYTHON%" chrome_target_signout.py
echo [!date! !time!] chrome target sign-out done code=!ERRORLEVEL! >> "%RUNLOG%"

REM ---------------------------------------------------------------------------
REM  Multi-account session refresh ? run ONCE, before the restart loop.
REM
REM  Brings every enabled account in config\target_accounts.json to a
REM  logged-in state and harvests its cookies (target.json / target-2.json /
REM  ...), which the WorkerPool then consumes (it auto-sizes from the same
REM  file). --auto does a SILENT REFRESH first (living sessions just re-harvest,
REM  no login, no Shape exposure) and only credential-logs the dead ones.
REM
REM  Deliberately OUTSIDE the :loop. app.py crash-restarts relaunch app.py
REM  only ? re-running logins on every relaunch would be a login storm that
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
    REM NOTE: uses !delayed! expansion ? the old %ERRORLEVEL% here expanded at
    REM PARSE time of this whole block (always 0), so failed logins were logged
    REM as code=0 and went unnoticed.
    "%PYTHON%" relogin_one.py all
    echo [!date! !time!] account login done code=!ERRORLEVEL! >> "%RUNLOG%"
) else (
    echo [INFO] config\target_accounts.json not found ? single-account legacy mode ^(relogin.py^).
)

REM ---------------------------------------------------------------------------
REM  Drop-readiness echo (2026-07-14). After the sign-out + account logins above,
REM  print a per-account MEMBER-session verdict so a cold/guest account is VISIBLE
REM  at startup instead of discovered mid-drop. Reads the freshly-harvested
REM  target*.json jars ? zero browser, ~2s, read-only. INFORMATIONAL ONLY: never
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
REM  Clean exit = operator stop, NOT a crash (2026-09-15 fix). app.py only ever
REM  reaches exit code 0 through its SIGINT/SIGTERM/SIGBREAK handler (os._exit(0))
REM  -- someone pressed Ctrl+C to stop for the morning, or the OS is logging off.
REM  A real crash never exits 0 (uncaught -> 1, access violation -> 0xC0000005,
REM  not-logged-in -> 87, deadman -> 2). Auto-relaunching after a clean stop was
REM  the 2026-09-11 footgun: the Ctrl+C reached app.py (exit 0) but not this batch,
REM  the loop relaunched, launch #2 died 0xC0000005 on the not-yet-released Chrome
REM  --user-data-dir lock, and launch #3 ran on as an unsupervised orphan. So a
REM  clean exit ENDS the wrapper; the crash-restart loop below still catches every
REM  genuine crash. Kill-switch (pre-09-15 always-relaunch): set WRAPPER_RELAUNCH_ON_CLEAN_EXIT=1
if "%EXITCODE%"=="0" if not "%WRAPPER_RELAUNCH_ON_CLEAN_EXIT%"=="1" (
    echo [%date% %time%] clean exit ^(operator stop^) -- wrapper done, not relaunching >> "%RUNLOG%"
    echo.
    echo app.py shut down cleanly ^(operator stop^). Wrapper done -- not relaunching.
    echo To run again, re-run this script.
    ping -n 6 127.0.0.1 >nul
    goto :done
)


REM ---------------------------------------------------------------------------
REM  Exit code 87 = app.py booted but the Target session was NOT logged in
REM  (2026-07-03 fix: it used to idle forever as a dashboard-only zombie).
REM  Recover the PROVEN way: re-run relogin_one.py all (home IP, validate-first)
REM  and relaunch. Burst guard: after 3 consecutive 87-cycles, cool down 10
REM  minutes so a genuinely-broken account can't login-storm Shape all night.
REM ---------------------------------------------------------------------------
if "%EXITCODE%"=="87" (
    set /a RELOGIN_BURSTS+=1
    echo [!date! !time!] exit 87: not-logged-in ? relogin cycle !RELOGIN_BURSTS!/3 >> "%RUNLOG%"
    if !RELOGIN_BURSTS! GEQ 4 (
        echo [!date! !time!] 3 relogin cycles failed ? cooling down 10 min before next try >> "%RUNLOG%"
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
REM contexts) ? caused a 0.27s/relaunch crash-loop on 2026-05-22 that burned
REM through 5 relaunches in <2s and clashed on Chrome's --user-data-dir lock
REM (STATUS_DLL_INIT_FAILED). `ping` is the reliable batch-sleep idiom:
REM 11 pings at 1s intervals = ~10s, no console dependency.
ping -n 11 127.0.0.1 >nul
goto loop

:done
endlocal & exit /b 0

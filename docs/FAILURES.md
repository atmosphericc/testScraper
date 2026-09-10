# Failure Log

> **Pivot note (2026-05-14):** post-pivot to the Round 2 resilient stack, the prior
> Walmart audit log (2026-05-11 PM-PM8) and pre-pivot Target entries have been
> moved to `docs/FAILURES_ARCHIVE.md`. This file is a clean slate for failures
> against the current architecture — see `docs/RESILIENT_STACK.md`.

## Archive Policy
Move entries older than 30 days where Outcome is confirmed resolved to
`docs/FAILURES_ARCHIVE.md`. Keep only:
- unresolved issues
- recent fixes (< 30 days)
- failures with "Root Fix Still Needed" notes

## Open Actions
_(none — last open action closed 2026-05-06: executor now returns `order_id` + `confirmation_url`
at `src/session/purchase_executor.py:1217-1239`; manager consumes them at
`src/purchasing/bulletproof_purchase_manager.py:1197-1204`.)_

---

## Format
### [DATE] - Failure Type - Retailer
**Symptom**:
**Root Cause**:
**Fix Applied**:
**Confidence**: high/medium/low
**Outcome**:

---

## Entries

### [2026-09-09] - The "~75-min per-Chrome wedge" is a Chrome-wide CDP dispatch stall with no precursor; the account Chromes never had the occlusion calculator disabled (last `--disable-features` wins) - TARGET
**Symptom**: every account Chrome stops answering CDP roughly hourly; the sentinel's 5-min tick +
63 s ladder made it look like a rigid 75-min clock. Cost per wedge: onset → `Session restored`
median 4.3 min (p90 5.8), i.e. that account is out of a drop window for ~5 min.
**Root Cause** (forensics on run_20260907, 35 wedges, 58 launches): lifetimes re-based on the
last confirmed-good CDP op are **61–92 min** (primary median 75.5, business 74.2, alt-1 63.5;
CV 0.12), lengthening through the run while traffic fell — neither wall-clock nor an event count
fits. **Harvester exonerated** (business with the harvest tab and primary without have identical
clocks). **No precursor**: the 120 s before each wedge contains nothing distinctive; the cookie
watchdog's `Storage.getCookies` round trip is 7 ms median at T-20 min and 7 ms at T-0 — it goes
7 ms → ∞ in one step. **Total and Chrome-wide**: four independent websockets to the same Chrome
(`Runtime.evaluate` on main/harvest tabs, `Storage.getCookies`, `Page.navigate`,
`Target.createTarget` on the browser socket) all stop within ~15 s while the other two
accounts' Chromes keep answering; nothing raises, every restart hard-kills a live process.
Forwarder/proxy/`WinError 10054`/memory/ping/`MAX_SIZE`/`_context_lock`/leaked-pause
hypotheses all fail the log. Best-fit finding: the account Chromes are launched with THREE
`--disable-features=` switches and Chrome keeps only the last (`session_manager.py` anti-idle
block), so zendriver's `IsolateOrigins,site-per-process,DisableLoadExtensionCommandLineSwitch`
AND the sweep pool's `CalculateNativeWinOcclusion` disable were never in effect for accounts —
the 16 sweep Chromes (which do disable it) never showed this stall, and on 08-24 the two
fp-chromium accounts never wedged while the stock-Chrome one did.
**Fix Applied**: one merged `--disable-features` value for the account Chromes that adds
`CalculateNativeWinOcclusion` (`TARGET_ACCOUNT_OCCLUSION_FIX=1`; site isolation left exactly as on
the winning nights, `TARGET_ACCOUNT_DISABLE_SITE_ISOLATION=1` restores zendriver's default for an
A/B); a `[WEDGE-PROBE]` that GETs `http://127.0.0.1:<port>/json/version` (plain HTTP, 3 s, once
per 60 s) whenever a tab-health probe times out, so the next wedge says whether Chrome's HTTP
thread is alive while CDP dispatch is dead.
**Confidence**: high on the characterisation (not rigid, no precursor, Chrome-wide, harvester
not involved); medium-low that the occlusion flag is the cause — it is the cheapest falsifiable
candidate; the probe is what turns the next run into evidence.
**Outcome**: SHIPPED, UNPROVEN LIVE. If the clock persists: run one account under fp-chromium as
the 08-24 control, then `TARGET_ACCOUNT_DISABLE_SITE_ISOLATION=1`, then `--enable-logging --v=1`
on one account Chrome.

---

### [2026-09-09] - DETECTION was dark 94-97% of the last run (3 AM slot: 0 reads in 1,501 attempts); the fallback reader never awaited its fetch; the mobile-app RedSky channel reads through the walled Bright Data IPs - TARGET
**Symptom**: run_20260907_231629.log audited end to end (de-duplicated). 40,083 sweep ticks but
**33,554 (83.7%) never became a request** (no usable session); 6,263 RedSky 200s, ALL inside two
windows totalling 38.5 of 1,126 minutes (23:20-23:23 at boot, 14:51-15:26); usable sessions = 0
for **94.4%** of the run (three ~234-min blackouts = the park ladder's 4 h cap); **02:30-04:30 =
1,501 ticks, 0 reads**; every one of the 16 sweep IPs was captcha-parked within 3.5 min of boot,
including 72.56.171.184 (the 09-07 probe's "OK-DATA" exit, parked 7x). The trusted-browser
fallback (`check_stock_via_tab`, ON for 18 h) returned **0 usable reads in 3,550 attempts**
(`Browser fetch error: no result` 3,424x). P(seeing a random 60 s restock window) = 3.5% (upper
bound); at the 3 AM drop hour = 0%. Purchase-side work (401-pulse, wave-first, banked replay) sat
behind a detector that could not fire.
**Root Cause**:
1. **The fallback reader never worked**: its RedSky JS is an async IIFE (a Promise) evaluated with
   zendriver's default `await_promise=False`, so `remote_object.value` was `None` on every read
   ("no result") — the fetch ran in-page, the answer was never awaited (also 4,567x on 09-04..07,
   where "proven live" was a misread). The 09-09 cadence change to 1-2 s therefore never ran.
2. **The BD prefixes are captcha-walled on the WEB aggregation regardless of profile freshness**:
   `RESILIENT_POOL_FRESH_PROFILES=1` (09-07 hypothesis) changed nothing — fresh sessions walled
   after ~33 reads at boot; the one recovery (4 sessions, 98% 200s for 18-31 min) came with
   equally fresh profiles and re-walled. All 16 sessions were relaunched 12-16x (the ~75-min
   pool-Chrome clock) = 220 profile wipes.
3. **The mobile-app aggregation is NOT walled**: raw HTTP `GET
   /redsky_aggregations/v1/apps/tcin_product_list_v2` with the app header set (`x-channel-id:
   APPS`, `x-client-platform: iPhone`, `x-client-version`, iOS `Target/…` UA) **through the walled
   exit 31.105.228.245 = HTTP 200 product data (0.72 s)** while the web URL through the same exit
   = 403 `captchaRelativeURL`. Same parser shape (`product_summaries[].fulfillment.shipping_options
   .availability_status`, `available_to_promise_quantity`, `item.relationship_type_code`). Two
   independent public monitors read Target this way (research 2026-09-09). From INSIDE a browser
   tab the apps URL is still captcha'd (plain headers) or CORS-preflight-blocked (app headers), so
   it must be raw HTTP.
4. Shot-path audit (adversarial, 12 findings): **a full-but-stale bank froze the harvester** —
   `pop_fresh` refused sets past the 100 s replay cap but left them in place, `need()` (TTL 300 s)
   stayed 0, no re-click for ~200 s of every 300 s cycle → most windows replayed nothing (the
   "bank EMPTY at 3/3" log); **`_cdp_continued_ids` collided across the three tabs** (interception
   ids restart per Fetch.enable; 2,476 cross-tab hits, job-1.0 alone 78x) and a colliding main-tab
   ATC was continued WITHOUT the banked headers; an empty request-header set would have let the
   override strip Content-Type/Origin; the rejected place-order RESPONSE was held paused while the
   body was fetched/decoded; the fast-lane ATC URL differed from the page's own (`%2C` + `key=`);
   every harvest capture was a STORE_PICKUP add; replay has never run on a real shot (22x self-test
   only) and only business harvested. Research: `-a0` is the overflow of the 7,900-char `-a`
   (absent in the current Target build — not a harvest defect); banked sets are single-use,
   IP-bound, product-agnostic (our design is right).
**Fix Applied** (all flag-gated; 10 files):
- `src/monitoring/redsky_channel.py` (new) + `tab_dispatcher._fire_raw_on`: `RESILIENT_REDSKY_CHANNEL
  =apps_raw` reads the app aggregation as raw urllib through each session's forwarder (or direct
  in home-IP mode), in a worker thread, reusing `_interpret_eval_result` so park/backoff/clear
  logic is unchanged; also used by the cache-busted verify read. Bat pins apps_raw.
- `stock_monitor.check_stock_via_tab`: `await_promise=True`; parks itself
  `RESILIENT_TRUSTED_READER_PARK_S` (600) on a captcha to protect the purchase account. Bat: reader
  cadence 3-5 s (the home IP tolerated ~200 reads/h on 09-07; 1-2 s was 10x that on the primary
  account, unmeasured).
- Bank: `pop_fresh` DISCARDS over-cap sets (`stale` counter), `refill_wanted()` re-clicks past half
  the cap, the loop gates on it; `TARGET_HARVEST_MAX_REPLAY_AGE_S=100` pinned; "bank STALE" vs
  "bank EMPTY" logs; `window census: shots/replayed/a0/stale` logged when a purchase ends.
- Interceptor: dedup key `f"{label}:{req_id}"`; override only with non-empty headers and a merge
  that is not shorter; checkout body read gated `TARGET_CHECKOUT_BODY_CAPTURE` (default off).
- `TARGET_ATC_BYTEMATCH=1`: fast-lane ATC URL = the page's `?field_groups=CART%2CCART_ITEMS%2C
  SUMMARY&key=9f36…`. `TARGET_HARVEST_PREFER_SHIPPING=1`: select the Shipping fulfillment cell once
  per PDP nav (JS click on the toggle) so captures are SHIPPING adds; CAPTURED logs `a_len`.
- `TARGET_HARVEST_SKIP=` (all three accounts harvest; the 09-04 primary skip was the hidden-tab bug,
  run_20260904_001027 shows the same warmup-open→failure signature). `RESILIENT_POOL_FRESH_PROFILES=0`.
- Tests: tests/test_redsky_apps_channel.py 55/55 (new), test_shape_harvest 141/141, sibling
  suites 90/53/23/70/10 green; tree compiles. Probe harness `redsky_browser_probe_bd.py` gained
  `PROBE_REDSKY_CHANNEL=apps|apps_plain`.
**Confidence**: high that the detector was dark and why (log arithmetic + the await_promise bug);
high that the app channel reads through the walled prefixes (probed on our own exit, both
directions); medium on how long HUMAN tolerates the app channel at 3/s from these prefixes
(unmeasured — the park ladder + `[STOCK STATS] 200=` will show it on the first run); medium on
the shot-path fixes converting a hot SKU (replay is still unproven on a real shot; the empty-429
demand limiter and the 3-account ticket count are unchanged).
**Outcome**: SHIPPED, UNPROVEN LIVE (user-launched). First-run checks: `[STOCK STATS] 200=` rising
past boot+5 min, `[STOCK][CAPTCHA-PARK]` quiet, `[STOCK][RATE] usable 16/16`; `[HARVEST/*]`
`fulfillment cell:` showing a Shipping click, captures with `a_len=`, and after a window `window
census: … replayed=N` with N>0. Later the same evening: the SHIPPING add body was settled from
two public real-browser captures (no `fulfillment` field; key order `item_channel_id, tcin,
quantity` / `cart_type, channel_id, shopping_context`) and byte-matched under the same flag; the
raw reader was driven end to end through the production forwarder path (2/2 reads HTTP 200 in
~0.6 s, 19/19 TCINs parsed); a `[WAITING_ROOM]` detector logs Target's "busier than we expected"
interstitial on the ATC response. **App-channel limiter measured** (raw soaks through two walled
exits): 0.5 reads/s = 293/293 clean for 10 min; 2 reads/s = HTTP 404 `{"errors":[{"message":"Not
Found"}],"data":{}}` on every read after ~166 reads (~80 s) and still 404 >3 min later (not a
captcha — an API quota, per IP). Production runs ~0.19 reads/s per exit; the bat now caps any exit
at 0.5/s (`RESILIENT_PER_IP_MAX_RPS=0.5`) and a Not-Found 404 parks only that session
(`RESILIENT_RAW_404_PARK_S=120`, ×2 per repeat, cap ×8, reset on 200) without souring its /16.
Still open: the ~75-min Chrome wedge (forensics in progress); the exact 404 ban length.

---

### [2026-09-09] - Shape harvester: the 99.7% click timeouts were a HIDDEN-TAB input stall, not CDP-socket contention; every failed tab open sat inside the ~75-min wedge - TARGET
**Symptom**: run_20260907_231629.log (09-07 23:16 → 09-08 18:06, business-only harvest).
De-duplicated counts (the run log carries every `[HARVEST/…]` line twice — print + logger):
**4,613** `click dispatch failed (TimeoutError)` vs **16** captures + 18 completed-but-no-POST
clicks; **160** tab-open attempts / **14** ready / **146** FAILED. The 09-08 diagnosis ("the
warmup interceptor's ~31,819 round trips saturate the account browser's single CDP websocket,
so the click's 6-11 sends lose the race") and the two band-aids built on it (budgets 25→45 s
and 6→12 s; `TARGET_HARVEST_CLICK_MOVES=2`, 7025492e) were wrong.
**Root Cause**:
1. zendriver opens ONE websocket PER TAB (`Connection(websocket_url=…/devtools/page/<id>)`),
   so the harvest tab never shared a socket with the warmup interceptor — whose 31.8k LINES
   were ~9k requests over 19 h (<0.5/s), nothing for a websocket.
2. Timeline: every successful open took 0.3–0.7 s and its first click captured in
   0.57–1.03 s. In 11 of the 14 browser lives a `[WARMUP] Opening warmup tab` line landed
   5–45 s BEFORE the first click failure, with every capture before it; in two more the
   warmup open came in the same second as the harvest tab (0 captures); life 1 at boot had
   the 16 sweep Chromes launching on-screen and the operator at the keyboard. Desktop
   Chrome's `ChromeDevToolsManagerDelegate::CreateNewTarget` hard-codes
   `WindowOpenDisposition::NEW_FOREGROUND_TAB` (it ignores both `background` and
   `newWindow`), so the warmup tab re-created after each sentinel Chrome restart pushed the
   harvest tab into the BACKGROUND.
3. Chromium queues `mouseMoved` as rAF-aligned input (`MainThreadEventQueue::IsRafAlignedEvent`);
   a hidden tab produces no main frame, so each queued move is released only by the fallback
   timer `kMaxRafDelay = 5 s` ("This fallback fires when the browser doesn't produce main
   frames … eg. Tab gets hidden"). `Input.dispatchMouseEvent` answers only on the renderer's
   ack (`InputInjector::OnInputEventAck`), so 2–12 moves × 5 s beat ANY budget, while
   `Runtime.evaluate` (no frame needed) kept working — exactly the observed signature. The
   account browsers' `--disable-backgrounding-occluded-windows` only turns OCCLUDED into
   VISIBLE (`WebContentsImpl::UpdateVisibilityAndNotifyPageAndView`); it does nothing for a
   background tab or a minimized window (`IsIconic` → HIDDEN).
4. Tab opens: 143/146 failures took exactly 10.0 s = zendriver's own
   `wait_for(TargetInfoChanged, 10)` inside `browser.get()`; every one followed a
   `button lookup failed` (evaluate timeout on the harvest tab) and ended with the sentinel
   restart — i.e. the pre-existing ~75-min wedge phase (entries 07-23 / 07-08), not a slow
   PDP. The 25→45 s budget could never fire first (opens outside the wedge: 0.5 s).
5. Every drop only cleared the handle (`self._harvest_tab = None`) — a leaked live PDP tab
   (React + Shape + PX sensors + Fetch interceptor) per drop; the 3-strike drop would have
   leaked one per ~45 s while hidden.
**Fix Applied** (flag-gated, harvest-only; 5 files): `shape_harvest.human_click` times every
CDP send (`stats`) and raises `HarvestTabNotPainting` when ONE `mouseMoved` exceeds
`TARGET_HARVEST_MOVE_ABORT_MS` (2500 — the 5 s fallback fingerprint); `VISIBILITY_PROBE_JS`
+ `visibility_verdict` (visibilityState + one rAF within 700 ms); executor
`_harvest_ensure_visible` probes before every click and re-activates the tab
(`Target.activateTarget`, bounded) — never over a parked account ('skip', no strike; a live
purchase does NOT block it: the shot path is fetch/JS-click based and the in-window refill is
what keeps the bank fresh for a wave's later shots); 'still hidden after activate' counts toward the 3-strike drop; `_harvest_drop_tab`
CLOSES the tab (browser-level `Target.closeTarget`, 5 s bound) at all five drop sites (not on
"browser changed"); `_harvest_close_orphans` closes PDP tabs left by timed-out opens;
`TARGET_HARVEST_CLICK_MOVES` default back to 9 (natural path); every click line logs
`sends/moves/max_move_ms/press_ms`. Bat: `TARGET_HARVEST_VIS_GUARD=1`,
`TARGET_HARVEST_MOVE_ABORT_MS=2500`, corrected note. Tests: test_shape_harvest 141/141 + the
five sibling suites green.
**Confidence**: high — mechanism confirmed by Chromium source, 14/14 lives, AND a local
reproduction on this machine (2026-09-09, throwaway Chrome on `data:` pages, no Target, no
profile): foreground `mouseMoved` ack 0–16 ms; after one `Target.createTarget` the first tab
reports `visibilityState=hidden`, `evaluate` still 0 ms, `mouseMoved` **5,032 / 5,015 ms**,
press+release 0 ms; `Target.activateTarget` from the tab's own session 63 ms → visible, moves
0 ms; `human_click` on the hidden tab aborts at move #1 with `HarvestTabNotPainting` (5,014 ms).
The 09-03 `-a0` capture was the SECOND capture on the same PDP (~40 s in), i.e. accumulated
interaction — the visibility fix makes repeated same-page clicks routine again. Remaining live
proof on the next quiet-night run: `max_move_ms` ≈ 16–50 on captures; `click ABORTED:
mouseMoved #1 took ~5000 ms` followed by `harvest tab re-activated -> VISIBLE` whenever a
warmup tab steals the foreground.
**Outcome**: SHIPPED, UNPROVEN LIVE (user-gated). Still open: the ~75-min wedge itself (every
open failure and every Chrome restart on 09-07 was that clock). `TARGET_HARVEST_SKIP=primary`
was justified on 09-04 by "18/18 clicks failed under sweep load" — CONFIRMED the same
hidden-tab mechanism, not loop contention: run_20260904_001027.log shows primary's harvest tab
ready 00:10:39, `[WARMUP] Opening warmup tab #1` at 00:10:59, first click failure 00:11:05 and
every click after. Un-skipping primary/alt-1 is justified once the guard is live-validated. Operator rule: never minimize
the account Chrome windows while the harvester runs.

---

### [2026-09-04 → 09-07] - Sweep blind for 3.9 days + two accounts driven to GUEST by the sentinel ladder - TARGET
**Symptom**: run_20260904_014716.log (bat 09-04 01:47 → user stop 09-07 22:30). The
home-IP sweep (`RESILIENT_HARVEST_VIA_LOCAL_IP=1`, 2 cold sessions) read **zero**
RedSky 200s the entire run (11,050 consecutive failed ground-truth reads, captcha
backoff pinned at x16 from 01:48); the 09-04 emergency trusted-tab reader
(`RESILIENT_FORCE_TAB_FETCH=1`) timed out 30,012× on `get_page` (3 s) and returned
"no result" 4,567×, 1 stock detection all run. From 09-05 the sentinel walked its full
ladder on business and alt-1 every tick: token repair → nav refresh → Chrome restart →
full sign-out → scripted relogin (`username did NOT advance` 0/12, 09-06 23:40 → 09-07
20:55) → relogin CAPPED; both accounts ended GUEST (`check_session_readiness.py`).
`TOKEN CHURN` alerts 7×. Harvester (business only): 88 captures, 1,270 harvest-tab
open failures, SELFTEST 424 2/2 at boot, 0 replays (no hot window occurred).
**Root Cause**: (1) The RedSky wall is HUMAN Security / PerimeterX (Target now runs
HUMAN's sensor on the storefront in addition to Shape — `_px2/_px3/_pxhd/_pxvid/pxcts`
on `.target.com` in every jar), keyed to the cold profiles/device trust after the 09-04
double-bot overload, so moving the sweep to the home IP did not help and the pool's
cold profiles stayed walled on any IP. (2) A challenged account browser renders the
"Press & Hold" interstitial on the sentinel's `/account` nav; the ladder read that page
as a dead session and escalated into the DESTRUCTIVE rungs, wiping still-valid
login-sessions and then failing the Shape-burned scripted relogin. (3) The run exceeded
the 24 h restart rule by ~3 days (login-sessions rot at 32-46 h), compounding (2).
**Fix Applied** (2026-09-07, flag-gated, no browser; tests/test_px_challenge_guard.py 90/90 +
test_captcha_backoff_local_ip 23/23, regression test_shape_harvest 95/95 + test_0828_phase2_fixes 70/70): BD sweep
pool restored (16 IPs, 3.0/s = 0.19/s per IP) with `probe_sweep_pool.bat` as the
read-only pre-drop standing check; `RESILIENT_FORCE_TAB_FETCH=auto`
(`src/monitoring/tab_fetch_policy.py`: trusted-browser fallback only while the pool has
no 200 for 180 s); `src/session/px_challenge.py` + sentinel guard
`_px_challenge_parks_ladder` (`TARGET_PX_CHALLENGE_GUARD=1`: a challenge page parks the
restart/sign-out/relogin rungs for 300 s, alerts `[PX-CHALLENGE]`/`[AUTH_CRITICAL]`,
leaves the widget for a person); `px_block` labels on ATC/place-order 403s;
`[STOCK][PX-CAPTCHA]` on the trusted reader; hand-login prompt names the widget.
**Probe verdict (2026-09-07 23:05, `probe_sweep_pool.bat`, same fresh profile, same
machine)**: 31.105.228.245 / 31.105.93.225 / 168.158.143.27 → 403 captcha envelope;
72.56.171.184 → 200 OK-DATA twice. So the wall is IP-RANGE reputation (HUMAN), not the
device, and after 3.9 days of rest most of the pool is still walled. Shipped on top
(same night, flag-gated, tests/test_captcha_session_park.py): a captcha 403 now parks
THAT session (`RESILIENT_CAPTCHA_PARK_S`=1800, ×2 per repeat, cap ×8) instead of
slowing the whole sweep, so the clean IPs keep cadence and walled ones retest on their
own; the sweep is capped at usable_sessions × `RESILIENT_PER_IP_MAX_RPS` (1.0), so one
clean IP is read at 1/s not 3/s; `RESILIENT_POOL_FRESH_PROFILES=1` wipes the pool's
scratch profiles at launch (they carry the HUMAN ids flagged on 09-04); the
trusted-tab `get_page` budget is `RESILIENT_TAB_FETCH_TIMEOUT_S`=8 (was 3); the probe
script gained `home` / `reserve` / single-IP modes. Every session walled → the auto
tab-fetch is the detector.
**Confidence**: high on the vendor + ladder mechanism (cookies, probes, log census) and
on the range verdict (probe); the real lever for the pool is NEW exits outside
31.105.x / 168.158.x (Bright Data refresh) — one clean IP at 1/s is a floor, not a plan.
**Outcome**: pending first live run. Operator: `hand_login_all.bat` → 3/3 MEMBER,
`probe_sweep_pool.bat all` / `reserve` / `home` → move CAPTCHA IPs to reserve, request
replacements from Bright Data, launch exactly once, ≤ 24 h.

### [2026-08-28] - Fri drop 0-for across 14 windows: unwinnable 401 wall behind the edge limiter, PLUS ~980 shots forfeited to the breaker and ~1.1s/cycle of self-inflicted retry waste - TARGET
**Symptom**: run_20260827_224501.log (bat 08-27 22:45 -> user stop 17:01).
Six TCINs released one at a time 02:03-04:44 in a fixed ~19-26 min sequence,
then replayed in 30-115 s backfills = 14 in-stock windows, 3,830 in-stock
seconds. 2,680 fast-lane shots (business 927 / primary 881 / alt-1 868),
ZERO 2xx. Census ([ATC_RESP] live): 2,248x empty-body 429
ERR_A2C_TCIN_RATE_LIMITED (84%), 429x 401 _ERR_AUTH_DENIED T83072242, 3x
FAST_SELLING/DCO (business). Hotpath led ground truth 14/14; lock->first
response 0.17-0.69 s. W05 (1011960739) had 21.6 min of continuous in-stock
evidence and got 58 shots in its last 18 min.
**Root Cause** (Phase-2 multi-agent audit 08-31, finders F2/F4/F6/F7/F9 +
adversarial skeptics on H1/H3/H4/H5; transcripts in session e99fcf14 wf_00f7556c):
TWO GATES IN SERIES, and both were shut for us:
1. CONFIRMED: empty-429 = a GLOBAL per-TCIN edge limiter ahead of Shape
   (shot #1 of the night 429'd on all 3 identities within 70-115 ms on
   never-POSTed TCINs while the same browsers' calm-TCIN dummy passed in the
   same second; 0x429 in 48,716 dummy POSTs across 4 nights; 429 returns
   ~80-120 ms FASTER than 401 and carries an error key while the 401 carries
   none). On REGULAR SKUs it ate 94-96% of shots all window; on HOT SKUs it
   was binding only the first 2-5 min, then went quiet as crowd traffic fell.
2. The 401 behind it is a TCIN-scoped Shape-score gate (not a dead token,
   not velocity): **P(401 | shot passed the limiter) = 100% on 08-28** —
   429/429 passed shots died, every identity, every TCIN, every velocity
   bucket, from shot #1, after 17 min at 1 shot/min, and after 50-66 min of
   rest. The hot-vs-regular "401 share" split (54-73% vs 5-8%) is the
   limiter's pass rate in disguise, NOT differential Shape strictness.
   Measure P(401|not 429) in every future audit.
WHAT SEPARATES WINNING NIGHTS (F2, decisive): the Shape verdict on
WAVE-FIRST shots (first shot per identity on a TCIN after a >=15 s own
pause). 07-31: wave-first-3 limiter-passers converted 32.6% (REG) / 9.1%
(HOT) vs 0.9% / 0.0% for later re-POSTs; 08-04: 4.7% vs 0.3%; 08-28: 0/74
(P ~ 3e-11 vs 07-31). Every order ever = shot #1-3 of a fresh wave,
including two 07-31 201s on attempt #1 of fresh waves AFTER 100+ own shots
on the TCIN. Night-level wave-first pass collapse 28% -> 5% -> 0%
(07-31 -> 08-04 -> 08-28) is the real gap; re-POSTs into a 401 are ~1% EV.
IDENTITY SPLIT (new): on the contested gate, business (real Chrome 151 +
31.98.158.87) passed 9/146 (6.2%) vs the fp-chromium pair 0/318 (Fisher
p=2.5e-5) pooled over 08-26+08-28 — confounded with IP range (31.98.x vs
168.158.x), NOT proof the engine is the cause. The calm-TCIN dummy 401 rate
(~20%) is identical across all three and says NOTHING about contested
readiness.
SELF-INFLICTED (all verified vs refuted):
- CONFIRMED: breaker loop (arm streak 20 -> 45 s cooldown -> 20 s
  LEVEL_REARM tick -> 'tcin_throttled_cooldown' non-transient bail kills the
  wave -> 1 escaping shot/identity/60 s -> 401 re-arms; streak resets ONLY
  on a 2xx and the TTL clock refreshes on every counted denial, so it can
  never decay under the loop). W05 48.3 -> 3.2 shots/min for 1,082 s (~821
  forgone), W06 ~90-111, W07 opened pre-armed on W06's carried streak (4
  shots in 59 s, ~46). REFUTED premise: nothing was protected — 245 arms in
  9 runs, 0 ever followed by a 2xx; post-arm probes at 3/min stayed 401 for
  18 min; hot-SKU 401 re-forms in ~45-95 s at ANY cadence (W12/W13 fresh
  streaks armed +92-105 s in); calm-TCIN heartbeat 401 flat pre/post-arm.
- CONFIRMED: per-retry dummy-POST warm (fires even with FORCE_REWARM=0;
  2,811 in-flight warms, ~3,000 dummy POSTs during races) + its ~20% 401s
  triggering the 0.6 s/probe confirm re-probe + 17/17 FALSE "confirmed dead"
  mid-race repairs (4-13 s stall each) = ~1.0-1.15 s of a 3.2-3.6 s cycle.
  The ring never dropped below 4 unused captures (each real shot refills
  it); injected headers are re-signed in-page (07-10). ~+40-45% tickets from
  removal, and LESS cookie burn (Refract: more cookies = more flags).
- CONFIRMED: cart-hold GET after EMPTY-body 429s — 1,526 GETs / 0 hits / 21
  runs; 1,049 followed edge-429s that are dropped before the carts app and
  cannot silently land. 0.19 s median each.
- CONFIRMED: shot-#1 TTL refresh (headers_age>60) — 4 firings, all taxing a
  wave's shot #1 by 0.2-2.05 s (one opened a lazy warmup tab mid-race).
- REFUTED: "FAST_SELLING 45-s cooldown idled business" — the cooldown is
  wired to place-order paths only; 0 [THROTTLE] lines all night; the 3 DCO
  shots retried at normal cadence.
- REFUTED: "2,228 s of inter-wave dead gaps / ~1,894 shots" — a
  double-counting artifact (breaker dead-air recounted per [RACE] line).
  Real: 16 in-stock boundaries, ~154 s, ~130-160 shots (median lag 11.6 s
  from the 20 s poll). Real but minor.
- Minor: W06 lost ~68 s to a cloaked-OOS hotpath flap that dropped the TCIN
  from the LEVEL_REARM map while ground truth said in stock (no
  last-in-stock grace in _build_level_rearm_map — known residual).
**Fix Applied** (2026-08-31, all flag-gated, armed in
run_bot_with_nightly_restart.bat, pinned by tests/test_0828_phase2_fixes.py
70/70 + 16 adjacent suites green):
- TARGET_ATC_GATE_BREAKER=0 (bat + code default flip in purchase_executor)
  ~= +980 shots on an 08-28 night.
- TARGET_RETRY_WARM=0 (manager): skip the awaited per-retry warm; forced
  re-warm, 60-90 s background refill, and sentinel keep-fresh unchanged
  (07-07 dead-token net intact). ~+40% tickets, ~25% less cookie burn.
- TARGET_CART_HOLD_SKIP_EDGE=1: skip the hold GET on empty-body 429s only;
  DCO/401 keep it (silent lands documented there).
- TARGET_SHOT_TTL_REFRESH=0: no proactive warm on the critical path; fire
  from the ring/cache (<90 s use_cached gate still applies).
- TARGET_ATC_DCO_AS_EDGE_CADENCE=1: ATC-level DCO rides the edge cadence.
- TARGET_LEVEL_REARM_S=3 (was 20): boundary ~1.5-6.5 s, ~+130-160 shots.
- TARGET_401_PULSE=1 STREAK=3 SLEEP 15-25 s (the doctrine bet, from F2):
  after 3 consecutive carts-401s an identity pauses 15-25 s IN PLACE OF one
  cadence sleep so its next shot re-enters wave-first (32.6% vs 0.9%).
  Edge-429s neither count nor reset the streak, so an edge-lottery window
  keeps FULL ticket cadence — the 08-18/08-21 ticket doctrine is unchanged
  where it applies.
- TARGET_ATC_REFERRER_PDP=1: fetch `referrer` INIT option carries the real
  PDP URL (the headers-object Referer is a forbidden name and never reached
  the wire; real adds carry the full PDP URL).
**Confidence**: high on the causal model and waste fixes (adversarially
verified, cross-night); medium on the pulse (mechanism proven on 07-31/08-04
data, not yet live-tested).
**Outcome**: pending next drop. NEXT LEVERS (not shipped): (1) the
contested-gate identity split is IP-range-confounded — test moving
primary/alt-1 to a 31.98-like exit or home IP before blaming fp-chromium;
(2) F9's banked-Shape-header experiment (4-arm in-Chrome A/B via
Fetch.continueRequest header rewrite on an elevated-level TCIN while OOS —
design in the Phase-2 dossier) decides whether the winners' bank mechanism
is viable on our identities; (3) sensor payload: our shots never carry
X-GyJwza5Z-a0 (idle-tab behavioral payload) while real ATCs do — PDP-parked
tab + real pointer events is the leading candidate for the hot-TCIN wall;
(4) capture ONE real DevTools ATC click and byte-match URL/key=/%2C/body.

---

### [2026-08-26] - 0-for on the 02:51 FPIC-S3 restock: bot mechanically flawless, lost the empty-429 lottery; census verdict = lottery windows are 0-for-1,865 tickets all-time - TARGET
**Symptom**: run_20260825_224317.log (user launch 22:43 after the 08-25
login-gate fix; ran 21 h to a clean 19:46 stop). 1011960739 (First Partner
IC-Series 3, hyped) flipped IN_STOCK 02:51:22, OOS by ~02:52:38 (~60-75 s
productive window; the only in-stock event of the run). Detect->shot#1 0.8 s.
85 fast-lane chains (business 30 / primary 28 / alt-1 27, ~4 s cadence each),
budget spent to the 110 s deadline, ZERO 2xx. Composition: 53x empty-body 429
(62%), 26x hard 401 _ERR_AUTH_DENIED T83072242 (30%), 6x DCO_RATE_LIMITED
(business only). First ~8 shots/identity almost all empty-429; every
identity's tail went all-401 -- and the tail correlates with the ~02:52:38
sell-out (post-OOS shots, ~8-10/identity, t-left<~34 s = wasted), not with
shot spacing. No fp-chromium vs real-Chrome asymmetry (business drew 401s
too; fp A/B still unjudged -- hyped-SKU night). All 08-21 fixes behaved
live: breaker never armed, 401 bail ~0.4 s, warmup nav guard held,
cart-hold reads ran.
**Root Cause**: Not a bug. This is the known hyped-SKU empty-429 edge
lottery, executed to spec. The 2026-08-26 cross-log census (all
FAST_LANE-instrumented drops since 07-24) is decisive: LOTTERY windows
(empty-429 >=50%) = 19 windows / 1,865 tickets / 4 ATC-2xx / **0 orders**
(P(order|ticket) 95% UB = 0.0016); REGULAR windows (401-dominated) = 27
windows / 4,914 tickets / 33 ATC-2xx / **15 orders** (P=0.0031). ~85% of all
ATC-2xx land within the first 3 attempts; every confirmed order but one was
attempt #1 of a fresh edge. 1011960739 lifetime: 0-for-~2,948 shots.
Conclusion: wins come from being FIRST on a 401-dominated REGULAR-SKU edge
(the bot already is, 0.8 s); raw ticket volume on a hyped lottery SKU has
never converted for us.
**Fix Applied** (2026-08-26, flag-gated, adversarially verified,
tests/test_edge429_cadence.py + full regression green):
1. Adaptive edge-lottery cadence: `TARGET_ATC_EDGE429_RETRY_DELAY_MIN/MAX`
   (default 2.0/3.0 s, floor-clamped) applies ONLY when the last attempt's
   gate_kind=='edge' (empty-body 429); 401/DCO keep 2.5/3.5. ~+50% tickets
   in a lottery window. HONEST LABEL: census EV of those extra tickets ~= 0
   on hyped SKUs -- a doctrine bet, not an evidenced converter. Rollback:
   set both EDGE knobs to 2.5/3.5.
2. `Tgt-Cart-Error-Key` capture on ATC POST responses (the 08-21 open item):
   CDP response-stage pattern on web_checkouts/v1/cart_items, log-only
   `[ATC_RESP] status= tgt-cart-error-key= x-request-id=` line;
   continue-exactly-once per the 07-23 leak rule, response continued BEFORE
   any logging so the hot path (incl. a winning 201) is never held.
   Kill-switch: TARGET_ATC_RESPONSE_HEADER_CAPTURE=0. Next lottery window
   finally decides demand-throttle vs identity-block.
Also: 08-25 login-gate + TCIN-visibility fixes live-validated over the 21 h
run (banner hourly, state file, zero new-code tracebacks).
**Confidence**: high (every number machine-extracted from the logs; census
scripts in the workflow scratchpad).
**Outcome**: Open operational items: (a) alt-1 login-session DIED 08-26
afternoon (TOKEN CHURN 12:21 -> relogin capped 12:30 -> GUEST jar) -- needs
a hand-login before the next window; (b) recorded, not changed: the wave
kept firing ~36 s past the 02:52:38 sell-out (retry-while-in-stock does not
re-check mid-wave) -- revisit only with care (05-22 stale-cache lesson);
(c) the REAL lever per the census is SKU regime: regular SKUs convert,
hyped lottery SKUs never have -- arming mix is an operator decision.

### [2026-08-25] - 0-for because NO Target restock happened (operator-confirmed); audit found a latent gap: the 4 TCINs armed for 08-24 were unpublished on Target (invisible to RedSky all night) - TARGET
**Symptom**: run_20260824_231920.log (bat launch #1 23:19:20 after hand-logins
23:14-23:16 — business + alt-1 credential logins, primary already in; the bat
relogin at 23:18 found 3/3 already logged in; check_session_readiness 3/3
MEMBER; clean user shutdown 07:40:38, 8h21m). 0 orders, and nothing to
post-mortem on the ATC side: zero `FAST_LANE` / `ATC fetch` / `chain done`
lines — the purchase manager never fired. Every `[STOCK TRACE] hotpath
in_stock=[] of 13 configured TCINs` (99×) and every `[GROUND-TRUTH] pool
cache-bust ok: in_stock=[] (9 TCINs)` (197×) was empty. Monitor was healthy:
88,886 sweeps @ 2.97/s, 200=87,751 (98.7%), 403=22, other=1,102 (1.2%; 08-21
baseline 1.05%); pool 16/16 ready; 7 ground-truth read failures (http=0);
canary 403 = the known raw-urllib false positive (same on 08-21). Accounts
healthy: sentinel logged_in=True all night, 13 `WRITE-AUTH DEAD` (401)
heartbeat events all self-repaired via cookie-delete + /account reload,
warmup 424 heartbeat alive throughout. THE line (verbatim, note the em dash; grep
`absent from RedSky` to find it): `[GROUND-TRUTH] 4 configured TCIN(s)
absent from RedSky bulk response — invisible to detection: [1012644665,
1012644666, 1012644667, 95290385]` every 2.5 min all night (197×) — as a
logger.warning nobody read. Note the ground-truth read says
"9 TCINs" while 13 are configured: that 13-9 gap IS the finding.
**Root Cause**: No Target restock occurred that night — the operator confirmed
this after the fact (2026-08-25). The 0-for is therefore fully explained by
"nothing dropped"; the bot behaved correctly. What the audit found is a LATENT
gap that would have cost the NEXT drop, not this one: the four absent TCINs are exactly the four NEW catalog
entries in 2932805a (committed 23:18:08; `config/product_catalog.json`
`date_added` 23:10:22-23:12:28 via configureProducts.py; verify:
`git show 2932805a -- config/product_catalog.json | grep date_added`) — the
same commit also re-armed 1010892065/67/68/69, which RedSky did resolve. The
four new ones landed as name-less `Product <tcin>` entries (never resolved by
RedSky). Off-host
verification 2026-08-25 ~08:00: RedSky `pdp_client_v1` returns HTTP 404 (no
product) for all four, while the control TCIN 1011960739 returns a full
payload (First Partner Illustration Collection Series 3, $17.99, street date
2026-08-07) — the four are unpublished on Target (or mistyped), not blocked.
Had a drop happened on those TCINs it could never have been detected; the 9
visible TCINs never flipped because nothing dropped. External context: restockd.app listed a POSSIBLE Target
release Tue 2026-08-25 12 AM PT / 3 AM ET ("Chaos Rising, Pitch Black, One
Piece & more") with the note "this drop might be pushed to Friday"
(2026-08-28, 12 AM PT / 3 AM ET); Mega Evolution-Chaos Rising itself released
2026-05-22, so that would be a restock. No monitored TCIN flipped in our
window (which covered 2-3 AM CT). Process failure: the 2026-07-30 patch
already logged the absent-TCIN warning; the operator step "read that line at
boot" failed silently — a warning-level log line every 2.5 min in an 8-hour
run is invisible.
Also observed (recorded, not fixed): the business identity (real Chrome 151,
nodriver-profile-2, the fp A/B control) wedged on the known ~75-min
per-Chrome CDP clock at 00:26, 01:36, 02:51, 04:06, 05:21, 06:31 — sentinel
escalated to browser restart, `Session restored after browser restart` ~48 s
later each time; the two fp-chromium identities (primary, alt-1) never
wedged. Coverage gap: the bot was NOT running 21:21-23:19 on 08-24 (the 21:21
auto-launch died to a KeyboardInterrupt in `_reap_orphan_repo_chromes`).
Host Chrome auto-updated to 151.0.7922.174; `account_identity._CHROME_BUILDS`
bumped to match (same major; preflight passes either way).
**Fix Applied** (2026-08-25, flag-gated, purchase path untouched): make the
condition LOUD and visible pre-drop.
1. Checker: on first detection of a configured-but-absent TCIN it prints a
   `[TCIN-VISIBILITY]` banner + logger.error + `on_alert` → dashboard activity
   feed (level error → also `logs/error_log.txt`), and re-alerts hourly while
   it stays invisible (`RESILIENT_TCIN_INVISIBLE_REALERT_S=3600`). When Target
   publishes a previously-invisible TCIN it logs `NOW VISIBLE` + a success
   feed entry. The existing every-5th-cycle logger.warning stays regardless.
   Hardened after adversarial review: "invisible" is debounced via sweep
   sightings (absent from the cache-bust read AND unseen by ANY 200 response
   for >`RESILIENT_TCIN_INVISIBLE_GRACE_S`=90 s), a 200 that parses to 0
   TCINs counts as a failed read, alerts are TTL-gated in BOTH directions,
   10 consecutive failed ground-truth reads (~5 min) raise a
   `[TCIN-VISIBILITY] UNKNOWN` alert so silence is never read as
   all-visible, and the state file is written on the first successful read.
   Round-3 hardening: a blind run is marked in the state file (`verified=false`
   + `gt_fail_streak` / `verification_failed_since_unix`; readers say UNKNOWN /
   WARN instead of trusting stale lists), the visibility bookkeeping runs AFTER
   the C0 cold/stale fire loop (it can never delay a purchase trigger), a
   recovered read logs `[TCIN-VISIBILITY] verification RESUMED after N
   consecutive failed ground-truth reads` and re-arms the UNKNOWN gate, and
   the pre-drop scripts warn up front when >30 TCINs are enabled (RedSky
   caps the unchunked ground-truth read at 30/req; the sweep chunks at 28).
2. Checker writes `state/tcin_visibility.json` (schema + reader API in
   `src/monitoring/tcin_visibility.py`: configured / visible / invisible /
   last_seen_unix per TCIN; `RESILIENT_TCIN_VISIBILITY_STATE=1`) — on change,
   at least every 2.5 min (every 5th ground-truth cycle) and on the first
   successful read; `updated_at_unix` is the freshness key (stale after 24 h).
3. Readers: `check_session_readiness.py` (runs inside
   run_bot_with_nightly_restart.bat and hand_login_all.bat) and
   `preflight_fp_drop.py` ([8b]) read that file and WARN about
   enabled-but-invisible TCINs (and flag TCINs added since the last run as
   unchecked).
Flags: `RESILIENT_TCIN_VISIBILITY_ALERT=0` / `RESILIENT_TCIN_VISIBILITY_STATE=0`
restore the old behaviour; `RESILIENT_TCIN_INVISIBLE_REALERT_S` (default 3600)
is a TTL (floor 60 s), `RESILIENT_TCIN_INVISIBLE_GRACE_S` (default 90) the
debounce (floor 30 s).
**Confidence**: high (no edge + invisible TCINs are both directly
log-evidenced; the "unpublished" verdict is corroborated off-host).
**Outcome**: Fix shipped (uncommitted at time of writing) + docs; operator
action for the next window: verify the four TCINs at the source
(configureProducts.py entries were name-less `Product <tcin>` = never
resolved by RedSky), keep them armed only if they are real upcoming SKUs
(they auto-appear when Target publishes them and the bot now shouts
`NOW VISIBLE`), re-run hand_login_all.bat before the next window (jars from
08-24 23:14 will be >24h old), and watch for the `[TCIN-VISIBILITY]` banner
in the same second as the first `[STOCK STATS] t=30.0s` line (30 s after `[MULTI_SESSION] started -- N/N sessions ready`, ~3-4 min after launch; 08-24: launch 23:19:20 -> pool ready 23:22:33 -> STATS + banner 23:23:03).
No banner is the GOOD case (it prints only when something is invisible) -- confirm it positively: the `[GROUND-TRUTH] pool cache-bust ok: in_stock=[] (N TCINs)` line in that same second must show N == the number of armed TCINs (08-24 showed `(9 TCINs)` for 13 armed = THE finding). If that line is missing too, look for `[GROUND-TRUTH] pool cache-bust read FAILED` / `no ready session` lines; after ~5 min of failed reads the bot prints `[TCIN-VISIBILITY] UNKNOWN -- N consecutive ground-truth reads failed` and stamps the state file `verified=false`.

### [2026-08-21] - 0-for on the First Partner Illustration Collection drop: the breaker armed on the EDGE LOTTERY and cut the only window; the 401 wall was never the differentiator - TARGET
**Symptom**: run_20260820_225041.log (bat launched 22:50 after hand-logins
22:22-22:33; user stopped 17:41). Two armed TCINs went live: 1011960739
03:12:48-03:25:32 (12.7 min) and 1011209279 04:13:54-04:18:34. 0 orders,
0×2xx on 151 chain shots (90 empty-body 429, 61 hard 401 _ERR_AUTH_DENIED
T83072242, 1 DCO-body 429) + 103 in-ladder retries (0×2xx). Detect→first
shot 0.8 s (fine). Breaker armed on all 3 identities at 03:14:39 (streak 20
in ~2 min); from 03:15 to 03:24 the fleet fired ~3 shots/min (1 per identity
per ~60 s, not 45 — quantized to the 20 s LEVEL_REARM tick) and every probe
from 03:19 on was a 401. business's login-session died 10:32 (6 h after the
last window; 4 sentinel credential relogins failed under fp-chromium on the
home IP; capped 11:42) — irrelevant to the 0-for, hand-login required.
Bot ran clean: 15 tracebacks are all asyncio WinError 10054 noise.
**Root Cause**: (a) The prior theory ("401 = cooked Device ID+, hopeless")
is falsified by the winning-night logs: 07-31 (9 orders, 1,014 shots) was
63% hard-401 and 08-04 (4 orders, 1,676 shots) was 76% — MORE 401-heavy
than tonight's 40% — and every win landed INSIDE a 401 wall, in the same
minute as 401s on the other identities. (b) Every 201 ever won came within
~2 min of an in-stock EDGE; 10/11 winners were shot #1 of their race. Windows
whose first 60 s were dominated by EMPTY-body 429 have NEVER converted on any
night (winning nights included — 07-31's 1011209279/1012055696/95274164
windows had that shape and 0-for'd while regular SKUs converted). Tonight's
only long window opened 30×429e / 2×401 = the never-converting shape, and
every SKU armed since 08-07 has been a hyper-hyped one. (c) The breaker then
made it worse: the streak that armed was 53/62 empty-body 429s — the global
edge lottery the 08-18 analysis itself says shot count is the only lever
against — not the carts-service denial the breaker was written for. It cut
~85% of window-1 shots (151 vs 1,014/1,676 on winning nights; at the
winning-night 0.7-1.9% per-shot 201 rate, 151 shots is a coin-flip for 0).
(d) Waste multipliers, present on winning nights too but contradicting the
"stop hammering" premise: the 401 repair ladder fires 2 more ATC writes + a
token_refresh + an /account page load per 401 and blocks the identity median
2.6 s / p90 7.3 s / max 9.3 s (0/85 mints, 0/103 in-ladder retries converted;
the same token READ the cart 200 after every 401, bogus-TCIN adds 424'd all
night → it is not a dead write token); the manager's inter-retry
`warm_shape_headers(force_fresh=True)` bypassed the purchase-time /cart
guard and fired a /cart load + cart PUT (ADDRESSES, the CVV re-entry trigger)
+ dummy POST between EVERY retry (03:13: 26 navs / 27 PUTs for 30 shots).
(e) Environment deltas vs the 9-order night, the only things that changed:
fp-chromium on all 3 (08-11), rotated primary/alt-1 exits (08-07), the
breaker (08-09/08-18), post-denial CART_HOLD reads (08-14). The ATC request
itself (headers, qty, fast lane, budget, cadence) is byte-identical.
**Fix Applied** (all flag-gated, defaults = new behaviour):
1. Breaker counts only carts-service denials: the 429 bail now carries
   `gate_kind` ('edge' = empty body, 'dco' = DCO body); `_note_atc_gate_outcome`
   treats 'edge' as neutral unless `TARGET_ATC_GATE_COUNT_EDGE_429=1`. Streak
   decays after `TARGET_ATC_GATE_STREAK_TTL_S` (600; 0 = never) so a same-SKU
   restock hours later is not one-denial-then-armed.
2. ATC-401 repair ladder OFF (`TARGET_ATC_401_LADDER=1` restores): a 401 runs
   the cart-hold READ then bails to the Error-Delay re-shoot immediately.
3. Inter-retry re-warm is non-forced (`TARGET_RETRY_FORCE_REWARM=1` restores
   the /cart reload + PUT); the dummy POST still refills the Shape ring.
4. `ident=<account>` appended to the `[FAST_LANE] chain done` / `ATC fetch`
   lines — per-identity ATC composition was unauditable from the run log.
5. fp-chromium A/B levers: `TARGET_FP_CHROMIUM_SKIP=<ids>` keeps named
   identities on real Chrome + their real profile (the 07-24..08-04 winning
   config) as the control arm; `TARGET_FP_SEED_SALT` rotates the engine-level
   device per salt (empty = bit-identical 08-11 seeds). Bat: SKIP=business,
   salt empty (one variable per identity). preflight_fp_drop.py now prints the
   per-identity plan and checks the new flags. tests: test_atc_gate_breaker
   +4 (edge-neutral, opt-in, TTL, source contract), test_fp_chromium +2 sections.
**Confidence**: high on the audit (3-agent forensic census with line refs
across 3 nights; totals reconcile: 254 main-tab POSTs = 90+61+61+42); high on
fixes 1-4 as removals of proven-useless work; medium on the A/B (one live
edge decides it — judge by per-identity composition, not orders alone).
**Outcome**: Pending the next live edge. If a regular SKU is armed it should
convert as before; on a hyped SKU the bot now keeps the full ~27 shots/min
through the window. Still open: the sentinel's in-run credential relogin
relaunches under fp-chromium on the home IP (failed 4/4 today) — hand-logins
remain mandatory; and whether the 429e-dominated edge on hyped SKUs is
beatable at all from this setup.
**Verification (2026-08-22, 7-agent adversarial + online-research workflow)**:
The direction of all 5 fixes held up. Online corroboration (5 independent bot
codebases — ZynBot/Destiny AIO/AndAIO + Refract + a 2017 Habr thread): HTTP 401
`{errorCode:T83072242, errorKey:_ERR_AUTH_DENIED}` is a Shape SIGNATURE
rejection ("Shape Block"), not an expired login / dead write token / qty limit;
the universal handling is "burn the Shape header set, get a fresh one, retry on
a fixed error delay" — nobody refreshes the OAuth token, re-scopes cookies, or
navs /account on a 401. That directly validates C2 (ladder off) and C3 (no
/cart reload between retries; the human playbook is "skip the cart page, spam
/checkout"). Empty-body 429 = a high-demand throttle everyone hits ("80% blocks
on a restock is normal" — Refract), validating C1's edge-neutral default.
Follow-up fixes applied from the review (flag-gated, tests green 64/64 breaker,
55/55 fp, 37/1 preflight):
  6. **Chrome major 150→151** (account_identity.py `_CHROME_BUILDS`): the host
     auto-updated to Chrome 151.0.7922.173 on 08-20, so every 08-21 login AND
     the business real-Chrome purchase arm presented a Chrome/150 UA on a 151
     engine — the exact UA-vs-engine incoherence that Shape-blocked logins on
     06-24, live during the drop. preflight now FAILs on any such drift
     (`account_identity UA major != installed Chrome`). Re-run hand_login_all.bat
     so jars are minted under 151.
  7. **Dead-token mid-window repair** (`TARGET_ATC_DEAD_TOKEN_MIDWINDOW_REPAIR=1`
     default): with the ladder off, a genuinely dead write token (the 07-07
     mode) had no in-window repair. The warmup heartbeat's existing
     confirmed-dead repair (dummy POST 401 → re-probe 401 →
     ensure_fresh_access_token on the SEPARATE warmup tab) is now allowed during
     a window too, still 300s-throttled and still requiring a CONFIRMED dead
     write-auth — so it is INERT on the 08-21 pattern (heartbeat 424 all night)
     and only fires on a real dead token.
  8. C2 comment corrected to cite the dummy-POST 424 heartbeat (not the
     read-only cart GET 200) as the write-auth liveness proof.
  9. login_profile_dir now receives account_id at all 3 login sites (latent:
     only bit if login-fp were enabled with SKIP).
Explicitly NOT changed (judgment calls, documented): the FAST_SELLING in-place
re-shoot still force-warms /cart (proven-converting on 08-04, out of scope);
business kept as the real-Chrome control though it is the noisiest signal
(needs a hand-login anyway; the Chrome-151 fix makes the control coherent).
Open recommendation for the next audit: capture the `Tgt-Cart-Error-Key`
RESPONSE header on ATC 429s via the CDP interceptor (page JS can't read it —
CORS) so empty-429 "high-demand vs bot-block" is decidable, not inferred; and
judge the fp A/B only on a REGULAR-SKU night (hyped-SKU nights are a null test).

---

### [2026-08-18] - 0-for on the 30th Anniversary restock: total ATC denial wall (85% global demand lottery) + LEVEL_REARM deadlock after emergency reset - TARGET
**Symptom**: Rolling 30th-anniv restock (~10 in-stock windows 01:30–05:00,
1010892xxx + 1011209279). 0 orders. The bot itself ran FLAWLESSLY — zero
process crashes (first crash-free hot drop; the f51ec80f tee fix held), fresh
hand-logins 2.5h pre-window, boot relogin VERIFY-only 3/3, fp-chromium live
on all 3 accounts, breaker + cart-hold + faulthandler all active. Yet 189
real ATC shots = 0×2xx: 160 empty-body 429 (edge/Shape-layer drop BEFORE the
carts app answers), 1 DCO_RATE_LIMITED 429, 28 hard 401 _ERR_AUTH_DENIED.
pre_checkout/place-order never fired once. Separately, 1011209279 sat ~8 min
in live stock with ZERO shots after 04:40:17.
**Root Cause**: (0-for) NOT a bot bug. Community intel (r/PokemonDeals, TYPA
live feed) confirms the wave was real and humans DID convert, but even humans
saw ~99% ATC denial — the dominant empty-body 429 is a GLOBAL demand lottery
at the edge; the minority 401 share is OUR per-identity hardening (rises with
per-TCIN hammering depth, resets on fresh TCINs — classic Device ID+
tracking). Our breaker throttles shots to protect the 15% problem while
sacrificing tickets in the 85% lottery. (deadlock) The manager's emergency
reset ("IN STOCK but still has completed status - reset failed!",
bulletproof_purchase_manager.py:2398) flipped a continuously-in-stock
'failed' item to bare 'ready' AND consumed the driving event; app.py's level
re-arm is failed-only and the edge publisher needs an OOS→IS flip that never
comes for continuously-in-stock items → orphaned mid-window.
**Fix Applied**: (deadlock) emergency reset now stamps a `rearm_hint_ts`
breadcrumb; `_build_level_rearm_map` re-arms in-stock 'ready' items carrying
a fresh hint (TTL = stale_after_s, 90s), riding the same money-safe
machinery as 'failed' re-arms. Kill-switch `TARGET_REARM_AFTER_EMERGENCY_RESET=0`
(stops stamping → branch inert). test_level_rearm_smoke 12/12 (+5).
(observability) `_check_cart_hold` was silent-on-miss — a zero-[CART_HOLD]
night couldn't distinguish "ran + empty" / "read also walled" / "never ran".
Miss now logged (200-with-TCIN-absent vs non-200 read), kill-switch
`TARGET_CART_HOLD_VERBOSE=0`. test_cart_hold_check 17/17. Breaker
aggressiveness (streak 8 / cooldown 120s vs looser) left as an operator
decision — flags `TARGET_ATC_GATE_STREAK_LIMIT` / `TARGET_ATC_GATE_COOLDOWN_S`.
**Confidence**: high on the audit (6-agent forensic census, totals reconcile
3 ways); high on the deadlock fix mechanism; the lottery-vs-hardening split
is inference from status/body signatures + community reports (medium).
**Outcome**: Deadlock can't orphan a mid-window TCIN again; next drop's
cart-hold audit will be conclusive. Strategy question (ticket count into the
global lottery vs identity protection) still open — 3 breaker-era hot-SKU
drops = 0 wins, all pre-breaker regular-SKU drops converted.

---

### [2026-08-16] - faulthandler dumps would MISS the run log (fd bound to console) - TARGET
**Symptom**: None yet — caught in the 08-16 pre-restock logging audit before
it could bite. The f51ec80f forensics (`faulthandler.enable()` at app.py top)
claimed "the restart wrapper redirects stderr into logs/runs/run_*.log"; the
wrapper does NOT redirect anything (`"%PYTHON%" app.py`, bare), and the run
log is an in-process Python-level `_Tee` swap of `sys.stdout/stderr`.
**Root Cause**: faulthandler captures the raw file DESCRIPTOR (fd 2 = the
console) at `enable()` time and writes dumps with raw fd syscalls — the
Python-level `_Tee` is invisible to it. On the next 0xC0000005 the all-thread
stack dump would print only to the console window, which dies with the crash/
restart — i.e. the exact forensics added for the next native crash would be
lost. Proven with a live AV repro (`faulthandler._sigsegv()` child mimicking
app.py's enable→tee sequence): dump absent from the tee'd log, present only
on captured console stderr; with the rebind, dump lands in the log file.
**Fix Applied**: `setup_run_logging()` re-binds faulthandler to the run file
right after the `_Tee` swap: `faulthandler.enable(file=run_fh,
all_threads=True)` (same `TARGET_FAULTHANDLER=0` kill-switch; `run_fh` lives
for the process lifetime, which faulthandler requires). Top-of-module
enable() stays as boot-phase coverage; its comment corrected. Trade-off:
after rebind the dump goes to the file, not the console — correct priority,
the console dies with the crash anyway.
**Confidence**: high (mechanism proven both ways with a real AV child)
**Outcome**: Next native crash names its exact Python lines in
`logs/runs/run_<ts>.log`. Pre-Tuesday audit otherwise ALL GREEN (preflight
29/29, egress 3/3, 12 suites, states clean, no blind TCINs).

---

### [2026-08-14] - 0-for on the 30th Anniversary restock: purchase-path native crash (tee close-during-write) + total ATC 429 wall - TARGET
**Symptom**: Huge rolling Pokémon 30th Anniversary restock (windows 02:06,
02:12, 03:17, 03:35, 03:40, 04:10, 04:42 — all 1010892xxx). 0 orders. app.py
died 4x with 0xC0000005 (02:07:56, 02:13:12, 03:36:29, 03:41:27 — Windows
Event 1000, python312.dll), each timestamp-matching a purchase log's final
write, each mid-purchase, costing ~4-5 min of blind reboot per kill and
killing 4 in-flight retry loops. Every fired ATC was gate-denied: ~130 shots
across 7 windows = 429 DCO_RATE_LIMITED ("high demand item") with sprinkled
401 _ERR_AUTH_DENIED, zero 2xx, all 3 accounts, while warmup dummy-POSTs
stayed 424 (write-auth alive). fp-chromium WAS live (3 distinct seeds,
JS spoof correctly skipped). Community reports say the drop was scuffed/buggy
Target-side.
**Root Cause**: (crash) `_PurchaseLogTee.close()/flush()` skipped `_lock`; on
the gate breaker's instant-bail races (3 racers, whole race ~5ms) the FIRST
finisher closed the buffered log file while other racers were mid-`write()`.
TextIOWrapper.close() frees C buffers and its flush syscall drops the GIL —
concurrent write touches freed memory → access violation no `except:` can
catch. Same mechanism killed 08-11's two "tee" crashes; the 08-11 `__dict__`
hardening fixed only the Python-level AttributeError symptom. (0-for) the
ATC demand/device gate denied every add on an ultra-hot scuffed drop —
NOT a token failure, NOT a crash consequence (the walls were total even in
windows we covered fully).
**Fix Applied**: `f51ec80f` — close()/flush() serialize on the write lock;
tee close + stdout restore moved to the LAST racer (`_do_resume` refcount),
which also un-truncates racer log tails; `faulthandler.enable()` in app.py
(kill-switch `TARGET_FAULTHANDLER=0`) so any future native fault prints all
thread stacks into `logs/runs/run_*.log`. Tests: tee 6/6 (2 new), breaker
40/40, race dispatch 4/4, wedge 17/17, level-rearm 8/8.
**Confidence**: high (crash mechanism + fix); medium (0-for attribution —
scuffed drop makes fp-chromium verdict INCONCLUSIVE; gate stayed soft-429
all night vs 08-07's hardening to pure 401, consistent with breaker+fp
reducing device-score burn but conversion unproven)
**Outcome**: Crash class closed; next normal-quality restock is the real
fp-chromium conversion test. Breaker verified working as designed (armed at
8-streak per identity, ~1 probe/120s). NOTE 08-14 12:14 `AUTH_CRITICAL`:
primary's session died post-drop, scripted relogin capped — hand-login
required before next window.

---

### [2026-07-31] - BEST NIGHT EVER (7 orders / 14 units) but late-window losses to server-side cart eviction + FS-hold abort; Endpoint 8 live-validated - TARGET
**Symptom**: 07-30→31 street-date drop (`logs/runs/package.log.2`, purchase logs
02:29-05:32). Fast lane converted 7-for-7 whenever the first place-order POST
was a 200 (all qty=2, 2.3-3.7s: 1011483406 ×2 W2, 95298172 ×2 W3/alt-1,
1011209273 W1, One Piece 95120836+95120832 W1). alt-1 — 0-for-3-nights on the
CVV challenge — converted TWICE via the in-lane pre-PUT CVV (`put=200`):
**Endpoint 8 is production-proven**. Losses: (a) street-date pair
95274164/95274160 lost to ATC walls (429-heavy early, then episodic
Shape-layer ATC-401 lockouts — 3 consecutive 401 `_ERR_AUTH_DENIED` on every
account incl. 100/100 at 03:11 and 57/57 at 05:30, while member tokens minted
fine); (b) late window 04:55-05:13: after each `424 RESERVATION_FAILURE`
Target EMPTIED the cart server-side (items → Saved-for-later), follow-up
place-order POSTs 400'd with NO `tgt-cart-error-key`, the checkout page
rendered "There are no items in your cart right now." (matched by NEITHER
phrase list) and the SPA bounced tabs to /cart — each attempt burned a DOM
click + 12s wait + screenshots (`place_order_timeout_20260731_*.png`);
(c) the 03:44 FS hold preserved the won cart 45s but the SPA bounced the tab
to /cart mid-hold, so the re-shoot loop's URL guard aborted 1/4 with ZERO
post-hold shots and the cart was cleared anyway; (d) the two ~22-min
street-date windows got ONE race + tail flickers each — resilient mode has no
periodic 'stock_updated' publisher, so a failed wave never re-races until the
next OOS→in-stock flip (17-18 min of live stock, zero shots). Also: FS
answered a first-shot POST at 04:55 on a TCIN the same account had bought 3
min earlier — the 07-21 "FS never answers shot #1" rule has a same-account
repeat-purchase exception.
**Root Cause**: (1) no evicted-cart detection on wire (400-no-key) or page
copy; (2) re-shoot URL guard treats an SPA bounce as terminal; (3) edge-only
stock publishing under USE_RESILIENT_STACK=1.
**Fix Applied**: evicted-cart fast-bail (`TARGET_EMPTY_CART_BAIL`, 4 sites in
`purchase_executor.py` + retryable DIAGNOSIS), one bounded re-nav to /checkout
in the re-shoot loop (`TARGET_RESHOOT_RENAV`), level re-arm publisher for
failed-but-still-stocked TCINs (`TARGET_LEVEL_REARM_S`, app.py). Tests:
test_empty_cart_bail_smoke (12), test_level_rearm_smoke (8).
**Confidence**: high (all three mechanisms log-proven)
**Outcome**: shipped 2026-08-02, unvalidated live. Watch next drop for
`[LEVEL_REARM]` re-races and `cart EVICTED` bails.

### [2026-08-02] - All 3 login-sessions died mid-68h-run; credential relogin Shape-burned (~0/25); auth ladder churned 576 Chrome restarts/day - TARGET
**Symptom**: post-drop audit of the same 68h run (07-31 00:49 → 08-02 20:37).
Login-session lifetimes ~32-46h (bat comment says "lasts days"): business died
07-31 10:17 (recovered once 16:27), alt-1 died 08-01 09:11 permanently,
primary 08-01 20:38 permanently, business again 08-02 14:26 — **from 08-02
14:26 to shutdown ZERO purchase-capable accounts** (all GUEST-only mints).
`relogin.log`: ~25 in-run credential relogins failed since 07-31 — "username
did NOT advance" ~80%, "STILL HAS SESSION → username field NOT found" ~20%,
one "password did NOT advance" — the classic Shape login-denial signature on
the HOME IP that historically passed (last clean logins 07-29). With relogin
capped, the sentinel ladder still ran nav-refresh → Chrome RESTART →
capped-no-op every 5 min per dead account: 113/258/576 restarts on
07-31/08-01/08-02 + 818 asyncio ConnectionResetError noise. The cloaking
alarm also fired every 30s for 62.5h post-drop (~67k extra origin fetches).
Detection layer stayed flawless throughout (0.122% 403, no missed restock).
**Root Cause**: (1) scripted login is Shape-denied — most consistent with
login-surface burn from drop-night volume + ladder hammering (25 attempts,
2 days, same device/IP); (2) ladder has no dead-session backoff; (3) alarm
has no confirmed-OOS backoff.
**Fix Applied**: dead-session park (`TARGET_SENTINEL_DEAD_SESSION_BACKOFF_S`,
default 1800s — parked accounts run only the rung-0 token check and self-clear
on recovery; test_dead_session_park_smoke 9/9); cloak-verify backoff 60→300s
on confirmed all-OOS (`TARGET_CLOAK_VERIFY_BACKOFF`). Relogin itself NOT
fixed (needs live login-page work).
**Confidence**: high on the churn fixes; medium on the burn theory
**Outcome**: **OPEN — before the next drop the operator must recover all 3
sessions manually: `set RELOGIN_DEADMAN_S=0` then
`python relogin_one.py all --manual` (hand-login per profile, saves jars),
then `python check_session_readiness.py` for 3/3 MEMBER.**

### [2026-07-24] - First fast-lane conversions (4 orders / 8 units); alt-1 0-for on a latched DOM chain; NEW "High-demand item" modal unrecognized - TARGET
**Symptom**: `logs/runs/run_20260723_235455.log` (07-23 23:54 → 07-24 09:48, 8
restock windows). The fast lane went **4-for-4 on place-order whenever ATC
201'd**: 02:24 W2/business + W1/primary Greninja (1011209273) qty2 each at
3.13s/3.59s, 03:47 W1 Pitch Black ETB (1011483406) qty2 at 3.50s, 03:58 W1
Greninja qty2 at 2.72s — all pure API, no nav. alt-1 went 0-for-the-night: its
02:04:50 fast-lane place-order got `400 MISSING_CREDIT_CARD_CVV` (the ONLY CVV
rejection all night — the challenge is **account-scoped to alt-1**, primary and
business placed 4 clean orders) → latched → every later shot went DOM-first →
dead-ended 3-for-3 (>140s impl hang 02:05-02:07; skeleton /checkout 02:24; /cart
nav status=0 03:18). At 02:24:33 the skeleton /checkout showed a NEW throttle
modal — **"High-demand item in your cart / …causing a delay. We're managing high
traffic right now. Please try again."**
(`logs/checkout_no_place_order_20260724_022433.png`) — matched by NEITHER
busy-phrase list → `DIAGNOSIS: Unknown failure` → `checkout_navigation_failed`
(terminal) → cart cleared 32s in, while the item stayed in stock ~18 more
minutes. Plausibly a missed third Greninja order.

**Root Cause**: (1) both busy/demand-throttle phrase lists predate this modal
variant; (2) the CVV latch had no realistic exit while latched — the 07-23
auto-unlatch requires a DOM-confirmed order, which the throttled DOM path never
produces (chicken-and-egg).

**Fix Applied**: added `'high-demand item'` / `'causing a delay'` /
`'managing high traffic'` to BOTH lists (JS `BUSY_PHRASES` in
`_handle_busy_modal` and the page-text diagnosis list,
`src/session/purchase_executor.py`) so this page state classifies
`checkout_busy_retryable` — pre-submit only; the `_api_order_id`
belt-and-suspenders still blocks any post-submit retry. Deleted
`state/cvv_challenge_alt-1.flag` so alt-1 rejoins the fast lane next run; if
Target still challenges the card, the header classifier re-latches after one
~0.4s 400 (bounded cost). Tests: new
`tests/test_high_demand_modal_phrases.py` 3/3 (extracts both source lists,
asserts the observed 07-24 modal copy AND the classic 06-30 busy copy match);
full executor cluster green 105/105.
**Confidence**: high on both.
**Outcome**: pending next restock.

### [2026-07-23] - Chromes wedge on a rigid ~70-75 min per-launch clock; the 07-20 "false wedge" verdict was wrong - TARGET
**Symptom**: `logs/runs/run_20260721_235217.log` (07-21 23:52 → 07-22 07:49, zero
restocks): 22 sentinel restart escalations. Primary's CDP went dead at 01:04 / 02:19 /
03:34 / 04:49 / 06:04 / 07:19 — **exactly 75 min apart** — with alt-1+business
following ~5 min behind each time. Onset is visible ~2 min before each escalation
(WATCHDOG cookie check TIMED OUT >45s at 02:17:41, then every 2s/6s TAB_HEALTH probe
fails for 2.5+ min until the restart). Re-basing each episode on the account's own
last (re)launch shows every Chrome wedges ~70-75 min after **its own launch** — the
clocks are per-Chrome and reset on relaunch. The 07-19 night's 23 destroys fit the
same ~70-min rate.

**What this overturns**: the 07-20 entry called these *false* wedges (transient CDP
backpressure) and shipped the 6s slow re-probe. That fix saved **0 of 80** double
timeouts this run (destroy count unchanged, 22 vs 23) because the socket is genuinely
dead for minutes, not busy for seconds. Cost per episode: 3-6 min during which that
account cannot buy a restock — and alt-1+business wedge *together* (their launch
clocks are synchronized), so 2 of 3 accounts go dark simultaneously every ~75 min.

**Root Cause** (candidate, instrumented to confirm): the fetch interceptor's dedup
early-return in `purchase_executor.py _on_request_paused` dropped events whose
(request_id, stage) key was already in the shared `_cdp_continued_ids` set —
**without sending Fetch.continueRequest**. Two ways a live request hits that path:
(1) CDP re-pauses every redirect hop under the SAME request id; (2) the warmup tabs'
`interception-job-N` ids collide across tabs in the shared set. Each hit = a request
paused forever inside Chrome. Leaked pauses accumulate from launch at the page's
natural request rate → fixed time-to-wedge, matching the rigid per-launch clock.

**Fix Applied**: dedup-hit events are still `continue_request`-ed (processing stays
suppressed — no double Shape cache/ring pushes; release-only, double-buy-safe since
continueRequest can only release a paused request, never re-send one) and counted:
watch for `[INTERCEPTOR:*] dedup hit #N` in the next overnight log. Counter climbing
AND wedges gone = confirmed. Counter ~0 AND wedges persist = theory dead, look
elsewhere (next suspects: unthrottled /cart pages leaking renderer memory under the
anti-idle flags). Kill-switch `TARGET_CDP_DEDUP_CONTINUE=0`.
Tests: `tests/test_cdp_dedup_leak_guard.py` 7/7; all five prior suites still green
(37/37, 16/16, 9/9, 5/5, 17/17).
**Confidence**: high that the leak is real and the fix is safe; medium that it is
THE wedge cause (instrumentation decides).
**Outcome** (2026-07-24 overnight, `run_20260723_235455.log`): instrumentation
answered — dedup counters CLIMBED (#100+ on one account, #50+ on two others, 19
logged milestones) **and the wedge clock persisted**: 20 `escalating to restart`
hard kills on the same ~70-75 min per-launch cadence, each one a genuine >6s
re-probe failure, plus 2 `purchase_impl_hang` (>140s) during live stock windows.
The leak was real and is now plugged, but it was NOT the wedge cause. Per the
decision tree above: next suspect = unthrottled /cart warmup pages leaking
renderer memory under the anti-idle flags.

### [2026-07-21 PM] - CORRECTION to the entry below: timing WAS the problem, and FAST_SELLING was self-inflicted - TARGET
**Symptom**: Same run (`logs/runs/run_20260720_231634.log`). Re-read of the raw log
overturns two conclusions in the 07-21 AM entry below.

**Correction 1 — the checkout POSTs were ordered, and the order is the story.**
Reconstructing every checkout POST in sequence per wave:

| Wave | #1 | #2 | #3+ |
|---|---|---|---|
| W2 1011483413 | RESERVATION_FAILURE | FAST_SELLING | FAST_SELLING ×4 |
| W5 95267143 | MISSING_CREDIT_CARD_CVV | RESERVATION_FAILURE ×3 | FAST_SELLING ×5 |
| W6 95267143 | MISSING_CREDIT_CARD_CVV | RESERVATION_FAILURE | — |
| W8 1011483413 | MISSING_CREDIT_CARD_CVV | RESERVATION_FAILURE | FAST_SELLING ×6 |
| W9 95267143 | MISSING_CREDIT_CARD_CVV | RESERVATION_FAILURE | http_400 |

`FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION` **never once answered the first shot of a
wave.** It is not an external wall we need more accounts to get around — it is our own
retry storm. It answered 0 of ~20 re-shoots, and two minutes of quiet cleared it (W6 at
03:28 got a non-throttled first shot after W5 at 03:26 tripped it). The 07-17 in-place
re-shoot loop was feeding the limiter that was blocking it. **This retires "top lever =
more accounts/IPs" as the read on FAST_SELLING.**

**Correction 2 — timing was the problem.** The AM entry measured detection→place-order
(~3.0s) and called it fast enough. The number that matters is **ATC-201 → first
place-order POST**, and W2 settles it with no CVV involved anywhere:

```
15895  ATC fetch status: 201 (t=0.92s)
15901  pre_checkout fired (fire-and-forget)
15920  Checkout nav done (t+1.452s)          <-- tab.get("/checkout/start")
15927  Checkout page ready (place_order) in 0.87s   <-- DOM poll
15931  CHECKOUT_TRANSITION t=3.3s
15938  API_PLACE_ORDER Firing checkout POST
15964  HTTP 429 RESERVATION_FAILURE
```

A clean, CVV-free first shot lost the reservation because **2.5 seconds of pure page
navigation sat between a successful ATC and the place-order POST**. Every wave paid it:
`CHECKOUT_TRANSITION` measured 2.4 / 2.8 / 3.0 / 3.3 / 3.6 / 3.9 / 4.1 / 5.0 / 10.1s.

**Root Cause**: The `/checkout/start` navigation was never load-bearing for the API
place-order. It existed so the cart hydrates server-side first — and that hydration *is*
`pre_checkout`, which the code fired **fire-and-forget** immediately before navigating.
The 2026-05-07 attempt to drop the nav failed with `424 CART_COMPARISION_FAILURE_ERROR`
only because it raced that un-awaited pre_checkout. Awaiting it removes the race by
construction, which leaves the nav with nothing to do.

**Fix Applied**:
- **`_api_fast_lane`** (`src/session/purchase_executor.py`) — ATC → **awaited**
  pre_checkout → place-order as ONE `tab.evaluate` fetch chain. No navigation, no CDP
  round-trip between steps: **~0.85s** trigger→committed order vs ~3.4s. Flag
  `TARGET_FAST_LANE=1` (default on; `=0` restores the old nav path exactly).
- Double-buy safety: a 2xx is a committed order (never re-shot, never reported as
  failure even if the body won't parse); a fired-but-no-response POST bails terminal and
  non-retryable; `_fastlane_placed` short-circuits the checkout+payment phases so no
  second place-order POST can fire; the flag resets per purchase.
- The chain refuses to fire the place-order POST unless ATC returned 201, pre_checkout
  returned 2xx, **and every cart item is our TCIN** (place-order buys the whole cart —
  this guard is new, the old path only logged a warning).
- On `FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION`: stop re-shooting and back off
  `TARGET_FAST_SELLING_COOLDOWN_S=45`. Other 429/424 keep the 07-17 behaviour.
- Fast lane is skipped while the CVV latch is on (that shot is a guaranteed 400) and
  during the fast-selling cooldown.
- The chain also returns `pre.payment_instructions` id/type/cvv flags — the prerequisite
  for moving CVV onto the API path (Endpoint 8) without waiting for a capture run.

**Verification**: `tests/test_fast_lane_checkout.py` 37/37 — including six tests that
extract the executor's **real** fetch-chain JS and run it under node with a stubbed
`fetch`, asserting the exact sequence of URLs requested (proving no place-order POST
fires on a bad ATC, an un-hydrated cart, or a foreign cart item). Pre-existing suites
still green: `test_checkout_inplace_reshoot` 16/16, `test_cvv_challenge_classify` 9/9,
`test_warmup_cart_nav_guard` 5/5, `test_wedge_recovery_smoke` 17/17.

**Outcome**: NOT yet validated on a live drop.

> **⚠ SELF-CORRECTION — the funnel A/B that re-ranks this entry.** After shipping the
> above I ran the comparison I should have run first: the full funnel of the 07-14 WIN
> night against this one, same code.
>
> | | 07-14 **WIN** | 07-21 **LOSS** |
> |---|---|---|
> | ATC fetches fired | 532 | 497 |
> | ATC 201 (carts) | **2** | **9** |
> | ATC 401 `_ERR_AUTH_DENIED` | 291 | 294 |
> | ATC 429 DCO_RATE_LIMITED | 235 | 193 |
> | place-order shots | 3 | 19 |
> | place-order **HTTP 200** | **1** | **0** |
> | CVV 400s | **0** | **4** |
> | `CHECKOUT_TRANSITION` ATC→shot | **3.4s** | 3.3s |
>
> This kills three theories, including one of mine:
> 1. The **~98% ATC failure rate is the normal steady state**, not a regression — the
>    401/429 counts are near-identical on the night we won. Not the bottleneck.
> 2. **Carts were never the constraint.** This night won 4.5× more carts than the
>    winning night and converted none.
> 3. **3.4s ATC→place-order WON on 07-14**, so the nav latency this entry blames is
>    survivable and is *not* proven causal. The fast lane is a genuine improvement
>    (more shots inside whatever window exists, and a 75% smaller window for a
>    background `/cart` ADDRESSES write to land in) but it is a **secondary** fix.
>
> The one variable that actually changed is the CVV challenge — see the entry below,
> whose core finding stands. **And the fast lane skips itself while the CVV latch is
> on, so on a CVV-challenged drop it does nothing.** Highest-value next work is
> Endpoint 8 (CVV on the API path), which needs one real cheap-item checkout to
> capture the `PUT payment_instructions/{id}` body.

---

### [2026-07-21] - 0-for-9 on NINE clean ATC 201s — Target began challenging the saved card's CVV - TARGET
> **Superseded in part by the 2026-07-21 PM entry above**: "Timing was never the
> problem" is wrong (ATC→shot#1 was 2.4-5.0s of page-nav dead time), and the
> FAST_SELLING tally below counts a self-inflicted retry storm, not 16 independent walls.
**Symptom**: Overnight 07-20→21 the bot detected **9/9 restocks** across 5 TCINs
(02:53–04:48) and fired a purchase within seconds of every one. Add-to-cart was the
**best of any night on record — 9 × HTTP 201** (the 07-14 night that actually converted
got only 2). Zero units bought. `logs/purchases/purchase_*_20260721_*.log`.

**Root Cause**: Every place-order POST that reached Target was rejected, and the
dominant new rejection was `HTTP 400` with **`tgt-cart-error-key: MISSING_CREDIT_CARD_CVV`**.
Three compounding defects:
1. **The API place-order body carries no CVV.** `_api_place_order` posts only
   `{cart_type:'REGULAR', channel_id:'10'}`. Once Target challenges the card, that shot
   is a *guaranteed* 400 — the fast path can never win.
2. **The rejection was invisible to the classifier.** The error key lives in the
   *response header*, but classification only read the *body* (which is empty here), so
   it was labelled generic `http_400` and dropped into the slow DOM path blind.
3. **The wasted ~1.0s killed the reservation.** 5-for-5, the DOM recovery that followed
   a CVV 400 then got `424 RESERVATION_FAILURE`. Worse, RESERVATION_FAILURE renders as
   Target's generic "busy" copy, so `_handle_busy_modal` claimed it and re-clicked Place
   Order into a dead reservation (15 wasted re-clicks × ~1.5s across the night).

Error-key tally for the night: 16 `FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION` (429),
8 `RESERVATION_FAILURE` (424), 5 `MISSING_CREDIT_CARD_CVV` (400). The CVV key is
**escalating** — 1 log on 06-30, 07-07 and 07-17 each, then **4 logs on 07-21**.

**Decisive comparison** — same account (primary), same TCIN (95267143), same code path:

| | 07-14 (the win) | 07-21 (0-for-9) |
|---|---|---|
| ATC | 201 @ t=0.45s | 201 @ t=0.83s |
| Place-order | **HTTP 200 → order placed** | **HTTP 400 MISSING_CREDIT_CARD_CVV** |

Timing was never the problem: place-order fired at **t≈3.0s** from detection. The bot
was fast enough; it spent its one good shot on a request Target was always going to refuse.

**Fix Applied**:
- Classify from the `tgt-cart-error-key` **header** (with a staleness guard — the field
  is reset per-purchase, not per-shot, and the in-place re-shoot loop reuses it) →
  new `cvv_required` reason. `src/session/purchase_executor.py`
- Latch `_cvv_required` on first challenge and **persist** it to
  `state/cvv_challenge_<account>.flag`, so the first place-order after the nightly
  restart already skips the doomed API shot. Env override `TARGET_CVV_REQUIRED=1/0`.
- When latched, skip the API shot and go **DOM-first** — the CVV modal handler answers
  in ~0.08s. Kill-switch `TARGET_CVV_DOM_FIRST=0`.
- Bail immediately on a wire-level `RESERVATION_FAILURE` instead of re-clicking Place
  Order into a dead reservation, so the manager can re-race ATC inside the stock window.
  Checked *before* the CVV branch (which `continue`s). Kill-switch `TARGET_RESERVATION_BAIL=0`.
- **Root-cause candidate (HYPOTHESIS, not yet confirmed):** Target's help article states
  CVV re-entry is required when *"a shipping address is updated during checkout"*. Loading
  `/cart` makes Target's own JS fire `PUT /web_checkouts/v1/cart?...&field_groups=ADDRESSES...`
  on this account's cart — and a **background warmup `/cart` nav landed inside the checkout
  window, on the same session, immediately before every CVV 400**. Warmup tabs now skip only
  the *navigation* while a purchase is live (the dummy POST still fires, so the Shape ring
  keeps refilling; `force_fresh` ATC-401 recovery still navigates).
  Kill-switch `TARGET_WARMUP_PAUSE_DURING_PURCHASE=0`.
- Enabled passive capture of Target's own CVV submit
  (`PUT /checkout_payments/v1/payment_instructions/{id}`) and the cart PUT body via
  `TARGET_API_CAPTURE_CHECKOUT_STEPS=true` → `logs/api_capture.log`. This both **confirms
  or refutes the address hypothesis** and yields the body shape needed to move CVV onto
  the API fast path (Endpoint 8, deferred since 05-06 because the challenge never fired).

**Confidence**: **high** on the diagnosis (the error key is explicit in the logs and the
07-14 vs 07-21 A/B is clean). **high** on the CVV latch / reservation-bail fixes.
**medium-low** on the warmup-nav *root cause* — it fits both the documented trigger and the
log ordering, but is unconfirmed until the captured cart-PUT body is inspected.

**Outcome**: Fixes shipped + tested (`tests/test_checkout_inplace_reshoot.py` 16/16,
`tests/test_cvv_challenge_classify.py` 9/9, `tests/test_warmup_cart_nav_guard.py` 5/5).
**Not yet validated against a live drop.**
**Root Fix Still Needed**: (a) confirm/refute the address-write hypothesis from the
captured cart PUT body; (b) if CVV persists, fold the CVV PUT into the pre-checkout
warmup so place-order never 400s; (c) `FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION` (16×,
the largest single bucket) remains unsolved — top lever is still more accounts/IPs.

### [2026-07-13 PM] - Host froze AGAIN on the new GPU driver — full 60-day hardware triage - HOST
**Symptom**: 17.4 min after the deliberate 23:04 reboot (NVIDIA 610.74 freshly active, light
load), the machine hard-stopped at **23:23:49** — ~34 s after a Kernel-Power 566
`SessionUnlock` transition, with zero precursor events (no nvlddmkm, WHEA, disk, thermal).
Kernel-Power 41 with `BugcheckCode=0`, no dump. The GPU-driver update is thereby
**empirically disproven as the fix** for host instability.
**Root Cause (triage verdict, read-only 60-day sweep)**: 36 dirty shutdowns in 60 days
(≈0.6/day), split 14× bugcheck 0x116 `nvlddmkm` (all param3 `0xC000009A` reset-failure,
05-21→07-13, on the OLD driver) + 22× no-bugcheck hard stops. Uptime-before-crash is
**memoryless** (17 min→6.4 d, median 19.5 h — no thermal/creep clustering); 7 crashes hit
01:00–08:30 at near-idle; 3 documented deaths began the same second as an idle→active
session/input transition. Zero WHEA machine-checks in 60 days — the hardware dies silently.
Ranked hypotheses:
1. **Degraded 13900K (Raptor Lake Vmin-shift defect) — HIGH (~60%).** CPU has run its whole
   life on microcode **0x11F** (BIOS 1.90, 2023-10-25, MSI MPG Z790 CARBON WIFI) — predates
   every Intel mitigation (0x125/0x129/0x12B/0x12F), on MSI's unlimited power defaults +
   "Ultimate Performance" plan. Textbook symptoms: recurring 0x116-at-idle, random no-dump
   freezes, transition-triggered deaths, silent (no MCE) escalation.
2. **4-DIMM DDR5 XMP beyond IMC spec — MED-HIGH (~50% contributing).** 4×16 GB Corsair
   CMT32GX5M2B5600C36 (two mixed 2×16 kits) at 5600 MT/s 1.25 V vs Intel's validated
   DDR5-4400 for 2DPC on a 13900K. Memoryless crash pattern = classic unstable-RAM print.
3. PSU/power transient — LOW-MED (~15-20%). 4. RTX 4090 itself — LOW (~10%; 17 crashes have
   no GPU precursor at all). 5. Thermals — VERY LOW. 6. Board/storage — VERY LOW.
**Fix Applied**: none possible in software. **Owner action checklist (discriminating order)**:
(1) **BIOS 1.90 → latest** (carries 0x12B/0x12F microcode + Intel Default limits — mandatory,
stops further CPU damage); (2) **disable XMP** (JEDEC ~4400) and observe ≥1 week — crashes
stop ⇒ RAM OC was the trigger, continue ⇒ CPU; (3) MemTest86 overnight ≥4 passes (XMP on,
then off if erroring); (4) if still crashing on new BIOS + JEDEC → **RMA the 13900K** (Intel
extended RPL warranty to 5 yr; interim: Intel Default profile / cap boost ~5.5 GHz);
(5) PSU pass (reseat 12VHPWR/EPS, swap-test) only if 1-4 don't resolve; (6) hygiene:
Voicemod `vgm.exe` crashed 60×/60 d, MSI Center ships the low-trust NTIOLib driver.
**Ops implication**: 5 of 5 unattended no-dump crashes FROZE (6-12 h dead until human reset)
— auto-logon/Startup-lnk cannot cover a hard freeze; until the hardware is fixed, any
unattended window can silently end a run. ~34 crashes/60 d corrected to exactly 36.
**Confidence**: high on the evidence (event-log census), medium on ranking (1 vs 2 needs the
XMP-off discriminator week)
**Outcome**: OPEN — top drop-night risk. Same-night context: the bot side is fully green
(auto-start chain validated twice this night; saved cards verified 3/3 — see commit a3273969).

### [2026-07-13] - Host GPU BSOD killed a healthy overnight run (9 h blind window) - Target
**Symptom**: Overnight wrapper run (started 07-12 23:40) monitored cleanly for 11.4 h —
122,556 sweeps @ 2.98/s, 98.7% HTTP 200, 403=12, `in_stock=[]` on all 498 ground-truth
probes (no restock missed while up). At ~11:08 on 07-13 requests began mass-failing
("other" +30 per 30 s window); watchdog logged "CDP websocket likely wedged" at 11:10:45;
machine died ~11:10:46.
**Root Cause**: NOT the bot. Windows bugcheck **0x116 VIDEO_TDR_FAILURE in `nvlddmkm.sys`**
(NVIDIA driver 32.0.16.1047, 2026-05-18; dump `C:\WINDOWS\Minidump\071326-19625-01.dmp`).
Second dirty shutdown in 2 days (07-12 15:11 left no bugcheck record — freeze/power-style).
The "CDP wedge" alarm was Chrome dying under the failing GPU driver — distinct from the
recurring 75-min/00:21 wedge. (Data point for that open mystery: W32Time stepped the clock
-3.7 s at exactly 00:21:00 this night and the run sailed through — time-sync alone is NOT
the wedge trigger.)
**Recovery gap**: machine auto-rebooted at 11:11 but sat at the Windows LOGIN screen ~9 h
(bot blind 11:10→20:16) — `TargetBot.lnk` in the Startup folder only fires at LOGON. At the
20:16 logon the full chain recovered unattended and green in ~60 s: relogin 3/3 → forwarder
pool LIVE (3 BD exits) → 3/3 workers in 4.3 s → write-auth 424 on all 3 accounts → sentinel
up. It then received 2× Ctrl+C + console close at 20:17:36-37 (operator shutdown, not a
defect).
**Fix Applied**: none in code — nothing bot-side to fix. Operational: (1) update /
clean-reinstall the NVIDIA driver; (2) consider Windows auto-logon so BSOD → reboot →
logon → Startup-lnk revives the bot with zero humans (every other link is now proven);
(3) forensics note: `logs/runs/package.log` is the cross-run source of truth — the per-run
tee (`run_20260712_234004.log`) froze at 11 KB at boot while package.log captured the whole
night.
**Confidence**: high (WER bugcheck 1001 + Kernel-Power 41 + package.log timeline all agree)
**Outcome**: CLOSED as a driver issue 2026-07-13 PM — driver updated to 610.74 same day, but
the machine froze again 17 min into the first post-update boot. Superseded by the
[2026-07-13 PM] hardware-triage entry above: the 0x116s were a symptom (14 identical since
05-21), the suspected root is CPU/RAM, not the driver. Bot-side validation stands.

### [2026-07-10] - ATC-401 root cause RESOLVED (write-auth, not Shape) + pre-drop launch - Target
**Context**: Pre-drop investigation via live experiments (`scratchpad/diag_shape_vs_token.py`
on home-IP and BD-forwarder paths; `scratchpad/check_tokens.py` offline JWT decode) plus a
45-file correlation sweep of `logs/purchases/*.log`. Two load-bearing beliefs were overturned
— recorded here so they are not re-litigated.
**Finding 1 — the ATC 401 `_ERR_AUTH_DENIED` (T83072242) is a WRITE-AUTH/token problem, NOT
Shape headers.** Shape re-warm cured **0 of 692** ATC 401s on 07-07; 96% of 401s already used
<10 s Shape captures. Decisive live proof: a page-context `fetch()` with deliberately GARBAGE
`X-GyJwza5Z-*` values STILL passed the gate (HTTP 424) — Shape's own JS re-signs every fetch
before it leaves Chrome, so the caller's Shape header values never reach the wire. The
`_shape_capture_ring` / `_cached_cart_headers` replay machinery is therefore **inert** (no harm,
no benefit). Do not attribute a 401 to Shape headers again.
**Finding 2 — "token churn" = a degraded login-session re-minting GUEST tokens.** Live: a ~13 h
old saved session re-minted a fresh (24 h TTL) but **member=False guest** token; `ensure_fresh_
access_token(force=True)` could not upgrade it. The keep-fresh mint rungs cannot recover a
guest/dead login-session — **only a credential relogin can** (matches the 07-07 wall). Fresh
`relogin_one.py all` restored all 3 accounts to member (239 m TTL, verified by decoding the
saved accessToken JWT). Suspected churn accelerant: a personal-profile Chrome logged into
Target (Target keeps one live accessToken per login-session, so two contexts evict each other).
**Finding 3 — BD purchase IPs are alive; the ATC path works.** Forwarder-bound to
31.105.133.83, page loaded, Shape live, ATC-shaped POST returned 424 (passed Shape+auth). A
real in-stock TCIN returns 201.
**Testing gotcha**: standalone scripts that call `pool.ensure_all_ready()` directly pass Chrome
the raw credentialed BD URL → Chrome 407 → `chrome-error://` → Shape never inits → HTTP 0
"Failed to fetch" (false "dead IP / write-auth dead"). Only
`BulletproofPurchaseManager._setup_purchase_forwarders()` starts the local CONNECT forwarder +
rewrites proxy_url to 127.0.0.1:port. Replicate it, or test on the home IP (proxy_url=None).
**Fix Applied**: NONE to the purchase/Shape/token code — deliberately did not touch the working
path hours before a drop. Fix was operational: fresh relogin (member tokens) + launch via
`run_bot_with_nightly_restart.bat`. Kept the uncommitted 07-08 churn-alarm + canary-retire
changes.
**Outcome**: Bot launched 01:29 and verified drop-ready — relogin 3/3 member; monitoring
2.97/s, 200=533/534 (99.8%), 403=0, 16 sessions; forwarder isolation ENGAGED (3 distinct BD
exits); all workers write-auth 424 alive with 401→self-heal working. Wrapper stdout captured to
`logs/wrapper_run_20260710.log` (launched the batch under a redirect; no wrapper edit).
Operator action to reduce churn: sign out/close personal-browser Target tabs before the drop.

### [2026-07-08] - First keep-fresh night: token CHURN on business/alt-1 + wedge now recurs every ~75 min - Target
**Symptom**: Overnight run 01:54–07:44 (stopped manually, clean Ctrl+C shutdown). No
restock — monitoring pool healthy all night (61,902 sweeps @ 2.97/s, 99.0% HTTP 200,
zero 403s, ground-truth 138 ok / 8 fail), so the 0-drop night is trusted. Three findings:
1. **Token churn**: `business` + `alt-1` write-auth heartbeats returned 401 ~15×/h each
   (192 `WRITE-AUTH DEAD` events, evenly spread 02:00→07:44). Background repair re-minted
   a fresh member token every time (39 + 44 mints, all via rung 2 cookie-delete +
   `/account` reload — **rung 1 `gsp/token_refresh` never minted once all night**), but
   Target invalidated each fresh token again within minutes. `primary` was immune:
   textbook keep-fresh, exactly 2 proactive mints at the ttl<1800s threshold. A fresh
   member JWT dying in minutes is not TTL — it's server-side invalidation.
2. **Wedge recurrence**: the all-worker CDP wedge (07-03/07-06 post-mortems) fired on a
   strict ~75–80 min period (03:11, 04:26/04:31, 05:41/05:51, 06:56/07:11) — all 3
   worker tabs failed `evaluate('true')` within ~60 ms of each other each time. The
   sentinel ladder (token repair → nav refresh → **browser restart**) self-healed every
   cycle in ~6.6 s + relaunch; no credential relogin was ever needed, `[AUTH_CRITICAL]`
   never fired. The 16 monitoring Chromes (daemon-thread loop) never wedged — only the
   3 worker browsers driven from the main asyncio loop did. Periodicity + loop-locality
   are new clues; root cause still open.
3. **Canary noise**: `[CANARY] clean-channel read failed http=403` every cycle of the
   run (streak 691; also all of 07-06, streak 1251 — it has plausibly never passed).
   `_canary_fetch` is raw `urllib` + Chrome UA = the exact raw-Python-TLS mismatch the
   2026-05-14 audit proved Shape 403s from clean IPs. "Host IP may be blocked" was a
   false alarm (same-endpoint browser-native reads were 99% 200 all night; home-IP
   relogins passed 3/3).
**Root Cause**: (1) unresolved — fresh member tokens on exactly two accounts get
invalidated minutes after mint while the third account's survive. Suspects, in order: a
second live session rotating the same login-session's token (a personal/default-profile
Chrome, open since 07-07 20:46, spanned the whole churn window — Target appears to keep
one live accessToken per login-session, so two contexts re-minting evict each other);
account-level risk flag from the 07-07 signout-storm (business looped signout→blocked
login for ~6 h that morning); the BD IP swap at 01:12 is a weaker fit (run 2 on the same
new IPs was 35/35 alive, though only 6 min long). (2) open since 07-03. (3) wrong
transport for a Shape-fronted endpoint.
**Fix Applied**: (a) `purchase_executor.py` — warmup heartbeat lines now carry the
account id (`[WARMUP#0/business]`; last night's lines were account-blind) + new
`[AUTH_CRITICAL]` **token-churn alarm** (≥4 background repairs in 1 h, once/h max) so
silent churn surfaces in `logs/error_log.txt`. (b) `stock_check_resilient.py` — canary
retires itself for the run after 20 consecutive 403s with an honest one-liner instead of
alarming all night. (c) No change to the sentinel ladder — it validated live (~12
wedge self-heals). Both files py_compile clean.
**Confidence**: high on no-restock, wedge self-heal, canary false-positive; medium on
churn attribution (external-session vs account-flag not yet discriminated).
**Outcome**: PENDING next run. Playbook: close/sign out any personal-browser Target
tabs (esp. business/alt-1) before starting the bot → if `[AUTH_CRITICAL] TOKEN CHURN`
still fires, credential-relogin the two accounts fresh (new login-session); if it
persists after that, re-pin their purchase IPs from the 6-IP reserve in
`config/proxyIps.json` (the pre-01:12 IPs were deallocated in the 50→25 zone downgrade
— context in the 07-07 FAILURES entry) and retest. Watch: churn alarm, wedge cadence,
`[WARMUP#N/<acct>]` 401 pattern. Root fix still needed: 75-min wedge; rung-1
token_refresh endpoint (dead — every repair pays the ~5-10 s rung-2 nav).

### [2026-07-07] - ATC 401 _ERR_AUTH_DENIED across all accounts (stale write token) - Target
**Symptom**: Three restock windows (02:03–02:18, 03:17–03:27, 03:48–04:16) detected
perfectly and raced by all 3 accounts, but nearly every ATC POST returned
`401 T83072242 _ERR_AUTH_DENIED`. Sentinel reported `logged_in=True` every 5 min
through the entire failure. The one worker whose auth was live (fresh token minted by
a 03:22 browser restart) got straight through to the known demand-throttle walls
(ATC `DCO_RATE_LIMITED`, checkout 429 `RESERVATION_FAILURE` → `checkout_busy_retryable`
re-race worked as designed). Net effect: the 3-account race silently degraded to ~1
account — the exact single-account wall multi-account was built to break.
Secondary: at 08:22 `business`'s session died for real; the sentinel's last-resort
`full_signout → credential login` looped every 5 min with Shape blocking the password
submit, leaving the account signed out with a **guest** accessToken.
**Root Cause**: carts.target.com WRITES authenticate via the `accessToken` cookie
(member JWT, ~4h TTL, `eid` claim only on member tokens). Every bot request is a raw
page-context `fetch()` that bypasses Target's SPA HTTP client — the layer that owns
refresh-token-on-401 — so nothing ever refreshed the token. The sentinel's `/account`
DOM check renders fine from `login-session` alone (blind to a dead/guest/expired write
token), and the ATC 401 recovery only refreshed Shape headers. Browser restarts
re-injected the same dead token from disk.
**Fix Applied**: token-first stack (2026-07-07, `session_manager.py` +
`purchase_executor.py`):
1. `get_access_token_status` / `refresh_access_token` / `ensure_fresh_access_token` —
   decode the live accessToken JWT (member/guest/ttl) and re-mint via the site's own
   `token_refresh` endpoint (rung 1, ~0.5s, no nav), else delete accessToken/idToken
   cookies + full page load so the edge must mint fresh (rung 2, endpoint-agnostic;
   never touches login-session/refreshToken).
2. Sentinel rung 0 = token check+repair every 300s tick (keeps tokens hot 24/7);
   DOM nav is now the fallback, and its verdict requires a member token to pass.
3. ATC 401 path repairs the token (force, endpoint rung) before the Shape re-warm;
   second consecutive 401 unlocks the nav rung. Cart-clear DELETE 401 repairs too.
4. Warmup dummy POST (fires every 60–90s anyway) now reports its status as a
   write-auth heartbeat: 401 → `WRITE-AUTH DEAD` log + background self-repair
   (rate-gated 300s, skipped mid-purchase).
5. Destructive credential relogin capped: 2 tries/6h, 20-min cooldown, one
   `[AUTH_CRITICAL]` alert to `logs/error_log.txt`, cookies left alone when capped
   (wrapper/nightly `relogin_one.py all` is the real fixer).
Knobs: `TARGET_TOKEN_KEEPFRESH=0` (kill-switch → pre-07-07 behavior),
`TARGET_TOKEN_MIN_TTL_S` (default 1800), `TARGET_TOKEN_REFRESH_URL`,
`TARGET_RELOGIN_MAX_PER_6H` (default 2), `TARGET_RELOGIN_COOLDOWN_S` (default 1200).
**Confidence**: high on diagnosis (JWT decode of saved sessions: `business` held a
guest token minted at 08:27:56 = the signout loop; `primary`/`alt-1` tokens carried
4h TTLs that expired mid-window; sentinel passed throughout). Medium-high on rung 1
(gsp endpoint from training knowledge — rung 2 is endpoint-agnostic and covers it;
first night's `[TOKEN]` log lines will show which rung the live site honors).
**Outcome**: FIX VALIDATED LIVE (2026-07-07 PM, through the real BD-proxy app.py path):
- `[WARMUP] dummy POST status 424 — write-auth alive` and `WRITE-AUTH DEAD — dummy POST
  returned 401` both fire correctly (heartbeat works).
- `[TOKEN] primary: fresh member token minted via cookie-delete + /account reload` — the
  exact repair that did NOT happen at 03:17 this morning now fires through the proxy. ✅
- primary + business steady-state `[SENTINEL] logged_in=True`; boot → `monitoring active`.
- Hardening from the live run: deleting `idToken` alongside `accessToken` made the reload
  re-mint a GUEST token — now delete ONLY `accessToken`; poll the jar for the async SPA
  mint (≤8s) instead of a fixed sleep; nav to auth-gated /account.

**Follow-up gap found (NOT yet fixed) — mid-run guest-downgrade can't self-heal on the
proxy path**: an account whose login-session has aged/degraded re-mints a GUEST token
(member=False) instead of a member one; `_is_fresh_member` correctly rejects it, the
sentinel escalates nav→restart→credential-relogin, but the in-app credential relogin runs
through the worker's **BD proxy** and Shape-blocks (`[RELOGIN] … failed`), so the account
stays guest. Observed on alt-1 (validate-first re-harvested 2.5h earlier); primary+business
(fresh `--force` full logins ~20min earlier) minted member fine. Distinguisher looks like
**full fresh login vs stale re-harvest**, not IP. Mitigation in place for tonight: all 3
accounts `--force` full-relogged-in so they START from robust member sessions; keep-fresh
maintains them. Real fixes to schedule: (1) route the sentinel's rung-3 credential relogin
through the HOME IP (RELOGIN_SKIP_PROXY) like the nightly wrapper's proven flow, so a
degraded account self-heals mid-run; (2) investigate why a re-harvested login-session
re-mints guest while a full-login one re-mints member (session-robustness / TTL of member
mint capability). Until (1) lands, the guarantee rests on fresh full logins at boot — which
the nightly wrapper's `relogin_one.py all` provides (dead/guest accounts get a full login;
member accounts get re-harvested).

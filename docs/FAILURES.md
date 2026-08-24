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

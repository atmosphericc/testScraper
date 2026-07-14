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

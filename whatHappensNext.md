# whatHappensNext.md

## ★ 2026-10-27 overnight session — READ THIS FIRST ★

**Operator /goal:** "be 100% ready for the next drop, comfortable all logs are in place (new
architecture), stress-test as hard as possible, big improvements not minor tweaks, succeed next
drop leveraging all we have." Proxies: **use BD ISP + IPRoyal resi only, no new procurement**;
stress full-send but spend residential GB efficiently.

**The honest bottom line (grounded in our own data, not hope):** there is **no code lever that
beats wall 1** — the edge limiter's admit `p` is site-wide, per-window, and independent of our
volume/accounts/lines (C-1023-26); Refract admits the same about their own bot. So "succeed next
drop" splits into: **(1)** take units IF the gate opens (the home bot is positioned to — it carted
+ ordered on 10-01 when p~30%), and **(2)** come away with a **definitive line/ASN verdict** — the
only result that changes the architecture. (2) is in our control and is the real prize; we failed
to capture it on 10-23 (resi arm froze; only 4 windows). **Tonight removed every obstacle to
capturing it next drop.**

**A refinement that adjusts the "residential" thesis:** home AND IPRoyal are both Comcast → they
likely **share one ASN fate** (why resi tracked home on 10-23). **Bright Data is on genuinely
different ASNs** → BD is the *cleaner* test of "does a different ASN beat home at wall 1." If any
line admits while home gets 0, BD is the likely one — which would point to **BD shooting, not
residential**. Residential's proven value is Shape (wall 2), reachable only after wall 1 opens.

**Landed tonight (all flag-gated, verified; bot later started GREEN at operator request — lanes still OFF):**
- **Freeze fix VALIDATED** — 25-min IPRoyal soak: 335 tokens, **0 failures**, 2 proactive relaunches
  at 721/722s, no CDP freeze. The 720s clamp keeps the resi/BD follower arms alive across a full
  drop window (the exact thing that killed the 10-23 measurement). [MEASURED 2026-10-27]
- **Lane bank freshest-first** fix (`TARGET_LANE_BANK_NEWEST_FIRST`, default off; **flip ON when
  lanes arm** — it matches the proven production `pop_fresh`). `tests/test_lane_pool.py` 30/30.
- **`NEXT_DROP_CHECKLIST.md`**: added a **self-grading VERDICT RULE** (LINE/ASN-SPECIFIC vs GLOBAL
  vs INCONCLUSIVE) + the ASN mapping, so the drop yields a definitive answer, not an underpowered shrug.
- **`tools/experiments/launch_drop_followers.py`**: one command launches all 4 cross-ASN follower
  lines + the BUY NOW arm (with `--proactive-relaunch-s 720`), killing the 01:35-CT hand-launch risk.
- **`docs/REFRACT_PARITY.md`**: full rewrite — Refract's architecture, the complete wall-by-wall
  **security model** (§9), the sticky-vs-rotate verdict, and every stale 09-20 claim corrected
  (lock already armed, bank already deep, profile-write moot for our members).
- **`CURRENT_STATE.md`** restored (was deleted) + header refreshed with tonight's state.
- **Line-health battery (money-free, post-boot):** ALL 6 proxy lines validated alive + Shape-passing —
  reserve:0/1 (BD) 3/3 & 3/3 → 424; resi:0 (freeze soak) 335 tok/0 fail; resi:1/2/3 all PASS (resi:1
  had one slow 9s shot, still passed). No dead sessions — the 10-23 silent-death failure is pre-empted.
  Logs: `logs/experiments/lane_linehealth_*`.
- **Pre-drop readiness (post-boot):** 3/3 accounts healthy MEMBER (start hot, tokens re-mint); 25/25
  TCINs visible to RedSky (no bad/invalid SKUs). Some SKUs are pre-orders (binder 1010892074 ~12-04;
  Delta Reign likely) — valid but won't drop imminently. `python check_session_readiness.py` = ✅ 3/3.

**Investigations this session confirmed (so we stop re-deriving):** the per-(account×TCIN) purchase
lock is ALREADY built+armed; the production Shape bank is ALREADY depth-6 (>Refract's 3); the
profile-write "cookie burn" does NOT apply to our pre-provisioned members; BUY NOW is ALREADY a
built, money-free, pre-registered drop arm (one body field, same bucket expected — low EV but fires
itself). Net: the "build more" lever is nearly exhausted; the architecture is largely done.

**Bot state (as of 2026-10-27 00:2x):** UP and **GREEN** — started 00:21:48, run
`run_20261027_002244.log`, boot_check GREEN (all 3 accounts logged in + write-auth alive + harvest
424/424 + member sentinels OK + purchase_thread started + 25 TCINs + real_purchasing=True). Armed on
the **PROVEN production path**; `ensure_armed.disable` REMOVED so the 01:30 auto-arm recovers a crash.
**LANES ARE OFF (`TARGET_LANE_MODE=0`) — deliberately.** Operator asked to "set up the lanes / go
balls to the wall" (2026-10-27 ~00:30); I recommended AGAINST arming and left them OFF, because:
(1) `p` is site-wide and volume-independent — more lanes ≠ more admits (C-1023-26; 0/466 with MORE
shots); (2) the resi>home premise is UNPROVEN/contradicted (119/119 both lines, 10-23); (3) the lane
path has never fired a real purchase (money-free validated only). Arming lanes stays gated on the
live-drop cross-ASN measurement. If the operator overrides: run claims-verifier on the premise first,
then `TARGET_LANE_MODE=1` + `TARGET_LANE_BANK_NEWEST_FIRST=1`, and watch `/api/lane-status`.

**At the next drop (the measurement that unlocks everything):**
1. Re-arm the bot (if a drop is expected): remove the disable flag + `bot_ctl start`, verify GREEN.
2. At ~01:35 CT: `python tools/experiments/launch_drop_followers.py` (fires BD + IPRoyal + BUY NOW;
   home follower auto-runs). Needs the session open OR schedule it via /pre-drop.
3. After: `python tools/events/build.py --only <run_id>`, then the readout + VERDICT RULE in
   `docs/NEXT_DROP_CHECKLIST.md`. Declare GLOBAL / LINE-SPECIFIC(name the ASN) / INCONCLUSIVE.
4. If a non-home ASN beats home → **that** is the green light to build that line's shooting (the big
   improvement, earned by data). If all-zero → global, and no line/volume change helps that night.

**Staged, not built tonight (for IF we arm shooting):** production per-shot egress-IP+ASN logging +
lane-shot parsing into the event store (failure-forensics Rank 1–5) — the event store cannot yet
split admits by ASN; the follower (the drop instrument) can, so this is belt-and-suspenders for the
money path, not on the critical path for the measurement.

**NEXT FOCUSED TASK (operator awake, ~30-60 min) — finish lane arm-readiness:** validate the checkout
handoff (201 cart → account Chrome → pre_checkout → CVV → place_order) AND the main-vs-lane
coordination (both `app.py` paths fan each in-stock TCIN to the main purchase manager AND the lane
pool on the SAME account×TCIN; their locks are separate and don't coordinate → **double-fire risk,
UNVALIDATED**). Money-safe via `test_app.py`; needs the live bot briefly DOWN (it owns the account
Chromes). Then `claims-verifier` on the premise, then arm as a labelled A/B with a kill switch. ONLY
then is `TARGET_LANE_MODE=1` safe. This is the honest path to putting the lanes we built into the fight.

**TO FIX / loose ends (none block the proven bot):**
- Refresh `.claude/state/CURRENT_STATE.md` body beyond the header (10-23 detail persists in places).
- Cosmetic: benign `WinError 10054` forwarder tunnel-close tracebacks in the run log (suppress later).
- For arming shooting later: production per-shot egress-IP+ASN logging + lane-shot event-store parsing
  (failure-forensics Rank 1-5) — the event store can't yet split admits by ASN; the follower can.
- resi:1 showed one 9s shot (residential latency variance) — watch, not yet a fix.

---

**Date:** 2026-10-26
**Branch:** `feat_refract_arch_v1`
**Subject:** Residential lane harvesters — switch + three fixes, live-validated off-window. NOT armed, NOT committed.

---

## 0) TL;DR

- The independent-lane ATC system (`src/lanes/`) was switched from Bright Data ISP to the 4
  IPRoyal **residential** harvester IPs. The BD ISP sweep pool still runs the stock monitor
  (the Refract split: residential for shooting, ISP for monitoring).
- Three harvester problems were fixed: (1) a **proxy sign-in (407) dialog** on residential that
  stalled the Chrome, (2) the **~17-min IPRoyal CDP freeze**, (3) **PerimeterX / HUMAN wall**
  detection.
- Validated **off-window, money-free**: harvester produces tokens through residential, and the
  curl_cffi shooter fires through the resi IP and **passes Shape (3/3 → HTTP 424)**.
- **The limiter premise is still UNPROVEN.** Off-window 424 proves the plumbing, not that
  residential beats home at the drop-time edge limiter. The last real drop (10-23) refused
  119/119 shots on BOTH home and residential.
- Everything is behind `TARGET_LANE_MODE=0` (off) and is **uncommitted** in the working tree.

---

## 1) What was done this session (2026-10-26)

### 1a. Proxy-auth fix (the bug the operator caught live)
- **Symptom:** opening the Target PDP in the residential harvester Chrome showed Chrome's native
  "sign in / username and password" dialog; the page stalled and no tokens were harvested.
- **Root cause:** the harvester authenticated the IPRoyal proxy with a CDP `Fetch.authRequired`
  handler that was armed only for the first navigation, then `fetch.disable()` was called. Every
  later connection hit a fresh HTTP 407 with no handler, so Chrome fell back to the native prompt.
- **Fix:** route ANY credentialed proxy (Bright Data **and** IPRoyal) through the existing local
  `ForwarderPool`. Chrome connects to `127.0.0.1`; the forwarder injects the proxy password
  upstream, so no 407 ever reaches Chrome. The CDP-auth branch was deleted.
  - New pure helper `_proxy_has_credentials()` decides forwarder-vs-direct.
  - The curl_cffi shooter is unchanged — it authenticates the proxy itself and exits the same
    IPRoyal sticky session, so harvester and shooter stay on the same exit IP.
- File: `src/lanes/shape_harvester.py` (`_ensure_chrome`, `_ensure_forwarder` docstring, helper).

### 1b. Freeze hardening (C-1023-12: IPRoyal CDP stall at 17-21 min)
- `safe_chrome_max_age()` clamps residential Chromes to **720s** regardless of the configured/env
  value (BD/home keep their value). 720s < the 17-min onset, with margin.
- **Reactive stall detector:** `_do_harvest()` now returns a bool; after
  `TARGET_LANE_STALL_FAILS=3` consecutive failed harvests the loop relaunches Chrome immediately
  (catches a freeze that arrives before max-age).
- **Rolling-hour relaunch budget:** reactive relaunches are capped at 6/hour
  (`relaunch_budget_exceeded`, mirrors the proven guest-follower `FS-W-D` pattern). Over budget =
  back off 30s and flag `unhealthy`, so a dead IP cannot thrash Chrome.
- File: `src/lanes/shape_harvester.py` (`_harvest_loop`, `_do_harvest`, `__init__`, `status`).

### 1c. PerimeterX / HUMAN wall detection (observability + recovery)
- After the PDP loads, a read-only PX markers probe runs (reuses `src/session/px_challenge.py`
  `PX_MARKERS_JS` + `is_px_challenge`). A wall that **persists a 2.5s re-probe** triggers a
  fresh-guest relaunch (rate-capped on the same budget).
- Design note: the fresh-guest model is KEPT on purpose. Per C-1023-14 the PX wall is
  *profile-bound* — a fresh guest loads `px_wall=0`. A persistent profile would *accumulate* the
  wall, so we must NOT make the harvester profile persistent.
- Observed live: the control TCIN's "Product not found" page draws a transient cold-start
  `px_container` ~1x per launch; recovered in 1 budgeted relaunch, then harvested cleanly.
- File: `src/lanes/shape_harvester.py` (`_ensure_chrome` PX block).

### 1d. Config switch to residential
- `run_bot_with_nightly_restart.bat`: `TARGET_LANE_IPS` changed `all_reserve,all_resi` → **`all_resi`**.
  New documented knobs: `TARGET_LANE_STALL_FAILS=3`, `TARGET_LANE_PX_PROBE=1`.
  `TARGET_LANE_MODE=0` unchanged (off).
- `config/proxyIps.json`: fixed the stale `_comment_residential` (it said "Bright Data" / "the bot
  does not read this key" — both wrong now; they're IPRoyal, and the lane pool reads them when
  `TARGET_LANE_IPS` resolves resi AND `TARGET_LANE_MODE=1`).
- The stock monitor is untouched — it reads the separate `proxies` (BD ISP) pool.

### 1e. Tests
- `tests/test_lane_pool.py`: +6 pure-helper tests (`_is_resi_proxy`, `safe_chrome_max_age` resi/BD,
  `_proxy_has_credentials`, `relaunch_budget_exceeded`). Credential-free URLs (the auto-mode
  classifier blocks realistic proxy creds in files).
- `tools/experiments/lane_validation.py` used for the live checks (pre-existing tool).

---

## 2) Files changed (ALL UNCOMMITTED)

```
src/lanes/shape_harvester.py          # forwarder auth, freeze hardening, PX detection, bool _do_harvest
run_bot_with_nightly_restart.bat      # TARGET_LANE_IPS=all_resi + new knobs
config/proxyIps.json                  # _comment_residential corrected
tests/test_lane_pool.py               # +6 tests, import line
```
(Plus the broader untracked `src/lanes/` tree and the app.py/bat/suite edits already in the working
tree from the 10-25 build.)

---

## 3) Validation results (money-free, control TCIN 81926151)

| Check | Result |
|---|---|
| Offline suite (`tests/run_offline_suite.py`) — the deploy gate | **77 passed, 0 failed, 0 skipped** (495s) |
| Lane unit tests (`test_lane_pool`) | **29/29** |
| Resi harvester live (`lane_validation.py --ip resi:0 --harvest-only`) | 13-16 tokens, **0 failures**, forwarder OK, `chrome_max_age=720s` |
| Resi full pipeline (`lane_validation.py --ip resi:0`) | **3/3 shots HTTP 424 PASS** (Shape accepts the IP-bound token via curl_cffi) |

Minor: a benign `WinError 10054` connection-reset traceback prints at shutdown (forwarder tunnels
closing) — cosmetic, RC=0, 0 harvest failures. Could be suppressed later.

---

## 4) PROVEN vs NOT PROVEN (read this before arming)

**Proven (off-window, mechanical):**
- Residential proxy auth works with no dialog (forwarder path).
- The harvester produces Shape tokens through a residential IP.
- The curl_cffi shooter fires through the residential IP and **Shape accepts** the IP-bound tokens
  (424, not a 401 deny) — the E-SHOOTER property holds on residential.

**NOT proven (the things that justify arming):**
- **Residential beats home at the edge limiter during a drop.** This is the entire premise of the
  switch. Off-window 424 says nothing about it — the control TCIN is never in a launch/limiter
  state. The most recent real drop (C-1023-10) refused **119/119 on BOTH home and residential**,
  "one shared admit rate per window, not a line/account key". So the premise is currently
  *contradicted*, not supported.
- Freeze-survival over a full 12-min proactive-relaunch window (the 720s clamp is set but was not
  stress-tested live for 15+ min).
- The checkout handoff on a residential cart (only harvest + ATC shoot were validated).

---

## 5) What should be next (ordered)

1. **Commit** the working tree to `feat_refract_arch_v1` (operator to approve). Suggested message
   scope: "resi harvesters: forwarder auth fix + freeze/PX hardening + all_resi switch + tests".
2. **Answer the limiter question at a live drop — this is the gate for arming residential.**
   Use the multi-line guest follower (`tools/experiments/limkey_guest_follow.py`) at the next drop
   to measure shots-past-the-limiter for a residential line vs the home line in the SAME window.
   - If resi `p` > home `p`: residential is justified — proceed to arm.
   - If resi `p` ~= home `p` (the 10-23 result): the switch buys nothing at the limiter; do NOT
     arm resi on the limiter rationale (it may still help only if home is individually throttled).
3. **Freeze stress test:** run `lane_validation.py --ip resi:0 --harvest-only --duration 900`
   (~15 min) to confirm the 720s proactive relaunch keeps tokens flowing across the freeze window.
4. **Checkout handoff validation** on a residential cart (test env / test_app.py path), money-safe.
5. Only then consider arming: `TARGET_LANE_MODE=1` for a labelled A/B with a pre-registered
   readout and kill rule, per the project's arming discipline. **Always verify before arming** —
   hand the bare premise to `claims-verifier`; a REFUTED verdict kills it.

---

## 6) Overall plan / strategy

**Architecture.** N accounts x M IPs. Each unique exit IP runs ONE guest-Chrome `ShapeHarvester`
that continuously mints Shape tokens; each (account x IP) lane has a lightweight curl_cffi
`LaneShooter` that draws a token and fires ATC. 201 carts hand off to the account's existing Chrome
for checkout. This scales shots-per-window far past the one-Chrome-per-account model — *when shots
can get through at all.*

**The binding wall is the edge rate limiter, not Shape.** Gate order (C-0930-03, VERIFIED):
edge limiter (429, ~130ms, no `x-ssx-hop`) -> Shape SSX hop (keyless 401) -> cart service. A 401
means you already beat the limiter. The limiter's admit probability `p` is set **site-wide per
drop window** and collapsed to ~0 on recent drops (CURRENT_STATE 10-23). More accounts/IPs/shots do
NOT raise `p`. So the lane system only converts when `p > 0`.

**Why residential at all.** The open question is whether a residential line draws a *different*
limiter admit than the home line (i.e., is a 0-night global, or just the home IP throttled?). If
residential `p` > home `p`, the resi harvesters are worth arming; if not, this is bandwidth spend
for no limiter gain. That question is unanswered and only a live drop can settle it (step 5.2).

**Division of IPs (now implemented):** residential (IPRoyal) for SHOOTING (lanes), BD ISP for
MONITORING (the stock sweep). Matches Refract's documented split.

**Guardrails:** money-free validation only until armed; `TARGET_LANE_MODE=0` is the kill switch
(exact prior fleet behaviour unchanged); offline suite (77/77) is the deploy gate; never blanket-run
`tests/`; ask before any daytime bot start/restart or forced login.

---

## 7) Arm / kill reference

- **Off (default/kill):** `TARGET_LANE_MODE=0` in `run_bot_with_nightly_restart.bat`.
- **On (A/B only, after the gate):** `TARGET_LANE_MODE=1`. Lanes then read `TARGET_LANE_IPS=all_resi`.
- Harvester knobs: `TARGET_LANE_CHROME_MAX_AGE_S` (resi clamped to 720 in code),
  `TARGET_LANE_STALL_FAILS=3`, `TARGET_LANE_PX_PROBE=1`, `TARGET_LANE_HARVEST_INTERVAL_S=4.0`,
  `TARGET_LANE_TOKEN_TTL_S=300`, `TARGET_LANE_WARMUP_TOKENS=5`.
- Health endpoint: `GET /api/lane-status` (harvesters: running/chrome/bank/unhealthy/px_walled;
  lanes: shots/edge_pass/orders).

---

## 8) Key references

- Live facts: `.claude/state/CURRENT_STATE.md` (currently deleted in the working tree — restore/
  refresh from git HEAD and update with the 10-26 resi-harvester status).
- Claims: `docs/CLAIMS.md` — C-0930-03 (gate order), C-0930-04 (limiter != trust), C-1023-10
  (119/119 both lines), C-1023-12 (resi freeze), C-1023-14 (PX profile-bound).
- Architecture: `docs/RESILIENT_STACK.md`; Target: `docs/RETAILERS/target.md`.
- Code: `src/lanes/{shape_harvester,lane_shooter,lane_pool,lane_manager,lane_executor,lane_checkout}.py`,
  `src/proxy/local_forwarder.py`.
- Validation tool: `tools/experiments/lane_validation.py`.

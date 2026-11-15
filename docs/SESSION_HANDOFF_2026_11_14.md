# Session Handoff -- 2026-11-14 (overnight, autonomous)

Read this first. Everything below is REVERSIBLE, flag-gated, and proven behind the offline suite.
NOTHING was booted, run live, or spent. No accounts created. All changes are UNCOMMITTED on branch
audit_1106_harvest_probe_fs1.

## TL;DR
1. Fixed the two things in our control: detection (BD cancelled -> free home-IP sweep) and the
   harvest bank-drain bug that was giving us literal zero on nights like 11-06.
2. Ran a full Wall-A + Wall-B investigation (9 agents, adversarially verified).
3. Corrected my OWN overstatement after verification: there is NOT "no code lever / volume only".
   The honest path is data + armed-untested levers + accounts -- see below.

## What landed (files + flag + how to revert)

A. DETECTION repoint (BD cancelled). Sweep now exits the HOME IP on apps_raw.
   - run_bot_with_nightly_restart.bat:71  RESILIENT_HARVEST_VIA_LOCAL_IP=1
   - config/proxyIps.json  "proxies" trimmed 10->1 (count placeholder; 0.5/s on home), reserve emptied.
     Backup: config/proxyIps.json.bak_20261114_prebd_cancel
   - PROVEN: raw apps_raw read 24/24 clean on home @0.5/s (this session). UNVERIFIED: the pool
     scratch-Chrome homepage-cookie readiness on home (needs a boot); the armed trusted-reader
     (FORCE_TAB_FETCH=auto, proven 200 on home) backstops it.
   - Revert: set the bat flag back to 0 AND restore proxyIps from the backup (needs a live pool).

B. WALL A FIX -- FX-1114-PXRECONFIRM (the empty-bank zero). purchase_executor._harvest_miss_probe
   now re-reads the DOM after ~2.5s before parking, so a transient "Product not found" decoy flash
   no longer parks+drains the bank (CONFIRMED cause of the 11-06 zero: 78 parks, all "Product not
   found", px_container-only; verified by claims-verifier).
   - Flag TARGET_HARVEST_PX_RECONFIRM: default OFF in code, ARMED =1 in the bat (after the RENAV line).
   - Test tests/test_harvest_px_reconfirm_1114.py (15/15, drives the real method via a fake tab).
   - Offline suite 85/85. Revert: TARGET_HARVEST_PX_RECONFIRM=0.
   - Bank robustness: the harvest ALREADY rotates decoys on misses, so with the re-confirm the bank
     survives a transient OR a genuinely-dead decoy (all 3 decoys live as of 11-14). Residual drains
     (a real Chrome/CDP wedge flushing the bank; consumption bursts) are documented, not fixed --
     they are trade-off-laden and need your call.

C. CODE COMMENT CORRECTION -- purchase_executor.py:1890 said FAST_SELLING was "per-account, never on
   the first shot" (refuted on hot SKUs; it is per-TCIN window-age). That wrong model drove the
   useless ride logic. Comment-only, no behavior change.

D. DOCS + MEMORY -- CLAIMS.md C-1114-01..09, CURRENT_STATE.md (11-14 block), memory
   (project_wallb_fast_selling, project_proxy_cost_posture, project_harvest_px_wall_binding updated),
   docs/ACCOUNT_SCALING_PLAN_2026_11_14.md, this handoff.

## The diagnosis (verified)

Hot-SKU zero = TWO walls after the edge limiter:
- WALL A (harvest bank drains -> page_signed -> Shape-401 -> no 201 to even place). This was the
  11-06 zero and it was OUR bug (dead-decoy false positive). FIXED (B above).
- WALL B (FAST_SELLING throttles the place-order). Characterized: a per-TCIN demand gate on the
  item's window-age (not account/IP), saturates ~2-3s into a hot window, holds until OOS. The only
  converting shot is an early (<~3s) FIRST place-order; riding converts 0.

## The honest forward path (corrected after verification -- C-1114-09)

My earlier "no code lever; volume is the ONLY lever" was OVERSTATED. The verifier confirmed the
narrow pieces (chain is minimal, its 1.6-3.6s is Target server latency, ride converts 0, no handler
gap) but flagged three real openings:

1. WE LACK DATA. Only n=1 hot cart has reached a place-order decision since 10-01; 20/21 lifetime
   orders are pre-09-01 ORDINARY SKUs. On 11-06 the wall was the empty bank, not FS. **The Wall A
   fix is the DATA GENERATOR** -- it gets hot carts to a checkout decision so we can finally measure
   the real Wall B ceiling.
2. ARMED, UNTESTED code levers exist: TARGET_FASTLANE_PRE_RETRY=1 (bat:1115, labeled "THE hot-SKU
   conversion fix" for the pre_checkout FS variant -- never tested on a binding hot pre=429) and
   TARGET_HELD_LINE_FLIP_STRIKE=1 (FX-1001-A). Watch these at the next drops.
3. ACCOUNT COUNT is the leading structural lever for more edge-limiter draws (each account = one
   draw per window; we run 3, competitor 30-200), gated by the shared IP/fingerprint and an
   unresolved "does the edge limiter key on IP?" question. Full map: ACCOUNT_SCALING_PLAN.

Settled NON-levers (do not re-chase): parallel dispatch (already parallel, 2ms spread), place-order
chain speed (already minimal, latency is Target''s), cadence/cap/hold/qty/ride, faster re-shots
(WAVE_FIRST_EDGE killed: 663 re-shots -> 0 edge passes).

## Operator decisions / to-dos (when you wake)

1. REVIEW + COMMIT. All changes are uncommitted on branch audit_1106_harvest_probe_fs1.
2. BOOT TO VERIFY (needs you -- I did NOT boot per the safety rules): a boot confirms both the
   home-IP detection readiness and the bank fix. Use tools/predrop/boot_check.py --follow to GREEN.
3. NEXT DROP(S): with the bank fixed, watch whether hot carts reach a place-order decision and
   whether pre_retry converts. This is the data that settles Wall B -- free.
4. ACCOUNT SCALING: read docs/ACCOUNT_SCALING_PLAN_2026_11_14.md. Cheap test = +1-2 accounts.
5. BUSINESS LOGIN (human-gated) -- restores the 3rd buyer, a free +1 draw.

## Verify / state
- Offline suite: 85/85 (Wall A fix run). Gate: python tests/run_offline_suite.py
- git status: 6 modified (CURRENT_STATE, CLAIMS, bat, purchase_executor, shape_harvest, run_offline_suite),
  2 new (test_harvest_px_reconfirm_1114.py, ACCOUNT_SCALING_PLAN). config/proxyIps.json is gitignored
  (edited, backup saved). The D/??  entries for CLAUDE.md, whatHappensNext.md, bat_audit etc. PREDATE
  this session.

## Post-build independent code review (7 cycles)

After landing the Wall A fix I ran /code-review (high) in a loop until convergence -- each cycle a
fresh reviewer that did not write the code. 7 cycles; every cycle fixed a real issue, suite re-run
green each time (85/85), and the branch-test file grew to 29 cases:
- Fail direction: the re-confirm was failing CLOSED (a re-read timeout/non-dict kept the first
  verdict and parked, reopening the drain) -> now FAILS OPEN.
- A strong/weak gate I tried reopened the drain for iframe/press_hold flashes -> reverted.
- Final design: PARK ONLY on a persistent STRONG marker on a lightweight PX re-read; a container-
  only #px-captcha verdict (flash OR stuck placeholder) never parks (rotate). On an inconclusive
  re-read, fall back to the first read (a strong first read still parks).
- Also fixed: helper drift, misleading logs, an over-broad title regex (removed), a delay floor.
ACCEPTED residual (documented in-code): a wall rendering ONLY #px-captcha and never a strong marker
is not parked -- unobserved, low cost (a real profile wall walls the buyer anyway).

### Review items NOT code-changed (your call)
- **Pre-existing deletions in the working tree:** CLAUDE.md, docs/NEXT_DROP_CHECKLIST.md,
  whatHappensNext.md are DELETED in the working tree -- these PREDATE this session (initial git
  status) and are unrelated to the harvest fix. The reviewer and memory both flag them. RESTORE
  before any commit:  git checkout HEAD -- CLAUDE.md docs/NEXT_DROP_CHECKLIST.md whatHappensNext.md
- **Home-IP sweep (RESILIENT_HARVEST_VIA_LOCAL_IP=1):** flagged every cycle as the riskiest change;
  it is user-mandated (BD cancelled) and backstopped by the armed trusted-reader. Verify at boot.
- **Lane harvester (shape_harvester.py) still treats px_container as a challenge:** OFF-path
  (TARGET_LANE_MODE=0); not changed. Unify is_px_challenge usage if lanes are ever armed.

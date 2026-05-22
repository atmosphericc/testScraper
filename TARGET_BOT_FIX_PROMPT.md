```
══════════════════════════════════════════════════════════════════════════════
TARGET BOT — POST 5/21 INCIDENT FIXES (8 CHANGES)
══════════════════════════════════════════════════════════════════════════════

Context:
  Repo: /Users/Eric/Desktop/testScraper (Target retail bot, resilient stack).
  Working branch: feat_refract_arch_v1 (confirm with `git status` first; if on
  another branch, ASK before switching).

  Background: A 7-hour overnight run on 2026-05-21 had three failure modes:
    1. Three Bright Data IPs on the 31.105.x.x subnet burned in a 90-second
       window at hour 1.7 — all 680 of the run's 403s came from these 3 IPs.
       The other 13 active IPs (on different subnets) had 0 403s for the full
       run. This is a SUBNET REPUTATION issue, not a rate issue.
    2. Synchronized 16-way "consecutive error" cascade at hour 1.2 — every
       session independently tripped the recycle threshold within 13 minutes
       during what was probably a single transient network blip. Watchdog
       recovered the pool, but caused a 13-min throughput drop.
    3. On a Windows host, sessions s1 and s6 entered chronic Chrome launch-
       failure loops (80+ cycles each) over the last 3 hours of the run —
       likely zombie Chrome processes holding the profile-dir lock. Pool
       ended at 5/16 healthy.

  We are NOT lowering RPS. The pool of 16 IPs at 3 RPS yields 0.19 RPS-per-IP,
  well within the validated headroom (60-min stress at 1.0 RPS-per-IP returned
  99.95%). 3 RPS stays. The fixes below address subnet concentration, cascade
  amplification, and Windows resource cleanup.

══════════════════════════════════════════════════════════════════════════════
ORDER OF EXECUTION

  Phase 1 (config only, no code) — fixes #1, #2, #3
  Phase 2 (single-line code edits) — fix #4
  Phase 3 (real code, test after) — fixes #5, #6
  Phase 4 (ops, no code) — fix #7
  Phase 5 (verification, real test) — fix #8

  Commit at the end of each phase. ASK before pushing.

══════════════════════════════════════════════════════════════════════════════
FIX #1 — Delete ghost TCIN 94300069 from config (5 min)
══════════════════════════════════════════════════════════════════════════════

Background: TCIN 94300069 does not exist on Target. It's currently
`enabled: false` in product_config but still appears in product_catalog.json
and the pokemon_target_seed CSVs, generating "No product found" GraphQL
responses each long run.

Edit these four files to remove every 94300069 reference:
  - config/product_config.json   (object with tcin "94300069", enabled: false)
  - config/product_catalog.json  (object with tcin "94300069")
  - config/pokemon_target_seed.csv          (the row containing 94300069)
  - config/pokemon_target_seed_filtered.csv (the row containing 94300069)

Also scan product_config.json for OTHER entries named "Product XXXXXX"
(generic placeholder names). For each one, decide with the user whether to
keep, rename with the real name, or delete. Do NOT silently delete any
others — just list them for review.

Validate both JSON files parse:
  python -c "import json; json.load(open('config/product_config.json'))"
  python -c "import json; json.load(open('config/product_catalog.json'))"

══════════════════════════════════════════════════════════════════════════════
FIX #2 — Rotate 31.105.x.x subnet IPs out of the active pool (10 min)
══════════════════════════════════════════════════════════════════════════════

Background: The active pool in config/proxyIps.json currently has 8 IPs on
the 31.105.x.x subnet (50% of active pool). The 5/21 incident burned 3 of
them in 90 seconds. The remaining 5 are at high subnet-reputation risk.

Current 16 active IPs (per config/proxyIps.json) include these 8 on
31.105.x.x:
    31.105.133.83
    31.105.18.86
    31.105.93.225
    31.105.155.55     (BURNED 5/21 — do not return to active)
    31.105.183.245    (BURNED 5/21 — do not return to active)
    31.105.153.101    (BURNED 5/21 — do not return to active)
    (and 2 more 31.105.x.x — re-verify by grepping the file)

The reserve_proxies list has 11 entries. Use these 8 non-31.105 reserves to
fill the active pool (do NOT promote 31.105.250.38 or 31.105.171.7 — same
risky subnet):
    31.98.169.203
    168.158.166.218
    168.158.180.157
    168.158.38.14
    168.158.168.109
    168.158.142.216
    168.158.36.241
    92.112.18.99
    168.158.51.225
    (use first 8 of these, leave the rest in reserve)

Steps:
  1. Backup: cp config/proxyIps.json config/proxyIps.json.bak_subnet_swap_<YYYYMMDD>
  2. Remove the 3 BURNED 31.105 IPs from the active pool entirely (delete,
     do NOT move to reserve — they are externally flagged).
  3. Move the remaining 5 active 31.105 IPs to reserve_proxies.
  4. Promote 8 non-31.105 IPs from reserve into active.
  5. Final active "proxies" list must have EXACTLY 16 entries (Chrome count
     == len(active)).
  6. Validate:
       python -c "import json; c=json.load(open('config/proxyIps.json')); \
                  assert len(c['proxies'])==16, f'active must be 16, got {len(c[\"proxies\"])}'; \
                  bad=[p for p in c['proxies'] if '-31.105.' in p]; \
                  assert not bad, f'31.105 leaked into active: {bad}'; \
                  print('OK: 16 active, none on 31.105')"

State file cleanup:
  After swapping IPs, state/proxy_state.json references the OLD active IPs by
  pinned_ip. If state/proxy_state.json exists on this machine (Mac has only
  .bak files; Windows runtime box should have a live one):
    - Check mtime first. If a bot is actively running (mtime < 5 min ago),
      STOP and ask the user to shut down the bot before continuing.
    - Backup: cp state/proxy_state.json state/proxy_state.json.bak_subnet_swap_<YYYYMMDD>
    - Delete the live file so the bot rebuilds state for the new IPs.

══════════════════════════════════════════════════════════════════════════════
FIX #3 — Phase 1 commit point
══════════════════════════════════════════════════════════════════════════════

Stage and commit Phase 1 config changes:

  git add config/product_config.json config/product_catalog.json \
          config/pokemon_target_seed.csv config/pokemon_target_seed_filtered.csv \
          config/proxyIps.json

  git status   # confirm nothing else is staged

Commit message (HEREDOC):

  config: drop ghost TCIN + rotate 31.105.x.x subnet out of active pool

  - Remove TCIN 94300069 from product_config.json, product_catalog.json,
    and both pokemon_target_seed CSVs (product does not exist on Target).
  - Rotate 5 active 31.105.x.x IPs to reserve; 3 burned IPs removed entirely
    (31.105.155.55, .183.245, .153.101). Promote 8 non-31.105 spares to active.
  - Backup at config/proxyIps.json.bak_subnet_swap_<date>.

  Diagnosed from 5/21 overnight run: subnet burn across 31.105/16 banned
  3 IPs in 90s while other 13 IPs on different subnets had 0 403s.

Do NOT push. Leave for user review.

══════════════════════════════════════════════════════════════════════════════
FIX #4 — Raise CONSECUTIVE_ERROR_RECYCLE_THRESHOLD from 5 to 10 (5 min)
══════════════════════════════════════════════════════════════════════════════

File: src/session/multi_session_pool.py:143

Background: At threshold=5, the 5/21 run had all 16 sessions independently
trip and cascade-recycle within 13 minutes during one global transient stall.
The watchdog already paces recycles 1-per-30s — but only AFTER sessions are
already marked crashed. Raising the per-session trip threshold absorbs short
transient blips before they cascade.

Edit line 143:
  CONSECUTIVE_ERROR_RECYCLE_THRESHOLD = 5    →    CONSECUTIVE_ERROR_RECYCLE_THRESHOLD = 10

Also update the comment block at lines 141-142 to reflect the new value
(replace any mention of "5 consec errors" with "10 consec errors").

Validate Python parses:
  python -c "import ast; ast.parse(open('src/session/multi_session_pool.py').read())"

Phase 2 commit:

  git add src/session/multi_session_pool.py
  git commit -m "$(cat <<'EOF'
  fix(pool): raise consecutive-error trip threshold 5 → 10

  Absorbs transient global stalls (network blips, OS scheduler hiccups)
  before they cascade the whole pool. The 5/21 run hit a 13-min outage
  when all 16 sessions independently tripped within minutes of each other
  during what was likely one short global stall.

  Watchdog still paces recycles at 1-per-30s, so worst case is still bounded.
  EOF
  )"

══════════════════════════════════════════════════════════════════════════════
FIX #5 — Subnet-aware proxy selection in pick_session() (~1 hr)
══════════════════════════════════════════════════════════════════════════════

File: src/session/multi_session_pool.py around line 505 (pick_session method)

Background: Right now pick_session() does `random.choice(ready)` — all
ready sessions weighted equally regardless of subnet. If Shape starts
souring a /16, the random picker keeps sending traffic into the
hot-zone. We want to detect subnet-level trouble and steer away.

Implementation:
  1. Add a helper that returns the /16 of a session's pinned IP:
       def _subnet_16(ip: str) -> str:
           parts = ip.split(".")
           return f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else ""

  2. Add a field to SessionEntry (around line 170) tracking recent 403
     count over a rolling 5-min window. Simplest impl: a deque of
     timestamps of 4xx responses, pruned to last 300s on read. Method
     `recent_4xx_count() -> int`.

  3. In tab_dispatcher.py, where consecutive_errors is incremented on
     4xx (search for `consecutive_errors += 1`), also append a timestamp
     to the new deque when http_status in (401, 403).

  4. Modify pick_session(): before random.choice(ready), compute the
     /16 of each ready session. If any /16 has >= 1 session with
     recent_4xx_count() >= 2 within the last 300s, exclude ALL sessions
     in that /16 from the pick. If exclusion would leave the pool empty,
     fall back to random.choice(ready) (better to use a possibly-hot IP
     than to halt monitoring entirely).

  5. Log when subnet exclusion fires so we can see it working:
       logger.info(f"[POOL] subnet {hot_16} excluded from pick "
                   f"(recent 4xx: {count}); pool reduced to {len(filtered)}")

  Validation: Python parses. Add a quick unit-style smoke at the bottom
  of the file (or in tests/) that constructs a fake pool with 3 sessions
  on "1.2.x.x" and 3 on "3.4.x.x", appends 2 timestamps to one session
  in 1.2.x.x, and asserts pick_session() never returns a 1.2.x.x session
  over 100 calls. If you write the smoke as a script, run it once and
  delete it after — do not commit the smoke.

Phase 3 commit:

  git add src/session/multi_session_pool.py src/monitoring/tab_dispatcher.py
  git commit -m "$(cat <<'EOF'
  feat(pool): subnet-aware proxy selection — exclude hot /16s

  Track a 5-min rolling 4xx count per session. When any session in a /16
  shows ≥2 recent 4xx, all sessions on that /16 are temporarily excluded
  from pick_session(). Falls back to random pick if exclusion would
  empty the pool.

  Directly addresses the 5/21 subnet-burn pattern: 3 IPs on 31.105.x.x
  burned in 90s while the random picker kept feeding traffic to the
  remaining 5 IPs on the same /16.
  EOF
  )"

══════════════════════════════════════════════════════════════════════════════
FIX #6 — Windows orphan-Chrome killer in _launch_persistent_one (~45 min)
══════════════════════════════════════════════════════════════════════════════

File: src/session/multi_session_pool.py:_launch_persistent_one (~line 314)

ONLY APPLIES ON WINDOWS. Wrap the new logic in `if sys.platform == "win32":`
so Mac/Linux behavior is unchanged.

Background: Sessions s1/s6 on the 5/21 Windows run each cycled 80+ times
with "Browser stderr: No output from browser". Almost certainly zombie
chrome.exe processes holding the user_data_dir lock file.

Implementation:
  1. Add a counter on SessionEntry: `consecutive_launch_failures: int = 0`.
     Bump it in the launch-failed except block (line 368-372); reset to 0
     on successful launch (after line 363 `s.state = "ready"`).

  2. Before launching (top of _launch_persistent_one, after `s.state =
     "starting"`), if consecutive_launch_failures >= 5 AND on Windows:
       a. Log: f"[POOL] {s.id} launch failed {n} times — purging orphans"
       b. Run subprocess (non-blocking, timeout=10s):
            taskkill /F /FI "IMAGENAME eq chrome.exe" /FI "WINDOWTITLE eq *"
          Filter further by user_data_dir if feasible (taskkill alone
          doesn't natively filter by command-line; document this and
          ship the broader kill — it's recovery-mode only, not steady
          state).
       c. Wait 2s for OS cleanup.
       d. If consecutive_launch_failures >= 10, ALSO rotate the profile
          dir name:
            old = s.profile_dir
            new = old.with_name(old.name + f"_b{int(time.time())}")
            try: shutil.move(str(old), str(new))
            except OSError: pass
            s.profile_dir = old   # keep canonical path; the rotated
                                   # corrupted dir is now out of the way
       e. Reset consecutive_launch_failures = 0 after recovery actions.

  3. Imports needed: subprocess, shutil, sys (most already imported —
     check the top of the file).

  Validation: Python parses. Document the behavior in a one-line comment
  block above the new block. Do NOT add taskkill calls to launch paths
  that don't need it — only fire after the 5-failure threshold.

Phase 3 commit (or separate commit if you prefer):

  git add src/session/multi_session_pool.py
  git commit -m "$(cat <<'EOF'
  fix(pool): Windows orphan-Chrome cleanup on chronic launch failures

  After 5 consecutive launch failures on Windows, taskkill orphaned
  chrome.exe processes that may be holding the profile-dir lock. After
  10 consecutive failures, also rotate the profile dir name. Mac/Linux
  behavior unchanged.

  Diagnosed from 5/21 Windows run where s1/s6 entered 80-cycle launch-
  failure loops over the last 3 hours, dropping pool from 16 to 5 healthy.
  EOF
  )"

══════════════════════════════════════════════════════════════════════════════
FIX #7 — Nightly auto-restart wrapper (Windows ops, no code) (~20 min)
══════════════════════════════════════════════════════════════════════════════

Not a code change to commit. This is an operational setup the user runs on
the Windows host to gracefully restart the bot every 24 hours.

Create the script `run_bot_with_nightly_restart.bat` in the repo root
(it CAN be committed if the user wants it tracked):

  @echo off
  REM Runs app.py with nightly restart at ~04:00 local. Wraps the bot in
  REM a loop that exits each call after 23h, letting Windows clean up
  REM zombie chrome.exe processes between runs.

  :restart_loop
  echo [%date% %time%] Starting Target bot run...
  python app.py
  echo [%date% %time%] Bot exited. Sleeping 30s before restart...
  timeout /t 30 /nobreak
  goto restart_loop

For the 23h cutoff, the cleanest path is for app.py to honor a
MAX_RUNTIME_HOURS env var (graceful self-shutdown). If that env var is
not already wired in app.py, ASK the user before adding it — they may
prefer to use Task Scheduler instead. Quick check: search app.py for
"MAX_RUNTIME" or any time-based shutdown — if absent, the simplest
patch is a background asyncio task that calls the existing
shutdown_handler after `MAX_RUNTIME_HOURS * 3600` seconds.

DO NOT auto-implement the env var without asking. Just stage the .bat
file and the prompt for the user.

══════════════════════════════════════════════════════════════════════════════
FIX #8 — Verify the purchase path with TEST_MODE smoke (operational, ~15 min)
══════════════════════════════════════════════════════════════════════════════

NOT a code change. Operational verification step.

Background: No successful real-mode purchase has happened since 2026-05-15.
Three fix commits landed since (1fd69cd0 CDP health-check, e2b4dce6 Shape
pool expansion, 8c5b007d cookie fix). We don't know if the ATC → checkout
path is still working until a real in-stock event hits — UNLESS we run a
TEST_MODE purchase smoke.

Steps the user will run (do not run for them; PRINT this section as
output and let them execute):

  1. Confirm test_app.py is the test entry point (verify by reading CLAUDE.md
     section "Test Before Deploy").
  2. Pick a known-available TCIN for the smoke — the existing TCIN 50270379
     (Extra Sugar-Free Polar Ice Mint gum, enabled in product_config) has
     been used for prior smoke tests.
  3. Confirm .env has valid TARGET_EMAIL / TARGET_PASSWORD / CARD_CVV (or
     the equivalents on this branch — check `git grep TARGET_EMAIL`).
  4. Run:
       python test_app.py
     and click through the TEST_MODE purchase trigger in the dashboard
     (or use whatever automated TEST_MODE harness exists — check
     tests/ folder for test_idle_then_detect_then_purchase.py which
     covers this flow).
  5. Watch for:
       - ATC POST returns 200/201
       - pre_checkout POST returns 200
       - Place Order succeeds (status 200, order_id parsed)
     If any step 4xxs, capture the log and report back rather than
     attempting fixes blind — purchase-path debugging is multi-system.

Print the above to the user. Do NOT run it for them.

══════════════════════════════════════════════════════════════════════════════
FINAL STATE
══════════════════════════════════════════════════════════════════════════════

After all 8 fixes:
  - 3 commits on the working branch (Phase 1 config, Phase 2 threshold,
    Phase 3 code), all UNPUSHED for user review.
  - Active proxy pool has 16 IPs, 0 on 31.105.x.x.
  - Pool no longer cascades on transient global stalls (threshold 10).
  - Pool steers away from hot subnets automatically.
  - Windows launch-failure loops break out via taskkill + profile rotation.
  - Operational setup (nightly restart, TEST_MODE smoke) handed off to
    user with explicit instructions.

Print a closing summary listing the three commit SHAs and remind the user:
  - The bot still runs at 3.0 RPS by default (TARGET_SWEEPS_PER_SEC=3.0).
    No rate change.
  - Push commits with `git push origin feat_refract_arch_v1` ONLY after
    user reviews.
  - Run the TEST_MODE smoke (Fix #8) before the next Pokemon Wednesday.

══════════════════════════════════════════════════════════════════════════════
ABORT CONDITIONS — stop and ask the user before continuing if:
══════════════════════════════════════════════════════════════════════════════

  - Any JSON file fails to parse after edits.
  - Any Python file fails AST parse after edits.
  - The git status shows unexpected staged changes from another branch.
  - state/proxy_state.json mtime is < 5 minutes old (bot is running).
  - reserve_proxies has fewer than 8 non-31.105 IPs to promote.
  - You can't locate any of the placeholder-named TCINs the prompt
    references (config may have drifted since the prompt was written).
  - app.py does NOT honor a MAX_RUNTIME env var and the user has not
    been consulted about adding one (Fix #7).
  - The pick_session implementation in src/session/multi_session_pool.py
    has been refactored since this prompt was written and `random.choice
    (ready)` is no longer the picking primitive (Fix #5 anchor would be
    invalid).

End-of-run summary: print a one-paragraph recap with commit SHAs, file
list, anything skipped or deferred, and the two operational steps left
for the user (Fix #7 setup + Fix #8 smoke).
```

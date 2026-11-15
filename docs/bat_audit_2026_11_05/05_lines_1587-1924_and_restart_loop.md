# Audit chunk 05: run_bot_with_nightly_restart.bat lines 1587-1924 (lane flags, session-heal flags, pre-launch steps, restart loop)

Audited 2026-11-05. Source file: `run_bot_with_nightly_restart.bat` (the "wrapper"). Verification greps were run against `src/`, `app.py`, `tools/`, `relogin_one.py`, `chrome_target_signout.py`.

## 1. Overview

This is the tail of the launcher. It does four jobs.

- (a) It sets the "independent lane" feature flags (`TARGET_LANE_*`): an alternative architecture, currently OFF, where each account fires add-to-cart (ATC) requests from its own proxy IP with its own anti-bot (Shape) tokens.
- (b) It sets session and credential-hygiene flags (token keep-fresh, scripted re-login cap, sign-out restore, SIGINT attribution) and the `ENSURE_ARMED_*` flags used by the exit-87 recovery branch.
- (c) It runs one-time pre-launch steps outside the loop: venv check, single-instance guard, personal-Chrome Target sign-out, account login pass (`relogin_one.py all`), drop-readiness echo.
- (d) It contains the crash-supervision loop (`:loop`, line 1818) that launches `app.py` and decides from the exit code whether to stop, run a relogin recovery, or just relaunch.

In security terms: (a) and (b) shape the bot's outward fingerprint and credential-replay behaviour (how many "independent shoppers" Target's edge rate limiter and Shape see, and how often we touch the login surface). (d) is a process supervisor (watchdog) whose job is availability without creating a login storm or a double instance that burns the proxy pool's reputation.

Cosmetic note: the file has mojibake (`?` where an em dash was). Harmless.

## 2. Table of every `set` line in the range

Line numbers are exact (from `grep -n "^set [A-Z]"`). All are environment variables consumed by the bot unless noted. "Read in code?" detail is in section 6.

| Line | Variable | Value as set | Purpose | Kill switch (per comments) |
|---|---|---|---|---|
| 1614 | TARGET_LANE_MODE | 0 | Master enable for the lane pool (parallel independent ATC shooters) | Already 0. "Kill: TARGET_LANE_MODE=0 (exact prior fleet behaviour)" |
| 1615 | TARGET_LANE_FAST_MIN_S | 3.0 | Min retry delay after an edge 429, per lane | none (cadence knob) |
| 1616 | TARGET_LANE_FAST_MAX_S | 4.5 | Max retry delay after an edge 429 | none |
| 1617 | TARGET_LANE_COOL_401_S | 25.0 | Cool-off after a streak of Shape 401s | none |
| 1618 | TARGET_LANE_COOL_401_STREAK | 3 | Consecutive 401s that trigger the cool-off | none |
| 1619 | TARGET_LANE_RACE_BUDGET_S | 110 | Max time one lane spends on one TCIN (product id) | none |
| 1637 | TARGET_LANE_IPS | all_resi | Which proxies lanes use (entire residential pool) | none; empty = all reserve + all residential |
| 1639 | TARGET_LANE_HARVEST_INTERVAL_S | 4.0 | Shape-token harvest cadence per harvester Chrome | none |
| 1640 | TARGET_LANE_TOKEN_TTL_S | 300 | Token lifetime in the bank | none |
| 1646 | TARGET_LANE_BANK_NEWEST_FIRST | 1 | Draw the freshest Shape token first | set 0 (code default 0) |
| 1647 | TARGET_LANE_TOKEN_MAX_USE_AGE_S | 90 | Refuse to fire a token older than 90 s | set 0 (code default 0 = no bound) |
| 1650 | TARGET_LANE_CHROME_MAX_AGE_S | 780 | Proactive harvester Chrome relaunch age (clamped to 720 s on residential in code) | none |
| 1653 | TARGET_LANE_STALL_FAILS | 3 | Consecutive failed harvests before a reactive relaunch (capped 6/hour per comment) | none |
| 1656 | TARGET_LANE_PX_PROBE | 1 | Read-only PX/HUMAN wall check on the harvester PDP; relaunch a fresh guest profile on a wall | set 0 |
| 1657 | TARGET_LANE_COOKIE_REFRESH_S | 60 | Lane cookie refresh interval | none |
| 1659 | TARGET_LANE_WARMUP_TOKENS | 5 | Tokens each harvester must produce before the first race | none |
| 1660 | TARGET_LANE_WARMUP_TIMEOUT_S | 120 | Warmup timeout | none |
| 1666 | TARGET_RELOGIN_RESTORE_ON_FAIL | 1 | Snapshot cookie jar before sign-out; restore it if the login fails | `=0` |
| 1691 | TARGET_TOKEN_KEEPFRESH | 0 | In-bot keep-fresh of the member token (OFF) | comment: set back to 1 once minting works again |
| 1699 | TARGET_RELOGIN_MAX_PER_6H | 0 | Cap on in-bot scripted credential relogins per 6 h; 0 blocks them | "Kill: delete this line" (but code default is 2, see drift) |
| 1706 | TARGET_TOKEN_MINT_LOG | 1 | Log-only: record Target's HTTP answer to the member-token mint rungs | set 0 |
| 1713 | TARGET_START_VALIDATE_RETRIES | 2 | Retry start-pass /account validation only when the page load raises | set 0 |
| 1719 | TARGET_SIGINT_ATTRIB | 1 | Log-only: one attribution line on Ctrl+C / SIGTERM | set 0 |
| 1870 | ENSURE_ARMED_EXIT87 | 1 | In the exit-87 branch, run `tools\ops\ensure_armed.py --mode exit87` (forced login for GUEST accounts) | edit this line to 0 (it overwrites any outside env value every pass) |
| 1877 | ENSURE_ARMED_PROFILE_SIGNALS | 1 | Treat a GUEST (sut=G) relogin label or boot-probe failure as "this profile needs its one forced login" | set 0 here AND `reg delete HKCU\Environment /v ENSURE_ARMED_PROFILE_SIGNALS /f` |
| 1883 | ENSURE_ARMED_ROWLOG | 1 | Log-only `[BOOTROWS]` line naming failing boot_check rows | set 0 here AND reg delete (HKCU) |
| 1890 | ENSURE_ARMED_STATUS_FAILCLOSED | 1 | A failed `bot_ctl status` read counts as unknown, not "not running", so ensure_armed touches nothing | set 0 here AND reg delete (HKCU) |

Not env config: wrapper-internal batch variables `ATTEMPT` and `RELOGIN_BURSTS` (set /a, line ~1815) and `EXITCODE` (line 1825). Kill-switch variables only READ by the batch in this range, never set: `CHROME_SIGNOUT_SKIP` (read by chrome_target_signout.py), `SINGLE_INSTANCE_GUARD_SKIP` (line 1752), `DROP_READINESS_SKIP` (line 1809), `WRAPPER_RELAUNCH_ON_CLEAN_EXIT` (line 1841).

Count: 27 env flags set in the range (17 lane + 6 session/credential + 4 ENSURE_ARMED), plus 4 read-only switches.

## 3. Narrative by section

### 3.1 Independent lane firing (2026-10-24 / 10-25 / 10-26 / C-1027), all inert while MODE=0

Concept. Today the fleet fires from one purchase manager using a wave-first policy (a 55-70 s fleet-wide sleep after a refusal). Target's edge limiter is per-TCIN and site-wide, but Shape (F5's client-attestation token) is per session. The lane design makes each account-by-IP combination an independent "shopper" with its own fast retry (3-4.5 s) on its own IP bucket, so a 401 on lane A says nothing about lane B. It was framed as a "pen-test / limiter stress-test".

Architecture (v2, 10-25): one guest Chrome "harvester" per unique exit IP continuously mints Shape tokens; "shooters" (one per account x IP, using curl_cffi, a TLS-impersonating HTTP client with no browser) fire ATC using those tokens; a 201 cart hands off to that account's existing Chrome for checkout.

Security view: this is token replay at scale. A token minted in a real browser is spent by a non-browser client. Risks: (1) fingerprint split, because the curl_cffi TLS/JA3 must be consistent with what Shape saw at mint time; (2) staleness, hence `TOKEN_MAX_USE_AGE_S=90` (the "proven path's own 90 s bound") and `BANK_NEWEST_FIRST=1`; (3) harvester Chrome degrades on IPRoyal residential after 17-21 min (C-1023-12 "CDP freeze"), hence the 720 s clamp; (4) the HUMAN/PerimeterX wall is profile-bound (C-1023-14), hence PX_PROBE relaunching a fresh guest profile.

Dated claims (may be stale): the 10-23 drop refused 119/119 shots on BOTH home and residential IPs (C-1023-10), so "residential beats home IP" is explicitly UNPROVEN as of 10-26 and "measure it at a live drop before MODE=1". 10-26 decision: residential for shooting, ISP/BD sweep pool for monitoring (monitor untouched by `TARGET_LANE_IPS`).

Why these values: 3.0-4.5 s is the "fast per-lane retry"; 25 s cool-off after 3 consecutive 401s limits hammering a burned session; 110 s race budget bounds one TCIN per lane; warmup 5 tokens / 120 s ensures harvesters are producing before the first race. C-1027 additions (NEWEST_FIRST, MAX_USE_AGE) are pre-armed "for when lanes go live".

Drop-day risk: with MODE=0 none of this runs (app.py:981 gates on exactly `'1'`). If MODE is flipped on untested, the bot adds up to accounts x IPs concurrent sessions: a much larger request volume and IP count, and a bigger blast radius for a pool-reputation burn (the 09-04 incident cited later in the file: two instances at ~6 req/s poisoned the Bright Data pool's HUMAN standing for days). The lane path has never run live (commit 438153e8 title), though a cross-path double-fire guard exists (src/purchasing/purchase_claim.py).

### 3.2 Non-destructive relogin (item 7): RESTORE_ON_FAIL=1

Incident: a 20:24 sentinel escalation signed the primary account OUT before the Shape-burned login failed, leaving a GUEST token for the next drop. Fix: snapshot the cookie jar before sign-out; restore it if the login fails. Risk if wrong: low; it is a fail-safe, and the code default is also 1.

### 3.3 Token keep-fresh and scripted relogin (2026-07-12 through 2026-10-20)

Comment history: on 07-12 keep-fresh was turned ON as "the #1 lever" (member token kept hot 24/7; the ATC 401 is the write-auth/member-token layer, not Shape). On 09-28 pre-drop it was turned OFF because, since 2026-09-25 about 06:00, nothing in the bot can mint a member token (rung 1 token_refresh 404 on 253/253 tries; rung 2 /account reload 0/12). With keep-fresh on, the repair step DELETED live tokens about 3.5 h after mint, and false 401x3 heartbeat verdicts deleted more (3 on 09-28), so tokens died before their natural 4 h life (C-0928-02/03/04).

`TARGET_RELOGIN_MAX_PER_6H=0` stops all in-bot scripted logins: 0 of 6 succeeded on 09-28 (username did not advance) and each attempt cleared live cookies and left the account GUEST (idToken sut=G). The wrapper-start `relogin_one.py` pass is explicitly not affected.

`TARGET_TOKEN_MINT_LOG=1` (C-0925-04, log only) records what Target answered when minting stopped (mint rate went from 80/80 to 0/62 at 06:01 on 09-25 on all three accounts). `TARGET_START_VALIDATE_RETRIES=2` (FX-1018-STARTVALIDATE, C-1018-17) retries the start pass's /account check only on raised exceptions (CDP/websocket errors), never on a login redirect or guest page, so a transient exception does not sign a MEMBER profile out. `TARGET_SIGINT_ATTRIB=1` (build C-1018-37, armed 10-20) logs who/what killed the bot (idle time, foreground window, bot_ctl stamp age); log only, exit paths unchanged.

Security framing: these flags trade availability for minimal credential surface. Every scripted login is a hit on the login endpoint that Shape/HUMAN scores, and failed ones also destroy the good session. The operator chose "do not log in from inside the bot".

Drop-day risks: (1) a member token lives 4 h. With keep-fresh OFF and in-bot relogin OFF, a bot started more than about 4 h before a drop may hold a dead token unless the wrapper-start pass minted one; the sentinel falls back to the pre-07-07 /account navigation check. (2) These findings are dated 09-28; this range does not say whether minting was repaired since. Treat as potentially stale and check the latest post-run.

### 3.4 Pre-launch steps (outside the loop)

- venv check (line ~1722): if `%PYTHON%` is missing, echo an error, wait about 30 s via `ping -n 31` (bounded; `pause` hangs on unattended boot and `timeout` returns instantly without a console), then `exit /b 1`.
- Personal-Chrome sign-out (2026-07-13): the operator's own Target login is the bot's `primary` account, so normal browsing is a SECOND live session and Target rotates the member token out from under the bot (07-10 guest-churn; 48 target.com cookies incl. a live refreshToken seen 07-13). "Remember to sign out" failed three sessions, so `chrome_target_signout.py` closes Chrome, deletes ONLY target.com cookies, reopens Chrome with tabs restored. Never fatal. Kill: `CHROME_SIGNOUT_SKIP=1`.
- Single-instance guard (2026-09-08), line 1752: `check_single_instance.py` is read-only and exits 9 if another app.py is running; the wrapper logs and `exit /b 9` after 30 s. Reason: the 09-04 disaster. Kill: `SINGLE_INSTANCE_GUARD_SKIP=1`. `ver >nul` first resets a stale ERRORLEVEL (09-09 fix). Only exit code 9 blocks; any other guard failure lets the launch proceed (fail-open).
- Account login pass (line 1794), once, deliberately outside the loop: `relogin_one.py all`, validate-first silent refresh of living sessions; only dead ones get a credential login. Outside the loop because re-running logins on each relaunch is a login storm that trips Shape new-device challenges. Skipped when `config\target_accounts.json` is absent. Uses `!ERRORLEVEL!` (delayed expansion) because `%ERRORLEVEL%` expands at parse time of the whole block.
- Drop-readiness echo (2026-07-14), line 1809: `check_session_readiness.py`, informational only, reads the harvested jars, no browser. Kill: `DROP_READINESS_SKIP=1`.

## 4. The restart loop

### 4.1 What launches

`"%PYTHON%" app.py` (line 1824), no arguments. `%PYTHON%` is `.venv\Scripts\python.exe`, else `venv\Scripts\python.exe` (lines 31-32). The exit code is captured immediately into `EXITCODE`; everything is appended to `logs\bot_restart_wrapper.log`.

### 4.2 Correction: there is no timed nightly restart

The filename says "nightly restart", but the header (lines 8-10) says "There is intentionally NO timed runtime cutoff: the operator stops the bot manually each morning". Nothing in the batch schedules a restart. So "when does the nightly restart fire?": it never fires on a clock. The loop is a crash-restart supervisor only. A restart happens when app.py exits non-zero, or when a human or `bot_ctl.py restart` stops and starts it. The only time-based actors are Windows scheduled tasks running `ensure_armed.py` (evening mode, per the ENSURE_ARMED comments), which live outside this file.

### 4.3 Flowchart

```
 [wrapper start]
      |
 venv python found? --no--> echo error, wait 30 s, EXIT 1
      | yes
 single-instance guard (check_single_instance.py)
      |-- exit 9 --> log, wait 30 s, EXIT 9
      | else
 chrome_target_signout.py   (never fatal)
 relogin_one.py all         (once; only if target_accounts.json exists)
 check_session_readiness.py (info only; skipped if DROP_READINESS_SKIP=1)
      |
 ATTEMPT=0, RELOGIN_BURSTS=0
      v
 +---------------------- :loop (line 1818) <------------------------------+
 | ATTEMPT += 1 ; log "launch #N"                                         |
 | python app.py   (blocks until it exits)                                |
 | EXITCODE = errorlevel ; log it                                         |
 |                                                                        |
 | EXITCODE == 0 AND WRAPPER_RELAUNCH_ON_CLEAN_EXIT != 1 ?                |
 |      yes --> log "clean exit (operator stop)", wait ~5 s, :done        |
 |              ---> endlocal & exit /b 0              [EXIT 0]           |
 |      no  v                                                             |
 | (set the four ENSURE_ARMED_* vars)                                     |
 | EXITCODE == 87 ?                                                       |
 |   yes: RELOGIN_BURSTS += 1                                             |
 |        RELOGIN_BURSTS >= 4 ? --> sleep 10 min ; RELOGIN_BURSTS = 0     |
 |        ENSURE_ARMED_EXIT87=1 ? --> ensure_armed.py --mode exit87       |
 |        accounts file exists ? --> relogin_one.py all                   |
 |   no : RELOGIN_BURSTS = 0                                              |
 | echo "Restarting in 10s"; ping -n 11 (~10 s)                           |
 | goto loop  ----------------------------------------------------------->+
 +------------------------------------------------------------------------+
```

### 4.4 Exit paths (every way the wrapper ends)

1. venv python missing: `exit /b 1` after 30 s (line 1727), before any launch.
2. Single-instance guard tripped (guard exit 9): `exit /b 9` after 30 s (line 1760).
3. Clean stop: app.py exits 0 and `WRAPPER_RELAUNCH_ON_CLEAN_EXIT` is not 1: `goto :done` -> `endlocal & exit /b 0` (line 1924).
4. cmd-level interrupt: Ctrl+C at the console can raise cmd's own "Terminate batch job (Y/N)?" prompt; Y ends the wrapper (errorlevel set by cmd, not coded here). Hard kill of cmd.exe likewise.
5. External: `bot_ctl.py stop` kills wrapper, app.py and bot Chromes if the graceful path fails (4.6).
6. Nothing else. Every non-zero exit code (1 uncaught, 2 deadman, 87 not-logged-in, 0xC0000005 access violation, anything) relaunches after about 10 s. There is no max-attempt counter and no escalating backoff beyond the fixed 10 s (plus the 10 min cool-down inside the 87 branch). Security/ops note: a persistently crashing app.py is relaunched forever, each relaunch re-touching Target and the proxies. `ping -n 11` is used for the sleep because `timeout` returned instantly without a true console and caused a 0.27 s/relaunch crash loop on 2026-05-22.

### 4.5 Scenarios

- Bot crashes (exit 1, 0xC0000005, etc.): logged, 10 s sleep, `goto loop`, launch #N+1. Account logins are NOT repeated (deliberate, to avoid a login storm). If the crash cause is persistent this is an unbounded loop. If it was an access violation caused by the old Chrome still holding `--user-data-dir`, 10 s may not be enough (the 09-11 failure mode).
- Not logged in (exit 87): app.py exits 87 itself (app.py:5063, gated by `TARGET_EXIT_ON_LOGIN_HALT`, default on) after killing its Chromes and noting Worker 1 in a boot-skip list. The wrapper increments the burst counter; at 4 it sleeps 10 min and resets; runs `ensure_armed.py --mode exit87` (forced login for GUEST accounts, at most 1 per account per 6 h); runs `relogin_one.py all`; sleeps 10 s; relaunches.
- Ctrl+C in the wrapper console: Windows delivers the event to every process on the console. app.py's handler (app.py:5141+) saves session, closes browsers, ends with `os._exit(0)` (line 5226). A second Ctrl+C inside app.py goes straight to `os._exit(1)`; a 15 s deadman thread force-exits with code 2 if cleanup wedges. Wrapper outcome: exit 0 = clean stop, wrapper ends. Exit 1 or 2 (double Ctrl+C or hung shutdown) is treated as a crash and the bot is RELAUNCHED unless cmd's "Terminate batch job" prompt is answered Y. This is a real gap against operator intent; the 09-15 fix covers only the exit-0 path.
- OS logoff or shutdown: app.py registers SIGINT, SIGTERM and SIGBREAK handlers (app.py:5228-5232). The batch comment says clean exit 0 happens when "the OS is logging off". I could not verify that Windows logoff/shutdown delivers any of those to a Python console app (it arrives as a console logoff/shutdown event that Python does not map to these signals, and the process is typically just terminated). If everything is killed in the logoff, the wrapper dies too and nothing relaunches, so the claim is moot rather than harmful. UNVERIFIED.
- Nightly restart: does not exist (4.2).
- Orphan processes: the 09-11 incident (launch #3 ran as an unsupervised orphan) and the pool-Chrome leak are the cited cases. Mitigations are in app.py: `_kill_browser_now` reaps pool Chromes and the purchase Chrome (app.py:5088+, also atexit), the exit-87 path calls it before `os._exit(87)`, and the single-instance guard prevents a second app.py. The wrapper itself does not kill stray python or Chrome between launches. `bot_ctl.py stop` additionally kills wrapper, app.py and every bot-profile chrome.exe after the grace period.

### 4.6 bot_ctl.py interaction

`tools/ops/bot_ctl.py` exists and manages this loop from outside; it is not called by the bat. `start` launches this bat as `cmd.exe /c "<bat>"` in its own new console. `status` identifies the wrapper as a cmd.exe whose command line contains `run_bot_with_nightly_restart.bat` (bot_ctl.py:83). `stop` sends Ctrl+C to the wrapper's console through a helper that attaches to that console (bot_ctl.py:54-70, 172+), waits `--grace` (default 30 s) for app.py to exit (clean exit 0, so the wrapper stops instead of relaunching), then kills any leftovers. It stamps the stop so `TARGET_SIGINT_ATTRIB` can label it "bot_ctl stop" versus an unattributed console Ctrl+C. `restart` = stop + start. If the Ctrl+C is ignored because of an inherited CONSOLE_IGNORE_CTRL_C flag (bot_ctl.py:206-209), graceful save never runs and bot_ctl kills after the grace. `ensure_armed.py` consults `bot_ctl status`, which is why STATUS_FAILCLOSED matters.

## 5. Interactions worth knowing

- ENSURE_ARMED_* are read in two places: this bat (exit-87 branch) and the USER environment (via `setx`) for scheduled tasks. They must agree; the comments say to change both.
- ENSURE_ARMED_EXIT87 is consumed only by the batch (`"!ENSURE_ARMED_EXIT87!"=="1"`). The four ENSURE_ARMED lines are set inside the loop after the clean-exit test, so an outside value is overwritten each pass.

## 6. VERIFICATION (is the variable actually read in code?)

| Variable | Result | Evidence |
|---|---|---|
| TARGET_LANE_MODE | READ-IN-CODE | app.py:981 (`os.environ.get('TARGET_LANE_MODE','0').strip()=='1'`) |
| TARGET_LANE_FAST_MIN_S | READ-IN-CODE | src/lanes/lane_pool.py:188 |
| TARGET_LANE_FAST_MAX_S | READ-IN-CODE | src/lanes/lane_pool.py:189 |
| TARGET_LANE_COOL_401_S | READ-IN-CODE | src/lanes/lane_pool.py:190 |
| TARGET_LANE_COOL_401_STREAK | READ-IN-CODE | src/lanes/lane_pool.py:191 |
| TARGET_LANE_RACE_BUDGET_S | READ-IN-CODE | src/lanes/lane_executor.py:31 |
| TARGET_LANE_IPS | READ-IN-CODE | src/lanes/lane_pool.py:174 |
| TARGET_LANE_HARVEST_INTERVAL_S | READ-IN-CODE | src/lanes/lane_pool.py:193 |
| TARGET_LANE_TOKEN_TTL_S | READ-IN-CODE | src/lanes/lane_pool.py:192 |
| TARGET_LANE_BANK_NEWEST_FIRST | READ-IN-CODE | src/lanes/shape_harvester.py:200 |
| TARGET_LANE_TOKEN_MAX_USE_AGE_S | READ-IN-CODE | src/lanes/shape_harvester.py:189 |
| TARGET_LANE_CHROME_MAX_AGE_S | READ-IN-CODE | src/lanes/lane_pool.py:194; 720 s residential clamp at shape_harvester.py:55,82-90 |
| TARGET_LANE_STALL_FAILS | READ-IN-CODE | src/lanes/shape_harvester.py:312; reactive window 3600 s at line 62 |
| TARGET_LANE_PX_PROBE | READ-IN-CODE | src/lanes/shape_harvester.py:314 |
| TARGET_LANE_COOKIE_REFRESH_S | READ-IN-CODE | src/lanes/lane_pool.py:195 |
| TARGET_LANE_WARMUP_TOKENS | READ-IN-CODE | src/lanes/lane_manager.py:33 |
| TARGET_LANE_WARMUP_TIMEOUT_S | READ-IN-CODE | src/lanes/lane_manager.py:34 |
| TARGET_RELOGIN_RESTORE_ON_FAIL | READ-IN-CODE | src/session/session_manager.py:2154 (default '1') |
| TARGET_TOKEN_KEEPFRESH | READ-IN-CODE | src/session/session_manager.py:1852, 2442 (default '1') |
| TARGET_RELOGIN_MAX_PER_6H | READ-IN-CODE | src/session/session_manager.py:1886 (default '2') |
| TARGET_TOKEN_MINT_LOG | READ-IN-CODE | src/session/session_manager.py:1759 (default '0') |
| TARGET_START_VALIDATE_RETRIES | READ-IN-CODE | relogin_one.py:97 (default '0') |
| TARGET_SIGINT_ATTRIB | READ-IN-CODE | app.py:4894 and :5165 (compares `== "1"`) |
| ENSURE_ARMED_EXIT87 | NOT FOUND in any .py (batch-only by design) | consumed by the bat at the `"!ENSURE_ARMED_EXIT87!"=="1"` test; ensure_armed.py only has a `--mode exit87` argument. Not dead config. |
| ENSURE_ARMED_PROFILE_SIGNALS | READ-IN-CODE | tools/ops/ensure_armed.py:366, 391 |
| ENSURE_ARMED_ROWLOG | READ-IN-CODE | tools/ops/ensure_armed.py:376 |
| ENSURE_ARMED_STATUS_FAILCLOSED | READ-IN-CODE | tools/ops/ensure_armed.py:371, 397 |
| CHROME_SIGNOUT_SKIP (read-only switch) | READ-IN-CODE | chrome_target_signout.py:210 |
| SINGLE_INSTANCE_GUARD_SKIP (read-only switch) | NOT FOUND in .py; batch-only | bat line 1752 |
| DROP_READINESS_SKIP (read-only switch) | NOT FOUND in .py; batch-only | bat line 1809 |
| WRAPPER_RELAUNCH_ON_CLEAN_EXIT (read-only switch) | NOT FOUND in .py; batch-only | bat line 1841 |

Totals: 27 env flags set in range; 26 READ-IN-CODE; 1 NOT FOUND in Python (ENSURE_ARMED_EXIT87), which is consumed by the batch itself, so it is not dead config. Of the four extra switches, three are batch-only (consumed correctly) and one is read by Python. Genuinely dead config in this range: none found.

Related, read in code but not set here: `TARGET_EXIT_ON_LOGIN_HALT` (app.py ~5030, default on) controls whether exit 87 happens; `TARGET_RELOGIN_COOLDOWN_S` (session_manager.py:1885, default 1200).

## 7. Comment vs code drift

1. Burst counter. The echo says "relogin cycle N/3" and "3 relogin cycles failed", but the test is `GEQ 4` on a counter incremented before the test. The cool-down fires on the 4th consecutive exit 87, and the log will read "4/3". Cosmetic, but the effective behaviour is 3 recovery cycles plus a 4th that triggers the sleep (the 4th still then runs the recovery after sleeping).
2. Header and 09-15 comment say Ctrl+C stops the wrapper. True only for the single-Ctrl+C clean exit 0. A double Ctrl+C (exit 1) or a >15 s wedge (deadman exit 2) relaunches.
3. File name says "nightly restart"; header says there is no timed restart. The name is stale.
4. `TARGET_RELOGIN_MAX_PER_6H` "Kill: delete this line": the code default is 2, so deleting the line RE-ENABLES the scripted relogins the comment says wrecked sessions on 09-28. Misleading wording.
5. `TARGET_START_VALIDATE_RETRIES`: the code default is 0, so if the line is removed the retry silently disappears (no drift, a dependency).
6. The TARGET_TOKEN_KEEPFRESH block still contains the 07-12 text "Keep-fresh ON ... Kill-switch: set to 0" right above the 09-28 text saying it is OFF. The value 0 is consistent with the later text, but the first paragraph reads as if it is on.
7. The clean-exit comment's "or the OS is logging off" is unverified (see 4.5).
8. Checked and consistent: LANE_IPS empty = all reserve + residential (lane_pool.py:178-181); RACE_BUDGET 110 and other defaults match the code; CHROME_MAX_AGE 780 vs 720 s residential clamp matches the comment.

## 8. Readiness notes

1. Unbounded crash loop. Any persistent non-zero exit relaunches every ~10 s forever with no counter. A bad config edit before a drop would hammer Target and the proxies instead of stopping.
2. Double Ctrl+C or a hung shutdown relaunches the bot (exit 1 / 2). To stop reliably use `bot_ctl.py stop` and confirm "stopped".
3. Member-token dependency (dated 09-28). KEEPFRESH=0 and MAX_PER_6H=0 mean only the wrapper-start `relogin_one.py all` pass or a hand login can mint a member token, and it lives 4 h. Starting the wrapper more than about 4 h before a drop risks a dead token at fire time with no in-bot repair. Confirm in the latest post-run whether minting works again; if so these may be stale.
4. Lane mode is OFF and "NOT yet drop-validated" (10-26); C-1027 freshness flags are pre-armed. Ensure MODE stays 0 unless the operator decides otherwise; flipping it adds many sessions/IPs and a never-run-live path.
5. The Chrome target sign-out runs at every wrapper start and closes the operator's personal Chrome. A `bot_ctl restart` while the operator is browsing disrupts them.
6. The single-instance guard fails open on any non-9 failure (e.g. the guard script crashing).
7. ENSURE_ARMED_* must also be set in HKCU\Environment (setx) so scheduled tasks agree with the wrapper; verify with `reg query HKCU\Environment` before a drop.
8. The exit-87 path still performs scripted logins (ensure_armed exit87 and relogin_one.py all) even though `TARGET_RELOGIN_MAX_PER_6H=0` bans in-bot ones. This is intended (the comment says the wrapper pass is unaffected), and capped at one forced login per account per 6 h, but it is the one place logins can occur mid-run.

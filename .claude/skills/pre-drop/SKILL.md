---
name: pre-drop
description: Get the bot into the best possible position before a known drop window, then hand off a boot checklist. Runs the deterministic preflight (tools/predrop/preflight.py), the saved `pre-drop` Workflow (parallel audits of arming, code risk, money safety, monitor, regime and external research, with blind verification of every GO/NO-GO claim), lands only verified flag-gated changes behind the offline suite, proves the proxy pool through the production path, pre-registers the readout, then boots the bot itself at T-20 (tools/ops/bot_ctl.py restart, from one-shot in-session wake-ups) and follows the boot to GREEN (tools/predrop/boot_check.py --follow). Use in the hours before a drop, restock or launch.
argument-hint: "[drop time (local) and/or TCINs, optional]"
arguments: drop-target
---

# Pre-drop: best fighting chance, no new risk

Drop target: `$drop-target`. If it names neither a time nor TCINs, ask ONE question for
both (the drop moment in local time, and the TCINs wanted tonight), then do not ask again.
A time alone is enough to start: the config's enabled list is the target list until the
operator names changes.

**Read `.claude/agent-context.md` before anything else.** Its safety rules bind this whole
procedure, and §2B (the four lever checks) is what stops this skill from shipping a
confident mistake under time pressure.

Rebuilt 2026-10-08 (operator: "100% ready for the toughest challenge yet"). Every
pre-launch miss since 09-16 now has a deterministic check (appendix); the audit runs as a
Workflow with the same blind verification as `/post-run`; the boot is proven by a script,
not by eye.

## Non-negotiables

- **This skill boots the bot itself** (operator 2026-10-14), through one door only:
  `python tools/ops/bot_ctl.py restart` at T-20 (Step 9). It never starts a raw `app.py`,
  a second wrapper, or `relogin_one.py` outside the wrapper's own start pass, and never
  restarts inside the window unless the bot is dead.
- **Never run `validate_proxies.py` while the bot is up** (it shares the forwarder ports
  and launches 18 Chromes). Never run it from inside an agent or the Workflow.
- **The gate is `python tests/run_offline_suite.py`** (37 files, ~7 min). Never a blanket
  `tests/` run. Save its output under `logs/analysis_<date>/predrop/`.
- **Every change ships flag-gated with a byte-identical off-state**, or it does not ship.
  "It's only a log line" is not an exemption — log lines have exact-match assertions.
- **Startup-path code is special** (10-07: a diagnostics `AttributeError` in
  `StockMonitorThread.start()` killed the monitoring loop before `purchase_thread.start()`;
  the process looked alive and could not buy; 36/36 tests had passed). Any new statement
  on a boot path is try/except-guarded and prints, and the first live boot is read with
  `tools/predrop/boot_check.py` before anyone is told it is safe.
- **Verify before arming.** Hand the bare claim, with none of your reasoning, to
  `claims-verifier` (the Workflow does this for every deciding claim). `REFUTED` or
  `NOT ESTABLISHED` kills the change — or kills the reason you were not making it.
- **No routine manual steps.** A hand login is asked for ONLY when readiness prints ❌ (a
  dead login-session or a guest jar); an expired member token on a live session is
  refreshed by the bat's own start pass. Never build or propose automation of the sign-in
  itself (a safety classifier stopped that on 10-04).
- **The config's enabled TCINs are the operator's deliberate target list.** Add only what
  they ask for; never remove, never re-add a removed SKU, never a third-party
  (BlueProton / marketplace) listing. Candidate TCINs found by research are offered as a
  list for the operator to decide, in one line, once.
- **Auto-mode classifier denials are final** for that outcome. Do not re-route through
  another tool, script or agent. Restore the tree, record the blocked step, hand it to the
  operator.
- **"Play it safe" is not automatically safe.** Declining a verified, gated fix before a
  drop is a choice with a cost. Say what the cost is.

## Modes — decided by the time left, printed by the preflight

| Mode | Time to the drop | Allowed |
|---|---|---|
| FULL | > 3 h | everything below, including verified flag-gated code changes |
| SHORT | 1–3 h | preflight, Workflow with `max_claims: 0, research: false`, config edits, the proxy proof if the rule says so, the checklist. **No code changes.** |
| LAST-CALL | < 1 h | preflight + checklist. Nothing new is armed inside the last hour. |

---

## Step 1 — Preflight (deterministic, read-only, ~10 s)

```
python tools/predrop/preflight.py --drop "YYYY-MM-DD HH:MM" --tcins <csv of tonight's TCINs>
python tools/predrop/preflight.py --drop "..." --tcins ... --json > logs/analysis_<date>/predrop/preflight.json
```

It measures what used to be read by eye, the same way every night: repo (HEAD, dirty code,
CRLF / duplicate `set`, dead flags, flags changed since the last live boot = UNPROVEN
LIVE, `app.py` setdefaults), processes (a bot already running and its age, orphaned bot
Chromes holding a profile lock, Parsec / RDP), host (display and sleep never on AC,
screensaver, Windows Update pause + pending reboot, GPU / power events in 72 h, RAM, disk,
the Startup autostart), accounts (`check_session_readiness.py`, login-session projected to
the drop, payment prerequisites, CVV equal to the code default, boot-skip / CVV / AC-1
latches), config (≤ 28 TCINs, requested present, duplicates, qty pins, last-run
visibility, purchase-state latches), monitor (pool size, parked exits, `apps_raw`,
chunking, the exact proxy-proof command), last run (boot GREEN / RED, end, STATS, stalls,
RSS, money markers) and the drop (mode, Windows Update overlap, when to start).

Read every FAIL and WARN with its `->` fix line. The signatures that have cost nights:

| Row | Means | Do |
|---|---|---|
| `accounts.readiness:<acct>` FAIL | dead login-session or guest jar | the only case for `hand_login_<acct>_force.bat`; re-run the preflight after |
| `process.orphan_chrome` FAIL | a Chrome holds a bot profile with no bot running | close it; the next boot would die 0xC0000005 on the profile lock (09-11) |
| `host.display` / `host.sleep` FAIL | the display can sleep → a wake started 27/27 GPU-crash episodes (10-02 lost the first edge) | `powercfg /change monitor-timeout-ac 0` |
| `host.wu_pause` WARN, `host.wu_reboot` FAIL | Windows Update may restart the host in the window | pause updates a week; a pending reboot = reboot now |
| `config.requested_absent` FAIL | a wanted TCIN is not in the config at all (09-21, 09-30) | Step 4 |
| `config.count` FAIL / WARN | > 30 breaks the ground-truth read; 29–30 halves the refresh | keep ≤ 28; prefer ≤ 24 (the apps read is unproven above 24) |
| `repo.since_last_boot` WARN | flags or code changed since the tree last booted live | every changed flag is UNPROVEN LIVE: name its kill switch + first-exercise marker |
| `last_run.boot` FAIL | the last boot on this host could not buy | fix before the next boot (the 10-07 class) |
| `accounts.cvv_latch` / `ac1_latch` / `boot_skip_file` WARN | persisted state changes tonight's behaviour | read the row; decide with the operator |
| `process.remote` WARN | Parsec / RDP tooling is up | no remote session on the host during the window |
| `monitor.proxy_state` WARN | an exit is parked into tonight | it self-clears on a 200; note it; validate if several |

Everything else that is WARN goes into the report once, with its fix line; it does not
block.

## Step 2 — The audit Workflow (do not re-do its work by hand)

```
Workflow({ name: "pre-drop", args: { drop: "YYYY-MM-DD HH:MM", tcins: ["...", "..."], note: "<operator note>" } })
```

SHORT mode: add `max_claims: 0, research: false`. It is `.claude/workflows/pre-drop.js`
(read-only; it never edits, arms, runs the proxy proof or launches anything):

1. **Preflight facts** (log-miner, Sonnet): the preflight JSON, the last run's boot check,
   the event store (`regime`, `monitor_hours` like-for-like, `diag`, `coverage`) and the
   REGIME WATCH comparison.
2. **Audits in parallel** (Opus roster): `arming` (what changed since the last live boot,
   its kill switch and first-exercise marker; flags built and never decided — 09-30 booted
   with F1 undecided; REM headers contradicting the set line below them), `code-risk` (is
   HEAD the tree that booted GREEN? if not, every startup-path change and whether an
   exception there is caught), `money` (spend exposure with tonight's TCINs; every persisted
   latch; wrong-item guards; the R-MONEY rule), `monitor` (slot-hour loss vs the last two
   drop nights, 206 storms, parked exits, RSS at the drop, whether the pool must be
   re-proven — by the rule, never by running it), `regime` (REGIME WATCH + the carry-forward
   list: every "operator must / before the next run" item from the newest state sections and
   whether it was done — 09-28 booted with an account the post-run had said to log in),
   `antibot` when the last run had shots, and `research` (retailer-researcher: Refract's
   live Target docs vs the newest local copy; tracker reports for the window; candidate
   TCINs as a list for the operator).
3. **Verification of every deciding claim** (`decides: true`, ≤ `max_claims` per audit): a
   blind replicator that sees only a question, a refuter that sees only the bare claim, a
   neutral judge.
4. **Critic** — only a question that could flip GO / NO-GO, add or remove a BLOCKING
   operator action, or change which flag must be killed before boot starts another round;
   `max_rounds` (default 2) bounds it; unfinished gaps are logged, never dropped.
5. **Scorecard**: `decision_inputs` = `blocking_actions` (each with the verdicts of the
   claims it rests on), `other_actions`, `unproven_live_flags`, `boot_markers`,
   `readout_rules`, `stale_facts`, plus every report and judged claim.

Wait for the completion notification; save the result under
`logs/analysis_<date>/predrop/workflow_result.json` before reading it. If the Workflow
tool is unavailable or denied, fall back to the `/bot-investigate` fan-out with the same
five briefs (one Agent call each, in one message) and `claims-verifier` on each deciding
claim — do not skip verification because the orchestration changed.

## Step 3 — Decide (the main session, not an agent)

- **Regime first.** A flagged regime change goes to the top of the report and into
  `docs/TARGET_CHANGES.md` with the detection gap.
- **NO-GO** = any preflight FAIL still standing, or a blocking action whose claims are
  CONFIRMED / PARTIALLY CONFIRMED. A blocking action resting on a REFUTED claim is dropped
  and said so. Unverified blocking actions are reported as such — the operator decides.
- **Every built-but-undecided flag gets a decision tonight**, recorded in CURRENT_STATE as
  "armed" or "declined (why)". Not deciding is the 09-30 mistake.
- Apply §2B to every lever you are tempted to move: is it the binding constraint; re-derive
  the mechanism, not the recommendation; check the EFFECT of a restore; dump the real
  schema before parsing it. **Orders come from EARLY place-orders** (18/21 ≤ ~4.6 s after
  detection; > 2 min 0/150): any lever that only acts late is worth ≈ 0 units.
- Separate what the operator must do (host, hand login on ❌, TCIN decisions, Windows
  Update) from what you do (config, arming, proof, record, the T-20 boot).

## Step 4 — Config

- Back up first: `config/product_config_backup_pre_<date>_drop.json`.
- Add only the TCINs the operator asked for that the preflight marked absent: digit string,
  `enabled: true`, `qty: 2`, a name (nothing branches on it). Enable disabled ones only on
  request. Touch nothing else.
- Keep enabled ≤ 28 (one RedSky chunk — same-response flips are all visible to the first
  dispatch). Above 24 the unchunked apps ground-truth read is untested: say so, and watch
  `[GROUND-TRUTH] … ok` after boot.
- A marketplace listing (RedSky RTC `VPC`) never fires; a typo'd TCIN for another Target
  item WOULD be bought (the buy path checks only the TCIN). Verify each new number at the
  source (`target.com/p/-/A-<tcin>`; a PDP 404 = unpublished, which may stay armed and
  shows `[TCIN-VISIBILITY]` until Target publishes it).
- `qty` missing or 0 = no pin (the qty-2 policy), live only while `TARGET_QTY_PER_TCIN=1`.
- The list loads at boot: a bot that is already running does not see an edit until it is
  restarted. Say so if the bot is up.
- List order is not a priority lever (C-0924-02). Do not spend time reordering.

## Step 5 — Prove the pool (by the rule, not by habit)

Run it when: the last production-path boot is > 48 h old, `proxyIps.json` or the pool size
changed, an exit is parked into tonight, or the Workflow's monitor audit says so. Skip it
when yesterday's boot on this tree showed `18/18 sessions ready` and a clean first STATS.

Use the exact command the preflight prints (`monitor.validate_cmd`): the wrapper's
`RESILIENT_*` / `STOCK_*` env (a bare run tests the `web` channel, which HUMAN/PX walls on
the BD prefixes — 09-22), `CHROME_STAGGER_TOTAL_S=30`, `STOCK_DIAG_PROBES=1` (exercises the
unchunked ground-truth read at tonight's TCIN count; `STOCK_CANARY=0` keeps the home IP out
of it), `VALIDATE_POOL=proxies` (the bot never reads `reserve_proxies`; "20/20" on `all`
counted two exits the bot does not use), `-u`. ~4 min, throwaway state dir, nothing
bought. **Judge 206s and timeouts from its `[STOCK][EXITS]` / `[STOCK][OTHER]` lines** —
its HEALTHY column reads only 200s and 403s. Never judge an exit with raw `requests`.

## Step 6 — Arm, gate, record every flag

- Flag-gated, default = current behaviour, byte-identical off. Verified by a fresh
  `claims-verifier` on the bare claim. Uncertain effect → one account
  (`<FLAG>_ACCOUNTS=alt-1` style) so the others are same-window controls.
- Wrapper edits: CRLF, ASCII, each variable assigned once and last; a REM must not
  contradict the `set` line under it (one "NOT ARMED" header sat above `=1` for weeks).
- Every flag whose value differs from the last live boot is listed in CURRENT_STATE as
  **UNPROVEN LIVE** with its kill switch and the exact log marker that proves it exercised.
- `python tests/run_offline_suite.py` on the exact armed tree. Report the real result.
- SHORT / LAST-CALL: nothing is armed. Say what was not done and why.

## Step 7 — Pre-register the readout

Before the run, PASS / FAIL fixed in advance, one rule per armed change: a saved query in
`tools/events/queries/<name>.sql` (`python tools/events/q.py <name> --run <run_id>`) plus
its written rule — never a new `readout_<date>.py`. Extend `tools/events/build.py` (and
`tests/test_events_parser.py`) for a marker the store does not parse. Smoke every new query
against a past run. **R-MONEY is always registered**: order history within 2 min of any
order and at once on `[AMBIGUOUS_COMMIT]` — no order with qty > 2, no TCIN twice by one
account in one window, no item that was not raced; any → kill the FX flags `=0`.

## Step 8 — Record

- `.claude/state/CURRENT_STATE.md`: bump the as-of line; a new PRE-DROP section on top
  (state, readiness, config, proof, armed / declined with kill switches, UNPROVEN LIVE,
  readout rules, boot checklist); **delete lines that turned out wrong**, do not caveat
  them; fix the Workflow's `stale_facts` at their source.
- Memory: a session file + the RESUME line. The machine restarts.
- **Offer to commit** (never push unasked). Say plainly that uncommitted reasoning is lost
  on a reboot — the files survive, the state file must describe the tree that will boot.

## Step 9 — Boot: the T-minus timeline (this session runs it)

Before you stop working, schedule two one-shot wake-ups in this session with `CronCreate`
(`recurring: false`; minute, hour, day and month pinned; local time): **T-60** ("pre-drop
T-60 re-check for <drop>") and **T-20** ("pre-drop T-20 boot for <drop>: Step 9"). They fire
only while this Claude session is open and idle — say so in the report, and leave the
session open until the boot is GREEN. If T-20 has already passed when Step 8 ends, boot now.

- **T-3 h or earlier (host):** Windows Update paused; display and sleep Never on AC; no
  Parsec / RDP / keyboard / mouse planned on the host during the window; BIOS / XMP
  checklist is still open (10-02).
- **T-60 (wake 1):** re-run the preflight (`--drop --tcins`). Readiness can change in two
  hours (10-04: primary went guest between 21:43 and 23:49). A readiness ❌ is the one step
  that needs a person (a hand login): put it first in the output and send a
  `PushNotification` when the tool is available.
- **T-20 (wake 2) — boot:**
  1. `python tools/ops/bot_ctl.py status --json`. Keep a running bot only when all three
     hold: it will be < 20 h old at T-0 (login-session rot 32–46 h; RSS grows ~0.4 GB/h), a
     one-pass `python tools/predrop/boot_check.py` on its run log is GREEN, and nothing that
     loads at boot changed after its `started` (config/*.json, the wrapper, `app.py`, `src/`
     — compare mtimes). Otherwise restart.
  2. `python tools/ops/bot_ctl.py restart`: Ctrl+C to the wrapper's console (app.py saves the
     session and closes Chrome), a kill after 30 s, every bot-profile Chrome cleaned up, then
     the wrapper opened through explorer.exe exactly like a double-click (its own window; it
     outlives this session). The relogin pass takes ~45 s, GREEN comes at ~+4.5 min. Exit 1 /
     3 = read its last line, fix that, run it once more.
  3. Right after the launch, in the background:
  ```
  python tools/predrop/boot_check.py --follow
  ```
  GREEN = every buy-path marker present: `[WORKER_POOL] sized … N account(s)` = enabled,
  `Login check result: True`, `LOGGED IN TO TARGET.COM`, `[INIT] TEST_MODE: false`,
  `use_real_purchasing: True`, `stock_thread started`, **`purchase_thread started`**,
  **`All threads started successfully`** (all by ~+13 s), no `Exception in thread`, write-auth
  alive ×N, harvest selftest `accepted=[424]` ×N, boot cart audit empty ×N,
  `[MULTI_SESSION] started — 18/18 sessions ready` (~+3.5 min), first `[STOCK STATS]`
  `403=0 429=0`, `[GROUND-TRUTH] … ok … (N TCINs)` = enabled, no `[TCIN-VISIBILITY]` banner
  naming a wanted TCIN, a `[STOCK STATUS] … #1 old=(first)` line per TCIN (RTC `VPC` =
  marketplace), `[FX1006]` flags = the wrapper, `[STALL_WATCHDOG] armed`, dashboard on 5001.
  4. RED, or PENDING past +8 min: read the RED row, fix the cause, `bot_ctl.py restart`,
     re-check — at most two restarts, none after T-5 (a degraded-but-buying bot beats a
     restart into the window). `login_check` / `wrapper_exit87` = a guest jar: run
     `bot_ctl.py stop` at once (never let the exit-87 loop cycle — 10-14 burned 64
     launches), then it is the person step above. `thread_start_exception` = a startup-path
     bug: kill the flag behind it in the wrapper and restart.
- **T-0:** hands off the host. Nothing is restarted mid-window unless the bot is dead
  (`bot_ctl.py status` shows no app.py — then `bot_ctl.py start` at once); a
  `WRITE-AUTH DEAD` is normal Shape burn unless it persists.
- **T+2 min after any order / at once on `[AMBIGUOUS_COMMIT]`:** Target order history
  (R-MONEY).
- **After the window:** `python tools/events/build.py` → `python tools/events/q.py <rules>
  --run <run_id>` → `/post-run`.

Then report the boot (GREEN time, kept or restarted, any RED and its fix), name the single
command that reads the run out, and stop.

---

## Appendix — every pre-launch miss since 2026-09-16, and what catches it now

| When | What was missed | Now caught by |
|---|---|---|
| 09-17, 09-25, 10-04 | exit-87 loops: Worker 1 a guest while the login pass said "already logged in" and readiness exited 0 | `accounts.readiness:*` (guest decode), `boot_check` `login_check` / `wrapper_exit87`; BOOTSKIP armed |
| 09-21 | booted with `WORKERS_PER_TCIN=1` + `CAP_ALWAYS=1` (one account per TCIN) | `arming` audit: value vs intent; the `[GUARDS] … D1 multi-SKU dispatch ON (…)` boot line |
| 09-21 | pool cut 16→8 with no soak; a 43-min 429 tarpit hid in `other` | STATS split per status; `[STOCK][OTHER]`; `monitor` audit slot-hour loss |
| 09-21, 09-30 | a requested TCIN absent from the config entirely | `config.requested_absent` |
| 09-22 | a bare `validate_proxies.py` tests the `web` channel | `monitor.validate_cmd` (full wrapper env) |
| 09-28 | booted with an account the previous post-run had said to hand-login | `regime` audit carry-forward list; readiness |
| 09-28 | readiness ✅ 3/3 while all three jars were guests | readiness guest decode (`READINESS_GUEST_CHECK`); `accounts.readiness:*` |
| 09-30 | F1 built, neither armed nor declined before boot | `arming` audit "built, never decided" → a recorded decision tonight |
| 10-02 | display sleep on AC → GPU crash at a wake 82 s before the first edge; 0 of 3 first shots | `host.display`, `host.sleep`, `host.screensaver`, `host.events`, `process.remote` |
| 10-02 | boot SELFTEST counted a 429 as accepted ×3 | `boot_check` `harvest:*` (accepted must be only 424) |
| 10-04 | "no hand login needed" from a 2-hour-old readiness; primary went guest | T-60 preflight re-run |
| 10-05 | third RedSky 206 storm at the 02:00 slot | `monitor` audit (`monitor_hours`, `shadow206`); `boot_check` first STATS |
| 10-06 | Windows Update not paused (default install ~03:00) | `host.wu_pause`, `host.wu_reboot`, `drop.wu_overlap` |
| 10-07 | startup `AttributeError` killed MonitoringLoop before `purchase_thread.start()`; every account marker read green | `boot_check` `purchase_thread` / `all_threads` / `thread_start_exception`; `code-risk` audit; the startup-path rule |
| ongoing | wrapper REM comments contradicting their `set` line or the state file | `arming` audit; `repo.*` rows |
| ongoing | business's configured CVV equals the hardcoded default; a 72-day-old CVV latch on alt-1 | `accounts.prereq:*`, `accounts.cvv_latch` |

Corrected on 2026-10-08: the old example "`MULTI_SKU_DISPATCH` sat at 0 for a week" was
REFUTED (the first boot after it was built printed `D1 multi-SKU dispatch ON`); the real
09-21 arming miss was one worker per TCIN. The old checklist string
`=== drop-readiness check ===` is console-only and never reaches a log; the boot check reads
the relogin and wrapper logs instead.

# 06 - Whole-file readiness audit of run_bot_with_nightly_restart.bat (2026-11-05)

Scope: cross-cutting audit, not per-line docs. Reader: security engineer new to retail bots.
Evidence tags: **[CODE]** = verified by reading/running code or tests this session; **[INFER]** = inferred from bat comments or docs only.
Methods: grep/python scans over every .py outside .venv/venv/.git/.build_wt/.claude/docs; `tools/predrop/preflight.py --no-readiness` (read-only) run at 18:06 today; the three new 1104 test files executed.

## A. Flag inventory
- `grep -nE '^\s*set '` returns **239** lines. Six are `set /a` arithmetic counters (loop only), leaving **233** variable assignments: roughly 229 config flags plus internals (PYTHON, LOGDIR, RUNLOG, EXITCODE). The repo's own preflight counts 228 flag variables (narrower definition); the difference is not material.
- Four `ENSURE_ARMED_*` flags (lines ~1870-1890) are set INSIDE the restart loop, after app.py has already exited. They only affect the exit-87 (not-logged-in) recovery path. Preflight confirms the user environment agrees (`host.profile_signals_env` OK).
- **Duplicates: 0.** Checked case-insensitively (batch variable names are case-insensitive). Preflight independently reports "228 variables, each set once". [CODE]
- Security framing: a duplicate is a latent mis-arm, because in batch the LAST assignment silently wins; a reviewer reads the first line and the process runs the second. None exist today. Caveat: the REM text contains long arming histories ("ARMED", "NOT ARMED", "SUPERSEDED"); the `set` line is the truth, the prose is not.
- Empty-valued sets (intentional "unset"): `TARGET_FP_SEED_SALT=`, `TARGET_ACCOUNT_EGRESS_BIND=`, `TARGET_HARVEST_SKIP=`. In batch `set X=` DELETES the variable, so code sees it unset and applies its default.

## B. Dead flags and reverse check
- **Dead (set but never read in any .py): 0 real ones.** Only `LOGDIR` and `RUNLOG`, which are batch-internal path variables. No flag is read only by tests/tools. Preflight agrees ("every one of the 228 wrapper variables is read somewhere"). [CODE]
- Caveat: "read somewhere" is not "effective on the live path". Example: the state docs call `TARGET_ATC_DEAD_TOKEN_MIDWINDOW_REPAIR=1` inert under `TARGET_TOKEN_KEEPFRESH=0`. Dead-by-effect is outside this mechanical pass.
- **Read by code but NOT set in the bat: 104 names** (fall back to code defaults). Security meaning: these are the fail-open / fail-closed defaults you inherit with no visible line. The ones that matter:

| Variable | Code default | Where | Note |
|---|---|---|---|
| TARGET_API_PLACE_ORDER | `true` (setdefault) | app.py:51 | Real API place-order ON by default |
| TARGET_API_CART_CLEAR | `true` (setdefault) | app.py:52 | |
| TARGET_API_PLACE_ORDER_OBSERVE | 'false' | purchase_executor.py:11300 | |
| TARGET_QTY_CEILING | '2' | bulletproof_purchase_manager.py:1014 | max units if no pin |
| TARGET_FORCE_QTY_1 | 'false' | bulletproof_purchase_manager.py:999 | |
| TARGET_QTY_OPTIMISTIC | '1' | bulletproof_purchase_manager.py:4719 | |
| TARGET_WORKER_POOL_SIZE | "1" | src/purchasing/worker_pool.py:379 | worker cap default |
| TARGET_WARMUP_TAB_COUNT | '2' | purchase_executor.py:1783 | |
| TARGET_RACE_ALL_WORKERS | '1' | bulletproof_purchase_manager.py:3960 | |
| TARGET_RACE_JOIN_FREE / _GAP_S / _BUDGET_S / _FRESH_S | '0' / '10' / '30' / '5' | bulletproof_purchase_manager.py:940-971 | |
| TARGET_RETRY_WHILE_IN_STOCK, _BUDGET_S | '1', '110' | bulletproof_purchase_manager.py:3144, 2149 | |
| TARGET_RETRY_CHECKOUT_BUSY | '1' | bulletproof_purchase_manager.py:3195 | |
| TARGET_FOREIGN_CART_BAIL | '1' | purchase_executor.py:5924 | |
| TARGET_BOOT_CART_AUDIT | '1' | purchase_executor.py:386 | |
| TARGET_CART_HOLD_CHECK / _VERBOSE | '1' / '1' | purchase_executor.py:2007, 2012 | |
| TARGET_CVV_REQUIRED | '' (latch files decide) | purchase_executor.py:8686 | alt-1 has a 101-day-old CVV latch file |
| TARGET_SESSION_SENTINEL | '1'; _INTERVAL_S '300'; _INITIAL_DELAY_S '90'; _CYCLE_INTERVAL '150' | bulletproof_purchase_manager.py:4417-4425 | sentinel ON by default |
| TARGET_SENTINEL_DEFER_RECENT_S / _MIN_TOKEN_S / _QUIET_LOCAL | 180 / 900 / '01:30-04:30' | bulletproof_purchase_manager.py:4503, 4549, 1050 | knobs of the new defer feature |
| TARGET_SENTINEL_HOMEIP_RELOGIN | '1' | session_manager.py:2221 | |
| TARGET_TOKEN_MIN_TTL_S / TARGET_RELOGIN_COOLDOWN_S | '1800' / '1200' | session_manager.py:1855, 1885 | |
| TARGET_REARM_KICK | '0' | bulletproof_purchase_manager.py:416 | built, not armed |
| TARGET_ATC_DCO_BURST_ANY_BODY | '0' | bulletproof_purchase_manager.py:3534 | |
| TARGET_ATC_RETRY_DELAY_MIN/MAX | 2.5 / 3.5 | bulletproof_purchase_manager.py:3436 | |
| TARGET_CHECKOUT_INPLACE_DELAY_MIN/MAX | 2.5 / 3.5 | purchase_executor.py:11671 | |
| TARGET_WRITE_AUTH_CONFIRM_N | '3' | purchase_executor.py:3797 | |
| TARGET_DOM_FALLBACK_ON_NO_RESPONSE | '0' | purchase_executor.py:11773 | |
| TARGET_EXIT_ON_LOGIN_HALT / _BOOT_CHROME_REAP / _FAULTHANDLER / _DISABLE_QUICKEDIT | '1' | app.py:5025, 4739, 39, 4651 | |
| TARGET_LAUNCH_OFFSCREEN | "1" | multi_session_pool.py:461 | |
| RESILIENT_EXIT_STATS, _GROUND_TRUTH_FIRES, _SPLIT_429, _VERIFY_ON_CLOAK, _TCIN_VISIBILITY_ALERT/_STATE | '1'; _TCIN_INVISIBLE_GRACE_S "90"; _REALERT_S "3600"; _RAW_CACHE_BUST '0' | stock_check_resilient.py:458-537, tab_dispatcher.py:234 | |

The rest are credentials/paths for helper scripts (TARGET_EMAIL, TARGET_PASSWORD, TARGET_PROFILE_DIR, TARGET_SESSION_PATH, TARGET_ACCOUNT_ID, TARGET_TIMEZONE, TARGET_CHROME_VERSION, TARGET_UA_AUTODETECT), egress internals (TARGET_ACCOUNT_EGRESS_CONNECT_S '4', _DNS '1', _FALLBACK_HOME '0', _AGE_EXEMPT '0'), rebuy internals (TARGET_REBUY_*), and test-only names (RESILIENT_TEST_*, TARGET_FOO, TARGET_NEVER_READ_FLAG, TARGET_TEST_TCIN, TARGET_SMOKE_ACCOUNTS). Good hygiene: credential env vars are NOT set in the bat (no secrets in the launcher). [CODE]
Limit: the scan regex only catches `environ.get / getenv / env_flag / _env*` calls with a literal name; flags read through other helpers or built dynamically could be missed. Treat the table as a floor.

## C. The uncommitted 11-05 change (bat vs HEAD, +18/-2)
**One sentence:** it arms the new `TARGET_SENTINEL_DEFER=1` (stops the 300 s session-sentinel from navigating the account page while a purchase window is open, which collided with the first shot in 3 of 5 windows on 10-30) and adds two comment-only annotations saying the old DCO-burst evidence does not reproduce on newer data.

Hunks:
1. ~line 870, comment only: "2026-11-04: those counts are pre-09-17 data ... flip era got past edge 1/19, see docs/CLAIMS.md C-1104-04". No behaviour change.
2. ~line 972, comment only: "does not reproduce on ident-tagged data, 3/33 vs 0/8". This undercuts the stated rationale for the still-ARMED `TARGET_ATC_DCO_BURST=1` and `TARGET_ATC_DCO_BURST_REQUIRE_BANK=0`. It is a pending decision, not a bug. It also cites `docs/CLAIMS.md`, which is currently DELETED in the working tree (exists in HEAD only).
3. ~line 1411: comment block + `set TARGET_SENTINEL_DEFER=1`. The only behaviour change.

Code check [CODE]:
- Read at `src/purchasing/bulletproof_purchase_manager.py:1040-1042`: `os.environ.get('TARGET_SENTINEL_DEFER','0').strip()=='1'`; junk = off. Used by `_sentinel_defer_reason` (~4487-4582) and the navigation guard (~4640).
- Knobs `TARGET_SENTINEL_DEFER_RECENT_S` (180, clamp 0-3600, line 4503), `TARGET_SENTINEL_DEFER_MIN_TOKEN_S` (900, line 4549), `TARGET_SENTINEL_QUIET_LOCAL` ('01:30-04:30', line 1050) match the comment's defaults. Unparseable QUIET_LOCAL = never quiet (fails toward old behaviour).
- Fail-open/closed: defer skips the check ONLY when the member token is known healthy (TTL >= 900 s); unknown / guest / expired token always runs the check. Residual risk: during a live window or quiet hours a session that dies silently is not probed by the sentinel, bounded by the 900 s TTL floor.
- **Critical cross-check:** the code implementing it is UNCOMMITTED. `git diff --stat`: bulletproof_purchase_manager.py +118, purchase_executor.py 6 lines, limkey_guest_follow.py 258 lines, tests/run_offline_suite.py +3. The bat arms a flag whose reader exists only in the dirty tree; if the tree is reverted the flag silently becomes a no-op. Preflight `repo.since_last_boot`: "TARGET_SENTINEL_DEFER: (unset) -> 1 ... 6 code/wrapper files dirty ... UNPROVEN LIVE" (last live boot 2026-11-04 06:34:59 on commit 9959a895).
- Tests: `tests/test_sentinel_defer_1104.py` ran 6/6 passed; wired into `tests/run_offline_suite.py:42`. Env names it exercises: TARGET_SENTINEL_DEFER, _DEFER_RECENT_S, _DEFER_MIN_TOKEN_S, TARGET_SENTINEL_QUIET_LOCAL, TARGET_STOCK_PROBE.

**Hook warning ("bat changed AFTER the state file"):** true but largely moot. `.claude/state/CURRENT_STATE.md` is DELETED from the working tree (preflight: FileNotFoundError). The last committed state file is dated 2026-10-27 and does not mention SENTINEL_DEFER. The only armed-flag difference vs the last live boot is that one flag (preflight: "1 flag value(s) changed, 0 removed"), and the bat diff is exactly the three hunks above, so no other armed-flag drift. The real exposure is "newly armed, unproven live, code uncommitted", not "flags may be wrong". Separately, ~3,800 lines of docs (CLAUDE.md, CLAIMS.md, CURRENT_STATE.md, NEXT_DROP_CHECKLIST.md, whatHappensNext.md) are staged-deleted, a documentation-loss risk.

## D. Armed-state check vs operator intent (as of 2026-11-04)
| Feature | Flag in bat | Value | Matches intent? |
|---|---|---|---|
| Lanes OFF | `TARGET_LANE_MODE` (line 1614); read at app.py:981 `== '1'` | **0** | YES. Other TARGET_LANE_* knobs are set but inert while MODE=0. [CODE] |
| Sentinel-defer ARMED | `TARGET_SENTINEL_DEFER` (line 1424) | **1** | YES in the bat; code uncommitted and unproven live. |
| Guest-follow slots | **No bat flag exists** (grep GUEST_FOLLOW / FOLLOW_SLOT in bat and src: nothing) | n/a | Slot grid is CLI args (`--slot-grid-s`, `--slot-offset-ms`, `--tag`, `--lock-name`) to `tools/experiments/limkey_guest_follow.py`, run by Task Scheduler, gated by file `state/guest_follow.enable`. `test_guest_follow_slots_1104.py` uses no env vars (pure functions); ALL PASS. |

Guest-follow detail: registered tasks are only `\TargetBot\guest-follow 2340` and `0135` (home line). The extra arms bd0 / bd0b / bd1 in `tools/experiments/guest_follow_arms_1104.md` are explicitly "NOT registered, nothing here has been run". The shooter script is modified and uncommitted (+258 lines) yet those existing tasks execute whatever is on disk, and `state/guest_follow.enable` (dated 2026-10-16) is present, so tonight's home follower runs the NEW slot code with no bat involvement. Blast radius: guest (non-purchasing) sampler, so bandwidth and traffic noise on the home line, not money.

**Auto-arm is disabled:** `state/ensure_armed.disable` exists (created 2026-11-03 23:45). The 23:15 / 01:30 `ensure-armed` tasks will therefore not (re)start the bot, and preflight shows no app.py running. Only the Startup-folder link `TargetBot.lnk` (starts the bat at logon/reboot) or a manual launch starts it. Confirm this is intended. [file presence CODE; semantics INFER from docs]

Runbook/preflight expectations:
- `docs/PRE_DROP_RUNBOOK.md` wants: drop-readiness echo with all 3 accounts MEMBER; TCIN visibility not PARTIAL; a `[GROUND-TRUTH] pool cache-bust ok` line; `TARGET_TOKEN_KEEPFRESH=0` and `TARGET_RELOGIN_MAX_PER_6H=0` (bat: both 0 [CODE]). The live items cannot be confirmed without a boot.
- `docs/DROP_DAY_PLAYBOOK.md` is a STALE Walmart "capture run, FINAL_PURCHASE=NO" doc (2026-08-17). Its "will NOT place an order" claim is FALSE for the current bat (TEST_MODE=false). Do not use it as a checklist.
- `tools/predrop/preflight.py` today: **GO-WITH-WARNINGS** (OK 25, WARN 8, UNKNOWN 2, no FAIL). Not passing or unconfirmed:
  1. `accounts.profile_guest`: business's live Chrome profile reads GUEST (sut=G) per newest relogin.log while its jar file says member (35.5 h ago).
  2. `repo.dirty`: 14 tracked files modified/deleted, including the code behind the newly armed flag.
  3. `repo.since_last_boot`: SENTINEL_DEFER unproven live.
  4. `repo.state_asof`, `state_bat_cites`: UNKNOWN (state file deleted).
  5. `host.wu_pause`: Windows Update not paused (install ~03:00; forced restart hit on 06-24).
  6. `host.events`: Kernel-Power 41 / EventLog 6008 unexpected shutdown 11-02 21:25; `process.remote`: parsecd.exe running (docs: 20/27 GPU-crash wakes carried remote-wake codes).
  7. `accounts.cvv_latch`: alt-1 CVV latch file 101 days old.
  8. `monitor.pool_config`: 10 active + 2 reserve exits (<15 recommended).
- Passing: bat CRLF (1925 lines), 25 enabled TCINs of 35 (one RedSky chunk <= 28), qty pins [2], TARGET_QTY_PER_TCIN=1, apps_raw channel, no orphan Chrome, display/sleep never.

## E. Safety / money flags (values plainly)
| Control | Value | Evidence |
|---|---|---|
| `TEST_MODE` (line 292) | **false** = REAL orders. Pinned so a stray TEST_MODE=true in the environment cannot silence an unattended run | app.py:1101, 1569, 1626 compare `== 'true'` [CODE] |
| `TARGET_API_PLACE_ORDER`, `TARGET_API_CART_CLEAR` | not in bat; app.py setdefault **true / true** | app.py:51-52 [CODE] |
| Accounts | **3 enabled**: primary, business, alt-1 | preflight [CODE] |
| Race set | 25 enabled TCINs of 35 | preflight [CODE] |
| Quantity | `TARGET_QTY_PER_TCIN=1` so per-entry qty is honoured; every enabled TCIN qty=2 (up to 2 units per order) | preflight, bat line 1586 [CODE] |
| Concurrency | `TARGET_MULTI_SKU_DISPATCH=1`, `_MAX_CONCURRENT=3`, `_WORKERS_PER_TCIN=3`; `TARGET_WORKER_POOL_SIZE` default 1 | bat 916-950 [CODE]; interaction INFER |
| Aggressive purchase paths | `TARGET_FAST_LANE=1`, `TARGET_FAST_LANE_CVV=1`, `TARGET_WONCART_DIRECT=1`, `TARGET_HELD_LINE_FLIP_STRIKE=1`, `TARGET_WONCART_HARD_MAX_TICKETS=1200`, `_MAX_TICKETS=40`, `TARGET_RETRY_WHILE_IN_STOCK_MAX=40` | values CODE; meaning INFER from comments |
| Duplicate-purchase guards (fail-closed) | `TARGET_AMBIGUOUS_COMMIT_LATCH=1` (1800 s, persisted), `TARGET_PO_5XX_AMBIGUOUS=1`, `TARGET_PO_2XX_AMBIGUOUS=1`, `TARGET_FOREIGN_CART_BAIL` (default 1), `TARGET_FOREIGN_KEEP_WON=1`, `TARGET_RESERVATION_BAIL=1`, `TARGET_EMPTY_CART_BAIL=1`. No latch file now (preflight OK) | bat; preflight |
| Re-buy | `TARGET_REBUY_NONBUYERS=1`, `TARGET_HELD_CART_REENTRY=1` ARMED: allow more than one purchase per TCIN across accounts. Confirm that is intended | values CODE; semantics INFER |
| Lanes | `TARGET_LANE_MODE=0`: no second firing path | bat 1614 [CODE] |
| Dry-run | None besides TEST_MODE=false; `TARGET_API_PLACE_ORDER_OBSERVE` default false | [CODE] |
| Wrapper loop | Relaunches app.py 10 s after any non-zero exit, forever; exit 0 stops; exit 87 = relogin (3 cycles then 10 min cool-down). Unattended + live orders means a crash loop could re-attempt orders; the disk-persisted ambiguous-commit latch exists to cover that | bat tail [CODE] |
| Spend cap | **No currency cap flag found.** Limits are structural: qty 2/TCIN, ticket counts, latches, claim registry | INFER (absence by grep) |

## F. Verdict: **CAUTION** (OK to boot and watch; not set-and-forget)
Not NO-GO: zero duplicates, zero dead flags, lanes OFF as intended, TEST_MODE deliberately false, new offline tests pass (3/3 files), preflight has no FAIL. Not GO: a newly armed flag rests on uncommitted, never-live code; business profile reads signed-out; auto-arm is disabled.

Ordered by severity:
1. **Commit (or consciously keep) the uncommitted code behind SENTINEL_DEFER.** A revert silently disables the armed flag; the modified limkey_guest_follow.py is already live via scheduled tasks. [VERIFIED: git diff --stat, grep]
2. **Resolve business profile = GUEST** (relogin_one.py validate-first or hand login) and confirm 3/3 MEMBER in the boot readiness echo. A guest buyer at flip time yields no orders for that identity. [problem VERIFIED by preflight; fix effect UNVERIFIED]
3. **Decide whether the bot should start tonight:** `state/ensure_armed.disable` (11-03 23:45) blocks auto-arm; bot is down. [file VERIFIED; semantics INFERRED]
4. **Treat SENTINEL_DEFER as an experiment:** watch the first boot with `tools/predrop/boot_check.py --follow`; readout `python tools/events/q.py first_shot_delay --run ID` plus [SENTINEL] greps (PASS: each ident <=150 ms in >=90% windows, no ERR_UNAUTHORIZED / guest increase). Kill: `TARGET_SENTINEL_DEFER=0`. [readout from bat text = INFER; flag and knob reads VERIFIED]
5. **Confirm live-money posture:** TEST_MODE=false, API place-order default true, REBUY_NONBUYERS=1, WONCART_HARD_MAX_TICKETS=1200, qty 2 per TCIN x 3 accounts, no currency cap. [VERIFIED values]
6. **Host stability:** pause Windows Update; no Parsec/RDP during the window; the 11-02 unexpected shutdown matches the known crash class. [VERIFIED by preflight]
7. **Documentation loss:** CLAUDE.md, docs/CLAIMS.md, CURRENT_STATE.md, NEXT_DROP_CHECKLIST.md, whatHappensNext.md are staged-deleted; the new bat comments cite CLAIMS.md C-1104-04 and preflight's state-cite checks cannot run. Restore or consciously accept. [VERIFIED]
8. **DCO burst still armed** (`TARGET_ATC_DCO_BURST=1`, `_REQUIRE_BANK=0`) although the new annotations say its evidence does not reproduce (1/19, 3/33). Operator decision. [text/values VERIFIED]
9. Minor: alt-1 101-day CVV latch; proxy pool 10+2 (<15); guest-follow arms bd0/bd0b/bd1 unregistered (register only if wanted); DROP_DAY_PLAYBOOK.md is stale Walmart content, ignore.
10. Not verifiable without a live boot: harvest self-test, ground-truth read, TCIN visibility freshness (34 h old), per-account egress IPs.

Files: bat `C:\Users\elric\Desktop\testScraper\run_bot_with_nightly_restart.bat`; diff `C:\Users\elric\Desktop\testScraper\docs\bat_audit_2026_11_05\_uncommitted_diff_1105.patch`; this report `C:\Users\elric\Desktop\testScraper\docs\bat_audit_2026_11_05\06_readiness_audit.md`.

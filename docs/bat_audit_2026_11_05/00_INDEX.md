# 00 - INDEX: audit of run_bot_with_nightly_restart.bat (2026-11-05)

This is the entry point. It merges the six audit documents in this folder into one reading guide, one inventory, one severity-ordered readiness list, and one operator cheat sheet. Nothing here is new investigation; every fact is carried from docs 01-06, and where the docs disagree that is stated. Evidence tags used below: **VERIFIED** (doc 06 or a chunk doc read code, ran a tool, or checked a file) and **INFERRED** (taken from the .bat's own REM comments or from reasoning, not independently checked).

---

## 1. How to read this folder

`run_bot_with_nightly_restart.bat` is a roughly 1,925-line Windows batch file that does two jobs for a Target.com purchase bot. First, it is an environment-variable configuration file: about 230 `set NAME=VALUE` lines that arm or disarm individual behaviours of the Python program `app.py` (how fast to poll stock, how to look like a human browser, how hard to retry checkout, and so on). Second, in its last ~200 lines it is a process supervisor: it checks the Python interpreter, guards against a second instance, logs accounts in once, then loops forever launching `app.py` and relaunching it if it crashes. Security framing: the first half is the bot's "outward behaviour policy" toward Target's defences (F5 Shape request signing, HUMAN/PerimeterX challenge pages, and the per-product edge rate limiter), and the second half is a watchdog whose main risk is availability and not creating a login storm or a double instance. Most flags carry dated REM paragraphs recording the incident that caused them, so the file doubles as an incident log. **The `set` lines are the truth; the prose is history and sometimes stale.**

| File | Bat lines | What it covers |
|---|---|---|
| `01_lines_0001-0410.md` | 1-410 | Header, venv autodetect, stock-monitor sweep rate and Bright Data pool history, the apps_raw detection fix, trusted-reader fallback, HUMAN Press & Hold guard, fingerprint/UA identity, TEST_MODE pin, add-to-cart retry tuning, ATC gate circuit breaker, in-place checkout re-shoot. |
| `02_lines_0407-0895.md` | 407-895 | Checkout fast lane, FAST_SELLING back-off, egress bind (not armed), won-cart hold and ride, CDP/Chrome health, CVV challenge handling, post-drop-audit guards, features deliberately switched OFF, wave-first doctrine, 401 pulse, DCO burst. |
| `03_lines_0895-1129.md` | 895-1129 | Multi-SKU dispatch, DCO burst bank rules, real product-page referrer, Shape harvester and banked-token replay, in-chain pre_checkout retry, fresh-page-per-harvest-click. |
| `04_lines_1129-1587.md` | 1129-1587 | Double-order guards (ambiguous-commit latch), won-cart direct checkout loop (WC-1) and ride-to-OOS, many-SKU restock fixes, fast re-shoot cadence A/B, login/jar/sentinel integrity (incl. SENTINEL_DEFER), telemetry, HUMAN/PX harvest handling, retired park experiment, per-TCIN quantity pin. |
| `05_lines_1587-1924_and_restart_loop.md` | 1587-1924 | Independent-lane flags (OFF), token keep-fresh and scripted-relogin caps, pre-launch steps, the `:loop` crash supervisor, exit-code paths, `bot_ctl.py` interaction, ENSURE_ARMED flags. |
| `06_readiness_audit.md` | whole file | Cross-cutting: flag count, duplicates, dead flags, code-read-but-not-set variables, the uncommitted 11-05 change, armed-state vs operator intent, money-safety flags, preflight results, and the CAUTION verdict. |

Also present: `_uncommitted_diff_1105.patch` (raw git diff; not part of the reading set).

Suggested order: read this index, then doc 06 (verdict and evidence), then the chunk doc for whatever area you are about to touch.

---

## 2. The file in one page

Read this top to bottom; it mirrors the file's layout.

1. **Header, venv, stack (lines 1-38; doc 01).** The wrapper autodetects `.venv\Scripts\python.exe` (fallback `venv\`) because a wrong interpreter caused two past incidents (05-22, 06-04). `USE_RESILIENT_STACK=1` selects the multi-session RedSky stock sweep. Failure here means the bot silently is not running on drop night: an availability risk, not a detection risk.

2. **Stock monitor cadence and the apps_raw channel (lines 39-194; doc 01).** `TARGET_SWEEPS_PER_SEC=3.0` is the global polling rate against Target's RedSky inventory API, spread over a Bright Data (BD) proxy pool at a per-IP cap of 0.5 requests per second. Because HUMAN captcha-walls the web surface, `RESILIENT_REDSKY_CHANNEL=apps_raw` reads the mobile-app endpoint as raw HTTP instead; this single flag is the "detection fix" and the code default is `web` (blind). Log-only taps for HTTP 206 responses and stock flips sit alongside. A trusted-account-browser reader (3-5 s cadence) is a fallback only when the pool is blind for 180 s.

3. **HUMAN Press & Hold guard (lines 195-208; doc 01).** When an account browser shows a Press & Hold interstitial, the guard treats it as a challenge, not a dead session; it parks the heavy recovery rungs for 300 s and never auto-solves. This exists because the old ladder's scripted relogins were Shape-burned (0 of 12 usernames advanced, 09-06/07) and left accounts GUEST.

4. **Fingerprint and UA identity (lines 209-287; doc 01).** All three accounts run real Chrome on one machine (`TARGET_FP_CHROMIUM=0`); JS spoofing is off; `TARGET_UA_MODE=engine` sends no UA override so harvest and purchase tabs match; logins go out from the home IP (BD-IP logins were Shape-blocked). Security trade-off: all accounts share one hardware fingerprint, which the operator's own note calls the top cross-account linker.

5. **Test-mode pin, ATC retry tuning, gate breaker, in-place re-shoot (lines 289-406; doc 01).** `TEST_MODE=false` is pinned so a stray shell value cannot silence a live run. Add-to-cart retries are capped at 40 with a 2-3 s sleep after empty-body edge 429s. The ATC "gate-wall" circuit breaker is OFF (it cut cadence with no benefit); the place-order POST is re-fired in place up to 4 times on 429/424 and stops when an `order_id` lands.

6. **Checkout fast lane, FAST_SELLING, egress bind (lines 407-492; doc 02).** The fast lane chains ATC, pre_checkout and place-order in one in-browser fetch sequence (about 0.85 s versus about 3.4 s), including an in-lane CVV PUT. FAST_SELLING (Target's per-item throttle) triggers a 45 s per-TCIN back-off. The NIC/hotspot egress bind is deliberately NOT armed after a pre-arming verifier refuted it.

7. **Won-cart hold and ride, CDP health, CVV handling, post-drop guards (lines 494-698; doc 02).** A won cart is held through the FAST_SELLING cooldown (6 cycles, 270 s) rather than cleared. Chrome health flags relaunch proxied Chromes every 35 min to dodge a CDP wedge. CVV-challenge flags go DOM-first and auto-unlatch. The 08-02 audit guards (empty-cart bail, re-nav, 3 s level re-arm, dead-session sentinel back-off) are set explicitly so the file stays the changelog.

8. **Switched-off features, wave-first doctrine, 401 pulse, DCO burst (lines 699-894; doc 02).** Several features proven useless or harmful are explicitly OFF (native ATC fallback, 401 repair ladder, per-retry re-warm). `WAVE_FIRST_ONLY=1` means no re-POST after a 401/429; the shot at t+0 is where almost all orders landed. The DCO burst (2 re-POSTs 1.0-1.5 s after a demand-throttle 429) is armed on evidence the newest note weakens.

9. **Multi-SKU dispatch, Shape harvest and replay, pre_checkout retry (lines 895-1129; doc 03).** Up to 3 TCINs in flight with 3 workers per TCIN. A background tab per account performs human-like clicks so Shape mints signed headers, which are "banked" (6 sets, 300 s max age) and swapped onto the real add-to-cart shot: token replay, in security terms. The JS chain also retries pre_checkout up to 5 times in 1.5 s.

10. **Order-safety guards, won-cart loop, sentinel and session integrity (lines 1129-1489; doc 04).** Idempotency-style latches (ambiguous-commit, 5xx/2xx-ambiguous place-order) stop a second order when the first is unknown. The WC-1 direct loop fires checkout tickets until the product is out of stock (ride-to-OOS, cap 1200 tickets). Jar and sentinel guards stop GUEST sessions masquerading as MEMBER. `TARGET_SENTINEL_DEFER=1` (line 1424, armed 11-04, uncommitted code) skips the sentinel's page navigation during live stock. Many log-only telemetry flags and HUMAN/PX harvest parking sit here too.

11. **Retired experiments and the qty pin (lines 1490-1587; doc 04).** The "park alt-1/business off hot SKUs" experiment is commented out; BG-1 halves background volume for those two accounts. `TARGET_QTY_PER_TCIN=1` honours per-entry quantity, and every race entry is qty 2.

12. **Lanes, session-heal flags, pre-launch steps, wrapper loop (lines 1587-1924; doc 05).** The `TARGET_LANE_*` independent-shooter architecture is set but inert (`TARGET_LANE_MODE=0`, never run live). Keep-fresh and in-bot scripted relogin are OFF, so only the wrapper-start `relogin_one.py all` pass can mint a member token. The `:loop` at line 1818 relaunches `app.py` 10 s after any non-zero exit with no attempt cap; exit 0 stops it; exit 87 triggers a relogin recovery. **There is no nightly restart despite the filename.**

---

## 3. Inventory

| Item | Finding | Source |
|---|---|---|
| Total `set` lines | 239 (6 are `set /a` loop counters), so 233 variable assignments: about 229 config flags plus internals (PYTHON, LOGDIR, RUNLOG, EXITCODE). The repo's preflight counts **228** flag variables (narrower definition). | doc 06 A (VERIFIED) |
| Chunk-doc tallies | Doc 01: 44 bot-facing. Doc 02: 46 active (+1 commented). Doc 03: 34. Doc 04: 71 active (+2 commented `REM set`). Doc 05: 27. Sum = 222. This is 6 short of preflight's 228; the docs do not reconcile the gap (likely line-range boundaries or definition differences). Treat **228** as the headline and 222 as "documented per chunk". | docs 01-05 vs 06 |
| Duplicates | **0.** Checked case-insensitively; preflight independently reports each of 228 variables set once. In batch the last assignment silently wins, so a duplicate would be a latent mis-arm. Doc 01 also found no later `set` overriding the early ones. Only a commented `HOLD_CYCLES` line (509) repeats a live name (520), which is harmless. | docs 01, 02, 06 (VERIFIED) |
| Dead flags (set but never read in any .py) | **0 real ones.** `LOGDIR` and `RUNLOG` are batch-internal. `ENSURE_ARMED_EXIT87` is not found in Python by design (consumed by the batch itself), and three kill switches (`SINGLE_INSTANCE_GUARD_SKIP`, `DROP_READINESS_SKIP`, `WRAPPER_RELAUNCH_ON_CLEAN_EXIT`) are batch-only. Caveat: "read somewhere" is not "effective on the live path" (for example `TARGET_ATC_DEAD_TOKEN_MIDWINDOW_REPAIR=1` is inert while `TARGET_TOKEN_KEEPFRESH=0`). | docs 01-06 (VERIFIED) |
| Inert-by-design but armed | Gate-breaker STREAK/COOLDOWN/COUNT_EDGE/TTL (breaker off), `TARGET_FP_CHROMIUM_SKIP`, `TARGET_FP_SEED_SALT` (master off), `TARGET_401_PULSE` + its 3 tuning lines (inert under WAVE_FIRST_ONLY=1), all `TARGET_LANE_*` (MODE=0), `TARGET_CHROME_RELAUNCH_DESYNC_S` (doc 04: "inert" not fully established). | docs 01, 02, 04, 05 |
| Bat values that differ from code defaults | **At least ~60 documented** (my tally from the docs' tables; the docs give no single total): about 19 in doc 01, 15 in doc 02, 17 in doc 03's explicit table, at least 5 in doc 04 (RESHOOT_FORCE_REWARM, WONCART_YIELD_FLEET, HARD_MAX_TICKETS, SENTINEL_STAGGER_S, LOGIN_CHECK_TIMEOUT_S) and at least 6 in doc 05 (RELOGIN_MAX_PER_6H, TOKEN_KEEPFRESH, TOKEN_MINT_LOG, START_VALIDATE_RETRIES, BANK_NEWEST_FIRST, TOKEN_MAX_USE_AGE_S). Doc 04 says most of its 71 flags default to the prior behaviour (0) in code, so the true count is probably higher. Consequence: **the .bat is load-bearing; a lost or typo'd line silently reverts to a weaker or falsified path** (for example a bare launch re-enables `TARGET_ATC_NATIVE_FALLBACK`, which fired 1,468 times returning 401/429 each time). | docs 01-05 |
| Read by code but NOT set in bat | **104 names** fall back to code defaults (a floor: the scan only catches literal-name `environ.get`-style calls). | doc 06 B (VERIFIED) |

**Money-relevant variables read by code but NOT set in the bat** (doc 06 B; you inherit these defaults with no visible line):

| Variable | Code default | Why it matters |
|---|---|---|
| `TARGET_API_PLACE_ORDER` | `true` (app.py:51 setdefault) | Real API place-order is ON by default. Doc 02 flagged this as unverified; doc 06 verified it. |
| `TARGET_API_CART_CLEAR` | `true` (app.py:52) | Cart clearing is on by default. |
| `TARGET_API_PLACE_ORDER_OBSERVE` | `false` | There is no observe-only dry-run mode in effect. |
| `TARGET_QTY_CEILING` / `TARGET_FORCE_QTY_1` / `TARGET_QTY_OPTIMISTIC` | `2` / `false` / `1` | Maximum units per order when no pin applies. |
| `TARGET_WORKER_POOL_SIZE` | `1` | Worker cap default. |
| `TARGET_RACE_ALL_WORKERS`, `TARGET_RACE_JOIN_FREE` (+`_GAP_S`, `_BUDGET_S`, `_FRESH_S`) | `1`; `0` / `10` / `30` / `5` | How many accounts race one TCIN and whether late joiners are admitted. |
| `TARGET_RETRY_WHILE_IN_STOCK`, `_BUDGET_S`; `TARGET_RETRY_CHECKOUT_BUSY` | `1`, `110`; `1` | Retry loop on/off and its 110 s time budget (the binding limit given `RETRY_WHILE_IN_STOCK_MAX=40`). |
| `TARGET_FOREIGN_CART_BAIL`, `TARGET_BOOT_CART_AUDIT` | `1`, `1` | Duplicate/foreign-cart protections (fail-closed). |
| `TARGET_CART_HOLD_CHECK` / `_VERBOSE` | `1` / `1` | Cart-hold polling on. |
| `TARGET_CVV_REQUIRED` | `''` (latch files decide) | alt-1 has a 101-day-old CVV latch file. |
| `TARGET_SESSION_SENTINEL` (+`_INTERVAL_S` 300, `_INITIAL_DELAY_S` 90, `_CYCLE_INTERVAL` 150) | `1` | Sentinel is ON by default; SENTINEL_DEFER's knobs (`_DEFER_RECENT_S` 180, `_DEFER_MIN_TOKEN_S` 900, `TARGET_SENTINEL_QUIET_LOCAL` `01:30-04:30`) also live here. |
| `TARGET_SENTINEL_HOMEIP_RELOGIN`, `TARGET_TOKEN_MIN_TTL_S`, `TARGET_RELOGIN_COOLDOWN_S` | `1`, `1800`, `1200` | Session-heal behaviour. |
| `TARGET_REARM_KICK`, `TARGET_ATC_DCO_BURST_ANY_BODY` | `0`, `0` | Built but not armed. |
| `TARGET_ATC_RETRY_DELAY_MIN/MAX`, `TARGET_CHECKOUT_INPLACE_DELAY_MIN/MAX` | `2.5`/`3.5` each | Retry pacing. |
| `TARGET_WRITE_AUTH_CONFIRM_N`, `TARGET_DOM_FALLBACK_ON_NO_RESPONSE` | `3`, `0` | Dead-token confirmation count; DOM fallback after a no-response place-order is off. |
| `TARGET_EXIT_ON_LOGIN_HALT` (+ boot Chrome reap, faulthandler, quickedit) | `1` | Controls the exit-87 not-logged-in path. |

Good hygiene noted by doc 06: credential variables (`TARGET_EMAIL`, `TARGET_PASSWORD`, etc.) are not set in the launcher. Doc 06 also found **no currency spend cap flag**; limits are structural (qty 2 per TCIN, ticket counts, latches, claim registry). Doc 02 adds that per-account `cvv` values live in plaintext in `config\target_accounts.json`.

---

## 4. Consolidated readiness verdict for the next restock

**Verdict: CAUTION (OK to boot and watch; not set-and-forget).** Doc 06 reached CAUTION; the merged evidence supports keeping it. Why not NO-GO: zero duplicates, zero dead flags, lanes OFF as intended, TEST_MODE deliberately false, the three new offline test files pass, preflight (GO-WITH-WARNINGS: 25 OK, 8 WARN, 2 UNKNOWN, no FAIL) has no failure, and every chunk doc found every flag has a code consumer. Why not GO: a newly armed flag rests on uncommitted, never-live code; business's Chrome profile reads GUEST; auto-arm is disabled. The chunk docs add several risk items (ride-to-OOS volume, member-token dependency, an unbounded crash loop, weakened DCO-burst evidence), but each is a known and bounded trade rather than a demonstrated fault, so none changes the word to NO-GO. Note that most "Known-weak" items are accepted operator bets with evidence from 07-2026 to 10-2026; see section 6.

Tags: **VERIFIED** = checked in code/tests/files; **INFERRED** = from comments or reasoning.

### Blockers (resolve or consciously decide before booting) - 3 items

1. **Commit (or consciously keep) the uncommitted code behind `TARGET_SENTINEL_DEFER`.** Doc 06 F1/C; doc 04 section 3.7; bat line 1424. The bat arms a flag whose reader exists only in the dirty working tree (`bulletproof_purchase_manager.py` +118 lines, `purchase_executor.py`, `limkey_guest_follow.py` +258, `tests/run_offline_suite.py` +3). Revert the tree and the flag silently becomes a no-op. The modified guest-follow script already runs via scheduled tasks. **VERIFIED** (git diff --stat, grep, preflight `repo.since_last_boot`). Action: commit the code and tests, or deliberately revert both flag and code; re-run preflight until `repo.dirty` reflects your intent.

2. **Resolve business profile = GUEST.** Doc 06 D/F2 (preflight `accounts.profile_guest`: live Chrome profile reads sut=G per newest relogin.log while its jar file says member, 35.5 h old); related guest-session hazard in doc 04 section 3.7 (JAR_GUEST_GUARD at 1399, LOGIN_PROBE_ACCOUNT_FALLBACK=0 at 1394). A GUEST buyer at flip time yields no orders for that identity. Problem **VERIFIED**; fix effect **UNVERIFIED**. Action: run `relogin_one.py` validate-first or hand login; require 3/3 MEMBER in the boot drop-readiness echo before the window.

3. **Decide whether the bot should start tonight; auto-arm is off.** Doc 06 D/F3: `state/ensure_armed.disable` (created 2026-11-03 23:45) stops the 23:15/01:30 `ensure-armed` tasks from starting the bot, and no `app.py` was running. Only the Startup-folder `TargetBot.lnk` or a manual launch starts it. File presence **VERIFIED**; semantics **INFERRED**. Action: confirm this is intended; if not, remove the file; if yes, boot manually at the planned time. Preflight also confirmed the user-environment `ENSURE_ARMED_*` values agree with the bat (`host.profile_signals_env` OK); doc 05 says change both if you edit them.

### Must-confirm before boot - 9 items

4. **Member-token freshness.** Doc 05 sections 3.3 and 8.3; bat lines 1691 (`TOKEN_KEEPFRESH=0`), 1699 (`RELOGIN_MAX_PER_6H=0`). A member token lives about 4 h; with keep-fresh and in-bot relogin off, only the wrapper-start `relogin_one.py all` pass (or a hand login) can mint one. Starting the wrapper more than about 4 h before the drop risks a dead token with no in-bot repair. The finding is dated 09-28; docs do not say if minting was repaired since. **INFERRED**. Action: read the latest post-run to see if minting works; time the boot to the window; confirm MEMBER at boot.

5. **Watch SENTINEL_DEFER as an experiment.** Doc 04 3.7, doc 06 F4; line 1424. Armed 11-04, verified offline only (6/6 tests here, 80/80 per doc 04), no live run. Residual risk: the sentinel is the only periodic guest detector, and defer skips it during live stock and 01:30-04:30 local (only when token TTL >= 900 s). Flag/knob reads **VERIFIED**; readout criteria **INFERRED** from bat text. Action: boot with `tools/predrop/boot_check.py --follow`; afterwards run `python tools/events/q.py first_shot_delay --run ID` and grep `[SENTINEL]`; PASS = each ident <=150 ms in >=90% of windows with no ERR_UNAUTHORIZED/guest increase. Kill: `TARGET_SENTINEL_DEFER=0`.

6. **Confirm the live-money posture.** Doc 06 E/F5; doc 04 R4; lines 292, 1338, 1238, 1586. `TEST_MODE=false` (real orders), API place-order default true (not in bat), `TARGET_REBUY_NONBUYERS=1`, `TARGET_WONCART_HARD_MAX_TICKETS=1200`, qty 2 on every one of 25 enabled TCINs across 3 accounts, no currency cap, and no dry run. Values **VERIFIED**; the "multiple orders by design" meaning **INFERRED**. Action: operator signs off on exposure; after any purchase, reconcile order history against `[AMBIGUOUS_COMMIT]` log and `state\rebuy_buyers.json`.

7. **Confirm the production launch path always goes through the bat.** Doc 02 section 5/6, doc 03 R5, doc 01 R7. Code defaults for the doctrine flags are off (wave-first, bank gate, OOS stop, stuck-reset guard, DCO burst) or on for falsified features (native ATC fallback, per-retry dummy POST). A launch that does not source the bat inverts the doctrine. Also `.build_wt/` holds stale copies of the bat and scripts. **INFERRED** (code defaults VERIFIED by greps; actual launch paths not enumerated). Action: verify `bot_ctl.py`, the Startup link and any scheduled task all call the root bat.

8. **Re-confirm the detection fix and pool health.** Doc 01 R2; line 106 (`apps_raw`); doc 06 preflight `monitor.pool_config` (10 active + 2 reserve exits, <15 recommended). Detection hinges on one flag and one IP-reputation assumption; evidence is a 09-09 probe; the app header fingerprint (version 2026.28.0, iOS 26.4.1) is hardcoded and ages. apps_raw channel confirmed present by preflight (**VERIFIED**); that it still beats the HUMAN wall today is **INFERRED**. Action: run `probe_sweep_pool.bat` pre-drop; look for the `[GROUND-TRUTH] pool cache-bust ok` line and TCIN visibility not PARTIAL (visibility data was 34 h old in preflight).

9. **Verify the harvest TCINs are still in stock and ship-eligible.** Doc 03 R4; line 1017 (`21516452,50225561,53274278`, chosen 09-03). If none work the bank empties and shots go page-signed. **INFERRED**. Action: spot-check on target.com or in the boot harvest log.

10. **Check account configuration against the narrative.** Doc 01 R3 and sections 3.8/7; doc 02 3.1 and R8; doc 06 preflight `accounts.cvv_latch`. (a) The primary account's `proxy_url` is emptied in `config\target_accounts.json` (home-IP purchases) and the bat does not enforce this. (b) Each account's `cvv` field must exist for the in-lane CVV PUT. (c) alt-1's CVV latch file is 101 days old. (d) `hand_login_all.bat` must mirror `TARGET_UA_MODE=engine` (doc 01: not checked). **INFERRED** except the latch age (**VERIFIED**). Action: inspect the JSON and latch file; grep `hand_login_all.bat`.

11. **Decide on the DCO burst.** Doc 02 R1, doc 03 R2, doc 06 F8; lines 891 (`TARGET_ATC_DCO_BURST=1`) and 973 (`REQUIRE_BANK=0`). The 11-04 REM annotations say the premise does not reproduce: re-POST edge-pass 15/48 (pre-09-17) fell to 1/19 in the flip era (doc 02), and 3/33 vs 0/8 on ident-tagged data (doc 03). These are two different cuts of C-1104-04, not a contradiction. Page-signed bursts drew 429 6 of 6 on 09-18. Text/values **VERIFIED**; benefit **UNPROVEN**. Action: operator decision; kill with `TARGET_ATC_DCO_BURST=0` or restore the bank gate with `REQUIRE_BANK=1`.

12. **Host stability.** Doc 06 F6: Windows Update not paused (install about 03:00; forced restart hit on 06-24); unexpected shutdown (Kernel-Power 41) on 11-02 21:25; `parsecd.exe` running (docs tie 20/27 GPU-crash wakes to remote-wake codes). **VERIFIED** by preflight. Action: pause Windows Update; avoid Parsec/RDP during the window. Also confirm the Chrome-signout step at wrapper start is acceptable (it closes the operator's personal Chrome; doc 05 R5).

### Known-weak-but-accepted (deliberate bets or bounded risks) - 13 items

13. **Ride-to-OOS is high-volume with weak evidence.** Doc 04 R1; lines 1219, 1226, 1237-1239. About 20 place-order POSTs a minute per won cart for up to about an hour (cap 1200). Doc 04 says conversion on a hot cart is NOT ESTABLISHED, and cites 0 of 574 place-orders after the first ~2 s ever receiving a 200 (C-1104-03). **INFERRED** benefit; values **VERIFIED**. Check blast radius: could a lockout hit the other two accounts or the shared home IP? Kill: `TARGET_WONCART_RIDE_TO_OOS=0`.

14. **Multi-SKU cap is neutralised.** Doc 03 R1; lines 949, 959. WORKERS_PER_TCIN=3 equals the 3-account fleet, so CAP_ALWAYS never binds and a second hot TCIN can get MULTI_SKU_MISS. *Doc nuance:* doc 03 audited only its own range; doc 04 (lines 1318-1327, 10-06/10-14) shows FAIR_SHARE, MISS_REARM and KEEP_LIVE_FAILED were added later to split accounts when TCINs flip in the same sweep, which partly mitigates the loss. A TCIN that goes live while all three accounts are already racing another remains unserved. Fewer than 3 logged-in accounts was not examined.

15. **Trusted-reader cadence on the primary purchase account.** Doc 01 R1; lines 175, 193-194. 3-5 s is 3.6-6x the only measured tolerated rate (about 200 reads/h on 09-07), and the HUMAN budget was never measured. It engages only when the pool is blind for 180 s, which is the pre-drop failure mode. A Press & Hold on the primary tab could cost the purchase account. Kill the exposure: `RESILIENT_FORCE_TAB_FETCH=0`.

16. **Shared device fingerprint with the gate breaker OFF.** Doc 01 R3; lines 282, 381. All three accounts run real Chrome on one machine (the 07-12 note calls device fingerprint the top cross-account linker), and a genuine identity-hardening wall is hammered at full cadence. A hard Shape device block would hit all three at once.

17. **Replay age of 300 s rests on vendor claims.** Doc 03 R3; line 1079. Measurement only covers up to 100 s; a stale-token replay shows as an ordinary Shape 401. The boot self-test does not cover mid-run. Rollback: `TARGET_HARVEST_MAX_REPLAY_AGE_S=100`.

18. **FAST_CADENCE alt-1 A/B may burn alt-1's budget.** Doc 04 R3; line 1373. Experiment E2 says Shape admits about 6 protected POSTs, then answers keyless 401 for 20-50 min at any cadence; fast re-shots spend that in seconds. Readout is NO-DATA until a window opens. Kill: `TARGET_FAST_CADENCE_IDENTS=0`.

19. **Unbounded crash loop and unreliable Ctrl+C.** Doc 05 R1, R2, R6; lines 1818-1924. Any non-zero exit relaunches after about 10 s forever with no counter; a double Ctrl+C (exit 1) or hung shutdown (deadman exit 2) also relaunches; the single-instance guard fails open on any non-9 error. **VERIFIED by reading the loop** in doc 05. Action: stop only with `python tools\ops\bot_ctl.py stop` and confirm "stopped"; do not edit config right before a drop.

20. **Rebuy plus qty 2 everywhere can place several orders by design.** Doc 04 R4, R6; lines 1338, 1586. Residual NOT ESTABLISHED risk: a committed order answered as a non-ambiguous non-2xx. The 09-18 evidence that qty 2 can fail where qty 1 succeeds is unresolved; nothing retries at qty 1.

21. **Won-cart hold monopolises the TCIN.** Doc 02 section 3.3 and notes; lines 504, 514-521. Up to about 4.5 min per call keeps the TCIN from other accounts. Deliberate (ride cap 300 s, not 900).

22. **Unconfirmed hypotheses are armed as if fixed.** Doc 02 notes; lines 562, 593, 642. WARMUP_PAUSE_DURING_PURCHASE is a labelled HYPOTHESIS; CDP_DEDUP_CONTINUE's "dedup hit #N" log check has no recorded verdict; CHROME_MAX_AGE_S=2100 is a mitigation (proxied Chromes still blip every 35 min).

23. **CVV in plaintext config.** Doc 02 section 3.1 and notes. `config\target_accounts.json` holds per-account `cvv` replayed by the in-lane PUT. A credential-handling exposure for the security review; an account missing the field cannot use the PUT.

24. **Extra request volume during live windows.** Doc 03 R6, R8; lines 986 (`TARGET_CART_HOLD_CHECK_INTERVAL_S=1.0`, four times the code default of 4, no rationale in the range) and 1126 (`TARGET_HARVEST_FRESH_PAGE_LIVE=1`, product-page reloads during a live purchase; the WEDGE-PROBE/TAB_HEALTH readout has no record). Confirm intent; if wedge lines cluster around reloads set `_LIVE=0`.

25. **HTTP 206 bodies remain unused for detection.** Doc 01 R2/3.5; lines 114-134. RedSky answered 73-95% of sweeps with 206 in some windows; the shadow ingest never fires a purchase. Strategic gap, not a defect.

### Hygiene (cheap fixes, low drop-day risk) - 7 items

26. **Documentation loss.** Doc 06 F7: `CLAUDE.md`, `docs/CLAIMS.md`, `.claude/state/CURRENT_STATE.md`, `docs/NEXT_DROP_CHECKLIST.md`, `whatHappensNext.md` (about 3,800 lines) are deleted in the working tree. New bat comments cite `docs/CLAIMS.md` C-1104-04 and preflight's state-cite checks cannot run (`repo.state_asof` UNKNOWN). **VERIFIED.** Restore or consciously accept.

27. **Stale or inert lines can mislead the next editor.** Docs 01, 02, 04. Breaker parameters, FP_CHROMIUM_SKIP/SEED_SALT, the 401_PULSE trio (785-788), DESYNC_S (1535), commented park/offset lines, the U1 (a)/(b) history block.

28. **Kill-by-delete traps.** Doc 04 section 5, doc 05 section 7, doc 02. Deleting a line does NOT disable some flags: `TARGET_PO_5XX_AMBIGUOUS` and `TARGET_STOCK_PROBE` (code default 1); deleting `TARGET_WONCART_YIELD_FLEET`, `TARGET_RESHOOT_FORCE_REWARM` or `TARGET_RELOGIN_MAX_PER_6H` (code default 2) RE-ENABLES the behaviour the 0 disarms; deleting `JITTER_S` restores 3 s, not "no jitter". Rule: kill by setting the explicit value, never by deleting.

29. **Cross-range coupling.** Doc 02, doc 04. `TARGET_ATC_DEAD_TOKEN_MIDWINDOW_REPAIR=1` (line 731) is safe only because `TARGET_TOKEN_KEEPFRESH=0` (1691): keep both or neither. `TARGET_FOREIGN_KEEP_WON` is inert unless `TARGET_PO_2XX_AMBIGUOUS=1`.

30. **Guest-follow tasks.** Doc 06 D. No bat flag exists for guest-follow; the slot grid is CLI arguments. Only `\TargetBot\guest-follow 2340` and `0135` are registered; arms bd0/bd0b/bd1 in `tools/experiments/guest_follow_arms_1104.md` are not registered and nothing there has been run. `state/guest_follow.enable` (2026-10-16) is present, so tonight's home follower runs the modified script. Blast radius is a non-purchasing guest sampler (bandwidth and noise, not money). Register arms only if wanted.

31. **Stale operator documents and names.** Doc 06: `docs/DROP_DAY_PLAYBOOK.md` is a stale Walmart "capture run, FINAL_PURCHASE=NO" doc (2026-08-17) whose "will NOT place an order" claim is FALSE for the current bat; do not use it as a checklist. Doc 05: the filename says "nightly restart" but none exists (header lines 8-10).

32. **Cosmetic only.** Mojibake (`?` for em-dashes), a literal tab eating a backslash in REM paths (e.g. `tests<TAB>est_...`), and the exit-87 echo reading "4/3" (effective behaviour is 3 recovery cycles, with the 4th triggering the 10 min sleep).

Items not verifiable without a live boot (doc 06 F10): harvest self-test, ground-truth read, TCIN visibility freshness, per-account egress IPs.

---

## 5. Comment vs code drift

Line numbers are those given by the chunk docs (a `~` means the doc was approximate). Docs that agree are cited together.

**Doc 01 (lines 1-410)**
1. Bat vs code defaults (so removing a line changes behaviour): `RESILIENT_PER_IP_MAX_RPS` code 1.0 vs bat 0.5 (97 area); `RESILIENT_RAW_404_PARK_S` 120 vs 900; read cadence 1-2 s vs 3-5 s (193-194); `RESILIENT_REDSKY_CHANNEL` `web` vs `apps_raw` (106); `TARGET_ATC_RESP_HDRS`/`_RESP_LABEL` 0 vs 1; `TARGET_UA_MODE` `legacy` vs `engine` (258); `STOCK_CANARY` on vs off (149); `TARGET_RETRY_WHILE_IN_STOCK_MAX` 24 vs 40 (303). Correction: the bat is the only thing arming the detection fix.
2. Lines ~76-86: "a lone clean IP is read at 1/s" is superseded by the 09-09 note and bat value 0.5/s; code default is still 1.0.
3. Lines ~39-50: "1.5/s halves RedSky load" is history; value is 3.0, and "Revert: 3.0" describes a revert already applied.
4. Lines 180-194: first cadence paragraph argues 1-2 s; the "09-09 CORRECTION" sets 3-5 s; the app.py docstring (~1270) still argues 1-2 s. Active value follows the correction.
5. Lines 209-235: "Rollback: delete these three SET lines" is stale; the lines now mean the opposite (0/1/1), so deleting does not roll back.
6. Lines 341-394: the breaker block reads as active but is OFF since 08-31 (code default also off). STREAK_LIMIT/COOLDOWN code defaults are 8/120, so a bare enable without the bat's 20/45 uses the stricter values.
7. Line 292: comment says `TEST_MODE=true` makes app.py a no-op; code also uses it for a repeated-fire test loop in `stock_check_resilient.py:455` (accepts 1/true/yes). Harmless while pinned false.
8. Line 283: `TARGET_FP_CHROMIUM_SKIP=business` comment ("keep ONE identity on real Chrome as control") is obsolete since all three run real Chrome (09-03).
9. REM-only encoding glitches (`config<TAB>arget_accounts.json` ~270; `?` for em-dashes). No runtime effect.

**Doc 02 (lines 407-895)**
10. Line 657: "all default-ON in code" is true for the five 08-02 guards but not for later flags (WAVE_FIRST_ONLY, 401_PULSE, SHOT_BANK_GATE, RETRY_STOP_WHEN_OOS, STUCK_RESET_LIVE_GUARD, DCO_BURST, DCO_AS_EDGE_CADENCE, CART_HOLD_SKIP_EDGE, FS_COOLDOWN_PER_TCIN all default 0 in code). Conversely NATIVE_FALLBACK, RETRY_WARM and SHOT_TTL_REFRESH default 1 in code and are held off only by the bat.
11. Lines 509/520: the 09-11 REM calls `HOLD_CYCLES=1` the "exact prior" behaviour; code default is 2 cycles / 80 s (executor comment ~11626). Bat sets 6 / 270 s. Line 509 (commented) duplicates the name at 520.
12. Line 832: WAVE_FIRST_EDGE prose reads "1 to 0" then "KILLED ... back to 1"; the set line is 1 (final state).
13. Line 885: names `TARGET_CART_HOLD_CHECK_INTERVAL`, which is not a real variable (the real `_S` one is at line 986); the "4 to 1 s" claim holds only if line 986 is intact.
14. Line 731: repair is "inert only because KEEPFRESH=0"; code default for KEEPFRESH is 1; the 0 is at line 1691 (outside the range).
15. Line 785-788: 401_PULSE is armed but inert under WAVE_FIRST_ONLY=1; successor `TARGET_IDENTITY_REST` is NOT armed (0 at 1537).
16. "Exact prior behaviour" claims on kill switches were not independently re-verified (doc 02).

**Doc 03 (lines 895-1129)**
17. Line 925: cites `_manager.py:2233` as where the retry loop breaks on success; that location is now a state-reset/diagnostic print (citation stale; behaviour not re-verified).
18. Lines 961-967 vs 973: the 09-18 paragraph says a burst "now fires ONLY with a replayable banked set" (matches code default `REQUIRE_BANK=1`) but the bat sets 0 (10-11 note). Two inverse "Kill" statements, each correct for its date. The 11-04 annotations undermine the REQUIRE_BANK=0 rationale while the setting is unchanged.
19. Line 959: "=1 caps EVERY TCIN"; with WORKERS_PER_TCIN=3 on a 3-account fleet the cap never binds. Code does what it says; the purpose is not served.
20. 09-20 pre_retry REM: "5 draws inside the first second" is approximate (4 gaps of 250 ms plus latency; the 1500 ms budget is the real bound).
21. `purchase_executor.py:5360` comment still describes the disproven 09-04 premise (primary shares the loop with the sweep); the REM at lines 1033-1037 says it was disproven 09-09.

**Doc 04 (lines 1129-1587)**
22. Lines ~1133-1135: header says every flag "defaults to the prior behaviour in code ... or deleting the line". Exceptions: `TARGET_PO_5XX_AMBIGUOUS` and `TARGET_STOCK_PROBE` (code default 1, so the bat's 1 is a no-op and deleting does not disable); `TARGET_RESHOOT_FORCE_REWARM` and `TARGET_WONCART_YIELD_FLEET` (code default 1; the bat's 0 is load-bearing).
23. ~Line 1214: "delete the JITTER_S line" restores code default 3 s, not "no jitter".
24. ~Line 1272: TTL "900 to 3600, the code's max" holds for the executor (clamp 60-3600) but the manager clamps 30-86400; they agree today only because 3600 is inside both.
25. ~Lines 1359-1366: FAST_CADENCE first paragraph says "on for every account / no need for a flag"; final set value (line 1373) is `alt-1` (10-22 revert, 10-23 re-arm). Trust the set line.
26. ~Lines 1523-1535: BG-1/DESYNC block says the scheme is inert (only proxied Chromes relaunch) yet `DESYNC_S` is still set and read (`session_manager.py:119`); whether it is gated on the proxied path was not traced (NOT ESTABLISHED).
27. `HARD_MAX_TICKETS` "safety ceiling": code clamps 50-1500 with default 150, so 1200 is accepted and the true ceiling is 1500. Consistent, not a contradiction.

**Doc 05 (lines 1587-1924)**
28. Burst counter: echo says "relogin cycle N/3" but the test is `GEQ 4` after incrementing, so the log can read "4/3" (cosmetic).
29. Header and 09-15 comment imply Ctrl+C stops the wrapper; true only for single-Ctrl+C clean exit 0. Double Ctrl+C (exit 1) or a >15 s wedge (deadman exit 2) relaunches.
30. File name "nightly restart" is stale; header says there is no timed cutoff.
31. Line 1699: `TARGET_RELOGIN_MAX_PER_6H` "Kill: delete this line" is misleading; code default is 2, so deleting re-enables scripted relogins.
32. Line 1713: `TARGET_START_VALIDATE_RETRIES` code default 0; removing the line silently drops the retry (dependency).
33. TOKEN_KEEPFRESH block still contains the 07-12 "Keep-fresh ON" paragraph above the 09-28 text saying OFF; value 0 matches the later text.
34. The "or the OS is logging off" clean-exit claim is UNVERIFIED (Windows logoff may not map to the handled signals).

**Doc 06**
35. Bat comments cite `docs/CLAIMS.md` (C-1104-04), which is deleted in the working tree.
36. Docs disagree on one point: doc 02 listed `TARGET_API_PLACE_ORDER` ("app.py defaults true") as unverified; doc 06 verified it at app.py:51 (setdefault true).
37. Hook warning "bat changed AFTER the state file" is true but largely moot because the state file is deleted (doc 06 C).
38. Doc 06's own count (228 preflight / 233 assignments) differs from the chunk-doc sum (222); see section 3.

---

## 6. Stale adversary facts

These are the dated claims about Target's anti-bot behaviour that the docs flagged. They are the operator's observations at the stated date, reported as history, not current fact. Oldest first.

| Date | Claim | Doc |
|---|---|---|
| 2026-05-07 | Dropping the checkout navigation failed with 424 CART_COMPARISION_FAILURE because pre_checkout was un-awaited. | 02 |
| 2026-06-24 | Stale Chrome 130 / macOS-on-Windows spoofed identity was flagged by Shape. | 01 |
| 2026-06-30 | A worker fired 24 ATC shots, all instant 429; pre-ATC 429s cannot double-buy. | 01 |
| 2026-07-12 | BD-IP logins are Shape-blocked ("password did NOT advance"); device fingerprint is "the top cross-account linker"; ATC 401 is the write-auth/member-token layer. | 01, 05 |
| 2026-07-17 to 07-21 | Checkout POST 429 FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION; FAST_SELLING answered 0 of about 20 re-shoots and 2 min of quiet cleared it; 07-20 ATC 201 at 0.92 s but place-order drew RESERVATION_FAILURE; 07-20/21 CVV challenge (0-for-9). | 01, 02 |
| 2026-07-23 to 07-28 | Wedges on a rigid clock 70-75 min after Chrome launch; CVV PUT endpoint captured with no Shape headers (07-24/07-28); 07-28 won 4 carts, cleared all, 0-for-250. | 02 |
| 2026-07-31 / 08-04 | Only drops with zero CVV failures that converted (9 and 4 orders); 07-31: Target moves items to "Saved for later" after 424. | 02, 04 |
| 2026-08-06/07 | Restock went 0 carts on about 2,960 ATC; native fallback fired 1,468 times returning 401/429. | 02 |
| 2026-08-09 to 08-31 | Gate breaker history; 08-21 401 ladder converted 0/85; 08-21 to 09-03 fp-chromium 0/318 vs real Chrome 9/146 (IP-range confounded); 08-26 edge-429 doctrine bet; 08-28 stack: 84% of shots empty-429, 100% of limiter-passing shots died 401, wave-first 32.6% vs 0.9%; 08-31 hot-SKU 401 re-forms in 45-95 s at any cadence. | 01, 02 |
| 2026-09-03 | Main-tab ATC carried the `-a0` chunk 0 of 53,950 times and never a real click; all hot-SKU windows since 08-07 died 100% 401. | 03 |
| 2026-09-04 to 09-09 | Two app.py instances at about 6 req/s poisoned BD pool HUMAN standing for days; home-IP sweep got zero 200s; 09-06/07 relogin 0 of 12; 09-07 probe 3 of 4 pool IPs captcha, home-IP browser tolerated about 200 reads/h; 09-09 per-IP 0.5/s clean vs 2/s 404 ban after about 166 reads; 09-09 apps_raw returned 200 where web returned 403; 09-09 census 19/20 orders on attempt 1, 0/13,244 at attempt >= 2. | 01, 02, 05 |
| 2026-09-10 to 09-13 | CDP wedge 49-84 min after launch only on proxied Chromes; UA/brand mismatch between harvest and purchase tabs; 09-13 forensics that every win rode a small first-click Shape set (no `-a0`). | 01, 02, 03 |
| 2026-09-16 to 09-23 | alt-1 passed the limiter 0/120 and business 0/331; limiter behaves as a shared per-TCIN bucket (104 logs, 9.6% to 0.4%); 09-18 burst re-POSTs 6/6 drew 429; 09-18 qty-2 reservation failures; 09-21/09-23 cap evidence NOT ESTABLISHED; 09-23 shots fired 0.1-19 s after OOS reads. | 03, 04 |
| 2026-09-25 | RedSky 206 rates 73-95% (also 10-05, 17 of 17 TCINs had stock fields in bodies); member-token mint rate fell from 80/80 to 0/62 at 06:01; 663 edge re-shots produced zero admissions. | 01, 02, 05 |
| 2026-09-28 | Token refresh 404 on 253/253 tries; scripted relogin 0/6 succeeded and left accounts GUEST; repair deleted live tokens about 3.5 h after mint. | 05 |
| 2026-10-05 to 10-11 | PX challenge can clear in place (10-05); 10-09: 686 of 690 POSTs drew edge 429 and three accounts re-probed one challenged document 139 times over about 4 h; 10-09 04:32 only admitted shot got no burst. | 02, 03, 04 |
| 2026-10-14 to 10-18 | Two guest accounts logged logged_in=True all morning (10-14); FAST_SELLING back-off per-account bug; egress-bind verifier refuted four grounds; 10-16 fast cadence 0/19 vs 4/136, drained bank; E2: Shape admits about 6 protected POSTs then keyless 401 for 20-50 min; 10-09/10-16 all 30 DEAD-token verdicts false (C-1018-16). | 02, 04 |
| 2026-10-23 to 10-26 | Lanes refused 119/119 shots on both home and residential IPs (residential-beats-home is UNPROVEN); harvester Chrome freezes on residential after 17-21 min; HUMAN wall is profile-bound. | 05 |
| 2026-10-30 / 11-04 | Sentinel navigation collided with 3 of 5 windows (first-shot delays 621-7,996 ms vs 28-47 ms). 11-04 re-analysis: DCO re-POST edge-pass 1/19 in the flip era, 3/33 vs 0/8 on ident-tagged data (C-1104-04), 0 of 574 post-2s place-orders got a 200 (C-1104-03). The only entries that already contradict an earlier claim. | 02, 03, 04 |

**Anything older than about 2 weeks (that is, before about 2026-10-22) should be re-derived before being relied on.** Doc 01 notes its dated claims are already 2 to 10 weeks old; doc 04 says to treat them as hypotheses until re-measured. Also unmeasured: the "1/s ban floor" and the "vendor-safe 1000-4000 ms band" (operator estimate and competitor docs, not measured on this target).

---

## 7. Kill switches cheat sheet

Always set an explicit value; do not delete lines (see Hygiene item 28). Edit the bat, then restart through `python tools\ops\bot_ctl.py restart`. Values are as set in the working tree on 2026-11-05.

| Control | Variable | Line | Current value | Value that turns it off / safe state |
|---|---|---|---|---|
| Sentinel defer (11-04 experiment) | `TARGET_SENTINEL_DEFER` | 1424 | `1` | `0` (byte-identical to old behaviour) |
| Lane mode (independent shooters) | `TARGET_LANE_MODE` | 1614 | `0` (already off) | Keep `0`. Only the literal `1` turns it on (app.py:981); never run live. |
| DCO burst | `TARGET_ATC_DCO_BURST` | 891 | `1` | `0` (exact prior). Related: `..._REQUIRE_BANK` line 973 is `0`; set `1` to require a banked Shape set. |
| Won-cart ride (manager wait extension) | `TARGET_WON_CART_RIDE` | 514 | `1` | `0` (holds clamp to 80 s) |
| Won-cart ride-to-OOS loop | `TARGET_WONCART_RIDE_TO_OOS` | 1239 | `1` | `0`, plus `LIVE_CAP_EXEMPT=0` (1237), `YIELD_FLEET=1` (1226), TTL 900 (1275) per the REM |
| Won-cart direct checkout loop | `TARGET_WONCART_DIRECT` | 1188 | `1` | `0` (exact legacy path). Note ambiguous-commit latch `=0` makes the loop refuse to arm. |
| Fast cadence re-shoot (alt-1 A/B) | `TARGET_FAST_CADENCE_IDENTS` | 1373 | `alt-1` | `0` (or `all` to widen to the fleet) |
| Test mode / dry-run | `TEST_MODE` | 292 | `false` (REAL orders) | `true` makes the bot a no-op / test loop (only literal `true`, case-insensitive, in the purchase manager). Do not flip casually. No other dry run exists (`TARGET_API_PLACE_ORDER_OBSERVE` default false). |
| Rebuy (multiple orders per TCIN) | `TARGET_REBUY_NONBUYERS` | 1338 | `1` | `0` |
| Trusted-reader fallback on primary | `RESILIENT_FORCE_TAB_FETCH` | 175 | `auto` | `0` (never use the trusted browser to read stock); `1` = always |
| Trusted-reader cadence | `RESILIENT_READ_CADENCE_MIN_S` / `_MAX_S` | 193 / 194 | `3.0` / `5.0` | Not an off switch; widen to reduce load on the primary account |
| apps_raw channel | `RESILIENT_REDSKY_CHANNEL` | 106 | `apps_raw` | `web` (re-exposes the HUMAN captcha wall; pool goes blind) |
| Fast lane | `TARGET_FAST_LANE` | 435 | `1` | `0` (pre-07-21 navigation path) |
| In-lane CVV PUT | `TARGET_FAST_LANE_CVV` | 448 | `1` | `0` |
| Shape harvest and replay | `TARGET_SHAPE_HARVEST` | 1016 | `1` | `0` (replay only: `TARGET_HARVEST_REPLAY=0`, line 1030) |
| Multi-SKU dispatch | `TARGET_MULTI_SKU_DISPATCH` | 916 | `1` | `0` |
| Pre_checkout retry | `TARGET_FASTLANE_PRE_RETRY` | 1103 | `1` | `0` |
| Harvest reloads during live window | `TARGET_HARVEST_FRESH_PAGE_LIVE` | 1126 | `1` | `0` |
| Sweep rate | `TARGET_SWEEPS_PER_SEC` | 50 | `3.0` | Lower the value to reduce load (no zero kill stated) |
| Keep-fresh / in-bot relogin (keep OFF) | `TARGET_TOKEN_KEEPFRESH` / `TARGET_RELOGIN_MAX_PER_6H` | 1691 / 1699 | `0` / `0` | Leave at `0`. Deleting `MAX_PER_6H` re-enables relogins (code default 2). |
| Exit-87 forced login | `ENSURE_ARMED_EXIT87` | 1870 | `1` | `0` here; `ENSURE_ARMED_PROFILE_SIGNALS`, `_ROWLOG`, `_STATUS_FAILCLOSED` (1877, 1883, 1890) need `0` here AND `reg delete HKCU\Environment /v <NAME> /f` |
| Ambiguous-commit latch (do not kill) | `TARGET_AMBIGUOUS_COMMIT_LATCH` | 1142 | `1` | `0` removes double-order protection and the won-cart loop refuses to arm. Leave on. |
| Wrapper skips | `SINGLE_INSTANCE_GUARD_SKIP`, `DROP_READINESS_SKIP`, `WRAPPER_RELAUNCH_ON_CLEAN_EXIT`, `CHROME_SIGNOUT_SKIP` | 1752, 1809, 1841, (py) | not set | Set to `1` to skip the guard, readiness echo, relaunch-on-exit-0 (`=1` makes a clean exit relaunch), or Chrome sign-out |
| Stop the whole bot | `bot_ctl.py stop` | n/a | n/a | Sends Ctrl+C to the wrapper console, waits `--grace` (default 30 s), then kills leftovers. Confirm "stopped"; a manual double Ctrl+C relaunches. |

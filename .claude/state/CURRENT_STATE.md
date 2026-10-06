# CURRENT STATE — the only place live facts belong

**As of: 2026-10-05 ~23:50 (PRE-DROP for the next restock: four 10-05 gate-audit flags ARMED at bat:1208-1220, COMMITTED `eb8ebaba` (not pushed); the final offline-suite run on the armed tree was DENIED by the auto-mode classifier — the operator runs it; primary needs a forced hand login) · gate-hardening audit C-1005-G01..G15 earlier this evening · post-run of `run_20261005_002518` (`wf_9651193b-efc`) ~13:30 · branch `feat_refract_arch_v1` · ARMED 10-05 (log-only): `RESILIENT_STATUS_LOG=1`, `RESILIENT_206_INGEST=shadow` (bat:128-129) · ARMED 10-04: FX-1001-A `TARGET_HELD_LINE_FLIP_STRIKE=1` (bat:1199) · F1 unarmed (operator declined 10-04) · host display never sleeps on AC (10-04)**

Every line below carries a date and a source. **Nothing in `.claude/agents/` or
`.claude/agent-context.md` may restate a fact from this file** — those hold method
only, so that facts rot in exactly one place where the rot is visible.

**Before relying on any line here:** compare its date against
`git log -1 --format=%cd`, the mtime of `run_bot_with_nightly_restart.bat`, and
`config/product_config.json`. A fact older than the last change to what it
describes is `[NOT ESTABLISHED]` until re-derived. Update this file at the end of
any investigation that changes one of these lines, and **delete lines that turn out
to be wrong rather than leaving them with a caveat.**

---

## STATE CARD — read this first (2026-10-04; every line is detailed further down)

| Topic | Now | Source |
|---|---|---|
| Target list | the ENABLED TCINs in `config/product_config.json` are the operator's deliberate choice. **17 enabled, all qty 2.** 10-04, operator ("make sure all the 30th are enabled … dont remove anything"): enabled 1010892068 Sylveon ex Box, 1010892075 Espeon ex and 1010892071 Umbreon ex Battle Decks; ADDED 1010892074 30th Celebration Binder Collection (pre-order, release 12-04; TCIN [REPORTED] by two alert accounts, PDP 404 until it publishes; added with the operator's explicit approval outside auto mode, ~22:45, after the auto-mode classifier had denied it). 1010892070 Knock Out Collection was added, then REMOVED at the operator's request ("i dont want the knock collection", 10-04 ~22:40) — UNWANTED, never re-add. Pitch Black 1011483413 stays OFF. Backup `config/product_config_backup_pre_2026-10-05_drop.json`. Never propose re-adding SKUs | operator 09-30, 10-02, 10-04 |
| Accounts | **10-05: all 3 enabled.** primary was hand-logged (force) 00:06 → its live Chrome has had NO `login-session` cookie since (watchdog `'login-session' MISSING` ×565 all run), yet 0 `ERR_UNAUTHORIZED` and an in-bot member-token re-mint at 08:10:21 (C-1005-09) — so a re-mint does NOT need a live login-session (contradicts the older belief); whether pre_checkout / place-order pass without it is NOT ESTABLISHED, and a boot with primary as a guest exit-87-loops the WHOLE bot (worker 1 = primary). History — 10-04 23:5x: primary DISABLED in `config/target_accounts.json` (operator: no hand login; backup `config/target_accounts.json.bak_20261004_primary_off`) → the 10-05 drop runs on 2 accounts, business = worker 1, alt-1 = worker 2.** Why: the 23:49 boot exited 87 three times — primary's jar had gone GUEST (sut=G) between 21:43 (member) and 23:49, cause NOT ESTABLISHED (lead: bat:1395-1430, the sign-out block (`chrome_target_signout.py` at :1429) — primary IS the operator's personal Target login; a personal browser/app session on it can rotate the bot's token). The bat's login pass reported the guest as "already logged in ✅" (known blind spot). To restore: one hand login for primary, then `enabled: true`. Otherwise 3 (primary, business, alt-1), all on the home IP; member tokens re-mint in-bot at the 4 h expiry under `TARGET_TOKEN_KEEPFRESH=0` + `TARGET_RELOGIN_MAX_PER_6H=0`; run `check_session_readiness.py` before a drop | C-0929-01, 09-30 jars |
| Monitor | 18 Bright Data ISP exits, `apps_raw` channel, every TCIN read ~every 0.34 s, read → first POST ~40 ms | 09-30 readout L3 |
| Wall 1 — edge limiter | 429 `ERR_A2C_TCIN_RATE_LIMITED`, answers FIRST (no `x-ssx-hop`); not explained by Shape/HUMAN trust, differs by network location, splits within same-IP volleys — mechanism NOT ESTABLISHED; strongly per TCIN. **10-01 RECOVERY with no armed change:** 1010892076 past the limiter 58/230 three-wide-race shots vs 1/195 the night before (its first carts ever); flip-race first shots 13/14; later shots past it 41/164 — cause NOT ESTABLISHED. **10-05 audit (09-11→10-02, 2,747 main shots, 90.3% edge 429):** 9 fixed headers, retry-after 0, no node/POP header; "same-IP volleys all-or-nothing" NOT ESTABLISHED; first-window effect home-only, TCIN×night predicts most; own-volume effect UNVERIFIABLE both ways (the 09-20 bucket table is all-SKU); the edge-429 retry knobs are inert. Instrument built, unarmed: INS-ATC-NET | C-0930-03/04, C-1001-11/14, C-1005-G01..G05 |
| Wall 2 — SSX / Shape | keyless 401 after the limiter; trust lives here (09-30: home 13/38 denied, BD exits 514/518). **10-01: a per-TCIN admit-then-401 switch** — on 1010892076 the first 6 past-limiter shots were admitted, then 70/70 keyless 401 from all 3 accounts while decoy 401s held at ~20%; 80/105 past-limiter shots were 401 (parser v2). **10-05 audit: the "admits early, refuses late" split is that one TCIN-night** — outside it ~25% of early and ~41% of later past-limiter shots were keyless 401; bank age does not predict it; each harvested set is replayed once | C-0930-05, C-1001-12, C-1005-G06 |
| Wall 3 — checkout | place-order FAST_SELLING / RESERVATION_FAILURE lottery (Refract: ~1% of carts → order). **Orders come from EARLY place-orders:** 18 of 21 orders ever ≤~4.6 s after detection; HTTP 200 by window age ≤5 s 18/34, >120 s 0/150 (pooled, descriptive). 10-01: 0/199 won-cart tickets; our caps / gates / yield / self-heal ENDED all 7 non-converting carts while the TCIN read live | C-1001-02..08 |
| Outcomes | **10-01: 1 order, 2 units — the first hot-SKU order ever** (primary, 1011960739, in-chain first shot of a flip race, `8cba94c1`); hot carts → order 1/18 lifetime. **10-02 (30th Celebration): 0** — first edge lost to a host crash (0 shots), the rest 128/129 edge 429. **10-05 (03:00 ET 30th slot): 0, nothing went on sale** — the binder 1010892074 page appeared OUT OF STOCK at 02:00 CT and never flipped (bot + 5 alert accounts agree); 0 in-stock reads on 17 TCINs | C-1001-01, C-1002-F1/F2, C-1005-01/OP |
| Monitor 206 storms | **02:00-03:34 on 10-05 RedSky 206'd 73.3% of sweeps; the bodies held complete stock fields (94/94) and we discard every 206** (third storm at the 02:00 CT slot: 09-25, 09-30, 10-05). Longest blind stretch <90 s; 0/28 past real windows opened inside a ≥50%-loss interval → a latency lever, not a measured unit lever. FS-206-SHADOW armed 10-05 (log-only) to measure agreement before any ingest | C-1005-03..06 |
| Host | **The #1 controllable loss on 10-02.** 27/27 NVIDIA GPU-error episodes since 07-17 began at a display WAKE (screen off → on); since the 09-27 driver (617.14) 5/5 ended in bugcheck 0x116; on-screen buyer Chromes freeze in every episode. **10-04 ~22:00 (operator-approved): display timeout on AC 900 s → Never (`powercfg /change monitor-timeout-ac 0`; DC stays 180 s), screensaver OFF (`ScreenSaveActive` 1 → 0, applied live by SystemParametersInfo); AC sleep was already Never.** Restore: `powercfg /change monitor-timeout-ac 15` + `ScreenSaveActive=1`. A remote connection (Parsec, RDP) can still be a wake trigger (20/27 wakes carried code 6, NOT independently verified). Root-cause checklist from 07-13 (BIOS/microcode, XMP) still not done | 10-02 + 10-04 sections |
| Blocked / operator decisions | **Host (operator):** no keyboard / mouse / remote session (Parsec, RDP) on the host during a drop window; then BIOS update + XMP off (07-13 checklist). **FX-1001-A** (a held line at a fresh flip → in-chain pre_checkout → place-order) — **v11 VERIFIED 10-03, ARMED 10-04 on all 3 accounts** (`set TARGET_HELD_LINE_FLIP_STRIKE=1`, bat:1199, IDENTS unset = every account per `purchase_executor.py:209-225`; the code on disk is the verified v11 — mtime 10-03 00:27:26, before the v11 suite and verifier #11); kill `=0`; readout R-FX in the 10-04 section. F1 — code landed flag-gated OFF; **operator declined arming 10-04** (~0 units on the late-draw data); EXP-1002-NET (one buyer on a second, non-BD network) — operator; E5 not built; E4 declined. **10-05: FX-1005-BOOTSKIP, FX-1005-PO2XX, FX-1005-FOREIGN-KEEP (needs PO2XX), INS-ATC-NET — ARMED 23:39 (bat:1208-1220), committed `eb8ebaba`, final suite pending (operator)** — details + checks (business CVV = the code default; readiness false red on primary's missing login-session) in the GATE-HARDENING AUDIT section | 10-02 + 10-04 + 10-05 audit sections |
| Process | `/post-run` → saved Workflow `.claude/workflows/post-run.js` (rounds until dry; blind replicator + refuter + judge per claim; canaries); facts from `tools/events/` (SQLite); PreToolUse hook blocks the common direct bot / login / live-test launches. **The hook is a safety net, not a guarantee:** an independent replay of 10,094 past commands (09-30) found launch forms it misses and some offline-test loops it wrongly blocks. The rule "bot start = operator only" still binds every agent regardless of the hook. | CLAUDE.md, 09-30 review |

## PRE-DROP 2026-10-05 ~23:50 (for the next restock; the binder 1010892074 is rumoured for 10-06 overnight [REPORTED, unconfirmed]) — `/pre-drop`

- **State:** HEAD `7132c0b4` + the uncommitted gate-audit work; bot NOT running (no python, port 5001
  free; the 21 Chromes are the operator's personal profile). Regime: no run since the 10-05 post-run,
  no open watch. Config unchanged since 10-04 23:45: 17 enabled (≤30), all qty 2,
  `TARGET_QTY_PER_TCIN=1`; backup `config/product_config_backup_pre_2026-10-06_drop.json`.
- **Readiness 23:15 [MEASURED]:** primary ❌ `login-session MISSING` (member token minted 08:10,
  expired); business + alt-1 ✅ (member tokens expired 11:55/11:56, login-session 26.1 d LEFT →
  the bat's start pass refreshes them). 17/17 TCINs visible on the last run. **primary needs one
  forced hand login** (`hand_login_primary_force.bat`) — it is Worker 1 (a guest W1 exit-87s the
  bot) and whether checkout works without a login-session is NOT ESTABLISHED (C-1005-09/G12).
- **Proxy pool PASS [MEASURED]:** `validate_proxies.py`, production env (`apps_raw` etc.), pool=all,
  180 s: 20/20 exits HEALTHY, 537 sweeps, 537×200, 0×403/429/other, 20 sessions ready, 0 crashed
  (`logs/analysis_2026_10_05/gate_audit/validate_proxies_predrop.txt`; the shutdown
  ConnectionResetError is teardown noise).
- **Arming audit:** the wrapper sets 174 flags, every one read by the code, none twice. Built but
  unarmed before tonight: the four 10-05 flags + F1 (operator declined 10-04).
- **ARMED 23:39, committed `eb8ebaba` 10-05 ~23:58 (not pushed), bat:1208-1220:** `TARGET_BOOT_SKIP_FAILED_W1=1`,
  `TARGET_PO_2XX_AMBIGUOUS=1`, `TARGET_FOREIGN_KEEP_WON=1`, `TARGET_ATC_NET_META=1` (CRLF, ASCII,
  each set once; pre-edit copy in the session scratchpad). A third fresh verifier on the final code
  found 4 more gaps, all fixed before arming: BOOTSKIP on a 2-account fleet stuck at 1 racer
  (clear now checked before the size rule) and a transient boot failure recorded as an account
  failure (only the probe's own "not logged in" answer records now); FOREIGN-KEEP's re-read rule
  lost on a strike re-entry / dirty flag (carried now); INS-ATC-NET could await a zendriver
  Network.enable on the purchase path when the tab had no Network handler (Network is listed
  as enabled first now; proven against the real `Connection._register_handlers`).
  `tests/test_gate_hardening_1005.py` 112/112, 9/9 mutations caught (files restored by hash).
  Event store v3 (`PARSER_VERSION=3`): tables `atc_net`, `gate_events`; queries `atc_net.sql`,
  `gate_events.sql`; `tests/test_events_parser.py` 168/168; every pre-existing table identical
  row for row after the rebuild.
- **NOT DONE — denied by the auto-mode classifier ("[Production Deploy]"):** the final
  `tests/run_offline_suite.py` on the armed tree. The last full-suite results: 34/34 at 23:0x on the
  code before the third verifier's fixes (`offline_suite_final_tree.txt`), and 34/34 (375 s) in
  the event-store agent's run, which overlapped those fixes — neither is proven to cover the exact
  final tree (the event-store agent's run ran 23:34-23:40, across the last edits at 23:38-23:39:46). **The operator runs it.**

**PRE-REGISTERED READOUT for the next run** (written before it; `python tools/events/build.py`
then `python tools/events/q.py <query> --run <run_id>`):
- **R-BOOTSKIP** (`gate_events`, + `logs/bot_restart_wrapper.log`): PASS = every exit 87 is
  followed by a `boot_skip_recorded` / `_cleared` / `_none` row naming Worker 1, no relaunch after a
  `recorded` boots the same fleet, and with 3 accounts enabled no boot races fewer than 2. FAIL = a
  skipped account fires a shot, or the fleet drops below 2 with 3 enabled. No exit 87 → N/A.
- **R-PO2XX** (`gate_events` kind `po2xx_guard`): expected 0 rows. Any row → order-history check at
  once; PASS = no further place-order on that cart after the row.
- **R-FOREIGN** (`gate_events`): FAIL = any `foreign_bail` row whose chain had a 2xx add/strike and a
  2xx pre (it should now enter the loop), or any order holding a TCIN other than the one raced.
  Every `foreign_keep_po_only` row must be preceded by its cart read (the line itself says so).
- **R-NET** (`atc_net`): coverage PASS = `[ATC_NET]` POST rows joined to ≥95% of main-tab fast-lane
  shots with `atc_t0` (`checks.atc_net_post_join`). Descriptive only, no pass/fail: within mixed
  first volleys of flip-opened races, does wire order (send_ms) predict passing the limiter; pass
  by connection reused vs new; pass by edge IP (C-1005-G02/G04).
- Carried over: **R-HOST** (0 GPU / bugcheck / display-wake events), **R-FX** (FX-1001-A strikes),
  **R-COVER** (every window volleyed on every free account), **R-DECOY** (~20% keyless 401).

**BOOT CHECKLIST (operator, first ~4 minutes):**
0. Before starting: `venv\Scripts\python.exe tests\run_offline_suite.py` → `34 passed, 0 failed`;
   `hand_login_primary_force.bat` → readiness 3/3 ✅ MEMBER.
1. `=== drop-readiness check ===` → three ✅ MEMBER, and no `BOOT SKIP: ⚠` line.
2. `[WORKER_POOL] sized from target_accounts.json: 3 account(s)` and no `[BOOT_SKIP] skipping` line.
3. `✅ LOGGED IN TO TARGET.COM`; `[ATC_NET] Network meta instrument installed` appears on the
   first purchase only (not at boot).
4. `[MULTI_SESSION] started -- 18/18 sessions ready`; `[GROUND-TRUTH] pool cache-bust ok:
   in_stock=[] (17 TCINs)`; no `[TCIN-VISIBILITY]` banner naming a wanted TCIN (the binder may
   read PARTIAL until verified).
5. First `[STOCK STATS]`: 200s, `403=0`.
Readout after the run: `python tools/events/build.py` → `python tools/events/q.py gate_events --run <id>`
and `python tools/events/q.py atc_net --run <id>` (then `/post-run`).

## GATE-HARDENING AUDIT 2026-10-05 evening — every gate G0..G5, claims C-1005-G01..G15 (`docs/CLAIMS.md`)

Operator: "i want to go through every gate and make it as resilient and perfect as possible".
`/bot-investigate`: 7 analysts (G0 stock-pipeline, G1 + G2 antibot, G3/G4 + G5 purchase-flow,
failure-forensics ledger, retailer-researcher) → 8 claims-verifiers on the findings → 2 on the
code. Artifacts: `logs/analysis_2026_10_05/gate_audit/` (offline suite outputs).

- **Where units go [MEASURED, 09-11 → 10-02, 2,747 main shots]:** 2,481 edge 429 (90.3%) → 246
  past the limiter → 190 keyless 401 / 36 FAST_SELLING / 14 carts → 1 order. Of the 14 carts: 1
  order, 4 ended by Target, 9 ended by OUR code while the TCIN read live — all 9 late or after a
  failed first place-order (≈0 units on the late-draw data). Our own per-gate code is NOT the
  binding constraint; the limiter on the first volley is, and its key is not established.
- **G1 limiter, what the data can and cannot say:** the response is 9 fixed headers, retry-after
  0, no node/POP header (`fastly-restarts: 1` only past it) (G01); "same-IP volleys are
  all-or-nothing" NOT ESTABLISHED (G02); first-window-of-the-night effect home-only and P=0.07
  within TCIN×night — the TCIN×night itself predicts most (G03); whether our own volume spends it
  is UNVERIFIABLE both ways, and the 09-20 "shared volume bucket" table is all-SKU and confounded
  (G04); the edge-429 retry-delay knobs are inert (G05). No public source names the key (Refract
  live docs unchanged since 09-30). The one lever with a large measured effect stays a SECOND
  NETWORK LOCATION (C-0930-04; EXP-1002-NET, operator) — it must also pass Shape.
- **G2 Shape:** the "Shape admits early, refuses late" split is one TCIN-night (1010892076, 10-01)
  — outside it ~25% of early and ~41% of later past-limiter shots were keyless 401 (G06). Bank age
  does not predict it; each harvested set is replayed exactly once.
- **G0 [MEASURED, analyst]:** 71/82 windows volleyed; callback → first POST p50 44 ms; the RedSky
  read itself p50 554 ms (G14). The structural gap is the fleet lock (`[MULTI_SKU_MISS]`): 0 since
  multi-SKU dispatch was armed 09-21, real in principle with 17 TCINs.
- **G3/G4/G5:** whole-cart clear on a foreign line (G07, defect real, never cost a line);
  `held_cart_other_tcin` sit-out (G08, 2 first-shot slots on 10-01, NOT fixed — FX-1001-C needs its
  own design); odd add-to-cart statuses → a dead 1.5 s DOM wait (G10, ≈0 units, not fixed); a
  place-order 2xx ≠ 200/201 → second POST (G11, never seen); in-chain 201 → place-order 1.3-3.2 s
  (G15); business's configured CVV equals the hardcoded default (`purchase_executor.py:24`).
- **Run level:** the exit-87 boot loop (G09: 15 exits / 5 wrapper starts, each ended by the
  operator); the "27.1 d login-session / 11-01 expiry" belief REFUTED — 27.1 d was time LEFT and the
  date is set by our `_fix_session_cookies` (G12); no timed restart by design (G13).

**BUILT 10-05, flag-gated, default off = byte-identical, NOT ARMED** (`tests/test_gate_hardening_1005.py`
97/97, 5/5 mutations caught, files restored by hash; offline suite 34/34 —
`logs/analysis_2026_10_05/gate_audit/offline_suite_final.txt`):
- **FX-1005-BOOTSKIP** `TARGET_BOOT_SKIP_FAILED_W1=1` (+ `TARGET_BOOT_SKIP_TTL_H`, default 12, 1-48):
  before exit 87 app.py records Worker 1 in `state/boot_skip_accounts.json`
  (`worker_pool.boot_skip_note_probe_failure`); the relaunch builds the fleet without it like
  `"enabled": false` but every worker KEEPS its slot number (labels `W{n}/{acct}` key the persisted
  AC-1 latch). A second failure while a skip is in force CLEARS the list (not an account problem →
  full fleet, the pre-flag loop), so it never cascades below 2; it never skips the last account.
  A real login via `relogin_one.py` (`--manual` hand login / `--force` / a scripted login that
  passed) clears the entry; the validate-first "already logged in" pass does not.
  `check_session_readiness.py` prints a `BOOT SKIP: ⚠` line per skipped account.
- **FX-1005-PO2XX** `TARGET_PO_2XX_AMBIGUOUS=1`: a place-order 2xx other than 200/201 is
  unresolved on all three API paths (fast lane, won-cart ticket, legacy + DOM fallback) →
  terminal, `_po_ambiguous`, AC-1 tag. Not covered: a place-order fired by a DOM click (none since
  08-04), and `TARGET_DOM_FALLBACK_ON_NO_RESPONSE=1` (unset) re-opens the DOM click.
- **FX-1005-FOREIGN-KEEP** `TARGET_FOREIGN_KEEP_WON=1` — **inert unless PO2XX is also =1** (in code):
  a won chain stopped at `foreign_cart_item` enters the won-cart loop (selective delete
  `keep_tcin=T`, strict pre_po gate) instead of the bail's whole-cart clear; every po_only ticket
  of such a cart re-reads the cart first (`L['foreign_entry']`). A failed / empty delete ends
  `foreign_stuck` with the cart held and no order. Residual (pre-existing loop property): a line
  landing between that read and the place-order is bought.
- **INS-ATC-NET** `TARGET_ATC_NET_META=1` (log-only): one `[ATC_NET]` line per main-tab
  cart_items request (POST + OPTIONS) — wire send ms, connection id / reused, edge IP:port,
  protocol, TTFB. Awaits nothing before the add-to-cart. Readout: arrival order vs pass within
  mixed volleys; pass by connection reuse / edge IP — the instrument for G02/G04.
- **Withdrawn:** FX-1005-EDGE-REARM — the production `on_in_stock` (`stock_monitor._adapter`)
  swallows every exception, so the branch was unreachable; the code was removed.

**ARMED 23:39, committed `eb8ebaba`** at bat:1208-1220 — see the PRE-DROP 2026-10-05 section
above for the third verifier's fixes, the readout rules and the boot checklist. Kill: `=0` each.

**Operator decisions / checks:** (1) confirm business's card CVV is its real code (it equals the
code's default); (2) readiness will likely show primary ❌ "login-session MISSING" although primary
ran a member night without it (C-1005-09/G12) — the "hand login only on ❌" rule would fire on a
false red; (3) EXP-1002-NET — one buyer on a second, non-BD network — is the only limiter lever
with a measured effect; (4) FX-1001-C (don't sit an account out of a different TCIN's flip) needs
its own design pass.

**Stale comments left in the wrapper (production file; not edited):** bat:299-311 (edge-429 delay
knobs "~50% more tickets" — inert, G05); bat:443-452 ("FAST_SELLING never answers a wave's first
checkout POST" — 4/8 in Sep-Oct did, G15); bat:747-748 and :847-856 (own-volume bucket — NOT
ESTABLISHED, G04); bat:1349-1350 ("the ATC 401 is the WRITE-AUTH layer, not Shape" — 190/190 main
401s since 09-11 are keyless, the Shape verdict); bat:1469-1470 ("TARGET_TOKEN_KEEPFRESH keep
healing 24/7" — KEEPFRESH=0 since 09-28).

## POST-RUN 2026-10-05 — the 03:00 ET 30th slot: 0 bought because NOTHING WENT ON SALE; a RedSky 206 storm blinded 73% of reads in the slot (claims C-1005-*, `docs/CLAIMS.md`)

Operator: "i believe only tcin 1010892074 restocked. but still we were unsuccessful … investigate
all issues and fix so that we can be ready for the next restock." Results:
`logs/analysis_2026_10_05/postrun/` (`workflow_result.json`, `sections/`, wf_9651193b-efc, 1 round,
28 agents, 0 errors, complete, 0 open gaps; 8 claims: 6 CONFIRMED, 2 PARTIALLY CONFIRMED, 0 REFUTED).

- **The run** `run_20261005_002518` (00:25:18 → 09:50:43, one launch, 3 accounts, 17 TCINs): **0 in-stock
  reads on any TCIN** → 0 flips, races, shots, carts, orders (C-1005-01). Units lost: **0** — there was
  nothing to buy.
- 🔑 **The binder never went on sale** (C-1005-OP, REFUTES the operator's belief as worded; REPORTED,
  5+ alert accounts). Its page appeared at 02:00 CT out of stock ("no stock went up", "Not Live Yet",
  later "did NOT drop", "didn't flip"); those accounts posted "now live" for every 10-02 flip and none on
  10-05. Two automated bots labelled the page's appearance "RESTOCK" at 02:00:06-09 CT — the likely source
  of the belief. The bot read exactly that: NOW VISIBLE 02:00:17, `ship=OUT_OF_STOCK` at 02:00:08. The PDP
  on 10-05 ~12:00 CT: $39.99, Out of Stock, street date Dec 4. Several accounts guessed the drop moved to
  10-06 overnight [REPORTED, unconfirmed].
- **Target-side: a RedSky 206 storm at the slot** (`docs/TARGET_CHANGES.md` 10-05): 02:00:18-03:34:28,
  12,396/16,904 sweeps lost (73.3%), store_positions errors on every product; 72.56.171.184 spared again
  (14.2% vs ~76%). **Ours: we discard the 206 bodies** although 94/94 sampled held complete stock fields
  (C-1005-03/04). Cost tonight 0; inside a storm a 2-5 s window has a modelled 9-28% miss chance.
- **Host: PASS** (C-1005-08) — 0 GPU / bugcheck / display-wake events, 0 wedges. The 10-04 display change
  held through the slot.
- **primary without a login-session** all run, still re-minted in-bot (C-1005-09) — see the Accounts row.
- **Pre-registered readouts (10-04):** R-FX INCONCLUSIVE (0 shots); R-COVER vacuous (0 flips); R-HOST PASS;
  R-DECOY 20.0% (443/2,215) → the 10-02 09:24 dip was a fluke, watch closed; R-VIS fails only for the
  unpublished binder (by design).
- **Other:** 08:25-09:30 the four 168.158/16 exits lost 29% (cause not logged; BD subnet, not Target);
  `[DISPATCHER] s10 flagged crashed` ×2 (new string, raw timeout). A 206 also bumps
  `consecutive_errors` (`tab_dispatcher.py:435`), so a long 206 streak + one timeout can flag a session
  crashed (did not bite tonight).

**ARMED 2026-10-05 (both LOG-ONLY, flag-gated, default off = unchanged; offline suite
`logs/analysis_2026_10_05/postrun/offline_suite_after_status_shadow.txt`):**
- **INS-STATUS-LOG `RESILIENT_STATUS_LOG=1`** (bat:128): `stock_monitor.redsky_status_fields` adds
  `sd_rtc / sd_services / sd_loyalty / sd_oos_reason` under the flag (the DX-1 pattern);
  `_log_status_changes` writes `[STOCK STATUS] tcin= #n old= new=AVAIL|RTC|svcN|LOYALTY in_stock= …` per
  change (first sighting included, ≤200/TCIN) AFTER every on_in_stock callback, and
  `[STOCK] SELLABLE-PARSED-OOS tcin= reason=` (no_services / atp_zero / not_target_direct / loyalty_only /
  other; new reason or ≥300 s, ≤50/TCIN). Event store tables `stock_status`, `sellable_oos`; query
  `status_changes.sql`.
- **FS-206-SHADOW `RESILIENT_206_INGEST=shadow`** (bat:129): `_fire_raw_on` sets `BulkResult.partial_raw`
  for a 206 (only value 'shadow' is on); `_dispatch_one` → `_shadow_206` keeps complete summaries with no
  fulfillment / item error (`shadow_filter_206`; an unattributable error disqualifies the body), parses
  them, and counts agreement with the TCIN's 200-sourced read ≤2 s old. One `[STOCK][206-SHADOW]` line a
  minute. Never calls on_in_stock, never writes `_tcin_status`. Table `shadow206`; query `shadow206.sql`.
- Tests: `tests/test_status_log_and_206_shadow.py` 57/57 (10 mutations caught, files restored by hash);
  `tests/test_events_parser.py` 140/140 (+11, 3 parser mutations caught; `q.py` RUN_TABLES drift guard).
- **Readout rules (pre-registered 10-05; run `python tools/events/build.py` first):**
  - **R-STATUS** (`q.py status_changes --run <run>`): COVERAGE PASS = every enabled TCIN visible to RedSky
    has ≥1 row; any `parsed_oos > 0` = a parser classification gap → investigate before the next drop; an
    operator "X restocked" report is CONFIRMED by `sellable_reads > 0` for X, REFUTED for the bot's window
    by `sellable_reads = 0`.
  - **R-206SHADOW** (`q.py shadow206 --run <run>`): ELIGIBLE to build FS-206-INGEST = paired ≥1,000 with
    disagree = 0; INCONCLUSIVE = paired <1,000 or no in-stock read on either side (OOS agreement alone does
    not validate positives); FAILED (never ingest) = any disagree > 0. Kill-switch: any
    `[STOCK][206-SHADOW] not counted` traceback line, or sweeps/s >5% below the previous run's same hours.
- **Deferred (eligible, not built):** FS-206-INGEST (needs SHADOW's agreement; value = latency inside a
  storm only, per the critic); FS-STATS-SPLIT / INS-GT-RATE (change the STATS line format → parser first;
  FS-STATS-SPLIT should also reset/split the 206 `consecutive_errors` bump).
- **Open (research):** why 72.56.171.184 is spared in storms (n=2); what triggers the 02:00 storm; why
  primary's live Chrome lost `login-session` between 00:06:10 and 00:10:17 (the wrapper's relaunch drops a
  session-only cookie?) and whether checkout works without it; the 168.158/16 08:25 cluster.

## PRE-DROP 2026-10-04 → 30th Celebration slot 2026-10-05 03:00 ET = 02:00 CT (`/pre-drop`)

Operator: "make sure all the 30th are enabled … 3am eastern tonight for 30th binders in targets
back end data … 100k 30th etbs and 24k 30th posters. Lesser items are 6k 30th tins and like 4k
booster bundles … besides the infinity stock pitch black … dont remove anything … make it as
ready as possible." Notes: `logs/analysis_2026_10_04/`.

- **State at 21:42** [MEASURED]: HEAD `3678ae6d`; no python process, port 5001 free; 13 chrome
  processes = the operator's own browser. Runs since the 10-02 post-run: one, `run_20261002_092404`
  (09:24-09:53, daytime, 0 shots, 0 flips, boot 18/18 ready, first STATS 403=0 429=0). It was
  missing from the event store; rebuilt 10-04.
- **Readiness 21:43** [MEASURED]: primary MEMBER until 00:48:14 (jar saved ~20:48, i.e. a fresh login);
  business and alt-1 member tokens EXPIRED 10-02 13:23:39 / 13:23:54; login-session 27.1 d LEFT
  on all three (a 30-day expiry our own `_fix_session_cookies` wrote, not Target's; C-1005-G12). Those two tokens were minted at 09:23:39 / 09:23:54 on 10-02, i.e. by the
  wrapper-start `relogin_one.py` pass re-minting EXPIRED tokens right before the 09:24 boot — the
  third such case (n=2 on 09-28). **And at 20:48:12 on 10-04 the operator's bat start (stopped
  after ~10 s) refreshed primary the same way:** `logs/relogin.log:3261` "primary: already logged
  in ✅ (cookies refreshed …)", token iat 20:48:14. So **no hand login is needed tonight**: the
  bat's validate-first pass (`relogin_one.py:402-411`) refreshes an expired member token from a
  live login-session, and the bat re-runs the readiness check after it (bat:1459-1463), so a pass
  that failed still shows ❌ at boot. A hand login is needed only for a dead login-session or a
  guest jar. The script's stale "nothing in the bot re-mints it; force a hand login" verdict is
  fixed (10-04, `READINESS_REMINT_AWARE`, default 1; =0 is byte-identical to `c1d24d8a`, checked).
  - **Why the typed login cannot simply be scripted:** the dead-session fallback
    (`relogin_one.py:413-431`) already types the config email and password and ticks KMSI, 3
    attempts. Shape blocks it at the username step (~0/25 historic, 2/5 on 09-25, 0/6 on 09-28
    "username did NOT advance"), and each failure leaves the account signed out. (The
    "login-session 27.1 d → a dead session ~10-31/11-01" inference that stood here is REFUTED,
    C-1005-G12: 27.1 d was time LEFT on a 30-day expiry our own `_fix_session_cookies` writes onto
    Target's session cookie; primary ran a member night on 10-05 with no login-session at all.)
- **Regime:** nothing new on a drop night since 10-02 (no run). One WATCH:
  `run_20261002_092404` decoy keyless 401 5/109 (4.6%) vs 18-20% in the four earlier runs; signed
  decoys 0/55 → `docs/TARGET_CHANGES.md` (2026-10-02 09:24 entry), rule R-DECOY below.
- **Config** [MEASURED]: 17 enabled ≤ 30, one RedSky chunk, no duplicate TCINs. Not added: the
  BlueProton marketplace listings titled "Pokemon 30th Celebration …" (1013562555 ETB 2-pack
  $439, 1013562569, 1013410935/40/68/60/81, 1013649945, 1014106191 — third-party resellers at
  2-6x the price; buying one is a real-money mistake).
- **Contention cost of the extra SKUs (C-0924-01):** a TCIN that flips while all 3 accounts race
  other TCINs is not re-raced while it stays in stock. [MEASURED] 0 `[MULTI_SKU_MISS]` /
  concurrency skips since multi-SKU dispatch was armed 09-21 (09-16 had 13; restock nights 09-23,
  09-25, 09-30, 10-01, 10-02). Real in principle, unobserved; watched by R-COVER.
- **Proxy pool:** not re-validated with `validate_proxies.py`. The last production-path boot
  (10-02 09:27) was 18/18 ready, first STATS 403=0 429=0, loss 0.112% over 4,483 sweeps; the
  boot checklist line `18/18 sessions ready` re-proves it on the same path ~4 min after launch.
- **Gate:** offline suite on this tree → `logs/analysis_2026_10_04/offline_suite_predrop_1005.txt`.

**Pre-registered readout (fixed 10-04 ~22:10, before the run).** `<run>` = tonight's
`run_2026100[45]_*`; `python tools/events/build.py` first.
- **R-FX (FX-1001-A, all 3 accounts):** WORKED = ≥1 `[HELD_LINE_STRIKE] order placed` or an order
  with `first_201_src=strike_400`. FAILED = any `[LEGACY_CART_GUARD]` line reaching a checkout with
  a non-raced TCIN in the cart, a strike place-order on a cart the guard would refuse, or an
  order containing an item we did not race / qty > 2 (order-history glance) → set `=0`.
  INCONCLUSIVE = no add-to-cart 400 MAX_PURCHASE on a flip (the expected case; 3 ever).
- **R-COVER:** `q.py coverage --run <run>` (new saved query, smoked on 10-01 / 10-02). PASS =
  `unraced` 0 on every target-list TCIN that flipped. An unraced 1010892076 / 1010892067 window
  with a `[MULTI_SKU_MISS]` naming it = the cost of tonight's extra SKUs → revisit.
- **R-HOST:** PASS = no `cdp_wedged_pre_atc` on ≥2 accounts within 60 s and no nvlddmkm 14 /
  bugcheck 0x116 in the System log during the run. FAIL with the display never sleeping = the
  wake is not the only trigger (C-1002-HOST-TRIGGER) → BIOS / XMP / driver next.
- **R-DECOY:** `q.py regime --run <run>` `d401_pct`. ≥15% over n ≥ 500 → the 10-02 09:24 dip
  was a fluke (close the watch). <10% over n ≥ 500 → a Shape/SSX change; open an investigation.
- **R-VIS:** boot `[GROUND-TRUTH] … (17 TCINs)`; no `[TCIN-VISIBILITY]` banner naming an enabled
  TCIN as absent after the first scan (1010892070 and the newly enabled three read PARTIAL until
  the bot verifies them).

## POST-RUN 2026-10-02 — 30th Celebration restock, 0 bought: the first edge lost to a HOST CRASH, the rest to the edge limiter (claims C-1002-*, `docs/CLAIMS.md`)

Operator: "back to being unsuccessful … investigate all issues fix and or come up with a
solution to get these 30th anniversary products … leverage online resources." Results:
`logs/analysis_2026_10_02/postrun/` (`workflow_result.json`, wf_3e516b75-b07, 3 rounds, 56
agents, `complete=False` on one host gap, closed below by the main session;
`recovered/research_30th_celebration_target.md`). A second host crash (09:21) killed the
first analysis session; the workflow was resumed from its journal.

- **Runs:** `run_20261002_011217` (boot 01:12, ended by the host crash 02:13) and
  `run_20261002_021547` (02:15 → 08:04 clean). Edges:
  - 1011407490 Booster Bundle at 02:11:58 (old run). restockd.app puts the 10-02 Target window
    start at 3:12 AM ET = 02:12 CT.
  - 1011407490 again at 02:19:37. This was a SECOND edge (OOS read 02:19:28).
  - 1012422107 Mini Tin at 02:45:55.
  - 1012644667 at 03:07:59 (one read).
- 🔴 **The first edge got 0 shots (C-1002-F2 CONFIRMED; AB-1002-WEDGE PARTIAL).**
  - All 3 buyer Chromes stopped answering CDP from 02:10:39: `cdp_wedged_pre_atc` ×3 at
    chrome_age 3596 s.
  - Host sequence: nvlddmkm 14 at 02:10:35 → TDR → bugcheck 0x116 → unexpected shutdown 02:13:29.
  - The off-screen monitor Chromes kept sweeping (17/18 exits, +267 OK) and read the flip. The
    on-screen buyers froze, as they did in 9/9 GPU episodes that overlapped a run
    (C-1002-TDR-SPLIT, C-1002-ERA-OFFSCREEN PARTIAL: window position is confounded with the
    driver and the era).
- **The rest of the night (AB-1002-W1 / C-1002-F1 CONFIRMED):**
  - 129 main add-to-carts = 128 edge 429 + 1 FAST_SELLING (primary, 1012422107, first shot).
    0 × 401, 0 × 201.
  - Our dispatch, gates and credentials cost 0 shots; read → fire was 31 / 62 / 41 ms.
  - In-window decoys got 0 × 429 (77 × 424 + 17 × 401), so the limiter was scoped to the hot
    TCINs, not to our line (AB-1002-TCIN-SCOPE CONFIRMED).
- **Regime: not a Target change on this evidence (C-1002-F3 PARTIAL).**
  - Tonight sits inside the pre-10-01 range: flip-race first shot 1/9 vs 15/65, p=0.67. 10-01
    is the departure.
  - The comparison is confounded by SKU mix, and the change point cannot be located: no
    add-to-cart was fired from 10-01 04:53 to 10-02 02:19.
  - The 3 TCINs have carted 0/895 shots ever, and 0/169 home later shots got past the limiter.
    "Uniquely different" is NOT ESTABLISHED (AB-1002-SKU PARTIAL).
- **Boot SELFTEST** (C-1002-F4 CONFIRMED): the control POST got 429 on 3/3 accounts at
  02:16:31-37, plus 5 decoy 429s. Never seen before in 55 account-boots. Cause NOT ESTABLISHED,
  cost 0. The SELFTEST counts a 429 as "accepted" (`purchase_executor.py` SELFTEST verdict,
  the 2C default-bucket pattern). That was harmless here.

### HOST — the crash trigger (C-1002-HOST-TRIGGER PARTIAL; gap2.1; main-session closure 10-02 ~15:00)
- **The trigger** [MEASURED]: 27/27 nvlddmkm-14 GPU-error episodes since 07-17 began at a display WAKE.
  - Each is a Kernel-Power 566 transition from session type 1 (screen off) to type 0 (screen on),
    26 of them within 0.38-0.49 s of the episode.
  - The type meanings come from 130 `0→1 reason=12` idle-timeout transitions.
  - Wake reason codes: 20/27 carried code 6 (the r2 analyst decoded it as a remote connection;
    a Parsec virtual display adapter is installed; mapping NOT independently verified), 3 code 3,
    2 code 31, 2 code 32.
  - 27 of ~189 wakes produced an episode.
- **Escalation since the driver change** [MEASURED]: since NVIDIA 617.14 (09-27 20:54), 5/5 episodes
  ended in bugcheck 0x116 with a dump (09-28 16:07, 09-29 09:06, 10-01 16:56, 10-02 02:10,
  10-02 09:18).
  - Before 617.14: 0/22 bugchecked, 4/22 ended in a dumpless stop within 5 min, and 5 recovered
    unaided in 2.25-3.9 min.
  - The on-screen buyers froze either way.
  - The previous driver, 610.74, is gone from the driver store, so a rollback means a download.
- **Host settings now** [MEASURED]: display off after 900 s on AC (DC 180 s), screensaver ON at 900 s, no AC
  sleep. So every unattended run reaches screen-off 15 min in, and the next wake is the trigger.
- **The 07-13 root-cause triage is still OPEN** (FAILURES.md):
  - degraded 13900K on microcode 0x11F (BIOS 1.90, 2023);
  - 4-DIMM XMP 5600.

### Ranked actions (by units)
1. **Host — operator, before the next drop:**
   - Set the display timeout to Never on AC and turn the screensaver off.
   - No keyboard, mouse or remote session (Parsec / RDP) on the host during a drop window;
     watch from another device. The dashboard binds 127.0.0.1 only (`app.py:4471`).
   - That is EXP-1002-NOWAKE (eligible). Then the 07-13 checklist (EXP-1002-HOSTFIX): BIOS update
     (microcode 0x12B/0x12F + Intel Default limits), then XMP off for ≥1 week.
   - A driver rollback to 610.74 (EXP-1002-DRIVER) is secondary. The old driver did not bugcheck,
     but 4/22 of its episodes still ended in a dumpless stop, and the buyers froze in every one.
2. **FX-1001-A, for nights that win carts:** see the status below.
3. **EXP-1002-NET** (one buyer on a second, non-BD network): operator decision.
   - This is an untested network class. BD exits were Shape-denied.
   - It conflicts with the 09-16 no-residential-IP preference.
4. **More accounts.** Refract's live docs (changed since 09-21) [REPORTED]: "Target has become a
   submit order lottery … only about 1% of carts turn into an order … 10 tasks does not cut it
   anymore".

**Not done:**
- INS-1002-HOST: an offline host-event tripwire in the post-run facts stage.
- INS-1002-WARM429: log warm-up 429 headers.
- EXP-1002-OFFSCREEN-BUYER: not eligible to arm.

### FX-1001-A status (10-03 — v11 VERIFIED; ARMED 10-04 on all 3 accounts)
- **History:** v3 → v11 over 10-02/03, each version checked by a FRESH claims-verifier.
  - #3-#7 refuted something each time: untracked-line exits (many in HEAD-inherited code), cancellation windows, a guard that trusted the add response's `cart_items` (it lists only the added line), and a guard that trusted `_delete_cart_items`' ok.
  - #8 and #9 confirmed the sink guard (claim 3). #10 confirmed claims 1, 2, 3, 4 and 5. **#11 confirmed claims 1 and 3b.** Its sweep was 2,644 runs with cancellation injected at every await: 0 untracked exits in scope.
- **What it does, with the flag on:**
  - An add-to-cart 400 MAX_PURCHASE (our line is held) continues IN THE SAME CHAIN to pre_checkout → place-order. That happens only when the cart holds nothing but our TCIN, with every quantity finite and the sum ≤ Q. Otherwise the line is kept and flagged.
  - A held or left-behind line in a NEW stock window is struck through the won-cart ticket loop.
  - **Sink guard:** the legacy whole-cart checkout runs only when a qualifying cart READ shows no line of another item. Other lines are deleted by exact id, then a qualifying re-read must come back clean. Otherwise the checkout is skipped.
  - The strike requires the qty guard and `TARGET_HELD_CART_REENTRY`. REENTRY=1 is in the bat (bat:1173). `TARGET_FASTLANE_QTY_GUARD` itself is NOT in the bat; the guard is forced on by REENTRY=1 (`fastlane_qty_guard_on`, `purchase_executor.py:403-410`).
- **Gates:** `tests/test_held_line_strike.py` 194/194; every v4-v11 fix is mutation-checked (`scratchpad/mutate_v*.py`); offline suite 32/32 (`logs/analysis_2026_10_02/postrun/offline_suite_after_fx1001a_v11.txt`). Flag off: JS renders byte-identical to HEAD and the differential runs show 0 diffs.
- **Known costs and residuals (verified, accepted):**
  - A strike evaluate that times out at the pre/cvv stage is terminal and trips the 1800 s AC-1 latch for that TCIN. That costs the window; it is safe.
  - When the guard cannot read the cart (e.g. a 429), it skips the legacy checkout. The legacy-201 path was 0/7 of the 10-01 legacy checkouts.
  - HEAD's own release paths can still drop a line on a 0-line "success".
  - A line can land after the guard's last read.
  - Live trigger frequency is small: 3 fast-lane MAX_PURCHASE 400s ever, all on 10-01.
- **ARMED 2026-10-04 by the operator, all 3 accounts:** `set TARGET_HELD_LINE_FLIP_STRIKE=1` at bat:1184 at the time, bat:1199 since the 10-05 insert (CRLF verified, assigned once, no IDENTS scope).
- **Readout rule** (pre-registered, grep on the run log):
  - WORKED = ≥1 `[HELD_LINE_STRIKE] order placed` or an order with `first_201_src=strike_400`.
  - FAILED = any `[LEGACY_CART_GUARD]` line reaching a checkout with a non-raced TCIN in the cart, or a strike place-order on a cart the guard would refuse. Kill-switch: `=0`.
  - INCONCLUSIVE = no MAX_PURCHASE 400 on a flip (expect several restock nights).
- **Stale comments, not yet edited:** `session_manager.py:2337-2339` and `run_bot_with_nightly_restart.bat:1263-1264` say the home-IP Chrome "never wedges". That holds only for the age-driven wedge; a host GPU episode freezes the on-screen buyers.

## POST-RUN 2026-10-01 — overnight restock, 8 carts, 1 ORDER (the first hot-SKU order ever) (claims C-1001-01..14, `docs/CLAIMS.md`)

Operator: "for the first time in a long time we have got a hot sku. But for the majority
of the night we failed. Please do a full investigation and fix all issues to be ready for
the next restock." Result + verdicts: `logs/analysis_2026_10_01/postrun/`
(`workflow_result.json`, `verdicts_full.md`, `regime_flags.md`).

- **The run** (`run_20260930_233818.log`, 09-30 23:38:17 → 10-01 16:59, ended by a host bugcheck 0x116, per the 10-02 post-run): 6 stock windows on 5 TCINs,
  67 races, 350 main-tab add-to-carts = 230 edge 429 + 80 keyless 401 + 13 FAST_SELLING +
  **8 × 201** + 13 status 0 (alt-1 preflight 429s) + 3 × 400 MAX_PURCHASE + 2 keyless 429 +
  1 × 424 [MEASURED, reconciled to 350]. F1 was NOT armed (C-1001-02).
- 🟢 **THE ORDER (C-1001-01, VERIFIED):** primary, 1011960739, qty 2, order `8cba94c1` — the
  first shot of the 03:48:35 flip race, ATC +98 ms after the first in-stock read, in-chain
  201 → pre 201 → place-order 200 in 3.00 s; the first place-order on that TCIN in its
  window. Exactly the archetype: an EARLY, in-chain first place-order.
- 🔴 **THE OTHER 7 CARTS (14 units) WERE ALL ENDED BY OUR CODE WHILE THE TCIN READ LIVE
  (C-1001-02..06, VERIFIED / narrowed).** Target refused every place-order on them (0/199
  tickets + 5 in-chain, FAST_SELLING / RESERVATION_FAILURE), but what ENDED each cart was ours:
  - 4 won-cart loops capped at live=True with ~164 s of deadline left (`cart_ticket_cap` 40 ×2 on
    1010892076, `call_cap` ×2 on 1011960739) — F1 not armed.
  - **The stranded-line cascade:** the held-cart TTL retire (900 s) got 429 on DELETE for the
    alt-1 / business 1011960739 lines at 04:04-04:05 and dropped the marker anyway
    (then `purchase_executor.py:9267-9268`; today `_held_cart_release` ~:9985-9994, where the armed
    FX-1001-A re-flags a failed release dirty instead); those lines then made our `foreign_cart_item` gate end
    both 1010892067 carts (04:12, 04:13) after a pre_checkout 201; at 04:53:25 the ATC-400
    MAX_PURCHASE self-heal wiped both carts (4 lines, no TCIN filter). The code comment at
    `purchase_executor.py:8128-8130` ("a foreign line comes from a prior failed attempt") does
    not cover this case.
  - `yield_fleet` gave up primary's verified, live 1010892067 cart for 1011960739 — a TCIN the
    dispatcher was never going to race again.
  - Our gates suppressed 4 of 18 flip-race first shots (race 16: `held_cart_release_failed` with
    Target's cart-GET 429; race 53: `held_cart_other_tcin`).
- 🔴 **THE RE-ARM GAP (C-1001-05):** 1011960739 read in stock 62 min after the order (129/129
  reads) and nothing was fired at it after 03:51:30. 'purchased' only until 04:00:51, when
  `reset_completed_purchases_by_stock_status` (`bulletproof_purchase_manager.py:1666-1737`)
  silently reset it to a bare 'ready'; the level re-arm (`app.py:1153-1165`) skips 'ready'
  without a `rearm_hint_ts`, and dispatch fires only on OOS→IS edges.
- 🔑 **WHAT CONVERTS (C-1001-07/08):** 18 of the 21 orders ever came ≤~4.6 s after the bot
  detected the TCIN in stock; HTTP 200 by window age ≤5 s 18/34, 6-30 s 2/52, 31-120 s 1/95,
  >120 s 0/150 (pooled, era-confounded, conditioned on earlier failure — descriptive, not
  causal); no order ever from a cart won >30 s into its window (0/19). **So the levers that
  only add LATE draws — F1, re-racing a 'purchased' TCIN, keeping held markers — are worth
  ~0 units on the data; the one lever that creates an EARLY place-order is FX-1001-A.**
- **Regime (C-1001-11..14):** the limiter RECOVERED on 1010892076 (past it 58/230 vs 1/195 the
  night before, first carts ever, no armed change — cause NOT ESTABLISHED); flip-race first
  shots 13/14; hot cart rate 8/342. But a **per-TCIN admit-then-401 switch** at the SSX hop:
  on 1010892076 the first 6 past-limiter shots were admitted, then 70/70 keyless 401 (all 3
  accounts) while decoy 401s held at ~20%. New error: OPTIONS-preflight 429 on cart_items, 7
  min on alt-1 (C-1001-13). Monitor clean in the restock hours (0.18% vs 1.20%; the 206 burst
  did not recur). All three logged in `docs/TARGET_CHANGES.md`.

**DECISION (main session, ranked by expected units at the next restock; every spec rests on
CONFIRMED / PARTIALLY CONFIRMED claims, critic notes applied):**

| # | Spec | Units | State |
|---|---|---|---|
| 1 | **FX-1001-A** (`TARGET_HELD_LINE_FLIP_STRIKE`, folds in FS-A `TARGET_ATC400_TO_HELD`): when an account already holds a line on the TCIN being raced at a fresh flip (ATC 400 MAX_PURCHASE, a dirty flag or a held marker naming it), run in-chain pre_checkout → foreign/qty gate → place-order on the existing line instead of skip / delete / self-heal wipe; never on an account × TCIN that already ordered this run | the only lever creating an EARLY place-order; would have applied 4× on 10-01 (race 16 ×2, 04:53:25 ×2); yield NOT ESTABLISHED (n=0); unclear whether its trigger should fire on a hysteresis-merged re-flip (`new_window=0`, race 67) | **BLOCKED 10-01 ~23:30: the operator approved the build; the auto-mode classifier DENIED a `purchase_executor.py` edit mid-build ("[Real-World Transactions]") → tree restored byte-identical to HEAD (blob 74e6b044), nothing landed.** Operator decision: build it with auto mode off (as F1 on 09-30), or by hand. Build notes from the attempt: (a) realistic 10-01 applicability is **2, not 4** — race 16 primary + business (single-TCIN carts); the 04:53:25 pair held 1011960739 + 1010892067, so pre_checkout's foreign-item guard would still have stopped the place-order, and stock was already gone (primary 424); (b) use the add-to-cart as the probe (a gone line just gets a normal 201) and gate the held/dirty unblock on a NEW window: stock snapshot `window_start` > our last action on the line; (c) a strike whose place-order returns 200 still carries atc=400, so it MUST return the success before the legacy ATC branches, or the old MAX_PURCHASE self-heal would clear the cart and add again after a committed order; (d) `woncart_eligible` must accept a strike so a FAST_SELLING place-order goes to the ticket loop; (e) flag off must leave the fast-lane JS byte-identical (golden test) |
| 2 | FX-1001-C / FS-B carve-out: a failed delete keeps the marker for bookkeeping WITHOUT blocking other TCINs' first shots, or our own held target-list line is not "foreign" | as specced: 0 units and −4 flip first shots (gap1.1 replay); with the carve-out: frees 2 carts / 2-4 first shots per such night, late-class carts | needs redesign before eligible |
| 3 | F1 arming (`TARGET_WONCART_LIVE_CAP_EXEMPT=1`) | ~0 on the late-draw data; floor = stop deleting a live cart (the delete → 429 → dirty-gate path) | code landed 3678ae6d; **arming = operator** (production-deploy gate) |
| 4 | FX-1001-B / FS-C (re-race / don't yield to a 'purchased' TCIN for marker holders) | ~0 (late draws) | low priority |
| 5 | FS-D (re-submit held lines on a 'purchased' TCIN) | ~0; deliberately buys a SECOND account's units of a TCIN already ordered | **operator's money call** |
| — | Instruments, 0 units: INS-1 / I-PO-2 (event store: retry-path 201s + `place_orders` table), FS-2 (ATC 400 body codes), FS-4 (`ssx_sequence.sql`) — offline, safe; FS-1 / FS-E / I-PO-1 (log-only prints in the bot), FS-3 (egress fingerprint) | — | specced, not landed |

- **Open research (not decision-changing):** whether the SSX switch is new or volume-driven;
  whether a cart DELETE/read 429 is tied to the held TCIN being live (0/2 deletes OK while
  1011960739 was live, 2/2 right after it went OOS); whether Target accepts a place-order on a
  cart holding two target-list TCINs; who removed won lines A/B on 1010892076 after 03:26.
  Full list: `workflow_result.json` → `research_questions` (20).
- **Operator lead from 09-30 (harvest clicks "half-failing") — investigated 10-01 by one
  antibot-analyst (MEASURED by that agent, not independently verified):** cosmetic for
  credentials. Every no-POST click is a RE-click on a PDP whose first click we already
  captured and killed with `fail_request` (1,185/1,185 no-POST; first clicks on a fresh PDP
  29/3,521, 27 of them the alt-1 preflight-429 episode); all 334 sets replayed on real shots
  were first-click captures (tokens=6). The on-page "item not added / something went wrong"
  is what every SUCCESSFUL capture looks like (we fail the request on purpose).
  `selected:False` is hard-coded (`shape_harvest.py:697`; 0/24,480 True ever); Target's PDP
  aria-label changed 09-15/16 account by account. The 401 switch on 1010892076 rode sets of
  all ages and all three harvest SKUs, and the same sets carted other TCINs later — not
  credential quality. **Real finding:** at drop hour the harvest SKUs lose their
  Add-to-cart button (21516452 on all 3 accounts 02:30-03:10 on 10-01; 0 button-absent on the
  10-02 and 10-05 runs per the 10-05 G2 audit, so NOT "every drop night"; cause NOT ESTABLISHED). Specced, not built: `TARGET_HARVEST_RECLICK_BLOCKED_DOC`
  (default 1 = today; 0 skips the dead re-click; one account only, 3 drop nights, compare the
  per-TCIN 401 share vs the other two) + log-only `TARGET_HARVEST_DOC_LOG`. Code pointers:
  `shape_harvest.py:674-698`, `purchase_executor.py:3681-3719`, `:3855-3999`.
- **Process (10-01):** a power outage killed the post-run workflow at round 3 (69 of 77
  agents done). Native `resumeFromRunId` cannot replay a concurrent run (its cache keys chain
  on call order) — it re-ran round 1 and was stopped. Recovered with a SEEDED continuation:
  the same script with the 69 journal results embedded by label, proven offline first
  (`logs/analysis_2026_10_01/postrun/recovery/`, `replay2.mjs`). Also: `post-run.js` builds
  the critic digest in report COMPLETION order — pin the order if the script is ever resumed.
  Offline suite baseline before any change: 31/31.

## POST-RUN 2026-09-30 — real restock, 1 cart, 0 orders (claims C-0930-02..08, `docs/CLAIMS.md`)

Operator: "massive restock", nothing purchased; wants a workflow-based fix, a rebuilt
bug-fixing process, and "besides rate limiting it's a trust factor". Reports and
extraction files: `logs/analysis_2026_09_30/postrun/`.

- **The run** (`run_20260930_014647.log`, 01:46:47 → 09:24:52): 12 stock windows on 5 TCINs,
  02:31-04:54 (1010892076 ×7, 1010892067 ×2, 1010892069, 1010892078, 95082118), 47 races all
  3 wide, 256 main-tab add-to-carts = 248 × 429 `ERR_A2C_TCIN_RATE_LIMITED` + 3 × 429
  FAST_SELLING (95082118, every account's first shot) + 3 × 401 + 1 × 503 + **1 × 201**
  (alt-1, 1010892067, 04:00:06). **0 orders.** Flip-race first shots 4/33, every other shot
  0/219 — [MEASURED]. **Regime: NOT ESTABLISHED either way** (C-0930-08 WEAKENED by the v2
  workflow eval): the pooled first-shot rate is COMPOSITION — 1010892076 had never carted
  before 10-01 (0 × 201 in 944 shots, 0/145 home first shots 09-15 → 09-30; C-0930-09; it carted twice on 10-01, C-1001-11) and was 105 of 138
  first shots tonight, while the new 95082118 went 3/3. **Monitor FLAG in the restock
  hours:** 02:00-04:59 loss 1.20% vs 0.040% the same hours on 09-29, from RedSky 206 bursts
  (382 vs 10 partial reads, C-0930-11); cost to detection not established (flips we saw
  were fired on in 37-110 ms).
- 🔴 **THE GATE ORDER (C-0930-03, VERIFIED for 09-30).** The limiter answers FIRST: every
  `ERR_A2C` 429 comes back in 111-247 ms with no `x-ssx-hop` / `fastly-restarts` / envoy
  header, while 201 / FAST_SELLING / 401 carry `x-ssx-hop=1` and take 0.85-6.4 s inside the
  same flip volley (not every past-limiter response is slow: a later-shot 401 took 250 ms and
  09-17's past-limiter answers 238-803 ms, so the HEADER SET is the primary evidence and the
  in-volley latency only corroborates it). Model [INFERRED]: **edge limiter (`ERR_A2C`, ~130 ms) → SSX hop
  (keyless 401 `_ERR_AUTH_DENIED` = the Shape verdict) → restart → cart service (201 /
  FAST_SELLING 429 / 424)**. So **a 401 IS a shot that got past the limiter.**
- 🔴 **THE LIMITER IS NOT TRUST (C-0930-04, counts VERIFIED, mechanism NOT ESTABLISHED).**
  09-15/16, same 91 races: alt-1 on a Bright Data exit (Shape-denied 61/61, the least
  trusted client) got past the limiter on 30/90 LATER shots; home-IP primary 0/90. So it is
  not explained by Shape/HUMAN trust and differs by network location — but it is NOT a
  simple per-IP allowance either: on 09-30 same-IP volleys fired within ~4 ms split 2/3, 1/3
  and 3/3 (windows 8, 11, 12). Mechanism NOT ESTABLISHED; the TCIN's own state dominates
  (1010892076: 0/145 home first shots 09-15 → 09-30; 10-01 it recovered, C-1001-11). **Trust shows at the SSX wall** (C-0930-05, PARTIALLY CONFIRMED): hot
  shots past the limiter were 401'd 514/518 on 3 BD exits vs 13/38 on the home IP.
- 🔴 **THE CART WAS RETIRED BY OUR OWN CAPS (C-0930-02, VERIFIED).** `call_cap` (120 s) at
  ticket 39, then `cart_ticket_cap` (40) at +128 s — while ticket 40's pre_checkout proved
  the line was ours and the TCIN read in stock ≥165 s more. The exit's delete read 429 →
  dirty flag → two in-stock dispatches fired nothing → at 04:13 (window 2, same TCIN) the
  bot deleted the ~13-minute-old line and fired 4 fresh add-to-carts (all edge 429). The
  line had survived 13 min in the cart. Refract: keep submitting, no cap stated.
  **Fix F1 is now LANDED (2026-09-30, flag-gated, default OFF = byte-identical):
  `TARGET_WONCART_LIVE_CAP_EXEMPT=1` makes the ticket cap + per-call cap not fire while the
  stock probe reads live; only `TARGET_WONCART_HARD_MAX_TICKETS` (150) and the ride deadline
  bound a live cart. `woncart_cap_binds` / `woncart_call_cap_binds` in `purchase_executor.py`,
  all 4 cap sites routed through them; `test_f1_live_cap_exempt` (10 checks); offline suite
  31/31. NOT YET ARMED — editing the production wrapper is blocked by the "[Production Deploy]"
  gate, so arming is the operator's step: add `set TARGET_WONCART_LIVE_CAP_EXEMPT=1` after
  bat:1142; kill-switch `=0`. Readout: event store `loop_ends.reason` + `tickets` max n per
  live cart.** Conversion value of more tickets: NOT ESTABLISHED and history leans against
  it — orders come from early place-orders — 18 of 21 ever ≤~4.6 s after detection,
  place-orders >2 min into a window 0/150 (C-1001-07, superseding C-0930-10's 17/20); the one
  post-FAST_SELLING order (94f186c1) was one FS, a 45 s hold, then one POST → 200, its cart won
  ~18-19.7 s in (C-1001-10). Hot won-cart tickets 0/289 (0/90 to 09-30 + 0/199 on 10-01).
- **REFUTED today:** "keyless 401 = an unsigned write" (C-0930-06: signed decoys 401 at the
  same ~20% rate, n=1,775); "the 40-ticket cap is not a lever" (C-0930-02); the gate order
  "Shape first → limiter → cart" (C-0930-03). **Instrument fault:** `readout_2026_09_30.py`
  double-counts decoys (W3/M1) and 4 H1 lines (C-0930-07) — use the event store.
- **Experiments that could discriminate (operator's call; NOT built):** E4 human control —
  during a window, the operator taps add-to-cart on a phone with a NON-bot account, ~60 s on
  home Wi-Fi then ~60 s on cellular (no code); E5 one account on a second, Shape-acceptable
  network identity with two same-window controls. Rejected on evidence: stop decoys (E1,
  C-0930-06), mimic the page's request shape (E3: the limiter already admits it at the
  flip). E2 (separate harvester browser) acts only on the SSX wall (~+0.7 carts/night max).
- **Process:** the post-run ran as a Workflow (15 agents, 0 errors, 9 claims verified, 0
  refuted). Structured event store `tools/events/` + saved post-run workflow: see the
  09-30 process section of `docs/CLAIMS.md` / memory when landed.

## PRE-DROP 2026-09-29 late → restock 2026-09-30 03:00 (`/pre-drop`)

Operator: restock at 03:00; TCINs `1010892076, 1010892067, 1010892069, 1010892078,
95082118`; "dont delete any skus"; wants the implementation fixed, not "lottery".

- **Config:** the first four were already enabled at `"qty": 2`. **`95082118` was in no
  config and no run log ever** → added enabled, `"qty": 2`, placeholder name (nothing
  branches on `name`). 11 enabled (≤30). Backup
  `config/product_config_backup_pre_2026-09-30_drop.json`. — [MEASURED 09-30 00:0x]
- 🟢 **The accounts keep themselves alive now (C-0929-01, REFUTES "nothing in the bot
  re-mints").** Under the 09-28 arming (`KEEPFRESH=0`, `RELOGIN_MAX_PER_6H=0`) each member
  token was re-minted inside the running bot at its 4 h expiry: the jars saved 09-29 23:13
  hold `sut=R` tokens issued **21:11:25 / 21:12:44 / 21:14:29**, mid-run, with no login or
  restart after 09:11:46, on the 4 h grid of the 01:11-01:14 hand logins (+8..+22 s over five
  cycles). 0 `401 ERR_UNAUTHORIZED` decoys in both 09-29 runs vs 1,348 on 09-28. 12 expiries,
  0 lapses. Mechanism NOT ESTABLISHED. **Hand-login timing is no longer the lever** — a
  start at any time works (before 01:11 the running bot re-mints; after 01:14 the wrapper-start
  pass re-mints an expired token, n=2 on 09-28). — [MEASURED 09-29/30]
- **Readiness 09-30 ~00:00:** 3/3 ✅ MEMBER, login-session 29.1 d, refreshToken 89.9 d, member
  tokens expire 01:11:25 / 01:12:44 / 01:14:29. — [MEASURED]
- **09-29 had NO restock** (log-miner, both runs): 0 in-stock reads, 0 races, 0 shots; every
  ATC response a decoy. Monitor: 01:16-06:33 55,692 sweeps 403=0 429=0 other 31 (0.056%);
  09:11-23:15 149,731 sweeps 403=0 429=0 other 214 (0.14%); 18/18 ready; `[STOCK][206]` 13 /
  59 isolated singles (some 9/10 incomplete); 0 visibility banners; tracebacks all
  `WinError 10054`. **No regime change.** This is the proxy-pool proof for tonight
  (production path, the wrapper's own env, 19 h), in place of `validate_proxies.py`. — [MEASURED]
- **`95082118` = Mega Evolution Ascended Heroes Elite Trainer Box** (web search listing +
  a 09-23 hobbyist repo pairing it with 1010892076 / 1010892069). — [REPORTED 09-30]
- 🔴 **Refract's LIVE docs (09-30, 5,798 lines; archived
  `logs/analysis_2026_09_30/research/refract_llms_full_2026_09_30.txt`) differ from the 09-21
  corpus (87 diff hunks) and say, verbatim: "Target has become a submit order lottery.
  Carting is not the hard part anymore: plenty of users cart around 90% of their tasks on a
  drop, especially on macOS. Checking out is… only about 1% of carts turn into an order"
  (L3504); "10 tasks does not cut it anymore, and why 5-account setups do not work. The
  median running task count is 12… The people you see hitting double digits… are probably
  running hundreds if not thousands of tasks" (L3506); "Local Windows gets flagged much
  faster and tops out around 30 tasks" (L3508); checkout = "spamming submit order to force
  the order through the rate limit. Every bot has to brute-force it this way… it is why the
  number of accounts you run matters so much" (L3699). — [REPORTED, vendor, no n]
  **So:** at their 1% cart→order, even their 90% carting on 3 accounts is ~0.03 orders a
  window; the scale lever is real. **But our CARTING is the gap they do not have:** 09-25
  carted 1 of 42 account-races (2.4%) and re-shots went 0/663 vs "~90% of tasks". (The
  "trust score / our own decoy traffic" lead that stood here was tested on 09-30 and does
  not hold for the limiter: C-0930-04 — the limiter tracks network location, not trust;
  C-0930-06 — signed and unsigned decoys 401 at the same rate, and 0 of 1,804 decoys ever
  drew a limiter 429. Refract's scaled users spread add-to-carts over residential harvester
  proxies, i.e. many network locations.)
- 🔴 **WHERE OUR CARTING DIES — per-shot credential join, 1,760 hot shots, five nights
  (09-11, 09-15/16, 09-17, 09-22/23, 09-25; orchestrator script, 09-30):**
  | IP | first shot of a race | every later shot |
  |---|---|---|
  | home line (all buyers since 09-20) | ~7-12% admitted | **0 of 1,300+** (09-15 primary 0/89, 09-22 0/19, 09-25 0/1,171) |
  | BD exits (09-15/16: alt-1 AZ, business Dallas) | 34% / 5.5% "passed" | same as first — but **every one a keyless 401** (71/71) |
  Page-signed (bank-empty) shots on strict SKUs 0/1,000; cookied re-shots on strict SKUs
  0/261 at any cookie age (0/135 under 15 s), account, harvest page, a0 or window age.
  Model, CORRECTED 09-30 (C-0930-03; the order that stood here was backwards): **edge
  limiter first (`ERR_A2C` 429, ~130 ms, no `x-ssx-hop`) → SSX hop (keyless 401 = the Shape
  verdict) → cart service (201 / FS 429 / 424).** So the BD exits PASSED the limiter
  (later shots too) and then failed Shape at the SSX hop; the home line passes Shape ~2/3 of
  the time but its limiter allowance is spent by the flip's first volley. Trust (the
  cookie score, Refract's "90% or higher") acts only at the SSX wall. **The synthetic-input
  hypothesis below therefore concerns the SSX wall only (NOT ESTABLISHED)** — hand logins pass ~100% while scripted logins in the same Chrome on the
  same IP fail ~100% (0/25), and every cookie is minted by a CDP `Input.dispatchMouseEvent`
  click of ≤9 Bezier points ~80 px apart, a teleported start, no scroll, no dwell
  (`shape_harvest.py:409-548`). Constraint: more mouse events grow the `-a` sensor toward the
  ~7,900-byte chunk limit → `-a0` → 431 header-too-large on the shot (09-13). Test design: A/B
  one account with human-grade input (OS-level SendInput or high-fidelity paths) on the next
  drop — any later-shot admission vs the 0/1,300 baseline is decisive; or scripted login on a
  throwaway account (0/25 baseline), operator-approved only. **Not changed before the 09-30
  drop** (a broken harvester would cost the only shots that pass).
  **Refined by Refract's live in-bot harvester page (09-30):** "Show Browser: does not matter at
  all… Harvesting works the same either way" → they do NOT rely on visible/OS-level input, which
  weakens the pure mouse-path idea. What they stress: a SEPARATE harvester browser (tasks only
  replay its cookies), "try different browser types… Chrome, Brave, Edge", resi or home IP, and
  "Local Windows gets flagged much faster" (device trust decays with use). Ours mints every
  cookie inside the ACCOUNT's own long-lived Chrome profile, which also fires a decoy ATC ~every
  46 s 24/7 (~20% unsigned → 401). = parity delta #1 of 09-21 ("browsers decoupled from
  buyers"). **Next experiment: one account fed by a clean separate harvester browser (fresh
  profile, not logged in, Brave/Edge), A/B on the next drop vs the 0/1,300 later-shot baseline.**
- **Checkout path map (purchase-flow-engineer, 09-30, `pe` = purchase_executor.py):**
  after a hot 201 the chain runs pre_checkout (≤5 tries, 250 ms, 1.5 s) → ONE place-order;
  a `pre_*` stop or a PO 429 FS/RF enters the ticket loop (`pe:672-696`); a first-PO 424,
  keyless/non-CVV 400 or 401/403 still falls to the legacy nav+DOM path (27.96 s on 09-25)
  and a **whole-cart clear** (now `pe:6060-6063`; the MAX_PURCHASE self-heal clear is `pe:5510-5592`).
  In the loop an unverified cart fires a place-order only after a pre 2xx; on 10-01 6 of 8 hot
  in-chain chains reached an in-chain place-order (the earlier "4/4 ended `pre=429 po=0`" no longer
  holds; G5 audit 10-05). The 40-ticket cap (and the 120 s per-call cap) ENDED
  A LIVE, VERIFIED CART on 09-30 (C-0930-02) — it is a lever; the fix is designed but not
  armed (see the 09-30 post-run section). PRE_RETRY, the 1,1,1,2,2,3 schedule, eviction
  read/PRESUME and RF_ENTRY: **0 live runs since arming** — tonight is their first live
  test if a cart is won. `TARGET_RESERVATION_BAIL` governs only the legacy DOM loop
  (`pe:10186-10192`), no conflict with R1. Per-ACCOUNT 45 s FS cooldown (`pe:7542-7565`,
  read `:4585`, set by the loop at `:9100-9104`) sends that account's next fresh cart on ANY
  TCIN down the legacy path — cost 0 on 09-30 (0 `[THROTTLE]` lines); follow-up. — [INFERRED from code + MEASURED counts, 09-30]
- **ARMED 09-30 ~01:35 (new code, LOG-ONLY): `TARGET_ATC_RESP_HDRS=1`** — one
  `[ATC_RESP_HDRS] tab= status= n= | name=value | ...` line per add-to-cart response with
  EVERY response header, sorted (main tab: every response; warmup: 401 only, first 20 per
  account); Set-Cookie values never printed (names only); built before the response is
  continued, printed after, no await (`purchase_executor.py` `atc_resp_hdrs_line`,
  `atc_resp_hdrs_on`). Answers: does the hot-item 429 carry Retry-After / rate-limit headers
  (a per-client penalty window would explain later shots 0/1,300), which layer answers each
  status, and what a keyless 401 is on the wire. `tests/test_dx_logs.py` +24 checks (276/276),
  4 mutations caught. Readout rule H1 in `readout_2026_09_30.py`. Kill: =0.
- **Operator hand-logged all three 09-30 ~01:09-01:12** → readiness 01:13: 3/3 ✅ MEMBER,
  tokens exp 05:09:47 / 05:10:49 / 05:12:14; 95082118 PARTIAL until the bot's first scan.
- (The line below was true until 01:35.) **Nothing new armed, no code changed.** Wrapper = HEAD + the uncommitted 09-28 pair.
  Offline suite **30/30** (358 s) on this tree. Readout `tools/analysis/readout_2026_09_30.py`
  (chains 09-29 → 09-25; adds N1 new-TCIN visibility and M1 token continuity with `--jars`:
  member / valid-at-stop / 4 h-grid). — [MEASURED 09-30]

## PRE-DROP 2026-09-28 late evening → possible drop 2026-09-29 03:00 (`/pre-drop`)

Operator: a drop may or may not come at 03:00; TCINs unannounced, so the config is
unchanged (the same 10 enabled, all `"qty": 2`, backup
`config/product_config_backup_pre_2026-09-29_drop.json`). More accounts are being
made; none is wired in tonight (a new account needs its address, card and one
manual order first).

- 🔴 **All three accounts are SIGNED OUT right now; each saved jar holds only a GUEST
  idToken (`sut=G`), no accessToken.** `check_session_readiness.py` printed ✅ 3/3
  MEMBER anyway: a jar with no accessToken fell through to "will re-mint at startup
  (usually fine)". Fixed (display only, `READINESS_GUEST_CHECK`, default on; exit
  code still always 0): it now reads the idToken and prints ❌ SIGNED OUT, and for a
  member token prints its expiry. **Every account needs a FORCED hand login**
  (`hand_login_primary_force.bat`, `hand_login_business_force.bat`,
  `hand_login_alt1_force.bat`). The wrapper's `relogin_one.py all` pass cannot do it:
  its check is the `/account` URL after 3 s (`relogin_one.py:66-74`), which a guest
  passes (business, 09-28 16:12). — [MEASURED 09-28 23:00]
- **The 09-28 16:12-21:30 run** (`run_20260928_161223.log`, daytime, killed by the
  operator): no stock (0 in-stock reads, 0 races, 0 shots). Monitor 56,029 sweeps,
  403=0, 429=0, other=27 (0.048%); 18r most of the run, 93 one-session dips to 17r;
  `[STOCK][206]` 21 isolated singles, every one `complete=10/10` (errors in
  `store_positions` / `promotions`, not fulfillment) — L1 works, no burst. 41
  tracebacks, all benign `WinError 10054`. — [MEASURED 09-28, log-miner]
- 🔴 **The mint outage persists and the bot turned it into dead accounts again.**
  Rung 1 (`gsp token_refresh`) 404 on 253/253; rung 2 0/12 on live sessions; 0
  `minted via` all run. All 80 mints 09-17 → 09-25 came from rung 2; rung 1 had not
  worked since 09-16 (docs/TARGET_CHANGES.md, updated). What still mints: a hand
  login and the wrapper-start validate pass (fresh Chrome loading `/account`, nothing
  deleted: primary 16:11:47, alt-1 16:12:14; n=2). **A member token lives exactly
  4 h** (n=5). Chain on 09-28 (primary and alt-1): the keep-fresh repair fired at
  19:44:01 with 27 min left → rung 2 deleted the live token → the nav refresh
  "succeeded" on generic selectors and saved the token-less jar over the good file →
  the scripted re-login (`Network.clearBrowserCookies`, then "username did NOT
  advance", 0/6 all day) left each a GUEST; the idToken `iat`s match the attempts
  (business 16:49:46, alt-1 20:19:51, primary 20:20:03). The 401x3 heartbeat also
  deleted live tokens on 3 false alarms (restored by the watchdog in 22-41 s). A
  write-dead account is still dispatched and fires 401s (`worker_pool.py:344-353`
  has no auth check). By 20h the fleet's decoy writes were 95% `401
  ERR_UNAUTHORIZED`, 100% at 21h. — [MEASURED 09-28; antibot-analyst +
  purchase-flow-engineer]
- ⚠️ **A decoy 424 ("write-auth alive") does not prove a MEMBER token:** primary and
  alt-1 read 424 for ~40 min after becoming guests (a guest cart accepts writes).
  Only the jar's accessToken `sut` or a `could NOT mint` line says member vs guest.
  — [MEASURED 09-28]
- **ARMED 2026-09-28 pre-drop (existing flags, no new code) — UNPROVEN LIVE
  (docs/CLAIMS.md C-0928-02..04):**
  `TARGET_TOKEN_KEEPFRESH=0` (was 1 since 07-12) — no in-bot path deletes a member
  token, so a hand-login token lives its full 4 h; the sentinel falls back to the
  pre-07-07 `/account` navigation check (holds the page lock p50 1.8 s, p90 8.8 s,
  n=41, every ~300 s per idle account). `TARGET_RELOGIN_MAX_PER_6H=0` (was unset = 2)
  — no in-bot scripted sign-out + login, so a live login-session outlives its token
  and the next wrapper start can re-mint from it. Fresh-context verifier: no deleter,
  no sign-out, no dispatch/order change CONFIRMED; "the /account check is the only new
  behaviour" REFUTED — also a park can't clear early, 3 failed `/account` checks in a
  row restart that buyer's Chrome (recent live-session checks 62/62 OK), the sentinel
  no longer sees token loss (heartbeat still logs it), a TOKEN CHURN alert's text is
  now false. `relogin_one.py` at wrapper start is untouched by both and still signs out
  on a login redirect or ANY navigation exception. `TARGET_SENTINEL_HOMEIP_RELOGIN=0`
  was considered and is a no-op (applies only to proxied buyers, `session_manager.py:2212`);
  `TARGET_TOKEN_MIN_TTL_S=0` was rejected (keeps the heartbeat's delete + the save race).
  Readout: `tools/analysis/readout_2026_09_29.py` (runs `readout_arm_2026_09_25.py`,
  then W1 first-shot 401s, W2 session survival, W3 decoy mix, K2 flag took, K3
  re-mint watch). Kill: KEEPFRESH=1 / delete the MAX line. Offline suite 30/30.
- Historic windows: 09-23 02:47-05:16, 09-25 03:32-05:41 (the only admissions 05:20 /
  05:23). (A "timing is the lever / nothing re-mints inside the bot" line stood here; it
  was REFUTED 09-30 by C-0929-01 — the bot re-mints at expiry under this arming.)
- **What actually happened 09-29:** the operator went to bed; forced hand logins via the
  new `hand_login_all_force.bat` (all accounts in one run, 30 min deadman) at 01:11-01:14
  → member tokens `iat` 01:11:11 / 01:12:36 / 01:14:07, **lifetime exactly 4.000 h (n=3,
  first hand-login measurement)**, member refreshToken 90 d (the guest one read 180 d).
  Bat started 01:16:05; its `relogin_one.py all` pass did **NOT** re-mint the still-valid
  tokens (exp unchanged, n=3) — a fresh-Chrome `/account` load with a VALID token does not
  refresh it; the 09-28 16:11 mints (n=2) were of expired tokens. [MEASURED 09-29]
  (The prediction "write-auth ends 05:11-05:14" was wrong: the running bot re-minted at
  05:11 and at every expiry after — C-0929-01. The scheduled-restart workaround, blocked
  by the auto-mode classifier on 09-29, is therefore not needed.)
- **Proxy pool:** not re-validated with `validate_proxies.py`; the 09-28 live run is
  the stronger proof — production path, the wrapper's own env, 5.3 h, 18/18 exits at
  ~100% (0-11 `other` each), 0 × 403 / 429. The 2 reserve exits were not exercised.

## 2026-09-25 FIRST-GATE INVESTIGATION — what "99% lost at the first gate" really is

Operator, after the post-run: "bots are so successful they must be doing something
different". Five agents and two fresh-context verifiers; ledger `docs/CLAIMS.md`
C-0925-07..11. **Nothing here has been tested on a drop.**

- **Only first shots pass, and they pass at 10-30%.** Six restock nights: first shots of
  flip-opened races 15/142 (10.6%) vs every other shot 14/4,013 (0.35%); home IP 15/65
  (23%); a TCIN's first window of the night 13/74 vs later windows 2/68. First shots are
  3.8% of our shots, so ~97% of first-gate failures are re-shots and re-flips.
  [VERIFIED 09-25, C-0925-07/08]
- **Not a per-identity or per-IP budget** (two home-IP accounts passed in the same volley
  twice on 09-25) and not Shape/HUMAN (0 block lines). Whether our own traffic spoils a
  TCIN's later windows: NOT ESTABLISHED (inseparable from the TCIN's restock state).
- **We get through the gate; we have never converted what we win.** 9 hot carts in the
  bot's life (07-14 → 09-25), all qty 2, 0 orders: 4 never reached a place-order, 5 died on
  the checkout FAST_SELLING throttle (09-25's also saw RESERVATION_FAILURE). The 5 s
  ticket loop (R5, armed 09-17) has never run on a hot cart; R1 routes the 09-25 case into
  it. [VERIFIED 09-25, C-0925-09]
- **qty 2 is not what kills hot carts** (REFUTED). A 1-per-guest online limit is NOT
  ESTABLISHED: Target accepted all 9 qty-2 hot adds and enforces its limits at add-to-cart.
- **Firing before the flip: no support** — 0 admissions ever before our read, and shots
  after an out-of-stock read never became a cart. Not built. [C-0925-10]
- **Read-to-POST is ~0.1 s; the flip-to-read leg (~0.17 s mean at 0.34 s read spacing) has
  never been measured**, hence L3. A faster read rate needs a daytime soak first: the 09-21
  429 tarpit hit at 0.375 reads/s per IP (we run 0.17). [C-0925-11]
- **Competitors (Refract docs, REPORTED):** 10 tasks on 10 unique accounts from a home IP,
  fire on monitor detection, "extremely low pass rate for everyone", and keep submitting
  through a FAST_SELLING checkout. More accounts is the lever that multiplies first-volley
  draws (scale plan 3 → 5 → 10).

**ARMED 2026-09-25 after the investigation — UNPROVEN LIVE:**
- **L3 `RESILIENT_FLIP_LOG=1`** — new code, log-only: one `[STOCK][FLIP]` line per
  out-of-stock → in-stock read (epoch-ms `read_ms`, `last_oos_ms`, the read's round trip, a
  new-window flag, RedSky's raw ATP / max_order_qty / purchase_limit), written after every
  dispatch callback, at most 50 per TCIN per run. Readout L3 in
  `tools/analysis/readout_arm_2026_09_25.py`: FAIL = a raced TCIN with no FLIP line.
  `tests/test_redsky_flip_log.py` 38 checks; 5 mutations caught. Kill: =0.

## 2026-09-25 DROP + POST-RUN — 0 orders; what is armed for the next run

**The run** (`run_20260925_004139.log`, 00:41 → ~07:50, stopped by the operator): stock
windows 03:32-05:41 on all 7 drop SKUs (the ETB twice), 14 races (13 of them 3 accounts
wide), 1,212 main-tab shots — 1,205 edge 429, 4 FAST_SELLING 429, 2 keyless 401, 1 × 201.
**0 orders.** In-stock read to the first POST ~0.1 s (C-0925-11). [MEASURED 09-25;
docs/CLAIMS.md C-0925-01..06]

- **The edge limiter is the binding stage (99.6% of shots)** and admitted only a window's
  FIRST volley (fired 0-11 ms after the window's first shot): first shots 5/20 vs re-shots
  0/663 at window age 2-120 s; first-shot rate unchanged vs 09-23 (p=0.33). [VERIFIED 09-25,
  C-0925-01; timing per C-0925-11]
- **The one cart was lost by OUR checkout routing:** business, AH Meganium tin, 201 at
  05:20:27 → pre_checkout 201 → in-chain place-order 429 `RESERVATION_FAILURE` → the
  won-cart loop's gate refused it (FAST_SELLING only) → 26.7 s legacy DOM detour on a page
  with no button → one in-window place-order → 424 `INVENTORY_NOT_AVAILABLE`.
  [VERIFIED 09-25, C-0925-02]
- **primary's first shot was a keyless 401 on the only two windows whose first shots were
  admitted** (Meganium 05:20, Feraligatr 05:23). [MEASURED 09-25, C-0925-06]
- **Target-side, cause unknown:** RedSky 206 bursts 02:10-04:49 (cost 0 s of detection
  tonight) and, after the drop, every account's member-token mint failing from 06:01
  (business dead at stop; primary and alt-1 recovered through a scripted re-login, which
  went 2/5). [MEASURED 09-25, C-0925-03/04; docs/TARGET_CHANGES.md]
- A2 3-wide: alt-1 (0 shots on 09-23) earned 3 of the 5 admissions. A3: 22 stops, 0
  premature (at the 2.7 s cadence ~3 shots per account still went out after the last
  in-stock read). G1: the Feraligatr raced 2-wide while business held its cart — no
  double race. [MEASURED 09-25]

**ARMED 2026-09-25 post-run (committed `16cec4ca`) — all UNPROVEN LIVE. Readout,
pre-registered and smoked on 09-25 and 09-23: `tools/analysis/readout_arm_2026_09_25.py`.**
- **K1 `TARGET_WAVE_FIRST_EDGE=1`** — A1 killed by its own pre-registered rule; edge and DCO
  429s take the 55-70 s cold re-entry again. A3 stays at 8 s (≈0.13 blind shots per
  account per window end at this cadence).
- **R1 `TARGET_WONCART_RF_ENTRY=1`** — new code (`woncart_eligible`): a place-order received
  as 429 RESERVATION_FAILURE enters the ticket loop; 424 stays out. No new double-order
  path (the loop stops on a 2xx, AC-1 latches on ambiguity). Conversion NOT ESTABLISHED.
  Kill: =0.
- **L1 `RESILIENT_206_LOG=1`** — new code, log-only: one `[STOCK][206]` summary a minute
  (RedSky's errors; which TCINs came back complete / absent / incomplete). No ingest yet:
  the parser reads missing fulfillment as out of stock.
- **L2 `TARGET_TOKEN_MINT_LOG=1`** — new code, log-only: rung 1's HTTP status and, on a
  failed rung 2, where the /account load landed. Decides C-0925-04 (a) vs (b).
- **Offline suite 29/29** (0 failed, 0 skipped, 354 s) on the final wrapper. New files `tests/test_redsky_206_log.py` (28) and
  `tests/test_token_mint_log.py` (22), 11 new checks in `test_won_cart_direct_smoke.py`;
  every new branch mutation-checked.
- **Deliberately NOT done:** a 206 ingest (09-25: needed L1's shape first — measured 10-05:
  94/94 storm bodies complete=17/17, store_positions errors only; what is still missing is
  AGREEMENT with 200 reads, measured by FS-206-SHADOW, armed 10-05); a token keep-and-restore
  (its motivating claim was REFUTED; worth ≤30 min per account); skipping the legacy DOM
  detour and stopping the legacy hold on OOS (R1 routes the observed case around both —
  follow-ups); the C-0924-01 MISS re-arm (0 contention skips on 09-25).

**Before the next run:** business needs a forced hand login — run
`hand_login_business_force.bat` (new 09-25; `relogin_one.py business --manual --force`); check
all three with `check_session_readiness.py`. Analysis agents now run on Opus 5.5 (reasoning,
verifiers) and Sonnet 5 (extraction).

## PRE-DROP 2026-09-24 late evening → drop 2026-09-25 03:00 (`/pre-drop`) — history

**Nothing new armed, config unchanged.** Tonight is the **first live night** of the
four 09-23 changes below (A1/A2/A3/G1).

- **The operator's 7 drop TCINs were already present, enabled, `"qty": 2`:**
  `1010892076` 30th ETB, `1010892067` 30th Poster, `1010892065` 30th Greninja ex Box,
  `95120834` Ascended Heroes Booster Bundle, `1012644667` / `1012644666` / `1012644665`
  Ascended Heroes Tins (Emboar / Feraligatr / Meganium). The other 3 enabled
  (`1010892078`, `1010892069`, `1011960739`) are kept (operator: "dont delete any
  skus"). 10 enabled, ≤30. All 10 were visible to RedSky on the 09-23 run. — [MEASURED 09-24]
- **Accounts:** `check_session_readiness.py` 3/3 MEMBER (persisted state). primary's jar
  was re-saved 09-24 18:54:50 by an **aborted wrapper start** (relogin_one validate-pass
  "already logged in ✅", then Ctrl+C / window close at 18:54:51 → `0xC000013A`; app.py
  never wrote a run log). business/alt-1 jars date from the 09-23 10:27 shutdown (~37 h
  idle; the script shows 42 h, its +5 h display skew). login-session 13.0 d / 21.0 d,
  refreshToken ~88 d. — [MEASURED 09-24 23:48]
- **Monitor details that are easy to get wrong:** production `TARGET_LEVEL_REARM_S` is
  **3 s** (`[LEVEL_REARM] armed — ... every 3s`, 09-23 log), not the 20 s code default.
  The buyer `TARGET_CHROME_MAX_AGE_S=2100` relaunch applies only to proxied Chromes
  (`session_manager.py:2320`, `self.proxy_url is not None`), so with all buyers on the
  home IP it never fires. The wrapper has no scheduled restart; it relaunches only after a
  crash. — [MEASURED 09-24]
- **Arming delta since the 09-22 audit:** the 5 flags changed on 09-23 are each set once
  and read by `bulletproof_purchase_manager.py`; no flag is set but unread; 82 flags are
  read by code but not set by the wrapper (same count as 09-22). The one new name,
  `TARGET_STOCK_PROBE_FRESH_S`, is a default knob (15 s, clamped 2-120; A3 fail-open),
  not a feature switch. Bat: CRLF, ASCII, 172 vars each set once; the 09-23 REM lines
  are clean (77 older REM lines carry `->`/`&`/`%%` and have run through every boot). —
  [MEASURED 09-24]
- **Offline suite 27/27** (0 failed, 0 skipped, 317 s), and 27/27 again after the bat
  REM correction (10 suite files parse the bat), on the exact working tree the bat will
  boot. — [MEASURED 09-25 00:16]
- 🔴 **A contention-skipped TCIN is not re-raced while it stays in stock (C-0924-01,
  VERIFIED for the pool-healthy regime).** `[MULTI_SKU_MISS]` leaves it `'ready'` with
  no rearm hint (`bulletproof_purchase_manager.py:4342-4350`); the sweep fires only on
  a False→True read and the level re-arm only takes `'failed'` or hinted TCINs
  (`app.py:1154-1165`). 23 historical distinct-TCIN skips on 7 nights: **0 raced in the
  same window**; in 11 the holder's race ended with the skipped SKU still in stock
  (≥1,120 s total) and it got nothing. Exception: the blind-pool tab-fetch fallback
  re-publishes the whole catalog every 3-5 s (n=0 during a skip). **Deliberately NOT
  fixed before the 09-25 drop** — the fix (stamp a rearm hint on the skip) would be a
  fifth unproven behavioural change on the night the first four go live, in the
  dispatch path, and must first be checked against lead C-0924-03. Cost of declining:
  if two SKUs' windows overlap tonight, the second gets zero shots unless it flickers.
  Readout D3 measures it. **First follow-up after tonight's readout.** — [VERIFIED 09-24]
- **Config order is not the priority lever it was believed to be (C-0924-02,
  VERIFIED):** every go-live is its own single-TCIN event handled in sweep read order;
  0 of 906 `[STOCK] IN STOCK:` lines ever named two TCINs. The list-order sort only
  acts on level re-arm and tab-fetch events. So nothing was reordered. — [VERIFIED 09-24]
- 🔴 **09-25 00:31-00:41 boot: primary's login had silently degraded to GUEST.** Four
  launches in a row exited 87 on the homepage probe (`[LOGIN_CHECK] probe error: Timeout
  (10.0s) waiting for element with text: 'Hi,'`) while every wrapper validate-pass said
  "already logged in ✅" — that pass only checks that `/account` did not redirect within
  3 s (`relogin_one.py:66-74`), which a guest passes (corrected 09-28). The read-only
  readiness check then showed primary `accessToken=guest/none (sut=G)` with a freshly
  reissued 180-day refreshToken (it read MEMBER `sut=R`, 89.8 d at 23:48). A **forced**
  hand login (`hand_login_primary_force.bat` = `relogin_one.py primary --manual --force`,
  new, untracked) at 00:40 → MEMBER, session-typed login-session; the next boot found
  "'Hi,' greeting found" at the first probe and went **ALL GREEN at 00:46:18** (18/18,
  10 TCINs, write-auth 3/3, selftests clean). — [MEASURED 09-25]
  - **The probe was right; it was not a false negative.** An apparent "harvest tab opens
    before the probe fails" ordering (5/5 boots) is a selection effect: a failing probe
    always takes ≥12 s, so the harvest line lands first whenever it fails. Do not cite it.
  - **Procedure:** if the boot loops on exit 87, stop the wrapper and hand-login the
    account with `--force` (plain `hand_login_all.bat` validates first and skips a guest
    jar that still has the cookie names). Letting the wrapper cycle is what degraded
    primary + alt-1 to GUEST on 08-25; 09-17's 4-failure loop likewise ended in an
    operator stop, not in the cooldown. — [MEASURED 09-25]
- Lead, unverified (C-0924-03): a single-TCIN event may reset another in-stock
  `'failed'` TCIN to bare `'ready'` (`bulletproof_purchase_manager.py:1658`, `:1671`,
  `:1721-1727`), hiding it from the level re-arm. One agent's code read; not measured.
  — [INFERRED 09-24]
- Readout, pre-registered: `tools/analysis/readout_2026_09_25.py` runs
  `readout_arm_2026_09_23.py` (T0-T6), then D1 visibility (every ground-truth read = 10),
  D2 priority (list order inside one stock update), D3 skipped-SKU coverage, D4 per-TCIN
  scoreboard. Smoked on 09-23 (reproduces the post-run: 37 shots = 16/8/4/9; D1 PASS on
  270 reads) and on 09-16 (D3: `1010892069` skipped for `1010892078`, 6 episodes, all 6
  LOST). — [MEASURED 09-24]

## ARMED FOR THE NEXT RESTOCK — set 2026-09-23 post-run (`/post-run`)

Four flag-gated changes, all armed in `run_bot_with_nightly_restart.bat` on
**2026-09-23** (they explain nothing before that date). Offline suite **27/27**
(316 s) with the new `tests/test_retry_oos_stop.py` (77 checks, mutation-checked).
Readout, pre-registered: `tools/analysis/readout_arm_2026_09_23.py` (T0-T6; smoked
on the 09-23 log, where T1 FAILs and T2 is INCONCLUSIVE under the old arming, as it
should). **All UNPROVEN LIVE.**

- **A1 `TARGET_WAVE_FIRST_EDGE=0` — KILLED 2026-09-25 by its own pre-registered rule and set
  back to 1** (C-0925-01, verified: 0 of 663 re-shots admitted). History: (was 1 since 09-09). Edge and DCO 429s re-fire at
  the 2.0-3.0 s edge cadence (`TARGET_ATC_EDGE429_RETRY_DELAY_MIN/MAX`, unchanged
  since 09-01) instead of a 55-70 s cold re-entry: ~35-40 shots per account per
  110 s race instead of 2. A 401 still takes the 55-70 s cold re-entry and its bank
  gate; the DCO burst is unchanged; the 40-attempt cap and 110 s deadline still
  bind; no new path to a place-order retry (fresh-context verifier, CONFIRMED on all
  five parts). **This is a BET, not a fix** — C-0923-02 is NOT ESTABLISHED in either
  direction. Kill rule (readout T4): revert to 1 if ≥150 re-shots at window age
  2-120 s get zero edge passes, or 401s exceed 30% of ≥60 shots. — [ARMED 09-23]
- **A2 `TARGET_MULTI_SKU_WORKERS_PER_TCIN=3`** (was 2 since 09-21 evening). alt-1 was
  logged in through every 09-23 window and fired zero shots because of the cap
  (C-0923-04, VERIFIED). **Cost (corrected 09-24, C-0924-01 VERIFIED):** a second
  TCIN that goes live while all 3 race the first gets `[MULTI_SKU_MISS]` and is
  **not** picked up when a racer frees — nothing re-publishes it while it stays in
  stock; only its own out-then-in flicker does (or the blind-pool tab-fetch
  fallback). History: 23 such skips, 0 raced in the same window. Contention itself
  is rare (15 pairs in 27 nights, 2 in the hot era; 0 of 5 windows on 09-23).
  `CAP_ALWAYS` stays 1. — [ARMED 09-23]
- **A3 `TARGET_RETRY_STOP_WHEN_OOS=1`, `TARGET_RETRY_OOS_STOP_S=8`** (new code,
  `_retry_oos_gone_s`). A race thread ends its window instead of firing when the
  monitor has had no in-stock read of the TCIN for 8 s (raw `last_true_at` /
  `last_false_at`, not the 20 s-hysteresis `live` flag); fail-open on missing or
  stale data. Checked before the cadence sleep and again right before the shot.
  **Exempt after a DCO/FAST_SELLING 429** (the cart service just answered for the
  TCIN). `grep [RETRY_OOS]`. (C-0923-03, VERIFIED.) — [ARMED 09-23]
- **G1 `TARGET_STUCK_RESET_LIVE_GUARD=1`** (new code, `_tcin_has_live_racer`,
  `_reservation_has_live_racer`). "A live racer is never stuck", at four points:
  the SILENT 60 s reset in `reset_completed_purchases_by_stock_status` (it runs
  first in `app._handle_stock_update`, on every periodic / edge / re-arm update),
  the 60 s reset in `process_stock_data` (now keeps scanning past a live race), a
  catch-all at the only live dispatch point (no race on a TCIN while a racer of its
  previous race is alive, whatever reset the record), and the 120 s reservation-TTL
  sweep (a live racer keeps its worker). Without it a race running past 60 s can be
  joined by a second race on the same TCIN that re-claims the same accounts
  (C-0923-05). **This path is LIVE-reachable under the 09-22/09-23 arming, not just
  A1's:** the silent reset has fired on a live race 6 times in production
  (C-0923-08, 08-27 and 09-15, harmless then because multi-SKU dispatch was off).
  The first cut guarded only the second copy and was REFUTED as sufficient by an
  adversarial review the same day; the four-point version reproduces and blocks the
  production call order offline, and a second fresh-context review CONFIRMED the
  same-TCIN protection. It also found, and live-reproduced, a pre-existing
  **cross-TCIN** gap (C-0923-09): with ≤1 session-ready account, dispatch takes the
  legacy path (no reservation, falls back to primary) and a busy account could be
  handed a second TCIN mid-purchase. Now also closed under G1: a worker with a live
  racer is never free for or reserved by a different TCIN, and with ≤1 ready account
  only one purchase runs at a time. Offline suite 27/27; `test_retry_oos_stop` 112
  checks, every guard point mutation-checked. — [ARMED 09-23]
- **Carried unchanged from the 09-22 pre-drop:** the same 10 hot TCINs; `"qty": 2` on
  every entry (the bat's qty-1 REM was corrected 09-23 to say so).

### Carried from the 09-22 pre-drop — still true

- **Config:** the same 10 enabled TCINs as 09-22, all hot, order unchanged (no entry
  has a `priority` field; list order only matters for multi-TCIN events, which a real
  go-live never is — C-0924-02). **All 25 entries now carry
  `"qty": 2`** — operator decision. Backup:
  `config/product_config_backup_pre_2026-09-23_drop.json`. — [MEASURED 09-22]
- 🔴 **The 09-18 U2 qty-1 pin is SUPERSEDED.** Six of the ten were pinned to 1 and
  four had no key. The flag `TARGET_QTY_PER_TCIN=1` is unchanged; it now pins
  everything to 2. The bat REM that still described the qty-1 pin was corrected on
  09-23. — [MEASURED 09-22]
- **Every qty decision ever logged for these 10 TCINs was already qty=2**, "RedSky
  limit unreported" — 462/462 `[QTY]` lines across all `logs/runs/`. The pin to 2
  removes the dependence on RedSky and `TARGET_QTY_OPTIMISTIC`. Pin math:
  `qty = max(1, min(2, cap))`, cap = `TARGET_QTY_CEILING` (unset → 2)
  (`bulletproof_purchase_manager.py:404-439`); the executor keeps qty>1 without PDP
  nav (`purchase_executor.py:4283-4284`) and re-clamps to the same ceiling
  (`:4366-4373`). — [MEASURED 09-22]
- **qty 2 reaches every REACHABLE add-to-cart POST** — claims-verifier, fresh
  context: the fast lane plus every legacy retry, wave-first re-entry and DCO-burst
  re-POST reuse ONE `qty` bound once per race (`bulletproof_purchase_manager.py:2071`,
  never reassigned in the `:2180-2210` loop). Exceptions drop to exactly 1, never
  more: the 422/409 `PURCHASE_LIMIT` retry (`purchase_executor.py:5054-5097`) and
  the 400 `EXCEEDED` self-heal's second try (`:5033-5044`). Held-cart / won-cart
  re-entry fires NO add-to-cart. Nothing can send >2: the native fallback's
  `min(qty,3)` clamp (`:7575`) is dead, `TARGET_ATC_NATIVE_FALLBACK=0` (`bat:630`).
  — [VERIFIED 09-22, PARTIALLY CONFIRMED only for the latent path below]
- ⚠️ **Latent, unreachable today: the DOM button-click ATC (`purchase_executor.py:5152-5226`)
  sets NO quantity** (Target's page default). It needs the buyer tab on a PDP, and the
  only buyer-tab PDP nav is `need_pdp_for_qty` (`:4272-4278`), which needs
  `TARGET_PDP_QTY_LOOKUP=1` **and** qty ≤ 1 — both, not either (the verifier's
  summary said "either"; the code and 0 click lines across all logs, including
  09-18→09-22 with six TCINs pinned to 1, say both). The SESSION_REUSE guard
  (`:4241-4242`) sweeps confirmation/thank/checkout/cart but **not a PDP**, so a tab
  that ever lands on one stays there. **Do not arm `TARGET_PDP_QTY_LOOKUP` without
  closing this.** — [VERIFIED 09-22]
- **Target has returned a per-customer purchase-limit rejection exactly once:**
  2026-07-14 03:01, TCIN 95267143, qty 2 → `400 MAX_PURCHASE_LIMIT_EXCEEDED`
  (`logs/runs/run_20260713_234024.log:23604`). The fast lane has no quantity
  handling — a 4xx ATC returns `fallthrough` (`purchase_executor.py:7727-7734`) and
  only the legacy path's self-heal retries at qty 1 (`:5054-5060`), i.e. ~2 extra
  POSTs. **On a hot SKU each of those POSTs has to pass the edge limiter again**, so
  a limit-1 item at qty 2 would likely throw away the one shot that got through.
  — [MEASURED 09-23, C-0923-07]
- **30th Celebration online limit is at least 2 for the Tin:** Target's cart
  ACCEPTED a qty-2 add of 1010892069 on 2026-09-16 03:29:43 (HTTP 201,
  `run_20260915_233355.log:46680`; the 07-14 limit hit shows Target rejects an
  over-limit qty at exactly that step). The other four have never had an add
  accepted, so theirs is unmeasured. Web sources (low reliability, 2026 articles):
  "limit of 2 of any one SKU per customer" and "limit one Pokémon TCG product
  purchase per credit or debit card", with some stores stricter in-store. Items:
  ...076 ETB $70, ...067 Poster Collection $14.99, ...069 Tin $30, ...078 Tech
  Sticker Collection $14.99, ...065 Greninja ex Box $30. **Operator keeps qty 2
  (09-23); the evidence supports it.** — [MEASURED 09-23 + REPORTED]
- **Arming audit (Phase 2):** 166 wrapper vars, each assigned exactly once, none
  conditional. 3 not read in `src/`/`app.py` — `LOGDIR` (bat-internal),
  `RELOGIN_SKIP_*` (read by `relogin_one.py`): no dead flags. 82 `TARGET_*` are
  read by code but not set by the wrapper; all 82 reconciled (69 plain env reads,
  13 indirect). Deliberately left OFF: HS-1 `TARGET_HOME_SHARE_GUARD` (its premise
  is the NOT-ESTABLISHED "extra volume hurts primary" claim), and
  `TARGET_CHECKOUT_BODY_CAPTURE` (holds the rejected place-order response behind a
  CDP round-trip on the re-shoot path, `purchase_executor.py:2224-2229`;
  `[FS_TICKET_BODY]` already covers the body). `TARGET_FASTLANE_QTY_GUARD` is
  forced on by the armed `WONCART_DIRECT`/`HELD_CART_REENTRY`
  (`bulletproof_purchase_manager.py:359-360`). — [MEASURED 09-22]
- **Proxy pool proven through the production path:** `validate_proxies.py`,
  `VALIDATE_POOL=all` (18 active + 2 reserve), 180 s, **with the wrapper's 16 monitor
  vars** + `CHROME_STAGGER_TOTAL_S=30`. **20/20 HEALTHY; 535 sweeps, 200=535, 403=0,
  429=0, other=0; 20 ready, 0 crashed**; warmup 3m46s; per-IP 200s sum to 535; 11
  tracebacks, all benign `WinError 10054`. The validator never prints its channel;
  `apps_raw` is inferred (exported in the same statement as `VALIDATE_POOL`, which
  took effect, and 0 PX walls on the BD prefixes). 09-22 was 532/532. ⚠️ A bare
  `validate_proxies.py` run tests the WRONG channel — the code default is `web`
  (`redsky_channel.py:50`), which is PX-walled on the BD prefixes. —
  [MEASURED 09-24 23:58]
- **Cross-TCIN dispatch contention is rare.** 674 `[RACE]` dispatches over 27
  nights; a second live TCIN was blocked (`[PURCHASE_CONCURRENCY]` skip or
  `[MULTI_SKU_MISS]`) in **15 distinct TCIN pairs on 7 nights — only 2 pairs in the
  hot era** (08-18, 09-15). 21 further skip lines are same-TCIN self-blocks
  (`'<tcin>#W3'` holds), not contention. So `WORKERS_PER_TCIN=2` idles one of three
  accounts in most lone-TCIN windows to cover a rare second TCIN. **Kept at 2** —
  whether a 3rd account at the same TCIN adds passes depends on the unsettled
  limiter key; pinned by `tests/test_0828_phase2_fixes.py:378`. Method note: a
  `[STOCK WATCH]`-episode overlap count is biased toward "lone" — that line prints
  every ~30 s, so sub-30 s flickers are invisible to it (09-15 showed 0 episode
  overlaps but 9 real cross-TCIN MISS lines). — [MEASURED 09-22]
- Readout pre-registered: `tools/analysis/readout_2026_09_23.py` (T0 qty, T1 label,
  T2 race width + MISS, T3 blindness + ready floor, T4 outcome, T5 stock events);
  smoked against 09-18 and 09-22. Offline suite **26/26**. — [MEASURED 09-22]

## Outcomes

- **20 orders in the bot's entire history. Zero hot/hype orders, ever.**
  — [MEASURED 09-20] `logs/analysis_2026_09_20/winning_shape/SUMMARY.txt`
- 🔴 **"All ordinary SKUs" has been DELETED from the line above — it was CIRCULAR.**
  It came from `winning_shape/eras.py:6-7`, which duplicates the same
  hand-maintained hot list, and under the old two-way classifier any TCIN nobody
  had judged fell through to "ordinary". So the claim only ever meant *"the order
  SKUs are not in our hot list"* — trivially true, since they were in **no** list.
  It is not evidence about hype and must not be used as such. — [VERIFIED 09-22]
- **All 20 orders are on SKUs that were never classified at all.** Two sets that
  overlap but are NOT identical — do not conflate them, I did once already:
  - **Order-producing (8), from primary log attribution:** `1011209273`,
    `1011483406`, `1011483414`, `95042136`, `95120832`, `95120836`, `95267143`,
    `95298172`. **None is in the config today — absent, not disabled.**
  - **`funnel.py`'s UNKNOWN shot bucket (8):** the same list except it has
    `1012055695` (shots, zero orders) and lacks `95042136` (1 order on 06-12 via
    the legacy API route, whose shots the fast-lane parser never emitted).
    `unknown_tcins` is collected at the shot site only, so a TCIN can carry orders
    without appearing there.

  With the three-way classifier (`funnel.py`, fixed 09-22) the whole-history
  funnel reads:

  | class | shots | carts | orders | G2 cart | G3 pre_checkout |
  |---|---|---|---|---|---|
  | ordinary (`1011483413` only) | 144 | 1 | **0** | 25.0% | 0% |
  | hot | 8,164 | 5 | **0** | 8.3% | 20.0% |
  | **unknown (the 8 above)** | 2,160 | 37 | **20** | **63.8%** | **91.9%** |

  The converting set carts at **7.7x** the hot rate. Those 8 are deliberately left
  `unknown` — there is no independent evidence of their class, and inventing one is
  the exact bug that produced the circular claim. — [MEASURED 09-22]
- **Zero orders of any kind since 2026-08-04.** — [MEASURED 09-20] same
- 78 carts won all-time, 58 lost, 20 converted. — [MEASURED 09-20] same

## The 2026-08-10 target-list change (`a30bc294`) — history, NOT a lever

**OPERATOR DECISION, 2026-09-30: "the tcins added are what i want if they arent added
that means i dont want them."** The enabled list in `config/product_config.json` is the
target list. The 8 SKUs below are absent ON PURPOSE. **Do not propose re-adding them**;
the goal is converting the SKUs the operator chose. This section only explains why
orders stopped after 08-04 (a different, less-protected target set), so that no
analysis compares the old order rate with tonight's SKUs as if it were one population.

All 20 orders came from **8 TCINs**. On **2026-08-10** — six days after the last
order — `a30bc294` ("sync product config/catalog to the 08-09 drop-ready TCIN
list") swapped `config/product_config.json` to a DISJOINT set, sharing zero TCINs
with the order-producing list. 0 of 8 are in the config (still true 09-30).
[VERIFIED 09-22, order attribution re-derived from primary logs]

| TCIN | Product | orders | last MONITORED |
|---|---|---|---|
| `1011483414` | Mega Evolution Pitch Black Booster Bundle | 3 | 08-04 |
| `1011209273` | Mega Greninja ex Premium Collection | 4 | 08-02 |
| `1011483406` | Mega Evolution Pitch Black Elite Trainer Box | 4 | 08-04 |
| `95298172` | Mega Evolution Chaos Rising Booster Bundle | 3 | 08-04 |
| `95267143` | Mega Evolution Chaos Rising Elite Trainer Box | 2 | 08-04 |
| `95120836` / `95120832` | One Piece Zoro / Kuzan Starter Decks | 2 | 08-02 |
| `95042136` | One Piece Luffy & Ace Starter Deck ST30 | 1 | 06-12 |

**The "Target stopped restocking them" hypothesis is `[NOT ESTABLISHED]` — it
failed its own strict test, 0 of 8.** Every one of the 8 stopped producing stock
lines (True *or* False) on or before its own last-order date. They were not
watched-and-absent; **they were never watched again.** `stock_monitor.py:360`
filters to enabled TCINs, so "stopped restocking" and "stopped being monitored"
are indistinguishable here, with zero exceptions in either direction.

**What IS directly evidenced is the config swap, not Target's behaviour.** Orders
stopped when the bot stopped being aimed at the SKUs that produced them — which was
the operator's choice of target, not a bot failure. Consequence for analysis: the
July-August order rate is NOT a baseline for the current hot-SKU list.

- Correction to `logs/analysis_2026_09_20/winning_shape/SUMMARY.txt`: it lists
  orders #1 and #2 as account `(pre-ident)`. Both were **W1/primary** —
  `build_table.py`'s regex requires a parenthetical that June-era lines lack.
  `[DISPATCH] <tcin> → W1/primary` is present in both purchase logs.
- n=20 re-confirmed independently: 21 raw hits, one duplicate (a `[RACE]`
  cycle-completion line re-emitting a winning order id). Three signal families
  agree file-by-file. Zero orders in any log after 08-04. [MEASURED 09-22]

## What actually happened after 2026-08-04

- **Zero orders of any kind since 2026-08-04.** Verified from the raw logs against
  five independent sources (116 `logs/runs/*.log`, `logs/purchases/*`,
  `purchase_states.json`, `activity_log.pkl`, `package.log`). Latest order:
  `logs/runs/run_20260804_000646.log:103231` — TCIN 1011483414, 06:13:48. —
  [VERIFIED 09-21, adversarial, no confound found]
- **The bot stopped being AIMED at ordinary SKUs — it did not fail at them.**
  True ordinary-SKU fast-lane volume in 08-06→09-18 is **~16 chains**, not 2,273:
  all on 09-17, all on TCIN `1011483413`. WIN-era ordinary volume was 2,288 chains.
  That is a **99.3% reduction in real ordinary-SKU attempts.** —
  [VERIFIED 09-21]
- Arming mix moved from ~**9 ordinary : 4 hot** (WIN era) to ~**1 ordinary : 23
  hot** (now). The one agreed-ordinary TCIN left is `1011483413`. — [VERIFIED 09-21]
- **Hot SKUs have never converted, in any era.** 0 orders from every hot cart ever
  won. Not a regression — never solved. — [MEASURED 09-20]

> **A previous version of this file claimed ordinary-SKU conversion "collapsed" to
> 0/135 at the stock edge. That claim was REFUTED on 09-21 and has been deleted.**
> It came from a classification bug (below), not from the bot failing. The
> project's own 09-20 conclusion — *"the target list changed, not the bot"* — was
> correct and survived the challenge.

## ✅ FIXED 2026-09-22 — the hot/ordinary classifier (commit `6483a161`)

**The defect was the DEFAULT, not the list.** `funnel.py` now has
`sku_class() -> hot | ordinary | unknown`; an unlisted TCIN is **never** guessed,
and the unknown bucket is printed with its TCINs named and an explicit *"do not
quote a hot-vs-ordinary rate while this is non-empty"*. `is_hot()` remains as a
back-compat shim for `limiter_key` / `readout_multi_sku` / `shot_index_yield`,
documented so callers know `not is_hot(t)` means **"hot or unknown"**, not
"ordinary". The four mis-bucketed TCINs (`1012644665/666/667`, `95290385`) are in.

**Also fixed in the same commit:** `shots.py` `FIRE`/`START` used a bare `(\d+)`
for the TCIN, so a `print()` with no trailing newline glued the next logger line
on and produced phantom 14-digit TCINs like `10126446662026` — those shots were
attributed to a SKU that does not exist. `IDENT` had already been hardened against
this same glue; these two had not. Now bounded to 8-10 digits with a glued-date
lookahead, verified on clean and glued 8- and 10-digit forms.

**Still true and still the constraint:** `config/product_config.json` carries no
hot/hype field, so there is no contemporaneous source to derive the class from and
the lists remain hand-maintained. The 8 order-producing TCINs are deliberately
left `unknown`. **Any analysis output from before 2026-09-22 that quotes a
hot-vs-ordinary split was produced by the two-way classifier and is suspect** —
re-run it rather than citing it.

---

**Historical description of the defect, kept because six scripts still carry
copies of the old list:**

`tools/analysis/funnel.py:34-36` defined `is_hot()` as a **static, hand-maintained
TCIN list**, duplicated in `logs/analysis_2026_09_20/winning_shape/eras.py:6-7` and
imported or copied by `limiter_key.py`, `readout_multi_sku.py`,
`throughput_vs_volume.py`, `home_vs_proxied.py`, `shot_index_yield.py`.
`config/product_config.json` has **no** hot/category field, so there is no
contemporaneous designation to fall back on.

It is **already known to be wrong**: TCINs `1012644665`, `1012644666`,
`1012644667` (Mega Evolution tins) and `95290385` (One Piece) are bucketed
"ordinary" but are hyped by the project's own 09-20 judgment. Those four are
**2,184 of the 2,273 (96%)** disputed chains, all fired on a single launch night
(2026-08-27) at ~1.2 shots/sec for 0 carts.

**Any hot-vs-ordinary comparison for dates ≥ 2026-08-24 produced by these scripts
is suspect until the list is fixed.** WIN-era (07-23→08-04) numbers are unaffected —
none of the disputed TCINs existed then. Fix by deriving the class from config
history plus actual publish dates, not from a set literal. — [VERIFIED 09-21]

## Shape credential harvest / bank

- **Harvest + replay first armed 2026-09-03**, commit `173683d5`. —
  [MEASURED 09-21] `git log -p -- run_bot_with_nightly_restart.bat`
- **Therefore the banked-credential path has never produced an order**: all 20
  orders predate 09-03, and there have been zero orders since 08-04. —
  [MEASURED 09-21, by composition of the two facts above]
- Bank is a per-account in-memory deque. `TARGET_HARVEST_BANK=6` armed (code
  default 3); 3 accounts → ≤18 system-wide. Refill ~1 per 40 s per account.
  TTL 300 s; replay-age cap armed at 300 s (code default 100). —
  [MEASURED 09-21] `shape_harvest.py:131-132,328`, `bat:833-835,885`
- Selection is newest-first (LIFO), with `TARGET_HARVEST_PREFER_NO_A0=1` armed, so
  the real comparator is "freshest without an `-a0` chunk, else newest". —
  [MEASURED 09-21] `shape_harvest.py:339-359`
- Harvest is intercept-and-abort (`BLOCKED_BY_CLIENT` before banking), matching the
  competitor's published design. — [MEASURED 09-21] `purchase_executor.py:3096-3100`
- **An empty or stale bank does NOT block a shot** — the ATC POST fires page-signed.
  Every branch ends in `continue_request`, never `fail_request`. True whether
  `TARGET_SHAPE_HARVEST` is 0 or 1. —
  [VERIFIED 09-21, adversarial, fresh context] `purchase_executor.py:2472-2482,3042-3052`
- **No code path abandons a purchase attempt over bank state.** Both bank gates
  (`TARGET_SHOT_BANK_GATE=1`, `TARGET_ATC_DCO_BURST_REQUIRE_BANK=1`) are bounded
  (≤8 s, ≤6 s), fail open on error, and apply **only to re-POSTs — never the first
  shot of a window.** Window-ending is driven by an independent ~110 s retry clock
  (`TARGET_RETRY_WHILE_IN_STOCK_MAX=40` armed), not by credential availability. —
  [VERIFIED 09-21] `bulletproof_purchase_manager.py:2543-2592,2402-2431,2091-2093`
- **There is exactly ONE live ATC dispatch mechanism**: in-page `fetch()` via
  `tab.evaluate`, intercepted by CDP `Fetch.requestPaused`. `cookie_harvester.py`
  is genuinely dead — not imported anywhere. —
  [VERIFIED 09-21] `purchase_executor.py:2111`

## ⚠️ ONE ACCOUNT PER TCIN — superseded (2 from 09-21 evening, 3 from 09-23)

**As of 09-23 the cap is 3 (A2 above), so this section is history of how the cap
was reasoned about.** Its arithmetic still holds: `_limit` is always
`WORKERS_PER_TCIN` under `CAP_ALWAYS=1`.

`bulletproof_purchase_manager.py:2771`:
`_limit = per_tcin if (_others or cap_always) else len(ready_workers)`

With `TARGET_MULTI_SKU_DISPATCH=1` **and `TARGET_MULTI_SKU_CAP_ALWAYS=1`** (both
armed, `bat:768,780`), `cap_always` is always true, so `_limit` is always
`TARGET_MULTI_SKU_WORKERS_PER_TCIN` — **even when one TCIN is live and the
other accounts sit idle.** That value was 1 until 09-21 evening; it is now **2**.

**Correction to this section's own reasoning:** the `[RACE]` line prints
`len(dispatch_workers)`, i.e. the **true post-cap dispatched count**, so a capped
night prints `racing 1 accounts` — the number was never stale. The surviving,
weaker version: a skimmer grepping for the string `[RACE]` without reading N
would miss the cap. — [VERIFIED 09-21, independent trace]

**"Dispatched" is still not "fired":** the AC-1 ambiguous-commit latch
(`TARGET_AMBIGUOUS_COMMIT_LATCH=1`, 1800 s, per identity x TCIN) can make a
reserved worker sit out with zero shots while `[RACE]` still counts it. It has
**never fired**, though: 0 `AMBIGUOUS_COMMIT` lines and 0 sit-outs across all 114
run logs, and no `state/ambiguous_commit_latch.json` exists. Theoretical, like
the 403 handler. — [MEASURED 09-21]

Combined with wave-first (55-70 s between shots), the shot budget for a 60 s hype
window was **~1-2 shots per identity** — measured on 09-23: 4 per account per
~2-min window, 37 for the whole night (C-0923-01). The competitor's published floor
is 10 tasks at a 3.5 s retry (~170 shots a minute); their local-Windows ceiling is
30. With A1 + A2 armed on 09-23 the budget becomes ~3 accounts x ~20-25 shots per
60 s — still ~a third of the competitor's starter floor, because we have 3 accounts,
not 10.

— [VERIFIED 09-21, triple-confirmed by three independent code reads]

**Before flipping `CAP_ALWAYS=0`:** it was armed 09-20 because without it "the
first TCIN grabbed the whole fleet and dispatch was a no-op." Reading the
expression, `cap_always=0` should cap to 1-per-TCIN only when *other* TCINs are
live and otherwise race all ready workers. **Verify against `:2759-2781` before
assuming.** Pre-register a readout asserting `[RACE] <tcin>: racing N accounts`
shows `N=3` on a single-TCIN night.

## Other production facts easy to get wrong

- `RESILIENT_REDSKY_CHANNEL=apps_raw` is the live stock-read channel — mobile-app
  RedSky via raw HTTP through each session's forwarder, **not** the in-page web
  fetch. The web path is walled by HUMAN/PX on the BD prefixes; apps_raw reads 200
  through the same exits. — [MEASURED 09-21]
- `app.py:51-65` calls `os.environ.setdefault` for 5 flags at import time, ahead of
  every other module: `TARGET_API_PLACE_ORDER='true'`, `TARGET_API_CART_CLEAR`,
  `USE_RESILIENT_STACK=1`, `TARGET_SWEEPS_PER_SEC=3.0`, `CHROME_STAGGER_TOTAL_S`.
  **Reading a `.get(name, default)` elsewhere in the tree is misleading** — the
  fast lane is armed by `app.py` alone, with zero wrapper involvement. —
  [MEASURED 09-21]
- Dead in production: `TARGET_ATC_401_LADDER=0` makes `:4727-4864` unreachable;
  `TARGET_ATC_NATIVE_FALLBACK` code-defaults `'1'` but the wrapper forces `0` —
  dead two independent ways. `TARGET_401_PULSE=1` is armed but **inert** under
  wave-first. — [MEASURED 09-21]
- Unverified lead: `_proceed_to_checkout`, `_proceed_to_checkout_direct` and
  `_find_checkout_button` in `purchase_executor.py` may have no callers. Spot-check
  before relying on it. — [REPORTED 09-21]

## ⚠️ 403 HAS NO HANDLER

`purchase_executor.py:4624-4648` prints `[SHAPE_BLOCK]`/`[PX_BLOCK]` and falls
through to a bare `else`. It returns `atc_failed_api_mode` **with no `gate_kind`
key**, so `_gk` is `''` — matching neither `auth401`, `edge` nor `dco`. A Shape or
PerimeterX block therefore gets the plain 2.5-3.5 s cadence instead of wave-first,
**and silently resets the 401 streak**
(`bulletproof_purchase_manager.py:2472-2473`). — [MEASURED 09-21]

## Hype-SKU handling — WE HAVE NONE

- **No branch anywhere in the purchase path treats a hot/hype SKU differently for
  credential purposes.** `gate_kind` is derived from Target's *response* at shot
  time (`'dco'`/`'edge'`/`'auth401'`), never from a TCIN classification.
  `config/product_config.json` carries **no** `hot` / `hype` / `demand_tier` /
  `sku_tier` key at all. The code treats hot and ordinary SKUs identically. —
  [VERIFIED 09-21, negative result] `purchase_executor.py:4705,4724`
- The competitor's equivalent (`Hype Product`) is a **mandatory** toggle for every
  Shape-protected drop and the first of their "rules that never change". What it
  does internally is not documented anywhere in their 43-page corpus. —
  [REPORTED 09-21]
- ⚠️ **Consequence for our own analysis:** since no hot/hype tag exists in config,
  any hot-vs-ordinary split in this project's analyses is derived elsewhere,
  possibly retroactively from a current list. If so, era comparisons that rely on
  it are corrupted. Under verification as of 09-21 — resolve before trusting any
  hot-vs-ordinary number.
- Login and customer-info/profile writes never consume a banked credential — they
  are outside the CDP `Fetch.enable` pattern scope. —
  [MEASURED 09-21] `purchase_executor.py:2490-2506`

## Observability gaps — known blind spots

- **`[ATC_RESP]` log lines carry no account, TCIN, request-id or thread-id**, so a
  shot cannot be joined to its credential state or its SKU class. Any such join is
  positional across 3 interleaved accounts and is unreliable. —
  [MEASURED 09-21]
- Credential markers (`REPLAY on main shot` / `bank STALE` / `bank EMPTY`,
  `purchase_executor.py:3048/3051/3077`) appear on ~1.2% of shots. —
  [MEASURED 09-21]
- ✅ **RESOLVED 09-21 — the "zero REPLAY markers on 09-21" scare was NOT a bug.**
  That day had **zero stock events, zero purchase attempts, zero `[RACE]`
  dispatches** (`in_stock=True` count = 0). No shots existed to replay onto, so
  zero markers is correct output. Do not re-open this. — [MEASURED 09-21]
- 🔴 **`[ATC_RESP]` IS NOT A SHOT COUNTER — 97.4% of it is harvester/warmup traffic.**
  By tab label, measured over two full nights from the raw tees:
  09-18 = **138 `main` vs 2,867 `warmup`**; 09-21 = **0 `main` vs 2,277 `warmup`**;
  harvest = 0 (diverted to `Fetch.failRequest` before any response).
  **Combined `main` share: 138/5,282 = 2.6%.** The rest is the warmup heartbeat
  (`_background_refill_loop`, every 60-90 s per tab, forever), the boot selftest,
  and the write-auth re-probe — all firing `_WARMUP_DUMMY_POST_JS` at the not-launched
  decoy TCIN `81926151` (`purchase_executor.py:40`; `21516452` / `50225561` / `53274278` are
  the harvester's click targets, cancelled inside the browser). **None are purchase attempts.**
  Any past or future shot-volume number derived from `[ATC_RESP]` is inflated up
  to ~40x, and the inflation scales with harvester activity, not with stock.
  — [MEASURED 09-21]
- **The label ALREADY EXISTS — it is just dropped on the way to `package.log`.**
  `purchase_executor.py:2276` prints `[INTERCEPTOR:{label}] [ATC_RESP] ...`;
  `:2277` logs the same line via `logger.info()` **without the label**. Only the
  logger line reaches `package.log`. Adding `label` (+ selftest flag + TCIN) to
  the logger call is a one-line-scope fix, not new plumbing. — [MEASURED 09-21]

## 🔴 `package.log` IS BLIND TO PRINT-ONLY MARKERS

`[RACE]`, `[FAST_LANE]`, `[MULTI_SKU_DISPATCH]` and `[PURCHASE_TRIGGER]` are
emitted with a bare `print()` (`bulletproof_purchase_manager.py:2792`, `:3664`),
never through `logging`. They reach **only** the per-boot raw tee
`logs/runs/run_<boot-ts>.log` (wired by `app.py`'s `sys.stdout = _Tee(...)`).

`package.log` shows 0 `[RACE]` lines for 09-18; the correct file for that boot
(`run_20260917_232400.log`) has **143**, and `logs/purchases/` holds 6 per-attempt
files (all TCIN 1012644666, 04:03-04:20). Dispatch happened.

**Rule: never use `package.log` alone to judge dispatch, race or shot activity.**
Use the per-boot `run_*.log` tees, cross-checked against `logs/purchases/`.
An earlier "13 in_stock, 0 races" alarm was entirely this artefact. — [MEASURED 09-21]

## The dispatch cap — observed live exactly once (09-23, 2-wide)

- 09-18 raw tee: **36 races, every one `[RACE] ...: racing 3 accounts`.** Zero
  `MULTI_SKU_DISPATCH` reservation lines — the feature was not armed that night.
- `TARGET_MULTI_SKU_CAP_ALWAYS` first appears in `679275d9` (the 09-20 work,
  committed 09-21). The 1-wide cap (09-21 night) never met stock.
- **09-23 is the only night the cap met stock: 9/9 races `racing 2 accounts`
  (W1/primary + W2/business, "fleet idle"), alt-1 0 shots, 0 `[MULTI_SKU_MISS]`,
  1 edge pass in 37 shots.** One night, all hot, n too small to say anything about
  the cap's effect on pass rate. The cap is 3 from 09-23 (A2). — [MEASURED 09-23]
- All other historical shot-volume data, including the "0.038 admits at 9-16 shots
  vs 0.545 at 2 shots" measurement that justified the cap, comes from the 3-wide
  era. — [MEASURED 09-21]
- 🔴 **That justifying measurement is now `[NOT ESTABLISHED]`** — re-verified
  adversarially from a fresh context on 09-21 evening:
  - It reproduces **only** under an undisclosed `--hot-only` + logs-since-08-25
    scope of `tools/analysis/throughput_vs_volume.py`. Run as the four code
    comments citing it imply, it is **0.486 vs 0.178** (2.7x, not 14x).
  - The two decisive cells are **11 windows / 6 passes** and **28 / 1**.
  - **Reverse causality is code-proven.** `bulletproof_purchase_manager.py:2233`
    breaks the retry loop on a *successful* shot, so low-shot windows are partly
    windows that resolved early. "Kept failing, so more shots accumulated" fully
    explains the correlation with no contribution from our own volume.
  - **Non-monotonic**: the 17+ bucket rebounds to **1.000** admits/window — the
    opposite of a depleting shared bucket, visible in the cited table itself.
  - **No window in the corpus ever fired 1 account** (33/33 and 32/32 identified
    high-volume windows were all 3 identities). The comparison condition the cap
    was armed on **has zero instances in the data.**
  - The headline "9x" matches neither cited cell (0.545/0.038 = 14.3x); it
    appears welded on from `limiter_key.py`'s separate 9.6-to-1.1% table.
  — [VERIFIED 09-21, adversarial, fresh context]
- **Armed 09-21 evening for the 09-22 03:00 drop:
  `TARGET_MULTI_SKU_WORKERS_PER_TCIN` 1 to 2** (`bat:797`). `CAP_ALWAYS` stays
  **1**: setting it to 0 does NOT mean "3 accounts on one TCIN" — traced through
  Gate A, the first live TCIN takes all 3 workers and every other live TCIN gets
  0 and prints `[MULTI_SKU_MISS]`, the bug CAP_ALWAYS=1 was armed to fix.
  `WORKERS_PER_TCIN` (clamped 1-8) is the actual dial. Readout pre-registered at
  `tools/analysis/readout_2026_09_22.py` (T2). — [MEASURED 09-21]
- **403 has never occurred.** `SHAPE_BLOCK` and `PX_BLOCK` markers: **0 across
  every run log in the repo's history.** The missing 403 handler (below) is a
  theoretical gap, not a live one — do not spend effort on it. — [MEASURED 09-21]
- 09-18 had 13 `in_stock=True` events and **zero `[RACE]` lines.** Unexplained;
  under investigation. — [MEASURED 09-21]
- `logs/runs/package.log` spans only 09-18 → 09-21. Full history is in 105 per-run
  logs under `logs/`. Grepping package.log alone silently truncates history to
  4 days. — [MEASURED 09-21]
- 429 had no handler anywhere in the sweep until `551b1807` (09-21), which split it
  out log-only. No backoff was built, deliberately. — [MEASURED 09-21]

## Fleet / config

- 3 accounts: `primary`, `business`, `alt-1`. All enabled, none in the harvest skip
  list. — [MEASURED 09-21] `config/target_accounts.json`
- All 3 buyers moved to the home IP in `679275d9` (09-20, pushed 09-21).
  **UNPROVEN LIVE.** — [MEASURED 09-21] git
- Monitor runs **18 active + 2 reserve** Bright Data ISP exits (restored from 8 on
  09-21 evening); **13 of the 18 active share one /16** — the 09-22 cascade subnet.
  All 20 validated HEALTHY 09-22 22:15. — [MEASURED 09-22] `config/proxyIps.json`
- BD exit registry: 2 of 5 in AS20012 (Chiller City Corp, a colo) **entered service
  2026-08-07** — one day before the zero-cart window opens; 2 of 5 are broker-leased
  `/17` space (Wookra LLC, netname `US-ISP`); 1 of 5 is unambiguous ISP space. —
  [REGISTRY 09-20] `logs/analysis_2026_09_20/research/BRIGHTDATA.md`
- ⚠️ Confound the source flags and downstream docs drop: the home IP is **also** the
  lowest-latency identity on every night measured, so exit reputation and latency
  cannot be separated in existing data. — [MEASURED 09-16]

## Where hot SKUs actually die — SETTLED 09-21

Of the ~50 hot shots that passed the edge limiter and never became a cart:
**45 (90%) `429 DCO_RATE_LIMITED`** ("Request throttled due to high demand item"),
2 `424 INVENTORY_UNAVAILABLE` (`CARTS.FULFILLMENT_AGGREGATOR`), 2 `431`, 1 `503`.
**0 of 50 were identity, credential or anti-bot rejections.** Across all 105 run
logs the entire `tgt-cart-error-key` vocabulary is retail-service keys — there is
no challenge/captcha/bot-detection vocabulary in this bot's history, ever.
**A credential fix cannot move this gate.** It is Target's cart-service demand
throttle. — [VERIFIED 09-21, exact reproduction of the funnel counters]

- The two `431`s are **self-inflicted**: the Shape token bundle is ~7,950-8,076 of
  the request's ~13,900-14,050 header bytes, tripping a generic header-size limit.
  Shape-adjacent, but not a Shape verdict. — [MEASURED 09-21]
- **The machine, end to end:** demand throttle rejects -> DCO burst re-POSTs at
  1.0-1.5 s -> the *edge* limiter closes (09-18: 6 of 7 bursts ended on an
  edge-429, `caps=0`, the burst cap was never once reached). The two throttles
  alternate and we lose to both. Raising `TARGET_ATC_DCO_BURST_MAX` is therefore
  inert. — [MEASURED 09-21]

## ⚠️ THE FUNNEL'S ORDINARY-SIDE NUMBERS ARE WRONG — hot side survives

Recomputed with the classifier defect corrected (the 3 Mega Evolution tins +
One Piece moved to hot):

| | as published | corrected |
|---|---|---|
| HOT g1_pass | 55 | 60 |
| HOT cart conversion | 9.1% (5/55) | 8.3% (5/60) |
| ORD g1_pass | 67/2,331 = **2.9%** | 62/204 = **30.4%** |

The defect is **undercoverage, not contamination** — real hyped TCINs land in
"ordinary"; no ordinary TCIN is ever wrongly tagged hot. So every hot-side number
survives. **The ordinary side does not:** the widely-quoted "hot takes only a 1.7x
penalty at the edge limiter" inverts under correction into a **>26x gap**, so it
must not be used even directionally. — [VERIFIED 09-21]

- **The "5 or 6 hot carts" contradiction is resolved: it is at least 7.** Two
  legacy-route hot carts (07-16 TCIN 1012055696, 07-23 TCIN 1011209279) are
  silently dropped by `shots.py:119-120` (an outcome line with no ident tag and no
  pending fast-lane shot is discarded). Both are WIN-era. Conversion is still 0
  under every count. — [MEASURED 09-21]
- Wins vs losses: all 3 identity-tagged wins are `primary` (home IP); credential
  source is **not** a discriminator (banked replay markers on all 3 wins and on
  14/21 losses). — [MEASURED 09-21]

## Open contradictions — do not assert either side

- **What a Target ATC 401 means — two kinds, now separable.** `401 ERR_UNAUTHORIZED`
  (key present) = a missing/deleted member token (09-25, 09-28). The KEYLESS 401
  (`_ERR_AUTH_DENIED`, 231-byte body, `x-ssx-hop=1`, no envoy) is produced at the SSX
  hop after the limiter admitted the shot — consistent with Refract's "a Shape block"
  (C-0930-03, headers + latency, 09-30). Which vendor stamps `x-ssx-hop`: NOT ESTABLISHED
  (no public source names it).
- **Limiter vs 401 order — RESOLVED 09-30 (C-0930-03): the limiter answers first.**
  `docs/FAILURES.md` 08-28 (limiter-first) was right; its 09-17 "401-first" correction
  and `docs/HOT_SKU_FIX_2026_09_16.md` L5 were wrong (corrected at source 09-30).
- **How many hot carts have ever been won — 5 or 6.** Three of this project's own
  analysis artifacts disagree. Conversion is 0 under every count.
- **Retry cadence**: wave-first ~60 s (ours until 09-23) vs "keep submitting" ~3.5 s
  (theirs). Re-derived hot-only at matched window age on 09-23 from a fresh context:
  **NOT ESTABLISHED in either direction** (C-0923-02 — p=0.33-0.40 under every
  specification; one night is 83% of the sample and flips the sign). The census
  that armed wave-first for the 429 lottery pooled every SKU and does not settle it
  for hot SKUs either. **Tested live 09-25 and KILLED:** at a 2.7 s cadence 0 of 663
  re-shots (window age 2-120 s) were admitted; all 5 admissions were first-volley shots
  (C-0925-01; over six nights first shots 15/142 vs every other shot 14/4,013, C-0925-07).
  Retries bought nothing at either cadence tried; whether they COST anything is NOT
  ESTABLISHED (C-0925-08: inseparable from the TCIN's restock state).

## 2026-09-23 RUN — 5 hot restock windows, 37 shots, all 429, 0 carts

`run_20260922_232734.log`, 23:27 → 10:27 (10.9 h), same 10 hot TCINs, qty 2.
**0-for-N, not 0-for-zero.** [MEASURED 09-23; claims C-0923-01..07 in `docs/CLAIMS.md`]

| window | TCIN | in stock (monitor) | shots | outcome |
|---|---|---|---|---|
| 02:47:39 | 1010892076 (30th ETB) | ~101-131 s, 3 edges | 8 | 8 edge 429 |
| 03:37:10 | 1010892076 | ~101-124 s, 4 edges | 8 | 8 edge 429 |
| 04:03:35 | 1010892067 | ~107-137 s, 3 edges | 8 | 8 edge 429 |
| 04:41:05 | 1010892069 | ~108-138 s, 3 edges | 9 | 8 edge 429 + 1 FAST_SELLING (primary, first shot) |
| 05:16:29 | 1010892078 | ~42-54 s, **7 flickers** | 4 | 4 edge 429 |

- **Bot mechanics were fine**: detection to first POST ≤0.08-0.98 s in all 9 races;
  ready sessions ≥17/18; no 401, no 403, no challenge; qty 2 by pin on every race.
- **We bought very few tickets**: 2 of 3 accounts (alt-1 capped out), 4 shots per
  account per window (wave-first), nothing fired 3-55 s into any window, and 8 of
  37 shots went out after the monitor had already read the TCIN out of stock.
  Flicker edges during a race are ignored by design (the TCIN is `'attempting'`),
  so window 5's 7 edges got 1 race. → A1/A2/A3/G1 above.
- **Target admitted 1 of 37 past the edge** — ordinary variance against hot-SKU
  history (C-0923-06), and the one that got through was throttled by the cart
  service. The first shot of each window (after 24-47 min of zero fleet shots on
  that TCIN) was edge-rejected 9 of 10 times, with both accounts' simultaneous
  shots (0-5 ms apart) failing together 4 of 5 times: not what an idle-refilled
  per-account bucket predicts. Account vs IP cannot be separated (all buyers share
  the home IP). [MEASURED 09-23]

## 2026-09-22 RUN — NO RESTOCK OCCURRED. The bot did not fail.

`run_20260921_225642.log`, 22:56 → 07:50 (8.84 h), 10 TCINs, 18 exits.
**Zero `in_stock=True` events. 0 shots, 0 carts, 0 orders — 0-for-ZERO, not 0-for-N.**
The purchase chain was never exercised. [MEASURED 09-22]

**The monitor was healthy and was never blind.** In the operator's 02:00-05:00
window: **32,132 sweeps, 200=32,125 (99.98%), 403=0, 429=0, other=6.** All 208
ground-truth reads returned exactly `(10 TCINs)`; `state/tcin_visibility.json`
ends with `invisible: []` and all 10 `last_seen == updated_at`. The 09-21
43-minute 429 tarpit did **not** recur — `429=` read 0 on all 1,060 intervals.
[VERIFIED 09-22, fresh context]

- (Corrected 10-05 post-run: the 09-22 line "zero-restock nights are the NORM, 6 of the
  last 9" no longer holds — since 09-15, 7 of 10 overnight runs before 10-05 had ≥1 stock
  window (event store `runs` / `windows`); 10-05 was the first zero-window night since
  09-29. [MEASURED 10-05; the target list changed over that span])
- Whole-run loss 302/93,608 = 0.32%; **excluding the 01:01-01:17 dip it is
  45/82,888 = 0.054%**, better than the 8-exit baseline of 0.07%. The 18-exit
  restore is vindicated on steady-state grounds. [MEASURED 09-22]
- **Detection has no debounce.** `_ingest_bulk_response` fires `on_in_stock` the
  same cycle on any False→True transition; `TARGET_STOCK_HYST_S=20` is pure
  bookkeeping and never touches `in_stock`. 10 TCINs = 1 chunk (cap 28), so every
  sweep reads all 10 — full-catalog refresh every **~0.336 s**. A 5-30 s flip was
  essentially certain to be caught **outside a RedSky 206 burst** — inside one (09-25) the
  read age reached 25 s, p90 ~8 s (C-0925-03). [VERIFIED 09-22, code-traced; qualified 09-25]

## ⚠️ 09-22 CASCADE — a Bright Data `/16` event. Recorded; deliberately NOT fixed.

**It was one subnet, not the fleet.** All 13 affected sessions are pinned to
`31.105.0.0/16`; the other 5 (`168.158.x.x` ×4, `72.56.x.x` ×1) logged **0-1**
failures the whole time — a clean 100% partition. Not host pressure, not age, not
Target: `403=0`, `429=0`, every failure `http_status=0`, i.e. connection-level.
True onset **01:01:25**, ~2.5 min *before* s13's heartbeat failure, which was
itself a symptom rather than the trigger. Usable floor was **7/18** (not 8), and
only **6** are needed to hold 3.0/s — `[STOCK][RATE]` read
`sweep 3.00/s (target 3.00/s)` continuously even at the floor. Worst bucket
71.9%. The subnet cascade fully drained by **01:10:25**; the 01:11 and 01:13
recycles were s17/s18 on *unaffected* subnets — ordinary churn, not this event.
252 of the run's 302 misses fall here. [VERIFIED 09-22]

**The 1-per-30 s watchdog pacing is DELIBERATE — do not raise it.** Its own
docstring (`multi_session_pool.py:594-597`): *"recycle ONE per tick (so we don't
burst-relaunch the entire pool if everything failed at once)"*. There is **no env
knob** — `WATCHDOG_INTERVAL_S`, `RECYCLE_COOLDOWN_S` and
`CONSECUTIVE_ERROR_RECYCLE_THRESHOLD` are hardcoded module constants. Raising it
would not even help: `_recycle_one` sleeps `random(5,15)` then takes ~8-30 s to
settle, so the **first** session back is ~20-50 s regardless of parallelism —
more per tick compresses the tail, not the floor. A burst of simultaneous fresh
Chrome launches is also exactly what the boot stagger exists to avoid showing
Shape. **Measured cost of this incident: indistinguishable from zero.**

🔴 **The real gap is that we cannot say WHY.** `log_per_request` is False in
production, so `BulkResult.error` (`stock_check_resilient.py:647-649`) is never
printed — **~300 `other` failures a night are completely opaque**, and this log
cannot distinguish "Bright Data blipped that /16" from "something dropped that
/16's connections". One line would settle it: log `result.error` the first time a
session's `consecutive_errors` crosses ~5. [MEASURED 09-22]

**Not the 07-23 wedge** (per-Chrome, age-correlated, needed a leak fix) **and not
the 09-21 tarpit** (pool-wide, 429-driven). Three different mechanisms on three
nights — do not merge them.

## ⚠️ STRUCTURAL BLIND SPOT: both channels share one pool

`STOCK_CANARY=0`, so the sweep and the ground-truth probe both draw from the same
18-session pool. A pool-wide cloaking event (a synchronized false-OOS served to
every exit) would be caught by nothing. No evidence it has ever occurred, and the
canary itself is known-broken (~100% rejected, raw urllib TLS). Naming it because
it is unmonitored, not because it is suspected. [MEASURED 09-22]

- Related correction: with `RESILIENT_REDSKY_CHANNEL=apps_raw` and
  `RESILIENT_RAW_CACHE_BUST` unset (default 0), the "cache-bust" ground-truth read
  **is not actually cache-busting** — both channels hit the same uncached endpoint.
  Its value is a second independent schedule, not cache evasion. Do not describe it
  as a cache-bust channel. [VERIFIED 09-22]

## REGIME WATCH — the tripwire

**Target is an adversary that changes without telling us. This section exists so a
change is caught in one run instead of six weeks.** `/post-run` compares every run
against these baselines before doing anything else.

Baselines to compare each run against (update these when a regime change is
confirmed, and log the change in `docs/TARGET_CHANGES.md`):

| Metric | Last-known-good | Current regime | As of |
|---|---|---|---|
| Ordinary-SKU cart rate @0-5s of stock edge | 15.6% (19/122) | **0.0%** (0/135) — no ordinary SKU armed since | 09-20 |
| Hot-SKU cart rate, all windows | 0.18% (2/1,106) | 0.06% (3/4,798); 09-23: 0/37; 09-25: 1/1,212 (1 cart in 8 windows); 09-30: 1/256 (1 cart in 12 windows); **10-01: 8/342 = 2.3% (3 of 6 windows carted) — RECOVERY, survives per-TCIN and leave-1010892076-out (6/74)** (C-1001-14); **10-02: 0/129** — not significant vs 10-01 (Fisher p=0.11) and inside the long-run 0.06%; SKU mix confounds it (tonight's 3 TCINs: 0/895 ever) | 10-02 |
| Hot-SKU edge pass, main shots, window age <150 s (pass = not an edge 429; 401 excluded) | 7.6% (20/264, all hot nights ex-08-27) | 09-23: 1/37 (2.7%), P=21.9% under the baseline — no change; 09-25: FIRST shots 5/20 vs re-shots 0/1,185 (the per-shot 0.41% is diluted by A1's re-shots; first-shot rate unchanged, p=0.33) — no change; 09-30: flip-race first shots 4/33, every other shot 0/219 — COMPOSITION, not a verdict: hot-list-only 1/123 at window age <150 s vs 7.6%, but 1010892076 (0/145 home first shots since 09-15, never carted) dominates; this baseline carries no TCIN mix, so compare per TCIN / leave-one-TCIN-out (C-0930-08 weakened, C-0930-09); 10-01: 12/49 = 24.5% hot-only (1010892076 alone 3/29 = 1.4x; the pooled 3.2x comes from the four small TCINs); 10-02: 1/37 = 2.7% (p=0.49 vs 7.6%) — back in the baseline range | 10-02 |
| Flip-race FIRST-shot edge pass, home IP (each account's first shot in a flip-opened race; 401 excluded) | 15/65 (23%), six restock nights to 09-25; every other home-IP shot 11/1,460 | same (C-0925-07); 09-30: 4/33 (12.1%; 2 × 401 + 1 × 503 excluded) pooled; hot-list only 1/30 (P=0.004 vs 15/65) but that is 1010892076-dominated; 95082118 3/3; every other shot 0/219. Regime verdict NOT ESTABLISHED until the baseline is restated per TCIN (fix spec FS-1); **10-01: 13/14 (92.9%; leave-1010892076-out 10/10; 1010892076 3/4 vs 0/20 on 09-30); every other shot 9/238 = 3.8% (raw-log count; event-store parser v2 gives 12/241 = 4.98% because it counts the 3 × 400 MAX_PURCHASE shots as past the limiter — quote either with its definition)** — RECOVERY (C-1001-14); **10-02: 1/9 (p=0.67 vs 15/65; p=0.0002 vs 10-01); every other shot 0/120** — back inside the pre-10-01 range, 10-01 was the departure (C-1002-F3) | 10-02 |
| Won hot cart → order | none ever | 0/10 lifetime (07-14 → 09-30): 4 never reached place-order, 5 died on checkout FAST_SELLING; 09-30 alt-1 1010892067: 41 place-orders (38 FAST_SELLING, 3 RESERVATION_FAILURE incl. the in-chain one), then RETIRED BY OUR CAPS with the line proven present and the TCIN in stock (C-0930-02). A first conversion is a RECOVERY signal; **10-01: FIRST CONVERSION — 1/8 (primary 1011960739, in-chain first shot, C-1001-01) → 1/18 lifetime**; the other 7 ended by OUR code while live, 0/199 tickets (C-1001-02); 10-02: no carts | 10-02 |
| Orders per drop night | 4-9 (07-24, 07-31, 08-04) | **0** since 08-04 (09-23, 09-25 and 09-30 had stock: 0; the 09-28 day run and both 09-29 runs had no stock); **10-01: 1 (2 units)**; 10-02: 0 (first edge lost to a host crash); 10-05: 0 — no stock event at all (nothing went on sale) | 10-05 |
| Monitor sweep loss, steady state | 0.07% (8 IPs) | 09-22: 0.054% (45/82,888, 18 exits, excl. the /16 cascade); 09-23: 0.095% whole run (111/117,429), ≈0.058% excl. a diffuse 90 s cluster at 08:16; **09-25: 11.8% (8,969/75,799) — 17 RedSky 206 bursts 02:10-04:49 (REGIME CHANGE, C-0925-03); 0.14% before 02:10, 0.073% after 06:25**; 09-28 day run: 0.048% (27/56,029), no burst; 09-30: 0.54% whole run (435/81,040) = 7.7x this row's 8-IP baseline; RESTOCK HOURS like-for-like: 02:00-04:59 1.20% vs 0.040% the same hours on 09-29 — FLAG, from RedSky 206 bursts (C-0930-11); 10-01: 0.167% whole run (309/184,823, all 'other'); restock hours 02:00-04:59 0.180% vs 1.20% on 09-30 — the 206 burst did not recur; 92 of 309 lost on one exit; **10-05: 12.854% whole run (12,924/100,545) — a 206 STORM 02:00:18-03:34:28, 73.3% lost (12,396/16,904), 0×403/429, pool-wide except 72.56.171.184 (14.2%); leave-the-storm-out 0.654%, of which 08:25-09:30 = the four 168.158/16 exits at 29% (a BD subnet cluster); 00-01 + 04-07 pooled 0.144% = baseline** (C-1005-05) | 10-05 |
| Warmup-heartbeat `[ATC_RESP]` mix (decoy POSTs — exists on zero-stock nights too) | 424 `ITEM_NOT_READY_FOR_LAUNCH` 76.7% / 401 19.4% (n=3,005) | 09-23: 79.0% / 20.7% / 503 0.3% / 429 0 (n=2,623, all `tab=warmup`; the 7 503s all 08:36-08:38); 09-25: 424 73.3% / 401 26.7% (n=1,705) incl. a NEW key `401 ERR_UNAUTHORIZED` (141, all from 06:01:14 — writes sent without a member token after the mint outage); 09-28: 424 39.6% / `401 ERR_UNAUTHORIZED` 51.0% / keyless 401 9.5% (n=1,322) — ERR_UNAUTHORIZED 22-30% 16-18h (business dead from boot), 95-100% from 20h (every token gone); 09-30: 424 80.0% / keyless 401 20.0% (n=1,804 unique — the readout printed 3,608, a double count, C-0930-07), 0 ERR_UNAUTHORIZED; signed and unsigned decoys 401 at the same rate (C-0930-06); 10-01: 424 79.6% / keyless 401 20.4% (n=4,063), 0 ERR_UNAUTHORIZED — no change, and flat through the SSX switch (C-1001-12); 10-02 09:24 run 4.6% (5/109) → a fluke: **10-05: 424 80.0% / keyless 401 20.0% (n=2,215), 0 ERR_UNAUTHORIZED** — no change | 10-05 |
| ATC 401 rate, home IP | 2.8% | 2.8%; 09-23 main shots 0/37 (P=35% under 2.8%); 09-25 main shots 2/1,212 (both primary's keyless first shots); 09-30: 3/256 (1.2%), all keyless — each a shot that PASSED the limiter (C-0930-03); **10-01: 80/350 (22.9%)** — 70 on 1010892076 after its first 6 admits (the per-TCIN SSX switch, C-1001-12); leave-1010892076-out 10/82 (12.2%); 10-02: 0/129, not informative (only 1 shot passed the limiter) | 10-02 |
| ATC 401 rate, BD exits | 13-21% | 13-21% | 09-20 |
| Member-token mint success (the repair's mint) | 80/80 (09-17 → 09-25 03:54, all rung 2) | **0/62 from 09-25 06:01; 09-28: 0/253 (rung 1 404 x253, rung 2 0/12 on live sessions)** — the REPAIR rungs stay broken (both delete or bypass the live token). **RECOVERY 09-29 with the repair off (KEEPFRESH=0): the running bot re-minted 12 of 12 expiries (3 accounts x 4), jars on the 4 h grid, 0 ERR_UNAUTHORIZED** (C-0929-01); **10-05: held — jars at stop all MEMBER, minted in-bot (business 07:55:38, alt-1 07:56:20, primary 08:10:21), 0 ERR_UNAUTHORIZED, incl. primary with NO login-session all run** (C-1005-09) | 10-05 |
| RedSky HTTP 206 on the monitor | 1-3 a night (13 runs) | **102** on 09-25, in 17 bursts (C-0925-03); 09-28 day run: 21 isolated singles, each `complete=10/10` (partial errors in `store_positions` / `promotions`); 09-30 on the NEW `[STOCK][206]` marker (from 09-25): 411 partial reads, 382 of them 02:00-04:59 vs 10 in the same hours on 09-29 (the "1-3 a night" baseline is an older instrument; compare reads per hour on the same marker) (C-0930-11); 10-01: 194 partial reads (11.2/h), only 24 in 02:00-04:59 vs 382 on 09-30; 10-02: 86; **10-05: 12,644 partial reads (incl. 143 ground-truth), 12,560 in 02:00-04:59 — the largest storm yet; bodies complete=17/17 with store_positions errors (94/94 sampled)** (C-1005-03) | 10-05 |
| In-bot scripted re-login | ~0/25 historic | 09-25: 2/5; **09-28: 0/6** ("username did NOT advance"), each left the account a GUEST | 09-28 |

**Declare a regime change and open an investigation when:** a cart rate moves by
more than ~3x in either direction, a status-code distribution shifts materially, a
new error string appears, or a metric goes to exactly zero over a meaningful n.
**A metric going to zero is the highest-priority signal** — it is what happened on
08-06 and what was missed.

Also watch for the inverse: something starting to work again. A recovery is as
informative as a collapse and is just as easy to miss.

## FACT DECAY CLASSES

Not all facts rot at the same rate. Tag accordingly, and treat an expired fact as
`[NOT ESTABLISHED]` rather than as knowledge:

| Class | Half-life | Examples |
|---|---|---|
| **Adversary behaviour** | **~2 weeks** | pass rates, 401/429 rates, limiter behaviour, what Shape scores, which IPs work |
| **Our config / armed flags** | until the next commit to the wrapper | every `TARGET_*` value, account roster, IP pool |
| **Our code structure** | until the next refactor | call paths, which module owns what |
| **Retailer API shape** | ~months | endpoint names, the 30-TCIN RedSky cap, error-key semantics |
| **Architecture / method** | stable | intercept-and-abort, one-Chrome-per-account, the epistemics |

Anything in the top row older than a month is a hypothesis, not a fact — however
many documents repeat it.

## Competitor reference

- Refract's complete public doc corpus (43 pages, 6,040 lines) pulled 2026-09-21 to
  `logs/analysis_2026_09_21/research/refract_llms_full_2026_09_21.txt`.
  Refresh from `help.refractbot.com/llms-full.txt`. Target module ≈ L3062-4351.
  **Superseded 09-30 by the live pull** `logs/analysis_2026_09_30/research/refract_llms_full_2026_09_30.txt`
  (5,798 lines, 87 diff hunks; adds "submit order lottery", ~1% cart→order, ~90% carting,
  "5-account setups do not work", median 12 tasks, local Windows tops out ~30 tasks).
- Their bank IS a hard admission gate ("Waiting for Cookies (Product)"); ours is
  not. Their target is 3 credentials per **running task**. — [REPORTED 09-21]
- Their proxy guidance has **two regimes**: ≤10 tasks + ≤2 harvesters on a local
  machine → no proxies at all, home IP; above that → residential on tasks and
  especially harvesters, ISP on the monitor. Carrying one regime's advice without
  its condition has already misled this project once. — [REPORTED 09-21]

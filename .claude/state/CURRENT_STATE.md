# CURRENT STATE — the only place live facts belong

**As of: 2026-09-25 ~11:45 (first-gate investigation after the 09-25 post-run) · HEAD `16cec4ca` (the post-run arming) + the L3 flip log below · branch `feat_refract_arch_v1`**

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
- **Deliberately NOT done:** a 206 ingest (needs L1's shape first); a token keep-and-restore
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
  "already logged in ✅" — that pass only checks the cookie NAMES exist. The read-only
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

## 🔴 WE STOPPED LOOKING. Commit `a30bc294`, 2026-08-10.

**The single most decision-relevant fact in this file.** [VERIFIED 09-22, fresh
context, order attribution re-derived from primary logs]

All 20 orders came from **8 TCINs**. On **2026-08-10** — six days after the last
order — `a30bc294` ("sync product config/catalog to the 08-09 drop-ready TCIN
list") **wholesale-swapped `config/product_config.json` to a DISJOINT set**,
sharing zero TCINs with the order-producing list. **It has stayed swapped for 43+
days. 0 of 8 are in the config today — absent, not merely disabled.**

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
did not stop because the bot broke or because Target went quiet — as far as the
data can say, they stopped when we stopped watching the SKUs that produced them.

- ⚠️ **The Pitch Black family is demonstrably still restocking.** Sibling
  `1011483413` (Pitch Black Booster Box) was in stock as recently as **09-18**,
  while `1011483406` and `1011483414` — **7 of the 20 orders between them** — have
  been unwatched for 43 days. This is the cheapest available experiment: the only
  way to learn whether the order-producing SKUs still restock is to watch them.
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

- **What a Target ATC 401 means.** Competitor docs: "A Shape block." This project
  (07-10): the write-auth / member-token layer. One is wrong and it decides where
  credential effort goes. Never run.
- **Whether the 401 gate sits before or after the edge limiter.**
  `docs/FAILURES.md:767` (08-28) says limiter-first; `:773-778` (09-17) corrects to
  401-first. Unresolved.
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

- **Zero-restock nights are the NORM: 6 of the last 9 full runs had zero stock
  events.** Two in a row is the base rate, not a regression. [MEASURED 09-22]
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
| Hot-SKU cart rate, all windows | 0.18% (2/1,106) | 0.06% (3/4,798); 09-23: 0/37; 09-25: 1/1,212 (1 cart in 8 windows) | 09-25 |
| Hot-SKU edge pass, main shots, window age <150 s (pass = not an edge 429; 401 excluded) | 7.6% (20/264, all hot nights ex-08-27) | 09-23: 1/37 (2.7%), P=21.9% under the baseline — no change; 09-25: FIRST shots 5/20 vs re-shots 0/1,185 (the per-shot 0.41% is diluted by A1's re-shots; first-shot rate unchanged, p=0.33) — no change | 09-25 |
| Flip-race FIRST-shot edge pass, home IP (each account's first shot in a flip-opened race; 401 excluded) | 15/65 (23%), six restock nights to 09-25; every other home-IP shot 11/1,460 | same (C-0925-07) | 09-25 |
| Won hot cart → order | none ever | 0/9 lifetime (07-14 → 09-25): 4 never reached place-order, 5 died on checkout FAST_SELLING. A first conversion is a RECOVERY signal | 09-25 |
| Orders per drop night | 4-9 (07-24, 07-31, 08-04) | **0** since 08-04 (09-23 and 09-25 had stock: 0) | 09-25 |
| Monitor sweep loss, steady state | 0.07% (8 IPs) | 09-22: 0.054% (45/82,888, 18 exits, excl. the /16 cascade); 09-23: 0.095% whole run (111/117,429), ≈0.058% excl. a diffuse 90 s cluster at 08:16; **09-25: 11.8% (8,969/75,799) — 17 RedSky 206 bursts 02:10-04:49 (REGIME CHANGE, C-0925-03); 0.14% before 02:10, 0.073% after 06:25** | 09-25 |
| Warmup-heartbeat `[ATC_RESP]` mix (decoy POSTs — exists on zero-stock nights too) | 424 `ITEM_NOT_READY_FOR_LAUNCH` 76.7% / 401 19.4% (n=3,005) | 09-23: 79.0% / 20.7% / 503 0.3% / 429 0 (n=2,623, all `tab=warmup`; the 7 503s all 08:36-08:38); 09-25: 424 73.3% / 401 26.7% (n=1,705) incl. a NEW key `401 ERR_UNAUTHORIZED` (141, all from 06:01:14 — writes sent without a member token after the mint outage) | 09-25 |
| ATC 401 rate, home IP | 2.8% | 2.8%; 09-23 main shots 0/37 (P=35% under 2.8%); 09-25 main shots 2/1,212 (both primary's keyless first shots) | 09-25 |
| ATC 401 rate, BD exits | 13-21% | 13-21% | 09-20 |
| Member-token mint success (the repair's mint) | 80/80 (09-17 → 09-25 03:54) | **0/62 from 09-25 06:01, all 3 accounts** — REGIME CHANGE candidate, cause NOT ESTABLISHED (C-0925-04) | 09-25 |
| RedSky HTTP 206 on the monitor | 1-3 a night (13 runs) | **102** on 09-25, in 17 bursts (C-0925-03) | 09-25 |

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
- Their bank IS a hard admission gate ("Waiting for Cookies (Product)"); ours is
  not. Their target is 3 credentials per **running task**. — [REPORTED 09-21]
- Their proxy guidance has **two regimes**: ≤10 tasks + ≤2 harvesters on a local
  machine → no proxies at all, home IP; above that → residential on tasks and
  especially harvesters, ISP on the monitor. Carrying one regime's advice without
  its condition has already misled this project once. — [REPORTED 09-21]

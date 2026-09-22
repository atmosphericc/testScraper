# CURRENT STATE — the only place live facts belong

**As of: 2026-09-21 · HEAD `551b1807` · branch `feat_refract_arch_v1`**

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

## Outcomes

- **20 orders in the bot's entire history. All ordinary SKUs. Zero hot/hype orders,
  ever.** — [MEASURED 09-20] `logs/analysis_2026_09_20/winning_shape/SUMMARY.txt`
- **Zero orders of any kind since 2026-08-04.** — [MEASURED 09-20] same
- 78 carts won all-time, 58 lost, 20 converted. — [MEASURED 09-20] same

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

## ⚠️ KNOWN-BROKEN: the hot/ordinary classifier

`tools/analysis/funnel.py:34-36` defines `is_hot()` as a **static, hand-maintained
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

## ⚠️ ONE ACCOUNT PER TCIN — superseded 09-21 evening (now 2; see below)

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
window is **~1-2 shots from one identity**. The competitor's published floor is 10
tasks at a 3.5 s retry (~170 shots); their local-Windows ceiling is 30 (~510).
Roughly two orders of magnitude, and the dominant factor is a flag.

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
  and the write-auth re-probe — all firing `_WARMUP_DUMMY_POST_JS` at decoy TCINs
  (`21516452`, `50225561`, `53274278`). **None are purchase attempts.**
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

## The single-worker cap has NEVER been observed live

- 09-18 raw tee: **36 races, every one `[RACE] ...: racing 3 accounts`.** Zero
  `MULTI_SKU_DISPATCH` reservation lines — the feature was not armed that night.
- `TARGET_MULTI_SKU_CAP_ALWAYS` first appears in `679275d9` (the 09-20 work,
  committed 09-21). **There has been no drop since.**
- Therefore: **all historical shot-volume data, including the "0.038 admits at
  9-16 shots vs 0.545 at 2 shots" measurement that justified the cap, comes
  entirely from the 3-wide era.** The 1-wide regime it created is unproven and
  unobserved. — [MEASURED 09-21]
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
- Monitor runs 8 Bright Data ISP IPs (down from 16/20).
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
- **Retry cadence**: wave-first ~60 s (ours) vs "keep submitting" ~3.5 s (theirs).
  `docs/REFRACT_PARITY.md` §4 states our own throughput evidence is era-confounded:
  *"Our data cannot settle this in-era."*

## REGIME WATCH — the tripwire

**Target is an adversary that changes without telling us. This section exists so a
change is caught in one run instead of six weeks.** `/post-run` compares every run
against these baselines before doing anything else.

Baselines to compare each run against (update these when a regime change is
confirmed, and log the change in `docs/TARGET_CHANGES.md`):

| Metric | Last-known-good | Current regime | As of |
|---|---|---|---|
| Ordinary-SKU cart rate @0-5s of stock edge | 15.6% (19/122) | **0.0%** (0/135) | 09-20 |
| Hot-SKU cart rate, all windows | 0.18% (2/1,106) | 0.06% (3/4,798) | 09-20 |
| Orders per drop night | 4-9 (07-24, 07-31, 08-04) | **0** since 08-04 | 09-20 |
| Monitor sweep loss, steady state | 0.07% (8 IPs) | 0.07% | 09-21 |
| ATC 401 rate, home IP | 2.8% | 2.8% | 09-20 |
| ATC 401 rate, BD exits | 13-21% | 13-21% | 09-20 |

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

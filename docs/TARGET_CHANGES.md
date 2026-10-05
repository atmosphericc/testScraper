# TARGET_CHANGES — the adversary's changelog

**Target is a moving opponent. This file tracks what *they* changed, separately
from what *we* changed.** `docs/FAILURES.md` records our failures;
`docs/CLAIMS.md` records our beliefs. Neither records the opponent's moves, and
conflating "our fix stopped working" with "their defense changed" has cost this
project weeks.

## How to use this file

- **Append an entry whenever behaviour changes and our code did not.** A step
  change in pass rate, a new error string, a new status code, a challenge that
  did not exist before, a rate limit that behaves differently.
- **Every entry carries a date, the evidence, and a confidence tag.** Suspected is
  fine — say suspected. An undated suspicion is worthless; a dated one is a lead.
- **Record the date we NOTICED separately from the date it CHANGED.** The gap
  between those two numbers is the thing to drive down. It was 6 weeks once.
- When an entry is later explained or refuted, edit it in place and say so.

Entry format:

```
### YYYY-MM-DD — short name
- **Changed:** what their side started doing
- **Noticed:** date we spotted it, and the detection gap
- **Evidence:** numbers with n, and the file
- **Our response:** what we did, or "none"
- **Confidence:** [MEASURED] / [SUSPECTED] / [REFUTED]
```

---

## Entries

### 2026-08-06 — the conversion collapse
- **Changed:** unknown. Conversion went to zero across **both** SKU classes with no
  change to our code path.
- **Noticed:** 2026-09-20 via `tools/analysis/funnel.py` era split.
  **Detection gap: ~6 weeks.** This is the single most expensive miss in the
  project's history and the reason the regime watch in `/post-run` exists.
- **Evidence:** ordinary SKUs at the stock edge 19/122 (15.6%) before vs 0/135
  (0.0%) after; Fisher p = 3.2e-07. Whole LOSE era: 2,273 ordinary chains → 1 cart,
  0 orders. Last order of any kind: 2026-08-04. [MEASURED 09-20]
- **Candidate causes, none confirmed:**
  - Two of five Bright Data exits (AS20012, Chiller City Corp, a colo) **entered
    service 2026-08-07** — one day into the window. [REGISTRY 09-20]
  - The armed target list shifted toward hyped TCINs over the same period.
  - A Target-side scoring change we have no visibility into.
- **Our response:** buyers moved to the home IP (`679275d9`, 09-20). UNPROVEN LIVE.
- **Confidence:** [MEASURED] that it happened; [NOT ESTABLISHED] why.
- ⚠️ Under adversarial verification as of 09-21 — the open attack is whether the
  hot/ordinary classification is applied retroactively.

### 2026-09-07 — HUMAN/PerimeterX "Press & Hold" appears on top of Shape
- **Changed:** a second anti-bot vendor's challenge began appearing on the RedSky
  path, in addition to F5 Shape.
- **Noticed:** same day.
- **Evidence:** `docs/HUMAN_PX_DIAGNOSIS_2026_09_08.md`.
- **Our response:** captcha park; `TARGET_HARVEST_MISS_PROBE` + PX park armed.
- **Confidence:** [MEASURED]

### 2026-09-21 — pool-wide 429 tarpit on the monitor
- **Changed:** a 43-minute window (03:21-04:04) in which the entire IP pool took
  429s on the sweep.
- **Noticed:** next morning — and only because someone looked. **429 had no handler
  anywhere in the sweep**, so it fell into an `other` bucket and was invisible.
- **Evidence:** ~99% of that run's loss in one window. [MEASURED 09-21]
- **Our response:** `551b1807` split 429 out, log-only. No backoff, deliberately.
- **Confidence:** [MEASURED]

### 2026-09-25 — RedSky answers the monitor's bulk read with HTTP 206 in bursts
- **Changed:** from 02:10 to 04:49 CDT, 17 bursts of 1.5-18 min in which 40-95% of
  monitor reads failed (63.9% inside bursts, 8,563/13,402); whole-run loss 11.8%
  (8,969/75,799) vs 0.05-0.10% on 09-22/09-23. The ground-truth reads, same function
  and URL as the sweep, failed 94x with **HTTP 206** (1-3 a night on 13 earlier runs).
  Every exit subnet was hit (17 exits on two /16s at 63-68%, the lone third /16 at 29%).
  Target-side: the no-proxy canary saw 206 in June, and single-TCIN VERIFY reads got
  206 too, so it is not one bad TCIN in the batch. Clean after 04:50.
- **Noticed:** live, within ~1 min (02:11), by the session's log watcher. Detection
  gap ~1 min.
- **Cost:** zero tonight: all 8 stock windows fell in clean gaps. Inside a burst the
  read age reached 25 s (p90 ~8 s), so a burst over a stock flip would have cost seconds.
- **Evidence:** docs/CLAIMS.md C-0925-03; `logs/analysis_2026_09_25/`.
  [MEASURED for the ground-truth 206s; INFERRED that the sweep's `other` is 206 — the
  sweep does not log per-request status]
- **Our response:** the bot read every 206 body and threw it away, so its shape is
  unknown. `RESILIENT_206_LOG=1` (armed 2026-09-25) logs one summary a minute. An
  ingest is deliberately NOT built yet: today's parser reads missing fulfillment as out
  of stock, so a naive ingest could mark everything out of stock.
- **Confidence:** [MEASURED] that it happened; cause [NOT ESTABLISHED].
- **UPDATE 2026-10-01: the restock-hours burst did not recur.** On the `[STOCK][206]`
  marker, 02:00-04:59: 382 partial reads on 09-30 → 24 on the 10-01 overnight run;
  like-for-like monitor loss in that band 1.20% → 0.180% (58/32,134). The run still
  averaged 11.2 partial reads/h, mostly outside the band (170 of 194). [MEASURED, facts
  agent on the event store, `logs/analysis_2026_10_01/postrun/regime_flags.md`]
- **UPDATE 2026-10-02: no burst.** 86 partial reads (14.8/h); 59/62 lines complete;
  02:00-04:59 monitor loss 0.209% vs 0.180% on 10-01. [MEASURED, 10-02 post-run facts]

### 2026-09-25 — member-token re-mint stops working for every account
- **Changed:** the bot's token repair minted a fresh member token 5/5 times
  00:48-03:54. From 06:01 every attempt failed on all three accounts
  (`could NOT mint ... present=False`), including a session only 42 min old. The
  signature is new: 4,241 of 4,368 earlier failure lines were `present=True
  member=False` on one rotted account while the others minted fine. The narrowest
  window for the change is 03:54-06:01 (no attempt in between).
- **Noticed:** live, 06:01, by the log watcher. Gap ~0.
- **What it is not:** Target did not kill the sessions — its cart service kept
  accepting each token right up to the moment the BOT'S OWN repair deleted it (the
  repair deletes the token before a replacement exists). [REFUTED: "sessions killed"]
- **Evidence:** docs/CLAIMS.md C-0925-04; `logs/runs/run_20260925_004139.log`.
- **Our response:** see C-0925-04 (repair safety) — the bot turned a mint outage into
  dead accounts. Target's answer to the mint request itself has never been logged.
- **Confidence:** [MEASURED] that mints stopped; why [NOT ESTABLISHED] — a Target-side
  change to the mint path vs re-mint disabled for these accounts or this IP after the
  drop's volume. The deciding observation is the mint request's own response.
- **UPDATE 2026-09-28 (pre-drop): still broken 3+ days later, and it was rung 2 that
  changed.** All 80 successful mints 09-17 → 09-25 03:54:46 came from rung 2 (delete the
  accessToken + reload `/account`); rung 1 (`gsp.target.com/gsp/token_refresh`) last
  succeeded **09-16 17:25** — it was dead before 09-25, so its 404 is not this change.
  The 09-28 16:12-21:30 run (first with `TARGET_TOKEN_MINT_LOG=1`): rung 1 **253/253
  `status=404 type=cors`**; rung 2 on live login-sessions **0/12** (landed on `/account`,
  `accessToken=none`), 0 `minted via` lines all run. What still minted: the wrapper's
  `relogin_one.py all` validate pass — a fresh Chrome on the saved profile loading
  `/account` WITHOUT deleting anything — gave primary and alt-1 member tokens at
  16:11:47 / 16:12:14 (n=2; business, whose session was already dead, got a guest).
  A member token lives **4 h**. Detection gap for the persistence: 3 days (no run
  between 09-25 07:50 and 09-28 16:12). [MEASURED 09-28, antibot-analyst +
  orchestrator; `logs/runs/run_20260928_161223.log`, `logs/relogin.log`]
  Whether Target now mints only on a load that carries an (expired) token, or the
  difference is fresh Chrome vs a long-lived tab: [NOT ESTABLISHED].
- **UPDATE 2026-09-29 (pre-drop for 09-30): minting works inside the running bot as
  long as nothing deletes the token.** With `TARGET_TOKEN_KEEPFRESH=0` +
  `TARGET_RELOGIN_MAX_PER_6H=0` (armed 09-28), all three accounts were re-minted at
  each 4 h expiry in long-lived tabs: jars at 23:13 hold `sut=R` tokens iat 21:11:25 /
  21:12:44 / 21:14:29, on the 4 h grid of the 01:11-01:14 hand logins, with no login or
  restart after 09:11:46; 0 decoy `401 ERR_UNAUTHORIZED` across 12 expiries (CLAIMS
  C-0929-01). So "fresh Chrome vs long-lived tab" is not the difference. What remains
  consistent with every observation: **a load that still carries the (expired) token
  mints a member token; a load after the token was deleted does not** (rung 2 deletes
  first: 80/80 before 09-25 06:01, 0/12 on 09-28). If that is the 09-25 change, it is
  Target requiring the old token to refresh. [INFERRED; the mint request itself is
  still unlogged]

### 2026-10-01 — 1010892076's edge limiter stops shutting the home line out (RECOVERY)
- **Changed:** on 1010892076, shots from three-wide home-line races got past the
  edge limiter 58/230 (~25%) vs 1/195 the night before (by race 22/39 vs 1/35), and the
  TCIN carted for the first time ever (primary 02:19:51, business 02:20:00; it had 0 ×
  201 in 944 shots before). Pooled: flip-race first-shot edge pass 13/14 vs the 15/65
  baseline; hot-SKU cart rate 8/342 vs 0.06%. Our wrapper, config and armed code were
  unchanged between the two runs (the only commit, F1, is off and unset).
- **Noticed:** 2026-10-01 post-run (the morning after). Detection gap ~1 day.
- **Evidence:** docs/CLAIMS.md C-1001-11, C-1001-14; `run_20260930_233818.log`.
- **Our response:** none yet. FS-3 (log a non-reversible egress fingerprint at boot
  and hourly) specced, to remove the one confound left.
- **Confidence:** [MEASURED] that it happened; cause [NOT ESTABLISHED] — runtime state
  differed (logins, cookies, proxy_state), the home egress identity is unlogged, and 52
  of the 58 passes fell in one window. Compare per TCIN; the pooled rates are mostly
  this TCIN's own move.
- **UPDATE 2026-10-02: did not carry over, and the reason cannot be separated from SKU mix.**
  - `run_20261002_021547`: 129 home-line add-to-carts on 3 different TCINs → 128 edge 429
    + 1 FAST_SELLING, 0 × 401, 0 × 201.
  - Flip-race first shots 1/9 (vs 13/14, p=0.0002; vs 15/65, p=0.67). Every other shot 0/120
    (vs 12/241, p=0.010; vs 11/1,460, p=1.0).
  - 1010892076 did not restock. 1012644667, the one TCIN raced on both nights, went 3/3
    FAST_SELLING → 0/3 edge-limited (p=0.10).
  - No add-to-cart was fired between 10-01 04:53:30 and 10-02 02:19:38, so the change point
    cannot be located. Our request path was unchanged; our target list changed 10-02 01:03.
  - **Confidence:** [MEASURED] rates; a Target-side change [NOT ESTABLISHED]. Tonight is inside
    the pre-10-01 range; 10-01 remains the departure (docs/CLAIMS.md C-1002-F3).

### 2026-10-01 — a per-TCIN admit-then-401 switch at the SSX hop
- **Changed:** on 1010892076 the first 6 shots past the limiter were admitted (4
  FAST_SELLING + 2 × 201, 02:15:19-02:20:00); every one of the next 70 past-limiter
  shots, from all 3 accounts across 2 windows (02:22:06-03:40:33), was a keyless 401
  (`_ERR_AUTH_DENIED`, `x-ssx-hop=1`). The warmup decoys' 401 rate did not move (19.1%
  in the span vs 20.5% outside, p=0.57), so it is not an account- or line-wide trust
  collapse. On every TCIN that drew a 401 tonight, all cart-service responses came
  before its first 401 (19 before, 0 after).
- **Noticed:** 2026-10-01 post-run. Whether it is NEW is open: no earlier night had
  enough past-limiter shots on one TCIN to show it (research question: re-check every
  pre-10-01 home run that has `x-ssx-hop` logging).
- **Evidence:** docs/CLAIMS.md C-1001-12.
- **Our response:** none. FS-4 (`ssx_sequence.sql`, a saved readout of admits before
  and after a TCIN's first keyless 401) and FS-E (log-only per-TCIN SSX state) specced.
- **Confidence:** [MEASURED] for one night and, well tested, one TCIN; as a rule
  across nights, and whether our own volume triggers it, [NOT ESTABLISHED].

### 2026-10-01 — CORS preflight to cart_items answered 429 (new error)
- **Changed:** 42 `OPTIONS …/web_checkouts/v1/cart_items` responses were HTTP 429, in
  one span 03:38:51-03:46:09, on alt-1's Chrome (main 13, harvest 27, warmup 2). The
  13 main-tab ones turned into the run's only 13 status-0 `atc_threw` shots. alt-1
  logged 0 harvest captures in the span; primary and business 11 each. One other
  OPTIONS 429 exists in 118 run logs (07-13, checkout endpoint).
- **Noticed:** 2026-10-01 post-run. Detection gap ~1 day.
- **Evidence:** docs/CLAIMS.md C-1001-13.
- **Our response:** none. FS-1 (log-only `[PREFLIGHT]` line with ident + headers, and
  a `preflights` table in the event store) specced. The headers of these 429s are not
  logged, so whether the limiter or another layer answered is unknown.
- **Confidence:** [MEASURED]; cause and recurrence [NOT ESTABLISHED].
- **UPDATE 2026-10-02: did not recur.** 28 OPTIONS responses: 24 × 200, 4 × 403 at boot (routine),
  0 × 429.

### 2026-10-02 — decoy add-to-cart answered keyless 429 on all three accounts for ≤27 s at boot
- **Changed:** the boot SELFTEST's dummy POSTs (TCIN 81926151, normally 424) got 429 with no
  error key and no envoy time.
  - 5/5 sent between 02:16:31.597 and 02:16:37.889, on primary, alt-1 and business. They were
    bracketed by 424 at 02:16:28.731 and 02:16:55.765.
  - 0 of the run's other 1,364 decoys were 429.
  - Control POST: 429 on 3/3, vs 424 on 54/55 earlier account-boots (21 runs; one -1).
  - Warm-up 429s before tonight: 1 in 18,545.
  - alt-1's `[BOOT_CART_AUDIT]` cart read also got 429.
  - The boot was ~4.5 min after 1011407490's first edge, and was the restart after a host crash.
- **Noticed:** 10-02 post-run (~6.7 h).
- **Evidence:** `run_20261002_021547.log` L895-982; docs/CLAIMS.md C-1002-F4.
- **Our response:** none; warm-up 429 headers are not logged (INS-1002-WARM429 specced).
- **Confidence:** [MEASURED]. The cause, and whether this is a drop-time throttle on the home line,
  are [NOT ESTABLISHED]. Cost 0.

### 2026-10-02 09:24 — signed decoys passed the SSX hop 55/55 (WATCH, one short run)
- **Changed (suspected):** in the short daytime run `run_20261002_092404` (09:24-09:53, 0 shots,
  no stock), the warm-up decoy keyless 401 share fell to **5/109 (4.6%)**. Every earlier run is at
  18-20%: 10-02 02:15 20.2% (n=1,369), 10-02 01:12 18.1% (n=237), 10-01 17:01 19.2% (n=172, also
  a boot right after a host crash), 09-30 23:38 20.4% (n=4,063).
  - Split by signing: Shape-signed decoys (`shape_tokens=6`) **0/55** 401; unsigned 5/54. In the
    10-02 01:12 run, signed 22/142 and unsigned 21/95.
  - The 424 key is unchanged (`ITEM_NOT_READY_FOR_LAUNCH,NOT_FOUND`).
- **Noticed:** 2026-10-04 21:5x pre-drop, when the event store was rebuilt (that run was not
  in it). Gap ~2.5 d.
- **Evidence:** `tools/events/q.py regime --run run_20261002_092404`; `decoys` grouped by
  status × shape_tokens.
- **Our response:** none; pre-registered as R-DECOY in the 10-05 readout. If tonight's decoy
  401 share stays under ~10% over n ≥ 500, treat it as a Shape/SSX change and open an
  investigation. If it is back at 18-20%, this was a 30-min fluke.
- **Confidence:** [MEASURED] counts. A change is [SUSPECTED]: n=109, 29 minutes, one run.

---

## Known-unknowns about the opponent

Things we would need to see their side to answer. Listed so they are not
re-litigated from first principles every few weeks:

- Whether one client's own retry rate lowers its admission odds at the high-demand
  limiter (the wave-first premise). Our hot-SKU data cannot settle it either way
  (docs/CLAIMS.md C-0923-02, 2026-09-23). **Tested live on 09-25**
  (TARGET_WAVE_FIRST_EDGE=0, 2.7 s median re-shot gap): 0 of 663 re-shots at window
  age 2-120 s were admitted; all 5 admissions were window-opening shots fired within
  0.1 s of the flip (first shots 5/20 vs 1/10 on 09-23, p=0.33 — no change). So retries
  bought no admissions at either cadence tried; whether they COST anything is still
  unmeasured. The kill rule tripped and the flag went back to 1 (C-0925-01). 09-23 also showed
  9 of 10 window-opening shots rejected after 24-47 min of zero fleet shots on the
  TCIN -- not what an idle-refilled per-account bucket predicts.
- What fraction of requests the high-demand rate limiter admits. Their own vendor
  docs say nobody outside Target knows.
- Whether a Target ATC 401 is a Shape block or the write-auth layer. Unresolved,
  and it decides where credential effort goes.
- Whether the edge limiter scores IP reputation or simply favours lowest latency.
  Confounded in all existing data.
- Whether Bright Data ASNs are specifically scored by Target/F5. No public evidence
  in either direction.

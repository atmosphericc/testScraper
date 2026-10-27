# Refract parity — what they do, what we do, how far off

**Refreshed:** 2026-10-26 · **Branch:** `feat_refract_arch_v1`

> **Supersedes the 2026-09-20 version** (recoverable in git history). That version predated
> two things: (a) the `src/lanes/` build, which *is* Refract's decoupled architecture — so its
> "what to change" list is now partly already-built; and (b) Refract's own regime shift. Its
> "10-task floor / don't scale past your machine" framing is stale — see §1.

**Sources.**
- Refract vendor docs (help-center corpus, `[REPORTED]`): primary
  `logs/analysis_2026_09_21/research/refract_llms_full_2026_09_21.txt`; **newer snapshots that
  change the regime** `logs/analysis_2026_10_11/...`, `logs/analysis_2026_10_18/...`,
  `logs/analysis_2026_10_19/outside/...`. The Target module was rewritten between 09-21 and 10-11.
- Our architecture: `src/lanes/`, `src/monitoring/`, `src/session/`, `src/proxy/`, `app.py`,
  `run_bot_with_nightly_restart.bat`, `whatHappensNext.md`.
- Measured limiter data (`[MEASURED]`): `tools/analysis/{limiter_key,edge_pass_by_gap,shot_index_yield,throughput_vs_volume}.py`.
- Status tags follow `docs/CLAIMS.md`.

---

## 0. TL;DR — the gap in three lines

- **Architecture: close.** We have already built Refract's decoupled harvester → bank →
  browserless-worker model (`src/lanes/`). It is money-free validated but **OFF**
  (`TARGET_LANE_MODE=0`) pending a live-drop gate. Closing this is cheap and mostly done.
- **Scale: the gap is INDEPENDENT IPs, not accounts.** Refract runs 30–200+ tasks across *many
  independent residential IPs* — so their volume is many independent lottery tickets. OUR shots
  share **correlated fate on one egress IP** (C-1015-01: one-IP accounts share the limiter's fate),
  so adding accounts on the home IP is NOT more tickets — it is measured flat, and the **operator
  has vetoed creating accounts for this** (C-1023-26). The only transferable form of Refract's
  volume is **more independent egress IPs = the residential lanes** — and whether a resi IP is an
  independent ticket (resi `p` > / ⊥ home `p`) is the one unproven gate (§7).
- **The wall: nobody is ahead, and volume does not move it for us.** The High-Demand edge limiter's
  admit probability `p` is **set SITE-WIDE by Target per window**, collapsed to ~0 on the last 3
  drops, and is **not a function of our volume, accounts, or lines** (C-1023-26). 10-16 fired
  **1,208 shots → 0**; 10-01 took **1 order at *slow* cadence** because `p` was ~30% that window.
  Refract admits the same ("nothing we or you can do"). Mimicking topology does **not** manufacture
  admits on a p≈0 night — when `p>0` the current bot already carts.

---

## 1. What Refract is — the goal state (updated to the 10-18 regime)

**The core idea (in security terms): decouple the credential-minter from the buyer.**

- **Harvester** = a *small* pool of real Chromium browsers that start an ATC/login and
  **intercept-and-abort** — capturing the F5 Shape sensor the browser signs, then killing the
  request so nothing is bought. Captured credentials go into a shared **bank**. A device-attested
  token mint. (L371, L3463: *"The harvester never sends the actual add-to-cart… It intercepts that
  request and saves the cookie for later."*)
- **Task** = a *large* pool of **browserless request workers** that withdraw a banked cookie and
  spend it. (L386 task = *"essentially a worker"*.) The tell is L3510: *"for around 90–120 cookies
  you will probably need about 6 browsers"* — impossible if buyers were browsers.
- **The bank is the admission gate.** No cookie → `Waiting for Cookies` → the task never fires.
  Throughput is governed by credential inventory, not timers.
- **Proxy doctrine:** residential on harvesters/tasks (Shape scores the egress), ISP on the
  monitor; at small scale the home IP *is* residential, so a couple of local harvesters on home IP
  is acceptable. (L3194, L3208, L3173.)
- **Account model:** one task per account per product (L3192); aged accounts, a real Gmail each, a
  real card each — virtual cards get linked/cancelled (L3652-3658).
- **Shape is a score, not a gate** (L3466-3476): proxy trust + device trust + browser + cookie
  volume + the product's live security level. ~3 cookies per running task; more *raises* flag risk.

**The regime shift (10-18 — this is the one that matters now).** `[REPORTED]`
> Target has become a *"submit-order lottery"*: with the High-Demand (DCO) rate limit,
> **only ~1% of carts turn into an order** (N-1018:L3259), so you cart many tasks and spam submit
> order. Median running task count ≈ 12; cook-group winners run *"hundreds if not thousands of
> tasks."* Windows **30–50** tasks, Mac **200+**. The browser **extension is deprecated**
> (in-bot harvester only, N-1018:L3253). *"Plenty of users cart ~90% of their tasks on a drop…
> checking out is [the hard part]."*

This reverses the 09-21 advice (*"Target is not like Walmart where the answer is always more
tasks… scaling past your machine makes you hit less"*, L3439). **CONFLICT between snapshots, by
date — the 10-18 regime is current.** `[REPORTED]`

**On the wall, Refract is blunt that they can't beat it either** (L3615-3638):
> *"Rate Limit (High Demand)… there is nothing we or you can do about it. It is a site-wide traffic
> throttle."* 429 codes: `FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION`, `ERR_A2C_TCIN_RATE_LIMITED`.
> The move is volume + persistence: *"keep submitting the order and let it ride."*

**So Refract's winning formula today is not a bypass.** It is: decoupled architecture (one box runs
many browserless buyers) × many aged accounts on **many independent residential IPs**, each
spamming submit-order against a site-wide lottery where ~1% of carts convert. **They win on ticket
count, not on a key.**

**Why that does NOT straightforwardly transfer to us (the measured subtlety).** Our own data says
`p` is site-wide and not a function of our volume/accounts/lines, and that same-IP accounts share
one fate (C-1023-26, C-1015-01). The reconciliation: Refract's volume helps because each
residential exit is (plausibly) an **independent** draw on `p`; our accounts don't help because
they sit behind **one correlated egress** (home IP). So the lesson we take from Refract is **not
"run more accounts"** — it is **"run more independent egress IPs,"** which is exactly what the lane
system provides. Whether a resi exit really is an independent ticket is unproven (§7) and is the
single fact that decides whether the lanes are worth arming.

**Gaps in the corpus (do NOT infer these from Refract):** endpoints, headers, JA3/JA4, whether a
task is a browser or HTTP worker (stated nowhere — our "HTTP worker" read is `[INFERENCE]`), the
limiter's bucket key/quota, sticky-session policy, provider names, session warming, and any
HUMAN/PerimeterX or Akamai handling on Target (PX is attributed only to Walmart).

---

## 2. Our architecture today (live vs built-off)

Two firing systems run additively:

1. **LIVE:** browser-per-account purchase path. **3 accounts**, each a persistent Chrome on the
   **home IP**, Shape handled by CDP intercept-and-swap (`src/session/shape_harvest.py` →
   `purchase_executor.py` fast lane: ATC→pre_checkout→place_order as one in-browser `tab.evaluate`
   chain). This is essentially Refract's *starter tier* — but at 3 accounts we are below even their
   **old** floor of 10, and ~an order of magnitude below the 30–200+ current regime.
2. **BUILT, OFF (`TARGET_LANE_MODE=0`, uncommitted):** `src/lanes/` — **a faithful implementation
   of Refract's decoupled model.** `shape_harvester` (browser, intercept-and-abort, one guest
   Chrome per exit IP) → `ShapeTokenBank` (FIFO, TTL 300s) → `lane_shooter` (curl_cffi,
   browserless, **same exit IP** as the harvester) → `lane_checkout` (hands the 201 cart back to
   the account's Chrome). `LanePool` = one harvester per IP, one lane per (account×IP), one checkout
   per account. Validated money-free: 3/3 ATC → Shape **424** (accept) through residential.

**Proxy pools (`config/proxyIps.json`):** 10 Bright Data **ISP** → stock monitor (live);
2 BD ISP reserve + 4 IPRoyal **residential** → lanes (off); **home IP** → the 3 accounts' live
purchases. This is the "Refract split" (resi-shoot / ISP-monitor), implemented but not yet live.

---

## 3. Where we ALREADY match — do not spend effort here

| Mechanism | Refract | Us |
|---|---|---|
| Decouple minter from buyer | harvester → bank → browserless task | **built** in `src/lanes/` (off) |
| Intercept-and-abort harvest | *"never completes the action"* | `shape_harvest.py` + lane harvester |
| Newest-cookie-first replay | `Use Newest First` default | **production bank LIFO ✅; lane bank is FIFO (oldest-first) — a real bug to fix, see B2** |
| Real-Chrome JA3/JA4 minting | browser-signed sensors | `tab.evaluate(fetch)` / CDP swap |
| Resi-shoot / ISP-monitor split | documented doctrine | implemented in lanes (off) |
| Monitor cadence | 3500 ms | 3 RPS / ~0.5 per IP — **ours is hotter** |

Detection is **not** the bottleneck.

---

## 4. Where we're behind, ranked

- **B1 — Task/account count (the decisive gap, costs money).** Refract 30–200+; us 3 accounts,
  ≤12 lanes. In a ~1%-convert lottery, tickets = accounts × lanes. Constraint on file: **one TCG
  purchase per card**, so each new account needs its own card. We can realistically go 3→10+
  (their old floor); "hundreds" is off the table on our resources.
- **B2 — Credential supply (mostly NOT a depth gap — corrected 2026-10-26).** The production bank is
  already armed at **depth 6 > Refract's 3**, continuous (on-demand) refill, newest-first (LIFO).
  Raising depth further is *counter-indicated* — our own `-a0` bloat finding (bloated sets 0/14, trip
  the edge header cap) is the same warning as Refract's "more cookies backfires." The real constraint
  is **mint-rate vs. burst-spend**: one harvester mints ~0.25 tok/s while a hot burst of 3 lanes draws
  ~0.67–1.0/s, so a window can *starve*. Also, our bank is **not an admission gate** (unlike Refract) —
  an empty bank just sends a page-signed shot (fail-open). Two genuine, small items remain: (1) the
  **lane bank draws FIFO/oldest-first** — flip to newest-first to match production + Refract; (2) the
  Refract **"profile-write burns a cookie" concern does NOT apply to us** — our accounts are
  pre-provisioned members with a persistent address+card, and the one in-window address write is
  already suppressed (`TARGET_WARMUP_PAUSE_DURING_PURCHASE=1`). So B2 shrinks to a FIFO fix + an
  optional in-race mint-rate knob, both flag-gated, not a "deepen the bank" project.
- **B3 — Browserless buyer end-to-end.** Refract tasks are HTTP workers through checkout; our lane
  **ATC** is curl_cffi but **checkout re-enters the account's Chrome** (`lane_checkout.py`). Fine at
  our scale; a ceiling if we ever push task count up.
- **B4 — Cadence doctrine.** Refract: *"keep submitting / let it ride."* We sleep 55–70s
  (`TARGET_WAVE_*`) after a 401/429 before cold re-entry. See §5 — our own data can't settle this,
  so it must ship as an A/B, not a blind flip.

---

## 5. The measured limiter reality — why topology alone isn't enough `[MEASURED]`

**The edge limiter is a shared per-TCIN VOLUME bucket, not a per-identity cooldown.** Pass rate vs
prior shots on the same TCIN within 120s, by *other* identities:

| prior shots (other identities) | shots | pass% |
|---|---|---|
| 0 | 83 | 9.6% |
| 1–2 | 109 | 9.2% |
| 3–4 | 190 | 1.1% |
| 5–8 | 435 | 0.7% |
| 9+ | 2150 | 0.4% |

- **Cross-identity and cross-IP:** primary (home IP) and alt/business (BD exits) suppress each
  other — different IPs, same collapse. Same monotonic collapse on *uncontested* ordinary SKUs, so
  not hype-contest confounding.
- **Not a cooldown:** gap since the identity's own last shot barely matters.
- **Whether higher volume nets *more total* passes is era-confounded and NOT established** — the
  one "throughput rises with volume" bucket is all July/August windows on fresh IPs; September has
  none because wave-first removed them by design. `[NOT ESTABLISHED]`
- **Most recent real drop (10-23): 119/119 refused on BOTH home and residential.** `p≈0` that
  window, set site-wide. This is why mimicking topology buys nothing on a dead night.

Refract's documented answer ("keep submitting") is the better prior — they are the vendor that
wins — but it is a prior, not a proof, so B4 ships as an A/B.

---

## 6. Roadmap to mimic Refract with our resources (ordered)

**Tier 1 — close the architecture gap (cheap, mostly built):**
1. **Pass the lane gate, then arm the A/B.** Freeze stress test (15-min resi harvest), checkout-
   handoff validation on a resi cart, then `TARGET_LANE_MODE=1` labelled A/B with a kill rule.
   Turns 3 accounts × 4 resi IPs into up to **12 independent browserless shooters** — max tickets
   from the accounts we have.
2. **Credential supply (B2) — shrunk to two small, premise-independent items.** (a) Flip the lane
   `ShapeTokenBank.withdraw()` from FIFO to **newest-first** (`TARGET_LANE_BANK_NEWEST_FIRST`, default
   = current) so a hot burst replays the freshest token, matching production + Refract — the one clean
   "land it" candidate. (b) Optionally an **in-race mint-rate knob** (shorter harvest interval while a
   window is live) to fight the 0.25/s-mint vs ~1/s-burst starve, capped and keeping `prefer_no_a0`.
   Do **NOT** deepen the bank (armed at 6 already; more = flag risk). The profile-write pre-warm is
   mostly moot for our member accounts (see B2); a minimal `TARGET_PREWARM_PROFILE` is possible but
   carries real PX-profile and account-linking risk — gate it hard, run once/day, never looped.
3. ~~Narrow the fleet purchase lock to per-(account × TCIN).~~ **ALREADY DONE & ARMED** (2026-09-20,
   verified 2026-10-26). `_worker_reservations` takes a synchronous per-(account×TCIN) reservation on
   the monitor thread *before* any purchase thread spawns (`bulletproof_purchase_manager.py:3864`,
   `TARGET_MULTI_SKU_DISPATCH=1`); the old `_active_purchases` registration-lag footgun is closed;
   covered by `tests/test_multi_sku_dispatch.py` in the offline gate. The only residual is the
   deliberate `TARGET_MULTI_SKU_WORKERS_PER_TCIN=3` **cap value** (operator's 09-23 choice — a cap of
   2 idled an account on single-TCIN nights; cost documented in the bat: 23 MULTI_SKU_MISS skips in
   107 logs, 0 raced same-window). A narrow refinement — a `*_CONTESTED` cap that shrinks the per-TCIN
   fleet share only while a 2nd TCIN is live — is possible but **gated on an unproven premise** (at 3
   shared-home-IP accounts, whether a 2nd ticket is independent is `[NOT ESTABLISHED]`): hand it to
   `claims-verifier` before building. Not speculative work.

**Tier 2 — adopt their cadence doctrine (A/B only):**
4. **"Keep submitting / let it ride" (B4):** A/B `TARGET_WAVE_FIRST_ONLY=0` on **one identity**, the
   other two holding current policy as an in-window control.

**Tier 3 — the decisive, expensive lever:**
5. **More accounts toward the floor of 10+ (B1),** each with its own card. Single highest-value
   move; the only way to approach the regime's ticket count. Do it after Tier 1 is measured.

**Keep:** residential-shoot / ISP-monitor split (done).

---

## 7. The gate above everything (read before arming resi)

The premise justifying residential at all — that a resi line draws a **different** limiter admit
than the home line — is **UNPROVEN and currently contradicted** (119/119 both lines, 10-23;
*"one shared admit rate per window, not a line/account key"*). Only a live drop with the multi-line
guest-follower (`tools/experiments/limkey_guest_follow.py`) settles it. Mimic the architecture
regardless (free and correct), but **set expectations by the limiter regime, not by the bot.**
Per project discipline: hand the bare premise to `claims-verifier` before arming — a REFUTED
verdict kills it.

---

## 8. Open contradictions & housekeeping

- **"401 = Shape block" (Refract FAQ, L3340) vs our conclusion that the ATC 401 is the write-auth/
  SSX-hop layer** (C-0930-03 gate order: edge limiter → Shape SSX hop (keyless 401) → cart). One is
  wrong; it changes where B2 effort goes. Unresolved.
- **"Hype Product" mode internals are not public** (the toggle that routes ATC down the
  Shape-credentialed path — inferred, unverified). Static analysis of the Refract extension bundle
  would likely answer this and the 401 question.
- **Newer Refract snapshots (10-11, 10-18, 10-19) are on disk but not yet fully folded in** beyond
  §1's regime note — worth a dedicated diff read before the next drop.
- **Other docs stale** (flagged for a separate pass): `docs/RESILIENT_STACK.md` still headlines the
  monitor as pure browser-native `tab.evaluate(fetch)`, but the wired production path is
  `RESILIENT_REDSKY_CHANNEL=apps_raw` (raw urllib GET of the mobile-apps endpoint). `docs/ANTIBOT.md`
  (and CLAUDE.md) say *"no curl_cffi in the request path"* — true for *unsigned* requests, but
  E-SHOOTER (C-1023-22) proves curl_cffi same-IP replay of a *signed* Shape token passes (424); the
  blanket phrasing is stale. `.claude/state/CURRENT_STATE.md` was **restored this session** (2026-10-26)
  from HEAD — still needs its as-of header refreshed with the 10-26 resi-harvester status.

---

## 9. Security model (wall-by-wall) & the sticky-vs-rotate verdict — authoritative 2026-10-26

Claim IDs inline; MEASURED vs REPORTED vs UNPROVEN as tagged. Gate order CONFIRMED against current
code (C-0930-03). **A 401 is a shot that already got PAST the limiter.**

### 9.1 The wall stack, in order
1. **Wall 1 — edge rate limiter.** `429`, content-length 0, `retry-after: 0`,
   `tgt-cart-error-key: ERR_A2C_TCIN_RATE_LIMITED`, **no `x-ssx-hop`**, latency p50 **147 ms** (the
   "~130 ms" elsewhere is the p5). Per-TCIN "high-demand" throttle switched on by the live drop state,
   **not by our traffic** — it exists before our first contact (fresh-TCIN POSTs 24/24 limited,
   C-1011-04). A keyless-429 variant (no `tgt-cart-error-key`) is classed `wall1_keyless`. **This is
   the wall that kills us.**
2. **Wall 2 — F5 Shape / SSX hop.** keyless `401`, body `_ERR_AUTH_DENIED`, 231 bytes,
   **`x-ssx-hop=1`**, `fastly-restarts=1`. Trust lives here. Distinct from keyed `401 ERR_UNAUTHORIZED`
   = a dead member token (write-auth, not Shape).
3. **Wall 3 — cart service / checkout DCO.** `201` on cart success, then place-order
   `429 FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION` / `RESERVATION_FAILURE` (the "submit-order lottery",
   carries `x-envoy-upstream-service-time`). Orders come from EARLY place-orders (18/21 ≤~4.6 s).

### 9.2 Where residential egress bites, per wall
- **Wall 1: no measured help; ONE unresolved branch.** On 10-23 resi shared home's fate (alt-1 0/40,
  home 0/134, bot 0/119) — but that test was **underpowered** (at a ~1% admit, 40 shots show 0 passes
  64% of the time; needed ≥5 windows, had 4). The limiter is **not a per-IP quota**; evidence points to
  per-POP / per-ASN / standing-hot-list keying (C-1011-05). `p` is site-wide and volume-independent
  (C-1023-26: same SKU/accounts 76/268 → 1/192 → 0/466 as `p` fell *with more shots*).
- **Wall 2 (Shape): residential SHOULD help, but we have ZERO own data** — because resi never got past
  wall 1 to reach wall 2. The Shape token is **IP-bound, not cookie-bound** (E-SHOOTER C-1023-23/24).
  Home denied 13/38; BD exits 514/518 (C-0930-05, one-night sample).
- **Wall 3: egress-neutral** (keyed on the cart, not the IP).

### 9.3 Sticky vs rotate/randomize — the verdict
- **Shape REQUIRES sticky.** The IP-bound token means the harvester (mint) and shooter (replay) must
  egress the **same** IP; a mid-flight IP change = instant keyless 401. In `src/lanes/` this is
  structural (one harvester per exit IP; each shooter built with that harvester's `url` + `bank`).
  IPRoyal stickiness = a `session-<hex>` token in the proxy username, **168 h** lifetime; the 4
  configured sessions are **no-kill-switch** (the kill-switch ones died in <10 h, C-1023-13).
- **Rotating/randomizing — what it buys vs costs:**
  - *Cold IP* is probably fine for Shape (a fresh guest passed 6× off-window, C-1021-01), **but a cold/
    rotated PROFILE meeting a new IP draws PX** — the HUMAN/PX wall is **profile-bound, not IP-bound**
    (C-1023-14). So never rotate the buyer profile; rotate exits only with fresh guest profiles.
  - *Limiter:* rotating **within one provider/ASN** (all 4 IPRoyal are one provider, likely one ASN)
    **shares one fate → buys nothing.** Only **cross-ASN/provider diversity** could give independent
    limiter draws — and that is **untested** (and we don't currently own cross-ASN resi IPs).
  - *Hard cost:* Chrome + an IPRoyal sticky session **freezes CDP at 7.6–21 min** of Chrome age
    (C-1023-12/18-21) — so rotation means constant Chrome relaunches (~45 MB/cold load, ~2.3 GB/day).
- **What the lane code randomizes today:** cadence jitter 3–4.5 s (good); real captured Chrome headers
  in wire order (good); **TLS/JA3 via curl_cffi `impersonate` — drift risk** if the profile lags the
  live Chrome major; **token order FIFO/oldest-first — a bug** (production uses freshest); session
  selection deterministic. Nothing is over-randomized.

### 9.4 What is necessary to get past the initial gate (honest)
- **Measured to HELP wall 1: nothing we control.** No lever has raised `p`.
- **Measured NOT to help:** more accounts on one IP (C-1015-01); faster cadence (10-16, 0/1,208);
  detection speed; edge-429 retry knobs (inert).
- **The ONE decisive experiment (unblocks the whole residential bet):** a **multi-line in-window guest
  follower across DIFFERENT ASNs** (home + BD reserve + ≥2 IPRoyal on *distinct* ASNs), equal
  cadence/cap, same races, window-stratified, pre-registered. **Positive signal:** a resi/BD line
  passes wall 1 in ≥3 windows where home does not (and ≥2× the reverse). Concordant zeros = underpowered,
  not proof. This needs a **live open drop window** and **cross-ASN resi IPs we don't yet have.**
- **One untested lever worth an arm:** the **BUY NOW subchannel** (`cart_subchannel:"BUYNOW"`) — our bot
  has fired it **0 times in 65,898 cart POSTs** (C-1011-10/C-1023-27); a BUYNOW-vs-plain A/B on the same
  hot TCINs would show whether it draws a lower edge-429 rate. Money-free (still an ATC path).
- **Sensor-depth gaps (wall 2, for when wall 1 opens):** every account/profile/guest shares **one PC's
  fingerprint** — a second-device test is a **$0 experiment** we have not run; plus the TLS-impersonation
  drift, the FIFO token-freshness bug, and PX profile taint on line changes.

### 9.5 Bottom line
Residential is the **right, proven tool for Shape (wall 2)** — and the design there is **sticky, never
random**. But Shape is unreachable until **wall 1 (the initial gate)** opens, and wall 1 is a site-wide
lottery with **no lever we've measured to move it.** Whether residential helps at wall 1 at all reduces
to one unanswered question — *is a p≈0 night global, or just our home IP/ASN throttled?* — and the only
thing that answers it is a **cross-ASN measurement at a live drop**, not a build. So "mimic Refract with
residential to beat the initial gate" is, with highest confidence: **plausible but unproven, gated on a
measurement and on cross-ASN IPs we don't own — not on code we haven't written.**

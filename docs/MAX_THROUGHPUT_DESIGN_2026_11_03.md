# Maximum-throughput buy-path design (2026-11-03)

Goal: extract the most *independent* purchase attempts per drop window from the
proxies and accounts we already own, with unlimited compute, **without burning
metered IPRoyal residential GB.** Written from the stance of: compute is free,
the scarce resources are distinct network identities, Shape tokens, accounts, and
residential bandwidth — in that order.

Status: DESIGN. Nothing here is armed. The lane machinery exists at
`TARGET_LANE_MODE=0`. The one number that sizes the whole thing is unmeasured and
is gated on the next live drop (§7).

> **⚠ VERIFICATION OUTCOME (2026-11-04) — THE CORE PREMISE DID NOT HOLD. DO NOT BUILD FROM THIS AS-IS.**
> Three adversarial verifiers checked this design against our data/code:
> - **Harvester on BD (§5): SOUND.** PerimeterX does not wall fresh-profile BD harvest (0 PX walls / 160+ loads); BD mints Shape-valid tokens off-window. The one bank-starvation on record was an *aged profile on IPRoyal*, not a BD/line property. ✅
> - **Per-ASN / per-prefix keying (§1, §2, §6 — the ENTIRE upside): NOT ESTABLISHED, leans REFUTED.** There is **no ASN measurement anywhere in the repo** (the event store has no egress-ASN field). The one multi-ASN night (09-15) shows a *different-ASN* line (business on 31.98.x) tracked home's fate (5.5% ≈ 2.2%) while only a single *exit* (alt-1 168.158.x, 34%) diverged — which by this doc's own rule (§7) leans **per-IP, not per-ASN**. The strongest number we have — **C-1023-26, all 38 runs** — says admit `p` is **site-wide and NOT a function of lines/accounts/volume**, i.e. more lines/ASNs/tasks raise *nothing*. **The 4–13× ticket table in §6 is speculative and currently contradicted by our own data, not a projection to act on.**
> - **"IPRoyal = Comcast ASN = no diversity" (§2, §4): WRONG as stated** — an unmeasured octet guess; one IPRoyal exit was seen on T-Mobile (172.59.x) and its /16 differs from home's. "Hold IPRoyal dark" still stands, but on **reliability (Chrome stall 7–21 min), cost (metered GB), and Shape parity** — not ASN diversity.
> - **Citation error:** the per-ASN inference was attributed to C-1011-03; the real limiter-key verdict is **C-1011-05 (NOT ESTABLISHED)**, which lists per-ASN as only one of three un-refuted candidates. "Standing" ≠ "confirmed."
>
> **Bottom line: SHELVED as a proposal.** Nothing here gets built until the P0 experiment (§7) runs on a real **open** window (`p>0`) and shows **two distinct BD ASNs sharing each other's fate while diverging from home**. That measurement has never succeeded (10-23 inconclusive, resi arm died, `p≈0` night). Until then, "ramp to more tasks/lines" is contradicted by our own evidence; the thing that wins is being present on an open window, which the existing 3-account home setup already carts on.

---

## MEASURED: our lines' real ASNs (2026-11-04) — first-ever ASN measurement, fixes a repo-wide gap

Resolved real exit IPs (from account notes + logs) against ip-api.com [MEASURED; ASN from BGP/RIR data is reliable; the specific BD exit IPs are from ~08-07 notes but the owning ASN of each range is stable]. Nobody had ever measured this — "Comcast" was an octet guess.

| Line / exit IP | ASN | ISP / region |
|---|---|---|
| home 67.184.x | **AS7922 Comcast** | Comcast, IL |
| BD 31.105.133.83 | **AS6079 RCN** | RCN, NY |
| BD 31.98.169.203 | **AS6079 RCN** | RCN, TX |
| BD 168.158.160.228 | **AS20012 Interworks** | Interworks Networking, AZ |
| BD 72.56.171.184 | **AS213541 WS Telecom** | WS Telecom, WY |
| IPRoyal A 73.44.x | **AS7922 Comcast** | Comcast, IL |
| IPRoyal B 73.246.x | **AS7922 Comcast** | Comcast, IL |
| IPRoyal D 172.59.x | **AS21928 T-Mobile** | T-Mobile, NM |

What this changes:
- **BD ISP gives REAL ASN diversity — 3+ genuinely distinct ASNs** (RCN, Interworks, WS Telecom), different states, all legit regional ISPs (good reputation substrate). NOT "one BD ASN." And **/16 ≠ ASN**: 31.105 and 31.98 are different /16s but both AS6079 RCN — so counting diversity by /16 overcounts.
- **IPRoyal is UNRELIABLE for ASN diversity:** 2 of 3 sampled sessions = AS7922 Comcast = home's *exact* ASN (zero diversity); 1 = T-Mobile. It rotates and often lands on home's ASN. "Hold IPRoyal dark" stands; the earlier "IPRoyal = Comcast = home" was right for the Comcast sessions, wrong only as a blanket (D was T-Mobile).
- **Per-ASN keying is still NOT supported — and this SHARPENS the 09-15 evidence against it:** that night home = AS7922 Comcast, business = **AS6079 RCN (a DIFFERENT ASN)** tracked home (5.5% ≈ 2.2%), while alt-1 = **AS20012 Interworks** diverged (34%). A different ASN (RCN) sharing home's fate ⇒ not per-ASN; the divergence was one specific exit/ISP (Interworks), i.e. per-IP/per-exit, not per-ASN.
- **Net:** the *substrate* for "go wide" is better than feared (real multi-ASN on BD), but the one live data point says ASN is not the key. Going wide is justified only if the gating experiment (two distinct BD ASNs, same open window) shows ASN-level behavior. Still unrun.

---

## Overnight verification — fast cadence / "spam ATC" (2026-11-04): NOT ESTABLISHED, REFUTED on carts

A fresh-context verifier tested "more ATC shots → more carts" against the event store. **Do NOT build a fast-cadence / spam lever.**
- The 10-29 seed (alt-1 fast 158 shots → 6 wall-1 passes > slow 2 each) is real but **n=1 night and confounded**: the race loop breaks on a **cart** (`bulletproof_purchase_manager.py:3306`), not a wall-1 pass — so an account that fails to cart keeps firing → "more shots" is an *outcome* of not carting, and alt-1's extra passes land in the low-p tail (age 577-647s), unmatched to the slow accounts' edge passes (age 0-60s). [MEASURED]
- **"At fixed p" is false:** 09-15 alt-1 and business fired identical 182 shots → 61 vs 10 passes (6×). p is an account/exit property, not a window constant. [MEASURED]
- **Cross-run non-monotonic:** the largest-n run (08-27, 2,680 shots) contradicts — most shots → fewest passes. [MEASURED]
- **On CARTS it reverses:** 10-29 carts = primary(slow) 2 / business(slow) 0 / alt-1(fast) 1 — the slow account carted most with 1/10th the shots; 09-15 alt-1's 61 wall-1 passes → 0 carts (Shape 401s the proxied line). Wall-1 passes ≈ uncorrelated with carts. [MEASURED]

Conclusion: spamming ATC does not buy carts and may cost them. The "spam helps wall-1" read rested on the confounded seed and does not survive. Would settle it: multiple p>0 nights, matched line + matched window-age bins, measured in carts.

---

## 1. The three gates (what every buy must clear)

| Gate | Mechanism (research + our data) | The lever |
|---|---|---|
| **Wall 1 — edge limiter** | L7 rate limiter on *add-to-cart*; answers first (~150 ms, no `x-ssx-hop`); a per-window Bernoulli lottery at probability `p` set **site-wide**. Sophisticated hot-SKU limiters key the bucket on **endpoint + IP *prefix/ASN*** (bounds bucket count, resists IP rotation) [INFERRED from limiter best-practice + our C-1011-03 "standing per POP/ASN"]. | **More distinct ASNs**, one clean shot per ASN per re-entry. More shots *within* an ASN = same bucket = self-throttle. |
| **Wall 2 — F5 Shape / SSX** | JS-VM sensor collects device FP + behavior → a **session token bound to one IP, ~5–10 min, one per protected POST** [MEASURED E-SHOOTER + REPORTED scrapfly/Refract]. Keyless 401 = the Shape verdict. Target also runs **HUMAN/PerimeterX** (Press & Hold) on the harvest. | Each firing line **self-harvests its own token on its own IP** (coherent real-browser FP). Harvester fleet mints; shooters consume. |
| **Wall 3 — checkout** | Per-account `FAST_SELLING` reservation lottery; **not IP-fixable** (the units are gone). | Already maxed: `WONCART_RIDE_TO_OOS=1`, ~147 tickets/cart. Needs a cart + an account. |

The walls are independent and want **opposite** lines: wall 1 wants ASN breadth,
wall 2 is solved per-line by self-consistency, wall 3 is account-bound. A single
ATC request exits one IP, so you cannot combine two lines' strengths on one shot —
you combine them by running **many self-consistent lines in parallel**.

## 2. Resource inventory (what we own, 2026-11-03)

| Resource | Count | Property | Role in this design |
|---|---|---|---|
| **Compute** | unlimited | user's host | run the whole harvester+shooter fleet; **not a constraint** |
| **Bright Data ISP** | 12 IPs (10 active + 2 reserve) across **3 ASNs** (31.105 / 168.158 / 72.56) | static ISP, reputation builds, cheap/unmetered, **faces PX on harvest** | **the workhorse** — wall-1 volume + harvesting |
| **IPRoyal residential** | ~4 sessions | **metered GB (scarce)**; IL ≈ **Comcast ASN = home's** (no wall-1 ASN diversity); 17–21 min Chrome stall | **minimize** — see §4 |
| **Home** | 1 | Comcast; proven at checkout | checkout handoff + 1 ASN ticket |
| **Accounts** | 3 | the conversion cap | one checkout-capable identity each |

**Distinct wall-1 ASN tickets available ≈ 4** (Comcast + 3 BD). That — not the IP
count, not compute — is the ceiling if the limiter keys per-ASN.

## 3. Architecture

```
                         ┌─────────────── BD ISP pool (12 IPs / 3 ASNs) ───────────────┐
  MONITOR (apps channel) │  HARVESTER FLEET            SHOOTER LANES                    │
  ~few BD IPs, reads      │  N Chromes / IP:            1 shooter / IP:                  │
  stock, never buys       │  run Shape JS,              fire ATC w/ a token harvested    │
                          │  intercept+ABORT the ATC,   on the SAME IP, wave-first,      │
                          │  bank the token (~KB each)  self-consistent (passes wall 2)  │
                          └───────────────┬───────────────────────┬─────────────────────┘
                                          │ 201 cart               │ (3 accounts fanned
                                          ▼                        │  across the ASNs)
                                 CHECKOUT on HOME chain  ◄──────────┘
                                 place-order → ride to OOS (existing won-cart loop)

  IPRoyal: OFF by default (metered). Optional tiny reserve only if a residential
  token ever measures better than BD — it currently does not (both pass wall 2).
```

**Mapping the 3 accounts for max independent tickets:** fire **each account from
one IP in each distinct ASN**, so each account gets up to ~4 ASN tickets per wave.
An account needs only **one** surviving cart to convert, so fanning raises
`P(≥1 cart)`, it does not multiply orders (checkout caps at 2×2/account anyway).

## 4. The IPRoyal bandwidth rule (the "be smart" part)

Residential GB is the only metered resource, so the design **keeps volume off it**:

1. **All harvesting + carting runs on BD ISP** (cheap/unmetered), never on IPRoyal.
2. **Every proxied browser uses route-level request interception** — abort
   `image/stylesheet/font/media` before the request leaves the browser, so the
   proxy bills nothing for them. Keep only the Shape sensor JS + the target XHR.
   Our own cost was ~45 MB per *cold page load*; intercept-and-abort a harvest is
   **sub-MB**. Expect **≥10×** reduction [REPORTED oxylabs/dev.to + our 45 MB datum].
3. **IPRoyal is OFF in the hot path by default.** It gives us *no* wall-1 ASN
   diversity (same Comcast ASN as home) and *no* unique wall-2 edge (BD→BD and
   resi→resi both pass Shape self-consistently, E-SHOOTER), while costing GB and
   stalling Chrome at 17 min. Smartest use of a metered, non-differentiated line
   is **barely use it** — hold it as a 1–2 line reserve, intercept-and-abort only.

## 5. Harvester ↔ shooter economics

- 1 token per protected ATC POST; token life ~5–10 min; IP-bound.
- Refract ratio [REPORTED]: ~6 harvester browsers per 90–120 cookies.
- With unlimited compute: run enough harvesters per BD IP to keep every lane's
  bank > 0 through a window, with a buffer. Tokens are cheap to over-produce; a
  **page-signed shot** (empty bank) is a wasted ticket, so over-provision.
- **PX dependency (risk):** BD harvest tabs draw HUMAN Press & Hold; a walled
  harvest empties the bank → page-signed shots. BD harvest reliability under PX is
  the single biggest technical unknown in this build (see §8).

## 6. What the throughput actually buys (the math)

`P(≥1 cart for an account this wave) = 1 − (1 − p)^(independent tickets)`

| Regime `p` | now (~1 ticket) | per-ASN (4 tickets) | per-IP (13 tickets) |
|---|---|---|---|
| 5% (10-29 night) | 5% | **18.5%** | **49%** |
| 30% (10-01 night) | 30% | **76%** | **99%** |

Per account, per wave. Across 3 accounts and several waves a drop, per-ASN alone
is a step change; per-IP is transformational. **The gap between the two columns is
the entire value of the measurement in §7** — and why building the wide fan before
measuring risks paying for 13 and getting 4.

## 7. The gate: measure keying before you scale spend

The design's ceiling (4 vs 13 tickets) = **does wall 1 key per-IP or per-ASN?**
UNMEASURED. The pre-registered experiment (guest followers on bd0/bd1 = two BD
ASNs + home, same window; `docs/NEXT_DROP_CHECKLIST.md`) answers it:

- **≥1 BD ASN carts while home gets 0, same window** → different-ASN beats home →
  per-ASN keying confirmed → scale to **3 accounts × passing ASNs**.
- **Two BD ASNs differ from each other** → leans per-IP → the wide fan is worth it.
- **Every line gets 0** → closed-gate night (`p≈0`), inconclusive; nobody wins it.

## 8. Risks / unknowns (ranked)

1. **BD harvest under PerimeterX** — if Press & Hold walls BD harvest at scale, the
   bank starves and the whole fan fires page-signed. Must validate harvest yield
   per BD IP before trusting lane output.
2. **One account from many IPs at once** — one identity, many simultaneous
   locations is an unusual Shape/account-risk signature; could trip account-level
   denial. Test small (1 account × 2 ASNs) before widening.
3. **ASN keying (§7)** — caps the whole thing at ~4 if pessimistic.
4. **Account cap (3)** — hard conversion ceiling; more needs account creation
   (operator-forbidden: detection/ban risk).
5. **Static-ISP small-pool reuse** — hammering 12 ISP IPs at one target is itself a
   watched pattern; wave-first + token freshness mitigate, reputation is finite.
6. **Checkout `FAST_SELLING`** — even a perfect cart fan converts ~1% (units gone);
   no IP fixes wall 3.

## 9. Build phases (all flag-gated, `TARGET_LANE_MODE`, byte-identical off)

- **P0 — measure (tonight):** the §7 experiment. No build. Output = independent
  ticket count + whether BD beats home.
- **P1 — 3 accounts × passing ASNs, self-harvest + intercept-abort, home checkout
  handoff.** The earned ramp. Validate PX harvest yield + the one-account-many-IPs
  signature small first.
- **P2 — widen to per-IP fan** only if P0 showed per-IP keying and P1 stayed clean.
- **IPRoyal** stays a dark reserve throughout.

**Bottom line:** with current proxies the real ceiling is **~4–13 independent
wall-1 tickets per wave (vs ~1 now), 3 checkout-capable**, run entirely on BD ISP
with intercept-and-abort, IPRoyal held dark. The ceiling is set by ASN keying, not
compute and not IP count. Measure that first; everything else follows.

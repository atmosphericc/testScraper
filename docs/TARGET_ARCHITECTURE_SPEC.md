# The architecture to actually convert hot SKUs — spec, cost, build order

Written 2026-09-20. Grounded in `tools/analysis/*.py` over all 104 run logs, plus
`logs/analysis_2026_09_20/research/{REFRACT_STACK,BRIGHTDATA,COST,FINDINGS}.md`.
Companion: `docs/REFRACT_PARITY.md` (the delta list), `docs/CLAIMS.md` (status ladder).

---

## 0. Are Bright Data lying?

**No — but the label is doing work the product does not do, and they never sold
you this use case.**

BD's own documentation says ISP proxies are *"residential IPs bought or leased
from Internet Service Providers (ISPs) for commercial use, **rather than for use
from private homes**."* That is BD telling you plainly these are not household
lines. Their AUP also prohibits automated checkout. So there is no lie — there is
a mismatch between what you bought and what you are using it for.

What registry data says about **your specific five exits** [REGISTRY/IP-INTEL, 2026-09-20]:

| exit | ASN | reality |
|---|---|---|
| 31.105.133.83 | AS6079 (RCN) | RIPE block held by **Wookra, LLC — an IPv4 *leasing broker***; netname the generic `US-ISP`; object **created 2026-02-02** |
| 31.98.158.87 | AS6079 (RCN) | same pattern |
| 92.112.18.99 | AS396356 Latitude.sh | IPinfo: **"Hosting Detected"** — an outright hosting ASN |
| 168.158.160.228 | AS20012 | **Chiller City Corporation**, a Mesa AZ chiller company with a small colo; 2 upstreams (Cogent, L3) |
| 168.158.32.64 | AS20012 | same /16 |

So 3 of 5 are colo/hosting and 2 are seven-month-old broker-leased space
re-announced through a real ISP's ASN. "ISP (static residential)" is a **routing
cosmetic**, not a household line. A consumer IP-intel lookup does not flag them;
a bot-defence vendor building its own ASN taxonomy (day/night traffic curve,
absence of eyeball prefixes, colo upstreams, no residential DHCP churn) very
plausibly does.

**Where BD is genuinely correct: the monitor.** BD sells a productised Target.com
scraper, and your own RedSky sweep runs at **99.95%**. That is BD delivering
exactly what it advertises. Keep them there.

**The honest verdict:** you are using a monitoring product on the checkout path.
It works perfectly at the job it was sold for and has produced **0 carts in 1,932
add-to-cart shots** at the job it was not.

---

## 1. The target architecture

```
                    ┌─────────────────────────────────────┐
   MONITOR          │  6 × BD ISP exits @ 0.5 RPS each    │   volume problem
   (read path)      │  24 TCINs = 1 chunk → 3.0 RPS       │   → many cheap IPs
   RedSky           │  per-TCIN refresh: every 333 ms     │   $18/mo
                    └──────────────┬──────────────────────┘
                                   │ in-stock edge
                                   ▼
                    ┌─────────────────────────────────────┐
   DISPATCH         │  ONE account per TCIN, SPREAD       │   ← the unlock
                    │  across every live TCIN             │
                    │  (today: 3 accounts stacked on 1)   │
                    └──────────────┬──────────────────────┘
                                   │
                                   ▼
                    ┌─────────────────────────────────────┐
   TASKS +          │  ALL accounts on the HOME LINE      │   trust problem
   HARVESTERS       │  zero proxies                       │   → one great IP
   (Shape path)     │  harvest tab + main tab, same       │   $0
                    │  browser (mint IP == spend IP) ✓    │
                    │  bank: 3 sets per RUNNING task      │
                    └─────────────────────────────────────┘
```

### Why the split
Two different defences guard the two paths and they want opposite things.

- **Monitor** = RedSky read endpoint, polled for hours. Guarded by rate limiting +
  HUMAN/PerimeterX, keyed on IP. Failure mode is **volume**. → many mediocre IPs.
- **Cart** = `carts.target.com`, a handful of requests at drop time. Guarded by
  **F5 Shape**, which fingerprints the browser *and scores IP reputation*. Failure
  mode is **trust**. → one excellent IP.

Measured, same nights, same code:

| | 401 rate | carts |
|---|---|---|
| BD exits | 13-21% | **0 / 1,932** |
| Home IP | 2.8% | **4 / 278** |

And Refract's binding constraint confirms the mechanism: *"Tasks present the
harvester proxy to Shape for add to cart and login"* — **the Shape sensor is bound
to the IP that minted it.** We already satisfy this (harvest tab and main tab share
a browser), which is exactly why the 2 BD accounts are *consistently* bad: they
mint on a flagged range and spend on it too.

---

## 2. The unlock nobody would guess: spread, don't stack

[MEASURED — `tools/analysis/limiter_key.py`]

Target's edge limiter is a **shared per-TCIN volume bucket**, not a per-identity
cooldown. Pass rate vs prior shots on that TCIN in 120 s:

| prior shots (other identities) | shots | pass% |
|---|---|---|
| 0 | 83 | **9.6%** |
| 1-2 | 109 | 9.2% |
| 3-4 | 190 | **1.1%** |
| 9+ | 2150 | **0.4%** |

- It is **not** a cooldown: a re-shot `<10 s` after the identity's own last shot
  passes **4/25 (16%)**. Volume is what kills, not recency.
- It is **not** hype-contest confounding: the same collapse appears on *ordinary*
  uncontested SKUs, same nights and code.
- It crosses identities **and IPs** — primary is on home, alt-1/business on BD.

**And we do the worst possible thing with it:** `[RACE] <tcin>: racing 3 accounts`
fired **36 of 36 times** on 09-17. All three identities pile onto one SKU while
other live hot TCINs go untouched (`[MULTI_SKU_MISS]` 13× on 09-16).

**This is why account count alone would not have saved you.** Ten accounts stacked
on one TCIN is ten times the volume in one bucket — the per-shot rate collapses
toward 0.4%. Ten accounts on *ten different TCINs* each get their own bucket near
9.6%. Same spend, ~20× the expected carts.

Refract's model is exactly this: **"one task per account per product."**

---

## 3. Build order (order matters — later steps depend on earlier ones)

| # | Change | Cost | Why this position |
|---|---|---|---|
| **1** | **Narrow the purchase lock** from fleet-global to per (account × TCIN), with synchronous worker reservation | code only | **The unlock for everything else.** Without it, extra accounts cannot work different SKUs and step 4 is wasted money. |
| **2** | **Move all accounts to the home line** (`proxy_url=""` for alt-1 and business) | **−$9/mo** | Free, reversible, and both the vendor docs and your own 0/1,932 say so. |
| **3** | **Bank depth → 3 sets per running task**, continuous refill; model the profile-write burn | code only | Becomes the binding constraint the instant 1 and 2 land — Refract says so explicitly and the bank is a hard admission gate. |
| **4** | **Trim monitor to 6 IPs** | **−$30/mo** | 6 × 0.5 RPS = exactly your 3.0 RPS. Verify on one live run before cancelling. |
| **5** | **A/B the retry cadence** (3.5 s vs 60 s wave-first) on **one identity**, other two as control | code only | Genuinely unresolved — see §4. Must not be a blind flip. |
| **6** | **More accounts**, each with its own card | cards | Only pays off *after* step 1. One TCG purchase per card, so N accounts need N cards. |
| **7** | *(optional)* second residential line — 5G home internet | +$35-50/mo | A genuine second consumer-ASN identity pool. Only if 1-6 are measured and you want more. |

**Net: $75/mo → $18/mo**, with more expected carts, before spending a dollar more.

---

## 4. What is still genuinely unresolved — do not let me pretend otherwise

- **Cadence.** Per-shot dilution is real and measured. But *total throughput* per
  window is flat-to-rising with volume, and the high-volume evidence is 13 **July**
  windows (1.46 passes/win, fresh IPs, pre-hype target list) vs 51 August windows
  (0.294). September has **zero** high-volume windows because wave-first removed
  them by design. **Our data cannot settle this in-era.** Refract says "keep
  submitting and let it ride"; that is the better prior, but it ships as an A/B.
- **401 meaning.** Refract's FAQ: *"What does error 401 mean in my Target logs? **A
  Shape block**."* Our 2026-07-10 conclusion was write-auth/token, not Shape. One
  is wrong, and it changes where bank effort goes.
- **Hype Product mode** is not public — 4 mentions in a 351 KB corpus. Unverified
  lead: `cdn.prismaio.com/refract/extension/latest.zip` (v3.1.11) was never
  downloaded; static analysis would likely answer this and the 401 question.
- **The conversion fix is unproven live.** `TARGET_FASTLANE_PRE_RETRY` (5e3791b5)
  addresses the gate every hot cart ever died at (pre=429 → 0-for-8; pre=2xx →
  14/34 converted). It has never seen a drop.

---

## 5. Honest expectation

Do not expect this to turn 0 into many. The funnel says hot SKUs give
P(cart|shot) = 0.16% against ordinary SKUs' 1.63%, and there have been **5 hot
carts in the bot's entire life**. Zero orders over six weeks was the *expected*
outcome of that funnel, not evidence of a broken bot.

What this architecture changes is the two multipliers:

- **carts** — stop self-suppressing (§2), and put every identity on the one
  connection that has ever produced a cart;
- **conversion per cart** — already shipped, unproven.

That is the whole lever. Anything else on the market is selling you the first
multiplier at a price, and the measured truth is that you were buying the wrong
one.

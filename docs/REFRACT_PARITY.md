# Refract parity plan — what they do, what we do, what to change

Sources: `logs/analysis_2026_09_20/research/REFRACT_STACK.md` (vendor docs, 351 KB
`llms-full.txt` corpus), `FINDINGS.md`, `COST.md`; measurements from
`tools/analysis/*.py` over all 104 run logs. Status tags follow `docs/CLAIMS.md`.

## 1. What Refract actually is

**It decouples the browser from the buyer.** A small pool of real Chromium
browsers ("harvesters") mints Shape credentials into a shared bank; a large pool
of *browserless request workers* ("tasks") spends them.

> "for around 90-120 cookies you will probably need about 6 browsers"
> "100-150 tasks on a Mac mini"; "Network capacity is usually the bottleneck"

That first sentence cannot exist if tasks were browsers. Their glossary:
harvester = "the browser process", task = "essentially a worker".

Consequences that matter to us:
- **The bank is a hard admission gate.** No cookie, and the task sits on
  `Waiting for Cookies (Product)` and does not fire. Concurrency is controlled by
  credential inventory, not by timers.
- **No serialization and no stagger.** "All tasks in the group go for whichever
  product the monitor picks up first."
- **Retry Delay is post-error only** — "task delay does nothing during a perfect
  checkout flow." Target retry/monitor delay `3500` ms; users run 1000-4000.
- **On the exact 429s we get** (`FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION`,
  `ERR_A2C_TCIN_RATE_LIMITED`): "**keep submitting the order and let it ride**."
- Cookies are **operation-bound** (ATC and login are separate pools) and
  **effectively IP-bound**: "Tasks present the harvester proxy to Shape for add
  to cart and login."
- Starter tier: 10 tasks on **10 unique accounts**, **no proxies at all** on
  tasks or harvesters (home IP); proxies only on the monitor.

## 2. Where we ALREADY match — do not spend effort here

| Mechanism | Us | Refract |
|---|---|---|
| Harvest = intercept-and-abort | `shape_harvest.py` FAILs the `cart_items` POST (BlockedByClient) | "It **never completes** those actions" |
| Newest-cookie-first replay | LIFO | `Use Newest First` default |
| Harvest on a cheap/any product | yes | "harvest on any in-stock product" |
| Real-Chrome dispatch (JA3/JA4) | `tab.evaluate(fetch(...))` | browser-minted sensors |
| Monitor cadence | 3 RPS / 3 IPs | 3500 ms — **ours is already hotter** |

Detection is not the bottleneck. The earlier premise that we needed to switch to
intercept-and-abort was wrong: we already do it.

## 3. The deltas, ranked

### D1 — Fleet-wide purchase lock (cheap, highest value)
`bulletproof_purchase_manager.py:3932` blocks a *distinct* in-stock TCIN because
another purchase holds the entire fleet. `[MULTI_SKU_MISS]` fired **13x on
09-16**. Refract has no such lock; its only contention is Target's per-account
cart (`Cart in Use`, resolved by retrying).

**Change:** narrow the lock from global to per **(account × TCIN)**. Different
accounts have different carts and genuinely do not contend. The existing code
comment already names the requirement — synchronous worker reservation, because
`_active_purchases` registration currently lags inside the spawned thread, so a
naive per-TCIN gate would assign one worker to two SKUs.

### D2 — 60 s wave-first sleep vs a 3.5 s retry loop (cheap, high value, RISKY)
After an ATC-level 401/429 we sleep 55-70 s (`TARGET_WAVE_REENTRY_MIN/MAX_S`)
before a cold re-entry. Measured cadence on 09-17: **2 shots per identity per
stock window**, a clean ~60 s sawtooth, even inside a 551 s window (17 shots
where a 3.5 s loop would fire ~157).

**But see section 4 — this is the one delta our own data contradicts.**

### D3 — Bank depth (high; becomes binding the moment D1/D2 land)
Ours: 3 sets per account, ~9 total, refilled on a schedule.
Refract: ~3 cookies **per running task**, continuously replenished, with the bank
gating whether a task fires at all. At a 3.5 s cadence one window drains us in
seconds.

Also `TARGET_HARVEST_TTL_S=300` is the **low end** of their documented 300-600 s;
they put real lifetime at 10-15 min. (We already raised replay age 100 → 300 s.)

**Hidden cost they document and we do not model:** the customer-info/profile
write is itself Shape-protected and burns a cookie. Refract does it at task
start, hours early.

### D4 — 3 accounts vs a published floor of 10 (high value, costs money)
At their own "80% blocks is a fine day", 3 shots expects <1 clean attempt per
window. Ranked below D1-D3 only because those three multiply the *existing* 3
accounts at near-zero cost. Constraint from `FINDINGS.md`: **one TCG purchase per
credit/debit card**, so each account needs its own card or only one order
survives.

## 4. The measurement that complicates D2 — read before flipping anything

`tools/analysis/limiter_key.py`, `edge_pass_by_gap.py`, `shot_index_yield.py`,
`throughput_vs_volume.py` (all new, read-only, re-runnable).

**The edge limiter is a shared per-TCIN VOLUME bucket, not a per-identity
cooldown.** [MEASURED]

Pass rate vs prior shots on the same TCIN in 120 s, by *other* identities:

| prior shots (other identities) | shots | pass% |
|---|---|---|
| 0 | 83 | 9.6% |
| 1-2 | 109 | 9.2% |
| 3-4 | 190 | 1.1% |
| 5-8 | 435 | 0.7% |
| 9+ | 2150 | 0.4% |

- Holding our own prior shots at 1-2: other=0 → 5.9%, 3-4 → 0.7%, 5-8 → **0.0%**.
- **Not a cooldown:** the gap since the identity's OWN last shot barely matters —
  `<10s` passes **4/25 (16%)** on 09-17, vs `40-80s` 3/31 (9.7%).
- **Not hype-contest confounding:** the same monotonic collapse appears on
  *ordinary* (uncontested) SKUs, same nights, same fleet, same code.
- **Cross-identity and cross-IP:** primary runs on the home IP, alt-1/business on
  Bright Data exits — different IPs entirely — and they still suppress each other.
- We race **all 3 accounts at ONE TCIN, 36 of 36 races** on 09-17.

**But throughput does not collapse.** Total passes per 120 s window are
flat-to-rising in volume; the best hot bucket is 17+ shots at 0.531
passes/window.

**That result is era-confounded and cannot be used.** It splits into 13 *July*
windows (1.46 passes/win — fresh IPs, pre-hype target list) vs 51 August windows
(0.294). September has **zero** 17+ windows, because wave-first removed them by
design. [NOT ESTABLISHED]

**So: our data proves the per-shot dilution is real, and cannot settle whether
higher volume nets more passes in the current era.** Refract's documented answer
("keep submitting") is the better prior — they are the vendor that wins — but D2
must ship as an **A/B behind its existing flag**, not as a blind flip.
`TARGET_WAVE_FIRST_ONLY=0` already restores the prior cadence.

## 5. Recommended order

1. **D1** — narrow the purchase lock to (account × TCIN), with synchronous worker
   reservation. Pure win, no cadence risk, flag-gated.
2. **D3** — raise bank depth and continuous refill; model the profile-write burn.
   Must precede D2, or D2 starves.
3. **D2** — A/B the retry cadence on **one identity only**, so the other two hold
   the current policy as a control inside the same window.
4. **D4** — more accounts, only after 1-3 are measured.

## 6. Open contradictions to resolve (not blocking)

- Refract's FAQ: "**What does error 401 mean in my Target logs?** A Shape block."
  Our 07-10 conclusion is that the ATC 401 is the write-auth/token layer, not
  Shape. One of the two is wrong, and it changes where D3 effort goes.
- **Hype Product mode is NOT public** — 4 mentions in the whole 351 KB corpus.
  Best inference: the switch that routes ATC down the Shape-credentialed path.
  Unverified.
- The Refract extension bundle (`cdn.prismaio.com/refract/extension/latest.zip`,
  v3.1.11) was not downloaded; static analysis would likely answer both of the
  above.

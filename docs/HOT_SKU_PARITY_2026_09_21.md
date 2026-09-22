# Hot-SKU parity vs Refract — 2026-09-21

Us vs the leading Target bot, scoped to **hype/Shape-protected drops**. Every claim
carries a tag and a citation. Supersedes `docs/REFRACT_PARITY.md` where they differ.

Sources: Refract's complete public corpus (43 pages, pulled 09-21 to
`logs/analysis_2026_09_21/research/refract_llms_full_2026_09_21.txt`); direct code
trace of the current tree; adversarial re-verification of every load-bearing claim.

---

## 0. Two corrections to this project's own record, made today

**(a) "Ordinary SKUs also collapsed on 08-06" is REFUTED.** An earlier reading of
the era split said ordinary-SKU conversion went 19/122 → 0/135. It did not. 96% of
the LOSE-era "ordinary" volume (2,184 of 2,273 chains) was four TCINs fired on one
launch night (2026-08-27) that the classifier mislabels. **True ordinary volume
after 08-06 is ~16 chains.** The bot did not fail at ordinary SKUs — it stopped
being aimed at them (99.3% fewer attempts). The project's own 09-20 conclusion,
*"the target list changed, not the bot,"* was correct. [VERIFIED 09-21]

**(b) The hot/ordinary classifier is broken and seven analysis scripts use it.**
`tools/analysis/funnel.py:34-36` is a static hand-maintained TCIN list with no
contemporaneous source (`config/product_config.json` has no class field). It is
already known-wrong for 4 currently-armed TCINs. **Any hot-vs-ordinary comparison
for dates ≥ 2026-08-24 from these scripts is suspect.** WIN-era numbers
(07-23→08-04) are unaffected. Fix before trusting further segmentation.
[VERIFIED 09-21]

---

## 1. THE HEADLINE GAP — we fire one shot; they fire dozens

**In the current production build, exactly ONE account ever fires at a given
TCIN.** Not three.

`bulletproof_purchase_manager.py:2771`:
```
_limit = per_tcin if (_others or cap_always) else len(ready_workers)
```
With `TARGET_MULTI_SKU_DISPATCH=1` and **`TARGET_MULTI_SKU_CAP_ALWAYS=1`** (both
armed, `bat:768,780`), `cap_always` is always true, so `_limit` always resolves to
`TARGET_MULTI_SKU_WORKERS_PER_TCIN = 1` — **even when only one TCIN is live and the
other two accounts are idle.** [MEASURED 09-21]

Stack that on wave-first cadence (55-70 s between shots, `:2474-2522`, armed) and
the shot budget for a 60-second hype window is:

| | Identities on the TCIN | Cadence | Shots per 60 s window |
|---|---|---|---|
| **Us, today** | **1** | 55-70 s | **~1-2** |
| Refract, their published floor | 10 | 3.5 s | ~170 |
| Refract, local-Windows ceiling | 30 | 3.5 s | ~510 |

That is not a 3× gap. It is roughly **two orders of magnitude**, and the larger
factor is a flag, not hardware.

**Caveat, stated honestly:** this project measured that the edge limiter is a
*shared per-TCIN volume bucket* — more shots lower the per-shot pass rate. But
`docs/REFRACT_PARITY.md` §4 concedes its own throughput evidence is era-confounded:
*"Our data cannot settle this in-era."* Refract's answer on our exact 429s is
*"keep submitting the order and let it ride."* They are the vendor that wins.
[REPORTED 09-21]

**Note on the flag's history:** `CAP_ALWAYS` was armed 09-20 because without it
"the first TCIN grabbed the whole fleet and dispatch was a no-op." Reading the
expression, `cap_always=0` should cap to 1-per-TCIN *only when other TCINs are
live* and otherwise race all ready workers — which is the wanted behaviour. **That
must be verified against the dispatch code before flipping, not assumed.**

---

## 2. Where they are and we are not

| Mechanism | Refract [REPORTED] | Us [MEASURED 09-21] |
|---|---|---|
| **Hype Product mode** | **Mandatory** for every Shape-protected drop; 1st of their "rules that never change" | **Does not exist.** No branch, no config tag. `gate_kind` is derived from Target's response, never from TCIN identity |
| **Bank as admission gate** | Hard gate — no cookie, task shows `Waiting for Cookies (Product)` and does not fire | **Not a gate.** Empty/stale bank → shot fires page-signed. Verified adversarially |
| Credentials per running task | 3, banked, continuously replenished | 6 per account, ≤18 fleet-wide. **Above their ratio** — bank depth is NOT our constraint |
| Identities | Floor 10; ceiling 30 (Windows) / 100-150 (Mac) | **3** |
| Device | *"buy macOS: it is the difference between a 30-task ceiling and a 100-150 one"* | Windows |
| Retry cadence | 3500 ms, post-error, "keep submitting" | 55-70 s wave-first |
| Proxies at our scale | ≤10 tasks + ≤2 harvesters local → **no proxies, home IP** | All 3 buyers on home IP since `679275d9` — **we match** |
| Harvest method | Intercept, never complete | `BLOCKED_BY_CLIENT` before banking — **we match** |
| Replay order | Newest first | LIFO — **we match** |

---

## 3. Bugs found today, in severity order

1. **403 has no handler at all.** `purchase_executor.py:4624-4648` prints
   `[SHAPE_BLOCK]`/`[PX_BLOCK]` and falls through to a bare `else`. It returns
   `atc_failed_api_mode` with **no `gate_kind` key**, so `_gk` is `''` — matching
   neither `auth401`, `edge` nor `dco`. A Shape or PerimeterX block therefore gets
   the plain 2.5-3.5 s cadence instead of wave-first, **and silently resets the 401
   streak** (`bulletproof_purchase_manager.py:2472-2473`). [MEASURED 09-21]
2. **Replay may be silently dead on the current build.** 2026-09-21: 2,277 ATC
   shots, **zero** `REPLAY on main shot` markers, while the boot selftest logged
   replay as live and credentials were being banked. 09-18 had 40 replays / 1,529
   shots. Under investigation. [MEASURED 09-21]
3. **The credential path has never produced an order.** Harvest+replay first armed
   **2026-09-03** (`173683d5`); last order **2026-08-04**. Every order this bot has
   ever placed predates the mechanism. No document said so. [MEASURED 09-21]
4. **Shots cannot be joined to their credential state or SKU class in the logs.**
   `[ATC_RESP]` lines carry no account, TCIN, request-id or thread-id. Markers
   cover ~1.2% of shots. Any credential-vs-outcome analysis is currently
   impossible. [MEASURED 09-21]

---

## 4. The evidence that argues AGAINST the credential theory

Before building hype mode, note where hot SKUs actually die. From the whole-history
funnel (⚠️ subject to the §0(b) classifier caveat for dates ≥08-24):

| Gate | Ordinary | Hot | Hot penalty |
|---|---|---|---|
| Survives the 401 | 51.1% (2,331/4,561) | 53.7% (3,173/5,907) | **none** |
| Passes the edge limiter | 2.9% (67/2,331) | 1.7% (55/3,173) | 1.7× |
| Becomes a cart | 56.7% (38/67) | 9.1% (5/55) | **6.2×** |
| pre_checkout 2xx | 89.5% (34/38) | 20.0% (1/5) | **4.5×** |

**The 401 layer — the one a Shape credential would fix — shows no hype penalty.**
The collapse is at the cart-service demand throttle and pre_checkout, which are
inventory/demand gates where a credential is irrelevant. 98.3% of non-401 hot shots
die at an empty-body volume limiter that never evaluates Shape.

This makes **volume**, not credentials, the better-supported lever — and it is why
§1 ranks first. It also means the unresolved 401 question (Refract: "a Shape
block"; this project 07-10: write-auth layer) decides whether §2's hype work is
the fix or a distraction. **Settle it before building.**

---

## 5. Recommended order

1. **Lift the single-worker cap for single-TCIN windows.** Free, flag-gated,
   restores 3× volume instantly. Verify the `cap_always=0` semantics against
   `:2759-2781` first; pre-register a readout that asserts `[RACE] <tcin>: racing
   N accounts` shows `N=3` on a one-TCIN night.
2. **Re-arm ordinary SKUs.** Free. Every order the bot has ever placed was an
   ordinary SKU; the arming mix went from 9:4 ordinary:hot to 1:23. This is the
   fastest route to *any* orders and it is independent of the hype problem.
3. **Fix the 403 handler** and **fix the classifier** (§0b). Both cheap, both
   currently corrupting the feedback loop we rely on to learn anything.
4. **Add account/TCIN/request-id to `[ATC_RESP]`.** Without it, none of the below
   can be measured.
5. **Settle the 401 question** with one decisive experiment.
6. **More identities** — 3 → 10 is their published floor. Costs money; one TCG
   purchase per card, so each account needs its own.
7. **A Mac mini** — their stated largest device lever. ~$600, and it is the
   difference between a 30-task and a 150-task ceiling on their own numbers.

Items 1-4 are free and should land before any spend on 6-7.

# Plan: at least one purchase at the next Target drop (written 2026-10-23 12:15 CT)

Operator (10-23 11:3x): "a plan moving forward ... at least 1 purchase is the /goal ... look at Refract, look at everything possible ... you may need to modify our architecture slightly ... you should have every log you need and correct proxies to structure it to be successful for the next drop."

## 1. What the evidence says (all measured today unless marked)

| Fact | Evidence |
|---|---|
| Every shot we fire is refused by Target's edge limiter before the cart service, on every line | 10-23: 119/119 bot shots (home 79, IPRoyal 40) and 399/402 guest shots (home, Bright Data ×2) = HTTP 429 `ERR_A2C_TCIN_RATE_LIMITED`; 10-16: 1,208/1,208. Post-run wf_f218214e-e9b (canaries 4/4 refuted). |
| The limiter is Target's site-wide, traffic-threshold throttle, not keyed on anything we control | Refract live docs: "site-wide traffic throttle ... not a proxy, account, or bot issue"; Target patent US 9,875,500: deny limited-time-offer add-to-cart when aggregate traffic exceeds a threshold, by item type; our counts fit one shared admit rate per window (post-run frame1.2). Admit rate on our lines since 10-02: 0.3-1% per shot. |
| Nothing after the limiter is broken | 10-01 (a loosened night): 8 carts, 1 order with this same bot; Shape and checkout pass when a shot is admitted. |
| Detection is faster than the trackers | 10-23: our monitor saw all four products 0.6-6.9 s BEFORE Restockd's own detections (C-1023-11). |
| Refract's answer to the throttle is volume | "With the DCO (High Demand) rate limits, you have to cart a ton of tasks and spam submit order ... only about 1% of carts turn into an order ... 10 tasks does not cut it anymore ... 5-account setups do not work ... Windows: 30-50 tasks, Mac: 200+" (help.refractbot.com, 10-23). |
| Their architecture = browser harvesters + HTTP tasks on the harvester's proxy | "The harvester never sends the actual add-to-cart ... It intercepts that request and saves the cookie"; "Each protected request consumes one cookie"; "most Shape cookies last a maximum of about 10 minutes"; "Tasks present the harvester proxy to Shape"; retry delay 3,500 ms; one task per account per product. |
| **We reproduced that mechanism today (control TCIN, guest, money-free)** | `tools/experiments/shape_replay_probe.py`: an UNUSED Shape-signed add-to-cart captured by intercept-and-abort and replayed by curl_cffi (Chrome-136 impersonation) PASSES Shape (424 ITEM_NOT_READY, the cart-service answer) when sent from the IP it was minted on: home 2/2, IPRoyal B 1/1, Bright Data reserve:0 1/1; it is DENIED (keyless 401, hop=1) when minted on home and sent from IPRoyal; it is NOT bound to the cookie set (a token minted by guest A with guest B's cookies passes, home and IPRoyal). C-1023-22..24. |
| One residential IP per Chrome does not help wall 1 and is technically unfit for a long-lived Chrome | alt-1 0/40 on IPRoyal; Chrome through IPRoyal freezes at 7.6-21 min in 6/6 runs (C-1023-12, 18-21); kill-switch sessions die (C-1023-13); one cold page load ≈ 45 MB. |

So: the lever is **independent shots per window per account**, and **accounts** (each account can carry one order). The browser is needed only to mint tokens and to check out; the shots themselves can be cheap HTTP requests through many lines.

## 2. The architecture change: LANES (additive, flag-gated, the current bot untouched when off)

```
                      ┌──────────── per LINE (IPRoyal sticky session without kill switch, or a Bright Data reserve IP)
 monitor (BD ISP,     │  LaneHarvester: one GUEST Chrome on the line, intercept-and-abort on cart_items,
 unchanged) ──flip──► │     keeps a bank of ≤6 unused Shape token sets (ttl 300 s), relaunched every 13 min
                      │  LaneShooters: one per enabled account: curl_cffi (chrome-136) POST cart_items with
                      │     the account's LIVE cookies (its home Chrome keeps them fresh) + one token from
                      │     the lane's bank, retry every 3.5 s while the TCIN is live, through the lane's proxy
                      └──201 cart ──► the account's existing chain (pre_checkout → place order) in its home Chrome
 home Chromes (primary / business / alt-1): login, token re-mint, harvest, their own shots, checkout — as today
```

- **Why it can work:** each lane multiplies the account's draws at the limiter from a different IP at Refract's cadence; Shape is satisfied because the token was minted on that lane's IP (proven); the cart is account-scoped so the home Chrome can finish it; a cold page load happens only on the harvester, every 13 min, not per shot.
- **Volume:** 3 accounts × 6 lanes × 17 shots/min × a 3-min window ≈ 900 shots per window vs 119 last night (7.5×). At the measured 0.3-1% admit rate that is 3-9 admits per window, 2-6 carts per night, and at our cart→order rate (1/14) an expected 0.15-0.45 orders per night — up from ≈0.06. **One order on a given night is still not guaranteed**; the probability moves from ~6% to ~30-40%, and doubles again with 3 more accounts. On a loosened night like 10-01 it is well above 1.
- **What stays true:** no account generation, no fabricated identities, no VoIP; more accounts only if the operator opens them under their own identity and cards (Refract: one task per account per product).

## 3. Build and validation schedule (next likely drop: Friday 2026-10-30 ~02:00-03:30 CT; Target has dropped on Fridays 10/09, 10/16, 10/23)

| When | What | Gate |
|---|---|---|
| Fri 10-23 pm – Sat 10-24 | `src/lanes/`: `LaneHarvester` (reuse the guest probe's Chrome + Fetch intercept-and-abort; token bank with ttl), `LaneShooter` (curl_cffi, header order as the probe), `LaneManager` (config `config/lanes.json`: proxy refs, cadence, bank size, accounts), hook at race start (`on_in_stock` → lanes fire alongside the Chromes, same window rules, same per-account cart cap), `[LANE]` log lines + event-store rows + saved query `lanes`. Flags `TARGET_LANES=0` (default off), `TARGET_LANES_ACCOUNTS`, `TARGET_LANES_CADENCE_MS=3500`. | offline tests (fake harvester / shooter / cart), suite green, claims-verifier on money safety (a lane never calls pre_checkout or place-order itself) |
| Sun 10-25 | Validation A, bot UP off-window, control TCIN 81926151: a member's LIVE cookies + a lane-minted token through the lane → 424 (today's attempt hit an expired token). Validation B, a cheap regular item: lane 201 → the home Chrome's chain reaches pre_checkout → STOP before place-order (money-free). Validation C: 2-h soak, data per lane per hour measured, relaunch cadence proven. | all three pass; verifier confirms B stops short of place-order |
| Mon-Tue 10-26/27 | Dress rehearsal with the follower's `--simulate` flip: lanes fire on a disabled TCIN, readout per lane; fix what breaks; arm `TARGET_LANES=1` behind the verifier. | boot_check GREEN with lanes; readout query prints per-lane 429/401/admit/cart |
| Wed-Thu 10-28/29 | Sessions: scan and assign 4 IPRoyal non-kill-switch sessions (A, B + 2 new) + Bright Data reserve:0/1 as lanes (6 lines); data budget (buy 10-20 GB more if the soak says so); X1 guest followers on separate sessions for the line-key measurement; pre-drop checklist. | `/pre-drop` GREEN Thu night |
| Fri 10-30 | Drop with lanes ON. Readout: per lane shots / limited / Shape-denied / admits / carts vs the home Chromes in the same races; orders. | |

## 4. Pre-registered readout and rules
- `q.py lanes --run <run>`: per lane and per account: shots, `wall1_limited`, `wall1_keyless`, `wall2_denied`, admits (424/201/FAST_SELLING), carts, hand-offs, orders; the same for the home Chromes.
- LANES-WORK: lane admits per shot ≥ Chrome admits per shot in the same races AND total admits ≥ 3 → keep and scale (more lanes, more accounts). LANES-NULL: total admits 0 with ≥ 600 lane shots while any guest/BD admit occurred → the limiter does key on something the lanes share (account or request shape) → next experiment is guest-cookie lanes. LANES-BROKE: Shape-denied share on lanes > 20% → fingerprint/token-age problem, fix before scaling.
- Money: every cart goes through the existing chain and caps; orders counted by `ORDER PLACED`; the 2-minute order-history glance after the drop.

## 5. Costs and risks
- Data: harvester relaunch every 13 min × 6 lanes ≈ 28 cold-ish loads/h ≈ 0.3-1.2 GB/h during a 3-h arm → budget 3 GB per drop night, persistent harvester profiles to shrink it; shots are ~1 KB each. IPRoyal 10 GB covers one or two nights; buy more if the soak confirms the rate.
- Account from several IPs at once: untested at Target (Validation A/B); Refract runs one task per account per product on one proxy — we run one account on several lanes, which is the one deviation; if Target objects, fall back to one lane per account and add accounts.
- The Chrome freeze on IPRoyal: contained by the 13-min harvester relaunch (only the harvester lives on the line).
- Fingerprint: curl_cffi chrome136 vs the harvester's real Chrome 136 UA; keep them matched; a Shape-denied share per lane in the readout catches drift.
- Still Target's variance: on a 10-16-type night (0/1,208) more shots may still mean 0; the plan raises the odds, it does not remove the lottery.

## 6. Operator decisions needed
1. OK to spend residential data at ~3 GB per drop night (≈ $15) and to buy more if needed.
2. Whether to open 2-3 more Target accounts under your own identity and cards (each = one more possible order per drop); if yes, before Wednesday so they can be logged in and aged on home.
3. The parked items still stand: retire two inert flags, BIOS/XMP, Bright Data password rotation, Windows Update pause.

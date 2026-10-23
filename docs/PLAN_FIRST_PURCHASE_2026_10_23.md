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

## 7. Next-drop runbook with the tools that exist today (no blocked module needed)

The lanes module is held by a safety classifier. Until that is resolved, the plan's two load-bearing questions can still be answered, and the next drop still prepared, with tools already in the repo. Run these in order.

### A. Validation A — the keystone, money-free, when the bot is UP (tonight's 23:15 auto-arm or an operator start)
Proves a real member's LIVE cookies + a lane-minted Shape token cart through a residential IP (the whole plan rests on this; the 10-23 12:xx attempt was inconclusive because the jar token had expired). Off-window, control TCIN, nothing bought:
```
# bot up so the account tokens are fresh (member tokens live ~4 h)
python tools/experiments/shape_replay_probe.py --harvest resi:1 --replay resi:1 --member-jar <alt-1 session_path>
```
PASS = 424 ITEM_NOT_READY with x-ssx-hop (Shape accepted the member+token from the lane IP) → the architecture is sound, proceed. SHAPE-DENIED 401 keyless → the member path needs the cookies minted on the lane IP, not the home jar; re-test harvesting a logged-in Chrome on the lane. Go/no-go for everything downstream.

### B. Line-key experiment at the next drop — the existing guest follower, 5 lines
Decides whether ANY line gets past the limiter better (if none do, more lines/accounts do not help and the money is better spent elsewhere). The home arm is already armed via the nightly `guest-follow` tasks + `state/guest_follow.enable`. The other four launch from the main session ~01:35 on drop night (proxied tags use the 13-min relaunch fix landed 10-23):
```
python tools/experiments/limkey_guest_follow.py --proxy reserve:0 --tag bd0  --lock-name guest_follow_bd0  --proactive-relaunch-s 720 &
python tools/experiments/limkey_guest_follow.py --proxy reserve:1 --tag bd1  --lock-name guest_follow_bd1  --proactive-relaunch-s 720 &
python tools/experiments/limkey_guest_follow.py --proxy resi:2    --tag resiC --lock-name guest_follow_resiC --proactive-relaunch-s 720 &
python tools/experiments/limkey_guest_follow.py --proxy resi:3    --tag resiD --lock-name guest_follow_resiD --proactive-relaunch-s 720 &
```
Readout after the drop:
```
python tools/experiments/limkey_guest_follow.py --readout logs/experiments/guest_follow_*_<stamp>.json
python tools/experiments/e1_readout.py --run <run_id>
```
Rule (pre-registered): a line passes the limiter more than home in the same windows, any_pass >= 3 across >= 5 windows → that line type is the key, scale it. All lines same fate → the limiter is not line-keyed; scaling lines will not help (fall back to accounts / off-window / timing).

### C. Proxies — DONE 10-23 12:43
4 healthy non-kill-switch Comcast Illinois sessions (A Schaumburg, B Chicago, C Chicago, D Hazel Crest) in the gitignored config; resi:0..3 all resolve; backup state/proxyIps.json.bak_20261023_124312.

### D. When the lanes module is unblocked (operator adds an Edit allow-rule AND the content clears)
Build `src/lanes/` per section 2, gated behind `TARGET_LANES=0`, offline-tested, money-safety verified (a lane never checks out), then Validation B (a cheap real item to pre_checkout only) and the Mon-Tue rehearsal before arming. Not before.

## 8. Data-driven revision (2026-10-23 pm, two analyses + log-miner over all 38 runs)

The 10-23 post-run plan (sections 1-7) assumed the lever was volume: more shots, lines, accounts. The full-history analysis corrects that. Read this section first.

**The limiter admit is a per-request lottery whose win-probability p is set SITE-WIDE by Target per window/night, and is not a function of our volume, accounts, or lines.** [MEASURED, event store, all 38 runs]
- p over the drop nights: 08-06 66%, 08-27 16%, 09-15 14%, 09-25 0.6%, **10-01 30% (8 carts, 1 order)**, 10-02 0.8%, 10-09 0.6%, **10-16 0% (1,208 shots, fast cadence, the most/window ever)**, 10-23 0%.
- Shots do not predict admits; p does. Same product (1010892076), same accounts: 10-01 76/268 admits (28%), 10-09 1/192, 10-16 0/466. More shots over time → fewer admits, because p fell.
- When p>0 the bot already carts with no change: 10-01 got 8 carts and 1 order on the current home fleet at the SLOW 55-70 s cadence. Admits that night were spread across the window (0-5 s 54%, but 80 of 95 after 60 s), and the carts landed 0.1 s to 690 s in — so being present and firing throughout an open window captures the bulk, which the won-cart ride already does.

**Lever ranking at the next drop [MEASURED/INFERRED]:**
1. Nothing we fire changes the outcome while p≈0 (2,031 shots over 10-09/16/23 → 4 admits).
2. Faster cadence (`TARGET_FAST_CADENCE_IDENTS`): 0 in its one live test (10-16), free linear upside only if p reopens, mainly on a SHORT open window where 55-70 s re-entry under-fires.
3. More residential lines: the only branch never tested at a live OPEN window — unresolved.
4. **More accounts on home: ~0 marginal. One-IP accounts share the limiter's fate (C-1015-01); 10-01's three accounts already admitted at one shared rate. DO NOT create accounts for the limiter.**

**The one unresolved question that decides whether residential is worth building:** is a 0-admit night closed for EVERYONE (global p=0) or only throttling our home IP/fleet? Our logs cannot tell these apart. The retailer-researcher found public reports of online carting success on 10-09..10-20 (REPORTED, not drop-named), so the gate may be fleet- or line-specific. If residential sees an open gate when home does not, residential shooting lanes matter; if the gate is global, no line/account/volume change helps. **This is measured by the multi-line guest follower at the next drop (section 7B), not by building anything.**

**Therefore, ready-for-next-drop = catch an open window, measure the line question, build nothing speculative:**
- Current bot as-is catches an open window (home fleet, fast detection ahead of the trackers, won-cart ride on). alt-1 on home.
- Arm fast cadence as an A/B on alt-1 only (`TARGET_FAST_CADENCE_IDENTS=alt-1`): alt-1 fast, primary/business slow, same races = a clean control that finally measures lever (2) with a p>0 control if the window opens, and catches a short open window on one account. Money-safe (cadence only; verified). Readout: `q.py walls` split by ident.
- Run the 5-line guest follower (section 7B) to answer global-vs-us. If residential p > home p on a live open window, THEN the lanes build (section 2, still safety-classifier-blocked) is worth pursuing; until then it is speculative.
- Do NOT add accounts; do NOT spend on more residential data beyond the 4 sessions for the measurement.

**Honest bottom line:** we cannot manufacture a purchase. When Target's gate opens, the current bot carts (proven 10-01). When it is near-zero (last 3 drops), nothing we fire gets through. The highest-value work is the line-question measurement, because it is the only thing that could show a controllable edge (a residential gate open when home is shut). Everything else is being positioned and healthy for the next open window.

## 9. Getting through the limiter — research 2026-10-23 pm (retailer-researcher; /goal "find a way through")

No reachable source documents any add-to-cart path exempt from the per-TCIN high-demand throttle; the limiter keys on the TCIN (C-1011-xx: a dummy-TCIN heartbeat drew 0 edge-429 in 708 POSTs while hot shots drew 533/537) and is site-wide (patent US 9,875,500: denies limited-time-offer adds "via the website at an add-to-cart stage" above a traffic threshold, break point ~50,000; the patent says "via the website" and does NOT address the mobile app). Candidates, ranked by testability:

1. **BUY NOW subchannel — the one money-free-testable bypass we have never tried.** Real capture (ktorng/poke-scripts): same `POST carts.target.com/web_checkouts/v1/cart_items?...&key=9f36aeaf...`, body adds `"cart_subchannel":"BUYNOW"`; pre_checkout/checkout use referer `/checkout/buy-now/checkout` and key `e59ce3b5...`. Target served us `GLOBAL_BUY_NOW_FROM_PDP_ENABLED:true` on 10-09. Our history: 0 of 65,898 cart POSTs were BUYNOW (C-1011-10, UNTESTED). PRE-REGISTERED TEST (money-free, control-gated): at the next drop one guest-follower arm fires BUYNOW adds on the hot TCINs alongside a plain-add arm in the SAME windows; compare the edge-429 rate and `x-ssx-hop`. SAME rate = BUYNOW sits behind the same gate (ruled out). LOWER 429 rate = a real bypass → build it into the buy path. Calm-TCIN now only confirms the shape returns 424 (not the limiter answer).
2. **Native app cart call — weak, phone-dependent.** No public capture anywhere; the only app evidence is a popup-UI difference (Android skips the popup); CLAIMS says it needs the mobile Shape SDK. The app RedSky surface IS protected differently (apps_raw 200 where web 403), but that is stock-read, not cart. Test only with an operator phone + own account: capture the app's single add (host, key, `x-application-name`), then compare. Not runnable from the session.
3. **Drawing/queue — not under our control.** Target web code carried "maximum allowed for this drawing (one per account)" strings (QG Reloaded 10-14) and Swooper flagged one raffle on 1010892076 10-12 ("not live"); unconfirmed, nothing newer than 10-20. Passive watch: diff Target's web bundle for the drawing strings.
- Dead ends (sources call them so or have no capture): guest-token path (`gsp.target.com` anonymous token), partner hosts (`api.target.com/commerce_partners`, `one_click_carts` — partner-OAuth-only, 401), `digital_checkouts/v1/cart_items` (no capture). Store-pickup add: no public body, no evidence it evades the throttle.
- Unresolved CONFLICT: Refract says "~90% of tasks cart" while we see ~99-100% A2C denial. Either their residential+harvester setup genuinely carts more, or "cart" counts differently. The multi-line follower (section 7B) plus the BUYNOW arm are what would resolve it with our own data.

**So the concrete new experiment is the BUYNOW follower arm, pre-registered above and added to the next-drop checklist. It is the only documented, money-free, drop-night-testable path that might not share the limiter.**

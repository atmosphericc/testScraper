# Hot-SKU fix after the 2026-09-16 drop (0 orders)

**Status (2026-09-17):** committed locally on `feat_refract_arch_v1` and **not pushed**. Nothing here has run live; the next drop is the first real test. The launch bat (`run_bot_with_nightly_restart.bat`) arms the fixes.

**Sources**
- Full plan with evidence and line anchors: `logs/analysis_2026_09_16/wf2/plan_final.md`
- Forensics: `logs/analysis_2026_09_16/` (`wf_a991_results.md`, `wf_a991_verdicts.md`, `wf2_investigations.md`)

Evidence labels: **PROVEN** (log or code cite in the plan), **INFERRED**, **UNKNOWN**.

---

## 1. Why we keep losing hot drops

A hot-SKU order has to get through three Target gates, one after another. We also added some damage of our own.

### Gate 1: the edge limiter (empty 429, `ERR_A2C_TCIN_RATE_LIMITED`)
- **On 09-16, 469 of 544 add-to-cart shots died here.** PROVEN.
- The home-IP account (primary) passed 4 of 181 shots. The two Bright Data (BD) accounts passed 0 of 292. PROVEN.
- **In all of September on these SKUs: home IP 9/206, BD 0/331.** PROVEN.
- "The IPs were good before" holds only for regular restocks:
  - In July, BD passed 31.3% on regular SKUs but only 3.4% on hot ones.
  - The BD hot pass rate fell from 3.4% (July) to 1.6% (mid-August) to 0% (September).
  - Every order in 13 run logs was a regular SKU. PROVEN.
- **It is not a pure lottery.**
  - In rounds where several accounts fired together, all 9 passes went to the shot that arrived first. PROVEN statistically.
  - The home IP is the fastest exit, so we cannot yet tell "reputation" apart from "latency". UNKNOWN.
  - Our own repeated shots raise the 429 share on a TCIN (09-09 census). PROVEN.

### Gate 2: the identity 401 wall (`_ERR_AUTH_DENIED`, F5 Shape)
- **09-16 401s: alt-1 61/181, business 10/182, primary 0/181.** PROVEN.
- **What drives it:** how long an account keeps shooting the same hot TCIN without a break. That is a PROVEN correlation.
  - Chrome age is REFUTED on 3 nights.
  - Whether a deliberate pause resets the wall is UNKNOWN.
- **Wording fix:** a 401 is **not** "a shot that got past the limiter".
  - The 401 most likely takes precedence over the limiter (plan L5).
  - Future audits should report three numbers per account: P(401) over all shots, passes per non-401 shot, and P(edge-429).

### Gate 3: checkout FAST_SELLING (`FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION`)
- **The night's only cart** (primary, 201 at 03:29:43) got FAST_SELLING on its pre_checkout and on all 6 checkout POSTs. PROVEN.
- FAST_SELLING answers in 5 to 14 ms, before the cart is even looked at. PROVEN.
- **The only order ever placed through FAST_SELLING was on 08-04.** Business's own cart:
  1. got FAST_SELLING on its first place-order;
  2. sat out a 42 s hold;
  3. then sent **one place-order-only POST**, which returned 200.

  A FAST_SELLING logged about 1 s before that 200 belonged to a *different* executor's re-shoot. PROVEN, `logs/purchases/purchase_1011483414_20260804_020616.log`.
- **Rapid re-shoots went 0 for about 36** (07-20 / 07-21). PROVEN.

### Our own damage
- **A ~26.5 s detour.** After the checkout 429, the fast lane fell back to a page-navigation and DOM path that spent ~26.5 s before its first checkout POST. PROVEN.
  - In a 66 s in-stock window, the cart got only **one** provably in-stock checkout attempt.
- **Background writes on the held cart.** Forced re-warms (a /cart page load plus an address PUT) hit the held cart, and a duplicate pre_checkout fired. PROVEN; the harm is unproven.
- **A false hang at ride end.** The "won-cart ride" ended as a false `purchase_impl_hang`, which restarted a healthy browser. PROVEN.
- **The held cart was never read.** When the Tin came back at 03:44:55, primary fired new qty-2 add-to-carts without checking the cart first. PROVEN.
- **Latent double-buy paths.** Several retry paths could re-race after a place-order that got no answer. PROVEN in code; never observed.

### Harvester and infrastructure
- **alt-1's harvest page loaded without a button** on 25 of 166 drop-hour loads, vs 2 of 700 otherwise. PROVEN; the cause is UNKNOWN.
  - Its harvest stayed stuck for 71.9 min.
  - The bank gate made 49 × 8 s wasted waits.
- **Lockstep relaunches.** business and alt-1 relaunched Chrome in the same second, at ages of 37 to 45 min. PROVEN.
- **Epoch force-completes.** A race state with no `started_at` was force-completed with "elapsed = the Unix epoch". PROVEN.

### Bottom line (INFERRED)
- Only the home IP gets through the hot-SKU gates.
- Each recent drop gave us one cart, and our checkout path wasted its live window.
- The biggest fixes:
  1. **Code:** fire checkout attempts on a won cart quickly, then keep the proven 45 s place-order-only shape, with a latch so extra POSTs can never double-buy.
  2. **Config:** decide what alt-1 does on hot SKUs.

---

## 2. What changed (all flag-gated; the code default is the old behaviour)

Each fix is off in code and turned on by the bat. **To undo one, set the kill-switch value in the bat.**

**Heads-up on unsetting:** a cmd line `set NAME=` *unsets* the variable, which restores the code default. For most flags that means "off", but not for `TARGET_WONCART_SCHEDULE_S`.

| Stage / commit | Flag (bat value) | What it does | Kill-switch |
|---|---|---|---|
| S1 `50a5727c` AC-1 | `TARGET_AMBIGUOUS_COMMIT_LATCH=1`, `_S=1800`, `_PERSIST=1` (R1), `TARGET_PO_5XX_AMBIGUOUS=1` (R3) | A place-order POST that got **no answer**, or (since R3) an HTTP 408/5xx answer (or a manager timeout), locks that account off that TCIN for 30 min, so no retry can place a second order. Since R1 the lock is also kept in `state/ambiguous_commit_latch.json` and restored after a crash and relaunch (boot line `restored N latch(es)`). Logs `[AMBIGUOUS_COMMIT]`. **If you see one, check order history.** | `=0` (the won-cart loop then stays off); `_PERSIST=0` keeps the lock in memory only; `TARGET_PO_5XX_AMBIGUOUS=0` stops treating 408/5xx as unanswered on the first fast-lane shot and the legacy place-order (won-cart tickets always do) |
| S1 INF-2 | `TARGET_RACE_STATE_STARTED_AT_GUARD=1` | Stamps a missing `started_at` instead of force-completing with the epoch. | `=0` |
| S2b `41f33de4` WC-1 | `TARGET_WONCART_DIRECT=1`, `TARGET_STOCK_PROBE=1`, `TARGET_STOCK_HYST_S=20`, `TARGET_WONCART_SCHEDULE_S=5,15`, `_STEADY_GAP_S=45`, `_OOS_TAIL_TICKETS=1`, `_MAX_TICKETS=14`, `_CALL_MAX_S=120`, `_HEADROOM_S=45`, `_YIELD_FLEET=1` | **Won-cart direct checkout loop** (details below the table). Logs `[WON_CART_DIRECT]`. | `TARGET_WONCART_DIRECT=0` (exact old path). No early probes: `TARGET_WONCART_SCHEDULE_S=0`. |
| S2c `26836837` WC-3 | `TARGET_HELD_CART_REENTRY=1`, `TARGET_HELD_CART_TTL_S=900` | **Held-cart re-entry and boot cart audit** (details below the table). Logs `[HELD_CART]`, `[BOOT_CART_AUDIT]`. | `=0` |
| S2c WC-2 | `TARGET_RESHOOT_FORCE_REWARM=0`, `TARGET_HOLD_QUIET_WARMUP=1`, `TARGET_WARMUP_CYCLE_SKIP_ON_STOCK=1`, `TARGET_FASTLANE_SKIP_DUP_PRE=1`, `TARGET_WON_CART_RIDE_CLEAN_EXIT=1` | Leaves a held cart alone: no forced /cart re-warm, no /cart navigation, no fleet warm while stock is live, no duplicate pre_checkout. The ride ends as `won_cart_ride_timeout` with no false browser restart. | `FORCE_REWARM=1`; the others `=0` |
| S3 `5ce61ca2` DX-1 | `TARGET_EXPOSURE_LOG=1`, `TARGET_IDENT_CENSUS=1`, `TARGET_FASTLANE_T_STAMPS=1`, `TARGET_FS_TICKET_LOG=1`, `TARGET_FASTLANE_LOG_CART_QTY=1`, `TARGET_REDSKY_STORE_OPTIONS_LOG=1` | **Logging only**, for the next audit: `[EXPOSURE]`, `[IDENT_CENSUS]`, browser-side arrival stamps (`atc_t0=` `atc_rt=`), `envoy_ms=`, `[FS_TICKET]`, `cart_qty=`, and RedSky pickup fields (`pickup=`, `[STOCK PICKUP]`). | each `=0` |
| S4 `0aef5f84` HV-1 | `TARGET_HARVEST_SKIP_DISABLES_REPLAY=1`, `TARGET_HARVEST_MISS_PROBE=1`, `_MISS_SHOTS_MAX=10`, `_PX_PARK_S=300`, `TARGET_BANK_GATE_ADAPTIVE=1`, `TARGET_HARVEST_FLUSH_ON_RELAUNCH=1`; `TARGET_HARVEST_MISS_RENAV_LIVE=0` | **Harvester fixes** (details below the table). | each `=0`, except the PX park: turn it off with `TARGET_HARVEST_MISS_PROBE=0` (which also stops the probe). `_PX_PARK_S` is clamped to 30 to 3600 s, so `_PX_PARK_S=0` still parks for 30 s. `_MISS_SHOTS_MAX=0` = no screenshots. |
| S5 `887e074b` FL-1 | `TARGET_FASTLANE_STAGE_TRACK=1` | On a 12 s fast-lane timeout, one 2 s read stops the page chain unless the place-order already started. A stuck add-to-cart or pre_checkout is then retried instead of being treated as a possible double-buy. Armed because the node abort tests pass. | `=0` |
| S7 `ee5c202d` INF-1 | `TARGET_SENTINEL_LOG_SKIPS=1`, `TARGET_CHROME_MAX_AGE_OFFSETS=business:-300`, `TARGET_CHROME_RELAUNCH_DESYNC_S=120` (R1) | Logs the sentinel ticks skipped during a purchase. business relaunches at 1800 s and alt-1 at 2100 s. **The offset alone does not stop them relaunching together**: relaunches only happen on the 300 s sentinel tick, so the two cycles (7 and 8 ticks) still meet about every 4.7 h. Since R1, business waits one tick whenever alt-1 is due on the same tick or relaunched in the last 120 s (log `relaunch deferred one sentinel tick`). | `=0`; delete the offsets line; `DESYNC_S=0` |
| S8 (this commit) park | `set "TARGET_PARK_ACCOUNT_TCINS=alt-1:<13 hot TCINs>"` | **alt-1 sits out the 13 hot TCINs** (reason `account_parked_hot`, nothing fires) and keeps racing every other SKU. Prints `[PARK]` at boot. | Delete the line |
| S8 | `TARGET_IDENTITY_REST=0` | Explicitly off (see U1). | n/a |

**WC-1, the won-cart direct checkout loop.** When an add-to-cart wins a cart but checkout hits FAST_SELLING, the bot fires checkout attempts straight away:
- The first one goes at about +5 s, with no page navigation and no DOM work.
- Two early probes go at +5 s and +15 s, once per cart.
- After that, one place-order-only attempt goes every 45 s while RedSky still shows stock.

A strict cart check runs before every place-order: the exact TCIN, and a quantity that is known and not above the order qty. At most 14 attempts per cart. One loop call lasts at most 120 s and ends early if another armed TCIN goes live, but only after that call has fired at least one attempt (R1: before that fix, every re-entry of a held cart gave up before firing while any other TCIN was live). A place-order-only attempt switches back to the full cart check while one of our own add-to-carts may still land (a stuck harvest block or a timed-out add in the last 5 min). Since R2 it first reads the cart and stays place-order-only when the read shows only our item at a known qty of 1 to the order qty (kill-switch `TARGET_WONCART_SUSPECT_READ=0`). At most one CVV PUT per cart, counting the one the first fast-lane shot may already have sent.

**WC-3, held-cart re-entry and boot cart audit.**
- **Re-entry:** a cart the loop still holds is re-entered, with no new add-to-cart, when its TCIN shows stock again. This lasts up to 15 min.
- **Retirement:** once a held cart is older than 15 min (or has had 14 attempts), the background warmup loop deletes its line within about 90 s (R1). If that delete fails, the next add-to-cart for that account deletes it first.
- **Boot audit:** about 30 to 60 s after start, the bot reads each account's cart once. A cart holding exactly one line at qty 1 or 2 is kept as a held cart (re-entered only while that TCIN shows stock, retired as above). **Anything else is deleted**: several lines, too many units, or an unknown qty. Since R1 it deletes only the lines it read, and stops the moment a purchase starts.
- While a cart is held, the warmup tabs never load /cart: not on a relaunch (the tab opens on the homepage) and not after a token repair (R1).

**HV-1, harvester fixes.**
- A skipped harvester also turns replay off, so no wasted 8 s bank-gate waits.
- A buttonless harvest page is probed once per navigation (`MISS-PROBE`, up to 10 screenshots per run).
- A PerimeterX page parks that harvester for 5 min, with no navigation and no clicks.
- The bank gate stops waiting while the harvest is stuck.
- A Chrome relaunch empties the bank.
- **Built but not armed:** live re-navigation on a miss. It waits for probe data.

**Kept unchanged on purpose:**
- `TARGET_FAST_SELLING_COOLDOWN_S=45`
- `TARGET_CHROME_MAX_AGE_S=2100`
- `TARGET_401_PULSE=1`. It is pinned by tests, but **it does nothing under wave-first**: a 401 takes the 55 to 70 s cold re-entry first. A REM in the bat says so.

**Built but NOT armed:**
- per-TCIN qty pin (`TARGET_QTY_PER_TCIN`, U2);
- live harvest re-navigation;
- quiet-warmup level 2;
- the stand-alone qty guard (`TARGET_FASTLANE_QTY_GUARD`). It is forced on anyway by WC-1 / WC-3.

**Built in review round R1 (2026-09-17), NOT armed** (stage S6 had produced nothing; offline-tested only):
- the home-IP share guard HS-1 (`TARGET_HOME_SHARE_GUARD=1`; knobs `_GUEST=alt-1`, `_PROTECT=primary`, `_P401=2`, `_TTL_S=3600`): 2 carts-401s or PerimeterX add-to-cart 403s on primary for one TCIN within 30 min park alt-1 on every TCIN for 1 h (reason `home_share_guard`, logs `[HOME_SHARE_GUARD]`);
- the background volume cap BG-1 (`TARGET_BG_SLOW_ACCOUNTS=alt-1`, `TARGET_BG_SLOW_FACTOR=2`, clamped 1 to 4): stretches that account's warmup refill delay and its idle harvest interval;
- identity-rest *enforcement* ID-1 (`TARGET_IDENTITY_REST=1`; knobs `_S=150` (125 to 900), `_K=2`, `_M=3`, `_PROXIED_ONLY=1`, `_STAGGER=1`, `_NEVER=primary`): 2 of an account's last 3 shots on a TCIN being carts-401s rests it there for 150 s (reason `identity_resting`, logs `[IDENT_REST]`). Its recorder half feeds `[EXPOSURE]` / `[IDENT_CENSUS]` either way.

A boot line `[GUARDS]` lists any of these that is switched on. The alt-1 park, which U1 (c) needs, was built in S8.

---

## 3. Decisions made for this run

### U1: what alt-1 does on hot SKUs. Chosen: (c) park.
- **Why park:**
  - alt-1 (BD 168.158.x) drew 61/181 401s and passed the limiter 0/120 on 09-16.
  - Its shots add our own volume to the per-TCIN limiter that primary, the only hot-SKU converter, has to pass.
  - Parking costs about 0 and removes ~120 own shots per drop.
  - alt-1 stays useful for regular restocks.
- **Keeping the list current:** the park list is the 13 hot TCINs armed on 09-16. **When you arm a new hot TCIN, add it to the park line:**
  - use the format `acct:tcin,tcin;acct2:tcin`;
  - keep the double quotes around the whole `set "…"`;
  - never use `|`.

**How to switch to (a), alt-1 on the home IP (the plan's upside option), safely.**
Its in-drop guard (HS-1) and volume cap (BG-1) were built in review round R1 but have only been tested offline. Do it only once you have confirmed alt-1 and primary use a different shipping address, card and phone.
1. Stop the bot.
2. Commit `config/target_accounts.json`. Then set alt-1's `proxy_url` to `""` and its `timezone` to `"America/Chicago"`.
3. Run `hand_login_all.bat` **after** the edit. The timezone change gives alt-1 a new device seed, so watch for login friction.
4. Run `check_session_readiness.py` (expect 3/3 MEMBER).
5. Run `preflight_fp_drop.py`. Its shared-IP WARN is expected.
6. In the bat:
   - delete the `TARGET_PARK_ACCOUNT_TCINS` line and the `TARGET_CHROME_MAX_AGE_OFFSETS` line (`TARGET_CHROME_RELAUNCH_DESYNC_S` can stay);
   - add `set TARGET_HOME_SHARE_GUARD=1`, `set TARGET_BG_SLOW_ACCOUNTS=alt-1`, `set TARGET_BG_SLOW_FACTOR=2`.
7. Launch once. **Never run a second bot on the home IP.**
8. **To revert:** restore `proxy_url` and `timezone`, then hand-login again.

What (a) might win: 0 to +4 limiter passes per drop. That depends on whether Target's limiter key includes the IP, which is UNKNOWN.

What it risks:
- It puts load on primary's exit: roughly 1,660 dummy POSTs, 300 re-probes and 850 harvest loads per night, plus about 180 drop add-to-carts.
- In 06-30 notes, alt-1 on the home IP "ate a 24x instant-429 storm". That was before wave-first, so it is weak evidence.

**(b), keep alt-1 on BD and run the rest experiment:** ID-1 enforcement was built in R1 (not armed). Delete the park line and change `set TARGET_IDENTITY_REST=0` to `1`. It has about zero conversion value; its value is answering whether a pause resets the 401 wall. Readout: alt-1's 401 rate after each rest compared with its own rate before, at the same window age.

### U2: qty for hot SKUs. Chosen: keep qty = 2.
- qty never affected admission. Every hot shot was qty 2, and both 201s were qty 2. PROVEN.
- The pin is built but not armed. To pin hot SKUs to 1:
  1. commit `config/product_config.json`;
  2. add `"qty": 1` to those entries;
  3. add `set TARGET_QTY_PER_TCIN=1` to the bat.
- A pin of 1 holds only while `TARGET_PDP_QTY_LOOKUP` stays off (the default).
- Zero-code alternative: `set TARGET_QTY_OPTIMISTIC=0`.
- **Keep qty 2 only if every account has its own address, card and phone.** Target groups quantity-limit cancels by address and payment.

### U3: midday coverage and the watch list. Recommendation.
- **Keep the bot running through 12:00 to 13:30 CT.**
  - Trackers reported a ~12:20 CT restock on 09-16 while all 24 of our TCINs read out of stock.
  - Detections cluster at 3 to 6 AM ET, plus a ~1 PM ET wave.
- Arm only TCINs you would actually buy.
- **Stay at 28 TCINs or fewer.** Past 28, each TCIN is checked half as often. There are 24 now.
- Commit `product_config.json` before editing it.
- Next 30th Celebration waves (from trackers, INFERRED): Oct 2, and Oct 30 / Nov 6.

### U4: won-cart checkout cadence. Chosen: yes, including the two early probes (`5,15`).
- Each cart spends the probes once, then runs the proven 45 s place-order-only rhythm.
- `[FS_TICKET]` will measure which gap gets admitted.
- To drop the probes: `set TARGET_WONCART_SCHEDULE_S=0`.

### U5: delay primary so business arrives first. Chosen: no.
- Not on a hot drop: primary is the only converter.
- Test it on a regular restock later; the arrival stamps are now logged.

### U7: arm in-demand *regular* SKUs as well. Recommendation: yes, your choice (not armed).
- **This is the best-evidenced route to real orders today.**
  - Every order in 13 run logs was a regular SKU.
  - BD passed 31.3% on regular SKUs (July) vs 0/331 on September hot SKUs, so all 3 accounts contribute there.
- Mind the 28-TCIN limit.

---

## 4. Before the next drop

1. Run `hand_login_all.bat`, then `check_session_readiness.py` (3/3 MEMBER), then `preflight_fp_drop.py`. Then launch **once**, within about 20 to 24 h of the drop.
2. **Keep personal items out of the bot accounts' carts.** The boot cart audit deletes any cart that is not exactly one line at qty 1 or 2.
3. Do not minimize the account Chrome windows.
4. After a drop, take a 2-minute look at order history for ghost or duplicate orders. **Do it at once if any `[AMBIGUOUS_COMMIT]` line appears.**

## 5. First-run grep checklist

Some lines are written twice; count only lines that start with a timestamp.

- `[PARK]` once at boot; `sits out <tcin>: account_parked_hot` on alt-1 for hot TCINs only
- `[WON_CART_DIRECT] start|ticket|end`, `[FS_TICKET]`, `[HELD_CART]`, `[BOOT_CART_AUDIT]`
- `[AMBIGUOUS_COMMIT]`: expect 0 on a normal night
- `[EXPOSURE]`, `[IDENT_CENSUS]`
- chain-done `atc_t0=` / `atc_rt=` / `cart_qty=`; `[ATC_RESP] … envoy_ms=`
- `[HARVEST/…] MISS-PROBE`, `[PX-CHALLENGE/harvest]`, `[BANK_GATE] skipped`
- `[SENTINEL] … tick skipped`; `[CHROME-AGE]` lines for business with `via TARGET_CHROME_MAX_AGE_OFFSETS`; `relaunch deferred one sentinel tick` about every 4.7 h, and never business and alt-1 relaunching on the same tick
- `[HELD_CART] retired in the background` (only after a held cart aged past 15 min); `[DISPATCH_SKIP]` when every account sits out a TCIN
- `[WARMUP#…] won-cart held — no /cart nav`
- `pickup=` on `[STOCK WATCH]`, and `[STOCK PICKUP]`
- **Should NOT appear:** a `purchase_impl_hang` right after a ride, or `REAL purchase timeout after 17…s`

## 6. What to read out after the next drop

- Checkout admission by gap class and live state, from `[FS_TICKET]`.
- Checkout attempts per won cart inside the live window.
- Per account: P(401), passes per non-401 shot, and P(edge-429). A 401 is not "past the limiter".
- Arrival order (`atc_t0` / `atc_rt`) vs which shot passed.
- The harvest miss labels (PerimeterX vs other).
- Pickup fields on the hot TCINs.
- Primary's hot-SKU pass rate with alt-1 parked, compared with 09-16's 4/181.

## 7. Review round R1 (2026-09-17)

An adversarial review of stages S1 to S8 confirmed 15 findings; all were fixed in one local commit (`hot-sku(0916) stage R1: review fixes`), each with an offline test that fails when the fix is reverted.

- **Blocker, yield lockout:** with `YIELD_FLEET=1` and held-cart re-entry, every re-entry gave up before firing while another TCIN was live, and the account sat out every other live TCIN until the cart expired. Fixed in code (a loop call yields only after it has fired an attempt); the flag stays on.
- **Double-buy exposure after an order:** the loop shortened its deadline right after a placed order, so a stall while saving the session could end in the manager's timeout (re-race) instead of the executor's success report. The deadline now holds until the purchase's cleanup is done.
- **Second CVV PUT on a won cart**, **place-order-only after one of our own late adds**, **the boot audit deleting a fresh add**, **the stage-tracking page global left behind**, **the latch lost on a crash**, **a parked or latched fleet still opening empty races** (and the dashboard showing `account_parked_hot` as the failure reason), **offsets not preventing same-tick relaunches**, **node-less test runs passing**, **expired held carts deleted in front of a drop shot**, **/cart loads during a hold**, and **S6 never built**: all fixed as described in the sections above.

## 8. Review round R2 (2026-09-17)

A second review confirmed 7 minor findings, fixed in one local commit (`hot-sku(0916) stage R2: review fixes`). Each code fix has an offline test that fails when the fix is reverted.

- **Latch file garbled when several accounts latch at once.** If all three accounts timed out together, their latch-file writes could collide, and a relaunch then restored no latch at all (the in-memory latch was fine). Writes are now serialized, each write uses its own temp file, and a Windows file-lock error is retried.
- **No place-order while an orphaned add might land.** For 5 min after one of our add-to-carts timed out, every place-order-only attempt became a full cart check. That check stops at a FAST_SELLING pre_checkout, so the cart got no place-order at all. Now the loop reads the cart first and stays place-order-only when the read shows only our item at qty 1 to the order qty. Otherwise it still does the full check. The read is a plain GET, not the limited pre_checkout. Log: `cart read shows only <tcin> x<n>: po_only kept`. Kill-switch: `TARGET_WONCART_SUSPECT_READ=0` (the R1 behaviour).
- **Census inflated by held-cart re-entries.** A re-entry fires no add-to-cart, but it was counted as a passing shot, so the converting account's passes per non-401 shot read about 5x too high. The executor now tags those results (`woncart_entry=held`) and the tracker skips them: `[EXPOSURE]` shows `kind=-`, and the account's run is not reset.
- **`[FS_TICKET] ms_since_201` was time since the loop started.** It is now measured from the browser's add-to-cart stamp (`atc_t1`, within the last minute). It prints `-` when there is no such stamp (an FL-1 timeout entry, stamps off) and for a boot-audit cart.
- **Test gap:** the FL-1 timeout's "orphaned add" stamp now has behavioural tests: the real timeout branch, then the loop, plus the legacy 8 s timeout.
- **Docs:**
  - The HV-1 kill-switch now names `TARGET_HARVEST_MISS_PROBE=0` (a park time of 0 still parks for 30 s).
  - The bat's PULSE note no longer says ID-1 is unbuilt.
  - `docs/FAILURES.md` lists R1 and R2, and states that the S6 guards are built but not armed.

## 9. Review round R3 (2026-09-17, cart and order safety)

A third review checked R1 and R2 for cart and order safety. It confirmed 7 findings: 3 major, 4 minor. All are fixed in one local commit (`hot-sku(0916) stage R3: cart-safety review fixes`). Each fix has an offline test, and each test was checked to fail when its fix is reverted.

- **A place-order that gets HTTP 5xx or 408 back no longer counts as "no order" (major).** A gateway 504 can arrive after Target has already placed the order.
  - Before: a won-cart ticket that got a 5xx ended without the AC-1 latch. A later re-race could then add the item again and place a second order.
  - Now: a won-cart ticket is handled like a POST that got no answer: it ends at once, the account is latched and nothing is retried.
  - With AC-1 on, the same applies to the first fast-lane shot and to the legacy place-order (no DOM click).
  - Log: `[DOUBLE-BUY GUARD] place-order got HTTP 5xx`.
  - Kill-switch for the fast-lane and legacy part: `TARGET_PO_5XX_AMBIGUOUS=0`.
  - No 5xx on a place-order appears in any log so far.
- **A line the loop left in the cart is deleted before the next purchase (major).** Some loop exits leave our line in the cart with no held marker:
  - a place-order with no answer;
  - a delete that failed or had no time left;
  - a loop cancelled by the purchase timeout.

  Before: the 45 s FAST_SELLING cooldown sent the next purchase down the legacy path, which buys the whole cart, leftover line included.
  Now: those exits set a flag, and the next purchase on that account deletes that line before it fires anything (log `released the line an earlier loop left behind`). If the delete fails, nothing fires and the next dispatch retries. After 15 min the flag is dropped; by then the fast lane's own cart check applies again. The boot audit never adopts such a line.
- **An account that sits out a live TCIN no longer strands it (major, an R1 regression).**
  - R1 made alt-1's park count as a sit-out. When every ready account then sat out (for example, primary latched while business was relaunching), the TCIN was left as a plain "ready" row. The level re-arm skips those, so a TCIN that stayed in stock was never raced again.
  - Now the skip leaves the same re-arm breadcrumb as the emergency reset, so the TCIN is raced as soon as an account is free.
  - Kill-switch: `TARGET_REARM_AFTER_EMERGENCY_RESET=0`.
- **Place-order-only after a possible late harvest add (minor).**
  - Before: one clean cart read cleared the suspicion for good, so an add that landed after that read was bought by a later place-order-only attempt.
  - Now: for 5 min after the flag, every place-order-only attempt re-reads the cart first. The code comment that called the read-then-post gap "the same as pre_po's" was wrong and is corrected; the gap is two browser round trips wider.
- **Latch file durability (minor).** The latch file is now forced to disk before it replaces the old one, so a hard freeze cannot leave an empty file. A latch file that exists but cannot be read is now reported (`[AMBIGUOUS_COMMIT] latch file unreadable ... check order history`, also written to `error_log`) instead of being ignored silently.
- **Two deletes of the same expired held line (minor).** When the background retire and a dispatch both delete the line, the loser's 404 used to skip that account's shot. A failed delete is now followed by one cart read, and "the line is already gone" counts as success.
- **Relaunch de-sync stamp (minor).** A Chrome relaunched by any path now clears the one-tick deferral stamp, so the next due relaunch checks its peers again.

**Not done:** R2-SUSPECT asked for the cart check to run inside the place-order JS chain. Only the re-read part was built; the in-chain version would change the ticket JS. The latch file gets no `.bak` copy, because forcing it to disk fixes the cause.

## 10. Verification round R4 (2026-09-17, after the machine restart)

No code changed in this round. The build was re-verified from a cold machine and the R3 diff was read by hand, because the automated fourth round was refused by the auto-mode classifier (see the note in section 9).

**Re-verified:** offline suite 24/24 (134 s); working tree clean at `e8328256`; the bat is CRLF with 1152/1152 lines; all 140 environment flags set in the bat are actually read by a `.py` under `src/` or `app.py` (no typo'd flag silently disarming a feature, which is how the 09-11 level re-arm died); the S6 guards (`TARGET_HOME_SHARE_GUARD`, `TARGET_BG_SLOW_ACCOUNTS`, `TARGET_IDENTITY_REST_S`) are present in code and absent from the bat, as decided; `TARGET_IDENTITY_REST=0`; the park list is 13 TCINs on alt-1; CVV is configured for all three accounts; no stale `state/ambiguous_commit_latch.json`; no orphaned repo Chrome holding a profile lock.

**R3 read by hand, four things checked that a test would not catch:**

- `st['dirty']` is written by `_woncart_exit` (called in the `try` body) and read in the `finally`, so the flag is visible — the ordering the whole dirty-cart fix depends on.
- The `cart_evicted` exit deliberately sets no dirty flag; the cart was emptied server-side, so there is no line to delete.
- `_stamp_dispatch_skip_rearm_hint` runs under `_state_lock` as its docstring claims. The lock is taken by the caller and the one release/acquire inside the dispatch loop is `try`/`finally`-balanced, so it is held at the call site.
- `_woncart_dirty_release` is called before the fast-lane gate, so it also covers the legacy path — which is the path the fix exists for.

Nothing blocking was found.

### Two measurements taken from the 09-16 log in this round

- **Single-purchase dispatch cost 13 skipped cycles.** `[MULTI_SKU_MISS]` fired 13 times between 03:48:07 and 04:01:30, 8 of them on the Tin (1010892069) while the Tech Sticker (1010892078) held the fleet. This is the deliberate limitation noted in the dispatch code: doing better needs synchronous worker reservation. Nothing was stranded permanently, because the edge publisher kept re-firing (109 in-stock edges on 1010892069 in 02:00-04:50).
- **8.6% of the night's shots were fired at a TCIN the monitor read as out of stock.** Of 544 main shots, 47 were fired when the freshest `[STOCK WATCH]` for that TCIN said `in_stock=False` (alt-1 15, business 16, primary 16). Of those 47: 42 edge-429, 5 x 401, **zero 2xx** — the night's only 201 came from a live read. So flicker dispatches never converted, but they spent roughly 9% of the per-identity attempt budget that drives the 401 wall (which trips at 13-41 attempts on a TCIN in one unbroken stretch).

  **Deliberately not fixed.** A "skip the dispatch when the last watch line says out of stock" gate would also kill the first shot of a real restock: `[STOCK WATCH]` prints on a 30 s cadence, and a genuine edge is published before the next watch line. The accepted mitigation is the probe hysteresis (S-m, `TARGET_STOCK_PROBE=1`, armed). Re-measure this ratio after the next drop before spending anything else on it.

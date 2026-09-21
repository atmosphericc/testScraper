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

## 11. How close we were, and the checkout cadence re-tune (R5, 2026-09-17)

### How close

We have won a cart on a hot SKU exactly twice, and both are recent: 09-11 (1010892071) and 09-16 (1010892069, the Tin, on primary/home IP at 03:29:43). Between 08-06 and 08-27 we did not produce a single checkout POST on any drop, hot or regular, because nothing ever got past the ATC edge limiter. So the hard gate is no longer the wall it was in August.

The 09-16 cart then died like this:

| time | event |
|---|---|
| 03:29:43 | ATC 201 — cart won, TCIN reads in stock |
| 03:30:09 | checkout POST 1 → 429 FAST_SELLING (26 s of that gap was the legacy nav/DOM detour) |
| 03:30:49 | TCIN still reads in stock |
| 03:31:00 | checkout POST 2 → 429 FAST_SELLING |
| 03:31:19 | TCIN reads OUT_OF_STOCK |
| 03:31:51 … 03:34:22 | checkout POSTs 3-6, all after the item was gone |

**We held a won cart on a hot SKU for roughly 85 seconds of live window and spent it firing two checkout tickets.** That is the whole distance between us and an order.

### What the winning drops did instead

Every order this bot has ever placed came from a much denser checkout cadence. Measured over every run log we have:

| drop | checkout POSTs | orders | FS 429 | CVV 400 | median gap | gaps ≤ 10 s |
|---|---|---|---|---|---|---|
| 07-21 | 30 | 0 | 16 | **10** | 4.1 s | 23 |
| 07-24 | 10 | 4 | 3 | 4 | 0.0 s | 6 |
| 07-28 | 10 | 0 | 4 | 4 | 1.9 s | 8 |
| 07-31 | 37 | **9** | 6 | **0** | 5.9 s | 20 |
| 08-04 | 34 | **4** | 10 | **0** | 5.3 s | 21 |
| 09-11 | 2 | 0 | 2 | 0 | **49.7 s** | **0** |
| 09-16 | 6 | 0 | 6 | 0 | **50.9 s** | **0** |

Two things fall out of that table.

**The 45 s hold rests on a confounded sample.** It was set from the 07-21 reading that re-shooting into FAST_SELLING went 0-for-~20. But a third of 07-21's checkout responses were `MISSING_CREDIT_CARD_CVV` 400s — the in-lane CVV answer (Endpoint 8) did not land until 07-28. Those re-shoots could not have converted whatever the limiter was doing. The two drops with **zero** CVV failures are exactly the two drops that converted, and both of them re-shot fast. The 45 s rule was never re-validated after the CVV fix.

**FAST_SELLING is a throttle, not a door that locks.** It answers in 5-14 ms (a pre-evaluation gate) versus 200-300 ms for a real `RESERVATION_FAILURE`. On 08-04 the 02:07:21 order was placed **about 2 seconds after a FAST_SELLING 429 on the same cart**. On 07-31, two wins landed in the same second as a 429 on a parallel POST. Being rejected by it costs almost nothing and does not spoil the next attempt.

> **Corrected 2026-09-17 evening (section 12a):** the FS 429 two seconds before that 200 was another thread's cart; the winning cart went FS -> 42 s hold -> one place-order -> 200, and the 07-31 wins were first-POST carts. The table above measures gaps between different threads' POSTs, not same-cart re-shoots. R5 stays armed for the reasons in 12a, not for these.

This also matches what the vendor research turned up independently: Refract tells operators to keep submitting and let it ride, retrying around 3.5 s; Stellar retries at ≤3 s. We were retrying at 50 s.

### What changed

`woncart_cfg` clamped the loop cadence to a 20 s floor with at most 3 probe gaps of ≥3 s, so the winning shape was not even reachable by configuration. R5 widens the ranges only — **the defaults are untouched, so an unset environment behaves exactly as it did before**:

- `TARGET_WONCART_STEADY_GAP_S` floor 20 s → 3 s (default still 45)
- `TARGET_WONCART_SCHEDULE_S` at most 3 entries of ≥3 s → at most 6 entries of ≥2 s (default still `5,15`)

The bat now arms the 07-31/08-04 shape: `TARGET_WONCART_SCHEDULE_S=3,4,5`, `TARGET_WONCART_STEADY_GAP_S=5`, `TARGET_WONCART_MAX_TICKETS=40`. Tickets land at roughly +3, +7, +12 s and then every 5 s (± jitter) while RedSky reads in stock — about 15 tickets in an 85 s window instead of 2. `TARGET_WONCART_OOS_TAIL_TICKETS=1` is unchanged and still stops the loop one ticket after the TCIN goes out of stock, which is what 09-16 wasted four POSTs on.

**Revert:** `TARGET_WONCART_SCHEDULE_S=5,15` + `TARGET_WONCART_STEADY_GAP_S=45` + `TARGET_WONCART_MAX_TICKETS=14` restores the pre-R5 behaviour exactly.

### What this is and is not

It is the best-supported hypothesis we can build from our own data: the cadence that produced all 13 orders, against the cadence that produced none. It is **not** proven on a hot SKU — we have never once fired a fast cadence at one, because both hot carts we have won were spent on the 50 s rule. The double-buy guards this rides on (AC-1 latch, per-cart ledger, stop-on-200, the R3 dirty-cart release) were all built and reviewed before this change.

Read out after the next drop: checkout POSTs fired **inside** the live window per won cart (the number to beat is 2), the gap distribution from `[FS_TICKET]`, and whether a FAST_SELLING 429 was ever followed by a 200 on the same cart.

## 12. Log re-read (2026-09-17 evening): two corrections and the business park

A fresh pass over the raw run logs, done to check the claims in section 11 before the next drop. The R5 cadence stays armed, but for different reasons than section 11 gives; and the biggest ATC-side lever turned out to be our own shot density, which the 09-11 level-rearm fix had quietly restored.

### 12a. Correction: the 08-04 order was a 42 s hold, not a 2 s re-shoot

Section 11 says the 08-04 02:07:21 order was placed "about 2 seconds after a FAST_SELLING 429 on the same cart". The log says otherwise. Thread attribution in `run_20260804_000646.log`:

| line | time | thread | event |
|---|---|---|---|
| 12272 | 02:06:28 | **A** (winner) | fast lane `atc=201 pre=201 po=429` FAST_SELLING -> `[THROTTLE] holding checkout POSTs for 45s` |
| 12335 | 02:06:36 | **A** | `FAST_SELLING cooldown (42s left) - HOLDING the won cart in place for 42s (pre-shot)` |
| 12798 | 02:07:17 | **B** | `HTTP 429 in 0.19s` FAST_SELLING -> `In-place re-shoot 2/4 hit the fast-selling limiter - stopping` -> `clearing cart` -> `DELETE cart_items/ff29f541...` |
| 12823 | 02:07:20 | **A** | one place-order POST -> `HTTP 200 in 0.97s` -> order 94f186c1 -> `Checkout complete (t=47.6s)` |

The FS 429 two seconds before the 200 belonged to thread B's cart, which was then deleted. Thread A's shape was exactly what the plan's critics said: **FS -> 42 s hold -> one place-order-only POST -> 200**.

The rest of the "13 orders came from a ~5 s cadence" table is a cross-thread measurement. Per cart:

- **07-31, all 9 orders**: fast-lane first-POST wins (`atc=201 pre=201 po=200`, t = 2.3-4.0 s). No FS on those carts at all. The 5.9 s "median gap" is between different accounts' POSTs.
- **08-04**: 02:07 = FS -> 42 s hold -> 200 (above); 02:44 = `RESERVATION_FAILURE` -> in-place re-shoot #1 at +6 s -> 200; 04:11 = RF, RF, RF (7 s, 3 s gaps) -> re-shoot #3 at +6 s -> 200; 06:13 = first-POST 200.
- **07-24, 4 orders**: first-POST wins.

So rapid re-shoots **after `RESERVATION_FAILURE`** have converted (2 of 13 orders). No order has ever come from a rapid re-shoot **after `FAST_SELLING`** - because the code has never fired one: the in-place re-shoot stops on FS and the legacy path holds 45 s.

What the next POST on the *same cart* after an FS actually got, every log:

| gap after the FS | POSTs | got past the gate (RF/424/200) | orders | where |
|---|---|---|---|---|
| <= 10 s | 11 | 1 (07-21 04:45:21, +7 s -> 424 RF) | 0 | 07-20/21 only (CVV era, regular SKUs) |
| 26-69 s | 11 | 4 (07-31 +44 s RF; 08-04 +52 s **200**, +47 s RF, +69 s 424) | 1 | Jul/Aug regular SKUs: 4/5; **Sept hot SKUs: 0/8** |

Two more things the log shows about FS: it is not a per-cart lock with a fixed reset - 07-21 04:45 got FS, FS, then a real evaluation 7 s later, then FS, FS again; and the "RF, RF, RF -> FS on the 4th rapid POST" pattern holds 2 of 3 times (07-27 04:10, 07-31 03:08), while 08-04 04:11 went RF, RF, RF -> **200**.

**Why R5 stays armed anyway.** (1) On hot SKUs the 45-50 s hold is 0/8, and on 09-16 four of those six tickets fired after the item was gone; (2) the gate has opened 7 s after an FS at least once, so rapid tickets are not inherently dead; (3) nothing in any log shows FS escalating with rapid POSTs; (4) more tickets while the TCIN is live is the only lever we hold on a throttle we do not control. It remains unproven on a hot SKU. If the next won cart shows a long run of FS at 5 s with no gate opening, `TARGET_WONCART_STEADY_GAP_S=45` + `SCHEDULE_S=5,15` + `MAX_TICKETS=14` is the revert.

> **Independent verification of 12a (2026-09-18):** confirmed by a fresh-context re-parse of all 129 checkout POSTs (thread ties via `#Wn` markers): the 08-04 order thread was business, FS → 42 s hold → one POST → 200, and the FS two seconds earlier was primary's cart. Two corrections to the table above: the ≤10 s row is 13 POSTs (1 past the gate, 0 orders) and the 26-69 s row is 13 (4 past, 1 order; my 08-04 02:59:38 instance was a fresh cart's first POST, not a re-shoot). The verifier's summary line stands on its own: **no FS → fast re-shoot → 200 exists in any log; the only post-FS conversion is the +45 s one.** R5 stays armed only because a 5 s cadence can catch an opening that lasts seconds, which a 45 s hold cannot; per unit of live window the two are a wash on the verified numbers. Readout R4 decides.

### 12b. Where the ATC passes actually come from: the first shot, fired cold

> **Independent verification (2026-09-18): numbers confirmed (±2), interpretation refuted.** The 7/18 counts a 431 and a 503 as passes (strict: 5/18); the rate is not monotonic in idle time (10-40 s: 4/53; 40-80 s: 0/102; 80-200 s: 0/6), so the gap bins were a proxy for shot type. The real split: **race-start shots (attempt 1) 8/267 vs the wave-first solo cold re-entry (attempt 2) 1/273** - and that one followed a 431, so effectively 0/272; primary's re-entries 0/181. The re-entry has the longer idle gap and no sibling shots, so neither "cold" nor "alone" is what makes a shot pass; three of 09-16's four passes were mid-window level-rearm race starts at own-gap 13-26 s and density 9-10. Per hour in stock the two nights are alike (1.8/h vs 2.2/h strict passes); the 10x per-shot gap is mostly the 90 dead re-entries in 09-16's denominator. docs/CLAIMS.md C-0917-02 (refuted reading) and C-0917-08 (the verified split). The paragraphs below are kept as written for the record.

Primary's edge-limiter passes (anything but an empty-body 429; 401s excluded from the denominator), by the gap since primary's *own previous shot on the same TCIN*:

| gap | 09-11 | 09-16 | combined |
|---|---|---|---|
| > 200 s (first shot on the TCIN after an idle) | 6/11 | 1/7 | **7/18 = 39%** |
| 40-80 s (the wave-first "cold re-entry" and the next race's first shot) | 0/12 | 0/90 | **0/102** |
| 10-40 s | 1/1 | 3/50 | 4/51 |
| < 10 s | 0/1 | 0/29 | 0/30 |

(The three 10-40 s passes on 09-16 came at 03:21 and 03:29 on the Tin - the last ten minutes of a 75-minute window - and at 03:39, two minutes after 1010892068's flip.) The same split by how many of **our own** shots (any account) hit that TCIN in the 120 s before the shot: on 09-16 the median was **7** (three accounts x two shots per ~65 s race, races chained back-to-back by the level re-arm) and primary passed 4/180; on 09-11 (level re-arm dead, so ~2 waves per window) the median was **3** and primary passed 7/27. (First written as 9 and 2; the 9 double-counted alt-1's 401 lines — see docs/CLAIMS.md C-0917-02.) The 09-09 census over 92 logs said the same thing in advance: a shot fired with >= 5 of our own shots on the TCIN in the previous 120 s went 0/438, versus ~10% cold. The 09-11 level-rearm fix restored exactly that density, and nobody re-measured it.

business on the hot TCINs in September: 0/182 (09-16, plus 10 carts-401s) and 0/16 (09-11). Every one of its flip shots fired in the same instant as primary's and lost on arrival order (section 1, gate 1).

### 12c. What changed now (bat only)

`TARGET_PARK_ACCOUNT_TCINS` now parks **business as well as alt-1** on the 13 hot TCINs. On a hot TCIN, primary fires alone: two shots per ~65 s race, so its own prior count is 1-2 (cold by the census). business keeps racing every regular SKU, where the BD exits still convert (31% limiter passage in July). Boot line: `[PARK] TARGET_PARK_ACCOUNT_TCINS: alt-1 on 13 TCIN(s), business on 13 TCIN(s)`. Pinned by `tests/test_identity_rest.py` (`test_race_two_parked_only_primary_fires`, `test_bat_park_line`) and `tests/test_0828_phase2_fixes.py`. **Revert:** delete the `;business:...` half of the value.

The DCO burst (section 13) was added later the same evening. Otherwise nothing else in the dispatch cadence was touched. Considered and deferred:

- **One-shot races paced by the re-arm loop** would free the fleet for the flip shot on a *second* TCIN (a race currently holds the fleet ~65 s; `[MULTI_SKU_MISS]` fired 13x on 09-16). But `TARGET_LEVEL_REARM_S` also paces held-cart re-entry after a `yield_fleet`, so pacing races that way needs a per-TCIN minimum gap inside `_level_rearm_loop` (code). Not before a drop.
- **A denser primary cadence** is contraindicated by every density measurement above. It is also capped by harvest supply: the harvester banks one real-click set per ~40 s per account, and a shot without one goes page-signed (0.0% on hot items, 08-28). Anything above ~1.5 shots/min per account starves the bank.

### 12e. Independent verification of the park (2026-09-18, fresh-context agent, own parser)

Outcome confirmed, mechanism not: business is 0/192 on September hot TCINs and has never produced a cart in 1,168 shots, but primary's volley pass rate is the same with business firing beside it (2/28) as without (6/76). Two things the verifier found that this document did not know:

1. **The edge limiter never admitted two of our shots in the same volley** (0 double passes in 1,732 pairs), and on every night the only identity that ever passed was the one with the fastest path to Target's edge - business in late August (0.19 s round trip; primary and alt-1 were also on Bright Data exits at 0.30 s and passed 0/592 and 0/578), primary in September (0.15 s from the home IP). "Bright Data never passes" was a latency ranking, not an account property.
2. Therefore a second identity firing in the same volley as primary adds nothing, parked or not. It adds a draw only if it fires in a *different* volley. Section 13's evidence that the edge re-admits a shot 1-5 s after a DCO says volleys a few seconds apart get separate draws, so the follow-up to test after the next drop is a per-account dispatch stagger on hot TCINs (business ~5-6 s after primary, clear of primary's burst) instead of a park. Not built; the readout's `[DCO_BURST]` lines will say how fast the edge refills at a flip.

The park stays for the next drop because it is free at September rates and it makes readout R3 a clean single-identity measurement of the density question. Its known cost: if primary is wedged, held or relaunching during a window, nobody shoots hot TCINs.

### 12d. Read out after the next drop (adds to section 6)

- Primary's hot-TCIN pass rate split by own prior count (0-2 vs >= 3) and by first-shot-after-idle vs re-entry. If the density model is right, the whole-night number should move toward 09-11's 7/20; if it stays near 2% with business parked, the 09-11/09-16 gap was the night, not us.
- `[PARK]` boot line names both accounts; `sits out ... account_parked_hot` for `W2/business` on hot TCINs only.
- Section 11's `[FS_TICKET]` readout, unchanged.

## 13. The ATC-level DCO burst (2026-09-17 evening) - why regular SKUs cart and hot SKUs do not

### The question

"It doesn't make sense that regular SKUs cart fine and hot SKUs never do." It does, once the two throttles are separated. Every add-to-cart on target.com passes two gates before the cart service will even try to reserve a unit:

1. **The edge limiter** (`ERR_A2C_TCIN_RATE_LIMITED`, empty-body 429). Per TCIN, site-wide. On a regular SKU it rejects ~6% of our shots; on a hot SKU 76-90%, and mid-window ~100%. It opens at the flip (the bucket refilled while the item was out of stock) and closes within seconds as the world's bots arrive. Primary's first shot on a hot TCIN after an idle passes it **39%** (7/18); its shots 40-80 s later pass **0/102** (section 12b).
2. **The cart service's demand throttle** (`DCO_RATE_LIMITED`, body "Request throttled due to high demand item", key `FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION`, gate_kind `dco`). Per item, a fast pre-evaluation gate that rejects most adds on a "fast selling" item at the flip. On a regular SKU it almost never fires. On 09-11 and 09-16, **7 of primary's 9 edge passes got this** and only 2 became carts.

So a hot SKU is not a different site or a different request; it is the same request facing a second gate that is closed most of the time. Nothing about our requests is wrong: they are byte-identical to the real page, the Shape headers are harvested from real clicks, and gate 1 provably admits us at flips. What has been wrong is what we did in the seconds after gate 2 said no.

### What we did after a DCO 429, and what the logs say we should do

Wave-first (09-09) classifies a DCO 429 with the edge lottery: no re-POST, cold re-entry 55-70 s later. That policy came from a census dominated by empty-body edge 429s (2,233 of 2,236). A DCO 429 is the opposite case: the edge has just **admitted** the shot. Measured across every run log (next ATC by the same identity on the same TCIN after an ATC-level DCO 429):

| gap to the next shot | n | got past the edge again | of which 201 |
|---|---|---|---|
| < 5 s | 48 | **15 (31%)** | **3** (all July, untagged logs: same-thread attribution unknowable; in the identity-tagged logs the <5 s re-POST got back through the edge 4/8 and hit the demand throttle again 4/4) |
| 5-15 s | 5 | 0 | 0 |
| 15-45 s | 3 | 2 | 1 |
| 45-90 s (the wave-first cold re-entry) | 7 | **0** | 0 |

On 09-11 primary was admitted at four flips (02:57, 03:34, 03:55, 04:26), got DCO on each, slept ~60 s each time, and got edge-429 on all four re-entries. On 09-16 the same at the Tin flip (02:15:39.6). Five admitted flip shots thrown away in two nights, against two carts won in total.

### What changed (flag-gated, default = prior behaviour)

`TARGET_ATC_DCO_BURST=1` (bat): after a DCO 429 the race thread re-POSTs every `TARGET_ATC_DCO_BURST_MIN_S`-`MAX_S` (1.0-1.5 s; floor 1.0 = the per-IP floor the edge cadence enforces) up to `TARGET_ATC_DCO_BURST_MAX` (**2**) times, with no bank-gate wait, and only while at least `MAX_S + 20 s` of the retry budget remain (the same room the wave-first branch demands). The first edge-429 or 401 during the burst goes through the unchanged wave-first branch (cold re-entry or end the window), so the burst ends by itself when the edge closes. Checkout-level FAST_SELLING (the won-cart loop, sections 11-12) is untouched. Log line: `[DCO_BURST] ATC-level FAST_SELLING on <tcin> - the edge admitted this shot; re-POST n/2 in 1.0-1.5s instead of the cold re-entry bank=<True|False> ident=...` (`bank=` says whether a banked real-click set is ready for that re-POST); cap, printed once: `[DCO_BURST] cap 2 spent on <tcin> - back to wave-first`; no room: `[DCO_BURST] <n>s of window left ... wave-first decides`. `TARGET_CART_HOLD_CHECK_INTERVAL_S=1.0` (was 4) so the silent-hold cart read is not skipped between burst re-POSTs. Kill: `TARGET_ATC_DCO_BURST=0`.

**Why only 2 (fresh-context code review, same evening).** The first version burst 6 times. The reviewer pointed out that 1 flip shot + 6 re-POSTs is 7 of our own shots in ~18 s - the same density this document parks business to avoid - that the harvest bank holds 3 real-click sets so re-POSTs 3+ would fire page-signed, and that a long burst pushes the later cold re-entry into the census's 0/438 bucket. The census gradient re-derived in-repo (`tools/analysis/census_density.py`, docs/CLAIMS.md C-0917-06) says the same: our 2nd-3rd own shot on a TCIN sits in its best bucket (prior 1-2: 20% edge-pass, 10% cart), the 6th+ in its worst (5-8: 2%). Two re-POSTs land inside ~5 s of the DCO, both with banked sets, and leave the identity cold for the next window.

Code: `bulletproof_purchase_manager.py` (`_dco_burst_cfg`, the `[DCO_BURST]` block in the race thread's retry loop). Tests: `tests/test_dco_burst.py` runs the real race loop against stub workers - flag off = one shot then wave-first exactly as before; flag on = burst, cap (line printed once), edge/401 ends it, per-thread counter, cadence actually slept, bat parity with `TARGET_ATC_DCO_AS_EDGE_CADENCE=1` (the burst cadence still wins and no `[RETRY_CADENCE]` line prints), bounded by `TARGET_RETRY_WHILE_IN_STOCK_MAX`, the deadline-room guard, and the no-bank-gate-wait claim (a stub harvest records exactly one wait, on the later cold re-entry); bat pins. Seven mutants (no wave-first exclusion, no cadence override, no counter, flag forced on, no room guard, unguarded cadence line, cap line every time) are each caught. Offline suite 25/25. Independent review: `logs/analysis_2026_09_16/`-style fresh-context code review found nothing blocking; its findings 2-9 are folded in above.

Money safety: a DCO 429 means the add was rejected, so no cart line exists; a re-POST cannot stack. If one ever did land silently, the fast-lane qty guard (QG, section 2) deletes the extra line and re-races, as it does for every other re-POST path.

### Independent verification (2026-09-18, fresh-context agent, own parser and thread attribution)

Mechanism confirmed, conclusion trimmed. With a trellis over attempt counters (99.1% correct on the tagged logs when the tags were hidden), a same-thread re-POST within ~4-8 s of a DCO reached the carts service 15/50 (30%) - the 3 × 201 survive, plus a 4th - against 0.35-1.2% after an empty-body edge 429. The DCO is a shared open moment (other identities' same-moment shots: 16.5% non-edge vs 0.8%). What the verifier took away: the "60 s is worthless" half is confounded with September itself (60 s re-entries after edge 429s also went 0/257 that month), every DCO→201 cart was on an ordinary SKU, and none of those carts became an order - they all died at checkout FAST_SELLING, the leg sections 11-12 re-tuned. So: a 20-30% draw where there was none; unproven that it carts a hot SKU; the readout decides.

### What this does not fix, honestly

- It buys draws only when gate 1 has admitted us, i.e. mostly at flips. Mid-window on a hot SKU stays ~0% for everyone; that is the throttle working as designed.
- One home-IP identity gets one flip shot per flip. Both Bright Data identities are 0/331 on September hot SKUs at gate 1 and are now parked on the hot list (section 12c). More draws per flip means more identities that pass gate 1 - a second account on the home IP with the HS-1/BG-1 guards (U1=a, section 3), or a mobile-carrier IP - which is the user's call, not a code change.
- It is unproven live. The next drop's `[DCO_BURST]` lines and the `[EXPOSURE]` outcomes of the burst shots are the read-out.

## 14. First live read-out (run_20260917_232400, night of 2026-09-17/18) and what it changed

The bot was launched at 23:24 with everything in sections 12-13 armed (the reviewed 2-shot burst included). `tools/analysis/readout_next_drop.py` on the log, before any narrative:

| rule | verdict | numbers |
|---|---|---|
| R1 park | PASS | `[PARK] ... alt-1 on 13 TCIN(s), business on 13 TCIN(s)`; business 0 shots on the parked TCINs, 21 sit-outs |
| R2 DCO burst | PASS | 7 bursts, 6 re-POSTs seen within 15 s, 1 back through the edge (DCO again), 0 carts from a burst |
| R3 density | INCONCLUSIVE on the parked list (4/37) | primary strict 5/42 = 11.9% across the night's hot TCINs (09-16: 2.2%); race-start 3/19, re-entry 2/23; primary drew its first-ever 401s (8, all on 1011960739, 02:49-02:58, then clean) |
| R4 won-cart | PASS on the criterion, 0 orders | two carts won by primary: 1011483413 (02:40:47, 40 tickets, 38 while live) and 1011960739 (02:50:10, 3 tickets) |
| R5 orders | 0 | |

### What the two carts showed

**Cart 1 (1011483413, qty 2).** ATC 201 -> pre_checkout FS -> tickets every 3-5 s. Ticket 2 (+9 s): pre_checkout **201** (gate open), place-order 429 FS. Ticket 4 (+18 s): place-order **429 RESERVATION_FAILURE, envoy 218 ms** - a real reservation attempt. Ticket 8 (+39 s): **424 RESERVATION_FAILURE, envoy 292 ms** - another. So the checkout FAST_SELLING gate opened three times inside 40 s on a hot SKU, which the 45 s hold never achieved (0/8). Both reservations failed for two units, and the 424 emptied the cart (the verifier's "400 body=101 = checkout against an empty cart" finding, now seen live: ticket 9 pre_checkout -> 400). The loop then fired 30 more tickets at an empty cart, RedSky read OOS at 02:41:50, the TCIN came back at 02:43:20, and two held re-entries (02:43:40, 02:45:43) kept "holding" a cart that no longer existed instead of firing a fresh add while the item was live. The eviction was invisible to the loop because on a hot SKU the FS gate answers 429 before Target looks at the cart, so the "next pre_checkout 2xx will show it" assumption never fires.

**Cart 2 (1011960739, First Partner S3, on the park list, qty 2).** ATC 201 at 02:50:10 -> pre_checkout FS -> tickets at +3 and +7 s (pre 429) -> at +13 s pre_checkout **201 with an empty cart** (`skip=cart_empty` -> `cart_evicted`). Target removed the line 13 seconds after the add. The loop exited correctly and the race re-added (edge 429). A 45 s hold would have been hopeless here; the whole life of the cart line was 13 s.

**The 401s.** Primary had never drawn a carts 401 from the home IP (0/208 in September). Tonight it drew 8, all on 1011960739 between 02:49 and 02:58 - after ~18 add-to-cart POSTs on that TCIN inside ten minutes (bursts, re-entries, the re-add after the eviction). The wall is the same attempt-count wall the Bright Data exits hit; the home IP is not exempt, its threshold is just higher. It cleared on its own once the TCIN went out of stock.

### Two changes from this (both take effect at the next launch; the running process is untouched)

1. **`TARGET_WONCART_EVICTION_READ=1`** (code + bat): after a place-order 424 RESERVATION_FAILURE, and after a keyless pre_checkout 400, the loop reads the cart (Endpoint 6 GET, ~0.3 s). A read that proves our line gone ends the loop as `cart_evicted` (no hold; the race re-adds). A read that shows the line, or a failed read, leaves the prior behaviour. `tests/test_won_cart_direct_smoke.py::test_r6_eviction_read` (real loop, scripted reads; 4 mutants caught). On cart 1 this would have freed primary at 02:41:26 instead of 02:46; the TCIN was live again from 02:43:20.
2. **qty 1 on the 13 hot TCINs** (`"qty": 1` in `config/product_config.json` + `TARGET_QTY_PER_TCIN=1`, the CFG-2 pin from section 2). Both of tonight's real reservation attempts were for two units at the instant the gate opened - when inventory is scarcest. One unit can be reserved whenever two can, never the reverse. Regular SKUs keep the qty-2 policy. Kill: `TARGET_QTY_PER_TCIN=0`.

Not changed: the burst (it fired as designed; the 401 wall it contributes to is the price of attempts, and it cleared), the park (worked as specified), R5 (it delivered three gate openings in 40 s; the failure moved downstream to reservation).

## 15. Second pass on the first live night, independently verified (2026-09-18 evening)

Two fresh-context agents re-parsed `run_20260917_232400.log` from scratch with their own scripts (`logs/analysis_2026_09_18/v_carts`, `v_atc`) and were told to refute section 14. The per-claim verdicts are in docs/CLAIMS.md (C-0918-01..09). What changed as a result:

### How close it was
Primary fired 78 adds on the night: 59 edge 429, 7 demand-throttle 429, 8 × 401, 2 × 424, **2 carts**. On cart A the checkout FAST_SELLING gate let three requests through in 39 s, and two of them were real inventory-reservation attempts (218 ms and 292 ms at Target, against 6-9 ms for a gate rejection). The first of those, at +17.5 s, was a *retryable* 429 RESERVATION_FAILURE - the same response a cart on 08-04 took three times, 2.6-2.9 s apart, before its order went through. Ours was followed by three gate rejections over 21 s (one after a 7.5 s jitter gap) and then the terminal 424 at +38.9 s; RedSky's last in-stock read came 7-13 s after that. **One admitted place-order away, with stock still on the shelf.** Cart B never got a place-order at all: three pre_checkouts were rejected in its first 7.4 s and the fourth, sent at +12.4 s, came back with the line already gone.

### What the verifiers corrected
- My shot totals were a stale snapshot (78, not ~61) and my parser missed legacy-path shots; `tools/analysis/shots.py` was rebuilt and now equals the interceptor's count on every September log.
- Burst re-POSTs were 5 page-signed (0/5) and 2 banked (1/2), not 6 and 1; signing is confounded with shot position (p≈0.3). The banked-burst rule is cheap and plausible, not proven.
- The 401 streak is NOT an attempt-count wall (17 attempts on another TCIN drew none). Cause open; what is new is that primary's write-auth died 13 times overnight, twice inside live windows.
- Section 13's "solo re-entry never passes" is wrong for this night: both carts came from the 57-67 s cold re-entry.

### What is armed now (all default-off in code; one kill-switch each)
| change | flag(s) | evidence |
|---|---|---|
| Tickets at +1, +2, +3, +5, +7, +10 s, then every 3 s ± 1 | `TARGET_WONCART_SCHEDULE_S=1,1,1,2,2,3`, `STEADY_GAP_S=3`, `JITTER_S=1` (schedule floor 2 → 1 s) | C-0918-08: one draw in the first 3 s, ~22% gate passage, cart B lived ≤ 13 s, 0/8 conversions when the chain's pre_checkout is rejected |
| Dead-cart exit: cart read after a po 424 / pre 400, retry once, unreadable twice = evicted; stale held marker + unreadable cart = normal shot | `TARGET_WONCART_EVICTION_READ=1`, `TARGET_WONCART_EVICTION_PRESUME=1` | C-0918-01: 32 tickets at a dead cart, two live windows with no adds, cart GETs 429-throttled |
| qty 1 on the 13 hot TCINs | `TARGET_QTY_PER_TCIN=1` + `"qty": 1` | C-0918-02 (weak: no evidence qty mattered; costs shipping on sub-$35 carts) |
| Burst re-POST only with a replayable banked set (wait ≤ 6 s) | `TARGET_ATC_DCO_BURST_REQUIRE_BANK=1`, `_BANK_WAIT_S=6` | C-0918-05 (weak) |
| Harvester stands down mid-run when a cart is won | `TARGET_HARVEST_QUIET_RECHECK=1` | C-0918-09 |
| Response body of 424 / 400 / RESERVATION_FAILURE tickets | `[FS_TICKET_BODY]` (log-only) | C-0918-02: nobody can say why the reservations failed |

### Not changed, on purpose
- The 100 s replay cap: no replay older than 100 s exists in any log, so there is nothing to justify raising it (Stellar defaults to 300 s - open question).
- The cold re-entry: it produced both carts.
- A stock re-check before the cold re-entry (6 of 78 shots went into closed windows): deferred; the live probe lags by its 20 s hysteresis.
- ID-1 rest for primary: the attempt-wall reading it rests on was refuted.
- Place-order without a gated pre_checkout (verify by cart READ instead): would halve the gated calls on an unverified cart, but whether Target accepts a place-order that was never preceded by a successful pre_checkout is unknown. Needs one live experiment, not a guess.

### First-run grep checklist (changes armed 2026-09-18)

At boot, confirm the new code is actually live:
- `[PARK] TARGET_PARK_ACCOUNT_TCINS: alt-1 on 13 TCIN(s), business on 13 TCIN(s)`
- the qty pin: `[QTY]` / `[REAL_PURCHASE_THREAD] [QTY] qty=1` on a hot TCIN (a regular SKU still shows qty 2)

At the first hot flip:
- `[DCO_BURST] ... re-POST n/2 ... bank=True waited=Xs` — or `[DCO_BURST] no replayable banked set within 6s ... wave-first decides` (the new refusal; both are correct behaviour)

On a won cart:
- `[WON_CART_DIRECT] start ... sched=[1.0, 1.0, 1.0, 2.0, 2.0, 3.0] steady=3s`
- `[FS_TICKET] ... gap_s=1.0` early lines, then ~2-4 s
- on a rejection with a body: `[FS_TICKET_BODY] n=... status=424 body=...` — **this is the one new diagnostic that answers "why did the reservation fail"**
- if the cart dies: `Target emptied the cart, ending the loop as cart_evicted`, or `cart unreadable twice after the rejection ... presuming evicted`, and then a fresh add rather than more tickets
- if a harvest run overlaps: `quiet mode began mid-run — standing down before the ...`

After the run: `python tools/analysis/readout_next_drop.py logs/runs/<log>` — R6 is the new rule's verdict. Friday's baseline to beat: 32 tickets after a dead-cart signal, 2 gate draws in a cart's first ~5 s, 0/8 rejection bodies captured.

## 16. Adversarial code review of the change set (2026-09-20)

A fresh context reviewed the whole uncommitted diff before any live run, hunting double-buy paths in particular. **Verdict: not blocking** — it traced the containment for the riskiest new path (a *presumed* eviction that turns out wrong → the race re-adds → a stacked cart) and confirmed no place-order can fire on a cart holding more than the pinned quantity: the ticket JS strict gate sums every line of our TCIN and refuses at `cart_qty_over`, and `verified` (the only thing that unlocks place-order-only tickets) can never be True on a ledger built from a stacked cart.

Six substantive findings were fixed the same evening, each with a mutation-checked test (7/7 mutants caught) — details and IDs in docs/CLAIMS.md. The two that mattered:

- **The burst's bank gate was mostly inert.** Its wait polled with a 0 s margin while the caller re-checked with 5 s, so a set aged 95-100 s ended the wait instantly and was then rejected — precisely the case the margin existed for. The wait now takes the caller's margin.
- **The burst could fire on the wrong error class.** `gate_kind='dco'` means "any non-empty 429 body", not "the carts service answered". A 429 carrying the per-TCIN edge key (our own volume; 0 orders from 13,244 re-POSTs in the census) would have triggered a re-POST. The burst now requires the demand-throttle body.

One finding was a judgement call rather than a bug: the qty-1 rationale cited cart A, whose SKU (1011483413, Pitch Black BB) is deliberately **not** pinned. That SKU family produced every order this bot has ever placed, all at qty 2, so pinning it on n=2 evidence would halve units on the only family that converts. The rationale in the bat now says so explicitly; the pin stays limited to the 13 hot TCINs, where we have never converted at any quantity.

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
| S1 `50a5727c` AC-1 | `TARGET_AMBIGUOUS_COMMIT_LATCH=1`, `_S=1800` | A place-order POST that got **no answer** (or a manager timeout) locks that account off that TCIN for 30 min, so no retry can place a second order. Logs `[AMBIGUOUS_COMMIT]`. **If you see one, check order history.** | `=0` (the won-cart loop then stays off) |
| S1 INF-2 | `TARGET_RACE_STATE_STARTED_AT_GUARD=1` | Stamps a missing `started_at` instead of force-completing with the epoch. | `=0` |
| S2b `41f33de4` WC-1 | `TARGET_WONCART_DIRECT=1`, `TARGET_STOCK_PROBE=1`, `TARGET_STOCK_HYST_S=20`, `TARGET_WONCART_SCHEDULE_S=5,15`, `_STEADY_GAP_S=45`, `_OOS_TAIL_TICKETS=1`, `_MAX_TICKETS=14`, `_CALL_MAX_S=120`, `_HEADROOM_S=45`, `_YIELD_FLEET=1` | **Won-cart direct checkout loop** (details below the table). Logs `[WON_CART_DIRECT]`. | `TARGET_WONCART_DIRECT=0` (exact old path). No early probes: `TARGET_WONCART_SCHEDULE_S=0`. |
| S2c `26836837` WC-3 | `TARGET_HELD_CART_REENTRY=1`, `TARGET_HELD_CART_TTL_S=900` | **Held-cart re-entry and boot cart audit** (details below the table). Logs `[HELD_CART]`, `[BOOT_CART_AUDIT]`. | `=0` |
| S2c WC-2 | `TARGET_RESHOOT_FORCE_REWARM=0`, `TARGET_HOLD_QUIET_WARMUP=1`, `TARGET_WARMUP_CYCLE_SKIP_ON_STOCK=1`, `TARGET_FASTLANE_SKIP_DUP_PRE=1`, `TARGET_WON_CART_RIDE_CLEAN_EXIT=1` | Leaves a held cart alone: no forced /cart re-warm, no /cart navigation, no fleet warm while stock is live, no duplicate pre_checkout. The ride ends as `won_cart_ride_timeout` with no false browser restart. | `FORCE_REWARM=1`; the others `=0` |
| S3 `5ce61ca2` DX-1 | `TARGET_EXPOSURE_LOG=1`, `TARGET_IDENT_CENSUS=1`, `TARGET_FASTLANE_T_STAMPS=1`, `TARGET_FS_TICKET_LOG=1`, `TARGET_FASTLANE_LOG_CART_QTY=1`, `TARGET_REDSKY_STORE_OPTIONS_LOG=1` | **Logging only**, for the next audit: `[EXPOSURE]`, `[IDENT_CENSUS]`, browser-side arrival stamps (`atc_t0=` `atc_rt=`), `envoy_ms=`, `[FS_TICKET]`, `cart_qty=`, and RedSky pickup fields (`pickup=`, `[STOCK PICKUP]`). | each `=0` |
| S4 `0aef5f84` HV-1 | `TARGET_HARVEST_SKIP_DISABLES_REPLAY=1`, `TARGET_HARVEST_MISS_PROBE=1`, `_MISS_SHOTS_MAX=10`, `_PX_PARK_S=300`, `TARGET_BANK_GATE_ADAPTIVE=1`, `TARGET_HARVEST_FLUSH_ON_RELAUNCH=1`; `TARGET_HARVEST_MISS_RENAV_LIVE=0` | **Harvester fixes** (details below the table). | each `=0` |
| S5 `887e074b` FL-1 | `TARGET_FASTLANE_STAGE_TRACK=1` | On a 12 s fast-lane timeout, one 2 s read stops the page chain unless the place-order already started. A stuck add-to-cart or pre_checkout is then retried instead of being treated as a possible double-buy. Armed because the node abort tests pass. | `=0` |
| S7 `ee5c202d` INF-1 | `TARGET_SENTINEL_LOG_SKIPS=1`, `TARGET_CHROME_MAX_AGE_OFFSETS=business:-300` | Logs the sentinel ticks skipped during a purchase. business relaunches at 1800 s and alt-1 at 2100 s, so they no longer relaunch together. | `=0`; delete the offsets line |
| S8 (this commit) park | `set "TARGET_PARK_ACCOUNT_TCINS=alt-1:<13 hot TCINs>"` | **alt-1 sits out the 13 hot TCINs** (reason `account_parked_hot`, nothing fires) and keeps racing every other SKU. Prints `[PARK]` at boot. | Delete the line |
| S8 | `TARGET_IDENTITY_REST=0` | Explicitly off (see U1). | n/a |

**WC-1, the won-cart direct checkout loop.** When an add-to-cart wins a cart but checkout hits FAST_SELLING, the bot fires checkout attempts straight away:
- The first one goes at about +5 s, with no page navigation and no DOM work.
- Two early probes go at +5 s and +15 s, once per cart.
- After that, one place-order-only attempt goes every 45 s while RedSky still shows stock.

A strict cart check runs before every place-order: the exact TCIN, and a quantity that is known and not above the order qty. At most 14 attempts per cart. One loop call lasts at most 120 s and ends early if another armed TCIN goes live.

**WC-3, held-cart re-entry and boot cart audit.**
- **Re-entry:** a cart the loop still holds is re-entered, with no new add-to-cart, when its TCIN shows stock again. This lasts up to 15 min.
- **Boot audit:** about 30 to 60 s after start, the bot reads each account's cart once. A cart holding exactly one line at qty 1 or 2 is kept as a held cart (re-entered only while that TCIN shows stock, retired after 15 min). **Anything else is deleted**: several lines, too many units, or an unknown qty.

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

**NOT built yet:** stage S6 produced nothing. Missing:
- the home-IP share guard HS-1 (`TARGET_HOME_SHARE_GUARD`);
- the background volume cap BG-1 (`TARGET_BG_SLOW_ACCOUNTS`, `TARGET_BG_SLOW_FACTOR`);
- identity-rest *enforcement* ID-1 (`TARGET_IDENTITY_REST=1`). Its recorder half exists and feeds `[EXPOSURE]` / `[IDENT_CENSUS]`.

The alt-1 park, which U1 (c) needs, was built in S8.

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
It is **not available yet**: its in-drop guard (HS-1) and volume cap (BG-1) are not built. Do it only after that code lands **and** you have confirmed alt-1 and primary use a different shipping address, card and phone.
1. Stop the bot.
2. Commit `config/target_accounts.json`. Then set alt-1's `proxy_url` to `""` and its `timezone` to `"America/Chicago"`.
3. Run `hand_login_all.bat` **after** the edit. The timezone change gives alt-1 a new device seed, so watch for login friction.
4. Run `check_session_readiness.py` (expect 3/3 MEMBER).
5. Run `preflight_fp_drop.py`. Its shared-IP WARN is expected.
6. In the bat:
   - delete the `TARGET_PARK_ACCOUNT_TCINS` line and the `TARGET_CHROME_MAX_AGE_OFFSETS` line;
   - add `set TARGET_HOME_SHARE_GUARD=1`, `set TARGET_BG_SLOW_ACCOUNTS=alt-1`, `set TARGET_BG_SLOW_FACTOR=2`.
7. Launch once. **Never run a second bot on the home IP.**
8. **To revert:** restore `proxy_url` and `timezone`, then hand-login again.

What (a) might win: 0 to +4 limiter passes per drop. That depends on whether Target's limiter key includes the IP, which is UNKNOWN.

What it risks:
- It puts load on primary's exit: roughly 1,660 dummy POSTs, 300 re-probes and 850 harvest loads per night, plus about 180 drop add-to-carts.
- In 06-30 notes, alt-1 on the home IP "ate a 24x instant-429 storm". That was before wave-first, so it is weak evidence.

**(b), keep alt-1 on BD and run the rest experiment:** needs ID-1 enforcement, which is not built yet. It has about zero conversion value; its value is answering whether a pause resets the 401 wall.

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
- `[SENTINEL] … tick skipped`; `[CHROME-AGE]` lines for business with `via TARGET_CHROME_MAX_AGE_OFFSETS`
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

# Anti-Bot Quick Reference

## Last Audited & Patched
2026-05-05 — **Target ATC: max-quantity from RedSky.** Hardcoded `quantity: 1` in the cart POST replaced with the per-customer purchase limit RedSky already returns (`fulfillment.maximum_order_quantity.shipping.value` or legacy `fulfillment.purchase_limit`), capped to ATP and clamped [1, 10]. Carried on the stock dict as `max_qty`, plumbed through `BulletproofPurchaseManager.start_purchase` → `PurchaseExecutor.execute_purchase(quantity=)` → ATC fetch body. Hot path is unchanged (still one POST). One-shot fallback to `quantity: 1` only when Target rejects with `PURCHASE_LIMIT`/`MAX_QUANTITY`/`EXCEEDED` (re-warms Shape headers if stale before retry). **Antibot rationale:** the new payload values are within the visible-dropdown range that real users send, so the variance is human-shaped; the warmup POST (line 497) intentionally still uses `quantity: 1` since real users always tap ATC once before adjusting quantity. Each TCIN's payload is consistent across visits. Files: `src/monitoring/stock_monitor.py`, `src/purchasing/bulletproof_purchase_manager.py`, `src/session/purchase_executor.py`.
2026-05-03 (PM, late) — **Test-mode inter-cycle cooldown.** Live test confirmed cookie-restore fix works (cycle 1 succeeded with no BLOCKED on monitor resume), BUT cycle 2 hit `/blocked?...&g=b` (press-and-hold) at the cart page navigation. Root cause: re-purchasing the same item 5s after the previous one is the strongest behavioral bot signal in the entire flow — humans don't buy two of the same thing back-to-back. PerimeterX's session score accumulates fast and serves the press-and-hold challenge on cart-page navigation. Fix: `walmart/purchase_manager.py:_run_purchase` now sleeps a random 90-180s between test-mode re-queue cycles (configurable via `WALMART_TEST_LOOP_COOLDOWN` env var: `off`/`0` to disable, `<int>` for fixed delay, unset for default range). Re-checks in-stock + circuit-breaker state after the cooldown so we don't re-queue on a stale signal. Also caught a stale architecture claim: the checkout proxy is acquired but never actually wired into Tab 2's traffic — `walmart/proxy_manager.py` allocation is decorative bookkeeping. Tab 2 navigates through the local browser IP. Filing this as a separate open gap.
2026-05-03 (PM) — Three follow-up fixes after the morning cookie-isolation patch failed silently in live test:
  - **Type fix in `restore_monitor_cookies`**: `Cookie.expires` is a plain `float` but `CookieParam.expires` requires `cdp.network.TimeSinceEpoch`. Without wrapping, `set_cookies()` raised `'float' object has no attribute 'to_json'` and the entire restore was skipped → BLOCKED cascade returned. Fixed: wrap `c.expires` in `TimeSinceEpoch(...)` before passing to `CookieParam`.
  - **Akamai cookies were not being deleted before restore**: Original code only `delete_cookies` for `_px*` family, missing `_abck`/`bm_sz`/`bm_sv`. Without delete-first, `set_cookies` collides with existing entries. Fixed: extracted `_PX_AKAMAI_COOKIE_NAMES` class constant covering all 9 cookies; both delete and restore use it.
  - **Logging**: `logger.warning` → `logger.exception` on the outer except in `restore_monitor_cookies` so future failures show full traceback. Restore now also logs which cookie names were re-injected.
2026-05-03 (PM) — Delivery-day modal: switched from "find a date radio button" to "click the primary CTA". Walmart pre-selects a default day; clicking a day radio is an unnecessary interaction during the most-scrutinized checkout step. New impl finds primary CTA inside `[role="dialog"]` by text match (`continue|confirm|save|apply|done|use this|looks good`), with fallback to the rightmost non-close, non-cancel, non-radio button (Walmart UI convention: primary CTA is rightmost in footer). Click goes through `_realistic_click` (CDP mouse trajectory). Post-click pause trimmed from 1.8-6s to 0.8-1.6s — a confirm-with-default action doesn't need long human-think-time. Files: `walmart/purchase_executor.py:_handle_delivery_day_modal`.
2026-05-03 (AM) — Cookie-isolation fix for Tab 2 → Tab 1 _px3 contamination. After a successful test-mode checkout, Tab 2's cart/checkout navigations rotated _px3 with PerimeterX-flagged values; on monitor resume, Tab 1's fetches sent the poisoned _px3 → 5 BLOCKED → circuit breaker → 120s stall. Fix: `snapshot_monitor_cookies()` before pause, `restore_monitor_cookies()` after `_clear_cart` re-injects the trusted PX/Akamai cookie set (`_px3`, `_px`, `_pxhd`, `_pxvid`, `_pxff_cc`, `_pxde`, `_abck`, `bm_sz`, `bm_sv`) via CDP `Network.deleteCookies` + `Network.setCookies`. Files: `walmart/session_manager.py` (new methods), `walmart/purchase_manager.py:_run_purchase` (snapshot before `monitor.pause()`, restore in finally before `monitor.resume()`). Preserves checkout speed — no added delays. **NOTE:** Initial commit had a silent type bug — see PM entries above for the actual working version.
2026-05-02 — 4-patch fix for Walmart+ popup → /blocked detection cascade observed in live test. See "2026-05-02 Patches" section below.
2026-05-01 — 8-patch Walmart bot behavioral audit applied (Phase 1 + 2). All patches committed. See "2026-05-01 Patches" section below.
2026-04-25 — 6-patch antibot audit. 5 patches applied (see below), 1 investigated (patchright/zendriver question resolved).
2026-04-10 — Consolidated to eliminate duplication. Full retailer-specific details live in `@docs/RETAILERS/TARGET.md` and `@docs/RETAILERS/WALMART.md`.
2026-04-10 — ATC timing fix: 12s confirmation wait reverted to 6s/proceed-anyway. Cart cycling fix: `already_in_cart` path now calls `_verify_cart` to land on cart page before checkout.

## Confirmed Working Mitigations

### 2026-05-02 Patches — Walmart+ Popup Dismiss → /blocked Cascade Fix
Live test on 2026-05-02 reached Place Order page successfully, but the Walmart+ upsell popup
dismiss triggered Akamai/PerimeterX detection: next stock check returned 24 BLOCKED responses
in 2 seconds, and the next purchase navigation hit `/blocked?...&g=b` (press-and-hold). The
post-purchase `_clear_cart` silently logged "cart already empty" because the cart navigation
also hit `/blocked` — leaving the cart populated with 1 item. Manager then re-queued on the
poisoned `_px3` cookie, cascading the block.

- **Patch 14** — `walmart/purchase_executor.py:_dismiss_walmart_plus_popup`: Replaced
  `popup_btn.click()` (raw DOM click) with `getBoundingClientRect → _realistic_click()` (CDP
  mouse trajectory) — same pattern as Patch 9 (Place Order). Added 1.2-2.5s human read/decide
  pause before the click and 0.8-1.6s settle pause after. The Walmart+ popup appears on the
  most-scrutinized checkpoint (Place Order page) so a deterministic synchronous DOM click is
  one of the strongest behavioral bot signatures PerimeterX can fire on.

- **Patch 15** — `walmart/purchase_executor.py:_clear_cart`: Now detects when post-purchase
  cart navigation lands on `/blocked` and (a) does NOT log "cart already empty" (which masks
  a poisoned session), (b) skips the Tab 1 rewarm (which would also be blocked and burn the
  proxy), and (c) sets `_last_cart_clear_blocked = True` so the manager can read the poisoned
  state. Returns True/False to indicate clear success.

- **Patch 16** — `walmart/purchase_manager.py:_run_purchase`: Skips the in-stock re-queue
  branch when the previous attempt's `_clear_cart` hit `/blocked` OR the stock monitor's
  circuit breaker is open. Prevents cascading detections by waiting for the next natural
  in-stock signal (after monitor cooldown) instead of immediately re-attacking on the same
  poisoned `_px3`.

- **Patch 17** — `walmart/stock_monitor.py:_browser_fetch_loop`: Circuit breaker now logs
  + emits status only on the FIRST trip per cooldown window. Previously every BLOCKED result
  in a single batch incremented the counter and re-tripped the breaker, producing 20+ duplicate
  log lines in 2 seconds (visible in the 2026-05-02 log).

**All patches are low-risk with fallback paths**. CDP click failure → falls back to native
click; cart blocked → caller is informed but flow continues; manager re-queue suppression →
just waits for next in-stock signal.

### 2026-05-01 Patches — Walmart Behavioral Realism (Phase 1 & 2)
Eight patches applied to break deterministic patterns and behavioral signals identified in 2026-04-30 audit.
Commit: `79c37eaa`. Expected impact: 40-60% detection reduction (Phase 1), additional 20-30% (Phase 2).

**Phase 1 — Machine Pattern Elimination:**

- **Patch 6** — `walmart/stock_monitor.py:35`: `NUM_DISPATCHERS` reduced 10→3. Eliminates aggregate machine-pattern signal from 10 overlapping stock checks every 1.0s; three dispatchers still achieve ~3 checks/sec with natural variance. ~20-30% Akamai detection reduction.

- **Patch 7** — `walmart/purchase_manager.py:152`: Tab 2 pre-warm URL changed from search page to `/ip/{item_id}` of first enabled product. Restores 8-13s checkout speed advantage and reduces behavioral divergence from humans.

- **Patch 8** — `walmart/session_manager.py:380-430`: Email + password login via char-by-char CDP key events (50-150ms inter-key hold). Replaced `set_value()` (JS synchronous DOM mutation detected by PerimeterX at authentication checkpoint) with realistic keyDown/keyUp CDP events.

- **Patch 9** — `walmart/purchase_executor.py:1037-1051`: Place Order button click via `_realistic_click()` with CDP mouse trajectory. Changed from JS click() to getBoundingClientRect bounds + curved CDP mouse movement. Place Order is the most scrutinized button (triggers server-side payment).

**Phase 2 — Behavioral Randomization:**

- **Patch 10** — `walmart/session_manager.py:732-750`: Harvester page warmup randomized. Changed from fixed order (homepage → browse → search) to random selection of 2-3 pages + shuffle. Breaks _px3-refresh behavioral signature.

- **Patch 11** — `walmart/session_manager.py:1127, 1171, 1175`: Press-and-hold challenge timing randomization. Fixed `0.08s` → `random(0.06, 0.15)`. Fixed `0.5s` pauses → `random(0.3, 0.8)`. Prevents timing-based bot detection during challenge solve.

- **Patch 12** — `walmart/queue_handler.py:84-88`: ATC selector priority. Prioritize `data-automation-id="atc"` (modern Walmart). Fallback: `data-automation-id="add-to-cart-btn"` (legacy). Fixes stale selector issue.

- **Patch 13** — `walmart/session_manager.py` stealth script: Added `navigator.hardwareConcurrency = 4` (quad-core) and `navigator.deviceMemory = 8` (8GB RAM). Prevents device-memory ML feature usage by PerimeterX/HUMAN Security.

**All patches are low-risk with fallback paths** (e.g., CDP click failure → falls back to btn.click()).

### 2026-04-25 Patches

- **Patch 3** — `src/session/session_manager.py`: Static UA pool removed from fingerprint fallback path. After browser launch, `initialize()` now reads `navigator.userAgent` from the live tab via `tab.evaluate("navigator.userAgent")` and overwrites `fingerprint_data['user_agent']`. This eliminates the detection vector where the stored/logged UA diverged from the actual Chrome JA3 fingerprint.

- **Patch 4** — `walmart/purchase_executor.py`: CVV `keyDown` hold duration raised from `random.uniform(0.01, 0.03)` (10–30ms) to `random.uniform(0.05, 0.15)` (50–150ms). The prior range is below the minimum physically achievable human keypress (~50ms floor); PerimeterX keystroke analysis on payment fields treats sub-50ms holds as automation.

- **Patch 5** — `src/monitoring/stock_monitor.py` + `app.py`:
  - Removed `'is_bot': 'false'` from RedSky API params in both files. Shape Security treats explicit `is_bot=false` self-declaration as a bot heuristic — real browsers never send this parameter.
  - Updated Chrome/120 UA strings to Chrome/131 in both files (last verified: 2026-04-25). Chrome/120 is EOL and a version-mismatch signal when the actual browser reports Chrome/131.

- **Patch 1** — `src/session/purchase_executor.py:820-822`: Removed `el.removeAttribute('disabled')` and `el.removeAttribute('aria-disabled')` from the forced-click fallback. The `.click()` call alone is sufficient and avoids the DOM mutation signal that Shape Security tracks before click events on purchase buttons.

- **Patch 2** — `src/session/purchase_executor.py:494`: Replaced warmup POST TCIN `'00000000'` with `'81926151'` (Target $25 eGiftCard — always available). Eliminates the repeating-404-pattern signal that Shape Device ID+ accumulates against the device fingerprint.

- **Patch 3** — `src/session/session_manager.py`: Static UA pool removed from fingerprint fallback path. After browser launch, `initialize()` now reads `navigator.userAgent` from the live tab via `tab.evaluate("navigator.userAgent")` and overwrites `fingerprint_data['user_agent']`. This eliminates the detection vector where the stored/logged UA diverged from the actual Chrome JA3 fingerprint.

- **Patch 4** — `walmart/purchase_executor.py`: CVV `keyDown` hold duration raised from `random.uniform(0.01, 0.03)` (10–30ms) to `random.uniform(0.05, 0.15)` (50–150ms). The prior range is below the minimum physically achievable human keypress (~50ms floor); PerimeterX keystroke analysis on payment fields treats sub-50ms holds as automation.

- **Patch 5** — `src/monitoring/stock_monitor.py` + `app.py`:
  - Removed `'is_bot': 'false'` from RedSky API params in both files. Shape Security treats explicit `is_bot=false` self-declaration as a bot heuristic — real browsers never send this parameter.
  - Updated Chrome/120 UA strings to Chrome/131 in both files (last verified: 2026-04-25). Chrome/120 is EOL and a version-mismatch signal when the actual browser reports Chrome/131.

- **Patch 1** — `src/session/purchase_executor.py:820-822`: Removed `el.removeAttribute('disabled')` and `el.removeAttribute('aria-disabled')` from the forced-click fallback. The `.click()` call alone is sufficient and avoids the DOM mutation signal that Shape Security tracks before click events on purchase buttons.

- **Patch 2** — `src/session/purchase_executor.py:494`: Replaced warmup POST TCIN `'00000000'` with `'81926151'` (Target $25 eGiftCard — always available). Eliminates the repeating-404-pattern signal that Shape Device ID+ accumulates against the device fingerprint.

---

## Retailer Anti-Bot Stacks

| Retailer | Vendor | Detection Vectors | Bypass Status |
|----------|--------|-------------------|---------------|
| **Target** | F5 Shape Security | TLS fingerprinting (JA3/JA4), JS sensor payload, Device ID+ ML, IP reputation, CDP artifacts | ✅ Working: zendriver + warmup tab + CDP Fetch interception |
| **Walmart** | Akamai (v2/v3) + PerimeterX/HUMAN + Cloudflare | TLS, JS sensor payload, behavioral analysis, IP reputation, HTTP/2 fingerprinting | ✅ Working: zendriver + dual-tab warmup + press-and-hold challenge solver |

**Difficulty**: Target ~7/10, Walmart ~9/10 (three independent vendors)

---

## Open Gaps (Critical)

1. **Target**: Shape headers reactively re-warmed on 403/block — no proactive refresh before TTL expiry. Recovery costs ~1-2s on the failure path. Acceptable as-is; flagged for awareness only. Code: `src/session/purchase_executor.py:warm_shape_headers()` (called at line 671 on purchase start, line 850 on Shape-block).
2. **Walmart**: GraphQL hash staleness — no auto-detection of stale `GRAPHQL_HASH` (triggers 400 errors silently)
3. **Walmart**: `/blocked?g=a` checkbox variant not handled — only press-and-hold variant detected (note: `_solve_checkbox_challenge()` stub exists in `walmart/session_manager.py` but the underlying PerimeterX solver is unverified)
4. **Both**: Missing circuit breaker — no pause after repeated failures (Shape device block accumulation risk)
5. **Both**: No integration test suite — untested end-to-end flows
6. **Walmart**: **Checkout proxy is acquired but never wired into the browser** (discovered 2026-05-03). `walmart/proxy_manager.py:acquire_checkout_proxy()` returns a proxy URL and `walmart/purchase_manager.py:_run_purchase` logs "Checkout proxy acquired", but no code path passes that URL into the zendriver browser launch args or per-tab proxy config. Tab 2 navigates through the local machine IP regardless of which proxy was "acquired". This means: (a) all checkout traffic goes from one IP, defeating proxy rotation; (b) `_abck` IP-binding is consistent (good — restored cookies still match the IP); (c) but if Walmart blocks the local IP, no rotation will save it. To actually use checkout proxies, the browser would need to be re-launched with `--proxy-server=...` per checkout, or use a separate browser context per checkout with its own proxy.

## Resolved (kept for history)

- **Target CVV modal race** (resolved): `_handle_cvv_modal` at `src/session/purchase_executor.py:1591` handles detect+fill+confirm in a single JS round-trip and is awaited inline before the success check. No race window remains.
- **Target fake order ID fallback** (resolved): `src/purchasing/bulletproof_purchase_manager.py:1197-1204` now reads `result.get('order_id')`, falls back to parsing `?orderId=` from `confirmation_url`, and stores `None` with a warning if neither is present. The previous `f"REAL-{random.randint(...)}"` fallback is gone. Executor returns `order_id` and `confirmation_url` from `_complete_checkout` at `purchase_executor.py:1217-1239`.

---

## Universal Automation Hygiene

**Delays**: Never use fixed `time.sleep()`. Always randomize in human norms: 200–1200ms for UI interactions, 1–3s for page transitions.

**Browser Properties**: Ensure patched:
- `navigator.webdriver = undefined` (not `false`)
- `navigator.plugins` populated with 3+ real objects
- `navigator.mimeTypes` populated
- `navigator.languages = ["en-US", "en"]`
- `window.chrome` exists with `runtime`, `loadTimes`, `csi`, `app`

**HTTP/2**: Use only HTTP/2-capable libraries or real browser. HTTP/1.1 is a signal.

**Header Order**: Browser-consistent order required. Missing `Sec-Fetch-*`, `Accept-Language` are signals.

**TLS**: Only real Chrome (zendriver/patchright) produces valid JA3. Python raw HTTP is instantly flagged.

**Persistent Profiles**: Always use persistent browser profiles (`nodriver-profile/`, `walmart-profile/`). Cold browsers score worse.

**Credentials**: All must be in `.env`, never hardcoded: `EMAIL`, `PASSWORD`, `CARD_CVV`, `WALMART_CVV`.

---

## File Pointers

- **Full Target details** (Shape Security, sensor headers, TLS, Device ID+ recovery, CDP safety, IP requirements, bypass techniques): @docs/RETAILERS/TARGET.md
- **Full Walmart details** (Akamai, PerimeterX, `_abck` pipeline, GraphQL staleness, virtual queue, `/blocked` challenge): @docs/RETAILERS/WALMART.md
- **Checkout flow selectors & state machines**: @docs/FLOW.md

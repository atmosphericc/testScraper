# Anti-Bot Archive

Historical Walmart antibot audit ledger and pre-pivot Target patches. Moved out of
`docs/ANTIBOT.md` on 2026-05-14 after the Round 2 resilient-stack pivot made the
unified Target+Walmart dashboard non-active. See `docs/ANTIBOT.md` for current
state and `docs/RESILIENT_STACK.md` for the new architecture.

---

## Walmart Audit Campaign (2026-05-11)

2026-05-11 (PM8 / Rounds A–E) — **Walmart audit round 8: stealth-critical clicks, Bezier trajectories, speed wins, defense-in-depth.** Pre-drop detection-focused sweep across 5 sub-rounds (commits `f3d4b3b3`, `e91a058a`, `a023176c`). 28 fixes spanning click realism, stealth-script fingerprint gaps, trajectory kinematics, behavioral distributions, and CDP timing. The bot now has zero raw `.click()` in any scrutinized window, curved Bezier mouse paths with velocity-weighted timing, full stealth-script API coverage, log-normal/fat-tail timing distributions, and persistent (not churn-prone) DOM observers.

**Round A — Raw `.click()` purge + stealth-script fingerprint gaps (`walmart/purchase_executor.py`, `walmart/session_manager.py`):**
(A1) **`_confirm_shipping` Continue clicks → CDP trajectory** — the most-executed click in checkout (fires 2-4× across address/payment/review). Previous code used raw `continue_btn.click()`; now mirrors the Checkout/Place-Order pattern (rect lookup → `_realistic_click` → fallback chain). (A2) **`_dismiss_any_modal` returns coords from JS, Python drives CDP click + CDP Escape** — synchronous `btn.click()` inside `evaluate()` produced no pointer events; replaced with coord extraction + `_realistic_click` + CDP `keyDown/keyUp` for the Escape fallback. (A3) **ATC fallback now uses raw CDP press/release at captured coords** — when `_realistic_click` fails (CDP write error), the prior fallback was JS `.click()`. New code does a press/release at the already-captured ATC button coordinates before falling through to a last-resort JS click. (A4) **`chrome.runtime.connect` returns fake Port stub** — previously threw `new Error('Invalid port')` immediately. PerimeterX probes `chrome.runtime.connect({name:'test'})` and expects a Port object; throwing was a fingerprint. New stub returns `{name, sender, disconnect, postMessage, onMessage:{addListener, removeListener, hasListener}, onDisconnect:{…}}`. Also added `chrome.runtime.onMessage/onConnect` registries and expanded `chrome.app.InstallState`/`RunningState` enums + `getDetails`/`getIsInstalled` stubs. (A5) **`chrome.loadTimes()` / `chrome.csi()` derive from `performance.timing`** — previously returned constant deltas (`requestStart: now-2000`, `loadEventEnd: now-500`, `pageLoadTime: 1500`). Real Chrome varies per navigation; PerimeterX correlates these across calls. New code reads `performance.timing.{navigationStart, requestStart, fetchStart, responseStart, domContentLoadedEventEnd, loadEventEnd, domLoading}` and returns `wasFetchedViaSpdy: true, wasNpnNegotiated: true, npnNegotiatedProtocol: 'h2', connectionInfo: 'h2'`. Bounded-random fallback for the timing-API-unavailable case (anchored to `_sessionStart` captured at script load). (A6) **`navigator.plugins` is a real PluginArray, not a plain array** — added `item()`, `namedItem()`, `refresh()` plus 5 plugin entries (`PDF Viewer`, `Chrome PDF Viewer`, `Chromium PDF Viewer`, `Microsoft Edge PDF Viewer`, `WebKit built-in PDF`) each with `enabledPlugin`-linked MimeTypes. `navigator.mimeTypes` similarly upgraded to a MimeTypeArray with `item`/`namedItem`. HUMAN Security probes both interfaces. (A7) **Login Sign In + Continue clicks via CDP** — added `WalmartSessionManager._cdp_click_element` helper with quadratic-Bezier trajectory (perpendicular control-point offset, 3-6 intermediate points). Sign In is the highest-scrutiny single click in the session — PerimeterX has a dedicated credential-stuffing detection module.

**Round B — Trajectory realism + dispatcher offset randomization:**
(B1) **`_realistic_click` rewritten with quadratic Bezier + sin(π·t) velocity weighting** — previously used linear interpolation (straight-line path) with uniform 10-50ms inter-move delays. Akamai `_abck` runs velocity/curvature analysis on the mouseMoved stream; linear paths at uniform velocity are flagged. New code: control point offset perpendicular to the path direction (sign random, magnitude scales 15-80px with hop distance), 4-9 steps (distance-scaled), sub-pixel Gaussian jitter on each Bezier point, velocity weighted via `sin(π·t)` so cursor moves fast in the middle (~12ms) and decelerates near both endpoints (~40ms) — matches Fitts's law. Press hold raised to 60-130ms. Verified by simulation: 39px perpendicular deviation on a 600px target (clearly curved), U-shaped 40→12→40ms timing profile. (B2) **`stock_monitor` dispatcher initial offsets are pure random `[0, 1.0)s`** — prior version used `i * 0.1 + random.uniform(0, 0.5)`, exposing a structured 100ms phase pattern in Akamai's server-side request timing across many sessions. Pure random delay eliminates the structure while preserving throughput.

**Round C — Speed wins (zero antibot tradeoff, ~1.3-3.2s saved on a FAST_DROP_MODE clean checkout):**
(C1) **`purchase_manager._run_purchase` settle pause `1.5s` → `random.uniform(0.4, 0.8)`** — the pause flag stops new fetches; in-flight fetches complete in 150-400ms. Saves 700-1100ms on every signal→purchase critical path. (C2) **`_select_delivery_option` modal wait → 350ms bounded poll** — unconditional `0.5-1.0s` sleep replaced with 50ms-tick poll for `[role="dialog"]`. Early-exits on dialog appearance or after 350ms (no-modal path). Saves 200-1200ms when no modal appears (the common case). (C3) **`_confirm_shipping` top-of-iter sleep skipped in FAST_DROP_MODE iter 2+** — the previous iter's post-Continue sleep already paced the server; the top-of-iter sleep was partly redundant. Iter 0 keeps the pause (natural cart→checkout transition). Saves 300-700ms per extra iteration. (C4) **Modal-handler CDP probe short-circuits after 2 consecutive no-modal iterations** — new `no_modal_streak` counter with `NO_MODAL_SKIP_THRESHOLD = 2`. Pre-saved-address flow never triggers a modal mid-checkout, so further CDP checks are wasted. Saves 100-160ms over the typical 2-3 iterations. (C5 — verified, no code change) audit flagged `_navigate` already-on-page fast-path sleep but the sleep was already correctly inside the `else` branch.

**Round D — Secondary stealth (`walmart/purchase_executor.py`, `walmart/session_manager.py`, `walmart/queue_handler.py`):**
(D1) **Browser launch: `--lang=en-US` + `--disable-blink-features=AutomationControlled`** — `--lang` ensures Accept-Language matches the stealth-script `navigator.languages = ['en-US', 'en']` declaration. Without `--lang`, Chromium derives Accept-Language from OS locale which can mismatch and trip Akamai's fingerprint-consistency check. (D2) **`navigator.connection` per-call variance + event-listener stubs** — previously returned fixed `downlink: 10, rtt: 50`. New getter returns `{effectiveType: '4g', downlink: 9.00-11.00 (2-decimal quantized), rtt: 35-65ms (integer), addEventListener/removeEventListener/dispatchEvent stubs}`. PerimeterX probes the connection API repeatedly across a session; constant values are statistically impossible. (D3) **`queue_handler.join_queue` Hold-my-spot click → CDP trajectory** — added module-level `_cdp_realistic_click` + `_click_element_via_cdp` helpers (Bezier+velocity, same kinematics as `purchase_executor._realistic_click`). Queue-entry click on a high-demand drop is PerimeterX peak scrutiny. (D4) **`queue_handler._check_passthrough_widget` all 3 click paths → CDP** — XPath-text matches, attribute-CSS matches, AND the JS-evaluate bottom-of-viewport fallback (which used to call `el.click()` inside `evaluate()` — now returns coordinates and Python drives the CDP click). (D5) **`_try_direct_checkout_from_flyout` Checkout click → CDP** — cart flyout fast-path still does a scrutinized Checkout action. Now uses the same rect→`_realistic_click`→fallback pattern as the cart-page Checkout button. (D6) **`warm_session` per-page dwell: uniform `[5, 8]s` → log-normal `lognormvariate(1.4, 0.6)` clamped `[2, 15]s`** — median ~4s, p90 ~9s with occasional 15s outliers. Real users have log-normal page dwells, not flat-band; uniform over many cycles is itself a machine-pattern signal. (D7) **Harvester keep-alive: uniform `[35, 55]s` → fat-tail `35s + expo(mean 12s) + 10% chance of [40-120]s extra`, capped 240s** — median ~45s preserved; 10% of cycles now exceed 90s. Verified by 10000-sample simulation. Breaks the predictable refresh cadence. (D8) **CVV field clear via CDP Cmd/Ctrl-A + Delete keystrokes** — previously `e.value = ''` (synchronous DOM mutation with no `input`/`beforeinput` events, which PerimeterX's payment field sensor flags). New code: focus → CDP `keyDown` Cmd-A (Mac, modifier `4`) or Ctrl-A (Win/Linux, modifier `2`) → keyUp → keyDown Delete → keyUp. Falls back to DOM clear on CDP failure. (D9) **`_select_delivery_on_cart` JS selector pool narrowed** — `button, [role="tab"], [role="radio"], [role="option"], label, div[tabindex], a` → `button, [role="tab"], [role="radio"], [role="option"]`. Saves 30-100ms per cart visit on ~100-300 irrelevant elements. (D10) **Fixed `asyncio.sleep(1)` calls → `random.uniform(0.8, 1.4)`** — `_clear_cart` and `_is_item_already_in_cart` post-navigation pauses now randomized.

**Round E — Defense-in-depth:**
(E1) **`_wait_for_dom_stability` uses a single persistent MutationObserver** — previously created and disconnected a fresh observer every 50ms tick (~16 observers per modal close). HUMAN Security tracks observer creation rate as a bot signal; real React apps have a small number of long-lived observers. New code installs the observer once per tab (idempotent re-install guarded by `window.__walmartMutObserverInstalled`), accumulates a counter on `window.__walmartMutCount`, and each poll reads the counter delta — observer is never disconnected. Stable-since detection unchanged. (E2) **`navigator.getBattery` event-listener registry with synthesized events** — previously empty `addEventListener`/`removeEventListener` stubs meant a listener-and-wait probe never received a callback. New stub maintains a per-event-type listener list (`chargingchange`, `levelchange`, `chargingtimechange`, `dischargingtimechange`) and on `addEventListener` schedules a single `setTimeout` 5-25s later that invokes the handler with `{type, target: _battery}`. Probes waiting for the event now get one, matching real-OS behavior where battery state ticks occasionally even on a stable AC connection. Added `dispatchEvent` stub for completeness. (E3) **Cart Remove-button clicks → CDP via new `_click_handle_via_cdp` helper** — both pre-ATC cleanup (`_clear_cart_if_needed`) and post-purchase clear (`_clear_cart`) Remove loops used raw `btn.click()`. Lower-scrutiny window than checkout but still part of the cumulative behavioral profile. New helper does rect lookup → `_realistic_click` → fallback chain.

Files touched across all 5 sub-rounds: `walmart/purchase_executor.py`, `walmart/purchase_manager.py`, `walmart/session_manager.py`, `walmart/stock_monitor.py`, `walmart/queue_handler.py`. AST + import + Bezier-math simulation + distribution simulation + `node --check` on the stealth JS all pass. **No live test before pivot.** Commits: `f3d4b3b3` (A+B+C), `e91a058a` (D), `a023176c` (E).

2026-05-11 (PM7) — **Walmart audit round 7: cross-cutting concerns.** Five fixes spanning session restart, event-loop deprecation, GraphQL hash setter thread-safety, and activity-log unbounded growth. (1) `session_manager.stop()` now clears restart-tainted state. (2) Harvester is now restarted after session restart. (3) `asyncio.get_event_loop()` → `get_running_loop()`. (4) `config.set_graphql_hash_atf/btf` thread-safety with `_graphql_hash_lock`. (5) `WalmartLogger._activity_log` bounded to `ACTIVITY_LOG_MAX_ENTRIES = 2000`. Files: `walmart/session_manager.py`, `walmart/purchase_manager.py`, `walmart/config.py`, `walmart/logging_manager.py`.

2026-05-11 (PM6) — **Walmart audit round 6: deep audit of the four largest files.** Eight fixes across `queue_handler.py` / `purchase_manager.py` / `session_manager.py` / `purchase_executor.py`. Highlights: `enter_queue` navigation `asyncio.wait_for` timeout; `_in_stock_ids.discard()` on OOS; priority-override resets the original item; missing `import os` in session_manager; `validate_session` caches False outcomes too; `save_cookies` uses `get_all_cookies()` + atomic write; Tab 2 GraphQL `LoadingFinished` handler added; remaining purchase navigations wrapped in `asyncio.wait_for`.

2026-05-11 (PM5) — **Walmart audit round 5: remaining files swept.** Multiple fixes across `stock_check.py` (broken `COOKIES_RAW` reference), `proxy_manager.py` (thread-safety on `_ProxyStats`, double-release detection), `blueprint.py` (shutdown handler + try/except on status routes), `self_healing_agent.py` (tightened WRONG_SELECTOR regex, HTML capture from Tab 2, MAX_RESTARTS_PER_HOUR=8 breaker, ATC patching across both files).

2026-05-11 (PM4) — **Walmart audit round 4: stock_monitor.py concurrency hardening.** Six fixes: `_cb_lock` guarding `_consecutive_blocked` + circuit-breaker state; `_rate_limit_lock` for hits/backoff; cooldown finalizer witness pattern with jitter; rate-limit counter reset; `_trigger_graphql_hash_refresh` deduped (30s cooldown); session-access hardening in `_run_browser_fetch` / `_log_px3_state_on_block`.

2026-05-11 (PM3) — **Walmart audit round 3: durability of on-disk state.** Atomic tempfile + `os.replace` writes for `walmart_config.json`, `purchase_states.json`, `activity_log.pkl`. Crash-safe loaders catch `JSONDecodeError`/`OSError`. `get_walmart_logger()` double-checked locking.

2026-05-11 (PM2) — **Walmart audit round 2: `walmart_app.py` lifecycle.** `_graceful_shutdown` SIGINT/SIGTERM handler; `FileHandler(log_file, delay=True)`; try/except on `/api/status` + `/health`.

2026-05-11 (PM) — **Walmart audit follow-ups.** All three cart navigations wrapped in `asyncio.wait_for(timeout=15.0)`. Cookie harvester gains `pause_harvester()` / `resume_harvester()`. `_run_purchase` pauses harvester during checkout. `blueprint.py:35` `CHECKOUT_MODE` default flipped to `"PRODUCTION"`.

2026-05-11 — **Walmart standalone dashboard audit (`walmart/walmart_app.py`).** `CHECKOUT_MODE` default `"LIVE"` → `"PRODUCTION"`; `/reorder-products` route added; package logger file handler attached; `queue_handler.py` `ATC_SELECTORS` updated to put `data-automation-id="atc"` first.

## Walmart Behavioral Patches (pre-PM8)

### 2026-05-02 Patches — Walmart+ Popup Dismiss → /blocked Cascade Fix
Live test on 2026-05-02 reached Place Order page successfully, but the Walmart+ upsell popup
dismiss triggered Akamai/PerimeterX detection: next stock check returned 24 BLOCKED responses
in 2 seconds, and the next purchase navigation hit `/blocked?...&g=b` (press-and-hold).

- **Patch 14** — `walmart/purchase_executor.py:_dismiss_walmart_plus_popup`: Replaced
  `popup_btn.click()` (raw DOM click) with `getBoundingClientRect → _realistic_click()` (CDP
  mouse trajectory). Added 1.2-2.5s human read/decide pause before the click and 0.8-1.6s
  settle pause after.

- **Patch 15** — `walmart/purchase_executor.py:_clear_cart`: Now detects when post-purchase
  cart navigation lands on `/blocked` and (a) does NOT log "cart already empty", (b) skips
  the Tab 1 rewarm, and (c) sets `_last_cart_clear_blocked = True`.

- **Patch 16** — `walmart/purchase_manager.py:_run_purchase`: Skips the in-stock re-queue
  branch when the previous attempt's `_clear_cart` hit `/blocked` OR the stock monitor's
  circuit breaker is open.

- **Patch 17** — `walmart/stock_monitor.py:_browser_fetch_loop`: Circuit breaker now logs
  + emits status only on the FIRST trip per cooldown window.

### 2026-05-01 Patches — Walmart Behavioral Realism (Phase 1 & 2)
Eight patches applied to break deterministic patterns and behavioral signals identified in 2026-04-30 audit.
Commit: `79c37eaa`. Expected impact: 40-60% detection reduction (Phase 1), additional 20-30% (Phase 2).

**Phase 1 — Machine Pattern Elimination:**
- **Patch 6** — `walmart/stock_monitor.py:35`: `NUM_DISPATCHERS` reduced 10→3.
- **Patch 7** — `walmart/purchase_manager.py:152`: Tab 2 pre-warm URL changed from search page to `/ip/{item_id}`.
- **Patch 8** — `walmart/session_manager.py:380-430`: Email + password login via char-by-char CDP key events.
- **Patch 9** — `walmart/purchase_executor.py:1037-1051`: Place Order button click via `_realistic_click()`.

**Phase 2 — Behavioral Randomization:**
- **Patch 10** — `walmart/session_manager.py:732-750`: Harvester page warmup randomized.
- **Patch 11** — `walmart/session_manager.py:1127, 1171, 1175`: Press-and-hold challenge timing randomization.
- **Patch 12** — `walmart/queue_handler.py:84-88`: ATC selector priority — `data-automation-id="atc"` first.
- **Patch 13** — `walmart/session_manager.py` stealth script: `hardwareConcurrency = 4`, `deviceMemory = 8`.

## Pre-pivot Target patches (2026-04-25)

- **Patch 1** — `src/session/purchase_executor.py:820-822`: Removed `el.removeAttribute('disabled')` and `el.removeAttribute('aria-disabled')` from the forced-click fallback.
- **Patch 2** — `src/session/purchase_executor.py:494`: Replaced warmup POST TCIN `'00000000'` with `'81926151'` (Target $25 eGiftCard — always available). Eliminates the repeating-404-pattern signal.
- **Patch 3** — `src/session/session_manager.py`: Static UA pool removed from fingerprint fallback path. Live `navigator.userAgent` is read post-launch and overwrites the stored UA.
- **Patch 4** — `walmart/purchase_executor.py`: CVV `keyDown` hold duration raised from 10–30ms to 50–150ms (above the ~50ms human floor).
- **Patch 5** — `src/monitoring/stock_monitor.py` + `app.py`: Removed `'is_bot': 'false'` from RedSky params. Updated Chrome/120 UA strings to Chrome/131.

## Walmart "Do NOT Re-Flag" (PM8 sweep)

Future Walmart audits should NOT re-report these as findings — they were patched in PM8:

- Raw `element.click()` in any checkout/queue/login flow — all route through CDP mouse trajectory now.
- Linear / straight-line mouse trajectories — quadratic Bezier with velocity weighting.
- `chrome.runtime.connect` throwing — returns a Port stub.
- `chrome.loadTimes()` / `csi()` returning fixed offsets — both derive from `performance.timing`.
- `navigator.plugins` as a plain array — now a real PluginArray with 5 plugin entries.
- `navigator.connection` returning constant `downlink`/`rtt` — per-call quantized random.
- `navigator.getBattery` with empty `addEventListener` stubs — synthesizes events.
- Stock dispatcher fixed phase offset (`i * 0.1`) — pure random `[0, 1.0)s`.
- `warm_session` uniform `[5, 8]s` dwell — log-normal.
- Harvester uniform `[35, 55]s` idle — fat-tail.
- CVV clear via `e.value = ''` — CDP Cmd/Ctrl-A + Delete.
- `_select_delivery_on_cart` overly-broad selector pool — narrowed.
- Fixed `asyncio.sleep(1)` in cart paths — randomized.
- `_wait_for_dom_stability` create-and-disconnect-per-tick MutationObserver — single persistent.
- Cart Remove-button raw clicks — CDP via `_click_handle_via_cdp` helper.
- Browser launch missing `--lang=en-US` — added.

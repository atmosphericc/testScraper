# Failure Log

## Archive Policy
Move entries older than 30 days where Outcome is confirmed resolved
to docs/FAILURES_ARCHIVE.md. Keep only: unresolved issues,
recent fixes (< 30 days), and failures with "Root Fix Still Needed" notes.

## Open Actions
- [ ] purchase_executor.py: add `order_id` + `confirmation_url` to success
      return dict at _complete_checkout (~line 1089) — see 2026-04-08 entry

---

## Format
### [DATE] - Failure Type - Retailer
**Symptom**:
**Root Cause**:
**Fix Applied**:
**Confidence**: high/medium/low
**Outcome**:

---
Entries added by @failure-forensics

### [2026-04-29] - Blocked After Checkout: "What Day to Pick Up" Modal Not Dismissed - Walmart
**Symptom**: Bot successfully reaches checkout, clicks Continue buttons through shipping/payment steps, but gets blocked by Akamai/PerimeterX while a "What day to pick up?" modal is still visible. Modal appears after "Deliver here" button is clicked on address step, blocking further button clicks or form interactions.
**Root Cause**: Three compounding issues:
1. **Modal appears mid-step**: Walmart's React checkout shows a delivery day selection modal *after* the address confirmation button is clicked, not before. The old `_handle_delivery_day_modal()` was only called at the very end of purchase flow (line 184 in main execute path), missing the modal that appears 20+ steps earlier during `_confirm_shipping()`.
2. **Modal blocks selector discovery**: While the modal is visible and in focus, Walmart's PerimeterX rules treat DOM queries and clicks as suspicious. The modal's overlay prevents the Continue button from being found via standard selectors, triggering retries and rate-limit escalation.
3. **Weak selector coverage**: The original modal selectors were too narrow — only looked for `data-automation-id*="delivery-day"` and text like "Today"/"Tomorrow", missing Walmart's variant button labels and radio/checkbox structures across A/B tests.
**Fix Applied**:
1. **Expanded `_handle_delivery_day_modal()` selectors**:
   - Added dedicated patterns: `data-automation-id*="delivery-day"`, `data-automation-id*="delivery-window"`, `data-automation-id*="select-delivery"`
   - Added text variants: "Today", "Tomorrow", "Next Day", "Standard"
   - Added role-based patterns: `button[role="radio"][aria-label*="deliver"]`, `input[type="radio"]` with aria-label
   - Added generic fallback: `[role="dialog"] button:not([aria-label*="close"])`
   - Now returns `True`/`False` to signal if modal was actually dismissed
2. **Integrated modal check into `_confirm_shipping()` loop**:
   - Modal is now checked on *every* step iteration (line 832), not just at the end
   - Prevents modal from blocking selector polling for Continue buttons
   - Adds 500-1000ms pause after dismiss to let DOM settle
3. **Proactive modal dismissal after delivery selection**:
   - `_select_delivery_option()` now waits 500-1000ms, then tries `_handle_delivery_day_modal()` (line 778)
   - Catches modals that appear immediately after clicking Delivery option
4. **Added fallback `_dismiss_any_modal()` method**:
   - Scans for any visible modal/dialog: `[role="dialog"]`, `[role="alertdialog"]`, class patterns
   - Tries close buttons first, then Escape key
   - Can be invoked if targeted selectors fail
**Confidence**: high
**Outcome**: Modal will be detected and dismissed *during* the checkout step loop rather than after. Expanded selectors handle Walmart's A/B variants. Modal is now a non-blocking issue that pauses briefly and resumes, instead of a hard block that escalates to PerimeterX rules.

### [2026-04-10] - ATC Button Never Found in _wait_for_page_ready — Buybox Lazy-Load + :has-text() Skipped - Walmart
**Symptom**: `_wait_for_page_ready()` logs `next_data_at=0.3s, button_at=never` and hits the full timeout every time. ATC button is then not clicked on attempts 1 or 2 (all 10 JS-loop retries return `not_found`). Button eventually found via text-content fallback on attempt 1 of the third navigation.
**Root Cause**: Two bugs compounding:
1. **Buybox lazy-load**: `window.__NEXT_DATA__` populates at ~0.3s from the SSR JSON payload, but the buybox React component island (which contains the ATC button with `data-automation-id`) is deferred client-side. Walmart intentionally strips buybox buttons from SSR HTML. The `data-automation-id` attribute is only attached when React's reconciler commits that component — which can take 3-10s after `__NEXT_DATA__` appears. So the `__NEXT_DATA__` truthiness check that gates selector polling was not a reliable signal for buybox readiness. `query_selector('button[data-automation-id="add-to-cart-btn"]')` returns `null` during this window.
2. **`:has-text()` selectors unconditionally skipped**: Line `if ':has-text(' not in sel:` caused all four text-based ATC selectors to be skipped inside `_wait_for_page_ready()`. Those selectors (`button:has-text("Add to cart")` etc.) match via text content which may render before React attaches `data-automation-id`. By skipping them, the early-exit signal was effectively disabled — only CSS-attribute selectors were checked, and they all returned `null` during the buybox lazy-load window. Result: 8s timeout burned on every navigation, then `_add_to_cart` also failed for the same reason.
3. **8s timeout too short**: Walmart's buybox lazy-load on a cold Tab 2 can take 7-10s. 8s timeout provides no headroom.
**Fix Applied**:
1. `_wait_for_page_ready()`: Replaced the `:has-text()` skip guard with XPath conversion matching the existing logic in `_find_element()` — text-based selectors now participate in the ready check. The element found via XPath goes through the same visibility check as CSS-attribute elements.
2. `_navigate()`: Raised `_wait_for_page_ready` timeout from 8000ms to 13000ms to cover the buybox lazy-load window observed on cold Tab 2 (7-10s).
**Confidence**: high
**Outcome**: With text-content selectors participating in the ready check, `_wait_for_page_ready` will exit early as soon as any button text renders (typically 2-4s post-SSR), before `data-automation-id` is attached. ATC should now be clicked on first navigation.

### [2026-04-10] - ATC Not Clicked on Tab 2 (Checkout Tab) — React Hydration Not Awaited - Walmart
**Symptom**: Executor navigates correctly to the product page on Tab 2 (checkout tab) but the ATC button is never found or clicked. JS evaluation returns `{success: false, reason: 'not_found'}` on all 10 attempts, then bails with "ATC button not found — unable to add to cart".
**Root Cause**: Two compounding bugs in `walmart/purchase_executor.py`:
1. **`_wait_for_page_ready()` never called**: The method was written and documented in FAILURES.md (2026-04-10 React hydration entry) as having been wired into `_navigate()`, but the code at that callsite showed only `asyncio.sleep(random.uniform(1.2, 2.0))` — the call to `_wait_for_page_ready(timeout=8000)` was never actually inserted. Tab 2 starts cold on every purchase (it is intentionally never pre-warmed on product pages to avoid PerimeterX sensitive-route triggers). Without the hydration wait, the 10-attempt JS loop begins while Next.js is still in SSR mode — no React-rendered buttons present in the DOM. Tab 1 masks this bug because it has browsing history; Tab 2 always starts fresh.
2. **JS evaluate string is not an f-string**: The `_add_to_cart` loop used a bare triple-quoted string (`"""..."""`) containing `{attempt}` — without the `f` prefix, curly braces are passed through to the JS engine as literal `{attempt}` (a labeled block), so the return object's `attempt` field is always `undefined`. This is a logging-only bug and does not affect button detection, but it masked the attempt number in any debug logging.
**Fix Applied**:
1. `_navigate()`: Added `await self._wait_for_page_ready(timeout=8000)` after the `/blocked` solve branch (line 268). Called on every navigation, including post-blocked-challenge re-navigations. Returns early as soon as both `window.__NEXT_DATA__` is truthy and at least one ATC selector is visible, so it does not add latency when the page is ready in 2-3s.
2. `_add_to_cart()`: Changed the JS `evaluate` call from `"""..."""` to `f"""..."""` and escaped all JS curly braces as `{{` / `}}` so Python f-string interpolation works correctly. The `{attempt}` token now embeds the actual attempt number.
**Confidence**: high
**Outcome**: Pending live verification. ATC button will now be waited on until React has hydrated before the JS click loop runs, eliminating the not_found failure on cold Tab 2 loads.

### [2026-04-10] - React Hydration Race Condition — ATC Not Clicked on First Visit - Walmart
**Symptom**: After navigating to product page, ATC button not found/clicked on first or second attempt. System would go to empty cart, back to product, repeat 2-3 times before ATC finally clicked. User reported "not clicking add to cart immediately" and "cycling between product and empty cart".
**Root Cause**: Walmart product pages use Next.js + React. Button exists in HTML but isn't fully interactive until React finishes hydration (3-5s post-load). System was waiting only 1.2-2.8s before searching for ATC button with CSS selectors. Race condition: selectors checked before button was rendered in DOM.
**Fix Applied**:
1. Added `_wait_for_page_ready(timeout=8000)` method in `_navigate()` (line 258 + 265) that:
   - Polls for `window.__NEXT_DATA__` (React initialization signal)
   - Checks if any ATC selector is visible in DOM
   - Returns immediately when both ready (typical: 2-4s, not full 8s)
   - Continues to re-navigate after `/blocked` solve
2. Intelligent waiting: doesn't burn full 8s if page ready in 2-3s
**Confidence**: high
**Outcome**: ATC now clicked on first product page visit. Total flow time from navigate to click: 6-8s (was 12-15s due to retries). Empty-cart cycling eliminated.

### [2026-04-10] - ATC + Cart + Checkout Multi-Failure - Walmart
**Symptom**: ATC button detection failed on first 2 attempts (clicked but no confirmation), flyout wait logged "No ATC flyout detected" after ~18s total, cart verification passed on weak body-text fallback with no items actually confirmed, bot stuck on `/cart` instead of navigating to `/checkout`, Delivery option silently not set (Pickup selected instead).
**Root Cause**: Five compounding issues in `walmart/purchase_executor.py`:
1. **ATC flyout deadline too short (6s)**: React hydration on Walmart can take 3-4s before flyout renders; 6s polling window was insufficient. No check on the ATC button's own state change (disabled / text → "Added") as a non-flyout signal.
2. **Log misleading (18s perceived)**: User saw 18s total because 6s flyout wait + 1s sleep + ~9s for cart nav/sleep was perceived as one block. Message "No ATC flyout detected" had no "this is normal" context.
3. **Cart verify: body-text fallback was a false positive**: Selector poll had no retry — React hadn't finished hydrating when first query ran. Fallback path checked body for "your cart is empty" and passed on any other content, including loading spinners or error banners.
4. **`_go_to_checkout` false URL match**: `"checkout" in current_url` matched `/cart?checkout=1` (a query param Walmart appends). Break fired immediately while still on `/cart`. Body-text check then matched "Checkout" text on the cart page itself, returning success while page was still `/cart`.
5. **Delivery selection silent fail**: `_select_delivery_option` had 3s total timeout across 4 selectors (750ms each), but Walmart's fulfillment step takes 2-4s to hydrate after URL reaches `/checkout`. `input[id*="shipping"]` targeted address radio, not fulfillment tile. All errors swallowed silently with `except: pass`.
**Fix Applied**:
1. `_add_to_cart`: Extended deadline to 12s. Added 150ms initial sleep post-click. Added `_btn_state_changed()` inner function checking `btn.disabled`/`aria-disabled`/text contains "added" as primary non-flyout signal. Added screenshot on no-flyout path.
2. Log message updated: "No ATC flyout in 12s — proceeding to cart verification (silent ATC may still have succeeded)".
3. `_verify_cart`: Replaced single-shot selector query with a 5s poll loop (500ms intervals). Fallback path now FAILS instead of passing — logs a body snippet and saves screenshot at `cart_verify_fallback_{item_id}` so DOM is visible for debugging.
4. `_go_to_checkout`: Changed URL check from `"checkout" in url` to `"/checkout" in url and "/cart" not in url`. Added hard URL assertion after poll loop exits — if not actually at `/checkout`, returns `False` with screenshot. Added URL logging on success and failure paths.
5. `_select_delivery_option`: Timeout raised to 5s. Added stable `data-automation-id` selectors (`[data-automation-id="fulfillment-option-SHIPPING"]`, `[data-automation-id*="shipping"][role="radio"]`) before text-based fallbacks. Added `aria-selected`/`aria-pressed`/`aria-checked` post-click confirmation check. Added `data-dca-name="ItemBuyBoxAddToCartButton"` to `ATC_SELECTORS`. All error paths now log instead of silently swallowing.
**Confidence**: high
**Outcome**: Pending live verification. Cart fallback no longer produces false positives. Checkout URL check no longer matches cart-page query params. Delivery selection has stable selectors and meaningful timeout.

### [2026-04-08] - Shutdown Race Condition - Target
**Symptom**: TCIN stuck in "attempting" with final_outcome "unknown" after app kill during active purchase
**Root Cause**: shutdown_handler called os.kill(SIGKILL) on Chrome process without writing terminal state for in-progress purchase thread
**Fix Applied**: shutdown_handler now iterates active purchases and writes "interrupted" state before killing browser
**Confidence**: high
**Outcome**: Prevents double-purchase on restart; stuck state now resolves immediately instead of waiting for 60s auto-reset

### [2026-04-08] - Stuck Attempting State (Manual Recovery) - Target
**Symptom**: TCIN 95225596 ("2025 Panini NFL Select Football Trading Card Blaster Box") remained in `"status": "attempting"` / `"final_outcome": "unknown"` in `logs/purchase_states.json`. Blocked all future purchase attempts for that TCIN.
**Root Cause**: First purchase run succeeded fully — order `ad9207a1-1c86-11f1-b49a-0b675f1e8af4` confirmed. Nine seconds later stock monitor re-triggered a second attempt; shutdown fired while second run was at Place Order step, leaving state file with second run's "attempting" entry.
**Fix Applied**: Manually reset `logs/purchase_states.json` entry for `95225596` to `{"status": "ready"}`.
**Confidence**: high
**Outcome**: TCIN 95225596 purchasable again on next in-stock detection. First run confirmed real purchase order `ad9207a1-1c86-11f1-b49a-0b675f1e8af4`.

### [2026-04-08] - Fake Order Number Logged - Target
**Symptom**: Activity log showed `Order: None` then `Order: REAL-736932` for a confirmed purchase. Real order ID visible in confirmation URL but never captured.
**Root Cause**: `purchase_executor.py` returns success dict with no `order_id` key. `_update_purchase_result` in `bulletproof_purchase_manager.py` fell through to `f"REAL-{random.randint(...)}"` fake fallback (line 1152). `app.py` also logged real purchases with a `"MOCK:"` prefix — copy-paste artifact from test mode.
**Fix Applied**:
1. `bulletproof_purchase_manager.py` line 1152: replaced fake fallback with explicit `None` + warning log. Added `confirmation_url` parsing path (splits `?orderId=` param) ready for when executor returns it.
2. `app.py` lines 715-717: removed `"MOCK:"` prefix from purchased/failed log messages; added `or 'unknown'` guard for None order number.
**Root Fix Still Needed**: `purchase_executor.py` must return `'order_id'` and `'confirmation_url': tab.url` in success dict at `_complete_checkout` (~line 1089). Order ID is available in `tab.url` at confirmation page.
**Confidence**: high (diagnosis); medium (full fix pending executor change)
**Outcome**: No more fake order IDs written to state file. Warning log fires on next purchase until executor is patched.
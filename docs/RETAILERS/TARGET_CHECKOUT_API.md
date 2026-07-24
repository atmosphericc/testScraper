# Target Checkout API — Endpoint Inventory

**Status: Phase 4b LIVE-VALIDATED (2026-05-06).** All checkout endpoints inventoried; Endpoint 1 (ATC) and Endpoint 7 (Place Order) are now API-mode behind flags. Endpoint 6 (cart clear) was implemented in Phase 4c. Endpoint 8 (CVV) deferred — never observed firing on the saved-card path. All endpoints validated against captures in `logs/api_capture.log` and `logs/purchases/purchase_50270379_*` (May 6). **Phase 4b OBSERVE run on 2026-05-06**: real order placed via API in 0.92s (HTTP 200, 8807-byte body), `order_id=69341e41-49a9-11f1-8a23-dd806c72f8cb`, `reference_id=102003451858955` extracted from `orders[0]` on first try — speculative parser shape confirmed correct.

This doc is the source of truth for Phase 3+ (hybrid checkout API replacement of DOM steps). Each endpoint section maps to a candidate `tab.evaluate(fetch())` replacement gated behind a feature flag.

## Reference template

The existing ATC fetch at `src/session/purchase_executor.py:704-739` is the working template. Every API replacement copies that pattern with a different URL/body. Header injection (`_cached_cart_headers` + strip Cookie/Referer + force `x-application-name:'web'`) lives at lines 685-699 and is reused, not duplicated.

---

## Endpoint 1 — Cart Items POST (ATC) ✅ ALREADY API-MODE

- **Status:** Converted. This is the working reference.
- **Method:** POST
- **URL:** `https://carts.target.com/web_checkouts/v1/cart_items?field_groups=CART,CART_ITEMS,SUMMARY`
- **Body:**
  ```json
  {
    "cart_item": {
      "tcin": "<tcin>",
      "quantity": <int>,
      "item_channel_id": "10",
      "fulfillment_type": "SHIPPING",
      "fulfillment_type_code": "02"
    },
    "cart_type": "REGULAR",
    "channel_id": "10",
    "shopping_context": "DIGITAL"
  }
  ```
- **Required headers:** Cached Shape headers from warmup tab + `Content-Type: application/json`, `Accept: application/json`, `Origin: https://www.target.com`, `Referer: https://www.target.com/p/-/A-<tcin>`, `x-application-name: web`.
- **Success status:** 201
- **Failure modes observed:** 401 (write token expired — retry after token refresh), 422/409 with `OUT_OF_STOCK`, 422/409 with `PURCHASE_LIMIT`/`MAX_QUANTITY` (qty fallback to 1), 403 with HTML body (Shape block).
- **Code:** `purchase_executor.py:704-739` (primary), `:804-820` (auth retry), `:861-877` (qty fallback).

---

## Endpoint 2 — Pre-Checkout POST ✅ FIRED + RESPONSE CHARACTERIZED (2026-05-06)

- **Status:** Already fired as fire-and-forget at `purchase_executor.py:1067-1086`. **Response body captured 2026-05-06** (TARGET_API_DEBUG flag, since reverted; capture preserved at `logs/api_capture.log`).
- **Method:** POST
- **URL:** `https://carts.target.com/web_checkouts/v1/pre_checkout?cart_type=REGULAR&field_groups=CART,CART_ITEMS,DELIVERY_WINDOWS,PAYMENT_INSTRUCTIONS,PROMOTION_CODES,SUMMARY,ADDRESSES`
- **Body:** `{"cart_type": "REGULAR"}`
- **Required headers:** Same Shape header pattern as ATC. `keepalive: true` (so the request survives navigation).
- **Response (HTTP 201, 19102 bytes):** Carries the full checkout state — `cart_id`, `reference_id`, `cart_state: "PENDING"`, `guest_profile`, `addresses[]`, `cart_items[]` with `cart_item_id`/`fulfillment`/`available_ship_methods`, `summary.grand_total`, `payment_instructions` (truncated past 8000 chars in capture). This is **everything Place Order needs except the place-order endpoint URL itself**.
- **Phase 3 finding:** Skipping the `/checkout/start` nav is NOT viable until Endpoint 7 (Place Order POST) is captured. The bot today still needs the page rendered to click the Place Order button. Pre_checkout's response is rich, but useless for skipping the nav unless we replace the click with a fetch.
- **Smaller win (Phase 4 candidate):** Use pre_checkout response for delivery option selection (replaces `_handle_delivery_options`) — saves ~150ms.

---

## Endpoint 3 — Cart GET (cart state read)

- **Status:** Already used as a passive read at `purchase_executor.py:1037-1047` (post-ATC verification).
- **Method:** GET
- **URL:** `https://carts.target.com/web_checkouts/v1/cart?cart_type=REGULAR&field_groups=CART,CART_ITEMS` (also `field_groups=ADDRESSES` and `field_groups=CART,CART_ITEMS,DELIVERY_WINDOWS,...,SUMMARY` variants observed).
- **Headers:** `credentials: 'include'`, `Accept: application/json`. Does NOT need Shape X-headers (read-only auth).
- **Use:** Ground truth on cart contents (`cart_items[].tcin`, `cart_items[].quantity`, `cart_items[].cart_item_id` — that last one is the key for the DELETE endpoint).

---

## Endpoint 4 — Fulfillment Consolidations GET ✅ CAPTURED (2026-05-06)

- **Method:** GET
- **Full URL:** `https://carts.target.com/digital_checkouts/v1/cart_fulfillments/consolidations?key=e59ce3b531b2c39afb2e2b8a71ff10113aac2a14&cart_type=REGULAR&limit=20&within=25`
- **Params:** `key` is the **public web API key** (same value used by every other web_checkouts call — not a cart ID). `cart_type=REGULAR`, `limit=20`, `within=25` (radius miles for store-pickup options).
- **Headers (9 total):** `Accept`, `Cookie`, `Origin: https://www.target.com`, `Referer: https://www.target.com/checkout`, `User-Agent`, `sec-ch-ua*` (3 variants), `x-application-name: web`. **Only 1 Shape X-header** in the cache at the time of fire — same as pre_checkout. No `Content-Type` (it's a GET).
- **Use:** Read-only enumeration of delivery options for the cart. Fires *automatically* during checkout page load — the bot already passively benefits.
- **DOM step it would replace:** `_handle_delivery_options` at `purchase_executor.py:2381`.
- **Phase 4a finding (2026-05-06):** **Replacement not viable on the warmed-up account hot path.** Both captured runs landed in `place_order` state on first checkout entry, with `[DELIVERY] Already in review state — skipping` — the DOM delivery step is already a no-op. Replacing it with a fetch saves nothing on the path the bot actually takes. Endpoint 4 stays *passively observed* (read by the page during nav). Promote to active replacement only if a future cart configuration (e.g. SHIP-only multi-day-window items, address change mid-checkout) starts triggering the DOM delivery step on the hot path.

---

## Endpoint 5 — Cart PUT ✅ CAPTURED (2026-05-06) — but not the S&C variant

- **Method:** PUT
- **Full URL:** `https://carts.target.com/web_checkouts/v1/cart?cart_type=REGULAR&field_groups=ADDRESSES%2CCART%2CCART_ITEMS%2CFINANCE_PROVIDERS%2CPROMOTION_CODES%2CSUMMARY&key=e59ce3b531b2c39afb2e2b8a71ff10113aac2a14`
- **Params:** Same public web API key. `field_groups` is URL-encoded (commas → `%2C`): `ADDRESSES`, `CART`, `CART_ITEMS`, `FINANCE_PROVIDERS`, `PROMOTION_CODES`, `SUMMARY`.
- **Headers (10 total):** `Accept: application/json`, `Content-Type: application/json`, `Cookie`, `Origin: https://www.target.com`, **`Referer: https://www.target.com/cart`** (key tell — see below), `User-Agent`, `sec-ch-ua*`, `x-application-name: web`. Only 1 Shape X-header at fire time.
- **Body (213 chars, identical across both runs):**
  ```json
  {"cart_type":"REGULAR","shopping_context":"DIGITAL","channel_id":"10","guest_location":{"country":"US","latitude":"42.056656","longitude":"-87.968300","state":"IL","zip_code":"60056"},"shopping_location_id":"880"}
  ```
  This is a **cart-init** PUT (re-asserts location + channel context). Body has no delivery-method or address-id fields.
- **Use:** Cart-context refresh fired by `target.com/cart` page load (Referer is `/cart`, not `/checkout`).
- **NOT what S&C clicks fire.** The captured PUTs above fired during `_clear_cart`'s navigation back to `/cart` for cleanup — they are not the shipping/payment "Save & Continue" mutation.
- **Phase 4a finding (2026-05-06):** The shipping/payment S&C variant of this PUT was **not observed** in either captured run, because the bot reached `place_order` state directly with `FLOW A: Place Order already enabled — no S&C needed`. Both runs printed `[PAYMENT] FLOW A: Place Order already enabled — no S&C needed`. To capture the S&C-variant PUT, the test cart needs to land in a state where S&C buttons appear (e.g. cart cleared between sessions, or a new payment method added). Until then, `_api_save_continue` is unsafe to write — there is no concrete body shape to send.
- **What we DO have:** the cart-init PUT body. This is potentially useful for a future "force-recompute cart" call after `_clear_cart`, but is not on the Phase 4a critical path.

---

## Endpoint 6 — Cart Items DELETE (cart clear) ✅ API-MODE BEHIND FLAG

- **Status:** Phase 4c implemented (2026-05-06). Behind `TARGET_API_CART_CLEAR=true`. Default off; when off, the DOM trash-icon flow at `_clear_cart` is unchanged.
- **Method:** DELETE
- **URL:** `https://carts.target.com/web_checkouts/v1/cart_items/<cart_item_id>` (cart_item_id is a UUID — not the TCIN)
- **Headers observed:** 16 total, 7 Shape X-headers (more than ATC — this is an authenticated mutation). The Phase 4c implementation reuses the cached Shape header pattern from ATC (strip Cookie/Referer, force `x-application-name:'web'`).
- **Flow:**
  1. `GET /cart?cart_type=REGULAR&field_groups=CART,CART_ITEMS` to pull `cart_items[].cart_item_id`.
  2. `DELETE /cart_items/<cart_item_id>` for each — issued serially (parallel would race the cart-state mutation).
  3. On any non-2xx the call falls back to the DOM clear path (no behavior regression).
- **Code:** `purchase_executor.py:_api_clear_cart` (helper) + flag check at top of `_clear_cart`.
- **Limitations:** SFL (Saved-For-Later) items are not handled by the API path — they live behind a different endpoint family that is not yet documented. The DOM SFL pass at `_clear_cart` Pass 2 still runs after the API path succeeds.
- **Use:** Removing items post-checkout (TEST_MODE) and during failure recovery.

---

## Endpoint 7 — Place Order POST ✅ IMPLEMENTED behind flag (2026-05-06)

- **Status:** `_api_place_order` written and wired into `_place_order` behind `TARGET_API_PLACE_ORDER=true` (default off). API attempt fires first; on non-success falls back to DOM click — except for terminal rejections (`oos`, `reservation_failure`) which return False directly to avoid double-attempt against an already-rejected cart. `TARGET_API_PLACE_ORDER_OBSERVE=true` (default off) logs the full success-response body to `logs/api_capture.log` so order_id parsing can be verified on the first real-order run. Capture flag is also checked: if `TARGET_API_CAPTURE_PLACE_ORDER` is on, the helper refuses to fire (interceptor would abort). Captured 2026-05-06 via `TARGET_API_CAPTURE_PLACE_ORDER=true` PROD-mode capture-and-abort. Full URL + headers + body in `logs/api_capture.log` from 2026-05-06T14:17:44.
- **Method:** POST
- **Full URL:** `https://carts.target.com/web_checkouts/v1/checkout?cart_type=REGULAR&field_groups=ADDRESSES%2CCART%2CCART_ITEMS%2CFINANCE_PROVIDERS%2CPAYMENT_INSTRUCTIONS%2CPICKUP_INSTRUCTIONS%2CPROMOTION_CODES%2CSUMMARY&key=e59ce3b531b2c39afb2e2b8a71ff10113aac2a14`
- **URL params:** Standard public web key. `field_groups` (URL-encoded commas): `ADDRESSES`, `CART`, `CART_ITEMS`, `FINANCE_PROVIDERS`, `PAYMENT_INSTRUCTIONS`, `PICKUP_INSTRUCTIONS`, `PROMOTION_CODES`, `SUMMARY`. The response carries all of these for confirmation rendering.
- **Body (41 chars total):**
  ```json
  {"cart_type":"REGULAR","channel_id":"10"}
  ```
  **The entire order context is implicit from the cookie session + cart state.** No items, no address, no payment ID, no CVV in the body. This is dramatically simpler than expected — the server resolves everything from the authenticated cart.
- **Headers (16 total, 7 Shape X-headers):** Full Shape token set (`X-GyJwza5Z-a/b/c/d/f/z`). Same rotation namespace as ATC `cart_items` POST. **No separate warmup needed** — the existing `_cached_cart_headers` (refreshed by the warmup tab POST'ing to `cart_items`) provides everything.
- **Phase 4b implementation (DONE 2026-05-06):** `_api_place_order` at `purchase_executor.py` patterned on the ATC fetch (header-injection identical: strip Cookie/Referer, force `x-application-name:'web'`, age-gate cached Shape headers at 90s with proactive 60s refresh). Wired into `_place_order` behind `TARGET_API_PLACE_ORDER=true`. On `success` the response body is JSON-parsed for the order_id (tries `order_id` / `orderId` / `order_number` / `id` / `reference_id` at root + same keys under `order`); regex fallback handles unknown shape. Synthesized `confirmation_url = https://www.target.com/checkout/confirmation?orderId=<order_id>` if response doesn't carry one. Stashed on `self._api_order_id` / `self._api_confirmation_url`; consumed by `_complete_checkout`'s success-dict builder (preferred over URL parsing).
- **Order-id parsing — VALIDATED 2026-05-06.** OBSERVE run on TCIN 50270379 confirmed the success-response shape: `{"orders":[{"order_id":"<uuid>","reference_id":"<10-15 digit>", ...}]}`. Parser pulled `orders[0].order_id` on first try; root-level + `order` singular + regex fallbacks were not exercised but kept defensively in case the shape ever drifts. Order placed for real (`69341e41-49a9-11f1-8a23-dd806c72f8cb`). `_OBSERVE` flag should be omitted in normal operation.
- **Confirmation-page side effects:** DOM mode also lands on `/checkout/confirmation?orderId=...` and `_complete_checkout` reads the URL plus calls `session_manager.save_session_state()`. API mode does NOT navigate, so any post-confirmation page side effects (analytics pixel firing, order-tracking link rendering) are skipped. Bot only needs the `order_id` for the success dict — this is fine, but worth noting if a future feature needs to scrape the confirmation page.

---

## Endpoint 8 — CVV Submit PUT 🟢 URL + SEQUENCE CONFIRMED 2026-07-21 (body still missing)

**Method/URL (confirmed):** `PUT https://carts.target.com/checkout_payments/v1/payment_instructions/<payment_instruction_id>`
— same host as everything else, so the existing `*carts.target.com*` CDP interception
pattern already catches it. `<payment_instruction_id>` is a UUID (observed:
`cb546a01-84da…`, `44c47dd1-79dc…`, `d4e591d1-84e8…` — it differs per cart) and is
carried in the **pre_checkout response's `payment_instructions[]`**, which
`_api_fast_lane` now extracts, so the fast path can build this URL without an extra GET.

**Headers:** 10 total, **only 1 Shape X-header** (vs 7 on place-order) — this is a
low-security mutation, so the full Shape token set is not required. Preceded by a CORS
`OPTIONS` preflight to the same URL.

**Confirmed firing sequence** (`logs/purchases/purchase_95267143_20260721_032616.log`):
```
API place-order POST            -> 400 tgt-cart-error-key: MISSING_CREDIT_CARD_CVV
DOM click Place Order
OPTIONS .../payment_instructions/<id>        <- preflight
CVV modal filled + Confirm clicked (0.022s)
PUT     .../payment_instructions/<id>        <- THIS endpoint
POST    /web_checkouts/v1/checkout           <- only now can the order succeed
```

**Still missing: the request body.** That run had `TARGET_API_CAPTURE_CHECKOUT_STEPS`
off, so the interceptor logged the PUT but not its `post_data`. The flag is now on and
the `is_cvv_put` capture branch is armed, so the next time the CVV modal fires the body
lands in `logs/api_capture.log`. **Do not guess this body** — a wrong PUT could corrupt
the cart's payment instruction. Once captured, insert the PUT between steps 2 and 3 of
`_api_fast_lane` and the fast lane works on a CVV-challenged card (today it deliberately
skips itself when the CVV latch is on, which is the fix's biggest remaining gap).

### Historical note — why this sat unknown until 07-21

## ~~Endpoint 8 — CVV Submit POST~~ 🟡 NOT FIRED IN 2026-05-06 CAPTURE

- **Status:** Did NOT fire during the 2026-05-06 PROD capture run on the gum SKU. The Place Order POST body (Endpoint 7 above) does NOT include CVV. Possible interpretations:
  1. The user's saved card on this account does not require CVV re-prompt for low-risk purchases — Endpoint 8 is conditional, not always-fires.
  2. CVV is handled by a separate POST that fires only when the CVV modal is triggered in DOM mode (`_handle_cvv_modal:1594`). On this run the modal never appeared (Place Order was already enabled, FLOW A).
  3. CVV may be tokenized client-side and bound into a hidden field of the cart state ahead of Place Order — meaning the "CVV submit" is actually a `cart_payment_instruction` PUT, not a separate POST.
- **Phase 4b decision:** Treat CVV as conditional-DOM. Keep `_handle_cvv_modal` as-is for now. If a future PROD capture run with `TARGET_API_CAPTURE_CHECKOUT_STEPS=true + TARGET_API_CAPTURE_PLACE_ORDER=true` ever sees an unknown POST/PUT during a CVV-modal scenario, that will surface Endpoint 8.
- **Same capture options as Endpoint 7** if you want to force a CVV-prompting card.

---

## Headers cache strategy

Per the warmup-tab interceptor at `purchase_executor.py:380-391`, only POST requests overwrite `_cached_cart_headers`. ATC works by POST'ing to `cart_items`. For endpoints that need *different* Shape header sets (e.g. potentially `/checkout` POST), the warmup tab needs to also trigger that POST so its headers get cached. This is an open architectural question for Phase 3+:

- **Option A:** A single warmup tab cycles through ATC and a "fake checkout" to capture both header sets. Works but adds bot-visible behavior to the warmup loop.
- **Option B:** A second warmup tab parked on the checkout page does the periodic fetches that capture checkout-namespace headers. Doubles the warmup cost.
- **Option C:** Capture once during the actual hot path (the executor's first DOM checkout in any session) and cache. Cheap but cold-start vulnerability.

Decision deferred to Phase 4 implementation.

---

## DOM steps that don't need API replacement

Some current DOM activity isn't a checkout endpoint at all and stays DOM:

- **CVV modal detection** — DOM-driven because Target only shows the modal under certain card-on-file conditions. Detection stays DOM; CVV submit endpoint becomes API (Endpoint 8).
- **Delivery-day modal dismissal** — UI state, no underlying POST per current observation.
- **"Busier than expected" modal** — UI-only retry trigger.
- **Address verification modal** — UI-only confirmation.

These remain DOM in `purchase_executor.py:_handle_*_modal`.

---

## Implementation phasing (per main plan)

- **Phase 3 ✅ IMPLEMENTED 2026-07-21 — `_api_fast_lane`:** Endpoint 2 expansion — the `/checkout/start` navigation is gone from the hot path. Endpoints 1 → 2 → 7 now run as a **single `tab.evaluate` fetch chain** (`TARGET_FAST_LANE=1`, default on), so there is no CDP round-trip between steps either. Measured saving is far bigger than the 1-2s estimated here: the 07-20→21 log shows `CHECKOUT_TRANSITION` (ATC-201 → place-order POST) at **2.4-5.0s**, essentially all of it `tab.get('/checkout/start')` plus the DOM poll for the Place Order button. The chain is ~0.85s. That gap was *losing drops*, not just costing time — clean CVV-free first shots came back `429 RESERVATION_FAILURE` because the reservation died inside the nav (see `docs/FAILURES.md` 2026-07-21 PM).
  - **The 2026-05-07 blocker is resolved, not bypassed.** Dropping the nav then produced `424 CART_COMPARISION_FAILURE_ERROR` on all 3 cycles because it raced the **fire-and-forget** pre_checkout. The fast lane **awaits** pre_checkout inside the chain, so the race cannot occur; if pre_checkout returns non-2xx the chain refuses to fire Endpoint 7 at all and hands back to the legacy nav path.
  - **Cart-contents guard (new):** Endpoint 7 buys the *whole cart*, so the chain aborts before firing if pre_checkout's `cart_items[]` contains any TCIN that is not the one being bought. The pre-07-21 path only logged a warning.
  - **Intel side-effect:** the chain returns `payment_instructions[].{id,type,cvv_required}` from the pre_checkout response — the prerequisite for Endpoint 8 below, obtainable without a dedicated capture run.
  - Validated: `tests/test_fast_lane_checkout.py` (37/37), which runs the real chain JS under node with a stubbed `fetch` and asserts the exact URL sequence.
- **Phase 4a:** ~~Endpoints 4 (delivery) and 5 (cart PUT for shipping S&C) — replace `_handle_delivery_options` and the shipping/payment S&C clicks.~~ **Closed without code change (2026-05-06).** Capture confirmed both endpoints fire only on cold-cart paths the warmed-up bot does not take. The hot path already lands in `place_order` state on first checkout entry — there is no DOM delivery step or S&C click to replace. See Endpoints 4 + 5 above for full reasoning. Reopens only if a future cart configuration starts triggering S&C on the hot path.
- **Phase 4b ✅ LIVE-VALIDATED (2026-05-06):** Endpoints 7 + 8. Endpoint 7 (`_api_place_order`) wired behind `TARGET_API_PLACE_ORDER=true`. Real production OBSERVE run completed: HTTP 200, 0.92s, order_id parsed correctly from `orders[0].order_id`, real order placed (`69341e41-49a9-11f1-8a23-dd806c72f8cb`). End-to-end checkout reduced from ~12-15s (DOM) to **7.34s**. DOM fallback on non-terminal failures (Shape block / 401 / unknown HTTP); terminal rejections (OOS / RESERVATION_FAILURE) bail without DOM retry. Endpoint 8 deferred — saved-card path on the gum SKU never fired CVV modal; conditional DOM fallback at `_handle_cvv_modal` covers the rare case.
- **Phase 4c ✅ LIVE-VALIDATED (2026-05-06, single-item):** Endpoint 6 (cart clear DELETE) — 5 TEST_MODE cycles, all 200 first try (commit `2b941af1`). Multi-item / SFL-only paths still untested live. Low priority.
- **Phase 5 ✅ DONE:** Worker class wraps session_manager + purchase_executor.
- **Phase 6 ✅ IMPLEMENTED, N=2 UNTESTED:** WorkerPool dispatch with per-Worker async loops + `acquire_for_tcin`. At N=1 (default) the primary Worker binds the existing global event loop, preserving legacy `run_coroutine_threadsafe` from the stock monitor. **Live test at N≥2 has not been performed** — requires a second Target account + `target-2.json` against a separate Chrome profile (`TARGET_SESSION_PATH`/`TARGET_PROFILE_DIR`). Parked while we operate single-account.

Each behind its own env flag: `TARGET_API_PRECHECKOUT_NAV_SKIP`, `TARGET_API_DELIVERY`, `TARGET_API_PAYMENT_SAC`, `TARGET_API_PLACE_ORDER`, `TARGET_API_CVV`, `TARGET_API_CART_CLEAR`. Default off.

## Capture flags (research; default off)

- `TARGET_API_CAPTURE_PLACE_ORDER` — capture-and-abort the `/web_checkouts/v1/checkout` POST. Existing flag for Endpoint 7. Logs URL+headers+body to `logs/api_capture.log` and returns synthetic 503 to the bot — order is never placed. Cookie redacted.
- `TARGET_API_CAPTURE_CHECKOUT_STEPS` — pass-through capture for cart PUT (Endpoint 5) and `cart_fulfillments` GET (Endpoint 4). Logs URL+headers (+body for PUT) to `logs/api_capture.log`. Never aborts. Safe to leave on for a single run; remove when Endpoints 4 + 5 are documented.

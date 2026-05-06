# Target Checkout API — Endpoint Inventory

**Status: Phase 1 — initial inventory from existing code + interceptor logs.** Place Order endpoint requires a live PROD-mode capture to fully document (TEST_MODE never clicks Place Order, so the JS-driven POST never fires). All other documented endpoints are validated against logs from `logs/purchases/purchase_50270379_*` (May 6).

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

## Endpoint 2 — Pre-Checkout POST ✅ ALREADY FIRED

- **Status:** Already fired as fire-and-forget at `purchase_executor.py:1067-1086`. The DOM `tab.get('/checkout/start')` still runs after, which is what makes navigation slow. **Phase 3 candidate:** if pre_checkout returns enough state to skip the navigation, we may be able to drive checkout entirely off the response and skip the page render.
- **Method:** POST
- **URL:** `https://carts.target.com/web_checkouts/v1/pre_checkout?cart_type=REGULAR&field_groups=CART,CART_ITEMS,DELIVERY_WINDOWS,PAYMENT_INSTRUCTIONS,PROMOTION_CODES,SUMMARY,ADDRESSES`
- **Body:** `{"cart_type": "REGULAR"}`
- **Required headers:** Same Shape header pattern as ATC. `keepalive: true` (so the request survives navigation).
- **DOM step it would replace:** `purchase_executor.py:1092` (`tab.get("https://www.target.com/checkout/start")`) — currently always navigates after pre_checkout fires. If response is sufficient, navigation can be eliminated entirely.
- **Open question (live capture needed):** what does the response body contain? Does it include the `placeOrder` URL/key needed for Endpoint 6?

---

## Endpoint 3 — Cart GET (cart state read)

- **Status:** Already used as a passive read at `purchase_executor.py:1037-1047` (post-ATC verification).
- **Method:** GET
- **URL:** `https://carts.target.com/web_checkouts/v1/cart?cart_type=REGULAR&field_groups=CART,CART_ITEMS` (also `field_groups=ADDRESSES` and `field_groups=CART,CART_ITEMS,DELIVERY_WINDOWS,...,SUMMARY` variants observed).
- **Headers:** `credentials: 'include'`, `Accept: application/json`. Does NOT need Shape X-headers (read-only auth).
- **Use:** Ground truth on cart contents (`cart_items[].tcin`, `cart_items[].quantity`, `cart_items[].cart_item_id` — that last one is the key for the DELETE endpoint).

---

## Endpoint 4 — Fulfillment Consolidations GET

- **Method:** GET
- **URL prefix observed:** `https://carts.target.com/digital_checkouts/v1/cart_fulfillments/consolidations?k=...`
- **Use:** Delivery option enumeration (drives the "What day to deliver" modal in DOM mode).
- **DOM step it would replace:** `purchase_executor.py:2213-2229` (`_handle_delivery_options`).
- **Open question (live capture needed):** full URL params, response shape. The `k=` param is a key — possibly a cart ID — needs verification.

---

## Endpoint 5 — Cart PUT (used during clear)

- **Method:** PUT
- **URL prefix:** `https://carts.target.com/web_checkouts/v1/cart?cart_type=REGULAR&field_groups=ADDRESSES,...`
- **Use:** Cart state mutation. Observed during `_clear_cart` flow.
- **Body & full param shape:** unknown — needs live capture.
- **Likely candidate:** if this is what shipping/payment "Save & Continue" buttons fire in DOM mode, it's a Phase 4 conversion target.

---

## Endpoint 6 — Cart Items DELETE (cart clear)

- **Status:** DOM-driven today via `_clear_cart` at `purchase_executor.py:2002`. The DELETE fires server-side from clicking the trash icon.
- **Method:** DELETE
- **URL:** `https://carts.target.com/web_checkouts/v1/cart_items/<cart_item_id>` (cart_item_id is a UUID — not the TCIN)
- **Headers observed:** 16 total, 7 Shape X-headers (more than ATC — this is an authenticated mutation).
- **Use:** Removing items post-checkout (TEST_MODE) and during failure recovery.
- **Phase 3 lite:** Convert `_clear_cart` to direct DELETE fetches. Not on critical path for win rate but reduces the noisy DOM cart-clearing dance.
- **Critical:** must first `GET /cart` to retrieve the `cart_item_id` for each item, then DELETE each.

---

## Endpoint 7 — Place Order POST 🔴 LIVE CAPTURE NEEDED

- **Status:** UNKNOWN URL/body. The bot today uses `_dispatch_click(place_order_button)` at `purchase_executor.py:2762`. The actual POST fires from Target's React handler. TEST_MODE never reaches this path so logs don't contain the request.
- **Best guess (from URL conventions):** likely `POST https://carts.target.com/web_checkouts/v1/checkout` (the interceptor at line 334 watches `web_checkouts/v1/checkout` POST, suggesting this is the known endpoint name).
- **Live capture method:** to be determined. Options:
  1. PROD_MODE walk on the $1.99 gum SKU (50270379) and let the Place Order actually fire — captures the full POST. Costs $1.99.
  2. Static analysis of Target's checkout JS bundle to extract the URL pattern.
  3. Browser DevTools manual checkout (user-driven) on a cancellable item, capture from Network tab.
- **What we already know from the interceptor at line 334:** the URL contains `web_checkouts/v1/checkout`, method is POST, and the response carries `tgt-cart-error-key` headers on rejection (RESERVATION_FAILURE, INVENTORY_NOT_AVAILABLE, CART_COMPARISON_FAILURE).
- **Headers expected:** Same Shape pattern as cart_items POST, plus likely additional auth tokens fetched during checkout-page render.

---

## Endpoint 8 — CVV Submit POST 🔴 LIVE CAPTURE NEEDED

- **Status:** UNKNOWN. Driven by DOM modal at `_handle_cvv_modal:1594`. Today the CVV is typed into an `<input>` field char-by-char and a submit button is clicked. The underlying POST is unobserved.
- **Open questions:** dedicated endpoint or part of Place Order body? Does it use different Shape headers? Is there a payment-tokenize step before the CVV gets POSTed?
- **Same capture options as Endpoint 7.**

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

- **Phase 3 (first conversion):** Endpoint 2 expansion — use the pre_checkout response to short-circuit the `/checkout/start` navigation. If response carries enough state (placeOrder URL, summary), `await tab.get(...)` becomes optional. Estimated saving: 1-2s per attempt.
- **Phase 4a:** Endpoints 4 (delivery) and 5 (cart PUT for shipping S&C) — replace `_handle_delivery_options` and the shipping/payment S&C clicks.
- **Phase 4b:** Endpoints 7 + 8 (Place Order + CVV) — requires live capture first.
- **Phase 4c:** Endpoint 6 (cart clear DELETE) — low priority; nice cleanup of `_clear_cart`.

Each behind its own env flag: `TARGET_API_PRECHECKOUT_NAV_SKIP`, `TARGET_API_DELIVERY`, `TARGET_API_PAYMENT_SAC`, `TARGET_API_PLACE_ORDER`, `TARGET_API_CVV`, `TARGET_API_CART_CLEAR`. Default off.

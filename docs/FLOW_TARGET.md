# Target Purchase Flow

## Selectors
(last verified: 2026-04-07 via @retailer-researcher — cross-referenced against live purchase_executor.py)

**Add to cart button (priority order):**
- `button[id^="addToCartButtonOrTextIdFor"]` ← primary confirmed in-code
- `button[data-test="addToCartButton"]`
- `button[data-testid="addToCartButton"]`
- `[data-testid*="add-to-cart"]`
- `button[data-test*="addToCart"]`
- `button[data-test*="add-to-cart"]`
- `button[data-test="chooseOptionsButton"]` ← variant/size picker entry
- Text fallback: "add to cart", "preorder", "pre-order", "add to bag", "ship it"
- Enabled check: `!el.disabled && el.getAttribute('aria-disabled') !== 'true'` + `getBoundingClientRect().width > 0`

**Shipping fulfillment option (product page — before ATC):**
- `[data-test="fulfillment-cell-shipping"]`
- `[data-test="shipItButton"]`
- `[data-testid="fulfillment-cell-shipping"]`
- Text fallback: "Ship it", "Ship It", "Ship"
- Check `aria-pressed` or `aria-selected` before clicking to skip if already selected

**Cart confirmation signals (post-ATC — any one sufficient):**
- `[data-test="add-to-cart-confirmation"]` — flyout (height > 0)
- `[data-test="cart-count"]` or `[data-testid="cart-count"]` — badge count > 0
- `[data-test="cart-drawer"]` — drawer (height > 0)
- Cart page: `[data-test="cart-item"]` or `[data-testid="cart-item"]` — items present

**Cart item removal (test mode / error recovery):**
- `button[data-test="cartItem-remove"]` ← primary
- `button[aria-label*="remove"]`
- `button[aria-label*="Remove"]`
- Saved-for-later: `button[data-test="sflItem-remove"]`, `button[data-test="sfl-item-remove"]`, `[data-testid="sflItem-remove"]`
- Empty cart confirmation: `[data-test="empty-cart"]` or body text "Your cart is empty"

**Save & Continue button:**
- `[data-test="save-and-continue-button"]` ← single canonical selector
- Text fallback: button text `"save and continue"` or `"save & continue"` (case-insensitive)
- Use MutationObserver to wait for React to render it before clicking
- Click via `dispatchEvent(new MouseEvent('click', {bubbles:true}))` — not `tab.select().click()`

**Checkout radio steps (inside S&C loop):**
- Step detection: scan all `input[type="radio"][offsetParent != null]`
- Delivery step identified by label text containing: "ship", "shipping", "pickup", "drive up", "same-day"
  - Select radio whose label includes ship/shipping keyword and NOT pickup/drive up keyword
- Payment step: no delivery keywords in labels → select first non-wallet radio (skip Apple Pay, PayPal, Affirm, etc.)
- Wallet/digital payment keywords to skip: "apple pay", "paypal", "cash app", "affirm", "venmo", "klarna", "afterpay", "sezzle", "zip"

**Save & Continue delivery options (checkout page — if delivery step active before S&C):**
- `[data-test="shipping-option"]`
- `[data-test="ship-option"]`
- `input[value="SHIPPING"]`
- `input[id*="shipping"]`
- `input[id*="ship-"]`

**Place Order button:**
- `[data-test="placeOrderButton"]` ← primary canonical selector
- `[data-testid="placeOrderButton"]`
- `#placeOrderButton`
- `button[class*="place-order"]`, `button[class*="placeOrder"]` ← CSS fallbacks (fragile)
- Text fallback: "place your order", "place order", "complete order"
- **Enabled check (all 5 conditions must pass):**
  1. `!el.disabled`
  2. `el.getAttribute('aria-disabled') !== 'true'`
  3. `!(el.className.toLowerCase().includes('disabled') || .includes('inactive'))`
  4. `getComputedStyle(el).pointerEvents !== 'none'`
  5. `parseFloat(getComputedStyle(el).opacity) >= 0.6`

**CVV modal input (priority order):**
- `input[name="cvv"]`
- `input[name="cvc"]`
- `input[id*="cvv" i]`
- `input[id*="cvc" i]`
- `input[placeholder*="CVV" i]`
- `input[placeholder*="security" i]`
- `input[aria-label*="CVV" i]`
- `input[aria-label*="security code" i]`
- `input[data-test*="cvv" i]`
- Fill via native setter: `Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, cvv)` then dispatch `input` + `change` + `blur` events — required for React form validation

**CVV confirm button (after filling):**
- `[data-test*="cvv"]`
- `[data-test="cvv-confirm-button"]`
- `[data-test="confirm-cvv"]`
- `[data-test*="confirm"]`
- `[data-test*="submit"]`
- `button[type="submit"]`
- Text fallback: "confirm", "submit", "continue" (case-insensitive)
- Poll up to 500ms for button to enable after CVV fill before clicking

**Login status detection:**
- `[data-test="@web/AccountLink"]`
- `[data-test="accountNav"]`
- `button[aria-label*="Account"]`
- `button[aria-label*="Hi,"]`
- Text: "Hi," present → logged in; "Sign in" present → logged out

**Sticky banner dismissal (before ATC):**
- `[data-test="app-banner-close"]`
- `[aria-label*="close"][class*="banner"]`
- `[aria-label*="dismiss"][class*="banner"]`
- `[data-test*="app-banner"] button`
- Fallback: JS hides all fixed/sticky elements in bottom 20% of viewport

**Error flyout dismissal (after failed ATC):**
- `[data-test="close-modal"]`
- `[aria-label="close"]` / `[aria-label="Close"]`
- `button[data-test*="close"]`
- `[data-testid="close-modal"]`
- Text fallback: button text "continue shopping"

**Order confirmation:**
- URL contains: `order-confirmation` (primary), `confirmation`, or `thank`
- Page text: "Thanks for your order!", "Order confirmed", "Thank you", "Your order has been placed", "Order number"
- CSS: `[data-test*="order-confirmation"]`, `[data-testid*="order-confirmation"]`
- URL pattern: `https://www.target.com/order-confirmation` (confirmed live)

## ATC POST — `carts.target.com`

**Endpoint:**
```
POST https://carts.target.com/web_checkouts/v1/cart_items?field_groups=CART,CART_ITEMS,SUMMARY
```
(Key param in URL: `key=<api_key>` — optional, some implementations include it)

**Required headers:**
- `Content-Type: application/json`
- `Accept: application/json`
- `Origin: https://www.target.com`
- `Referer: https://www.target.com/p/-/A-{TCIN}` ← must match product page
- `x-application-name: web`
- Shape Security `X-*` headers (captured via CDP `Fetch.enable` interceptor, 90s TTL)
- Credentials: `include` (sends session cookies automatically — do NOT pass Cookie header manually)

**Request body:**
```json
{
  "cart_item": {
    "tcin": "{TCIN}",
    "quantity": "{QUANTITY}",
    "item_channel_id": "10",
    "fulfillment_type": "SHIPPING",
    "fulfillment_type_code": "02"
  },
  "cart_type": "REGULAR",
  "channel_id": "10",
  "shopping_context": "DIGITAL"
}
```

**Quantity selection** (added 2026-05-05): `{QUANTITY}` is sourced at stock-monitor
time from the RedSky `product_summary_with_fulfillment_v1` response. The monitor
extracts the per-customer cap from
`item.fulfillment.maximum_order_quantity.shipping.value` (newer shape) or
`item.fulfillment.purchase_limit` (older), takes the lesser of that and
`fulfillment.shipping_options.available_to_promise_quantity`, and clamps to
[1, 10]. The value rides on the per-TCIN stock dict as `max_qty` and is passed
through `BulletproofPurchaseManager.start_purchase(..., max_qty=...)` →
`PurchaseExecutor.execute_purchase(..., quantity=...)` → ATC POST. When neither
RedSky cap is present, defaults to 1.

The warmup POST (fake TCIN, fires from `_warmup_tab` to capture Shape headers)
intentionally still uses `quantity: 1`. Real users always tap "Add to cart"
once before adjusting quantity, so warming with anything else would itself be
a fingerprint signal.

**ATC response status codes:**
- `200` / `201` — success, item added to cart
- `401` — auth denied (write token expired); wait for React to refresh token (button enabled = token fresh), then retry
- `403 + HTML body` — Shape Security block (stale/missing Shape headers or 403 with JSON = different error)
- `409` / `422` — item OOS at cart API (body contains `OUT_OF_STOCK`)
- `409` / `422` — per-customer purchase limit exceeded (body contains `PURCHASE_LIMIT` / `MAX_QUANTITY` / `EXCEEDED`); executor retries once with `quantity: 1` if RedSky's reported cap was wrong
- `424` — checkout POST rejected (see `tgt-cart-error-key` response header)

## `pre_checkout` API Call

**Endpoint:**
```
POST https://carts.target.com/web_checkouts/v1/pre_checkout?cart_type=REGULAR&field_groups=CART,CART_ITEMS,DELIVERY_WINDOWS,PAYMENT_INSTRUCTIONS,PROMOTION_CODES,SUMMARY,ADDRESSES
```
**Body:** `{"cart_type": "REGULAR"}`

**When it fires:** Fire-and-forget immediately before navigating to `/checkout/start`. Use `keepalive: true` on the fetch so the request completes even after page navigation. Target's cart page JS fires this and then redirects without awaiting the response — mirror this exactly. The server processes it in ~200-300ms; by the time Place Order fires (~1.5s+ later) it has long completed.

**Required?** Yes — omitting pre_checkout causes checkout to skip payment/address pre-population, leading to empty form state when S&C is clicked. It initializes the checkout session server-side.

## Cart GET (inventory check before checkout)

```
GET https://carts.target.com/web_checkouts/v1/cart?cart_type=REGULAR&field_groups=CART,CART_ITEMS
```
Credentials: include. Returns `{cart_items: [{tcin, quantity}]}`. Use to detect duplicate items from prior failed attempts (count > 1 = stale item from previous cycle).

## Checkout Error Codes (`tgt-cart-error-key` response header — from 424 responses)

| Code | Meaning |
|------|---------|
| `RESERVATION_FAILURE` | Race condition — someone else bought last unit between ATC and Place Order |
| `INVENTORY_NOT_AVAILABLE` | Item OOS at checkout submission time |
| `CART_COMPARISION_FAILURE` | Cart state mismatch (server cart differs from checkout session) |

These appear on the `tgt-cart-error-key` response header of the checkout POST (`web_checkouts/v1/checkout`), intercepted via CDP RESPONSE stage. The CDP interceptor watches `*web_checkouts/v1/checkout*` at RESPONSE stage and `*carts.target.com*` at REQUEST stage simultaneously.

## Step-by-Step Flow (SPA — all steps inside /checkout/start)
1. Navigate to product page: `https://www.target.com/p/-/A-{TCIN}`
2. (Optional) Select Shipping fulfillment option if product offers multiple fulfillment types
3. ATC: `POST https://carts.target.com/web_checkouts/v1/cart_items?field_groups=CART,CART_ITEMS,SUMMARY`
   - Primary: fetch-based POST fired immediately (no need to wait for page render)
   - Fallback: ATC button click if fetch returns non-200/201
   - On 401: wait for ATC button to enable (= token refresh complete), then retry fetch
4. Verify cart: badge count > 0, flyout visible, or API GET confirms item in cart
5. Fire pre_checkout (fire-and-forget, `keepalive: true`): `POST .../pre_checkout`
6. Navigate to: `https://www.target.com/checkout/start`
7. Wait for either `[data-test="placeOrderButton"]` or `[data-test="save-and-continue-button"]` to appear
8. If Place Order immediately visible and enabled → skip to step 10 (account pre-fills everything)
9. S&C loop (max 6 iterations):
   a. Handle radio buttons (delivery → select Shipping; payment → select saved card)
   b. Wait for `[data-test="save-and-continue-button"]` via MutationObserver (max 5s)
   c. Click S&C via mouse event sequence (mousedown + mouseup + click, all bubbling)
   d. Wait for transition: Place Order enabled → exit loop; next S&C appears → continue loop
   e. Check for address-verify modal after each S&C click and dismiss if found
10. Find enabled Place Order button: `[data-test="placeOrderButton"]` (check all 5 enabled conditions)
11. Dismiss any "busier than expected" banner if present before clicking
12. Click Place Order
13. Poll for CVV modal (up to 12s): fill CVV → dispatch input/change/blur events → click confirm button
14. Watch for:
    - URL containing `order-confirmation`/`thank`/`confirmation` → success
    - "Busier than expected" / "temporary issue" → dismiss modal, retry Place Order (max 3 attempts)
    - OOS/stock error → fail immediately
    - Still on checkout after 12s → fail
15. Order confirmed: URL is `https://www.target.com/order-confirmation` (or variant)

## Known Fragile Points
- **CVV modal always appears** for saved cards after Place Order click — must handle every time; poll for it immediately after click and handle before checking for busy/stock errors
- **Place Order enabled state is CSS-based**, not HTML `disabled` — must check all 5 conditions: `!disabled`, `aria-disabled !== 'true'`, no `disabled`/`inactive` in className, `pointerEvents !== 'none'`, `opacity >= 0.6`
- **"Busier than expected" modal** during high-traffic drops — phrases: "busier", "temporary issue", "can't view", "try again soon", "busy right now", "limiting how many guests", "please keep trying" — retry Place Order up to 3x after dismissal
- **Shape header TTL ~90s** — cached headers go stale; ATC POST returns 403 HTML if reusing expired headers; warmup tab fires dummy POST every cycle to keep headers fresh
- **ATC 401 path** — write token expires; poll for ATC button `'ready'` state (not badge — GET /cart always returns 200 so it's not a valid auth proxy); button enabled = write token refreshed
- **ATC cookies are session-scoped** — do not reuse across purchase attempts
- **Cookie domain mismatch** — `.target.com` domain required for `carts.target.com` requests; `www.target.com`-scoped cookies must be re-injected with domain `.target.com` via CDP `Network.setCookie`; skip analytics cookies (visitorId, GA, etc.)
- **CDP surface minimization** — only `Fetch.enable` on patterns `*carts.target.com*` (REQUEST) and `*web_checkouts/v1/checkout*` (RESPONSE); do not call `Runtime.enable` (Shape detects it)
- **Warmup tab dummy POST** — cart page load only triggers GET/PUT; a dummy POST to `cart_items` with fake TCIN `00000000` is required to trigger Shape JS to generate and attach X-* headers
- **RedSky API key rotation** — two keys hardcoded for redundancy (`ff457966e64d5e877fdbad070f276d18ecec4a01`, `9f36aeafbe60771e321a7cc95a78140772ab3e96`); monitor for non-200 and rotate
- **Checkout error codes** from `tgt-cart-error-key` response header (CDP RESPONSE intercept on checkout POST):
  - `RESERVATION_FAILURE` — race condition loss (someone else got last unit)
  - `INVENTORY_NOT_AVAILABLE` — OOS at checkout time
  - `CART_COMPARISION_FAILURE` — cart state mismatch
- **Address verify modal** — appears after S&C click on some networks ("Verify address", "Sorry something went wrong", "Address not found"); dismissible via `[data-test*="confirm"]` or `[data-test*="use-address"]` inside the dialog
- **S&C on empty form** — if checkout form has no filled inputs (billing/shipping not pre-filled), S&C click will fail; detect via form state check before clicking
- **pre_checkout `Referer` header** — must be `https://www.target.com/cart` (not the product page); Target's cart page JS uses this Referer when firing pre_checkout

---

## Last Updated
- Target: 2026-04-08 — Full selector and flow audit by @retailer-researcher; primary source: live `src/session/purchase_executor.py` (2,947 lines), cross-referenced against Tempo Monitors ATC script docs, Manbearpixel PS5 bookmarklet gist, Refract bot docs. All selectors confirmed present in production codebase.

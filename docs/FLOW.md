# Purchase Flow State

## Target Flow
### Selectors (last verified: 2026-04-07 via @retailer-researcher — cross-referenced against live purchase_executor.py)

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

### ATC POST — `carts.target.com`

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
    "quantity": 1,
    "item_channel_id": "10",
    "fulfillment_type": "SHIPPING",
    "fulfillment_type_code": "02"
  },
  "cart_type": "REGULAR",
  "channel_id": "10",
  "shopping_context": "DIGITAL"
}
```

**ATC response status codes:**
- `200` / `201` — success, item added to cart
- `401` — auth denied (write token expired); wait for React to refresh token (button enabled = token fresh), then retry
- `403 + HTML body` — Shape Security block (stale/missing Shape headers or 403 with JSON = different error)
- `409` / `422` — item OOS at cart API (body contains `OUT_OF_STOCK`)
- `424` — checkout POST rejected (see `tgt-cart-error-key` response header)

### `pre_checkout` API Call

**Endpoint:**
```
POST https://carts.target.com/web_checkouts/v1/pre_checkout?cart_type=REGULAR&field_groups=CART,CART_ITEMS,DELIVERY_WINDOWS,PAYMENT_INSTRUCTIONS,PROMOTION_CODES,SUMMARY,ADDRESSES
```
**Body:** `{"cart_type": "REGULAR"}`

**When it fires:** Fire-and-forget immediately before navigating to `/checkout/start`. Use `keepalive: true` on the fetch so the request completes even after page navigation. Target's cart page JS fires this and then redirects without awaiting the response — mirror this exactly. The server processes it in ~200-300ms; by the time Place Order fires (~1.5s+ later) it has long completed.

**Required?** Yes — omitting pre_checkout causes checkout to skip payment/address pre-population, leading to empty form state when S&C is clicked. It initializes the checkout session server-side.

### Cart GET (inventory check before checkout)

```
GET https://carts.target.com/web_checkouts/v1/cart?cart_type=REGULAR&field_groups=CART,CART_ITEMS
```
Credentials: include. Returns `{cart_items: [{tcin, quantity}]}`. Use to detect duplicate items from prior failed attempts (count > 1 = stale item from previous cycle).

### Checkout Error Codes (`tgt-cart-error-key` response header — from 424 responses)

| Code | Meaning |
|------|---------|
| `RESERVATION_FAILURE` | Race condition — someone else bought last unit between ATC and Place Order |
| `INVENTORY_NOT_AVAILABLE` | Item OOS at checkout submission time |
| `CART_COMPARISION_FAILURE` | Cart state mismatch (server cart differs from checkout session) |

These appear on the `tgt-cart-error-key` response header of the checkout POST (`web_checkouts/v1/checkout`), intercepted via CDP RESPONSE stage. The CDP interceptor watches `*web_checkouts/v1/checkout*` at RESPONSE stage and `*carts.target.com*` at REQUEST stage simultaneously.

### Step-by-Step Flow (SPA — all steps inside /checkout/start)
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

### Known Fragile Points
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

## Walmart Flow
### Selectors (last verified: 2026-04-07 via @retailer-researcher DOM pass)

**CRITICAL: NEVER use CSS class selectors — Walmart hashes class names on every deploy.
Use only `data-automation-id`, `data-testid`, `aria-label`, `data-tl-id`, or `:has-text()` selectors.**

---

**Add to Cart button (product page — priority order):**
- `button[data-automation-id="add-to-cart-btn"]` ← primary, confirmed in purchase_executor.py
- `button[data-tl-id="ProductPrimaryCTA-cta_add_to_cart_button"]` ← confirmed secondary
- `button:has-text("Add to cart")`
- `button:has-text("Add to Cart")`
- `button:has-text("Pre-order")`
- `button:has-text("Pre-Order")`
- `button:has-text("Preorder")`
- Note: also look for `[data-item-id="{item_id}"] button[data-automation-id="add-to-cart-btn"]` in FBT section
- FBT fallbacks `.frequently-bought-together button[...]` and class-based XPaths REMOVED 2026-04-08 (CSS classes; Walmart hashes them on every deploy)

**ATC confirmation signals (flyout/modal after click):**
- `[data-automation-id="cart-flyout"]`
- `[data-automation-id="atc-flyout"]`
- `button:has-text("View cart")`
- `a:has-text("View cart")`
- `button:has-text("Go to cart")`
- `button:has-text("Added to cart")`
- `button:has-text("Added")`

**Cart page — items present:**
- `[data-automation-id="cart-item"]` ← primary
- `[data-testid="cart-item"]` ← fallback
- `.cart-item` ← REMOVED 2026-04-08 (CSS class; Walmart hashes class names on every deploy)
- Empty cart signal: body text contains "your cart is empty"

**Cart page — remove item buttons:**
- `button[data-automation-id="remove-item"]`
- `button[aria-label*="Remove"]`
- XPath: `//button[contains(., "Remove")]`

**Proceed to Checkout button (cart page — priority order):**
- `button[data-automation-id="checkout-btn"]`
- `a[data-automation-id="checkout-btn"]`
- `button:has-text("Checkout")`
- `a:has-text("Checkout")`

**Checkout page load detection (after clicking Checkout):**
- `[data-automation-id="checkout-page"]`
- `[data-page-type="checkout"]`
- `form[id*="checkout"]`
- URL contains "checkout"
- Body text contains "payment", "shipping", or "order summary"

**Delivery option selector (fulfillment step):**
- `input[id*="shipping"][type="radio"]`
- `label:has-text("Delivery") input[type="radio"]`
- `label:has-text("Ship") input[type="radio"]`
- `button:has-text("Delivery")`

**Continue / address confirmation buttons (checkout multi-step loop):**
- `button:has-text("Continue")`
- `button:has-text("Deliver here")`
- `button:has-text("Use this address")`
- `button:has-text("Continue to payment")`
- `button:has-text("Review your order")`
- `button:has-text("Deliver to this address")`
- Note: loop up to 6 times; stop when Place Order button appears

**Shipping address form field IDs (if address must be entered):**
- `#firstName`
- `#lastName`
- `#addressLineOne`
- `#phone`
- `#email`
- `#city`
- `#postalCode`
- State: typically a `<select>` — use `select[name="state"]` or `select[id*="state"]`

**CVV input (payment page — priority order):**
- `input[name="cvv"]`
- `input[autocomplete="cc-csc"]`
- `input[placeholder*="CVV"]`
- `input[placeholder*="CVC"]`
- `input[aria-label*="CVV"]`
- `input[aria-label*="security code"]`

**Place Order button (review step — priority order):**
- `button[data-automation-id="place-order-btn"]`
- `button:has-text("Place order")`
- `button:has-text("Place Order")`
- `button:has-text("Submit order")`

**Order confirmation page:**
- URL pattern: `https://www.walmart.com/checkout/thankyou?version=v3/` (confirmed)
- URL regex match: `order-confirmation|order/confirm|thank-you|order-placed`
- Order number elements (priority order):
  - `[data-automation-id="order-confirmation-number"]`
  - `[data-automation-id="confirmation-order-id"]`
  - `h1:has-text("Your order is confirmed")`
  - `h1:has-text("Thank you")`
  - `span:has-text("Order #")`
- Fallback: extract 7+ digit number from URL or page text

**Frequently Bought Together (FBT) queue bypass:**
- `[data-testid="frequently-bought-together"] button[data-automation-id="add-to-cart-btn"]`
- `[data-item-id="{item_id}"] button[data-automation-id="add-to-cart-btn"]`
- XPath: `//*[@data-testid="frequently-bought-together"]//button[contains(., "Add to cart")]`
- XPath: `//*[@data-item-id="{item_id}"]//button[contains(., "Add to cart")]`

**Virtual queue detection:**
- `[data-automation-id*="queue-entry"]`
- `[data-automation-id*="hold-spot"]`
- Post-queue ATC: `button[data-automation-id="add-to-cart-btn"]`

---

### URL Structure
| Step | URL |
|------|-----|
| Product page | `https://www.walmart.com/ip/{slug}/{item_id}` |
| Cart | `https://www.walmart.com/cart` |
| Checkout entry | `https://www.walmart.com/checkout` |
| Checkout sign-in gate | `https://www.walmart.com/checkout/#/sign-in` |
| Confirmation (v3) | `https://www.walmart.com/checkout/thankyou?version=v3/` |

Note: Walmart checkout uses hash-based SPA routing. The hash fragment changes per step but the URL root stays at `/checkout`. The codebase detects checkout page load by inspecting body text and element presence rather than relying on hash fragment values, which is correct — hash fragments are not stable across A/B tests.

---

### Step-by-Step Flow
1. Navigate to product page: `https://www.walmart.com/ip/{slug}/{item_id}`
2. Warm session: browser should have browsed product pages (Akamai behavioral profile)
3. Check for virtual queue — if detected, call `QueueHandler.wait_for_passthrough()`
4. Try FBT add-to-cart bypass (bypasses virtual queue validation) — if item confirmed in cart, skip to step 8
5. Clear cart if items already present (`button[data-automation-id="remove-item"]`)
6. Click ATC button (`button[data-automation-id="add-to-cart-btn"]`)
7. Wait for ATC flyout/confirmation signal
8. Navigate to cart (`https://www.walmart.com/cart`), verify `[data-automation-id="cart-item"]` present
9. Click Checkout button (`button[data-automation-id="checkout-btn"]`)
10. Solve `/blocked` PerimeterX press-and-hold if intercepted
11. Multi-step checkout loop (max 6 iterations):
    a. Check if Place Order button visible → if yes, done with loop
    b. Select Delivery if fulfillment choice shown
    c. Click Continue/Deliver here/Use this address button
12. Enter CVV (`input[name="cvv"]` or `input[autocomplete="cc-csc"]`)
13. Click Place Order (`button[data-automation-id="place-order-btn"]`)
14. Wait for URL to match `order-confirmation|thank-you|order-placed` regex (20s timeout)
15. Extract order ID from `[data-automation-id="order-confirmation-number"]` or URL digits

---

### Known Fragile Points
- **`_px3` TTL ~60s** — PerimeterX clearance expires fast; must complete checkout within this window or re-solve challenge
- **Press-and-hold CAPTCHA** — appears unpredictably; CDP `dispatchMouseEvent` sequence: mousedown → hold 2-3s → mouseup
- **CSS class selectors break on every deploy** — self-healing agent (`walmart/self_healing_agent.py`) auto-patches, but use stable `data-automation-id` / `data-testid` selectors first
- **Direct `/cart` or `/checkout` navigation** — triggers immediate Akamai challenge; must flow from product page
- **Cart-to-checkout transition** — must be > ~1.5s or Akamai scores it as bot behavior
- **`GRAPHQL_HASH` staleness** — BTF hash changes on every Walmart deploy; 400 response on stock check = hash stale; see ANTIBOT.md
- **Seller ID check required** — only buy from `F55CDC31AB754BB68FE0851F0F1F2C96` (Walmart.com direct); third-party sellers will fail checkout
- **ATC button `data-tl-id`** — the `data-tl-id="ProductPrimaryCTA-cta_add_to_cart_button"` attribute is a tracking/analytics ID that may be more stable than `data-automation-id` across A/B variants; keep it as a fallback
- **Checkout step count varies** — Walmart A/B tests the step count (2-step vs 3-step checkout); the 6-iteration loop in `_confirm_shipping()` handles both
- **`/blocked` mid-checkout** — PerimeterX can challenge mid-step-loop; `_confirm_shipping()` now checks URL for `/blocked` at every iteration and calls the challenge solver; returns early on unresolvable challenge (2026-04-08)
- **Login wall mid-checkout** — Walmart ejects to `/checkout/#/sign-in` when session cookie expires; `_confirm_shipping()` now detects `sign-in`/`login` in URL and returns early with log (2026-04-08)
- **Confirmation URL** — `walmart.com/checkout/thankyou?version=v3/` is confirmed current as of 2026-04-07; regex fallback (`order-confirmation|thank-you|order-placed`) covers legacy patterns

### Stock Check Fields (GraphQL / `__NEXT_DATA__`)
| Field | In-Stock Value | Notes |
|-------|---------------|-------|
| `availabilityStatus` | `IN_STOCK` or `PRE_ORDER_SELLABLE` | Primary signal |
| `showAtc` | `true` | Must be true — `false` = Walmart+ gated |
| `sellerId` | `F55CDC31AB754BB68FE0851F0F1F2C96` | Walmart.com direct only |

---

## Last Updated
- Target: 2026-04-08 — Full selector and flow audit by @retailer-researcher; primary source: live `src/session/purchase_executor.py` (2,947 lines), cross-referenced against Tempo Monitors ATC script docs, Manbearpixel PS5 bookmarklet gist, Refract bot docs. All selectors confirmed present in production codebase.
- Walmart: 2026-04-08 — Stale CSS class selectors removed from purchase_executor.py (`_query_selector_all`, FBT); PX captcha selector fallbacks hardened in session_manager.py; `_confirm_shipping()` gains `/blocked` + login-wall guards + randomized step delays; navigate sleeps randomized.

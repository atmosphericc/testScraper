# Walmart Purchase Flow

## Selectors
(last verified: 2026-04-07 via @retailer-researcher DOM pass)

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

**Cart page — items present (last verified: 2026-04-10):**
- `[data-automation-id="cart-item"]` ← primary
- `[data-testid="cart-item"]` ← A/B variant
- `[data-automation-id="cart-item-container"]` ← wrapper variant seen in some cohorts
- `[data-testid="cart-item-container"]` ← wrapper variant (data-testid)
- `button[data-automation-id="remove-item"]` ← proxy: only present when item exists
- `input[data-automation-id="item-qty"]` ← proxy: qty spinner only present when item exists
- `button[aria-label*="Remove"]` ← proxy: aria-label stable across deploys
- `.cart-item` ← REMOVED 2026-04-08 (CSS class; Walmart hashes class names on every deploy)
- Empty cart signal: body text contains "your cart is empty"
- JS-state probe: `window.__NEXT_DATA__.props.pageProps.initialData.data.cart.cartLines` (or `.lineItems` / `.items`) — used as secondary verification when CSS selectors fail; returns item count directly from React store

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

## URL Structure
| Step | URL |
|------|-----|
| Product page | `https://www.walmart.com/ip/{slug}/{item_id}` |
| Cart | `https://www.walmart.com/cart` |
| Checkout entry | `https://www.walmart.com/checkout` |
| Checkout sign-in gate | `https://www.walmart.com/checkout/#/sign-in` |
| Confirmation (v3) | `https://www.walmart.com/checkout/thankyou?version=v3/` |

Note: Walmart checkout uses hash-based SPA routing. The hash fragment changes per step but the URL root stays at `/checkout`. The codebase detects checkout page load by inspecting body text and element presence rather than relying on hash fragment values, which is correct — hash fragments are not stable across A/B tests.

---

## Step-by-Step Flow
1. Navigate to product page: `https://www.walmart.com/ip/{slug}/{item_id}`
2. Warm session: browser should have browsed product pages (Akamai behavioral profile)
3. Check for virtual queue — if detected, call `QueueHandler.wait_for_passthrough()`
4. Try FBT add-to-cart bypass (bypasses virtual queue validation) — if item confirmed in cart, skip to step 8
5. Clear cart if items already present (`button[data-automation-id="remove-item"]`)
6. Click ATC button (`button[data-automation-id="add-to-cart-btn"]`)
7. Wait for ATC flyout/confirmation signal
8. Navigate to cart (`https://www.walmart.com/cart`), verify item present via multi-layer check:
   - Layer 1 (8s poll): CSS selectors `[data-automation-id="cart-item"]`, `[data-testid="cart-item"]`, container variants, proxy selectors (`remove-item` btn, `item-qty` input, `aria-label*="Remove"`)
   - Layer 2 (concurrent): `window.__NEXT_DATA__` JS-state probe for `cart.cartLines`/`lineItems`/`items` count
   - Layer 3 (fallback): body text — "your cart is empty" → hard fail; presence of "checkout"/"subtotal"/"qty"/"item" → proceed
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

## Known Fragile Points
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

## Stock Check Fields (GraphQL / `__NEXT_DATA__`)
| Field | In-Stock Value | Notes |
|-------|---------------|-------|
| `availabilityStatus` | `IN_STOCK` or `PRE_ORDER_SELLABLE` | Primary signal |
| `showAtc` | `true` | Must be true — `false` = Walmart+ gated |
| `sellerId` | `F55CDC31AB754BB68FE0851F0F1F2C96` | Walmart.com direct only |

---

## Last Updated
- Walmart: 2026-04-10 — **Bug fix**: ATC 12s confirmation wait reverted to 6s/proceed-anyway — `_add_to_cart` now returns `True` after 6s flyout window regardless of confirmation signal; cart verification is the authority on ATC success. Button-enabled pre-check capped at 2s max (was 8s). Both changes restore the fast-path timing that worked in the prior confirmed run.
- Walmart: 2026-04-10 — **Bug fix**: Checkout cycling eliminated — `already_in_cart` path now calls `_verify_cart()` instead of setting `cart_ok=True` directly. `_is_item_already_in_cart()` always returns browser to product page; skipping `_verify_cart` left browser on product page when `_go_to_checkout` expected to be on cart page. Fix: always run `_verify_cart` to land on cart page before proceeding to checkout.
- Walmart: 2026-04-10 — Cart verification hardened: added `cart-item-container` variants, proxy selectors (`remove-item` btn, `item-qty` input, `aria-label*="Remove"`), concurrent `window.__NEXT_DATA__` JS-state probe, extended poll to 8s, positive body-text signals (`checkout`/`subtotal`/`qty`/`item`) added to fallback. Poll-and-proceed logic confirmed sound: `return True` on non-empty body prevents ATC retry loop.
- Walmart: 2026-04-08 — Stale CSS class selectors removed from purchase_executor.py (`_query_selector_all`, FBT); PX captcha selector fallbacks hardened in session_manager.py; `_confirm_shipping()` gains `/blocked` + login-wall guards + randomized step delays; navigate sleeps randomized.

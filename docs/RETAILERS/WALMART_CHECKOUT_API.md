# Walmart Checkout API — Hybrid Architecture Research

**Last Researched: 2026-05-11**
**Status: Pre-implementation research — no code touched.**
**Purpose: Design brief for porting the Target hybrid (API-first) checkout approach to Walmart.**
**Next agent: @purchase-flow-engineer**

---

## Executive Summary

The Target hybrid bot uses real Chrome for Shape-header warming and TLS fingerprinting, then
calls Target's REST APIs (`carts.target.com`) directly — bypassing DOM navigation for the
heaviest checkout steps and achieving 2.6s purchases. The analogous Walmart hybrid would do the
same with Walmart's checkout REST API at `www.walmart.com/api/checkout/v3/...`. However, the
feasibility comparison is unfavorable:

- **Target**: Shape Security gates the API more leniently than the DOM for sessions that already
  hold valid Shape sensor headers. Intercepting those headers from the warm browser tab is
  sufficient to call the API without a fresh browser navigation.
- **Walmart**: PerimeterX gates the checkout API aggressively. The `_px3` cookie is validated on
  every checkout API call, not just browser navigations. Unlike Target's Shape headers (which are
  static until TTL), Walmart's `_px3` is bound to the specific TLS session of the browser that
  generated it — raw Python HTTP clients cannot reuse it because their JA3 fingerprint differs.
  Every API call from outside the real browser context risks a 403/challenge response.

The primary speed win available for Walmart is therefore **within the existing browser session**:
issuing `fetch()` calls from inside the real Chrome tab (as we already do for stock checks) to
replace the slowest DOM navigation steps, rather than replacing the browser with Python HTTP. This
is a meaningfully different architecture from Target's hybrid — call it "browser-internal API
mode" vs. "Python HTTP API mode."

The research below covers all four requested areas with actionable specifics.

---

## Section 1 — Walmart's Internal REST Checkout API

### Architecture Context

Walmart's web checkout is a Next.js + React SPA. Internally, Walmart migrated to a federated
Apollo GraphQL gateway ("Orchestra") for all read paths (product data, search, personalization).
However, the **checkout write path** (ATC, contract creation, place order) is served by a
separate legacy REST API under `www.walmart.com/api/checkout/v3/...` — not GraphQL. The
Orchestra GraphQL endpoint (`/orchestra/pdp/graphql/`) is what the stock monitor already
intercepts for `ItemByIdBtf` and `ItemByIdAtf` hashes; it does not expose a `placeOrder`
mutation. Checkout mutations have not been publicly observed at any GraphQL endpoint.

**Confidence: high** — Confirmed by cross-referencing multiple open-source bot implementations
(bird-bot, PhoenixBot, wallybot) which all use the same REST path. No evidence of a GraphQL
checkout mutation endpoint in any captured network traffic or public research as of May 2026.

**Source:** GitHub analysis of
[bird-bot/sites/walmart.py](https://github.com/natewong1313/bird-bot/blob/master/sites/walmart.py),
[PhoenixBot/sites/walmart.py](https://github.com/Strip3s/PhoenixBot/blob/master/sites/walmart.py),
[wallybot/wallybot.py](https://github.com/doprdele/wallybot/blob/master/wallybot.py)

**Risk:** The REST API may be updated or deprecated without notice. The `/api/checkout/v3/`
path has been stable across bot implementations from 2020–2024, but Walmart's GraphQL migration
could eventually absorb checkout writes. Monitor for 404/410 responses on these paths.

### Step 1 — AddToCart

**Endpoint (accounts / logged-in):**
```
POST https://www.walmart.com/api/v3/cart/guest/:CID/items
```

The `:CID` is the Walmart Customer ID, available in the browser's `CID` cookie set after login.
Despite the `/guest/` path segment, this endpoint serves both guest and logged-in accounts when
the appropriate session cookies are present.

**Alternative (older mobile-API path, less stable):**
```
POST https://api.mobile.walmart.com/cart/items
```

**Payload:**
```json
{
  "offerId": "<offer_id>",
  "quantity": 1
}
```

The `offerId` is the marketplace offer identifier for the specific seller/product listing. It is
NOT the item PID (numeric item ID in the URL). The `offerId` is available from:
1. `window.__NEXT_DATA__.props.pageProps.initialData.data.product.offers[0].offerId` — most
   reliable path on modern product pages.
2. Network-intercepted from the product page's own GraphQL response on Tab 2 — our existing
   stock monitor intercept already captures this.
3. The `offerId` is also visible as the first segment in the ATC flyout URL on the product page.

**Required Headers:**
```
Content-Type: application/json
Accept: application/json
Cookie: <full Walmart cookie jar including _abck, bm_sz, _px3, auth, CID, ACID, locDataV3>
User-Agent: <real Chrome UA>
Accept-Language: en-US,en;q=0.9
wm_offer_id: <same offerId as in body>
Referer: https://www.walmart.com/ip/<slug>/<item_id>
Origin: https://www.walmart.com
```

No `wm_qos.correlation_id` or `wm_mp.seller_id` headers are required for the consumer-facing
ATC endpoint. Those headers are specific to the Seller/Marketplace B2B API (developer.walmart.com)
and are not part of the consumer checkout flow.

**Success response:** HTTP 201 with JSON body containing `{"checkoutable": true, ...}`.

**Failure modes:**
- HTTP 400 — invalid `offerId` or item not purchasable
- HTTP 401/403 — missing or invalid `_px3` / `_abck` cookies
- HTTP 409 — item already in cart
- HTTP 422 — quantity limit exceeded or item OOS

**Confidence: high** — Multiple independent bot implementations confirm this endpoint and payload
shape. The `wm_offer_id` header is a confirmed addition from PhoenixBot source.

**Risk:** The `offerId` is seller-specific. If Walmart switches to a different offer or a third-
party seller fulfills the item, the `offerId` changes. Always extract it fresh from the product
page at purchase time rather than caching it.

**Code pointer:** `walmart/purchase_executor.py` — the `_add_to_cart()` method. Also
`walmart/queue_handler.py` — `_wait_for_atc()` which currently polls DOM; could be replaced with
a POST to this endpoint from inside the browser tab via `tab.evaluate()`.

### Step 2 — Contract Creation (Cart-to-Checkout Transition)

This is the equivalent of Target's `pre_checkout` call — it moves the cart into a checkout
session and returns the PCID (Purchase Contract ID) that all subsequent checkout calls need.

**Endpoint:**
```
POST https://www.walmart.com/api/checkout/v3/contract?page=CHECKOUT_VIEW
```

**Payload:**
```json
{
  "customerId": "<CID>",
  "customerType": "REGISTERED",
  "affiliateInfo": {},
  "crt": "<cart_reference_token>"
}
```

The `crt` (cart reference token) is returned by the ATC response or available from
`/api/v3/cart/guest/:CID` (a GET call). On some implementations the `crt` field is omitted and
the server derives it from the session cookie.

**Response:** Contains `PCID` (the purchase contract ID) at the root of the JSON object, plus
fulfillment options, item list with `itemIds`, estimated total. The PCID is a UUID-like string.

**Confidence: medium** — Confirmed in multiple bot implementations but the `crt` field semantics
and whether it is required for logged-in sessions is uncertain. The logged-in flow may only need
the session cookies.

**Risk:** This is the single highest-latency call in the checkout flow (server-side cart
consolidation, inventory reservation). Typically 400–900ms. Walmart may add PerimeterX
enforcement here — see Section 4.

**Code pointer:** `walmart/purchase_executor.py` — `_cart_and_checkout()`. Currently this
navigates to `/cart` via the browser; the DOM equivalent of this step is clicking Checkout on
the cart page.

### Step 3 — Fulfillment Method Selection

**Endpoint:**
```
POST https://www.walmart.com/api/checkout/v3/contract/:PCID/fulfillment
```

**Payload:**
```json
{
  "groups": [
    {
      "fulfillmentOption": "S2H",
      "itemIds": ["<item_id_1>"],
      "shipMethod": "VALUE"
    }
  ]
}
```

`fulfillmentOption`: `"S2H"` for Ship-to-Home (delivery), `"STORE"` for pickup.
`shipMethod`: `"VALUE"` for standard shipping. Use `"EXPEDITED"` for 2-day.

**Confidence: high** — Consistent across all three bot implementations reviewed.

**Risk:** If a pickup-eligible item has no ship option, Walmart may reject `"S2H"` with a
validation error requiring a store selection. In practice, for hot-drop items (Pokemon cards,
consoles) the default is always S2H.

**Code pointer:** `walmart/purchase_executor.py` — `_select_delivery_option()` and
`_confirm_shipping()`. These currently drive the DOM through multiple Continue button clicks; this
API call replaces all of them for the fulfillment step.

### Step 4 — Shipping Address Confirmation

**Endpoint:**
```
POST https://www.walmart.com/api/checkout/v3/contract/:PCID/shipping-address
```

**Payload:** For saved-address accounts the payload is typically the saved address object echoed
back (extracted from the contract response). Structure:
```json
{
  "firstName": "...",
  "lastName": "...",
  "addressLineOne": "...",
  "city": "...",
  "state": "XX",
  "postalCode": "00000",
  "phone": "..."
}
```

**Confidence: medium** — Confirmed structurally but the exact required fields for a saved-address
account vs. entering a new address may differ. For a saved account Walmart's DOM pre-fills and
requires only a Continue click — the API equivalent may accept an empty body with the PCID to
signal "use saved address."

**Risk:** A delivery-day modal appears mid-step in DOM mode. Whether the API path also requires a
delivery-day selection (separate API call or field in the address payload) is unknown without live
capture.

**Code pointer:** `walmart/purchase_executor.py` — `_confirm_shipping()` loop, specifically the
"Deliver here"/"Use this address" Continue button clicks.

### Step 5 — Payment Submission (PIE Encryption Required)

This is the most complex step due to card tokenization. Walmart does NOT accept CVV in plaintext.

**PIE Key Retrieval:**
```
GET https://securedataweb.walmart.com/pie/v1/wmcom_us_vtg_pie/getkey.js?bust=<timestamp>
```

Response is a JS snippet that sets `PIE.L`, `PIE.E`, `PIE.K`, `PIE.key_id`, `PIE.phase`. These
are RSA public key components used to encrypt card data client-side.

**Card Data Submission Endpoint:**
```
POST https://www.walmart.com/api/checkout-customer/:CID/credit-card
```

**Payload:** Card number and CVV are encrypted with the PIE public key before transmission. The
encrypted values plus `keyId`, `phase`, `expiryMonth`, `expiryYear`, billing address, and
`isGuest: false` (for logged-in accounts) are POSTed.

**Response:** Returns a `piHash` — a server-side payment token that represents the card
authorization for this checkout session.

**Payment Binding Endpoint:**
```
POST https://www.walmart.com/api/checkout/v3/contract/:PCID/payment
```

**Payload:**
```json
{
  "payments": [
    {
      "paymentType": "CREDITCARD",
      "preferenceId": "<saved_card_preference_id>",
      "cvv": "<PIE_encrypted_CVV>",
      "piHash": "<from_credit_card_response>",
      "cardType": "VISA",
      "firstName": "...",
      "lastName": "...",
      "addressLineOne": "...",
      "city": "...",
      "state": "XX",
      "postalCode": "...",
      "expiryMonth": "XX",
      "expiryYear": "XXXX",
      "phone": "..."
    }
  ]
}
```

For saved-card accounts the `preferenceId` identifies the saved payment method and most billing
fields may be optional (server fills from saved profile). The CVV still requires PIE encryption
even for saved cards.

**Confidence: medium** — The PIE encryption requirement is confirmed; the exact payload shape
for saved-card accounts is inferred from pattern matching across implementations and may differ
from guest checkout payloads.

**Risk:** This is the highest-scrutiny API call. PerimeterX has a dedicated payment-field sensor
module. Submitting CVV via API from outside the real browser JA3 context is the riskiest step in
the hybrid approach. If PerimeterX blocks here specifically, the PIE flow is the first thing to
revert to DOM.

Additionally: PIE key rotation. The `getkey.js` endpoint is fetched fresh per transaction but
the PIE keys themselves rotate periodically. A key that was valid for one transaction may be
rejected by the payment endpoint if Walmart rotates keys mid-session.

**Code pointer:** `walmart/purchase_executor.py` — CVV entry in `_confirm_shipping()`. The
current implementation types CVV character-by-character via CDP `dispatchKeyEvent`. The API
alternative would need to implement PIE encryption in Python (RSA with `PIE.E` and `PIE.K`
parameters — standard RSA PKCS#1 v1.5 encryption, implementable with `cryptography` library).

### Step 6 — Place Order

**Endpoint:**
```
PUT https://www.walmart.com/api/checkout/v3/contract/:PCID/order
```

**Payload:** Empty JSON object `{}` — the PCID carries all order state from prior steps.

**Method:** `PUT` (not POST).

**Response:** JSON containing order confirmation details including order ID. The order ID is a
numeric string typically 13–16 digits.

**Required Headers:**
```
Content-Type: application/json
Accept: application/json
Cookie: <full cookie jar including fresh _px3, _abck, auth, CID>
wm_cvv_in_session: true
wm_vertical_id: 0
Referer: https://www.walmart.com/checkout
Origin: https://www.walmart.com
```

The `wm_cvv_in_session: true` header signals to the server that CVV was provided in the current
session (via the payment step). Without it, the order may be rejected.

**Confidence: medium-high** — The endpoint, method, and empty-body pattern are confirmed across
multiple implementations. The exact header set for a 2024–2026 session may have evolved.

**Risk:** This is the single most PerimeterX-scrutinized call. A stale or invalid `_px3` at this
point causes a 403 redirect to `/blocked`. The cookie must have been refreshed within the last
~50s by the real browser session. See Section 4 for the enforcement model.

**Code pointer:** `walmart/purchase_executor.py` — Place Order button click logic in
`_confirm_shipping()` and `_go_to_checkout()`. The `PLACE_ORDER_SELECTORS` array. The
confirmation URL capture.

### Anti-CSRF / Correlation Headers

The consumer-facing Walmart checkout API does NOT use:
- `wm_sec.key_id` / `wm_sec.auth_signature` — these are exclusively for the B2B Marketplace API
  (seller-facing), not consumer checkout
- `wm_consumer.id` — same, B2B Marketplace only
- `traceparent` — no public evidence this is required on the checkout path

The only required non-standard headers are `wm_offer_id` (ATC step), `wm_cvv_in_session`, and
`wm_vertical_id` (payment/place-order steps). The consumer API relies on session cookies for
auth, not signed headers.

**Confidence: high** — B2B headers are not present in any consumer-facing bot implementation
and are documented only in developer.walmart.com's Marketplace API docs.

### Complete Checkout Sequence Summary

| Step | Endpoint | Method | Key Output |
|------|----------|--------|-----------|
| ATC | `/api/v3/cart/guest/:CID/items` | POST | cart populated |
| Contract | `/api/checkout/v3/contract` | POST | PCID |
| Fulfillment | `/api/checkout/v3/contract/:PCID/fulfillment` | POST | fulfillment confirmed |
| Address | `/api/checkout/v3/contract/:PCID/shipping-address` | POST | address confirmed |
| PIE keys | `securedataweb.walmart.com/pie/v1/.../getkey.js` | GET | PIE.K, PIE.key_id |
| Card submission | `/api/checkout-customer/:CID/credit-card` | POST | piHash |
| Payment binding | `/api/checkout/v3/contract/:PCID/payment` | POST | payment confirmed |
| Place order | `/api/checkout/v3/contract/:PCID/order` | PUT | orderId |

**Theoretical API-only checkout latency:** 600ms (ATC) + 700ms (contract) + 200ms (fulfillment)
+ 200ms (address) + 150ms (PIE) + 400ms (card) + 300ms (payment) + 500ms (order) = ~3.0s
before PerimeterX validation overhead.

---

## Section 2 — Max-Quantity Per-Product Field

### Primary Field

**JSONPath:** `props.pageProps.initialData.data.product.orderLimit`

This is the authoritative per-customer purchase ceiling exposed by Walmart's product page
`__NEXT_DATA__` blob. Confirmed by multiple scraping guides that explicitly extract this field
from live product pages as of 2024–2026.

**Companion field:** `props.pageProps.initialData.data.product.orderMinLimit` — minimum purchase
quantity, typically 1.

**Confidence: high** — Multiple independent sources confirm `orderLimit` as the correct field
name; the Scrapfly Walmart scraping guide (2026) shows explicit code extracting this value.

**Source:** [scrapfly.io/blog/posts/how-to-scrape-walmartcom](https://scrapfly.io/blog/posts/how-to-scrape-walmartcom)

### OfferId Location (for ATC)

The `offerId` required for ATC is located at:
```
props.pageProps.initialData.data.product.offers[0].offerId
```

For search results pages (different structure):
```
props.pageProps.initialData.searchResult.itemStacks[0].items[n].offerId
```

**Confidence: medium** — The search results path is directly confirmed. The product page path is
inferred from the schema pattern; the exact sub-key may vary by product category (some products
use a different offers array name). Cross-reference against what the CDP network intercept
returns in `_pending_graphql` for the same item ID to validate.

### Typical Value Ranges

- **Hot-drop collectibles (Pokemon, trading cards):** `orderLimit: 1` or `orderLimit: 2` at
  drop time. Walmart frequently updates this in real-time as stock depletes.
- **Consumer electronics (consoles, GPUs):** `orderLimit: 1` on limited-allocation drops.
- **Commodity goods:** `orderLimit: 10–20` or absent (no enforced limit).
- **Walmart+ early-access items:** `orderLimit: 1` enforced at account level as well as page
  level.

### ATP (Available-to-Promise) Field

No separate ATP/inventory-quantity field has been found in the public `__NEXT_DATA__` structure.
Walmart does not expose available inventory count to the client — only `availabilityStatus`
(`"IN_STOCK"`, `"OUT_OF_STOCK"`, `"IN_STORE_ONLY"`, etc.). The `orderLimit` is therefore the
only client-side quantity ceiling available.

**Current codebase status:** `walmart/config.py` and `walmart/stock_monitor.py` do not currently
parse `orderLimit`. The quantity submitted in ATC is hardcoded to 1 in the current flow (DOM
click). For the hybrid API path, parse `orderLimit` from `__NEXT_DATA__` on Tab 2 at product
page load time, cap to `min(orderLimit, 1)` for maximum compatibility (Walmart frequently rejects
quantities > 1 on hot items even when `orderLimit` says higher).

**Code pointer:** `walmart/purchase_executor.py` — `_navigate()` and `_wait_for_page_ready()`
already parse `__NEXT_DATA__` for ATC button presence; extend this to also extract `orderLimit`
and `offers[0].offerId` for use in the API call.

---

## Section 3 — Virtual Queue Mechanics

### Queue Provider

Walmart uses **Queue-it** (`queue-it.net`) as its virtual waiting-room provider. This is
confirmed for high-demand drops including Pokemon TCG releases (Prismatic Evolutions drop,
May 2025), console restocks, and GPU allocations.

**Confidence: high** — Confirmed by the poke-alerts.com Walmart Queue Decoder tool which
explicitly monitors `queue-it.net` traffic on walmart.com, the ivanr101/walmart-queue-checker
Chrome extension (decodes Queue-it ticket data from the URL), and multiple community reports.

**Source:**
- [poke-alerts.com/walmart-queue](https://poke-alerts.com/walmart-queue)
- [github.com/ivanr101/walmart-queue-checker](https://github.com/ivanr101/walmart-queue-checker)
- [thekelpeeshow.com — Bots Strike Again](https://thekelpeeshow.com/bots-strike-again-how-queue-bypasses-ruined-walmarts-prismatic-evolutions-pokemon-drop/)

### Queue URL Pattern

When Walmart activates a queue for a product drop, accessing the product page redirects to a
Queue-it waiting room URL. Pattern (based on Queue-it's standard implementation):

```
https://walmart.queue-it.net/...?c=walmart&e=<event_id>&t=<target_url>&...
```

After queue passthrough, Queue-it redirects back to the original Walmart product URL with a
`queueittoken` query parameter appended:
```
https://www.walmart.com/ip/<slug>/<item_id>?queueittoken=<token>
```

Walmart's server exchanges this token for the `QueueITAccepted-SDFrts345E-V3_<event_id>` cookie,
which grants access to the product page for the current session.

### Queue-it Cookie Flow

1. User navigates to product page → Walmart server detects queue is active → 302 redirect to
   Queue-it waiting room (`walmart.queue-it.net`)
2. Queue-it sets `Queue-it` cookie (persistent, ~1 year, domain: `queue-it.net`) — visitor
   identity
3. Queue-it sets `Queue-it-<event_id>` cookie — event-specific state
4. When position is reached: redirect to `afterevent.aspx` → generates `queueittoken` param
5. User lands back on Walmart with `?queueittoken=...` in URL
6. Walmart server validates token → sets `QueueITAccepted-SDFrts345E-V3_<event_id>` cookie on
   `walmart.com` domain (HttpOnly, short-lived — typically 1–5 minutes)
7. Product page now accessible; ATC button becomes active

### Queue State Detection (DOM Signals)

The queue waiting room page shows these text signals (decoded from Queue-it tickets):
- `"This deal is selling fast"` — user has good admission likelihood (Queue-it: `likely`)
- `"This deal is almost gone"` — user has poor admission likelihood (Queue-it: `unlikely`)
- Numeric queue position is embedded in the Queue-it ticket, not shown directly — requires
  ticket decode

The ATC button on the product page is the canonical passthrough signal: when the queue releases
the user, the product page loads normally and the ATC button appears in the DOM. Our existing
`queue_handler.py` `_wait_for_atc()` is the correct detection mechanism.

### Queue `validateTickets` API

The Chrome extension `alxmyth/walmart-queue-monitor` intercepts a `validateTickets` API endpoint
on `walmart.com`. This endpoint is called by Walmart's page JS to check queue admission status.
The extension monkey-patches `fetch()` and `XMLHttpRequest` to capture the response. The response
includes: ticket status (`pending`, `valid`, `expired`), ticket number, admission likelihood
percentage, estimated turn time.

**Current queue_handler.py status:** The handler does not intercept `validateTickets` — it polls
the DOM for ATC button presence. This is sufficient for passthrough detection but misses the
ability to predict passthrough time. Intercepting `validateTickets` via CDP `RequestWillBeSent`
could allow earlier preparation (warm `_px3`, pre-navigate) before passthrough is granted.

### FBT (Frequently Bought Together) Queue Bypass

Walmart's product page loads a "Frequently Bought Together" (FBT) module independently from the
main buybox React island. On many drops the FBT module loads and becomes ATC-able before the
queue kicks in for the main buybox. This is the only reliable documented queue bypass.

**Confirmation:** A restock tracker (Ricanking, May 2025) confirmed FBT bypass worked for the
Prismatic Evolutions Pokemon SPC drop: `"Adding to cart under 'frequently bought together' — No
other way to beat the queue"`. Multiple community reports confirm 30K+ successful checkouts via
Refract bot on this drop using this method.

**Source:** [x.com/ricanking6/status/1922850592621314358](https://x.com/ricanking6/status/1922850592621314358)

**Current codebase status:** `walmart/purchase_executor.py` has a `_try_fbt_add_to_cart()`
method. Based on the community evidence, this is a high-value path and should be the PRIMARY ATC
attempt on any queue-active drop, before falling through to the main buybox ATC path.

### Bypass via Direct API

On some older drops, the checkout REST API did not enforce queue state — sending an ATC POST
directly bypassed the queue. As of 2024–2025 this no longer works reliably. Walmart's ATC
endpoint now validates queue admission server-side for queue-activated items. The `QueueITAccepted`
cookie must be present in the ATC request cookie jar for queue-protected items.

**Confidence: medium** — Community reports indicate both that direct API bypass worked historically
and that it was patched. Current status is uncertain; worth testing on a live queue drop.

### Cookie Requirements for Post-Queue Checkout

After queue passthrough, the `QueueITAccepted-SDFrts345E-V3_<event_id>` cookie must be present
in all subsequent checkout calls (it is part of the browser's cookie jar automatically if using
the real browser). For a Python HTTP hybrid approach, this cookie must be explicitly forwarded.

**Code pointer:** `walmart/queue_handler.py` — `wait_for_passthrough()`. The cookie snapshot
taken by `session_manager.snapshot_monitor_cookies()` should be extended to include the
`QueueITAccepted` cookie when it is present.

---

## Section 4 — PerimeterX / Akamai on the API Path

### Which Endpoints Require Fresh `_px3`

PerimeterX enforcement on Walmart's checkout endpoints is tiered by business risk:

| Endpoint | PX3 Enforcement | Notes |
|----------|----------------|-------|
| ATC (`/api/v3/cart/.../items`) | Moderate | Enforced but `_px3` from a warm session typically suffices. Older `_px3` (30–50s) may pass if behavioral score is high. |
| Contract creation (`/api/checkout/v3/contract`) | Moderate-High | PerimeterX escalates enforcement at cart→checkout transition. `_px3` must be < 50s old. |
| Payment (`/api/checkout-customer/:CID/credit-card`) | High | Payment field sensor fires here. `_px3` must be fresh (< 30s). PIE encryption is the PCI safeguard; PX is the bot safeguard. |
| Place Order (`/api/checkout/v3/.../order`) | Highest | This is PerimeterX's highest-priority enforcement point. `_px3` must be < 30s old. Any anomaly (TLS mismatch, cookie origin mismatch, behavioral score spike) blocks here. |

**Confidence: medium** — Tiering inferred from community reports, the codebase's `PX3_MAX_AGE_SECONDS = 50` value (which was validated against real purchase flows), and PerimeterX's documented "Hype Sale" mode for checkout-critical pages. Exact TTL thresholds at each step are not publicly confirmed.

### How PerimeterX Challenges Appear on the API Path

Unlike browser navigation (which redirects to `/blocked`), API calls from within the browser tab
that fail PerimeterX validation receive:
- HTTP 403 with a JSON body: `{"errorCode": "SITE_ACCESS_DENIED", ...}` — when `_px3` is
  expired or the TLS session changed mid-checkout
- HTTP 200 with a challenge body (rare on API path) — when PerimeterX wants a JS-solvable
  challenge inline

For pure Python HTTP clients (outside the browser), PerimeterX detects the JA3 mismatch before
the request even reaches the application layer and returns:
- HTTP 403 "Access Denied" or a redirect to `/blocked` — depending on whether the edge enforcer
  intercepts first (Akamai 403) or the application enforcer (PX redirect)

**Key finding for hybrid architecture:** The Target hybrid works because Shape Security gates the
API via the Shape sensor headers, which are JS-generated and can be captured from the real browser
and replayed in Python HTTP. PerimeterX uses TLS-session-bound validation — the `_px3` cookie is
cryptographically tied to the JA3/JA4 fingerprint of the browser session that generated it.
Replaying `_px3` from a Python `requests` or `httpx` call fails because the TLS fingerprint
differs. **There is no equivalent of "capturing Shape headers" for PerimeterX.**

**Implication:** The viable hybrid mode for Walmart is `tab.evaluate(fetch(...))` — issuing the
checkout API call from inside the real Chrome tab, which preserves the correct TLS session and
the correct `_px3` origin. Python HTTP is not viable as the checkout caller.

### Sensor Data POST

PerimeterX's JS sensor fires before sensitive navigations. For the checkout path, the sensor POST
goes to:
```
https://collector-<customer_hash>.px-cloud.net/api/v2/collector
```

This is fired automatically by the PerimeterX JS SDK loaded on Walmart's pages. When navigating
within the real browser, this fires naturally. For `tab.evaluate(fetch(...))` API calls that skip
a full page navigation, the sensor may not fire. This gap may cause `_px3` to not refresh
between API calls.

**Mitigation:** Between API calls, trigger a micro-navigation or use `tab.evaluate()` to manually
execute Walmart's PX sensor refresh call. The exact sensor trigger mechanism requires a live
capture session to characterize. Alternatively: keep `_confirm_shipping()` DOM-based for the
steps that refresh `_px3` (address and payment steps) and only use the API path for Place Order.

**Confidence: medium** — The sensor-POST gap is a known issue with API hybridization against
PerimeterX. The mitigation approach is standard but the specifics for Walmart have not been
validated live.

### Known Public Bot Stack Approaches

Based on research across commercial bot documentation (Refract, Hidden AIO) and open-source
implementations:

1. **Full browser (current approach):** Best detection evasion, slowest. This is what our bot
   currently does. Refract's standard flow also uses a full browser approach.

2. **Browser + `tab.evaluate(fetch())` for API calls (recommended hybrid):** The browser handles
   all fingerprinting and cookie management; Python orchestrates the flow via CDP; the actual
   checkout API calls go through `tab.evaluate()`. This preserves JA3 consistency. Speed gain:
   ~3–5s on the DOM navigation steps (cart page load, checkout page load, Continue button clicks).

3. **Pure API (Python HTTP):** Not viable against modern Walmart/PerimeterX. PhoenixBot and
   bird-bot (which used Python HTTP) were effective in 2020–2021 when PerimeterX enforcement was
   lighter. As of 2023+, these approaches are blocked on the checkout path. The
   `wallybot` implementation uses a mobile-app UA spoofing approach to bypass PX; this also no
   longer works reliably (mobile API endpoints have their own HUMAN Security enforcement).

4. **Offer ID + Skip Monitoring (Refract's "OID" mode):** For non-queue drops, Refract uses the
   `offerId` to directly POST ATC without monitoring, bypassing the stock-check cycle. This is
   a legitimate speed optimization that works when the OID is sourced externally (cookgroup).
   Our bot does not currently implement this path.

### PerimeterX Challenge on GraphQL Endpoint

The Orchestra GraphQL endpoint (`/orchestra/pdp/graphql/ItemByIdBtf/...`) used for stock checks
does NOT require `_px3` — it is served from a different application context (product data API,
not checkout API) and relies only on Akamai `_abck` for bot detection. Akamai enforcement on
the stock endpoint is what produces the BLOCKED responses the stock monitor sees. This is
separate from PerimeterX enforcement on the checkout path.

**Confidence: high** — Confirmed by the existing codebase's dual-system model (`_abck` for
stock checks, `_px3` for checkout) and supported by the ANTIBOT.md debugging rule:
"403 'Pardon Our Interruption' = Akamai problem. `/blocked` = PerimeterX problem."

---

## Section 5 — Recommended Hybrid Architecture for Walmart

Based on this research, the recommended hybrid approach for Walmart differs significantly from
Target's approach:

### Target Model (Python HTTP after header capture)
```
Browser (Chrome) → warm Shape headers → capture headers via CDP → Python HTTP issues API calls
```

### Walmart Viable Model (browser-internal fetch)
```
Browser (Chrome) → all requests issued via tab.evaluate(fetch(...)) → Python orchestrates flow
```

### Proposed Call Sequence

```
[Python] Signal stock-in
[Browser/Tab2] Navigate to product page (already warmed — existing approach)
[tab.evaluate] Extract offerId from window.__NEXT_DATA__ or CDP intercept
[tab.evaluate] POST /api/v3/cart/.../items  ← replaces DOM ATC click
[tab.evaluate] Wait 300–600ms (natural user pause)
[tab.evaluate] POST /api/checkout/v3/contract  ← replaces cart page nav + Checkout button click
[tab.evaluate] POST /api/checkout/v3/contract/:PCID/fulfillment  ← replaces fulfillment step DOM
[tab.evaluate] POST /api/checkout/v3/contract/:PCID/shipping-address  ← replaces address step
[tab.evaluate] GET securedataweb.walmart.com/.../getkey.js  ← PIE key retrieval
[tab.evaluate] POST /api/checkout-customer/:CID/credit-card  ← PIE-encrypted CVV
[tab.evaluate] POST /api/checkout/v3/contract/:PCID/payment  ← bind payment
[tab.evaluate] PUT /api/checkout/v3/contract/:PCID/order  ← Place Order
[Python] Extract orderId from response
```

**Estimated speed:** 2.5–4s end-to-end, assuming each API call takes 200–700ms and PerimeterX
does not challenge. This is competitive with or faster than the Target hybrid's 2.6s.

**Key unknowns requiring live capture before implementation:**
1. Whether `_px3` refreshes between `tab.evaluate()` fetch calls (sensor POST behavior)
2. Exact PCID extraction path from contract response
3. PIE encryption library implementation (Python RSA using `PIE.K`/`PIE.E` params)
4. Whether saved-address/saved-card accounts skip the address and card submission steps
   (likely: the contract response would show the saved address and the payment binding would use
   the `preferenceId` directly without the PIE call)
5. `QueueITAccepted` cookie requirement on queue drops for the ATC endpoint

### Speed Improvement Estimate vs. Current DOM Flow

Current DOM flow (observed 12–18s):
- Navigate to product page: 2–4s (already warm on Tab 2: ~0.5s)
- ATC click + flyout: 0.5–2s
- Cart page navigation: 1–3s
- Checkout button + checkout page load: 2–4s
- Continue button clicks (2–3×): 3–6s
- CVV entry: 0.5–1s
- Place Order click: 0.5–1s

Hybrid API flow (estimated 2.5–4s):
- Product page already warm: 0s
- API calls (8×, sequential): 2–4s total
- No page navigations, no DOM polling, no modal handling

Savings: 8–14s. This exceeds the Target improvement (which was ~5–7s saved).

---

## Discovery Methodology Notes

The following methodology is recommended for the live capture session needed before implementation:

1. **Chrome DevTools capture:** Open Walmart, start a checkout on a cheap item (e.g., a $0.50
   candy bar or a digital download), open Network tab filtered to `XHR` + `Fetch`. Capture every
   request from ATC through order confirmation. Note: filter to domain `walmart.com` (not
   `queue-it.net`, `px-cloud.net`, `akamaiedge.net`).

2. **CDP-level capture in our bot:** Add a temporary `_on_network_request_checkout` CDP handler
   (similar to the existing GraphQL handler) that logs ALL request URLs and response status codes
   from Tab 2 during a real purchase. This gives us exact endpoint URLs, header sets, and
   response shapes from our actual session context — more reliable than third-party sources.

3. **Extract offerId:** After a successful DOM-mode purchase, log `window.__NEXT_DATA__` from Tab
   2 and validate the `orderLimit` and `offers[0].offerId` paths.

4. **Contract response capture:** Log the full JSON response from the contract creation step to
   identify the PCID field name and understand the saved-address/card flow for logged-in accounts.

---

## Open Questions for @purchase-flow-engineer

1. **PIE encryption in Python:** Can we implement RSA encryption using the PIE key params from
   `getkey.js` inside a `tab.evaluate()` call (JS, avoids Python crypto dependency) or do we
   prefer a Python implementation using the `cryptography` library? The former avoids cross-
   language encoding issues; the latter is more auditable.

2. **Saved-card shortcut:** For logged-in accounts with a saved card, does the contract response
   include the card's `preferenceId`? If so, steps 5 and 6 (PIE key + card submission) may be
   skippable — just POST payment with `preferenceId` + encrypted CVV only, which is simpler.

3. **Queue-activated drops:** On queue drops, should the hybrid flow fall back to full DOM after
   passthrough (safer) or continue with API calls (faster but untested with `QueueITAccepted`
   cookie in the jar)?

4. **FBT bypass promotion:** Given the May 2025 Prismatic Evolutions evidence, should
   `_try_fbt_add_to_cart()` become the primary ATC path on all drops (not just a fallback)?

5. **`_px3` refresh between fetch calls:** The riskiest unknown. Recommend testing with a
   `TEST_MODE` run that issues all API calls via `tab.evaluate()` while logging `_px3` age
   before and after each call.

# Walmart Profile
## Last Researched: 2026-04-07

---

## Checkout Flow

### Page Sequence (Ship-to-Home, Logged-In Account)

```
1. Product Page          https://www.walmart.com/ip/<slug>/<item_id>
2. Cart                  https://www.walmart.com/cart
3. Checkout (multi-step) https://www.walmart.com/checkout
   3a. Fulfillment step  — shipping vs. pickup selection (may be skipped for pure ship items)
   3b. Address step      — confirm or select shipping address
   3c. Payment step      — confirm saved card + enter CVV
   3d. Review step       — order summary + Place Order button
4. Order Confirmation    https://www.walmart.com/orders/<order-id>
                         OR /checkout/order-confirmation
                         OR /order-confirmation (URL varies by A/B test cohort)
```

Walmart's checkout is a **React SPA**. The URL stays at `/checkout` throughout steps 3a–3d; only the in-page "step" component changes. The URL does not reflect which sub-step you are on until you reach the confirmation page. Rely on DOM presence of step-specific elements, not URL, to know where you are.

### Add to Cart

- Primary CTA: `button[data-automation-id="add-to-cart-btn"]`
- Legacy/variant: `button[data-tl-id="ProductPrimaryCTA-cta_add_to_cart_button"]`
- Text-based fallback: `button:has-text("Add to cart")`, `button:has-text("Add to Cart")`
- Pre-order variant: `button:has-text("Pre-order")`, `button:has-text("Preorder")`
- Add-to-cart confirmation flyout: `[data-automation-id="cart-flyout"]`, `[data-automation-id="atc-flyout"]`
- Stock availability signal (buy box present): `button[data-dca-name="ItemBuyBoxAddToCartButton"]`

After a successful ATC click, Walmart shows one of:
- A drawer/flyout with "View cart" / "Go to cart" buttons
- The ATC button text changes to "Added" or a checkmark
- Silently succeeds with no visual indicator (common on high-traffic drops)

### Cart Page

- Cart URL: `https://www.walmart.com/cart`
- Cart item container: `[data-automation-id="cart-item"]`, `[data-testid="cart-item"]`, `.cart-item`
- Checkout button (on cart page): `button[data-automation-id="checkout-btn"]`, `a[data-automation-id="checkout-btn"]`
- Empty cart detection: check `document.body.innerText` for "your cart is empty"
- Product tiles (category/search pages): `[data-dca-name='ui_product_tile:vertical_index']`

### Checkout Sub-Steps (Step 3)

All sub-steps live at `/checkout`. Continue/advance buttons:

```
button:has-text("Continue")
button:has-text("Deliver here")
button:has-text("Use this address")
button:has-text("Continue to payment")
button:has-text("Review your order")
button:has-text("Deliver to this address")
```

Checkout page load signal (use any as confirmation):
- `[data-automation-id="checkout-page"]`
- `[data-page-type="checkout"]`
- `form[id*="checkout"]`
- Body text contains "payment", "shipping", or "order summary"

Address fields (when address entry is required — rare for saved accounts):
- Street: `input[name="addressLineOne"]`, `input[autocomplete="address-line1"]`
- City: `input[name="city"]`, `input[autocomplete="address-level2"]`
- State: `select[name="state"]`, `select[autocomplete="address-level1"]`
- ZIP: `input[name="postalCode"]`, `input[autocomplete="postal-code"]`

Payment / CVV:
- CVV input: `input[name="cvv"]`, `input[autocomplete="cc-csc"]`, `input[placeholder*="CVV"]`, `input[aria-label*="security code"]`
- CVV is nearly always required even with saved cards on Walmart

### Place Order Button

```
button[data-automation-id="place-order-btn"]
button:has-text("Place order")
button:has-text("Place Order")
button:has-text("Submit order")
```

### Confirmation Page

- Confirmation URL patterns (regex): `order-confirmation|order/confirm|thank-you|order-placed`
- Order number selector: `[data-automation-id="order-confirmation-number"]`, `[data-automation-id="confirmation-order-id"]`
- Text fallbacks: `h1:has-text("Your order is confirmed")`, `span:has-text("Order #")`
- Order ID is numeric, typically 13–16 digits. Extract with `\d{6,}` from element text or URL.
- Order detail URL: `https://www.walmart.com/orders/<order_id>`

### Product Page Key Selectors

```
Name:     h1[itemprop="name"]  OR  h1#main-title
Brand:    a[data-seo-id="brand-name"]
Price:    span[data-seo-id="hero-price"]
Image:    img[data-seo-id="hero-image"]
Discount: div[data-testid="dollar-saving"]
```

### Guest Checkout

Guest checkout is **available** but less reliable for automation:
- Requires entering email, address, and full payment info fresh each transaction
- No CVV prefill; full card number required
- Higher bot-score due to no account session history
- Walmart's checkout page asks for email + address together in one step (no separate email-first gate)
- **Recommendation: use account login flow.** Saved address + saved card with CVV-only entry is faster and scores lower on behavioral analysis.

### Walmart+ Account Differences

- Walmart+ members may get early access to limited-release product drops (items listed as "Walmart+ members only" before the general drop)
- Walmart+ early access drops: product page shows an "Early Access" banner; ATC button is locked until the early access window opens (typically 1 hour before public)
- The bot must be logged into a Walmart+ account to access these; a non-member session will see a prompt to join Walmart+
- `config.py` notes that Walmart+ exclusive items can trigger automatic membership purchase if the "auto-buy Walmart+" flag is enabled — treat this as a risk in production

### Interstitial Pages / Prompts

1. **Virtual Queue** (`/blocked` or queue iframe): Appears on high-demand drops. The page URL changes to something like `/checkout?type=queue` or a separate queue domain. See `queue_handler.py` for detection/handling.
2. **Fulfillment Selector**: When an item supports both Ship and Pickup, a modal or step asks you to choose. Selectors: `label:has-text("Delivery")`, `button:has-text("Delivery")`. Always click Delivery/Ship first.
3. **Substitution Prompt**: Appears during grocery/pickup checkout when an item is OOS. Asks if you want a substitution. Not typically relevant for electronics/limited drops, but present for grocery.
4. **Age Verification**: Appears for alcohol, certain medications. Modal with date-of-birth fields or a confirmation checkbox. Rare for the product types typically targeted.
5. **"Robot or Human?" CAPTCHA**: Appears at `/blocked` — "Activate and hold the button to confirm you're human." This is Walmart's PerimeterX (HUMAN Security) challenge, not a CAPTCHA in the traditional sense. See Anti-Bot section.
6. **Store Pickup Prompt**: If the detected user location has a Walmart store, a modal may prompt "Pick up at [store]?" for eligible items. Must dismiss or select Ship to continue.

### Fulfillment: Store Pickup vs. Ship-to-Home

- Ship-to-home flow: standard sequence above
- Store pickup flow: after ATC, checkout adds a fulfillment step; address step is replaced by store selection. Payment and review steps remain.
- Automation should always select ship-to-home (Delivery) to avoid store-specific inventory gaps and to skip the store selection step.

### Express Checkout / Walmart Pay

- Walmart Pay (in-store QR code only) does not apply to web checkout
- No "Express Checkout" button for third-party wallets on the main checkout flow as of early 2026
- Apple Pay / Google Pay are listed as supported payment methods but appear as options within the standard checkout payment step, not as an ATC-to-confirm shortcut
- "Buy Now" / direct-to-checkout buttons exist on some product pages (especially mobile); these navigate directly to `/checkout` skipping the cart entirely. Selector: `button:has-text("Buy now")`, `button[data-automation-id="buy-now-btn"]`

---

## Anti-Bot Stack

### Confirmed Vendors (confidence: high)

Walmart runs a **layered, three-vendor anti-bot stack** — one of the most aggressive in US retail:

| Layer | Vendor | Role |
|-------|--------|------|
| 1 | **Akamai Bot Manager** (v2/v3) | Edge WAF, TLS fingerprinting, JavaScript challenge, `_abck` cookie |
| 2 | **PerimeterX / HUMAN Security** | Behavioral analysis, `_px3` cookie, interactive "hold button" challenge |
| 3 | **Cloudflare** | CDN, DDoS mitigation, additional IP reputation layer |

Difficulty rating: **9/10**. This combination is rare; both Akamai and PerimeterX independently challenge the session, so a bypass of one does not guarantee passing the other.

### Akamai Bot Manager — Detection Vectors

#### 1. TLS Fingerprinting (JA3/JA4) — Highest Signal
- Each TLS client handshake produces a JA3 hash (cipher suites, TLS version, extensions, ALPN).
- Python `requests`, `httpx`, `scrapy` have well-known bot JA3 hashes and are blocked immediately.
- Chromium-based browsers (real Chrome, zendriver) produce legitimate JA3 hashes.
- **Critical:** The TLS fingerprint must match a known good browser signature. `curl_cffi` with `impersonate="chrome120"` (or similar) is needed for raw HTTP approaches.

#### 2. IP Reputation
- Datacenter IPs (AWS, GCP, Azure, DigitalOcean, etc.) are classified negatively and typically blocked at the edge.
- Residential and mobile IPs score positively.
- Walmart specifically enforces US-only proxies; non-US IPs (including Canada, Mexico) trigger 456 blocks or instant challenges.

#### 3. JavaScript / Browser Fingerprinting
Akamai's client-side script collects:
- `navigator.webdriver` — must be `undefined` (not `false`)
- `navigator.plugins` — must have 3+ real plugin objects (automation contexts often return 0)
- `navigator.mimeTypes` — must be populated
- `navigator.languages` — must be `["en-US", "en"]` or similar
- `window.chrome` — must exist with `runtime`, `loadTimes`, `csi`, `app` properties
- Canvas fingerprint (pixel rendering via WebGL/2D canvas)
- Audio context fingerprint
- Screen resolution and color depth
- Hardware concurrency (`navigator.hardwareConcurrency`)
- Device memory (`navigator.deviceMemory`)

#### 4. `_abck` Cookie and `sensor_data` Payload

The `_abck` cookie is the primary Akamai session token:
- Set on first page load via a JavaScript challenge (`/akam/11/pixel_xxxxx` or similar endpoint)
- Contains an **encrypted, base64-encoded telemetry blob** validated on every subsequent request
- Structure: two-phase encryption — (1) colon-delimited JSON payload shuffled by a PRNG seeded with a JS file hash, then (2) character substitution using a `bm_sz` cookie-derived hash
- Each `sensor_data` POST is unique per session (replay attack prevention via session-specific seeds)
- Initial sensor requests use a default cookie hash of `"8888888"`; subsequent requests derive the hash from the returned `bm_sz` cookie value
- `_abck` is validated server-side on every request; an invalid or missing cookie results in a 403 with "Pardon Our Interruption" page

**Full Akamai cookie set on Walmart:**

| Cookie | Purpose | HTTP-only |
|--------|---------|-----------|
| `_abck` | Bot Manager session token (encrypted telemetry) | No |
| `ak_bmsc` | Akamai cache/security optimization, distinguishes humans from bots | Yes |
| `bm_sz` | Used to seed `_abck` encryption in subsequent requests | No |
| `bm_sv` | Akamai cache function | No |

#### 5. HTTP Protocol / Header Analysis
- Akamai checks for HTTP/2 (modern browsers use HTTP/2; many scraping libraries default to HTTP/1.1)
- Header order matters — Chrome sends headers in a specific order; reordered headers are a bot signal
- `Origin`, `Referer`, `User-Agent`, and `Accept-Language` must be present and consistent
- Missing or incorrect `Sec-Fetch-*` headers (e.g., `Sec-Fetch-Site`, `Sec-Fetch-Mode`) are a signal

#### 6. Behavioral Analysis
- Mouse movement patterns (natural curves vs. straight lines)
- Click coordinates (real users click slightly off-center; bots often click exact center)
- Scroll behavior and timing
- Time-on-page before interaction
- Navigation sequence (Akamai expects product page → cart → checkout, not direct checkout URL)
- Request rate and inter-request timing

### PerimeterX / HUMAN Security — Detection Vectors

- Deployed on checkout-critical pages (cart, `/checkout`, payment step)
- Primary token: `_px3` cookie — clearance token with a ~60 second TTL on high-security pages
- Also uses `_pxvid`, `pxcts` cookies
- **"Hold the button" challenge**: Interactive challenge requiring a click-and-hold. Cannot be solved programmatically without a real mouse event (CDP `dispatchMouseEvent` with proper timing may work, but HUMAN's behavioral analysis checks for non-human hold patterns)
- Behavioral signals monitored: mouse acceleration, click pressure (if available), inter-event timing, whether the hold duration is within human norms

### Known Detection Triggers (Walmart-Specific)

1. Direct navigation to `/checkout` without a prior cart session — immediate block
2. Cart → checkout transition faster than ~1.5 seconds — high bot score
3. Clicking ATC button at exact center coordinates without scroll
4. Missing `Referer` header on cart page load (should be product page URL)
5. Pagination without incrementing Referer (page 2 should reference page 1)
6. Reusing `_abck` cookies across different IP addresses
7. Non-US proxy IP at any point in the session (456 block)
8. `navigator.webdriver === true` (not patched)
9. Zero browser plugins
10. Rapid-fire product page loads without human-like pauses
11. Session with no prior browsing history (cold sessions score lower)

### Bypass Approaches for zendriver / CDP

**patchright** (Playwright fork with 22 AST-level patches) is the current Walmart automation library — not zendriver. Patchright removes CDP leaks, patches `navigator.webdriver`, and disables `Runtime.enable`. It has a meaningfully better baseline against Akamai and PerimeterX than standard Selenium or Playwright:
- Avoids the `navigator.webdriver` leak inherent in WebDriver protocol
- ~67% lower detection rate than standard headless Chrome
- Still requires stealth patching and behavioral warmup for PerimeterX

**Recommended stealth stack for patchright:**
1. Run real Chrome (not bundled Chromium) with `HEADLESS=False` — non-headless reduces fingerprint distance significantly
2. Inject stealth JS via `cdp.page.add_script_to_evaluate_on_new_document()` to patch `navigator.webdriver`, `navigator.plugins`, `navigator.mimeTypes`, `window.chrome`
3. Use a persistent Chrome profile (`user_data_dir`) so the browser has real cookie history, localStorage, and cached assets
4. Residential US proxies only — no datacenter, no non-US
5. Warm the Akamai session before the drop: browse product pages, add to wishlist, navigate organically. Akamai's behavioral model updates in real time.
6. Keep `_px3` cookie fresh (< 50 seconds old) when entering checkout — re-warm if stale
7. Human-like delays between all actions (200–1200ms random)
8. Avoid direct URL navigation to `/cart` or `/checkout` when possible; prefer clicking UI elements

**What does NOT work reliably:**
- Selenium with `undetected_chromedriver` — still leaks WebDriver signals that Akamai catches
- Headless Chrome without stealth patches
- Datacenter or shared residential proxies
- Raw HTTP with `sensor_data` generation (Akamai v3 uses deployment-specific JS file hashes that change with each Walmart frontend deploy; maintaining a working generator requires constant reverse engineering)

---

## Session & Auth

### Cookie Requirements for Checkout

Minimum required cookies for a valid checkout session:

| Cookie | Required For | Notes |
|--------|-------------|-------|
| `_abck` | All pages | Must be freshly generated by Akamai JS challenge |
| `ak_bmsc` | All pages | HTTP-only; set automatically by Akamai |
| `bm_sz` | Akamai sensor validation | Used to seed `_abck` re-encryption |
| `_px3` | Cart + checkout pages | PerimeterX clearance; ~60s TTL on high-security pages |
| `_pxvid` | PerimeterX tracking | Persistent visitor ID |
| `auth` / `CID` | Account pages | Walmart account session token |
| `ACID` | Store selection/inventory | Locks session to a specific store/location |
| `locDataV3` | Store/pricing localization | |
| `locGuestData` | Guest location | |

The `ACID`, `locDataV3`, and `locGuestData` cookies control which store's inventory and pricing you see. These must be set correctly or items may appear OOS/wrong price.

### Session Expiry

- `_px3` clearance cookie: ~60 seconds TTL on high-security pages (cart, checkout). **Must re-warm session if idle > 50 seconds before checkout attempt.**
- Walmart account session (`auth` cookie): typically 30 days for "stay signed in" sessions
- `_abck`: session-scoped but Akamai validates the behavioral score continuously; a session that goes idle may need to rebuild its behavioral profile
- Idle session threshold: if the browser has been idle > 30 minutes, re-validate by navigating to the homepage before attempting a purchase

### Login Flow

```
1. Navigate to https://www.walmart.com/account/login
2. Fill: input[name="email"], input[type="email"]
3. Fill: input[name="password"], input[type="password"]
4. Click: button[type="submit"], button:has-text("Sign in")
5. Confirm redirect to /account or /
```

Two-factor authentication (SMS/email OTP) may be required on first login from a new device/profile. With a persistent Chrome profile, this is only triggered once.

### Account vs. Guest for Automation

**Account login is strongly preferred:**
- Saved address skips address entry fields entirely
- Saved card requires only CVV (3 digits vs. full 16-digit card number)
- Account session has browsing/purchase history → lower bot score
- Guest sessions start with zero behavioral history → higher initial suspicion

### Payment Info Storage

- Walmart stores card details server-side; the browser never sees the full PAN after saving
- CVV is **never** stored — always required fresh at checkout
- "Walmart Pay" is in-store only (QR code scan); not applicable to web checkout
- Express payment buttons (Apple Pay / Google Pay) are available in the payment step but require a different interaction flow — their "confirm" dialog is browser-native, not a Walmart DOM element

---

## Quirks

### React SPA Behavior

- Walmart's entire checkout flow is a client-side React SPA. Page transitions do not trigger full navigation events — `page.url` may not update when sub-steps change.
- Always poll for DOM elements rather than waiting for URL changes within `/checkout`
- DOM mutation can be slow after clicking a checkout button — allow 1.5–2 seconds before querying for the next step's elements
- GraphQL is used extensively for stock checks and checkout state. Walmart uses a persisted query hash (`data-hash` parameter) in GraphQL requests. This hash changes with each frontend deploy and must be updated periodically (see `GRAPHQL_HASH` in `config.py`).

### Dynamic CSS Classes

- Walmart's build pipeline uses CSS Modules with hashed class names (e.g., `.f7-dn4`). These change on every frontend deploy.
- **Never target elements by class name alone.** Use `data-automation-id`, `data-testid`, `data-dca-name`, `aria-label`, `name`, `type`, or `:has-text()` selectors exclusively.
- `data-automation-id` attributes are the most stable — they are explicitly maintained by Walmart's QA team and rarely change without a deliberate DOM refactor.

### A/B Testing

- Walmart runs heavy A/B testing on the checkout flow. At any time, ~10–20% of sessions may see a different:
  - ATC button text ("Add to cart" vs. "Add to Cart" vs. "Shop now")
  - Checkout page layout (single-page vs. multi-step)
  - CVV input position
- Always implement selector arrays with 3+ fallbacks. The `purchase_executor.py` pattern of iterating a list of selectors until one matches is the correct approach.

### The `/blocked` Challenge Page

- URL: `https://www.walmart.com/blocked` (or redirect to it)
- This is Walmart's PerimeterX "hold the button" challenge page
- Appears when the `_px3` cookie is missing, expired, or the bot score exceeds a threshold
- Solving requires: a mouse-down event held for a minimum ~6 seconds on a specific button, followed by a mouse-up (0.5–2s is insufficient — PerimeterX behavioral analysis rejects short holds)
- CDP `dispatchMouseEvent` with realistic timing and coordinates can solve this, but HUMAN Security's behavioral analysis checks for inhuman patterns (instant response, perfect coordinates, exact duration)
- After solving, the browser is redirected to the originally requested URL with a new `_px3` cookie

### The Virtual Queue

- Appears on high-demand product drops (limited-release electronics, etc.)
- Walmart uses a virtual queue that holds users before allowing them to add to cart
- Detected by: presence of an iframe or full-page component with text like "You're in line", "virtual queue", or a Walmart queue service domain
- The queue typically clears in seconds to minutes; the page auto-navigates when the user's position is reached
- **FBT (Frequently Bought Together) ATC bypass**: Adding the target item via the "Frequently Bought Together" module sometimes bypasses the virtual queue because the FBT module loads independently of the queue check. This is implemented in `purchase_executor.py` as `_try_fbt_add_to_cart()`.

### Rate Limiting and IP Blocking

- Walmart rate-limits at the IP level. Aggressive scraping (> ~20–30 requests/minute from one IP) triggers a soft block (429 or 403).
- Checkout-specific pages have a stricter rate limit. Multiple rapid checkout attempts from the same IP will trigger the PerimeterX challenge.
- After a 456 block (session-level block, not IP block), rotating to a new proxy is insufficient on its own — the account session must also be re-warmed.
- Proxy cooldown of 5 minutes after any 403/429 is a reasonable minimum. The `config.py` `PROXY_COOLDOWN_SECONDS = 300` value reflects this.
- Error rate threshold: bench a proxy if error rate exceeds 10% over the last 100 requests.

### Store Pickup vs. Ship-to-Home Differences

| Aspect | Ship-to-Home | Store Pickup |
|--------|-------------|--------------|
| Address step | Confirm shipping address | Select pickup store |
| Fulfillment step | May appear if item supports both | Always appears |
| Payment step | Same | Same |
| Inventory | National warehouse stock | Store-specific stock |
| OOS behavior | May show "sold out" immediately | May show store OOS even if ship available |
| Automation complexity | Lower | Higher (must handle store selection modal) |

### OOS / Substitution Prompts During Checkout

- If an item goes OOS between ATC and Place Order, Walmart shows a modal or inline error at the cart or review step
- The item is either removed from cart or marked unavailable with a "Remove" prompt
- For grocery orders: "Allow substitution" toggle per item; automation should dismiss or pre-set these
- Detection: check for `[data-automation-id="item-unavailable"]`, `[data-testid="oos-item"]`, or text containing "no longer available" or "out of stock" during cart verification

### SKU / Item ID

- Walmart item IDs (PIDs) are numeric strings, e.g., `15042474261`
- They appear in the product page URL: `walmart.com/ip/<slug>/<item_id>`
- Offer IDs (OIDs) identify a specific seller's listing for an item; different from PID. OIDs are needed for "cart spamming" and forced queue bypass techniques.
- For standard automation: PID is sufficient for ATC.

### Authorization Charges

- When adding a new payment method to an account, Walmart sends a small authorization charge (~$1) to verify the card
- Rapid addition of the same card across multiple accounts triggers bank-level fraud detection
- Reuse the same saved payment method across purchases rather than re-entering card details

### GraphQL API (for stock monitoring)

- Walmart uses persisted GraphQL queries for product data
- Endpoints: `https://www.walmart.com/orchestra/home/graphql/<operation_name>/<hash>`
- Primary hash (`ItemByIdBtf`): defined in `config.py` as `GRAPHQL_HASH`; may change on frontend deploy
- ATF hash (`ItemByIdAtf`): auto-discovered at runtime by intercepting browser network traffic
- If the GraphQL hash returns 400/404, it must be manually re-extracted by inspecting a product page network request

---

## Recommended Implementation Notes

### For zendriver Automation

**Browser Configuration**
- Use real Chrome (not bundled Chromium): `BROWSER_CHANNEL = "chrome"`
- Run non-headless: `HEADLESS = False` — headless Chrome has measurably different fingerprints
- Use a persistent profile directory: `user_data_dir=<path>` — maintains cookies, localStorage, and behavioral history across runs
- Set a realistic window size: `--window-size=1920,1080`

**Stealth Injection (inject via `cdp.page.add_script_to_evaluate_on_new_document`)**
- Patch `navigator.webdriver` to `undefined` (not `false`)
- Populate `navigator.plugins` with 3 realistic plugin objects
- Populate `navigator.mimeTypes`
- Set `navigator.languages = ["en-US", "en"]`
- Ensure `window.chrome` exists with `runtime`, `loadTimes`, `csi`, `app`

**Session Warming (before drop)**
- Warm the Akamai behavioral profile 10–15 minutes before a drop by browsing product pages
- Ensure `_px3` cookie is fresh (< 50 seconds old) when entering checkout
- Use `SESSION_VALIDATE_INTERVAL = 600` to periodically keep the session alive during idle periods
- Force re-login after 30 minutes idle (`SESSION_MAX_IDLE = 1800`)

**Selector Strategy**
- Primary: `data-automation-id` attributes — most stable
- Secondary: `data-testid`, `data-dca-name`, `name`, `type`, `aria-label`
- Tertiary: `:has-text()` with exact known strings
- Never: CSS class names (hashed, change on every deploy)
- Always maintain arrays of 3+ fallback selectors per interactive element

**Timing**
- Add 200–1200ms human-like random delays between all interactions
- After ATC click, wait 1.5–2s before checking for flyout confirmation
- After checkout button click, wait up to 15s for checkout page to load (React SPA hydration can be slow)
- After Place Order click, wait up to 20s for confirmation URL

**Proxy Requirements**
- Residential US proxies only (no datacenter, no non-US)
- Sticky proxy per checkout session — do not rotate mid-checkout
- Rotate after any 403/429 with a 5-minute cooldown
- Bench proxies with > 10% error rate over last 100 requests
- Avoid Mexico, Canada proxies — known to trigger 456 blocks

**Handling `/blocked`**
- Detect by URL containing "/blocked" after any navigation
- Attempt CDP-level mouse-down-hold-mouse-up on the challenge button
- If CDP solve fails, the session is likely too hot — rest the proxy and account, re-warm from homepage before retrying

**Handling the Virtual Queue**
- Monitor for queue iframe/overlay before attempting ATC
- Try FBT (Frequently Bought Together) ATC path first as it sometimes bypasses queue
- If in queue, wait for pass-through signal and re-navigate to product URL afterward

**Key Configuration Values (from `config.py`)**
- `PX3_MAX_AGE_SECONDS = 50` — re-warm session if `_px3` is older than this
- `QUEUE_POLL_INTERVAL = 5` seconds
- `QUEUE_TIMEOUT = 1800` seconds (30 min max queue wait)
- `CIRCUIT_BREAKER_FAILURES = 3` — pause after 3 consecutive failures
- `CIRCUIT_BREAKER_PAUSE = 60` seconds
- `CHECKOUT_PROXY_POOL_SIZE = 12` sticky proxies for checkout
- `GRAPHQL_HASH` — persisted query hash for `ItemByIdBtf`; update when Walmart deploys new frontend

**GraphQL Hash Maintenance**
- The `ItemByIdBtf` hash changes on each Walmart frontend deploy (roughly every 2–6 weeks)
- Monitor for 400/404 on stock check requests; update `GRAPHQL_HASH` in `config.py` by inspecting a live product page's network traffic
- The `ItemByIdAtf` hash is auto-discovered by intercepting real browser network events — no manual update needed

**Confidence Notes**
- Selector patterns confirmed via live codebase (`purchase_executor.py`) and public scraping references
- Anti-bot vendor stack (Akamai + PerimeterX/HUMAN + Cloudflare) confirmed by multiple independent sources
- `_px3` 60-second TTL is from operational observation in `config.py` comments
- Akamai v3 sensor data encryption details sourced from public security research (medium.com/@glizzykingdreko)
- Zendriver bypass success rates from published benchmark (Dima Kynal, Medium)
- Specific PerimeterX behavioral triggers are inferred from operational behavior and public bot-operator documentation (Refract bot guide); exact thresholds are proprietary
